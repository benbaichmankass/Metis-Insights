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


_TICK_SIZE_CACHE: dict[str, float] | None = None


def tick_size_for(symbol: str) -> float | None:
    """Venue price grid for *symbol*, or ``None`` when it cannot be resolved.

    Sibling of :func:`contract_value_usd_for` over the same single source
    (``config/instruments.yaml``), added 2026-08-23 for
    `BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND`: grading whether a resting
    stop sits where the journal declared needs the venue's tick, and every
    caller that wanted one was re-reading the YAML itself.

    ⚠️ **Returns ``None`` rather than a default, unlike its sibling.** A wrong
    contract value scales a P&L figure; a wrong TICK turns a real protection
    divergence into "agrees" or the reverse, so the safe failure is to refuse
    and let the caller report `no_tick_size`. Do not add a fallback here.

    Memoized process-wide; reset ``_TICK_SIZE_CACHE`` to force a reload (tests).
    """
    global _TICK_SIZE_CACHE
    if not symbol:
        return None
    if _TICK_SIZE_CACHE is None:
        try:
            profiles = load_instrument_profiles()
            cache: dict[str, float] = {}
            for sym, prof in (profiles or {}).items():
                try:
                    tick = float(getattr(prof, "tick_size", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if tick > 0:
                    cache[sym] = tick
            _TICK_SIZE_CACHE = cache
        except Exception:  # noqa: BLE001 — best-effort; None is the honest answer
            _TICK_SIZE_CACHE = {}
    return _TICK_SIZE_CACHE.get(symbol)


# Commission-free venues: US equities/ETFs on Alpaca settle at $0 commission
# (only sub-basis-point SEC/TAF regulatory fees on the SELL leg, treated as
# negligible). The flat crypto-style round-trip-bps estimate over-charges them
# ~25× — this is the `spy_pullback_1h` net-R sign-flip: gross +1.456R →
# net −0.457R was a fee-model artifact, not a real cost (M24 net-R re-grade,
# `docs/research/M24-net-r-regrade-findings-2026-07-17.md`; resolved 2026-07-29).
# All 14 (alpaca, spot) roster rows (SPY/QQQ/IWM/GLD/SLV/GDX/IAUM/IEF/QLD/TQQQ/
# SPLG/SCHA/USO/TLT) are commission-free regardless of underlying asset_class —
# so this keys on the VENUE, not the asset class.
_COMMISSION_FREE_VENUES: set[tuple[str, str]] = {("alpaca", "spot")}
_ROUNDTRIP_FEE_BPS_CACHE: dict[str, float] | None = None


def roundtrip_fee_bps_for(symbol: str) -> float | None:
    """Venue-appropriate round-trip fee in basis points for *symbol*, or ``None``.

    Returns ``0.0`` for a commission-free venue (US equity/ETF on Alpaca), or
    **``None``** meaning "no venue-specific rate — use the estimator's default"
    (crypto / futures / fx keep ``trade_costs.DEFAULT_FEE_BPS_ROUNDTRIP``). Pure
    resolver over ``config/instruments.yaml`` (keyed on the instrument's
    ``exchange`` + ``category``); best-effort — an unknown symbol → ``None``
    (conservative: never silently zero an unknown venue's cost), never raises.
    Memoized process-wide; reset ``_ROUNDTRIP_FEE_BPS_CACHE`` to force a reload
    (tests only). Sibling of ``contract_value_usd_for`` — the single resolver
    the close-path cost estimate (``database._record_trade_cost_estimate``)
    reads so a commission-free equity is not charged a crypto-perp fee.
    """
    global _ROUNDTRIP_FEE_BPS_CACHE
    if not symbol:
        return None
    if _ROUNDTRIP_FEE_BPS_CACHE is None:
        try:
            profiles = load_instrument_profiles()
            cache: dict[str, float] = {}
            for sym, p in (profiles or {}).items():
                venue = (getattr(p, "exchange", None), getattr(p, "category", None))
                if venue in _COMMISSION_FREE_VENUES:
                    cache[sym] = 0.0
            _ROUNDTRIP_FEE_BPS_CACHE = cache
        except Exception:  # noqa: BLE001 — best-effort; None keeps the estimator default
            _ROUNDTRIP_FEE_BPS_CACHE = {}
    return _ROUNDTRIP_FEE_BPS_CACHE.get(symbol)
