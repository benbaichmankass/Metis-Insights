"""MI-283 — the Bybit position dedupe keys on the BOOK, not on the symbol.

``BL-20260909-ACCOUNT-OPEN-POSITIONS-DEDUPES-BYBIT-POSITIONS-BY-SYMBOL-SO-A-HEDGE-BOOK-IS-DROPPED-AND-THE-ORDER-STATUS-RECONCILER-CLOSES-A-LIVE-ROW``.

WHAT WAS WRONG. ``account_open_positions``' bybit branch gated on
``if sym in seen`` — SYMBOL alone, consulting no account and no
``positionIdx``. Since ``BYBIT_HEDGE_MODE_SYMBOLS`` was armed (2026-08-30) a
hedge symbol returns one row per book, so the second LIVE book was discarded.

WHY THAT REACHED MONEY. The returned list feeds
``order_monitor._exchange_position_set``, which keys on
``(symbol, normalised_side)``. The two books of a hedge symbol are OPPOSITE
sides, so dropping one removed a whole ``(symbol, side)`` pair from that set —
and the journal row sitting on the dropped side then read FLAT at the close
test, so the order-status reconciler closed a live position.

MEASURED (``/api/diag/log_file?name=position_read_state_soak``, a 1000-row tail
of a 14,722,608-byte file, read 2026-09-12, window 03:20:49Z→07:36:53Z):

| account           | reads | reads dropping a NON-ZERO book |
|-------------------|------:|-------------------------------:|
| ``bybit_1``       |   376 |                **376 (100.0%)**|
| ``bybit_2`` (real)|   368 |                       0 (0.0%) |
| ``bybit_portfolio``|  256 |                       0 (0.0%) |

Dropped on ``bybit_1``: ``SOLUSDT idx=1 Buy`` ×291, ``BTCUSDT idx=2 Sell`` ×241.
Two false closes totalling **-$762.496** of manufactured loss were attributed
to it (#11867).

⚠️ **THE REAL-MONEY ZERO IS NOT SAFETY, AND THE MECHANISM IS WHY.** The
``size <= 0`` skip runs BEFORE the dedupe and a zero-size row never enters
``seen``, so the dedupe bites only when TWO books are BOTH non-zero. Over that
same window ``bybit_2`` enumerated both books on every single read (``BTCUSDT``
idx1+idx2 and ``ADAUSDT`` idx1+idx2, 368 reads each) — every one of them
zero-size. The hedge enumeration is live on real money; the account simply held
no two-sided book in the window. The exposure is structural and one two-sided
position away, not absent.

⚠️ **A UNIT TEST CLEARS NOTHING HERE AND THIS FILE DOES NOT CLAIM TO.** A
fixture cannot reproduce which row Bybit lists first, and that ordering IS the
mechanism. These tests pin the DECISION; the fleet observation is separate and
is recorded in the work object.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.units.accounts.clients import (
    _bybit_book_key,
    account_bybit_open_orders,
    account_open_positions,
)
from src.runtime.order_monitor import _exchange_position_set


def _acct(**kw):
    base = {
        "account_id": "bybit_not_in_accounts_yaml",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "market_type": "linear",
        "symbols": [],
    }
    base.update(kw)
    return base


def _client(rows, orders=None):
    class _Fake:
        def __init__(self):
            self.calls = []

        def get_positions(self, **kw):
            self.calls.append(kw)
            return {"result": {"list": list(rows)}}

        def get_open_orders(self, **kw):
            self.calls.append(kw)
            return {"result": {"list": list(orders or [])}}

    return _Fake()


def _run(account, rows, tmp_path):
    c = _client(rows)
    with patch("src.units.accounts.clients.bybit_client_for", return_value=c), \
         patch("src.utils.paths.runtime_logs_dir", return_value=tmp_path):
        return account_open_positions(account), c


#: The live ``bybit_1`` SOLUSDT shape, both books non-zero. idx=1 Buy is the
#: one the soak recorded as dropped 291 times.
TWO_LIVE_BOOKS = [
    {"symbol": "SOLUSDT", "side": "Sell", "size": "776.8", "avgPrice": "150.0",
     "unrealisedPnl": "-1.0", "positionIdx": 2},
    {"symbol": "SOLUSDT", "side": "Buy", "size": "776.8", "avgPrice": "149.0",
     "unrealisedPnl": "0.6", "positionIdx": 1},
]


# ---------------------------------------------------------------------------
# THE SEAM WHERE THE HARM HAPPENED — asserted end to end, not reasoned about
# ---------------------------------------------------------------------------


class TestTheCloseDecisionCanSeeBothBooks:
    """``account_open_positions`` → ``_exchange_position_set`` → the close test.

    This crosses the seam the defect lived on. Pinning only the returned list
    would leave the actual claim — *the reconciler can now see the long* —
    unasserted, and that claim is the entire point of the change.
    """

    def test_both_sides_reach_the_reconcilers_lookup_set(self, tmp_path):
        out, _ = _run(_acct(), TWO_LIVE_BOOKS, tmp_path)
        live = _exchange_position_set(out)
        assert ("SOLUSDT", "long") in live, (
            "the LONG book is invisible to the close test — this is the "
            "condition that closed trades 5704 and 5705"
        )
        assert ("SOLUSDT", "short") in live
        assert live == {("SOLUSDT", "long"), ("SOLUSDT", "short")}

    def test_the_set_can_only_gain_pairs_never_lose_them(self, tmp_path):
        """The safety argument for a Tier-2 order-path change, asserted.

        Every money-path consumer reads this SET. The change can only ever add
        a row that was previously discarded, and a set makes a same-side
        duplicate idempotent — so the set is a superset of what it was, and the
        reconciler moves from CLOSE toward DEFER. It can never move the other
        way. If a future edit makes it possible to LOSE a pair, this fails.
        """
        out, _ = _run(_acct(), TWO_LIVE_BOOKS, tmp_path)
        after = _exchange_position_set(out)
        # What the symbol-only dedupe would have produced: first book only.
        before = _exchange_position_set([dict(TWO_LIVE_BOOKS[0])])
        assert before <= after
        assert after - before == {("SOLUSDT", "long")}

    def test_a_same_side_duplicate_is_idempotent_in_the_set(self):
        """Why an extra row is harmless where a missing one is not."""
        dup = [
            {"symbol": "SOLUSDT", "side": "Buy", "size": 1.0},
            {"symbol": "SOLUSDT", "side": "Buy", "size": 1.0},
        ]
        assert _exchange_position_set(dup) == {("SOLUSDT", "long")}


# ---------------------------------------------------------------------------
# THE DEDUPE KEY
# ---------------------------------------------------------------------------


class TestBookKey:
    @pytest.mark.parametrize("raw,expected", [
        (0, 0), (1, 1), (2, 2), ("1", 1), (" 2 ", 2), (-1, -1),
    ])
    def test_declared_books_parse_to_ints(self, raw, expected):
        assert _bybit_book_key(raw) == expected

    def test_absent_book_id_reproduces_the_old_symbol_only_key(self):
        """A venue that sends no ``positionIdx`` must be UNAFFECTED.

        ``None`` for every row of a symbol means every row keys alike, which is
        exactly the pre-2026-09-12 behaviour. That is the conservative fallback
        and it is deliberate: this change must not alter a one-way venue.
        """
        assert _bybit_book_key(None) is None

    def test_an_unparseable_book_id_is_never_equal_to_a_real_one(self):
        """``we could not read the book id`` must not collapse two live books.

        Polarity note, because it is the OPPOSITE of
        ``bybit_position_book._parse_size``'s and that looks inconsistent until
        the questions are separated: that module SELECTS one book and refuses
        when unsure; this one LISTS what exists, where a DROP is what closes
        live positions and an extra row is idempotent downstream (asserted
        above). Erring toward emitting is the safe direction for a listing.
        """
        bad = _bybit_book_key("not-a-book")
        assert bad != 1 and bad != 2 and bad != 0 and bad is not None
        assert bad != _bybit_book_key("also-not-a-book")
        # Identical unreadable values still dedupe — the conservative half.
        assert bad == _bybit_book_key("not-a-book")

    def test_an_unparseable_id_still_emits_both_rows(self, tmp_path):
        rows = [
            {"symbol": "SOLUSDT", "side": "Sell", "size": "1", "positionIdx": "x"},
            {"symbol": "SOLUSDT", "side": "Buy", "size": "1", "positionIdx": "y"},
        ]
        out, _ = _run(_acct(), rows, tmp_path)
        assert len(out) == 2


# ---------------------------------------------------------------------------
# WHAT MUST NOT MOVE
# ---------------------------------------------------------------------------


class TestNoUnapprovedBehaviourRidesAlong:
    def test_the_zero_size_skip_is_unchanged(self, tmp_path):
        """A zero-size row is still dropped, and still before the dedupe.

        Load-bearing: it is why ``bybit_2`` recorded zero dedupe drops while
        enumerating both books. If the order flipped, a zero row would enter
        the dedupe and the measurement above would stop meaning what it means.
        """
        rows = [
            {"symbol": "ADAUSDT", "side": "Buy", "size": "0", "positionIdx": 1},
            {"symbol": "ADAUSDT", "side": "Sell", "size": "0", "positionIdx": 2},
        ]
        out, _ = _run(_acct(), rows, tmp_path)
        assert out == []

    def test_could_not_read_is_still_none_not_empty(self):
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=None):
            assert account_open_positions(_acct()) is None

    def test_the_cross_check_fires_no_extra_venue_call(self, tmp_path):
        """⚠️ THE REGRESSION THIS CHANGE MOST EASILY CAUSES, PINNED.

        The per-symbol cross-check is gated on ``sym in seen``. Re-keying THAT
        set to ``(symbol, book)`` would make the membership test always false
        and fire a fresh ``get_positions`` for every configured symbol on every
        read — an unbounded per-tick broker round-trip, which is the shape of
        both June 2026 wedges. So ``seen`` stays symbol-keyed and this asserts
        it: a symbol the settleCoin page already surfaced gets NO second call.
        """
        rows = [
            {"symbol": "SOLUSDT", "side": "Buy", "size": "1", "positionIdx": 1},
            {"symbol": "SOLUSDT", "side": "Sell", "size": "1", "positionIdx": 2},
        ]
        out, client = _run(_acct(symbols=["SOLUSDT"]), rows, tmp_path)
        assert len(out) == 2
        scoped = [c for c in client.calls if c.get("symbol")]
        assert scoped == [], (
            f"the cross-check re-queried an already-surfaced symbol: {scoped}"
        )

    def test_an_unsurfaced_symbol_is_still_cross_checked(self, tmp_path):
        """The other half — the cross-check must still DO its job.

        Without this, the test above passes vacuously for a change that
        disabled the cross-check entirely (BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND,
        which false-closed a real-money BTCUSDT row).
        """
        rows = [
            {"symbol": "SOLUSDT", "side": "Buy", "size": "1", "positionIdx": 1},
        ]
        _, client = _run(_acct(symbols=["SOLUSDT", "BTCUSDT"]), rows, tmp_path)
        scoped = [c.get("symbol") for c in client.calls if c.get("symbol")]
        assert scoped == ["BTCUSDT"]


# ---------------------------------------------------------------------------
# THE SECOND SITE — the diag read surface
# ---------------------------------------------------------------------------


class TestDiagReadSurfaceAlsoSeesBothBooks:
    """``account_bybit_open_orders`` carried the IDENTICAL defect.

    Not named by the backlog row, which describes only
    ``account_open_positions``. Found 2026-09-12 by grepping the fix site's own
    pattern across the file. It is NOT the order path — its sole consumer is
    ``/api/diag/bybit_open_orders`` — but that route is the instrument
    ``OI-20260909-INTENT-REDUCE-LEG-RESIZE-...`` tells a session to verify a
    resized protective leg against, so a dropped book means a VERIFICATION made
    against the wrong book.
    """

    def test_both_hedge_books_appear_on_the_diag_surface(self):
        c = _client(TWO_LIVE_BOOKS, orders=[])
        with patch("src.units.accounts.clients.bybit_client_for", return_value=c):
            out = account_bybit_open_orders(_acct())
        assert out is not None
        idxs = sorted(p["position_idx"] for p in out["positions"])
        assert idxs == [1, 2], f"a hedge book was dropped from diag: {out}"

    def test_could_not_look_is_still_none(self):
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=None):
            assert account_bybit_open_orders(_acct()) is None
