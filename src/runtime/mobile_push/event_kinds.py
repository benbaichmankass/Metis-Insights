"""Canonical event-kind taxonomy for the mobile-push notifier (M12 S4).

Every notification fanned out via ``src.runtime.mobile_push.publish_event``
carries a stable string ``kind`` that serves three purposes:

  1. **Server-side routing.** ``FcmNotifier.publish_to_subscribers`` filters
     by the device's ``subscriptions`` JSON column — a device can opt in or
     out per kind without touching the FCM token.
  2. **Client-side routing.** ``IctMessagingService`` on the Android side
     pivots on the kind to pick a NotificationChannel (so the operator can
     silence ``telegram`` in the system shade while keeping ``trade_closed``
     loud), and to choose the title/body composer.
  3. **Operator subscription UI.** The Android Notifications screen
     enumerates these kinds as toggles. Adding a kind here makes it
     toggleable; the bot side and app side stay in sync because both
     import from this module (Python side directly; Android side mirrors
     the names in `feature/notifications/EventKind.kt`).

Renaming a kind in this module is a breaking change for already-installed
apps — the device's saved ``subscriptions`` JSON references the old name
and the operator silently stops receiving the renamed kind. Never rename
in place; add a new kind, fan out to both, and remove the old one in a
follow-up sprint once telemetry confirms zero devices still subscribed to
the old name.

The kinds below are split into:

- **In flight.** A call site already emits this kind today.
- **Reserved.** Listed here so the Android subscription UI can offer the
  toggle ahead of the bot side wiring (no harm: subscribing to a kind that
  no caller emits is a no-op). M12 S4 reserves the slots that S5/S6/S7
  will fill, so the operator's preferences survive across sprints.
"""
from __future__ import annotations

from typing import Final

# ---- IN FLIGHT --------------------------------------------------------------

#: A real (non-backtest, non-demo) trade was just inserted into the
#: ``trades`` table at ``status='open'``. Payload: ``trade_id, symbol,
#: direction, qty, entry_price, sl, tp, strategy, account``. Emitted by
#: ``Database._fire_trade_opened_event`` at the bottom of
#: ``insert_trade``. Pairs with ``TRADE_CLOSED`` so the operator's phone
#: shows the full trade lifecycle, not just the close.
TRADE_OPENED: Final = "trade_opened"

#: An existing open trade row had its SL or TP moved (monitor-driven
#: trail, BE flip, partial-fill adjustment). Payload: ``trade_id,
#: symbol, sl, tp, strategy, account``. Emitted by
#: ``Database._fire_trade_updated_event`` from ``update_trade`` when
#: ``sl`` or ``tp`` are in the update set and the row isn't closing.
TRADE_UPDATED: Final = "trade_updated"

#: A real (non-backtest, non-demo) trade transitioned to ``status='closed'``.
#: Payload: ``trade_id, symbol, direction, pnl, pnl_percent, exit_reason,
#: strategy, account``. Emitted by ``Database._fire_trade_closed_event``
#: at the bottom of ``update_trade``. The most operationally important
#: kind — these are the money-event notifications.
TRADE_CLOSED: Final = "trade_closed"

#: Hourly operator summary — open positions, 24h P&L, win rate. Fired
#: by ``scripts/send_hourly_now.py`` and the in-process hourly report
#: scheduler. Payload mirrors the Telegram body. Renames the legacy
#: ``pnl_digest`` slot; ``pnl_digest`` stays in ``ALL_KINDS`` so already
#: installed apps whose subscription JSON references it keep working.
HOURLY_SUMMARY: Final = "hourly_summary"

#: Operator-actionable warning — watchdog stale-heartbeat, IB Gateway
#: wedge, 7-point health red, systemd unit failed, risk caps violated.
#: Loud channel by design; rolls up the legacy ``health_concern`` and
#: ``service_down`` kinds (both kept in ``ALL_KINDS`` for back-compat).
WARNING: Final = "warning"

#: GitHub Actions workflow finished a system-action on the live VM
#: (deploy, restart, account-mode flip, etc.). Payload: ``action,
#: status, run_url, reason``. Fired by the operator workflow after the
#: action lands; mirrored to Telegram by the same helper.
WORKFLOW_UPDATE: Final = "workflow_update"

#: Any message the bot would have sent to the operator's Telegram chat,
#: mirrored to FCM so the phone can show it without the user opening
#: Telegram. Payload: ``text, parse_mode``. Emitted by
#: ``src.runtime.notify._publish_telegram_to_fcm``. Default-on
#: subscription per the S2 wiring; once the trade / hourly / warning /
#: workflow kinds cover their content, expect to demote this kind so
#: the same event doesn't fire two pushes.
TELEGRAM: Final = "telegram"

# ---- DEPRECATED (kept in ALL_KINDS so already-installed devices' --------
# ---- subscription JSON keeps resolving; rotation per the rename rule) ---

#: Legacy kind — superseded by ``HOURLY_SUMMARY``. Kept so already
#: installed apps whose subscription JSON references it keep working.
PNL_DIGEST: Final = "pnl_digest"

#: Legacy kind — rolled up into ``WARNING``.
HEALTH_CONCERN: Final = "health_concern"

#: Legacy kind — rolled up into ``WARNING``.
SERVICE_DOWN: Final = "service_down"

# ---- RESERVED (Android UI offers toggle; bot-side caller lands later) ---

#: An ICT signal was detected and emitted to the order package layer.
#: Future payload: ``symbol, side, strategy, confidence, pattern, price``.
SIGNAL_EMITTED: Final = "signal_emitted"


#: Canonical list (insertion order = display order in the Android UI).
#: The operator's four categories first, then the legacy / reserved
#: slots.
ALL_KINDS: Final[tuple[str, ...]] = (
    TRADE_OPENED,
    TRADE_UPDATED,
    TRADE_CLOSED,
    HOURLY_SUMMARY,
    WARNING,
    WORKFLOW_UPDATE,
    TELEGRAM,
    PNL_DIGEST,
    HEALTH_CONCERN,
    SERVICE_DOWN,
    SIGNAL_EMITTED,
)

#: Human-readable label for each kind. Used by the Android UI to render
#: a toggle row and by the operator runbook to explain what each kind
#: covers. Kept on the bot side so the Python tests and the docs share
#: one source.
LABELS: Final[dict[str, str]] = {
    TRADE_OPENED: "Trade opened",
    TRADE_UPDATED: "Trade updated (SL/TP)",
    TRADE_CLOSED: "Trade closed",
    HOURLY_SUMMARY: "Hourly summary",
    WARNING: "Warning",
    WORKFLOW_UPDATE: "Workflow update",
    TELEGRAM: "Telegram mirror",
    PNL_DIGEST: "P&L digest (legacy)",
    HEALTH_CONCERN: "Health concern (legacy)",
    SERVICE_DOWN: "Service down (legacy)",
    SIGNAL_EMITTED: "Signal emitted",
}

#: One-line description per kind for the Android subscription screen.
DESCRIPTIONS: Final[dict[str, str]] = {
    TRADE_OPENED: "Each new real-money trade as it opens.",
    TRADE_UPDATED: "Stop-loss / take-profit moves on an open trade.",
    TRADE_CLOSED: "Every closed real-money trade (not backtests / demos).",
    HOURLY_SUMMARY: "The hourly operator summary — P&L, open trades, win rate.",
    WARNING: "Watchdog, IB Gateway, health-red, service-down, risk-cap.",
    WORKFLOW_UPDATE: "GitHub Actions ops workflow finished (deploy, restart, mode flip).",
    TELEGRAM: "Mirror of every Telegram message (catch-all fallback).",
    PNL_DIGEST: "Legacy — use Hourly summary.",
    HEALTH_CONCERN: "Legacy — use Warning.",
    SERVICE_DOWN: "Legacy — use Warning.",
    SIGNAL_EMITTED: "Each ICT detection — fires once per buy/sell signal.",
}

#: The subset of kinds whose payload semantics the bot already emits.
#: Tests + the runbook lean on this so a regression that drops a real
#: call site (e.g. someone refactors ``_fire_trade_closed_event`` away)
#: is loud.
IN_FLIGHT: Final[frozenset[str]] = frozenset(
    {TRADE_OPENED, TRADE_UPDATED, TRADE_CLOSED, TELEGRAM, SIGNAL_EMITTED}
)


def is_known(kind: str) -> bool:
    """Return True if ``kind`` is one of the canonical event kinds.

    Used by the device-registration endpoint to reject typos in the
    Android-supplied ``subscriptions`` list before they end up in the
    DB and silently never match a publish.
    """
    return kind in LABELS


__all__ = [
    "TRADE_OPENED",
    "TRADE_UPDATED",
    "TRADE_CLOSED",
    "HOURLY_SUMMARY",
    "WARNING",
    "WORKFLOW_UPDATE",
    "TELEGRAM",
    "PNL_DIGEST",
    "HEALTH_CONCERN",
    "SERVICE_DOWN",
    "SIGNAL_EMITTED",
    "ALL_KINDS",
    "LABELS",
    "DESCRIPTIONS",
    "IN_FLIGHT",
    "is_known",
]
