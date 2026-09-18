"""A directory declaration must not collide a branch with its OWN slug file.

`BL-20260912-SCOPE-OVERLAP-RESOLVES-A-DIRECTORY-DECLARATION-ONTO-PER-BRANCH-FILES-THAT-CANNOT-COLLIDE`.

MEASURED on PR #11896's scope-overlap comment (2026-09-12T07:22:06Z): it
reported 11 paths declared by a LIVE session, and **7 of those 11 are files no
other branch can write** — 4 resolving `.github/pr-landing/` and 3 resolving
`.github/pr-automerge-requests/` onto that branch's own slug file.

Those directories exist BECAUSE of a collision. `.github/pr-automerge-request`
was a single shared file that collided by construction; it hit live on
2026-08-21, cost #10086 a merge commit and a full CI cycle, and was split into
per-branch files for exactly that reason. `.github/merge-slots/` is the same
repair applied to `session-board.json::merge_slot`, whose shared field moved in
**39 of the last 40** commits touching it. The audit resolved a directory
declaration onto every file inside, so the split's entire benefit was invisible
to it and it re-reported the collision the split had eliminated.

That is the desensitised alarm `CLAUDE.md` calls this repo's own worst failure
mode, with an inverted incentive on top: declaring a directory precisely made
the alarm louder.

⚠️ **THE POSITIVE CONTROL IS THE LOAD-BEARING HALF, and the row says so.** A
matcher that simply stopped reading `.github/pr-landing/` would satisfy the
"noise stopped" half on its own and would DELETE a true positive — a session
declaring that directory and then editing somebody ELSE's declaration is a real
collision. Without the control, *"the noise stopped"* is indistinguishable from
*"the check stopped"*. Every suppression assertion below is therefore paired
with one proving the audit still fires.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ci" / "check_scope_overlap.py"


def _load():
    spec = importlib.util.spec_from_file_location("_scope_overlap_pb", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

MINE = "claude/mi280-u96-scope-overlap-per-branch"
MY_SLUG = "mi280-u96-scope-overlap-per-branch"
THEIR_SLUG = "some-other-session-branch"

#: A START from ANOTHER live session declaring the two directories, in the shape
#: the extractor actually reads (backticked paths under a declare section).
OTHER_START = f"""▶️ **START** — another lane
Session: `session_0OTHERotherotherother`
Branch: `claude/{THEIR_SLUG}`

Touching: `.github/pr-landing/`, `.github/pr-automerge-requests/`,
`.github/merge-slots/`, `docs/claude/OPEN-ITEMS.json`
"""


def _assess(changed, *, branch=MINE):
    return G.assess(
        changed,
        [{"body": OTHER_START, "url": "u", "created_at": "2026-09-12T07:00:00Z"}],
        my_branch=branch, my_pr=99999, my_body="",
    )


# ── 1. THE PURE PREDICATE ────────────────────────────────────────────────────

def test_owner_is_the_stem_for_a_file_directly_in_a_per_branch_dir():
    for d in G.PER_BRANCH_DIRS:
        assert G.per_branch_owner(f"{d}/{MY_SLUG}.json") == MY_SLUG


def test_a_file_outside_those_dirs_has_no_owner():
    assert G.per_branch_owner("docs/claude/OPEN-ITEMS.json") is None
    assert G.per_branch_owner("scripts/ci/check_scope_overlap.py") is None


def test_a_nested_file_has_no_owner_because_the_convention_says_nothing_about_it():
    assert G.per_branch_owner(".github/pr-landing/nested/dir/f.json") is None


def test_readme_in_such_a_dir_is_shared_and_never_exempt():
    """It is the one file in there that CAN genuinely collide."""
    for d in G.PER_BRANCH_DIRS:
        assert G.per_branch_owner(f"{d}/README.md") is None
        assert G.is_own_per_branch_file(f"{d}/README.md", MINE) is False


def test_another_branchs_slug_file_is_not_mine():
    assert G.is_own_per_branch_file(f".github/pr-landing/{THEIR_SLUG}.json", MINE) is False


def test_an_unknown_branch_is_never_an_exemption():
    """`we could not look` must not silence the true positive."""
    assert G.is_own_per_branch_file(f".github/pr-landing/{MY_SLUG}.json", None) is False
    assert G.is_own_per_branch_file(f".github/pr-landing/{MY_SLUG}.json", "") is False


def test_the_slug_rule_is_the_imported_one_not_a_second_copy():
    import check_pr_landing  # noqa: F401  (import path set up by the module)
    assert G.branch_slug is check_pr_landing.branch_slug
    assert G.PER_BRANCH_DIRS == (check_pr_landing.LANDING_DIR,
                                 check_pr_landing.AUTOMERGE_DIR,
                                 check_pr_landing.BRANCH_SLOT_DIR)


# ── 2. THE SUPPRESSION ───────────────────────────────────────────────────────

def test_my_own_slug_files_are_not_reported_against_a_directory_declaration():
    v = _assess([f".github/pr-landing/{MY_SLUG}.json",
                 f".github/pr-automerge-requests/{MY_SLUG}.txt",
                 f".github/merge-slots/{MY_SLUG}.json"])
    assert v["hits"] == []
    assert v["state"] == "no_overlap"


def test_the_suppression_is_counted_never_hidden():
    """A suppressor that hides its suppressions cannot be audited."""
    v = _assess([f".github/pr-landing/{MY_SLUG}.json",
                 f".github/merge-slots/{MY_SLUG}.json"])
    assert v["sole_writer_suppressed"] == 2


def test_it_is_counted_apart_from_self_declared_because_they_differ():
    """`self_declared` = I declared it. This = nobody else could have written it."""
    v = _assess([f".github/pr-landing/{MY_SLUG}.json"])
    assert v["sole_writer_suppressed"] == 1
    assert v["self_declared"] == 0


def test_every_early_return_still_carries_the_key():
    """A consumer reading the field must not KeyError on a refusal path."""
    for v in (G.assess([], [], my_branch=MINE),
              G.assess(["a.py"], [], my_branch=MINE),
              G.assess(["a.py"], [{"body": OTHER_START, "url": "u", "created_at": "t"}],
                       my_branch=MINE, board_health="stale")):
        assert "sole_writer_suppressed" in v


# ── 3. THE POSITIVE CONTROLS — the half that must never be deleted ───────────

def test_editing_ANOTHER_branchs_slug_file_is_still_a_real_collision():
    """The true positive the row explicitly says must be kept."""
    v = _assess([f".github/pr-landing/{THEIR_SLUG}.json"])
    assert v["state"] == "overlap"
    assert len(v["hits"]) == 1
    assert v["sole_writer_suppressed"] == 0
    assert v["hits"][0]["file"] == f".github/pr-landing/{THEIR_SLUG}.json"


def test_an_EXACT_declaration_of_my_own_slug_file_is_still_reported():
    """A directory prefix is noise; naming this very file is an explicit claim."""
    exact = f"""▶️ **START** — another lane
Session: `session_0OTHERotherotherother`
Branch: `claude/{THEIR_SLUG}`

Touching: `.github/pr-landing/{MY_SLUG}.json`
"""
    v = G.assess([f".github/pr-landing/{MY_SLUG}.json"],
                 [{"body": exact, "url": "u", "created_at": "t"}],
                 my_branch=MINE, my_pr=99999, my_body="")
    assert v["state"] == "overlap"
    assert v["sole_writer_suppressed"] == 0


def test_a_genuinely_shared_register_is_untouched_by_this_change():
    v = _assess(["docs/claude/OPEN-ITEMS.json"])
    assert v["state"] == "overlap"
    assert len(v["hits"]) == 1
    assert v["sole_writer_suppressed"] == 0


def test_the_readme_in_a_per_branch_dir_still_collides():
    v = _assess([".github/merge-slots/README.md"])
    assert v["state"] == "overlap"
    assert v["sole_writer_suppressed"] == 0


def test_a_mixed_diff_suppresses_only_the_own_file():
    v = _assess([f".github/pr-landing/{MY_SLUG}.json",
                 f".github/pr-landing/{THEIR_SLUG}.json",
                 "docs/claude/OPEN-ITEMS.json"])
    assert v["sole_writer_suppressed"] == 1
    assert {h["file"] for h in v["hits"]} == {
        f".github/pr-landing/{THEIR_SLUG}.json", "docs/claude/OPEN-ITEMS.json"}


def test_the_modules_own_self_test_still_passes():
    assert G._self_test() == 0
