"""Tests for the A1 backtest-augment runner (WORK-PLAN-2026-08-02 W1.2).

The runner is a composition: `emit_trades_for` (fetch + config-exact harness
`--emit-trades`, tested in the regime-debt-matrix suite) → `record_harness_trades`
→ `write_backtest_trades` (is_backtest=1 rows). These tests monkeypatch the
harness-fetch half (which needs pandas/numpy + network) and verify the runner's
own contract: the roster is the pinned pooled 3×3, only `is_backtest=1` rows are
written, a per-symbol replay records under the right (strategy, symbol), an emit
failure is NAMED not swallowed, and the SUMMARY states the population.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import scripts.ml.backtest_augment_runner as runner


def test_default_roster_matches_pinned_manifest():
    # Verified against ml/configs/setup-candidates-metalabel-p2pool-v1.yaml.
    assert runner.DEFAULT_ROSTER == (
        "trend_donchian", "squeeze_breakout_4h", "htf_pullback_trend_2h")
    assert runner.DEFAULT_SYMBOLS == ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _fake_emit_factory(tmp_path: Path, *, n_per_leg=3):
    """Return an emit_trades_for stub that writes a small emit JSONL per leg."""
    def _fake_emit(name, cfg, workdir, days, *, symbol_override=None, fee_override=None):
        sym = symbol_override or (cfg.get("symbols") or ["BTCUSDT"])[0]
        emit = Path(workdir) / f"{name}__{sym}__trades.jsonl"
        emit.parent.mkdir(parents=True, exist_ok=True)
        with emit.open("w") as fh:
            for i in range(n_per_leg):
                fh.write(json.dumps({
                    "strategy": name, "symbol": sym,
                    "entry_time": f"2026-0{(i % 9) + 1}-01T00:00:00+00:00",
                    "direction": "long" if i % 2 == 0 else "short",
                    "gross_r": 1.0, "net_r": 0.5 if i % 2 == 0 else -0.4,
                    "confidence": 0.4,
                }) + "\n")
        return {
            "strategy": name, "symbol": sym, "timeframe": cfg.get("timeframe"),
            "harness": "trend", "feed": {"source": "binance"},
            "fidelity": "faithful", "omitted_levers": [],
            "fee_bps_roundtrip": 7.5, "emit_path": str(emit), "n_emitted": n_per_leg,
        }
    return _fake_emit


def _fake_resolve(name):
    tf = {"trend_donchian": "1h", "squeeze_breakout_4h": "4h",
          "htf_pullback_trend_2h": "2h"}.get(name, "1h")
    return {"symbols": ["BTCUSDT"], "timeframe": tf, "donchian": 20}


def test_runner_records_only_is_backtest_rows(tmp_path, monkeypatch):
    # emit_trades_for/resolve_strategy are imported INSIDE run() from rdm at call
    # time, so patching the source module is what takes effect.
    import scripts.research.regime_debt_matrix as rdm
    monkeypatch.setattr(rdm, "emit_trades_for", _fake_emit_factory(tmp_path))
    monkeypatch.setattr(rdm, "resolve_strategy", _fake_resolve)

    db = tmp_path / "backtest_trades.db"
    rc = runner.main([
        "--db", str(db), "--workdir", str(tmp_path / "work"),
        "--out-dir", str(tmp_path / "out"), "--days", "30",
        "--run-tag", "a1-test", "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT",
    ])
    assert rc == 0
    conn = sqlite3.connect(str(db))
    # 3 strategies × 3 symbols × 3 rows = 27, ALL is_backtest=1.
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 27
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE is_backtest=1").fetchone()[0] == 27
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE is_backtest=0").fetchone()[0] == 0
    # Every pooled (strategy, symbol) leg is present.
    got = set(conn.execute("SELECT DISTINCT strategy_name, symbol FROM trades").fetchall())
    assert ("trend_donchian", "SOLUSDT") in got
    assert ("htf_pullback_trend_2h", "ETHUSDT") in got
    assert len(got) == 9

    result = json.loads((tmp_path / "out" / "backtest_augment_result.json").read_text())
    assert result["total_recorded_is_backtest_rows"] == 27
    assert result["legs_ok"] == 9 and result["legs_failed"] == 0
    assert result["run_tag"] == "a1-test"
    summary = (tmp_path / "out" / "SUMMARY.md").read_text()
    assert "is_backtest=1" in summary and "trend_donchian" in summary


def test_emit_failure_is_named_not_swallowed(tmp_path, monkeypatch):
    def _emit_one_fails(name, cfg, workdir, days, *, symbol_override=None, fee_override=None):
        if symbol_override == "ETHUSDT":
            return {"strategy": name, "symbol": symbol_override,
                    "timeframe": cfg.get("timeframe"), "harness": "trend",
                    "emit_path": None, "n_emitted": 0,
                    "error": "fetch failed: RuntimeError: no rows"}
        return _fake_emit_factory(tmp_path)(name, cfg, workdir, days,
                                            symbol_override=symbol_override)
    import scripts.research.regime_debt_matrix as rdm
    monkeypatch.setattr(rdm, "emit_trades_for", _emit_one_fails)
    monkeypatch.setattr(rdm, "resolve_strategy", _fake_resolve)

    db = tmp_path / "bt.db"
    rc = runner.main([
        "--db", str(db), "--workdir", str(tmp_path / "w"),
        "--out-dir", str(tmp_path / "o"), "--only", "trend_donchian",
        "--symbols", "BTCUSDT,ETHUSDT", "--days", "10",
    ])
    assert rc == 0
    result = json.loads((tmp_path / "o" / "backtest_augment_result.json").read_text())
    assert result["legs_failed"] == 1 and result["legs_ok"] == 1
    failed = [leg for leg in result["legs"] if leg.get("error")]
    assert len(failed) == 1 and failed[0]["symbol"] == "ETHUSDT"
    assert "fetch failed" in failed[0]["error"]
    # The failing leg did not poison the DB: the good BTC leg's rows are present.
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE symbol='BTCUSDT'").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE symbol='ETHUSDT'").fetchone()[0] == 0


def test_unknown_strategy_named_skip(tmp_path, monkeypatch):
    import scripts.research.regime_debt_matrix as rdm
    monkeypatch.setattr(rdm, "emit_trades_for", _fake_emit_factory(tmp_path))
    monkeypatch.setattr(rdm, "resolve_strategy", lambda name: None)

    db = tmp_path / "bt.db"
    rc = runner.main(["--db", str(db), "--workdir", str(tmp_path / "w"),
                      "--out-dir", str(tmp_path / "o"), "--only", "not_a_strategy"])
    assert rc == 0
    result = json.loads((tmp_path / "o" / "backtest_augment_result.json").read_text())
    assert result["legs_failed"] == 1
    assert "not declared in strategies.yaml" in result["legs"][0]["error"]
