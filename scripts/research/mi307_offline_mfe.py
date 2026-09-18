#!/usr/bin/env python3
# wiring: manual-only — a research READ a session runs when it needs the offline
# answer to "how far do this leg's trades actually go, and where would a target
# have been hit". Nothing schedules it: the numbers only mean something beside a
# Tier-3 decision about the legs they name, and a CI job asserting a particular
# reach-rate would fail every time a leg is retuned or the candle span grows.
# Its --selftest IS the wired half (the invariants never change though the fleet does).
"""MI-307 — the OFFLINE per-leg MFE distribution, and what it says about a target.

WHY THIS EXISTS
---------------
`OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-EXISTS-AND-ITS-VERDICT-HAS-NOT-BEEN-ACTED-ON`
names exactly two ways to clear, and this is the second: *"the offline-harness
MFE distribution is produced at stated n and a per-leg proposal is put in front
of the operator off THAT n rather than off the live 1-8."*

MI-148's live instrument (`scripts/research/bracket_calibration_report.py`) is
correct and is starved: its `--mfe` view reads `position_telemetry.peak_r`, which
is small-n, `estimated`, and a declared LOWER BOUND on every row. The offline
harness has none of those three problems, and one the live side does not have.

WHAT THIS ANSWERS THAT THE EXISTING TOOLING DOES NOT
-----------------------------------------------------
`m20_fleet_exit_sweep.winner_mfe_p80` already computes a per-leg MFE percentile
and this does NOT duplicate it — it asks a different question of the same rows,
and the difference is the whole point:

* that one is **WINNER-ONLY** (`net_r > 0`), because it calibrates a TRAIL arm,
  and a trail can only act on a trade that is already winning;
* a **TAKE-PROFIT** is hit by any trade whose excursion reaches it, including
  every trade that went +2R and then reversed into a stop. Those are precisely
  the trades a target would have changed, so grading a target on a winner-only
  population removes the evidence for it.

So the reach-rate here is over **ALL graded trades**, and the winner-only figure
is reported beside it as the comparison — never instead of it.

REACH-RATE IS A COUNT, NOT A QUANTILE, AND THAT IS DELIBERATE
--------------------------------------------------------------
`P(mfe_r >= x)` is the exact fraction of trades that would have TOUCHED a target
at `x`R, and it needs no interpolation convention. Two incompatible percentile
definitions already exist in this repo for good reasons — `m31_mfe_parity._pct`
is nearest-rank, `bracket_calibration.quantile` is linear-interpolated — and
introducing a third to express a quantity that is natively a count would be the
drift those two modules each warn about. Quantiles reported here are
`bracket_calibration.quantile`, IMPORTED, so they are comparable to MI-148's
live instrument by construction.

⚠️ THE HARNESS IS RUN **UNCAPPED**, AND THAT IS A DIFFERENT BOOK FROM LIVE
---------------------------------------------------------------------------
With `--tp-cap-pct 0.099` the harness exits at the venue clamp, so `mfe_r` is
TRUNCATED at each trade's own `cap_r` and the distribution can say nothing about
what lies above it. That capped book is the right one for MFE **parity** against
live — which is why `m31_harness_mfe_dist.py` REFUSES to write an uncapped
distribution under the name Check B reads, and this module does not write that
artifact. It is the wrong book for SETTING a target, because the question is
where trades got to, not where the clamp stopped the record of it.

Both are produced. They are never pooled and never swapped:

* `uncapped`  — the target-setting basis. No take-profit exit path at all.
* `capped`    — the live-comparable basis, for the m31 artifact.

⚠️ MFE IS STILL CENSORED BY THE TRAIL IN BOTH, and that is not a defect to fix:
a trade ends when its exit mechanism ends it, so `mfe_r` is "how far it got
before the exit fired". That is exactly the quantity a take-profit competes
with, so it is the right basis — but it means these numbers describe the
excursion available UNDER THE CURRENT TRAIL, not under no exit at all.

⚠️ AND A REACH-RATE IS NOT A P&L CLAIM. `P(mfe_r >= x)` says a target at `x`
would have been TOUCHED; it does not say the leg earns more with it. A target
truncates the right tail the trail is there to harvest, and on these legs the
trail is the declared profit exit. Net-R is a SWEEP's question
(`e35_bracket_geometry_sweep`), not this module's, and nothing here proposes a
value — every `tp_r` is Tier-3 and the operator's.

Usage:
    mi307_offline_mfe.py --run            # run the harnesses, write the JSON
    mi307_offline_mfe.py --report         # print the table from the JSON
    mi307_offline_mfe.py --selftest       # prove the tool can fail

Tier 1 — research tooling. Reads candles and config, writes JSON under
docs/research/. Touches no live path, no config, no order.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve()
REPO = _HERE.parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(_HERE.parent))

import yaml  # noqa: E402

from exit_capture import mfe_r_of  # noqa: E402  (the ONE MFE reader)
from src.runtime.bracket_calibration import quantile  # noqa: E402 (the ONE quantile)
from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT  # noqa: E402 (the ONE clamp)
from m20_fleet_exit_sweep import (  # noqa: E402  (the ONE leg->harness resolver)
    FAMILY_HARNESS, base_args, classify, harness_implements_flag, resolve_data,
)

DEFAULT_OUT = REPO / "docs" / "research" / "mi307-offline-mfe-2026-09-18.json"

#: Candidate targets the reach-rate is reported at. Chosen to span the range
#: live legs actually declare (1.5 / 2 / 3 / 4 / 6 are all live `tp_r` values
#: on some leg today) plus the two ends that bracket them.
REACH_GRID = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]

#: Below this a per-leg number is reported but must NOT carry a proposal.
#: 30 matches `winner_mfe_p80`'s own floor, which is the only precedent in the
#: repo for "enough MFE readings to say something per leg"; it is a BORROWED
#: threshold, not a derived one, and is stated as such wherever it is applied.
MIN_N_FOR_PROPOSAL = 30


def load_legs() -> Dict[str, dict]:
    raw = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    return raw.get("strategies", raw)


def cap_r_of(row: Dict[str, Any]) -> Optional[float]:
    """Per-trade venue-clamp ceiling in R, from the harness's OWN entry and stop.

    `cap_r = TP_VENUE_CAP_PCT * entry / risk` — the same expression
    `src/runtime/position_telemetry.py` computes live, with the clamp IMPORTED
    rather than re-declared.

    ⚠️ THIS IS THE ONE DENOMINATOR THE LIVE SIDE CANNOT PRODUCE HONESTLY.
    `trades.stop_loss` is the FINAL TRAILED stop, not the risk taken at entry
    (`bracket_calibration.py`'s own "WHY PERCENT-OF-ENTRY AND NOT R"), so live R
    is contaminated. The harness emits the ENTRY stop and its `gross_r` is
    exactly -1.0 on a stop-out, which is what makes R clean here — and is the
    reason this unit can report in R at all where MI-148 deliberately cannot.
    """
    try:
        entry = float(row["entry"])
        sl = float(row["sl"])
    except (KeyError, TypeError, ValueError):
        return None
    risk = abs(entry - sl)
    if risk <= 0 or entry <= 0:
        return None
    return TP_VENUE_CAP_PCT * entry / risk


def summarise_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-leg MFE summary. Every count carries its own denominator.

    `n_rows` is what the harness emitted; `n_mfe` is what carried a readable
    MFE. They are reported separately because "the leg made no trades" and "the
    leg made trades whose MFE we could not read" are different facts, and the
    second is a harness/reader shape mismatch that must never be reported as a
    thin sample (`exit_capture.mfe_r_of`'s own lesson).
    """
    mfes: List[float] = []
    win_mfes: List[float] = []
    caps: List[float] = []
    pairs: List[tuple] = []          # (mfe_r, cap_r) — both readable
    mfe_pcts: List[float] = []       # MFE as a fraction of entry price
    tp_exits = 0
    rows_without_mfe = 0
    for r in rows:
        if str(r.get("exit_reason") or "") == "take_profit":
            tp_exits += 1
        m = mfe_r_of(r)
        if m is None:
            rows_without_mfe += 1
            continue
        mfes.append(m)
        # PERCENT-OF-ENTRY, the basis MI-148 and MI-155 both settle on.
        # `mfe_pct = mfe_r * risk / entry` is exact here because the harness
        # emits the ENTRY stop, so `risk` is the risk actually taken. MI-155's
        # reconciliation is the rule followed throughout this module:
        # MEASURE in percent-of-entry, EXPRESS the config knob as `tp_r`.
        try:
            _entry = float(r["entry"])
            _risk = abs(_entry - float(r["sl"]))
            if _entry > 0 and _risk > 0:
                mfe_pcts.append(m * _risk / _entry)
        except (KeyError, TypeError, ValueError):
            pass
        try:
            if float(r.get("net_r") or 0.0) > 0:
                win_mfes.append(m)
        except (TypeError, ValueError):
            pass
        c = cap_r_of(r)
        if c is not None:
            caps.append(c)
            pairs.append((m, c))

    n = len(mfes)
    out: Dict[str, Any] = {
        "n_rows": len(rows),
        "n_mfe": n,
        "rows_without_mfe": rows_without_mfe,
        "n_winners_with_mfe": len(win_mfes),
        # None, never 0.0, on an empty sample: zero is a real quantile.
        "mfe_r_p50": quantile(mfes, 0.50),
        "mfe_r_p75": quantile(mfes, 0.75),
        "mfe_r_p80": quantile(mfes, 0.80),
        "mfe_r_p90": quantile(mfes, 0.90),
        "mfe_r_max": (max(mfes) if mfes else None),
        # percent-of-entry, directly comparable to MI-148's live `--mfe` view
        # and to TP_VENUE_CAP_PCT itself (0.099).
        "n_mfe_pct": len(mfe_pcts),
        "mfe_pct_p50": quantile(mfe_pcts, 0.50),
        "mfe_pct_p70": quantile(mfe_pcts, 0.70),
        "mfe_pct_p80": quantile(mfe_pcts, 0.80),
        "mfe_pct_p90": quantile(mfe_pcts, 0.90),
        "mfe_pct_p95": quantile(mfe_pcts, 0.95),
        # Share of trades whose excursion reached the venue clamp itself. This
        # is the figure MI-151's published table calls `reached_venue_cap_rate`
        # and is what makes this row drop into MI-155's reference unchanged.
        "reached_venue_cap_rate": (
            (sum(1 for x in mfe_pcts if x >= TP_VENUE_CAP_PCT) / len(mfe_pcts))
            if mfe_pcts else None),
        "winner_mfe_r_p50": quantile(win_mfes, 0.50),
        "winner_mfe_r_p80": quantile(win_mfes, 0.80),
        # The venue clamp expressed in this leg's own R, per trade.
        "cap_r_n": len(caps),
        "cap_r_p50": quantile(caps, 0.50),
        "cap_r_p10": quantile(caps, 0.10),
        "cap_r_p90": quantile(caps, 0.90),
        "reach_rate": None,
        "reach_rate_winners_only": None,
        "clamp_binds_rate": None,
        # THE ACTIONABLE ONE. A declared `tp_r` does not set the target: the
        # effective target is `min(cap_r, tp_r)` (tp_venue_cap.py's own warning
        # that NO tp_r reproduces the clamp). So the reach rate a candidate
        # would ACTUALLY produce is P(mfe_r >= min(cap_r, x)) — never P(mfe_r >= x),
        # which silently assumes the declared value reaches the wire.
        "effective_reach_rate": None,
        "n_pairs": len(pairs),
        # Observed TP-exit share of the CAPPED run — the positive control.
        "tp_exit_rate": (tp_exits / len(rows)) if rows else None,
        "tp_exits": tp_exits,
    }
    if pairs:
        out["effective_reach_rate"] = {
            f"{x:g}": sum(1 for m, c in pairs if m >= min(c, x)) / len(pairs)
            for x in REACH_GRID
        }
    if n:
        out["reach_rate"] = {
            f"{x:g}": sum(1 for m in mfes if m >= x) / n for x in REACH_GRID
        }
    if win_mfes:
        out["reach_rate_winners_only"] = {
            f"{x:g}": sum(1 for m in win_mfes if m >= x) / len(win_mfes)
            for x in REACH_GRID
        }
    if caps:
        # How often the venue clamp is TIGHTER than the candidate target, i.e.
        # how often a declared tp_r at that level would never reach the wire.
        out["clamp_binds_rate"] = {
            f"{x:g}": sum(1 for c in caps if c < x) / len(caps) for x in REACH_GRID
        }
    return out


def run_leg(leg: str, cfg: dict, *, capped: bool) -> Dict[str, Any]:
    """Run one leg's own harness with its OWN declared params.

    Returns a state dict. The four states are never collapsed:
      `ok`                  the harness ran and emitted rows
      `no_harness`          the family has no capped-capable harness
      `no_data`             no candle file for this symbol/timeframe — the
                            crypto-only feed limit, NOT a statement about the leg
      `not_capped_capable`  harness AND candles both exist, but the harness
                            implements no `--tp-cap-pct`, so the venue clamp
                            cannot be modelled — see below
      `harness_failed`      it ran and returned non-zero — *we could not look*

    ⚠️ `not_capped_capable` IS A SEPARATE STATE AND MUST NOT BE FOLDED INTO
    `no_data`. The scalp and fvg families are crypto and their candles ARE
    fetchable; what is missing is the harness flag. Reporting them as `no_data`
    would say the FEED cannot serve them, which is false, and would hide a gap
    that is one harness flag wide rather than a data-availability wall. The
    consequence is real and is why the distinction earns a state: without
    `--tp-cap-pct` there is no capped arm, so neither the live-comparable
    distribution nor the positive control in § 2.1 can exist for that leg — an
    uncapped-only number would be an unfalsifiable one.

    ⚠️ The membership test is `m20_fleet_exit_sweep.harness_implements_flag`,
    which READS THE HARNESS SOURCE rather than consulting a hardcoded list, so
    this cannot go stale the day a harness gains the flag.
    """
    fam = classify(leg)
    harness = FAMILY_HARNESS.get(fam or "")
    sym = (cfg.get("symbols") or [None])[0]
    tf = str(cfg.get("timeframe") or "1h")
    base_rec = {"leg": leg, "family": fam, "symbol": sym, "timeframe": tf,
                "capped": capped}
    if not harness:
        return {**base_rec, "state": "no_harness",
                "why": f"no harness registered for family {fam!r}"}
    data, proxy, resample = resolve_data(str(sym), tf, REPO / "data")
    if not data:
        return {**base_rec, "state": "no_data",
                "why": f"no candle file for {sym}/{tf} under data/"}

    implements = harness_implements_flag(harness, "--tp-cap-pct")
    if implements is False:
        return {**base_rec, "state": "not_capped_capable",
                "why": f"{os.path.basename(harness)} implements no --tp-cap-pct, "
                       "so the venue clamp cannot be modelled and no capped arm "
                       "(and therefore no positive control) is possible"}
    args = base_args(leg, cfg, fam or "", data, resample)
    if capped:
        args += ["--tp-cap-pct", str(TP_VENUE_CAP_PCT)]
        tp_r = cfg.get("tp_r")
        if tp_r is not None:
            args += ["--tp-r", str(tp_r)]
    # `--strategy-name` exists on the trend + pullback harnesses only; the
    # emit row's `strategy` field is what m31 groups on, so pass it where the
    # harness accepts it and fall back to grouping by the leg id we already know.
    fd, tmp = tempfile.mkstemp(prefix=f"mi307_{leg}_", suffix=".jsonl")
    os.close(fd)
    Path(tmp).unlink(missing_ok=True)
    cmd = [sys.executable, str(REPO / harness), *args, "--emit-trades", tmp]
    if harness in (FAMILY_HARNESS["donchian"], FAMILY_HARNESS["pullback"]):
        cmd += ["--strategy-name", leg]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           cwd=str(REPO))
    except subprocess.TimeoutExpired:
        return {**base_rec, "state": "harness_failed", "why": "timeout"}
    if p.returncode != 0:
        return {**base_rec, "state": "harness_failed",
                "why": (p.stderr or "").strip()[-400:] or f"rc={p.returncode}"}
    rows: List[Dict[str, Any]] = []
    try:
        for line in Path(tmp).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as exc:
        return {**base_rec, "state": "harness_failed",
                "why": f"emit unreadable: {type(exc).__name__}"}
    finally:
        Path(tmp).unlink(missing_ok=True)
    rec = {**base_rec, "state": "ok", "data_file": os.path.basename(data),
           "proxy": bool(proxy), "resample": resample,
           "emit_path": tmp, "rows": rows}
    rec.update(summarise_rows(rows))
    rec.pop("rows", None)
    return rec


def run_all(out_path: Path, *, only: Optional[List[str]] = None) -> int:
    legs = load_legs()
    results: List[Dict[str, Any]] = []
    for leg in sorted(legs):
        cfg = legs[leg]
        if not isinstance(cfg, dict):
            continue
        if only and leg not in only:
            continue
        for capped in (False, True):
            rec = run_leg(leg, cfg, capped=capped)
            rec["tp_r_declared"] = cfg.get("tp_r")
            rec["tp_intent_mode"] = (cfg.get("tp_intent") or {}).get("mode")
            rec["tp_intent_reason"] = (cfg.get("tp_intent") or {}).get("reason")
            rec["execution"] = cfg.get("execution")
            results.append(rec)
            tag = "capped" if capped else "uncapped"
            print(f"  {leg:26} {tag:9} {rec['state']:15} "
                  f"n_mfe={rec.get('n_mfe')} p80={rec.get('mfe_r_p80')}")
    payload = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "tp_venue_cap_pct": TP_VENUE_CAP_PCT,
        "reach_grid": REACH_GRID,
        "min_n_for_proposal": MIN_N_FOR_PROPOSAL,
        "quantile_definition": "src.runtime.bracket_calibration.quantile "
                               "(linear-interpolated) — IMPORTED, so these are "
                               "comparable to MI-148's live instrument. NOTE "
                               "m31_harness_mfe_dist uses nearest-rank _pct; "
                               "the two are different definitions.",
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(f"\nwrote {out_path} ({len(results)} rows)")
    return 0


def selftest() -> int:
    """RULE ONE: show the tool finds a positive AND refuses the bad cases."""
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {name}: {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    print("mi307-offline-mfe selftest")

    rows = [{"strategy": "L", "mfe_r": float(i), "net_r": (1.0 if i > 3 else -1.0),
             "entry": 100.0, "sl": 99.0} for i in range(1, 11)]
    s = summarise_rows(rows)
    check("1 n and denominators are separate",
          s["n_rows"] == 10 and s["n_mfe"] == 10 and s["rows_without_mfe"] == 0)
    check("2 reach-rate is an exact count over ALL trades",
          s["reach_rate"]["3"] == 0.8 and s["reach_rate"]["10"] == 0.1)
    check("3 winners-only reach-rate differs from the all-trades one",
          s["reach_rate_winners_only"]["3"] == 1.0
          and s["reach_rate_winners_only"] != s["reach_rate"])
    # entry 100, sl 99 -> risk 1 -> cap_r = 0.099*100/1 = 9.9
    check("4 cap_r comes from the ENTRY stop and the imported clamp",
          s["cap_r_p50"] is not None and abs(s["cap_r_p50"] - 9.9) < 1e-9)
    check("5 clamp_binds_rate is 0 below cap_r and 1 above",
          s["clamp_binds_rate"]["8"] == 0.0 and s["clamp_binds_rate"]["10"] == 1.0)

    mixed = rows + [{"strategy": "L"}, {"strategy": "L", "mfe_r": None}]
    s2 = summarise_rows(mixed)
    check("6 rows without a readable MFE are COUNTED, never dropped silently",
          s2["n_mfe"] == 10 and s2["rows_without_mfe"] == 2 and s2["n_rows"] == 12)

    nested = [{"strategy": "S", "meta": {"mfe_r": 2.0}, "net_r": 1.0,
               "entry": 10.0, "sl": 9.0}]
    check("7 the nested ict_scalp MFE shape is read (mfe_r_of, not row['mfe_r'])",
          summarise_rows(nested)["n_mfe"] == 1)

    # 10-11 — the effective-target rule, which is the module's whole claim.
    # entry 100 / sl 99 -> risk 1 -> cap_r 9.9, so the clamp is LOOSE here and
    # the effective reach must be IDENTICAL to the naive one.
    check("10 with a loose clamp, effective reach == naive reach",
          s["effective_reach_rate"] == s["reach_rate"])
    # entry 100 / sl 95 -> risk 5 -> cap_r = 0.099*100/5 = 1.98, a TIGHT clamp.
    # Every trade whose mfe_r >= 1.98 hits the effective target at ANY candidate
    # x >= 1.98, so effective reach must EXCEED the naive reach above the clamp.
    tight = [{"strategy": "T", "mfe_r": float(i), "net_r": 1.0,
              "entry": 100.0, "sl": 95.0} for i in range(1, 11)]
    st = summarise_rows(tight)
    check("11 with a tight clamp, effective reach EXCEEDS naive above the clamp",
          st["effective_reach_rate"]["6"] > st["reach_rate"]["6"]
          and abs(st["cap_r_p50"] - 1.98) < 1e-9
          and st["effective_reach_rate"]["6"] == 0.9)

    tp_rows = [{"strategy": "T", "mfe_r": 1.0, "net_r": 1.0, "entry": 100.0,
                "sl": 99.0, "exit_reason": "take_profit"},
               {"strategy": "T", "mfe_r": 1.0, "net_r": -1.0, "entry": 100.0,
                "sl": 99.0, "exit_reason": "stop"}]
    # 13 — percent-of-entry must be derived from the ENTRY stop, not assumed.
    # entry 100 / sl 95 -> risk 5; mfe_r 2 -> mfe_pct = 2*5/100 = 0.10.
    pct = summarise_rows([{"strategy": "P", "mfe_r": 2.0, "net_r": 1.0,
                           "entry": 100.0, "sl": 95.0}])
    check("13 mfe_pct = mfe_r * risk / entry, from the ENTRY stop",
          pct["n_mfe_pct"] == 1 and abs(pct["mfe_pct_p50"] - 0.10) < 1e-12)

    check("12 tp_exit_rate counts take_profit exits over ALL emitted rows",
          summarise_rows(tp_rows)["tp_exit_rate"] == 0.5)

    empty = summarise_rows([])
    check("8 an empty sample yields None, never 0.0",
          empty["mfe_r_p50"] is None and empty["reach_rate"] is None
          and empty["n_mfe"] == 0)

    # 9 — a leg with no candle file must report `no_data`, NOT an empty
    #     distribution. This is the crypto-only feed limit, and reporting it as
    #     a measured zero is the exact defect the unit exists to name.
    rec = run_leg("gld_pullback_1d",
                  {"symbols": ["GLD"], "timeframe": "1d", "atr_stop_mult": 2.0},
                  capped=False)
    check("9 a leg with no candles is `no_data`, not an empty measurement",
          rec["state"] in ("no_data", "no_harness") and "n_mfe" not in rec)

    # 14 — the scalp family must NOT report `no_data` when its candles exist.
    # This is the state that would otherwise read as a feed limit it is not.
    check("14 harness_implements_flag reads the SOURCE for --tp-cap-pct",
          harness_implements_flag(FAMILY_HARNESS["donchian"], "--tp-cap-pct") is True
          and harness_implements_flag(FAMILY_HARNESS["scalp"], "--tp-cap-pct") is False)

    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated leg ids")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--write-reference", default=None, metavar="PATH",
                    help="project the uncapped result into MI-155's "
                         "backtest-mfe-reference schema")
    args = ap.parse_args(argv[1:])

    if args.selftest:
        return selftest()
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    if args.run:
        return run_all(Path(args.out), only=only)
    if args.write_reference:
        return write_reference(Path(args.out), Path(args.write_reference))
    if args.report:
        return report(Path(args.out))
    print("ERROR: one of --run / --report / --selftest is required",
          file=sys.stderr)
    return 2


def report(path: Path) -> int:
    if not path.exists():
        print(f"NO ARTIFACT at {path} — the offline distribution has not been "
              "produced. That is absent, not zero.")
        return 0
    payload = json.loads(path.read_text())
    rows = [r for r in payload["results"] if not r["capped"]]
    print(f"MI-307 offline MFE — UNCAPPED basis, {len(rows)} leg(s), "
          f"generated {payload['generated_at']}")
    print(f"{'leg':26} {'state':14} {'n':>5} {'p50':>6} {'p80':>6} {'p90':>6} "
          f"{'capR50':>7} {'tp_r':>5} {'intent':>10}")
    for r in sorted(rows, key=lambda r: r["leg"]):
        def f(k, p=2):
            v = r.get(k)
            return "   n/a" if v is None else f"{v:.{p}f}"
        print(f"{r['leg']:26} {r['state']:14} {str(r.get('n_mfe') or ''):>5} "
              f"{f('mfe_r_p50'):>6} {f('mfe_r_p80'):>6} {f('mfe_r_p90'):>6} "
              f"{f('cap_r_p50'):>7} {str(r.get('tp_r_declared')):>5} "
              f"{str(r.get('tp_intent_mode')):>10}")
    return 0

def write_reference(src: Path, out: Path) -> int:
    """Project this unit's UNCAPPED result into MI-155's reference schema.

    `scripts/research/exit_location_fidelity.py` — MI-155's condition-1
    instrument — reads `docs/research/data/backtest-mfe-reference-*.json`,
    keyed `(symbol, family, timeframe)`. That file today holds **3 legs**, all
    `trend_donchian` 15m, TRANSCRIBED from MI-151's published table because the
    9,814-row corpus behind it was produced on the trainer and never committed.
    With only those 3, the instrument grades every one of the 44 enabled+live
    legs `no_backtest_corpus_for_leg` — so condition 1 cannot be evaluated at
    all, for any leg, whatever the live soak does.

    This writes the same schema from rows that were MEASURED here and can be
    re-measured by re-running `--run`, which is the difference between a
    transcription and a reproduction.

    ⚠️ IT DOES NOT CLEAR CONDITION 1 and must not be read as doing so. It
    supplies the BACKTEST half only; the live half (`n_live >= 30`) is a soak
    and is unmet on every leg — max n_live measured 25 on 2026-09-18. What it
    changes is the REASON a leg abstains, from "both halves missing" to "live
    depth only", which is a fact about what is left to do rather than a verdict.

    ⚠️ THE UNCAPPED ARM IS THE ONE PROJECTED, deliberately. The capped arm
    truncates every excursion at that trade's own `cap_r`, so its
    `reached_venue_cap_rate` would be an artefact of the cap being the exit
    rather than a measurement of how often the excursion got there.
    """
    payload = json.loads(src.read_text())
    prior = json.loads(out.read_text()) if out.exists() else {"legs": []}
    # family_of is MI-155's, imported so the join key cannot drift.
    sys.path.insert(0, str(REPO / "scripts" / "research"))
    from exit_location_fidelity import family_of  # noqa: E402

    kept = {(l["symbol"], l["family"], l["timeframe"]): l
            for l in (prior.get("legs") or [])}
    prior_keys = set(kept)

    # ⚠️ MI-155's KEY IS NOT UNIQUE OVER THIS FLEET, AND THE COLLISION IS REAL.
    # `(symbol, family, timeframe)` deliberately omits the leg, because it must
    # join to a LIVE leg by those three fields. But four pairs of legs share a
    # key while running DIFFERENT declared params — measured here, their p90
    # disagrees by 1.13x to 1.67x. Taking whichever arrives first would bake an
    # arbitrary pick into the artifact and hide a 1.67x disagreement behind a
    # single number. So: the larger-n arm supplies the figures (more evidence,
    # stated rather than implied) and the collision TRAVELS WITH THE ROW.
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in payload["results"]:
        if r.get("capped") or r.get("state") != "ok" or not r.get("n_mfe_pct"):
            continue
        key = (str(r["symbol"]), family_of(r["leg"]), str(r["timeframe"]))
        grouped.setdefault(key, []).append(r)

    added = 0
    for key, members in sorted(grouped.items()):
        if key in prior_keys:
            # NEVER overwrite MI-151's transcribed rows: they are the record of
            # an independent measurement, and replacing them would delete the
            # only cross-check this unit has.
            continue
        members.sort(key=lambda m: (-int(m["n_mfe_pct"]), m["leg"]))
        r = members[0]
        row = {
            "leg": r["leg"], "symbol": r["symbol"], "family": key[1],
            "timeframe": r["timeframe"], "n": r["n_mfe_pct"],
            "p50": r["mfe_pct_p50"], "p70": r["mfe_pct_p70"],
            "p80": r["mfe_pct_p80"], "p90": r["mfe_pct_p90"],
            "p95": r["mfe_pct_p95"],
            "reached_venue_cap_rate": r["reached_venue_cap_rate"],
            "source": "MI-307 mi307_offline_mfe.py --run (uncapped arm)",
        }
        if len(members) > 1:
            p90s = [m["mfe_pct_p90"] for m in members if m["mfe_pct_p90"]]
            row["key_not_unique"] = True
            row["key_shared_with"] = [m["leg"] for m in members[1:]]
            row["key_selection"] = "largest n_mfe_pct"
            row["key_collision_p90_spread"] = (
                (max(p90s) / min(p90s)) if len(p90s) > 1 and min(p90s) else None)
            row["key_collision_p90_by_leg"] = {
                m["leg"]: m["mfe_pct_p90"] for m in members}
            print(f"  ⚠️ key {key} is shared by {len(members)} legs "
                  f"({', '.join(m['leg'] for m in members)}); took "
                  f"{r['leg']} (n={r['n_mfe_pct']}), p90 spread "
                  f"{row['key_collision_p90_spread']:.2f}x")
        kept[key] = row
        added += 1
    prior["legs"] = [kept[k] for k in sorted(kept)]
    out.write_text(json.dumps(prior, indent=2) + "\n")
    print(f"wrote {out}: {len(prior['legs'])} leg(s) (+{added} from MI-307)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
