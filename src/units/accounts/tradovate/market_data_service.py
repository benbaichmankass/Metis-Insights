"""Contract lookup + quote subscription.

Contract metadata is fetched via REST so callers can resolve a symbol
to a contractId before subscribing on the WS. Quote subscriptions are
delegated to the WebSocket client — this service is the glue that keeps
the symbol→contract_id cache in sync.
"""
from __future__ import annotations

from .endpoints import REST
from .logging_utils import get_logger
from .models import Contract
from .rest_client import TradovateRestClient

_log = get_logger(__name__)


class MarketDataService:
    def __init__(self, rest: TradovateRestClient, ws=None):
        self._rest = rest
        self._ws = ws
        self._contract_by_symbol: dict[str, Contract] = {}

    def find_contract(self, symbol: str) -> Contract | None:
        sym = symbol.upper()
        if sym in self._contract_by_symbol:
            return self._contract_by_symbol[sym]
        raw = self._rest.get(REST.contract_find, params={"name": sym})
        if not isinstance(raw, dict) or "id" not in raw:
            return None
        c = Contract.from_api(raw)
        self._contract_by_symbol[sym] = c
        return c

    def contract_id_for(self, symbol: str) -> int | None:
        c = self.find_contract(symbol)
        return c.id if c else None

    def suggest(self, prefix: str, limit: int = 10) -> list[Contract]:
        raw = self._rest.get(REST.contract_suggest, params={"t": prefix, "l": limit})
        if not isinstance(raw, list):
            return []
        return [Contract.from_api(c) for c in raw if isinstance(c, dict)]

    def subscribe_quote(self, symbol: str, on_quote) -> None:
        """Subscribe to a symbol's quote stream via the WS client.

        ``on_quote`` is a callable ``(Quote) -> None``. No-op when this
        service was constructed without a WS client (e.g. unit tests).
        """
        if self._ws is None:
            _log.warning("subscribe_quote called without WS", extra={"symbol": symbol})
            return
        self._ws.subscribe_quote(symbol, on_quote)
