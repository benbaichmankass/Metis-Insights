"""The liveness probe is verified once per window, not once per fetch.

`BL-20260816-IB-QUEUE-TIMEOUT-EXCEEDS-EXIT-BUDGET`, workplan item 1.0.

WHY THIS FILE EXISTS. `IBClient.connect()` runs on EVERY IB market-data fetch,
and `_probe_liveness` ran on every one of them — including the cached-handle
path that otherwise short-circuits. Measured on the live trader 2026-08-22 over
four disjoint journal windows spanning 01:30Z-07:40Z (n = 75 events / 2226 s):
the first probe attempt timed out at 5.0 s on 2.02 calls per minute and the
retry then answered, costing 6.5 s each — 488 s of blocking in 2226 s, 21.9% of
wall clock — while `liveness probe timed out twice`, the branch that actually
condemns a connection, fired ZERO times in that population.

The tests below are written to FAIL against the pre-2026-08-22 file: there,
`_probe_liveness` is called once per `connect()`, so the call counts asserted
here are wrong by construction. That is the point — a test that passes either
way would not have caught the tax.

What is deliberately NOT changed, and is asserted here so it cannot drift:
a FRESH connect still always probes, a probe FAILURE is never cached, and
`IB_PROBE_CACHE_S <= 0` restores the old behaviour exactly.
"""
from __future__ import annotations

import time

import pytest

from src.units.accounts import ib_client as ib_mod
from src.units.accounts.ib_client import IBClient, IBConnectionError


class _StubIB:
    def __init__(self):
        self._connected = False

    def connect(self, host, port, clientId, timeout=10.0, readonly=False):
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    def managedAccounts(self):
        return ["DUQ325724"]

    def accountSummary(self, account=None):
        return []


def _client(**kw):
    return IBClient(
        port=kw.pop("port", 7497),
        client_id=kw.pop("client_id", 1),
        account=kw.pop("account", "DUQ325724"),
        _ib_factory=lambda: _StubIB(),
        **kw,
    )


def _count_probes(client, *, result=True):
    """Replace `_probe_liveness` with a counter. Returns the mutable count list."""
    calls: list[int] = []

    def _fake(_ib):
        calls.append(1)
        return result

    client._probe_liveness = _fake  # type: ignore[method-assign]
    return calls


# ---------------------------------------------------------------- the tax


def test_cached_handle_is_probed_once_not_once_per_connect(monkeypatch):
    """THE FALSIFIER. Ten connects on one live handle → ONE probe.

    Pre-fix this is 10 — one per `connect()` — which at the measured live rate
    is the 6.5 s-per-fetch tax this change exists to remove.
    """
    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", 60.0)
    c = _client()
    calls = _count_probes(c)
    for _ in range(10):
        c.connect()
    assert len(calls) == 1, (
        f"expected exactly 1 probe across 10 connects on one cached handle, "
        f"got {len(calls)}"
    )


def test_probe_runs_again_once_the_window_expires(monkeypatch):
    """The cache SKIPS a repeat; it does not retire the check."""
    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", 60.0)
    c = _client()
    calls = _count_probes(c)
    c.connect()
    assert len(calls) == 1
    c.connect()
    assert len(calls) == 1
    # Expire the window without sleeping through it.
    c._probe_ok_until = time.monotonic() - 0.001
    c.connect()
    assert len(calls) == 2, "an expired verdict must be re-probed, not trusted"


# ------------------------------------------------------- the rollback path


def test_zero_cache_probes_every_connect_exactly_as_before(monkeypatch):
    """`IB_PROBE_CACHE_S <= 0` is the sanctioned one-env-flip rollback."""
    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", 0.0)
    c = _client()
    calls = _count_probes(c)
    for _ in range(5):
        c.connect()
    assert len(calls) == 5
    assert c._probe_cache_valid() is False
    assert c._probe_cache_seconds_remaining() is None


def test_negative_cache_is_treated_as_disabled(monkeypatch):
    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", -5.0)
    c = _client()
    calls = _count_probes(c)
    c.connect()
    c.connect()
    assert len(calls) == 2


def test_unparseable_env_falls_back_to_the_default_not_to_zero(monkeypatch):
    """A typo must not silently change behaviour in EITHER direction."""
    monkeypatch.setenv("IB_PROBE_CACHE_S", "not-a-number")
    assert ib_mod._env_float("IB_PROBE_CACHE_S", 60.0) == 60.0
    monkeypatch.setenv("IB_PROBE_CACHE_S", "")
    assert ib_mod._env_float("IB_PROBE_CACHE_S", 60.0) == 60.0


# --------------------------------------------- what must NOT be weakened


def test_a_fresh_connect_always_probes_even_with_a_live_verdict(monkeypatch):
    """The case the probe was BUILT for is untouched.

    A cached verdict describes the handle we held; a new socket is a new
    subject and the old verdict cannot speak for it.
    """
    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", 3600.0)
    c = _client()
    calls = _count_probes(c)
    c.connect()
    assert len(calls) == 1
    remaining_before = c._probe_cache_seconds_remaining()
    assert remaining_before is not None and remaining_before > 0
    # Drop the socket. The verdict must not survive it.
    c.disconnect()
    assert c._probe_ok_until == 0.0
    assert c._probe_cache_seconds_remaining() is None
    c.connect()
    assert len(calls) == 2, "a fresh handle must be probed, cache or not"


def test_a_failing_probe_on_a_cached_handle_still_condemns(monkeypatch):
    """A mid-life wedge is still caught — the cache never suppresses a failure."""
    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", 3600.0)
    monkeypatch.setattr(ib_mod, "_IB_PROBE_TRUST_FRESH_HANDSHAKE", False)
    c = _client()
    _count_probes(c, result=True)
    c.connect()
    assert c._probe_cache_valid() is True
    # The gateway wedges mid-life. Force the next probe to run and fail.
    c._probe_ok_until = time.monotonic() - 0.001
    _count_probes(c, result=False)
    with pytest.raises(IBConnectionError):
        c.connect()
    assert c._probe_ok_until == 0.0, "a failed probe must never leave a verdict"
    assert c._probe_cache_valid() is False


def test_a_failed_probe_is_not_cached_on_the_best_effort_path(monkeypatch):
    """Proceeding best-effort is not the same as having verified anything."""
    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", 3600.0)
    monkeypatch.setattr(ib_mod, "_IB_PROBE_TRUST_FRESH_HANDSHAKE", True)
    c = _client()
    _count_probes(c, result=False)
    c.connect()  # fresh handshake trusted, probe did not answer
    assert c._probe_ok_until == 0.0
    assert c._probe_cache_valid() is False


# ------------------------------------------------------------ legibility


def test_connection_state_distinguishes_no_verdict_from_an_expiring_one(monkeypatch):
    """`None` and `0.0` are different statements and must stay distinguishable."""
    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", 60.0)
    c = _client()
    st = c.connection_state()
    assert st["probe_cache_seconds_remaining"] is None
    assert st["probe_cache_configured_s"] == 60.0

    _count_probes(c)
    c.connect()
    st = c.connection_state()
    assert st["probe_cache_seconds_remaining"] is not None
    assert 0.0 < st["probe_cache_seconds_remaining"] <= 60.0

    monkeypatch.setattr(ib_mod, "_IB_PROBE_CACHE_S", 0.0)
    assert c.connection_state()["probe_cache_seconds_remaining"] is None
