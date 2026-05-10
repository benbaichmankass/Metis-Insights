#!/usr/bin/env python3
"""Verify the architecture doc's "Verification Checklist" reflects
reality (S-AI-WS10 follow-up).

`docs/ARCHITECTURE-CANONICAL.md` ends with a "Verification
Checklist (current state)" section. Each `[x]` line names a path
the doc claims exists. This script walks those lines, resolves the
backtick-quoted paths against the repo root, and reports any that
no longer exist.

Output: JSON object with:

  {
    "checked": <N>,            # number of [x] lines processed
    "missing": [...],          # list of {"line": int, "path": str, "description": str}
    "verified": <N - len(missing)>,
    "audited_at_utc": "...",
    "doc_path": "..."
  }

Always exits 0. Drift detection is informational; the workflow
that calls this script decides whether to open an issue (or not).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DOC = Path("docs/ARCHITECTURE-CANONICAL.md")
_CHECKLIST_HEADER = "## Verification Checklist"
_CHECKED_LINE_RE = re.compile(r"^\s*-\s*\[x\]\s+(?P<desc>.+)$")
_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")


def parse_checklist(doc_text: str) -> list[tuple[int, str, list[str]]]:
    """Return [(lineno, description, paths)] for every `[x]` line in
    the Verification Checklist section. Path strings are extracted
    from any backtick-quoted spans on the line. Lines without
    backtick paths still get returned with an empty path list so
    the caller can decide whether to verify them via heuristic.
    """
    out: list[tuple[int, str, list[str]]] = []
    in_section = False
    for lineno, raw in enumerate(doc_text.splitlines(), start=1):
        line = raw.rstrip("\n")
        if line.startswith("## "):
            in_section = line.startswith(_CHECKLIST_HEADER)
            continue
        if not in_section:
            continue
        m = _CHECKED_LINE_RE.match(line)
        if not m:
            continue
        desc = m.group("desc").strip()
        paths = _BACKTICK_PATH_RE.findall(desc)
        out.append((lineno, desc, paths))
    return out


def _path_exists(repo_root: Path, candidate: str) -> bool:
    """Return True if `candidate` exists relative to repo_root.

    Handles:
      - exact path: `src/main.py`
      - directory glob: `deploy/ict-*.{service,timer}`
      - directory: `deploy/`
    """
    candidate = candidate.strip()
    if not candidate:
        return True  # nothing to verify
    # Brace-expand the simple `{a,b}` pattern manually — pathlib's
    # glob doesn't grok it. Spawning a `glob`-like across alternations
    # is enough for our doc.
    alternatives = _expand_braces(candidate)
    for alt in alternatives:
        if any(_resolve(repo_root, alt)):
            return True
    return False


def _expand_braces(pat: str) -> list[str]:
    m = re.search(r"\{([^{}]+)\}", pat)
    if not m:
        return [pat]
    options = m.group(1).split(",")
    prefix = pat[: m.start()]
    suffix = pat[m.end():]
    out: list[str] = []
    for opt in options:
        out.extend(_expand_braces(prefix + opt + suffix))
    return out


def _resolve(repo_root: Path, pat: str) -> list[Path]:
    """Glob `pat` against repo_root. Supports plain paths and
    fnmatch-style wildcards."""
    if any(ch in pat for ch in "*?["):
        return list(repo_root.glob(pat))
    target = repo_root / pat
    return [target] if target.exists() else []


def audit(repo_root: Path, doc_path: Path) -> dict:
    if not doc_path.is_file():
        return {
            "error": "doc_missing",
            "doc_path": str(doc_path),
            "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    items = parse_checklist(doc_path.read_text(encoding="utf-8"))
    missing: list[dict] = []
    # Heuristic: only verify the FIRST backtick-quoted path on each
    # `[x]` line. Subsequent backticked spans are usually commentary
    # ("X with sub-paths Y, Z") or path fragments not anchored at
    # the repo root. The first span is the primary assertion.
    checked = 0
    for lineno, desc, paths in items:
        if not paths:
            continue
        checked += 1
        primary = paths[0]
        if not _path_exists(repo_root, primary):
            missing.append({
                "line": lineno,
                "path": primary,
                "description": desc,
            })
    return {
        "doc_path": str(doc_path),
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "checked": checked,
        "missing": missing,
        "verified": checked - len(missing),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="audit_verification_checklist")
    parser.add_argument(
        "--doc", type=Path, default=_DEFAULT_DOC,
        help=f"path to the architecture doc (default: {_DEFAULT_DOC})",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(),
        help="repo root (default: cwd)",
    )
    args = parser.parse_args(argv)
    report = audit(args.repo_root, args.doc)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
