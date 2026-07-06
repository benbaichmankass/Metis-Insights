"""Shared single-worker executor for blocking, event-loop-driving account/
position reads (``src.units.accounts.clients.account_open_positions`` and its
IB branch in particular).

Why this exists (BL-20260706-IBCONCURRENCY): ``account_open_positions``'s IB
path calls ``IBClient.positions()`` / ``IBClient.balance()``
(``src/units/accounts/ib_client.py``), which are **synchronous, event-loop-
driving** calls — ib_insync's sync API wraps its own coroutines and drives
them to completion via ``IBClient._ensure_event_loop()`` + ``run_until_
complete``-style execution. ``_ensure_event_loop`` first checks
``asyncio.get_running_loop()``: inside an ``async def`` FastAPI route handler
that check SUCCEEDS (uvicorn's loop is running on this very thread), so
ib_insync then tries to drive that ALREADY-RUNNING loop synchronously —
exactly the "Cannot run the event loop while another loop is running" /
"Future ... attached to a different loop" errors seen live 2026-07-06 when a
burst of concurrent `/api/bot/positions` + `/api/bot/strategies/*/review` +
`/api/diag/exchange_positions` requests landed on the same account within
the same second.

The fix mirrors ``src/web/api/routers/candles.py``'s ``_FETCH_EXECUTOR``
pattern exactly: offload the blocking call onto a dedicated worker thread via
``run_in_executor`` so ib_insync's sync call runs on a thread with **no**
already-running loop (``_ensure_event_loop`` then creates/reuses that
client's own persistent loop, safely, on a thread nothing else touches).
``max_workers=1`` also SERIALIZES calls through one thread, so two
concurrent requests reading the same account's client can never race each
other's loop-binding state.

**Deliberately a SEPARATE executor from ``candles._FETCH_EXECUTOR``**, not a
shared one: the account/position-read path resolves its IB connection via
``src.units.accounts.clients.ib_read_client_for`` /
``_ib_read_client_id()`` (clientId ``9000 + pid % 900``), while the candle
fetcher's IB connection uses a distinct clientId (``IB_MD_CLIENT_ID``,
default ``600``, set in ``candles.py::_settings``). Different clientIds mean
different registry-cached ``IBClient`` objects (keyed by
``(host, port, client_id)`` in ``ib_client.py::_CONN_REGISTRY``) with
independent per-client event-loop state — so there is no shared-connection
race to prevent between the two paths, and no reason to serialize unrelated
market-data candle fetches behind account/position reads (or vice versa).
Each concern gets its own single-worker executor.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

# Single dedicated worker thread for account/position/balance reads that may
# touch an IB-backed client. See module docstring for why this is distinct
# from candles.py's _FETCH_EXECUTOR.
_ACCOUNT_READ_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="account-read",
)


async def run_account_read(fn: Callable[..., Any], *args: Any) -> Any:
    """Await *fn(*args)* on the dedicated single-worker account-read thread.

    Call this from an ``async def`` route handler in place of calling a
    blocking account/position-read helper (e.g.
    ``src.units.accounts.clients.account_open_positions``) directly — see the
    module docstring for why a direct synchronous call is unsafe here.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_ACCOUNT_READ_EXECUTOR, fn, *args)
