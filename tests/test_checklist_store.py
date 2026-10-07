"""scripts/ops/checklist.py -- the per-row checklist store (PI-20261004-APBY4NTV-0003)."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import checklist as C  # noqa: E402

REAL = Path(__file__).resolve().parents[1]


def test_self_test_passes():
    assert C._self_test() == 0


def test_the_real_monolith_round_trips_byte_for_byte(tmp_path):
    (tmp_path / C.MONOLITH.parent).mkdir(parents=True)
    shutil.copy(REAL / C.MONOLITH, tmp_path / C.MONOLITH)
    n = C.seed(repo=tmp_path)
    assert n == len(json.loads((REAL / C.MONOLITH).read_text())["items"])
    assert C.check(tmp_path) == []
    assert C.load(tmp_path) == json.loads((REAL / C.MONOLITH).read_text())


def test_two_branches_adding_different_rows_touch_different_files(tmp_path):
    (tmp_path / C.MONOLITH.parent).mkdir(parents=True)
    shutil.copy(REAL / C.MONOLITH, tmp_path / C.MONOLITH)
    C.seed(repo=tmp_path)
    a = C.write_row({"id": "NEW-A", "title": "a", "state": "queued"}, tmp_path)
    b = C.write_row({"id": "NEW-B", "title": "b", "state": "queued"}, tmp_path)
    assert a != b and a.parent == b.parent


def test_readers_return_the_same_data_before_and_after_the_cutover(tmp_path, monkeypatch):
    """The b1 readers must serve identical data from the monolith and from the
    seeded store, and keep serving it after the monolith file is deleted."""
    import importlib

    from src.runtime import manager_status as ms

    rel = C.MONOLITH
    (tmp_path / rel.parent).mkdir(parents=True)
    shutil.copy(REAL / rel, tmp_path / rel)
    want = json.loads((REAL / rel).read_text())
    path = tmp_path / rel

    sr = importlib.import_module("session_registry")
    mw = importlib.import_module("manager_wake")
    rdb = importlib.import_module("render_daily_brief")

    def read_all():
        return [
            ms.read_json_file(path).data,
            sr.read_json(path)[0],
            mw._load_json(path)[0],
            rdb.read_json(rel, tmp_path)[0],
        ]

    assert all(d == want for d in read_all()), "monolith path"
    C.seed(repo=tmp_path)
    assert all(d == want for d in read_all()), "seeded store, monolith still present"
    path.unlink()
    assert all(d == want for d in read_all()), "monolith deleted: rows are the truth"
    assert ms.read_json_file(tmp_path / "docs/claude/work/NOPE" / rel.name).state == "absent"
