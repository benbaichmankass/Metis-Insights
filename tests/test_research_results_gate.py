"""R4 research→results shadow-gate — verdict logic + reporter.

Pins the ONE thing the gate must never get wrong: it judges a leg on MEASURED
money and ABSTAINS below the coverage floor, never gating on a fabricated
``totalPnl`` (design §2/§3, the binding data-trust constraint). Pure-logic
tests + an end-to-end reporter run over a synthetic journal (real SQL +
aggregation, no mocks) so the ``totalPnlMeasured`` producer→consumer seam is
covered too.
"""
from __future__ import annotations

import json
import sqlite3

from src.runtime.research_results_gate import (
    ABSTAIN_THIN,
    ABSTAIN_UNVERIFIED,
    PASS,
    WOULD_BLOCK,
    combined_leg_verdict,
    source_verdict,
    summarize,
)


def _stats(**kw):
    base = {
        "name": "leg", "trades": 50, "totalPnl": 0.0, "totalPnlMeasured": 0.0,
        "pnlCoverage": 1.0, "pnlMeasuredCount": 50,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- source_verdict
def test_pass_when_measured_net_positive_and_covered():
    v = source_verdict(_stats(totalPnlMeasured=120.0, pnlCoverage=0.9))
    assert v["status"] == PASS


def test_would_block_when_measured_net_negative_and_covered():
    v = source_verdict(_stats(totalPnlMeasured=-120.0, pnlCoverage=0.9))
    assert v["status"] == WOULD_BLOCK


def test_abstain_below_coverage_floor_even_with_positive_measured_net():
    """The load-bearing one: a leg below the floor NEVER passes on its measured
    net — the measured subset is too thin to trust, so the gate abstains."""
    v = source_verdict(_stats(totalPnlMeasured=500.0, pnlCoverage=0.3))
    assert v["status"] == ABSTAIN_UNVERIFIED


def test_positive_totalPnl_never_rescues_a_zero_measured_leg():
    """The R4 poison: a fabricated book shows a big totalPnl but 0 measured net.
    Even ABOVE the floor a zero measured net is a pass only at exactly break-even
    — a NEGATIVE measured net with a positive fabricated totalPnl must block."""
    v = source_verdict(_stats(totalPnl=9000.0, totalPnlMeasured=-50.0, pnlCoverage=0.8))
    assert v["status"] == WOULD_BLOCK
    assert v["totalPnl"] == 9000.0            # carried for contrast only
    assert v["totalPnlMeasured"] == -50.0     # the actual decision input


def test_abstain_thin_below_min_trades():
    v = source_verdict(_stats(trades=5, totalPnlMeasured=100.0))
    assert v["status"] == ABSTAIN_THIN


def test_none_source_abstains_thin():
    assert source_verdict(None)["status"] == ABSTAIN_THIN
    assert source_verdict({})["status"] == ABSTAIN_THIN


def test_null_coverage_treated_as_zero_not_pass():
    v = source_verdict(_stats(pnlCoverage=None, totalPnlMeasured=100.0))
    assert v["status"] == ABSTAIN_UNVERIFIED


# ------------------------------------------------------- combined_leg_verdict
def test_real_money_wins_when_it_does_not_abstain():
    real = _stats(totalPnlMeasured=-30.0, pnlCoverage=0.9)     # would_block
    mirror = _stats(totalPnlMeasured=200.0, pnlCoverage=0.9)   # pass
    v = combined_leg_verdict(real, mirror)
    assert v["chosenSource"] == "real_money"
    assert v["status"] == WOULD_BLOCK


def test_mirror_carries_when_real_money_abstains():
    real = _stats(trades=3, totalPnlMeasured=10.0)             # abstain_thin
    mirror = _stats(totalPnlMeasured=-80.0, pnlCoverage=0.8)   # would_block
    v = combined_leg_verdict(real, mirror)
    assert v["chosenSource"] == "mirror"
    assert v["status"] == WOULD_BLOCK


def test_both_abstain_reports_abstain():
    real = _stats(trades=2)
    mirror = _stats(trades=1)
    v = combined_leg_verdict(real, mirror)
    assert v["status"] in (ABSTAIN_THIN, ABSTAIN_UNVERIFIED)


def test_summarize_histogram():
    legs = [
        {"status": PASS}, {"status": PASS}, {"status": WOULD_BLOCK},
        {"status": ABSTAIN_THIN},
    ]
    h = summarize(legs)
    assert h == {PASS: 2, WOULD_BLOCK: 1, ABSTAIN_UNVERIFIED: 0, ABSTAIN_THIN: 1}


# ----------------------------------------------------- reporter (end-to-end)
_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY, account_id TEXT, strategy_name TEXT, symbol TEXT,
    direction TEXT, entry_price REAL, stop_loss REAL, position_size REAL,
    pnl REAL, status TEXT, is_backtest INTEGER DEFAULT 0, is_demo INTEGER DEFAULT 0,
    account_class TEXT, setup_type TEXT, reconcile_status TEXT, exit_reason TEXT,
    closed_at TEXT, timestamp TEXT, notes TEXT
);
CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY, linked_trade_id INTEGER, updated_at TEXT);
"""


def _mk_db(tmp_path, rows):
    """rows = [(strategy, pnl, pnl_source, is_demo)]."""
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    for i, (strategy, pnl, src, is_demo) in enumerate(rows, start=1):
        notes = json.dumps({"pnl_source": src}) if src else "{}"
        acct = "bybit_1" if is_demo else "bybit_2"
        klass = "paper" if is_demo else "real_money"
        conn.execute(
            "INSERT INTO trades (id, account_id, strategy_name, symbol, direction, "
            "entry_price, stop_loss, position_size, pnl, status, is_backtest, "
            "is_demo, account_class, closed_at, timestamp, notes) VALUES "
            "(?,?,?,'BTCUSDT','long',100.0,99.0,1.0,?, 'closed',0,?,?,?,?,?)",
            (i, acct, strategy, pnl, is_demo, klass,
             f"2026-07-{10 + (i % 18):02d}T12:00:00Z",
             f"2026-07-{10 + (i % 18):02d}T11:00:00Z", notes),
        )
    conn.commit()
    conn.close()
    return db


def test_reporter_flags_a_measured_loser_and_abstains_on_fabrication(tmp_path):
    from scripts.research.research_results_gate_report import build_report

    # 'loser': 25 real-money MEASURED rows netting negative → would_block.
    # 'fabricated': 25 real-money rows all local_markprice → abstain_unverified.
    rows = [("loser", -5.0, "bybit_closed_pnl", 0) for _ in range(25)]
    rows += [("fabricated", 9000.0, "local_markprice", 0) for _ in range(25)]
    db = _mk_db(tmp_path, rows)

    report = build_report(db, "all", min_trades=20)
    by = {leg["strategy"]: leg for leg in report["legs"]}
    assert by["loser"]["status"] == WOULD_BLOCK
    assert by["loser"]["chosenSource"] == "real_money"
    assert by["fabricated"]["status"] == ABSTAIN_UNVERIFIED
    assert report["enforced"] is False   # OBSERVE-ONLY
    # would_block sorts before abstain (a review looks at it first)
    assert report["legs"][0]["strategy"] == "loser"
