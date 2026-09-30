"""The record-scoped grandfather list (scripts/ops/pipeline.py,
GRANDFATHERED_COLLISION_RECORDS) exempts exactly the named record file --
never its whole id -- and the live store checks green with it.

Background: two update records for PI-20260929-AQRK6CL1-0014 were appended
from two different bases and both merged (2026-09-30); the later one is not an
append-only extension of the earlier, and append-only history cannot be
rewritten. The id-level GRANDFATHERED_COLLISIONS would also hide any FUTURE
collision on 0014, which that list's own comment forbids.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.ops import pipeline as pl

REPO = Path(__file__).resolve().parents[1]
REC = "20260930T080938782595Z-98f85ccf.json"


def test_only_the_named_record_is_grandfathered_not_its_id():
    assert pl._is_grandfathered_collision({"id": "PI-20260929-AQRK6CL1-0014", "at": REC})
    # the same id at any OTHER record is still a NEW collision
    assert not pl._is_grandfathered_collision({"id": "PI-20260929-AQRK6CL1-0014",
                                               "at": "20261001T000000000000Z-deadbeef.json"})
    assert "PI-20260929-AQRK6CL1-0014" not in pl.GRANDFATHERED_COLLISIONS
    # the pre-existing id-level entry keeps working
    assert pl._is_grandfathered_collision({"id": "PI-20260921-0002", "at": "anything.json"})


def test_the_live_store_checks_green_and_still_reports_the_collision():
    got = subprocess.run([sys.executable, "scripts/ops/pipeline.py", "--check"],
                         cwd=REPO, capture_output=True, text=True)
    assert got.returncode == 0, got.stdout[-2000:] + got.stderr[-2000:]
    line = [ln for ln in got.stdout.splitlines() if REC in ln]
    assert line and "GRANDFATHERED" in line[0]      # loud, never silently passed
    assert "NOT clean" in got.stdout                # a store with a collision is never "clean"


def test_the_displaced_text_was_restored():
    item = pl.load().items["PI-20260929-AQRK6CL1-0014"]
    assert "07:21Z" in item["what"] and "watchlist_diff changed:false" in item["what"]
    assert "BLOCKED BEFORE ANY CODE LANDED" in item["what"]
