"""Entry decisions the LIVE unit makes on a FORMING bar — the one implementation.

PI-20260929-VOLSKIP-0001 / -SIGNAL-0002. Live, the pullback and donchian
variant builders (src/runtime/strategy_signal_builders.py
_htf_pullback_variant_builder / _trend_donchian_variant_builder) call the
unit's order_package every ~2 min on a 200-bar frame whose last row is the
still-forming bar. This module reproduces that decision from 1m data and
returns it as the ``entry_override`` mapping both Stage-0 harnesses accept:

    {bar_index: {"direction", "entry", "atr", "tick_min", "rest_high", "rest_low"}}

Consumers — they must not re-implement it:
  * scripts/backtest_pullback.py / scripts/backtest_trend.py
    ``run_backtest(decision_bar="forming", ...)`` (the harness mode)
  * scripts/research/whole_signal_forming_bar_replay.py (RQ-20260929-301)
  * scripts/research/forming_bar_pooled_regrade.py (RQ-20260929-302)

At each tick k = tick_step, 2·tick_step, …, tf − tick_step minutes into bar i
the frame is 199 closed bars + bar i truncated to its first k minutes (open,
running high/low, last close). The first tick whose order_package returns a
package that the builder's side_filter admits enters at that tick's price with
the package's ATR; later ticks of the bar are ignored. ``rest_high/rest_low``
= the bar's range after the entry tick (the harness exits there on a touched
stop/target).

order_package is only called on ticks passing an EXACT necessary direction
pre-filter (close vs the shift(1) midline/range or Donchian channel — neither
depends on the forming row), so the result equals calling it on every tick.
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path[:1]:
    # ROOT FIRST: scripts/ml would otherwise shadow the repo's ml package,
    # which the live builder module imports (ml.datasets).
    sys.path.insert(0, str(ROOT))

WINDOW = 200            # live fetch_candles(limit=200)
TICK_STEP = 2           # live tick cadence, minutes


def tf_minutes(timeframe: str) -> int:
    tf = str(timeframe).strip().lower()
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    raise ValueError(f"unsupported timeframe {timeframe!r}")


def _utc(ts: pd.Series) -> pd.Series:
    t = pd.to_datetime(ts, utc=True)
    return t


def minute_matrices(m1: pd.DataFrame, bars: pd.DataFrame, tf_min: int) -> Dict[str, np.ndarray]:
    """Per bar (row, aligned to ``bars``) × minute-into-bar (col): the running
    high / low / last close the live fetch sees after that minute, and the
    remaining high / low from that minute on."""
    ts = _utc(m1["ts"] if "ts" in m1 else m1["timestamp"])
    key = ts.dt.floor(f"{tf_min}min")
    row_of = pd.Series(np.arange(len(bars)), index=pd.DatetimeIndex(_utc(bars["timestamp"])))
    rows = row_of.reindex(key).to_numpy()
    ok = ~np.isnan(rows)
    r = rows[ok].astype(int)
    c = ((ts - key).dt.total_seconds() // 60).astype(int).to_numpy()[ok]
    shape = (len(bars), tf_min)
    H = np.full(shape, np.nan)
    L = np.full(shape, np.nan)
    C = np.full(shape, np.nan)
    H[r, c] = m1["high"].to_numpy(float)[ok]
    L[r, c] = m1["low"].to_numpy(float)[ok]
    C[r, c] = m1["close"].to_numpy(float)[ok]
    return {"run_h": np.fmax.accumulate(H, axis=1),
            "run_l": np.fmin.accumulate(L, axis=1),
            "last_c": pd.DataFrame(C).ffill(axis=1).to_numpy(),
            "rest_h": np.fmax.accumulate(H[:, ::-1], axis=1)[:, ::-1],
            "rest_l": np.fmin.accumulate(L[:, ::-1], axis=1)[:, ::-1]}


def resolve_side_filter(block: dict) -> str:
    from src.runtime.strategy_signal_builders import _resolve_side_filter
    return _resolve_side_filter(block)


def direction_prefilter(family: str, bars: pd.DataFrame, block: dict,
                        close_tick: np.ndarray) -> np.ndarray:
    """EXACT necessary condition for the unit to return a package, evaluated
    at each tick's close (columns). True = call order_package."""
    hi_s, lo_s, cl = bars["high"], bars["low"], bars["close"]
    sf = resolve_side_filter(block)
    with np.errstate(invalid="ignore", divide="ignore"):
        if family == "pullback":
            tl, pl = int(block["trend_lookback"]), int(block["pullback_lookback"])
            pf = float(block["pullback_frac"])
            mid = ((hi_s.rolling(tl).max().shift(1) + lo_s.rolling(tl).min().shift(1)) / 2).to_numpy()[:, None]
            rhi = hi_s.rolling(pl).max().shift(1).to_numpy()[:, None]
            rlo = lo_s.rolling(pl).min().shift(1).to_numpy()[:, None]
            prev = cl.shift(1).to_numpy()[:, None]
            pos = (close_tick - rlo) / (rhi - rlo)
            lng = (close_tick > mid) & (pos <= pf) & (close_tick > prev)
            sht = (close_tick < mid) & (pos >= 1 - pf) & (close_tick < prev)
        elif family == "trend":
            dn = int(block["donchian"])
            lng = close_tick > hi_s.rolling(dn).max().shift(1).to_numpy()[:, None]
            sht = close_tick < lo_s.rolling(dn).min().shift(1).to_numpy()[:, None]
        else:
            raise ValueError(f"unknown family {family!r}")
    no = np.zeros_like(lng, dtype=bool)
    return (lng if sf != "short" else no) | (sht if sf != "long" else no)


# ── live-unit evaluation (runs in worker processes) ────────────────────────

_W: Dict[str, Any] = {}


def _init_worker(family: str, label: str, block: dict, bars: pd.DataFrame,
                 mats: Optional[Dict[str, np.ndarray]]) -> None:
    from src.runtime import entry_head_pwin
    from src.runtime.strategy_signal_builders import _side_filter_suppresses
    # The P_win head only ANNOTATES a package after every gate passed; it
    # never gates or sizes. Stubbed so ~10^5 calls do not score a model.
    entry_head_pwin.maybe_score_entry_pwin = lambda **_kw: None
    if family == "pullback":
        from src.units.strategies.htf_pullback_trend_2h import order_package
    else:
        from src.units.strategies.trend_donchian import order_package
    sym = str((block.get("symbols") or [""])[0])
    _W.update(order_package=order_package,
              cfg={"symbol": sym, "timeframe": str(block.get("timeframe")),
                   **block, "strategy_label": label},
              sf=resolve_side_filter(block), supp=_side_filter_suppresses,
              base=bars[["timestamp", "open", "high", "low", "close"]].reset_index(drop=True),
              mats=mats)


def _evaluate(frame: pd.DataFrame) -> Optional[dict]:
    """The live builder's decision on one frame: order_package, then the
    builder's side_filter. None = side 'none'."""
    try:
        pkg = _W["order_package"](dict(_W["cfg"]), candles_df=frame)
    except ValueError:
        return None
    if _W["supp"](pkg["direction"], _W["sf"]):
        return None
    return pkg


def _forming_chunk(args: Tuple[List[int], List[List[int]]]) -> Dict[int, dict]:
    idxs, ticks_per = args
    base, M = _W["base"], _W["mats"]
    out: Dict[int, dict] = {}
    for i, ticks in zip(idxs, ticks_per):
        fr = base.iloc[i - WINDOW + 1:i + 1].reset_index(drop=True).copy()
        for k in ticks:                        # ascending: first firing tick wins
            col = k - 1                        # minutes [0, k) seen at tick k
            fr.loc[WINDOW - 1, ["high", "low", "close"]] = (
                M["run_h"][i, col], M["run_l"][i, col], M["last_c"][i, col])
            pkg = _evaluate(fr)
            if pkg is None:
                continue
            rh, rl = M["rest_h"][i, k], M["rest_l"][i, k]
            out[i] = {"direction": pkg["direction"], "entry": float(pkg["entry"]),
                      "atr": float(pkg["meta"]["atr"]), "tick_min": k,
                      "rest_high": None if np.isnan(rh) else float(rh),
                      "rest_low": None if np.isnan(rl) else float(rl)}
            break
    return out


def _closed_chunk(idxs: List[int]) -> Dict[int, dict]:
    base = _W["base"]
    out: Dict[int, dict] = {}
    for i in idxs:
        pkg = _evaluate(base.iloc[i - WINDOW + 1:i + 1].reset_index(drop=True))
        if pkg is not None:
            out[i] = {"direction": pkg["direction"], "entry": float(pkg["entry"]),
                      "atr": float(pkg["meta"]["atr"])}
    return out


def _chunks(seq: List[Any], n: int) -> List[List[Any]]:
    return [seq[j::n] for j in range(n)] if seq else []


def _pool(workers: int, family: str, label: str, block: dict, bars: pd.DataFrame,
          mats: Optional[Dict[str, np.ndarray]]):
    return ProcessPoolExecutor(max(1, workers), initializer=_init_worker,
                               initargs=(family, label, block, bars, mats))


def forming_entry_override(bars: pd.DataFrame, m1: pd.DataFrame, block: dict, *,
                           family: str, label: str, workers: int = 4,
                           tick_step: int = TICK_STEP,
                           mats: Optional[Dict[str, np.ndarray]] = None) -> Dict[int, dict]:
    """The live forming-bar entry decision for every bar of ``bars`` (positional
    index, harness frame), from 1m data ``m1`` (columns ts|timestamp, high,
    low, close) and the leg's config/strategies.yaml ``block``."""
    bars = bars.reset_index(drop=True)
    tf_min = tf_minutes(block.get("timeframe"))
    if mats is None:
        mats = minute_matrices(m1, bars, tf_min)
    ticks = list(range(tick_step, tf_min - 1, tick_step))
    pre = direction_prefilter(family, bars, block, mats["last_c"][:, [k - 1 for k in ticks]])
    idx = [i for i in range(WINDOW - 1, len(bars) - 1) if pre[i].any()]
    tks = [[ticks[c] for c in np.flatnonzero(pre[i])] for i in idx]
    out: Dict[int, dict] = {}
    with _pool(workers, family, label, block, bars, mats) as ex:
        for part in ex.map(_forming_chunk, list(zip(_chunks(idx, workers * 8),
                                                    _chunks(tks, workers * 8)))):
            out.update(part)
    return out


def closed_liveframe_override(bars: pd.DataFrame, block: dict, *, family: str,
                              label: str, workers: int = 4) -> Dict[int, dict]:
    """The live unit's decision on the 200 CLOSED bars ending at each i (entry
    at close[i]) — isolates the frame from the forming-bar difference."""
    bars = bars.reset_index(drop=True)
    pre = direction_prefilter(family, bars, block, bars["close"].to_numpy()[:, None])[:, 0]
    idx = [i for i in range(WINDOW - 1, len(bars) - 1) if pre[i]]
    out: Dict[int, dict] = {}
    with _pool(workers, family, label, block, bars, None) as ex:
        for part in ex.map(_closed_chunk, _chunks(idx, workers * 8)):
            out.update(part)
    return out


def cli_inputs(klines_dir: str, strategy_name: str) -> Tuple[pd.DataFrame, dict]:
    """(1m frame, YAML block) for a harness CLI ``--decision-bar forming`` run:
    every <SYMBOL>-1m-*.zip Binance archive in ``klines_dir`` for the leg's
    pinned symbol, and the leg's config/strategies.yaml block."""
    import importlib.util
    import yaml
    cfg = yaml.safe_load((ROOT / "config/strategies.yaml").read_text())
    block = cfg.get("strategies", cfg).get(strategy_name)
    if not block:
        raise SystemExit(f"--decision-bar forming: no config/strategies.yaml block "
                         f"named {strategy_name!r} (pass --strategy-name <leg>)")
    sym = str((block.get("symbols") or [""])[0])
    vs = sys.modules.get("vol_skip_forming_bar_replay")
    if vs is None:
        spec = importlib.util.spec_from_file_location(
            "vol_skip_forming_bar_replay", Path(__file__).with_name("vol_skip_forming_bar_replay.py"))
        vs = importlib.util.module_from_spec(spec)
        sys.modules["vol_skip_forming_bar_replay"] = vs
        spec.loader.exec_module(vs)
    return vs.load_1m(klines_dir, sym), block
