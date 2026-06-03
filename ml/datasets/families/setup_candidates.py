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
    `{entry_ts, direction(±1), pnl, pnl_percent}` newest-first. Best-effort: a
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
        sql = (
            "SELECT timestamp, direction, pnl, pnl_percent FROM trades "
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
        out.append({
            "entry_ts": str(r["timestamp"] or ""),
            "direction": direction,
            "pnl": r["pnl"],
            "pnl_percent": r["pnl_percent"],
        })
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
) -> dict[str, Any]:
    """Signal-time (past-only) feature fields shared by synthetic + live rows.

    Computed from bar ``e`` and the inclusive past window only — identical for a
    CUSUM-sampled synthetic candidate and a REAL trade located at bar ``e``, so
    both populations live in one feature space (the live holdout is comparable)."""
    log_ret = log_returns[e]
    hour_of_day, dayofweek = _parse_ts_hour_dow(str(rows[e].get("ts", "")))
    lag_1 = log_returns[e - 1] if e - 1 >= 0 else None
    lag_2 = log_returns[e - 2] if e - 2 >= 0 else None
    momentum = float(sum(
        v for v in log_returns[max(0, e - momentum_window + 1): e + 1]
        if v is not None
    ))
    return {
        "ts": str(rows[e].get("ts", "")),
        "symbol": str(rows[e].get("symbol", "")),
        "timeframe": str(rows[e].get("timeframe", "")),
        "source": str(rows[e].get("source", "")),
        "signal_vol": float(vol),
        "log_return": float(log_ret) if log_ret is not None else 0.0,
        "rolling_log_return_vol": float(vol),
        "vol_bucket": _bucket_for(vol, boundaries, bucket_labels),
        "momentum": momentum,
        "hour_of_day": int(hour_of_day),
        "dayofweek": int(dayofweek),
        "log_return_lag_1": float(lag_1) if lag_1 is not None else 0.0,
        "log_return_lag_2": float(lag_2) if lag_2 is not None else 0.0,
    }


class SetupCandidatesBuilder(DatasetBuilder):
    family: ClassVar[str] = _FAMILY
    builder_version: ClassVar[str] = "v1"
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
        "momentum": float,           # cum log-return over the momentum window
        "hour_of_day": int,
        "dayofweek": int,
        "log_return_lag_1": float,
        "log_return_lag_2": float,
        # triple-barrier label (future-only) — outcome columns, NOT features
        "barrier_touched": str,      # 'tp' | 'sl' | 'timeout' | 'live' (real trade)
        "label": int,                # +1 tp, -1 sl, sign(ret) at timeout
        "won": int,                  # 1 if label > 0 else 0 (meta-label target)
        "r_multiple": float,         # ret / stop-distance (risk units)
        "ret": float,                # direction-signed net return
        "holding_bars": int,
        # real-vs-synthetic split flag (domain-shift discipline)
        "is_live_trade": bool,
    }

    def iter_rows(
        self,
        *,
        market_raw_path: Path | str,
        vol_window_n: int = 20,
        momentum_window: int = 10,
        max_holding: int = 10,
        pt_mult: float = 1.0,
        sl_mult: float = 1.0,
        slippage: float = 0.0,
        cusum_threshold_mult: float = 1.0,
        n_vol_buckets: int = 3,
        include_synthetic: bool = True,
        live_trades_db: Path | str | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
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
        if include_synthetic:
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
                                      boundaries, bucket_labels, momentum_window),
                    "direction": int(side),
                    "entry_price": float(entry_price),
                    "barrier_touched": outcome.barrier,
                    "label": int(outcome.label),
                    "won": 1 if outcome.label > 0 else 0,
                    "r_multiple": float(outcome.r_multiple),
                    "ret": float(outcome.ret),
                    "holding_bars": int(outcome.holding_bars),
                    "is_live_trade": False,
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
                yield {
                    **_feature_fields(rows, e, log_returns, vol,
                                      boundaries, bucket_labels, momentum_window),
                    "direction": int(tr["direction"]),
                    "entry_price": float(closes[e]),
                    "barrier_touched": "live",
                    "label": 1 if won else -1,
                    "won": won,
                    "r_multiple": 0.0,  # real stop distance not reconstructed here
                    "ret": ret,
                    "holding_bars": 0,
                    "is_live_trade": True,
                }
