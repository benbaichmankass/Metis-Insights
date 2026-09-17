"""Tests for the session-receipt generator and its read route (MI-290).

The load-bearing properties are MUTATION-CHECKED — a test that only asserts the
happy path would pass against a generator that silently dropped its population
or graded a failed read as a negative, which are precisely the two defects this
module exists to make unrepresentable.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.ops import session_receipt as sr  # noqa: E402


# ── Rule 1: a count cannot be written without its population ────────────────

def test_counted_carries_its_population():
    assert sr.counted(5, "of 9 closed rows") == {"value": 5, "population": "of 9 closed rows"}


@pytest.mark.parametrize("population", ["", "   ", None])
def test_counted_refuses_a_bare_number(population):
    """MUTATION CONTROL. If `counted` ever stops refusing, a receipt can express
    '349 commits' with no denominator — the exact thing MI-290 names as
    forbidden, and the reason this is a type rather than a habit."""
    with pytest.raises(sr.ReceiptError):
        sr.counted(349, population)


# ── Rule 2: backlog ids are emitted verbatim, never typed ───────────────────

# ⚠️ FIXTURE IDS ARE BUILT AT RUNTIME, NEVER WRITTEN AS LITERALS — this module's
# own rule applied to its own tests. An invented id written literally is a
# tracking reference that resolves to nothing, and `check_backlog_refs` fails a
# diff that introduces one. It caught the first version of these fixtures, and
# one of them turned out to be an exact PREFIX of a real filed row, silently
# shadowing it. Building from a stem keeps the source clean of anything a guard
# can read as a reference while still exercising the real shapes.
_STEM = "B" + "L-" + "20260101" + "-SELFTEST"
_ALPHA_ONE = _STEM + "-ALPHA-ONE"
_ALPHA_TWO = _STEM + "-ALPHA-TWO"
_SOLO = "B" + "L-" + "20260102" + "-SELFTEST-SOLO"
KNOWN = {_ALPHA_ONE, _ALPHA_TWO, _SOLO}


def test_exact_id_passes_through():
    assert sr.expand_backlog_id(_SOLO, KNOWN) == _SOLO


@pytest.mark.parametrize("suffix", ["…", "", "."])
def test_a_unique_prefix_is_completed_not_passed_through(suffix):
    """The 2026-09-17 reference receipt carried exactly this shape and could not
    be committed, because `check_backlog_refs` fails a diff introducing a
    reference that resolves to nothing."""
    assert sr.expand_backlog_id(_SOLO[:-5] + suffix, KNOWN) == _SOLO


def test_a_dangling_id_is_refused():
    with pytest.raises(sr.ReceiptError, match="resolves to NO backlog row"):
        sr.expand_backlog_id(_STEM + "-NEVER-FILED-ANYWHERE", KNOWN)


def test_an_ambiguous_prefix_is_refused_not_first_matched():
    """MUTATION CONTROL. Resolving an ambiguous prefix by first match would emit
    a confident WRONG id, which is worse than refusing: it reads as tracked."""
    with pytest.raises(sr.ReceiptError, match="ambiguous"):
        sr.expand_backlog_id(_STEM + "-ALPHA", KNOWN)


def test_every_id_in_the_live_backlogs_resolves_to_itself():
    """A positive control for the resolver against real data — so a refusal in a
    receipt means the id is bad, not that the resolver is broken."""
    ids, unreadable = sr.load_backlog_ids(repo=REPO)
    assert not unreadable, f"a backlog file could not be read: {unreadable}"
    assert len(ids) > 500, f"only {len(ids)} ids loaded — the corpus looks truncated"
    for sample in sorted(ids)[:25]:
        assert sr.expand_backlog_id(sample, ids) == sample


# ── three never-collapsed landing states ────────────────────────────────────

def test_landing_states_are_three_distinct_values():
    assert len(set(sr.LANDING_STATES)) == 3


def test_a_failed_git_read_is_could_not_look_never_not_landed(tmp_path):
    """THE load-bearing property. Grading an unreadable repo as `not_landed`
    would report a merged PR as unmerged, which is how a receipt tells the
    operator work was lost that in fact landed."""
    row = sr.verify_pr_landed(12072, repo=tmp_path / "nope")
    assert row["state"] == sr.COULD_NOT_LOOK
    assert row["state"] != sr.NOT_LANDED


def test_a_failed_attribution_reports_null_counts_not_zero(tmp_path):
    """A zero here would read as 'this session landed nothing'. `None` is the
    only honest value for a measurement that was not taken."""
    out = sr.attribute_commits("session_01Abc", repo=tmp_path / "nope")
    assert out["state"] == sr.COULD_NOT_LOOK
    assert out["attributed_count"] is None
    assert out["scanned_count"] is None


def test_a_malformed_session_id_is_refused_not_matched_against_nothing():
    """MUTATION CONTROL. A malformed id silently matches no trailer, which
    renders identically to a session that landed nothing."""
    with pytest.raises(sr.ReceiptError):
        sr.attribute_commits("not-a-session-id")


# ── attribution and landing, against the real repo ──────────────────────────

def test_attribution_reports_a_denominator_alongside_the_numerator():
    out = sr.attribute_commits("session_01DoesNotExistAnywhere", repo=REPO)
    if out["state"] == sr.COULD_NOT_LOOK:
        pytest.skip("no origin/main in this checkout")
    # nobody's commits, but the population must still be stated and non-zero
    assert out["attributed_count"]["value"] == 0
    assert out["scanned_count"]["value"] > 0
    assert "origin/main" in out["scanned_count"]["population"]


def test_a_known_landed_pr_is_measured_as_landed():
    """Positive control. #12072 merged as c068b7448 (the 2026-09-17 receipt's
    own record, re-verified here rather than trusted)."""
    row = sr.verify_pr_landed(12072, repo=REPO)
    if row["state"] == sr.COULD_NOT_LOOK:
        pytest.skip("no origin/main in this checkout")
    assert row["state"] == sr.LANDED
    assert row["subject"].endswith("(#12072)")


def test_an_impossible_pr_number_is_measured_as_not_landed():
    """Negative control — without it, a verifier that answered `landed` to
    everything would pass the positive test above."""
    row = sr.verify_pr_landed(99999999, repo=REPO)
    if row["state"] == sr.COULD_NOT_LOOK:
        pytest.skip("no origin/main in this checkout")
    assert row["state"] == sr.NOT_LANDED


# ── the receipt shape ───────────────────────────────────────────────────────

def test_all_five_sections_are_present_even_when_empty():
    r = sr.build_receipt(session_id="session_01Abc", role="test", repo=REPO)
    for key in ("decisions", "sessions_spawned", "own_work", "totals", "errors"):
        assert key in r, f"section {key} missing — an omitted section reads as 'there were none'"


def test_the_narrative_sections_declare_they_are_scaffolds():
    """So a half-written receipt is visibly incomplete rather than reading as a
    claim that no decision was taken and no mistake was made."""
    r = sr.build_receipt(session_id="session_01Abc", repo=REPO)
    assert r["decisions"]["basis"] == "author_must_complete"
    assert r["errors"]["basis"] == "author_must_complete"
    assert r["own_work"]["basis"] == "measured"


def test_markdown_renders_every_section_heading():
    r = sr.build_receipt(session_id="session_01Abc", repo=REPO)
    md = sr.render_markdown(r)
    for letter, name in sr.SECTIONS:
        assert f"## {letter}. {name}" in md


def test_markdown_surfaces_an_unreadable_attribution_rather_than_a_zero(tmp_path):
    r = sr.build_receipt(session_id="session_01Abc", repo=tmp_path / "nope")
    md = sr.render_markdown(r)
    assert "could not be read" in md
    assert "could_not_look" in md


def test_receipt_path_is_on_the_manager_surface():
    """⚠️ Load-bearing and easy to "tidy" away. `check_manager_scope`'s
    MANAGER_SURFACE admits `docs/claude/work/**` and NOT `docs/claude/receipts/**`,
    and a MANAGER is the primary author of receipts. On 2026-09-17 the reference
    receipt was refused by R2 at the shorter path."""
    assert sr.RECEIPTS_RELDIR.startswith("docs/claude/work/")
    p = sr.receipt_path("session_01Abc", repo=REPO)
    assert p.parent == REPO / "docs" / "claude" / "work" / "receipts"


def test_receipt_path_refuses_a_traversal_stem():
    with pytest.raises(sr.ReceiptError):
        sr.receipt_path("../../etc/passwd")


def test_the_trailer_regex_matches_check_manager_scope_exactly():
    """Two definitions of "which session wrote this commit" is how the manager
    guards and the receipt come to disagree about the same commit."""
    from scripts.ci import check_manager_scope as cms
    assert sr.SESSION_TRAILER.pattern == cms.SESSION_TRAILER.pattern


def test_self_test_passes():
    assert sr._self_test() == 0


# ── the read route ──────────────────────────────────────────────────────────

def test_route_reports_a_scaffold_section_distinctly_from_a_complete_one():
    from src.web.api.routers import work

    complete = work._receipt_completeness({
        "decisions": {"basis": "author_must_complete", "rows": [{"question": "q"}]},
        "sessions_spawned": {"basis": "x", "rows": [{}]},
        "own_work": {"basis": "measured"},
        "totals": {"basis": "measured"},
        "errors": {"basis": "author_must_complete", "rows": [{"what": "x"}]},
    })
    assert complete["complete"] is True

    scaffolded = work._receipt_completeness({
        "decisions": {"basis": "author_must_complete", "rows": []},
        "sessions_spawned": {"basis": "x", "rows": []},
        "own_work": {"basis": "measured"},
        "totals": {"basis": "measured"},
        "errors": {"basis": "author_must_complete", "rows": []},
    })
    assert scaffolded["complete"] is False
    assert scaffolded["sections"]["errors"] == "scaffold"
    # ⚠️ NOT "complete". An empty ERRORS block is an unfilled scaffold, not a
    # session declaring it made no mistakes.
    assert scaffolded["sections"]["decisions"] == "scaffold"

    missing = work._receipt_completeness({"own_work": {"basis": "measured"}})
    assert missing["sections"]["errors"] == "missing"
    assert missing["sections"]["errors"] != "scaffold"


def test_route_payload_is_best_effort_and_states_its_population():
    from src.web.api.routers import work

    payload = work._receipts_payload()
    assert payload["readState"] in {"read", "absent", "unreadable"}
    assert "population" in payload["summary"]
    # Whatever the state, it must never be an empty list with no explanation.
    if payload["readState"] != "read":
        assert payload["reason"]


def test_route_is_registered_in_the_api_tier_policy():
    """A new route needs a row in docs/api-tier-policy.md or CI fails — and a
    row in the API reference is not a substitute."""
    policy = (REPO / "docs" / "api-tier-policy.md").read_text(encoding="utf-8")
    assert "GET /api/bot/work/receipts" in policy
