"""S-017 — smoke_test_trade.py safety + signal-shape tests.

The script never gets unit-tested on the live VM; the safety cap is the
only thing standing between an operator typo and a 10-BTC order. Tests
here pin:

* `--qty > MAX_SAFE_QTY` returns 2 (refuses to start).
* `--qty <= 0` returns 2.
* LIVE without `--confirm` returns 2.
* LIVE without `ALLOW_LIVE_TRADING=true` returns 2.
* Disabled account in accounts.yaml → returns 2.
* Missing API key in env → returns 2.
* Signal shape: smoke entries always carry
  `meta.strategy_name="smoke_test"` + `meta.is_smoke=True`.
* Audit log entries for OPEN attempts always tag `strategy=smoke_test`.
* Open + close round-trip on a stubbed safe_place_order: 4 audit events
  written (open_attempt, open_result, close_attempt, close_result).

The tests stub `safe_place_order` so they never touch a real exchange.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import smoke_test_trade as smoke  # noqa: E402


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------


def test_qty_above_cap_refuses(monkeypatch, caplog):
    rc = smoke.main(["--account", "bybit_2", "--qty", "0.01",
                     "--side", "buy", "--confirm"])
    assert rc == 2


def test_qty_zero_refuses():
    rc = smoke.main(["--account", "bybit_2", "--qty", "0",
                     "--side", "buy", "--confirm"])
    assert rc == 2


def test_qty_negative_refuses():
    rc = smoke.main(["--account", "bybit_2", "--qty", "-0.0001",
                     "--side", "buy", "--confirm"])
    assert rc == 2


def test_live_without_confirm_refuses():
    rc = smoke.main(["--account", "bybit_2", "--qty", "0.0001",
                     "--side", "buy"])
    assert rc == 2


def test_live_without_allow_flag_refuses(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
    monkeypatch.setattr(smoke, "_account_settings",
                        lambda name: {"BYBIT_API_KEY": "fake",
                                      "BYBIT_API_SECRET": "fake"})
    rc = smoke.main(["--account", "bybit_2", "--qty", "0.0001",
                     "--side", "buy", "--confirm"])
    assert rc == 2


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------


def test_account_settings_disabled_account_refuses(monkeypatch):
    monkeypatch.setattr(smoke, "_load_accounts_yaml",
                        lambda: {"prop_breakout_1": {"enabled": False}})
    with pytest.raises(SystemExit):
        smoke._account_settings("prop_breakout_1")


def test_account_settings_unknown_name(monkeypatch):
    monkeypatch.setattr(smoke, "_load_accounts_yaml", lambda: {})
    with pytest.raises(SystemExit):
        smoke._account_settings("bybit_999")


def test_account_settings_missing_creds(monkeypatch):
    monkeypatch.setattr(
        smoke, "_load_accounts_yaml",
        lambda: {"bybit_2": {"api_key_env": "BYBIT_API_KEY_2", "exchange": "bybit"}},
    )
    monkeypatch.delenv("BYBIT_API_KEY_2", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET_2", raising=False)
    with pytest.raises(SystemExit):
        smoke._account_settings("bybit_2")


def test_account_settings_happy_path(monkeypatch):
    monkeypatch.setattr(
        smoke, "_load_accounts_yaml",
        lambda: {"bybit_2": {"api_key_env": "BYBIT_API_KEY_2", "exchange": "bybit"}},
    )
    monkeypatch.setenv("BYBIT_API_KEY_2", "fake-key")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "fake-secret")
    settings = smoke._account_settings("bybit_2")
    assert settings["BYBIT_API_KEY"] == "fake-key"
    assert settings["BYBIT_API_SECRET"] == "fake-secret"
    assert settings["EXCHANGE"] == "bybit"
    assert settings["ACCOUNT_ID"] == "bybit_2"


# ---------------------------------------------------------------------------
# Signal shape
# ---------------------------------------------------------------------------


def test_build_smoke_signal_tags_strategy_correctly():
    sig = smoke._build_smoke_signal("buy", 0.0001, "bybit_2", note="test")
    assert sig["meta"]["strategy_name"] == "smoke_test"
    assert sig["meta"]["is_smoke"] is True
    assert sig["meta"]["account_id"] == "bybit_2"
    assert sig["side"] == "buy"
    assert sig["qty"] == 0.0001
    assert "smoke_id" in sig["meta"]


def test_build_smoke_signal_smoke_ids_unique():
    a = smoke._build_smoke_signal("buy", 0.0001, "bybit_2", note="t")
    b = smoke._build_smoke_signal("buy", 0.0001, "bybit_2", note="t")
    assert a["meta"]["smoke_id"] != b["meta"]["smoke_id"]


# ---------------------------------------------------------------------------
# Round-trip dispatch (stubbed safe_place_order)
# ---------------------------------------------------------------------------


def test_dispatch_dry_run_passes_safety_settings(monkeypatch):
    captured: dict = {}

    def fake_safe_place_order(order, settings, client):
        captured["order"] = order
        captured["settings"] = dict(settings)
        captured["client"] = client
        return {"status": "skipped", "reason": "DRY_RUN"}

    fake_orders = MagicMock()
    fake_orders.safe_place_order = fake_safe_place_order
    monkeypatch.setitem(sys.modules, "src.runtime.orders", fake_orders)

    sig = smoke._build_smoke_signal("buy", 0.0001, "bybit_2", note="t")
    settings = {"BYBIT_API_KEY": "k", "BYBIT_API_SECRET": "s"}
    result = smoke._dispatch(sig, settings, dry_run=True)
    assert result["status"] == "skipped"
    assert captured["settings"]["DRY_RUN"] == "true"
    assert captured["settings"]["ALLOW_LIVE_TRADING"] == "false"
    assert captured["client"] is None


def test_main_round_trip_dry_run_writes_four_audit_events(
    monkeypatch, tmp_path,
):
    """Open + close in dry-run should write 4 entries to signal_audit.jsonl,
    all tagged strategy=smoke_test."""
    audit = tmp_path / "signal_audit.jsonl"
    monkeypatch.setattr(smoke, "AUDIT_PATH", audit)
    monkeypatch.setattr(
        smoke, "_load_accounts_yaml",
        lambda: {"bybit_2": {"api_key_env": "BYBIT_API_KEY_2", "exchange": "bybit"}},
    )
    monkeypatch.setenv("BYBIT_API_KEY_2", "fake")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "fake")
    monkeypatch.setattr(smoke.time, "sleep", lambda *a, **kw: None)

    fake_orders = MagicMock()
    # Both legs report submitted so we exercise the close path.
    fake_orders.safe_place_order = lambda *a, **kw: {"status": "submitted"}
    monkeypatch.setitem(sys.modules, "src.runtime.orders", fake_orders)

    rc = smoke.main([
        "--account", "bybit_2", "--qty", "0.0001",
        "--side", "buy", "--dry-run",
    ])
    assert rc == 0

    lines = [json.loads(l) for l in audit.read_text().splitlines() if l.strip()]
    assert len(lines) == 4
    events = [l["event"] for l in lines]
    assert events == [
        "smoke_open_attempt", "smoke_open_result",
        "smoke_close_attempt", "smoke_close_result",
    ]
    for line in lines:
        assert line["strategy"] == "smoke_test"


def test_main_returns_one_when_open_rejected(monkeypatch, tmp_path):
    """A rejection from the exchange is still a successful smoke (proves
    plumbing-on-rejection); script returns 1 to make the operator-side
    distinction visible. No close is attempted."""
    audit = tmp_path / "signal_audit.jsonl"
    monkeypatch.setattr(smoke, "AUDIT_PATH", audit)
    monkeypatch.setattr(
        smoke, "_load_accounts_yaml",
        lambda: {"bybit_2": {"api_key_env": "BYBIT_API_KEY_2", "exchange": "bybit"}},
    )
    monkeypatch.setenv("BYBIT_API_KEY_2", "fake")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "fake")

    fake_orders = MagicMock()
    fake_orders.safe_place_order = lambda *a, **kw: {
        "status": "failed_exchange", "reason": "qty too small",
    }
    monkeypatch.setitem(sys.modules, "src.runtime.orders", fake_orders)

    rc = smoke.main([
        "--account", "bybit_2", "--qty", "0.0001",
        "--side", "buy", "--dry-run",
    ])
    assert rc == 1
    lines = [json.loads(l) for l in audit.read_text().splitlines() if l.strip()]
    # Only open_attempt + open_result; no close_*.
    assert [l["event"] for l in lines] == [
        "smoke_open_attempt", "smoke_open_result",
    ]
    assert lines[1]["status"] == "failed_exchange"
