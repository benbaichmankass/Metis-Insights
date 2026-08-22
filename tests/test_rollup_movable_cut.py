"""The roll-up's "movable by a session" count is MEASURED, not implied by a bucket name.

`BL-20260817-ROLLUP-CALLS-A-CELL-MOVABLE-WHEN-NO-SWEEP-PATH-EXISTS`.

`gate_partition` keys on each cell's STATUS STRING. That is the right call — it
cannot drift from the matrix — and it is also blind to whether the remedy the
status implies is POSSIBLE. A `pending` cell printed as *"no sweep has been run"*
invites *"so run it"*, and for `exit_ladder` / `regime_flip_exit` there is no run
to make: no arm in the fleet sweep, no flag in either harness.

Measured 2026-08-17: **all four** cells in the two "movable" buckets rested on
those two levers, so the honest movable count was **ZERO** while the roll-up's
text implied four. That number had already reached three operator pings, several
board comments, and a scheduled check-in.

WHAT EACH TEST IS FOR — three of these pin the failure mode rather than the
happy path, and two deliberately re-derive from the source files instead of
trusting the constant:

1. `test_no_sweep_arm_exists_for_either_lever` — reads `m20_fleet_exit_sweep.py`
   and asserts each lever appears at most once (its own absence comment). If
   someone adds a real arm, this fails and the constant must shrink. That is the
   point: the constant is a claim about other files, so the test checks them.
2. `test_no_harness_flag_exists_for_either_lever` — AST over both harnesses. A
   grep would match a docstring; `add_argument` is the surface that decides.
3. `test_exit_head_ml_is_not_in_the_set` — it HAS its own driver and IS swept, so
   its cells are gated on trade counts (`arithmetic`/`accrual`). Folding it in
   here would relabel a measurable-but-thin lever as unmeasurable.
4. `test_the_two_cuts_partition_the_two_buckets` — the invariant that survives
   cells resolving: `movable + no_sweep_path` is exactly
   `harness_gap + never_attempted`, with no double-count and nothing dropped.
5. `test_internal_keys_are_not_printed_as_buckets` — the cut must not render as a
   gate kind, which would double-count the total in the human report.
6. `test_the_cut_is_NOT_inside_the_partition` — the lesson from a real CI failure.
   The first draft returned the cut INSIDE `gate_partition`'s dict, which is a
   STRICT PARTITION reconciling to `cells_to_done`; that double-counted 4 cells
   (total read 26 vs a true 22) and broke four pre-existing tests in
   `tests/test_gate_partition.py` — the file named after the function being
   changed, which I had not run. A cross-cut and a partition are different shapes.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROLLUP = REPO / "scripts" / "research" / "m20_coverage_rollup.py"
SWEEP = REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py"
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"
HARNESSES = ("scripts/backtest_trend.py", "scripts/backtest_squeeze.py")


def _mod():
    spec = importlib.util.spec_from_file_location("_rollup_probe", ROLLUP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_rollup_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


def _partition():
    """The partition via the SAME inputs `rollup()` uses.

    ⚠️ No `hasattr` fallback here, deliberately. An earlier draft did
    `m.reachability(matrix) if hasattr(m, "reachability") else []` — and
    `reachability` does not exist, so every run silently passed `[]`, leaving
    `arith_legs` empty and the arithmetic reclassification un-exercised. The
    tests still passed. A fallback that turns a wrong name into an empty input
    is a green that checked a different function than the one it names; the real
    entry point is `fold_reachability`, and if it is ever renamed this raises
    instead of quietly degrading.
    """
    m = _mod()
    matrix = json.loads(MATRIX.read_text())
    part = m.gate_partition(matrix, m.fold_reachability(matrix))
    return m, part, m.movable_cut(part)


def _argparse_flags(rel: str) -> set[str]:
    tree = ast.parse((REPO / rel).read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.startswith("--"):
                    out.add(arg.value)
    return out


def test_no_sweep_arm_exists_for_either_lever():
    """The constant is a claim about the SWEEP, so check the sweep.

    Each lever may appear at most once — in the comment stating it is absent
    from `LEVER_DECLARED_KEYS`. A second mention means an arm may now exist and
    the cell really is runnable.
    """
    src = SWEEP.read_text()
    for lever in _mod().LEVERS_WITHOUT_A_SWEEP_PATH:
        n = len(re.findall(r"\b" + re.escape(lever) + r"\b", src))
        assert n <= 1, (
            f"{lever} now appears {n}x in m20_fleet_exit_sweep.py — if a real "
            f"sweep arm was added, remove it from LEVERS_WITHOUT_A_SWEEP_PATH")


def test_no_harness_flag_exists_for_either_lever():
    """AST, not grep — `add_argument` is the surface a run actually uses."""
    flags = set()
    for rel in HARNESSES:
        flags |= _argparse_flags(rel)
    # Sanity: the probe must be able to SEE flags at all, or its silence is empty.
    assert "--trail-mult" in flags, "AST probe found no known flag — probe is blind"
    for stem in ("--exit-ladder", "--regime-flip"):
        offenders = {f for f in flags if f.startswith(stem)}
        assert not offenders, f"a harness now exposes {offenders}"


def test_exit_head_ml_is_not_in_the_set():
    """It has its own driver and IS swept; its gate is trade counts, not absence."""
    assert "exit_head_ml" not in _mod().LEVERS_WITHOUT_A_SWEEP_PATH


def test_every_no_sweep_path_cell_rests_on_a_declared_lever():
    m, _part, cut = _partition()
    for ident in cut.get("no_sweep_path", []):
        assert ident[3] in m.LEVERS_WITHOUT_A_SWEEP_PATH, ident


def test_the_two_cuts_partition_the_two_buckets():
    """Nothing dropped, nothing double-counted — the invariant that outlives today."""
    _, part, cut = _partition()
    buckets = sorted(part.get("harness_gap", []) + part.get("never_attempted", []))
    cuts = sorted(cut.get("movable", []) + cut.get("no_sweep_path", []))
    assert cuts == buckets
    assert not (set(cut.get("movable", [])) & set(cut.get("no_sweep_path", [])))


def test_measured_state_2026_08_17():
    """The observation that motivated this, as a dated regression anchor.

    ⚠️ This one is EXPECTED to change as cells resolve — a future session that
    makes a lever sweepable should update it and say so. It is here because the
    whole finding is that the count was 4 when it should have read 0, and a test
    that only checked invariants would not have caught that.
    """
    _, _part, cut = _partition()
    # RESTATED 2026-08-20, and the docstring above says to say so.
    #
    # `movable` moved 0 -> 7 for a legitimate reason: the `bracket_geometry`
    # column was added, it HAS a sweep producer of its own
    # (`scripts/research/e35_bracket_geometry_sweep.py`), and exactly 7 legs are
    # pending against it — all `ict_scalp`, all crypto at 5m/15m, where the free
    # lane genuinely serves candles and `m20_fleet_exit_sweep.classify` returns
    # `scalp` with a real SCALP_HARNESS. A session can move them, so `movable` is
    # the honest bucket and the count is real work, not bookkeeping.
    #
    # The first version of that column made this read 10. All three of the
    # excess were MY errors, caught here rather than by review:
    # `ict_scalp_mgc_15m` was `pending` while every other MGC/MES/MHG row said
    # `blocked:no_free_lane_candle_feed` (MGC is an IBKR COMEX future the free
    # lane cannot serve); `sol_pullback_2h` and `trend_donchian_sol_4h` were
    # `pending` while the results doc's own per-leg table lists both as SWEPT
    # with a gate-passing cell and no dispersion band. All three corrected; the
    # class is
    # BL-20260820-BRACKET-GEOMETRY-COLUMN-SHIPPED-THREE-MISGRADED-ROWS.
    #
    # `no_sweep_path` is UNCHANGED at 4 — `bracket_geometry` has a driver, so it
    # never belonged in that bucket, and the fact that this number did not move
    # is the check that the new column was classified rather than just absorbed.
    assert len(cut.get("movable", [])) == 7
    assert {i[3] for i in cut["movable"]} == {"bracket_geometry"}
    assert len(cut.get("no_sweep_path", [])) == 4
    assert {i[3] for i in cut["no_sweep_path"]} == {"exit_ladder", "regime_flip_exit"}


def test_internal_keys_are_not_printed_as_buckets():
    """A leading-underscore key must not surface as a gate kind in the report.

    ⚠️ An earlier draft guarded this with `if hasattr(m, "report")` and the
    function is called `render` — so `text` was `""`, the `if` never ran, and the
    test PASSED while asserting nothing. Calling `render` unguarded is the point:
    the renderer is what a human reads, and it is where the misleading count was.
    """
    m = _mod()
    text = m.render(m.rollup(json.loads(MATRIX.read_text())))
    assert text, "render() produced nothing — the probe is blind"
    # The cut must not be rendered as gate-kind rows...
    assert not re.search(r"^\s+\d+\s+_?movable\b", text, re.M)
    assert not re.search(r"^\s+\d+\s+_?no_sweep_path\b", text, re.M)
    # ...while the measured count IS rendered, and reads 0 rather than 4.
    assert "MOVABLE BY A SESSION: 7" in text
    assert "NO SWEEP PATH AT ALL: 4" in text


def test_the_cut_is_NOT_inside_the_partition():
    """The lesson from breaking four pre-existing tests, pinned so it cannot recur.

    `gate_partition` is a STRICT PARTITION of the done-condition:
    `tests/test_gate_partition.py` asserts it reconciles to `cells_to_done` and
    that no cell sits in two buckets. `movable_cut` is an OVERLAPPING VIEW of two
    of its buckets. An earlier draft returned the cut INSIDE the partition dict,
    which double-counted 4 cells and made the total read 26 against a true 22 --
    caught in CI by tests named after the very function being changed, which I
    had not run. A cross-cut and a partition are different shapes.
    """
    m, part, cut = _partition()
    assert set(part) <= {"arithmetic", "accrual", "data", "harness_gap",
                         "never_attempted", "unclassified"}, sorted(part)
    for leaked in ("movable", "no_sweep_path", "_movable", "_no_sweep_path"):
        assert leaked not in part, f"{leaked} leaked into the partition dict"
    # And the cut is reachable from rollup() under its own top-level key.
    r = m.rollup(json.loads(MATRIX.read_text()))
    assert "movable_cut" in r
    assert r["movable_cut"] == cut
    # The partition still reconciles to the done-condition, unchanged by the cut.
    assert sum(len(v) for v in r["gate_partition"].values()) == r["cells_to_done"]
