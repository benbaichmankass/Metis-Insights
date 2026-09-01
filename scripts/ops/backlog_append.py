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

    ok = sum(checks)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


#: The three live review backlogs, in the order the session-end routine names
#: them. Kept here rather than re-derived, because the reader this protects
#: (``tests/test_backlog_append.py``) builds its paths by interpolating a loop
#: variable, which no static scan can resolve — the blind spot that let a broken
#: file reach ``main`` on 2026-09-01.
LIVE_BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
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
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.check_live:
        return check_live_backlogs()
    if not (a.backlog and a.row_json):
        ap.error("--backlog and --row-json are required (or --self-test)")
    row = json.loads(pathlib.Path(a.row_json).read_text())
    n = append_row(pathlib.Path(a.backlog), row, updated_at=a.updated_at)
    print(f"appended {row.get('id')} — {n} item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
