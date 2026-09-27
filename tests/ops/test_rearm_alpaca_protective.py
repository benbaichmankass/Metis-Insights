"""`rearm_alpaca_protective.py` — every refusal, the happy path, and the
cancel→place failure branches, against a stateful fake of the Alpaca REST API.

The case the action exists for (PI-20260926-HJPL5ABP-0001): alpaca_paper SPY,
net 19 long, ONE resting GTC OCO sized 11, one open journal row (trade 6131,
8 shares, sl 760.63928571 / tp 840.652575).
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "rearm_alpaca_protective",
        _ROOT / "scripts" / "ops" / "rearm_alpaca_protective.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ra = _load()

ROW_6131 = {"id": 6131, "symbol": "SPY", "direction": "long", "position_size": 8.0,
            "stop_loss": 760.63928571, "take_profit_1": 840.652575,
            "strategy_name": "spy_pullback_1h", "created_at": "2026-09-24T13:37:54Z"}


def _oco(qty=11, stop=730.10, limit=820.55, side="sell", ids=("6682fcfd", "8b4956ce")):
    """An Alpaca OCO as the nested open-orders read returns it (parent + leg)."""
    return [
        {"id": ids[0], "symbol": "SPY", "side": side, "type": "limit",
         "order_class": "oco", "status": "new", "qty": str(qty),
         "limit_price": str(limit), "time_in_force": "gtc"},
        {"id": ids[1], "symbol": "SPY", "side": side, "type": "stop",
         "order_class": "oco", "status": "held", "qty": str(qty),
         "stop_price": str(stop), "time_in_force": "gtc"},
    ]


class FakeAlpaca:
    """Minimal stateful Alpaca: positions, open orders, cancel, OCO place."""

    def __init__(self, *, qty=19.0, side="long", price=800.0, orders=None,
                 held=11.0, pos_fail=False, orders_fail=False,
                 place_fail=0, restore_fail=False, cancel_sticks=False,
                 clock_open=False):
        self.qty, self.side, self.price = qty, side, price
        self.orders: List[Dict[str, Any]] = [dict(o) for o in (orders or [])]
        self.status: Dict[str, str] = {o["id"]: o["status"] for o in self.orders}
        self.held = held
        self.pos_fail, self.orders_fail = pos_fail, orders_fail
        self.place_fail = place_fail          # number of POSTs to refuse
        self.restore_fail = restore_fail
        self.cancel_sticks = cancel_sticks    # DELETE accepted, stays pending_cancel
        self.clock_open = clock_open
        self.posts: List[Dict[str, Any]] = []
        self.deletes: List[str] = []
        self._n = 0

    # the client surface the script uses
    def _open_orders_for_symbol(self, sym):
        if self.orders_fail:
            return None
        return [dict(o) for o in self.orders if self.status[o["id"]] not in
                ("canceled", "filled", "rejected")]

    def _request(self, method, path, body=None):
        if path.startswith("/v2/positions/"):
            if self.pos_fail:
                return {"retCode": -1, "retMsg": "network"}
            if self.qty <= 0:
                return {"retCode": 404, "retMsg": "position does not exist"}
            return {"retCode": 0, "result": {
                "qty": str(self.qty), "side": self.side,
                "qty_available": str(self.qty - self.held),
                "current_price": str(self.price), "avg_entry_price": "790"}}
        if path == "/v2/clock":
            return {"retCode": 0, "result": {"is_open": self.clock_open}}
        if method == "DELETE":
            oid = path.rsplit("/", 1)[1]
            self.deletes.append(oid)
            if self.cancel_sticks:
                self.status[oid] = "pending_cancel"
            else:
                self.status[oid] = "canceled"
                self._recompute_held()
            return {"retCode": 0, "result": {}}
        if method == "GET" and path.startswith("/v2/orders/"):
            oid = path.rsplit("/", 1)[1]
            return {"retCode": 0, "result": {"id": oid, "status": self.status.get(oid, "new")}}
        if method == "POST" and path == "/v2/orders":
            self.posts.append(body)
            is_restore = len(self.posts) > 2
            if self.place_fail > 0 and not is_restore:
                self.place_fail -= 1
                return {"retCode": 403, "retMsg": "insufficient qty available"}
            if is_restore and self.restore_fail:
                return {"retCode": 403, "retMsg": "insufficient qty available"}
            self._n += 1
            pid, lid = f"new-{self._n}", f"new-{self._n}-stop"
            q = body["qty"]
            self.orders += [
                {"id": pid, "symbol": body["symbol"], "side": body["side"], "type": "limit",
                 "order_class": "oco", "status": "new", "qty": q,
                 "limit_price": body["take_profit"]["limit_price"], "time_in_force": "gtc"},
                {"id": lid, "symbol": body["symbol"], "side": body["side"], "type": "stop",
                 "order_class": "oco", "status": "held", "qty": q,
                 "stop_price": body["stop_loss"]["stop_price"], "time_in_force": "gtc"},
            ]
            self.status[pid], self.status[lid] = "new", "held"
            self._recompute_held()
            return {"retCode": 0, "result": {"id": pid, "status": "new"}}
        raise AssertionError(f"unexpected call {method} {path}")

    def _recompute_held(self):
        parents = [o for o in self.orders if o["type"] == "limit"
                   and self.status[o["id"]] not in ("canceled", "filled")]
        self.held = sum(float(o["qty"]) for o in parents)


@pytest.fixture
def run(monkeypatch):
    def _run(client, rows=(ROW_6131,), apply=False, exchange="alpaca"):
        monkeypatch.setattr(ra, "_load_account",
                            lambda a: {"account_id": a, "exchange": exchange})
        monkeypatch.setattr(ra, "_build_client", lambda cfg: client)
        monkeypatch.setattr(ra, "_open_rows",
                            lambda a, s: None if rows is None else [dict(r) for r in rows])
        monkeypatch.setattr(ra, "_sleep", lambda s: None)
        monkeypatch.setenv("ALPACA_REARM_SETTLE_S", "0")
        monkeypatch.setenv("ALPACA_PLACE_CONFIRM_S", "0")
        return ra.rearm("alpaca_paper", "spy", apply=apply)
    return _run


# ───────────────────────────────────────────────────────────── happy path
def test_dry_run_plans_one_oco_for_the_net_19_at_the_journal_levels(run):
    c = FakeAlpaca(orders=_oco())
    code, out = run(c)
    assert code == ra.EXIT_OK and out["state"] == "ready"
    assert out["plan"]["place"]["qty"] == "19"
    assert out["plan"]["place"]["stop_loss"]["stop_price"] == "760.64"
    assert out["plan"]["place"]["take_profit"]["limit_price"] == "840.65"
    assert out["plan"]["place"]["time_in_force"] == "gtc"
    assert out["plan"]["place"]["side"] == "sell"
    assert sorted(out["plan"]["cancel"]) == ["6682fcfd", "8b4956ce"]
    assert out["untracked_qty"] == 11.0
    assert out["level_rule"].startswith("single open row")
    assert c.posts == [] and c.deletes == []          # a dry run touches nothing


def test_apply_rearms_and_verifies_from_the_venue(run):
    c = FakeAlpaca(orders=_oco())
    code, out = run(c, apply=True)
    assert code == ra.EXIT_OK, out
    assert out["state"] == "rearmed"
    assert sorted(c.deletes) == ["6682fcfd", "8b4956ce"]
    assert len(c.posts) == 1 and c.posts[0]["qty"] == "19"
    assert out["coverage_after"]["stop_qty"] == 19.0
    assert out["coverage_after"]["target_qty"] == 19.0


def test_already_armed_is_a_noop(run):
    c = FakeAlpaca(orders=_oco(qty=19, stop=760.64, limit=840.65), held=19)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_OK and out["state"] == "already_armed"
    assert c.deletes == [] and c.posts == []


def test_works_with_the_market_closed_and_reports_it(run):
    c = FakeAlpaca(orders=_oco(), clock_open=False)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_OK and out["market_open"] is False


def test_naked_position_with_no_resting_orders_is_armed(run):
    c = FakeAlpaca(orders=[], held=0)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_OK and out["state"] == "rearmed"
    assert c.deletes == []


# ───────────────────────────────────────────────────────────── refusals
def test_refuses_a_non_protective_resting_order(run):
    stray = {"id": "stray", "symbol": "SPY", "side": "buy", "type": "market",
             "status": "new", "qty": "5"}
    c = FakeAlpaca(orders=_oco() + [stray])
    code, out = run(c, apply=True)
    assert code == ra.EXIT_REFUSED and out["state"] == "refused_non_protective_order"
    assert c.deletes == [] and c.posts == []


def test_refuses_a_same_side_limit_as_non_protective(run):
    """A BUY limit on a long is an ENTRY, not a take-profit."""
    entry = {"id": "e1", "symbol": "SPY", "side": "buy", "type": "limit",
             "status": "new", "qty": "3", "limit_price": "700"}
    code, out = run(FakeAlpaca(orders=_oco() + [entry]), apply=True)
    assert out["state"] == "refused_non_protective_order"


def test_position_read_failure_is_could_not_look_not_flat(run):
    c = FakeAlpaca(orders=_oco(), pos_fail=True)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_COULD_NOT_LOOK and out["state"] == "could_not_look"
    assert c.deletes == [] and c.posts == []


def test_orders_read_failure_is_could_not_look_not_none(run):
    c = FakeAlpaca(orders=_oco(), orders_fail=True)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_COULD_NOT_LOOK
    assert "NOT 'no orders" in out["detail"]


def test_journal_read_failure_is_could_not_look(run):
    code, out = run(FakeAlpaca(orders=_oco()), rows=None, apply=True)
    assert code == ra.EXIT_COULD_NOT_LOOK


def test_refuses_with_no_open_row(run):
    code, out = run(FakeAlpaca(orders=_oco()), rows=(), apply=True)
    assert code == ra.EXIT_REFUSED and out["state"] == "refused_no_open_row"


@pytest.mark.parametrize("field", ["stop_loss", "take_profit_1"])
def test_refuses_when_journal_levels_missing(run, field):
    row = dict(ROW_6131, **{field: None})
    code, out = run(FakeAlpaca(orders=_oco()), rows=(row,), apply=True)
    assert code == ra.EXIT_REFUSED and out["state"] == "refused_levels_missing"


def test_refuses_ambiguous_levels_across_rows(run):
    other = dict(ROW_6131, id=6000, position_size=11.0, stop_loss=730.1)
    c = FakeAlpaca(orders=_oco())
    code, out = run(c, rows=(other, ROW_6131), apply=True)
    assert code == ra.EXIT_REFUSED and out["state"] == "ambiguous_levels"
    assert c.deletes == []


def test_rows_agreeing_at_2dp_are_not_ambiguous(run):
    other = dict(ROW_6131, id=6000, position_size=11.0, stop_loss=760.641)
    code, out = run(FakeAlpaca(orders=_oco()), rows=(other, ROW_6131))
    assert code == ra.EXIT_OK and out["level_rule"].startswith("2 open rows")
    assert out["untracked_qty"] == 0.0


def test_refuses_direction_mismatch(run):
    # No resting orders, so the direction guard (not the stray-order guard,
    # which would catch the long-side sell legs first) is what refuses.
    c = FakeAlpaca(orders=[], side="short", held=0)
    code, out = run(c, apply=True)
    assert out["state"] == "refused_direction_mismatch" and c.posts == []


def test_refuses_journal_exceeding_position(run):
    code, out = run(FakeAlpaca(orders=[], qty=5, held=0), apply=True)
    assert out["state"] == "refused_journal_exceeds_position"


def test_refuses_levels_not_straddling_price(run):
    code, out = run(FakeAlpaca(orders=_oco(), price=750.0), apply=True)
    assert out["state"] == "refused_levels_wrong_side"


def test_refuses_fractional_position(run):
    code, out = run(FakeAlpaca(orders=[], qty=19.5, held=0), apply=True)
    assert out["state"] == "refused_fractional_position"


def test_refuses_non_alpaca_account(run):
    code, out = run(FakeAlpaca(orders=_oco()), exchange="bybit", apply=True)
    assert out["state"] == "refused_not_alpaca"


def test_flat_with_orders_is_refused_not_armed(run):
    code, out = run(FakeAlpaca(orders=_oco(), qty=0), apply=True)
    assert out["state"] == "refused_flat_with_orders"


# ─────────────────────────────────────────── the cancel → place gap
def test_place_refused_twice_restores_the_old_oco(run):
    c = FakeAlpaca(orders=_oco(), place_fail=2)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_FAILED_RESTORED
    assert out["state"] == "restored_old_protection"
    restore = c.posts[-1]
    assert restore["qty"] == "11"
    assert restore["stop_loss"]["stop_price"] == "730.10"
    assert restore["take_profit"]["limit_price"] == "820.55"


def test_place_refused_once_then_succeeds_on_retry(run):
    c = FakeAlpaca(orders=_oco(), place_fail=1)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_OK and out["state"] == "rearmed"
    assert len(out["place_attempts"]) == 2


def test_place_and_restore_both_refused_is_NAKED_exit_5(run):
    c = FakeAlpaca(orders=_oco(), place_fail=2, restore_fail=True)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_NAKED and out["state"] == "NAKED"


def test_unrestorable_old_shape_is_NAKED_not_restored(run):
    stop_only = [_oco()[1]]
    c = FakeAlpaca(orders=stop_only, place_fail=2)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_NAKED and out["state"] == "NAKED"


def test_pending_cancel_places_nothing(run):
    c = FakeAlpaca(orders=_oco(), cancel_sticks=True)
    code, out = run(c, apply=True)
    assert code == ra.EXIT_NAKED and out["state"] == "cancel_unsettled"
    assert c.posts == []
    assert set(out["settle"]["order_status"].values()) == {"pending_cancel"}


def test_orders_changing_between_grade_and_cancel_cancels_nothing(run, monkeypatch):
    c = FakeAlpaca(orders=_oco())
    reads = iter([_oco(), _oco() + [_oco(ids=("x", "y"))[0]]])
    monkeypatch.setattr(ra, "_read_orders", lambda client, sym: next(reads))
    code, out = run(c, apply=True)
    assert out["state"] == "refused_orders_changed" and c.deletes == []


# ───────────────────────────────────────────── journal read is real + read-only
def test_open_rows_reads_exact_symbol_read_only(monkeypatch, tmp_path):
    db = tmp_path / "j.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,"
        " direction TEXT, position_size REAL, stop_loss REAL, take_profit_1 REAL,"
        " strategy_name TEXT, created_at TEXT, status TEXT, is_backtest INTEGER);"
        "INSERT INTO trades VALUES (6131,'alpaca_paper','SPY','long',8,760.6,840.6,"
        "'spy_pullback_1h','2026-09-24','open',0);"
        "INSERT INTO trades VALUES (7,'alpaca_paper','SPYG','long',3,1,2,'x','t','open',0);"
        "INSERT INTO trades VALUES (8,'alpaca_paper','SPY','long',3,1,2,'x','t','open',1);")
    conn.commit()
    conn.close()
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "trade_journal_db_path", lambda: str(db))
    rows = ra._open_rows("alpaca_paper", "spy")
    assert [r["id"] for r in rows] == [6131]      # not SPYG, not the backtest row


# ═════════════════════════════════════════════════════════ ROW MODE
# The corrected SPY geometry (dry run, issue #13064): the 11 shares are open
# row 4347 (spy_trend_long_1d) and the resting OCO is ITS OWN protection. Only
# row 6131 is naked; qty_available is 8.
ROW_4347 = {"id": 4347, "account_id": "alpaca_paper", "symbol": "SPY", "direction": "long",
            "position_size": 11.0, "stop_loss": 744.47714286, "take_profit_1": 830.42638,
            "strategy_name": "spy_trend_long_1d", "status": "open", "is_backtest": 0,
            "created_at": "2026-08-03"}
ROW_6131_FULL = dict(ROW_6131, account_id="alpaca_paper", status="open", is_backtest=0)


def _oco_4347():
    return _oco(qty=11, stop=744.48, limit=830.43)


def test_net_mode_refuses_the_real_spy_geometry_as_ambiguous(run):
    """Pins the dry-run outcome of #13064: two tracked rows, different levels."""
    c = FakeAlpaca(orders=_oco_4347())
    code, out = run(c, rows=(ROW_4347, ROW_6131), apply=True)
    assert code == ra.EXIT_REFUSED and out["state"] == "ambiguous_levels"
    assert c.deletes == [] and c.posts == []


@pytest.fixture
def run_row(monkeypatch):
    def _run(client, row=ROW_6131_FULL, row_state="ok", apply=False, row_id=6131,
             symbol="spy", account="alpaca_paper"):
        monkeypatch.setattr(ra, "_load_account",
                            lambda a: {"account_id": a, "exchange": "alpaca"})
        monkeypatch.setattr(ra, "_build_client", lambda cfg: client)
        monkeypatch.setattr(ra, "_row_by_id",
                            lambda rid: (row_state, None if row is None else dict(row)))
        monkeypatch.setattr(ra, "_sleep", lambda s: None)
        monkeypatch.setenv("ALPACA_PLACE_CONFIRM_S", "0")
        return ra.rearm_row(account, symbol, row_id, apply=apply)
    return _run


def test_row_dry_run_plans_one_oco_for_the_row_and_cancels_nothing(run_row):
    c = FakeAlpaca(orders=_oco_4347())
    code, out = run_row(c)
    assert code == ra.EXIT_OK and out["state"] == "ready"
    body = out["plan"]["place"]
    assert (body["qty"], body["side"], body["time_in_force"]) == ("8", "sell", "gtc")
    assert body["stop_loss"]["stop_price"] == "760.64"
    assert body["take_profit"]["limit_price"] == "840.65"
    assert out["plan"]["cancel"] == []
    assert c.posts == [] and c.deletes == []


def test_row_apply_places_and_verifies_leaving_the_sibling_oco_alone(run_row):
    c = FakeAlpaca(orders=_oco_4347())
    code, out = run_row(c, apply=True)
    assert code == ra.EXIT_OK, out
    assert out["state"] == "rearmed_row"
    assert c.deletes == []                        # row 4347's OCO untouched
    assert len(c.posts) == 1 and c.posts[0]["qty"] == "8"
    assert out["coverage_after"]["stop_qty"] == 19.0   # 11 (4347) + 8 (6131)


def test_row_already_armed_is_a_noop(run_row):
    c = FakeAlpaca(orders=_oco_4347() + _oco(qty=8, stop=760.64, limit=840.65,
                                               ids=("a", "b")), held=19)
    code, out = run_row(c, apply=True)
    assert code == ra.EXIT_OK and out["state"] == "already_armed" and c.posts == []


def test_row_refuses_when_one_matching_leg_already_rests(run_row):
    stop_only = [_oco(qty=8, stop=760.64, ids=("a", "b"))[1]]
    c = FakeAlpaca(orders=_oco_4347() + stop_only, held=11)
    code, out = run_row(c, apply=True)
    assert out["state"] == "refused_row_protection_partially_rests" and c.posts == []


def test_row_refuses_when_shares_are_held(run_row):
    c = FakeAlpaca(orders=_oco_4347(), held=15)          # qty_available 4 < 8
    code, out = run_row(c, apply=True)
    assert code == ra.EXIT_REFUSED and out["state"] == "refused_qty_unavailable"
    assert c.posts == []


@pytest.mark.parametrize("patch,state", [
    ({"status": "closed"}, "refused_row_not_open"),
    ({"is_backtest": 1}, "refused_row_backtest"),
    ({"account_id": "alpaca_live"}, "refused_row_wrong_account"),
    ({"symbol": "QQQ"}, "refused_row_wrong_symbol"),
    ({"stop_loss": None}, "refused_levels_missing"),
    ({"take_profit_1": 0}, "refused_levels_missing"),
    ({"position_size": 8.5}, "refused_row_qty"),
    ({"direction": "short"}, "refused_direction_mismatch"),
    ({"position_size": 25.0}, "refused_row_exceeds_position"),
])
def test_row_refusals(run_row, patch, state):
    c = FakeAlpaca(orders=_oco_4347())
    code, out = run_row(c, row=dict(ROW_6131_FULL, **patch), apply=True)
    assert code == ra.EXIT_REFUSED and out["state"] == state, out
    assert c.posts == [] and c.deletes == []


def test_row_missing_is_refused(run_row):
    code, out = run_row(FakeAlpaca(orders=_oco_4347()), row=None, row_state="missing")
    assert out["state"] == "refused_row_missing"


@pytest.mark.parametrize("kw", [
    {"row_state": "could_not_look", "row": None},
])
def test_row_journal_unreadable_is_could_not_look(run_row, kw):
    code, out = run_row(FakeAlpaca(orders=_oco_4347()), **kw)
    assert code == ra.EXIT_COULD_NOT_LOOK


def test_row_position_unreadable_is_could_not_look(run_row):
    c = FakeAlpaca(orders=_oco_4347(), pos_fail=True)
    code, out = run_row(c, apply=True)
    assert code == ra.EXIT_COULD_NOT_LOOK and c.posts == []


def test_row_orders_unreadable_is_could_not_look(run_row):
    c = FakeAlpaca(orders=_oco_4347(), orders_fail=True)
    code, out = run_row(c, apply=True)
    assert code == ra.EXIT_COULD_NOT_LOOK and c.posts == []


def test_row_refuses_flat_position(run_row):
    code, out = run_row(FakeAlpaca(orders=[], qty=0, held=0), apply=True)
    assert out["state"] == "refused_flat"


def test_row_refuses_a_non_protective_order(run_row):
    stray = {"id": "s", "symbol": "SPY", "side": "buy", "type": "limit", "status": "new",
             "qty": "2", "limit_price": "700"}
    c = FakeAlpaca(orders=_oco_4347() + [stray])
    code, out = run_row(c, apply=True)
    assert out["state"] == "refused_non_protective_order" and c.posts == []


def test_row_refuses_overcover(run_row):
    """A differently-priced 8-share OCO already rests: 11+8+8 > 19."""
    other = _oco(qty=8, stop=750.0, limit=850.0, ids=("x", "y"))
    c = FakeAlpaca(orders=_oco_4347() + other, held=11)   # avail forced to 8
    code, out = run_row(c, apply=True)
    assert out["state"] == "refused_would_overcover" and c.posts == []


def test_row_refuses_levels_not_straddling_price(run_row):
    code, out = run_row(FakeAlpaca(orders=_oco_4347(), price=755.0), apply=True)
    assert out["state"] == "refused_levels_wrong_side"


def test_row_place_failure_changes_nothing(run_row):
    c = FakeAlpaca(orders=_oco_4347(), place_fail=1)
    code, out = run_row(c, apply=True)
    assert code == ra.EXIT_FAILED_RESTORED
    assert out["state"] == "place_failed_nothing_changed" and c.deletes == []


def test_row_by_id_is_read_only_and_real(monkeypatch, tmp_path):
    db = tmp_path / "j.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,"
        " direction TEXT, position_size REAL, stop_loss REAL, take_profit_1 REAL,"
        " strategy_name TEXT, created_at TEXT, status TEXT, is_backtest INTEGER);"
        "INSERT INTO trades VALUES (6131,'alpaca_paper','SPY','long',8,760.6,840.6,"
        "'spy_pullback_1h','2026-09-24','open',0);")
    conn.commit()
    conn.close()
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "trade_journal_db_path", lambda: str(db))
    assert ra._row_by_id(6131)[0] == "ok"
    assert ra._row_by_id(9999) == ("missing", None)


def test_cli_row_flag_routes_to_row_mode(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(ra, "rearm_row",
                        lambda a, s, r, apply, out: (seen.update(r=r, a=apply) or (0, {"ok": 1})))
    monkeypatch.setattr(ra, "rearm", lambda *a, **k: pytest.fail("net mode must not run"))
    assert ra.main(["--account", "alpaca_paper", "--symbol", "SPY", "--row", "6131"]) == 0
    assert seen == {"r": 6131, "a": False}


# ───────────────────────────────── wrapper banner matches the mode
def _banner(apply, row):
    import subprocess
    wrapper = _ROOT / "scripts" / "ops" / "rearm_alpaca_protective_action.sh"
    script = (f"source <(sed -n '/^rearm_banner()/,/^}}/p' {wrapper}); "
              f"rearm_banner '{apply}' '{row}' alpaca_paper SPY")
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, check=True).stdout


def test_row_mode_apply_banner_never_says_cancel():
    """Dispatch #13115 (row-mode apply) printed 'cancel the resting protective
    legs' although row mode cancels nothing."""
    out = _banner("true", "6131")
    assert "ROW MODE" in out and "cancels NOTHING" in out
    assert "cancel the resting" not in out


def test_net_mode_apply_banner_says_it_cancels():
    out = _banner("true", "")
    assert "NET MODE" in out and "cancel the resting protective legs" in out


@pytest.mark.parametrize("row,mode", [("6131", "ROW MODE"), ("", "NET MODE")])
def test_dry_run_banner_names_the_mode(row, mode):
    out = _banner("", row)
    assert "DRY-RUN" in out and mode in out and "cancel the resting" not in out
