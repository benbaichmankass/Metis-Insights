"""The ranking-key arms must be the arms they claim to be.

WHAT THIS PINS, AND WHY EACH ASSERTION EARNS ITS PLACE
------------------------------------------------------
The trade-prioritisation A/B is only worth running if each arm actually IS its
declared arm. Three ways it could silently not be, all of which have precedent
in this repo:

1. **The baseline is a copy.** `confidence_first` must BE
   `src.runtime.intents._election_sort_key`, by identity. A hand-written
   "shipped key" would be free to drift from the key that runs, and the A/B
   would measure the copy — the two-expressions-of-one-ordering failure the
   `intents.py` election-key comments record in detail.
2. **`decided_by` names a term the arm does not carry.** `deciding_term` zips
   the key tuple against a module-level name tuple; an arm that reorders one
   without the other emits a `decided_by` naming the WRONG term. That is
   unprovenanced diagnostic output (sub-class A) in the exact field the whole
   result is stratified by.
3. **The control peeks.** A `random_tiebreak` that reads confidence, priority
   or the track record is not a control, and the arm it would flatter is the
   one already live on real money.

Plus the two contamination hazards a replay creates: a live-journal track
record (look-ahead + irreproducible) and an arm left installed after a raising
run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts" / "research")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ranking_keys as rk  # noqa: E402
from src.runtime import intents as im  # noqa: E402
from src.runtime.intents import StrategyIntent, elect_from_gated  # noqa: E402


def _intent(strategy, *, confidence=0.5, priority=0, side="long", ts=0.0):
    return StrategyIntent(strategy=strategy, symbol="BTCUSDT", side=side,
                          target_qty=1.0, entry=100.0, sl=99.0, tp=110.0,
                          confidence=confidence, priority=priority, timestamp=ts)


def test_self_test_passes():
    assert rk.main(["ranking_keys.py", "--self-test"]) == 0


def test_shipped_arm_is_the_live_function_by_identity():
    # Not "behaves the same" — IS. A copy would pass a behavioural test on the
    # day it was written and diverge silently afterwards.
    assert rk.make_key(rk.SHIPPED_ARM) is im._election_sort_key


def test_every_declared_arm_is_constructible_and_accepts_both_branch_arities():
    a = _intent("alpha", confidence=0.7, ts=1.0)
    for arm in rk.ARMS:
        key = rk.make_key(arm, seed=11)
        assert isinstance(key(a), tuple)
        # The CONFLICT branch calls with include_target_qty=False. A mismatched
        # arity there is swallowed by a fail-permissive caller rather than
        # raising, which is how a replay silently stops measuring its own arm.
        assert isinstance(key(a, include_target_qty=False), tuple)


def test_unknown_arm_is_refused_not_defaulted():
    with pytest.raises(ValueError):
        rk.make_key("confidence_second")


@pytest.mark.parametrize("arm", rk.ARMS)
def test_decided_by_only_ever_names_a_term_the_arm_carries(arm):
    a = _intent("alpha", confidence=0.9, ts=1.0)
    b = _intent("bravo", confidence=0.4, ts=2.0)
    tr = rk.ReplayTrackRecord()
    tr.set_now(86400.0)
    with rk.install_ranking_key(arm, seed=5, track_record=tr):
        d = elect_from_gated((a, b))
        term = d.meta["decided_by"]
    allowed = set(rk.ARM_TERMS[arm]) | {
        im.ELECTION_TERM_UNCONTESTED, im.ELECTION_TERM_UNKNOWN,
    }
    if arm != rk.CONTROL_ARM:
        # Every arm but the control genuinely carries `-target_qty` on the
        # reinforcement branch, so that term is a legitimate `decided_by`.
        allowed.add(im.ELECTION_TERM_TARGET_QTY)
    assert term in allowed, f"{arm} reported decided_by={term!r}, which it does not rank on"


def test_the_control_can_never_report_target_qty_as_the_deciding_term():
    """The mislabel this test exists for, MEASURED before it was fixed.

    `deciding_term` names terms by ZIPPING `(target_qty,) + ELECTION_TERMS`
    against the key tuple. A control that omitted the `target_qty` slot
    misaligned the two, and its random draw was reported as `decided_by=
    target_qty` on 100 of 208 contested trades in the 2026-09-08 primary run —
    unprovenanced diagnostic output in the exact field the analysis stratifies
    by. The fix is a CONSTANT placeholder in that slot, which realigns the
    names and, being constant, can never itself decide.
    """
    tr = rk.ReplayTrackRecord()
    tr.set_now(86400.0)
    seen = set()
    for k in range(40):
        a = _intent(f"s{k}a", confidence=0.9, ts=float(k))
        b = _intent(f"s{k}b", confidence=0.9, ts=float(k))
        with rk.install_ranking_key(rk.CONTROL_ARM, seed=k, track_record=tr):
            # Same side => the REINFORCEMENT branch, which is the one that
            # prepends `target_qty` to the name list.
            seen.add(elect_from_gated((a, b)).meta["decided_by"])
    assert seen == {"random"}, (
        f"the control reported {sorted(seen)} as deciding terms; it ranks on "
        f"nothing but a seeded digest, so anything else is a mislabel")


def test_control_reads_no_evidence_term():
    key = rk.make_key(rk.CONTROL_ARM, seed=3)
    plain = _intent("alpha", confidence=0.5, priority=0, ts=1.0)
    loaded = _intent("alpha", confidence=1.0, priority=99, ts=1.0)
    assert key(plain) == key(loaded), (
        "the control must be blind to confidence AND declared priority; a "
        "control that peeks flatters the arm that is already live on real money")


def test_control_is_reproducible_seed_sensitive_and_rerolls_per_tick():
    a1 = _intent("alpha", ts=1.0)
    a2 = _intent("alpha", ts=2.0)
    k_a, k_b = rk.make_key(rk.CONTROL_ARM, seed=1), rk.make_key(rk.CONTROL_ARM, seed=2)
    assert k_a(a1) == k_a(a1)          # reproducible: a control nobody can re-run is not evidence
    assert k_a(a1) != k_b(a1)          # the seed is real
    assert k_a(a1) != k_a(a2)          # re-rolls per tick, else it is a NAME ranking in disguise


def test_priority_first_ranks_declared_priority_above_confidence():
    low_conf_high_pri = _intent("pri", confidence=0.1, priority=5, ts=9.0)
    high_conf = _intent("conf", confidence=0.99, priority=0, ts=1.0)
    with rk.install_ranking_key("priority_first"):
        d = elect_from_gated((high_conf, low_conf_high_pri))
        assert d.winning_intent.strategy == "pri"
        assert d.meta["decided_by"] == "declared_priority"
    # ...and the shipped arm reaches the opposite conclusion on the same pair,
    # which is what makes the two arms a comparison rather than a formality.
    with rk.install_ranking_key(rk.SHIPPED_ARM):
        d = elect_from_gated((high_conf, low_conf_high_pri))
        assert d.winning_intent.strategy == "conf"


def test_recent_pnl_first_ranks_track_record_above_confidence():
    a = _intent("alpha", confidence=0.9, ts=1.0)
    b = _intent("bravo", confidence=0.2, ts=2.0)
    tr = rk.ReplayTrackRecord(window_days=3.0)
    tr.set_now(86400.0 * 10)
    tr.record_close(strategy="bravo", pnl=1000.0, exit_epoch_s=86400.0 * 9)
    with rk.install_ranking_key("recent_pnl_first", track_record=tr):
        d = elect_from_gated((a, b))
    assert d.winning_intent.strategy == "bravo"
    assert d.meta["decided_by"] == "recent_pnl"


def test_replay_track_record_keeps_the_live_three_states_and_the_inf_semantics():
    tr = rk.ReplayTrackRecord(window_days=3.0)
    tr.set_now(86400.0 * 10)
    tr.record_close(strategy="graded", pnl=-50.0, exit_epoch_s=86400.0 * 9)
    # An UNGRADED strategy sorts LAST, never above a LOSING record. A 0.0 here
    # would assert an observation nobody made.
    assert tr.rank("ungraded") == float("inf")
    assert tr.rank("graded") == 50.0
    assert tr.state_for("ungraded") == rk.NO_TRADES
    assert tr.state_for("graded") == rk.MEASURED


def test_track_record_window_is_measured_from_the_REPLAY_clock():
    # A window anchored to wall-clock `now` would grade 2024 closes against a
    # 2026 cutoff and read permanently empty — the tier-3 term silently inert.
    tr = rk.ReplayTrackRecord(window_days=3.0)
    tr.set_now(86400.0 * 10)
    tr.record_close(strategy="stale", pnl=999.0, exit_epoch_s=86400.0 * 1)
    assert tr.rank("stale") == float("inf")
    tr.set_now(86400.0 * 2)
    assert tr.rank("stale") == -999.0


def test_installing_an_arm_repoints_the_track_record_off_the_live_journal():
    tr = rk.ReplayTrackRecord()
    before = im._track_record_rank
    with rk.install_ranking_key(rk.SHIPPED_ARM, track_record=tr):
        assert getattr(im._track_record_rank, "__self__", None) is tr, (
            "the shipped arm's tier-3 term must read the REPLAY's closes, not "
            "the live trader's trade_journal.db — that would be both look-ahead "
            "and a dependency on one machine's DB file")
    assert im._track_record_rank is before


def test_the_arm_is_restored_even_when_the_replay_raises():
    key, terms, rank = im._election_sort_key, im.ELECTION_TERMS, im._track_record_rank
    with pytest.raises(RuntimeError):
        with rk.install_ranking_key(rk.CONTROL_ARM):
            raise RuntimeError("replay exploded")
    assert im._election_sort_key is key
    assert im.ELECTION_TERMS is terms
    assert im._track_record_rank is rank


def test_the_four_declared_arms_are_exactly_the_designs_four():
    # Pinned against docs/research/trade-prioritisation-research-DESIGN.yaml.
    # An arm silently added or dropped changes the multiplicity correction the
    # reporter declares (alpha/3) without anything saying so.
    assert rk.ARMS == ("confidence_first", "priority_first",
                       "recent_pnl_first", "random_tiebreak")
    assert rk.SHIPPED_ARM == "confidence_first"
    assert rk.CONTROL_ARM == "random_tiebreak"


def test_the_boundary_is_runtime_patching_not_a_source_edit():
    # The Tier-3 boundary, asserted rather than promised: after any number of
    # installs the live module's own attributes are the ones it shipped with,
    # so nothing this harness does can outlive its own run.
    key0, terms0, rank0 = im._election_sort_key, im.ELECTION_TERMS, im._track_record_rank
    for arm in rk.ARMS:
        with rk.install_ranking_key(arm, seed=1, track_record=rk.ReplayTrackRecord()):
            pass
    assert im._election_sort_key is key0
    assert im.ELECTION_TERMS is terms0
    assert im._track_record_rank is rank0
