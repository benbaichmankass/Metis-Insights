"""Part A + Part B — the margin basis is broker-derived, and it is recorded.

Operator-approved Tier-3/Tier-2 change, 2026-08-13, closing
``BL-20260813-ICTSCALP-BTC-BYBIT2-BALANCE-REJECTS``.

THE DEFECT. On the real-money ``bybit_2`` book Bybit leaves every ACCOUNT-level
margin aggregate as the empty string, so ``totalAvailableBalance`` was unusable
and the margin pre-flight cap silently fell back to TOTAL EQUITY — counting the
initial margin already pledged to open positions as though it were free. Nine
venue refusals (ErrCode 110007) across three strategies and two symbols; 30% of
that account's orders in the window.

THE FIX, two halves:
  A  derive available margin from the USDT COIN block, where Bybit does publish
     it (``equity - totalPositionIM - totalOrderIM``); and when even that is
     unreadable, subtract ESTIMATED pledged margin from the equity basis rather
     than treating equity as free.
  B  stamp WHICH basis produced the size onto the journal row, so a refusal is
     attributable from the row instead of by contradiction across nine diag
     reads.

Every fixture below uses the REAL measured values from diag #9031/#9034 rather
than round numbers, so a regression has to reproduce the actual incident.
"""
from __future__ import annotations

import pytest

from src.units.accounts.execute import (
    AVAILABLE_STATE_COIN_DERIVED,
    AVAILABLE_STATE_DEPRECATED,
    AVAILABLE_STATE_UNAVAILABLE,
    AVAILABLE_STATE_VENUE,
    _derive_available_from_coin_block,
    _num,
    read_linear_available_balance,
)

# ── measured, diag #9034 (git_sha f55f748d, 2026-08-13T14:19:02Z) ──────────
BYBIT2_EQUITY = 280.82858992
BYBIT2_POS_IM = 53.93341186
BYBIT2_ORDER_IM = 0.0
BYBIT2_AVAILABLE = BYBIT2_EQUITY - BYBIT2_POS_IM - BYBIT2_ORDER_IM  # 226.895...

BYBIT2_ACCOUNT = {
    # Every account-level aggregate empty — this is the shape, verbatim.
    "accountType": "UNIFIED",
    "totalEquity": "280.53872331",
    "totalWalletBalance": "269.30794795",
    "totalMarginBalance": "",
    "totalAvailableBalance": "",
    "totalInitialMargin": "",
    "totalMaintenanceMargin": "",
    "coin": [
        {
            "coin": "USDT",
            "equity": "280.82858992",
            "walletBalance": "269.58618992",
            "unrealisedPnl": "11.2424",
            "totalPositionIM": "53.93341186",
            "totalOrderIM": "0",
            "availableToWithdraw": "",
            "availableToBorrow": "",
        },
        {"coin": "BTC", "equity": "0", "usdValue": "0.00051015",
         "collateralSwitch": False, "marginCollateral": True},
    ],
}


class _Client:
    def __init__(self, account):
        self.account = account

    def get_wallet_balance(self, accountType):  # noqa: N803 (venue's spelling)
        return {"retCode": 0, "result": {"list": [self.account]}}


# ══ PART A ─ the read ═════════════════════════════════════════════════════

def test_the_measured_bybit2_shape_now_resolves_to_coin_derived():
    """THE INCIDENT, reproduced. This exact payload used to yield None and put
    the sizer on the equity fallback."""
    value, state, detail = read_linear_available_balance(_Client(BYBIT2_ACCOUNT))
    assert state == AVAILABLE_STATE_COIN_DERIVED
    assert value == pytest.approx(BYBIT2_AVAILABLE)
    assert value == pytest.approx(226.895178, abs=1e-5)
    assert "coin block" in detail


def test_venue_field_still_outranks_the_derivation():
    """A book that publishes its own available figure must keep using it — the
    derivation is for accounts where Bybit does not."""
    acct = dict(BYBIT2_ACCOUNT, totalAvailableBalance="79988.85274159")
    value, state, _ = read_linear_available_balance(_Client(acct))
    assert state == AVAILABLE_STATE_VENUE
    assert value == pytest.approx(79988.85274159)


def test_derivation_outranks_the_deprecated_field():
    """Ordering is load-bearing. availableToWithdraw is withdrawal eligibility,
    deprecated for UNIFIED in 2025-01; the derivation is margin semantics over
    fields Bybit still publishes. Preferring the substitute would strand the
    account on the worse of the two."""
    coin = dict(BYBIT2_ACCOUNT["coin"][0], availableToWithdraw="9999.99")
    acct = dict(BYBIT2_ACCOUNT, coin=[coin])
    value, state, _ = read_linear_available_balance(_Client(acct))
    assert state == AVAILABLE_STATE_COIN_DERIVED
    assert value == pytest.approx(BYBIT2_AVAILABLE)


@pytest.mark.parametrize("missing", ["equity", "totalPositionIM", "totalOrderIM"])
def test_a_missing_input_REFUSES_the_derivation_rather_than_defaulting(missing):
    """THE DANGEROUS DEFAULT. Treating an absent totalPositionIM as 0 would
    claim nothing is pledged — the exact over-permission this fix removes, on
    an account whose fields are already known to come back blank."""
    coin = {k: v for k, v in BYBIT2_ACCOUNT["coin"][0].items() if k != missing}
    coin["availableToWithdraw"] = "111.11"
    acct = dict(BYBIT2_ACCOUNT, coin=[coin])
    value, state, detail = read_linear_available_balance(_Client(acct))
    assert state == AVAILABLE_STATE_DEPRECATED, "must not silently derive from a partial block"
    assert value == pytest.approx(111.11)
    assert missing in detail


def test_partial_block_with_no_deprecated_field_is_unavailable_not_zero():
    coin = {k: v for k, v in BYBIT2_ACCOUNT["coin"][0].items() if k != "totalPositionIM"}
    acct = dict(BYBIT2_ACCOUNT, coin=[coin])
    value, state, _ = read_linear_available_balance(_Client(acct))
    assert (value, state) == (None, AVAILABLE_STATE_UNAVAILABLE)


def test_underwater_book_floors_at_zero_never_negative():
    """Pledged margin above equity is a real state; the answer is 'no room',
    not a negative ceiling that would flip the sign of max_qty."""
    coin = dict(BYBIT2_ACCOUNT["coin"][0], equity="10", totalPositionIM="50")
    acct = dict(BYBIT2_ACCOUNT, coin=[coin])
    value, state, detail = read_linear_available_balance(_Client(acct))
    assert state == AVAILABLE_STATE_COIN_DERIVED
    assert value == 0.0
    assert "floored" in detail


@pytest.mark.parametrize("blank", ["", "null", None, "not-a-number"])
def test_num_treats_blank_as_ABSENT_not_zero(blank):
    """`float('')` raises; the danger is a caller 'helpfully' catching that and
    substituting 0.0. Absent must stay absent."""
    assert _num(blank) is None


def test_num_parses_a_real_value():
    assert _num("53.93341186") == pytest.approx(53.93341186)


def test_derivation_helper_reports_why_it_declined():
    ok, detail = _derive_available_from_coin_block({"equity": "100", "totalOrderIM": "0"})
    assert ok is None and "totalPositionIM" in detail


# ══ PART A ─ the sizer ════════════════════════════════════════════════════

def _rm(**kw):
    from src.units.accounts.risk import RiskManager
    cfg = {"risk_pct": 0.015, "leverage": 3, "qty_precision": 3, "min_qty": 0.001,
           "max_dd_pct": 0.05, "daily_loss_pct": 0.05}
    cfg.update(kw)
    return RiskManager(cfg)


class _Pkg:
    def __init__(self, entry, sl, symbol="BTCUSDT", direction="buy", confidence=0.9):
        self.entry, self.sl, self.symbol = entry, sl, symbol
        self.direction, self.confidence = direction, confidence
        self.tp, self.meta, self.strategy = entry * 1.01, {}, "ict_scalp_5m"


def test_equity_fallback_now_subtracts_pledged_margin(monkeypatch):
    """THE REJECTION WINDOW, replayed. Equity ~$274.91 with XRP $161.55 + ETH
    $76.61 open at 3x => $79.39 pledged. The cap used to size from the full
    equity and emit 0.011 BTC, which the venue refused nine times."""
    rm = _rm()
    monkeypatch.setattr(rm, "_open_gross_notional_from_db", lambda: 161.55 + 76.61)
    out: dict = {}
    qty = rm.position_size(
        _Pkg(63704.8, 63497.59), 274.91, market_type="linear",
        available_usd=None, total_account_usd=274.91, margin_basis_out=out,
    )
    assert out["kind"] == "equity_minus_pledged"
    assert out["basis_usd"] == pytest.approx(274.91 - 79.386666, abs=0.01)
    assert "ESTIMATED" in out["detail"]
    # The emitted size drops from the refused 0.011 to something the venue's
    # real available margin covers.
    assert qty <= 0.008 + 1e-9, f"expected <= 0.008, got {qty}"
    assert qty * 63704.8 / 3 <= out["basis_usd"], "sized IM must fit inside the basis"


def test_unreadable_open_notional_does_NOT_silently_adjust(monkeypatch):
    """'We could not look' must not become 'nothing is pledged'. The basis is
    left alone AND the kind says so, so a later refusal is attributable."""
    rm = _rm()
    monkeypatch.setattr(rm, "_open_gross_notional_from_db", lambda: None)
    out: dict = {}
    rm.position_size(
        _Pkg(63704.8, 63497.59), 274.91, market_type="linear",
        available_usd=None, total_account_usd=274.91, margin_basis_out=out,
    )
    assert out["kind"] == "equity_unadjusted"
    assert out["basis_usd"] == pytest.approx(274.91)
    assert "unreadable" in out["detail"]


def test_a_read_failure_is_treated_as_unknown_not_as_flat(monkeypatch):
    rm = _rm()
    def boom(): raise RuntimeError("db locked")
    monkeypatch.setattr(rm, "_open_gross_notional_from_db", boom)
    out: dict = {}
    rm.position_size(
        _Pkg(63704.8, 63497.59), 274.91, market_type="linear",
        available_usd=None, total_account_usd=274.91, margin_basis_out=out,
    )
    assert out["kind"] == "equity_unadjusted", "an exception must not read as zero pledged"


def test_broker_available_path_is_labelled_and_unchanged_in_arithmetic():
    """When the venue (or the derivation) supplies a figure, the cap arithmetic
    is exactly what it always was — only the label is new."""
    rm = _rm()
    out: dict = {}
    qty = rm.position_size(
        _Pkg(63704.8, 63497.59), 274.91, market_type="linear",
        available_usd=BYBIT2_AVAILABLE, total_account_usd=274.91,
        available_basis_kind=AVAILABLE_STATE_COIN_DERIVED, margin_basis_out=out,
    )
    assert out["kind"] == "coin_derived"
    assert out["basis_usd"] == pytest.approx(BYBIT2_AVAILABLE)
    assert out["max_qty_by_margin"] == pytest.approx(
        BYBIT2_AVAILABLE * 3 * 0.9 / 63704.8
    )
    assert qty > 0


def test_the_label_never_changes_the_arithmetic():
    """Sizing must not branch on the basis kind — if it did, the label and the
    number would have a way to drift apart."""
    rm = _rm()
    sizes = set()
    for kind in (AVAILABLE_STATE_VENUE, AVAILABLE_STATE_COIN_DERIVED, None, "anything"):
        sizes.add(rm.position_size(
            _Pkg(63704.8, 63497.59), 274.91, market_type="linear",
            available_usd=BYBIT2_AVAILABLE, total_account_usd=274.91,
            available_basis_kind=kind,
        ))
    assert len(sizes) == 1, f"the label moved the size: {sizes}"


def test_futures_is_untouched_by_all_of_this():
    """The crypto notional/leverage cap never applied to futures and still
    must not — futures margin is per-contract SPAN, not price*qty/leverage."""
    rm = _rm(qty_precision=0, min_qty=1)
    out: dict = {}
    rm.position_size(
        _Pkg(5000.0, 4990.0, symbol="MES"), 100000.0, market_type="futures",
        available_usd=None, total_account_usd=100000.0, margin_basis_out=out,
    )
    assert out == {}, "the margin cap must not run for futures"


def test_margin_basis_out_is_optional():
    """Every existing caller passes no out-dict; they must keep working."""
    rm = _rm()
    assert rm.position_size(
        _Pkg(63704.8, 63497.59), 274.91, market_type="linear",
        available_usd=BYBIT2_AVAILABLE,
    ) > 0


# ══ PART B ─ the stamp ════════════════════════════════════════════════════

def test_declared_basis_kinds_match_what_the_reader_can_emit():
    """The stamp's vocabulary must cover every state the reader produces, or a
    row could carry a kind nothing documents."""
    from src.units.accounts.risk import MARGIN_BASIS_KINDS
    assert AVAILABLE_STATE_VENUE in MARGIN_BASIS_KINDS
    assert AVAILABLE_STATE_COIN_DERIVED in MARGIN_BASIS_KINDS
    assert len(set(MARGIN_BASIS_KINDS)) == len(MARGIN_BASIS_KINDS)


def test_rejection_writer_stamps_the_basis(monkeypatch):
    """The row that recorded the nine 110007s now says what it was sized from."""
    import src.units.accounts.execute as ex
    seen = {}

    def fake(pkg, cfg, order, **kw):
        seen.update(kw)
        return True

    monkeypatch.setattr(ex, "_log_trade_to_journal", fake)
    basis = {"kind": "equity_minus_pledged", "basis_usd": 195.52}
    ex.log_rejection_to_journal(
        _Pkg(63704.8, 63497.59), {"account_id": "bybit_2"},
        reason="ab not enough for new order (ErrCode: 110007)",
        status="exchange_rejected", sized_qty=0.011, margin_basis=basis,
    )
    assert seen["extra_notes"] == {"margin_basis": basis}


def test_absent_basis_is_omitted_not_written_empty(monkeypatch):
    """'No basis recorded' must stay distinct from 'basis was unknown'."""
    import src.units.accounts.execute as ex
    seen = {}
    monkeypatch.setattr(ex, "_log_trade_to_journal",
                        lambda pkg, cfg, order, **kw: seen.update(kw) or True)
    ex.log_rejection_to_journal(
        _Pkg(1.0, 0.9), {"account_id": "x"}, reason="r",
        status="rejected", sized_qty=0.0, margin_basis=None,
    )
    assert seen["extra_notes"] is None


def test_read_state_wire_values_are_pinned():
    """These strings are WRITTEN INTO JOURNAL ROWS and read by operators, so a
    rename is a data-format change, not a refactor. Pinning the literals also
    keeps every state visible to `collapsed-state-guard`, which matches on the
    wire tokens rather than on the constant names."""
    assert AVAILABLE_STATE_VENUE == "venue_available"
    assert AVAILABLE_STATE_COIN_DERIVED == "coin_derived"
    assert AVAILABLE_STATE_DEPRECATED == "deprecated_withdrawable"
    assert AVAILABLE_STATE_UNAVAILABLE == "unavailable"


def test_every_read_state_is_reachable_from_a_real_response_shape():
    """One payload per state — proof the ladder can actually land on each,
    rather than a state existing only as a constant."""
    shapes = {
        "venue_available": dict(BYBIT2_ACCOUNT, totalAvailableBalance="123.45"),
        "coin_derived": BYBIT2_ACCOUNT,
        "deprecated_withdrawable": dict(
            BYBIT2_ACCOUNT,
            coin=[{"coin": "USDT", "availableToWithdraw": "77.7"}],
        ),
        "unavailable": dict(BYBIT2_ACCOUNT, coin=[{"coin": "USDT"}]),
    }
    reached = {
        read_linear_available_balance(_Client(acct))[1] for acct in shapes.values()
    }
    assert reached == set(shapes), f"unreachable state(s): {set(shapes) - reached}"
