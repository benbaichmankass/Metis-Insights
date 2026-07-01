"""Prop manual-bridge inbound ingest (P2) — the report-back chokepoint.

One entry point, :func:`ingest_report`, that the REST endpoint
(``POST /api/bot/prop/report``) and any future automated executor call with a
report the operator/executor posts back after acting on a prop ticket. Two
report shapes (auto-detected, or set explicitly via ``kind``):

- **fill / close** (``kind="fill"``) — a ticket was placed and/or closed on the
  Breakout terminal. Journaled to ``prop_fills``, linked to its outbound ticket,
  and (when ``status="closed"``) fires the ``prop_closed`` notification — the
  trade-close follow-up the operator was missing. An ``status="open"`` /
  ``"filled"`` fill fires ``prop_fill``.

  **``status="placed"`` (a working order that has NOT filled yet)** is distinct
  from ``open``/``filled`` (a position that is actually live). A limit / pending
  order placed on the terminal but not yet tripped holds no position and has no
  P&L, so it advances the ticket to ``placed`` (NOT ``filled``) and fires **no**
  fill notification — the trade isn't open. When the limit later trips the
  operator reports ``open``/``filled`` and the ticket moves on to ``filled``.
  Lifecycle: ``emitted → [placed] → filled → closed`` (the ``placed`` step is
  optional — a market fill goes straight to ``filled``).
- **account status** (``kind="account_status"``) — balance / equity / day P&L /
  drawdown snapshot. Journaled to ``prop_account_status`` and surfaced on the
  dashboard's rule-distance panel.

Validation raises :class:`ValueError` on a structurally bad report (the router
turns that into a 400); everything past the write — the notification, the
ticket-status update — is best-effort and never fails the ingest.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from src.prop import prop_journal, prop_reconcile

logger = logging.getLogger(__name__)

_FILL_STATUSES = {"placed", "open", "filled", "closed", "skipped"}


def _infer_kind(report: Dict[str, Any]) -> str:
    if report.get("kind"):
        return str(report["kind"]).strip().lower()
    # An account-status report carries balance/equity but no fill semantics.
    if ("balance" in report or "equity" in report) and "status" not in report:
        return "account_status"
    return "fill"


def ingest_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Journal one inbound prop report; fire the notification; link the ticket."""
    if not isinstance(report, dict):
        raise ValueError("report must be a JSON object")
    account_id = str(report.get("account_id") or report.get("account") or "").strip()
    if not account_id:
        raise ValueError("report needs account_id")

    kind = _infer_kind(report)

    if kind == "account_status":
        row_id = prop_journal.insert_account_status({
            "account_id": account_id,
            "balance": report.get("balance"),
            "equity": report.get("equity"),
            "realized_today": report.get("realized_today"),
            "unrealized": report.get("unrealized"),
            "day_start_balance": report.get("day_start_balance"),
            "drawdown": report.get("drawdown"),
            "raw": report,
        })
        rule_distance = prop_reconcile.compute_rule_distance(account_id)
        return {
            "ok": True, "kind": "account_status", "id": row_id,
            "rule_distance": rule_distance,
        }

    # --- fill / close ---
    status = str(report.get("status") or "closed").strip().lower()
    if status not in _FILL_STATUSES:
        raise ValueError(
            f"fill status must be one of {sorted(_FILL_STATUSES)}, got {status!r}"
        )
    symbol = report.get("symbol")
    if not symbol:
        raise ValueError("fill report needs a symbol")

    # Normalise an inbound venue symbol (what the executor / operator typed on
    # the Breakout terminal, e.g. "ETHUSD") back to the bot's canonical symbol
    # ("ETHUSDT") — the journal + ticket reconciliation are keyed on canonical.
    # Passthrough for an already-canonical or unmapped symbol. The original
    # symbol is preserved verbatim in `raw` (the full report below). Best-effort:
    # a resolver hiccup must never fail the ingest.
    try:
        from src.prop.symbol_map import to_bot_symbol

        symbol = to_bot_symbol(symbol) or symbol
    except Exception as exc:  # noqa: BLE001 — never fail ingest over symbol mapping
        logger.warning("prop_report: symbol normalise failed for %s: %s", symbol, exc)

    fill = {
        "account_id": account_id,
        "ticket_id": report.get("ticket_id"),
        "external_order_id": report.get("external_order_id"),
        "symbol": symbol,
        "direction": report.get("direction"),
        "qty": report.get("qty"),
        "entry_price": report.get("entry_price"),
        "exit_price": report.get("exit_price"),
        "sl": report.get("sl"),
        "tp": report.get("tp"),
        "pnl": report.get("pnl"),
        "pnl_percent": report.get("pnl_percent"),
        "status": status,
        "reason": report.get("reason"),
        "opened_at": report.get("opened_at"),
        "closed_at": report.get("closed_at"),
        "raw": report,
    }

    # Link to the outbound ticket it came from (explicit id, else best match).
    try:
        fill["ticket_id"] = prop_reconcile.match_fill_to_ticket(fill)
    except Exception as exc:  # noqa: BLE001 — linking is best-effort
        logger.warning("prop_report: ticket match failed: %s", exc)

    row_id = prop_journal.insert_fill(fill)

    # Advance the matched ticket's lifecycle (best-effort). `placed` is the
    # working-order state (limit/pending order on the terminal, not yet filled)
    # and must NOT collapse into `filled` — that's the bug this separates.
    if fill.get("ticket_id"):
        try:
            new_status = (
                "closed" if status == "closed"
                else "skipped" if status == "skipped"
                else "placed" if status == "placed"
                else "filled")
            prop_journal.set_ticket_status(fill["ticket_id"], new_status)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prop_report: ticket status update failed: %s", exc)

    # Fire the notification (the missing 'second message'). Never fatal.
    # `placed` fires NOTHING here — a working order that hasn't filled is not a
    # trade event (no position, no P&L); the fill notification waits for the
    # actual open/filled report.
    notified = {"push": False, "telegram": False}
    if status in ("open", "filled", "closed"):
        try:
            from src.prop.breakout_notify import emit_prop_fill

            notified = emit_prop_fill(fill)
        except Exception as exc:  # noqa: BLE001 — notification never fatal
            logger.warning("prop_report: notification failed: %s", exc)

    return {
        "ok": True, "kind": "fill", "id": row_id,
        "status": status, "ticket_id": fill.get("ticket_id"),
        "notified": notified,
    }


__all__ = ["ingest_report"]
