"""Standalone profile loaders for account and instrument configurations.

These functions provide a clean public API for loading typed profile objects
from YAML config files. They are used by coordinator.account_profiles and
coordinator.instrument_profiles properties (S2 wiring) and can be imported
directly in tests and utilities without touching the live coordinator.

Default paths follow the standard config/ layout and can be overridden in tests.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.account_profile import AccountProfile
    from src.core.instrument_profile import InstrumentProfile

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_INSTRUMENTS_PATH = os.path.join(_REPO_ROOT, "config", "instruments.yaml")


def load_account_profiles(
    path: str | None = None,
) -> dict[str, "AccountProfile"]:
    """Load config/accounts.yaml and return typed AccountProfile objects.

    Delegates to the canonical src.config.accounts_loader.load_accounts_dict()
    reader so this function never hand-rolls its own YAML parser.

    Args:
        path: Override path. Defaults to config/accounts.yaml (via canonical loader).

    Returns:
        Dict keyed by account_id. Empty dict on any load failure.
    """
    from src.core.account_profile import AccountProfile
    from src.config.accounts_loader import load_accounts_dict

    raw = load_accounts_dict(path)
    return {
        account_id: AccountProfile.from_dict(account_id, data)
        for account_id, data in raw.items()
    }


def load_instrument_profiles(
    path: str | None = None,
) -> dict[str, "InstrumentProfile"]:
    """Load config/instruments.yaml and return typed InstrumentProfile objects.

    Falls back to the pre-built BTCUSDT/Bybit profile when instruments.yaml
    does not exist yet. This preserves current behavior during the S2->S7
    migration window.

    Args:
        path: Override path to instruments.yaml. Defaults to config/instruments.yaml.

    Returns:
        Dict keyed by symbol. Falls back to {BTCUSDT: <pre-built>} on FileNotFoundError.
    """
    import yaml
    from src.core.instrument_profile import InstrumentProfile

    resolved = path or _DEFAULT_INSTRUMENTS_PATH
    try:
        with open(resolved, "r") as f:
            raw = yaml.safe_load(f) or {}
        profiles: dict[str, InstrumentProfile] = {}
        for symbol, data in raw.get("instruments", {}).items():
            profiles[symbol] = InstrumentProfile(
                symbol=symbol,
                exchange=data.get("exchange", "unknown"),
                category=data.get("category", "unknown"),
                base_asset=data.get("base_asset", symbol),
                quote_currency=data.get("quote_currency", "USD"),
                settlement_currency=data.get("settlement_currency", "USD"),
                tick_size=float(data.get("tick_size", 0.01)),
                min_qty=float(data.get("min_qty", 1.0)),
                qty_step=float(data.get("qty_step", 1.0)),
                contract_value_usd=float(data.get("contract_value_usd", 1.0)),
                max_leverage=int(data.get("max_leverage", 0)),
                display_name=data.get("display_name", symbol),
            )
        return profiles
    except FileNotFoundError:
        btc = InstrumentProfile.btcusdt_bybit_linear()
        logger.debug("instruments.yaml not found at %s; using pre-built BTCUSDT profile", resolved)
        return {btc.symbol: btc}
    except Exception as exc:
        logger.warning("load_instrument_profiles: failed to parse %s: %s", resolved, exc)
        return {}
