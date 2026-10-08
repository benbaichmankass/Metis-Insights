"""scripts/ops/checklist.py -- the per-row checklist store (PI-20261004-APBY4NTV-0003)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import checklist as C  # noqa: E402

REAL = Path(__file__).resolve().parents[1]


def _real():
    """The live checklist in monolith shape. The monolith FILE was deleted at the seed
    cutover, so the real data is the committed per-row store, rendered back."""
    return C.load(REAL)


def _write_real_monolith(repo):
    """Materialise the real checklist as a monolith file under ``repo`` (the pre-cutover
    layout), byte-for-byte what the generator renders."""
    (repo / C.MONOLITH.parent).mkdir(parents=True, exist_ok=True)
    (repo / C.MONOLITH).write_text(C.render(REAL), encoding="utf-8")


def test_self_test_passes():
    assert C._self_test() == 0


def test_the_real_monolith_round_trips_byte_for_byte(tmp_path):
    _write_real_monolith(tmp_path)
    n = C.seed(repo=tmp_path)
    assert n == len(_real()["items"])
    assert C.check(tmp_path) == []
    assert C.load(tmp_path) == _real()


def test_two_branches_adding_different_rows_touch_different_files(tmp_path):
    _write_real_monolith(tmp_path)
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
    _write_real_monolith(tmp_path)
    want = _real()
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


def test_load_at_reads_both_layouts_from_git_history(tmp_path):
    import subprocess

    def sh(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    sh("init", "-q", "-b", "main")
    sh("config", "user.email", "t@t")
    sh("config", "user.name", "t")
    (tmp_path / C.MONOLITH.parent).mkdir(parents=True)
    mono = {"schema_version": 2, "items": [{"id": "B", "state": "queued"}, {"id": "A", "state": "done"}]}
    (tmp_path / C.MONOLITH).write_text(json.dumps(mono, indent=2) + "\n")
    sh("add", "-A")
    sh("commit", "-qm", "monolith")
    C.seed(repo=tmp_path)
    (tmp_path / C.MONOLITH).unlink()
    C.write_row({"id": "C", "state": "queued"}, tmp_path)
    sh("add", "-A")
    sh("commit", "-qm", "rows")
    assert C.load_at(tmp_path, "HEAD~1") == mono, "monolith era"
    now = C.load_at(tmp_path, "HEAD")
    assert [r["id"] for r in now["items"]] == ["B", "A", "C"], "store era, order preserved"
    assert now == C.load(tmp_path)
    assert C.load_at(tmp_path, "nonexistent-ref") is None, "an unknown ref is absent, not an error"


# ── the write CLI (manager recipe: every register edit goes through it) ─────
def _seeded_repo(tmp_path):
    _write_real_monolith(tmp_path)
    C.seed(repo=tmp_path)
    return tmp_path


def _cli(tmp_path, *args):
    import importlib

    cl = importlib.import_module("checklist")
    return cl.main(["--repo", str(tmp_path), *args])


def test_cli_add_set_note_archive_round_trip_and_never_touches_the_header(tmp_path, capsys):
    repo = _seeded_repo(tmp_path)
    hdr = (repo / C.STORE / "_header.json").read_bytes()
    assert _cli(repo, "add", "NEW-ROW", "--title", "t", "--state", "queued", "--owner", "me",
                "--tier", "1", "--prs", "5,6", "--by", "session_x") == 0
    row = json.loads((repo / C.STORE / "NEW-ROW.json").read_text())
    assert row["tier"] == 1 and row["prs"] == [5, 6] and row["updated_by"] == "session_x" and row["updated_at"]
    assert _cli(repo, "set", "NEW-ROW", "state=in_flight", "lane=session_abc", 'ceiling_usd=12.5') == 0
    assert C.read_row("NEW-ROW", repo)["ceiling_usd"] == 12.5
    assert _cli(repo, "note", "NEW-ROW", "first") == 0
    assert _cli(repo, "note", "NEW-ROW", "second", "--append") == 0
    assert C.read_row("NEW-ROW", repo)["note"] == "first second"
    assert _cli(repo, "archive-lane", "NEW-ROW") == 0
    r = C.read_row("NEW-ROW", repo)
    assert r["lane"] is None and r["lane_history"][0]["lane"] == "session_abc"
    assert [x["id"] for x in C.load(repo)["items"]][-1] == "NEW-ROW", "a new row appends at the end"
    assert (repo / C.STORE / "_header.json").read_bytes() == hdr, "row edits must not touch the shared header"
    assert not list((repo / C.STORE).glob(".*.tmp")), "no temp file left behind"


def test_cli_refuses_bad_state_duplicate_id_unknown_row_and_unseeded_store(tmp_path, capsys):
    repo = _seeded_repo(tmp_path)
    assert _cli(repo, "add", "X1", "--title", "t") == 0
    assert _cli(repo, "add", "X1", "--title", "t") == 1, "duplicate id"
    assert _cli(repo, "set", "X1", "state=wip-ish") == 1, "state outside the file's own vocabulary"
    assert C.read_row("X1", repo)["state"] == "queued", "a refused edit writes nothing"
    assert _cli(repo, "set", "NOPE", "state=done") == 1
    assert _cli(repo, "add", "../evil", "--title", "t") == 1
    assert _cli(repo, "archive-lane", "X1") == 1, "no lane to archive"
    assert _cli(repo, "add", "X2") == 1, "add needs a title"
    fresh = tmp_path / "unseeded"
    fresh.mkdir()
    assert _cli(fresh, "add", "Y", "--title", "t") == 1, "refused until the cutover has seeded the store"


def test_cli_header_sets_one_top_level_key(tmp_path):
    repo = _seeded_repo(tmp_path)
    assert _cli(repo, "header", "updated_by", "session_z") == 0
    assert C.load(repo)["updated_by"] == "session_z"
    assert [r["id"] for r in C.load(repo)["items"]] == [r["id"] for r in _real()["items"]]


def test_check_after_the_cutover_reports_the_store_instead_of_crashing(capsys):
    """`--check` used to read the deleted monolith and die with FileNotFoundError; with the
    monolith gone it must say the rows are the register and that they are readable."""
    import importlib

    cl = importlib.import_module("checklist")
    assert not (REAL / C.MONOLITH).exists(), "the monolith is retired -- this tests the post-cutover path"
    assert cl.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "monolith retired" in out and "readable" in out
