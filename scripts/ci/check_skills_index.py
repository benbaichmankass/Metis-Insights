#!/usr/bin/env python3
"""CI guard: docs/claude/INDEX.md must name every skill, and invent none.

WHY (the defect class this guard exists to prevent recurring)
-------------------------------------------------------------
``docs/claude/INDEX.md`` is the routing surface a session reads to answer *"is
there already a skill for this?"*. Measured 2026-08-31 it named **12 of 31**
skills. The 19 absentees included ``system-review``, ``full-system-audit``,
``session-coordination``, ``backlog-drain``, ``research-driver``,
``exit-refinement`` and ``macro-research`` — i.e. most of the skills a session
would actually reach for.

An incomplete index is worse than no index, because this one presents itself as
the list: a session searching it and finding nothing gets a **negative with no
denominator** and reasonably concludes the capability does not exist — then
improvises, or reports it missing. That is the same shape ``workflow-catalog``
was built for (its doc index was 45.9% incomplete) and the same shape
``CLAUDE-RULES-CANONICAL`` § "Green is not evidence" names: an artifact whose
claim is true relative to its own scope, while the scope is wrong.

The cause is structural. Every inventory in this repo that STAYS correct has a
CI check behind it. This one did not, so every skill added since the index was
written could land unnamed and **none of them announced itself**.

WHAT IT CHECKS — two directions, because the gap runs both ways
---------------------------------------------------------------
**A. COMPLETENESS.** Every ``.claude/skills/*/SKILL.md`` must be named in the
index, in a backticked token or a link.

**B. NO PHANTOMS.** Every ``.claude/skills/<name>`` path or backticked skill
token in the index must resolve to a skill that exists — an index pointing at a
renamed or deleted skill sends a session to nothing.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections.abc import Sequence

INDEX = "docs/claude/INDEX.md"
SKILL_GLOB = ".claude/skills/*/SKILL.md"


def skill_names(root: str = ".") -> set[str]:
    return {
        os.path.basename(os.path.dirname(p))
        for p in glob.glob(os.path.join(root, SKILL_GLOB))
    }


def named_in_index(text: str) -> set[str]:
    """Skill names the index mentions as a backticked token or a skills path."""
    out: set[str] = set(re.findall(r"\.claude/skills/([a-z0-9][a-z0-9-]*)", text))
    out |= set(re.findall(r"`([a-z0-9][a-z0-9-]*)`", text))
    return out


def evaluate(skills: set[str], text: str) -> tuple[list[str], list[str]]:
    named = named_in_index(text)
    undocumented = sorted(skills - named)
    # Direction B is scoped to tokens that LOOK like a skill reference — a
    # bare backticked word is usually a filename or a field, so only an
    # explicit `.claude/skills/<name>` path counts as a phantom claim.
    path_refs = set(re.findall(r"\.claude/skills/([a-z0-9][a-z0-9-]*)", text))
    phantoms = sorted(p for p in path_refs if p not in skills)
    return undocumented, phantoms


def _self_test() -> int:
    idx = "see `.claude/skills/alpha` and `beta` for details"
    u, p = evaluate({"alpha", "beta"}, idx)
    assert (u, p) == ([], []), f"clean state should pass: {u} {p}"

    u, _ = evaluate({"alpha", "beta", "gamma"}, idx)
    assert u == ["gamma"], f"undocumented skill not caught: {u}"

    _, p = evaluate({"beta"}, idx)
    assert p == ["alpha"], f"phantom path not caught: {p}"

    # a backticked non-skill word must NOT be read as a phantom
    _, p = evaluate({"alpha", "beta"}, idx + " and `some-file`")
    assert p == [], f"bare backtick wrongly treated as a skill claim: {p}"

    print("skills-index: self-test OK — 4 planted controls all fire")
    return 0


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()

    path = os.path.join(args.root, INDEX)
    if not os.path.exists(path):
        print(f"skills-index: FAIL — {INDEX} not found")
        return 1
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    skills = skill_names(args.root)
    undocumented, phantoms = evaluate(skills, text)

    print(f"skills-index: {len(skills)} skill(s) · {len(skills) - len(undocumented)} named in {INDEX}")
    if not undocumented and not phantoms:
        print("skills-index: OK — every skill is named and every reference resolves")
        return 0
    if undocumented:
        print("\nFAIL — skills absent from the index that routes sessions to them:")
        for n in undocumented:
            print(f"  - {n}")
        print("\n  A session searching the index for this capability finds nothing and")
        print("  concludes it does not exist. Add it under the right group heading.")
    if phantoms:
        print("\nFAIL — index references a skill that does not exist:")
        for n in phantoms:
            print(f"  - .claude/skills/{n}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
