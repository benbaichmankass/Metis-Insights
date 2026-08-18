"""`scripts/ops/regime_policy_coverage.py` — the gate's denominator.

The audit exists because the regime gate writes an audit row ONLY when it
refuses, so its reach is unobservable from the audit stream. These tests pin
the two properties that make the number trustworthy: the population is stated,
and the cell grading is DELEGATED to the live gate rather than copied.
"""
import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "regime_policy_coverage", REPO / "scripts/ops/regime_policy_coverage.py")
rpc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rpc)


def test_self_test_passes():
    assert rpc.main(["--self-test"]) == 0


# --- the bug this script was born with --------------------------------------

def test_yaml_boolean_off_is_graded_as_an_off_cell():
    """PyYAML resolves the YAML 1.1 literal `off` to Python False.

    The first draft compared `str(value).lower() == "off"`, never matched, and
    reported 0 governed legs out of 47 — wrong in the reassuring direction. The
    live gate is correct (`value is False or value == "off"`); this pins that
    the audit now asks the gate instead of re-deciding.
    """
    pol = {"chop": {"a": {"long": False, "short": True}}}
    assert rpc._off_sides(pol, "chop", "a") == ["long"]


def test_the_literal_string_off_is_graded_the_same_way():
    """A hand-edited quoted `"off"` must mean what the bare literal means."""
    pol = {"chop": {"a": {"long": "off", "short": "on"}}}
    assert rpc._off_sides(pol, "chop", "a") == ["long"]


def test_grading_is_delegated_not_reimplemented():
    """Guards against a future re-introduction of the local copy.

    A second implementation of a decision predicate is the drift shape this
    repo keeps paying for; the whole fix was to collapse the two.

    Scans the function's CODE, not its source text: the docstring explains the
    bug and necessarily quotes `== "off"`, so a raw substring check would trip
    on the explanation. A guard that is cheaper to trip than to satisfy gets
    silenced by deleting the explanation, which is the worst outcome available.
    """
    import ast
    tree = ast.parse((REPO / "scripts/ops/regime_policy_coverage.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_off_sides")
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)
                           and isinstance(fn.body[0].value.value, str)) else fn.body
    literals = {n.value for b in body for n in ast.walk(b)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "off" not in literals, (
        "_off_sides compares a cell value itself again — ask the gate instead")
    calls = {ast.unparse(n.func) for b in body for n in ast.walk(b)
             if isinstance(n, ast.Call)}
    assert "_evaluate_trend_cell" in calls


def test_delegation_agrees_with_the_gate_on_every_cell_value():
    """Same inputs, same answer — checked, not asserted."""
    from src.runtime.regime.policy import _evaluate_trend_cell
    for value in (False, True, "off", "on", 0.5, None, "wat"):
        pol = {"chop": {"a": {"long": value, "short": "on"}}}
        gate = bool(_evaluate_trend_cell(
            strategy="a", side="long", regime="chop", policy=pol).get("gated"))
        assert ("long" in rpc._off_sides(pol, "chop", "a")) is gate, value


# --- population -------------------------------------------------------------

def test_population_is_live_legs_only():
    doc = {"strategies": {
        "live_on": {"enabled": True, "execution": "live"},
        "shadow": {"enabled": True, "execution": "shadow"},
        "disabled": {"enabled": False, "execution": "live"},
        "default_exec": {"enabled": True},          # execution defaults to live
        "not_a_dict": "nope",
    }}
    assert sorted(rpc.live_legs(doc)) == ["default_exec", "live_on"]


def test_trend_vol_mention_does_not_count_as_coverage():
    """The 2-D block is a separate axis with its own enforce preconditions."""
    pol = {"trend_vol": {"chop": {"calm": {"c": {"long": False}}}}}
    assert rpc.cells_for(pol, "c") == {}


def test_verdicts_keep_listed_open_apart_from_unlisted():
    """Same runtime behaviour, different facts: measured-and-allowed vs never
    measured. Collapsing them is the whole thing the audit exists to prevent."""
    pol = {"chop": {"listed": {"long": True, "short": True}}}
    doc = {"strategies": {"listed": {"enabled": True, "execution": "live"},
                          "absent": {"enabled": True, "execution": "live"}}}
    got = {r["strategy"]: r["verdict"] for r in rpc.audit(doc, pol)["rows"]}
    assert got == {"listed": rpc.LISTED_OPEN, "absent": rpc.UNLISTED}


def test_orphan_policy_keys_are_surfaced_and_not_counted():
    pol = {"chop": {"a": {"long": False}, "retired": {"long": False}}}
    doc = {"strategies": {"a": {"enabled": True, "execution": "live"}}}
    rep = rpc.audit(doc, pol)
    assert rep["orphan_policy_keys"] == ["retired"]
    assert rep["live_legs"] == 1


# --- the committed configuration --------------------------------------------

def test_committed_config_reports_a_real_and_stated_denominator():
    """Not a threshold assertion — a tripwire on the SHAPE of the finding.

    The measured state on 2026-08-18 is 3 governed / 1 listed_open / 43
    unlisted of 47 live legs. If a future session authors cells, this should be
    updated with the new measurement; what must never happen is the counts
    silently ceasing to be reported.
    """
    import yaml
    doc = yaml.safe_load((REPO / "config/strategies.yaml").read_text())
    pol = yaml.safe_load((REPO / "config/regime_policy.yaml").read_text())
    rep = rpc.audit(doc, pol)
    assert rep["live_legs"] == sum(rep["counts"].values())
    assert rep["counts"][rpc.GOVERNED] >= 1, (
        "zero governed legs means the gate can refuse nothing — either the "
        "table emptied, or the grading regressed to the string-compare bug")
    assert rep["counts"][rpc.UNLISTED] > 0, (
        "an all-covered fleet would be a genuine change worth re-reading, not "
        "a silent pass")
