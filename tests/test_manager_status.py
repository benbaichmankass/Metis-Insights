"""Tests for the `/status` readout (`src.runtime.manager_status`).

The claims worth pinning are the ones a live `/status` could not cheaply
re-check, and the two that a green run would otherwise hide:

* the rendered message NEVER exceeds Telegram's 4096-char cap, on a fixture
  sized like the real checklist -- and when it drops lines it SAYS SO with
  counts, because a truncated list that reads as complete is the
  unstated-population error this repo has a top-level rule about;
* the operator's binding section order (checklist -> recently done -> next) is
  asserted rather than assumed;
* `behind_main` is REACHABLE and is distinguishable from `unknown` -- a stale
  tree and an ungradeable one are opposite readings and only one of them can be
  reported as current;
* an UNREADABLE checklist never renders as an EMPTY one.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.runtime import manager_status as ms


NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# ── fixtures ────────────────────────────────────────────────────────────────

def _fake_git(mapping: dict[str, tuple], default=(None, "unexpected arg")):
    """A `GitRunner` keyed on the first meaningful git argument."""
    def run(args):
        key = " ".join(args)
        for prefix, result in mapping.items():
            if key.startswith(prefix):
                return result
        return default
    return run


def _synced_git(sha="0b52157"):
    return _fake_git({
        "rev-parse --short HEAD": (sha, None),
        "rev-parse --short origin/main": (sha, None),
    })


def _behind_git(head="0b52157", main="a1b2c3d", behind=4):
    return _fake_git({
        "rev-parse --short HEAD": (head, None),
        "rev-parse --short origin/main": (main, None),
        "rev-list --count": (str(behind), None),
        "log -1": ((NOW - timedelta(hours=2, minutes=18)).isoformat(), None),
    })


#: The real file measured 2026-09-02 at `main` 0b52157: 57 items, 123,033
#: characters of JSON, mean 2,158 / max 7,532 per item. This fixture reproduces
#: the SHAPE that breaks a naive dump -- the item count, the state mix, and
#: titles at the real maximum length (117 chars) -- so the 4096 path is
#: exercised against something realistically sized rather than a toy.
_REAL_STATE_MIX = (
    ["in_flight"] * 16 + ["landed_unproven"] * 14 + ["queued"] * 11
    + ["done"] * 11 + ["blocked"] * 4 + ["ready"] * 1
)


def _big_checklist(n_owner_missing: int = 19) -> dict:
    items = []
    for idx, state in enumerate(_REAL_STATE_MIX):
        item = {
            "id": f"MI-{idx:02d}-A-REASONABLY-LONG-WORK-ITEM-IDENTIFIER",
            "title": (
                "Phase H — the control half: decisions answerable from the UI, "
                "the read gate, and everything downstream of it"
            ),
            "state": state,
            # A long free-prose key, as the real items carry.
            "note": "x" * 1800,
        }
        if idx >= n_owner_missing:
            item["owner"] = f"session_01AbCdEfGhIjKlMnOpQrSt{idx:02d}"
        if state == "blocked":
            item["blocked_on"] = [{
                "kind": "item", "ref": f"MI-{idx:02d}-SOME-BLOCKER",
                "since": "2026-09-01T21:22Z",
                "what": "attaching require_session is only tractable once the "
                        "other consumers are archived",
            }]
        items.append(item)
    return {
        "schema_version": 1,
        "cycle": "CY-20260901-OPERATING-LAYER",
        "as_of": "2026-09-02T10:23:00Z",
        "updated_at": "2026-09-02T09:36:00Z",
        "items": items,
    }


def _write_repo(tmp_path: Path, checklist: dict, sessions: dict | None = None) -> Path:
    work = tmp_path / "docs" / "claude" / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "MANAGER-CHECKLIST.json").write_text(
        json.dumps(checklist), encoding="utf-8")
    if sessions is not None:
        (work / "SESSIONS.json").write_text(json.dumps(sessions), encoding="utf-8")
    return tmp_path


# ═════════════════════════════════════════════════════════════════════════════
# Tree provenance — three states, never collapsed
# ═════════════════════════════════════════════════════════════════════════════


def test_synced_when_head_equals_origin_main():
    tree = ms.read_tree_provenance(repo_dir=Path("/x"), git=_synced_git(), now=NOW)
    assert tree.state == ms.TREE_SYNCED
    # 0 here is a REAL reading (the tree is level), unlike the `None` below.
    assert tree.behind_commits == 0
    assert "synced" in ms.render_tree_stamp(tree)


def test_behind_main_is_reachable_and_carries_its_distance():
    tree = ms.read_tree_provenance(repo_dir=Path("/x"), git=_behind_git(), now=NOW)
    assert tree.state == ms.TREE_BEHIND
    assert tree.behind_commits == 4
    assert tree.main_age_hours == pytest.approx(2.3, abs=0.05)
    stamp = ms.render_tree_stamp(tree)
    assert "behind_main" in stamp and "4 commit" in stamp
    assert "may be stale" in stamp


@pytest.mark.parametrize("git, why", [
    (_fake_git({"rev-parse --short HEAD": (None, "not a git repo")}),
     "HEAD unreadable"),
    (_fake_git({"rev-parse --short HEAD": ("0b52157", None),
                "rev-parse --short origin/main": (None, "unknown revision")}),
     "origin/main unreadable"),
    (_fake_git({"rev-parse --short HEAD": ("0b52157", None),
                "rev-parse --short origin/main": ("a1b2c3d", None),
                "rev-list --count": (None, "boom")}),
     "count unreadable"),
])
def test_unknown_when_we_could_not_look(git, why):
    tree = ms.read_tree_provenance(repo_dir=Path("/x"), git=git, now=NOW)
    assert tree.state == ms.TREE_UNKNOWN, why
    # ⚠️ NEVER a fabricated zero: "we could not count" and "zero behind" are
    # opposite statements about the same tree.
    assert tree.behind_commits is None


def test_a_tree_ahead_of_main_is_unknown_not_synced():
    """HEAD differs from origin/main but is behind it by ZERO commits.

    It carries commits `main` does not, so what of `main` it reflects cannot be
    established. Grading it `synced` would assert currency nobody measured.
    """
    git = _fake_git({
        "rev-parse --short HEAD": ("deadbee", None),
        "rev-parse --short origin/main": ("a1b2c3d", None),
        "rev-list --count": ("0", None),
    })
    tree = ms.read_tree_provenance(repo_dir=Path("/x"), git=git, now=NOW)
    assert tree.state == ms.TREE_UNKNOWN
    assert "carries commits" in tree.note


def test_behind_main_and_unknown_render_differently():
    """The whole point of the third state: a reader must be able to tell them apart."""
    behind = ms.render_tree_stamp(
        ms.read_tree_provenance(repo_dir=Path("/x"), git=_behind_git(), now=NOW))
    unknown = ms.render_tree_stamp(
        ms.read_tree_provenance(
            repo_dir=Path("/x"),
            git=_fake_git({"rev-parse --short HEAD": (None, "nope")}), now=NOW))
    assert behind != unknown
    assert "behind_main" in behind and "behind_main" not in unknown
    assert "unknown" in unknown and "unknown" not in behind


def test_every_declared_tree_state_is_reachable():
    """No declared state is a dead branch — the `collapsed-state-guard` premise."""
    reached = set()
    for git in (_synced_git(), _behind_git(),
                _fake_git({"rev-parse --short HEAD": (None, "nope")})):
        reached.add(
            ms.read_tree_provenance(repo_dir=Path("/x"), git=git, now=NOW).state)
    assert reached == set(ms.TREE_STATES)


# ═════════════════════════════════════════════════════════════════════════════
# The 4096-char path
# ═════════════════════════════════════════════════════════════════════════════


def test_realistic_checklist_never_exceeds_the_telegram_cap(tmp_path):
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW)
    assert out.messages, "a status must always produce a reply"
    for body in out.messages:
        assert len(body) <= ms.TELEGRAM_MESSAGE_LIMIT, (
            f"message of {len(body)} chars exceeds the 4096 cap")


def test_optional_sections_are_compacted_before_anything_is_dropped(tmp_path):
    """The degradation ladder: compact beats drop.

    Measured 2026-09-02 against the REAL 57-item checklist: rendering the
    optional sections in full dropped BOTH of them entirely, so the operator
    received the checklist and neither of the other two parts they asked for.
    Compaction keeps every id at reduced detail instead.
    """
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    # `expandable=False` selects the PLAIN-TEXT PACKER, which is what this test
    # is about. build_status defaults to the one-message expandable render
    # (operator, 2026-09-03); the packer's degradation ladder is unchanged,
    # still the fallback, and still asserted here.
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW,
                          expandable=False)

    assert out.compacted, "the optional sections should compact, not vanish"
    # NO row is missing -- compaction loses detail, never items.
    assert out.complete and not out.omissions
    footer = out.messages[-1]
    assert "COMPACTED (all ids present, titles omitted)" in footer
    assert "OMITTED" not in footer, (
        "nothing was dropped, so an OMITTED line would be a false claim")

    # Every compacted item is still represented, by its short id.
    body = "\n".join(out.messages)
    for idx, item in enumerate(_big_checklist()["items"]):
        if item["state"] in ("done", "landed_unproven", "queued", "ready"):
            assert f"MI-{idx:02d}" in body


def test_compaction_keeps_done_and_landed_unproven_apart(tmp_path):
    """A compact rendering is not a licence to blur two different facts."""
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    # `expandable=False` selects the PLAIN-TEXT PACKER, which is what this
    # test is about. build_status defaults to the one-message expandable
    # render (operator, 2026-09-03); the packer's degradation ladder is
    # unchanged, still the fallback, and still asserted here.
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW,
                          expandable=False)
    body = "\n".join(out.messages)
    assert "recently_done" in out.compacted
    assert "done (11):" in body
    assert "landed_unproven (14):" in body


def test_omission_count_is_stated_when_lines_are_genuinely_dropped(tmp_path):
    """Squeezed past compaction, the drop is COUNTED and NAMED, never silent."""
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW,
                          limit=1400, max_messages=1)

    assert out.omissions, "one 1400-char message cannot carry 57 items"
    assert not out.complete
    footer = out.messages[-1]
    assert "OMITTED" in footer
    dropped = sum(o.total - o.shown for o in out.omissions)
    assert str(dropped) in footer, "the footer must state HOW MANY were dropped"
    # Every omitted section is named with BOTH numbers, so the population is
    # stated rather than left for the reader to infer.
    for om in out.omissions:
        assert f"{om.total - om.shown} of {om.total} {om.section}" in footer
    assert "SUMMARY, not the full checklist" in footer
    assert ms.CHECKLIST_RELPATH in footer, "say where the full list lives"


def test_a_squeeze_drops_the_optional_sections_before_the_checklist(tmp_path):
    """Display order and DROP order are different orders, deliberately."""
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW,
                          limit=1400, max_messages=1)
    dropped = {o.section: o for o in out.omissions}
    if "in_flight" in dropped:
        # If in_flight lost lines, the optional sections must have lost ALL of
        # theirs first -- the checklist is what survives a squeeze.
        for optional in ("next", "recently_done"):
            assert optional in dropped and dropped[optional].shown == 0
    # The counts block is never dropped: it is the denominator for everything.
    assert "📋 CHECKLIST — 57 items" in out.messages[0]
    assert "in_flight 16" in out.messages[0]


def test_a_small_checklist_reports_itself_complete(tmp_path):
    checklist = {
        "as_of": "2026-09-02T10:23:00Z",
        "items": [
            {"id": "MI-01", "title": "One", "state": "in_flight",
             "owner": "session_01AAA"},
            {"id": "MI-02", "title": "Two", "state": "done"},
        ],
    }
    repo = _write_repo(tmp_path, checklist, sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW)
    assert out.complete and not out.omissions
    assert "Complete: all 2 checklist items" in out.messages[-1]
    assert "OMITTED" not in out.messages[-1]


def test_chunking_stays_within_the_message_cap_and_is_numbered(tmp_path):
    """A budget too small for one message spills, and each chunk still fits."""
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    # `expandable=False` selects the PLAIN-TEXT PACKER, which is what this
    # test is about. build_status defaults to the one-message expandable
    # render (operator, 2026-09-03); the packer's degradation ladder is
    # unchanged, still the fallback, and still asserted here.
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW,
                          limit=1200, max_messages=3, expandable=False)
    assert len(out.messages) > 1
    for body in out.messages:
        assert len(body) <= 1200 + len("📋 MANAGER STATUS (continued 2/3)\n")
    assert "continued 2/" in out.messages[1]


# ═════════════════════════════════════════════════════════════════════════════
# The binding section order
# ═════════════════════════════════════════════════════════════════════════════


def test_section_order_is_checklist_then_recently_done_then_next(tmp_path):
    """Operator directive, 2026-09-01. Not negotiable, so it is asserted."""
    checklist = {
        "as_of": "2026-09-02T10:23:00Z",
        "items": [
            {"id": "MI-01", "title": "Flying", "state": "in_flight"},
            {"id": "MI-02", "title": "Stuck", "state": "blocked",
             "blocked_on": [{"kind": "item", "ref": "MI-01", "what": "waiting"}]},
            {"id": "MI-03", "title": "Shipped", "state": "done"},
            {"id": "MI-04", "title": "Queued up", "state": "queued"},
        ],
    }
    repo = _write_repo(tmp_path, checklist, sessions={"sessions": []})
    body = "\n".join(ms.build_status(
        repo_dir=repo, git=_synced_git(), now=NOW).messages)

    checklist_at = body.index("📋 CHECKLIST")
    done_at = body.index("RECENTLY DONE")
    next_at = body.index("NEXT")
    assert checklist_at < done_at < next_at
    # And the checklist block leads the body — a status that opens with a
    # narrative is not following the directive.
    assert body.index("IN FLIGHT") < done_at
    assert body.index("BLOCKED") < done_at


def test_recently_done_states_its_population_rather_than_implying_a_window(tmp_path):
    """No completion timestamp exists in the file, so "recently" is qualified."""
    checklist = {"as_of": "2026-09-02T10:23:00Z", "items": [
        {"id": "MI-01", "title": "Shipped", "state": "done"},
        {"id": "MI-02", "title": "Merged only", "state": "landed_unproven"},
    ]}
    repo = _write_repo(tmp_path, checklist, sessions={"sessions": []})
    body = "\n".join(ms.build_status(
        repo_dir=repo, git=_synced_git(), now=NOW).messages)
    assert "not a window" in body
    # done vs landed_unproven are never collapsed — each line keeps its label.
    assert "[done]" in body and "[landed_unproven]" in body
    assert "effect NOT observed" in body


def test_blocked_items_name_what_they_are_blocked_on(tmp_path):
    checklist = {"as_of": "2026-09-02T10:23:00Z", "items": [
        {"id": "MI-03-MORNING-DIGEST", "title": "The 06:20 work digest",
         "state": "blocked",
         "blocked_on": [{"kind": "item", "ref": "MI-02-CLAUDE-CHANNEL",
                         "since": "2026-09-01T21:22Z",
                         "what": "there is no separated channel yet"}]},
    ]}
    repo = _write_repo(tmp_path, checklist, sessions={"sessions": []})
    body = "\n".join(ms.build_status(
        repo_dir=repo, git=_synced_git(), now=NOW).messages)
    assert "MI-02-CLAUDE-CHANNEL" in body
    assert "there is no separated channel yet" in body


def test_blocked_with_no_declared_edge_says_so(tmp_path):
    """`blocked` without a typed edge is reportable, not silently ordinary."""
    checklist = {"as_of": "2026-09-02T10:23:00Z", "items": [
        {"id": "MI-09", "title": "Waiting on something", "state": "blocked"},
    ]}
    repo = _write_repo(tmp_path, checklist, sessions={"sessions": []})
    body = "\n".join(ms.build_status(
        repo_dir=repo, git=_synced_git(), now=NOW).messages)
    assert "blocked_on NOT DECLARED" in body


# ═════════════════════════════════════════════════════════════════════════════
# Reading failures are never reported as emptiness
# ═════════════════════════════════════════════════════════════════════════════


def test_unreadable_checklist_is_not_rendered_as_an_empty_one(tmp_path):
    work = tmp_path / "docs" / "claude" / "work"
    work.mkdir(parents=True)
    (work / "MANAGER-CHECKLIST.json").write_text("{not json", encoding="utf-8")
    out = ms.build_status(repo_dir=tmp_path, git=_synced_git(), now=NOW)
    body = out.messages[0]
    assert out.checklist_read == "unreadable"
    assert "CHECKLIST UNREADABLE" in body
    assert "NOT a claim that nothing is in flight" in body


def test_absent_checklist_is_distinguishable_from_unreadable(tmp_path):
    out = ms.build_status(repo_dir=tmp_path, git=_synced_git(), now=NOW)
    assert out.checklist_read == "absent"
    assert "CHECKLIST ABSENT" in out.messages[0]


def test_missing_sessions_registry_says_we_could_not_look(tmp_path):
    """Never report every owner as unregistered because the registry is missing."""
    checklist = {"as_of": "2026-09-02T10:23:00Z", "items": [
        {"id": "MI-01", "title": "Flying", "state": "in_flight",
         "owner": "session_01AbCdEfGh"},
    ]}
    repo = _write_repo(tmp_path, checklist)  # no SESSIONS.json
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW)
    body = "\n".join(out.messages)
    assert out.sessions_read == "absent"
    assert "NOT that owners are unregistered" in body
    assert "registry_unread" in body


def test_status_never_raises_even_when_git_explodes(tmp_path):
    def boom(args):
        raise RuntimeError("git is on fire")

    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=boom, now=NOW)
    assert out.messages and out.tree.state == ms.TREE_UNKNOWN


# ═════════════════════════════════════════════════════════════════════════════
# Owner grading
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("owner, registered, grade", [
    ("session_01AbCdEfGhIj", {"session_01AbCdEfGhIj"}, "registered"),
    ("session_01AbCdEfGhIj", set(), "unregistered"),
    ("session_01AbCdEfGhIj", None, "registry_unread"),
    ("manager", {"session_01AbCdEfGhIj"}, "not_a_session"),
    # Measured on the real file: an owner can be free prose.
    ("drains #1-#3 merged; #4 (session_012zFXi2) running", set(), "not_a_session"),
    (None, set(), "unowned"),
    ("", set(), "unowned"),
])
def test_owner_grading_never_coerces_prose_into_a_session_id(owner, registered, grade):
    _, got = ms.grade_owner(owner, registered)
    assert got == grade


def test_a_free_prose_owner_is_never_counted_as_a_missing_registration():
    """`manager` is not an unregistered session — it is not a session at all."""
    _, grade = ms.grade_owner("manager (SHOULD HAVE BEEN DELEGATED)", set())
    assert grade == "not_a_session"


# ═════════════════════════════════════════════════════════════════════════════
# ONE message with expandable sections (operator, 2026-09-03)
#
# "the 'status' message in the claude bot needs to be reformatted - only 1
#  message each time, with drop-down sections so that i can see the whole
#  summary upfront and decide what sections to dive into"
# ═════════════════════════════════════════════════════════════════════════════


def _uncollapsed(text: str) -> str:
    """Everything OUTSIDE the expandable blockquotes — what is visible upfront."""
    out, rest = [], text
    while ms._EXPANDABLE_OPEN in rest:
        head, rest = rest.split(ms._EXPANDABLE_OPEN, 1)
        out.append(head)
        _hidden, rest = rest.split(ms._EXPANDABLE_CLOSE, 1)
    out.append(rest)
    return "".join(out)


def test_status_is_ONE_message_in_html_with_expandable_sections(tmp_path):
    """The operator asked for one message with drop-downs. Both halves pinned."""
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW)

    assert len(out.messages) == 1, "one message each time — not a chunked spill"
    assert out.parse_mode == "HTML", (
        "expandable blockquotes are an HTML-parse-mode construct; sending this "
        "as plain text would show the raw tags")
    assert ms._EXPANDABLE_OPEN in out.messages[0]
    assert len(out.messages[0]) <= ms.TELEGRAM_MESSAGE_LIMIT


def test_the_summary_is_visible_UPFRONT_not_inside_a_drop_down(tmp_path):
    """"see the whole summary upfront and decide what sections to dive into".

    Collapsing the summary would satisfy the letter of "drop-down sections" and
    defeat the ask, so every section HEADING (which carries its own count) and
    the counts/owner roll-up must sit OUTSIDE the blockquotes.
    """
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW)
    visible = _uncollapsed(out.messages[0])

    assert "MANAGER STATUS" in visible
    assert "checklist as_of" in visible, "staleness is part of the summary"
    assert "in_flight" in visible, "the lifecycle counts are the roll-up itself"
    assert "owners (in_flight+blocked)" in visible
    for heading_fragment in ("IN FLIGHT", "BLOCKED", "RECENTLY DONE", "NEXT"):
        assert heading_fragment in visible, (
            f"{heading_fragment} heading must be visible so the operator can "
            f"choose whether to dive in")


def test_item_DETAIL_is_inside_the_drop_downs_not_upfront(tmp_path):
    """The counterpart: rows go in the blockquote, or nothing was collapsed."""
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW)
    visible = _uncollapsed(out.messages[0])

    assert "MI-00-A-REASONABLY-LONG" not in visible, (
        "per-item rows belong inside the expandable section")
    assert "MI-00-A-REASONABLY-LONG" in out.messages[0], (
        "…but they are still in the message")


def test_free_prose_from_the_checklist_is_html_escaped(tmp_path):
    """⚠️ Titles are prose written by other sessions.

    An unescaped `<` or `&` either breaks Telegram's parse (400 on the WHOLE
    message) or silently swallows text as a tag. A /status that 400s is
    strictly worse than an ugly one.
    """
    checklist = {
        "as_of": "2026-09-02T00:00:00Z",
        "items": [{"id": "ITEM-X", "state": "in_flight",
                   "title": "fix <b>bold</b> & the A&B thing", "owner": "manager"}],
    }
    repo = _write_repo(tmp_path, checklist, sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW)
    body = out.messages[0]

    assert "&lt;b&gt;bold&lt;/b&gt;" in body
    assert "A&amp;B" in body
    assert "<b>bold</b>" not in body, "a raw tag from DATA would be parsed as markup"


def test_a_compacted_section_is_never_clipped_so_all_ids_present_stays_true(tmp_path):
    """The footer promises "all ids present" for a compacted section.

    A compacted line is already an id-list, so clipping one drops IDS and makes
    that promise false. Clipping is for full-form rows, where the ellipsis
    removes a TITLE and the id survives at the front of the line.
    """
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW)
    if not out.compacted:
        pytest.skip("this fixture did not need compaction")

    body = out.messages[0]
    assert "COMPACTED (all ids present, titles omitted)" in body
    for line in body.splitlines():
        if line.lstrip().startswith(("done (", "landed_unproven (", "queued (",
                                     "ready (")):
            assert not line.rstrip().endswith("…"), (
                "a clipped id-list would make the 'all ids present' claim false")


def test_a_squeeze_that_still_does_not_fit_STATES_what_was_dropped(tmp_path):
    """A silent drop is the collapsed-state failure this repo files bugs about.

    And a SECOND message would contradict the operator's instruction, so the
    only honest option left is to drop and SAY SO.
    """
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    # Above the irreducible floor (header + headings + footer), so the drop
    # path is what is exercised rather than the cut of last resort below.
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW, limit=1500)

    assert len(out.messages) == 1, "still ONE message, even under a hard squeeze"
    assert len(out.messages[0]) <= 1500
    assert out.omissions, "rows were dropped at this budget"
    assert "OMITTED" in out.messages[0], "…and the message says so"


def test_a_budget_below_the_irreducible_floor_is_CUT_and_says_so(tmp_path):
    """The summary itself cannot shrink, so a tiny budget still overflows.

    Returning an over-length string would make Telegram 400 the WHOLE reply and
    the operator would get NO status. Cutting is the lesser harm — and the cut
    is stated in the message rather than left to look like the end of a short
    but complete readout.
    """
    repo = _write_repo(tmp_path, _big_checklist(), sessions={"sessions": []})
    out = ms.build_status(repo_dir=repo, git=_synced_git(), now=NOW, limit=400)

    assert len(out.messages) == 1
    assert len(out.messages[0]) <= 400, (
        "the contract is that the message never exceeds the cap")
    assert "[CUT:" in out.messages[0], "a silent cut would read as complete"
