"""scripts/ops/r4_demotion_gate.py — R4 enforcing as the Stage-2 DEMOTION gate,
under the T3 AND net<0 rule (operator, 2026-09-29, "Last 20 + strict test").

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

#: A healthy Stage-0 stream: mean +0.2R, so the 20-trade p10 is a little
#: below zero and a -0.3R/trade window (-6R) sits well under it.
HEALTHY_SRC = [1.0, -0.8, 0.6, -1.0, 1.2]
#: A strong stream whose 20-trade p10 is POSITIVE (xrp_4h / ief / iaum shape).
STRONG_SRC = [1.5, 0.9, 1.1, -0.2, 1.3]


def _row(leg, n, r, usd=None, cov=0.8):
    usd = r * 10.0 if usd is None else usd
    return {"name": leg, "trades": n, "totalPnlMeasured": usd, "totalPnl": usd,
            "pnlCoverage": cov, "pnlMeasuredCount": int(n * cov), "totalR": r,
            "rTradeCount": n}


def _entry(leg, windows, usd=None, cov=0.8, avail=None):
    """One leg's /performance/recent entry from its window R values (oldest
    first). usd defaults to the R sign, so R4 and R agree unless told not to."""
    n = 20 * len(windows)
    tot = round(sum(windows), 4)
    return {"closedAvailable": avail if avail is not None else n, "nUsed": n,
            "complete": n == 40,
            "last": {"perStrategy": [_row(leg, n, tot, usd=usd, cov=cov)]},
            "blocks": [{"perStrategy": [_row(leg, 20, w, cov=cov)],
                        "closedFrom": f"2026-09-{10 + i:02d}T00:00:00",
                        "closedTo": f"2026-09-{10 + i:02d}T12:00:00"}
                       for i, w in enumerate(windows)]}


def _recent(real=None, mirror=None):
    return {"n": 40, "block": 20, "error": False,
            "realMoney": {"readState": "ok", "perStrategy": real or {}},
            "mirror": {"readState": "ok", "accountIds": ["bybit_portfolio", "alpaca_portfolio"],
                       "perStrategy": mirror or {}}}


@pytest.fixture
def root(tmp_path):
    def make(accounts_text=ACCOUNTS_TEXT, mandates=None, proposed=None, evidence=None):
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "config" / "accounts.yaml").write_text(accounts_text)
        (tmp_path / "config" / "strategies.yaml").write_text("strategies: {}\n")
        (tmp_path / "config" / "mandates.yaml").write_text(yaml.safe_dump(
            {"mandates": [MANDATE] if mandates is None else mandates, "proposed": proposed or []}))
        ev = {LEG: HEALTHY_SRC, OTHER: HEALTHY_SRC, EQ: HEALTHY_SRC} if evidence is None else evidence
        for leg, xs in ev.items():
            src = f"comms/strategy_evidence/runs/t/{leg}__trades.jsonl"
            (tmp_path / src).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / src).write_text("".join(json.dumps({"net_r": x}) + "\n" for x in xs))
            (tmp_path / f"comms/strategy_evidence/{leg}.json").write_text(
                json.dumps({"strategy": leg, "source_run": src}))
        return tmp_path
    return make


def _roster(root, acct):
    return yaml.safe_load((root / "config/accounts.yaml").read_text())["accounts"][acct]["strategies"]


def _dec(out, leg=LEG):
    return next(d for d in out["legs"] if d["leg"] == leg)


def test_fixture_thresholds_are_what_the_tests_assume(root):
    r = root(evidence={LEG: HEALTHY_SRC, EQ: STRONG_SRC})
    healthy = g.mr.stage0_block_p10(LEG, r)["p10"]
    strong = g.mr.stage0_block_p10(EQ, r)["p10"]
    assert -6.0 < healthy < 0.0, healthy
    assert strong > 0.0, strong


# ── positive controls ──────────────────────────────────────────────────────
def test_apply_demotes_a_leg_with_both_windows_below_p10_from_live_and_mirror(root):
    r = root()
    out = g.run(_recent(real={LEG: _entry(LEG, [-6.0, -6.0])}), root=r, apply=True)
    assert out["demoted"] == [{"leg": LEG, "accounts": ["bybit_2", "bybit_portfolio"]}]
    assert _roster(r, "bybit_2") == [OTHER]
    assert _roster(r, "bybit_2") == _roster(r, "bybit_portfolio")   # mirror equality holds
    assert LEG in _roster(r, "bybit_1")                              # back on the soak book
    rec = json.loads((r / f"comms/mandate_evidence/mirror_window/{LEG}.json").read_text())
    assert rec["rule"] == g.mr.T3_RULE_ID and rec["window"] == "last40"
    assert rec["net_r_net_of_full_cost"] == -12.0 and rec["n_closed"] == 40
    assert [w["net_r"] for w in rec["windows"]] == [-6.0, -6.0]
    assert [w["n_closed"] for w in rec["windows"]] == [20, 20]
    # the threshold, its seed and its source are in the record
    assert rec["p10_threshold"] == g.mr.stage0_block_p10(LEG, r)["p10"]
    assert rec["bootstrap_seed"] == g.mr.T3_BOOTSTRAP_SEED
    assert rec["evidence_record"] == f"comms/strategy_evidence/{LEG}.json"
    assert rec["evidence_source_run"].endswith(f"{LEG}__trades.jsonl")
    assert (r / rec["source_run"]).is_file()
    firing = json.loads(next((r / "comms/mandate_firings").glob("*.json")).read_text())
    assert firing["action"] == "remove" and firing["leg"] == LEG
    assert "# the live roster" in (r / "config/accounts.yaml").read_text()


def test_a_planted_minus_0_3R_leg_demotes_only_after_two_windows(root):
    """-0.3R/trade = -6R per 20-trade window. One bad window after a healthy
    one HOLDS; the second consecutive bad window DEMOTES."""
    r = root()
    before = (r / "config/accounts.yaml").read_text()
    one = g.run(_recent(real={LEG: _entry(LEG, [+2.0, -6.0], usd=-10.0)}), root=r, apply=True)
    assert _dec(one)["action"] == g.HOLD and "T3 not met" in _dec(one)["why"]
    assert (r / "config/accounts.yaml").read_text() == before
    two = g.run(_recent(real={LEG: _entry(LEG, [-6.0, -6.0])}), root=r, apply=True)
    assert _dec(two)["action"] == g.DEMOTE and [d["leg"] for d in two["demoted"]] == [LEG]


def test_mirror_source_carries_the_call_when_real_money_abstains(root):
    r = root()
    out = g.run(_recent(real={LEG: _entry(LEG, [-6.0])},              # 20 real trades: thin
                        mirror={LEG: _entry(LEG, [-7.0, -6.5])}), root=r, apply=True)
    assert [d["leg"] for d in out["demoted"]] == [LEG]
    rec = json.loads((r / f"comms/mandate_evidence/mirror_window/{LEG}.json").read_text())
    assert rec["chosen_source"] == "mirror" and rec["net_r_net_of_full_cost"] == -13.5


def test_block_form_roster_on_alpaca_live(root):
    r = root()
    out = g.run(_recent(real={EQ: _entry(EQ, [-6.0, -6.0])}), root=r, apply=True)
    assert out["demoted"] == [{"leg": EQ, "accounts": ["alpaca_live", "alpaca_portfolio"]}]
    assert not _roster(r, "alpaca_live") and not _roster(r, "alpaca_portfolio")
    assert _roster(r, "alpaca_paper") == [EQ]
    assert "# real money" in (r / "config/accounts.yaml").read_text()   # prose kept


def test_dry_run_reports_the_demotion_and_writes_nothing(root):
    r = root()
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_recent(real={LEG: _entry(LEG, [-6.0, -6.0])}), root=r, apply=False)
    assert out["mode"] == "dry_run" and [d["leg"] for d in out["demoted"]] == [LEG]
    assert (r / "config/accounts.yaml").read_text() == before
    assert not (r / "comms/mandate_evidence").exists() and out["written"] == []


# ── HOLD: the evidence does not support a cut ──────────────────────────────
def test_a_healthy_leg_stays_hold(root):
    r = root()
    out = g.run(_recent(real={LEG: _entry(LEG, [+2.0, +3.0])}), root=r, apply=True)
    assert _dec(out)["action"] == g.HOLD and out["demoted"] == []
    assert not (r / "comms/mandate_evidence").exists()


def test_a_positive_p10_leg_that_is_making_money_is_never_demoted(root):
    """Both windows are BELOW its (positive) p10 and R4's USD read even says
    would_block -- but net R is positive, so the never-clause holds it."""
    r = root(evidence={EQ: STRONG_SRC})
    p10 = g.mr.stage0_block_p10(EQ, r)["p10"]
    w = round(p10 / 4, 4)                                   # 0 < w < p10
    out = g.run(_recent(real={EQ: _entry(EQ, [w, w], usd=-5.0)}), root=r, apply=True)
    d = _dec(out, EQ)
    assert d["action"] == g.HOLD and "non-negative" in d["why"]
    assert out["demoted"] == [] and EQ in _roster(r, "alpaca_live")
    # ...and with windows negative but ABOVE zero-capped bar it still cannot fire
    # on a positive p10 alone: the bar is min(p10, 0).
    out = g.run(_recent(real={EQ: _entry(EQ, [-0.5, +0.4])}), root=r, apply=True)
    assert _dec(out, EQ)["action"] == g.HOLD


@pytest.mark.parametrize("entry,why", [
    (lambda: _entry(LEG, [-6.0, -6.0], cov=0.59), "abstain_unverified"),
    (lambda: _entry(LEG, [-6.0, -6.0], usd=0.0), "pass"),
    (lambda: _entry(LEG, [-6.0, +6.0], usd=-5.0), "non-negative"),
    (lambda: _entry(LEG, [-1.0, -6.0]), "T3 not met"),
])
def test_hold_and_abstain_paths_never_touch_the_roster(root, entry, why):
    r = root()
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_recent(real={LEG: entry()}), root=r, apply=True)
    assert why in _dec(out)["why"] and _dec(out)["action"] != g.DEMOTE
    assert out["demoted"] == [] and (r / "config/accounts.yaml").read_text() == before
    assert not (r / "comms/mandate_evidence").exists()


# ── ABSTAIN: we could not look ─────────────────────────────────────────────
@pytest.mark.parametrize("real", [None, "one_window", "39"])
def test_fewer_than_40_closed_trades_abstains_and_is_counted(root, real):
    r = root()
    ent = {None: None, "one_window": _entry(LEG, [-9.0]),
           "39": {**_entry(LEG, [-9.0, -9.0]), "complete": False}}[real]
    if real == "39":
        ent["last"]["perStrategy"][0]["trades"] = 39
    out = g.run(_recent(real={LEG: ent} if ent else {}), root=r, apply=True)
    d = _dec(out)
    assert d["action"] == g.ABSTAIN and d["abstain"] == "thin"
    assert out["counts"]["abstain"] >= 1 and out["counts"]["abstain_thin"] >= 1
    assert out["demoted"] == []


def test_no_usable_evidence_abstains_loudly_and_never_guesses(root, tmp_path, capsys):
    r = root(evidence={OTHER: HEALTHY_SRC, EQ: HEALTHY_SRC})          # LEG has no record
    out = g.run(_recent(real={LEG: _entry(LEG, [-20.0, -20.0])}), root=r, apply=True)
    d = _dec(out)
    assert d["action"] == g.ABSTAIN and d["abstain"] == "no_evidence"
    assert "NO USABLE STAGE-0 EVIDENCE" in d["why"] and d["threshold"]["p10"] is None
    assert out["no_evidence"] == [LEG] and out["counts"]["abstain_no_evidence"] == 1
    assert out["demoted"] == [] and LEG in _roster(r, "bybit_2")
    assert "NO USABLE STAGE-0 EVIDENCE" in g.render(out)
    # the CLI says it on stderr as an Actions warning
    p = tmp_path / "recent.json"
    p.write_text(json.dumps(_recent(real={LEG: _entry(LEG, [-20.0, -20.0])})))
    assert g.main(["--recent-json", str(p), "--root", str(r)]) == 0
    assert f"::warning::r4-demotion-gate: {LEG} has NO USABLE STAGE-0 EVIDENCE" in capsys.readouterr().err


def test_a_single_trade_source_run_is_not_usable_evidence(root):
    r = root(evidence={LEG: [0.5], OTHER: HEALTHY_SRC, EQ: HEALTHY_SRC})
    out = g.run(_recent(real={LEG: _entry(LEG, [-9.0, -9.0])}), root=r, apply=False)
    assert _dec(out)["abstain"] == "no_evidence"


def test_only_stage2_legs_are_evaluated(root):
    out = g.run(_recent(real={"some_soak_only_leg": _entry("some_soak_only_leg", [-9.0, -9.0])}),
                root=root(), apply=False)
    assert {d["account"] for d in out["legs"]} <= {"bybit_2", "alpaca_live", "ib_live"}
    assert "some_soak_only_leg" not in {d["leg"] for d in out["legs"]}


# ── the resolver decides, and agrees with the gate ─────────────────────────
@pytest.mark.parametrize("windows,usd", [
    ([-6.0, -6.0], None), ([+2.0, -6.0], -1.0), ([+2.0, +3.0], None), ([-6.0, +6.0], -1.0),
    ([-1.0, -6.0], None),
])
def test_the_resolvers_verdict_matches_the_gates(root, windows, usd):
    """Whatever the gate decides, the resolver reading the record the gate
    would write for it must agree: DEMOTE <-> FIRE, anything else <-> not."""
    r = root()
    d = g.evaluate(_recent(real={LEG: _entry(LEG, windows, usd=usd)}), r)
    d = next(x for x in d if x["leg"] == LEG)
    run_rel = "comms/mandate_evidence/mirror_window/runs/x-last40.json"
    (r / run_rel).parent.mkdir(parents=True, exist_ok=True)
    (r / run_rel).write_text("{}\n")
    (r / f"comms/mandate_evidence/mirror_window/{LEG}.json").write_text(
        json.dumps(g.mirror_window_record(d, run_rel, "last40", "2026-09-29T00:00:00Z")))
    res = g.mr.resolve(LEG, "S2", "S1", "bybit_2", root=r)
    assert (d["action"] == g.DEMOTE) == (res["verdict"] == g.mr.FIRE), (d["why"], res)


def test_resolver_refuses_a_record_whose_threshold_was_edited(root):
    r = root()
    d = next(x for x in g.evaluate(_recent(real={LEG: _entry(LEG, [-6.0, -6.0])}), r)
             if x["leg"] == LEG)
    assert d["action"] == g.DEMOTE
    run_rel = "comms/mandate_evidence/mirror_window/runs/x-last40.json"
    (r / run_rel).parent.mkdir(parents=True, exist_ok=True)
    (r / run_rel).write_text("{}\n")
    rel = r / f"comms/mandate_evidence/mirror_window/{LEG}.json"
    rec = g.mirror_window_record(d, run_rel, "last40", "2026-09-29T00:00:00Z")
    rel.write_text(json.dumps(rec))
    assert g.mr.resolve(LEG, "S2", "S1", "bybit_2", root=r)["verdict"] == g.mr.FIRE
    for key, val in (("p10_threshold", 99.0), ("bootstrap_seed", 1)):
        rel.write_text(json.dumps({**rec, key: val}))
        res = g.mr.resolve(LEG, "S2", "S1", "bybit_2", root=r)
        assert res["verdict"] == g.mr.REFUSE and res["clause"] == "R-T3-THRESHOLD", key
    # an old-shape (pre-T3) record states a negative net but no windows: NEEDS-DATA
    rel.write_text(json.dumps({k: v for k, v in rec.items() if k not in ("rule", "windows")}))
    res = g.mr.resolve(LEG, "S2", "S1", "bybit_2", root=r)
    assert res["verdict"] == g.mr.NEEDS_DATA and res["clause"] == "R-T3-WINDOWS"


# ── REFUSE: the resolver decides, and a refusal leaves the roster alone ────
def test_refused_when_the_mandate_is_only_proposed(root):
    r = root(mandates=[], proposed=[MANDATE])
    before = (r / "config/accounts.yaml").read_text()
    out = g.run(_recent(real={LEG: _entry(LEG, [-6.0, -6.0])}), root=r, apply=True)
    assert out["demoted"] == [] and out["refused"][0]["clause"] == "R-MANDATE-NOT-GRANTED"
    assert (r / "config/accounts.yaml").read_text() == before


def test_refused_when_the_cut_would_add_to_the_soak_book(root):
    text = ACCOUNTS_TEXT.replace(f"strategies: [{LEG}, {OTHER}]", f"strategies: [{OTHER}]", 1)
    r = root(accounts_text=text)
    out = g.run(_recent(real={LEG: _entry(LEG, [-6.0, -6.0])}), root=r, apply=True)
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


def test_cli_dry_run_exit_zero_and_bad_payloads_exit_two(tmp_path, root):
    r = root()
    p = tmp_path / "recent.json"
    p.write_text(json.dumps(_recent()))
    assert g.main(["--recent-json", str(p), "--root", str(r)]) == 0
    # a caller may not choose its own window
    assert g.main(["--recent-json", str(p), "--root", str(r), "--window", "30d"]) == 2
    for bad in ({}, {**_recent(), "n": 60},
                {**_recent(), "mirror": {"readState": "no_portfolio_accounts_declared"}},
                {**_recent(), "error": True},
                {"window": "30d", "perStrategy": []}):     # the OLD /performance body
        p.write_text(json.dumps(bad))
        assert g.main(["--recent-json", str(p), "--root", str(r)]) == 2, bad


def test_the_gate_parses_a_real_route_payload(tmp_path, monkeypatch, root):
    """The payload shape comes from the route itself, not from this file's
    helper: seed a journal, call GET /performance/recent, feed the gate."""
    sys.path.insert(0, str(REPO / "tests"))
    import _perf_window_golden_seed as S
    from src.web.api.routers import performance as P
    db = tmp_path / "tj.db"
    S.seed(db, legs=((LEG, "bybit_2", "real_money", 0), (LEG, "bybit_portfolio", "paper", 1)),
           n_per=45)
    monkeypatch.setattr(P, "_DB_PATH", db)
    monkeypatch.setattr(P, "journal_trust_map", lambda: S.TRUST_MAP)
    monkeypatch.setattr(P, "_portfolio_paper_account_ids", lambda: ["bybit_portfolio"])
    recent = json.loads(json.dumps(P.get_performance_recent(n=40)))
    d = _dec(g.run(recent, root=root(), apply=False))
    assert d["book"]["last"]["trades"] == 40 and len(d["book"]["blocks"]) == 2
    assert all(b["trades"] == 20 for b in d["book"]["blocks"])
    assert d["r4"]["real"]["status"] != "abstain_thin"        # the gate saw 40 trades


def test_mirror_window_record_carries_both_pnl_bases_without_moving_the_verdict():
    """FIX-SA-05 / JC-SA-03: the gate keeps MEASURED+ESTIMATED as its input and
    the evidence record adds the MEASURED-only half and the ESTIMATED count."""
    from scripts.ops.r4_demotion_gate import mirror_window_record
    chosen = {"trades": 40, "totalPnlMeasured": 900.0, "totalPnlMeasuredOnly": -50.0,
              "pnlMeasuredCount": 12, "pnlEstimatedCount": 18, "pnlCoverage": 0.4,
              "coverageFloor": 0.3, "minTrades": 40}
    dec = {"leg": "L", "account": "bybit_2", "totalR": -1.0, "rTradeCount": 40,
           "book": {"last": chosen, "blocks": [], "blockSpans": []},
           "threshold": {"p10": -2.0, "seed": 1, "draws": 2, "block": 20,
                         "evidence_record": "e", "source_run": "s", "source_n": 3},
           "r4": {"chosenSource": "real_money", "real": chosen, "mirror": {},
                  "status": "PASS", "detail": "d"}}
    rec = mirror_window_record(dec, "run", "last40", "2026-09-29T00:00:00Z")
    assert rec["net_usd_measured"] == 900.0
    assert rec["net_usd_measured_only"] == -50.0
    assert rec["n_estimated"] == 18
