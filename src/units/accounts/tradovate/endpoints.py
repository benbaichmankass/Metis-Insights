"""Single source of truth for Tradovate REST paths + WS topics.

Every endpoint name lives here. If Tradovate renames a path or a
community-reported topic turns out to be wrong, fix it in this file
only — no other module hardcodes a string. Items flagged ``UNCERTAIN``
in the docstring should be verified against the live API before being
used in production order paths.

Base URL resolution is in ``config.TradovateConfig.urls`` so this
module stays purely about path names.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RestEndpoints:
    # Auth — high confidence
    auth_token: str = "/auth/accesstokenrequest"
    auth_renew: str = "/auth/renewaccesstoken"
    auth_me: str = "/auth/me"

    # Accounts — high confidence
    account_list: str = "/account/list"
    account_item: str = "/account/item"  # ?id=

    # Contracts — high confidence
    contract_find: str = "/contract/find"  # ?name=
    contract_suggest: str = "/contract/suggest"  # ?t=
    contract_item: str = "/contract/item"  # ?id=

    # Orders
    order_place: str = "/order/placeorder"
    order_cancel: str = "/order/cancelorder"
    order_modify: str = "/order/modifyorder"
    order_list: str = "/order/list"  # UNCERTAIN: may be /order/items

    # Positions
    position_list: str = "/position/list"  # UNCERTAIN
    position_item: str = "/position/item"  # UNCERTAIN

    # Fills
    fill_list: str = "/fill/list"  # UNCERTAIN

    # Cash balances (used by the smoke test to confirm a sim account)
    cash_balance: str = "/cashBalance/getCashBalanceSnapshot"  # UNCERTAIN


@dataclass(frozen=True)
class WsTopics:
    """WebSocket request topics. Tradovate frames look like
    ``<topic>\\n<requestId>\\n<queryString>\\n<body-json>``.
    """

    authorize: str = "authorize"
    md_subscribe_quote: str = "md/subscribequote"
    md_unsubscribe_quote: str = "md/unsubscribequote"
    md_subscribe_dom: str = "md/subscribedom"  # UNCERTAIN
    user_sync: str = "user/syncrequest"  # subscribe to orders/positions/fills


REST = RestEndpoints()
WS = WsTopics()
