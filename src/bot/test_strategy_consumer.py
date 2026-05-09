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

Hardening (P2):

  * **Subprocess execution + per-run timeout.** The default runner
    spawns ``python -m src.backtest.run_backtest_m5 <strategy>`` and
    waits up to ``M5_BACKTEST_TIMEOUT_S`` seconds (default 120). A
    multi-MB CSV no longer blocks the comms poll loop, and a runaway
    backtest is bounded by a wall-clock timeout.
  * **Single-flight lock per ``request_id``.** Belt-and-suspenders on
    top of the comms state machine: the artifact transitions to
    SENT before the runner fires, but a fast in-flight set guards
    re-entry from a delayed timer or a second ``scan_and_run`` call
    in the same event-loop tick.
  * **``M5_CONSUMER_ENABLED`` env gate.** ``install_comms_handlers``
    only wires the default consumer when the env var is set; tests
    construct ``BacktestConsumer`` directly and bypass the gate.
  * **Structured error envelope.** Subprocess timeouts and non-zero
    exits surface to the operator answer + the validation log with
    truncated stderr (no full traceback in the Telegram bubble).

The consumer ignores any artifact that does not match
``test_strategy:`` — operator-initiated requests like
``new_session:<sprint>`` flow through to Claude untouched.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
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

# Env-gate flag for ``install_comms_handlers``. The default consumer
# is only installed when this evaluates true; explicit
# ``BacktestConsumer(...)`` construction bypasses the gate so tests
# can exercise the closed loop without flipping a process env var.
M5_CONSUMER_ENABLED_ENV = "M5_CONSUMER_ENABLED"

# Subprocess wall-clock cap. Mirrors the docstring; override per
# environment without redeploying via ``M5_BACKTEST_TIMEOUT_S``.
DEFAULT_BACKTEST_TIMEOUT_S = 120
_BACKTEST_TIMEOUT_ENV = "M5_BACKTEST_TIMEOUT_S"

# How much stderr we ferry to the operator on a subprocess error.
# The full text still lands in the validation log via the wrapping
# error string. Keep this short enough to render in a Telegram bubble.
_STDERR_TRUNCATE_CHARS = 800


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


class BacktestRunnerError(RuntimeError):
    """Base class for runner failures the consumer expects to handle.

    Subclasses carry a stable ``label`` so the validation log gets a
    canonical ``outcome`` instead of the raw error class name (which
    can shift if we refactor or swap the runner).
    """

    label = "error"

    def __init__(self, message: str, *, exit_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class BacktestTimeout(BacktestRunnerError):
    label = "timeout"


class BacktestSubprocessFailure(BacktestRunnerError):
    label = "subprocess_failure"


# ---------------------------------------------------------------------------
# Default backtest runner — subprocess invocation of run_backtest_m5.

def _backtest_timeout_s() -> int:
    raw = os.environ.get(_BACKTEST_TIMEOUT_ENV)
    if not raw:
        return DEFAULT_BACKTEST_TIMEOUT_S
    try:
        n = int(raw)
        return n if n > 0 else DEFAULT_BACKTEST_TIMEOUT_S
    except ValueError:
        return DEFAULT_BACKTEST_TIMEOUT_S


def _truncate(text: str, *, limit: int = _STDERR_TRUNCATE_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def default_run_backtest(strategy: str) -> BacktestRunResult:
    """Spawn ``run_backtest_m5`` and parse its JSON envelope.

    Wall-clock bounded by ``M5_BACKTEST_TIMEOUT_S`` (default 120s).
    Raises:

      * ``BacktestTimeout`` — the subprocess exceeded the timeout.
      * ``BacktestSubprocessFailure`` — the subprocess exited
        non-zero, or its stdout did not contain a parseable JSON
        envelope. Carries truncated stderr in the message.

    The consumer wraps these into the operator answer + validation
    log so the artifact reaches ANSWERED on every path.
    """
    cmd = [sys.executable, "-m", "src.backtest.run_backtest_m5", strategy]
    timeout = _backtest_timeout_s()
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell.
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BacktestTimeout(
            f"backtest exceeded {timeout}s (strategy={strategy})"
        ) from exc

    if proc.returncode != 0:
        stderr = _truncate(proc.stderr)
        raise BacktestSubprocessFailure(
            f"backtest subprocess exit={proc.returncode}: {stderr or '(empty stderr)'}",
            exit_code=proc.returncode,
        )

    # The script prints status lines on stdout and the JSON envelope
    # as the last line. Pick the last non-empty line.
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not lines:
        raise BacktestSubprocessFailure(
            "backtest subprocess produced empty stdout (no JSON envelope)"
        )
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise BacktestSubprocessFailure(
            f"backtest subprocess stdout not JSON: {exc}; tail={_truncate(lines[-1], limit=200)!r}"
        ) from exc

    summary = payload.get("summary") or {}
    db_row_id = payload.get("db_row_id")
    if not isinstance(summary, dict):
        raise BacktestSubprocessFailure(
            f"backtest subprocess JSON missing 'summary' dict (got {type(summary).__name__})"
        )
    return BacktestRunResult(
        summary=summary,
        db_row_id=int(db_row_id) if isinstance(db_row_id, int) else None,
    )


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
        # In-flight set of request_ids — guards against re-entry of
        # the same artifact across overlapping ``scan_and_run`` calls
        # (e.g. a delayed-timer callback while a backtest subprocess
        # is still running). Single-process today; the lock makes the
        # check/insert atomic across event-loop ticks and any future
        # thread-pool runner.
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()

    def scan_and_run(self, store: RequestStore) -> int:
        """Run one consumer pass; return the number of artifacts processed.

        Idempotent in the sense that ``apply_answer`` transitions the
        artifact past PENDING on completion, so a second pass within
        the same poll cycle (or a re-entrancy from a delayed timer)
        won't re-run a finished artifact. The in-flight set provides
        an additional fast guard for overlapping passes.
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
            request_id = request.request_id
            with self._lock:
                if request_id in self._in_flight:
                    logger.info(
                        "BacktestConsumer: %s already in flight; skipping",
                        request_id,
                    )
                    continue
                self._in_flight.add(request_id)
            try:
                self._run_one(store, request, strategy, apply_answer)
                processed += 1
            finally:
                with self._lock:
                    self._in_flight.discard(request_id)
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

        outcome = error.label if isinstance(error, BacktestRunnerError) else "error"
        payload: dict = {
            "event": "backtest_run",
            "request_id": request.request_id,
            "strategy": strategy,
            "outcome": outcome,
            "started_at_utc": started_at,
            "completed_at_utc": completed_at,
            "error": f"{type(error).__name__}: {error}",
        }
        if isinstance(error, BacktestRunnerError) and error.exit_code is not None:
            payload["exit_code"] = error.exit_code
        log_validation(payload, base=self._validation_log_base)


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
