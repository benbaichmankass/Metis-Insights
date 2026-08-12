"""The exit-loop health state must be REACHABLE, not merely written.

`exit_loop_health.write_state_file`'s own docstring says it persists `status()`
"for the diag surface". #8778 shipped that writer with no `_LOG_FILES` entry, so
the one surface a relay-bound session can actually reach did not serve it — the
written-but-not-readable shape `provenance-consumer-guard` exists to catch
(#8665's exposure block reached `status()` via `**risk_report` and no consumer
ever referenced the key).

Why this matters more here than for a soak log: the exit loop left the liveness
watchdog's coverage by construction. That coverage was never a probe — it was
the fact that exit evaluation ran INLINE on the tick whose heartbeat the
watchdog measures. Moving it to its own thread means a stalled exit loop is a
condition NOTHING else observes, so an unreadable state file is not a cosmetic
gap; it is the only evidence that the decouple is still working.

These tests pin the wiring, not the prose. Both would have failed on the merged
#8778.
"""
from __future__ import annotations

import json

from src.runtime import exit_loop_health
from src.web.api.routers import diag


def test_exit_loop_health_is_served_by_the_diag_log_file_allowlist():
    assert "exit_loop_health" in diag._LOG_FILES, (
        "exit_loop_health.write_state_file writes a file 'for the diag surface' "
        "that the diag surface does not serve — written and not readable"
    )


def test_the_served_path_is_the_one_the_writer_actually_writes():
    """Name-match is not enough: an entry pointing at the wrong file reads clean.

    The allowlist could name the key correctly and resolve a path nothing ever
    writes, which serves an empty tail forever and looks like a quiet, healthy
    loop — sub-class C, an unasserted denominator. So compare against the path
    the WRITER returns, not against a literal repeated here.
    """
    written = exit_loop_health.write_state_file()
    assert written is not None, "writer returned no path"
    assert diag._LOG_FILES["exit_loop_health"].name == exit_loop_health.STATE_FILE_NAME
    # The writer resolves through runtime_logs_dir(); so does the router. Compare
    # basenames AND that the router's parent is that same resolved dir.
    from src.utils.paths import runtime_logs_dir

    assert diag._LOG_FILES["exit_loop_health"].parent == runtime_logs_dir()


def test_the_served_payload_carries_the_state_a_reader_needs(tmp_path):
    """A reachable file that omits the state vocabulary is still unreadable.

    Pins the four-state field and the two numbers that make a max interpretable
    (`passes` is the denominator for `max_pass_ms`).
    """
    path = exit_loop_health.write_state_file(runtime_dir=str(tmp_path))
    payload = json.loads((tmp_path / exit_loop_health.STATE_FILE_NAME).read_text())
    assert path is not None
    assert payload.get("state") in {"unknown", "never_ran", "fresh", "stale"}
    for key in ("passes", "max_pass_ms", "age_seconds", "generated_at"):
        assert key in payload, f"{key} missing — a max with no denominator"
