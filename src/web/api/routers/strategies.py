"""GET /api/bot/strategies — strategy overview for the dashboard Strategies tab.

Returns per-strategy config, lifetime trade stats, and the update
changelog (from config/strategy_changelog.json). Tier 1 — no auth.

Response shape:
  {
    "as_of": "ISO-8601Z",
    "strategies": [
      {
        "name": "vwap",
        "enabled": true,
        "risk_pct": 1.0,
        "timeframe": "5m",
        "symbols": ["BTCUSDT"],
        "config": { ... raw yaml params ... },
        "description": { "short": "...", "how_it_works": "..." },
        "stats": {
          "total_trades": 42,
          "wins": 28,
          "losses": 14,
          "win_rate_pct": 66.7,
          "total_pnl": 124.50,
          "avg_pnl_per_trade": 2.97,
          "exit_reasons": { "sl": 14, "tp": 6, "vwap_cross": 18, "other": 4 }
        },
        "changelog": [
          { "date": "2026-05-12", "ref": "PR #1031", "summary": "..." }
        ]
      }
    ]
  }
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_STRATEGIES_YAML = _REPO_ROOT / "config" / "strategies.yaml"
_CHANGELOG_JSON = _REPO_ROOT / "config" / "strategy_changelog.json"
_DB_PATH = Path(os.environ.get("TRADE_JOURNAL_DB", str(_REPO_ROOT / "trade_journal.db")))

_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "vwap": {
        "short": "VWAP mean-reversion on 5m BTCUSDT",
        "how_it_works": (
            "Enters when price deviates significantly from the rolling session VWAP "
            "(Volume-Weighted Average Price). "
            "A 4h EMA-200 ±2% band gate blocks counter-trend entries — no longs when "
            "the market is in a confirmed downtrend, no shorts in an uptrend. "
            "Exits fire in priority order: SL cross → TP cross → VWAP reclaim → "
            "240-minute time decay. Risk: 1% per trade."
        ),
    },
    "turtle_soup": {
        "short": "Liquidity sweep + reversal, 15m setup / 1m entry",
        "how_it_works": (
            "Identifies false breakouts (liquidity sweeps) of recent swing highs and "
            "lows on the 15m chart. Waits for a reversal candle confirming rejection, "
            "then refines entry on the 1m chart. "
            "Stop is 0.30× ATR below/above entry. "
            "TP1 at 1.0R (25% position closed, stop moved to break-even), "
            "trailing ATR stop on the remainder, TP2 at 3.0R. "
            "Risk: 0.5% per trade."
        ),
    },
    "ict_scalp_5m": {
        "short": "ICT liquidity-sweep scalp on 5m BTCUSDT",
        "how_it_works": (
            "Trades the ICT sweep → displacement → fair-value-gap sequence. "
            "Waits for price to sweep a recent swing high/low (liquidity grab), "
            "confirm a displacement candle (body > 1.3× ATR), then enters on a "
            "wick-rejection back out of the resulting FVG. A 1h EMA-20 "
            "higher-timeframe bias gate blocks counter-trend entries. "
            "Stop is ATR-buffered beyond the sweep; take-profit at 1.5R. "
            "Risk: 0.3% per trade."
        ),
    },
}


def _load_strategies_yaml() -> Dict[str, Any]:
    if not _STRATEGIES_YAML.exists():
        return {}
    try:
        with _STRATEGIES_YAML.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return raw.get("strategies") or {} if isinstance(raw, dict) else {}
    except Exception:  # allow-silent: best-effort yaml read; logs + returns safe empty default
        logger.exception("strategies: failed to load strategies.yaml")
        return {}


def _load_changelog() -> Dict[str, List[Dict[str, str]]]:
    if not _CHANGELOG_JSON.exists():
        return {}
    try:
        with _CHANGELOG_JSON.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # allow-silent: best-effort json read; logs + returns safe empty default
        logger.exception("strategies: failed to load strategy_changelog.json")
        return {}


def _query_stats(db_path: Path) -> Dict[str, Dict[str, Any]]:
    """Return per-strategy aggregate stats from the trades table."""
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(strategy_name, 'unknown') AS strategy_name,
                    COUNT(*) AS total_trades,
                    SUM(CASE WHEN COALESCE(pnl, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(SUM(COALESCE(pnl, 0)), 4) AS total_pnl,
                    exit_reason
                FROM trades
                WHERE status = 'closed'
                  AND COALESCE(is_backtest, 0) = 0
                GROUP BY strategy_name, exit_reason
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:  # allow-silent: best-effort db read; logs + returns safe empty default
        logger.exception("strategies: sqlite read failed")
        return {}

    # Aggregate by strategy, collecting exit reason counts in a second pass.
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = row["strategy_name"]
        if name not in by_strategy:
            by_strategy[name] = {
                "total_trades": 0,
                "wins": 0,
                "total_pnl": 0.0,
                "exit_reasons": {},
            }
        s = by_strategy[name]
        s["total_trades"] += row["total_trades"]
        s["wins"] += row["wins"]
        s["total_pnl"] = round(s["total_pnl"] + (row["total_pnl"] or 0.0), 4)
        reason = _normalise_exit_reason(row["exit_reason"])
        s["exit_reasons"][reason] = s["exit_reasons"].get(reason, 0) + row["total_trades"]

    result: Dict[str, Dict[str, Any]] = {}
    for name, s in by_strategy.items():
        total = s["total_trades"]
        wins = s["wins"]
        losses = total - wins
        total_pnl = s["total_pnl"]
        result[name] = {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / total * 100, 1) if total else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl_per_trade": round(total_pnl / total, 4) if total else 0.0,
            "exit_reasons": s["exit_reasons"],
        }
    return result


def _normalise_exit_reason(raw: Optional[str]) -> str:
    if not raw:
        return "other"
    r = raw.strip().lower()
    if r in ("sl", "sl_cross"):
        return "sl"
    if r in ("tp", "tp_cross"):
        return "tp"
    if r == "vwap_cross":
        return "vwap_cross"
    if r == "time_decay":
        return "time_decay"
    if r.startswith("reconciler"):
        return "reconciler"
    return "other"


def _empty_stats() -> Dict[str, Any]:
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate_pct": 0.0,
        "total_pnl": 0.0,
        "avg_pnl_per_trade": 0.0,
        "exit_reasons": {},
    }


@router.get("/strategies")
async def get_strategies() -> Dict[str, Any]:
    """Return config, stats, descriptions, and changelog for every strategy."""
    strategies_cfg = _load_strategies_yaml()
    changelog = _load_changelog()
    stats_by_name = _query_stats(_DB_PATH)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    out: List[Dict[str, Any]] = []
    for name, cfg in strategies_cfg.items():
        if not isinstance(cfg, dict):
            continue
        stats = stats_by_name.get(name, _empty_stats())
        desc = _DESCRIPTIONS.get(name, {"short": name, "how_it_works": ""})
        out.append({
            "name": name,
            "enabled": bool(cfg.get("enabled", True)),
            "risk_pct": cfg.get("risk_pct"),
            "timeframe": cfg.get("timeframe"),
            "symbols": cfg.get("symbols", []),
            "config": cfg,
            "description": desc,
            "stats": stats,
            "changelog": changelog.get(name, []),
        })

    return {"as_of": now, "strategies": out}
