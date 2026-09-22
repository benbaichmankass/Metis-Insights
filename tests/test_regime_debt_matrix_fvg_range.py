"""FVG-range harness wiring in the regime debt matrix (checklist row E25).

Why this file exists: `fvg_range_15m` sits on **bybit_2**, a real-money account,
and was the only one of the four legs failing B1's four-clause evidence bar that
failed **all four clauses** — because nothing had ever measured it.
`scripts/backtest_fvg_range.py` existed and had already been cost-wired (E3); it
was simply never reachable, because `classify()` had three branches and none of
them matched a range mean-reversion config. `build_strategy_evidence.py` said so
in its own `no_harness` error text: *"a missing classifier branch plus a lever
map, not absent infrastructure."*

This is the squeeze wiring (`test_regime_debt_matrix_squeeze.py`,
BL-20260730-SQUEEZE-NO-HARNESS) one family along, and the same two properties are
load-bearing and regression-prone:

1. **The fidelity declaration must stay honest.** `backtest_fvg_range.py` has no
   `--adx-min`, and its `--tp-r` binds only under `--exit-style tp1r`. Wiring
   either in naively would declare a leg `faithful` while the harness ignored a
   live filter or a live target.
2. **An unsupported flag must degrade, not crash.** The shared `common` argv list
   hard-codes `--atr-stop-mult`/`--trail-mult`, which this harness does not
   accept; passing them aborts the subprocess, which the caller reports as
   `harness failed` — a missing capability misread as a broken run.

Plus one this family added: the feed-interval maps `.get(timeframe, <daily>)`,
so a 15m leg silently fetched DAILY bars. Nothing reached that path while only
1h/2h/4h/1d legs routed. Routing a 15m family makes it reachable.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "research"))

rdm = pytest.importorskip("regime_debt_matrix")


# The live bybit_2 config as of 2026-09-22, copied verbatim so a config change
# shows up as a diff here as well as in the anchored tests below.
LIVE_FVG = {
    "model": None,
    "signal_prefixes": ["fvg_range", "fvg_mr"],
    "enabled": True,
    "execution": "shadow",
    "timeframe": "15m",
    "symbols": ["BTCUSDT"],
    "range_lookback": 48,
    "atr_period": 14,
    "adx_period": 14,
    "adx_max": 20.0,
    "min_width_pct": 0.015,
    "max_width_pct": 0.12,
    "touch_tol_pct": 0.002,
    "min_touches": 4,
    "third_frac": 0.34,
    "fvg_search": 24,
    "min_fvg_size_bps": 2.0,
    "atr_stop_buffer": 0.25,
    "timeout_bars": 48,
    "min_confidence": 0.0,
}


def _build(cfg, harness=None):
    return rdm.build_harness_cmd(
        "s", cfg, harness or rdm.classify(cfg),
        "/tmp/d.csv", "15m", "/tmp/e.jsonl", "/tmp/j.json")


def _declared_flags(path: str) -> set[str]:
    """The option strings a harness's parser actually declares.

    Static, but it reads the real source rather than trusting a docstring — the
    harnesses build their parsers inside `main()`, so there is no importable
    `build_parser()` seam to interrogate.
    """
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


class TestClassification:
    def test_fvg_config_classifies_as_fvg_range(self):
        assert rdm.classify(LIVE_FVG) == "fvg_range"

    def test_the_real_live_config_is_not_unclassifiable(self):
        """The actual regression: this returned None, so a real-money leg had no
        number at all."""
        import yaml
        cfg = yaml.safe_load(
            open(os.path.join(REPO, "config/strategies.yaml"))
        )["strategies"]["fvg_range_15m"]
        assert rdm.classify(cfg) == "fvg_range"

    def test_donchian_still_wins_over_fvg_range(self):
        """Precedence: adding a fourth branch must not silently re-route an
        already-measured strategy to a different harness."""
        assert rdm.classify(dict(LIVE_FVG, donchian=20)) == "trend"

    def test_pullback_still_wins_over_fvg_range(self):
        assert rdm.classify(dict(LIVE_FVG, pullback_frac=0.5)) == "pullback"

    def test_squeeze_still_wins_over_fvg_range(self):
        assert rdm.classify(dict(LIVE_FVG, kc_mult=1.0, bb_period=20)) == "squeeze"

    def test_partial_fvg_params_do_not_classify(self):
        """Either key alone is not a range strategy — guessing a harness is worse
        than declaring the leg unmeasurable."""
        assert rdm.classify({"range_lookback": 48, "symbols": ["X"]}) is None
        assert rdm.classify({"third_frac": 0.34, "symbols": ["X"]}) is None

    def test_the_signature_keys_are_unique_to_this_family(self):
        """The conjunction is only safe while no OTHER leg carries both keys.
        Anchored on the real roster so a future config that collides surfaces
        here rather than by being silently re-routed."""
        import yaml
        S = yaml.safe_load(
            open(os.path.join(REPO, "config/strategies.yaml"))
        )["strategies"]
        hits = [n for n, c in S.items() if isinstance(c, dict)
                and "range_lookback" in c and "third_frac" in c]
        assert hits == ["fvg_range_15m"], f"signature keys now collide: {hits}"


class TestHarnessCommand:
    def test_invokes_the_fvg_harness(self):
        argv, _, _ = _build(LIVE_FVG)
        assert any(a.endswith("scripts/backtest_fvg_range.py") for a in argv)

    def test_passes_the_live_range_geometry(self):
        argv, _, _ = _build(LIVE_FVG)
        for flag, val in (("--range-lookback", "48"), ("--third-frac", "0.34"),
                          ("--min-touches", "4"), ("--atr-stop-buffer", "0.25"),
                          ("--fvg-search", "24")):
            assert flag in argv and argv[argv.index(flag) + 1] == val

    def test_the_harness_actually_declares_every_flag_we_pass(self):
        """The strongest check here, and it must never skip. A flag the harness
        does not define surfaces only as a subprocess failure at runtime."""
        declared = _declared_flags("scripts/backtest_fvg_range.py")
        assert "--range-lookback" in declared, "sanity: parser extraction found nothing"
        argv, _, _ = _build(LIVE_FVG)
        passed = {a for a in argv[2:] if a.startswith("--")}
        missing = sorted(passed - declared)
        assert not missing, (
            "regime_debt_matrix passes flags backtest_fvg_range.py does not "
            f"declare: {missing}")

    def test_the_shared_common_flags_are_not_passed(self):
        """`common` carries --atr-stop-mult/--trail-mult, which this harness does
        not accept. Reusing it would abort the subprocess and read as a broken
        harness rather than a missing flag."""
        argv, _, _ = _build(LIVE_FVG)
        assert "--atr-stop-mult" not in argv
        assert "--trail-mult" not in argv

    def test_lever_flags_are_forwarded_when_declared(self):
        argv, _, _ = _build(dict(LIVE_FVG, stale_exit_bars=12))
        assert "--stale-exit-bars" in argv
        assert argv[argv.index("--stale-exit-bars") + 1] == "12"


class TestFidelityIsHonest:
    def test_live_config_is_faithful(self):
        _, faithful, omitted = _build(LIVE_FVG)
        assert faithful and omitted == []

    def test_adx_max_is_modelled_but_adx_min_is_not(self):
        """backtest_fvg_range.py declares --adx-max and no --adx-min. Claiming
        faithful on an adx_min-carrying leg would assert a floor the harness never
        applies."""
        declared = _declared_flags("scripts/backtest_fvg_range.py")
        assert "--adx-max" in declared and "--adx-min" not in declared
        argv, faithful, omitted = _build(dict(LIVE_FVG, adx_min=15))
        assert "--adx-min" not in argv
        assert not faithful and "adx_min" in omitted

    def test_tp_r_degrades_rather_than_being_forwarded(self):
        """`--tp-r` EXISTS but binds only under `--exit-style tp1r` (default is
        `mid`). Forwarding it unconditionally would claim a target the harness
        then ignores — the squeeze block's `tp_r` lesson, same shape."""
        argv, faithful, omitted = _build(dict(LIVE_FVG, tp_r=1.5))
        assert "--tp-r" not in argv
        assert not faithful and "tp_r" in omitted

    def test_an_unknown_lever_degrades_to_approximate(self):
        _, faithful, omitted = _build(dict(LIVE_FVG, some_new_lever=1))
        assert not faithful and "some_new_lever" in omitted

    def test_exit_head_is_reported_as_omitted(self):
        """Consistency with the trend/pullback/squeeze contract."""
        _, faithful, omitted = _build(dict(LIVE_FVG, exit_head_model="m"))
        assert not faithful and "exit_head_model" in omitted


class TestIntradayFeedIntervals:
    """The maps `.get(timeframe, <daily>)`, so a MISSING timeframe did not fail —
    it silently fetched daily bars for a leg told to `--resample 15m`. Two
    opposite facts ("no mapping for this bar" / "this leg trades daily") shared
    one value. Unreachable while only 1h/2h/4h/1d legs routed; reachable now.
    """

    def test_15m_crypto_resolves_to_the_15m_kline_not_daily(self):
        feed = rdm.resolve_feed("BTCUSDT", "15m")
        assert feed["source"] == "binance"
        assert feed["interval"] == "15", "15m fell back to the daily kline"
        assert feed["resample"] == "15m"

    def test_5m_crypto_resolves_to_the_5m_kline(self):
        assert rdm.resolve_feed("BTCUSDT", "5m")["interval"] == "5"

    def test_15m_equity_resolves_to_an_intraday_yahoo_interval(self):
        assert rdm.resolve_feed("MGC", "15m")["interval"] == "15m"

    def test_the_intraday_codes_are_the_ones_the_fetcher_speaks(self):
        """`fetch_backtest_candles.py --interval` documents the Bybit kline codes
        it accepts. A code outside that set is a fetch failure, not a bar."""
        src = open(os.path.join(REPO, "scripts/ops/fetch_backtest_candles.py"),
                   encoding="utf-8").read()
        accepted = {"1", "3", "5", "15", "30", "60", "120", "240", "D", "W"}
        assert "1/3/5/15/30/60/120/240/D/W" in src, "sanity: help text moved"
        assert set(rdm._TF_TO_BYBIT_INT.values()) <= accepted

    def test_the_existing_timeframes_are_unchanged(self):
        """The fix adds entries; it must not move a routed leg's feed."""
        for tf, code in (("1h", "60"), ("2h", "120"), ("4h", "240"), ("1d", "D")):
            assert rdm._TF_TO_BYBIT_INT[tf] == code
        for tf, code in (("1h", "60m"), ("2h", "60m"), ("4h", "60m"), ("1d", "1d")):
            assert rdm._TF_TO_YF_INT[tf] == code


class TestCoverageStatesStayDistinct:
    """`no_harness` / `harness_failed` / `not_attempted` / `measured` are four
    states, and this change moves one leg between two of them. Collapsing any
    pair is the defect `scripts/ci/check_collapsed_states.py` exists for.
    """

    def test_the_producer_still_declares_all_four(self):
        sys.path.insert(0, os.path.join(REPO, "scripts", "ops"))
        import build_strategy_evidence as bse
        assert set(bse.COVERAGE_STATES) == {
            "no_harness", "harness_failed", "not_attempted", "measured"}

    def test_a_routed_leg_no_longer_reports_no_harness(self):
        """`no_harness` means classify() routes nothing — an honest statement
        about OUR wiring. Once the branch exists it must stop being emitted for
        this family, or the state would mean two different things."""
        assert rdm.classify(LIVE_FVG) is not None

    def test_an_unroutable_leg_still_reports_no_harness(self):
        """The negative control: the state must remain reachable, or the guard is
        checking a value nothing can produce. `ict_scalp_*` is the live example —
        eight legs, a harness that exists, and no branch."""
        import yaml
        S = yaml.safe_load(
            open(os.path.join(REPO, "config/strategies.yaml"))
        )["strategies"]
        assert rdm.classify(S["ict_scalp_5m"]) is None
