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


_UNSET = object()


class _FakeClient:
    def __init__(self, ret=None, raises=False, coverage=_UNSET):
        self.calls = []
        self._ret = ret if ret is not None else {"retCode": 0, "result": {"orderId": 1}}
        self._raises = raises
        # `applied_ok` is verified by RE-READING the venue, so a fake that cannot
        # answer that read is a fake of a broken gateway, not of a working one.
        # Default: both declared legs rest (the success path).
        self._coverage = ({"stop_qty": 15.0, "target_qty": 15.0}
                          if coverage is _UNSET else coverage)

    def modify_protective(self, order):
        self.calls.append(order)
        if self._raises:
            raise RuntimeError("gateway wedged")
        return self._ret

    def protection_coverage(self, symbol):
        return self._coverage


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


class TestAppliedOkComesFromTheVenue:
    """`BL-20260823-REASSERT-REPORTS-APPLIED-OK-ON-A-HALF-ARMED-BRACKET`.

    retCode 0 proves the CALL succeeded. `place_protective` places a stop and a
    limit but returns ONE orderId, so the envelope cannot distinguish a full
    bracket from a stop-only one. Measured live: MES 4350 re-asserted with a
    declared take-profit, returned retCode 0, and the account-wide broker read
    showed a stop and no limit order anywhere.
    """

    def _state(self, cov, call_ok=True, want_tp=True, raises=False):
        from src.runtime.order_monitor import _reassert_applied_state

        class _C:
            def protection_coverage(self, symbol):
                if raises:
                    raise RuntimeError("gateway wedged")
                return cov

        return _reassert_applied_state(_C(), "MES", call_ok, want_tp=want_tp)

    def test_both_legs_resting_is_the_only_success(self):
        assert self._state({"stop_qty": 15.0, "target_qty": 15.0}) == "both_legs_resting"

    def test_stop_only_is_not_a_success(self):
        """The live MES shape. It can stop out or run; it cannot take profit."""
        assert self._state({"stop_qty": 15.0, "target_qty": 0.0}) == "stop_only"

    def test_nothing_resting_after_an_ok_call(self):
        assert self._state({"stop_qty": 0.0, "target_qty": 0.0}) == "no_legs_resting"

    def test_a_failed_read_is_unverified_never_success(self):
        """`protection_coverage` returns None on a read failure — *we could not
        look*. The sweep refuses to re-arm on an unconfirmed read; it must equally
        refuse to declare a repair on one."""
        assert self._state(None) == "unverified"
        assert self._state({"stop_qty": 15.0, "target_qty": 1.0}, raises=True) == "unverified"

    def test_an_ungraded_side_is_unverified_not_absent(self):
        """A missing target_qty means the side was not graded — which is not the
        same as a target that is not there."""
        assert self._state({"stop_qty": 15.0, "target_qty": None}) == "unverified"

    def test_a_failed_call_is_not_verified_against_the_venue(self):
        assert self._state({"stop_qty": 15.0, "target_qty": 15.0}, call_ok=False) == "call_failed"

    def test_stop_only_is_success_when_no_target_was_declared(self):
        """Only a bracket that ASKED for a target can be target-naked."""
        assert self._state({"stop_qty": 15.0, "target_qty": 0.0},
                           want_tp=False) == "both_legs_resting"

    def test_the_five_states_are_distinct(self):
        seen = {
            self._state({"stop_qty": 15.0, "target_qty": 15.0}),
            self._state({"stop_qty": 15.0, "target_qty": 0.0}),
            self._state({"stop_qty": 0.0, "target_qty": 0.0}),
            self._state(None),
            self._state({"stop_qty": 1.0, "target_qty": 1.0}, call_ok=False),
        }
        assert len(seen) == 5, seen


class TestSummaryDoesNotCollapseTheOutcomes:
    """"Could not verify" is not "failed", and a half-armed bracket is neither.

    The first version of the venue-verification fix counted all three as
    `stop_price_reassert_failed` — reproducing, in the roll-up, the exact
    collapse the applied_state split exists to remove. Caught by the existing
    wiring test going red, not by reading the diff.
    """

    def _run_with(self, monkeypatch, _isolate, coverage):
        monkeypatch.setenv("PROTECTION_REASSERT_MODE", "apply")
        monkeypatch.setenv("PROTECTION_REASSERT_ACCOUNTS", "ib_paper")
        return _run(_FakeClient(coverage=coverage))

    def test_both_legs_counts_as_reasserted(self, monkeypatch, _isolate):
        s = self._run_with(monkeypatch, _isolate, {"stop_qty": 15.0, "target_qty": 15.0})
        assert s["stop_price_reasserted"] == 1
        # `.get`: the helper's summary only gains a key when it is incremented,
        # so asserting a zero must not require the key to pre-exist.
        assert s.get("stop_price_reassert_incomplete", 0) == 0
        assert s.get("stop_price_reassert_unverified", 0) == 0

    def test_stop_only_counts_as_incomplete_not_as_success(self, monkeypatch, _isolate):
        """The live MES shape."""
        s = self._run_with(monkeypatch, _isolate, {"stop_qty": 15.0, "target_qty": 0.0})
        assert s.get("stop_price_reassert_incomplete", 0) == 1
        assert s.get("stop_price_reasserted", 0) == 0

    def test_an_unreadable_venue_counts_as_unverified_not_as_failed(
            self, monkeypatch, _isolate):
        s = self._run_with(monkeypatch, _isolate, None)
        assert s.get("stop_price_reassert_unverified", 0) == 1
        assert s.get("stop_price_reassert_failed", 0) == 0
        assert s.get("stop_price_reasserted", 0) == 0
