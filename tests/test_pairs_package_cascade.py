"""The pairs sleeve must close its OWN ``order_packages`` rows.

``_close_pair`` writes ``status='closed'`` straight to the trade row, so it never
routes through ``order_monitor._close_trade_from_order_status`` — the only place the
package cascade runs. Every pairs leg closed there left its package open until the
second-line-of-defence sweep stamped the generic ``stuck_cascade_recovered``, which is
a **bookkeeping repair, not an exit**.

Measured on the live journal 2026-08-22 (newest 500 closed rows, 2026-07-15 →
2026-08-22, ``filter_state`` asserted ``applied``) the two arms reconcile exactly:
57 pairs legs closed via the monitor ↔ 57 packages carrying a real reason; 120 closed
via ``_close_pair``/``intent_reduce`` ↔ 120 left for the sweep (109 swept + 11 not yet).
``pairs_revert``/``pairs_stop``/``pairs_half_open_cleanup`` appear on **99 trade rows
and ZERO package rows**.

Rows: ``BL-20260822-PAIRS-PACKAGES-CLOSED-BY-THE-STUCK-CASCADE-SWEEP`` ·
``BL-20260822-PACKAGE-CLOSE-REASON-IS-NOT-THE-EXIT-RECORD``.
"""
from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from src.units.strategies import pairs_executor as px


def _real_order_packages_ddl() -> str:
    """The PRODUCTION ``order_packages`` DDL, lifted from the module that owns it.

    Deliberately NOT hand-written — a hand-declared schema is what let an earlier
    pairs suite pass against a table production does not have while the real query
    raised on every live tick (BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED).
    """
    src = Path(__file__).resolve().parents[1] / "src/units/db/database.py"
    text = src.read_text(encoding="utf-8")
    i = text.index("CREATE TABLE IF NOT EXISTS order_packages")
    return text[i:text.index("''')", i)]


def _seed(db_path: Path, *, pkg_id: str = "op-1", strategy: str = "pairs_sol_btc_a",
          linked_trade_id: int = 42) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(_real_order_packages_ddl())
    cols = {r[1] for r in conn.execute("PRAGMA table_info(order_packages)")}
    assert "linked_trade_id" in cols, "production DDL lost linked_trade_id"
    conn.execute(
        "INSERT INTO order_packages (order_package_id, strategy_name, symbol, "
        "direction, entry, sl, tp, created_at, updated_at, status, linked_trade_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (pkg_id, strategy, "SOLUSDT", "buy", 1.0, 0.9, 1.2,
         "2026-08-22T00:00:00+00:00", "2026-08-22T00:00:00+00:00", "open",
         linked_trade_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- wiring

def test_close_pair_actually_calls_the_cascade():
    """The WIRING, not just the helper.

    A correct helper that is never called fixes nothing — the same failure shape as
    ``attach-ib-target``'s ops clientId helper, which existed and was never passed to
    the factory. This is the assertion that fails against the pre-fix file.
    """
    src = inspect.getsource(px._close_pair)
    assert "_cascade_close_pair_package(" in src, (
        "_close_pair does not cascade its package — every pairs leg it closes will "
        "be left for the stuck-cascade sweep"
    )


def test_cascade_is_passed_the_pairs_native_reason_not_a_literal():
    """The package must carry the reason the TRADE exited for.

    Stamping any fixed string here would reproduce the defect one level up: the
    package would close, but ``close_reason`` still would not be the exit record.
    """
    src = inspect.getsource(px._close_pair)
    line = next(l for l in src.splitlines() if "_cascade_close_pair_package(" in l)
    assert 'f"pairs_{outcome}"' in line, (
        f"cascade must forward the leg's own exit reason; got: {line.strip()}"
    )


def test_cascade_call_is_inside_the_success_path():
    """It must sit AFTER the update_trade that closes the row, not before it.

    Cascading a package for a close that then fails would mark the decision finished
    while the position is still open.
    """
    src = inspect.getsource(px._close_pair)
    assert src.index("update_trade") < src.index("_cascade_close_pair_package("), (
        "cascade must follow the trade-row close, not precede it"
    )


# ------------------------------------------------------------- behaviour

def test_cascade_closes_the_linked_package_with_the_exit_reason(tmp_path):
    from src.units.db.database import Database

    db_path = tmp_path / "j.db"
    _seed(db_path, linked_trade_id=42)
    db = Database(db_path=str(db_path))

    ok = px._cascade_close_pair_package(db, 42, "pairs_revert")
    assert ok is True

    conn = sqlite3.connect(db_path)
    status, reason = conn.execute(
        "SELECT status, close_reason FROM order_packages WHERE order_package_id='op-1'"
    ).fetchone()
    conn.close()
    assert status == "closed"
    assert reason == "pairs_revert", (
        "the package must carry the leg's real exit reason — 'stuck_cascade_recovered' "
        "is the sweep's bookkeeping repair, not an exit"
    )


def test_cascade_returns_false_when_no_package_is_linked(tmp_path):
    """A miss is False, not an exception — the sweep stays the backstop."""
    from src.units.db.database import Database

    db_path = tmp_path / "j.db"
    _seed(db_path, linked_trade_id=42)
    db = Database(db_path=str(db_path))
    assert px._cascade_close_pair_package(db, 99999, "pairs_stop") is False


# ------------------------------------------------- failure isolation (critical)

def test_a_cascade_failure_never_raises(monkeypatch):
    """Bookkeeping failure must be swallowed HERE.

    The surrounding ``except`` in ``_close_pair`` sets ``closed_ok = False``, which the
    tick reads as *the leg did not flatten*. Letting a package-write failure reach it
    would report a successfully flattened leg as still open — strictly worse than the
    defect being fixed.
    """
    import src.runtime.order_monitor as om

    def boom(*a, **k):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(om, "_cascade_close_linked_package", boom)
    assert px._cascade_close_pair_package(object(), 42, "pairs_revert") is False


def test_close_pair_reports_ok_even_when_the_cascade_explodes(monkeypatch, tmp_path):
    """End-to-end: a broker close that SUCCEEDED stays ``closed=True`` when the
    package write fails. This is the property that protects the sleeve's state."""
    import src.units.accounts.execute as ex
    import src.units.db.database as dbmod
    import src.utils.paths as paths

    class _FakeDB:
        def __init__(self, *a, **k):
            pass

        def get_trades(self, filters=None, limit=None):
            return [{"id": 7, "direction": "long", "position_size": 2.0,
                     "entry_price": 100.0}]

        def update_trade(self, *a, **k):
            return True

    monkeypatch.setattr(dbmod, "Database", _FakeDB)
    monkeypatch.setattr(paths, "trade_journal_db_path", lambda *a, **k: ":memory:")
    monkeypatch.setattr(ex, "close_open_position", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(px, "_cascade_close_pair_package",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    pair = {"name": "pairs_sol_btc", "symbol_a": "SOLUSDT", "symbol_b": "BTCUSDT"}
    res = px._close_pair(object(), {"account_id": "bybit_1"}, pair,
                         "revert", 110.0, 90.0)
    assert res["closed"] is True, (
        "a package-bookkeeping failure was reported as a failed flatten — the leg is "
        "flat at the broker and would be stranded as 'still open'"
    )
