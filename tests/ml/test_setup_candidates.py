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


def test_range_vol_features_emitted(tmp_path: Path):
    # builder v2 (S-MLOPT-S8 follow-up): every row carries the four range-based
    # vol estimators, past-only over the same window as rolling_log_return_vol.
    ddir = _write_market_raw(tmp_path, _trending_closes())
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
    ))
    assert rows
    range_vol_cols = (
        "parkinson_vol", "garman_klass_vol", "rogers_satchell_vol", "yang_zhang_vol",
    )
    for r in rows:
        for col in range_vol_cols:
            assert col in r, f"missing {col}"
            assert isinstance(r[col], float)
            assert r[col] >= 0.0  # emitted as a stdev (sqrt of a variance) or 0.0
        # The widened ±0.4% high/low fixture gives a non-zero intrabar range,
        # so at least the high-low Parkinson estimator must be strictly positive.
        assert r["parkinson_vol"] > 0.0


def test_range_vol_features_on_live_rows(tmp_path: Path):
    # Real-trade rows live in the SAME feature space as synthetic rows, incl. the
    # range-vol estimators (the live_holdout train/eval space must match).
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_trades(db, [((base + timedelta(hours=50)).isoformat(), "buy", 12.5)])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db,
    ))
    live = [r for r in rows if r["is_live_trade"]]
    assert len(live) == 1
    assert live[0]["yang_zhang_vol"] >= 0.0
    assert live[0]["parkinson_vol"] > 0.0


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


def _seed_trades_with_risk(
    db: Path,
    entries: list[tuple[str, str, float, float | None, float | None, float | None]],
) -> None:
    """entries = [(iso_ts, direction, pnl, entry_price, stop_loss, size), ...].

    Seeds a trades table carrying the risk columns the live-row realized-R
    reconstruction reads (`MB-20260717-M23-LIVEROW-REALIZED-R`). A `None`
    stop_loss/entry/size models a journal row the writer left un-populated.
    """
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, "
        "timestamp TEXT, status TEXT, pnl REAL, pnl_percent REAL, "
        "entry_price REAL, stop_loss REAL, position_size REAL, "
        "is_backtest INT, is_demo INT)"
    )
    for i, (ts, direction, pnl, entry, stop, size) in enumerate(entries):
        conn.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, "BTCUSDT", direction, ts, "closed", pnl, pnl / 100.0,
             entry, stop, size, 0, 0),
        )
    conn.commit()
    conn.close()


def test_live_rows_carry_reconstructed_realized_r(tmp_path: Path):
    """A live row's r_multiple is the real net R (pnl / |entry-stop|*size), not
    the old 0.0 placeholder — so the EV gate is exact (MB-20260717-M23-LIVEROW-REALIZED-R)."""
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    # Long win: entry 100, stop 98 -> risk/unit 2; size 3 -> $6 risk; pnl +$12 -> +2.0R.
    # Short loss: entry 100, stop 101 -> risk/unit 1; size 5 -> $5 risk; pnl -$5 -> -1.0R.
    _seed_trades_with_risk(db, [
        ((base + timedelta(hours=50)).isoformat(), "buy", 12.0, 100.0, 98.0, 3.0),
        ((base + timedelta(hours=120)).isoformat(), "sell", -5.0, 100.0, 101.0, 5.0),
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db,
    ))
    live = [r for r in rows if r["is_live_trade"]]
    assert len(live) == 2
    won_row = next(r for r in live if r["direction"] == 1)
    lost_row = next(r for r in live if r["direction"] == -1)
    assert math.isclose(won_row["r_multiple"], 2.0, rel_tol=1e-9)
    assert math.isclose(lost_row["r_multiple"], -1.0, rel_tol=1e-9)
    assert won_row["r_multiple_source"] == "stop_distance"
    assert lost_row["r_multiple_source"] == "stop_distance"


def test_live_row_r_falls_back_to_unit_when_risk_absent(tmp_path: Path):
    """Missing stop/size (old-schema journal or an un-stopped row) degrades to a
    signed unit-R, never a silent 0.0 the EV scorer would read as a real 0R."""
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # (a) Old schema entirely (no risk columns): _seed_trades -> unit fallback.
    db_old = tmp_path / "old.db"
    _seed_trades(db_old, [((base + timedelta(hours=50)).isoformat(), "buy", 7.0)])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db_old,
    ))
    live = [r for r in rows if r["is_live_trade"]]
    assert len(live) == 1
    assert live[0]["r_multiple"] == 1.0  # won -> +1 coarse
    assert live[0]["r_multiple_source"] == "unit_fallback"
    # (b) Columns present but NULL stop on a losing trade -> -1 fallback.
    db_null = tmp_path / "null.db"
    _seed_trades_with_risk(db_null, [
        ((base + timedelta(hours=50)).isoformat(), "sell", -4.0, 100.0, None, 2.0),
    ])
    rows2 = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db_null,
    ))
    live2 = [r for r in rows2 if r["is_live_trade"]]
    assert len(live2) == 1
    assert live2[0]["r_multiple"] == -1.0
    assert live2[0]["r_multiple_source"] == "unit_fallback"


def test_r_label_threshold_emits_r_aware_target(tmp_path: Path):
    """M23 variant C1: r_label_threshold adds won_r = 1[r_multiple >= tau] on every
    row, without disturbing the binary won (pnl>0) reporting target."""
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    # Long +2.0R (clears tau=0.5), long +0.2R win (below tau), short -1.0R loss.
    _seed_trades_with_risk(db, [
        ((base + timedelta(hours=40)).isoformat(), "buy", 12.0, 100.0, 98.0, 3.0),   # R=+2.0
        ((base + timedelta(hours=80)).isoformat(), "buy", 0.6, 100.0, 98.0, 1.0),    # R=+0.3
        ((base + timedelta(hours=120)).isoformat(), "sell", -5.0, 100.0, 101.0, 5.0),# R=-1.0
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db, r_label_threshold=0.5,
    ))
    # Every emitted row (synthetic + live) carries won_r consistent with its R.
    for r in rows:
        assert r["won_r"] == (1 if r["r_multiple"] >= 0.5 else 0)
    live = [r for r in rows if r["is_live_trade"]]
    assert len(live) == 3
    big = next(r for r in live if abs(r["r_multiple"] - 2.0) < 1e-9)
    small = next(r for r in live if abs(r["r_multiple"] - 0.3) < 1e-9)
    loss = next(r for r in live if abs(r["r_multiple"] + 1.0) < 1e-9)
    assert big["won_r"] == 1 and big["won"] == 1        # big winner clears tau
    assert small["won_r"] == 0 and small["won"] == 1    # small winner: won but not won_r
    assert loss["won_r"] == 0 and loss["won"] == 0


def test_backtest_r_haircut_only_affects_backtest_won_r(tmp_path: Path):
    """M23 variant C3: backtest_r_haircut lowers a backtest row's R before the
    won_r threshold (train-side faithfulness), but never touches a live row's."""
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    # A backtest trade at R=+0.8 (pnl stores R for backtest rows) and a live trade
    # at real R=+0.8 (entry100/stop98/size1.5 -> risk 3 -> pnl 2.4 -> R=0.8).
    _seed_journal(db, backtest=[
        ((base + timedelta(hours=50)).isoformat(), "buy", 0.8, "squeeze"),
    ])
    # add the live risk columns for the live trade via a second table shape:
    # reuse _seed_trades_with_risk on a separate db, then merge is messy — instead
    # seed the live row into the SAME db with the risk columns present.
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE trades ADD COLUMN entry_price REAL")
    conn.execute("ALTER TABLE trades ADD COLUMN stop_loss REAL")
    conn.execute("ALTER TABLE trades ADD COLUMN position_size REAL")
    rid = conn.execute("SELECT COALESCE(MAX(id),-1)+1 FROM trades").fetchone()[0]
    conn.execute(
        "INSERT INTO trades (id, symbol, direction, timestamp, status, pnl, "
        "pnl_percent, is_backtest, is_demo, strategy_name, entry_price, stop_loss, "
        "position_size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, "BTCUSDT", "buy", (base + timedelta(hours=120)).isoformat(), "closed",
         2.4, 2.4, 0, 0, "", 100.0, 98.0, 1.5),  # real R = 2.4/(2*1.5) = 0.8
    )
    conn.commit()
    conn.close()
    # tau=0.5, haircut=0.5: backtest R 0.8 - 0.5 = 0.3 < 0.5 -> won_r 0;
    # live R 0.8 (never haircut) >= 0.5 -> won_r 1.
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db, backtest_trades_db=db,
        include_cusum=False, r_label_threshold=0.5, backtest_r_haircut=0.5,
    ))
    bt = [r for r in rows if r["event_source"] == "backtest"]
    live = [r for r in rows if r["event_source"] == "live"]
    assert len(bt) == 1 and len(live) == 1
    assert bt[0]["won_r"] == 0    # haircut pushed the backtest R below tau
    assert live[0]["won_r"] == 1  # live R untouched, still clears tau
    # Without the haircut, the same backtest R=0.8 would clear tau=0.5 -> won_r 1.
    rows_nohc = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db, backtest_trades_db=db,
        include_cusum=False, r_label_threshold=0.5,
    ))
    bt_nohc = [r for r in rows_nohc if r["event_source"] == "backtest"]
    assert bt_nohc[0]["won_r"] == 1


def test_r_label_threshold_absent_by_default(tmp_path: Path):
    """Without r_label_threshold, no won_r column is emitted (schema-optional)."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
    ))
    assert rows
    assert all("won_r" not in r for r in rows)


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


# -----------------------------------------------------------------------------
# Backtest event source (S-MLOPT-S6-FU-2)
# -----------------------------------------------------------------------------

def _seed_journal(
    db: Path,
    *,
    live: list[tuple[str, str, float]] | None = None,
    backtest: list[tuple[str, str, float, str]] | None = None,
) -> None:
    """Create a trades table carrying both real (is_backtest=0) and recorded
    backtest (is_backtest=1) rows in one DB — the single-journal layout the
    S-MLOPT-S7 recorder demo produced (a TEMP copy with both row kinds).

    live = [(iso_ts, direction, pnl), ...]
    backtest = [(iso_ts, direction, pnl, strategy_name), ...]  (is_backtest=1)
    """
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, symbol TEXT, "
        "direction TEXT, timestamp TEXT, status TEXT, pnl REAL, pnl_percent REAL, "
        "is_backtest INT, is_demo INT, strategy_name TEXT)"
    )
    rid = conn.execute("SELECT COALESCE(MAX(id), -1) + 1 FROM trades").fetchone()[0]
    cols = ("id, symbol, direction, timestamp, status, pnl, pnl_percent, "
            "is_backtest, is_demo, strategy_name")
    for ts, direction, pnl in (live or []):
        conn.execute(
            f"INSERT INTO trades ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, "BTCUSDT", direction, ts, "closed", pnl, pnl / 100.0, 0, 0, ""),
        )
        rid += 1
    for ts, direction, pnl, strat in (backtest or []):
        conn.execute(
            f"INSERT INTO trades ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, "BTCUSDT", direction, ts, "closed", pnl, pnl / 100.0, 1, 0, strat),
        )
        rid += 1
    conn.commit()
    conn.close()


def test_backtest_rows_sampled_and_labeled(tmp_path: Path):
    """Each is_backtest=1 row produces one backtest candidate, carrying the
    harness's realized outcome (not a synthetic triple-barrier)."""
    closes = _trending_closes()
    ddir = _write_market_raw(tmp_path, closes)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "backtest_trades.db"
    _seed_journal(db, backtest=[
        ((base + timedelta(hours=50)).isoformat(), "buy", 1.8, "squeeze"),    # win
        ((base + timedelta(hours=120)).isoformat(), "sell", -1.0, "fade"),    # loss
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, backtest_trades_db=db,
    ))
    bt = [r for r in rows if r["event_source"] == "backtest"]
    cusum = [r for r in rows if r["event_source"] == "cusum"]
    assert len(cusum) >= 5            # CUSUM candidates still produced
    assert len(bt) == 2              # both backtest trades located + emitted
    for r in bt:
        # Real-execution outcome, NOT a synthetic barrier; train-side.
        assert r["barrier_touched"] == "backtest"
        assert r["is_live_trade"] is False
        assert r["signal_vol"] > 0   # past-only feature window
        assert r["vol_bucket"].startswith("vol_b")
    won_row = next(r for r in bt if r["direction"] == 1)
    lost_row = next(r for r in bt if r["direction"] == -1)
    assert won_row["won"] == 1 and won_row["label"] == 1
    assert lost_row["won"] == 0 and lost_row["label"] == -1
    # The recorder stores realized R in pnl, so r_multiple recovers it.
    assert won_row["r_multiple"] == 1.8
    assert lost_row["r_multiple"] == -1.0


def test_backtest_strategy_filter(tmp_path: Path):
    """`backtest_strategies` restricts to a subset of harness strategies."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "backtest_trades.db"
    _seed_journal(db, backtest=[
        ((base + timedelta(hours=30)).isoformat(), "buy", 1.2, "squeeze"),
        ((base + timedelta(hours=60)).isoformat(), "buy", 0.8, "fade"),
        ((base + timedelta(hours=90)).isoformat(), "sell", -0.5, "trend"),
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, backtest_trades_db=db,
        backtest_strategies=("squeeze", "fade"),
    ))
    bt = [r for r in rows if r["event_source"] == "backtest"]
    assert len(bt) == 2  # squeeze + fade; trend filtered out


def test_include_backtest_reuses_live_db(tmp_path: Path):
    """`include_backtest=True` (no dedicated DB) reads is_backtest=1 rows from
    `live_trades_db` — the single-journal S7 flow. Live rows stay eval-side;
    backtest rows ride train-side."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_journal(
        db,
        live=[((base + timedelta(hours=30)).isoformat(), "buy", 9.0)],
        backtest=[
            ((base + timedelta(hours=60)).isoformat(), "buy", 1.5, "squeeze"),
            ((base + timedelta(hours=90)).isoformat(), "sell", -1.0, "fade"),
        ],
    )
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, live_trades_db=db, include_backtest=True,
    ))
    live = [r for r in rows if r["event_source"] == "live"]
    bt = [r for r in rows if r["event_source"] == "backtest"]
    assert len(live) == 1 and len(bt) == 2
    # live_holdout discipline: backtest rows train-side, live rows eval-side.
    assert all(r["is_live_trade"] for r in live)
    assert not any(r["is_live_trade"] for r in bt)


def test_backtest_missing_db_is_noop(tmp_path: Path):
    """A non-existent backtest_trades_db doesn't crash — just no backtest rows."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
        backtest_trades_db=tmp_path / "does-not-exist.db",
    ))
    assert rows  # CUSUM rows still emitted
    assert all(r["event_source"] == "cusum" for r in rows)


def test_backtest_only_train_plus_real_eval(tmp_path: Path):
    """The manifest's split: `include_cusum=False` + backtest (train) + live
    (eval) → only backtest + live rows, cleanly partitionable by is_live_trade."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_journal(
        db,
        live=[((base + timedelta(hours=40)).isoformat(), "buy", 5.0)],
        backtest=[
            ((base + timedelta(hours=h)).isoformat(), "buy", 1.0, "squeeze")
            for h in (20, 60, 100, 140)
        ],
    )
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, include_cusum=False,
        backtest_trades_db=db, live_trades_db=db,
    ))
    sources = {r["event_source"] for r in rows}
    assert sources == {"backtest", "live"}
    assert sum(1 for r in rows if not r["is_live_trade"]) == 4  # train: backtest
    assert sum(1 for r in rows if r["is_live_trade"]) == 1      # eval: real


def test_four_source_mix(tmp_path: Path):
    """All four samplers compose: CUSUM + signal_log + backtest + live."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    _seed_journal(
        db,
        live=[((base + timedelta(hours=30)).isoformat(), "buy", 9.0)],
        backtest=[((base + timedelta(hours=110)).isoformat(), "buy", 1.0, "squeeze")],
    )
    _seed_signals(db, [
        ((base + timedelta(hours=60)).isoformat(), "ict_scalp", "BTCUSDT", "buy"),
        ((base + timedelta(hours=90)).isoformat(), "ict_scalp", "BTCUSDT", "sell"),
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
        live_trades_db=db, signal_log_db=db, backtest_trades_db=db,
    ))
    assert {r["event_source"] for r in rows} == {
        "cusum", "signal_log", "backtest", "live"
    }
    # Only the real trade is eval-side; cusum/signal_log/backtest are train-side.
    assert all(r["is_live_trade"] for r in rows if r["event_source"] == "live")
    assert not any(
        r["is_live_trade"] for r in rows if r["event_source"] != "live"
    )


def test_backtest_strategies_string_form(tmp_path: Path):
    """The build CLI passes family args as `key=a,b`; comma strings work too."""
    ddir = _write_market_raw(tmp_path, _trending_closes())
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "backtest_trades.db"
    _seed_journal(db, backtest=[
        ((base + timedelta(hours=40)).isoformat(), "buy", 1.0, "squeeze"),
        ((base + timedelta(hours=80)).isoformat(), "buy", 0.5, "fade"),
        ((base + timedelta(hours=120)).isoformat(), "sell", -0.3, "trend"),
    ])
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=ddir, vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5, backtest_trades_db=db,
        backtest_strategies="squeeze,trend",
    ))
    assert sum(1 for r in rows if r["event_source"] == "backtest") == 2
