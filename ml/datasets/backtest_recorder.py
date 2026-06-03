"""Record backtest per-trade rows into `trade_journal.db::trades` (S-MLOPT-S7).

The backtest harnesses (the `sim/` engine, `scripts/backtest_*.py`) produce
per-trade results (`sim.ledger.SimTrade`: entry/sl/tp/exit/r_multiple). This
module persists those as **`is_backtest = 1`** rows in the canonical trades
table so the `trade_outcomes` / `setup_labels` families can surface them with
`include_backtest=True` (Phase 1.3) — manufacturing more labeled training data
than the ~80 real closed trades the decision models collapse against (G4).

**Safety / tiering.** `is_backtest = 1` rows are excluded by every live, stats,
and default dataset path (which all filter `is_backtest = 0`), so recorded
backtest trades can never enter money reporting — the same contract the M5
`backtest_results` writer relies on. Recording is **opt-in** (a caller passes a
`db_path`); nothing here runs against the production DB autonomously. The
mapper (`sim_trade_to_trade_row`) is a pure function; only `write_backtest_trades`
touches a DB, and it INSERTs `is_backtest = 1` rows only.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

# Columns we populate on an inserted backtest trade row. `id` autoincrements;
# everything else the live writer fills is left NULL/default.
_INSERT_COLUMNS = (
    "timestamp", "symbol", "direction", "entry_price", "exit_price",
    "stop_loss", "take_profit_1", "position_size", "setup_type", "killzone",
    "bias", "entry_reason", "exit_reason", "pnl", "pnl_percent", "status",
    "notes", "is_backtest", "strategy_name", "account_id", "is_demo",
    "created_at",
)

_LONG_ALIASES = frozenset({"long", "buy", "1", "+1"})


def sim_trade_to_trade_row(
    trade: Mapping[str, Any],
    *,
    run_tag: str,
    risk_pct: float = 1.0,
) -> dict[str, Any] | None:
    """Map one `SimTrade.to_dict()` to a `trades`-table row dict (`is_backtest=1`).

    Returns ``None`` for an unclosed / unlabeled trade (no `r_multiple`) — there
    is no outcome to learn from. The label columns are derived so the existing
    families work unchanged: ``pnl`` = realized R (proxy, so ``won = pnl > 0``),
    ``pnl_percent`` = ``r_multiple * risk_pct`` (so `setup_labels`'
    ``r_multiple = pnl_percent / risk_pct`` recovers the sim R). ``setup_type``
    falls back to the strategy name so `setup_labels` (which requires a non-empty
    `setup_type`) includes the row.
    """
    r_multiple = trade.get("r_multiple")
    if r_multiple is None or trade.get("exit_ts") is None:
        return None
    r = float(r_multiple)
    meta = trade.get("meta") or {}
    direction_raw = str(trade.get("direction", "")).lower()
    direction = "buy" if direction_raw in _LONG_ALIASES else "sell"
    strategy = str(trade.get("strategy", "") or "")
    setup_type = str(meta.get("setup_type") or strategy or "backtest")
    entry_ts = trade.get("entry_ts")
    exit_ts = trade.get("exit_ts")
    return {
        "timestamp": entry_ts,
        "symbol": trade.get("symbol"),
        "direction": direction,
        "entry_price": _as_float(trade.get("entry")),
        "exit_price": _as_float(trade.get("exit")),
        "stop_loss": _as_float(trade.get("sl")),
        "take_profit_1": _as_float(trade.get("tp")),
        "position_size": None,
        "setup_type": setup_type,
        "killzone": meta.get("killzone"),
        "bias": meta.get("bias"),
        "entry_reason": meta.get("entry_reason"),
        "exit_reason": trade.get("exit_reason"),
        "pnl": r,                       # realized R as a pnl proxy → won = R > 0
        "pnl_percent": r * float(risk_pct),
        "status": "closed",
        "notes": run_tag,
        "is_backtest": 1,
        "strategy_name": strategy,
        "account_id": "backtest",
        "is_demo": 0,
        "created_at": entry_ts,
        # carried through but unused by the families:
        "_exit_ts": exit_ts,
    }


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sql_default(col_type: str | None) -> Any:
    """A type-appropriate non-NULL default for a column we don't populate."""
    t = (col_type or "").upper()
    if any(tok in t for tok in ("INT", "REAL", "NUM", "FLOA", "DOUB")):
        return 0
    return ""


def write_backtest_trades(
    db_path: Path | str,
    sim_trades: Iterable[Mapping[str, Any]],
    *,
    run_tag: str,
    risk_pct: float = 1.0,
) -> int:
    """INSERT closed backtest trades as `is_backtest = 1` rows; returns the count.

    Skips open/unlabeled trades. Requires the `trades` table to already exist
    (it always does on the live VM / trainer copies; tests create it). Only ever
    writes `is_backtest = 1` rows.

    **Schema-adaptive:** the live `trades` table has `NOT NULL` constraints on
    columns the live writer always fills but a backtest row may not (e.g.
    `position_size`). We introspect `PRAGMA table_info` and (a) only insert
    columns that exist, (b) fill any NOT-NULL-without-default column we didn't
    map with a type-appropriate default — so the recorder works against the real
    schema without hardcoding its constraints.
    """
    rows = [
        r for r in (
            sim_trade_to_trade_row(t, run_tag=run_tag, risk_pct=risk_pct)
            for t in sim_trades
        )
        if r is not None
    ]
    if not rows:
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        # name -> (type, notnull, dflt_value, pk)
        info = {
            r[1]: (r[2], int(r[3]), r[4], int(r[5]))
            for r in conn.execute("PRAGMA table_info(trades)").fetchall()
        }
        # Columns to insert: our mapped fields that exist in the table …
        cols = [c for c in _INSERT_COLUMNS if c in info]
        # … plus any NOT-NULL column (no default, not the PK) we didn't map.
        for name, (_t, notnull, dflt, pk) in info.items():
            if notnull and dflt is None and not pk and name not in cols:
                cols.append(name)
        records = []
        for r in rows:
            rec = []
            for c in cols:
                col_type, notnull, _dflt, _pk = info[c]
                val = r.get(c)
                if val is None and notnull:
                    val = _sql_default(col_type)
                rec.append(val)
            records.append(rec)
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO trades ({', '.join(cols)}) VALUES ({placeholders})"
        conn.executemany(sql, records)
        conn.commit()
    finally:
        conn.close()
    return len(rows)
