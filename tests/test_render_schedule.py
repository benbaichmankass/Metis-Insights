"""Tests for the WORK SCHEDULE renderer (`scripts/ops/render_schedule.py`).

⚠️ **These are NOT a second copy of the module's `--self-test`.** The
self-test runs in CI on every PR and asserts the invariants that must never
regress (decisions-vs-monitoring split, the cron reader's commented-out
negative control, the three read-states never collapsing). These tests
exercise what a self-test cannot cheaply reach: real files on disk and the
CLI's argument plumbing. Modelled on `tests/test_render_daily_brief.py`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ops import render_schedule as rs  # noqa: E402


def test_the_modules_own_self_test_passes_as_a_subprocess():
    r = subprocess.run(
        [sys.executable, "scripts/ops/render_schedule.py", "--self-test"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_modules_own_check_passes_over_the_live_tree():
    r = subprocess.run(
        [sys.executable, "scripts/ops/render_schedule.py", "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── read_json, on real files ───────────────────────────────────────────────

def test_read_json_distinguishes_absent_unreadable_read(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "bad.json").write_text("{oops", encoding="utf-8")
    (tmp_path / "d" / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    assert rs.read_json(Path("d/missing.json"), tmp_path) == (None, "absent")
    assert rs.read_json(Path("d/bad.json"), tmp_path)[1] == "unreadable"
    assert rs.read_json(Path("d/ok.json"), tmp_path) == ({"a": 1}, "read")


# ── the real SCHEDULE.json on disk — a positive control on live content ───

def test_the_committed_schedule_json_declares_all_four_cadenced_sessions():
    doc, state = rs.read_json(rs.SCHEDULE_DECL, REPO_ROOT)
    assert state == "read"
    sv = rs.cadenced_sessions(doc, state)
    ids = {s.get("id") for s in sv["sessions"]}
    assert ids == {"daily-sync", "health-review", "performance-review", "ml-review"}
    for s in sv["sessions"]:
        assert s.get("source"), f"{s.get('id')} carries no source citation"


# ── cron reader against the real .github/workflows tree — the guard this ──
# module exists to enforce: a commented-out cron must not appear armed.

def test_cron_reader_excludes_the_known_disarmed_workflow():
    c = rs.read_workflow_crons(REPO_ROOT)
    assert c["state"] in ("read", "partial")
    names = {w["workflow"] for w in c["workflows"]}
    # due-list.yml's cron is commented out (2026-09-21 operating-reset disarm)
    # — it must not appear as an armed schedule.
    assert "due-list.yml" not in names
    # health-snapshot.yml's cron is live.
    assert "health-snapshot.yml" in names


# ── build() against a real, isolated tree ──────────────────────────────────

def _write_pipeline_item(store: Path, **over):
    item = {
        "id": "PI-TEST-0001", "what": "a test item",
        "origin": {"kind": "audit", "ref": "#1", "rerun": "true"},
        "due_when": {"kind": "observation", "clears_when": "x"},
        "next_action": "ask_operator", "state": "queued",
        "routed_to": None, "terminal_reason": None,
    }
    item.update(over)
    store.parent.mkdir(parents=True, exist_ok=True)
    with open(store, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(item) + "\n")


def test_build_reads_all_three_inputs_from_an_isolated_root(tmp_path):
    pipeline_store = tmp_path / "docs/claude/work/PIPELINE.jsonl"
    _write_pipeline_item(pipeline_store)

    decl = tmp_path / "docs/claude/work/SCHEDULE.json"
    decl.parent.mkdir(parents=True, exist_ok=True)
    decl.write_text(json.dumps({"sessions": [
        {"id": "daily-sync", "title": "Daily sync", "cadence": "once per day",
         "source": "CLAUDE.md"},
    ]}), encoding="utf-8")

    wf_dir = tmp_path / ".github/workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "x.yml").write_text(
        "name: x\non:\n  schedule:\n    - cron: '0 0 * * *'\n", encoding="utf-8")

    import datetime
    b = rs.build(today=datetime.date(2026, 9, 21), root=tmp_path)
    assert b["cadencedSessions"]["state"] == "read"
    assert b["crons"]["state"] == "read"
    assert b["pipeline"]["healthy"] is True
    assert len(b["decisionsOwed"]) == 1
    md = rs.render(b)
    assert "Daily sync" in md
    assert "PI-TEST-0001" in md
    assert "0 0 * * *" in md


def test_build_declares_all_holes_on_an_empty_tree(tmp_path):
    import datetime
    b = rs.build(today=datetime.date(2026, 9, 21), root=tmp_path)
    assert b["cadencedSessions"]["state"] == "absent"
    assert b["crons"]["state"] == "absent"
    assert b["pipeline"]["healthy"] is True  # a missing store is empty, not broken
    md = rs.render(b)
    assert "we looked; it is not there" in md


# ── the adversarial direction: a malformed input must be DECLARED ─────────

def test_an_unparseable_schedule_json_is_declared_not_silently_empty(tmp_path):
    decl = tmp_path / "docs/claude/work/SCHEDULE.json"
    decl.parent.mkdir(parents=True, exist_ok=True)
    decl.write_text("{not json", encoding="utf-8")
    import datetime
    b = rs.build(today=datetime.date(2026, 9, 21), root=tmp_path)
    assert b["cadencedSessions"]["state"] == "unreadable"
    md = rs.render(b)
    assert "could not look" in md


# ── the split is pipeline.due(), never re-derived ──────────────────────────

def test_decisions_and_monitoring_are_pipelines_own_due_rows(tmp_path):
    store = tmp_path / "docs/claude/work/PIPELINE.jsonl"
    _write_pipeline_item(store, id="D", next_action="ask_operator")
    _write_pipeline_item(store, id="M", next_action="dispatch_lane")
    import datetime
    from scripts.ops import pipeline
    today = datetime.date(2026, 9, 21)
    b = rs.build(today=today, root=tmp_path)
    res = pipeline.read_log(store)
    due_ids = {i["id"] for i in pipeline.due(res.items.values(), today)}
    assert due_ids == {"D", "M"}
    assert {r["id"] for r in b["decisionsOwed"]} == {"D"}
    assert {r["id"] for r in b["monitoringDue"]} == {"M"}


# ── CLI plumbing ────────────────────────────────────────────────────────

def test_the_cli_json_flag_emits_the_envelope():
    r = subprocess.run(
        [sys.executable, "scripts/ops/render_schedule.py", "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    env = json.loads(r.stdout)
    assert env["schemaVersion"] == 1
    assert env["coverageComplete"] is False
    assert "cadencedSessions" in env and "decisionsOwed" in env and "crons" in env
