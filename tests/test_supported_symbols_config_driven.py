"""Config-driven intent-layer symbol whitelist (2026-06-11).

``StrategyIntent`` validation must accept every symbol declared on an
account in ``config/accounts.yaml`` — adding an instrument to config can
never require a code edit in ``src/runtime/intents.py``. The static
``SUPPORTED_SYMBOLS`` base had drifted behind accounts.yaml once (the M15
XAUUSD / SPY / QQQ / GLD wiring), which would have raised ``ValueError``
out of ``_collect_intents`` on the first actionable signal from those
strategies.
"""
from __future__ import annotations

import pytest

from src.runtime import intents
from src.runtime.intents import (
    SUPPORTED_SYMBOLS,
    StrategyIntent,
    supported_symbols,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    intents._reset_config_symbols_cache()
    yield
    intents._reset_config_symbols_cache()


def test_static_base_still_supported():
    accepted = supported_symbols()
    for sym in ("BTCUSDT", "MES", "MGC", "MHG"):
        assert sym in SUPPORTED_SYMBOLS
        assert sym in accepted


def test_accounts_yaml_symbols_are_supported():
    """Every symbol declared on a configured account constructs an intent.

    Reads the real config/accounts.yaml — this is the regression guard
    that the M15 instruments (and any future ones) are accepted without
    touching intents.py.
    """
    from src.config.accounts_loader import load_accounts_dict

    declared = {
        str(sym).upper().replace("/", "")
        for cfg in load_accounts_dict().values()
        for sym in (cfg or {}).get("symbols") or []
    }
    assert declared, "accounts.yaml declared no symbols — fixture broke?"
    accepted = supported_symbols()
    for sym in sorted(declared):
        assert sym in accepted, f"{sym} declared in accounts.yaml but rejected"
        intent = StrategyIntent(
            strategy="config_driven_test", symbol=sym, side="long", target_qty=0.0,
        )
        assert intent.symbol == sym


def test_m15_symbols_construct_intents():
    """The concrete symbols that were stranded by the static whitelist."""
    for sym in ("XAUUSD", "SPY", "QQQ", "GLD"):
        intent = StrategyIntent(strategy="m15_test", symbol=sym, side="short", target_qty=0.0)
        assert intent.symbol == sym


def test_undeclared_symbol_still_rejected():
    with pytest.raises(ValueError, match="must be one of"):
        StrategyIntent(strategy="typo_test", symbol="DOGEUSDT", side="long", target_qty=0.0)


def test_config_load_failure_falls_back_to_static_base(monkeypatch):
    """A broken accounts.yaml read degrades to the static base, never narrower."""
    import src.config.accounts_loader as loader

    def _boom(*args, **kwargs):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(loader, "load_accounts_dict", _boom)
    intents._reset_config_symbols_cache()
    assert supported_symbols() == SUPPORTED_SYMBOLS
    # The static base keeps working through the failure.
    intent = StrategyIntent(strategy="failsafe_test", symbol="MES", side="long", target_qty=0.0)
    assert intent.symbol == "MES"
