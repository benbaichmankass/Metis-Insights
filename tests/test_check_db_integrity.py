"""Tests for scripts/check_db_integrity.py — the Phase-4 INV-1..5 guardrail.

Each invariant is seeded BOTH recently (alert-worthy) and as a LEGACY row
(no alert) plus clean rows, and we assert recent_count / total_count / alert.
We also cover the INV-2 broker-sweep grace exclusion, the INV-3
either-direction package-link resolution, and the --fail-on-alert exit codes.

The DB is built with the canonical fixture (``make_canonical_db`` +
``insert_trade`` / ``insert_order_package``) so a real schema migration —
not a parallel CREATE TABLE — is what these tests run against.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The ``real_schema_db`` pytest fixture is auto-discovered via the
# ``pytest_plugins = ("tests.fixtures.real_schema_db",)`` registration in
# tests/conftest.py — so we import only the insert helpers here (importing
# the fixture too would shadow it and trip F811).
from tests.fixtures.real_schema_db import (
    insert_order_package,
    insert_trade,
)

# Load the script as a module (scripts/ isn't a package).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_db_integrity.py"
_spec = importlib.util.spec_from_file_location("check_db_integrity", _SCRIPT)
cdi = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(cdi)


# A fixed "now" so window math is deterministic.
NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _recent_ts() -> str:
    # 2h ago — well inside the default 48h window AND past the 6h pnl grace?
    # 2h ago is INSIDE the 6h grace; use this for non-INV-2 recent rows.
    return _iso(NOW - timedelta(hours=2))


def _recent_wallclock_ts() -> str:
    # 2h before REAL now — for tests that drive the CLI path (cdi.main),
    # which computes its window against datetime.now() and CANNOT be injected
    # with the fixed NOW. Seeding against the fixed NOW time-bombed: once
    # real-now passed NOW+48h (2026-06-19T10:00Z) the "recent" row aged out of
    # the default 48h window, the alert stopped firing, and the --fail-on-alert
    # exit-code assertion flipped 1→0 (BL-20260619). Anchoring to real now keeps
    # the seeded row genuinely recent on any run date.
    return _iso(datetime.now(timezone.utc) - timedelta(hours=2))


def _recent_past_grace_ts() -> str:
    # 10h ago — recent (< 48h) but past the 6h pnl-sweep grace.
    return _iso(NOW - timedelta(hours=10))


def _legacy_ts() -> str:
    # 30 days ago — outside the 48h window (legacy backlog).
    return _iso(NOW - timedelta(days=30))


def _run(db: Path, **kw):
    return cdi.run_checks(str(db), now=NOW, **kw)


def _check(report, check_id):
    return next(c for c in report["checks"] if c["id"] == check_id)


# ---------------------------------------------------------------------------
# Clean DB
# ---------------------------------------------------------------------------


def test_clean_db_no_alerts(real_schema_db):
    db = real_schema_db()
    # A fully-conforming closed real trade linked both directions.
    insert_order_package(db, order_package_id="op-clean", status="closed",
                         linked_trade_id=1, updated_at=_recent_ts())
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=60000.0,
        position_size=0.001, pnl=12.5, closed_at=_recent_ts(),
        created_at=_recent_ts(), timestamp=_recent_ts(),
        order_package_id="op-clean",
    )
    report = _run(db)
    assert report["any_alert"] is False
    for c in report["checks"]:
        assert c["recent_count"] == 0, c
        assert c["total_count"] == 0, c


# ---------------------------------------------------------------------------
# INV-1 — closed AND closed_at IS NULL
# ---------------------------------------------------------------------------


def test_inv1_recent_alerts_legacy_does_not(real_schema_db):
    db = real_schema_db()
    # RECENT regression: closed, no closed_at; window basis = created_at (2h).
    rid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id="op-1r",
        closed_at=None, created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-1r", status="closed",
                         linked_trade_id=rid, updated_at=_recent_ts())
    # LEGACY: closed, no closed_at, 30 days old.
    lid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="ETHUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id="op-1l",
        closed_at=None, created_at=_legacy_ts(), timestamp=_legacy_ts(),
    )
    insert_order_package(db, order_package_id="op-1l", status="closed",
                         linked_trade_id=lid, updated_at=_legacy_ts())

    inv1 = _check(_run(db), "INV-1")
    assert inv1["recent_count"] == 1
    assert inv1["total_count"] == 2
    assert inv1["alert"] is True
    assert rid in inv1["sample_ids"]


def test_inv1_legacy_only_no_alert(real_schema_db):
    db = real_schema_db()
    lid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="ETHUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id="op-1l",
        closed_at=None, created_at=_legacy_ts(), timestamp=_legacy_ts(),
    )
    insert_order_package(db, order_package_id="op-1l", status="closed",
                         linked_trade_id=lid, updated_at=_legacy_ts())
    inv1 = _check(_run(db), "INV-1")
    assert inv1["recent_count"] == 0
    assert inv1["total_count"] == 1
    assert inv1["alert"] is False


def test_inv1_backtest_rows_ignored(real_schema_db):
    db = real_schema_db()
    insert_trade(
        db, is_backtest=1, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        closed_at=None, created_at=_recent_ts(), timestamp=_recent_ts(),
        order_package_id="op-bt",
    )
    inv1 = _check(_run(db), "INV-1")
    assert inv1["recent_count"] == 0
    assert inv1["total_count"] == 0


# ---------------------------------------------------------------------------
# INV-2 — closed AND pnl IS NULL, with the broker-sweep grace exclusion
# ---------------------------------------------------------------------------


def test_inv2_grace_window_excluded(real_schema_db):
    db = real_schema_db()
    # Just closed 2h ago, pnl NULL — INSIDE the 6h grace → NOT flagged.
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=None, order_package_id="op-2g",
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-2g", status="closed",
                         linked_trade_id=1, updated_at=_recent_ts())
    inv2 = _check(_run(db), "INV-2")
    assert inv2["recent_count"] == 0
    assert inv2["total_count"] == 0
    assert inv2["alert"] is False


def test_inv2_past_grace_recent_alerts(real_schema_db):
    db = real_schema_db()
    # Closed 10h ago, pnl still NULL — past the 6h grace, inside 48h window.
    rid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=None, order_package_id="op-2r",
        closed_at=_recent_past_grace_ts(), created_at=_recent_past_grace_ts(),
        timestamp=_recent_past_grace_ts(),
    )
    insert_order_package(db, order_package_id="op-2r", status="closed",
                         linked_trade_id=rid, updated_at=_recent_past_grace_ts())
    # LEGACY past-grace null-pnl row.
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="ETHUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=None, order_package_id="op-2l",
        closed_at=_legacy_ts(), created_at=_legacy_ts(), timestamp=_legacy_ts(),
    )
    insert_order_package(db, order_package_id="op-2l", status="closed",
                         linked_trade_id=2, updated_at=_legacy_ts())

    inv2 = _check(_run(db), "INV-2")
    assert inv2["recent_count"] == 1
    assert inv2["total_count"] == 2
    assert inv2["alert"] is True
    assert rid in inv2["sample_ids"]


def test_inv2_custom_grace(real_schema_db):
    db = real_schema_db()
    # Closed 4h ago, pnl NULL.
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=None, order_package_id="op-2c",
        closed_at=_iso(NOW - timedelta(hours=4)),
        created_at=_iso(NOW - timedelta(hours=4)),
        timestamp=_iso(NOW - timedelta(hours=4)),
    )
    insert_order_package(db, order_package_id="op-2c", status="closed",
                         linked_trade_id=1, updated_at=_iso(NOW - timedelta(hours=4)))
    # Default 6h grace → 4h-old row is inside grace, not flagged.
    assert _check(_run(db), "INV-2")["recent_count"] == 0
    # 2h grace → the 4h-old row is now past grace, flagged.
    assert _check(_run(db, pnl_grace_hours=2.0), "INV-2")["recent_count"] == 1


# ---------------------------------------------------------------------------
# INV-3 — no resolvable order-package link by EITHER direction
# ---------------------------------------------------------------------------


def test_inv3_unlinked_recent_alerts(real_schema_db):
    db = real_schema_db()
    # RECENT: open trade, order_package_id NULL, no package references it.
    rid = insert_trade(
        db, is_backtest=0, status="open", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        order_package_id=None,
        created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    # LEGACY unlinked.
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="ETHUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id=None,
        closed_at=_legacy_ts(), created_at=_legacy_ts(), timestamp=_legacy_ts(),
    )
    inv3 = _check(_run(db), "INV-3")
    assert inv3["recent_count"] == 1
    assert inv3["total_count"] == 2
    assert inv3["alert"] is True
    assert rid in inv3["sample_ids"]


def test_inv3_forward_link_resolves(real_schema_db):
    db = real_schema_db()
    # order_package_id set (forward link) → resolved, NOT flagged, even with
    # no reverse linked_trade_id (the documented many-to-one design).
    insert_order_package(db, order_package_id="op-fwd", status="closed",
                         linked_trade_id=None, updated_at=_recent_ts())
    insert_trade(
        db, is_backtest=0, status="open", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        order_package_id="op-fwd",
        created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    inv3 = _check(_run(db), "INV-3")
    assert inv3["recent_count"] == 0
    assert inv3["total_count"] == 0


def test_inv3_reverse_link_resolves(real_schema_db):
    db = real_schema_db()
    # order_package_id NULL (forward absent) BUT a package's linked_trade_id
    # points at it (reverse link present) → resolved, NOT flagged.
    rid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id=None,
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-rev", status="closed",
                         linked_trade_id=rid, updated_at=_recent_ts())
    inv3 = _check(_run(db), "INV-3")
    assert inv3["recent_count"] == 0
    assert inv3["total_count"] == 0


# ---------------------------------------------------------------------------
# INV-4 — account_class IS NULL
# ---------------------------------------------------------------------------


def test_inv4_recent_alerts_legacy_does_not(real_schema_db):
    db = real_schema_db()
    rid = insert_trade(
        db, is_backtest=0, status="open", account_class=None,
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        order_package_id="op-4r",
        created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-4r", status="open",
                         linked_trade_id=rid, updated_at=_recent_ts())
    # LEGACY null account_class.
    insert_trade(
        db, is_backtest=0, status="closed", account_class=None,
        symbol="ETHUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id="op-4l",
        closed_at=_legacy_ts(), created_at=_legacy_ts(), timestamp=_legacy_ts(),
    )
    insert_order_package(db, order_package_id="op-4l", status="closed",
                         linked_trade_id=2, updated_at=_legacy_ts())

    inv4 = _check(_run(db), "INV-4")
    assert inv4["recent_count"] == 1
    assert inv4["total_count"] == 2
    assert inv4["alert"] is True
    assert rid in inv4["sample_ids"]


# ---------------------------------------------------------------------------
# INV-5 — terminal package whose linked_trade_id disagrees with the back-ref
# ---------------------------------------------------------------------------


def test_inv5_concrete_mismatch_recent_alerts(real_schema_db):
    db = real_schema_db()
    # Trade points back at a DIFFERENT package than the one linking it.
    rid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id="op-OTHER",
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    # Terminal package links rid, but rid.order_package_id == "op-OTHER".
    insert_order_package(db, order_package_id="op-5r", status="closed",
                         linked_trade_id=rid, updated_at=_recent_ts())
    # LEGACY mismatch.
    lid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="ETHUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id="op-OTHER2",
        closed_at=_legacy_ts(), created_at=_legacy_ts(), timestamp=_legacy_ts(),
    )
    insert_order_package(db, order_package_id="op-5l", status="closed",
                         linked_trade_id=lid, updated_at=_legacy_ts())

    inv5 = _check(_run(db), "INV-5")
    assert inv5["recent_count"] == 1
    assert inv5["total_count"] == 2
    assert inv5["alert"] is True
    assert "op-5r" in inv5["sample_ids"]


def test_inv5_null_backref_not_flagged(real_schema_db):
    db = real_schema_db()
    # The documented design: package links a trade whose order_package_id is
    # NULL (the many-to-one convenience) — NOT a mismatch.
    rid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id=None,
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-ok", status="closed",
                         linked_trade_id=rid, updated_at=_recent_ts())
    inv5 = _check(_run(db), "INV-5")
    assert inv5["recent_count"] == 0
    assert inv5["total_count"] == 0


def test_inv5_agreeing_backref_not_flagged(real_schema_db):
    db = real_schema_db()
    # Package links a trade that points back at the SAME package — agree.
    rid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id="op-agree",
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-agree", status="closed",
                         linked_trade_id=rid, updated_at=_recent_ts())
    inv5 = _check(_run(db), "INV-5")
    assert inv5["recent_count"] == 0
    assert inv5["total_count"] == 0


def test_inv5_open_package_not_terminal_not_flagged(real_schema_db):
    db = real_schema_db()
    # Same cross-link, but the package is still OPEN (non-terminal) → skip.
    rid = insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, order_package_id="op-OTHER",
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-open", status="open",
                         linked_trade_id=rid, updated_at=_recent_ts())
    inv5 = _check(_run(db), "INV-5")
    assert inv5["recent_count"] == 0
    assert inv5["total_count"] == 0


# ---------------------------------------------------------------------------
# INV-6 — malformed-JSON notes (BL-20260618-CLOSEDFLAT-MALFORMED-JSON)
# ---------------------------------------------------------------------------


def test_inv6_malformed_notes_recent_alerts(real_schema_db):
    db = real_schema_db()
    # Recent trade whose notes is INVALID JSON (char-slice truncation) → alert.
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, notes='{"closed_at": "2026-07-09T00:00:00Z", "reason": "trunc',
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    # Legacy malformed row (informational — total, not recent).
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="ETHUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, notes='{"closed_at": "old", "reason": "trunc',
        closed_at=_legacy_ts(), created_at=_legacy_ts(), timestamp=_legacy_ts(),
    )
    inv6 = _check(_run(db), "INV-6")
    assert inv6["recent_count"] == 1
    assert inv6["total_count"] == 2
    assert inv6["alert"] is True


def test_inv6_valid_and_null_notes_not_flagged(real_schema_db):
    db = real_schema_db()
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, notes='{"closed_at": "2026-07-09T00:00:00Z", "ok": true}',
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_trade(
        db, is_backtest=0, status="closed", account_class="real_money",
        symbol="ETHUSDT", direction="long", entry_price=1.0, position_size=1.0,
        pnl=1.0, notes=None,
        closed_at=_recent_ts(), created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    inv6 = _check(_run(db), "INV-6")
    assert inv6["recent_count"] == 0
    assert inv6["total_count"] == 0
    assert inv6["alert"] is False


# ---------------------------------------------------------------------------
# any_alert + --fail-on-alert exit codes
# ---------------------------------------------------------------------------


def test_any_alert_aggregates(real_schema_db):
    db = real_schema_db()
    insert_trade(
        db, is_backtest=0, status="open", account_class=None,
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        order_package_id="op-x",
        created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-x", status="open",
                         linked_trade_id=1, updated_at=_recent_ts())
    assert _run(db)["any_alert"] is True


def test_fail_on_alert_exit_codes(real_schema_db):
    db = real_schema_db()
    # Seed a recent INV-4 regression. cdi.main() uses REAL now (no inject),
    # so anchor the seed to wall-clock now — not the fixed NOW — or the row
    # ages out of the 48h window and the alert silently stops firing.
    insert_trade(
        db, is_backtest=0, status="open", account_class=None,
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        order_package_id="op-x",
        created_at=_recent_wallclock_ts(), timestamp=_recent_wallclock_ts(),
    )
    insert_order_package(db, order_package_id="op-x", status="open",
                         linked_trade_id=1, updated_at=_recent_wallclock_ts())

    # Without --fail-on-alert: exit 0 even with an alert.
    rc = cdi.main(["--db", str(db)])
    assert rc == 0
    # With --fail-on-alert: exit 1.
    rc = cdi.main(["--db", str(db), "--fail-on-alert"])
    assert rc == 1


def test_fail_on_alert_clean_exits_zero(real_schema_db):
    db = real_schema_db()
    rc = cdi.main(["--db", str(db), "--fail-on-alert"])
    assert rc == 0


def test_db_error_exit_two(tmp_path):
    # mode=ro refuses to create a missing DB → sqlite error → exit 2.
    missing = tmp_path / "nope.db"
    rc = cdi.main(["--db", str(missing)])
    assert rc == 2


# ---------------------------------------------------------------------------
# message rendering
# ---------------------------------------------------------------------------


def test_build_alert_message_only_lists_alerts(real_schema_db):
    db = real_schema_db()
    insert_trade(
        db, is_backtest=0, status="open", account_class=None,
        symbol="BTCUSDT", direction="long", entry_price=1.0, position_size=1.0,
        order_package_id="op-x",
        created_at=_recent_ts(), timestamp=_recent_ts(),
    )
    insert_order_package(db, order_package_id="op-x", status="open",
                         linked_trade_id=1, updated_at=_recent_ts())
    report = _run(db)
    msg = cdi.build_alert_message(report)
    assert msg.startswith("[WARN] DB integrity:")
    assert "INV-4" in msg
    # Non-alerting checks not enumerated.
    assert "INV-1:" not in msg
