"""ROADMAP_MACRO M1 — tests for the economic-calendar spine producer."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.macro.econ_calendar_produce import (
    _capture_meta,
    build_news_config_events,
    produce,
    rows_from_captures,
)
from tests.test_m1_econ_calendar_data import FIXTURE


def _write_capture(d: Path, name: str, md: str) -> Path:
    p = d / name
    p.write_text(md, encoding="utf-8")
    return p


def test_capture_meta_header_wins_over_filename(tmp_path):
    md = "<!-- country: DE  observed_at: 2026-07-29T06:38:00Z -->\n" + FIXTURE
    p = _write_capture(tmp_path, "US-20260101T000000Z.md", md)
    # header country/observed_at override the filename convention
    assert _capture_meta(p, md) == ("DE", "2026-07-29T06:38:00Z")


def test_capture_meta_falls_back_to_filename(tmp_path):
    p = _write_capture(tmp_path, "US-20260729T063800Z.md", FIXTURE)
    assert _capture_meta(p, FIXTURE) == ("US", "2026-07-29T06:38:00Z")


def test_rows_from_captures_stamps_and_skips(tmp_path):
    _write_capture(tmp_path, "US-20260729T063800Z.md", FIXTURE)
    _write_capture(tmp_path, "no-meta.md", FIXTURE)  # unstampable
    rows, skipped = rows_from_captures(tmp_path)
    assert rows and all(r["observed_at"] == "2026-07-29T06:38:00Z" for r in rows)
    assert [s["file"] for s in skipped] == ["no-meta.md"]


def test_produce_full_regen_is_idempotent(tmp_path):
    caps = tmp_path / "caps"
    caps.mkdir()
    _write_capture(caps, "US-20260729T063800Z.md", FIXTURE)
    snap = tmp_path / "snap.jsonl"
    up = tmp_path / "up.json"

    s1 = produce(captures_dir=caps, snapshots_path=snap, upcoming_path=up)
    assert s1["rows"] == s1["written"] > 0
    assert s1["resolved_with_surprise"] >= 1  # the EIA gas case has a surprise
    first = snap.read_text()

    # re-running over the same captures reproduces the log byte-for-byte
    s2 = produce(captures_dir=caps, snapshots_path=snap, upcoming_path=up)
    assert snap.read_text() == first
    assert s2["rows"] == s1["rows"]

    # the upcoming.json holds the forward calendar
    up_doc = json.loads(up.read_text())
    kinds = {e["kind"] for e in up_doc["events"]}
    assert "fomc" in kinds


def test_produce_new_capture_adds_rows_pit(tmp_path):
    caps = tmp_path / "caps"
    caps.mkdir()
    _write_capture(caps, "US-20260729T063800Z.md", FIXTURE)
    snap = tmp_path / "snap.jsonl"
    s1 = produce(captures_dir=caps, snapshots_path=snap, upcoming_path=None)

    # a second, later capture → additional PIT lines (never an overwrite)
    _write_capture(caps, "US-20260805T063800Z.md", FIXTURE)
    s2 = produce(captures_dir=caps, snapshots_path=snap, upcoming_path=None)
    assert s2["rows"] == 2 * s1["rows"]
    obs = {json.loads(ln)["observed_at"] for ln in snap.read_text().splitlines()}
    assert obs == {"2026-07-29T06:38:00Z", "2026-08-05T06:38:00Z"}


def test_build_news_config_events_filters_class_impact_window(tmp_path):
    from scripts.macro.econ_calendar_produce import _latest_upcoming
    rows, _ = rows_from_captures(_seed(tmp_path))
    upcoming = _latest_upcoming(rows)
    events = build_news_config_events(upcoming, as_of="2026-07-29", horizon_days=45, min_impact=0.5)
    classes = {e["class"] for e in events}
    # fomc (impact 1.0) + pce (HIGH) are in-window, mapped, above min_impact
    assert "fomc" in classes and "pce" in classes
    # EIA gas upcoming was LOW impact (0.2) → excluded from the news config
    assert all(e["impact"] >= 0.5 for e in events)
    # every emitted event carries the {class, time, impact} the news layer reads
    for e in events:
        assert set(e) == {"class", "time", "impact"}


def test_emit_config_preserves_header(tmp_path):
    caps = _seed(tmp_path)
    cfg = tmp_path / "economic_calendar.yaml"
    # a stand-in base config with header + defaults + the events sentinel
    cfg_base = tmp_path / "config" / "economic_calendar.yaml"
    cfg_base.parent.mkdir(parents=True, exist_ok=True)
    cfg_base.write_text(
        "# header docs\ndefaults:\n  pre_window_minutes: 60\n"
        "symbol_event_classes:\n  MES: [fomc, cpi]\nevents: []\n",
        encoding="utf-8",
    )
    import scripts.macro.econ_calendar_produce as mod
    mod.DEFAULT_CONFIG = cfg_base  # point the header-preserver at our base
    produce(captures_dir=caps, snapshots_path=tmp_path / "s.jsonl",
            upcoming_path=None, emit_config=cfg, as_of="2026-07-29")
    out = cfg.read_text()
    assert "symbol_event_classes:" in out          # header preserved
    assert "class: fomc" in out                    # events populated
    assert out.count("events:") == 1               # single events block


def _seed(tmp_path: Path) -> Path:
    caps = tmp_path / "caps"
    caps.mkdir(exist_ok=True)
    (caps / "US-20260729T063800Z.md").write_text(FIXTURE, encoding="utf-8")
    return caps
