"""E66 — an open prop position's CURRENT stop reaches the journal, and the rule
distance subtracts what open positions can still lose.

The planted scenario is the one MEASURED on breakout_1 on 2026-09-24 (E59,
docs/research/prop-state-2026-09-24.md §2): snapshot balance 4,784.78, equity
4,794.76 (uPnL +9.98), static floor 4,700.00, and fill #44, an ETH short
1.3 @ 2666.11 journaled with ``sl: null``. The operator later moved its stop
2736.00 -> 2711.10.

The expected figures are computed by hand here, not taken from the code:
    pnl at stop (short) = (2666.11 - 2711.10) * 1.3 = -58.487
    worst-case equity   = 4794.76 - 9.98 - 58.487   = 4726.293
    cushion after risk  = 4726.293 - 4700.00        = 26.293   (E59: "$26.29 gross")
    plain distance      = 4794.76 - 4700.00         = 94.76
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.prop import breakout_notify, prop_journal, prop_reconcile, prop_risk_gate
from src.prop.prop_report import ingest_report

ACCT = "breakout_1"


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    # The fill notification is irrelevant here and must not reach a network.
    monkeypatch.setattr(breakout_notify, "emit_prop_fill",
                        lambda fill: {"push": False, "telegram": False})
    return tmp_path


def _snapshot() -> dict:
    """The E59 snapshot (id 20), re-dated to now so freshness reads ``ok``."""
    return {"account_id": ACCT, "balance": 4784.78, "equity": 4794.76,
            "unrealized": 9.98, "realized_today": None,
            "reported_at": datetime.now(timezone.utc).isoformat()}


def _open_eth_short(**extra) -> int:
    report = {"account_id": ACCT, "symbol": "ETHUSDT", "direction": "short",
              "status": "open", "entry_price": 2666.11, "qty": 1.3,
              "reason": "SL 2736.00 TP 2416.00"}
    report.update(extra)
    return ingest_report(report)["id"]


# ── the positive control: the defect exists before the amend ─────────────

def test_fill_44_as_journaled_is_stop_unknown_never_zero_risk(isolated_db):
    _open_eth_short()  # exactly #44's shape: no sl field
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_risk_state"] == "stop_unknown"
    assert rd["after_open_risk_state"] == "stop_unknown"
    assert rd["open_loss_to_stop_usd"] is None, "unknown must not read as 0"
    assert rd["distance_to_dd_floor_after_open_risk_usd"] is None
    # The plain equity distance is unchanged: what E59 measured.
    assert rd["distance_to_dd_floor_usd"] == pytest.approx(94.76, abs=0.01)
    pos = rd["open_risk"]["positions"][0]
    assert pos["missing"] == ["sl"]


def test_a_75_ticket_against_an_unknown_stop_grades_unknown_not_within(
        isolated_db, monkeypatch):
    """The E59 D2 failure: $75 < $94.76 graded within_cushion. Not any more."""
    _open_eth_short()
    monkeypatch.setattr(prop_journal, "latest_account_status",
                        lambda _a: _snapshot())
    v = prop_risk_gate.grade_account_ticket_risk(ACCT, risk_usd=75.0)
    assert v["state"] == prop_risk_gate.UNKNOWN
    assert v["open_risk_state"] == "stop_unknown"
    assert "no journaled stop" in v["reason"]
    text = "\n".join(prop_risk_gate.caveat_lines(v))
    assert '"kind":"amend"' in text


# ── the amend path ───────────────────────────────────────────────────────

def test_amend_records_the_moved_stop_and_its_history(isolated_db):
    fid = _open_eth_short(sl=2736.00, tp=2416.00)
    out = ingest_report({"kind": "amend", "account_id": ACCT,
                         "symbol": "ETHUSD", "direction": "sell",  # venue + synonym
                         "sl": 2711.10})
    assert out["kind"] == "amend" and out["id"] == fid
    assert (out["sl_before"], out["sl"]) == (2736.00, 2711.10)
    assert out["tp"] == 2416.00, "an amend without tp must not clear the target"
    row = prop_journal.list_fills(account_id=ACCT)[0]
    assert row["sl"] == 2711.10 and row["tp"] == 2416.00
    assert row["entry_price"] == 2666.11 and row["qty"] == 1.3, \
        "an amend must not touch the position's other fields"
    import json
    trail = json.loads(row["amendments"])
    assert [(a["sl_before"], a["sl"]) for a in trail] == [(2736.00, 2711.10)]


def test_known_stop_subtracts_the_loss_to_stop_fill_44_figures(isolated_db):
    _open_eth_short()
    ingest_report({"kind": "amend", "account_id": ACCT, "symbol": "ETHUSDT",
                   "direction": "short", "sl": 2711.10})
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_risk_state"] == "measured"
    assert rd["open_loss_to_stop_usd"] == pytest.approx(58.487, abs=1e-6)
    assert rd["distance_to_dd_floor_after_open_risk_usd"] == pytest.approx(
        26.293, abs=1e-3)
    # Daily half stays unknown because realized_today was never reported (E59).
    assert rd["distance_to_daily_loss_after_open_risk_usd"] is None
    v = prop_risk_gate.grade_ticket_risk(
        risk_usd=75.0,
        distance_to_dd_floor_usd=rd["distance_to_dd_floor_after_open_risk_usd"],
        distance_to_daily_loss_usd=rd["distance_to_daily_loss_after_open_risk_usd"],
        status_freshness=rd["status_freshness"],
        open_risk_state=rd["after_open_risk_state"],
        open_loss_to_stop_usd=rd["open_loss_to_stop_usd"])
    assert v["state"] == prop_risk_gate.EXCEEDS
    assert v["cushion_usd"] == pytest.approx(26.293, abs=1e-3)
    assert "still at risk" in v["reason"]


def test_placed_report_carrying_sl_is_known_without_an_amend(isolated_db):
    """The new template field: sl on the open report is enough."""
    _open_eth_short(sl=2711.10)
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_risk_state"] == "measured"
    assert rd["distance_to_dd_floor_after_open_risk_usd"] == pytest.approx(
        26.293, abs=1e-3)


def test_a_ticket_stop_is_never_counted_as_the_position_stop(isolated_db):
    """#44's ticket said 2736.109; the terminal said otherwise. Instruction ≠ fact."""
    prop_journal.record_ticket({"ticket_id": "prop-manual-5ef9bb2e58c2",
                                "account_id": ACCT, "symbol": "ETHUSDT",
                                "direction": "short", "entry": 2681.92,
                                "sl": 2736.109, "tp": 2416.0})
    _open_eth_short(ticket_id="prop-manual-5ef9bb2e58c2")
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_risk_state"] == "stop_unknown"
    assert rd["open_risk"]["positions"][0]["ticket_sl"] == 2736.109


def test_a_reported_stop_beats_the_ticket_stop_for_the_alert_path(isolated_db):
    from src.prop.prop_monitor_pulse import find_open_prop_positions
    prop_journal.record_ticket({"ticket_id": "t1", "account_id": ACCT,
                                "symbol": "ETHUSDT", "direction": "short",
                                "sl": 2736.109, "tp": 2416.0})
    _open_eth_short(ticket_id="t1", sl=2711.10)
    (pos,) = find_open_prop_positions(account_id=ACCT)
    assert pos["sl"] == 2711.10 and pos["sl_source"] == "fill"
    assert pos["ticket_sl"] == 2736.109


def test_a_re_report_without_sl_does_not_wipe_the_amended_stop(isolated_db):
    _open_eth_short(ticket_id="t9", sl=2736.00)
    ingest_report({"kind": "amend", "account_id": ACCT, "symbol": "ETHUSDT",
                   "direction": "short", "sl": 2711.10})
    _open_eth_short(ticket_id="t9")  # relay retry of the original open report
    rows = prop_journal.list_fills(account_id=ACCT)
    assert len(rows) == 1 and rows[0]["sl"] == 2711.10


# ── multiple open positions ──────────────────────────────────────────────

def test_multiple_open_positions_sum_their_loss_to_stop(isolated_db):
    _open_eth_short(sl=2711.10)
    ingest_report({"account_id": ACCT, "symbol": "SOLUSDT", "direction": "long",
                   "status": "open", "entry_price": 150.0, "qty": 10.0,
                   "sl": 147.0})
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    # 58.487 (ETH) + (150 - 147) * 10 = 30.0 (SOL)
    assert rd["open_risk"]["open_count"] == 2
    assert rd["open_loss_to_stop_usd"] == pytest.approx(88.487, abs=1e-6)
    assert rd["distance_to_dd_floor_after_open_risk_usd"] == pytest.approx(
        94.76 - 9.98 - 88.487, abs=1e-3)


def test_one_unknown_among_several_makes_the_aggregate_unknown(isolated_db):
    _open_eth_short(sl=2711.10)
    ingest_report({"account_id": ACCT, "symbol": "SOLUSDT", "direction": "long",
                   "status": "open", "entry_price": 150.0, "qty": 10.0})
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_risk_state"] == "stop_unknown"
    assert rd["open_risk"]["stop_unknown_count"] == 1
    assert rd["open_loss_to_stop_usd"] is None, "a partial sum understates risk"
    known = [p for p in rd["open_risk"]["positions"] if not p["missing"]]
    assert known[0]["loss_to_stop_usd"] == pytest.approx(58.487, abs=1e-6)


def test_a_closed_position_no_longer_counts(isolated_db):
    _open_eth_short(sl=2711.10)
    ingest_report({"account_id": ACCT, "symbol": "ETHUSDT", "direction": "short",
                   "status": "closed", "entry_price": 2666.11,
                   "exit_price": 2600.0, "qty": 1.3, "pnl": 85.9, "reason": "tp"})
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_risk_state"] == "no_open_positions"
    assert rd["open_loss_to_stop_usd"] == 0.0


def test_a_stop_in_profit_never_grows_the_cushion_past_equity(isolated_db):
    _open_eth_short(sl=2600.0)  # trailed into profit: pnl at stop +85.943
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_loss_to_stop_usd"] == 0.0
    assert rd["distance_to_dd_floor_after_open_risk_usd"] == pytest.approx(
        rd["distance_to_dd_floor_usd"])


def test_flat_account_after_figures_equal_the_plain_distance(isolated_db):
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_risk_state"] == "no_open_positions"
    assert rd["distance_to_dd_floor_after_open_risk_usd"] == rd[
        "distance_to_dd_floor_usd"]


def test_an_unreadable_position_read_is_unreadable_not_flat(isolated_db, monkeypatch):
    from src.prop import prop_monitor_pulse

    def _boom(**_kw):
        raise RuntimeError("db locked")
    monkeypatch.setattr(prop_monitor_pulse, "find_open_prop_positions", _boom)
    rd = prop_reconcile.compute_rule_distance(ACCT, _snapshot())
    assert rd["open_risk_state"] == "unreadable"
    assert rd["distance_to_dd_floor_after_open_risk_usd"] is None


def test_a_rule_distance_without_the_state_grades_unknown(monkeypatch):
    """A reader that omits `after_open_risk_state` must not restore the optimism."""
    monkeypatch.setattr(prop_reconcile, "compute_rule_distance",
                        lambda _a: {"distance_to_dd_floor_usd": 94.76,
                                    "status_freshness": "ok"})
    v = prop_risk_gate.grade_account_ticket_risk(ACCT, risk_usd=75.0)
    assert v["state"] == prop_risk_gate.UNKNOWN


# ── amend refusals ───────────────────────────────────────────────────────

@pytest.mark.parametrize("report, needle", [
    ({"symbol": "ETHUSDT", "direction": "short"}, "needs sl and/or tp"),
    ({"symbol": "ETHUSDT", "sl": 2711.1}, "missing direction"),
    ({"symbol": "ETHUSDT", "direction": "short", "sl": "abc"}, "must be a number"),
    ({"symbol": "ETHUSDT", "direction": "short", "sl": 0}, "positive"),
    ({"symbol": "ETHUSDT", "direction": "long", "sl": 2711.1}, "no OPEN position"),
    ({"symbol": "ETHUSDT", "direction": "short", "sl": 2711.1, "fill_id": 999},
     "no OPEN position"),
])
def test_amend_refuses_rather_than_guesses(isolated_db, report, needle):
    _open_eth_short()
    with pytest.raises(ValueError, match=needle):
        ingest_report({"kind": "amend", "account_id": ACCT, **report})


def test_rendered_ticket_asks_for_sl_and_offers_the_amend():
    from datetime import timedelta
    from types import SimpleNamespace
    from src.prop import breakout_ticket as bt

    sig = SimpleNamespace(strategy="trend_donchian_eth_prop", symbol="ETHUSDT",
                          direction="short", entry=2681.92, sl=2736.109,
                          tp=2416.0,
                          signal_time=datetime(2026, 9, 23, 14, 12,
                                               tzinfo=timezone.utc))
    cfg = SimpleNamespace(dxtrade_symbol="ETHUSD", account_size_usd=5000.0,
                          contract_value_usd_per_point=1.0, risk_pct=1.5)
    t = SimpleNamespace(signal=sig, cfg=cfg, qty_units=1.384, risk_usd=75.0,
                        side="SELL", entry_min=2668.37, entry_max=2695.47,
                        rr=4.9, valid_until=sig.signal_time + timedelta(hours=1))
    text = bt.render_ticket(t)  # no account_id: the gate is not consulted
    placed = next(ln for ln in text.splitlines() if "placed :" in ln)
    assert '"sl":<stop you set>' in placed and '"tp":<target you set>' in placed
    assert '"kind":"amend"' in text
