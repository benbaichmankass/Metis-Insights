"""Declared baseline for the test-isolation audit — the known offenders.

⚠️ **THIS IS A RATCHET, NOT AN ALLOWLIST TO GROW.** Every entry is a test that
leaves ``sys.modules`` changed. Shrinking this dict is the work; adding to it
needs a reason as good as the ones already here, and a new entry should be
treated as a finding to fix rather than a line to add.

⚠️ **MEASURED ON ONE SANDBOX** (full suite, 13,334 passed, 785 s, 2026-08-28),
and this repo's own history says that is not the same as CI: all three of the
2026-08-27 isolation defects were **invisible in CI**, because CI installs the
full requirements and a leaner box was the only thing that could see them. The
inverse is equally possible — CI may reach a name this box does not. That is
why the audit ships at ``annotate`` and this baseline has not yet been
confirmed against a CI run.

Keyed ``nodeid -> [states]``. A state not listed for a nodeid is NOT baselined.
"""
from __future__ import annotations

# Each group carries WHY the test does this, so a future reader can tell an
# understood-and-contained mutation from a fresh accident.
BASELINE: dict[str, tuple[str, ...]] = {
    # ------------------------------------------------------------------
    # Deliberate absence simulation: the test proves a code path works
    # WITHOUT a package, so it must delete the package to create the
    # condition. The intent is sound; the missing half is the restore.
    # ------------------------------------------------------------------
    "tests/test_fetch_backtest_candles_yfinance.py::test_the_map_loads_without_the_ml_package": (
        "module_removed",
    ),
    "tests/test_silent_except_sweep.py::test_dashboard_strategy_data_failure_reports": (
        "module_removed",
    ),

    # ------------------------------------------------------------------
    # Import-light assertions: the test pops a module so it can prove the
    # module under test does NOT pull it in, then re-imports. The re-import
    # lands a NEW object, so the slot is left holding a different module
    # than the run started with.
    #
    # ⚠️ `test_notify_session.py` already carries a `_pop_and_restore`
    # helper, added by BL-20260827-NOTIFY-SESSION-...-NEVER-RESTORES, whose
    # row reads "FIXED same session". These four tests are the call sites
    # that fix did NOT cover — they still pop bare (lines 217, 242, 268,
    # 294). Recording that the earlier fix was PARTIAL rather than letting
    # the resolved row imply the file is clean.
    # ------------------------------------------------------------------
    "tests/test_insights_router.py::test_router_module_does_not_import_anthropic": (
        "module_replaced_real",
    ),
    "tests/test_notify_session.py::TestTelegramDirectSuccess::test_send_returns_cleanly_and_script_exits_zero": (
        "module_replaced_real",
    ),
    "tests/test_notify_session.py::TestTelegramDirectMissingCreds::test_warns_and_returns_without_raising_and_script_exits_zero": (
        "module_replaced_real",
    ),
    "tests/test_notify_session.py::TestTelegramDirectNetworkError::test_url_error_yields_nonzero_exit_with_stderr_marker": (
        "module_replaced_real",
    ),
    "tests/test_notify_session.py::TestTelegramDirectNoTokenInLogs::test_synthetic_token_never_appears_in_log_records": (
        "module_replaced_real",
    ),

    # ------------------------------------------------------------------
    # Path-resolution reloads: `_reload()` pops the module and re-imports it
    # under a different environment to prove reader and writer agree on a
    # path. Necessarily replaces the slot; never restores the original.
    # ------------------------------------------------------------------
    "tests/test_runtime_paths_alignment.py::test_heartbeat_reader_writer_alignment_default": (
        "module_replaced_real",
    ),
    "tests/test_runtime_paths_alignment.py::test_heartbeat_reader_writer_alignment_with_data_dir": (
        "module_replaced_real",
    ),
    "tests/test_runtime_paths_alignment.py::test_signal_audit_reader_writer_alignment_with_data_dir": (
        "module_replaced_real",
    ),
    "tests/test_runtime_paths_alignment.py::test_runtime_status_reader_writer_alignment_with_data_dir": (
        "module_replaced_real",
    ),
    "tests/test_runtime_paths_alignment.py::test_shadow_predictions_reader_writer_alignment_with_data_dir": (
        "module_replaced_real",
    ),
    "tests/test_runtime_paths_alignment.py::test_runtime_logs_dir_override_takes_precedence": (
        "module_replaced_real",
    ),
}


def is_baselined(nodeid: str, state: str) -> bool:
    """True when *state* for *nodeid* is a declared, understood offender."""
    return state in BASELINE.get(nodeid, ())
