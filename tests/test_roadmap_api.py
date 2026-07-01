"""Tests for the /api/bot/roadmap router (dashboard Roadmap tab).

Two layers:

  * pure-parser unit tests over synthetic markdown (stable — no filesystem),
    covering the milestone-table boundary (must NOT run into the ``###``
    sub-tables in the same section), status normalization, the ledger
    M-mapping, and the sprint-section splitter.
  * integration tests via TestClient against the REAL committed ``ROADMAP.md``
    + ``docs/sprint-logs/`` (present/structure + the path-traversal guard).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.web.api.routers import roadmap as rm


# ── pure-parser unit tests ────────────────────────────────────────────────
_SYNTHETIC = """\
# Roadmap

> **Last Updated:** 2026-07-01 (test)

## M0..M15 Milestone Roadmap

| Milestone | Type | Focus | Status |
|---|---|---|---|
| **M6** | auto | Web app UI | 🔄 IN PROGRESS |
| **M7** | pm | Review gate | ✅ DONE — log [`S-M7-X`](docs/sprint-logs/S-M7-X.md). |
| **M18** | pm | Allocator | 📋 PROPOSED |

### A sub-table in the same section

| WS | Title | Status |
|---|---|---|
| **WS1** | Baseline | ✅ DONE |

## Historical Sprint Ledger

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| **S-FOO** | A thing [`S-FOO-2026-01-01`](docs/sprint-logs/S-FOO-2026-01-01.md) | ✅ Done | M9 |
"""


def test_parse_milestones_stops_before_subtable() -> None:
    ms = rm._parse_milestones(_SYNTHETIC)
    ids = [m["id"] for m in ms]
    # The WS1 row lives in a ### sub-table and must NOT be parsed as a milestone.
    assert ids == ["M6", "M7", "M18"]
    m7 = next(m for m in ms if m["id"] == "M7")
    assert m7["status"] == "done"
    assert m7["statusEmoji"] == "✅"
    assert "S-M7-X" in m7["sprintRefs"]
    assert next(m for m in ms if m["id"] == "M6")["status"] == "in_progress"
    assert next(m for m in ms if m["id"] == "M18")["status"] == "planned"


def test_parse_ledger_map() -> None:
    mapping = rm._parse_ledger_map(_SYNTHETIC)
    assert mapping.get("S-FOO-2026-01-01") == "M9"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("✅ DONE 2026-06-09", "done"),
        ("🔄 IN PROGRESS", "in_progress"),
        ("📋 PROPOSED", "planned"),
        ("⛔ Blocked", "blocked"),
        ("COMPLETE — no emoji", "done"),
        ("nothing here", "unknown"),
    ],
)
def test_normalize_status(text: str, expected: str) -> None:
    assert rm._normalize_status(text)[0] == expected


def test_parse_sprint_sections() -> None:
    body = "# Sprint Log: S-X\n\n## Objective\n- Primary goal: do a thing\n\n## Work Completed\n- item 1\n"
    secs = rm._parse_sprint_sections(body)
    heads = [s["heading"] for s in secs]
    assert heads == ["Objective", "Work Completed"]
    assert "do a thing" in secs[0]["body"]


def test_infer_milestone_from_name() -> None:
    assert rm._infer_milestone_from_name("S-M15-PHASE0-2026-06-10") == "M15"
    assert rm._infer_milestone_from_name("S-ANDROID-S1-2026-05-26") == "M12"
    assert rm._infer_milestone_from_name("S-MLOPT-S9-2026-06-04") == "M14"
    assert rm._infer_milestone_from_name("S-SOMETHING-ELSE") is None


# ── integration tests against the real repo ───────────────────────────────
@pytest.fixture
def client() -> TestClient:
    from src.web.api import main as api_main

    return TestClient(api_main.app)


def test_roadmap_index_present(client: TestClient) -> None:
    r = client.get("/api/bot/roadmap")
    assert r.status_code == 200
    data = r.json()
    assert data["present"] is True
    assert data["summary"]["total"] > 0
    assert data["sprintCount"] > 0
    # Every milestone row carries the normalized fields the dashboard reads.
    for m in data["milestones"]:
        assert {"id", "status", "statusEmoji", "focus", "sprintIds", "sprintCount"} <= set(m)
    # The sprint index is newest-first by end date.
    ends = [s["dateEnd"] or "" for s in data["sprints"]]
    assert ends == sorted(ends, reverse=True)


def test_sprint_detail_roundtrips(client: TestClient) -> None:
    idx = client.get("/api/bot/roadmap").json()
    assert idx["sprints"], "expected at least one sprint log in the repo"
    sid = idx["sprints"][0]["id"]
    r = client.get(f"/api/bot/roadmap/sprint/{sid}")
    assert r.status_code == 200
    d = r.json()
    assert d["present"] is True
    assert d["id"] == sid
    assert d["markdown"]
    assert isinstance(d["sections"], list)


def test_sprint_unknown_id_is_not_found_not_error(client: TestClient) -> None:
    r = client.get("/api/bot/roadmap/sprint/S-DOES-NOT-EXIST-9999")
    assert r.status_code == 200
    assert r.json()["present"] is False


def test_sprint_path_traversal_rejected(client: TestClient) -> None:
    # A traversal attempt must be rejected by the id validator (400), never
    # allowed to escape docs/sprint-logs/.
    r = client.get("/api/bot/roadmap/sprint/..%2f..%2fCLAUDE")
    assert r.status_code in (400, 404)
