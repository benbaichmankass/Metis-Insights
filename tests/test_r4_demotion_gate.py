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


#: A healthy Stage-0 record: mean +0.30R/trade, so the 20-trade p10 is
#: negative (about -3.9R) -- the shape of the frequent bybit_2 legs.
HEALTHY_RECORD = [3.0, -1.0, -1.0, -1.0, 1.5] * 8          # 40 trades
#: trend_donchian_xrp_4h's shape: a high mean, so its p10 is POSITIVE.
HIGH_MEAN_RECORD = [4.0, 2.0, 1.0, -1.0] * 6               # 24 trades, mean +1.5R


def _stats(n=20, usd=-50.0, cov=0.8, r=-12.0):
    return {"trades": n, "totalPnlMeasured": usd, "totalPnl": usd, "pnlCoverage": cov,
            "pnlMeasuredCount": int(n * cov), "totalR": r, "rTradeCount": n}


def _book(legs):
    """{leg: [newest-window stats, previous-window stats]} -> recentBlocks book."""
    return {leg: {"available": sum((b or {}).get("trades", 0) for b in blocks), "blocks": blocks}
            for leg, blocks in (legs or {}).items()}


def _perf(real=None, mirror=None):
    return {"window": "all", "since": None, "perStrategy": [],
            "paperPortfolio": {"perStrategy": []},
            "recentBlocks": {"blockSize": 20, "blocks": 2,
                             "real": _book(real), "mirror": _book(mirror)}}


def _record(root, leg, net_rs):
    run = f"comms/strategy_evidence/runs/test/{leg}__trades.jsonl"
    (root / run).parent.mkdir(parents=True, exist_ok=True)
    (root / run).write_text("".join(json.dumps({"net_r": x}) + "\n" for x in net_rs))
    (root / f"comms/strategy_evidence/{leg}.json").write_text(
        json.dumps({"strategy": leg, "source_run": run}))


@pytest.fixture
def root(tmp_path):
    def make(accounts_text=ACCOUNTS_TEXT, mandates=None, proposed=None, records=None):
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "config" / "accounts.yaml").write_text(accounts_text)
        (tmp_path / "config" / "strategies.yaml").write_text("strategies: {}\n")
        (tmp_path / "config" / "mandates.yaml").write_text(yaml.safe_dump(
            {"mandates": [MANDATE] if mandates is None else mandates, "proposed": proposed or []}))
        for leg, xs in ({LEG: HEALTHY_RECORD, OTHER: HEALTHY_RECORD, EQ: HEALTHY_RECORD}
                        if records is None else records).items():
            _record(tmp_path, leg, xs)
        return tmp_path
    return make


def _roster(root, acct):
    return yaml.safe_load((root / "config/accounts.yaml").read_text())["accounts"][acct]["strategies"]


def _no_mirror_record(r):
    return not (r / "comms/mandate_evidence").exists()


BROKEN = [_stats(r=-6.0), _stats(r=-6.0)]          # a planted -0.3R/trade leg, both windows


# ── the threshold ──────────────────────────────────────────────────────────
def test_threshold_is_the_records_own_p10_with_a_fixed_seed(root):
    r = root()
    t1, t2 = g.leg_threshold(LEG, r), g.leg_threshold(LEG, r)
    assert t1 == t2 and t1["seed"] == g.P10_SEED and t1["n_record_trades"] == 40
    assert -6.0 < t1["p10"] < -2.0, t1                  # healthy record -> negative p10
    hi = g.leg_threshold(LEG, root(records={LEG: HIGH_MEAN_RECORD}))
    assert hi["p10"] > 0, hi                            # high-mean record -> POSITIVE p10


# ── positive controls ──────────────────────────────────────────────────────
def test_planted_broken_leg_is_demoted_after_two_windows(root):
    r = root()
    out = g.run(_perf(real={LEG: BROKEN}), root=r, window="last20x2", apply=True)
    assert out["demoted"] == [{"leg": LEG, "accounts": ["bybit_2", "bybit_portfolio"]}]
    assert _roster(r, "bybit_2") == [OTHER]
    assert _roster(r, "bybit_2") == _roster(r, "bybit_portfolio")   # mirror equality holds
    assert LEG in _roster(r, "bybit_1")                              # back on the soak book
    rec = json.loads((r / f"comms/mandate_evidence/mirror_window/{LEG}.json").read_text())
    assert rec["net_r_net_of_full_cost"] == -6.0 and rec["n_closed"] == 20
    assert (r / rec["source_run"]).is_file()
    firing = json.loads(next((r / "comms/mandate_firings").glob("*.json")).read_text())
    assert firing["action"] == "remove" and firing["leg"] == LEG
    assert "# the live roster" in (r / "config/accounts.yaml").read_text()


def test_one_bad_window_alone_does_not_demote(root):
    r = root()
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_perf(real={LEG: [_stats(r=-6.0), _stats(usd=40.0, r=5.0)]}), root=r,
                window="last20x2", apply=True)
    dec = next(d for d in out["legs"] if d["leg"] == LEG)
    assert dec["action"] == g.HOLD and "w1" in dec["why"]
    assert out["demoted"] == [] and (r / "config/accounts.yaml").read_text() == before


def test_mirror_window_record_carries_the_threshold_provenance(root):
    r = root()
    g.run(_perf(real={LEG: BROKEN}), root=r, window="last20x2", apply=True)
    rec = json.loads((r / f"comms/mandate_evidence/mirror_window/{LEG}.json").read_text())
    assert rec["trigger_rule"] == g.TRIGGER_RULE == g.mr.DEMOTE_TRIGGER_RULE
    assert rec["p10_threshold"] == g.leg_threshold(LEG, r)["p10"]
    assert rec["threshold_seed"] == g.P10_SEED and rec["threshold_draws"] == g.P10_DRAWS
    assert rec["threshold_source_record"] == f"comms/strategy_evidence/{LEG}.json"
    assert rec["threshold_source_run"].endswith(f"{LEG}__trades.jsonl")
    assert [w["net_r"] for w in rec["windows"]] == [-6.0, -6.0] and rec["block_size"] == 20


def test_mirror_source_carries_the_call_when_real_money_abstains(root):
    r = root()
    out = g.run(_perf(real={LEG: [_stats(n=3), _stats(n=3)]}, mirror={LEG: BROKEN}),
                root=r, window="last20x2", apply=True)
    assert [d["leg"] for d in out["demoted"]] == [LEG]
    rec = json.loads((r / f"comms/mandate_evidence/mirror_window/{LEG}.json").read_text())
    assert rec["chosen_source"] == "mirror"


def test_block_form_roster_on_alpaca_live(root):
    r = root()
    out = g.run(_perf(real={EQ: BROKEN}), root=r, window="last20x2", apply=True)
    assert out["demoted"] == [{"leg": EQ, "accounts": ["alpaca_live", "alpaca_portfolio"]}]
    assert not _roster(r, "alpaca_live") and not _roster(r, "alpaca_portfolio")
    assert _roster(r, "alpaca_paper") == [EQ]
    assert "# real money" in (r / "config/accounts.yaml").read_text()   # prose kept


def test_dry_run_reports_the_demotion_and_writes_nothing(root):
    r = root()
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_perf(real={LEG: BROKEN}), root=r, window="last20x2", apply=False)
    assert out["mode"] == "dry_run" and [d["leg"] for d in out["demoted"]] == [LEG]
    assert (r / "config/accounts.yaml").read_text() == before
    assert _no_mirror_record(r) and out["written"] == []


# ── HOLD / ABSTAIN: the evidence does not support a cut ────────────────────
@pytest.mark.parametrize("blocks,why", [
    ([_stats(usd=40.0, r=5.0), _stats(usd=30.0, r=3.0)], "HOLD"),             # healthy
    ([_stats(cov=0.59), _stats(cov=0.59)], "abstain_unverified"),
    ([_stats(usd=0.0, r=-6.0), _stats(r=-6.0)], "pass"),                     # USD not negative
    ([_stats(r=-2.0), _stats(r=-6.0)], "not below own p10"),                 # above p10
    ([_stats(r=None), _stats(r=-6.0)], "not stated"),
])
def test_hold_paths_never_touch_the_roster(root, blocks, why):
    r = root()
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_perf(real={LEG: blocks}), root=r, window="last20x2", apply=True)
    dec = next(d for d in out["legs"] if d["leg"] == LEG)
    assert dec["action"] == g.HOLD and why in dec["why"], dec["why"]
    assert out["demoted"] == [] and (r / "config/accounts.yaml").read_text() == before
    assert _no_mirror_record(r)


def test_fewer_than_forty_trades_abstains_and_is_counted(root):
    r = root()
    out = g.run(_perf(real={LEG: [_stats(r=-6.0)]}), root=r, window="last20x2", apply=True)
    dec = next(d for d in out["legs"] if d["leg"] == LEG)
    assert dec["action"] == g.HOLD and dec["abstain"] == g.ABSTAIN_THIN
    assert LEG in out["abstained"][g.ABSTAIN_THIN] and out["demoted"] == []


@pytest.mark.parametrize("records", [
    {},                                              # no record at all
    {LEG: HEALTHY_RECORD[:7]},                       # ief-shaped: 7 trades < 20
])
def test_no_usable_stage0_record_abstains_loudly_and_never_demotes(root, records):
    r = root(records=records)
    out = g.run(_perf(real={LEG: BROKEN}), root=r, window="last20x2", apply=True)
    dec = next(d for d in out["legs"] if d["leg"] == LEG)
    assert dec["action"] == g.HOLD and dec["abstain"] == g.ABSTAIN_NO_RECORD
    assert "never a guessed threshold" in dec["why"]
    assert LEG in out["abstained"][g.ABSTAIN_NO_RECORD]
    assert out["demoted"] == [] and LEG in _roster(r, "bybit_2")
    assert "abstained: no_record=" in g.render(out)


def test_positive_threshold_leg_making_money_is_never_demoted(root):
    r = root(records={LEG: HIGH_MEAN_RECORD, OTHER: HEALTHY_RECORD, EQ: HEALTHY_RECORD})
    p10 = g.leg_threshold(LEG, r)["p10"]
    assert p10 > 1.0
    # both windows below the positive p10 but PROFITABLE, and USD positive
    out = g.run(_perf(real={LEG: [_stats(usd=10.0, r=1.0), _stats(usd=5.0, r=0.5)]}),
                root=r, window="last20x2", apply=True)
    assert out["demoted"] == [] and LEG in _roster(r, "bybit_2")
    # even if USD read negative, a non-negative R window never demotes
    out = g.run(_perf(real={LEG: [_stats(r=0.5), _stats(r=0.2)]}),
                root=r, window="last20x2", apply=True)
    dec = next(d for d in out["legs"] if d["leg"] == LEG)
    assert dec["action"] == g.HOLD and "not negative" in dec["why"]
    assert out["demoted"] == [] and LEG in _roster(r, "bybit_2")


def test_only_stage2_legs_are_evaluated(root):
    out = g.run(_perf(real={"some_soak_only_leg": BROKEN}), root=root(), window="last20x2",
                apply=False)
    assert {d["account"] for d in out["legs"]} <= {"bybit_2", "alpaca_live", "ib_live"}
    assert "some_soak_only_leg" not in {d["leg"] for d in out["legs"]}


# ── REFUSE: the resolver decides, and a refusal leaves the roster alone ────
def test_refused_when_the_mandate_is_only_proposed(root):
    r = root(mandates=[], proposed=[MANDATE])
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_perf(real={LEG: BROKEN}), root=r, window="last20x2", apply=True)
    assert out["demoted"] == [] and out["refused"][0]["clause"] == "R-MANDATE-NOT-GRANTED"
    assert (r / "config/accounts.yaml").read_text() == before


def test_refused_when_the_cut_would_add_to_the_soak_book(root):
    text = ACCOUNTS_TEXT.replace(f"strategies: [{LEG}, {OTHER}]", f"strategies: [{OTHER}]", 1)
    r = root(accounts_text=text)
    out = g.run(_perf(real={LEG: BROKEN}), root=r, window="last20x2", apply=True)
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
    # a time-window payload (the rule this gate replaced) is refused, not read
    p.write_text(json.dumps({"window": "30d", "perStrategy": [], "paperPortfolio": {}}))
    assert g.main(["--perf-json", str(p)]) == 2


def test_mirror_window_record_carries_both_pnl_bases_without_moving_the_verdict():
    """FIX-SA-05 / JC-SA-03: the gate keeps MEASURED+ESTIMATED as its input and
    the evidence record adds the MEASURED-only half and the ESTIMATED count."""
    from scripts.ops.r4_demotion_gate import mirror_window_record
    chosen = {"trades": 30, "totalPnlMeasured": 900.0, "totalPnlMeasuredOnly": -50.0,
              "pnlMeasuredCount": 12, "pnlEstimatedCount": 18, "pnlCoverage": 0.4,
              "coverageFloor": 0.3, "minTrades": 20}
    r4 = {"chosenSource": "real_money", "real": chosen, "mirror": {},
          "status": "PASS", "detail": "d"}
    dec = {"leg": "L", "account": "bybit_2", "totalR": -1.0, "rTradeCount": 30, "r4": r4,
           "trigger_rule": "t", "windows": [{"window": 0, "totalR": -1.0, "r4": r4}],
           "threshold": {"p10": -4.0, "seed": 1, "draws": 2, "source_record": "x",
                         "source_run": "y"}}
    rec = mirror_window_record(dec, "run", "30d", None, "2026-09-29T00:00:00Z")
    assert rec["net_usd_measured"] == 900.0
    assert rec["net_usd_measured_only"] == -50.0
    assert rec["n_estimated"] == 18
