"""`/api/bot/performance` publishes `bracketOutcome`, AND IT IS ACTUALLY WIRED.

⚠️ WHY THIS FILE EXISTS SEPARATELY FROM `tests/test_bracket_outcome.py`. That
one proves the CLASSIFIER is right. This one proves the ROUTE reaches it — the
`new-table-wiring-guard` lesson, that a green suite over a fixture which cannot
produce the signal proves nothing about wiring. The pre-existing
`test_performance_r_provenance` fixture's `order_packages` has **no `sl`/`tp`
columns at all**, so every row there degrades to `no_bracket_record` and a
`bracketOutcome` block that was computed but never populated would pass it.

It also pins the schema-degradation half in BOTH directions, which is where this
branch's one real defect was: `opk.sl`/`opk.tp` were originally selected under
the *trades*-table guard, so a legacy `order_packages` raised `no such column:
opk.sl` and errored the WHOLE endpoint — every metric, every window blanked to
buy one derived figure.

MEASURED against the live journal 2026-09-06 (5518 trades + 4435 order_packages
via /api/bot/db/table/*): real money n=424, 420 gradeable, 228 (54.3%) reached a
declared bracket — 187 sl vs 41 tp.
"""
from __future__ import annotations

import sqlite3

from src.web.api.routers.performance import _aggregate, _empty, _query

_FULL_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY, account_id TEXT, strategy_name TEXT, symbol TEXT,
    direction TEXT, entry_price REAL, stop_loss REAL, take_profit_1 REAL,
    exit_price REAL, position_size REAL, pnl REAL, status TEXT,
    is_backtest INTEGER DEFAULT 0, is_demo INTEGER DEFAULT 0,
    account_class TEXT, setup_type TEXT, reconcile_status TEXT,
    exit_reason TEXT, closed_at TEXT, timestamp TEXT, notes TEXT,
    order_package_id TEXT
);
CREATE TABLE order_packages (
    order_package_id TEXT PRIMARY KEY, linked_trade_id INTEGER,
    updated_at TEXT, meta TEXT, sl REAL, tp REAL
);
"""

# `order_packages` WITHOUT the bracket columns — the legacy shape the whole
# optional-column guard exists for, and the one the sibling suite uses.
_LEGACY_SCHEMA = _FULL_SCHEMA.replace(", sl REAL, tp REAL", "")


def _mk(tmp_path, rows, *, schema=_FULL_SCHEMA):
    """rows = [(direction, entry, exit_price, pkg_sl, pkg_tp, setup_type, notes)]"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(schema)
    has_bracket = "sl REAL" in schema
    for i, (direction, entry, exit_px, sl, tp, setup, notes) in enumerate(rows, start=1):
        opid = f"pkg-{i}"
        conn.execute(
            "INSERT INTO trades (id, account_id, strategy_name, symbol, direction,"
            " entry_price, stop_loss, take_profit_1, exit_price, position_size,"
            " pnl, status, is_backtest, is_demo, account_class, setup_type,"
            " exit_reason, closed_at, timestamp, notes, order_package_id)"
            " VALUES (?,?, 'a', 'BTCUSDT', ?, ?, ?, ?, ?, 1.0, 5.0, 'closed',"
            " 0, 0, 'real_money', ?, 'reconciler_filled', ?, ?, ?, ?)",
            [i, "bybit_2", direction, entry, sl, tp, exit_px, setup,
             f"2026-07-{10 + i:02d}T12:00:00Z", f"2026-07-{10 + i:02d}T11:00:00Z",
             notes or "{}", opid])
        if has_bracket:
            conn.execute(
                "INSERT INTO order_packages (order_package_id, linked_trade_id,"
                " updated_at, meta, sl, tp) VALUES (?,?,?,NULL,?,?)",
                [opid, i, f"2026-07-{10 + i:02d}T12:00:00Z", sl, tp])
        else:
            conn.execute(
                "INSERT INTO order_packages (order_package_id, linked_trade_id,"
                " updated_at, meta) VALUES (?,?,?,NULL)",
                [opid, i, f"2026-07-{10 + i:02d}T12:00:00Z"])
    conn.commit()
    conn.close()
    return db


def _agg(tmp_path, rows, **kw):
    db = _mk(tmp_path, rows, **kw)
    return _aggregate(_query(db, since=None), "all", None)


def test_the_route_actually_REACHES_the_classifier_and_populates_it(tmp_path):
    """The wiring assertion. A `bracketOutcome` block of all-zeros would pass a
    key-presence test; this requires the route to have produced real verdicts
    from the joined `order_packages.sl`/`.tp`."""
    agg = _agg(tmp_path / "a", [
        ("long",  100.0,  94.0,  95.0, 110.0, None, None),   # through the stop
        ("long",  100.0, 112.0,  95.0, 110.0, None, None),   # through the target
        ("long",  100.0, 103.0,  95.0, 110.0, None, None),   # mid-bracket
        ("short", 100.0, 106.0, 105.0,  90.0, None, None),   # short, stop
    ])
    bo = agg["bracketOutcome"]
    assert (bo["reachedSl"], bo["reachedTp"], bo["midBracket"]) == (2, 1, 1)
    assert bo["gradeable"] == 4
    assert bo["reachedRatio"] == 0.75
    # The states partition the window — checkable by arithmetic, not trusted.
    assert sum(v for k, v in bo.items()
               if k not in ("gradeable", "reachedRatio")) == agg["totalTrades"]


def test_the_denominator_is_GRADEABLE_and_not_totalTrades(tmp_path):
    """Folding *we could not look* into the denominator would publish a rate
    over a population it does not describe."""
    agg = _agg(tmp_path / "b", [
        ("long", 100.0,  94.0, 95.0, 110.0, None, None),          # reached_sl
        ("long", 100.0,  None, 95.0, 110.0, None, None),          # no_exit_price
        ("long", 100.0,  94.0, 95.0, 110.0, "intent_reduce", None),  # excluded
    ])
    bo = agg["bracketOutcome"]
    assert agg["totalTrades"] == 3
    assert bo["gradeable"] == 1
    assert bo["reachedRatio"] == 1.0, "1 of 1 GRADEABLE, not 1 of 3 rows"
    assert bo["noExitPrice"] == 1 and bo["excludedReduceLeg"] == 1


def test_a_fabricated_exit_price_is_refused_through_the_real_query(tmp_path):
    """The refusal must survive the notes round-trip through SQLite, not just
    the pure function."""
    agg = _agg(tmp_path / "c", [
        ("long", 100.0, 94.0, 95.0, 110.0, None,
         '{"exit_price_source": "local_markprice"}'),
    ])
    bo = agg["bracketOutcome"]
    assert bo["priceNotMeasurable"] == 1
    assert bo["gradeable"] == 0
    assert bo["reachedRatio"] is None, "null, never 0.0 — nothing was graded"


def test_a_legacy_order_packages_DEGRADES_and_does_not_error_the_endpoint(tmp_path):
    """THE REGRESSION THIS BRANCH ACTUALLY HIT. Selecting `opk.sl` without its
    own schema guard raised `no such column: opk.sl`, which the caller turns
    into a ZEROED envelope — every metric for every window blanked to buy one
    derived figure. The other metrics must survive intact."""
    agg = _agg(tmp_path / "d", [("long", 100.0, 94.0, 95.0, 110.0, None, None)],
               schema=_LEGACY_SCHEMA)
    assert agg["totalTrades"] == 1, "the endpoint still served"
    assert not agg.get("error"), "no error envelope -- the endpoint did not blank"
    bo = agg["bracketOutcome"]
    assert bo["noBracketRecord"] == 1, "we could not look — not 'it missed'"
    assert bo["gradeable"] == 0 and bo["reachedRatio"] is None


def test_the_key_is_present_with_explicit_zeros_on_the_empty_envelope():
    """A key that disappears makes a consumer branch on absence, and absence is
    not one of the states."""
    bo = _empty("all", None)["bracketOutcome"]
    assert set(bo) == {
        "reachedSl", "reachedTp", "midBracket", "gradeable", "reachedRatio",
        "noExitPrice", "priceNotMeasurable", "noBracketRecord",
        "directionUnreadable", "excludedReduceLeg",
    }
    assert bo["reachedRatio"] is None, "null, never 0.0 — no window was read"
    assert all(v == 0 for k, v in bo.items() if k != "reachedRatio")
