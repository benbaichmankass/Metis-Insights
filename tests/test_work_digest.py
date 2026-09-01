"""Tests for scripts/ops/work_digest.py — the Phase B daily roll-up.

The digest's whole value is that the operator can trust it, so these tests are
weighted toward the ways a notification LIES rather than toward its formatting:

  * reporting a window nobody examined as a quiet day (the false negative that
    matters — a shallow clone makes this the common case, not an edge case),
  * pinging on activity rather than state change (the measured failure: 202 of
    376 CRITICAL/ERROR rows in one window were a single un-latched alarm),
  * a latch that suppresses when it is broken rather than sending.

Every check that asserts a failure is reported also asserts the probe finds the
positive case, so "correctly reported" stays distinguishable from "reports
everything".
"""
from __future__ import annotations

import json
import subprocess

from scripts.ops import work_digest as wd
from scripts.ops.work_phase_ping import OBJECTS_DIR, PING_WORTHY


# ── the false negative that matters most ─────────────────────────────────

def test_unresolvable_window_is_not_reported_as_a_quiet_day():
    """A ref outside a shallow clone means WE DID NOT LOOK, never 'no changes'."""
    d = wd.build_digest("definitely-not-a-ref-000000", "HEAD")
    assert d["digestState"] == "window_unresolved"
    assert d["digestState"] != "no_changes"
    assert d["unresolvedRef"] == "definitely-not-a-ref-000000"


def test_unresolvable_window_says_so_in_the_operator_visible_text():
    """The distinction is worthless if it never reaches the message."""
    text = wd.render(wd.build_digest("definitely-not-a-ref-000000", "HEAD"))
    assert "NOT examined" in text
    assert "NOT 'nothing changed'" in text


def test_resolvable_window_grades_a_real_state_not_unresolved():
    """Positive control: the probe must be able to resolve a window at all."""
    d = wd.build_digest("HEAD", "HEAD")
    assert d["digestState"] in ("no_changes", "changes_observed")
    assert d["unresolvedRef"] is None


def test_identical_refs_are_no_changes_which_is_a_real_observation():
    d = wd.build_digest("HEAD", "HEAD")
    assert d["digestState"] == "no_changes"
    assert d["changes"] == []
    assert "No lifecycle change" in wd.render(d)


# ── state changes only, never activity ───────────────────────────────────

def test_event_predicate_is_imported_not_redefined():
    """Two copies of 'what counts as an event' is how they drift apart."""
    assert wd.PING_WORTHY is PING_WORTHY


def test_dormant_and_ready_are_not_events():
    """Moving to dormant/ready is not something to wake an operator for."""
    assert "dormant" not in wd.PING_WORTHY
    assert "ready" not in wd.PING_WORTHY
    # Positive control: the states that ARE events.
    assert {"in_flight", "waiting", "done", "accepted"} <= wd.PING_WORTHY


def test_a_real_lifecycle_transition_is_detected_in_a_temp_repo(tmp_path, monkeypatch):
    """End-to-end over a real git history, not a mocked diff."""
    repo = tmp_path / "r"
    (repo / OBJECTS_DIR).mkdir(parents=True)

    def run(*a):
        return subprocess.run(a, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")

    obj = repo / OBJECTS_DIR / "WO-T.yaml"
    obj.write_text('id: WO-T\ntitle: "A thing"\nlifecycle: ready\n')
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()

    obj.write_text('id: WO-T\ntitle: "A thing"\nlifecycle: in_flight\n')
    run("git", "add", "-A")
    run("git", "commit", "-qm", "move")

    monkeypatch.setattr(wd, "REPO_ROOT", repo)
    import scripts.ops.work_phase_ping as wpp
    monkeypatch.setattr(wpp, "REPO_ROOT", repo)

    d = wd.build_digest(base, "HEAD")
    assert d["digestState"] == "changes_observed"
    assert len(d["changes"]) == 1
    assert d["changes"][0]["from"] == "ready"
    assert d["changes"][0]["to"] == "in_flight"
    assert "ready → in_flight" in wd.render(d)


def test_a_non_lifecycle_edit_is_not_an_event(tmp_path, monkeypatch):
    """Editing a file is ACTIVITY. It must not produce a ping."""
    repo = tmp_path / "r"
    (repo / OBJECTS_DIR).mkdir(parents=True)

    def run(*a):
        return subprocess.run(a, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")

    obj = repo / OBJECTS_DIR / "WO-T.yaml"
    obj.write_text('id: WO-T\ntitle: "A thing"\nlifecycle: in_flight\n')
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()

    # A real edit that changes no lifecycle.
    obj.write_text('id: WO-T\ntitle: "A thing"\nlifecycle: in_flight\nnote: edited\n')
    run("git", "add", "-A")
    run("git", "commit", "-qm", "edit")

    monkeypatch.setattr(wd, "REPO_ROOT", repo)
    import scripts.ops.work_phase_ping as wpp
    monkeypatch.setattr(wpp, "REPO_ROOT", repo)

    d = wd.build_digest(base, "HEAD")
    assert d["digestState"] == "no_changes", "a file edit is activity, not an event"


# ── the standing partition ───────────────────────────────────────────────

def test_standing_lifecycle_buckets_sum_to_object_count():
    st = wd.standing_state("HEAD")
    assert sum(st["lifecycle"].values()) == st["objectCount"]


def test_every_lifecycle_key_present_including_zeros():
    lc = wd.standing_state("HEAD")["lifecycle"]
    for state in (*wd.LIFECYCLE_STATES, wd.UNKNOWN):
        assert state in lc, f"{state} vanished — a consumer would branch on absence"


def test_render_states_the_population_on_its_numbers():
    text = wd.render(wd.build_digest("HEAD", "HEAD"))
    assert "population:" in text
    assert "of " in text  # the standing line carries its denominator


# ── the WIP ceiling is a reading, not a gate ─────────────────────────────

def test_ceiling_hit_is_loud_and_under_ceiling_is_not():
    d = wd.build_digest("HEAD", "HEAD")
    quiet = wd.render(d)
    hit_standing = dict(
        d["standing"],
        wip=dict(d["standing"]["wip"], inFlight=wd.WIP_CEILING, ceilingHit=True),
    )
    loud = wd.render({**d, "standing": hit_standing})
    assert "WIP CEILING HIT" in loud
    assert "WIP CEILING HIT" not in quiet, "positive control: not-hit stays quiet"


def test_ceiling_is_reported_as_enforced_because_it_is():
    """RENAMED 2026-09-01, because the old name asserted the opposite of the truth.

    It was `test_ceiling_is_never_reported_as_enforced` and was correct when
    Phase B shipped. Phase C (#10657) shipped check_wip_ceiling.py, and a ninth
    in_flight object is now genuinely REFUSED — so a digest still saying
    "declared, not enforced" tells a reader they may open one. The dangerous
    direction, which is why the name changed rather than the assertion being
    loosened.
    """
    wip = wd.standing_state("HEAD")["wip"]
    assert wip["enforced"] is True
    assert wip["state"] == "enforced_in_ci"
    rendered = wd.render(wd.build_digest("HEAD", "HEAD"))
    assert "not a gate" not in rendered, (
        "the digest must not tell a reader the ceiling is advisory — it is a gate")
    assert "IS a gate" in rendered


# ── coverage: never claims to be the whole picture ───────────────────────

def test_digest_declares_the_store_incomplete():
    d = wd.build_digest("HEAD", "HEAD")
    assert d["coverageComplete"] is False
    # Still names Phase C — but as history now, not as a pending migration.
    assert "Phase C" in wd.render(d)
    assert "COMPLETE" in wd.render(d)


# ── the latch fails loud ─────────────────────────────────────────────────

def test_unreadable_latch_sends_rather_than_suppresses(tmp_path, monkeypatch):
    """On a notification path, failing loud is the only safe direction."""
    monkeypatch.setattr(wd, "STATE", tmp_path / "nope" / "missing.json")
    assert wd._already_sent_today("2026-09-01") is False


def test_latch_suppresses_a_second_digest_on_the_same_day(tmp_path, monkeypatch):
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"lastDigestDay": "2026-09-01"}))
    monkeypatch.setattr(wd, "STATE", state)
    assert wd._already_sent_today("2026-09-01") is True
    assert wd._already_sent_today("2026-09-02") is False, "a new day must send"


def test_write_appends_exactly_one_row_and_latches(tmp_path, monkeypatch):
    pending = tmp_path / "pending.jsonl"
    monkeypatch.setattr(wd, "PENDING", pending)
    monkeypatch.setattr(wd, "STATE", tmp_path / "state.json")

    assert wd.main(["--base", "HEAD", "--head", "HEAD", "--write"]) == 0
    rows = [json.loads(line) for line in pending.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "work_digest"
    assert rows[0]["digest_state"] == "no_changes"

    # Second invocation the same day must NOT queue a duplicate.
    assert wd.main(["--base", "HEAD", "--head", "HEAD", "--write"]) == 0
    rows2 = [json.loads(line) for line in pending.read_text().splitlines() if line.strip()]
    assert len(rows2) == 1, "the daily latch must stop a second digest"


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    pending = tmp_path / "pending.jsonl"
    monkeypatch.setattr(wd, "PENDING", pending)
    monkeypatch.setattr(wd, "STATE", tmp_path / "state.json")
    assert wd.main(["--base", "HEAD", "--head", "HEAD"]) == 0
    assert not pending.exists(), "default must be print-only"


def test_self_test_passes():
    assert wd._self_test() == 0


# ── the two surfaces may never disagree again ────────────────────────────

def test_digest_and_route_publish_the_same_ceiling_facts():
    """The regression this whole change exists to prevent.

    These facts lived in TWO places. Phase C shipped the enforcement and updated
    neither, so the deployed SPA told the operator the ceiling was advisory while
    it would in fact fail their CI. Fixing only the route would have left the
    digest saying it — the same sentence corrected once and still wrong somewhere
    else.

    Both now import src/utils/work_facts.py. This asserts they actually AGREE,
    rather than trusting that they both import it: a future edit that reintroduces
    a local literal fails here instead of reaching the operator.
    """
    from src.utils import work_facts
    from src.web.api.routers import work as route

    digest_wip = wd.standing_state("HEAD")["wip"]
    route_wip = route.get_work()["wip"]

    assert digest_wip["ceiling"] == route_wip["ceiling"] == work_facts.WIP_CEILING
    assert digest_wip["enforced"] == route_wip["enforced"] == work_facts.CEILING_ENFORCED
    assert digest_wip["state"] == route_wip["state"] == work_facts.CEILING_STATE
