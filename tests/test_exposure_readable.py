"""The exposure measurement must reach a HUMAN, not just a dict.

PR #8665 split `RiskManager` exposure into observe / policy / verdict so that
`report()["exposure"]` is emitted ALWAYS — the stated purpose being that an
operator choosing `max_gross_exposure_pct` has to see the current multiple
FIRST, since the old design gated the measurement on the ceiling already being
declared.

It shipped with no reader. The block reached `TradingAccount.status()` through
`**risk_report`, and the sole consumer — the Telegram `/accounts_status`
renderer — never referenced the key. The pre-existing checks all passed,
because they asked whether a consumer would BREAK on the new key (it splats the
dict without branching, so nothing did). Nobody asked whether a consumer would
DISPLAY it.

That gap is the whole subject of this file. The tests below assert the number is
*rendered*, which is a claim about what an operator can see, not about what a
dict contains — a distinction the original review collapsed.
"""

from __future__ import annotations

import re

import pytest

from src.units.ui.processor import format_account_status_block


def _status(**over):
    base = {
        "name": "bybit_2",
        "exchange": "bybit",
        "account_type": "standard",
        "daily_pnl": -12.0,
        "max_daily_loss_usd": 100.0,
        "open_positions": 3,
        "configured": True,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# The regression that motivated the file
# ---------------------------------------------------------------------------

def test_a_measured_multiple_actually_APPEARS_in_the_rendered_block():
    """The bug: this passed through `status()` and was never printed."""
    out = format_account_status_block(_status(exposure={
        "policy_declared": False,
        "max_gross_exposure_pct": None,
        "open_gross_notional": 20000.0,
        "equity": 2000.0,
        "exposure_multiple": 10.0,
        "headroom_usd": None,
        "measured": True,
        "unmeasured_reason": None,
    }))
    assert "10.00x" in out, (
        "the multiple must be VISIBLE to the operator — reaching status() is "
        "not the same as being read"
    )


def test_no_ceiling_is_stated_plainly_and_never_faked_as_a_zero_ceiling():
    """`max_gross_exposure_pct` is null (never 0.0) when undeclared.

    Rendering `10.00x / 0.00x` would read as a ceiling of zero — i.e. an
    account massively in breach — which is the exact misreading the null was
    chosen to prevent.
    """
    out = format_account_status_block(_status(exposure={
        "policy_declared": False,
        "max_gross_exposure_pct": None,
        "open_gross_notional": 20000.0,
        "equity": 2000.0,
        "exposure_multiple": 10.0,
        "headroom_usd": None,
        "measured": True,
        "unmeasured_reason": None,
    }))
    assert "no ceiling" in out
    # The precise hazard is a FABRICATED ceiling OPERAND, so assert that no
    # "<multiple>x / <ceiling>x" ratio is rendered at all. Two sloppier
    # assertions were tried first and both matched the wrong thing:
    # `"0.00x" not in out` (a substring of the legitimate "10.00x") and
    # `"/" not in line` (matches the "/" of the closing "</i>" tag).
    assert not re.search(r"[\d.]+x\s*/\s*[\d.]+x", out), (
        "no ceiling is declared, so no ratio may be rendered"
    )


# ---------------------------------------------------------------------------
# Unmeasured is NOT flat — the distinction the pure module exists to protect
# ---------------------------------------------------------------------------

def test_unmeasured_is_not_rendered_as_flat():
    """"We could not look" and "the account is flat" are opposite instructions.

    A `0.00x` here would tell the operator there is no exposure, when the truth
    is that exposure is unknown.
    """
    out = format_account_status_block(_status(exposure={
        "policy_declared": False,
        "max_gross_exposure_pct": None,
        "open_gross_notional": None,
        "equity": None,
        "exposure_multiple": None,
        "headroom_usd": None,
        "measured": False,
        "unmeasured_reason": "no_equity",
    }))
    assert "not measured" in out
    assert "no_equity" in out, "name the missing input, don't just say unknown"
    assert "0.00x" not in out


def test_flat_is_rendered_as_flat_and_is_distinguishable_from_unmeasured():
    flat = format_account_status_block(_status(exposure={
        "policy_declared": False,
        "max_gross_exposure_pct": None,
        "open_gross_notional": 0.0,
        "equity": 5000.0,
        "exposure_multiple": 0.0,
        "headroom_usd": None,
        "measured": True,
        "unmeasured_reason": None,
    }))
    unmeasured = format_account_status_block(_status(exposure={
        "policy_declared": False,
        "max_gross_exposure_pct": None,
        "open_gross_notional": None,
        "equity": None,
        "exposure_multiple": None,
        "headroom_usd": None,
        "measured": False,
        "unmeasured_reason": "no_notional",
    }))
    assert "0.00x" in flat
    assert "not measured" not in flat
    assert flat != unmeasured, "the two states must not render identically"


# ---------------------------------------------------------------------------
# Declared ceiling
# ---------------------------------------------------------------------------

def test_declared_ceiling_shows_multiple_over_ceiling_with_headroom():
    out = format_account_status_block(_status(exposure={
        "policy_declared": True,
        "max_gross_exposure_pct": 2.0,
        "open_gross_notional": 3000.0,
        "equity": 2000.0,
        "exposure_multiple": 1.5,
        "headroom_usd": 1000.0,
        "measured": True,
        "unmeasured_reason": None,
    }))
    assert "1.50x / 2.00x" in out
    assert "headroom $1,000" in out


def test_at_or_over_the_ceiling_is_visually_flagged():
    out = format_account_status_block(_status(exposure={
        "policy_declared": True,
        "max_gross_exposure_pct": 2.0,
        "open_gross_notional": 5000.0,
        "equity": 2000.0,
        "exposure_multiple": 2.5,
        "headroom_usd": 0.0,
        "measured": True,
        "unmeasured_reason": None,
    }))
    assert "⚠️" in out
    assert "2.50x / 2.00x" in out


# ---------------------------------------------------------------------------
# Back-compat: the renderer must not require the key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exposure",
    [None, {}, {"policy_declared": False}, "not-a-dict", 3],
)
def test_absent_or_malformed_exposure_omits_the_line_and_never_raises(exposure):
    """`PropRiskManager` overrides `report()`, and older payloads predate the
    key entirely. Neither may crash the operator's whole status page over an
    observability line.

    The `{}` and partial-dict cases are the ones that bit during development:
    a truthiness-only guard let them through to the "not measured (unknown)"
    branch, printing a confident negative about exposure derived from a payload
    that carried no measurement claim at all. Absent information must render as
    nothing, not as a finding.
    """
    kwargs = {} if exposure is None else {"exposure": exposure}
    out = format_account_status_block(_status(**kwargs))
    assert "Exposure:" not in out
    assert "bybit_2" in out
    assert "📦 Open: 3" in out


def test_the_rest_of_the_block_is_unchanged_by_this_addition():
    """The exposure line is additive — nothing else about the block moves."""
    without = format_account_status_block(_status())
    with_exp = format_account_status_block(_status(exposure={
        "policy_declared": False,
        "max_gross_exposure_pct": None,
        "open_gross_notional": 100.0,
        "equity": 1000.0,
        "exposure_multiple": 0.1,
        "headroom_usd": None,
        "measured": True,
        "unmeasured_reason": None,
    }))
    for line in without.splitlines():
        assert line in with_exp, f"pre-existing line lost: {line!r}"
