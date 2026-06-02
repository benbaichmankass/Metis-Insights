"""Single source of truth for Tradovate REST paths + WS topics.

Every endpoint name lives here. If Tradovate renames a path or a
community-reported topic turns out to be wrong, fix it in this file
only — no other module hardcodes a string. Items flagged ``UNCERTAIN``
should be verified against the live API before being used in a
production order path.

Casing convention
-----------------
Tradovate's REST router is case-insensitive in practice, but the official
docs (https://partner.tradovate.com/api/rest-api-endpoints/) use
camelCase for operation names (``renewAccessToken``, ``placeOrder``,
``cancelOrder``, ``modifyOrder``) and lowercase for query operations
(``/auth/accesstokenrequest``, ``/account/list``, ``/order/list``,
``/fill/list``). We follow that convention so the paths read identically
to the docs even though either spelling would route.

Base URL resolution lives in ``config.TradovateConfig.urls`` so this
module stays purely about path names.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RestEndpoints:
    # Auth — verified against partner.tradovate.com docs (2026-06-02).
    auth_token: str = "/auth/accesstokenrequest"     # POST, body=auth payload
    auth_renew: str = "/auth/renewAccessToken"        # GET, header=Bearer
    auth_me: str = "/auth/me"                          # GET

    # Accounts — partner docs confirm GET, no body.
    account_list: str = "/account/list"
    account_item: str = "/account/item"                # ?id=

    # Contracts — high confidence (used in every public example).
    contract_find: str = "/contract/find"              # ?name=
    contract_suggest: str = "/contract/suggest"        # ?t=
    contract_item: str = "/contract/item"              # ?id=

    # Orders — docs use camelCase operation names.
    order_place: str = "/order/placeOrder"             # POST
    order_cancel: str = "/order/cancelOrder"           # POST
    order_modify: str = "/order/modifyOrder"           # POST
    order_list: str = "/order/list"                    # UNCERTAIN: forum
                                                       # references both
                                                       # /order/list and
                                                       # /order/items; left
                                                       # at /order/list per
                                                       # the example-api-js
                                                       # convention.

    # Positions — community-confirmed but no swagger entry seen.
    position_list: str = "/position/list"              # UNCERTAIN
    position_item: str = "/position/item"              # UNCERTAIN

    # Fills — forum confirms /fill/list returns 200 with all filled orders.
    fill_list: str = "/fill/list"

    # Cash balances (used by the smoke test to confirm a sim account).
    # No swagger seen for the snapshot operation; the WS event ``cashBalance``
    # is the canonical alternative path.
    cash_balance: str = "/cashBalance/getCashBalanceSnapshot"  # UNCERTAIN


@dataclass(frozen=True)
class WsTopics:
    """WebSocket request topics. Tradovate frames look like
    ``<topic>\\n<requestId>\\n<queryString>\\n<body-json>``.
    """

    authorize: str = "authorize"
    md_subscribe_quote: str = "md/subscribeQuote"     # camelCase per docs
    md_unsubscribe_quote: str = "md/unsubscribeQuote"
    md_subscribe_dom: str = "md/subscribeDOM"          # UNCERTAIN
    # user/syncrequest is the post-auth subscription that pushes orders,
    # positions, fills, and cashBalance events on the trading socket.
    user_sync: str = "user/syncrequest"


REST = RestEndpoints()
WS = WsTopics()
