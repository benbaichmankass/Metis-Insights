"""The rule-distance panel must say how old the snapshot behind it is.

WHY (Tier-2, 2026-08-14). Every number `compute_rule_distance` returns is a
function of ONE operator-reported `prop_account_status` row, and the Breakout
manual bridge has no broker feed to refresh it. The only field a consumer had
was `status_present`, which says a row EXISTS — so a three-week-old snapshot
rendered a full-looking cushion to an account-killer, indistinguishable from a
live one. That is the unasserted-denominator shape applied to a safety guard:
the value is real, the label is confident, and the reader concludes wrongly.

The four freshness states are never collapsed, and `ok` is not the default for
"we didn't check".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


def _status(hours_old: float) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    return {
        "account_id": "breakout_1", "balance": 5000.0, "equity": 4950.0,
        "realized_today": -30.0, "unrealized": -20.0,
        "reported_at": ts.isoformat(),
    }


def test_absent_snapshot_is_absent_not_stale(isolated_db: Path) -> None:
    """No row ever reported is its own state — nothing was measured to age."""
    from src.prop.prop_reconcile import compute_rule_distance

    rd = compute_rule_distance("breakout_1", {})
    assert rd["status_freshness"] == "absent"
    assert rd["status_age_hours"] is None
    assert rd["status_present"] is False


def test_fresh_snapshot_is_ok_and_carries_its_age(isolated_db: Path) -> None:
    from src.prop.prop_reconcile import compute_rule_distance

    rd = compute_rule_distance("breakout_1", _status(2.0))
    assert rd["status_freshness"] == "ok"
    assert 1.9 <= rd["status_age_hours"] <= 2.1
    assert rd["status_max_age_hours"] == 24.0


def test_stale_snapshot_keeps_its_distances_but_says_stale(
        isolated_db: Path) -> None:
    """The cushion is still returned — throwing away the last known value helps
    nobody. What changes is that the caveat travels WITH it."""
    from src.prop.prop_reconcile import compute_rule_distance

    rd = compute_rule_distance("breakout_1", _status(72.0))
    assert rd["status_freshness"] == "stale"
    assert rd["status_age_hours"] >= 71.0
    # still computed, not nulled out
    assert rd["distance_to_dd_floor_usd"] is not None


def test_undateable_snapshot_reads_stale_never_ok(isolated_db: Path) -> None:
    """A row that cannot be dated cannot be shown to be current.

    Fail-safe direction on a safety cushion, and it matches
    `prop_balance.prop_sizing_balance`, which refuses to size off an undateable
    snapshot for the same reason. The two must not disagree about one row.
    """
    from src.prop.prop_reconcile import compute_rule_distance

    row = _status(1.0)
    row["reported_at"] = "not-a-timestamp"
    rd = compute_rule_distance("breakout_1", row)
    assert rd["status_freshness"] == "stale"
    # age is None because it is UNDATEABLE, not because it is absent — the
    # freshness field is what tells those apart, never the null.
    assert rd["status_age_hours"] is None
    assert rd["status_present"] is True


def test_disabled_check_is_unchecked_not_ok(
        isolated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`PROP_STATUS_REQUEST_MAX_AGE_HOURS <= 0` means we did not look.

    Reporting that as `ok` would be a fabricated all-clear: the operator turned
    the check off, which is not the same as the snapshot being current.
    """
    from src.prop.prop_reconcile import compute_rule_distance

    monkeypatch.setenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", "0")
    rd = compute_rule_distance("breakout_1", _status(500.0))
    assert rd["status_freshness"] == "unchecked"
    # The age is still reported — disabling the VERDICT does not disable the
    # measurement, so a consumer can still judge for itself.
    assert rd["status_age_hours"] >= 499.0


def test_one_threshold_governs_sizing_and_the_guard(
        isolated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sizing and the rule-distance guard read the SAME staleness threshold.

    Two definitions of "too old to trust" would let the bot size a ticket off a
    balance the safety panel had already written off (or vice versa).
    """
    from src.prop import prop_balance
    from src.prop.prop_reconcile import compute_rule_distance

    monkeypatch.setenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", "6")
    assert prop_balance.max_age_hours() == 6.0
    assert compute_rule_distance("breakout_1", _status(7.0))["status_freshness"] == "stale"
    assert compute_rule_distance("breakout_1", _status(5.0))["status_freshness"] == "ok"


def test_router_lifts_freshness_onto_the_envelope(isolated_db: Path) -> None:
    """`/api/bot/prop/status` carries the verdict at the top level too.

    A consumer reading only `present` + `status` would otherwise still have no
    way to tell a live cushion from a stale one.
    """
    from fastapi.testclient import TestClient

    from src.prop import prop_journal
    from src.web.api.main import app

    prop_journal.insert_account_status({
        "account_id": "breakout_1", "balance": 5000.0, "equity": 4950.0,
    })
    body = TestClient(app).get(
        "/api/bot/prop/status?account_id=breakout_1").json()

    assert body["present"] is True
    assert body["status_freshness"] == "ok"
    assert body["status_age_hours"] is not None
    # and the same verdict is inside the panel, for a consumer that reads it
    assert body["rule_distance"]["status_freshness"] == "ok"


def test_router_reports_a_read_failure_as_error_not_absent(
        isolated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed read is `error` — NOT `absent`, which would claim we looked."""
    from fastapi.testclient import TestClient

    from src.prop import prop_journal
    from src.web.api.main import app

    def _boom(_account_id):
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(prop_journal, "latest_account_status", _boom)
    body = TestClient(app).get(
        "/api/bot/prop/status?account_id=breakout_1").json()

    assert body["status_freshness"] == "error"
    assert body["present"] is False
    assert body["rule_distance"] is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
