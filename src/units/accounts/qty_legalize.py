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

# ---------------------------------------------------------------------------
# The venue per-order CEILING is a THREE-state answer, never a nullable float
# (2026-09-02, BL-20260902-AVAX-VENUE-MAX-CLAMP-INERT-WHEN-THE-LIVE-LOOKUP-MISSES;
# mechanism filed as BL-20260814-VENUE-MAX-NONE-CANNOT-SAY-WE-COULD-NOT-LOOK).
#
# ⚠️ THIS IS THE THIRD OCCURRENCE OF THE SAME LIVE DEFECT. BL-20260810 shipped
# the clamp (and was marked `resolved`); BL-20260821 recorded it rejecting
# again at ~34,000; on 2026-09-02 `ict_scalp_avax_5m` sent qty 22995.1 against
# a 22,000 cap eight times. The CLAMP was correct every time. What was wrong is
# that `venue_max=None` meant three different things and the clamp treated all
# three as "no ceiling exists — place it":
#
#   published       the venue named a ceiling                  -> CLAMP
#   absent          we read the venue's own lotSizeFilter and
#                   it carries no ceiling                      -> place unmodified
#   could_not_look  nothing that can SPEAK to a ceiling
#                   answered (live lookup failed/empty; the
#                   static map, which holds step/min only; an
#                   InstrumentProfile with no max_qty)         -> place, but SAY SO
#
# Only `absent` is evidence that placing unclamped is safe. `could_not_look` is
# the absence of evidence, and reading it as `absent` is what made the clamp a
# silent no-op on the one path already known to reject.
#
# ⚠️ `could_not_look` deliberately does NOT refuse. The clamp's whole safety
# argument is that it cannot alter an order the venue would have accepted;
# refusing on an unresolved ceiling would start blocking orders that are legal
# today (every non-Bybit venue and every static-map symbol resolve here), which
# is a far larger blast radius than the bug. It places — and is now legible
# instead of silent, which is what lets a fourth occurrence be seen.
MAX_STATE_PUBLISHED = "published"
MAX_STATE_ABSENT = "absent"
MAX_STATE_COULD_NOT_LOOK = "could_not_look"


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
    # The venue's per-order CEILING, and whether this qty was cut down to it
    # (2026-08-13, BL-20260810-ICTSCALP-AVAX-QTY-EXCEEDS-VENUE-MAX).
    #
    # ``venue_max=None`` means the venue published no maximum — NOT that the
    # maximum is zero, and not that the qty is under it. ``clamped`` is a
    # separate field rather than something a caller infers by comparing qty to
    # venue_max, because an input that happened to equal the cap exactly is
    # NOT a clamp and must not be logged or journalled as one.
    venue_max: Optional[float] = None
    clamped: bool = False
    # WHY `venue_max` is None, which the float alone cannot say. One of
    # MAX_STATE_PUBLISHED / MAX_STATE_ABSENT / MAX_STATE_COULD_NOT_LOOK.
    # Defaults to `could_not_look` because a construction that says nothing
    # about a ceiling has not established that there is none — the honest
    # default is "nobody looked", never "there is no limit".
    venue_max_state: str = MAX_STATE_COULD_NOT_LOOK
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
) -> Optional[Tuple[float, float, Optional[float], str, str]]:
    """Resolve ``(qty_step, min_qty, max_qty|None, max_state, source)``, or ``None``.

    The FIFTH element (2026-09-02) says WHY ``max_qty`` is ``None``, which the
    float alone cannot. See the module header: only ``MAX_STATE_ABSENT``
    licenses placing an order unclamped.

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

    def _from_profile() -> Optional[Tuple[float, float, Optional[float], str, str]]:
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
            # The profile MAY now state a ceiling (`max_qty`, added 2026-09-02).
            # When it does not, the state is COULD_NOT_LOOK, never ABSENT — this
            # source cannot speak to the venue's ceiling, and the old code's own
            # comment said exactly that while returning a value that read as
            # "no ceiling". That gap is the third-occurrence defect.
            raw_max = getattr(prof, "max_qty", None)
            vmax: Optional[float] = None
            if raw_max is not None:
                try:
                    cand = float(raw_max)
                    if cand > 0:
                        vmax = cand
                except (TypeError, ValueError):
                    vmax = None
            max_state = (
                MAX_STATE_PUBLISHED if vmax is not None
                else MAX_STATE_COULD_NOT_LOOK
            )
            return (step, vmin, vmax, max_state, "instrument_profile")
        return None

    def _from_live() -> Optional[Tuple[float, float, Optional[float], str, str]]:
        # Live venue lot rule (Bybit-only). Non-Bybit venues carry their own
        # whole-unit handling in risk.py, so they resolve None here.
        exchange = acct_exchange or "bybit"
        if exchange != "bybit":
            return None
        try:
            from src.units.accounts.execute import _bybit_category
            from src.units.accounts.precision import get_lot_bounds_stated
            category = _bybit_category(account_cfg)
            # STATED, not `get_lot_bounds`: the plain form collapses "the venue
            # published no ceiling" with "the static map answered and cannot
            # speak to one", and the clamp needs them apart.
            lot = get_lot_bounds_stated(client, symbol, category)
        except Exception as exc:  # noqa: BLE001 — never block on a lookup
            logger.debug(
                "qty_legalize: live lot-rule lookup failed for %s: %s", symbol, exc,
            )
            return None
        if lot is None:
            return None
        step_d, min_d, max_d, max_state = lot
        try:
            return (float(step_d), float(min_d),
                    float(max_d) if max_d is not None else None,
                    max_state, "live_lot_rule")
        except (TypeError, ValueError):
            return None

    order = (_from_live, _from_profile) if prefer_live else (_from_profile, _from_live)
    chosen = None
    for idx, resolver in enumerate(order):
        result = resolver()
        if result is not None:
            chosen = (idx, result)
            break
    if chosen is None:
        return None  # rule unknown

    idx, (step, vmin, vmax, max_state, source) = chosen
    # CEILING-ONLY cross-consult. The winning source decides step/min/source
    # exactly as before — this can change NOTHING but the ceiling — but when it
    # cannot speak to a ceiling we ask the other source rather than reporting
    # "we could not look" while a published answer sits one resolver away.
    # Concretely: a symbol in `precision._STATIC_LOT_RULE` resolves from the
    # static map (which carries step/min only) whenever the live lookup misses,
    # and would otherwise never consult the profile's `max_qty`.
    if max_state == MAX_STATE_COULD_NOT_LOOK:
        other = order[1 - idx]()
        if other is not None and other[3] == MAX_STATE_PUBLISHED:
            vmax, max_state = other[2], other[3]
    return (step, vmin, vmax, max_state, source)


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
        # No lot rule at all: WE COULD NOT LOOK. Passthrough is unchanged (the
        # pre-seam contract — this must never ADD a refusal), but it no longer
        # claims the venue has no ceiling.
        return LegalizedQty(
            qty=float(qty), ok=True, reason="",
            venue_min=None, step=None, source="unknown",
            qty_str=str(float(qty)),
            venue_max_state=MAX_STATE_COULD_NOT_LOOK,
        )

    step, venue_min, venue_max, venue_max_state, source = rule
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
            venue_max=venue_max, venue_max_state=venue_max_state,
        )

    min_d = Decimal(str(venue_min))
    aligned = float(aligned_d)
    aligned_str = str(aligned_d)  # step-precise wire string (keeps trailing zeros)
    if aligned_d <= 0 or aligned_d < min_d:
        return LegalizedQty(
            qty=aligned, ok=False, reason="below_venue_min_qty",
            venue_min=venue_min, step=step, source=source, qty_str=aligned_str,
            venue_max=venue_max, venue_max_state=venue_max_state,
        )

    # --- venue per-order CEILING (2026-08-13) -----------------------------
    # BL-20260810-ICTSCALP-AVAX-QTY-EXCEEDS-VENUE-MAX
    # (id kept on ONE line: wrapping it mid-token hides it from every grep AND
    # from the tracking-ref guard, which then reads it as a reference to a row
    # that was never filed.)
    #
    # Symmetric with the floor above and deliberately in the SAME seam: a
    # second bespoke max-check elsewhere is how the four scattered minimum
    # checks this module exists to replace came about.
    #
    # CLAMP, not refuse. Three reasons, in order of weight:
    #   1. It CANNOT change an order the venue would have accepted. The branch
    #      is entered only when aligned > max, and any such order is bounced by
    #      the venue today. So the blast radius is exactly the set of currently
    #      FAILING orders — the safety argument is structural, not empirical.
    #   2. It matches the established local idiom: risk.py already CLAMPS for
    #      its two ceilings (max_qty_by_margin, max_qty_by_exposure) rather
    #      than refusing. A ceiling clamps here.
    #   3. It is strictly risk-REDUCING — the resulting position is smaller
    #      than the risk model asked for, never larger.
    # Splitting across several orders was rejected: under one-way netting the
    # legs merge into one position anyway, so it buys nothing and adds a
    # multi-order failure mode to the last gate before the exchange.
    clamped = False
    # Keyed on the STATE, never on `venue_max is not None` (2026-09-02). The
    # null test read `could_not_look` as "no ceiling exists" and skipped the
    # clamp — that is the whole third-occurrence defect, at one line. Only a
    # PUBLISHED ceiling is a ceiling; the other two states carry no cap to
    # apply, and `venue_max` is None under both by construction.
    if venue_max_state == MAX_STATE_PUBLISHED and venue_max is not None:
        max_d = Decimal(str(venue_max))
        if max_d > 0 and aligned_d > max_d:
            # Floor the CAP to the step — the cap itself need not be a
            # multiple of it, and an unaligned qty is its own rejection.
            try:
                from src.units.accounts.precision import quantize_qty
                capped_d = Decimal(str(quantize_qty(float(max_d), step_d)))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "qty_legalize: cap quantize failed for %s: %s — submitting unclamped",
                    symbol, exc,
                )
                capped_d = None
            if capped_d is not None:
                if capped_d < min_d:
                    # max < min is a contradictory venue rule. Refuse rather
                    # than emit a knowingly-illegal qty; do not "fix" it by
                    # picking one of the two bounds.
                    return LegalizedQty(
                        qty=float(capped_d), ok=False,
                        reason="venue_max_below_min_qty",
                        venue_min=venue_min, step=step, source=source,
                        qty_str=str(capped_d), venue_max=venue_max,
                        venue_max_state=venue_max_state,
                    )
                logger.warning(
                    "qty_legalize: %s qty %s exceeds venue maxOrderQty %s — "
                    "clamping to %s (source=%s)",
                    symbol, aligned_str, venue_max, capped_d, source,
                )
                aligned_d = capped_d
                aligned = float(aligned_d)
                aligned_str = str(aligned_d)
                clamped = True

    return LegalizedQty(
        qty=aligned, ok=True, reason="",
        venue_min=venue_min, step=step, source=source, qty_str=aligned_str,
        venue_max=venue_max, clamped=clamped, venue_max_state=venue_max_state,
    )


def instrument_lot(
    symbol: str,
    *,
    exchange: str = "",
    client: Any = None,
    prefer_live: bool = False,
    instruments_path: Optional[str] = None,
) -> Optional[Tuple[float, float]]:
    """Public ``(qty_step, min_qty)`` resolver for *symbol*, or ``None``.

    The offline-friendly lot lookup the RiskManager uses to make its sub-min
    refusal + flooring INSTRUMENT-aware (Phase 3,
    BL-20260628-CRYPTO-INSTRUMENT-MIN-FLOOR) — resolving from the SAME
    ``InstrumentProfile`` (``config/instruments.yaml``) the guards resolve from,
    so there is one source of truth for the venue minimum. ``None`` when the
    symbol has no profile and no live rule (caller falls back to its own
    default). ``exchange``/``client`` are optional — the sizing layer resolves
    offline by symbol and passes neither. Never raises.
    """
    try:
        rule = _resolve_venue_lot_rule(
            symbol, {"exchange": exchange}, client,
            prefer_live=prefer_live, instruments_path=instruments_path,
        )
    except Exception as exc:  # noqa: BLE001 — never block sizing on a lookup
        logger.debug("instrument_lot: resolution error for %s: %s", symbol, exc)
        return None
    if rule is None:
        return None
    step, vmin, _vmax, _max_state, _source = rule
    return (step, vmin)
