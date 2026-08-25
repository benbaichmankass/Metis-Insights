"""Tests for the M31 P2 position-telemetry record.

What is worth pinning is what would let this record a confident wrong answer:
a missing MFE fabricated as 0.0, a peak that walks BACKWARDS (MFE is one-way),
`rr_from_here` computed from an already-crossed level, the cap constant
drifting from production, and — the constraint the whole design rests on — the
peak coming from a SECOND definition instead of the lever's own.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from src.runtime import position_telemetry as pt
from src.runtime.trail_decay import (PEAK_MEASURED, PEAK_NO_RISK,
                                     PEAK_THIN_WINDOW, PEAK_UNANCHORED)

REPO = Path(__file__).resolve().parents[1]

# The live XRP short, as measured: entry 1.0806, risk 0.02726786 (2.5233%),
# TP 0.9736206 (= the 9.9% cap), stop ratcheted to 1.04193571, price ~1.0021.
XRP = dict(entry=1.0806, risk=0.02726786, tp=0.9736206,
           stop=1.04193571, price=1.002072)


def _window(lows, highs=None):
    highs = highs or [x * 1.001 for x in lows]
    return pd.DataFrame({"high": highs, "low": lows, "close": lows})


def _pkg(**over):
    p = {"order_package_id": "pkg-1", "symbol": "XRPUSDT", "entry": XRP["entry"],
         "tp": XRP["tp"], "account_id": "bybit_2", "strategy_name": "xrp_pullback_2h"}
    p.update(over)
    return p


def _meta(**over):
    m = {"risk_per_unit": XRP["risk"], "entry_time": "2026-07-29 12:16:00"}
    m.update(over)
    return m


class TestTheMotivatingTrade:
    """The record must reproduce the numbers that took a hand investigation."""

    def _rec(self):
        return pt.build_record(
            open_pkg=_pkg(), meta=_meta(),
            window=_window([1.06, 1.02, 0.992, 1.0021]),
            direction="short", current_price=XRP["price"], stop=XRP["stop"],
            target=XRP["tp"], strategy="xrp_pullback_2h")

    def test_cap_r_is_the_ceiling_the_tp_clamp_imposes(self):
        assert abs(pt.cap_r(XRP["entry"], XRP["risk"]) - 3.923) < 0.01

    def test_open_r_and_pct_of_cap(self):
        r = self._rec()
        assert abs(r["open_r"] - 2.880) < 0.01
        assert abs(r["pct_of_cap"] - 73.4) < 0.5

    def test_rr_from_here_is_the_number_that_answers_the_question(self):
        r = self._rec()
        # 1.04R of upside against 1.46R of give-back => holding is < 1:1
        assert abs(r["r_to_target"] - 1.043) < 0.01
        assert abs(r["r_to_stop"] - 1.462) < 0.01
        assert abs(r["rr_from_here"] - 0.713) < 0.01
        assert r["rr_from_here"] < 1.0

    def test_peak_is_recorded_and_stamped_estimated(self):
        r = self._rec()
        assert r["peak_state"] == PEAK_MEASURED
        assert r["peak_r"] > r["open_r"]          # the low went past the mark
        assert r["giveback_r"] > 0
        assert r["peak_provenance"] == "estimated"


class TestAbsentMfeIsNeverZero:
    def test_unanchored_window_reports_why_not_a_flat_trade(self):
        r = pt.build_record(open_pkg=_pkg(), meta=_meta(entry_time=None),
                            window=_window([1.06, 1.0]), direction="short",
                            current_price=1.0, stop=XRP["stop"], target=XRP["tp"])
        assert r["peak_state"] == PEAK_UNANCHORED
        assert r["peak_r"] is None and r["giveback_r"] is None

    def test_thin_window_is_distinct_from_unanchored(self):
        r = pt.build_record(open_pkg=_pkg(), meta=_meta(),
                            window=_window([1.06]), direction="short",
                            current_price=1.0, stop=XRP["stop"], target=XRP["tp"])
        assert r["peak_state"] == PEAK_THIN_WINDOW

    def test_missing_risk_is_no_risk_not_a_zero_r_trade(self):
        r = pt.build_record(open_pkg=_pkg(), meta=_meta(risk_per_unit=None),
                            window=_window([1.06, 1.0]), direction="short",
                            current_price=1.0, stop=XRP["stop"], target=XRP["tp"])
        assert r["peak_state"] == PEAK_NO_RISK
        assert r["open_r"] is None and r["cap_r"] is None

    def test_the_four_peak_states_are_distinguishable(self):
        got = set()
        for meta, win in ((_meta(), _window([1.06, 0.99])),
                          (_meta(entry_time=None), _window([1.06, 0.99])),
                          (_meta(), _window([1.06])),
                          (_meta(risk_per_unit=0), _window([1.06, 0.99]))):
            got.add(pt.build_record(open_pkg=_pkg(), meta=meta, window=win,
                                    direction="short", current_price=1.0)["peak_state"])
        assert got == {PEAK_MEASURED, PEAK_UNANCHORED, PEAK_THIN_WINDOW,
                       PEAK_NO_RISK}


class TestRrFromHereRefusesNonsense:
    def test_no_ratio_once_the_stop_is_already_crossed(self):
        """A stop the wrong side of price makes the ratio a sign artefact."""
        r = pt.build_record(open_pkg=_pkg(), meta=_meta(),
                            window=_window([1.06, 1.05]), direction="short",
                            current_price=1.05, stop=1.00, target=XRP["tp"])
        assert r["r_to_stop"] < 0
        assert r["rr_from_here"] is None

    def test_no_ratio_without_a_target(self):
        r = pt.build_record(open_pkg=_pkg(tp=None), meta=_meta(),
                            window=_window([1.06, 1.0]), direction="short",
                            current_price=1.0, stop=XRP["stop"], target=None)
        assert r["rr_from_here"] is None and r["r_to_stop"] is not None


class TestIdentity:
    def test_a_row_we_cannot_attribute_is_not_written(self):
        assert pt.build_record(open_pkg=_pkg(order_package_id=None),
                               meta=_meta(), window=_window([1.0, 0.9]),
                               direction="short", current_price=0.9) is None


class TestPersistence:
    def _db(self, tmp):
        path = str(Path(tmp) / "j.db")
        with sqlite3.connect(path) as c:
            c.execute(f"""CREATE TABLE {pt.TABLE} (
                order_package_id TEXT PRIMARY KEY, trade_id TEXT, strategy TEXT,
                symbol TEXT, account_id TEXT, direction TEXT, entry REAL,
                risk_per_unit REAL, last_price REAL, open_r REAL, peak_r REAL,
                peak_state TEXT, giveback_r REAL, bars_held INTEGER,
                bars_since_peak INTEGER, cap_r REAL, pct_of_cap REAL,
                r_to_stop REAL, r_to_target REAL, rr_from_here REAL,
                peak_provenance TEXT, levers TEXT, updated_at TEXT)""")
        return path

    def test_peak_r_never_walks_backwards(self):
        """MFE is one-way. A later pass on a shorter window (a re-fetch, a
        restart) must not be able to lower a recorded peak."""
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            deep = pt.build_record(open_pkg=_pkg(), meta=_meta(),
                                   window=_window([1.06, 0.95]),
                                   direction="short", current_price=1.0)
            shallow = pt.build_record(open_pkg=_pkg(), meta=_meta(),
                                      window=_window([1.06, 1.05]),
                                      direction="short", current_price=1.0)
            assert deep["peak_r"] > shallow["peak_r"]
            assert pt.write_record(deep, db_path=db)
            assert pt.write_record(shallow, db_path=db)
            with sqlite3.connect(db) as c:
                got = c.execute(
                    f"SELECT peak_r, last_price FROM {pt.TABLE}").fetchone()
            assert abs(got[0] - deep["peak_r"]) < 1e-9, "peak regressed"

    def test_two_unmeasurable_passes_leave_peak_r_NULL_not_the_sentinel(self):
        """THE PRODUCER REGRESSION
        (BL-20260818-TELEMETRY-PEAK-R-STORES-COALESCE-SENTINEL).

        The -1e18 in the upsert is a SORT KEY: it exists so a NULL LOSES to any
        real number and a peak can never walk backwards. But when BOTH sides are
        NULL — a row whose peak_state is `thin_window`, i.e. never measurable —
        MAX has nothing to compare and returns the sentinel, which is then
        persisted as though it had been observed.

        The write path matters: the first pass INSERTs (and correctly stores
        NULL); only the second pass takes the UPDATE branch that manufactures
        it. So a single write cannot reproduce this and the test needs two.

        Measured live 2026-08-25 on 60 rows: 11 carried -1e+18, served through
        peak_pct_of_cap as values down to -8.2e+19.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            thin = pt.build_record(open_pkg=_pkg(), meta=_meta(),
                                   window=_window([1.0]),
                                   direction="short", current_price=1.0)
            assert thin["peak_state"] == pt.PEAK_THIN_WINDOW, thin
            assert thin["peak_r"] is None, thin
            assert pt.write_record(thin, db_path=db)      # INSERT
            assert pt.write_record(thin, db_path=db)      # UPDATE — the branch
            with sqlite3.connect(db) as c:
                got = c.execute(
                    f"SELECT peak_r, peak_state FROM {pt.TABLE}").fetchone()
            assert got[1] == pt.PEAK_THIN_WINDOW
            assert got[0] is None, (
                f"stored {got[0]!r} — the sort sentinel was persisted as a "
                "measurement")

    def test_a_real_peak_still_survives_an_unmeasurable_later_pass(self):
        """The control the fix must not break: a NULL arriving later must still
        LOSE to an established peak. If the CASE were written as a plain
        `COALESCE(excluded.peak_r, table.peak_r)` this would pass too — but
        `test_peak_r_never_walks_backwards` is what pins the ordering, and this
        pins that the NULL-vs-real case is still handled after the change."""
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            deep = pt.build_record(open_pkg=_pkg(), meta=_meta(),
                                   window=_window([1.06, 0.95]),
                                   direction="short", current_price=1.0)
            thin = pt.build_record(open_pkg=_pkg(), meta=_meta(),
                                   window=_window([1.0]),
                                   direction="short", current_price=1.0)
            assert deep["peak_r"] is not None and thin["peak_r"] is None
            assert pt.write_record(deep, db_path=db)
            assert pt.write_record(thin, db_path=db)
            with sqlite3.connect(db) as c:
                got = c.execute(f"SELECT peak_r FROM {pt.TABLE}").fetchone()
            assert abs(got[0] - deep["peak_r"]) < 1e-9, (
                "an unmeasurable pass wiped an established peak")

    def test_a_write_failure_is_survivable(self):
        assert pt.write_record({"order_package_id": "x"},
                               db_path="/nonexistent/dir/j.db") is False

    def test_upsert_is_one_row_per_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            for _ in range(3):
                pt.write_record(pt.build_record(
                    open_pkg=_pkg(), meta=_meta(), window=_window([1.06, 1.0]),
                    direction="short", current_price=1.0), db_path=db)
            with sqlite3.connect(db) as c:
                assert c.execute(f"SELECT COUNT(*) FROM {pt.TABLE}").fetchone()[0] == 1


class TestOneDefinition:
    def test_the_peak_comes_from_the_levers_own_function(self):
        """The design's first constraint, asserted rather than trusted: if the
        peak were re-derived here, patching the lever's function would not
        change the record."""
        called = {}
        real = pt.since_entry_peak

        def spy(*a, **k):
            called["yes"] = True
            return real(*a, **k)

        pt.since_entry_peak = spy
        try:
            pt.build_record(open_pkg=_pkg(), meta=_meta(),
                            window=_window([1.06, 0.99]), direction="short",
                            current_price=1.0)
        finally:
            pt.since_entry_peak = real
        assert called.get("yes"), "build_record did not use since_entry_peak"

    def test_cap_constant_IS_the_owner_not_a_copy(self):
        """Identity, replacing a source-regex comparison of literals."""
        from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT
        from src.units.strategies.trend_donchian import (
            _TP_SENTINEL_CAP_PCT as donchian_cap)
        assert pt._TP_SENTINEL_CAP_PCT is TP_VENUE_CAP_PCT
        assert donchian_cap is TP_VENUE_CAP_PCT


class TestWiredIntoBothFamilies:
    @pytest.mark.parametrize("mod", ["trend_donchian.py",
                                     "htf_pullback_trend_2h.py"])
    def test_monitor_calls_the_recorder(self, mod):
        src = (REPO / "src" / "units" / "strategies" / mod).read_text()
        assert "record_position_telemetry(" in src, (
            f"{mod} does not write telemetry — the record would be empty for "
            f"that family and its absence would look like 'no open trades'")


class TestVocabularyIsPinned:
    def test_constants_equal_the_literals_the_producer_emits(self):
        """`since_entry_peak` assigns literals so the producer visibly emits
        every state; these constants are what consumers import. If the two ever
        diverge, a consumer's `== PEAK_MEASURED` silently stops matching."""
        src = (REPO / "src" / "runtime" / "trail_decay.py").read_text()
        for const, literal in ((PEAK_MEASURED, "measured"),
                               (PEAK_UNANCHORED, "unanchored"),
                               (PEAK_THIN_WINDOW, "thin_window"),
                               (PEAK_NO_RISK, "no_risk")):
            assert const == literal
            assert f'"peak_state": "{literal}"' in src or \
                   f'out["peak_state"] = "{literal}"' in src, (
                       f"producer does not emit the literal {literal!r}")


class TestAccountIdIsResolvedNotAccepted:
    """`account_id` must come from `trades`, because nothing else can supply it.

    Measured on the live VM 2026-08-16 — the FIRST post-deploy read of this
    table — all 12 rows had `account_id: null`, and no row could ever have had
    anything else: `order_packages` has no `account_id` column and
    `monitor(cfg, candles_df, open_pkg)` has no account in scope to pass one.
    A column that is structurally unpopulatable reads, to a consumer, exactly
    like "this position has no account".
    """

    def _db(self, tmp):
        path = str(Path(tmp) / "j.db")
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT)")
            c.execute("INSERT INTO trades (id, account_id) VALUES (4574, 'bybit_1')")
            c.execute(f"""CREATE TABLE {pt.TABLE} (
                order_package_id TEXT PRIMARY KEY, trade_id TEXT, strategy TEXT,
                symbol TEXT, account_id TEXT, direction TEXT, entry REAL,
                risk_per_unit REAL, last_price REAL, open_r REAL, peak_r REAL,
                peak_state TEXT, giveback_r REAL, bars_held INTEGER,
                bars_since_peak INTEGER, cap_r REAL, pct_of_cap REAL,
                r_to_stop REAL, r_to_target REAL, rr_from_here REAL,
                peak_provenance TEXT, levers TEXT, updated_at TEXT)""")
        return path

    def _row(self, **over):
        r = {k: None for k in (
            "order_package_id trade_id strategy symbol account_id direction entry "
            "risk_per_unit last_price open_r peak_r peak_state giveback_r bars_held "
            "bars_since_peak cap_r pct_of_cap r_to_stop r_to_target rr_from_here "
            "peak_provenance levers updated_at").split()}
        r.update(order_package_id="pkg-1", trade_id="4574", updated_at="t0")
        r.update(over)
        return r

    def _account(self, db, pkg="pkg-1"):
        with sqlite3.connect(db) as c:
            return c.execute(
                f"SELECT account_id FROM {pt.TABLE} WHERE order_package_id=?",
                (pkg,)).fetchone()[0]

    def test_insert_resolves_the_account_from_trades(self):
        """`trades.id` is INTEGER and `trade_id` is stored TEXT — without the
        CAST this join silently matches nothing and the column stays NULL."""
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            pt.write_record(self._row(), db_path=db)
            assert self._account(db) == "bybit_1"

    def test_update_backfills_a_row_already_written_as_null(self):
        """Every row written before this fix carries NULL, and the upsert path
        is the only one a live open position takes after its first pass."""
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            pt.write_record(self._row(), db_path=db)
            with sqlite3.connect(db) as c:
                c.execute(f"UPDATE {pt.TABLE} SET account_id=NULL")
            pt.write_record(self._row(updated_at="t1"), db_path=db)
            assert self._account(db) == "bybit_1"

    def test_an_unknown_trade_stays_null_rather_than_fabricating(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            pt.write_record(
                self._row(order_package_id="pkg-2", trade_id="99999"), db_path=db)
            assert self._account(db, "pkg-2") is None

    def test_a_resolved_account_is_never_overwritten_by_a_null(self):
        """COALESCE order is load-bearing: a later pass whose lookup misses
        must not wipe an account we already established."""
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            pt.write_record(self._row(), db_path=db)
            with sqlite3.connect(db) as c:      # the trade row disappears
                c.execute("DELETE FROM trades")
            pt.write_record(self._row(updated_at="t2"), db_path=db)
            assert self._account(db) == "bybit_1"

    def test_an_explicit_caller_value_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            pt.write_record(
                self._row(order_package_id="pkg-3", account_id="explicit"), db_path=db)
            assert self._account(db, "pkg-3") == "explicit"
