"""The single seam that turns a raw quantity into an exchange-LEGAL quantity.

Phase 1 of the sizing/qty-legalization consolidation
(``docs/sizing-legalization-DESIGN.md``). This module is a **pure addition**:
nothing calls it yet. It exists so the four scattered venue-minimum checks —
``coordinator.py`` sized-qty guard (:1500), ``coordinator.py`` intent-delta
guard (:1900), the ``execute._submit_order`` pre-flight (:958), and the
whole-unit refusals in ``risk.py`` — can be migrated onto ONE implementation
(Phases 2-3), so the recurring "a sub-lot qty reached the order path" bug class
(BL-20260611-005 / BL-20260619-ETHMIN / BL-20260622-ALPACA-FRACTIONAL /
PR #5700) cannot resurface at a site someone forgot to update.

Scope = **concern C, venue legalization only**: step-align (floor, never up —
realised risk must not exceed the sized cap) and enforce the exchange minimum
lot, else refuse. It does NOT do risk sizing (concern A, ``RiskManager``) and
does NOT compute the reconciliation delta (concern B, ``intents``). The
account-level ``risk.min_qty`` is a RISK floor, not a venue rule, so it is NOT
folded in here — the caller keeps its own risk floor until Phase 3 unifies them.

Minimum-resolution order (all fail-safe — a miss degrades to passthrough, i.e.
today's "rule unknown -> submit unmodified" contract, never a blocked order):

  1. ``InstrumentProfile`` for the symbol from ``config/instruments.yaml`` — the
     authoritative, offline, per-symbol source (already loaded by ``risk.py`` /
     ``coordinator.py``; it carries ``min_qty`` / ``qty_step`` for every wired
     instrument). Used only when the profile's exchange matches the account's
     (or either is unknown), so a name-collision across venues can't apply the
     wrong lot.
  2. The live venue lot rule (``precision.get_lot_rule`` -> cache -> live
     instruments-info -> static map), Bybit-only — covers a symbol the account
     trades that has no profile entry yet.
  3. ``None`` -> passthrough (ok=True, qty unchanged): non-Bybit venues with no
     profile (IBKR/Alpaca/OANDA carry their own whole-unit handling in
     ``risk.py``), or an unresolvable rule.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LegalizedQty:
    """Result of legalizing a raw quantity against a venue's lot rule.

    ``ok`` False means REFUSE this trade (a per-trade refusal — the
    Prime-Directive shape; the account stays live). ``reason`` carries the
    cause token (``below_venue_min_qty``) so callers journal the same clean
    refusal the coordinator's sized-qty guard emits today, never a noisy
    ``exchange_rejected`` / ``bybit_place_order_failed`` row.

    ``qty`` is the step-aligned quantity: the value to send when ``ok`` is
    True, or the (sub-minimum) floored value for logging when ``ok`` is False.
    When no rule resolves it is the untouched input (passthrough).
    """

    qty: float
    ok: bool
    reason: str
    venue_min: Optional[float]
    step: Optional[float]
    source: str  # "instrument_profile" | "live_lot_rule" | "unknown"
    # The exact string to put on the wire — the step-precise Decimal
    # representation (preserves trailing zeros, e.g. "0.100" for step 0.001),
    # so a caller that submits a string (the Bybit pre-flight) sends byte-for-
    # byte what it sent pre-seam. Equal to ``str(float(qty))`` on passthrough.
    qty_str: str = ""


# --- profile cache (instruments.yaml rarely changes at runtime; a restart
# reloads). Keyed by resolved path so a test override doesn't poison the
# default-path cache. Thread-safe for the web-api's threadpool callers. ---
_PROFILE_CACHE: Dict[Optional[str], Dict[str, Any]] = {}
_PROFILE_LOCK = threading.Lock()


def _load_profiles(instruments_path: Optional[str]) -> Dict[str, Any]:
    cached = _PROFILE_CACHE.get(instruments_path)
    if cached is not None:
        return cached
    with _PROFILE_LOCK:
        cached = _PROFILE_CACHE.get(instruments_path)
        if cached is not None:
            return cached
        try:
            from src.core.profile_loader import load_instrument_profiles
            profiles = load_instrument_profiles(instruments_path) or {}
        except Exception as exc:  # noqa: BLE001 — never block the order path on config load
            logger.warning("qty_legalize: instrument-profile load failed: %s", exc)
            profiles = {}
        _PROFILE_CACHE[instruments_path] = profiles
        return profiles


def _reset_profile_cache() -> None:
    """Test hook: drop the cached instrument profiles."""
    with _PROFILE_LOCK:
        _PROFILE_CACHE.clear()


def _resolve_venue_lot_rule(
    symbol: str,
    account_cfg: dict,
    client: Any = None,
    *,
    profiles: Optional[Dict[str, Any]] = None,
    instruments_path: Optional[str] = None,
    prefer_live: bool = False,
) -> Optional[Tuple[float, float, str]]:
    """Resolve ``(qty_step, min_qty, source)`` for *symbol*, or ``None``.

    ``None`` means "rule unknown" — the caller must NOT refuse on a venue-min
    basis (passthrough). ``source`` is ``"instrument_profile"`` or
    ``"live_lot_rule"``. ``profiles`` may be injected (tests); otherwise the
    cached ``instruments.yaml`` load is used.

    ``prefer_live``: when False (default), the offline ``InstrumentProfile``
    is authoritative and the live lot rule is the fallback — right for the
    coordinator's *sizing-time* guards (deterministic, no exchange round-trip
    on the hot path). When True, the LIVE lot rule (``get_lot_rule`` →
    cache/live/static) is preferred and the profile is the fallback — right
    for the ``_submit_order`` pre-flight, the last gate before the exchange,
    where the freshest venue truth matters and the profile only ADDS coverage
    for a symbol the live path can't resolve. With ``prefer_live=True`` this is
    a strict superset of the pre-fix ``get_lot_rule``-only resolution, so
    wiring it in never changes a verdict for a symbol that already resolved.
    """
    acct_exchange = str(account_cfg.get("exchange") or "").strip().lower()

    def _from_profile() -> Optional[Tuple[float, float, str]]:
        prof_map = profiles if profiles is not None else _load_profiles(instruments_path)
        prof = prof_map.get(symbol) if prof_map else None
        if prof is None:
            return None
        prof_exchange = str(getattr(prof, "exchange", "") or "").strip().lower()
        # Only trust the profile when its venue matches the account's (or
        # either is unknown) — guards against a same-named symbol on a
        # different venue borrowing the wrong lot.
        venue_matches = (
            not acct_exchange
            or not prof_exchange
            or prof_exchange in ("unknown",)
            or acct_exchange in ("unknown",)
            or prof_exchange == acct_exchange
        )
        step = float(getattr(prof, "qty_step", 0.0) or 0.0)
        vmin = float(getattr(prof, "min_qty", 0.0) or 0.0)
        if venue_matches and step > 0 and vmin > 0:
            return (step, vmin, "instrument_profile")
        return None

    def _from_live() -> Optional[Tuple[float, float, str]]:
        # Live venue lot rule (Bybit-only). Non-Bybit venues carry their own
        # whole-unit handling in risk.py, so they resolve None here.
        exchange = acct_exchange or "bybit"
        if exchange != "bybit":
            return None
        try:
            from src.units.accounts.execute import _bybit_category
            from src.units.accounts.precision import get_lot_rule
            category = _bybit_category(account_cfg)
            lot = get_lot_rule(client, symbol, category)
        except Exception as exc:  # noqa: BLE001 — never block on a lookup
            logger.debug(
                "qty_legalize: live lot-rule lookup failed for %s: %s", symbol, exc,
            )
            return None
        if lot is None:
            return None
        step_d, min_d = lot
        try:
            return (float(step_d), float(min_d), "live_lot_rule")
        except (TypeError, ValueError):
            return None

    order = (_from_live, _from_profile) if prefer_live else (_from_profile, _from_live)
    for resolver in order:
        result = resolver()
        if result is not None:
            return result
    return None  # rule unknown


def legalize_qty(
    qty: float,
    *,
    account_cfg: dict,
    symbol: str,
    client: Any = None,
    profiles: Optional[Dict[str, Any]] = None,
    instruments_path: Optional[str] = None,
    prefer_live: bool = False,
) -> LegalizedQty:
    """Turn *qty* into an exchange-legal quantity for *symbol* on this account.

    Floors *qty* DOWN to the venue's ``qty_step`` and refuses (``ok=False``,
    ``reason="below_venue_min_qty"``) when the floored value is below the
    venue's ``minOrderQty``. When no lot rule resolves the input passes through
    unchanged (``ok=True``, ``source="unknown"``) — byte-for-byte the current
    "rule unknown -> submit unmodified" contract, so wiring this seam in later
    can never *add* a refusal where there wasn't one.

    Never raises: any resolution error degrades to passthrough.
    """
    try:
        rule = _resolve_venue_lot_rule(
            symbol, account_cfg, client,
            profiles=profiles, instruments_path=instruments_path,
            prefer_live=prefer_live,
        )
    except Exception as exc:  # noqa: BLE001 — legalization must never crash the order path
        logger.warning("qty_legalize: resolution error for %s: %s — passthrough", symbol, exc)
        rule = None

    if rule is None:
        return LegalizedQty(
            qty=float(qty), ok=True, reason="",
            venue_min=None, step=None, source="unknown",
            qty_str=str(float(qty)),
        )

    step, venue_min, source = rule
    step_d = Decimal(str(step))
    # Floor DOWN to the step (never round up — realised risk must not exceed
    # the sized cap). Mirrors precision.quantize_qty exactly.
    try:
        from src.units.accounts.precision import quantize_qty
        aligned_d = Decimal(str(quantize_qty(float(qty), step_d)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("qty_legalize: quantize failed for %s: %s — passthrough", symbol, exc)
        return LegalizedQty(
            qty=float(qty), ok=True, reason="",
            venue_min=venue_min, step=step, source=source,
            qty_str=str(float(qty)),
        )

    min_d = Decimal(str(venue_min))
    aligned = float(aligned_d)
    aligned_str = str(aligned_d)  # step-precise wire string (keeps trailing zeros)
    if aligned_d <= 0 or aligned_d < min_d:
        return LegalizedQty(
            qty=aligned, ok=False, reason="below_venue_min_qty",
            venue_min=venue_min, step=step, source=source, qty_str=aligned_str,
        )
    return LegalizedQty(
        qty=aligned, ok=True, reason="",
        venue_min=venue_min, step=step, source=source, qty_str=aligned_str,
    )
