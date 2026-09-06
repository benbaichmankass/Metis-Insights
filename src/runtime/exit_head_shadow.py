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

⚠️ **E3 HAS SHIPPED — THIS IS NO LONGER OBSERVE-ONLY** (live 2026-07-12,
#6211/#6216/#6217; corrected here 2026-08-13, `field beats comment`). Until
then this paragraph read *"Observe-only by construction: the function returns
``None`` always … and nothing reads the logs back. Graduation to a real exit
influence is E3"* — three claims that the code has since falsified, on a live
money path:

* ``maybe_score_exit_head`` returns the **record dict** on a successful score
  (``None`` only on the no-op paths), exactly as its own docstring says.
* ``trend_donchian.monitor`` **reads it** — ``_exit_head_verdict(...)``, and
  ``if eh_verdict is not None: return eh_verdict``. That return closes a live
  position.
* E3 is past tense. ``exit_head_action: close`` is declared on three
  ``execution: live`` strategies in ``config/strategies.yaml``
  (``trend_donchian``, ``trend_donchian_sol``, ``trend_donchian_eth``).

What is still true, and is the whole safety story: **this module decides
nothing.** It scores and logs; the APPLY is in the monitor and is gated on
(a) the strategy YAML declaring ``exit_head_action``, (b) the mirrored
artifact sitting at stage ``advisory`` — the operator promotion gate — and
(c) the conditional policy firing. Rollback is deleting the YAML lines or
demoting the artifact stage. No enable gate: absence of the artifact (mirror
not yet published, non-donchian VM, dev sandbox) makes every call a cheap
no-op, the honest default-permissive shape (Prime Directive).

The module NAME still says ``_shadow`` and now under-describes it. Renaming is
deliberately not done here — an import-surface change is not a docstring fix,
and a wrong name a reader can see beats a stale paragraph a reader believes.

Open question on the head this module serves, filed as
``BL-20260813-SHIPPED-DONCHIAN-1H-HEAD-RESTS-ON-BESTARM``: the E1 verdict
behind the ship was scored as ``max`` over ~7 tau arms on the test fold, and
that best-arm basis was measured on 2026-08-13 as the source of the entire
fleet-level edge (+1.217R best-arm vs −0.341R under causal tau selection,
514 folds). Whether THIS head survives a causal selection is **unmeasured**.
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

# per-file: {path_str: (mtime, artifact, booster)} — reloaded when the mirror
# publishes a newer file.
_CACHE: dict = {}
# One score per (model_id, order_package_id, last-closed-bar timestamp).
_SEEN: set = set()


def _artifact_dir():
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / "trainer_mirror" / "exit_head"


def _load_artifacts():
    """Every servable ``(artifact, booster)`` in the mirror's exit_head dir.

    Multi-artifact since M20 P4.2 — a second head (e.g. the peak-is-in
    retarget) rides the same channel by publishing another ``*.json``; each
    scores + logs under its own ``model_id``. Cached per file by mtime; a
    missing dir or garbled file is silently skipped — worst case the shadow
    is disabled, never the monitor."""
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
        except Exception:  # noqa: BLE001 — shadow must never break the monitor
            logger.debug("exit_head_shadow: artifact load failed: %s", path,
                         exc_info=True)
    return out


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def shape_params(shape: Dict[str, Any]) -> tuple:
    """``(tau, below_r, policy)`` from an artifact's declared ``shape`` block.

    Extracted so the OFFLINE replay
    (``scripts/ml/exit_head_replay.py``) reads the thresholds from the same
    place the live monitor does instead of re-deriving the defaults.
    """
    return (_f(shape.get("tau")) or 0.10,
            _f(shape.get("below_r")) or 0.5,
            str(shape.get("policy") or "below_half_r"))


def would_exit_for(shape: Dict[str, Any], score: float, open_r: float) -> bool:
    """THE exit-head decision predicate — one owner, live and offline.

    ``would_exit`` semantics follow the artifact's declared shape:
    ``below_half_r`` (the live head) fires on LOW scores against losers; the
    ``peak_*`` shapes fire on HIGH scores (``P(peak_is_in) > tau``).

    Extracted from :func:`maybe_score_exit_head` (behaviour-preserving, same
    expression) specifically so the offline replay that re-resolves a backtest
    trade's exit CANNOT drift from the live monitor's decision. A second copy
    of this predicate is the defect class documented in
    ``FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md`` §5f: two harness copies
    that disagreed about the trail's ATR basis, one of them cited as the
    reference implementation by a live Tier-3 lever.
    """
    tau, below_r, policy = shape_params(shape)
    if policy.startswith("peak"):
        return score > tau and (policy != "peak_winner" or open_r >= below_r)
    return score < tau and open_r < below_r


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
    full_highs = candles_df["high"].astype(float).to_numpy()
    full_lows = candles_df["low"].astype(float).to_numpy()
    full_closes = candles_df["close"].astype(float).to_numpy()
    full_vols = None
    if "volume" in candles_df.columns:
        try:
            full_vols = candles_df["volume"].astype(float).to_numpy()
        except (TypeError, ValueError):
            full_vols = None

    # Wilder ATR-14 full series (mirror of the builder's atr_series: valid
    # from index >= 14, seeded with the first bar's plain range).
    atrs = []
    _atr = None
    _prev_close = None
    for i in range(n):
        tr = (full_highs[i] - full_lows[i]) if _prev_close is None else max(
            full_highs[i] - full_lows[i], abs(full_highs[i] - _prev_close),
            abs(full_lows[i] - _prev_close))
        _atr = tr if _atr is None else (_atr * 13 + tr) / 14
        atrs.append(_atr if i >= 14 else None)
        _prev_close = full_closes[i]

    sign = 1.0 if is_long else -1.0
    mfe = mae = 0.0
    chop_hits = 0
    stagn_run = 0
    # M20 P4.3 exhaustion-state trackers — EXACT mirror of the E0 builder's
    # per-bar loop (build_exit_head_dataset.rows_for_trade); live == train
    # parity is enforced by tests/test_exit_head_feature_parity.py.
    peak_a = 0
    atr_at_peak = None
    mom8_at_peak = None
    vol_at_peak = None
    dc_hist = []
    prev_m = None
    for a in range(len(win)):
        k = entry_idx + a
        hi, lo, cl = full_highs[k], full_lows[k], full_closes[k]
        hi_r = ((hi - entry) if is_long else (entry - lo)) / risk
        lo_r = ((lo - entry) if is_long else (entry - hi)) / risk
        m = ((cl - entry) if is_long else (entry - cl)) / risk
        mom_8 = None
        if k >= 8 and full_closes[k - 8] > 0:
            mom_8 = sign * (cl / full_closes[k - 8] - 1.0)
        new_peak = hi_r > mfe
        mfe = max(mfe, hi_r)
        mae = min(mae, lo_r)
        if new_peak or a == 0:
            peak_a = a
            atr_at_peak = atrs[k]
            mom8_at_peak = mom_8
            vol_at_peak = (float(full_vols[k])
                           if full_vols is not None
                           and math.isfinite(full_vols[k]) else None)
        if abs(m) < 0.25:
            chop_hits += 1
            stagn_run += 1
        else:
            stagn_run = 0
        atr_k = atrs[k]
        dc_lo_k = float(full_lows[max(0, k - 19):k + 1].min())
        dc_hi_k = float(full_highs[max(0, k - 19):k + 1].max())
        dc_dist_k = ((cl - (dc_lo_k + dc_hi_k) / 2.0) / atr_k) if atr_k else None
        if a == len(win) - 1:
            last_mom_8 = mom_8
            last_dc_dist = dc_dist_k
            band_ext_pctile = None
            if dc_dist_k is not None and len(dc_hist) >= 3:
                fav_dc = sign * dc_dist_k
                band_ext_pctile = round(
                    sum(1 for x in dc_hist if x <= fav_dc) / len(dc_hist), 4)
            bars_since_peak = a - peak_a
            failure_swing = (1 if a > 0 and bars_since_peak <= 2
                             and prev_m is not None and m < prev_m else 0)
        elif dc_dist_k is not None:
            dc_hist.append(sign * dc_dist_k)
        prev_m = m
    age = len(win) - 1
    open_r = ((full_closes[-1] - entry) if is_long
              else (entry - full_closes[-1])) / risk
    mom_decay = ((mom8_at_peak - last_mom_8)
                 if last_mom_8 is not None and mom8_at_peak is not None else None)
    atr_now = atrs[n - 1]
    atr_impulse_phase = ((atr_now / atr_at_peak)
                         if atr_now and atr_at_peak else None)
    vol_at_peak_ratio = None
    if full_vols is not None and vol_at_peak:
        vw = sorted(v for v in full_vols[max(0, n - 20):n]
                    if math.isfinite(v) and v > 0)
        vol_med = vw[len(vw) // 2] if len(vw) >= 5 else None
        vol_at_peak_ratio = (vol_at_peak / vol_med) if vol_med else None

    # entry-time refs need bars strictly BEFORE entry
    def _rvol(cl):
        if len(cl) < 3:
            return None
        rets = [math.log(b / a) for a, b in zip(cl, cl[1:]) if a > 0 and b > 0]
        if len(rets) < 2:
            return None
        mu = sum(rets) / len(rets)
        return math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))

    vol_entry = _rvol(list(full_closes[max(0, entry_idx - 21):entry_idx]))
    vol_now = _rvol(list(full_closes[max(0, n - 21):n]))
    atr_entry = atrs[entry_idx - 1] if entry_idx >= 1 else None
    dc_dist = last_dc_dist

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
        # P4.3 exhaustion features (live twin of the E0 builder block)
        "bars_since_peak": int(bars_since_peak),
        "mom_8": (round(float(last_mom_8), 6)
                  if last_mom_8 is not None else None),
        "mom_decay": (round(float(mom_decay), 6)
                      if mom_decay is not None else None),
        "atr_impulse_phase": (round(float(atr_impulse_phase), 4)
                              if atr_impulse_phase is not None else None),
        "vol_at_peak_ratio": (round(float(vol_at_peak_ratio), 4)
                              if vol_at_peak_ratio is not None else None),
        "band_ext_pctile": (float(band_ext_pctile)
                            if band_ext_pctile is not None else None),
        "failure_swing": int(failure_swing),
        "is_long": 1 if is_long else 0,
        "_bar_ts": ts.isoformat(),
    }


#: Family tokens each consuming unit will accept from an artifact's own
#: ``family`` field. A SET, not a string, because the token is minted by the
#: trainer (``export_exit_head.py`` writes ``fam_dir.name`` — a directory name
#: from the training round) and this repo does not control that name. A single
#: hardcoded string would turn a harmless naming difference into a permanent,
#: SILENT refusal to score. Normalised lowercase/stripped on both sides.
_ACCEPTED_FAMILIES: dict = {
    "donchian": {"donchian", "trend_donchian"},
    "ict_scalp": {"ict_scalp", "scalp"},
}


def maybe_score_exit_head(meta: Dict[str, Any], open_pkg: Dict[str, Any],
                          candles_df, direction: str,
                          family: Optional[str] = None,
                          ) -> Optional[Dict[str, Any]]:
    """Score one open trade at the last CLOSED bar.

    Called from ``trend_donchian.monitor`` after the close-path checks.
    Scoring itself is side-effect-logging only (shadow_predictions +
    exit_lever_soak); every failure mode is a silent no-op returning
    ``None``. On a successful score the record dict is returned so the
    monitor's E3 APPLY path can consult it — that path is gated separately
    (strategy-YAML declare + artifact ``stage == "advisory"``); the scorer
    itself never decides anything.

    ``family`` (MI-150) is the CALLING UNIT's declaration of which exit-head
    family it is entitled to score against, checked below against the
    artifact's own ``family`` field.

    ⚠️ **IT IS CALLER-OPT-IN, AND THAT IS DELIBERATE.** ``family=None`` means
    *the caller did not declare* — **not** *no family applies* — and skips the
    check, which is byte-for-byte the pre-MI-150 behaviour. The donchian call
    site deliberately still passes nothing: the live mirror's advisory artifact
    (``exit-head-donchian-1h-v1``) closes real positions, and **what value its
    ``family`` field actually carries has NOT been read from the mirror** (the
    exit_head artifact dir is not on the diag ``log_file`` allowlist, so it
    could not be measured from a PM-side session). Tightening a live money path
    against an unverified string is how a working exit gate goes quiet. Gating
    donchian too is a separate change that must FIRST read that value —
    ``BL-20260906-EXIT-HEAD-GUARD-IGNORES-THE-ARTIFACT-FAMILY-FIELD``.
    """
    try:
        # In-distribution guard: each head was trained on specific
        # (timeframe, symbols); every strategy that reuses the donchian
        # monitor (incl. equities-1d variants) reaches this hook, and an
        # out-of-family score would pollute the shadow track record (the
        # 2026-07-12 IWM-1d rows). Fail-closed on a timeframe mismatch or
        # unknown timeframe; symbol list enforced when the artifact carries
        # one.
        meta_tf = str(meta.get("timeframe") or "")
        candidates = []
        for artifact, booster in _load_artifacts():
            a_tf = str(artifact.get("tf") or "")
            if not a_tf or meta_tf != a_tf:
                continue
            symbols = artifact.get("symbols")
            if symbols and str(open_pkg.get("symbol") or "") not in symbols:
                continue
            # FAMILY gate (MI-150). The artifact has declared a `family` since
            # export_exit_head.py:116 and NOTHING has ever read it, while this
            # guard's own docstring says it exists to stop "an out-of-family
            # score". That was inert while donchian was the only family with a
            # published head; it stops being inert the moment a second family
            # ships one, because `tf` + `symbols` alone do not separate two
            # strategies trading the SAME symbol on the SAME timeframe — which
            # is exactly ict_scalp vs donchian on SOLUSDT.
            #
            # ⚠️ A MISMATCH IS LOGGED AT WARNING, NOT SWALLOWED. The token is
            # minted by the trainer and a naming difference is indistinguishable
            # from a genuine out-of-family artifact from in here; refusing
            # silently would present "the trainer named the dir differently" as
            # "this head never fires", which is the same unreadable state
            # `tf`-mismatch already produces. The refusal is correct; being
            # quiet about it is not.
            if family is not None:
                a_fam = str(artifact.get("family") or "").strip().lower()
                if a_fam:
                    accepted = _ACCEPTED_FAMILIES.get(
                        str(family).strip().lower(), {str(family).strip().lower()})
                    if a_fam not in accepted:
                        logger.warning(
                            "exit_head_shadow: REFUSING out-of-family artifact "
                            "%s (artifact family=%r, caller family=%r, accepted"
                            "=%s) — no score produced for %s/%s. If this is a "
                            "naming difference and not a real mismatch, add the "
                            "token to _ACCEPTED_FAMILIES.",
                            artifact.get("model_id"), a_fam, family,
                            sorted(accepted), open_pkg.get("symbol"), meta_tf)
                        continue
                # An artifact declaring NO family is NOT graded as a match: it
                # is "we could not look". It is allowed through (a legacy
                # artifact predating the field must not be silently disabled)
                # and the fact is recorded on the record as `family_state`.
            candidates.append((artifact, booster))
        if not candidates:
            return None
        tf = meta_tf
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

        advisory_record = None
        first_record = None
        for artifact, booster in candidates:
            model_id = str(artifact.get("model_id") or MODEL_ID)
            seen_key = (model_id, pkg_id, row["_bar_ts"])
            if seen_key in _SEEN:
                continue
            _SEEN.add(seen_key)

            features = artifact.get("features") or []
            vec = [[float(row[f]) if row.get(f) is not None else float("nan")
                    for f in features]]
            score = float(booster.predict(vec)[0])
            shape = artifact.get("shape") or {}
            tau, below_r, policy = shape_params(shape)
            would_exit = would_exit_for(shape, score, row["open_r"])

            record = {
                "predicted_at_utc": datetime.now(timezone.utc).isoformat(),
                "model_id": model_id,
                "stage": str(artifact.get("stage") or "shadow"),
                "tau": tau,
                "below_r": below_r,
                "score": round(score, 6),
                "event_source": "exit_head",
                "symbol": str(open_pkg.get("symbol") or ""),
                # ⚠️ The fallback follows the CALLER's declared family, not a
                # hardcoded "trend_donchian". A scalp row landing in
                # shadow_predictions.jsonl labelled `trend_donchian` would be
                # UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A (CLAUDE.md
                # § "Diagnostic provenance"): the label names a strategy the
                # score did not come from, and every aggregate over that log
                # reads the label.
                "strategy": str(meta.get("strategy_label")
                                or open_pkg.get("strategy_name")
                                or family
                                or "trend_donchian"),
                # Three states, never collapsed: `matched` (artifact declared a
                # family and the caller's is accepted) / `undeclared_by_artifact`
                # (**we could not look** — a legacy artifact, NOT a match) /
                # `not_checked` (the caller declared nothing).
                "family_state": (
                    "not_checked" if family is None
                    else ("matched" if str(artifact.get("family") or "").strip()
                          else "undeclared_by_artifact")),
                "order_package_id": pkg_id,
                "policy": policy,
                "would_exit": would_exit,
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
            if would_exit:
                try:
                    from src.runtime.exit_lever_soak import (
                        record_exit_lever_annotation)

                    record_exit_lever_annotation(
                        lever="exit_head",
                        strategy=record["strategy"],
                        symbol=record["symbol"],
                        direction=direction,
                        # per-bar rows: fold the bar ts into the dedup key so a
                        # persistent would-exit logs once per bar, not once ever
                        order_package_id=f"{model_id}:{pkg_id}@{row['_bar_ts']}",
                        params={"model_id": model_id, "tau": tau,
                                "below_r": below_r},
                        state={"age_bars": row["age_bars"],
                               "open_r": row["open_r"],
                               "score": round(score, 4), "entry": entry},
                    )
                except Exception:  # noqa: BLE001
                    pass
            if first_record is None:
                first_record = record
            if advisory_record is None and record["stage"] == "advisory":
                advisory_record = record
        # M20 E3: the record is returned so the strategy monitor's APPLY
        # path can act on it — but only behind its own gates (YAML declare
        # + artifact stage == "advisory"). With multiple artifacts the
        # ADVISORY head's record wins (shadow heads are observe-only by
        # stage); observe-only callers ignore the return value entirely.
        return advisory_record or first_record
    except Exception:  # noqa: BLE001 — the monitor must never feel this
        logger.debug("exit_head_shadow: scoring failed", exc_info=True)
        return None
