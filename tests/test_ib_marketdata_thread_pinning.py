"""Every IB market-data request must run on ONE thread.

Regression guard for BL-20260814-IB-EVENTLOOP-CONTENTION (live incident
2026-08-14). ib_insync's cached IB binds its socket transport to a single
event loop, and ``IBClient._ensure_event_loop`` documents that "dispatching a
request on a different loop would hang instead of returning bars". The web-api
already encodes the invariant (``candles._FETCH_EXECUTOR``, max_workers=1,
annotated "load-bearing"); the trader did not, because until the M20 exit-loop
decouple it had exactly one IB caller by construction.

The decouple gave the trader a second caller (the exit-evaluation daemon
thread) sharing the SAME cached IBClient as the main tick thread, which
produced live:

    IBMarketData.get_ohlcv failed for symbol=MES timeframe=5m:
        This event loop is already running

and, via liveness-probe timeouts, a tripped circuit breaker that suppressed
ALL IB calls for 120s at a time — MONITOR BLIND on open MGC/MES packages,
strategy_builder RuntimeErrors, and sizing_failed on new entries.

These tests assert the PROPERTY (all fetches land on one thread), not the
implementation, so they still hold if the pinning mechanism is swapped.
"""
from __future__ import annotations

import threading
import time

import pytest


pytest.importorskip("pandas")


class _Bar:
    date = "2026-08-14 08:00:00"
    open = high = low = close = 4300.0
    volume = 10


class _RecordingClient:
    """Minimal IBClient stand-in that records the calling thread per fetch.

    ``dwell`` holds each fetch inside ``reqHistoricalData`` long enough that
    two unserialised callers WOULD overlap, and ``max_concurrent`` records the
    peak occupancy so the serialisation claim is measured rather than assumed.
    """

    def __init__(self, dwell: float = 0.0):
        self.symbol = "MGC"
        self.fetch_threads: list[str] = []
        self.max_concurrent = 0
        self._in_flight = 0
        self._dwell = dwell
        self._lock = threading.Lock()

    # --- surface IBMarketData.get_ohlcv actually touches -------------------
    def connect(self):
        return self

    def _ensure_event_loop(self):
        return None

    def _build_contract(self, symbol):
        return object()

    def reqMarketDataType(self, *_a, **_k):
        return None

    def reqHistoricalData(self, *_a, **_k):
        with self._lock:
            self.fetch_threads.append(threading.current_thread().name)
            self._in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self._in_flight)
        try:
            if self._dwell:
                time.sleep(self._dwell)
        finally:
            with self._lock:
                self._in_flight -= 1
        return [_Bar()]


def _market_data(client):
    from src.exchange.ib_connector import IBMarketData

    return IBMarketData(port=7497, client_id=498, account="DUQ325724", _client=client)


def test_concurrent_callers_all_fetch_on_one_thread():
    """Two caller threads (the tick + the exit loop) → one fetch thread.

    Without the pin each fetch runs on its own caller thread and this sees two
    distinct names — the exact condition that drives one asyncio loop from two
    threads.
    """
    client = _RecordingClient()
    md = _market_data(client)

    errors: list[BaseException] = []

    def call():
        try:
            for _ in range(5):
                assert md.get_ohlcv("MGC", "15m", limit=1) is not None
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    threads = [
        threading.Thread(target=call, name=f"caller-{i}") for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(client.fetch_threads) == 10
    assert len(set(client.fetch_threads)) == 1, (
        "IB fetches ran on multiple threads: "
        f"{sorted(set(client.fetch_threads))}"
    )
    # And it is the dedicated pinned thread, not whichever caller got there
    # first — a caller-thread name here would mean the pin silently degraded.
    assert set(client.fetch_threads).pop().startswith("ib-marketdata")
    assert not any(n.startswith("caller-") for n in client.fetch_threads)


def test_fetches_never_overlap():
    """Peak occupancy inside reqHistoricalData is exactly 1.

    Each fetch dwells long enough that four unserialised callers would
    demonstrably overlap, so `max_concurrent > 1` without the pin. This is the
    stronger claim: same-thread-name alone would still hold if a pool happened
    to reuse one name, whereas occupancy measures the serialisation directly.
    """
    client = _RecordingClient(dwell=0.05)
    md = _market_data(client)

    results: list[object] = []
    lock = threading.Lock()

    def call():
        df = md.get_ohlcv("MGC", "15m", limit=1)
        with lock:
            results.append(df)

    threads = [threading.Thread(target=call, name=f"c{i}") for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 4
    assert all(df is not None for df in results)
    assert client.max_concurrent == 1, (
        f"fetches overlapped: peak occupancy {client.max_concurrent}"
    )


def test_reentrant_call_on_pinned_thread_does_not_deadlock():
    """A call already ON the pinned thread runs inline.

    Submitting to a max_workers=1 pool from inside its own worker would wait
    for a slot that cannot free — a self-deadlock. The re-entrancy guard is
    what keeps any future in-pool caller safe.
    """
    from src.exchange import ib_connector

    client = _RecordingClient()
    md = _market_data(client)

    done = ib_connector._IB_FETCH_EXECUTOR.submit(
        lambda: md.get_ohlcv("MGC", "15m", limit=1)
    ).result(timeout=15)

    assert done is not None
    assert client.fetch_threads
    assert all(n.startswith("ib-marketdata") for n in client.fetch_threads)


def test_queue_timeout_exceeds_fetch_timeout():
    """The wait covers QUEUE time plus the request, so it must exceed the
    per-request cap — otherwise a queued-but-healthy fetch is discarded as a
    failure and reads to the caller exactly like a gateway fault."""
    from src.exchange import ib_connector

    assert (
        ib_connector._IB_FETCH_QUEUE_TIMEOUT_S
        > ib_connector._IB_FETCH_TIMEOUT_S
    )
