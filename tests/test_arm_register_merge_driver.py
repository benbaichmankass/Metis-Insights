"""The register merge driver must be armed for a session that never thought to.

BL-20260906-REGISTER-MERGE-DRIVER-SHIPS-UNARMED-AND-A-SESSION-PAID-ELEVEN-HAND-RESOLUTIONS
asks for criterion (a) — "a repo bootstrap step runs install_merge_driver.sh, so
a fresh container arms itself" — and explicitly forbids closing it with a
reminder in a doc.

⚠️ THESE CONTROLS EXIST BECAUSE THE ARMING IS INVISIBLE WHEN IT WORKS. An
un-armed clone does not break: it merges the registers the old way and the cost
is paid later, by hand, by whoever merges next. So nothing about a green run
tells you the step happened — which is the same shape as the defect itself.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_run_guards_arm", REPO / "scripts" / "ci" / "run_guards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_decision_is_pure_and_its_states_are_not_collapsed():
    g = _load()
    # CI is skipped whatever the clone looks like: a runner's clone is thrown
    # away after one job, so arming it is a side effect with no beneficiary.
    for installed in (True, False, None):
        assert g.arm_decision(True, installed, True) == g.ARM_SKIPPED_CI
    assert g.arm_decision(False, True, True) == g.ARM_ALREADY
    assert g.arm_decision(False, False, True) == g.ARM_ARMED
    # No installer is a DIFFERENT fact from an un-armed clone we can fix.
    assert g.arm_decision(False, False, False) == g.ARM_NO_INSTALLER


def test_an_unreadable_git_config_is_never_banked_as_armed():
    """`we could not look` must not read as `it is fine` — and must not run an
    installer over a state nobody saw either."""
    g = _load()
    assert g.arm_decision(False, None, True) == g.ARM_UNKNOWN
    assert g.arm_decision(False, None, True) != g.ARM_ALREADY
    assert g.arm_decision(False, None, True) != g.ARM_ARMED


def test_the_installer_really_arms_a_clone_that_is_not_armed(tmp_path):
    """END TO END against a real git repo, not a mock.

    The whole claim is about git's own config, so a fake would assert only that
    the author believed the shell script works.
    """
    repo = tmp_path / "clone"
    (repo / "scripts" / "ops").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    src = REPO / "scripts" / "ops" / "install_merge_driver.sh"
    (repo / "scripts" / "ops" / "install_merge_driver.sh").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "scripts" / "ops" / "merge_json_register.py").write_text(
        "", encoding="utf-8")

    def driver() -> str:
        return subprocess.run(
            ["git", "config", "--get", "merge.jsonregister.driver"],
            cwd=repo, capture_output=True, text=True).stdout.strip()

    assert driver() == "", "a fresh clone must start un-armed, or the control is inert"
    rc = subprocess.run(["bash", "scripts/ops/install_merge_driver.sh"],
                        cwd=repo, capture_output=True, text=True).returncode
    assert rc == 0
    got = driver()
    assert got, "the installer did not arm the clone"
    assert "merge_json_register.py" in got, got


def test_run_guards_actually_calls_the_arming_step():
    """WIRING. Every control above grades a function nothing calls unless this
    passes — the `registered but never executed` shape."""
    g = _load()
    seen = []
    g.arm_register_merge_driver = lambda: seen.append(1) or "already_armed"
    g.main(["--list"])
    assert seen == [1], "run_guards.main() no longer arms the clone"
