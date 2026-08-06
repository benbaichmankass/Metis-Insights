"""backtest↔live fidelity calibrator (scripts/research/backtest_fidelity_calibrate.py).

Covers the pure `agreement()` gate (the 5 cases the P0 design promised), the P1
`stratified_agreement()` (uniform-cost-gap vs concentrated-regime-bias separation),
and the read-only DB readers + `--trust-map` over synthetic SQLite fixtures.
"""
from __future__ import annotations

import importlib.util
import sqlite3

import pytest
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

    def test_drifts_on_MAGNITUDE_when_winrate_and_ks_both_pass(self):
        """The 2026-08-06 hole, pinned. htf_pullback_trend_2h/BTC cleared the
        win-rate gap (0.123) AND KS(R) (0.213) and read `calibrated` while its
        live mean-R was -3.41 vs a backtest -0.04. Shape and frequency can both
        agree while magnitude is off by two orders — if this ever passes again,
        the third axis regressed."""
        # KS only counts the CDF FRACTION a row occupies, so one extreme row in
        # 30 moves KS by ~1/30 while moving the mean by 3.3 — the exact geometry
        # that let the real leg through. Both prior axes pass; magnitude fails.
        live = [0.0] * 29 + [-99.5]
        bt = [0.0] * 100
        r = cal.agreement(live, bt)
        assert r["win_rate_diff"] == 0.0                  # frequency: identical
        assert r["ks_realized_r"] <= cal.MAX_KS           # shape: PASSES
        assert r["mean_r_gap"] > cal.MAX_MEAN_R_GAP       # magnitude: fails
        assert r["verdict"] == "drifts"
        assert "mean-R gap" in r["reason"]

    def test_mean_r_gap_is_reported_and_thresholded(self):
        r = cal.agreement([1.0] * 40, [1.0] * 40)
        assert r["mean_r_gap"] == 0.0
        assert r["thresholds"]["max_mean_r_gap"] == cal.MAX_MEAN_R_GAP

    def test_outlier_dominated_mean_says_so_in_the_reason(self):
        """A magnitude failure driven by ONE row must say so — otherwise the
        reader re-derives it by dumping rows, which is what happened."""
        live = [-99.5] + [0.0] * 39               # one poisoned row
        bt = [0.0] * 40
        r = cal.agreement(live, bt)
        assert r["verdict"] == "drifts"
        assert r["live_mean_r_outlier_share"] >= cal.OUTLIER_DOMINANCE_FLAG
        assert "ONE row" in r["reason"]

    def test_outlier_share_is_NOT_a_gate(self):
        """Reported, never gated. Forgiving a magnitude failure because it is
        concentrated would silently wave through a poisoned sample."""
        live = [-99.5] + [0.0] * 39
        r = cal.agreement(live, [0.0] * 40)
        assert r["verdict"] == "drifts"   # still fails despite the flag

    def test_outlier_share_none_below_three_samples(self):
        assert cal._mean_outlier_share([1.0, 2.0]) is None
        assert cal._mean_outlier_share([]) is None

    def test_outlier_share_zero_when_all_identical(self):
        assert cal._mean_outlier_share([2.0, 2.0, 2.0]) == 0.0

    def test_outlier_share_CEILING_is_one_half(self):
        """Deviations above and below a mean sum to the same total, so a lone
        value on its side contributes exactly half and can NEVER exceed it.
        This is why OUTLIER_DOMINANCE_FLAG must sit below 0.5 — a threshold at
        or above it would be unreachable, i.e. a silently dead flag."""
        for n in (3, 10, 40, 200):
            share = cal._mean_outlier_share([-99.5] + [0.0] * (n - 1))
            assert share == pytest.approx(0.5), f"n={n}"
        assert cal.OUTLIER_DOMINANCE_FLAG < 0.5

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
        " notes TEXT, timestamp TEXT, entry_price REAL, stop_loss REAL,"
        " position_size REAL)"
    )
    for r in rows:
        con.execute(
            "INSERT INTO trades (strategy_name,symbol,direction,status,pnl,is_backtest,"
            "notes,timestamp,entry_price,stop_loss,position_size)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r["strategy_name"], r["symbol"], r.get("direction", "long"),
             r.get("status", "closed"), r["pnl"], is_backtest, r.get("notes"),
             r.get("timestamp", r.get("entry_ts", "2026-01-01T00:00:00Z")),
             r.get("entry_price"), r.get("stop_loss"), r.get("position_size")),
        )
    con.commit()
    con.close()


def _seed_legacy_schema(db, rows):
    """Seed a trades table with NO entry_price/stop_loss/position_size columns —
    the minimal/legacy schema. Exercises the PRAGMA guard in `_live_rows`."""
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, strategy_name TEXT,"
        " symbol TEXT, direction TEXT, status TEXT, pnl REAL, is_backtest INT,"
        " notes TEXT, timestamp TEXT)"
    )
    for r in rows:
        con.execute(
            "INSERT INTO trades (strategy_name,symbol,direction,status,pnl,is_backtest,"
            "notes,timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (r["strategy_name"], r["symbol"], "long", "closed", r["pnl"], 0,
             r.get("notes"), "2026-01-01T00:00:00Z"),
        )
    con.commit()
    con.close()


#: A row whose risk is 10 USD per unit × 1 unit (crypto contract_value 1.0), so
#: `r_multiple` is simply pnl / 10.
def _risky(pnl, **kw):
    return {"strategy_name": "s", "symbol": "BTCUSDT", "pnl": pnl,
            "entry_price": 100.0, "stop_loss": 90.0, "position_size": 1.0, **kw}


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
        _seed(live_db, [_risky(1.0 if i % 2 else -1.0) for i in range(40)], 0)
        _seed(bt_db, [{"strategy_name": "s", "symbol": "BTCUSDT",
                       "pnl": 0.3, "entry_ts": "2026-02-01T00:00:00Z"}
                      for _ in range(50)], 1)
        assert len(cal._live_rows(live_db, "s", "BTCUSDT")[0]) == 40
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
        _seed(live_db, [_risky(1.0, symbol="X"),
                        _risky(1.0, symbol="X", notes="fab")], 0)
        rows, diag = cal._live_rows(live_db, "s", "X")
        assert len(rows) == 1  # the fabricated row excluded
        # Excluded by PROVENANCE, so it never reaches the R stage: it is not in
        # rows_trusted, and coverage is 1.0 over the trusted row. A fabricated
        # row must not depress r_coverage — that would read as an R-measurement
        # problem when it is a trust problem.
        assert diag["rows_scanned"] == 2
        assert diag["rows_trusted"] == 1
        assert diag["r_coverage"] == 1.0


class TestStopDistanceR:
    """P1.x — the real live-R axis, and the guarantees that keep it honest."""

    def test_stop_distance_r_is_pnl_over_risk_not_the_sign(self, tmp_path, monkeypatch):
        _permissive_provenance(monkeypatch)
        db = str(tmp_path / "j.db")
        # risk = |100-90| * 1 * 1.0 = 10 USD  →  R = pnl / 10
        _seed(db, [_risky(25.0), _risky(-5.0)], 0)
        rows, diag = cal._live_rows(db, "s", "BTCUSDT")
        assert sorted(r["r"] for r in rows) == [-0.5, 2.5]
        assert diag["r_basis"] == "stop_distance"
        assert diag["r_coverage"] == 1.0

    def test_sign_proxy_is_opt_in_and_collapses_to_plus_minus_one(
            self, tmp_path, monkeypatch):
        _permissive_provenance(monkeypatch)
        db = str(tmp_path / "j.db")
        _seed(db, [_risky(25.0), _risky(-5.0)], 0)
        rows, diag = cal._live_rows(db, "s", "BTCUSDT", r_basis="sign_proxy")
        assert sorted(r["r"] for r in rows) == [-1.0, 1.0]
        assert diag["r_basis"] == "sign_proxy"

    def test_default_basis_is_stop_distance(self):
        assert cal.DEFAULT_R_BASIS == "stop_distance"

    def test_unknown_basis_raises_rather_than_defaulting(self, tmp_path):
        with pytest.raises(ValueError):
            cal._live_rows(str(tmp_path / "j.db"), "s", "X", r_basis="whatever")

    def test_unmeasurable_row_is_EXCLUDED_never_proxied(self, tmp_path, monkeypatch):
        """The core P1.x guarantee: a row with no usable stop drops OUT of the R
        sample. Back-filling it with ±1 would rebuild the point-mass artifact
        inside a sample labelled 'stop_distance'."""
        _permissive_provenance(monkeypatch)
        db = str(tmp_path / "j.db")
        _seed(db, [
            _risky(25.0),                                    # measurable
            _risky(30.0, stop_loss=None),                    # no stop
            _risky(30.0, stop_loss=100.0),                   # flat stop → risk 0
            _risky(30.0, position_size=None),                # no size
        ], 0)
        rows, diag = cal._live_rows(db, "s", "BTCUSDT")
        assert [r["r"] for r in rows] == [2.5]
        assert 1.0 not in [r["r"] for r in rows]  # no sign-proxy substitution
        assert diag["rows_trusted"] == 4 and diag["rows_r_measured"] == 1
        assert diag["r_coverage"] == 0.25

    def test_legacy_schema_degrades_to_zero_coverage_not_a_crash(
            self, tmp_path, monkeypatch):
        _permissive_provenance(monkeypatch)
        db = str(tmp_path / "legacy.db")
        _seed_legacy_schema(db, [{"strategy_name": "s", "symbol": "X", "pnl": 1.0}])
        rows, diag = cal._live_rows(db, "s", "X")
        assert rows == []
        assert diag["rows_trusted"] == 1 and diag["r_coverage"] == 0.0

    def test_no_trusted_rows_gives_coverage_None_not_zero(self, tmp_path, monkeypatch):
        """None vs 0.0 is load-bearing: 'no trusted live trade' and 'trusted
        trades exist but none was R-measurable' are different findings."""
        _permissive_provenance(monkeypatch, lambda row: False)
        db = str(tmp_path / "j.db")
        _seed(db, [_risky(1.0)], 0)
        _rows, diag = cal._live_rows(db, "s", "BTCUSDT")
        assert diag["rows_trusted"] == 0
        assert diag["r_coverage"] is None

    def test_unmeasurable_leg_is_not_reported_as_untraded(self, tmp_path, monkeypatch):
        """sub-class C: an empty R sample must not read as a clean 'no live
        trades' when trusted trades demonstrably exist."""
        _permissive_provenance(monkeypatch)
        live_db, bt_db = str(tmp_path / "j.db"), str(tmp_path / "b.db")
        _seed(live_db, [_risky(5.0, stop_loss=None) for _ in range(40)], 0)
        _seed(bt_db, [{"strategy_name": "s", "symbol": "BTCUSDT", "pnl": 0.3}
                      for _ in range(50)], 1)
        res = cal._calibrate_leg(live_db, bt_db, "s", "BTCUSDT")
        assert res["verdict"] == "insufficient-live"
        assert "NONE was R-measurable" in res["reason"]
        assert "not untraded" in res["reason"]
        assert res["live_r"]["rows_trusted"] == 40

    def test_leg_result_declares_which_axis_it_computed(self, tmp_path, monkeypatch):
        _permissive_provenance(monkeypatch)
        live_db, bt_db = str(tmp_path / "j.db"), str(tmp_path / "b.db")
        _seed(live_db, [_risky(5.0) for _ in range(40)], 0)
        _seed(bt_db, [{"strategy_name": "s", "symbol": "BTCUSDT", "pnl": 0.3}
                      for _ in range(50)], 1)
        for basis in cal.R_BASES:
            res = cal._calibrate_leg(live_db, bt_db, "s", "BTCUSDT", r_basis=basis)
            assert res["live_r"]["r_basis"] == basis

    def test_sign_proxy_KS_is_the_artifact_real_R_removes(self, tmp_path, monkeypatch):
        """The § 5b finding, pinned. Against the SAME continuous backtest sample,
        the ±1 proxy forces a large KS while the real R — here drawn to match the
        backtest — does not. If this ever inverts, the axis fix regressed."""
        _permissive_provenance(monkeypatch)
        live_db, bt_db = str(tmp_path / "j.db"), str(tmp_path / "b.db")
        # Live R spread across 0.2..0.4, mirroring the backtest's 0.3 cluster.
        _seed(live_db, [_risky(2.0 + (i % 3)) for i in range(40)], 0)
        _seed(bt_db, [{"strategy_name": "s", "symbol": "BTCUSDT",
                       "pnl": 0.2 + 0.1 * (i % 3)} for i in range(60)], 1)
        real = cal._calibrate_leg(live_db, bt_db, "s", "BTCUSDT")
        proxy = cal._calibrate_leg(live_db, bt_db, "s", "BTCUSDT",
                                   r_basis="sign_proxy")
        assert proxy["ks_realized_r"] > real["ks_realized_r"]
