"""Pairs-leg placement facts — the half that made the hedge-mode row gradeable.

WHY (measured 2026-08-30): `OI-20260830-BYBIT-HEDGE-MODE-ARMED-BUT-UNEXERCISED`
clears only on a `pairs_soak` `open` whose legs carry a hedge `position_idx` AND
that faced a concurrent directional position. NEITHER was recorded — `position_idx`
occurred 0 times across 400 live rows and nothing recorded directional exposure —
so the criterion was unsatisfiable however long the sleeve ran.

The ORDER PATH was already correct: `_place_pair` -> `execute_pkg` ->
`_submit_order` -> `apply_position_idx`. `apply_position_idx` RETURNS the
resolved book and the call site discarded it. These tests pin that it is now
carried out, and — the load-bearing one — that carrying it out changed nothing
about what gets placed.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.runtime import bybit_position_mode as bpm
from src.units.strategies import pairs_executor as px


class _Leg:
    def __init__(self, symbol, direction):
        self.symbol, self.direction = symbol, direction


# --- the wire fact is carried out, with its state ---------------------------


def test_the_sent_book_is_recorded_with_the_state_that_explains_it(monkeypatch):
    monkeypatch.setenv("BYBIT_HEDGE_MODE_SYMBOLS", "bybit_1:SOLUSDT")
    kwargs, observed = {}, {}
    res = bpm.apply_position_idx(kwargs, "bybit_1", "SOLUSDT", "short")
    observed["position_idx"] = res.idx
    observed["position_idx_state"] = res.state
    row = px._leg_placement_row(_Leg("SOLUSDT", "short"), "present", observed,
                                placed=True, trade_id="t1")
    assert kwargs["positionIdx"] == 2, "hedge_short is book 2"
    assert row["position_idx"] == 2
    assert row["position_idx_state"] == "hedge_short"
    assert row["directional_open"] == "present"
    assert row["placed"] is True


def test_a_null_idx_is_never_ambiguous():
    """⚠️ THE distinction this field exists for. `one_way` (absent kwarg is
    CORRECT) and `unresolved` (hedge declared, book unknown, so Bybit REFUSES
    the order) BOTH carry idx None. A bare int could not tell them apart, and
    they are opposite outcomes."""
    one_way = px._leg_placement_row(
        _Leg("BTCUSDT", "long"), "absent",
        {"position_idx": None, "position_idx_state": "one_way"},
        placed=True, trade_id="t")
    unresolved = px._leg_placement_row(
        _Leg("BTCUSDT", "long"), "absent",
        {"position_idx": None, "position_idx_state": "unresolved"},
        placed=True, trade_id="t")
    assert one_way["position_idx"] is unresolved["position_idx"] is None
    assert one_way["position_idx_state"] != unresolved["position_idx_state"]


def test_an_order_that_never_reached_the_wire_omits_the_keys_rather_than_guessing():
    """Absent is a third thing again — not one_way, not unresolved."""
    row = px._leg_placement_row(_Leg("MGC", "long"), "unreadable", {},
                                placed=False, trade_id=None)
    assert "position_idx" not in row and "position_idx_state" not in row
    assert row["placed"] is False and "trade_id" not in row


# --- the directional read: three states, never collapsed --------------------


def _journal(tmp_path, rows):
    db = tmp_path / "j.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE trades (account_id TEXT, symbol TEXT, "
                  "status TEXT, strategy_name TEXT, setup_type TEXT, "
                  "is_backtest INTEGER)")
        c.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?)", rows)
    return str(db)


def test_a_directional_position_is_present(tmp_path):
    db = _journal(tmp_path, [("bybit_1", "SOLUSDT", "open",
                              "trend_donchian_sol_4h", "donchian", 0)])
    assert px._directional_open_state("bybit_1", "SOLUSDT", db) == "present"


def test_our_own_pairs_legs_are_not_directional(tmp_path):
    """A pairs leg is not a directional book to net against. Counting one would
    make every pair look at-risk of stranding and the field meaningless."""
    db = _journal(tmp_path, [
        ("bybit_1", "SOLUSDT", "open", "pairs_sol_eth_a", "pairs", 0),
        ("bybit_1", "SOLUSDT", "open", "other", "pairs_entry", 0),
    ])
    assert px._directional_open_state("bybit_1", "SOLUSDT", db) == "absent"


@pytest.mark.parametrize("row,why", [
    (("bybit_1", "SOLUSDT", "closed", "trend_donchian_sol_4h", "donchian", 0),
     "a CLOSED directional row is not a live book"),
    (("bybit_1", "ETHUSDT", "open", "trend_donchian_eth_4h", "donchian", 0),
     "a different SYMBOL nets against a different book"),
    (("bybit_2", "SOLUSDT", "open", "trend_donchian_sol_4h", "donchian", 0),
     "a different ACCOUNT nets nothing here"),
    (("bybit_1", "SOLUSDT", "open", "trend_donchian_sol_4h", "donchian", 1),
     "a backtest row is not a live position"),
])
def test_scoping_of_the_directional_read(tmp_path, row, why):
    db = _journal(tmp_path, [row])
    assert px._directional_open_state("bybit_1", "SOLUSDT", db) == "absent", why


def test_an_unreadable_journal_is_never_absent(tmp_path):
    """⚠️ 'We could not look' and 'there was nothing there' are opposite, and
    only the second makes a clean open meaningful. Fail-loud, never quiet."""
    assert px._directional_open_state("bybit_1", "SOLUSDT",
                                      str(tmp_path / "nope.db")) == "unreadable"
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database at all")
    assert px._directional_open_state("bybit_1", "SOLUSDT", str(broken)) == "unreadable"


def test_the_pairs_predicate_is_imported_not_reimplemented():
    """order_monitor's own docstring says two copies could drift into
    disagreeing about who owns a row — the seam its alarm came from. So this
    must be the SAME function object, not a lookalike."""
    import inspect

    from src.runtime.order_monitor import _is_pairs_sleeve_row
    src = inspect.getsource(px._directional_open_state)
    assert "_is_pairs_sleeve_row" in src
    assert "startswith" not in src, "re-derived the predicate instead of importing it"
    assert _is_pairs_sleeve_row({"setup_type": "pairs", "strategy_name": ""}) is True


# --- the safety proof: the ORDER PATH is unchanged --------------------------


def test_observed_is_write_only_and_cannot_change_what_is_placed():
    """THE claim this unit rests on. `observed` must never be read or branched
    on inside the wire path — otherwise an observability out-param could alter a
    live order. Checked STRUCTURALLY against the real source rather than by
    asserting a flag, because the flag version would be vacuous."""
    import ast
    import inspect

    from src.units.accounts import execute as ex

    fn = [n for n in ast.walk(ast.parse(inspect.getsource(ex)))
          if isinstance(n, ast.FunctionDef) and n.name == "_submit_order"]
    assert fn, "the wire boundary vanished — wiring regression"
    for node in ast.walk(fn[0]):
        # The ONLY permitted shapes are `observed is not None` and
        # `observed[...] = ...`. Anything else is a read that could branch.
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) \
                and node.left.id == "observed":
            assert all(isinstance(o, (ast.Is, ast.IsNot)) for o in node.ops), \
                "observed compared by value — that is a branch on caller data"
        if isinstance(node, ast.If):
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) \
                        and sub.value.id == "observed":
                    raise AssertionError("branched on an observed VALUE")


def test_every_pre_existing_caller_is_unchanged_by_default():
    """`observed` defaults to None on both functions, so the ~dozen existing
    callers place byte-for-byte what they placed before."""
    import inspect

    from src.units.accounts.execute import _submit_order, execute_pkg
    for fn in (_submit_order, execute_pkg):
        assert inspect.signature(fn).parameters["observed"].default is None


# --- the thread is CONNECTED, end to end ------------------------------------


def _decision():
    return px.PairDecision(
        event="open", pair="SOLUSDT/ETHUSDT",
        soak={"pairs_group_id": "pair-test", "direction": "short_spread",
              "entry_spread": 1.0, "stop_spread": 2.0, "z": 2.1, "beta": 0.25},
        legs=[px.LegOrder("SOLUSDT", "short", 2.3, 105.0, 210.0, 52.5),
              px.LegOrder("ETHUSDT", "long", 0.02, 2457.0, 1228.0, 4914.0)],
    )


def test_place_pair_carries_the_wire_fact_all_the_way_out(monkeypatch, tmp_path):
    """THE wiring test. A stubbed `execute_pkg` writes into the `observed` dict
    exactly as the real `_submit_order` does; `_place_pair` must surface it.

    This is the test that fails if anyone drops `observed=observed` from the
    `execute_pkg` call — a regression that is otherwise SILENT, because the
    soak keeps writing rows and they simply lose the field the whole unit
    exists to record.
    """
    db = _journal(tmp_path, [("bybit_1", "SOLUSDT", "open",
                              "trend_donchian_sol_4h", "donchian", 0)])
    monkeypatch.setattr(px, "_journal_db_path", lambda: db)
    monkeypatch.setattr("src.core.coordinator._log_new_order_package", lambda pkg: None)

    seen = {}

    def _fake_execute_pkg(pkg, account_cfg, *, exchange_client=None,
                          qty_override=None, observed=None, **kw):
        seen[pkg.symbol] = observed
        assert observed is not None, "_place_pair must pass an observed dict"
        idx = 2 if pkg.symbol == "SOLUSDT" else 1
        observed["position_idx"] = idx
        observed["position_idx_state"] = "hedge_short" if idx == 2 else "hedge_long"
        return f"tid-{pkg.symbol}"

    monkeypatch.setattr("src.units.accounts.execute.execute_pkg", _fake_execute_pkg)

    res = px._place_pair(object(), {"account_id": "bybit_1", "exchange": "bybit"},
                         {"name": "pairs_sol_eth", "symbol_a": "SOLUSDT",
                          "symbol_b": "ETHUSDT"},
                         _decision(), "1h")

    assert res["placed"] is True
    lp = {r["symbol"]: r for r in res["leg_placement"]}
    assert set(lp) == {"SOLUSDT", "ETHUSDT"}, "both legs recorded, not just one"
    assert lp["SOLUSDT"]["position_idx"] == 2
    assert lp["SOLUSDT"]["position_idx_state"] == "hedge_short"
    assert lp["ETHUSDT"]["position_idx"] == 1
    # The directional read is per-SYMBOL: SOL has a donchian book, ETH does not.
    assert lp["SOLUSDT"]["directional_open"] == "present"
    assert lp["ETHUSDT"]["directional_open"] == "absent"
    assert lp["SOLUSDT"]["trade_id"] == "tid-SOLUSDT"


def test_a_failed_second_leg_still_reports_both(monkeypatch, tmp_path):
    """An `open_failed` row that listed only the leg that worked would
    under-report the very event it exists to describe."""
    monkeypatch.setattr(px, "_journal_db_path", lambda: str(tmp_path / "absent.db"))
    monkeypatch.setattr("src.core.coordinator._log_new_order_package", lambda pkg: None)
    monkeypatch.setattr(px, "_unwind_legs", lambda *a, **k: [])
    monkeypatch.setattr(px, "_alert_partial_placement", lambda *a, **k: None)

    def _boom(pkg, account_cfg, *, exchange_client=None, qty_override=None,
              observed=None, **kw):
        if pkg.symbol == "ETHUSDT":
            raise RuntimeError("venue refused")
        observed["position_idx"] = 2
        observed["position_idx_state"] = "hedge_short"
        return "tid-a"

    monkeypatch.setattr("src.units.accounts.execute.execute_pkg", _boom)
    res = px._place_pair(object(), {"account_id": "bybit_1", "exchange": "bybit"},
                         {"name": "pairs_sol_eth", "symbol_a": "SOLUSDT",
                          "symbol_b": "ETHUSDT"},
                         _decision(), "1h")
    assert res["placed"] is False
    lp = {r["symbol"]: r for r in res["leg_placement"]}
    assert set(lp) == {"SOLUSDT", "ETHUSDT"}
    assert lp["SOLUSDT"]["placed"] is True
    assert lp["ETHUSDT"]["placed"] is False, "the failed leg is recorded, not dropped"
    # An unreadable journal must not silently read as "no directional position".
    assert lp["ETHUSDT"]["directional_open"] == "unreadable"
