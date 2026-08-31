"""THE SEAM TEST: one queue unit per label, driven through every real hop.

WHY THIS EXISTS. Every hop of the research chain is already tested in
ISOLATION — `grade_power` returns the right verdict, `_fire` injects the right
input, the extractor stamps a row, `load_units` reads a stamp back, `append`
refuses a premature close. All of those passed on 2026-08-31 while the chain
JOINING them was broken in two places at once: `load_units` recorded
`power_state` and nothing read it, and `append` would happily close an
`accruing` unit as answered. Both were found by reading, not by a failure.

That is this repo's own recorded lesson — *"every contributing component was
individually correct, which is why line-by-line audits kept returning clean:
the defect lives at the seams"* (`src/runtime/provenance.py`). A per-hop suite
cannot catch a label that stops travelling BETWEEN hops.

⚠️ NOTHING HERE RE-IMPLEMENTS A HOP. Each stage calls the production function
and feeds its real output to the next:

    grade_power  ->  _fire (subprocess captured)  ->  rows_from_report
                 ->  a real JSONL corpus  ->  load_units  ->  append

The `_fire` stage is captured rather than stubbed for a specific reason: an
earlier version of a neighbouring test re-implemented the injection rule and so
asserted only that the test and the test agreed. Here the real `gh workflow
run` argv is intercepted and the `-f` pairs are parsed back out, so the rule
under test is the one the dispatcher actually runs.

The env hand-off mirrors e35-bracket-sweep.yml verbatim:
    RESEARCH_UNIT: ${{ inputs.research_unit }}
    RESEARCH_POWER_STATE: ${{ inputs.power_state }}
A separate assertion in tests/test_research_queue.py pins those two lines, so
this file may model the hand-off without also being its own evidence.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.research import dispatch_queue as dq  # noqa: E402
from scripts.research.research_queue import (  # noqa: E402
    ACCRUING, CLEARED, DATA_SHORTFALL_STATES, INFEASIBLE, KIND_DETERMINISTIC,
    NOT_APPLICABLE, POWER_STATES, RUNNABLE_POWER_STATES, UNDECLARED,
    UNDERPOWERED, UNVERIFIABLE, grade_power,
)


def _mod(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rd = _mod("scripts/research/research_disposition.py", "_chain_rd")
e35 = _mod("scripts/research/e35_corpus_extract.py", "_chain_e35")


# ---------------------------------------------------------------------------
# One entry per label. The POINT is that these are graded, never asserted into
# a state: `_expect` says what the author believes, and stage 1 checks it.
# ---------------------------------------------------------------------------
def _entry(**kw):
    base = {
        "id": "RQ-CHAIN-000", "title": "t", "question": "q",
        "cadence": "once", "status": "queued", "kind": "experiment",
        "power": {"expected_n": 400, "min_detectable_effect": 0.3, "basis": "b",
                  "feasibility": {"source": "corpus", "corpus": "e35",
                                  "statistic": "min_per_leg"}},
        "routing": {"peak_memory_gb": 2.0},
        "run": {"workflow": "e35-bracket-sweep.yml",
                "inputs": {"only": "trend_donchian",
                           "research_unit": "RQ-CHAIN-000"}},
        "lands": {"store": "docs/research/e35-bracket-corpus.jsonl"},
    }
    base.update(kw)
    return base


def _power(**kw):
    p = {"expected_n": 400, "min_detectable_effect": 0.3, "basis": "b",
         "feasibility": {"source": "corpus", "corpus": "e35",
                         "statistic": "min_per_leg"}}
    p.update(kw)
    return p


#: (label, entry, observed-corpus-n). The observation is what makes `cleared`
#: and `infeasible` differ on IDENTICAL declarations — the distinction is in the
#: DATA, not in the YAML, which is the whole reason the gate reads a corpus.
CASES = [
    (CLEARED, _entry(id="RQ-CHAIN-CLR",
                     run={"workflow": "w.yml",
                          "inputs": {"research_unit": "RQ-CHAIN-CLR"}}), 10_000.0),
    (UNDERPOWERED, _entry(id="RQ-CHAIN-UND",
                          power=_power(expected_n=10),
                          run={"workflow": "w.yml",
                               "inputs": {"research_unit": "RQ-CHAIN-UND"}}), 10_000.0),
    (INFEASIBLE, _entry(id="RQ-CHAIN-INF",
                        run={"workflow": "w.yml",
                             "inputs": {"research_unit": "RQ-CHAIN-INF"}}), 4.0),
    (ACCRUING, _entry(id="RQ-CHAIN-ACC",
                      power=_power(feasibility={"source": "none",
                                                "accrual_basis": "declared, thin leg"}),
                      run={"workflow": "w.yml",
                           "inputs": {"research_unit": "RQ-CHAIN-ACC"}}), 10_000.0),
    (NOT_APPLICABLE, _entry(id="RQ-CHAIN-NA", kind=KIND_DETERMINISTIC,
                            why_not_inferential="re-grades a fixed ledger",
                            run={"workflow": "w.yml",
                                 "inputs": {"research_unit": "RQ-CHAIN-NA"}}), 10_000.0),
    (UNDECLARED, _entry(id="RQ-CHAIN-UDC", power=None,
                        run={"workflow": "w.yml",
                             "inputs": {"research_unit": "RQ-CHAIN-UDC"}}), 10_000.0),
    (UNVERIFIABLE, _entry(id="RQ-CHAIN-UVF",
                          power={"expected_n": 400, "min_detectable_effect": 0.3},
                          run={"workflow": "w.yml",
                               "inputs": {"research_unit": "RQ-CHAIN-UVF"}}), 10_000.0),
]


def _observe(monkeypatch, n):
    from scripts.research import research_queue as rq
    monkeypatch.setattr(rq, "observed_n_by_leg", lambda corpus: {"leg_a": n})


def _dispatch_inputs(monkeypatch, entry, state):
    """Run the REAL `_fire` and read back the inputs it would send."""
    seen = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _capture(cmd, **kw):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(dq.subprocess, "run", _capture)
    ok, _ = dq._fire(entry, route="runner", ref="main", power_state=state)
    assert ok
    cmd = seen["cmd"]
    out = {}
    for i, tok in enumerate(cmd):
        if tok == "-f":
            k, _, v = cmd[i + 1].partition("=")
            out[k] = v
    return out


def _report():
    return {
        "generated_at": "2026-08-31T00:00:00+00:00",
        "tp_cap_pct": 0.099, "fee_bps_roundtrip": 7.5,
        "legs": [{"leg": "trend_donchian", "symbol": "BTCUSDT", "tf": "1h",
                  "family": "trend", "execution": "live", "base": {},
                  "cells": [{"cell": "tp_r=3.0", "axis": "tp_r"}],
                  "gate": [{"cell": "tp_r=3.0", "base_oos_trades": 57,
                            "split_meta": {"split_mode": "oos-trades",
                                           "split_target_oos": 60}}]}],
    }


@pytest.mark.parametrize("label,entry,observed", CASES, ids=[c[0] for c in CASES])
def test_the_label_survives_every_hop(monkeypatch, tmp_path, label, entry, observed):
    """Grade -> dispatch -> stamp -> corpus -> read back. No hop re-implemented."""
    # 1. the gate
    _observe(monkeypatch, observed)
    verdict = grade_power(entry)
    assert verdict.state == label, (
        f"the fixture no longer grades {label!r}; it grades {verdict.state!r}. "
        f"Fix the fixture, never the assertion — the point is that the DATA "
        f"and the declaration decide the label.")

    # A blocked unit must never reach the rest of the chain. Asserting the
    # ABSENCE is as load-bearing as asserting the presence: a gate that blocks
    # and then lets the run happen anyway is worse than no gate.
    if label not in RUNNABLE_POWER_STATES:
        assert not verdict.runnable
        return
    assert verdict.runnable

    # 2. the dispatcher — the REAL argv it would send
    inputs = _dispatch_inputs(monkeypatch, entry, verdict.state)
    assert inputs.get("power_state") == label, (
        "the COMPUTED verdict must ride to the workflow; a hand-declared one "
        "could drift from what the gate actually decided")
    assert inputs.get("research_unit") == entry["id"]

    # 3. the workflow's env hand-off, verbatim from e35-bracket-sweep.yml
    monkeypatch.setenv("RESEARCH_UNIT", inputs["research_unit"])
    monkeypatch.setenv("RESEARCH_POWER_STATE", inputs["power_state"])

    # 4. the extractor
    rows = e35.rows_from_report(_report(), "chain-test")
    assert rows, "the extractor produced no rows — the chain proves nothing"

    # 5. a real corpus file
    corpus = tmp_path / "chain-corpus.jsonl"
    corpus.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setitem(rd.CORPORA, "e35",
                        (corpus, "sweep_generated_at", "leg"))

    # 6. the reader
    state, units = rd.load_units("e35")
    assert state == "read"
    assert units, "load_units read nothing back from a corpus it just wrote"
    for (_stamp, _leg), meta in units.items():
        assert meta["power_state"] == label, (
            f"the admission label did not survive the corpus round trip: "
            f"graded {label!r}, read back {meta['power_state']!r}")
        assert meta["research_unit"] == entry["id"]

    # 7. the disposition — leniency at the front door, strictness at the reading
    (stamp, leg), = units.items() if len(units) == 1 else [list(units.items())[0]]
    body = {"corpus": "e35", "run_stamp": stamp[0], "leg": stamp[1],
            "verdict": "no_action_warranted",
            "reason": "Both folds clear the gate and no cell moves; nothing to ship."}
    ledger = tmp_path / "ledger.jsonl"
    if label in DATA_SHORTFALL_STATES:
        with pytest.raises(ValueError, match=label):
            rd.append(body, ledger=ledger)
    else:
        rd.append(body, ledger=ledger)
        assert json.loads(ledger.read_text())["accrual_check"] == "clear"


def test_every_declared_label_is_covered_by_this_file():
    """THE DENOMINATOR. Without it, adding an eighth state and forgetting to
    wire it through the chain leaves this suite green over a population that
    silently shrank — the unstated-denominator error applied to our own tests.
    """
    covered = {c[0] for c in CASES}
    missing = [s for s in POWER_STATES if s not in covered]
    assert not missing, (
        f"{missing} declared in POWER_STATES but never driven through the "
        f"chain. Add a case; do not narrow the assertion.")
