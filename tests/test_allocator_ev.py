"""M18 P1 — cost-aware EV scorer for the capital allocator."""
from __future__ import annotations

from src.core.signal_contract import SignalPackage
from src.runtime.allocator_ev import (
    candidate_ev_r,
    candidate_ev_score,
    compute_ev_r,
)


def _cand(side="long", entry=100.0, sl=99.0, tp=103.0, conf=0.6) -> SignalPackage:
    return SignalPackage(
        strategy_id="s", symbol="BTCUSDT", account_id="", side=side,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        timestamp_utc="2026-06-29T00:00:00+00:00",
        source_context={"confidence": conf},
    )


class TestComputeEvR:
    def test_basic_value_net_of_fee(self):
        # risk=1, reward=3 → R_target=3; p=0.6 → gross EV = 0.6*3 - 0.4*1 = 1.4
        # fee_R = (7.5/1e4)*100/1 = 0.075 → EV = 1.4 - 0.075
        ev = compute_ev_r(entry=100.0, sl=99.0, tp=103.0, p_win=0.6)
        assert abs(ev - (1.4 - 0.075)) < 1e-9

    def test_zero_fee_override(self):
        ev = compute_ev_r(entry=100.0, sl=99.0, tp=103.0, p_win=0.6, fee_bps_roundtrip=0.0)
        assert abs(ev - 1.4) < 1e-9

    def test_funding_subtracts(self):
        a = compute_ev_r(entry=100.0, sl=99.0, tp=103.0, p_win=0.6, fee_bps_roundtrip=0.0)
        b = compute_ev_r(entry=100.0, sl=99.0, tp=103.0, p_win=0.6, fee_bps_roundtrip=0.0, funding_r=0.2)
        assert abs((a - b) - 0.2) < 1e-9

    def test_fee_is_in_r_units_scales_inversely_with_stop_distance(self):
        # Isolate the fee term: EV(fee=0) − EV(fee=X) == fee_R, which grows as the
        # stop tightens (fee is a larger fraction of a smaller risk).
        def fee_component(sl):
            return (
                compute_ev_r(entry=100.0, sl=sl, tp=103.0, p_win=1.0, fee_bps_roundtrip=0.0)
                - compute_ev_r(entry=100.0, sl=sl, tp=103.0, p_win=1.0, fee_bps_roundtrip=10.0)
            )
        tight = fee_component(99.5)   # risk 0.5 → fee_R = (10/1e4)*100/0.5 = 0.2
        wide = fee_component(99.0)    # risk 1.0 → fee_R = (10/1e4)*100/1.0 = 0.1
        assert abs(tight - 0.2) < 1e-9
        assert abs(wide - 0.1) < 1e-9
        assert tight > wide

    def test_p_win_clamped(self):
        # p>1 clamped to 1 → EV = R_target - fee
        ev = compute_ev_r(entry=100.0, sl=99.0, tp=103.0, p_win=5.0, fee_bps_roundtrip=0.0)
        assert abs(ev - 3.0) < 1e-9

    def test_negative_cost_inputs_clamped_to_zero(self):
        ev = compute_ev_r(entry=100.0, sl=99.0, tp=103.0, p_win=0.6, fee_bps_roundtrip=-50.0, funding_r=-1.0)
        assert abs(ev - 1.4) < 1e-9  # negative fee/funding treated as 0

    def test_zero_risk_distance_is_none(self):
        assert compute_ev_r(entry=100.0, sl=100.0, tp=103.0, p_win=0.6) is None

    def test_bad_inputs_are_none(self):
        assert compute_ev_r(entry=None, sl=99.0, tp=103.0, p_win=0.6) is None
        assert compute_ev_r(entry=100.0, sl=99.0, tp=103.0, p_win=float("nan")) is None


class TestCandidateAdapters:
    def test_candidate_ev_r_reads_package(self):
        ev = candidate_ev_r(_cand(entry=100.0, sl=99.0, tp=103.0, conf=0.6))
        assert abs(ev - (1.4 - 0.075)) < 1e-9

    def test_score_sentinel_for_unscorable(self):
        bad = _cand()
        object.__setattr__(bad, "stop_loss", bad.entry_price)  # risk 0 → un-scorable
        assert candidate_ev_score(bad) < -1.0e8
        # a real candidate scores well above the sentinel
        assert candidate_ev_score(_cand()) > -1.0

    def test_score_never_raises_on_garbage(self):
        assert candidate_ev_score(object()) < -1.0e8

    def test_higher_ev_ranks_above_higher_raw_confidence(self):
        # A: high confidence but tiny reward (1:0.2 RR) → low/neg EV
        a = _cand(entry=100.0, sl=99.0, tp=100.2, conf=0.9)
        # B: lower confidence but big reward (1:5 RR) → higher EV
        b = _cand(entry=100.0, sl=99.0, tp=105.0, conf=0.5)
        assert candidate_ev_score(b) > candidate_ev_score(a)
