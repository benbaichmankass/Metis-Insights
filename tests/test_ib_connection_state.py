"""IB connection-state legibility (BL-20260707-IB-STATE-LEGIBILITY).

Covers IBClient.connection_state() + the module-level snapshot/writer and the
/api/diag/ib_state read path. Pure observability — no socket, no order path.
"""
from __future__ import annotations

import json

import pytest

from src.units.accounts.ib_client import (
    IBClient,
    snapshot_ib_connection_states,
    write_ib_state_file,
)


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


def test_connection_state_never_connected_before_connect():
    c = _client()
    st = c.connection_state()
    assert st["state"] == "never_connected"
    assert st["connected"] is False
    assert st["breaker_open"] is False
    assert st["last_ok_utc"] is None
    assert st["likely_wedged"] is False


def test_connection_state_connected_after_connect():
    c = _client()
    c.connect()
    st = c.connection_state()
    assert st["state"] == "connected"
    assert st["connected"] is True
    assert st["last_ok_utc"] is not None
    assert st["last_ok_age_seconds"] is not None
    assert st["consecutive_failures"] == 0
    assert st["likely_wedged"] is False


def test_connection_state_breaker_open_is_transitory():
    c = _client()
    c._trip_breaker(reason="liveness_probe_timeout")
    st = c.connection_state()
    assert st["state"] == "breaker_open"
    assert st["breaker_open"] is True
    assert st["breaker_seconds_remaining"] > 0
    assert st["consecutive_failures"] == 1
    # one failure = transitory, not a wedge
    assert st["likely_wedged"] is False
    assert st["last_fail_reason"] == "liveness_probe_timeout"


def test_connection_state_likely_wedged_after_three_failures():
    c = _client()
    for _ in range(3):
        c._trip_breaker(reason="account_warmup_timeout")
    st = c.connection_state()
    assert st["consecutive_failures"] == 3
    assert st["likely_wedged"] is True


def test_healthy_connect_clears_failure_streak():
    c = _client()
    c._trip_breaker(reason="connect_failed")
    c._trip_breaker(reason="connect_failed")
    # force the breaker window to elapse so connect() doesn't fast-fail
    c._breaker_open_until = 0.0
    c.connect()
    st = c.connection_state()
    assert st["state"] == "connected"
    assert st["consecutive_failures"] == 0


def test_snapshot_and_write_file(tmp_path):
    c = _client(client_id=4242)
    c.connect()
    # the connected client is now in the process registry via get_ib_client?
    # connection_state() is read directly here; snapshot reads the registry.
    snap = c.connection_state()
    assert snap["client_id"] == 4242

    target = tmp_path / "ib_state.json"
    write_ib_state_file(path=target)
    assert target.exists()
    payload = json.loads(target.read_text())
    assert "generated_at" in payload
    assert isinstance(payload["clients"], list)


def test_write_file_never_raises_on_bad_path():
    # A directory that cannot be written to must not raise (best-effort).
    write_ib_state_file(path="/nonexistent-dir-xyz/ib_state.json")  # no exception


@pytest.mark.parametrize("bad", [None])
def test_snapshot_returns_list(bad):
    assert isinstance(snapshot_ib_connection_states(), list)
