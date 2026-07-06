"""Tests for src/runtime/health.py — S-022 PR3."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime import health as h  # noqa: E402
from src.runtime.health import (  # noqa: E402
    HealthCheck,
    check_accounts_api,
    check_db,
    check_disk,
    check_git_drift,
    check_last_fetch,
    check_service,
    check_tick_freshness,
    overall_status,
    run_all_checks,
)


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["mock"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


@pytest.fixture(autouse=True)
def _restore_data_loaders():
    saved = sys.modules.get("src.bot.data_loaders")
    yield
    if saved is None:
        sys.modules.pop("src.bot.data_loaders", None)
    else:
        sys.modules["src.bot.data_loaders"] = saved


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def test_service_active_returns_ok():
    with patch.object(h, "_run", return_value=_proc(stdout="active\n")):
        c = check_service("ict-trader-live.service")
    assert c.status == "ok"
    assert "active" in c.detail


def test_service_inactive_returns_critical():
    with patch.object(h, "_run", return_value=_proc(stdout="inactive\n", returncode=3)):
        c = check_service()
    assert c.status == "critical"
    assert "inactive" in c.detail


def test_service_no_systemctl_returns_warn_not_critical():
    with patch.object(h, "_run", side_effect=FileNotFoundError("systemctl not found")):
        c = check_service()
    assert c.status == "warn"
    assert "systemctl" in c.detail


def test_service_timeout_is_critical():
    with patch.object(h, "_run", side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=5)):
        c = check_service()
    assert c.status == "critical"


# ---------------------------------------------------------------------------
# Git drift
# ---------------------------------------------------------------------------


def test_git_drift_in_sync_is_ok():
    sha = "a" * 40
    procs = {
        ("rev-parse", "HEAD"): _proc(stdout=sha + "\n"),
        ("rev-parse", "origin/main"): _proc(stdout=sha + "\n"),
    }

    def fake_run(cmd, **_kw):
        for key, p in procs.items():
            if all(k in cmd for k in key):
                return p
        return _proc(returncode=1)

    with patch.object(h, "_run", side_effect=fake_run):
        c = check_git_drift()
    assert c.status == "ok"
    assert "in sync" in c.detail


def test_git_drift_few_commits_recent_is_warn():
    head = "a" * 40
    upstream = "b" * 40
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    def fake_run(cmd, **_kw):
        if "rev-parse" in cmd and "HEAD" in cmd:
            return _proc(stdout=head + "\n")
        if "rev-parse" in cmd and "origin/main" in cmd:
            return _proc(stdout=upstream + "\n")
        if "rev-list" in cmd:
            return _proc(stdout="3\n")
        if "log" in cmd:
            return _proc(stdout=fresh + "\n")
        return _proc()

    with patch.object(h, "_run", side_effect=fake_run):
        c = check_git_drift()
    assert c.status == "warn"
    assert "3 commits behind" in c.detail
    assert c.ctx["behind"] == 3


def test_git_drift_aged_commits_is_critical():
    head = "a" * 40
    upstream = "b" * 40
    aged = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()

    def fake_run(cmd, **_kw):
        if "rev-parse" in cmd and "HEAD" in cmd:
            return _proc(stdout=head + "\n")
        if "rev-parse" in cmd and "origin/main" in cmd:
            return _proc(stdout=upstream + "\n")
        if "rev-list" in cmd:
            return _proc(stdout="5\n")
        if "log" in cmd:
            return _proc(stdout=aged + "\n")
        return _proc()

    with patch.object(h, "_run", side_effect=fake_run):
        c = check_git_drift()
    assert c.status == "critical"
    assert "h old" in c.detail
    assert c.ctx["age_hours"] is not None
    assert c.ctx["age_hours"] >= 24


def test_git_drift_no_git_binary_is_warn():
    with patch.object(h, "_run", side_effect=FileNotFoundError):
        c = check_git_drift()
    assert c.status == "warn"


def test_git_drift_unresolvable_branch_is_warn():
    def fake_run(cmd, **_kw):
        if "rev-parse" in cmd and "HEAD" in cmd:
            return _proc(stdout="a" * 40 + "\n")
        return _proc(returncode=128, stderr="unknown ref\n")

    with patch.object(h, "_run", side_effect=fake_run):
        c = check_git_drift()
    assert c.status == "warn"


# ---------------------------------------------------------------------------
# Last fetch
# ---------------------------------------------------------------------------


def test_last_fetch_recent_is_ok(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    fetch_head = git_dir / "FETCH_HEAD"
    fetch_head.write_text("")
    c = check_last_fetch(repo_dir=tmp_path, stale_minutes=15.0)
    assert c.status == "ok"


def test_last_fetch_stale_is_warn(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    fetch_head = git_dir / "FETCH_HEAD"
    fetch_head.write_text("")
    # backdate it 30 min
    old = time.time() - 30 * 60
    import os
    os.utime(str(fetch_head), (old, old))
    c = check_last_fetch(repo_dir=tmp_path, stale_minutes=15.0)
    assert c.status == "warn"
    assert ">" in c.detail


def test_last_fetch_missing_is_warn(tmp_path):
    (tmp_path / ".git").mkdir()  # no FETCH_HEAD
    c = check_last_fetch(repo_dir=tmp_path)
    assert c.status == "warn"


# ---------------------------------------------------------------------------
# Tick freshness
# ---------------------------------------------------------------------------


def test_tick_recent_is_ok(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text("")
    c = check_tick_freshness(audit_path=p, tick_interval_s=900)
    assert c.status == "ok"


def test_tick_stale_is_critical(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text("")
    import os
    old = time.time() - 3600
    os.utime(str(p), (old, old))
    c = check_tick_freshness(audit_path=p, tick_interval_s=900)
    assert c.status == "critical"


def test_tick_missing_is_critical(tmp_path):
    p = tmp_path / "missing.jsonl"
    c = check_tick_freshness(audit_path=p)
    assert c.status == "critical"


# ---------------------------------------------------------------------------
# Accounts API
# ---------------------------------------------------------------------------


def test_accounts_api_all_ok():
    fake = MagicMock()
    fake.list_accounts = lambda: [{"account_id": "main"}, {"account_id": "alt"}]
    fake.account_balance = lambda _: {"total_usdt": 100.0}
    sys.modules["src.bot.data_loaders"] = fake
    c = check_accounts_api()
    assert c.status == "ok"
    assert "2 broker-API accounts" in c.detail


def test_accounts_api_partial_failure_is_warn():
    fake = MagicMock()
    fake.list_accounts = lambda: [
        {"account_id": "main"}, {"account_id": "alt"}, {"account_id": "third"},
    ]
    fake.account_balance = lambda acc: (
        {"total_usdt": 100.0} if acc["account_id"] == "main" else None
    )
    sys.modules["src.bot.data_loaders"] = fake
    c = check_accounts_api()
    assert c.status == "warn"
    assert "alt" in c.detail and "third" in c.detail


def test_accounts_api_no_accounts_is_ok():
    fake = MagicMock()
    fake.list_accounts = lambda: []
    fake.account_balance = lambda _: None
    sys.modules["src.bot.data_loaders"] = fake
    c = check_accounts_api()
    assert c.status == "ok"


def test_accounts_api_skips_manual_bridge_breakout():
    # BL-20260623-003: the breakout prop account has an explicitly-empty
    # EXCHANGE_MANAGEMENT_CAPS set (no broker balance API — it executes via
    # Telegram/FCM tickets). It must be SKIPPED, not counted as "API down",
    # so a real broker outage isn't inflated (the false 3/7 -> real 2/7).
    fake = MagicMock()
    fake.list_accounts = lambda: [
        {"account_id": "bybit_2", "exchange": "bybit"},
        {"account_id": "breakout_1", "exchange": "breakout"},
    ]
    fake.account_balance = lambda acc: (
        {"total_usdt": 100.0} if acc.get("exchange") == "bybit" else None
    )
    sys.modules["src.bot.data_loaders"] = fake
    c = check_accounts_api()
    # breakout returns None but is skipped → still OK over the 1 real account.
    assert c.status == "ok"
    assert c.ctx.get("skipped") == ["breakout_1"]
    assert c.ctx.get("total") == 1
    assert "1 manual-bridge skipped" in c.detail


def test_accounts_api_skips_shelved_dry_account():
    # BL-20260705-HEALTHCHECK-SHELVED-ACCOUNTS: a dry/shelved account
    # (mode != live, e.g. the 2FA-blocked ib_live) reads unreachable BY
    # DESIGN. It must be SKIPPED into the 'shelved' bucket, not counted as
    # "API down" — otherwise the roll-up is perma-WARN and everyone learns
    # to ignore WARN. The one genuinely-live account still grades OK.
    fake = MagicMock()
    fake.list_accounts = lambda: [
        {"account_id": "bybit_2", "exchange": "bybit", "mode": "live"},
        {"account_id": "ib_live", "exchange": "interactive_brokers", "mode": "dry_run"},
    ]
    fake.account_balance = lambda acc: (
        {"total_usdt": 100.0} if acc.get("account_id") == "bybit_2" else None
    )
    sys.modules["src.bot.data_loaders"] = fake
    c = check_accounts_api()
    assert c.status == "ok"
    assert c.ctx.get("shelved") == ["ib_live"]
    assert c.ctx.get("total") == 1
    assert "1 dry/shelved skipped" in c.detail


def test_accounts_api_absent_mode_defaults_live_and_is_probed():
    # An account that omits `mode` is treated as live (default-permissive) and
    # still probed — so a genuinely-down account without an explicit mode is
    # NOT silently hidden in the shelved bucket.
    fake = MagicMock()
    fake.list_accounts = lambda: [{"account_id": "bybit_2", "exchange": "bybit"}]
    fake.account_balance = lambda _: None
    sys.modules["src.bot.data_loaders"] = fake
    c = check_accounts_api()
    assert c.status == "warn"
    assert "bybit_2" in c.detail


def test_accounts_api_loader_explosion_is_warn():
    fake = MagicMock()
    fake.list_accounts.side_effect = RuntimeError("loaders broken")
    fake.account_balance = lambda _: None
    sys.modules["src.bot.data_loaders"] = fake
    c = check_accounts_api()
    assert c.status == "warn"


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def test_db_writable_is_ok(tmp_path):
    db = tmp_path / "tj.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()
    c = check_db(db_path=db)
    assert c.status == "ok"


def test_db_missing_returns_warn(tmp_path, monkeypatch):
    # check_db resolves the canonical DB via trade_journal_db_path()
    # (S-PERSIST-CANON). Point both the env-resolved path and the explicit
    # db_path at non-existent files so the probe finds no DB → warn. Using
    # the env makes this robust against any other test that created a
    # trade_journal.db at the real repo root.
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "nope.db"))
    c = check_db(db_path=tmp_path / "nope.db")
    assert c.status == "warn"


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------


def test_disk_high_free_is_ok():
    fake = type("U", (), {"total": 100, "free": 80, "used": 20})
    with patch("src.runtime.health.shutil.disk_usage", return_value=fake):
        c = check_disk(warn_pct=10.0)
    assert c.status == "ok"


def test_disk_low_free_is_warn():
    fake = type("U", (), {"total": 100, "free": 5, "used": 95})
    with patch("src.runtime.health.shutil.disk_usage", return_value=fake):
        c = check_disk(warn_pct=10.0)
    assert c.status == "warn"


def test_disk_oserror_is_warn():
    with patch("src.runtime.health.shutil.disk_usage", side_effect=OSError("nope")):
        c = check_disk()
    assert c.status == "warn"


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------


def test_run_all_checks_swallows_per_check_exception():
    def explodes() -> HealthCheck:
        raise RuntimeError("oh no")

    def fine() -> HealthCheck:
        return HealthCheck("fine", "ok", "fine")

    out = run_all_checks(checks=[fine, explodes])
    assert len(out) == 2
    assert out[0].status == "ok"
    assert out[1].status == "warn"
    assert "raised" in out[1].detail


def test_overall_status_picks_worst():
    items = [
        HealthCheck("a", "ok", "ok"),
        HealthCheck("b", "warn", "wee"),
        HealthCheck("c", "critical", "down"),
    ]
    assert overall_status(items) == "critical"
    assert overall_status(items[:2]) == "warn"
    assert overall_status(items[:1]) == "ok"
    assert overall_status([]) == "ok"
