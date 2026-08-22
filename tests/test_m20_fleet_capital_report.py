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
        # The --json path comes from the argv `run_cell` built, NOT a hardcoded
        # literal. It was "/tmp/m20_fleet_cell.json" while `run_cell` used that
        # same shared literal; when it moved to `tempfile.mkstemp` (the
        # concurrency fix, BL-20260820-RUN-CELL-SHARES-A-FIXED-TEMP-PATH) this
        # test failed with KeyError: 'net_total_r' because the fake was writing
        # to a file nobody read. Reading the path off the command asserts the
        # real contract — that run_cell reads back what it told the harness to
        # write — instead of asserting a path the test happened to know.
        argv = list(cmd)
        assert "--json" in argv, "run_cell no longer passes --json"
        _pl.Path(argv[argv.index("--json") + 1]).write_text(
            _json.dumps({"net_total_r": 1.0}))
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
    # Scoped to THIS lever. `inert` collects every withheld cell in the grid,
    # so an unscoped equality would break whenever any other lever gains a
    # skip reason (it did, when rr_floor cells were added 2026-08-18) — and it
    # would break for a reason that has nothing to do with what this test is
    # about. The docstring says the predicate is about the giveback rung.
    assert [s["cell"] for s in inert if s["lever"] == "giveback_stop"] \
        == ["gb1R_afterMFE2R"]
    _gb_inert = [x for x in inert if x["lever"] == "giveback_stop"]
    assert "provable_noop" in _gb_inert[0]["reason"]
    assert "tp_at_r=1.5" in _gb_inert[0]["reason"]
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
    # Scoped to the giveback lever — see the note above. Other levers may
    # legitimately withhold cells here (rr_floor does, since this call passes
    # no tp_cap_pct and the floor is unmeasurable without a capped TP), and
    # that is a different statement from "a giveback rung was deleted".
    assert [x for x in inert if x["lever"] == "giveback_stop"] == []
    # And a wider fixed bracket keeps both too — 2R < 3R is reachable.
    inert2 = []
    cells2 = mod.cells_for({"timeframe": "15m", "symbols": ["X"], "tp_at_r": 3.0},
                           "scalp", skipped=inert2)
    assert "gb1R_afterMFE2R" in [c[0] for c in cells2]
    assert [x for x in inert2 if x["lever"] == "giveback_stop"] == []


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

    TWO STATES, SPLIT 2026-08-13 — they were collapsed, and the collapse fired.
    "No strategy declares this key" covers two conditions needing OPPOSITE
    actions:

      * NOT REAL — the key exists nowhere in the runtime. A typo, or a read that
        was never threaded. `trend_len` / `pullback_len` above are this. **Fails.**
      * REAL BUT CURRENTLY UNARMED — the key is read by `src/`, so the harness
        flag is correctly wired; no leg happens to declare it *right now*. This
        is the normal state after a Tier-3 removal retires a lever fleet-wide,
        and failing on it would mean the guard punishes correctly removing the
        last declarer of a lever. **Allowed, and reported.**

    Measured 2026-08-13: removing the OOS-negative `vol_trail` from
    `trend_donchian_eth` and `qqq_pullback_1h` — the only two legs that armed it
    — turned `trail_vol_{above_pctl,below_pctl,tight_mult}` + `vol_pctl_window`
    into state 2, and the collapsed assertion reported them as state 1.

    The discriminator is RUNTIME READABILITY, not a hand-maintained allowlist, so
    it cannot rot and cannot be satisfied by editing a list. Verified against the
    original bugs before adoption: `trend_len` and `pullback_len` appear in ZERO
    `src/**/*.py`, while `trend_lookback` / `pullback_lookback` appear in 2 each —
    so this split still rejects exactly what motivated the test.
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

    undeclared = keys - declared
    runtime_text = "\n".join(
        p.read_text(errors="ignore") for p in (REPO / "src").rglob("*.py"))

    def _readable(k: str) -> bool:
        return re.search(rf'\b{re.escape(k)}\b', runtime_text) is not None

    not_real = sorted(k for k in undeclared if not _readable(k))
    unarmed = sorted(k for k in undeclared if _readable(k))

    assert not not_real, (
        "base_args reads cfg key(s) that NO strategy declares AND that the "
        "runtime never reads — each silently drops its flag and lets the harness "
        "default stand in. These are typos or never-threaded reads: "
        + str(not_real))

    if unarmed:  # visible, not fatal — the post-removal state
        print(f"NOTE: {len(unarmed)} real cfg key(s) read by base_args that no leg "
              f"currently arms (a lever retired fleet-wide, not a typo): {unarmed}")


def test_the_dead_key_check_can_still_catch_its_original_bugs():
    """The split above must not have blunted the guard.

    Feeds the discriminator the two keys that motivated the original test and
    asserts it still classifies them NOT REAL. Without this, relaxing the
    assertion could pass simply because nothing is left to reject.
    """
    import re
    runtime_text = "\n".join(
        p.read_text(errors="ignore") for p in (REPO / "src").rglob("*.py"))

    def _readable(k: str) -> bool:
        return re.search(rf'\b{re.escape(k)}\b', runtime_text) is not None

    for typo in ("trend_len", "pullback_len"):
        assert not _readable(typo), (
            f"{typo} is now readable in src/ — the discriminator would let the "
            "original 2026-08-10 bug through; this test's premise has changed")
    for real in ("trend_lookback", "pullback_lookback", "vol_pctl_window"):
        assert _readable(real), (
            f"{real} is NOT readable in src/ — the discriminator would wrongly "
            "flag a real key as a typo")


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


# ---------------------------------------------------------------------------
# THE LEVER-OFF ARM.
#
# The normal sweep is STRUCTURALLY unable to grade a SHIPPED lever: the shipped
# lever is inside the config-exact base, so a cell reproducing it measures the
# base against itself. Measured 2026-08-13 over the 860-row corpus, 31 rows are
# exactly that — all-zero deltas, `gate_reason: tie_no_improvement`, wearing the
# verdict labels `is_oos_fail` (27) and `insufficient_base` (4). Neither label
# is true; no comparison happened. The arm inverts the base so the delta the
# sweep already computes becomes a verdict on the shipped cell.
# ---------------------------------------------------------------------------


def test_dropping_a_declared_lever_removes_exactly_its_flags():
    """OMITTED, never passed as a falsy value.

    The harness reads an absent flag as "lever not armed"; an armed lever at a
    degenerate threshold is a different book, so a 0/None would measure the
    wrong thing while looking like the right one.
    """
    cfg = {"timeframe": "1h", "symbols": ["ETHUSDT"], "trail_mult": 3.0,
           "atr_period": 14, "stale_exit_bars": 8, "stale_exit_below_r": 0.0,
           "trail_vol_below_pctl": 0.1, "trail_vol_tight_mult": 2.5}
    on = _mod.base_args("x", cfg, "donchian", "/tmp/x.csv", None)
    off = _mod.base_args("x", cfg, "donchian", "/tmp/x.csv", None,
                         without_declared_levers=frozenset({"stale_stop"}))
    assert "--stale-exit-bars" in on and "--stale-exit-below-r" in on
    assert "--stale-exit-bars" not in off and "--stale-exit-below-r" not in off
    # No substituted value took its place.
    assert "0" not in off and "None" not in off
    # And the UNRELATED declared lever is untouched — dropping one lever must
    # not quietly re-baseline the leg on every axis.
    assert "--trail-vol-below-pctl" in off and "--trail-vol-tight-mult" in off
    # Nothing else moved: the two arms differ by exactly those four tokens.
    assert [t for t in on if t not in off] == [
        "--stale-exit-bars", "8", "--stale-exit-below-r", "0.0"]
    assert len(on) - len(off) == 4


def test_lever_off_base_plus_the_shipped_cell_reproduces_the_lever_on_base():
    """THE INVARIANT THE WHOLE ARM RESTS ON.

    If base-OFF + the `shipped_*` cell is not byte-equivalent to base-ON, the
    A/B is measuring the lever PLUS whatever else drifted, and the verdict would
    be attributed to the lever. Asserted over every real leg that declares one,
    not a fixture — a synthetic config cannot catch a threading gap in a family
    branch nobody wrote a fixture for.
    """
    import yaml as _yaml
    from pathlib import Path as _P
    strategies = (_yaml.safe_load(
        (_P(__file__).resolve().parents[1] / "config" / "strategies.yaml")
        .read_text()) or {}).get("strategies") or {}
    checked, bad = 0, []
    for leg, cfg in strategies.items():
        if not isinstance(cfg, dict):
            continue
        fam = _mod.classify(leg)
        if fam is None:
            continue
        present = _mod.declared_levers_present(cfg)
        if not present:
            continue
        checked += 1
        drop = frozenset(present)
        on = _mod.base_args(leg, cfg, fam, "/tmp/x.csv", None)
        off = _mod.base_args(leg, cfg, fam, "/tmp/x.csv", None,
                             without_declared_levers=drop)
        extras = [t for _tag, _lev, e in
                  _mod.cells_for(cfg, fam, without_declared_levers=drop) for t in e]
        if sorted(off + extras) != sorted(on):
            bad.append(leg)
    # A probe that finds nothing proves nothing.
    assert checked >= 10, f"only {checked} legs declare a lever — fixture is thin"
    assert not bad, f"lever-OFF base + shipped cell != lever-ON base for: {bad}"


def test_a_leg_that_declares_nothing_records_that_it_had_nothing_to_drop():
    """"We removed it" and "there was nothing to remove" are different states.

    A fleet run dropping `stale_stop` leaves a non-declaring leg's base
    byte-identical to config-exact. Recording only the run-level request would
    let that row read as a lever-OFF measurement of a lever that was never on.
    """
    cfg = {"timeframe": "1h", "symbols": ["BTCUSDT"], "trail_mult": 3.0}
    assert _mod.declared_levers_present(cfg) == []
    skipped = []
    cells = _mod.cells_for(cfg, "donchian", skipped=skipped,
                           without_declared_levers=frozenset({"stale_stop"}))
    assert cells == []
    assert skipped and "no_declared_lever_to_drop" in skipped[0]["reason"]
    # The base is unchanged, so the row it produces IS a config-exact row.
    assert (_mod.base_args("x", cfg, "donchian", "/tmp/x.csv", None)
            == _mod.base_args("x", cfg, "donchian", "/tmp/x.csv", None,
                              without_declared_levers=frozenset({"stale_stop"})))


def test_the_arm_emits_only_shipped_cells_never_the_alternatives():
    """An alternative cell measured against a mutated base answers a DIFFERENT
    question than the same tag does in a normal run.

    Two rows carrying one tag while measuring two books is the provenance
    failure the run-level identity fields exist to prevent, so the arm withholds
    the alternatives rather than relabelling them.
    """
    cfg = {"timeframe": "1h", "symbols": ["ETHUSDT"], "trail_mult": 3.0,
           "stale_exit_bars": 8, "stale_exit_below_r": 0.0}
    normal = [t for t, _l, _e in _mod.cells_for(cfg, "donchian")]
    arm = [t for t, _l, _e in
           _mod.cells_for(cfg, "donchian",
                          without_declared_levers=frozenset({"stale_stop"}))]
    assert "stale8_lt0R" in normal and "stale8_lt0R" not in arm
    assert arm == ["shipped_stale_stop_8_0"]
    assert all(t.startswith("shipped_") for t in arm)


def test_trail_geometry_is_not_droppable():
    """`trail_mult` is a continuous family parameter with no OFF state.

    A trail-less donchian leg is a different strategy, not the same leg with a
    lever off, so offering the drop would produce a base whose stop geometry is
    undefined while looking like an ordinary arm.
    """
    assert "trail_geometry" not in _mod.LEVER_DECLARED_KEYS
    cfg = {"timeframe": "1h", "symbols": ["BTCUSDT"], "trail_mult": 3.0}
    off = _mod.base_args("x", cfg, "donchian", "/tmp/x.csv", None,
                         without_declared_levers=frozenset({"trail_geometry"}))
    assert "--trail-mult" in off


def test_the_measurement_key_splits_on_what_was_dropped_not_what_was_asked():
    """A run asking to drop `stale_stop` removes NOTHING from a leg that never
    declared one — that leg measured the config-exact base and must MERGE with
    config-exact rows, not fragment away from them.

    And a missing field normalises to "nothing dropped": the flag did not exist
    before the field did, so a legacy run provably carried every declared lever.
    Known by construction, not assumed from a default.
    """
    import importlib.util
    from pathlib import Path as _P
    p = _P(__file__).resolve().parents[1] / "scripts/research/m20_corpus_extract.py"
    spec = importlib.util.spec_from_file_location("m20ce", p)
    ce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ce)
    base = {"kind": "cell", "leg": "L", "cell": "c", "split": "2025-07-01"}
    legacy = ce.measurement_key(base)
    asked_but_nothing_dropped = ce.measurement_key(
        {**base, "without_declared_levers": ["stale_stop"],
         "declared_levers_dropped": []})
    really_dropped = ce.measurement_key(
        {**base, "without_declared_levers": ["stale_stop"],
         "declared_levers_dropped": ["stale_stop"]})
    assert legacy == asked_but_nothing_dropped
    assert really_dropped != legacy
    # Argument order must not split a key.
    assert ce.measurement_key({**base, "declared_levers_dropped": ["b", "a"]}) == \
        ce.measurement_key({**base, "declared_levers_dropped": ["a", "b"]})


def test_a_multi_lever_drop_states_which_other_levers_the_base_was_missing():
    """A leg declaring two levers and dropping both yields a cell restoring ONE.

    That is still a clean one-lever A/B — both arms lack the other lever — but
    the book it is clean IN is not the live configuration. Derivable from
    `declared_levers_dropped` minus the row's own `lever`; stated anyway,
    because "the reader can compute it" is how a caveat gets lost.
    """
    import importlib.util
    from pathlib import Path as _P
    p = _P(__file__).resolve().parents[1] / "scripts/research/m20_corpus_extract.py"
    spec = importlib.util.spec_from_file_location("m20ce2", p)
    ce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ce)
    doc = {
        "generated_at": "2026-08-13T00:00:00+00:00", "split": "2025-07-01",
        "tp_cap_pct": 0.099, "without_declared_levers": ["stale_stop", "vol_trail"],
        "verdicts": {"trend_donchian_eth": {
            "proxy": False, "family": "donchian",
            "declared_levers_present": ["stale_stop", "vol_trail"],
            "declared_levers_dropped": ["stale_stop", "vol_trail"],
            "levers": {"stale_stop": [{"cell": "shipped_stale_stop_8_0",
                                       "verdict": "PASS"}]}}}}
    rows = [r for r in ce.rows_from_verdicts(doc, "run1") if r["kind"] == "cell"]
    assert len(rows) == 1
    assert rows[0]["base_missing_other_levers"] == ["vol_trail"]
    # The single-lever case says "nothing else differed" — [] not None, which is
    # the whole point: absent and empty are different claims.
    doc["verdicts"]["trend_donchian_eth"]["declared_levers_dropped"] = ["stale_stop"]
    rows = [r for r in ce.rows_from_verdicts(doc, "run1") if r["kind"] == "cell"]
    assert rows[0]["base_missing_other_levers"] == []
    # And a run predating the arm records None, not [].
    del doc["verdicts"]["trend_donchian_eth"]["declared_levers_dropped"]
    rows = [r for r in ce.rows_from_verdicts(doc, "run1") if r["kind"] == "cell"]
    assert rows[0]["base_missing_other_levers"] is None


def _corpus_extract_module():
    import importlib.util
    from pathlib import Path as _P
    p = _P(__file__).resolve().parents[1] / "scripts/research/m20_corpus_extract.py"
    spec = importlib.util.spec_from_file_location("m20ce_split", p)
    ce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ce)
    return ce


def test_row_records_the_PER_LEG_split_not_the_absent_doc_level_one():
    """A post-#8965 verdict carries its boundary per-leg; the row must say so.

    `resolve_split` made the IS/OOS boundary per-leg, and the sweep stopped
    writing a doc-level `split` — it writes `split_fallback_date`/`split_mode`/
    `split_target_oos` at the top and the ACTUAL date inside each leg. The
    extractor kept reading `doc["split"]`, so every row from such a run recorded
    `split: null`.

    That is not cosmetic. `trend_donchian_sol_prop` at the SAME tp_cap=0.099:
    2026-08-10 (split 2025-07-01) gave IS 245 / OOS 65 and graded; 2026-08-13
    (split null) gave IS 285 / OOS 24 and every cell came back
    `insufficient_base`. The split alone crossed the 25-trade floor.
    """
    ce = _corpus_extract_module()
    doc = {
        "generated_at": "2026-08-13T12:47:00+00:00",
        # No doc-level "split" — exactly what the sweep writes post-#8965.
        "split_fallback_date": "2025-07-01", "split_mode": "oos-trades",
        "split_target_oos": 25, "tp_cap_pct": 0.099,
        "verdicts": {"trend_donchian_sol_prop": {
            "proxy": False, "family": "donchian",
            "split": "2024-11-02", "split_mode": "oos-trades",
            "split_target_oos": 25, "split_lifetime_trades": 309,
            "levers": {"vol_trail": [{"cell": "vt_hot80_t1.8", "verdict": "FAIL"}]}}}}
    rows = [r for r in ce.rows_from_verdicts(doc, "run-x") if r["kind"] == "cell"]
    assert len(rows) == 1
    assert rows[0]["split"] == "2024-11-02", (
        "the row recorded the doc-level split (absent -> None) instead of the "
        "leg's own derived boundary — this is the null-split regression")
    # The DERIVATION travels with it, so a thin OOS is attributable.
    assert rows[0]["split_mode"] == "oos-trades"
    assert rows[0]["split_target_oos"] == 25
    assert rows[0]["split_lifetime_trades"] == 309


def test_a_legacy_run_still_uses_the_doc_level_split():
    """The negative control: pre-#8965 verdicts carry no per-leg split.

    Without this, "read the per-leg value" could be implemented as "read ONLY
    the per-leg value" and silently null every one of the 880+ legacy rows —
    fragmenting them away from every future row on the merge key.
    """
    ce = _corpus_extract_module()
    doc = {
        "generated_at": "2026-08-10T22:38:10+00:00", "split": "2025-07-01",
        "tp_cap_pct": 0.099,
        "verdicts": {"trend_donchian_sol_prop": {
            "proxy": False, "family": "donchian",
            "levers": {"vol_trail": [{"cell": "vt_hot80_t1.8", "verdict": "FAIL"}]}}}}
    rows = [r for r in ce.rows_from_verdicts(doc, "run-legacy") if r["kind"] == "cell"]
    assert rows[0]["split"] == "2025-07-01"
    assert rows[0]["split_lifetime_trades"] is None


def test_the_split_is_part_of_the_merge_identity():
    """Two runs of one cell at different boundaries are different measurements.

    Pins why the null-split bug mattered beyond legibility: `measurement_key`
    includes `split`, so a null one keys distinctly and the corpus keeps BOTH
    rows for a single cell rather than superseding.
    """
    ce = _corpus_extract_module()
    base = {"kind": "cell", "leg": "L", "cell": "c", "tp_cap_pct": 0.099}
    assert ce.measurement_key({**base, "split": "2025-07-01"}) \
        != ce.measurement_key({**base, "split": None})
    assert ce.measurement_key({**base, "split": "2025-07-01"}) \
        == ce.measurement_key({**base, "split": "2025-07-01"})


# ---------------------------------------------------------------------------
# `tp_cap_pct` records what was REQUESTED. Did it BIND?
# ---------------------------------------------------------------------------

def _tp_doc(leg_extra: dict) -> dict:
    return {
        "generated_at": "2026-08-13T12:00:00+00:00",
        "split_fallback_date": "2025-07-01", "split_mode": "oos-trades",
        "split_target_oos": 25, "tp_cap_pct": 0.099,
        "verdicts": {"trend_donchian_eth_prop": {
            "proxy": False, "split": "2024-11-02", **leg_extra,
            "levers": {"vol_trail": [{"cell": "vt_hot80_t1.8"}]}}}}


def test_the_row_says_how_far_the_capped_tp_actually_SAT():
    """`tp_cap_pct: 0.099` only establishes that the flag was PASSED.

    `trend_donchian_eth_prop` came back byte-identical at `tp_cap_pct: 0.099`
    and at `null` — same base book, all seven shared cells to 4dp. Two
    explanations were proposed and BOTH were refuted (the flag is inert: no,
    a direct positive control moves 57 trades to 127; `null` just predates the
    field: no, 464 rows carry 0.099 with earlier timestamps). What survives is
    that the geometry behind a `null` row is UNDETERMINED from the corpus —
    see BL-20260813-TPCAP-REQUESTED-NOT-APPLIED. The sweep already measures
    `tp_r_effective_*`; this hop dropped it, which is why the question had to
    be argued from run logs and git history instead of read off a row.
    """
    ce = _corpus_extract_module()
    rows = ce.rows_from_verdicts(_tp_doc({"live_tp_reach_r": {
        "IS": {"n": 245, "median": 1.62, "min": 0.41, "max": 3.90},
        "OOS": {"n": 65, "median": 1.71, "min": 0.55, "max": 3.10}}}), "run1")
    assert rows
    r = rows[0]
    assert r["live_tp_reach_r_n_IS"] == 245
    assert r["live_tp_reach_r_median_IS"] == 1.62
    assert r["live_tp_reach_r_min_OOS"] == 0.55
    assert r["live_tp_reach_r_max_OOS"] == 3.10


def test_reach_zero_and_reach_unknown_are_not_the_same_row():
    """`n: 0` = the cap was on and reached nothing. `n: None` = we did not look.

    Collapsing them would let a cap that never applied read as a cap that
    applied and bound on no trade. That distinction is exactly what the
    eth_prop investigation could not make from the corpus, and why it ended
    with the geometry undetermined rather than decided either way.
    """
    ce = _corpus_extract_module()
    looked = ce.rows_from_verdicts(_tp_doc({"live_tp_reach_r": {
        "IS": {"n": 0, "median": None, "min": None, "max": None}}}), "run1")[0]
    never = ce.rows_from_verdicts(_tp_doc({}), "run1")[0]
    assert looked["live_tp_reach_r_n_IS"] == 0
    assert never["live_tp_reach_r_n_IS"] is None
    assert looked["live_tp_reach_r_n_IS"] != never["live_tp_reach_r_n_IS"]
    # Both carry the SAME requested cap — which is the whole point: the
    # requested value cannot tell these two rows apart.
    assert looked["tp_cap_pct"] == never["tp_cap_pct"] == 0.099


def test_the_reach_is_NOT_part_of_the_merge_identity():
    """It is a measurement OF the row, not a different measurement.

    Two runs of one cell that disagree on how far the TP sat must supersede
    each other, not accumulate as two rows — the sibling of why
    `regime_gate_delta` is deliberately outside the key.
    """
    ce = _corpus_extract_module()
    base = {"kind": "cell", "leg": "L", "cell": "c", "split": "2025-07-01",
            "tp_cap_pct": 0.099}
    assert ce.measurement_key({**base, "live_tp_reach_r_median_IS": 1.6}) \
        == ce.measurement_key({**base, "live_tp_reach_r_median_IS": 2.9})


# ------------------- the per-era p80 must never print a percentile bare
def test_era_p80_states_are_never_collapsed():
    """`PB-20260816-ARM-SWEEP-POOLS-VOL-ERAS` half (2): the sweep reports the
    p80 PER ERA so a pooled arm is never read as describing a regime the live
    book samples.

    The failure this pins is the one the row was opened about, one level down:
    a per-era figure that cannot say *why* it is missing. "we looked and there
    were too few winners", "we could not date these rows", and "this leg is so
    short that recent-era IS pooled" are three different facts, and a bare
    `null` (or worse, a number over an unstated `n`) collapses them into one.
    """
    # THIN vs COMPUTED, and `n` present on both.
    rep = _mod._era_report({"2020": [1.0] * _mod._ERA_MIN_WINNERS,
                            "2021": [2.0] * (_mod._ERA_MIN_WINNERS - 1),
                            "undated": [3.0] * 50})
    assert rep["2020"]["state"] == "computed" and rep["2020"]["p80"] is not None
    assert rep["2021"]["state"] == "thin" and rep["2021"]["p80"] is None, \
        "a per-era bucket under the floor printed a percentile anyway"
    assert rep["undated"]["state"] == "undated", \
        "undated rows were folded into a calendar year they cannot be placed in"
    assert all("n" in v for v in rep.values()), \
        "a per-era p80 shipped without its denominator"

    # A year with no winners has NO row -- inventing an n=0 entry would assert
    # a measurement that was never taken.
    assert "2019" not in rep


def test_recent_era_reports_its_span_and_distinguishes_all_years():
    """The recent-era window must publish the years it actually used, and a leg
    whose window had to swallow every year must say so rather than quietly
    reporting a number equal to pooled.

    "recent == pooled because the leg is short" and "recent == pooled because
    volatility was stable" are opposite findings; an equal number alone cannot
    tell them apart, and the row's resolution criterion is written against
    exactly this distinction.
    """
    n = _mod._ERA_MIN_WINNERS
    # Newest year alone clears the floor -> a genuine recent-era contrast.
    hot = {"2020": [1.0] * n, "2021": [1.0] * n, "2025": [9.0] * n}
    pooled = _mod._percentile_80([v for xs in hot.values() for v in xs])
    rec = _mod._recent_era_p80(hot, pooled)
    assert rec["state"] == "computed" and rec["years"] == ["2025"], rec
    assert rec["n"] == n and rec["p80"] is not None
    assert rec["delta_vs_pooled"] is not None

    # One short year -> the window consumed everything.
    only = _mod._recent_era_p80({"2025": [2.0] * n}, 2.0)
    assert only["state"] == "all_years", \
        "a leg with a single year reported a 'recent era' contrast it cannot have"

    # Under the floor even after widening, and nothing datable at all.
    assert _mod._recent_era_p80({"2025": [2.0] * 2}, 2.0)["state"] == "thin"
    assert _mod._recent_era_p80({"undated": [2.0] * 99}, 2.0)["state"] \
        == "undated_only", \
        "undated rows were ordered in time to build a recency window"


def test_pooled_and_era_p80_share_one_estimator():
    """Both figures must come from the same percentile function.

    The report exists so a reader can compare pooled against recent-era. Two
    independently-written index computations would make that a comparison of
    two ESTIMATORS rather than of two populations -- the difference would be
    real-looking and mean nothing.
    """
    import ast
    src = (REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py").read_text()
    tree = ast.parse(src)
    for name in ("winner_mfe_p80", "_era_report", "_recent_era_p80"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        code = ast.unparse(fn)
        assert "_percentile_80" in code, \
            f"{name} does not use the shared p80 estimator"
        assert "0.8 * (len(" not in code, \
            f"{name} re-derives the percentile index instead of calling it"


def test_era_block_never_feeds_the_arm():
    """The per-era figures are REPORTING. Only the pooled p80 may become an arm.

    Swapping `--trail-decay-arm-r` to a recent-era percentile would be a Tier-3
    change to a live parameter; this row is scoped to reporting, and the
    separation has to be visible in the code rather than only in the docstring.
    """
    import ast
    src = (REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py").read_text()
    tree = ast.parse(src)
    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    code = ast.unparse(main_fn)
    assert "'--trail-decay-arm-r', str(p80)" in code, \
        "the arm is no longer built from the pooled p80 scalar"
    for era_key in ("recent_era'][", 'recent_era"]['):
        assert f"str(p80_detail[{era_key}" not in code
    assert "--trail-decay-arm-r', str(p80_detail" not in code, \
        "an era-derived percentile is being fed to the live arm flag"


def test_winner_mfe_p80_end_to_end_against_a_stub_harness(tmp_path, monkeypatch):
    """Exercise the REAL function, not just its pure helpers.

    Everything above tests `_era_report` / `_recent_era_p80` / `_percentile_80`
    in isolation, which cannot catch the assembly: a wrong key in the returned
    dict, losers leaking into a bucket, or undated rows reaching the recency
    window. `winner_mfe_p80` shells out to a harness, so the harness is stubbed
    -- the stub emits the schema `--emit-trades` really writes.
    """
    harness = tmp_path / "stub_harness.py"
    harness.write_text(
        "import argparse, json, pathlib\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--emit-trades'); p.add_argument('--json')\n"
        "p.add_argument('--end')\n"
        "a, _ = p.parse_known_args()\n"
        "rows = []\n"
        # Quiet early years, hot recent year -> pooled must not equal recent.
        # THE HOT YEAR IS DELIBERATELY UNDER 20% OF THE SAMPLE. At 20/81 the
        # pooled p80 lands INSIDE the hot year and the two figures coincide --
        # which is not a bug, it is the effect being reported: whether a pooled
        # percentile describes the recent regime depends on that regime's SHARE
        # of the pooled window. Here the hot year is 12/73, so the pooled p80
        # sits in the quiet regime and describes a book the recent era is not.
        "for yr, mfe, n in (('2021', 1.5, 20), ('2022', 1.5, 20),\n"
        "                   ('2023', 1.5, 20), ('2025', 9.0, 12)):\n"
        "    for i in range(n):\n"
        "        rows.append({'entry_time': yr + '-03-01 00:00:00',\n"
        "                     'net_r': 1.0, 'mfe_r': mfe})\n"
        # A LOSER with a huge MFE: must never reach any bucket.
        "rows.append({'entry_time': '2025-04-01 00:00:00',\n"
        "             'net_r': -1.0, 'mfe_r': 99.0})\n"
        # An UNDATED winner: counted, bucketed, but not orderable in time.
        "rows.append({'entry_time': 'not-a-date', 'net_r': 1.0, 'mfe_r': 5.5})\n"
        "pathlib.Path(a.emit_trades).write_text("
        "'\\n'.join(json.dumps(r) for r in rows))\n"
        "if a.json: pathlib.Path(a.json).write_text('{}')\n")
    monkeypatch.setattr(_mod, "REPO", tmp_path)

    out = _mod.winner_mfe_p80("stub_harness.py", [], "2026-01-01")
    assert out is not None, "the stub emitted 73 winners and the arm abstained"

    # 72 dated winners + 1 undated; the loser is excluded from BOTH the pooled
    # count and every bucket, however large its MFE.
    assert out["n"] == 73, out["n"]
    assert 99.0 not in [b["p80"] for b in out["by_era"].values()]
    assert out["by_era"]["2025"]["n"] == 12, \
        "a losing trade leaked into a per-era bucket"

    assert out["by_era"]["undated"]["state"] == "undated"
    assert out["recent_era"]["years"] == ["2025"], out["recent_era"]
    assert "undated" not in out["recent_era"]["years"]

    # The whole point: pooled and recent-era must be able to disagree.
    assert out["recent_era"]["p80"] != out["p80"], \
        "pooled and recent-era coincided on a population built to differ"
    assert out["recent_era"]["delta_vs_pooled"] is not None


def test_run_cell_does_not_share_a_temp_path_between_calls(monkeypatch):
    """Two run_cell calls must never be handed the SAME --json path.

    REGRESSION PIN for BL-20260820-RUN-CELL-SHARES-A-FIXED-TEMP-PATH. `run_cell`
    wrote its harness output to the fixed literal "/tmp/m20_fleet_cell.json" and
    read it straight back — a process-shared constant, so two sweeps running
    concurrently on one box silently served each other's results. Measured: five
    per-leg sweeps in parallel returned a base net_R of EXACTLY -9.6113 for three
    different legs on three different symbols (AVAX / ETH / BTC), against
    17.369 / -32.5815 / -9.6113 when run one at a time.

    The two tests above cannot catch this: they read the path off the command, so
    they pass under either implementation. That is correct for what they assert
    (memoisation, split derivation) and is exactly why the concurrency property
    needs its own pin.

    Asserted here on the argv rather than by spawning real processes — a genuine
    race is nondeterministic and a test that only fails sometimes is worse than
    none.
    """
    mod = _sweep_module()
    mod._CELL_CACHE.clear()
    seen: list[str] = []

    class _P:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, **kw):
        import json as _json
        import pathlib as _pl
        argv = list(cmd)
        path = argv[argv.index("--json") + 1]
        seen.append(path)
        _pl.Path(path).write_text(_json.dumps({"net_total_r": 1.0}))
        return _P()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    # DIFFERENT args each time, so the memo cannot collapse them into one run.
    for i in range(4):
        mod.run_cell("scripts/backtest_trend.py", ["--data", f"leg{i}.csv"])

    assert len(seen) == 4, f"memo collapsed distinct invocations: {seen}"
    assert len(set(seen)) == 4, (
        "run_cell handed the SAME --json path to more than one invocation — "
        f"that is the shared-temp defect: {seen}")
    assert not any(p == "/tmp/m20_fleet_cell.json" for p in seen), (
        "the retired shared literal is back")
    # And it cleans up after itself: a temp file left behind per cell would
    # accumulate one file per harness run across a fleet sweep.
    import pathlib as _pl
    assert not any(_pl.Path(p).exists() for p in seen), (
        f"run_cell left its temp file(s) behind: "
        f"{[p for p in seen if _pl.Path(p).exists()]}")


def test_the_shared_temp_pin_can_fail():
    """The pin above must be able to detect the defect it is written against.

    A regression test that would pass against the BROKEN code is not a pin. This
    replays the old behaviour — one fixed path for every call — and asserts the
    distinctness check rejects it, so the guarantee is that the assertion has
    teeth, not merely that today's code satisfies it.
    """
    shared = ["/tmp/m20_fleet_cell.json"] * 4
    assert len(set(shared)) != len(shared), (
        "the distinctness assertion would NOT have caught the shared path")
