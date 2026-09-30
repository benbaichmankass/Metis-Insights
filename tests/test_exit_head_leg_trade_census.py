"""The per-year trade census must count what the harness emitted and hide nothing."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _mod():
    sys.path.insert(0, str(REPO / "scripts" / "research"))
    spec = importlib.util.spec_from_file_location(
        "exit_head_leg_trade_census", REPO / "scripts/research/exit_head_leg_trade_census.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_count_by_year_iso_epoch_and_unreadable(tmp_path):
    m = _mod()
    p = tmp_path / "t.jsonl"
    rows = [{"entry_time": "2025-03-01T00:00:00+00:00"},
            {"entry_time": "2025-12-31T23:45:00Z"},
            {"entry_time": 1780000000},            # 2026-05-28 UTC
            {"entry_time": None},                  # unreadable, must be counted
            {"no_entry_time": 1}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    out = m.count_by_year(p)
    assert out["by_year"] == {2025: 2, 2026: 1}
    assert out["total"] == 3 and out["unreadable_rows"] == 2
