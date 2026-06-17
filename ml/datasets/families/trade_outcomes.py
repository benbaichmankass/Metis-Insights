"""`trade_outcomes` dataset family (WS5-A; backtest augmentation S-MLOPT-S7).

Reads CLOSED trades from `trade_journal.db::trades` and emits them with a
derived `won` label (`pnl > 0`). The first label dataset on the AI-traders
track.

By default only **live** (non-backtest) trades are read — unchanged WS5-A
behavior. Pass `include_backtest=True` (S-MLOPT-S7, Phase 1.3) to ALSO read
`is_backtest = 1` rows recorded by the backtest harnesses; every row then
carries a `source` field (`"live"` / `"backtest"`) so a trainer can train on
live+backtest while the evaluator holds out **real** trades only (`source ==
"live"`, via the `live_holdout` split with `live_flag_column: source` +
`live_flag_true_value: live`). This manufactures more labeled training data
than the ~80 real trades collapse the decision models against (gap G4).

Leakage discipline: the dataset includes both `pnl` (outcome) and
`won` (label derived from `pnl`). Any trainer consuming this family
MUST scope its feature columns explicitly to avoid trivial leakage
(`pnl`-as-feature predicts `won` perfectly). The dataset metadata
carries `leakage_test_status: skipped` precisely because leakage
prevention is the trainer's responsibility, not the dataset's.

Builder is read-only against the live DB (SQLite `mode=ro` URI).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from src.runtime.local_pnl import canon_direction

from ..builder import DatasetBuilder
from ..metadata import LeakageStatus

_RAW_COLUMNS = (
    "id",
    "timestamp",
    "symbol",
    "direction",
    "strategy_name",
    "setup_type",
    "killzone",
    "bias",
    "pnl",
    "pnl_percent",
    "account_id",
    "created_at",
)

# Columns that carry text but may be NULL upstream. The builder
# normalises NULL → empty string so the dataset has a single string
# type token per column.
_NULLABLE_TEXT = {
    "timestamp",
    "symbol",
    "direction",
    "strategy_name",
    "setup_type",
    "killzone",
    "bias",
    "account_id",
    "created_at",
}


class TradeOutcomesBuilder(DatasetBuilder):
    family: ClassVar[str] = "trade_outcomes"
    builder_version: ClassVar[str] = "v1"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.SKIPPED
    label_version: ClassVar[str] = "won-from-pnl-v1"
    schema: ClassVar[Mapping[str, type]] = {
        "id": int,
        "timestamp": str,
        "symbol": str,
        "direction": str,
        "strategy_name": str,
        "setup_type": str,
        "killzone": str,
        "bias": str,
        "pnl": float,
        "pnl_percent": float,
        "account_id": str,
        "created_at": str,
        "won": bool,
        "source": str,   # "live" | "backtest" (S-MLOPT-S7)
    }

    def iter_rows(
        self,
        *,
        db_path: Path,
        strategy_name: str | None = None,
        symbol: str | None = None,
        include_backtest: bool = False,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if not db_path.is_file():
            raise FileNotFoundError(f"trade_journal.db not found at {db_path}")
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            select_cols = ", ".join(_RAW_COLUMNS) + ", is_backtest"
            sql = f"SELECT {select_cols} FROM trades WHERE status = 'closed'"
            if not include_backtest:
                sql += " AND is_backtest = 0"
            params: list[Any] = []
            if strategy_name is not None:
                sql += " AND strategy_name = ?"
                params.append(strategy_name)
            if symbol is not None:
                sql += " AND symbol = ?"
                params.append(symbol)
            sql += " ORDER BY id ASC"
            for row in conn.execute(sql, params):
                pnl = row["pnl"]
                if pnl is None:
                    # CLOSED but no pnl is a malformed row; skip rather
                    # than emit an unlabelled label.
                    continue
                payload: dict[str, Any] = {}
                for col in _RAW_COLUMNS:
                    value = row[col]
                    if col in _NULLABLE_TEXT and value is None:
                        payload[col] = ""
                    elif col == "pnl" or col == "pnl_percent":
                        payload[col] = float(value) if value is not None else 0.0
                    elif col == "id":
                        payload[col] = int(value)
                    elif col == "direction":
                        payload[col] = canon_direction(value) or value
                    else:
                        payload[col] = value
                payload["won"] = bool(payload["pnl"] > 0)
                payload["source"] = "backtest" if row["is_backtest"] else "live"
                yield payload
        finally:
            conn.close()
