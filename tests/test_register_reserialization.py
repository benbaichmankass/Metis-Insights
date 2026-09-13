"""A PR can rewrite a shared register without losing a single row.

On 2026-09-12T05:44:18Z a session merged `origin/main` into a live branch from
a clone where the register merge driver was NOT armed and hand-resolved four
register conflicts. The `OPEN-ITEMS.json` resolution re-serialized the whole
file. Semantically it was CLEAN — 84 items in, 84 out, nothing lost — so this
is a PROVENANCE defect: the file was re-attributed to that PR, every sibling
branch touching it was made to conflict, and the row-level history went.

⚠️ THE ONLY THING THAT NOTICED WAS A CANARY IN AN UNRELATED TOOL NAMING A
DIFFERENT CONDITION — `manager_preflight.py --self-test` hardcodes "OPEN-ITEMS
does not round-trip" as an expectation, so it fires on churn as a SIDE EFFECT
and reads as a broken self-test. A session could "fix" it by editing the
expectation and destroy the only detector.

MEASURED over EVERY commit-vs-first-parent pair that changed one of the five
driver-bound registers in the last 400 commits touching each — POPULATION 1768
pairs: **1757 clean · 9 reserialized · 2 unreadable**. All nine are
`OPEN-ITEMS.json`, the one file `merge_json_register.py` names as not
byte-reproducible. Both `unreadable` cases are real and one minute apart: a
register was committed WITH CONFLICT MARKERS IN IT (b66d4ad91) and repaired by
the next commit (4c79a7eab) — which is why "we could not parse it" has to be
its own state and must never read as clean.

⚠️ `lost > budget` ALONE WAS NOT ENOUGH, and history is how that was
established rather than argued: it returned 13 findings, four of them ordinary
merges at ratios 1.02x-2.79x. Re-serialization is a WHOLE-FILE property, and
the measured separation on that axis is not close — nine members at
0.960-0.975 of the file, then a gap of 0.58, then 0.378 and below.

Run: ``python3 -m pytest tests/test_register_reserialization.py``
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ci" / "check_register_reserialization.py"


def _load():
    spec = importlib.util.spec_from_file_location("_regreser", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

ROWS = [{"id": f"BL-{i:04d}", "title": f"row {i} — em-dash", "detail": "x" * 40}
        for i in range(40)]


def _ser(rows, stamp="2026-09-01"):
    return ('{\n  "schema_version": 1,\n  "updated_at": "%s",\n  "items": [\n'
            % stamp
            + ",\n".join("    " + json.dumps(r, ensure_ascii=False) for r in rows)
            + "\n  ]\n}\n")


BASE = _ser(ROWS)


def test_self_test_passes():
    ok, fails = G._selftest(quiet=True)
    assert ok, f"register-reserialization self-test failures: {fails}"


# ── THE FINDING ─────────────────────────────────────────────────────────────

def test_a_pure_reserialization_is_caught_although_no_row_changed():
    """The whole point: nothing is lost and it is still a defect."""
    head = json.dumps(json.loads(BASE), indent=2, ensure_ascii=False) + "\n"
    v = G.grade(BASE, head, path="p")
    assert v["state"] == G.RESERIALIZED
    assert v["rows_touched"] == 0, "the fixture must isolate FORMATTING"
    assert v["lost_fraction"] >= G.LOST_FRACTION_FLOOR


def test_a_reserialization_hiding_behind_a_real_append_is_still_caught():
    """The incident's actual shape — one honest new row, whole file rewritten.
    An edit-sized budget must not excuse it."""
    rows = ROWS + [{"id": "BL-9999", "title": "new", "detail": "y"}]
    head = json.dumps(json.loads(_ser(rows)), indent=2, ensure_ascii=False) + "\n"
    v = G.grade(BASE, head, path="p")
    assert v["state"] == G.RESERIALIZED
    assert v["rows_touched"] == 1


# ── THE POSITIVE CONTROLS — without these a guard that fails everything wins ─

def test_an_honest_append_is_clean():
    head = BASE.replace("\n  ]\n}\n", ',\n    {"id": "BL-9999", "title": "new"}\n  ]\n}\n')
    assert G.grade(BASE, head, path="p")["state"] == G.CLEAN


def test_an_honest_edit_is_clean():
    doc = json.loads(BASE)
    doc["items"][3]["detail"] = "z" * 40
    head = BASE.replace(json.dumps(ROWS[3], ensure_ascii=False),
                        json.dumps(doc["items"][3], ensure_ascii=False))
    assert G.grade(BASE, head, path="p")["state"] == G.CLEAN


def test_a_diff_over_budget_but_not_a_rewrite_is_clean_AND_reaches_that_branch():
    """The control the measured history forced, and it must REACH the floor.

    `lost > budget` alone called four ordinary commits re-serialized. A fixture
    that comes in UNDER the budget proves the budget and says nothing about the
    floor — which is how the first attempt at this control was written.
    """
    big = [{"id": f"BL-{i:05d}", "title": f"row {i} — em-dash", "detail": "x" * 30}
           for i in range(400)]
    base = _ser(big)
    lines = base.splitlines(keepends=True)
    for i in range(4, 34):
        lines[i] = lines[i].replace('", "', '",  "')
    head = "".join(lines).replace(
        "\n  ]\n}\n", ',\n    {"id": "BL-99999", "title": "new"}\n  ]\n}\n')
    v = G.grade(base, head, path="p")
    assert v["lost_lines"] > v["budget"], "this control does not reach the floor"
    assert v["state"] == G.CLEAN
    assert v["lost_fraction"] < G.LOST_FRACTION_FLOOR


# ── "WE COULD NOT LOOK" IS ITS OWN STATE, and it fired twice on real history ─

@pytest.mark.parametrize("base,head", [
    (None, BASE),
    (BASE, None),
    (BASE, "<<<<<<< HEAD\n" + BASE),
    (BASE, '{"no_rows": 1}'),
])
def test_a_register_we_could_not_read_is_never_clean(base, head):
    assert G.grade(base, head, path="p")["state"] == G.UNREADABLE


def test_identical_bytes_are_untouched_not_clean():
    """Different facts: `clean` means we compared and it held; `untouched`
    means there was nothing to compare."""
    assert G.grade(BASE, BASE, path="p")["state"] == G.UNTOUCHED


def test_every_state_is_reachable_so_none_is_decorative():
    reser = json.dumps(json.loads(BASE), indent=2, ensure_ascii=False) + "\n"
    honest = BASE.replace("\n  ]\n}\n", ',\n    {"id": "Z", "title": "n"}\n  ]\n}\n')
    assert {G.grade(BASE, BASE, path="p")["state"],
            G.grade(BASE, honest, path="p")["state"],
            G.grade(BASE, reser, path="p")["state"],
            G.grade(None, BASE, path="p")["state"],
            G.grade(None, BASE, path="p",
                    base_state=G._git_base.ABSENT_AT_BASE)["state"],
            } == set(G.ALL_STATES)


# ── `new register` vs `we could not look`: the same bytes, opposite facts ───
# `git show <ref>:<path>` fails identically for a path absent from a real ref
# and for a ref that cannot be resolved. This guard collapsed them one layer
# above `_git_base.read_at`, which already told them apart — so a PR that
# legitimately ADDED a register graded `unreadable` and FAILED.
#
# BOTH DIRECTIONS OR NEITHER. Fixing only the first would turn every unreadable
# base into a pass, which is strictly worse than the bug being fixed: the bug
# was loud and wrong, that would be quiet and wrong.

def test_a_register_ABSENT_at_the_fork_point_is_new_not_unreadable():
    assert G.grade(None, BASE, path="p",
                   base_state=G._git_base.ABSENT_AT_BASE)["state"] == G.NEW_REGISTER


def test_an_UNREADABLE_base_is_still_unreadable_after_the_split():
    assert G.grade(None, BASE, path="p",
                   base_state=G._git_base.UNREADABLE)["state"] == G.UNREADABLE


def test_the_permissive_verdict_cannot_be_reached_by_saying_nothing():
    """A caller that omits `base_state` gets the STRICT verdict.

    The default matters more than it looks: `grade` is called from a self-test,
    from these tests and from `check`, and if omission meant "new register" then
    every one of the existing `grade(None, ...)` call sites would silently flip
    to a pass. The permissive verdict must cost an explicit say.
    """
    assert G.grade(None, BASE, path="p")["state"] == G.UNREADABLE


def test_an_absent_base_with_an_unreadable_head_is_not_a_new_register():
    """Both sides gone is not a register being added — it is nothing to grade."""
    assert G.grade(None, None, path="p",
                   base_state=G._git_base.ABSENT_AT_BASE)["state"] == G.UNREADABLE


def test_a_new_register_reports_an_UNDEFINED_fraction_not_zero():
    """0 base lines lost is a real reading; 0/0 of a file is not `none of it`."""
    v = G.grade(None, BASE, path="p", base_state=G._git_base.ABSENT_AT_BASE)
    assert v["lost_lines"] == 0
    assert v["lost_fraction"] is None


# ── THE SCOPE, which must not be able to go quietly empty ───────────────────

def test_the_register_set_comes_from_gitattributes_and_is_not_empty():
    """A hardcoded second list is how a guard drifts from the driver it
    protects, and an empty list would make every run vacuously green."""
    regs = G.registers_from_gitattributes(REPO)
    assert len(regs) >= 3, regs
    assert "docs/claude/OPEN-ITEMS.json" in regs
    for rel in regs:
        assert (REPO / rel).is_file(), f"{rel} is bound to the driver and absent"


def test_unmarked_candidates_are_reported_so_clean_is_not_read_as_coverage():
    """`clean` over 5 files is a SCOPE result. The count of register-shaped
    JSON files nobody bound to the driver is published on every run."""
    unmarked = G.unmarked_registers(REPO)
    marked = set(G.registers_from_gitattributes(REPO))
    assert not (set(unmarked) & marked), "a file cannot be both"
    assert isinstance(unmarked, list)


def test_the_floor_and_the_budget_are_BOTH_required():
    """Either alone is a different guard. The budget alone called four ordinary
    commits re-serialized; the floor alone would miss a small register that was
    genuinely rewritten only if its budget also allowed it — so the conjunction
    is the claim, and this pins that it is a conjunction."""
    doc = json.loads(BASE)
    doc["items"][0]["detail"] = "q" * 40
    head = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    v = G.grade(BASE, head, path="p")
    assert v["state"] == G.RESERIALIZED
    assert v["lost_lines"] > v["budget"] and v["lost_fraction"] >= G.LOST_FRACTION_FLOOR


# ── TWO CONTROLS A MUTATION RUN FORCED, and both are the same shape ─────────
# Each pins one half of the conjunction by making the OTHER half satisfied, so
# the plant that deletes it changes a verdict. Without them, `lost > budget`
# and `lost - head` could each be removed with the suite still green — which is
# exactly the "a control that cannot reach the branch it targets" failure this
# lane has now hit five times in one day.

def test_a_LARGE_HONEST_REMOVAL_over_the_floor_is_still_clean():
    """Pins the BUDGET. Pruning most of a small register clears the whole-file
    floor on its own, and it is not a re-serialization — the budget is the only
    thing that says so. Deleting the budget makes this go red."""
    base = _ser(ROWS)                       # 40 rows
    head = _ser(ROWS[:4])                   # honestly keep four
    v = G.grade(base, head, path="p")
    assert v["lost_fraction"] >= G.LOST_FRACTION_FLOOR, \
        "this control does not reach the budget branch"
    assert v["state"] == G.CLEAN, v
    assert v["lost_lines"] <= v["budget"]


def test_a_LARGE_HONEST_APPEND_is_clean_which_pins_the_DIRECTION():
    """Pins that `lost` counts BASE lines that vanished, not head lines that
    appeared. A big honest append adds many lines and removes one; measured the
    wrong way round it looks like a rewrite. Both directions are large for a
    real re-serialization, so only an asymmetric case can tell them apart."""
    base = _ser(ROWS[:4])
    head = _ser(ROWS)                       # 36 rows appended
    v = G.grade(base, head, path="p")
    assert v["state"] == G.CLEAN, v
    assert v["lost_lines"] <= 2, \
        f"an append must lose ~nothing from the base; got {v['lost_lines']}"


# ---------------------------------------------------------------------------
# THE BASE-SELECTION CONTROL, 2026-09-13.
#
# Every test above -- and all 11 self-test calls -- exercise the PURE `grade()`.
# `check()` was exercised ZERO times, and `check()` is where the base is chosen.
# That is why the defect below survived: the decision was covered, the caller
# was not. (Same shape as BL-20260912-SEVEN-OF-TEN-BASE-READING-GUARDS-... and
# the `_at_ref` escape it records.)
#
# ⚠️ A GREEN RUN IS NOT EVIDENCE HERE. With the defect present the guard still
# grades `clean` -- masked by a 75% floor AND by a budget that inflates with the
# same contaminated input. A control asserting only the exit code passes against
# the defect. It must assert the REPORTED NUMBER.
# ---------------------------------------------------------------------------


def _run(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _register(rows):
    return json.dumps({"items": rows}, indent=2, ensure_ascii=False) + "\n"


def _row(i):
    return {"id": f"BL-{i:04d}", "title": f"row {i} — with an em-dash",
            "detail": "x" * 40}


def _repo_with_moved_base():
    """A repo whose branch is behind a main that appended rows of its own.

    Returns (repo_path, register_relpath). The branch makes ONE honest append.
    """
    tmp = tempfile.mkdtemp()
    repo = Path(tmp)
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "user.email", "t@example.invalid")
    _run(repo, "config", "user.name", "t")
    rel = "docs/claude/health-review-backlog.json"
    (repo / "docs" / "claude").mkdir(parents=True)
    (repo / ".gitattributes").write_text(
        f"{rel} merge=jsonregister\n", encoding="utf-8")
    base_rows = [_row(i) for i in range(40)]
    (repo / rel).write_text(_register(base_rows), encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "base")

    _run(repo, "checkout", "-q", "-b", "feature")
    (repo / rel).write_text(_register(base_rows + [_row(900)]), encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "one honest appended row")

    # main moves ahead, the way other sessions move it: more rows.
    _run(repo, "checkout", "-q", "main")
    (repo / rel).write_text(
        _register(base_rows + [_row(i) for i in range(500, 520)]), encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "other sessions append")
    _run(repo, "checkout", "-q", "feature")
    return repo, rel


def test_an_honest_append_against_a_MOVED_base_reports_zero_lost_lines():
    """PLANTED: reading the base at the TIP blames this diff for main's rows.

    Asserts the NUMBER, not the exit code -- the guard grades `clean` either
    way, which is exactly how this survived.
    """
    repo, rel = _repo_with_moved_base()

    # Sanity: the fixture really does have a base that moved ahead, or the
    # control proves nothing.
    behind = _run(repo, "rev-list", "--count", "HEAD..main").stdout.strip()
    assert behind == "1", f"fixture did not move main ahead: {behind}"

    out = G.check("main", root=repo)
    row = next(r for r in out["rows"] if r["path"] == rel)
    assert row["lost_lines"] == 0, (
        f"an honest one-row append reported {row['lost_lines']} lost line(s). "
        "Those are main's rows, not this diff's — the base is being read at the "
        "TIP instead of the fork point.")
    assert row["state"] == G.CLEAN

    # NEGATIVE CONTROL: the defect, reinstated. Read at the tip and the same
    # honest append must report a NON-ZERO count, or this test cannot fail and
    # is decoration.
    tip_text = subprocess.run(
        ["git", "show", f"main:{rel}"], cwd=repo,
        capture_output=True, text=True).stdout
    head_text = (repo / rel).read_text(encoding="utf-8")
    at_tip = G.grade(tip_text, head_text, path=rel)
    assert at_tip["lost_lines"] > 0, (
        "reverting to a tip read did NOT change the reported number, so this "
        "control does not discriminate and proves nothing")


def test_check_resolves_the_fork_point_rather_than_trusting_the_ref():
    """The resolver is USED, not merely imported — a resolver nothing is proven
    to use is decoration (the lesson this guard's sibling row records)."""
    repo, rel = _repo_with_moved_base()
    merge_base = _run(repo, "merge-base", "HEAD", "main").stdout.strip()
    tip = _run(repo, "rev-parse", "main").stdout.strip()
    assert merge_base and tip and merge_base != tip, "fixture is degenerate"

    # Grading against the ref NAME must equal grading against the fork-point
    # SHA, and must NOT equal grading against the tip.
    by_name = next(r for r in G.check("main", root=repo)["rows"]
                   if r["path"] == rel)
    by_sha = next(r for r in G.check(merge_base, root=repo)["rows"]
                  if r["path"] == rel)
    assert by_name["lost_lines"] == by_sha["lost_lines"] == 0


# ---------------------------------------------------------------------------
# THE SPLIT AT THE LAYER IT ACTUALLY LIVED ON. The pure tests above argue the
# policy; these two prove `check` HANDS IT THE STATE. A policy the caller never
# consults is decoration — the exact lesson the fork-point row records, where
# `grade` was well covered and `check` was exercised zero times.
# ---------------------------------------------------------------------------


def _repo_that_ADDS_a_register():
    """A branch whose diff introduces a register that does not exist at base.

    Returns (repo_path, new_register_relpath, pre_existing_relpath).
    """
    tmp = Path(tempfile.mkdtemp())
    _run(tmp, "init", "-q", "-b", "main")
    _run(tmp, "config", "user.email", "t@example.invalid")
    _run(tmp, "config", "user.name", "t")
    old = "docs/claude/health-review-backlog.json"
    new = "docs/claude/brand-new-register.json"
    (tmp / "docs" / "claude").mkdir(parents=True)
    (tmp / ".gitattributes").write_text(f"{old} merge=jsonregister\n",
                                        encoding="utf-8")
    (tmp / old).write_text(_register([_row(i) for i in range(40)]),
                           encoding="utf-8")
    _run(tmp, "add", "-A")
    _run(tmp, "commit", "-qm", "base")

    _run(tmp, "checkout", "-q", "-b", "feature")
    (tmp / ".gitattributes").write_text(
        f"{old} merge=jsonregister\n{new} merge=jsonregister\n", encoding="utf-8")
    (tmp / new).write_text(_register([_row(i) for i in range(5)]),
                           encoding="utf-8")
    _run(tmp, "add", "-A")
    _run(tmp, "commit", "-qm", "add a new register")
    return tmp, new, old


def test_a_PR_that_ADDS_a_register_passes_AND_the_row_is_still_NAMED():
    """PLANTED: before the split this exact diff graded `unreadable` -> ok=False.

    ⚠️ ASSERTING ONLY `ok` WOULD PASS IF THE ROW HAD VANISHED ALTOGETHER, which
    is a different and worse fix — a register silently dropped from the scope is
    how `clean` stops meaning anything. So the STATE is asserted, by name.
    """
    repo, new, _old = _repo_that_ADDS_a_register()
    out = G.check("main", root=repo)

    row = next((r for r in out["rows"] if r["path"] == new), None)
    assert row is not None, (
        f"{new} is absent from the graded rows entirely. Passing by dropping a "
        "register from the scope is not passing.")
    assert row["state"] == G.NEW_REGISTER, (
        f"a register this diff ADDS graded {row['state']!r}. Before the split it "
        "graded 'unreadable', which FAILED a legitimate PR.")
    assert out["ok"] is True
    assert new in out["new_registers"], "the new register is not reported"

    # NEGATIVE CONTROL: the collapse, reinstated. Drop the state on the way in
    # and the very same inputs must go back to `unreadable`, or this test does
    # not discriminate and proves nothing.
    head_text = (repo / new).read_text(encoding="utf-8")
    collapsed = G.grade(None, head_text, path=new)
    assert collapsed["state"] == G.UNREADABLE, (
        "withholding the base state did NOT change the verdict, so this control "
        "cannot fail and is decoration")


def test_check_still_FAILS_on_a_register_it_could_not_read():
    """The other direction, through `check`.

    A register present at the fork point whose head carries conflict markers is
    the real `unreadable` this repo has seen twice (b66d4ad91, repaired one
    commit later). The split must not have bought the new-register pass by
    making every unreadable register pass.
    """
    repo, _new, old = _repo_that_ADDS_a_register()
    (repo / old).write_text(
        "<<<<<<< HEAD\n" + (repo / old).read_text(encoding="utf-8"),
        encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "commit a register with conflict markers in it")

    out = G.check("main", root=repo)
    row = next(r for r in out["rows"] if r["path"] == old)
    assert row["state"] == G.UNREADABLE, (
        f"a conflict-markered register graded {row['state']!r} — 'we could not "
        "parse it' must never read as fine")
    assert out["ok"] is False
    assert old in out["unreadable"]
