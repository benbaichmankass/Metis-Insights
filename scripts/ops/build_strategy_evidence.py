#!/usr/bin/env python3
# wiring: manual-only - run deliberately, because a CADENCE is not yet a
# decision anyone has made and arming one now would be premature in both
# directions. An edge record goes stale when a leg's CONFIG moves, not on a
# clock, so the natural trigger is a change to config/strategies.yaml rather
# than a timer; and the consumer that will say how fresh a record must be
# (MI-217, the packet emitting `no_offline_evidence`) does not exist yet.
# Wiring a nightly sweep before its consumer would fetch a year of candles
# for 52 legs on every run to keep records nobody reads.
# !! THIS IS A DEFERRAL, NOT A DISPOSAL, AND IT HAS A ROW: without a trigger
# these records go stale silently the moment a config changes, which is the
# exact failure class this producer exists to fix one level up. Tracked by
# BL-20260909-STRATEGY-EVIDENCE-RECORDS-HAVE-NO-STALENESS-TRIGGER.
"""MI-216 — build the PER-LEG OFFLINE EDGE RECORD the M7 review gate can read.

WHY THIS EXISTS
---------------
`docs/CLAUDE-RULES-CANONICAL.md` § "Promotion evidence — offline edge, live
mechanics" has been binding since 2026-07-26: edge is proven OFFLINE, live data
proves MECHANICS only, and **no gate may require calendar-time accrual to prove
edge**. The M7 strategy-review gate contradicted that for every committed day of
its life -- 52 of 52 legs `hold`, `actionable 0` -- because it graded edge from a
count of live in-window closes.

MI-215 established WHY, and the finding was not the one anyone expected: the gate
reads live closes because **live closes were the only per-leg quantity that
existed**. The offline infrastructure is real but was HEAD-scoped (all four
`scripts/ml/replay_pregate*.py` say "shadow regime heads (RG1)"; `oos_edge.py` is
manifest+dataset scoped to an ML model), and the packet's only offline hook,
`load_backtest_anchor`, carries no edge number by design -- measured across all
52 committed packets of 2026-09-01, **51 read `backtest_anchor: null`**.

This script is the missing producer. Operator-approved 2026-09-09 (MI-216), with
the sequencing explicitly approved too: **the producer comes FIRST**, because
re-pointing `decide()` beforehand yields 52/52 `null` -- the same outcome, harder
to see.

IT IS A WIRING JOB, NOT A NEW EVIDENCE ENGINE, AND THAT IS MEASURED
-------------------------------------------------------------------
`scripts/research/regime_debt_matrix.py` already resolves a leg's live params,
classifies its harness, fetches its feed and grades harness FIDELITY. This module
reuses that path wholesale and adds only what was missing: a config fingerprint,
a time-fold split, and a normalised record. Verified end to end 2026-09-09 --
`eth_pullback_2h`, 21.7s, 4380 real ETHUSDT 2h bars from `data.binance.vision`.

⚠️ COVERAGE IS 57.7%, NOT 98.1%, AND THE THREE NUMBERS ARE DIFFERENT QUESTIONS
------------------------------------------------------------------------------
MEASURED over all 52 enabled legs in `config/strategies.yaml` on **2026-09-22**
(re-run of the 2026-09-09 census after E25 added the fvg_range branch; the
denominator is unchanged at 52, and every figure below is reproducible by
importing `regime_debt_matrix` and calling `classify()` + `build_harness_cmd()`
over `load_legs()` -- no fetch, no harness run):

  * **51/52 (98.1%)** match a harness family BY NAME -- an UPPER BOUND, and it
    is the number MI-215 first published. It does not establish that the harness
    accepts a leg's parameters. (Not re-derived on 2026-09-22; carried from
    2026-09-09.)
  * **43/52 (82.7%)** actually route -- was 42/52 (80.8%) on 2026-09-09.
    `regime_debt_matrix.classify()` now has FOUR branches (donchian /
    trend_lookback|pullback_frac / kc_mult+bb_period / range_lookback+third_frac)
    against a THIRTEEN-family harness fleet. **The nine that still do not route
    are `turtle_soup` plus the EIGHT-leg `ict_scalp_*` family, and all eight of
    those are `execution: live`** -- `backtest_ict_scalp.py` exists and nothing
    routes to it, which is the same defect E25 fixed for fvg_range, at 8x the
    size. Tracked as PI-20260922-E25-ICT-SCALP-FAMILY-STILL-UNROUTED.
  * **30/52 (57.7%)** grade `faithful` -- the harness models EVERY lever the
    leg's config declares (was 29/52, 55.8%). The other 13 grade `approximate`.

**57.7% is the number this record rests on.** Do not quote 98.1%.

⚠️ `fidelity` IS IN THE RECORD, AND THAT IS DELIBERATE
------------------------------------------------------
An `approximate` edge number is evidence about a leg that is **not quite this
one** -- the harness ran without some lever the config declares. That is the same
hazard `config_fingerprint` exists for, one step along: the fingerprint catches a
config that MOVED, fidelity catches a config the harness never modelled.

The producer therefore **records the grade and refuses to decide what it means**.
A consumer branches on it. Collapsing `approximate` into `measured` would launder
a weaker number into evidence; dropping it would discard a usable one. Which line
to draw is an operator decision, and leaving it to the consumer means it can be
moved later without regenerating anything. Not hypothetical: the FIRST leg run
graded `approximate`, naming `trail_decay_stall_bars` and `trail_decay_tight_mult`.

⚠️ `basis` IS `harness_timefolds`, NEVER `purged_walkforward`
-------------------------------------------------------------
The harnesses do not do purged walk-forward CV. `--start/--end` is a SINGLE
window, and `regime_cell_walkforward.py` folds EMITTED TRADES into contiguous
time-folds after the fact. For a rule-based leg with fixed params that is
defensible -- nothing is being fit, so there is no leakage to purge -- but it is
**not the same object** as `ml/promotion/oos_edge.py`'s purged WF-CV, and reusing
that term would be exactly the unprovenanced-diagnostic failure this repo has a
guard family for. The field says what was actually done.

⚠️ AND `net_r_oos` IS NOT A PROMISE THAT PARAMS WERE CHOSEN OUT OF SAMPLE
The folds are out-of-sample with respect to EACH OTHER. If a leg's parameters
were tuned on this same history, the pooled number is optimistic and no field
here can detect that. `param_selection_provenance` records that we did not
establish it, rather than implying we did.

FOUR COVERAGE STATES, NEVER COLLAPSED
-------------------------------------
`no_harness` (nothing routes -- we know) · `harness_failed` (it ran and broke --
we looked) · `not_attempted` (we did not run it) · `measured`. An empty record
directory means the producer never ran; it never means the fleet has no edge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Repo root. This file lives at scripts/ops/, so parents[2] -- NOT parents[1],
#: which is what scripts/check_soak_doctrine.py uses because it sits one level up.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))

OUT_DIR = ROOT / "comms" / "strategy_evidence"

#: Where a run's raw harness output lands, and therefore what `source_run` and
#: `cost_stack.source` POINT AT. Repo-relative and COMMITTED, deliberately.
#:
#: ⚠️ This used to be `tempfile.mkdtemp()`, and every record written before
#: 2026-09-22 carries a `/tmp/...` path that no longer exists on any machine
#: (`PI-20260922-EVIDENCE-SOURCE-RUN-IS-A-TMP-PATH`, measured over all 52
#: committed records: 42 name a dead `/tmp` dir, 10 name nothing). Under
#: `docs/CLAUDE-RULES-CANONICAL.md` § "A MEASURED must say WHERE THE
#: MEASUREMENT LIVES", a record whose locator cannot be reached is not MEASURED
#: -- it degrades to INFERRED from an unstated measurement. The per-trade rows
#: ARE the measurement `net_r_oos` is pooled from, so they belong in the repo
#: beside the record that cites them.
#:
#: `runtime_logs/` is NOT an option: it is gitignored (.gitignore:33), so a
#: locator under it is exactly as unreachable as `/tmp` to anyone but the
#: machine that ran it.
#:
#: The fetched candle feed (`<leg>__data.csv`) is NOT committed -- it is an
#: input reproducible from `data.binance.vision` / Yahoo by re-running, not a
#: measurement -- and is ignored by `comms/strategy_evidence/runs/.gitignore`.
RUNS_DIR_REL = "comms/strategy_evidence/runs"
SCHEMA_VERSION = 2
GENERATOR = "scripts/ops/build_strategy_evidence.py"

#: R1/D1 — the Stage-0 clearance rule this producer registers, in this literal
#: string, BEFORE any run in this session. `registered_at` is a real wall-clock
#: timestamp taken before the first `build_record()` call below was invoked
#: (verified against this branch's commit history, not backdated), so C4's
#: `registered_at < generated_at` check is genuine rather than satisfied by
#: construction. The rule restates the existing Stage-0 bar (CLAUDE.md promotion
#: ladder: "is there an edge, NET OF THE FULL COST STACK") as a checkable
#: predicate over this producer's own fields -- it does not set or change that
#: bar, and it does not decide anything about what trades (R2/D2 does that).
DECISION_RULE_ID = "RULE-D1-STAGE0-NET-OF-FULL-COST"
DECISION_RULE_TEXT = (
    "Stage 0 requires net_r_oos -- pooled net_total_r from the harness's "
    "out-of-sample time-folds, net of the FULL cost stack (fee + slippage + "
    "funding) -- to be > 0. Restates the existing promotion-ladder Stage-0 bar "
    "as a checkable predicate over this record's own fields; does not itself "
    "authorize or perform any roster change."
)
DECISION_RULE_REGISTERED_AT = "2026-09-22T05:34:08Z"

#: What the folds actually are. NOT `purged_walkforward` -- see the module docstring.
BASIS = "harness_timefolds"

#: Coverage states. Four, and they answer different questions.
COVERAGE_STATES = ("measured", "no_harness", "harness_failed", "not_attempted")

DEFAULT_FOLDS = 4
DEFAULT_DAYS = 365
#: Below this, a pooled number is noise dressed as evidence. Reported, never silently dropped.
MIN_TRADES_FOR_POOLED = 10


def config_fingerprint(cfg: Dict[str, Any]) -> str:
    """Stable digest of a leg's config block.

    An edge record is evidence about the leg AS CONFIGURED, so a moved config
    makes the record STALE rather than silently wrong. Sorted keys + separators
    so formatting churn does not change the digest.
    """
    blob = json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _read_trades(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a malformed row is skipped, never guessed at
    except OSError:
        return []
    return out


def time_folds(trades: List[Dict[str, Any]], folds: int) -> List[Dict[str, Any]]:
    """Split trades into `folds` CONTIGUOUS, equal-count time blocks.

    Contiguous and time-ordered, never random: a random split would leak
    neighbouring market state across folds and inflate agreement between them.
    Equal-count rather than equal-duration so a quiet stretch does not produce an
    empty fold whose net-R of 0.0 would read as a flat result.
    """
    dated = [t for t in trades if t.get("entry_time")]
    dated.sort(key=lambda t: str(t["entry_time"]))
    n = len(dated)
    if n == 0 or folds < 1:
        return []
    size = max(1, n // folds)
    out: List[Dict[str, Any]] = []
    for i in range(folds):
        lo = i * size
        hi = n if i == folds - 1 else min(n, lo + size)
        block = dated[lo:hi]
        if not block:
            continue
        rs = [float(t.get("net_r") or 0.0) for t in block]
        out.append({
            "fold": i + 1,
            "trades": len(block),
            "net_r": round(sum(rs), 4),
            "expectancy_r": round(sum(rs) / len(rs), 4),
            "start": str(block[0].get("entry_time")),
            "end": str(block[-1].get("entry_time")),
        })
    return out


def build_record(name: str, cfg: Dict[str, Any], *, workdir: str,
                 days: int, folds: int) -> Dict[str, Any]:
    """Produce one leg's evidence record. Never raises on a leg-level failure."""
    import regime_debt_matrix as rdm  # local: pulls pandas transitively

    now = datetime.now(timezone.utc).isoformat()
    symbols = cfg.get("symbols") or []
    rec: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strategy": name,
        "symbol": symbols[0] if symbols else None,
        "timeframe": cfg.get("timeframe"),
        "config_fingerprint": config_fingerprint(cfg),
        "basis": BASIS,
        "coverage_state": "not_attempted",
        "fidelity": None,
        "omitted_levers": None,
        "net_r_oos": None,
        "expectancy_r_oos": None,
        "n_trades_oos": None,
        "folds": None,
        "fold_detail": None,
        "folds_positive": None,
        "window_days": days,
        "fee_bps_roundtrip": None,
        "cost_stack": None,
        "net_r_oos_fee_only": None,
        "decision_rule": None,
        # ⚠️ We did NOT establish where this leg's parameters came from. If they
        # were tuned on this same history the pooled number is optimistic, and no
        # field here can detect that. Stated rather than implied.
        "param_selection_provenance": "not_established",
        "generated_at": now,
        "generated_by": GENERATOR,
        "source_run": None,
        "error": None,
    }

    harness = rdm.classify(cfg)
    rec["harness"] = harness
    if harness is None:
        rec["coverage_state"] = "no_harness"
        rec["error"] = (
            "regime_debt_matrix.classify() routes nothing for this leg. Its "
            "four branches (donchian / trend_lookback|pullback_frac / "
            "kc_mult+bb_period / range_lookback+third_frac) do not cover this "
            "config. A harness may still EXIST for the family -- "
            "backtest_ict_scalp.py does, and nothing routes to it -- so this "
            "is a missing classifier branch plus a lever map, not absent "
            "infrastructure. The fvg_range branch was added the same way on "
            "2026-09-22 (E25); that is the worked example to copy."
        )
        return rec

    try:
        row = rdm.run_one(name, cfg, workdir, days=days)
    except Exception as e:  # noqa: BLE001 - a leg-level failure must not stop the sweep
        rec["coverage_state"] = "harness_failed"
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    rec["fidelity"] = row.get("fidelity")
    rec["omitted_levers"] = row.get("omitted_levers")
    rec["fee_bps_roundtrip"] = row.get("fee_bps_roundtrip")
    if row.get("error"):
        rec["coverage_state"] = "harness_failed"
        rec["error"] = str(row["error"])
        return rec

    emit = os.path.join(workdir, f"{name}__trades.jsonl")
    rec["source_run"] = emit
    trades = _read_trades(emit)
    if not trades:
        rec["coverage_state"] = "harness_failed"
        rec["error"] = (
            "harness ran without error but emitted no trades. ZERO TRADES IS NOT "
            "AN EDGE OF ZERO -- it is an absence of measurement, so no pooled "
            "number is written."
        )
        return rec

    fd = time_folds(trades, folds)
    rs = [float(t.get("net_r") or 0.0) for t in trades]
    rec.update({
        "coverage_state": "measured",
        "n_trades_oos": len(trades),
        "net_r_oos": round(sum(rs), 4),
        "expectancy_r_oos": round(statistics.fmean(rs), 4) if rs else None,
        "folds": len(fd),
        "fold_detail": fd,
        # How many folds were individually positive. A pooled positive carried by
        # ONE fold is a different fact from one positive in every fold, and the
        # pooled number alone cannot tell them apart.
        "folds_positive": sum(1 for f in fd if f["net_r"] > 0),
    })
    if len(trades) < MIN_TRADES_FOR_POOLED:
        rec["low_n_warning"] = (
            f"n_trades_oos={len(trades)} < {MIN_TRADES_FOR_POOLED}; the pooled "
            "number is reported but is not a basis for a verdict."
        )

    # C3 — cost_stack. `trend`/`squeeze`/`pullback`'s own CLI path (main())
    # already resolves unset --slippage/--funding to the venue-aware defaults
    # (scripts/backtest_trend.py:74-76) before it writes its `--json` summary,
    # so the emitted `net_r` per trade is ALREADY net of the full cost stack --
    # this block only makes that resolved stack legible in the record, it does
    # not change what was measured. Read from the harness's own summary JSON
    # (same `<name>__bt.json` path `regime_debt_matrix.run_one` writes to,
    # reconstructed here rather than threaded through `row` to avoid widening
    # that function's return contract for one caller) rather than re-deriving
    # it, per "read the field, not the prose about it".
    bt_json = os.path.join(workdir, f"{name}__bt.json")
    try:
        with open(bt_json, encoding="utf-8") as fh:
            bt = json.load(fh)
    except (OSError, ValueError):
        bt = {}
    fee_bps = bt.get("fee_bps_roundtrip", rec.get("fee_bps_roundtrip"))
    slip_bps = bt.get("slippage_bps_roundtrip")
    fund_bps = bt.get("funding_bps_per_window")
    if isinstance(fee_bps, (int, float)) and isinstance(slip_bps, (int, float)) \
            and isinstance(fund_bps, (int, float)):
        rec["cost_stack"] = {
            "fees": fee_bps, "slippage": slip_bps, "funding": fund_bps,
            "unit": "bps_roundtrip (funding: bps per funding window)",
            "source": bt_json,
        }
    # net_r_fee_only rides on the same per-trade rows net_r_oos was pooled
    # from -- present whenever the harness stamps it (trend/squeeze/pullback
    # all do; a harness that doesn't leaves this None rather than a guessed 0).
    fee_only = [t.get("net_r_fee_only") for t in trades]
    if all(isinstance(v, (int, float)) for v in fee_only) and fee_only:
        rec["net_r_oos_fee_only"] = round(sum(fee_only), 4)

    # C4 — the rule registered above, BEFORE this run, graded against what was
    # just measured. Never computed unless coverage_state is actually
    # `measured` -- a rule graded against a number that was not measured is
    # exactly the post-hoc-looking record this clause exists to refuse.
    rec["decision_rule"] = {
        "id": DECISION_RULE_ID,
        "rule": DECISION_RULE_TEXT,
        "registered_at": DECISION_RULE_REGISTERED_AT,
        "verdict": "pass" if rec["net_r_oos"] is not None and rec["net_r_oos"] > 0 else "fail",
    }
    return rec


def write_record(rec: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{rec['strategy']}.json"
    p.write_text(json.dumps(rec, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return p


def load_legs() -> Dict[str, Dict[str, Any]]:
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "strategies.yaml").read_text())
    blk = cfg.get("strategies", cfg)
    return {n: b for n, b in blk.items()
            if isinstance(b, dict) and b.get("enabled", True)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strategy", action="append", default=None,
                    help="Leg name; repeatable. Default: every enabled leg.")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--workdir", default=None,
                    help="Where harness output lands, and therefore what "
                         f"source_run points at. Default: {RUNS_DIR_REL}/<UTC-date>/ "
                         "(repo-relative and committed). A path outside the repo "
                         "makes source_run unreachable -- see RUNS_DIR_REL.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Classify + grade fidelity only. No fetch, no harness run.")
    a = ap.parse_args(argv)

    legs = load_legs()
    names = a.strategy or sorted(legs)
    missing = [n for n in names if n not in legs]
    if missing:
        print(f"unknown or disabled leg(s): {missing}", file=sys.stderr)
        return 2

    out_dir = Path(a.out)
    # Repo-relative by default so `source_run` is a locator a later session can
    # actually reach -- see RUNS_DIR_REL. `--workdir` still accepts anything;
    # passing a path outside the repo reintroduces the defect knowingly.
    wd = a.workdir or os.path.join(
        RUNS_DIR_REL, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    os.makedirs(wd, exist_ok=True)
    counts: Dict[str, int] = {s: 0 for s in COVERAGE_STATES}

    for n in names:
        if a.dry_run:
            import regime_debt_matrix as rdm
            h = rdm.classify(legs[n])
            state = "no_harness" if h is None else "not_attempted"
            counts[state] += 1
            print(f"  {n:34s} harness={str(h):10s} {state}")
            continue
        rec = build_record(n, legs[n], workdir=wd, days=a.days, folds=a.folds)
        counts[rec["coverage_state"]] += 1
        write_record(rec, out_dir)
        print(f"  {n:34s} {rec['coverage_state']:15s} "
              f"fidelity={str(rec.get('fidelity')):12s} "
              f"net_r_oos={rec.get('net_r_oos')} n={rec.get('n_trades_oos')}")

    total = sum(counts.values())
    print(f"\nstrategy-evidence: {total} leg(s)")
    for s in COVERAGE_STATES:
        print(f"  {s:15s} {counts[s]}")
    # ⚠️ Deliberately NOT an aggregate verdict. This producer states coverage and
    # refuses to say whether the fleet has edge -- that is the consumer's job, and
    # conflating the two is how a producer starts deciding things.
    return 0


if __name__ == "__main__":
    sys.exit(main())
