"""M7 Strategy Review Gate — packet generator.

Reads the canonical trade journal + the regime policy + the trainer-VM
sweep mirror (when present) and emits one **review packet** per strategy:
a per-window aggregate + trend×vol regime-cell PnL slice + execution
diagnostics + a *mechanical* proposed action (`promote | hold | tune |
demote_shadow | kill`) per the gate defined in
[`docs/strategy-review-gate.md`](../../docs/strategy-review-gate.md).

The script is **Tier 1** — it reads, computes, and writes packet JSON +
a Markdown summary under `runtime_logs/strategy_reviews/<UTC-date>/`.
It NEVER mutates `config/strategies.yaml` or any live-path file. The
`proposed_action` is a proposal; the operator decides whether to ship a
Tier-3 PR that flips the YAML.

CLI:

    python -m scripts.ml.strategy_review_packet \\
        --strategy vwap \\
        --window-days 7 \\
        [--db-path /data/bot-data/trade_journal.db] \\
        [--out-dir runtime_logs/strategy_reviews] \\
        [--all-btc-strategies]

`--all-btc-strategies` iterates over every strategy whose YAML
`symbols:` contains `BTCUSDT` (the MES question — delayed CME data — is
operator-tracked separately; see `docs/audits/strategy-loss-drivers-2026-05-23.md`).

The packet contract is documented in
[`docs/strategy-review-gate.md`](../../docs/strategy-review-gate.md) §
"The review packet" — this script is the canonical generator.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from src.utils.paths import runtime_logs_dir, trade_journal_db_path  # noqa: E402

_STRATEGIES_YAML = _REPO_ROOT / "config" / "strategies.yaml"
_REGIME_POLICY_YAML = _REPO_ROOT / "config" / "regime_policy.yaml"

SCHEMA_VERSION = 1
GENERATOR_NAME = "scripts/ml/strategy_review_packet.py v1.0"


# ---------------------------------------------------------------------------
# Data classes — the wire shape mirrors docs/strategy-review-gate.md.
# ---------------------------------------------------------------------------


@dataclass
class Headline:
    n_decisions: int = 0
    n_filled: int = 0
    n_closed: int = 0
    n_wins: int = 0
    win_rate: Optional[float] = None
    pnl_total: float = 0.0
    expectancy: Optional[float] = None
    max_drawdown: float = 0.0
    avg_hold_seconds: Optional[int] = None
    fill_rate: Optional[float] = None
    rejection_cluster: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_decisions": self.n_decisions,
            "n_filled": self.n_filled,
            "n_closed": self.n_closed,
            "n_wins": self.n_wins,
            "win_rate": self.win_rate,
            "pnl_total": round(self.pnl_total, 4),
            "expectancy": self.expectancy,
            "max_drawdown": round(self.max_drawdown, 4),
            "avg_hold_seconds": self.avg_hold_seconds,
            "fill_rate": self.fill_rate,
            "rejection_cluster": self.rejection_cluster,
        }


@dataclass
class RegimeCell:
    trend: str
    vol: str
    n_decisions: int = 0
    n_closed: int = 0
    n_wins: int = 0
    win_rate: Optional[float] = None
    pnl_total: float = 0.0
    expectancy: Optional[float] = None
    regime_policy_cell: str = "unknown"  # one of {"on","off","unknown"}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cell": {"trend": self.trend, "vol": self.vol},
            "n_decisions": self.n_decisions,
            "n_closed": self.n_closed,
            "n_wins": self.n_wins,
            "win_rate": self.win_rate,
            "pnl_total": round(self.pnl_total, 4),
            "expectancy": self.expectancy,
            "regime_policy_cell": self.regime_policy_cell,
        }


@dataclass
class ExecutionDiagnostics:
    entry_slippage_bps: Optional[float] = None
    fill_rate: Optional[float] = None
    dispatch_latency_seconds: Optional[float] = None
    confidence_distribution: Dict[str, Optional[float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_slippage_bps": self.entry_slippage_bps,
            "fill_rate": self.fill_rate,
            "dispatch_latency_seconds": self.dispatch_latency_seconds,
            "confidence_distribution": self.confidence_distribution,
        }


# ---------------------------------------------------------------------------
# Strategy / regime-policy YAML loaders.
# ---------------------------------------------------------------------------


def load_strategies_yaml(path: Path = _STRATEGIES_YAML) -> Dict[str, Dict[str, Any]]:
    """Return the per-strategy raw config block keyed by name.

    Tolerates a missing file (test environments / pre-deploy lint).
    """
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    out = raw.get("strategies") or raw
    return {k: dict(v or {}) for k, v in out.items() if isinstance(v, dict)}


def load_regime_policy(path: Path = _REGIME_POLICY_YAML) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Return regime_policy.yaml's 1-D trend × strategy × direction table.

    Output shape (regime → strategy → {long, short}):

        {
          "trending":     {"vwap": {"long": "off", "short": "off"}, ...},
          "transitional": {...},
          "chop":         {...},
        }

    Cells not present default to "on" (the policy is permissive by
    design). The 2-D `trend_vol` block is intentionally not consumed by
    this gate — the matrix in `docs/strategy-review-gate.md` is keyed on
    the 1-D trend axis only for now; once `trend_vol` cells are
    authored a follow-up sprint will extend the gate.
    """
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for trend in ("trending", "transitional", "chop"):
        block = raw.get(trend) or {}
        if not isinstance(block, dict):
            continue
        out[trend] = {}
        for strategy, cell in block.items():
            if not isinstance(cell, dict):
                continue
            out[trend][strategy] = {
                "long": str(cell.get("long", "on")).lower(),
                "short": str(cell.get("short", "on")).lower(),
            }
    return out


def regime_policy_cell_for(
    policy: Mapping[str, Mapping[str, Mapping[str, str]]],
    strategy: str,
    trend: str,
    long_short_hint: Optional[str] = None,
) -> str:
    """Return "on" / "off" / "unknown" for (strategy, trend) per regime_policy.

    The matrix in regime_policy.yaml is keyed by direction; this helper
    rolls long+short up: if BOTH directions are "off" return "off",
    if BOTH are "on" return "on", else return "unknown" (mixed). If the
    trend / strategy is absent → "unknown" (default-permissive, matches
    the YAML).
    """
    block = policy.get(trend) or {}
    cell = block.get(strategy)
    if not cell:
        return "unknown"
    if long_short_hint and long_short_hint in cell:
        return cell[long_short_hint]
    long_v = cell.get("long", "on")
    short_v = cell.get("short", "on")
    if long_v == short_v:
        return long_v
    return "unknown"


# ---------------------------------------------------------------------------
# SQL pulls — narrow, time-bounded, read-only.
# ---------------------------------------------------------------------------


def _ro_conn(db_path: str) -> sqlite3.Connection:
    """Read-only connection — refuses any write by URI mode=ro."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def pull_decisions(
    db_path: str,
    strategy: str,
    window_start: datetime,
    window_end: datetime,
) -> List[Dict[str, Any]]:
    """Pull every order package + linked trade for *strategy* in the window.

    Returns a list of plain dicts merging `order_packages` (the decision
    row) with the linked `trades` row when present. PnL is `NULL` for
    open or never-filled packages.

    Window is on the package `created_at` field — the moment the
    strategy DECIDED. A package created in-window whose linked trade
    closes after `window_end` is still included with `n_closed = 0`
    contribution.
    """
    with _ro_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              op.order_package_id, op.strategy_name, op.symbol, op.direction,
              op.entry, op.sl, op.tp, op.confidence,
              op.created_at AS pkg_created_at,
              op.updated_at AS pkg_updated_at,
              op.status     AS pkg_status,
              op.close_reason AS pkg_close_reason,
              op.signal_logic,
              op.meta       AS pkg_meta,
              op.linked_trade_id,
              t.id          AS trade_id,
              t.timestamp   AS trade_ts,
              t.entry_price AS trade_entry_price,
              t.exit_price  AS trade_exit_price,
              t.pnl         AS trade_pnl,
              t.status      AS trade_status,
              t.exit_reason AS trade_exit_reason,
              t.is_backtest AS trade_is_backtest
            FROM order_packages AS op
            LEFT JOIN trades AS t ON t.id = op.linked_trade_id
            WHERE op.strategy_name = ?
              AND op.created_at >= ?
              AND op.created_at <  ?
            ORDER BY datetime(op.created_at) ASC
            """,
            (strategy, window_start.isoformat(), window_end.isoformat()),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        # Backtest rows poison aggregates the same way they do in /stats —
        # filter them out at source (the trader writes is_backtest=0 on
        # live; the M5 backtest consumer writes is_backtest=1).
        if rec.get("trade_is_backtest") == 1:
            continue
        out.append(rec)
    return out


def pull_regime_stamp_index(
    db_path: str,
    strategy: str,
    window_start: datetime,
    window_end: datetime,
) -> Dict[str, Dict[str, Optional[str]]]:
    """Return {order_package_id: {"regime": ..., "vol_regime": ...}}.

    The regime/vol stamp lives in the `signals.meta` JSON dual-write.
    Pre-S-MLOPT-S15b rows lack `vol_regime`; pre-PERF-20260601-006 rows
    lack `regime`. Both → None in the returned dict (the cell falls
    through to `vol = "unknown"`).
    """
    out: Dict[str, Dict[str, Optional[str]]] = {}
    try:
        with _ro_conn(db_path) as conn:
            # The signals table may not exist on very-early DBs.
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "signals" not in tables:
                return out
            rows = conn.execute(
                """
                SELECT meta FROM signals
                WHERE strategy = ?
                  AND logged_at_utc >= ?
                  AND logged_at_utc <  ?
                """,
                (strategy, window_start.isoformat(), window_end.isoformat()),
            ).fetchall()
    except sqlite3.DatabaseError:
        return out
    for row in rows:
        meta_raw = row["meta"]
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        opid = meta.get("order_package_id")
        if not opid:
            continue
        out[str(opid)] = {
            "regime": (meta.get("regime") or None),
            "vol_regime": (meta.get("vol_regime") or None),
        }
    return out


# ---------------------------------------------------------------------------
# Aggregation.
# ---------------------------------------------------------------------------


def _safe_div(a: float, b: float) -> Optional[float]:
    return (a / b) if b else None


def _isoparse(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Tolerate trailing Z and tz-naive timestamps.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _is_closed(row: Mapping[str, Any]) -> bool:
    tstatus = (row.get("trade_status") or "").lower()
    if tstatus.startswith("closed"):
        return True
    pstatus = (row.get("pkg_status") or "").lower()
    return pstatus == "closed"


def compute_headline(decisions: Sequence[Mapping[str, Any]]) -> Headline:
    h = Headline()
    h.n_decisions = len(decisions)
    pnls: List[float] = []
    hold_seconds: List[int] = []
    reject_counter: Dict[str, int] = {}

    for row in decisions:
        if row.get("linked_trade_id"):
            h.n_filled += 1
        if _is_closed(row):
            h.n_closed += 1
            pnl = row.get("trade_pnl")
            if pnl is not None:
                pnls.append(float(pnl))
                if float(pnl) > 0:
                    h.n_wins += 1
            opened = _isoparse(row.get("pkg_created_at"))
            closed = _isoparse(row.get("pkg_updated_at"))
            if opened and closed:
                hold_seconds.append(int((closed - opened).total_seconds()))
        pstatus = (row.get("pkg_status") or "").lower()
        if pstatus.startswith("failed"):
            reason = (row.get("pkg_close_reason") or pstatus) or "unknown"
            reject_counter[reason] = reject_counter.get(reason, 0) + 1

    h.win_rate = _safe_div(h.n_wins, h.n_closed)
    h.pnl_total = float(sum(pnls)) if pnls else 0.0
    h.expectancy = _safe_div(h.pnl_total, h.n_closed)
    h.fill_rate = _safe_div(h.n_filled, h.n_decisions)
    h.avg_hold_seconds = (
        int(sum(hold_seconds) / len(hold_seconds)) if hold_seconds else None
    )
    if pnls:
        peak = 0.0
        cum = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = min(max_dd, cum - peak)
        h.max_drawdown = max_dd
    if reject_counter:
        h.rejection_cluster = max(reject_counter.items(), key=lambda kv: kv[1])[0]
    return h


_TREND_VALUES = ("trending", "transitional", "chop", "unknown")
_VOL_VALUES = ("calm", "volatile", "unknown")


def compute_regime_cells(
    decisions: Sequence[Mapping[str, Any]],
    regime_index: Mapping[str, Mapping[str, Optional[str]]],
    policy: Mapping[str, Mapping[str, Mapping[str, str]]],
    strategy: str,
) -> List[RegimeCell]:
    """Group decisions by (trend, vol) and compute the per-cell aggregate.

    Cells are emitted only when `n_decisions ≥ 1`. The `regime_policy_cell`
    is the verdict the regime router would return for the cell —
    matched against `regime_policy.yaml` § 1-D trend axis.
    """
    bucket: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for row in decisions:
        stamp = regime_index.get(str(row.get("order_package_id")), {})
        trend = (stamp.get("regime") or "unknown").lower()
        vol = (stamp.get("vol_regime") or "unknown").lower()
        if trend not in _TREND_VALUES:
            trend = "unknown"
        if vol not in _VOL_VALUES:
            vol = "unknown"
        bucket.setdefault((trend, vol), []).append(row)

    out: List[RegimeCell] = []
    for (trend, vol), rows in sorted(bucket.items()):
        cell = RegimeCell(trend=trend, vol=vol)
        cell.n_decisions = len(rows)
        cell.regime_policy_cell = regime_policy_cell_for(policy, strategy, trend)
        pnls: List[float] = []
        for r in rows:
            if _is_closed(r):
                cell.n_closed += 1
                pnl = r.get("trade_pnl")
                if pnl is not None:
                    pnls.append(float(pnl))
                    if float(pnl) > 0:
                        cell.n_wins += 1
        cell.win_rate = _safe_div(cell.n_wins, cell.n_closed)
        cell.pnl_total = float(sum(pnls)) if pnls else 0.0
        cell.expectancy = _safe_div(cell.pnl_total, cell.n_closed)
        out.append(cell)
    return out


def compute_execution_diagnostics(
    decisions: Sequence[Mapping[str, Any]],
) -> ExecutionDiagnostics:
    d = ExecutionDiagnostics()
    slippage_bps: List[float] = []
    latency_seconds: List[float] = []
    confidences: List[float] = []
    n_filled = 0

    for row in decisions:
        conf = row.get("confidence")
        if conf is not None:
            try:
                confidences.append(float(conf))
            except (TypeError, ValueError):
                pass
        if not row.get("linked_trade_id"):
            continue
        n_filled += 1
        pkg_entry = row.get("entry")
        trade_entry = row.get("trade_entry_price")
        if pkg_entry and trade_entry:
            try:
                pe = float(pkg_entry)
                te = float(trade_entry)
                if pe > 0:
                    bps = (te - pe) / pe * 10_000.0
                    # Signed slippage matters: positive = filled worse for longs;
                    # for shorts the sign is inverted at the strategy level, but
                    # the dashboard distinction is "absolute slippage drift", so
                    # mirror sign by direction.
                    direction = (row.get("direction") or "").lower()
                    if direction == "short":
                        bps = -bps
                    slippage_bps.append(bps)
            except (TypeError, ValueError):
                pass
        pkg_created = _isoparse(row.get("pkg_created_at"))
        trade_ts = _isoparse(row.get("trade_ts"))
        if pkg_created and trade_ts:
            latency_seconds.append((trade_ts - pkg_created).total_seconds())

    d.fill_rate = _safe_div(n_filled, len(decisions))
    d.entry_slippage_bps = (
        round(sum(slippage_bps) / len(slippage_bps), 4) if slippage_bps else None
    )
    d.dispatch_latency_seconds = (
        round(sum(latency_seconds) / len(latency_seconds), 3)
        if latency_seconds
        else None
    )
    if confidences:
        confidences.sort()
        n = len(confidences)
        median = confidences[n // 2] if n % 2 else (
            confidences[n // 2 - 1] + confidences[n // 2]
        ) / 2
        mean = sum(confidences) / n
        var = sum((c - mean) ** 2 for c in confidences) / n
        d.confidence_distribution = {
            "min": min(confidences),
            "max": max(confidences),
            "p50": median,
            "std": var**0.5,
            "n": n,
        }
    else:
        d.confidence_distribution = {
            "min": None,
            "max": None,
            "p50": None,
            "std": None,
            "n": 0,
        }
    return d


# ---------------------------------------------------------------------------
# The decision matrix.
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    action: str  # "promote" | "hold" | "tune" | "demote_shadow" | "kill"
    reasons: List[str] = field(default_factory=list)
    alternative: str = "none"
    tier: int = 3


def _all_cells_off(cells: Sequence[RegimeCell]) -> bool:
    """True if every cell with n_decisions >= 1 has policy = off."""
    cells_with_n = [c for c in cells if c.n_decisions >= 1]
    if not cells_with_n:
        return False
    return all(c.regime_policy_cell == "off" for c in cells_with_n)


def _any_policy_off_cell_present(cells: Sequence[RegimeCell]) -> bool:
    return any(c.regime_policy_cell == "off" and c.n_decisions >= 1 for c in cells)


def _degenerate_confidence(diag: ExecutionDiagnostics) -> bool:
    cdist = diag.confidence_distribution or {}
    std = cdist.get("std")
    cmin = cdist.get("min")
    cmax = cdist.get("max")
    if std is None or cmin is None or cmax is None:
        return False
    return std == 0.0 and cmin == cmax == 1.0


def decide(
    headline: Headline,
    cells: Sequence[RegimeCell],
    diag: ExecutionDiagnostics,
    execution: str,
    shadow_soak_days: int,
) -> Decision:
    """Mechanical gate per `docs/strategy-review-gate.md` § Threshold table.

    The matrix is intentionally ordered to short-circuit on the strongest
    catastrophic signal first. Overrides (§ Overrides in the doc) fire
    BEFORE the matrix.
    """
    decision = Decision(action="hold")
    n = headline.n_closed
    win = headline.win_rate or 0.0
    exp = headline.expectancy
    exp_neg = (exp is not None) and (exp < 0.0)
    all_off = _all_cells_off(cells)
    any_off = _any_policy_off_cell_present(cells)
    degenerate_conf = _degenerate_confidence(diag)

    # --- Override 1: execution-mode mismatch (shadow but has fills).
    if execution == "shadow" and headline.n_filled > 0:
        decision.action = "hold"
        decision.reasons.append(
            "execution_mode_mismatch: strategy is shadow but n_filled>0 — pipeline anomaly; do not act."
        )
        return decision

    # --- The matrix.
    if n == 0:
        decision.action = "hold"
        decision.reasons.append("no closed trades in window — insufficient evidence.")
    elif n < 30:
        if win <= 0.10 and exp_neg and any_off:
            decision.action = "kill"
            decision.reasons.append(
                f"catastrophic at low n (win_rate={win:.1%}, expectancy<0) AND policy-OFF cell present in slice."
            )
        elif win <= 0.25 and exp_neg and all_off:
            decision.action = "kill"
            decision.reasons.append(
                f"win_rate={win:.1%}, expectancy<0, every regime cell is policy-OFF — no edge anywhere."
            )
        elif win <= 0.25 and exp_neg:
            decision.action = "demote_shadow"
            decision.reasons.append(
                f"catastrophic at low n (win_rate={win:.1%}, expectancy<0) but mixed regime cells — demote, do not kill yet."
            )
        else:
            decision.action = "hold"
            decision.reasons.append(
                "low n with non-catastrophic stats — not enough evidence to act."
            )
    elif n < 100:
        if win <= 0.30 and exp_neg and all_off:
            decision.action = "kill"
            decision.reasons.append(
                f"mid-n win_rate={win:.1%}, expectancy<0, every regime cell policy-OFF."
            )
        elif win < 0.40 and exp_neg:
            decision.action = "demote_shadow"
            decision.reasons.append(
                f"mid-n win_rate={win:.1%}, expectancy<0 — demote to shadow pending tune."
            )
        elif 0.40 <= win < 0.50 or (exp is not None and abs(exp) < 1e-6):
            decision.action = "tune"
            decision.reasons.append(
                f"mid-n win_rate={win:.1%}, expectancy~0 — point at M8 parameter search."
            )
        elif win >= 0.50 and exp is not None and exp > 0:
            decision.action = "hold"
            decision.reasons.append(
                f"mid-n win_rate={win:.1%}, expectancy>0 — let it season to n>=100."
            )
        else:
            decision.action = "hold"
            decision.reasons.append("mid-n stats not actionable.")
    else:  # n >= 100
        if win < 0.40 and exp_neg and all_off:
            decision.action = "kill"
            decision.reasons.append(
                f"large-n win_rate={win:.1%}, expectancy<0, every cell policy-OFF."
            )
        elif win < 0.40 and exp_neg:
            decision.action = "demote_shadow"
            decision.reasons.append(
                f"large-n win_rate={win:.1%}, expectancy<0 — demote pending tune."
            )
        elif 0.40 <= win <= 0.55 and exp is not None and abs(exp) < (
            abs(headline.pnl_total) / max(1, n) * 0.10
        ):
            decision.action = "tune"
            decision.reasons.append(
                f"large-n win_rate={win:.1%}, expectancy thin — tune."
            )
        elif (
            win > 0.55
            and exp is not None
            and exp > 0
            and headline.max_drawdown >= -3.0 * exp
        ):
            decision.action = "promote"
            decision.reasons.append(
                f"large-n win_rate={win:.1%}, expectancy>0, max_dd within 3× expectancy."
            )
        else:
            decision.action = "hold"
            decision.reasons.append("large-n stats not actionable.")

    # --- Override 2: degenerate confidence — tune (preferred over kill unless
    #     also catastrophic). The matrix may have produced `kill` already; we
    #     only soften kill→tune when n is small (variance can't be ruled out).
    if degenerate_conf:
        decision.reasons.append(
            "degenerate confidence (std=0, max=min=1.0) — PERF-20260601-010 pattern."
        )
        if decision.action == "kill" and n < 30:
            decision.action = "tune"
            decision.reasons.append(
                "softened kill→tune: low n + degenerate confidence — fix the confidence first."
            )

    # --- Override 3: already at terminal state.
    if decision.action == "demote_shadow" and execution == "shadow":
        if all_off:
            decision.action = "kill"
            decision.reasons.append(
                "already at shadow + still losing AND every cell policy-OFF — escalate to kill."
            )
        else:
            decision.action = "hold"
            decision.reasons.append(
                "already at shadow — record continued loss, no further demotion available."
            )

    # --- Override 4: promote requires cohort time.
    if decision.action == "promote" and shadow_soak_days < 14:
        decision.action = "hold"
        decision.reasons.append(
            f"promote requires >=14 days shadow soak; current = {shadow_soak_days}d."
        )

    if decision.action == "hold":
        decision.tier = 1  # observation-only

    return decision


# ---------------------------------------------------------------------------
# Markdown summary — what gets embedded in the PR body.
# ---------------------------------------------------------------------------


def render_markdown(packet: Dict[str, Any]) -> str:
    """Render the packet as a compact Markdown summary suitable for a PR body."""
    h = packet["headline"]
    diag = packet["execution_diagnostics"]
    decision = packet["proposed_action"]
    cells = packet["regime_cells"]

    lines: List[str] = []
    lines.append(f"# Strategy Review Packet — `{packet['strategy']}`")
    lines.append("")
    lines.append(
        f"**Window:** `{packet['window_start']}` → `{packet['window_end']}`  "
    )
    lines.append(
        f"**Current state:** execution=`{packet['execution']}`  enabled=`{packet['enabled']}`  "
    )
    lines.append(f"**Proposed action:** **`{decision}`**  (tier {packet['tier']})")
    if packet.get("sla_due_by"):
        lines.append(f"**Tier-3 SLA due by:** `{packet['sla_due_by']}`")
    lines.append("")
    lines.append("## Reasons")
    for r in packet["reasons"]:
        lines.append(f"- {r}")
    if packet.get("alternative") and packet["alternative"] != "none":
        lines.append("")
        lines.append(f"**Alternative:** {packet['alternative']}")
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| n_decisions | {h['n_decisions']} |")
    lines.append(f"| n_filled | {h['n_filled']} |")
    lines.append(f"| n_closed | {h['n_closed']} |")
    lines.append(f"| n_wins | {h['n_wins']} |")
    lines.append(
        "| win_rate | "
        + (f"{h['win_rate']:.1%}" if h["win_rate"] is not None else "—")
        + " |"
    )
    lines.append(f"| pnl_total | {h['pnl_total']:.2f} |")
    lines.append(
        "| expectancy | "
        + (f"{h['expectancy']:.4f}" if h["expectancy"] is not None else "—")
        + " |"
    )
    lines.append(f"| max_drawdown | {h['max_drawdown']:.2f} |")
    lines.append(
        "| fill_rate | "
        + (f"{h['fill_rate']:.1%}" if h["fill_rate"] is not None else "—")
        + " |"
    )
    if h.get("rejection_cluster"):
        lines.append(f"| rejection_cluster | `{h['rejection_cluster']}` |")
    lines.append("")
    lines.append("## Regime cells (trend × vol)")
    if not cells:
        lines.append("")
        lines.append("_no decisions in window_")
    else:
        lines.append("")
        lines.append(
            "| trend | vol | n | closed | wins | win% | PnL | policy |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for c in cells:
            wr = c["win_rate"]
            lines.append(
                "| "
                + c["cell"]["trend"]
                + " | "
                + c["cell"]["vol"]
                + " | "
                + str(c["n_decisions"])
                + " | "
                + str(c["n_closed"])
                + " | "
                + str(c["n_wins"])
                + " | "
                + (f"{wr:.1%}" if wr is not None else "—")
                + " | "
                + f"{c['pnl_total']:.2f}"
                + " | "
                + f"`{c['regime_policy_cell']}`"
                + " |"
            )
    lines.append("")
    lines.append("## Execution diagnostics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(
        "| entry_slippage_bps | "
        + (
            f"{diag['entry_slippage_bps']:.2f}"
            if diag.get("entry_slippage_bps") is not None
            else "—"
        )
        + " |"
    )
    lines.append(
        "| dispatch_latency_seconds | "
        + (
            f"{diag['dispatch_latency_seconds']:.3f}"
            if diag.get("dispatch_latency_seconds") is not None
            else "—"
        )
        + " |"
    )
    cdist = diag.get("confidence_distribution") or {}
    if cdist.get("n", 0) > 0:
        lines.append(
            "| confidence (min/p50/max/std) | "
            + f"{cdist['min']:.3f} / {cdist['p50']:.3f} / {cdist['max']:.3f} / {cdist['std']:.3f}"
            + " |"
        )
    else:
        lines.append("| confidence | _no decisions_ |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "_Generated by_ `"
        + packet["generated_by"]
        + "` _at_ `"
        + packet["generated_at"]
        + "`."
    )
    lines.append(
        "_Gate: [`docs/strategy-review-gate.md`](../../docs/strategy-review-gate.md)._"
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Top-level packet builder.
# ---------------------------------------------------------------------------


def build_packet(
    strategy: str,
    db_path: str,
    window_start: datetime,
    window_end: datetime,
    strategies_cfg: Optional[Mapping[str, Mapping[str, Any]]] = None,
    regime_policy: Optional[Mapping[str, Mapping[str, Mapping[str, str]]]] = None,
    shadow_soak_days: int = 0,
) -> Dict[str, Any]:
    """Build a packet dict for *strategy* over the given window.

    *db_path* is the trade-journal SQLite path; *window_start/_end* are
    timezone-aware UTC datetimes. Returns the packet as a plain dict
    ready to be JSON-serialised; the Markdown twin is `render_markdown`.
    """
    strategies_cfg = strategies_cfg if strategies_cfg is not None else load_strategies_yaml()
    regime_policy = regime_policy if regime_policy is not None else load_regime_policy()

    cfg = dict(strategies_cfg.get(strategy) or {})
    execution = str(cfg.get("execution", "live")).lower()
    enabled = bool(cfg.get("enabled", True))

    decisions = pull_decisions(db_path, strategy, window_start, window_end)
    regime_index = pull_regime_stamp_index(db_path, strategy, window_start, window_end)

    headline = compute_headline(decisions)
    cells = compute_regime_cells(decisions, regime_index, regime_policy, strategy)
    diag = compute_execution_diagnostics(decisions)

    decision = decide(headline, cells, diag, execution, shadow_soak_days)

    now = datetime.now(timezone.utc).isoformat()
    sla_due = None
    if decision.action in ("demote_shadow", "kill"):
        sla_due = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    packet = {
        "schema_version": SCHEMA_VERSION,
        "strategy": strategy,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "execution": execution,
        "enabled": enabled,
        "headline": headline.to_dict(),
        "regime_cells": [c.to_dict() for c in cells],
        "execution_diagnostics": diag.to_dict(),
        "backtest_anchor": None,  # follow-up: wire the trainer-mirror lookup
        "proposed_action": decision.action,
        "reasons": decision.reasons,
        "alternative": decision.alternative,
        "tier": decision.tier,
        "sla_due_by": sla_due,
        "generated_at": now,
        "generated_by": GENERATOR_NAME,
    }
    return packet


def write_packet(
    packet: Dict[str, Any], out_dir: Path
) -> Tuple[Path, Path]:
    """Write packet JSON + Markdown to out_dir/<UTC-date>/<strategy>.{json,md}."""
    utc_date = packet["generated_at"][:10]  # YYYY-MM-DD
    day_dir = Path(out_dir) / utc_date
    day_dir.mkdir(parents=True, exist_ok=True)
    json_path = day_dir / f"{packet['strategy']}.json"
    md_path = day_dir / f"{packet['strategy']}.md"
    json_path.write_text(json.dumps(packet, indent=2, sort_keys=False))
    md_path.write_text(render_markdown(packet))
    return json_path, md_path


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _parse_window(window_days: int) -> Tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=window_days)
    return start, end


def _strategies_for_btc(cfg: Mapping[str, Mapping[str, Any]]) -> List[str]:
    out: List[str] = []
    for name, block in cfg.items():
        symbols = block.get("symbols") or []
        if isinstance(symbols, list) and "BTCUSDT" in symbols:
            out.append(name)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Generate M7 strategy review packets.")
    p.add_argument("--strategy", action="append", default=[], help="strategy name (repeatable)")
    p.add_argument(
        "--all-btc-strategies",
        action="store_true",
        help="iterate every strategy whose symbols include BTCUSDT (excludes MES).",
    )
    p.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="review window length in days (default 7).",
    )
    p.add_argument(
        "--db-path",
        default=None,
        help="trade_journal.db path (default: src.utils.paths.trade_journal_db_path()).",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="root for per-day packet output (default runtime_logs/strategy_reviews).",
    )
    p.add_argument(
        "--shadow-soak-days",
        type=int,
        default=0,
        help="how long this strategy has been at execution: shadow (for promote gate).",
    )
    args = p.parse_args(argv)

    db_path = args.db_path or trade_journal_db_path()
    out_dir = Path(args.out_dir) if args.out_dir else runtime_logs_dir() / "strategy_reviews"
    cfg = load_strategies_yaml()
    policy = load_regime_policy()

    targets: List[str] = list(args.strategy)
    if args.all_btc_strategies:
        targets = sorted(set(targets) | set(_strategies_for_btc(cfg)))
    if not targets:
        p.error("provide --strategy NAME or --all-btc-strategies")

    window_start, window_end = _parse_window(args.window_days)

    for strategy in targets:
        packet = build_packet(
            strategy=strategy,
            db_path=db_path,
            window_start=window_start,
            window_end=window_end,
            strategies_cfg=cfg,
            regime_policy=policy,
            shadow_soak_days=args.shadow_soak_days,
        )
        json_path, md_path = write_packet(packet, out_dir)
        print(f"[{strategy}] action={packet['proposed_action']:<14} -> {md_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
