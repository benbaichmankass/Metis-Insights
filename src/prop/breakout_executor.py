"""Breakout prop 'executor' — emit a Telegram/FCM ticket instead of a broker call.

The Breakout prop account is driven by the **manual browser-bridge** POC
(`docs/integrations/breakout-poc-manual-bridge-DESIGN.md`): the bot never places
a live order on Breakout's DXTrade terminal itself. Instead, when a prop-routed
strategy fires, this module turns the order into a paste-ready **trade-setup
ticket** and emits it as a typed ``prop_signal`` (FCM push + Telegram) for a
human / assistant to place under supervision. The broker-side bracket (SL+TP at
entry) is the real-time safety net; our side is notify + journal only.

It is wired as the ``EXCHANGE_MAP["breakout"]`` / ``execute._submit_order``
branch so an account with ``exchange: breakout`` flows through the normal
order path, but the "placement" is a ticket emission — NO exchange socket is
opened, and the returned id is a **manual-fill marker** (``prop-manual-<uuid>``)
so the order package is journaled WITHOUT a real exchange position the monitor
would try to reconcile/close. A live fill only exists once a human places it and
reports back (the design's inbound ``/prop_report`` path).

Tier-1 to format/emit (a message, not an order); the order-path WIRING that
routes a live prop account here is Tier-3 (accounts.yaml). Best-effort: a
notification failure logs a WARNING but the journal row is still written, so the
operator sees the decision even if the push/telegram leg dropped.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MANUAL_FILL_PREFIX = "prop-manual-"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTING_PATH = _REPO_ROOT / "config" / "prop_rulesets" / "breakout_routing.yaml"

# trend_donchian fires on 2h bars (flagship) — the ticket TTL is timeframe-aware,
# so a sensible per-strategy default keeps a stale setup from being placed late.
_DEFAULT_TIMEFRAME = "2h"


def is_manual_fill_id(trade_id: Any) -> bool:
    """True when *trade_id* is a Breakout manual-fill marker (no live position)."""
    return isinstance(trade_id, str) and trade_id.startswith(MANUAL_FILL_PREFIX)


def _load_routing() -> Dict[str, Any]:
    try:
        import yaml
        with open(_ROUTING_PATH) as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001 — fall back to defaults, never raise
        logger.warning("breakout_executor: routing load failed (%s); using defaults", exc)
        return {}


def _per_symbol(routing: Dict[str, Any], symbol: str, key: str, default: Any) -> Any:
    """Read a per-symbol override from routing[symbols][SYMBOL][key], else top-level."""
    sym_block = ((routing.get("symbols") or {}).get(symbol) or {})
    if key in sym_block and sym_block[key] is not None:
        return sym_block[key]
    if key in routing and routing[key] is not None:
        return routing[key]
    return default


def _reticket_suppress_reason(
    account_id: str, symbol: str, direction: str,
) -> Optional[str]:
    """Reason to SUPPRESS a new ticket for (account, symbol, direction), or None.

    ONE TICKET PER TRADE (operator directive 2026-07-05, BL-20260705-PROP-
    RETICKET-WHILE-OPEN): the manual bridge re-fired fresh tickets every time a
    prop-routed strategy re-signalled — two new ETHUSDT-long tickets landed at
    10:05Z/10:08Z while the operator was already holding the 08:06Z fill.
    Suppress when either:

    - an OPEN prop position exists for the key (newest ``prop_fills`` row is
      ``open``/``filled`` — the same derivation the monitor pulse uses), or
    - a still-LIVE outstanding ticket exists: ``placed`` (working order on the
      terminal), ``expiry_prompted``/``awaiting_report`` (operator mid-dialog),
      or ``emitted`` whose ``valid_until`` has not passed. An EXPIRED unacted
      ticket does NOT block — a fresh signal after the old setup went stale is
      a new trade decision.

    Fail-OPEN: any journal read error returns None so a genuine trade is never
    stranded by a read hiccup (same posture as the reconciler guards).
    """
    try:
        from src.prop.prop_monitor_pulse import find_open_prop_positions

        sym = str(symbol or "").upper()
        d = str(direction or "").lower()
        for pos in find_open_prop_positions(account_id=account_id):
            if (str(pos.get("symbol") or "").upper() == sym
                    and str(pos.get("direction") or "").lower() == d):
                return (
                    f"open_position: {pos.get('qty')} @ {pos.get('entry_price')} "
                    f"since {pos.get('opened_at')} (ticket {pos.get('ticket_id')})"
                )

        from src.prop import prop_journal

        now = datetime.now(timezone.utc)
        for t in prop_journal.list_tickets(account_id=account_id, limit=200):
            if (str(t.get("symbol") or "").upper() != sym
                    or str(t.get("direction") or "").lower() != d):
                continue
            status = str(t.get("status") or "").lower()
            if status in ("placed", "expiry_prompted", "awaiting_report"):
                return f"outstanding_ticket:{status}: {t.get('ticket_id')}"
            if status == "emitted":
                vu = t.get("valid_until")
                try:
                    vu_dt = datetime.fromisoformat(
                        str(vu).replace("Z", "+00:00")) if vu else None
                    if vu_dt is not None and vu_dt.tzinfo is None:
                        vu_dt = vu_dt.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    vu_dt = None
                if vu_dt is None or vu_dt > now:
                    return f"outstanding_ticket:emitted: {t.get('ticket_id')}"
    except Exception as exc:  # noqa: BLE001 — fail-open, never strand a trade
        logger.warning(
            "breakout_executor: reticket guard read failed for %s/%s/%s (%s) — "
            "allowing emission", account_id, symbol, direction, exc,
        )
    return None


def emit_prop_ticket(
    order: Dict[str, Any],
    account_cfg: Dict[str, Any],
    *,
    timeframe: Optional[str] = None,
    _emitter: Any = None,
) -> str:
    """Build this account's leg from its ruleset and emit it as a ``prop_signal``.

    Uses the canonical prop-accounts architecture (DESIGN §3/§4): the account
    resolves to its :class:`~src.prop.account_rulesets.AccountBacktestUnit` via
    ``unit_for_account`` (so sizing comes from the account's own ruleset/risk —
    no hardcoded size), and the per-account leg is built with
    ``src.prop.multi_account_ticket.build_account_leg``. The leg's ticket is
    emitted (FCM + the prop Telegram bot). A ``skip`` leg (size rounds to zero /
    invalid) is journaled without a push.

    Returns a ``prop-manual-<uuid>`` trade id (a manual-fill marker — the order
    package journals, but no live exchange position is created). Raises only on a
    structurally invalid order (missing entry/sl/tp). A notification-delivery
    failure is swallowed (logged) so the journal row is never lost.

    ``_emitter`` is an injection seam for tests (defaults to
    ``src.prop.breakout_notify.emit_prop_signal``).
    """
    from src.prop.breakout_ticket import BreakoutSignal
    from src.prop.multi_account_ticket import build_account_leg
    from src.prop.account_rulesets import unit_for_account

    symbol = str(order.get("symbol") or "")
    direction = str(order.get("direction") or "").lower()
    if direction not in ("long", "short"):
        # _submit_order gives side Buy/Sell; map if direction absent
        side = str(order.get("side") or "").lower()
        direction = "long" if side in ("buy", "b") else "short"
    entry = float(order.get("entry") or 0.0)
    sl = float(order.get("sl") or 0.0)
    tp = float(order.get("tp") or 0.0)
    strategy = str(order.get("strategy") or account_cfg.get("account_id") or "prop")
    if entry <= 0 or sl <= 0 or tp <= 0:
        raise ValueError(
            f"breakout_executor: ticket needs positive entry/sl/tp; got "
            f"entry={entry} sl={sl} tp={tp} for {symbol}"
        )

    account_id = str(account_cfg.get("account_id") or account_cfg.get("id") or "breakout")

    # ONE TICKET PER TRADE: a held position or a still-live outstanding ticket
    # for this (account, symbol, direction) suppresses a fresh emission — the
    # suppressed row is journaled (status 'suppressed', no push) so the decision
    # stays auditable without paging the operator again.
    suppress = _reticket_suppress_reason(account_id, symbol, direction)
    if suppress:
        trade_id = f"{MANUAL_FILL_PREFIX}{uuid.uuid4().hex[:12]}"
        logger.info(
            "breakout_executor: %s reticket SUPPRESSED for %s %s (%s) → %s",
            account_id, symbol, direction, suppress, trade_id,
        )
        try:
            from src.prop import prop_journal

            prop_journal.record_ticket({
                "ticket_id": trade_id,
                "account_id": account_id,
                "strategy": strategy,
                "symbol": symbol,
                "direction": direction,
                "entry": entry, "sl": sl, "tp": tp,
                "signal_time": datetime.now(timezone.utc).isoformat(),
                "status": "suppressed",
                "message": f"reticket suppressed — {suppress}",
                "order_package_id": order.get("order_package_id") or (
                    order["meta"].get("order_package_id")
                    if isinstance(order.get("meta"), dict) else None
                ),
            })
        except Exception as exc:  # noqa: BLE001 — audit row is best-effort
            logger.warning(
                "breakout_executor: suppressed-ticket journal write failed: %s", exc)
        return trade_id

    routing = _load_routing()
    sig = BreakoutSignal(
        strategy=strategy, symbol=symbol, direction=direction,
        entry=entry, sl=sl, tp=tp,
        timeframe=str(timeframe or _DEFAULT_TIMEFRAME),
        signal_time=datetime.now(timezone.utc),
    )
    unit = unit_for_account(account_id, account_cfg)
    leg = build_account_leg(
        sig, unit,
        dxtrade_symbol=_per_symbol(routing, symbol, "dxtrade_symbol", None),
        contract_value_usd_per_point=float(
            _per_symbol(routing, symbol, "contract_value_usd_per_point", 1.0)),
        entry_band_frac=float(routing.get("entry_band_frac") or 0.25),
        ttl_bars=float(routing.get("ttl_bars") or 1.0),
    )

    trade_id = f"{MANUAL_FILL_PREFIX}{uuid.uuid4().hex[:12]}"
    if leg.decision != "place" or leg.ticket is None:
        logger.info(
            "breakout_executor: %s leg SKIP for %s (%s) — journaled, no push → %s",
            account_id, symbol, leg.reason, trade_id,
        )
        return trade_id

    # P3 observe-only soak: log the laddered ticket that WOULD be emitted (the
    # materialized ExitPlan sized against this leg) next to the single-target
    # ticket actually sent. Best-effort — never changes or blocks the emission.
    try:
        from src.runtime.exit_ladder_soak import record_exit_ladder_soak
        record_exit_ladder_soak(
            venue="prop",
            strategy=sig.strategy, symbol=symbol, direction=sig.direction,
            entry=sig.entry, sl=sig.sl, tp=sig.tp, qty=leg.ticket.qty_units,
            account_id=account_id,
            account_class=str(getattr(leg, "account_class", "") or ""),
            timeframe=sig.timeframe,
            order_meta=(order.get("meta") if isinstance(order.get("meta"), dict) else None),
            extra={"side": leg.ticket.side, "rr": leg.ticket.rr,
                   "qty_units": leg.ticket.qty_units},
        )
    except Exception as exc:  # noqa: BLE001 — observe-only metadata
        logger.debug("exit_ladder_soak(prop) skipped for %s: %s", symbol, exc)

    # Record the OUTBOUND ticket to the prop journal so the inbound report-back
    # (P2) can reconcile a fill against it and un-acted tickets are detectable
    # (P3). Best-effort — a journal hiccup must never block the emission.
    try:
        from src.prop import prop_journal

        # Capture the rendered ticket text so the dashboard can show the exact
        # message that was sent out (best-effort — a render hiccup just stores
        # no message, never blocks the journal write).
        ticket_message = None
        try:
            from src.prop.breakout_notify import ticket_to_fields

            ticket_message = ticket_to_fields(
                leg.ticket, account_id=account_id, ticket_id=trade_id).get("text")
        except Exception:  # noqa: BLE001 — message capture is cosmetic
            ticket_message = None

        prop_journal.record_ticket({
            "ticket_id": trade_id,
            "account_id": account_id,
            "strategy": sig.strategy,
            "symbol": symbol,
            "direction": sig.direction,
            "side": leg.ticket.side,
            "entry": sig.entry,
            "sl": sig.sl,
            "tp": sig.tp,
            "qty": leg.ticket.qty_units,
            "risk_usd": leg.ticket.risk_usd,
            "signal_time": sig.signal_time.isoformat(),
            "valid_until": leg.ticket.valid_until.isoformat(),
            "status": "emitted",
            # The execute_pkg breakout branch passes the package id in
            # order["meta"]["order_package_id"] (the order dict has no top-level
            # key), so the previous order.get("order_package_id") was ALWAYS
            # None — every prop_tickets row had a null order_package_id, breaking
            # the ticket↔order-package join the dashboard prop view + reconcile
            # rely on. Read from meta, top-level as a fallback for any other
            # caller that does set it directly.
            "order_package_id": (
                order.get("order_package_id")
                or (order.get("meta") or {}).get("order_package_id")
            ),
            "message": ticket_message,
        })
    except Exception as exc:  # noqa: BLE001 — journaling never blocks emission
        logger.warning("breakout_executor: ticket journal failed for %s: %s",
                       symbol, exc)

    try:
        if _emitter is not None:
            # Injected emitter (tests) keeps the simple (ticket) signature.
            _emitter(leg.ticket)
        else:
            from src.prop.breakout_notify import emit_prop_signal
            # Pass the account + ticket id so the rendered ticket's report-back
            # JSON block is pre-filled — this is what lets the executor reply
            # with a copy-paste fill the inbound ingest accepts verbatim.
            emit_prop_signal(leg.ticket, account_id=account_id, ticket_id=trade_id)
    except Exception as exc:  # noqa: BLE001 — never lose the journal row over a push
        logger.warning("breakout_executor: ticket emit failed for %s: %s", symbol, exc)

    logger.info(
        "breakout_executor: emitted prop ticket %s %s entry=%s sl=%s tp=%s "
        "(risk $%.2f) → %s (manual fill — no live position created)",
        symbol, sig.direction, entry, sl, tp, leg.ticket.risk_usd, trade_id,
    )
    return trade_id
