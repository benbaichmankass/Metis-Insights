"""Project-wide pytest fixtures + stubs for the ICT Trading Bot suite.

S-016 H5 (BUG-010 fix): centralises the optional-import stubs that
~10 test files were copy-pasting individually. The copy-paste pattern
broke when S-014.5 PR #184 added a module-level
``_VM_WRITE_BUTTONS = InlineKeyboardMarkup([[…]])`` to
``src/bot/telegram_query_bot.py`` — passing a list as the first
positional arg to a bare ``MagicMock`` class crashes
``_mock_set_magics`` because lists are unhashable.

This conftest fixes the contract in one place: ``InlineKeyboardButton``
and ``InlineKeyboardMarkup`` are stubbed as **callable factories**
that return fresh ``MagicMock`` instances, so module-level constructor
calls work regardless of what positional shapes the caller uses.

Test files are free to add their own per-test stubs on top — this
conftest only stubs the things that need to exist *before* the bot
module imports.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock


# S-067 follow-up #1: register the shared real-schema sqlite fixture
# module so the ``real_schema_db`` pytest fixture is auto-discovered
# in every test file in this directory tree. New tests should prefer
# this over rolling a per-file ``CREATE TABLE`` so a future production
# schema change fails the regression test instead of silently passing.
pytest_plugins = ("tests.fixtures.real_schema_db",)


# ---------------------------------------------------------------------------
# Optional-dep stubs — only inserted if the real module isn't installed
# in this venv. Tests that genuinely need the real package can override
# this with ``pytest.importorskip("pandas")`` or similar; this conftest
# only fills holes left by the lean sandbox env.
# ---------------------------------------------------------------------------


def _stub_optional(name: str) -> None:
    """Insert ``MagicMock()`` for *name* if the import would fail."""
    if name in sys.modules:
        return
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = MagicMock()


# Heavy / optional imports the bot module pulls in transitively
# (signal_notifications → matplotlib; web client → fastapi; etc).
# Only stub the ones whose absence is benign for tests.
#
# S-045 T1 (BUG-062 fix): we also stub `telegram.error` and
# `telegram.constants` here. `src/bot/comms_handler.py` does
# `from telegram.error import TelegramError`, and
# `src/bot/claude_bridge.py` does `from telegram.constants import
# ChatAction`. Without these submodule stubs, ~45 tests that
# transitively import either bot module fail collection with
# "No module named 'telegram.error'; 'telegram' is not a package"
# because Python treats the bare-MagicMock `telegram` entry as a
# leaf, not a package. See `docs/claude/ci-status-checks.md`
# § pytest-collect.
for _name in (
    "matplotlib",
    "matplotlib.pyplot",
    "telegram",
    "telegram.ext",
    "telegram.error",
    "telegram.constants",
    "dotenv",
    "requests",
    # --- added 2026-08-27, BL-20260827-TEST-MODULES-STUB-SKLEARN-INTO-SYS-MODULES-AT-IMPORT-TIME
    #
    # These four are NOT here because anything in conftest needs them. They are
    # here to WIN THE SLOT before ~8 test modules run their own copy-pasted
    # stub block at import time:
    #
    #     for _mod in ("pandas", ..., "sklearn", ...):
    #         sys.modules.setdefault(_mod, MagicMock())
    #
    # `setdefault` guards on ALREADY-IMPORTED, not on IMPORTABLE — so on a box
    # where the real sklearn IS installed but simply has not been imported yet,
    # the MagicMock wins and every later `import sklearn.linear_model` dies with
    # "'sklearn' is not a package". `ml/calibration/fit.py::_fit_platt` imports
    # sklearn LOCALLY inside the function, which is exactly what makes it lose
    # that race: nothing imports sklearn during collection.
    #
    # `_stub_optional` above is the CORRECT form of the same idea — it tries the
    # real import first and stubs only on ImportError. Running it here for these
    # names puts the REAL module in sys.modules before any test module is
    # imported, which makes all eight `setdefault` calls no-ops without touching
    # them. This conftest's own header already records that it exists to
    # centralise stubs "~10 test files were copy-pasting"; those copies were
    # never removed, and this closes the gap from the one place that is
    # guaranteed to run first.
    #
    # Measured before/after: `pytest tests/ml/calibration/ tests/test_outcomes_integration.py`
    # went 5 failed / 17 passed -> 0 failed.
):
    _stub_optional(_name)


def _preimport_if_available(name: str) -> None:
    """Import *name* if it is installed. If it is NOT, do NOTHING — never stub.

    ⚠️ **THE "NEVER STUB" HALF IS LOAD-BEARING AND WAS LEARNED THE HARD WAY.**
    An earlier version of this fix routed these four through `_stub_optional`,
    which installs a `MagicMock` when the import fails. That broke `pytest.approx`
    outright in any environment WITHOUT numpy — `_pytest.python_api.is_bool` does::

        if np := sys.modules.get("numpy"):
            return isinstance(val, np.bool_)

    so a MagicMock in that slot makes `np.bool_` a MagicMock, which is not a
    type, and `isinstance()` raises `TypeError`. Caught by the `guards` CI job,
    which installs only ruff/import-linter/pyyaml/pytest and therefore has no
    numpy: `tests/test_over_cover_decision.py::test_reproduces_the_2026_08_20_failure`
    died on a `pytest.approx` comparison of `200.0`. `pytest-run` stayed green
    because it installs the full requirements — so the leaner job was the only
    thing that could see it.

    Pre-importing is all that is actually needed. The goal is only to make the
    REAL module win the `sys.modules` slot before ~8 test modules run their own
    copy-pasted `sys.modules.setdefault(_mod, MagicMock())` at import time —
    `setdefault` guards on ALREADY-IMPORTED rather than IMPORTABLE, so without
    this the mock shadows an installed package and a later
    `import sklearn.linear_model` dies with "'sklearn' is not a package".

    When the package is genuinely absent there is nothing to protect: leaving
    `sys.modules` untouched is both correct and strictly safer, and those test
    modules' own stubs still cover the tests that need them.
    """
    try:
        __import__(name)
    except Exception:  # allow-silent: absence is the expected case and is a no-op
        # Deliberately broad and deliberately silent. A heavy optional package
        # can fail to import for reasons beyond ImportError (a partial install,
        # a binary/ABI mismatch), and NONE of them should take the whole suite
        # down at conftest time — the pre-import is an optimisation, not a
        # dependency. Nothing is stubbed on this path.
        return


for _name in ("numpy", "pandas", "scipy", "sklearn"):
    _preimport_if_available(_name)


# ---------------------------------------------------------------------------
# Telegram stubs (centralised — fixes BUG-010).
#
# We expose `MagicMock`-style classes for the symbols imported at the
# top of `src/bot/telegram_query_bot.py`:
#
#     from telegram import (
#         Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
#     )
#     from telegram.ext import (
#         Application, CommandHandler, CallbackQueryHandler, ContextTypes,
#     )
#
# `Update`, `BotCommand`, etc. are fine as `MagicMock` (no callers use
# them in arg positions that crash `_mock_set_magics`).
#
# `InlineKeyboardButton` and `InlineKeyboardMarkup` MUST be callables
# that return fresh mocks — module-level code does
# `InlineKeyboardMarkup([[...]])` and we cannot pass a list to MagicMock's
# first positional (it ends up as `_mock_methods` which crashes when
# hashed).
# ---------------------------------------------------------------------------


_tg = sys.modules.get("telegram")
if _tg is not None:
    _tg.Update = getattr(_tg, "Update", MagicMock)
    _tg.BotCommand = getattr(_tg, "BotCommand", MagicMock)
    # Always override these two even if the test file already touched
    # the telegram module — the lambda factory is the only safe shape.
    _tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()
    _tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()

# S-045 T1: `telegram.error.TelegramError` MUST be a real exception
# class — `comms_handler.py` does `except TelegramError:` and a
# bare MagicMock attr crashes the except clause with TypeError.
_tg_err = sys.modules.get("telegram.error")
if _tg_err is not None:
    _existing_te = getattr(_tg_err, "TelegramError", None)
    if not (isinstance(_existing_te, type) and issubclass(_existing_te, BaseException)):
        class _StubTelegramError(Exception):
            """Stand-in for `telegram.error.TelegramError`."""

        _tg_err.TelegramError = _StubTelegramError
    # Cross-link so `telegram.error` is also reachable as `telegram.error`
    # when a caller does `import telegram` then `telegram.error.X`.
    if _tg is not None:
        _tg.error = _tg_err

# `telegram.constants.ChatAction` — referenced by `claude_bridge.py`.
_tg_const = sys.modules.get("telegram.constants")
if _tg_const is not None:
    _tg_const.ChatAction = getattr(_tg_const, "ChatAction", MagicMock())
    if _tg is not None:
        _tg.constants = _tg_const

_tgext = sys.modules.get("telegram.ext")
if _tgext is not None:
    _tgext.Application = getattr(_tgext, "Application", MagicMock)
    _tgext.CommandHandler = getattr(_tgext, "CommandHandler", MagicMock)
    _tgext.CallbackQueryHandler = getattr(
        _tgext, "CallbackQueryHandler", MagicMock,
    )
    # `MessageHandler` + `filters` are imported by `comms_handler.py`
    # and `claude_bridge.py` but were missing from the conftest stub.
    _tgext.MessageHandler = getattr(_tgext, "MessageHandler", MagicMock)
    _tgext.filters = getattr(_tgext, "filters", MagicMock())
    _ctx = getattr(_tgext, "ContextTypes", MagicMock())
    _ctx.DEFAULT_TYPE = getattr(_ctx, "DEFAULT_TYPE", MagicMock)
    _tgext.ContextTypes = _ctx


# ---------------------------------------------------------------------------
# First-party module-stub leakage guard.
#
# Several test files stub *first-party* modules at import time, e.g.
# ``for _mod in (..., "src.runtime.notify", ...): sys.modules.setdefault(
# _mod, MagicMock())``. That pattern was added when the lean sandbox
# couldn't import those modules; with the heavy/telegram deps now stubbed
# above they import cleanly. The problem is order-dependence: whichever
# test file is collected first wins the ``setdefault`` and installs a
# ``MagicMock`` that survives for the whole session — so a later test that
# needs the *real* module (and patches a real attribute on it) fails with
# ``AttributeError`` or runs vacuously against the mock. This produced a
# block of order-dependent failures (test_s026_g4, test_pipeline_news_veto,
# test_notify_send_via_alert_manager, test_hourly_report, …) that pass in
# isolation but fail in the full suite.
#
# Fix: import the real first-party modules here, before any test module is
# collected. ``setdefault`` then no-ops (the real module is already
# present), so every test sees the real module regardless of collection
# order. Telegram/pandas/etc. are already stubbed above, so these imports
# are network-free and side-effect-light. Anything that genuinely can't
# import is skipped (it falls back to the per-file stub, same as before).
for _real_mod in (
    "src.utils.paths",
    "src.runtime.notify",
    "src.runtime.outcomes",
    "src.runtime.signal_notifications",
    "src.runtime.signal_writer",
    "src.utils.signal_audit_logger",
    "src.runtime.health",
    "src.runtime.market_data",
    "src.runtime.order_monitor",
    "src.runtime.orders",
    "src.runtime.hourly_report",
    "src.runtime.pipeline",
):
    try:
        __import__(_real_mod)
    except Exception:  # pragma: no cover - env-specific import gaps
        pass


# ---------------------------------------------------------------------------
# Intent-multiplexer per-bar emission debounce — test isolation.
#
# The re-entry-storm guard (PERF-20260601-001) keeps module-level,
# wall-clock-bucketed state in ``intent_multiplexer._LAST_EMITTED_BUCKET`` so a
# strategy emits at most once per closed bar across the many ticks inside it.
# That state is intentionally process-lived in production, but in the test
# suite it leaks across tests: an emission in one test would debounce an
# unrelated later test whose ``multiplexed_intent_signal_builder`` /
# ``_debounce_emissions`` call lands in the same wall-clock bar bucket. Clear it
# around every test. Best-effort + import-free: only touch the module if a test
# already imported it (so we never force its heavy transitive import).
# ---------------------------------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_intent_emission_debounce():
    def _clear() -> None:
        mod = sys.modules.get("src.runtime.intent_multiplexer")
        if mod is not None and hasattr(mod, "_LAST_EMITTED_BUCKET"):
            mod._LAST_EMITTED_BUCKET.clear()

    _clear()
    yield
    _clear()


# ---------------------------------------------------------------------------
# Test-isolation audit (Lane A, 2026-08-28).
#
# Detects a test that leaves ``sys.modules`` or ``runtime_logs/`` changed —
# the CLASS behind three separate defects found on 2026-08-27, each of which
# was fixed only as an instance. Rationale, the cheap-detect/expensive-classify
# split, and why "replaced" alone is not the finding: ``tests/isolation_audit``.
#
# ⚠️ DEFAULT IS ``annotate`` — it REPORTS and fails nothing. Blast radius is
# 13,334 tests, and the baseline was measured on ONE sandbox while this repo's
# own history says a sandbox and CI disagree about exactly this class in BOTH
# directions. ``TEST_ISOLATION_AUDIT=enforce`` opts in; ``off`` disables.
# The same annotate-then-apply discipline the runtime uses for
# ``NETTING_ATTRIBUTION_MODE`` / ``PROTECTION_STRAY_GROUP_MODE``.
# ---------------------------------------------------------------------------
import os  # noqa: E402
from pathlib import Path  # noqa: E402

from tests import isolation_audit as _iso  # noqa: E402
from tests.isolation_baseline import is_baselined as _is_baselined  # noqa: E402

_ISO_MODE = _iso.resolve_mode(os.environ.get("TEST_ISOLATION_AUDIT"))
_ISO_RUNTIME_LOGS = Path(os.environ.get("RUNTIME_LOGS_DIR", "runtime_logs"))
_iso_findings: list[tuple[str, str, str]] = []
_iso_state: dict[str, object] = {}


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if _ISO_MODE == _iso.MODE_OFF:
        return
    _iso_state["modules"] = _iso.snapshot_modules()
    _iso_state["tree"] = _iso.snapshot_tree(_ISO_RUNTIME_LOGS)


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    """Compare after the test's own fixtures have finalised.

    ``trylast`` matters: fixture teardown runs inside the default
    ``pytest_runtest_teardown`` implementation, so running last is what lets a
    ``monkeypatch``-restored entry correctly report CLEAN rather than as a leak.
    """
    if _ISO_MODE == _iso.MODE_OFF:
        return
    before = _iso_state.get("modules")
    if before is None:
        _iso_findings.append((item.nodeid, _iso.NOT_MEASURED, "no setup snapshot"))
        return

    for state, names in _iso.diff_modules(before).items():
        if state == _iso.MODULE_REPLACED_SYNTHETIC:
            continue  # a test-local loader name re-created by design
        _iso_findings.append((item.nodeid, state, ",".join(sorted(names)[:10])))

    tree_before = _iso_state.get("tree")
    tree_after = _iso.snapshot_tree(_ISO_RUNTIME_LOGS)
    if tree_before is None or tree_after is None:
        _iso_findings.append((item.nodeid, _iso.NOT_MEASURED, "runtime_logs unreadable"))
    else:
        changed = (tree_after - tree_before) | (tree_before - tree_after)
        if changed:
            _iso_findings.append(
                (item.nodeid, _iso.RUNTIME_LOGS_WRITTEN, ",".join(sorted(changed)[:10]))
            )


def pytest_sessionfinish(session, exitstatus):
    if _ISO_MODE == _iso.MODE_OFF or not _iso_findings:
        return
    undeclared = [
        (n, s, d)
        for n, s, d in _iso_findings
        if s in _iso.HARMFUL and not _is_baselined(n, s)
    ]
    counts: dict[str, int] = {}
    for _, s, _d in _iso_findings:
        counts[s] = counts.get(s, 0) + 1

    tw = getattr(session.config, "get_terminal_writer", lambda: None)()
    def _say(line: str) -> None:
        if tw is not None:
            tw.line(line)
        else:  # pragma: no cover - non-terminal runners
            print(line)

    _say("")
    _say(f"test-isolation audit [{_ISO_MODE}]: " + ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())))
    if undeclared:
        _say(f"  ⚠️ {len(undeclared)} UNDECLARED harmful finding(s) — not in tests/isolation_baseline.py:")
        for nodeid, state, detail in undeclared[:20]:
            _say(f"    [{state}] {nodeid}\n        {detail}")
        if _ISO_MODE == _iso.MODE_ENFORCE:
            raise SystemExit(
                f"test-isolation audit: {len(undeclared)} undeclared harmful finding(s). "
                "Restore what the test changed, or declare it in tests/isolation_baseline.py."
            )
