#!/usr/bin/env python3
"""ML-2 · build the predictive-bracket corpus from harness trade emits.

**This is the unit of work MI-148 named and left unclaimed.** Its memo
(`docs/research/bracket-calibration-2026-09-06.md`) ends:

    *"What is NOT yet available, and must be before per-leg numbers are set: an
    MFE distribution at proper n. The live telemetry gives 1-8 rows per leg. The
    un-circular source is the offline harness over historical candles, which
    reads no live broker state and is not blocked on anything. That is the next
    unit of work, and it is the honest gate on clause 1."*

This builds that. It converts `--emit-trades` JSONL rows into the ML-2 corpus:
one row per backtest trade, carrying a **decision-time exogenous feature
vector** and the **percent-of-entry** outcome columns the calibration grader
speaks.

--------------------------------------------------------------------------
WHY THE BACKTEST CORPUS IS CLEANER THAN LIVE FOR THIS ONE QUESTION
--------------------------------------------------------------------------
MI-148 chose percent-of-entry over R because the live R denominator is
contaminated: `trades.stop_loss` is the FINAL trailed stop, so the entry-time
risk is erased. **That contamination is a property of the live journal, not of
R.** In the harness, `Trade.sl` is the entry bar's stop and no lever overwrites
it — `risk = |entry - sl|` is the decision-time risk by construction.

So the backtest corpus can express BOTH bases exactly, and this builder emits
both (`*_frac` percent-of-entry, and the harness's own `*_r`). The percent-of-
entry columns are the ones the grader uses, so the two instruments agree; the R
columns are carried so a reader can see the mapping rather than take it on
trust.

--------------------------------------------------------------------------
⚠️ THE ONE ROW SHAPE THAT CANNOT BE CONVERTED — AND IT IS SILENT
--------------------------------------------------------------------------
`scripts/backtest_trend.py` applies the M20 bank lever AFTER computing the
exit R:

    if banked:
        r = bank_frac * bank_at_r + (1.0 - bank_frac) * r

so with `--bank-frac > 0` the emitted `gross_r` is a **BLEND of two fills** and
no longer maps to a single exit price. Inverting it would manufacture an exit
location that never existed.

**The emit carries no bank flag**, so a row cannot be self-diagnosed. This
builder therefore takes `--bank-frac-asserted` (default 0.0) — the caller
states what the run used — and REFUSES the whole file with
`exit_recoverable: blended_unrecoverable` when it is non-zero, rather than
silently inverting. Three states, never collapsed:

  * ``exact``                  — risk and gross_r both readable, bank lever off
  * ``blended_unrecoverable``  — the bank lever was on; the exit price is gone
  * ``unreadable``             — a field is missing or non-finite

MFE is unaffected by the bank lever (it is a path statistic, computed before
the blend), so `mfe_frac` survives on a blended run and `exit_frac` does not.
The two are reported separately for exactly that reason.

--------------------------------------------------------------------------
FEATURES ARE DECISION-TIME AND EXOGENOUS, BY CONSTRUCTION (§ 0.2)
--------------------------------------------------------------------------
`docs/design/exit-mechanism-construction-PROCESS.md` § 0.2 names the root cause
of every negative exit result to date: all 11 of 11 features every exit study
learns from are ENDOGENOUS — functions of the trade's own path, clock, geometry
or symbol. Its corollary: *"no lever beats holding"* means only *"no function of
these eleven inputs beats holding"*.

Every feature emitted here is knowable AT THE ENTRY BAR:

  ``risk_frac``   |entry - sl| / entry. The vol-at-entry proxy. E3.6(2) measured
                  that a percent-of-entry target makes `tp_R` and `ATR/close`
                  literally the same variable (collinearity confirmed 19/19,
                  worst deviation 2.78e-17) — so this column IS the volatility
                  state, not a proxy for it.
  ``is_long``     direction, +1/0.
  ``confidence``  the strategy's own decision-time conviction (for
                  `trend_donchian`, breakout depth past the channel / ATR).
  ``hour_sin/cos`` session position, cyclically encoded so 23:00 and 00:00 are
                  adjacent rather than maximally distant.
  ``dow``         day of week.

⚠️ **`mfe_r`, `gross_r`, `exit_time` and anything derived from them are
OUTCOMES, never features.** They are the trade's own subsequent path — using one
as an input is § 0.2 with extra steps. `FEATURE_NAMES` is the closed list and
`build_rows` reads nothing else.

--------------------------------------------------------------------------
Tier-1, observe-only. Reads emit files, writes a corpus file. No live broker
state, no DB write, no order path, no runtime caller.

Usage:
    python scripts/research/ml2_bracket_corpus.py \
        --emit /tmp/emit_btc_15m.jsonl --leg trend_donchian_btc_15m \
        --out /tmp/ml2_corpus.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

#: The closed feature list. Decision-time and exogenous — see the docstring.
FEATURE_NAMES: Tuple[str, ...] = (
    "risk_frac", "is_long", "confidence", "hour_sin", "hour_cos", "dow",
)

#: Columns that are OUTCOMES. Named explicitly so a reader can see that none of
#: them appears in FEATURE_NAMES, rather than having to check by eye.
OUTCOME_NAMES: Tuple[str, ...] = ("mfe_frac", "exit_frac", "mfe_r", "gross_r", "net_r")

EXIT_EXACT = "exact"
EXIT_BLENDED = "blended_unrecoverable"
EXIT_UNREADABLE = "unreadable"


def _f(v: Any) -> Optional[float]:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _parse_ts(v: Any) -> Optional[datetime]:
    """Parse the harness's `str(pd.Timestamp)` output. None, never a guess."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("T", " ")
    for suffix in ("+00:00", "Z", " UTC"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def build_row(raw: Dict[str, Any], *, bank_frac_asserted: float = 0.0) -> Dict[str, Any]:
    """Convert ONE harness emit row into a corpus row.

    Never raises. A row that cannot be converted comes back with
    ``exit_recoverable`` / ``mfe_state`` saying why, and null outcome columns —
    never a fabricated zero, which would drag every aggregate toward a value
    nobody observed.
    """
    entry = _f(raw.get("entry"))
    sl = _f(raw.get("sl"))
    mfe_r = _f(raw.get("mfe_r"))
    gross_r = _f(raw.get("gross_r"))
    direction = str(raw.get("direction") or "").strip().lower()
    ts = _parse_ts(raw.get("entry_time"))

    out: Dict[str, Any] = {
        "leg": raw.get("strategy"),
        "symbol": raw.get("symbol"),
        "entry_time": raw.get("entry_time"),
        "exit_time": raw.get("exit_time"),
        "exit_reason": raw.get("exit_reason"),
        "direction": direction or None,
        # outcomes
        "mfe_frac": None, "exit_frac": None,
        "mfe_r": mfe_r, "gross_r": gross_r, "net_r": _f(raw.get("net_r")),
        # states
        "exit_recoverable": EXIT_UNREADABLE,
        "mfe_state": EXIT_UNREADABLE,
        # features
        **{k: None for k in FEATURE_NAMES},
    }

    if entry is None or sl is None or entry <= 0:
        return out
    risk = abs(entry - sl)
    if risk <= 0:
        # A zero-risk trade makes every R figure a division by zero. Degenerate,
        # not convertible — and NOT a 0.0 outcome.
        return out

    risk_frac = risk / entry
    out["risk_frac"] = risk_frac
    out["is_long"] = 1.0 if direction == "long" else 0.0
    out["confidence"] = _f(raw.get("confidence")) or 0.0
    if ts is not None:
        ang = 2.0 * math.pi * (ts.hour + ts.minute / 60.0) / 24.0
        out["hour_sin"] = math.sin(ang)
        out["hour_cos"] = math.cos(ang)
        out["dow"] = float(ts.weekday())
    else:
        # Time features unreadable -> leave None. The model drops the row rather
        # than imputing a session that was not observed.
        pass

    # MFE is a PATH statistic, computed before the bank blend, so it survives a
    # banked run. exit_frac does not. Different states, deliberately.
    if mfe_r is not None:
        out["mfe_frac"] = mfe_r * risk_frac
        out["mfe_state"] = EXIT_EXACT

    if bank_frac_asserted and bank_frac_asserted > 0:
        out["exit_recoverable"] = EXIT_BLENDED
    elif gross_r is not None:
        out["exit_frac"] = gross_r * risk_frac
        out["exit_recoverable"] = EXIT_EXACT
    return out


def build_rows(raws: Iterable[Dict[str, Any]], *, bank_frac_asserted: float = 0.0,
               leg_override: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = []
    for raw in raws:
        r = build_row(raw, bank_frac_asserted=bank_frac_asserted)
        if leg_override:
            r["leg"] = leg_override
        rows.append(r)
    return rows


def feature_matrix(rows: Sequence[Dict[str, Any]], outcome: str = "mfe_frac"
                   ) -> Tuple[List[List[float]], List[float], Dict[str, int]]:
    """Return (X, y, dropped) over rows whose features AND outcome are complete.

    ``dropped`` states WHY each excluded row was excluded, so the population is
    reconstructable from the output rather than asserted in prose. A builder
    that reports only the kept count cannot tell a clean corpus from a
    catastrophically filtered one — the `build_exit_head_dataset` failure where
    371 of 371 rows were dropped before the candle stage and it read as "the
    family has no data" for as long as the harness existed.
    """
    X: List[List[float]] = []
    y: List[float] = []
    dropped = {"missing_feature": 0, "missing_outcome": 0, "kept": 0}
    for r in rows:
        vals = [_f(r.get(k)) for k in FEATURE_NAMES]
        if any(v is None for v in vals):
            dropped["missing_feature"] += 1
            continue
        ov = _f(r.get(outcome))
        if ov is None:
            dropped["missing_outcome"] += 1
            continue
        X.append([float(v) for v in vals])  # type: ignore[arg-type]
        y.append(ov)
        dropped["kept"] += 1
    return X, y, dropped


def summarise(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Population summary. Every count a reader needs to judge the corpus."""
    n = len(rows)
    by_leg: Dict[str, int] = {}
    for r in rows:
        by_leg[str(r.get("leg"))] = by_leg.get(str(r.get("leg")), 0) + 1
    mfe_ok = sum(1 for r in rows if r.get("mfe_state") == EXIT_EXACT)
    exit_ok = sum(1 for r in rows if r.get("exit_recoverable") == EXIT_EXACT)
    blended = sum(1 for r in rows if r.get("exit_recoverable") == EXIT_BLENDED)
    return {
        "n_rows": n,
        "n_legs": len(by_leg),
        "rows_per_leg": dict(sorted(by_leg.items())),
        "mfe_exact": mfe_ok,
        "mfe_unreadable": n - mfe_ok,
        "exit_exact": exit_ok,
        "exit_blended_unrecoverable": blended,
        "exit_unreadable": n - exit_ok - blended,
    }


def read_emit(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emit", action="append", required=True,
                    help="harness --emit-trades JSONL (repeatable)")
    ap.add_argument("--leg", action="append", default=None,
                    help="leg name per --emit, in order (else the row's `strategy`)")
    ap.add_argument("--bank-frac-asserted", type=float, default=0.0,
                    help="what --bank-frac the harness run used. Non-zero => "
                         "exit_frac is REFUSED as blended_unrecoverable.")
    ap.add_argument("--out", default=None, help="corpus JSONL (default: stdout summary only)")
    args = ap.parse_args()

    legs = args.leg or []
    all_rows: List[Dict[str, Any]] = []
    for i, path in enumerate(args.emit):
        raws = read_emit(path)
        override = legs[i] if i < len(legs) else None
        all_rows.extend(build_rows(raws, bank_frac_asserted=args.bank_frac_asserted,
                                   leg_override=override))

    summary = summarise(all_rows)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            for r in all_rows:
                fh.write(json.dumps(r) + "\n")
        summary["out"] = args.out
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
