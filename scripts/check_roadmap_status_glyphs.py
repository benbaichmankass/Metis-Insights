#!/usr/bin/env python3
"""Guard: every ``## Milestone Roadmap`` status cell leads with a canonical glyph.

Why this exists (R6, S-ROADMAP-STATUS-REVIEW-2026-08-01):
``src/web/api/routers/roadmap.py::_normalize_status`` buckets a milestone by the
**leading** status glyph (``✅🔄🔜⚠️⛔📋``, the ROADMAP.md § "Status Key" set). When
a cell instead leads with an **off-spec** glyph (``🟡``/``🟢`` — not in the Status
Key and not in the router's ``_STATUS_EMOJI`` map), the glyph scan misses and the
parser falls through to a **keyword scan of the whole cell body**. That body
routinely contains words like "DONE"/"COMPLETE"/"CLOSED" (e.g. "E-3 CLOSED"), so
the milestone silently mis-buckets on ``/api/bot/roadmap`` — M21 read as ``done``
while it was actually dormant; M29/M30 led with ``🟢``. This guard forbids the
class at the source: it fails CI when any milestone status cell does NOT lead with
a canonical Status-Key glyph, so ``/api/bot/roadmap`` ``summary`` counts always
match the human table.

Same guard family as ``canonical-db-resolver`` / ``env-gate-guard`` /
``silent-empty-guard``. **Stdlib-only** (no fastapi import) so it runs in a
minimal CI job; ``tests/test_roadmap_status_glyphs.py`` cross-checks that
``_CANONICAL_GLYPHS`` below stays equal to the router's ``_STATUS_EMOJI`` keys.

Usage::

    python scripts/check_roadmap_status_glyphs.py           # checks ./ROADMAP.md
    python scripts/check_roadmap_status_glyphs.py path/to/ROADMAP.md

Exit 0 = every milestone cell leads with a mapped glyph; exit 1 = one or more
off-spec/prose-leading cells (the offenders are printed).
"""
from __future__ import annotations

import sys
from pathlib import Path

# MUST stay equal to src/web/api/routers/roadmap.py::_STATUS_EMOJI keys AND
# ROADMAP.md § "Status Key". Cross-checked by tests/test_roadmap_status_glyphs.py.
_CANONICAL_GLYPHS: frozenset[str] = frozenset({"✅", "🔄", "🔜", "📋", "⚠️", "⚠", "⛔"})

# The router only inspects the first 6 chars of the stripped cell for a glyph
# (roadmap.py::_normalize_status: ``glyph in stripped[:6]``). Match that exactly.
_LEAD_WINDOW = 6


def _split_table_row(line: str) -> list[str] | None:
    """Mirror of roadmap.py::_split_table_row — trimmed cells, or None."""
    s = line.strip()
    if not s.startswith("|"):
        return None
    return [c.strip() for c in s.strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(set(c) <= {"-", ":", " "} and c for c in cells)


def offending_rows(text: str) -> list[tuple[str, str]]:
    """Return (milestone_id, status_cell) for every cell NOT leading with a glyph.

    Parsing mirrors roadmap.py::_parse_milestones so the guard validates exactly
    what the router parses (find the ``## Milestone Roadmap`` table, first real
    row is the header, stop at the next heading / trailing prose line).
    """
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## ") and "Milestone Roadmap" in ln:
            start = i
            break
    if start is None:
        # No milestone table at all — treat as a structural failure, not a pass.
        return [("<no-table>", "no '## Milestone Roadmap' section found")]

    offenders: list[tuple[str, str]] = []
    header_seen = False
    for ln in lines[start + 1:]:
        if ln.strip().startswith("#"):
            break
        cells = _split_table_row(ln)
        if cells is None:
            if header_seen:
                break
            continue
        if len(cells) < 4 or _is_separator_row(cells):
            continue
        if not header_seen:
            header_seen = True  # first real row is the header
            continue
        mid = cells[0].strip().strip("*").strip()
        if not mid:
            continue
        status_cell = cells[3]
        lead = status_cell.strip().lstrip("*").strip()[:_LEAD_WINDOW]
        if not any(g in lead for g in _CANONICAL_GLYPHS):
            offenders.append((mid, status_cell.strip()))
    return offenders


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("ROADMAP.md")
    if not path.exists():
        print(f"check_roadmap_status_glyphs: {path} not found", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8", errors="replace")
    offenders = offending_rows(text)
    if not offenders:
        print(f"check_roadmap_status_glyphs: OK — every milestone cell in {path} "
              f"leads with a canonical glyph ({''.join(sorted(_CANONICAL_GLYPHS))}).")
        return 0
    print(
        "check_roadmap_status_glyphs: FAIL — the following milestone status cells "
        "do NOT lead with a canonical Status-Key glyph, so /api/bot/roadmap will "
        "keyword-scan the cell body and can mis-bucket the milestone:",
        file=sys.stderr,
    )
    for mid, cell in offenders:
        print(f"  - {mid}: {cell[:100]}", file=sys.stderr)
    print(
        f"\nFix: make each cell lead with one of {''.join(sorted(_CANONICAL_GLYPHS))} "
        "(ROADMAP.md § 'Status Key'). Do NOT use 🟡/🟢 — they are off-spec.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
