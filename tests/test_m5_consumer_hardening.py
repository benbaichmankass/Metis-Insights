"""M5 P2 — hardening tests.

Pins:

  * ``M5_CONSUMER_ENABLED`` env gate — ``install_comms_handlers``
    only wires the default consumer when the var is truthy.
  * Subprocess runner — happy path JSON parse, timeout → ``BacktestTimeout``,
    non-zero exit → ``BacktestSubprocessFailure`` with truncated stderr,
    malformed stdout → structured error.
  * Validation log error envelope carries the runner's ``label``
    (``timeout`` / ``subprocess_failure``) instead of the raw class
    name + an ``exit_code`` field when present.
  * Single-flight lock — a re-entrant ``scan_and_run`` call while a
    runner is in flight does not re-fire the same artifact.
  * Two artifacts in one cycle still process sequentially (regression
    guard for P1 behaviour).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Stub telegram before the comms_handler import — same pattern as
# tests/test_m5_consumer.py. ``tests/conftest.py`` already establishes
# the canonical ``telegram.error.TelegramError`` stub; do NOT
# override it here (overrides pollute comms_handler's frozen import).
for _mod in ("telegram", "telegram.ext", "telegram.error", "dotenv", "requests"):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: SimpleNamespace(args=a, kwargs=kw)
_tg.InlineKeyboardMarkup = lambda rows: SimpleNamespace(inline_keyboard=rows)
_tg.error = sys.modules["telegram.error"]

_tg_ext = sys.modules["telegram.ext"]
_tg_ext.Application = MagicMock
# CallbackQueryHandler / MessageHandler take ``filters.TEXT &
# ~filters.COMMAND`` expressions — using ``MagicMock`` directly
# triggers ``spec`` on the filter arg which is itself a Mock and
# raises ``InvalidSpecError``. Plain callables sidestep the spec
# inference entirely.
_tg_ext.CallbackQueryHandler = lambda *a, **kw: SimpleNamespace()
_tg_ext.MessageHandler = lambda *a, **kw: SimpleNamespace()
_tg_ext.ContextTypes = MagicMock()
_tg_ext.ContextTypes.DEFAULT_TYPE = object


class _FakeFilters:
    TEXT = MagicMock()
    COMMAND = MagicMock()


_tg_ext.filters = _FakeFilters

import subprocess as _subprocess  # noqa: E402

from src.bot import comms_handler as ch  # noqa: E402
from src.bot import test_strategy_consumer as tsc  # noqa: E402
from src.bot.test_strategy_consumer import (  # noqa: E402
    BacktestConsumer,
    BacktestRunResult,
    BacktestSubprocessFailure,
    BacktestTimeout,
    M5_CONSUMER_ENABLED_ENV,
    default_run_backtest,
)
from src.comms import RequestStore, STATUS  # noqa: E402
from src.comms.templates import make_test_strategy_request  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures

@pytest.fixture
def store(tmp_path: Path) -> RequestStore:
    return RequestStore(tmp_path / "comms")


@pytest.fixture
def runtime_logs(tmp_path: Path, monkeypatch) -> Path:
    logs_dir = tmp_path / "runtime_logs"
    logs_dir.mkdir()
    monkeypatch.setenv("VALIDATION_LOG_PATH", str(logs_dir / "validation.jsonl"))
    return logs_dir


@pytest.fixture(autouse=True)
def _disable_git_push(monkeypatch):
    monkeypatch.delenv("COMMS_PUSH_ENABLED", raising=False)


# ---------------------------------------------------------------------------
# install_comms_handlers env gate

@pytest.fixture
def stub_handlers(monkeypatch):
    """Replace handler classes that comms_handler imported at module load.

    Test files that import comms_handler may have set telegram.ext.
    MessageHandler to a MagicMock class — calling it with a filter
    expression triggers the InvalidSpecError. We rebind on the
    comms_handler namespace itself so the env-gate path runs cleanly
    regardless of test ordering.
    """
    monkeypatch.setattr(ch, "MessageHandler", lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(ch, "CallbackQueryHandler", lambda *a, **kw: SimpleNamespace())


class TestInstallEnvGate:
    def test_consumer_disabled_when_env_unset(self, tmp_path, monkeypatch, stub_handlers):
        monkeypatch.delenv(M5_CONSUMER_ENABLED_ENV, raising=False)
        application = SimpleNamespace(
            bot_data={},
            add_handler=MagicMock(),
            post_init=None,
        )
        poller = ch.install_comms_handlers(application, repo_root=tmp_path, chat_id="123")
        assert poller.backtest_consumer is None

    def test_consumer_enabled_when_env_truthy(self, tmp_path, monkeypatch, stub_handlers):
        monkeypatch.setenv(M5_CONSUMER_ENABLED_ENV, "1")
        application = SimpleNamespace(
            bot_data={},
            add_handler=MagicMock(),
            post_init=None,
        )
        poller = ch.install_comms_handlers(application, repo_root=tmp_path, chat_id="123")
        assert poller.backtest_consumer is not None
        assert isinstance(poller.backtest_consumer, BacktestConsumer)

    @pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes", "on", "True"])
    def test_consumer_enabled_for_truthy_aliases(self, tmp_path, monkeypatch, stub_handlers, flag):
        monkeypatch.setenv(M5_CONSUMER_ENABLED_ENV, flag)
        application = SimpleNamespace(
            bot_data={},
            add_handler=MagicMock(),
            post_init=None,
        )
        poller = ch.install_comms_handlers(application, repo_root=tmp_path, chat_id="123")
        assert poller.backtest_consumer is not None

    @pytest.mark.parametrize("flag", ["0", "", "false", "no", "off"])
    def test_consumer_disabled_for_falsy_aliases(self, tmp_path, monkeypatch, stub_handlers, flag):
        monkeypatch.setenv(M5_CONSUMER_ENABLED_ENV, flag)
        application = SimpleNamespace(
            bot_data={},
            add_handler=MagicMock(),
            post_init=None,
        )
        poller = ch.install_comms_handlers(application, repo_root=tmp_path, chat_id="123")
        assert poller.backtest_consumer is None

    def test_explicit_consumer_bypasses_env_gate(self, tmp_path, monkeypatch, stub_handlers):
        # Explicit instantiation always wins so tests never depend
        # on the env var being set.
        monkeypatch.delenv(M5_CONSUMER_ENABLED_ENV, raising=False)
        explicit = BacktestConsumer(runner=lambda s: BacktestRunResult({}, db_row_id=None))
        application = SimpleNamespace(
            bot_data={},
            add_handler=MagicMock(),
            post_init=None,
        )
        poller = ch.install_comms_handlers(
            application, repo_root=tmp_path, chat_id="123",
            backtest_consumer=explicit,
        )
        assert poller.backtest_consumer is explicit


# ---------------------------------------------------------------------------
# default_run_backtest — subprocess wrapper

def _proc_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class TestDefaultRunner:
    def test_parses_json_envelope_on_last_stdout_line(self, monkeypatch):
        envelope = json.dumps({"db_row_id": 42, "summary": {"total_trades": 5}})
        stdout = f"Backtest data loaded\n{envelope}\n"
        monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _proc_result(stdout=stdout))
        # Patch on the consumer's namespace too — default_run_backtest
        # calls subprocess.run via its module-level import.
        monkeypatch.setattr(tsc.subprocess, "run", lambda *a, **kw: _proc_result(stdout=stdout))

        result = default_run_backtest("vwap")
        assert result.db_row_id == 42
        assert result.summary["total_trades"] == 5

    def test_timeout_raises_BacktestTimeout(self, monkeypatch):
        def _raise(*a, **kw):
            raise _subprocess.TimeoutExpired(cmd=a[0] if a else ["x"], timeout=kw.get("timeout", 0))
        monkeypatch.setattr(tsc.subprocess, "run", _raise)
        monkeypatch.setenv("M5_BACKTEST_TIMEOUT_S", "5")
        with pytest.raises(BacktestTimeout) as excinfo:
            default_run_backtest("vwap")
        assert "5s" in str(excinfo.value)
        assert "vwap" in str(excinfo.value)

    def test_nonzero_exit_raises_subprocess_failure(self, monkeypatch):
        monkeypatch.setattr(
            tsc.subprocess, "run",
            lambda *a, **kw: _proc_result(stderr="ValueError: bad data\n", returncode=1),
        )
        with pytest.raises(BacktestSubprocessFailure) as excinfo:
            default_run_backtest("vwap")
        assert excinfo.value.exit_code == 1
        # stderr ferried into the message.
        assert "ValueError: bad data" in str(excinfo.value)

    def test_long_stderr_is_truncated(self, monkeypatch):
        long = "X" * 5000
        monkeypatch.setattr(
            tsc.subprocess, "run",
            lambda *a, **kw: _proc_result(stderr=long, returncode=2),
        )
        with pytest.raises(BacktestSubprocessFailure) as excinfo:
            default_run_backtest("vwap")
        msg = str(excinfo.value)
        # Truncation marker present, full payload is not.
        assert "…" in msg
        assert long not in msg

    def test_empty_stdout_raises(self, monkeypatch):
        monkeypatch.setattr(
            tsc.subprocess, "run",
            lambda *a, **kw: _proc_result(stdout="   \n", returncode=0),
        )
        with pytest.raises(BacktestSubprocessFailure):
            default_run_backtest("vwap")

    def test_non_json_stdout_raises(self, monkeypatch):
        monkeypatch.setattr(
            tsc.subprocess, "run",
            lambda *a, **kw: _proc_result(stdout="this is not json\n", returncode=0),
        )
        with pytest.raises(BacktestSubprocessFailure):
            default_run_backtest("vwap")

    def test_missing_summary_field_raises(self, monkeypatch):
        envelope = json.dumps({"db_row_id": 1, "summary": "oops-not-a-dict"})
        monkeypatch.setattr(
            tsc.subprocess, "run",
            lambda *a, **kw: _proc_result(stdout=envelope + "\n", returncode=0),
        )
        with pytest.raises(BacktestSubprocessFailure):
            default_run_backtest("vwap")

    def test_invalid_timeout_env_falls_back(self, monkeypatch):
        # Bad value must not crash the runner; we just use the default.
        captured: dict = {}

        def _capture(*a, **kw):
            captured["timeout"] = kw.get("timeout")
            return _proc_result(
                stdout=json.dumps({"db_row_id": 1, "summary": {}}),
                returncode=0,
            )

        monkeypatch.setattr(tsc.subprocess, "run", _capture)
        monkeypatch.setenv("M5_BACKTEST_TIMEOUT_S", "not-an-int")
        default_run_backtest("vwap")
        assert captured["timeout"] == tsc.DEFAULT_BACKTEST_TIMEOUT_S


# ---------------------------------------------------------------------------
# Validation-log error envelope carries the runner label

class TestErrorEnvelope:
    def test_timeout_outcome_is_timeout(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)

        def _runner(_strategy):
            raise BacktestTimeout("backtest exceeded 5s (strategy=vwap)")

        consumer = BacktestConsumer(runner=_runner)
        consumer.scan_and_run(store)

        log = (runtime_logs / "validation.jsonl").read_text().strip()
        row = json.loads(log)
        assert row["outcome"] == "timeout"
        assert "BacktestTimeout" in row["error"]
        # Exit-code field absent on the timeout path.
        assert "exit_code" not in row

    def test_subprocess_failure_outcome_includes_exit_code(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)

        def _runner(_strategy):
            raise BacktestSubprocessFailure("subprocess exit=2: bad", exit_code=2)

        consumer = BacktestConsumer(runner=_runner)
        consumer.scan_and_run(store)

        row = json.loads((runtime_logs / "validation.jsonl").read_text().strip())
        assert row["outcome"] == "subprocess_failure"
        assert row["exit_code"] == 2

    def test_generic_exception_outcome_remains_error(self, store, runtime_logs):
        # Pre-P2 behaviour preserved for callers that raise raw
        # exceptions (e.g. a custom test runner).
        req = make_test_strategy_request("vwap")
        store.create(req)

        def _runner(_strategy):
            raise RuntimeError("something else broke")

        consumer = BacktestConsumer(runner=_runner)
        consumer.scan_and_run(store)

        row = json.loads((runtime_logs / "validation.jsonl").read_text().strip())
        assert row["outcome"] == "error"


# ---------------------------------------------------------------------------
# Single-flight lock — re-entrant scan does not re-fire same artifact

class TestSingleFlightLock:
    def test_in_flight_request_is_not_re_entered(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)

        # Runner that calls scan_and_run again while still mid-run —
        # simulates a delayed-timer callback firing inside the same
        # event loop tick. The second scan must skip this artifact.
        re_entry_count = {"n": 0}

        def _reentrant_runner(strategy: str) -> BacktestRunResult:
            re_entry_count["n"] += 1
            # Re-issue scan; it will see the artifact still in
            # _in_flight (we are inside _run_one) and bail.
            consumer.scan_and_run(store)  # noqa: F821 — bound below.
            return BacktestRunResult({"total_trades": 0}, db_row_id=1)

        consumer = BacktestConsumer(runner=_reentrant_runner)
        consumer.scan_and_run(store)

        # Runner fired exactly once.
        assert re_entry_count["n"] == 1
        # Single validation row.
        rows = [
            json.loads(line)
            for line in (runtime_logs / "validation.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(rows) == 1

    def test_in_flight_set_clears_after_run(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)
        consumer = BacktestConsumer(
            runner=lambda s: BacktestRunResult({"total_trades": 0}, db_row_id=1)
        )
        consumer.scan_and_run(store)
        assert consumer._in_flight == set()

    def test_in_flight_set_clears_after_runner_exception(self, store, runtime_logs):
        req = make_test_strategy_request("vwap")
        store.create(req)

        def _boom(_strategy):
            raise RuntimeError("backtest crashed")

        consumer = BacktestConsumer(runner=_boom)
        consumer.scan_and_run(store)
        # Even on the error path the lock must release.
        assert consumer._in_flight == set()


# ---------------------------------------------------------------------------
# Sequential processing (P1 behaviour preserved under P2 changes)

class TestSequentialProcessing:
    def test_two_artifacts_run_serially_no_lock_contention(self, store, runtime_logs):
        a = make_test_strategy_request("vwap")
        b = make_test_strategy_request("turtle_soup")
        store.create(a)
        store.create(b)

        seen: list[str] = []

        def _runner(strategy: str) -> BacktestRunResult:
            seen.append(strategy)
            return BacktestRunResult({"total_trades": 1}, db_row_id=len(seen))

        consumer = BacktestConsumer(runner=_runner)
        processed = consumer.scan_and_run(store)
        assert processed == 2
        assert sorted(seen) == ["turtle_soup", "vwap"]
        # Both artifacts answered, lock released.
        for req in (a, b):
            loaded = store.load(req.request_id)
            assert loaded.status == STATUS.ANSWERED
        assert consumer._in_flight == set()
