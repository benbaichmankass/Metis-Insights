"""S-026 G4 — Pin strategy-attribution in the pipeline audit log.

BUG-033: the operator's hourly summary showed actionable signals
attributed to ``strategy: "unknown"`` while the same hourly's
"Strategies (today)" section correctly listed them under their real
names. This module pins the contract that **every actionable signal
flowing through the production multiplexer path lands a real strategy
name in the audit log**, even if upstream meta is incomplete.

Tests cover:
  * vwap-actionable via multiplexer → audit row strategy="vwap"
  * turtle_soup-actionable via multiplexer → audit row strategy="turtle_soup"
  * Direct STRATEGY=vwap routing → audit row strategy="vwap"
  * Defensive fallback: signal with no meta + no env STRATEGY →
    audit row strategy="multiplexed" (NOT "unknown")
  * BUG-033 diagnostic warning fires when meta.strategy_name is
    missing on an actionable signal
"""
from __future__ import annotations

import logging
import sys
import types
from unittest import mock

import pytest

if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()


@pytest.fixture
def captured_audit(monkeypatch):
    """Capture log_signal payloads in-memory + return a reader.

    Patches the ``log_signal`` symbol *as imported by* ``pipeline``
    (rather than the underlying module) — `tests/test_kill_switch.py`
    and friends stub `src.utils.signal_audit_logger`, and that stub
    survives across tests in the full sweep, which would otherwise
    swallow our writes.

    Yields a callable ``reader()`` that returns the captured payload
    dicts in call order.
    """
    captured: list[dict] = []

    def _capture(payload):
        # Match log_signal's behaviour: snapshot the dict so later
        # mutations by the caller don't leak into the captured row.
        captured.append(dict(payload or {}))

    monkeypatch.setattr("src.runtime.pipeline.log_signal", _capture)

    return lambda: list(captured)


@pytest.fixture(autouse=True)
def _silence_telegram(monkeypatch):
    """Pipeline calls notify_operator / send_via_alert_manager every tick.
    Silence those for tests."""
    monkeypatch.setattr("src.runtime.pipeline.notify_operator", lambda *a, **k: None)
    monkeypatch.setattr("src.runtime.pipeline.send_via_alert_manager", lambda *a, **k: None)
    monkeypatch.setattr("src.runtime.pipeline.write_status", lambda *a, **k: None)
    monkeypatch.setattr("src.runtime.pipeline.get_news_score",
                        lambda *a, **k: types.SimpleNamespace(
                            veto=False, decision="ok", adjustment=0.0,
                            item_count=0, reason="ok",
                        ))


def _vwap_actionable_signal(_settings):
    """Signal shape that vwap_signal_builder produces in production
    after a buy fires."""
    return {
        "symbol": "BTCUSDT",
        "side": "buy",
        "entry_price": 50_000.0,
        "stop_loss": 49_500.0,
        "take_profit": 51_000.0,
        "meta": {
            "strategy_name": "vwap",
            "vwap": 50_500.0,
            "current_price": 49_800.0,
            "std_dev": 100.0,
            "deviation_std": -2.5,
            "sl_std_mult": 1.0,
        },
    }


def _turtle_actionable_signal(_settings):
    return {
        "symbol": "BTCUSDT",
        "side": "buy",
        "entry_price": 50_000.0,
        "stop_loss": 49_500.0,
        "take_profit": 51_000.0,
        "price": 50_000.0,
        "meta": {
            "strategy_name": "turtle_soup",
            "confidence": 0.7,
            "direction": "long",
        },
    }


def _stub_runtime(monkeypatch):
    """Common stubs: avoid live coordinator + avoid runtime-counter side effects."""
    monkeypatch.setattr(
        "src.runtime.pipeline.inject_runtime_counters",
        lambda settings, _client: settings,
    )
    monkeypatch.setattr(
        "src.runtime.pipeline.inject_per_strategy_counters",
        lambda settings, _name: settings,
    )
    # Force the multi-account dispatch fast-path off so safe_place_order
    # runs the legacy single-client path (clean status without a real
    # Coordinator). The audit-log site is the same in both branches.
    monkeypatch.setenv("MULTI_ACCOUNT_DISPATCH", "false")
    monkeypatch.setenv("DRY_RUN", "true")


class TestActionableSignalsLogTheirStrategyName:
    def test_vwap_signal_via_run_pipeline_logs_vwap(self, captured_audit, monkeypatch):
        from src.runtime.pipeline import run_pipeline

        _stub_runtime(monkeypatch)

        run_pipeline(
            settings={"SYMBOL": "BTCUSDT", "DRY_RUN": "true"},
            exchange_client=None,
            telegram_client=None,
            signal_builder=_vwap_actionable_signal,
        )

        rows = captured_audit()
        results = [r for r in rows if r.get("event") == "pipeline_result"]
        assert len(results) == 1, f"expected 1 pipeline_result row, got {rows}"
        assert results[0]["strategy"] == "vwap", (
            f"BUG-033: actionable VWAP signal must log strategy='vwap', "
            f"got {results[0]!r}"
        )
        assert results[0]["side"] == "buy"

    def test_turtle_soup_signal_via_run_pipeline_logs_turtle_soup(
        self, captured_audit, monkeypatch,
    ):
        from src.runtime.pipeline import run_pipeline

        _stub_runtime(monkeypatch)

        run_pipeline(
            settings={"SYMBOL": "BTCUSDT", "DRY_RUN": "true"},
            exchange_client=None,
            telegram_client=None,
            signal_builder=_turtle_actionable_signal,
        )

        rows = captured_audit()
        results = [r for r in rows if r.get("event") == "pipeline_result"]
        assert len(results) == 1
        assert results[0]["strategy"] == "turtle_soup"

    def test_multiplexer_preserves_strategy_through_to_audit(
        self, captured_audit, monkeypatch,
    ):
        """G1 fix verified end-to-end: the multiplexer's dict-shallow-copy
        + meta-rewrap preserves meta.strategy_name through to the audit
        log site. (Pre-G1 the dict(signal) shallow copy with subsequent
        meta-mutation could drop strategy_name; the regression must not
        return.)"""
        from src.runtime import pipeline as pl
        _stub_runtime(monkeypatch)

        # Force the multiplexer to consider only one strategy and have
        # it return an actionable VWAP-shaped signal.
        monkeypatch.setattr(pl, "STRATEGIES", ["vwap"])
        monkeypatch.setitem(pl._STRATEGY_BUILDERS, "vwap", _vwap_actionable_signal)

        pl.run_pipeline(
            settings={"SYMBOL": "BTCUSDT", "DRY_RUN": "true"},
            exchange_client=None,
            telegram_client=None,
            signal_builder=pl.multiplexed_signal_builder,
        )

        rows = captured_audit()
        results = [r for r in rows if r.get("event") == "pipeline_result"]
        assert len(results) == 1
        assert results[0]["strategy"] == "vwap", (
            "Multiplexer must preserve meta.strategy_name through to the audit log"
        )


class TestNeverLogUnknownByDefault:
    """Defensive: if upstream meta is incomplete, the final fallback
    must NOT be 'unknown' — operator's hourly counts that as a real
    bucket and a missing label is uninformative. Use 'multiplexed'
    (the production builder name) as the safe default instead."""

    def test_actionable_signal_with_no_meta_falls_back_to_multiplexed(
        self, captured_audit, monkeypatch,
    ):
        from src.runtime.pipeline import run_pipeline
        _stub_runtime(monkeypatch)
        # Make sure env STRATEGY is not set so the real fallback fires.
        monkeypatch.delenv("STRATEGY", raising=False)

        # Bare signal — no meta.strategy_name, no top-level strategy.
        bad = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "entry_price": 50_000.0,
            "stop_loss": 49_500.0,
            "take_profit": 51_000.0,
        }
        run_pipeline(
            settings={"SYMBOL": "BTCUSDT", "DRY_RUN": "true"},
            exchange_client=None,
            telegram_client=None,
            signal_builder=lambda _s: bad,
        )

        rows = captured_audit()
        results = [r for r in rows if r.get("event") == "pipeline_result"]
        assert len(results) == 1
        assert results[0]["strategy"] != "unknown", (
            "BUG-033: 'unknown' must not leak into the audit log; the "
            "fallback default is now 'multiplexed'"
        )
        # Specifically: env STRATEGY is unset so the final-default fires.
        assert results[0]["strategy"] == "multiplexed"

    def test_strategy_env_wins_over_default(
        self, captured_audit, monkeypatch,
    ):
        """settings/env STRATEGY still attributes correctly when meta is missing."""
        from src.runtime.pipeline import run_pipeline
        _stub_runtime(monkeypatch)
        monkeypatch.setenv("STRATEGY", "vwap")

        bad = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "entry_price": 50_000.0,
            "stop_loss": 49_500.0,
            "take_profit": 51_000.0,
        }
        run_pipeline(
            settings={"SYMBOL": "BTCUSDT", "DRY_RUN": "true"},
            exchange_client=None,
            telegram_client=None,
            signal_builder=lambda _s: bad,
        )

        rows = captured_audit()
        results = [r for r in rows if r.get("event") == "pipeline_result"]
        assert results[0]["strategy"] == "vwap"


class TestDiagnosticWarningFires:
    """The temporary BUG-033 diagnostic warning emits when an actionable
    signal lacks the strong attribution sources (meta.strategy_name +
    top-level strategy). The warning includes the signal+settings keys
    so the next operator-side ping cycle pinpoints the leak path."""

    def test_warning_fires_for_actionable_no_meta_strategy_name(
        self, captured_audit, monkeypatch, caplog,
    ):
        from src.runtime.pipeline import run_pipeline
        _stub_runtime(monkeypatch)

        bad = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "entry_price": 50_000.0,
            "stop_loss": 49_500.0,
            "take_profit": 51_000.0,
        }
        with caplog.at_level(logging.WARNING, logger="src.runtime.pipeline"):
            run_pipeline(
                settings={"SYMBOL": "BTCUSDT", "DRY_RUN": "true"},
                exchange_client=None,
                telegram_client=None,
                signal_builder=lambda _s: bad,
            )

        diag_msgs = [
            rec.message for rec in caplog.records
            if "audit: actionable signal lacks" in rec.message
        ]
        assert len(diag_msgs) == 1, (
            f"BUG-033 diagnostic warning must fire exactly once for "
            f"actionable + missing-meta signals; got: {diag_msgs}"
        )
        # Diagnostic content contains the keys the operator needs.
        assert "signal_keys=" in diag_msgs[0]
        assert "meta_keys=" in diag_msgs[0]
        assert "settings_has_STRATEGY=" in diag_msgs[0]
        assert "env_has_STRATEGY=" in diag_msgs[0]

    def test_warning_does_not_fire_for_well_attributed_signals(
        self, captured_audit, monkeypatch, caplog,
    ):
        from src.runtime.pipeline import run_pipeline
        _stub_runtime(monkeypatch)

        with caplog.at_level(logging.WARNING, logger="src.runtime.pipeline"):
            run_pipeline(
                settings={"SYMBOL": "BTCUSDT", "DRY_RUN": "true"},
                exchange_client=None,
                telegram_client=None,
                signal_builder=_vwap_actionable_signal,
            )

        diag_msgs = [
            rec.message for rec in caplog.records
            if "audit: actionable signal lacks" in rec.message
        ]
        assert diag_msgs == [], (
            "Diagnostic warning must NOT fire when meta.strategy_name "
            f"is set; got: {diag_msgs}"
        )

    def test_warning_does_not_fire_for_no_signal_ticks(
        self, captured_audit, monkeypatch, caplog,
    ):
        """side='none' ticks are normal (no-trade); diagnostic must skip them."""
        from src.runtime.pipeline import run_pipeline
        _stub_runtime(monkeypatch)

        flat = {"symbol": "BTCUSDT", "side": "none"}
        with caplog.at_level(logging.WARNING, logger="src.runtime.pipeline"):
            run_pipeline(
                settings={"SYMBOL": "BTCUSDT", "DRY_RUN": "true"},
                exchange_client=None,
                telegram_client=None,
                signal_builder=lambda _s: flat,
            )

        diag_msgs = [
            rec.message for rec in caplog.records
            if "audit: actionable signal lacks" in rec.message
        ]
        assert diag_msgs == []
