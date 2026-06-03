"""Tests for the setup_candidates dataset family (S-MLOPT-S5)."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.datasets.families.setup_candidates import SetupCandidatesBuilder
from ml.datasets.registry import get_builder, list_families


def _write_market_raw(root: Path, closes: list[float]) -> Path:
    """Write a minimal market_raw dataset dir with one bar per close.

    Highs/lows are widened ±0.4% around close so triple-barrier touches are
    reachable; opens carry the prior close forward (gap-free).
    """
    ddir = root / "market_raw" / "BTCUSDT" / "1h" / "v1"
    ddir.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with (ddir / "data.jsonl").open("w", encoding="utf-8") as fh:
        prev = closes[0]
        for i, c in enumerate(closes):
            row = {
                "ts": (base + timedelta(hours=i)).isoformat(),
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "open": float(prev),
                "high": float(max(prev, c) * 1.004),
                "low": float(min(prev, c) * 0.996),
                "close": float(c),
                "volume": 1.0,
                "source": "test",
            }
            fh.write(json.dumps(row) + "\n")
            prev = c
    return ddir


def _trending_closes(n: int = 200) -> list[float]:
    # Alternating up/down runs so the CUSUM filter fires both sides.
    closes = [100.0]
    for i in range(1, n):
        drift = 0.004 if (i // 12) % 2 == 0 else -0.004
        closes.append(closes[-1] * (1 + drift))
    return closes


def test_family_registered():
    assert "setup_candidates" in list_families()
    assert isinstance(get_builder("setup_candidates"), SetupCandidatesBuilder)


def test_builds_labeled_rows(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, _trending_closes())
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
    ))
    assert len(rows) >= 5, f"expected several candidates, got {len(rows)}"
    # Both long and short candidates were sampled.
    dirs = {r["direction"] for r in rows}
    assert dirs <= {1, -1}
    assert 1 in dirs and -1 in dirs
    for r in rows:
        assert r["barrier_touched"] in {"tp", "sl", "timeout"}
        assert r["label"] in {-1, 0, 1}
        assert r["won"] == (1 if r["label"] > 0 else 0)
        assert r["is_live_trade"] is False
        assert r["entry_price"] > 0
        assert r["signal_vol"] > 0


def test_build_writes_valid_schema(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, _trending_closes())
    out = tmp_path / "datasets-out"
    paths = SetupCandidatesBuilder().build(
        output_dir=out, version="v1", source="test",
        symbol_scope="BTCUSDT", timeframe="1h",
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
    )
    # The builder validated every row against the schema (would raise otherwise).
    meta = json.loads(paths.metadata.read_text())
    assert meta["family"] == "setup_candidates"
    assert meta["leakage_test_status"] == "passed"
    assert meta["row_count"] >= 5
    assert meta["label_version"] == "triple-barrier-v1"


def test_no_lookahead_entry_is_next_bar(tmp_path: Path):
    # entry_price must equal the OPEN of the bar AFTER the signal bar — never
    # the signal bar's own close (that would be look-ahead).
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    raw = [json.loads(ln) for ln in (ddir / "data.jsonl").read_text().splitlines()]
    by_ts = {r["ts"]: r for r in raw}
    ordered_ts = [r["ts"] for r in raw]
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
    ))
    for r in rows:
        sig_pos = ordered_ts.index(r["ts"])
        next_ts = ordered_ts[sig_pos + 1]
        assert math.isclose(r["entry_price"], by_ts[next_ts]["open"]), (
            "entry must be the post-signal bar open (no signal-bar look-ahead)"
        )


def test_empty_when_too_few_bars(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, [100.0, 101.0, 102.0])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
    ))
    assert rows == []
