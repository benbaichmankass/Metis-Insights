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
    load_backtest_anchor,
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


# ---------------------------------------------------------------------------
# Backtest anchor — trainer-mirror lookup.
# ---------------------------------------------------------------------------


class TestBacktestAnchor:
    def test_no_mirror_dir_returns_none(self, tmp_path: Path):
        # tmp_path has no `backtests/` subdir → helper returns None cleanly.
        assert load_backtest_anchor("vwap", root=tmp_path) is None

    def test_no_summary_mentioning_strategy_returns_none(self, tmp_path: Path):
        day_dir = tmp_path / "2026-06-04"
        day_dir.mkdir()
        (day_dir / "SUMMARY.md").write_text(
            "# trend_donchian only sweep\nfade_breakout net_r=12.4\n"
        )
        assert load_backtest_anchor("vwap", root=tmp_path) is None

    def test_returns_most_recent_dir_mentioning_strategy(self, tmp_path: Path):
        # Two days: only the older one mentions vwap. Helper still surfaces it.
        old = tmp_path / "2026-05-30"
        old.mkdir()
        (old / "SUMMARY.md").write_text("vwap variant net_r=-8.2 ...\n")
        new = tmp_path / "2026-06-01"
        new.mkdir()
        (new / "SUMMARY.md").write_text("trend_donchian only\n")
        anchor = load_backtest_anchor("vwap", root=tmp_path)
        assert anchor is not None
        assert anchor["date"] == "2026-05-30"
        assert anchor["summary_table_present"] is True
        assert "vwap" in anchor["note"]

    def test_newer_dir_wins_when_both_mention(self, tmp_path: Path):
        old = tmp_path / "2026-05-30"
        old.mkdir()
        (old / "SUMMARY.md").write_text("vwap older\n")
        new = tmp_path / "2026-06-04"
        new.mkdir()
        (new / "SUMMARY.md").write_text("vwap newer\n")
        anchor = load_backtest_anchor("vwap", root=tmp_path)
        assert anchor["date"] == "2026-06-04"

    def test_case_insensitive_match(self, tmp_path: Path):
        day = tmp_path / "2026-06-01"
        day.mkdir()
        (day / "SUMMARY.md").write_text("VWAP variant net_r ...\n")
        anchor = load_backtest_anchor("vwap", root=tmp_path)
        assert anchor is not None


# ---------------------------------------------------------------------------
# Orphaned-package exclusion — the headline gate bug from the M7 first-run.
# ---------------------------------------------------------------------------


class TestOrphanedPackagesAreNotClosedTrades:
    """Regression: a shadow / never-filled package whose ``order_packages.status``
    happens to be ``"closed"`` (the trader marks an orphaned package closed
    when the bar ends) must NOT count toward ``n_closed``. The gate doc's
    headline-table definition (``trades`` JOIN where ``status ∈ closed_*``)
    is the truth; the prior OR-branch on ``pkg_status == "closed"`` inflated
    ``n_closed`` with orphans and pushed strategies with mostly-shadow
    decisions into the catastrophic-zone path of the matrix.

    The htf_pullback_trend_2h finding from M7's first on-VM run (2026-06-09)
    surfaced this: n_decisions=18, n_filled=2, but the headline reported
    n_closed=15 with win_rate=6.7% — 13 of those "closed losses" were
    orphans masquerading as filled-then-closed trades, and the resulting
    demote_shadow proposal would have been a false positive.
    """

    @pytest.fixture
    def db_with_orphans(self, tmp_path: Path) -> Path:
        """Two filled+closed trades (one win, one loss) PLUS thirteen
        orphaned packages (``status='closed'``, no ``linked_trade_id``).
        Honest n_closed = 2; pre-fix n_closed would have been 15.
        """
        path = tmp_path / "trade_journal.db"
        make_canonical_db(path)
        now = "2026-06-09T12:00:00+00:00"

        conn = sqlite3.connect(str(path))
        try:
            # Two real fills: one win, one loss.
            for i, (pnl, status) in enumerate([(50.0, "closed_tp"), (-30.0, "closed_sl")]):
                trade_id = conn.execute(
                    "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
                    "position_size, status, pnl, is_backtest, strategy_name, account_id) "
                    "VALUES (?, 'BTCUSDT', 'long', 60000.0, 0.001, ?, ?, 0, 'demo_strategy', 'bybit_2')",
                    (now, status, pnl),
                ).lastrowid
                conn.execute(
                    "INSERT INTO order_packages "
                    "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
                    "confidence, created_at, updated_at, status, linked_trade_id) "
                    "VALUES (?, 'demo_strategy', 'BTCUSDT', 'long', 60000.0, 59000.0, 62000.0, "
                    "0.7, ?, ?, 'closed', ?)",
                    (f"pkg-filled-{i}", now, now, trade_id),
                )

            # Thirteen orphans: pkg_status='closed', linked_trade_id NULL, no
            # trades row. This is what a shadow strategy emits when the bar
            # closes without the package ever filling.
            for i in range(13):
                conn.execute(
                    "INSERT INTO order_packages "
                    "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
                    "confidence, created_at, updated_at, status, linked_trade_id) "
                    "VALUES (?, 'demo_strategy', 'BTCUSDT', 'long', 60000.0, 59000.0, 62000.0, "
                    "0.7, ?, ?, 'closed', NULL)",
                    (f"pkg-orphan-{i}", now, now),
                )
            conn.commit()
        finally:
            conn.close()
        return path

    def test_headline_n_closed_only_counts_filled_trades(
        self, db_with_orphans: Path
    ):
        from scripts.ml.strategy_review_packet import pull_decisions

        window_start = datetime(2026, 6, 8, tzinfo=timezone.utc)
        window_end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        decisions = pull_decisions(
            str(db_with_orphans), "demo_strategy", window_start, window_end
        )
        assert len(decisions) == 15  # all decisions visible

        h = compute_headline(decisions)
        assert h.n_decisions == 15
        assert h.n_filled == 2
        assert h.n_closed == 2  # the bug: pre-fix this was 15
        assert h.n_wins == 1
        assert h.win_rate == pytest.approx(0.5)  # pre-fix: 1/15 = 6.7%

    def test_orphan_only_strategy_yields_n_closed_zero_and_hold(
        self, tmp_path: Path
    ):
        """A strategy whose entire window is orphans (e.g. execution=shadow
        with zero fills) must surface as ``n_closed=0`` → ``hold`` for
        insufficient evidence, not as ``n_closed=N → catastrophic``."""
        path = tmp_path / "trade_journal.db"
        make_canonical_db(path)
        now = "2026-06-09T12:00:00+00:00"

        conn = sqlite3.connect(str(path))
        try:
            for i in range(20):
                conn.execute(
                    "INSERT INTO order_packages "
                    "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
                    "confidence, created_at, updated_at, status, linked_trade_id) "
                    "VALUES (?, 'shadow_only', 'BTCUSDT', 'long', 60000.0, 59000.0, 62000.0, "
                    "1.0, ?, ?, 'closed', NULL)",
                    (f"pkg-orphan-{i}", now, now),
                )
            conn.commit()
        finally:
            conn.close()

        cfg = {"shadow_only": {"execution": "shadow", "enabled": True}}
        packet = build_packet(
            strategy="shadow_only",
            db_path=str(path),
            window_start=datetime(2026, 6, 8, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 10, tzinfo=timezone.utc),
            strategies_cfg=cfg,
            regime_policy={},
            shadow_soak_days=15,
        )
        h = packet["headline"]
        assert h["n_decisions"] == 20
        assert h["n_filled"] == 0
        assert h["n_closed"] == 0
        assert packet["proposed_action"] == "hold"
        assert any("insufficient evidence" in r for r in packet["reasons"])


# ---------------------------------------------------------------------------
# Regime stamp resolution — read from order_packages.meta, not signals.meta.
# ---------------------------------------------------------------------------


class TestRegimeStampFromPkgMeta:
    """Regression: every package emitted by the live trader carries
    ``regime`` + ``vol_regime`` in ``order_packages.meta`` (the
    strategy's signal builder calls ``_stamp_regime_on_meta`` before
    the package is logged — diag #3116 verified this for ict_scalp_5m
    and vwap under live traffic). The packet's original
    ``pull_regime_stamp_index`` joined against ``signals.meta`` instead,
    where the eval row doesn't carry ``order_package_id`` — so every
    cell came back ``unknown``. The fix reads regime from ``pkg_meta``
    directly (already loaded by ``pull_decisions``).
    """

    @pytest.fixture
    def db_with_pkg_meta_regime(self, tmp_path: Path) -> Path:
        path = tmp_path / "trade_journal.db"
        make_canonical_db(path)
        now = "2026-06-09T12:00:00+00:00"

        conn = sqlite3.connect(str(path))
        try:
            for i, (regime, vol) in enumerate([
                ("trending", "calm"),
                ("trending", "volatile"),
                ("chop", "calm"),
            ]):
                trade_id = conn.execute(
                    "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
                    "position_size, status, pnl, is_backtest, strategy_name, account_id) "
                    "VALUES (?, 'BTCUSDT', 'long', 60000.0, 0.001, 'closed_tp', ?, 0, 'regime_test', 'bybit_2')",
                    (now, 10.0 if i == 0 else -5.0),
                ).lastrowid
                # The strategy's signal builder writes regime + vol_regime
                # into the package meta. Modelled here as a JSON string,
                # which is what pull_decisions returns for op.meta.
                meta_json = json.dumps({
                    "strategy_name": "regime_test",
                    "regime": regime,
                    "vol_regime": vol,
                    "adx_14": 28.0,
                })
                conn.execute(
                    "INSERT INTO order_packages "
                    "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
                    "confidence, created_at, updated_at, status, linked_trade_id, meta) "
                    "VALUES (?, 'regime_test', 'BTCUSDT', 'long', 60000.0, 59000.0, 62000.0, "
                    "0.7, ?, ?, 'closed', ?, ?)",
                    (f"pkg-rg-{i}", now, now, trade_id, meta_json),
                )
            conn.commit()
        finally:
            conn.close()
        return path

    def test_cells_resolve_from_pkg_meta_when_signals_join_empty(
        self, db_with_pkg_meta_regime: Path
    ):
        from scripts.ml.strategy_review_packet import pull_decisions

        ws = datetime(2026, 6, 8, tzinfo=timezone.utc)
        we = datetime(2026, 6, 10, tzinfo=timezone.utc)
        decisions = pull_decisions(
            str(db_with_pkg_meta_regime), "regime_test", ws, we
        )
        assert len(decisions) == 3

        # Empty signals index — exactly what the live VM was returning.
        cells = compute_regime_cells(decisions, {}, {}, "regime_test")
        by_cell = {(c.trend, c.vol): c for c in cells}
        assert ("trending", "calm") in by_cell
        assert ("trending", "volatile") in by_cell
        assert ("chop", "calm") in by_cell
        # The catastrophic-zone tracking still works:
        c = by_cell[("trending", "calm")]
        assert c.n_closed == 1
        assert c.n_wins == 1
        assert c.pnl_total == pytest.approx(10.0)

    def test_pkg_meta_takes_precedence_over_signals_lookup(
        self, db_with_pkg_meta_regime: Path
    ):
        """If both sources have data, pkg_meta wins (it's the
        authoritative source — written at decision time, no JOIN
        timing race)."""
        from scripts.ml.strategy_review_packet import pull_decisions

        ws = datetime(2026, 6, 8, tzinfo=timezone.utc)
        we = datetime(2026, 6, 10, tzinfo=timezone.utc)
        decisions = pull_decisions(
            str(db_with_pkg_meta_regime), "regime_test", ws, we
        )
        # Bogus signals-lookup data — should be ignored when pkg_meta has it.
        regime_index = {
            "pkg-rg-0": {"regime": "WRONG", "vol_regime": "WRONG"},
        }
        cells = compute_regime_cells(decisions, regime_index, {}, "regime_test")
        # pkg_meta says trending/calm — that's what wins, NOT WRONG.
        assert ("trending", "calm") in {(c.trend, c.vol) for c in cells}

    def test_signals_lookup_used_when_pkg_meta_missing_regime_field(
        self, tmp_path: Path
    ):
        """Backwards-compat: packages with meta JSON that PREDATES
        ``_stamp_regime_on_meta`` (no regime/vol_regime fields) still
        resolve via the signals lookup."""
        path = tmp_path / "trade_journal.db"
        make_canonical_db(path)
        now = "2026-06-09T12:00:00+00:00"
        conn = sqlite3.connect(str(path))
        try:
            trade_id = conn.execute(
                "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
                "position_size, status, pnl, is_backtest, strategy_name, account_id) "
                "VALUES (?, 'BTCUSDT', 'long', 60000.0, 0.001, 'closed_tp', 5.0, 0, 'legacy_pkg', 'bybit_2')",
                (now,),
            ).lastrowid
            # Legacy meta JSON: no regime/vol_regime fields.
            conn.execute(
                "INSERT INTO order_packages "
                "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
                "confidence, created_at, updated_at, status, linked_trade_id, meta) "
                "VALUES ('pkg-legacy', 'legacy_pkg', 'BTCUSDT', 'long', 60000.0, 59000.0, 62000.0, "
                "0.7, ?, ?, 'closed', ?, ?)",
                (now, now, trade_id, json.dumps({"strategy_name": "legacy_pkg"})),
            )
            conn.commit()
        finally:
            conn.close()

        from scripts.ml.strategy_review_packet import pull_decisions

        ws = datetime(2026, 6, 8, tzinfo=timezone.utc)
        we = datetime(2026, 6, 10, tzinfo=timezone.utc)
        decisions = pull_decisions(str(path), "legacy_pkg", ws, we)
        regime_index = {"pkg-legacy": {"regime": "chop", "vol_regime": "calm"}}
        cells = compute_regime_cells(decisions, regime_index, {}, "legacy_pkg")
        assert ("chop", "calm") in {(c.trend, c.vol) for c in cells}


# ---------------------------------------------------------------------------
# YAML boolean coercion (PyYAML reads `off` as False) — must normalize.
# ---------------------------------------------------------------------------


class TestPolicyValueNormalization:
    """Regression: PyYAML 1.1 booleans (`off`/`on` unquoted) coerce to
    Python `False`/`True`. The matrix's ``c.regime_policy_cell == "off"``
    check never matches a boolean, so the all-cells-off escalation path
    silently broke on the 2026-06-09 vwap packet (460 packages across
    9 policy-OFF cells, matrix never escalated).
    """

    def test_yaml_bool_false_normalizes_to_off(self):
        # Modelling exactly what yaml.safe_load returns for `off`.
        policy = {"trending": {"vwap": {"long": False, "short": False}}}
        assert regime_policy_cell_for(policy, "vwap", "trending") == "off"

    def test_yaml_bool_true_normalizes_to_on(self):
        policy = {"chop": {"trend_donchian": {"long": True, "short": True}}}
        assert regime_policy_cell_for(policy, "trend_donchian", "chop") == "on"

    def test_string_off_passes_through(self):
        policy = {"trending": {"vwap": {"long": "off", "short": "off"}}}
        assert regime_policy_cell_for(policy, "vwap", "trending") == "off"

    def test_mixed_bool_yields_unknown(self):
        policy = {"trending": {"trend_donchian": {"long": True, "short": False}}}
        assert regime_policy_cell_for(policy, "trend_donchian", "trending") == "unknown"

    def test_direction_hint_with_bool_value(self):
        policy = {"trending": {"htf_pullback_trend_2h": {"long": True, "short": False}}}
        # long-only packet → policy: on
        assert (
            regime_policy_cell_for(
                policy, "htf_pullback_trend_2h", "trending", "long"
            )
            == "on"
        )
        # short-only packet → policy: off
        assert (
            regime_policy_cell_for(
                policy, "htf_pullback_trend_2h", "trending", "short"
            )
            == "off"
        )

    def test_decide_kill_path_works_with_bool_policy_values(self):
        """End-to-end: when every cell's policy is parsed as bool False
        (PyYAML 1.1), the matrix's all-off escalation MUST fire."""
        h = _headline(
            n_closed=120, n_wins=10, win_rate=0.08, pnl_total=-500.0, expectancy=-4.16
        )
        # All cells policy: off, modelled as the bool False the YAML
        # parser returns — the normalization should bridge so kill fires.
        bool_off_cell = _cell(policy="off")  # already string via the helper
        cells = [bool_off_cell for _ in range(3)]
        d = decide(h, cells, _diag(), execution="live", shadow_soak_days=0)
        assert d.action == "kill"


# ---------------------------------------------------------------------------
# Direction-aware policy lookup — per-cell direction hint.
# ---------------------------------------------------------------------------


class TestDirectionAwarePolicyLookup:
    """Regression: htf_pullback_trend_2h's live packages on the 2026-06-09
    run all fired ``direction=long``; ``regime_policy.yaml`` lists
    ``trending: htf_pullback_trend_2h: { long: on, short: off }``. The
    rolled-up verdict reads ``unknown`` (mixed) — but the cell is
    unambiguously ``on`` for the actual direction (long). Bucketing
    should pass the direction hint when all packages share one.
    """

    @pytest.fixture
    def db_long_only_bucket(self, tmp_path: Path) -> Path:
        path = tmp_path / "trade_journal.db"
        make_canonical_db(path)
        now = "2026-06-09T12:00:00+00:00"
        conn = sqlite3.connect(str(path))
        try:
            for i in range(3):
                trade_id = conn.execute(
                    "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
                    "position_size, status, pnl, is_backtest, strategy_name, account_id) "
                    "VALUES (?, 'BTCUSDT', 'long', 60000.0, 0.001, 'closed_tp', -1.0, 0, "
                    "'htf_pullback_trend_2h', 'bybit_2')",
                    (now,),
                ).lastrowid
                meta_json = json.dumps({
                    "strategy_name": "htf_pullback_trend_2h",
                    "regime": "trending",
                    "vol_regime": "calm",
                })
                conn.execute(
                    "INSERT INTO order_packages "
                    "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
                    "confidence, created_at, updated_at, status, linked_trade_id, meta) "
                    "VALUES (?, 'htf_pullback_trend_2h', 'BTCUSDT', 'long', 60000.0, "
                    "59000.0, 62000.0, 0.7, ?, ?, 'closed', ?, ?)",
                    (f"pkg-hp-{i}", now, now, trade_id, meta_json),
                )
            conn.commit()
        finally:
            conn.close()
        return path

    def test_long_only_bucket_resolves_long_direction_policy(
        self, db_long_only_bucket: Path
    ):
        from scripts.ml.strategy_review_packet import pull_decisions

        ws = datetime(2026, 6, 8, tzinfo=timezone.utc)
        we = datetime(2026, 6, 10, tzinfo=timezone.utc)
        decisions = pull_decisions(
            str(db_long_only_bucket), "htf_pullback_trend_2h", ws, we
        )
        policy = {
            "trending": {
                "htf_pullback_trend_2h": {"long": "on", "short": "off"},
            },
        }
        cells = compute_regime_cells(decisions, {}, policy, "htf_pullback_trend_2h")
        # All three packages are direction=long → policy resolves to "on"
        # (the long-direction policy), NOT the rolled-up "unknown".
        assert len(cells) == 1
        assert cells[0].regime_policy_cell == "on"

    def test_mixed_direction_bucket_falls_back_to_rolled_up_verdict(
        self, tmp_path: Path
    ):
        path = tmp_path / "trade_journal.db"
        make_canonical_db(path)
        now = "2026-06-09T12:00:00+00:00"
        conn = sqlite3.connect(str(path))
        try:
            for i, direction in enumerate(["long", "short", "long"]):
                trade_id = conn.execute(
                    "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
                    "position_size, status, pnl, is_backtest, strategy_name, account_id) "
                    "VALUES (?, 'BTCUSDT', ?, 60000.0, 0.001, 'closed_tp', -1.0, 0, "
                    "'mixed_strat', 'bybit_2')",
                    (now, direction),
                ).lastrowid
                meta_json = json.dumps({
                    "strategy_name": "mixed_strat",
                    "regime": "trending",
                    "vol_regime": "calm",
                })
                conn.execute(
                    "INSERT INTO order_packages "
                    "(order_package_id, strategy_name, symbol, direction, entry, sl, tp, "
                    "confidence, created_at, updated_at, status, linked_trade_id, meta) "
                    "VALUES (?, 'mixed_strat', 'BTCUSDT', ?, 60000.0, "
                    "59000.0, 62000.0, 0.7, ?, ?, 'closed', ?, ?)",
                    (f"pkg-mx-{i}", direction, now, now, trade_id, meta_json),
                )
            conn.commit()
        finally:
            conn.close()

        from scripts.ml.strategy_review_packet import pull_decisions

        ws = datetime(2026, 6, 8, tzinfo=timezone.utc)
        we = datetime(2026, 6, 10, tzinfo=timezone.utc)
        decisions = pull_decisions(str(path), "mixed_strat", ws, we)
        policy = {
            "trending": {"mixed_strat": {"long": "on", "short": "off"}},
        }
        cells = compute_regime_cells(decisions, {}, policy, "mixed_strat")
        # 2 longs + 1 short → no single direction → rolled-up "unknown"
        assert cells[0].regime_policy_cell == "unknown"
