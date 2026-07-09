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
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter

from src.config.accounts_loader import load_accounts_dict
from src.utils.paths import runtime_logs_dir, trade_journal_db_path
from src.web.api._clean_trades import (
    exclude_reconciler_predicate,
    exclude_reset_flat_predicate,
    exclude_superseded_predicate,
    not_paper_predicate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_STRATEGIES_YAML = _REPO_ROOT / "config" / "strategies.yaml"
_ACCOUNTS_YAML = _REPO_ROOT / "config" / "accounts.yaml"
_CHANGELOG_JSON = _REPO_ROOT / "config" / "strategy_changelog.json"
_DESCRIPTIONS_JSON = _REPO_ROOT / "config" / "strategy_descriptions.json"
_DB_PATH = Path(trade_journal_db_path())

# Freshness window (seconds) for treating the pipeline's last tick as
# "running". The trader writes runtime_status.json every tick and the
# heartbeat every 60s; 5 min is comfortably beyond a normal tick gap
# without masking a genuine stall. Mirrors the dashboard's intent.
_TICK_FRESH_S = 300

def _load_descriptions() -> Dict[str, Dict[str, str]]:
    """Per-strategy human-readable descriptions.

    Single source of truth is ``config/strategy_descriptions.json`` (keyed
    by strategy name → ``{short, how_it_works}``), kept as a sibling of
    ``strategy_changelog.json`` so the strategy roster's prose metadata
    lives outside the Tier-3 ``strategies.yaml``. Authoring/updating an
    entry is a step in the ``new-strategy`` skill.
    """
    if not _DESCRIPTIONS_JSON.exists():
        return {}
    try:
        with _DESCRIPTIONS_JSON.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # allow-silent: best-effort json read; logs + returns safe empty default
        logger.exception("strategies: failed to load strategy_descriptions.json")
        return {}


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
            # Real-money only (account_class authoritative, is_demo fallback;
            # excludes paper AND prop). Previously NO paper filter → paper/prop
            # trades blended into the Strategies tab's lifetime per-strategy
            # stats ("real and paper never blended" contract). `resolved` counts
            # trades with a non-NULL pnl; win_rate / avg_pnl are computed over
            # `resolved` (not `total_trades`) so a reconciler-incomplete NULL-pnl
            # row no longer counts as a loss / deflates expectancy — matching
            # /performance. `total_trades` stays the full closed count so the
            # exit_reasons breakdown still covers every closed trade.
            rows = conn.execute(
                """
                SELECT
                    COALESCE(strategy_name, 'unknown') AS strategy_name,
                    COUNT(*) AS total_trades,
                    SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(SUM(pnl), 4) AS total_pnl,
                    exit_reason
                FROM trades
                WHERE status = 'closed'
                  AND COALESCE(is_backtest, 0) = 0
                """
                # Canonical predicates (src.web.api._clean_trades): real-money
                # only + drop reconciler ``orphan_adopt`` artifacts from the
                # per-strategy lifetime stats.
                + not_paper_predicate("")
                + exclude_reconciler_predicate("")
                + exclude_superseded_predicate("")
                + exclude_reset_flat_predicate("")
                + """
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
                "resolved": 0,
                "wins": 0,
                "total_pnl": 0.0,
                "exit_reasons": {},
            }
        s = by_strategy[name]
        s["total_trades"] += row["total_trades"]
        s["resolved"] += (row["resolved"] or 0)
        s["wins"] += row["wins"]
        s["total_pnl"] = round(s["total_pnl"] + (row["total_pnl"] or 0.0), 4)
        reason = _normalise_exit_reason(row["exit_reason"])
        s["exit_reasons"][reason] = s["exit_reasons"].get(reason, 0) + row["total_trades"]

    result: Dict[str, Dict[str, Any]] = {}
    for name, s in by_strategy.items():
        total = s["total_trades"]
        resolved = s["resolved"]
        wins = s["wins"]
        # losses over the RESOLVED set (win-rate denominator), so an unresolved
        # NULL-pnl closed trade is neither a win nor a loss.
        losses = resolved - wins
        total_pnl = s["total_pnl"]
        result[name] = {
            "total_trades": total,
            "resolved_trades": resolved,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / resolved * 100, 1) if resolved else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl_per_trade": round(total_pnl / resolved, 4) if resolved else 0.0,
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
        "resolved_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate_pct": 0.0,
        "total_pnl": 0.0,
        "avg_pnl_per_trade": 0.0,
        "exit_reasons": {},
    }


def _read_runtime_status() -> Dict[str, Any]:
    """The pipeline's per-tick runtime_status.json (live view of what's running).

    Carries ``strategies`` (the names the running process actually loaded),
    ``live`` (per-account live/dry), and ``last_tick_utc``. Empty dict if
    the file is missing/unreadable (pipeline never wrote one yet)."""
    path = runtime_logs_dir() / "runtime_status.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("strategies: runtime_status read failed")
        return {}
    return raw if isinstance(raw, dict) else {}


def _account_routing() -> Dict[str, List[str]]:
    """Map each strategy → the account ids that route it.

    Source of truth for "which accounts run this strategy": each
    account's per-account ``strategies`` filter (the coordinator's
    dispatch gate). Read via the canonical ``load_accounts_dict`` (the
    dict-shape reader) — never a hand-rolled parser. Returns
    {strategy_name: [account_id, ...]}."""
    errors: List[Dict[str, Any]] = []
    try:
        accounts = load_accounts_dict(_ACCOUNTS_YAML, errors=errors)
    except Exception:  # allow-silent: best-effort; logs + safe default
        logger.exception("strategies: failed to load account routing")
        return {}
    routing: Dict[str, List[str]] = {}
    for aid, acfg in (accounts or {}).items():
        for sname in ((acfg or {}).get("strategies") or []):
            routing.setdefault(str(sname), []).append(str(aid))
    return routing


def _tick_age_seconds(last_tick_utc: Any) -> Optional[float]:
    if not isinstance(last_tick_utc, str):
        return None
    try:
        ts = datetime.fromisoformat(last_tick_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


@router.get("/strategies")
def get_strategies() -> Dict[str, Any]:
    """Return config, live-runtime status, stats, descriptions, and changelog.

    "Live runtime" surfaces what the bot is **actually** running, not just
    the static YAML: ``loaded`` (the running process reported this strategy
    in its per-tick runtime_status), ``running`` (loaded AND the last tick
    is fresh), and ``accounts`` (which accounts route the strategy, with
    each account's live/dry state). This is what makes the Strategies tab a
    transparent view of the VM rather than a config echo."""
    strategies_cfg = _load_strategies_yaml()
    changelog = _load_changelog()
    descriptions = _load_descriptions()
    stats_by_name = _query_stats(_DB_PATH)
    rt = _read_runtime_status()
    routing = _account_routing()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    loaded_set = {str(s) for s in (rt.get("strategies") or [])}
    live_map = rt.get("live") if isinstance(rt.get("live"), dict) else {}
    last_tick = rt.get("last_tick_utc")
    tick_age = _tick_age_seconds(last_tick)
    bot_running = tick_age is not None and tick_age <= _TICK_FRESH_S

    out: List[Dict[str, Any]] = []
    for name, cfg in strategies_cfg.items():
        if not isinstance(cfg, dict):
            continue
        stats = stats_by_name.get(name, _empty_stats())
        desc = descriptions.get(name, {"short": name, "how_it_works": ""})
        loaded = name in loaded_set
        accounts = [
            {"id": aid, "live": bool(live_map.get(aid, False))}
            for aid in routing.get(name, [])
        ]
        out.append({
            "name": name,
            "enabled": bool(cfg.get("enabled", True)),
            # Strategy-level execution gate (config/strategies.yaml::execution).
            # Default-permissive: omitted → "live" (the canonical gate — what's
            # declared runs). Surfaced as a clean top-level field so consumers
            # can count the fleet by stage (live / shadow / disabled) without
            # digging into raw `config` — the authoritative source for the
            # dashboard + Android "strategy fleet" executive summary.
            "execution": str(cfg.get("execution", "live")).strip().lower() or "live",
            # Live-runtime truth (vs the static `enabled` flag above).
            "loaded": loaded,
            "running": bool(loaded and bot_running),
            "accounts": accounts,
            "risk_pct": cfg.get("risk_pct"),
            "timeframe": cfg.get("timeframe"),
            "symbols": cfg.get("symbols", []),
            "config": cfg,
            "description": desc,
            "stats": stats,
            "changelog": changelog.get(name, []),
        })

    return {
        "as_of": now,
        "runtime": {
            "bot_running": bot_running,
            "last_tick_utc": last_tick,
            "tick_age_seconds": round(tick_age, 1) if tick_age is not None else None,
            "loaded_strategies": sorted(loaded_set),
        },
        "strategies": out,
    }
