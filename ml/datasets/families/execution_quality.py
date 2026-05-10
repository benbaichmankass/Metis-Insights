"""`execution_quality` dataset family (S-AI-WS5-D).

Source-of-truth: `trade_journal.db` joining `trades` (CLOSED,
non-backtest) with `order_packages` on
`order_packages.linked_trade_id = trades.id`. The trades table
records the actual fill (`entry_price`, `timestamp`); the
order_packages table records the intent (`entry`, `created_at`).
The delta between the two is the execution-quality surface.

Label: `entry_slippage_bps` (continuous, signed). Computed as
`((actual_entry - intended_entry) / intended_entry) * 10_000`
with the sign convention that **positive = worse for the trader**:

- LONG: paid MORE than intended → positive slippage (bad)
- SHORT: sold for LESS than intended → positive slippage (bad)
- Negative values mean the fill was BETTER than the intent.

Capped at `±slippage_cap_bps` (default 200 bps, i.e. 2 %) to
protect against rare partial fills or misrecorded intents.

Bookkeeping (not for training): `fill_latency_seconds` =
`trade.timestamp - order_package.created_at`. Operator-readable
diagnostic; trainers may use it as a feature but the baseline
manifest does not.

Leakage discipline: `entry_slippage_bps` and `fill_latency_seconds`
are both outcomes of the execution path. A trainer targeting
`entry_slippage_bps` MUST exclude `fill_latency_seconds` and
`actual_entry` from `feature_column`. `intended_entry` is set at
signal time and is fair game. `leakage_test_status: skipped` —
trainer's responsibility (same rule as `setup_labels`,
`trade_outcomes`).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from ..builder import DatasetBuilder
from ..metadata import LeakageStatus

_DEFAULT_SLIPPAGE_CAP_BPS = 200.0

_NULLABLE_TEXT = {
    "trade_timestamp",
    "trade_created_at",
    "order_created_at",
    "symbol",
    "direction",
    "strategy_name",
    "setup_type",
    "killzone",
    "bias",
    "account_id",
    "signal_logic",
}


def _clip(value: float, cap: float) -> float:
    if value > cap:
        return cap
    if value < -cap:
        return -cap
    return value


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    raw = ts.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _signed_slippage_bps(direction: str, intended: float, actual: float) -> float:
    """Positive = trader paid worse than intended."""
    if intended <= 0:
        return 0.0
    raw_pct = (actual - intended) / intended
    if direction.upper() in {"SHORT", "SELL"}:
        raw_pct = -raw_pct
    return raw_pct * 10_000.0


class ExecutionQualityBuilder(DatasetBuilder):
    family: ClassVar[str] = "execution_quality"
    builder_version: ClassVar[str] = "v1"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.SKIPPED
    label_version: ClassVar[str] = "entry-slippage-bps-v1"
    schema: ClassVar[Mapping[str, type]] = {
        "trade_id": int,
        "order_package_id": str,
        "trade_timestamp": str,
        "trade_created_at": str,
        "order_created_at": str,
        "symbol": str,
        "direction": str,
        "strategy_name": str,
        "setup_type": str,
        "killzone": str,
        "bias": str,
        "account_id": str,
        "signal_logic": str,
        "confidence": float,
        "intended_entry": float,
        "actual_entry": float,
        "entry_slippage_bps": float,
        "fill_latency_seconds": float,
    }

    def iter_rows(
        self,
        *,
        db_path: Path,
        slippage_cap_bps: float = _DEFAULT_SLIPPAGE_CAP_BPS,
        strategy_name: str | None = None,
        symbol: str | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if slippage_cap_bps <= 0:
            raise ValueError(
                f"slippage_cap_bps must be > 0; got {slippage_cap_bps}"
            )
        if not db_path.is_file():
            raise FileNotFoundError(f"trade_journal.db not found at {db_path}")

        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            sql = (
                "SELECT "
                " t.id              AS trade_id, "
                " op.order_package_id AS order_package_id, "
                " t.timestamp       AS trade_timestamp, "
                " t.created_at      AS trade_created_at, "
                " op.created_at     AS order_created_at, "
                " t.symbol          AS symbol, "
                " t.direction       AS direction, "
                " t.strategy_name   AS strategy_name, "
                " t.setup_type      AS setup_type, "
                " t.killzone        AS killzone, "
                " t.bias            AS bias, "
                " t.account_id      AS account_id, "
                " op.signal_logic   AS signal_logic, "
                " op.confidence     AS confidence, "
                " op.entry          AS intended_entry, "
                " t.entry_price     AS actual_entry "
                "FROM trades t "
                "INNER JOIN order_packages op "
                " ON op.linked_trade_id = t.id "
                "WHERE t.status = 'CLOSED' AND t.is_backtest = 0 "
                " AND t.entry_price IS NOT NULL "
                " AND op.entry IS NOT NULL "
                " AND op.entry > 0"
            )
            params: list[Any] = []
            if strategy_name is not None:
                sql += " AND t.strategy_name = ?"
                params.append(strategy_name)
            if symbol is not None:
                sql += " AND t.symbol = ?"
                params.append(symbol)
            sql += " ORDER BY t.id ASC"

            for row in conn.execute(sql, params):
                intended = row["intended_entry"]
                actual = row["actual_entry"]
                if intended is None or actual is None:
                    continue
                intended = float(intended)
                actual = float(actual)
                if intended <= 0:
                    continue
                direction = (row["direction"] or "").strip()
                slippage_bps = _clip(
                    _signed_slippage_bps(direction, intended, actual),
                    float(slippage_cap_bps),
                )

                trade_time = _parse_iso(row["trade_timestamp"]) or _parse_iso(
                    row["trade_created_at"]
                )
                order_time = _parse_iso(row["order_created_at"])
                if trade_time is not None and order_time is not None:
                    fill_latency = (trade_time - order_time).total_seconds()
                else:
                    fill_latency = 0.0

                payload: dict[str, Any] = {
                    "trade_id": int(row["trade_id"]),
                    "order_package_id": str(row["order_package_id"] or ""),
                    "trade_timestamp": row["trade_timestamp"] or "",
                    "trade_created_at": row["trade_created_at"] or "",
                    "order_created_at": row["order_created_at"] or "",
                    "symbol": row["symbol"] or "",
                    "direction": direction,
                    "strategy_name": row["strategy_name"] or "",
                    "setup_type": row["setup_type"] or "",
                    "killzone": row["killzone"] or "",
                    "bias": row["bias"] or "",
                    "account_id": row["account_id"] or "",
                    "signal_logic": row["signal_logic"] or "",
                    "confidence": float(
                        row["confidence"] if row["confidence"] is not None else 0.0
                    ),
                    "intended_entry": intended,
                    "actual_entry": actual,
                    "entry_slippage_bps": float(slippage_bps),
                    "fill_latency_seconds": float(fill_latency),
                }
                yield payload
        finally:
            conn.close()
