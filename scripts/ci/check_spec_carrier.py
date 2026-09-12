#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py as `spec-carrier-guard`
"""An artifact that SPECIFIES WORK must be pointed at by something, or it is invisible.

BL-20260906-A-RESEARCH-ARTIFACT-THAT-SPECIFIES-WORK-IS-NOT-REGISTERED-AS-WORK.
`docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md` was written 2026-08-23,
carried the operator's directive verbatim, stated its populations, and ended
*"Paste this whole file as the opening message of a NEW session."* **Nobody did,
for 14 days** — and the same thesis was then re-derived across at least SEVEN
further memos and built zero times. `docs/research/` and `docs/design/` are WRITE
surfaces with no READ path into planning, so a perfect ready-to-paste spec is
exactly as invisible as a bad one.

WHAT IT CHECKS, AND THE SCOPE IS THE LOAD-BEARING CHOICE
--------------------------------------------------------
Diff-scoped, on ADDED files only. **A whole-tree gate would red every PR from
day one** — measured 2026-09-12: 42 of 87 spec-shaped artifacts are already
un-carried — which is the guard-drowned-at-birth failure this repo has paid for.
The existing population is REPORTED by ``--census`` instead, because the row's
own criterion says a mechanism covering only new artifacts leaves the existing
population unaddressed, and a number nobody prints is a number nobody acts on.

⚠️ TWO CLASSIFIERS, AND THEIR ERRORS RUN IN OPPOSITE DIRECTIONS. Say so when
quoting either.
  * **Spec-shaped** is lexical: a filename matching `SESSION-PROMPT` /
    `-PROPOSAL-` / `-DESIGN` / `WORKPLAN` / `-PLAN-`, OR text that asks for a
    follow-up in its own words. The name half OVER-counts — a finished
    `-DESIGN` whose work shipped still matches — so the total is an UPPER bound
    on live debt. The text half is the sharp one: an artifact that says *"paste
    this as the opening message"* is asking, whatever its name.
  * **Carried** is substring presence of the path or the bare filename anywhere
    in the carrier corpus. That is PERMISSIVE — a passing mention counts — so it
    UNDER-counts un-carried specs. Requiring a typed reference would be better
    and would fail against every carrier format in the repo today.

So a `uncarried` verdict means *nothing anywhere so much as names this file*,
which is a low bar and exactly why crossing it is worth failing a PR over.

⚠️ FOUR STATES, NEVER COLLAPSED: `carried` · `uncarried` · `not_a_spec` ·
`undecidable` (the file could not be read). `undecidable` is REPORTED and does
not pass quietly — *we could not look* is not *it is fine*.

⚠️ THE OVERRIDE IS A CARRIER, NOT A MARKER. There is deliberately no
`# spec-carrier-ok:` comment: the whole finding is that a spec with no carrier
is invisible, and a marker that silences the guard without creating a carrier
would reproduce the defect with an extra step. To land a new spec, file a row or
a work object naming its path — which is the thing that was missing.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Dict, List, Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

SPEC_DIRS = ("docs/research", "docs/design")
NAME_RE = re.compile(r"(SESSION-PROMPT|-PROPOSAL-|-DESIGN|WORKPLAN|-PLAN-)", re.I)
#: The artifact asking, in its own words, for a follow-up that is not this file.
ASK_RE = re.compile(
    r"paste this|paste the whole|as the opening message|next session should"
    r"|a follow-up session|not yet built|is owed|must be built|to be built by",
    re.I,
)

#: Where a carrier may live. Globs, because the work store is one file per object.
CARRIER_GLOBS = (
    "docs/claude/work/objects/*.yaml",
    "docs/claude/work/intents/*.yaml",
    "docs/claude/work/steps/*.yaml",
)
CARRIER_FILES = (
    "docs/claude/OPEN-ITEMS.json",
    "docs/claude/work/MANAGER-CHECKLIST.json",
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/research-review-backlog.json",
)

CARRIED, UNCARRIED, NOT_A_SPEC, UNDECIDABLE = (
    "carried", "uncarried", "not_a_spec", "undecidable")


def carrier_corpus(root: pathlib.Path) -> str:
    parts: List[str] = []
    for g in CARRIER_GLOBS:
        for p in sorted(root.glob(g)):
            try:
                parts.append(p.read_text(errors="replace"))
            except OSError:
                continue
    for f in CARRIER_FILES:
        p = root / f
        if p.is_file():
            try:
                parts.append(p.read_text(errors="replace"))
            except OSError:
                continue
    return "\n".join(parts)


def classify(path: pathlib.Path, root: pathlib.Path, corpus: str) -> Dict[str, object]:
    rel = str(path.relative_to(root)) if path.is_absolute() else str(path)
    if not rel.endswith(".md") or not any(rel.startswith(d) for d in SPEC_DIRS):
        return {"path": rel, "state": NOT_A_SPEC, "why": "outside the spec directories"}
    try:
        text = (root / rel).read_text(errors="replace")
    except OSError as exc:
        # We could not look. Never NOT_A_SPEC — that would pass it silently.
        return {"path": rel, "state": UNDECIDABLE, "why": f"unreadable: {exc}"}
    by_name = bool(NAME_RE.search(pathlib.Path(rel).name))
    by_text = bool(ASK_RE.search(text))
    if not (by_name or by_text):
        return {"path": rel, "state": NOT_A_SPEC, "why": "neither a spec-shaped name nor a follow-up ask"}
    carried = rel in corpus or pathlib.Path(rel).name in corpus
    return {
        "path": rel,
        "state": CARRIED if carried else UNCARRIED,
        "by_name": by_name,
        # The SHARP half: the artifact asks for follow-up in its own words, so a
        # `-DESIGN` whose work is finished cannot inflate it.
        "by_text": by_text,
        "why": ("a carrier names it" if carried
                else "nothing in the work store, OPEN-ITEMS, the checklist or the "
                     "backlogs so much as names this path"),
    }


def added_paths(base: str, root: pathlib.Path) -> List[str] | None:
    """Files ADDED by this diff. `None` means we could not look."""
    try:
        out = subprocess.run(
            ["git", "diff", "--diff-filter=A", "--name-only", f"{base}...HEAD"],
            cwd=root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def census(root: pathlib.Path) -> Dict[str, object]:
    corpus = carrier_corpus(root)
    rows = []
    for d in SPEC_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            rows.append(classify(p, root, corpus))
    spec = [r for r in rows if r["state"] in (CARRIED, UNCARRIED)]
    unc = [r for r in spec if r["state"] == UNCARRIED]
    return {
        "scanned": len(rows),
        "spec_shaped": len(spec),
        "uncarried": len(unc),
        "uncarried_self_referential": [r for r in unc if r.get("by_text")],
        "uncarried_name_only": [r for r in unc if not r.get("by_text")],
        "undecidable": [r for r in rows if r["state"] == UNDECIDABLE],
        # ⚠️ The denominator, asserted rather than assumed: a corpus that failed
        # to load would make EVERY spec read uncarried, which is the vacuous
        # green's evil twin — a confident red over nothing.
        "corpus_chars": len(corpus),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", help="enforce over files ADDED vs this ref")
    ap.add_argument("--census", action="store_true", help="report the whole tree; never fails")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    if a.census:
        c = census(REPO_ROOT)
        if a.json:
            print(json.dumps({k: (v if not isinstance(v, list) else [r["path"] for r in v])
                              for k, v in c.items()}, indent=2))
            return 0
        print(f"spec-carrier census: {c['spec_shaped']} spec-shaped artifact(s) of "
              f"{c['scanned']} scanned under {', '.join(SPEC_DIRS)}; "
              f"{c['uncarried']} UN-CARRIED.")
        print(f"  self-referential (the text itself asks for follow-up): "
              f"{len(c['uncarried_self_referential'])}")
        for r in c["uncarried_self_referential"]:
            print(f"     {r['path']}")
        print(f"  name-only (-DESIGN/-PLAN, may be finished work — an UPPER bound): "
              f"{len(c['uncarried_name_only'])}")
        if c["undecidable"]:
            print(f"  ⚠️ undecidable (could not read): {len(c['undecidable'])}")
        note = ("ok" if c["corpus_chars"] > 1000 else
                "SUSPICIOUSLY SMALL — a corpus that failed to load makes EVERY "
                "spec read uncarried, which is a confident red over nothing")
        print(f"  carrier corpus: {c['corpus_chars']} chars ({note})")
        return 0

    if not a.base:
        print("::error::spec-carrier: no --base and no --census, so NOTHING was "
              "checked. This is not a pass.", file=sys.stderr)
        return 2
    added = added_paths(a.base, REPO_ROOT)
    if added is None:
        print(f"::error::spec-carrier: could not read the diff against {a.base} — "
              f"refusing to report a green over a population it failed to read.",
              file=sys.stderr)
        return 2
    corpus = carrier_corpus(REPO_ROOT)
    findings = []
    undecidable = []
    for rel in added:
        v = classify(pathlib.Path(rel), REPO_ROOT, corpus)
        if v["state"] == UNCARRIED:
            findings.append(v)
        elif v["state"] == UNDECIDABLE:
            undecidable.append(v)
    for v in undecidable:
        print(f"::error::spec-carrier: {v['path']} — {v['why']}. We could not look, "
              f"which is not the same as it being fine.")
    for v in findings:
        print(f"::error::spec-carrier: {v['path']} SPECIFIES WORK and NOTHING points at "
              f"it. {v['why']}. File a work object under docs/claude/work/objects/ or a "
              f"row in OPEN-ITEMS/a backlog naming this path. ⚠️ There is deliberately "
              f"no silencing marker: the defect IS a spec with no carrier, so a marker "
              f"would reproduce it with an extra step.")
    if findings or undecidable:
        return 1
    print(f"spec-carrier: OK — {len(added)} added file(s) checked, none is an "
          f"un-carried spec.")
    return 0


def _self_test() -> int:
    import tempfile
    fails = []

    def ok(label, cond):
        # ⚠️ (label, cond) and NOT (cond, label). The first version took them the
        # other way round while every call site passed the label first, so every
        # control "passed" on a non-empty string being truthy — a self-test that
        # could not fail, in a guard about things that are invisible. Caught by
        # reading the output and seeing eleven controls print `ok True`.
        if not cond:
            fails.append(label)
        print(f"  {'ok ' if cond else 'FAIL'} {label}")

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "docs/research").mkdir(parents=True)
        (root / "docs/design").mkdir(parents=True)
        (root / "docs/claude/work/objects").mkdir(parents=True)
        (root / "docs/research/X-SESSION-PROMPT.md").write_text("Paste this whole file.\n")
        (root / "docs/research/Y-DESIGN.md").write_text("a finished design\n")
        (root / "docs/research/plain-memo.md").write_text("just a measurement\n")
        (root / "docs/claude/work/objects/w.yaml").write_text(
            "id: WO-1\nspec: docs/research/Y-DESIGN.md\n")

        corpus = carrier_corpus(root)
        ok("the carrier corpus loads from the work store",
           "docs/research/Y-DESIGN.md" in corpus)

        v = classify(pathlib.Path("docs/research/X-SESSION-PROMPT.md"), root, corpus)
        ok("a SESSION-PROMPT nothing points at is UNCARRIED", v["state"] == UNCARRIED)
        ok("…and it is flagged as self-referential, the sharp half", v["by_text"])
        v = classify(pathlib.Path("docs/research/Y-DESIGN.md"), root, corpus)
        ok("a spec a work object NAMES is carried", v["state"] == CARRIED)
        v = classify(pathlib.Path("docs/research/plain-memo.md"), root, corpus)
        ok("an ordinary memo is not_a_spec — the guard is not a docs tax",
           v["state"] == NOT_A_SPEC)
        v = classify(pathlib.Path("src/runtime/pipeline.py"), root, corpus)
        ok("a file outside the spec directories is not_a_spec", v["state"] == NOT_A_SPEC)
        v = classify(pathlib.Path("docs/research/absent-DESIGN.md"), root, corpus)
        ok("an UNREADABLE file is undecidable, never a quiet pass",
           v["state"] == UNDECIDABLE)

        # A spec whose TEXT asks but whose NAME is ordinary is still caught —
        # the motivating artifact would have been missed by a name-only rule if
        # it had been called anything else.
        (root / "docs/research/quiet-name.md").write_text(
            "The next session should build this.\n")
        v = classify(pathlib.Path("docs/research/quiet-name.md"), root,
                     carrier_corpus(root))
        ok("an ORDINARY-NAMED file whose text asks for follow-up is still a spec",
           v["state"] == UNCARRIED and v["by_text"] and not v["by_name"])

        c = census(root)
        ok("the census counts spec-shaped artifacts and un-carried separately",
           c["spec_shaped"] == 3 and c["uncarried"] == 2)
        ok("…and splits the sharp self-referential subset out",
           len(c["uncarried_self_referential"]) == 2
           and len(c["uncarried_name_only"]) == 0)
        ok("…and reports the corpus size, so a corpus that failed to load cannot "
           "masquerade as 'everything is un-carried'", c["corpus_chars"] > 0)

    print(f"spec-carrier self-test: {'PASS' if not fails else 'FAIL'} "
          f"({len(fails)} failure(s))")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
