"""What `hyro_fit_verdict.py` writes must be accepted by the research-result collector.

RQ-20260930-501 graded PASS on 2026-09-30 but its verdict.json said read_state "graded", which is not in the
contract's closed set, so `script_run.derive_record` turned a measured PASS into producer_failed / n=null. These
tests run the REAL derive_record over what the script produces, so that class cannot recur silently.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    s = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


hfv = _load("hfv_contract", "scripts/research/hyro_fit_verdict.py")
sr = _load("script_run_contract", "scripts/research/script_run.py")
LEDGERS = [REPO / f"comms/strategy_evidence/runs/2026-09-25/ict_scalp_{c}_15m__trades.jsonl"
           for c in ("eth", "sol", "xrp")]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in LEDGERS), reason="committed ledgers unavailable")


def derive(tmp_path, verdict):
    out = Path("out")
    (tmp_path / out).mkdir(parents=True, exist_ok=True)
    (tmp_path / out / "verdict.json").write_text(json.dumps(verdict))
    (tmp_path / out / "run-manifest.json").write_text(json.dumps(
        {"all_ok": True, "commands": [{"index": 0, "argv": ["python3", "x.py"], "exit_code": 0}]}))
    plan = SimpleNamespace(unit="RQ-20260930-501", out_dir=out, rule_id="RULE-X", rule_registered_at="2026-09-30")
    return sr.derive_record(plan, repo=tmp_path)


def test_a_graded_pass_or_fail_lands_as_measured_with_an_integer_n(tmp_path):
    spec = hfv.spec_from_ledgers("book", [str(p) for p in LEDGERS])
    v = hfv.grade(spec, "config/prop_rulesets/hyrotrader.yaml")
    rec = derive(tmp_path, v)
    assert rec["read_state"] == "measured" and rec["verdict"] == v["verdict"] in ("pass", "fail")
    assert rec["n"] == str(spec["n"]) and "producer_failed" not in rec.get("note", "")


def test_an_underpowered_run_is_measured_indeterminate_not_a_malformed_record(tmp_path):
    spec = hfv.spec_from_ledgers("eth only", [str(LEDGERS[0])])          # 117 < 300
    v = hfv.grade(spec, "config/prop_rulesets/hyrotrader.yaml")
    rec = derive(tmp_path, v)
    assert (rec["read_state"], rec["verdict"]) == ("measured", "indeterminate")
    assert "underpowered" in rec["note"]


def test_a_producer_failure_has_null_n_and_lands_as_producer_failed(tmp_path):
    v = dict(verdict="not_applicable", read_state="producer_failed", n=None, leg="x", population="no ledger")
    rec = derive(tmp_path, v)
    assert (rec["read_state"], rec["verdict"], rec["n"]) == ("producer_failed", "not_applicable", "null")
    assert "malformed" not in rec.get("note", "")


def test_the_old_words_are_still_refused_by_the_collector(tmp_path):
    rec = derive(tmp_path, dict(verdict="pass", read_state="graded", n=3, population="x"))
    assert rec["read_state"] == "producer_failed" and "malformed" in rec["note"]
