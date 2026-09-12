"""A guard whose RUNNER is absent checked NOTHING, and said `FAIL`.

MEASURED INCIDENT, 2026-09-10. A session ran `run_guards.py` in a container
without `pytest`, read ``PASS 66 · FAIL 4, all four environmental, not the
diff``, and shipped. CI — the one runner where the tools are installed — then
named **two real, load-bearing defects sitting inside those very guards**: a
new `system-action` absent from `tests/ops/test_system_actions_workflow.py`'s
`EXPECTED_ACTIONS`, which would have merged looking correct and aborted the
first time an operator ran an approved real-money-adjacent repair; and
`scripts/ops/notify_run.sh` with no priority mapping, so the run would have
notified the operator with no routing. Neither is reachable by any guard that
does not need that runner.

⚠️ THE GAP WAS ROUTING, NOT KNOWLEDGE. The one-command remedy was already
written down — inside a 1298-row backlog `CLAUDE.md` itself calls too large to
read at session start — while the coordination board, which every session DOES
read before its first substantive change, carried a true and incomplete note
whose practical effect was to license discounting three guards. So the remedy
has to arrive at the moment the condition is hit, from the tool, and not in
another document. `BL-20260910-...` rules out editing that board note (it is
TRUE, and treating a correct observation as the defect would discourage the
reporting this repo wants) and rules out another line in CLAUDE.md.

⚠️ AND `could_not_run` STILL FAILS THE RUN. A guard that could not run is not
a guard that passed, and in CI an absent runner means the image is broken. The
change is only that it can no longer be mistaken for a finding about the diff.

⚠️ THIS CONTAINER HAS pytest 9.1.1, import-linter AND ruff, so the condition
cannot be reached by being in it — every control below CONSTRUCTS it, by
hiding a binary from `PATH` or by pointing the probe at an interpreter that
does not have the module. A green run here is not evidence and is not treated
as any.

Run: ``python3 -m pytest tests/ci/test_run_guards_runner_absent.py``
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "run_guards.py"


def _load():
    spec = importlib.util.spec_from_file_location("_runguards", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RG = _load()


def _fresh():
    """A module with an EMPTY runner cache.

    The cache is process-wide and memoises a probe result, so a test that hid a
    binary after another test had already probed it would silently assert
    nothing.
    """
    m = _load()
    m._RUNNER_PRESENT.clear()
    return m


# ── THE DETECTION ───────────────────────────────────────────────────────────

def test_a_present_binary_is_not_reported_missing():
    """Negative control FIRST: a probe that says everything is absent would
    make every assertion below vacuously true."""
    m = _fresh()
    assert m.missing_runner(["ruff", "check", "."]) is None
    assert m.missing_runner(["python3", "-m", "pytest", "-q"]) is None


def test_an_absent_binary_is_named(monkeypatch):
    m = _fresh()
    monkeypatch.setattr(m.shutil, "which", lambda n: None)
    assert m.missing_runner(["ruff", "check", "."]) == "ruff"


def test_an_absent_MODULE_is_named(monkeypatch):
    """The pytest case, which is the one that actually bit."""
    m = _fresh()
    monkeypatch.setattr(m, "_module_importable", lambda interp, mod: False)
    assert m.missing_runner(["python3", "-m", "pytest", "-q"]) == "pytest"


def test_the_module_probe_asks_the_interpreter_the_step_will_USE():
    """`importlib.util.find_spec` in THIS process answers about THIS process's
    environment, which need not be the `python3` on PATH. Getting that wrong
    would report a runner present that the step cannot find — the same defect
    one level down."""
    m = _fresh()
    assert m._module_importable(sys.executable, "json") is True
    assert m._module_importable(sys.executable, "a_module_that_is_not_there") is False
    assert m._module_importable("/nonexistent/python", "json") is False, \
        "an interpreter that does not exist cannot have the module"


def test_an_unrecognised_shape_is_reported_PRESENT_not_absent():
    """Fail toward running the guard. Reporting an unknown shape as absent
    would convert a real failure into a silent `could_not_run`, which is
    strictly worse than the defect being fixed."""
    m = _fresh()
    assert m.missing_runner([]) is None


# ── THE REMEDY MUST REACH THE SESSION, which is the row's whole content ─────

def test_every_runner_the_registry_uses_has_a_recorded_remedy():
    """POPULATION: every step in the guard registry. A runner with no remedy
    leaves the next session to work it out, which is the routing failure."""
    used = set()
    for g in RG.GUARDS:
        for step in g["steps"]:
            argv = step if isinstance(step, list) else step.get("argv", [])
            if not argv:
                continue
            if argv[0] in ("python3", "python"):
                if len(argv) >= 3 and argv[1] == "-m":
                    used.add(argv[2])
            else:
                used.add(argv[0])
    # `python3` itself is the interpreter running this; it is not installable
    # from inside a run that is already happening.
    missing = {u for u in used if u not in RG.RUNNER_REMEDY} - {"python3", "python"}
    assert not missing, (
        f"these runners appear in the registry with no recorded remedy: "
        f"{sorted(missing)} — add them to RUNNER_REMEDY")


def test_the_remedy_is_the_one_command_the_backlog_already_recorded():
    assert RG.RUNNER_REMEDY["pytest"] == "pip install pytest"


# ── END TO END: the condition CONSTRUCTED, since this container cannot have it ─

def _run_guards(extra_env: dict, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, **extra_env}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=600)


def test_hiding_ruff_yields_COULD_NOT_RUN_the_remedy_and_a_NONZERO_exit(tmp_path):
    """The whole contract, observed rather than reasoned about.

    `PATH` is narrowed to a directory holding only the interpreter, so `ruff`
    is genuinely unreachable — the closest this container can get to the
    2026-09-10 condition.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for exe in ("python3", "git"):
        src = subprocess.run(["which", exe], capture_output=True, text=True).stdout.strip()
        if src:
            os.symlink(src, bin_dir / exe)
    out = _run_guards({"PATH": str(bin_dir)}, "--only", "ruff-lint", "--all")
    blob = out.stdout + out.stderr

    assert "COULD NOT RUN" in blob, blob[-3000:]
    assert "COULD-NOT-RUN 1" in blob, "the summary must count it in its own bucket"
    assert "FAIL 0" in blob, "it must NOT be counted as a failing guard"
    assert "pip install ruff" in blob, "the remedy must be printed at the point of failure"
    assert "not a finding about your diff" in blob.replace("\n", " "), \
        "the message must say what it is NOT, or it reads as a finding"
    assert out.returncode != 0, (
        "a guard that could not run is not a guard that passed — this must "
        "still fail the run")


def test_with_every_runner_present_nothing_is_graded_could_not_run():
    """The positive control for the end-to-end path. Without it, a probe that
    always reported `absent` would pass the test above and break every run."""
    out = _run_guards({}, "--only", "ruff-lint", "--all")
    blob = out.stdout + out.stderr
    assert "COULD-NOT-RUN 0" in blob, blob[-2000:]
    assert "COULD NOT RUN" not in blob.split("=" * 72)[-1]
    assert out.returncode == 0, blob[-2000:]
