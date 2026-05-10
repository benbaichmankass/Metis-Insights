"""Architecture-doc guard (S-AI-WS10).

Soft-warning helper invoked from
``.github/workflows/arch-doc-guard.yml``. Given a list of changed
file paths (one per line on stdin, or ``--changed-files``), prints
a ``::warning`` GitHub Actions annotation when the PR touches
high-impact subsystems without also touching the architecture doc.

The script ALWAYS exits 0 — by design. Hard-failing this check
would push the team to bypass it ("ignore the docs job, I'll
update later") instead of updating the docs. Advisory beats
adversarial; a future workstream can upgrade once the workflow
is fluent.

Heuristic:

- ``HIGH_IMPACT_PATTERNS`` lists fnmatch-style globs that count as
  architecture-impacting code. Adjust as the codebase evolves —
  see ``docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`` for
  the criteria.
- ``ARCH_DOC_PATTERNS`` lists arch-doc paths. If any changed file
  matches one of these, the PR is considered "doc-updated" and no
  warning fires.

Run locally::

    python -m scripts.arch_doc_guard --changed-files=$(
        git diff --name-only origin/main...HEAD | paste -sd ' '
    )

Exit status: always 0.
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from typing import Iterable, Sequence

# Patterns that flag a PR as architecture-impacting. Order doesn't
# matter (any match is enough). Use fnmatch-style globs;
# directory recursion needs an explicit ``/**/`` segment.
HIGH_IMPACT_PATTERNS: tuple[str, ...] = (
    # Pipeline + coordinator + runtime
    "src/pipeline/**",
    "src/pipeline/*.py",
    "src/core/coordinator.py",
    "src/core/dispatcher*.py",
    "src/runtime/pipeline.py",
    "src/runtime/shadow_adapter.py",
    "src/runtime/health.py",
    # Strategies + dashboards + web surfaces
    "src/units/strategies/**",
    "src/units/strategies/*.py",
    "src/units/dashboards/**",
    "src/web/api/main.py",
    "src/web/api/routers/**",
    # ML model boundary
    "ml/registry/**",
    "ml/predictors/**",
    "ml/promotion/**",
    "ml/shadow/**",
    "ml/trainers/**",
    "ml/evaluators/**",
    "ml/datasets/**",
    # Configuration shape
    "config/strategies.yaml",
    "config/accounts.yaml",
    "config/units.yaml",
)

# Paths that, when touched, satisfy the guard. Touching any of
# these in a PR with high-impact code changes is the documented
# escape hatch.
ARCH_DOC_PATTERNS: tuple[str, ...] = (
    "docs/ARCHITECTURE-CANONICAL.md",
    "docs/architecture/**",
    "docs/architecture/*.md",
    "docs/pipeline/stage-contracts.md",
    "docs/CLAUDE-RULES-CANONICAL.md",
    "CLAUDE.md",
)


def _any_match(path: str, patterns: Sequence[str]) -> bool:
    """Return True if *path* matches any of *patterns* via
    ``fnmatch.fnmatch``. ``**`` is treated as wildcard-recursive by
    converting to ``*`` (fnmatch's globbing doesn't distinguish
    levels, which is fine for our flat-ish checks)."""
    flattened = [p.replace("**/", "*").replace("**", "*") for p in patterns]
    return any(fnmatch.fnmatch(path, p) for p in flattened)


def classify(
    changed: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Split *changed* file paths into (high_impact, arch_doc).

    Both lists are returned in input order with duplicates
    preserved — the caller can use them to compose the warning
    message.
    """
    high_impact: list[str] = []
    arch_doc: list[str] = []
    for raw in changed:
        path = raw.strip()
        if not path:
            continue
        if _any_match(path, HIGH_IMPACT_PATTERNS):
            high_impact.append(path)
        if _any_match(path, ARCH_DOC_PATTERNS):
            arch_doc.append(path)
    return high_impact, arch_doc


def format_warning(high_impact: Sequence[str]) -> str:
    """Render the GitHub Actions ``::warning`` annotation body."""
    sample = ", ".join(high_impact[:5])
    extra = "" if len(high_impact) <= 5 else f" (+{len(high_impact) - 5} more)"
    return (
        "::warning title=Architecture-doc guard::This PR touches "
        f"{len(high_impact)} high-impact path(s) "
        f"({sample}{extra}) but does not update any architecture "
        "doc. If the change is architectural, update "
        "docs/ARCHITECTURE-CANONICAL.md (and the Change log row) "
        "or a companion doc. If not, tick "
        "'Architecture impact: Not applicable' in the PR template. "
        "See docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md."
    )


def _read_changed_files(args: argparse.Namespace) -> list[str]:
    # An explicit ``--changed-files=""`` (empty string) means "no
    # changed files" and is honoured as such — only ``None`` (the
    # flag absent) falls through to stdin.
    if args.changed_files is not None:
        return [p for p in args.changed_files.split() if p]
    return [line for line in sys.stdin.read().splitlines() if line.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arch_doc_guard")
    parser.add_argument(
        "--changed-files",
        default=None,
        help=(
            "Whitespace-separated list of changed file paths. If "
            "omitted, read newline-separated paths from stdin."
        ),
    )
    args = parser.parse_args(argv)
    changed = _read_changed_files(args)
    high_impact, arch_doc = classify(changed)
    if high_impact and not arch_doc:
        print(format_warning(high_impact))
    return 0  # always advisory


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
