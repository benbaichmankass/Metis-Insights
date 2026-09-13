#!/usr/bin/env python3
# wiring: manual-only - this is a LIBRARY for the session-end routine and for
# any tooling that appends a backlog row (import `append_row`), plus a CLI for a
# one-off append. Giving it a scheduled runner would mean scheduling the act of
# filing a finding, which is not a thing that can be automated.
"""Append a row to a review backlog WITHOUT reformatting the file.

WHY THIS EXISTS
---------------
The three review backlogs are the only files a session is REQUIRED to edit at
session end (``CLAUDE.md`` § "Every session"), and editing them is booby-trapped.

They are stored ``ensure_ascii=False`` — real em-dashes on disk. Python's
``json.dumps`` defaults to ``ensure_ascii=True``, so the obvious
read-append-write idiom escapes every non-ASCII character and rewrites every
line containing one. Nothing warns: the file stays valid JSON and the row is
correctly added. Measured on a ONE-ROW append: **21,307 insertions, 21,288
deletions**.

That is not a cosmetic problem. ``impossibility-claim-guard`` (and every other
diff-scoped guard) reads *added-vs-origin/main*, so a whole-file reformat
**re-attributes every pre-existing unsubstantiated row to the appending PR** —
turning someone's unrelated change red for eight rows they never wrote, and
burying the ones they did.

``BL-20260820-BACKLOG-APPEND-REFORMATS-AND-REATTRIBUTES`` names the remedy
this file implements: *"a helper both the session-end routine and any tooling
append through — that round-trips the untouched file and REFUSES to write when
its own serialisation does not reproduce the original byte-for-byte."* It is
explicit that documenting "remember ensure_ascii=False" is NOT sufficient,
because the file already documents plenty that sessions miss.

THE REFUSAL IS THE FEATURE. This helper does not guess the format and it does
not "fix" a file whose formatting it cannot reproduce. It detects the exact
serialisation by round-tripping the ORIGINAL bytes against a candidate list,
and if none reproduces them it refuses to write at all — because a helper that
silently falls back to a default is the trap wearing a helper's clothes.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, Optional, Tuple

#: Candidate serialisations, most-likely first. A file whose bytes none of these
#: reproduce is REFUSED rather than reformatted.
_CANDIDATES: Tuple[Dict[str, Any], ...] = (
    {"indent": 2, "ensure_ascii": False},
    {"indent": 1, "ensure_ascii": False},
    {"indent": 4, "ensure_ascii": False},
    {"indent": 2, "ensure_ascii": True},
    {"indent": 1, "ensure_ascii": True},
)


class FormatNotReproducible(RuntimeError):
    """The file's byte layout matches no known serialisation — refuse to write."""


def detect_format(raw: str, doc: Any) -> Tuple[Dict[str, Any], str]:
    """Return (json.dumps kwargs, trailing) that reproduce *raw* byte-for-byte."""
    for kw in _CANDIDATES:
        body = json.dumps(doc, **kw)
        for trailing in ("\n", ""):
            if body + trailing == raw:
                return kw, trailing
    raise FormatNotReproducible(
        "no candidate serialisation reproduces the file byte-for-byte; refusing "
        "to write rather than reformat it (that reformat re-attributes every "
        "pre-existing row to this diff; see "
        "BL-20260820-BACKLOG-APPEND-REFORMATS-AND-REATTRIBUTES)"
    )


class SimilarRowExists(Exception):
    """A row already in this backlog reads like the one being filed.

    Raised so the caller must LOOK before filing. It is not a verdict that the
    row is a duplicate — see :mod:`scripts.ops.backlog_search`: a duplicate
    should be dropped, while a RECURRENCE is evidence the first fix did not
    hold and is one of the most valuable rows there is. Only a human reading
    both can tell, which is exactly why this raises instead of dropping the row.
    """


#: Overlap above which :func:`append_row` refuses without acknowledgement.
#: CHOSEN, not tuned: the duplicate that motivated this (an exit-label row
#: filed 2026-08-26 that restated two 2026-08-22 rows) scores 0.80 and 0.70
#: against them, while the unrelated neighbours in the same result sit at 0.60
#: and below. There is no larger labelled set behind it — it is one worked
#: example, and the override exists because the threshold will be wrong
#: sometimes.
SIMILAR_REFUSE_SCORE = 0.65


def append_row(path: pathlib.Path, row: Dict[str, Any],
               *, updated_at: Optional[str] = None,
               similar_ok: bool = False) -> int:
    """Append *row* to the backlog at *path*. Returns the new item count.

    Raises :class:`FormatNotReproducible` rather than writing a reformatted file.

    Also raises :class:`SimilarRowExists` when an existing row in the SAME
    backlog overlaps this one's text above :data:`SIMILAR_REFUSE_SCORE` — pass
    ``similar_ok=True`` once you have read the candidates and decided.

    WHY THE SECOND REFUSAL EXISTS. Operator directive 2026-08-26: *"We aren't
    using the backlog/lessons learned logs correctly if we still keep running
    into the same fuck ups."* The id check above catches only an EXACT repeat,
    which never happens — ids carry the filing date. The backlogs are 951 / 109
    / 104 rows, so checking by hand is not practical, so nobody does, so the
    log accumulates lessons and teaches none. Measured the same day: a row was
    filed as a fresh discovery that restated two rows from four days earlier
    whose mechanism was already named and half already fixed. It was caught by
    accident, not by any check.

    ⚠️ **Silence here is not proof of novelty.** The probe is token overlap; a
    row phrased in different words scores zero. It makes the cheap check
    unavoidable — it does not make the expensive one unnecessary.
    """
    raw = path.read_text()
    doc = json.loads(raw)
    kw, trailing = detect_format(raw, doc)

    items = doc["items"] if isinstance(doc, dict) else doc
    original_items = list(items)
    existing = {i.get("id") for i in items if isinstance(i, dict)}
    if row.get("id") in existing:
        raise ValueError(f"{row['id']} is already filed — refusing to duplicate")

    if not similar_ok:
        try:
            from scripts.ops.backlog_search import format_hits, search
            hits = [h for h in search(
                " ".join(str(row.get(k) or "") for k in ("id", "title", "detail")),
                paths=[str(path)], limit=5)
                # provenance: score — |query tokens ∩ row tokens| /
                # |query tokens|; LEXICAL overlap, never a semantic verdict
                if (h.get("score") or 0) >= SIMILAR_REFUSE_SCORE]
        except Exception:  # noqa: BLE001  # allow-silent: the pre-check must never block a legitimate file
            hits = []
        if hits:
            raise SimilarRowExists(
                f"{len(hits)} existing row(s) in {path.name} read like this one.\n"
                + format_hits(hits)
                + "\n\nIf it is a DUPLICATE, drop yours and update the existing "
                  "row instead. If it is a RECURRENCE, say so IN the new row — "
                  "that the earlier fix did not hold is the finding — and "
                  "re-file with similar_ok=True."
            )
    items.append(row)
    if isinstance(doc, dict) and updated_at:
        doc["updated_at"] = updated_at

    out = json.dumps(doc, **kw) + trailing

    # Belt and braces, SEMANTIC not textual. A byte-prefix check is wrong here:
    # `updated_at` legitimately changes and sits BEFORE `items`, so the prefix
    # moves on a correct append. (My own planted control caught that — which is
    # the argument for the control.) What must hold is that this is an ADDITION:
    # re-parse the output, and require every pre-existing item to be byte-equal
    # under the same serialisation.
    check = json.loads(out)
    check_items = check["items"] if isinstance(check, dict) else check
    if len(check_items) != len(items):
        raise FormatNotReproducible("round-trip lost or gained rows — refusing to write")
    for before_row, after_row in zip(original_items, check_items[:-1]):
        if json.dumps(before_row, **kw) != json.dumps(after_row, **kw):
            raise FormatNotReproducible(
                "an existing row changed under serialisation — refusing to write, "
                "because a diff-scoped guard would re-attribute it to this change"
            )
    path.write_text(out)
    return len(items)


class RowNotFound(Exception):
    """No row in this backlog carries that id — refuse rather than create one.

    An `update_row` that silently APPENDED on a miss would turn a typo'd id
    into a new, half-populated row, which is strictly worse than an error: the
    caller believes it amended the row it named.
    """


def update_row(path: pathlib.Path, row_id: str,
               *, fields: Optional[Dict[str, Any]] = None,
               append: Optional[Dict[str, str]] = None,
               updated_at: Optional[str] = None) -> Dict[str, Any]:
    """Amend an EXISTING row in place, preserving the file's exact bytes.

    Returns the amended row. `fields` REPLACES named keys (creating one is
    allowed — see below); `append` adds text to the END of an existing string
    field. At least one of them is required.

    WHY THIS EXISTS — THE TWO GUARDS PUSHED IN OPPOSITE DIRECTIONS
    --------------------------------------------------------------
    `BL-20260905-BACKLOG-APPEND-HAS-NO-EDIT-PATH-SO-AMENDING-A-ROW-REQUIRES-THE-FORBIDDEN-HAND-EDIT`.
    `CLAUDE.md` says to file through `append_row`, NEVER by hand. But
    `check_backlog_criteria` REFUSES a row missing `resolution_criteria` or
    `tier`, so rows *will* need amending — and this module only appended. The
    cheapest route to a required edit was therefore the forbidden one, which is
    this repo's own definition of a guard cheaper to lie to than to satisfy.

    CREATING A KEY VIA `fields` IS DELIBERATELY ALLOWED, because adding the two
    fields that guard demands is the motivating case. `append` is the opposite:
    it REFUSES a missing or non-string field, since appending to a field that
    does not exist means the caller is wrong about the row's shape.

    ⚠️ AND THAT REFUSAL IS LOAD-BEARING, BECAUSE THE BACKLOGS CARRY TWO ROW
    SHAPES. Measured on the health backlog 2026-09-12: **614 of 1496 rows have
    no `detail` field at all** (377 resolved / 118 kept_open / 106 open / 8
    wont_fix / 4 superseded / 1 invalid) and narrate in `summary` + `evidence`
    instead. So the `--append-field detail` default is wrong for ~41% of rows,
    and a helper that CREATED the missing field on append would quietly give
    those rows a second, competing narrative field that no reader knows to look
    at. The refusal is how a caller finds out which shape it is holding — which
    is what happened the first time this tool was used in anger.

    ⚠️ IT HAS HAPPENED THREE TIMES, WHICH IS WHY THE REMEDY IS CODE RATHER THAN
    A REMINDER. `BL-20260901-BACKLOG-ROUND-TRIP-BROKEN-AGAIN-BY-A-HAND-WRITTEN-SPLICE`
    recorded the second occurrence ONE DAY after the first was repaired; a third
    landed 2026-09-12, from the most natural idiom there is —
    ``json.dumps(text)[1:-1]``, whose default is ``ensure_ascii=True``, spliced
    into a file stored ``ensure_ascii=False``. The file still parses and still
    reads correctly through ``json.load``; what breaks is the BYTE round-trip,
    so ``append_row`` then refuses EVERY write to that backlog, repo-wide, for
    every session. The blast radius is the argument: one session's splice
    disarms the mandated writer for everyone.

    WHAT IS VERIFIED BEFORE THE WRITE (the row's own criterion asks for it):
      * the file reproduces byte-for-byte under a known serialisation, or this
        REFUSES — inherited from :func:`detect_format`, and the reason a
        hand-splice is unsafe in the first place;
      * every OTHER row is byte-identical under that same serialisation, so a
        diff-scoped guard cannot re-attribute someone else's row to this change;
      * every field of the target row that was NOT named is byte-identical;
      * an appended field's new value STARTS WITH its old value — an append that
        rewrote history would otherwise pass every other check here.
    """
    if not fields and not append:
        raise ValueError("update_row needs `fields` and/or `append` — refusing a no-op write")

    raw = path.read_text()
    doc = json.loads(raw)
    kw, trailing = detect_format(raw, doc)

    items = doc["items"] if isinstance(doc, dict) else doc
    matches = [i for i in items if isinstance(i, dict) and i.get("id") == row_id]
    if not matches:
        raise RowNotFound(
            f"{row_id} is not filed in {path.name} — refusing to create it. "
            "If this is a new finding, file it with append_row; if the id is a "
            "typo, fix the id (citing a row that does not exist reads as tracked "
            "while being tracked by nobody)."
        )
    if len(matches) > 1:
        raise ValueError(
            f"{row_id} appears {len(matches)} times in {path.name} — refusing to "
            "guess which one to amend"
        )
    target = matches[0]
    before_rows = [json.dumps(i, **kw) for i in items]
    before_target = dict(target)

    for key, value in (append or {}).items():
        current = target.get(key)
        if not isinstance(current, str):
            raise ValueError(
                f"cannot append to {key!r} on {row_id}: it is "
                f"{type(current).__name__}, not a string. Use `fields` to SET it."
            )
        target[key] = current + value
    for key, value in (fields or {}).items():
        target[key] = value

    if isinstance(doc, dict) and updated_at:
        doc["updated_at"] = updated_at

    out = json.dumps(doc, **kw) + trailing

    check = json.loads(out)
    check_items = check["items"] if isinstance(check, dict) else check
    if len(check_items) != len(items):
        raise FormatNotReproducible("round-trip lost or gained rows — refusing to write")
    touched = 0
    for before_ser, after_row in zip(before_rows, check_items):
        after_ser = json.dumps(after_row, **kw)
        if before_ser != after_ser:
            touched += 1
            if after_row.get("id") != row_id:
                raise FormatNotReproducible(
                    f"row {after_row.get('id')!r} changed and is not the target — "
                    "refusing to write, because a diff-scoped guard would "
                    "re-attribute it to this change"
                )
    if touched != 1:
        raise FormatNotReproducible(
            f"expected exactly 1 changed row, {touched} changed — refusing to write"
        )
    amended = [i for i in check_items if i.get("id") == row_id][0]
    named = set(fields or {}) | set(append or {})
    for key, value in before_target.items():
        if key in named:
            continue
        if json.dumps(amended.get(key), **kw) != json.dumps(value, **kw):
            raise FormatNotReproducible(
                f"field {key!r} on {row_id} changed but was not named — refusing to write"
            )
    for key in (append or {}):
        if not str(amended.get(key, "")).startswith(str(before_target.get(key, ""))):
            raise FormatNotReproducible(
                f"append to {key!r} did not PRESERVE the existing text — refusing "
                "to write; an append that rewrites history passes every other "
                "check here, which is why this one exists"
            )
    path.write_text(out)
    return amended


def _self_test() -> int:
    """Planted controls — including the exact failure this helper exists for."""
    import tempfile

    checks = []

    def ck(name, ok):
        checks.append(bool(ok))
        print(f"  {'ok ' if ok else 'FAIL'} {name}")

    doc = {"schema_version": 1, "updated_at": "2026-01-01",
           "items": [{"id": "BL-1", "title": "em—dash and ünicode"}]}
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "b.json"

        # (1) A file written ensure_ascii=False round-trips and appends cleanly.
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        n = append_row(p, {"id": "BL-2", "title": "new"}, updated_at="2026-01-02")
        after = p.read_text()
        ck("appends a row", n == 2)
        ck("non-ASCII survives unescaped", "em—dash" in after and "\\u2014" not in after)
        reparsed = json.loads(after)
        ck("addition-only (every pre-existing row byte-identical)",
           json.dumps(reparsed["items"][0], indent=2, ensure_ascii=False)
           == json.dumps(doc["items"][0], indent=2, ensure_ascii=False))
        ck("updated_at is allowed to move", reparsed["updated_at"] == "2026-01-02")

        # (2) THE CONTROL: a file this helper cannot reproduce must be REFUSED,
        #     not reformatted. Separators no candidate uses.
        p2 = pathlib.Path(td) / "odd.json"
        p2.write_text(json.dumps(doc, indent=3, separators=(" ,", " : ")))
        raw2 = p2.read_text()
        try:
            append_row(p2, {"id": "BL-3"})
            ck("refuses an unreproducible format", False)
        except FormatNotReproducible:
            ck("refuses an unreproducible format", True)
        ck("refused file is untouched", p2.read_text() == raw2)

        # (3) Duplicate ids are refused.
        try:
            append_row(p, {"id": "BL-2"})
            ck("refuses a duplicate id", False)
        except ValueError:
            ck("refuses a duplicate id", True)

        # (4) update_row — the EDIT path
        #     Its row is BL-20260905-BACKLOG-APPEND-HAS-NO-EDIT-PATH-SO-AMENDING-A-ROW-REQUIRES-THE-FORBIDDEN-HAND-EDIT
        #     -- ON ONE LINE, over the margin, deliberately. Eliding it to
        #     `...` AND wrapping it both produce a token that resolves to
        #     NOTHING, which reads as tracked while being tracked by nobody;
        #     I did each of those once writing this file and the guard caught
        #     both. See BL-20260909-A-MANAGER-TRUNCATED-A-BACKLOG-ID-FOUR-TIMES-IN-ONE-SESSION-AND-ONLY-A-GUARD-EVER-CAUGHT-IT
        #     Every control
        #     here is about the file's BYTES, because the failure being
        #     prevented still parses and still reads correctly.
        p4 = pathlib.Path(td) / "edit.json"
        base = {"schema_version": 1, "updated_at": "2026-01-01", "items": [
            {"id": "BL-A", "title": "first — em dash", "detail": "alpha"},
            {"id": "BL-B", "title": "second ⚠️ warn", "detail": "beta"},
        ]}
        canon = json.dumps(base, indent=2, ensure_ascii=False) + "\n"
        p4.write_text(canon)
        row = update_row(p4, "BL-B", append={"detail": " + appended — with an em dash"})
        after4 = p4.read_text()
        ck("update_row appends to a string field", row["detail"].startswith("beta"))
        ck("the appended non-ASCII is stored LITERALLY, not \\uXXXX "
           "(the exact 3rd-occurrence defect)",
           "appended — with" in after4 and "\\u2014" not in after4)
        ck("the file still round-trips byte-for-byte after an update",
           after4 == json.dumps(json.loads(after4), indent=2, ensure_ascii=False) + "\n")
        ck("an untouched row is byte-identical",
           json.dumps(json.loads(after4)["items"][0], indent=2, ensure_ascii=False)
           == json.dumps(base["items"][0], indent=2, ensure_ascii=False))
        ck("the DIFF IS LINE-LOCAL — exactly one line differs",
           sum(1 for a, b in zip(canon.splitlines(), after4.splitlines()) if a != b) == 1
           and len(canon.splitlines()) == len(after4.splitlines()))

        #     `fields` may CREATE a key — the motivating case is adding the
        #     `tier`/`resolution_criteria` that check_backlog_criteria demands.
        row = update_row(p4, "BL-A", fields={"tier": "1", "detail": "replaced"})
        ck("fields may create a key that did not exist", row.get("tier") == "1")
        ck("fields REPLACES rather than appends", row["detail"] == "replaced")

        #     Refusals.
        try:
            update_row(p4, "BL-NOPE", fields={"tier": "1"})
            ck("refuses an id that is not filed", False)
        except RowNotFound:
            ck("refuses an id that is not filed", True)
        update_row(p4, "BL-A", fields={"refs": []})
        try:
            update_row(p4, "BL-A", append={"refs": "x"})
            ck("refuses to append to a non-string field", False)
        except ValueError:
            ck("refuses to append to a non-string field", True)
        try:
            update_row(p4, "BL-A", append={"no_such_field": "x"})
            ck("refuses to append to a field that does not exist", False)
        except ValueError:
            ck("refuses to append to a field that does not exist", True)
        try:
            update_row(p4, "BL-A")
            ck("refuses a no-op write", False)
        except ValueError:
            ck("refuses a no-op write", True)

        #     THE MUTATION CONTROL the backlog row asks for BY NAME: corrupt the
        #     serialisation and confirm the edit path goes red, so the passes
        #     above cannot be passing for an unrelated reason.
        #
        #     ⚠️ THE CORRUPTION MUST BE **MIXED**, AND FINDING THAT OUT IS ITSELF
        #     THE CONTROL WORKING. A wholly ensure_ascii=True file is a KNOWN
        #     serialisation (`_CANDIDATES`), so it reproduces and is legitimately
        #     editable — the first version of this control wrote one and failed,
        #     correctly. What no candidate can reproduce is one row escaped and
        #     another literal, which is exactly what a hand-splice leaves behind
        #     and exactly the third occurrence this edit path exists to end.
        p5 = pathlib.Path(td) / "corrupt.json"
        p5.write_text(canon.replace("first — em dash", "first \\u2014 em dash", 1))
        ck("a MIXED file is NOT byte-reproducible (control is armed)",
           "\\u2014" in p5.read_text() and "⚠️" in p5.read_text())
        raw5 = p5.read_text()
        try:
            update_row(p5, "BL-A", fields={"tier": "1"})
            ck("MUTATION: refuses to edit a file it cannot reproduce", False)
        except FormatNotReproducible:
            ck("MUTATION: refuses to edit a file it cannot reproduce", True)
        ck("MUTATION: the refused file is untouched", p5.read_text() == raw5)

    ok = sum(checks)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


#: The live review backlogs, in the order the session-end routine names them.
#: Kept here rather than re-derived, because the reader this protects
#: (``tests/test_backlog_append.py``) builds its paths by interpolating a loop
#: variable, which no static scan can resolve — the blind spot that let a broken
#: file reach ``main`` on 2026-09-01.
#:
#: ⚠️ THIS TUPLE WAS THE THREE HEALTH/PERFORMANCE/ML BACKLOGS UNTIL 2026-09-02,
#: AND ``docs/claude/research-review-backlog.json`` WAS THE UNGUARDED FOURTH.
#: The research backlog was split out of the performance one on 2026-08-30 and
#: nothing added it here — so it inherited the exact exposure this guard exists
#: to close, and inherited it silently. It is not covered by ``pytest-run``
#: either: ``tests/test_pytest_run_filter.py::DELIBERATELY_EXCLUDED`` excludes
#: the backlogs as a class, so a serialisation break in it reached ``main``
#: green by the same route measured on 2026-09-01.
#:
#: The lesson is the membership rule, not the one file: **splitting a backlog is
#: not complete until the new file is named here.** A backlog absent from this
#: tuple is not "not yet guarded" — it is unguarded in a way no CI signal
#: reports.
LIVE_BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/research-review-backlog.json",
)


def check_live_backlogs(root: Optional[pathlib.Path] = None) -> int:
    """Refuse if any live backlog no longer round-trips. Returns a exit code.

    WHY THIS IS A GUARD AND NOT A TEST. ``pytest-run`` short-circuits on a diff
    that touches no code, and the three backlogs are DELIBERATELY excluded from
    its relevance filter (``tests/test_pytest_run_filter.py::DELIBERATELY_EXCLUDED``)
    because they change on nearly every PR and widening there costs CI minutes on
    all of them. The consequence, measured 2026-09-01: a hand-edited row merged on
    a backlog-only PR, ``detect_format`` could no longer reproduce the file, and
    ``append_row`` refused EVERY write repo-wide — while the test that catches
    exactly this could not run on the PR that introduced it. The signal arrived
    hours later, on three unrelated PRs that happened to touch code.

    The `guards` job never short-circuits, so this runs on the backlog-only PR
    itself. That is the whole point: **a check that cannot run on the change it
    guards is not a guard.**

    ⚠️ A MISSING FILE IS NOT A PASS. It is reported and returns non-zero, because
    "we could not look" and "we looked and it was fine" are different states.
    """
    root = root or pathlib.Path(".")
    bad: list[str] = []
    for rel in LIVE_BACKLOGS:
        path = root / rel
        if not path.exists():
            bad.append(f"{rel}: MISSING — cannot be checked (not the same as clean)")
            continue
        raw = path.read_text()
        try:
            detect_format(raw, json.loads(raw))
        except FormatNotReproducible:
            bad.append(
                f"{rel}: no candidate serialisation reproduces it byte-for-byte, so "
                "append_row will REFUSE EVERY WRITE to it repo-wide. Almost always a "
                "row spliced in by hand with ensure_ascii=True; re-serialise that row "
                "canonically (indent=2, ensure_ascii=False) rather than reformatting "
                "the file, which would re-attribute every pre-existing row to your diff."
            )
        except json.JSONDecodeError as exc:
            bad.append(f"{rel}: not valid JSON — {exc}")
    if bad:
        print("::error::a live review backlog does not round-trip:")
        for line in bad:
            print(f"  {line}")
        return 1
    print(f"backlog round-trip: OK — {len(LIVE_BACKLOGS)} live backlog(s) reproduce byte-for-byte")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backlog", help="path to a *-backlog.json")
    ap.add_argument("--row-json", help="path to a JSON file holding the row")
    ap.add_argument("--updated-at")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--check-live", action="store_true",
                    help="round-trip every live backlog; refuse if any cannot be reproduced")
    # The EDIT path. Text comes from a FILE, never from argv: an observation
    # worth recording is a paragraph, and shell quoting is where the em-dashes
    # and newlines this helper exists to protect get mangled on the way in.
    ap.add_argument("--update", metavar="ROW_ID",
                    help="amend an EXISTING row instead of appending a new one")
    ap.add_argument("--append-field", default="detail",
                    help="with --update: the string field --text is appended to (default: detail)")
    ap.add_argument("--text", metavar="PATH",
                    help="with --update: file whose contents are APPENDED to --append-field")
    ap.add_argument("--set", metavar="PATH", dest="set_json",
                    help="with --update: JSON object of fields to REPLACE (may create a key)")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.check_live:
        return check_live_backlogs()
    if a.update:
        if not a.backlog:
            ap.error("--backlog is required with --update")
        if not (a.text or a.set_json):
            ap.error("--update needs --text and/or --set")
        append = {a.append_field: pathlib.Path(a.text).read_text()} if a.text else None
        fields = json.loads(pathlib.Path(a.set_json).read_text()) if a.set_json else None
        row = update_row(pathlib.Path(a.backlog), a.update,
                         fields=fields, append=append, updated_at=a.updated_at)
        print(f"updated {row.get('id')} — field(s) "
              f"{sorted(set(append or {}) | set(fields or {}))}; the file still "
              f"round-trips and no other row changed")
        return 0
    if not (a.backlog and a.row_json):
        ap.error("--backlog and --row-json are required (or --self-test)")
    row = json.loads(pathlib.Path(a.row_json).read_text())
    n = append_row(pathlib.Path(a.backlog), row, updated_at=a.updated_at)
    print(f"appended {row.get('id')} — {n} item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
