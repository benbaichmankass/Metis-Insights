"""The e35 corpus must carry an ACHIEVED OOS sample size, not a run target.

WHY THIS EXISTS
---------------
R4's power gate needs to know whether a cell was judged on enough data. Until
2026-08-31 the e35 corpus carried no achieved count at all, so
`research_disposition.N_FIELD["e35"]` was `None` and every e35 unit was
UNGRADEABLE — 28 of them sat in exactly that state.

The tempting field, `split_target_oos`, is wrong twice over: it is a run TARGET
rather than a measurement, and measured over the whole corpus on 2026-08-31 it
is non-null on 377 of 8,321 rows (4.5%) with exactly ONE distinct value, 50.
Keying the gate on it would grade 4.5% of the corpus against a constant and call
the rest unknown.

The producer emitted the real counts all along —
`e35_bracket_geometry_sweep.gate` writes `base_is_trades` / `base_oos_trades`,
both `run_cell(...)["total_trades"]` — and the extractor never read them.
Written-and-never-read is the `exit_price_source` shape this repo already pays
for, one layer down.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXTRACT = REPO / "scripts/research/e35_corpus_extract.py"


def _report(**gate_extra):
    """A minimal report.json in the shape the extractor's own selftest uses."""
    gate = {
        "cell": "sm1.5", "verdict": "is_oos_fail",
        "is": {"passed": True, "d_net_r": 1.0, "reason": "ok"},
        "oos": {"passed": False, "d_net_r": -2.0, "reason": "net_r_worse"},
        "split_meta": {"split_mode": "oos-trades", "split_target_oos": 50},
    }
    gate.update(gate_extra)
    return {
        "generated_at": "2026-08-31T04:00:00+00:00",
        "tp_cap_pct": 0.099,
        "fee_bps_roundtrip": 0.0,
        "legs": [{
            "leg": "demo_leg", "symbol": "GLD", "tf": "1h",
            "family": "pullback", "execution": "live",
            "base": {"net_total_r": 1.0, "max_drawdown_r": -1.0},
            "cells": [{"cell": "sm1.5", "axis": "stop", "stop_mult": 1.5,
                       "net_total_r": 2.0, "max_drawdown_r": -1.0,
                       "net_expectancy_r": 0.1, "d_net_r": 1.0, "d_max_dd": 0.0}],
            # `gate` is nested INSIDE the leg — the extractor builds
            # gate_by_cell from leg["gate"], not from a top-level key.
            "gate": [gate],
        }],
    }


def _extract(tmp_path, report) -> list[dict]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps(report))
    store = tmp_path / "corpus.jsonl"
    r = subprocess.run(
        [sys.executable, str(EXTRACT), str(run_dir), "--corpus", str(store)],
        cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return [json.loads(ln) for ln in store.read_text().splitlines() if ln.strip()]


def test_the_achieved_oos_count_reaches_the_corpus(tmp_path):
    """The positive control: when the producer emits it, the row carries it."""
    rows = _extract(tmp_path, _report(base_oos_trades=137, base_is_trades=402))
    assert rows, "no rows extracted"
    assert rows[0]["base_oos_trades"] == 137
    assert rows[0]["base_is_trades"] == 402


def test_an_absent_count_is_null_never_zero(tmp_path):
    """A zero would assert a MEASURED empty sample. Absent is not empty."""
    rows = _extract(tmp_path, _report())
    assert rows[0]["base_oos_trades"] is None
    assert rows[0]["base_is_trades"] is None


def test_the_power_gate_reads_the_achieved_field_not_the_target():
    """N_FIELD must not point at `split_target_oos`.

    It is a run setting with one distinct value across the corpus; keying the
    gate on it would compare every cell against a constant.
    """
    sys.path.insert(0, str(REPO))
    from scripts.research.research_disposition import N_FIELD
    assert N_FIELD["e35"] == "base_oos_trades", N_FIELD["e35"]
    assert N_FIELD["e35"] != "split_target_oos"


def test_split_target_oos_really_is_a_constant_in_the_live_corpus():
    """Pins the measurement the choice above rests on, so it cannot rot silently.

    If a future sweep ever varies the target, this fails and the reasoning in
    N_FIELD's comment needs re-reading rather than trusting.
    """
    store = REPO / "docs/research/e35-bracket-corpus.jsonl"
    if not store.exists():
        return
    vals, rows = set(), 0
    with store.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            rows += 1
            v = json.loads(line).get("split_target_oos")
            if v is not None:
                vals.add(v)
    assert rows, "corpus is empty — this test would be vacuous"
    assert vals == {50}, f"split_target_oos is no longer a single constant: {vals}"
