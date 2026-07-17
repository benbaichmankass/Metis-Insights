#!/usr/bin/env python3
"""Net-R re-grade scorecard — M24 Phase 2 (Tier-1, offline, observability-only).

Design of record: ``docs/research/M24-net-r-cost-aware-DESIGN.md`` (P2 —
"Net-R re-grade of strategies/exits"). Builds directly on M24 P1
(``src.runtime.net_r_label`` — ``net_r_for_trade`` / ``risk_usd_for_trade`` /
``net_r_coverage``): recompute the per-strategy / per-(strategy, symbol)
aggregates on **true net-of-cost R** instead of estimate-R, and answer the P2
question — *which strategies/legs are net-positive after REAL costs, and does
any cell flip sign vs the gross view* (the thin-edge crypto perps where funding
is non-trivial are the ones to watch).

The **sign-flip flag** is the headline: a cell flips when its total net-R sign
differs from its total gross-R sign — i.e. real fees + funding turn a
gross-winner into a net-loser (or, rarely, the reverse). Each such cell is
flagged for a Tier-3 review; this script does **not** itself change any config.

Read-only + honest:
  * Opens ``trade_journal.db`` strictly READ-ONLY (``mode=ro`` URI) — never a
    write, never CREATE. Never touches a live-path file.
  * Uncosted / risk-uncomputable rows are reported in the coverage buckets
    (from P1's ``net_r_coverage``), never silently folded into a 0.
  * ``gross_R`` uses the SAME risk denominator as ``net_R`` (P1's
    ``risk_usd_for_trade``), so gross-vs-net sign comparison is apples-to-apples.

stdlib + the repo's own modules only (no pandas / numpy / fastapi).

Usage:
  python scripts/research/net_r_regrade.py                 # scorecard to stdout
  python scripts/research/net_r_regrade.py --since 2026-06-01T00:00:00Z
  python scripts/research/net_r_regrade.py --out artifacts/  # + JSON artifact
  python scripts/research/net_r_regrade.py --db /path/to/fixture.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.runtime.net_r_label import (  # noqa: E402
    net_r_coverage,
    net_r_for_trade,
    risk_usd_for_trade,
)

# The columns P1's label functions consult. We SELECT a defensive subset of
# whatever the live schema actually has (a fixture DB may omit the newer cost
# columns), aliasing strategy_name → strategy and enriching contract_value from
# config/instruments.yaml (no such column on trades).
_DESIRED_COLUMNS = (
    "id",
    "strategy_name",
    "symbol",
    "direction",
    "entry_price",
    "stop_loss",
    "position_size",
    "pnl",
    "gross_pnl",
    "fee_taker_usd",
    "fee_maker_usd",
    "funding_paid_usd",
    "cost_source",
    "contract_value_usd",
    "contract_value",
    "closed_at",
    "timestamp",
)


# ---------------------------------------------------------------------------
# Pure core — importable by tests WITHOUT a DB.
# ---------------------------------------------------------------------------
def _sign(x: float) -> int:
    """-1 / 0 / +1 sign of a float (0.0 → 0)."""
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _empty_agg() -> dict:
    return {
        "n": 0,
        "n_r": 0,  # rows with a computable R (net + gross)
        "sum_net_R": 0.0,
        "sum_gross_R": 0.0,
        "n_broker": 0,
        "n_estimate": 0,
        "sum_net_R_broker": 0.0,
        "sum_net_R_estimate": 0.0,
    }


def _finalize_agg(agg: dict, coverage_cell: dict) -> dict:
    """Turn running sums into means + the sign-flip flag + coverage buckets."""
    n_r = agg["n_r"]
    sum_net = agg["sum_net_R"]
    sum_gross = agg["sum_gross_R"]
    mean_net = round(sum_net / n_r, 8) if n_r else None
    mean_gross = round(sum_gross / n_r, 8) if n_r else None
    mean_net_broker = (
        round(agg["sum_net_R_broker"] / agg["n_broker"], 8) if agg["n_broker"] else None
    )
    mean_net_estimate = (
        round(agg["sum_net_R_estimate"] / agg["n_estimate"], 8)
        if agg["n_estimate"]
        else None
    )
    # Sign-flip: net total sign differs from gross total sign, and neither is 0.
    # Only meaningful when at least one R-measured trade exists.
    net_sign = _sign(sum_net)
    gross_sign = _sign(sum_gross)
    sign_flip = bool(n_r and net_sign != 0 and gross_sign != 0 and net_sign != gross_sign)
    return {
        "n": agg["n"],
        "n_r_measured": n_r,
        "sum_net_R": round(sum_net, 8),
        "mean_net_R": mean_net,
        "sum_gross_R": round(sum_gross, 8),
        "mean_gross_R": mean_gross,
        "cost_drag_R": round(sum_gross - sum_net, 8) if n_r else None,
        "mean_net_R_broker": mean_net_broker,
        "mean_net_R_estimate": mean_net_estimate,
        "sign_flip": sign_flip,
        "coverage": {
            "broker_costed": coverage_cell.get("broker_costed", 0),
            "estimate_costed": coverage_cell.get("estimate_costed", 0),
            "uncosted": coverage_cell.get("uncosted", 0),
            "r_uncomputable": coverage_cell.get("r_uncomputable", 0),
        },
    }


def _accumulate(agg: dict, trade, label) -> None:
    """Fold one trade's net/gross R into a running aggregate dict."""
    agg["n"] += 1
    if label is None:
        return  # R-uncomputable — counted in P1 coverage, not in the R sums.
    risk = label["risk_usd"]
    if not risk:
        return
    net_r = label["net_R"]
    # gross_R shares the exact risk denominator so the sign comparison is fair.
    gross = trade.get("gross_pnl")
    if gross is None:
        gross = trade.get("pnl")
    try:
        gross_r = float(gross) / risk
    except (TypeError, ValueError):
        return
    agg["n_r"] += 1
    agg["sum_net_R"] += net_r
    agg["sum_gross_R"] += gross_r
    if label["cost_source"] == "broker":
        agg["n_broker"] += 1
        agg["sum_net_R_broker"] += net_r
    elif label["cost_source"] == "estimate":
        agg["n_estimate"] += 1
        agg["sum_net_R_estimate"] += net_r


def regrade(trades: list) -> dict:
    """Re-grade scorecard over a list of closed-trade dict rows (pure, no DB).

    Each ``trade`` is a mapping in the shape P1's ``net_r_for_trade`` consumes
    (``strategy`` / ``symbol`` / ``entry_price`` / ``stop_loss`` /
    ``position_size`` / ``gross_pnl`` or ``pnl`` / the cost columns /
    ``cost_source`` / optional ``contract_value_usd``).

    Returns a JSON-serializable dict:

    ```
    {
      "trade_count": int,
      "coverage": {...},                       # P1 net_r_coverage overall + by_cell
      "by_strategy": [{"strategy", ...agg}],   # sorted, sign_flip flagged
      "by_cell":     [{"strategy","symbol", ...agg}],
      "sign_flips":  [{"scope","strategy","symbol",...}],  # cells/strategies that flip
    }
    ```
    """
    coverage = net_r_coverage(trades)
    # Index coverage cells by (strategy, symbol) for the per-cell bucket counts.
    cell_cov = {(c["strategy"], c["symbol"]): c for c in coverage.get("by_cell", [])}

    from src.runtime.net_r_label import normalize_symbol  # symbol folding, P1's

    strat_agg: dict = {}
    cell_agg: dict = {}
    # Roll per-strategy coverage up from the per-cell coverage buckets.
    strat_cov: dict = {}
    for (strat, sym), cov in cell_cov.items():
        s = strat_cov.setdefault(
            strat,
            {"broker_costed": 0, "estimate_costed": 0, "uncosted": 0, "r_uncomputable": 0},
        )
        for k in s:
            s[k] += cov.get(k, 0)

    for trade in trades:
        strat = trade.get("strategy")
        strat_key = str(strat) if strat is not None else ""
        sym_key = normalize_symbol(trade.get("symbol"))
        label = net_r_for_trade(trade)
        _accumulate(strat_agg.setdefault(strat_key, _empty_agg()), trade, label)
        _accumulate(cell_agg.setdefault((strat_key, sym_key), _empty_agg()), trade, label)

    by_strategy = []
    sign_flips = []
    for strat in sorted(strat_agg):
        fin = _finalize_agg(
            strat_agg[strat],
            strat_cov.get(strat, {}),
        )
        row = {"strategy": strat, **fin}
        by_strategy.append(row)
        if fin["sign_flip"]:
            sign_flips.append({"scope": "strategy", "strategy": strat, **fin})

    by_cell = []
    for (strat, sym) in sorted(cell_agg):
        fin = _finalize_agg(cell_agg[(strat, sym)], cell_cov.get((strat, sym), {}))
        row = {"strategy": strat, "symbol": sym, **fin}
        by_cell.append(row)
        if fin["sign_flip"]:
            sign_flips.append({"scope": "cell", "strategy": strat, "symbol": sym, **fin})

    return {
        "trade_count": len(trades),
        "coverage": coverage,
        "by_strategy": by_strategy,
        "by_cell": by_cell,
        "sign_flips": sign_flips,
    }


# ---------------------------------------------------------------------------
# Markdown rendering.
# ---------------------------------------------------------------------------
def _fmt(x) -> str:
    return "—" if x is None else f"{x:+.3f}"


def render_markdown(report: dict, *, generated_at: str, db_path: str, since) -> str:
    cov = report["coverage"]
    lines = []
    lines.append("# M24 P2 — Net-R re-grade scorecard")
    lines.append("")
    lines.append(f"- generated_at: `{generated_at}`")
    lines.append(f"- db: `{db_path}`")
    if since:
        lines.append(f"- since: `{since}`")
    lines.append(f"- closed trades scanned: **{report['trade_count']}**")
    lines.append(
        "- coverage: "
        f"broker={cov.get('broker_costed', 0)} · "
        f"estimate={cov.get('estimate_costed', 0)} · "
        f"uncosted={cov.get('uncosted', 0)} · "
        f"r_uncomputable={cov.get('r_uncomputable', 0)}"
    )
    flips = report["sign_flips"]
    if flips:
        lines.append(
            f"- **⚠️ {len(flips)} sign-flip(s)** — cells net-negative after real "
            "costs (Tier-3 review):"
        )
        for f in flips:
            where = f["strategy"] if f["scope"] == "strategy" else (
                f"{f['strategy']} / {f['symbol']}"
            )
            lines.append(
                f"  - {f['scope']}: `{where}` — gross ΣR {_fmt(f['sum_gross_R'])} "
                f"→ net ΣR {_fmt(f['sum_net_R'])} (n_R={f['n_r_measured']})"
            )
    else:
        lines.append("- sign-flips: none")
    lines.append("")
    lines.append("## Per-strategy")
    lines.append("")
    lines.append("| strategy | n | n_R | Σgross_R | Σnet_R | drag_R | flip |")
    lines.append("|---|--:|--:|--:|--:|--:|:--:|")
    for r in report["by_strategy"]:
        lines.append(
            f"| {r['strategy'] or '∅'} | {r['n']} | {r['n_r_measured']} | "
            f"{_fmt(r['sum_gross_R'])} | {_fmt(r['sum_net_R'])} | "
            f"{_fmt(r['cost_drag_R'])} | {'🚩' if r['sign_flip'] else ''} |"
        )
    lines.append("")
    lines.append("## Per-(strategy, symbol) cell")
    lines.append("")
    lines.append("| strategy | symbol | n | n_R | Σgross_R | Σnet_R | drag_R | flip |")
    lines.append("|---|---|--:|--:|--:|--:|--:|:--:|")
    for r in report["by_cell"]:
        lines.append(
            f"| {r['strategy'] or '∅'} | {r['symbol'] or '∅'} | {r['n']} | "
            f"{r['n_r_measured']} | {_fmt(r['sum_gross_R'])} | {_fmt(r['sum_net_R'])} "
            f"| {_fmt(r['cost_drag_R'])} | {'🚩' if r['sign_flip'] else ''} |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB reader — thin, read-only.
# ---------------------------------------------------------------------------
def _existing_columns(conn: sqlite3.Connection) -> set:
    cols = set()
    for row in conn.execute("PRAGMA table_info(trades)"):
        cols.add(row[1])
    return cols


def load_trades(db_path: str, since: str | None = None) -> list:
    """Load resolved closed non-backtest trades READ-ONLY, shaped for ``regrade``.

    Aliases ``strategy_name`` → ``strategy`` and enriches ``contract_value_usd``
    from ``config/instruments.yaml`` when the row carries no contract value, so
    futures risk (MES/MGC/MHG) normalises correctly. Degrades gracefully if the
    schema lacks a column (a fixture DB) by SELECTing only what exists.
    """
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        have = _existing_columns(conn)
        select_cols = [c for c in _DESIRED_COLUMNS if c in have]
        if "id" not in select_cols and "id" in have:
            select_cols.insert(0, "id")
        col_sql = ", ".join(select_cols) if select_cols else "*"
        where = "status='closed' AND COALESCE(is_backtest,0)=0"
        params: list = []
        if since:
            # closed_at is epoch-ms-or-ISO; use the shared normaliser so a
            # reconciler-filled epoch-ms close is not dropped by datetime().
            try:
                from src.utils.closed_at import close_time_sql

                time_expr = close_time_sql("closed_at", "timestamp")
            except Exception:
                time_expr = "COALESCE(closed_at, timestamp)"
            where += f" AND {time_expr} >= ?"
            params.append(since)
        sql = f"SELECT {col_sql} FROM trades WHERE {where}"
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()

    try:
        from src.runtime.local_pnl import contract_value_usd_for
    except Exception:  # pragma: no cover - best-effort enrichment
        contract_value_usd_for = None

    out = []
    for r in rows:
        # Alias strategy_name → strategy (the name P1's functions read).
        if "strategy" not in r and "strategy_name" in r:
            r["strategy"] = r.get("strategy_name")
        # Enrich contract value if the row carries none (no such trades column).
        if (
            contract_value_usd_for is not None
            and not r.get("contract_value_usd")
            and not r.get("contract_value")
        ):
            try:
                r["contract_value_usd"] = contract_value_usd_for(r.get("symbol"))
            except Exception:  # pragma: no cover
                pass
        out.append(r)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="M24 P2 net-R re-grade scorecard (read-only, observability-only)."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Override trade_journal.db path (default: canonical resolver). "
        "Opened strictly read-only.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO-8601 lower bound on close time (e.g. 2026-06-01T00:00:00Z).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Directory to write the JSON + markdown artifacts into (optional).",
    )
    args = parser.parse_args(argv)

    if args.db:
        db_path = args.db
    else:
        from src.utils.paths import trade_journal_db_path

        db_path = trade_journal_db_path()

    if not Path(db_path).exists():
        print(f"error: trade_journal.db not found at {db_path}", file=sys.stderr)
        return 2

    trades = load_trades(db_path, since=args.since)
    report = regrade(trades)
    generated_at = datetime.now(timezone.utc).isoformat()
    report["generated_at"] = generated_at
    report["db_path"] = str(db_path)
    report["since"] = args.since

    md = render_markdown(
        report, generated_at=generated_at, db_path=str(db_path), since=args.since
    )
    print(md)

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path = out_dir / f"net_r_regrade_{stamp}.json"
        md_path = out_dir / f"net_r_regrade_{stamp}.md"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True))
        md_path.write_text(md)
        print(f"\n[wrote] {json_path}", file=sys.stderr)
        print(f"[wrote] {md_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
