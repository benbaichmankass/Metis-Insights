"""`backtest_results` dataset family (WS3).

Reads aggregate backtest run summaries from the live
`trade_journal.db` (table `backtest_results`, populated by the M5
backtest consumer) and emits them as a versioned dataset under the
canonical layout. Only stable columns are exported; the schema below
is the contract.

Safety: this builder is read-only. It opens the SQLite file in URI
mode with `mode=ro` so a runaway builder cannot mutate the live DB.
No network access; no exchange calls.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from ..builder import DatasetBuilder

# Stable column subset: id + identifying fields + headline metrics. The
# live table also includes profit_factor, expectancy, avg_win, etc.; we
# omit those here on purpose to keep the family schema small and
# stable. Adding columns is a schema bump and triggers a new dataset
# version; removing one is a breaking change and requires a follow-up.
_COLUMNS = (
    "id",
    "run_date",
    "strategy_version",
    "start_date",
    "end_date",
    "total_trades",
    "winning_trades",
    "losing_trades",
    "win_rate",
    "sharpe_ratio",
    "total_pnl",
    "total_pnl_pct",
    "max_drawdown_pct",
    "created_at",
)


class BacktestResultsBuilder(DatasetBuilder):
    family: ClassVar[str] = "backtest_results"
    builder_version: ClassVar[str] = "v1"
    schema: ClassVar[Mapping[str, type]] = {
        "id": int,
        "run_date": str,
        "strategy_version": str,
        "start_date": str,
        "end_date": str,
        "total_trades": int,
        "winning_trades": int,
        "losing_trades": int,
        "win_rate": float,
        "sharpe_ratio": float,
        "total_pnl": float,
        "total_pnl_pct": float,
        "max_drawdown_pct": float,
        "created_at": str,
    }

    def iter_rows(
        self, *, db_path: Path, strategy_version: str | None = None, **_: Any
    ) -> Iterator[Mapping[str, Any]]:
        if not db_path.is_file():
            raise FileNotFoundError(f"trade_journal.db not found at {db_path}")
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            select_cols = ", ".join(_COLUMNS)
            sql = f"SELECT {select_cols} FROM backtest_results"
            params: tuple[Any, ...] = ()
            if strategy_version is not None:
                sql += " WHERE strategy_version = ?"
                params = (strategy_version,)
            sql += " ORDER BY id ASC"
            for row in conn.execute(sql, params):
                yield {col: row[col] for col in _COLUMNS}
        finally:
            conn.close()
