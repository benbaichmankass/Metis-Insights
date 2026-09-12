"""MI-222 — ``account_open_positions`` collapses three materially different
venue answers into one indistinguishable value, and that collapse is why no
detector layered on top of it can report an unprotected position.

WHY THIS FILE EXISTS. ``docs/CLAUDE-RULES-CANONICAL.md`` and every detector work
object in this repo carry the same rule: **states are never collapsed** — "we did
not look", "we looked and found nothing", and "we found a divergence" are three
different facts and must stay three different facts. This file pins the fact that
the fleet's single position reader **violates that rule at the source**, in
``clients.py::_emit``:

    size = _f(p.get("size"))
    if size <= 0:
        return              # no counter, no log, no positionIdx

Every protective sweep's view of the venue is built on this function. So the
collapse is not a per-call-site nit that a careful caller can work around — it is
inherited by every consumer, and a detector cannot distinguish states its reader
has already merged.

WHAT THIS COST, TWICE, ONE DAY APART. Two real-money P1 investigations stalled on
exactly this missing distinction:

* ``BL-20260908-BYBIT-POSITION-PROTECTION-GRADES-A-SYMBOL-OFF-ROWS0-SO-A-HEDGE-BOOK-READS-FLAT-AND-A-LIVE-POSITION-IS-CLOSED``
  records verbatim: *"NOT ESTABLISHED: which of ``_flat``'s two triggers fired —
  ``not rows`` or ``rows[0].size <= 0``. No repo surface exposes the raw
  get_positions payload; saying which would be a guess."*
* MI-221 (``docs/claude/work/BYBIT2-ETH-PHANTOM-CLOSE-2026-09-09.md`` § 4c) hit
  the identical wall on this function, and named the raw payload "the single
  highest-value missing observation".

WHY IT IS A LIVE-MONEY DEFECT AND NOT ONLY AN OBSERVABILITY ONE. The value this
function returns feeds ``_exchange_position_set`` (``order_monitor.py:2884``),
which keys on ``(symbol, normalised_side)``, which gates the close decision at
``order_monitor.py:4348``. A dropped book reads as flat and a **live position is
closed**.

⚠️ THESE TESTS PIN CURRENT BEHAVIOUR, NOT DESIRED BEHAVIOUR. They are
characterization tests. They assert that the collapse *happens*, so that the day
someone fixes it these tests fail loudly and are updated deliberately rather than
the fix landing unnoticed. Remediation is Tier-2/3 (it changes what the
reconciler considers open on a live order path) and was explicitly HELD by
MI-221 — it is not shipped here.

THE KNOWN-POSITIVE PROBLEM THIS FILE SOLVES. A detector that has only ever
reported "clean" has not been shown able to report anything else. The real
bybit_2/ETHUSDT position cannot serve as a known positive: three independent
venue reads say flat while the operator says open, unreconciled (MI-221 § 5.1).
These fakes are a known positive that costs nothing and touches no venue — each
one is a venue state that a correct reader would distinguish and this one does
not.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.units.accounts.clients import account_open_positions


@pytest.fixture
def bybit_account():
    """A real-money-shaped bybit cfg whose roster genuinely resolves EMPTY.

    ``account_id`` is deliberately one that does NOT appear in
    ``config/accounts.yaml``. That is load-bearing and was got wrong on the
    first attempt, so it is recorded here rather than left as a trap:

    ``_bybit_configured_symbols`` (``clients.py:1221``) returns the cfg's own
    ``symbols`` only when it is TRUTHY, and otherwise falls back to loading
    ``accounts.yaml`` by ``account_id``. So ``symbols: []`` on a REAL account id
    does not suppress the cross-check — it gets **backfilled from config** and
    the sweep runs anyway (pinned in
    ``TestRosterCrossCheckAlreadyExists::test_empty_roster_on_a_real_account_is_backfilled_from_config``).

    An unknown ``account_id`` is therefore the only way to isolate the
    settleCoin page, which these tests need so the collapse is attributable to
    ``_emit`` alone rather than to a second read.
    """
    return {
        "account_id": "bybit_not_in_accounts_yaml",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "market_type": "linear",
        "symbols": [],
    }


def _client_returning(rows):
    """A fake bybit client whose ``get_positions`` always answers ``rows``.

    Records every call so a test can prove how many reads happened.
    """

    class _FakeClient:
        def __init__(self):
            self.calls = []

        def get_positions(self, **kw):
            self.calls.append(kw)
            return {"result": {"list": list(rows)}}

    return _FakeClient()


# The three venue answers under test. They are materially different facts about
# a real-money account and a correct reader would tell them apart.

# (i) The venue genuinely holds nothing on this symbol.
VENUE_FLAT: list = []

# (ii) The venue holds a row for the symbol and reports its size as zero. This
# is what a closed-but-still-listed book looks like, and it is also what the
# *sibling* of a live hedge book looks like.
VENUE_ZERO_SIZE_ROW = [
    {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": "0",
        "avgPrice": "2453.97",
        "unrealisedPnl": "0",
        "positionIdx": 1,
    },
]

# (iii) The venue holds TWO books on the symbol under hedge mode — armed on
# bybit_2/ETHUSDT since 2026-08-30. The zero-size one is listed FIRST. The
# second is a LIVE 0.04 long: exactly trade 5471's shape.
VENUE_HEDGE_BOOK_LIVE_LONG_SECOND = [
    {
        "symbol": "ETHUSDT",
        "side": "Sell",
        "size": "0",
        "avgPrice": "0",
        "unrealisedPnl": "0",
        "positionIdx": 2,
    },
    {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": "0.04",
        "avgPrice": "2453.97",
        "unrealisedPnl": "0.6464",
        "positionIdx": 1,
    },
]


class TestThreeVenueStatesRenderIdentically:
    """The core claim: three different venue answers, one output."""

    @pytest.mark.parametrize(
        "label,rows",
        [
            ("venue genuinely flat", VENUE_FLAT),
            ("venue returned a zero-size row", VENUE_ZERO_SIZE_ROW),
        ],
    )
    def test_flat_and_zero_size_row_are_indistinguishable(
        self, bybit_account, label, rows,
    ):
        """``[]`` from a genuine flat and ``[]`` from a zero-size row.

        The caller receives the same object and there is no counter, no flag and
        no log line anywhere in the return value to tell them apart. This is the
        distinction MI-221 § 4c could not make and that
        BL-20260908 could not make a day earlier.
        """
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_client_returning(rows),
        ):
            out = account_open_positions(bybit_account)

        assert out == [], f"{label}: expected the collapsed empty list"
        # And the decisive part — the value carries NOTHING that discriminates.
        assert out is not None, "None is reserved for 'could not read'"

    def test_a_live_book_survives_a_zero_size_sibling_listed_first(
        self, bybit_account,
    ):
        """A NEGATIVE result, kept because it narrows the blast radius.

        It would be easy to assume a zero-size sibling masks a live book. It
        does not, on this path: ``_emit`` returns on ``size <= 0`` **before**
        ``seen.add(sym)``, so the zero row consumes no dedupe slot and the live
        0.04 long is still emitted.

        This matters for MI-221 § 4a, which could not establish whether the
        hedge-book mechanism closed trade 5471. It rules out one of the two
        candidate shapes: a zero-size sibling alone is NOT sufficient to hide a
        live book. Only TWO NON-ZERO books do that — which is the next test, and
        which requires a second genuinely live ETH book to have existed at
        2026-09-08T13:37Z, still unestablished.
        """
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_client_returning(VENUE_HEDGE_BOOK_LIVE_LONG_SECOND),
        ):
            out = account_open_positions(bybit_account)

        assert out is not None
        # Pin the ACTUAL behaviour. ``size <= 0`` returns before ``seen.add``,
        # so the zero-size sibling does not mask the live book on THIS path.
        assert len(out) == 1, (
            "the live 0.04 long must survive a zero-size sibling listed first"
        )
        assert out[0]["symbol"] == "ETHUSDT"
        assert out[0]["size"] == 0.04

    def test_the_emitted_row_carries_no_position_idx(self, bybit_account):
        """Even when the live book IS emitted, its book identity is erased.

        ``_emit`` builds a fixed five-key dict and ``positionIdx`` is not one of
        them. So a consumer keyed on ``(symbol, side)`` — which is exactly what
        ``_exchange_position_set`` does — cannot tell WHICH book it is holding,
        and two books on one symbol are one entry.
        """
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_client_returning(VENUE_HEDGE_BOOK_LIVE_LONG_SECOND),
        ):
            out = account_open_positions(bybit_account)

        # ⚠️ INVERTED 2026-09-12 (MI-283). It asserted
        # ``"position_idx" not in out[0]`` -- a correct characterization of the
        # defect, which is what made the three venue states indistinguishable
        # to a caller. The row now carries it.
        assert out and out[0]["position_idx"] == 1
        assert set(out[0]) == {
            "symbol", "side", "size", "entry_price", "unrealised_pnl",
            "position_idx",
        }


class TestSymbolDedupeDropsTheSecondBook:
    """⚠️ REPAIRED 2026-09-12 (MI-283) — the dedupe keys on the BOOK now.

    The class name is deliberately left alone: it is what this file
    CHARACTERIZED, and several rows reference it by name. The behaviour it
    characterized is gone; read the test bodies, not the class name.
    """

    def test_two_live_hedge_books_on_one_symbol_are_both_returned(
        self, bybit_account,
    ):
        """⚠️ INVERTED 2026-09-12 (MI-283) — this is the defect being repaired.

        It read ``test_two_live_hedge_books_on_one_symbol_report_as_one`` and
        asserted ``len(out) == 1``, characterizing the hedge-mode drop MI-221
        § 4a confirmed: both books clear the ``size <= 0`` guard, the first
        added ``ETHUSDT`` to ``seen``, and the second hit ``if sym in seen:
        return`` and was **silently dropped**.

        The consequence was a live-money one, which is why it is repaired: if
        the dropped book is the one a journal row sits on,
        ``_exchange_position_set`` lacks that ``(symbol, side)`` pair, the test
        at ``order_monitor.py:4348`` reads flat, and the reconciler CLOSES A
        LIVE POSITION. Two such closes, -$762.496 of manufactured loss, were
        attributed to it (#11867), against a drop rate MEASURED at 376 of 376
        ``bybit_1`` reads (100.0%) over 2026-09-12T03:20:49Z→07:36:53Z.
        """
        rows = [
            {
                "symbol": "ETHUSDT", "side": "Sell", "size": "0.10",
                "avgPrice": "2492.67", "unrealisedPnl": "-1.0",
                "positionIdx": 2,
            },
            {
                "symbol": "ETHUSDT", "side": "Buy", "size": "0.04",
                "avgPrice": "2453.97", "unrealisedPnl": "0.6464",
                "positionIdx": 1,
            },
        ]
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_client_returning(rows),
        ):
            out = account_open_positions(bybit_account)

        assert out is not None
        assert len(out) == 2, f"a live hedge book was dropped: {out}"
        # Both SIDES survive. Asserted on side rather than count alone because
        # the whole harm was the loss of a (symbol, side) pair from
        # ``_exchange_position_set`` -- two rows of the SAME side would pass a
        # length check and still leave the close decision broken.
        assert {p["side"] for p in out} == {"Buy", "Sell"}
        # The live 0.04 LONG -- trade 5471's book, the one that used to vanish.
        longs = [p for p in out if p["side"] == "Buy"]
        assert len(longs) == 1
        assert longs[0]["size"] == 0.04
        assert longs[0]["position_idx"] == 1


class TestCouldNotReadIsStillDistinguishable:
    """The one state the reader DOES keep separate — pinned so it stays that way.

    ``None`` means "could not read" and ``[]`` means "looked, found nothing".
    That distinction is correct and load-bearing: ``_reconcile_orphan_exchange_positions``
    (``order_monitor.py:3129-3135``) skips an account ENTIRELY on ``None`` rather
    than treating it as flat. This test exists so a future refactor that
    "simplifies" ``None`` into ``[]`` fails here instead of silently teaching the
    reconciler that an unreadable account is an empty one.
    """

    def test_missing_creds_returns_none_not_empty_list(self, bybit_account):
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=None,
        ):
            out = account_open_positions(bybit_account)

        assert out is None, "missing creds must not read as a flat book"

    def test_sdk_exception_returns_none_not_empty_list(self, bybit_account):
        class _Boom:
            def get_positions(self, **kw):
                raise RuntimeError("venue unreachable")

        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=_Boom(),
        ):
            out = account_open_positions(bybit_account)

        assert out is None, "a failed read must not read as a flat book"


class TestRosterCrossCheckAlreadyExists:
    """MI-222's structural correction, pinned so it is not rebuilt.

    The MI-222 spawn brief directed this lane to BUILD a roster-driven sweep
    over ``config/accounts.yaml::<account>.symbols``, on the premise that it was
    "the only enumeration complete BY CONSTRUCTION" and did not exist. **It
    already exists**, inside ``account_open_positions`` itself
    (``clients.py:1410``), added by ``BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND``
    for the identical failure shape on the identical real-money account.

    ``tests/test_accounts_clients_open_positions.py::TestBybitPerSymbolCrossCheck``
    already covers its behaviour. This test pins the narrower fact the
    correction turns on — that the roster is consulted **from config** and drives
    a read per configured symbol — so the next lane handed the same brief finds
    an executable answer instead of rebuilding a duplicate that would report the
    target position CLEAN.
    """

    def test_unknown_account_id_resolves_an_empty_roster(self, bybit_account):
        """Asserts the fixture's isolation premise rather than assuming it."""
        client = _client_returning(VENUE_FLAT)
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=client,
        ):
            account_open_positions(bybit_account)

        assert len(client.calls) == 1, "unknown id must not add per-symbol reads"
        assert "symbol" not in client.calls[0]

    def test_empty_roster_on_a_real_account_is_backfilled_from_config(self):
        """⚠️ The roster sweep cannot be switched off from the cfg dict.

        Found by getting it wrong: this test originally asserted that
        ``symbols: []`` suppresses the cross-check. It does not. ``[]`` is
        falsy, so ``_bybit_configured_symbols`` falls through to
        ``accounts.yaml`` and returns the account's REAL roster.

        This strengthens MI-222's correction rather than weakening it. The
        roster-driven enumeration the MI-222 brief asked this lane to BUILD is
        not merely present — for any account whose ``account_id`` is in
        ``accounts.yaml`` it is **unconditional**, and a caller cannot opt out
        by handing in an empty roster. A new roster sweep would therefore
        duplicate an enumeration that is already guaranteed to have run.

        Population = ``config/accounts.yaml::bybit_2.symbols``, 4 symbols.
        """
        account = {
            "account_id": "bybit_2",
            "exchange": "bybit",
            "api_key_env": "BYBIT_KEY_2",
            "market_type": "linear",
            "symbols": [],          # explicitly empty — and overridden anyway
        }
        client = _client_returning(VENUE_FLAT)
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=client,
        ):
            account_open_positions(account)

        scoped = [c.get("symbol") for c in client.calls if "symbol" in c]
        assert scoped == ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT"], (
            "an empty cfg roster must be backfilled from accounts.yaml"
        )

    def test_configured_roster_drives_one_read_per_unseen_symbol(self):
        """The roster sweep, demonstrated: 4 symbols → 1 page + 4 scoped reads.

        This is also the per-cadence cost as a NUMBER, which the brief asked for
        and which is already being paid: ``len(symbols)`` scoped reads per
        account per call, minus symbols the settleCoin page already surfaced.
        Population = bybit_2's configured roster, 4 symbols.
        """
        account = {
            "account_id": "bybit_2",
            "exchange": "bybit",
            "api_key_env": "BYBIT_KEY_2",
            "market_type": "linear",
            "symbols": ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT"],
        }
        client = _client_returning(VENUE_FLAT)
        with patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=client,
        ):
            out = account_open_positions(account)

        # 1 settleCoin page + one scoped read per configured symbol.
        assert len(client.calls) == 5
        scoped = [c.get("symbol") for c in client.calls if "symbol" in c]
        assert scoped == ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT"]

        # ⚠️ AND THE POINT OF THE WHOLE CORRECTION: the roster sweep ran, it
        # read ETHUSDT by name, and it still reports the account CLEAN — because
        # the venue answered flat and the reader cannot say more than that.
        # A new roster-driven detector would produce this same empty result.
        assert out == []
