"""The candle cache must not serve one client's frames to a different client.

WHY THIS FILE EXISTS
--------------------
`_candle_cache_key` used to key on ``id(client)``. `id()` is unique only among
SIMULTANEOUSLY LIVE objects, and the cache stores the integer rather than a
reference — so it never kept the client alive, and CPython was free to hand the
freed address to the next client while the previous client's frame was still
inside its TTL.

That is not a rare edge. Measured: a 2000-iteration allocate-then-drop loop
produced **1999** id collisions, the first at iteration 1 — reuse is the NORM
for short-lived objects. It surfaced as
``test_turtle_soup_flat_market_returns_side_none`` returning ``buy`` on a flat
market, having inherited a sibling stub's bullish-sweep frame
(BL-20260817-CANDLE-CACHE-KEYS-ON-ID-CLIENT-WHICH-CPYTHON-RECYCLES).

`test_the_old_id_key_would_alias` is the load-bearing test: it reproduces the
DEFECT against the old scheme on the same objects the fixed scheme is asked
about. Without it, `test_a_recycled_address_does_not_inherit` could pass simply
because no address was ever reused, and would prove nothing.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.runtime import market_data as md


class _Client:
    """Stands in for a connector: a plain object with an instance ``__dict__``."""


class _Slotted:
    """A client that cannot carry an attribute — the refuse-to-cache path."""

    __slots__ = ()


@pytest.fixture(autouse=True)
def _clear_cache():
    md.reset_candle_cache()
    yield
    md.reset_candle_cache()


def _frame(tag: str) -> pd.DataFrame:
    return pd.DataFrame({"close": [1.0], "tag": [tag]})


def _key(client):
    return md._candle_cache_key(client, "BTCUSDT", "15m", 200, None)


def test_two_live_clients_never_share_a_key():
    """The property the key exists for, stated directly."""
    a, b = _Client(), _Client()
    assert _key(a) is not None
    assert _key(a) != _key(b)


def test_the_same_client_hits_its_own_entry():
    """The fix must not defeat the cache it is fixing."""
    c = _Client()
    md._candle_cache_put(_key(c), _frame("mine"))
    got = md._candle_cache_get(_key(c))
    assert got is not None and got["tag"].iloc[0] == "mine"


def _make_client_at_a_recycled_address():
    """Allocate and drop clients until an address is genuinely reused.

    Returns ``(first_token, second_client, address)`` where the second client
    occupies an address a previous, now-dead client held.
    """
    for _ in range(10_000):
        dead = _Client()
        addr = id(dead)
        token = md._client_identity_token(dead)
        del dead
        fresh = _Client()
        if id(fresh) == addr:
            return token, fresh, addr
        del fresh
    pytest.skip("could not force an id() collision in this interpreter")


def test_the_old_id_key_would_alias():
    """THE DEFECT, reproduced — this is what makes the next test meaningful.

    On the SAME pair of objects the fixed scheme is asked about below, the old
    ``id()``-based key collides. A green suite for the fix means nothing unless
    the bug is shown to be reachable on the same fixtures.
    """
    _tok, fresh, addr = _make_client_at_a_recycled_address()
    old_key_dead = (addr, "BTCUSDT", "15m", 200)
    old_key_fresh = (id(fresh), "BTCUSDT", "15m", 200)
    assert old_key_dead == old_key_fresh, (
        "expected the recycled address to collide under the id() scheme; if it "
        "does not, this test is not exercising the defect and the guard below "
        "is unproven")


def test_a_recycled_address_does_not_inherit(monkeypatch):
    """The fix: a new client at a dead client's address gets a MISS."""
    dead_token, fresh, _addr = _make_client_at_a_recycled_address()
    # Seed the cache as the dead client would have.
    md._candle_cache_put((dead_token, "BTCUSDT", "15m", 200), _frame("dead"))
    assert md._candle_cache_get(_key(fresh)) is None, (
        "the fresh client inherited the dead client's frame — the aliasing "
        "this fix exists to prevent")


def test_tokens_are_never_reused():
    """A monotonic counter, so two distinct objects can never share a token."""
    seen = set()
    for _ in range(500):
        c = _Client()
        tok = md._client_identity_token(c)
        assert tok not in seen
        seen.add(tok)
        del c


def test_a_client_that_cannot_carry_a_token_is_not_cached():
    """Refusing to cache is safe; guessing is what this fix exists to stop."""
    assert md._client_identity_token(_Slotted()) is None
    assert _key(_Slotted()) is None


def test_a_none_client_is_not_cached():
    assert md._client_identity_token(None) is None


def test_a_since_request_is_still_never_cached():
    """The pre-existing carve-out must survive the change."""
    assert md._candle_cache_key(_Client(), "BTCUSDT", "15m", 200, 12345) is None


def test_the_token_is_stable_across_calls():
    """Same object, same token — or every call would miss its own entry."""
    c = _Client()
    assert md._client_identity_token(c) == md._client_identity_token(c)


def test_a_class_level_attribute_is_not_read_as_an_instance_token():
    """A shared class attribute would be the very collision being fixed.

    The lookup goes through ``__dict__``, not ``getattr``, precisely so a value
    defined on the class cannot be mistaken for a per-instance identity.
    """

    class _Shared:
        pass

    setattr(_Shared, md._CLIENT_TOKEN_ATTR, 4242)
    a, b = _Shared(), _Shared()
    ta, tb = md._client_identity_token(a), md._client_identity_token(b)
    assert ta != tb, "two instances shared a class-level token"
    assert 4242 not in (ta, tb), "the class-level value was read as an identity"
