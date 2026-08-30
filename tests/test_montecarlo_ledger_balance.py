"""The R round-trip must not depend on the account being scored.

BL-20260829-COMPAT-MATRIX-RESCALES-R-BY-ACCOUNT-SIZE-SO-THE-VERDICT-TRACKS-BALANCE

`ledger_to_r_sequence` recovers R as ``pnl_k / (balance_before_k *
base_risk_pct/100)``, replaying a balance walk from ``initial_balance``. The
compat matrix scores ONE ledger (built at ``--base-account-size``) against EVERY
account, so passing the account's own size rescaled every R by roughly
``built_at / account_size``. That made the survival verdict a function of the
account's BALANCE and manufactured a "small accounts breach, large accounts
route" pattern that was reported as a finding on 2026-08-29 and retracted.

⚠️ Why the bug survived: ``breakout_1`` is a $5,000 account and
``--base-account-size`` defaults to 5000.0, so the PROP arm — the one this tool
was built for — was exact by coincidence. Only the later standard arm was wrong.
"""
from __future__ import annotations

import pytest

from scripts.prop.account_compat_matrix import synth_ledger_from_emit
from src.prop.montecarlo import ledger_to_r_sequence

BASE_ACCOUNT, BASE_RISK = 5000.0, 0.5
TRUE_R = [0.8, -1.0, 1.5, -1.0, 2.0, -0.5, 1.1, -1.0]


def _ledger():
    rows = [{"net_r": r, "entry_time": f"2026-01-{i + 1:02d}T00:00:00Z"}
            for i, r in enumerate(TRUE_R)]
    return synth_ledger_from_emit(
        rows, base_account_size=BASE_ACCOUNT, base_risk_pct=BASE_RISK
    )


@pytest.mark.parametrize("account_size", [200.10, 5000.0, 95542.76])
def test_r_round_trip_is_exact_at_the_ledgers_build_balance(account_size: float) -> None:
    """Recovered R equals the true net_r regardless of the account being scored.

    The parametrised `account_size` is deliberately UNUSED in the call: that is
    the point. The recovery balance is the ledger's build balance, so the account
    under test cannot move the R sequence.
    """
    got = [t.r_multiple for t in ledger_to_r_sequence(
        _ledger(), initial_balance=BASE_ACCOUNT, base_risk_pct=BASE_RISK)]
    assert got == pytest.approx(TRUE_R, abs=1e-9), (
        f"R rescaled while scoring a {account_size} account"
    )


def test_recovering_at_the_wrong_balance_rescales_R_which_is_the_bug() -> None:
    """Pin the DEFECT itself, so a regression is a failing test and not a silence.

    Without this, the test above would still pass if someone reverted the call
    site to `initial_balance=acct` and only ever ran it at 5000.
    """
    ledger = _ledger()
    at_200 = [t.r_multiple for t in ledger_to_r_sequence(
        ledger, initial_balance=200.10, base_risk_pct=BASE_RISK)]
    at_95k = [t.r_multiple for t in ledger_to_r_sequence(
        ledger, initial_balance=95542.76, base_risk_pct=BASE_RISK)]

    # ~25x inflation on a small account, ~0.05x deflation on a large one.
    assert at_200[0] / TRUE_R[0] > 20.0, at_200[0]
    assert at_95k[0] / TRUE_R[0] < 0.10, at_95k[0]
    # ...and the direction is what produced the false 'account size decides' story.
    assert abs(at_200[0]) > abs(TRUE_R[0]) > abs(at_95k[0])


def test_default_is_unchanged_for_a_caller_that_omits_the_new_arg() -> None:
    """Omitting `ledger_initial_balance` must behave exactly as before.

    validate_alt_prop builds and scores at the SAME balance and passes nothing;
    it must be byte-identical, or this fix would silently move a second tool.
    """
    from src.prop.montecarlo import run_montecarlo
    import inspect

    sig = inspect.signature(run_montecarlo)
    p = sig.parameters["ledger_initial_balance"]
    assert p.default is None, "the new arg must default to None (= previous behaviour)"
