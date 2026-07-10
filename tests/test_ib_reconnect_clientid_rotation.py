"""IB post-restart reconnect wedge — teardown + clientId rotation.

BL-20260709-IB-POSTRESTART-RECONNECT-WEDGE. After a gateway restart the old
socket goes half-open (isConnected() False) while the gateway still holds the
base clientId, so a fresh connect on the SAME id times out until an external
restart reaps it (~18-min wedge). The fix: tear down the stale handle before a
fresh connect, disconnect a failed handle instead of leaking it, and — after N
consecutive failures on the base id — rotate to a fresh clientId so the stale
session can't keep blocking the reconnect.

With ``_ib_factory`` set, ``_probe_liveness``/``_warm_account_data`` self-skip
(there is no real socket), so ``connect()`` succeeds iff ``ib.connect()`` does
not raise. Mirrors the stub seam in ``tests/test_ib_connection_state.py``.
"""
from __future__ import annotations

import pytest

import src.units.accounts.ib_client as ibmod
from src.units.accounts.ib_client import IBClient, IBConnectionError


class _FlakyIB:
    """Stub whose connect() times out for clientIds in ``stuck``; records every
    clientId tried + disconnect() calls."""

    def __init__(self, stuck, attempts):
        self.stuck = stuck
        self.attempts = attempts
        self._connected = False
        self.disconnect_calls = 0

    def connect(self, host, port, clientId, timeout=10.0, readonly=False):
        self.attempts.append(clientId)
        if clientId in self.stuck:
            raise TimeoutError("simulated stuck clientId (stale gateway session)")
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self.disconnect_calls += 1
        self._connected = False

    def managedAccounts(self):
        return ["DUQ325724"]

    def accountSummary(self, account=None):
        return []


def test_reconnect_rotates_clientid_after_threshold(monkeypatch):
    monkeypatch.setattr(ibmod, "_IB_RECONNECT_ROTATE_CLIENTID_AFTER", 2)
    monkeypatch.setattr(ibmod, "_IB_RECONNECT_CLIENTID_STRIDE", 100)
    # Never let the real reset-window suppress rotation while the suite runs.
    monkeypatch.setattr(IBClient, "_in_ibkr_reset_window", staticmethod(lambda: False))
    attempts: list[int] = []
    c = IBClient(
        port=7497,
        client_id=498,
        account="DUQ",
        _ib_factory=lambda: _FlakyIB(stuck={498}, attempts=attempts),
    )
    for _ in range(2):  # two failures on the base id (fail_count 1, then 2)
        c._breaker_open_until = 0.0  # clear cooldown so connect() actually runs
        with pytest.raises(IBConnectionError):
            c.connect()
    c._breaker_open_until = 0.0
    c.connect()  # 3rd attempt: fail_count>=after → rotate off the stuck base id
    assert attempts[:2] == [498, 498]  # base id tried while under threshold
    assert attempts[-1] == 598  # rotated (498 + 1*100)
    assert c._breaker_fail_count == 0  # healthy connect cleared the streak


def test_reconnect_disconnects_stale_handle_before_fresh_connect(monkeypatch):
    monkeypatch.setattr(IBClient, "_in_ibkr_reset_window", staticmethod(lambda: False))
    attempts: list[int] = []
    made: list[_FlakyIB] = []

    def factory():
        ib = _FlakyIB(stuck=set(), attempts=attempts)
        made.append(ib)
        return ib

    c = IBClient(port=7497, client_id=498, account="DUQ", _ib_factory=factory)
    c.connect()  # made[0] connects cleanly
    made[0]._connected = False  # simulate gateway restart: half-open, NOT disconnected
    c._breaker_open_until = 0.0
    c.connect()  # reconnect must tear down made[0] first, then build a fresh handle
    assert made[0].disconnect_calls >= 1
    assert len(made) == 2  # a fresh handle was built


def test_failed_connect_does_not_leak_the_handle(monkeypatch):
    monkeypatch.setattr(ibmod, "_IB_RECONNECT_ROTATE_CLIENTID_AFTER", 0)  # rotation off
    attempts: list[int] = []
    made: list[_FlakyIB] = []

    def factory():
        ib = _FlakyIB(stuck={498}, attempts=attempts)
        made.append(ib)
        return ib

    c = IBClient(port=7497, client_id=498, account="DUQ", _ib_factory=factory)
    c._breaker_open_until = 0.0
    with pytest.raises(IBConnectionError):
        c.connect()
    assert made[-1].disconnect_calls >= 1  # failed handle disconnected, not leaked
    assert c._ib is None
