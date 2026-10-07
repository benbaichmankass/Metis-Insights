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
