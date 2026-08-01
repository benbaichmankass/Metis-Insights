"""CI guard test: every ROADMAP.md milestone status cell leads with a canonical glyph.

Backs R6 (S-ROADMAP-STATUS-REVIEW-2026-08-01). Runs in the existing pytest-run
suite, so it fails CI if a future milestone cell leads with an off-spec glyph
(``🟡``/``🟢``) or with prose — the class that silently mis-bucketed M21 as
``done`` on ``/api/bot/roadmap`` (the parser keyword-scans the body when the
leading glyph is unmapped, and cell bodies routinely contain DONE/COMPLETE/CLOSED).
"""
from __future__ import annotations

from pathlib import Path

from scripts.check_roadmap_status_glyphs import _CANONICAL_GLYPHS, offending_rows
from src.utils.paths import repo_root


def test_glyph_set_matches_router_status_emoji() -> None:
    """The stdlib-only guard must stay in lockstep with the router's map.

    This is the DRY safety net for the guard carrying its own inline copy of the
    canonical glyph set (so the guard can run without importing fastapi).
    """
    from src.web.api.routers.roadmap import _STATUS_EMOJI

    assert _CANONICAL_GLYPHS == frozenset(_STATUS_EMOJI.keys())


def test_committed_roadmap_has_no_offspec_leading_glyphs() -> None:
    text = (Path(repo_root()) / "ROADMAP.md").read_text(encoding="utf-8", errors="replace")
    offenders = offending_rows(text)
    assert offenders == [], (
        "ROADMAP.md milestone status cells must lead with a canonical Status-Key "
        f"glyph ({''.join(sorted(_CANONICAL_GLYPHS))}); offenders: {offenders}"
    )


def test_guard_flags_offspec_glyph() -> None:
    bad = (
        "## Milestone Roadmap\n\n"
        "| Milestone | Type | Focus | Status |\n"
        "|---|---|---|---|\n"
        "| **M99** | x | y | 🟢 P0 SCOPE LOCKED — E-3 CLOSED |\n"
    )
    offenders = offending_rows(bad)
    assert [mid for mid, _ in offenders] == ["M99"]


def test_guard_accepts_canonical_glyph() -> None:
    good = (
        "## Milestone Roadmap\n\n"
        "| Milestone | Type | Focus | Status |\n"
        "|---|---|---|---|\n"
        "| **M99** | x | y | 🔄 P0 SCOPE LOCKED — E-3 CLOSED |\n"
    )
    assert offending_rows(good) == []
