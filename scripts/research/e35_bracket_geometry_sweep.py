#!/usr/bin/env python3
"""E3.5 — sweep the BRACKET GEOMETRY itself: (tp_r, atr_stop_mult, timeout_bars).

WHY THIS EXISTS
---------------
`docs/design/exit-mechanism-construction-PROCESS.md` § 0.1 measured that **a level
fixed at entry decides 78.5% of exits** on `xrp_pullback_2h`, and § E3's 2026-08-20
run filed `BL-20260820-TP-LEVEL-IS-THE-ONE-EXIT-PARAMETER-NEVER-SWEPT`: on a fleet
whose own census says the bracket decides most exits, **the take-profit level has
never been a swept dimension in any exit sweep.** Every M20 cell to date moves a
*lever* (stale / giveback / trail / rr_floor) against a bracket held at its config
value. This sweeps the bracket.

Per § E3.5 (the operator directive) this is the **step-1 diagnostic, not the
destination**: it establishes the static optimum and an honest baseline, against
which a state-CONDITIONED bracket must later be judged. Its own most likely useful
outcome is a flat surface — *"if net R barely moves across the grid, the exit
dimension has little to give and active management inherits that"* — which is a
result, not a null run.

A NEW COVERAGE-MATRIX DIMENSION, NOT A NEW GATE
-----------------------------------------------
Every comparison here reuses `m20_fleet_exit_sweep`'s own definitions by IMPORT —
`base_args` (config-exact base), `resolve_data`, `classify`, `resolve_split`,
`run_cell`, `beats` (Path A), `walkforward`, `is_path_b_candidate`,
`drawdown_exchange_rate`, `FOLDS`. Nothing is re-derived. A verdict here is the
same verdict the fleet sweep would produce for a cell with these args, and if that
module's gate moves, this moves with it.

⚠️ **NET OF FEES IS LOAD-BEARING HERE IN A WAY IT WAS NOT FOR THE LEVER SWEEPS.**
A lever replay takes exactly one exit per trade, so the per-exit fee cancels between
arms. A *lower take-profit does not*: it changes the trade population and raises
turnover, and the E3 screen measured a round trip at **0.082-0.163 R** against a
fee-free mean edge of **+0.1376 R** (XRP) / **+0.1167 R** (SOL). A fee-free R basis
would flatter a lower target exactly where it is most likely to pass. Every harness
here charges `src/runtime/execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP` (7.5) through
the harness's own `--fee-bps-roundtrip`, and `net_total_r` is net of it.

⚠️⚠️ **THE STOP AXIS CHANGES THE UNIT R IS MEASURED IN — AND THE FIX IS NOT A
SECOND BASIS.** `risk = atr_stop_mult * ATR` (identical formula in both harnesses
and the live units — see `m20_fleet_exit_sweep.arm_atr_close_ceiling`), so moving
the stop moves R itself.

An earlier version of this module computed a fixed-notional return beside `net_R`
and REFUSED any stop-axis cell where the two disagreed in sign. **That rule was
backwards and is gone.** `RiskManager.position_size` sizes by RISK, not by notional
— `qty = risk_budget / risk` — so `net_R` *is* the unit the account experiences, and
fixed notional is not an alternative truth about it. Measured on `ada_pullback_2h`
(228 base trades, full history, net of fees), the two bases disagree on exactly the
cells in the *desirable* direction:

    atr_stop_mult   net_R    mean risk/entry   notional per unit risk-budget
        1.5        -8.63         0.0375              26.67
        2.0        -9.17         0.0503              19.89
        2.5 (base) +14.98        0.0630              15.89
        3.0        +17.23        0.0760              13.16
        3.5        +14.70        0.0884              11.31

`sm3` beats base in R **and** needs less notional per unit of risk budget. The old
rule refused it.

What the notional figure is genuinely for is **LEVERAGE**, and that is what it
reports now. `notional per unit risk-budget = 1 / (risk/entry)`, so a TIGHTER stop
buys its R with MORE leverage, and the margin pre-flight cap really does bind on
this system (`bybit_2`'s 110007 refusals). Every stop-axis cell therefore carries
`leverage_multiple` — its notional-per-risk-budget relative to base — and a cell
with `leverage_multiple > 1` is flagged `leverage_contingent`: its `net_R` gain is
conditional on the account being able to size it. **Reported, never gated** — a
threshold with no measured distribution behind it is the mistake
`docs/design/gross-exposure-governance-DESIGN.md` § 6-7 refuses. Never quote a
stop-axis `net_R` without `leverage_multiple` beside it.

Observe-only, Tier-1: reads config + candles, writes a report, touches nothing live.
Changing `tp_at_r` / `atr_stop_mult` / `timeout_bars` in `config/strategies.yaml` is
**Tier-3** and is NOT what this produces — it produces the evidence for that decision.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import yaml  # noqa: E402

import m20_fleet_exit_sweep as fleet  # noqa: E402

# ---------------------------------------------------------------------------
# THE GRID.
#
# Pre-registered here and NOT widened after seeing a result — the same discipline
# `e3_joint_lever_sweep.BANK_AT` states. Each axis brackets the live value:
#
#   tp_r          live is 50.0 on every donchian/pullback leg (the "no R target"
#                 sentinel) and 6.0 on the two `_prop` legs, with the real ceiling
#                 set by the 9.9% venue clamp (`--tp-cap-pct`). So the grid walks
#                 DOWN from the sentinel into the region a real R target occupies.
#                 1.5 is included because it is what every live `ict_scalp` leg
#                 actually trades — the value the E2 label should have matched,
#                 per the tracking id on the next line (never abbreviated with an
#                 ellipsis: a truncated id resolves to NOTHING and reads as
#                 tracked while being tracked by nobody):
# BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY
#   atr_stop_mult live is 2.5 on every leg in scope. Symmetric either side.
#   timeout_bars  live is the harness default (200 trend/pullback, 48 squeeze);
#                 the grid reaches well below it because the census records
#                 timeout as a near-empty exit bucket (5 of 284 on the E0 leg),
#                 i.e. the current value is far outside the binding region.
# ---------------------------------------------------------------------------
TP_R_GRID = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0)
STOP_MULT_GRID = (1.5, 2.0, 3.0, 3.5)
TIMEOUT_GRID = (24, 48, 96, 400)

# Harness `--timeout-bars` defaults, MEASURED by reading each parser (2026-08-20),
# not assumed to be shared: backtest_trend.py:982 and backtest_pullback.py:961 are
# 200, backtest_squeeze.py:522 is 48. The base value matters because a grid point
# equal to it is a provable no-op and is reported as such rather than as a measured
# zero delta.
HARNESS_TIMEOUT_DEFAULT = {
    "scripts/backtest_trend.py": 200,
    "scripts/backtest_pullback.py": 200,
    "scripts/backtest_squeeze.py": 48,
}

AXES = ("tp", "stop", "timeout")


def _flag(args: list[str], flag: str) -> str | None:
    """Last value of `flag` in `args`, or None. String — callers coerce."""
    val = None
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            val = args[i + 1]
    return val


def base_geometry(harness: str, base: list[str]) -> dict:
    """The (tp_r, stop_mult, timeout, tp_cap) the config-exact base ACTUALLY ran.

    Read off the emitted args, with the harness's own parser default as the
    documented fallback — never a guessed constant. `source` records which, per
    axis, so a grid point that coincides with the base is distinguishable from
    one whose base we failed to read.
    """
    out: dict[str, Any] = {}
    for key, flag, dflt in (
        ("tp_r", "--tp-r", 50.0),
        ("stop_mult", "--atr-stop-mult", 2.5),
        ("tp_cap_pct", "--tp-cap-pct", 0.0),
    ):
        raw = _flag(base, flag)
        out[key] = float(raw) if raw is not None else dflt
        out[f"{key}_source"] = "base_args" if raw is not None else "harness_default"
    raw_to = _flag(base, "--timeout-bars")
    if raw_to is not None:
        out["timeout"] = int(float(raw_to))
        out["timeout_source"] = "base_args"
    else:
        dflt_to = HARNESS_TIMEOUT_DEFAULT.get(harness)
        out["timeout"] = dflt_to
        out["timeout_source"] = (
            "harness_default" if dflt_to is not None else "unknown")
    return out


def cell_args(base: list[str], tp_r: float | None, stop_mult: float | None,
              timeout: int | None) -> list[str]:
    """Base args with the named bracket flags REPLACED (never appended twice).

    Strip-then-append, for the reason `base_args`'s `min_confidence_override`
    gives: argparse would take the last of two flags and get the right number,
    but the recorded command IS the evidence for what a row measured, and a
    command carrying two contradictory values cannot be read back as a claim
    about either.
    """
    drop = set()
    if tp_r is not None:
        drop.add("--tp-r")
    if stop_mult is not None:
        drop.add("--atr-stop-mult")
    if timeout is not None:
        drop.add("--timeout-bars")
    out: list[str] = []
    i = 0
    while i < len(base):
        if base[i] in drop:
            i += 2
            continue
        out.append(base[i])
        i += 1
    if tp_r is not None:
        out += ["--tp-r", f"{tp_r:g}"]
    if stop_mult is not None:
        out += ["--atr-stop-mult", f"{stop_mult:g}"]
    if timeout is not None:
        out += ["--timeout-bars", str(int(timeout))]
    return out


def cell_tag(tp_r: float | None, stop_mult: float | None,
             timeout: int | None) -> str:
    parts = []
    if tp_r is not None:
        parts.append(f"tp{tp_r:g}")
    if stop_mult is not None:
        parts.append(f"sm{stop_mult:g}")
    if timeout is not None:
        parts.append(f"to{int(timeout)}")
    return "_".join(parts) if parts else "base"


def notional_return(harness: str, args: list[str], start=None,
                    end=None) -> dict:
    """Sum of `net_r * risk / entry` over the run's emitted trades.

    The fixed-notional companion to `net_total_r`, and the ONLY basis in which a
    `stop`-axis comparison is unit-safe (see the module docstring). `risk` is
    recomputed as `|entry - sl|` from the emitted row rather than read from a
    field, because that is the definition both harnesses size R by.

    Three states, never collapsed — a fabricated 0.0 would read as "the book made
    nothing" when we in fact never measured it:
      * `measured`      — rows were emitted and parsed; `total` is a float.
      * `no_trades`     — the run produced zero trades; `total` is 0.0, honestly.
      * `unmeasured`    — the harness errored, emitted nothing, or every row was
                          unparseable; `total` is None.
    """
    fd, path = tempfile.mkstemp(prefix="e35_emit_", suffix=".jsonl")
    os.close(fd)
    fd2, jpath = tempfile.mkstemp(prefix="e35_json_", suffix=".json")
    os.close(fd2)
    # ONE subprocess produces BOTH the summary and the emitted trades. Calling
    # `run_cell` and then a second emit-only run for the same args doubled the
    # cost of every stop-axis cell for a number the first run already had.
    cmd = [sys.executable, str(REPO / harness), *args,
           "--emit-trades", path, "--json", jpath]
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=fleet.CELL_TIMEOUT_S)
        if p.returncode != 0:
            return {"state": "unmeasured", "total": None, "n": 0,
                    "why": (p.stderr or p.stdout)[-200:]}
        try:
            summary = json.loads(Path(jpath).read_text())
        except (OSError, json.JSONDecodeError):
            summary = None
        total, n, bad = 0.0, 0, 0
        risk_fracs: list[float] = []
        try:
            text = Path(path).read_text()
        except OSError as exc:
            return {"state": "unmeasured", "total": None, "n": 0,
                    "why": f"emit unreadable: {exc}"}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                entry, sl = float(r["entry"]), float(r["sl"])
                net_r = float(r["net_r"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                bad += 1
                continue
            risk = abs(entry - sl)
            if entry <= 0 or risk <= 0 or not math.isfinite(net_r):
                bad += 1
                continue
            total += net_r * risk / entry
            risk_fracs.append(risk / entry)
            n += 1
        if n == 0:
            # Zero PARSED rows is ambiguous on its own: a run with no trades and
            # a run whose every row was malformed both emit nothing usable. `bad`
            # separates them.
            if bad == 0:
                return {"state": "no_trades", "total": 0.0, "n": 0, "bad": 0}
            return {"state": "unmeasured", "total": None, "n": 0, "bad": bad,
                    "why": f"{bad} rows unparseable, 0 usable"}
        mean_rf = sum(risk_fracs) / len(risk_fracs)
        return {"state": "measured", "total": round(total, 6), "n": n,
                "bad": bad, "summary": summary,
                # mean(risk/entry): the per-trade risk as a fraction of entry
                # notional. Its RECIPROCAL is notional per unit of risk budget,
                # i.e. the leverage this arm requires.
                "mean_risk_per_entry": round(mean_rf, 6),
                "notional_per_risk_budget": round(1.0 / mean_rf, 3)}
    except subprocess.TimeoutExpired:
        return {"state": "unmeasured", "total": None, "n": 0,
                "why": f"timeout after {fleet.CELL_TIMEOUT_S:.0f}s"}
    finally:
        for _p in (path, jpath):
            try:
                os.unlink(_p)
            except OSError:
                pass


def leverage_check(cell_not: dict, base_not: dict) -> dict:
    """How much MORE notional per unit of risk budget does this cell need?

    `qty = risk_budget / risk`, so `notional = risk_budget / (risk/entry)` and the
    leverage an arm requires is `1 / mean(risk/entry)`. Relative to base:

        leverage_multiple = mean(risk/entry)_base / mean(risk/entry)_cell

    `> 1` means the cell buys its `net_R` with MORE leverage, so the gain is
    contingent on the account being able to size it (the margin pre-flight cap
    binds on this system — `bybit_2`'s 110007 refusals). `<= 1` means the gain is
    unconditional in that respect.

    **REPORTED, NEVER GATED.** There is no measured distribution of tolerable
    leverage on this fleet, and inventing a ceiling here is the exact mistake
    `gross-exposure-governance-DESIGN.md` § 6-7 refuses.

    States, never collapsed — a fabricated 1.0 would read as "no leverage change"
    when we in fact never measured one:
      * `measured`       — both arms parsed; `leverage_multiple` is a float.
      * `unmeasured`     — either side unmeasured: **we did not look.**
      * `not_applicable` — the cell does not move the stop, so R is unchanged.
    """
    if cell_not.get("state") == "not_applicable":
        return {"state": "not_applicable"}
    cr = cell_not.get("mean_risk_per_entry")
    br = base_not.get("mean_risk_per_entry")
    if not cr or not br or cr <= 0 or br <= 0:
        return {"state": "unmeasured"}
    mult = br / cr
    return {"state": "measured",
            "leverage_multiple": round(mult, 4),
            "cell_notional_per_risk_budget": cell_not.get(
                "notional_per_risk_budget"),
            "base_notional_per_risk_budget": base_not.get(
                "notional_per_risk_budget"),
            "leverage_contingent": bool(mult > 1.0)}


def axis_of(tp_r, stop_mult, timeout, base_geo: dict) -> str:
    """Which axes this cell actually moves off the base. '+'-joined, 'none' if it
    moves nothing (a grid point equal to the base — a provable no-op)."""
    moved = []
    if tp_r is not None and tp_r != base_geo.get("tp_r"):
        moved.append("tp")
    if stop_mult is not None and stop_mult != base_geo.get("stop_mult"):
        moved.append("stop")
    if timeout is not None and base_geo.get("timeout") is not None \
            and int(timeout) != int(base_geo["timeout"]):
        moved.append("timeout")
    return "+".join(moved) if moved else "none"


def plan_legs(data_dir: Path, only: list[str] | None,
              tp_cap_pct: float, *,
              ignore_missing_data: bool = False) -> tuple[list[dict], list[dict]]:
    """(runnable, skipped) — resolved through the fleet sweep's OWN resolvers.

    Symbol/timeframe/data/base are read EXACTLY as `m20_fleet_exit_sweep.main`
    reads them (`cfg["symbols"][0]`, `classify`, `resolve_data`, `base_args`
    with `tp_cap_pct` positional). Re-deriving any of them here is how this tool
    and the fleet sweep would come to disagree about what a leg's base is.

    ``ignore_missing_data`` (default **False** — the sweep's own behaviour is
    byte-for-byte unchanged) drops **only** the data-presence gate, for callers
    that PLAN work whose first step is to fetch the data. `e35_shard_plan.py` is
    that caller: leg CSVs are gitignored, so on a fresh CI checkout every leg
    resolves `data=None` and the matrix expands to zero jobs
    (`BL-20260824-E35-SHARD-PLANNER-CANNOT-PLAN-ON-A-FRESH-CHECKOUT`; measured
    `0 job(s); 55 not scheduled (data_missing=43, out_of_scope_family=12)`,
    exit 1 — and `e35-bracket-sweep.yml` had therefore never run).

    ⚠️ **THE FLAG LIVES HERE, NOT IN A PARALLEL PLANNER.** The obvious fix was a
    config-only loop inside the shard planner, but then "which legs are in
    scope" would exist TWICE and the two copies are free to drift — a shard
    plan that schedules a leg the sweep would refuse, or misses one it accepts,
    with nothing to catch it. One loop, one scope; the flag removes exactly one
    `if`.

    ⚠️ **A data-pending entry carries `base: None`, and that is deliberate.**
    `base_args` needs the resolved data path, so a base built without data would
    be a fiction. Such an entry is stamped `data_pending: True` and is safe ONLY
    for callers that read scope fields (leg/family/symbol/tf); anything reading
    `base`/`base_geometry` must not be handed one. The sweep never sets the flag,
    so its own rows always carry a real base.
    """
    cfg_all = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    strats = cfg_all.get("strategies", cfg_all)
    runnable: list[dict] = []
    skipped: list[dict] = []
    for name, c in sorted(strats.items()):
        if not isinstance(c, dict) or (only and name not in only):
            continue
        fam = fleet.classify(name)
        if fam not in ("donchian", "pullback", "squeeze"):
            # `scalp`/`fvg` carry a REAL `tp_at_r`/`tp_r` bracket rather than the
            # 50.0 sentinel, so this grid would mean something different on them.
            # Out of SCOPE, recorded as such — not silently absent.
            skipped.append({"leg": name,
                            "reason": f"out_of_scope_family:{fam}"})
            continue
        sym = (c.get("symbols") or [None])[0]
        tf = str(c.get("timeframe") or "1h")
        data, proxy, resample = fleet.resolve_data(str(sym), tf, data_dir)
        if data is None and not ignore_missing_data:
            skipped.append({"leg": name, "reason": f"data_missing:{sym}"})
            continue
        harness = fleet.FAMILY_HARNESS[fam]
        # A base without data would be a fiction, so it is None and SAYS so
        # rather than being half-built from defaults.
        base = (None if data is None
                else fleet.base_args(name, c, fam, data, resample, tp_cap_pct))
        runnable.append({
            "leg": name, "family": fam, "symbol": sym, "tf": tf,
            "harness": harness, "data": data, "proxy": proxy,
            "resample": resample, "base": base,
            "execution": c.get("execution", "live"),
            "declared_levers_present": fleet.declared_levers_present(c),
            "base_geometry": (None if base is None
                              else base_geometry(harness, base)),
            "data_pending": data is None,
        })
    return runnable, skipped


# ---------------------------------------------------------------------------
# STAGE 1 — the response surface.
#
# One full-history run per grid cell. This is the step-1 diagnostic the operator
# directive asks for, and its most likely answer is "flat". Reporting the SPREAD
# of net_total_r across the whole grid is therefore the headline, not the argmax:
# an argmax over a flat surface is noise wearing a recommendation's clothes.
# ---------------------------------------------------------------------------
def surface(leg: dict, *, singles_only: bool, log) -> dict:
    harness, base = leg["harness"], leg["base"]
    geo = leg["base_geometry"]
    base_row = fleet.run_cell(harness, base)
    if "error" in base_row:
        return {"leg": leg["leg"], "error": base_row["error"]}
    b_net = _f(base_row, "net_total_r")
    b_dd = _f(base_row, "max_drawdown_r")
    b_n = base_row.get("total_trades")

    combos: list[tuple] = []
    if singles_only:
        for v in TP_R_GRID:
            combos.append((v, None, None))
        for v in STOP_MULT_GRID:
            combos.append((None, v, None))
        for v in TIMEOUT_GRID:
            combos.append((None, None, v))
    else:
        tp_vals = (None,) + TP_R_GRID
        sm_vals = (None,) + STOP_MULT_GRID
        to_vals = (None,) + TIMEOUT_GRID
        combos = [c for c in itertools.product(tp_vals, sm_vals, to_vals)
                  if any(x is not None for x in c)]

    # The fixed-notional base is needed once, and ONLY if a stop-axis cell will
    # be graded against it. Computing it unconditionally would spend a run per
    # leg on a number nothing reads.
    base_not: dict | None = None

    rows = []
    for tp_r, sm, to in combos:
        ax = axis_of(tp_r, sm, to, geo)
        tag = cell_tag(tp_r, sm, to)
        if ax == "none":
            # A grid point identical to the base is a PROVABLE no-op, and a
            # measured 0.0 delta here would be indistinguishable from a lever
            # that ran and did nothing. Recorded, never run.
            rows.append({"cell": tag, "axis": "none", "state": "inert_equals_base",
                         "tp_r": tp_r, "stop_mult": sm, "timeout": to})
            continue
        args = cell_args(base, tp_r, sm, to)
        c_not = None
        if "stop" in ax:
            # One run, both readings. Falls back to `run_cell` only if the
            # combined run could not produce a summary, so a leverage-measurement
            # failure never costs the cell itself.
            if base_not is None:
                base_not = notional_return(harness, base)
            c_not = notional_return(harness, args)
            r = c_not.get("summary") or fleet.run_cell(harness, args)
        else:
            r = fleet.run_cell(harness, args)
        if "error" in r:
            rows.append({"cell": tag, "axis": ax, "state": "error",
                         "why": r["error"]})
            continue
        c_net, c_dd = _f(r, "net_total_r"), _f(r, "max_drawdown_r")
        row = {
            "cell": tag, "axis": ax, "state": "measured",
            "tp_r": tp_r, "stop_mult": sm, "timeout": to,
            "trades": r.get("total_trades"),
            "net_total_r": c_net, "max_drawdown_r": c_dd,
            "net_expectancy_r": _f(r, "net_expectancy_r"),
            "win_rate_pct": _f(r, "win_rate_pct"),
            "by_outcome": r.get("by_outcome"),
            "d_net_r": None if (c_net is None or b_net is None)
                       else round(c_net - b_net, 4),
            "d_max_dd": None if (c_dd is None or b_dd is None)
                        else round(c_dd - b_dd, 4),
        }
        # UNIT SAFETY — stop-axis cells only (see module docstring).
        if "stop" in ax:
            row["notional"] = c_not
            row["base_notional"] = base_not
            row["leverage"] = leverage_check(c_not, base_not)
        else:
            row["leverage"] = {"state": "not_applicable"}
        rows.append(row)
        log(row | {"leg": leg["leg"], "window": "full"})
    measured = [r for r in rows if r["state"] == "measured"
                and r["net_total_r"] is not None]
    nets = [r["net_total_r"] for r in measured]
    return {
        "leg": leg["leg"], "family": leg["family"], "symbol": leg["symbol"],
        "tf": leg["tf"], "execution": leg["execution"],
        "base_geometry": geo,
        "base": {"net_total_r": b_net, "max_drawdown_r": b_dd,
                 "trades": b_n, "by_outcome": base_row.get("by_outcome"),
                 "tp_r_effective_median": base_row.get("tp_r_effective_median"),
                 "max_mfe_r": base_row.get("max_mfe_r")},
        "cells": rows,
        # THE HEADLINE. `spread` is how much the whole bracket dimension is worth
        # on this leg, full history, net of fees — read it BEFORE any argmax.
        "surface": {
            "n_measured": len(measured),
            "n_cells": len(rows),
            "net_r_min": round(min(nets), 4) if nets else None,
            "net_r_max": round(max(nets), 4) if nets else None,
            "net_r_spread": round(max(nets) - min(nets), 4) if nets else None,
            "base_net_r": b_net,
            "best_cell": (max(measured, key=lambda r: r["net_total_r"])["cell"]
                          if measured else None),
            "best_d_net_r": (round(max(nets) - b_net, 4)
                             if nets and b_net is not None else None),
        },
    }


def _f(d: dict, k: str) -> float | None:
    try:
        return float(d[k])
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# STAGE 2 — the M20 gate, unchanged, applied to the surface's candidates.
#
# `resolve_split` + `beats` (Path A) + `is_path_b_candidate` + `walkforward` are
# imported, not restated. The ONLY thing this stage adds is the unit-safety
# refusal for stop-axis cells.
# ---------------------------------------------------------------------------
def gate(leg: dict, cells: list[dict], split_mode: str, split: str,
         target_oos: int, log) -> list[dict]:
    harness, base = leg["harness"], leg["base"]
    # `resolve_split` returns (boundary_date, meta) — unpacked, not `.get`ed.
    # The meta rides into every record so a verdict states its own derivation
    # (target vs achieved, and any clamp), which is the whole reason that
    # function returns it.
    boundary, split_meta = fleet.resolve_split(
        harness, base, split_mode, split, target_oos)
    out = []
    for c in cells:
        tag = c["cell"]
        args = cell_args(base, c.get("tp_r"), c.get("stop_mult"),
                         c.get("timeout"))
        b_is = fleet.run_cell(harness, base, end=boundary)
        c_is = fleet.run_cell(harness, args, end=boundary)
        b_oos = fleet.run_cell(harness, base, start=boundary)
        c_oos = fleet.run_cell(harness, args, start=boundary)
        for w, bb, cc in (("is", b_is, c_is), ("oos", b_oos, c_oos)):
            log({"leg": leg["leg"], "cell": tag, "window": w,
                 "base": bb, "lever": cc})
        rec: dict[str, Any] = {
            "leg": leg["leg"], "cell": tag, "axis": c["axis"],
            "split": boundary, "split_mode": split_mode,
            "split_meta": split_meta,
            "base_is_trades": b_is.get("total_trades"),
            "base_oos_trades": b_oos.get("total_trades"),
            "is": fleet.beats_detail(c_is, b_is),
            "oos": fleet.beats_detail(c_oos, b_oos),
        }
        if any("error" in x for x in (b_is, c_is, b_oos, c_oos)):
            rec["verdict"] = "error"
            out.append(rec)
            continue
        # LEVERAGE RIDES WITH EVERY STOP-AXIS VERDICT. Not a gate — the fleet
        # has no measured distribution of tolerable leverage, so a threshold
        # here would be invented. But a stop-axis net_R quoted without it is
        # unreadable, so it is attached to the record before the verdict.
        if "stop" in c["axis"]:
            rec["leverage"] = c.get("leverage") or {"state": "unmeasured"}
        path_a = fleet.beats(c_is, b_is) and fleet.beats(c_oos, b_oos)
        # PATH B WAS UNREACHABLE BY CONSTRUCTION UNTIL 2026-08-23
        # (BL-20260823-E35-PATH-B-UNREACHABLE-RAW-RUNCELL-DICT). This passed the
        # RAW `c_oos` run_cell dict where `is_path_b_candidate` reads
        # `d_net_r_per_capital_day` -- a key `run_cell` never emits (proved over
        # 308 run_cell dicts across 11 legs: `net_r_per_capital_day` present,
        # the DELTA absent). `.get` returned None, `_up(None)` is False, so the
        # predicate answered False for every cell ever gated, whatever its
        # numbers. The fleet sweep passes `capital_delta(c_oos, b_oos)`; this
        # now does the same, so the cell-vs-base delta actually exists.
        #
        # This is the SAME gate gap the fleet sweep found and fixed on
        # 2026-08-10 -- "a Path B candidate short-circuited to `is_oos_fail`
        # BEFORE any walk-forward ran, so every Path B candidate on record had
        # ZERO generalisation evidence" -- reproduced in the newer sweep, and
        # the second instance of the lever sweep getting a fix the bracket
        # sweep did not inherit (the first: the write-only corpus,
        # BL-20260823-E35-SWEEP-EVIDENCE-HAS-NO-DURABLE-PATH).
        #
        # `capital_delta` is REPORTED, never graded, and returns None (never
        # 0.0) for an unmeasurable rate -- so an unmeasurable cell still fails
        # the predicate, which is correct: it is "we could not look", not "the
        # rate did not improve".
        cap_oos = fleet.capital_delta(c_oos, b_oos)
        rec["capital"] = {"IS": fleet.capital_delta(c_is, b_is),
                          "OOS": cap_oos}
        path_b = (not path_a) and fleet.is_path_b_candidate(
            fleet.beats_detail(c_is, b_is), fleet.beats_detail(c_oos, b_oos),
            cap_oos)
        if not path_a and not path_b:
            rec["verdict"] = "is_oos_fail"
            out.append(rec)
            continue
        rec["path"] = "A" if path_a else "B"
        wf = fleet.walkforward(harness, base, args, log, leg["leg"], tag,
                               require_dd=path_a)
        rec["wf"] = wf
        rec["dd_rate_is"] = fleet.drawdown_exchange_rate(c_is, b_is)
        rec["dd_rate_oos"] = fleet.drawdown_exchange_rate(c_oos, b_oos)
        usable = wf.get("usable") or 0
        # The ~2/3 tally, and the EFFECTIVE tally beside it. Never one without
        # the other (BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS).
        passed = usable > 0 and wf["wins"] >= math.ceil(2 * usable / 3)
        rec["verdict"] = (("wf_pass" if path_a else "path_b_wf_pass")
                          if passed else "wf_fail")
        out.append(rec)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--only", default=None,
                    help="comma-separated leg names")
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "e35_bracket"))
    ap.add_argument("--singles-only", action="store_true",
                    help="one-axis-at-a-time grid only (no joint cells)")
    ap.add_argument("--surface-only", action="store_true",
                    help="stage 1 only; skip the IS/OOS + walk-forward gate")
    ap.add_argument("--gate-top", type=int, default=3,
                    help="how many surface cells per axis go to the gate "
                         "(plus the joint argmax). 0 = gate nothing.")
    ap.add_argument("--split", default="2025-07-01")
    ap.add_argument("--split-mode", choices=["oos-trades", "date"],
                    default="oos-trades")
    ap.add_argument("--split-target-oos", type=int, default=50)
    ap.add_argument("--tp-cap-pct", type=float, default=fleet.LIVE_TP_CAP_PCT)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv[1:])

    if a.selftest:
        return _selftest()

    only = [s.strip() for s in a.only.split(",")] if a.only else None
    runnable, skipped = plan_legs(Path(a.data_dir), only, a.tp_cap_pct)
    print(f"plan: {len(runnable)} legs runnable, {len(skipped)} skipped")
    for s in skipped:
        print(f"  SKIP {s['leg']}: {s['reason']}")
    if a.list:
        for p in runnable:
            g = p["base_geometry"]
            print(f"  RUN  {p['leg']:26s} {p['harness'].split('/')[-1]:22s} "
                  f"{Path(p['data']).name:18s} "
                  f"base tp_r={g['tp_r']:g}({g['tp_r_source']}) "
                  f"sm={g['stop_mult']:g}({g['stop_mult_source']}) "
                  f"to={g['timeout']}({g['timeout_source']}) "
                  f"cap={g['tp_cap_pct']:g}")
        return 0

    run_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    fh = results_path.open("a")

    def log(row: dict) -> None:
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grid": {"tp_r": list(TP_R_GRID), "atr_stop_mult": list(STOP_MULT_GRID),
                 "timeout_bars": list(TIMEOUT_GRID),
                 "singles_only": bool(a.singles_only)},
        "tp_cap_pct": a.tp_cap_pct,
        "fee_bps_roundtrip": "harness default "
                             "(execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP)",
        "skipped": skipped, "legs": [],
    }
    for p in runnable:
        print(f"== {p['leg']} ==", flush=True)
        s = surface(p, singles_only=a.singles_only, log=log)
        if "error" in s:
            print(f"   ERROR {s['error'][:120]}")
            report["legs"].append(s)
            continue
        sf = s["surface"]
        print(f"   base net_R={sf['base_net_r']} | grid net_R "
              f"[{sf['net_r_min']}, {sf['net_r_max']}] spread={sf['net_r_spread']}"
              f" | best {sf['best_cell']} d={sf['best_d_net_r']}", flush=True)
        if not a.surface_only and a.gate_top > 0:
            cands = _gate_candidates(s["cells"], a.gate_top)
            s["gate"] = gate(p, cands, a.split_mode, a.split,
                             a.split_target_oos, log)
            for g in s["gate"]:
                print(f"   GATE {g['cell']:>16s} [{g['axis']}] -> {g['verdict']}"
                      + (f"  wf={g['wf']['summary']}"
                         f" (eff {g['wf']['summary_effective']})"
                         if g.get("wf") else ""), flush=True)
        report["legs"].append(s)
    fh.close()
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (run_dir / "SUMMARY.md").write_text(render_summary(report))
    print(f"\nwrote {run_dir}/report.json + SUMMARY.md + results.jsonl")
    return 0


def _gate_candidates(cells: list[dict], top: int) -> list[dict]:
    """Top-`top` measured cells per axis by net_total_r, plus the joint argmax.

    Per-AXIS rather than global, deliberately: a global top-N on a leg where one
    axis dominates would send three cells of the same axis to the gate and never
    test the others, so an axis could read as ungraded when it was simply
    out-ranked. The joint argmax rides along so the E3 falsifier — *a combined
    cell must beat the best single cell* — has both terms available.
    """
    if top <= 0:
        # `0` means gate nothing, and that has to include the joint argmax —
        # otherwise the one documented way to run surface-only-through-this-
        # function still sends a cell to the gate. Caught by the self-test.
        return []
    measured = [c for c in cells
                if c.get("state") == "measured" and c.get("net_total_r") is not None]
    picked: dict[str, dict] = {}
    for ax in AXES:
        single = [c for c in measured if c["axis"] == ax]
        for c in sorted(single, key=lambda r: -r["net_total_r"])[:top]:
            picked[c["cell"]] = c
    joint = [c for c in measured if "+" in c["axis"]]
    if joint:
        best = max(joint, key=lambda r: r["net_total_r"])
        picked[best["cell"]] = best
    return list(picked.values())


def _lev_cell(lev: dict | None) -> str:
    """Render the leverage column. `n/a` and `unmeasured` are DIFFERENT and stay
    different: one is "the cell does not move the stop", the other is "we could
    not look"."""
    if not lev:
        return "—"
    st = lev.get("state")
    if st == "measured":
        m = lev["leverage_multiple"]
        return f"{m:.2f}x" + (" ⚠" if lev.get("leverage_contingent") else "")
    return {"not_applicable": "n/a", "unmeasured": "unmeasured"}.get(st, str(st))


def render_summary(report: dict) -> str:
    L = ["# E3.5 — bracket-geometry sweep (tp_r x atr_stop_mult x timeout_bars)",
         "",
         f"Generated `{report['generated_at']}` · "
         f"tp_cap_pct `{report['tp_cap_pct']}` · fees "
         f"`{report['fee_bps_roundtrip']}`",
         "",
         "**Every `net_R` below is NET OF FEES.** A lower take-profit raises "
         "turnover, so a fee-free basis would flatter exactly the cells most "
         "likely to pass.",
         "",
         "⚠️ **A stop-axis `net_R` is unreadable without `leverage x`** — "
         "`risk = atr_stop_mult * ATR` and `qty = risk_budget / risk`, so a "
         "TIGHTER stop buys its R with MORE leverage. `leverage x > 1` means the "
         "gain is contingent on the account being able to size it. Reported, "
         "never gated.",
         "",
         "## Response surface (full history)",
         "",
         "Read `spread` first: it is what the whole bracket dimension is worth on "
         "that leg. An argmax over a flat surface is noise.",
         "",
         "| leg | exec | tf | base net_R | grid min | grid max | **spread** | "
         "best cell | best Δ | n |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for leg in report["legs"]:
        if "error" in leg:
            L.append(f"| {leg['leg']} | — | — | ERROR: "
                     f"{str(leg['error'])[:60]} | | | | | | |")
            continue
        s = leg["surface"]
        L.append(f"| `{leg['leg']}` | {leg['execution']} | {leg['tf']} | "
                 f"{s['base_net_r']} | {s['net_r_min']} | {s['net_r_max']} | "
                 f"**{s['net_r_spread']}** | `{s['best_cell']}` | "
                 f"{s['best_d_net_r']} | {s['n_measured']}/{s['n_cells']} |")
    gated = [(leg, g) for leg in report["legs"] for g in leg.get("gate", [])]
    L += ["", "## Gate (IS/OOS Path A/B + yearly walk-forward)", ""]
    if not gated:
        L.append("_No cell reached the gate._")
    else:
        L += ["| leg | cell | axis | verdict | path | wf | wf effective | "
              "leverage x |", "|---|---|---|---|---|---|---|---|"]
        for leg, g in gated:
            wf = g.get("wf") or {}
            L.append(f"| `{g['leg']}` | `{g['cell']}` | {g['axis']} | "
                     f"**{g['verdict']}** | {g.get('path','—')} | "
                     f"{wf.get('summary','—')} | "
                     f"{wf.get('summary_effective','—')} | "
                     f"{_lev_cell(g.get('leverage'))} |")
    L += ["", "## Base geometry actually measured", "",
          "| leg | tp_r | source | atr_stop_mult | source | timeout | source | "
          "tp_cap |", "|---|---|---|---|---|---|---|---|"]
    for leg in report["legs"]:
        g = leg.get("base_geometry")
        if not g:
            continue
        L.append(f"| `{leg['leg']}` | {g['tp_r']:g} | {g['tp_r_source']} | "
                 f"{g['stop_mult']:g} | {g['stop_mult_source']} | "
                 f"{g['timeout']} | {g['timeout_source']} | "
                 f"{g['tp_cap_pct']:g} |")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Self-test — pure functions only, no harness runs. `--selftest`.
# ---------------------------------------------------------------------------
def _selftest() -> int:
    ok = fail = 0

    def chk(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
        else:
            fail += 1
            print(f"FAIL {name}: got {got!r} want {want!r}")

    base = ["--data", "d.csv", "--symbol", "X", "--atr-stop-mult", "2.5",
            "--tp-cap-pct", "0.099", "--tp-r", "50.0"]
    # cell_args REPLACES, never duplicates a flag.
    a = cell_args(base, 2.0, None, None)
    chk("tp replaced once", a.count("--tp-r"), 1)
    chk("tp value", a[a.index("--tp-r") + 1], "2")
    chk("stop untouched", a[a.index("--atr-stop-mult") + 1], "2.5")
    a2 = cell_args(base, None, 1.5, 96)
    chk("stop replaced once", a2.count("--atr-stop-mult"), 1)
    chk("stop value", a2[a2.index("--atr-stop-mult") + 1], "1.5")
    chk("timeout appended", a2[a2.index("--timeout-bars") + 1], "96")
    chk("tp preserved", a2[a2.index("--tp-r") + 1], "50.0")

    geo = base_geometry("scripts/backtest_trend.py", base)
    chk("geo tp_r", geo["tp_r"], 50.0)
    chk("geo tp_r source", geo["tp_r_source"], "base_args")
    chk("geo stop", geo["stop_mult"], 2.5)
    chk("geo timeout default", geo["timeout"], 200)
    chk("geo timeout source", geo["timeout_source"], "harness_default")
    geo_sq = base_geometry("scripts/backtest_squeeze.py", ["--data", "d"])
    chk("squeeze timeout default", geo_sq["timeout"], 48)
    chk("unknown harness timeout", base_geometry("nope.py", [])["timeout"], None)
    chk("unknown harness source",
        base_geometry("nope.py", [])["timeout_source"], "unknown")

    # axis_of: a grid point equal to the base moves nothing.
    g = {"tp_r": 50.0, "stop_mult": 2.5, "timeout": 200}
    chk("axis none", axis_of(50.0, None, None, g), "none")
    chk("axis tp", axis_of(2.0, None, None, g), "tp")
    chk("axis joint", axis_of(2.0, 1.5, None, g), "tp+stop")
    chk("axis timeout eq base", axis_of(None, None, 200, g), "none")
    chk("axis timeout", axis_of(None, None, 24, g), "timeout")
    chk("axis unknown-base timeout is not moved",
        axis_of(None, None, 24, {"tp_r": 50.0, "stop_mult": 2.5,
                                 "timeout": None}), "none")

    chk("tag", cell_tag(2.0, 1.5, 96), "tp2_sm1.5_to96")
    chk("tag base", cell_tag(None, None, None), "base")

    # leverage_check — reported, never gated.
    def m(rf, tot=0.0):
        return {"state": "measured", "total": tot, "mean_risk_per_entry": rf,
                "notional_per_risk_budget": (round(1 / rf, 3) if rf else None)}
    lc = leverage_check(m(0.0375), m(0.0630))
    chk("lev tighter stop needs more", lc["leverage_contingent"], True)
    chk("lev multiple", round(lc["leverage_multiple"], 3), 1.68)
    lc2 = leverage_check(m(0.0884), m(0.0630))
    chk("lev wider stop needs less", lc2["leverage_contingent"], False)
    chk("lev multiple wider", round(lc2["leverage_multiple"], 3), 0.713)
    chk("lev equal is not contingent",
        leverage_check(m(0.063), m(0.063))["leverage_contingent"], False)
    chk("lev na", leverage_check({"state": "not_applicable"}, m(0.063))["state"],
        "not_applicable")
    chk("lev unmeasured cell",
        leverage_check({"state": "unmeasured"}, m(0.063))["state"], "unmeasured")
    chk("lev unmeasured base",
        leverage_check(m(0.063), {"state": "unmeasured"})["state"], "unmeasured")
    chk("lev zero refuses to divide",
        leverage_check(m(0.0), m(0.063))["state"], "unmeasured")

    # _gate_candidates — per-axis, plus the joint argmax.
    cells = [
        {"cell": "tp1", "axis": "tp", "state": "measured", "net_total_r": 1.0},
        {"cell": "tp2", "axis": "tp", "state": "measured", "net_total_r": 9.0},
        {"cell": "sm1", "axis": "stop", "state": "measured", "net_total_r": 0.5},
        {"cell": "to1", "axis": "timeout", "state": "measured", "net_total_r": 0.4},
        {"cell": "j1", "axis": "tp+stop", "state": "measured", "net_total_r": 8.0},
        {"cell": "j2", "axis": "tp+stop", "state": "measured", "net_total_r": 2.0},
        {"cell": "bad", "axis": "tp", "state": "error"},
    ]
    got = sorted(c["cell"] for c in _gate_candidates(cells, 1))
    chk("gate candidates", got, ["j1", "sm1", "to1", "tp2"])
    chk("gate none", _gate_candidates(cells, 0), [])
    got2 = sorted(c["cell"] for c in _gate_candidates(cells, 2))
    chk("gate top2 keeps both tp", got2, ["j1", "sm1", "to1", "tp1", "tp2"])

    chk("_f ok", _f({"x": "1.5"}, "x"), 1.5)
    chk("_f missing", _f({}, "x"), None)
    chk("_f junk", _f({"x": "abc"}, "x"), None)
    chk("_flag last wins", _flag(["--a", "1", "--a", "2"], "--a"), "2")
    chk("_flag absent", _flag(["--a", "1"], "--b"), None)
    chk("_flag dangling", _flag(["--a"], "--a"), None)

    # Grid points must not silently collide with the live base value.
    chk("2.5 not in stop grid", 2.5 in STOP_MULT_GRID, False)
    chk("50 not in tp grid", 50.0 in TP_R_GRID, False)
    chk("200 not in timeout grid", 200 in TIMEOUT_GRID, False)

    print(f"selftest: {ok} pass, {fail} fail")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
