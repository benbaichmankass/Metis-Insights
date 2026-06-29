"""S-026 G2 — Position sizing moves into the per-account RiskManager.

Pins the contract: ``RiskManager.position_size(pkg, balance_usd) -> qty``
is the *only* function that decides quantity in the codebase. Inputs:
the strategy's trade idea (entry/sl) and the per-account balance.
Output: qty in base-asset units.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

# pipeline.py needs matplotlib at import time via signal_notifications.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager


def _pkg(entry: float = 50_000.0, sl: float = 49_500.0) -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=entry,
        sl=sl,
        tp=51_000.0,
        confidence=0.7,
    )


class TestPositionSizeContract:
    """RiskManager.position_size is the single sizing entry-point post-G2."""

    def test_balance_drives_qty(self):
        """Same package, two balances → two different qtys.
        risk_pct=0.01, distance=500. balance=10_000 → 100/500=0.2;
        balance=1_000 → 10/500=0.02.

        leverage=100 keeps the 2026-05-12 margin pre-flight cap from
        binding (with leverage=1 the buffer fallback would clamp
        balance=10_000 to 0.18) so this isolates the risk-% sizing.
        """
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50,
                          "leverage": 100})
        pkg = _pkg(entry=50_000.0, sl=49_500.0)

        qty_big = rm.position_size(pkg, balance_usd=10_000.0)
        qty_small = rm.position_size(pkg, balance_usd=1_000.0)

        assert qty_big == pytest.approx(0.2, rel=1e-3)
        assert qty_small == pytest.approx(0.02, rel=1e-3)
        assert qty_big > qty_small, (
            "Bigger balance must size into a bigger position"
        )

    def test_two_accounts_two_qtys(self):
        """Same package, two RiskManagers (different balances) → two qtys.
        Pins the multi-account contract from the sprint prompt.

        leverage=100 keeps the 2026-05-12 margin pre-flight cap from
        binding so this isolates the risk-% sizing.
        """
        rm_a = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50,
                            "leverage": 100})
        rm_b = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50,
                            "leverage": 100})
        pkg = _pkg(entry=50_000.0, sl=49_500.0)

        qty_a = rm_a.position_size(pkg, balance_usd=5_000.0)
        qty_b = rm_b.position_size(pkg, balance_usd=500.0)

        assert qty_a == pytest.approx(0.1, rel=1e-3)
        assert qty_b == pytest.approx(0.01, rel=1e-3)

    def test_non_positive_balance_returns_zero(self):
        """The only balance gate left (the arbitrary ``min_balance_usd``
        floor was removed 2026-06-24): a NON-POSITIVE balance refuses to
        size (physics — you can't risk a fraction of zero). A small
        positive balance no longer refuses on a floor; it sizes off the
        risk budget (subject to the margin cap + exchange min lot).

        leverage=100 keeps the 2026-05-12 margin pre-flight cap from
        clamping the small-positive-balance case so this isolates the
        balance gate.
        """
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50,
                          "leverage": 100})
        pkg = _pkg()

        # Non-positive — refuse to size (the only balance floor: physics).
        assert rm.position_size(pkg, balance_usd=0.0) == 0.0
        assert rm.position_size(pkg, balance_usd=-5.0) == 0.0

        # No arbitrary $50 min-balance floor remains: a balance whose 1%-risk
        # budget clears the exchange min lot SIZES. entry/sl give a $500
        # risk-distance, so $50 at 1% = $0.50 risk == exactly the 0.001 BTC lot.
        assert rm.position_size(pkg, balance_usd=50.0) > 0.0

        # But a balance too small to afford the min lot at the configured risk
        # now REFUSES (0.0) rather than being bumped UP to the min lot — the bump
        # silently over-risked the account (#3910 Item 3, operator-approved
        # refuse 2026-06-28). $10 / $49.99 at 1% give < the 0.001-lot's $0.50.
        assert rm.position_size(pkg, balance_usd=49.99) == 0.0
        assert rm.position_size(pkg, balance_usd=10.0) == 0.0

    def test_default_risk_pct_is_one_percent(self):
        """Operator-confirmed default: 1% balance per trade."""
        rm = RiskManager({})  # all defaults
        assert rm.risk_pct == 0.01
        # No ``min_balance_usd`` attribute anymore — the arbitrary
        # minimum-balance floor was removed 2026-06-24.
        assert not hasattr(rm, "min_balance_usd")

    def test_no_max_position_clamp(self):
        """Operator directive: no hard-coded max-position cap on sizing.
        A huge balance must scale qty proportionally — no upper clip.

        S-026 G3: the daily-loss budget gate IS a sizing-time clamp;
        bump ``daily_usd`` high so this assertion isolates the
        "no max-position clamp" property.

        2026-05-12: the margin pre-flight cap is also a sizing-time
        ceiling; set ``leverage`` high so it never binds and the
        assertion stays scoped to the no-max-position-clamp property.
        """
        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 1_000_000_000,  # disable daily-loss gate
            "leverage": 100,  # disable margin pre-flight cap
        })
        pkg = _pkg(entry=50_000.0, sl=49_500.0)

        # Balance = $1M, risk = $10k, distance = $500 → qty = 20.
        qty = rm.position_size(pkg, balance_usd=1_000_000.0)
        assert qty == pytest.approx(20.0, rel=1e-3), (
            "S-026 G2: no max-position clamp — qty must scale linearly with balance"
        )

    def test_smoke_test_order_bypasses_sizing(self):
        """meta.is_test=True orders use meta.test_qty (or default), not risk math."""
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50})
        pkg = OrderPackage(
            strategy="smoke_test",
            symbol="BTCUSDT",
            direction="long",
            entry=50_000.0,
            sl=49_500.0,
            tp=51_000.0,
            meta={"is_test": True, "test_qty": 0.0001},
        )
        # Smoke test bypasses the balance gate too — the whole point is
        # to exercise the live plumbing without sizing real risk in.
        assert rm.position_size(pkg, balance_usd=0.0) == pytest.approx(0.0001)

    def test_legacy_strategy_risk_pct_meta_is_ignored(self):
        """The per-strategy risk multiplier was removed 2026-06-29: sizing is
        the account-level risk_pct basis only (× the RiskManager confidence
        scalar, which is ``off``/1.0 here). A leftover
        meta["strategy_risk_pct"] must NOT change the size.

        leverage=100 keeps the 2026-05-12 margin pre-flight cap from binding.
        """
        rm = RiskManager({"risk_pct": 0.01, "leverage": 100})
        pkg_plain = _pkg()
        pkg_legacy = _pkg()
        pkg_legacy.meta = {"strategy_risk_pct": 0.5}  # legacy field — must be ignored

        qty_plain = rm.position_size(pkg_plain, balance_usd=10_000.0)
        qty_legacy = rm.position_size(pkg_legacy, balance_usd=10_000.0)

        assert qty_legacy == pytest.approx(qty_plain, rel=1e-3)


class TestSizeOrderFromCfgDelegatesToRiskManager:
    """Backwards-compat: size_order_from_cfg now goes through RiskManager."""

    def test_cfg_with_risk_pct_produces_same_qty_as_risk_manager(self):
        from src.units.accounts.risk import size_order_from_cfg

        cfg = {"risk_pct": 0.02, "min_balance_usd": 50}
        pkg = _pkg(entry=50_000.0, sl=49_000.0)

        qty_via_cfg = size_order_from_cfg(pkg, cfg, balance_usdt=10_000.0)
        qty_via_rm = RiskManager(cfg).position_size(pkg, balance_usd=10_000.0)

        assert qty_via_cfg == qty_via_rm


class TestMultiAccountDispatchSizesPerAccount:
    """Coordinator.multi_account_execute calls position_size per account."""

    def _stub_accounts(self, monkeypatch):
        """Return a fake load_accounts() that yields two accounts, both
        with their own RiskManager, place_order returning a dry trade-id.

        leverage=100 keeps the 2026-05-12 margin pre-flight cap from
        clamping the risk-% qtys these tests pin.
        """

        rm_a = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50,
                            "leverage": 100})
        rm_b = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50,
                            "leverage": 100})

        class _Account:
            def __init__(self, name, rm):
                self.name = name
                self.exchange = "bybit"
                self.account_type = "regular"
                self.risk_manager = rm
                self.dry_run = True
                # Coordinator.multi_account_execute builds account_cfg from
                # account.api_key_env (bare attribute access) — stub fixtures
                # must carry it or dispatch raises AttributeError.
                self.api_key_env = ""
                self.market_type = "spot"
                self.calls = []

            def place_order(self, pkg, *, dry_run=None):
                self.calls.append(pkg)
                return f"dry-{self.name}-1"

        accounts = [_Account("acc_a", rm_a), _Account("acc_b", rm_b)]

        def _fake_load(_path):
            return accounts

        monkeypatch.setattr("src.units.accounts.load_accounts", _fake_load)
        return accounts

    def test_two_balances_yield_two_qtys(self, monkeypatch, tmp_path):
        """Pins the sprint contract: same pkg, two balances → two qtys."""
        from src.core.coordinator import Coordinator

        self._stub_accounts(monkeypatch)
        # Need a path that "exists" so the FileNotFoundError branch
        # isn't taken — point at a tmp file so load_accounts is reached.
        accounts_path = tmp_path / "accounts.yaml"
        accounts_path.write_text("accounts: {}\n")

        pkg = _pkg(entry=50_000.0, sl=49_500.0)
        # Stash per-account balances on the package so the default
        # balance_fetcher reads them.
        pkg.meta = {
            "account_balances_usd": {"acc_a": 10_000.0, "acc_b": 1_000.0},
        }

        coord = Coordinator()
        results = coord.multi_account_execute(
            pkg, accounts_path=str(accounts_path), dry_run=True,
        )

        names = {r["name"]: r for r in results}
        assert names["acc_a"]["sized_qty"] == pytest.approx(0.2, rel=1e-3)
        assert names["acc_b"]["sized_qty"] == pytest.approx(0.02, rel=1e-3)

        # Map stamped on pkg.meta for downstream readers.
        sized = (pkg.meta or {}).get("sized_qty_by_account") or {}
        assert sized["acc_a"] == pytest.approx(0.2, rel=1e-3)
        assert sized["acc_b"] == pytest.approx(0.02, rel=1e-3)

        # Both accounts were routed. (Routing now goes through
        # execute_pkg, not account.place_order — see S-028 — so the
        # "was routed" signal is a result row with a trade_id and no
        # error rather than a place_order call count.)
        assert names["acc_a"]["trade_id"] is not None
        assert names["acc_a"]["error"] is None
        assert names["acc_b"]["trade_id"] is not None
        assert names["acc_b"]["error"] is None

    def test_zero_balance_account_is_skipped(self, monkeypatch, tmp_path):
        """An account with a non-positive balance produces qty=0 and is
        NOT routed. The arbitrary min-balance floor was removed
        2026-06-24, so the only balance refusal is a non-positive
        balance — feed acc_b $0 to exercise it (a small positive balance
        no longer refuses on a floor)."""
        from src.core.coordinator import Coordinator

        self._stub_accounts(monkeypatch)
        accounts_path = tmp_path / "accounts.yaml"
        accounts_path.write_text("accounts: {}\n")

        pkg = _pkg(entry=50_000.0, sl=49_500.0)
        pkg.meta = {
            "account_balances_usd": {"acc_a": 5_000.0, "acc_b": 0.0},
        }

        coord = Coordinator()
        results = coord.multi_account_execute(
            pkg, accounts_path=str(accounts_path), dry_run=True,
        )

        names = {r["name"]: r for r in results}
        assert names["acc_a"]["sized_qty"] > 0
        assert names["acc_a"]["error"] is None
        assert names["acc_b"]["sized_qty"] == 0.0
        assert "zero_balance" in names["acc_b"]["error"]

        # acc_a routed; acc_b skipped (qty=0). Routing now goes through
        # execute_pkg, not account.place_order (S-028), so "routed" is a
        # trade_id with no error and "skipped" is a None trade_id with a
        # zero_balance error.
        assert names["acc_a"]["trade_id"] is not None
        assert names["acc_b"]["trade_id"] is None

    def test_balance_fetcher_override(self, monkeypatch, tmp_path):
        """Caller can inject a custom balance fetcher (live processor wiring path)."""
        from src.core.coordinator import Coordinator

        self._stub_accounts(monkeypatch)
        accounts_path = tmp_path / "accounts.yaml"
        accounts_path.write_text("accounts: {}\n")

        pkg = _pkg(entry=50_000.0, sl=49_500.0)

        balances = {"acc_a": 2_000.0, "acc_b": 8_000.0}
        coord = Coordinator()
        results = coord.multi_account_execute(
            pkg,
            accounts_path=str(accounts_path),
            dry_run=True,
            balance_fetcher=lambda acc: balances[acc.name],
        )

        names = {r["name"]: r for r in results}
        # acc_a: 2000 * 0.01 / 500 = 0.04
        # acc_b: 8000 * 0.01 / 500 = 0.16
        assert names["acc_a"]["sized_qty"] == pytest.approx(0.04, rel=1e-3)
        assert names["acc_b"]["sized_qty"] == pytest.approx(0.16, rel=1e-3)
