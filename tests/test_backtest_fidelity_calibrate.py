"""backtest↔live fidelity calibrator (scripts/research/backtest_fidelity_calibrate.py).

Covers the pure `agreement()` gate (the 5 cases the P0 design promised), the P1
`stratified_agreement()` (uniform-cost-gap vs concentrated-regime-bias separation),
and the read-only DB readers + `--trust-map` over synthetic SQLite fixtures.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))
_spec = importlib.util.spec_from_file_location(
    "backtest_fidelity_calibrate",
    REPO / "scripts" / "research" / "backtest_fidelity_calibrate.py",
)
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)


class TestAgreementGate:
    def test_insufficient_live_below_floor(self):
        r = cal.agreement([1.0, -1.0], [0.1] * 100)
        assert r["verdict"] == "insufficient-live"
        assert r["n_live"] == 2

    def test_calibrated_when_winrate_and_ks_within_tol(self):
        # Both sign distributions (±1); WR 50% vs 52.5% and matching shape → KS tiny.
        live = [1.0] * 20 + [-1.0] * 20          # 50% WR
        bt = [1.0] * 21 + [-1.0] * 19            # 52.5% WR
        r = cal.agreement(live, bt)
        assert r["n_live"] == 40
        assert r["verdict"] == "calibrated"

    def test_drifts_on_winrate_gap(self):
        live = [-1.0] * 40                        # 0% WR
        bt = [1.0] * 40                           # 100% WR
        r = cal.agreement(live, bt)
        assert r["verdict"] == "drifts"
        assert "win-rate" in r["reason"]

    def test_drifts_on_ks_gap(self):
        # equal win-rate (both 50%) but the backtest R shape is far from live's ±1
        # sign proxy → KS trips even though win-rate agrees.
        live = [1.0] * 20 + [-1.0] * 20
        bt = [0.05] * 20 + [-0.05] * 20
        r = cal.agreement(live, bt)
        assert r["ks_realized_r"] is not None and r["ks_realized_r"] > 0.30
        assert r["verdict"] == "drifts"

    def test_empty_backtest_does_not_crash(self):
        # Regression: agreement() used to format a None win-rate gap and crash.
        r = cal.agreement([1.0] * 30, [])
        assert r["backtest_win_rate"] is None
        assert r["verdict"] == "drifts"
        assert "unavailable" in r["reason"]


class TestStratifiedAgreement:
    def _rows(self, n, won_frac, direction, r_val):
        won = int(n * won_frac)
        return [{"r": r_val if i < won else -abs(r_val), "direction": direction,
                 "ts": "2026-03-01T00:00:00Z"} for i in range(n)]

    def test_concentrated_bias_shows_in_one_stratum(self):
        # longs agree (50/50 both), shorts drift (live 0% vs bt 100%). Each stratum
        # must clear the 30-live floor to earn a verdict, so 35 rows/side.
        live = self._rows(35, 0.5, "long", 1.0) + self._rows(35, 0.0, "short", 1.0)
        bt = self._rows(35, 0.5, "long", 1.0) + self._rows(35, 1.0, "short", 1.0)
        out = cal.stratified_agreement(live, bt, key="direction")
        assert out["strata"]["short"]["verdict"] == "drifts"
        assert out["strata"]["long"]["verdict"] == "calibrated"

    def test_year_stratum_uses_ts(self):
        live = [{"r": 1.0, "direction": "long", "ts": "2025-06-01T00:00:00Z"}] * 30
        bt = [{"r": 0.9, "direction": "long", "ts": "2025-07-01T00:00:00Z"}] * 30
        out = cal.stratified_agreement(live, bt, key="year")
        assert "2025" in out["strata"]

    def test_year_of_handles_epoch_ms_and_iso(self):
        assert cal._year_of("2026-08-04T12:00:00Z") == "2026"
        assert cal._year_of("1767225600000") == "2026"  # epoch-ms 2026-01-01
        assert cal._year_of(None) is None
        assert cal._year_of("garbage") is None


def _seed(db, rows, is_backtest):
    # Mirror the REAL recorder schema (ml.datasets.backtest_recorder): the harness
    # ENTRY time lands in `timestamp`; there is NO separate entry_ts column. A test
    # schema with a phantom entry_ts is what hid the trust-map `no such column`
    # crash — the reader must run against the real column set.
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, strategy_name TEXT,"
        " symbol TEXT, direction TEXT, status TEXT, pnl REAL, is_backtest INT,"
        " notes TEXT, timestamp TEXT)"
    )
    for r in rows:
        con.execute(
            "INSERT INTO trades (strategy_name,symbol,direction,status,pnl,is_backtest,"
            "notes,timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (r["strategy_name"], r["symbol"], r.get("direction", "long"),
             r.get("status", "closed"), r["pnl"], is_backtest, r.get("notes"),
             r.get("timestamp", r.get("entry_ts", "2026-01-01T00:00:00Z"))),
        )
    con.commit()
    con.close()


def _permissive_provenance(monkeypatch, predicate=lambda row: True):
    """Force the live reader's provenance filter to `predicate` (the real filter
    correctly rejects synthetic rows for lacking measured-provenance fields)."""
    import src.runtime.provenance as prov
    monkeypatch.setattr(prov, "pnl_is_trustworthy", predicate)


class TestDbReadersAndTrustMap:
    def test_readers_and_trust_map(self, tmp_path, monkeypatch):
        _permissive_provenance(monkeypatch)
        live_db = str(tmp_path / "journal.db")
        bt_db = str(tmp_path / "backtest.db")
        _seed(live_db, [{"strategy_name": "s", "symbol": "BTCUSDT",
                         "pnl": (1.0 if i % 2 else -1.0)} for i in range(40)], 0)
        _seed(bt_db, [{"strategy_name": "s", "symbol": "BTCUSDT",
                       "pnl": 0.3, "entry_ts": "2026-02-01T00:00:00Z"}
                      for _ in range(50)], 1)
        assert len(cal._live_rows(live_db, "s", "BTCUSDT")) == 40
        assert len(cal._backtest_rows(bt_db, "s", "BTCUSDT")) == 50
        assert cal._legs_in(live_db, 0) == {("s", "BTCUSDT")}
        # trust map over the intersection
        legs = cal._legs_in(live_db, 0) & cal._legs_in(bt_db, 1)
        assert legs == {("s", "BTCUSDT")}
        res = cal._calibrate_leg(live_db, bt_db, "s", "BTCUSDT", stratify="direction")
        assert res["n_live"] == 40 and res["n_backtest"] == 50
        assert "stratified" in res

    def test_live_reader_excludes_fabricated(self, tmp_path, monkeypatch):
        # provenance filter that drops rows whose notes says 'fab'.
        _permissive_provenance(
            monkeypatch, lambda row: "fab" not in str(row.get("notes") or ""))
        live_db = str(tmp_path / "j.db")
        _seed(live_db, [{"strategy_name": "s", "symbol": "X", "pnl": 1.0},
                        {"strategy_name": "s", "symbol": "X", "pnl": 1.0,
                         "notes": "fab"}], 0)
        rows = cal._live_rows(live_db, "s", "X")
        assert len(rows) == 1  # the fabricated row excluded
