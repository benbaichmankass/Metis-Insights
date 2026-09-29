"""EXIT-CLUSTER: an ``exchange_flat_reconciled`` close that was really a bracket fill
gets the bracket's label, off a MEASURED price only.

On Alpaca and IB the stop and target rest AT THE BROKER. When one fires, the
position-snapshot reconciler only sees the symbol go flat and writes
``exchange_flat_reconciled``. ``_sweep_pending_pnl_from_bybit`` then recovers the
venue fill but, before this change, relabelled only ``''`` / ``reconciler_filled``.

MEASURED 2026-09-29 over the 77 closed non-backtest ``exchange_flat_reconciled`` rows
with ``closed_at`` >= 2026-07-31 (live journal via ``/api/diag/journal`` and the fills
store via ``/api/bot/pnl/exchange/fills``, pulled by
``scripts/research/realized_slippage.py pull``): 48 exit at or through a package level,
16 within 0.0-8.3 bps of one, 10 are 42+ bps from both, 3 have no fill.

Every trade row, package level and fill record below is copied from that pull (trade
ids named per test). The broker record has the exact key set ``fills_pnl.exit_from_fills``
returns.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3

import pytest

from src.runtime import order_monitor as om

# Real notes string from trade 5766 as the snapshot reconciler + sweeps left it.
_REAL_FLAT_NOTES = json.dumps({
    "closed_at": "2026-09-16T15:04:50.918283+00:00",
    "closed_by": "position_snapshot_reconciler",
    "closed_reason": (
        "(symbol, side) confirmed absent from the exchange open-positions snapshot "
        "across two observations AND, where the integration supports it, by a direct "
        "per-symbol broker check (alpaca 404); integration has no per-order status "
        "reader (non-Bybit). PnL filled by the local-PnL sweep (mark-to-market)."
    ),
    "reset_event": False,
})


def _rec(avg_exit_price, source="exchange_fill", closed_pnl=None):
    """The dict ``fills_pnl.exit_from_fills`` returns (same keys)."""
    return {
        "avg_exit_price": avg_exit_price, "fees": 0.0, "avg_entry_price": None,
        "closed_pnl": closed_pnl, "qty": 72.0, "side": "buy",
        "closed_at": "2026-09-16T15:01:07Z", "source": source,
    }


# (trade id, account, symbol, direction, qty, entry, fill, pkg sl, pkg tp)
# Real rows from the 2026-09-29 pull.
_TLT_5766 = (5766, "alpaca_portfolio", "TLT", "short", 72.0, 81.2, 81.19,
             81.19321429, 73.1612)
_USO_5364 = (5364, "alpaca_paper", "USO", "long", 19.0, 141.15, 139.17684210526318,
             138.58857143, 151.83285714)
_USO_5561 = (5561, "alpaca_paper", "USO", "long", 3.0, 145.15, 157.25,
             151.07857143, 157.25285714)
_IAUM_6097 = (6097, "alpaca_live", "IAUM", "long", 2.0, None, 41.27, 41.98, None)


def _run(tmp_path, monkeypatch, row_spec, *, rec=None, exit_reason="exchange_flat_reconciled",
         notes=_REAL_FLAT_NOTES, setup_type="tlt_pullback_1h"):
    tid, acct, sym, direction, qty, entry, fill, sl, tp = row_spec
    # Relative timestamps: the sweep only selects rows closed in the last 7 days.
    now = _dt.datetime.now(_dt.timezone.utc)
    created = (now - _dt.timedelta(days=2)).isoformat()
    closed = (now - _dt.timedelta(hours=1)).isoformat()
    db_path = tmp_path / "j.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, "
        "position_size REAL, entry_price REAL, account_id TEXT, created_at TEXT, "
        "closed_at TEXT, notes TEXT, setup_type TEXT, exit_reason TEXT, status TEXT, "
        "is_backtest INTEGER, pnl REAL, exit_price REAL, pnl_percent REAL, "
        "order_package_id TEXT)")
    conn.execute(
        "CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY, sl REAL, "
        "tp REAL, linked_trade_id INTEGER)")
    pkg = f"pkg-{tid}"
    conn.execute(
        "INSERT INTO trades (id,symbol,direction,position_size,entry_price,account_id,"
        "created_at,closed_at,notes,setup_type,exit_reason,status,is_backtest,pnl,"
        "order_package_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,'closed',0,NULL,?)",
        (tid, sym, direction, qty, entry or 41.0, acct, created, closed, notes,
         setup_type, exit_reason, pkg))
    conn.execute("INSERT INTO order_packages VALUES (?,?,?,?)", (pkg, sl, tp, tid))
    conn.commit()
    conn.close()

    captured = {}

    class _DB:
        def connect(self):
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        def update_trade(self, t, updates):
            captured[t] = dict(updates)

    monkeypatch.setattr(om, "_load_account_cfgs_for_reconcile",
                        lambda: {acct: {"account_id": acct, "exchange": "alpaca"}})
    import src.units.accounts.clients as _clients
    monkeypatch.setattr(_clients, "account_closed_pnl_for_trade",
                        lambda *a, **k: rec if rec is not None else _rec(fill),
                        raising=False)
    summary = om._sweep_pending_pnl_from_bybit(_DB())
    return summary, captured[tid], json.loads(captured[tid]["notes"])


def test_cent_rounded_stop_fill_is_labelled_sl(tmp_path, monkeypatch):
    """Trade 5766: stop filled at 81.19 against a package stop of 81.19321429.

    The strict inequality calls that 'between' (the fill is 0.4 bps inside the level,
    because Alpaca took the stop at 81.19). It was the stop.
    """
    summary, upd, notes = _run(tmp_path, monkeypatch, _TLT_5766)
    assert upd["exit_reason"] == "sl"
    assert upd["exit_price"] == 81.19
    assert summary["reclassified"] == 1
    assert notes["exit_reason_source"] == "price_vs_pkg_bracket"
    assert notes["pre_label_exit_reason"] == "exchange_flat_reconciled"
    assert notes["exit_price_source"] == "exchange_fill"


def test_target_fill_a_fraction_short_of_the_level_is_labelled_tp(tmp_path, monkeypatch):
    """Trade 5561: target filled at 157.25 against 157.25285714."""
    summary, upd, notes = _run(tmp_path, monkeypatch, _USO_5561, setup_type="uso_trend_1h")
    assert upd["exit_reason"] == "tp"
    assert notes["exit_reason_source"] == "price_vs_pkg_bracket"


def test_gap_through_stop_on_real_money_is_labelled_sl(tmp_path, monkeypatch):
    """Trade 6097 (alpaca_live): opened below... the 13:30 UTC open gapped 168 bps through
    the 41.98 stop and it filled at 41.27. Through the level, so sl, tolerance or not."""
    summary, upd, _ = _run(tmp_path, monkeypatch, _IAUM_6097, setup_type="iaum_pullback_1d")
    assert upd["exit_reason"] == "sl"


def test_mid_range_exit_keeps_the_flat_reason(tmp_path, monkeypatch):
    """Trade 5364: filled 42 bps inside the stop. That is not a bracket fill, so the
    reason stays exchange_flat_reconciled and the row is stamped unresolved."""
    summary, upd, notes = _run(tmp_path, monkeypatch, _USO_5364, setup_type="uso_trend_1h")
    assert "exit_reason" not in upd
    assert summary["reclassified"] == 0
    assert notes["exit_reason_source"] == "unresolved"
    assert "pre_label_exit_reason" not in notes


def test_a_non_measured_price_is_refused_not_labelled(tmp_path, monkeypatch):
    """A record whose source is not a fill must never mint a label on this path."""
    summary, upd, notes = _run(tmp_path, monkeypatch, _TLT_5766,
                               rec=_rec(81.19, source="candle_at_close"))
    assert "exit_reason" not in upd
    assert notes["exit_reason_source"] == "refused_unmeasured_price"
    # The price write is untouched by the refusal.
    assert upd["exit_price"] == 81.19


def test_ib_execution_is_measured_and_labelled(tmp_path, monkeypatch):
    """ib_paper rows resolve through the same sweep with source 'ib_execution'.
    Real trade 6022 (MGC short): filled 4317.5, package tp 4317.49428571."""
    spec = (6022, "ib_paper", "MGC", "short", 69.0, 4355.5, 4317.5,
            4348.96675, 4317.49428571)
    summary, upd, notes = _run(tmp_path, monkeypatch, spec,
                               rec=_rec(4317.5, source="ib_execution", closed_pnl=2622.0),
                               setup_type="ict_scalp_mgc_15m")
    assert upd["exit_reason"] == "tp"
    assert upd["pnl"] == 2622.0


def test_reconciler_filled_keeps_the_strict_rule(tmp_path, monkeypatch):
    """The tolerance is scoped to exchange_flat_reconciled. The same 5766 prices on a
    reconciler_filled row stay unresolved, exactly as before this change."""
    summary, upd, notes = _run(tmp_path, monkeypatch, _TLT_5766,
                               exit_reason="reconciler_filled")
    assert "exit_reason" not in upd
    assert notes["exit_reason_source"] == "unresolved"


def test_a_real_reason_is_still_never_clobbered(tmp_path, monkeypatch):
    summary, upd, _ = _run(tmp_path, monkeypatch, _TLT_5766, exit_reason="sl_cross")
    assert "exit_reason" not in upd


@pytest.mark.parametrize("direction,px,sl,tp,tol,want", [
    ("short", 81.19, 81.19321429, 73.1612, 0.0, None),    # strict: between
    ("short", 81.19, 81.19321429, 73.1612, 10.0, "sl"),   # 0.4 bps inside
    ("long", 139.17684, 138.58857143, 151.83285714, 10.0, None),  # 42 bps inside
    ("long", 100.0, 99.95, 100.05, 10.0, None),           # bracket too narrow: strict
    ("long", 99.95, 99.95, 100.05, 10.0, "sl"),           # strict still applies
])
def test_classifier_tolerance(tmp_path, direction, px, sl, tp, tol, want):
    db_path = tmp_path / "c.db"
    c = sqlite3.connect(db_path)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, order_package_id TEXT)")
    c.execute("CREATE TABLE order_packages (order_package_id TEXT, sl REAL, tp REAL, "
              "linked_trade_id INTEGER)")
    c.execute("INSERT INTO trades VALUES (1, 'p1')")
    c.execute("INSERT INTO order_packages VALUES ('p1', ?, ?, 1)", (sl, tp))
    c.commit()
    c.close()

    class _DB:
        def connect(self):
            return sqlite3.connect(db_path)

    row = {"id": 1, "direction": direction, "symbol": "X"}
    assert om._classify_broker_exit(_DB(), row, px, tolerance_bps=tol) == want


def test_protected_prose_is_shortened_before_every_key_is_dropped():
    """Live trade 4711 stores notes of exactly {"_truncated": true}: the snapshot
    reconciler's ~330-char protected `closed_reason` plus the sweeps' stamps passed the
    500-char cap and the fallback dropped every key, the exit-price provenance included.
    The same shape now keeps every protected key and shortens only the prose."""
    from src.utils.json_notes import dump_capped

    notes = json.loads(_REAL_FLAT_NOTES)
    notes.update({
        "pnl_source": "local_compute", "exit_price_source": "exchange_fill",
        "exit_reason_source": "price_vs_pkg_bracket",
        "pre_label_exit_reason": "exchange_flat_reconciled",
        "contract_value_usd": 1.0,
    })
    out = dump_capped(notes, 500)
    assert len(out) <= 500
    back = json.loads(out)
    for k in ("closed_at", "closed_by", "pnl_source", "exit_price_source",
              "exit_reason_source", "pre_label_exit_reason"):
        assert back[k] == notes[k], k
    assert back["closed_reason"].startswith("(symbol, side) confirmed absent")


# ── Venue order identity (manager-folded FIX-SA-02: bybit_2, real money) ──────
# Real ids, sizes and prices from the 2026-09-29 pull. Trade 5697 (bybit_2 XRPUSDT
# short 55.6, reconciler_filled): its own sl order filled 55.6 at 1.4416. Trade 4863
# (bybit_2 BTCUSDT long 0.005, netting_attributed): its own tp order filled 0.005 at
# 75699.9. Timestamps are shifted to "now" because the sweep only looks back 14 days
# (5697 closed 2026-09-14, 4863 on 2026-08-21); the gap between fill and close is
# kept (~2.5 min, ~3 min).
_T5697 = {"id": 5697, "account_id": "bybit_2", "symbol": "XRPUSDT", "direction": "short",
          "position_size": 55.6, "exit_reason": "reconciler_filled",
          "sl_order_id": "9cd6763c-656c-459b-ba35-93a8f6d97bb8",
          "tp_order_id": "60cdc0f7-ab92-4f45-b2eb-eb7075d5124d",
          "setup_type": "xrp_pullback_2h",
          "notes": '{"exit_price_source": "bybit_closed_pnl", "exit_reason_source": "unresolved"}'}
_T4863 = {"id": 4863, "account_id": "bybit_2", "symbol": "BTCUSDT", "direction": "long",
          "position_size": 0.005, "exit_reason": "netting_attributed",
          "sl_order_id": "59de5fda-5e66-43a8-b2a2-e9b11890c2ec",
          "tp_order_id": "2de01b61-1452-4f20-bcdb-7416da68539c",
          "setup_type": "ict_scalp_5m",
          "notes": '{"netting_attribution_basis": "leg_gone", "exit_price_source": "candle_at_close"}'}


def _ago(**kw):
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(**kw)).isoformat()


def _fill(exec_id, acct, sym, side, price, qty, when, oid):
    return (exec_id, acct, sym, side, price, qty, when, oid)


_REAL_FILLS = [
    _fill("b1734a58-3bf2-5f3c-85b4-6f064ad5c034", "bybit_2", "XRP/USDT:USDT", "buy",
          1.4416, 55.6, _ago(hours=2, minutes=2, seconds=30),
          "9cd6763c-656c-459b-ba35-93a8f6d97bb8"),
    _fill("af896b68-5d31-529e-b3c6-a1e4c445fd14", "bybit_2", "BTC/USDT:USDT", "sell",
          75699.9, 0.005, _ago(hours=2, minutes=3),
          "2de01b61-1452-4f20-bcdb-7416da68539c"),
]


def _run_bracket_sweep(tmp_path, rows, fills=None, *, mutate_before_write=None):
    fills = _REAL_FILLS if fills is None else fills
    jp, fp = tmp_path / "j.db", tmp_path / "f.db"
    c = sqlite3.connect(jp)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT, "
              "direction TEXT, position_size REAL, exit_reason TEXT, notes TEXT, "
              "sl_order_id TEXT, tp_order_id TEXT, setup_type TEXT, status TEXT, "
              "is_backtest INTEGER, closed_at TEXT, created_at TEXT)")
    for r in rows:
        c.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,'closed',0,?,?)",
                  (r["id"], r["account_id"], r["symbol"], r["direction"],
                   r["position_size"], r["exit_reason"], r["notes"], r["sl_order_id"],
                   r["tp_order_id"], r["setup_type"], _ago(hours=2), _ago(days=3)))
    c.commit()
    c.close()
    f = sqlite3.connect(fp)
    f.execute("CREATE TABLE exchange_fills (exec_id TEXT, account_id TEXT, symbol TEXT, "
              "side TEXT, price REAL, qty REAL, exec_time TEXT, order_id TEXT)")
    f.executemany("INSERT INTO exchange_fills VALUES (?,?,?,?,?,?,?,?)", fills)
    f.commit()
    f.close()

    class _DB:
        def __init__(self):
            self.n = 0

        def connect(self):
            self.n += 1
            # The 2nd connect is the conditional write: let a test change the
            # row in between, the way a concurrent operator mark would.
            if self.n == 2 and mutate_before_write:
                mc = sqlite3.connect(jp)
                mutate_before_write(mc)
                mc.commit()
                mc.close()
            return sqlite3.connect(jp)

        def update_trade(self, *a, **k):  # must not be used: it cannot guard
            raise AssertionError("the sweep must write through its guarded UPDATE")

    s = om._sweep_exit_label_from_bracket_order(
        _DB(), fills_conn_factory=lambda: sqlite3.connect(fp))
    rc = sqlite3.connect(jp)
    rc.row_factory = sqlite3.Row
    out = {r["id"]: dict(r) for r in rc.execute("SELECT * FROM trades")}
    rc.close()
    return s, out


def test_bybit2_stop_order_fill_relabels_reconciler_filled(tmp_path):
    s, out = _run_bracket_sweep(tmp_path, [_T5697])
    assert out[5697]["exit_reason"] == "sl"
    n = json.loads(out[5697]["notes"])
    assert n["exit_reason_source"] == "venue_bracket_order"
    assert n["pre_label_exit_reason"] == "reconciler_filled"
    assert n["exit_price_source"] == "bybit_closed_pnl"   # nothing else touched
    assert s["relabelled"] == 1


def test_bybit2_target_order_fill_relabels_netting_attributed(tmp_path):
    s, out = _run_bracket_sweep(tmp_path, [_T4863])
    assert out[4863]["exit_reason"] == "tp"
    assert json.loads(out[4863]["notes"])["pre_label_exit_reason"] == "netting_attributed"


def test_partial_target_then_close_by_another_order_is_not_labelled_tp(tmp_path):
    """REVIEW-14106 (b). No such row exists in the 2026-09-29 pull (0 of 236 rows
    whose bracket order filled), so this plants it on trade 4863's real shape: the
    target order fills 0.002 of 0.005, then a different order closes the rest."""
    fills = [
        _fill("p1", "bybit_2", "BTC/USDT:USDT", "sell", 75699.9, 0.002,
              _ago(hours=2, minutes=30), "2de01b61-1452-4f20-bcdb-7416da68539c"),
        _fill("p2", "bybit_2", "BTC/USDT:USDT", "sell", 75510.0, 0.003,
              _ago(hours=2, minutes=3), "operator-flatten-order"),
    ]
    s, out = _run_bracket_sweep(tmp_path, [_T4863], fills=fills)
    assert out[4863]["exit_reason"] == "netting_attributed"
    assert s["no_bracket_fill"] == 1 and s["relabelled"] == 0


def test_target_filled_whole_position_then_sibling_fill_still_tp(tmp_path):
    """Rule 2: the target order filled the whole 0.005; a netting sibling's exit
    fill lands after it. The trade's own target still ended it."""
    fills = list(_REAL_FILLS) + [
        _fill("sib", "bybit_2", "BTC/USDT:USDT", "sell", 75701.0, 0.004,
              _ago(hours=2, minutes=2), "sibling-order"),
    ]
    s, out = _run_bracket_sweep(tmp_path, [_T4863], fills=fills)
    assert out[4863]["exit_reason"] == "tp"


def test_concurrent_operator_mark_is_not_overwritten(tmp_path):
    """REVIEW-14106 (a): the reason is re-checked inside the UPDATE."""
    def _operator_marks(c):
        c.execute("UPDATE trades SET exit_reason='operator_flatten_reconciled' WHERE id=5697")
    s, out = _run_bracket_sweep(tmp_path, [_T5697], mutate_before_write=_operator_marks)
    assert out[5697]["exit_reason"] == "operator_flatten_reconciled"
    assert s["lost_race"] == 1 and s["relabelled"] == 0


def test_no_bracket_fill_leaves_the_row(tmp_path):
    s, out = _run_bracket_sweep(tmp_path, [_T5697], fills=[])
    assert out[5697]["exit_reason"] == "reconciler_filled" and s["no_bracket_fill"] == 1


def test_a_real_reason_is_not_relabelled_by_order_identity(tmp_path):
    s, out = _run_bracket_sweep(tmp_path, [dict(_T5697, exit_reason="sl_cross")])
    assert out[5697]["exit_reason"] == "sl_cross" and s["scanned"] == 0


def test_unreadable_store_is_not_no_fill(tmp_path):
    jp = tmp_path / "j2.db"
    c = sqlite3.connect(jp)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT, "
              "direction TEXT, position_size REAL, exit_reason TEXT, notes TEXT, "
              "sl_order_id TEXT, tp_order_id TEXT, setup_type TEXT, status TEXT, "
              "is_backtest INTEGER, closed_at TEXT, created_at TEXT)")
    c.execute("INSERT INTO trades VALUES (1,'bybit_2','XRPUSDT','short',1,'reconciler_filled',"
              "'{}','a','b','x','closed',0,datetime('now'),datetime('now'))")
    c.commit()
    c.close()

    class _DB:
        def connect(self):
            return sqlite3.connect(jp)

    s = om._sweep_exit_label_from_bracket_order(_DB(), fills_conn_factory=lambda: None)
    assert s["store_unreadable"] == 1 and s["no_bracket_fill"] == 0


_F = lambda *xs: [{"order_id": o, "qty": q} for o, q in xs]  # noqa: E731


@pytest.mark.parametrize("sl,tp,fills,size,want", [
    ("s", "t", _F(("s", 10)), 10, "sl"),                    # last fill on stop
    ("s", "t", _F(("t", 10)), 10, "tp"),                    # last fill on target
    ("s", "t", _F(("t", 4), ("x", 6)), 10, None),           # partial tp, other close
    ("s", "t", _F(("t", 10), ("x", 3)), 10, "tp"),          # full tp, sibling after
    ("s", "t", _F(("t", 4), ("s", 6)), 10, "sl"),           # partial tp, stop last
    ("s", None, _F(("x", 10)), 10, None),                   # no bracket fill
    (None, None, _F(("s", 10)), 10, None),                  # no bracket ids
    ("s", "t", [], 10, None),                               # no fills
])
def test_bracket_leg_from_exit_fills(sl, tp, fills, size, want):
    assert om.bracket_leg_from_exit_fills(sl, tp, fills, size) == want


def test_venue_bracket_order_is_measured():
    from src.runtime import provenance as prov
    assert prov.classify("venue_bracket_order", "exit_reason_source") == prov.MEASURED


def test_backfill_apply_does_not_overwrite_a_reason_changed_since_plan(tmp_path):
    """REVIEW-14106 (a), backfill side: plan() saw reconciler_filled, an operator
    marked the row before --apply ran; apply() must leave it and not count it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bfx", "scripts/ops/backfill_exit_labels.py")
    bfx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bfx)
    c = sqlite3.connect(tmp_path / "j.db")
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, exit_reason TEXT, notes TEXT)")
    c.execute("INSERT INTO trades VALUES (5697, 'operator_flatten_reconciled', '{}')")
    c.execute("INSERT INTO trades VALUES (4863, 'netting_attributed', '{}')")
    c.commit()
    planned = [
        {"id": 5697, "action": "relabel", "basis": "measured",
         "source": "venue_bracket_order", "new_reason": "sl",
         "old_reason": "reconciler_filled"},
        {"id": 4863, "action": "relabel", "basis": "measured",
         "source": "venue_bracket_order", "new_reason": "tp",
         "old_reason": "netting_attributed"},
    ]
    assert bfx.apply(c, planned) == 1
    got = dict(c.execute("SELECT id, exit_reason FROM trades").fetchall())
    assert got == {5697: "operator_flatten_reconciled", 4863: "tp"}
