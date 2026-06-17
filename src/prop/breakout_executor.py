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


def emit_prop_ticket(
    order: Dict[str, Any],
    account_cfg: Dict[str, Any],
    *,
    timeframe: Optional[str] = None,
    _emitter: Any = None,
) -> str:
    """Build a Breakout ticket from *order* and emit it as a ``prop_signal``.

    Returns a ``prop-manual-<uuid>`` trade id (a manual-fill marker — the order
    package journals, but no live exchange position is created). Raises only on a
    structurally invalid order (missing entry/sl/tp); a notification-delivery
    failure is swallowed (logged) so the journal row is never lost.

    ``_emitter`` is an injection seam for tests (defaults to
    ``src.prop.breakout_notify.emit_prop_signal``).
    """
    from src.prop.breakout_ticket import BreakoutSignal, TicketConfig, build_ticket

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

    routing = _load_routing()
    cfg = TicketConfig(
        account_size_usd=float(account_cfg.get("account_size_usd")
                               or routing.get("account_size_usd") or 5000.0),
        risk_pct=float(account_cfg.get("risk_pct") or routing.get("risk_pct") or 0.6),
        dxtrade_symbol=_per_symbol(routing, symbol, "dxtrade_symbol", None),
        contract_value_usd_per_point=float(
            _per_symbol(routing, symbol, "contract_value_usd_per_point", 1.0)),
        entry_band_frac=float(routing.get("entry_band_frac") or 0.25),
        ttl_bars=float(routing.get("ttl_bars") or 1.0),
    )
    sig = BreakoutSignal(
        strategy=strategy, symbol=symbol, direction=direction,
        entry=entry, sl=sl, tp=tp,
        timeframe=str(timeframe or _DEFAULT_TIMEFRAME),
        signal_time=datetime.now(timezone.utc),
    )
    ticket = build_ticket(sig, cfg)

    emitter = _emitter
    if emitter is None:
        from src.prop.breakout_notify import emit_prop_signal as emitter  # type: ignore
    try:
        emitter(ticket)
    except Exception as exc:  # noqa: BLE001 — never lose the journal row over a push
        logger.warning("breakout_executor: ticket emit failed for %s: %s", symbol, exc)

    trade_id = f"{MANUAL_FILL_PREFIX}{uuid.uuid4().hex[:12]}"
    logger.info(
        "breakout_executor: emitted prop ticket %s %s entry=%s sl=%s tp=%s → %s "
        "(manual fill — no live position created)",
        symbol, sig.direction, entry, sl, tp, trade_id,
    )
    return trade_id
