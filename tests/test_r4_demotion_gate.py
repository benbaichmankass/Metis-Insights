"""scripts/ops/r4_demotion_gate.py — R4 enforcing as the Stage-2 DEMOTION gate.

Positive controls first (a gate that never demoted would pass every HOLD test),
then every HOLD / REFUSE path, then the roster text edit against the REAL
config/accounts.yaml, whose rosters come in both flow and block form.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import r4_demotion_gate as g  # noqa: E402

LEG = "ada_pullback_2h"
OTHER = "xrp_pullback_2h"
EQ = "ief_pullback_1d"

ACCOUNTS_TEXT = f"""\
accounts:
  bybit_1:
    exchange: bybit
    account_class: paper
    strategies: [{LEG}, {OTHER}]
  bybit_2:
    exchange: bybit
    account_class: real_money
    strategies: [{OTHER}, {LEG}]   # the live roster
  bybit_portfolio:
    exchange: bybit
    account_class: paper
    paper_role: portfolio
    strategies: [{OTHER}, {LEG}]
  alpaca_paper:
    exchange: alpaca
    account_class: paper
    strategies:
      - {EQ}              # paper soak
  alpaca_live:
    exchange: alpaca
    account_class: real_money
    strategies:
      - {EQ}      # real money
  alpaca_portfolio:
    exchange: alpaca
    account_class: paper
    paper_role: portfolio
    strategies:
      - {EQ}
"""

MANDATE = {"id": "MD-DEMOTE-S2-S1", "grants": "x", "direction": "derisk_only",
           "granted_by": "operator", "granted_at": "2026-09-24"}


def _stats(n=25, usd=-50.0, cov=0.8, r=-4.0):
    return {"trades": n, "totalPnlMeasured": usd, "totalPnl": usd, "pnlCoverage": cov,
            "pnlMeasuredCount": int(n * cov), "totalR": r, "rTradeCount": n}


def _perf(real=None, mirror=None):
    return {"window": "30d", "since": "2026-08-26T00:00:00+00:00",
            "perStrategy": [{"name": k, **v} for k, v in (real or {}).items()],
            "paperPortfolio": {"perStrategy": [{"name": k, **v} for k, v in (mirror or {}).items()]}}


@pytest.fixture
def root(tmp_path):
    def make(accounts_text=ACCOUNTS_TEXT, mandates=None, proposed=None):
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "config" / "accounts.yaml").write_text(accounts_text)
        (tmp_path / "config" / "strategies.yaml").write_text("strategies: {}\n")
        (tmp_path / "config" / "mandates.yaml").write_text(yaml.safe_dump(
            {"mandates": [MANDATE] if mandates is None else mandates, "proposed": proposed or []}))
        return tmp_path
    return make


def _roster(root, acct):
    return yaml.safe_load((root / "config/accounts.yaml").read_text())["accounts"][acct]["strategies"]


# ── positive controls ──────────────────────────────────────────────────────
def test_apply_demotes_a_would_block_leg_from_live_and_mirror(root):
    r = root()
    out = g.run(_perf(real={LEG: _stats()}), root=r, window="30d", apply=True)
    assert out["demoted"] == [{"leg": LEG, "accounts": ["bybit_2", "bybit_portfolio"]}]
    assert _roster(r, "bybit_2") == [OTHER]
    assert _roster(r, "bybit_2") == _roster(r, "bybit_portfolio")   # mirror equality holds
    assert LEG in _roster(r, "bybit_1")                              # back on the soak book
    rec = json.loads((r / f"comms/mandate_evidence/mirror_window/{LEG}.json").read_text())
    assert rec["net_r_net_of_full_cost"] == -4.0 and rec["n_closed"] == 25
    assert (r / rec["source_run"]).is_file()
    firing = json.loads(next((r / "comms/mandate_firings").glob("*.json")).read_text())
    assert firing["action"] == "remove" and firing["leg"] == LEG
    # untouched lines stay byte-identical, including the trailing comment
    assert "# the live roster" in (r / "config/accounts.yaml").read_text()


def test_mirror_source_carries_the_call_when_real_money_abstains(root):
    r = root()
    out = g.run(_perf(real={LEG: _stats(n=3)}, mirror={LEG: _stats(n=30, r=-2.0)}),
                root=r, window="30d", apply=True)
    assert [d["leg"] for d in out["demoted"]] == [LEG]
    rec = json.loads((r / f"comms/mandate_evidence/mirror_window/{LEG}.json").read_text())
    assert rec["chosen_source"] == "mirror" and rec["net_r_net_of_full_cost"] == -2.0


def test_block_form_roster_on_alpaca_live(root):
    r = root()
    out = g.run(_perf(real={EQ: _stats()}), root=r, window="30d", apply=True)
    assert out["demoted"] == [{"leg": EQ, "accounts": ["alpaca_live", "alpaca_portfolio"]}]
    assert not _roster(r, "alpaca_live") and not _roster(r, "alpaca_portfolio")
    assert _roster(r, "alpaca_paper") == [EQ]
    assert "# real money" in (r / "config/accounts.yaml").read_text()   # prose kept


def test_dry_run_reports_the_demotion_and_writes_nothing(root):
    r = root()
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_perf(real={LEG: _stats()}), root=r, window="30d", apply=False)
    assert out["mode"] == "dry_run" and [d["leg"] for d in out["demoted"]] == [LEG]
    assert (r / "config/accounts.yaml").read_text() == before
    assert not (r / "comms").exists() and out["written"] == []


# ── HOLD: R4 does not support a cut ────────────────────────────────────────
@pytest.mark.parametrize("stats,why", [
    (None, "abstain_thin"),
    (_stats(n=19), "abstain_thin"),
    (_stats(cov=0.59), "abstain_unverified"),
    (_stats(usd=0.0, r=-1.0), "pass"),
    (_stats(usd=-5.0, r=0.0), "not negative"),
    (_stats(usd=-5.0, r=None), "not stated"),
])
def test_hold_paths_never_touch_the_roster(root, stats, why):
    r = root()
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_perf(real={LEG: stats} if stats else {}), root=r, window="30d", apply=True)
    dec = next(d for d in out["legs"] if d["leg"] == LEG)
    assert dec["action"] == g.HOLD and why in dec["why"]
    assert out["demoted"] == [] and (r / "config/accounts.yaml").read_text() == before
    assert not (r / "comms").exists()


def test_only_stage2_legs_are_evaluated(root):
    out = g.run(_perf(real={"some_soak_only_leg": _stats()}), root=root(), window="30d", apply=False)
    assert {d["account"] for d in out["legs"]} <= {"bybit_2", "alpaca_live", "ib_live"}
    assert "some_soak_only_leg" not in {d["leg"] for d in out["legs"]}


# ── REFUSE: the resolver decides, and a refusal leaves the roster alone ────
def test_refused_when_the_mandate_is_only_proposed(root):
    r = root(mandates=[], proposed=[MANDATE])
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_perf(real={LEG: _stats()}), root=r, window="30d", apply=True)
    assert out["demoted"] == [] and out["refused"][0]["clause"] == "R-MANDATE-NOT-GRANTED"
    assert (r / "config/accounts.yaml").read_text() == before


def test_refused_when_the_cut_would_add_to_the_soak_book(root):
    text = ACCOUNTS_TEXT.replace(f"strategies: [{LEG}, {OTHER}]", f"strategies: [{OTHER}]", 1)
    r = root(accounts_text=text)
    out = g.run(_perf(real={LEG: _stats()}), root=r, window="30d", apply=True)
    assert out["refused"][0]["clause"] == "R-DERISK-ADD"
    assert LEG in _roster(r, "bybit_2")


def test_apply_proposal_refuses_any_addition(root):
    with pytest.raises(ValueError, match="ADDS"):
        g.apply_proposal(root(), {"roster_add": {"bybit_1": [LEG]}, "roster_remove": {}}, LEG)


# ── the text edit ──────────────────────────────────────────────────────────
def test_remove_refuses_an_ambiguous_or_absent_edit():
    with pytest.raises(ValueError):
        g.remove_from_roster(ACCOUNTS_TEXT, "bybit_2", "not_a_leg")
    with pytest.raises(ValueError):
        g.remove_from_roster(ACCOUNTS_TEXT, "no_such_account", LEG)


def test_remove_does_not_reach_into_the_next_account():
    out = g.remove_from_roster(ACCOUNTS_TEXT, "alpaca_live", EQ)
    parsed = yaml.safe_load(out)["accounts"]
    assert not parsed["alpaca_live"]["strategies"]
    assert parsed["alpaca_portfolio"]["strategies"] == [EQ]
    assert parsed["alpaca_paper"]["strategies"] == [EQ]


def test_every_real_stage2_leg_can_be_cut_exactly_from_the_real_file():
    """The live accounts.yaml mixes flow and block rosters with heavy comments.
    For every leg on a real Stage-2 roster, cutting it from its account AND its
    mirror must change only those two rosters."""
    text = (REPO / "config/accounts.yaml").read_text()
    base = yaml.safe_load(text)["accounts"]
    legs = g.stage2_legs(REPO)
    assert legs, "no Stage-2 legs found -- the probe must see a positive"
    for acct, leg in legs:
        mirror = g.mr.MIRROR_OF.get(acct)
        edited = g.remove_from_roster(text, acct, leg)
        if mirror and leg in (base[mirror].get("strategies") or []):
            edited = g.remove_from_roster(edited, mirror, leg)
        after = yaml.safe_load(edited)["accounts"]
        for name, cfg in base.items():
            want = list(cfg.get("strategies") or [])
            if name == acct or (name == mirror and leg in want):
                want.remove(leg)
            assert list(after[name].get("strategies") or []) == want, (acct, leg, name)


def test_cli_dry_run_exit_zero_and_bad_payload_exit_two(tmp_path, capsys):
    p = tmp_path / "perf.json"
    p.write_text(json.dumps(_perf()))
    assert g.main(["--perf-json", str(p)]) == 0
    p.write_text("{}")
    assert g.main(["--perf-json", str(p)]) == 2
