#!/usr/bin/env python3
"""M30 · P5 — the EXIT-timing research panel builder (MFE/MAE excursions).

The exit-timing study (the operator's load-bearing prior — "edge lives in
exit/regime") asks, per closed trade: how much realized-R did the trade *give
back* from its peak, and does that giveback / capture-ratio depend on
decision-time features? That needs the intrabar price **path** over each trade's
holding window, which C1 does not join (``trade_journal.db`` persists no candles
/ MFE / MAE). This builder closes that gap: for each closed trade it fetches the
candle path over ``[entry, exit]`` (via the historical-range
``market_data.fetch_candles(since=…)``, CCXT/Bybit today), computes the excursion
outcome columns with the pure ``src.research.excursions`` helper, and joins them
to the SAME decision-time feature vector C1 emits — producing the analysis-ready
exit panel the C2 toolkit reads (outcome = ``giveback_r`` / ``capture_ratio`` /
``mfe_r`` / … instead of ``win`` / ``r``).

**Coverage is honest.** A trade whose candle window can't be fetched (a non-CCXT
venue today; a window older than the exchange's history; a fetch error) is kept
with ``excursion_present: false`` and null excursion columns — the honest
coverage denominator (mirroring the fc-geometry soak's ``fc_present``), never a
fabricated path.

**Observe-only, Tier-1 usage / Tier-2 dependency.** The builder itself only
reads (``trade_journal.db`` + candle fetches) and writes a panel file; it never
touches the order path. It does depend on the P5 range-fetch extension to
``market_data`` / the Bybit connector (a Tier-2 read-path change) — so this whole
P5 slice ships as a **draft for operator review**. The candle fetcher is injected
(``fetcher=``) so the wiring is unit-tested with synthetic candles offline; the
real per-trade fetch is validated VM-side.

Usage::

    python scripts/research/build_exit_panel.py --db /path/trade_journal.db \\
        --cohort real --timeframe 15m --out runtime_logs/research/exit_panel.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.research.component_vector import (  # noqa: E402
    KIND_CATEGORICAL,
    KIND_GATE,
    KIND_GRADED,
    extract,
)
from src.research.excursions import (  # noqa: E402
    compute_excursions,
    excursion_feature_names,
)
from src.utils.paths import trade_journal_db_path  # noqa: E402

try:
    from src.utils.closed_at import close_time_sql  # noqa: E402
except Exception:  # noqa: BLE001
    close_time_sql = None  # type: ignore[assignment]

try:
    from src.web.api._clean_trades import (  # noqa: E402
        exclude_reconciler_predicate,
        exclude_superseded_predicate,
        not_paper_predicate,
        paper_predicate,
    )
except Exception:  # noqa: BLE001
    def not_paper_predicate(prefix: str = "") -> str:  # type: ignore[misc]
        return (
            f" AND COALESCE({prefix}account_class,'') != 'paper'"
            f" AND COALESCE({prefix}is_demo,0) = 0"
        )

    def paper_predicate(prefix: str = "") -> str:  # type: ignore[misc]
        return (
            f" AND (COALESCE({prefix}account_class,'') = 'paper'"
            f" OR COALESCE({prefix}is_demo,0) = 1)"
        )

    def exclude_reconciler_predicate(prefix: str = "") -> str:  # type: ignore[misc]
        return f" AND COALESCE({prefix}setup_type,'') != 'adopted_orphan'"

    def exclude_superseded_predicate(prefix: str = "") -> str:  # type: ignore[misc]
        return f" AND COALESCE({prefix}reconcile_status,'') != 'superseded'"


# Candle-interval seconds for the small set of research timeframes (→ how many
# bars a holding window needs, and the `since` step).
_TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}

_EXCURSION_COLS = excursion_feature_names()
_OUTCOME_COLS = tuple(["pnl", "win", "r", *_EXCURSION_COLS])
_KEY_COLS = ("strategy", "symbol", "cohort", "closed_at", "excursion_present")


# ---------------------------------------------------------------------------
# time parsing
# ---------------------------------------------------------------------------


def _to_epoch_ms(value: Any) -> Optional[int]:
    """Parse a journal timestamp (epoch-ms string / epoch-sec / ISO-8601) → ms.

    Tolerant: returns ``None`` on anything unparseable.
    """
    if value is None:
        return None
    # numeric epoch (seconds or ms)
    try:
        f = float(value)
        # heuristic: >1e11 ⇒ already ms; else seconds
        return int(f) if f > 1e11 else int(f * 1000)
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if not s:
        return None
    try:
        from datetime import datetime, timezone

        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# candle fetcher (injectable)
# ---------------------------------------------------------------------------

# A fetcher takes (symbol, timeframe, since_ms, limit) → list of candle dicts
# ({high, low, ...}) or None. The default uses the live range-fetch path; tests
# inject a synthetic one so the wiring is exercised offline.
Fetcher = Callable[[str, str, int, int], Optional[List[Dict[str, Any]]]]


def _default_fetcher(symbol: str, timeframe: str, since_ms: int, limit: int):
    """Live candle fetch over a historical window (Bybit/CCXT `since=`)."""
    try:
        from src.runtime.market_data import connector_for_symbol, fetch_candles
    except Exception:  # noqa: BLE001
        return None
    try:
        client = connector_for_symbol(symbol)
    except Exception:  # noqa: BLE001
        client = None
    df = fetch_candles(
        symbol, timeframe, limit=limit, since=since_ms, exchange_client=client
    )
    if df is None:
        return None
    try:
        return df.to_dict("records")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# DB read (geometry + decision-time features)
# ---------------------------------------------------------------------------


def _open_ro(db_path: str) -> Optional[sqlite3.Connection]:
    p = Path(db_path)
    if not p.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _cols(conn: sqlite3.Connection, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _decode(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _close_expr() -> str:
    if close_time_sql is not None:
        try:
            return close_time_sql("t.closed_at", "op.updated_at", "t.timestamp")
        except Exception:  # noqa: BLE001
            pass
    return "COALESCE(t.closed_at, op.updated_at, t.timestamp)"


def fetch_exit_rows(
    conn: sqlite3.Connection, *, cohort: str, strategy: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Closed trades × order_packages → rows carrying BOTH the exit geometry
    (entry/stop/exit/side/times/symbol) AND the decision-time feature vector."""
    tcols = _cols(conn, "trades")
    if not tcols:
        return []
    ocols = _cols(conn, "order_packages")
    have_op = bool(ocols)
    have_meta = "meta" in ocols
    close_expr = _close_expr()

    if have_op:
        sel_sl = "op.signal_logic AS signal_logic,"
        sel_conf = "op.confidence AS op_confidence,"
        sel_meta = "op.meta AS op_meta," if have_meta else "NULL AS op_meta,"
        meta_agg = "MIN(meta) AS meta," if have_meta else ""
        join = (
            "LEFT JOIN (\n"
            "  SELECT linked_trade_id, MIN(signal_logic) AS signal_logic,\n"
            "         MIN(confidence) AS confidence,\n"
            f"         {meta_agg}\n"
            "         MIN(updated_at) AS updated_at\n"
            "  FROM order_packages WHERE linked_trade_id IS NOT NULL\n"
            "  GROUP BY linked_trade_id\n"
            ") op ON op.linked_trade_id = t.id"
        )
    else:
        sel_sl = "NULL AS signal_logic,"
        sel_conf = "NULL AS op_confidence,"
        sel_meta = "NULL AS op_meta,"
        join = ""

    cohort_sql = paper_predicate("t.") if cohort == "paper" else not_paper_predicate("t.")
    cohort_sql += exclude_reconciler_predicate("t.") + exclude_superseded_predicate("t.")
    params: List[Any] = []
    strat_sql = ""
    if strategy:
        strat_sql = " AND COALESCE(t.strategy_name,'') = ?"
        params.append(strategy)

    sql = f"""
        SELECT t.strategy_name AS strategy, t.symbol AS symbol,
               t.direction AS direction,
               t.entry_price AS entry_price, t.stop_loss AS stop_loss,
               t.exit_price AS exit_price, t.pnl AS pnl,
               t.timestamp AS entry_time,
               {sel_sl}{sel_conf}{sel_meta}
               {close_expr} AS closed_at
        FROM trades t
        {join}
        WHERE t.status = 'closed' AND COALESCE(t.is_backtest,0) = 0
              AND t.pnl IS NOT NULL{cohort_sql}{strat_sql}
        ORDER BY {close_expr} ASC
    """
    try:
        raw = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []

    out: List[Dict[str, Any]] = []
    for row in raw:
        keys = row.keys()
        strat = row["strategy"] or "(unknown)"
        signal_logic = _decode(row["signal_logic"] if "signal_logic" in keys else None)
        meta = _decode(row["op_meta"] if "op_meta" in keys else None)
        conf = row["op_confidence"] if "op_confidence" in keys else None
        if meta or signal_logic:
            combined: Dict[str, Any] = {}
            if isinstance(meta, dict):
                combined.update(meta)
            if isinstance(signal_logic, dict):
                combined.update(signal_logic)
        else:
            combined = None  # type: ignore[assignment]
        comp = extract(strat, combined, extra={"confidence": conf})
        feat: Dict[str, float] = {}
        cat: Dict[str, str] = {}
        gate: Dict[str, bool] = {}
        for name, c in comp.items():
            if c.kind == KIND_GRADED:
                feat[name] = c.value
            elif c.kind == KIND_CATEGORICAL:
                cat[name] = c.value
            elif c.kind == KIND_GATE:
                gate[name] = bool(c.value)
        out.append(
            {
                "strategy": strat,
                "symbol": row["symbol"] or "",
                "cohort": cohort,
                "direction": row["direction"],
                "entry_price": row["entry_price"],
                "stop_loss": row["stop_loss"],
                "exit_price": row["exit_price"],
                "entry_time": row["entry_time"],
                "closed_at": row["closed_at"] if "closed_at" in keys else None,
                "pnl": row["pnl"],
                "feat": feat,
                "cat": cat,
                "gate": gate,
            }
        )
    return out


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _needed_limit(entry_ms: int, exit_ms: int, tf_seconds: int, cap: int = 1000) -> int:
    span_s = max(0, (exit_ms - entry_ms) // 1000)
    bars = span_s // max(tf_seconds, 1) + 2  # +2 for the entry/exit boundary bars
    return int(min(max(bars, 1), cap))


def _slice_window(candles: Sequence[Dict[str, Any]], entry_ms: int, exit_ms: int):
    """Keep candles whose timestamp is within [entry, exit]. If timestamps are
    absent/unparseable, keep all (assume the fetch already bounded the window)."""
    kept = []
    saw_ts = False
    for c in candles:
        ts = c.get("timestamp") if isinstance(c, dict) else None
        ms = _to_epoch_ms(ts)
        if ms is None:
            kept.append(c)
            continue
        saw_ts = True
        if entry_ms <= ms <= exit_ms:
            kept.append(c)
    return kept if saw_ts else list(candles)


def build_exit_panel(
    *,
    db_path: str,
    cohort: str = "real",
    strategy: Optional[str] = None,
    timeframe: str = "15m",
    fetcher: Optional[Fetcher] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(rows, manifest)`` — the exit panel. Tolerant end-to-end."""
    fetch = fetcher or _default_fetcher
    tf_seconds = _TF_SECONDS.get(timeframe, 900)
    conn = _open_ro(db_path)
    trade_rows: List[Dict[str, Any]] = []
    db_present = conn is not None
    if conn is not None:
        try:
            trade_rows = fetch_exit_rows(conn, cohort=cohort, strategy=strategy)
        finally:
            conn.close()

    flat: List[Dict[str, Any]] = []
    covered = 0
    for tr in trade_rows:
        entry_ms = _to_epoch_ms(tr.get("entry_time"))
        exit_ms = _to_epoch_ms(tr.get("closed_at"))
        exc: Dict[str, Any] = {k: None for k in _EXCURSION_COLS}
        present = False
        if entry_ms is not None and exit_ms is not None and exit_ms >= entry_ms:
            limit = _needed_limit(entry_ms, exit_ms, tf_seconds)
            candles = fetch(tr["symbol"], timeframe, entry_ms, limit)
            if candles:
                window = _slice_window(candles, entry_ms, exit_ms)
                res = compute_excursions(
                    window,
                    entry_price=tr.get("entry_price"),
                    stop_loss=tr.get("stop_loss"),
                    side=tr.get("direction"),
                    exit_price=tr.get("exit_price"),
                )
                if res.get("bars_held"):
                    exc = {k: res.get(k) for k in _EXCURSION_COLS}
                    present = True
        if present:
            covered += 1

        rec: Dict[str, Any] = {
            "strategy": tr["strategy"],
            "symbol": tr["symbol"],
            "cohort": tr["cohort"],
            "closed_at": tr.get("closed_at"),
            "excursion_present": present,
            "pnl": tr.get("pnl"),
            "win": 1 if (tr.get("pnl") or 0) > 0 else 0,
            "r": exc.get("mfe_r") if False else None,  # r not computed here; excursions carry R
        }
        # excursion outcome columns
        for k in _EXCURSION_COLS:
            rec[k] = exc.get(k)
        # decision-time feature columns
        for k, v in tr.get("feat", {}).items():
            rec[f"feat_{k}"] = v
        for k, v in tr.get("cat", {}).items():
            rec[f"cat_{k}"] = v
        for k, v in tr.get("gate", {}).items():
            rec[f"gate_{k}"] = v
        flat.append(rec)

    feature_cols = sorted(
        {
            k
            for rec in flat
            for k in rec
            if k.startswith(("feat_", "cat_", "gate_"))
        }
    )
    manifest = {
        "source_db": db_path,
        "db_present": db_present,
        "cohort": cohort,
        "strategy_filter": strategy,
        "timeframe": timeframe,
        "row_count": len(flat),
        "excursion_covered": covered,
        "excursion_coverage_pct": round(100.0 * covered / len(flat), 1) if flat else 0.0,
        "key_cols": list(_KEY_COLS),
        "outcome_cols": list(_OUTCOME_COLS),
        "feature_cols": feature_cols,
        "leakage_contract": (
            "feature_cols + key_cols are decision-time (recorded at signal time, "
            "before the exit); the excursion outcome_cols (mfe_r/mae_r/giveback_r/"
            "capture_ratio/…) summarise the strictly-future post-entry price path. "
            "Regress an excursion outcome on features only; never a feature on an "
            "outcome."
        ),
    }
    return flat, manifest


def write_panel(
    rows: List[Dict[str, Any]], manifest: Dict[str, Any], out_path: Path
) -> tuple[Path, Path]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    manifest_path = out_path.with_suffix(out_path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out_path, manifest_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the M30 EXIT-timing research panel (excursion outcomes ← "
            "decision-time features) from trade_journal.db + candle paths. "
            "Read-only, observe-only; depends on the P5 range-fetch extension."
        )
    )
    parser.add_argument("--db", default=None, help="trade_journal.db path (default: resolver).")
    parser.add_argument("--cohort", choices=["real", "paper"], default="real")
    parser.add_argument("--strategy", default=None, help="Restrict to one strategy_name.")
    parser.add_argument("--timeframe", default="15m",
                        help=f"Candle timeframe for excursions ({'/'.join(_TF_SECONDS)}).")
    parser.add_argument("--out", default="runtime_logs/research/exit_panel.jsonl")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    db_path = args.db or str(trade_journal_db_path())
    rows, manifest = build_exit_panel(
        db_path=db_path, cohort=args.cohort, strategy=args.strategy,
        timeframe=args.timeframe,
    )
    out_path, manifest_path = write_panel(rows, manifest, Path(args.out))
    if not args.quiet:
        print(
            f"exit panel: {manifest['row_count']} rows "
            f"({manifest['excursion_covered']} with excursions, "
            f"{manifest['excursion_coverage_pct']}%) "
            f"→ {out_path} (+ {manifest_path.name})"
        )
        if not manifest["db_present"]:
            print(f"  note: DB not found at {db_path} — empty panel emitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
