"""The IB connector memo — T.1 of `docs/claude/WORKPLAN-2026-08-21.md`.

WHY THIS FILE EXISTS, and why it asserts a CALL COUNT rather than a value.

`_client_cache_key` used to fall through to ``None`` for ``interactive_brokers``,
so ``connector_for_symbol`` built a fresh ``IBMarketData`` on every candle
request. ``_candle_cache_key`` keys on a per-OBJECT lifetime token
(``_client_identity_token``), so a fresh wrapper produced a cache key that had
never been seen and could never be seen again: **every IB candle request was a
guaranteed venue round trip, at any TTL.**

That defect is invisible to every value-based test. The frames returned were
correct; only their COST was wrong. Measured on the live trader
(`/api/diag/tick_cost`, off-loop, one process, n=433 exit passes): the single
open IB 15m package produced ``fetch.15m`` at **1.002 fetches per pass** — one
venue round trip every pass, zero cache hits — while the three non-IB frames
landed within 12% of what their TTL and revisit interval predict. Full working:
`docs/research/exit-eval-fetch-attribution-2026-08-21.md`.

So the assertion here is the count. A cache that never hits is not
"slower but correct"; it is a different system, and the only thing that can
observe the difference is how many times the venue was asked.

⚠️ **These tests were shown to FAIL against the pre-fix file**, which is the
whole point — an assertion never observed failing is not evidence. Pre-fix,
`test_repeated_requests_hit_the_cache_on_every_venue` reports the IB rows at
5 venue calls out of 5 while bybit/alpaca report 1.
"""
from __future__ import annotations

import pytest

from src.runtime import market_data as md


class _CountingConnector:
    """A stand-in connector that records how often the venue was asked."""

    def __init__(self, counter: dict, kind: str) -> None:
        self._counter = counter
        self._kind = kind

    def get_ohlcv(self, symbol, timeframe, limit=200):  # noqa: D102
        self._counter[self._kind] = self._counter.get(self._kind, 0) + 1
        return [[1, 1.0, 1.0, 1.0, 1.0, 1.0]] * 3


@pytest.fixture
def venue(monkeypatch):
    """Patch the UNCACHED builder only.

    The real `_build_exchange_client` memo and the real `_candle_cache_key` both
    still run, so the test exercises the code under test rather than a mock of
    it. Both process-wide caches are reset either side so ordering cannot leak.
    """
    counter: dict = {}

    def _uncached(settings):
        name = str(settings.get("EXCHANGE", "bybit")).strip().lower()
        if name in ("interactive_brokers", "ib"):
            return _CountingConnector(counter, "ib")
        if name == "alpaca":
            return _CountingConnector(counter, "alpaca")
        return _CountingConnector(counter, "bybit")

    monkeypatch.setattr(md, "_build_exchange_client_uncached", _uncached)
    # A resolvable IB endpoint, so identity resolution does not depend on
    # whatever `config/accounts.yaml` happens to hold on the machine running
    # the suite.
    monkeypatch.setenv("IB_HOST", "10.0.0.251")
    monkeypatch.setenv("IB_PORT", "4002")
    monkeypatch.setenv("CANDLE_CACHE_TTL_FRACTION", "0.10")
    monkeypatch.setenv("CANDLE_CACHE_TTL_MAX_S", "300")
    md.reset_candle_cache()
    md.reset_exchange_client_cache()
    yield counter
    md.reset_candle_cache()
    md.reset_exchange_client_cache()


def _venue_calls(counter, exchange, symbol, timeframe, n=5):
    """Issue *n* identical requests, re-resolving the connector each time.

    Re-resolving per call is not an artificial stress: it is exactly what the
    live loops do, because `connector_for_symbol` is called per signal-builder
    and per open package.
    """
    key = {"interactive_brokers": "ib", "alpaca": "alpaca", "bybit": "bybit"}[exchange]
    before = counter.get(key, 0)
    for _ in range(n):
        client = md._build_exchange_client({"EXCHANGE": exchange})
        md.fetch_candles(symbol, timeframe, limit=200, exchange_client=client)
    return counter.get(key, 0) - before


@pytest.mark.parametrize(
    "exchange,symbol,timeframe",
    [
        ("bybit", "BTCUSDT", "4h"),
        ("alpaca", "QQQ", "1d"),
        ("interactive_brokers", "MGC", "15m"),
        ("interactive_brokers", "MHG", "1d"),
    ],
)
def test_repeated_requests_hit_the_cache_on_every_venue(
    venue, exchange, symbol, timeframe
):
    """5 identical in-TTL requests must cost exactly ONE venue round trip.

    IB regressed to 5-of-5 for as long as `_client_cache_key` excluded it. The
    two non-IB rows are the CONTROLS: they passed before the fix and must keep
    passing, so a failure here localises to IB rather than to the cache itself.
    """
    calls = _venue_calls(venue, exchange, symbol, timeframe)
    assert calls == 1, (
        f"{exchange}/{timeframe} went to the venue {calls}/5 times — a cache "
        f"that never hits is not slower-but-correct, it is a different system"
    )


def test_ib_memo_returns_the_same_wrapper_for_one_endpoint(venue):
    """The memo must return one object, which is what makes the cache key stable.

    This is the MECHANISM behind the test above, asserted separately so a
    failure says which half broke.
    """
    first = md._build_exchange_client({"EXCHANGE": "interactive_brokers"})
    second = md._build_exchange_client({"EXCHANGE": "interactive_brokers"})
    assert first is second


def test_ib_memo_adds_no_new_socket_sharing():
    """The safety case, asserted rather than argued.

    `IBMarketData` holds no socket: it takes its client from `get_ib_client()`,
    which is already a process-wide registry keyed on (host, port, client_id).
    So every wrapper for one endpoint ALREADY shared one `IBClient` before this
    change, and memoizing the wrapper cannot increase the number of live IB
    clientIds — which is what `BL-20260706-IBACCTUPDATES-COLLISION` governs.

    Asserted structurally (the constructor delegates to the registry) rather
    than by opening a socket, because the suite must not need a gateway.
    """
    import inspect

    from src.exchange import ib_connector

    src = inspect.getsource(ib_connector.IBMarketData.__init__)
    assert "get_ib_client(" in src, (
        "IBMarketData no longer obtains its client from the process-wide "
        "get_ib_client() registry — the memo's safety argument rested on that, "
        "so re-derive it before trusting this memo"
    )


def test_unresolvable_ib_endpoint_declines_to_memo(monkeypatch):
    """No `ib_port` anywhere => no memo, and the SAME error as before.

    Fail-safe: refusing to memo is always correct, and the caller must still
    see the original `ValueError` rather than a new failure mode.
    """
    monkeypatch.delenv("IB_HOST", raising=False)
    monkeypatch.delenv("IB_PORT", raising=False)
    monkeypatch.setattr(md, "_ib_account_field", lambda field: None)

    assert md._ib_connection_identity({"EXCHANGE": "interactive_brokers"}) is None
    assert md._client_cache_key({"EXCHANGE": "interactive_brokers"}) is None
    with pytest.raises(ValueError, match="no ib_port"):
        md._build_ib_market_data({"EXCHANGE": "interactive_brokers"})


def test_identity_resolver_is_the_only_definition(monkeypatch):
    """`_build_ib_market_data` must CONSTRUCT from the same tuple the memo keys on.

    One definition, two readers. If they ever drift, the memo would key on an
    endpoint different from the one actually dialled — the precise defect
    `_connector_class_id` exists to prevent for the other venues.
    """
    monkeypatch.setenv("IB_HOST", "10.0.0.251")
    monkeypatch.setenv("IB_PORT", "4002")
    monkeypatch.setattr(md, "_ib_account_field", lambda field: None)

    built = {}

    class _Spy:
        def __init__(self, **kwargs):
            built.update(kwargs)

    import src.exchange.ib_connector as ibc

    monkeypatch.setattr(ibc, "IBMarketData", _Spy)

    settings = {"EXCHANGE": "interactive_brokers"}
    identity = md._ib_connection_identity(settings)
    md._build_ib_market_data(settings)

    host, port, md_client_id, account, md_type = identity
    assert built["host"] == str(host)
    assert built["port"] == int(port)
    assert built["client_id"] == md_client_id
    assert built["market_data_type"] == md_type
