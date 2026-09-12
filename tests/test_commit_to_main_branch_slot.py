"""`commit-to-main` must take R13's PER-BRANCH slot route, never the shared field.

⚠️ **27 WORKFLOWS CALL THIS ONE ACTION**, so every automation PR wrote the same
four lines of `docs/claude/session-board.json` and dirtied every other
board-touching branch.

**MEASURED on `origin/main` 2026-09-12. POPULATION: the last 40 commits.**
18 touch `session-board.json` and **all 18** change the `merge_slot` lines —
**including all 3 automation commits**. That is the mechanism
`BL-20260908-R13-MAKES-EVERY-AUTOMATION-PR-WRITE-THE-MERGE-SLOT-SO-MAIN-CAN-NEVER-BE-QUIET`
measured at **seventeen** consecutive conflict cycles on one PR: a branch whose
only conflict is those four lines cannot converge, because the arrival rate of
board-touching commits exceeds a CI window.

⚠️ **THESE TESTS RUN THE REAL SHELL**, extracted from the action and executed
against a real git repo with the real `claim_merge_slot.py`. A test that grepped
the YAML for `--branch-claim` would pass on a step whose `git add` still staged
the board, and on one that wrote no claim at all.

⚠️ **AND THE EXTRACTION IS ITSELF ASSERTED.** A rename that moved the step would
otherwise leave every test below exercising nothing and reporting green.

Run: ``python3 -m pytest tests/test_commit_to_main_branch_slot.py``
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ACTION = REPO / ".github" / "actions" / "commit-to-main" / "action.yml"
SLOT_DIR = ".github/merge-slots"
BOARD = "docs/claude/session-board.json"


def _claim_block() -> str:
    """The claim + stage lines, straight from the action."""
    src = ACTION.read_text(encoding="utf-8")
    start = src.index("python3 scripts/ops/claim_merge_slot.py")
    end = src.index("git commit -m", start)
    return src[start:end]


def test_the_claim_block_is_actually_found():
    """Guards the extraction: a rename leaves every test below proving nothing."""
    b = _claim_block()
    assert "claim_merge_slot.py" in b
    assert "git add --" in b


def test_it_takes_the_per_branch_route_not_the_shared_field():
    b = _claim_block()
    assert "--branch-claim" in b, (
        "the claim must take the per-branch route; the shared `merge_slot` field "
        "is what made every automation PR dirty every other board-touching branch")
    assert BOARD not in b, (
        f"{BOARD} is still being staged by the claim step — the whole point is "
        f"that this action stops touching it")


def test_it_stages_the_directory_rather_than_re_deriving_the_slug():
    """A second slug derivation is how the two drift apart later."""
    b = _claim_block()
    assert f'"{SLOT_DIR}"' in b, (
        "stage the merge-slots DIRECTORY, so whatever the script wrote is what "
        "gets staged; re-deriving the filename here is a second rule")


def test_it_refuses_when_no_claim_file_landed():
    """An exit code is a claim about the CALL; the file is the claim about the
    RESULT. A step that reports success for something it did not achieve is the
    exact class this action's own header exists to end."""
    b = _claim_block()
    assert "git status --porcelain -- .github/merge-slots/" in b
    assert "exit 1" in b


def _mkrepo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    (r / "scripts" / "ops").mkdir(parents=True)
    (r / "docs" / "claude").mkdir(parents=True)
    (r / ".github" / "pr-landing").mkdir(parents=True)
    (r / ".github" / "pr-automerge-requests").mkdir(parents=True)
    for name in ("claim_merge_slot.py", "merge_slot_state.py"):
        src = REPO / "scripts" / "ops" / name
        if src.is_file():
            (r / "scripts" / "ops" / name).write_bytes(src.read_bytes())
    (r / BOARD).write_bytes((REPO / BOARD).read_bytes())
    for cmd in (["init", "-q"], ["config", "user.email", "a@b.invalid"],
                ["config", "user.name", "a"], ["add", "-A"],
                ["commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(r)] + cmd, check=True,
                       capture_output=True)
    return r


def _run_block(root: Path, branch: str) -> subprocess.CompletedProcess:
    slug = branch.removeprefix("claude/").replace("/", "-")
    (root / ".github" / "pr-landing" / f"{slug}.json").write_text(
        json.dumps({"tier": 1, "landing": "self", "why": "test"}))
    (root / ".github" / "pr-automerge-requests" / f"{slug}.txt").write_text("x\n")
    script = f'set -e\nBR={branch!r}\nSLUG={slug!r}\n' + _claim_block()
    return subprocess.run(["bash", "-c", script], cwd=root,
                          capture_output=True, text=True)


def test_two_concurrent_automation_claims_do_not_touch_the_same_bytes(tmp_path):
    """The whole finding, reproduced: two automation runs in flight at once."""
    root = _mkrepo(tmp_path)
    board_before = (root / BOARD).read_text(encoding="utf-8")
    for br in ("automation/data-commit-34999999999-1",
               "automation/work-digest-34888888888-1"):
        p = _run_block(root, br)
        assert p.returncode == 0, f"{br}: {p.stderr}"
    written = sorted(f.name for f in (root / SLOT_DIR).glob("*.json"))
    assert written == ["automation-data-commit-34999999999-1.json",
                       "automation-work-digest-34888888888-1.json"], written
    assert (root / BOARD).read_text(encoding="utf-8") == board_before, (
        "the shared board must be byte-identical — that it is unchanged is the "
        "property that stops two automation runs conflicting")


def test_the_claim_it_writes_names_its_own_branch(tmp_path):
    """R13 grades the claim's BODY too; a file in the right place carrying the
    wrong branch is not a claim this branch holds."""
    root = _mkrepo(tmp_path)
    br = "automation/data-commit-34999999999-1"
    assert _run_block(root, br).returncode == 0
    doc = json.loads(
        (root / SLOT_DIR / "automation-data-commit-34999999999-1.json").read_text())
    assert doc.get("branch") == br
    assert str(doc.get("held_by") or "").strip(), "an unattributable claim is not a claim"
    assert str(doc.get("claimed_at") or "").strip(), "a claim nobody can time out is not a claim"


def test_it_fails_loudly_when_the_claim_script_writes_nothing(tmp_path):
    """The refusal path, exercised rather than read. A stubbed script that exits
    0 and writes nothing must NOT be reported as a successful claim."""
    root = _mkrepo(tmp_path)
    (root / "scripts" / "ops" / "claim_merge_slot.py").write_text(
        "import sys\nsys.exit(0)\n")
    p = _run_block(root, "automation/data-commit-34999999999-1")
    assert p.returncode != 0, (
        "a script that exits 0 without writing a claim must fail the step — "
        "otherwise the PR is opened holding no R13 claim and can never merge")
    assert "REFUSING" in (p.stdout + p.stderr)
