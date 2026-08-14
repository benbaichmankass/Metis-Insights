"""IB market-data reads must serialize against the order path on one client.

WHY THIS TEST EXISTS (issue #9233, 2026-08-14).

`ib_client.py` declares the invariant in prose: one `IBClient` wraps ONE
ib_insync socket on one clientId driven by a not-thread-safe asyncio loop, so
"two threads on one socket is strictly worse than
BL-20260706-IBACCTUPDATES-COLLISION", and it states the accepted cost —
"the two loops SERIALISE on IB".

The prose was never true for market data. That note's inventory counts
`IBClient`'s own public methods; `IBMarketData` lives in a sibling module,
shares the SAME client instance via the `get_ib_client` registry, and took
`_usage_lock` only inside `connect()` — which releases it on return. Every
actual data call ran unlocked. When the M20 exit-loop decouple put exit
evaluation on a second thread, that raced the tick thread and produced
`"This event loop is already running"` on live MES fetches, breaker trips, and
MONITOR BLIND on real MGC/MES packages.

So these tests assert the INVARIANT (no two threads inside the IB data call at
once), not the implementation.

VERIFIED AGAINST THE PRE-FIX CODE, because a test that passes both ways proves
nothing. Measured by stashing the fix and re-running: **3 of the 5 fail** —
`..._never_overlaps_on_one_client` (the load-bearing one,
`observed_max_concurrency == 2`), `..._shared_lock_is_the_clients_own`, and
`..._without_usage_lock_is_flagged`.

The other two pass both ways and are regression guards, not discriminators —
say so rather than imply the whole file bites:
  * `..._blocks_while_the_order_path_holds_the_lock` — pre-fix, the reader still
    blocked, just inside `connect()` instead of around the data call. It guards
    the direction of the dependency, not the fix.
  * `..._unsupported_timeframe_still_short_circuits` — a pure regression guard.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.exchange.ib_connector import IBMarketData


class _ConcurrencyProbeIB:
    """Stands in for `ib_insync.IB`, recording peak in-flight concurrency."""

    def __init__(self, barrier_delay: float = 0.05) -> None:
        self._barrier_delay = barrier_delay
        self._inflight = 0
        self.observed_max_concurrency = 0
        self._probe_lock = threading.Lock()

    def reqMarketDataType(self, _mode: int) -> None:  # noqa: N802 — ib_insync API
        return None

    def reqHistoricalData(self, *_args, **_kwargs):  # noqa: N802 — ib_insync API
        with self._probe_lock:
            self._inflight += 1
            self.observed_max_concurrency = max(
                self.observed_max_concurrency, self._inflight
            )
        # Hold the "socket" long enough that a genuinely-unserialized caller
        # overlaps. Without the sleep both threads could finish disjointly by
        # luck and the test would pass against broken code.
        time.sleep(self._barrier_delay)
        with self._probe_lock:
            self._inflight -= 1
        return []


class _FakeIBClient:
    """Minimal IBClient surface: the real `_usage_lock` and the private hooks."""

    def __init__(self, ib: _ConcurrencyProbeIB) -> None:
        self._usage_lock = threading.RLock()
        self._ib = ib
        self.ensure_loop_calls = 0

    def connect(self):
        # Mirrors the real client: takes its own lock and RELEASES it on return.
        # This is exactly why locking only inside connect() never protected the
        # data call that follows.
        with self._usage_lock:
            return self._ib

    def _ensure_event_loop(self) -> None:
        self.ensure_loop_calls += 1

    def _build_contract(self, symbol: str):
        return {"symbol": symbol}


def _drive(md: IBMarketData, threads: int = 2) -> None:
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            md.get_ohlcv("MES", "5m", limit=10)
        except BaseException as exc:  # noqa: BLE001 — surface into the assert
            errors.append(exc)

    workers = [threading.Thread(target=_run) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=10)
    assert not errors, f"get_ohlcv raised: {errors!r}"
    assert not any(w.is_alive() for w in workers), "get_ohlcv deadlocked"


def test_concurrent_get_ohlcv_never_overlaps_on_one_client():
    """THE invariant. Fails pre-fix with observed_max_concurrency == 2."""
    ib = _ConcurrencyProbeIB()
    md = IBMarketData(port=4002, client_id=1, _client=_FakeIBClient(ib))

    _drive(md, threads=2)

    assert ib.observed_max_concurrency == 1, (
        "two threads were inside the IB data call at once — market-data reads "
        "are not serialized against the order path on this client"
    )


def test_shared_lock_is_the_clients_own_not_a_private_one():
    """Serializing on a private lock would pass the test above and still race
    the ORDER path, which holds `IBClient._usage_lock`. Assert identity."""
    client = _FakeIBClient(_ConcurrencyProbeIB())
    md = IBMarketData(port=4002, client_id=1, _client=client)

    assert md._ib_lock is client._usage_lock
    assert md._ib_lock_is_shared is True


def test_get_ohlcv_blocks_while_the_order_path_holds_the_lock():
    """A data read must WAIT on a lock already held by an order-path call.

    NOT a discriminator — this passed pre-fix too, because `connect()` took the
    lock even when the data call did not. Kept as a regression guard on the
    direction of the dependency."""
    client = _FakeIBClient(_ConcurrencyProbeIB())
    md = IBMarketData(port=4002, client_id=1, _client=client)
    entered = threading.Event()

    def _reader() -> None:
        md.get_ohlcv("MES", "5m", limit=10)
        entered.set()

    with client._usage_lock:  # stand in for place()/close() holding it
        t = threading.Thread(target=_reader)
        t.start()
        assert not entered.wait(timeout=0.3), (
            "get_ohlcv proceeded while the order path held _usage_lock"
        )
    t.join(timeout=10)
    assert entered.is_set(), "get_ohlcv did not resume after the lock released"


def test_client_without_usage_lock_is_flagged_not_silently_unguarded():
    """A missing lock must be a visible construction-time fact. Silently
    running unguarded is the failure mode this whole class of bug is made of."""

    class _LocklessClient(_FakeIBClient):
        def __init__(self) -> None:
            super().__init__(_ConcurrencyProbeIB())
            del self._usage_lock

    md = IBMarketData(port=4002, client_id=1, _client=_LocklessClient())

    assert md._ib_lock_is_shared is False
    # Still usable — a test double must behave exactly as before.
    assert md.get_ohlcv("MES", "5m", limit=10) is None


def test_unsupported_timeframe_still_short_circuits_before_the_lock():
    """Regression guard: the early return must not have been pulled inside the
    lock, which would take it for a call that never touches IB."""
    client = _FakeIBClient(_ConcurrencyProbeIB())
    md = IBMarketData(port=4002, client_id=1, _client=client)

    assert md.get_ohlcv("MES", "7h", limit=10) is None
    assert client.ensure_loop_calls == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
