"""`setup_labels_audit` dataset family (S-AI-WS5-C-FU).

V2 evolution of `setup_labels`. Operator-chosen scope (2026-05-10):
join recorded setups in `runtime_logs/signal_audit.jsonl` with the
matching CLOSED trade in `trade_journal.db` to surface richer
setup-time features (pattern, side, confidence, bars-back-of-setup)
that the bare trade-journal row does not carry. Label is the same
`r_multiple` continuous target as v1.

Join discipline: no stable signal_id exists across audit events and
trade rows. The composite key is
`(strategy_name, symbol, timestamp_within_window)` — audit
`logged_at_utc` versus trade `created_at` (fall back to `timestamp`)
within `match_window_seconds` (default 60s). Each audit event is
matched at most once; trades with no audit hit are dropped.

Survivorship: only setups that fired into a closed trade get a
label. Rejected audits (those carrying a non-empty
`stage_rejections` payload, or no `entry` / `price`) are dropped.
This is acknowledged in the sprint log; v1 (`setup_labels`) has the
same property by construction (it never sees the audit log).

Leakage: identical contract to v1 — `pnl`, `pnl_percent`, and
`r_multiple` are outcomes; the trainer must exclude them from
features when targeting `r_multiple`. `leakage_test_status` is
`skipped` (trainer's responsibility, same as `setup_labels` /
`trade_outcomes`).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

from ..builder import DatasetBuilder
from ..metadata import LeakageStatus

_AUDIT_EVENTS = frozenset({"turtle_soup_eval", "pipeline_result"})

_TRADE_RAW_COLUMNS = (
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


def _audit_is_setup_fired(event: Mapping[str, Any]) -> bool:
    """A setup `fired` if it has a tradeable side + entry/price + no rejections."""
    if event.get("event") not in _AUDIT_EVENTS:
        return False
    if event.get("stage_rejections"):
        return False
    has_entry = event.get("entry") is not None or event.get("price") is not None
    if not has_entry:
        return False
    if not event.get("strategy") or not event.get("symbol"):
        return False
    if not event.get("logged_at_utc"):
        return False
    return True


def _read_audit_events(audit_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with audit_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            if not _audit_is_setup_fired(evt):
                continue
            out.append(evt)
    return out


def _index_audit_by_strategy_symbol(
    events: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    bucket: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for evt in events:
        key = (str(evt["strategy"]), str(evt["symbol"]))
        bucket.setdefault(key, []).append(evt)
    for key in bucket:
        bucket[key].sort(
            key=lambda e: _parse_iso(e.get("logged_at_utc")) or datetime.min.replace(
                tzinfo=timezone.utc
            )
        )
    return bucket


def _match_audit(
    bucket: dict[tuple[str, str], list[dict[str, Any]]],
    *,
    strategy: str,
    symbol: str,
    trade_time: datetime,
    window_seconds: int,
    consumed: set[int],
) -> tuple[dict[str, Any] | None, float | None]:
    candidates = bucket.get((strategy, symbol), [])
    best: tuple[int, dict[str, Any], float] | None = None
    for idx, evt in enumerate(candidates):
        evt_id = id(evt)
        if evt_id in consumed:
            continue
        evt_time = _parse_iso(evt.get("logged_at_utc"))
        if evt_time is None:
            continue
        offset = (trade_time - evt_time).total_seconds()
        if abs(offset) > window_seconds:
            continue
        if best is None or abs(offset) < abs(best[2]):
            best = (idx, evt, offset)
    if best is None:
        return None, None
    consumed.add(id(best[1]))
    return best[1], best[2]


class SetupLabelsAuditBuilder(DatasetBuilder):
    family: ClassVar[str] = "setup_labels_audit"
    builder_version: ClassVar[str] = "v1"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.SKIPPED
    label_version: ClassVar[str] = "r-multiple-from-pnl-pct-v1"
    schema: ClassVar[Mapping[str, type]] = {
        "trade_id": int,
        "trade_timestamp": str,
        "trade_created_at": str,
        "symbol": str,
        "direction": str,
        "strategy_name": str,
        "setup_type": str,
        "killzone": str,
        "bias": str,
        "account_id": str,
        "pnl": float,
        "pnl_percent": float,
        "won": bool,
        "r_multiple": float,
        "audit_event": str,
        "audit_logged_at_utc": str,
        "audit_side": str,
        "audit_pattern": str,
        "audit_confidence": float,
        "audit_bars_back_of_setup": int,
        "match_offset_seconds": float,
    }

    def iter_rows(
        self,
        *,
        audit_log_path: Path,
        db_path: Path,
        risk_pct: float = 1.0,
        r_cap: float = 3.0,
        match_window_seconds: int = 60,
        strategy_name: str | None = None,
        symbol: str | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if risk_pct <= 0:
            raise ValueError(f"risk_pct must be > 0; got {risk_pct}")
        if r_cap <= 0:
            raise ValueError(f"r_cap must be > 0; got {r_cap}")
        if match_window_seconds <= 0:
            raise ValueError(
                f"match_window_seconds must be > 0; got {match_window_seconds}"
            )
        if not db_path.is_file():
            raise FileNotFoundError(f"trade_journal.db not found at {db_path}")
        if not audit_log_path.is_file():
            raise FileNotFoundError(
                f"signal_audit.jsonl not found at {audit_log_path}"
            )

        bucket = _index_audit_by_strategy_symbol(_read_audit_events(audit_log_path))
        consumed: set[int] = set()

        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            select_cols = ", ".join(_TRADE_RAW_COLUMNS)
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
                trade_strategy = row["strategy_name"] or ""
                trade_symbol = row["symbol"] or ""
                if not trade_strategy or not trade_symbol:
                    continue
                trade_time = _parse_iso(row["created_at"]) or _parse_iso(
                    row["timestamp"]
                )
                if trade_time is None:
                    continue
                evt, offset = _match_audit(
                    bucket,
                    strategy=trade_strategy,
                    symbol=trade_symbol,
                    trade_time=trade_time,
                    window_seconds=match_window_seconds,
                    consumed=consumed,
                )
                if evt is None or offset is None:
                    continue

                pnl = row["pnl"]
                pnl_percent_raw = row["pnl_percent"]
                if pnl is None:
                    continue
                pnl_percent = (
                    float(pnl_percent_raw) if pnl_percent_raw is not None else 0.0
                )
                r_multiple = _clip(pnl_percent / float(risk_pct), float(r_cap))

                payload: dict[str, Any] = {
                    "trade_id": int(row["id"]),
                    "trade_timestamp": row["timestamp"] or "",
                    "trade_created_at": row["created_at"] or "",
                    "symbol": trade_symbol,
                    "direction": row["direction"] or "",
                    "strategy_name": trade_strategy,
                    "setup_type": row["setup_type"] or "",
                    "killzone": row["killzone"] or "",
                    "bias": row["bias"] or "",
                    "account_id": row["account_id"] or "",
                    "pnl": float(pnl),
                    "pnl_percent": pnl_percent,
                    "won": bool(float(pnl) > 0),
                    "r_multiple": float(r_multiple),
                    "audit_event": str(evt.get("event") or ""),
                    "audit_logged_at_utc": str(evt.get("logged_at_utc") or ""),
                    "audit_side": str(evt.get("side") or ""),
                    "audit_pattern": str(evt.get("pattern") or ""),
                    "audit_confidence": float(evt.get("confidence") or 0.0),
                    "audit_bars_back_of_setup": int(
                        evt.get("bars_back_of_setup")
                        if evt.get("bars_back_of_setup") is not None
                        else -1
                    ),
                    "match_offset_seconds": float(offset),
                }
                yield payload
        finally:
            conn.close()
