"""`exit_candidates` dataset family — exit-management ML feasibility (P0).

Implements the P0 dataset of
``docs/research/exit-management-ml-experiment-DESIGN.md`` (Framing A —
optimal-exit classifier). Where ``setup_candidates`` asks *"should I OPEN this
setup?"* (one row per signal bar), this family asks *"now that I'm IN a trade,
should I keep HOLDING from here?"* — **one row per in-trade bar**.

## How it works

For each (synthetic or real) trade, walk its in-trade bars; at each sampled
in-trade bar ``t`` emit:

1. **Position-state features (past-only, the NEW signal vs the entry problem):**
   - ``unrealized_r``     — current mark-to-market in R (risk units), signed by
     the trade direction.
   - ``bars_held``        — bars since entry (entry bar = 0).
   - ``mfe_r_so_far``     — max favorable excursion so far, in R (≥ 0).
   - ``mae_r_so_far``     — max adverse excursion so far, in R (≤ 0).
   - ``dist_to_stop_atr`` — distance from the current close to the stop, in ATR
     (vol) units (≥ 0 while the stop is intact).
   - ``dist_to_target_atr`` — distance from the current close to the +β·risk
     target, in ATR units.

2. **Market features (past-only, reused from the market-bar feature space so
   live==train):** ``log_return`` / ``rolling_log_return_vol`` / ``vol_bucket``
   / the four range-based vol estimators / ``momentum`` / ``hour_of_day`` /
   ``dayofweek`` / ``log_return_lag_{1,2}`` — exactly the
   ``market_features`` / ``setup_candidates`` columns, computed from the
   inclusive past window ``[t - vol_window_n + 1 .. t]`` only.

3. **Label ``should_hold ∈ {0,1}`` (future-only):** a triple-barrier from bar
   ``t`` over the REMAINING horizon — does price reach a favorable +β·risk level
   (``hold_target_mult * vol``) before the trade's stop within ``hold_horizon``
   bars? ``1`` ⇔ holding from ``t`` is worth it (the favorable barrier wins),
   ``0`` otherwise (the stop / an adverse timeout). Computed with
   ``ml.datasets.labeling.label_event`` — the SAME labeler ``setup_candidates``
   uses, applied from the *in-trade* bar instead of the entry bar.

## Leakage discipline (PASSED by construction)

An in-trade row at bar ``t`` carries features from the inclusive **past** window
``[.. t]`` only (position state accumulated over ``[entry .. t]``; market
features over ``[t - vol_window_n + 1 .. t]``). The label comes from the
**future** path ``[t + 1 .. t + 1 + hold_horizon]`` (the forward race entered at
``t + 1``'s open). The two windows never overlap → a feature cannot leak the
label. This is unit-tested (`tests/test_exit_candidates.py`): mutate any future
bar and the features at ``t`` are byte-identical; mutate any past bar and the
label is unchanged.

A trainer consuming this family MUST scope ``feature_columns`` to exclude the
outcome columns (``should_hold`` / ``label`` / ``fwd_r_multiple`` / ``fwd_ret``
/ ``barrier_touched`` / ``hold_bars`` / ``is_live_trade``) — declared in the
manifest's ``forbidden_features`` so the trainer's leakage guard catches a
mistake.

## Synthetic vs live arm (domain-shift discipline)

The **synthetic** arm (default) manufactures hypothetical trades from bar
history for volume: CUSUM-sample an entry event (the breach side = direction),
size an ATR-scaled stop + a +β·risk target, then sample the in-trade bars. These
rows are ``is_live_trade=False`` / ``event_source="synthetic"`` and ride the
TRAIN side of ``live_holdout``.

Pass ``live_trades_db=<trade_journal.db>`` (mirrors ``setup_candidates``) to
ALSO reconstruct the in-trade bars of REAL closed trades — located on the candle
data by their entry/exit timestamps, same feature/label computation — tagged
``is_live_trade=True`` / ``event_source="live"``. The ``live_holdout`` split then
trains on synthetic in-trade rows and evaluates on the real ones — the mandatory
domain-shift check. Set ``include_synthetic=False`` to emit only the real rows.

The live arm degrades cleanly: a missing/empty journal, a trade whose
entry/exit bars can't be located on this symbol's candles, or a row missing a
usable stop is skipped — never raised — exactly like ``setup_candidates``'s
live arm.

## Determinism

Pure stdlib + the existing labeling/vol modules; no randomness. The synthetic
candidate set is a deterministic function of the candles + knobs (the CUSUM
event stream is deterministic), so a given (dataset, knobs) always yields the
same rows. The ``seed`` kwarg is accepted for interface parity / future
stochastic sampling but is not consulted by the deterministic path.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
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

# setup_candidates owns the market_raw row loader, the quantile bucketing, the
# ts→(hour, dow) parse, and the bar-index lookup; reuse them verbatim so the
# market-feature half is identical to the entry family's (one feature space).
from .setup_candidates import (
    _bar_index_at_or_before,
    _bucket_for,
    _load_market_raw_rows,
    _parse_ts_hour_dow,
    _quantile_buckets,
    _resolve_market_raw_paths,
)

_FAMILY = "exit_candidates"


def _direction_pm1(side: str) -> int:
    s = str(side or "").lower()
    return -1 if s in ("sell", "short", "-1") else 1


def _load_live_trades_for_exit(
    db_path: Path | str, symbol: str
) -> list[dict[str, Any]]:
    """REAL closed (non-backtest, non-demo) trades for one symbol, with the
    fields needed to reconstruct in-trade bars.

    Mirrors ``setup_candidates._load_live_trades`` but additionally pulls the
    geometry the exit walk needs: the entry/exit timestamps, the entry price,
    and the stop (``stop_loss``; ``sl`` fallback) so the risk distance is real,
    not synthetic. ``closed_at`` is preferred for the exit time, falling back to
    the order-package-less ``timestamp`` only when absent. Best-effort: a
    missing DB / table / column returns ``[]``.

    Returns ``{entry_ts, exit_ts, direction(±1), entry_price, stop_loss}``
    time-sorted ascending by entry.
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
        # ``stop_loss`` is the canonical journal column; some seed/legacy rows
        # carry ``sl`` instead — COALESCE both, NULL when neither present.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        sl_expr = (
            "COALESCE(stop_loss, sl)"
            if "stop_loss" in cols and "sl" in cols
            else "stop_loss" if "stop_loss" in cols
            else "sl" if "sl" in cols
            else "NULL"
        )
        closed_expr = "closed_at" if "closed_at" in cols else "NULL"
        sql = (
            f"SELECT timestamp, {closed_expr} AS closed_at, direction, "
            f"entry_price, {sl_expr} AS stop_loss "
            "FROM trades WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
            "AND COALESCE(is_demo,0)=0 AND symbol=? "
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
        entry_price = r["entry_price"]
        if entry_price is None:
            continue
        out.append({
            "entry_ts": str(r["timestamp"] or ""),
            "exit_ts": str(r["closed_at"] or "") or str(r["timestamp"] or ""),
            "direction": _direction_pm1(r["direction"]),
            "entry_price": float(entry_price),
            "stop_loss": (
                float(r["stop_loss"]) if r["stop_loss"] is not None else None
            ),
        })
    return out


class ExitCandidatesBuilder(DatasetBuilder):
    family: ClassVar[str] = _FAMILY
    builder_version: ClassVar[str] = "v1"
    # Window separation (position+market features past-only, hold label
    # future-only) makes leakage impossible by construction — the same
    # guarantee market_features / setup_candidates stamp.
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.PASSED
    label_version: ClassVar[str] = "exit-hold-triple-barrier-v1"
    schema: ClassVar[Mapping[str, type]] = {
        # identity / context
        "ts": str,
        "symbol": str,
        "timeframe": str,
        "source": str,
        "direction": int,            # +1 long trade, -1 short trade
        # --- position-state features (past-only, the exit-specific signal) ---
        "unrealized_r": float,       # current MTM in R, direction-signed
        "bars_held": int,            # bars since entry (entry bar = 0)
        "mfe_r_so_far": float,       # max favorable excursion so far, in R (>=0)
        "mae_r_so_far": float,       # max adverse excursion so far, in R (<=0)
        "dist_to_stop_atr": float,   # close→stop distance in vol/ATR units
        "dist_to_target_atr": float, # close→+beta target distance in vol units
        # --- market features (past-only, reused from market-bar feature space) ---
        "log_return": float,
        "rolling_log_return_vol": float,
        "vol_bucket": str,
        "parkinson_vol": float,
        "garman_klass_vol": float,
        "rogers_satchell_vol": float,
        "yang_zhang_vol": float,
        "momentum": float,
        "hour_of_day": int,
        "dayofweek": int,
        "log_return_lag_1": float,
        "log_return_lag_2": float,
        # --- forward hold label (future-only) — outcome cols, NOT features ---
        "should_hold": int,          # 1 if the favorable barrier wins from t
        "barrier_touched": str,      # 'tp'|'sl'|'timeout' (synthetic/live)
        "label": int,                # +1 favorable, -1 adverse, sign at timeout
        "fwd_r_multiple": float,     # forward outcome ret / stop-dist (R units)
        "fwd_ret": float,            # direction-signed forward net return
        "hold_bars": int,            # bars to the forward barrier resolution
        # --- domain-shift split flag + sampler tag ---
        "is_live_trade": bool,
        "event_source": str,         # 'synthetic' | 'live'
    }

    def iter_rows(
        self,
        *,
        market_raw_path: Path | str | None = None,
        market_raw_paths: list[Path | str] | str | None = None,
        vol_window_n: int = 20,
        momentum_window: int = 10,
        # entry geometry (synthetic arm)
        cusum_threshold_mult: float = 1.0,
        sl_mult: float = 1.0,          # stop = sl_mult * vol below/above entry
        target_beta: float = 2.0,      # take-profit at +target_beta * risk
        max_trade_bars: int = 30,      # cap on synthetic in-trade horizon
        in_trade_sample_step: int = 1, # sample every Nth in-trade bar
        # forward hold-label geometry
        hold_horizon: int = 10,        # remaining-horizon bars for the label
        hold_target_mult: float | None = None,  # favorable barrier; default beta*sl
        slippage: float = 0.0,
        n_vol_buckets: int = 3,
        include_synthetic: bool = True,
        live_trades_db: Path | str | None = None,
        seed: int = 42,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        """Build in-trade exit-candidate rows for one OR several symbols.

        Knobs (β / H called out in the design):

        - ``sl_mult`` — the stop distance in local-vol units (the "ATR" risk
          unit ``unrealized_r`` / the excursions / ``dist_to_stop_atr`` are
          measured in). Entry risk = ``sl_mult * vol``.
        - ``target_beta`` — the synthetic trade's take-profit at ``+β · risk``
          (so a synthetic trade has a 1:β stop:target geometry).
        - ``hold_horizon`` (H) — how many forward bars the ``should_hold``
          triple-barrier races from each in-trade bar.
        - ``hold_target_mult`` — the favorable-barrier distance for the hold
          label, in vol units; defaults to ``target_beta * sl_mult`` so "worth
          holding" means "can still reach a +β·risk move before the stop".
        - ``max_trade_bars`` / ``in_trade_sample_step`` — bound + thin the
          per-trade in-trade-bar sampling so one long trade doesn't dominate.
        """
        if vol_window_n < 2:
            raise ValueError(f"vol_window_n must be >= 2; got {vol_window_n}")
        if momentum_window < 1:
            raise ValueError(f"momentum_window must be >= 1; got {momentum_window}")
        if cusum_threshold_mult <= 0:
            raise ValueError(
                f"cusum_threshold_mult must be > 0; got {cusum_threshold_mult}"
            )
        if sl_mult <= 0:
            raise ValueError(f"sl_mult must be > 0; got {sl_mult}")
        if target_beta <= 0:
            raise ValueError(f"target_beta must be > 0; got {target_beta}")
        if max_trade_bars < 1:
            raise ValueError(f"max_trade_bars must be >= 1; got {max_trade_bars}")
        if in_trade_sample_step < 1:
            raise ValueError(
                f"in_trade_sample_step must be >= 1; got {in_trade_sample_step}"
            )
        if hold_horizon < 1:
            raise ValueError(f"hold_horizon must be >= 1; got {hold_horizon}")

        favorable_mult = (
            hold_target_mult if hold_target_mult is not None
            else target_beta * sl_mult
        )
        if favorable_mult <= 0:
            raise ValueError(
                f"hold_target_mult must be > 0; got {favorable_mult}"
            )
        # The hold label is a triple-barrier with the favorable (TP) barrier at
        # +favorable_mult * vol and the stop (SL) barrier at sl_mult * vol — the
        # same labeler setup_candidates uses, applied from the in-trade bar.
        hold_config = BarrierConfig(
            pt_mult=favorable_mult, sl_mult=sl_mult,
            max_holding=hold_horizon, slippage=slippage,
        )

        paths = _resolve_market_raw_paths(market_raw_path, market_raw_paths)
        for path in paths:
            yield from self._iter_one_symbol(
                path,
                vol_window_n=vol_window_n,
                momentum_window=momentum_window,
                cusum_threshold_mult=cusum_threshold_mult,
                sl_mult=sl_mult,
                target_beta=target_beta,
                max_trade_bars=max_trade_bars,
                in_trade_sample_step=in_trade_sample_step,
                hold_config=hold_config,
                n_vol_buckets=n_vol_buckets,
                include_synthetic=include_synthetic,
                live_trades_db=live_trades_db,
            )

    # ------------------------------------------------------------------ #
    # per-symbol build
    # ------------------------------------------------------------------ #
    def _iter_one_symbol(
        self,
        market_raw_path: Path | str,
        *,
        vol_window_n: int,
        momentum_window: int,
        cusum_threshold_mult: float,
        sl_mult: float,
        target_beta: float,
        max_trade_bars: int,
        in_trade_sample_step: int,
        hold_config: BarrierConfig,
        n_vol_buckets: int,
        include_synthetic: bool,
        live_trades_db: Path | str | None,
    ) -> Iterator[Mapping[str, Any]]:
        rows = _load_market_raw_rows(Path(market_raw_path))
        rows.sort(key=lambda r: r.get("ts", ""))
        n = len(rows)
        # Need at least one entry + one in-trade bar + one forward window.
        if n < vol_window_n + hold_config.max_holding + 3:
            return

        closes = [float(r["close"]) for r in rows]
        highs = [float(r["high"]) for r in rows]
        lows = [float(r["low"]) for r in rows]
        opens = [float(r["open"]) for r in rows]

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
        median_vol = statistics.median(complete_vols)
        threshold = cusum_threshold_mult * median_vol
        if threshold <= 0:
            return

        ctx = _SymbolContext(
            rows=rows, n=n, opens=opens, highs=highs, lows=lows, closes=closes,
            log_returns=log_returns, past_vols=past_vols,
            boundaries=boundaries, bucket_labels=bucket_labels,
            vol_window_n=vol_window_n, momentum_window=momentum_window,
            sl_mult=sl_mult, hold_config=hold_config,
            max_trade_bars=max_trade_bars,
            in_trade_sample_step=in_trade_sample_step,
        )

        # --- Synthetic arm: CUSUM entries → walk in-trade bars ---
        if include_synthetic:
            for e, side in cusum_events(log_prices(closes), threshold):
                entry_idx = e + 1
                if entry_idx >= n:
                    continue
                entry_vol = past_vols[e]
                if entry_vol is None or entry_vol <= 0:
                    continue
                entry_price = opens[entry_idx]
                if entry_price <= 0:
                    continue
                risk = sl_mult * entry_vol  # fractional stop distance
                if risk <= 0:
                    continue
                # Synthetic stop price = entry_price * (1 -/+ risk).
                stop_price = (
                    entry_price * (1.0 - risk) if side == 1
                    else entry_price * (1.0 + risk)
                )
                # End the synthetic trade when its own stop or +beta target is
                # hit, or at max_trade_bars — bounding the in-trade window.
                trade_end = self._synthetic_trade_end(
                    ctx, entry_idx=entry_idx, direction=side,
                    entry_price=entry_price, risk=risk, target_beta=target_beta,
                )
                yield from self._walk_in_trade_bars(
                    ctx,
                    entry_idx=entry_idx, trade_end=trade_end, direction=side,
                    entry_price=entry_price, stop_price=stop_price, risk=risk,
                    is_live=False, event_source="synthetic",
                )

        # --- Live arm: reconstruct real closed trades' in-trade bars ---
        if live_trades_db is not None:
            symbol = str(rows[0].get("symbol", "")) if rows else ""
            bar_ts = [str(r.get("ts", "")) for r in rows]
            for tr in _load_live_trades_for_exit(live_trades_db, symbol):
                entry_idx_raw = _bar_index_at_or_before(bar_ts, tr["entry_ts"])
                if entry_idx_raw is None:
                    continue
                # Enter on the bar at/after the entry ts (no pre-entry bar).
                entry_idx = entry_idx_raw
                if entry_idx >= n:
                    continue
                entry_vol = past_vols[entry_idx]
                if entry_vol is None or entry_vol <= 0:
                    continue
                entry_price = tr["entry_price"]
                if entry_price <= 0:
                    continue
                # Real stop → real risk distance. Fall back to the synthetic
                # ATR-scaled stop only when the journal row has no usable stop,
                # so unrealized_r / excursions are still well-defined.
                stop_price = tr["stop_loss"]
                if stop_price is not None and stop_price > 0:
                    risk = abs(entry_price - stop_price) / entry_price
                else:
                    risk = sl_mult * entry_vol
                    stop_price = (
                        entry_price * (1.0 - risk) if tr["direction"] == 1
                        else entry_price * (1.0 + risk)
                    )
                if risk <= 0:
                    continue
                # End the in-trade walk at the bar covering the exit ts (or the
                # max-bars cap), so we replay only the bars the trade was open.
                exit_idx = _bar_index_at_or_before(bar_ts, tr["exit_ts"])
                trade_end = exit_idx if exit_idx is not None and exit_idx >= entry_idx \
                    else entry_idx
                trade_end = min(trade_end, entry_idx + ctx.max_trade_bars)
                yield from self._walk_in_trade_bars(
                    ctx,
                    entry_idx=entry_idx, trade_end=trade_end,
                    direction=tr["direction"], entry_price=entry_price,
                    stop_price=stop_price, risk=risk,
                    is_live=True, event_source="live",
                )

    # ------------------------------------------------------------------ #
    # synthetic trade-end resolution
    # ------------------------------------------------------------------ #
    @staticmethod
    def _synthetic_trade_end(
        ctx: "_SymbolContext",
        *,
        entry_idx: int,
        direction: int,
        entry_price: float,
        risk: float,
        target_beta: float,
    ) -> int:
        """Last in-trade bar index of a synthetic trade — where its OWN stop or
        +β·risk target is first touched, else ``entry_idx + max_trade_bars``
        (clamped to the series). Bounds how far the in-trade walk runs."""
        tp_ret = target_beta * risk
        if direction == 1:
            tp_price = entry_price * (1.0 + tp_ret)
            sl_price = entry_price * (1.0 - risk)
        else:
            tp_price = entry_price * (1.0 - tp_ret)
            sl_price = entry_price * (1.0 + risk)
        horizon = min(entry_idx + ctx.max_trade_bars, ctx.n - 1)
        for j in range(entry_idx, horizon + 1):
            hi, lo = ctx.highs[j], ctx.lows[j]
            if direction == 1:
                if lo <= sl_price or hi >= tp_price:
                    return j
            else:
                if hi >= sl_price or lo <= tp_price:
                    return j
        return horizon

    # ------------------------------------------------------------------ #
    # the in-trade walk
    # ------------------------------------------------------------------ #
    def _walk_in_trade_bars(
        self,
        ctx: "_SymbolContext",
        *,
        entry_idx: int,
        trade_end: int,
        direction: int,
        entry_price: float,
        stop_price: float,
        risk: float,
        is_live: bool,
        event_source: str,
    ) -> Iterator[Mapping[str, Any]]:
        """Emit one row per sampled in-trade bar ``t`` in ``[entry_idx ..
        trade_end]``. Features at ``t`` are past-only; the label is a forward
        triple-barrier from ``t``. Excursions accumulate over ``[entry .. t]``.
        """
        mfe_r = 0.0   # max favorable excursion so far (R, >= 0)
        mae_r = 0.0   # max adverse excursion so far (R, <= 0)
        n = ctx.n
        for t in range(entry_idx, trade_end + 1):
            # Update running excursions with bar t's extremes (past-only — bar t
            # is "now", its high/low are known at its close).
            hi_ret = direction * (ctx.highs[t] / entry_price - 1.0)
            lo_ret = direction * (ctx.lows[t] / entry_price - 1.0)
            fav = max(hi_ret, lo_ret)
            adv = min(hi_ret, lo_ret)
            mfe_r = max(mfe_r, fav / risk)
            mae_r = min(mae_r, adv / risk)

            # Only EMIT on sampled bars + only when a full forward window exists
            # AND a complete past window exists (so features are well-defined).
            if (t - entry_idx) % ctx.in_trade_sample_step != 0:
                continue
            if t + 1 >= n:
                continue
            vol = ctx.past_vols[t]
            if vol is None or vol <= 0 or ctx.log_returns[t] is None:
                continue

            close_t = ctx.closes[t]
            unrealized_r = (direction * (close_t / entry_price - 1.0)) / risk
            # Distances measured in vol/ATR units (>=0 toward the level; the
            # sign of the directional gap to stop is folded out — magnitude in R
            # of how far the stop is from here).
            dist_to_stop = direction * (close_t / stop_price - 1.0) if stop_price > 0 else 0.0
            # +beta target relative to entry, expressed from the current close.
            target_ret = ctx.hold_config.pt_mult * vol
            if direction == 1:
                target_price = entry_price * (1.0 + target_ret)
            else:
                target_price = entry_price * (1.0 - target_ret)
            dist_to_target = direction * (target_price / close_t - 1.0)

            # --- Forward hold label: triple-barrier from bar t over [t+1 ..] ---
            fwd_entry_idx = t + 1
            fwd_entry_price = ctx.opens[fwd_entry_idx]
            if fwd_entry_price <= 0:
                continue
            outcome = label_event(
                ctx.highs, ctx.lows, ctx.closes,
                entry_idx=fwd_entry_idx, entry_price=fwd_entry_price,
                direction=direction, vol=vol, config=ctx.hold_config,
            )
            if outcome is None:
                continue
            should_hold = 1 if outcome.label > 0 else 0

            yield {
                **self._market_features(ctx, t),
                "direction": int(direction),
                # position-state features
                "unrealized_r": float(unrealized_r),
                "bars_held": int(t - entry_idx),
                "mfe_r_so_far": float(mfe_r),
                "mae_r_so_far": float(mae_r),
                "dist_to_stop_atr": float(dist_to_stop / vol) if vol > 0 else 0.0,
                "dist_to_target_atr": float(dist_to_target / vol) if vol > 0 else 0.0,
                # forward hold label
                "should_hold": int(should_hold),
                "barrier_touched": outcome.barrier,
                "label": int(outcome.label),
                "fwd_r_multiple": float(outcome.r_multiple),
                "fwd_ret": float(outcome.ret),
                "hold_bars": int(outcome.holding_bars),
                # split flag + tag
                "is_live_trade": bool(is_live),
                "event_source": event_source,
            }

    # ------------------------------------------------------------------ #
    # market features at bar t (past-only) — mirrors setup_candidates
    # ------------------------------------------------------------------ #
    @staticmethod
    def _market_features(ctx: "_SymbolContext", t: int) -> dict[str, Any]:
        rows = ctx.rows
        log_ret = ctx.log_returns[t]
        vol = ctx.past_vols[t]
        hour_of_day, dayofweek = _parse_ts_hour_dow(str(rows[t].get("ts", "")))
        lag_1 = ctx.log_returns[t - 1] if t - 1 >= 0 else None
        lag_2 = ctx.log_returns[t - 2] if t - 2 >= 0 else None
        momentum = float(sum(
            v for v in ctx.log_returns[max(0, t - ctx.momentum_window + 1): t + 1]
            if v is not None
        ))
        s = max(0, t - ctx.vol_window_n + 1)
        w_open, w_high, w_low, w_close = (
            ctx.opens[s: t + 1], ctx.highs[s: t + 1],
            ctx.lows[s: t + 1], ctx.closes[s: t + 1],
        )
        w_prev_close = [ctx.closes[j - 1] if j - 1 >= 0 else None
                        for j in range(s, t + 1)]
        return {
            "ts": str(rows[t].get("ts", "")),
            "symbol": str(rows[t].get("symbol", "")),
            "timeframe": str(rows[t].get("timeframe", "")),
            "source": str(rows[t].get("source", "")),
            "log_return": float(log_ret) if log_ret is not None else 0.0,
            "rolling_log_return_vol": float(vol) if vol is not None else 0.0,
            "vol_bucket": _bucket_for(
                vol if vol is not None else 0.0, ctx.boundaries, ctx.bucket_labels
            ),
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


class _SymbolContext:
    """Per-symbol precomputed arrays + knobs threaded through the in-trade walk.

    A plain attribute bag (not a dataclass) so the hot walk reads ``ctx.closes``
    etc. without per-row dict lookups; instantiated once per symbol.
    """

    __slots__ = (
        "rows", "n", "opens", "highs", "lows", "closes",
        "log_returns", "past_vols", "boundaries", "bucket_labels",
        "vol_window_n", "momentum_window", "sl_mult", "hold_config",
        "max_trade_bars", "in_trade_sample_step",
    )

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]],
        n: int,
        opens: list[float],
        highs: list[float],
        lows: list[float],
        closes: list[float],
        log_returns: list[float | None],
        past_vols: list[float | None],
        boundaries: list[float],
        bucket_labels: list[str],
        vol_window_n: int,
        momentum_window: int,
        sl_mult: float,
        hold_config: BarrierConfig,
        max_trade_bars: int,
        in_trade_sample_step: int,
    ) -> None:
        self.rows = rows
        self.n = n
        self.opens = opens
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.log_returns = log_returns
        self.past_vols = past_vols
        self.boundaries = boundaries
        self.bucket_labels = bucket_labels
        self.vol_window_n = vol_window_n
        self.momentum_window = momentum_window
        self.sl_mult = sl_mult
        self.hold_config = hold_config
        self.max_trade_bars = max_trade_bars
        self.in_trade_sample_step = in_trade_sample_step


# Re-exported for symmetry with setup_candidates' importable helpers / tests.
__all__ = ["ExitCandidatesBuilder"]
