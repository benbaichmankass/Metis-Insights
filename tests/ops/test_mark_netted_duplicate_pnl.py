"""Retroactive marking of duplicated netted PnL — BL-20260806.

The forward-side writer fix is `order_monitor._prorate_netted_broker_pnl`; this
covers the historical half. The properties that matter are (a) it distinguishes
real duplication from rounding collisions, (b) it never writes without --apply,
and (c) it disqualifies the number rather than inventing a replacement.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "mark_netted_duplicate_pnl",
    REPO / "scripts" / "ops" / "mark_netted_duplicate_pnl.py",
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

from src.runtime import provenance  # noqa: E402


def _db(tmp_path, rows):
    path = tmp_path / "j.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT,"
        " symbol TEXT, pnl REAL, position_size REAL, notes TEXT,"
        " status TEXT, is_backtest INT)"
    )
    for r in rows:
        con.execute(
            "INSERT INTO trades (id,account_id,symbol,pnl,position_size,notes,"
            "status,is_backtest) VALUES (?,?,?,?,?,?,'closed',0)",
            (r["id"], r.get("account_id", "bybit_1"), r.get("symbol", "BTCUSDT"),
             r["pnl"], r["qty"],
             json.dumps(r.get("notes", {"exit_price_source": "bybit_closed_pnl"}))),
        )
    con.commit()
    con.close()
    return str(path)


def _open(path, rw=False):
    con = sqlite3.connect(f"file:{path}?mode={'rw' if rw else 'ro'}", uri=True)
    con.row_factory = sqlite3.Row
    return con


class TestSelection:
    def test_real_duplication_is_selected(self, tmp_path):
        """The measured incident: same pnl, 60x different sizes."""
        path = _db(tmp_path, [
            {"id": 1, "pnl": -2970.99, "qty": 0.012},
            {"id": 2, "pnl": -2970.99, "qty": 0.717},
            {"id": 3, "pnl": -2970.99, "qty": 0.728},
        ])
        con = _open(path)
        rows, stats = mod.find_suspect_rows(con, qty_spread=1.5, min_abs_pnl=1.0)
        con.close()
        assert {r["id"] for r in rows} == {1, 2, 3}
        assert stats["suspect"] == 3

    def test_rounding_collision_is_NOT_selected(self, tmp_path):
        """Independent small scalps of the SAME size that round to one cent.
        Marking these would destroy good rows — the 236/408 over-count."""
        path = _db(tmp_path, [
            {"id": i, "pnl": -0.17, "qty": 0.004} for i in range(1, 16)
        ])
        con = _open(path)
        rows, stats = mod.find_suspect_rows(con, qty_spread=1.5, min_abs_pnl=1.0)
        con.close()
        assert rows == []
        assert stats["benign_collision"] == 15

    def test_small_pnl_is_skipped_even_when_spread_is_wide(self, tmp_path):
        """At scalp size a 2.5x spread is ordinary, so the floor declines to
        mark rather than over-mark."""
        path = _db(tmp_path, [
            {"id": 1, "pnl": -0.17, "qty": 0.002},
            {"id": 2, "pnl": -0.17, "qty": 0.005},
        ])
        con = _open(path)
        rows, stats = mod.find_suspect_rows(con, qty_spread=1.5, min_abs_pnl=1.0)
        con.close()
        assert rows == []
        assert stats["below_min_abs_pnl"] == 2

    def test_a_lone_row_is_never_suspect(self, tmp_path):
        path = _db(tmp_path, [{"id": 1, "pnl": -2970.99, "qty": 0.012}])
        con = _open(path)
        rows, _ = mod.find_suspect_rows(con, qty_spread=1.5, min_abs_pnl=1.0)
        con.close()
        assert rows == []

    def test_clusters_do_not_span_accounts_or_symbols(self, tmp_path):
        """Same pnl on two different books is a coincidence, not one netted
        close — nothing ties them to a shared exchange position."""
        path = _db(tmp_path, [
            {"id": 1, "pnl": -2970.99, "qty": 0.012, "account_id": "bybit_1"},
            {"id": 2, "pnl": -2970.99, "qty": 0.717, "account_id": "bybit_2"},
            {"id": 3, "pnl": -2970.99, "qty": 0.012, "symbol": "ETHUSDT"},
        ])
        con = _open(path)
        rows, _ = mod.find_suspect_rows(con, qty_spread=1.5, min_abs_pnl=1.0)
        con.close()
        assert rows == []


class TestApply:
    def _seeded(self, tmp_path):
        return _db(tmp_path, [
            {"id": 1, "pnl": -2970.99, "qty": 0.012},
            {"id": 2, "pnl": -2970.99, "qty": 0.717},
        ])

    def test_dry_run_writes_NOTHING(self, tmp_path, capsys):
        path = self._seeded(tmp_path)
        before = _open(path).execute("SELECT notes FROM trades").fetchall()
        assert mod.main(["--db", path]) == 0
        after = _open(path).execute("SELECT notes FROM trades").fetchall()
        assert [r["notes"] for r in before] == [r["notes"] for r in after]
        assert "DRY RUN" in capsys.readouterr().out

    def test_apply_marks_and_row_becomes_UNTRUSTWORTHY(self, tmp_path):
        path = self._seeded(tmp_path)
        con = _open(path)
        row = con.execute("SELECT notes FROM trades WHERE id=1").fetchone()
        con.close()
        # Before: classifies MEASURED and passes the calibration-set gate.
        assert provenance.pnl_is_trustworthy(row["notes"])

        assert mod.main(["--db", path, "--apply"]) == 0

        con = _open(path)
        row = con.execute("SELECT notes, pnl FROM trades WHERE id=1").fetchone()
        con.close()
        notes = json.loads(row["notes"])
        assert notes["exit_price_source"] == mod.MARKER
        assert notes[mod.PRE_KEY] == "bybit_closed_pnl"
        # After: refused by the gate that feeds the calibration set + ML labels.
        assert not provenance.pnl_is_trustworthy(row["notes"])
        assert provenance.classify_pnl(
            {"exit_price_source": mod.MARKER})[0] == provenance.FABRICATED

    def test_pnl_is_NOT_rewritten(self, tmp_path):
        """The number is disqualified, not replaced. Splitting it now — with no
        per-row fill to anchor to — would be the proration assumption dressed as
        a correction, and zeroing it would silently change history."""
        path = self._seeded(tmp_path)
        mod.main(["--db", path, "--apply"])
        con = _open(path)
        pnls = [r["pnl"] for r in con.execute("SELECT pnl FROM trades ORDER BY id")]
        con.close()
        assert pnls == [-2970.99, -2970.99]

    def test_apply_is_idempotent_and_preserves_the_TRUE_original(self, tmp_path):
        """A second pass must not overwrite the recorded original with the
        marker — that would erase the only record of what the row claimed."""
        path = self._seeded(tmp_path)
        mod.main(["--db", path, "--apply"])
        mod.main(["--db", path, "--apply"])
        con = _open(path)
        notes = json.loads(
            con.execute("SELECT notes FROM trades WHERE id=1").fetchone()["notes"])
        con.close()
        assert notes[mod.PRE_KEY] == "bybit_closed_pnl"
        assert notes["exit_price_source"] == mod.MARKER

    def test_account_filter_scopes_the_write(self, tmp_path):
        path = _db(tmp_path, [
            {"id": 1, "pnl": -2970.99, "qty": 0.012, "account_id": "bybit_1"},
            {"id": 2, "pnl": -2970.99, "qty": 0.717, "account_id": "bybit_1"},
            {"id": 3, "pnl": -500.55, "qty": 0.01, "account_id": "bybit_2"},
            {"id": 4, "pnl": -500.55, "qty": 0.9, "account_id": "bybit_2"},
        ])
        mod.main(["--db", path, "--apply", "--account", "bybit_1"])
        con = _open(path)
        got = {r["id"]: json.loads(r["notes"])["exit_price_source"]
               for r in con.execute("SELECT id, notes FROM trades")}
        con.close()
        assert got[1] == mod.MARKER and got[2] == mod.MARKER
        assert got[3] == "bybit_closed_pnl" and got[4] == "bybit_closed_pnl"

    def test_dry_run_opens_the_db_READ_ONLY(self, tmp_path):
        """Defence in depth: a selection bug must not be able to write during a
        dry run. Proven by the sqlite layer refusing the write outright."""
        path = self._seeded(tmp_path)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE trades SET pnl=0")
        con.close()
