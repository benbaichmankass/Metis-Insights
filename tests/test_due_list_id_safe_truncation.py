"""`render_due_list.py` must never MANUFACTURE a dangling tracking id.

⚠️ WHAT THIS IS ABOUT, BECAUSE IT READS LIKE A FORMATTING NIT AND IS NOT.
A bare ``[:200]`` over register-supplied prose cuts wherever 200 characters
land. On 2026-09-12 that landed inside a filed id, so every subsequent render
of ``DUE.json``/``DUE.md`` carried ``BL-20260912-BYBIT2-SETTLE-COIN-PAGE-R`` —
a prefix of a real row, and an id that resolves to nothing.
``scripts/ops/check_backlog_refs.py`` then correctly refused the commit, so the
due-list producer generated output its own repo could never accept:
``error-feed-digest.yml`` landed **0 of 22** runs and the due-list sat frozen at
2026-09-13T05:20 for four days.

MEASURED on the live registers, 2026-09-17, both arms of the same render:

    before the fix   DUE.json: 1 guard-visible id,  and it was the fragment
    after  the fix   DUE.json: 0 guard-visible ids, 127 of 128 other ids intact
                     (−38 bytes: exactly the fragment plus its separating space)

⚠️ THE CLASS WAS ALREADY KNOWN TWICE AND NEITHER READING REACHED THE PRODUCER.
`render_due_list.py` carries a comment at the `src_probes` hand-off explaining
that an id is kept on one line *because* wrapping truncates it, and
`check_backlog_refs.py::suggest_for` records measuring n=3 truncations on
2026-09-13, concluding the failure mode is REFORMATTING. Both were right about
**hand-written prose**. Neither asked whether anything truncated ids
*programmatically* — and something did, hourly, into a committed artifact.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import importlib.util

REPO = Path(__file__).resolve().parents[1]
RENDERER = REPO / "scripts" / "ops" / "render_due_list.py"
GUARD = REPO / "scripts" / "ops" / "check_backlog_refs.py"

def _load(path: Path, name: str):
    """Load a `scripts/` module by path.

    ⚠️ `sys.modules[spec.name]` BEFORE `exec_module`, and it is not optional:
    `render_due_list.py` defines a `@dataclass`, and dataclasses resolve
    `cls.__module__` through `sys.modules` at decoration time. Without the
    registration this dies at import with a bare
    `AttributeError: 'NoneType' object has no attribute '__dict__'`, which
    names no cause. Same idiom as `tests/test_analyze_research_panel.py`.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

R = _load(RENDERER, "_due_renderer_under_test")

#: The live 2026-09-12 summary, verbatim — this is the string that took the
#: due-list off the air, not a constructed lookalike.
LIVE_SUMMARY = (
    "OPERATOR-RAISED 2026-09-12: a real-money ETHUSDT SHORT 0.05 on bybit_2 "
    "open at the venue with no stop and no take-profit, invisible to every bot "
    "surface. Filed as BL-20260912-BYBIT2-SETTLE-COIN-PAGE-RETURNS-FEWER-"
    "POSITIONS-THAN-THE-VENUE-HOLDS-SO-A-REAL-MONEY-HEDGE-BOOK-IS-NEVER-"
    "FETCHED-AND-SITS-NAKED (severity critical, Tier-2)."
)

#: The guard's own pattern, loaded from the guard rather than restated here.
GUARD_REF: re.Pattern = _load(GUARD, "_backlog_refs_under_test").REF

# ── the regression itself, with its positive control ───────────────────────

def test_the_old_plain_truncation_really_did_manufacture_a_dangling_id():
    """THE POSITIVE CONTROL. Without it, the test below proves nothing.

    A guard that cannot be shown to go red is not evidence. This asserts the
    *pre-fix* behaviour genuinely produced the fragment, so the passing test
    that follows is measuring a real repair and not a vacuous one.
    """
    old = LIVE_SUMMARY[:200]
    assert old.endswith("BL-20260912-BYBIT2-SETTLE-COIN-PAGE-R")
    found = GUARD_REF.findall(old)
    assert found == ["BL-20260912-BYBIT2-SETTLE-COIN-PAGE-R"], found
    # …and it is a PREFIX of the real row, which is exactly what makes it
    # dangerous: it reads as a tracked reference.
    assert LIVE_SUMMARY.count(found[0]) == 1

def test_the_clip_does_not_manufacture_a_dangling_id():
    out = R._clip(LIVE_SUMMARY, 200)
    assert "BL-20260912-BYBIT2-SETTLE-COIN-PAGE-R" not in out
    assert GUARD_REF.findall(out) == []
    # It cut BEFORE the id rather than mangling it, and kept the prose.
    assert out.startswith("OPERATOR-RAISED 2026-09-12:")
    assert out.endswith("Filed as")
    assert len(out) <= 200

# ── the two sets must not drift ────────────────────────────────────────────

def test_clip_prefix_vocabulary_equals_the_guards_own():
    """The copy in `_ID_PREFIXES` is only safe because this fails on drift.

    If `check_backlog_refs.py::REF` learns a fourth prefix and `_clip` does
    not, the renderer resumes manufacturing fragments for that prefix and
    every other test here still passes — the exact silent-divergence failure a
    copied constant has.
    """
    m = re.search(r"\(\?:([A-Z|]+)\)-", GUARD_REF.pattern)
    assert m, f"could not read the prefix alternation out of {GUARD_REF.pattern!r}"
    assert set(m.group(1).split("|")) == set(R._ID_PREFIXES)

# ── the rule, stated as cases ──────────────────────────────────────────────

def test_a_cut_in_ordinary_text_is_an_ordinary_truncation():
    text = "x" * 500
    assert R._clip(text, 200) == "x" * 200

def test_text_shorter_than_the_limit_is_returned_whole():
    assert R._clip("short", 200) == "short"

def test_a_cut_landing_on_the_hyphen_still_drops_the_id():
    """`BL-20260912-BYBIT2-` is as dangling as `…-PAGE-R`.

    This is the case a naive `[A-Z0-9]*$` tail pattern misses, because the
    string ends on punctuation rather than mid-word.
    """
    text = "tracked by BL-20260912-BYBIT2-SETTLE and more"
    limit = len("tracked by BL-20260912-BYBIT2-")
    out = R._clip(text, limit)
    assert GUARD_REF.findall(out) == []
    assert out == "tracked by"

def test_an_id_that_ends_before_the_cut_is_kept_whole():
    """The clip must not be over-eager: a COMPLETE id is a valid reference."""
    text = "see BL-20260912-ALPHA then a long tail " + "y" * 300
    out = R._clip(text, 60)
    assert "BL-20260912-ALPHA" in out
    assert GUARD_REF.findall(out) == ["BL-20260912-ALPHA"]

def test_a_cut_landing_exactly_at_the_END_of_a_complete_id_keeps_it():
    """The case that exercises the `nxt` boundary check — and ONLY this shape.

    ⚠️ RECORDED BECAUSE MY FIRST BATTERY MISSED IT. Planting the boundary
    check away (deleting the `nxt.isalnum() or nxt == "-"` early return) was
    ABSORBED by five other tests: `_ID_TAIL` is anchored to the end of the
    cut, so in every case I had written the cut ended in ordinary prose and
    the pattern simply did not match — the early return was never what
    decided the outcome.

    It decides the outcome in exactly one shape: the cut falls immediately
    AFTER a complete id, so the tail matches, and only `nxt` (a space, i.e.
    nothing was split) stops the clip from throwing a perfectly valid
    reference away. Without the check this returns `"see"` and silently loses
    the id — over-eagerness, the opposite failure from the one being fixed,
    and just as wrong.

    Same lesson as the `CONFLICT_UNREADABLE` plant on #12372: a battery that
    never constructs the deciding input looks complete and proves nothing.
    """
    text = "see BL-20260912-ALPHA more prose here"
    limit = len("see BL-20260912-ALPHA")
    assert text[limit] == " ", "the test's own premise: the cut splits nothing"
    out = R._clip(text, limit)
    assert out == "see BL-20260912-ALPHA"
    assert GUARD_REF.findall(out) == ["BL-20260912-ALPHA"]

def test_an_id_longer_than_the_limit_at_offset_zero_is_emitted_whole():
    """The one branch that may OVERRUN `limit`, and why that is right.

    Cutting before the id would leave the empty string — losing the row's only
    content. Ids are bounded; a fragment is not recoverable. So: whole id.
    """
    ident = "BL-20260912-" + "A" * 200
    out = R._clip(ident + " trailing prose", 50)
    assert out == ident
    assert len(out) > 50
    assert GUARD_REF.findall(out) == [ident]

def test_none_and_empty_are_handled():
    assert R._clip(None, 200) == ""
    assert R._clip("", 200) == ""

def test_a_non_string_is_coerced_not_crashed():
    """`src_blocked_edges` passes a raw `blocked_on` ref, which may be a dict."""
    assert R._clip({"kind": "operator_decision"}, 200)

# ── the class, not just the instance ───────────────────────────────────────

_WIDE_SLICE = re.compile(r"\[:\s*(\d+)\s*\]")

#: The shortest string `check_backlog_refs.py::REF` can match, e.g.
#: `BL-20260912-A` — 2 (prefix) + 1 + 8 (date) + 1 + 1 (slug) = 13.
#:
#: ⚠️ THIS IS A DERIVED BOUND, NOT A TUNED THRESHOLD, and the distinction is
#: the point. A slice narrower than this CANNOT leave a guard-visible fragment,
#: because the output is shorter than any id the guard recognises — so
#: exempting it is a proof, not a guess. It also keeps the check off list
#: slices (`added[:5]`), which is what a bare width regex cannot tell apart
#: from a text clip.
_MIN_REF_LEN = 13

#: Widths that slice a TIMESTAMP, not prose: `[:19]` is ISO-to-seconds. This
#: one IS a declared exemption rather than a derived one — 19 > 13, so a text
#: clip at 19 could in principle leave a fragment. Declared rather than hidden.
_TIMESTAMP_WIDTHS = {19}

def test_the_min_ref_length_bound_is_real_and_not_a_guessed_threshold():
    """`_MIN_REF_LEN` must actually be the guard's shortest match.

    If `REF` changes shape, an exemption justified as "too short to match"
    silently stops being true. This re-derives it from the guard itself.
    """
    shortest = "BL-20260912-A"
    assert len(shortest) == _MIN_REF_LEN
    assert GUARD_REF.findall(shortest) == [shortest]
    # …and one character less is genuinely unmatchable.
    assert GUARD_REF.findall(shortest[:-1]) == []

def test_no_text_slice_survives_in_the_renderer():
    """Stops the class coming back at a NEW site, which is how it got here.

    The instance is one `[:200]`. The defect is the *idiom*: any bare width
    slice over register-supplied prose can land inside an id. Fixing eleven
    call sites without forbidding the twelfth would be the same partial fix
    that left this live after the class was already understood twice.
    """
    offenders = []
    for n, line in enumerate(RENDERER.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        # The helper's own docstring quotes the idiom it replaces.
        if stripped.startswith(("#", "*", '"""')) or "``[:200]``" in line:
            continue
        for m in _WIDE_SLICE.finditer(line):
            width = int(m.group(1))
            if width < _MIN_REF_LEN or width in _TIMESTAMP_WIDTHS:
                continue
            offenders.append(f"{n}: {stripped[:110]}")
    assert not offenders, (
        "bare width slice(s) over text — use `_clip(text, N)`, which never "
        "leaves a partial tracking id:\n  " + "\n  ".join(offenders)
    )

def test_every_clip_call_site_passes_a_positive_limit():
    src = RENDERER.read_text(encoding="utf-8")
    calls = re.findall(r"_clip\([^()]*?,\s*(-?\d+)\s*\)", src)
    assert calls, "no _clip call sites found — did the helper get reverted?"
    assert all(int(c) > 0 for c in calls), calls
