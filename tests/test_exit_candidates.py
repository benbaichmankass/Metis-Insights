"""Tests for the exit_candidates dataset family (exit-management ML P0).

Covers the design's pre-registered discipline
(``docs/research/exit-management-ml-experiment-DESIGN.md`` §4, §6):

  - **Leakage** — features at an in-trade bar ``t`` are invariant to bars
    ``> t`` (mutate the future, features unchanged); the ``should_hold`` label
    is invariant to bars ``< t`` (mutate the past, label unchanged). This is the
    single most important property — the future exit defines the label, so a
    feature that peeks forward would inflate OOS AUC fraudulently.
  - **Label sanity** — a constructed up-trend in-trade bar (long) → should_hold
    = 1; a bar right before an adverse barrier → 0.
  - **Determinism / robustness** — same (dataset, knobs) → identical rows; a
    degenerate (too-short / flat) input emits nothing rather than raising; the
    family builds a non-empty frame from a small synthetic fixture and registers
    with the trainer.
  - **Live arm** — real closed trades' in-trade bars are reconstructed, tagged
    ``is_live_trade=True`` / ``event_source="live"``, in the same feature space.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.datasets.families.exit_candidates import ExitCandidatesBuilder
from ml.datasets.registry import get_builder, list_families

_FEATURE_COLS = (
    "unrealized_r", "bars_held", "mfe_r_so_far", "mae_r_so_far",
    "dist_to_stop_atr", "dist_to_target_atr",
    "log_return", "rolling_log_return_vol", "vol_bucket",
    "parkinson_vol", "garman_klass_vol", "rogers_satchell_vol", "yang_zhang_vol",
    "momentum", "hour_of_day", "dayofweek",
    "log_return_lag_1", "log_return_lag_2",
)


# ----------------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------------
def _write_market_raw(root: Path, candles: list[dict]) -> Path:
    """Write a market_raw dataset dir from explicit OHLC candles.

    Each ``candles[i]`` is ``{open, high, low, close}``; ts/symbol/timeframe are
    synthesized (1h bars from 2026-01-01).
    """
    ddir = root / "market_raw" / "ETHUSDT" / "5m" / "v1"
    ddir.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with (ddir / "data.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(candles):
            row = {
                "ts": (base + timedelta(hours=i)).isoformat(),
                "symbol": "ETHUSDT",
                "timeframe": "5m",
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": 1.0,
                "source": "test",
            }
            fh.write(json.dumps(row) + "\n")
    return ddir


def _candle(prev: float, close: float) -> dict:
    """OHLC bar from prev_close → close with ±0.4% wicks (touch-reachable)."""
    return {
        "open": prev,
        "high": max(prev, close) * 1.004,
        "low": min(prev, close) * 0.996,
        "close": close,
    }


def _series_candles(closes: list[float]) -> list[dict]:
    out = []
    prev = closes[0]
    for c in closes:
        out.append(_candle(prev, c))
        prev = c
    return out


def _alt_closes(n: int = 220) -> list[float]:
    """Alternating up/down runs so the CUSUM filter fires both directions."""
    closes = [100.0]
    for i in range(1, n):
        drift = 0.004 if (i // 12) % 2 == 0 else -0.004
        closes.append(closes[-1] * (1 + drift))
    return closes


def _build(ddir: Path, **kw) -> list[dict]:
    params = dict(
        market_raw_path=ddir, vol_window_n=10, cusum_threshold_mult=0.5,
        max_trade_bars=12, hold_horizon=6,
    )
    params.update(kw)
    return list(ExitCandidatesBuilder().iter_rows(**params))


# ----------------------------------------------------------------------------
# registration + basic build
# ----------------------------------------------------------------------------
def test_family_registered():
    assert "exit_candidates" in list_families()
    assert isinstance(get_builder("exit_candidates"), ExitCandidatesBuilder)


def test_builds_nonempty_in_trade_rows(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, _series_candles(_alt_closes()))
    rows = _build(ddir)
    assert len(rows) >= 10, f"expected many in-trade rows, got {len(rows)}"
    dirs = {r["direction"] for r in rows}
    assert dirs <= {1, -1} and (1 in dirs or -1 in dirs)
    for r in rows:
        assert r["should_hold"] in {0, 1}
        assert r["barrier_touched"] in {"tp", "sl", "timeout"}
        assert r["label"] in {-1, 0, 1}
        assert r["should_hold"] == (1 if r["label"] > 0 else 0)
        assert r["bars_held"] >= 0
        assert r["mfe_r_so_far"] >= 0.0
        assert r["mae_r_so_far"] <= 0.0
        assert r["is_live_trade"] is False
        assert r["event_source"] == "synthetic"
        for col in _FEATURE_COLS:
            assert col in r, f"missing feature {col}"


def test_bars_held_increases_within_a_trade(tmp_path: Path):
    # bars_held is the in-trade clock — within one synthetic trade it must be a
    # non-negative increasing index (entry bar = 0).
    ddir = _write_market_raw(tmp_path, _series_candles(_alt_closes()))
    rows = _build(ddir, in_trade_sample_step=1)
    assert any(r["bars_held"] == 0 for r in rows)  # at least one entry-bar row
    assert all(isinstance(r["bars_held"], int) and r["bars_held"] >= 0 for r in rows)


def test_empty_when_too_few_bars(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, _series_candles([100.0, 101.0, 102.0]))
    assert _build(ddir) == []


def test_flat_series_does_not_raise(tmp_path: Path):
    # Degenerate flat series: zero vol → no events / no rows, never a raise.
    ddir = _write_market_raw(tmp_path, _series_candles([100.0] * 60))
    rows = _build(ddir)
    assert rows == []


def test_determinism(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, _series_candles(_alt_closes()))
    a = _build(ddir, seed=1)
    b = _build(ddir, seed=999)  # seed is interface-parity; path is deterministic
    assert a == b and len(a) > 0


# ----------------------------------------------------------------------------
# leakage discipline (THE core property)
# ----------------------------------------------------------------------------
def test_features_invariant_to_future_bars(tmp_path: Path):
    """Mutating bars AFTER an in-trade bar t must NOT change the features at t
    (features are past-only). The label MAY change (it is future-only) — so we
    compare only the feature columns, keyed by (ts, direction, bars_held)."""
    closes = _alt_closes()
    ddir = _write_market_raw(tmp_path, _series_candles(closes))
    base_rows = _build(ddir)
    assert base_rows

    # Mutate the LAST 20% of bars drastically (a future shock) and rebuild.
    mutated = list(closes)
    cut = int(len(mutated) * 0.8)
    for i in range(cut, len(mutated)):
        mutated[i] = mutated[i] * 1.5  # large forward perturbation
    ddir2 = _write_market_raw(tmp_path / "mut", _series_candles(mutated))
    mut_rows = _build(ddir2)

    def feat_key(r):
        return (r["ts"], r["direction"], r["bars_held"])

    def feat_vals(r):
        return {c: r[c] for c in _FEATURE_COLS}

    mut_by_key = {feat_key(r): feat_vals(r) for r in mut_rows}
    # Every base row whose ENTIRE feature+forward window predates the cut must
    # have byte-identical features in the mutated build. Restrict to rows well
    # before the cut so neither the past window nor a position opened later is
    # touched.
    checked = 0
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cut_ts = (base + timedelta(hours=cut - 1)).isoformat()
    for r in base_rows:
        if r["ts"] >= cut_ts:
            continue
        k = feat_key(r)
        if k in mut_by_key:
            assert feat_vals(r) == mut_by_key[k], (
                f"feature leak: row {k} features changed when only future bars "
                f"were mutated"
            )
            checked += 1
    assert checked >= 5, f"too few comparable rows checked ({checked})"


def test_label_invariant_to_past_bars(tmp_path: Path):
    """Mutating bars BEFORE an in-trade bar t's forward window must NOT change
    the should_hold label (the label reads only bars > t). We isolate a single
    constructed trade so the entry/walk geometry is fixed, then perturb a bar
    strictly before the labeled forward window."""
    # A noisy up-trend long: entry events fire; in-trade bars walk forward; the
    # forward hold-window of a row at t reads [t+1 .. t+1+H]. Mutating bar 5
    # (deep in the past) can't touch any forward window. Noise keeps vol > 0
    # (a perfectly monotone series has zero rolling stdev → no CUSUM events).
    closes = [100.0]
    for i in range(1, 120):
        wobble = 1.001 if i % 2 == 0 else 1.005  # net up-drift with vol
        closes.append(closes[-1] * wobble)
    ddir = _write_market_raw(tmp_path, _series_candles(closes))
    rows_a = _build(ddir, cusum_threshold_mult=0.3)

    closes_b = list(closes)
    closes_b[5] = closes_b[5] * 0.5  # drastic PAST shock, far before any label
    ddir_b = _write_market_raw(tmp_path / "b", _series_candles(closes_b))
    rows_b = _build(ddir_b, cusum_threshold_mult=0.3)

    # Compare labels for rows whose forward window starts after the early shock
    # AND whose features predate nothing we perturbed in a way that re-routes
    # CUSUM. To keep the comparison clean, key only on rows late enough that the
    # bar-5 mutation is outside both their past vol window and forward window.
    def lab_key(r):
        return (r["ts"], r["direction"], r["bars_held"])

    a_by = {lab_key(r): r["should_hold"] for r in rows_a}
    b_by = {lab_key(r): r["should_hold"] for r in rows_b}
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Rows at/after bar 30 are far past the vol-window reach of bar 5.
    late_ts = (base + timedelta(hours=30)).isoformat()
    common = [k for k in a_by if k in b_by and k[0] >= late_ts]
    assert len(common) >= 5
    for k in common:
        assert a_by[k] == b_by[k], (
            f"label leak: should_hold for {k} changed when only a far-past bar "
            f"was mutated"
        )


# ----------------------------------------------------------------------------
# label sanity
# ----------------------------------------------------------------------------
def _trend_candle(prev: float, close: float, up: bool) -> dict:
    """A trending bar with a SMALL counter-wick — so the triple-barrier's
    favorable (up) barrier is reached before the adverse (down) one for a long.
    A long's stop is below entry; a tiny down-wick keeps the bar from straddling
    both barriers (the generic ±0.4% wick in `_candle` would force the
    adverse-first stop and defeat the sanity check)."""
    hi = max(prev, close) * (1.0008 if up else 1.0002)
    lo = min(prev, close) * (0.9998 if up else 0.9992)
    return {"open": prev, "high": hi, "low": lo, "close": close}


def test_uptrend_long_should_hold_true(tmp_path: Path):
    """A long in a clean up-trend: from each in-trade bar, the favorable
    barrier is reached before the stop → should_hold = 1 dominates."""
    closes = [100.0]
    for i in range(1, 160):
        wobble = 1.001 if i % 2 == 0 else 1.006  # net up-drift, vol > 0
        closes.append(closes[-1] * wobble)
    candles = []
    prev = closes[0]
    for c in closes:
        candles.append(_trend_candle(prev, c, up=c >= prev))
        prev = c
    ddir = _write_market_raw(tmp_path, candles)
    rows = _build(ddir, cusum_threshold_mult=0.3, hold_horizon=8)
    longs = [r for r in rows if r["direction"] == 1]
    assert longs, "expected long in-trade rows in an up-trend"
    held = sum(r["should_hold"] for r in longs)
    # In a clean up-trend a long should overwhelmingly be worth holding.
    assert held / len(longs) >= 0.8, (
        f"up-trend long should_hold rate too low: {held}/{len(longs)}"
    )


def test_bar_before_adverse_barrier_should_hold_false(tmp_path: Path):
    """A long whose price then collapses: the in-trade bars just before the
    adverse move must NOT be worth holding (should_hold = 0 dominates)."""
    # Up to bar 60, then a sustained crash so any still-open long's forward
    # window hits the stop before the favorable target.
    closes = [100.0]
    for i in range(1, 60):
        wobble = 1.001 if i % 2 == 0 else 1.006  # rise (opens longs), vol > 0
        closes.append(closes[-1] * wobble)
    for i in range(60, 140):
        closes.append(closes[-1] * 0.99)    # crash
    ddir = _write_market_raw(tmp_path, _series_candles(closes))
    rows = _build(ddir, cusum_threshold_mult=0.3, hold_horizon=8)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Rows whose forward window is entirely inside the crash region.
    crash_start = (base + timedelta(hours=58)).isoformat()
    crash_end = (base + timedelta(hours=118)).isoformat()
    longs_in_crash = [
        r for r in rows
        if r["direction"] == 1 and crash_start <= r["ts"] <= crash_end
    ]
    assert longs_in_crash, "expected long rows entering the crash window"
    not_held = sum(1 for r in longs_in_crash if r["should_hold"] == 0)
    assert not_held / len(longs_in_crash) >= 0.7, (
        f"longs into a crash should mostly NOT hold: "
        f"{not_held}/{len(longs_in_crash)}"
    )


# ----------------------------------------------------------------------------
# live arm (domain-shift discipline)
# ----------------------------------------------------------------------------
def _seed_trades(db: Path, entries: list[tuple[str, str, str, float, float]]) -> None:
    """entries = [(entry_iso, exit_iso, direction, entry_price, stop_loss), ...]
    of REAL closed ETHUSDT trades."""
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, "
        "timestamp TEXT, closed_at TEXT, status TEXT, entry_price REAL, "
        "stop_loss REAL, pnl REAL, is_backtest INT, is_demo INT)"
    )
    for i, (ets, xts, direction, ep, sl) in enumerate(entries):
        conn.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (i, "ETHUSDT", direction, ets, xts, "closed", ep, sl, 1.0, 0, 0),
        )
    conn.commit()
    conn.close()


def test_live_trade_in_trade_bars_reconstructed(tmp_path: Path):
    closes = _alt_closes()
    ddir = _write_market_raw(tmp_path, _series_candles(closes))
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    # Entry at hour 50, exit at hour 60, a long with a real stop 1% below entry.
    entry_price = closes[50]
    _seed_trades(db, [(
        (base + timedelta(hours=50)).isoformat(),
        (base + timedelta(hours=60)).isoformat(),
        "buy", entry_price, entry_price * 0.99,
    )])
    rows = _build(ddir, live_trades_db=db)
    live = [r for r in rows if r["is_live_trade"]]
    synth = [r for r in rows if not r["is_live_trade"]]
    assert synth, "synthetic rows still produced"
    assert live, "real trade's in-trade bars reconstructed"
    for r in live:
        assert r["event_source"] == "live"
        assert r["direction"] == 1
        assert r["rolling_log_return_vol"] > 0  # past-only features present
        assert r["vol_bucket"].startswith("vol_b")
        assert r["should_hold"] in {0, 1}
    # The in-trade walk spans entry..exit (≤ ~10 bars) — bounded, not the whole
    # series.
    assert max(r["bars_held"] for r in live) <= 12


def test_include_synthetic_false_emits_only_live(tmp_path: Path):
    closes = _alt_closes()
    ddir = _write_market_raw(tmp_path, _series_candles(closes))
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "trade_journal.db"
    entry_price = closes[40]
    _seed_trades(db, [(
        (base + timedelta(hours=40)).isoformat(),
        (base + timedelta(hours=52)).isoformat(),
        "buy", entry_price, entry_price * 0.99,
    )])
    rows = _build(ddir, include_synthetic=False, live_trades_db=db)
    assert rows and all(r["is_live_trade"] for r in rows)
    assert all(r["event_source"] == "live" for r in rows)


def test_live_missing_db_is_noop(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, _series_candles(_alt_closes()))
    rows = _build(ddir, live_trades_db=tmp_path / "nope.db")
    assert rows and all(r["event_source"] == "synthetic" for r in rows)


def test_live_no_trades_table_is_noop(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, _series_candles(_alt_closes()))
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()  # empty DB, no trades table
    rows = _build(ddir, live_trades_db=db)
    assert rows and all(r["event_source"] == "synthetic" for r in rows)


# ----------------------------------------------------------------------------
# build → schema validation
# ----------------------------------------------------------------------------
def test_build_writes_valid_schema(tmp_path: Path):
    ddir = _write_market_raw(tmp_path, _series_candles(_alt_closes()))
    out = tmp_path / "datasets-out"
    paths = ExitCandidatesBuilder().build(
        output_dir=out, version="v1", source="test",
        symbol_scope="ETHUSDT", timeframe="5m",
        market_raw_path=ddir, vol_window_n=10, cusum_threshold_mult=0.5,
        max_trade_bars=12, hold_horizon=6,
    )
    meta = json.loads(paths.metadata.read_text())
    assert meta["family"] == "exit_candidates"
    assert meta["leakage_test_status"] == "passed"
    assert meta["row_count"] >= 10
    assert meta["label_version"] == "exit-hold-triple-barrier-v1"
