"""
Offline tests for the VWAP strategy runtime path.

All tests use fake candle data — no exchange calls, no secrets, no network.

Dependency note: requires pandas (listed in requirements.txt).
If pandas is absent the entire module is skipped via pytest.importorskip.
matplotlib is mocked so the pipeline import chain works without it installed.
"""
import sys
import types
from unittest import mock

# Provide a minimal matplotlib stub so pipeline.py can be imported without
# matplotlib installed (matplotlib is a transitive dep of signal_notifications).
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

import pytest

pd = pytest.importorskip("pandas")

# S-012 PR C5: VWAP helpers moved from strategies/vwap_signal_builder.py
# (deleted) into src/units/strategies/vwap.py.
from src.units.strategies.vwap import (  # noqa: E402
    MIN_CANDLES,
    ENTRY_STD_THRESHOLD,
    build_vwap_signal,
    compute_vwap,
)
from src.runtime.orders import safe_place_order  # noqa: E402
from src.runtime.pipeline import run_pipeline  # noqa: E402
from src.runtime.validation import validate_startup  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candles(*close_prices, volume=1000.0):
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    rows = []
    for i, close in enumerate(close_prices):
        rows.append({
            "timestamp": i,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": volume,
        })
    return pd.DataFrame(rows)


def _candles_below_vwap():
    """Candles where the last price is well below VWAP (triggers buy)."""
    # High prices dominate the window, then a sharp dip at the end.
    prices = [100, 102, 101, 103, 102, 60]
    return _candles(*prices)


def _candles_above_vwap():
    """Candles where the last price is well above VWAP (triggers sell)."""
    prices = [100, 98, 99, 97, 98, 140]
    return _candles(*prices)


def _candles_near_vwap():
    """Candles where the last price is within 1 std-dev of VWAP (no signal)."""
    prices = [100, 100, 100, 100, 100, 100]
    return _candles(*prices)


class DummyExchangeClient:
    def __init__(self):
        self.calls = []

    def place_order(self, **order):
        self.calls.append(order)
        return {"ok": True, "order": order}


class DummyTelegramClient:
    def __init__(self):
        self.messages = []

    def send_message(self, message: str):
        self.messages.append(message)


# ---------------------------------------------------------------------------
# Unit: compute_vwap
# ---------------------------------------------------------------------------

class TestComputeVwap:
    def test_basic_vwap_calculation(self):
        df = _candles(100, 102, 101)
        vwap = compute_vwap(df)
        # typical_price = (high + low + close) / 3
        # high = close + 2, low = close - 2 → typical = close
        # VWAP = mean of close prices (equal volume)
        expected = (100 + 102 + 101) / 3
        assert abs(vwap - expected) < 0.01

    def test_too_few_candles_raises(self):
        df = _candles(100)  # only 1 row
        with pytest.raises(ValueError, match="at least"):
            compute_vwap(df)

    def test_empty_dataframe_raises(self):
        df = pd.DataFrame()
        with pytest.raises(ValueError, match="non-empty"):
            compute_vwap(df)

    def test_zero_volume_raises(self):
        df = _candles(100, 102, volume=0)
        with pytest.raises(ValueError, match="volume"):
            compute_vwap(df)

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"close": [100, 102], "volume": [1, 1]})
        with pytest.raises(ValueError, match="missing columns"):
            compute_vwap(df)

    def test_vwap_weighted_by_volume(self):
        """Higher-volume candles should pull VWAP toward their typical price."""
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100, "volume": 1},
            {"timestamp": 1, "open": 199, "high": 202, "low": 198, "close": 200, "volume": 100},
        ])
        vwap = compute_vwap(df)
        assert vwap > 190, "VWAP should be pulled toward the high-volume candle"


# ---------------------------------------------------------------------------
# Unit: build_vwap_signal
# ---------------------------------------------------------------------------

class TestBuildVwapSignal:
    def test_buy_signal_when_price_below_vwap(self):
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        assert signal["symbol"] == "BTCUSDT"
        assert signal["meta"]["strategy_name"] == "vwap"
        assert signal["meta"]["current_price"] < signal["meta"]["vwap"]

    def test_sell_signal_when_price_above_vwap(self):
        df = _candles_above_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "sell"
        assert signal["meta"]["current_price"] > signal["meta"]["vwap"]

    def test_no_signal_when_price_near_vwap(self):
        df = _candles_near_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"

    def test_signal_includes_vwap_meta(self):
        df = _candles(100, 102, 101, 103, 100)
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert "vwap" in signal["meta"]
        assert "current_price" in signal["meta"]
        assert "std_dev" in signal["meta"]
        assert "deviation_std" in signal["meta"]
        assert "reason" in signal["meta"]

    def test_signal_does_not_carry_qty(self):
        """S-026 G1: strategies emit the trade idea, not the order.
        Sizing is decided per-account by the RiskManager, so the
        strategy package must never carry a top-level ``qty`` field."""
        for df in (_candles_below_vwap(), _candles_above_vwap(), _candles_near_vwap()):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
            assert "qty" not in signal, (
                f"S-026 G1: build_vwap_signal must not emit qty (got {signal!r})"
            )

    def test_insufficient_candles_raises(self):
        df = _candles(100)
        with pytest.raises(ValueError, match="at least"):
            build_vwap_signal(df, symbol="BTCUSDT")

    # ----- G5 (CP-2026-05-02-12, option a) — VWAP must populate sl/tp -----

    def test_buy_signal_carries_entry_sl_tp_at_top_level(self):
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        for k in ("entry_price", "stop_loss", "take_profit"):
            assert k in signal, (
                f"BUY signal missing top-level {k}; multi-account dispatch "
                f"requires it (signal_carries_full_sltp gate)"
            )
        # Mean-reversion: TP = VWAP, entry below VWAP, SL further below entry.
        assert signal["take_profit"] == signal["meta"]["vwap"]
        assert signal["entry_price"] < signal["take_profit"]
        assert signal["stop_loss"] < signal["entry_price"]

    def test_sell_signal_carries_entry_sl_tp_at_top_level(self):
        df = _candles_above_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "sell"
        for k in ("entry_price", "stop_loss", "take_profit"):
            assert k in signal
        # Mean-reversion: TP = VWAP, entry above VWAP, SL further above entry.
        assert signal["take_profit"] == signal["meta"]["vwap"]
        assert signal["entry_price"] > signal["take_profit"]
        assert signal["stop_loss"] > signal["entry_price"]

    def test_no_signal_does_not_emit_sl_tp(self):
        df = _candles_near_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        # SL/TP keys must be absent for no-trade signals; the multi-account
        # dispatch fast-path uses .get() and falls through correctly when
        # they're missing.
        assert "entry_price" not in signal
        assert "stop_loss" not in signal
        assert "take_profit" not in signal

    def test_sl_distance_uses_sl_std_mult(self):
        """The stop-loss distance from entry equals sl_std_mult * std_dev
        (subject to the ATR floor — the larger of the two wins).

        The default 0.5 reflects the 2026-05-17 revert (issue #1370): the
        3.16-year backtest with the HTF 4h ±2% gate (PR #1175) present
        showed widening SL from 0.5σ → 0.75σ cost ~63% of total R
        (V_1175_htf_only +411 R vs V_1175_1183_htf_sl +148 R). The ATR
        floor in build_vwap_signal still provides the noise guard PR #1183
        sought, without the R:R contract drift.
        """
        df = _candles_below_vwap()
        s_default = build_vwap_signal(df, symbol="BTCUSDT")
        s_wide = build_vwap_signal(df, symbol="BTCUSDT", sl_std_mult=2.0)

        std_dev = s_default["meta"]["std_dev"]
        atr = s_default["meta"]["atr"]
        # SL distance is max(sl_std_mult * std_dev, atr) — whichever is
        # larger wins. With the fixture's small std_dev relative to the
        # one-bar gap, the ATR floor dominates at the default 0.5σ;
        # at sl_std_mult=2.0 the std-dev term wins.
        d_default = s_default["entry_price"] - s_default["stop_loss"]
        d_wide = s_wide["entry_price"] - s_wide["stop_loss"]
        assert d_default == pytest.approx(max(0.5 * std_dev, atr), rel=1e-6)
        assert d_wide == pytest.approx(max(2.0 * std_dev, atr), rel=1e-6)

    def test_signal_is_packageable_after_g5_fix(self):
        """The signal returned by build_vwap_signal must satisfy the
        pipeline's _signal_carries_full_sltp predicate so the
        multi-account dispatch fast-path accepts it instead of falling
        into the legacy ALLOW_LIVE_TRADING gate."""
        from src.runtime.pipeline import _signal_carries_full_sltp

        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert _signal_carries_full_sltp(signal), (
            "Post-G5: VWAP signals must be packageable "
            "(this was the BUG)"
        )

    # ----- BUG-043: confidence must thread through to the journal -----

    def test_actionable_buy_signal_carries_nonzero_confidence(self):
        """BUG-043: pre-fix every VWAP order package logged as
        confidence=0.0 because build_vwap_signal never emitted the
        field. The pipeline's _signal_to_order_package then read
        meta.get("confidence") or 0.0 and silently zeroed every row.
        Pin a non-zero value at both top-level and meta so consumers
        (pipeline, renderer, journal) see a real conviction."""
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        assert "confidence" in signal, (
            "BUG-043: top-level confidence must be present so "
            "_extract_order_package_fields renders it"
        )
        assert signal["confidence"] > 0.0, (
            f"BUG-043: actionable buy signal must report non-zero "
            f"confidence (got {signal['confidence']!r})"
        )
        assert signal["meta"]["confidence"] == signal["confidence"], (
            "BUG-043: meta.confidence must mirror top-level so "
            "_signal_to_order_package threads it to OrderPackage"
        )
        assert signal["confidence"] <= 1.0, (
            "VWAP confidence formula caps at 1.0 — anything above is "
            "a regression in the rounding path"
        )

    def test_actionable_sell_signal_carries_nonzero_confidence(self):
        df = _candles_above_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "sell"
        assert signal["confidence"] > 0.0
        assert signal["meta"]["confidence"] == signal["confidence"]

    def test_no_signal_still_emits_confidence_field(self):
        """Even when the signal is non-actionable, meta.confidence
        must be present (and may be 0.0). This keeps the meta shape
        stable for downstream renderers."""
        df = _candles_near_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "confidence" in signal["meta"]

    # ----- 2026-05-03 operator directive: 1.0σ entry + 0.5σ stop = 1:2 R:R -----

    def test_entry_threshold_pinned_to_one_sigma_per_operator_directive(self):
        """CP-2026-05-03-20 operator directive: ENTRY_STD_THRESHOLD reverted
        from 2.0σ to 1.0σ to raise order-package cadence. Pin the value so
        a future Sharpe-tuning sprint that quietly re-raises it has to
        explicitly delete this test (and own the cadence regression)."""
        assert ENTRY_STD_THRESHOLD == 1.0, (
            "Operator directive 2026-05-03 fixed ENTRY_STD_THRESHOLD at "
            "1.0σ for cadence. Any change must come with a fresh "
            "out-of-sample threshold sweep + operator approval."
        )

    def test_sl_default_pinned_to_current_value(self):
        """2026-05-19 param sweep (S-VWAP-SWEEP-DISPATCH, issue #1569):
        12-combo ENTRY×SL sweep over 16 windows × 14 days ranked SL=0.3
        top-4 across the full ENTRY grid. ENTRY=1.0/SL=0.3 achieved
        mean_total_r=+4.88 vs SL=0.5 at -0.46 (rank 9/12). Tier-3 change
        approved by Ben; merged in PR #1571. The ATR-based floor in
        build_vwap_signal still provides the noise guard PR #1183 sought.
        Pins the current value so a future tuning sprint must explicitly
        update this test (and document the rationale) when it changes."""
        from src.units.strategies.vwap import SL_STD_MULT_DEFAULT
        assert SL_STD_MULT_DEFAULT == 0.3, (
            "2026-05-19 param sweep (issue #1569) set SL_STD_MULT_DEFAULT "
            "to 0.3 (from 0.5). Any further change requires a fresh "
            "out-of-sample SL sweep + operator approval."
        )

    def test_risk_reward_at_entry_boundary(self):
        """End-to-end pin of the R:R contract at the entry boundary.

        2026-05-19 param sweep (S-VWAP-SWEEP-DISPATCH, issue #1569)
        intentionally relaxed the 2026-05-03 2:1 directive: SL tightened
        from 0.5σ → 0.3σ while ENTRY stays at 1.0σ, giving a 3.33:1
        boundary R:R. The sweep justified this — SL=0.3 configs ranked
        top-4 out of 12. Operators tuning either value must move the other
        in lock-step or the R:R contract drifts. Realised R:R on signals
        that fire deeper than 1σ will exceed the floor."""
        from src.units.strategies.vwap import SL_STD_MULT_DEFAULT

        for df, side, direction_factor in (
            (_candles_below_vwap(), "buy", +1),  # reward = vwap - entry > 0
            (_candles_above_vwap(), "sell", -1),  # reward = entry - vwap > 0
        ):
            sig = build_vwap_signal(df, symbol="BTCUSDT")
            assert sig["side"] == side
            entry = sig["entry_price"]
            sl = sig["stop_loss"]
            tp = sig["take_profit"]

            risk = abs(entry - sl)
            reward = abs(tp - entry)
            assert risk > 0 and reward > 0

            # Pin the constant ratio so a change to either ENTRY_STD_THRESHOLD
            # or SL_STD_MULT_DEFAULT forces an explicit test update.
            boundary_rr = ENTRY_STD_THRESHOLD / SL_STD_MULT_DEFAULT
            assert boundary_rr == pytest.approx(1.0 / 0.3, rel=1e-6), (
                "Boundary R:R is ENTRY_STD_THRESHOLD / "
                "SL_STD_MULT_DEFAULT = 1.0 / 0.3 = 3.33:1 per the "
                "2026-05-19 param sweep (issue #1569, PR #1571). Update "
                "this test when either constant changes."
            )
            # The ATR floor (sl_distance = max(sl_sigma, 1 ATR)) can widen the
            # SL in synthetic fixtures where the last bar carries a large True
            # Range (dramatic price drop/spike). When ATR dominates, realized
            # R:R falls below boundary_rr. The constant pin above is the
            # real contract guard; here we verify the signal is at minimum
            # profitable (reward > risk, i.e. R:R > 1).
            assert (reward / risk) >= 1.0, (
                f"R:R regression: realised reward/risk={reward/risk:.3f} "
                f"is below 1:1 "
                f"(side={side}, entry={entry}, sl={sl}, tp={tp}). "
                "ATR floor may be widening SL in this synthetic fixture."
            )

    def test_confidence_threads_through_to_journal_row(
        self, tmp_path, monkeypatch,
    ):
        """End-to-end pin: signal → _signal_to_order_package →
        _log_new_order_package → SELECT confidence from order_packages.
        Pre-fix this read 0.0 for every VWAP signal (BUG-043)."""
        monkeypatch.setenv(
            "TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"),
        )
        from src.runtime.pipeline import _signal_to_order_package
        from src.core.coordinator import _log_new_order_package
        from src.units.db.database import Database

        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        pkg = _signal_to_order_package(signal, settings={"SYMBOL": "BTCUSDT"})
        assert pkg.confidence > 0.0, (
            "OrderPackage must carry the strategy's confidence — "
            "regression in _signal_to_order_package's meta extraction"
        )

        order_package_id = _log_new_order_package(pkg)
        assert order_package_id and order_package_id.startswith("pkg-")

        db = Database(db_path=str(tmp_path / "trade_journal.db"))
        rows = db.get_order_packages_by_strategy(pkg.strategy)
        assert len(rows) == 1
        assert rows[0]["confidence"] == pytest.approx(pkg.confidence)
        assert rows[0]["confidence"] > 0.0, (
            "BUG-043 regression: order_packages.confidence must be "
            "non-zero for an actionable VWAP signal"
        )


# ---------------------------------------------------------------------------
# S-VWAP-POLICY-LIVE-WIRE — regime-aware policy gate
# ---------------------------------------------------------------------------

class TestPolicyGate:
    """build_vwap_signal must honour vwap_policy decisions:
      * allow=False  → side='none' regardless of deviation magnitude
      * threshold override → uses overridden sigma instead of ENTRY_STD_THRESHOLD
      * unknown / unrecognised regime → falls through to ENTRY_STD_THRESHOLD
    """

    def _skip_policy(self, regime: str) -> dict:
        return {
            "allow": False,
            "threshold": None,
            "rationale": "test skip",
            "_regime_info": {"regime": regime, "trend": regime.split("/")[0], "volatility": "low"},
            "regime": regime,
            "fallback": False,
        }

    def _override_policy(self, regime: str, threshold: float) -> dict:
        return {
            "allow": True,
            "threshold": threshold,
            "rationale": "test override",
            "_regime_info": {"regime": regime},
            "regime": regime,
            "fallback": False,
        }

    def test_policy_skip_suppresses_buy_signal(self):
        """allow=False must return side='none' even when deviation >> threshold."""
        df = _candles_below_vwap()  # normally triggers buy
        with mock.patch(
            "src.units.strategies.vwap.policy_for_candles",
            return_value=self._skip_policy("weak-up/low"),
        ):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "regime_policy_skip" in signal["meta"]["reason"]
        assert signal["meta"]["policy_regime"] == "weak-up/low"
        assert signal["meta"]["policy_allow"] is False

    def test_policy_skip_suppresses_sell_signal(self):
        """allow=False must return side='none' even on a sell-triggering deviation."""
        df = _candles_above_vwap()  # normally triggers sell
        with mock.patch(
            "src.units.strategies.vwap.policy_for_candles",
            return_value=self._skip_policy("sideways/low"),
        ):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "regime_policy_skip" in signal["meta"]["reason"]
        assert signal["meta"]["policy_regime"] == "sideways/low"

    def test_policy_skip_meta_includes_vwap_and_deviation(self):
        """Skip meta must include VWAP and deviation for audit/debugging."""
        df = _candles_below_vwap()
        with mock.patch(
            "src.units.strategies.vwap.policy_for_candles",
            return_value=self._skip_policy("weak-up/low"),
        ):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["meta"]["vwap"] > 0
        assert "deviation_std" in signal["meta"]
        assert signal["meta"]["std_dev"] > 0

    def test_policy_threshold_override_raises_entry_bar(self):
        """A 2.0σ override must suppress signals that would fire at 1.0σ.

        Candles [100, 100, X] produce deviation = -sqrt(2) ≈ -1.41σ
        (above 1.0σ but below 2.0σ), so the signal fires at the
        module default but must be suppressed when policy overrides to 2.0σ.
        """
        # 2 candles at 100, last at 80 → deviation ≈ -1.41σ
        df = _candles(100, 100, 80)

        # Verify fires at default 1.0σ with no mock (real policy would classify
        # as "strong-down/high" on 3 bars < 10 → unknown → DEFAULT allow=True,
        # threshold=None → effective_threshold=1.0)
        default_signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert default_signal["side"] == "buy", (
            "fixture must trigger buy at 1.0σ; check _candles deviation"
        )
        assert abs(default_signal["meta"]["deviation_std"]) < 2.0, (
            "fixture deviation must be between 1.0 and 2.0σ for this test to be valid"
        )

        # With 2.0σ override, the same signal must be suppressed
        with mock.patch(
            "src.units.strategies.vwap.policy_for_candles",
            return_value=self._override_policy("strong-up/low", 2.0),
        ):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none", (
            "2.0σ override must suppress a 1.41σ deviation"
        )
        assert signal["meta"].get("policy_threshold") == 2.0
        assert signal["meta"].get("policy_allow") is True

    def test_policy_threshold_override_allows_deep_signal(self):
        """When deviation >= override threshold the signal still fires."""
        df = _candles_below_vwap()  # deviation ≈ -2.24σ > 2.0σ
        with mock.patch(
            "src.units.strategies.vwap.policy_for_candles",
            return_value=self._override_policy("strong-up/low", 2.0),
        ):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        assert signal["meta"].get("policy_threshold") == 2.0

    def test_unknown_regime_falls_through_to_module_constant(self):
        """Regime not in policy table → DEFAULT_POLICY (allow=True,
        threshold=None) → effective_threshold = ENTRY_STD_THRESHOLD.
        Signal behaves identically to the no-policy baseline."""
        # Small fixtures (< 10 candles) classify as 'unknown' in classify_regime
        # → DEFAULT_POLICY (allow=True, threshold=None) automatically.
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        assert signal["meta"]["policy_allow"] is True
        assert signal["meta"]["policy_threshold"] is None

    def test_policy_meta_present_on_actionable_signal(self):
        """policy_regime, policy_allow, policy_threshold must appear in
        meta on every signal (not just skips) for telemetry."""
        for df, expected_side in (
            (_candles_below_vwap(), "buy"),
            (_candles_above_vwap(), "sell"),
        ):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
            assert signal["side"] == expected_side
            assert "policy_regime" in signal["meta"]
            assert "policy_allow" in signal["meta"]
            assert "policy_threshold" in signal["meta"]

    def test_policy_table_skips_weak_down_low(self):
        """weak-down/low must be on the SKIP list (2026-05-26).

        Historical evidence (issue #1536) was n=3 @ 1.5σ, mean +0.08 R,
        2/3 positive — flat, dropped from the override list. Previously
        fell through to DEFAULT_POLICY (1.0σ), which is even more
        noise-prone than the already-flat 1.5σ. The 2026-05-26
        health-review confirmed the live cost (15 same-direction
        reinforcement fires aggregating to target_qty=0 in 2h).
        """
        from src.units.strategies.vwap_policy import POLICY_TABLE, lookup_policy

        assert "weak-down/low" in POLICY_TABLE, (
            "weak-down/low must be an explicit policy entry, not a fall-through"
        )
        entry = POLICY_TABLE["weak-down/low"]
        assert entry["allow"] is False
        assert entry["threshold"] is None

        looked_up = lookup_policy("weak-down/low")
        assert looked_up["allow"] is False
        assert looked_up["fallback"] is False
        assert looked_up["regime"] == "weak-down/low"

    def test_policy_skip_suppresses_weak_down_low_buy_signal(self):
        """A weak-down/low classification must suppress the buy signal
        even when price is well past the 1.0σ default threshold —
        parallels test_policy_skip_suppresses_buy_signal but pins the
        2026-05-26 addition."""
        df = _candles_below_vwap()  # would normally trigger buy at 1.0σ
        with mock.patch(
            "src.units.strategies.vwap.policy_for_candles",
            return_value=self._skip_policy("weak-down/low"),
        ):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "regime_policy_skip" in signal["meta"]["reason"]
        assert signal["meta"]["policy_regime"] == "weak-down/low"
        assert signal["meta"]["policy_allow"] is False


# ---------------------------------------------------------------------------
# Phase 1 — UTC-day session-anchored VWAP slice
# (2026-05-07-vwap-accuracy training run, PR #481)
# ---------------------------------------------------------------------------

import datetime as _dt  # noqa: E402

from src.units.strategies.vwap import (  # noqa: E402
    SESSION_MIN_BARS,
    _session_anchor_slice,
)


def _ts_candles(timestamps, *, close_pattern=(99, 100, 101), volume=1000.0):
    """Build OHLCV candles with explicit UTC timestamps."""
    rows = []
    for i, ts in enumerate(timestamps):
        close = close_pattern[i % len(close_pattern)]
        rows.append({
            "timestamp": ts,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": volume,
        })
    return pd.DataFrame(rows)


class TestSessionAnchorSlice:
    """`_session_anchor_slice` is the entry point for Phase 1 of the
    VWAP-accuracy adoption — it must fall back gracefully on every
    edge case so callers never see a regression versus the rolling
    window when the timestamp data isn't trustworthy."""

    def test_no_timestamp_column_returns_full_df(self):
        df = pd.DataFrame([
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 1000},
        ])
        out = _session_anchor_slice(df)
        assert len(out) == len(df)
        assert out is df

    def test_integer_index_timestamps_collapse_to_epoch_day_zero(self):
        """Existing tests use ``timestamp: i`` for small int i. All such
        values resolve to 1970-01-01 — a single UTC day — so the slice
        must return the full df. Locks the no-regression contract for
        every pre-existing fixture in this file."""
        df = pd.DataFrame([
            {"timestamp": i, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
            for i in range(20)
        ])
        out = _session_anchor_slice(df)
        assert len(out) == len(df)

    def test_timestamps_within_one_utc_day_returns_full_df(self):
        bars = [
            _dt.datetime(2026, 5, 7, h, 0, 0, tzinfo=_dt.timezone.utc)
            for h in range(0, 22, 2)
        ]
        df = _ts_candles(bars)
        out = _session_anchor_slice(df)
        assert len(out) == len(df), "all bars are 2026-05-07 — slice should be the full df"

    def test_timestamps_spanning_midnight_slice_to_post_midnight(self):
        # 4 May-6 bars + (SESSION_MIN_BARS + 1) May-7 bars at 5m so the
        # post-midnight slice exceeds the threshold (raised to 50 in
        # the 2026-05-08 hotfix to keep σ stable) and the slice is
        # actually returned instead of the full-df fallback.
        post_midnight_bars = SESSION_MIN_BARS + 1
        bars = (
            [_dt.datetime(2026, 5, 6, 22 + h // 2, 30 * (h % 2), 0,
                          tzinfo=_dt.timezone.utc) for h in range(4)]
            + [_dt.datetime(2026, 5, 7, 0, 0, 0, tzinfo=_dt.timezone.utc)
               + _dt.timedelta(minutes=5 * i)
               for i in range(post_midnight_bars)]
        )
        df = _ts_candles(bars)
        out = _session_anchor_slice(df)
        assert len(out) == post_midnight_bars
        assert out["timestamp"].iloc[0].day == 7
        assert out["timestamp"].iloc[-1] == bars[-1]

    def test_too_few_post_midnight_bars_falls_back_to_full(self):
        """When fewer than SESSION_MIN_BARS bars sit past midnight we
        fall back to the full lookback so σ doesn't get computed from
        a 1-2 bar sample early in the session."""
        bars = (
            [_dt.datetime(2026, 5, 6, 22 + i // 2, 30 * (i % 2), 0,
                          tzinfo=_dt.timezone.utc) for i in range(4)]
            + [_dt.datetime(2026, 5, 7, 0, 0, 0, tzinfo=_dt.timezone.utc)]
            + [_dt.datetime(2026, 5, 7, 0, 5, 0, tzinfo=_dt.timezone.utc)]
        )
        df = _ts_candles(bars)
        # only 2 bars on 2026-05-07 < SESSION_MIN_BARS (5)
        out = _session_anchor_slice(df)
        assert len(out) == len(df), (
            f"expected full df fallback when post-midnight slice has "
            f"< {SESSION_MIN_BARS} bars"
        )

    def test_zero_volume_in_session_slice_falls_back_to_full(self):
        """A zero-volume session slice (e.g., all-volume-zero bars
        right after a maintenance window) must fall back, not crash
        compute_vwap downstream.

        The post-midnight slice must clear ``SESSION_MIN_BARS`` so the
        bar-count fallback is *not* what's exercising the test — we
        want the volume-fallback path specifically.
        """
        post_midnight = SESSION_MIN_BARS + 5
        zero_vol = _ts_candles(
            [_dt.datetime(2026, 5, 7, 0, 0, 0, tzinfo=_dt.timezone.utc)
             + _dt.timedelta(minutes=5 * i) for i in range(post_midnight)],
            volume=0.0,
        )
        prior = _ts_candles(
            [_dt.datetime(2026, 5, 6, 23, 0, 0, tzinfo=_dt.timezone.utc)
             + _dt.timedelta(minutes=5 * i) for i in range(5)],
            volume=1000.0,
        )
        df_full = pd.concat([prior, zero_vol], ignore_index=True)
        out = _session_anchor_slice(df_full)
        assert len(out) == len(df_full), "zero-volume session must fall back"

    def test_unparseable_timestamps_fall_back(self):
        df = pd.DataFrame([
            {"timestamp": "not-a-date", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
            for _ in range(10)
        ])
        out = _session_anchor_slice(df)
        assert len(out) == len(df), "unparseable timestamps must fall back"


class TestBuildVwapSignalAnchoring:
    """End-to-end integration of the session-anchored slice with the
    public ``build_vwap_signal`` API. Asserts the meta carries the
    new audit fields and that the anchor mode actually changes the
    computed VWAP / σ when the lookback spans midnight."""

    def test_meta_records_anchor_mode_and_window_size(self):
        df = _candles_below_vwap()  # integer timestamps → "rolling"
        sig = build_vwap_signal(df, symbol="BTCUSDT")
        assert sig["side"] == "buy"
        assert sig["meta"]["vwap_anchor"] == "rolling"
        assert sig["meta"]["vwap_window_bars"] == len(df)

    def test_anchor_session_when_lookback_spans_midnight(self):
        """When the lookback straddles UTC midnight, the anchored
        VWAP is computed only from post-midnight bars and the meta
        flags ``vwap_anchor='session'``."""
        # Prior-day bars (uniform high price) + post-midnight bars
        # exceeding ``SESSION_MIN_BARS`` (raised to 50 in the
        # 2026-05-08 hotfix) so the slice is taken rather than
        # falling back. Post-midnight bars trend lower so the
        # anchored VWAP is meaningfully below the rolling VWAP.
        post_midnight_count = SESSION_MIN_BARS + 5
        prior_bars = [
            _dt.datetime(2026, 5, 6, 18, 0, 0, tzinfo=_dt.timezone.utc)
            + _dt.timedelta(minutes=5 * i)
            for i in range(5)
        ]
        post_bars = [
            _dt.datetime(2026, 5, 7, 0, 0, 0, tzinfo=_dt.timezone.utc)
            + _dt.timedelta(minutes=5 * i)
            for i in range(post_midnight_count)
        ]
        bars = prior_bars + post_bars
        rows = []
        for i, ts in enumerate(bars):
            # Prior-day bars uniformly high; post-midnight bars trend
            # downward so the slice's typical-price mean diverges
            # from the full-df mean.
            close = 110.0 if i < 5 else 100.0 - 0.2 * (i - 5)
            rows.append({
                "timestamp": ts,
                "open": close - 1, "high": close + 2, "low": close - 2,
                "close": close, "volume": 1000,
            })
        df = pd.DataFrame(rows)
        sig = build_vwap_signal(df, symbol="BTCUSDT")
        assert sig["meta"]["vwap_anchor"] == "session"
        assert sig["meta"]["vwap_window_bars"] == post_midnight_count
        # Sanity: anchored VWAP ≠ rolling VWAP because pre-midnight bars are excluded.
        from src.units.strategies.vwap import compute_vwap
        rolling_vwap = compute_vwap(df)
        anchored_vwap = sig["meta"]["vwap"]
        assert abs(rolling_vwap - anchored_vwap) > 0.5, (
            f"anchored VWAP ({anchored_vwap}) should differ from rolling "
            f"({rolling_vwap}) when the slice excludes prior-day high bars"
        )


# ---------------------------------------------------------------------------
# Integration: STRATEGY=vwap routes to VWAP logic via run_pipeline
# ---------------------------------------------------------------------------

class TestVwapPipelineRouting:
    def _vwap_no_signal_builder(self, settings):
        return {
            "symbol": settings.get("SYMBOL", "BTCUSDT"),
            "side": "none",
            "meta": {"strategy_name": "vwap"},
        }

    def _vwap_buy_signal_builder(self, settings):
        # S-026 G1: signals carry no qty — pipeline injects a placeholder
        # for safe_place_order until G2 moves sizing into the
        # per-account RiskManager.
        return {
            "symbol": settings.get("SYMBOL", "BTCUSDT"),
            "side": "buy",
            "meta": {"strategy_name": "vwap", "vwap": 100.0, "current_price": 90.0},
        }

    def test_vwap_strategy_routes_correctly(self, monkeypatch):
        """STRATEGY=vwap should invoke the vwap signal builder."""
        called_with = {}

        def fake_vwap_builder(settings):
            called_with["settings"] = settings
            return {"symbol": "BTCUSDT", "side": "none"}

        monkeypatch.setattr("src.runtime.pipeline.vwap_signal_builder", fake_vwap_builder)
        monkeypatch.setenv("STRATEGY", "vwap")

        settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "10"}
        run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
        )

        assert called_with, "vwap_signal_builder was not called"

    def test_legacy_path_never_calls_exchange(self):
        """E1-F1 (full-system audit 2026-07-09): the legacy single-client
        branch (an actionable signal without top-level sl/tp) used to place
        a naked placeholder-qty order on the injected exchange client — a
        live-money bypass of the one sanctioned order path
        (Coordinator.multi_account_execute + per-account RiskManager). That
        placement was removed: the branch now refuses, so exchange_client is
        NEVER called."""
        exchange = DummyExchangeClient()
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        run_pipeline(
            settings,
            exchange_client=exchange,
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_buy_signal_builder,
        )
        assert len(exchange.calls) == 0, (
            "E1-F1: the legacy path must NEVER place an order — it refuses "
            "(no sanctioned per-account sizing path)"
        )

    def test_legacy_path_returns_refused_status(self):
        """E1-F1: an SL/TP-less actionable signal is refused, not placed —
        ``status:refused`` reason ``actionable_signal_missing_sltp``."""
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        result = run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_buy_signal_builder,
        )
        assert result["order_result"]["status"] == "refused"
        assert result["order_result"]["reason"] == "actionable_signal_missing_sltp"

    def test_vwap_no_signal_returns_skipped(self):
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        result = run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_no_signal_builder,
        )
        assert result["order_result"]["status"] == "skipped"
        assert result["order_result"]["reason"] == "no_signal"


# ---------------------------------------------------------------------------
# S-026 G1: signals without top-level qty are still routed to multi-account
# dispatch (sizing happens per-account; the strategy emits the trade idea).
# ---------------------------------------------------------------------------


class TestQtylessSignalRoutesToMultiAccountDispatch:
    """Strategy signal that satisfies _signal_carries_full_sltp and has no
    qty must still be routed through the multi-account dispatch fast-path.
    Quantity is the per-account RiskManager's job (G2)."""

    def test_qtyless_packageable_signal_dispatches_per_account(self, monkeypatch):
        from src.runtime import pipeline as pl

        # Strategy emits the trade idea — symbol/side/entry/sl/tp + meta —
        # explicitly NO qty.
        signal = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "entry_price": 50_000.0,
            "stop_loss": 49_500.0,
            "take_profit": 51_000.0,
            "meta": {"strategy_name": "vwap"},
        }
        assert "qty" not in signal

        # Capture the OrderPackage that reaches multi_account_execute and
        # short-circuit the actual fan-out so no exchange/file I/O runs.
        captured = {}

        class _StubCoord:
            def multi_account_execute(self, pkg, dry_run=False):
                captured["pkg"] = pkg
                return [{"name": "fake", "trade_id": "dry-1", "error": None}]

        monkeypatch.setattr("src.core.coordinator.Coordinator", lambda: _StubCoord())
        # Stub strategy-monocle gates added after this test was authored — they
        # would otherwise intercept dispatch before it reaches multi_account_execute.
        monkeypatch.setattr(
            "src.runtime.pipeline._has_open_package_for_strategy", lambda *a, **k: None
        )
        monkeypatch.setattr(
            "src.runtime.pipeline._recent_refusal_for_strategy", lambda *a, **k: None
        )
        monkeypatch.setattr(
            "src.runtime.pipeline._same_bar_entry_for_strategy", lambda *a, **k: None
        )

        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "false",
            "ALLOW_LIVE_TRADING": "true",
            "MULTI_ACCOUNT_DISPATCH": "true",
            "MAX_QTY": "1",
        }
        result = pl.run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
            signal_builder=lambda _s: signal,
        )

        assert result["order_result"]["status"] == "multi_account_dispatched", (
            f"S-026 G1: qty-less actionable signal must reach the "
            f"multi-account dispatch fast-path; got "
            f"{result['order_result']!r}"
        )
        assert "pkg" in captured, "multi_account_execute was never called"
        # OrderPackage carries the trade idea — no qty field.
        pkg = captured["pkg"]
        assert pkg.symbol == "BTCUSDT"
        assert pkg.direction == "long"
        assert pkg.entry == 50_000.0
        assert not hasattr(pkg, "qty"), (
            "OrderPackage must not carry qty (sizing is per-account)"
        )


# ---------------------------------------------------------------------------
# Safety: live mode without explicit gate fails closed
# ---------------------------------------------------------------------------

class TestLiveSafetyGate:
    def test_live_without_allow_live_trading_submits_by_default(self):
        """BUG-031: DRY_RUN=false with ALLOW_LIVE_TRADING absent submits.
        Live is the default — the safety rails are the risk manager and
        /halt, not an extra opt-in env var."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "false", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "submitted"
        assert len(client.calls) == 1

    def test_live_with_explicit_gate_is_submitted(self):
        """DRY_RUN=false + ALLOW_LIVE_TRADING=true → order reaches exchange."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "submitted"
        assert len(client.calls) == 1

    def test_dry_run_flag_does_not_gate_safe_place_order(self):
        """2026-05-03 operator directive: DRY_RUN is not a process-level
        gate in safe_place_order. The per-account RiskManager
        (mode: live|dry_run in accounts.yaml) is the only dry/live
        toggle. safe_place_order is a payload-validation + halt-flag +
        risk-cap rail, NOT a mode gate. DRY_RUN in settings has no
        effect here — the order reaches the exchange regardless."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "true", "ALLOW_LIVE_TRADING": "true", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "submitted"
        assert len(client.calls) == 1

    def test_mode_and_dry_run_flags_are_ignored_by_validate_startup(self, monkeypatch):
        """2026-05-03 operator directive: MODE, DRY_RUN, and
        ALLOW_LIVE_TRADING checks were removed from validate_startup.
        The per-account RiskManager is the only dry/live toggle.
        validate_startup must NOT raise for any combination of these
        env vars when all required fields are valid."""
        _required = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
        }
        for combos in [
            {"MODE": "LIVE", "DRY_RUN": "true", "ALLOW_LIVE_TRADING": "false"},
            {"MODE": "PAPER", "DRY_RUN": "true"},
            {"MODE": "paper", "DRY_RUN": "true"},
            {"MODE": "live", "DRY_RUN": "true", "ALLOW_LIVE_TRADING": "false"},
        ]:
            for k, v in {**_required, **combos}.items():
                monkeypatch.setenv(k, v)
            validate_startup()  # must not raise


# ---------------------------------------------------------------------------
# Edge cases: missing / malformed candle data
# ---------------------------------------------------------------------------

class TestVwapEdgeCases:
    def test_single_candle_insufficient(self):
        df = _candles(100)
        with pytest.raises(ValueError, match="at least"):
            build_vwap_signal(df, symbol="BTCUSDT")

    def test_exactly_min_candles_is_accepted(self):
        df = _candles(*([100] * MIN_CANDLES))
        # All-same prices → std_dev = 0 → deviation = 0 → no signal, but no error
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"

    def test_vwap_meta_never_contains_api_key(self):
        """Ensure VWAP signal meta cannot leak credentials."""
        df = _candles(100, 102, 101)
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        meta_str = str(signal)
        for suspicious in ("api_key", "api_secret", "token", "password", "secret"):
            assert suspicious not in meta_str.lower(), (
                f"Signal output contains suspicious key: {suspicious}"
            )


# ---------------------------------------------------------------------------
# Invalid candle data — must return no-trade, never raise
# ---------------------------------------------------------------------------

class TestVwapInvalidDataNoTrade:
    """Bad market data must yield a no-trade signal; the tick must not crash."""

    def test_zero_volume_returns_no_trade(self):
        df = _candles(100, 102, 101, volume=0)
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "qty" not in signal
        assert signal["meta"]["strategy_name"] == "vwap"

    def test_zero_volume_reason_text(self):
        df = _candles(100, 102, 101, volume=0)
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        reason = signal["meta"]["reason"]
        assert "zero" in reason.lower() or "negative" in reason.lower()

    def test_zero_volume_does_not_raise(self):
        df = _candles(100, 102, 101, volume=0)
        build_vwap_signal(df, symbol="BTCUSDT")  # must not raise

    def test_missing_volume_column_returns_no_trade(self):
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100},
            {"timestamp": 1, "open": 100, "high": 103, "low": 99, "close": 101},
        ])
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "qty" not in signal

    def test_missing_volume_column_does_not_raise(self):
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100},
            {"timestamp": 1, "open": 100, "high": 103, "low": 99, "close": 101},
        ])
        build_vwap_signal(df, symbol="BTCUSDT")  # must not raise

    def test_empty_dataframe_returns_no_trade(self):
        df = pd.DataFrame()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "qty" not in signal

    def test_empty_dataframe_does_not_raise(self):
        df = pd.DataFrame()
        build_vwap_signal(df, symbol="BTCUSDT")  # must not raise

    def test_normal_candles_still_produce_signal(self):
        """Valid candle data must continue to generate actionable signals."""
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        assert "qty" not in signal
        assert signal["meta"]["strategy_name"] == "vwap"
        assert signal["meta"]["current_price"] < signal["meta"]["vwap"]

    def test_pipeline_zero_volume_skips_order_placement(self):
        """Zero-volume candles routed through pipeline must not reach order placement."""
        exchange = DummyExchangeClient()

        def zero_volume_builder(settings):
            df = _candles(100, 102, 101, volume=0)
            return build_vwap_signal(df, symbol=settings.get("SYMBOL", "BTCUSDT"))

        settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "1"}
        result = run_pipeline(
            settings,
            exchange_client=exchange,
            telegram_client=DummyTelegramClient(),
            signal_builder=zero_volume_builder,
        )
        assert result["order_result"]["status"] == "skipped"
        assert exchange.calls == []


# ---------------------------------------------------------------------------
# Recent-context filter (operator directive 2026-05-13)
# 24h max lookback, recency-weighted (EWM), informational only.
# ---------------------------------------------------------------------------

from src.units.strategies.vwap import (  # noqa: E402
    RECENT_CONTEXT_NEUTRAL_BAND_PCT_DEFAULT,
    _compute_recent_context,
)


def _ctx_candles(close_prices, volume=1000.0):
    """Build an OHLCV DataFrame for recent-context tests."""
    rows = []
    for i, close in enumerate(close_prices):
        rows.append({
            "timestamp": i,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": volume,
        })
    return pd.DataFrame(rows)


class TestComputeRecentContext:
    """Unit tests for _compute_recent_context — 24h recency-weighted trend helper."""

    def test_up_when_prices_rise_above_band(self):
        """Steady price rise → EWM-weighted current above window open → trend=up."""
        df = _ctx_candles([100.0, 101.0, 102.0, 103.5])  # +3.5%
        result = _compute_recent_context(df, neutral_band_pct=0.003)
        assert result["trend"] == "up"
        assert result["pct"] > 0.003

    def test_down_when_prices_fall_below_band(self):
        """Steady price fall → EWM-weighted current below window open → trend=down."""
        df = _ctx_candles([103.5, 102.0, 101.0, 100.0])  # -3.4%
        result = _compute_recent_context(df, neutral_band_pct=0.003)
        assert result["trend"] == "down"
        assert result["pct"] < -0.003

    def test_flat_when_prices_stable(self):
        """Flat prices → EWM ≈ window open → trend=flat."""
        df = _ctx_candles([100.0, 100.0, 100.0, 100.0])
        result = _compute_recent_context(df, neutral_band_pct=0.003)
        assert result["trend"] == "flat"
        assert abs(result["pct"]) < 0.003

    def test_unknown_when_dataframe_is_none(self):
        result = _compute_recent_context(None)
        assert result["trend"] == "unknown"
        assert result["pct"] == 0.0

    def test_unknown_when_fewer_than_two_rows(self):
        df = _ctx_candles([100.0])
        result = _compute_recent_context(df)
        assert result["trend"] == "unknown"

    def test_unknown_when_close_column_missing(self):
        df = pd.DataFrame([{"timestamp": 0, "volume": 1000}, {"timestamp": 1, "volume": 1000}])
        result = _compute_recent_context(df)
        assert result["trend"] == "unknown"

    def test_recent_bars_dominate_via_ewm(self):
        """EWM weighting: a sharp recent move outweighs a flat earlier period.
        Window starts flat then spikes up sharply at the end — trend should be up."""
        flat = [100.0] * 20
        spike = [130.0]  # big move in the last bar
        df = _ctx_candles(flat + spike)
        result = _compute_recent_context(df, neutral_band_pct=0.003)
        assert result["trend"] == "up", (
            "EWM must weight the recent spike heavily enough to push "
            "the context above the neutral band even with 20 flat bars before it."
        )

    def test_uses_module_default_neutral_band(self):
        """Default neutral_band_pct equals RECENT_CONTEXT_NEUTRAL_BAND_PCT_DEFAULT."""
        df = _ctx_candles([100.0, 100.0])  # flat
        result = _compute_recent_context(df)
        assert result["trend"] == "flat"
        assert RECENT_CONTEXT_NEUTRAL_BAND_PCT_DEFAULT == pytest.approx(0.003, rel=1e-6)


class TestRecentContextInSignalMeta:
    """Verify build_vwap_signal surfaces recent_context in meta without blocking signals."""

    def test_meta_always_contains_recent_context_key(self):
        """recent_context and recent_context_pct are present in meta on every tick."""
        df = _candles_below_vwap()
        sig = build_vwap_signal(df, symbol="BTCUSDT")
        assert "recent_context" in sig["meta"]
        assert "recent_context_pct" in sig["meta"]

    def test_recent_context_unknown_when_no_context_candles_passed(self):
        """Without recent_context_candles_df the context defaults to 'unknown'."""
        df = _candles_below_vwap()
        sig = build_vwap_signal(df, symbol="BTCUSDT")
        assert sig["meta"]["recent_context"] == "unknown"
        assert sig["meta"]["recent_context_pct"] == 0.0

    def test_up_context_surfaced_in_meta(self):
        ctx_df = _ctx_candles([100.0, 102.0, 104.0, 106.0])  # rising → up
        df = _candles_below_vwap()
        sig = build_vwap_signal(df, symbol="BTCUSDT", recent_context_candles_df=ctx_df)
        assert sig["meta"]["recent_context"] == "up"
        assert sig["meta"]["recent_context_pct"] > 0.003

    def test_down_context_surfaced_in_meta(self):
        ctx_df = _ctx_candles([106.0, 104.0, 102.0, 100.0])  # falling → down
        df = _candles_below_vwap()
        sig = build_vwap_signal(df, symbol="BTCUSDT", recent_context_candles_df=ctx_df)
        assert sig["meta"]["recent_context"] == "down"
        assert sig["meta"]["recent_context_pct"] < -0.003

    def test_sell_signal_fires_in_up_context(self):
        """A sell signal must fire even when recent context is 'up'.
        The context is informational — it never blocks signals."""
        ctx_df = _ctx_candles([100.0, 102.0, 104.0, 106.0])
        df = _candles_above_vwap()
        sig = build_vwap_signal(df, symbol="BTCUSDT", recent_context_candles_df=ctx_df)
        assert sig["side"] == "sell", (
            "Sell signal must not be blocked by uptrending recent context. "
            "Mean-reversion shorts are valid in uptrending markets."
        )
        assert sig["meta"]["recent_context"] == "up"

    def test_buy_signal_fires_in_down_context(self):
        """A buy signal must fire even when recent context is 'down'.
        The context is informational — it never blocks signals."""
        ctx_df = _ctx_candles([106.0, 104.0, 102.0, 100.0])
        df = _candles_below_vwap()
        sig = build_vwap_signal(df, symbol="BTCUSDT", recent_context_candles_df=ctx_df)
        assert sig["side"] == "buy", (
            "Buy signal must not be blocked by downtrending recent context. "
            "Mean-reversion longs are valid in downtrending markets."
        )
        assert sig["meta"]["recent_context"] == "down"

    def test_no_signal_also_carries_recent_context(self):
        """recent_context is present in meta even for no-signal (side=none) ticks."""
        ctx_df = _ctx_candles([100.0, 101.0])
        df = _candles_near_vwap()
        sig = build_vwap_signal(df, symbol="BTCUSDT", recent_context_candles_df=ctx_df)
        assert sig["side"] == "none"
        assert "recent_context" in sig["meta"]

    def test_custom_neutral_band_respected(self):
        """neutral_band_pct parameter flows through to _compute_recent_context."""
        ctx_df_large = _ctx_candles([100.0, 104.0])  # +4% — up under any band
        result_large = _compute_recent_context(ctx_df_large, neutral_band_pct=0.003)
        assert result_large["trend"] == "up"

        ctx_df_tiny = _ctx_candles([100.0, 100.001])  # +0.001% — flat under 0.3% band
        result_tiny = _compute_recent_context(ctx_df_tiny, neutral_band_pct=0.003)
        assert result_tiny["trend"] == "flat"
