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

#: A trade (real OR paper — backtests excluded) was just inserted into
#: the ``trades`` table at ``status='open'``. Payload: ``trade_id, symbol,
#: direction, qty, entry_price, sl, tp, strategy, account, account_class,
#: is_paper``. Emitted by ``Database._fire_trade_opened_event`` at the
#: bottom of ``insert_trade`` (via ``mobile_push.trade_events`` → typed
#: FCM + Telegram). Pairs with ``TRADE_CLOSED`` so the operator's phone
#: shows the full trade lifecycle, not just the close.
TRADE_OPENED: Final = "trade_opened"

#: An existing open trade row had its SL or TP moved (monitor-driven
#: trail, BE flip, partial-fill adjustment). Payload: ``trade_id,
#: symbol, sl, tp, strategy, account``. Emitted by
#: ``Database._fire_trade_updated_event`` from ``update_trade`` when
#: ``sl`` or ``tp`` are in the update set and the row isn't closing.
TRADE_UPDATED: Final = "trade_updated"

#: A trade (real OR paper — backtests excluded) transitioned to
#: ``status='closed'``. Payload: ``trade_id, symbol, direction, pnl,
#: pnl_percent, exit_reason, strategy, account, account_class, is_paper``.
#: Emitted by ``Database._fire_trade_closed_event`` at the bottom of
#: ``update_trade``. The most operationally important kind — these are
#: the money-event notifications (paper tagged via ``is_paper``).
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

#: A Breakout prop-firm "trade setup" ticket — a paste-ready bracket-order
#: instruction (entry band / SL / TP / size / validity) for the prop account.
#: Payload: ``strategy, symbol, side, direction, entry, entry_min, entry_max,
#: sl, tp, rr, qty, risk_usd, valid_until, text`` (``text`` carries the full
#: rendered ticket for the notification body). Emitted by
#: ``src.prop.breakout_notify.emit_prop_signal`` (Breakout POC). Kept distinct
#: from the live ``trade_opened`` / ``trade_closed`` money events so the
#: operator can route/silence it on its own Android channel.
PROP_SIGNAL: Final = "prop_signal"

#: A Breakout prop ticket was reported FILLED / opened on the terminal — the
#: inbound report-back the operator/executor posts after placing a ticket
#: (manual-bridge P2). Payload: ``account, symbol, direction, qty, entry_price,
#: ticket_id, external_order_id``. Emitted by ``src.prop.prop_report.ingest_report``.
#: Distinct from the live ``trade_opened`` money event — prop is a third funding
#: class, tracked separately.
PROP_FILL: Final = "prop_fill"

#: A Breakout prop trade was reported CLOSED — the trade-close follow-up the
#: operator was missing (manual-bridge P2). Payload: ``account, symbol,
#: direction, pnl, pnl_percent, exit_price, reason, ticket_id``. Emitted by
#: ``src.prop.prop_report.ingest_report`` when a fill/close report carries
#: ``status='closed'``. Distinct from the live ``trade_closed`` money event.
PROP_CLOSED: Final = "prop_closed"

#: A periodic "still monitoring" heartbeat for an OPEN prop trade. The prop
#: account has no broker API, so the bot can't push real-time fills — this
#: pulse (default every 15 min while a prop position is open) reassures the
#: operator the system is still actively tracking the trade, even when there's
#: nothing to report ("no change"). Does NOT replace the real-time
#: ``prop_fill`` / ``prop_closed`` events — it's the liveness signal between
#: them. Payload: ``account, symbol, direction, qty, entry, sl, tp,
#: opened_at, age_minutes, ticket_id, text``. Emitted by
#: ``src.prop.prop_monitor_pulse.run_prop_monitor_pulse``.
PROP_MONITOR: Final = "prop_monitor"

#: One-shot alert when an open prop trade's current price crosses its SL or TP
#: level. Since the prop account has no broker feed the bot can't know when the
#: executor actually closed the trade; this ping prompts the operator to check
#: and report back, with a pre-filled JSON report template in the message body.
#: Fires at most once per level (SL and TP each get one alert) per open
#: position lifetime — cleared only when the fill closes.
#: Payload: ``account, symbol, direction, level_type (sl|tp), current_price,
#: sl, tp, entry, ticket_id, age_minutes, text``.
#: Emitted by ``src.prop.prop_sl_tp_alert.run_prop_sl_tp_alert``.
PROP_SL_TP_ALERT: Final = "prop_sl_tp_alert"


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
    PROP_SIGNAL,
    PROP_FILL,
    PROP_CLOSED,
    PROP_MONITOR,
    PROP_SL_TP_ALERT,
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
    PROP_SIGNAL: "Prop trade setup",
    PROP_FILL: "Prop trade filled",
    PROP_CLOSED: "Prop trade closed",
    PROP_MONITOR: "Prop trade monitoring pulse",
    PROP_SL_TP_ALERT: "Prop SL/TP crossing alert",
}

#: One-line description per kind for the Android subscription screen.
DESCRIPTIONS: Final[dict[str, str]] = {
    TRADE_OPENED: "Each new trade (real or paper) as it opens.",
    TRADE_UPDATED: "Stop-loss / take-profit moves on an open trade (real or paper).",
    TRADE_CLOSED: "Every closed trade — real or paper (backtests excluded).",
    HOURLY_SUMMARY: "The hourly operator summary — P&L, open trades, win rate.",
    WARNING: "Watchdog, IB Gateway, health-red, service-down, risk-cap.",
    WORKFLOW_UPDATE: "GitHub Actions ops workflow finished (deploy, restart, mode flip).",
    TELEGRAM: "Mirror of every Telegram message (catch-all fallback).",
    PNL_DIGEST: "Legacy — use Hourly summary.",
    HEALTH_CONCERN: "Legacy — use Warning.",
    SERVICE_DOWN: "Legacy — use Warning.",
    SIGNAL_EMITTED: "Each ICT detection — fires once per buy/sell signal.",
    PROP_SIGNAL: "Breakout prop-firm trade-setup tickets — entry/SL/TP to place.",
    PROP_FILL: "A prop ticket reported filled/opened on the terminal.",
    PROP_CLOSED: "A prop trade reported closed — PnL + exit reason.",
    PROP_MONITOR: "Periodic 'still monitoring' pulse while a prop trade is open.",
    PROP_SL_TP_ALERT: "One-shot alert when a prop trade's price crosses its SL or TP — prompts close report.",
}

#: The subset of kinds whose payload semantics the bot already emits.
#: Tests + the runbook lean on this so a regression that drops a real
#: call site (e.g. someone refactors ``_fire_trade_closed_event`` away)
#: is loud.
IN_FLIGHT: Final[frozenset[str]] = frozenset(
    {TRADE_OPENED, TRADE_UPDATED, TRADE_CLOSED, TELEGRAM, SIGNAL_EMITTED,
     PROP_SIGNAL, PROP_FILL, PROP_CLOSED, PROP_MONITOR, PROP_SL_TP_ALERT}
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
    "PROP_SIGNAL",
    "PROP_FILL",
    "PROP_CLOSED",
    "PROP_MONITOR",
    "PROP_SL_TP_ALERT",
    "ALL_KINDS",
    "LABELS",
    "DESCRIPTIONS",
    "IN_FLIGHT",
    "is_known",
]
