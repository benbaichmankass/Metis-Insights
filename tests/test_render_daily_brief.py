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
    pipeline.append(item, store, intent="new")


def test_build_reads_all_three_inputs_from_an_isolated_root(tmp_path):
    pipeline_store = tmp_path / pipeline.STORE
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
    store = tmp_path / pipeline.STORE
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


# ── BRIEF-FIX (2026-10-04): ranked, capped, truthful ───────────────────────
from datetime import date  # noqa: E402

TODAY = date(2026, 10, 4)


def _pi(i, *, state="queued", action="dispatch_lane", what="w", **extra):
    return {"id": f"PI-20260{900 + i % 90:03d}-T-{i:04d}", "what": what, "state": state,
            "origin": {"kind": "audit", "ref": "r", "rerun": "r"},
            "due_when": {"kind": "observation", "clears_when": "c"},
            "next_action": action, "routed_to": "LANE" if state == "routed" else None,
            "terminal_reason": None, **extra}


def _brief(items, ck_items, *, activity=None, soaks=None):
    res = pipeline.LoadResult(items={i["id"]: i for i in items}, records=len(items))
    ask = sorted((i for i in pipeline.due(items, TODAY) if i["next_action"] == "ask_operator"),
                 key=rdb._rank_key)
    return {
        "forDate": TODAY.isoformat(), "generatedAt": "x", "coverageComplete": False,
        "pipeline": {"stats": pipeline.stats(res, TODAY),
                     "section0Lines": rdb.section0_lines(res, TODAY),
                     "healthy": True, "askOperator": ask},
        "checklistState": "read", "checklist": rdb.checklist_view({"items": ck_items}),
        "mandatesState": "absent", "mandates": rdb.mandates_view(None, "absent"),
        "activity": activity or {"state": "read", "commits": [], "coversDays": 30},
        "soaks": soaks or {"state": "absent", "rows": [], "asOf": None},
    }


def test_brief_stays_under_the_size_cap_on_a_worst_case_population():
    big = "x" * 4000
    items = [_pi(i, what=big, state="routed" if i % 2 else "queued",
                 action="ask_operator" if i < 25 else "dispatch_lane") for i in range(700)]
    ck = ([{"id": f"D{i}", "state": "done", "owner": "m", "title": big, "note": big}
           for i in range(300)]
          + [{"id": f"L{i}", "state": "landed_unproven", "owner": "m", "title": big, "note": big}
             for i in range(100)]
          + [{"id": f"F{i}", "state": "in_flight", "owner": "m", "title": big, "note": big}
             for i in range(25)]
          + [{"id": f"O{i}", "state": "queued", "owner": "operator", "title": big, "note": big}
             for i in range(6)])
    md = rdb.render(_brief(items, ck))
    assert len(md.encode()) <= rdb.BRIEF_SIZE_CAP_BYTES, len(md.encode())
    # the scale is still visible with its denominator
    assert "700 item(s) due" in md and "never-checked" in md


def test_due_bucket_agrees_with_is_due_and_names_each_branch():
    cases = {
        "never-checked": {"kind": "observation", "check_every_days": 7},
        "lapsed": {"kind": "observation", "check_every_days": 7, "last_checked": "2026-09-01"},
        "date-passed": {"kind": "date", "due_date": "2026-09-01"},
        "no-cadence": {"kind": "observation"},
        None: {"kind": "observation", "check_every_days": 7, "last_checked": "2026-10-03"},
    }
    for want, dw in cases.items():
        it = _pi(1, due_when=dw)
        assert pipeline.due_bucket(it, TODAY) == want
        assert (want is not None) == pipeline.is_due(it, TODAY)


def test_section0_ranks_money_path_then_ask_operator_then_oldest():
    items = [_pi(i, what=f"plain{i}") for i in range(30)]
    items.append(_pi(31, what="ASKME", action="ask_operator"))
    items.append(_pi(32, what="MONEY", severity="high"))
    md = rdb.render(_brief(items, []))
    s0 = md.split("## §0")[1].split("## §1")[0]
    assert s0.index("MONEY") < s0.index("ASKME") < s0.index("plain")
    assert "17 more due, not shown" in s0 and "pipeline.py --due --all" in s0


def test_section2_includes_pipeline_ask_operator_items():
    items = [_pi(1, what="NEEDSYOU", action="ask_operator"), _pi(2, what="not-for-you")]
    md = rdb.render(_brief(items, []))
    s2 = md.split("## §2")[1].split("## §3")[0]
    assert "NEEDSYOU" in s2 and "not-for-you" not in s2 and "(0 checklist + 1 pipeline)" in s2


def _ck_row(id_, stamp):
    return {"id": id_, "state": "in_flight", "owner": "m", "title": id_, "lane": f"session_{id_}",
            "note": f"[{stamp}T10:00Z mgr] update" if stamp else "no stamp here, deadline 2026-10-20"}


def test_section4_flags_stale_rows_and_a_future_date_is_not_activity():
    ck = [_ck_row("FRESH", "2026-10-03"), _ck_row("OLD", "2026-09-20"), _ck_row("BARE", None)]
    s4 = rdb.render(_brief([], ck)).split("## §4")[1].split("## §5")[0]
    live, stale = s4.split("STALE — no live evidence")
    assert "FRESH" in live and "OLD" not in live
    assert "OLD" in stale.split("BLOCKED")[0] and "BARE" in stale.split("BLOCKED")[0]
    assert "no stamp or commit found" in stale


def test_a_commit_naming_the_lane_rescues_a_stale_row_and_short_history_is_declared():
    ck = [_ck_row("OLD", "2026-09-20")]
    act = {"state": "short", "coversDays": 1,
           "commits": [(TODAY, "chore: tick (#1)", "lane session_OLD finished a step")]}
    s4 = rdb.render(_brief([], ck, activity=act)).split("## §4")[1]
    assert "(git commit)" in s4.split("### 🕸️")[0] and "not provable" in s4


def test_soak_slot_absent_read_and_missing_fields():
    absent = rdb.render(_brief([], []))
    assert "source absent (we looked" in absent and "not 'no soaks'" in absent
    soaks = {"state": "read", "asOf": "2026-10-04", "rows": [
        {"leg": "L1", "account": "bybit_1", "state": "accruing", "days_in": 3}]}
    md = rdb.render(_brief([], [], soaks=soaks))
    assert "L1" in md and "day 3" in md and "ends —" in md and "ends 0" not in md
    assert "accruing 1" in md


def test_read_soaks_imports_soak_state_live(tmp_path, monkeypatch):
    import types
    mod = types.ModuleType("scripts.ops.soak_state")
    mod.soak_states = lambda: [{"leg": "L", "account": "a", "state": "overdue",
                                "end_date": "2026-10-01", "reason": "r", "started": "2026-10-01"}]
    monkeypatch.setitem(sys.modules, "scripts.ops.soak_state", mod)
    got = rdb.read_soaks(TODAY)
    assert got["state"] == "read" and got["rows"][0]["days_in"] == 3
    mod.soak_states = lambda: 1 / 0
    assert rdb.read_soaks(TODAY)["state"] == "unreadable"
    monkeypatch.setitem(sys.modules, "scripts.ops.soak_state", None)  # import raises
    assert rdb.read_soaks(TODAY)["state"] in ("absent", "unreadable")


def test_git_activity_is_cached_on_head_sha(monkeypatch):
    calls = []
    monkeypatch.setattr(rdb, "_head_sha", lambda root: "abc")
    monkeypatch.setattr(rdb, "_git_activity_uncached",
                        lambda root, today, days=45: calls.append(1) or {"state": "read", "commits": [], "coversDays": 9})
    rdb._ACTIVITY_CACHE.clear()
    rdb.git_activity(REPO_ROOT, TODAY)
    rdb.git_activity(REPO_ROOT, TODAY)
    assert len(calls) == 1
    monkeypatch.setattr(rdb, "_head_sha", lambda root: "def")
    rdb.git_activity(REPO_ROOT, TODAY)
    assert len(calls) == 2
    rdb._ACTIVITY_CACHE.clear()


def test_ranked_items_is_the_order_section0_uses():
    items = [_pi(1, what="plain"), _pi(2, what="MONEY", severity="high")]
    res = pipeline.LoadResult(items={i["id"]: i for i in items}, records=2)
    assert [i["what"] for i in rdb.ranked_items(res, TODAY)] == ["MONEY", "plain"]
