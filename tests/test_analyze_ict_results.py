"""Tests for bin/analyze_ict_results.py — ICT backtest result analyzer."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "analyze_ict_results",
    str(REPO_ROOT / "bin" / "analyze_ict_results.py"),
)
analyze = importlib.util.module_from_spec(_SPEC)
sys.modules["analyze_ict_results"] = analyze
_SPEC.loader.exec_module(analyze)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_pair(symbol, tf, *, ok=True, trades=10, winners=6, avg_r=0.8,
               total_return=5.0, max_dd=2.0, profit_factor=1.8, error=None):
    if not ok:
        return {"symbol": symbol, "timeframe": tf, "path": "x.csv",
                "ok": False, "summary": None, "error": error or "nope"}
    return {
        "symbol": symbol, "timeframe": tf, "path": "x.csv", "ok": True,
        "summary": {
            "total_trades": trades,
            "winners": winners,
            "losers": trades - winners,
            "win_rate_pct": round(winners / trades * 100, 1),
            "avg_r_multiple": avg_r,
            "total_pnl": round(total_return * 100, 2),
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd,
            "profit_factor": profit_factor,
        },
    }


def _make_report(pairs):
    return {"aggregate": {}, "pairs": pairs}


# ---------------------------------------------------------------------------
# _aggregate_from_pairs
# ---------------------------------------------------------------------------

def test_aggregate_empty():
    agg = analyze._aggregate_from_pairs([])
    assert agg["total_trades"] == 0
    assert agg["win_rate_pct"] == 0.0
    assert agg["avg_r_multiple"] == 0.0


def test_aggregate_single_pair():
    pairs = [_make_pair("BTCUSDT", "5m", trades=20, winners=12, avg_r=1.0,
                        total_return=8.0, max_dd=3.0)]
    agg = analyze._aggregate_from_pairs(pairs)
    assert agg["total_trades"] == 20
    assert agg["pairs_ok"] == 1
    assert agg["win_rate_pct"] == 60.0
    assert agg["avg_r_multiple"] == 1.0


def test_aggregate_multi_pair():
    pairs = [
        _make_pair("BTC", "5m", trades=30, winners=18, avg_r=0.9),
        _make_pair("ETH", "5m", trades=20, winners=11, avg_r=0.7),
        _make_pair("SPY", "5m", ok=False, error="file missing"),
    ]
    agg = analyze._aggregate_from_pairs(pairs)
    assert agg["total_trades"] == 50
    assert agg["pairs_ok"] == 2
    assert agg["pairs_failed"] == 1
    assert agg["win_rate_pct"] == pytest.approx((29 / 50) * 100, abs=0.2)
    assert agg["avg_r_multiple"] == pytest.approx(0.8, abs=0.01)


def test_aggregate_excludes_zero_trade_pair():
    pairs = [
        _make_pair("BTC", "5m", trades=40, winners=24, avg_r=1.0),
        # Simulate a pair that ran ok but produced no trades (backtester
        # returns {"error": "No trades executed"} with total_trades absent).
        {"symbol": "FLAT", "timeframe": "5m", "path": "x.csv", "ok": True,
         "summary": {"error": "No trades executed"}},
    ]
    agg = analyze._aggregate_from_pairs(pairs)
    assert agg["total_trades"] == 40


# ---------------------------------------------------------------------------
# go_verdict
# ---------------------------------------------------------------------------

def test_go_verdict_passes():
    agg = {"total_trades": 60, "win_rate_pct": 58.0, "avg_r_multiple": 0.7}
    go, fails = analyze.go_verdict(agg, min_trades=50, min_wr=55.0)
    assert go is True
    assert fails == []


def test_go_verdict_fails_insufficient_trades():
    agg = {"total_trades": 30, "win_rate_pct": 60.0, "avg_r_multiple": 0.8}
    go, fails = analyze.go_verdict(agg, min_trades=50, min_wr=55.0)
    assert go is False
    assert any("total_trades" in f for f in fails)


def test_go_verdict_fails_low_win_rate():
    agg = {"total_trades": 60, "win_rate_pct": 48.0, "avg_r_multiple": 0.5}
    go, fails = analyze.go_verdict(agg, min_trades=50, min_wr=55.0)
    assert go is False
    assert any("win_rate_pct" in f for f in fails)


def test_go_verdict_fails_negative_r():
    agg = {"total_trades": 60, "win_rate_pct": 58.0, "avg_r_multiple": -0.1}
    go, fails = analyze.go_verdict(agg, min_trades=50, min_wr=55.0)
    assert go is False
    assert any("avg_r_multiple" in f for f in fails)


def test_go_verdict_fails_multiple():
    agg = {"total_trades": 10, "win_rate_pct": 40.0, "avg_r_multiple": -0.5}
    go, fails = analyze.go_verdict(agg, min_trades=50, min_wr=55.0)
    assert go is False
    assert len(fails) == 3


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

def test_render_markdown_go_verdict_in_output():
    pairs = [_make_pair("BTC", "5m", trades=60, winners=36, avg_r=0.8)]
    report = _make_report(pairs)
    agg = analyze._aggregate_from_pairs(pairs)
    go, fails = analyze.go_verdict(agg, 50, 55.0)
    md = analyze.render_markdown(report, agg, go, fails, 50, 55.0, "test.json")
    assert "GO" in md
    assert "BTC" in md


def test_render_markdown_nogo_shows_failed_criteria():
    pairs = [_make_pair("BTC", "5m", trades=10, winners=5, avg_r=0.2)]
    report = _make_report(pairs)
    agg = analyze._aggregate_from_pairs(pairs)
    go, fails = analyze.go_verdict(agg, 50, 55.0)
    md = analyze.render_markdown(report, agg, go, fails, 50, 55.0, "test.json")
    assert "NO-GO" in md
    assert "total_trades" in md


def test_render_markdown_failed_pair_shown():
    pairs = [_make_pair("BAD", "5m", ok=False, error="data file not found")]
    report = _make_report(pairs)
    agg = analyze._aggregate_from_pairs(pairs)
    go, fails = analyze.go_verdict(agg, 50, 55.0)
    md = analyze.render_markdown(report, agg, go, fails, 50, 55.0, "test.json")
    assert "data file not found" in md


# ---------------------------------------------------------------------------
# main() — file I/O end-to-end
# ---------------------------------------------------------------------------

def test_main_writes_markdown_file(tmp_path: Path):
    pairs = [
        _make_pair("BTCUSDT", "5m", trades=30, winners=18, avg_r=0.9),
        _make_pair("ETHUSDT", "5m", trades=25, winners=15, avg_r=0.7),
    ]
    report_json = tmp_path / "report.json"
    report_json.write_text(json.dumps(_make_report(pairs)))
    out_md = tmp_path / "report.md"

    rc = analyze.main([
        "--input", str(report_json),
        "--output", str(out_md),
        "--min-trades", "50",
        "--min-wr", "55",
    ])
    assert rc == 0
    assert out_md.exists()
    content = out_md.read_text()
    assert "BTCUSDT" in content
    assert "ETHUSDT" in content


def test_main_exits_on_missing_input():
    with pytest.raises(SystemExit):
        analyze.main(["--input", "/nonexistent/report.json"])


def test_main_exits_on_bad_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{")
    with pytest.raises(SystemExit):
        analyze.main(["--input", str(bad)])
