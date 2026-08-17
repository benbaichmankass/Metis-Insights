"""The Bybit available-margin read must say WHICH branch it took.

FOUR states since 2026-08-13 (Part A added ``coin_derived``); see
``read_linear_available_balance`` for the ladder and why the derivation ranks
above the deprecated field.

BL-20260701-BYBIT-AVAILABLE-FIELD (filed 2026-08-13). The read used to return
a bare ``Optional[float]`` and log nothing on either non-venue branch, so three
materially different states arrived at the sizer as one value:

  * the account-level ``totalAvailableBalance``  — broker truth
  * the per-coin ``availableToWithdraw``         — a SUBSTITUTE (deprecated for
                                                   UNIFIED accounts 2025-01-09;
                                                   withdrawal eligibility, not
                                                   new-order margin)
  * nothing readable                             — we COULD NOT LOOK

That collapse is why establishing what had happened on bybit_2 during the
2026-08 110007 rejections required four diag pulls and a proof by contradiction
(BL-20260813-ICTSCALP-BTC-BYBIT2-BALANCE-REJECTS) — and even then the two
non-venue branches stayed undecidable from outside.

The load-bearing test in this file is
``test_sizer_facing_return_is_byte_for_byte_unchanged``: the split is
observability only. If it ever fails, a read-path change has silently become an
order-path change.
"""
from __future__ import annotations

import logging

import pytest

from src.units.accounts.execute import (
    AVAILABLE_STATE_DEPRECATED,
    AVAILABLE_STATE_UNAVAILABLE,
    AVAILABLE_STATE_VENUE,
    _fetch_linear_available_balance,
    read_linear_available_balance,
)


class _Client:
    """Minimal stand-in for the Bybit client's one method used here."""

    def __init__(self, account: dict | None = None, raises: Exception | None = None):
        self._account = account
        self._raises = raises

    def get_wallet_balance(self, accountType: str):  # noqa: N803  (venue's spelling)
        assert accountType == "UNIFIED"
        if self._raises is not None:
            raise self._raises
        return {"result": {"list": [self._account]}} if self._account is not None else {}


def _venue(total: str = "264.71", coin_avail: str = "999.99") -> _Client:
    return _Client({
        "totalAvailableBalance": total,
        "coin": [{"coin": "USDT", "availableToWithdraw": coin_avail}],
    })


def _deprecated_only(coin_avail: str = "274.91") -> _Client:
    # The shape that matters: the account-level field present but EMPTY, which
    # is what Bybit returns for account types that do not populate it. An empty
    # string is not zero and must not be read as one.
    return _Client({
        "totalAvailableBalance": "",
        "coin": [{"coin": "USDT", "availableToWithdraw": coin_avail}],
    })


# ── the three states are reachable and distinct ────────────────────────────

def test_venue_field_is_broker_truth():
    value, state, detail = read_linear_available_balance(_venue("264.71"))
    assert (value, state) == (pytest.approx(264.71), AVAILABLE_STATE_VENUE)
    assert detail is None


def test_venue_field_wins_over_the_deprecated_one():
    """Preference order is the whole point of the 2026-07-01 change."""
    value, state, _ = read_linear_available_balance(_venue("264.71", "999.99"))
    assert value == pytest.approx(264.71)
    assert state == AVAILABLE_STATE_VENUE


@pytest.mark.parametrize("absent", ["", "null", None])
def test_absent_account_field_falls_back_and_SAYS_SO(absent):
    client = _Client({
        "totalAvailableBalance": absent,
        "coin": [{"coin": "USDT", "availableToWithdraw": "274.91"}],
    })
    value, state, detail = read_linear_available_balance(client)
    assert value == pytest.approx(274.91)
    assert state == AVAILABLE_STATE_DEPRECATED
    assert detail and "deprecated" in detail


def test_nothing_readable_is_unavailable_not_zero():
    """'We could not look' and 'the account is flat' are opposite statements."""
    value, state, detail = read_linear_available_balance(_Client({"coin": []}))
    assert value is None, "must be None — a fabricated 0.0 would read as 'no margin'"
    assert state == AVAILABLE_STATE_UNAVAILABLE
    assert detail


def test_raising_client_is_unavailable_and_names_the_exception():
    value, state, detail = read_linear_available_balance(
        _Client(raises=RuntimeError("connection reset"))
    )
    assert (value, state) == (None, AVAILABLE_STATE_UNAVAILABLE)
    assert "RuntimeError" in detail and "connection reset" in detail


def test_the_three_states_are_mutually_exclusive_strings():
    states = {AVAILABLE_STATE_VENUE, AVAILABLE_STATE_DEPRECATED, AVAILABLE_STATE_UNAVAILABLE}
    assert len(states) == 3, "two states collapsing to one string defeats the contract"


# ── the split changed NOTHING the sizer sees ───────────────────────────────

@pytest.mark.parametrize(
    "client, expected",
    [
        (_venue("264.71"), 264.71),          # venue figure
        (_deprecated_only("274.91"), 274.91),  # deprecated substitute
        (_Client({"coin": []}), None),         # could not look
        (_Client(raises=ValueError("boom")), None),
    ],
)
def test_sizer_facing_return_is_byte_for_byte_unchanged(client, expected):
    """THE NEGATIVE CONTROL — scope narrowed 2026-08-13 (Part A shipped).

    Originally this asserted the read could not move ANY sized quantity, when
    the three-state split was observability only. That is no longer true:
    Part A (Tier-3, operator-approved) deliberately inserts a ``coin_derived``
    rung, so an account whose USDT coin block IS derivable now receives a
    DIFFERENT and more correct figure than before.

    What this still guards, and what the fixtures below are chosen to cover, is
    that the OTHER rungs are untouched: a present venue field, a coin block that
    is not derivable, and the could-not-look paths all return exactly what they
    always did. If one of these moves, a rung changed that was not supposed to.
    The coin-derived rung has its own tests below.
    """
    got = _fetch_linear_available_balance(client)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_negative_venue_figure_is_floored_at_zero_as_before():
    value, state, _ = read_linear_available_balance(_venue("-5.0"))
    assert value == 0.0 and state == AVAILABLE_STATE_VENUE


# ── the branch is no longer silent ─────────────────────────────────────────

def test_deprecated_substitution_warns(caplog):
    with caplog.at_level(logging.WARNING):
        _fetch_linear_available_balance(_deprecated_only())
    assert any(
        "SUBSTITUTED" in r.message or "SUBSTITUTED" in r.getMessage()
        for r in caplog.records
    ), "a substitute wearing the label of broker truth must not be silent"


def test_could_not_look_warns(caplog):
    with caplog.at_level(logging.WARNING):
        _fetch_linear_available_balance(_Client(raises=RuntimeError("x")))
    assert any("could NOT read" in r.getMessage() for r in caplog.records)


def test_venue_branch_does_not_warn(caplog):
    """Broker truth is the normal path — warning on it would be alarm fatigue,
    which this repo treats as its own P1 bug."""
    with caplog.at_level(logging.WARNING):
        _fetch_linear_available_balance(_venue())
    assert not [r for r in caplog.records if "available_balance" in r.getMessage()]


# ── the contract is registered, so the guard actually enforces it ──────────

def test_contract_is_registered_with_collapsed_state_guard():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts/ci/check_collapsed_states.py"
    spec = importlib.util.spec_from_file_location("_ccs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    entry = [c for c in mod.CONTRACTS if c["name"] == "bybit_available.read_state"]
    assert entry, "registering the contract is how a three-state field becomes enforced"
    from src.units.accounts.execute import AVAILABLE_STATE_COIN_DERIVED
    assert set(entry[0]["states"]) == {
        AVAILABLE_STATE_VENUE,
        AVAILABLE_STATE_COIN_DERIVED,
        AVAILABLE_STATE_DEPRECATED,
        AVAILABLE_STATE_UNAVAILABLE,
    }, "the registry must name the SAME strings the producer emits"


# ── `unavailable` had a residual collapse INSIDE it (2026-08-13) ───────────
#
# The first live read of the new Bybit arm returned `unavailable` for bybit_2
# with the one-line detail "neither ... present". That is true and useless: it
# cannot say whether the venue ERRORED, returned NO account object, or returned
# one LACKING both fields — three different bugs with three different fixes,
# collapsed into one string. Registering a three-state contract does not make
# the states internally honest; the state was right and the detail was not.

def test_retcode_error_says_the_venue_refused():
    client = _Client()
    client.get_wallet_balance = lambda accountType: {  # type: ignore[method-assign]
        "retCode": 10003, "retMsg": "API key invalid", "result": {},
    }
    value, state, detail = read_linear_available_balance(client)
    assert (value, state) == (None, AVAILABLE_STATE_UNAVAILABLE)
    assert "retCode=10003" in detail and "API key invalid" in detail


@pytest.mark.parametrize(
    "resp",
    [
        {"retCode": 0, "result": {"list": []}},  # empty list
        {"retCode": 0},                          # no result block at all
        {"retCode": 0, "result": {}},            # result present, no list
    ],
)
def test_no_account_object_is_distinguishable_from_missing_fields(resp):
    client = _Client()
    client.get_wallet_balance = lambda accountType: resp  # type: ignore[method-assign]
    value, state, detail = read_linear_available_balance(client)
    assert (value, state) == (None, AVAILABLE_STATE_UNAVAILABLE)
    assert "NO account object" in detail


def test_account_present_but_fieldless_names_the_keys_it_DID_carry():
    """The keys are the denominator for the negative — without them 'missing'
    is unfalsifiable."""
    client = _Client({"accountType": "UNIFIED", "totalEquity": "280.75", "coin": []})
    value, state, detail = read_linear_available_balance(client)
    assert (value, state) == (None, AVAILABLE_STATE_UNAVAILABLE)
    assert "account object present" in detail
    assert "totalEquity" in detail and "accountType" in detail


def test_detail_never_leaks_a_balance_VALUE():
    """Keys only. A wallet response holds balances and this string reaches logs
    and a diag payload."""
    client = _Client({"accountType": "UNIFIED", "totalEquity": "280.75", "coin": []})
    _, _, detail = read_linear_available_balance(client)
    assert "280.75" not in detail


def test_key_list_is_bounded():
    # Bound is 14: bybit_2's real response carries 12 keys, so a 12-cap would
    # have silently TRUNCATED the very evidence that identified this defect
    # (totalAvailableBalance present-but-empty sat at index 7 of 12). Widened
    # with headroom — but still bounded, because an unbounded key dump is a
    # log-flood.
    client = _Client({f"field_{i:02d}": i for i in range(40)})
    _, _, detail = read_linear_available_balance(client)
    assert detail.count("field_") <= 14, "an unbounded key dump is a log-flood"


def test_raised_exception_says_it_RAISED():
    """Distinct from a clean response that carried nothing — one is 'the call
    failed', the other is 'the call worked and had no answer'."""
    _, state, detail = read_linear_available_balance(_Client(raises=TimeoutError("t")))
    assert state == AVAILABLE_STATE_UNAVAILABLE
    assert detail.startswith("call raised:")


def test_the_four_unavailable_sub_reasons_are_mutually_distinct():
    def detail_for(resp=None, raises=None, account=None):
        c = _Client(account, raises)
        if resp is not None:
            c.get_wallet_balance = lambda accountType: resp  # type: ignore[method-assign]
        return read_linear_available_balance(c)[2]

    details = {
        detail_for(resp={"retCode": 10003, "retMsg": "x", "result": {}}),
        detail_for(resp={"retCode": 0, "result": {"list": []}}),
        detail_for(account={"accountType": "UNIFIED"}),
        detail_for(raises=RuntimeError("boom")),
    }
    assert len(details) == 4, f"sub-reasons collapsed: {details}"


# ── present-but-EMPTY is not ABSENT (2026-08-13, second live read) ─────────
#
# bybit_2's response carries `totalAvailableBalance` in its key list AND
# carries it empty. The previous detail said "carried neither
# totalAvailableBalance nor ..." while listing that very key — a diagnostic
# contradicting its own evidence. "Bybit did not send this key" and "Bybit sent
# it blank" have different causes and different fixes.

_BYBIT_2_SHAPE = {
    "accountType": "UNIFIED", "totalEquity": "274.91", "totalAvailableBalance": "",
    "totalInitialMargin": "79.39", "totalMaintenanceMargin": "4.2",
    "accountIMRate": "0.28", "accountLTV": "0", "coin": [],
}


def test_present_but_empty_is_reported_as_present():
    _, state, detail = read_linear_available_balance(_Client(dict(_BYBIT_2_SHAPE)))
    assert state == AVAILABLE_STATE_UNAVAILABLE
    assert "PRESENT but empty" in detail
    assert "ABSENT" not in detail


def test_genuinely_absent_key_is_reported_as_absent():
    shape = {k: v for k, v in _BYBIT_2_SHAPE.items() if k != "totalAvailableBalance"}
    _, state, detail = read_linear_available_balance(_Client(shape))
    assert state == AVAILABLE_STATE_UNAVAILABLE
    assert "ABSENT from the response" in detail
    assert "PRESENT but empty" not in detail


def test_detail_never_claims_a_key_is_missing_while_listing_it():
    """The self-contradiction guard: whatever the wording, a key named in the
    key list must not also be described as not carried."""
    _, _, detail = read_linear_available_balance(_Client(dict(_BYBIT_2_SHAPE)))
    listed = "'totalAvailableBalance'" in detail
    claimed_missing = "carried neither totalAvailableBalance" in detail
    assert not (listed and claimed_missing)


# ── the margin fields are readable, verbatim and non-interpreting ──────────

def test_margin_fields_returns_every_declared_field():
    from src.units.accounts.execute import _MARGIN_FIELDS, read_linear_margin_fields
    fields, err = read_linear_margin_fields(_Client(dict(_BYBIT_2_SHAPE)))
    assert err is None
    assert set(_MARGIN_FIELDS) <= set(fields), (
        "every declared field must appear so a MISSING one is visibly null "
        "rather than silently dropped"
    )
    assert set(fields) - set(_MARGIN_FIELDS) == {"coin_usdt", "coin_count", "coins_other"}
    assert fields["totalEquity"] == "274.91"
    assert fields["totalInitialMargin"] == "79.39"
    assert fields["totalAvailableBalance"] == ""
    assert fields["totalMarginBalance"] is None  # absent on this account -> null, not dropped


def test_margin_fields_does_not_interpret():
    """It must return raw venue strings. The moment it computes, it becomes an
    order-path input that nobody validated."""
    from src.units.accounts.execute import read_linear_margin_fields
    fields, _ = read_linear_margin_fields(_Client(dict(_BYBIT_2_SHAPE)))
    assert isinstance(fields["totalEquity"], str)
    assert "available" not in {k.lower() for k in fields} - {"totalavailablebalance"}


@pytest.mark.parametrize(
    "client, expect",
    [
        (_Client(raises=RuntimeError("x")), "call raised"),
        (_Client({}), None),
    ],
)
def test_margin_fields_error_paths(client, expect):
    from src.units.accounts.execute import read_linear_margin_fields
    fields, err = read_linear_margin_fields(client)
    if expect:
        assert fields is None and expect in err
    else:
        assert err is None and fields is not None


def test_margin_fields_reports_a_retcode_refusal():
    from src.units.accounts.execute import read_linear_margin_fields
    c = _Client()
    c.get_wallet_balance = lambda accountType: {"retCode": 10003, "result": {}}  # type: ignore[method-assign]
    fields, err = read_linear_margin_fields(c)
    assert fields is None and "retCode=10003" in err


# ── the per-coin USDT block is reported WHOLE, not cherry-picked ───────────
#
# The 2026-08-13 error this guards against: reading a KEY LIST, seeing
# `totalInitialMargin` in it, and inferring it carried a value. Every
# account-level margin aggregate on bybit_2 is the empty string. Selecting
# fields from the coin block would reproduce that mistake one level down.

def test_usdt_coin_block_is_returned_verbatim():
    from src.units.accounts.execute import read_linear_margin_fields
    shape = dict(_BYBIT_2_SHAPE)
    shape["coin"] = [
        {"coin": "BTC", "walletBalance": "0.5"},
        {"coin": "USDT", "walletBalance": "269.27", "totalPositionIM": "79.39",
         "totalOrderIM": "0", "equity": "280.07", "availableToWithdraw": ""},
    ]
    fields, err = read_linear_margin_fields(_Client(shape))
    assert err is None
    block = fields["coin_usdt"]
    assert block["totalPositionIM"] == "79.39"
    assert block["availableToWithdraw"] == "", "empty must survive as empty, not vanish"
    assert set(block) == {"coin", "walletBalance", "totalPositionIM", "totalOrderIM",
                          "equity", "availableToWithdraw"}, "block must be verbatim"
    assert fields["coin_count"] == 2


def test_usdt_block_is_null_when_absent_and_count_still_reported():
    from src.units.accounts.execute import read_linear_margin_fields
    shape = dict(_BYBIT_2_SHAPE)
    shape["coin"] = [{"coin": "BTC"}]
    fields, _ = read_linear_margin_fields(_Client(shape))
    assert fields["coin_usdt"] is None
    assert fields["coin_count"] == 1, "a count of 1 with no USDT is a different state from no coins at all"


def test_usdt_block_is_bounded():
    from src.units.accounts.execute import read_linear_margin_fields
    shape = dict(_BYBIT_2_SHAPE)
    shape["coin"] = [dict({"coin": "USDT"}, **{f"f{i:02d}": i for i in range(60)})]
    fields, _ = read_linear_margin_fields(_Client(shape))
    assert len(fields["coin_usdt"]) <= 24


# ── every OTHER coin, because USDT alone makes the derivation a lower bound ─

def test_other_coins_are_reported_with_their_collateral_flags():
    from src.units.accounts.execute import read_linear_margin_fields
    shape = dict(_BYBIT_2_SHAPE)
    shape["coin"] = [
        {"coin": "USDT", "walletBalance": "269.27", "totalPositionIM": "53.93"},
        {"coin": "BTC", "equity": "0.004", "usdValue": "260.11",
         "marginCollateral": True, "collateralSwitch": True,
         "walletBalance": "0.004", "totalPositionIM": "0", "totalOrderIM": "0"},
    ]
    fields, _ = read_linear_margin_fields(_Client(shape))
    others = fields["coins_other"]
    assert [c["coin"] for c in others] == ["BTC"], "USDT must not repeat in coins_other"
    assert others[0]["usdValue"] == "260.11"
    assert others[0]["marginCollateral"] is True, (
        "a collateral-flagged second coin raises the real ceiling — without this "
        "the derivation is a silent LOWER bound"
    )


def test_other_coins_is_empty_list_not_null_when_usdt_is_the_only_coin():
    """[] means 'we looked and there are none'. null would mean 'we did not
    look' — different states."""
    from src.units.accounts.execute import read_linear_margin_fields
    shape = dict(_BYBIT_2_SHAPE)
    shape["coin"] = [{"coin": "USDT", "walletBalance": "1"}]
    fields, _ = read_linear_margin_fields(_Client(shape))
    assert fields["coins_other"] == []
    assert fields["coin_count"] == 1


def test_other_coins_declares_every_field_even_when_absent():
    from src.units.accounts.execute import read_linear_margin_fields
    shape = dict(_BYBIT_2_SHAPE)
    shape["coin"] = [{"coin": "ETH"}]
    fields, _ = read_linear_margin_fields(_Client(shape))
    assert fields["coins_other"][0]["marginCollateral"] is None, (
        "an absent flag must be visibly null, never dropped"
    )


def test_other_coins_is_bounded():
    from src.units.accounts.execute import read_linear_margin_fields
    shape = dict(_BYBIT_2_SHAPE)
    shape["coin"] = [{"coin": f"C{i:02d}"} for i in range(30)]
    fields, _ = read_linear_margin_fields(_Client(shape))
    assert len(fields["coins_other"]) <= 8
    assert fields["coin_count"] == 30, "the COUNT must stay truthful even when the list is capped"
