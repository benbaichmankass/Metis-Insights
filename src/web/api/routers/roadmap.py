"""Tier-1 read endpoints exposing the product roadmap + sprint-log ledger.

Backs the dashboard's **Roadmap** tab — a progress visualization that opens
roadmap → milestones → sprints, surfacing the notes/summaries from each work
session (the sprint logs).

- ``GET /api/bot/roadmap`` — the parsed milestone table (with a normalized
  status + progress roll-up) plus an index of every sprint log
  (id, title, date, owning milestone, path). Newest-first sprint index.
- ``GET /api/bot/roadmap/sprint/{sprint_id}`` — one sprint log parsed into its
  ``##`` sections plus the raw markdown, so a consumer can render the full
  session write-up.

File-backed from the committed ``ROADMAP.md`` + ``docs/sprint-logs/*.md`` (the
VM's ``ict-git-sync`` mirrors ``main``). Read-only, no secrets, no DB — so this
adds no table and is exempt from the new-table-wiring guard. Best-effort: a
missing/garbled roadmap degrades to an empty envelope, never a 5xx.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from src.utils.paths import repo_root

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/roadmap", tags=["roadmap"])

# Sprint-log filenames are constrained; also guards the {sprint_id} path param
# against traversal (we only ever open <dir>/<id>.md).
_SPRINT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Leading status glyph on a milestone/sprint status cell → normalized token.
# Mirrors ROADMAP.md § "Status Key".
_STATUS_EMOJI: dict[str, str] = {
    "✅": "done",
    "🔄": "in_progress",
    "🔜": "next",
    "📋": "planned",
    "⚠️": "reopened",
    "⚠": "reopened",
    "⛔": "blocked",
}

# Coarse buckets for the progress roll-up.
_DONE = {"done"}
_ACTIVE = {"in_progress", "next", "reopened"}
_PENDING = {"planned", "blocked"}

# Milestone inferred from a sprint-log filename when the roadmap cell doesn't
# reference it explicitly. Ordered — first match wins.
_FILENAME_MILESTONE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^S-M(\d+)\b"), "M{0}"),           # S-M7-…, S-M15-…
    (re.compile(r"^S-MLOPT\b"), "M14"),
    (re.compile(r"^S-ANDROID\b"), "M12"),
    (re.compile(r"^S-AI-WS\b"), "M9"),
    (re.compile(r"^S-REFACTOR\b"), "M11"),
    (re.compile(r"^S-AUDIT\b"), "M17"),
    (re.compile(r"^S-ALLOC\b"), "M18"),
    (re.compile(r"^S-STRAT-IMPROVE\b"), "M14"),
]

_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def _roadmap_path() -> Path:
    return Path(repo_root()) / "ROADMAP.md"


def _sprint_logs_dir() -> Path:
    return Path(repo_root()) / "docs" / "sprint-logs"


def _normalize_status(text: str) -> tuple[str, str]:
    """Return (token, glyph) for a status cell from its leading emoji.

    Falls back to a keyword scan so a cell that leads with prose still buckets.
    """
    stripped = text.strip().lstrip("*").strip()
    for glyph, token in _STATUS_EMOJI.items():
        if glyph in stripped[:6]:
            return token, glyph
    upper = stripped.upper()
    if "DONE" in upper or "COMPLETE" in upper or "CLOSED" in upper:
        return "done", "✅"
    if "IN PROGRESS" in upper:
        return "in_progress", "🔄"
    if "PROPOSED" in upper or "NOT STARTED" in upper or "BACKLOG" in upper:
        return "planned", "📋"
    if "BLOCKED" in upper:
        return "blocked", "⛔"
    return "unknown", "⚪"


def _split_table_row(line: str) -> list[str] | None:
    """Split a markdown table row into trimmed cells, or None if not a row."""
    s = line.strip()
    if not s.startswith("|"):
        return None
    # Drop the leading/trailing pipe, split on unescaped pipes.
    cells = [c.strip() for c in s.strip("|").split("|")]
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    return all(set(c) <= {"-", ":", " "} and c for c in cells)


def _clean_cell(cell: str) -> str:
    """Strip bold markers off a short label cell (e.g. ``**M7**`` → ``M7``)."""
    return cell.strip().strip("*").strip()


def _short_label(status_text: str, limit: int = 160) -> str:
    """A one-line status label: the text up to the first sentence/dash break."""
    t = status_text.strip().lstrip("*").strip()
    # Cut at the first em-dash / period that ends the headline clause.
    for sep in (" — ", " – ", ". ", " ("):
        idx = t.find(sep)
        if 0 < idx < limit:
            return t[:idx].strip()
    return (t[:limit] + "…") if len(t) > limit else t


def _sprint_refs_in(cell: str) -> list[str]:
    """Sprint-log ids referenced by ``docs/sprint-logs/<id>.md`` links in a cell."""
    ids: list[str] = []
    for m in re.finditer(r"docs/sprint-logs/([A-Za-z0-9._-]+?)\.md", cell):
        sid = m.group(1)
        if sid not in ids:
            ids.append(sid)
    return ids


def _parse_milestones(text: str) -> list[dict[str, Any]]:
    """Parse the ``## Milestone Roadmap`` table into milestone dicts.

    Each milestone carries the sprint ids its status cell links to, so the
    consumer can drill milestone → sprint without a separate mapping.
    """
    lines = text.splitlines()
    # Find the milestone-table section.
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## ") and "Milestone Roadmap" in ln:
            start = i
            break
    if start is None:
        return []
    milestones: list[dict[str, Any]] = []
    header_seen = False
    for ln in lines[start + 1:]:
        if ln.strip().startswith("#"):
            break  # any heading (## or ###) ends the milestone table
        cells = _split_table_row(ln)
        if cells is None:
            # A non-table line AFTER the table body has started ends the table —
            # this stops us running into the ### sub-tables in the same section.
            if header_seen:
                break
            continue
        if len(cells) < 4:
            continue
        if _is_separator_row(cells):
            continue
        if not header_seen:
            # First real row is the header (| Milestone | Type | Focus | Status |)
            header_seen = True
            continue
        mid = _clean_cell(cells[0])
        if not mid:
            continue
        status_cell = cells[3]
        token, glyph = _normalize_status(status_cell)
        milestones.append(
            {
                "id": mid,
                "type": cells[1].strip(),
                "focus": cells[2].strip(),
                "status": token,
                "statusEmoji": glyph,
                "statusLabel": _short_label(status_cell),
                "statusDetail": status_cell.strip(),
                "sprintRefs": _sprint_refs_in(status_cell),
            }
        )
    return milestones


def _parse_ledger_map(text: str) -> dict[str, str]:
    """Map sprint-log id → milestone from the ``## Historical Sprint Ledger``.

    Each ledger row links its sprint file(s) and names an ``M-mapping`` in the
    last column; we tie every referenced sprint file to that milestone token.
    """
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## ") and "Sprint Ledger" in ln:
            start = i
            break
    if start is None:
        return {}
    out: dict[str, str] = {}
    header_seen = False
    for ln in lines[start + 1:]:
        if ln.strip().startswith("## "):
            break
        cells = _split_table_row(ln)
        if not cells or len(cells) < 4 or _is_separator_row(cells):
            continue
        if not header_seen:
            header_seen = True
            continue
        mm = re.search(r"M\d+", cells[-1])
        if not mm:
            continue
        milestone = mm.group(0)
        for cell in cells:
            for sid in _sprint_refs_in(cell):
                out.setdefault(sid, milestone)
    return out


def _parse_last_updated(text: str) -> tuple[str | None, str | None]:
    """Return (date, headline) from ROADMAP.md's '> **Last Updated:**' line.

    That line is a single enormous run-on paragraph (the full change note), so
    we extract only the leading date + the first bold parenthetical headline —
    the rest is unreadable as a banner (and it bloated the payload ~15 KB)."""
    m = re.search(r">\s*\*\*Last Updated:\*\*\s*(.+)", text)
    if not m:
        return None, None
    raw = m.group(1).strip()
    dm = re.match(r"(20\d{2}-\d{2}-\d{2})", raw)
    date = dm.group(1) if dm else None
    hm = re.search(r"\(\*\*(.+?)\*\*\)", raw)
    headline = hm.group(1).strip() if hm else None
    if headline is None and date is None:
        # No structured header — fall back to the first short clause.
        headline = raw.split(" — ")[0].strip()[:120] or None
    return date, headline


def _infer_milestone_from_name(sprint_id: str) -> str | None:
    for pat, tmpl in _FILENAME_MILESTONE_RULES:
        m = pat.match(sprint_id)
        if m:
            return tmpl.format(*m.groups()) if m.groups() else tmpl
    return None


def _parse_sprint_head(path: Path) -> dict[str, Any]:
    """Light parse of a sprint log's head for the index (title + dates)."""
    title = path.stem
    objective = None
    date_start = None
    date_end = None
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return {"title": title, "objective": None, "dateStart": None, "dateEnd": None}
    lines = head.splitlines()
    section = None
    for ln in lines:
        s = ln.strip()
        if s.startswith("## "):
            section = s[3:].strip().lower()
            continue
        if section == "date range":
            if s.lower().startswith("- start"):
                dm = _DATE_RE.search(s)
                if dm:
                    date_start = dm.group(1)
            elif s.lower().startswith("- end"):
                dm = _DATE_RE.search(s)
                if dm:
                    date_end = dm.group(1)
        elif section == "objective" and objective is None:
            m = re.search(r"-\s*(?:primary goal|goal)\s*:\s*(.+)", s, re.IGNORECASE)
            if m:
                objective = m.group(1).strip().rstrip(".") or None
    # Fall back to the id's trailing date for the timeline sort.
    if not date_end:
        dm = _DATE_RE.search(path.stem)
        if dm:
            date_end = dm.group(1)
    return {
        "title": title,
        "objective": objective,
        "dateStart": date_start,
        "dateEnd": date_end,
    }


# ── module-level cache keyed on (roadmap mtime, dir mtime, file count) ────────
_CACHE: dict[str, Any] = {}


def _cache_key() -> tuple:
    rp = _roadmap_path()
    dp = _sprint_logs_dir()
    try:
        rm = rp.stat().st_mtime_ns if rp.exists() else 0
    except OSError:
        rm = 0
    try:
        files = list(dp.glob("*.md")) if dp.exists() else []
        dm = max((f.stat().st_mtime_ns for f in files), default=0)
        n = len(files)
    except OSError:
        dm, n = 0, 0
    return (rm, dm, n)


def _build_index() -> dict[str, Any]:
    rp = _roadmap_path()
    if not rp.exists():
        return {
            "present": False,
            "milestones": [],
            "sprints": [],
            "summary": {"done": 0, "active": 0, "pending": 0, "total": 0},
            "sprintCount": 0,
        }
    try:
        text = rp.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("roadmap: failed to read %s: %s", rp, exc)
        return {"present": False, "error": str(exc), "milestones": [], "sprints": [],
                "summary": {"done": 0, "active": 0, "pending": 0, "total": 0}, "sprintCount": 0}

    milestones = _parse_milestones(text)

    # Reverse index: sprint_id -> milestone id. Priority: explicit ref in a
    # milestone status cell > the Historical Sprint Ledger's M-mapping.
    ref_map: dict[str, str] = {}
    for ms in milestones:
        for sid in ms["sprintRefs"]:
            ref_map.setdefault(sid, ms["id"])
    for sid, milestone in _parse_ledger_map(text).items():
        ref_map.setdefault(sid, milestone)

    # Enumerate sprint logs.
    sprints: list[dict[str, Any]] = []
    dp = _sprint_logs_dir()
    known_ids = {m["id"] for m in milestones}
    for f in (dp.glob("*.md") if dp.exists() else []):
        sid = f.stem
        head = _parse_sprint_head(f)
        milestone = ref_map.get(sid) or _infer_milestone_from_name(sid)
        if milestone and milestone not in known_ids:
            # Inference produced an id not in the table (e.g. M9 aliasing M10) —
            # keep it only if it's a plausible milestone token, else drop to None.
            milestone = milestone if re.match(r"^M\d+$", milestone) else None
        sprints.append(
            {
                "id": sid,
                "title": head["title"],
                "objective": head["objective"],
                "dateStart": head["dateStart"],
                "dateEnd": head["dateEnd"],
                "milestone": milestone,
                "path": f"docs/sprint-logs/{f.name}",
            }
        )

    # Newest-first by end date (then id) so the timeline reads top-down.
    sprints.sort(key=lambda s: (s["dateEnd"] or "", s["id"]), reverse=True)

    # Attach a per-milestone sprint-id list + count.
    by_ms: dict[str, list[str]] = {}
    for s in sprints:
        if s["milestone"]:
            by_ms.setdefault(s["milestone"], []).append(s["id"])
    for ms in milestones:
        ms["sprintIds"] = by_ms.get(ms["id"], [])
        ms["sprintCount"] = len(ms["sprintIds"])

    done = sum(1 for m in milestones if m["status"] in _DONE)
    active = sum(1 for m in milestones if m["status"] in _ACTIVE)
    pending = sum(1 for m in milestones if m["status"] in _PENDING)

    last_updated, last_updated_headline = _parse_last_updated(text)
    return {
        "present": True,
        "lastUpdated": last_updated,
        "lastUpdatedHeadline": last_updated_headline,
        "milestones": milestones,
        "sprints": sprints,
        "summary": {
            "done": done,
            "active": active,
            "pending": pending,
            "total": len(milestones),
        },
        "sprintCount": len(sprints),
    }


def _get_index() -> dict[str, Any]:
    key = _cache_key()
    if _CACHE.get("key") != key:
        _CACHE["key"] = key
        _CACHE["data"] = _build_index()
    return _CACHE["data"]


@router.get("")
def get_roadmap() -> dict[str, Any]:
    """Milestones + progress roll-up + the full sprint-log index (newest-first)."""
    return _get_index()


def _parse_sprint_sections(text: str) -> list[dict[str, str]]:
    """Split a sprint-log body into ``## <heading>`` → body blocks (in order)."""
    sections: list[dict[str, str]] = []
    heading: str | None = None
    buf: list[str] = []
    for ln in text.splitlines():
        if ln.startswith("## "):
            if heading is not None:
                sections.append({"heading": heading, "body": "\n".join(buf).strip()})
            heading = ln[3:].strip()
            buf = []
        elif ln.startswith("# "):
            # Top-level title — skip, it's the sprint id.
            continue
        else:
            if heading is not None:
                buf.append(ln)
    if heading is not None:
        sections.append({"heading": heading, "body": "\n".join(buf).strip()})
    return sections


@router.get("/sprint/{sprint_id}")
def get_sprint(sprint_id: str) -> dict[str, Any]:
    """One sprint log: parsed ``##`` sections + raw markdown + index metadata."""
    if not _SPRINT_ID_RE.match(sprint_id):
        raise HTTPException(status_code=400, detail="invalid sprint id")
    dp = _sprint_logs_dir()
    path = (dp / f"{sprint_id}.md")
    # Defense in depth: ensure the resolved path stays inside the logs dir.
    try:
        resolved = path.resolve()
        resolved.relative_to(dp.resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="invalid sprint id")
    if not resolved.exists():
        return {"present": False, "id": sprint_id}
    try:
        raw = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("roadmap: failed to read sprint %s: %s", sprint_id, exc)
        return {"present": False, "id": sprint_id, "error": str(exc)}

    # Pull this sprint's index row for milestone/date metadata.
    meta = next((s for s in _get_index().get("sprints", []) if s["id"] == sprint_id), None)
    return {
        "present": True,
        "id": sprint_id,
        "path": f"docs/sprint-logs/{sprint_id}.md",
        "milestone": (meta or {}).get("milestone"),
        "dateStart": (meta or {}).get("dateStart"),
        "dateEnd": (meta or {}).get("dateEnd"),
        "objective": (meta or {}).get("objective"),
        "sections": _parse_sprint_sections(raw),
        "markdown": raw,
    }
