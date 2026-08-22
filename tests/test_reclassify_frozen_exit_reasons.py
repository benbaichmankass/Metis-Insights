"""The historical relabel must STAGE, scope, and refuse to clobber.

Covers ``scripts/ops/reclassify_frozen_exit_reasons.py``
(``BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE``, historical half).

The properties that matter are behavioural, so these build a real sqlite journal and
run the real entry point rather than inspecting source.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "reclassify_frozen_exit_reasons",
    Path(__file__).resolve().parents[1] / "scripts/ops/reclassify_frozen_exit_reasons.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def _journal(tmp_path: Path) -> Path:
    p = tmp_path / "j.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT,"
              " exit_price REAL, exit_reason TEXT, setup_type TEXT, notes TEXT,"
              " account_id TEXT, closed_at TEXT, status TEXT, is_backtest INTEGER,"
              " order_package_id TEXT)")
    c.execute("CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY, sl REAL,"
              " tp REAL, linked_trade_id INTEGER)")

    def add(tid, *, px, reason, src, setup="ict_scalp_5m", sl=64230.9, tp=64278.6):
        pkg = f"pkg-{tid}"
        c.execute("INSERT INTO order_packages VALUES (?,?,?,?)", (pkg, sl, tp, tid))
        c.execute("INSERT INTO trades (id,symbol,direction,exit_price,exit_reason,"
                  "setup_type,notes,account_id,closed_at,status,is_backtest,"
                  "order_package_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, "BTCUSDT", "long", px, reason, setup,
                   json.dumps({"exit_price_source": src}), "bybit_2",
                   "2026-08-18T05:14:42Z", "closed", 0, pkg))

    # 1: broker truth, crossed the stop  -> relabel 'sl'
    add(1, px=64230.0, reason="reconciler_filled", src="bybit_closed_pnl")
    # 2: estimated price, crossed the stop -> relabellable but OUT of broker_truth scope
    add(2, px=64230.0, reason="reconciler_filled", src="local_markprice")
    # 3: broker truth, mid-bracket -> genuinely between, no relabel
    add(3, px=64250.0, reason="reconciler_filled", src="bybit_closed_pnl")
    # 4: broker truth, crossed -- but already carries a REAL reason
    add(4, px=64230.0, reason="pairs_half_open_cleanup", src="bybit_closed_pnl")
    # 5: broker truth, crossed -- but is a reduce leg
    add(5, px=64230.0, reason="reconciler_filled", src="bybit_closed_pnl",
        setup="intent_reduce")
    c.commit(); c.close()
    return p


def _reasons(db: Path):
    c = sqlite3.connect(db)
    out = dict(c.execute("SELECT id, exit_reason FROM trades").fetchall())
    c.close()
    return out


def test_annotate_writes_nothing(tmp_path, capsys):
    db = _journal(tmp_path)
    before = _reasons(db)
    rc = mod.main(["--db", str(db), "--out", str(tmp_path / "a.jsonl")])
    assert rc == 0
    assert _reasons(db) == before, "annotate must not touch the journal"
    assert "ANNOTATE ONLY" in capsys.readouterr().out


def test_annotate_still_measures_every_provenance(tmp_path):
    """Scoping the WRITE must never scope the MEASUREMENT."""
    db = _journal(tmp_path)
    out = tmp_path / "a.jsonl"
    mod.main(["--db", str(db), "--provenance", "broker_truth", "--out", str(out)])
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    provs = {r["provenance"] for r in rows}
    assert "estimated_or_worse" in provs, (
        "the out-of-scope class must still be measured and annotated"
    )


def test_apply_scoped_to_broker_truth_leaves_the_estimated_row(tmp_path):
    db = _journal(tmp_path)
    mod.main(["--db", str(db), "--provenance", "broker_truth", "--apply",
              "--out", str(tmp_path / "a.jsonl")])
    r = _reasons(db)
    assert r[1] == "sl", "the broker-truth crossed row should be relabelled"
    assert r[2] == "reconciler_filled", "the estimated row is out of scope"


def test_apply_never_clobbers_a_real_reason_or_grades_a_reduce(tmp_path):
    db = _journal(tmp_path)
    mod.main(["--db", str(db), "--provenance", "all", "--apply",
              "--out", str(tmp_path / "a.jsonl")])
    r = _reasons(db)
    assert r[4] == "pairs_half_open_cleanup", "a real reason must survive"
    assert r[5] == "reconciler_filled", "a reduce leg must never be graded"
    assert r[3] == "reconciler_filled", "a mid-bracket exit is genuinely between"


def test_apply_records_the_prior_value_so_it_is_reversible(tmp_path):
    db = _journal(tmp_path)
    mod.main(["--db", str(db), "--provenance", "broker_truth", "--apply",
              "--out", str(tmp_path / "a.jsonl")])
    c = sqlite3.connect(db)
    notes = json.loads(c.execute("SELECT notes FROM trades WHERE id=1").fetchone()[0])
    c.close()
    assert notes["exit_reason_prior"] == "reconciler_filled"
    assert notes["exit_reason_source"] == "price_vs_pkg_bracket"
    assert notes.get("exit_reason_backfilled_at")
