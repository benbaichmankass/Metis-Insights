"""Tests for the follow-through pipeline (`scripts/ops/pipeline.py`).

⚠️ THIS IS NOT A DUPLICATE OF `--self-test`. That self-test is the guard's
check and covers the module's surface. These are the invariants whose
regression would silently rebuild one of the five documented reasons work used
to get dropped (docs/plans/OPERATING-PLAN-2026-09-21.md § 3b) — the ones worth
failing the whole suite over, not just a guard.

The distinction matters because a future session optimising this module will
read the self-test as "the tests" and may loosen it to make an import pass.
These live in `tests/` so that loosening also turns `pytest-run` red.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.ops import pipeline as P  # noqa: E402


def _item(**over):
    item = {
        "id": "PI-1",
        "what": "a thing",
        "origin": {"kind": "audit", "ref": "#1", "rerun": "python3 x.py"},
        "due_when": {"kind": "observation", "clears_when": "it is true",
                     "check_every_days": 7, "last_checked": "2026-09-01"},
        "next_action": "check_observation",
        "state": "queued",
    }
    item.update(over)
    return item


# ── reason (4): an item may not simply stop being mentioned ─────────────────
@pytest.mark.parametrize("state", P.TERMINAL_STATES)
def test_a_terminal_state_requires_a_stated_reason(state):
    with pytest.raises(P.PipelineError, match="terminal_reason"):
        P.validate(_item(state=state))
    # The positive control: WITH a reason it is accepted, so the test above is
    # measuring the reason and not some other refusal.
    assert P.validate(_item(state=state, terminal_reason="why"))


# ── reason (2): it must know when it needs attention ────────────────────────
def test_an_observation_item_without_a_clears_when_is_refused():
    with pytest.raises(P.PipelineError, match="clears_when"):
        P.validate(_item(due_when={"kind": "observation", "check_every_days": 7}))


# ── reason (3): it must be able to re-ask its own question ──────────────────
def test_an_item_that_cannot_regenerate_its_finding_is_refused():
    with pytest.raises(P.PipelineError, match="origin.rerun"):
        P.validate(_item(origin={"kind": "audit", "ref": "#1", "rerun": ""}))


# ── reason (1): due is computed, so nothing has to choose to look ───────────
def test_due_is_computed_from_the_clock_not_from_a_flag():
    t = date(2026, 9, 21)
    stale = _item(due_when={"kind": "observation", "clears_when": "x",
                            "check_every_days": 7, "last_checked": "2026-09-01"})
    fresh = _item(due_when={"kind": "observation", "clears_when": "x",
                            "check_every_days": 7, "last_checked": "2026-09-20"})
    assert P.is_due(stale, t)
    assert not P.is_due(fresh, t)          # negative control
    # Neither item carries any "due" flag — the difference is only the clock.
    assert "due" not in stale and "due" not in fresh


# ── the property that says this is not the eight registers again ────────────
def test_a_due_unrouted_item_stays_counted_until_it_is_routed():
    t = date(2026, 9, 21)
    item = _item(id="A")
    assert P.unrouted_count([item], t) == 1
    routed = _item(id="A", state="routed", routed_to="A7")
    assert P.unrouted_count([routed], t) == 0
    # …but it is still DUE, i.e. routing transfers ownership rather than
    # making the item disappear from the operator's view.
    assert [i["id"] for i in P.due([routed], t)] == ["A"]


# ── the loudest invariant: a broken store never reads as an empty one ───────
def test_an_unreadable_record_is_never_silently_dropped(tmp_path):
    store = tmp_path / "P.jsonl"
    P.append(_item(id="GOOD"), store)
    store.write_text(store.read_text() + "{not json\n")

    res = P.read_log(store)
    assert len(res.items) == 1, "the good record still loads"
    assert len(res.unreadable) == 1, "the bad record is REPORTED, not skipped"
    assert res.healthy is False
    assert P.stats(res)["readable"] is False

    rendered = "\n".join(P.render_section_0(res, date(2026, 9, 21)))
    assert "COULD NOT BE PARSED" in rendered

    # ⚠️ THE NEGATIVE CONTROL, and it is the point of this test: a healthy
    # store must NOT emit that warning, or the probe above proves nothing.
    clean = tmp_path / "C.jsonl"
    P.append(_item(id="GOOD"), clean)
    assert "COULD NOT BE PARSED" not in "\n".join(
        P.render_section_0(P.read_log(clean), date(2026, 9, 21)))


def test_a_store_that_cannot_be_fully_read_fails_the_check(tmp_path):
    store = tmp_path / "P.jsonl"
    store.write_text("{not json\n")
    assert P._check(store) == 1


# ── append-only: state changes never rewrite history ────────────────────────
def test_state_changes_append_and_the_audit_trail_survives(tmp_path):
    store = tmp_path / "P.jsonl"
    P.append(_item(id="X"), store)
    P.append(_item(id="X", state="routed", routed_to="A7"), store)
    P.append(_item(id="X", state="killed", terminal_reason="superseded"), store)

    res = P.read_log(store)
    assert res.items["X"]["state"] == "killed", "current state is the LAST record"
    assert res.records == 3, "every earlier record is still in the log"
    assert len(store.read_text().strip().splitlines()) == 3


def test_append_refuses_an_invalid_item_and_writes_nothing(tmp_path):
    store = tmp_path / "P.jsonl"
    with pytest.raises(P.PipelineError):
        P.append(_item(state="killed"), store)
    assert not store.exists() or store.read_text() == ""


# ── the module is actually runnable as the guard invokes it ─────────────────
def test_self_test_passes_as_a_subprocess():
    r = subprocess.run(
        [sys.executable, "scripts/ops/pipeline.py", "--self-test"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELF-TEST PASS" in r.stdout


def test_the_committed_store_is_valid():
    """The real store, not a fixture — this is what the guard protects."""
    assert P._check(REPO / "docs/claude/work/PIPELINE.jsonl") == 0
