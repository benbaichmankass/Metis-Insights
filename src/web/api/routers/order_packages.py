"""Tier-1 read endpoint: order packages (decision-level) for the dashboard.

One row per order package from ``trade_journal.db::order_packages`` —
the bot's actual *decision* (which strategy proposed what), as opposed to
the fill-level ``trades`` view. Each row is enriched with:

  * ``pnl`` / ``tradeStatus`` from the linked trade (``linked_trade_id``).
  * ``claudeScore`` — the Claude strategy-decision grade from
    ``comms/claude_strategy_scores.jsonl`` (written by ``/health-review`` /
    ``scripts/ops/score_order_packages.py``), keyed by ``order_package_id``.
    ``None`` until a health-review has scored that package.
  * ``signalLogic`` / ``meta`` — the decision reasoning the bot recorded at
    signal time (``order_packages.signal_logic`` / ``meta`` TEXT columns,
    JSON-decoded to whatever shape the writer used; ``meta`` typically carries
    setup_type / killzone / bias). Powers the dashboard's open-trade detail
    card. ``None`` when the writer left the column empty.

Per-model **shadow scores** are intentionally NOT joined here. They are
keyed by trade and already served by ``/api/bot/trades/scores``; the
dashboard composes them onto these rows client-side via
``linkedTradeId`` (avoids duplicating the shadow-window aggregation).

Best-effort: missing DB / file → empty rows or null fields. Tier 1, no auth.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.utils.paths import trade_journal_db_path
from src.web.api._asset_class import asset_class_for_symbol
from src.web.api._clean_trades import account_class_wire, not_paper_predicate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(trade_journal_db_path())
_CLAUDE_SCORES = _REPO_ROOT / "comms" / "claude_strategy_scores.jsonl"

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Paper/not-paper split + the account_class wire helper come from the canonical
# src.web.api._clean_trades module (single source of truth). Joined ``trades``
# alias is ``t``. No reconciler exclusion here — this is a decision-level LIST
# (order packages); ``orphan_adopt`` rows have no order package so never appear.
_NOT_PAPER_PREDICATE = not_paper_predicate("t.")
_account_class_wire = account_class_wire


def _load_claude_scores() -> Dict[str, Dict[str, Any]]:
    """Map ``order_package_id`` → the latest Claude decision-score row.

    The file is append-only NDJSON; the first line is a ``_meta`` header and
    a package can be re-scored, so last-occurrence wins. Best-effort: a
    missing/malformed file yields an empty map (every package then renders
    ``claudeScore: null``).
    """
    if not _CLAUDE_SCORES.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with _CLAUDE_SCORES.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                opid = row.get("order_package_id")
                if not opid:
                    continue  # skips the `_meta` header row
                out[str(opid)] = row
    except OSError:  # allow-silent: best-effort read; logs + returns what we have
        logger.exception("order_packages: failed to read claude scores")
    return out


def _claude_score_wire(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "grade": row.get("decision_grade"),
        "score": row.get("decision_grade_score"),
        "entryQuality": row.get("entry_quality"),
        "exitQuality": row.get("exit_quality"),
        "riskManagement": row.get("risk_management"),
        "executed": row.get("executed"),
        "rationale": row.get("rationale"),
        "alternativeAction": row.get("alternative_action"),
        "reviewer": row.get("reviewer"),
        "reviewedAt": row.get("reviewed_at"),
    }


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _decode_json_field(v: Any) -> Any:
    """Decode a TEXT column that holds JSON (``meta`` / ``signal_logic``).

    The bot writes ``order_packages.meta`` and ``order_packages.signal_logic``
    as JSON strings (decision reasoning: setup_type, killzone, bias, the
    detector geometry, etc.). Return the parsed object when it's valid JSON,
    the raw string when it's plain text, and ``None`` when empty/null — so a
    consumer can render whatever shape the writer used without guessing.
    """
    if v is None:
        return None
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return s


def _query_order_packages(
    db_path: Path, limit: int, since: Optional[str], strategy: Optional[str],
    include_demo: bool = False,
) -> List[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # 2026-06-04 reporting-cleanup: select trades.is_demo so the wire
        # row carries the flag; demo rows are excluded by default so the
        # current consumer behavior is preserved. Pass include_demo=true
        # to get both segments (each row tagged via ``isDemo``).
        # Resolve each package to ONE representative trade row.
        #
        # ``order_packages.linked_trade_id`` is only written for the
        # real-money *primary OPEN entry*. Packages whose fill was a
        # demo/paper leg, an ``intent_reduce`` leg, or an orphan-adopt
        # leave ``linked_trade_id`` NULL — those trades reference the
        # package via the canonical ``trades.order_package_id`` column
        # instead. So a join on ``linked_trade_id`` alone leaves MANY
        # filled packages showing ``linkedTradeId: null`` + no PnL +
        # ``tradeStatus: null`` (the read-side of the
        # "trade with no order package" defect).
        #
        # Resolution rule (preserves one-row-per-package):
        #   1. prefer ``op.linked_trade_id`` (the existing primary-entry
        #      link — unchanged when present), else
        #   2. fall back to a trade where
        #      ``trades.order_package_id = op.order_package_id``. When
        #      several legs reference the package we pick ONE
        #      deterministically — a non-backtest leg, preferring an
        #      OPEN leg, then the most-recent id. This best matches what
        #      ``linked_trade_id`` would have pointed at (the live
        #      primary entry):
        #          ORDER BY (status='open') DESC, id DESC LIMIT 1
        #
        # ``op.linked_trade_id`` is still what the wire ``linkedTradeId``
        # reports (the package's own declared link — meaning unchanged);
        # only pnl / tradeStatus / accountClass / isDemo now resolve via
        # this fallback ``t``.
        #
        # The paper/backtest WHERE filter still evaluates on the resolved
        # ``t`` (alias unchanged), so its semantics are preserved:
        #   * a package whose ONLY trade is paper → the resolved ``t`` is
        #     that paper row → _NOT_PAPER_PREDICATE excludes it by default;
        #   * a package with NO trade at all → ``t.*`` is NULL →
        #     COALESCE(t.is_backtest,0)=0 passes and the NULL-tolerant
        #     NOT-paper predicate passes → it still appears (unexecuted).
        # The fallback sub-SELECT already restricts to non-backtest legs
        # so a package whose only ``order_package_id``-linked trade is a
        # backtest row resolves ``t`` to NULL and is treated as unexecuted
        # (not silently dropped by the outer is_backtest filter).
        sql = """
            SELECT op.order_package_id, op.strategy_name, op.symbol, op.direction,
                   op.entry, op.sl, op.tp, op.confidence,
                   op.created_at, op.updated_at, op.status, op.close_reason,
                   op.linked_trade_id, op.signal_logic, op.meta, op.model_scores,
                   t.pnl AS trade_pnl, t.status AS trade_status,
                   COALESCE(t.is_demo, 0) AS trade_is_demo,
                   t.account_class AS trade_account_class
            FROM order_packages op
            LEFT JOIN trades t ON t.id = COALESCE(
                op.linked_trade_id,
                (SELECT tx.id FROM trades tx
                  WHERE tx.order_package_id = op.order_package_id
                    AND COALESCE(tx.is_backtest, 0) = 0
                  ORDER BY (tx.status = 'open') DESC, tx.id DESC
                  LIMIT 1)
            )
            WHERE COALESCE(t.is_backtest, 0) = 0
        """
        if not include_demo:
            sql += _NOT_PAPER_PREDICATE
        params: List[Any] = []
        if strategy:
            sql += " AND op.strategy_name = ?"
            params.append(strategy)
        if since:
            sql += " AND datetime(op.created_at) >= datetime(?)"
            params.append(since)
        sql += " ORDER BY datetime(op.created_at) DESC LIMIT ?"
        params.append(limit)
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@router.get("/order-packages")
def get_order_packages(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    since: Optional[str] = Query(None, max_length=64),
    strategy: Optional[str] = Query(None, max_length=64),
    include_paper: bool = Query(False),
    include_demo: bool = Query(False),
) -> Dict[str, Any]:
    """Return up to ``limit`` order packages (newest-first by created_at),
    each enriched with its linked-trade PnL, the Claude decision score,
    and the ``accountClass`` / ``isDemo`` flags.

    ``include_paper=true`` includes paper-account packages alongside
    real-money (each row tagged via ``accountClass``). Default false
    preserves the prior behavior (real-money only). ``include_demo`` is a
    deprecated alias (effective include = include_paper OR include_demo).

    Best-effort: returns an empty ``rows`` list on missing DB or sqlite
    error so the dashboard tab stays usable.
    """
    effective_include = include_paper or include_demo
    if not _DB_PATH.exists():
        return {"rows": [], "count": 0, "claude_log_present": _CLAUDE_SCORES.is_file()}
    try:
        rows = _query_order_packages(
            _DB_PATH, limit, since, strategy, include_demo=effective_include,
        )
    except sqlite3.Error:  # allow-silent: best-effort read; logs + returns empty so the tab stays usable
        logger.exception("order_packages: sqlite read failed")
        return {"rows": [], "count": 0, "claude_log_present": _CLAUDE_SCORES.is_file()}

    claude = _load_claude_scores()
    out: List[Dict[str, Any]] = []
    for r in rows:
        opid = str(r["order_package_id"])
        linked = r["linked_trade_id"]
        out.append({
            "orderPackageId": opid,
            "createdAt": r["created_at"],
            "updatedAt": r["updated_at"],
            "strategy": r["strategy_name"],
            "symbol": r["symbol"],
            # ``assetClass`` — coarse reporting bucket for the symbol so a
            # consumer can group/filter order packages by asset group.
            # Reporting-only, config-driven with a heuristic fallback; never null.
            "assetClass": asset_class_for_symbol(r["symbol"]),
            "direction": r["direction"],
            "entry": _f(r["entry"]),
            "sl": _f(r["sl"]),
            "tp": _f(r["tp"]),
            "confidence": _f(r["confidence"]),
            "status": r["status"],
            "closeReason": r["close_reason"],
            "linkedTradeId": str(linked) if linked is not None else None,
            "pnl": _f(r["trade_pnl"]),
            "tradeStatus": r["trade_status"],
            "isDemo": bool(r["trade_is_demo"]),
            # accountClass ("paper" | "real_money") — canonical funding
            # category; never null (falls back to is_demo for old rows).
            "accountClass": _account_class_wire(
                r["trade_account_class"], r["trade_is_demo"],
            ),
            # Decision reasoning the bot recorded at signal time. Both are
            # JSON-or-text TEXT columns; decoded to whatever shape the writer
            # used (dict for structured meta/logic, str for plain text, None
            # when unset). `meta` typically carries setup_type / killzone /
            # bias; `signalLogic` the detector's decision trace.
            "signalLogic": _decode_json_field(r["signal_logic"]),
            "meta": _decode_json_field(r["meta"]),
            # Per-model ML decision scores captured at signal time —
            # {model_id: {stage, score}}. Cheap SELECT; replaces the
            # per-request shadow-log recompile for the consumer cards.
            "modelScores": _decode_json_field(r["model_scores"]),
            "claudeScore": _claude_score_wire(claude.get(opid)),
        })
    return {
        "rows": out,
        "count": len(out),
        "claude_log_present": _CLAUDE_SCORES.is_file(),
    }
