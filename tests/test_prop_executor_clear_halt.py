"""``breakout-login-check apply: executor-clear-halt`` (manager decision,
2026-09-28): clears the executor's AUTO-REVERT latch only with a reason, only
when a latch with a recorded reason exists; logs the prior reason, appends a
record, and moves the latch aside (never deletes it). Runs the real script's
branch against a stub ``_lib.sh`` in a temporary HOME."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUB_LIB = """
REPO_DIR="${REPO_DIR:-/nonexistent}"
log() { echo "[test] $*"; }
record_audit() { echo "audit $*" >> "${HOME}/audit.log"; }
load_runtime_env() { :; }
"""


@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "ops"
    d.mkdir()
    shutil.copy(REPO / "scripts" / "ops" / "breakout_login_check_action.sh", d / "action.sh")
    (d / "_lib.sh").write_text(STUB_LIB)
    home = tmp_path / "home"
    (home / ".cache" / "metis-prop-browser" / "executor").mkdir(parents=True)
    return d / "action.sh", home


def _run(script, home, reason="manager: trip investigated, cause fixed in #1", latch=None, actor="mgr",
         apply="executor-clear-halt"):
    halt = home / ".cache" / "metis-prop-browser" / "executor" / "halted"
    if latch is not None:
        halt.write_text(latch)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "ACTION_APPLY": apply,
           "ACTION_REASON": reason, "ACTION_ACTOR": actor, "ACTION_ISSUE": "42"}
    p = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=30)
    return p, halt


def test_clears_with_reason_and_records_the_prior(sandbox):
    script, home = sandbox
    p, halt = _run(script, home, latch="2026-09-28T21:00Z AUTO-REVERT: t1: partial_no_sl_tp\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert not halt.exists()
    moved = list(halt.parent.glob("halted.cleared-*"))
    assert len(moved) == 1 and "partial_no_sl_tp" in moved[0].read_text()
    rec = json.loads((halt.parent / "halt_clears.jsonl").read_text().splitlines()[-1])
    assert rec["actor"] == "mgr" and rec["issue"] == "42" and "cause fixed" in rec["reason"]
    assert "partial_no_sl_tp" in rec["prior"]
    assert "prior latch: 2026-09-28T21:00Z AUTO-REVERT" in p.stdout


def test_refuses_without_a_reason(sandbox):
    script, home = sandbox
    p, halt = _run(script, home, reason="  ", latch="AUTO-REVERT: x\n")
    assert p.returncode == 1 and halt.exists() and "reason is required" in p.stdout


def test_refuses_when_no_latch_is_set(sandbox):
    script, home = sandbox
    p, _ = _run(script, home)
    assert p.returncode == 1 and "nothing to clear" in p.stdout


def test_refuses_a_latch_with_no_recorded_reason(sandbox):
    script, home = sandbox
    p, halt = _run(script, home, latch="  \n")
    assert p.returncode == 1 and halt.exists() and "no recorded reason" in p.stdout


def test_the_workflow_allows_the_apply_and_requires_a_reason():
    wf = (REPO / ".github" / "workflows" / "system-actions.yml").read_text()
    # the token must sit in the apply allowlist group, wherever in the group
    import re
    assert re.search(r"\|executor-clear-halt[|)]", wf)
    assert "executor-clear-halt requires a 'reason:' line" in wf


def test_the_executor_never_clears_its_own_latch():
    src = (REPO / "src" / "prop" / "prop_executor.py").read_text()
    assert "halt_file.unlink" not in src and "halted.cleared" not in src


def _roll(home, text='{"state": "verified", "sl": 2710.0}'):
    roll = home / ".cache" / "metis-prop-browser" / "executor" / "modify_rollout.json"
    roll.write_text(text)
    return roll


def test_clears_the_modify_rollout_latch_only_by_naming_it(sandbox):
    script, home = sandbox
    roll = _roll(home)
    p, halt = _run(script, home, reason="manager: first modify observed and reviewed",
                   apply="executor-clear-rollout")
    assert p.returncode == 0, p.stdout + p.stderr
    assert not roll.exists() and not halt.exists()
    moved = list(roll.parent.glob("modify_rollout.json.cleared-*"))
    assert len(moved) == 1 and "verified" in moved[0].read_text()
    rec = json.loads((roll.parent / "halt_clears.jsonl").read_text().splitlines()[-1])
    assert rec["latch"] == "modify_rollout.json" and "verified" in rec["prior"]


def test_clearing_the_halt_leaves_the_rollout_latch_in_place(sandbox):
    # Clearing an unrelated AUTO-REVERT halt must never re-arm a modify step.
    script, home = sandbox
    roll = _roll(home)
    p, halt = _run(script, home, latch="AUTO-REVERT: t1: partial_no_sl_tp\n")
    assert p.returncode == 0, p.stdout + p.stderr
    assert not halt.exists() and roll.exists() and "verified" in roll.read_text()
    assert not list(roll.parent.glob("modify_rollout.json.cleared-*"))
    recs = [json.loads(x) for x in (roll.parent / "halt_clears.jsonl").read_text().splitlines()]
    assert [r["latch"] for r in recs] == ["halted"]


def test_clearing_the_rollout_leaves_the_halt_in_place(sandbox):
    script, home = sandbox
    roll = _roll(home)
    p, halt = _run(script, home, latch="AUTO-REVERT: t1: partial_no_sl_tp\n", apply="executor-clear-rollout")
    assert p.returncode == 0, p.stdout + p.stderr
    assert not roll.exists() and halt.exists() and "partial_no_sl_tp" in halt.read_text()
    assert not list(halt.parent.glob("halted.cleared-*"))


def test_clear_halt_with_only_the_rollout_latch_set_clears_nothing(sandbox):
    script, home = sandbox
    roll = _roll(home)
    p, _ = _run(script, home)
    assert p.returncode == 1 and "nothing to clear" in p.stdout and roll.exists()


def test_clear_rollout_refuses_without_a_reason(sandbox):
    script, home = sandbox
    roll = _roll(home)
    p, _ = _run(script, home, reason=" ", apply="executor-clear-rollout")
    assert p.returncode == 1 and roll.exists() and "reason is required" in p.stdout


def test_the_workflow_allows_clear_rollout_and_requires_its_reason():
    import re
    wf = (REPO / ".github" / "workflows" / "system-actions.yml").read_text()
    assert len(re.findall(r"\|executor-clear-rollout[|)]", wf)) == 2
    assert '*",executor-clear-halt,"*|*",executor-clear-rollout,"*)' in wf
