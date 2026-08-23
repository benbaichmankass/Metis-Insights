"""The re-assert DECISION — pure, so the policy is testable without a broker.

Operator directive 2026-08-23: the pipeline must be able to adjust an IB trade
without disconnecting the integration. `IBClient.modify_protective` already
could; nothing ever CALLED it on a venue/journal divergence, because
`interpret_verdict` compares intent to `order_packages.sl` and never to the
venue (BL-20260823-MODIFY-IDEMPOTENCE-COMPARES-INTENT-TO-JOURNAL-NEVER-TO-VENUE).

These tests pin the policy that decides when it fires. The 2026-08-20
remediation went wrong precisely because the decision was made ad hoc against a
live position; making it a pure function is what lets it be argued about here
instead of there.
"""
from __future__ import annotations

import pytest

from src.runtime.protection_price import grade_protection_price
from src.runtime.protection_reassert import (
    DEFAULT_COOLDOWN_S, MODE_ANNOTATE, MODE_APPLY, MODE_OFF,
    STATE_AGREES, STATE_NEEDS_BOTH_LEGS, STATE_NOT_GRADED,
    STATE_POSITION_ABSENT, STATE_REASSERT, STATE_SUPPRESSED_ATTEMPTS,
    STATE_SUPPRESSED_COOLDOWN, account_may_apply, decide_reassert,
    resolve_mode,
)

MES_TICK = 0.25
DECLARED_SL = 7533.69642857
DECLARED_TP = 8390.59025


def _verdict(resting, *, declared=DECLARED_SL, direction="long", tick=MES_TICK):
    return grade_protection_price(
        declared=declared, resting_prices=resting, direction=direction,
        side="stop", tick_size=tick)


def _decide(**kw):
    base = dict(price_verdict=_verdict([7516.5]), declared_sl=DECLARED_SL,
                declared_tp=DECLARED_TP, position_size=15.0, trade_is_open=True)
    base.update(kw)
    return decide_reassert(**base)


class TestTheLiveCase:
    def test_MES_4350_decides_to_reassert_both_declared_levels(self):
        d = _decide()
        assert d["state"] == STATE_REASSERT
        assert d["levels"] == {"sl": DECLARED_SL, "tp": DECLARED_TP}
        assert round(d["ticks"]) == 69
        assert d["exposure"] == "more_exposed"

    def test_the_levels_are_the_JOURNAL_S_not_the_venue_s(self):
        """The whole rule of BL-20260820's criterion 2 in one assertion.

        A re-assert must move the VENUE to the DECLARATION, never record the
        venue's level as the new declaration — that would launder a stray leg
        into the journal and make the divergence unfindable.
        """
        d = _decide()
        assert d["levels"]["sl"] == DECLARED_SL
        assert d["levels"]["sl"] != 7516.5


class TestAgreementAndAbstention:
    def test_a_matching_leg_does_not_reassert(self):
        d = _decide(price_verdict=_verdict([7533.75]))
        assert d["state"] == STATE_AGREES
        assert d["levels"] is None

    @pytest.mark.parametrize("verdict,why", [
        (None, "grading never ran"),
        ({"state": "no_tick_size"}, "the tick could not be resolved"),
        ({"state": "no_resting_price"}, "a leg rests but its price is unreadable"),
        ({"state": "no_declared_level"}, "the journal declares nothing"),
    ])
    def test_an_undecided_verdict_is_NOT_agreement(self, verdict, why):
        d = _decide(price_verdict=verdict)
        assert d["state"] == STATE_NOT_GRADED, why
        assert d["levels"] is None

    def test_no_resting_leg_is_left_to_the_NAKED_sweep(self):
        """A position with nothing resting is naked, not diverged.

        Treating it here would double-count one condition as two and race the
        naked sweep's own re-arm on the same position.
        """
        d = _decide(price_verdict=_verdict([]))
        assert d["state"] == STATE_NOT_GRADED
        assert d["levels"] is None


class TestRefusals:
    def test_a_flat_or_closed_trade_never_gets_protection_reasserted(self):
        """Placing a resting stop with no position behind it is the naked
        -reverse hazard, reached from the other direction."""
        for kw in (dict(trade_is_open=False), dict(position_size=0.0),
                   dict(position_size=None), dict(position_size="")):
            assert _decide(**kw)["state"] == STATE_POSITION_ABSENT

    def test_the_position_check_runs_BEFORE_the_price_check(self):
        """Order matters: a flat book must refuse even on a clean divergence,
        not fall through to a re-assert because the prices happen to differ."""
        d = _decide(trade_is_open=False)
        assert d["state"] == STATE_POSITION_ABSENT

    @pytest.mark.parametrize("sl,tp", [
        (DECLARED_SL, None), (None, DECLARED_TP), (None, None),
        (DECLARED_SL, 0.0), (0.0, DECLARED_TP),
    ])
    def test_one_declared_leg_is_a_REFUSAL_not_a_half_reassert(self, sl, tp):
        """`modify_protective` re-arms the WHOLE bracket — its own docstring
        says re-arming only the changed leg leaves the position half
        protected. So a missing declared level must refuse, never proceed."""
        d = _decide(declared_sl=sl, declared_tp=tp)
        assert d["state"] == STATE_NEEDS_BOTH_LEGS
        assert d["levels"] is None


class TestBounding:
    def test_a_recent_attempt_suppresses_the_next(self):
        d = _decide(seconds_since_last_attempt=60.0)
        assert d["state"] == STATE_SUPPRESSED_COOLDOWN

    def test_an_elapsed_cooldown_allows_it_again(self):
        d = _decide(seconds_since_last_attempt=DEFAULT_COOLDOWN_S + 1)
        assert d["state"] == STATE_REASSERT

    def test_a_first_ever_attempt_is_not_suppressed(self):
        assert _decide(seconds_since_last_attempt=None)["state"] == STATE_REASSERT

    def test_the_attempt_budget_hands_it_to_a_human(self):
        """A re-assert that keeps failing is a fault whose cause is not the
        level. Hammering it would be the desensitised alarm in order form."""
        d = _decide(attempts_so_far=3, seconds_since_last_attempt=99999)
        assert d["state"] == STATE_SUPPRESSED_ATTEMPTS

    def test_attempts_are_checked_before_the_cooldown(self):
        """Otherwise an exhausted key reports 'come back later' forever
        instead of 'this needs a person'."""
        d = _decide(attempts_so_far=3, seconds_since_last_attempt=1.0)
        assert d["state"] == STATE_SUPPRESSED_ATTEMPTS

    def test_a_zero_budget_refuses_every_attempt(self):
        assert _decide(max_attempts=0)["state"] == STATE_SUPPRESSED_ATTEMPTS


class TestMode:
    @pytest.mark.parametrize("raw,want", [
        ("off", MODE_OFF), ("annotate", MODE_ANNOTATE), ("apply", MODE_APPLY),
        ("  APPLY  ", MODE_APPLY), ("Off", MODE_OFF),
    ])
    def test_recognised_values(self, raw, want):
        assert resolve_mode(raw) == want

    @pytest.mark.parametrize("raw", [None, "", "   ", "aply", "1", "true", "on"])
    def test_an_unparseable_value_falls_back_to_ANNOTATE(self, raw):
        """Not `off` — a typo must not silently stop the observation.
        Not `apply` — and certainly must not switch a live order path on."""
        assert resolve_mode(raw) == MODE_ANNOTATE


class TestAllowlist:
    def test_an_EMPTY_allowlist_means_NONE(self):
        """⚠️ Deliberately the OPPOSITE of CONVICTION_SIZING_ACCOUNTS and
        NETTING_ATTRIBUTION_ACCOUNTS, where empty means ALL.

        Those widen a size and a DB write. This one cancels and re-places a
        live position's exit, so inheriting the convention would mean an unset
        variable arms an order path on every account including real money.
        """
        for allow in (None, "", "   ", ",", " , , "):
            assert account_may_apply("ib_paper", allow) is False

    def test_only_a_named_account_may_apply(self):
        assert account_may_apply("ib_paper", "ib_paper") is True
        assert account_may_apply("bybit_2", "ib_paper") is False
        assert account_may_apply("ib_paper", "ib_paper, bybit_1") is True

    def test_whitespace_and_blank_entries_do_not_widen_it(self):
        assert account_may_apply("ib_paper", " ib_paper , ") is True
        assert account_may_apply("", "ib_paper") is False
        assert account_may_apply(None, "ib_paper") is False

    def test_a_substring_is_not_a_match(self):
        """`ib` must not arm `ib_paper` and `ib_live` at once."""
        assert account_may_apply("ib_paper", "ib") is False
        assert account_may_apply("ib_live", "ib_paper") is False


class TestIdempotenceFilterAndReassertAreTwoSided:
    """`BL-20260823-MODIFY-IDEMPOTENCE...` criterion 5, both halves at once.

    The criterion is explicit that this is a PAIR: *"a build that re-asserts on
    every pass is as broken as one that never does, and would re-amend every leg
    on every tick."* Testing only the divergence half would pass on a build that
    re-asserts unconditionally, which is the more dangerous failure — it puts a
    real cancel-and-re-place on every live bracket every pass.

    The two halves live in different modules on purpose (see
    `interpret_verdict`'s docstring): the filter must NOT learn to read the
    venue, because it runs per open position per pass. So this pins the seam
    rather than either side alone.
    """

    def test_agreeing_pair_is_still_filtered_out(self):
        from src.runtime.monitor_verdict import interpret_verdict
        d = interpret_verdict({"sl": 7533.69642857}, current_sl=7533.69642857)
        assert d.rejection == "no_meaningful_change"
        assert d.sl is None

    def test_a_diverging_pair_still_produces_a_modify(self):
        from src.runtime.monitor_verdict import interpret_verdict
        d = interpret_verdict({"sl": 7533.69642857}, current_sl=7516.5)
        assert d.kind == "modify"
        assert d.sl == 7533.69642857

    def test_the_reassert_fires_on_the_divergence_the_filter_cannot_see(self):
        """The MES 4350 shape: journal and venue disagree, so the strategy
        recomputes the journal's OWN level and the filter deletes it. The
        divergence is invisible from `interpret_verdict`; the re-assert is what
        sees it, and it must decide `reassert` on exactly this input."""
        from src.runtime.monitor_verdict import interpret_verdict
        declared, resting = 7533.69642857, 7516.5

        # What the modify path sees when the strategy recomputes its own level:
        assert interpret_verdict(
            {"sl": declared}, current_sl=declared).rejection == "no_meaningful_change"

        # Grade the real divergence through the shared comparator rather than
        # hand-building a verdict dict — a hand-built one can drift from what
        # grade_protection_price actually emits, which is how a test ends up
        # passing against a shape production never produces.
        out = _decide(price_verdict=_verdict([resting], declared=declared))
        assert out["state"] == STATE_REASSERT
        assert round(out["ticks"]) == 69

    def test_an_agreeing_venue_does_not_reassert(self):
        out = _decide(price_verdict=_verdict([DECLARED_SL]))
        assert out["state"] == STATE_AGREES
