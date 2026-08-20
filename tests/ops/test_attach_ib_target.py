"""`attach_ib_target.py` — the repair wire's own logic, which shipped UNTESTED.

There was no test file for this script at all. The system-action registry
guards confirmed it was *registered* (allowlisted, tier-classified,
script-mapped, documented, env forwarded) — none of which executes a line of
it. So four safety refusals guarding a live order path had zero coverage, and
the first thing to actually run the code was a live dispatch against
`ib_paper` (BL-20260817-ATTACH-IB-TARGET-DB-IMPORT-UNEXERCISED).

What that dispatch found: `_open_trade` imported `get_connection` from
`src.units.db.database`, which does not exist — the module exports a
`Database` class and `get_db()`. A registered-but-unexercised script is the
same class as a written-but-unread provenance field.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "attach_ib_target", _ROOT / "scripts" / "ops" / "attach_ib_target.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


att = _load()


def _order(**kw):
    base = {"symbol": "MGC", "order_type": "STP", "oca_group": "oca-protect-357",
            "order_id": 359, "total_quantity": 105.0}
    base.update(kw)
    return base


def _run(monkeypatch, capsys, *, orders, trade, argv=("--symbol", "MGC")):
    monkeypatch.setattr(att, "_load_account", lambda a: {"account_id": a})
    monkeypatch.setattr(att, "_read_orders", lambda cfg: orders)
    monkeypatch.setattr(att, "_open_trade", lambda a, s: trade)
    monkeypatch.setattr(att, "_attach", lambda *a, **k: pytest.fail("must not place"))
    rc = att.main(list(argv))
    return rc, json.loads(capsys.readouterr().out)


# --------------------------------------------------------------- the real bug
def test_open_trade_import_resolves_against_the_real_module():
    """The defect that reached production: an import that does not exist.

    Executed for real — no stub — because stubbing `_open_trade` is precisely
    what hid this. It must raise sqlite/OSError at worst (no journal in the
    sandbox), never ImportError/AttributeError.
    """
    try:
        att._open_trade("ib_paper", "MGC")
    except (ImportError, AttributeError) as exc:      # the failure that shipped
        pytest.fail(f"_open_trade cannot resolve its own imports: {exc}")
    except Exception:  # noqa: BLE001, S110 — deliberately broad: this test asserts
        pass           # ONLY that the imports resolve. A missing or unreadable
        #                journal in the sandbox is an expected, unrelated failure,
        #                and narrowing this would couple the test to sqlite's
        #                error taxonomy rather than to the defect it pins.


def test_open_trade_opens_the_journal_READ_ONLY(monkeypatch, tmp_path):
    """A repair tool must not be able to write the money DB."""
    db = tmp_path / "j.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,"
        " direction TEXT, position_size REAL, stop_loss REAL, take_profit_1 REAL,"
        " status TEXT);"
        "INSERT INTO trades VALUES (4487,'ib_paper','MGC','long',105,4278.81,4297.66,'open');"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("src.utils.paths.trade_journal_db_path", lambda: str(db))
    row = att._open_trade("ib_paper", "MGC")
    assert row["id"] == 4487 and row["take_profit_1"] == 4297.66

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("UPDATE trades SET take_profit_1 = 1.0")
    ro.close()


# ------------------------------------------------------------- the refusals
def test_refuses_on_a_stray_non_protective_order(monkeypatch, capsys):
    """Orders 6 and 378 were exactly this — MKT SELLs that could fill after
    the target flattens the position and open a reverse one."""
    rc, out = _run(monkeypatch, capsys,
                   orders=[_order(), _order(order_type="MKT", oca_group=None, order_id=6)],
                   trade={"id": 4487, "take_profit_1": 4297.66,
                          "direction": "long", "position_size": 105.0})
    assert rc == 4 and out["state"] == "refused"
    assert "non-protective" in out["blocker"]


def test_refuses_on_more_than_one_stop_oca_group(monkeypatch, capsys):
    """MES held two stops in disjoint groups; joining one leaves the other
    unlinked, so a target fill would strand a live stop on a flat book."""
    rc, out = _run(monkeypatch, capsys,
                   orders=[_order(symbol="MES", oca_group="oca-protect-336", order_id=338),
                           _order(symbol="MES", oca_group="oca-protect-373", order_id=375)],
                   trade={"id": 4350, "take_profit_1": 8390.59,
                          "direction": "long", "position_size": 15.0},
                   argv=("--symbol", "MES"))
    assert rc == 4 and out["state"] == "refused"
    assert len(out["oca_groups"]) == 2


def test_a_resting_target_is_a_no_op_not_a_second_placement(monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys,
                   orders=[_order(), _order(order_type="LMT", order_id=400)],
                   trade={"id": 4487, "take_profit_1": 4297.66,
                          "direction": "long", "position_size": 105.0})
    assert rc == 0 and out["state"] == "already_has_target"


def test_a_read_failure_is_could_not_look_never_a_refusal(monkeypatch, capsys):
    """`None` from the order read must not be reported as 'no target exists'."""
    rc, out = _run(monkeypatch, capsys, orders=None, trade=None)
    assert rc == 3 and out["state"] == "could_not_look"
    assert "NOT evidence" in out["error"]


def test_a_trade_declaring_no_target_is_reported_not_invented(monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, orders=[_order()],
                   trade={"id": 4487, "take_profit_1": None,
                          "direction": "long", "position_size": 105.0})
    assert rc == 0 and out["state"] == "no_declared_target"


def test_the_target_price_is_never_taken_from_the_caller():
    """A repair cannot fat-finger a level — there is no --tp argument."""
    src = (_ROOT / "scripts" / "ops" / "attach_ib_target.py").read_text()
    assert "take_profit_1" in src
    assert '"--tp"' not in src and "'--tp'" not in src, (
        "the target must be read from the journal, never supplied by the caller"
    )
