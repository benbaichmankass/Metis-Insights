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


def test_all_four_states_are_reachable_so_none_is_decorative():
    reser = json.dumps(json.loads(BASE), indent=2, ensure_ascii=False) + "\n"
    honest = BASE.replace("\n  ]\n}\n", ',\n    {"id": "Z", "title": "n"}\n  ]\n}\n')
    assert {G.grade(BASE, BASE, path="p")["state"],
            G.grade(BASE, honest, path="p")["state"],
            G.grade(BASE, reser, path="p")["state"],
            G.grade(None, BASE, path="p")["state"]} == set(G.ALL_STATES)


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
