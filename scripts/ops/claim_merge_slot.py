#!/usr/bin/env python3
"""Write the R13 merge-slot claim into `docs/claude/session-board.json` WITHOUT
reformatting the rest of the file.

WHY THIS EXISTS
---------------
`check_pr_landing.py` R13 fails any branch that ARMS the landing route while it
does not hold `merge_slot` in `docs/claude/session-board.json`, claimed in its
own diff. `.github/actions/commit-to-main/action.yml` arms that route for every
automation producer and never wrote the claim, so every automation PR it opened
failed its own required guard and could never merge. Measured 2026-09-09: 47
open PRs, 46 of them automation, the guards job reading `PASS 53 · FAIL 1` with
the 1 being `pr-landing-guard` on R13.

⚠️ WHY A SPLICE AND NOT `json.dump`
-----------------------------------
`session-board.json` is hand-maintained and its serialisation matches NO
`json.dumps` indent — measured: `indent=1`, `2`, `3`, `4` and `\\t` all differ
from the file on disk (the root is indented 1 space, nested members 2). A
whole-file re-serialisation therefore rewrites all ~139 lines to change 5. That
is the exact failure `scripts/ops/backlog_append.py` was written for ("a naive
read-append-write reformats every non-ASCII line and re-attributes ~21k lines to
your PR"), and here it is worse than cosmetic: this file is written by every
session, so a whole-file rewrite turns every concurrent edit into a guaranteed
merge conflict instead of a 5-line one.

So the claim is spliced over the EXACT source span of the `merge_slot` value and
every other byte of the file is preserved. The result is verified before it is
written: it must re-parse, `merge_slot.branch` must be what was asked for, and
every OTHER top-level key must be deep-equal to what was there. A splice that
cannot prove those refuses rather than writing.

⚠️ WHAT THIS DOES NOT DO
------------------------
It does not serialize anything. R13's own text is explicit that a committed
claim reaches no other session until the branch merges
(`BL-20260810-MERGE-SLOT-MIRROR-UNWRITABLE-PRE-MERGE`), so two automation runs
in flight at once will each write a valid claim and never see one another. Their
claims then CONFLICT textually at merge time — verified, not assumed:
`CONFLICT (content): Merge conflict in docs/claude/session-board.json`. Handling
that is the caller's job; `commit-to-main` resolves it in its stale-branch
refresh by re-running this script over `main`'s version of the file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ONE OWNER for "is the incumbent claim a ghost?" — imported rather than
# re-derived, so the claimant's report and any other reader cannot drift.
import merge_slot_state as mss  # noqa: E402


class SpliceError(RuntimeError):
    """The claim could not be written safely, so nothing was written."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_top_level_value_span(text: str, key: str) -> tuple[int, int]:
    """Return (start, end) of the VALUE of top-level ``key`` in ``text``.

    Walks the source tracking string/escape state and brace depth, so a match
    inside a string (this file's `_doc` discusses `merge_slot` in prose) or at a
    deeper level (`schema.merge_slot` is a sibling DESCRIPTION of the same name)
    is not mistaken for the real key. Both of those are present in the real file,
    which is why the naive `text.find('"merge_slot"')` is wrong here.
    """
    depth = 0
    i = 0
    n = len(text)
    target = json.dumps(key)  # the key exactly as it appears in source
    while i < n:
        ch = text[i]
        if ch == '"':
            # Consume the whole string token, honouring escapes.
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            if j >= n:
                raise SpliceError("unterminated string while scanning the board")
            token = text[i:j + 1]
            k = j + 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            if depth == 1 and token == target and k < n and text[k] == ":":
                v = k + 1
                while v < n and text[v] in " \t\r\n":
                    v += 1
                try:
                    _, end = json.JSONDecoder().raw_decode(text, v)
                except ValueError as exc:  # pragma: no cover - malformed source
                    raise SpliceError(f"`{key}` value does not parse: {exc}") from exc
                return (v, end)
            i = j + 1
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        i += 1
    raise SpliceError(
        f"no top-level `{key}` key found. Refusing to write: appending one would "
        f"reformat or restructure a file this script exists to leave alone.")


def _line_indent(text: str, pos: int) -> int:
    line_start = text.rfind("\n", 0, pos) + 1
    return len(text[line_start:pos]) - len(text[line_start:pos].lstrip(" "))


def infer_indents(value_src: str, key_indent: int) -> tuple[int, int]:
    """Return the (child, closing-brace) indents OF THE BLOCK BEING REPLACED.

    ⚠️ ABSOLUTE indents, not a step added to `key_indent`. Measured on the real
    `session-board.json`: the root members sit at 1 space but the `merge_slot`
    key sits at 2 with its own members ALSO at 2 — the file has been hand-edited
    and is internally inconsistent. A step-based render "tidies" that to 4 and
    rewrites whitespace nobody asked it to touch, which is churn on the one file
    every session edits. Reproducing what is there keeps the diff to the claim.
    """
    lines = value_src.split("\n")
    child = None
    for line in lines[1:]:
        if line.strip():
            child = len(line) - len(line.lstrip(" "))
            break
    close = None
    for line in reversed(lines):
        if line.strip():
            close = len(line) - len(line.lstrip(" "))
            break
    if child is None:          # an inline or empty block tells us nothing
        return (key_indent + 2, key_indent)
    return (child, key_indent if close is None else close)


def render_value(claim: dict, child_indent: int, close_indent: int) -> str:
    members = ",\n".join(
        f"{' ' * child_indent}{json.dumps(k, ensure_ascii=False)}: "
        f"{json.dumps(v, ensure_ascii=False)}"
        for k, v in claim.items())
    return "{\n" + members + "\n" + " " * close_indent + "}"


def splice(text: str, claim: dict) -> str:
    start, end = find_top_level_value_span(text, "merge_slot")
    key_pos = text.rfind('"merge_slot"', 0, start)
    key_indent = _line_indent(text, key_pos)
    child_indent, close_indent = infer_indents(text[start:end], key_indent)
    new_text = (text[:start]
                + render_value(claim, child_indent, close_indent)
                + text[end:])

    # VERIFY BEFORE WRITING — the splice must change the slot and nothing else.
    try:
        before = json.loads(text)
        after = json.loads(new_text)
    except json.JSONDecodeError as exc:
        raise SpliceError(f"the spliced board does not parse: {exc}") from exc
    if after.get("merge_slot") != claim:
        raise SpliceError("the spliced `merge_slot` did not read back as written")
    if {k: v for k, v in before.items() if k != "merge_slot"} != \
       {k: v for k, v in after.items() if k != "merge_slot"}:
        raise SpliceError("the splice changed a key other than `merge_slot`")
    return new_text


def build_claim(branch: str, held_by: str, purpose: str,
                claimed_at: Optional[str] = None) -> dict[str, Any]:
    # R13 requires `branch`, and non-empty `held_by` + `claimed_at`
    # ("a claim nobody can attribute or time out is not a claim").
    if not branch.strip():
        raise SpliceError("--branch is empty; R13 grades `merge_slot.branch`")
    if not held_by.strip():
        raise SpliceError("--held-by is empty; R13 refuses an unattributable claim")
    return {
        "held_by": held_by.strip(),
        "branch": branch.strip(),
        "claimed_at": claimed_at or _now_iso(),
        "purpose": purpose.strip(),
    }


def branch_slot_rel(branch: str, base_dir: str = ".github/merge-slots") -> str:
    """One file per branch, named for the branch.

    ⚠️ THE PATH IS NOT THE CLAIM. A file copied from another branch would sit at
    the wrong path but could equally be renamed, so the branch is ALSO written
    inside and `check_pr_landing.py` requires the two to agree. The filename is
    what keeps two claims off the same lines; the field is what makes the claim
    attributable.
    """
    import re as _re
    slug = _re.sub(r"[^A-Za-z0-9._-]", "-", branch.removeprefix("claude/"))
    return f"{base_dir}/{slug}.json"


def _write_branch_claim(a) -> int:
    """The conflict-free R13 route.

    Written whole rather than spliced: there is no surrounding document to
    preserve, which is the entire point — `splice()` exists only because the
    shared board is a file other people also edit.
    """
    rel = Path(branch_slot_rel(a.branch, a.branch_claim_dir))
    claim = build_claim(a.branch, a.held_by, a.purpose, a.claimed_at)
    try:
        rel.parent.mkdir(parents=True, exist_ok=True)
        rel.write_text(json.dumps(claim, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    except OSError as exc:
        print(f"claim-merge-slot: cannot write {rel}: {exc}", file=sys.stderr)
        return 2
    print(f"claim-merge-slot: {rel} -> {a.branch} (per-branch route; the shared "
          f"merge_slot field is deliberately NOT touched, so this cannot conflict "
          f"with another branch's claim)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", default="docs/claude/session-board.json")
    ap.add_argument("--branch")
    ap.add_argument("--held-by")
    ap.add_argument("--purpose", default="")
    ap.add_argument("--claimed-at", default=None,
                    help="override the timestamp (tests only)")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-incumbent-check", action="store_true",
                    help="Skip grading the claim being overwritten. It costs one "
                         "`git ls-remote`; this exists for offline tests, not as "
                         "a way to stop looking.")
    ap.add_argument("--incumbent-pr-json", default=None,
                    help="Optional pull-request payload for the INCUMBENT "
                         "claim's branch. Authoritative when given; without it a "
                         "still-present branch grades `undecidable`, never "
                         "`live`.")
    ap.add_argument("--branch-claim", action="store_true",
                    help="write the CONFLICT-FREE per-branch claim at "
                         ".github/merge-slots/<slug>.json instead of splicing the "
                         "one shared merge_slot field. R13 accepts either. Prefer "
                         "this on a lane branch: the shared field is a single line "
                         "every armed branch must overwrite, and `main` moved it 39 "
                         "times in the last 40 commits that touched it, so an armed "
                         "branch conflicts faster than its own CI can finish.")
    ap.add_argument("--branch-claim-dir", default=".github/merge-slots",
                    help="tests only")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()
    if not a.branch or not a.held_by:
        ap.error("--branch and --held-by are required (or pass --self-test)")

    if a.branch_claim:
        # ⚠️ THIS RETURNS BEFORE THE INCUMBENT REPORT BELOW, AND THAT IS THE
        # POINT RATHER THAN AN OVERSIGHT. The report answers "what am I
        # DISPLACING?" — and the per-branch route displaces nothing: it writes a
        # file named for this branch and leaves the shared `merge_slot` field
        # untouched. Grading the incumbent here would print a verdict about a
        # claim this invocation is not overwriting, which is a statement with no
        # bearing on the act being performed. The ghost question belongs to the
        # shared-field route, where a real displacement happens.
        return _write_branch_claim(a)

    path = Path(a.board)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"claim-merge-slot: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    # ── WHAT AM I DISPLACING? ────────────────────────────────────────────────
    # The release half of this protocol has NO mechanism and never has: measured
    # over the last 25 commits touching this file, ZERO wrote a cleared slot. So
    # the incumbent claim is almost always a ghost, and every claimant has had to
    # make that call alone — waiting forever on a merged PR, or displacing blind.
    # Both happened live on 2026-09-09.
    #
    # ⚠️ IT IS PRINTED BEFORE THE WRITE AND NEVER BLOCKS IT. R13's own docstring
    # says a committed claim reaches no other session until the branch merges, so
    # this field serializes nothing and refusing to overwrite it would invent a
    # lock the protocol does not have — and would deadlock every armed branch
    # behind whichever ghost happened to be last. The answer is information at
    # the moment the judgement is made, which is the cost the backlog rows name.
    if not a.no_incumbent_check:
        try:
            incumbent, readable = mss.read_claim(path)
            if readable:
                pr = None
                if a.incumbent_pr_json:
                    try:
                        raw = json.loads(
                            Path(a.incumbent_pr_json).read_text(encoding="utf-8"))
                        pr = raw[0] if isinstance(raw, list) and raw else raw
                    except (OSError, json.JSONDecodeError, TypeError):
                        pr = None
                branch = str((incumbent or {}).get("branch") or "")
                print(mss.render(mss.grade(
                    incumbent, mss.branch_on_origin(branch, path.parent.parent.parent)
                    if branch else None, pr)))
        except Exception as exc:                      # noqa: BLE001
            # ⚠️ A REPORT MUST NEVER TAKE DOWN THE WRITE IT ANNOTATES. R13 fails
            # an armed branch that does not hold the slot, so a crash here would
            # red a PR over a courtesy read.
            print(f"claim-merge-slot: could not grade the incumbent claim "
                  f"({exc}) — WE DID NOT LOOK. Proceeding with the claim.")

    try:
        new_text = splice(text, build_claim(a.branch, a.held_by, a.purpose,
                                            a.claimed_at))
    except SpliceError as exc:
        print(f"claim-merge-slot: REFUSED, nothing written — {exc}", file=sys.stderr)
        return 3
    path.write_text(new_text, encoding="utf-8")
    print(f"claim-merge-slot: {path} merge_slot -> {a.branch}")
    return 0


def _changed_lines(before: str, after: str) -> int:
    """Real added+removed line count, the way git and a human see it.

    Deliberately NOT ``zip(before, after)``: that compares line *i* to line *i*,
    so a replacement with a different LINE COUNT shifts everything after it and
    reports the whole remainder as changed. See the note in ``_self_test``.
    """
    import difflib
    return sum(
        1 for line in difflib.unified_diff(
            before.split("\n"), after.split("\n"), n=0, lineterm="")
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    )


def _self_test() -> int:
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): "
              f"{'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    # A board shaped like the real one: 1-space root indent, 2-space members,
    # the key name also appearing inside a prose string AND as a nested key.
    board = (
        '{\n'
        ' "_doc": "the `merge_slot` field below is a mirror, not the claim",\n'
        ' "schema": {\n'
        '  "merge_slot": "{ held_by, branch, claimed_at }"\n'
        ' },\n'
        ' "merge_slot": {\n'
        '  "held_by": "session_OLD",\n'
        '  "branch": "claude/somebody-else",\n'
        '  "claimed_at": "2026-09-09T05:21:20Z"\n'
        ' },\n'
        ' "active_sessions": [],\n'
        ' "updated_at": "2026-09-09T05:21:20Z"\n'
        '}\n'
    )
    claim = build_claim("automation/work-digest-1-1", "run 1", "p",
                        claimed_at="2026-09-09T06:00:00Z")
    out = splice(board, claim)

    check("the claim lands on merge_slot.branch",
          json.loads(out)["merge_slot"]["branch"], "automation/work-digest-1-1")
    check("the nested schema.merge_slot DESCRIPTION is untouched",
          json.loads(out)["schema"]["merge_slot"],
          "{ held_by, branch, claimed_at }")
    check("the prose mention of merge_slot is untouched",
          json.loads(out)["_doc"].startswith("the `merge_slot` field"), True)
    check("no other top-level key moved",
          {k: v for k, v in json.loads(out).items() if k != "merge_slot"},
          {k: v for k, v in json.loads(board).items() if k != "merge_slot"})

    # THE POINT OF THE SPLICE: everything OUTSIDE the replaced span is
    # byte-identical, so the diff is the claim and not the file. Asserted
    # against the span itself rather than against line numbers — the claim has
    # one more member than the block it replaces, so a line-index test would
    # measure the shift and not the property.
    _s, _e = find_top_level_value_span(board, "merge_slot")
    check("every byte BEFORE the slot is preserved", out.startswith(board[:_s]), True)
    check("every byte AFTER the slot is preserved", out.endswith(board[_e:]), True)
    check("the file's 1-space root indent is preserved",
          ' "merge_slot": {' in out, True)
    check("the fixture's 2-space member indent is reproduced",
          '\n  "held_by": "run 1",' in out, True)
    check("the fixture's 1-space closing brace is reproduced",
          '\n }' in out.split('"claimed_at"')[1], True)

    # An empty attribution is refused, not written — R13 would fail it anyway,
    # and a claim written but unattributable is the worse of the two states.
    for label, kwargs in (("empty branch", {"branch": "", "held_by": "x"}),
                          ("empty held_by", {"branch": "b", "held_by": " "})):
        try:
            build_claim(purpose="", **kwargs)
            check(f"{label} refused", "wrote", "refused")
        except SpliceError:
            check(f"{label} refused", "refused", "refused")

    # A board with no top-level merge_slot REFUSES rather than inventing one.
    try:
        splice('{\n "active_sessions": []\n}\n', claim)
        check("missing merge_slot refused", "wrote", "refused")
    except SpliceError:
        check("missing merge_slot refused", "refused", "refused")

    # And it must work on the REAL file, not just the fixture.
    real = Path(__file__).resolve().parents[2] / "docs/claude/session-board.json"
    if real.exists():
        src = real.read_text(encoding="utf-8")
        spliced = splice(src, claim)
        check("real board: claim reads back",
              json.loads(spliced)["merge_slot"]["branch"],
              "automation/work-digest-1-1")
        # ⚠️ MEASURE A REAL DIFF, NOT A POSITIONAL LINE COMPARISON. This was
        # `sum(... for x, y in zip(before, after) if x != y)`, which compares
        # line *i* of the old file against line *i* of the new one — so a claim
        # with a DIFFERENT NUMBER OF LINES than the one it replaces shifts every
        # following line and reads as if the whole file had been rewritten. That
        # is not hypothetical: on 2026-09-10 an outgoing 6-key claim (a manager
        # had hand-added `found_stale` and `concurrent_sibling`) was replaced by
        # this script's own 4-key claim, the board went 94 -> 91 lines, and the
        # metric reported 87 changed against a budget of 5 — while the splice was
        # byte-for-byte correct on both sides of the slot. It failed on `main`,
        # so it failed `pytest-run` on every open PR.
        # A real diff keeps the teeth it was built for: a whole-file
        # re-serialisation still produces a diff far larger than a claim, which
        # `tests/test_claim_merge_slot.py` asserts with a planted control.
        # THE BOUND IS THE SLOT'S OWN SIZE, not a magic number: a claim replaces
        # the old slot with the new one, so the honest ceiling is "the lines of
        # both slots and nothing else". That scales when a session hand-adds keys
        # to the outgoing claim (which is what broke the old fixed budget) and
        # still fails loudly on a whole-file reformat, which touches lines
        # belonging to neither slot.
        _s, _e = find_top_level_value_span(src, "merge_slot")
        _s2, _e2 = find_top_level_value_span(spliced, "merge_slot")
        budget = src[_s:_e].count("\n") + spliced[_s2:_e2].count("\n") + 2
        d = _changed_lines(src, spliced)
        check("real board: the diff is confined to the slot (no whole-file reformat)",
              d <= budget, True)
    else:  # pragma: no cover
        print("  self-test (real board): SKIPPED — file absent")

    print(f"claim-merge-slot self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
