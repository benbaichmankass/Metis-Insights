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

#: A real (non-backtest, non-demo) trade transitioned to ``status='closed'``.
#: Payload: ``trade_id, symbol, direction, pnl, pnl_percent, exit_reason,
#: strategy, account``. Emitted by ``Database._fire_trade_closed_event``
#: at the bottom of ``update_trade``. The most operationally important
#: kind — these are the money-event notifications.
TRADE_CLOSED: Final = "trade_closed"

#: Any message the bot would have sent to the operator's Telegram chat,
#: mirrored to FCM so the phone can show it without the user opening
#: Telegram. Payload: ``text, parse_mode``. Emitted by
#: ``src.runtime.notify._publish_telegram_to_fcm``. Default-on
#: subscription per the S2 wiring; some operators will want to silence
#: this once the trade-close + signal kinds are louder.
TELEGRAM: Final = "telegram"

# ---- RESERVED (Android UI offers toggle; bot-side caller lands later) -------

#: An ICT signal was detected and emitted to the order package layer.
#: Future payload (S5 wire): ``symbol, side, strategy, confidence,
#: pattern, price``. Useful for ``signal_emitted`` notifications that
#: precede the order — operators on noisy strategies will want this off
#: by default once it ships.
SIGNAL_EMITTED: Final = "signal_emitted"

#: A health-check transitioned to ``"concern"`` (the 7-point suite
#: produced a red status). Future payload: ``check, summary,
#: action_required``. Loud channel — these are reasons to look at the
#: dashboard now.
HEALTH_CONCERN: Final = "health_concern"

#: A systemd unit went inactive/failed. Future payload: ``unit, state,
#: sub_state``. Loud channel by design.
SERVICE_DOWN: Final = "service_down"

#: Daily / hourly P&L digest. Future payload: ``window, pnl, win_rate,
#: open_trades, summary_md``. Quiet by default — most operators will
#: want this only as a low-priority morning summary, not a system shade
#: pop.
PNL_DIGEST: Final = "pnl_digest"


#: Canonical list (insertion order = display order in the Android UI).
ALL_KINDS: Final[tuple[str, ...]] = (
    TRADE_CLOSED,
    TELEGRAM,
    SIGNAL_EMITTED,
    HEALTH_CONCERN,
    SERVICE_DOWN,
    PNL_DIGEST,
)

#: Human-readable label for each kind. Used by the Android UI to render
#: a toggle row and by the operator runbook to explain what each kind
#: covers. Kept on the bot side so the Python tests and the docs share
#: one source.
LABELS: Final[dict[str, str]] = {
    TRADE_CLOSED: "Trade closed",
    TELEGRAM: "Telegram mirror",
    SIGNAL_EMITTED: "Signal emitted",
    HEALTH_CONCERN: "Health concern",
    SERVICE_DOWN: "Service down",
    PNL_DIGEST: "P&L digest",
}

#: One-line description per kind for the Android subscription screen.
DESCRIPTIONS: Final[dict[str, str]] = {
    TRADE_CLOSED: "Every closed real-money trade (not backtests / demos).",
    TELEGRAM: "Every message the bot would have sent to Telegram.",
    SIGNAL_EMITTED: "Each ICT detection — reserved, lands in M12 S5.",
    HEALTH_CONCERN: "7-point health check turned red — reserved, M12 S6.",
    SERVICE_DOWN: "systemd unit failed — reserved, M12 S6.",
    PNL_DIGEST: "Daily / hourly P&L summary — reserved, M12 S7.",
}

#: The subset of kinds whose payload semantics the bot already emits.
#: Tests + the runbook lean on this so a regression that drops a real
#: call site (e.g. someone refactors ``_fire_trade_closed_event`` away)
#: is loud.
IN_FLIGHT: Final[frozenset[str]] = frozenset({TRADE_CLOSED, TELEGRAM})


def is_known(kind: str) -> bool:
    """Return True if ``kind`` is one of the canonical event kinds.

    Used by the device-registration endpoint to reject typos in the
    Android-supplied ``subscriptions`` list before they end up in the
    DB and silently never match a publish.
    """
    return kind in LABELS


__all__ = [
    "TRADE_CLOSED",
    "TELEGRAM",
    "SIGNAL_EMITTED",
    "HEALTH_CONCERN",
    "SERVICE_DOWN",
    "PNL_DIGEST",
    "ALL_KINDS",
    "LABELS",
    "DESCRIPTIONS",
    "IN_FLIGHT",
    "is_known",
]
