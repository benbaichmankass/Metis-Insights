"""`commit-to-main` must not stack a caller's second branch on its first.

PI-20261006-LCEVL8D5-0001 (lane RQ-INFRA, 2026-10-07). `research-queue-dispatch`
calls the action twice in one job (liveness receipt, then stamps). The replay
onto a fresh `main` used to run only when the job ran off `main`, so the stamp
branch carried the receipt commit under it (#16900: `commits: 2`,
`mergeable_state: dirty`). A conflicting PR gets no `pull_request` runs, so the
stamp never landed. These tests run the action's REAL replay block, sliced out of
action.yml, in a scratch repo -- not a copy of it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ACTION = Path(__file__).resolve().parents[1] / ".github/actions/commit-to-main/action.yml"
START = 'if [ "${GITHUB_REF:-refs/heads/main}" != "refs/heads/main" ]'
END = "# Push the throwaway branch."


def _block() -> str:
    text = ACTION.read_text(encoding="utf-8")
    assert START in text and END in text, "replay block moved -- update this slice"
    body = text[text.index(START):text.index(END)]
    # drop the trailing comment-only tail so the slice ends on the closing `fi`
    return body[:body.rindex("fi") + 2]


def _git(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(tmp: Path):
    origin = tmp / "origin.git"
    _git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp / "work"
    _git(tmp, "clone", "-q", str(origin), str(work))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(work, "config", k, v)
    (work / "receipt.json").write_text("0\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "base")
    _git(work, "push", "-q", "origin", "HEAD:main")
    return origin, work


def _run_block(work: Path, ref: str) -> None:
    script = "set -euo pipefail\nBR=automation/x\n" + _block()
    subprocess.run(["bash", "-c", script], cwd=work, check=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(work),
                        "GITHUB_REF": ref}, capture_output=True, text=True)


def _commit(work: Path, name: str, text: str) -> None:
    (work / name).write_text(text)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", name)


def test_second_call_in_a_job_is_replayed_onto_main_not_stacked(tmp_path: Path) -> None:
    origin, work = _repo(tmp_path)
    # call 1: receipt commit on main's tip; parent == main tip -> nothing to replay
    _commit(work, "receipt.json", "1\n")
    first = _git(work, "rev-parse", "HEAD")
    _run_block(work, "refs/heads/main")
    assert _git(work, "rev-parse", "HEAD") == first, "parent is main's tip: unchanged"
    # the receipt PR squash-merges and main moves on (a different sha, same content)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(other, "config", k, v)
    (other / "receipt.json").write_text("2\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "squash + later receipt")
    _git(other, "push", "-q", "origin", "HEAD:main")
    main_tip = _git(other, "rev-parse", "HEAD")
    # call 2: stamp commit on top of call 1's commit (the stacking)
    _commit(work, "stamp.yaml", "stamped\n")
    _run_block(work, "refs/heads/main")
    assert _git(work, "rev-parse", "HEAD~1") == main_tip, "stamp must sit on main's tip"
    changed = _git(work, "diff", "--name-only", "HEAD~1", "HEAD").split()
    assert changed == ["stamp.yaml"], f"stamp branch carries only its own paths: {changed}"


def test_a_caller_already_on_main_tip_is_not_touched(tmp_path: Path) -> None:
    _origin, work = _repo(tmp_path)
    _commit(work, "x.json", "1\n")
    before = _git(work, "rev-parse", "HEAD")
    _run_block(work, "refs/heads/main")
    assert _git(work, "rev-parse", "HEAD") == before


def test_the_condition_is_not_keyed_on_github_ref_alone() -> None:
    assert "git ls-remote origin refs/heads/main" in _block()
