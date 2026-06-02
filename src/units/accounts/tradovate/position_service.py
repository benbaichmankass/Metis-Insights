"""Position reads — current net positions per account/contract."""
from __future__ import annotations

from .endpoints import REST
from .models import Position
from .rest_client import TradovateRestClient


class PositionService:
    def __init__(self, rest: TradovateRestClient):
        self._rest = rest

    def list_positions(self) -> list[Position]:
        raw = self._rest.get(REST.position_list)
        if not isinstance(raw, list):
            return []
        return [Position.from_api(item) for item in raw if isinstance(item, dict)]

    def net_qty(self, account_id: int, contract_id: int) -> int:
        for p in self.list_positions():
            if p.account_id == account_id and p.contract_id == contract_id:
                return p.net_pos
        return 0
