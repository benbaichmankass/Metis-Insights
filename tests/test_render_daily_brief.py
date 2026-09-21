"""Tests for the DAILY BRIEF renderer (`scripts/ops/render_daily_brief.py`).

⚠️ **These are NOT a second copy of the module's `--self-test`.** The
self-test runs in CI on every PR and asserts the invariants that must never
regress (§0 is pipeline.py's own text; `landed_unproven` is never `done`; the
three read-states never collapse). These tests exercise what a self-test
cannot cheaply reach: real files on disk, the CLI's argument plumbing, and
the `--write` path.

The pre-reset version of this file tested the four-section module this one
replaces (`registerStates` keyed to `SESSIONS.json` / `OPEN-PRS.json` /
`MANAGER-LEASE.json` / `OPEN-ITEMS.json` / `CYCLE-PRIORITY.json`, all
archived under `docs/archive/2026-09-21-operating-reset/`). It is replaced
wholesale rather than patched, because the module's shape changed —
six sections, three inputs, no MCP-only observation boundary. Restore the old
file from git history if the old module's subject ever returns.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ops import pipeline  # noqa: E402
from scripts.ops import render_daily_brief as rdb  # noqa: E402


def test_the_modules_own_self_test_passes_as_a_subprocess():
    r = subprocess.run(
        [sys.executable, "scripts/ops/render_daily_brief.py", "--self-test"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_modules_own_check_passes_over_the_live_tree():
    r = subprocess.run(
        [sys.executable, "scripts/ops/render_daily_brief.py", "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── read_json / read_yaml, on real files ───────────────────────────────────

def test_read_json_distinguishes_absent_unreadable_read(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "bad.json").write_text("{oops", encoding="utf-8")
    (tmp_path / "d" / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    assert rdb.read_json(Path("d/missing.json"), tmp_path) == (None, "absent")
    assert rdb.read_json(Path("d/bad.json"), tmp_path)[1] == "unreadable"
    assert rdb.read_json(Path("d/ok.json"), tmp_path) == ({"a": 1}, "read")


def test_read_yaml_distinguishes_absent_unreadable_read(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "bad.yaml").write_text("a: [oops", encoding="utf-8")
    (tmp_path / "d" / "ok.yaml").write_text("mandates: []\n", encoding="utf-8")
    assert rdb.read_yaml(Path("d/missing.yaml"), tmp_path) == (None, "absent")
    assert rdb.read_yaml(Path("d/bad.yaml"), tmp_path)[1] == "unreadable"
    assert rdb.read_yaml(Path("d/ok.yaml"), tmp_path) == ({"mandates": []}, "read")


# ── build() against a real, isolated tree ──────────────────────────────────

def _write_pipeline_item(store: Path, **over):
    item = {
        "id": "PI-TEST-0001", "what": "a test item",
        "origin": {"kind": "audit", "ref": "#1", "rerun": "true"},
        "due_when": {"kind": "observation", "clears_when": "x"},
        "next_action": "dispatch_lane", "state": "queued",
        "routed_to": None, "terminal_reason": None,
    }
    item.update(over)
    store.parent.mkdir(parents=True, exist_ok=True)
    with open(store, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(item) + "\n")


def test_build_reads_all_three_inputs_from_an_isolated_root(tmp_path):
    pipeline_store = tmp_path / "docs/claude/work/PIPELINE.jsonl"
    _write_pipeline_item(pipeline_store)

    checklist = tmp_path / "docs/claude/work/MANAGER-CHECKLIST.json"
    checklist.parent.mkdir(parents=True, exist_ok=True)
    checklist.write_text(json.dumps({"items": [
        {"id": "X1", "state": "in_flight", "owner": "build lane", "title": "t"},
    ]}), encoding="utf-8")

    mandates = tmp_path / "config/mandates.yaml"
    mandates.parent.mkdir(parents=True, exist_ok=True)
    mandates.write_text("mandates:\n  - id: MD-KILL-QUESTION\n    grants: derisk_only\n",
                        encoding="utf-8")

    import datetime
    b = rdb.build(today=datetime.date(2026, 9, 21), root=tmp_path)
    assert b["checklistState"] == "read"
    assert b["mandatesState"] == "read"
    assert b["pipeline"]["healthy"] is True
    assert b["pipeline"]["stats"]["items"] == 1
    md = rdb.render(b)
    assert "MD-KILL-QUESTION" in md
    assert "- **X1**" in md
    assert "PI-TEST-0001" in md


def test_build_declares_all_three_inputs_absent_on_an_empty_tree(tmp_path):
    import datetime
    b = rdb.build(today=datetime.date(2026, 9, 21), root=tmp_path)
    assert b["checklistState"] == "absent"
    assert b["mandatesState"] == "absent"
    assert b["pipeline"]["healthy"] is True  # a missing store is empty, not broken
    md = rdb.render(b)
    assert md.count("we looked; it is not there") >= 3  # §2, §3, §4
    assert "no mandate mechanism exists yet" in md.lower()


# ── the adversarial direction: a malformed input must be DECLARED ─────────

def test_an_unparseable_checklist_is_declared_not_silently_empty(tmp_path):
    checklist = tmp_path / "docs/claude/work/MANAGER-CHECKLIST.json"
    checklist.parent.mkdir(parents=True, exist_ok=True)
    checklist.write_text("{not json", encoding="utf-8")
    import datetime
    b = rdb.build(today=datetime.date(2026, 9, 21), root=tmp_path)
    assert b["checklistState"] == "unreadable"
    md = rdb.render(b)
    assert md.count("we could not look") >= 3


# ── §0/§5 are pipeline.py's own numbers, never re-derived ─────────────────

def test_section0_and_section5_are_pipelines_own_numbers(tmp_path):
    store = tmp_path / "docs/claude/work/PIPELINE.jsonl"
    _write_pipeline_item(store, id="A", state="queued")
    _write_pipeline_item(store, id="B", state="routed", routed_to="A7",
                         terminal_reason=None)
    import datetime
    today = datetime.date(2026, 9, 21)
    b = rdb.build(today=today, root=tmp_path)
    res = pipeline.read_log(store)
    assert b["pipeline"]["stats"]["unrouted"] == pipeline.unrouted_count(
        res.items.values(), today)
    md = rdb.render(b)
    assert f"Unrouted pipeline items: {pipeline.unrouted_count(res.items.values(), today)}." in md


# ── the write path ──────────────────────────────────────────────────────

def test_write_produces_a_dated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rdb, "BRIEF_DIR", tmp_path / "briefs")
    import datetime
    b = rdb.build(today=datetime.date(2026, 9, 21), root=REPO_ROOT)
    out_dir = tmp_path / "briefs"
    out_dir.mkdir(parents=True)
    out = out_dir / f"{b['forDate']}.md"
    out.write_text(rdb.render(b), encoding="utf-8")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# DAILY BRIEF — ")
    assert "do not hand-edit" in text
    assert "## §0 — WHAT CAME DUE" in text


def test_the_cli_write_flag_actually_writes(tmp_path):
    r = subprocess.run(
        [sys.executable, "scripts/ops/render_daily_brief.py", "--write"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "daily-brief: wrote comms/briefs/" in r.stdout
    written = r.stdout.split("wrote ")[1].split(" ")[0]
    path = REPO_ROOT / written
    assert path.exists()
    try:
        text = path.read_text(encoding="utf-8")
        assert "## §0 — WHAT CAME DUE" in text
        assert "## §5 — SPEND" in text
    finally:
        path.unlink(missing_ok=True)


def test_the_cli_json_flag_emits_the_envelope():
    r = subprocess.run(
        [sys.executable, "scripts/ops/render_daily_brief.py", "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    env = json.loads(r.stdout)
    assert env["schemaVersion"] == 1
    assert env["coverageComplete"] is False
    assert "pipeline" in env and "checklistState" in env and "mandatesState" in env
