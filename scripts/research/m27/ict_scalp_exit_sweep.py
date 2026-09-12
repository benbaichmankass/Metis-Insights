#!/usr/bin/env python3
"""M27 — config-exact exit-lever IS/OOS sweep for the ict_scalp gold leg.

The exit-refinement skill's P2 stage for `ict_scalp_mgc_15m`, run against the
powered Dukascopy spot-XAU 15m proxy (the same 178k-bar dataset that carried the
entry decision — MGC's own IBKR 15m history is structurally too thin, see
docs/research/M27-P0-MGC-15m-findings-2026-07-28.md). Uses the NEW exit-lever
flags on scripts/backtest_ict_scalp.py.

CONFIG-EXACT: mirrors the M27 P0 invocation (scripts/research/m27/run_symbol_p0.py)
— `--symbol MGC --timeframe 15m --sim-breakeven` + the harness defaults, loading
the ict_scalp_5m YAML detection params (the mgc leg is a config-exact copy).

GATE (M20 IS/OOS pre-filter): a cell is a CANDIDATE only if it beats the
config-exact baseline on net_R (total_r, higher better) AND maxDD (max_drawdown_r,
lower better) in BOTH the in-sample and out-of-sample windows. Anything else is an
honest_negative at this stage. Candidates would then go to a yearly walk-forward
(the M20 confirmation step) before any Tier-3 live-monitor declare.

R-metrics are the harness's fee-free R (the established M20 lever-gate basis); the
baseline-vs-cell comparison is fee-neutral. Trade-count deltas are reported as a
fee-sensitivity proxy (an earlier exit can free earlier re-entries).

Tier-1 research tooling — never writes config. Usage:
    python3 scripts/research/m27/ict_scalp_exit_sweep.py \
        --data data/XAUUSD_15m_deep.csv --split 2025-07-01 --out <dir>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[3]

# THE CAPITAL-EFFICIENCY COMPARISON HAS ONE OWNER AND IS IMPORTED, NOT COPIED.
# exit-refinement/SKILL.md: "single-homed in scripts/capital_efficiency.py --
# never re-derived per harness, or a cross-harness comparison means nothing."
# `capital_delta` is the cell-vs-base reporter built on it; importing keeps this
# sweep's Path B rows comparable with the fleet sweep's. The module guards its
# own main(), so the import has no side effects (verified: 0.03s, no argparse).
def _capital_delta(cell: dict, base: dict) -> dict:
    import importlib.util
    src = _REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py"
    spec = importlib.util.spec_from_file_location("_m20_fleet", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.capital_delta(cell, base)

_HARNESS = _REPO / "scripts" / "backtest_ict_scalp.py"

# (cell_tag, matrix_lever, extra harness args) — mirrors
# scripts/research/m20_fleet_exit_sweep.py::cells_for for the stale/giveback
# levers (the only M20 levers that apply to a fixed-bracket scalp).
CELLS = [
    ("stale8_lt0R", "stale_stop", ["--stale-exit-bars", "8"]),
    ("stale12_lt0R", "stale_stop", ["--stale-exit-bars", "12"]),
    ("gb1R_afterMFE1R", "giveback_stop",
     ["--giveback-min-mfe-r", "1.0", "--giveback-r", "1.0"]),
    ("gb1R_afterMFE2R", "giveback_stop",
     ["--giveback-min-mfe-r", "2.0", "--giveback-r", "1.0"]),
]

# M20 `exit_ladder` (partial-TP bank) cells — added 2026-08-09, once
# backtest_ict_scalp.py gained bank_frac/bank_at_r. The eight live ict_scalp
# legs read `blocked:no_harness_levers` for this column until then.
#
# The rungs are CONFIG-RELATIVE, as FRACTIONS of the leg's own tp_at_r, and
# this is load-bearing rather than tidiness: ict_scalp is a FIXED-bracket
# strategy, so a rung at or above tp_at_r coincides with the take-profit and
# the blend `f*tp + (1-f)*tp` returns tp exactly — a provable no-op. The
# fleet-wide default grid (m20_fleet_exit_sweep / m20_exit_sweep) uses
# bank_at_r in (1.0, 1.5); on a tp_at_r=1.5 leg HALF those cells would measure
# nothing and still report a confident "no effect". Fractions cannot drift
# into that. Proven in tests/test_ict_scalp_exit_levers.py::
# test_rung_at_or_above_tp_is_a_provable_noop.
_RUNG_FRACS_OF_TP = (1.0 / 3.0, 2.0 / 3.0)
_BANK_FRACS = (0.25, 0.5)


# M20 `bracket_geometry` (take-profit distance) cells — added 2026-09-12
# (MI-278 U3). Until `backtest_ict_scalp.py` gained `--tp-at-r` the scalp
# target was not merely UNSWEPT but UNSWEEPABLE: e35_bracket_geometry_sweep
# refuses the family by design (`out_of_scope_family`), m20_fleet_exit_sweep
# passes no target on its `scalp` branch, and this harness exposed 30
# `add_argument` calls and not one was a target. The 8 ict_scalp_* rows read
# `pending` in the coverage matrix, which understated that.
#
# THE GRID BRACKETS THE LIVE VALUE ON BOTH SIDES, and that is the whole design
# rather than a courtesy: MI-277's question is "are winners being cut short",
# which biases an author toward testing only WIDER targets, and a one-sided
# grid cannot tell "further is better" from "we only looked further".
#
# ⚠️ The live value itself is NOT emitted as a cell. It IS the baseline arm, so
# a cell equal to it would compare the baseline against itself and report a
# confident zero — the same provable-no-op shape `_RUNG_FRACS_OF_TP` avoids one
# lever up.
_TP_GRID = (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)


# M20 `breakeven_ratchet` cells — added 2026-09-12 (MI-278 U3). The ratchet
# (`_base.monitor_breakeven_sl`, `be_offset_bps: 15`, armed at 1R) is ON for
# all 8 live ict_scalp legs and rides in BASE_FLAGS as `--sim-breakeven`, i.e.
# it is part of the config-exact BASE of every scalp cell ever measured —
# CORRECT, and exactly why it has never been a swept VARIABLE.
#
# ⚠️ THIS CELL FAMILY IS A DISARM, WHICH IS THE OPPOSITE POLARITY TO EVERY
# OTHER CELL HERE. The others ADD a lever to the base; this one REMOVES one.
# Read a positive delta as "the shipped baseline is costing this much", not as
# "an override helps" — the two are easy to confuse in a verdict table.
#
# Screened on one leg-quarter (SOLUSDT 5m 2025Q3, live parity): disarming was
# worth +2.729 R at the live `tp_at_r`, with `be_stop` share rising 0.0% ->
# 40.0% as the target widened from 0.75R to 3.0R. That is a SCREEN, and it is
# what this cell exists to test properly.
#
# ⚠️ IT IS A NO-OP BELOW 1R BY CONSTRUCTION and the grid must not pretend
# otherwise: the ratchet arms at 1R, so on a leg whose `tp_at_r <= 1.0` the
# trade exits before it can ever arm, and the cell would measure exactly zero
# while reading like a tested negative. Verified to three decimals on the
# screen. Emitted only where it can bind — the same discipline
# `_RUNG_FRACS_OF_TP` applies to the ladder rungs.
_BE_ARM_AT_R = 1.0


def breakeven_cells(tp_at_r: float) -> list:
    """(tag, matrix_lever, extra_args) for DISARMING the break-even ratchet.

    Returns [] when the leg's target sits at or below the arming threshold,
    because there the cell is a provable no-op rather than a negative result.
    """
    if float(tp_at_r) <= _BE_ARM_AT_R:
        return []
    return [("be_off", "breakeven_ratchet", ["--no-sim-breakeven"])]


# M20 `breakeven_ratchet` x target CROSS — added 2026-09-12 (MI-278 U6), for
# `BL-20260912-THE-BREAK-EVEN-RATCHET-COSTS-2-7R-AT-THE-LIVE-SCALP-TARGET-AND-HAS-NEVER-BEEN-SWEPT`.
#
# ⚠️ THE ONE-AXIS `be_off` CELL ABOVE DOES NOT CLOSE THAT ROW, AND MI-278 U3
# RAN IT AND REPORTED A NEGATIVE ON ALL SEVEN LEGS. The row's resolution
# criteria asks for the ratchet CROSSED with the target, because the screen
# that opened it showed the two are ENTANGLED in the live config: part of what
# "a narrower target" buys is simply putting the target BELOW the ratchet's 1R
# arming threshold, so a one-axis sweep of EITHER lever attributes the other's
# effect to it. Screened 2x2 on SOLUSDT 5m 2025Q3 (n=60-72, no IS/OOS, no
# walk-forward — a SCREEN, and the row says so):
#
#                     BE ARMED (live)   BE DISARMED
#     tp_at_r 0.75       +7.911 R         +7.911 R      <- identical, by construction
#     tp_at_r 1.5        -0.695 R         +2.033 R
#
# THE ARMED ARM IS ALREADY MEASURED. `tp_cells` swept the whole `_TP_GRID` with
# the ratchet ON (memo section 4l), so the cross only needs the DISARMED row —
# these cells COMPLETE a 2xN grid rather than re-running half of it.
#
# ⚠️ THE GRID IS THE SCREEN'S RUNGS, NOT `_TP_GRID`, AND THAT IS A COST
# DECISION STATED RATHER THAN HIDDEN. This workflow's own header records that a
# full 8-arm grid on a ~124k-row file already sits at roughly the 150-minute
# job timeout, and the 5m corpora are ~373k rows — so crossing every `_TP_GRID`
# value would risk spending the budget and delivering nothing. These are the
# screen's rungs either side of the live target, which is where the decision
# lives. A wider cross is a later run, not a silently dropped one.
#
# ⚠️ `tp_at_r == 1.0` IS DELIBERATELY NOT CROSSED. The ratchet arms AT 1R, so a
# target AT 1R is a tie whose resolution depends on the order the harness
# evaluates the two within a bar. A number produced there would mean whatever
# that implementation detail happens to be, which is worse than not measuring
# it — so it is refused rather than reported.
_BE_CROSS_TP = (0.75, 2.0, 3.0)


def breakeven_cross_cells(tp_at_r: float) -> list:
    """(tag, matrix_lever, extra_args) for the ratchet DISARMED at other targets.

    One cell per `_BE_CROSS_TP` rung that is not the leg's own target — that
    rung is the plain `be_off` cell and crossing it would be a duplicate.

    ⚠️ THE RUNG AT OR BELOW THE ARMING THRESHOLD IS KEPT ON PURPOSE, AND IT IS
    THE POSITIVE CONTROL — the only cell in this sweep whose answer is known in
    advance. Below 1R the ratchet can never arm, so this cell MUST reproduce
    the ARMED `tp0.75R` cell exactly, and the screen confirmed that to three
    decimals. Every other cell here is a claim about a lever; this one is the
    check that the disarm plumbing does what its tag says. Dropping it as a
    "provable no-op" — which is the right call in `breakeven_cells`, where the
    comparison is against a base on which the ratchet is equally unreachable —
    would remove the only cell that can catch `--no-sim-breakeven` silently
    failing to strip the flag, and a silent failure there would make every
    other cell in the family read as a confident zero.
    """
    out = []
    for tp in _BE_CROSS_TP:
        if abs(tp - float(tp_at_r)) < 1e-9:
            continue                      # that rung IS the plain `be_off` cell
        suffix = "_ctl" if tp <= _BE_ARM_AT_R else ""
        out.append((f"be_off@tp{tp:g}R{suffix}", "breakeven_ratchet",
                    ["--no-sim-breakeven", "--tp-at-r", str(tp)]))
    return out


def tp_cells(tp_at_r: float) -> list:
    """(tag, matrix_lever, extra_args) for the take-profit distance grid."""
    out = []
    for tp in _TP_GRID:
        if abs(tp - float(tp_at_r)) < 1e-9:
            continue                      # the leg's own value is the baseline
        out.append((f"tp{tp:g}R", "bracket_geometry", ["--tp-at-r", str(tp)]))
    return out


def ladder_cells(tp_at_r: float) -> list:
    """(tag, matrix_lever, extra_args) for the partial-TP ladder grid."""
    out = []
    for frac in _BANK_FRACS:
        for rf in _RUNG_FRACS_OF_TP:
            rung = round(tp_at_r * rf, 3)
            if rung <= 0 or rung >= tp_at_r:      # never emit a no-op cell
                continue
            out.append((f"bank{frac:g}@{rung:g}R", "exit_ladder",
                        ["--bank-frac", str(frac), "--bank-at-r", str(rung)]))
    return out


def timeout_share(window: dict) -> float | None:
    """Fraction of a window's trades force-closed by the harness time exit.

    THIS IS A FIDELITY NUMBER, NOT A PERFORMANCE ONE. Production has NO
    time-based exit on any ict_scalp leg, so every `timeout` trade is one the
    live leg would still have been holding. Tracked by
    BL-20260912-THE-ICT-SCALP-HARNESS-FORCE-CLOSES-AT-24-BARS-AND-LIVE-HAS-NO-TIME-EXIT-AT-ALL
    — the id is kept on ONE line deliberately: a tracking id wrapped across two
    lines is graded as dangling by artifact-validity-guard, i.e. it reads as
    tracked while being tracked by nobody. This session made that exact mistake
    twice before catching it here. It is
    printed beside each cell's verdict because it is NOT constant across the
    grid: measured on SOLUSDT 5m it rises MONOTONICALLY with the target,
    21.4% at 0.75R to 58.5% at 4R, so a wide cell is graded on a population
    where most trades never resolved. A verdict that does not carry this
    cannot be told apart from one measured at parity.

    Returns None -- never 0.0 -- when the window has no trades or reports no
    outcome map: `we could not look` is not `nothing timed out`.
    """
    by = window.get("by_outcome")
    n = window.get("trades") or 0
    if not isinstance(by, dict) or not n:
        return None
    return by.get("timeout", 0) / n


# M20 `stop_geometry` cells — the ONLY stop-side knob this family has
# (MI-278 U3, 2026-09-12). The ict_scalp stop is STRUCTURAL — `sl =
# sweep_extreme ± atr_sl_buffer_mult * ATR` — so `atr_stop_mult`, the e35
# lever, does not exist here: all 8 live legs declare it None, which is also
# why e35 never touched this family. Until `--atr-sl-buffer-mult` the buffer
# was UNSWEEPABLE rather than unswept, the same shape as the target was.
#
# THE GRID BRACKETS THE LIVE 0.20 ON BOTH SIDES, and that is deliberate for the
# same reason the target grid does: MI-278's question is whether exits are
# mistimed, and a one-sided grid can only ever answer half of it. A TIGHTER
# buffer takes the stop closer to the swept extreme (more stop-outs, smaller R
# per trade, and R is the denominator here so it moves everything); a WIDER one
# clears more noise at the cost of a larger loss when it is hit.
#
# 0.0 IS DELIBERATELY ABSENT AND IS NOT AN OVERSIGHT: at zero the stop sits
# exactly ON the level the setup is defined by having swept, and the harness
# REFUSES it (`resolve_sl_buffer_override`). A cell that cannot run is worse
# than no cell — it reports as an error and reads as a failed sweep.
_SL_BUFFER_GRID = (0.05, 0.10, 0.15, 0.30, 0.40, 0.60)
_LIVE_SL_BUFFER = 0.20      # src/units/strategies/ict_scalp.py::_DEFAULTS


def sl_buffer_cells(sl_buffer_mult: float = _LIVE_SL_BUFFER) -> list:
    """(tag, matrix_lever, extra_args) for the stop-buffer grid.

    Excludes the leg's OWN value, which IS the baseline — measuring a cell
    against itself yields a guaranteed zero delta and reports as a tested
    negative rather than as the no-op it is.
    """
    out = []
    for m in _SL_BUFFER_GRID:
        if abs(m - float(sl_buffer_mult)) < 1e-9:
            continue
        out.append((f"slbuf{m:g}", "stop_geometry", ["--atr-sl-buffer-mult", str(m)]))
    return out


def all_cells(tp_at_r: float) -> list:
    """THE assembly of every cell this sweep offers — one owner, not two.

    Production and the tests both read this. It was inlined twice in `main`
    (once to build the run set, once to name the available levers in the
    no-cells-selected error), and a family added to one and not the other
    would be a lever the sweep can run but cannot NAME — or worse, one the
    error message advertises and the sweep never runs.
    """
    return (list(CELLS) + ladder_cells(tp_at_r) + tp_cells(tp_at_r)
            + breakeven_cells(tp_at_r) + breakeven_cross_cells(tp_at_r)
            + sl_buffer_cells())


def declared_lever_flags(leg_cfg: dict) -> list:
    """The leg's OWN shipped exit levers — part of its config-exact BASE.

    A new lever cell is measured ON TOP of what the leg already ships (the
    fleet sweep's `declared_levers` rule). Verified 2026-08-09 against
    config/strategies.yaml: of the eight ict_scalp legs only
    `ict_scalp_eth_15m` declares any (stale_exit_bars 12, stale_exit_below_r
    0.0), so omitting this would silently sweep that ONE leg against a
    baseline it does not actually run live.
    """
    flags = []
    for flag, key in (("--stale-exit-bars", "stale_exit_bars"),
                      ("--stale-exit-below-r", "stale_exit_below_r"),
                      ("--giveback-min-mfe-r", "giveback_min_mfe_r"),
                      ("--giveback-r", "giveback_r")):
        v = leg_cfg.get(key)
        if v is not None:
            flags.extend([flag, str(v)])
    return flags

# Config-exact base flags (M27 run_symbol_p0.py invocation, minus the
# regime-attribution flags which do not touch the exit path). SYMBOL/TIMEFRAME
# are filled per-leg by main(); every ict_scalp leg is a config-exact copy so the
# harness self-loads the shared ict_scalp_5m detection params for all of them.
def base_flags(symbol: str, timeframe: str, declared: list | None = None,
               timeout_bars: int | None = None) -> list:
    """The config-exact base every cell is measured against.

    ⚠️ `timeout_bars` IS A LIVE-PARITY AXIS, NOT A TUNING KNOB, and it defaults
    to None = pass nothing = the harness default of 24, which is byte-for-byte
    what every M27 scalp verdict already in the coverage matrix was measured at.

    WHY IT EXISTS (MI-278 U3, 2026-09-12): `backtest_ict_scalp.py` force-closes
    every trade at `timeout_bars`, and NO ict_scalp leg has ANY time-based exit
    in production — established from six directions with a positive control (the
    same probe finds `time_decay` in vwap / fvg_range_15m / fade_breakout_4h, so
    the scalp silence is a real absence). This script never passed the flag, so
    all 25 graded scalp cells inherit the 24. Measured on one leg-quarter, the
    timer ends 38.4% of trades at the live `tp_at_r` and supplies ~62% of the
    reported net R — **the error FLATTERS**, and its grip TIGHTENS as the target
    widens, so it biases the book against exactly the hold-longer levers M20
    tests. Filed as (id kept on ONE line — a tracking id wrapped across two
    reads as a DANGLING reference to artifact-validity-guard, which is how a
    row that IS filed can grade as tracked by nobody):
    `BL-20260912-THE-ICT-SCALP-HARNESS-FORCE-CLOSES-AT-24-BARS-AND-LIVE-HAS-NO-TIME-EXIT-AT-ALL`

    ⚠️ THE DEFAULT IS DELIBERATELY *NOT* CHANGED TO PARITY. Doing so would
    silently re-grade every existing scalp cell against a different book, which
    is the population-mixing this file's own dataset header warns about. A
    parity run is an EXPLICIT second run, and only IT may be cited by a
    proposal about how long a winner is held.
    """
    out = (["--symbol", symbol, "--timeframe", timeframe, "--sim-breakeven"]
           + list(declared or []))
    if timeout_bars is not None:
        out += ["--timeout-bars", str(int(timeout_bars))]
    return out


# module-level, set in main() so run_cell stays a pure (data, extra)->metrics call
BASE_FLAGS: list[str] = ["--symbol", "MGC", "--timeframe", "15m", "--sim-breakeven"]


def run_cell(data_csv: Path, extra: list[str], out_json: Path) -> dict:
    # Cache: a valid prior JSON is reused (each run is a pure function of its
    # args), so an interrupted sweep resumes instead of re-walking.
    if out_json.exists():
        try:
            return json.loads(out_json.read_text())
        except Exception:  # noqa: BLE001 — corrupt/partial, re-run
            pass
    # A cell may REMOVE a base flag as well as add one: `--no-<flag>` strips
    # `--<flag>` from the base rather than being passed through. Needed because
    # the break-even ratchet is part of the config-exact BASE, so the only way
    # to make it a variable is to take it out — and an unknown `--no-*` must
    # never reach the harness, which would exit non-zero and read as a failed
    # cell rather than a broken sweep.
    base = list(BASE_FLAGS)
    passthrough = []
    for a in extra:
        if a.startswith("--no-"):
            drop = "--" + a[len("--no-"):]
            if drop not in base:
                return {"error": f"cell asks to remove {drop!r} which is not in BASE_FLAGS"}
            base.remove(drop)
        else:
            passthrough.append(a)
    cmd = [sys.executable, str(_HARNESS), "--data", str(data_csv),
           *base, *passthrough, "--json", str(out_json)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return {"error": res.stderr.strip()[-500:] or "nonzero exit"}
    return json.loads(out_json.read_text())


def metrics(summary: dict) -> dict:
    return {
        "trades": summary.get("total_trades", 0),
        "total_r": summary.get("total_r", 0.0),
        "max_dd_r": summary.get("max_drawdown_r", 0.0),
        # PATH B INPUTS — RECORDED, NEVER GRADED HERE. The exit-refinement gate
        # has two qualifying paths and this sweep grades only Path A (net_R AND
        # maxDD). Path B's two thresholds — how much `net_r_per_capital_day`
        # must improve, and how much net_R may fall — are DELIBERATELY UNSET
        # repo-wide: `m20_fleet_exit_sweep.capital_delta` says the operator sets
        # them "from a measured distribution, not from a number a session
        # invented", and even that sweep's `path_b_wf_pass` verdict explicitly
        # "IS NOT A PROMOTION". So carrying the raw fields is the whole of what
        # is owed; a pass/fail computed here would be a session inventing an
        # operator-reserved threshold. Fee basis differs from Path A on purpose:
        # `total_r` is fee-free (the established M20 lever-gate basis) and these
        # are fee-NET, which is what `net_r_per_capital_day` is defined on.
        "net_total_r": summary.get("net_total_r"),
        "net_r_per_capital_day": summary.get("net_r_per_capital_day"),
        "net_r_per_position_day": summary.get("net_r_per_position_day"),
        "capital_days": summary.get("capital_days"),
        "mean_bars_held": summary.get("mean_bars_held"),
        "expectancy_r": summary.get("expectancy_r", 0.0),
        "win_rate_pct": summary.get("win_rate_pct", 0.0),
        "by_outcome": summary.get("by_outcome", {}),
        "error": summary.get("error"),
    }


def beats(cell: dict, base: dict) -> bool:
    """M20 IS/OOS pre-filter: beat baseline on net_R AND maxDD (lower dd better)."""
    if cell.get("error") or base.get("error"):
        return False
    return (cell["total_r"] > base["total_r"]
            and cell["max_dd_r"] < base["max_dd_r"])


def beats_or_ties(cell: dict, base: dict) -> bool:
    """Walk-forward per-fold rule: beat-or-tie baseline on net_R AND maxDD."""
    if cell.get("error") or base.get("error"):
        return False
    return (cell["total_r"] >= base["total_r"]
            and cell["max_dd_r"] <= base["max_dd_r"])


# Yearly walk-forward folds (mirror scripts/research/m20_fleet_exit_sweep.py).
FOLDS = [("2021", "2021-01-01", "2022-01-01"), ("2022", "2022-01-01", "2023-01-01"),
         ("2023", "2023-01-01", "2024-01-01"), ("2024", "2024-01-01", "2025-01-01"),
         ("2025", "2025-01-01", "2026-01-01"), ("2026", "2026-01-01", None)]
_WF_MIN_TRADES = 10  # a fold with fewer baseline trades is not usable


def walk_forward(df, ts, out: Path, cell_tags: dict) -> dict:
    """M20 confirmation gate for the IS/OOS candidate cells: run baseline vs each
    candidate cell on every yearly fold; a cell PASSES if it beats-or-ties the
    baseline on net_R AND maxDD in >= ceil(2/3) of the USABLE folds (usable =
    baseline has >= _WF_MIN_TRADES trades). Returns {tag: {folds, pass, usable,
    verdict}}."""
    import math
    wf: dict = {}
    # slice + run baseline once per fold
    fold_base = {}
    fold_csv = {}
    for name, start, end in FOLDS:
        mask = (ts >= pd.Timestamp(start, tz="UTC"))
        if end is not None:
            mask &= (ts < pd.Timestamp(end, tz="UTC"))
        csv = out / f"wf_{name}.csv"
        df[mask].to_csv(csv, index=False)
        fold_csv[name] = csv
        fold_base[name] = metrics(run_cell(csv, [], out / f"wf_base_{name}.json"))
    for tag, extra in cell_tags.items():
        rows = []
        pass_n = usable = 0
        for name, _s, _e in FOLDS:
            base_m = fold_base[name]
            if base_m.get("error") or base_m["trades"] < _WF_MIN_TRADES:
                rows.append({"fold": name, "usable": False})
                continue
            usable += 1
            cell_m = metrics(run_cell(fold_csv[name], extra, out / f"wf_{tag}_{name}.json"))
            ok = beats_or_ties(cell_m, base_m)
            pass_n += 1 if ok else 0
            rows.append({"fold": name, "usable": True, "pass": ok,
                         "d_netR": round(cell_m["total_r"] - base_m["total_r"], 2),
                         "d_maxDD": round(cell_m["max_dd_r"] - base_m["max_dd_r"], 2)})
        need = math.ceil(2 * usable / 3) if usable else 99
        verdict = ("PASS" if (usable >= 3 and pass_n >= need)
                   else "honest_negative")
        wf[tag] = {"pass_folds": pass_n, "usable_folds": usable,
                   "need": need, "verdict": verdict, "folds": rows}
    return wf


def _fidelity_suffix(cell: dict) -> str:
    """`  [timeout IS x% OOS y%]`, or an explicit unknown. Never silently absent."""
    parts = []
    for w in ("IS", "OOS"):
        sh = timeout_share(cell.get(w) or {})
        parts.append(f"{w} {sh:.0%}" if sh is not None else f"{w} ?")
    return f"  [timeout {' '.join(parts)}]"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(_REPO / "data" / "XAUUSD_15m_deep.csv"))
    ap.add_argument("--symbol", default="MGC",
                    help="Bot symbol for the leg (default MGC — the gold leg).")
    ap.add_argument("--timeframe", default="15m",
                    help="Leg timeframe label (default 15m).")
    ap.add_argument("--split", default="2025-07-01",
                    help="IS/OOS boundary (UTC date; IS < split <= OOS).")
    ap.add_argument("--leg", default=None,
                    help="Strategy leg name in config/strategies.yaml (e.g. "
                         "ict_scalp_eth_15m). Resolves symbol, timeframe, "
                         "tp_at_r and the leg's DECLARED exit levers, so the "
                         "base is config-exact. Overrides --symbol/--timeframe.")
    ap.add_argument("--cells", default=None,
                    help="CSV of matrix levers to restrict cells to (e.g. "
                         "exit_ladder) — skips already-verdicted columns.")
    ap.add_argument("--walkforward", action="store_true",
                    help="After IS/OOS, run the yearly walk-forward confirmation "
                         "(M20 gate) on any cell that passed the IS/OOS pre-filter.")
    ap.add_argument("--timeout-bars", type=int, default=None, metavar="N",
                    help="LIVE-PARITY AXIS, not a tuning knob. Default None = pass "
                         "nothing = the harness default of 24, which is what every "
                         "M27 scalp cell in the coverage matrix was measured at. "
                         "Production has NO time-based exit on any ict_scalp leg, so "
                         "a large value (e.g. 100000) is the PARITY arm — and only "
                         "the parity arm may be cited by a proposal about hold "
                         "length. Both arms are reported so the gap is quantified "
                         "rather than swapped in silently (MI-278 U3).")
    ap.add_argument("--out", required=True, help="Output dir for slices + JSON.")
    args = ap.parse_args(argv[1:])

    symbol, timeframe, declared, tp_at_r, leg_name = (
        args.symbol, args.timeframe, [], 1.5, None)
    if args.leg:
        import yaml
        legs = (yaml.safe_load((_REPO / "config" / "strategies.yaml").read_text())
                or {}).get("strategies") or {}
        cfg = legs.get(args.leg)
        if not isinstance(cfg, dict):
            print(f"ERROR: leg {args.leg!r} not in config/strategies.yaml",
                  file=sys.stderr)
            return 2
        leg_name = args.leg
        symbol = (cfg.get("symbols") or [args.symbol])[0]
        timeframe = str(cfg.get("timeframe") or args.timeframe)
        declared = declared_lever_flags(cfg)
        # tp_at_r drives the ladder rungs; the harness default is the fallback.
        tp_at_r = float(cfg.get("tp_at_r") or 1.5)

    global BASE_FLAGS
    BASE_FLAGS = base_flags(symbol, timeframe, declared, args.timeout_bars)

    # Cells = the stale/giveback grid + the config-relative ladder grid,
    # optionally filtered to one matrix lever.
    cells = all_cells(tp_at_r)
    if args.cells:
        want = {c.strip() for c in args.cells.split(",") if c.strip()}
        cells = [c for c in cells if c[1] in want]
    if not cells:
        print(f"ERROR: no cells selected (--cells {args.cells!r}); "
              f"available levers: "
              f"{sorted({c[1] for c in all_cells(tp_at_r)})}",
              file=sys.stderr)
        return 2
    _to = args.timeout_bars
    print(f"leg={leg_name or '(explicit)'} symbol={symbol} tf={timeframe} "
          f"tp_at_r={tp_at_r} declared_base={declared or 'none'} "
          f"timeout_bars={_to if _to is not None else '24 (harness default — NOT live parity; production has no time exit)'}", flush=True)
    print(f"cells ({len(cells)}): {[c[0] for c in cells]}", flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.data)
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    split = pd.Timestamp(args.split, tz="UTC")
    is_csv = out / "slice_is.csv"
    oos_csv = out / "slice_oos.csv"
    df[ts < split].to_csv(is_csv, index=False)
    df[ts >= split].to_csv(oos_csv, index=False)
    windows = {"IS": is_csv, "OOS": oos_csv}
    print(f"data: {args.data} ({len(df)} bars) split {args.split} -> "
          f"IS {int((ts < split).sum())} / OOS {int((ts >= split).sum())} bars",
          flush=True)

    # Build every (tag, window) job, then run them concurrently — each harness
    # subprocess is single-threaded, so a pool of 4 keeps the box busy. Every
    # run is byte-identical to the serial version (same args); only wall-clock
    # changes. A finished JSON is reused (cache), so an interrupted run resumes.
    jobs = []  # (result_key, window, extra, out_json)
    for w, csv in windows.items():
        jobs.append((("base", w), csv, [], out / f"base_{w}.json"))
    for tag, lever, extra in cells:
        for w, csv in windows.items():
            jobs.append(((tag, w), csv, extra, out / f"{tag}_{w}.json"))

    computed: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run_cell, csv, extra, oj): key
                for key, csv, extra, oj in jobs}
        for fut, key in list(futs.items()):
            computed[key] = metrics(fut.result())

    base = {"IS": computed[("base", "IS")], "OOS": computed[("base", "OOS")]}
    for w in ("IS", "OOS"):
        print(f"  BASE {w:3s}: trades={base[w]['trades']} "
              f"total_R={base[w]['total_r']} maxDD={base[w]['max_dd_r']} "
              f"exp_R={base[w]['expectancy_r']}", flush=True)

    results = {"data": args.data, "split": args.split, "leg": leg_name,
               "symbol": symbol, "timeframe": timeframe, "tp_at_r": tp_at_r,
               "declared_base": declared, "baseline": base, "cells": {}}
    for tag, lever, extra in cells:
        cell = {"IS": computed[(tag, "IS")], "OOS": computed[(tag, "OOS")]}
        is_beat = beats(cell["IS"], base["IS"])
        oos_beat = beats(cell["OOS"], base["OOS"])
        verdict = "CANDIDATE" if (is_beat and oos_beat) else "honest_negative"
        results["cells"][tag] = {"lever": lever, "extra": extra,
                                 "IS": cell["IS"], "OOS": cell["OOS"],
                                 "is_beat": is_beat, "oos_beat": oos_beat,
                                 "verdict": verdict,
                                 # Path B evidence, REPORTED beside the Path A
                                 # verdict and never folded into it. `verdict`
                                 # stays a Path A word; a reader wanting Path B
                                 # reads these and applies the operator's
                                 # thresholds, which this file does not hold.
                                 "capital_delta": {
                                     "IS": _capital_delta(cell["IS"], base["IS"]),
                                     "OOS": _capital_delta(cell["OOS"], base["OOS"]),
                                 },
                                 "gate_paths_graded": ["A"]}
        print(f"  {tag:18s} [{lever}]  "
              f"IS ΔR={cell['IS']['total_r'] - base['IS']['total_r']:+.2f} "
              f"ΔDD={cell['IS']['max_dd_r'] - base['IS']['max_dd_r']:+.2f} "
              f"(n{cell['IS']['trades']}) | "
              f"OOS ΔR={cell['OOS']['total_r'] - base['OOS']['total_r']:+.2f} "
              f"ΔDD={cell['OOS']['max_dd_r'] - base['OOS']['max_dd_r']:+.2f} "
              f"(n{cell['OOS']['trades']})  -> {verdict}"
              + _fidelity_suffix(cell), flush=True)

    cands = [t for t, c in results["cells"].items() if c["verdict"] == "CANDIDATE"]
    print(f"\nCANDIDATES (pass IS+OOS pre-filter): "
          f"{cands or 'NONE — all honest_negative'}", flush=True)

    # Walk-forward confirmation (M20 gate) for the IS/OOS candidates.
    if args.walkforward and cands:
        print("\n=== yearly walk-forward (M20 gate: beat-or-tie net_R AND maxDD "
              ">= ceil(2/3) usable folds) ===", flush=True)
        cell_tags = {t: dict(results["cells"][t])["extra"] for t in cands}
        wf = walk_forward(df, ts, out, cell_tags)
        results["walkforward"] = wf
        for tag, r in wf.items():
            results["cells"][tag]["walkforward_verdict"] = r["verdict"]
            fold_str = " ".join(
                f"{f['fold']}:{'PASS' if f.get('pass') else ('-' if f.get('usable') else 'skip')}"
                for f in r["folds"])
            print(f"  {tag:18s} {r['pass_folds']}/{r['usable_folds']} usable folds "
                  f"(need {r['need']}) -> {r['verdict']}   [{fold_str}]", flush=True)
        survivors = [t for t, r in wf.items() if r["verdict"] == "PASS"]
        print(f"\nWALK-FORWARD SURVIVORS (M20-gated, -> Tier-3 proposal): "
              f"{survivors or 'NONE — candidates fail walk-forward, honest_negative'}",
              flush=True)

    (out / "verdicts.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"verdicts.json -> {out / 'verdicts.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
