"""Tests for the trade-lifecycle pings (TELEGRAM-SPEC §4.2).

Covers the three builders in ``src.runtime.execution_diagnostics``
(open / update / close) and the trader drainer's ``parse_mode``
pass-through in ``src.bot.cloud_notifier``. The builders are best-effort
file producers; the drainer is the consumer that renders the self-titled
HTML (so the "Details ▾" expandable blockquote works).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from src.runtime import execution_diagnostics as ed


def _read_payload(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_trade_open_ping_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path)
    path = ed.enqueue_trade_open(
        account="bybit_1", strategy="vwap", symbol="BTCUSDT", side="buy",
        qty=0.01, entry=80000.0, sl=79000.0, tp=82000.0, order_id="oid-1",
    )
    assert path is not None
    payload = _read_payload(path)
    assert payload["parse_mode"] == "HTML"
    body = payload["body"]
    assert "TRADE OPENED" in body and "BTCUSDT" in body and "BUY" in body
    # Expandable details block present.
    assert "<blockquote expandable>" in body
    assert "bybit_1" in body and "vwap" in body and "oid-1" in body


def test_trade_close_ping_win_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path)
    win = _read_payload(ed.enqueue_trade_close(
        symbol="ETHUSDT", account="a1", strategy="vwap",
        entry=2000.0, exit_price=2100.0, pnl=45.0, reason="TP1",
    ))["body"]
    assert "TRADE CLOSED" in win and "+$45.00" in win and "✅ win" in win

    loss = _read_payload(ed.enqueue_trade_close(
        symbol="ETHUSDT", pnl=-12.5, reason="SL",
    ))["body"]
    assert "-$12.50" in loss and "❌ loss" in loss


def test_trade_update_ping_lists_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path)
    body = _read_payload(ed.enqueue_trade_update(
        symbol="BTCUSDT", account="a1", strategy="turtle_soup",
        changes=["SL moved 79000 → 79500", "partial close 50%"],
    ))["body"]
    assert "TRADE UPDATED" in body and "BTCUSDT" in body
    assert "SL moved" in body and "partial close 50%" in body


def test_trade_open_ping_never_raises_on_bad_dir(tmp_path, monkeypatch):
    # Point the inbox at a path that can't be created (a file, not a dir).
    bad = tmp_path / "afile"
    bad.write_text("x")
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", bad / "sub")
    # mkdir under a file raises internally; the builder swallows + returns None.
    assert ed.enqueue_trade_open(
        account="a", strategy="s", symbol="X", side="buy", qty=1.0,
    ) is None


def test_drainer_honours_parse_mode_and_skips_prefix(tmp_path, monkeypatch):
    """An HTML ping is sent verbatim (no priority prefix) with parse_mode."""
    from src.bot import cloud_notifier

    monkeypatch.setattr(cloud_notifier, "PENDING_PINGS_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234")
    (tmp_path / "001-trade-open.json").write_text(json.dumps({
        "priority": "normal",
        "body": "<b>🟢 TRADE OPENED — BTCUSDT BUY</b>",
        "parse_mode": "HTML",
    }), encoding="utf-8")

    sent = []
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock(side_effect=lambda **kw: sent.append(kw))

    asyncio.new_event_loop().run_until_complete(
        cloud_notifier._drain_pending_pings(ctx)
    )

    assert len(sent) == 1
    assert sent[0]["parse_mode"] == "HTML"
    # Self-titled: no "ℹ️ " priority prefix prepended.
    assert sent[0]["text"] == "<b>🟢 TRADE OPENED — BTCUSDT BUY</b>"


def test_drainer_plain_ping_keeps_priority_prefix(tmp_path, monkeypatch):
    from src.bot import cloud_notifier

    monkeypatch.setattr(cloud_notifier, "PENDING_PINGS_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234")
    (tmp_path / "002-plain.json").write_text(json.dumps({
        "priority": "high", "body": "something happened",
    }), encoding="utf-8")

    sent = []
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock(side_effect=lambda **kw: sent.append(kw))

    asyncio.new_event_loop().run_until_complete(
        cloud_notifier._drain_pending_pings(ctx)
    )

    assert len(sent) == 1
    assert sent[0]["parse_mode"] is None
    assert sent[0]["text"].startswith("🔔")  # high-priority icon prefix


def test_modify_path_fires_update_ping(monkeypatch):
    """order_monitor's SL/TP-modify branch emits a trade-update ping."""
    from src.runtime import order_monitor as om

    captured = {}
    monkeypatch.setattr(ed, "enqueue_trade_update", lambda **kw: captured.update(kw))
    monkeypatch.setattr(om, "_send_modify_to_exchange", lambda *a, **k: {"ok": True})

    db = MagicMock()
    db.get_trades.return_value = [{
        "id": 7, "symbol": "BTCUSDT", "account_id": "bybit_1",
        "strategy_name": "vwap", "status": "open",
    }]

    summary = om._StrategyTickSummary()
    open_pkg = {
        "order_package_id": "pkg1", "linked_trade_id": 7,
        "symbol": "BTCUSDT", "strategy_name": "vwap",
    }
    om._apply_update(db, open_pkg, {"sl": 79500.0}, summary)

    assert summary.updated_count == 1
    assert captured.get("symbol") == "BTCUSDT"
    assert captured.get("account") == "bybit_1"
    assert any("SL" in c for c in captured.get("changes", []))


def test_orphan_created_flag_logs_and_pings(tmp_path, monkeypatch):
    """A new orphan row fires a CRITICAL red-flag ping (with the /system-review
    call-to-action) AND appends a durable follow-up event for the backlog drain."""
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path)
    log = tmp_path / "orphan_events.jsonl"
    monkeypatch.setattr(ed, "ORPHAN_EVENTS_LOG", log)

    path = ed.enqueue_orphan_created_flag(
        account="ib_paper", symbol="MHG", side="short", trade_id=2835,
        origin="reverse_reconciler_adopt", reason="no matching open journal row",
    )

    # Red-flag ping.
    assert path is not None
    payload = _read_payload(path)
    assert payload["priority"] == "critical"
    body = payload["body"]
    assert "ORPHAN TRADE CREATED" in body
    assert "MHG" in body and "short" in body and "2835" in body
    assert "/system-review" in body

    # Durable follow-up record.
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    evt = json.loads(lines[0])
    assert evt["kind"] == "orphan_created"
    assert evt["account"] == "ib_paper" and evt["symbol"] == "MHG"
    assert evt["trade_id"] == 2835
    assert evt["origin"] == "reverse_reconciler_adopt"
    assert evt["ts"]  # timestamped


def test_orphan_created_flag_appends_each_event(tmp_path, monkeypatch):
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path)
    log = tmp_path / "orphan_events.jsonl"
    monkeypatch.setattr(ed, "ORPHAN_EVENTS_LOG", log)
    ed.enqueue_orphan_created_flag(account="a", symbol="MGC", side="long",
                                   trade_id=1, origin="adopt_bare")
    ed.enqueue_orphan_created_flag(account="a", symbol="MGC", side="long",
                                   trade_id=2, origin="adopt_bare")
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_close_failure_ping_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path)
    path = ed.enqueue_close_failure(
        account="ib_paper", symbol="MHG", side="long", qty=3,
        consecutive=3, error="venue error",
    )
    assert path is not None
    payload = _read_payload(path)
    assert payload["priority"] == "high"
    body = payload["body"]
    assert "CLOSE failing" in body and "MHG" in body
    assert "Consecutive close failures: 3" in body


def test_stuck_package_sweep_ping_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path)
    payload = _read_payload(ed.enqueue_stuck_package_sweep(count=2))
    assert payload["priority"] == "high"
    assert "Stuck linked-package sweep" in payload["body"]
    assert "2 order package" in payload["body"]
