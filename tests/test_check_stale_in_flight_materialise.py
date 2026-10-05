"""_materialise_base must work when the retired objects dir is absent at base.

Regression: `git archive` named docs/claude/work/objects unconditionally, so on
any base without it the ratchet read `base_unreadable` forever.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_stale_in_flight as g  # noqa: E402


def _repo(tmp: Path, with_objects: bool) -> str:
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    cl = tmp / g.ol.CHECKLIST_RELPATH
    cl.parent.mkdir(parents=True)
    cl.write_text('{"items": []}')
    if with_objects:
        od = tmp / g.ol.OBJECTS_RELDIR
        od.mkdir(parents=True)
        (od / "x.yaml").write_text("lifecycle: done\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, check=True,
                          capture_output=True, text=True).stdout.strip()


def _run(monkeypatch, tmp_path, with_objects):
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    sha = _repo(src, with_objects)

    def fake_git(args, cwd=src):
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True)
        return p.returncode, p.stdout
    monkeypatch.setattr(g, "_git", fake_git)
    return g._materialise_base(sha, dest), dest


def test_materialise_without_objects_dir(monkeypatch, tmp_path):
    ok, dest = _run(monkeypatch, tmp_path, with_objects=False)
    assert ok, "base without the retired objects dir must still materialise"
    assert (dest / g.ol.CHECKLIST_RELPATH).is_file()


def test_materialise_with_objects_dir(monkeypatch, tmp_path):
    ok, dest = _run(monkeypatch, tmp_path, with_objects=True)
    assert ok
    assert (dest / g.ol.OBJECTS_RELDIR / "x.yaml").is_file()
