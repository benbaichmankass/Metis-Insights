"""MI-258 — a decision request must not outlive its subject, and must not be
withdrawn on a guess.

The load-bearing assertions here are the REFUSALS. Anything that grades
``subject_gone`` withdraws a question from the operator's work list, so every
test that a wrong input does NOT reach ``gone`` is protecting against hiding a
live decision — the expensive failure direction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.decision_subject import (
    OFFLINE_UNRESOLVABLE_KINDS,
    SUBJECT_GONE,
    SUBJECT_KINDS,
    SUBJECT_LIVE,
    SUBJECT_STATES,
    SUBJECT_UNDECLARED,
    SUBJECT_UNKNOWN,
    SubjectResolver,
    grade_subject,
    is_actionable,
    normalise_subject,
)

REAL = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A minimal repo shaped like the real one, so nothing depends on live data."""
    (tmp_path / "docs/claude/work/objects").mkdir(parents=True)
    (tmp_path / "docs/claude/OPEN-ITEMS.json").write_text(
        json.dumps({"items": [{"id": "OI-LIVE-ROW-THAT-EXISTS"},
                              {"id": "OI-PREFIXED-ROW-WITH-A-LONG-TAIL"}]})
    )
    (tmp_path / "docs/claude/work/MANAGER-CHECKLIST.json").write_text(
        json.dumps({"items": [{"id": "MI-LIVE"}]})
    )
    for name in ("health", "performance", "ml", "research"):
        (tmp_path / f"docs/claude/{name}-review-backlog.json").write_text(
            json.dumps([{"id": f"BL-{name.upper()}-LIVE"}])
        )
    (tmp_path / "docs/claude/work/objects/a.yaml").write_text(
        "id: WO-LIVE\ndecision_requests:\n- id: DEC-LIVE\n"
    )
    (tmp_path / "some/file.md").parent.mkdir(parents=True)
    (tmp_path / "some/file.md").write_text("x")
    return tmp_path


# ── the four states are four, and nobody collapses them ──────────────────────


def test_the_four_states_are_distinct_and_ordered_by_attention():
    assert len(set(SUBJECT_STATES)) == 4
    assert SUBJECT_STATES[-1] == SUBJECT_GONE, "the only withdrawing state sorts last"
    assert SUBJECT_UNDECLARED != SUBJECT_UNKNOWN


def test_undeclared_is_not_unknown(store: Path):
    """*Nothing to check* and *we checked and could not tell* are opposite facts.

    Measured 25 of 26 on 2026-09-11, so pooling them would report a 96%
    resolution failure for work the resolver was never asked to do.
    """
    undeclared = grade_subject({"id": "R"}, SubjectResolver(store))
    assert undeclared["subjectState"] == SUBJECT_UNDECLARED
    unknown = grade_subject(
        {"id": "R", "subject": {"kind": "pull_request", "ref": "123"}},
        SubjectResolver(store),
    )
    assert unknown["subjectState"] == SUBJECT_UNKNOWN


def test_every_grade_carries_all_four_keys(store: Path):
    """A key that VANISHES makes a consumer branch on absence."""
    for req in ({"id": "R"}, {"id": "R", "subject": {"kind": "open_item", "ref": "OI-LIVE-ROW-THAT-EXISTS"}}):
        g = grade_subject(req, SubjectResolver(store))
        assert set(g) == {"subject", "subjectState", "subjectBasis", "subjectNote"}
        assert g["subjectBasis"], "a state with no basis is an unprovenanced verdict"


# ── the resolver finds positives, so a `gone` means something ────────────────


@pytest.mark.parametrize(
    "kind,ref",
    [
        ("open_item", "OI-LIVE-ROW-THAT-EXISTS"),
        ("checklist_item", "MI-LIVE"),
        ("backlog_row", "BL-HEALTH-LIVE"),
        ("backlog_row", "BL-RESEARCH-LIVE"),
        ("work_object", "WO-LIVE"),
        ("decision_request", "DEC-LIVE"),
        ("path", "some/file.md"),
    ],
)
def test_every_resolvable_kind_can_find_a_positive(store: Path, kind: str, ref: str):
    """The denominator for every negative. A probe that cannot find a positive
    is not evidence when it is quiet."""
    assert SubjectResolver(store).resolve({"kind": kind, "ref": ref})[0] == SUBJECT_LIVE


@pytest.mark.parametrize(
    "kind,ref",
    [
        ("open_item", "OI-DELETED"),
        ("checklist_item", "MI-DELETED"),
        ("backlog_row", "BL-DELETED"),
        ("work_object", "WO-DELETED"),
        ("decision_request", "DEC-DELETED"),
        ("path", "some/deleted.md"),
    ],
)
def test_a_genuine_absence_grades_gone(store: Path, kind: str, ref: str):
    assert SubjectResolver(store).resolve({"kind": kind, "ref": ref})[0] == SUBJECT_GONE


# ── THE REFUSALS ────────────────────────────────────────────────────────────


def test_a_truncated_ref_is_never_graded_gone(store: Path):
    """THE MEASURED FAILURE, pinned.

    A YAML line wrap cut `OI-20260831-PER-ACCOUNT-ARBITRATION-…` mid-token in
    the live store and a naive reader graded the LIVE row `gone`. A ref that is
    a strict prefix of a live id cannot be graded either way.
    """
    state, basis = SubjectResolver(store).resolve(
        {"kind": "open_item", "ref": "OI-PREFIXED-ROW-WITH-A-"}
    )
    assert state == SUBJECT_UNKNOWN
    assert "truncated" in basis
    assert "OI-PREFIXED-ROW-WITH-A-LONG-TAIL" in basis, "name the id we suspect"


def test_an_unreadable_register_grades_unknown_never_gone(store: Path, tmp_path: Path):
    """The closed-flat-invariant inversion, refused.

    A failed read graded as the reassuring value is how a falsifier gets
    silenced by exactly the condition it exists to catch.
    """
    (store / "docs/claude/OPEN-ITEMS.json").write_text("{ not json")
    state, basis = SubjectResolver(store).resolve(
        {"kind": "open_item", "ref": "OI-LIVE-ROW-THAT-EXISTS"}
    )
    assert state == SUBJECT_UNKNOWN
    assert "could not be read" in basis


def test_a_missing_register_grades_unknown_never_gone(tmp_path: Path):
    state, _ = SubjectResolver(tmp_path).resolve({"kind": "open_item", "ref": "OI-X"})
    assert state == SUBJECT_UNKNOWN


def test_a_partial_work_store_may_say_live_but_never_gone(store: Path):
    """709 of 710 files read is not evidence the 710th did not hold the id."""
    (store / "docs/claude/work/objects/broken.yaml").write_text("id: [unclosed\n  : :\n")
    res = SubjectResolver(store)
    assert res.resolve({"kind": "work_object", "ref": "WO-LIVE"})[0] == SUBJECT_LIVE
    state, basis = res.resolve({"kind": "work_object", "ref": "WO-DELETED"})
    assert state == SUBJECT_UNKNOWN
    assert "could not be parsed" in basis


@pytest.mark.parametrize("kind", ["pull_request", "branch"])
def test_a_kind_needing_the_network_refuses_rather_than_fetching(store: Path, kind: str):
    """Fetching on an API request path is the wedge class this repo paid for twice.

    ⚠️ THE KINDS ARE SPELLED OUT HERE AND NOT PARAMETRIZED OVER
    ``OFFLINE_UNRESOLVABLE_KINDS`` — mutation-tested 2026-09-11: emptying that
    frozenset made this test SKIP rather than FAIL, i.e. the guard vanished
    along with the thing it guards. A test whose population is the constant
    under test cannot fail when that constant is wrong.
    """
    assert kind in SUBJECT_KINDS, "the kind must still be a declarable one"
    state, basis = SubjectResolver(store).resolve({"kind": kind, "ref": "x"})
    assert state == SUBJECT_UNKNOWN
    assert "no network call" in basis


def test_the_offline_unresolvable_set_still_names_both_network_kinds():
    """The membership itself, asserted independently — see the note above."""
    assert OFFLINE_UNRESOLVABLE_KINDS == frozenset({"pull_request", "branch"})


@pytest.mark.parametrize(
    "subject",
    [
        {"kind": "not-a-kind", "ref": "x"},
        {"kind": "open_item", "ref": ""},
        {"ref": "OI-LIVE-ROW-THAT-EXISTS"},
    ],
)
def test_a_malformed_declaration_grades_unknown_never_gone(store: Path, subject: dict):
    assert SubjectResolver(store).resolve(subject)[0] == SUBJECT_UNKNOWN


def test_a_broken_declaration_is_not_reported_as_no_declaration():
    """An author's broken block and an absent block have different remedies."""
    assert normalise_subject({"kind": "open_item"}) is not None
    assert normalise_subject(None) is None
    assert normalise_subject("OI-X") is None


def test_a_resolver_that_raises_does_not_grade_gone(store: Path):
    class Exploding(SubjectResolver):
        def resolve(self, subject):  # type: ignore[override]
            raise RuntimeError("boom")

    g = grade_subject({"subject": {"kind": "open_item", "ref": "OI-X"}}, Exploding(store))
    assert g["subjectState"] == SUBJECT_UNKNOWN


# ── `actionable` — the one owner of "is this work?" ─────────────────────────


def test_only_gone_withdraws_a_question():
    for state in SUBJECT_STATES:
        expected = state != SUBJECT_GONE
        assert is_actionable(settled=False, subject_state=state) is expected


def test_a_settled_question_is_never_work_whatever_its_subject():
    for state in SUBJECT_STATES:
        assert is_actionable(settled=True, subject_state=state) is False


# ── the live corpus, so the module is anchored to real data ─────────────────


def test_the_one_measured_instance_grades_gone_on_the_real_store():
    """`DR-20260908-CLEAR-THE-LOUD-TRADE-PRIORITISATION-ROW` asks whether to
    remove a row that was deleted on 2026-09-09 (`ba5fccc1a`, #11509).

    ⚠️ This asserts the DECLARATION and the REGISTER together. If the row is
    ever restored to OPEN-ITEMS.json this test correctly fails — the question
    would become live again, and that is the behaviour, not a broken test.
    """
    import yaml

    path = REAL / "docs/claude/work/objects/WO-20260908-BUILD-THE-N-BOOK-RANKING-KEY-HARNESS.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    req = next(
        r for r in data["decision_requests"]
        if r["id"] == "DR-20260908-CLEAR-THE-LOUD-TRADE-PRIORITISATION-ROW"
    )
    assert req["subject"]["kind"] == "open_item"
    g = grade_subject({"subject": normalise_subject(req["subject"])}, SubjectResolver(REAL))
    assert g["subjectState"] == SUBJECT_GONE
    assert is_actionable(settled=False, subject_state=g["subjectState"]) is False


def test_no_live_request_declares_a_subject_this_module_cannot_name():
    """A kind nobody taught the resolver grades `unknown` forever — silently
    useless. Catch it at the declaration instead."""
    import yaml

    bad = []
    for path in sorted((REAL / "docs/claude/work/objects").glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for req in (data.get("decision_requests") or []):
            if not isinstance(req, dict):
                continue
            sub = req.get("subject")
            if isinstance(sub, dict) and sub.get("kind") not in SUBJECT_KINDS:
                bad.append((req.get("id"), sub.get("kind")))
    assert not bad, f"unknown subject kind(s) declared: {bad}"


# ── the awaitingOperator subtraction (MI-258 follow-up, caught by arithmetic) ──


def test_a_moot_in_transit_row_does_not_hide_a_live_question():
    """THE BUG THIS FUNCTION EXISTS FOR, pinned.

    `moot_unanswered` spans every unsettled state; the operator's backlog spans
    only AWAITING_OPERATOR_STATES. `in_transit` is unsettled and is NOT in that
    backlog, so subtracting the wider count from the narrower sum removes a
    question the operator really does owe.
    """
    from src.runtime.work_decisions import awaiting_operator_count

    rows = [
        {"answerState": "in_transit", "subjectState": SUBJECT_GONE},
        {"answerState": "not_submitted", "subjectState": "subject_live"},
    ]
    awaiting, moot_unanswered, moot_awaiting = awaiting_operator_count(rows)
    # The live question SURVIVES. Naively subtracting `moot_unanswered` gives 0.
    assert awaiting == 1
    assert moot_unanswered == 1, "the moot in-transit row is still REPORTED"
    assert moot_awaiting == 0, "…but it was never in the operator's backlog"


def test_a_moot_awaiting_row_is_taken_off_the_operators_backlog():
    from src.runtime.work_decisions import awaiting_operator_count

    rows = [{"answerState": "engaged_not_settled", "subjectState": SUBJECT_GONE}]
    assert awaiting_operator_count(rows) == (0, 1, 1)


def test_awaiting_operator_is_never_negative_over_any_state_combination():
    """An exhaustive sweep, because this is the failure mode that hides work."""
    from itertools import product

    from src.runtime.work_decisions import ANSWER_STATES, awaiting_operator_count

    for states in product(ANSWER_STATES, repeat=2):
        for subs in product(SUBJECT_STATES, repeat=2):
            rows = [{"answerState": a, "subjectState": s} for a, s in zip(states, subs)]
            awaiting, wide, narrow = awaiting_operator_count(rows)
            assert awaiting >= 0
            assert narrow <= wide, "the subtracted count can never exceed the reported one"
            assert narrow <= len(rows)


def test_the_awaiting_vocabulary_excludes_in_transit():
    """An in-transit answer waits on a COMMITTER, never on the operator."""
    from src.runtime.work_decisions import AWAITING_OPERATOR_STATES

    assert "in_transit" not in AWAITING_OPERATOR_STATES
    assert "committed" not in AWAITING_OPERATOR_STATES
    assert "not_submitted" in AWAITING_OPERATOR_STATES
