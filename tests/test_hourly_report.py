"""Tests for src/runtime/hourly_report.py — S-022 PR2."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

for _mod in ("dotenv",):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime import hourly_report as hr  # noqa: E402
from src.runtime.hourly_report import (  # noqa: E402
    _load_audit_lines_since,
    account_snapshots,
    build_hourly_report,
    health_summary,
    outcomes_in_window,
    render_report,
    summarize_ticks,
    trades_in_window,
)
from src.utils.signal_audit_logger import should_send_summary  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_data_loaders():
    """Snapshot/restore sys.modules['src.bot.data_loaders'] across tests so
    injected MagicMocks don't leak into the broader test session.
    """
    saved = sys.modules.get("src.bot.data_loaders")
    yield
    if saved is None:
        sys.modules.pop("src.bot.data_loaders", None)
    else:
        sys.modules["src.bot.data_loaders"] = saved


# ---------------------------------------------------------------------------
# Cadence: should_send_summary fires once per hour
# ---------------------------------------------------------------------------


def test_should_send_summary_fires_each_hour(tmp_path, monkeypatch):
    marker = tmp_path / "summary_markers.json"
    monkeypatch.setattr("src.utils.signal_audit_logger.SUMMARY_FILE", marker)
    base = datetime(2026, 5, 1, 14, 5, tzinfo=timezone.utc)
    assert should_send_summary(base) is True
    # Same hour, second call → False
    assert should_send_summary(base + timedelta(minutes=10)) is False
    # Next hour → True
    assert should_send_summary(base + timedelta(hours=1)) is True
    # Skipping ahead 5 hours → True
    assert should_send_summary(base + timedelta(hours=6)) is True


def test_should_send_summary_returns_true_at_offhours(tmp_path, monkeypatch):
    """Old behavior was hour in {7, 19} only; new must fire at e.g. 14, 03, 11."""
    marker = tmp_path / "markers.json"
    monkeypatch.setattr("src.utils.signal_audit_logger.SUMMARY_FILE", marker)
    for h in (3, 11, 14, 22):
        marker.unlink(missing_ok=True)
        ts = datetime(2026, 5, 1, h, 0, tzinfo=timezone.utc)
        assert should_send_summary(ts) is True


# ---------------------------------------------------------------------------
# Audit-line filtering
# ---------------------------------------------------------------------------


def _write_audit(path: Path, events) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def test_audit_filters_by_window(tmp_path):
    f = tmp_path / "signal_audit.jsonl"
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    inside = (now - timedelta(minutes=30)).isoformat()
    outside = (now - timedelta(hours=2)).isoformat()
    _write_audit(f, [
        {"event": "pipeline_result", "logged_at_utc": inside, "status": "submitted"},
        {"event": "pipeline_result", "logged_at_utc": outside, "status": "submitted"},
        {"event": "pipeline_result", "logged_at_utc": "garbage"},  # skip
        {"event": "pipeline_result"},  # no ts → skip
    ])
    records = _load_audit_lines_since(now - timedelta(hours=1), path=f)
    assert len(records) == 1
    assert records[0]["status"] == "submitted"


# ---------------------------------------------------------------------------
# Tick + signal summarizer
# ---------------------------------------------------------------------------


def test_summarize_ticks_buckets_statuses():
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    records = [
        {"event": "pipeline_result", "logged_at_utc": now.isoformat(),
         "status": "submitted", "side": "buy", "strategy": "vwap"},
        {"event": "pipeline_result", "logged_at_utc": now.isoformat(),
         "status": "dry_run", "side": "buy", "strategy": "vwap"},
        {"event": "pipeline_result", "logged_at_utc": now.isoformat(),
         "status": "skipped", "side": "none"},
        {"event": "pipeline_result", "logged_at_utc": now.isoformat(),
         "status": "failed_exchange", "side": "buy", "strategy": "ict"},
        {"event": "pipeline_result", "logged_at_utc": now.isoformat(),
         "status": "halted", "side": "buy", "strategy": "vwap"},
    ]
    s = summarize_ticks(records)
    assert s["ticks_ok"] == 4  # submitted + dry_run + skipped + halted
    assert s["ticks_err"] == 1  # failed_exchange
    # signals = actionable buy/sell that weren't skipped/halted
    # → submitted/dry_run/failed_exchange = 3
    assert s["signals_total"] == 3
    assert s["signals_by_strategy"] == {"vwap": 2, "ict": 1}


# ---------------------------------------------------------------------------
# Trade-journal queries
# ---------------------------------------------------------------------------


def _make_trade_journal(path: Path, rows):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                created_at TEXT,
                symbol TEXT,
                direction TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl REAL,
                position_size REAL,
                strategy_name TEXT,
                status TEXT,
                is_backtest INTEGER DEFAULT 0
            )
        """)
        conn.executemany(
            "INSERT INTO trades (timestamp, created_at, symbol, direction, entry_price,"
            " exit_price, pnl, position_size, strategy_name, status, is_backtest)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_trades_in_window_buckets_open_and_closed(tmp_path, monkeypatch):
    db = tmp_path / "tj.db"
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    inside = (now - timedelta(minutes=30)).isoformat()
    outside = (now - timedelta(hours=2)).isoformat()
    _make_trade_journal(db, [
        # placed within window, still open
        (inside, inside, "BTCUSDT", "long", 50_000, None, None, 0.001, "vwap", "open", 0),
        # closed within window, +PnL
        (inside, inside, "BTCUSDT", "long", 50_000, 50_500, 5.0, 0.001, "vwap", "closed", 0),
        # closed within window, -PnL
        (inside, inside, "BTCUSDT", "short", 50_000, 50_500, -2.0, 0.001, "ict", "closed", 0),
        # outside window — excluded
        (outside, outside, "BTCUSDT", "long", 49_000, 49_500, 1.0, 0.001, "vwap", "closed", 0),
        # backtest — excluded
        (inside, inside, "BTCUSDT", "long", 49_000, 49_500, 100.0, 0.001, "vwap", "closed", 1),
    ])
    monkeypatch.setattr(hr, "_trade_journal_path", lambda: db)
    out = trades_in_window(now - timedelta(hours=1))
    assert len(out["placed"]) == 3  # 3 live rows in window
    assert len(out["closed"]) == 2  # 2 closed live rows in window
    assert abs(out["realized_pnl"] - 3.0) < 1e-9


def test_trades_in_window_missing_db_returns_empty(monkeypatch):
    monkeypatch.setattr(hr, "_trade_journal_path", lambda: None)
    out = trades_in_window(datetime.now(timezone.utc))
    assert out == {"placed": [], "closed": [], "realized_pnl": 0.0}


# ---------------------------------------------------------------------------
# Account snapshots — balance + 1h delta
# ---------------------------------------------------------------------------


def test_account_snapshots_uses_previous_for_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "RUNTIME_LOGS", tmp_path)
    monkeypatch.setattr(hr, "BALANCE_SNAPSHOT_FILE", tmp_path / "snap.json")

    fake_loaders = MagicMock()
    fake_loaders.list_accounts = lambda: [
        {"account_id": "main", "exchange": "bybit"},
        {"account_id": "alt", "exchange": "bybit"},
    ]
    fake_loaders.account_balance = lambda acc: (
        {"total_usdt": 1000.0} if acc["account_id"] == "main" else None
    )
    fake_loaders.account_open_positions = lambda acc: (
        [{"symbol": "BTCUSDT"}] if acc["account_id"] == "main" else None
    )
    sys.modules["src.bot.data_loaders"] = fake_loaders

    # First call: no previous snapshot → delta None
    out1 = account_snapshots()
    by_id = {a["account_id"]: a for a in out1}
    assert by_id["main"]["balance"] == 1000.0
    assert by_id["main"]["delta_1h"] is None
    assert by_id["main"]["api_ok"] is True
    assert by_id["main"]["open_positions"] == 1
    assert by_id["alt"]["api_ok"] is False
    assert by_id["alt"]["balance"] is None

    # Bump main's balance and check delta
    fake_loaders.account_balance = lambda acc: (
        {"total_usdt": 1010.0} if acc["account_id"] == "main" else None
    )
    out2 = account_snapshots()
    by_id = {a["account_id"]: a for a in out2}
    assert abs(by_id["main"]["delta_1h"] - 10.0) < 1e-9


def test_account_snapshots_safe_when_data_loaders_unavailable(monkeypatch):
    """``list_accounts`` raising RuntimeError → None sentinel.

    S-067 follow-up D2: a ``list_accounts()`` call failure now returns
    ``None`` (data unavailable) rather than ``[]`` (no accounts
    configured). The renderer surfaces the distinction.
    """
    sys.modules.pop("src.bot.data_loaders", None)
    bad = MagicMock()
    bad.list_accounts.side_effect = RuntimeError("loader broken")
    bad.account_balance = lambda *_: None
    bad.account_open_positions = lambda *_: None
    sys.modules["src.bot.data_loaders"] = bad
    out = account_snapshots()
    assert out is None


def test_account_snapshots_returns_none_on_oserror(monkeypatch):
    """OSError from list_accounts → None sentinel (D2 narrowing covers OSError)."""
    sys.modules.pop("src.bot.data_loaders", None)
    bad = MagicMock()
    bad.list_accounts.side_effect = OSError("filesystem hiccup")
    bad.account_balance = lambda *_: None
    bad.account_open_positions = lambda *_: None
    sys.modules["src.bot.data_loaders"] = bad
    assert account_snapshots() is None


def test_render_account_section_data_unavailable():
    """When ``accounts is None``, renderer surfaces 'data unavailable'."""
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    report = {
        "now_utc": now,
        "ticks": {"ticks_ok": 0, "ticks_err": 0, "signals_total": 0,
                  "signals_by_strategy": {}, "last_tick_ts": None},
        "trades": {"placed": [], "closed": [], "realized_pnl": 0.0},
        "accounts": None,  # D2 sentinel
        "strategies": [],
        "outcomes": {"top_errors": []},
        "health": {"tick_age_s": 60, "tick_stale": False, "tick_interval_s": 900,
                   "warn_count": 0, "error_count": 0, "critical_count": 0,
                   "overall": "ok", "checks": []},
    }
    from src.runtime.hourly_report import render_accounts_report
    txt = render_accounts_report(report)
    assert "Accounts — data unavailable" in txt
    assert "list_accounts() raised" in txt
    # Trades section still renders.
    assert "Trades" in txt
    # The "no accounts configured" wording must NOT appear — it would
    # collapse the unavailable sentinel back into the empty-list path.
    assert "no accounts configured" not in txt


def test_assemble_hourly_data_with_failing_list_accounts(tmp_path, monkeypatch):
    """End-to-end: list_accounts raising → assemble_hourly_data carries None
    in the wire shape, build_accounts_hourly_report renders without crashing."""
    monkeypatch.setattr(hr, "SIGNAL_AUDIT_FILE", tmp_path / "missing.jsonl")
    monkeypatch.setattr(hr, "OUTCOMES_FILE", tmp_path / "missing-outcomes.jsonl")
    monkeypatch.setattr(hr, "RUNTIME_LOGS", tmp_path)
    monkeypatch.setattr(hr, "BALANCE_SNAPSHOT_FILE", tmp_path / "snap.json")
    monkeypatch.setattr(hr, "_trade_journal_path", lambda: None)

    sys.modules.pop("src.bot.data_loaders", None)
    bad = MagicMock()
    bad.list_accounts.side_effect = RuntimeError("loader broken")
    bad.account_balance = lambda *_: None
    bad.account_open_positions = lambda *_: None
    bad.strategy_dashboard_data = lambda: []
    sys.modules["src.bot.data_loaders"] = bad

    from src.runtime.hourly_report import (
        assemble_hourly_data,
        build_accounts_hourly_report,
    )
    data = assemble_hourly_data(now_utc=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc))
    assert data["accounts"] is None, "accounts wire-shape carries None sentinel"
    txt = build_accounts_hourly_report(now_utc=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc))
    assert "Accounts — data unavailable" in txt


# ---------------------------------------------------------------------------
# Strategy snapshots — D3 None-sentinel regression
# (see docs/audits/silent-empty-reporting-2026-05-10.md § Phase-2 #3)
# ---------------------------------------------------------------------------


def test_strategy_snapshots_returns_none_on_runtime_error(monkeypatch):
    """``strategy_dashboard_data`` raising RuntimeError → None sentinel.

    S-067 follow-up D3: a call-time failure now returns ``None``
    (data unavailable) rather than ``[]`` (no strategies active).
    """
    from src.runtime.hourly_report import strategy_snapshots
    sys.modules.pop("src.bot.data_loaders", None)
    bad = MagicMock()
    bad.strategy_dashboard_data.side_effect = RuntimeError("loader broken")
    sys.modules["src.bot.data_loaders"] = bad
    assert strategy_snapshots() is None


def test_strategy_snapshots_returns_none_on_oserror(monkeypatch):
    """OSError from strategy_dashboard_data → None sentinel."""
    from src.runtime.hourly_report import strategy_snapshots
    sys.modules.pop("src.bot.data_loaders", None)
    bad = MagicMock()
    bad.strategy_dashboard_data.side_effect = OSError("filesystem hiccup")
    sys.modules["src.bot.data_loaders"] = bad
    assert strategy_snapshots() is None


def test_strategy_snapshots_returns_empty_on_no_data(monkeypatch):
    """Empty list return from data_loaders stays the empty path."""
    from src.runtime.hourly_report import strategy_snapshots
    sys.modules.pop("src.bot.data_loaders", None)
    fake = MagicMock()
    fake.strategy_dashboard_data = lambda: []
    sys.modules["src.bot.data_loaders"] = fake
    assert strategy_snapshots() == []


def test_render_strategy_section_data_unavailable():
    """When ``strategies is None``, renderer surfaces 'data unavailable'."""
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    report = {
        "now_utc": now,
        "ticks": {"ticks_ok": 0, "ticks_err": 0, "signals_total": 0,
                  "signals_by_strategy": {}, "last_tick_ts": None},
        "trades": {"placed": [], "closed": [], "realized_pnl": 0.0},
        "accounts": [],
        "strategies": None,  # D3 sentinel
        "outcomes": {"top_errors": []},
        "health": {"tick_age_s": 60, "tick_stale": False, "tick_interval_s": 900,
                   "warn_count": 0, "error_count": 0, "critical_count": 0,
                   "overall": "ok", "checks": []},
    }
    txt = render_report(report)
    assert "Strategies (today) — data unavailable" in txt
    assert "strategy_dashboard_data() raised" in txt
    # The "(none active)" wording must NOT appear — it would
    # collapse the unavailable sentinel into the empty-list path.
    assert "(none active)" not in txt


def test_assemble_hourly_data_with_failing_strategy_dashboard(tmp_path, monkeypatch):
    """End-to-end: strategy_dashboard_data raising → wire-shape carries None,
    build_hourly_report renders the 'data unavailable' section."""
    monkeypatch.setattr(hr, "SIGNAL_AUDIT_FILE", tmp_path / "missing.jsonl")
    monkeypatch.setattr(hr, "OUTCOMES_FILE", tmp_path / "missing-outcomes.jsonl")
    monkeypatch.setattr(hr, "RUNTIME_LOGS", tmp_path)
    monkeypatch.setattr(hr, "BALANCE_SNAPSHOT_FILE", tmp_path / "snap.json")
    monkeypatch.setattr(hr, "_trade_journal_path", lambda: None)

    sys.modules.pop("src.bot.data_loaders", None)
    bad = MagicMock()
    bad.list_accounts = lambda: []
    bad.account_balance = lambda *_: None
    bad.account_open_positions = lambda *_: None
    bad.strategy_dashboard_data.side_effect = RuntimeError("loader broken")
    sys.modules["src.bot.data_loaders"] = bad

    from src.runtime.hourly_report import assemble_hourly_data
    data = assemble_hourly_data(now_utc=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc))
    assert data["strategies"] is None, "strategies wire-shape carries None sentinel"
    txt = build_hourly_report(now_utc=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc))
    assert "Strategies (today) — data unavailable" in txt


# ---------------------------------------------------------------------------
# Outcomes in window
# ---------------------------------------------------------------------------


def test_outcomes_in_window_aggregates(tmp_path, monkeypatch):
    f = tmp_path / "outcomes.jsonl"
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    inside = (now - timedelta(minutes=10)).isoformat()
    outside = (now - timedelta(hours=2)).isoformat()
    f.write_text("\n".join([
        json.dumps({"ts": inside, "level": "warn", "action": "x", "status": "y"}),
        json.dumps({"ts": inside, "level": "error", "action": "order_submit",
                    "status": "failed_exchange", "reason": "bybit 503"}),
        json.dumps({"ts": inside, "level": "error", "action": "order_submit",
                    "status": "failed_exchange", "reason": "bybit 503"}),
        json.dumps({"ts": inside, "level": "critical", "action": "tick",
                    "status": "exception", "reason": "KeyError"}),
        json.dumps({"ts": outside, "level": "error", "action": "x", "status": "y"}),
    ]))
    monkeypatch.setattr(hr, "OUTCOMES_FILE", f)
    out = outcomes_in_window(now - timedelta(hours=1))
    assert out["warn_count"] == 1
    assert out["error_count"] == 2
    assert out["critical_count"] == 1
    assert out["top_errors"][0][1] == 2  # bybit 503 fingerprint counted twice


def test_outcomes_in_window_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "OUTCOMES_FILE", tmp_path / "nope.jsonl")
    out = outcomes_in_window(datetime.now(timezone.utc))
    assert out == {"warn_count": 0, "error_count": 0,
                   "critical_count": 0, "top_errors": []}


# ---------------------------------------------------------------------------
# Health summary
# ---------------------------------------------------------------------------


def test_health_summary_marks_stale_on_old_tick():
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    last_tick = (now - timedelta(minutes=45)).isoformat()
    h = health_summary(
        last_tick_ts=last_tick,
        outcomes={"warn_count": 0, "error_count": 0, "critical_count": 0},
        tick_interval_s=900,  # 15 min
        now_utc=now,
        health_checks=[],
    )
    # 45 min > 2 * 15 min → stale → degraded
    assert h["tick_stale"] is True
    assert h["overall"] == "degraded"


def test_health_summary_critical_marks_degraded():
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    h = health_summary(
        last_tick_ts=now.isoformat(),
        outcomes={"warn_count": 0, "error_count": 0, "critical_count": 1},
        tick_interval_s=900,
        now_utc=now,
        health_checks=[],
    )
    assert h["overall"] == "degraded"


def test_health_summary_error_marks_warn():
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    h = health_summary(
        last_tick_ts=now.isoformat(),
        outcomes={"warn_count": 0, "error_count": 1, "critical_count": 0},
        tick_interval_s=900,
        now_utc=now,
        health_checks=[],
    )
    assert h["overall"] == "warn"


def test_health_summary_clean_is_ok():
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    h = health_summary(
        last_tick_ts=(now - timedelta(minutes=2)).isoformat(),
        outcomes={"warn_count": 0, "error_count": 0, "critical_count": 0},
        tick_interval_s=900,
        now_utc=now,
        health_checks=[],
    )
    assert h["overall"] == "ok"


def test_health_summary_picks_up_check_results():
    """When a passed check is critical, overall must be degraded."""
    from src.runtime.health import HealthCheck
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    h = health_summary(
        last_tick_ts=now.isoformat(),
        outcomes={"warn_count": 0, "error_count": 0, "critical_count": 0},
        tick_interval_s=900,
        now_utc=now,
        health_checks=[HealthCheck("service", "critical", "service down")],
    )
    assert h["overall"] == "degraded"


def test_health_summary_warn_check_demotes_to_warn():
    from src.runtime.health import HealthCheck
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    h = health_summary(
        last_tick_ts=now.isoformat(),
        outcomes={"warn_count": 0, "error_count": 0, "critical_count": 0},
        tick_interval_s=900,
        now_utc=now,
        health_checks=[HealthCheck("disk", "warn", "low disk")],
    )
    assert h["overall"] == "warn"


# ---------------------------------------------------------------------------
# Renderer + top-level assembler
# ---------------------------------------------------------------------------


def test_render_report_contains_each_section():
    """S-telegram-format: ``render_report`` now returns the strategies-
    focused HTML report. Account-only fields (balance, trades placed)
    moved to ``render_accounts_report``. The pin is on what the
    operator MUST see in each pane."""
    from src.runtime.hourly_report import render_accounts_report
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    report = {
        "now_utc": now,
        "ticks": {"ticks_ok": 4, "ticks_err": 0, "signals_total": 2,
                  "signals_by_strategy": {"vwap": 2}, "last_tick_ts": now.isoformat()},
        "trades": {"placed": [{"id": 1}], "closed": [], "realized_pnl": 0.0},
        "accounts": [{"account_id": "main", "balance": 1000.0,
                      "delta_1h": 5.0, "api_ok": True, "open_positions": 1}],
        "strategies": [{"strategy": "vwap", "signals_today": 5,
                        "pnl": 4.20, "open_pos": 1}],
        "outcomes": {"top_errors": []},
        "health": {"tick_age_s": 60, "tick_stale": False, "tick_interval_s": 900,
                   "warn_count": 0, "error_count": 0, "critical_count": 0,
                   "overall": "ok", "checks": []},
    }
    strat_txt = render_report(report)
    for needle in [
        "Strategies",
        "Performance",
        "4 ok / 0 errored",
        "vwap×2",
        "vwap: 5 signals",
        "Health",
        "All systems normal",
        "<blockquote expandable>",
    ]:
        assert needle in strat_txt, f"missing: {needle}\nfull:\n{strat_txt}"

    acct_txt = render_accounts_report(report)
    for needle in [
        "Accounts",
        "Trades",
        "1 placed",
        "main: bal $1,000.00",
        "+$5.00",
        "<blockquote expandable>",
    ]:
        assert needle in acct_txt, f"missing: {needle}\nfull:\n{acct_txt}"


def test_render_report_degraded_path_calls_for_action():
    now = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
    report = {
        "now_utc": now,
        "ticks": {"ticks_ok": 0, "ticks_err": 4, "signals_total": 0,
                  "signals_by_strategy": {}, "last_tick_ts": None},
        "trades": {"placed": [], "closed": [], "realized_pnl": 0.0},
        "accounts": [],
        "strategies": [],
        "outcomes": {"top_errors": [("order_submit:bybit 503", 4)]},
        "health": {"tick_age_s": None, "tick_stale": True, "tick_interval_s": 900,
                   "warn_count": 1, "error_count": 4, "critical_count": 0,
                   "overall": "degraded"},
    }
    txt = render_report(report)
    assert "[DEGRADED]" in txt
    assert "ACTION NEEDED" in txt
    assert "order_submit:bybit 503 (4x)" in txt


def test_build_hourly_report_never_raises_when_everything_is_empty(tmp_path, monkeypatch):
    """No audit, no DB, no outcomes — should still return a renderable report."""
    monkeypatch.setattr(hr, "SIGNAL_AUDIT_FILE", tmp_path / "missing.jsonl")
    monkeypatch.setattr(hr, "OUTCOMES_FILE", tmp_path / "missing-outcomes.jsonl")
    monkeypatch.setattr(hr, "RUNTIME_LOGS", tmp_path)
    monkeypatch.setattr(hr, "BALANCE_SNAPSHOT_FILE", tmp_path / "snap.json")
    monkeypatch.setattr(hr, "_trade_journal_path", lambda: None)

    fake_loaders = MagicMock()
    fake_loaders.list_accounts = lambda: []
    fake_loaders.account_balance = lambda *_: None
    fake_loaders.account_open_positions = lambda *_: None
    fake_loaders.strategy_dashboard_data = lambda: []
    sys.modules["src.bot.data_loaders"] = fake_loaders

    txt = build_hourly_report(now_utc=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc))
    # S-telegram-format: header is the new "Strategies — <slot>" line;
    # the legacy "Hourly Report" wording lives in ``render_report_plain``
    # for plain-mode callers that still need it.
    assert "Strategies" in txt
    assert "0 ok / 0 errored" in txt
    assert "(none active)" in txt


def test_build_hourly_report_swallows_assembly_errors(monkeypatch):
    """If e.g. data_loaders explodes mid-build, return a degraded message, not raise."""
    def boom(*_, **__):
        raise RuntimeError("assembler kaboom")
    monkeypatch.setattr(hr, "summarize_ticks", boom)
    txt = build_hourly_report(now_utc=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc))
    assert "[WARN]" in txt
    assert "Report assembly failed" in txt
