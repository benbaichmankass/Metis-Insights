"""The /api/diag/bybit_raw_closed_pnl ROUTE WIRING — not the accessor.

⚠️ WHY THIS FILE EXISTS, and it is a defect I shipped. The accessor tests in
``tests/test_bybit_raw_closed_pnl_accessor.py`` all passed while the route was
BROKEN: it called ``run_account_read(fn, acc, symbol=..., start_ms=..., end_ms=...)``,
and ``run_account_read(fn, *args)`` is POSITIONAL-ONLY because it forwards to
``loop.run_in_executor``, which takes no kwargs. That raises ``TypeError`` at
REQUEST time — never at import, and never in a test that calls the accessor
directly.

The first live query returned
``read_state: "could_not_look"`` with
``error: "TypeError: run_account_read() got an unexpected keyword argument 'symbol'"``.
The three-state design did its job — the route reported *we could not look*
rather than an empty record list, so a broken read could not be mistaken for a
venue that booked nothing. But the wiring was untested, and an accessor-level
test cannot reach it BY CONSTRUCTION.

So this asserts the seam: the route must hand ``run_account_read`` a callable it
can invoke with the account POSITIONALLY, with the window already bound.
"""
from __future__ import annotations

import asyncio
import inspect

from src.web.api import _account_read_executor
from src.web.api.routers import diag as diag_router


def test_run_account_read_is_positional_only():
    """The premise of the bug, pinned so it cannot silently change under us."""
    sig = inspect.signature(_account_read_executor.run_account_read)
    kinds = [p.kind for p in sig.parameters.values()]
    assert inspect.Parameter.VAR_KEYWORD not in kinds, (
        "run_account_read forwards to loop.run_in_executor, which takes no "
        "kwargs; if this ever gains **kwargs, the partial below is redundant "
        "but still correct")


def test_route_binds_the_window_before_the_executor_hop(monkeypatch):
    """THE REGRESSION TEST. The route must pass a callable that
    `run_account_read` can invoke with ONLY the account, positionally."""
    seen: dict = {}

    def _fake_accessor(account, *, symbol, start_ms, end_ms):
        seen["account_id"] = account.get("account_id")
        seen["symbol"] = symbol
        seen["start_ms"] = start_ms
        seen["end_ms"] = end_ms
        return {"query_state": "no_rows", "record_count": 0, "records": []}

    async def _fake_run_account_read(fn, *args):
        # EXACTLY what the real one does: positional only, no kwargs. A route
        # that passed kwargs would raise TypeError here, as it did live.
        return fn(*args)

    import src.units.accounts.clients as clients_mod
    import src.units.ui.data_loaders as loaders_mod
    monkeypatch.setattr(clients_mod, "account_bybit_raw_closed_pnl",
                        _fake_accessor, raising=False)
    monkeypatch.setattr(loaders_mod, "list_accounts",
                        lambda: [{"account_id": "bybit_2", "exchange": "bybit"}])
    monkeypatch.setattr(diag_router, "run_account_read", _fake_run_account_read)
    monkeypatch.setattr(diag_router, "_require_diag_token", lambda request: None)

    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        diag_router.get_bybit_raw_closed_pnl(
            request=object(), account_id="bybit_2", symbol="ETHUSDT",
            start_ms=111, end_ms=222,
        )
    )
    assert seen == {"account_id": "bybit_2", "symbol": "ETHUSDT",
                    "start_ms": 111, "end_ms": 222}, (
        "the window must survive the executor hop bound to the callable")
    assert out["read_state"] == "closed_pnl_read"
    assert out["result"]["query_state"] == "no_rows"


def test_a_raising_accessor_is_could_not_look_not_an_empty_result(monkeypatch):
    """The property that saved this from being reported as 'the venue booked
    nothing': a failed read must never present as an empty record list."""
    async def _boom(fn, *args):
        raise RuntimeError("executor hop failed")

    import src.units.ui.data_loaders as loaders_mod
    monkeypatch.setattr(loaders_mod, "list_accounts",
                        lambda: [{"account_id": "bybit_2", "exchange": "bybit"}])
    monkeypatch.setattr(diag_router, "run_account_read", _boom)
    monkeypatch.setattr(diag_router, "_require_diag_token", lambda request: None)

    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        diag_router.get_bybit_raw_closed_pnl(
            request=object(), account_id="bybit_2", symbol="ETHUSDT",
            start_ms=111, end_ms=222,
        )
    )
    assert out["read_state"] == "could_not_look"
    assert out["result"] is None and out["error"]
