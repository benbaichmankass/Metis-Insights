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
import re
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


def routing_path_for(ruleset_path: Optional[str] = None) -> Path:
    """The routing file for an account's ruleset: the ruleset's own
    ``routing:`` key (``config/``-relative) when it declares one, else
    ``breakout_routing.yaml`` — which is what every account read before a
    second prop account existed, and what ``breakout.yaml`` (no ``routing:``
    key) still resolves to (TRADEIFY-WIRE 2026-09-30)."""
    if not ruleset_path:
        return _ROUTING_PATH
    import yaml
    with open(ruleset_path) as fh:
        spec = (yaml.safe_load(fh) or {}).get("routing")
    return (_REPO_ROOT / "config" / str(spec)) if spec else _ROUTING_PATH


def _load_routing(ruleset_path: Optional[str] = None) -> Dict[str, Any]:
    try:
        import yaml
        with open(routing_path_for(ruleset_path)) as fh:
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


#: Statuses that block a re-ticket for their key with NO time window: a working
#: order, an operator "yes, placed" never reported, or a phone attempt in flight
#: (``claimed``; the phone's claim watchdog ends it within CLAIM_TIMEOUT_S).
#: Each may be a live position the fills journal cannot see yet.
_BLOCKING_UNTIL_RESOLVED = ("placed", "awaiting_report", "claimed")
#: Statuses that block only while the ticket's validity has not passed (or
#: cannot be read). ``expiry_prompted`` and ``invalidated_prompted`` used to
#: block for ``valid_until`` + a 24 h STALE_PROMPT_GRACE; retired by operator
#: directive 2026-10-07 ("if a ticket expires and I haven't logged a trade, the
#: system should assume that the trade wasnt placed and the ticket should be
#: kept alive"). MEASURED that day: velotrade_1's expired ETH-short ticket sat
#: in ``expiry_prompted`` and suppressed four signals for 17 h.
_BLOCKING_WHILE_VALID = ("emitted", "expiry_prompted", "invalidated_prompted")


def _parse_valid_until(vu: Any) -> Optional[datetime]:
    """``valid_until`` as an aware UTC datetime, or None when absent / unparseable."""
    if not vu:
        return None
    try:
        dt = datetime.fromisoformat(str(vu).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _reticket_suppress_reason(
    account_id: str, symbol: str, direction: str, *, now: Optional[datetime] = None,
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
      terminal), ``awaiting_report`` (operator answered "yes, placed" and never
      reported the fill) or ``claimed`` (a phone attempt in flight) -- each may
      be a live position the fills journal cannot see, so they block until
      resolved, with NO time window (manager decision 2026-09-29 17:24Z); or
      ``emitted`` / ``expiry_prompted`` / ``invalidated_prompted`` whose
      ``valid_until`` has not passed, or cannot be read (fail-safe).

    An EXPIRED ticket with no logged fill never blocks, whatever prompt it
    carries: it was not placed and the opportunity stays alive (operator
    directive 2026-10-07; docs/ARCHITECTURE-CANONICAL.md § "Prop ticket flow").

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

        now = now or datetime.now(timezone.utc)
        for t in prop_journal.list_tickets(account_id=account_id, limit=200):
            if (str(t.get("symbol") or "").upper() != sym
                    or str(t.get("direction") or "").lower() != d):
                continue
            status = str(t.get("status") or "").lower()
            if status in _BLOCKING_UNTIL_RESOLVED:
                # Blocks until reported, NO time window (manager decision
                # 2026-09-29 17:24Z: a doubled prop position costs more than
                # one lost signal -- this is the fail-safe side).
                return f"outstanding_ticket:{status}: {t.get('ticket_id')}"
            if status in _BLOCKING_WHILE_VALID:
                vu_dt = _parse_valid_until(t.get("valid_until"))
                if vu_dt is None or vu_dt > now:
                    return f"outstanding_ticket:{status}: {t.get('ticket_id')}"
    except Exception as exc:  # noqa: BLE001 — fail-open, never strand a trade
        logger.warning(
            "breakout_executor: reticket guard read failed for %s/%s/%s (%s) — "
            "allowing emission", account_id, symbol, direction, exc,
        )
    return None


_PLACED_BLOCKER_RE = re.compile(r"^outstanding_ticket:placed:\s*(prop-[a-z]+-[0-9a-f]+)$")


def _supersede_candidate(
    suppress: str, *, account_id: str, account_cfg: Dict[str, Any], trade_id: str,
    strategy: str, symbol: str, direction: str, entry: float, sl: float, tp: float,
    timeframe: str, now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """The supersede candidate a suppressed ticket carries, or None.

    OPERATOR DECISION 2026-10-08 ~20:03Z ("Approve as proposed", pipeline item
    PI-20261008-3QRUJSYR-0001, checklist row PROP-SUPERSEDE): a new signal for
    the same (account, strategy, symbol, direction) whose outstanding ticket is
    ``placed`` may supersede that resting entry. Emission has no terminal read,
    so it only MARKS the candidate; the executor, holding the read, decides and
    performs cancel -> confirm -> old ``skipped: superseded by <new>`` -> new
    released (``prop_supersede.release``).

    A candidate is recorded only when the blocker is a ``placed`` ticket of the
    SAME strategy on this account, and the ticket rebuilds with emission's own
    functions (``prop_ticket_reissue.rebuild_fields``): its rendered message
    carries the entry band the executor checks BEFORE it cancels anything.
    Every other suppression (an open position, ``awaiting_report``,
    ``claimed``, an ``emitted`` ticket, another strategy) records no candidate
    and blocks exactly as before. Any error records no candidate (fail closed:
    the block stands)."""
    m = _PLACED_BLOCKER_RE.match(str(suppress or ""))
    if not m:
        return None
    blocker_id = m.group(1)
    try:
        from src.prop import prop_journal
        from scripts.ops.prop_ticket_reissue import rebuild_fields

        blocker = prop_journal.get_ticket(blocker_id)
        if (not blocker or str(blocker.get("account_id") or "") != account_id
                or str(blocker.get("status") or "").lower() != "placed"
                or str(blocker.get("strategy") or "") != strategy):
            return None
        now = now or datetime.now(timezone.utc)
        fields, why = rebuild_fields(
            {"ticket_id": trade_id, "account_id": account_id, "strategy": strategy,
             "symbol": symbol, "direction": direction, "entry": entry, "sl": sl, "tp": tp},
            account_cfg, now, timeframe=timeframe)
        if fields is None:
            logger.info("breakout_executor: %s no supersede candidate for %s (%s)",
                        trade_id, blocker_id, why)
            return None
        return {"blocked_by": blocker_id, "at": now.isoformat(),
                "message": fields["message"], "valid_until": fields["valid_until"]}
    except Exception as exc:  # noqa: BLE001 — no candidate: the block stands
        logger.warning("breakout_executor: supersede candidate failed for %s (%s); block stands",
                       trade_id, exc)
        return None


def _leverage_refusal(account_id: str, unit: Any, symbol: str, qty_units: Any, entry: Any,
                      cvpp: float) -> Optional[str]:
    """The ruleset's leverage-cap breach reason for this ticket, or None.

    Basis = min(nominal account size, live sizing balance when readable). A
    declared cap that cannot be checked is a breach (could not look)."""
    from src.prop import prop_rule_guards

    source = getattr(unit, "source", None)
    if source and not Path(source).is_file():
        # The account DECLARES a ruleset but it cannot be read: fail closed
        # rather than read "no rule declared" (T2 review nit,
        # PI-20260930-BHYHMK2H-0001).
        return f"leverage: ruleset {Path(source).name} unreadable (could not look)"
    limits = prop_rule_guards.load_limits(source)
    if source and not limits:
        return f"leverage: ruleset {Path(source).name} has no readable limits (could not look)"
    caps = prop_rule_guards.leverage_caps(limits)
    if not caps:
        return None
    live: list = []
    try:
        from src.prop import prop_balance

        state, bal, _meta = prop_balance.prop_sizing_balance(account_id)
        if state == "ok":
            live.append(bal)
    except Exception as exc:  # noqa: BLE001 — the nominal size still bounds it
        logger.warning("breakout_executor: leverage basis balance read failed for %s: %s", account_id, exc)
    size = (getattr(unit, "account_size_usd", None)
            or getattr(getattr(unit, "ruleset", None), "account_size_usd", None))
    try:
        notional = float(qty_units) * float(entry) * float(cvpp)
    except (TypeError, ValueError):
        notional = None
    return prop_rule_guards.leverage_breach(
        symbol=symbol, notional_usd=notional, caps=caps,
        basis_usd=prop_rule_guards.leverage_basis(size, live))


def emit_prop_ticket(
    order: Dict[str, Any],
    account_cfg: Dict[str, Any],
    *,
    timeframe: Optional[str] = None,
    _emitter: Any = None,
    test_ping: bool = False,
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

    ``test_ping=True`` (the ``send-prop-test-ping`` action only) journals the
    ticket as ``status='test_ping'`` instead of ``'emitted'`` and skips the
    exit-ladder soak record. FIX-CA-11: an ``emitted`` row is what
    ``_reticket_suppress_reason`` (and the expiry/invalidation/reconcile
    scans) key on, so a synthetic test ticket used to suppress the next real
    signal for the same (account, symbol, direction) until it expired.
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
        candidate = _supersede_candidate(
            suppress, account_id=account_id, account_cfg=account_cfg, trade_id=trade_id,
            strategy=strategy, symbol=symbol, direction=direction, entry=entry, sl=sl, tp=tp,
            timeframe=str(timeframe or _DEFAULT_TIMEFRAME))
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
                **({"meta": {"supersede": candidate}} if candidate else {}),
            })
        except Exception as exc:  # noqa: BLE001 — audit row is best-effort
            logger.warning(
                "breakout_executor: suppressed-ticket journal write failed: %s", exc)
        return trade_id

    sig = BreakoutSignal(
        strategy=strategy, symbol=symbol, direction=direction,
        entry=entry, sl=sl, tp=tp,
        timeframe=str(timeframe or _DEFAULT_TIMEFRAME),
        signal_time=datetime.now(timezone.utc),
    )
    unit = unit_for_account(account_id, account_cfg)
    # Routing follows the account's ruleset (its `routing:` key); breakout.yaml
    # declares none, so breakout_1 reads breakout_routing.yaml exactly as before.
    routing = _load_routing(unit.source)

    # SIZING MODE (operator 2026-09-27 ~11:12Z, declared in the ruleset's
    # `sizing:` block): `flat` returns no override and reads nothing, so the
    # ticket below is the pre-existing ticket byte for byte; `room` sizes
    # min(risk_pct x live balance, k x binding cushion) and skips below the
    # minimum or when the cushion cannot be read. See src/prop/prop_sizing.py.
    from src.prop import prop_sizing
    sizing = prop_sizing.resolve(
        account_id, ruleset_path=unit.source, risk_pct=unit.risk_pct,
        strategy=strategy)
    if sizing.skip_reason:
        trade_id = f"{MANUAL_FILL_PREFIX}{uuid.uuid4().hex[:12]}"
        logger.info(
            "breakout_executor: %s leg SKIPPED by %s sizing for %s %s (%s) → %s",
            account_id, sizing.mode, symbol, direction, sizing.skip_reason, trade_id,
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
                "signal_time": sig.signal_time.isoformat(),
                "status": "skipped",
                "message": sizing.skip_reason,
                "order_package_id": order.get("order_package_id") or (
                    order["meta"].get("order_package_id")
                    if isinstance(order.get("meta"), dict) else None
                ),
                "meta": {"sizing_mode": sizing.mode, "sizing": sizing.detail},
            })
        except Exception as exc:  # noqa: BLE001 — audit row is best-effort
            logger.warning(
                "breakout_executor: sizing-skip journal write failed: %s", exc)
        return trade_id

    leg = build_account_leg(
        sig, unit,
        dxtrade_symbol=_per_symbol(routing, symbol, "dxtrade_symbol", None),
        contract_value_usd_per_point=float(
            _per_symbol(routing, symbol, "contract_value_usd_per_point", 1.0)),
        entry_band_frac=float(routing.get("entry_band_frac") or 0.25),
        ttl_bars=float(routing.get("ttl_bars") or 1.0),
        risk_usd_override=sizing.risk_usd,
    )

    trade_id = f"{MANUAL_FILL_PREFIX}{uuid.uuid4().hex[:12]}"

    # RISK GATE, ENFORCE (operator 2026-09-27 ~08:40Z, PI-20260924-MQ3CDMU6-0002):
    # hold the ticket to the CURRENT sizing mode's configured cap. `off` /
    # `annotate` never touch the size; under `enforce` a flat $75 ticket is AT
    # its $75 cap, so it is unchanged and not rebuilt (byte-identical).
    gate_cap = None  # set only when the gate RESIZED the ticket
    if leg.decision == "place" and leg.ticket is not None:
        from src.prop import prop_risk_gate
        cap = prop_risk_gate.enforce_ticket_cap(
            risk_usd=leg.ticket.risk_usd, cap_usd=sizing.cap_usd, sizing_mode=sizing.mode)
        if cap["action"] == prop_risk_gate.CAP_RESIZED:
            logger.warning("breakout_executor: %s %s %s — %s",
                           account_id, symbol, direction, cap["cause"])
            gate_cap = cap
            leg = build_account_leg(
                sig, unit,
                dxtrade_symbol=_per_symbol(routing, symbol, "dxtrade_symbol", None),
                contract_value_usd_per_point=float(
                    _per_symbol(routing, symbol, "contract_value_usd_per_point", 1.0)),
                entry_band_frac=float(routing.get("entry_band_frac") or 0.25),
                ttl_bars=float(routing.get("ttl_bars") or 1.0),
                risk_usd_override=cap["risk_usd"],
            )
        elif cap["action"] == prop_risk_gate.CAP_REFUSED:
            logger.warning("breakout_executor: %s %s %s REFUSED — %s → %s",
                           account_id, symbol, direction, cap["cause"], trade_id)
            try:
                from src.prop import prop_journal

                prop_journal.record_ticket({
                    "ticket_id": trade_id, "account_id": account_id,
                    "strategy": strategy, "symbol": symbol, "direction": direction,
                    "entry": entry, "sl": sl, "tp": tp,
                    "signal_time": sig.signal_time.isoformat(),
                    "status": "skipped", "message": cap["cause"],
                    "order_package_id": (order.get("order_package_id")
                                         or (order.get("meta") or {}).get("order_package_id")),
                    "meta": {"risk_gate": cap},
                })
            except Exception as exc:  # noqa: BLE001 — audit row is best-effort
                logger.warning("breakout_executor: gate-refusal journal write failed: %s", exc)
            return trade_id

    if leg.decision != "place" or leg.ticket is None:
        logger.info(
            "breakout_executor: %s leg SKIP for %s (%s) — journaled, no push → %s",
            account_id, symbol, leg.reason, trade_id,
        )
        return trade_id

    # FIRM LEVERAGE CAP (TRADEIFY-WIRE T2; declared per ruleset, breakout.yaml
    # declares none so breakout_1 never reaches the refusal). A breach item:
    # `enforce` refuses the ticket, `report` logs it and emits.
    lev_reason = _leverage_refusal(account_id, unit, symbol, leg.ticket.qty_units, sig.entry,
                                   float(_per_symbol(routing, symbol, "contract_value_usd_per_point", 1.0)))
    if lev_reason:
        from src.prop import prop_risk_gate

        if prop_risk_gate.breach_guards_for(account_id) == "report":
            logger.warning("breakout_executor: %s %s %s — %s (breach_guards=report, emitting anyway)",
                           account_id, symbol, direction, lev_reason)
        else:
            logger.warning("breakout_executor: %s %s %s REFUSED — %s → %s",
                           account_id, symbol, direction, lev_reason, trade_id)
            try:
                from src.prop import prop_journal

                prop_journal.record_ticket({
                    "ticket_id": trade_id, "account_id": account_id,
                    "strategy": strategy, "symbol": symbol, "direction": direction,
                    "entry": entry, "sl": sl, "tp": tp,
                    "signal_time": sig.signal_time.isoformat(),
                    "status": "skipped", "message": lev_reason,
                    "order_package_id": (order.get("order_package_id")
                                         or (order.get("meta") or {}).get("order_package_id")),
                    "meta": {"rule_guard": "leverage"},
                })
            except Exception as exc:  # noqa: BLE001 — audit row is best-effort
                logger.warning("breakout_executor: leverage-refusal journal write failed: %s", exc)
            return trade_id

    # P3 observe-only soak: log the laddered ticket that WOULD be emitted (the
    # materialized ExitPlan sized against this leg) next to the single-target
    # ticket actually sent. Best-effort — never changes or blocks the emission.
    # A synthetic test ping is not a signal, so it records no soak row.
    if not test_ping:
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
            "status": "test_ping" if test_ping else "emitted",
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
            # ROOM mode only: how the size was reached. Flat rows stay exactly
            # as they were (no meta key), so the flat journal row is unchanged.
            # A flat ticket the gate left alone adds no meta key, so its row is
            # unchanged; room sizing and any gate resize are recorded.
            **({"meta": {"sizing_mode": sizing.mode, "sizing": sizing.detail,
                         "risk_gate": gate_cap}}
               if (sizing.mode != prop_sizing.FLAT or gate_cap is not None
                   or sizing.detail) else {}),
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
