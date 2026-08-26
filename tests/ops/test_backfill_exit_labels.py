"""Tests for scripts/ops/backfill_exit_labels.py (GATE 0 / G1).

The script relabels ``trades.exit_reason`` on rows that were PRICED after they
were closed — the frozen-label defect
(``BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE``). It is gated on the
PRICE's own provenance, because ``_classify_broker_exit`` is provenance-blind:
handed ``local_markprice`` (the market at SWEEP time, hours after the exit) it
would manufacture an sl/tp verdict out of unrelated later price action.

⚠️ **The first test here runs the script as a SUBPROCESS, and that shape is the
whole point.** It shipped in #10262 with ``sys.path`` walking two levels from
``scripts/ops/`` — landing on ``scripts/``, not the repo root — so ``import src``
raised ``ModuleNotFoundError`` and the tool could not run at all. An
``importlib.util.spec_from_file_location`` test (the pattern every sibling
backfill test uses) would NOT have caught it: under pytest the repo root is
already on ``sys.path``, so the broken insert is harmless and the import
succeeds. Only an invocation with a foreign cwd and no inherited ``PYTHONPATH``
— which is exactly how ``backfill_exit_labels_action.sh`` calls it, by absolute
path — reproduces production. Measured 2026-08-26: **0 rows** on the live
journal carried ``pre_backfill_exit_reason`` three days after the tool merged.

Uses the canonical-schema fixture so a production column rename fails the test
rather than silently passing.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.fixtures.real_schema_db import (
    insert_order_package,
    insert_trade,
    make_canonical_db,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "backfill_exit_labels.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_exit_labels", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── The regression that actually bit ─────────────────────────────────────────

def test_script_runs_standalone_the_way_the_wrapper_invokes_it(tmp_path):
    """Absolute path, foreign cwd, no inherited PYTHONPATH — production's shape.

    ``backfill_exit_labels_action.sh`` runs ``python3 "${PY_SCRIPT}"`` with no
    ``cd`` and no ``PYTHONPATH``, so ``sys.path[0]`` is ``scripts/ops`` and the
    script's own insert is the ONLY thing that can put the repo root on the
    path. If that insert is wrong the tool is inert.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--self-test"],
        cwd=str(tmp_path),          # deliberately NOT the repo root
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"the tool cannot run standalone.\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    # The self-test is the wrapper's precondition for applying; it must have
    # actually exercised its planted controls, not just exited 0.
    assert "self-test: 12/12 passed" in proc.stdout, proc.stdout


def test_dry_run_is_read_only_and_says_so(tmp_path):
    """A dry run opens the DB ``mode=ro``, so it cannot mutate the money DB even
    by accident. Assert the *file* is unchanged, not merely that it printed."""
    db = make_canonical_db(tmp_path / "j.db")
    pkg = insert_order_package(db, order_package_id="p1", symbol="BTCUSDT",
                               direction="long", sl=90.0, tp=110.0)
    insert_trade(db, status="closed", is_backtest=0, symbol="BTCUSDT",
                 timestamp="2026-08-01T00:00:00Z", entry_price=100.0, position_size=1.0,
                 direction="long", exit_price=89.0, exit_reason="reconciler_filled",
                 order_package_id=pkg,
                 notes=json.dumps({"exit_price_source": "bybit_closed_pnl"}))
    before = db.read_bytes()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--db", str(db)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "DRY RUN — nothing written" in proc.stdout
    assert db.read_bytes() == before, "a dry run must not touch the file"


# ── The provenance gate: every branch shown able to fire ─────────────────────

def _seed(tmp_path) -> Path:
    db = make_canonical_db(tmp_path / "j.db")
    insert_order_package(db, order_package_id="pkg", symbol="BTCUSDT",
                         direction="long", sl=90.0, tp=110.0)

    def t(**kw):
        base = dict(status="closed", is_backtest=0, symbol="BTCUSDT",
                    timestamp="2026-08-01T00:00:00Z", entry_price=100.0, position_size=1.0,
                    direction="long", exit_reason="reconciler_filled",
                    order_package_id="pkg")
        base.update(kw)
        return insert_trade(db, **base)

    ids = {}
    ids["measured_sl"] = t(exit_price=89.0,
                           notes=json.dumps({"exit_price_source": "bybit_closed_pnl"}))
    ids["measured_tp"] = t(exit_price=111.0,
                           notes=json.dumps({"exit_price_source": "exchange_fill"}))
    ids["estimated_sl"] = t(exit_price=89.0,
                            notes=json.dumps({"exit_price_source": "candle_at_close"}))
    ids["fabricated"] = t(exit_price=89.0,
                          notes=json.dumps({"exit_price_source": "local_markprice"}))
    ids["unverified"] = t(exit_price=89.0, notes=json.dumps({}))
    ids["unresolved"] = t(exit_price=100.0,   # strictly between sl and tp
                          notes=json.dumps({"exit_price_source": "bybit_closed_pnl"}))
    ids["reduce"] = t(exit_price=89.0, setup_type="intent_reduce",
                      notes=json.dumps({"exit_price_source": "bybit_closed_pnl"}))
    ids["real_reason"] = t(exit_price=89.0, exit_reason="pairs_stop",
                           notes=json.dumps({"exit_price_source": "bybit_closed_pnl"}))
    ids["already"] = t(exit_price=89.0, notes=json.dumps(
        {"exit_price_source": "bybit_closed_pnl",
         "exit_reason_source": "price_vs_pkg_bracket"}))
    return db, ids


def _plan_by_id(mod, db):
    conn = sqlite3.connect(str(db))
    try:
        planned, stats = mod.plan(conn)
    finally:
        conn.close()
    return {p["id"]: p for p in planned}, stats


def test_every_branch_fires(tmp_path):
    mod = _load_module()
    db, ids = _seed(tmp_path)
    by_id, stats = _plan_by_id(mod, db)

    assert by_id[ids["measured_sl"]]["new_reason"] == "sl"
    assert by_id[ids["measured_sl"]]["source"] == "price_vs_pkg_bracket"
    assert by_id[ids["measured_tp"]]["new_reason"] == "tp"

    # An inference on an inference must NOT read as the stronger verdict.
    est = by_id[ids["estimated_sl"]]
    assert est["new_reason"] == "sl"
    assert est["source"] == "price_vs_pkg_bracket_est_price"

    # The refusals — the reason this script exists.
    for key in ("fabricated", "unverified"):
        p = by_id[ids[key]]
        assert p["action"] == "refuse", key
        assert p["new_reason"] is None, key
        assert p["source"] == "refused_unmeasured_price", key

    # A genuine mid-bracket close stays generic, stamped honestly.
    assert by_id[ids["unresolved"]]["action"] == "unresolved"
    assert by_id[ids["unresolved"]]["new_reason"] is None

    # Guards: never planned at all.
    assert ids["reduce"] not in by_id, "a reduce leg's bracket can be INVERTED"
    assert ids["real_reason"] not in by_id, "a better record must not be overwritten"
    assert ids["already"] not in by_id, "idempotency"
    assert stats["skip_already_classified"] == 1
    assert stats["skip_has_real_reason"] == 1


def test_apply_is_reversible_and_idempotent(tmp_path):
    mod = _load_module()
    db, ids = _seed(tmp_path)
    conn = sqlite3.connect(str(db))
    try:
        planned, _ = mod.plan(conn)
        mod.apply(conn, planned)
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        reason, raw = conn.execute(
            "SELECT exit_reason, notes FROM trades WHERE id = ?",
            (ids["measured_sl"],)).fetchone()
        n = json.loads(raw)
        assert reason == "sl"
        # Reversible from the row itself — no separate journal needed.
        assert n["pre_backfill_exit_reason"] == "reconciler_filled"
        assert n["exit_reason_price_basis"] == "measured"

        # A refused row keeps its label and is STAMPED, so "we looked and
        # declined" stays distinguishable from "the classifier never ran" —
        # the absence semantics that made this defect class readable at all.
        reason, raw = conn.execute(
            "SELECT exit_reason, notes FROM trades WHERE id = ?",
            (ids["fabricated"],)).fetchone()
        assert reason == "reconciler_filled"
        assert json.loads(raw)["exit_reason_source"] == "refused_unmeasured_price"
        assert "pre_backfill_exit_reason" not in json.loads(raw)

        # Second run is a no-op: everything now carries a stamp.
        planned2, stats2 = mod.plan(conn)
    finally:
        conn.close()
    assert planned2 == [], "re-running must be a no-op"
    assert stats2["eligible"] == 0


def test_a_fabricated_price_would_otherwise_have_been_labelled(tmp_path):
    """The negative control. The refused row sits at 89.0 against a stop of 90.0,
    so the classifier WOULD have called it 'sl' — the refusal is the provenance
    gate doing work, not the price happening to be unclassifiable.
    """
    mod = _load_module()
    assert mod.classify("long", 89.0, 90.0, 110.0) == "sl"
