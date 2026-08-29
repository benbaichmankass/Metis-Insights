"""The exit LABEL must be re-derived at the moment the price arrives.

``_close_trade_from_order_status``'s no-record fallback hard-codes
``exit_reason='reconciler_filled'`` and leaves ``exit_price`` NULL — correctly, because
at that moment there is no price to classify against. ``_sweep_pending_pnl_from_bybit``
is the moment the price arrives, and until this change it wrote the price and left the
label frozen at the one instant the answer could not be known.

MEASURED 2026-08-22 over the 395 gradeable ``reconciler_filled`` rows (an exit price and
a linked package carrying at least one positive level; 172 with no package and 5 with no
price EXCLUDED, not counted): on the **155** carrying a BROKER-TRUTH price, **91 (58.7%)**
had actually reached a declared bracket level, and **181 of 181** mislabelled rows carried
no ``exit_reason_source`` key — a 100% signature that none reached the classifier.

Row: ``BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE``.
"""
from __future__ import annotations

import inspect

from src.runtime import order_monitor as om


def _sweep_src() -> str:
    return inspect.getsource(om._sweep_pending_pnl_from_bybit)


def test_the_sweep_reads_setup_type_so_reduce_legs_can_be_excluded():
    """Without ``setup_type`` in the SELECT, ``is_reduce_leg`` cannot be derived.

    A reduce's bracket can be INVERTED relative to the order direction, so classifying
    one would mislabel it as sl/tp — the exact failure the classifier's own exclusion
    exists to prevent. This is the hazard that makes the change unsafe if skipped.
    """
    src = _sweep_src()
    assert "setup_type" in src, (
        "the sweep must SELECT setup_type — otherwise is_reduce_leg is always False "
        "and an intent_reduce leg gets mislabelled sl/tp"
    )


def test_the_sweep_actually_calls_the_classifier():
    src = _sweep_src()
    assert "_classify_broker_exit" in src, (
        "the sweep writes the price; it must also re-derive the label that depends on it"
    )


def test_reduce_legs_are_excluded_by_passing_is_reduce_leg():
    src = _sweep_src()
    assert "is_reduce_leg=" in src, (
        "the classifier call must pass is_reduce_leg — omitting it defaults to False "
        "and reduces get graded"
    )
    assert "intent_reduce" in src, "is_reduce_leg must be derived from the reduce markers"


def test_only_a_generic_reason_is_overwritten():
    """A row closed by another path with a REAL reason must not be clobbered.

    The sweep selects on ``pnl IS NULL``, which can include rows a different path closed
    with ``pairs_*`` / ``sl_cross`` / etc. Overwriting one of those destroys a better
    record than the one being written.
    """
    src = _sweep_src()
    assert "reconciler_filled" in src, (
        "the relabel must be gated on the row still carrying the generic reason"
    )


def test_the_marker_is_stamped_only_when_the_classifier_ran():
    """``exit_reason_source``'s ABSENCE is what made the 181/181 signature readable.

    Stamping it on rows the classifier never saw would destroy the very marker the
    measurement depends on.
    """
    src = _sweep_src()
    assert "exit_reason_source" in src
    assert "price_vs_pkg_bracket" in src and "unresolved" in src, (
        "both classifier outcomes must be recorded distinctly"
    )


def _run_sweep(tmp_path, monkeypatch, *, rec, classifier, row_over=None):
    """Exercise the real sweep against a real sqlite trades table.

    Source-grepping cannot answer "does a label failure lose the price write" — only
    running it can. This builds the minimum the sweep touches and captures the update.
    """
    import datetime as _dt
    import sqlite3

    # ⚠️ THE ROW'S created_at MUST BE RELATIVE, NEVER A LITERAL DATE.
    #
    # The sweep's own SELECT filters `datetime(created_at) >= datetime('now',
    # '-7 days')` (Bybit's closed-pnl retention window). This fixture used to
    # hard-code "2026-08-22T00:00:00+00:00" -- inside the window on the day the
    # test was written, and OUTSIDE it exactly 7 days later. It began failing at
    # 2026-08-29T00:00:00Z with no code change, and took `main` red with it: the
    # row stopped matching, the sweep processed nothing, and the five behavioural
    # tests failed as KeyError/empty-capture rather than as anything meaningful.
    #
    # Note which half broke: the 6 SOURCE-INSPECTION tests above kept passing, so
    # the suite reported the implementation as fine while the behavioural half
    # silently stopped exercising it. A time-bomb fixture does not announce
    # itself as one -- it looks like a real regression in unrelated code.
    #
    # BL-20260829-FIXTURE-DATE-AGES-OUT-OF-THE-QUERY-WINDOW-AND-TAKES-MAIN-RED
    created_at = (_dt.datetime.now(_dt.timezone.utc)
                  - _dt.timedelta(days=1)).isoformat()

    db_path = tmp_path / "j.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, "
        "position_size REAL, entry_price REAL, account_id TEXT, created_at TEXT, "
        "notes TEXT, setup_type TEXT, exit_reason TEXT, status TEXT, "
        "is_backtest INTEGER, pnl REAL, exit_price REAL, pnl_percent REAL)"
    )
    row = {
        "id": 1, "symbol": "BTCUSDT", "direction": "long", "position_size": 0.001,
        "entry_price": 64108.7, "account_id": "bybit_2",
        "created_at": created_at, "notes": "{}",
        "setup_type": "ict_scalp_5m", "exit_reason": "reconciler_filled",
        "status": "closed", "is_backtest": 0, "pnl": None,
    }
    row.update(row_over or {})
    conn.execute(
        "INSERT INTO trades (id,symbol,direction,position_size,entry_price,account_id,"
        "created_at,notes,setup_type,exit_reason,status,is_backtest,pnl) "
        "VALUES (:id,:symbol,:direction,:position_size,:entry_price,:account_id,"
        ":created_at,:notes,:setup_type,:exit_reason,:status,:is_backtest,:pnl)", row)
    conn.commit()
    conn.close()

    captured = {}

    class _DB:
        def connect(self):
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c
        def update_trade(self, tid, updates):
            captured[tid] = dict(updates)

    monkeypatch.setattr(om, "_load_account_cfgs_for_reconcile",
                        lambda: {"bybit_2": {"account_id": "bybit_2"}})
    import src.units.accounts.clients as _clients
    monkeypatch.setattr(_clients, "account_closed_pnl_for_trade",
                        lambda *a, **k: rec, raising=False)
    monkeypatch.setattr(om, "_classify_broker_exit", classifier)
    monkeypatch.setattr(om, "_broker_pnl_source", lambda r: "bybit_closed_pnl")
    monkeypatch.setattr(om, "_broker_pnl_note_key", lambda r: "bybit_closed_pnl")
    summary = om._sweep_pending_pnl_from_bybit(_DB())
    return summary, captured


_REC = {"avg_exit_price": 64230.0, "closed_pnl": 0.094, "closed_at": None}


def test_a_classification_failure_cannot_lose_the_price_write(tmp_path, monkeypatch):
    """THE load-bearing guard, exercised rather than grepped.

    The pnl/exit_price write is the half that matters; a bookkeeping label must never
    be able to take it down.
    """
    def _boom(*a, **k):
        raise RuntimeError("classifier exploded")

    summary, captured = _run_sweep(tmp_path, monkeypatch, rec=_REC, classifier=_boom)
    assert captured, "the sweep must still have written something"
    upd = captured[1]
    assert upd["exit_price"] == 64230.0, "the price write was lost to a label failure"
    assert upd.get("pnl") == 0.094, "the pnl write was lost to a label failure"
    assert "exit_reason" not in upd, "a failed classification must not write a label"
    assert summary["filled"] == 1


def test_a_resolved_classification_is_written(tmp_path, monkeypatch):
    summary, captured = _run_sweep(
        tmp_path, monkeypatch, rec=_REC, classifier=lambda *a, **k: "sl")
    upd = captured[1]
    assert upd["exit_reason"] == "sl"
    assert summary["reclassified"] == 1
    assert '"exit_reason_source": "price_vs_pkg_bracket"' in upd["notes"]


def test_an_unresolved_classification_leaves_the_reason_alone(tmp_path, monkeypatch):
    summary, captured = _run_sweep(
        tmp_path, monkeypatch, rec=_REC, classifier=lambda *a, **k: None)
    upd = captured[1]
    assert "exit_reason" not in upd, "mid-bracket exits keep reconciler_filled"
    assert summary["reclassified"] == 0
    assert '"exit_reason_source": "unresolved"' in upd["notes"]


def test_a_row_with_a_real_reason_is_never_clobbered(tmp_path, monkeypatch):
    """The sweep selects on pnl IS NULL, which can catch a row another path closed."""
    summary, captured = _run_sweep(
        tmp_path, monkeypatch, rec=_REC, classifier=lambda *a, **k: "sl",
        row_over={"exit_reason": "pairs_half_open_cleanup"})
    upd = captured[1]
    assert "exit_reason" not in upd, "a real reason must survive the sweep"
    assert upd["exit_price"] == 64230.0, "the price is still written"
    assert summary["reclassified"] == 0


def test_a_reduce_leg_is_never_graded(tmp_path, monkeypatch):
    """A reduce's bracket can be INVERTED, so grading it mislabels it sl/tp."""
    seen = {}

    def _cls(db, row, px, *, is_reduce_leg=False):
        seen["is_reduce_leg"] = is_reduce_leg
        return None if is_reduce_leg else "sl"

    summary, captured = _run_sweep(
        tmp_path, monkeypatch, rec=_REC, classifier=_cls,
        row_over={"setup_type": "intent_reduce"})
    assert seen["is_reduce_leg"] is True, "the reduce marker did not reach the classifier"
    assert "exit_reason" not in captured[1]
    assert summary["reclassified"] == 0


def test_the_summary_declares_reclassified_up_front():
    """A run that re-classified nothing must report 0, not omit the key.

    'We looked and none qualified' must not be indistinguishable from 'we did not look'.
    """
    src = _sweep_src()
    assert '"reclassified": 0' in src, (
        "the counter must be declared in the summary initialiser, not created on first use"
    )
