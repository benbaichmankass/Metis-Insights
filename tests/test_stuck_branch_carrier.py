"""The stuck-automation-branch probe has a CARRIER, and the carrier can report.

`BL-20260912-THE-STUCK-BRANCH-PROBE-IS-WIRED-TO-NOTHING-SO-181-STRANDED-AUTOMATION-BRANCHES-ARE-SEEN-BY-NOBODY`.

The probe was correct and its `--self-test` had always passed. What was absent
was anything that RAN it, so a measured 184 stranded `automation/*` branches
(full clone, 2026-09-12) were knowable and known by nobody. These tests pin the
four things that make the carrier a carrier rather than a second unread tool:

1. it runs on a schedule, from the ONE workflow that checks out `fetch-depth: 0`
   (on a shallow clone the probe manufactures a full population of false
   findings — measured: `stuck 184 · unknown 0` where the truth is unknowable);
2. its receipt actually LANDS (a path missing from `commit-to-main` computes the
   answer and throws it away, which is the same failure one level up);
3. a reader GRADES the receipt (`render_due_list.src_stuck_branches`), including
   the case where the carrier is armed and has produced nothing;
4. it pages on a TRANSITION and never on the STANDING COUNT.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github/workflows/probes.yml"
PROBE = REPO / "scripts/ops/stuck_automation_branches.py"
RECEIPT_PATH = "docs/claude/STUCK-BRANCHES.json"


def _load(name: str, rel: str):
    """Import an ops script by path, registering it in ``sys.modules``."""
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # load-bearing: siblings import by name
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sab():
    return _load("stuck_automation_branches", "scripts/ops/stuck_automation_branches.py")


@pytest.fixture(scope="module")
def due():
    return _load("render_due_list", "scripts/ops/render_due_list.py")


@pytest.fixture(scope="module")
def steps():
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return doc["jobs"]["run"]["steps"]


# ── 1. the carrier exists, on a FULL clone ─────────────────────────────────

def test_probes_declares_the_carrier(steps):
    runs = [s.get("run", "") for s in steps if isinstance(s.get("run"), str)]
    assert any("stuck_automation_branches.py" in r and "--receipt" in r
               for r in runs), (
        "no step in probes.yml runs the stuck-branch probe with --receipt; the "
        "probe is back to running on nobody's schedule, which IS the defect")


def test_the_carrier_checks_out_a_full_clone(steps):
    """⚠️ LOAD-BEARING. On a shallow clone `git cherry`'s `+` cannot be told
    apart from 'the upstream range is truncated', so the probe reports a full
    population of CONFIDENT false findings. This is why the step lives in
    `probes.yml` and not in a workflow with a default checkout."""
    checkouts = [s for s in steps
                 if isinstance(s.get("uses"), str) and "actions/checkout" in s["uses"]]
    assert checkouts, "probes.yml has no checkout step at all"
    assert all(str((s.get("with") or {}).get("fetch-depth")) == "0"
               for s in checkouts), (
        "the stuck-branch carrier requires fetch-depth: 0 — without it "
        "containment is not computable and every branch is reported stuck")


def test_the_receipt_actually_lands(steps):
    """A receipt computed and not committed is the SAME defect, one level up."""
    commits = [s for s in steps
               if isinstance(s.get("uses"), str) and "commit-to-main" in s["uses"]]
    assert commits, "probes.yml no longer lands anything"
    paths = " ".join(str((s.get("with") or {}).get("paths", "")) for s in commits)
    assert RECEIPT_PATH in paths, (
        f"{RECEIPT_PATH} is written by the carrier and not in any "
        f"commit-to-main `paths:` — it would be computed and thrown away")


def test_the_receipt_is_written_before_the_due_list_renders(steps):
    """The premise of same-run freshness: the due-list of a run must be able to
    read that run's receipt, or the reader is always a day behind its writer."""
    names = [s.get("name", "") for s in steps]
    write = next(i for i, s in enumerate(steps)
                 if isinstance(s.get("run"), str)
                 and "stuck_automation_branches.py" in s["run"])
    render = next(i for i, s in enumerate(steps)
                  if isinstance(s.get("run"), str)
                  and "render_due_list.py" in s["run"])
    assert write < render, (
        f"the receipt step must precede the due-list refresh; got "
        f"{names[write]!r} at {write} and {names[render]!r} at {render}")


def test_the_probe_no_longer_claims_to_be_manual_only():
    """`check_unwired_artifacts.py` accepts `# wiring: manual-only — <why>` as a
    justification, so leaving the marker in place would excuse the carrier away
    again. Asserted through the GUARD'S OWN regex, with a positive control, so
    this cannot pass by the regex having changed underneath it."""
    guard = _load("check_unwired_artifacts", "scripts/ci/check_unwired_artifacts.py")
    marker = guard.MARKER
    assert marker.search("# wiring: manual-only — a human runs it when triaging"), (
        "the positive control does not match — this test is not probing what it "
        "claims to probe")
    assert not marker.search(PROBE.read_text(encoding="utf-8")), (
        "the probe still declares itself manual-only while a workflow carries "
        "it; field beats comment, and the stale marker is what would excuse it")


# ── 2. the reader grades it ────────────────────────────────────────────────

def test_the_reader_is_registered(due):
    assert due.src_stuck_branches in due.SOURCES, (
        "src_stuck_branches is not in SOURCES, so the receipt lands on main and "
        "is read by nobody — the failure class this whole unit exists to end")


def _receipt(tmp_path: Path, **over) -> Path:
    doc = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prefix": "automation/", "shared_ref": "origin/main", "stale_hours": 6.0,
        "population": 3, "refetch": "not_needed", "read_state": "complete",
        "read_note": "every branch in the population was graded",
        "counts": {"stuck": 2, "in_flight": 1, "landed": 0,
                   "no_remote": 0, "unknown": 0},
        "stuck": ["automation/a", "automation/b"],
        "last_transition": {"state": "no_change", "added": [], "cleared": [],
                            "note": "the same 2 branch(es) as the last receipt"},
    }
    doc.update(over)
    root = tmp_path / "root"
    (root / "docs/claude").mkdir(parents=True, exist_ok=True)
    (root / ".github/workflows").mkdir(parents=True, exist_ok=True)
    (root / ".github/workflows/probes.yml").write_text(WF.read_text(encoding="utf-8"))
    (root / RECEIPT_PATH).write_text(json.dumps(doc))
    return root


def test_armed_and_never_produced_is_not_a_clean_read(due, tmp_path):
    """THE STATE THIS UNIT EXISTS TO MAKE VISIBLE. A declared carrier that has
    landed nothing must degrade the envelope, never read as 'nothing due'."""
    root = tmp_path / "empty"
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/probes.yml").write_text(WF.read_text(encoding="utf-8"))
    res = due.src_stuck_branches(root, date.today())
    assert res.state == "could_not_read", (
        f"a declared-but-silent carrier graded {res.state!r} — an armed "
        f"producer that has never produced is not a clean result")
    assert "produced nothing" in res.note


def test_no_carrier_and_no_receipt_is_not_applicable(due, tmp_path):
    """The control for the test above: WITHOUT a declaring workflow the same
    absent file is `not_applicable`, a different fact. If these two collapse,
    'nobody built it' and 'it is broken' become one state."""
    root = tmp_path / "bare"
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/probes.yml").write_text("name: probes\non:\n  schedule:\n    - cron: '20 5 * * *'\n")
    res = due.src_stuck_branches(root, date.today())
    assert res.state == "not_applicable"


def test_the_standing_count_never_pages(due, tmp_path):
    """⚠️ THE DESENSITISED-ALARM CONTROL. 184 rows every morning is the alarm
    everyone learns to walk past. An unchanged standing count must produce NO
    row — and the count must still be legible, in the note."""
    res = due.src_stuck_branches(_receipt(tmp_path), date.today())
    assert res.state == "read"
    assert res.rows == [], (
        f"the standing count produced {len(res.rows)} row(s); this carrier is "
        f"required to page on a TRANSITION only")
    assert "2 branch(es) standing" in res.note, (
        "the count vanished entirely — quiet is not the same as invisible")


def test_a_new_stranding_pages(due, tmp_path):
    root = _receipt(tmp_path, last_transition={
        "state": "changed", "added": ["automation/c"], "cleared": [],
        "note": "1 newly stranded, 0 cleared (count 2 -> 3)"})
    res = due.src_stuck_branches(root, date.today())
    assert [r["id"] for r in res.rows] == ["stuck-branches-changed"]
    assert res.rows[0]["loud"] is True
    assert "automation/c" in res.rows[0]["why_due"]


def test_a_degraded_receipt_pages(due, tmp_path):
    root = _receipt(tmp_path, read_state="partial",
                    read_note="0 unknown + 12 unconfirmed no_remote of 190")
    res = due.src_stuck_branches(root, date.today())
    ids = [r["id"] for r in res.rows]
    assert "stuck-branches-degraded" in ids, (
        "a carrier that could not see the whole population read as a clean "
        "count — which is exactly how a shallow checkout goes unnoticed")


def test_a_stale_receipt_pages(due, tmp_path):
    """The carrier going quiet looks EXACTLY like a quiet week. It must not."""
    old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    res = due.src_stuck_branches(_receipt(tmp_path, generated_at=old), date.today())
    ids = [r["id"] for r in res.rows]
    assert "stuck-branches-receipt-stale" in ids, (
        f"a 9-day-old receipt did not page; got {ids}")


def test_an_undateable_receipt_pages(due, tmp_path):
    res = due.src_stuck_branches(_receipt(tmp_path, generated_at="whenever"),
                                 date.today())
    assert "stuck-branches-receipt-undateable" in [r["id"] for r in res.rows]


def test_an_unreadable_receipt_is_not_an_empty_one(due, tmp_path):
    root = _receipt(tmp_path)
    (root / RECEIPT_PATH).write_text("{not json")
    res = due.src_stuck_branches(root, date.today())
    assert res.state == "could_not_read"


# ── 3. the transition vocabulary the reader depends on ─────────────────────

def test_the_probe_self_test_covers_the_transition(sab):
    assert sab._selftest() == 0


def test_a_partial_read_is_graded_before_any_comparison(sab):
    """Pinned here as well as in the module's own self-test because it is the
    one ordering whose loss produces a CONFIDENT WRONG answer: every branch the
    run failed to fetch would be reported as `cleared`."""
    complete = {"read_state": sab.READ_COMPLETE, "stuck": ["a", "b", "c"]}
    partial = {"read_state": sab.READ_PARTIAL, "read_note": "shallow", "stuck": []}
    got = sab.transition(complete, partial)
    assert got["state"] == sab.DEGRADED
    assert got["cleared"] == [], "a truncated read reported branches as cleared"


def test_first_run_is_not_no_change(sab):
    got = sab.transition(None, {"read_state": sab.READ_COMPLETE, "stuck": ["a"]})
    assert got["state"] == sab.FIRST_RUN
    assert sab.FIRST_RUN not in sab.PAGING_STATES
