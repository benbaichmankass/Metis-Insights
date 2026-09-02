#!/usr/bin/env python3
"""M30 · C1 — the analysis-ready research PANEL builder.

The technical-quant-research platform's first brick (scoping:
``docs/research/technical-quant-research-platform-scoping-2026-07-27.md``). It
emits **one analysis-ready table** joining every closed trade's **outcome**
(``pnl`` / ``win`` / R-multiple / close-time) to its **decision-time features**
(the canonical ``component_vector`` structure vector + the stamped regime / vol
categoricals + a per-row model-score summary) — the single queryable panel the
C2 toolkit (regression / feature-importance / conditional-edge tables) and every
downstream study read, instead of each re-deriving the same join.

**Faithful to the existing analyzer.** It reuses the *canonical* primitives the
Layer-1a report (``scripts/research/component_edge_report.py``) uses — the
component adapter (``src.research.component_vector.extract``), the R-basis
(``src.web.api._clean_trades.r_multiple`` + ``src.runtime.local_pnl.contract_value_usd_for``),
the epoch-ms-aware close-time expr (``src.utils.closed_at.close_time_sql``), and
the cohort / reconciler / superseded predicates — so a panel row sees exactly
what the report sees, plus the categorical + gate + model-score columns the
report drops. No new join logic invented; no new data source (v1 is a pure join
over ``trade_journal.db`` we already persist).

**Leakage discipline (the platform's binding invariant).** Every FEATURE column
is a decision-time fact (recorded at signal time, before the outcome exists); the
OUTCOME columns (``pnl`` / ``win`` / ``r``) are strictly future to them. The
emitted manifest stamps this split explicitly so a study can never regress an
outcome on a same-time/future column by accident.

**Observe-only, Tier-1.** Read-only ``mode=ro`` SQLite; never opens a broker
socket, never writes the money DB, never touches the order path. Tolerant: a
missing / unreadable DB yields an empty panel + an honest manifest, never a
crash.

Usage::

    python scripts/research/build_research_panel.py            # → runtime_logs/research/panel.jsonl (+ .manifest.json)
    python scripts/research/build_research_panel.py --cohort both --out /tmp/panel.jsonl
    python scripts/research/build_research_panel.py --db /path/to/trade_journal.db --strategy trend_donchian
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.research.component_vector import (  # noqa: E402
    KIND_CATEGORICAL,
    KIND_GATE,
    KIND_GRADED,
    extract,
)

# --- Canonical resolvers / R-basis (single source of truth) ----------------
# Resolve the DB via the canonical resolver only — never an inline
# TRADE_JOURNAL_DB read / CWD fallback (the canonical-db-resolver CI guard).
from src.utils.paths import trade_journal_db_path  # noqa: E402

try:  # epoch-ms-aware close-time SQL; degrade to a plain COALESCE if absent.
    from src.utils.closed_at import close_time_sql  # noqa: E402
except Exception:  # noqa: BLE001
    close_time_sql = None  # type: ignore[assignment]

try:
    from src.web.api._clean_trades import (  # noqa: E402
        exclude_reconciler_predicate,
        exclude_superseded_predicate,
        not_paper_predicate,
        paper_predicate,
        r_multiple,
    )
    from src.runtime.local_pnl import contract_value_usd_for  # noqa: E402

    _CLEAN_TRADES_OK = True
except Exception:  # noqa: BLE001 — keep the tool importable on a minimal tree
    _CLEAN_TRADES_OK = False

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

    def r_multiple(pnl, entry_price, stop_loss, qty, contract_value_usd):  # type: ignore[misc]
        try:
            if None in (pnl, entry_price, stop_loss, qty):
                return None
            risk = abs(float(entry_price) - float(stop_loss)) * abs(float(qty)) * float(
                contract_value_usd or 1.0
            )
            return float(pnl) / risk if risk > 0 else None
        except (TypeError, ValueError):
            return None

    def contract_value_usd_for(symbol):  # type: ignore[misc]  # inert: symbol — import-fallback stub; must match the real signature, and a flat 1.0 is the declared degraded value
        return 1.0


# ---------------------------------------------------------------------------
# Panel row
# ---------------------------------------------------------------------------


@dataclass
class PanelRow:
    """One closed trade: outcome + decision-time feature columns, flattened."""

    strategy: str
    symbol: str
    cohort: str  # "real" | "paper"
    closed_at: Optional[str]
    # outcome (strictly future to every feature)
    pnl: float
    win: int
    r: Optional[float]
    # decision-time features, flattened by kind
    feat: Dict[str, float] = field(default_factory=dict)  # graded (numeric)
    cat: Dict[str, str] = field(default_factory=dict)  # categorical
    gate: Dict[str, bool] = field(default_factory=dict)  # boolean gates
    model_score_mean: Optional[float] = None
    model_score_max: Optional[float] = None
    model_score_count: int = 0

    def to_flat(self) -> Dict[str, Any]:
        """One flat, pandas-friendly record. Feature keys are prefixed by kind
        so a consumer can select the decision-time columns unambiguously."""
        rec: Dict[str, Any] = {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "cohort": self.cohort,
            "closed_at": self.closed_at,
            # outcomes
            "pnl": self.pnl,
            "win": self.win,
            "r": self.r,
        }
        for k, v in self.feat.items():
            rec[f"feat_{k}"] = v
        for k, v in self.cat.items():
            rec[f"cat_{k}"] = v
        for k, v in self.gate.items():
            rec[f"gate_{k}"] = v
        if self.model_score_count:
            rec["feat_model_score_mean"] = self.model_score_mean
            rec["feat_model_score_max"] = self.model_score_max
            rec["model_score_count"] = self.model_score_count
        return rec


# ---------------------------------------------------------------------------
# DB read (read-only)
# ---------------------------------------------------------------------------


def _close_time_expr() -> str:
    if close_time_sql is not None:
        try:
            return close_time_sql("t.closed_at", "op.updated_at", "t.timestamp")
        except Exception:  # noqa: BLE001
            pass
    return "COALESCE(t.closed_at, op.updated_at, t.timestamp)"


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


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _decode_json(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _model_score_summary(raw: Any) -> tuple[Optional[float], Optional[float], int]:
    """(mean, max, count) over the row's per-model scores, or (None, None, 0).

    ``model_scores`` is ``{model_id: {stage, score}}`` captured at signal time
    (order_packages.model_scores). Decision-time by construction — safe as a
    feature. Non-numeric / malformed entries are skipped, not fabricated.

    ⚠️ ``score`` DOES NOT MEAN THE SAME THING ACROSS HEADS, so ``mean``/``max``
    here pool quantities that are not commensurable. Per
    ``scripts/ml/_regime_score_semantics`` (the one module that owns this
    answer): it is P(positive) for a binary head but ``max(proba)`` for a
    MulticlassPredictor — hence ``>= 0.5`` by construction for a 2-class regime
    head, and HIGH for a head that is confidently CALM. Every regime head in
    the fleet is multiclass. Read ``ms_mean``/``ms_max`` as *aggregate head
    confidence*, never as a probability of anything in particular, and do not
    threshold them as if they were one.
    """
    d = _decode_json(raw)
    if not d:
        return None, None, 0
    scores: List[float] = []
    for v in d.values():
        s = None
        if isinstance(v, dict):
            # provenance: score — the raw logged predict() field, whose meaning
            # depends on head arity (see this function's docstring). NOT a
            # class probability.
            s = v.get("score")
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            s = v
        try:
            if s is not None and not isinstance(s, bool):
                fs = float(s)
                if fs == fs:  # not NaN
                    scores.append(fs)
        except (TypeError, ValueError):
            continue
    if not scores:
        return None, None, 0
    return sum(scores) / len(scores), max(scores), len(scores)


def fetch_panel_rows(
    conn: sqlite3.Connection,
    *,
    cohort: str,
    strategy: Optional[str] = None,
) -> List[PanelRow]:
    """Join closed trades × order_packages → PanelRow list for one cohort.

    ``cohort`` ∈ {"real", "paper"}. Never raises on a bad row (a row whose
    signal_logic won't decode simply contributes no feature columns).
    Mirrors ``component_edge_report._fetch_trades`` and additionally keeps the
    categorical + gate + model-score columns that report drops.
    """
    tcols = _table_columns(conn, "trades")
    if not tcols:
        return []
    ocols = _table_columns(conn, "order_packages")
    have_op = bool(ocols)

    close_expr = _close_time_expr()
    sel_entry = "t.entry_price AS entry_price," if "entry_price" in tcols else ""
    sel_stop = "t.stop_loss AS stop_loss," if "stop_loss" in tcols else ""
    sel_qty = "t.position_size AS qty," if "position_size" in tcols else ""
    # P0.1 (2026-07-31 audit): the panel's `pnl`/`win`/`r` outcome columns are
    # the substrate for standing research verdicts, so rows whose pnl
    # provenance is not measured/estimated are excluded — judged via
    # provenance.pnl_is_trustworthy over trades.notes. Guarded on the column
    # existing (a fixture/legacy journal without `notes` cannot be judged and
    # is passed through unfiltered, with the skip printed below).
    sel_notes = "t.notes AS trade_notes," if "notes" in tcols else ""

    if have_op:
        have_ms = "model_scores" in ocols
        have_meta = "meta" in ocols
        sel_sl = "op.signal_logic AS signal_logic,"
        sel_conf = "op.confidence AS op_confidence,"
        sel_ms = "op.model_scores AS model_scores," if have_ms else "NULL AS model_scores,"
        # P4: the killzone / bias / setup_type (+ stamped regime) decision-time
        # context lives in the SEPARATE order_packages.meta column, which C1
        # historically didn't read — the binding feature-capture gap Study 2
        # found. Pull it in (guarded on the column existing) so it merges into
        # the component extractor below.
        sel_meta = "op.meta AS op_meta," if have_meta else "NULL AS op_meta,"
        # MIN(...) mirrors the report's MIN() collapse for the
        # one-order-package-per-trade case (deterministic pick when >1).
        ms_agg = "MIN(model_scores) AS model_scores," if have_ms else ""
        meta_agg = "MIN(meta) AS meta," if have_meta else ""
        join = (
            "LEFT JOIN (\n"
            "  SELECT linked_trade_id,\n"
            "         MIN(signal_logic) AS signal_logic,\n"
            "         MIN(confidence)   AS confidence,\n"
            f"         {ms_agg}\n"
            f"         {meta_agg}\n"
            "         MIN(updated_at)   AS updated_at\n"
            "  FROM order_packages\n"
            "  WHERE linked_trade_id IS NOT NULL\n"
            "  GROUP BY linked_trade_id\n"
            ") op ON op.linked_trade_id = t.id"
        )
    else:
        sel_sl = "NULL AS signal_logic,"
        sel_conf = "NULL AS op_confidence,"
        sel_ms = "NULL AS model_scores,"
        sel_meta = "NULL AS op_meta,"
        join = ""

    where = [
        "t.status = 'closed'",
        "COALESCE(t.is_backtest, 0) = 0",
        "t.pnl IS NOT NULL",
    ]
    where_sql = " AND ".join(where)
    cohort_sql = paper_predicate("t.") if cohort == "paper" else not_paper_predicate("t.")
    cohort_sql += exclude_reconciler_predicate("t.") + exclude_superseded_predicate("t.")

    params: List[Any] = []
    strat_sql = ""
    if strategy:
        strat_sql = " AND COALESCE(t.strategy_name,'') = ?"
        params.append(strategy)

    sql = f"""
        SELECT t.strategy_name AS strategy,
               t.symbol AS symbol,
               t.pnl AS pnl,
               {sel_entry}{sel_stop}{sel_qty}{sel_notes}
               {sel_sl}{sel_conf}{sel_ms}{sel_meta}
               {close_expr} AS closed_at
        FROM trades t
        {join}
        WHERE {where_sql}{cohort_sql}{strat_sql}
        ORDER BY {close_expr} ASC
    """
    try:
        raw = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []

    judge_provenance = bool(sel_notes)
    if judge_provenance:
        from src.runtime.provenance import pnl_is_trustworthy
    else:
        print(
            f"  note: provenance filter SKIPPED for cohort={cohort} — this "
            f"journal has no trades.notes column, so pnl provenance cannot "
            f"be judged; panel rows are unfiltered."
        )
    excluded_untrusted = 0
    out: List[PanelRow] = []
    for row in raw:
        keys = row.keys()
        strat = row["strategy"] or "(unknown)"
        symbol = row["symbol"] or ""
        try:
            pnl = float(row["pnl"])
        except (TypeError, ValueError):
            continue
        if judge_provenance and not pnl_is_trustworthy(row["trade_notes"]):
            excluded_untrusted += 1
            continue
        entry = row["entry_price"] if "entry_price" in keys else None
        stop = row["stop_loss"] if "stop_loss" in keys else None
        qty = row["qty"] if "qty" in keys else None
        rr = r_multiple(pnl, entry, stop, qty, contract_value_usd_for(symbol))

        signal_logic = _decode_json(row["signal_logic"] if "signal_logic" in keys else None)
        meta = _decode_json(row["op_meta"] if "op_meta" in keys else None)
        conf = row["op_confidence"] if "op_confidence" in keys else None
        # P4: merge order_packages.meta UNDER signal_logic (signal_logic wins on
        # any key collision, preserving the pre-P4 regime/adx behaviour) so the
        # meta-only session/bias context (killzone / bias / setup_type) flows to
        # the extractor for every strategy, while a strategy that also writes a
        # richer signal_logic key keeps its own value.
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

        ms_mean, ms_max, ms_count = _model_score_summary(
            row["model_scores"] if "model_scores" in keys else None
        )

        out.append(
            PanelRow(
                strategy=strat,
                symbol=symbol,
                cohort=cohort,
                closed_at=row["closed_at"] if "closed_at" in keys else None,
                pnl=pnl,
                win=1 if pnl > 0 else 0,
                r=rr,
                feat=feat,
                cat=cat,
                gate=gate,
                model_score_mean=ms_mean,
                model_score_max=ms_max,
                model_score_count=ms_count,
            )
        )
    if excluded_untrusted:
        total = len(out) + excluded_untrusted
        print(
            f"  POPULATION CHANGED (cohort={cohort}): excluded "
            f"{excluded_untrusted} of {total} closed rows whose pnl "
            f"provenance is not measured/estimated "
            f"(provenance.pnl_is_trustworthy over trades.notes); "
            f"{len(out)} panel rows kept."
        )
    return out


# ---------------------------------------------------------------------------
# Build + manifest
# ---------------------------------------------------------------------------

# Leakage stamp: which columns are decision-time (safe as regressors) vs the
# outcome (strictly future). A study MUST regress outcome-on-feature only.
_OUTCOME_COLS = ("pnl", "win", "r")
_KEY_COLS = ("strategy", "symbol", "cohort", "closed_at")


def build_panel(
    *,
    db_path: str,
    cohort: str = "real",
    strategy: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(rows, manifest)``. Tolerant: unreadable DB → empty panel."""
    cohorts = ["real", "paper"] if cohort == "both" else [cohort]
    conn = _open_ro(db_path)
    rows: List[PanelRow] = []
    db_present = conn is not None
    if conn is not None:
        try:
            for c in cohorts:
                rows.extend(fetch_panel_rows(conn, cohort=c, strategy=strategy))
        finally:
            conn.close()

    flat = [r.to_flat() for r in rows]
    feature_cols = sorted(
        {
            k
            for rec in flat
            for k in rec
            if k not in _OUTCOME_COLS and k not in _KEY_COLS and k != "model_score_count"
        }
    )
    by_cohort: Dict[str, int] = {}
    for rec in flat:
        by_cohort[rec["cohort"]] = by_cohort.get(rec["cohort"], 0) + 1

    manifest = {
        "source_db": db_path,
        "db_present": db_present,
        "clean_trades_helpers": _CLEAN_TRADES_OK,
        "cohort": cohort,
        "strategy_filter": strategy,
        "row_count": len(flat),
        "rows_by_cohort": by_cohort,
        "key_cols": list(_KEY_COLS),
        "outcome_cols": list(_OUTCOME_COLS),
        "feature_cols": feature_cols,
        # The binding invariant, stamped so a study can assert it:
        "leakage_contract": (
            "feature_cols + key_cols are decision-time (recorded at signal "
            "time, before the outcome); outcome_cols are strictly future. "
            "Regress outcome-on-feature only; never a feature on an outcome."
        ),
    }
    return flat, manifest


def write_panel(
    rows: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    out_path: Path,
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
            "Build the M30 analysis-ready research panel (outcome ← "
            "decision-time features) from trade_journal.db. Read-only, "
            "observe-only, Tier-1."
        )
    )
    parser.add_argument(
        "--db",
        default=None,
        help="trade_journal.db path (default: canonical resolver).",
    )
    parser.add_argument(
        "--cohort",
        choices=["real", "paper", "both"],
        default="real",
        help="Which funding cohort to emit (default: real).",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Restrict to one strategy_name (default: all).",
    )
    parser.add_argument(
        "--out",
        default="runtime_logs/research/panel.jsonl",
        help="Output JSONL path (a sibling .manifest.json is written too).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human summary line (still writes files).",
    )
    args = parser.parse_args(argv)

    db_path = args.db or str(trade_journal_db_path())
    rows, manifest = build_panel(
        db_path=db_path, cohort=args.cohort, strategy=args.strategy
    )
    out_path, manifest_path = write_panel(rows, manifest, Path(args.out))
    if not args.quiet:
        print(
            f"panel: {manifest['row_count']} rows "
            f"({manifest['rows_by_cohort'] or 'empty'}), "
            f"{len(manifest['feature_cols'])} feature cols "
            f"→ {out_path} (+ {manifest_path.name})"
        )
        if not manifest["db_present"]:
            print(f"  note: DB not found at {db_path} — empty panel emitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
