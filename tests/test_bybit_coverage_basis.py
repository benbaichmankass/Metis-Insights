"""The STAGING contract for the Bybit graded-book coverage basis.

These are the tests that make "stage it on bybit_1 first" a property of the
system rather than a sentence in a document. The policy is a pure function
precisely so it can be argued here instead of against a live position — the
lesson of BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG.
"""

from __future__ import annotations

import pytest

from src.runtime import bybit_coverage_basis as basis


# ---- mode resolution -------------------------------------------------------
@pytest.mark.parametrize("raw", ["off", "annotate", "apply"])
def test_declared_modes_round_trip(raw):
    assert basis.resolve_mode(raw) == raw


@pytest.mark.parametrize("raw", [None, "", "  ", "APPLYY", "on", "true", "1", 7])
def test_an_unparseable_mode_falls_back_to_annotate_never_off_or_apply(raw):
    """A typo must not silently switch the observation off, and must certainly
    not switch a live order path on. Both failure directions are asserted, not
    just the one that happens to be safe."""
    got = basis.resolve_mode(raw)
    assert got == basis.MODE_ANNOTATE
    assert got != basis.MODE_OFF
    assert got != basis.MODE_APPLY


def test_mode_is_case_and_whitespace_insensitive():
    assert basis.resolve_mode("  APPLY \n") == basis.MODE_APPLY


# ---- the allowlist POLARITY — the load-bearing test ------------------------
@pytest.mark.parametrize("allow", [None, "", "   ", ",", " , ,"])
def test_an_EMPTY_allowlist_means_NONE_not_ALL(allow):
    """⚠️ THE TEST THAT CATCHES A "HARMONISATION" TO THE SIBLING KNOBS.

    CONVICTION_SIZING_ACCOUNTS and NETTING_ATTRIBUTION_ACCOUNTS read an empty
    allowlist as ALL. This one must not: it decides whether a live position's
    protective stop is cancelled and re-placed, so an unset variable must not
    arm every account including real money. If someone ever "tidies" this
    toward its siblings, this is the test that fails.
    """
    for account in ("bybit_1", "bybit_2", "bybit_portfolio", "anything"):
        assert basis.account_may_apply(account, allow) is False


def test_an_allowlisted_account_may_apply_and_a_sibling_may_not():
    assert basis.account_may_apply("bybit_1", "bybit_1") is True
    assert basis.account_may_apply("bybit_2", "bybit_1") is False


def test_whitespace_and_multiple_entries_parse():
    allow = " bybit_1 , bybit_portfolio "
    assert basis.account_may_apply("bybit_1", allow) is True
    assert basis.account_may_apply("bybit_portfolio", allow) is True
    assert basis.account_may_apply("bybit_2", allow) is False


@pytest.mark.parametrize("account", [None, "", "   "])
def test_an_unnamed_account_never_applies(account):
    """We cannot show the account is allowlisted; the fail-safe direction for a
    live order-path change is to decline."""
    assert basis.account_may_apply(account, "bybit_1,bybit_2") is False


def test_a_substring_is_not_a_match():
    """`bybit_1` must not be admitted by an allowlist naming `bybit_10`."""
    assert basis.account_may_apply("bybit_1", "bybit_10") is False


# ---- effective mode + apply_scope ------------------------------------------
def test_apply_on_an_allowlisted_account_is_allowlisted():
    assert basis.effective_mode("apply", "bybit_1", "bybit_1") == (
        basis.MODE_APPLY, basis.SCOPE_ALLOWLISTED)


def test_apply_held_back_reads_annotate_but_says_why():
    """A held-back row must never read as an applied one, AND must be
    distinguishable from one where apply was never asked for."""
    mode, scope = basis.effective_mode("apply", "bybit_2", "bybit_1")
    assert (mode, scope) == (basis.MODE_ANNOTATE, basis.SCOPE_NOT_ALLOWLISTED)
    assert scope != basis.SCOPE_NOT_APPLY


def test_annotate_is_not_apply_never_asked_for():
    assert basis.effective_mode("annotate", "bybit_1", "bybit_1") == (
        basis.MODE_ANNOTATE, basis.SCOPE_NOT_APPLY)


def test_off_is_carried_through_rather_than_collapsed_into_annotate():
    assert basis.effective_mode("off", "bybit_1", "bybit_1") == (
        basis.MODE_OFF, basis.SCOPE_NOT_APPLY)


# ---- the decision ----------------------------------------------------------
def _decide(**kw):
    args = dict(
        global_mode="apply", account_id="bybit_1", allowlist_raw="bybit_1",
        size=0.018, eps=0.0, side_blind_qty=0.46, graded_qty=0.0,
        coverage_state="graded", source="partial_sl_legs", symbol="BTCUSDT",
    )
    args.update(kw)
    return basis.coverage_decision(**args)


def test_armed_the_graded_figure_binds_and_the_masked_hole_is_seen():
    """THE DEFECT, at the decision layer. Side-blind 0.46 >= 0.018 says
    covered; the graded book has 0.0 and is naked."""
    d = _decide()
    assert d["basis"] == basis.BASIS_GRADED and d["binding"] is True
    assert d["bound_qty"] == 0.0
    assert d["decision"] == basis.DECISION_REARM_INDICATED
    assert d["verdict_side_blind"] == basis.VERDICT_COVERED
    assert d["verdict_graded"] == basis.VERDICT_UNCOVERED
    assert d["verdicts_differ"] is True


@pytest.mark.parametrize("gm,allow", [
    ("apply", ""),            # armed mode, EMPTY allowlist -> NONE
    ("apply", "bybit_1"),     # armed mode, DIFFERENT account
    ("annotate", "bybit_2"),  # allowlisted but apply never asked for
    ("off", "bybit_2"),
])
def test_held_back_the_side_blind_figure_still_binds(gm, allow):
    """The pre-gate behaviour, byte for byte: on any account the operator has
    not staged, the same 0.46 >= 0.018 comparison decides, exactly as it did
    before this module existed."""
    d = _decide(global_mode=gm, account_id="bybit_2", allowlist_raw=allow,
                graded_qty=None if gm == "off" else 0.0,
                coverage_state=(basis.COVERAGE_NOT_COMPUTED if gm == "off"
                                else "graded"))
    assert d["binding"] is False
    assert d["basis"] == basis.BASIS_SIDE_BLIND
    assert d["bound_qty"] == 0.46
    assert d["decision"] == basis.DECISION_SKIP_COVERED


def test_the_MEASUREMENT_survives_being_held_back():
    """⚠️ THE ALLOWLIST SCOPES THE BINDING, NEVER THE MEASUREMENT — the exact
    correction NETTING_ATTRIBUTION_ACCOUNTS needed on 2026-08-09. A held-back
    account must still produce the `verdicts_differ` evidence a reviewer needs
    in order to widen the allowlist TO it."""
    d = _decide(account_id="bybit_2", allowlist_raw="bybit_1")
    assert d["binding"] is False
    assert d["verdict_graded"] == basis.VERDICT_UNCOVERED
    assert d["verdicts_differ"] is True, (
        "the finding must be visible on the account being staged toward")


def test_verdicts_differ_is_None_when_only_one_verdict_exists():
    """`None` is *we could not compare*, never *they agree*."""
    d = _decide(graded_qty=None, coverage_state=basis.COVERAGE_NOT_COMPUTED)
    assert d["verdicts_differ"] is None
    assert d["verdict_graded"] is None


def test_a_graded_zero_is_a_reading_and_a_None_is_not():
    """0.0 means nothing protects this book — a serious measurement. None means
    we could not look. Collapsing them is the whole class of defect here."""
    assert _decide(graded_qty=0.0)["graded_qty"] == 0.0
    assert _decide(graded_qty=None,
                   coverage_state="leg_side_ungraded")["graded_qty"] is None


def test_armed_and_ungradeable_REFUSES_in_both_directions():
    d = _decide(graded_qty=None, coverage_state="leg_side_ungraded")
    assert d["bound_qty"] is None
    assert d["decision"] == basis.DECISION_REFUSED_UNGRADEABLE
    assert d["side_blind_qty"] == 0.46, (
        "the side-blind sum is reported, but it must not become bound_qty")


def test_held_back_and_ungradeable_does_NOT_refuse():
    """An `annotate` mode that introduced a new refusal would not be an
    annotation — it would be a live behaviour change wearing the wrong name."""
    d = _decide(account_id="bybit_2", allowlist_raw="bybit_1",
                graded_qty=None, coverage_state="leg_side_ungraded")
    assert d["bound_qty"] == 0.46
    assert d["decision"] == basis.DECISION_SKIP_COVERED


def test_the_row_carries_effective_mode_beside_the_requested_one():
    d = _decide(account_id="bybit_2", allowlist_raw="bybit_1")
    assert d["mode"] == basis.MODE_ANNOTATE
    assert d["global_mode"] == basis.MODE_APPLY
    assert d["apply_scope"] == basis.SCOPE_NOT_ALLOWLISTED


def test_eps_tolerance_is_applied_to_whichever_basis_binds():
    d = _decide(graded_qty=0.0179, eps=0.0001)
    assert d["decision"] == basis.DECISION_SKIP_COVERED
    d2 = _decide(graded_qty=0.0179, eps=0.0)
    assert d2["decision"] == basis.DECISION_REARM_INDICATED
