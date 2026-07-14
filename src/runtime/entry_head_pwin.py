"""M18 Phase A — P_win ENTRY-head live scorer (observe-only annotate).

The M21 E-3 rounds gated a P_win entry head on the donchian-1h and
pullback-2h families (first_touch_1r label; τ-skip beats taking every trade
on net_R AND maxDD 6/6 / 4/4 walk-forward folds — sprint log
``S-M21-ENTRY-REFINEMENT-2026-07-13.md``). Phase A of the operator-approved
M18 allocator wiring (2026-07-14) puts that head's score NEXT TO every
candidate the allocator soak sees, without influencing anything:

* the signal builders (``trend_donchian`` + ``htf_pullback_trend_2h``) call
  :func:`maybe_score_entry_pwin` at signal time and stamp the result into
  the signal ``meta`` (``head_p_win`` / ``head_p_win_model``), which rides
  ``Intent.meta`` → ``SignalPackage.raw`` into the allocator-soak record;
* each score is also appended to ``runtime_logs/shadow_predictions.jsonl``
  (``event_source: "entry_head"``) so the shadow-stats surfaces track it.

Features are the SIGNAL-BAR set the head is trained on (post-Phase-A
builder: ``entry_mom_8`` / ``entry_dc_dist_atr`` / ``entry_hour`` /
``entry_dayofweek`` / ``is_long`` / ``entry_confidence``) — everything is
computable from the decision bar the unit is already acting on, so live ==
train by construction (tests/test_entry_head_live_parity.py).

**Observe-only by construction**: every failure mode returns ``None`` and
the caller stamps nothing; nothing live reads the annotation back. Phase B
(``allocator_ev.candidate_p_win`` consuming the head) is a separate,
backtest-gated Tier-3 change. No enable gate: an absent artifact (mirror
not yet published, dev sandbox) makes every call a cheap no-op — the honest
default-permissive shape (Prime Directive). Artifacts arrive over the
standard trainer→live mirror channel
(``runtime_logs/trainer_mirror/entry_head/*.json``, written by
``scripts/ml/export_entry_head.py`` + ``publish_trainer_mirror.sh``).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SHADOW_LOG_NAME = "shadow_predictions.jsonl"

# {path_str: (mtime, artifact, booster)}
_CACHE: dict = {}


def _artifact_dir():
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / "trainer_mirror" / "entry_head"


def _load_artifacts():
    """Every servable ``(artifact, booster)`` in the mirror's entry_head dir.

    Same shape as exit_head_shadow._load_artifacts: cached per file by
    mtime; a missing dir or garbled file is silently skipped — worst case
    the annotate is disabled, never the signal builder."""
    out = []
    try:
        files = sorted(_artifact_dir().glob("*.json"))
    except OSError:
        return out
    for path in files:
        try:
            st = path.stat()
            key = str(path)
            ent = _CACHE.get(key)
            if ent and ent[0] == st.st_mtime and ent[2] is not None:
                out.append((ent[1], ent[2]))
                continue
            artifact = json.loads(path.read_text(encoding="utf-8"))
            import lightgbm as lgb

            booster = lgb.Booster(model_str=artifact["booster_txt"])
            _CACHE[key] = (st.st_mtime, artifact, booster)
            out.append((artifact, booster))
        except Exception:  # noqa: BLE001 — annotate must never break a signal
            logger.debug("entry_head_pwin: artifact load failed: %s", path,
                         exc_info=True)
    return out


def _signal_bar_features(candles_df, direction: str,
                         confidence: Optional[float]) -> Optional[Dict[str, Any]]:
    """The builder's SIGNAL-BAR entry-feature block, computed at the LAST
    row of the unit's frame (the decision bar the unit is acting on).
    Mirror of build_exit_head_dataset.rows_for_trade's k0 block — Wilder
    ATR-14 seeded from the first bar, Donchian-20 mid distance in ATRs,
    favourable-signed 8-bar momentum. All bars <= the decision bar."""
    try:
        import pandas as pd

        n = len(candles_df)
        if n < 2 or "timestamp" not in getattr(candles_df, "columns", []):
            return None
        highs = candles_df["high"].astype(float).to_numpy()
        lows = candles_df["low"].astype(float).to_numpy()
        closes = candles_df["close"].astype(float).to_numpy()
        k0 = n - 1
        is_long = direction == "long"
        sign = 1.0 if is_long else -1.0

        entry_mom_8 = None
        if k0 >= 8 and closes[k0 - 8] > 0:
            entry_mom_8 = round(sign * (closes[k0] / closes[k0 - 8] - 1.0), 6)

        # Wilder ATR-14 (builder parity: valid from index >= 14)
        _atr = None
        _prev = None
        for i in range(n):
            tr = (highs[i] - lows[i]) if _prev is None else max(
                highs[i] - lows[i], abs(highs[i] - _prev), abs(lows[i] - _prev))
            _atr = tr if _atr is None else (_atr * 13 + tr) / 14
            _prev = closes[i]
        atr0 = _atr if k0 >= 14 else None

        entry_dc_dist = None
        if atr0:
            lo20 = float(lows[max(0, k0 - 19):k0 + 1].min())
            hi20 = float(highs[max(0, k0 - 19):k0 + 1].max())
            entry_dc_dist = round((closes[k0] - (lo20 + hi20) / 2.0) / atr0, 4)

        ts = pd.to_datetime(candles_df["timestamp"].iloc[-1], utc=True,
                            errors="coerce")
        if pd.isna(ts):
            return None
        ts = ts.to_pydatetime()
        conf = None
        try:
            c = float(confidence)
            conf = c if math.isfinite(c) else None
        except (TypeError, ValueError):
            pass
        return {
            "entry_mom_8": entry_mom_8,
            "entry_dc_dist_atr": entry_dc_dist,
            "entry_hour": ts.hour,
            "entry_dayofweek": ts.weekday(),
            "is_long": 1 if is_long else 0,
            "entry_confidence": conf,
            "_bar_ts": ts.isoformat(),
        }
    except Exception:  # noqa: BLE001
        return None


def maybe_score_entry_pwin(*, family: str, symbol: str, timeframe: str,
                           direction: str, confidence: Any,
                           candles_df, strategy: str = "") -> Optional[Dict[str, Any]]:
    """Score one just-built signal with the matching P_win entry head.

    Returns ``{"model_id", "stage", "p_win"}`` for the caller to stamp into
    the signal meta (observe-only annotation), or ``None`` on any miss —
    no artifact, family/tf/symbol out of distribution, feature failure.
    Never raises into the signal builder.
    """
    try:
        best = None
        for artifact, booster in _load_artifacts():
            if str(artifact.get("kind") or "entry_pwin") != "entry_pwin":
                continue
            if str(artifact.get("family") or "") != family:
                continue
            if str(artifact.get("tf") or "") != str(timeframe or ""):
                continue
            symbols = artifact.get("symbols")
            if symbols and symbol not in symbols:
                continue
            row = _signal_bar_features(candles_df, direction, confidence)
            if row is None:
                return None
            features = artifact.get("features") or []
            vec = [[float(row[f]) if row.get(f) is not None else float("nan")
                    for f in features]]
            p_win = float(booster.predict(vec)[0])
            record = {
                "predicted_at_utc": datetime.now(timezone.utc).isoformat(),
                "model_id": str(artifact.get("model_id") or "entry-pwin"),
                "stage": str(artifact.get("stage") or "shadow"),
                "score": round(p_win, 6),
                "event_source": "entry_head",
                "symbol": symbol,
                "strategy": strategy or family,
                "feature_row": {k: v for k, v in row.items()
                                if not k.startswith("_")},
            }
            try:
                from src.utils.paths import runtime_logs_dir

                path = runtime_logs_dir() / SHADOW_LOG_NAME
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except Exception:  # noqa: BLE001
                pass
            out = {"model_id": record["model_id"], "stage": record["stage"],
                   "p_win": round(p_win, 6)}
            if best is None or record["stage"] == "advisory":
                best = out
        return best
    except Exception:  # noqa: BLE001 — the signal builder must never feel this
        logger.debug("entry_head_pwin: scoring failed", exc_info=True)
        return None
