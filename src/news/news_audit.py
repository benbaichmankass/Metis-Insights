"""Shadow-soak audit log for the M9 news layer.

Writes one JSON line per actionable signal that the news layer evaluated to
``runtime_logs/news_decisions.jsonl`` — the OBSERVE-ONLY record we accrue before
the layer is ever allowed to block or resize a live order. It captures what the
layer *would* have decided (veto / boost / reduce / neutral, the adjustment, the
query used, the symbol) so a later review can answer "would enabling this veto
have helped or hurt?" against real trades, exactly as the shadow-model ladder
accrues a track record before promotion.

Discipline (mirrors the rest of the news package + the context-snapshot writer):
  - **Best-effort.** Every error is swallowed; a failed write never affects the
    trade. Returns ``True`` on a successful append, ``False`` otherwise.
  - **Gated on layer-active.** The pipeline only calls this when
    ``news_client.is_active`` is true, so the log stays empty (no per-tick noise)
    until the layer is enabled.
  - **No order influence.** Writing this record changes nothing about the trade.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.news.news_score import NewsScoreResult
from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

_LOG_BASENAME = "news_decisions.jsonl"


def news_decisions_path():
    """Absolute path to the shadow-soak log (under the canonical runtime_logs dir)."""
    return runtime_logs_dir() / _LOG_BASENAME


def _top_items(result: NewsScoreResult, limit: int = 3) -> list:
    """The top-N scored news items the layer read, as ``{headline, url, score}``.

    Surfaced on ``/api/bot/news/recent`` so a consumer can render a clickable
    ticker of the specific articles that drove the decision (highest absolute
    score first — the items that moved the tilt most). Best-effort: a missing
    field degrades to an empty string, never raises.
    """
    try:
        scores = list(result.raw_scores or [])
        scores.sort(key=lambda s: abs(float(s.get("score", 0.0) or 0.0)), reverse=True)
        out = []
        for s in scores[:limit]:
            headline = str(s.get("headline", "") or "")
            if not headline:
                continue
            out.append(
                {
                    "headline": headline[:200],
                    "url": str(s.get("url", "") or ""),
                    "score": s.get("score", 0.0),
                }
            )
        return out
    except Exception:  # noqa: BLE001
        return []


def log_news_decision(
    *,
    result: NewsScoreResult,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    strategy: Optional[str] = None,
    query: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """Append one shadow-decision record. Never raises; returns success bool."""
    try:
        record: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": side,
            "strategy": strategy,
            "query": query,
            "decision": result.decision,
            "adjustment": result.adjustment,
            "veto": result.veto,
            "item_count": result.item_count,
            "reason": (result.reason or "")[:240],
            "top_items": _top_items(result),
        }
        if extra:
            record.update(extra)
        path = news_decisions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("news_audit: failed to log decision — %s", exc)
        return False
