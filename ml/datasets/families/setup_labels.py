"""`setup_labels` dataset family (S-AI-WS5-C).

Source-of-truth: `trade_journal.db::trades` (CLOSED, non-backtest,
non-null pnl, **non-empty `setup_type`**). Operator-chosen scope
(2026-05-10): the smallest viable dataset for a setup-quality
baseline — every closed trade that had a setup tag.

Differences vs `trade_outcomes`:

- Filters to rows with a non-empty `setup_type` only (operator-
  tagged setups). The `setup_quality` baseline must score setups
  the strategy actually identified, not arbitrary closes.
- Adds a derived `r_multiple` label = `pnl_percent / risk_pct`
  capped at `±r_cap` (defaults: `risk_pct=1.0`, `r_cap=3.0`).
  Caps protect against outliers (a 30% win on a 1% risked trade
  is presumably a sign measurement error or a position that
  rode through a regime change, not a typical win).

Leakage discipline (S-AI-WS5-C): `pnl`, `pnl_percent`, and
`r_multiple` are all outcomes. A trainer targeting `r_multiple`
MUST exclude the other two from `feature_column`. The dataset
records `leakage_test_status: skipped` because leakage prevention
is the trainer's responsibility (same rule as `trade_outcomes`).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

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


def _clip(value: float, cap: float) -> float:
    if value > cap:
        return cap
    if value < -cap:
        return -cap
    return value


def _parse_ts_hour_dow(ts_str: str) -> tuple[int, int]:
    """Parse signal-time ts → ``(hour_of_day, dayofweek)``.

    Tolerant of ``Z`` suffix + explicit offsets. Returns ``(0, 0)`` on
    parse failure so a single malformed row doesn't drop out — the row
    still emits and the trainer's leakage gate stays clean (these are
    signal-time features, not outcomes).
    """
    if not ts_str:
        return 0, 0
    s = str(ts_str).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0, 0
    return dt.hour, dt.weekday()


class SetupLabelsBuilder(DatasetBuilder):
    family: ClassVar[str] = "setup_labels"
    # v2: adds hour_of_day + dayofweek derived from `created_at` (signal
    # time, non-leaking). Phase-2 feature expansion for the v2 LightGBM
    # setup-quality regressor.
    builder_version: ClassVar[str] = "v2"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.SKIPPED
    label_version: ClassVar[str] = "r-multiple-from-pnl-pct-v1"
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
        "hour_of_day": int,
        "dayofweek": int,
        "won": bool,
        "r_multiple": float,
    }

    def iter_rows(
        self,
        *,
        db_path: Path,
        risk_pct: float = 1.0,
        r_cap: float = 3.0,
        strategy_name: str | None = None,
        symbol: str | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if risk_pct <= 0:
            raise ValueError(f"risk_pct must be > 0; got {risk_pct}")
        if r_cap <= 0:
            raise ValueError(f"r_cap must be > 0; got {r_cap}")
        if not db_path.is_file():
            raise FileNotFoundError(f"trade_journal.db not found at {db_path}")

        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            select_cols = ", ".join(_RAW_COLUMNS)
            sql = (
                f"SELECT {select_cols} FROM trades "
                "WHERE status = 'closed' AND is_backtest = 0 "
                "AND pnl IS NOT NULL "
                "AND setup_type IS NOT NULL AND TRIM(setup_type) <> ''"
            )
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
                pnl_percent_raw = row["pnl_percent"]
                if pnl is None:
                    continue
                pnl_percent = (
                    float(pnl_percent_raw) if pnl_percent_raw is not None else 0.0
                )
                r_multiple = _clip(pnl_percent / float(risk_pct), float(r_cap))

                payload: dict[str, Any] = {}
                for col in _RAW_COLUMNS:
                    value = row[col]
                    if col in _NULLABLE_TEXT and value is None:
                        payload[col] = ""
                    elif col == "pnl" or col == "pnl_percent":
                        payload[col] = float(value) if value is not None else 0.0
                    elif col == "id":
                        payload[col] = int(value)
                    else:
                        payload[col] = value
                payload["won"] = bool(payload["pnl"] > 0)
                payload["r_multiple"] = float(r_multiple)
                # Derive signal-time features from `created_at` (when the
                # trade was opened — non-leaking). Falls back to `timestamp`
                # which historically held the same value pre S-AI-WS5-A.
                ts_for_time = payload.get("created_at") or payload.get("timestamp") or ""
                hour, dow = _parse_ts_hour_dow(ts_for_time)
                payload["hour_of_day"] = int(hour)
                payload["dayofweek"] = int(dow)
                yield payload
        finally:
            conn.close()
