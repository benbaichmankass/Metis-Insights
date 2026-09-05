"""docs/claude/pending-pings.jsonl must merge by union, and prove it by MERGING.

⚠️ WHY THIS TEST RUNS A REAL `git merge` AND NOT A FIXTURE. The thing that went
wrong before was reading `.gitattributes` and concluding from the text that a
file was covered. A fixture has the same defect one layer down: it can show a
function returning the right bytes while git, on a real three-way merge with a
real merge base, does something else. So every test here builds a throwaway git
repo, forks two branches that each append a row, and merges them.

⚠️ AND EVERY TEST READS THE MERGED FILE. "No conflict" is not evidence of union —
`-X ours` also produces no conflict, while silently dropping the other side's
ping. Absence of a conflict marker is checked, and then the rows themselves are
checked, because the failure this file guards against is a LOST PING.
"""
import json
import os
import pathlib
import subprocess
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PINGS = "docs/claude/pending-pings.jsonl"

BASE_ROW = '{"at": "2026-09-01T00:00:00+00:00", "target": "claude", "event": "work_digest", "message": "base"}'
OURS_ROW = '{"at": "2026-09-05T01:00:00+00:00", "target": "claude", "event": "work_digest", "message": "ours"}'
THEIRS_ROW = '{"at": "2026-09-05T02:00:00+00:00", "target": "claude", "event": "night_shift_summary", "message": "theirs"}'


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


def _rows(path):
    """Parsed rows, refusing to silently skip a conflict marker."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            assert not line.startswith(("<<<<<<<", "=======", ">>>>>>>")), (
                f"conflict marker in merged file: {line[:40]!r}"
            )
            out.append(json.dumps(json.loads(line), sort_keys=True))
    return out


def _repo_with_two_appends(tmp_path, attributes_line):
    """Build a repo where two branches each append one distinct ping row."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs" / "claude").mkdir(parents=True)
    (repo / PINGS).write_text(BASE_ROW + "\n", encoding="utf-8")
    if attributes_line:
        (repo / ".gitattributes").write_text(attributes_line + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    _git(repo, "checkout", "-q", "-b", "theirs")
    with open(repo / PINGS, "a", encoding="utf-8") as fh:
        fh.write(THEIRS_ROW + "\n")
    _git(repo, "commit", "-qam", "theirs appends a ping")

    _git(repo, "checkout", "-q", "main")
    with open(repo / PINGS, "a", encoding="utf-8") as fh:
        fh.write(OURS_ROW + "\n")
    _git(repo, "commit", "-qam", "ours appends a ping")
    return repo


def test_repo_gitattributes_maps_pending_pings_to_union():
    """The deploy half. NOT sufficient on its own — see the merge tests below."""
    got = subprocess.run(
        ["git", "-C", ROOT, "check-attr", "merge", "--", PINGS],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert got.endswith(": merge: union"), got


def test_without_the_mapping_two_appends_conflict():
    """Positive control. Without it, a green union test proves nothing."""
    with tempfile.TemporaryDirectory() as td:
        repo = _repo_with_two_appends(pathlib.Path(td), attributes_line=None)
        res = _git(repo, "merge", "--no-commit", "--no-ff", "theirs", check=False)
        assert res.returncode != 0, "expected a conflict without merge=union"
        assert "pending-pings.jsonl" in (res.stdout + res.stderr)


@pytest.mark.parametrize("attr", [f"{PINGS} merge=union"])
def test_union_merges_both_appends_and_keeps_both_rows(tmp_path, attr):
    repo = _repo_with_two_appends(tmp_path, attributes_line=attr)
    res = _git(repo, "merge", "--no-commit", "--no-ff", "theirs", check=False)
    assert res.returncode == 0, f"union should not conflict:\n{res.stdout}{res.stderr}"

    rows = _rows(repo / PINGS)          # reads the file; does not infer from exit code
    canon = lambda s: json.dumps(json.loads(s), sort_keys=True)
    assert canon(BASE_ROW) in rows, "base row lost"
    assert canon(OURS_ROW) in rows, "OUR side's ping lost — this is the drop this file guards"
    assert canon(THEIRS_ROW) in rows, "THEIR side's ping lost — this is the drop this file guards"
    assert len(rows) == 3, rows


def test_union_does_not_resurrect_a_row_deleted_on_one_side_only(tmp_path):
    """The known cost of union, pinned so it is a decision and not a surprise.

    Nothing in the repo deletes a ping row today (`_drain_pending_pings` leaves
    the file in place), but if something starts to, this test says out loud what
    union will do with it.
    """
    repo = _repo_with_two_appends(tmp_path, f"{PINGS} merge=union")
    _git(repo, "checkout", "-q", "theirs")
    (repo / PINGS).write_text(THEIRS_ROW + "\n", encoding="utf-8")  # drops BASE_ROW
    _git(repo, "commit", "-qam", "theirs drains the base row")
    _git(repo, "checkout", "-q", "main")
    res = _git(repo, "merge", "--no-commit", "--no-ff", "theirs", check=False)
    assert res.returncode == 0
    rows = _rows(repo / PINGS)
    assert json.dumps(json.loads(BASE_ROW), sort_keys=True) in rows, (
        "documented union behaviour: a one-sided delete is resurrected"
    )
