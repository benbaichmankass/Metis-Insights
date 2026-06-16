"""Trade-lifecycle notification dispatch — open / close / update.

A single chokepoint the journal's ``_fire_trade_*`` observers call so a
trade event reaches the operator on **both** channels at once:

  1. **Typed FCM push** — ``publish_event(kind, payload)`` for the
     ``trade_opened`` / ``trade_closed`` / ``trade_updated`` kind, routed
     per-device subscription. Gated by ``MOBILE_PUSH_ENABLED`` inside
     ``publish_event`` (a no-op when off / no credentials).
  2. **Telegram message** — a concise human line to the operator's chat
     via ``send_telegram_direct``. The mirror-to-FCM is disabled on this
     send (``mirror_to_fcm=False``) so the phone doesn't get a second,
     generic ``telegram`` push on top of the typed one.

Both real-money and **paper** trades fire (only backtest replays are
skipped — see the journal observers). The operator asked for paper
opens/closes/updates too, so the funding class rides in the payload
(``account_class`` / ``is_paper``) and the message is tagged ``[paper]``
or ``[live]`` rather than suppressed.

Trust contract (mirrors ``mobile_push``):

- **Off the trader's thread.** The Telegram + FCM HTTP I/O runs on a
  short-lived daemon thread so a slow/unreachable Telegram or FCM can
  never add latency to the order-record (``insert_trade``) or monitor
  close (``update_trade``) path. Notifications are best-effort; a missed
  one is acceptable, a stalled trader loop is not.
- **Never raises into the caller.** Every failure path is swallowed +
  logged.
- **Reductive.** Informs the operator only; holds no decision authority.

Configuration (environment):

- ``TRADE_EVENT_TELEGRAM_DISABLED`` — truthy to suppress the per-trade
  Telegram line (rollback lever; default off → Telegram on). The typed
  FCM push stays governed by ``MOBILE_PUSH_ENABLED``. This is a
  notification side-channel switch, not a trading gate.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "y", "on"}

# Kinds this module knows how to format. Kept loose — an unknown kind
# still dispatches with a generic line so a future kind isn't dropped.
TRADE_OPENED = "trade_opened"
TRADE_CLOSED = "trade_closed"
TRADE_UPDATED = "trade_updated"


def _telegram_enabled() -> bool:
    # allow-silent: notification side-channel kill-switch (mirrors
    # MOBILE_PUSH_ENABLED), NOT a trading/live-dry gate — the BUG-039
    # rule targets *_ENABLED/*_DISABLED flags that strand order-path
    # capability; this one only mutes the per-trade Telegram line.
    raw = os.environ.get("TRADE_EVENT_TELEGRAM_DISABLED", "")  # allow-silent: see above — comms side-channel, not a trading gate
    return raw.strip().lower() not in _TRUTHY


def _fmt_num(value: Any) -> str:
    """Compact numeric formatting that tolerates None / non-numbers."""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        # Trim trailing zeros without scientific notation for typical
        # prices / pnl while keeping small crypto sizes readable.
        return f"{value:,.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def _class_tag(payload: dict[str, Any]) -> str:
    ac = payload.get("account_class")
    if ac:
        return "paper" if str(ac).strip().lower() == "paper" else "live"
    return "paper" if payload.get("is_paper") else "live"


def format_trade_event_message(kind: str, payload: dict[str, Any]) -> str:
    """Build the plain-text Telegram line for a trade event."""
    symbol = payload.get("symbol", "?")
    direction = str(payload.get("direction", "")).upper()
    strategy = payload.get("strategy") or "?"
    account = payload.get("account") or "?"
    tag = _class_tag(payload)

    if kind == TRADE_CLOSED:
        pnl = payload.get("pnl")
        pnl_pct = payload.get("pnl_percent")
        emoji = "🔴"
        if isinstance(pnl, (int, float)):
            emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
        pnl_str = _fmt_num(pnl)
        pct_str = f" ({_fmt_num(pnl_pct)}%)" if pnl_pct is not None else ""
        reason = payload.get("exit_reason") or "—"
        return (
            f"{emoji} CLOSE {symbol} {direction} [{tag}] "
            f"PnL {pnl_str}{pct_str} · {reason} · {strategy}/{account}"
        )

    if kind == TRADE_UPDATED:
        return (
            f"✏️ UPDATE {symbol} {direction} [{tag}] "
            f"SL {_fmt_num(payload.get('sl'))} · TP {_fmt_num(payload.get('tp'))} "
            f"· {strategy}/{account}"
        )

    # Default + TRADE_OPENED.
    return (
        f"🟩 OPEN {symbol} {direction} [{tag}] "
        f"{_fmt_num(payload.get('qty'))} @ {_fmt_num(payload.get('entry_price'))} "
        f"· SL {_fmt_num(payload.get('sl'))} · TP {_fmt_num(payload.get('tp'))} "
        f"· {strategy}/{account}"
    )


def _send_telegram(kind: str, payload: dict[str, Any]) -> None:
    """Telegram line for a trade event. Best-effort; runs on a worker thread."""
    try:
        from src.runtime.notify import send_telegram_direct

        send_telegram_direct(
            format_trade_event_message(kind, payload),
            parse_mode=None,
            # We already fired the typed FCM push below — disable the
            # generic ``telegram`` mirror so the phone isn't double-notified.
            mirror_to_fcm=False,
        )
    except Exception as exc:  # noqa: BLE001 — notification must never raise
        logger.warning("trade_events: Telegram send failed (%s): %s", kind, exc)


def notify_trade_event(kind: str, payload: dict[str, Any]) -> None:
    """Fan a trade event out to FCM (typed push) + Telegram.

    The FCM publish runs inline — it mirrors the pre-existing observer-hook
    behaviour (and is gated off by default / fast when on). The Telegram
    send is dispatched to a short-lived daemon thread so a slow/unreachable
    Telegram can never add latency to ``insert_trade`` / ``update_trade``.
    Best-effort throughout — never raises into the caller.
    """
    # 1) Typed FCM push — inline. Resolved at call time so tests that
    #    monkeypatch ``src.runtime.mobile_push.publish_event`` observe it.
    try:
        from src.runtime.mobile_push import publish_event

        publish_event(kind, payload)
    except Exception as exc:  # noqa: BLE001 — notification must never raise
        logger.warning("trade_events: FCM publish failed (%s): %s", kind, exc)

    # 2) Telegram line — off-thread, mirror disabled (no double push).
    if not _telegram_enabled():
        return
    try:
        threading.Thread(
            target=_send_telegram,
            args=(kind, dict(payload)),
            name="trade-event-telegram",
            daemon=True,
        ).start()
    except Exception as exc:  # noqa: BLE001 — even thread spawn must not raise
        logger.warning("trade_events: Telegram thread failed to start: %s", exc)


__all__ = [
    "notify_trade_event",
    "format_trade_event_message",
    "TRADE_OPENED",
    "TRADE_CLOSED",
    "TRADE_UPDATED",
]
