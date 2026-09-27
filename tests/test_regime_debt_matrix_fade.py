"""Fade harness wiring in the regime debt matrix (checklist row E39).

Why this file exists: `classify()` had four branches (trend / pullback /
squeeze / fvg_range / ict_scalp) and NONE of them was fade. `fade_breakout_4h`
declares `donchian` (the channel it fades pierces — the same structural key
`trend_donchian` legs use), so `"donchian" in cfg` caught it FIRST and every
"fade_breakout_4h" evidence record ever built measured
`scripts/backtest_trend.py`'s breakout-CONTINUATION entry against fade's
stop/trail/timeout params, not `scripts/backtest_fade.py`'s structurally
OPPOSITE failed-breakout-REVERSION entry the live unit
(`src/units/strategies/fade_breakout_4h.py`) actually trades. Unlike the
`fvg_range`/`ict_scalp`/squeeze precedents (`BL-20260730-SQUEEZE-NO-HARNESS`),
this was not an honest `no_harness` refusal — `classify()` never returned
`None` for it, so the corpus carried a confidently wrong `measured` row
instead of a gap. MEASURED 2026-09-25, before vs after this fix, same window
(365d, 4 folds), net of the full cost stack: `net_r_oos` **+3.66 -> -16.09**,
`folds_positive` 3/4 -> 1/4, `fidelity` approximate (3 omitted levers) ->
faithful (0).

Two properties are load-bearing and regression-prone, same shape as the
fvg_range/squeeze precedents:

1. **The discriminator must not re-route an already-measured strategy.**
   `pierce_min` is carried by exactly one leg fleet-wide (checked below,
   anchored on the live roster) and is checked BEFORE the `donchian` branch,
   so every existing `trend_donchian*` leg keeps its harness unchanged.
2. **The fidelity declaration must stay honest and the unsupported-flag
   degrade must not crash.** `backtest_fade.py`'s stop is `--atr-stop-buffer`,
   not `--atr-stop-mult` — reusing the shared `common` argv would abort the
   subprocess. It also has no `--adx-min`.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "research"))

rdm = pytest.importorskip("regime_debt_matrix")


# The live bybit_2 config as of 2026-09-25, copied verbatim so a config change
# shows up as a diff here as well as in the anchored tests below.
LIVE_FADE = {
    "model": None,
    "signal_prefixes": ["fade_breakout", "fade"],
    "enabled": True,
    "execution": "shadow",
    "timeframe": "4h",
    "symbols": ["BTCUSDT"],
    "donchian": 20,
    "atr_period": 14,
    "atr_stop_buffer": 0.5,
    "pierce_min": 0.0,
    "trail_mult": 3.5,
    "adx_max": 20.0,
    "adx_period": 14,
    "tp_r": 50.0,
    "timeout_bars": 48,
}


def _build(cfg, harness=None):
    return rdm.build_harness_cmd(
        "s", cfg, harness or rdm.classify(cfg),
        "/tmp/d.csv", "4h", "/tmp/e.jsonl", "/tmp/j.json")


def _declared_flags(path: str) -> set[str]:
    """The option strings a harness's parser actually declares — reads the
    real source rather than trusting a docstring, since the harnesses build
    their parsers inside `main()` with no importable `build_parser()` seam."""
    src = open(os.path.join(REPO, path), encoding="utf-8").read()
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
    return out


def _live_strategies() -> dict:
    return yaml.safe_load(
        open(os.path.join(REPO, "config/strategies.yaml"))
    )["strategies"]


class TestClassification:
    def test_fade_config_classifies_as_fade(self):
        assert rdm.classify(LIVE_FADE) == "fade"

    def test_the_real_live_config_is_not_misrouted_to_trend(self):
        """The actual regression: this returned "trend", so a real-money-held
        leg's Stage-0 evidence measured the wrong strategy's entry direction."""
        cfg = _live_strategies()["fade_breakout_4h"]
        assert rdm.classify(cfg) == "fade"

    def test_donchian_trend_legs_are_unaffected(self):
        """Precedence: adding the fade branch must not silently re-route an
        already-measured trend_donchian leg."""
        assert rdm.classify(_live_strategies()["trend_donchian"]) == "trend"

    def test_pierce_min_alone_is_sufficient(self):
        assert rdm.classify({"pierce_min": 0.0, "symbols": ["X"]}) == "fade"

    def test_the_signature_key_is_unique_to_this_family(self):
        """The discriminator is only safe while no OTHER leg carries it.
        Anchored on the real roster so a future config that collides surfaces
        here rather than by being silently re-routed."""
        S = _live_strategies()
        hits = [n for n, c in S.items() if isinstance(c, dict) and "pierce_min" in c]
        assert hits == ["fade_breakout_4h"], f"signature key now collides: {hits}"


class TestHarnessCommand:
    def test_invokes_the_fade_harness(self):
        argv, _, _ = _build(LIVE_FADE)
        assert any(a.endswith("scripts/backtest_fade.py") for a in argv)

    def test_passes_the_live_fade_geometry(self):
        argv, _, _ = _build(LIVE_FADE)
        for flag, val in (("--donchian", "20"), ("--atr-stop-buffer", "0.5"),
                          ("--pierce-min", "0.0"), ("--trail-mult", "3.5"),
                          ("--adx-max", "20.0"), ("--timeout-bars", "48")):
            assert flag in argv and argv[argv.index(flag) + 1] == val

    def test_the_harness_actually_declares_every_flag_we_pass(self):
        """The strongest check here, and it must never skip. A flag the harness
        does not define surfaces only as a subprocess failure at runtime."""
        declared = _declared_flags("scripts/backtest_fade.py")
        assert "--pierce-min" in declared, "sanity: parser extraction found nothing"
        argv, _, _ = _build(LIVE_FADE)
        passed = {a for a in argv[2:] if a.startswith("--")}
        missing = sorted(passed - declared)
        assert not missing, (
            "regime_debt_matrix passes flags backtest_fade.py does not "
            f"declare: {missing}")

    def test_the_shared_common_atr_stop_mult_is_not_passed(self):
        """`common` carries --atr-stop-mult, which this harness does not
        accept (its stop is --atr-stop-buffer). Reusing it would abort the
        subprocess and read as a broken harness rather than a missing flag."""
        argv, _, _ = _build(LIVE_FADE)
        assert "--atr-stop-mult" not in argv

    def test_tp_r_models_the_live_capped_tp(self):
        """E55-style: --tp-cap-pct/--tp-r are one flag pair."""
        argv, _, _ = _build(LIVE_FADE)
        assert "--tp-cap-pct" in argv and "--tp-r" in argv
        assert argv[argv.index("--tp-r") + 1] == "50.0"


class TestFidelityIsHonest:
    def test_live_config_is_faithful(self):
        _, faithful, omitted = _build(LIVE_FADE)
        assert faithful and omitted == []

    def test_adx_max_is_modelled_but_adx_min_is_not(self):
        """backtest_fade.py declares --adx-max and no --adx-min. Claiming
        faithful on an adx_min-carrying leg would assert a floor the harness
        never applies."""
        declared = _declared_flags("scripts/backtest_fade.py")
        assert "--adx-max" in declared and "--adx-min" not in declared
        argv, faithful, omitted = _build(dict(LIVE_FADE, adx_min=15))
        assert "--adx-min" not in argv
        assert not faithful and "adx_min" in omitted

    def test_an_unknown_lever_degrades_to_approximate(self):
        _, faithful, omitted = _build(dict(LIVE_FADE, some_new_lever=1))
        assert not faithful and "some_new_lever" in omitted

    def test_exit_head_is_reported_as_omitted(self):
        """Consistency with the trend/pullback/squeeze/fvg_range contract."""
        _, faithful, omitted = _build(dict(LIVE_FADE, exit_head_model="m"))
        assert not faithful and "exit_head_model" in omitted
