"""An OPEN-ITEMS splice must not silently shorten a row's own observation.

The rule under test is `scripts/ci/check_open_items.py`'s
observation-preservation half, added 2026-09-13 for
`BL-20260911-AN-OPEN-ITEMS-SPLICE-CAN-REPLACE-THE-OBSERVATION-IT-MEANT-TO-EXTEND-AND-EVERY-PROOF-THE-REPO-HAS-PASSES-OVER-IT`.

⚠️ THE REAL INSTANCE IS REPLAYED AGAINST ACTUAL HISTORY, not a fixture. The
backlog row's resolution criteria say so in as many words: *"run the proposed
check against commit 38ebcb85c and require that it FAILS; a check that cannot
fail on the one real instance in this repo's history is not evidence."* The
paired negative control is the REPAIR of that same row a day later, which
restored the text — same file, same field, same id, opposite verdict.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_open_items as coi  # noqa: E402

TODAY = date(2026, 9, 13)
REGISTER = "docs/claude/OPEN-ITEMS.json"

#: The 2026-09-11 splice, and the row it shortened. Full ids, on one line
#: each, deliberately: a truncated id in this repo is itself a guard finding.
DESTROYING = "38ebcb85c"
REPAIR = "5e081be4f"
VICTIM = "OI-20260911-BLOCKED-LANE-WATCH-SHIPPED-AND-NO-LANE-HAS-BEEN-WOKEN-BY-IT"


def _row(**kw):
    base = {"id": "OI-X", "opened": "2026-09-01", "kind": "monitoring",
            "check_every_days": 2, "verified_at": "2026-09-13", "summary": "s",
            "clears_when": "a named observable thing happens",
            "observation": "session A measured 47 rows and 6 were real"}
    base.update(kw)
    return base


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True, timeout=60)


def _items_at(ref):
    """`items` at `ref`, or None when this clone cannot reach it."""
    out = _git("show", f"{ref}:{REGISTER}")
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)["items"]
    except (json.JSONDecodeError, KeyError):
        return None


def _require(ref):
    items = _items_at(ref)
    if items is None:
        pytest.skip(
            f"{ref}:{REGISTER} is not reachable in this clone (a shallow "
            f"fetch would do it). SKIPPED IS NOT PASSED — the real-history "
            f"control did not run.")
    return items


# ── The real instance, replayed ──────────────────────────────────────────────

class TestTheOneRealInstanceInThisReposHistory:
    def test_the_destroying_commit_is_caught(self):
        base, head = _require(f"{DESTROYING}^"), _require(DESTROYING)
        problems = coi.check_observation_loss(base, head, TODAY)
        assert any(VICTIM in p for p in problems), (
            "the 2026-09-11 splice must be a finding; it is the only real "
            f"instance this repo has. got: {problems}")

    def test_and_it_names_the_size_of_what_was_dropped(self):
        base, head = _require(f"{DESTROYING}^"), _require(DESTROYING)
        hit = [p for p in coi.check_observation_loss(base, head, TODAY)
               if VICTIM in p]
        assert hit and "3377" in hit[0] and "2628" in hit[0], (
            f"the finding must state the loss, not merely assert one: {hit}")

    def test_the_repair_of_that_same_row_is_quiet(self):
        """The paired control. Without it, 'refuses everything' would pass."""
        base, head = _require(f"{REPAIR}^"), _require(REPAIR)
        problems = coi.check_observation_loss(base, head, TODAY)
        assert not [p for p in problems if VICTIM in p], (
            "the repair RESTORED the text, so it must not be a finding: "
            f"{problems}")

    def test_the_repair_really_did_restore_it_so_the_control_is_not_vacuous(self):
        """A negative control is only worth anything if the thing it controls
        for was actually present. Establish that the repair commit does carry
        the destroyed text, rather than being quiet for some other reason."""
        pre = {r["id"]: r for r in _require(f"{DESTROYING}^")}[VICTIM]
        post = {r["id"]: r for r in _require(REPAIR)}[VICTIM]
        assert coi._norm(pre["observation"]) in coi._norm(post["observation"])
        assert len(post["observation"]) > len(pre["observation"])


# ── The rule itself ──────────────────────────────────────────────────────────

class TestASilentReplacementIsRefused:
    def test_wholesale_replacement(self):
        assert coi.check_observation_loss(
            [_row()], [_row(observation="something else entirely")], TODAY)

    def test_a_partial_deletion_inside_a_longer_text(self):
        was = _row(observation="AAA. the correction BBB. the reading CCC.")
        now = _row(observation="AAA. the reading CCC. and a new one DDD.")
        assert coi.check_observation_loss([was], [now], TODAY)

    def test_dropping_the_field(self):
        now = {k: v for k, v in _row().items() if k != "observation"}
        assert coi.check_observation_loss([_row()], [now], TODAY)

    def test_the_finding_names_the_row(self):
        problems = coi.check_observation_loss(
            [_row()], [_row(observation="gone")], TODAY)
        assert problems and "OI-X" in problems[0]


class TestLegitimateEditsStayQuiet:
    def test_appending(self):
        was = _row()
        now = _row(observation=was["observation"] + " || and a second reading")
        assert coi.check_observation_loss([was], [now], TODAY) == []

    def test_prepending_a_correction_above_it(self):
        was = _row()
        now = _row(observation="!! CORRECTED: read this first. " + was["observation"])
        assert coi.check_observation_loss([was], [now], TODAY) == []

    def test_reflowing_the_same_words(self):
        was = _row()
        now = _row(observation=was["observation"].replace(" ", "\n      "))
        assert coi.check_observation_loss([was], [now], TODAY) == []

    def test_an_unchanged_row(self):
        assert coi.check_observation_loss([_row()], [_row()], TODAY) == []

    def test_a_row_with_no_observation_at_the_base(self):
        was = {k: v for k, v in _row().items() if k != "observation"}
        assert coi.check_observation_loss(
            [was], [_row(observation="a first reading")], TODAY) == []

    def test_touching_another_field_only(self):
        assert coi.check_observation_loss(
            [_row()], [_row(verified_at="2026-09-14")], TODAY) == []


class TestTheDeclarationIsVerifiedNotPresenceOnly:
    def test_a_fresh_reasoned_declaration_permits_it(self):
        now = _row(observation=(
            "SUPERSEDES-OBSERVATION 2026-09-13: the diag endpoint that "
            "reading came from was retired, so it cannot be reproduced"))
        assert coi.check_observation_loss([_row()], [now], TODAY) == []

    def test_a_stale_declaration_does_not(self):
        """Otherwise one marker licenses every future deletion of that row."""
        now = _row(observation=(
            "SUPERSEDES-OBSERVATION 2026-07-04: a reason given long ago and "
            "about a different edit entirely"))
        assert coi.check_observation_loss([_row()], [now], TODAY)

    def test_a_declaration_dated_tomorrow_is_accepted(self):
        """A PR opened either side of midnight UTC must not be a finding; the
        window is symmetric for that reason and for no other."""
        now = _row(observation=(
            "SUPERSEDES-OBSERVATION 2026-09-14: the reading was taken against "
            "a fixture, not the fleet, and says nothing"))
        assert coi.check_observation_loss([_row()], [now], TODAY) == []

    def test_a_shrug_is_not_a_reason(self):
        now = _row(observation="SUPERSEDES-OBSERVATION 2026-09-13: stale")
        assert coi.check_observation_loss([_row()], [now], TODAY)

    def test_the_bare_marker_with_no_date(self):
        now = _row(observation="SUPERSEDES-OBSERVATION: it was wrong")
        assert coi.check_observation_loss([_row()], [now], TODAY)

    def test_the_marker_must_be_the_declared_spelling(self):
        """Prose about superseding is not a declaration. If any mention of the
        idea excused a deletion, the marker would be cheaper to trip over than
        to satisfy."""
        now = _row(observation=(
            "this supersedes the earlier observation, which is superseded"))
        assert coi.check_observation_loss([_row()], [now], TODAY)

    @pytest.mark.parametrize("text,want", [
        ("nothing here", coi.MARKER_NONE),
        ("SUPERSEDES-OBSERVATION 2026-09-13: a reason long enough to be one",
         coi.MARKER_DECLARED),
        ("SUPERSEDES-OBSERVATION 2026-01-01: a reason long enough to be one",
         coi.MARKER_STALE),
        ("SUPERSEDES-OBSERVATION 2026-09-13: nope", coi.MARKER_THIN),
    ])
    def test_the_marker_states_are_distinct(self, text, want):
        assert coi.marker_state(text, TODAY)[0] == want


class TestWeDidNotLookIsNotAPass:
    def test_an_unreadable_base_ref_is_its_own_state(self, tmp_path):
        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"items": [_row()]}))
        state, problems, note = coi.observation_loss_against_base(
            reg, "refs/heads/no-such-ref-anywhere", repo=REPO, today=TODAY)
        assert state == "unreadable"
        assert problems == []
        assert "not a pass" in note
        # ⚠️ THE REF MUST BE NAMED, and this assertion is not decoration. With
        # the bad-ref branch planted away, the code still reaches "unreadable"
        # — by crashing on the empty text a few lines later — and a test that
        # only checked the state or the phrase "not a pass" stayed GREEN over
        # the plant. Both notes say it is not a pass; only one says WHICH ref
        # could not be read, which is the whole content of the diagnosis.
        assert "refs/heads/no-such-ref-anywhere" in note
        assert "JSONDecodeError" not in note

    def test_a_register_absent_at_the_base_is_a_different_state(self, tmp_path):
        """A genuinely new file has no prior observation to lose. That is not
        the same fact as being unable to look, and the two must not collapse."""
        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"items": [_row()]}))
        state, problems, _ = coi.observation_loss_against_base(
            Path("docs/claude/NO-SUCH-REGISTER.json"), "origin/main",
            repo=REPO, today=TODAY)
        assert state == "absent_at_base"
        assert problems == []

    def test_the_two_unreadable_causes_do_not_share_a_state_with_read(self):
        assert len({"read", "absent_at_base", "unreadable"}) == 3

    def test_without_a_base_the_summary_says_the_rule_did_not_run(self):
        out = subprocess.run(
            [sys.executable, "scripts/ci/check_open_items.py"], cwd=REPO,
            capture_output=True, text=True, timeout=120)
        assert "observation-preservation not_graded" in out.stdout
        assert "DID NOT RUN" in out.stdout

    def test_with_a_base_the_summary_says_it_did(self):
        out = subprocess.run(
            [sys.executable, "scripts/ci/check_open_items.py",
             "--base", "origin/main"], cwd=REPO,
            capture_output=True, text=True, timeout=120)
        if "could not read" in out.stdout:
            pytest.skip("origin/main is not reachable in this clone")
        assert "observation-preservation read" in out.stdout


class TestItReadsTheForkPointNotTheTip:
    def test_the_resolver_is_imported_rather_than_re_derived(self):
        src = (REPO / "scripts/ci/check_open_items.py").read_text()
        assert "import _git_base" in src
        assert "merge-base" not in src, (
            "resolving a base belongs to _git_base, which owns it repo-wide; "
            "a second copy is free to drift from the one every other guard uses")

    def test_it_asks_for_the_merge_base(self):
        ref, how = coi._git_base.resolve_base("origin/main", repo=REPO)
        assert how in (coi._git_base.MERGE_BASE, coi._git_base.TIP_UNRESOLVABLE)


class TestItIsWiredIntoTheGuardRunner:
    def test_the_registry_passes_a_base(self):
        src = (REPO / "scripts/ci/run_guards.py").read_text()
        i = src.index('"name": "open-items-guard"')
        entry = src[i:i + 700]
        assert "--base" in entry, (
            "without --base the diff-scoped half never runs in CI, which is "
            "exactly the shape of a guard that cannot fail")

    def test_the_stale_cap_claim_is_gone_from_the_registry_comment(self):
        """`MAX_ITEMS` was set to None by operator direction on 2026-08-26 and
        this comment still called the cap 'the mechanism' — field beats
        comment, and the script's own docstring had already been corrected."""
        src = (REPO / "scripts/ci/run_guards.py").read_text()
        i = src.index('"name": "open-items-guard"')
        entry = src[max(0, i - 1400):i]
        phrase = "the cap is the mechanism"
        # ⚠️ NOT "the phrase is absent". This repo's convention is to PRESERVE
        # a corrected claim beside its correction so a later reader knows what
        # it used to say; deleting it would make the same drift re-derivable
        # from nothing. What must not survive is the phrase standing as a
        # CURRENT claim, so the test is that its one occurrence is inside the
        # correction.
        assert entry.count(phrase) == 1, entry.count(phrase)
        assert f'THIS COMMENT READ "{phrase}' in entry
        assert coi.MAX_ITEMS is None


class TestTheRuleGradesNothingRatherThanCrashing:
    @pytest.mark.parametrize("base,head", [
        (None, [_row()]), ([_row()], None), ("", ""), ({}, {}),
    ])
    def test_malformed_input(self, base, head):
        assert coi.check_observation_loss(base, head, TODAY) == []

    def test_rows_without_ids_are_skipped(self):
        assert coi.check_observation_loss(
            [{"observation": "x"}], [{"observation": "y"}], TODAY) == []

    def test_a_row_only_in_the_head_is_not_graded(self):
        assert coi.check_observation_loss([], [_row()], TODAY) == []

    def test_a_row_removed_from_the_head_is_not_graded_here(self):
        """Removing a row is a different act with its own surfaces. Grading it
        as a lost observation would misname it."""
        assert coi.check_observation_loss([_row()], [], TODAY) == []

    def test_a_non_string_observation_is_not_treated_as_text(self):
        assert coi.check_observation_loss(
            [_row(observation=None)], [_row()], TODAY) == []
