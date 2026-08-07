"""One place that builds an authenticated ccxt Bybit client.

**Why this module exists.** ``config/accounts.yaml`` declares ``demo: true`` on
``bybit_1`` and ``bybit_portfolio``; Bybit serves demo trading from a SEPARATE
host (``api-demo.bybit.com``), and a demo key presented to mainnet is rejected
with ``retCode 10003 "API key is invalid"``. The order path has always honoured
this — ``src/units/accounts/clients.py`` routes ``demo: true`` through pybit's
``HTTP(demo=True)``. The two cost-sweep pullers did not: each hand-rolled its own
``ccxt.bybit({...})`` with no demo awareness, so both demo accounts failed 100%
of every nightly run (BL-20260807-BYBIT-DEMO-FILLS-NEVER-PULLED).

That is the same shape as the news-feed second registry: a fact about an account
was declared in one place and independently re-derived — or here, silently
ignored — in another. So this builder is the ONE construction site both pullers
use. A future puller that needs a Bybit client imports this; it does not write a
third ``ccxt.bybit(...)``.

Read-only by intent: callers use it for ``fetch_my_trades`` /
``fetch_funding_history``. Nothing here places an order.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DemoRoutingUnsupported", "build_bybit_client"]


class DemoRoutingUnsupported(RuntimeError):
    """The installed ccxt cannot route to Bybit's demo host.

    Raised rather than falling back to mainnet **on purpose**: a silent fallback
    is precisely the bug this module fixes — it would send demo credentials to
    the live host, get ``retCode 10003``, and be indistinguishable from an
    account that simply had no fills.
    """


def build_bybit_client(
    *,
    api_key: str,
    api_secret: str,
    category: str,
    demo: bool = False,
) -> Any:
    """Return an authenticated ``ccxt.bybit`` client for one account.

    ``category`` is the Bybit V5 product category (``linear`` for USDT-margined
    perps, else ``spot``); it selects ccxt's ``defaultType`` routing. Callers
    must ALSO pass ``category`` explicitly on each V5 call — the
    construction-time default alone is not load-bearing on the unified account
    (same convention the order path follows).

    ``demo=True`` calls ccxt's ``enable_demo_trading(True)``, which swaps
    ``urls['api']`` for ``urls['demotrading']`` **and** short-circuits
    ``fetch_currencies`` to ``{}``. That second effect matters as much as the
    first: ``load_markets()`` calls ``fetch_currencies`` → the *private*
    ``/v5/asset/coin/query-info`` endpoint, which is exactly where the nightly
    run raised before any fills request was ever issued.
    """
    import ccxt  # noqa: PLC0415  (heavy; keep --help snappy)

    exchange = ccxt.bybit({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "swap" if category == "linear" else "spot"},
    })

    if demo:
        enable = getattr(exchange, "enable_demo_trading", None)
        if not callable(enable):
            raise DemoRoutingUnsupported(
                "installed ccxt "
                f"({getattr(ccxt, '__version__', 'unknown')}) has no "
                "bybit.enable_demo_trading(); a demo account cannot be reached "
                "on the mainnet host. Upgrade ccxt in the VM venv "
                "(requirements.txt pins ccxt>=4.0.0; demo routing needs a "
                "release that ships enable_demo_trading — verified present in "
                "4.5.71)."
            )
        enable(True)

    logger.info(
        "build_bybit_client: category=%s demo=%s host=%s",
        category, demo, exchange.urls.get("api"),
    )
    return exchange
