"""
Offline tests for ``bin/backtest_ict.py`` — multi-symbol/timeframe ICT
backtest CLI scaffold.

These tests cover only the CLI plumbing (manifest loading, pair parsing,
result aggregation, and a single end-to-end synthetic run). They do **not**
validate ICT strategy edge — that lives in the existing backtester tests
and the Colab research notebooks.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import the CLI module via a stable path. The script lives in bin/ and is
# importable by file path.
import importlib.util

_CLI_SPEC = importlib.util.spec_from_file_location(
    "backtest_ict_cli",
    str(REPO_ROOT / "bin" / "backtest_ict.py"),
)
backtest_ict_cli = importlib.util.module_from_spec(_CLI_SPEC)
# Register before exec so @dataclass can resolve forward references via
# sys.modules[cls.__module__] under `from __future__ import annotations`.
sys.modules["backtest_ict_cli"] = backtest_ict_cli
_CLI_SPEC.loader.exec_module(backtest_ict_cli)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Pair / manifest parsing
# ---------------------------------------------------------------------------


def test_parse_pair_arg_happy():
    p = backtest_ict_cli.parse_pair_arg("BTCUSDT:5m:data/btc_5m.csv")
    assert p.symbol == "BTCUSDT"
    assert p.timeframe == "5m"
    assert p.path == "data/btc_5m.csv"


@pytest.mark.parametrize("bad", [
    "BTCUSDT:5m",                # missing path
    "BTCUSDT::data/btc_5m.csv",  # blank timeframe
    ":5m:data/btc.csv",          # blank symbol
    "BTCUSDT:5m:",               # blank path
    "no-colons-at-all",
])
def test_parse_pair_arg_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        backtest_ict_cli.parse_pair_arg(bad)


def test_load_manifest_reads_columns(tmp_path: Path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "symbol,timeframe,path\n"
        "BTCUSDT,5m,data/btc.csv\n"
        "ETHUSDT,15m,data/eth.csv\n"
    )
    pairs = backtest_ict_cli.load_manifest(manifest)
    assert [p.symbol for p in pairs] == ["BTCUSDT", "ETHUSDT"]
    assert [p.timeframe for p in pairs] == ["5m", "15m"]
    assert [p.path for p in pairs] == ["data/btc.csv", "data/eth.csv"]


def test_load_manifest_missing_column_raises(tmp_path: Path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("symbol,path\nBTCUSDT,data/btc.csv\n")
    with pytest.raises(ValueError, match="missing columns"):
        backtest_ict_cli.load_manifest(manifest)


# ---------------------------------------------------------------------------
# Aggregate / reporting
# ---------------------------------------------------------------------------


def test_aggregate_handles_empty():
    agg = backtest_ict_cli.aggregate([])
    assert agg == {
        "pairs_total": 0,
        "pairs_ok": 0,
        "pairs_failed": 0,
        "trades_total": 0,
        "winners_total": 0,
        "win_rate_pct": 0.0,
    }


def test_aggregate_combines_pair_summaries():
    PR = backtest_ict_cli.PairResult
    results = [
        PR("BTCUSDT", "5m", "x.csv", True,
           summary={"total_trades": 4, "winners": 3}),
        PR("ETHUSDT", "5m", "y.csv", True,
           summary={"total_trades": 6, "winners": 3}),
        PR("SOLUSDT", "5m", "z.csv", False, error="nope"),
    ]
    agg = backtest_ict_cli.aggregate(results)
    assert agg["pairs_total"] == 3
    assert agg["pairs_ok"] == 2
    assert agg["pairs_failed"] == 1
    assert agg["trades_total"] == 10
    assert agg["winners_total"] == 6
    assert agg["win_rate_pct"] == 60.0


# ---------------------------------------------------------------------------
# run_pair behaviour on missing / malformed data
# ---------------------------------------------------------------------------


def test_run_pair_missing_file_returns_failure(tmp_path: Path):
    pair = backtest_ict_cli.Pair("BTCUSDT", "5m", str(tmp_path / "nope.csv"))
    result = backtest_ict_cli.run_pair(pair)
    assert result.ok is False
    assert result.summary is None
    assert "data" in (result.error or "").lower()


def test_run_pair_missing_columns_returns_failure(tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text("timestamp,close\n2026-01-01T00:00:00,100\n")
    pair = backtest_ict_cli.Pair("BTCUSDT", "5m", str(bad))
    result = backtest_ict_cli.run_pair(pair)
    assert result.ok is False
    assert "missing OHLCV columns" in (result.error or "")


# ---------------------------------------------------------------------------
# End-to-end: synthetic flat-market CSV → backtester runs cleanly with 0 trades
# ---------------------------------------------------------------------------


def _make_flat_csv(path: Path, n: int = 200) -> None:
    """Write a synthetic OHLCV file with no FVGs (flat market). The
    backtester should run end-to-end and simply produce zero trades."""
    ts = pd.date_range("2026-01-01", periods=n, freq="5min")
    rows = pd.DataFrame({
        "timestamp": ts,
        "open":   [100.0] * n,
        "high":   [100.5] * n,
        "low":    [99.5]  * n,
        "close":  [100.0] * n,
        "volume": [10.0]  * n,
    })
    rows.to_csv(path, index=False)


def test_run_pair_end_to_end_no_trades(tmp_path: Path):
    csv = tmp_path / "flat.csv"
    _make_flat_csv(csv)
    pair = backtest_ict_cli.Pair("FLAT", "5m", str(csv))
    result = backtest_ict_cli.run_pair(pair)

    # The backtester returns {"error": "No trades executed"} when no trades
    # fired. That's a successful run from the CLI's perspective — the
    # scaffolding worked; it just produced an empty book.
    assert result.ok is True
    assert result.summary is not None
    assert result.summary.get("error") == "No trades executed" or \
           int(result.summary.get("total_trades", 0)) == 0


def test_main_writes_output_file(tmp_path: Path, capsys):
    csv = tmp_path / "flat.csv"
    _make_flat_csv(csv)
    out = tmp_path / "report.json"

    rc = backtest_ict_cli.main([
        "--pair", f"FLAT:5m:{csv}",
        "--output", str(out),
        "--quiet",
    ])
    assert rc == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["aggregate"]["pairs_total"] == 1
    assert payload["aggregate"]["pairs_ok"] == 1


# ---------------------------------------------------------------------------
# Validate data/ict_validate_manifest.csv is well-formed
# ---------------------------------------------------------------------------


def test_ict_validate_manifest_exists_and_loads():
    manifest = REPO_ROOT / "data" / "ict_validate_manifest.csv"
    assert manifest.exists(), "data/ict_validate_manifest.csv not found"
    pairs = backtest_ict_cli.load_manifest(manifest)
    assert len(pairs) == 4
    symbols = [p.symbol for p in pairs]
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols
    assert "SPY" in symbols
    assert "QQQ" in symbols


def test_ict_validate_manifest_timeframes():
    manifest = REPO_ROOT / "data" / "ict_validate_manifest.csv"
    pairs = backtest_ict_cli.load_manifest(manifest)
    by_symbol = {p.symbol: p.timeframe for p in pairs}
    assert by_symbol["BTCUSDT"] == "5m"
    assert by_symbol["ETHUSDT"] == "5m"
    assert by_symbol["SPY"] == "5m"
    assert by_symbol["QQQ"] == "15m"


def test_ict_validate_manifest_runs_all_pairs():
    """End-to-end: run the validate manifest against the placeholder OHLCV files."""
    manifest = REPO_ROOT / "data" / "ict_validate_manifest.csv"
    pairs = backtest_ict_cli.load_manifest(manifest)
    results = backtest_ict_cli.run_all(pairs)
    assert len(results) == 4
    # All placeholder CSVs exist and are valid OHLCV files.
    failed = [(r.symbol, r.error) for r in results if not r.ok]
    assert failed == [], f"Some pairs failed: {failed}"
