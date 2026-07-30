"""Squeeze-harness wiring in the regime debt matrix (BL-20260730-SQUEEZE-NO-HARNESS).

Why this file exists: the 2026-07-30 authored-cell re-audit found that
`squeeze_breakout_4h` — a **live** strategy carrying **four** authored regime cells —
classified as `unclassifiable`, so none of its live gates could be re-checked. The
harness that validated the strategy in the first place
(`scripts/backtest_squeeze.py`) already existed; it was simply never wired into
`classify()`.

Two properties are load-bearing here and both are regression-prone:

1. **The fidelity declaration must stay honest.** `backtest_squeeze.py` has no
   `--tp-r` flag, so wiring it in naively would silently declare a `tp_r`-carrying
   strategy `faithful` while ignoring its profit target. That is precisely the
   "green relative to a wrong scope" bug class this session exists to kill, so the
   omission is bounded by `_SQZ_TP_R_NONBINDING` and tested in both directions.

2. **An unsupported flag must degrade, not crash.** `backtest_squeeze.py` accepts no
   `--adx-*` flags. Passing them would fail the subprocess with "unrecognized
   arguments", which surfaces as a *harness error* — misattributing a missing
   capability as a broken run, the hardest kind of failure to read correctly.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "research"))

rdm = pytest.importorskip("regime_debt_matrix")


LIVE_SQUEEZE = {
    "model": None,
    "signal_prefixes": ["squeeze_breakout", "squeeze"],
    "enabled": True,
    "execution": "live",
    "timeframe": "4h",
    "symbols": ["BTCUSDT"],
    "bb_period": 20,
    "bb_std": 2.0,
    "kc_mult": 1.0,
    "atr_period": 14,
    "atr_stop_mult": 2.5,
    "trail_mult": 3.5,
    "tp_r": 50.0,
}


def _build(cfg, harness=None):
    return rdm.build_harness_cmd(
        "s", cfg, harness or rdm.classify(cfg),
        "/tmp/d.csv", "4h", "/tmp/e.jsonl", "/tmp/j.json")


class TestClassification:
    def test_squeeze_config_classifies_as_squeeze(self):
        assert rdm.classify(LIVE_SQUEEZE) == "squeeze"

    def test_the_real_live_config_is_not_unclassifiable(self):
        """The actual regression: this returned None, stranding 4 live cells."""
        import yaml
        cfg = yaml.safe_load(
            open(os.path.join(REPO, "config/strategies.yaml"))
        )["strategies"]["squeeze_breakout_4h"]
        assert rdm.classify(cfg) == "squeeze"

    def test_donchian_still_wins_over_squeeze(self):
        """Precedence matters: an already-measured strategy must not be silently
        re-routed to a different harness by adding squeeze detection."""
        cfg = dict(LIVE_SQUEEZE, donchian=20)
        assert rdm.classify(cfg) == "trend"

    def test_pullback_still_wins_over_squeeze(self):
        cfg = dict(LIVE_SQUEEZE, pullback_frac=0.5)
        assert rdm.classify(cfg) == "pullback"

    def test_partial_squeeze_params_do_not_classify(self):
        """kc_mult alone is not a squeeze — guessing a harness is worse than
        declaring the strategy unmeasurable."""
        assert rdm.classify({"kc_mult": 1.0, "symbols": ["X"]}) is None
        assert rdm.classify({"bb_period": 20, "symbols": ["X"]}) is None


class TestHarnessCommand:
    def test_invokes_the_squeeze_harness(self):
        argv, _, _ = _build(LIVE_SQUEEZE)
        assert any(a.endswith("scripts/backtest_squeeze.py") for a in argv)

    def test_passes_the_live_squeeze_geometry(self):
        argv, _, _ = _build(LIVE_SQUEEZE)
        for flag, val in (("--bb-period", "20"), ("--bb-std", "2.0"),
                          ("--kc-mult", "1.0")):
            assert flag in argv and argv[argv.index(flag) + 1] == val

    def test_the_harness_actually_declares_every_flag_we_pass(self):
        """The strongest check here, and it must never skip.

        A flag the harness does not define surfaces only as a subprocess failure at
        runtime — which the caller reports as `harness failed: ...`, i.e. a missing
        capability misread as a broken run. `backtest_squeeze.py` builds its parser
        inside `main()`, so rather than skip on the absence of a `build_parser()`
        seam, read the option strings the source actually declares. Static, but it
        checks the real thing.
        """
        import ast

        src = open(os.path.join(REPO, "scripts/backtest_squeeze.py"),
                   encoding="utf-8").read()
        declared: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        declared.add(arg.value)
        assert "--bb-period" in declared, "sanity: parser extraction found nothing"

        argv, _, _ = _build(LIVE_SQUEEZE)
        passed = {a for a in argv[2:] if a.startswith("--")}
        missing = sorted(passed - declared)
        assert not missing, (
            f"regime_debt_matrix passes flags backtest_squeeze.py does not declare: "
            f"{missing}")

    def test_lever_flags_are_forwarded_when_declared(self):
        argv, _, _ = _build(dict(LIVE_SQUEEZE, stale_exit_bars=12))
        assert "--stale-exit-bars" in argv
        assert argv[argv.index("--stale-exit-bars") + 1] == "12"


class TestFidelityIsHonest:
    def test_live_config_is_faithful(self):
        _, faithful, omitted = _build(LIVE_SQUEEZE)
        assert faithful and omitted == []

    def test_a_binding_tp_r_degrades_to_approximate(self):
        """THE load-bearing case. tp_r: 3 is a real profit target the harness does
        not model; declaring that faithful would author cells off a wrong model."""
        _, faithful, omitted = _build(dict(LIVE_SQUEEZE, tp_r=3.0))
        assert not faithful
        assert "tp_r" in omitted

    def test_the_nonbinding_threshold_is_the_boundary(self):
        below = _build(dict(LIVE_SQUEEZE, tp_r=rdm._SQZ_TP_R_NONBINDING - 0.1))
        at = _build(dict(LIVE_SQUEEZE, tp_r=rdm._SQZ_TP_R_NONBINDING))
        assert "tp_r" in below[2] and not below[1]
        assert at[2] == [] and at[1]

    def test_absent_tp_r_is_not_an_omission(self):
        cfg = {k: v for k, v in LIVE_SQUEEZE.items() if k != "tp_r"}
        _, faithful, omitted = _build(cfg)
        assert faithful and "tp_r" not in omitted

    def test_an_unknown_lever_degrades_to_approximate(self):
        _, faithful, omitted = _build(dict(LIVE_SQUEEZE, some_new_lever=1))
        assert not faithful and "some_new_lever" in omitted

    def test_exit_head_is_never_replayable(self):
        """Consistency with the trend/pullback contract."""
        assert "exit_head_model" in rdm._UNREPLAYABLE
        _, faithful, omitted = _build(dict(LIVE_SQUEEZE, exit_head_model="m"))
        assert not faithful and "exit_head_model" in omitted


class TestAdxDegradesRatherThanCrashes:
    def test_adx_flags_are_not_passed_to_the_squeeze_harness(self):
        argv, _, _ = _build(dict(LIVE_SQUEEZE, adx_min=15))
        assert "--adx-min" not in argv, "backtest_squeeze.py defines no --adx-min"
        assert "--adx-max" not in argv

    def test_adx_is_reported_as_an_omitted_lever(self):
        """It must not vanish: unsupported-and-silent is how a gate gets authored
        against a model that ignored a live filter."""
        _, faithful, omitted = _build(dict(LIVE_SQUEEZE, adx_min=15))
        assert not faithful and "adx_min" in omitted

    def test_adx_still_reaches_the_trend_harness(self):
        """The fix must not regress the harnesses that DO support adx."""
        cfg = {"symbols": ["BTCUSDT"], "donchian": 20, "adx_min": 15}
        argv, _, _ = _build(cfg)
        assert "--adx-min" in argv
