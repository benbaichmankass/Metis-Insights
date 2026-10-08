"""Regression: killzone/bias in the signal meta must reach ``trades`` and the
``setup_labels`` dataset (previously the INSERT omitted both columns, so the
dataset read 100% NaN)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.execute import _log_trade_to_journal
from ml.datasets.families.setup_labels import SetupLabelsBuilder


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return db_path


def _pkg(meta):
    return OrderPackage(
        strategy="vwap", symbol="BTCUSDT", direction="short",
        entry=50_000.0, sl=50_500.0, tp=49_000.0, confidence=0.7, meta=meta,
    )


def test_killzone_bias_persist_and_reach_setup_labels(tmp_journal):
    cfg = {"account_id": "bybit_2", "exchange": "bybit"}
    meta = {"entry_reason": "x", "killzone": "ny", "bias": "bearish"}
    assert _log_trade_to_journal(_pkg(meta), cfg, {"qty": 1.0}, trade_id="t1")
    # a signal with no killzone/bias stays NULL, not the string "None"
    assert _log_trade_to_journal(_pkg({"entry_reason": "x"}), cfg, {"qty": 1.0}, trade_id="t2")

    conn = sqlite3.connect(str(tmp_journal))
    rows = conn.execute("SELECT killzone, bias FROM trades ORDER BY id").fetchall()
    assert rows == [("ny", "bearish"), (None, None)]
    # close both so setup_labels (CLOSED, pnl not null) sees them
    conn.execute("UPDATE trades SET status='closed', pnl=1.0, pnl_percent=1.0, "
                 "setup_type=COALESCE(NULLIF(setup_type,''),'vwap')")
    conn.commit()
    conn.close()

    out = list(SetupLabelsBuilder().iter_rows(db_path=Path(tmp_journal)))
    populated = [r for r in out if r["killzone"] and r["bias"]]
    assert len(populated) == 1 and populated[0]["killzone"] == "ny"
