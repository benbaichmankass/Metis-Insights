"""The e35 base arm must carry a NON-BINDING bar-count exit.

BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES

WHY THIS TEST EXISTS. `scripts/backtest_{trend,pullback}.py` force-close every trade
at `min(entry_i + timeout_bars, n - 1)` (default 200; squeeze 48), and NO live unit for
those families implements a bar-count exit — `timeout_bars` is read only by
`fvg_range_15m.py` and `fade_breakout_4h.py`, each from its own `_DEFAULTS`. Production's
effective timeout is INFINITE. Measured over `docs/research/e35-bracket-corpus.jsonl`
(41 legs, 1,588 graded pairs) the default BINDS on 27.6% of pairs and 18 of 41 legs, so a
base arm left on the default is not live-parity and its deltas are measured against an
exit production does not have.

The fix is deliberately scoped to THIS SWEEP (operator decision, 2026-08-29): the
harnesses' own argparse defaults are untouched because 9 other entry points inherit them
and are unmeasured on this axis. These tests pin the narrow fix so it cannot be widened
OR lost silently.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts", "research"))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


E35 = _load("_e35_sweep_for_tests",
            "scripts/research/e35_bracket_geometry_sweep.py")


def test_sentinel_is_not_zero_and_not_a_tape_constant():
    """0 means *exit on the entry bar*, silently — never "no timeout"."""
    assert E35.NO_BAR_COUNT_EXIT > 0
    # 10_000 is tests/trend_harness_engine.RIDE_TO_TAPE_END, sized for a ~50-bar
    # synthetic tape. A 5-year 15m series is ~175,000 bars, so it would BIND here.
    assert E35.NO_BAR_COUNT_EXIT > 1_000_000


def test_base_arm_pins_exactly_one_timeout_flag():
    pinned = E35.cell_args(["--data", "d", "--tp-r", "50.0"],
                           None, None, E35.NO_BAR_COUNT_EXIT)
    assert pinned.count("--timeout-bars") == 1
    assert pinned[pinned.index("--timeout-bars") + 1] == str(E35.NO_BAR_COUNT_EXIT)


def test_a_config_supplied_timeout_is_replaced_never_duplicated():
    """`fleet.base_args` emits `--timeout-bars` on its squeeze/fvg branches, so the
    pin must STRIP before appending. Two contradictory values in one recorded
    command cannot be read back as a claim about either."""
    repinned = E35.cell_args(["--data", "d", "--timeout-bars", "48"],
                             None, None, E35.NO_BAR_COUNT_EXIT)
    assert repinned.count("--timeout-bars") == 1
    assert repinned[repinned.index("--timeout-bars") + 1] == str(E35.NO_BAR_COUNT_EXIT)


def test_pinned_base_reads_back_as_base_args_not_harness_default():
    pinned = E35.cell_args(["--data", "d"], None, None, E35.NO_BAR_COUNT_EXIT)
    geo = E35.base_geometry("scripts/backtest_trend.py", pinned)
    assert geo["timeout"] == E35.NO_BAR_COUNT_EXIT
    assert geo["timeout_source"] == "base_args"


def test_every_grid_point_now_moves_the_timeout_axis():
    """With a non-binding base every grid point is a real TIGHTENING, so none can
    be mislabelled `axis: none` (a provable no-op) the way a grid point equal to
    the base would be."""
    pinned = E35.cell_args(["--data", "d"], None, None, E35.NO_BAR_COUNT_EXIT)
    geo = E35.base_geometry("scripts/backtest_trend.py", pinned)
    for g in E35.TIMEOUT_GRID:
        assert E35.axis_of(None, None, g, geo) == "timeout", g


def test_harness_defaults_are_deliberately_untouched():
    """The narrow scope IS the decision. If someone later flips these defaults,
    this test should fail and send them to the 9 unmeasured consumers first."""
    import re
    for rel, expected in (("scripts/backtest_trend.py", "200"),
                          ("scripts/backtest_pullback.py", "200"),
                          ("scripts/backtest_squeeze.py", "48")):
        src = open(os.path.join(_REPO, rel), encoding="utf-8").read()
        m = re.search(r'add_argument\(\s*"--timeout-bars",\s*type=int,\s*'
                      r'default=(\d+)\)', src)
        assert m is not None, f"{rel}: could not read the --timeout-bars default"
        assert m.group(1) == expected, (
            f"{rel} default moved to {m.group(1)}. That is NOT a local change: 9 "
            "entry points inherit it (strategy_tune_sweep, exit_head_replay, "
            "m23_phase1_experiment, m20_trail_resweep, trend_harness_divergence, "
            "build_continuous_contract, recombination_sweep, m15_phase0_sweep, "
            "m15_ws_c_kfold) and are UNMEASURED on this axis. See WORKPLAN D1.")


def test_the_two_dead_config_keys_stay_deleted():
    """`mgc_pullback_1d` / `mhg_pullback_1d` carried `timeout_bars: 200` that
    nothing read — not the live unit, not the pullback branch of base_args. A key
    with no reader in the LIVE trading config is indistinguishable from a real
    parameter, which is why it was deleted (operator-approved 2026-08-29)."""
    import yaml
    cfg = yaml.safe_load(
        open(os.path.join(_REPO, "config", "strategies.yaml"), encoding="utf-8")
    )["strategies"]
    for leg in ("mgc_pullback_1d", "mhg_pullback_1d"):
        assert "timeout_bars" not in cfg[leg], (
            f"{leg} re-declares timeout_bars. The pullback unit has no reader for "
            "it; re-add only alongside a real consumer.")
