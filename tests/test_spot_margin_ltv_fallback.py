"""S-056 — spot-margin LTV fallback when Bybit API returns
``availableToBorrow=0`` for the order's spending-side coin.

Operator-confirmed (2026-05-08): the bot's spot-margin wallet is by
design always 100 % USDT at idle — every position closes back to
USDT — so ``walletBalance(BTC)=0`` is structural, not a "no margin"
signal. Bybit V5 empties ``availableToBorrow`` for any coin row
with walletBalance=0, which used to zero the API-derived capacity
and silently refuse the trade even though the operator's margin
tier had plenty of room.

Fix: when the API field is empty/zero, fall back to
``usdt_collateral × spot_margin_ltv`` (default 0.5 — half of
Bybit's typical 80 % retail tier; per-account override in
``risk.spot_margin_ltv``). Capped by ``risk.max_borrow_btc`` so
it can never exceed the operator's configured tier ceiling.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.execute import _SPOT_BUY_SAFETY_BUFFER
from src.units.accounts.risk import (
    DEFAULT_SPOT_MARGIN_LTV,
    RiskManager,
)


def _short_pkg(entry: float = 80_000.0):
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="short",
        entry=entry,
        sl=entry + 100.0,
        tp=entry - 200.0,
        confidence=0.6,
    )


def _long_pkg(entry: float = 80_000.0):
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=entry,
        sl=entry - 100.0,
        tp=entry + 200.0,
        confidence=0.6,
    )


def _account_stub(*, ltv: float = DEFAULT_SPOT_MARGIN_LTV,
                  max_borrow_btc: float = 0.5):
    acc = MagicMock()
    acc.name = "bybit_2"
    acc.exchange = "bybit"
    acc.api_key_env = "BYBIT_API_KEY_FAKE"
    acc.account_type = "regular"
    acc.market_type = "spot-margin"
    acc.dry_run = False
    acc.cached_balance_usd = 99_999.0
    acc.strategies = ["vwap"]
    acc.risk_manager = RiskManager({
        "risk_pct": 0.01,
        "min_balance_usd": 50,
        "spot_margin_ltv": ltv,
        "max_borrow_btc": max_borrow_btc,
    })
    return acc


def _drive(*, pkg, spot_balances, acc):
    """Run multi_account_execute with stubs around the wallet read,
    sizer, and per-account loader; capture the kwargs the coordinator
    passes into RiskManager.position_size.
    """
    from src.core.coordinator import Coordinator
    with patch(
        "src.units.accounts.risk.RiskManager.position_size",
        return_value=0.0,
    ) as mock_size, patch(
        "src.units.accounts.load_accounts", return_value=[acc],
    ), patch(
        "src.core.coordinator._log_new_order_package",
        return_value="pkg-fake",
    ), patch(
        "src.units.accounts.clients.bybit_client_for",
        return_value=MagicMock(),
    ), patch(
        "src.units.accounts.execute._fetch_spot_coin_balances",
        return_value=spot_balances,
    ):
        coord = Coordinator()
        coord.multi_account_execute(
            pkg, accounts_path="/dev/null",
            balance_fetcher=lambda a: 99_999.0,
        )
    _positional, kwargs = mock_size.call_args
    return kwargs


# ---------------------------------------------------------------------------
# RiskManager config plumbing
# ---------------------------------------------------------------------------


class TestRiskManagerLtvConfig:
    def test_default_ltv_is_0_5(self):
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50})
        assert rm.spot_margin_ltv == DEFAULT_SPOT_MARGIN_LTV
        assert rm.spot_margin_ltv == 0.5

    def test_override_via_config(self):
        rm = RiskManager({
            "risk_pct": 0.01, "min_balance_usd": 50,
            "spot_margin_ltv": 0.7,
        })
        assert rm.spot_margin_ltv == 0.7

    def test_clamped_to_unit_interval_high(self):
        """A typo or 100 %+ leverage request is clamped to 1.0 so a
        bad config can't request more than full collateral as
        borrow.
        """
        rm = RiskManager({
            "risk_pct": 0.01, "min_balance_usd": 50,
            "spot_margin_ltv": 1.5,
        })
        assert rm.spot_margin_ltv == 1.0

    def test_clamped_to_unit_interval_low(self):
        """Negative LTV → clamped to 0 so the fallback contributes
        nothing (legacy behaviour preserved for that input).
        """
        rm = RiskManager({
            "risk_pct": 0.01, "min_balance_usd": 50,
            "spot_margin_ltv": -0.2,
        })
        assert rm.spot_margin_ltv == 0.0


# ---------------------------------------------------------------------------
# SHORT side: the headline trade-875 case
# ---------------------------------------------------------------------------


class TestShortSideLtvFallback:
    def test_zero_api_capacity_falls_back_to_ltv_collateral(self):
        """Headline contract for the bybit_2 trade-875 incident.
        With $186.87 USDT, BTC walletBalance=0, and Bybit returning
        ``availableToBorrow=0`` for BTC, the fallback uses
        ``186.87 × 0.5 = $93.435`` of borrow capacity.
        """
        spot_balances = {
            "base_coin": "BTC", "base_qty": 0.0,
            "base_usd_value": 0.0, "quote_usdt": 186.87,
            "quote_borrow_usd": 0.0, "base_borrow_qty": 0.0,
            "base_borrow_usd": 0.0, "total_account_usd": 186.87,
        }
        kwargs = _drive(
            pkg=_short_pkg(entry=80_220.7),
            spot_balances=spot_balances,
            acc=_account_stub(ltv=0.5),
        )
        expected = 186.87 * 0.5 * _SPOT_BUY_SAFETY_BUFFER
        assert kwargs["available_usd"] == pytest.approx(expected, rel=1e-6)
        assert kwargs["available_usd"] > 0.0

    def test_api_capacity_preferred_when_populated(self):
        """When Bybit DOES populate ``availableToBorrow``, the API
        path's capacity wins (it's the ground truth for the live
        tier). Fallback is for when the API returns 0, not a
        replacement.
        """
        spot_balances = {
            "base_coin": "BTC", "base_qty": 0.0,
            "base_usd_value": 0.0, "quote_usdt": 89.0,
            "quote_borrow_usd": 0.0,
            "base_borrow_qty": 0.5,    # API populated, 0.5 BTC line
            "base_borrow_usd": 0.0,
            "total_account_usd": 89.0,
        }
        pkg = _short_pkg(entry=50_000.0)
        kwargs = _drive(
            pkg=pkg, spot_balances=spot_balances, acc=_account_stub(),
        )
        # API path: 0.5 BTC × $50k = $25k.
        # Fallback:  $89 × 0.5 = $44.5 — much smaller.
        # max() picks the API path.
        api_path = 0.5 * 50_000.0 * _SPOT_BUY_SAFETY_BUFFER
        assert kwargs["available_usd"] == pytest.approx(api_path, rel=1e-6)

    def test_fallback_capped_by_max_borrow_btc(self):
        """``max_borrow_btc`` is the operator-configured ceiling.
        The fallback must never exceed it — even if collateral×LTV
        would imply more BTC than the tier allows.
        """
        spot_balances = {
            "base_coin": "BTC", "base_qty": 0.0,
            "base_usd_value": 0.0, "quote_usdt": 100_000.0,
            "quote_borrow_usd": 0.0, "base_borrow_qty": 0.0,
            "base_borrow_usd": 0.0, "total_account_usd": 100_000.0,
        }
        pkg = _short_pkg(entry=80_000.0)
        # Fallback would imply 100k × 0.5 / 80k = 0.625 BTC, but the
        # operator capped max_borrow_btc at 0.1.
        acc = _account_stub(ltv=0.5, max_borrow_btc=0.1)
        kwargs = _drive(pkg=pkg, spot_balances=spot_balances, acc=acc)
        # Capped: 0.1 BTC × $80k × 0.995 = $7960.
        expected = 0.1 * 80_000.0 * _SPOT_BUY_SAFETY_BUFFER
        assert kwargs["available_usd"] == pytest.approx(expected, rel=1e-6)

    def test_zero_ltv_disables_fallback(self):
        """Operator can set ``spot_margin_ltv: 0`` to opt out of the
        fallback (e.g. while debugging, or if the operator wants a
        strictly API-driven sizing path). Reverts to the pre-S-056
        zero-capacity refusal.
        """
        spot_balances = {
            "base_coin": "BTC", "base_qty": 0.0,
            "base_usd_value": 0.0, "quote_usdt": 186.87,
            "quote_borrow_usd": 0.0, "base_borrow_qty": 0.0,
            "base_borrow_usd": 0.0, "total_account_usd": 186.87,
        }
        kwargs = _drive(
            pkg=_short_pkg(),
            spot_balances=spot_balances,
            acc=_account_stub(ltv=0.0),
        )
        assert kwargs["available_usd"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# LONG side: leveraged buy on a USDT-only wallet
# ---------------------------------------------------------------------------


class TestLongSideLtvFallback:
    def test_long_uses_collateral_plus_ltv_when_api_empty(self):
        """Long-side fallback: with $186 USDT collateral and LTV 0.5,
        total spending power = collateral + (collateral × LTV)
                              = $186 × 1.5 = $279.
        That's the leverage path: existing cash + borrowable USDT.
        """
        spot_balances = {
            "base_coin": "BTC", "base_qty": 0.0,
            "base_usd_value": 0.0, "quote_usdt": 186.87,
            "quote_borrow_usd": 0.0, "base_borrow_qty": 0.0,
            "base_borrow_usd": 0.0, "total_account_usd": 186.87,
        }
        kwargs = _drive(
            pkg=_long_pkg(),
            spot_balances=spot_balances,
            acc=_account_stub(ltv=0.5),
        )
        expected = 186.87 * 1.5 * _SPOT_BUY_SAFETY_BUFFER
        assert kwargs["available_usd"] == pytest.approx(expected, rel=1e-6)

    def test_long_api_capacity_preferred_when_populated(self):
        """When Bybit's ``quote_borrow_usd`` is populated, the API
        path's total (cash + borrow) wins.
        """
        spot_balances = {
            "base_coin": "BTC", "base_qty": 0.0,
            "base_usd_value": 0.0, "quote_usdt": 100.0,
            "quote_borrow_usd": 400.0,   # plenty of borrow
            "base_borrow_qty": 0.0, "base_borrow_usd": 0.0,
            "total_account_usd": 100.0,
        }
        kwargs = _drive(
            pkg=_long_pkg(),
            spot_balances=spot_balances,
            acc=_account_stub(),
        )
        # API: 100 + 400 = 500. Fallback: 100 × 1.5 = 150. max=500.
        expected = 500.0 * _SPOT_BUY_SAFETY_BUFFER
        assert kwargs["available_usd"] == pytest.approx(expected, rel=1e-6)

    def test_long_residue_in_base_coin_does_not_brick_dispatch(self):
        """S-058 — the headline #574 incident.

        Spot-margin wallet held $185 of equity but it was sitting in
        BTC (residue from an orphaned LONG whose USDT borrow leg got
        repaid by the S-055 reconciler but whose BTC asset was never
        sold back to USDT). Pre-S-058 the fallback collateral input
        was ``quote_usdt`` (= $0 here) so available_usd collapsed to
        0 and every new vwap dispatch refused with
        ``zero_exchange_capacity`` — even though the account has
        $185 of real collateral against the USDT borrow line.

        After S-058 the fallback uses ``total_account_usd`` so the
        residue still counts as collateral.
        """
        spot_balances = {
            "base_coin": "BTC",
            "base_qty": 0.00231,           # ~$185 of BTC
            "base_usd_value": 185.08,
            "quote_usdt": 0.0,              # ← drained by orphaned LONG
            "quote_borrow_usd": 0.0,        # ← Bybit zeros this when
                                            #    walletBalance(USDT)=0
            "base_borrow_qty": 0.0,
            "base_borrow_usd": 0.0,
            "total_account_usd": 185.08,    # ← but totalEquity is intact
        }
        kwargs = _drive(
            pkg=_long_pkg(),
            spot_balances=spot_balances,
            acc=_account_stub(ltv=0.5),
        )
        # collateral_usd = 185.08, fallback_usd = 92.54.
        # api_avail_usd = 0 + 0 = 0.
        # effective = max(0, usdt_collateral=0 + 92.54) = 92.54.
        expected = 92.54 * _SPOT_BUY_SAFETY_BUFFER
        assert kwargs["available_usd"] == pytest.approx(expected, rel=1e-3)
        assert kwargs["available_usd"] > 0.0

    def test_short_residue_in_non_base_coin_uses_total_equity_fallback(self):
        """S-058 — short path with residue in a non-base coin.

        Equity is $185 (e.g. residue in ETH or another coin Bybit
        treats as collateral); ``base_qty=0`` so the API path's
        ``base_borrow_qty + base_qty`` term is empty. Pre-S-058 the
        fallback used ``quote_usdt`` (=0), zeroing capacity. After
        S-058 the fallback uses ``total_account_usd`` so the short
        can still size against the collateralised borrow line.

        (Residue in BTC itself is already covered by the API path —
        ``base_qty`` flows directly into ``api_base_qty``.)
        """
        spot_balances = {
            "base_coin": "BTC",
            "base_qty": 0.0,                # no BTC residue
            "base_usd_value": 0.0,
            "quote_usdt": 0.0,              # no USDT cash
            "quote_borrow_usd": 0.0,
            "base_borrow_qty": 0.0,         # API empty
            "base_borrow_usd": 0.0,
            "total_account_usd": 185.08,    # equity held in some
                                            # other coin (ETH, etc.)
        }
        kwargs = _drive(
            pkg=_short_pkg(entry=80_000.0),
            spot_balances=spot_balances,
            acc=_account_stub(ltv=0.5, max_borrow_btc=0.5),
        )
        # collateral_usd = 185.08, fallback_usd = 92.54,
        # fallback_btc_qty = 92.54 / 80k = 0.001157 BTC (under cap),
        # effective_usd = 0.001157 × 80k = 92.54.
        expected = 92.54 * _SPOT_BUY_SAFETY_BUFFER
        assert kwargs["available_usd"] == pytest.approx(expected, rel=1e-3)
        assert kwargs["available_usd"] > 0.0


# ---------------------------------------------------------------------------
# Defence boundary: non-spot-margin accounts unchanged
# ---------------------------------------------------------------------------


class TestNonSpotMarginUnchanged:
    def test_cash_spot_short_side_path_unchanged(self):
        """A cash-spot account (``market_type: spot``) hits the
        elif/else branches and gets ``available_usd=None``. The
        fallback is gated on _is_spot_margin so cash spot stays
        bit-identical.
        """
        # When _is_spot_margin is False, the if-block doesn't run and
        # the elif at line 826 sets balance/available_usd. We assert
        # by changing the account stub's market_type and confirming
        # available_usd stays None for SHORT.
        from src.core.coordinator import Coordinator

        spot_balances = {
            "base_coin": "BTC", "base_qty": 0.0,
            "base_usd_value": 0.0, "quote_usdt": 100.0,
            "quote_borrow_usd": 0.0, "base_borrow_qty": 0.0,
            "base_borrow_usd": 0.0, "total_account_usd": 100.0,
        }
        acc = _account_stub()
        acc.market_type = "spot"   # cash spot, not spot-margin

        with patch(
            "src.units.accounts.risk.RiskManager.position_size",
            return_value=0.0,
        ) as mock_size, patch(
            "src.units.accounts.load_accounts", return_value=[acc],
        ), patch(
            "src.core.coordinator._log_new_order_package",
            return_value="pkg-fake",
        ), patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=MagicMock(),
        ), patch(
            "src.units.accounts.execute._fetch_spot_coin_balances",
            return_value=spot_balances,
        ):
            coord = Coordinator()
            coord.multi_account_execute(
                _short_pkg(), accounts_path="/dev/null",
                balance_fetcher=lambda a: 99_999.0,
            )
        _positional, kwargs = mock_size.call_args
        # Cash-spot SHORT path sets available_usd = None.
        assert kwargs["available_usd"] is None
