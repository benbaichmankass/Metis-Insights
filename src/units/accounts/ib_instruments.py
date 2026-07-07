"""IBKR per-symbol instrument-type resolver — FUT vs STK (2026-07-07).

Answers, for a given symbol, "is this a futures contract or an equity/ETF
on Interactive Brokers, and on which exchange?" Config-driven from
``config/instruments.yaml::instruments.<SYMBOL>.ib`` (see the schema note
at the top of that file), with a **back-compat fallback** to the legacy
hardcoded ``{MES: CME, MGC: COMEX, MHG: COMEX}`` futures map that
:class:`~src.units.accounts.ib_client.IBClient` carried before this
resolver existed — so any caller/test whose ``instruments.yaml`` doesn't
carry an ``ib:`` block yet still resolves MES/MGC/MHG unchanged.

Why this exists (docs/integrations/ibkr-equity-etf-support-DESIGN.md §4.1):
``ib_paper`` mixes futures (MES/MGC/MHG) and equities (the alpaca-ETF
basket, reused on the same clientId per the 2026-07-07 operator decision)
on ONE account. A single per-account ``market_type: futures`` field cannot
express that split, so both :meth:`IBClient._build_contract` (which
contract shape to build) and the coordinator's per-order whole-share
sizing resolution need a **per-symbol**, not per-account, answer. This
module is the single source of truth both call.

An unmapped symbol raises :class:`ValueError` from
:func:`ib_instrument_spec` — mirrors the pre-2026-07-07 behavior where a
stray non-futures symbol reaching ``_build_contract`` was refused rather
than silently misrouted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Back-compat default — used only when a symbol has no `ib:` block in
# config/instruments.yaml (e.g. a minimal/legacy instruments.yaml fixture
# in a test). Mirrors the hardcoded dict IBClient._build_contract carried
# before this resolver existed, so MES/MGC/MHG behavior is unchanged even
# without the config addition.
_LEGACY_FUT_EXCHANGES = {"MES": "CME", "MGC": "COMEX", "MHG": "COMEX"}


@dataclass(frozen=True)
class IBInstrumentSpec:
    """Resolved IBKR contract shape for one symbol."""

    symbol: str
    sec_type: str  # "FUT" | "STK"
    exchange: str  # e.g. "CME" / "COMEX" (futures) or "SMART" (equities)
    primary_exchange: Optional[str] = None  # equities only — qualifyContracts hint
    currency: str = "USD"


_SPEC_CACHE: Optional[dict] = None


def _load_specs() -> dict:
    """Lazily load + cache the `ib:` blocks from config/instruments.yaml.

    Mirrors the caching shape of ``risk.contract_value_usd_for`` — a
    module-level cache so the per-order resolution stays cheap. Any read/
    parse failure degrades to an empty map (every lookup then falls back
    to the legacy FUT default or raises for a truly unknown symbol) —
    never raises out of this loader.
    """
    global _SPEC_CACHE
    if _SPEC_CACHE is not None:
        return _SPEC_CACHE
    specs: dict = {}
    try:
        import yaml
        from src.core.profile_loader import _DEFAULT_INSTRUMENTS_PATH

        with open(_DEFAULT_INSTRUMENTS_PATH, "r") as f:
            raw = yaml.safe_load(f) or {}
        for symbol, data in (raw.get("instruments") or {}).items():
            ib_block = (data or {}).get("ib")
            if not ib_block:
                continue
            sym = str(symbol).upper()
            specs[sym] = IBInstrumentSpec(
                symbol=sym,
                sec_type=str(ib_block.get("sec_type") or "").upper(),
                exchange=str(ib_block.get("exchange") or "SMART"),
                primary_exchange=ib_block.get("primary_exchange"),
                currency=str(ib_block.get("currency") or "USD"),
            )
    except Exception:  # noqa: BLE001 — never block a lookup on a config read
        specs = {}
    _SPEC_CACHE = specs
    return specs


def ib_instrument_spec(symbol: Optional[str]) -> IBInstrumentSpec:
    """Resolve *symbol* to its :class:`IBInstrumentSpec` (FUT or STK).

    Raises ``ValueError`` for a symbol with neither a `config/instruments.yaml`
    `ib:` block nor a legacy futures-map entry — the same failure shape
    ``IBClient._build_contract`` raised pre-2026-07-07 for a stray symbol.
    """
    sym = str(symbol or "MES").upper()
    spec = _load_specs().get(sym)
    if spec is not None:
        return spec
    legacy_exchange = _LEGACY_FUT_EXCHANGES.get(sym)
    if legacy_exchange is not None:
        return IBInstrumentSpec(symbol=sym, sec_type="FUT", exchange=legacy_exchange, currency="USD")
    raise ValueError(
        f"ib_instrument_spec: no IB instrument-type mapping for symbol={sym!r}. "
        "Add an `ib:` block to config/instruments.yaml before routing it to "
        "an IB account."
    )


def is_ib_equity_symbol(symbol: Optional[str]) -> bool:
    """True if *symbol* resolves to an IBKR STK (equity/ETF) contract.

    Fail-safe: any resolution error (unmapped symbol) returns ``False`` so
    an unrecognized symbol never gets silently treated as an equity — it
    falls through to the existing futures-shaped path, and
    ``_build_contract`` raises its own clear error at order time.
    """
    try:
        return ib_instrument_spec(symbol).sec_type == "STK"
    except Exception:  # noqa: BLE001
        return False


def ib_order_market_type(symbol: Optional[str], default: str) -> str:
    """Effective per-order sizing ``market_type`` for an IBKR account.

    ``ib_paper`` mixes futures and equities on one account, so the static
    ``accounts.yaml::market_type`` field can't express the split. Returns
    ``"equity"`` for a resolved STK symbol (routes ``RiskManager.position_size``
    onto the whole-SHARE path — round-up-to-1-share relaxation + the margin/
    buying-power notional cap — instead of the strict futures
    refuse-sub-1-contract path) and *default* (the account's configured
    ``market_type``, unchanged) for everything else, including any symbol
    this resolver doesn't recognize.
    """
    return "equity" if is_ib_equity_symbol(symbol) else default
