"""Positive controls for `roster-promotion-evidence-guard` against the REAL config.

The guard's own `--self-test` plants into synthetic fixtures, which proves the
logic. This file plants into a COPY OF THE REAL `config/accounts.yaml` and
asserts the guard fires — the stronger control, and the one
`tests/test_strategy_decision_record.py` set the precedent for.

⚠️ THE POINT OF THE REAL-CONFIG PLANT is that a fixture can be shaped to the
guard. The real file cannot: `alpaca_live` writes its roster as a BLOCK list and
`bybit_1` writes the same field as a FLOW list (`strategies: [a, b, c]`) on one
932-character line. A line-oriented guard would have had to handle both spellings
and the second is the one nobody would have thought to test. Parsing YAML makes
them the same edit, and these tests hold that property down.

Nothing here mutates the repo's config — every plant is applied to a copy in a
tmp dir and the guard is pointed at it.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts/ci/check_roster_promotion_evidence.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_roster_promotion_evidence", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

REAL_ACCOUNTS = (REPO / "config/accounts.yaml").read_text(encoding="utf-8")
REAL_STRATEGIES = yaml.safe_load((REPO / "config/strategies.yaml").read_text(encoding="utf-8"))["strategies"]
EVIDENCE = REPO / "comms/strategy_evidence"


def _with_leg(account: str, leg: str) -> str:
    """The real accounts.yaml with `leg` appended to `account`'s roster."""
    data = yaml.safe_load(REAL_ACCOUNTS)
    data["accounts"][account]["strategies"] = list(
        data["accounts"][account].get("strategies") or []) + [leg]
    return yaml.safe_dump(data)


def _without_legs(account: str, n: int) -> str:
    data = yaml.safe_load(REAL_ACCOUNTS)
    legs = list(data["accounts"][account].get("strategies") or [])
    data["accounts"][account]["strategies"] = legs[:-n] if n else legs
    return yaml.safe_dump(data)


# --------------------------------------------------------------------------
# The real-money accounts are DERIVED, so assert the derivation, not a list.
# --------------------------------------------------------------------------
def test_risk_bearing_accounts_are_derived_from_account_class():
    r = G.rosters(REAL_ACCOUNTS)
    risk = {a for a, c in r.items() if G.is_risk_bearing(c["class"])}
    paper = {a for a, c in r.items() if not G.is_risk_bearing(c["class"])}
    # Every risk-bearing account carries a class that is NOT the one vouched-for
    # value. Asserting the PROPERTY rather than the membership is what keeps this
    # test from going stale the day an account is added.
    assert risk, "no risk-bearing account found — the derivation has broken"
    assert all(r[a]["class"] != "paper" for a in risk)
    assert all(r[a]["class"] == "paper" for a in paper)
    # `prop` is risk-bearing. It is a THIRD funding category and reading the
    # axis as a paper/real_money boolean would silently exempt breakout_1.
    assert "prop" in {r[a]["class"] for a in risk} or "breakout_1" not in r


def test_a_real_money_account_in_dry_run_is_still_risk_bearing():
    """`ib_live` is `account_class: real_money` while `mode: dry_run`.

    Keying on `mode` would let a leg be listed there with no evidence and then be
    armed by a separate `set-account-mode` action that never re-checks the roster.
    """
    r = G.rosters(REAL_ACCOUNTS)
    if "ib_live" not in r:
        pytest.skip("ib_live is no longer configured")
    assert r["ib_live"]["mode"] == "dry_run"
    assert G.is_risk_bearing(r["ib_live"]["class"]) is True


# --------------------------------------------------------------------------
# Positive controls on the real file.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("account", ["alpaca_live", "bybit_2", "breakout_1"])
def test_adding_a_leg_to_a_real_money_roster_is_caught(account, tmp_path):
    """The edit the OLD guard cannot see: no `mode:` / `execution:` line moves."""
    head = _with_leg(account, "tlt_pullback_1d")
    found = G.promotions(REAL_ACCOUNTS, head)
    assert [(f["kind"], f["leg"], f["account"]) for f in found] == [
        ("roster_add", "tlt_pullback_1d", account)]
    findings = G.grade(found, REAL_STRATEGIES, EVIDENCE)
    assert len(findings) == 1, "a leg with no passing record must produce a finding"
    # C3 and C4 are what the whole committed corpus fails on today; naming them
    # here means a change that silently starts passing them fails this test.
    assert "C3 FAIL" in findings[0] and "C4 FAIL" in findings[0]


def test_flow_style_roster_edit_is_caught_the_same_as_block_style():
    """`bybit_1` writes `strategies:` as a FLOW list; `alpaca_live` as a BLOCK list.

    Same edit, two spellings. A line-matching guard sees them differently; this
    one must not.
    """
    r = G.rosters(REAL_ACCOUNTS)
    assert not G.is_risk_bearing(r["bybit_1"]["class"]), "bybit_1 is expected to be paper"
    # The paper side stays silent...
    assert G.promotions(REAL_ACCOUNTS, _with_leg("bybit_1", "tlt_pullback_1d")) == []
    # ...and the identical edit on the real-money side fires. A matched pair, so
    # the silence above is a measurement rather than an absence of a probe.
    assert len(G.promotions(REAL_ACCOUNTS, _with_leg("bybit_2", "tlt_pullback_1d"))) == 1


def test_class_flip_arms_a_whole_existing_roster():
    """One line. `bybit_1` carries 26 legs; moving it to real_money arms them all."""
    data = yaml.safe_load(REAL_ACCOUNTS)
    n = len(data["accounts"]["bybit_1"]["strategies"])
    data["accounts"]["bybit_1"]["account_class"] = "real_money"
    found = G.promotions(REAL_ACCOUNTS, yaml.safe_dump(data))
    assert len(found) == n and all(f["kind"] == "class_flip" for f in found)


def test_unrecognised_account_class_fails_closed():
    data = yaml.safe_load(REAL_ACCOUNTS)
    data["accounts"]["bybit_1"]["account_class"] = "sovereign_wealth_fund"
    found = G.promotions(REAL_ACCOUNTS, yaml.safe_dump(data))
    assert found, "a class the guard has never seen must be treated as risk-bearing"


# --------------------------------------------------------------------------
# Negative controls. DEMOTION STAYS FREE — this is the one that must never break.
# --------------------------------------------------------------------------
def test_removing_legs_from_a_real_money_roster_is_free():
    """The A6-shaped diff: two legs dropped from `alpaca_live`."""
    assert G.promotions(REAL_ACCOUNTS, _without_legs("alpaca_live", 2)) == []


def test_emptying_a_real_money_roster_is_free():
    assert G.promotions(REAL_ACCOUNTS, _without_legs("bybit_2", 99)) == []


def test_moving_an_account_out_of_a_risk_bearing_class_is_free():
    data = yaml.safe_load(REAL_ACCOUNTS)
    data["accounts"]["bybit_2"]["account_class"] = "paper"
    assert G.promotions(REAL_ACCOUNTS, yaml.safe_dump(data)) == []


def test_unchanged_config_is_clean():
    assert G.promotions(REAL_ACCOUNTS, REAL_ACCOUNTS) == []


# --------------------------------------------------------------------------
# The bar must be SATISFIABLE — a gate nothing can pass is a ban.
# --------------------------------------------------------------------------
def test_a_compliant_record_clears_the_bar(tmp_path):
    leg = "tlt_pullback_1d"
    rec = {
        "strategy": leg, "coverage_state": "measured", "harness": "pullback",
        "n_trades_oos": 8,  # the real n for this leg; there is NO floor, by decision
        "cost_stack": {"fees": 7.5, "slippage": 2.0, "funding": 0.0},
        "decision_rule": {"id": "DR-X", "rule": "net_r_oos > 0",
                          "registered_at": "2026-09-01T00:00:00+00:00", "verdict": "pass"},
        "generated_at": "2026-09-10T00:00:00+00:00",
        "config_fingerprint": G.config_fingerprint(REAL_STRATEGIES[leg]),
    }
    (tmp_path / f"{leg}.json").write_text(json.dumps(rec), encoding="utf-8")
    found = G.promotions(REAL_ACCOUNTS, _with_leg("alpaca_live", leg))
    assert G.grade(found, REAL_STRATEGIES, tmp_path) == []


def test_n_equals_eight_passes_because_there_is_no_minimum():
    """The operator was offered a sample-size floor and DECLINED it (2026-09-21).

    `tlt_pullback_1d` reached a real-money roster on n=8. If a later session adds
    a floor, this test fails and the commit has to say the bar was re-opened.
    """
    rec = {"coverage_state": "measured", "harness": "pullback", "n_trades_oos": 8,
           "cost_stack": {"fees": 1.0, "slippage": 1.0, "funding": 0.0},
           "decision_rule": {"id": "d", "rule": "r", "registered_at": "2026-01-01",
                             "verdict": "pass"},
           "generated_at": "2026-02-01"}
    assert [ok for _, ok, _ in G.clause_verdicts(rec)] == [True, True, True, True]


# --------------------------------------------------------------------------
# The fingerprint must agree with the PRODUCER's, or identity is meaningless.
# --------------------------------------------------------------------------
def test_fingerprint_agrees_with_every_committed_record():
    """52 committed records, recomputed from the live config. Any drift is STALE.

    This also cross-checks the guard's re-implementation of
    `build_strategy_evidence.py::config_fingerprint` against artifacts that
    producer actually wrote — a second author, not a second call.
    """
    checked = mismatched = 0
    for path in sorted(EVIDENCE.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        leg = rec.get("strategy")
        if leg not in REAL_STRATEGIES:
            continue
        checked += 1
        if rec.get("config_fingerprint") != G.config_fingerprint(REAL_STRATEGIES[leg]):
            mismatched += 1
    assert checked > 0, "no committed record could be checked — the probe is vacuous"
    assert mismatched == 0, f"{mismatched}/{checked} committed records are STALE"


def test_population_census_is_computable_and_reports_its_denominator():
    rep = G.census(REAL_ACCOUNTS, REAL_STRATEGIES, EVIDENCE)
    assert rep["roster_slots"] == rep["passing"] + rep["failing"]
    assert rep["roster_slots"] > 0, "a census over zero slots would be vacuous"
    assert rep["risk_bearing_accounts"], "the derivation found no risk-bearing account"
