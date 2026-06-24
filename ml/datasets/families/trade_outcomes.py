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

# S-MLOPT-S12 Part B: the as-of-signal account-state columns the optional
# LEFT JOIN to `account_context_snapshots` attaches. Mirrors the tested join
# in the `account_context` family. Snapshot DB column → dataset column.
_SNAPSHOT_TABLE = "account_context_snapshots"
_SNAPSHOT_DB_COLUMNS = (
    "equity",
    "daily_pnl_realized",
    "daily_equity_high",
    "daily_drawdown_pct",
    "open_trades_count",
)


def _snapshot_table_present(conn: sqlite3.Connection) -> bool:
    """True iff `account_context_snapshots` exists in this DB."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_SNAPSHOT_TABLE,),
    ).fetchone()
    return row is not None


def _coerce_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TradeOutcomesBuilder(DatasetBuilder):
    family: ClassVar[str] = "trade_outcomes"
    # v1 → v2 (S-MLOPT-S12 Part B): adds the five optional `*_at_signal`
    # snapshot columns. They serialize as None unless the build is run with
    # `include_snapshots=True` AND the `account_context_snapshots` table is
    # present, so a flag-off build is unchanged except for the all-None
    # columns the schema now always carries.
    builder_version: ClassVar[str] = "v2"
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
        # S-MLOPT-S12 Part B opt-in: as-of-signal account state attached by
        # the LEFT JOIN to `account_context_snapshots` when
        # `include_snapshots=True` AND the table exists. Nullable — None when
        # the join is off OR the row had no matching snapshot.
        "equity_at_signal": float,
        "daily_pnl_realized_at_signal": float,
        "daily_equity_high_at_signal": float,
        "daily_drawdown_pct_at_signal": float,
        "open_trades_count_at_signal": int,
    }

    def iter_rows(
        self,
        *,
        db_path: Path,
        strategy_name: str | None = None,
        symbol: str | None = None,
        include_backtest: bool = False,
        include_snapshots: bool = False,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if not db_path.is_file():
            raise FileNotFoundError(f"trade_journal.db not found at {db_path}")
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row

            # S-MLOPT-S12 Part B: attach the as-of-signal account-state
            # snapshot via LEFT JOIN, only when the caller opted in AND the
            # table exists (a test fixture / pre-instrumentation DB falls
            # through to the unchanged path with the five columns None). Same
            # join the `account_context` family uses: trades → order_packages
            # on op.linked_trade_id == t.id, snapshot on (order_package_id,
            # account_id). The snapshot is captured PRE-decision (before the
            # RiskManager runs), so it is leak-free for a `won` label derived
            # from the realised pnl.
            join_snapshots = include_snapshots and _snapshot_table_present(conn)

            trade_select = (
                ", ".join(f"t.{c}" for c in _RAW_COLUMNS) + ", t.is_backtest"
            )
            if join_snapshots:
                snap_select = ", ".join(
                    f"snap.{c}" for c in _SNAPSHOT_DB_COLUMNS
                )
                sql = (
                    f"SELECT {trade_select}, {snap_select} "
                    "FROM trades t "
                    "LEFT JOIN order_packages op "
                    "  ON op.linked_trade_id = t.id "
                    f"LEFT JOIN {_SNAPSHOT_TABLE} snap "
                    "  ON snap.order_package_id = op.order_package_id "
                    " AND snap.account_id = t.account_id "
                    "WHERE t.status = 'closed'"
                )
            else:
                sql = (
                    f"SELECT {trade_select} FROM trades t "
                    "WHERE t.status = 'closed'"
                )
            if not include_backtest:
                sql += " AND t.is_backtest = 0"
            params: list[Any] = []
            if strategy_name is not None:
                sql += " AND t.strategy_name = ?"
                params.append(strategy_name)
            if symbol is not None:
                sql += " AND t.symbol = ?"
                params.append(symbol)
            sql += " ORDER BY t.id ASC"
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
                # S-MLOPT-S12 Part B: attach the five snapshot columns. When
                # the join didn't run, every row still carries them as None so
                # the dataset schema stays complete (DatasetBuilder validates
                # column completeness).
                if join_snapshots:
                    payload["equity_at_signal"] = _coerce_float_or_none(
                        row["equity"]
                    )
                    payload["daily_pnl_realized_at_signal"] = (
                        _coerce_float_or_none(row["daily_pnl_realized"])
                    )
                    payload["daily_equity_high_at_signal"] = (
                        _coerce_float_or_none(row["daily_equity_high"])
                    )
                    payload["daily_drawdown_pct_at_signal"] = (
                        _coerce_float_or_none(row["daily_drawdown_pct"])
                    )
                    payload["open_trades_count_at_signal"] = (
                        _coerce_int_or_none(row["open_trades_count"])
                    )
                else:
                    payload["equity_at_signal"] = None
                    payload["daily_pnl_realized_at_signal"] = None
                    payload["daily_equity_high_at_signal"] = None
                    payload["daily_drawdown_pct_at_signal"] = None
                    payload["open_trades_count_at_signal"] = None
                yield payload
        finally:
            conn.close()
