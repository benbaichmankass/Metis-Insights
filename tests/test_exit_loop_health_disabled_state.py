"""A disabled exit-loop decouple must not report a healthy loop.

WHY (live-verified 2026-08-14). `write_state_file` was only ever called by
`_exit_loop`, and `run_exit_loop_health_check` only inside `if decoupled:`. With
`EXIT_LOOP_DECOUPLE_DISABLED=1` neither ran, so the PREVIOUS process's payload
survived — and it said `"state": "fresh"`. Measured on the live trader:
`generated_at 2026-08-14T11:46:13Z` on a file describing a process that started
at `11:46:29Z`, i.e. a fossil stamped 16s before its own subject, reporting a
loop that did not exist.

These tests are written against the INVARIANT — after a disabled-mode write, no
reader can conclude the loop is healthy — and they fail against the pre-fix code,
where `write_disabled_state_file` does not exist at all.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import exit_loop_health


def _write_fossil(tmp_path) -> None:
    """Stand in for the previous process's healthy payload."""
    (tmp_path / exit_loop_health.STATE_FILE_NAME).write_text(
        json.dumps({
            "state": "fresh",
            "stale": False,
            "passes": 55,
            "age_seconds": 12.3,
            "generated_at": "2026-08-14T11:46:13.939319+00:00",
        }),
        encoding="utf-8",
    )


def _read(tmp_path) -> dict:
    return json.loads(
        (tmp_path / exit_loop_health.STATE_FILE_NAME).read_text(encoding="utf-8")
    )


def test_disabled_write_overwrites_a_previous_processes_fresh(tmp_path):
    """THE regression. A fossil saying `fresh` must not survive."""
    _write_fossil(tmp_path)
    assert _read(tmp_path)["state"] == "fresh"  # precondition

    exit_loop_health.write_disabled_state_file(runtime_dir=str(tmp_path))

    payload = _read(tmp_path)
    assert payload["state"] == "never_ran"
    assert payload["stale"] is False
    assert payload["decouple_disabled"] is True


def test_disabled_state_is_distinguishable_from_a_loop_that_failed_to_start(tmp_path):
    """Both are `never_ran`; only `decouple_disabled` separates a deliberate
    rollback from a thread that died. Collapsing them would re-introduce the
    very ambiguity this fix removes, one level down."""
    exit_loop_health.write_disabled_state_file(runtime_dir=str(tmp_path))
    disabled = _read(tmp_path)

    exit_loop_health._reset_for_tests()
    exit_loop_health.write_state_file(runtime_dir=str(tmp_path))
    failed_to_start = _read(tmp_path)

    assert disabled["state"] == failed_to_start["state"] == "never_ran"
    assert disabled.get("decouple_disabled") is True
    assert failed_to_start.get("decouple_disabled") is not True


def test_generated_at_advances_so_a_fossil_stays_detectable(tmp_path):
    """Written every tick, not once at boot: a `generated_at` that stops moving
    is itself the fossil signal a reader needs."""
    exit_loop_health.write_disabled_state_file(runtime_dir=str(tmp_path))
    first = _read(tmp_path)["generated_at"]
    exit_loop_health.write_disabled_state_file(runtime_dir=str(tmp_path))
    second = _read(tmp_path)["generated_at"]

    assert second >= first
    assert first is not None and second is not None


def test_payload_never_claims_health(tmp_path):
    """No field a reader might scan should read as healthy."""
    exit_loop_health.write_disabled_state_file(runtime_dir=str(tmp_path))
    payload = _read(tmp_path)

    assert payload["state"] != "fresh"
    assert payload["passes"] == 0
    assert payload["last_pass_utc"] is None
    assert payload["age_seconds"] is None
    assert "not met" in payload["note"]


def test_write_is_best_effort_and_never_raises():
    """This runs on the live tick; observability must never break the loop."""
    assert exit_loop_health.write_disabled_state_file(
        runtime_dir="/proc/nonexistent-cannot-mkdir/x"
    ) is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
