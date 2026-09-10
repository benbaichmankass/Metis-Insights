"""Tests for MI-216's per-leg offline edge producer.

The load-bearing properties are the ones that keep a WEAK number from reading as
a strong one: the fingerprint must move when the config moves, the folds must be
contiguous in time, and the four coverage states must stay apart. A pooled net-R
with none of those is exactly the kind of confident number this whole work stream
exists to stop trusting.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "build_strategy_evidence", ROOT / "scripts" / "ops" / "build_strategy_evidence.py"
)
bse = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(bse)


# ---------------------------------------------------------------------------
# config_fingerprint — an edge record is evidence about the leg AS CONFIGURED.
# ---------------------------------------------------------------------------

def test_fingerprint_moves_when_a_param_moves():
    """THE load-bearing property. If this fails, a stale record reads as current."""
    a = bse.config_fingerprint({"atr_stop_mult": 2.0, "symbols": ["ETHUSDT"]})
    b = bse.config_fingerprint({"atr_stop_mult": 2.5, "symbols": ["ETHUSDT"]})
    assert a != b


def test_fingerprint_is_stable_under_key_reordering():
    """Formatting churn must not invalidate every record in the fleet."""
    a = bse.config_fingerprint({"a": 1, "b": 2})
    b = bse.config_fingerprint({"b": 2, "a": 1})
    assert a == b


def test_fingerprint_is_stable_across_calls():
    cfg = {"donchian": 20, "symbols": ["BTCUSDT"], "timeframe": "4h"}
    assert bse.config_fingerprint(cfg) == bse.config_fingerprint(cfg)


# ---------------------------------------------------------------------------
# time_folds — contiguous and time-ordered, or the folds leak into each other.
# ---------------------------------------------------------------------------

def _t(day: int, r: float) -> dict:
    return {"entry_time": f"2026-01-{day:02d} 00:00:00+00:00", "net_r": r}


def test_folds_are_contiguous_and_time_ordered():
    trades = [_t(d, 1.0) for d in range(1, 13)]
    fd = bse.time_folds(list(reversed(trades)), 4)  # unsorted input on purpose
    assert len(fd) == 4
    assert [f["trades"] for f in fd] == [3, 3, 3, 3]
    ends = [f["end"] for f in fd]
    starts = [f["start"] for f in fd]
    assert starts == sorted(starts), "folds must be in time order"
    for i in range(len(fd) - 1):
        assert ends[i] < starts[i + 1], "folds must not overlap in time"


def test_fold_net_r_sums_to_the_pooled_total():
    """Arithmetic cross-check — the repo's own rule about catching what re-reading misses."""
    trades = [_t(d, r) for d, r in zip(range(1, 9), [1, -2, 3, -4, 5, -6, 7, -8])]
    fd = bse.time_folds(trades, 4)
    assert round(sum(f["net_r"] for f in fd), 6) == round(sum(t["net_r"] for t in trades), 6)


def test_folds_positive_distinguishes_one_good_block_from_four():
    """A pooled positive carried by ONE fold is not the same fact as four positives.

    Measured on the first real record: eth_pullback_2h pooled +3.3242 with only
    2 of 4 folds positive (+6.32, -2.92, +2.77, -2.85). The pooled number alone
    cannot tell those apart, which is why folds_positive is published.
    """
    lopsided = bse.time_folds([_t(d, r) for d, r in
                               zip(range(1, 9), [10, 10, -1, -1, -1, -1, -1, -1])], 4)
    assert sum(1 for f in lopsided if f["net_r"] > 0) == 1

    even = bse.time_folds([_t(d, 1.0) for d in range(1, 9)], 4)
    assert sum(1 for f in even if f["net_r"] > 0) == 4


def test_no_trades_yields_no_folds_not_a_zero_fold():
    """Zero trades is an ABSENCE of measurement, never an edge of zero."""
    assert bse.time_folds([], 4) == []


def test_undated_trades_are_dropped_not_dated_to_now():
    """A trade with no entry_time cannot be placed in a fold; guessing would fabricate."""
    fd = bse.time_folds([{"net_r": 5.0}, _t(1, 1.0), _t(2, 1.0)], 2)
    assert sum(f["trades"] for f in fd) == 2


# ---------------------------------------------------------------------------
# The states, and the contract that keeps them apart.
# ---------------------------------------------------------------------------

def test_four_coverage_states_and_basis_is_not_purged_walkforward():
    """`basis` must not borrow a term that means something stricter elsewhere.

    ml/promotion/oos_edge.py does purged WF-CV. These folds are emitted trades
    split after the fact. Naming them the same would be the unprovenanced
    -diagnostic failure this repo has a guard family for.
    """
    assert set(bse.COVERAGE_STATES) == {
        "measured", "no_harness", "harness_failed", "not_attempted"}
    assert bse.BASIS == "harness_timefolds"
    assert "purged" not in bse.BASIS


def test_record_for_an_unclassifiable_leg_is_no_harness_and_carries_no_number():
    """`no_harness` must never leak a pooled number — there is nothing to pool."""
    rec = bse.build_record("fake_leg", {"symbols": ["XXXUSDT"], "timeframe": "1h"},
                           workdir="/tmp", days=5, folds=2)
    assert rec["coverage_state"] == "no_harness"
    assert rec["net_r_oos"] is None
    assert rec["n_trades_oos"] is None
    assert rec["error"] and "classify" in rec["error"]


def test_unclassifiable_record_still_carries_a_fingerprint():
    """A leg we could not measure is still a leg whose config we can identify."""
    rec = bse.build_record("fake_leg", {"symbols": ["XXXUSDT"], "timeframe": "1h"},
                           workdir="/tmp", days=5, folds=2)
    assert rec["config_fingerprint"].startswith("sha256:")


def test_param_provenance_is_declared_unestablished():
    """The folds are OOS w.r.t. each other; that is not the same as params chosen OOS.

    If a leg was tuned on this history the pooled number is optimistic and nothing
    here can detect it — so the record says we did not establish it rather than
    letting `net_r_oos` imply we did.
    """
    rec = bse.build_record("fake_leg", {"symbols": ["XXXUSDT"], "timeframe": "1h"},
                           workdir="/tmp", days=5, folds=2)
    assert rec["param_selection_provenance"] == "not_established"
