"""Tests for the fleet sweep's capital-efficiency REPORTING (Path B input).

`scripts/research/m20_fleet_exit_sweep.py::capital_delta` is the half that was
missing when every pullback `stale_stop` cell in the coverage matrix was graded
`honest_negative` off the 2026-07-12 fleet sweep: `scripts/capital_efficiency.py`
did not exist until 2026-08-10 (`3240557`), so Path A's net_R-AND-maxDD gate was
the only axis those verdicts could see, and a lever that frees capital at a small
net_R cost fails `beats()` before a walk-forward ever runs.

These pin the two properties that make the reported number safe to set a
threshold from:

  1. **Unmeasurable is `None`, never `0.0`.** A fabricated zero would rank an
     un-measured cell alongside a genuinely flat one, which is the collapsed-state
     failure `capital_efficiency.days_from_bars` already refuses upstream.
  2. **It reports, it does not grade.** Path B's thresholds are deliberately
     unset; nothing in this module may encode one.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "m20_fleet_exit_sweep", REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["m20_fleet_exit_sweep"] = _mod
_spec.loader.exec_module(_mod)

capital_delta = _mod.capital_delta
beats = _mod.beats


def _summary(*, net_r, capital_day=None, position_day=None, bars=None,
             cap_days=None, mdd=1.0):
    return {
        "net_total_r": net_r,
        "max_drawdown_r": mdd,
        "net_r_per_capital_day": capital_day,
        "net_r_per_position_day": position_day,
        "mean_bars_held": bars,
        "capital_days": cap_days,
    }


def test_unmeasurable_rate_reports_none_not_zero():
    """The whole point of the module upstream: 'we could not measure the hold'
    and 'the hold was zero' are opposite statements."""
    cell = _summary(net_r=10.0, capital_day=None)
    base = _summary(net_r=12.0, capital_day=None)
    out = capital_delta(cell, base)
    assert out["cell_net_r_per_capital_day"] is None
    assert out["base_net_r_per_capital_day"] is None
    assert out["d_net_r_per_capital_day"] is None
    # and specifically NOT 0.0 — a consumer sorting on this must be able to
    # separate unmeasured from flat.
    assert out["d_net_r_per_capital_day"] is not 0.0  # noqa: F632 - identity is the point


def test_half_measured_pair_still_refuses_to_invent_a_delta():
    """One side measured and the other not is the case a naive `(c or 0)-(b or 0)`
    would turn into a confident, wrong number."""
    out = capital_delta(_summary(net_r=10.0, capital_day=0.5),
                        _summary(net_r=10.0, capital_day=None))
    assert out["cell_net_r_per_capital_day"] == 0.5
    assert out["base_net_r_per_capital_day"] is None
    assert out["d_net_r_per_capital_day"] is None


def test_the_path_b_population_is_measured_even_though_path_a_rejects_it():
    """A capital-freeing lever: net_R falls slightly, capital/day rises. Path A
    says no; the capital axis must still carry a real number, because this cell
    is exactly what Path B exists to evaluate."""
    base = _summary(net_r=20.0, capital_day=0.10, bars=149.0, mdd=5.0)
    cell = _summary(net_r=18.0, capital_day=0.40, bars=30.0, mdd=5.0)

    assert beats(cell, base) is False              # Path A rejects it
    out = capital_delta(cell, base)
    assert out["d_net_r_per_capital_day"] == 0.30  # 4x the capital rate
    assert out["d_net_total_r"] == -2.0
    assert out["net_r_retained_frac"] == 0.9       # lost 10% of net_R
    assert out["d_mean_bars_held"] == -119.0


def test_net_r_retained_frac_is_none_when_base_net_r_is_not_positive():
    """'net_R fell no more than X%' is meaningless against a zero or negative
    base — None, not a large-looking ratio."""
    for bad_base in (0.0, -5.0):
        out = capital_delta(_summary(net_r=-1.0, capital_day=0.2),
                            _summary(net_r=bad_base, capital_day=0.1))
        assert out["net_r_retained_frac"] is None
    ok = capital_delta(_summary(net_r=5.0, capital_day=0.2),
                       _summary(net_r=10.0, capital_day=0.1))
    assert ok["net_r_retained_frac"] == 0.5


def test_non_numeric_values_degrade_to_none_rather_than_raising():
    """The sweep must never die on one odd harness payload mid-fleet."""
    out = capital_delta({"net_total_r": "n/a", "net_r_per_capital_day": "x"},
                        {"net_total_r": None})
    assert out["d_net_r_per_capital_day"] is None
    assert out["d_net_total_r"] is None
    assert out["net_r_retained_frac"] is None


def test_module_encodes_no_path_b_threshold():
    """Path B's two thresholds are the operator's to set from the measured
    distribution. A session inventing one here is the failure this guards.

    Scans the CODE, not the prose: the docstring necessarily *mentions*
    thresholds in order to say it does not apply any, and a raw substring
    search over the whole function flags that explanation as a violation. The
    docstring is stripped before scanning so the test cannot be satisfied by
    deleting the comment that explains the rule — the same reasoning that makes
    `provenance-consumer-guard`'s override verified rather than presence-only.
    """
    import ast
    src = (REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "capital_delta")
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                      # drop the docstring
    assert body, "capital_delta has no executable body to check"
    code = "\n".join(ast.unparse(n) for n in body)

    # No pass/fail vocabulary in the returned keys or logic...
    for banned in ("PASS", "path_b", "threshold", "verdict"):
        assert banned.lower() not in code.lower(), \
            f"capital_delta must report, not grade: found {banned!r}"
    # ...and no comparison of a metric against a numeric literal, which is what
    # a hard-coded threshold would look like. The `bn > 0` guard is a
    # well-definedness check on the DENOMINATOR, not a gate on the metric, so
    # it is allowed by name.
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Compare):
            continue
        rendered = ast.unparse(node)
        if rendered == "bn > 0":
            continue
        assert not any(isinstance(c, ast.Constant) and isinstance(c.value, (int, float))
                       and not isinstance(c.value, bool)
                       for c in node.comparators), \
            f"capital_delta compares a metric to a literal (a threshold?): {rendered}"


# --------------------------------- the percentile arm must not fake abstention
def test_winner_mfe_p80_distinguishes_shape_mismatch_from_thin_sample():
    """`winner_mfe_p80` returns None for two very different reasons, and its
    docstring contract declares only one of them ("< 30 winners").

    Measured 2026-08-10: it read `row["mfe_r"]` top-level, so for every
    `ict_scalp` leg -- whose harness nests mfe_r under `meta` -- it collected
    zero MFEs and returned the not-enough-winners answer for a leg with 1,102
    trades. An inert arm that reports a legitimate-looking abstention is worse
    than one that errors, because the caller records the abstention as data.

    Structural, because the function shells out to a real harness: it must read
    through the ONE accessor, and it must branch on winners-seen so the two
    causes stay distinguishable.
    """
    import ast
    src = (REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "winner_mfe_p80")
    code = ast.unparse(fn)
    assert "mfe_r_of" in code, \
        "winner_mfe_p80 re-derives the MFE read instead of using the one accessor"
    assert "t['mfe_r']" not in code and 't["mfe_r"]' not in code, \
        "winner_mfe_p80 still does a raw top-level mfe_r read"
    assert "winners_seen" in code, \
        "winner_mfe_p80 cannot tell 'no winners' from 'no readable MFE'"


# ------------- every swept harness must be able to REPORT the capital axis
def test_every_swept_harness_emits_the_capital_keys():
    """The axis the operator raised to a PRINCIPLE on 2026-08-10 has to exist in
    every harness the fleet sweep drives, or it is silently blind on a family.

    Measured that day: the sweep returned `net_r_per_capital_day: null` for 14
    of 14 cells on EVERY donchian leg while the pullback legs measured 14/14.
    Not a property of those books — backtest_pullback.py imports
    capital_efficiency and backtest_trend.py / backtest_squeeze.py did not. It
    blinded the axis precisely where the giveback census says the money is (the
    1h Bybit trend legs carry ~1,400R of the fleet's 2,443R).

    Same shape as the mfe_r gap two hours earlier, which is why this is a test
    over ALL harnesses rather than a fix to two files: a metric present in one
    harness and absent in its sibling degrades to a null that reads as
    "measured, no effect".
    """
    import importlib.util
    import pandas as pd
    import sys as _sys
    _sys.path.insert(0, str(REPO / "scripts"))
    import capital_efficiency as ce

    required = set(ce.empty())
    empty_df = pd.DataFrame({"timestamp": pd.to_datetime([]), "open": [], "high": [],
                             "low": [], "close": [], "volume": []})
    # The harnesses scripts/research/m20_fleet_exit_sweep.py::HARNESS routes to.
    for name in ("backtest_trend", "backtest_pullback", "backtest_squeeze"):
        spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[name] = mod
        spec.loader.exec_module(mod)
        s = mod._summarize([], empty_df, timeframe="1h", symbol="X", params={})
        missing = sorted(required - set(s))
        assert not missing, (
            f"{name} cannot report the capital axis (missing {missing}) — the "
            "fleet sweep will emit null for every cell on this family and it "
            "will read as 'measured, no effect'")
        # ...and an unmeasured rate is None, never a fabricated zero.
        assert s["net_r_per_capital_day"] is None, (
            f"{name} reports a zero-trade capital rate as "
            f"{s['net_r_per_capital_day']!r} — 'we could not measure' and 'the "
            "rate was zero' are opposite statements")


def _sweep_module():
    """Load the sweep driver by path (scripts/research is not a package)."""
    import importlib.util
    import sys as _sys
    spec = importlib.util.spec_from_file_location(
        "m20_fleet_exit_sweep", REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["m20_fleet_exit_sweep"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_run_cell_memo_collapses_the_repeated_walkforward_base(monkeypatch):
    """The same invocation must be executed ONCE per process.

    Not a micro-optimization. The walk-forward re-runs each leg's base for
    every fold of every candidate, and those runs are byte-identical across
    candidates — so a five-candidate leg paid for the same six base folds five
    times. On an ict_scalp 5m leg (census-measured at ~955s per full-history
    run) that redundancy is hours, and it is what pushed the family past the
    job timeout.
    """
    mod = _sweep_module()
    mod._CELL_CACHE.clear()
    calls = []

    class _P:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, **kw):
        calls.append(tuple(cmd))
        import json as _json
        import pathlib as _pl
        _pl.Path("/tmp/m20_fleet_cell.json").write_text(_json.dumps({"net_total_r": 1.0}))
        return _P()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    args = ["--data", "x.csv"]
    for _ in range(5):
        assert mod.run_cell("scripts/backtest_trend.py", args,
                            start="2021-01-01", end="2022-01-01")["net_total_r"] == 1.0
    assert len(calls) == 1, f"memo did not hold — ran {len(calls)} times"
    # A different window is a different measurement and must NOT be served
    # from the cache.
    mod.run_cell("scripts/backtest_trend.py", args, start="2022-01-01", end="2023-01-01")
    assert len(calls) == 2


def test_run_cell_does_not_cache_a_timeout(monkeypatch):
    """A timeout is 'we did not finish looking', not a measured result.

    Caching it would make one slow run permanent for the rest of the process
    and turn a transient into a fleet of confident `verdict: error` rows.
    """
    mod = _sweep_module()
    mod._CELL_CACHE.clear()
    calls = []

    def boom(cmd, **kw):
        calls.append(1)
        raise mod.subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(mod.subprocess, "run", boom)
    for _ in range(3):
        out = mod.run_cell("scripts/backtest_trend.py", ["--data", "x.csv"])
        assert "timeout" in out["error"]
    assert len(calls) == 3, "a timeout was cached as though it were an answer"


def test_cell_timeout_default_clears_the_measured_scalp_runtime():
    """The 900s cap this used to carry sat under a measured scalp run.

    2026-08-10 census: one full-history ict_scalp_5m run took 955s. An IS-window
    run of the same leg is most of that, so the old cap would have converted a
    real measurement into `verdict: error` on the largest scalp leg.
    """
    mod = _sweep_module()
    assert mod.CELL_TIMEOUT_S >= 1800, (
        f"cell timeout {mod.CELL_TIMEOUT_S}s is at or under the measured "
        "955s full-history scalp run — the sweep would time out the leg it "
        "was dispatched to measure")


def test_the_four_netr_shapes_are_never_collapsed():
    """IS-only-up and OOS-only-up mean OPPOSITE things and must not share a bucket.

    The SUMMARY previously computed `oos_only` as the plain COMPLEMENT of
    "up on both windows" and printed it under the label "only out-of-sample" —
    a real count under a wrong name. Measured against the first scalp leg it
    ran on (ict_scalp_sol_15m, 2026-08-10) it inverted the diagnosis: every one
    of the five cells was negative or zero on OOS, and the header said all five
    improved out-of-sample only. `be_touch_arm` there is IS +1.138 / OOS
    -1.8913 — the OVERFIT shape — reported as an out-of-sample improver.
    """
    mod = _sweep_module()

    def _up(v):
        return v is not None and v > 0

    rows = [
        {"is_d_net_r": 1.0, "oos_d_net_r": 2.0},    # both
        {"is_d_net_r": 1.138, "oos_d_net_r": -1.8913},  # IS-only (the real case)
        {"is_d_net_r": -1.0, "oos_d_net_r": 3.0},   # OOS-only
        {"is_d_net_r": -1.0, "oos_d_net_r": -2.0},  # neither
        {"is_d_net_r": 0.0, "oos_d_net_r": 0.0},    # neither (a tie is not an improvement)
        {"is_d_net_r": None, "oos_d_net_r": 1.0},   # ungradeable — its own state
    ]
    known = [r for r in rows
             if r["is_d_net_r"] is not None and r["oos_d_net_r"] is not None]
    both = [r for r in known if _up(r["is_d_net_r"]) and _up(r["oos_d_net_r"])]
    is_only = [r for r in known if _up(r["is_d_net_r"]) and not _up(r["oos_d_net_r"])]
    oos_only = [r for r in known if not _up(r["is_d_net_r"]) and _up(r["oos_d_net_r"])]
    neither = [r for r in known
               if not _up(r["is_d_net_r"]) and not _up(r["oos_d_net_r"])]

    assert (len(both), len(is_only), len(oos_only), len(neither)) == (1, 1, 1, 2)
    # The four buckets partition the GRADEABLE rows exactly, and the ungradeable
    # row is in none of them — "we could not compare" is not "did not improve".
    assert len(both) + len(is_only) + len(oos_only) + len(neither) == len(known)
    assert len(known) == len(rows) - 1

    # And the shape helper the table prints must agree with those buckets.
    shape = mod.__dict__.get("_shape")
    if shape is None:  # defined inside main(); assert the vocabulary instead
        return
    assert shape(rows[1]) == "IS-only"


def test_the_gate_and_capital_net_r_deltas_are_kept_apart():
    """The 'Δ netR OOS' column must come from the GATE, not the capital block.

    It printed `d_net_total_r` (capital) under a gate-named header while the
    gate's own `oos_d_net_r` was never stored at all. The two are probably the
    same quantity — 'probably' is not a provenance, and a silent divergence
    would make the column lie without changing anything visible.
    """
    src = (REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py").read_text()
    assert '"oos_d_net_r": g_oos.get("d_net_r")' in src, (
        "the gate's OOS net_R delta is not captured; the table has nothing "
        "gate-sourced to print")
    # The gate-named column must not be fed from the capital key.
    assert "{d['d_net_total_r']} | " not in src, (
        "a capital-block value is still being printed under a gate-named column")


def test_a_giveback_rung_at_or_above_the_fixed_tp_is_withheld_with_a_reason():
    """A cell that CANNOT fire must not be reported as one that fired flat.

    Every live ict_scalp leg is tp_at_r 1.5 and the grid emitted
    gb1R_afterMFE2R for all of them. The harness's exit order settles that it
    is a PROVABLE no-op, not a rare one: the TP check returns before the
    giveback block is reached, so no trade is ever alive at MFE >= 2R. Three
    legs (sol_15m, xrp_15m, eth_15m) duly reported it at exactly 0.0 on net_R,
    maxDD and capital/day across BOTH windows — under the gate reason
    `tie_no_improvement`, which reads as "measured, made no difference".

    Withheld, not silently dropped: "not asked" and "asked and flat" are
    different states.
    """
    mod = _sweep_module()
    scalp = {"timeframe": "15m", "symbols": ["ETHUSDT"], "tp_at_r": 1.5}
    inert = []
    cells = mod.cells_for(scalp, "scalp", skipped=inert)
    tags = [c[0] for c in cells]
    assert "gb1R_afterMFE2R" not in tags
    assert [s["cell"] for s in inert] == ["gb1R_afterMFE2R"]
    assert "provable_noop" in inert[0]["reason"]
    assert "tp_at_r=1.5" in inert[0]["reason"]
    # The 1R rung is BELOW the bracket and stays — it measured non-zero on all
    # three of those legs, so the predicate must not over-reach.
    assert "gb1R_afterMFE1R" in tags


def test_a_leg_with_no_fixed_bracket_keeps_every_rung():
    """The predicate is about a FIXED bracket, not about giveback in general.

    A donchian/pullback leg declares no tp_at_r (it trails to a far sentinel),
    so both rungs are reachable and withholding either would delete real
    coverage — the opposite failure to the cosmetic cell.
    """
    mod = _sweep_module()
    inert = []
    cells = mod.cells_for({"timeframe": "1h", "symbols": ["BTCUSDT"],
                           "trail_mult": 3.0}, "donchian", skipped=inert)
    gb = [c[0] for c in cells if c[1] == "giveback_stop"]
    assert gb == ["gb1R_afterMFE1R", "gb1R_afterMFE2R"]
    assert inert == []
    # And a wider fixed bracket keeps both too — 2R < 3R is reachable.
    inert2 = []
    cells2 = mod.cells_for({"timeframe": "15m", "symbols": ["X"], "tp_at_r": 3.0},
                           "scalp", skipped=inert2)
    assert "gb1R_afterMFE2R" in [c[0] for c in cells2]
    assert inert2 == []


def test_inert_reason_is_none_when_the_rung_is_reachable():
    mod = _sweep_module()
    assert mod.inert_giveback_reason({"tp_at_r": 1.5}, 1.0) is None
    assert mod.inert_giveback_reason({"tp_at_r": 1.5}, 1.5) is not None  # at == inert
    assert mod.inert_giveback_reason({"tp_at_r": 1.5}, 2.0) is not None
    # Unparseable / absent / non-positive tp_at_r must NOT be read as a bracket
    # — guessing there would delete a reachable cell.
    for cfg in ({}, {"tp_at_r": None}, {"tp_at_r": "x"}, {"tp_at_r": 0}):
        assert mod.inert_giveback_reason(cfg, 2.0) is None


def _fake_runs(mod, per_window):
    """Patch run_cell so walkforward() sees a scripted fold sequence.

    Keyed on (start, end) and whether the argv carries the cell flag, so the
    base and lever arms of each fold can be scripted independently.
    """
    def fake(harness, args, start=None, end=None):
        is_cell = "--CELL" in args
        return per_window[(start, "cell" if is_cell else "base")]
    mod.run_cell = fake


def test_path_b_walkforward_does_not_gate_on_the_axis_path_b_trades():
    """A drawdown-trading cell fails Path A's fold test BY CONSTRUCTION.

    Path A demands net_R no worse AND maxDD no worse in each fold. A Path B
    candidate is, by definition, buying net_R with drawdown — so under Path A's
    rule it scores 0/N in every fold and the walk-forward answers a question the
    cell never claimed to pass. That 0/N would read as a measured negative.
    """
    mod = _sweep_module()
    # Every fold: net_R better by 1.0, drawdown worse by 0.5.
    script = {}
    for _name, fs, _fe in mod.FOLDS:
        script[(fs, "base")] = {"net_total_r": 10.0, "max_drawdown_r": -5.0}
        script[(fs, "cell")] = {"net_total_r": 11.0, "max_drawdown_r": -4.5}
    _fake_runs(mod, script)

    strict = mod.walkforward("h", ["--base"], ["--base", "--CELL"],
                             lambda row: None, "leg", "cell", require_dd=True)
    lenient = mod.walkforward("h", ["--base"], ["--base", "--CELL"],
                              lambda row: None, "leg", "cell", require_dd=False)
    assert strict["wins"] == 0 and strict["usable"] == len(mod.FOLDS)
    assert lenient["wins"] == len(mod.FOLDS)
    # And the cost is RECORDED per fold, not gated away — that distribution is
    # what an operator sets a drawdown tolerance against.
    assert all(f["d_max_dd"] == 0.5 for f in lenient["folds"])
    assert all(f["d_net_r"] == 1.0 for f in lenient["folds"])


def test_walkforward_separates_unusable_folds_from_lost_ones():
    """An errored fold is not a lost fold — it lowers the denominator.

    Folding them together would let a cell that could only be measured twice
    report the same '2/2' as one measured across six.
    """
    mod = _sweep_module()
    script = {}
    for i, (_n, fs, _fe) in enumerate(mod.FOLDS):
        if i < 2:
            script[(fs, "base")] = {"error": "no data"}
            script[(fs, "cell")] = {"error": "no data"}
        else:
            script[(fs, "base")] = {"net_total_r": 1.0, "max_drawdown_r": -1.0}
            script[(fs, "cell")] = {"net_total_r": 2.0, "max_drawdown_r": -2.0}
    _fake_runs(mod, script)
    wf = mod.walkforward("h", ["--base"], ["--base", "--CELL"],
                         lambda row: None, "leg", "cell", require_dd=True)
    assert wf["usable"] == len(mod.FOLDS) - 2
    assert wf["wins"] == wf["usable"]
    assert wf["summary"] == f"{wf['wins']}/{wf['usable']}"
    unusable = [f for f in wf["folds"] if not f["usable"]]
    assert len(unusable) == 2 and all("why" in f for f in unusable)


def test_path_b_predicate_needs_both_windows_and_capital_up():
    """The real numbers, so the predicate is pinned to measurement not intent."""
    mod = _sweep_module()
    # ict_scalp_sol_5m be_touch_arm — the first true Path B candidate in the
    # scalp family: IS +10.7225 (drawdown BETTER), OOS +1.3233 (drawdown worse).
    assert mod.is_path_b_candidate(
        {"d_net_r": 10.7225}, {"d_net_r": 1.3233},
        {"d_net_r_per_capital_day": 0.0828}) is True
    # ict_scalp_xrp_5m stale8 — IS -13.2485 / OOS +9.056, the best OOS cell in
    # the sweep and a 22R swing between adjacent periods. NOT a trade-off.
    assert mod.is_path_b_candidate(
        {"d_net_r": -13.2485}, {"d_net_r": 9.056},
        {"d_net_r_per_capital_day": 0.0397}) is False
    # Capital efficiency down, or unmeasured, is not "up".
    for cap in ({"d_net_r_per_capital_day": -0.5},
                {"d_net_r_per_capital_day": None}, {}):
        assert mod.is_path_b_candidate(
            {"d_net_r": 1.0}, {"d_net_r": 1.0}, cap) is False


def test_the_derived_tolerance_discriminates_where_a_fleet_scalar_cannot():
    """The evidence-based Path B tolerance (operator directive 2026-08-10).

    No scalar: a cell may deepen drawdown only if net_R per unit of drawdown
    does not get worse, so the allowance is derived from each leg's own base.
    The same +1.0R-for-+2.0R ask must be REJECTED on an efficient book and
    allowed on an inefficient one — a fleet-wide "+2R is fine" passes both.
    """
    mod = _sweep_module()
    ask = lambda nb, db: mod.drawdown_exchange_rate(  # noqa: E731
        {"net_total_r": nb + 1.0, "max_drawdown_r": db + 2.0},
        {"net_total_r": nb, "max_drawdown_r": db})

    efficient = ask(40.0, 12.0)          # base rate 3.33 R per R of drawdown
    assert efficient["passes"] is False
    assert efficient["allowed_d_max_dd"] == 0.3
    assert efficient["headroom"] == -1.7

    inefficient = ask(4.0, 9.0)          # base rate 0.44
    assert inefficient["passes"] is True
    assert inefficient["allowed_d_max_dd"] == 2.25
    assert inefficient["headroom"] == 0.25


def test_the_ratio_and_marginal_forms_agree():
    """N_c/D_c >= N_b/D_b  <=>  dN/dD >= N_b/D_b.

    Asserted rather than trusted: the docstring claims the two readings are the
    same condition, and a divergence would mean the reported `allowed`/`headroom`
    (derived from the marginal form) disagreed with the `passes` flag (computed
    by cross-multiplication).
    """
    mod = _sweep_module()
    for nb, db in ((40.0, 12.0), (4.0, 9.0), (7.5, 3.25)):
        for d_net in (0.1, 1.0, 5.0, 12.0):
            for d_dd in (0.05, 0.5, 2.0, 6.0):
                r = mod.drawdown_exchange_rate(
                    {"net_total_r": nb + d_net, "max_drawdown_r": db + d_dd},
                    {"net_total_r": nb, "max_drawdown_r": db})
                marginal_ok = (d_net / d_dd) >= (nb / db)
                assert r["passes"] == marginal_ok, (nb, db, d_net, d_dd)
                # headroom sign must agree with the verdict too.
                assert (r["headroom"] >= 0) == marginal_ok, (nb, db, d_net, d_dd)


def test_ungradeable_is_never_a_pass():
    mod = _sweep_module()
    cell = {"net_total_r": 6.0, "max_drawdown_r": 10.0}
    cases = {
        "base_unprofitable": {"net_total_r": -5.0, "max_drawdown_r": 9.0},
        "base_no_drawdown": {"net_total_r": 5.0, "max_drawdown_r": 0.0},
        "unreadable": {"net_total_r": 5.0},
    }
    for reason, base in cases.items():
        r = mod.drawdown_exchange_rate(cell, base)
        assert r["passes"] is None, reason
        assert r["reason"] == reason
        assert r["allowed_d_max_dd"] is None


# --------------------------------------------------------------------------
# `base_args` calls itself the CONFIG-EXACT base. That claim is load-bearing —
# every Δ in a promotion packet is measured against it — and it was FALSE for
# two census legs until 2026-08-10: `declared_levers()` threaded the stale,
# giveback and trail-DECAY levers but not the trail-VOL one, so
# `trend_donchian_eth` (below 0.1 / tight 2.5) and `qqq_pullback_1h`
# (above 0.8 / tight 2.5) were measured against a baseline missing a lever that
# is armed in live. Both harnesses had carried the flags all along.
#
# The completeness half is the point: a NEW exit-lever key added to
# config/strategies.yaml must fail here until it is threaded, rather than
# silently widening the gap between "the base" and "the config".
# --------------------------------------------------------------------------

_LEVER_KEY_TO_FLAG = {
    "stale_exit_bars": "--stale-exit-bars",
    "stale_exit_below_r": "--stale-exit-below-r",
    "giveback_min_mfe_r": "--giveback-min-mfe-r",
    "giveback_r": "--giveback-r",
    "trail_decay_arm_r": "--trail-decay-arm-r",
    "trail_decay_stall_bars": "--trail-decay-stall-bars",
    "trail_decay_tight_mult": "--trail-decay-tight-mult",
    "trail_vol_above_pctl": "--trail-vol-above-pctl",
    "trail_vol_below_pctl": "--trail-vol-below-pctl",
    "trail_vol_tight_mult": "--trail-vol-tight-mult",
}

# Exit-lever-shaped YAML keys that are deliberately NOT harness flags, each with
# the reason. Anything else matching the prefixes below is an unthreaded lever.
_NOT_A_LEVER = {
    # The ENTRY vol-skip gate + its window — threaded separately in the donchian
    # branch as --vol-skip-*/--vol-pctl-window, not part of declared_levers().
    "vol_skip_above_pctl", "vol_skip_below_pctl", "vol_pctl_window",
    # The base chandelier mult the levers TIGHTEN FROM, threaded per family.
    "trail_mult",
}


def _lever_shaped(key: str) -> bool:
    return key.startswith(("stale_exit_", "giveback_", "trail_decay_", "trail_vol_"))


def _declaring_legs():
    """(leg, cfg, family) for every donchian/pullback leg declaring a lever."""
    import yaml
    cfg = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    strats = cfg.get("strategies", cfg)
    out = []
    for name, block in strats.items():
        if not isinstance(block, dict):
            continue
        fam = "donchian" if "donchian" in name else (
            "pullback" if "pullback" in name else None)
        if fam is None:
            continue
        if any(_lever_shaped(k) for k in block):
            out.append((name, block, fam))
    return out


def test_every_declared_exit_lever_reaches_the_config_exact_base():
    legs = _declaring_legs()
    # A probe that finds nothing proves nothing — this fixture must be non-empty
    # or the assertion below is vacuous.
    assert legs, "no donchian/pullback leg declares an exit lever — fixture is dead"
    missing = []
    for name, block, fam in legs:
        argv = _mod.base_args(name, block, fam, "/tmp/x.csv", None)
        for key, flag in _LEVER_KEY_TO_FLAG.items():
            if block.get(key) is None:
                continue
            if flag not in argv:
                missing.append(f"{name}: {key}={block[key]} not threaded ({flag})")
    assert not missing, (
        "the base is NOT config-exact for:\n  " + "\n  ".join(missing))


def test_no_exit_lever_key_in_yaml_is_unmapped():
    """A new lever key must be threaded, not silently dropped."""
    unmapped = set()
    for name, block, _fam in _declaring_legs():
        for key in block:
            if _lever_shaped(key) and key not in _LEVER_KEY_TO_FLAG and key not in _NOT_A_LEVER:
                unmapped.add(f"{key} (on {name})")
    assert not unmapped, (
        "exit-lever-shaped YAML key(s) with no harness flag mapping — thread them "
        "through base_args::declared_levers or add them to _NOT_A_LEVER with a "
        f"reason: {sorted(unmapped)}")


def test_every_cfg_key_base_args_reads_is_declared_by_some_strategy():
    """A `cfg` key no strategy declares is DEAD, and dead reads fail silently.

    `opt(flag, key)` passes nothing when `cfg.get(key)` is None, so a
    misspelled key does not raise — it drops the flag and the harness quietly
    substitutes its OWN default. That is invisible in the output, which is what
    makes it worse than a crash.

    Two real instances found on 2026-08-10, both by reading the YAML rather
    than the code:
      * `trail_vol_*` — a REAL key that was simply never threaded.
      * `trend_len` / `pullback_len` — keys that do not exist anywhere; the YAML
        says `trend_lookback` / `pullback_lookback`. The sweep had been reading
        the wrong names and inheriting backtest_pullback's 40/10/0.5 defaults,
        which diverge from what 11 of 19 pullback legs actually declare. That
        one is ENTRY geometry: it changes which trades exist, not just exits.

    This asserts the weaker but fully general property — every key read must be
    declared by at least one strategy. It cannot catch a key that is threaded
    to the wrong FLAG, but it catches every key that is simply not real.
    """
    import re
    import yaml
    src = (REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py").read_text()
    start = src.index("def base_args")
    body = src[start:src.index("\ndef ", start + 10)]
    keys = set(re.findall(r'opt\(\s*"[^"]+"\s*,\s*"([^"]+)"\s*\)', body))
    assert len(keys) > 15, f"the opt() scrape found only {len(keys)} keys — regex drifted"

    cfg = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    declared = set()
    for block in (cfg.get("strategies", cfg)).values():
        if isinstance(block, dict):
            declared |= set(block)

    dead = sorted(keys - declared)
    assert not dead, (
        "base_args reads cfg key(s) that NO strategy declares — each silently "
        "drops its flag and lets the harness default stand in: " + str(dead))


# --------------------------------- the geometry banner must state what RAN
#
# `base_args` applies --tp-cap-pct ONLY to LIVE_TP_CAPPED_FAMILIES, because only
# those units carry `_TP_SENTINEL_CAP_PCT`. Measured 2026-08-10: ict_scalp.py
# contains ZERO occurrences of it. The PR-comment banner read the RUN-LEVEL flag
# and printed "LIVE-PARITY (capped TP 0.099)" on all 8 scalp legs anyway --
# asserting a geometry the code did not apply, on the one line whose whole job is
# to say which geometry produced the numbers below it.


def test_the_cap_is_not_applied_to_a_family_whose_unit_has_no_cap():
    """`base_args` must not pass --tp-cap-pct to a family that cannot use it."""
    mod = _sweep_module()
    assert "scalp" not in mod.LIVE_TP_CAPPED_FAMILIES
    capped = mod.base_args("x", {}, "pullback", "d", None, 0.099)
    uncapped = mod.base_args("x", {}, "scalp", "d", None, 0.099)
    assert "--tp-cap-pct" in capped, "the allowlisted family lost its cap"
    assert "--tp-cap-pct" not in uncapped, (
        "a family whose unit carries no _TP_SENTINEL_CAP_PCT was handed the cap")


def test_the_scalp_unit_really_has_no_cap_so_the_allowlist_is_not_arbitrary():
    """Anchored on the UNIT, not on the allowlist restating itself.

    If `ict_scalp` ever gains a `_TP_SENTINEL_CAP_PCT`, the allowlist is then
    wrong and this fails -- which is the direction that matters, because the
    silent outcome would be a scalp sweep measuring a geometry production no
    longer runs.
    """
    unit = REPO / "src" / "units" / "strategies" / "ict_scalp.py"
    assert unit.exists()
    mod = _sweep_module()
    has_cap = "_TP_SENTINEL_CAP_PCT" in unit.read_text()
    assert has_cap == ("scalp" in mod.LIVE_TP_CAPPED_FAMILIES), (
        "the scalp unit's cap and the sweep's allowlist disagree -- one of them "
        "moved and the sweep is now measuring the wrong geometry")


# ------------------------------------- the GRANT CAP (dN/N_b <= 1.0, Tier-3)
#
# `allowed = D_b x (dN/N_b)` is a FRACTION of the base book's whole drawdown and
# is unbounded above: 31 corpus rows are entitled to more than the entire base
# drawdown, the largest at 1.70x. The cap is structural -- the point where a
# share becomes an expansion -- not a fitted number.
#
# It is easy to misread, so the misreadings are what these tests pin.


def _rate(nb, db, dn, dd):
    mod = _sweep_module()
    return mod.drawdown_exchange_rate(
        {"net_total_r": nb + dn, "max_drawdown_r": db + dd},
        {"net_total_r": nb, "max_drawdown_r": db})


def test_the_cap_enters_the_decision_not_just_the_printed_allowance():
    """Clamping only the reported number would describe a policy code ignores.

    The case must be one the RATE test admits and only the CAP refuses --
    otherwise it proves nothing about the cap. Base 10R net / 10R dd (rate 1.0);
    cell 30R net / 25R dd, so the rate test passes (30/25 >= 10/10) while the ask
    of +15R exceeds the 10R the cap allows.

    My first draft of this test used dd=15 with dn=10, which fails the RATE
    (200 < 250) and would have passed while testing nothing.
    """
    out = _rate(nb=10.0, db=10.0, dn=20.0, dd=15.0)
    assert out["grant_ratio"] == 2.0
    assert out["allowed_d_max_dd_uncapped"] == 20.0
    assert out["allowed_d_max_dd"] == 10.0, "the cap is D_b"
    assert out["passes"] is False
    assert out["reason"] == "grant_exceeds_base_drawdown", (
        "a cap refusal must not read as a rate refusal -- they call for "
        "opposite follow-ups")


def test_a_capped_entitlement_is_not_a_rejection():
    """THE misreading. The cap binds the ENTITLEMENT; the ask is what decides.

    This is `tlt_pullback_1h trail4`'s real shape: ratio 1.70, so the
    entitlement is clamped -- and the cell asks for LESS drawdown than the base
    (it IMPROVES it), so it passes untouched. Reading `grant_capped: true` as
    "too risky" would reject a cell that reduced drawdown.
    """
    out = _rate(nb=20.8991, db=27.7805, dn=35.4716, dd=-0.6916)
    assert out["grant_capped"] is True
    assert out["allowed_d_max_dd_uncapped"] > out["allowed_d_max_dd"]
    assert out["allowed_d_max_dd"] == 27.7805, "the cap is D_b itself"
    assert out["passes"] is True, "a capped entitlement rejected a drawdown IMPROVEMENT"
    assert out["reason"] is None


def test_the_cap_is_prophylactic_on_the_measured_population():
    """Zero verdicts change on the corpus -- stated in code, not just in prose.

    Of 31 over-entitled corpus rows, none actually asks for more drawdown than
    D_b. If a future sweep makes this test's premise false, the cap has started
    binding real cells and that is a finding, not a regression.
    """
    # The five real capped rows, with their real asks. All must still pass.
    for nb, db, dn, dd in [
        (12.8, 15.3471, 13.8853, 0.778),      # eth_pullback_prop_2h decay_stall10_t1.8
        (20.8991, 27.7805, 35.4716, -0.6916),  # tlt_pullback_1h trail4
    ]:
        out = _rate(nb, db, dn, dd)
        assert out["grant_capped"] is True
        assert out["passes"] is True, f"the cap refused a real corpus row: {out}"


def test_a_row_exactly_at_the_cap_is_not_marked_capped():
    """Strict `<`: at the bound is not clamped, and the flag must not blur that."""
    out = _rate(nb=10.0, db=10.0, dn=10.0, dd=0.0)   # ratio exactly 1.0
    assert out["grant_ratio"] == 1.0
    assert out["grant_capped"] is False
    assert out["allowed_d_max_dd"] == out["allowed_d_max_dd_uncapped"]


def test_the_uncapped_entitlement_is_still_reported():
    """A reader must be able to see WHAT was clamped, not just the result.

    Reporting only the clamped value would hide the very thing that motivated
    the cap -- an entitlement 1.7x the base book's whole drawdown.
    """
    out = _rate(nb=10.0, db=10.0, dn=17.0, dd=-1.0)
    assert out["allowed_d_max_dd_uncapped"] == 17.0
    assert out["allowed_d_max_dd"] == 10.0
    assert out["grant_ratio"] == 1.7
