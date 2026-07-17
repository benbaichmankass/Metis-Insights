"""`setup_candidates` dataset family (S-MLOPT-S5, M14 Phase 1.1).

Manufactures a **dense, properly-labeled** dataset of *hypothetical* trade
setups from bar history, so the decision models (setup-quality / meta-label)
can train on thousands of labeled rows instead of the ~80 real closed trades
that collapse them to a baseline (gap G4).

## How it works

1. Read a canonical `market_raw` OHLCV dataset (pass
   `market_raw_path=<dataset-dir>` via `iter_rows_kwargs`).
2. **Sample candidate events** from the close path with the de Prado symmetric
   **CUSUM filter** (`ml.datasets.labeling.cusum_events`) — bars where a
   sustained move triggered, de-clustered. The breach side gives each candidate
   a direction (up-breach → long, down-breach → short). This is the canonical
   event sampler that pairs with triple-barrier labeling, and it's what makes
   the dataset dense (thousands of events from bar history).
3. **Label each event** with the **triple-barrier** method
   (`ml.datasets.labeling.label_event`): entry at the *next* bar's open (no
   signal-bar look-ahead), a take-profit / stop-loss sized to the signal bar's
   local volatility, and a vertical timeout barrier. Fills are modeled
   conservatively (high/low touch detection, adverse-first on a straddling bar,
   optional slippage).
4. Emit one row per candidate: **signal-time features only** + the barrier
   outcome as the label.

## Leakage discipline (PASSED by construction)

A candidate at bar `e` carries features computed from the inclusive **past**
window `[e - window + 1 .. e]` only. The label comes from the **future** path
`[e + 1 .. e + 1 + max_holding]` (entry is at bar `e + 1`'s open). The two
windows never overlap, so a feature cannot leak the barrier outcome. A trainer
consuming this family MUST still scope its `feature_columns` to exclude the
outcome columns (`label`, `won`, `r_multiple`, `ret`, `barrier_touched`,
`holding_bars`, `exit_*`) — same rule as `trade_outcomes` / `setup_labels`.

## Domain-shift caveat (MUST mitigate downstream)

These are **synthetic** fills, not live fills — no partials, no real slippage
distribution, no latency. The labeler mitigates optimism (conservative
fills + slippage knob), but a model trained here MUST be **evaluated on a
held-out set of REAL live trades**, never on synthetic rows.

Pass `live_trades_db=<trade_journal.db>` (S-MLOPT-S6) to also emit REAL closed
trades for the symbol: each is located at the bar covering its entry time and
emitted in the **same feature space** (past-only features from that bar) with
its **actual** realized outcome, tagged `is_live_trade: true` and
`barrier_touched: "live"`. Synthetic rows carry `is_live_trade: false`. The
`live_holdout` split strategy
(`ml.experiments.splitters.split_live_holdout`) then trains on the synthetic
rows and evaluates on the real ones — the mandatory domain-shift check. Set
`include_synthetic=false` to emit only the real rows.

## Signal-log event source (MB-20260603-002, S-MLOPT-S6 follow-up)

The S6 live-holdout eval surfaced a large train↔eval domain gap: synthetic
CUSUM candidates have a ~0.457 win rate but the strategies' real BTCUSDT trades
have 0.244. CUSUM momentum events are a different (easier) population than the
strategies' actual setups, so a meta-label trained on synthetic CUSUM lost to
the majority-class baseline on the real holdout (acc 0.670 < 0.756).

Pass ``signal_log_db=<trade_journal.db>`` to also sample candidate events from
the strategies' **real decision points** — every ``side=buy|sell`` row in
``trade_journal.db::signals`` (the audit-log dual-write) becomes a candidate at
the bar covering its ``logged_at_utc``, with the **same triple-barrier label**
the CUSUM events use (synthetic but apples-to-apples — barriers sized to local
vol from the same ``BarrierConfig``). The result is a training distribution
that matches the strategies' real setups; the live-trade holdout still measures
domain transfer to real PnL outcomes.

These rows carry ``event_source: "signal_log"`` (CUSUM rows are tagged
``"cusum"`` and real trades ``"live"``) and ``is_live_trade: false`` because
their label is synthetic, so they ride on the live_holdout train side alongside
the CUSUM rows. Use ``signal_log_strategies=("ict_scalp",...)`` to restrict to
specific strategies, or ``include_cusum=False`` to emit signal-log + live
populations only.

## Backtest event source (S-MLOPT-S6-FU-2)

The signal-log eval (#2716) was an honest negative: signal-log win rate (0.469)
is almost identical to CUSUM (0.457), and both lose to the majority baseline on
the real holdout (signal-log acc 0.526 < CUSUM 0.670 < 0.756). That ruled out
the *event sampler* as the lever — the train↔eval gap is the **synthetic
triple-barrier LABEL**: candle barriers assume optimistic fills, no slippage and
no risk-manager filtering, so both synthetic populations win ~0.46 while the real
trades (which real execution + risk gating filter the obvious winners out of)
win 0.244.

Pass ``backtest_trades_db=<db>`` (or ``include_backtest=True`` to reuse
``live_trades_db``) to attack the label directly: the strategies' standalone
backtest harnesses (``scripts/backtest_squeeze.py``, ``backtest_fade.py``,
``backtest_trend.py``, ``backtest_ict_scalp.py``,
``src/backtest/run_backtest_vwap.py``) already model real slippage + each
strategy's actual entry rule + per-strategy exit logic, so every backtest trade
is a **real-distribution** label at a real signal time. Their per-trade results
are persisted as ``is_backtest=1`` rows by
``ml.datasets.backtest_recorder.write_backtest_trades`` (the S-MLOPT-S7 pattern)
and read back here: each is located at the bar covering its entry time, emitted
in the same past-only feature space, and tagged ``event_source: "backtest"`` +
``is_live_trade: false`` with the harness's **actual realized outcome** (``won``
from the recorded R-proxy ``pnl``) — never a synthetic triple-barrier. So they
ride the train side of ``live_holdout`` while the real trades stay the only
eval-side population: the apples-to-apples backtest-train + real-eval the
signal-log experiment approximated. ``backtest_strategies=("squeeze",...)``
restricts to specific strategies; ``include_cusum=False`` + no ``signal_log_db``
emits the pure backtest-train + real-eval split.

## Range-based vol estimators (builder v2, S-MLOPT-S8 follow-up)

Every emitted row carries the four range-based vol estimators from S-MLOPT-S9
(``parkinson_vol`` / ``garman_klass_vol`` / ``rogers_satchell_vol`` /
``yang_zhang_vol``), computed over the SAME inclusive past window as
``rolling_log_return_vol`` from each candidate's OHLC (``ml.datasets.
volatility_estimators`` — the same module + computation ``market_features``
uses, so the columns are comparable across families). Past-only → leakage-safe
by construction. S8 (cross-symbol pooling) and S9 (range-vol features) are two
INDEPENDENT levers; ``setup-candidates-metalabel-xsym-yz-v1`` stacks both. The
estimators are emitted for every population (CUSUM / signal-log / backtest /
live) so the live-holdout feature space stays identical across train + eval.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from ..builder import DatasetBuilder
from ..labeling import BarrierConfig, cusum_events, label_event
from ..labeling.triple_barrier import log_prices
from ..metadata import LeakageStatus
from ..volatility_estimators import (
    _sqrt_or_zero,
    garman_klass_var,
    parkinson_var,
    rogers_satchell_var,
    yang_zhang_var,
)

_FAMILY = "setup_candidates"


def _parse_ts_hour_dow(ts_str: str) -> tuple[int, int]:
    """ISO-8601 ``ts`` → ``(hour_of_day, dayofweek)``; ``(0, 0)`` on parse
    failure so one malformed bar doesn't abort the build."""
    if not ts_str:
        return 0, 0
    s = ts_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0, 0
    return dt.hour, dt.weekday()


def _quantile_buckets(values: list[float], n_buckets: int) -> tuple[list[float], list[str]]:
    """Bucket boundaries + labels (`vol_b0`..`vol_b{K-1}`) over the dataset."""
    if n_buckets < 2:
        raise ValueError(f"n_vol_buckets must be >= 2; got {n_buckets}")
    labels = [f"vol_b{i}" for i in range(n_buckets)]
    if not values:
        return [], labels
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    boundaries = [
        sorted_vals[max(0, min(n - 1, int(round(i * n / n_buckets)) - 1))]
        for i in range(1, n_buckets)
    ]
    return boundaries, labels


def _bucket_for(value: float, boundaries: list[float], labels: list[str]) -> str:
    for i, cut in enumerate(boundaries):
        if value <= cut:
            return labels[i]
    return labels[-1]


def _load_market_raw_rows(market_raw_path: Path) -> list[dict[str, Any]]:
    data_path = market_raw_path / "data.jsonl"
    if not data_path.is_file():
        raise FileNotFoundError(
            f"market_raw data.jsonl not found at {data_path}; build a market_raw "
            "dataset first via `python -m ml.datasets build market_raw ...`"
        )
    rows: list[dict[str, Any]] = []
    with data_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                rows.append(json.loads(line))
    return rows


def _load_live_trades(db_path: Path | str, symbol: str) -> list[dict[str, Any]]:
    """REAL closed (non-backtest, non-demo) trades for one symbol.

    The held-out real population for the domain-shift eval (S-MLOPT-S6). Mirrors
    the filter the `setup_labels` / `trade_outcomes` families use so the live
    holdout reflects exactly the trades the journal counts. Returns
    `{entry_ts, direction(±1), pnl, pnl_percent, entry_price, stop_loss,
    position_size}` newest-first — the last three are the risk columns the live
    rows' realized-R is reconstructed from (`MB-20260717-M23-LIVEROW-REALIZED-R`);
    they serialize as `None` on a pre-schema DB (old fixtures / a journal missing
    those columns) so callers fall back to a coarse unit-R. Best-effort: a
    missing DB / table returns `[]`.
    """
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    conn.row_factory = sqlite3.Row
    try:
        # Detect which risk columns this journal actually carries so an
        # old-schema DB (the S6 fixtures, a migration-behind copy) degrades to
        # NULL risk fields instead of raising — the same best-effort posture the
        # rest of this loader takes.
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        except sqlite3.OperationalError:
            return []
        risk_cols = [c for c in ("entry_price", "stop_loss", "position_size")
                     if c in have]
        select_cols = ["timestamp", "direction", "pnl", "pnl_percent", *risk_cols]
        sql = (
            f"SELECT {', '.join(select_cols)} FROM trades "
            "WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
            "AND COALESCE(is_demo,0)=0 AND symbol=? AND pnl IS NOT NULL "
            "ORDER BY timestamp"
        )
        try:
            db_rows = conn.execute(sql, (symbol,)).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in db_rows:
        side = str(r["direction"] or "").lower()
        direction = -1 if side in ("sell", "short", "-1") else 1
        keys = r.keys()
        out.append({
            "entry_ts": str(r["timestamp"] or ""),
            "direction": direction,
            "pnl": r["pnl"],
            "pnl_percent": r["pnl_percent"],
            "entry_price": r["entry_price"] if "entry_price" in keys else None,
            "stop_loss": r["stop_loss"] if "stop_loss" in keys else None,
            "position_size": r["position_size"] if "position_size" in keys else None,
        })
    return out


def _live_realized_r(tr: dict[str, Any], won: int) -> tuple[float, str]:
    """Realized R-multiple for a REAL closed trade + which source produced it.

    Prefer the **net, cost-aware** R = ``pnl / (|entry − stop| × size)``: the
    trade's realized dollar pnl (already net of fees) over its dollar risk at
    entry. For a linear instrument (this dataset is BTCUSDT-only, 1:1 contract
    value) the ``size`` cancels the dollar units, so this is exactly the
    realized R the EV gate needs — a losing trade that hit its stop reads ≈ −1R,
    a 2R winner reads ≈ +2R. Falls back to a coarse signed unit-R (±1 by the
    win/loss bit) when the risk columns are missing/zero (a pre-schema journal
    or a trade the writer didn't stop-populate), so the column is never a
    silent 0.0 that the EV scorer would treat as a real 0R outcome.

    Returns ``(r_multiple, source)`` where source ∈ {"stop_distance",
    "unit_fallback"}.
    """
    pnl = tr.get("pnl")
    entry = tr.get("entry_price")
    stop = tr.get("stop_loss")
    size = tr.get("position_size")
    if (pnl is not None and entry is not None and stop is not None
            and size is not None):
        try:
            risk = abs(float(entry) - float(stop)) * float(size)
            if risk > 0:
                return float(pnl) / risk, "stop_distance"
        except (TypeError, ValueError):
            pass
    return (1.0 if won else -1.0), "unit_fallback"


def _load_backtest_trades(
    db_path: Path | str,
    symbol: str,
    *,
    strategies: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Recorded BACKTEST trades (``is_backtest=1``) for one symbol (S-MLOPT-S6-FU-2).

    The harness-labeled TRAIN population: each row was written by
    ``ml.datasets.backtest_recorder.write_backtest_trades`` from a standalone
    strategy backtest (``scripts/backtest_*.py`` / ``src/backtest/``), so it
    carries the strategy's ACTUAL entry rule + the harness's realized outcome
    (modeled slippage + the strategy's own exit logic) — a real-execution-
    distribution label, unlike the synthetic triple-barrier CUSUM / signal-log
    rows. Mirrors ``_load_live_trades`` but reads ``is_backtest=1`` (and
    optionally restricts to ``strategies`` by ``strategy_name``). Returns
    ``{entry_ts, direction(±1), pnl, pnl_percent, strategy}`` time-sorted.
    Best-effort: missing DB / table / column returns ``[]``.
    """
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    conn.row_factory = sqlite3.Row
    try:
        params: list[Any] = [symbol]
        sql = (
            "SELECT timestamp, direction, pnl, pnl_percent, strategy_name "
            "FROM trades WHERE status='closed' AND COALESCE(is_backtest,0)=1 "
            "AND symbol=? AND pnl IS NOT NULL"
        )
        if strategies:
            strat_placeholders = ",".join(["?"] * len(strategies))
            sql += f" AND strategy_name IN ({strat_placeholders})"
            params.extend(strategies)
        sql += " ORDER BY timestamp"
        try:
            db_rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in db_rows:
        side = str(r["direction"] or "").lower()
        direction = -1 if side in ("sell", "short", "-1") else 1
        out.append({
            "entry_ts": str(r["timestamp"] or ""),
            "direction": direction,
            "pnl": r["pnl"],
            "pnl_percent": r["pnl_percent"],
            "strategy": str(r["strategy_name"] or ""),
        })
    return out


def _normalise_str_tuple(
    value: tuple[str, ...] | list[str] | str | None,
) -> tuple[str, ...] | None:
    """Normalise the dataset CLI's ``key=a,b,c`` strings to a tuple.

    Returns ``None`` for ``None`` / empty so callers can apply their own
    default. Mirrors ``_resolve_market_raw_paths``'s string-split convention so
    the build CLI's ``key=value`` pairs land as the same shape as a Python list.
    """
    if value is None:
        return None
    if isinstance(value, str):
        items = [s.strip() for s in value.split(",") if s.strip()]
    else:
        items = [str(s).strip() for s in value if str(s).strip()]
    return tuple(items) if items else None


def _load_signal_log_events(
    db_path: Path | str,
    symbol: str,
    *,
    sides: tuple[str, ...] = ("buy", "sell"),
    strategies: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Audit-log buy/sell signal rows for one symbol (MB-20260603-002).

    Reads ``trade_journal.db::signals`` — the dual-write of the JSONL audit
    stream every strategy's signal builder emits (``side`` ∈ ``{buy, sell,
    none}``). The buy/sell rows are the strategies' **real decision points**;
    sampling candidates from them makes the train distribution match real
    setups (vs CUSUM events, which are momentum-driven and easier — the
    S-MLOPT-S6 domain gap).

    Returns ``{event_ts, direction(±1), strategy}`` time-sorted ascending.
    Best-effort: missing DB / table / column returns ``[]``. ``sides`` selects
    which audit rows count (default buy + sell; ``none`` is the no-signal
    evaluation row, never a candidate). ``strategies`` optionally restricts to
    a subset; ``None`` = all.
    """
    path = Path(db_path)
    if not path.exists():
        return []
    sides_lower = tuple(s.lower() for s in sides if s)
    if not sides_lower:
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    conn.row_factory = sqlite3.Row
    try:
        side_placeholders = ",".join(["?"] * len(sides_lower))
        params: list[Any] = [symbol, *sides_lower]
        sql = (
            "SELECT logged_at_utc, strategy, side FROM signals "
            f"WHERE symbol=? AND LOWER(side) IN ({side_placeholders})"
        )
        if strategies:
            strat_placeholders = ",".join(["?"] * len(strategies))
            sql += f" AND strategy IN ({strat_placeholders})"
            params.extend(strategies)
        sql += " ORDER BY logged_at_utc"
        try:
            db_rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in db_rows:
        side = str(r["side"] or "").lower()
        direction = -1 if side in ("sell", "short") else 1
        out.append({
            "event_ts": str(r["logged_at_utc"] or ""),
            "direction": direction,
            "strategy": str(r["strategy"] or ""),
        })
    return out


def _resolve_market_raw_paths(
    market_raw_path: Path | str | None,
    market_raw_paths: list[Path | str] | str | None,
) -> list[Path]:
    """Normalise the single/multi `market_raw` path inputs to a list of dirs.

    Accepts a single `market_raw_path`, a list `market_raw_paths`, or a
    comma-separated string (the build CLI passes family args as `key=value`
    strings). Raises if neither is given."""
    out: list[Path] = []
    if market_raw_paths is not None:
        items = (
            market_raw_paths.split(",")
            if isinstance(market_raw_paths, str)
            else list(market_raw_paths)
        )
        out.extend(Path(str(p).strip()) for p in items if str(p).strip())
    if market_raw_path is not None:
        out.append(Path(str(market_raw_path)))
    if not out:
        raise ValueError(
            "setup_candidates requires market_raw_path or market_raw_paths"
        )
    return out


def _bar_index_at_or_before(sorted_ts: list[str], target_ts: str) -> int | None:
    """Index of the last bar whose ts is ≤ ``target_ts`` (None if none / blank)."""
    if not target_ts:
        return None
    i = bisect_right(sorted_ts, target_ts) - 1
    return i if i >= 0 else None


def _feature_fields(
    rows: list[dict[str, Any]],
    e: int,
    log_returns: list[float | None],
    vol: float,
    boundaries: list[float],
    bucket_labels: list[str],
    momentum_window: int,
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    vol_window_n: int,
) -> dict[str, Any]:
    """Signal-time (past-only) feature fields shared by synthetic + live rows.

    Computed from bar ``e`` and the inclusive past window only — identical for a
    CUSUM-sampled synthetic candidate and a REAL trade located at bar ``e``, so
    both populations live in one feature space (the live holdout is comparable).

    The four range-based vol estimators (S-MLOPT-S9: parkinson / garman_klass /
    rogers_satchell / yang_zhang) are computed over the SAME inclusive past
    window ``[e - vol_window_n + 1 .. e]`` as ``rolling_log_return_vol`` and
    emitted as a stdev (``sqrt`` of the variance estimate), mirroring
    ``market_features`` exactly so the two families' columns are comparable.
    Past-only → leakage-safe by construction. The S9 finding is that
    Yang-Zhang lifts the regime heads' separation; this makes those same
    estimators available at signal time for the meta-label decision model
    (S-MLOPT-S8 follow-up — cross-symbol + range-vol are two independent
    levers, combined here)."""
    log_ret = log_returns[e]
    hour_of_day, dayofweek = _parse_ts_hour_dow(str(rows[e].get("ts", "")))
    lag_1 = log_returns[e - 1] if e - 1 >= 0 else None
    lag_2 = log_returns[e - 2] if e - 2 >= 0 else None
    momentum = float(sum(
        v for v in log_returns[max(0, e - momentum_window + 1): e + 1]
        if v is not None
    ))
    # Range-vol window: the inclusive past OHLC window `[s .. e]`, same as
    # `rolling_log_return_vol`. YZ's overnight term needs each bar's prior close.
    s = max(0, e - vol_window_n + 1)
    w_open, w_high, w_low, w_close = (
        opens[s : e + 1], highs[s : e + 1], lows[s : e + 1], closes[s : e + 1],
    )
    w_prev_close = [closes[j - 1] if j - 1 >= 0 else None
                    for j in range(s, e + 1)]
    return {
        "ts": str(rows[e].get("ts", "")),
        "symbol": str(rows[e].get("symbol", "")),
        "timeframe": str(rows[e].get("timeframe", "")),
        "source": str(rows[e].get("source", "")),
        "signal_vol": float(vol),
        "log_return": float(log_ret) if log_ret is not None else 0.0,
        "rolling_log_return_vol": float(vol),
        "vol_bucket": _bucket_for(vol, boundaries, bucket_labels),
        "parkinson_vol": _sqrt_or_zero(parkinson_var(w_high, w_low)),
        "garman_klass_vol": _sqrt_or_zero(
            garman_klass_var(w_open, w_high, w_low, w_close)),
        "rogers_satchell_vol": _sqrt_or_zero(
            rogers_satchell_var(w_open, w_high, w_low, w_close)),
        "yang_zhang_vol": _sqrt_or_zero(
            yang_zhang_var(w_open, w_high, w_low, w_close, w_prev_close)),
        "momentum": momentum,
        "hour_of_day": int(hour_of_day),
        "dayofweek": int(dayofweek),
        "log_return_lag_1": float(lag_1) if lag_1 is not None else 0.0,
        "log_return_lag_2": float(lag_2) if lag_2 is not None else 0.0,
    }


class SetupCandidatesBuilder(DatasetBuilder):
    family: ClassVar[str] = _FAMILY
    # v2 (S-MLOPT-S8 follow-up): added the four range-based vol estimators
    # (parkinson/garman_klass/rogers_satchell/yang_zhang) at signal time, so the
    # meta-label can combine the cross-symbol lever (S8) with the range-vol
    # lever (S9). v1 emitted only rolling_log_return_vol + vol_bucket.
    builder_version: ClassVar[str] = "v2"
    # Window separation (features past-only, label future-only) makes leakage
    # impossible by construction — same guarantee market_features stamps.
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.PASSED
    label_version: ClassVar[str] = "triple-barrier-v1"
    schema: ClassVar[Mapping[str, type]] = {
        # identity / context
        "ts": str,
        "symbol": str,
        "timeframe": str,
        "source": str,
        # signal-time features (past-only)
        "direction": int,            # +1 long candidate, -1 short candidate
        "entry_price": float,        # next-bar open the trade entered at
        "signal_vol": float,         # local vol that sized the barriers
        "log_return": float,
        "rolling_log_return_vol": float,
        "vol_bucket": str,
        # range-based vol estimators (S-MLOPT-S9), past-only over the same
        # window as rolling_log_return_vol — emitted as a stdev (sqrt of var).
        "parkinson_vol": float,
        "garman_klass_vol": float,
        "rogers_satchell_vol": float,
        "yang_zhang_vol": float,
        "momentum": float,           # cum log-return over the momentum window
        "hour_of_day": int,
        "dayofweek": int,
        "log_return_lag_1": float,
        "log_return_lag_2": float,
        # triple-barrier label (future-only) — outcome columns, NOT features
        "barrier_touched": str,      # 'tp'|'sl'|'timeout' (synthetic) | 'live' (real) | 'backtest' (harness)
        "label": int,                # +1 tp, -1 sl, sign(ret) at timeout
        "won": int,                  # 1 if label > 0 else 0 (meta-label target)
        "r_multiple": float,         # ret / stop-distance (risk units)
        # how a `live` row's r_multiple was derived (MB-20260717-M23-LIVEROW-REALIZED-R):
        # 'stop_distance' = real net R (pnl / |entry-stop|*size), 'unit_fallback'
        # = coarse ±1 when the risk columns were absent. Only emitted on live
        # rows; synthetic/backtest rows omit it (their R comes from the barrier /
        # recorder pnl directly).
        "r_multiple_source": str,
        # M23 variant C1 R-aware TRAINING target — 1[r_multiple >= r_label_threshold].
        # Only emitted when iter_rows is called with r_label_threshold set; a
        # meta-label manifest targets this instead of `won` to rank P(materially
        # good trade) rather than P(win). Absent (validator-allowed) otherwise.
        "won_r": int,
        "ret": float,                # direction-signed net return
        "holding_bars": int,
        # real-vs-synthetic split flag (domain-shift discipline)
        "is_live_trade": bool,
        # which sampler emitted this row: 'cusum' / 'signal_log' / 'backtest' /
        # 'live'. ``cusum`` + ``signal_log`` carry synthetic triple-barrier
        # labels; ``backtest`` carries a harness's real-execution outcome
        # (S-MLOPT-S6-FU-2) — all three ride the TRAIN side of ``live_holdout``
        # (``is_live_trade=False``). ``live`` carries the real PnL outcome and
        # is the only eval side. Added MB-20260603-002 (S-MLOPT-S6 follow-up)
        # so the meta-label can be re-evaluated when the training distribution
        # matches real setups instead of CUSUM momentum events.
        "event_source": str,
    }

    def iter_rows(
        self,
        *,
        market_raw_path: Path | str | None = None,
        market_raw_paths: list[Path | str] | str | None = None,
        vol_window_n: int = 20,
        momentum_window: int = 10,
        max_holding: int = 10,
        pt_mult: float = 1.0,
        sl_mult: float = 1.0,
        slippage: float = 0.0,
        cusum_threshold_mult: float = 1.0,
        n_vol_buckets: int = 3,
        include_synthetic: bool = True,
        include_cusum: bool | None = None,
        live_trades_db: Path | str | None = None,
        signal_log_db: Path | str | None = None,
        signal_log_strategies: tuple[str, ...] | list[str] | str | None = None,
        signal_log_sides: tuple[str, ...] | list[str] | str | None = None,
        backtest_trades_db: Path | str | None = None,
        include_backtest: bool = False,
        backtest_strategies: tuple[str, ...] | list[str] | str | None = None,
        r_label_threshold: float | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        """Build candidate rows for one OR several symbols (S-MLOPT-S8).

        Pass a single `market_raw_path`, or `market_raw_paths` (a list, or a
        comma-separated string from the build CLI) to build a **joint
        cross-symbol** dataset (BTC + MES) — each symbol's bars are CUSUM-sampled
        + vol-bucketed against its OWN distribution (BTC and MES volatilities
        differ), then concatenated; the `symbol` column lets a model condition on
        / transfer across symbols. The smaller-data symbol (MES) borrows
        statistical strength from the larger (BTC).

        ``include_cusum`` is the new (MB-20260603-002) name for the CUSUM toggle;
        the legacy ``include_synthetic`` alias is preserved (it controlled the
        same flag in S5/S6/S7). When both are passed, ``include_cusum`` wins.

        ``backtest_trades_db`` (S-MLOPT-S6-FU-2) reads ``is_backtest=1`` rows
        recorded by ``ml.datasets.backtest_recorder`` and emits them tagged
        ``event_source="backtest"`` (train side, real-execution labels).
        ``include_backtest=True`` is the S7-style convenience: when
        ``backtest_trades_db`` is not given it reads the backtest rows from
        ``live_trades_db`` (the single-DB flow the S7 recorder demo used, where
        one journal carries both ``is_backtest=0`` real rows and ``is_backtest=1``
        recorded rows). ``backtest_strategies`` restricts to a subset.

        ``r_label_threshold`` (M23 variant C1) — when set, every emitted row also
        carries ``won_r = 1[r_multiple >= r_label_threshold]``, an R-aware training
        target a meta-label manifest can point at instead of the binary ``won``
        (pnl>0). The exact-R EV gate showed the P(win) head ranks win-probability
        but not loss-magnitude; ``won_r`` teaches it to prefer trades that clear a
        materially-good R. Leaves ``won`` untouched (still the reporting truth).
        """
        if vol_window_n < 2:
            raise ValueError(f"vol_window_n must be >= 2; got {vol_window_n}")
        if momentum_window < 1:
            raise ValueError(f"momentum_window must be >= 1; got {momentum_window}")
        if cusum_threshold_mult <= 0:
            raise ValueError(
                f"cusum_threshold_mult must be > 0; got {cusum_threshold_mult}"
            )
        config = BarrierConfig(
            pt_mult=pt_mult, sl_mult=sl_mult,
            max_holding=max_holding, slippage=slippage,
        )
        emit_cusum = include_cusum if include_cusum is not None else include_synthetic
        strat_tuple = _normalise_str_tuple(signal_log_strategies)
        sides_tuple = _normalise_str_tuple(signal_log_sides) or ("buy", "sell")
        bt_strat_tuple = _normalise_str_tuple(backtest_strategies)
        # S7-style: include_backtest reuses live_trades_db when no dedicated
        # backtest DB is given (one journal with both is_backtest 0/1 rows).
        backtest_db = backtest_trades_db
        if backtest_db is None and include_backtest:
            backtest_db = live_trades_db
        # M23 variant C1 (docs/research/M23-phase1-variantC-DESIGN-2026-07-17.md):
        # when a threshold is given, emit an R-aware TRAINING target
        # `won_r = 1[r_multiple >= r_label_threshold]` alongside the binary `won`
        # (pnl>0) — so a meta-label manifest can target `won_r` and learn to pick
        # trades that clear a materially-good R, not just trades that win. The EV
        # gate still reports real win-rate (`won`) + realized R (`r_multiple`), so
        # a wrong `won_r` can't corrupt the eval — it only changes the train
        # target. Computed once here (all four event sources carry `r_multiple`).
        thr = float(r_label_threshold) if r_label_threshold is not None else None
        paths = _resolve_market_raw_paths(market_raw_path, market_raw_paths)
        for path in paths:
            for row in self._iter_one_symbol(
                path, config=config, vol_window_n=vol_window_n,
                momentum_window=momentum_window,
                cusum_threshold_mult=cusum_threshold_mult,
                n_vol_buckets=n_vol_buckets, include_cusum=emit_cusum,
                live_trades_db=live_trades_db,
                signal_log_db=signal_log_db,
                signal_log_strategies=strat_tuple,
                signal_log_sides=sides_tuple,
                backtest_trades_db=backtest_db,
                backtest_strategies=bt_strat_tuple,
            ):
                if thr is not None:
                    rv = row.get("r_multiple")
                    row["won_r"] = (
                        1 if isinstance(rv, (int, float)) and rv >= thr else 0
                    )
                yield row

    def _iter_one_symbol(
        self,
        market_raw_path: Path | str,
        *,
        config: BarrierConfig,
        vol_window_n: int,
        momentum_window: int,
        cusum_threshold_mult: float,
        n_vol_buckets: int,
        include_cusum: bool,
        live_trades_db: Path | str | None,
        signal_log_db: Path | str | None = None,
        signal_log_strategies: tuple[str, ...] | None = None,
        signal_log_sides: tuple[str, ...] = ("buy", "sell"),
        backtest_trades_db: Path | str | None = None,
        backtest_strategies: tuple[str, ...] | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        max_holding = config.max_holding
        rows = _load_market_raw_rows(Path(market_raw_path))
        rows.sort(key=lambda r: r.get("ts", ""))
        n = len(rows)
        if n < vol_window_n + max_holding + 2:
            return  # not enough bars for one past window + one forward window

        closes = [float(r["close"]) for r in rows]
        highs = [float(r["high"]) for r in rows]
        lows = [float(r["low"]) for r in rows]
        opens = [float(r["open"]) for r in rows]

        # Past-only log returns + rolling vol.
        log_returns: list[float | None] = [None] * n
        for i in range(1, n):
            if closes[i - 1] > 0 and closes[i] > 0:
                log_returns[i] = math.log(closes[i] / closes[i - 1])
        past_vols: list[float | None] = [None] * n
        for i in range(n):
            window = [v for v in log_returns[max(0, i - vol_window_n + 1): i + 1]
                      if v is not None]
            if len(window) >= 2:
                past_vols[i] = statistics.pstdev(window)

        complete_vols = [v for v in past_vols if v is not None]
        if not complete_vols:
            return
        boundaries, bucket_labels = _quantile_buckets(complete_vols, n_vol_buckets)
        # Adaptive CUSUM threshold: scale to the typical bar volatility so the
        # event count tracks the market's activity rather than a magic constant.
        median_vol = statistics.median(complete_vols)
        threshold = cusum_threshold_mult * median_vol
        if threshold <= 0:
            return

        # --- Synthetic candidates: CUSUM events → triple-barrier labels ---
        if include_cusum:
            for e, side in cusum_events(log_prices(closes), threshold):
                entry_idx = e + 1
                if entry_idx >= n:
                    continue  # no next bar to enter on
                vol = past_vols[e]
                if vol is None or vol <= 0 or log_returns[e] is None:
                    continue  # signal bar lacks a complete past window
                entry_price = opens[entry_idx]
                outcome = label_event(
                    highs, lows, closes,
                    entry_idx=entry_idx, entry_price=entry_price,
                    direction=side, vol=vol, config=config,
                )
                if outcome is None:
                    continue
                yield {
                    **_feature_fields(rows, e, log_returns, vol,
                                      boundaries, bucket_labels, momentum_window,
                                      opens=opens, highs=highs, lows=lows,
                                      closes=closes, vol_window_n=vol_window_n),
                    "direction": int(side),
                    "entry_price": float(entry_price),
                    "barrier_touched": outcome.barrier,
                    "label": int(outcome.label),
                    "won": 1 if outcome.label > 0 else 0,
                    "r_multiple": float(outcome.r_multiple),
                    "ret": float(outcome.ret),
                    "holding_bars": int(outcome.holding_bars),
                    "is_live_trade": False,
                    "event_source": "cusum",
                }

        # --- Signal-log candidates: strategies' real decision points ---
        # MB-20260603-002 (S-MLOPT-S6 follow-up). Each ``buy``/``sell`` row in
        # ``trade_journal.db::signals`` (the audit-log dual-write) is located
        # at the bar covering its ``logged_at_utc`` and labeled with the SAME
        # triple-barrier as the CUSUM candidates (same ``BarrierConfig``, same
        # local-vol sizing, same fill rules). The label is synthetic but the
        # *event distribution* is the strategies' actual setups — which is the
        # train↔eval domain gap the S6 eval surfaced. These rows ride the
        # train side of ``live_holdout`` (``is_live_trade=False``); the real
        # trades remain the only eval-side population.
        if signal_log_db is not None:
            symbol = str(rows[0].get("symbol", "")) if rows else ""
            bar_ts = [str(r.get("ts", "")) for r in rows]
            events = _load_signal_log_events(
                signal_log_db, symbol,
                sides=signal_log_sides,
                strategies=signal_log_strategies,
            )
            for ev in events:
                e = _bar_index_at_or_before(bar_ts, ev["event_ts"])
                if e is None:
                    continue
                entry_idx = e + 1
                if entry_idx >= n:
                    continue
                vol = past_vols[e]
                if vol is None or vol <= 0 or log_returns[e] is None:
                    continue
                entry_price = opens[entry_idx]
                outcome = label_event(
                    highs, lows, closes,
                    entry_idx=entry_idx, entry_price=entry_price,
                    direction=ev["direction"], vol=vol, config=config,
                )
                if outcome is None:
                    continue
                yield {
                    **_feature_fields(rows, e, log_returns, vol,
                                      boundaries, bucket_labels, momentum_window,
                                      opens=opens, highs=highs, lows=lows,
                                      closes=closes, vol_window_n=vol_window_n),
                    "direction": int(ev["direction"]),
                    "entry_price": float(entry_price),
                    "barrier_touched": outcome.barrier,
                    "label": int(outcome.label),
                    "won": 1 if outcome.label > 0 else 0,
                    "r_multiple": float(outcome.r_multiple),
                    "ret": float(outcome.ret),
                    "holding_bars": int(outcome.holding_bars),
                    "is_live_trade": False,
                    "event_source": "signal_log",
                }

        # --- Backtest candidates: harness-labeled real-execution outcomes ---
        # S-MLOPT-S6-FU-2. The S6 + S6-FU evals showed the synthetic
        # triple-barrier LABEL (not the event sampler) is the train↔eval gap:
        # CUSUM (win 0.457) and signal-log (0.469) share one easy synthetic
        # distribution, while the real trades (0.244) live in a harder
        # real-execution one. The strategies' standalone backtest harnesses model
        # real slippage + each strategy's actual entry rule + per-strategy exit
        # logic, so each backtest trade is a real-distribution label at a real
        # signal time. Recorded as ``is_backtest=1`` rows by
        # ``ml.datasets.backtest_recorder`` and read back here tagged
        # ``event_source="backtest"`` + ``is_live_trade=False`` so they ride the
        # TRAIN side of ``live_holdout`` while the REAL trades remain the only
        # eval-side population (apples-to-apples backtest-train + real-eval).
        if backtest_trades_db is not None:
            symbol = str(rows[0].get("symbol", "")) if rows else ""
            bar_ts = [str(r.get("ts", "")) for r in rows]  # already sorted
            for tr in _load_backtest_trades(
                backtest_trades_db, symbol, strategies=backtest_strategies,
            ):
                e = _bar_index_at_or_before(bar_ts, tr["entry_ts"])
                if e is None:
                    continue
                vol = past_vols[e]
                if vol is None or vol <= 0:
                    continue
                pnl = tr["pnl"]
                won = 1 if (pnl is not None and float(pnl) > 0) else 0
                pnl_pct = tr["pnl_percent"]
                ret = float(pnl_pct) / 100.0 if pnl_pct is not None else 0.0
                # The recorder stores realized R in ``pnl`` (R proxy) and
                # ``r_multiple * risk_pct`` in ``pnl_percent`` — so a backtest
                # row's R is ``pnl`` directly (unlike live rows, where the real
                # stop distance isn't reconstructed and r_multiple stays 0.0).
                r_multiple = float(pnl) if pnl is not None else 0.0
                yield {
                    **_feature_fields(rows, e, log_returns, vol,
                                      boundaries, bucket_labels, momentum_window,
                                      opens=opens, highs=highs, lows=lows,
                                      closes=closes, vol_window_n=vol_window_n),
                    "direction": int(tr["direction"]),
                    "entry_price": float(closes[e]),
                    "barrier_touched": "backtest",
                    "label": 1 if won else -1,
                    "won": won,
                    "r_multiple": r_multiple,
                    "ret": ret,
                    "holding_bars": 0,
                    "is_live_trade": False,
                    "event_source": "backtest",
                }

        # --- Real trades: the held-out live population (domain-shift eval) ---
        # Each REAL closed trade is located at the bar covering its entry time
        # and emitted in the SAME feature space (past-only features from that
        # bar) with its ACTUAL realized outcome — never a synthetic barrier — so
        # a model trained on synthetic rows can be scored on real ones
        # (`split_strategy: live_holdout`).
        if live_trades_db is not None:
            symbol = str(rows[0].get("symbol", "")) if rows else ""
            bar_ts = [str(r.get("ts", "")) for r in rows]  # already sorted
            for tr in _load_live_trades(live_trades_db, symbol):
                e = _bar_index_at_or_before(bar_ts, tr["entry_ts"])
                if e is None:
                    continue
                vol = past_vols[e]
                if vol is None or vol <= 0:
                    continue
                pnl = tr["pnl"]
                won = 1 if (pnl is not None and float(pnl) > 0) else 0
                pnl_pct = tr["pnl_percent"]
                ret = float(pnl_pct) / 100.0 if pnl_pct is not None else 0.0
                # Reconstruct realized R from the trade's own risk (stop
                # distance × size) so the EV gate is exact — was a hardcoded
                # 0.0 placeholder that the scorer mistook for a real 0R outcome
                # (MB-20260717-M23-LIVEROW-REALIZED-R). Falls back to signed
                # unit-R when the risk columns are absent.
                r_multiple, r_source = _live_realized_r(tr, won)
                yield {
                    **_feature_fields(rows, e, log_returns, vol,
                                      boundaries, bucket_labels, momentum_window,
                                      opens=opens, highs=highs, lows=lows,
                                      closes=closes, vol_window_n=vol_window_n),
                    "direction": int(tr["direction"]),
                    "entry_price": float(closes[e]),
                    "barrier_touched": "live",
                    "label": 1 if won else -1,
                    "won": won,
                    "r_multiple": r_multiple,
                    "r_multiple_source": r_source,
                    "ret": ret,
                    "holding_bars": 0,
                    "is_live_trade": True,
                    "event_source": "live",
                }
