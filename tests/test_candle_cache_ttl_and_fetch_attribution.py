"""Candle-cache TTL resolution + the `fetchby.*` consumer attribution cut.

WHY THIS FILE EXISTS. `_candle_cache_ttl` shipped 2026-08-10 with NO test
coverage of its own — grepping `tests/` for `candle_cache` or
`CANDLE_CACHE_TTL` before this file returned nothing. It is a pure function
that decides how stale a frame may be, and `close.iloc[-1]` off that frame is
the CURRENT PRICE behind a live order's entry geometry, so its arithmetic is
order-path-adjacent and was untested.

The fail-safe direction is the part worth pinning hardest: an unparseable or
non-positive cap must fall back to the DEFAULT, never to "no cache" and never
to "unbounded cache". A typo in an env var must not silently move the price a
trade is sized from — in either direction.
"""

from __future__ import annotations

import threading

import pytest

from src.runtime import market_data
from src.runtime import tick_cost


# --------------------------------------------------------------------------
# _candle_cache_ttl — the arithmetic
# --------------------------------------------------------------------------
class TestCandleCacheTtl:
    def test_fraction_governs_when_it_lands_under_the_cap(self, monkeypatch):
        """5m at the default 0.10 wants 30s, which the 60s cap does not touch."""
        monkeypatch.delenv("CANDLE_CACHE_TTL_FRACTION", raising=False)
        monkeypatch.delenv("CANDLE_CACHE_TTL_MAX_S", raising=False)
        assert market_data._candle_cache_ttl("5m") == pytest.approx(30.0)

    @pytest.mark.parametrize(
        "timeframe,uncapped",
        [("15m", 90.0), ("1h", 360.0), ("4h", 1440.0), ("1d", 8640.0)],
    )
    def test_cap_is_the_binding_term_for_every_bar_at_or_above_15m(
        self, monkeypatch, timeframe, uncapped
    ):
        """The measured finding, pinned as a test.

        Each of these frames WANTS the value in `uncapped` under the declared
        `bar_seconds * fraction` contract, and every one of them is clamped to
        60s instead. This is not a bug being asserted as correct — it is the
        incumbent behaviour being pinned so that changing the cap has to be a
        deliberate act with a failing test behind it.
        """
        monkeypatch.delenv("CANDLE_CACHE_TTL_FRACTION", raising=False)
        monkeypatch.delenv("CANDLE_CACHE_TTL_MAX_S", raising=False)
        assert uncapped > 60.0, "fixture would not exercise the cap"
        assert market_data._candle_cache_ttl(timeframe) == pytest.approx(60.0)

    def test_raising_the_cap_lets_the_fraction_through(self, monkeypatch):
        monkeypatch.delenv("CANDLE_CACHE_TTL_FRACTION", raising=False)
        monkeypatch.setenv("CANDLE_CACHE_TTL_MAX_S", "300")
        # 1h wants 360s, so 300 still binds; 15m wants 90s, so it comes through.
        assert market_data._candle_cache_ttl("1h") == pytest.approx(300.0)
        assert market_data._candle_cache_ttl("15m") == pytest.approx(90.0)

    @pytest.mark.parametrize("bad", ["", "abc", "None", "60s"])
    def test_unparseable_cap_falls_back_to_the_default_not_to_disabled(
        self, monkeypatch, bad
    ):
        """A typo must not silently switch caching off."""
        monkeypatch.delenv("CANDLE_CACHE_TTL_FRACTION", raising=False)
        monkeypatch.setenv("CANDLE_CACHE_TTL_MAX_S", bad)
        assert market_data._candle_cache_ttl("1h") == pytest.approx(60.0)

    @pytest.mark.parametrize("bad", ["0", "-1", "-300.5"])
    def test_non_positive_cap_falls_back_to_the_default_not_to_disabled(
        self, monkeypatch, bad
    ):
        """`0` on the CAP is a typo; the sanctioned off-switch is the FRACTION.

        Reading a 0 cap as "disable" would give two different env vars the same
        off-semantics, and the one documented in the rollback runbook is the
        fraction. Collapsing them would make an operator who mistyped the cap
        believe they had merely narrowed it.
        """
        monkeypatch.delenv("CANDLE_CACHE_TTL_FRACTION", raising=False)
        monkeypatch.setenv("CANDLE_CACHE_TTL_MAX_S", bad)
        assert market_data._candle_cache_ttl("1h") == pytest.approx(60.0)

    def test_zero_fraction_is_the_sanctioned_off_switch(self, monkeypatch):
        monkeypatch.setenv("CANDLE_CACHE_TTL_FRACTION", "0")
        monkeypatch.setenv("CANDLE_CACHE_TTL_MAX_S", "3600")
        assert market_data._candle_cache_ttl("1h") == 0.0
        # ...and a 0 TTL must also refuse to build a cache key at all.
        assert market_data._candle_cache_key(object(), "BTCUSDT", "1h", 200, None) is None

    def test_unknown_timeframe_serves_fresh(self, monkeypatch):
        monkeypatch.setenv("CANDLE_CACHE_TTL_MAX_S", "3600")
        assert market_data._candle_cache_ttl("7m") == 0.0

    def test_since_requests_are_never_cached(self):
        """A historical-range reconstruction goes to the venue every time."""
        assert market_data._candle_cache_key(object(), "BTCUSDT", "1h", 200, 1) is None


# --------------------------------------------------------------------------
# fetchby.* — the consumer cut
# --------------------------------------------------------------------------
class TestFetchConsumerAttribution:
    def setup_method(self):
        tick_cost.reset_for_test() if hasattr(tick_cost, "reset_for_test") else None

    def test_no_phase_reports_none_not_a_neighbour(self):
        assert tick_cost.current_phase() is None

    def test_phase_is_published_while_a_hook_is_open(self):
        with tick_cost.hook("pipeline.signal_build"):
            assert tick_cost.current_phase() == "pipeline.signal_build"
        assert tick_cost.current_phase() is None

    def test_innermost_phase_wins_and_unwinds(self):
        with tick_cost.hook("order_monitor"):
            assert tick_cost.current_phase() == "order_monitor"
            with tick_cost.hook("monitor.strategy_monitor_loop"):
                assert tick_cost.current_phase() == "monitor.strategy_monitor_loop"
            # Must unwind to the parent, not to None.
            assert tick_cost.current_phase() == "order_monitor"

    def test_a_fetch_hook_does_not_overwrite_the_phase(self):
        """Without this, every fetch would attribute to itself."""
        with tick_cost.hook("pipeline.signal_build"):
            with tick_cost.hook("fetch.1h"):
                assert tick_cost.current_phase() == "pipeline.signal_build"

    def test_phase_unwinds_even_when_the_body_raises(self):
        with pytest.raises(RuntimeError):
            with tick_cost.hook("pipeline.signal_build"):
                raise RuntimeError("boom")
        assert tick_cost.current_phase() is None

    def test_another_thread_does_not_inherit_the_phase(self):
        """The decoupled exit loop must not attribute to the tick thread's phase."""
        seen = {}

        def worker():
            seen["phase"] = tick_cost.current_phase()

        with tick_cost.hook("pipeline.signal_build"):
            t = threading.Thread(target=worker)
            t.start()
            t.join()

        assert seen["phase"] is None

    def test_fetch_phase_records_both_cuts(self, monkeypatch):
        """One fetch lands once in `fetch.*` and once in `fetchby.*`."""
        recorded = []
        monkeypatch.setattr(
            tick_cost, "record_hook", lambda n, ms: recorded.append(n)
        )
        with tick_cost.hook("pipeline.regime_bar_scoring"):
            with market_data._fetch_phase("1d"):
                pass
        assert "fetch.1d" in recorded
        assert "fetchby.regime_bar_scoring" in recorded

    def test_fetch_outside_any_phase_is_labelled_unattributed(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            tick_cost, "record_hook", lambda n, ms: recorded.append(n)
        )
        with market_data._fetch_phase("5m"):
            pass
        assert "fetchby.unattributed" in recorded


class TestHookNameBudget:
    def test_budget_has_headroom_for_both_cuts(self):
        """The live chain was at 27 of 32 before `fetchby.*` was added.

        Asserted rather than commented because the failure mode is silent: an
        overflowing name is refused and counted in `hook_names_refused`, and a
        truncated split reads exactly like a complete one to anybody who does
        not check that counter.
        """
        assert tick_cost._MAX_HOOK_NAMES >= 48
