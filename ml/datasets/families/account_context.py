"""`account_context` dataset family (S-AI-WS5-F).

Source-of-truth: `trade_journal.db::trades` joined with
`config/accounts.yaml`. Filters to non-backtest trades whose
`account_id` matches a prop-style account in the YAML
(distinguished by `type: prop`). For each row, the family attaches
the account's mission rules and emits the binary action label
`was_taken` derived from `trades.status`.

Label: `was_taken` (bool). Derived from `trades.status`:

- `was_taken = True`  → status ∈ {open, closed, CLOSED}
- `was_taken = False` → status ∈ {rejected, exchange_rejected, REJECTED}

The "rejected" rows are the labelled negatives — signals the
strategy generated but the prop-mission gate (or the exchange)
refused to take. They carry a `skip_reason` parsed from
`entry_reason` (e.g. `SKIP_MISSION_MET`, `DAILY_LOSS_CAP`,
`INTRADAY_DRAWDOWN`).

Mission rules (replicated per row, sourced from YAML):

- `account_state` (evaluation / funded)
- `target_profit_pct`, `min_active_days`, `min_daily_profit_pct`
- `max_dd_pct`, `daily_usd_cap`, `pos_size_cap`, `risk_pct_setting`
- `overnight_restricted`

The mission rules are **as-of build time**, not as-of trade time.
Per-trade equity / drawdown snapshots are not currently recorded
in `trades`; an instrumentation follow-up could backfill them.
For the baseline, the static rules + the parsed `skip_reason` are
enough to learn per-setup acceptance rates.

Leakage discipline: `pnl`, `pnl_percent`, and `position_size`
are post-decision outcomes; trainers targeting `was_taken` MUST
exclude them. `skip_reason` is the action's outcome — also a
leak. Only signal-time / mission-rule columns are fair game as
features. `leakage_test_status: skipped` (trainer's
responsibility, same as the other label families).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping

import yaml

from ..builder import DatasetBuilder
from ..metadata import LeakageStatus

_TAKEN_STATUSES = frozenset({"open", "closed"})
_REJECTED_STATUSES = frozenset({"rejected", "exchange_rejected"})

_TRADE_COLUMNS = (
    "id",
    "timestamp",
    "created_at",
    "symbol",
    "direction",
    "strategy_name",
    "setup_type",
    "killzone",
    "bias",
    "account_id",
    "status",
    "entry_reason",
    "pnl",
    "pnl_percent",
    "position_size",
)


def _coerce_str(value: Any) -> str:
    return "" if value is None else str(value)


def _coerce_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    try:
        return bool(value)
    except (TypeError, ValueError):
        return False


def _normalise_status(value: Any) -> str:
    return _coerce_str(value).strip().lower()


def _parse_skip_reason(entry_reason: str) -> str:
    """Pull the skip-reason code out of a `REJECTED:` entry_reason.

    Format: ``REJECTED: <CODE> | <base reason>``. If `entry_reason`
    is not a rejection or doesn't carry a code, returns "".
    """
    text = _coerce_str(entry_reason).strip()
    if not text.upper().startswith("REJECTED:"):
        return ""
    after = text.split(":", 1)[1].strip()
    if not after:
        return ""
    return after.split("|", 1)[0].strip()


def _load_prop_accounts(
    accounts_yaml_path: Path,
) -> dict[str, dict[str, Any]]:
    """Return {account_id: account_block} for every prop-typed account.

    A prop-typed account is one whose YAML block carries `type:
    prop`. Live (`type: live`) and dry/test accounts are excluded.
    """
    if not accounts_yaml_path.is_file():
        raise FileNotFoundError(
            f"accounts.yaml not found at {accounts_yaml_path}"
        )
    raw = yaml.safe_load(accounts_yaml_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    accounts = raw.get("accounts")
    if not isinstance(accounts, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for account_id, block in accounts.items():
        if not isinstance(block, dict):
            continue
        if _coerce_str(block.get("type")).strip().lower() != "prop":
            continue
        out[str(account_id)] = block
    return out


def _mission_view(block: Mapping[str, Any]) -> dict[str, Any]:
    """Project an accounts.yaml block into the per-row mission columns."""
    phase = block.get("phase_requirements") or {}
    risk = block.get("risk") or {}
    if not isinstance(phase, dict):
        phase = {}
    if not isinstance(risk, dict):
        risk = {}
    return {
        "account_state": _coerce_str(block.get("account_state")),
        "target_profit_pct": _coerce_float(phase.get("target_profit_pct")),
        "min_active_days": _coerce_float(phase.get("min_active_days")),
        "min_daily_profit_pct": _coerce_float(
            phase.get("min_daily_profit_pct")
        ),
        "max_dd_pct": _coerce_float(risk.get("max_dd_pct")),
        "daily_usd_cap": _coerce_float(risk.get("daily_usd")),
        "pos_size_cap": _coerce_float(risk.get("pos_size")),
        "risk_pct_setting": _coerce_float(risk.get("risk_pct")),
        "overnight_restricted": _coerce_bool(block.get("overnight_restricted")),
    }


class AccountContextBuilder(DatasetBuilder):
    family: ClassVar[str] = "account_context"
    builder_version: ClassVar[str] = "v1"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.SKIPPED
    label_version: ClassVar[str] = "was-taken-from-status-v1"
    schema: ClassVar[Mapping[str, type]] = {
        "trade_id": int,
        "timestamp": str,
        "created_at": str,
        "symbol": str,
        "direction": str,
        "strategy_name": str,
        "setup_type": str,
        "killzone": str,
        "bias": str,
        "account_id": str,
        "status": str,
        "entry_reason": str,
        "skip_reason": str,
        "was_taken": bool,
        "pnl": float,
        "pnl_percent": float,
        "position_size": float,
        "account_state": str,
        "target_profit_pct": float,
        "min_active_days": float,
        "min_daily_profit_pct": float,
        "max_dd_pct": float,
        "daily_usd_cap": float,
        "pos_size_cap": float,
        "risk_pct_setting": float,
        "overnight_restricted": bool,
    }

    def iter_rows(
        self,
        *,
        db_path: Path,
        accounts_yaml_path: Path,
        account_id: str | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        if not db_path.is_file():
            raise FileNotFoundError(
                f"trade_journal.db not found at {db_path}"
            )
        prop_accounts = _load_prop_accounts(accounts_yaml_path)
        if account_id is not None:
            prop_accounts = {
                k: v for k, v in prop_accounts.items() if k == account_id
            }
        if not prop_accounts:
            return

        mission_views = {
            aid: _mission_view(block) for aid, block in prop_accounts.items()
        }

        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            select_cols = ", ".join(_TRADE_COLUMNS)
            placeholders = ", ".join("?" for _ in prop_accounts)
            sql = (
                f"SELECT {select_cols} FROM trades "
                "WHERE is_backtest = 0 "
                f" AND account_id IN ({placeholders}) "
                " ORDER BY id ASC"
            )
            for row in conn.execute(sql, list(prop_accounts.keys())):
                aid = _coerce_str(row["account_id"])
                mission = mission_views.get(aid)
                if mission is None:
                    continue
                status = _normalise_status(row["status"])
                if status in _TAKEN_STATUSES:
                    was_taken = True
                elif status in _REJECTED_STATUSES:
                    was_taken = False
                else:
                    continue
                entry_reason = _coerce_str(row["entry_reason"])
                skip_reason = (
                    _parse_skip_reason(entry_reason)
                    if not was_taken
                    else ""
                )

                payload: dict[str, Any] = {
                    "trade_id": int(row["id"]),
                    "timestamp": _coerce_str(row["timestamp"]),
                    "created_at": _coerce_str(row["created_at"]),
                    "symbol": _coerce_str(row["symbol"]),
                    "direction": _coerce_str(row["direction"]),
                    "strategy_name": _coerce_str(row["strategy_name"]),
                    "setup_type": _coerce_str(row["setup_type"]),
                    "killzone": _coerce_str(row["killzone"]),
                    "bias": _coerce_str(row["bias"]),
                    "account_id": aid,
                    "status": _coerce_str(row["status"]),
                    "entry_reason": entry_reason,
                    "skip_reason": skip_reason,
                    "was_taken": was_taken,
                    "pnl": _coerce_float(row["pnl"]),
                    "pnl_percent": _coerce_float(row["pnl_percent"]),
                    "position_size": _coerce_float(row["position_size"]),
                }
                payload.update(mission)
                yield payload
        finally:
            conn.close()
