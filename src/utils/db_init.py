"""Trade-journal SQLite initialization helpers (D-3).

Centralises the "enable WAL + tune synchronous" boot step. WAL mode
allows concurrent readers + one writer without lock contention, which
removes a class of ``sqlite3.OperationalError: database is locked``
errors when the pipeline, order_monitor, dashboard API, and diag
relay all touch the trade journal at once.

WAL is a persistent file-level setting in SQLite — once set, it
survives across processes and connections. Calling
``enable_wal_mode()`` on every boot is idempotent and cheap.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


def journal_db_path() -> str:
    """Resolve the trade-journal DB path.

    Delegates to the canonical ``trade_journal_db_path()`` resolver
    (``TRADE_JOURNAL_DB`` env → ``$DATA_DIR/trade_journal.db`` →
    repo-root ``trade_journal.db``). Never CWD-relative.
    """
    from src.utils.paths import trade_journal_db_path
    return trade_journal_db_path()


def enable_wal_mode(db_path: Optional[str] = None) -> bool:
    """Enable WAL journal mode on the trade-journal database.

    Returns True when WAL is active after the call, False on any error
    (logs a warning — never raises so a misconfigured DB path can't
    block trader boot).
    """
    path = db_path or journal_db_path()
    try:
        with sqlite3.connect(path, timeout=5) as conn:
            mode_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA synchronous=NORMAL")
            active = bool(mode_row and str(mode_row[0]).lower() == "wal")
            if active:
                logger.info("trade journal: WAL mode active (%s)", path)
            else:
                logger.warning(
                    "trade journal: WAL pragma returned %s (not active)", mode_row,
                )
            return active
    except Exception as exc:  # noqa: BLE001
        logger.warning("trade journal: WAL enable skipped — %s", exc)
        return False
