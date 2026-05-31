"""Phase-5 SIM harness tests — the OPTIONAL $ account-realism layer.

Locks the account model folded in from ``scripts/backtest_system.py``: position
sizing (``_risk_qty``), the UTC daily-loss halt, capital utilization, the $
summary block, and the flip-policy reconcile. Crucially also guards that the R
metrics are UNCHANGED when no ``AccountConfig`` is supplied — the consolidation
is additive and back-compatible (Phase 1-4's 49 tests stay green).

Mirrors ``test_sim_phase1.py``: the engine is driven through stub strategies
injected into STRATEGY_UNITS (no pandas-heavy real strategies, no live candles),
and the REAL ``aggregate_intents`` still arbitrates conflicts.
"""
from __future__ import annotations

import sys
import types

import pytest

from sim.account import AccountConfig, SimAccount, _utc_day


# --------------------------------------------------------------------------
# Position sizing matches backtest_system._risk_qty
# --------------------------------------------------------------------------
class TestRiskSizing:
    def test_risk_qty_matches_formula(self):
        # _risk_qty(bal, rpct, entry, sl) = (bal * rpct/100) / |entry-sl|
        acct = SimAccount(AccountConfig(initial_balance=10_000.0, risk_pct=1.0))
        # |100 - 95| = 5 ; risk_cash = 10000 * 0.01 = 100 ; qty = 100 / 5 = 20
        assert acct.risk_qty(100.0, 95.0) == pytest.approx(20.0)
        # the $ at risk (1R) is balance * risk_pct/100
        assert acct.size(100.0, 95.0) == pytest.approx(100.0)

    def test_degenerate_stop_skips(self):
        acct = SimAccount(AccountConfig(initial_balance=10_000.0, risk_pct=1.0))
        assert acct.risk_qty(100.0, 100.0) == 0.0   # zero stop distance
        assert acct.size(100.0, 100.0) == 0.0       # → skip the open

    def test_pnl_is_r_times_risk_cash(self):
        # A +2R winner with 1% risk on $10k → +$200 (2.0 * 0.01 * 10000).
        acct = SimAccount(AccountConfig(initial_balance=10_000.0, risk_pct=1.0))
        risk_cash = acct.size(100.0, 95.0)
        acct.on_close("s1", risk_cash, 2.0, "2026-01-01T00:00:00Z")
        s = acct.summary()
        assert s["final_balance"] == pytest.approx(10_200.0)
        assert s["net_usd"] == pytest.approx(200.0)
        assert s["return_pct"] == pytest.approx(2.0)
        assert s["per_strategy_usd"]["s1"] == {"pnl_usd": 200.0, "trades": 1}


# --------------------------------------------------------------------------
# Daily-loss halt
# --------------------------------------------------------------------------
class TestDailyLossHalt:
    def test_halt_blocks_after_breach(self):
        # risk 5% → a 1R loss is -$500 = -5%, below the 4% daily cap → halt.
        acct = SimAccount(AccountConfig(initial_balance=10_000.0, risk_pct=5.0,
                                        daily_loss_pct=4.0))
        day = "2026-02-02T00:00:00Z"
        acct.note_day(day)
        assert acct.can_open(day) is True           # nothing lost yet
        acct.on_close("s1", acct.size(100, 95), -1.0, day)  # -$500
        assert acct.can_open(day) is False          # cap breached → halt
        assert acct.summary()["halted_days"] == ["2026-02-02"]

    def test_new_day_resets_halt(self):
        acct = SimAccount(AccountConfig(initial_balance=10_000.0, risk_pct=5.0,
                                        daily_loss_pct=4.0))
        d1, d2 = "2026-02-02T00:00:00Z", "2026-02-03T00:00:00Z"
        acct.note_day(d1)
        acct.on_close("s1", acct.size(100, 95), -1.0, d1)
        assert acct.can_open(d1) is False
        acct.note_day(d2)                            # fresh day, fresh budget
        assert acct.can_open(d2) is True

    def test_zero_cap_disables_halt(self):
        acct = SimAccount(AccountConfig(initial_balance=10_000.0, risk_pct=50.0,
                                        daily_loss_pct=0.0))
        day = "2026-02-02T00:00:00Z"
        acct.note_day(day)
        acct.on_close("s1", acct.size(100, 95), -1.0, day)  # huge loss
        assert acct.can_open(day) is True           # halt disabled


# --------------------------------------------------------------------------
# Capital utilization + drawdown
# --------------------------------------------------------------------------
class TestSummaryMetrics:
    def test_capital_utilization(self):
        acct = SimAccount(AccountConfig(initial_balance=10_000.0, risk_pct=1.0))
        acct.mark_utilization(total_bars=4, bars_with_open=1)
        assert acct.summary()["capital_utilization_pct"] == pytest.approx(25.0)

    def test_drawdown_and_return_over_dd(self):
        acct = SimAccount(AccountConfig(initial_balance=10_000.0, risk_pct=10.0))
        # +1R then -1R: bal 10000 -> 11000 (peak) -> 10000. DD = 1000 = ~9.09%.
        acct.on_close("s1", 1000.0, 1.0, "2026-01-01T00:00:00Z")
        acct.on_close("s1", 1100.0, -1.0, "2026-01-02T00:00:00Z")
        s = acct.summary()
        assert s["max_drawdown_usd"] == pytest.approx(1100.0)
        assert s["final_balance"] == pytest.approx(9900.0)
        assert s["net_usd"] == pytest.approx(-100.0)
        # return_over_dd = net / maxDD = -100 / 1100
        assert s["return_over_dd"] == pytest.approx(round(-100.0 / 1100.0, 2))

    def test_utc_day_helper(self):
        assert _utc_day("2026-01-01T12:34:56Z") == "2026-01-01"
        assert _utc_day("2026-01-01 12:34:56") == "2026-01-01"
        assert _utc_day(0) == "1970-01-01"
        assert _utc_day(None) == "?"


# --------------------------------------------------------------------------
# Engine integration — account layer is opt-in + back-compatible
# --------------------------------------------------------------------------
def _make_stub_strategy_module(signal_for_bar):
    mod = types.ModuleType("sim_stub_strategy")

    def order_package(cfg, candles_df=None):
        n = 0 if candles_df is None else len(candles_df)
        return signal_for_bar(n)

    mod.order_package = order_package
    return mod


@pytest.fixture
def patch_strategy_units(monkeypatch):
    import sim.engine as engine

    registered = {}

    def register(name, module):
        modname = f"sim_stub_{name}"
        sys.modules[modname] = module
        registered[name] = modname
        new_map = dict(engine.STRATEGY_UNITS)
        new_map[name] = modname
        monkeypatch.setattr(engine, "STRATEGY_UNITS", new_map)

    yield register
    for modname in registered.values():
        sys.modules.pop(modname, None)


def _candles(n, base=100.0):
    return [{"ts": f"2021-01-01T00:{i:02d}:00Z", "open": base, "high": base + 5,
             "low": base - 5, "close": base, "volume": 1.0} for i in range(n)]


class TestEngineAccountLayer:
    def test_account_block_only_when_configured(self, patch_strategy_units):
        from sim.engine import run_replay

        def ts_win(n):
            # entry 100, sl 90 (risk 10), tp 105 (0.5R) — resolves on a +5 bar.
            return {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                    "sl": 90, "tp": 105, "confidence": 0.9, "meta": {}}

        patch_strategy_units("turtle_soup", _make_stub_strategy_module(ts_win))

        without = run_replay(candles=_candles(40), strategies=["turtle_soup"],
                             warmup_bars=5).summary()
        assert "account" not in without

        with_acct = run_replay(
            candles=_candles(40), strategies=["turtle_soup"], warmup_bars=5,
            account=AccountConfig(initial_balance=10_000.0, risk_pct=1.0),
        ).summary()
        assert "account" in with_acct
        # at least one trade filled → balance moved off the initial 10k
        assert with_acct["account"]["initial_balance"] == 10_000.0
        assert "final_balance" in with_acct["account"]
        assert "capital_utilization_pct" in with_acct["account"]
        assert "per_strategy_usd" in with_acct["account"]

    def test_r_metrics_identical_without_account(self, patch_strategy_units):
        """The R block is byte-identical with vs without the account layer."""
        from sim.engine import run_replay

        def ts_win(n):
            return {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                    "sl": 90, "tp": 105, "confidence": 0.9, "meta": {}}

        patch_strategy_units("turtle_soup", _make_stub_strategy_module(ts_win))

        r_only = run_replay(candles=_candles(40), strategies=["turtle_soup"],
                            warmup_bars=5).summary()
        with_acct = run_replay(
            candles=_candles(40), strategies=["turtle_soup"], warmup_bars=5,
            account=AccountConfig(initial_balance=10_000.0, risk_pct=1.0),
        ).summary()

        for k in ("portfolio", "per_strategy", "funnel", "equity_curve_r"):
            assert r_only[k] == with_acct[k], f"{k} drifted under the account layer"
        # the account run ADDS exactly the account key on top of the R block
        assert set(with_acct) - set(r_only) == {"account"}

    def test_account_pnl_tracks_r(self, patch_strategy_units):
        """$ net == sum over closed trades of (R * risk_cash committed at entry).

        This is the account's defining invariant: each trade's $ PnL is its
        realized R times the 1R risk-cash the account sized it at (which is
        ``balance * risk_pct/100`` at THAT trade's entry — the balance compounds
        across trades, so we recompute it per trade rather than assuming one
        fixed sizing).
        """
        from sim.engine import run_replay

        def ts_win(n):
            # 0.5R winner (tp 105, risk 10) — resolves on the +5 high each bar.
            return {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                    "sl": 90, "tp": 105, "confidence": 0.9, "meta": {}}

        patch_strategy_units("turtle_soup", _make_stub_strategy_module(ts_win))
        led = run_replay(
            candles=_candles(40), strategies=["turtle_soup"], warmup_bars=5,
            account=AccountConfig(initial_balance=10_000.0, risk_pct=1.0),
        )
        s = led.summary()
        closed = [t for t in led.trades if t.exit_ts is not None]
        assert closed, "expected at least one closed trade"
        # Reconstruct $ net from each trade's recorded R * its committed risk_cash.
        expected_net = sum(t.r_multiple * t.meta["risk_cash"] for t in closed)
        assert s["account"]["net_usd"] == pytest.approx(round(expected_net, 2), abs=0.01)
        assert s["account"]["final_balance"] == pytest.approx(
            10_000.0 + expected_net, abs=0.01)


class TestFlipPolicy:
    """reverse vs hold produce different trade counts on a constructed conflict.

    A single strategy (turtle_soup) opens LONG on the FIRST decision bar with an
    unreachable TP so the position stays open; on every LATER bar it emits a
    conflicting SHORT. Using one strategy means it always wins the aggregator, so
    the desired side deterministically flips long→short against the held long —
    exactly the opposite-side reconcile ``backtest_system`` handles. Under "hold"
    the long is kept (the short is ignored); under "reverse"/"flat" the long is
    force-closed on the first conflict bar (reverse then reopens the short).
    """

    def _setup(self, patch_strategy_units, warmup=5):
        # The first decision bar is at index == warmup, so n bars of history at
        # that point is warmup+1. Open long while history <= warmup+1, then short.
        threshold = warmup + 1

        def ts_flip(n):
            if n <= threshold:
                return {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                        "sl": 90, "tp": 100000, "confidence": 0.9, "meta": {}}
            return {"symbol": "BTCUSDT", "direction": "short", "entry": 100,
                    "sl": 110, "tp": -100000, "confidence": 0.9, "meta": {}}

        patch_strategy_units("turtle_soup", _make_stub_strategy_module(ts_flip))

    def test_hold_keeps_one_open(self, patch_strategy_units):
        from sim.engine import run_replay
        self._setup(patch_strategy_units)
        led = run_replay(candles=_candles(40), strategies=["turtle_soup"],
                         warmup_bars=5, flip_policy="hold")
        # hold: the original long fills once and is never displaced by the later
        # opposing shorts → exactly one fill, nothing closed (TP unreachable).
        assert led.funnel()["turtle_soup"]["filled"] == 1
        assert led.summary()["portfolio"]["closed_trades"] == 0

    def test_reverse_closes_and_reopens(self, patch_strategy_units):
        from sim.engine import run_replay
        self._setup(patch_strategy_units)
        led = run_replay(candles=_candles(40), strategies=["turtle_soup"],
                         warmup_bars=5, flip_policy="reverse")
        # reverse: the long is force-closed on the first conflicting short, then
        # the short is opened → more than one fill + a "flip"-reason close.
        assert led.funnel()["turtle_soup"]["filled"] >= 2
        closed = [t for t in led.trades if t.exit_ts is not None]
        assert any(t.exit_reason == "flip" for t in closed)

    def test_reverse_vs_hold_differ(self, patch_strategy_units):
        from sim.engine import run_replay
        self._setup(patch_strategy_units)
        hold = run_replay(candles=_candles(40), strategies=["turtle_soup"],
                          warmup_bars=5, flip_policy="hold")
        rev = run_replay(candles=_candles(40), strategies=["turtle_soup"],
                         warmup_bars=5, flip_policy="reverse")
        # the whole point of the consolidation: the policy changes the trade set.
        assert (rev.funnel()["turtle_soup"]["filled"]
                != hold.funnel()["turtle_soup"]["filled"])

    def test_flat_closes_no_reopen(self, patch_strategy_units):
        from sim.engine import run_replay
        self._setup(patch_strategy_units)
        led = run_replay(candles=_candles(40), strategies=["turtle_soup"],
                         warmup_bars=5, flip_policy="flat")
        # flat closes the long on the conflict but does NOT open the short. The
        # next bar's short then opens (flat only suppresses the reopen on the
        # SAME conflict bar), so we assert at least one flip-close occurred.
        closed = [t for t in led.trades if t.exit_reason == "flip"]
        assert len(closed) >= 1


# --------------------------------------------------------------------------
# CLI helpers: account args enable the layer; flip-policy reads the live default
# --------------------------------------------------------------------------
class TestCliHelpers:
    class _Args:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def test_account_from_args_toggle(self):
        from sim.__main__ import _account_from_args

        none_args = self._Args(initial_balance=None, risk_pct=None,
                               daily_loss_pct=None)
        assert _account_from_args(none_args) is None

        cfg = _account_from_args(self._Args(initial_balance=5_000.0,
                                            risk_pct=None, daily_loss_pct=None))
        assert isinstance(cfg, AccountConfig)
        assert cfg.initial_balance == 5_000.0
        # unspecified knobs fall back to AccountConfig defaults
        assert cfg.risk_pct == AccountConfig().risk_pct

    def test_resolve_flip_policy_reads_live_default(self):
        from sim.__main__ import _resolve_flip_policy
        from src.runtime.intents import resolve_flip_policy

        # explicit CLI value wins
        assert _resolve_flip_policy("reverse") == "reverse"
        # no CLI value → the LIVE source of truth (not a hardcoded literal)
        assert _resolve_flip_policy(None) == resolve_flip_policy()
