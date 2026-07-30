#!/usr/bin/env python3
"""Keep the research capability index complete — or fail the build.

WHY
---
`docs/research/RESEARCH-CAPABILITY-INDEX.md` exists because a 2026-07-30 session concluded
an ML exit head "cannot be replayed offline" and built a whole disposition on it. It can —
`scripts/research/analyze_exit_head.py` does exactly that. The session never found the tool
because **47 of 51 scripts in `scripts/research/` were mentioned in no skill**, while
`backtesting/SKILL.md` asserted it mapped *"every real backtest entry point in the repo."*

An index is only worth anything while it is complete, and **a stale index that looks
complete is worse than no index** — it is the same false-completeness that caused the
incident, just relocated. So completeness is mechanically enforced rather than trusted.

CONTRACT
--------
Every `scripts/research/*.py` must be either (a) referenced in the index, or (b) listed in
`EXEMPT` **with a reason**. There is no third state — "nobody got around to it" is not
representable, which is the point. A new research script fails CI until it is routed.

Stdlib-only; safe from CI or a session.

Usage:
  python scripts/ops/check_research_index.py          # exit 0 clean / 1 unindexed
  python scripts/ops/check_research_index.py --list   # print the index's coverage
"""
from __future__ import annotations

import argparse
import os
import pathlib

REPO = pathlib.Path(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
INDEX = pathlib.Path("docs/research/RESEARCH-CAPABILITY-INDEX.md")
RESEARCH_DIR = pathlib.Path("scripts/research")

# Scripts deliberately NOT routed in the index, each with the reason it needn't be.
# A reason is mandatory: an unexplained exemption is how a silence list forms.
# Currently empty, and that is the healthy state: every research script is routed.
# Add an entry ONLY with a real reason; the reason is mandatory and a dead entry is
# reported as stale, so this cannot quietly become a silence list.
EXEMPT: dict[str, str] = {}


def index_text(repo: pathlib.Path = REPO) -> str:
    p = repo / INDEX
    if not p.exists():
        raise FileNotFoundError(f"capability index missing: {INDEX}")
    return p.read_text(encoding="utf-8", errors="replace")


def research_scripts(repo: pathlib.Path = REPO) -> list[str]:
    d = repo / RESEARCH_DIR
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.py"))


def unindexed(repo: pathlib.Path = REPO) -> list[str]:
    """Scripts referenced neither in the index nor in EXEMPT."""
    text = index_text(repo)
    out = []
    for name in research_scripts(repo):
        if name in EXEMPT:
            continue
        # Match the bare filename; the index writes full repo-relative paths, so a
        # basename hit is sufficient and tolerates path-style changes.
        if name not in text:
            out.append(name)
    return out


def exemption_problems() -> list[str]:
    return [f"{k}: exemption has no reason" for k, v in EXEMPT.items()
            if not (v or "").strip()]


def stale_exemptions(repo: pathlib.Path = REPO) -> list[str]:
    """Exempted names that no longer exist — dead entries that hide future gaps."""
    # NO special cases. An earlier draft carved out the one entry the author had just
    # added so it could never be reported stale -- which is precisely the "exempt myself
    # from my own guard" move that makes a guard decorative. If an exemption names a file
    # that does not exist, it is dead weight hiding a future gap, whatever its name.
    present = set(research_scripts(repo))
    return sorted(k for k in EXEMPT if k not in present)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    repo = pathlib.Path(args.repo_root)

    try:
        scripts = research_scripts(repo)
        missing = unindexed(repo)
    except FileNotFoundError as exc:
        print(f"::error::{exc}")
        return 1

    if args.list:
        exempt_present = [k for k in EXEMPT if k in set(scripts)]
        print(f"{len(scripts)} research scripts; "
              f"{len(scripts) - len(missing) - len(exempt_present)} indexed, "
              f"{len(exempt_present)} exempt, {len(missing)} UNINDEXED")

    problems = exemption_problems()
    for s in stale_exemptions(repo):
        problems.append(f"{s}: exempted but no longer exists — remove the entry")

    if missing:
        print("::error::research scripts absent from the capability index "
              f"({len(missing)}): a session cannot route to a tool it cannot find, which "
              "is how the 2026-07-30 'ML exit head cannot be replayed' error happened.")
        for m in missing:
            print(f"  {RESEARCH_DIR}/{m}")
        print("")
        print(f"Fix: add a row to {INDEX} under the question it answers, or add it to "
              "EXEMPT in scripts/ops/check_research_index.py WITH a reason.")

    for p in problems:
        print(f"::error::{p}")

    if missing or problems:
        return 1
    print(f"OK — all {len(scripts)} research scripts are routed "
          f"({len(EXEMPT)} exempt).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
