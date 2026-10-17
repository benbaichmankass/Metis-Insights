"""Cost-aware expected-value scorer for the capital allocator — M18 P1.

A **pure**, observe-only per-candidate score that ranks the full opportunity set
(the M18 P0b candidate batch) by **expected net R** rather than raw confidence.
It is the ``score_fn`` the allocator soak (M18 P0c) plugs in to make its regret
metric *cost-aware* — and the same scorer the eventual selector (M18 P2+) will
rank on. **Nothing here influences an order**; it only scores.

The score, in **R-units** (multiples of the trade's own stop-distance risk, so
cross-instrument candidates compare on one axis):

    EV_R = P_win · R_target − (1 − P_win) · 1.0 − fee_R − funding_R

- ``R_target = |tp − entry| / |entry − sl|`` — reward in R (the win pays this
  many multiples of the 1R stop).
- ``P_win`` — the candidate's calibrated win-probability proxy: its strategy
  confidence (``source_context['confidence']`` = the dominant conviction input
  ``c_strat``). A full conviction blend is a later refinement.
- ``fee_R = (fee_bps_roundtrip / 1e4) · |entry| / |entry − sl|`` — the
  **round-trip transaction cost** expressed in R (qty-independent). This is the
  cost term the per-cell live path never charges today (it's backtest-only); the
  fixed default is replaced by the **logged** per-trade fees once M18 P0a lands.
- ``funding_R`` — perp funding / prop swap in R; **0.0 by default** at P1 (it
  needs an expected hold-time, fed from the P0a capture). Carried as an input so
  the formula is complete and a caller can supply an estimate.

Fail-permissive: anything un-derivable (missing/zero risk distance, bad numbers)
→ ``None`` from :func:`candidate_ev_r`, and a deliberately very-low score from
:func:`candidate_ev_score` so an un-scorable candidate ranks last but never
raises and never strands a tick.
"""
from __future__ import annotations

import json as _json
import logging
import math
import os as _os
import sqlite3 as _sqlite3
import time as _time
from pathlib import Path as _Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Round-trip transaction cost in basis points. The single owner is now the shared
# execution-realism cost model (``src.runtime.execution_costs``); re-exported here
# so every existing ``from allocator_ev import DEFAULT_FEE_BPS_ROUNDTRIP`` caller is
# unchanged (P1 consolidation, FAITHFUL-BACKTEST-PLATFORM-DESIGN § 3.B). The EV
# scorer's ``funding_R`` input is the R-space analogue of that model's funding term.
from src.runtime.execution_costs import DEFAULT_FEE_BPS_ROUNDTRIP  # noqa: F401,E402

# Score returned for an un-scorable candidate — ranks strictly below any real
# EV (real EV_R is bounded well above this) without raising.
_UNSCORABLE = -1.0e9


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def compute_ev_r(
    *,
    entry: Any,
    sl: Any,
    tp: Any,
    p_win: Any,
    fee_bps_roundtrip: float = DEFAULT_FEE_BPS_ROUNDTRIP,
    funding_r: float = 0.0,
) -> Optional[float]:
    """Pure cost-aware expected net R for one candidate. ``None`` if un-derivable.

    See the module docstring for the formula. ``p_win`` is clamped to ``[0, 1]``;
    ``fee_bps_roundtrip`` / ``funding_r`` clamp negatives to 0 (a cost never adds
    edge). Never raises.
    """
    e, s, t = _f(entry), _f(sl), _f(tp)
    p = _f(p_win)
    if e is None or s is None or t is None or p is None:
        return None
    risk = abs(e - s)
    if risk <= 0.0:
        return None
    p = min(1.0, max(0.0, p))
    r_target = abs(t - e) / risk
    fee_bps = max(0.0, _f(fee_bps_roundtrip) or 0.0)
    fund = max(0.0, _f(funding_r) or 0.0)
    fee_r = (fee_bps / 1.0e4) * abs(e) / risk
    return p * r_target - (1.0 - p) * 1.0 - fee_r - fund


def candidate_p_win(candidate: Any) -> Optional[float]:
    """The candidate's win-probability proxy — its strategy confidence (c_strat)."""
    try:
        ctx = getattr(candidate, "source_context", None) or {}
        return _f(ctx.get("confidence"))
    except AttributeError:
        return None


def candidate_ev_r(
    candidate: Any,
    *,
    fee_bps_roundtrip: float = DEFAULT_FEE_BPS_ROUNDTRIP,
    funding_r: float = 0.0,
) -> Optional[float]:
    """Cost-aware EV_R for a ``SignalPackage`` candidate, or ``None``. Never raises."""
    try:
        return compute_ev_r(
            entry=getattr(candidate, "entry_price", None),
            sl=getattr(candidate, "stop_loss", None),
            tp=getattr(candidate, "take_profit", None),
            p_win=candidate_p_win(candidate),
            fee_bps_roundtrip=fee_bps_roundtrip,
            funding_r=funding_r,
        )
    except Exception:  # noqa: BLE001 — pure scorer must never raise into the soak/allocator
        logger.debug("candidate_ev_r: un-scorable candidate", exc_info=False)
        return None


def candidate_ev_score(candidate: Any) -> float:
    """``score_fn`` adapter for the allocator soak / selector.

    Returns the cost-aware EV_R; an un-scorable candidate gets a sentinel low
    score so it ranks last without raising. (The soak only fires on ≥ 2
    candidates, so the sentinel never spuriously wins.)
    """
    ev = candidate_ev_r(candidate)
    return ev if ev is not None else _UNSCORABLE


# ---------------------------------------------------------------------------
# Per-account ALLOCATION score — NETTED-SLICES (operator 2026-10-15)
# ---------------------------------------------------------------------------
#
# Operator, verbatim: "... we just need to make sure we know how to allocate
# between the trades correctly. And it should be based on those other things,
# not just backtesting. It's just that backtesting is also a parameter here."
# And (manager relay, ~20:22Z): "none of this should be new and we should work
# based on what we've already built".
#
# So this is the SAME conviction blend the signal builders already stamp
# observe-only on `meta.conviction` (`conviction.compute_conviction`: c_strat,
# c_setup, c_wr, c_reg — each a calibrated P(win) in [0, 1], weights
# renormalised over the inputs present), EXTENDED by the two inputs the stamp
# never had:
#   c_bt    backtest evidence: 0.5 + expectancy_r_oos from the leg's committed
#           comms/strategy_evidence/<leg>.json, ONLY when coverage_state is
#           `measured` (net of the full cost stack). Anything else is DROPPED
#           (not 0.5, not 0): an absent record is a statement about us.
#   c_live  live track record: (wins + 1) / (n + 2) over the leg's last
#           `live_window_trades` closed journal trades. No trades -> DROPPED.
# The allocation conviction is p_win; the cost-aware EV_R above is computed
# from it and recorded beside the weight. The WEIGHT of a strategy in its
# account's (symbol) budget is p_win / Σ p_win over that account's same-side
# contenders (`allocation_weights`). Weights live in config/slice_allocation.yaml
# (Tier-3 to change); the observe-only stamp's own DEFAULT_CONVICTION_WEIGHTS
# are untouched, so nothing that reads `meta.conviction` today moves.
#
# Consumed by `arbitration_fanout.plan_per_account_election` for accounts with
# `position_model: slices`; `positions.admit_slice` turns the weight into a qty.

_REPO_ROOT = _Path(__file__).resolve().parents[2]
_ALLOC_CONFIG_PATH = _REPO_ROOT / "config" / "slice_allocation.yaml"
_EVIDENCE_DIR = _REPO_ROOT / "comms" / "strategy_evidence"

#: Used only when the YAML cannot be read; pinned equal to the file by a test.
DEFAULT_ALLOCATION_CONFIG: dict = {
    "weights": {"c_strat": 0.35, "c_setup": 0.10, "c_wr": 0.10, "c_reg": 0.10,
                "c_bt": 0.20, "c_live": 0.15},
    "live_window_trades": 30,
    "symbol_budget_trades": 1.0,
}
_TTL_S = 300.0
_CFG_CACHE: Optional[tuple] = None
_LIVE_CACHE: Optional[tuple] = None
_BT_CACHE: dict = {}


def load_allocation_config(force: bool = False) -> dict:
    """The allocation config, TTL-cached; the pinned defaults when unreadable."""
    global _CFG_CACHE
    now = _time.monotonic()
    if not force and _CFG_CACHE is not None and now - _CFG_CACHE[0] < _TTL_S:
        return _CFG_CACHE[1]
    cfg = {"weights": dict(DEFAULT_ALLOCATION_CONFIG["weights"]),
           "live_window_trades": DEFAULT_ALLOCATION_CONFIG["live_window_trades"],
           "symbol_budget_trades": DEFAULT_ALLOCATION_CONFIG["symbol_budget_trades"]}
    try:
        import yaml  # type: ignore

        with open(_ALLOC_CONFIG_PATH, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if isinstance(raw.get("weights"), dict):
            cfg["weights"] = {k: float(raw["weights"].get(k, 0.0) or 0.0)
                              for k in DEFAULT_ALLOCATION_CONFIG["weights"]}
        if raw.get("live_window_trades") is not None:
            cfg["live_window_trades"] = max(1, int(raw["live_window_trades"]))
        if raw.get("symbol_budget_trades") is not None:
            cfg["symbol_budget_trades"] = max(0.0, float(raw["symbol_budget_trades"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("allocator_ev: allocation config unreadable (%s) — pinned defaults", exc)
    _CFG_CACHE = (now, cfg)
    return cfg


def backtest_input(strategy: str, *, evidence_dir: Optional[_Path] = None) -> tuple:
    """``(c_bt or None, state)`` from the leg's committed evidence record."""
    if evidence_dir is None:
        hit = _BT_CACHE.get(strategy)
        if hit is not None and _time.monotonic() - hit[0] < _TTL_S:
            return hit[1]
    path = (evidence_dir or _EVIDENCE_DIR) / f"{strategy}.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rec = _json.load(fh)
        if str(rec.get("coverage_state") or "") != "measured":
            out = (None, f"coverage_{rec.get('coverage_state') or 'unknown'}")
        else:
            exp = _f(rec.get("expectancy_r_oos"))
            out = (None, "no_expectancy") if exp is None else (
                min(1.0, max(0.0, 0.5 + exp)), "measured")
    except FileNotFoundError:
        out = (None, "no_record")
    except Exception:  # noqa: BLE001
        out = (None, "unreadable")
    if evidence_dir is None:
        _BT_CACHE[strategy] = (_time.monotonic(), out)
    return out


def live_counts(window: int, db_path: Optional[str] = None) -> dict:
    """``{strategy: (wins, n)}`` over each leg's last *window* closed live trades."""
    global _LIVE_CACHE
    now = _time.monotonic()
    use_cache = db_path is None
    if use_cache and _LIVE_CACHE is not None and now - _LIVE_CACHE[0] < _TTL_S:
        return _LIVE_CACHE[1]
    out: dict = {}
    try:
        if db_path is None:
            from src.utils.paths import trade_journal_db_path
            db_path = trade_journal_db_path()
        if not db_path or not _os.path.exists(db_path):
            return out
        conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT strategy_name, pnl FROM ("
                "  SELECT strategy_name, pnl, ROW_NUMBER() OVER ("
                "    PARTITION BY strategy_name ORDER BY id DESC) AS rn"
                "  FROM trades WHERE status = 'closed' AND pnl IS NOT NULL"
                "  AND COALESCE(is_backtest, 0) = 0 AND strategy_name IS NOT NULL"
                ") WHERE rn <= ?",
                (int(window),),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("allocator_ev: live track record unreadable: %s", exc)
        return out
    for name, pnl in rows:
        w, n = out.get(str(name), (0, 0))
        out[str(name)] = (w + (1 if float(pnl) > 0 else 0), n + 1)
    if use_cache:
        _LIVE_CACHE = (now, out)
    return out


def live_input(strategy: str, counts: dict) -> tuple:
    """``(c_live or None, state)`` — Laplace-smoothed live win rate."""
    wins, n = counts.get(str(strategy), (0, 0))
    if n <= 0:
        return None, "no_trades"
    return (wins + 1.0) / (n + 2.0), "measured"


def intent_allocation_score(
    intent: Any,
    *,
    config: Optional[dict] = None,
    counts: Optional[dict] = None,
    evidence_dir: Optional[_Path] = None,
) -> dict:
    """``{"score", "ev_r", "inputs", "states", "weights_used"}`` for one intent.

    ``score`` is the allocation conviction (``compute_conviction`` over the
    stamped c_strat/c_setup/c_wr/c_reg plus c_bt and c_live). When no input at
    all is present the score is 0.0 and ``allocation_weights`` falls back to
    its labelled equal split.
    """
    from src.runtime.conviction import compute_conviction

    cfg = config or load_allocation_config()
    strategy = str(getattr(intent, "strategy", "") or "")
    meta = getattr(intent, "meta", None) or {}
    stamped = (meta.get("conviction") or {}) if isinstance(meta, dict) else {}
    inputs: dict = {}
    states: dict = {}
    for k, v in ((stamped.get("inputs_used") or {}) if isinstance(stamped, dict) else {}).items():
        inputs[k] = v
        states[k] = "stamped"
    if "c_strat" not in inputs:
        inputs["c_strat"] = _f(getattr(intent, "confidence", None))
        states["c_strat"] = "intent_confidence"
    inputs["c_bt"], states["c_bt"] = backtest_input(strategy, evidence_dir=evidence_dir)
    if counts is None:
        counts = live_counts(int(cfg.get("live_window_trades") or 30))
    inputs["c_live"], states["c_live"] = live_input(strategy, counts)
    news = (stamped.get("news_multiplier") if isinstance(stamped, dict) else None)
    conv = compute_conviction(
        inputs, weights=cfg.get("weights"),
        news_multiplier=1.0 if news is None else news,
    )
    score = float(conv.conviction) if conv.conviction is not None else 0.0
    ev = compute_ev_r(entry=getattr(intent, "entry", None), sl=getattr(intent, "sl", None),
                      tp=getattr(intent, "tp", None), p_win=score)
    return {
        "score": round(score, 6),
        "ev_r": None if ev is None else round(ev, 6),
        "inputs": {k: (None if v is None else round(float(v), 6)) for k, v in inputs.items()},
        "states": states,
        "weights_used": {k: round(v, 6) for k, v in conv.weights_used.items()},
    }


def allocation_weights(scores: dict) -> tuple:
    """``(weights, basis)`` — ``score_i / Σ score``.

    ``basis`` is ``"score"`` normally and ``"equal_split_last_resort"`` when no
    score is positive (deterministic, labelled). A strategy's NAME never moves a
    weight.
    """
    clean = {k: max(0.0, _f(v) or 0.0) for k, v in scores.items()}
    if not clean:
        return {}, "no_contenders"
    total = sum(clean.values())
    if total <= 0:
        eq = 1.0 / len(clean)
        return {k: eq for k in clean}, "equal_split_last_resort"
    return {k: v / total for k, v in clean.items()}, "score"


def reset_allocation_caches() -> None:
    """Test hook."""
    global _CFG_CACHE, _LIVE_CACHE
    _CFG_CACHE = None
    _LIVE_CACHE = None
    _BT_CACHE.clear()
