"""Tests for the M24 P2 net-R re-grade scorecard (scripts/research/net_r_regrade.py).

The pure core (``regrade``) is exercised directly WITHOUT a DB; a tiny temp
SQLite fixture additionally verifies the read-only ``load_trades`` reader (the
``strategy_name`` → ``strategy`` alias + the schema-defensive SELECT) round-trips
into the same scorecard.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

# Load the script as a module (it lives under scripts/, not an importable pkg).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "research" / "net_r_regrade.py"
_spec = importlib.util.spec_from_file_location("net_r_regrade", _SCRIPT)
nrr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nrr)


# ---------------------------------------------------------------------------
# Fixture rows — the five cases the P2 spec calls for.
# All crypto (contract_value 1.0) so risk math is transparent.
# ---------------------------------------------------------------------------
def _rows():
    return [
        # (1) broker-costed WINNER: risk 20, gross 40 (gross_R +2.0),
        #     fees 1.0+0.5, funding 0.3 → net 38.2 → net_R +1.91.
        dict(id=1, strategy="trend_donchian", symbol="BTCUSDT", direction="long",
             entry_price=100.0, stop_loss=90.0, position_size=2.0, pnl=40.0,
             fee_taker_usd=1.0, fee_maker_usd=0.5, funding_paid_usd=0.3,
             cost_source="broker"),
        # (2) estimate-costed LOSER: risk 5, gross -10 (gross_R -2.0),
        #     taker 0.5 → net -10.5 → net_R -2.1 (no flip — a loser stays a loser).
        dict(id=2, strategy="vwap", symbol="ETHUSDT", direction="short",
             entry_price=50.0, stop_loss=55.0, position_size=1.0, pnl=-10.0,
             fee_taker_usd=0.5, fee_maker_usd=0.0, funding_paid_usd=0.0,
             cost_source="estimate"),
        # (3) funding-heavy near-breakeven that FLIPS SIGN: risk 5,
        #     gross +2 (gross_R +0.4) but funding 3.0 → net -1.0 → net_R -0.2.
        dict(id=3, strategy="funding_flip", symbol="SOLUSDT", direction="long",
             entry_price=100.0, stop_loss=95.0, position_size=1.0, pnl=2.0,
             fee_taker_usd=0.0, fee_maker_usd=0.0, funding_paid_usd=3.0,
             cost_source="broker"),
        # (4) UNCOSTED row: risk 10, gross 5, no cost_source → costed False,
        #     R still computable (costs count as 0).
        dict(id=4, strategy="uncosted_strat", symbol="BTCUSDT", direction="long",
             entry_price=100.0, stop_loss=90.0, position_size=1.0, pnl=5.0),
        # (5) RISK-UNCOMPUTABLE row: no stop → net_R None → r_uncomputable bucket.
        dict(id=5, strategy="noR_strat", symbol="BTCUSDT", direction="long",
             entry_price=100.0, stop_loss=None, position_size=1.0, pnl=10.0,
             cost_source="broker"),
    ]


def _cell(report, strategy, symbol):
    for r in report["by_cell"]:
        if r["strategy"] == strategy and r["symbol"] == symbol:
            return r
    raise AssertionError(f"cell {strategy}/{symbol} not found")


def _strat(report, strategy):
    for r in report["by_strategy"]:
        if r["strategy"] == strategy:
            return r
    raise AssertionError(f"strategy {strategy} not found")


# ---------------------------------------------------------------------------
# Pure-core tests (no DB).
# ---------------------------------------------------------------------------
def test_coverage_buckets():
    rep = nrr.regrade(_rows())
    cov = rep["coverage"]
    assert cov["total"] == 5
    assert cov["broker_costed"] == 2      # rows 1 + 3
    assert cov["estimate_costed"] == 1    # row 2
    assert cov["uncosted"] == 1           # row 4
    assert cov["r_uncomputable"] == 1     # row 5
    assert rep["trade_count"] == 5


def test_per_cell_net_r_aggregates():
    rep = nrr.regrade(_rows())
    win = _cell(rep, "trend_donchian", "BTCUSDT")
    assert win["n"] == 1 and win["n_r_measured"] == 1
    assert abs(win["sum_gross_R"] - 2.0) < 1e-9
    assert abs(win["sum_net_R"] - 1.91) < 1e-9
    assert abs(win["cost_drag_R"] - (2.0 - 1.91)) < 1e-9
    assert win["mean_net_R_broker"] is not None
    assert win["sign_flip"] is False

    loser = _cell(rep, "vwap", "ETHUSDT")
    assert abs(loser["sum_gross_R"] - (-2.0)) < 1e-9
    assert abs(loser["sum_net_R"] - (-2.1)) < 1e-9
    assert loser["sign_flip"] is False    # loser → worse loser, not a flip

    uncosted = _cell(rep, "uncosted_strat", "BTCUSDT")
    assert uncosted["n_r_measured"] == 1
    assert abs(uncosted["sum_net_R"] - 0.5) < 1e-9   # gross 5 / risk 10, no costs
    assert uncosted["coverage"]["uncosted"] == 1


def test_sign_flip_fires_on_funding_flip_cell():
    rep = nrr.regrade(_rows())
    flip = _cell(rep, "funding_flip", "SOLUSDT")
    assert abs(flip["sum_gross_R"] - 0.4) < 1e-9    # +2 / 5 → gross winner
    assert abs(flip["sum_net_R"] - (-0.2)) < 1e-9   # net -1 / 5 → net loser
    assert flip["sign_flip"] is True

    # It also surfaces in the sign_flips list (both cell + strategy scope).
    flip_strats = {f["strategy"] for f in rep["sign_flips"]}
    assert "funding_flip" in flip_strats
    assert any(f["scope"] == "cell" and f["strategy"] == "funding_flip"
               for f in rep["sign_flips"])
    # The winner must NOT be flagged.
    assert "trend_donchian" not in flip_strats


def test_strategy_rollup_and_r_uncomputable_excluded_from_sums():
    rep = nrr.regrade(_rows())
    noR = _strat(rep, "noR_strat")
    assert noR["n"] == 1
    assert noR["n_r_measured"] == 0             # risk-uncomputable → not summed
    assert noR["sum_net_R"] == 0.0
    assert noR["coverage"]["r_uncomputable"] == 1
    assert noR["sign_flip"] is False


def test_empty_input():
    rep = nrr.regrade([])
    assert rep["trade_count"] == 0
    assert rep["coverage"]["total"] == 0
    assert rep["by_strategy"] == []
    assert rep["sign_flips"] == []


# ---------------------------------------------------------------------------
# DB round-trip test — read-only load_trades over a temp SQLite fixture.
# Uses the strategy_name column to exercise the strategy_name → strategy alias.
# ---------------------------------------------------------------------------
def _build_fixture_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            strategy_name TEXT,
            symbol TEXT,
            direction TEXT,
            entry_price REAL,
            stop_loss REAL,
            position_size REAL,
            pnl REAL,
            fee_taker_usd REAL,
            fee_maker_usd REAL,
            funding_paid_usd REAL,
            cost_source TEXT,
            status TEXT,
            is_backtest INTEGER,
            closed_at TEXT,
            timestamp TEXT
        )
        """
    )
    for r in _rows():
        conn.execute(
            "INSERT INTO trades (id, strategy_name, symbol, direction, entry_price, "
            "stop_loss, position_size, pnl, fee_taker_usd, fee_maker_usd, "
            "funding_paid_usd, cost_source, status, is_backtest, closed_at, timestamp) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["id"], r["strategy"], r["symbol"], r["direction"], r["entry_price"],
                r.get("stop_loss"), r["position_size"], r["pnl"],
                r.get("fee_taker_usd"), r.get("fee_maker_usd"),
                r.get("funding_paid_usd"), r.get("cost_source"),
                "closed", 0, "2026-07-10T00:00:00Z", "2026-07-10T00:00:00Z",
            ),
        )
    # A backtest row that MUST be excluded by load_trades.
    conn.execute(
        "INSERT INTO trades (id, strategy_name, symbol, direction, entry_price, "
        "stop_loss, position_size, pnl, cost_source, status, is_backtest, "
        "closed_at, timestamp) VALUES (99,'bt','BTCUSDT','long',100,90,1,999,"
        "'broker','closed',1,'2026-07-10T00:00:00Z','2026-07-10T00:00:00Z')"
    )
    conn.commit()
    conn.close()


def test_load_trades_readonly_roundtrip(tmp_path):
    db = tmp_path / "fixture.db"
    _build_fixture_db(db)

    trades = nrr.load_trades(str(db))
    assert len(trades) == 5                       # backtest row excluded
    # strategy_name → strategy alias applied.
    assert all("strategy" in t for t in trades)
    assert {t["strategy"] for t in trades} == {
        "trend_donchian", "vwap", "funding_flip", "uncosted_strat", "noR_strat"
    }

    rep = nrr.regrade(trades)
    assert rep["coverage"]["total"] == 5
    assert _cell(rep, "funding_flip", "SOLUSDT")["sign_flip"] is True
    assert abs(_cell(rep, "trend_donchian", "BTCUSDT")["sum_net_R"] - 1.91) < 1e-9

    # The DB was opened read-only — a write must be refused.
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        raised = False
        try:
            ro.execute("UPDATE trades SET pnl = 0 WHERE id = 1")
        except sqlite3.OperationalError:
            raised = True
        assert raised, "mode=ro connection must refuse writes"
    finally:
        ro.close()
