"""A prop report can only touch its OWN account's ticket (TRADEIFY-WIRE F1).

With a second prop account (tradeify_1) beside breakout_1, a report carrying an
explicit ``ticket_id`` that belongs to the other account must neither link to
nor advance that ticket. Same-account behaviour is unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return db


@pytest.fixture
def no_notify(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []
    from src.prop import breakout_notify

    monkeypatch.setattr(breakout_notify, "emit_prop_fill",
                        lambda fill, **kw: calls.append(fill) or {"push": False, "telegram": False})
    return calls


def _ticket(tid: str, account: str) -> None:
    from src.prop import prop_journal

    prop_journal.record_ticket({"ticket_id": tid, "account_id": account,
                                "symbol": "ETHUSDT", "direction": "long", "entry": 2500.0})


def _status(tid: str) -> str:
    from src.prop import prop_journal

    return prop_journal.get_ticket(tid)["status"]


def test_match_rejects_explicit_ticket_of_another_account(isolated_db: Path) -> None:
    from src.prop import prop_reconcile

    _ticket("prop-b1", "breakout_1")
    fill = {"account_id": "tradeify_1", "ticket_id": "prop-b1",
            "symbol": "ETHUSDT", "direction": "long", "status": "filled"}
    assert prop_reconcile.match_fill_to_ticket(fill) is None


def test_match_keeps_explicit_ticket_of_same_account(isolated_db: Path) -> None:
    from src.prop import prop_reconcile

    _ticket("prop-b1", "breakout_1")
    fill = {"account_id": "breakout_1", "ticket_id": "prop-b1", "status": "filled"}
    assert prop_reconcile.match_fill_to_ticket(fill) == "prop-b1"
    # an explicit id with no ticket row behaves exactly as before
    assert prop_reconcile.match_fill_to_ticket(
        {"account_id": "breakout_1", "ticket_id": "prop-unknown"}) == "prop-unknown"


def test_set_ticket_status_scoped_by_account(isolated_db: Path) -> None:
    from src.prop import prop_journal

    _ticket("prop-b1", "breakout_1")
    assert prop_journal.set_ticket_status("prop-b1", "filled", account_id="tradeify_1") == 0
    assert _status("prop-b1") == "emitted"
    assert prop_journal.set_ticket_status("prop-b1", "filled", account_id="breakout_1") == 1
    assert _status("prop-b1") == "filled"
    # unscoped callers (expiry / invalidation prompts) are unchanged
    assert prop_journal.set_ticket_status("prop-b1", "closed") == 1


def test_cross_account_report_does_not_advance_other_ticket(
    isolated_db: Path, no_notify: list
) -> None:
    from src.prop import prop_report

    _ticket("prop-b1", "breakout_1")
    out = prop_report.ingest_report({
        "account_id": "tradeify_1", "ticket_id": "prop-b1",
        "symbol": "ETHUSDT", "direction": "long", "status": "filled",
        "entry_price": 2500.0, "qty": 0.1,
    })
    assert out["ok"] and out["ticket_id"] is None
    assert _status("prop-b1") == "emitted"


def test_same_account_report_still_advances_ticket(
    isolated_db: Path, no_notify: list
) -> None:
    from src.prop import prop_report

    _ticket("prop-b1", "breakout_1")
    out = prop_report.ingest_report({
        "account_id": "breakout_1", "ticket_id": "prop-b1",
        "symbol": "ETHUSDT", "direction": "long", "status": "filled",
        "entry_price": 2500.0, "qty": 0.1,
    })
    assert out["ok"] and out["ticket_id"] == "prop-b1"
    assert _status("prop-b1") == "filled"
