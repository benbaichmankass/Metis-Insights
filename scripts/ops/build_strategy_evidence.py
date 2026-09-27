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
  * **51/52 (98.1%)** actually route -- was 43/52 (82.7%) before E28 added the
    fifth branch the same day. `regime_debt_matrix.classify()` now has FIVE
    (donchian / trend_lookback|pullback_frac / kc_mult+bb_period /
    range_lookback+third_frac / sweep_lookback_bars+mitigation_mode) against a
    THIRTEEN-family harness fleet. **The one that still does not route is
    `turtle_soup`.** ⚠️ That this now EQUALS the by-name upper bound above is a
    coincidence of arithmetic, not the same measurement -- the two would part
    again the moment a leg matched a family by name without its config being
    accepted, which is exactly what the upper bound cannot see.
  * **37/52 (71.2%)** grade `faithful` -- the harness models EVERY lever the
    leg's config declares. The other **14** of the 51 routed grade
    `approximate` (37 + 14 = 51; the 52nd is `turtle_soup`, unrouted), and
    `ict_scalp_xrp_5m` is one of the 14: its `off_cells` is `not_expressible`
    by this harness (MI-321), so its number is the UNGATED arm.
    ⚠️ **THIS NUMBER HAS A HISTORY WORTH READING, NOT JUST QUOTING.** It read
    37/52 (71.2%) until E46 (2026-09-22) found that WRONG for the wrong
    reason -- `tp_r` sat in the trend/pullback `PLAIN` sets ASSERTING the
    harness modelled it, while `build_harness_cmd` passed neither `--tp-r`
    nor the `--tp-cap-pct` that makes it take effect, so 12 legs claimed
    `faithful` with `omitted_levers: []` against a declared, binding
    take-profit (`PI-20260922-E41-0005`). E46 corrected the grade to
    25/52 (48.1%) by computing it from the argv that actually ran, rather
    than fixing the harness -- the honest number for a harness that could
    not model `tp_r` at all. **E55 (2026-09-24) is what fixed the harness**:
    `regime_debt_matrix._tp_r_flags` now forwards `--tp-cap-pct`/`--tp-r`
    (live-parity with the venue TP clamp `src/runtime/tp_venue_cap.py` owns)
    for every trend/pullback leg with a readable `tp_r`, so those same 12
    legs are now genuinely modelled and the count lands back at 37/52 --
    the SAME figure as before E46, for the OPPOSITE and now-correct reason.
    Regenerating the corpus through the harness (never hand-edited) flipped
    8 of the 41 enabled trend/pullback legs' Stage-0 verdicts; none of the 8
    are on a real-money account (`config/accounts.yaml`, checked explicitly
    against all four) -- see `PI-20260924-EDNBNMSG-0001`.

**71.2% is the number this record rests on, as of 2026-09-24.** Do not quote
98.1%, and do not quote the intermediate 48.1% as current -- both are real
measurements of this corpus at different points in its history, not
interchangeable readings of today.

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

⚠️ AND A FAILED RUN MAY NEVER OVERWRITE A `measured` RECORD (E46, 2026-09-22)
----------------------------------------------------------------------------
OBSERVED, not inferred: `yfinance` was absent from a sandbox on 2026-09-22, so
every leg graded `harness_failed` and this producer REWROTE the committed
records for `gld_pullback_1d` and `qqq_trend_long_1d`, nulling `net_r_oos`,
`n_trades_oos` and `fold_detail`. Two real measurements, destroyed by an
ordinary environment gap; they survived only because the lane read `git diff`
before committing (`PI-20260922-E41-0007`).

The four states above are only honest while they survive a bad run. A
`harness_failed` stub written over a measurement does NOT read as a loss -- it
reads as a leg nobody ever measured, which is this repo's own collapsed state
("we could not look" rendered identically to "we looked and found nothing")
sitting on the corpus B1 reads to admit a leg to a REAL-MONEY roster.

So `write_record` REFUSES, and the refusal is loud on three surfaces because a
tool that quietly declines to update is its own collapsed state: a stderr line,
a `<leg>__refused_record.json` sidecar beside the run's other output carrying
the record it would have written, and exit `EXIT_REFUSED_OVERWRITE` (3) from
`main()`. The committed record comes out BYTE-IDENTICAL -- asserted on the real
committed artifact in `tests/test_strategy_evidence.py`, with the negative
control that proves the plant reaches the writer.

⚠️ A SUCCESSFUL re-measure overwrites exactly as before. `measured -> measured`
is a different question and this guard does not touch it.
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
#: ⚠️ This used to be `tempfile.mkdtemp()`. MEASURED on `0e0a8f3` over all 52
#: committed records: **42 name a `/tmp/...` dir that exists on no machine
#: today, and 10 name nothing at all** -- so this is NOT only a pre-2026-09-22
#: problem, R1's own 2026-09-22 records name `/tmp/tmp.RsNZr5lfkw`
#: (`PI-20260922-EVIDENCE-SOURCE-RUN-IS-A-TMP-PATH`). Under
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

#: What `write_record` decided to do about a record already on disk. Three
#: values, never collapsed -- a `refuse_*` that rendered the same as a `write`
#: would be a silent skip, which is its own collapsed state.
OVERWRITE_DECISIONS = ("write", "refuse_measured", "refuse_unreadable")

#: Exit code when at least one refusal fired. NOT 2 -- that is already the
#: usage error for an unknown leg, and "you asked for a leg that does not
#: exist" and "this run tried to destroy a measurement" are different findings.
#: A refusal is loud on stderr AND in the exit code, because a producer whose
#: only signal is a line of stdout is one `| tail` away from silent.
EXIT_REFUSED_OVERWRITE = 3

DEFAULT_FOLDS = 4
DEFAULT_DAYS = 365
#: Below this, a pooled number is noise dressed as evidence. Reported, never silently dropped.
MIN_TRADES_FOR_POOLED = 10


# Fields that are EXECUTION GATES, not strategy parameters. They decide whether
# a leg trades; they do not change what the harness would produce for it, so
# they are excluded from the fingerprint.
#
# ⚠️ THIS EXCLUSION IS LOAD-BEARING AND WAS FOUND BY A DEFECT, 2026-09-22 (E40).
# Until it existed, the digest covered the WHOLE config block, so flipping
# `execution: shadow -> live` moved the fingerprint — which meant EVERY
# PROMOTION INVALIDATED ITS OWN EVIDENCE at the moment of promotion, and
# check_roster_promotion_evidence.py refused it with `identity FAIL ... STALE`
# while C1-C4 all passed. B1 landed 2026-09-21 and had permitted zero
# promotions; the first one attempted found it.
#
# ⚠️ DO NOT WIDEN THIS SET TO QUIET A GUARD COMPLAINT. A moved `atr_stop_mult`,
# `entry_z`, `lookback` or any other parameter the harness reads MUST still
# invalidate the record — that is the whole job of the check, and the
# `--self-test` plants exactly that case.
_GATE_FIELDS: frozenset = frozenset({"execution", "enabled"})


def config_fingerprint(cfg: Dict[str, Any]) -> str:
    """Stable digest of a leg's STRATEGY PARAMETERS.

    An edge record is evidence about the leg AS CONFIGURED, so a moved config
    makes the record STALE rather than silently wrong. Sorted keys + separators
    so formatting churn does not change the digest.

    Execution gates (`_GATE_FIELDS`) are excluded: they decide whether the leg
    trades, not what a backtest of it would produce. See the note above.
    """
    params = {k: v for k, v in (cfg or {}).items() if k not in _GATE_FIELDS}
    blob = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
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


def path_stats(trades: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Descriptive PATH statistics over the ordered per-trade R series.

    WHY THIS EXISTS (B6). `net_r_oos` is a SUM and `fold_detail` is a sum per
    fold, and neither is an equity PATH: a fold reading -5.39R may never have
    been 5.39R underwater at any single moment. The prop bar the operator set
    is `E[profit before the account dies] > the account cost`, which is a
    ruin-with-payoff question, and the quantity it needs -- P(target before
    floor) -- is a property of the PATH, not of the total. Measured 2026-09-22
    across all 52 committed records: none carried a path statistic, so that
    probability was not computable for any leg.

    ⚠️ THIS FUNCTION DELIBERATELY DOES NOT GRADE ANYTHING. It emits
    descriptive statistics and stops. The prop bar is registered by B6 and it
    must be registered BEFORE these numbers are read; a producer that also
    scored them against `breakout_1`'s 4.0R floor / 6.67R target would be
    writing the rule after seeing the result, which is the exact ordering
    clause C4 exists to refuse. No threshold from config/prop_rulesets/ is
    imported here, on purpose.

    ⚠️ AND THE SUMMARY IS NOT A SUBSTITUTE FOR THE SERIES. `source_run` names
    the committed per-trade rows; anything a later consumer needs that is not
    here (a first-passage simulation, a bootstrap, a different ordering) is
    computed from those rows. This block exists so the common questions do not
    require reading 80 lines of JSONL, not so the JSONL can be discarded.
    """
    dated = [t for t in trades if t.get("entry_time")]
    dated.sort(key=lambda t: str(t["entry_time"]))
    rs = [float(t.get("net_r") or 0.0) for t in dated]
    if not rs:
        return None

    # Running equity in R, and its worst peak-to-trough excursion. This is the
    # `how far underwater did it actually go` number a per-fold sum cannot give.
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    trough_at = 0
    for i, r in enumerate(rs):
        equity += r
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd, trough_at = dd, i + 1

    # Worst single calendar day, by EXIT date -- a day's damage is realised when
    # the trades close, not when they opened. Trades with no exit_time are
    # excluded and the count says how many, rather than being folded in at 0.0.
    by_day: Dict[str, float] = {}
    no_exit = 0
    for t in dated:
        et = t.get("exit_time")
        if not et:
            no_exit += 1
            continue
        by_day[str(et)[:10]] = by_day.get(str(et)[:10], 0.0) + float(t.get("net_r") or 0.0)
    worst_day = min(by_day.values()) if by_day else None
    worst_day_date = min(by_day, key=by_day.get) if by_day else None

    return {
        "series_location": "source_run (the per-trade rows, in order, with net_r)",
        "n": len(rs),
        "max_drawdown_r": round(max_dd, 4),
        "max_drawdown_at_trade": trough_at,
        "final_equity_r": round(equity, 4),
        "max_equity_r": round(peak, 4),
        "worst_trade_r": round(min(rs), 4),
        "best_trade_r": round(max(rs), 4),
        "worst_day_r": round(worst_day, 4) if worst_day is not None else None,
        "worst_day_date": worst_day_date,
        "trades_without_exit_time": no_exit,
        "ordering": "by entry_time ascending; pooled ACROSS folds, so this is the "
                    "full-window path, not a per-fold one",
        "not_a_verdict": "Descriptive only. No bar is applied here -- the prop "
                         "bar is B6's to register, and it must be registered "
                         "before these numbers are read.",
    }


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
        "path_stats": None,
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
            "five branches (donchian / trend_lookback|pullback_frac / "
            "kc_mult+bb_period / range_lookback+third_frac / "
            "sweep_lookback_bars+mitigation_mode) do not cover this config. A "
            "harness may still EXIST for the family, so this is a missing "
            "classifier branch plus a lever map, not necessarily absent "
            "infrastructure -- check scripts/backtest_*.py before concluding "
            "otherwise. The fvg_range branch was added that way on 2026-09-22 "
            "(E25) and the ict_scalp family the same day (E28); those are the "
            "worked examples to copy. ⚠️ E28 is the one to read FIRST if the "
            "harness reads config/strategies.yaml itself, because it had to fix "
            "that read before the route was sound."
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
        # B6 -- the equity PATH, which no sum can reconstruct. See path_stats().
        "path_stats": path_stats(trades),
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


def existing_coverage_state(path: Path) -> str:
    """What the record ALREADY on disk says, before this run touches it.

    Returns a `COVERAGE_STATES` value, or one of two states that are NOT
    coverage states and must never be folded into them:

    * ``absent`` -- there is no record at this path.
    * ``unreadable`` -- there IS one and we could not parse it.

    ⚠️ ``unreadable`` IS NOT ``absent``, and collapsing the two is how this
    guard would be walked around: "there was no measurement here" and "we could
    not look at the measurement that is here" are opposite statements, and only
    one of them makes an overwrite safe. A truncated or half-written
    `measured` record is exactly the artifact a clobbering run produces, so
    reading a parse failure as "nothing to protect" would hand the destructive
    write the one case it most needs refused. Same shape as
    `src/runtime/exit_anchor.py`'s three-way contract and
    `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states".
    """
    if not path.exists():
        return "absent"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(rec, dict):
        return "unreadable"
    state = rec.get("coverage_state")
    if state not in COVERAGE_STATES:
        # A record whose coverage_state is missing or is a value this producer
        # never emits is not a record we understand. Treat it as unreadable
        # rather than as "not measured" -- see the note above.
        return "unreadable"
    return state


def overwrite_decision(existing: str, incoming: str) -> str:
    """Decide whether `incoming` may replace the record `existing` describes.

    THE ONE RULE, and its shape is not negotiable (E46 / PI-20260922-E41-0007):
    **a run that did not measure anything may never overwrite a record that
    currently reads `measured`.**

    OBSERVED 2026-09-22, not inferred: `yfinance` was absent from a sandbox, so
    every leg graded `harness_failed`, and the producer rewrote the committed
    `measured` records for `gld_pullback_1d` and `qqq_trend_long_1d` -- nulling
    `net_r_oos`, `n_trades_oos` and `fold_detail`. They survived only because
    the lane read `git diff` before committing.

    Why this is worse than losing a number. `comms/strategy_evidence/` is the
    corpus B1's four-clause bar reads to decide whether a leg may reach a
    REAL-MONEY roster, and a `harness_failed` stub does not read as a loss --
    it reads as a leg nobody ever measured. That is this repo's own collapsed
    state ("we could not look" rendered identically to "we looked and found
    nothing") sitting on the promotion path.

    ⚠️ WHAT THIS DELIBERATELY DOES NOT DECIDE: whether a SUCCESSFUL re-measure
    may overwrite a `measured` record. It may, exactly as before -- that is a
    different question and E46 was fenced out of it. `measured -> measured`
    returns ``write``.
    """
    if existing == "unreadable":
        return "refuse_unreadable"
    if existing == "measured" and incoming != "measured":
        return "refuse_measured"
    return "write"


def _repo_rel(path: Path) -> str:
    """Repo-relative when it can be, absolute when it genuinely is elsewhere.

    An absolute sandbox path is a locator that exists on no other machine --
    the same defect `RUNS_DIR_REL` exists to keep out of `source_run`, and it
    would be no better inside a refusal note.
    """
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _refusal_note(leg: str, decision: str, existing: str, incoming: str,
                  target: Path) -> str:
    if decision == "refuse_measured":
        why = (f"the committed record reads coverage_state={existing!r} and this "
               f"run produced {incoming!r}, which is not a measurement")
    else:
        why = (f"the record at {_repo_rel(target)} could not be read, so whether it holds a "
               f"measurement is UNKNOWN -- and unknown is not permission")
    return (f"REFUSED to overwrite {leg}: {why}. The committed record is "
            f"UNCHANGED, byte for byte.")


def write_record(rec: Dict[str, Any], out_dir: Path, *,
                 refusal_dir: Optional[Path] = None) -> tuple[Optional[Path], str]:
    """Write one leg's record, or REFUSE and leave the committed bytes alone.

    Returns ``(path, decision)``: `path` is the file written, or ``None`` when
    nothing was written; `decision` is an `OVERWRITE_DECISIONS` value.

    ⚠️ THE REFUSAL IS LOUD HERE, INSIDE THE WRITER, not only in the caller's
    return-value handling. A tool that quietly declines to update is its own
    collapsed state -- indistinguishable from one that updated successfully --
    so the stderr line and (when `refusal_dir` is given) the sidecar do not
    depend on any caller remembering to check. `main()` adds the third signal,
    a non-zero exit.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{rec['strategy']}.json"
    existing = existing_coverage_state(p)
    decision = overwrite_decision(existing, rec.get("coverage_state"))
    if decision != "write":
        note = _refusal_note(rec["strategy"], decision, existing,
                             rec.get("coverage_state"), p)
        print(f"  !! {note}", file=sys.stderr)
        # The failure is not thrown away: the record this run WOULD have
        # written lands beside the run's other output, so its `error` is
        # readable afterwards rather than existing only in a terminal.
        if refusal_dir is not None:
            try:
                refusal_dir.mkdir(parents=True, exist_ok=True)
                side = refusal_dir / f"{rec['strategy']}__refused_record.json"
                side.write_text(json.dumps({
                    "refusal": {
                        "decision": decision,
                        "existing_coverage_state": existing,
                        "incoming_coverage_state": rec.get("coverage_state"),
                        "target": _repo_rel(p),
                        "note": note,
                        "refused_at": datetime.now(timezone.utc).isoformat(),
                        "guard": "build_strategy_evidence.overwrite_decision",
                    },
                    "would_have_written": rec,
                }, indent=2) + "\n", encoding="utf-8")
                print(f"     failure detail: {side}", file=sys.stderr)
            except OSError as e:  # noqa: BLE001
                # Losing the sidecar must not turn a refusal into a write, and
                # must not be silent either.
                print(f"     (could not write the refusal sidecar: {e})",
                      file=sys.stderr)
        return None, decision
    p.write_text(json.dumps(rec, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return p, decision


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
    #: Legs whose committed record this run refused to overwrite. Reported
    #: separately from `counts`, because a refused leg's coverage_state is a
    #: true statement about THIS RUN while the file on disk still holds the
    #: earlier measurement -- folding the two together is what would let the
    #: summary imply a record now reads `harness_failed` when it does not.
    refused: List[tuple] = []

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
        _, decision = write_record(rec, out_dir, refusal_dir=Path(wd))
        if decision != "write":
            refused.append((n, decision))
        print(f"  {n:34s} {rec['coverage_state']:15s} "
              f"fidelity={str(rec.get('fidelity')):12s} "
              f"net_r_oos={rec.get('net_r_oos')} n={rec.get('n_trades_oos')}"
              f"{'  [REFUSED - committed record left intact]' if decision != 'write' else ''}")

    total = sum(counts.values())
    print(f"\nstrategy-evidence: {total} leg(s)")
    for s in COVERAGE_STATES:
        print(f"  {s:15s} {counts[s]}")
    print(f"  {'refused_overwrite':15s} {len(refused)}"
          "   (this run's state; the committed record is unchanged)")
    # ⚠️ Deliberately NOT an aggregate verdict. This producer states coverage and
    # refuses to say whether the fleet has edge -- that is the consumer's job, and
    # conflating the two is how a producer starts deciding things.
    if refused:
        print("\nREFUSED TO OVERWRITE A COMMITTED MEASUREMENT:", file=sys.stderr)
        for leg, decision in refused:
            print(f"  {leg:34s} {decision}", file=sys.stderr)
        print("Nothing was lost. Fix the run (the usual cause is a missing "
              "feed dependency -- yfinance for the Yahoo lane) and re-run; a "
              "SUCCESSFUL re-measure still overwrites normally.", file=sys.stderr)
        return EXIT_REFUSED_OVERWRITE
    return 0


if __name__ == "__main__":
    sys.exit(main())
