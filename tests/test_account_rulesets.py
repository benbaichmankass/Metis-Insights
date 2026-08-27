"""Tests for the account → backtest-ruleset resolver (src/prop/account_rulesets.py).

Locks the multi-account contract: every account resolves to a unit; real/paper
accounts → a `standard` ruleset synthesized from their risk block (limits carried,
no profit target, no prop economics); a prop account (exchange=breakout or an
explicit backtest_ruleset) → the prop ruleset (target + economics). risk_pct is
normalized to percent.
"""
from __future__ import annotations

from src.prop.account_rulesets import all_account_units, unit_for_account


def test_real_account_resolves_standard():
    # Backtest/compat-matrix notional now comes from an explicit
    # ``account_size_usd`` (the live notional cap ``pos_size`` was removed
    # 2026-06-24); absent it, _DEFAULT_STANDARD_SIZE is used.
    acct = {
        "exchange": "bybit", "account_class": "real_money",
        "risk": {"risk_pct": 0.01, "max_dd_pct": 0.05, "daily_loss_pct": 0.05, "account_size_usd": 500},
    }
    u = unit_for_account("bybit_2", acct)
    assert u.kind == "standard"
    assert u.risk_pct == 1.0                     # 0.01 fraction -> 1.0 percent
    assert u.account_size_usd == 500
    assert u.ruleset.limits.daily_loss_pct == 0.05
    assert u.ruleset.limits.max_drawdown_pct == 0.05
    assert u.ruleset.evaluation.profit_target_pct is None   # no target for a real account
    assert u.ruleset.economics.account_fee_usd == 0.0       # not a disposable prop account


def test_breakout_account_resolves_prop():
    acct = {"exchange": "breakout", "account_class": "real_money", "risk": {"risk_pct": 0.006}}
    u = unit_for_account("breakout_1", acct)
    assert u.kind == "prop"
    assert u.ruleset.ruleset == "breakout"
    assert u.ruleset.evaluation.profit_target_pct == 0.10   # +10% target
    assert u.ruleset.economics.account_fee_usd == 45.0      # prop economics present
    assert u.account_size_usd == u.ruleset.account_size_usd


def test_flat_runtime_account_cfg_sizes_off_top_level_risk_pct():
    """Regression: the live coordinator passes a FLAT account_cfg (risk_pct at
    the top level from RiskManager.risk_pct), NOT nested under a 'risk' block.

    Before the fix unit_for_account only read account['risk']['risk_pct'], saw
    nothing on the runtime path, and silently used _DEFAULT_RISK_PCT (0.5%) — so
    every emitted prop ticket was sized at 0.5% instead of the configured 1.5%
    ($25 vs the intended $75 on the $5k Breakout account). The top-level fallback
    must resolve the real risk.
    """
    flat = {"exchange": "breakout", "account_class": "real_money",
            "risk_pct": 0.015}   # the shape coordinator.multi_account_execute builds
    u = unit_for_account("breakout_1", flat)
    assert u.kind == "prop"
    assert u.risk_pct == 1.5     # 0.015 fraction -> 1.5 percent (NOT the 0.5 default)


def test_nested_risk_block_still_wins_over_top_level():
    # The compat-matrix path (raw accounts.yaml, nested risk block) is unchanged;
    # an explicit nested risk_pct takes precedence over any top-level value.
    acct = {"exchange": "breakout", "risk_pct": 0.005,
            "risk": {"risk_pct": 0.015}}
    u = unit_for_account("breakout_1", acct)
    assert u.risk_pct == 1.5


def test_explicit_backtest_ruleset_field_wins():
    acct = {"exchange": "bybit", "backtest_ruleset": "prop_rulesets/breakout.yaml",
            "risk": {"risk_pct": 0.01}}
    u = unit_for_account("bybit_prop_master", acct)
    assert u.kind == "prop"
    assert u.ruleset.ruleset == "breakout"


def test_all_accounts_resolve_without_error():
    """Every account resolves, and NO account is silently given a size.

    ⚠️ This test used to assert ``u.account_size_usd > 0`` for every account,
    which passed ONLY because an unestablished size silently became
    ``_DEFAULT_STANDARD_SIZE`` ($10,000). It was therefore a test OF the defect:
    it would have failed the moment the code started telling the truth, and it
    duly did (2026-08-27). The assertion is not weakened here — it is made
    conditional on the state the unit now reports, and a REFUSAL is required to
    carry a reason.
    """
    units = all_account_units()
    assert units, "expected at least one account"
    for aid, u in units.items():
        assert u.kind in ("prop", "standard")
        assert u.risk_pct > 0
        assert u.ruleset is not None
        if u.gradeable:
            assert u.account_size_usd > 0, aid
            assert u.size_state in ("declared", "measured"), aid
        else:
            # A refusal must SAY SO and say WHY — an unexplained None would be
            # the same silent hole in the other direction.
            assert u.account_size_usd is None, aid
            assert u.size_state in ("stale", "unreadable"), aid
            assert u.size_reason, f"{aid}: a refusal with no stated reason"
        # The point of the whole change: the placeholder is gone from every path.
        assert u.account_size_usd != 10_000.0 or u.size_state == "declared", aid


def test_standard_account_size_is_never_defaulted():
    """An account with no declared size and no readable balance REFUSES.

    The regression guard for
    ``BL-20260827-STANDARD-ARM-MISMODELS-INTRADAY-MAX-DD-AS-A-TERMINAL-FLOOR``'s
    sizing half: 0 of 11 accounts declare ``account_size_usd``, so before the
    fix this path produced a confident $10,000 for every one of them.
    """
    acct = {"exchange": "bybit", "account_class": "real_money",
            "risk": {"risk_pct": 0.01, "max_dd_pct": 0.05}}
    u = unit_for_account("bybit_2", acct, snapshots=None)
    assert u.account_size_usd is None
    assert not u.gradeable
    assert u.size_state == "unreadable"


def test_standard_account_sizes_off_the_live_balance():
    """With a fresh snapshot the account is graded at its REAL size."""
    from datetime import datetime, timezone
    now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    snaps = {"bybit_2": {"balance": 302.68, "api_ok": True,
                         "ts": "2026-08-27T09:00:30+00:00"}}
    acct = {"exchange": "bybit", "account_class": "real_money",
            "risk": {"risk_pct": 0.01, "max_dd_pct": 0.05}}
    import src.prop.account_rulesets as ar
    from src.prop.standard_account_size import resolve_standard_account_size as rsas
    orig = ar.resolve_standard_account_size
    ar.resolve_standard_account_size = lambda a, r=None, **kw: rsas(a, r, **{**kw, "now": now})
    try:
        u = unit_for_account("bybit_2", acct, snapshots=snaps)
    finally:
        ar.resolve_standard_account_size = orig
    assert u.gradeable
    assert u.account_size_usd == 302.68
    assert u.size_state == "measured"
    assert u.size_as_of is not None


def test_standard_ruleset_is_an_intraday_refusal_not_a_terminal_floor():
    """The type fix, locked.

    ``accounts.yaml::risk.max_dd_pct`` is an INTRA-DAY drawdown from today's
    high that refuses one trade. Grading it as a static terminal floor off the
    starting balance was wrong on BOTH axes, and is what
    ``BL-20260827-COMPAT-MATRIX-STANDARD-ARM-BORROWED-A-TYPE`` records.
    """
    acct = {"exchange": "bybit", "account_class": "real_money",
            "risk": {"risk_pct": 0.01, "max_dd_pct": 0.05, "daily_loss_pct": 0.05,
                     "account_size_usd": 500}}
    u = unit_for_account("bybit_2", acct)
    assert u.ruleset.limits.drawdown_type == "intraday_high"
    assert u.ruleset.limits.drawdown_breach == "refusal"
    assert u.ruleset.limits.drawdown_is_terminal is False


def test_prop_account_semantics_are_byte_for_byte_unchanged():
    """The prop arm must not move. It is the arm that was always correct."""
    acct = {"exchange": "breakout", "account_class": "real_money",
            "risk": {"risk_pct": 0.015}}
    u = unit_for_account("breakout_1", acct)
    assert u.kind == "prop"
    assert u.ruleset.limits.drawdown_type == "static"
    assert u.ruleset.limits.drawdown_breach == "terminal"
    assert u.ruleset.limits.drawdown_is_terminal is True
    assert u.account_size_usd == 5000.0
    assert u.gradeable
