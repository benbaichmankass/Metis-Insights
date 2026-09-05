"""Signal-to-OrderPackage bridge — extracted from pipeline.py (PR-9 / D1).

Converts a pipeline-shape signal dict (``{symbol, side, price/entry_price,
stop_loss, take_profit, meta}``) into the ``OrderPackage`` the Coordinator
expects (``direction``, ``entry``, ``sl``, ``tp``). Kept as a thin module so
pipeline.py and tests can import it without dragging in the full coordinator.
"""
from __future__ import annotations

from typing import Any, Dict


def _signal_to_order_package(signal: Dict[str, Any], settings: dict):
    """Build an ``OrderPackage`` from a pipeline signal dict.

    The signal shape is what every builder in this module produces:
    ``{symbol, side, price/entry_price, stop_loss, take_profit,
    meta: {strategy_name, ...}}`` — S-026 G1: no qty (sizing is the
    per-account RiskManager's job in G2). The Coordinator's
    per-account dispatch path consumes ``OrderPackage``, which has a
    slightly different shape (``direction`` instead of ``side``,
    ``entry`` / ``sl`` / ``tp``). This helper bridges the two so we
    can fan a pipeline-generated signal out to every account in
    ``config/accounts.yaml`` without changing the strategy builders.
    """
    from src.core.coordinator import OrderPackage

    meta = dict(signal.get("meta") or {})
    side = str(signal.get("side", "")).strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError(
            f"_signal_to_order_package: side must be buy/sell, got {side!r}"
        )
    direction = "long" if side == "buy" else "short"

    entry = signal.get("entry_price") or signal.get("price") or meta.get("price")
    sl = signal.get("stop_loss") or meta.get("stop_loss") or meta.get("sl")
    tp = signal.get("take_profit") or meta.get("take_profit") or meta.get("tp")
    if entry is None or sl is None or tp is None:
        raise ValueError(
            "_signal_to_order_package: signal missing entry/sl/tp "
            f"(entry={entry!r}, sl={sl!r}, tp={tp!r}); strategy must "
            "populate price+stop_loss+take_profit before fan-out."
        )

    strategy = (
        meta.get("strategy_name")
        or signal.get("strategy")
        or settings.get("STRATEGY")
        or "unknown"
    )
    return OrderPackage(
        strategy=str(strategy),
        symbol=str(signal.get("symbol") or settings.get("SYMBOL") or "BTCUSDT"),
        direction=direction,
        entry=float(entry),
        sl=float(sl),
        tp=float(tp),
        confidence=float(meta.get("confidence") or 0.0),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Signal identity (BL-20260905 — empty-sizing brake)
# ---------------------------------------------------------------------------
#
# The empty-sizing brake must refuse re-emission of THE SAME signal without
# muting the next one, so it needs an identity that is stable across ticks and
# different for a genuinely new signal. On 2026-06-01 all seven
# ``mes_trend_long_1d`` packages shared one ``entry_time``
# ('2026-06-01 00:00:00') and one ``donchian_hi`` (7611.75): one daily signal
# re-emitted seven times, not seven signals. Geometry + entry_time is exactly
# what separated those two readings.
#
# ⚠️ ONE derivation, TWO call sites. The gate sees a pipeline *signal dict*
# and the coordinator sees an *OrderPackage*; a second copy of "how a signal
# maps to entry/sl/tp/direction" is how the two would silently disagree and
# the brake would stop matching its own refusals. So the signal-side helper
# NORMALISES THROUGH ``_signal_to_order_package`` — the package builder is the
# normaliser — and both sides then hash the same package fields.

def signal_key_for_package(pkg: Any) -> str:
    """Stable identity for the signal *pkg* represents.

    Hashes strategy + symbol + direction + entry/sl/tp (rounded to 8 dp so a
    float round-trip through JSON/SQLite cannot change the key) plus
    ``meta['entry_time']`` when the strategy publishes one. Returns a short
    hex digest; never raises (an underivable key returns ``""``, which every
    caller treats as "no identity — do not brake").
    """
    import hashlib

    try:
        meta = getattr(pkg, "meta", None) or {}
        entry_time = meta.get("entry_time") if isinstance(meta, dict) else None
        parts = [
            str(getattr(pkg, "strategy", "") or ""),
            str(getattr(pkg, "symbol", "") or ""),
            str(getattr(pkg, "direction", "") or ""),
            f"{float(getattr(pkg, 'entry', 0.0) or 0.0):.8f}",
            f"{float(getattr(pkg, 'sl', 0.0) or 0.0):.8f}",
            f"{float(getattr(pkg, 'tp', 0.0) or 0.0):.8f}",
            str(entry_time or ""),
        ]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001 — an identity helper must never break dispatch
        return ""


def signal_key_for_signal(
    signal: Dict[str, Any], settings: Dict[str, Any] | None = None
) -> str:
    """``signal_key_for_package`` for a pipeline signal dict.

    Returns ``""`` when the signal cannot be turned into a package at all
    (missing entry/sl/tp) — an un-keyable signal is never braked, which
    degrades to the pre-2026-09-05 behaviour rather than guessing.
    """
    try:
        return signal_key_for_package(
            _signal_to_order_package(signal, settings or {})
        )
    except Exception:  # noqa: BLE001
        return ""
