"""Tests for the M7 Strategy Review Gate.

Covers:

1. ``decide`` — the threshold decision matrix at every boundary row
   defined in ``docs/strategy-review-gate.md`` § Threshold table.
2. Override behaviour: execution-mode mismatch, degenerate confidence,
   already-at-shadow escalation, promote-requires-soak.
3. ``compute_regime_cells`` — the trend×vol slicer end-to-end against
   a populated real-schema DB.
4. The ``regime_policy_cell_for`` helper.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.ml.strategy_review_packet import (
    ExecutionDiagnostics,
    Headline,
    RegimeCell,
    build_packet,
    compute_headline,
    compute_regime_cells,
    decide,
    regime_policy_cell_for,
)
from tests.fixtures.real_schema_db import make_canonical_db


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _headline(
    *,
    n_decisions: int = 0,
    n_filled: int = 0,
    n_closed: int = 0,
    n_wins: int = 0,
    win_rate: float | None = None,
    pnl_total: float = 0.0,
    expectancy: float | None = None,
    max_drawdown: float = 0.0,
    fill_rate: float | None = None,
) -> Headline:
    return Headline(
        n_decisions=n_decisions,
        n_filled=n_filled,
        n_closed=n_closed,
        n_wins=n_wins,
        win_rate=win_rate,
        pnl_total=pnl_total,
        expectancy=expectancy,
        max_drawdown=max_drawdown,
        fill_rate=fill_rate,
    )


def _diag(
    *,
    conf_min: float | None = 0.4,
    conf_max: float | None = 0.8,
    conf_std: float | None = 0.1,
    conf_p50: float | None = 0.6,
    n: int = 5,
) -> ExecutionDiagnostics:
    return ExecutionDiagnostics(
        entry_slippage_bps=None,
        fill_rate=None,
        dispatch_latency_seconds=None,
        confidence_distribution={
            "min": conf_min,
            "max": conf_max,
            "p50": conf_p50,
            "std": conf_std,
            "n": n,
        },
    )


def _cell(
    trend: str = "trending",
    vol: str = "calm",
    *,
    n: int = 1,
    closed: int = 1,
    wins: int = 0,
    win_rate: float | None = 0.0,
    pnl_total: float = -100.0,
    policy: str = "on",
) -> RegimeCell:
    return RegimeCell(
        trend=trend,
        vol=vol,
        n_decisions=n,
        n_closed=closed,
        n_wins=wins,
        win_rate=win_rate,
        pnl_total=pnl_total,
        expectancy=(pnl_total / closed) if closed else None,
        regime_policy_cell=policy,
    )


# ---------------------------------------------------------------------------
# Decision matrix — boundary cases per row of the threshold table.
# ---------------------------------------------------------------------------


class TestDecisionMatrix:
    """One test per row of docs/strategy-review-gate.md § Threshold table."""

    def test_n_zero_yields_hold(self):
        h = _headline(n_decisions=0, n_closed=0)
        d = decide(h, [], _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "hold"
        assert d.tier == 1

    def test_low_n_catastrophic_with_off_cell_present_kills(self):
        h = _headline(n_closed=20, n_wins=1, win_rate=0.05, pnl_total=-200.0, expectancy=-10.0)
        cells = [_cell(policy="off"), _cell(trend="chop", policy="on")]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "kill"

    def test_low_n_25pct_winrate_all_cells_off_kills(self):
        h = _headline(n_closed=20, n_wins=5, win_rate=0.25, pnl_total=-50.0, expectancy=-2.5)
        cells = [_cell(policy="off"), _cell(trend="chop", policy="off")]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "kill"

    def test_low_n_25pct_winrate_mixed_cells_demotes_shadow(self):
        h = _headline(n_closed=20, n_wins=5, win_rate=0.25, pnl_total=-50.0, expectancy=-2.5)
        cells = [_cell(policy="off"), _cell(trend="chop", policy="on")]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        # one cell off, one on, no all-off → demote_shadow (not kill at low n).
        assert d.action == "demote_shadow"

    def test_low_n_otherwise_holds(self):
        h = _headline(n_closed=20, n_wins=8, win_rate=0.40, pnl_total=10.0, expectancy=0.5)
        cells = [_cell(policy="on", win_rate=0.40, pnl_total=10.0)]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "hold"

    def test_midn_30pct_all_off_kills(self):
        h = _headline(n_closed=50, n_wins=15, win_rate=0.30, pnl_total=-100.0, expectancy=-2.0)
        cells = [_cell(policy="off") for _ in range(3)]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "kill"

    def test_midn_below_40_negative_demotes(self):
        h = _headline(n_closed=50, n_wins=18, win_rate=0.36, pnl_total=-30.0, expectancy=-0.6)
        cells = [_cell(policy="on")]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "demote_shadow"

    def test_midn_40_to_50_with_flat_exp_tunes(self):
        h = _headline(n_closed=50, n_wins=22, win_rate=0.44, pnl_total=0.0, expectancy=0.0)
        cells = [_cell(policy="on")]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "tune"

    def test_midn_50_pos_holds(self):
        h = _headline(n_closed=50, n_wins=30, win_rate=0.60, pnl_total=50.0, expectancy=1.0)
        cells = [_cell(policy="on")]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "hold"

    def test_large_n_below_40_all_off_kills(self):
        h = _headline(n_closed=120, n_wins=40, win_rate=0.333, pnl_total=-400.0, expectancy=-3.3)
        cells = [_cell(policy="off") for _ in range(3)]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "kill"

    def test_large_n_below_40_any_on_demotes(self):
        h = _headline(n_closed=120, n_wins=40, win_rate=0.333, pnl_total=-400.0, expectancy=-3.3)
        cells = [_cell(policy="off"), _cell(trend="chop", policy="on")]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "demote_shadow"

    def test_large_n_promote_requires_winrate_and_dd_within_3x_expectancy(self):
        # 60% WR, exp=2.0, max_dd=-5.0 → within 3× → promote.
        h = _headline(
            n_closed=120, n_wins=72, win_rate=0.60, pnl_total=240.0,
            expectancy=2.0, max_drawdown=-5.0,
        )
        cells = [_cell(policy="on")]
        d = decide(h, cells, _diag(), execution="shadow", shadow_soak_days=20)
        assert d.action == "promote"

    def test_large_n_promote_blocked_when_dd_too_deep(self):
        h = _headline(
            n_closed=120, n_wins=72, win_rate=0.60, pnl_total=240.0,
            expectancy=2.0, max_drawdown=-50.0,  # dd > 3*exp → not within tolerance
        )
        cells = [_cell(policy="on")]
        d = decide(h, cells, _diag(), execution="shadow", shadow_soak_days=20)
        assert d.action == "hold"


# ---------------------------------------------------------------------------
# Overrides.
# ---------------------------------------------------------------------------


class TestOverrides:
    def test_shadow_with_fills_holds_with_anomaly_reason(self):
        h = _headline(n_decisions=10, n_filled=3, n_closed=3, n_wins=0,
                      win_rate=0.0, pnl_total=-30.0, expectancy=-10.0)
        cells = [_cell(policy="off")]
        d = decide(h, cells, _diag(), execution="shadow", shadow_soak_days=0)
        assert d.action == "hold"
        assert any("execution_mode_mismatch" in r for r in d.reasons)

    def test_degenerate_confidence_softens_kill_to_tune_at_low_n(self):
        h = _headline(n_closed=20, n_wins=1, win_rate=0.05, pnl_total=-30.0, expectancy=-1.5)
        cells = [_cell(policy="off")]
        diag = _diag(conf_min=1.0, conf_max=1.0, conf_std=0.0, conf_p50=1.0)
        d = decide(h, cells, diag, execution="live", shadow_soak_days=0)
        # Matrix would say `kill`; degenerate-conf + low n softens to `tune`.
        assert d.action == "tune"
        assert any("degenerate confidence" in r for r in d.reasons)

    def test_degenerate_confidence_does_not_soften_kill_at_higher_n(self):
        h = _headline(n_closed=120, n_wins=10, win_rate=0.08, pnl_total=-500.0, expectancy=-4.16)
        cells = [_cell(policy="off") for _ in range(3)]
        diag = _diag(conf_min=1.0, conf_max=1.0, conf_std=0.0, conf_p50=1.0)
        d = decide(h, cells, diag, execution="live", shadow_soak_days=0)
        # n>=100 + all-off → kill stays kill.
        assert d.action == "kill"

    def test_already_shadow_demote_escalates_to_kill_when_all_off(self):
        h = _headline(n_closed=50, n_wins=10, win_rate=0.20, pnl_total=-100.0, expectancy=-2.0)
        cells = [_cell(policy="off"), _cell(trend="chop", policy="off")]
        # Matrix at this n/wr would say demote_shadow; with execution=shadow + all-off,
        # the gate escalates to kill.
        d = decide(h, cells, _diag(), execution="shadow", shadow_soak_days=10)
        assert d.action == "kill"

    def test_already_shadow_demote_falls_back_to_hold_when_mixed(self):
        h = _headline(n_closed=50, n_wins=18, win_rate=0.36, pnl_total=-30.0, expectancy=-0.6)
        cells = [_cell(policy="off"), _cell(trend="chop", policy="on")]
        d = decide(h, cells, _diag(), execution="shadow", shadow_soak_days=10)
        # No further demotion available — record continued loss; hold.
        assert d.action == "hold"

    def test_promote_requires_14_day_soak(self):
        h = _headline(
            n_closed=120, n_wins=72, win_rate=0.60, pnl_total=240.0,
            expectancy=2.0, max_drawdown=-5.0,
        )
        cells = [_cell(policy="on")]
        d = decide(h, cells, _diag(), execution="shadow", shadow_soak_days=5)
        assert d.action == "hold"
        assert any("14 days shadow soak" in r for r in d.reasons)


# ---------------------------------------------------------------------------
# Regime policy helper.
# ---------------------------------------------------------------------------


class TestRegimePolicyCellHelper:
    def test_both_off_returns_off(self):
        policy = {"trending": {"vwap": {"long": "off", "short": "off"}}}
        assert regime_policy_cell_for(policy, "vwap", "trending") == "off"

    def test_both_on_returns_on(self):
        policy = {"chop": {"trend_donchian": {"long": "on", "short": "on"}}}
        assert regime_policy_cell_for(policy, "trend_donchian", "chop") == "on"

    def test_mixed_returns_unknown(self):
        policy = {"trending": {"trend_donchian": {"long": "on", "short": "off"}}}
        assert regime_policy_cell_for(policy, "trend_donchian", "trending") == "unknown"

    def test_missing_strategy_returns_unknown(self):
        policy = {"trending": {"vwap": {"long": "off", "short": "off"}}}
        assert regime_policy_cell_for(policy, "unknown_strategy", "trending") == "unknown"

    def test_direction_hint_returns_that_direction(self):
        policy = {"trending": {"trend_donchian": {"long": "on", "short": "off"}}}
        assert regime_policy_cell_for(policy, "trend_donchian", "trending", "long") == "on"
        assert regime_policy_cell_for(policy, "trend_donchian", "trending", "short") == "off"


# ---------------------------------------------------------------------------
# Regime slicer — populated DB end-to-end.
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """Build a canonical DB with:

    - 3 vwap order packages in 'trending' regime, all closed losers.
    - 2 vwap order packages in 'chop' regime, one winner one loser.
    - 1 unrelated turtle_soup row.

    Each order_package_id is paired with a row in the `signals` table
    whose meta carries {regime, vol_regime, order_package_id} — the
    same shape the trader's dual-write produces.
    """
    path = tmp_path / "trade_journal.db"
    make_canonical_db(path)
    rows = [
        # (opid, strategy, regime, vol, pnl, closed)
        ("pkg-vwap-tr-1", "vwap", "trending", "calm", -50.0, True),
        ("pkg-vwap-tr-2", "vwap", "trending", "calm", -30.0, True),
        ("pkg-vwap-tr-3", "vwap", "trending", "volatile", -20.0, True),
        ("pkg-vwap-ch-1", "vwap", "chop", "calm", 40.0, True),
        ("pkg-vwap-ch-2", "vwap", "chop", "calm", -10.0, True),
        ("pkg-turtle-1", "turtle_soup", "trending", "calm", 100.0, True),
    ]

    conn = sqlite3.connect(str(path))
    try:
        for opid, strategy, regime, vol, pnl, closed in rows:
            now = "2026-06-09T12:00:00+00:00"
            # Insert trade first; trade_id wires back into order_packages.linked_trade_id.
            trade_id = conn.execute(
                "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
                "position_size, status, pnl, is_backtest, strategy_name, account_id) "
                "VALUES (?, 'BTCUSDT', 'long', 60000.0, 0.001, ?, ?, 0, ?, 'bybit_2')",
                (now, "closed_tp" if closed else "open", pnl, strategy),
            ).lastrowid
            conn.execute(
                "INSERT INTO order_packages "
                "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
                "confidence, created_at, updated_at, status, linked_trade_id) "
                "VALUES (?, ?, 'BTCUSDT', 'long', 60000.0, 59000.0, 62000.0, "
                "0.5, ?, ?, 'closed', ?)",
                (opid, strategy, now, now, trade_id),
            )
            # Signals dual-write row carrying the regime stamp.
            meta = json.dumps({
                "order_package_id": opid,
                "regime": regime,
                "vol_regime": vol,
            })
            conn.execute(
                "INSERT INTO signals (logged_at_utc, strategy, symbol, side, meta) "
                "VALUES (?, ?, 'BTCUSDT', 'long', ?)",
                (now, strategy, meta),
            )
        conn.commit()
    finally:
        conn.close()
    return path


class TestRegimeSlicerEndToEnd:
    def test_per_cell_pnl_attribution_matches_meta_stamps(
        self, populated_db: Path
    ):
        from scripts.ml.strategy_review_packet import (
            pull_decisions,
            pull_regime_stamp_index,
        )

        window_start = datetime(2026, 6, 8, tzinfo=timezone.utc)
        window_end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        decisions = pull_decisions(str(populated_db), "vwap", window_start, window_end)
        regime_index = pull_regime_stamp_index(
            str(populated_db), "vwap", window_start, window_end
        )
        # The fixture writes 5 vwap rows; turtle_soup is filtered out.
        assert len(decisions) == 5

        policy = {
            "trending": {"vwap": {"long": "off", "short": "off"}},
            "chop": {"vwap": {"long": "off", "short": "off"}},
        }
        cells = compute_regime_cells(decisions, regime_index, policy, "vwap")
        by_cell = {(c.trend, c.vol): c for c in cells}

        # trending/calm: 2 rows, both losers (-50, -30) → wins=0, pnl=-80
        c = by_cell[("trending", "calm")]
        assert c.n_decisions == 2
        assert c.n_closed == 2
        assert c.n_wins == 0
        assert c.pnl_total == pytest.approx(-80.0)
        assert c.regime_policy_cell == "off"

        # trending/volatile: 1 row, loser -20
        c = by_cell[("trending", "volatile")]
        assert c.n_decisions == 1
        assert c.pnl_total == pytest.approx(-20.0)
        assert c.regime_policy_cell == "off"

        # chop/calm: 2 rows, one +40 one -10 → wins=1, pnl=30
        c = by_cell[("chop", "calm")]
        assert c.n_decisions == 2
        assert c.n_wins == 1
        assert c.pnl_total == pytest.approx(30.0)
        assert c.regime_policy_cell == "off"

    def test_headline_aggregates_match_per_cell_sum(self, populated_db: Path):
        from scripts.ml.strategy_review_packet import pull_decisions

        window_start = datetime(2026, 6, 8, tzinfo=timezone.utc)
        window_end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        decisions = pull_decisions(str(populated_db), "vwap", window_start, window_end)
        h = compute_headline(decisions)
        assert h.n_decisions == 5
        assert h.n_closed == 5
        assert h.n_wins == 1
        assert h.win_rate == pytest.approx(0.2)
        assert h.pnl_total == pytest.approx(-70.0)
        assert h.expectancy == pytest.approx(-14.0)

    def test_build_packet_end_to_end_returns_kill_for_vwap_all_off(
        self, populated_db: Path
    ):
        # Test the live-execution catastrophic+all-off → kill path
        # directly (execution=shadow with n_filled>0 trips the
        # execution-mode-mismatch override, which is its own test below).
        cfg = {"vwap": {"execution": "live", "enabled": True}}
        policy = {
            "trending": {"vwap": {"long": "off", "short": "off"}},
            "chop": {"vwap": {"long": "off", "short": "off"}},
            "transitional": {"vwap": {"long": "off", "short": "off"}},
        }
        window_start = datetime(2026, 6, 8, tzinfo=timezone.utc)
        window_end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        packet = build_packet(
            strategy="vwap",
            db_path=str(populated_db),
            window_start=window_start,
            window_end=window_end,
            strategies_cfg=cfg,
            regime_policy=policy,
            shadow_soak_days=20,
        )
        assert packet["execution"] == "live"
        # Win rate 20% over n=5 closed: matches low-n catastrophic+all-off → kill.
        assert packet["proposed_action"] == "kill"
        assert packet["sla_due_by"] is not None

    def test_shadow_strategy_with_anomalous_fills_holds_with_override_reason(
        self, populated_db: Path
    ):
        """Same DB, but the strategy is configured shadow → the fixture's
        ``linked_trade_id`` rows are pipeline anomalies; the override-1
        guard fires and the packet holds with a reason — it does NOT
        recommend a kill on indeterminate pipeline state."""
        cfg = {"vwap": {"execution": "shadow", "enabled": True}}
        policy = {
            "trending": {"vwap": {"long": "off", "short": "off"}},
            "chop": {"vwap": {"long": "off", "short": "off"}},
        }
        window_start = datetime(2026, 6, 8, tzinfo=timezone.utc)
        window_end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        packet = build_packet(
            strategy="vwap",
            db_path=str(populated_db),
            window_start=window_start,
            window_end=window_end,
            strategies_cfg=cfg,
            regime_policy=policy,
            shadow_soak_days=20,
        )
        assert packet["proposed_action"] == "hold"
        assert any("execution_mode_mismatch" in r for r in packet["reasons"])
