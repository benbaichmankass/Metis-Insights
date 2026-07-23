"""M28 P1 — tests for the point-in-time valuation-snapshot store."""

from __future__ import annotations

from src.units.strategies.macro_thesis.valuation_store import (
    latest_reads_for_symbol,
    read_latest_snapshots,
    read_snapshot_records,
    write_snapshots,
)


def _row(symbol, metric, value, label, observed_at):
    return {"symbol": symbol, "metric": metric, "value": value,
            "label": label, "observed_at": observed_at, "as_of": "d"}


def test_write_then_read_records_newest_first(tmp_path):
    p = tmp_path / "snap.jsonl"
    n = write_snapshots([
        _row("TLT", "real_yield_10y", 2.0, "rich", "2026-07-23T00:00:00Z"),
        _row("credit_risk", "credit_spread", 3.0, "fair", "2026-07-23T00:00:00Z"),
    ], path=p)
    assert n == 2
    recs = read_snapshot_records(path=p)
    assert len(recs) == 2
    # newest-first = append order reversed; last appended is first out
    assert recs[0]["symbol"] == "credit_risk"


def test_append_only_is_point_in_time(tmp_path):
    p = tmp_path / "snap.jsonl"
    write_snapshots([_row("TLT", "real_yield_10y", 2.0, "rich", "2026-07-23T00:00:00Z")], path=p)
    # a revised value is a NEW line, not an overwrite
    write_snapshots([_row("TLT", "real_yield_10y", 2.4, "rich", "2026-07-23T06:00:00Z")], path=p)
    recs = read_snapshot_records(path=p)
    assert len(recs) == 2                       # both retained (history preserved)
    latest = read_latest_snapshots(path=p)
    # latest read = newest observed_at
    assert latest[("TLT", "real_yield_10y")]["value"] == 2.4


def test_read_latest_picks_newest_per_key(tmp_path):
    p = tmp_path / "snap.jsonl"
    write_snapshots([
        _row("TLT", "real_yield_10y", 2.0, "rich", "2026-07-23T00:00:00Z"),
        _row("GLD", "real_yield_10y", 2.0, "rich", "2026-07-23T00:00:00Z"),
        _row("TLT", "real_yield_10y", 2.5, "rich", "2026-07-23T12:00:00Z"),  # newer TLT
    ], path=p)
    latest = read_latest_snapshots(path=p)
    assert latest[("TLT", "real_yield_10y")]["value"] == 2.5
    assert latest[("GLD", "real_yield_10y")]["value"] == 2.0
    assert len(latest) == 2                      # two distinct keys


def test_latest_reads_for_symbol(tmp_path):
    p = tmp_path / "snap.jsonl"
    write_snapshots([
        _row("GLD", "real_yield_10y", 2.0, "rich", "t1"),
        _row("GLD", "gold_silver_ratio", 80.0, "cheap", "t1"),
        _row("SPY", "equity_risk_premium", 0.03, "fair", "t1"),
    ], path=p)
    gld = latest_reads_for_symbol("GLD", path=p)
    assert len(gld) == 2
    assert {r["metric"] for r in gld} == {"real_yield_10y", "gold_silver_ratio"}


def test_read_missing_file_is_empty(tmp_path):
    assert read_snapshot_records(path=tmp_path / "nope.jsonl") == []
    assert read_latest_snapshots(path=tmp_path / "nope.jsonl") == {}


def test_read_skips_bad_lines(tmp_path):
    p = tmp_path / "snap.jsonl"
    p.write_text('{"symbol":"TLT","metric":"m","observed_at":"t"}\nNOT JSON\n\n')
    recs = read_snapshot_records(path=p)
    assert len(recs) == 1


def test_read_records_limit(tmp_path):
    p = tmp_path / "snap.jsonl"
    write_snapshots([_row("S", "m", float(i), "fair", f"t{i:02d}") for i in range(5)], path=p)
    assert len(read_snapshot_records(path=p, limit=2)) == 2


def test_rows_missing_symbol_or_metric_ignored_by_latest(tmp_path):
    p = tmp_path / "snap.jsonl"
    write_snapshots([{"value": 1.0, "observed_at": "t"}, _row("TLT", "m", 2.0, "rich", "t")], path=p)
    latest = read_latest_snapshots(path=p)
    assert list(latest.keys()) == [("TLT", "m")]
