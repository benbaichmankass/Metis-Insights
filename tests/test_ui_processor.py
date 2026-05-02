"""CP-2026-05-02 — UI processor unit tests.

These cover the three read APIs the operator's bug report exposed:

* Account-first balance shape (key fingerprint included so duplicate
  keys are visible at the data layer, not buried in formatters).
* Recent signals always carry a ``strategy`` field.
* The hourly report passthrough never raises.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ui import processor


def test_get_recent_signals_returns_strategy_field(tmp_path, monkeypatch):
    audit = tmp_path / "signal_audit.jsonl"
    rows = [
        {"logged_at_utc": "2026-05-02T10:00:00+00:00", "strategy": "vwap",
         "symbol": "BTCUSDT", "side": "buy", "qty": 0.001, "status": "skipped"},
        {"logged_at_utc": "2026-05-02T10:01:00+00:00", "strategy": "turtle_soup",
         "symbol": "BTCUSDT", "side": "sell", "qty": 0.001,
         "status": "multi_account_dispatched"},
    ]
    audit.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setenv("SIGNAL_AUDIT_PATH", str(audit))

    out = processor.get_recent_signals(limit=10)
    assert len(out) == 2
    # Every row must carry a strategy field; operator asked for it
    # explicitly. The processor backfills "unknown" if missing.
    for rec in out:
        assert "strategy" in rec and rec["strategy"]


def test_get_recent_signals_filters_by_strategy(tmp_path, monkeypatch):
    audit = tmp_path / "signal_audit.jsonl"
    rows = [
        {"logged_at_utc": "2026-05-02T10:00:00+00:00", "strategy": "vwap",
         "symbol": "BTCUSDT", "side": "buy", "qty": 0.001, "status": "skipped"},
        {"logged_at_utc": "2026-05-02T10:01:00+00:00", "strategy": "turtle_soup",
         "symbol": "BTCUSDT", "side": "sell", "qty": 0.001, "status": "skipped"},
    ]
    audit.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setenv("SIGNAL_AUDIT_PATH", str(audit))

    out = processor.get_recent_signals(limit=10, strategy="vwap")
    assert len(out) == 1
    assert out[0]["strategy"] == "vwap"


def test_get_account_balances_includes_account_id_and_fingerprint():
    fake_accounts = [
        {"account_id": "bybit_1", "exchange": "bybit",
         "strategies": ["turtle_soup"]},
        {"account_id": "bybit_2", "exchange": "bybit",
         "strategies": ["vwap"]},
    ]
    fake_diag = {
        "bybit_1": {"status": "ok", "total_usdt": 37.17, "raw": {}, "error": None},
        "bybit_2": {"status": "ok", "total_usdt": 41.02, "raw": {}, "error": None},
    }

    with patch("src.bot.data_loaders.list_accounts", return_value=fake_accounts), \
         patch(
             "src.bot.data_loaders.account_balance_with_diagnostic",
             side_effect=lambda acc: fake_diag[acc["account_id"]],
         ), \
         patch(
             "src.units.accounts.clients.resolve_credentials",
             side_effect=lambda acc: {
                 "api_key": "AAAA1111" if acc["account_id"] == "bybit_1"
                 else "BBBB2222",
                 "api_secret": "x",
             },
         ):
        rows = processor.get_account_balances()

    assert [r["account_id"] for r in rows] == ["bybit_1", "bybit_2"]
    assert rows[0]["key_fingerprint"] == "…1111"
    assert rows[1]["key_fingerprint"] == "…2222"
    # Different account_ids should show different totals — this is
    # exactly the symptom the operator complained about (both showing
    # the same number despite different accounts).
    assert rows[0]["total_usdt"] != rows[1]["total_usdt"]


def test_get_account_balances_surfaces_missing_creds():
    with patch(
        "src.bot.data_loaders.list_accounts",
        return_value=[{"account_id": "bybit_1", "exchange": "bybit",
                       "strategies": ["turtle_soup"]}],
    ), patch(
        "src.bot.data_loaders.account_balance_with_diagnostic",
        return_value={"status": "missing_creds", "total_usdt": None,
                      "raw": None, "error": "missing env vars: BYBIT_API_KEY_1"},
    ), patch(
        "src.units.accounts.clients.resolve_credentials", return_value={},
    ):
        rows = processor.get_account_balances()

    assert rows[0]["status"] == "missing_creds"
    assert "BYBIT_API_KEY_1" in (rows[0]["error"] or "")


def test_get_hourly_report_never_raises():
    out = processor.get_hourly_report()
    assert isinstance(out, str)
    assert out  # non-empty


def test_get_hourly_report_forwards_kwargs_to_build():
    """Sprint 025 T1: cmd_hourly passes now_utc + tick_interval_s through
    the processor; the processor must forward both into
    build_hourly_report so the bot and the webapp see the same window."""
    captured = {}

    def fake_build(*, now_utc=None, tick_interval_s=900):
        captured["now_utc"] = now_utc
        captured["tick_interval_s"] = tick_interval_s
        return "ok"

    fake_module = MagicMock()
    fake_module.build_hourly_report = fake_build
    with patch.dict(
        sys.modules, {"src.runtime.hourly_report": fake_module}, clear=False,
    ):
        from datetime import datetime, timezone
        ts = datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc)
        out = processor.get_hourly_report(now_utc=ts, tick_interval_s=300)
    assert out == "ok"
    assert captured["now_utc"] == ts
    assert captured["tick_interval_s"] == 300


def test_get_hourly_report_default_kwargs_omit_now_utc():
    """When called with no args, the processor must NOT pass
    ``now_utc=None`` through — the runtime helper expects an absent
    keyword (so it can default to ``datetime.now``), not an explicit
    None."""
    captured_kwargs = []

    def fake_build(**kwargs):
        captured_kwargs.append(kwargs)
        return "ok"

    fake_module = MagicMock()
    fake_module.build_hourly_report = fake_build
    with patch.dict(
        sys.modules, {"src.runtime.hourly_report": fake_module}, clear=False,
    ):
        processor.get_hourly_report()
    assert captured_kwargs[0] == {"tick_interval_s": 900}
