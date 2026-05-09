"""M5 — Strategy testing workflow tests.

Pins the closed loop introduced in M5 P1:

  * ``BacktestConsumer.scan_and_run`` — happy path, error path, and
    skip behaviour for non-``test_strategy:`` artifacts.
  * ``apply_answer`` driving the artifact from PENDING to ANSWERED
    when the consumer succeeds; same path on the error branch (so
    the artifact never strands in PENDING).
  * Validation log NDJSON shape — keys present, outcome correct,
    db_row_id surfaced on the ok branch.
  * Telegram free-text answer is plain text and contains the
    db_row_id pointer.

The dispatch-side registry check on ``cmd_test_strategy`` lives in
``tests/test_m5_dispatch_validation.py`` so the heavy telegram stubs
stay out of this file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub telegram + telegram.ext before importing comms_handler — same
# pattern as tests/test_s027_comms_handler.py.
# ---------------------------------------------------------------------------
for _mod in ("telegram", "telegram.ext", "telegram.error", "dotenv", "requests"):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: SimpleNamespace(args=a, kwargs=kw)
_tg.InlineKeyboardMarkup = lambda rows: SimpleNamespace(inline_keyboard=rows)


class _FakeTelegramError(Exception):
    pass


sys.modules["telegram.error"].TelegramError = _FakeTelegramError
sys.modules["telegram"].error = sys.modules["telegram.error"]

_tg_ext = sys.modules["telegram.ext"]
_tg_ext.Application = MagicMock
_tg_ext.CallbackQueryHandler = MagicMock
_tg_ext.MessageHandler = MagicMock
_tg_ext.ContextTypes = MagicMock()
_tg_ext.ContextTypes.DEFAULT_TYPE = object


class _FakeFilters:
    TEXT = MagicMock()
    COMMAND = MagicMock()


_tg_ext.filters = _FakeFilters


# Now we can import the consumer + comms primitives.
from src.bot import comms_handler as ch  # noqa: E402
from src.bot.test_strategy_consumer import (  # noqa: E402
    BacktestConsumer,
    BacktestRunResult,
    _format_summary,
    _is_test_strategy,
    _strategy_from_task,
)
from src.comms import RequestStore, STATUS  # noqa: E402
from src.comms.templates import make_test_strategy_request, make_new_session_request  # noqa: E402
from src.utils.validation_logger import log_validation  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures

@pytest.fixture
def store(tmp_path: Path) -> RequestStore:
    return RequestStore(tmp_path / "comms")


@pytest.fixture
def runtime_logs(tmp_path: Path, monkeypatch) -> Path:
    """Point the validation logger at a tmp dir for the duration of the test."""
    logs_dir = tmp_path / "runtime_logs"
    logs_dir.mkdir()
    monkeypatch.setenv("VALIDATION_LOG_PATH", str(logs_dir / "validation.jsonl"))
    return logs_dir


@pytest.fixture(autouse=True)
def _disable_git_push(monkeypatch):
    """Keep apply_answer's GitPusher disabled so tests never touch git."""
    monkeypatch.delenv("COMMS_PUSH_ENABLED", raising=False)


# ---------------------------------------------------------------------------
# Task parsing helpers

class TestTaskHelpers:
    def test_is_test_strategy_matches_pending_test_task(self, store):
        req = make_test_strategy_request("vwap")
        store.create(req)
        assert _is_test_strategy(req) is True

    def test_is_test_strategy_skips_non_pending(self, store):
        req = make_test_strategy_request("vwap")
        store.create(req)
        store.mark_sent(req)
        assert _is_test_strategy(req) is False

    def test_is_test_strategy_skips_other_tasks(self, store):
        req = make_new_session_request("S-099")
        assert _is_test_strategy(req) is False

    def test_strategy_from_task_extracts_name(self):
        assert _strategy_from_task("test_strategy:vwap") == "vwap"
        assert _strategy_from_task("test_strategy:turtle_soup") == "turtle_soup"

    def test_strategy_from_task_returns_empty_for_other(self):
        assert _strategy_from_task("new_session:S-099") == ""
        assert _strategy_from_task(None) == ""
        assert _strategy_from_task("") == ""


# ---------------------------------------------------------------------------
# BacktestConsumer — happy path

def _ok_runner(*, db_row_id: int = 42, total_trades: int = 5):
    """Build a deterministic runner stub for tests."""
    summary = {
        "run_date": "2026-05-09",
        "strategy_version": "vwap",
        "start_date": "2026-04-01",
        "end_date": "2026-05-08",
        "total_trades": total_trades,
        "winning_trades": 3,
        "losing_trades": 2,
        "win_rate": 60.0,
        "profit_factor": 1.8,
        "expectancy": 12.5,
        "max_drawdown": -45.0,
        "max_drawdown_pct": -2.1,
        "sharpe_ratio": 1.34,
        "total_pnl": 250.0,
        "total_pnl_pct": 2.5,
        "avg_win": 30.0,
        "avg_loss": -15.0,
        "largest_win": 80.0,
        "largest_loss": -25.0,
    }

    def _runner(strategy: str) -> BacktestRunResult:
        return BacktestRunResult(summary={**summary, "strategy_version": strategy}, db_row_id=db_row_id)

    return _runner


class TestConsumerHappyPath:
    def test_happy_path_transitions_to_answered(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)
        consumer = BacktestConsumer(runner=_ok_runner(db_row_id=7))

        processed = consumer.scan_and_run(store)

        assert processed == 1
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.ANSWERED
        assert loaded.response is not None
        assert len(loaded.response.answers) == 1
        ans = loaded.response.answers[0]
        assert ans.question_id == "results"
        assert ans.answer_type == "free_text"
        assert "vwap" in ans.free_text
        assert "db_row_id: 7" in ans.free_text

    def test_validation_log_ok_row(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)
        consumer = BacktestConsumer(runner=_ok_runner(db_row_id=11, total_trades=8))
        consumer.scan_and_run(store)

        log_path = runtime_logs / "validation.jsonl"
        rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
        assert len(rows) == 1
        row = rows[0]
        assert row["event"] == "backtest_run"
        assert row["request_id"] == req.request_id
        assert row["strategy"] == "vwap"
        assert row["outcome"] == "ok"
        assert row["db_row_id"] == 11
        assert "started_at_utc" in row
        assert "completed_at_utc" in row
        assert "logged_at_utc" in row
        assert row["summary"]["total_trades"] == 8
        assert row["summary"]["win_rate"] == 60.0

    def test_skips_non_test_strategy_artifacts(self, store, runtime_logs):
        ns_req = make_new_session_request("S-099")
        store.create(ns_req)
        consumer = BacktestConsumer(runner=_ok_runner())

        processed = consumer.scan_and_run(store)

        assert processed == 0
        # The new_session artifact must remain PENDING for the
        # delivery pass — the consumer must not touch it.
        loaded = store.load(ns_req.request_id)
        assert loaded.status == STATUS.PENDING
        assert loaded.response is None

    def test_skips_already_sent_test_strategy(self, store, runtime_logs):
        # An artifact that's already SENT (operator opened it manually
        # via the deliver pass) must not be re-run.
        req = make_test_strategy_request("vwap")
        store.create(req)
        store.mark_sent(req)
        consumer = BacktestConsumer(runner=_ok_runner())

        processed = consumer.scan_and_run(store)

        assert processed == 0


# ---------------------------------------------------------------------------
# Error path — missing data, registry miss, runner exception

class TestConsumerErrorPath:
    def test_runner_exception_writes_error_answer(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)

        def _boom(_strategy):
            raise FileNotFoundError("data/backtest_candles.csv missing")

        consumer = BacktestConsumer(runner=_boom)
        processed = consumer.scan_and_run(store)

        assert processed == 1
        loaded = store.load(req.request_id)
        # Even on error the request must reach ANSWERED so it
        # archives cleanly. (P2 hardening adds richer error envelopes;
        # P1 just guarantees the loop closes.)
        assert loaded.status == STATUS.ANSWERED
        ans = loaded.response.answers[0]
        assert "failed" in ans.free_text.lower()
        assert "FileNotFoundError" in ans.free_text
        assert "data/backtest_candles.csv missing" in ans.free_text

    def test_runner_exception_writes_error_validation_row(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)

        def _boom(_strategy):
            raise ValueError("strategy registry returned empty config")

        consumer = BacktestConsumer(runner=_boom)
        consumer.scan_and_run(store)

        log_path = runtime_logs / "validation.jsonl"
        row = json.loads(log_path.read_text().splitlines()[-1])
        assert row["outcome"] == "error"
        assert row["request_id"] == req.request_id
        assert "ValueError" in row["error"]
        assert "strategy registry returned empty config" in row["error"]
        assert "db_row_id" not in row
        assert "summary" not in row


# ---------------------------------------------------------------------------
# Multiple artifacts in one cycle

class TestMultipleArtifacts:
    def test_two_pending_test_artifacts_processed_in_one_pass(self, store, runtime_logs):
        a = make_test_strategy_request("vwap")
        b = make_test_strategy_request("turtle_soup")
        store.create(a)
        store.create(b)

        seen = []

        def _runner(strategy):
            seen.append(strategy)
            return _ok_runner()(strategy)

        consumer = BacktestConsumer(runner=_runner)
        processed = consumer.scan_and_run(store)

        assert processed == 2
        assert sorted(seen) == ["turtle_soup", "vwap"]
        # Both artifacts ANSWERED.
        for req in (a, b):
            loaded = store.load(req.request_id)
            assert loaded.status == STATUS.ANSWERED
        # Two rows in the validation log.
        rows = [
            json.loads(line)
            for line in (runtime_logs / "validation.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(rows) == 2
        assert {r["strategy"] for r in rows} == {"vwap", "turtle_soup"}


# ---------------------------------------------------------------------------
# Telegram answer formatting

class TestAnswerFormatting:
    def test_summary_is_plain_text(self):
        result = _ok_runner(db_row_id=99)("vwap")
        text = _format_summary("vwap", result)
        # No markdown special chars at message start (BUG-009/030/031).
        assert not text.startswith("*")
        assert not text.startswith("_")
        assert not text.startswith("`")
        assert "vwap" in text
        assert "db_row_id: 99" in text
        assert "trades:" in text
        assert "win_rate:" in text


# ---------------------------------------------------------------------------
# CommsPoller wiring — confirms scan_and_run runs in poll_once

def _async_run(coro):
    import asyncio
    return asyncio.run(coro)


class TestPollerWiring:
    def test_poll_once_runs_backtest_consumer_pass(self, store, runtime_logs):
        # Pin: the M5 consumer pass runs from CommsPoller.poll_once
        # before delivery, so a /test artifact is ANSWERED in the
        # same cycle it was minted (no Telegram delivery needed).
        req = make_test_strategy_request("vwap")
        store.create(req)

        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
        application = SimpleNamespace(bot=bot, bot_data={})

        consumer = BacktestConsumer(runner=_ok_runner(db_row_id=1))
        poller = ch.CommsPoller(
            store=store,
            repo_root=store.root.parent,
            chat_id="123",
            backtest_consumer=consumer,
        )
        _async_run(poller.poll_once(application))

        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.ANSWERED
        # Critical: bot.send_message was NOT called — the consumer
        # transitioned the artifact past PENDING before the deliver
        # pass saw it. This is the closed-loop guarantee.
        assert not bot.send_message.called

    def test_poll_once_skips_consumer_when_unset(self, store, runtime_logs):
        # Existing test_s027 pollers construct CommsPoller without
        # backtest_consumer — they must not break.
        req = make_test_strategy_request("vwap")
        store.create(req)

        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
        application = SimpleNamespace(bot=bot, bot_data={})

        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        _async_run(poller.poll_once(application))

        loaded = store.load(req.request_id)
        # Without the consumer, the artifact flows through the deliver
        # pass — same behaviour the M1 P1-D dispatch had before M5.
        assert loaded.status == STATUS.SENT


# ---------------------------------------------------------------------------
# validation_logger — direct unit tests

class TestValidationLogger:
    def test_writes_ndjson_with_logged_at(self, tmp_path):
        base = tmp_path / "rt"
        log_validation(
            {"event": "backtest_run", "request_id": "REQ-X", "outcome": "ok"},
            base=base,
        )
        path = base / "validation.jsonl"
        assert path.exists()
        row = json.loads(path.read_text().strip())
        assert row["event"] == "backtest_run"
        assert row["request_id"] == "REQ-X"
        assert "logged_at_utc" in row

    def test_appends_one_line_per_call(self, tmp_path):
        base = tmp_path / "rt"
        log_validation({"event": "backtest_run", "i": 1}, base=base)
        log_validation({"event": "backtest_run", "i": 2}, base=base)
        lines = (base / "validation.jsonl").read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["i"] == 1
        assert json.loads(lines[1])["i"] == 2

    def test_never_raises_on_unwritable_path(self, tmp_path, caplog):
        # Point at a path whose parent is a regular file — mkdir
        # raises NotADirectoryError. The writer must swallow it.
        bad_parent = tmp_path / "not-a-dir"
        bad_parent.write_text("x")
        log_validation({"event": "backtest_run"}, base=bad_parent / "deeper")
        # No exception bubbled up — that's the contract.
