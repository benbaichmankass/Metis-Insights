"""PI-20261004-FOJGFIZF-0004: a harness-dispatch result for a queue unit must
carry the verdict of THAT unit's pre-registered rule, or `wiring_only` (which
the grader refuses to close on) -- never the bare E7 `no_action_warranted`."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import harness_dispatch_result as h  # noqa: E402
import queue_grade as qg  # noqa: E402
from research_result import build, validate  # noqa: E402

RULE = ("PASS IF net_total_r > 0 AND n_trades >= 39 -- Stage-0 evidence only. "
        "FAIL IF net_total_r <= 0 with n_trades >= 39 -- re-queues for a "
        "confirmatory run. indeterminate IF n_trades < 39.")
UNIT = {"id": "RQ-20300101-001",
        "decision_rule": {"id": "RULE-T-39", "registered_at": "2030-01-01", "rule": RULE}}
OOS_UNIT = {"id": "RQ-20300101-002",
            "decision_rule": {"id": "RULE-TPL-OOS", "registered_at": "2030-01-01",
                              "rule": "PASS IF net-of-cost OOS R > 0 with n >= 39. FAIL IF "
                                      "net-of-cost OOS R <= 0 with n >= 39. indeterminate IF n < 39."}}


def _run(unit, net, n, uid="RQ-20300101-001"):
    return h.derive_full(harness="pairs", symbol="SPY", timeframe="1h",
                         build_result="success",
                         result={"total_trades": n, "net_total_r": net},
                         research_unit=uid, unit=unit)


@pytest.mark.parametrize("net,n,want", [
    (-121.96, 178, "fail"),       # planted: net<0, n>=N
    (0.0, 39, "fail"),            # rule says <= 0
    (18.72, 144, "pass"),         # planted: net>0, n>=N
    (5.0, 10, "indeterminate"),   # planted: n<N
    (-5.0, 38, "indeterminate"),
])
def test_unit_rule_applied(net, n, want):
    out = _run(UNIT, net, n)
    assert out[0] == want and out[1] == "measured" and out[3] == n
    assert out[6] == "RULE-T-39" and out[7] == "2030-01-01"


@pytest.mark.parametrize("unit", [OOS_UNIT, None,
                                  {"id": "X", "decision_rule": {"rule": "PASS if good"}}])
def test_unparseable_is_wiring_only(unit):
    out = _run(unit, -10.0, 100)
    assert out[0] == h.WIRING_ONLY and out[1] == "measured"
    assert out[6] == h.DECISION_RULE_ID


def test_bare_e7_dispatch_unchanged():
    out = h.derive_full(harness="squeeze", symbol="BTCUSDT", timeframe="1h",
                        build_result="success", result={"total_trades": 5, "net_total_r": -1})
    assert out[0] == "no_action_warranted" and out[6] == h.DECISION_RULE_ID


def test_parse_rule_floors():
    assert h.parse_rule(RULE) == 39
    assert h.parse_rule(RULE.replace("39", "20")) == 20
    assert h.parse_rule(RULE.replace("< 39", "< 40")) is None
    assert h.parse_rule(RULE.split("indeterminate")[0]) is None
    assert h.parse_rule(None) is None


def test_real_units_parse():
    """The three units the finding names carry the parseable shape on main."""
    floors = {u: h.parse_rule(h.load_unit(u)["decision_rule"]["rule"])
              for u in ("RQ-20260929-105", "RQ-20260929-106", "RQ-20260929-107")}
    assert floors == {"RQ-20260929-105": 20, "RQ-20260929-106": 39, "RQ-20260929-107": 39}


@pytest.mark.parametrize("unit,net,n", [(UNIT, -1.0, 50), (UNIT, 1.0, 3), (OOS_UNIT, 1.0, 50)])
def test_records_admissible(unit, net, n):
    v, rs, pop, nn, meas, note, rid, rat = _run(unit, net, n)
    rec = build(research_unit="RQ-20300101-001", decision_rule_id=rid,
                decision_rule_registered_at=rat, verdict=v, read_state=rs,
                population_description=pop, n=nn, workflow="w", run_id="1",
                commit_sha="abc", tool="t", measurement=meas,
                artifact_store="research/results/", artifact_locator="x", note=note)
    assert validate(rec) == []


def test_wiring_only_refused_when_not_measured():
    rec = build(research_unit=None, decision_rule_id="R", decision_rule_registered_at="2030-01-01",
                verdict="wiring_only", read_state="producer_failed", population_description="p",
                n=None, workflow="w", run_id="1", commit_sha="abc", tool="t", measurement={},
                artifact_store="research/results/", artifact_locator="x", note="")
    assert validate(rec)


def test_grader_does_not_close_on_wiring_only():
    row = ("research/results/U/1.jsonl", {"verdict": "wiring_only", "read_state": "measured"})
    g = qg.grade_e5([row], 1)
    assert g["verdict"] is None
    d = qg.decide({"cadence": "once", "decision_rule": {"id": "R"}, "generated": {}}, g, "2030-01-01")
    assert d["status"] is None and d["grading"]["needs_review"] is True


# ── lane RQ-FIX (2026-10-06): a missing PyYAML is the RUNNER's defect ─────────
# MEASURED: 8 of 358 result rows across 6 units landed wiring_only with the note
# "queue file is missing/unreadable" while the file was present at every commit --
# the result job of research-harness-dispatch.yml never installed PyYAML and
# load_unit's blanket except turned the ImportError into "unit unreadable".

def _no_yaml(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "yaml":
            raise ImportError("No module named 'yaml'")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)


def test_a_missing_yaml_dependency_raises_instead_of_reading_as_an_unreadable_unit(tmp_path, monkeypatch):
    (tmp_path / "RQ-20300101-001.yaml").write_text("id: RQ-20300101-001\n")
    _no_yaml(monkeypatch)
    with pytest.raises(h.UnitLoaderUnavailable) as exc:
        h.load_unit("RQ-20300101-001", tmp_path)
    assert "PyYAML" in str(exc.value) and "RQ-20300101-001" in str(exc.value)


def test_main_exits_nonzero_and_lands_nothing_without_yaml(tmp_path, monkeypatch, capsys):
    (tmp_path / "RQ-20300101-001.yaml").write_text("id: RQ-20300101-001\n")
    (tmp_path / "result.json").write_text('{"total_trades": 397, "net_total_r": -5.4539}')
    monkeypatch.setattr(h, "QUEUE_DIR", tmp_path)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    _no_yaml(monkeypatch)
    rc = h.main(["--harness", "pullback", "--research-unit", "RQ-20300101-001",
                 "--result-json", str(tmp_path / "result.json"), "--out-dir", str(tmp_path / "rr")])
    assert rc == 2
    assert "::error::" in capsys.readouterr().out
    assert not (tmp_path / "rr" / "measurement.json").exists()


def test_an_absent_or_malformed_unit_file_is_still_wiring_only_not_a_crash(tmp_path, capsys):
    assert h.load_unit("RQ-20300101-009", tmp_path) is None            # no file
    (tmp_path / "RQ-20300101-001.yaml").write_text("id: [unclosed\n")  # malformed
    assert h.load_unit("RQ-20300101-001", tmp_path) is None
    assert "::warning::" in capsys.readouterr().out


def test_with_yaml_present_the_real_unit_reads_and_grades():
    """Positive control over the unit the double-fire measured: present on main,
    mechanical rule, and its landed measurement grades FAIL under it."""
    unit = h.load_unit("RQ-20261006-005")
    assert isinstance(unit, dict) and h.parse_rule(unit["decision_rule"]["rule"]) == 39
    v, rid, _reg, _note = h.apply_unit_rule(unit, {"total_trades": 397, "net_total_r": -5.4539})
    assert (v, rid) == ("fail", "RULE-RQ1006-005-XRPUSDT-1h-PULLBACK-STAGE0")


def test_the_result_job_installs_pyyaml_before_deriving():
    import yaml
    wf = yaml.safe_load((REPO / ".github/workflows/research-harness-dispatch.yml").read_text())
    steps = wf["jobs"]["research_result"]["steps"]
    install = [i for i, s in enumerate(steps) if "pyyaml" in str(s.get("run", "")).lower()
               and "pip install" in str(s.get("run", ""))]
    derive = [i for i, s in enumerate(steps) if "harness_dispatch_result.py" in str(s.get("run", ""))]
    assert install and derive and install[0] < derive[0], "pyyaml must be installed before the derive step"


def test_the_grader_lets_a_regraded_row_supersede_its_wiring_only_original():
    """The re-grade APPENDS (never edits), so {wiring_only, fail} would otherwise read
    'not unanimous' forever. Same run_id -> superseded; a different run's wiring_only
    row still blocks."""
    wo = {"verdict": "wiring_only", "read_state": "measured", "produced_by": {"run_id": "1"}}
    fail = {"verdict": "fail", "read_state": "measured", "produced_by": {"run_id": "1"}}
    g = qg.grade_e5([("f/1.jsonl", wo), ("f/1.jsonl", fail)], 1)
    assert g["verdict"] == "fail" and "superseded" in g["reason"], g
    other = {"verdict": "wiring_only", "read_state": "measured", "produced_by": {"run_id": "2"}}
    g2 = qg.grade_e5([("f/1.jsonl", wo), ("f/1.jsonl", fail), ("f/2.jsonl", other)], 1)
    assert g2["verdict"] is None and "not unanimous" in g2["reason"], g2
