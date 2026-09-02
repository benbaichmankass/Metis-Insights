"""`/api/bot/performance` publishes R-DENOMINATOR provenance beside `rCoverage`.

WHY. `rCoverage` answers *how much of this window is R-MEASURABLE*. It has never
answered *and was that risk the trade's INITIAL risk* — a different question with
a worse answer. `trades.stop_loss` holds the CURRENT stop, not the initial one:
``src/runtime/order_monitor.py::_apply_update`` mirrors every confirmed trailing
amend onto the row (correctly — ``/api/bot/positions`` must show where the stop
IS). R is defined against entry-time risk, so a trailed stop shrinks the
denominator without bound and inflates `totalR` / `expectancyR` with it.

MEASURED — live journal copy `/home/ubuntu/ict-trading-bot/data/trade_journal.db`
on the trainer VM, mtime 2026-09-02T04:28:35Z, max(created_at)
2026-09-02T04:11:21Z, trader serving sha 2c7ae605. Population: closed, pnl NOT
NULL, non-backtest, n=1346 — contaminated 118 · confirmedInitial 156 ·
unverified 1051 · noBasis 21.

⚠️ THESE TESTS DRIVE THE REAL SQL AND THE REAL AGGREGATION against a synthetic
journal, so the optional-column select, the order_packages PK join and the
JSON-meta read are all actually exercised — a mocked row would prove none of it.

⚠️ EVERY DISCRIMINATION IS ASSERTED IN BOTH DIRECTIONS. A route that reported
`contaminated` for every row would pass a one-sided suite.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.web.api.routers.performance import _aggregate, _empty, _query

_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY, account_id TEXT, strategy_name TEXT, symbol TEXT,
    direction TEXT, entry_price REAL, stop_loss REAL, take_profit_1 REAL,
    position_size REAL, pnl REAL, status TEXT, is_backtest INTEGER DEFAULT 0,
    is_demo INTEGER DEFAULT 0, account_class TEXT, setup_type TEXT,
    reconcile_status TEXT, exit_reason TEXT, closed_at TEXT, timestamp TEXT,
    notes TEXT, order_package_id TEXT
);
CREATE TABLE order_packages (
    order_package_id TEXT PRIMARY KEY, linked_trade_id INTEGER,
    updated_at TEXT, meta TEXT
);
"""


def _mk_db(tmp_path, rows, *, schema=_SCHEMA):
    """rows = [(strategy, direction, entry, stop, tp, pnl, declared_risk|None)]"""
    tmp_path.mkdir(parents=True, exist_ok=True)   # the both-direction pairs
    db = tmp_path / "trade_journal.db"                #  use tmp_path/"a" | "b"
    conn = sqlite3.connect(str(db))
    conn.executescript(schema)
    for i, (strategy, direction, entry, stop, tp, pnl, declared) in enumerate(rows, start=1):
        opid = f"pkg-{i}"
        conn.execute(
            "INSERT INTO trades (id, account_id, strategy_name, symbol, direction,"
            " entry_price, stop_loss, take_profit_1, position_size, pnl, status,"
            " is_backtest, is_demo, account_class, exit_reason, closed_at,"
            " timestamp, notes, order_package_id)"
            " VALUES (?,?,?,?,?,?,?,?,1.0,?,'closed',0,0,'real_money','sl_cross',?,?,'{}',?)",
            [i, "bybit_2", strategy, "BTCUSDT", direction, entry, stop, tp, pnl,
             f"2026-07-{10 + i:02d}T12:00:00Z", f"2026-07-{10 + i:02d}T11:00:00Z", opid])
        meta = json.dumps({"risk_per_unit": declared}) if declared is not None else None
        conn.execute(
            "INSERT INTO order_packages (order_package_id, linked_trade_id,"
            " updated_at, meta) VALUES (?,?,?,?)",
            [opid, i, f"2026-07-{10 + i:02d}T12:00:00Z", meta])
    conn.commit()
    conn.close()
    return db


def _agg(tmp_path, rows, **kw):
    return _aggregate(_query(_mk_db(tmp_path, rows, **kw), since=None), "all", None)


def _strat(agg, name):
    return next(e for e in agg["perStrategy"] if e["name"] == name)


# ─────────────────────── the block exists and partitions ───────────────────
def test_the_block_exists_with_every_state_key(tmp_path):
    agg = _agg(tmp_path, [("vwap", "long", 100.0, 99.0, 110.0, 5.0, None)])
    rp = agg["rProvenance"]
    for key in ("contaminated", "confirmedInitial", "unverified", "noBasis",
                "tightenedVsDeclared", "declaredRiskRecords", "ratioBar"):
        assert key in rp, f"missing {key}"


def test_the_empty_envelope_carries_the_block_too(tmp_path):
    # A key that disappears on an empty window makes a consumer branch on
    # absence, and absence is not one of the states.
    rp = _empty("all", None)["rProvenance"]
    assert rp["contaminated"] == 0 and rp["unverified"] == 0
    assert "ratioBar" in rp


def test_the_four_states_partition_the_population(tmp_path):
    rows = [
        ("a", "long",  100.0, 101.0, 110.0,  5.0, None),   # contaminated
        ("a", "long",  100.0,  98.0, 110.0,  5.0, 2.0),    # confirmed
        ("a", "long",  100.0,  99.0, 110.0,  5.0, None),   # unverified
        ("a", "short", 100.0, 101.0,  90.0, -5.0, None),   # unverified (clean side)
        ("a", "long",  100.0,  None, 110.0,  5.0, None),   # no_basis (no stop)
    ]
    agg = _agg(tmp_path, rows)
    rp = agg["rProvenance"]
    # Each bucket is asserted NON-ZERO, so a bucket that stops being published
    # (or is hard-coded to 0) breaks THIS test. That matters: the
    # `collapsed-state-guard` registration's consumer evidence is satisfied by
    # any repo file naming the constants — this suite included — so the guard
    # does NOT by itself prove the ROUTE branches on all four. This does.
    assert rp["contaminated"] == 1
    assert rp["confirmedInitial"] == 1
    assert rp["unverified"] == 2
    assert rp["noBasis"] == 1
    total = rp["contaminated"] + rp["confirmedInitial"] + rp["unverified"] + rp["noBasis"]
    assert total == agg["totalTrades"] == 5, "the partition must be checkable by arithmetic"


# ─────────────────── both-direction discrimination controls ────────────────
def test_a_planted_trailed_row_is_flagged_and_its_clean_twin_is_not(tmp_path):
    """The load-bearing control: same row, stop moved to the risk side."""
    contaminated = _agg(tmp_path / "a", [("a", "long", 100.0, 101.0, 110.0, 5.0, None)])
    clean = _agg(tmp_path / "b", [("a", "long", 100.0, 99.0, 110.0, 5.0, None)])
    assert contaminated["rProvenance"]["contaminated"] == 1
    assert clean["rProvenance"]["contaminated"] == 0, (
        "the detector fired on a clean row — it has been shown to FIRE, not to "
        "DISCRIMINATE")


def test_the_short_side_discriminates_too(tmp_path):
    contaminated = _agg(tmp_path / "a", [("a", "short", 100.0, 99.0, 90.0, 5.0, None)])
    clean = _agg(tmp_path / "b", [("a", "short", 100.0, 101.0, 90.0, 5.0, None)])
    assert contaminated["rProvenance"]["contaminated"] == 1
    assert clean["rProvenance"]["contaminated"] == 0


def test_a_mirrored_bracket_is_NOT_reported_as_contaminated(tmp_path):
    """Trade 3319 (sol_pullback_2h, setup_type='intent_reduce'): a reduce leg
    whose `direction` is the closing side while its SL/TP are the ORIGINAL
    position's, so the WHOLE bracket reads inverted. 108 of 108 such rows on
    the live journal are intent_reduce. A trail moves the STOP; a mirror moves
    BOTH, and that is the discriminator."""
    agg = _agg(tmp_path, [("a", "long", 78.27, 80.5325, 70.52127, -5.0, None)])
    assert agg["rProvenance"]["contaminated"] == 0
    assert agg["rProvenance"]["unverified"] == 1


def test_the_mirror_discriminator_turns_on_the_TP_alone(tmp_path):
    mirrored = _agg(tmp_path / "a", [("a", "long", 78.27, 80.5325, 70.52127, -5.0, None)])
    trailed = _agg(tmp_path / "b", [("a", "long", 78.27, 80.5325, 86.0, -5.0, None)])
    assert mirrored["rProvenance"]["contaminated"] == 0
    assert trailed["rProvenance"]["contaminated"] == 1


# ───────────────────── the declared-risk second axis ───────────────────────
def test_a_matching_declared_risk_confirms_and_a_disagreeing_one_does_not(tmp_path):
    """The order_packages PK join and the JSON read are exercised here — if the
    join were broken BOTH would land `unverified` and this pair would fail."""
    confirmed = _agg(tmp_path / "a", [("a", "long", 100.0, 98.0, 110.0, 5.0, 2.0)])
    disagree = _agg(tmp_path / "b", [("a", "long", 100.0, 98.0, 110.0, 5.0, 9.0)])
    assert confirmed["rProvenance"]["confirmedInitial"] == 1
    assert disagree["rProvenance"]["confirmedInitial"] == 0
    assert disagree["rProvenance"]["unverified"] == 1, (
        "a disagreement is NOT a proof — it must land in UNVERIFIED, never in "
        "CONTAMINATED")


def test_tightened_count_catches_what_the_side_test_cannot(tmp_path):
    """A stop trailed to just SHORT of entry is side-plausible and just as
    wrong. It is the detector's blind spot on the side axis, and the whole
    reason the declared-risk axis is published."""
    agg = _agg(tmp_path, [("a", "long", 100.0, 99.99, 110.0, 500.0, 5.0)])
    assert agg["rProvenance"]["contaminated"] == 0, "side test cannot see it"
    assert agg["rProvenance"]["tightenedVsDeclared"] == 1, "the ratio axis can"
    assert agg["rProvenance"]["declaredRiskRecords"] == 1, (
        "the bar-crossing count must ship with its own denominator")


def test_a_row_with_no_declared_record_is_not_counted_in_the_denominator(tmp_path):
    agg = _agg(tmp_path, [("a", "long", 100.0, 99.99, 110.0, 500.0, None)])
    assert agg["rProvenance"]["declaredRiskRecords"] == 0
    assert agg["rProvenance"]["tightenedVsDeclared"] == 0


# ───────────────────────── nothing is excluded ─────────────────────────────
def test_totalR_and_rCoverage_are_UNCHANGED_by_the_detector(tmp_path):
    """The rule this whole change is built around. Silently dropping the
    contaminated rows would convert a visible-wrong number into an
    invisible-wrong one over an unstated population."""
    rows = [
        ("a", "long", 100.0, 101.0, 110.0, 5.0, None),   # contaminated, R = 5/1
        ("a", "long", 100.0,  99.0, 110.0, 5.0, None),   # clean,        R = 5/1
    ]
    agg = _agg(tmp_path, rows)
    assert agg["totalTrades"] == 2
    assert agg["rTradeCount"] == 2, "the contaminated row is STILL in the R count"
    assert agg["rCoverage"] == 1.0
    assert agg["totalR"] == pytest.approx(10.0), "its R is STILL in the sum"
    assert agg["rProvenance"]["contaminated"] == 1, "...and it is REPORTED"


# ──────────────────────────── per-strategy ─────────────────────────────────
def test_per_strategy_block_is_published_and_partitions(tmp_path):
    rows = [
        ("dirty", "long", 100.0, 101.0, 110.0, 5.0, None),
        ("dirty", "long", 100.0, 101.5, 110.0, 5.0, None),
        ("clean", "long", 100.0,  98.0, 110.0, 5.0, 2.0),
    ]
    agg = _agg(tmp_path, rows)
    dirty, clean = _strat(agg, "dirty"), _strat(agg, "clean")
    assert dirty["rProvenance"]["contaminated"] == 2
    assert clean["rProvenance"]["contaminated"] == 0
    assert clean["rProvenance"]["confirmedInitial"] == 1
    for row in (dirty, clean):
        rp = row["rProvenance"]
        assert sum(rp.values()) == row["trades"], (
            "per-strategy counts must sum to `trades` by construction")


def test_a_wholly_contaminated_leg_is_visible_beside_its_inflated_expectancyR(tmp_path):
    """The shape that motivated this: `mgc_trend_1h` paper reads 18 of 19
    contaminated on the live journal. A consumer reading `expectancyR` alone
    sees a number; reading `rProvenance` beside it sees that the number is
    computed from a stop the trade never risked."""
    rows = [("mgc_trend_1h", "long", 4318.872, 4319.97857143, 4400.0, -4437.76, None)]
    leg = _strat(_agg(tmp_path, rows), "mgc_trend_1h")
    assert leg["expectancyR"] is not None, "the number is still published"
    assert leg["rProvenance"]["contaminated"] == leg["rTradeCount"] == 1, (
        "...and 100% of the R behind it is provably not initial-stop risk")


# ─────────────────── graceful degradation on a legacy schema ───────────────
def test_a_schema_without_direction_degrades_to_unverified_never_confirmed(tmp_path):
    """A missing optional column must degrade the grade toward *we could not
    look*, never toward a confirmation, and must not 500 the endpoint."""
    schema = _SCHEMA.replace("    direction TEXT, entry_price REAL,",
                             "    entry_price REAL,")
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(schema)
    conn.execute(
        "INSERT INTO trades (id, account_id, strategy_name, symbol, entry_price,"
        " stop_loss, take_profit_1, position_size, pnl, status, is_backtest,"
        " is_demo, account_class, exit_reason, closed_at, timestamp, notes,"
        " order_package_id) VALUES"
        " (1,'bybit_2','a','BTCUSDT',100.0,98.0,110.0,1.0,5.0,'closed',0,0,"
        " 'real_money','sl_cross','2026-07-11T12:00:00Z','2026-07-11T11:00:00Z',"
        " '{}','pkg-1')")
    conn.execute("INSERT INTO order_packages VALUES ('pkg-1',1,"
                 "'2026-07-11T12:00:00Z','{\"risk_per_unit\": 2.0}')")
    conn.commit()
    conn.close()
    agg = _aggregate(_query(db, since=None), "all", None)
    assert agg["totalTrades"] == 1
    rp = agg["rProvenance"]
    assert rp["confirmedInitial"] == 0, "no direction -> no confirmation, ever"
    assert rp["unverified"] == 1
