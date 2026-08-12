"""IBClient serialises socket use across threads — section 6.4.

`_REGISTRY_LOCK` guards only lookup/creation, so once `ib_read_client_for` returns,
every caller shares one `IBClient` wrapping ONE `ib_insync.IB` — one socket on one
clientId, asyncio-driven and not thread-safe. Two threads on one socket is worse
than BL-20260706-IBACCTUPDATES-COLLISION, which was two DIFFERENT clients on one
account and still broke `accountDownloadEnd` delivery.

Inline that was impossible (single-threaded trader). The exit-half decouple makes
it possible, so these pin the guard before the loop exists.
"""
from __future__ import annotations

import threading
import time

from src.units.accounts.ib_client import IBClient


def _client() -> IBClient:
    return IBClient(host="127.0.0.1", port=4002, client_id=1, symbol="MES")


# --- reentrancy is REQUIRED, not a preference ---------------------------------

def test_the_lock_is_reentrant():
    """12 of 17 public methods call self.connect(); modify_protective calls
    place_protective; self_test calls balance. A non-reentrant lock deadlocks on
    essentially every operation, and the symptom would be a frozen exit loop —
    exactly what exit_loop_health alerts on, via the fix meant to prevent it."""
    c = _client()
    with c._usage_lock:
        with c._usage_lock:          # would hang forever on a plain Lock
            pass


def test_nested_public_calls_do_not_deadlock(monkeypatch):
    """The real shape: an outer locked method invoking another locked one."""
    c = _client()
    monkeypatch.setattr(c, "_locked_connect", lambda: "connected")
    monkeypatch.setattr(c, "_locked_balance", lambda: {"ok": True})

    done: list = []

    def outer() -> None:
        with c._usage_lock:
            c.connect()              # re-enters
            done.append(c.balance())  # re-enters again

    t = threading.Thread(target=outer, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "nested public calls deadlocked"
    assert done == [{"ok": True}]


# --- the lock actually excludes ------------------------------------------------

def test_two_threads_never_overlap_inside_a_socket_call(monkeypatch):
    """The property the guard exists for: no interleaving on one socket."""
    c = _client()
    inside = []
    overlaps = []

    def slow_positions():
        inside.append(1)
        if len(inside) > 1:
            overlaps.append(len(inside))
        time.sleep(0.02)
        inside.pop()
        return []

    monkeypatch.setattr(c, "_locked_positions", slow_positions)
    threads = [threading.Thread(target=c.positions) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not overlaps, f"two threads were inside the socket call at once: {overlaps}"


def test_each_client_has_its_own_lock():
    """Two clients are two sockets on two clientIds — serialising across them
    would be pure lost throughput for no safety."""
    a, b = _client(), _client()
    assert a._usage_lock is not b._usage_lock


# --- state readers stay OUT of the lock, on purpose ---------------------------

def test_state_readers_do_not_block_behind_a_held_lock(monkeypatch):
    """connect() can hold the lock ~20s (probe + retry + warm-up + retry), and
    write_ib_state_file runs on the main tick precisely to report a wedged client.
    A diag surface that hangs whenever the thing it describes is busy reports
    nothing exactly when it is needed."""
    c = _client()
    released = threading.Event()
    reader_done = threading.Event()

    def holder() -> None:
        with c._usage_lock:
            released.wait(timeout=5)

    h = threading.Thread(target=holder, daemon=True)
    h.start()
    time.sleep(0.05)                 # let the holder acquire

    def reader() -> None:
        _ = c.connected              # a PROPERTY, not a method — must NOT block
        c.connection_state()
        c.fingerprint()
        reader_done.set()

    r = threading.Thread(target=reader, daemon=True)
    r.start()
    r.join(timeout=2)
    assert reader_done.is_set(), (
        "a state reader blocked behind the usage lock — observability must not "
        "hang while the client it describes is busy")
    released.set()
    h.join(timeout=5)


# --- every socket-touching method is actually behind it -----------------------

def test_every_socket_method_is_wrapped():
    """Structural: a socket method added later without the wrap reintroduces the
    hazard silently, and the failure mode is corruption rather than an exception."""
    import inspect
    import re
    src = inspect.getsource(IBClient)
    EXPECTED = ["connect", "disconnect", "place", "place_protective",
                "modify_protective", "close", "cancel_resting_protection",
                "has_protective_orders", "cancel", "status", "balance",
                "positions", "executions", "self_test"]
    missing = [m for m in EXPECTED
               if not re.search(rf"def {m}\(.*?\n(?:.*?\n)*?"
                                rf"\s+with self\._usage_lock:\s*\n"
                                rf"\s+return self\._locked_{m}\(", src)]
    assert not missing, f"socket methods not behind the usage lock: {missing}"
