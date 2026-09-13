"""MI-222 Tier-2 — the collapsed read is now COUNTABLE, and what every caller
receives is UNCHANGED.

Operator-approved 2026-09-09 as **observable-first**: add the counter, the log
line and the ``position_idx`` at ``clients.py``'s ``_emit`` so that "genuinely
flat" / "a zero-size row was returned" / "no row was returned at all" become
three distinguishable, countable facts -- and do NOT change what any caller
sees.

⚠️ THE SECOND HALF IS THE ONE THAT MATTERS, AND IT IS WHY THIS FILE EXISTS.
``account_open_positions``' return value feeds ``_exchange_position_set``
(``order_monitor.py:2884``), which keys on ``(symbol, normalised_side)``, which
gates the CLOSE decision at ``order_monitor.py:4348`` on real money. If the set
of positions a caller receives changes by one element, this shipped the Tier-3
behaviour change the operator did not approve.

So the invariant is asserted DIRECTLY -- same inputs, same returned list -- and
not reasoned about. ``TestReturnValueIsByteIdentical`` below runs the real
function over the same venue payloads the characterization suite uses and pins
the exact list, key by key. A future change that "improves" what is emitted
fails here, loudly, instead of reaching a close decision.

⚠️ **SUPERSEDED IN PART, 2026-09-12 (MI-283) -- READ THIS BEFORE QUOTING THE
PARAGRAPH ABOVE.** The unchanged-return invariant was MI-222's APPROVED SCOPE,
never a claim that the dedupe was correct; this file said so itself, pinning the
drop with the docstring *"The defect is COUNTED, not fixed."* The fix is now
approved and landed: the dedupe keys on ``(symbol, position_idx)`` and the
return carries ``position_idx``. Two assertions are INVERTED rather than
deleted, each naming what it used to say, so the file still records which
behaviour was deliberate and when it changed.

**What is NOT superseded, and is now load-bearing in the other direction:** the
return value may only ever GAIN the book that was being dropped. The class
below still pins the ordinary, flat, zero-size and could-not-read cases exactly
as before, because a "fix" that also moved any of those would be a second,
unapproved behaviour change riding the approved one.

WHAT IS DELIBERATELY NOT DONE, so the next reader does not file it as an
omission:

* **Not registered with ``check_collapsed_states.py``'s CONTRACTS table.** That
  guard requires a consumer that BRANCHES on the states, and there is none --
  BY DESIGN, because the approved scope forbids changing what any caller
  receives. Registering it would mean inventing a decorative branch to satisfy
  a guard, which is the exact failure that guard exists to catch. When a real
  consumer is added, register it then.
* **No behaviour change to the dedupe or the size gate.** The rows that were
  dropped before are still dropped. They are now counted and named.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.units.accounts.clients import account_open_positions


@pytest.fixture
def bybit_account():
    """A bybit cfg whose roster resolves EMPTY, isolating the settleCoin page.

    ``account_id`` is deliberately absent from ``config/accounts.yaml``:
    ``_bybit_configured_symbols`` prefers the cfg's ``symbols`` only when
    TRUTHY and otherwise falls back to loading ``accounts.yaml`` by
    ``account_id``, so ``symbols: []`` on a REAL id is backfilled from config
    and the cross-check runs anyway.
    """
    return {
        "account_id": "bybit_not_in_accounts_yaml",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "market_type": "linear",
        "symbols": [],
    }


def _client_returning(rows):
    class _FakeClient:
        def __init__(self):
            self.calls = []

        def get_positions(self, **kw):
            self.calls.append(kw)
            return {"result": {"list": list(rows)}}

    return _FakeClient()


VENUE_FLAT: list = []

VENUE_ZERO_SIZE_ROW = [
    {"symbol": "ETHUSDT", "side": "Buy", "size": "0", "avgPrice": "2453.97",
     "unrealisedPnl": "0", "positionIdx": 1},
]

# Two LIVE books on one symbol, opposite sides. Both clear the size gate. Until
# 2026-09-12 the SECOND was dropped by the symbol-only dedupe -- the drop that
# reaches money. Both are now returned.
VENUE_TWO_LIVE_BOOKS = [
    {"symbol": "ETHUSDT", "side": "Sell", "size": "0.10", "avgPrice": "2492.67",
     "unrealisedPnl": "-1.0", "positionIdx": 2},
    {"symbol": "ETHUSDT", "side": "Buy", "size": "0.04", "avgPrice": "2453.97",
     "unrealisedPnl": "0.6464", "positionIdx": 1},
]

# The ONLY shape that still reaches the dedupe after 2026-09-12: the venue
# listing the SAME book twice. It is not an expected venue answer, which is
# exactly why the drop-reporting machinery is kept and re-pointed here rather
# than deleted along with the defect it used to fire on.
VENUE_DUPLICATE_BOOK = [
    {"symbol": "ETHUSDT", "side": "Buy", "size": "0.04", "avgPrice": "2453.97",
     "unrealisedPnl": "0.6464", "positionIdx": 1},
    {"symbol": "ETHUSDT", "side": "Buy", "size": "0.04", "avgPrice": "2453.97",
     "unrealisedPnl": "0.6464", "positionIdx": 1},
]

VENUE_ORDINARY = [
    {"symbol": "BTCUSDT", "side": "Buy", "size": "0.005", "avgPrice": "78250.4",
     "unrealisedPnl": "1.23", "positionIdx": 1},
    {"symbol": "SOLUSDT", "side": "Sell", "size": "12", "avgPrice": "150.0",
     "unrealisedPnl": "-0.4", "positionIdx": 2},
]


def _run(account, rows, tmp_path):
    """Call the real function with the soak redirected into tmp_path."""
    client = _client_returning(rows)
    with patch("src.units.accounts.clients.bybit_client_for",
               return_value=client), \
         patch("src.utils.paths.runtime_logs_dir", return_value=tmp_path):
        out = account_open_positions(account)
    soak = tmp_path / "position_read_state_soak.jsonl"
    lines = ([json.loads(x) for x in soak.read_text(encoding="utf-8").splitlines() if x]
             if soak.exists() else [])
    return out, lines, client


# ---------------------------------------------------------------------------
# THE LOAD-BEARING HALF — the return value must not move
# ---------------------------------------------------------------------------


class TestReturnValueIsByteIdentical:
    """Same inputs, same returned list. Asserted, never reasoned about."""

    def test_ordinary_book_returns_exactly_the_expected_rows(
        self, bybit_account, tmp_path,
    ):
        out, _, _ = _run(bybit_account, VENUE_ORDINARY, tmp_path)
        assert out == [
            {"symbol": "BTCUSDT", "side": "Buy", "size": 0.005,
             "entry_price": 78250.4, "unrealised_pnl": 1.23,
             "position_idx": 1},
            {"symbol": "SOLUSDT", "side": "Sell", "size": 12.0,
             "entry_price": 150.0, "unrealised_pnl": -0.4,
             "position_idx": 2},
        ]

    def test_returned_rows_now_carry_position_idx(
        self, bybit_account, tmp_path,
    ):
        """⚠️ INVERTED 2026-09-12 (MI-283) — it read
        ``test_no_position_idx_leaks_into_the_returned_rows`` and asserted the
        RETURN must NOT gain the key, because MI-222's approved scope was
        observation-only. That scope was the reason, not the design: emitting
        ``position_idx`` is half of what the backlog row asks for, and it is
        approved now. The assertion is inverted rather than deleted so the
        history of the decision survives in the file that enforces it.
        """
        out, _, _ = _run(bybit_account, VENUE_ORDINARY, tmp_path)
        assert out, "fixture produced no rows; the assertion below is vacuous"
        for row in out:
            assert set(row) == {
                "symbol", "side", "size", "entry_price", "unrealised_pnl",
                "position_idx",
            }
        assert [r["position_idx"] for r in out] == [1, 2]

    @pytest.mark.parametrize(
        "label,rows,expected",
        [
            ("venue genuinely flat", VENUE_FLAT, []),
            ("venue returned a zero-size row", VENUE_ZERO_SIZE_ROW, []),
        ],
    )
    def test_dropped_rows_are_still_dropped(
        self, bybit_account, tmp_path, label, rows, expected,
    ):
        out, _, _ = _run(bybit_account, rows, tmp_path)
        assert out == expected, f"{label}: the return value moved"

    def test_both_live_hedge_books_are_returned(
        self, bybit_account, tmp_path,
    ):
        """⚠️ INVERTED 2026-09-12 (MI-283) — THIS IS THE WHOLE FIX.

        It read ``test_symbol_dedupe_still_drops_the_second_live_book`` and
        asserted ``len(out) == 1``, with the docstring *"The defect is COUNTED,
        not fixed. Fixing it is the Tier-3 change."* This IS that change.

        Why it matters, concretely: the returned list feeds
        ``order_monitor._exchange_position_set``, which keys on
        ``(symbol, normalised_side)`` and gates the CLOSE decision. The two
        books here are OPPOSITE sides, so dropping one removed a whole
        ``(symbol, side)`` pair from that set and the surviving journal row on
        the other side read FLAT. Two false closes and -$762.496 of
        manufactured loss were attributed to exactly this (#11867).
        """
        out, _, _ = _run(bybit_account, VENUE_TWO_LIVE_BOOKS, tmp_path)
        assert len(out) == 2, f"a live hedge book was dropped: {out}"
        # Both books survive AND keep their own side/size -- a fix that
        # returned two copies of one book would pass a length check alone.
        assert {(r["side"], r["size"], r["position_idx"]) for r in out} == {
            ("Sell", 0.10, 2), ("Buy", 0.04, 1),
        }

    def test_could_not_read_still_returns_none_not_empty(self, bybit_account):
        """``None`` (could not read) must stay distinct from ``[]`` (flat).

        ``_reconcile_orphan_exchange_positions`` skips an account ENTIRELY on
        ``None`` rather than treating it as flat, so collapsing these two would
        teach the reconciler that an unreadable account is an empty one.
        """
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=None):
            assert account_open_positions(bybit_account) is None


# ---------------------------------------------------------------------------
# THE POINT OF THE CHANGE — the three states are now distinguishable
# ---------------------------------------------------------------------------


class TestThreeStatesAreNowCountable:
    """What was one silent value is now three named, counted facts."""

    def test_genuinely_flat_records_no_rows(self, bybit_account, tmp_path):
        _, lines, _ = _run(bybit_account, VENUE_FLAT, tmp_path)
        assert len(lines) == 1
        q = lines[0]["queries"][0]
        assert q["query_state"] == "no_rows"
        assert q["row_count"] == 0
        assert lines[0]["dropped_zero_size_count"] == 0

    def test_zero_size_row_is_distinguishable_from_flat(
        self, bybit_account, tmp_path,
    ):
        """The discrimination MI-221 could not make, now in the record.

        Both reads return ``[]``. The soak line does not: this one says the
        venue ANSWERED with a row and names the book it was on.
        """
        _, lines, _ = _run(bybit_account, VENUE_ZERO_SIZE_ROW, tmp_path)
        row = lines[0]
        assert row["queries"][0]["query_state"] == "rows_returned"
        assert row["dropped_zero_size_count"] == 1
        dropped = row["dropped_zero_size"][0]
        assert dropped["symbol"] == "ETHUSDT"
        assert dropped["position_idx"] == 1
        assert dropped["size_raw"] == "0"

    def test_a_duplicate_book_is_still_named_with_its_position_idx(
        self, bybit_account, tmp_path,
    ):
        """⚠️ RE-POINTED 2026-09-12 (MI-283), NOT DELETED.

        It read ``test_dropped_hedge_book_is_named_with_its_position_idx`` and
        drove ``VENUE_TWO_LIVE_BOOKS``, because a hedge sibling WAS what this
        dedupe dropped. It no longer is. The reporting machinery is still
        correct and still worth pinning, so it is driven by the one shape that
        can still reach it -- the venue listing the same book twice.
        """
        _, lines, _ = _run(bybit_account, VENUE_DUPLICATE_BOOK, tmp_path)
        row = lines[0]
        assert row["dropped_symbol_dedupe_count"] == 1
        dropped = row["dropped_symbol_dedupe"][0]
        assert dropped["symbol"] == "ETHUSDT"
        assert dropped["position_idx"] == 1
        assert dropped["side"] == "Buy"
        assert row["rows_seen"] == 2
        assert row["emitted"] == 1

    def test_two_hedge_books_no_longer_record_a_drop_at_all(
        self, bybit_account, tmp_path,
    ):
        """The counter must go to ZERO for the shape that used to trip it.

        Asserted separately from the return-value test because "both books are
        returned" and "nothing was recorded as dropped" are different facts,
        and a fix that emitted both while still logging a phantom drop would
        keep firing the alarm this change is supposed to retire.
        """
        out, lines, _ = _run(bybit_account, VENUE_TWO_LIVE_BOOKS, tmp_path)
        assert len(out) == 2
        assert lines[0]["dropped_symbol_dedupe_count"] == 0
        assert lines[0]["dropped_symbol_dedupe"] == []
        assert lines[0]["rows_seen"] == 2
        assert lines[0]["emitted"] == 2

    def test_a_failed_cross_check_records_could_not_look(self, tmp_path):
        """A read that FAILED is never a synonym for flat.

        Before this change a failed per-symbol cross-check and a clean empty
        one were both simply "nothing was added to out".
        """
        account = {
            "account_id": "bybit_not_in_accounts_yaml",
            "exchange": "bybit",
            "api_key_env": "BYBIT_KEY_2",
            "market_type": "linear",
            "symbols": ["ETHUSDT"],
        }

        class _PageOkSymbolFails:
            def get_positions(self, **kw):
                if "symbol" in kw:
                    raise RuntimeError("venue unreachable")
                return {"result": {"list": []}}

        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=_PageOkSymbolFails()), \
             patch("src.utils.paths.runtime_logs_dir", return_value=tmp_path):
            out = account_open_positions(account)

        # The primary settleCoin read still stands, so the contract is [].
        assert out == []
        soak = tmp_path / "position_read_state_soak.jsonl"
        row = json.loads(soak.read_text(encoding="utf-8").splitlines()[0])
        assert row["could_not_look_count"] == 1
        scoped = [q for q in row["queries"] if q["scope"] == "symbol_scoped"]
        assert scoped[0]["query_state"] == "could_not_look"
        assert scoped[0]["row_count"] is None
        assert "venue unreachable" in scoped[0]["error"]


# ---------------------------------------------------------------------------
# IT REACHES A HUMAN, AND ONLY WHEN IT SHOULD
# ---------------------------------------------------------------------------


class TestFindingReachesAHumanSurface:
    """``logger.warning`` reaches the journal and nowhere else."""

    def test_duplicate_book_reports_through_outcomes(
        self, bybit_account, tmp_path,
    ):
        """⚠️ RE-POINTED + RENAMED EVENT 2026-09-12 (MI-283).

        It drove ``VENUE_TWO_LIVE_BOOKS`` and asserted the event name
        ``hedge_book_dropped``. Both moved, and the event rename is deliberate:
        after the dedupe keys on ``(symbol, position_idx)`` a hedge sibling
        cannot collide, so that name would describe a cause no code path can
        reach -- ``diagnostic-provenance`` sub-class A, in our own alarm.
        """
        client = _client_returning(VENUE_DUPLICATE_BOOK)
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=client), \
             patch("src.utils.paths.runtime_logs_dir", return_value=tmp_path), \
             patch("src.runtime.outcomes.report") as rep:
            account_open_positions(bybit_account)

        assert rep.call_count == 1
        args, kwargs = rep.call_args
        assert args[0] == "position_read_state"
        assert args[1] == "duplicate_book_dropped"
        assert kwargs["account_id"] == "bybit_not_in_accounts_yaml"

    def test_two_hedge_books_no_longer_page(self, bybit_account, tmp_path):
        """The 506-occurrence warn class is retired, and that is asserted.

        MEASURED: ``hedge_book_dropped`` appears 506x in
        ``docs/claude/ERROR-FEED-DIGEST.md`` and ``position_read_state`` is 168
        of the 1000 (capped, therefore truncated) warn rows measured
        2026-09-11. Every one of those was this shape. If a future change
        re-introduces the page for a routine two-book read, the feed occlusion
        comes back with it -- so it fails here.
        """
        client = _client_returning(VENUE_TWO_LIVE_BOOKS)
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=client), \
             patch("src.utils.paths.runtime_logs_dir", return_value=tmp_path), \
             patch("src.runtime.outcomes.report") as rep:
            out = account_open_positions(bybit_account)
        assert len(out) == 2, "guard: the fixture must produce the two-book read"
        assert rep.call_count == 0

    def test_a_clean_read_does_not_page(self, bybit_account, tmp_path):
        """A WARN per read would be the desensitised-alarm failure."""
        client = _client_returning(VENUE_ORDINARY)
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=client), \
             patch("src.utils.paths.runtime_logs_dir", return_value=tmp_path), \
             patch("src.runtime.outcomes.report") as rep:
            account_open_positions(bybit_account)

        assert rep.call_count == 0

    def test_a_failing_soak_write_never_breaks_the_read(
        self, bybit_account, tmp_path,
    ):
        """This sits on the tick path. It must never take down the read."""
        client = _client_returning(VENUE_ORDINARY)
        with patch("src.units.accounts.clients.bybit_client_for",
                   return_value=client), \
             patch("src.utils.paths.runtime_logs_dir",
                   side_effect=OSError("disk full")):
            out = account_open_positions(bybit_account)

        assert len(out) == 2, "a soak failure must not change the return value"


def test_soak_name_is_on_the_diag_allowlist():
    """Shipped in the SAME commit as the writer, and pinned here so a refactor
    cannot silently drop it.

    A soak that is written and cannot be READ is a write-only log — the
    ``VALIDATION_LOG_PATH`` failure this repo already paid for, and the
    ``exit_loop_health`` #8778 shape. It matters more than usual here: this file
    is the ONLY evidence that could ever justify the Tier-3 behaviour change the
    operator deliberately held back, and the OPEN-ITEMS row that governs it
    (``OI-20260909-POSITION-READ-STATE-SOAK-…``) names this exact route in its
    ``clears_when``. Without the entry that row is unfollowable by construction.

    I shipped the writer without the allowlist entry on the first pass; no guard
    caught it and it was found by reading a sibling row's ``probe_absent_reason``.
    Hence a test rather than a habit.
    """
    pytest.importorskip("fastapi")
    from src.web.api.routers import diag

    from src.units.accounts.clients import POSITION_READ_SOAK_LOG_NAME

    assert "position_read_state_soak" in diag._LOG_FILES
    # The route name and the file the writer actually opens must agree — two
    # constants drifting apart is how a live route starts serving 404 for a log
    # that is being written perfectly well.
    assert (
        diag._LOG_FILES["position_read_state_soak"].name
        == POSITION_READ_SOAK_LOG_NAME
    )
