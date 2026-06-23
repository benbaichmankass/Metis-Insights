"""S-015 regression — VWAP timeframe is sourced from strategies.yaml.

Operator directive (verbatim, S-015 mid-sprint chat):

  > vwap should be wired to 5 minutes not 15 minutes so we should
  > do that fix as well

Pre-fix bug: ``vwap_signal_builder`` read ``TIMEFRAME`` from the
per-account env first; if any operator's ``.env.bybit_2`` file still
had the legacy ``TIMEFRAME=15m`` line, the YAML change to ``5m``
would be a silent no-op. These tests pin the new resolution order:
strategies.yaml first, env second, default third.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

# matplotlib stub mirrors tests/test_vwap_strategy.py — pipeline.py imports
# signal_notifications which imports matplotlib transitively.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

import pytest
import yaml

pd = pytest.importorskip("pandas")


def _strategies_yaml_vwap():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load((repo / "config" / "strategies.yaml").read_text())
    return (raw.get("strategies") or {}).get("vwap", {}) or {}


def test_strategies_yaml_pins_vwap_to_5m():
    """The on-disk YAML must explicitly say 5m — this is the operator's
    source of truth and what live deployments load."""
    cfg = _strategies_yaml_vwap()
    assert cfg.get("timeframe") == "5m", (
        f"strategies.yaml vwap.timeframe is {cfg.get('timeframe')!r}, "
        f"must be '5m' per S-015 operator directive"
    )


def test_vwap_signal_builder_prefers_strategies_yaml(monkeypatch):
    """Even when the per-account env file sets ``TIMEFRAME=15m``, the
    strategies.yaml entry must win. This is the actual failure mode
    on the production VM if the operator's `.env` files weren't
    updated alongside the YAML."""
    from src.runtime import strategy_signal_builders as ssb
    from src.units import strategies as strategies_mod

    captured: dict = {}

    class _StubExchange:
        def get_ohlcv(self, symbol, timeframe, limit=100):
            captured["timeframe"] = timeframe
            captured["symbol"] = symbol
            return None  # short-circuit before build_vwap_signal

    monkeypatch.setattr(
        ssb, "_build_killzone_exchange", lambda s: _StubExchange()
    )
    monkeypatch.setattr(
        strategies_mod, "load_strategy_config",
        lambda *a, **kw: {"vwap": {"timeframe": "5m", "enabled": True}},
    )
    monkeypatch.setattr(
        ssb, "load_strategy_config",
        lambda *a, **kw: {"vwap": {"timeframe": "5m", "enabled": True}},
        raising=False,
    )

    settings = {"SYMBOL": "BTCUSDT", "TIMEFRAME": "15m", "MAX_QTY": 1.0}
    with pytest.raises(RuntimeError, match="no candle data returned"):
        ssb.vwap_signal_builder(settings)

    assert captured["timeframe"] == "5m", (
        f"vwap_signal_builder used {captured['timeframe']!r} from env "
        f"instead of '5m' from strategies.yaml"
    )


def test_vwap_signal_builder_falls_through_to_env_if_yaml_absent(monkeypatch):
    """If strategies.yaml doesn't have a vwap entry (regression: a future
    refactor accidentally drops the key), the env var still works as
    a fallback. Same for the lowercase ``timeframe`` settings key.
    Default of 5m only fires when nothing else is configured."""
    from src.runtime import strategy_signal_builders as ssb
    from src.units import strategies as strategies_mod

    captured: dict = {}

    class _StubExchange:
        def get_ohlcv(self, symbol, timeframe, limit=100):
            captured["timeframe"] = timeframe
            return None

    monkeypatch.setattr(
        ssb, "_build_killzone_exchange", lambda s: _StubExchange()
    )
    # vwap present + enabled but no timeframe → falls through to the env var.
    # (An absent/disabled vwap now short-circuits to side=none, so the entry
    # must be enabled to exercise the timeframe-resolution fallthrough.)
    monkeypatch.setattr(
        strategies_mod, "load_strategy_config",
        lambda *a, **kw: {"vwap": {"enabled": True}},
    )
    monkeypatch.setattr(
        ssb, "load_strategy_config",
        lambda *a, **kw: {"vwap": {"enabled": True}}, raising=False,
    )

    settings = {"SYMBOL": "BTCUSDT", "TIMEFRAME": "1h"}
    with pytest.raises(RuntimeError):
        ssb.vwap_signal_builder(settings)
    assert captured["timeframe"] == "1h"


def test_vwap_default_timeframe_is_5m_when_nothing_configured(monkeypatch):
    from src.runtime import strategy_signal_builders as ssb
    from src.units import strategies as strategies_mod

    captured: dict = {}

    class _StubExchange:
        def get_ohlcv(self, symbol, timeframe, limit=100):
            captured["timeframe"] = timeframe
            return None

    monkeypatch.setattr(
        ssb, "_build_killzone_exchange", lambda s: _StubExchange()
    )
    monkeypatch.setattr(
        strategies_mod, "load_strategy_config",
        lambda *a, **kw: {"vwap": {"enabled": True}},
    )
    monkeypatch.setattr(
        ssb, "load_strategy_config",
        lambda *a, **kw: {"vwap": {"enabled": True}}, raising=False,
    )
    settings = {"SYMBOL": "BTCUSDT"}
    with pytest.raises(RuntimeError):
        ssb.vwap_signal_builder(settings)
    assert captured["timeframe"] == "5m"


def test_vwap_disabled_short_circuits_without_eval(monkeypatch):
    """BL-20260610-001: a disabled vwap (enabled:false, M7-killed) must
    short-circuit to side=none BEFORE any fetch or ``vwap_eval`` emission —
    matching every other builder, so a killed strategy goes fully silent on
    the audit surface and burns no per-tick eval cycles."""
    from src.runtime import strategy_signal_builders as ssb
    from src.units import strategies as strategies_mod

    emitted: list = []
    fetched: list = []

    monkeypatch.setattr(ssb, "log_signal", lambda row: emitted.append(row))
    monkeypatch.setattr(
        ssb, "_build_killzone_exchange",
        lambda s: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    monkeypatch.setattr(
        ssb, "fetch_candles",
        lambda *a, **k: fetched.append(a) or None, raising=False,
    )
    # vwap explicitly disabled (and the absent-entry default is also disabled).
    monkeypatch.setattr(
        strategies_mod, "load_strategy_config",
        lambda *a, **kw: {"vwap": {"enabled": False}},
    )
    monkeypatch.setattr(
        ssb, "load_strategy_config",
        lambda *a, **kw: {"vwap": {"enabled": False}}, raising=False,
    )

    out = ssb.vwap_signal_builder({"SYMBOL": "BTCUSDT"})

    sig = out.get("signal", out) if isinstance(out, dict) else {}
    assert (sig.get("side") or out.get("side")) == "none"
    # No exchange build, no candle fetch, and ZERO audit rows (no vwap_eval).
    assert fetched == []
    assert not any(
        (r.get("event") == "vwap_eval") for r in emitted
    ), f"disabled vwap still emitted eval rows: {emitted}"
