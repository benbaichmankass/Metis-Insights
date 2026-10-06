"""tradeify_1 — the second prop account (TRADEIFY-WIRE PR B, Tier-3).

Pins: the account ships DRY with the memo's B3 roster at 0.5% risk and the
same breach guards as breakout_1; its tickets are sized and routed off ITS OWN
ruleset (not breakout.yaml); and breakout_1's tickets are byte-identical to
what they were before the runtime account_cfg started carrying
``backtest_ruleset``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.prop.breakout_executor import emit_prop_ticket, routing_path_for

REPO = Path(__file__).resolve().parents[1]
ACCOUNTS = yaml.safe_load((REPO / "config" / "accounts.yaml").read_text())["accounts"]


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))


# ── the account entry ─────────────────────────────────────────────────────


def test_tradeify_goes_live_with_the_eth_sol_roster_at_half_percent():
    t = ACCOUNTS["tradeify_1"]
    assert t["mode"] == "live"   # PR C go-live; PR B shipped dry_run
    assert t["exchange"] == "breakout" and t["type"] == "prop" and t["account_class"] == "prop"
    # ict_scalp_xrp_15m dropped at go-live (operator 2026-10-03 "Drop it (Recommended)":
    # it fails its causal rebuild, #15603); XRPUSDT left the pull list with it.
    # trend_donchian_sol_prop removed 2026-10-05 (#16598, RQ-20261005-807 FAIL alone) and RE-ADDED the
    # same day as the second leg of the ETH+SOL book (Tier-3, data-backed: RQ-20261005-902, rule
    # RULE-RQ1005-PROPEXPAND-TRADEIFY, INACTIVITY_SAFE_IMPROVEMENT -- min-seed P 0.372 vs 0.284,
    # EV +$179 vs +$58; not a 0.70 pass). Pinned so a roster change is a visible decision.
    assert t["strategies"] == ["trend_donchian_eth_prop", "trend_donchian_sol_prop"]
    assert t["symbols"] == ["ETHUSDT", "SOLUSDT"]
    assert t["risk"]["risk_pct"] == 0.005
    assert t["risk"]["max_dd_pct"] == 0.06 and t["risk"]["daily_loss_pct"] == 0.03
    assert t["backtest_ruleset"] == "prop_rulesets/tradeify_247_1step.yaml"
    assert t["account_size_usd"] == 10000


def test_breach_guards_enforce_not_report():
    """Manager review of #14672: a breach must REFUSE on tradeify_1."""
    from src.prop.prop_risk_gate import breach_guards_for
    assert "breach_guards" not in ACCOUNTS["tradeify_1"]["risk"]
    assert breach_guards_for("tradeify_1") == "enforce"
    assert breach_guards_for("breakout_1") == "report"   # unchanged


def test_one_leg_per_symbol_so_our_own_legs_cannot_hedge():
    strat = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    blocks = strat.get("strategies", strat)
    syms = []
    for leg in ACCOUNTS["tradeify_1"]["strategies"]:
        s = blocks[leg].get("symbols") or [blocks[leg].get("symbol")]
        syms.extend(x for x in s if x)
    assert len(syms) == len(set(syms)) == 2, syms     # ETH + SOL (book re-add 2026-10-05); XRP dropped at go-live
    assert set(syms) == set(ACCOUNTS["tradeify_1"]["symbols"])


def test_ruleset_is_tradeify_and_unconfirmed():
    from src.prop.ruleset import load_ruleset
    path = REPO / "config" / "prop_rulesets" / "tradeify_247_1step.yaml"
    rs = load_ruleset(path)
    raw = yaml.safe_load(path.read_text())
    assert rs.account_size_usd == 10000.0
    assert rs.limits.daily_loss_pct == 0.03 and rs.limits.max_drawdown_pct == 0.06
    assert rs.limits.drawdown_type == "static"
    assert raw["unconfirmed"] is True
    assert raw["limits"]["daily_loss_reset_utc"] == "22:00"
    assert raw["sizing"] == {"mode": "flat", "flat": {"max_risk_usd": 50}}
    assert raw["routing"] == "prop_rulesets/tradeify_routing.yaml"


# ── routing resolution ────────────────────────────────────────────────────


def test_breakout_ruleset_still_routes_through_breakout_routing():
    bpath = REPO / "config" / "prop_rulesets" / "breakout.yaml"
    assert "routing" not in (yaml.safe_load(bpath.read_text()) or {})
    assert routing_path_for(str(bpath)).name == "breakout_routing.yaml"
    assert routing_path_for(None).name == "breakout_routing.yaml"


def test_tradeify_routes_through_its_own_map():
    tpath = REPO / "config" / "prop_rulesets" / "tradeify_247_1step.yaml"
    assert routing_path_for(str(tpath)).name == "tradeify_routing.yaml"
    r = yaml.safe_load((REPO / "config" / "prop_rulesets" / "tradeify_routing.yaml").read_text())
    # The routing map may name more symbols than the roster trades (XRPUSDT stays
    # mapped after its leg was dropped); every symbol the account pulls must be mapped.
    assert set(ACCOUNTS["tradeify_1"]["symbols"]) <= set(r["symbols"])


# ── tickets: the runtime account_cfg shape (FLAT, as the coordinator builds it) ──


def _runtime_cfg(name: str) -> dict:
    """What Coordinator.multi_account_execute hands execute_pkg, from the loader."""
    from src.units.accounts import load_accounts
    acc = next(a for a in load_accounts(str(REPO / "config" / "accounts.yaml")) if a.name == name)
    return {"account_id": acc.name, "exchange": acc.exchange,
            "risk_pct": acc.risk_manager.risk_pct, "account_class": acc.account_class,
            "backtest_ruleset": getattr(acc, "backtest_ruleset", None)}


def _emit(cfg, **order):
    o = {"symbol": "SOLUSDT", "direction": "long", "side": "Buy",
         "entry": 150.0, "sl": 145.0, "tp": 162.0, "strategy": "trend_donchian_sol_prop"}
    o.update(order)
    seen = {}
    emit_prop_ticket(o, cfg, _emitter=lambda t: seen.setdefault("t", t))
    return seen["t"]


def test_loader_carries_backtest_ruleset_onto_the_account():
    assert _runtime_cfg("tradeify_1")["backtest_ruleset"] == "prop_rulesets/tradeify_247_1step.yaml"
    assert _runtime_cfg("breakout_1")["backtest_ruleset"] == "prop_rulesets/breakout.yaml"


def test_breakout_ticket_is_identical_with_and_without_the_ruleset_key():
    """breakout_1's runtime cfg did NOT carry backtest_ruleset before; its prop
    unit then defaulted to breakout.yaml. Carrying it now must change nothing."""
    cfg = _runtime_cfg("breakout_1")
    with_key = _emit(cfg)
    old_shape = {k: v for k, v in cfg.items() if k != "backtest_ruleset"}
    # the opposite direction: a second SOL-long would be suppressed by the
    # one-ticket-per-trade guard, which is not what this compares
    without_key = _emit(old_shape, direction="short", side="Sell", entry=150.0, sl=155.0, tp=138.0)
    assert with_key.risk_usd == pytest.approx(75.0)
    assert without_key.risk_usd == pytest.approx(75.0)
    assert with_key.cfg.dxtrade_symbol == without_key.cfg.dxtrade_symbol == "SOLUSD"
    assert with_key.qty_units == pytest.approx(75.0 / 5.0)
    assert without_key.qty_units == pytest.approx(75.0 / 5.0)


def test_tradeify_ticket_is_sized_off_its_own_ruleset():
    t = _emit(_runtime_cfg("tradeify_1"), symbol="XRPUSDT", entry=0.60, sl=0.58, tp=0.66,
              strategy="ict_scalp_xrp_15m")
    # 0.5% x $10,000 = $50 (breakout.yaml would have given 0.5% x $5,000 = $25)
    assert t.risk_usd == pytest.approx(50.0)
    assert t.qty_units == pytest.approx(50.0 / 0.02)
    assert t.cfg.dxtrade_symbol == "XRPUSD"


def test_without_the_plumbing_tradeify_would_have_been_mis_sized():
    """The bug this PR fixes, stated as a test: the old runtime cfg (no
    backtest_ruleset) resolves a prop account to breakout.yaml."""
    cfg = {k: v for k, v in _runtime_cfg("tradeify_1").items() if k != "backtest_ruleset"}
    t = _emit(cfg, symbol="ETHUSDT", entry=2000.0, sl=1980.0, tp=2060.0, strategy="trend_donchian_eth_prop")
    assert t.risk_usd == pytest.approx(25.0)   # 0.5% x Breakout's $5,000 — wrong account size


def test_dry_account_emits_nothing():
    """mode: dry_run → execute_pkg's breakout branch returns a dry- id, no ticket."""
    from types import SimpleNamespace
    from src.units.accounts.execute import execute_pkg
    pkg = SimpleNamespace(symbol="SOLUSDT", direction="long", entry=150.0, sl=145.0, tp=162.0,
                          strategy="trend_donchian_sol_prop", meta={})
    cfg = {**_runtime_cfg("tradeify_1"), "mode": "dry_run"}
    tid = execute_pkg(pkg, cfg, exchange_client=None, dry_run=True)
    assert str(tid).startswith("dry-")
    from src.prop import prop_journal
    assert prop_journal.list_tickets(account_id="tradeify_1", limit=10) == []


# ── a bare Telegram report still resolves to the LIVE prop account ───────────


def test_bare_prop_report_defaults_to_tradeify_1_with_breakout_1_dry_run(monkeypatch):
    """Since 2026-10-05 breakout_1 is `mode: dry_run` (operator, set-account-mode
    run 37273806314) and tradeify_1 is the only LIVE prop account, so a bare fill
    report / screenshot resolves to tradeify_1 from the accounts file alone (the
    single-live rule; a dry_run account places nothing, so a bare report cannot
    be about it). No env pin. breakout_1 keeps `report_default: true`, which only
    matters again if two prop accounts are live."""
    from src.prop import telegram_report_handler as h

    monkeypatch.delenv("PROP_DEFAULT_ACCOUNT", raising=False)
    assert h.default_prop_account() == "tradeify_1"


def test_default_prop_account_rules(monkeypatch):
    from src.config import accounts_loader
    from src.prop import telegram_report_handler as h

    monkeypatch.delenv("PROP_DEFAULT_ACCOUNT", raising=False)
    prop = {"exchange": "breakout", "type": "prop", "account_class": "prop"}

    def use(accts):
        monkeypatch.setattr(accounts_loader, "load_accounts_dict", lambda *a, **k: accts)

    use({"a": {**prop, "mode": "live"}, "b": {**prop, "mode": "dry_run"}})
    assert h.default_prop_account() == "a"
    use({"a": {**prop, "mode": "live"}, "b": {**prop}})       # mode missing = live
    assert h.default_prop_account() is None                   # two live: ask, never guess
    use({"a": {**prop, "mode": "dry_run"}, "b": {**prop, "mode": "dry_run"}})
    assert h.default_prop_account() is None
    use({"a": {**prop, "mode": "dry_run"}})                   # the only prop account
    assert h.default_prop_account() == "a"


def test_report_default_breaks_a_tie_between_live_prop_accounts(monkeypatch):
    from src.config import accounts_loader
    from src.prop import telegram_report_handler as h

    monkeypatch.delenv("PROP_DEFAULT_ACCOUNT", raising=False)
    prop = {"exchange": "breakout", "type": "prop", "account_class": "prop", "mode": "live"}
    monkeypatch.setattr(accounts_loader, "load_accounts_dict", lambda *a, **k: {
        "a": {**prop, "report_default": True}, "b": {**prop}})
    assert h.default_prop_account() == "a"
    monkeypatch.setattr(accounts_loader, "load_accounts_dict", lambda *a, **k: {
        "a": {**prop, "report_default": True}, "b": {**prop, "report_default": True}})
    assert h.default_prop_account() is None                   # two defaults: ask


def test_auto_executed_accounts_do_not_take_the_bare_report_default(monkeypatch):
    """VELOTRADE-GOLIVE: an account executed over REST (dxtrade_api) or by the
    phone never takes a manual Telegram report-back, so its going live must not
    make the bare-report default ambiguous."""
    from src.config import accounts_loader
    from src.prop import platform as plat
    from src.prop import telegram_report_handler as h

    monkeypatch.delenv("PROP_DEFAULT_ACCOUNT", raising=False)
    prop = {"exchange": "breakout", "type": "prop", "account_class": "prop", "mode": "live"}
    monkeypatch.setattr(accounts_loader, "load_accounts_dict",
                        lambda *a, **k: {"manual": dict(prop), "rest": dict(prop), "phone": dict(prop)})
    monkeypatch.setattr(plat, "auto_executed_accounts", lambda *a, **k: {"rest", "phone"})
    assert h.default_prop_account() == "manual"
    # two MANUAL live accounts are still ambiguous without a report_default flag
    monkeypatch.setattr(accounts_loader, "load_accounts_dict",
                        lambda *a, **k: {"m1": dict(prop), "m2": dict(prop), "rest": dict(prop)})
    assert h.default_prop_account() is None


def test_auto_executed_accounts_reads_the_real_config():
    from src.prop.platform import auto_executed_accounts
    auto = auto_executed_accounts()
    assert "velotrade_1" in auto          # platform: dxtrade_api
    assert "breakout_2" in auto           # phone_accounts
    assert "tradeify_1" not in auto and "breakout_1" not in auto
