"""S-043 — M3 order-layer refusal tests (gap closers).

Closes the M3 "order-layer refusal tests" gap. Every refusal path on
the order route is enumerated below and pinned to a stable
status/reason token so downstream code (the trade-journal writer in
``src/units/accounts/execute.py``, the rejection renderers in
``src/ui/processor.py``, the Telegram surface, the diagnostic-pings
in ``src/runtime/execution_diagnostics.py``) cannot drift silently.

The two layers covered:

* ``safe_place_order`` (``src/runtime/orders.py``) — payload validation
  + halt-flag + risk-cap rail. Refusals here do **not** depend on a
  per-account ``RiskManager``; they are the process-level guards every
  order traverses regardless of routing.
* ``RiskManager.evaluate`` (``src/units/accounts/risk.py``) — per-account
  risk gate. Returns ``(allow, reason)``; the reason token is the stable
  contract surface (logged into ``trade_journal.entry_reason``, rendered
  by ``processor.format_packages``, surfaced via ``/last5`` etc.).

Pre-existing tests already cover the ValueError-raising paths and the
RiskManager.approve() boolean shape. This file fills the remaining gaps:

1. Non-dict order input → "order must be a dictionary".
2. Empty / missing symbol → "symbol is required".
3. Whitespace-only symbol (after .strip()) → "symbol is required".
4. ``RiskManager.evaluate()`` direct (allow, reason) tuple for every
   reject path — pins the exact reason tokens that downstream
   contracts depend on.
5. ``account_mode_dry_run`` reason token (the single dry/live toggle
   in the codebase per operator directive 2026-05-03).
6. Smoke-test orders bypass even when ``dry_run=True`` (test orders
   exercise the live plumbing path; the bypass is checked first).
7. Halt flag takes precedence over risk caps (the kill-switch is the
   first gate after payload validation).
"""
from __future__ import annotations

import pytest

from src.core.coordinator import OrderPackage
from src.runtime.orders import safe_place_order
from src.units.accounts.risk import RiskManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Client:
    """Stand-in exchange client; never exercised by refusal-path tests."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def place_order(self, **order):
        self.calls.append(order)
        return {"ok": True, "order_id": "abc123"}


def _settings(**overrides):
    base: dict = {}
    base.update(overrides)
    return base


def _order(**overrides):
    base = {"symbol": "BTCUSDT", "side": "buy", "qty": 1.0, "price": 50_000.0}
    base.update(overrides)
    return base


def _pkg(estimated_value: float | None = 100.0) -> OrderPackage:
    """Real-shape OrderPackage — the type RiskManager.evaluate consumes."""
    meta: dict = {"strategy_name": "vwap"}
    if estimated_value is not None:
        meta["estimated_value"] = estimated_value
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=50_000.0,
        sl=49_000.0,
        tp=52_000.0,
        confidence=0.8,
        meta=meta,
    )


def _smoke_pkg(test_qty: float = 0.0001) -> OrderPackage:
    """Smoke-test OrderPackage — meta.is_test=True bypasses the risk gate."""
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="long",
        entry=50_000.0,
        sl=49_000.0,
        tp=52_000.0,
        confidence=0.0,
        meta={
            "strategy_name": "vwap",
            "is_test": True,
            "test_qty": test_qty,
            "smoke_id": "deadbeef",
        },
    )


# ---------------------------------------------------------------------------
# safe_place_order — payload validation gaps
# ---------------------------------------------------------------------------


class TestPayloadValidationRefusals:
    """The first line of defence: ``safe_place_order`` validates the
    order dict shape before any risk math. These three paths previously
    had no test."""

    def test_non_dict_order_input_returns_failed_validation(self):
        """A non-dict order (None, list, str, int) is rejected before
        any field lookup. This protects the call site from AttributeError
        when a strategy returns the wrong shape."""
        result = safe_place_order(None, _settings(), _Client())
        assert result["status"] == "failed_validation"
        assert "must be a dictionary" in result["reason"]

    def test_list_order_input_returns_failed_validation(self):
        result = safe_place_order(["BTCUSDT", "buy", 1.0], _settings(), _Client())
        assert result["status"] == "failed_validation"
        assert "must be a dictionary" in result["reason"]

    def test_string_order_input_returns_failed_validation(self):
        result = safe_place_order("not-a-dict", _settings(), _Client())
        assert result["status"] == "failed_validation"
        assert "must be a dictionary" in result["reason"]

    def test_missing_symbol_returns_failed_validation(self):
        """``symbol`` is the first business-field check after the dict
        guard. Missing key resolves to '' via ``.get(..., "")`` and is
        rejected by the truthy check."""
        result = safe_place_order(
            {"side": "buy", "qty": 1.0}, _settings(), _Client()
        )
        assert result["status"] == "failed_validation"
        assert "symbol is required" in result["reason"]

    def test_empty_string_symbol_returns_failed_validation(self):
        result = safe_place_order(
            {"symbol": "", "side": "buy", "qty": 1.0}, _settings(), _Client()
        )
        assert result["status"] == "failed_validation"
        assert "symbol is required" in result["reason"]

    def test_whitespace_only_symbol_returns_failed_validation(self):
        """``symbol`` is normalized via ``.strip().upper()`` so whitespace
        collapses to an empty string and triggers the missing-symbol guard."""
        result = safe_place_order(
            {"symbol": "   ", "side": "buy", "qty": 1.0},
            _settings(),
            _Client(),
        )
        assert result["status"] == "failed_validation"
        assert "symbol is required" in result["reason"]


# ---------------------------------------------------------------------------
# safe_place_order — halt-flag precedence
# ---------------------------------------------------------------------------


class TestHaltFlagPrecedence:
    """The kill-switch (halt flag) is the first gate after payload
    validation. It must take precedence even when a risk cap would
    independently refuse — otherwise a misconfigured cap could mask
    the operator's `/halt` intent."""

    def test_halt_flag_beats_max_position_usd_cap(self, tmp_path):
        """Both halt AND MAX_POSITION_USD would refuse this order; halt
        wins. Confirms the order of checks: halt is evaluated before
        the hard risk guards."""
        flag = tmp_path / "halt.flag"
        flag.write_text("halted")
        settings = _settings(
            HALT_FLAG_PATH=str(flag),
            MAX_POSITION_USD="10",  # would also fire
        )
        # Notional 50_000 USD vs MAX_POSITION_USD=10 — would raise
        # ValueError if halt didn't gate first.
        result = safe_place_order(_order(), settings, _Client())
        assert result["status"] == "halted"
        assert result["reason"] == "halt_flag_active"

    def test_halt_flag_beats_max_qty_cap(self, tmp_path):
        flag = tmp_path / "halt.flag"
        flag.write_text("halted")
        settings = _settings(
            HALT_FLAG_PATH=str(flag),
            MAX_QTY="0.001",  # qty=1.0 would also fail
        )
        result = safe_place_order(_order(), settings, _Client())
        assert result["status"] == "halted"

    def test_halt_flag_beats_max_open_positions(self, tmp_path):
        flag = tmp_path / "halt.flag"
        flag.write_text("halted")
        settings = _settings(
            HALT_FLAG_PATH=str(flag),
            MAX_OPEN_POSITIONS="1",
            CURRENT_OPEN_POSITIONS="5",  # would also fire
        )
        result = safe_place_order(_order(), settings, _Client())
        assert result["status"] == "halted"


# ---------------------------------------------------------------------------
# RiskManager.evaluate — direct (allow, reason) tuple contract
# ---------------------------------------------------------------------------


class TestRiskManagerEvaluateReasons:
    """``RiskManager.approve()`` returns a bool; ``evaluate()`` returns
    ``(allow, reason)``. The reason token is the stable contract surface
    consumed by ``execute_pkg`` (writes it to ``trade_journal.entry_reason``)
    and by the UI layer (``processor.format_packages`` uses it for the
    "REJECTED: X" prefix). Existing tests pin ``approve()`` only — these
    pin the reason vocabulary directly."""

    def test_evaluate_clean_account_returns_allow_none(self):
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0}
        )
        ok, reason = rm.evaluate(_pkg(estimated_value=100.0))
        assert ok is True
        assert reason is None

    def test_evaluate_daily_loss_cap_uses_DAILY_LOSS_CAP_token(self):
        """``DAILY_LOSS_CAP`` is referenced as a literal in
        ``tests/test_packages_command.py`` and ``processor.format_packages``;
        if the token drifts, the rejection-renderer breaks silently."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0}
        )
        rm.daily_pnl = -150.0  # past the daily loss cap
        ok, reason = rm.evaluate(_pkg(estimated_value=100.0))
        assert ok is False
        assert reason == "DAILY_LOSS_CAP"

    def test_evaluate_position_size_cap_uses_POSITION_SIZE_CAP_token(self):
        """``POSITION_SIZE_CAP`` is the second canonical reason token.
        Pin it so the rejection-renderer / journal contract holds."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0}
        )
        ok, reason = rm.evaluate(_pkg(estimated_value=600.0))
        assert ok is False
        assert reason == "POSITION_SIZE_CAP"

    def test_evaluate_intraday_drawdown_uses_INTRADAY_DRAWDOWN_token(self):
        """The drawdown reason token is the third canonical one;
        ``execute_pkg`` writes it through to the trade journal."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 1_000.0, "pos_size": 1_000.0}
        )
        rm.update_equity(10_000.0)
        rm.update_equity(9_500.0)  # exactly 5 % drawdown — at cap
        ok, reason = rm.evaluate(_pkg(estimated_value=100.0))
        assert ok is False
        assert reason == "INTRADAY_DRAWDOWN"

    def test_evaluate_with_no_estimated_value_skips_position_size_check(self):
        """When meta omits ``estimated_value``, the position-size check
        cannot run and is skipped. The order should pass the size gate
        and (with a clean account) be approved with reason=None."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0}
        )
        ok, reason = rm.evaluate(_pkg(estimated_value=None))
        assert ok is True
        assert reason is None

    def test_evaluate_position_at_exact_cap_passes(self):
        """The cap is ``> max_pos_size_usd`` (strict greater-than) so an
        order at exactly the cap is accepted — boundary pin."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0}
        )
        ok, reason = rm.evaluate(_pkg(estimated_value=500.0))
        assert ok is True
        assert reason is None

    def test_evaluate_daily_loss_at_exact_cap_passes(self):
        """``daily_pnl < -max_daily_loss_usd`` is strict; PnL of exactly
        ``-cap`` is accepted. Boundary pin matches ``approve()`` shape."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0}
        )
        rm.daily_pnl = -100.0
        ok, reason = rm.evaluate(_pkg(estimated_value=100.0))
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# RiskManager.evaluate — account_mode_dry_run (the single dry/live toggle)
# ---------------------------------------------------------------------------


class TestEvaluateAccountModeDryRun:
    """Per operator directive 2026-05-03 (BUG-039) the per-account
    ``mode: live | dry_run`` field in ``config/accounts.yaml`` is the
    **only** dry/live toggle in the codebase. ``RiskManager.dry_run=True``
    is set from that field; ``evaluate()`` then refuses every real order
    with the canonical reason ``"account_mode_dry_run"`` so the executor
    logs the would-be trade to the journal but never calls the exchange.

    These tests pin the exact reason token (lowercase, with underscores)
    because it is stable surface consumed by:
      * ``src/units/accounts/execute.py`` → trade-journal entry_reason
      * ``scripts/check_dry_run_in_diff.py`` → live-mode CI guard
      * ``tests/test_multi_account_execute_per_account_mode.py`` (assert)
    """

    def test_evaluate_dry_run_mode_refuses_with_canonical_reason(self):
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0},
            dry_run=True,
        )
        ok, reason = rm.evaluate(_pkg(estimated_value=100.0))
        assert ok is False
        assert reason == "account_mode_dry_run"

    def test_evaluate_dry_run_mode_takes_precedence_over_other_caps(self):
        """When dry_run is True AND a risk cap would also reject (e.g.
        oversize), the dry_run reason wins. The per-account mode is the
        first gate after the smoke-test bypass — the executor is supposed
        to record `account_mode_dry_run` regardless of what cap *would*
        have fired in live mode."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0},
            dry_run=True,
        )
        rm.daily_pnl = -200.0  # would also trigger DAILY_LOSS_CAP
        ok, reason = rm.evaluate(_pkg(estimated_value=999.0))  # oversize
        assert ok is False
        assert reason == "account_mode_dry_run"

    def test_evaluate_live_mode_default_passes_clean_orders(self):
        """``dry_run`` defaults to False (live). No mode flag means the
        order proceeds through the rest of the gates. Pin to prevent a
        regression where dry_run becomes the default."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0}
        )
        # No dry_run kwarg → defaults to False per __init__.
        assert rm.dry_run is False
        ok, reason = rm.evaluate(_pkg(estimated_value=100.0))
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# Smoke-test bypass (meta.is_test=True)
# ---------------------------------------------------------------------------


class TestSmokeTestBypass:
    """A smoke-test order (``meta.is_test=True``) is a sub-min-lot probe
    designed to exercise the live exchange-rejection path. It must
    bypass every risk gate inside ``evaluate()`` because:

    1. Its qty is intentionally below the exchange min-lot — the
       exchange's rejection is the success signal.
    2. It carries no realistic ``estimated_value`` (often inflated for
       diagnostic visibility), so the position-size cap is meaningless.
    3. It must run even when the account is in dry_run mode, otherwise
       the operator can never validate that live plumbing works on a
       paper account before flipping to live.

    The contract is documented in ``RiskManager.evaluate.__doc__`` and
    BUG-038 (smoke-test live-plumbing fix). Pin it here."""

    def test_smoke_order_bypasses_all_caps_and_dry_run(self):
        """A smoke-test order with insanely-bad risk params + dry_run=True
        + breached daily loss + oversize estimated_value still returns
        ``(True, None)`` because the test bypass is checked first."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0},
            dry_run=True,
        )
        rm.daily_pnl = -1_000.0  # would block real orders
        rm.update_equity(10_000.0)
        rm.update_equity(5_000.0)  # 50 % drawdown
        smoke = _smoke_pkg()
        smoke.meta["estimated_value"] = 999_999.0  # would block real orders
        ok, reason = rm.evaluate(smoke)
        assert ok is True
        assert reason is None

    def test_smoke_order_bypasses_dry_run_mode_specifically(self):
        """The smoke bypass beats the dry_run gate. This is essential
        for BUG-038's fix: smoke tests must reach the exchange even on
        dry_run accounts to validate live plumbing."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0},
            dry_run=True,
        )
        ok, reason = rm.evaluate(_smoke_pkg())
        assert ok is True
        assert reason is None

    def test_real_order_without_is_test_flag_does_not_bypass(self):
        """Inverse pin: an order without ``meta.is_test=True`` is a real
        order and is subject to every gate. Confirms the bypass key is
        ``is_test`` specifically."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0},
            dry_run=True,
        )
        pkg = _pkg(estimated_value=100.0)
        # Sanity: pkg.meta has no is_test flag.
        assert "is_test" not in pkg.meta
        ok, reason = rm.evaluate(pkg)
        assert ok is False
        assert reason == "account_mode_dry_run"

    def test_smoke_order_with_is_test_false_does_not_bypass(self):
        """Defensive: ``is_test=False`` (explicit) is treated like a real
        order, same as the absent key. The bypass requires a truthy
        ``is_test`` value."""
        rm = RiskManager(
            {"max_dd_pct": 0.05, "daily_usd": 100.0, "pos_size": 500.0},
            dry_run=True,
        )
        pkg = _pkg(estimated_value=100.0)
        pkg.meta["is_test"] = False
        ok, reason = rm.evaluate(pkg)
        assert ok is False
        assert reason == "account_mode_dry_run"


# ---------------------------------------------------------------------------
# safe_place_order — exchange-not-called proof on every refusal path
# ---------------------------------------------------------------------------


class TestExchangeNotCalledOnRefusal:
    """Every refusal path must short-circuit before ``client.place_order``.
    This is a separate concern from "did we return the right reason?" —
    a future refactor could swap a reason but accidentally call the
    exchange anyway. Pin the no-call invariant on each gate."""

    def test_failed_validation_does_not_call_exchange(self):
        client = _Client()
        safe_place_order(
            {"symbol": "BTCUSDT", "side": "hold", "qty": 1.0},
            _settings(),
            client,
        )
        assert client.calls == []

    def test_halt_does_not_call_exchange(self, tmp_path):
        flag = tmp_path / "halt.flag"
        flag.write_text("halted")
        client = _Client()
        safe_place_order(
            _order(),
            _settings(HALT_FLAG_PATH=str(flag)),
            client,
        )
        assert client.calls == []

    def test_max_qty_refusal_does_not_call_exchange(self):
        client = _Client()
        safe_place_order(
            _order(qty=100.0),
            _settings(MAX_QTY="1.0"),
            client,
        )
        assert client.calls == []

    def test_per_strategy_refusal_does_not_call_exchange(self):
        client = _Client()
        safe_place_order(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "qty": 1.0,
                "meta": {"strategy_name": "vwap"},
            },
            _settings(
                MAX_POS_PER_STRATEGY="1",
                STRATEGY_OPEN_POSITIONS="1",  # at cap
            ),
            client,
        )
        assert client.calls == []

    def test_max_position_usd_refusal_does_not_call_exchange(self):
        client = _Client()
        with pytest.raises(ValueError, match="MAX_POSITION_USD"):
            safe_place_order(
                _order(qty=1.0, price=50_000.0),
                _settings(MAX_POSITION_USD="100"),
                client,
            )
        assert client.calls == []
