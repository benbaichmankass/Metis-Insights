"""Tests for the setup_candidates dataset family (S-MLOPT-S5)."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.datasets.families.setup_candidates import SetupCandidatesBuilder
from ml.datasets.registry import get_builder, list_families


def _seed_trades(db: Path, entries: list[tuple[str, str, float]]) -> None:
    """entries = [(iso_ts, direction, pnl), ...] of REAL closed BTCUSDT trades."""
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, "
        "timestamp TEXT, status TEXT, pnl REAL, pnl_percent REAL, "
        "is_backtest INT, is_demo INT)"
    )
    for i, (ts, direction, pnl) in enumerate(entries):
        conn.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)",
            (i, "BTCUSDT", direction, ts, "closed", pnl, pnl / 100.0, 0, 0),
        )
    conn.commit()
    conn.close()


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


def test_real_trade_rows_appended(tmp_path: Path):
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Two REAL closed trades whose entry ts fall inside the bar range.
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [
        ((base + timedelta(hours=50)).isoformat(), "buy", 12.5),   # win, long
        ((base + timedelta(hours=120)).isoformat(), "sell", -8.0),  # loss, short
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db,
    ))
    live = [r for r in rows if r["is_live_trade"]]
    synth = [r for r in rows if not r["is_live_trade"]]
    assert len(synth) >= 5  # synthetic candidates still produced
    assert len(live) == 2   # both real trades located + emitted
    for r in live:
        assert r["barrier_touched"] == "live"
        assert r["signal_vol"] > 0       # real rows carry past-only features
        assert r["vol_bucket"].startswith("vol_b")
    won_row = next(r for r in live if r["direction"] == 1)
    lost_row = next(r for r in live if r["direction"] == -1)
    assert won_row["won"] == 1 and won_row["label"] == 1
    assert lost_row["won"] == 0 and lost_row["label"] == -1


def test_include_synthetic_false_emits_only_live(tmp_path: Path):
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [((base + timedelta(hours=60)).isoformat(), "buy", 5.0)])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        include_synthetic=False, live_trades_db=db,
    ))
    assert rows and all(r["is_live_trade"] for r in rows)


# -----------------------------------------------------------------------------
# Signal-log event source (MB-20260603-002, S-MLOPT-S6 follow-up)
# -----------------------------------------------------------------------------

def _seed_signals(
    db: Path,
    entries: list[tuple[str, str, str, str]],
) -> None:
    """entries = [(iso_logged_at_utc, strategy, symbol, side), ...].

    Mirrors `src.units.db.database` schema (signals: logged_at_utc, strategy,
    symbol, side, qty, status, reason, meta) — only the columns the
    setup_candidates reader needs.
    """
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS signals ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, logged_at_utc TEXT NOT NULL, "
        "strategy TEXT, symbol TEXT, side TEXT, qty REAL, status TEXT, "
        "reason TEXT, meta TEXT)"
    )
    for ts, strategy, symbol, side in entries:
        conn.execute(
            "INSERT INTO signals (logged_at_utc, strategy, symbol, side) "
            "VALUES (?, ?, ?, ?)",
            (ts, strategy, symbol, side),
        )
    conn.commit()
    conn.close()


def test_signal_log_rows_sampled_and_labeled(tmp_path: Path):
    """Each buy/sell audit row produces one signal-log candidate, triple-barrier
    labeled at the bar covering its logged ts."""
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_signals(db, [
        ((base + timedelta(hours=40)).isoformat(), "ict_scalp", "BTCUSDT", "buy"),
        ((base + timedelta(hours=80)).isoformat(), "ict_scalp", "BTCUSDT", "sell"),
        ((base + timedelta(hours=120)).isoformat(), "vwap", "BTCUSDT", "none"),  # dropped
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, signal_log_db=db,
    ))
    sig = [r for r in rows if r["event_source"] == "signal_log"]
    cusum = [r for r in rows if r["event_source"] == "cusum"]
    assert len(cusum) >= 5  # CUSUM candidates still produced
    assert len(sig) == 2    # only the two buy/sell rows; "none" is skipped
    dirs = sorted(r["direction"] for r in sig)
    assert dirs == [-1, 1]
    for r in sig:
        # Triple-barrier label, NOT 'live' — signal-log rows carry synthetic
        # outcomes that ride the train side of live_holdout.
        assert r["barrier_touched"] in {"tp", "sl", "timeout"}
        assert r["is_live_trade"] is False
        assert r["entry_price"] > 0  # next-bar-open entry (no look-ahead)
        assert r["signal_vol"] > 0   # past-only feature window


def test_signal_log_strategy_filter(tmp_path: Path):
    """`signal_log_strategies` restricts to a subset of strategies."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_signals(db, [
        ((base + timedelta(hours=30)).isoformat(), "ict_scalp", "BTCUSDT", "buy"),
        ((base + timedelta(hours=60)).isoformat(), "vwap", "BTCUSDT", "buy"),
        ((base + timedelta(hours=90)).isoformat(), "turtle_soup", "BTCUSDT", "sell"),
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, signal_log_db=db,
        signal_log_strategies=("ict_scalp", "vwap"),
    ))
    sig = [r for r in rows if r["event_source"] == "signal_log"]
    assert len(sig) == 2  # only ict_scalp + vwap; turtle_soup filtered out


def test_signal_log_filters_by_symbol(tmp_path: Path):
    """Signals for OTHER symbols are not located on this market_raw dataset."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_signals(db, [
        ((base + timedelta(hours=40)).isoformat(), "ict_scalp", "BTCUSDT", "buy"),
        ((base + timedelta(hours=50)).isoformat(), "mes_trend_long_1d", "MES", "buy"),
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, signal_log_db=db,
    ))
    sig = [r for r in rows if r["event_source"] == "signal_log"]
    assert len(sig) == 1  # MES row not matched to BTCUSDT market_raw


def test_signal_log_missing_db_is_noop(tmp_path: Path):
    """A non-existent signal_log_db doesn't crash — just emits no signal_log rows."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
        signal_log_db=tmp_path / "does-not-exist.db",
    ))
    assert rows  # CUSUM rows still emitted
    assert all(r["event_source"] == "cusum" for r in rows)


def test_signal_log_no_signals_table_is_noop(tmp_path: Path):
    """Empty / signals-table-absent DB: best-effort -> no signal_log rows."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    db = tmp_path / "trade_journal.db"
    sqlite3.connect(str(db)).close()  # empty DB, no signals table
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, signal_log_db=db,
    ))
    assert all(r["event_source"] == "cusum" for r in rows)


def test_signal_log_and_cusum_disable(tmp_path: Path):
    """`include_cusum=False` + `signal_log_db` set => only signal_log rows."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_signals(db, [
        ((base + timedelta(hours=h)).isoformat(), "ict_scalp", "BTCUSDT", "buy")
        for h in (20, 40, 60, 80, 100)
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, signal_log_db=db, include_cusum=False,
    ))
    assert rows
    assert all(r["event_source"] == "signal_log" for r in rows)


def test_signal_log_three_source_mix(tmp_path: Path):
    """All three samplers compose: CUSUM + signal_log + live_trades_db."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [
        ((base + timedelta(hours=30)).isoformat(), "buy", 9.0),
    ])
    _seed_signals(db, [
        ((base + timedelta(hours=60)).isoformat(), "ict_scalp", "BTCUSDT", "buy"),
        ((base + timedelta(hours=90)).isoformat(), "ict_scalp", "BTCUSDT", "sell"),
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
        live_trades_db=db, signal_log_db=db,
    ))
    sources = {r["event_source"] for r in rows}
    assert sources == {"cusum", "signal_log", "live"}
    live = [r for r in rows if r["event_source"] == "live"]
    sig = [r for r in rows if r["event_source"] == "signal_log"]
    assert len(live) == 1
    assert len(sig) == 2
    # live_holdout discipline: live rows are eval-side, signal_log rows ride
    # train-side alongside cusum.
    assert all(r["is_live_trade"] for r in live)
    assert not any(r["is_live_trade"] for r in sig)


def test_signal_log_strategies_string_form(tmp_path: Path):
    """The build CLI passes family args as `key=a,b`; comma strings work too."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_signals(db, [
        ((base + timedelta(hours=40)).isoformat(), "ict_scalp", "BTCUSDT", "buy"),
        ((base + timedelta(hours=80)).isoformat(), "vwap", "BTCUSDT", "buy"),
        ((base + timedelta(hours=120)).isoformat(), "turtle_soup", "BTCUSDT", "sell"),
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, signal_log_db=db,
        signal_log_strategies="ict_scalp,turtle_soup",
    ))
    # Strategy name isn't preserved on the emitted row (feature space stays
    # fixed); the count proves the comma-string filter applied — ict_scalp +
    # turtle_soup land, vwap is filtered out.
    assert sum(1 for r in rows if r["event_source"] == "signal_log") == 2
