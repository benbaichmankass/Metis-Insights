#!/usr/bin/env python3
"""Component-edge report — signal-research framework Layer 1a (DESIGN §4.1a / §8).

The missing rung on the scoring ladder: the system scores trading quality at
**strategy** and **decision** granularity, but never at **signal-component**
granularity. A strategy is a bundle of predicates (sweep + displacement + FVG +
mitigation + HTF-bias + a confidence threshold); we score the bundle but have
never asked *which predicate carries the edge and which is dead weight*. This
report adds that, read-only, over existing journal history.

What it does
------------
For each strategy with closed trades, join ``trades`` × ``order_packages`` on
``order_package_id``, decode each row's ``signal_logic`` through the canonical
component-vector adapter (``src.research.component_vector``), and for every
**graded** canonical component:

  * **Conditional expectancy-R by bucket** — bucket the component into terciles
    (configurable; min-per-bucket floor) and report n / win_rate / mean-R /
    expectancy (mean pnl) per bucket. Monotone lift across buckets ⇒ the
    component carries edge; flat ⇒ dead weight.
  * **Univariate discrimination** — AUC of the component vs realised win
    (rank-based Mann-Whitney; stdlib math, no sklearn).
  * **Decay** — the same bucketed edge recomputed over the recent window vs the
    prior window, reporting the direction of change.
  * **Verdict** ∈ {edge, weak, none, insufficient}, honouring the **M18 prior**
    (entry-feature edge ≈ coin-flip OOS): small-n / flat / non-monotone is
    reported plainly as "no edge / insufficient", NEVER manufactured.

**Marginal lift** (does the component add edge after controlling for the
others) is OPTIONAL in P0: a dependency-safe hand-rolled logistic fit runs only
when numpy is importable; otherwise the report emits
``marginal_lift: not_computed`` rather than faking it.

R-metric basis
--------------
R = ``pnl / (|entry_price - stop_loss| * |position_size| * contract_value_usd)``
— the SAME normalisation ``/api/bot/performance`` uses
(``src.web.api._clean_trades.r_multiple`` +
``src.runtime.local_pnl.contract_value_usd_for``), so a micro crypto trade and a
futures contract compare on one R axis. When a trade's risk is unknown, its R is
**null** — never a raw-pnl fallback (the design's honesty rule). ``rCoverage``
reports the fraction of trades that were R-measurable.

Cohort
------
Defaults to the **real-money** cohort (account_class-authoritative, is_demo
fallback — the same ``not_paper_predicate`` the rest of the API uses), excluding
backtest + reconciler/superseded artifact rows. ``--include-paper`` adds a
SEPARATE paper section (never blended — the "real and paper never blended"
contract).

Outputs (DESIGN §8)
-------------------
``runtime_logs/signal_research/component_edge_<strategy>.json`` + ``.md`` per
strategy, plus a combined ``component_edge_index.json``. Best-effort,
never-raise writer.

Tier-1, read-only: opens a ``mode=ro`` connection, reads the journal, writes
report files. Touches NOTHING live.

CLI
---
    python scripts/research/component_edge_report.py
    python scripts/research/component_edge_report.py --strategy vwap
    python scripts/research/component_edge_report.py --include-paper --min-bucket 15
    python scripts/research/component_edge_report.py --db /path/to/trade_journal.db

Backtest-log mode (run over BACKTEST volume — thousands of trades — instead of
the thin live journal). The standalone harnesses now emit the LIVE
order_package ``meta`` on each ``--emit-trades`` row, so the same per-component
analysis runs over the much larger backtest cohort. R is taken straight from
the emitted ``net_r`` / ``gross_r`` (rCoverage = 1.0 by construction); the DB
is not touched at all. Cohort is labelled ``backtest``::

    python scripts/research/component_edge_report.py \
        --backtest-log runtime_logs/backtest_vwap_trades.jsonl
    python scripts/research/component_edge_report.py \
        --backtest-log scalp_trades.jsonl --strategy-name ict_scalp_5m
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The repo root is two levels up from scripts/research/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.research.component_vector import (  # noqa: E402
    KIND_GRADED,
    extract,
    graded_component_names,
)

# --- Canonical resolvers / R-basis (single source of truth) ----------------
# REQUIRED: resolve the DB via the canonical resolver, never an inline
# TRADE_JOURNAL_DB read / CWD fallback (the canonical-db-resolver CI guard).
from src.utils.paths import trade_journal_db_path  # noqa: E402

try:
    from src.utils.closed_at import close_time_sql  # noqa: E402
except Exception:  # noqa: BLE001 — degrade to a plain COALESCE if unavailable
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
        col = f"{prefix}account_class" if prefix else "account_class"
        demo = f"{prefix}is_demo" if prefix else "is_demo"
        return (
            f" AND NOT (COALESCE({col},'') IN ('paper','prop')"
            f" OR ({col} IS NULL AND COALESCE({demo},0)=1))"
        )

    def paper_predicate(prefix: str = "") -> str:  # type: ignore[misc]
        col = f"{prefix}account_class" if prefix else "account_class"
        demo = f"{prefix}is_demo" if prefix else "is_demo"
        return (
            f" AND (COALESCE({col},'')='paper'"
            f" OR ({col} IS NULL AND COALESCE({demo},0)=1))"
        )

    def exclude_reconciler_predicate(prefix: str = "") -> str:  # type: ignore[misc]
        sn = f"{prefix}strategy_name" if prefix else "strategy_name"
        return f" AND COALESCE({sn},'') NOT IN ('orphan_adopt')"

    def exclude_superseded_predicate(prefix: str = "") -> str:  # type: ignore[misc]
        rs = f"{prefix}reconcile_status" if prefix else "reconcile_status"
        return f" AND COALESCE({rs},'') != 'superseded'"

    def r_multiple(pnl, entry_price, stop_loss, qty, contract_value_usd):  # type: ignore[misc]
        try:
            if None in (pnl, entry_price, stop_loss, qty):
                return None
            risk = (
                abs(float(entry_price) - float(stop_loss))
                * abs(float(qty))
                * float(contract_value_usd or 0.0)
            )
            return float(pnl) / risk if risk > 0 else None
        except (TypeError, ValueError):
            return None

    def contract_value_usd_for(symbol):  # type: ignore[misc]
        return 1.0


# numpy is OPTIONAL — only used for the marginal-lift logistic fit. The report
# is fully usable (univariate only) without it.
try:
    import numpy as _np  # noqa: E402

    _NUMPY_OK = True
except Exception:  # noqa: BLE001
    _NUMPY_OK = False


DEFAULT_MIN_BUCKET = 10
DEFAULT_N_BUCKETS = 3  # terciles
# Recent-vs-prior decay windows (days).
DECAY_RECENT_DAYS = 30
DECAY_PRIOR_DAYS = 30

VERDICT_EDGE = "edge"
VERDICT_WEAK = "weak"
VERDICT_NONE = "none"
VERDICT_INSUFFICIENT = "insufficient"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TradeRow:
    """One closed trade joined to its order package."""

    strategy: str
    symbol: str
    pnl: float
    win: int  # 1 if pnl > 0 else 0
    r: Optional[float]  # R-multiple, or None when risk unknown
    closed_at: Optional[str]
    components: Dict[str, Any]  # canonical graded-component name -> float value


# ---------------------------------------------------------------------------
# DB read (read-only)
# ---------------------------------------------------------------------------


def _close_time_expr() -> str:
    """Canonical close-time SQL expression (epoch-ms aware when available)."""
    if close_time_sql is not None:
        try:
            return close_time_sql("t.closed_at", "op.updated_at", "t.timestamp")
        except Exception:  # noqa: BLE001
            pass
    return "COALESCE(t.closed_at, op.updated_at, t.timestamp)"


def _open_ro(db_path: str) -> Optional[sqlite3.Connection]:
    """Open a read-only connection, or None when the DB is absent / unreadable."""
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


def _fetch_trades(
    conn: sqlite3.Connection,
    *,
    paper: bool,
    strategy: Optional[str],
) -> List[TradeRow]:
    """Join closed trades × order_packages, decode signal_logic → components.

    Real-money by default; ``paper=True`` selects the paper cohort. Never
    raises on a bad row — a row whose signal_logic won't decode contributes no
    components (the adapter is tolerant) but still counts toward n / win-rate.
    """
    tcols = _table_columns(conn, "trades")
    if not tcols:
        return []
    ocols = _table_columns(conn, "order_packages")
    have_op = bool(ocols)

    close_expr = _close_time_expr()
    # Optional R-inputs — select only when the column exists (legacy schema).
    sel_entry = "t.entry_price AS entry_price," if "entry_price" in tcols else ""
    sel_stop = "t.stop_loss AS stop_loss," if "stop_loss" in tcols else ""
    sel_qty = "t.position_size AS qty," if "position_size" in tcols else ""

    # signal_logic + confidence come from order_packages; if there is no
    # order_packages table at all, those degrade to NULL.
    if have_op:
        sel_sl = "op.signal_logic AS signal_logic,"
        sel_conf = "op.confidence AS op_confidence,"
        join = (
            "LEFT JOIN (\n"
            "  SELECT linked_trade_id,\n"
            "         MIN(signal_logic) AS signal_logic,\n"
            "         MIN(confidence)   AS confidence,\n"
            "         MIN(updated_at)   AS updated_at\n"
            "  FROM order_packages\n"
            "  WHERE linked_trade_id IS NOT NULL\n"
            "  GROUP BY linked_trade_id\n"
            ") op ON op.linked_trade_id = t.id"
        )
    else:
        sel_sl = "NULL AS signal_logic,"
        sel_conf = "NULL AS op_confidence,"
        join = ""

    where = [
        "t.status = 'closed'",
        "COALESCE(t.is_backtest, 0) = 0",
        "t.pnl IS NOT NULL",
    ]
    where_sql = " AND ".join(where)
    cohort = paper_predicate("t.") if paper else not_paper_predicate("t.")
    cohort += exclude_reconciler_predicate("t.") + exclude_superseded_predicate("t.")

    params: List[Any] = []
    strat_sql = ""
    if strategy:
        strat_sql = " AND COALESCE(t.strategy_name,'') = ?"
        params.append(strategy)

    sql = f"""
        SELECT t.strategy_name AS strategy,
               t.symbol AS symbol,
               t.pnl AS pnl,
               {sel_entry}{sel_stop}{sel_qty}
               {sel_sl}{sel_conf}
               {close_expr} AS closed_at
        FROM trades t
        {join}
        WHERE {where_sql}{cohort}{strat_sql}
        ORDER BY {close_expr} ASC
    """
    try:
        raw = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []

    out: List[TradeRow] = []
    for row in raw:
        keys = row.keys()
        strat = row["strategy"] or "(unknown)"
        symbol = row["symbol"] or ""
        try:
            pnl = float(row["pnl"])
        except (TypeError, ValueError):
            continue
        entry = row["entry_price"] if "entry_price" in keys else None
        stop = row["stop_loss"] if "stop_loss" in keys else None
        qty = row["qty"] if "qty" in keys else None
        rr = r_multiple(pnl, entry, stop, qty, contract_value_usd_for(symbol))

        sl_raw = row["signal_logic"] if "signal_logic" in keys else None
        signal_logic = _decode_json(sl_raw)
        conf = row["op_confidence"] if "op_confidence" in keys else None
        comp = extract(strat, signal_logic, extra={"confidence": conf})
        graded = {
            name: c.value
            for name, c in comp.items()
            if c.kind == KIND_GRADED
        }
        out.append(
            TradeRow(
                strategy=strat,
                symbol=symbol,
                pnl=pnl,
                win=1 if pnl > 0 else 0,
                r=rr,
                closed_at=row["closed_at"] if "closed_at" in keys else None,
                components=graded,
            )
        )
    return out


def _decode_json(raw: Any) -> Optional[Dict[str, Any]]:
    """Decode a signal_logic JSON blob to a dict, or None (tolerant)."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, (str, bytes)):
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _present_strategies(conn: sqlite3.Connection, *, paper: bool) -> List[str]:
    """Distinct strategy_name values present in the closed-trade cohort."""
    cohort = paper_predicate("t.") if paper else not_paper_predicate("t.")
    cohort += exclude_reconciler_predicate("t.") + exclude_superseded_predicate("t.")
    sql = f"""
        SELECT DISTINCT COALESCE(t.strategy_name,'(unknown)') AS s
        FROM trades t
        WHERE t.status='closed' AND COALESCE(t.is_backtest,0)=0
          AND t.pnl IS NOT NULL{cohort}
        ORDER BY s
    """
    try:
        return [r["s"] for r in conn.execute(sql).fetchall() if r["s"]]
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------------------
# Backtest-log read (the --backtest-log input mode)
# ---------------------------------------------------------------------------


def _coerce_float(value: Any) -> Optional[float]:
    """Best-effort float, or None — local mirror so this path needs no DB libs."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return out


def _row_to_traderow(
    row: Dict[str, Any], *, force_strategy: Optional[str]
) -> Optional[TradeRow]:
    """Map ONE emit-trades JSONL row to a :class:`TradeRow`.

    The R-multiple is taken DIRECTLY from the emitted ``net_r`` (fallback
    ``gross_r``) — these are ALREADY risk-normalised R, so the backtest cohort
    is R-measured by construction (no journal r_multiple / contract_value
    recompute). ``win`` is ``r > 0``. The component vector is extracted from the
    row's ``meta`` (the LIVE order_package meta the harness now emits), with the
    emitted ``confidence`` folded in via ``extra`` (matching the DB path).

    Returns ``None`` for a row with no usable R (so it never silently counts as
    a 0-R win/loss). ``pnl`` is set to the R value so the per-bucket
    ``expectancy`` (mean pnl) reads as mean-R for the backtest cohort.
    """
    if not isinstance(row, dict):
        return None
    strat = force_strategy or row.get("strategy")
    if not strat:
        return None
    strat = str(strat)

    r = _coerce_float(row.get("net_r"))
    if r is None:
        r = _coerce_float(row.get("gross_r"))
    if r is None:
        return None

    meta = row.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    conf = row.get("confidence")
    comp = extract(strat, meta, extra={"confidence": conf})
    graded = {
        name: c.value for name, c in comp.items() if c.kind == KIND_GRADED
    }
    # entry_time drives the decay windows; absent → decay degrades gracefully.
    ts = row.get("entry_time")
    return TradeRow(
        strategy=strat,
        symbol=str(row.get("symbol") or ""),
        pnl=r,  # R-as-pnl: backtest cohort expectancy is in R units
        win=1 if r > 0 else 0,
        r=r,
        closed_at=str(ts) if ts is not None else None,
        components=graded,
    )


def read_backtest_log(
    log_path: str, *, force_strategy: Optional[str] = None
) -> Dict[str, List[TradeRow]]:
    """Read an emit-trades JSONL into ``{strategy_name: [TradeRow, ...]}``.

    Never raises: a missing / empty / malformed file (or any malformed line)
    yields the rows it could parse (possibly an empty dict). One bad line is
    skipped, not fatal.
    """
    grouped: Dict[str, List[TradeRow]] = {}
    p = Path(log_path)
    if not p.exists() or not p.is_file():
        return grouped
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return grouped
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        tr = _row_to_traderow(row, force_strategy=force_strategy)
        if tr is None:
            continue
        grouped.setdefault(tr.strategy, []).append(tr)
    return grouped


# ---------------------------------------------------------------------------
# Stats (pure, stdlib)
# ---------------------------------------------------------------------------


def _quantile_edges(values: Sequence[float], n_buckets: int) -> List[float]:
    """Interior quantile edges splitting *values* into n_buckets quantiles."""
    xs = sorted(values)
    if not xs or n_buckets < 2:
        return []
    edges: List[float] = []
    for i in range(1, n_buckets):
        q = i / n_buckets
        pos = q * (len(xs) - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            edges.append(xs[lo])
        else:
            frac = pos - lo
            edges.append(xs[lo] * (1 - frac) + xs[hi] * frac)
    return edges


def _bucket_index(value: float, edges: Sequence[float]) -> int:
    """Bucket index 0..len(edges) for *value* against interior *edges*."""
    idx = 0
    for e in edges:
        if value > e:
            idx += 1
        else:
            break
    return idx


def _mean(xs: Sequence[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _auc_win(values: Sequence[float], wins: Sequence[int]) -> Optional[float]:
    """AUC of *values* discriminating win (1) vs loss (0), via the rank-sum
    (Mann-Whitney U) identity. None when one class is empty. 0.5 = no
    discrimination; tie-corrected (mid-ranks)."""
    pairs = [(v, w) for v, w in zip(values, wins) if v is not None]
    pos = [v for v, w in pairs if w == 1]
    neg = [v for v, w in pairs if w == 0]
    n_pos, n_neg = len(pos), len(neg)
    if n_pos == 0 or n_neg == 0:
        return None
    # Mid-ranks over the pooled sample.
    pooled = sorted((v, w) for v, w in pairs)
    ranks: List[float] = [0.0] * len(pooled)
    i = 0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based mid-rank
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    rank_sum_pos = sum(r for r, (_, w) in zip(ranks, pooled) if w == 1)
    u = rank_sum_pos - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def _is_monotone(seq: Sequence[Optional[float]]) -> Optional[str]:
    """Return 'up' / 'down' if the non-None subsequence is monotone (allowing
    ties), else None. Needs >= 2 defined points."""
    vals = [v for v in seq if v is not None]
    if len(vals) < 2:
        return None
    up = all(b >= a for a, b in zip(vals, vals[1:]))
    down = all(b <= a for a, b in zip(vals, vals[1:]))
    if up and down:
        return None  # all equal — flat, not a monotone edge
    if up:
        return "up"
    if down:
        return "down"
    return None


def _bucketize(
    rows: Sequence[TradeRow],
    component: str,
    *,
    n_buckets: int,
    min_bucket: int,
) -> Optional[Dict[str, Any]]:
    """Bucket *rows* by *component* into quantiles; per-bucket n / win_rate /
    mean-R / expectancy. None when too few measured rows for the floor."""
    measured = [r for r in rows if component in r.components]
    values = [float(r.components[component]) for r in measured]
    if len(measured) < min_bucket * 2:
        return None  # need at least 2 buckets' worth of data

    # Shrink n_buckets so every bucket can clear the floor.
    eff_buckets = max(2, min(n_buckets, len(measured) // max(min_bucket, 1)))
    edges = _quantile_edges(values, eff_buckets)
    if not edges:
        return None

    buckets: List[List[TradeRow]] = [[] for _ in range(eff_buckets)]
    for r in measured:
        buckets[_bucket_index(float(r.components[component]), edges)].append(r)

    # Reject if any bucket fell below the floor (degenerate quantiles on heavy
    # ties); report insufficient rather than a misleading split.
    if any(len(b) < min_bucket for b in buckets):
        return None

    out_buckets: List[Dict[str, Any]] = []
    for i, b in enumerate(buckets):
        rs = [r.r for r in b if r.r is not None]
        win_rate = sum(x.win for x in b) / len(b) if b else None
        out_buckets.append(
            {
                "bucket": i,
                "n": len(b),
                "lo": min(float(x.components[component]) for x in b),
                "hi": max(float(x.components[component]) for x in b),
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "mean_r": round(_mean(rs), 4) if rs else None,
                "r_count": len(rs),
                "expectancy": round(_mean([x.pnl for x in b]), 6),
            }
        )
    return {
        "n_buckets": eff_buckets,
        "edges": [round(e, 8) for e in edges],
        "buckets": out_buckets,
        "n_measured": len(measured),
    }


def _verdict_for(
    bucketing: Optional[Dict[str, Any]],
    auc: Optional[float],
    n_measured: int,
    *,
    min_bucket: int,
) -> Tuple[str, str]:
    """Assign a verdict ∈ {edge, weak, none, insufficient} + a one-line reason.

    Honours the M18 prior: small-n / flat / non-monotone ⇒ no edge, stated
    plainly — never manufactured.
    """
    if bucketing is None or n_measured < min_bucket * 2:
        return (
            VERDICT_INSUFFICIENT,
            f"only {n_measured} R/measured rows — below the bucketing floor; "
            "no edge inference attempted (M18 prior).",
        )
    # Prefer the mean-R monotonicity; fall back to win-rate if no bucket had R.
    r_seq = [b["mean_r"] for b in bucketing["buckets"]]
    wr_seq = [b["win_rate"] for b in bucketing["buckets"]]
    mono_r = _is_monotone(r_seq)
    mono_wr = _is_monotone(wr_seq)
    mono = mono_r or mono_wr
    auc_dist = abs((auc - 0.5)) if auc is not None else 0.0

    # Edge spread across buckets (top-vs-bottom on the available R/WR axis).
    spread = _bucket_spread(r_seq) or _bucket_spread(wr_seq) or 0.0

    if mono and auc_dist >= 0.10:
        return (
            VERDICT_EDGE,
            f"monotone {mono} across buckets and AUC {round(auc, 3)} "
            f"(|0.5-AUC|={round(auc_dist, 3)}); component discriminates.",
        )
    if (mono and auc_dist >= 0.03) or (auc_dist >= 0.08 and spread > 0):
        return (
            VERDICT_WEAK,
            f"some signal (monotone={mono}, AUC={round(auc, 3) if auc is not None else None}) "
            "but below the edge bar — treat as a lead, not a result.",
        )
    return (
        VERDICT_NONE,
        f"flat / non-monotone (AUC={round(auc, 3) if auc is not None else None}); "
        "no edge detected — consistent with the M18 prior that entry-feature "
        "edge is ~coin-flip (the edge likely lives in exit/regime).",
    )


def _bucket_spread(seq: Sequence[Optional[float]]) -> Optional[float]:
    vals = [v for v in seq if v is not None]
    if len(vals) < 2:
        return None
    return max(vals) - min(vals)


# ---------------------------------------------------------------------------
# Decay (recent vs prior window)
# ---------------------------------------------------------------------------


def _parse_close_ts(raw: Any) -> Optional[datetime]:
    """Parse a closed_at value (ISO-8601, SQLite datetime, or epoch-ms string)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # epoch-ms numeric string (the reconciler close path writes Bybit updatedTime)
    if s.isdigit():
        try:
            ms = int(s)
            # Heuristic: 13-digit ⇒ ms, 10-digit ⇒ s.
            secs = ms / 1000.0 if len(s) >= 12 else float(ms)
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        # SQLite "YYYY-MM-DD HH:MM:SS"
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _decay(
    rows: Sequence[TradeRow],
    component: str,
    *,
    n_buckets: int,
    min_bucket: int,
) -> Dict[str, Any]:
    """Compare bucketed edge over the recent window vs the prior window."""
    now = datetime.now(timezone.utc)
    recent_cut = now.timestamp() - DECAY_RECENT_DAYS * 86400
    prior_cut = now.timestamp() - (DECAY_RECENT_DAYS + DECAY_PRIOR_DAYS) * 86400

    recent: List[TradeRow] = []
    prior: List[TradeRow] = []
    for r in rows:
        dt = _parse_close_ts(r.closed_at)
        if dt is None:
            continue
        ts = dt.timestamp()
        if ts >= recent_cut:
            recent.append(r)
        elif ts >= prior_cut:
            prior.append(r)

    def _window_mean_r(window: Sequence[TradeRow]) -> Optional[float]:
        rs = [r.r for r in window if r.r is not None and component in r.components]
        return round(_mean(rs), 4) if rs else None

    recent_r = _window_mean_r(recent)
    prior_r = _window_mean_r(prior)
    direction = "unknown"
    if recent_r is not None and prior_r is not None:
        if recent_r > prior_r + 1e-9:
            direction = "improving"
        elif recent_r < prior_r - 1e-9:
            direction = "fading"
        else:
            direction = "flat"
    return {
        "recent_days": DECAY_RECENT_DAYS,
        "prior_days": DECAY_PRIOR_DAYS,
        "recent_n": len(recent),
        "prior_n": len(prior),
        "recent_mean_r": recent_r,
        "prior_mean_r": prior_r,
        "direction": direction,
    }


# ---------------------------------------------------------------------------
# Marginal lift (optional, numpy-gated logistic fit)
# ---------------------------------------------------------------------------


def _marginal_lift(
    rows: Sequence[TradeRow], components: Sequence[str]
) -> Dict[str, Any]:
    """Multivariate logistic-regression coefficient per component (does it add
    edge after controlling for the others). Only when numpy is available;
    otherwise an explicit not_computed note — never a faked number."""
    if not _NUMPY_OK:
        return {
            "computed": False,
            "note": "marginal_lift: not_computed (univariate only in P0 — "
            "numpy unavailable; no multivariate fit attempted)",
        }
    usable = [r for r in rows if all(c in r.components for c in components)]
    # Need both classes + comfortably more rows than features.
    n_feat = len(components)
    if n_feat == 0 or len(usable) < max(20, 5 * n_feat):
        return {
            "computed": False,
            "note": f"marginal_lift: not_computed (only {len(usable)} complete-vector "
            f"rows for {n_feat} features — too few for a stable fit)",
        }
    wins = {r.win for r in usable}
    if wins != {0, 1}:
        return {
            "computed": False,
            "note": "marginal_lift: not_computed (only one outcome class present)",
        }
    try:
        x_raw = _np.array(
            [[float(r.components[c]) for c in components] for r in usable],
            dtype=float,
        )
        y = _np.array([float(r.win) for r in usable], dtype=float)
        # Standardise features so coefficients are comparable; guard zero-var.
        mu = x_raw.mean(axis=0)
        sd = x_raw.std(axis=0)
        sd_safe = _np.where(sd > 1e-12, sd, 1.0)
        x_std = (x_raw - mu) / sd_safe
        x = _np.column_stack([_np.ones(len(usable)), x_std])
        beta = _fit_logistic(x, y)
        if beta is None:
            return {
                "computed": False,
                "note": "marginal_lift: not_computed (fit did not converge)",
            }
        coefs = {
            comp: round(float(beta[i + 1]), 4) for i, comp in enumerate(components)
        }
        return {
            "computed": True,
            "n": len(usable),
            "standardized_coefficients": coefs,
            "intercept": round(float(beta[0]), 4),
            "note": "Standardised logistic-regression coefficients of realised win "
            "on the component vector — sign+magnitude is the marginal "
            "contribution after controlling for the other components. "
            "Hand-rolled IRLS (no sklearn/statsmodels); read as a lead, "
            "not a gate (the k-fold ladder is the gate).",
        }
    except Exception as exc:  # noqa: BLE001 — optional, never break the report
        return {
            "computed": False,
            "note": f"marginal_lift: not_computed (fit error: {exc})",
        }


def _fit_logistic(x, y, *, iters: int = 50, ridge: float = 1e-4):
    """Tiny IRLS logistic regression (numpy). Returns the coefficient vector
    or None if it diverges. Ridge term keeps the normal equations invertible
    on collinear / separable data."""
    n_feat = x.shape[1]
    beta = _np.zeros(n_feat)
    eye = _np.eye(n_feat) * ridge
    for _ in range(iters):
        eta = x @ beta
        eta = _np.clip(eta, -30, 30)
        p = 1.0 / (1.0 + _np.exp(-eta))
        w = _np.clip(p * (1 - p), 1e-6, None)
        grad = x.T @ (y - p)
        hess = x.T @ (x * w[:, None]) + eye
        try:
            step = _np.linalg.solve(hess, grad)
        except Exception:  # noqa: BLE001
            return None
        beta = beta + step
        if not _np.all(_np.isfinite(beta)):
            return None
        if _np.max(_np.abs(step)) < 1e-6:
            break
    return beta if _np.all(_np.isfinite(beta)) else None


# ---------------------------------------------------------------------------
# Per-strategy analysis
# ---------------------------------------------------------------------------


def analyze_strategy(
    strategy: str,
    rows: Sequence[TradeRow],
    *,
    n_buckets: int,
    min_bucket: int,
) -> Dict[str, Any]:
    """Full graded-component edge analysis for one strategy's closed trades."""
    n = len(rows)
    r_measured = sum(1 for r in rows if r.r is not None)
    r_coverage = round(r_measured / n, 4) if n else 0.0
    wins = sum(r.win for r in rows)

    # The graded components this strategy CAN produce (spec order), restricted
    # to those that actually appear on at least one row.
    candidate_names = graded_component_names(strategy)
    present = [c for c in candidate_names if any(c in r.components for r in rows)]

    components_out: List[Dict[str, Any]] = []
    for comp in present:
        measured = [r for r in rows if comp in r.components]
        values = [float(r.components[comp]) for r in measured]
        wins_seq = [r.win for r in measured]
        auc = _auc_win(values, wins_seq)
        bucketing = _bucketize(
            rows, comp, n_buckets=n_buckets, min_bucket=min_bucket
        )
        verdict, reason = _verdict_for(
            bucketing, auc, len(measured), min_bucket=min_bucket
        )
        components_out.append(
            {
                "component": comp,
                "kind": KIND_GRADED,
                "n_measured": len(measured),
                "auc_win": round(auc, 4) if auc is not None else None,
                "bucketing": bucketing,
                "decay": _decay(
                    rows, comp, n_buckets=n_buckets, min_bucket=min_bucket
                ),
                "verdict": verdict,
                "verdict_reason": reason,
            }
        )

    marginal = _marginal_lift(rows, present)

    insufficient = n < min_bucket * 2
    return {
        "strategy": strategy,
        "n_closed": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else None,
        "r_measured": r_measured,
        "rCoverage": r_coverage,
        "insufficient_data": insufficient,
        "graded_components": components_out,
        "marginal_lift": marginal,
        "notes": (
            "INSUFFICIENT closed-trade history for component-edge inference "
            f"(n={n} < {min_bucket * 2}); reported for completeness only."
            if insufficient
            else "Per-component verdicts honour the M18 prior — a 'none' verdict "
            "is itself a finding (edge lives in exit/regime, not this predicate)."
        ),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_md(report: Dict[str, Any]) -> str:
    s = report["strategy"]
    lines: List[str] = []
    lines.append(f"# Component-edge report — `{s}`")
    lines.append("")
    lines.append(f"_Generated {report['generated_at']} · cohort: {report['cohort']}_")
    lines.append("")
    lines.append(
        f"- closed trades: **{report['n_closed']}** · win-rate: "
        f"**{_pct(report['win_rate'])}** · R-coverage: "
        f"**{_pct(report['rCoverage'])}** ({report['r_measured']} R-measured)"
    )
    if report["insufficient_data"]:
        lines.append("")
        lines.append(
            "> **INSUFFICIENT DATA** — too few closed trades for component-edge "
            "inference. No edge is claimed (M18 prior)."
        )
    ml = report.get("marginal_lift", {})
    if ml.get("computed"):
        lines.append("")
        lines.append("**Marginal lift (standardised logit coefficients):** " + ", ".join(
            f"`{k}`={v}" for k, v in ml.get("standardized_coefficients", {}).items()
        ))
    else:
        lines.append("")
        lines.append(f"_{ml.get('note', 'marginal_lift: not_computed')}_")
    lines.append("")

    comps = report.get("graded_components", [])
    if not comps:
        lines.append("_No graded components present on this cohort's rows._")
        return "\n".join(lines) + "\n"

    for c in comps:
        lines.append(f"## `{c['component']}`  →  **{c['verdict'].upper()}**")
        lines.append("")
        lines.append(
            f"{c['verdict_reason']}  "
            f"(AUC={c['auc_win']}, n_measured={c['n_measured']})"
        )
        lines.append("")
        bk = c.get("bucketing")
        if bk:
            lines.append("| bucket | range | n | win% | mean R | expectancy |")
            lines.append("|---|---|---|---|---|---|")
            for b in bk["buckets"]:
                lines.append(
                    f"| {b['bucket']} | {round(b['lo'], 4)}…{round(b['hi'], 4)} "
                    f"| {b['n']} | {_pct(b['win_rate'])} | "
                    f"{b['mean_r'] if b['mean_r'] is not None else '—'} | "
                    f"{b['expectancy']} |"
                )
        else:
            lines.append("_Insufficient measured rows to bucket._")
        d = c.get("decay", {})
        lines.append("")
        lines.append(
            f"**Decay:** {d.get('direction', 'unknown')} — recent "
            f"{d.get('recent_days')}d mean-R {d.get('recent_mean_r')} "
            f"(n={d.get('recent_n')}) vs prior {d.get('prior_days')}d "
            f"{d.get('prior_mean_r')} (n={d.get('prior_n')})."
        )
        lines.append("")
    return "\n".join(lines) + "\n"


def _pct(v: Optional[float]) -> str:
    return f"{round(v * 100, 1)}%" if isinstance(v, (int, float)) else "—"


# ---------------------------------------------------------------------------
# Output (best-effort, never raise)
# ---------------------------------------------------------------------------


def _write_outputs(out_dir: Path, reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write per-strategy json+md and a combined index. Returns a summary dict.

    Best-effort: a write failure for one file is logged into the summary but
    never raises, so a read that succeeded always yields whatever it could.
    """
    written: List[str] = []
    errors: List[str] = []
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append(f"mkdir {out_dir}: {exc}")
        return {"written": written, "errors": errors, "dir": str(out_dir)}

    index_entries: List[Dict[str, Any]] = []
    for rep in reports:
        s = rep["strategy"]
        safe = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in s)
        json_path = out_dir / f"component_edge_{safe}.json"
        md_path = out_dir / f"component_edge_{safe}.md"
        try:
            json_path.write_text(json.dumps(rep, indent=2, default=str))
            written.append(str(json_path))
        except OSError as exc:
            errors.append(f"write {json_path}: {exc}")
        try:
            md_path.write_text(_render_md(rep))
            written.append(str(md_path))
        except OSError as exc:
            errors.append(f"write {md_path}: {exc}")
        index_entries.append(
            {
                "strategy": s,
                "n_closed": rep["n_closed"],
                "rCoverage": rep["rCoverage"],
                "insufficient_data": rep["insufficient_data"],
                "verdicts": {
                    c["component"]: c["verdict"]
                    for c in rep.get("graded_components", [])
                },
                "json": json_path.name,
                "md": md_path.name,
            }
        )

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cohort": reports[0]["cohort"] if reports else "real_money",
        "strategies": index_entries,
        "errors": errors,
    }
    index_path = out_dir / "component_edge_index.json"
    try:
        index_path.write_text(json.dumps(index, indent=2, default=str))
        written.append(str(index_path))
    except OSError as exc:
        errors.append(f"write {index_path}: {exc}")

    return {"written": written, "errors": errors, "dir": str(out_dir)}


def _out_dir() -> Path:
    """Resolve runtime_logs/signal_research/ via the canonical resolver."""
    try:
        from src.utils.paths import runtime_logs_dir

        return runtime_logs_dir() / "signal_research"
    except Exception:  # noqa: BLE001 — degrade to repo-relative
        return _REPO_ROOT / "runtime_logs" / "signal_research"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def build_reports(
    db_path: str,
    *,
    strategy: Optional[str] = None,
    include_paper: bool = False,
    n_buckets: int = DEFAULT_N_BUCKETS,
    min_bucket: int = DEFAULT_MIN_BUCKET,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build component-edge reports for all (or one) strategy and write them.

    Returns a summary envelope. NEVER raises — a missing/empty/corrupt DB or a
    strategy with zero closed trades yields a clean 'insufficient' report.
    """
    out_dir = out_dir or _out_dir()
    conn = _open_ro(db_path)
    if conn is None:
        # Missing/unreadable DB → a clean empty index, no traceback.
        summary = _write_outputs(out_dir, [])
        summary.update(
            {
                "db": db_path,
                "db_present": Path(db_path).exists(),
                "strategies_analyzed": 0,
                "note": "trade_journal.db absent or unreadable — empty report.",
            }
        )
        return summary

    try:
        cohorts: List[Tuple[str, bool]] = [("real_money", False)]
        if include_paper:
            cohorts.append(("paper", True))

        reports: List[Dict[str, Any]] = []
        for cohort_name, paper in cohorts:
            if strategy:
                targets = [strategy]
            else:
                targets = _present_strategies(conn, paper=paper)
            if not targets and not strategy:
                # No closed trades in this cohort — emit nothing for it.
                continue
            for strat in targets:
                rows = _fetch_trades(conn, paper=paper, strategy=strat)
                rep = analyze_strategy(
                    strat, rows, n_buckets=n_buckets, min_bucket=min_bucket
                )
                rep["cohort"] = cohort_name
                rep["generated_at"] = datetime.now(timezone.utc).isoformat()
                reports.append(rep)
    except Exception as exc:  # noqa: BLE001 — never raise from a read tool
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        summary = _write_outputs(out_dir, [])
        summary.update(
            {
                "db": db_path,
                "db_present": True,
                "strategies_analyzed": 0,
                "note": f"read error — empty report ({exc}).",
            }
        )
        return summary
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    summary = _write_outputs(out_dir, reports)
    summary.update(
        {
            "db": db_path,
            "db_present": True,
            "strategies_analyzed": len(reports),
            "numpy_available": _NUMPY_OK,
            "clean_trades_helpers": _CLEAN_TRADES_OK,
        }
    )
    return summary


def build_reports_from_backtest_log(
    log_path: str,
    *,
    strategy_name: Optional[str] = None,
    n_buckets: int = DEFAULT_N_BUCKETS,
    min_bucket: int = DEFAULT_MIN_BUCKET,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build component-edge reports from a BACKTEST emit-trades JSONL.

    Bypasses the DB/journal path entirely: groups the emitted trades by
    ``strategy`` (or forces ``strategy_name``), feeds each group through the
    SAME :func:`analyze_strategy` the DB path uses, and writes the same
    ``component_edge_<strategy>.{json,md}`` + ``component_edge_index.json``.
    The ``cohort`` is labelled ``"backtest"`` and the source log path + total
    trade count are recorded. rCoverage is 1.0 by construction (R comes
    straight from the emitted net_r / gross_r).

    NEVER raises — a missing / empty / malformed log yields a clean empty index
    (no per-strategy report, exit 0).
    """
    out_dir = out_dir or _out_dir()
    grouped = read_backtest_log(log_path, force_strategy=strategy_name)
    total_trades = sum(len(rows) for rows in grouped.values())

    reports: List[Dict[str, Any]] = []
    for strat in sorted(grouped):
        rows = grouped[strat]
        rep = analyze_strategy(
            strat, rows, n_buckets=n_buckets, min_bucket=min_bucket
        )
        rep["cohort"] = "backtest"
        rep["generated_at"] = datetime.now(timezone.utc).isoformat()
        rep["source"] = {
            "kind": "backtest_log",
            "log_path": str(log_path),
            "trades": len(rows),
        }
        reports.append(rep)

    summary = _write_outputs(out_dir, reports)
    summary.update(
        {
            "source": "backtest_log",
            "log_path": str(log_path),
            "log_present": Path(log_path).exists(),
            "total_trades": total_trades,
            "strategies_analyzed": len(reports),
            "cohort": "backtest",
            "numpy_available": _NUMPY_OK,
        }
    )
    return summary


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Signal-research Layer-1a graded-component edge report "
        "(read-only; writes runtime_logs/signal_research/).",
    )
    p.add_argument(
        "--strategy",
        default=None,
        help="Limit to one strategy_name (default: all present in the cohort).",
    )
    p.add_argument(
        "--db",
        default=None,
        help="Override trade_journal.db path (default: the canonical resolver).",
    )
    p.add_argument(
        "--backtest-log",
        default=None,
        help="Read a BACKTEST emit-trades JSONL instead of the journal "
        "(bypasses the DB entirely). Each row's net_r/gross_r is the R "
        "(rCoverage=1.0 by construction); cohort is labelled 'backtest'.",
    )
    p.add_argument(
        "--strategy-name",
        default=None,
        help="Force all --backtest-log rows under this strategy name "
        "(default: group by each row's 'strategy' field).",
    )
    p.add_argument(
        "--include-paper",
        action="store_true",
        help="Additionally report the paper cohort as a SEPARATE section "
        "(never blended with real money).",
    )
    p.add_argument(
        "--min-bucket",
        type=int,
        default=DEFAULT_MIN_BUCKET,
        help=f"Min trades per quantile bucket (default {DEFAULT_MIN_BUCKET}).",
    )
    p.add_argument(
        "--buckets",
        type=int,
        default=DEFAULT_N_BUCKETS,
        help=f"Quantile buckets per component (default {DEFAULT_N_BUCKETS}).",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Override output directory (default runtime_logs/signal_research/).",
    )
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else None
    if args.backtest_log:
        # Backtest-log mode takes precedence and needs no DB at all.
        summary = build_reports_from_backtest_log(
            args.backtest_log,
            strategy_name=args.strategy_name,
            n_buckets=max(2, int(args.buckets)),
            min_bucket=max(1, int(args.min_bucket)),
            out_dir=out_dir,
        )
    else:
        db_path = args.db or trade_journal_db_path()
        summary = build_reports(
            db_path,
            strategy=args.strategy,
            include_paper=args.include_paper,
            n_buckets=max(2, int(args.buckets)),
            min_bucket=max(1, int(args.min_bucket)),
            out_dir=out_dir,
        )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
