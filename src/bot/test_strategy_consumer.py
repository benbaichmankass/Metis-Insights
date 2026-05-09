"""M5 — strategy-testing artifact consumer.

The Telegram bot dispatches ``/test <strategy>`` to a
``comms/requests/REQ-…-ts<strategy>.json`` artifact (M1 P1-D, see
``src/comms/templates.py::make_test_strategy_request``). This module
is the consumer half: every comms-poller cycle, scan the active
artifacts for ``task = "test_strategy:<strategy>"`` rows, run the
backtest, persist the headline metrics to ``backtest_results``, write
a formatted summary back via ``apply_answer``, and append one row to
the M5 validation log.

The consumer is deliberately bolted into ``CommsPoller.poll_once`` as
an extra pass — it owns no timer, no task, and no chat. The poller
already runs every 60 s and already brokers the request lifecycle;
adding a second loop would just duplicate plumbing.

Out of scope for P1 (tracked in P2 — hardening):

  * Subprocess execution + per-run timeout. P1 runs ``ICTBacktester``
    inline. A multi-MB CSV will block the poll loop for the duration
    of the run; for the small fixture we ship today this is < 1 s.
  * Single-flight lock per request_id. P1 runs each PENDING artifact
    exactly once per cycle; the ``apply_answer`` ANSWERED transition
    is the durable guard against a re-run.
  * ``M5_CONSUMER_ENABLED`` env gate. P1 is wired in unconditionally
    so the closed loop is exercised on every test run; P2 adds the
    gate before the VM rollout.

The consumer ignores any artifact that does not match
``test_strategy:`` — operator-initiated requests like
``new_session:<sprint>`` flow through to Claude untouched.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from src.comms import (
    Answer,
    Request,
    RequestStore,
    STATUS,
)
from src.comms.templates import TEST_STRATEGY_TASK_PREFIX
from src.utils.validation_logger import log_validation

logger = logging.getLogger(__name__)

# Default is the dispatch path's task prefix (``test_strategy:``); we
# look for ``<prefix>:<strategy>`` exactly so a future ``test_*`` task
# (e.g. ``test_data:``) doesn't accidentally fall through.
_TASK_PREFIX = f"{TEST_STRATEGY_TASK_PREFIX}:"


@dataclass
class BacktestRunResult:
    """Headline metrics returned by the backtest runner.

    ``summary`` is the dict that gets persisted to
    ``backtest_results`` (one column per key). ``db_row_id`` is the
    auto-increment id of the inserted row, surfaced to the validation
    log + the Telegram answer so the operator can pull the full row
    by id later.
    """

    summary: dict
    db_row_id: Optional[int]


# ---------------------------------------------------------------------------
# Default backtest runner — a thin wrapper around
# ``src/backtest/run_backtest.py`` that returns the result dict instead
# of writing it via the script's own sqlite3 connection.

def default_run_backtest(strategy: str) -> BacktestRunResult:
    """Run the ICT backtester against the configured CSV and persist.

    Returns the same headline-metrics dict that
    ``run_backtest.summarize`` produces. Persistence goes through
    ``Database.save_backtest_results`` so the row also lands in the
    canonical ``trade_journal.db`` table (the script's
    ``ensure_tables`` path is for the standalone CLI only).

    Raises any exception from the underlying backtester verbatim —
    the consumer wraps the call in its own try/except and converts
    failures to a structured error answer.
    """
    # Import lazily so test fixtures can stub this function without
    # paying the pandas import cost.
    from src.backtest.run_backtest import load_data, summarize
    from src.backtest.backtester import ICTBacktester
    from src.units.db.database import Database

    df, source_path = load_data()
    bt = ICTBacktester(df, {})
    trades = bt.run()
    start_date = str(df["timestamp"].iloc[0].date())
    end_date = str(df["timestamp"].iloc[-1].date())
    summary = summarize(trades, start_date, end_date, strategy)
    summary["data_source"] = str(source_path)
    # Database.save_backtest_results inserts every column it gets —
    # ``data_source`` is not on the table, so strip it before writing
    # but keep it on the in-memory summary for the validation log.
    persistable = {k: v for k, v in summary.items() if k != "data_source"}
    db_path = os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"
    db = Database(db_path=db_path)
    row_id = db.save_backtest_results(persistable)
    return BacktestRunResult(summary=summary, db_row_id=row_id)


# ---------------------------------------------------------------------------
# Consumer

class BacktestConsumer:
    """Scan + run pass over ``test_strategy:`` requests.

    ``runner`` is injected so tests can substitute a deterministic
    fake without exercising the real backtester or DB. The default
    runner reads ``BACKTEST_DATA_PATH`` / ``data/backtest_candles.csv``
    and writes to ``trade_journal.db``.
    """

    def __init__(
        self,
        *,
        runner: Optional[Callable[[str], BacktestRunResult]] = None,
        validation_log_base: Optional[Path] = None,
    ) -> None:
        self.runner = runner or default_run_backtest
        self._validation_log_base = validation_log_base

    def scan_and_run(self, store: RequestStore) -> int:
        """Run one consumer pass; return the number of artifacts processed.

        Idempotent in the sense that ``apply_answer`` transitions the
        artifact past PENDING on completion, so a second pass within
        the same poll cycle (or a re-entrancy from a delayed timer)
        won't re-run a finished artifact.
        """
        # Import here so the comms_handler module can pull this
        # consumer in without a circular import on apply_answer.
        from src.bot.comms_handler import apply_answer

        processed = 0
        for request in store.list_pending():
            if not _is_test_strategy(request):
                continue
            strategy = _strategy_from_task(request.task)
            if not strategy:
                continue
            self._run_one(store, request, strategy, apply_answer)
            processed += 1
        return processed

    def _run_one(
        self,
        store: RequestStore,
        request: Request,
        strategy: str,
        apply_answer: Callable[..., Request],
    ) -> None:
        started_at = _utcnow_iso()
        request_id = request.request_id

        # Stake claim: pending → sent. The comms state machine
        # requires this intermediate hop before we can land an
        # answer (apply_answer only legalises sent → answered). It
        # also stops the deliver pass in the same poll cycle from
        # racing this artifact to Telegram.
        try:
            store.transition(
                request,
                to_status=STATUS.SENT,
                actor="bot",
                note="m5-backtest-consumer claimed",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "BacktestConsumer: failed to claim %s; skipping this cycle",
                request_id,
            )
            return

        try:
            result = self.runner(strategy)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "BacktestConsumer: backtest failed for %s (%s)",
                request_id, strategy,
            )
            self._write_error_answer(
                store=store,
                request=request,
                strategy=strategy,
                error=exc,
                started_at=started_at,
                apply_answer=apply_answer,
            )
            return

        self._write_ok_answer(
            store=store,
            request=request,
            strategy=strategy,
            result=result,
            started_at=started_at,
            apply_answer=apply_answer,
        )

    def _write_ok_answer(
        self,
        *,
        store: RequestStore,
        request: Request,
        strategy: str,
        result: BacktestRunResult,
        started_at: str,
        apply_answer: Callable[..., Request],
    ) -> None:
        completed_at = _utcnow_iso()
        free_text = _format_summary(strategy, result)
        answer = Answer(
            question_id="results",
            answer_type="free_text",
            received_at=completed_at,
            free_text=free_text[:4000],
        )
        try:
            apply_answer(
                store=store,
                request=request,
                answer=answer,
                operator={"username": "m5-backtest-consumer"},
            )
        except Exception:  # noqa: BLE001
            # apply_answer's own push failure is already logged. Log
            # the validation row regardless so the run is auditable.
            logger.exception(
                "BacktestConsumer: apply_answer failed for %s; row was persisted",
                request.request_id,
            )

        log_validation(
            {
                "event": "backtest_run",
                "request_id": request.request_id,
                "strategy": strategy,
                "outcome": "ok",
                "started_at_utc": started_at,
                "completed_at_utc": completed_at,
                "db_row_id": result.db_row_id,
                "summary": _summary_subset(result.summary),
            },
            base=self._validation_log_base,
        )

    def _write_error_answer(
        self,
        *,
        store: RequestStore,
        request: Request,
        strategy: str,
        error: BaseException,
        started_at: str,
        apply_answer: Callable[..., Request],
    ) -> None:
        completed_at = _utcnow_iso()
        message = _format_error(strategy, error)
        answer = Answer(
            question_id="results",
            answer_type="free_text",
            received_at=completed_at,
            free_text=message[:4000],
        )
        try:
            apply_answer(
                store=store,
                request=request,
                answer=answer,
                operator={"username": "m5-backtest-consumer"},
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "BacktestConsumer: apply_answer failed during error path for %s",
                request.request_id,
            )

        log_validation(
            {
                "event": "backtest_run",
                "request_id": request.request_id,
                "strategy": strategy,
                "outcome": "error",
                "started_at_utc": started_at,
                "completed_at_utc": completed_at,
                "error": f"{type(error).__name__}: {error}",
            },
            base=self._validation_log_base,
        )


# ---------------------------------------------------------------------------
# Helpers

def _is_test_strategy(request: Request) -> bool:
    task = request.task or ""
    return task.startswith(_TASK_PREFIX) and request.status == STATUS.PENDING


def _strategy_from_task(task: Optional[str]) -> str:
    if not task or not task.startswith(_TASK_PREFIX):
        return ""
    return task[len(_TASK_PREFIX):].strip()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Headline metrics surfaced to Telegram + the validation log. We
# deliberately keep the list short — operator can pull the full row
# from ``backtest_results`` by ``db_row_id`` if they want raw data.
_SUMMARY_KEYS = (
    "total_trades",
    "winning_trades",
    "losing_trades",
    "win_rate",
    "profit_factor",
    "expectancy",
    "max_drawdown_pct",
    "sharpe_ratio",
    "total_pnl",
    "start_date",
    "end_date",
)


def _summary_subset(summary: dict) -> dict:
    return {k: summary.get(k) for k in _SUMMARY_KEYS if k in summary}


def _format_summary(strategy: str, result: BacktestRunResult) -> str:
    """Build the Telegram free-text answer.

    Plain text only — the comms handler renders without parse_mode and
    BUG-009/030/031 require dynamic content stay out of Markdown/HTML.
    """
    s = result.summary
    lines = [
        f"M5 backtest result — {strategy}",
        f"window: {s.get('start_date', '?')} .. {s.get('end_date', '?')}",
        f"trades: {s.get('total_trades', 0)} (W {s.get('winning_trades', 0)} / L {s.get('losing_trades', 0)})",
        f"win_rate: {s.get('win_rate', 0)}%",
        f"profit_factor: {s.get('profit_factor', 0)}",
        f"expectancy: {s.get('expectancy', 0)}",
        f"sharpe: {s.get('sharpe_ratio', 0)}",
        f"max_dd_pct: {s.get('max_drawdown_pct', 0)}",
        f"total_pnl: {s.get('total_pnl', 0)}",
    ]
    if result.db_row_id is not None:
        lines.append(f"db_row_id: {result.db_row_id}  (full row in backtest_results)")
    return "\n".join(lines)


def _format_error(strategy: str, error: BaseException) -> str:
    name = type(error).__name__
    msg = str(error).strip() or "(no message)"
    # Truncate long messages so the artifact stays readable; the
    # validation log keeps the full string.
    if len(msg) > 1500:
        msg = msg[:1500] + "…"
    return (
        f"M5 backtest failed — {strategy}\n"
        f"error: {name}: {msg}\n"
        "See runtime_logs/validation.jsonl for the audit row."
    )
