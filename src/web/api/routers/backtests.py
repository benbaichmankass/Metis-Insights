"""M5 P4 — GET /api/bot/backtests.

Tier-1 read endpoint for the dashboard's Backtests tab. Returns the
N most recent rows from ``trade_journal.db::backtest_results``, the
table populated by the M5 backtest consumer (one row per
``/test <strategy>`` invocation).

The dashboard tab consumes this list to surface a strategy-test
history with the headline metrics from each run; the operator can
pull the full row by ``id`` from the DB if they need raw config /
percentile fields not surfaced here.

Wire-shape (camelCase per the dashboard convention):

    { id, strategy, runDate, startDate, endDate,
      totalTrades, winningTrades, losingTrades,
      winRate, profitFactor, expectancy,
      sharpeRatio, maxDrawdownPct, totalPnl,
      createdAt }

See ``docs/api-tier-policy.md`` Tier 1.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.utils.paths import runtime_logs_dir, trade_journal_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(trade_journal_db_path())

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Trainer-mirror subpath holding the strategy-improvement / validation
# backtest sweeps. The trainer VM runs ``scripts/ops/run_backtest_sweep.sh``
# (output → ``$ICT_TRADER_DATA_ROOT/backtests/<UTC-date>/``) and
# ``scripts/ops/publish_trainer_mirror.sh`` rsyncs the small JSON/MD
# artifacts (never the multi-MB candle CSVs) into this directory on the
# live VM. Resolved per-request via ``runtime_logs_dir()`` so the
# ``DATA_DIR`` / ``RUNTIME_LOGS_DIR`` env overrides apply consistently
# (same contract as ``src/web/api/routers/training_center.py``).
_SWEEPS_SUBPATH = ("trainer_mirror", "backtests")
SWEEPS_DEFAULT_LIMIT = 20
SWEEPS_MAX_LIMIT = 100


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_to_wire(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        # ``id`` is stringified to match the rest of this API surface
        # (``trades_closed.py`` and the ``positions`` endpoint both
        # serialise integer DB ids as strings). Keeping the convention
        # uniform lets the dashboard treat ``id`` the same way across
        # every list endpoint.
        "id": str(row["id"]),
        # ``strategy_version`` is the column the M5 consumer stamps
        # with the strategy name (see run_backtest_m5.py); surface
        # under the friendlier ``strategy`` key for the dashboard.
        "strategy": row["strategy_version"],
        "runDate": row["run_date"],
        "startDate": row["start_date"],
        "endDate": row["end_date"],
        "totalTrades": _coerce_int(row["total_trades"]) or 0,
        "winningTrades": _coerce_int(row["winning_trades"]) or 0,
        "losingTrades": _coerce_int(row["losing_trades"]) or 0,
        "winRate": _coerce_float(row["win_rate"]),
        "profitFactor": _coerce_float(row["profit_factor"]),
        "expectancy": _coerce_float(row["expectancy"]),
        "sharpeRatio": _coerce_float(row["sharpe_ratio"]),
        "maxDrawdownPct": _coerce_float(row["max_drawdown_pct"]),
        "totalPnl": _coerce_float(row["total_pnl"]),
        "createdAt": row["created_at"],
    }


def _query_backtests(
    db_path: Path,
    limit: int,
    strategy: Optional[str],
) -> List[Dict[str, Any]]:
    """Return up to *limit* backtest rows, newest-first by id.

    ``id`` is a monotonic AUTOINCREMENT and the M5 consumer never
    backdates inserts, so ordering by id is equivalent to ordering
    by ``created_at`` and avoids a string-compare on the timestamp.

    *strategy* (optional, exact match against ``strategy_version``)
    lets the dashboard filter the history to one strategy at a time.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT id, run_date, strategy_version, start_date, end_date, "
            "total_trades, winning_trades, losing_trades, win_rate, "
            "profit_factor, expectancy, sharpe_ratio, max_drawdown_pct, "
            "total_pnl, created_at "
            "FROM backtest_results"
        )
        params: List[Any] = []
        if strategy:
            sql += " WHERE strategy_version = ?"
            params.append(strategy)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_wire(r) for r in rows]


@router.get("/backtests")
def get_backtests(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    strategy: Optional[str] = Query(None, max_length=64),
) -> List[Dict[str, Any]]:
    """Return up to ``limit`` recent backtest result rows.

    Best-effort: returns ``[]`` on missing DB, missing
    ``backtest_results`` table (fresh checkout, M5 consumer never
    ran), or any sqlite read error. The dashboard treats an empty
    list as "no backtests yet" and keeps the tab usable.
    """
    if not _DB_PATH.exists():
        return []
    try:
        return _query_backtests(_DB_PATH, limit, strategy)
    except sqlite3.OperationalError as exc:
        # "no such table: backtest_results" lands here on a fresh
        # checkout where the M5 consumer has never written; collapse
        # to an empty list instead of a 500.
        if "no such table" in str(exc).lower():
            return []
        logger.exception("backtests: sqlite operational error")
        return []
    except sqlite3.Error:  # allow-silent: tier-1 dashboard read; logged via logger.exception, dashboard treats [] as "no data" — same contract as trades_closed.py
        logger.exception("backtests: sqlite read failed")
        return []
    except Exception:  # noqa: BLE001  # allow-silent: tier-1 dashboard read; logged via logger.exception — never 500 the dashboard tab on an unexpected error
        logger.exception("backtests: unexpected error")
        return []


def _sweeps_root() -> Path:
    return runtime_logs_dir().joinpath(*_SWEEPS_SUBPATH)


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _sweep_to_wire(sweep_dir: Path) -> Dict[str, Any]:
    """One mirrored backtest-sweep dir → wire dict.

    ``summary_md`` is the human-readable comparable table the harness
    writes (``SUMMARY.md``) — schema-stable across run modes, so it is
    the primary dashboard render. ``metrics`` is the raw
    ``all_metrics.json`` dump (per-variant Metrics; shape varies by run
    mode) for drill-down. ``extra_metrics`` collects any sibling
    ``*_metrics.json`` (e.g. ``ict_scalp_metrics.json``) the harness
    emits separately.
    """
    summary_path = sweep_dir / "SUMMARY.md"
    generated_at: Optional[str] = None
    try:
        # Prefer SUMMARY.md mtime; fall back to the dir mtime so a sweep
        # with no SUMMARY still carries a timestamp.
        stat_src = summary_path if summary_path.exists() else sweep_dir
        generated_at = (
            datetime.fromtimestamp(stat_src.stat().st_mtime, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except OSError:
        generated_at = None

    extra: Dict[str, Any] = {}
    for f in sorted(sweep_dir.glob("*_metrics.json")):
        if f.name == "all_metrics.json":
            continue
        parsed = _read_json(f)
        if parsed is not None:
            extra[f.name] = parsed

    return {
        "date": sweep_dir.name,
        "summary_md": _read_text(summary_path),
        "metrics": _read_json(sweep_dir / "all_metrics.json"),
        "extra_metrics": extra,
        "generated_at": generated_at,
    }


@router.get("/backtests/sweeps")
def get_backtest_sweeps(
    limit: int = Query(SWEEPS_DEFAULT_LIMIT, ge=1, le=SWEEPS_MAX_LIMIT),
) -> Dict[str, Any]:
    """Return the mirrored strategy-improvement / validation backtest sweeps.

    Newest-first by directory name (the harness names each run
    ``<UTC-date>``). Best-effort: a missing mirror dir (trainer never
    published, or this is a fresh checkout) returns
    ``{present: False, sweeps: []}`` rather than a 500, so the dashboard
    tab stays usable.

    See ``docs/runbooks/trainer-backtest.md`` for the producer side and
    ``scripts/ops/publish_trainer_mirror.sh`` for the mirror rsync.
    """
    root = _sweeps_root()
    if not root.exists():
        return {"present": False, "dir": str(root), "mirror_age_seconds": None, "sweeps": []}
    try:
        dirs = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )[:limit]
        sweeps = [_sweep_to_wire(d) for d in dirs]
        mirror_age: Optional[float] = None
        mtimes = [p.stat().st_mtime for p in root.rglob("*") if p.is_file()]
        if mtimes:
            mirror_age = round(time.time() - max(mtimes), 1)
        return {
            "present": True,
            "dir": str(root),
            "mirror_age_seconds": mirror_age,
            "sweeps": sweeps,
        }
    except OSError:
        logger.exception("backtests: sweeps read failed")
        return {"present": False, "dir": str(root), "mirror_age_seconds": None, "sweeps": []}
