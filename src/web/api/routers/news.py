"""`/api/bot/news/recent` — Tier-1 read surface for the M9 news layer.

Surfaces the news shadow-soak log (`runtime_logs/news_decisions.jsonl`) so the
dashboard can show what the news layer decided per actionable signal (decision,
adjustment, veto, query, symbol) and any applied influence downsizes. Read-only;
newest-first. Empty envelope (`present:false`) until the layer is active
(`NEWS_SOURCE=rss`, or `newsapi` + `NEWS_API_KEY`), which is when the writer
begins logging.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query

from src.news.news_audit import news_decisions_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/news/recent")
def news_recent(limit: int = Query(100, ge=1, le=500)) -> dict:
    """Newest-first tail of the news shadow-soak decisions log."""
    path = news_decisions_path()
    if not path.exists():
        return {"present": False, "log_path": str(path), "count": 0, "records": []}
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("news_recent: could not read %s — %s", path, exc)
        return {"present": True, "log_path": str(path), "count": 0, "records": [],
                "error": str(exc)}

    records = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(records) >= limit:
            break
    return {"present": True, "log_path": str(path), "count": len(records), "records": records}
