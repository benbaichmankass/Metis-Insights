"""Retroactive-decision backfill of shadow predictions (2026-05-19).

For every historical row in ``trade_journal.db::trades`` (open,
closed, orphaned, rejected, exchange_rejected), build the signal-
time feature row the strategy would have produced and score it
against every model currently at ``target_deployment_stage:
shadow``. Write one JSONL record per (trade, model) pair to a
backfill log file. The records carry ``backfill_kind:
"retroactive_decision"`` and the source ``trade_id`` so consumers
can:

- Distinguish them from real-time predictions
  (``backfill_kind is not None``).
- Join deterministically to the source trade
  (``trade_id`` carries through to
  ``/api/bot/trades/scores``).
- Optionally filter them out of window-over-window drift
  comparisons (the synthetic ``predicted_at_utc`` is set to
  run-time per the operator's chosen semantics, so leaving
  backfill records in a drift query would distort it).

Leakage discipline: the projection includes **only signal-time
columns** — strategy_name, symbol, direction, confidence (from
``order_packages.confidence``, set at signal-emit time),
setup_type, killzone, bias. ``pnl``, ``pnl_percent``,
``exit_price``, ``exit_reason``, ``r_multiple`` are post-decision
outcomes and never enter the feature row.

Idempotency: this writer truncates the output file before writing.
Re-running the backfill always produces a fresh snapshot — there's
no append, so a stale row can't carry over from a previous run.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..predictors.base import Predictor
from ..predictors.shadow import ShadowPredictor
from ..registry.model_registry import ModelRegistry, RegistryEntry
from .factory import (
    ShadowFactoryError,
    _load_model_state,
    _resolve_predictor_class,
    discover_shadow_stage_model_ids,
)

_LOGGER = logging.getLogger(__name__)


# Statuses that represent a genuine "the strategy emitted a signal"
# event — even if the gate rejected it, the signal-time feature row
# exists and is scorable. By contrast, the legacy "rejected_too_small"
# / "error" statuses don't reflect a strategy intent the operator
# would want scored.
_BACKFILLABLE_STATUSES = frozenset({
    "open",
    "closed",
    "orphaned",
    "rejected",
    "exchange_rejected",
})


def _iter_trade_rows(
    db_path: Path,
    *,
    include_rejected: bool,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    """Yield trade rows joined with their linked order_package row.

    ``order_packages.confidence`` is the signal-time confidence the
    strategy stamped on the package (separate from ``trades.notes``-
    encoded confidence). Best-effort: trades without a linked
    package still yield, with ``op_confidence = None``.
    """
    statuses = _BACKFILLABLE_STATUSES
    if not include_rejected:
        statuses = frozenset({"open", "closed", "orphaned"})
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in statuses)
        sql = (
            "SELECT t.id, t.symbol, t.direction, t.strategy_name, "
            "       t.setup_type, t.killzone, t.bias, t.status, "
            "       t.timestamp, op.confidence AS op_confidence "
            "FROM trades t "
            "LEFT JOIN order_packages op ON op.linked_trade_id = t.id "
            "WHERE COALESCE(t.is_backtest, 0) = 0 "
            f"AND t.status IN ({placeholders}) "
            "ORDER BY t.id ASC"
        )
        params = list(statuses)
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        for row in conn.execute(sql, params):
            yield {
                "id": int(row["id"]),
                "symbol": row["symbol"] or "",
                "direction": row["direction"] or "",
                "strategy_name": row["strategy_name"] or "",
                "setup_type": row["setup_type"] or "",
                "killzone": row["killzone"] or "",
                "bias": row["bias"] or "",
                "status": row["status"] or "",
                "timestamp": row["timestamp"] or "",
                "op_confidence": (
                    float(row["op_confidence"])
                    if row["op_confidence"] is not None
                    else None
                ),
            }
    finally:
        conn.close()


def _build_signal_feature_row(trade: dict[str, Any]) -> dict[str, Any]:
    """Project a trade row into the signal-time feature dict.

    Matches the shape of ``src/units/strategies/*.py::
    _build_shadow_feature_row`` so the same shadow models that
    score real-time signals score the backfill rows identically.
    Strategy-specific extras (ict_scalp's `sweep_depth_atr` etc.)
    aren't reconstructible from the DB alone and are omitted —
    the per-strategy `PerStrategyWinRateTrainer` models only key
    off strategy_name / setup_type so this gap is invisible to
    them.
    """
    return {
        "strategy_name": trade["strategy_name"],
        "symbol": trade["symbol"],
        "direction": trade["direction"],
        "confidence": float(trade.get("op_confidence") or 0.0),
        "setup_type": trade["setup_type"],
        "killzone": trade["killzone"],
        "bias": trade["bias"],
    }


def _load_shadow_predictors(
    registry: ModelRegistry,
) -> list[ShadowPredictor]:
    """Resolve every shadow-stage model to a non-logging ShadowPredictor.

    ``log_path=None`` because the backfill writer below emits the
    record manually with overridden ``predicted_at_utc`` /
    ``backfill_kind`` / ``trade_id`` fields. The real-time audit log
    path would emit a `datetime.now()`-stamped line with none of
    the backfill metadata, which is exactly the noise we're
    avoiding.
    """
    predictors: list[ShadowPredictor] = []
    for mid in discover_shadow_stage_model_ids(registry):
        try:
            entry = registry.get(mid)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "backfill: registry.get(%s) failed: %s — skipped", mid, exc,
            )
            continue
        try:
            inner = _instantiate_predictor(entry, registry_root=registry.root)
        except ShadowFactoryError as exc:
            _LOGGER.warning(
                "backfill: failed to instantiate %s: %s — skipped", mid, exc,
            )
            continue
        predictors.append(ShadowPredictor(
            inner,
            model_id=entry.model_id,
            stage=entry.target_deployment_stage,
            log_path=None,
        ))
    return predictors


def _instantiate_predictor(
    entry: RegistryEntry,
    *,
    registry_root: Path,
) -> Predictor:
    """Load model state + instantiate the wrapped predictor.

    Mirrors the inner half of `factory.resolve_predictor` minus the
    audit-log wrapping. Pulled into a helper so the backfill writer
    can emit its own log line shape.
    """
    state = _load_model_state(
        entry.model_state_path, registry_root=registry_root,
    )
    trainer_qualname = state.get("trainer")
    if not isinstance(trainer_qualname, str):
        raise ShadowFactoryError(
            f"model {entry.model_id!r} state has no `trainer` qualname"
        )
    predictor_cls = _resolve_predictor_class(trainer_qualname)
    return predictor_cls(state)


def run_backfill(
    *,
    db_path: Path,
    registry: ModelRegistry,
    output_path: Path,
    include_rejected: bool = True,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Score every historical trade against every shadow-stage model.

    Returns a summary dict:

        {
            "models": [<model_id>, ...],
            "trade_count": int,
            "record_count": int,    # models × trades after per-row failures
            "output_path": str,
            "skipped_trades": int,  # trades where every predictor errored
        }

    The output JSONL file is truncated before writing, so the result
    is a single coherent snapshot regardless of prior runs.
    """
    predictors = _load_shadow_predictors(registry)
    model_ids = [p.model_id for p in predictors]
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    trade_count = 0
    record_count = 0
    skipped_trades = 0
    with output_path.open("w", encoding="utf-8") as fh:
        for trade in _iter_trade_rows(
            db_path,
            include_rejected=include_rejected,
            limit=limit,
        ):
            trade_count += 1
            feature_row = _build_signal_feature_row(trade)
            wrote_for_trade = 0
            for predictor in predictors:
                try:
                    score = float(predictor._wrapped.predict(feature_row))  # noqa: SLF001
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "backfill: predict failed model=%s trade_id=%s: %s",
                        predictor.model_id, trade["id"], exc,
                    )
                    continue
                payload = {
                    "predicted_at_utc": now_iso,
                    "model_id": predictor.model_id,
                    "stage": predictor.stage,
                    "score": score,
                    "row_keys": sorted(feature_row.keys()),
                    "feature_row": dict(feature_row),
                    "backfill_kind": "retroactive_decision",
                    "trade_id": str(trade["id"]),
                }
                fh.write(json.dumps(payload) + "\n")
                wrote_for_trade += 1
                record_count += 1
            if wrote_for_trade == 0:
                skipped_trades += 1
    return {
        "models": model_ids,
        "trade_count": trade_count,
        "record_count": record_count,
        "output_path": str(output_path),
        "skipped_trades": skipped_trades,
    }
