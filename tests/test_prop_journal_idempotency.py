"""prop_journal.insert_fill idempotency (BL-20260706-PROP-INSERT-FILL-IDEMPOTENCY).

A re-reported prop fill (operator re-report / corrective re-report / relay retry)
must UPDATE the existing row in place, not append a duplicate — the prop_fills
id 15/16 same-ETH-trade double-log. Kept in its own module (no FastAPI TestClient
import) so it runs without the httpx test dependency.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.prop import prop_journal


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return db


def _count(account_id: str = "breakout_1") -> int:
    return len(prop_journal.list_fills(account_id=account_id, limit=1000))


def test_reposting_same_fill_updates_in_place(isolated_db: Path) -> None:
    """Same (account, external_order_id, status) → one row, updated, same id."""
    base = {
        "account_id": "breakout_1", "external_order_id": "500139412",
        "symbol": "ETHUSDT", "direction": "long", "status": "open",
        "entry_price": 1800.0, "qty": 1.0,
    }
    id1 = prop_journal.insert_fill(dict(base))
    # Re-report the SAME fill (relay retry) with a corrected entry price.
    id2 = prop_journal.insert_fill(dict(base, entry_price=1801.5))
    assert id1 == id2, "re-report must reuse the existing row id"
    assert _count() == 1, "re-report must not append a duplicate"
    rows = prop_journal.list_fills(account_id="breakout_1", limit=10)
    assert rows[0]["entry_price"] == 1801.5, "the update must land"


def test_direction_synonym_repost_is_deduped(isolated_db: Path) -> None:
    """buy then long for the SAME external_order_id+status → one canonical row.

    This is the exact prop_fills id15(buy)/id16(long) double-log.
    """
    id1 = prop_journal.insert_fill({
        "account_id": "breakout_1", "external_order_id": "500139412",
        "symbol": "ETHUSDT", "direction": "buy", "status": "open", "qty": 1.0,
    })
    id2 = prop_journal.insert_fill({
        "account_id": "breakout_1", "external_order_id": "500139412",
        "symbol": "ETHUSDT", "direction": "long", "status": "open", "qty": 1.0,
    })
    assert id1 == id2
    assert _count() == 1
    rows = prop_journal.list_fills(account_id="breakout_1", limit=10)
    assert rows[0]["direction"] == "long", "direction normalized to canonical long"


def test_distinct_status_is_a_separate_row(isolated_db: Path) -> None:
    """open then closed for the same order are DISTINCT lifecycle events."""
    prop_journal.insert_fill({
        "account_id": "breakout_1", "external_order_id": "500139412",
        "symbol": "ETHUSDT", "direction": "long", "status": "open", "qty": 1.0,
    })
    prop_journal.insert_fill({
        "account_id": "breakout_1", "external_order_id": "500139412",
        "symbol": "ETHUSDT", "direction": "long", "status": "closed",
        "qty": 1.0, "exit_price": 1850.0, "pnl": 50.0,
    })
    assert _count() == 2, "a different status is a genuinely distinct event"


def test_fallback_key_dedupes_without_external_order_id(isolated_db: Path) -> None:
    """No external_order_id → (account, ticket_id, status, qty, exit_price) key."""
    fill = {
        "account_id": "breakout_1", "ticket_id": "tk-1", "symbol": "ETHUSDT",
        "direction": "sell", "status": "closed", "qty": 2.0, "exit_price": 1850.0,
    }
    id1 = prop_journal.insert_fill(dict(fill))
    id2 = prop_journal.insert_fill(dict(fill, pnl=12.0))  # corrective re-report
    assert id1 == id2
    assert _count() == 1
    rows = prop_journal.list_fills(account_id="breakout_1", limit=10)
    assert rows[0]["direction"] == "short", "sell normalized to short"


def test_keyless_fill_still_appends(isolated_db: Path) -> None:
    """No external_order_id AND no ticket_id → cannot dedup → plain append
    (unchanged pre-fix behaviour; never silently drops an un-keyable report)."""
    f = {"account_id": "breakout_1", "symbol": "ETHUSDT", "status": "closed", "qty": 1.0}
    prop_journal.insert_fill(dict(f))
    prop_journal.insert_fill(dict(f))
    assert _count() == 2
