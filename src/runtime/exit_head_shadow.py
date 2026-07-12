"""M20 E2 — donchian exit-head live SHADOW scorer (observe-only).

The E1.5 walk-forward passed the donchian exit head (memo
``docs/research/M20-exit-refinement-2026-07-12.md`` § 9: conditional shape
``below_half_r @ tau=0.10`` beats actual exits on net_R + maxDD +
net_R/pos-day in 5/5 folds). E2 graduates it to a live shadow: once per
closed native bar per open donchian-family trade, ``trend_donchian.monitor``
calls :func:`maybe_score_exit_head`, which

* computes the SAME in-trade feature row the E0 builder computes
  (``scripts/ml/build_exit_head_dataset.py``) from the candles the monitor
  already fetched — no new network I/O;
* scores it with the trainer-exported LightGBM artifact
  (``runtime_logs/trainer_mirror/exit_head/<model_id>.json``, delivered by
  ``publish_trainer_mirror.sh`` — the standard trainer→live channel);
* appends the score to ``runtime_logs/shadow_predictions.jsonl``
  (``event_source: "exit_head"``) so the shadow-stats surfaces track it; and
* when the E1.5 shape says *would exit* (``score < tau`` AND
  ``open_r < below_r``), writes one row per bar to
  ``runtime_logs/exit_lever_soak.jsonl`` with ``lever: "exit_head"``.

**Observe-only by construction**: the function returns ``None`` always,
never raises into the monitor, and nothing reads the logs back. Graduation
to a real exit influence is E3 — Tier-3, operator-gated, behind this
shadow's track record. No enable gate: absence of the artifact (mirror not
yet published, non-donchian VM, dev sandbox) makes every call a cheap no-op,
which is the honest default-permissive shape (Prime Directive).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# bar length per artifact ``tf`` — used to trim the current forming bar so
# the scorer only ever sees CLOSED bars (live == train)
_TF_SECONDS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
               "2h": 7200, "4h": 14400, "1d": 86400}

logger = logging.getLogger(__name__)

MODEL_ID = "exit-head-donchian-1h-v1"
ARTIFACT_SUBPATH = ("trainer_mirror", "exit_head", f"{MODEL_ID}.json")
SHADOW_LOG_NAME = "shadow_predictions.jsonl"

# (artifact mtime, parsed artifact dict, booster) — reloaded when the mirror
# publishes a newer file.
_CACHE: dict = {}
# One score per (order_package_id, last-closed-bar timestamp).
_SEEN: set = set()


def _artifact_path():
    from src.utils.paths import runtime_logs_dir

    p = runtime_logs_dir()
    for part in ARTIFACT_SUBPATH:
        p = p / part
    return p


def _load_artifact():
    """(artifact, booster) or (None, None). Cached by file mtime; silent on
    any failure — a missing/garbled artifact just disables the shadow."""
    try:
        path = _artifact_path()
        st = path.stat()
    except OSError:
        return None, None
    try:
        if _CACHE.get("mtime") == st.st_mtime and _CACHE.get("booster") is not None:
            return _CACHE["artifact"], _CACHE["booster"]
        artifact = json.loads(path.read_text(encoding="utf-8"))
        import lightgbm as lgb

        booster = lgb.Booster(model_str=artifact["booster_txt"])
        _CACHE.update({"mtime": st.st_mtime, "artifact": artifact,
                       "booster": booster})
        return artifact, booster
    except Exception:  # noqa: BLE001 — shadow must never break the monitor
        logger.debug("exit_head_shadow: artifact load failed", exc_info=True)
        return None, None


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _feature_row(candles_df, entry: float, risk: float, direction: str,
                 entry_idx: Optional[int]) -> Optional[Dict[str, Any]]:
    """The E0 builder's leakage-guarded in-trade feature row, computed on the
    monitor's pandas frame at the LAST bar. ``entry_idx`` is the index of the
    first in-trade bar within ``candles_df`` (None ⇒ entry precedes the
    fetched window — age-dependent features stay honest via the caller's
    skip; entry-time refs degrade to None)."""
    import pandas as pd  # already a hard dep of the monitor path

    is_long = direction == "long"
    n = len(candles_df)
    if entry_idx is None or entry_idx >= n:
        return None
    win = candles_df.iloc[entry_idx:]
    if len(win) < 2:
        return None
    highs = win["high"].astype(float).to_numpy()
    lows = win["low"].astype(float).to_numpy()
    closes = win["close"].astype(float).to_numpy()

    mfe = mae = 0.0
    chop_hits = 0
    stagn_run = 0
    for hi, lo, cl in zip(highs, lows, closes):
        hi_r = ((hi - entry) if is_long else (entry - lo)) / risk
        lo_r = ((lo - entry) if is_long else (entry - hi)) / risk
        m = ((cl - entry) if is_long else (entry - cl)) / risk
        mfe = max(mfe, hi_r)
        mae = min(mae, lo_r)
        if abs(m) < 0.25:
            chop_hits += 1
            stagn_run += 1
        else:
            stagn_run = 0
    age = len(win) - 1
    open_r = ((closes[-1] - entry) if is_long else (entry - closes[-1])) / risk

    # entry-time refs need bars strictly BEFORE entry
    def _rvol(cl):
        if len(cl) < 3:
            return None
        rets = [math.log(b / a) for a, b in zip(cl, cl[1:]) if a > 0 and b > 0]
        if len(rets) < 2:
            return None
        mu = sum(rets) / len(rets)
        return math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))

    full_closes = candles_df["close"].astype(float).to_numpy()
    vol_entry = _rvol(list(full_closes[max(0, entry_idx - 21):entry_idx]))
    vol_now = _rvol(list(full_closes[max(0, n - 21):n]))

    def _atr_at(idx):
        # Wilder ATR-14 up to and including idx (mirror of the builder).
        atr = None
        prev_close = None
        h = candles_df["high"].astype(float).to_numpy()
        lo_ = candles_df["low"].astype(float).to_numpy()
        c = full_closes
        for i in range(0, idx + 1):
            tr = (h[i] - lo_[i]) if prev_close is None else max(
                h[i] - lo_[i], abs(h[i] - prev_close), abs(lo_[i] - prev_close))
            atr = tr if atr is None else (atr * 13 + tr) / 14
            prev_close = c[i]
        return atr if idx >= 14 else None

    atr_entry = _atr_at(entry_idx - 1) if entry_idx >= 1 else None
    atr_now = _atr_at(n - 1)
    dc_lo = float(candles_df["low"].astype(float).iloc[max(0, n - 20):n].min())
    dc_hi = float(candles_df["high"].astype(float).iloc[max(0, n - 20):n].max())
    dc_dist = ((full_closes[-1] - (dc_lo + dc_hi) / 2.0) / atr_now) if atr_now else None

    ts = candles_df["timestamp"].iloc[-1]
    try:
        ts = pd.to_datetime(ts, utc=True).to_pydatetime()
    except Exception:  # noqa: BLE001
        ts = datetime.now(timezone.utc)
    # plain Python floats — numpy scalars are not JSON-serializable and the
    # best-effort writers would drop the record silently
    return {
        "age_bars": int(age), "open_r": round(float(open_r), 4),
        "mfe_r": round(float(mfe), 4), "mae_r": round(float(mae), 4),
        "giveback_r": round(float(mfe - open_r), 4),
        "chop_frac_so_far": round(float(chop_hits / len(win)), 4),
        "stagnation_run": int(stagn_run),
        "dist_to_stop_r": round(float(open_r) + 1.0, 4),
        "vol_ratio_vs_entry": (round(float(vol_now / vol_entry), 4)
                               if vol_now and vol_entry else None),
        "atr_ratio_vs_entry": (round(float(atr_now / atr_entry), 4)
                               if atr_now and atr_entry else None),
        "donchian_mid_dist_atr": (round(float(dc_dist), 4)
                                  if dc_dist is not None else None),
        "hour_of_day": ts.hour, "dayofweek": ts.weekday(),
        "is_long": 1 if is_long else 0,
        "_bar_ts": ts.isoformat(),
    }


def maybe_score_exit_head(meta: Dict[str, Any], open_pkg: Dict[str, Any],
                          candles_df, direction: str) -> None:
    """Score one open donchian-family trade at the current bar (observe-only).

    Called from ``trend_donchian.monitor`` after the close-path checks.
    Every failure mode is a silent no-op. Returns ``None`` always.
    """
    try:
        artifact, booster = _load_artifact()
        if booster is None:
            return None
        # In-distribution guard: the head was trained on specific
        # (timeframe, symbols); every strategy that reuses the donchian
        # monitor (incl. equities-1d variants) reaches this hook, and an
        # out-of-family score would pollute the shadow track record (the
        # 2026-07-12 IWM-1d rows). Fail-closed on a timeframe mismatch or
        # unknown timeframe; symbol list enforced when the artifact carries
        # one.
        tf = str(artifact.get("tf") or "")
        meta_tf = str(meta.get("timeframe") or "")
        if not tf or meta_tf != tf:
            return None
        symbols = artifact.get("symbols")
        if symbols and str(open_pkg.get("symbol") or "") not in symbols:
            return None
        entry = _f(open_pkg.get("entry"))
        risk = _f(meta.get("risk_per_unit"))
        if entry is None or risk is None or risk <= 0:
            return None
        if not meta.get("entry_time"):
            return None
        # locate the first in-trade bar (same semantics as _since_entry, but
        # we need the INDEX so pre-entry bars stay available for entry refs)
        import pandas as pd

        if "timestamp" not in getattr(candles_df, "columns", []):
            return None
        # Score the last CLOSED bar only. The monitor's fetch includes the
        # current forming bar as the final row; the E0 training rows are all
        # closed bars, so scoring the partial bar is train/serve skew (caught
        # live 2026-07-12: two records for the same bar with drifting open_r).
        # A bar whose open + tf hasn't elapsed yet is partial — trim it.
        tf_s = _TF_SECONDS.get(tf)
        if tf_s:
            last_ts = pd.to_datetime(candles_df["timestamp"].iloc[-1],
                                     utc=True, errors="coerce")
            if not pd.isna(last_ts):
                now = datetime.now(timezone.utc)
                if last_ts.to_pydatetime() + timedelta(seconds=tf_s) > now:
                    candles_df = candles_df.iloc[:-1]
                    if len(candles_df) < 2:
                        return None

        ts = pd.to_datetime(candles_df["timestamp"], utc=True, errors="coerce")
        cutoff = pd.to_datetime(meta.get("entry_time"), utc=True, errors="coerce")
        if pd.isna(cutoff):
            return None
        # STRICTLY-AFTER anchor, matching the E0 builder's
        # ``bisect_right(cand_ts, t_open)``: the bar carrying the signal/fill
        # is excluded, rows start at the NEXT bar. ``>=`` included that entry
        # bar live (meta entry_time is the signal bar's own label), dragging
        # pre-entry price into mfe/mae — the age-off-by-one the 2026-07-12
        # trainer parity diff caught (live mae -0.77R vs offline -0.15R).
        in_trade = ts > cutoff
        if not bool(in_trade.any()) or bool(in_trade.all()):
            return None  # entry outside the fetched window — age unknowable
        entry_idx = int(in_trade.to_numpy().argmax())

        row = _feature_row(candles_df, entry, risk, direction, entry_idx)
        if row is None:
            return None
        pkg_id = str(open_pkg.get("order_package_id") or "")
        seen_key = (pkg_id, row["_bar_ts"])
        if seen_key in _SEEN:
            return None
        _SEEN.add(seen_key)

        features = artifact.get("features") or []
        vec = [[float(row[f]) if row.get(f) is not None else float("nan")
                for f in features]]
        score = float(booster.predict(vec)[0])
        shape = artifact.get("shape") or {}
        tau = _f(shape.get("tau")) or 0.10
        below_r = _f(shape.get("below_r")) or 0.5
        would_exit = score < tau and row["open_r"] < below_r

        record = {
            "predicted_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_id": artifact.get("model_id") or MODEL_ID,
            "stage": "shadow",
            "score": round(score, 6),
            "event_source": "exit_head",
            "symbol": str(open_pkg.get("symbol") or ""),
            "strategy": str(meta.get("strategy_label")
                            or open_pkg.get("strategy_name") or "trend_donchian"),
            "order_package_id": pkg_id,
            "would_exit": would_exit,
            "feature_row": {k: v for k, v in row.items() if not k.startswith("_")},
        }
        try:
            from src.utils.paths import runtime_logs_dir

            path = runtime_logs_dir() / SHADOW_LOG_NAME
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception:  # noqa: BLE001
            pass
        if would_exit:
            try:
                from src.runtime.exit_lever_soak import record_exit_lever_annotation

                record_exit_lever_annotation(
                    lever="exit_head",
                    strategy=record["strategy"],
                    symbol=record["symbol"],
                    direction=direction,
                    # per-bar rows: fold the bar ts into the dedup key so a
                    # persistent would-exit logs once per bar, not once ever
                    order_package_id=f"{pkg_id}@{row['_bar_ts']}",
                    params={"model_id": record["model_id"], "tau": tau,
                            "below_r": below_r},
                    state={"age_bars": row["age_bars"], "open_r": row["open_r"],
                           "score": round(score, 4), "entry": entry},
                )
            except Exception:  # noqa: BLE001
                pass
        return None
    except Exception:  # noqa: BLE001 — the monitor must never feel this
        logger.debug("exit_head_shadow: scoring failed", exc_info=True)
        return None
