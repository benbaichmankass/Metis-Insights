"""`constraint_readout.py::_one_line` must never MANUFACTURE a dangling id.

⚠️ THIS IS THE SAME BUG CLASS, ONE FILE OVER, AND IT SURVIVED THE FIRST FIX.
`render_due_list.py`'s own bare ``[:200]`` slice over register-supplied prose
landed inside a filed tracking id on 2026-09-12 and manufactured a reference
that resolves to nothing (see ``tests/test_due_list_id_safe_truncation.py``
for that incident). It was fixed there in #12378 (2026-09-17), id-safe up to
200 chars.

But ``constraint_readout.py`` applies a SECOND, NARROWER re-clip (140/180
chars at its own call sites) to titles that already survived the FIRST clip —
so an already-id-safe 200-char title could still be cut a SECOND time, at a
different boundary, inside the same id. Measured live: ``constraint-
readout.yml`` kept failing ``artifact-validity-guard`` on exactly this shape
through scheduled run #15 (2026-09-16, PR #12331) — ONE DAY AFTER #12378
merged — because ``_one_line(r["title"], 180)`` cut a title that
``render_due_list._clip`` had correctly left whole at 200. Run #16
(2026-09-17) was the first of 16 scheduled runs to succeed, once the module
this file exercises was also made id-safe. See MI-270 /
BL-20260911-THE-CONSTRAINT-READOUT-CRON-NOW-CLEARS-ITS-SELF-TEST-AND-DIES-ON-
SESSION-BRIEF-GUARD-REJECTING-THE-BRIEF-IT-JUST-RENDERED.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
READOUT = REPO / "scripts" / "ops" / "constraint_readout.py"
RENDERER = REPO / "scripts" / "ops" / "render_due_list.py"
GUARD = REPO / "scripts" / "ops" / "check_backlog_refs.py"


def _load(path: Path, name: str):
    """Load a `scripts/` module by path — same idiom as
    `tests/test_due_list_id_safe_truncation.py` (module registration before
    `exec_module`, required because these files use dataclasses that resolve
    `cls.__module__` through `sys.modules` at decoration time).
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


CR = _load(READOUT, "_constraint_readout_under_test")
R = _load(RENDERER, "_due_renderer_under_test_2")

#: The guard's own pattern, loaded from the guard rather than restated here.
GUARD_REF: re.Pattern = _load(GUARD, "_backlog_refs_under_test_2").REF

#: The live 2026-09-12 summary, verbatim — the string that took the
#: constraint-readout cron off the air (via `_one_line`'s own second clip)
#: one day after `render_due_list.py`'s copy of this bug was fixed.
LIVE_SUMMARY = (
    "OPERATOR-RAISED 2026-09-12: a real-money ETHUSDT SHORT 0.05 on bybit_2 "
    "open at the venue with no stop and no take-profit, invisible to every bot "
    "surface. Filed as BL-20260912-BYBIT2-SETTLE-COIN-PAGE-RETURNS-FEWER-"
    "POSITIONS-THAN-THE-VENUE-HOLDS-SO-A-REAL-MONEY-HEDGE-BOOK-IS-NEVER-"
    "FETCHED-AND-SITS-NAKED (severity critical, Tier-2)."
)


# ── the regression itself, with its positive control ───────────────────────

def test_the_old_plain_truncation_really_did_manufacture_a_dangling_id():
    """THE POSITIVE CONTROL. Without it, the test below proves nothing."""
    old = LIVE_SUMMARY[:179] + "…"
    assert old.endswith("BL-20260912-BYBI…")
    found = GUARD_REF.findall(old)
    assert found == ["BL-20260912-BYBI"], found


def test_one_line_at_180_does_not_manufacture_a_dangling_id():
    """The exact call shape that failed run #15: `_one_line(title, 180)` on
    a title `render_due_list._clip` had already clipped id-safely at 200."""
    upstream = R._clip(LIVE_SUMMARY, 200)
    # The upstream (first) clip leaves the id whole, or cuts before it —
    # either way it must not itself be dangling.
    assert GUARD_REF.findall(upstream) == []

    out = CR._one_line(upstream, 180)
    assert GUARD_REF.findall(out) == []
    assert "BL-20260912-BYBI" not in out
    assert out.startswith("OPERATOR-RAISED 2026-09-12:")
    # `upstream` is already <=180 chars (the first clip cut before "Filed
    # as"), so the second clip is a no-op: NO further truncation, NO ellipsis
    # — this is the live 2026-09-17+ shape read straight off docs/claude/
    # READOUT.md, not a constructed lookalike.
    assert len(upstream) <= 180
    assert out == upstream
    assert out.endswith("Filed as")


def test_one_line_direct_on_the_raw_summary_is_also_id_safe():
    """Not just the two-pass composition — `_one_line` alone must be safe,
    since it is called on titles from several sources, not only the
    due-list."""
    out = CR._one_line(LIVE_SUMMARY, 180)
    assert GUARD_REF.findall(out) == []
    assert "BL-20260912-BYBI" not in out


# ── the rule, stated as cases (mirrors test_due_list_id_safe_truncation.py) ─

def test_a_cut_in_ordinary_text_is_an_ordinary_truncation():
    text = "x" * 500
    assert CR._one_line(text, 160) == "x" * 159 + "…"


def test_text_at_or_under_the_limit_is_returned_whole_no_ellipsis():
    assert CR._one_line("short text", 160) == "short text"
    exact = "y" * 160
    assert CR._one_line(exact, 160) == exact


def test_an_id_that_ends_before_the_cut_is_kept_whole():
    text = "see BL-20260912-ALPHA then a long tail " + "z" * 300
    out = CR._one_line(text, 60)
    assert "BL-20260912-ALPHA" in out
    assert GUARD_REF.findall(out) == ["BL-20260912-ALPHA"]


def test_an_id_longer_than_the_limit_at_offset_zero_is_emitted_whole():
    """The one branch that may OVERRUN `limit` — see
    `render_due_list._clip`'s docstring for why that is the right call: a
    fragment is not recoverable, and a filed id is bounded."""
    ident = "BL-20260912-" + "A" * 200
    out = CR._one_line(ident + " trailing prose", 50)
    assert out == ident + "…"
    assert len(out) > 50
    assert GUARD_REF.findall(out) == [ident]


def test_none_and_empty_are_handled():
    assert CR._one_line(None, 160) == ""
    assert CR._one_line("", 160) == ""


def test_whitespace_is_collapsed_before_the_limit_is_applied():
    assert CR._one_line("a   b\n\nc", 160) == "a b c"


# ── the two id-safe clips must not drift apart ──────────────────────────────

def test_one_line_delegates_to_the_same_clip_render_due_list_uses():
    """`_one_line` must not carry its OWN copy of the id-safe primitive —
    a second copy is exactly how the two passes drifted apart the first
    time (render_due_list.py fixed, constraint_readout.py was not, for a
    full day of scheduled failures).

    `_due_lib()` does a REAL `import render_due_list` off `sys.path`, which
    is a different module OBJECT from this test's own spec-loaded `R` (a
    deliberate `_load()` under a distinct name, needed for `R`'s dataclasses
    — see that helper's docstring). So this asserts behavioural identity —
    same source file, same function name, same code object — rather than
    `is` identity, which a legitimate double-import would fail for reasons
    that have nothing to do with the drift this test exists to catch.
    """
    lib = CR._due_lib()
    assert lib.__file__ == R.__file__ == str(RENDERER)
    assert lib._clip.__code__.co_code == R._clip.__code__.co_code
    assert lib._clip.__qualname__ == R._clip.__qualname__ == "_clip"


# ── the class, not just the instance ────────────────────────────────────────

_WIDE_SLICE = re.compile(r"\[:\s*(\d+)\s*\]")
_MIN_REF_LEN = 13  # BL-20260912-A — see test_due_list_id_safe_truncation.py
_TIMESTAMP_WIDTHS = {10, 19}  # date-only and ISO-to-seconds slices


def test_no_bare_text_slice_survives_in_one_line_or_its_call_sites():
    """Stops the class recurring at a NEW site in this file.

    `_one_line` itself is now id-safe; this guards the surrounding module
    against a THIRD naive ``[:N]`` being added later over register-supplied
    prose (a title, a note, an observation) without going through it.

    Skips: (a) text inside a triple-quoted docstring/comment, since prose
    ABOUT the old bug (this module's own module-level note, this function's
    docstring) legitimately quotes the idiom it replaced; and (b) the
    `_self_test` function's OWN deliberate positive control, which
    reconstructs the pre-fix naive slice on purpose to prove the fix is real
    (`test_the_old_plain_truncation_really_did_manufacture_a_dangling_id`'s
    sibling, inline in the module rather than in this file).
    """
    offenders = []
    in_docstring = False
    for n, line in enumerate(READOUT.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        triple_count = stripped.count('"""') + stripped.count("'''")
        was_in_docstring = in_docstring
        if triple_count % 2 == 1:
            in_docstring = not in_docstring
        if was_in_docstring or in_docstring:
            continue
        if stripped.startswith(("#", '"""', "'''")):
            continue
        if "_old_naive" in line or "positive control" in stripped.lower():
            continue
        for m in _WIDE_SLICE.finditer(line):
            width = int(m.group(1))
            if width < _MIN_REF_LEN or width in _TIMESTAMP_WIDTHS:
                continue
            offenders.append(f"{n}: {stripped[:110]}")
    assert not offenders, (
        "bare width slice(s) over text outside _one_line's own (now id-safe) "
        "implementation — route register-supplied prose through _one_line() "
        "instead:\n  " + "\n  ".join(offenders)
    )
