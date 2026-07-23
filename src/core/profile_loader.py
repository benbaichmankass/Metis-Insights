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


# ---------------------------------------------------------------------------
# Canonical USD-per-point contract-value resolver (single source).
# ---------------------------------------------------------------------------
# Reference-data lookup over config/instruments.yaml — a Signals/Platform-layer
# concern (contract specs), NOT a sizing/Execution one. Lives here (the pure
# profile loader) so PnL/journal callers can resolve the multiplier WITHOUT
# importing the sizing module (src.units.accounts.risk) — which pulls in the
# coordinator/executor and so drags the whole Execution layer into any caller.
# src.units.accounts.risk.contract_value_usd_for and
# src.runtime.local_pnl.contract_value_usd_for now both delegate here, so this
# is the one definition (M0b layer-drain, BL-20260723-DB-LAYER-IMPURITY).
_CONTRACT_VALUE_USD_CACHE: dict[str, float] | None = None


def contract_value_usd_for(symbol: str) -> float:
    """USD-per-point contract value for *symbol* (default 1.0).

    Canonical resolver over ``config/instruments.yaml`` (single source). Pure —
    no Execution/sizing imports. Best-effort: any failure falls back to 1.0
    (the crypto-perp value), never raises. Memoized process-wide; reset the
    module global ``_CONTRACT_VALUE_USD_CACHE`` to force a reload (tests only).
    """
    global _CONTRACT_VALUE_USD_CACHE
    if not symbol:
        return 1.0
    if _CONTRACT_VALUE_USD_CACHE is None:
        try:
            profiles = load_instrument_profiles()
            _CONTRACT_VALUE_USD_CACHE = {
                sym: float(getattr(p, "contract_value_usd", 1.0) or 1.0)
                for sym, p in (profiles or {}).items()
            }
        except Exception:  # noqa: BLE001 — best-effort; default keeps crypto correct
            _CONTRACT_VALUE_USD_CACHE = {}
    return _CONTRACT_VALUE_USD_CACHE.get(symbol, 1.0)
