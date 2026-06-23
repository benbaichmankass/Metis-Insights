"""Prop venue ↔ bot symbol mapping — one source of truth.

The bot's canonical symbol for an instrument (e.g. ``ETHUSDT``) is NOT what the
prop venue's terminal calls it (Breakout/DXTrade lists it as ``ETHUSD``). The
manual bridge crosses that boundary twice:

- **Outbound** (ticket): the paste-ready setup must name the symbol the
  *executor* will actually trade on the terminal, or they waste time/compute
  resolving "ETHUSDT → which DXTrade instrument?" by hand.
- **Inbound** (report-back): a report — whether the dashboard form, the issue
  relay, or a future Telegram listener — may carry EITHER the venue symbol the
  human typed (``ETHUSD``) or the canonical bot symbol (``ETHUSDT``). The
  journal + ticket reconciliation are keyed on the **canonical** symbol, so an
  inbound venue symbol must normalise back before it's written.

Both directions resolve from the SAME ``config/prop_rulesets/breakout_routing.yaml``
``symbols[<bot>].dxtrade_symbol`` map the outbound ticket already reads, so the
mapping never drifts between the two halves of the bridge. Unmapped symbols pass
through unchanged (fail-open: a missing map never strands a trade — it just
shows the bot symbol, today's behaviour).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTING_PATH = _REPO_ROOT / "config" / "prop_rulesets" / "breakout_routing.yaml"


def _load_routing() -> Dict:
    try:
        import yaml

        with open(_ROUTING_PATH) as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001 — fail-open to an empty (passthrough) map
        logger.warning("symbol_map: routing load failed (%s); passthrough only", exc)
        return {}


def _build_maps(routing: Dict) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return ``(bot→venue, venue→bot)`` from the routing ``symbols`` block.

    Reads ``symbols[<bot>].dxtrade_symbol`` (the outbound ticket's source of
    truth); a ``null`` / empty value contributes no mapping (passthrough). Both
    sides of the reverse map are keyed case-folded so an operator typing
    ``ethusd`` still resolves.
    """
    bot_to_venue: Dict[str, str] = {}
    venue_to_bot: Dict[str, str] = {}
    symbols = (routing.get("symbols") or {}) if isinstance(routing, dict) else {}
    for bot_sym, block in symbols.items():
        if not isinstance(block, dict):
            continue
        venue = block.get("dxtrade_symbol")
        if not venue:
            continue
        bot_to_venue[str(bot_sym).upper()] = str(venue)
        venue_to_bot[str(venue).upper()] = str(bot_sym)
    return bot_to_venue, venue_to_bot


def to_venue_symbol(bot_symbol: Optional[str]) -> Optional[str]:
    """Bot canonical symbol → the prop venue (DXTrade) symbol the executor trades.

    ``ETHUSDT`` → ``ETHUSD`` when mapped; the bot symbol unchanged when not.
    ``None``/empty in → ``None`` out.
    """
    if not bot_symbol:
        return bot_symbol
    bot_to_venue, _ = _build_maps(_load_routing())
    return bot_to_venue.get(str(bot_symbol).upper(), bot_symbol)


def to_bot_symbol(symbol: Optional[str]) -> Optional[str]:
    """Any inbound symbol → the bot's canonical symbol (the journal key).

    Accepts EITHER the venue symbol (``ETHUSD`` → ``ETHUSDT``) or an already
    canonical bot symbol (``ETHUSDT`` → ``ETHUSDT``, passthrough). Unknown
    symbols pass through unchanged so a never-before-seen instrument is never
    dropped. ``None``/empty in → unchanged.
    """
    if not symbol:
        return symbol
    bot_to_venue, venue_to_bot = _build_maps(_load_routing())
    key = str(symbol).upper()
    # Already a canonical bot symbol that we map outbound? Keep it.
    if key in bot_to_venue:
        return symbol
    # A venue symbol we know → canonicalise.
    return venue_to_bot.get(key, symbol)


__all__ = ["to_venue_symbol", "to_bot_symbol"]
