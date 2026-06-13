"""Per-model live attribution (ML go-live readiness, 2026-05-25).

Answers the question the operator needs before promoting any model past
``shadow``: *does this model's score actually agree with what happened to
the trade?* It joins the shadow-prediction audit log
(``runtime_logs/shadow_predictions.jsonl`` + the optional
``…_backfill.jsonl``) to closed trades in ``trade_journal.db`` and
computes, per model, discrimination + calibration against the realized
outcome.

This is **decision-support only** — nothing here touches the order path
or mutates the registry. It is the evidence feedstock for
``ml.promotion.gates`` (the ``live_agreement`` gate) and
``ml.promotion.stage_guard``.

The join mirrors ``src/web/api/routers/trade_scores.py`` exactly so the
CLI and the dashboard endpoint never disagree:

- a backfill record (carrying ``trade_id``) joins directly to that trade;
- a real-time record joins when its ``predicted_at_utc`` falls inside the
  trade's ``[opened_at, closed_at]`` window AND (when the record carries
  ``feature_row.symbol``) the symbol matches.

Per (model, trade) we keep the **signal-time** score — the earliest
record in the window — because that is the prediction the model would
have made at the decision point, which is what a live-influence gate
cares about.

Metrics per model (all pure-stdlib, no numpy/scipy):

- ``n`` — closed trades this model scored.
- ``win_rate`` — fraction of those trades with ``pnl > 0``.
- ``score_mean`` — mean signal-time score.
- ``auc`` — Mann-Whitney rank AUC: P(score(win) > score(loss)), ties 0.5.
  0.5 = no discrimination, >0.5 = higher score → better outcome,
  <0.5 = inverted (the model is anti-correlated with success). ``None``
  when there isn't at least one win and one loss.
- ``brier`` — only when every score looks like a probability (in
  ``[0, 1]``): mean ``(score - win)^2``. ``None`` otherwise.
- ``baseline_brier`` — the constant base-rate floor ``p*(1-p)`` where
  ``p = win_rate``; the naive number ``brier`` must beat.
- ``brier_lift`` — ``baseline_brier - brier`` (positive = the model's
  probabilities beat the constant base rate). ``None`` when ``brier`` is.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..shadow.inspector import ShadowRecord, iter_records


# ---------------------------------------------------------------------------
# Trade loading (I/O — kept separate from the pure metric logic below)
# ---------------------------------------------------------------------------


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _decode_notes_closed_at(notes: Any) -> str | None:
    if not isinstance(notes, str) or not notes:
        return None
    try:
        decoded = json.loads(notes)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    val = decoded.get("closed_at")
    return str(val) if val is not None else None


def load_closed_trades(
    db_path: Path | str,
    *,
    limit: int | None = None,
    include_demo: bool = False,
) -> list[dict[str, Any]]:
    """Load closed, non-backtest trades with their open/close windows.

    Mirrors ``trades_closed._query_closed_trades`` /
    ``trade_scores._load_trade_windows`` (same COALESCE close-time
    derivation) so attribution sees exactly the trades the dashboard
    journal shows. Best-effort: an unset (``None``) or missing DB
    returns ``[]`` — the trainer VM has no live ``trade_journal.db``,
    so ``--db`` is optional for ``promotion-readiness`` / ``stage-guard``
    and the live-attribution gates simply read as insufficient-data.
    """
    if db_path is None:
        return []
    path = Path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT t.id, t.symbol, t.pnl, t.pnl_percent, t.status,
                   t.timestamp AS opened_at, t.notes,
                   op.updated_at AS op_updated_at
            FROM trades t
            LEFT JOIN order_packages op ON op.linked_trade_id = t.id
            WHERE t.status = 'closed'
              AND COALESCE(t.is_backtest, 0) = 0
        """
        if not include_demo:
            sql += " AND COALESCE(t.is_demo, 0) = 0"
        sql += " ORDER BY datetime(COALESCE(op.updated_at, t.timestamp)) DESC"
        params: list[Any] = []
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        opened_at = _parse_iso(r["opened_at"])
        if opened_at is None:
            continue
        closed_at = _parse_iso(r["op_updated_at"] or _decode_notes_closed_at(r["notes"]))
        if closed_at is None:
            closed_at = opened_at  # degenerate window — still joinable by trade_id
        pnl = r["pnl"]
        out.append({
            "id": str(r["id"]),
            "symbol": r["symbol"],
            "pnl": float(pnl) if pnl is not None else None,
            "opened_at": opened_at,
            "closed_at": closed_at,
        })
    return out


def load_shadow_records(*paths: Path | str) -> list[ShadowRecord]:
    """Read shadow records from one or more JSONL logs (best-effort)."""
    out: list[ShadowRecord] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        try:
            out.extend(iter_records(path))
        except (OSError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# Join + metrics (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JoinedScore:
    """One (model, trade) pair: the model's signal-time score and the
    trade's realized outcome."""

    model_id: str
    stage: str
    trade_id: str
    score: float
    win: bool
    pnl: float | None


def join_scores_to_trades(
    trades: Iterable[Mapping[str, Any]],
    records: Iterable[ShadowRecord],
) -> list[JoinedScore]:
    """Join shadow records to trades, one signal-time score per
    (model, trade).

    Uses the same matching rules as the ``/api/bot/trades/scores``
    endpoint: ``trade_id`` for backfill records; window + symbol for
    real-time records. The earliest record in the window wins (the
    signal-time prediction). Trades whose ``pnl`` is ``None`` are
    skipped — there is no outcome to attribute to.
    """
    records = list(records)
    out: list[JoinedScore] = []
    for t in trades:
        pnl = t.get("pnl")
        if pnl is None:
            continue
        trade_id = str(t.get("id") or "")
        symbol = t.get("symbol")
        window_start: datetime = t["opened_at"]
        window_end: datetime = t["closed_at"]
        # earliest record per model for this trade
        best: dict[str, tuple[datetime, ShadowRecord]] = {}
        for r in records:
            if r.trade_id is not None:
                if r.trade_id != trade_id:
                    continue
            else:
                if r.predicted_at_utc < window_start or r.predicted_at_utc > window_end:
                    continue
                if r.feature_row is not None and symbol is not None:
                    rec_symbol = r.feature_row.get("symbol")
                    if rec_symbol and rec_symbol != symbol:
                        continue
            prev = best.get(r.model_id)
            if prev is None or r.predicted_at_utc < prev[0]:
                best[r.model_id] = (r.predicted_at_utc, r)
        for model_id, (_, r) in best.items():
            out.append(JoinedScore(
                model_id=model_id,
                stage=r.stage,
                trade_id=trade_id,
                score=r.score,
                win=pnl > 0,
                pnl=float(pnl),
            ))
    return out


def rank_auc(win_scores: list[float], loss_scores: list[float]) -> float | None:
    """Mann-Whitney rank AUC = P(win_score > loss_score), ties 0.5.

    ``None`` when either group is empty (no discrimination measurable).
    """
    if not win_scores or not loss_scores:
        return None
    wins = 0.0
    for ws in win_scores:
        for ls in loss_scores:
            if ws > ls:
                wins += 1.0
            elif ws == ls:
                wins += 0.5
    return wins / (len(win_scores) * len(loss_scores))


@dataclass(frozen=True)
class ModelAttribution:
    model_id: str
    stage: str
    n: int
    win_rate: float
    score_mean: float
    score_min: float
    score_max: float
    auc: float | None
    brier: float | None
    baseline_brier: float | None
    brier_lift: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "stage": self.stage,
            "n": self.n,
            "win_rate": self.win_rate,
            "score_mean": self.score_mean,
            "score_min": self.score_min,
            "score_max": self.score_max,
            "auc": self.auc,
            "brier": self.brier,
            "baseline_brier": self.baseline_brier,
            "brier_lift": self.brier_lift,
        }


def aggregate_attribution(joined: Iterable[JoinedScore]) -> list[ModelAttribution]:
    """Per-model discrimination + calibration over joined (score, outcome)
    pairs. Ordered by ``n`` desc then ``model_id`` asc for stable output."""
    by_model: dict[str, list[JoinedScore]] = {}
    for j in joined:
        by_model.setdefault(j.model_id, []).append(j)
    out: list[ModelAttribution] = []
    for model_id, rows in by_model.items():
        n = len(rows)
        scores = [r.score for r in rows]
        wins = [r for r in rows if r.win]
        losses = [r for r in rows if not r.win]
        win_rate = len(wins) / n if n else 0.0
        auc = rank_auc([r.score for r in wins], [r.score for r in losses])
        # Brier only when every score is a probability-like value in [0,1].
        if scores and all(0.0 <= s <= 1.0 for s in scores):
            brier = sum((r.score - (1.0 if r.win else 0.0)) ** 2 for r in rows) / n
            baseline_brier = win_rate * (1.0 - win_rate)
            brier_lift = baseline_brier - brier
        else:
            brier = baseline_brier = brier_lift = None
        out.append(ModelAttribution(
            model_id=model_id,
            stage=rows[0].stage,
            n=n,
            win_rate=win_rate,
            score_mean=sum(scores) / n,
            score_min=min(scores),
            score_max=max(scores),
            auc=auc,
            brier=brier,
            baseline_brier=baseline_brier,
            brier_lift=brier_lift,
        ))
    return sorted(out, key=lambda a: (-a.n, a.model_id))


def compute_attribution(
    *,
    db_path: Path | str,
    shadow_log: Path | str,
    backfill_log: Path | str | None = None,
    trade_limit: int | None = None,
    include_demo: bool = False,
) -> list[ModelAttribution]:
    """End-to-end: load trades + shadow records, join, aggregate."""
    trades = load_closed_trades(db_path, limit=trade_limit, include_demo=include_demo)
    paths = [shadow_log] + ([backfill_log] if backfill_log else [])
    records = load_shadow_records(*paths)
    joined = join_scores_to_trades(trades, records)
    return aggregate_attribution(joined)
