"""TP doctrine B1: the TP-revision verdict contract and both amend paths.

Exchange path: monitor() -> {"tp", "tp_reason"} -> interpret_verdict ->
order_monitor._apply_update -> _send_modify_to_exchange (fails closed: an
unconfirmed amend leaves the package row unchanged).
Prop path: prop_trail._tp_step -> plan_tp_revision -> modify_bracket(None, tp)
on a TP-capable adapter, confirmed on re-read.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.prop import prop_trail as pt
from src.prop.platform.base import Position
from src.runtime import tp_revision as tr
from src.runtime.monitor_verdict import KIND_MODIFY, interpret_verdict

from tests.test_prop_trail import SOL, Api, bars, cfg, now_after


@pytest.fixture
def fixed_rule(monkeypatch):
    """A registered rule that predicts `entry + k*risk` (k from params)."""
    def rule(*, direction, entry, risk, bars, entry_time, params):
        k = float(params.get("k", 1.5))
        return (entry + k * risk if direction == "long" else entry - k * risk), {"k": k}
    monkeypatch.setitem(tr.RULES, "fixed_k", rule)
    return {"tp_revision": {"rule": "fixed_k", "k": 1.5}}


# ── the contract ────────────────────────────────────────────────────────────
def test_undeclared_leg_gets_no_revision(fixed_rule):
    assert tr.plan_tp_revision(leg={}, direction="long", entry=100, risk=5, bars=None,
                               entry_time=None, ref_price=101) is None


def test_unknown_rule_gets_no_revision():
    assert tr.plan_tp_revision(leg={"tp_revision": "nobody"}, direction="long", entry=100, risk=5,
                               bars=None, entry_time=None, ref_price=101) is None


def test_declared_rule_yields_a_reasoned_revision(fixed_rule):
    rev = tr.plan_tp_revision(leg=fixed_rule, direction="long", entry=100, risk=5, bars=None,
                              entry_time=None, ref_price=101)
    assert rev.tp == 107.5 and not rev.clamped and rev.rule == "fixed_k"
    assert "fixed_k" in rev.reason and "+1.50R" in rev.reason
    v = tr.as_verdict(rev)
    assert v == {"tp": 107.5, "tp_reason": rev.reason}


def test_short_mirror_and_string_declaration(monkeypatch, fixed_rule):
    rev = tr.plan_tp_revision(leg={"tp_revision": "fixed_k"}, direction="short", entry=100, risk=5,
                              bars=None, entry_time=None, ref_price=99)
    assert rev.tp == 92.5


def test_revision_through_price_is_not_placed(fixed_rule):
    # ref price already beyond the predicted target -> an instant fill nobody decided
    assert tr.plan_tp_revision(leg=fixed_rule, direction="long", entry=100, risk=5, bars=None,
                               entry_time=None, ref_price=125) is None


def test_revision_is_clamped_to_the_venue_cap_from_current_price_and_says_so(fixed_rule):
    leg = {"tp_revision": {"rule": "fixed_k", "k": 40.0}}          # 300 vs cap 101*1.099
    rev = tr.plan_tp_revision(leg=leg, direction="long", entry=100, risk=5, bars=None,
                              entry_time=None, ref_price=101)
    assert rev.clamped and rev.tp == pytest.approx(101 * 1.099)
    assert "CLAMPED" in rev.reason


def test_a_raising_rule_never_escapes(monkeypatch):
    def boom(**kw):
        raise RuntimeError("x")
    monkeypatch.setitem(tr.RULES, "boom", boom)
    assert tr.plan_tp_revision(leg={"tp_revision": "boom"}, direction="long", entry=100, risk=5,
                               bars=None, entry_time=None, ref_price=101) is None


def test_merge_verdict_close_wins_and_sl_merges(fixed_rule):
    rev = tr.TpRevision(tp=120.0, rule="fixed_k", reason="r")
    assert tr.merge_verdict({"action": "close", "reason": "sl_cross"}, rev) == {"action": "close",
                                                                               "reason": "sl_cross"}
    assert tr.merge_verdict({"sl": 99.0}, rev) == {"sl": 99.0, "tp": 120.0, "tp_reason": "r"}
    assert tr.merge_verdict(None, rev) == {"tp": 120.0, "tp_reason": "r"}
    assert tr.merge_verdict({"sl": 99.0}, None) == {"sl": 99.0}


def test_interpreter_carries_tp_reason_only_with_a_surviving_tp():
    d = interpret_verdict({"tp": 120.0, "tp_reason": "why"}, current_sl=95, current_tp=110)
    assert d.kind == KIND_MODIFY and d.tp == 120.0 and d.tp_reason == "why"
    d = interpret_verdict({"sl": 97.0, "tp": 110.0, "tp_reason": "why"}, current_sl=95, current_tp=110)
    assert d.tp is None and d.tp_reason is None and d.sl == 97.0       # tp filtered as no change


# ── exchange amend path ─────────────────────────────────────────────────────
class _Summary:
    def __init__(self):
        self.no_change_count = self.updated_count = self.error_count = 0
        self.errors: List[str] = []


class _Db:
    def __init__(self):
        self.pkg_updates: List[Dict[str, Any]] = []
        self.trade_updates: List[Any] = []

    def update_order_package(self, pkg_id, updates):
        self.pkg_updates.append(dict(updates))

    def update_trade(self, trade_id, fields):
        self.trade_updates.append((trade_id, dict(fields)))


def _apply(monkeypatch, ok: bool):
    from src.runtime import order_monitor as om
    sent: List[Dict[str, Any]] = []
    pings: List[Any] = []
    leg = {"id": 7, "account_id": "bybit_1", "symbol": "ETHUSDT", "direction": "long",
           "position_size": 1.0, "strategy_name": "trend_donchian_eth_4h"}
    monkeypatch.setattr(om, "_package_open_legs", lambda db, pkg: ([leg], "resolved"))

    def _send(leg, **kw):
        sent.append(kw)
        return {"ok": ok, "error": None if ok else "venue said no"}
    monkeypatch.setattr(om, "_send_modify_to_exchange", _send)
    import src.runtime.execution_diagnostics as ed
    monkeypatch.setattr(ed, "enqueue_trade_update", lambda **kw: pings.append(kw))
    db, summary = _Db(), _Summary()
    pkg = {"order_package_id": "p1", "sl": 95.0, "tp": 109.9, "symbol": "ETHUSDT"}
    om._apply_update(db, pkg, {"tp": 120.0, "tp_reason": "measured move +4.00R"}, summary)
    return sent, db, summary, pings


def test_exchange_path_forwards_the_revised_tp_and_names_the_prediction(monkeypatch):
    sent, db, summary, pings = _apply(monkeypatch, ok=True)
    assert sent and sent[0]["tp"] == 120.0 and sent[0]["sl"] is None
    assert db.pkg_updates == [{"tp": 120.0}]
    assert db.trade_updates == [(7, {"take_profit_1": 120.0})]
    assert summary.updated_count == 1
    assert pings and any("measured move" in c for c in pings[0]["changes"])


def test_exchange_path_fails_closed_on_an_unconfirmed_amend(monkeypatch):
    sent, db, summary, pings = _apply(monkeypatch, ok=False)
    assert sent and db.pkg_updates == [] and db.trade_updates == []
    assert summary.error_count == 1 and not pings


# ── prop amend path ─────────────────────────────────────────────────────────
class TpAdapter:
    """A TP-capable prop adapter (the dxtrade_api shape)."""
    platform = "dxtrade_api"
    TP_AMEND_SUPPORTED = True
    quote = {"bid": 101.0, "ask": 101.02}

    def __init__(self, positions: List[Position], apply: bool = True, ok: bool = True):
        self.positions, self.apply, self.ok, self.calls = positions, apply, ok, []

    def read_positions(self, page):
        return [Position(**p.as_dict()) for p in self.positions]

    def read_quote(self, page, venue_symbol):
        return self.quote

    def modify_bracket(self, page, position, stop_loss, take_profit, *, arm=False, rollout=None):
        self.calls.append((stop_loss, take_profit, arm))
        if arm and self.ok and self.apply:
            for p in self.positions:
                if p.symbol == position.symbol:
                    if stop_loss is not None:
                        p.stop_loss = stop_loss
                    if take_profit is not None:
                        p.take_profit = take_profit
        return {"ok": self.ok, "clicked": arm, "why": "http 200" if self.ok else "http 400"}


class BrowserAdapter(TpAdapter):
    platform = "dxtrade"
    TP_AMEND_SUPPORTED = False


def _pos(sl=99.0, tp=130.0):
    # resting SL above the replayed trail so the SL step holds and the TP step runs
    return Position(symbol="SOLUSD", side="long", quantity=1, entry_price=100, stop_loss=sl, take_profit=tp)


def _run(adapter, api, mode, tmp_path, leg):
    c = bars([(101.5, 99.5, 101)], forming=(101.2, 100.8, 101.0))
    return pt.run_trail_step(adapter=adapter, page=None, api=api, cfg=cfg(), mode=mode,
                             state_dir=tmp_path, candles_fn=lambda s, tf: c, now=now_after(1),
                             legs={"trend_donchian_sol_prop": leg})


def test_prop_tp_capable_adapter_amends_tp_only_confirms_and_reports(tmp_path, fixed_rule):
    a, api = TpAdapter([_pos()]), Api()
    res = _run(a, api, "live", tmp_path, {**SOL, **fixed_rule})
    assert a.calls == [(None, 107.5, True)]                          # TP-only, SL untouched
    assert a.positions[0].take_profit == 107.5 and a.positions[0].stop_loss == 99.0
    rep = [b for b in api.posted if b.get("source") == "prop_trail_tp"]
    assert rep and rep[0]["tp"] == 107.5 and "fixed_k" in rep[0]["reason"]
    assert not res.alerts
    a.calls.clear()
    _run(a, api, "live", tmp_path, {**SOL, **fixed_rule})            # resting TP == prediction
    assert a.calls == []


def test_prop_undeclared_leg_never_touches_the_tp(tmp_path, fixed_rule):
    a, api = TpAdapter([_pos()]), Api()
    _run(a, api, "live", tmp_path, SOL)
    assert all(tp is None for _sl, tp, _arm in a.calls)


def test_prop_browser_adapter_is_skipped_with_a_logged_reason(tmp_path, fixed_rule):
    a, api = BrowserAdapter([_pos()]), Api()
    res = _run(a, api, "live", tmp_path, {**SOL, **fixed_rule})
    assert a.calls == [] and not res.alerts


def test_prop_read_only_builds_but_does_not_arm(tmp_path, fixed_rule):
    a, api = TpAdapter([_pos()]), Api()
    _run(a, api, "read_only", tmp_path, {**SOL, **fixed_rule})
    assert a.calls == [(None, 107.5, False)] and a.positions[0].take_profit == 130.0
    assert not api.posted


def test_prop_refused_tp_amend_alerts_once_and_keeps_the_resting_tp(tmp_path, fixed_rule):
    a, api = TpAdapter([_pos()], ok=False), Api()
    res = _run(a, api, "live", tmp_path, {**SOL, **fixed_rule})
    assert any("TP amend to 107.5 refused" in x for x in res.alerts)
    res2 = _run(a, api, "live", tmp_path, {**SOL, **fixed_rule})
    assert not any("refused" in x for x in res2.alerts)
    res3 = _run(a, api, "live", tmp_path, {**SOL, **fixed_rule})     # capped at MAX_ATTEMPTS_PER_TARGET
    assert len(a.calls) == pt.MAX_ATTEMPTS_PER_TARGET
    assert a.positions[0].take_profit == 130.0 and not res3.alerts


def test_prop_unconfirmed_tp_amend_alerts(tmp_path, fixed_rule):
    a, api = TpAdapter([_pos()], apply=False), Api()
    res = _run(a, api, "live", tmp_path, {**SOL, **fixed_rule})
    assert any("not confirmed" in x for x in res.alerts)
    assert not [b for b in api.posted if b.get("source") == "prop_trail_tp"]


def test_prop_no_quote_means_no_tp_amend(tmp_path, fixed_rule):
    a, api = TpAdapter([_pos()]), Api()
    a.quote = {}
    _run(a, api, "live", tmp_path, {**SOL, **fixed_rule})
    assert a.calls == []


def test_adapter_capability_flags_match_the_paths():
    from src.prop.platform.dxtrade import DXtradeAdapter
    from src.prop.platform.dxtrade_api import DXtradeApiAdapter
    assert DXtradeApiAdapter.TP_AMEND_SUPPORTED is True
    assert DXtradeAdapter.TP_AMEND_SUPPORTED is False      # rollout guard: SL-only tightens
    from src.prop.platform.dxtrade import rollout_tighten_mismatch
    bad = rollout_tighten_mismatch("long", 95.0, 110.0, 96.0, 107.5, {"bid": 109, "ask": 109.1})
    assert any("take profit unchanged" in b for b in bad)
