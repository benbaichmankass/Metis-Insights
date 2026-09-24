"""Tests for the follow-through pipeline (`scripts/ops/pipeline.py`).

⚠️ THIS IS NOT A DUPLICATE OF `--self-test`. That self-test is the guard's
check and covers the module's surface. These are the invariants whose
regression would silently rebuild one of the five documented reasons work used
to get dropped (docs/plans/OPERATING-PLAN-2026-09-21.md § 3b) — the ones worth
failing the whole suite over, not just a guard.

The distinction matters because a future session optimising this module will
read the self-test as "the tests" and may loosen it to make an import pass.
These live in `tests/` so that loosening also turns `pytest-run` red.

⚠️ RE-POINTED 2026-09-24 (lane E64): the store is now a DIRECTORY, one
immutable JSON file per record, not a single append-only JSONL file — see
`scripts/ops/pipeline.py`'s module docstring for why (every writer's line
landed at the same end-of-file position, so concurrent PRs re-conflicted on
every merge). Fixtures below build directories, not files; the invariants
under test are unchanged.
"""
from __future__ import annotations

import json
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
    store = tmp_path / "pipeline"
    P.append(_item(id="GOOD"), store, intent="new")
    (store / "zz-broken.json").write_text("{not json\n", encoding="utf-8")

    res = P.read_log(store)
    assert len(res.items) == 1, "the good record still loads"
    assert len(res.unreadable) == 1, "the bad record is REPORTED, not skipped"
    assert res.healthy is False
    assert P.stats(res)["readable"] is False

    rendered = "\n".join(P.render_section_0(res, date(2026, 9, 21)))
    assert "COULD NOT BE PARSED" in rendered

    # ⚠️ THE NEGATIVE CONTROL, and it is the point of this test: a healthy
    # store must NOT emit that warning, or the probe above proves nothing.
    clean = tmp_path / "clean"
    P.append(_item(id="GOOD"), clean, intent="new")
    assert "COULD NOT BE PARSED" not in "\n".join(
        P.render_section_0(P.read_log(clean), date(2026, 9, 21)))


def test_a_store_that_cannot_be_fully_read_fails_the_check(tmp_path):
    store = tmp_path / "pipeline"
    store.mkdir()
    (store / "broken.json").write_text("{not json\n", encoding="utf-8")
    assert P._check(store) == 1


def test_a_resurrected_flat_file_beside_the_directory_fails_the_check(tmp_path):
    """PLANTED DEFECT, found in review the day this module shipped (E64):
    at least 6 lane PRs were still appending to the OLD flat file when this
    migration merged, so each hits a modify/delete conflict on it -- and a
    hand-resolved conflict can easily choose to KEEP the file. That
    resurrects a flat PIPELINE.jsonl beside the new directory, and
    `read_log(store)` (directory-shaped `store`) never looks at a SIBLING
    file -- it would silently drop every row filed there while `--check`
    kept reporting clean. This must be refused outright."""
    store = tmp_path / "docs" / "claude" / "work" / "pipeline"
    P.append(_item(id="X"), store, intent="new")
    assert P._check(store) == 0, "negative control: no sibling file, clean check"

    legacy = store.parent / "PIPELINE.jsonl"
    legacy.write_text(json.dumps(_item(id="Y")) + "\n", encoding="utf-8")
    assert P._check(store) == 1, (
        "a resurrected sibling flat file must fail the check, never pass "
        "silently while ignoring rows filed there")


def test_a_preexisting_flat_file_is_still_read_as_a_migration_safety_net(tmp_path):
    """The directory is the live format; a flat file is read too, so a
    caller that has not migrated yet (or a rollback) is not silently
    treated as an empty store."""
    legacy = tmp_path / "legacy.jsonl"
    legacy.write_text(json.dumps(_item(id="OLD")) + "\n", encoding="utf-8")
    res = P.read_log(legacy)
    assert res.items["OLD"]["id"] == "OLD"
    assert res.records == 1


# ── append-only: state changes never rewrite history ────────────────────────
def test_state_changes_append_and_the_audit_trail_survives(tmp_path):
    store = tmp_path / "pipeline"
    P.append(_item(id="X"), store, intent="new")
    P.append(_item(id="X", state="routed", routed_to="A7"), store, intent="update")
    P.append(_item(id="X", state="killed", terminal_reason="superseded"), store,
             intent="update")

    res = P.read_log(store)
    assert res.items["X"]["state"] == "killed", "current state is the LAST record"
    assert res.records == 3, "every earlier record is still in the log"
    assert len(list(store.glob(f"*{P.RECORD_SUFFIX}"))) == 3, (
        "every record is its OWN file — nothing is ever edited in place")


def test_two_concurrent_appends_never_collide_on_a_filename(tmp_path):
    """The actual merge-safety property: two records written to the same
    store get two DIFFERENT filenames, even back-to-back with no delay."""
    store = tmp_path / "pipeline"
    P.append(_item(id="X"), store, intent="new")
    P.append(_item(id="Y"), store, intent="new")
    names = [p.name for p in store.glob(f"*{P.RECORD_SUFFIX}")]
    assert len(names) == 2
    assert len(set(names)) == 2, "two appends must never share a filename"


def test_append_refuses_an_invalid_item_and_writes_nothing(tmp_path):
    store = tmp_path / "pipeline"
    with pytest.raises(P.PipelineError):
        P.append(_item(state="killed"), store, intent="new")
    assert not store.exists()


def test_append_requires_intent_to_be_declared(tmp_path):
    """There is deliberately no default -- the caller having to say which it
    means is the fix, not an optional nicety."""
    store = tmp_path / "pipeline"
    with pytest.raises(TypeError):
        P.append(_item(id="X"), store)  # intent omitted on purpose


# ── the live defect this module shipped with: two findings, one id ──────────
def test_intent_new_refuses_an_id_that_already_exists(tmp_path):
    store = tmp_path / "pipeline"
    P.append(_item(id="X"), store, intent="new")
    with pytest.raises(P.PipelineError, match="already exists"):
        P.append(_item(id="X"), store, intent="new")


def test_intent_update_refuses_a_different_finding_reusing_the_id(tmp_path):
    """The exact shape of the live PI-20260921-0002 collision: two UNRELATED
    findings, same id. This is the regression this module shipped with."""
    store = tmp_path / "pipeline"
    P.append(_item(id="X", what="board-pointer readers still exist",
                    origin={"kind": "audit", "ref": "#1", "rerun": "x"}),
              store, intent="new")
    with pytest.raises(P.PipelineError, match="does not look like a state change"):
        P.append(_item(id="X", what="pr-landing-guard remedy text is stale",
                        origin={"kind": "session", "ref": "sess_2", "rerun": "y"}),
                  store, intent="update")
    # And the refusal actually stopped the write -- the store is untouched.
    assert P.read_log(store).items["X"]["what"] == "board-pointer readers still exist"


def test_intent_update_refuses_with_no_prior_record(tmp_path):
    store = tmp_path / "pipeline"
    with pytest.raises(P.PipelineError, match="no prior record"):
        P.append(_item(id="NEVER-FILED"), store, intent="update")


def test_identity_check_allows_append_only_elaboration_of_what(tmp_path):
    """Real data caught this: PI-20260921-0004 legitimately grew its `what`
    (2080 -> 2995 chars, same session adding a measured detail) and a strict
    `==` identity check flagged it as a collision. `new.what` starting with
    `prior.what` must be accepted, not just an identical string."""
    store = tmp_path / "pipeline"
    base = _item(id="X", what="the short version")
    P.append(base, store, intent="new")
    grown = dict(base, what="the short version, now with more detail",
                 state="routed", routed_to="A7")
    P.append(grown, store, intent="update")  # must not raise
    assert P.read_log(store).collisions == []


def test_identity_check_rejects_a_what_that_is_not_a_pure_extension(tmp_path):
    store = tmp_path / "pipeline"
    base = _item(id="X", what="the original text")
    P.append(base, store, intent="new")
    with pytest.raises(P.PipelineError, match="does not look like a state change"):
        P.append(dict(base, what="a rewritten and unrelated text"), store,
                  intent="update")


def test_check_reports_a_collision_instead_of_reading_clean(tmp_path, capsys):
    store = tmp_path / "pipeline"
    store.mkdir()
    a = _item(id="X", what="finding A",
              origin={"kind": "audit", "ref": "#1", "rerun": "x"})
    b = _item(id="X", what="finding B",
              origin={"kind": "session", "ref": "sess_2", "rerun": "y"})
    (store / "0001-a.json").write_text(json.dumps(a), encoding="utf-8")
    (store / "0002-b.json").write_text(json.dumps(b), encoding="utf-8")

    res = P.read_log(store)
    assert len(res.collisions) == 1
    assert res.collisions[0]["id"] == "X"

    rc = P._check(store)
    out = capsys.readouterr().out
    assert rc == 1, "a NEW (non-grandfathered) collision fails the guard"
    assert "pipeline: clean —" not in out


def test_check_grandfathers_the_one_known_collision_without_calling_it_clean(tmp_path, capsys):
    """The real committed store carries exactly one PRE-EXISTING collision
    (PI-20260921-0002) that predates this fix and cannot be repaired without
    rewriting append-only history. It must stay green so CI does not block
    on debt nobody can pay down here -- but it must never read as `clean`."""
    store = tmp_path / "pipeline"
    store.mkdir()
    gid = next(iter(P.GRANDFATHERED_COLLISIONS))
    a = _item(id=gid, what="finding A",
              origin={"kind": "audit", "ref": "#1", "rerun": "x"})
    b = _item(id=gid, what="finding B",
              origin={"kind": "session", "ref": "sess_2", "rerun": "y"})
    (store / "0001-a.json").write_text(json.dumps(a), encoding="utf-8")
    (store / "0002-b.json").write_text(json.dumps(b), encoding="utf-8")

    rc = P._check(store)
    out = capsys.readouterr().out
    assert rc == 0
    assert "pipeline: clean —" not in out
    assert gid in out, "the grandfathered collision is still named, not hidden"


# ── allocation: two concurrent sessions must not pick the same next number ──
def test_mint_id_scopes_by_session_so_two_sessions_never_collide(tmp_path):
    store = tmp_path / "pipeline"
    a = P.mint_id("session_AAAAAAAAAAAAAAAA", today=date(2026, 9, 21), store=store)
    b = P.mint_id("session_BBBBBBBBBBBBBBBB", today=date(2026, 9, 21), store=store)
    assert a != b
    assert a.startswith("PI-20260921-")
    assert b.startswith("PI-20260921-")


def test_mint_id_is_gapless_and_readable_within_one_session(tmp_path):
    store = tmp_path / "pipeline"
    ref = "session_CCCCCCCCCCCCCCCC"
    first = P.mint_id(ref, today=date(2026, 9, 21), store=store)
    P.append(_item(id=first), store, intent="new")
    second = P.mint_id(ref, today=date(2026, 9, 21), store=store)
    assert second != first
    assert second.startswith(first.rsplit("-", 1)[0] + "-")


# ── the actual property lane E64 exists to buy ───────────────────────────────
def test_two_branches_each_appending_one_record_merge_cleanly(tmp_path):
    """POSITIVE CONTROL: simulate two branches each appending one record and
    merge them with PLAIN git (no merge driver), in both orders. The old
    single-append-only-file design failed exactly this scenario in
    production (9 green lane PRs, each squash re-conflicting the rest, ONLY
    on this file — see the module docstring)."""
    git = pytest.importorskip("shutil").which("git")
    if git is None:
        pytest.skip("git not on PATH")

    def run(*args, cwd):
        r = subprocess.run(["git", *args], cwd=str(cwd),
                            capture_output=True, text=True)
        assert r.returncode == 0, f"git {args} failed: {r.stdout}\n{r.stderr}"
        return r

    repo = tmp_path / "repo"
    repo.mkdir()
    run("init", "-q", "-b", "main", cwd=repo)
    run("config", "user.email", "test@example.invalid", cwd=repo)
    run("config", "user.name", "pytest", cwd=repo)

    store = repo / "docs" / "claude" / "work" / "pipeline"
    P.append(_item(id="BASE"), store, intent="new")
    run("add", "-A", cwd=repo)
    run("commit", "-q", "-m", "base", cwd=repo)

    run("checkout", "-q", "-b", "branch-a", cwd=repo)
    P.append(_item(id="A", what="branch A's finding"), store, intent="new")
    run("add", "-A", cwd=repo)
    run("commit", "-q", "-m", "branch A", cwd=repo)

    run("checkout", "-q", "main", cwd=repo)
    run("checkout", "-q", "-b", "branch-b", cwd=repo)
    P.append(_item(id="B", what="branch B's finding"), store, intent="new")
    run("add", "-A", cwd=repo)
    run("commit", "-q", "-m", "branch B", cwd=repo)

    run("checkout", "-q", "main", cwd=repo)
    run("merge", "--no-edit", "branch-a", cwd=repo)
    run("merge", "--no-edit", "branch-b", cwd=repo)  # would have conflicted, flat-file era

    res = P.read_log(store)
    assert set(res.items) == {"BASE", "A", "B"}
    assert res.collisions == []


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
    assert P._check(REPO / P.STORE) == 0
