"""The re-assert path is EXECUTED here, not inspected.

Earlier today a source-inspection test in this same cluster passed straight
over a `NameError` in the branch it claimed to cover — ruff's F821 caught it, a
test should have. So every test below actually runs
`order_monitor._reassert_from_divergence` against a fake client and asserts on
what it did, including the negative cases where it must do nothing.
"""
from __future__ import annotations

import json

import pytest

from src.runtime import order_monitor as om
from src.runtime.protection_price import grade_protection_price

DECLARED_SL = 7533.69642857
DECLARED_TP = 8390.59025


class _FakeClient:
    def __init__(self, ret=None, raises=False):
        self.calls = []
        self._ret = ret if ret is not None else {"retCode": 0, "result": {"orderId": 1}}
        self._raises = raises

    def modify_protective(self, order):
        self.calls.append(order)
        if self._raises:
            raise RuntimeError("gateway wedged")
        return self._ret


def _row(**over):
    r = {"id": 4350, "account_id": "ib_paper", "symbol": "MES",
         "direction": "long", "position_size": 15.0,
         "stop_loss": DECLARED_SL, "take_profit_1": DECLARED_TP}
    r.update(over)
    return r


def _verdict(resting=7516.5):
    return grade_protection_price(
        declared=DECLARED_SL, resting_prices=[resting], direction="long",
        side="stop", tick_size=0.25)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Fresh soak file and fresh cooldown state per test."""
    soak = tmp_path / "protection_reassert_soak.jsonl"
    monkeypatch.setattr(om, "_reassert_soak_path", lambda: str(soak))
    om._REASSERT_STATE.clear()
    for k in ("PROTECTION_REASSERT_MODE", "PROTECTION_REASSERT_ACCOUNTS",
              "PROTECTION_REASSERT_COOLDOWN_S", "PROTECTION_REASSERT_MAX_ATTEMPTS"):
        monkeypatch.delenv(k, raising=False)
    yield soak
    om._REASSERT_STATE.clear()


def _run(client, *, row=None, verdict=None, summary=None):
    summary = summary if summary is not None else {
        "stop_price_reasserted": 0, "stop_price_reassert_failed": 0,
        "stop_price_reassert_annotated": 0}
    om._reassert_from_divergence(
        db=None, client=client, account_id="ib_paper", protect_symbol="MES",
        row=row or _row(), price_verdict=verdict or _verdict(), summary=summary)
    return summary


def _soak(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestDefaultIsObserveOnly:
    def test_with_NO_env_set_nothing_is_placed(self, _isolate):
        """The shipped default must not touch a live bracket."""
        c = _FakeClient()
        s = _run(c)
        assert c.calls == []
        assert s["stop_price_reassert_annotated"] == 1
        assert s["stop_price_reasserted"] == 0

    def test_the_annotate_row_still_records_the_full_decision(self, _isolate):
        c = _FakeClient()
        _run(c)
        rows = _soak(_isolate)
        assert len(rows) == 1
        r = rows[0]
        assert r["decision"] == "reassert", (
            "the DECISION is 'act'; only the mode held it back — collapsing "
            "those two would make a staged run look like a clean one")
        assert r["acted"] is False
        assert r["mode"] == "annotate"
        assert r["apply_scope"] == "not_allowlisted"
        assert r["declared_sl"] == DECLARED_SL
        assert r["resting_stop"] == 7516.5
        assert round(r["ticks"]) == 69
        assert r["exposure"] == "more_exposed"


class TestApplyPath:
    def test_apply_on_an_allowlisted_account_sends_BOTH_declared_levels(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        c = _FakeClient()
        s = _run(c)
        assert len(c.calls) == 1
        sent = c.calls[0]
        assert sent["sl"] == DECLARED_SL and sent["tp"] == DECLARED_TP, (
            "modify_protective re-arms the WHOLE bracket — sending one leg "
            "would drop the other")
        assert sent["symbol"] == "MES"
        assert sent["direction"] == "long", "the POSITION's side, not an order side"
        assert sent["qty"] == 15.0
        assert s["stop_price_reasserted"] == 1

    def test_it_sends_the_JOURNAL_level_not_the_venue_level(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        c = _FakeClient()
        _run(c)
        assert c.calls[0]["sl"] == DECLARED_SL
        assert c.calls[0]["sl"] != 7516.5

    def test_apply_on_a_NON_allowlisted_account_places_nothing(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "bybit_1")
        c = _FakeClient()
        s = _run(c)
        assert c.calls == []
        assert s["stop_price_reassert_annotated"] == 1
        assert _soak(_isolate)[0]["apply_scope"] == "not_allowlisted"

    def test_an_UNSET_allowlist_places_nothing_even_at_apply(
            self, monkeypatch, _isolate):
        """⚠️ Empty means NONE here, unlike its siblings where empty means ALL.
        An unset variable must not arm an order path."""
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        c = _FakeClient()
        assert _run(c) and c.calls == []

    def test_a_broker_refusal_is_counted_as_failed_not_success(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        c = _FakeClient(ret={"retCode": 1, "retMsg": "IB connect failed"})
        s = _run(c)
        assert s["stop_price_reassert_failed"] == 1
        assert s["stop_price_reasserted"] == 0
        assert _soak(_isolate)[0]["applied_ok"] is False


class TestItNeverBreaksTheSweep:
    def test_a_raising_client_does_not_propagate(self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        _run(_FakeClient(raises=True))  # must not raise

    def test_an_agreeing_leg_places_nothing(self, _isolate, monkeypatch):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        c = _FakeClient()
        _run(c, verdict=_verdict(resting=7533.75))
        assert c.calls == []
        assert _soak(_isolate)[0]["decision"] == "agrees"

    def test_a_missing_declared_target_refuses_rather_than_half_arming(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        c = _FakeClient()
        _run(c, row=_row(take_profit_1=None))
        assert c.calls == []
        assert _soak(_isolate)[0]["decision"] == "needs_both_legs"


class TestBounding:
    def test_a_second_sweep_inside_the_cooldown_does_not_re_place(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        c = _FakeClient()
        for _ in range(3):
            _run(c)
        assert len(c.calls) == 1, (
            "modify_protective is a real cancel-and-re-place; a per-sweep "
            "retry is churn on a live bracket, not a retry")
        assert _soak(_isolate)[-1]["decision"] == "suppressed_cooldown"

    def test_a_zero_cooldown_still_stops_at_the_attempt_budget(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        monkeypatch.setenv("PROTECTION_REASSERT_COOLDOWN_S", "0")
        c = _FakeClient()
        for _ in range(6):
            _run(c)
        assert len(c.calls) == 3, "the default budget is 3 attempts per key"
        assert _soak(_isolate)[-1]["decision"] == "suppressed_attempts"

    def test_an_unparseable_knob_falls_back_to_the_default_not_to_zero(
            self, monkeypatch, _isolate):
        """A typo must not silently remove the bound."""
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        monkeypatch.setenv("PROTECTION_REASSERT_COOLDOWN_S", "one hour")
        c = _FakeClient()
        for _ in range(2):
            _run(c)
        assert len(c.calls) == 1


class TestModeIsNotAnEnableGate:
    def test_mode_off_places_nothing_and_still_records(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "off")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        c = _FakeClient()
        _run(c)
        assert c.calls == []
        assert _soak(_isolate)[0]["mode"] == "off"

    def test_a_typo_in_the_mode_does_NOT_arm_apply(
            self, monkeypatch, _isolate):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "aply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        c = _FakeClient()
        _run(c)
        assert c.calls == []
        assert _soak(_isolate)[0]["mode"] == "annotate"
