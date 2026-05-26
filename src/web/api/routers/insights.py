"""AI Analyst insights endpoints (M13 S1).

Tier-1 read surface that serves natural-language insights + structured
grades over the bot's live trading data. Backed by a file-based cache
under ``runtime_logs/insights/`` written by the
``ict-insights-generator`` systemd timer (see ``src/runtime/insights/``
landing in M13 S1 PR C).

The router itself NEVER calls the Anthropic API and NEVER imports the
``anthropic`` SDK — its only job is to read the most recent cache file
and return it inside the envelope. The two-process split is
load-bearing: it caps per-request latency at file I/O, caps daily cost
at the timer's cadence, and means an Anthropic outage cannot wedge the
dashboard.

Endpoints (all unauth GET — operational telemetry, no secrets):

- ``GET /api/bot/insights/summary`` — overall system, last 24h.
- ``GET /api/bot/insights/recent?limit=N`` — last N closed trades.
- ``GET /api/bot/insights/strategy/{name}`` — per-strategy session.
- ``GET /api/bot/insights/health`` — narrative over the latest health
  snapshot.

Cache-miss path: a missing cache file (fresh deploy, first run not yet
fired, ``INSIGHTS_ENABLED=0``) returns 200 with a neutral placeholder
envelope (``summary_md: "<not yet generated>"``, ``grade: "good"``,
``signals: []``, ``cache_age_seconds: null``) so the dashboard renders
a placeholder rather than erroring.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/insights", tags=["insights"])

_INSIGHTS_DIR = runtime_logs_dir() / "insights"

# Strategy name must match the convention used by config/strategies.yaml
# (lowercase letters, digits, underscores; no slashes that could escape
# the cache dir). Mirrors the validation pattern used by
# src/web/api/routers/strategies.py.
_STRATEGY_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")


def _placeholder(cache_path: Path) -> dict[str, Any]:
    """Envelope returned when the cache file is missing or unreadable.

    Status 200 with a neutral payload — the dashboard surfaces "not yet
    generated" rather than erroring, which matches the existing
    /api/bot/health/* contract for missing snapshots.
    """
    return {
        "summary_md": "_Insights not yet generated. The generator timer "
        "writes a cache here every ~10 min; this placeholder shows until "
        "the first run lands._",
        "grade": "good",
        "signals": [],
        "data_window": None,
        "row_counts": None,
        "generated_at": None,
        "cache_age_seconds": None,
        "model_id": None,
        "cache_present": False,
        "cache_path": str(cache_path),
    }


def _read_cache(cache_path: Path) -> dict[str, Any]:
    """Read a cache file and stamp ``cache_age_seconds`` from its mtime.

    Returns the placeholder envelope (200) on any failure (missing,
    unreadable, malformed JSON). Logs a warning so the operator can
    diagnose the generator if needed.
    """
    if not cache_path.exists():
        return _placeholder(cache_path)
    try:
        with cache_path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("insights: failed to read %s: %s", cache_path, exc)
        return _placeholder(cache_path)
    if not isinstance(payload, dict):
        logger.warning("insights: %s is not a JSON object", cache_path)
        return _placeholder(cache_path)

    age_seconds = max(
        0,
        int(datetime.now(timezone.utc).timestamp() - cache_path.stat().st_mtime),
    )
    payload.setdefault("summary_md", "")
    payload.setdefault("grade", "good")
    payload.setdefault("signals", [])
    payload.setdefault("data_window", None)
    payload.setdefault("row_counts", None)
    payload.setdefault("generated_at", None)
    payload.setdefault("model_id", None)
    payload["cache_age_seconds"] = age_seconds
    payload["cache_present"] = True
    payload["cache_path"] = str(cache_path)
    return payload


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    return _read_cache(_INSIGHTS_DIR / "summary.json")


@router.get("/recent")
def get_recent(
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    """Narrative over the last N closed trades.

    ``limit`` is honoured by the generator at write time. The cache here
    holds whatever ``limit`` the generator used most recently; the
    request-time ``limit`` is echoed back so the consumer can compare
    against what the cache actually reflects.
    """
    payload = _read_cache(_INSIGHTS_DIR / "recent.json")
    payload["requested_limit"] = limit
    return payload


@router.get("/strategy/{name}")
def get_strategy(name: str) -> dict[str, Any]:
    """Per-strategy session view.

    ``name`` is validated against the strategy-name pattern to keep the
    cache lookup safely inside the insights dir.
    """
    if not _STRATEGY_NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="invalid strategy name")
    return _read_cache(_INSIGHTS_DIR / f"strategy_{name}.json")


@router.get("/health")
def get_health() -> dict[str, Any]:
    return _read_cache(_INSIGHTS_DIR / "health.json")


# ---------------------------------------------------------------------------
# History + usage (M13 S1 / PR F)
#
# These two endpoints expose the persistent side of the analyst — the
# `trade_journal.db::insights_history` table (one row per generator run)
# and `insights_usage` table (per-call tokens + estimated cost). Both
# are read-only of the canonical store; the writes are done by the
# generator process landed in PR C.
#
# Unlike the cache-only endpoints above, these DO touch the DB — they
# use the same `src.runtime.insights.{history,usage}` helpers the
# generator uses for its writes, so the read shape is guaranteed to
# match. The router still does NOT import `anthropic`.
# ---------------------------------------------------------------------------


@router.get("/history")
def get_history(
    endpoint: str = Query(..., description="summary | recent | strategy | health"),
    hours: int = Query(default=24, ge=1, le=24 * 7),
    limit: int = Query(default=50, ge=1, le=500),
    strategy_name: str | None = Query(default=None, description="filter when endpoint=strategy"),
) -> dict[str, Any]:
    """Newest-first rows from ``insights_history`` for an endpoint.

    ``endpoint`` is the same enum the generator uses (``summary`` /
    ``recent`` / ``strategy`` / ``health``). For ``strategy`` an
    optional ``strategy_name`` filter scopes to one strategy's rows.

    Returns ``{rows: [...], count, hours, endpoint, strategy_name}``.
    Each row carries the decoded ``signals`` / ``data_window`` /
    ``row_counts`` / ``payload`` so the consumer can drill in without
    a second query. Empty rows when the table doesn't exist yet or no
    runs land in the window — never errors.
    """
    if endpoint not in {"summary", "recent", "strategy", "health"}:
        raise HTTPException(status_code=400, detail="invalid endpoint")

    if strategy_name is not None and not _STRATEGY_NAME_PATTERN.match(strategy_name):
        raise HTTPException(status_code=400, detail="invalid strategy name")

    # Lazy-import so the cache-only endpoints above still work even if
    # the runtime/insights/ package isn't importable (e.g. a stripped-
    # down deploy). The router stays useful as long as the cache files
    # are present.
    try:
        from src.runtime.insights import history as history_mod
    except ImportError as exc:
        logger.warning("insights: history module not importable: %s", exc)
        return {
            "rows": [],
            "count": 0,
            "endpoint": endpoint,
            "hours": hours,
            "limit": limit,
            "strategy_name": strategy_name,
            "table_present": False,
        }

    rows = history_mod.recent_history(
        endpoint=endpoint,
        hours=hours,
        limit=limit,
        strategy_name=strategy_name,
    )
    return {
        "rows": rows,
        "count": len(rows),
        "endpoint": endpoint,
        "hours": hours,
        "limit": limit,
        "strategy_name": strategy_name,
        "table_present": True,
    }


@router.get("/usage")
def get_usage() -> dict[str, Any]:
    """Per-month cost + token total + per-endpoint split.

    Returns the shape ``src.runtime.insights.usage.summarize_usage``
    produces:

      {
        "current_month_usd":   <float>,
        "current_month_tokens": <int>,
        "current_month_calls":  <int>,
        "budget_usd":           <float, INSIGHTS_MONTHLY_BUDGET_USD>,
        "month_start":          "<iso>",
        "by_endpoint": [{"endpoint": "summary", "spent": ..., "calls": ...}, ...],
        "price_table_as_of":   "YYYY-MM-DD",
        "table_present":        <bool>
      }
    """
    try:
        from src.runtime.insights import usage as usage_mod
    except ImportError as exc:
        logger.warning("insights: usage module not importable: %s", exc)
        return {
            "current_month_usd": 0.0,
            "current_month_tokens": 0,
            "current_month_calls": 0,
            "budget_usd": 0.0,
            "month_start": None,
            "by_endpoint": [],
            "price_table_as_of": None,
            "table_present": False,
        }
    return usage_mod.summarize_usage()
