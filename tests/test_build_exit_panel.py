"""M30 · P5 — tests for scripts/research/build_exit_panel.py.

Synthetic fixture DB + an INJECTED candle fetcher (no network, no live DB), so
the builder wiring — geometry read, per-trade window fetch, excursion join,
honest coverage flag, leakage-stamped manifest — is exercised offline. Loaded via
importlib (scripts/research is not a package).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(os.path.dirname(_HERE), "scripts", "research", "build_exit_panel.py")


def _load():
    spec = importlib.util.spec_from_file_location("build_exit_panel", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bx = _load()


def _make_db(path):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE trades (
          id INTEGER PRIMARY KEY, strategy_name TEXT, symbol TEXT, status TEXT,
          direction TEXT, pnl REAL, is_backtest INT, account_class TEXT, is_demo INT,
          entry_price REAL, stop_loss REAL, exit_price REAL,
          timestamp TEXT, closed_at TEXT, setup_type TEXT, reconcile_status TEXT);
        CREATE TABLE order_packages (
          id INTEGER PRIMARY KEY, linked_trade_id INT, signal_logic TEXT,
          confidence REAL, meta TEXT, updated_at TEXT);
        """
    )
    # trade 1: long BTCUSDT, entry 100 stop 90 exit 110, held 2h
    c.execute(
        "INSERT INTO trades VALUES "
        "(1,'vwap','BTCUSDT','closed','buy',10.0,0,'real_money',0,100,90,110,"
        "'2026-07-25T00:00:00Z','2026-07-25T02:00:00Z',NULL,NULL)"
    )
    c.execute(
        "INSERT INTO order_packages VALUES (10,1,?,0.8,?, '2026-07-25T00:00:00Z')",
        (json.dumps({"deviation_std": 1.5}), json.dumps({"killzone": "NY_AM"})),
    )
    # trade 2: short BTCUSDT — the fetcher will return NO candles for it
    c.execute(
        "INSERT INTO trades VALUES "
        "(2,'vwap','ETHUSDT','closed','sell',-5.0,0,'real_money',0,200,210,205,"
        "'2026-07-25T03:00:00Z','2026-07-25T04:00:00Z',NULL,NULL)"
    )
    c.commit()
    c.close()


def _fetcher_factory():
    """A synthetic fetcher: rich candles for BTCUSDT, nothing for ETHUSDT."""
    def fetch(symbol, timeframe, since_ms, limit):
        if symbol != "BTCUSDT":
            return None  # simulates an uncoverable window / non-CCXT venue
        # a path that runs to high 130 (3R) then settles; timestamps within window
        base = since_ms
        step = 900_000  # 15m
        return [
            {"timestamp": base + 0 * step, "high": 105, "low": 98, "close": 102},
            {"timestamp": base + 1 * step, "high": 130, "low": 108, "close": 120},
            {"timestamp": base + 2 * step, "high": 115, "low": 109, "close": 110},
        ]
    return fetch


def test_build_exit_panel_joins_excursions_and_features(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    rows, manifest = bx.build_exit_panel(
        db_path=db, cohort="real", timeframe="15m", fetcher=_fetcher_factory()
    )
    assert manifest["row_count"] == 2
    by_symbol = {r["symbol"]: r for r in rows}

    btc = by_symbol["BTCUSDT"]
    assert btc["excursion_present"] is True
    assert btc["mfe_r"] == 3.0  # high 130, entry 100, risk 10
    assert btc["realized_r"] == 1.0  # exit 110
    assert btc["giveback_r"] == 2.0  # 3R peak, kept 1R
    # decision-time features joined (incl. the P4 killzone cat)
    assert abs(btc["feat_vwap_deviation_std"] - 1.5) < 1e-9
    assert btc["cat_killzone"] == "ny_am"

    # ETH: fetcher returned nothing → honest coverage flag, null excursions
    eth = by_symbol["ETHUSDT"]
    assert eth["excursion_present"] is False
    assert eth["mfe_r"] is None and eth["giveback_r"] is None

    # coverage denominator is honest
    assert manifest["excursion_covered"] == 1
    assert manifest["excursion_coverage_pct"] == 50.0


def test_manifest_leakage_partition_and_outcome_cols(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    _, manifest = bx.build_exit_panel(db_path=db, fetcher=_fetcher_factory())
    # excursion outcome columns are stamped as outcomes, never features
    for col in ("mfe_r", "mae_r", "giveback_r", "capture_ratio"):
        assert col in manifest["outcome_cols"]
        assert col not in manifest["feature_cols"]
    assert all(
        c.startswith(("feat_", "cat_", "gate_")) for c in manifest["feature_cols"]
    )
    assert "leakage_contract" in manifest


def test_window_slice_bounds_to_holding_period(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)

    def fetch_with_out_of_window(symbol, timeframe, since_ms, limit):
        if symbol != "BTCUSDT":
            return None
        step = 900_000
        return [
            {"timestamp": since_ms + 0 * step, "high": 105, "low": 98},   # in window
            {"timestamp": since_ms + 1 * step, "high": 130, "low": 108},  # in window (MFE)
            # far-future bar with an even higher high — must be EXCLUDED by the slice
            {"timestamp": since_ms + 100 * step, "high": 999, "low": 500},
        ]

    rows, _ = bx.build_exit_panel(db_path=db, timeframe="15m", fetcher=fetch_with_out_of_window)
    btc = next(r for r in rows if r["symbol"] == "BTCUSDT")
    # the 999 bar is outside [entry, exit] (2h window) → MFE stays 3R, not ~9R
    assert btc["mfe_r"] == 3.0


def test_missing_db_is_empty_not_crash(tmp_path):
    rows, manifest = bx.build_exit_panel(db_path=str(tmp_path / "nope.db"))
    assert rows == []
    assert manifest["row_count"] == 0
    assert manifest["db_present"] is False


def test_epoch_ms_parsing():
    assert bx._to_epoch_ms("2026-07-25T00:00:00Z") == 1784937600000
    assert bx._to_epoch_ms(1784937600000) == 1784937600000  # already ms
    assert bx._to_epoch_ms(1784937600) == 1784937600000  # seconds → ms
    assert bx._to_epoch_ms(None) is None
    assert bx._to_epoch_ms("garbage") is None


def test_main_cli_runs(tmp_path, monkeypatch):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    # patch the default fetcher so the CLI path needs no network
    monkeypatch.setattr(bx, "_default_fetcher", lambda *a, **k: None)
    out = tmp_path / "exit_panel.jsonl"
    rc = bx.main(["--db", db, "--out", str(out), "--quiet"])
    assert rc == 0
    assert out.exists() and out.with_suffix(".jsonl.manifest.json").exists()


def test_limit_bounds_to_most_recent_trades(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    # 2 trades; trade 2 (ETHUSDT) closes later (04:00 > 02:00) → most-recent
    rows, manifest = bx.build_exit_panel(
        db_path=db, limit=1, fetcher=_fetcher_factory()
    )
    assert manifest["row_count"] == 1
    assert manifest["limit"] == 1
    assert rows[0]["symbol"] == "ETHUSDT"
