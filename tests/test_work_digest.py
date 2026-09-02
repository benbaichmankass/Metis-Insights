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
import json as _json
import pathlib as _pathlib
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
    """REWORDED 2026-09-02 — the assertion is stronger, not looser.

    It read ``"No lifecycle change" in render(d)``, which was exactly the
    sentence the digest had no business saying: it measured the work STORE and
    reported it as though it had measured the WORK. The digest now reads six
    registers, so the quiet-window sentence must state how many of them it
    actually read — "nothing changed across 6 of 6" and "across 4 of 6" are
    different claims and only the first is a clean night.
    """
    d = wd.build_digest("HEAD", "HEAD")
    assert d["digestState"] == "no_changes"
    assert d["changes"] == []
    assert d["events"] == []
    text = wd.render(d)
    assert "No state change" in text
    assert "register(s) read" in text, (
        "a quiet-window claim must carry the denominator of what was examined")


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


# ═════════════════════════════════════════════════════════════════════════
# Defect 2 (2026-09-02) — it measured the work STORE and called that the WORK
#
# On the busiest night this system has had — 49 commits / 49 merged PRs in
# 1bae542a..d06cd3e9, two retired backlog classes, a cleared monitoring row and
# a closed capability gap — the digest emitted `No lifecycle change`. Every one
# of those is a decision or a state change; not one moves a `lifecycle:` field
# in `docs/claude/work/objects/*.yaml`. A confident report of a quiet night is
# worse than no report, because it looks like oversight happened.
# ═════════════════════════════════════════════════════════════════════════

_ROOT = _pathlib.Path(__file__).resolve().parents[1]


def _mini_repo(tmp_path, monkeypatch):
    """A git repo with the register files, so the diff is real, not mocked."""
    repo = tmp_path / "r"
    (repo / OBJECTS_DIR).mkdir(parents=True)

    def run(*a):
        return subprocess.run(a, cwd=repo, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    monkeypatch.setattr(wd, "REPO_ROOT", repo)
    import scripts.ops.work_phase_ping as wpp
    monkeypatch.setattr(wpp, "REPO_ROOT", repo)
    return repo, run


def _write(repo, rel, items):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps({"items": items}), encoding="utf-8")


def _sha(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


# ── the sources are real files, in the real repo ─────────────────────────

def test_every_declared_source_exists_on_disk():
    """A source path that has drifted reads as `absent` forever — silently
    correct-looking, and permanently blind. Pinned, not trusted."""
    missing = [s.path for s in wd.SOURCES if not (_ROOT / s.path).exists()]
    assert not missing, f"declared sources not on disk: {missing}"


def test_sources_cover_the_registers_the_operator_actually_uses():
    declared = {s.path for s in wd.SOURCES}
    for required in ("docs/claude/work/MANAGER-CHECKLIST.json",
                     "docs/claude/OPEN-ITEMS.json"):
        assert required in declared, f"{required} is where decisions land"
    backlogs = {p.as_posix()[len(_ROOT.as_posix()) + 1:]
                for p in (_ROOT / "docs" / "claude").glob("*-review-backlog.json")}
    assert backlogs <= declared, (
        f"a review backlog exists that the digest never reads: "
        f"{sorted(backlogs - declared)}")


# ── a backlog verdict is an event; an edit is not ────────────────────────

def test_a_backlog_row_reaching_a_terminal_disposition_is_an_event(tmp_path, monkeypatch):
    repo, run = _mini_repo(tmp_path, monkeypatch)
    _write(repo, "docs/claude/health-review-backlog.json",
           [{"id": "BL-1", "title": "a defect", "status": "open"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    _write(repo, "docs/claude/health-review-backlog.json",
           [{"id": "BL-1", "title": "a defect", "status": "resolved"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "close")

    events, reads, _ = wd.register_events(base, "HEAD")
    assert reads["health backlog"] == "read"
    assert [(e["id"], e["from"], e["to"]) for e in events] == \
        [("BL-1", "open", "resolved")]


def test_editing_a_backlog_row_without_closing_it_is_activity(tmp_path, monkeypatch):
    """The anti-changelog filter, structurally: only the STATE FIELD is watched."""
    repo, run = _mini_repo(tmp_path, monkeypatch)
    _write(repo, "docs/claude/health-review-backlog.json",
           [{"id": "BL-1", "title": "a defect", "status": "open"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    _write(repo, "docs/claude/health-review-backlog.json",
           [{"id": "BL-1", "title": "a defect", "status": "open",
             "note": "looked at it again"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "edit")

    events, _, _ = wd.register_events(base, "HEAD")
    assert events == [], "an edit that closes nothing is activity, not an event"


def test_a_filed_backlog_row_is_counted_but_not_enumerated(tmp_path, monkeypatch):
    """A backlog gains rows constantly; the VERDICT is the event, not the filing.

    Enumerating filings would rebuild the desensitized-alarm failure on a daily
    cadence — but the count still ships, so the filing rate stays visible.
    """
    repo, run = _mini_repo(tmp_path, monkeypatch)
    _write(repo, "docs/claude/health-review-backlog.json",
           [{"id": "BL-1", "status": "open"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    _write(repo, "docs/claude/health-review-backlog.json",
           [{"id": "BL-1", "status": "open"}, {"id": "BL-2", "status": "open"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "file")

    events, _, counts = wd.register_events(base, "HEAD")
    assert events == []
    assert counts["health backlog"]["added"] == 1


# ── the register: clearing a row is the event that matters ───────────────

def test_clearing_an_open_items_row_is_an_event(tmp_path, monkeypatch):
    repo, run = _mini_repo(tmp_path, monkeypatch)
    _write(repo, "docs/claude/OPEN-ITEMS.json",
           [{"id": "OI-1", "summary": "watch a thing"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    _write(repo, "docs/claude/OPEN-ITEMS.json", [])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "clear")

    events, _, _ = wd.register_events(base, "HEAD")
    assert [(e["id"], e["to"]) for e in events] == [("OI-1", "CLEARED")]
    assert "OI-1" in wd.render(wd.build_digest(base, "HEAD"))


# ── the checklist: scoping is not an event, blocking is ──────────────────

def test_a_checklist_item_landing_in_queued_is_not_an_event(tmp_path, monkeypatch):
    repo, run = _mini_repo(tmp_path, monkeypatch)
    _write(repo, "docs/claude/work/MANAGER-CHECKLIST.json", [])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    _write(repo, "docs/claude/work/MANAGER-CHECKLIST.json",
           [{"id": "MI-1", "title": "scoped", "state": "queued"},
            {"id": "MI-2", "title": "stuck", "state": "blocked"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "add")

    events, _, _ = wd.register_events(base, "HEAD")
    ids = {e["id"] for e in events}
    assert ids == {"MI-2"}, "queued is scoping (activity); blocked needs a human"


def test_queued_and_triage_are_deliberately_not_checklist_events():
    assert "queued" not in wd.CHECKLIST_EVENTS
    assert "triage" not in wd.CHECKLIST_EVENTS
    # Positive control: the states that ARE decisions.
    assert {"done", "blocked", "landed_unproven"} <= wd.CHECKLIST_EVENTS


def test_landed_unproven_is_an_event_because_merged_is_not_observed():
    """`done` and `landed_unproven` are different facts; collapsing them is the
    failure this repo keeps paying for."""
    assert "landed_unproven" in wd.CHECKLIST_EVENTS
    assert "done" in wd.CHECKLIST_EVENTS


# ── "could not look" vs "nothing changed", per source ────────────────────

def test_an_unreadable_register_is_never_reported_as_a_quiet_one(tmp_path, monkeypatch):
    repo, run = _mini_repo(tmp_path, monkeypatch)
    _write(repo, "docs/claude/health-review-backlog.json",
           [{"id": "BL-1", "status": "open"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    (repo / "docs/claude/health-review-backlog.json").write_text(
        "{ this is not json", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "corrupt")

    d = wd.build_digest(base, "HEAD")
    assert d["sourceReads"]["health backlog"] == "unreadable"
    text = wd.render(d)
    assert "UNREADABLE" in text
    assert "could not look" in text
    # Positive control: a readable register is not slandered as unreadable.
    assert d["sourceReads"]["open-items register"] != "unreadable"


def test_absent_is_an_observation_and_unreadable_is_not(tmp_path, monkeypatch):
    """`absent` means we looked and it is not there. `unreadable` means we did
    not look. Only the second may downgrade the digest."""
    repo, run = _mini_repo(tmp_path, monkeypatch)
    (repo / "x.txt").write_text("x")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    d = wd.build_digest(_sha(repo), "HEAD")
    assert set(d["sourceReads"].values()) == {"absent"}
    assert d["digestState"] == "no_changes", (
        "every register genuinely absent is a real, quiet observation")


def test_a_partially_read_window_says_so_rather_than_reading_as_clean(tmp_path, monkeypatch):
    repo, run = _mini_repo(tmp_path, monkeypatch)
    _write(repo, "docs/claude/OPEN-ITEMS.json", [{"id": "OI-1"}])
    _write(repo, "docs/claude/health-review-backlog.json", [{"id": "BL-1", "status": "open"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    (repo / "docs/claude/health-review-backlog.json").write_text("nope", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "corrupt")

    text = wd.render(wd.build_digest(base, "HEAD"))
    assert "not a clean night" in text


def test_source_reads_ship_even_on_the_unresolved_envelope():
    """A key that vanishes makes a consumer branch on absence, and absence is
    not one of the states."""
    d = wd.build_digest("definitely-not-a-ref-000000", "HEAD")
    assert set(d["sourceReads"]) == {s.name for s in wd.SOURCES}
    assert set(d["sourceReads"].values()) == {"not_attempted"}


def test_a_register_created_inside_the_window_is_not_diffed(tmp_path, monkeypatch):
    """Otherwise every row it has ever held reads as 'new tonight' — a loud lie."""
    repo, run = _mini_repo(tmp_path, monkeypatch)
    (repo / "x.txt").write_text("x")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    _write(repo, "docs/claude/OPEN-ITEMS.json",
           [{"id": f"OI-{i}"} for i in range(40)])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "create")

    events, _, counts = wd.register_events(base, "HEAD")
    assert events == []
    assert counts["open-items register"]["firstSeen"] is True
    assert "first appeared in this window" in wd.render(wd.build_digest(base, "HEAD"))


# ── merged PRs are attribution, never the headline ───────────────────────

def test_merged_pr_count_is_never_the_headline(tmp_path, monkeypatch):
    repo, run = _mini_repo(tmp_path, monkeypatch)
    _write(repo, "docs/claude/OPEN-ITEMS.json", [{"id": "OI-1"}])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    base = _sha(repo)
    _write(repo, "docs/claude/OPEN-ITEMS.json", [])
    run("git", "add", "-A")
    run("git", "commit", "-qm", "clear a row (#123)")

    d = wd.build_digest(base, "HEAD")
    assert d["window"]["mergedPrs"] == 1
    lines = wd.render(d).splitlines()
    assert "merged PRs" not in lines[1], (
        "the headline is what CHANGED; PR volume is activity and belongs on "
        "the population line")
    assert any("merged PRs" in ln for ln in lines), "…but it is still reported"


# ── the acceptance case: the night this defect was found on ──────────────

def test_the_real_night_produces_more_than_no_lifecycle_change():
    """MEASURED against this clone's own history (source: `git log`, read
    2026-09-02). Skips rather than lies when the window is not in the clone —
    a shallow checkout must not turn this into a false pass or a false fail.
    """
    import pytest
    base, head = "1bae542a", "d06cd3e9"
    if wd._resolve(base) is None or wd._resolve(head) is None:
        pytest.skip(f"{base}..{head} not in this clone (shallow)")
    d = wd.build_digest(base, head)
    assert d["digestState"] == "changes_observed"
    assert len(d["events"]) >= 20, (
        f"the busiest night on record produced {len(d['events'])} events")
    text = wd.render(d)
    assert "No state change" not in text
    # It must name what needs a human, and it must not become a changelog.
    assert "NEEDS YOU" in text
    assert len(text.splitlines()) <= 30, "a digest that lists everything is unread"
