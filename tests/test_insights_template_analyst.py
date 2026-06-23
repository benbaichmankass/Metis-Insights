"""Tests for the M13 S2 template analyst — the provider-free default mode.

Verifies:
- The generator routes to the template path when INSIGHTS_MODEL_MODE
  is unset (default) or set to "template".
- The template path NEVER calls the injected `anthropic_call` seam.
- Cache + history rows are written with model_id="template:v1" and
  cost = $0; the usage row is `ok` with 0 input + 0 output tokens.
- Budget gate is bypassed (template can run even when budget=0).
- Empty-DB case produces a valid envelope without erroring.
- Per-endpoint outputs include grounded metrics + correct grade.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime.insights import generator, template_analyst, usage


@pytest.fixture
def template_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    runtime_logs = tmp_path / "runtime_logs"
    runtime_logs.mkdir()
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(runtime_logs))
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    monkeypatch.setenv("INSIGHTS_ENABLED", "1")
    # Pin to template explicitly — exercises the default flip too.
    monkeypatch.setenv("INSIGHTS_MODEL_MODE", "template")
    return {"db": db_path, "runtime_logs": runtime_logs}


def _seed_trades(db_path: Path, rows: list[dict]) -> None:
    """Hydrate the canonical schema then insert some closed-trade rows."""
    from src.units.db.database import Database

    Database(str(db_path))  # bootstrap tables
    # summary_data() windows closed trades on the canonical close-time basis
    # (epoch-ms-aware closed_at -> order_packages.updated_at -> open timestamp).
    # These fixtures carry no closed_at / order package, so close-time falls back
    # to `timestamp`; default it to "just now" so a "recently closed trade"
    # fixture actually lands inside the 24h window (it previously relied on the
    # created_at default, which the old created_at-window query used).
    recent_ts = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    for r in rows:
        cur.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
            "position_size, status, pnl, exit_reason, strategy_name, "
            "is_backtest, is_demo) VALUES (?,?,?,?,?,?,?,?,?,0,0)",
            (
                r.get("timestamp", recent_ts),
                r["symbol"], r["direction"], r["entry_price"],
                r["position_size"], r["status"], r["pnl"],
                r.get("exit_reason"), r.get("strategy_name"),
            ),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Template-path routing
# ---------------------------------------------------------------------------


def test_default_mode_is_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSIGHTS_MODEL_MODE", raising=False)
    # The accessor short-circuits on missing env to "template" — the default.
    assert generator._mode() == "template"


def test_template_path_does_not_invoke_anthropic(template_env: dict[str, Path]) -> None:
    seen = {"called": False}

    def stub(*_args, **_kwargs):
        seen["called"] = True
        return {"text": "{}", "input_tokens": 1, "output_tokens": 1}

    payload = generator.generate("summary", anthropic_call=stub)
    assert payload is not None
    assert seen["called"] is False, "template mode must not invoke anthropic_call"


def test_template_writes_cache_history_and_zero_cost_usage(
    template_env: dict[str, Path]
) -> None:
    payload = generator.generate("summary")
    assert payload is not None
    assert payload["model_id"] == template_analyst.MODEL_ID
    # Cache file
    cache_path = template_env["runtime_logs"] / "insights" / "summary.json"
    assert cache_path.exists()
    saved = json.loads(cache_path.read_text())
    assert saved["model_id"] == template_analyst.MODEL_ID

    # Usage table: one ok row, 0 cost.
    summary = usage.summarize_usage()
    assert summary["current_month_calls"] == 1
    assert summary["current_month_tokens"] == 0
    assert summary["current_month_usd"] == 0.0
    assert summary["by_endpoint"][0]["endpoint"] == "summary"


def test_template_bypasses_budget_gate(
    template_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even with an absurdly low budget, the template path runs.
    monkeypatch.setenv("INSIGHTS_MONTHLY_BUDGET_USD", "0.0")
    payload = generator.generate("summary")
    assert payload is not None
    assert payload["model_id"] == template_analyst.MODEL_ID


def test_template_handles_empty_db(template_env: dict[str, Path]) -> None:
    # No DB created — the data sources tolerate a missing file.
    payload = generator.generate("summary")
    assert payload is not None
    assert payload["grade"] in {"good", "watch", "concern"}
    assert "summary_md" in payload
    assert "Trades" in payload["summary_md"] or "No trades" in payload["summary_md"]


def test_template_grade_concern_on_heavy_loss(template_env: dict[str, Path]) -> None:
    _seed_trades(template_env["db"], [
        {"symbol": "BTCUSDT", "direction": "buy", "entry_price": 100,
         "position_size": 1, "status": "closed", "pnl": -150.0,
         "exit_reason": "sl", "strategy_name": "vwap"},
    ])
    payload = generator.generate("summary")
    assert payload["grade"] == "concern"
    # net PnL was -$150 — should fire the drawdown_threshold signal.
    kinds = {s.get("kind") for s in payload.get("signals", [])}
    assert "drawdown_threshold" in kinds


def test_template_grade_good_on_profit(template_env: dict[str, Path]) -> None:
    _seed_trades(template_env["db"], [
        {"symbol": "BTCUSDT", "direction": "buy", "entry_price": 100,
         "position_size": 1, "status": "closed", "pnl": 25.0,
         "exit_reason": "tp", "strategy_name": "vwap"},
        {"symbol": "BTCUSDT", "direction": "sell", "entry_price": 100,
         "position_size": 1, "status": "closed", "pnl": 15.0,
         "exit_reason": "tp", "strategy_name": "vwap"},
    ])
    payload = generator.generate("summary")
    assert payload["grade"] == "good"
    assert "vwap" in payload["summary_md"]
    assert "$40.00" in payload["summary_md"]


def test_template_strategy_endpoint(template_env: dict[str, Path]) -> None:
    _seed_trades(template_env["db"], [
        {"symbol": "BTCUSDT", "direction": "buy", "entry_price": 100,
         "position_size": 1, "status": "closed", "pnl": 10.0,
         "exit_reason": "tp", "strategy_name": "vwap"},
        {"symbol": "BTCUSDT", "direction": "buy", "entry_price": 100,
         "position_size": 1, "status": "closed", "pnl": -5.0,
         "exit_reason": "sl", "strategy_name": "vwap"},
    ])
    payload = generator.generate("strategy", strategy_name="vwap")
    assert payload is not None
    assert "vwap" in payload["summary_md"].lower()
    # cache file landed under strategy_<name>.json
    cache_path = template_env["runtime_logs"] / "insights" / "strategy_vwap.json"
    assert cache_path.exists()


def test_template_recent_endpoint_table_rendering(template_env: dict[str, Path]) -> None:
    _seed_trades(template_env["db"], [
        {"symbol": "BTCUSDT", "direction": "buy", "entry_price": 100,
         "position_size": 1, "status": "closed", "pnl": 7.0,
         "exit_reason": "tp", "strategy_name": "vwap"},
    ])
    payload = generator.generate("recent", limit=10)
    assert payload is not None
    md = payload["summary_md"]
    assert "| #" in md and "| strategy" in md
    assert "vwap" in md
    # BL-20260529-006 regression: the header stats must reflect the SAME
    # closed trades the table shows. recent_data() filters WHERE status='closed'
    # but does NOT select the status column, so the template must not re-drop
    # rows on an absent status key (which zeroed the header to 0W/0L/$0 over a
    # populated table). One +$7 win => 1W / 0L / $7.00, not 0W / 0L / $0.00.
    assert "1W / 0L" in md, md
    assert "$7.00" in md
    assert "0W / 0L" not in md


def test_template_health_endpoint_missing_snapshot(template_env: dict[str, Path]) -> None:
    payload = generator.generate("health")
    assert payload is not None
    assert "Health snapshot" in payload["summary_md"]


# ---------------------------------------------------------------------------
# Invalid mode falls back to template (no API key required)
# ---------------------------------------------------------------------------


def test_invalid_mode_falls_back_to_template(
    template_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSIGHTS_MODEL_MODE", "bogus")
    payload = generator.generate("summary")
    assert payload is not None
    assert payload["model_id"] == template_analyst.MODEL_ID
