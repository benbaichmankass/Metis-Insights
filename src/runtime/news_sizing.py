"""Execution-time news downsize — wires the reductive news-influence operator
into the live order path (M9 graduated "act" layer, step 2).

The news score is computed once per signal in `src/runtime/pipeline.py` and
stamped onto `pkg.meta["news"]` (via the signal meta that `order_bridge` copies).
This module reads that stamp, builds the policy from env, and returns a
**reductive** size factor that `Coordinator.multi_account_execute` applies to the
RiskManager-computed per-account qty — composed *after* (multiplicatively with)
the advisory downsize, so both can only ever shrink the order.

**Default off.** Gated by `NEWS_INFLUENCE_MODE` (off / annotate / downsize,
default off). With the flag off — or in `annotate` — `apply_news_downsize`
returns the qty unchanged. Every step is wrapped so a missing stamp / bad config
can never break the trading tick (deterministic fallback to the unchanged qty).

The factor is computed once per package and cached on `pkg.meta['_news_factor']`.
A scheduled event is a **consideration**, not a blackout: it can only reduce
exposure when the trade is at risk of being knocked off course (see
`docs/news-influence-DESIGN.md`). `event_risk` is read from the stamp (0.0 until
the economic-calendar feed lands — step 3).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from src.news.news_influence import NewsInfluencePolicy, news_size_factor

logger = logging.getLogger(__name__)


def _policy_from_env() -> NewsInfluencePolicy:
    """Build a NewsInfluencePolicy from NEWS_INFLUENCE_* env vars (fail-safe off)."""
    from src.runtime.runtime_flags import _news_influence_mode

    mode = _news_influence_mode({})

    def _f(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    try:
        return NewsInfluencePolicy(
            mode=mode,
            size_floor=_f("NEWS_INFLUENCE_SIZE_FLOOR", 0.5),
            oppose_threshold=_f("NEWS_INFLUENCE_OPPOSE_THRESHOLD", 0.05),
            event_risk_weight=_f("NEWS_INFLUENCE_EVENT_RISK_WEIGHT", 0.5),
        )
    except ValueError:  # bad knob → inert
        return NewsInfluencePolicy(mode="off")


def _side_from_direction(pkg: Any) -> str:
    """OrderPackage.direction is long/short; news_size_factor wants buy/sell."""
    d = str(getattr(pkg, "direction", "") or "").lower()
    if d in ("long", "buy"):
        return "buy"
    if d in ("short", "sell"):
        return "sell"
    return ""


def compute_news_factor(pkg: Any) -> tuple[float, dict]:
    """Resolve the news downsize factor for ``pkg``. ``1.0`` == no downsize.

    Never raises — any failure falls back to ``(1.0, ...)``.
    """
    try:
        policy = _policy_from_env()
        if policy.mode != "downsize":
            return 1.0, {"action": "no_policy", "mode": policy.mode}
        meta = getattr(pkg, "meta", None) or {}
        news = meta.get("news") if isinstance(meta, dict) else None
        if not isinstance(news, dict):
            return 1.0, {"action": "no_news_stamp"}
        adjustment = float(news.get("adjustment", 0.0) or 0.0)
        event_risk = float(news.get("event_risk", 0.0) or 0.0)
        factor, record = news_size_factor(
            adjustment,
            _side_from_direction(pkg),
            policy,
            flag_enabled=True,
            event_risk=event_risk,
        )
        return factor, record
    except Exception as exc:  # noqa: BLE001
        logger.warning("compute_news_factor failed: %s", exc)
        return 1.0, {"action": "error", "error": str(exc)}


def apply_news_downsize(pkg: Any, sized_qty: float, *, account_name: str = "") -> float:
    """Scale a RiskManager-computed per-account qty by the news factor.

    Reductive: ``sized_qty * factor`` with ``factor ∈ [size_floor, 1.0]`` (never
    amplifies). Computed once and cached on ``pkg.meta['_news_factor']``. Inert
    when ``NEWS_INFLUENCE_MODE`` is off/annotate (factor ``1.0``). Never raises —
    on any error the qty is returned unchanged.
    """
    try:
        if sized_qty is None or sized_qty <= 0:
            return sized_qty
        meta = getattr(pkg, "meta", None)
        if isinstance(meta, dict) and "_news_factor" in meta:
            factor = meta["_news_factor"]
        else:
            factor, record = compute_news_factor(pkg)
            if isinstance(meta, dict):
                meta["_news_factor"] = factor
                meta["news_influence_decision"] = record
        if factor >= 1.0:
            return sized_qty
        new_qty = sized_qty * factor
        logger.info(
            "news_downsize strategy=%s account=%s factor=%.4f qty %.8f -> %.8f",
            getattr(pkg, "strategy", "?"), account_name, factor, sized_qty, new_qty,
        )
        _log_news_sizing(pkg, account_name, sized_qty, new_qty, factor)
        return new_qty
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_news_downsize failed (returning unchanged qty): %s", exc)
        return sized_qty


def _log_news_sizing(
    pkg: Any, account_name: str, intended_qty: float, final_qty: float, factor: float,
) -> None:
    """Append the applied downsize to the news shadow-soak log."""
    try:
        from src.news.news_audit import news_decisions_path

        path = news_decisions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = getattr(pkg, "meta", None) or {}
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "influence_applied",
            "strategy": str(getattr(pkg, "strategy", "") or ""),
            "symbol": str(getattr(pkg, "symbol", "") or ""),
            "account": account_name,
            "action": "downsize",
            "factor": factor,
            "intended_qty": intended_qty,
            "final_qty": final_qty,
            "news_influence_decision": meta.get("news_influence_decision"),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except OSError as exc:
        logger.warning("_log_news_sizing: could not write audit log: %s", exc)
