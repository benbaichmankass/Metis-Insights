"""Account discovery — list accounts, pick a sim account."""
from __future__ import annotations

from typing import Iterable

from .endpoints import REST
from .models import Account
from .rest_client import TradovateRestClient


class AccountService:
    def __init__(self, rest: TradovateRestClient):
        self._rest = rest

    def list_accounts(self) -> list[Account]:
        raw = self._rest.get(REST.account_list)
        if not isinstance(raw, list):
            return []
        return [Account.from_api(item) for item in raw if isinstance(item, dict)]

    def get(self, account_id: int) -> Account | None:
        raw = self._rest.get(REST.account_item, params={"id": account_id})
        if not isinstance(raw, dict):
            return None
        return Account.from_api(raw)

    def pick_simulation_account(self, accounts: Iterable[Account] | None = None) -> Account | None:
        """Return the first simulation account, or None.

        Tradovate marks demo accounts with ``accountType="Customer"`` and
        ``legalStatus="Individual"`` in simulation; but the most reliable
        signal across community reports is ``legalStatus == "Simulator"``
        or ``name`` containing "DEMO". We try both and fall back to the
        first active account.
        """
        items = list(accounts) if accounts is not None else self.list_accounts()
        for a in items:
            if (a.legal_status or "").lower() == "simulator":
                return a
            if "demo" in a.name.lower() or "sim" in a.name.lower():
                return a
        return next((a for a in items if a.active), None)
