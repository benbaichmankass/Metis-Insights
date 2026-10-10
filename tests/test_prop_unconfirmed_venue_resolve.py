"""VELO-UNCONFIRMED-1008: a submit that no terminal re-read finds is resolved
against the VENUE's order history, never written off as
``skipped: unconfirmed_submit`` while that history could answer.

2026-10-08 15:28Z velotrade_1 ``prop-manual-df6e8938ab89`` (ETH short LIMIT
0.82 @ 2471.31): POSTed http 200, misread as ``partial_no_sl_tp``, cancelled by
the executor's own containment in the same cycle, then recorded
``skipped: unconfirmed_submit`` with nothing saying whether a position had
existed.
"""
from __future__ import annotations

import pytest

from src.prop import prop_executor as pe
from src.prop.platform.dxtrade_api import DXtradeApiAdapter, client_ids
from tests.test_prop_executor import NOW, SPEC, FakeAdapter, FakeApi, run
from tests.test_prop_platform_dxtrade_api import BASE, A, FakeServer

EID = client_ids("t1")["entry"]


@pytest.fixture
def env(tmp_path):
    ledger = pe.IntentLedger(tmp_path / "ledger.jsonl")
    state = pe.ExecutorState(tmp_path)
    state.save({"day": pe.trading_day(NOW), "day_start_captured": 5000.0})
    return ledger, state


class HistAdapter(FakeAdapter):
    """A terminal that also answers ``read_history`` (the DXtrade REST shape)."""

    def __init__(self, history=None, history_error=None, **kw):
        super().__init__(**kw)
        self.history, self.history_error = history, history_error
        self.history_calls = []

    def read_history(self, client_order_ids):
        self.history_calls.append(list(client_order_ids))
        if self.history_error:
            raise RuntimeError(self.history_error)
        return list(self.history or [])


def _h(status, filled=None, price=None, final=True, cid=EID):
    return {"client_id": cid, "status": status, "final": final, "filled_qty": filled,
            "avg_price": price, "fills": [], "position_effect": "OPEN"}


def _submitted(ledger, **extra):
    ledger.record("t1", "submitted", spec=SPEC, detail="http 200", **extra)


def test_cancelled_entry_is_skipped_with_the_venue_reason_on_the_first_miss(env):
    ledger, _ = env
    _submitted(ledger, leg_fix_tried=True)
    api = FakeApi()
    ad = HistAdapter(history=[_h("CANCELED", filled=0.0)])
    res = run(ad, api, env)
    row = ledger.latest()["t1"]
    assert row["state"] == "skipped" and row["resolved_by"] == "venue_history"
    assert "CANCELED with nothing filled" in row["reason"] and "executor's own containment" in row["reason"]
    assert "unconfirmed_submit" not in row["reason"]
    assert [p["status"] for p in api.posts if p.get("kind") == "fill"] == ["skipped"]
    assert ad.history_calls == [[EID]]
    assert not any("unconfirmed placement" in f for f in res.failures)
    assert not any(c[0] in ("cancel_order", "flatten", "modify_bracket") for c in ad.calls)  # reads only


def test_filled_then_gone_is_journaled_open_at_the_venue_fill(env):
    ledger, _ = env
    _submitted(ledger)
    api = FakeApi()
    res = run(HistAdapter(history=[_h("COMPLETED", filled=0.5, price=120.4)]), api, env)
    row = ledger.latest()["t1"]
    assert row["state"] == "open" and row["fill_price"] == 120.4
    fills = [p for p in api.posts if p.get("kind") == "fill"]
    assert fills[-1]["status"] == "open" and fills[-1]["entry_price"] == 120.4
    assert any("FILLED" in a for a in res.alerts)


def test_absent_resolves_only_after_the_miss_budget(env):
    ledger, _ = env
    _submitted(ledger)
    ad = HistAdapter(history=[])
    run(ad, FakeApi(), env)
    assert ledger.state("t1") == "unconfirmed"          # a fresh POST may not be listed yet
    run(ad, FakeApi(), env)
    run(ad, FakeApi(), env)
    row = ledger.latest()["t1"]
    assert row["state"] == "skipped" and "no order under this ticket's client id" in row["reason"]


@pytest.mark.parametrize("ad", [HistAdapter(history_error="http 503"),
                                HistAdapter(history=[_h("WORKING", final=False)])])
def test_unread_or_working_is_never_written_off(env, ad):
    ledger, _ = env
    _submitted(ledger)
    for _ in range(5):
        res = run(ad, FakeApi(), env)
    assert ledger.state("t1") == "unconfirmed"          # still watched, asked again every cycle
    assert any("kept unresolved" in a for a in res.alerts)
    assert len(ad.history_calls) == 5


def test_adapter_without_history_keeps_the_old_path(env):
    ledger, _ = env
    _submitted(ledger)
    for _ in range(3):
        run(FakeAdapter(), FakeApi(), env)
    assert ledger.latest()["t1"]["reason"] == "unconfirmed_submit"


def test_venue_entry_outcome_over_the_rest_adapter():
    srv = FakeServer()
    srv.on(("GET", A + "/orders/history"), 200, {"orders": [
        {"clientOrderId": EID, "status": "CANCELED", "finalStatus": True, "side": "SELL",
         "legs": [{"positionEffect": "OPEN", "filledQuantity": 0}], "executions": []}]})
    a = DXtradeApiAdapter(transport=srv, sleep=lambda s: None)
    a.login(None, BASE, "trader", "pw")
    assert pe.venue_entry_outcome(a, {"ticket_id": "t1"}) == {"outcome": "never_filled", "status": "CANCELED"}
    assert "with-client-id=" + EID in srv.sent("GET", "/orders/history")[-1]["url"]
    # A failed read is "could not look", never "the venue has no such order".
    srv.on(("GET", A + "/orders/history"), 500, {"errorCode": 1, "description": "boom"})
    assert pe.venue_entry_outcome(a, {"ticket_id": "t1"})["outcome"] == "unread"
