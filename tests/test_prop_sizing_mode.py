"""Prop ticket SIZING MODE — flat (current breakout_1) vs room (fresh accounts).

Operator decision 2026-09-27 ~11:12Z ("Current flat, room sizing next"),
declared in config/prop_rulesets/breakout.yaml ``sizing:`` and applied by
src/prop/prop_sizing.py from src/prop/breakout_executor.emit_prop_ticket.

The load-bearing test is the first one: the CURRENT account's tickets must be
byte-identical to what the code produced before room sizing existed. The golden
(tests/fixtures/breakout_1_flat_tickets_golden.json) was captured from
origin/main c097a1c with the unmodified code, against the real
config/accounts.yaml breakout_1 entry.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

import src.prop.breakout_executor as be
from src.prop import prop_sizing
from src.prop.breakout_ticket import render_ticket

_REPO = Path(__file__).resolve().parents[1]
_GOLDEN = _REPO / "tests" / "fixtures" / "breakout_1_flat_tickets_golden.json"
_RULESET = _REPO / "config" / "prop_rulesets" / "breakout.yaml"
_FIXED = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


class _FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401 — test clock
        return _FIXED


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    monkeypatch.setattr(be, "datetime", _FrozenDT)


def _breakout_1_cfg() -> dict:
    from src.config.accounts_loader import load_accounts_dict

    acct = dict(load_accounts_dict()["breakout_1"])
    acct["account_id"] = "breakout_1"
    return acct


def _emit(order: dict, acct: dict):
    seen: dict = {}
    tid = be.emit_prop_ticket(order, acct, timeframe="1h",
                              _emitter=lambda t: seen.setdefault("t", t))
    return tid, seen.get("t")


# --------------------------------------------------------------------------
# The current account is unchanged.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("gate_mode", ["off", "annotate", "enforce"])
def test_current_breakout_1_flat_tickets_byte_identical_to_pre_room_golden(gate_mode, monkeypatch):
    """breakout_1 as declared today (sizing.mode: flat) emits EXACTLY the
    tickets the pre-room code emitted: same Ticket fields, same $75 risk, same
    qty, same rendered text, byte for byte.

    The only permitted difference in the Ticket repr is the NEW
    ``risk_usd_override=None`` field on TicketConfig, which is stripped and
    asserted to be None — i.e. the override is provably unused.

    Run under every PROP_TICKET_RISK_GATE_MODE, ENFORCE included (the live VM
    reads `enforce`): the flat $75 ticket sits at its $75 cap, so the gate
    leaves it alone. The golden was captured under the default (annotate); the
    rendered cushion caveat is empty here (isolated journal, no snapshot) in
    every mode except `off`, which suppresses it, so `off` is compared on the
    Ticket only.
    """
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", gate_mode)
    assert prop_sizing.load_sizing_config(_RULESET).mode == prop_sizing.FLAT
    golden = json.loads(_GOLDEN.read_text())["cases"]
    assert len(golden) == 3
    acct = _breakout_1_cfg()
    for case in golden:
        _, t = _emit(dict(case["order"]), acct)
        assert t is not None, case["order"]
        assert t.cfg.risk_usd_override is None
        assert t.risk_usd == 75.0
        now_repr = repr(t).replace(", risk_usd_override=None", "")
        assert now_repr == case["ticket_repr"]
        rendered = render_ticket(t, now=_FIXED, account_id="breakout_1",
                                 ticket_id="prop-manual-golden")
        if gate_mode != "off":
            assert rendered == case["rendered"]


def test_flat_mode_reads_no_rule_distance(monkeypatch: pytest.MonkeyPatch):
    # Flat must not even LOOK at the live cushion — the flat path is the old path.
    from src.prop import prop_reconcile

    def _boom(*a, **k):
        raise AssertionError("flat sizing must not read compute_rule_distance")

    monkeypatch.setattr(prop_reconcile, "compute_rule_distance", _boom)
    d = prop_sizing.resolve("breakout_1", ruleset_path=_RULESET, risk_pct=1.5)
    assert d == prop_sizing.SizingDecision(mode="flat", cap_usd=75.0)


def test_declared_config_is_flat_now_room_k033_next():
    block = yaml.safe_load(_RULESET.read_text())["sizing"]
    assert block["mode"] == "flat"
    assert block["next_instance_mode"] == "room"
    assert block["room"]["k"] == 0.33
    assert block["room"]["min_risk_usd"] == 10
    cfg = prop_sizing.load_sizing_config(_RULESET)
    assert (cfg.k, cfg.min_risk_usd, cfg.on_cushion_unknown) == (0.33, 10.0, "skip")


# --------------------------------------------------------------------------
# Room mode (the next, fresh account).
# --------------------------------------------------------------------------
def _room_ruleset(tmp_path: Path) -> Path:
    data = yaml.safe_load(_RULESET.read_text())
    data["sizing"]["mode"] = data["sizing"]["next_instance_mode"]
    p = tmp_path / "prop_rulesets" / "breakout_room.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data))
    return p


def _rd(balance=5000.0, dd=300.0, daily=150.0, fresh="ok", ors="no_open_positions"):
    return {"balance": balance, "status_freshness": fresh, "after_open_risk_state": ors,
            "distance_to_dd_floor_after_open_risk_usd": dd,
            "distance_to_daily_loss_after_open_risk_usd": daily}


def test_room_formula_matches_the_sim():
    # min(1.5% x 5000, 0.33 x min(300,150)) = min(75, 49.5) = 49.5
    r, skip = prop_sizing.room_risk_usd(risk_pct_frac=0.015, balance_usd=5000.0,
                                        cushion_usd=150.0, k=0.33, min_risk_usd=10.0)
    assert (r, skip) == (49.5, None)
    # plenty of cushion -> capped at 1.5% of the LIVE balance, not the nominal
    r, _ = prop_sizing.room_risk_usd(risk_pct_frac=0.015, balance_usd=5400.0,
                                     cushion_usd=1000.0, k=0.33, min_risk_usd=10.0)
    assert r == 81.0
    # $24 of room (the 2026-09-27 live state) -> 7.92 < $10 -> skipped
    r, skip = prop_sizing.room_risk_usd(risk_pct_frac=0.015, balance_usd=4724.0,
                                        cushion_usd=24.0, k=0.33, min_risk_usd=10.0)
    assert r is None and "below the $10.00 minimum" in skip
    # negative cushion never produces negative risk
    r, skip = prop_sizing.room_risk_usd(risk_pct_frac=0.015, balance_usd=4690.0,
                                        cushion_usd=-10.0, k=0.33, min_risk_usd=10.0)
    assert r is None and skip


@pytest.mark.parametrize("rd,why", [
    (_rd(fresh="stale"), "stale"),
    (_rd(ors="stop_unknown"), "stop_unknown"),
    (_rd(daily=None), "daily-loss"),
    (_rd(dd=None), "DD-floor"),
])
def test_room_unknown_cushion_skips_never_falls_back_to_flat(tmp_path, rd, why):
    d = prop_sizing.resolve("breakout_1", ruleset_path=_room_ruleset(tmp_path),
                            risk_pct=1.5, rule_distance=rd)
    assert d.mode == "room" and d.risk_usd is None
    assert "cushion unknown" in d.skip_reason and why in d.skip_reason


def test_room_emits_room_sized_ticket(tmp_path, monkeypatch):
    rs = _room_ruleset(tmp_path)
    from src.prop import prop_reconcile
    monkeypatch.setattr(prop_reconcile, "compute_rule_distance",
                        lambda _a, *k, **kw: _rd(balance=5000.0, dd=300.0, daily=150.0))
    acct = _breakout_1_cfg()
    acct["backtest_ruleset"] = str(rs)
    order = {"symbol": "SOLUSDT", "direction": "long", "side": "Buy",
             "entry": 150.0, "sl": 145.0, "tp": 162.0, "strategy": "trend_donchian_sol_prop"}
    tid, t = _emit(order, acct)
    assert be.is_manual_fill_id(tid)
    assert t is not None and t.risk_usd == 49.5
    assert abs(t.qty_units - 49.5 / 5.0) < 1e-9
    text = render_ticket(t, now=_FIXED, account_id="breakout_1")
    assert "ROOM-SIZED" in text and "Do NOT scale this up" in text


def test_room_skip_is_journaled_not_pushed(tmp_path, monkeypatch):
    rs = _room_ruleset(tmp_path)
    from src.prop import prop_journal, prop_reconcile
    monkeypatch.setattr(prop_reconcile, "compute_rule_distance",
                        lambda _a, *k, **kw: _rd(balance=4724.0, dd=24.0, daily=141.72))
    rows = []
    monkeypatch.setattr(prop_journal, "record_ticket", lambda row: rows.append(row))
    acct = _breakout_1_cfg()
    acct["backtest_ruleset"] = str(rs)
    order = {"symbol": "ETHUSDT", "direction": "short", "side": "Sell",
             "entry": 2666.11, "sl": 2711.10, "tp": 2440.0, "strategy": "trend_donchian_eth_prop"}
    tid, t = _emit(order, acct)
    assert t is None                      # nothing pushed to the operator
    assert be.is_manual_fill_id(tid)
    assert [r["status"] for r in rows] == ["skipped"]
    assert rows[0]["meta"]["sizing_mode"] == "room"
    assert "below the $10.00 minimum" in rows[0]["message"]


def test_unknown_mode_refuses(tmp_path):
    data = yaml.safe_load(_RULESET.read_text())
    data["sizing"]["mode"] = "rooom"
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        prop_sizing.load_sizing_config(p)


# --------------------------------------------------------------------------
# Risk gate ENFORCE (operator 2026-09-27 ~08:40Z; PI-20260924-MQ3CDMU6-0002).
# --------------------------------------------------------------------------
from src.prop import prop_risk_gate as gate  # noqa: E402


@pytest.mark.parametrize("gm", ["off", "annotate"])
def test_gate_off_and_annotate_never_touch_size(gm):
    r = gate.enforce_ticket_cap(risk_usd=75.0, cap_usd=50.0, gate_mode=gm, sizing_mode="flat")
    assert (r["action"], r["risk_usd"]) == (gate.CAP_NOT_ENFORCED, 75.0)
    r = gate.enforce_ticket_cap(risk_usd=75.0, cap_usd=None, gate_mode=gm)
    assert (r["action"], r["risk_usd"]) == (gate.CAP_NOT_ENFORCED, 75.0)


def test_gate_enforce_flat_at_cap_unchanged_above_cap_resized_no_cap_refused():
    r = gate.enforce_ticket_cap(risk_usd=75.0, cap_usd=75.0, gate_mode="enforce", sizing_mode="flat")
    assert (r["action"], r["risk_usd"], r["cause"]) == (gate.CAP_UNCHANGED, 75.0, None)
    r = gate.enforce_ticket_cap(risk_usd=90.0, cap_usd=75.0, gate_mode="enforce", sizing_mode="flat")
    assert (r["action"], r["risk_usd"]) == (gate.CAP_RESIZED, 75.0)
    assert "exceeds the flat-mode cap $75.00" in r["cause"]
    r = gate.enforce_ticket_cap(risk_usd=75.0, cap_usd=None, gate_mode="enforce", sizing_mode="flat")
    assert (r["action"], r["risk_usd"]) == (gate.CAP_REFUSED, None) and r["cause"]


def test_gate_enforce_room_caps_at_room_formula():
    r = gate.enforce_ticket_cap(risk_usd=75.0, cap_usd=49.5, gate_mode="enforce", sizing_mode="room")
    assert (r["action"], r["risk_usd"]) == (gate.CAP_RESIZED, 49.5)
    r = gate.enforce_ticket_cap(risk_usd=49.5, cap_usd=49.5, gate_mode="enforce", sizing_mode="room")
    assert r["action"] == gate.CAP_UNCHANGED


def _ruleset_with(tmp_path: Path, **sizing_over) -> Path:
    data = yaml.safe_load(_RULESET.read_text())
    for k, v in sizing_over.items():
        if isinstance(v, dict):
            data["sizing"].setdefault(k, {}).update(v)
        else:
            data["sizing"][k] = v
    p = tmp_path / "prop_rulesets" / "breakout_x.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data))
    return p


_SOL = {"symbol": "SOLUSDT", "direction": "long", "side": "Buy",
        "entry": 150.0, "sl": 145.0, "tp": 162.0, "strategy": "trend_donchian_sol_prop"}


@pytest.mark.parametrize("gm,expected", [("off", 75.0), ("annotate", 75.0), ("enforce", 50.0)])
def test_executor_flat_ticket_over_its_cap_is_resized_only_under_enforce(tmp_path, monkeypatch, gm, expected):
    # A flat cap declared BELOW the formula's $75 (drift between the two) is
    # the case enforce exists for: only enforce cuts it, and says why.
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", gm)
    rows = []
    from src.prop import prop_journal
    monkeypatch.setattr(prop_journal, "record_ticket", lambda row: rows.append(row))
    acct = _breakout_1_cfg()
    acct["backtest_ruleset"] = str(_ruleset_with(tmp_path, flat={"max_risk_usd": 50}))
    _, t = _emit(dict(_SOL), acct)
    assert t is not None and t.risk_usd == expected
    emitted = [r for r in rows if r["status"] == "emitted"]
    assert len(emitted) == 1
    if gm == "enforce":
        assert emitted[0]["meta"]["risk_gate"]["action"] == "resized"
        assert abs(t.qty_units - 50.0 / 5.0) < 1e-9
    else:
        assert "meta" not in emitted[0]


def test_executor_enforce_refuses_when_no_cap_is_declared(tmp_path, monkeypatch):
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "enforce")
    data = yaml.safe_load(_RULESET.read_text())
    data["sizing"].pop("flat")
    p = tmp_path / "prop_rulesets" / "nocap.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data))
    rows = []
    from src.prop import prop_journal
    monkeypatch.setattr(prop_journal, "record_ticket", lambda row: rows.append(row))
    acct = _breakout_1_cfg()
    acct["backtest_ruleset"] = str(p)
    tid, t = _emit(dict(_SOL), acct)
    assert t is None and be.is_manual_fill_id(tid)
    assert [r["status"] for r in rows] == ["skipped"]
    assert rows[0]["meta"]["risk_gate"]["action"] == "refused"


def test_executor_room_mode_under_enforce_emits_room_size(tmp_path, monkeypatch):
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "enforce")
    from src.prop import prop_reconcile
    monkeypatch.setattr(prop_reconcile, "compute_rule_distance",
                        lambda _a, *k, **kw: _rd(balance=5000.0, dd=300.0, daily=150.0))
    acct = _breakout_1_cfg()
    acct["backtest_ruleset"] = str(_ruleset_with(tmp_path, mode="room"))
    _, t = _emit(dict(_SOL), acct)
    assert t is not None and t.risk_usd == 49.5
