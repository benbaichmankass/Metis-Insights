"""S-013 M2 PR #2 — GET /api/pnl.

Read-only per-account P&L from ``trade_journal.db``. Account roster is
sourced from ``config/accounts.yaml`` so an account with zero live
trades still appears in the response (with all-zero values).

Empty journal → all zeros (200). DB file unreachable → 503.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from src.utils.paths import trade_journal_db_path
from src.web.api._clean_trades import (
    exclude_reconciler_predicate,
    exclude_superseded_predicate,
    not_paper_predicate,
)
from src.web.api.auth import require_session

router = APIRouter(prefix="/api", tags=["pnl"])

_REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_VERSION = 1


def _resolve_db_path() -> Path:
    return Path(trade_journal_db_path())


def _resolve_accounts_yaml() -> Path:
    return _REPO_ROOT / "config" / "accounts.yaml"


def _load_account_ids(accounts_yaml: Path) -> list[str]:
    import logging as _logging
    from src.config.accounts_loader import load_accounts_dict
    errors: list = []
    result = load_accounts_dict(accounts_yaml, errors=errors)
    for err in errors:
        try:
            from src.runtime.outcomes import Level, report
            report(
                "pnl_endpoint",
                "accounts_yaml_read_failed",
                level=Level.WARN,
                reason=err.get("error", "parse error"),
                path=err.get("path", str(accounts_yaml)),
            )
        except ImportError:
            _logging.getLogger(__name__).warning(
                "_load_account_ids: outcomes.report unavailable: %s", err
            )
    return list(result.keys())


def _zero_account() -> Dict[str, float]:
    return {"realized_usd": 0.0, "unrealized_usd": 0.0, "trades_today": 0}


def _query_pnl(
    db_path: Path, account_ids: list[str], now_utc: datetime
) -> Dict[str, Dict[str, float]]:
    if not db_path.exists():
        # DB hasn't been initialised yet — return all-zero per account.
        return {aid: _zero_account() for aid in account_ids}

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        # Realised + unrealised per account, live trades only.
        # status != 'open' → realised; status = 'open' → unrealised.
        cur.execute(
            """
            SELECT account_id,
                   COALESCE(SUM(CASE WHEN status != 'open' THEN pnl ELSE 0 END), 0) AS realized,
                   COALESCE(SUM(CASE WHEN status =  'open' THEN pnl ELSE 0 END), 0) AS unrealized
              FROM trades
             WHERE COALESCE(is_backtest, 0) = 0
            """
            # Canonical predicates (src.web.api._clean_trades): real-money only +
            # drop reconciler ``orphan_adopt`` artifacts from per-account PnL.
            + not_paper_predicate("")
            + exclude_reconciler_predicate("")
            + exclude_superseded_predicate("")
            + """
             GROUP BY account_id
            """
        )
        sums = {row[0]: (float(row[1]), float(row[2])) for row in cur.fetchall()}

        # Trades opened today (UTC). Exclude refusal rows so the count
        # reflects real exchange submissions (CP-2026-05-03-14).
        today_iso = now_utc.strftime("%Y-%m-%d")
        cur.execute(
            """
            SELECT account_id, COUNT(*) AS cnt
              FROM trades
             WHERE COALESCE(is_backtest, 0) = 0
            """
            + not_paper_predicate("")
            + exclude_reconciler_predicate("")
            + exclude_superseded_predicate("")
            + """
               AND COALESCE(status, 'open')
                       NOT IN ('rejected', 'exchange_rejected', 'rejected_too_small', 'orphaned')
               AND substr(COALESCE(created_at, timestamp), 1, 10) = ?
             GROUP BY account_id
            """,
            (today_iso,),
        )
        counts = {row[0]: int(row[1]) for row in cur.fetchall()}
    finally:
        conn.close()

    seen = set(account_ids)
    out: Dict[str, Dict[str, float]] = {aid: _zero_account() for aid in account_ids}
    # Surface any DB account_id we didn't know about (legacy 'live', etc.)
    for aid in sums.keys() | counts.keys():
        if aid not in seen:
            out[aid] = _zero_account()
    for aid, (realized, unrealized) in sums.items():
        out[aid]["realized_usd"] = round(realized, 2)
        out[aid]["unrealized_usd"] = round(unrealized, 2)
    for aid, cnt in counts.items():
        out[aid]["trades_today"] = cnt
    return out


def build_pnl(
    db_path: Optional[Path] = None,
    accounts_yaml: Optional[Path] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    db_path = db_path or _resolve_db_path()
    accounts_yaml = accounts_yaml or _resolve_accounts_yaml()
    now = now_utc or datetime.now(timezone.utc)
    account_ids = _load_account_ids(accounts_yaml)
    try:
        accounts = _query_pnl(db_path, account_ids, now)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "pnl_unavailable", "reason": f"db error: {exc.__class__.__name__}"},
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "accounts": accounts,
        "as_of_utc": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


@router.get("/pnl")
def get_pnl(_session: dict = Depends(require_session)) -> Dict[str, Any]:
    return build_pnl()
