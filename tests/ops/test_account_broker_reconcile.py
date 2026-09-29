"""``scripts/ops/account_broker_reconcile.py`` — the automated journal-vs-
broker reconciler for alpaca_live / breakout_1 / bybit_2
(OI-20260826-JOURNAL-TRUST-COVERS-ONE-ACCOUNT, operator "Build automated
reconcile", 2026-09-28).

Pins the three-state contract (agree / divergent / could_not_look, plus
alpaca's stated `not_exposed` for realized P&L) on each account, and the
fingerprint's independence from `captured_at` / exact P&L magnitude — the
same design `broker_bracket_reconcile.py` already relies on so its scheduled
caller only comments when the GRADED STATE moves.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "account_broker_reconcile",
        _ROOT / "scripts" / "ops" / "account_broker_reconcile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = _load()


# --------------------------------------------------------------- alpaca_live

def test_alpaca_agree():
    j = [{"account": "alpaca_live", "symbol": "AAPL", "side": "long", "qty": 10}]
    payload = {"accounts": [{"account_id": "alpaca_live", "read_state": "orders_read",
               "result": {"positions": [{"symbol": "AAPL", "side": "long", "qty": 10}],
                          "orders": [{"symbol": "AAPL", "order_type": "stop"},
                                     {"symbol": "AAPL", "order_type": "limit",
                                      "order_class": "bracket"}]}}]}
    r = m.reconcile_alpaca(j, payload)
    assert r["positions_state"] == "agree"
    assert r["protection_state"] == "agree"
    assert r["pnl_state"] == "not_exposed"  # alpaca exposes no broker-side realized pnl


def test_alpaca_divergent_qty_and_naked():
    j = [{"account": "alpaca_live", "symbol": "AAPL", "side": "long", "qty": 10}]
    qty_mismatch = {"accounts": [{"account_id": "alpaca_live", "read_state": "orders_read",
                    "result": {"positions": [{"symbol": "AAPL", "side": "long", "qty": 7}],
                               "orders": []}}]}
    r = m.reconcile_alpaca(j, qty_mismatch)
    assert r["positions_state"] == "divergent"
    assert r["positions"][0]["state"] == "qty_mismatch"

    naked = {"accounts": [{"account_id": "alpaca_live", "read_state": "orders_read",
             "result": {"positions": [{"symbol": "AAPL", "side": "long", "qty": 10}],
                        "orders": []}}]}
    r = m.reconcile_alpaca(j, naked)
    assert r["positions_state"] == "agree"
    assert r["protection_state"] == "divergent"
    assert r["protection"]["naked"] == ["AAPL"]


def test_alpaca_could_not_look():
    j = [{"account": "alpaca_live", "symbol": "AAPL", "side": "long", "qty": 10}]
    assert m.reconcile_alpaca(j, {"accounts": []})["positions_state"] == "could_not_look"
    assert m.reconcile_alpaca(j, None)["positions_state"] == "could_not_look"
    unread = {"accounts": [{"account_id": "alpaca_live", "read_state": "could_not_look",
              "result": None}]}
    r = m.reconcile_alpaca(j, unread)
    assert r["positions_state"] == "could_not_look"
    assert r["protection_state"] == "could_not_look"


# ------------------------------------------------------------------ bybit_2

def _bybit_fixture(*, position_side="long", stop=50000, target=70000, realized_usd=10.0):
    """Broker position row: the REAL `_bybit_position_row` shape
    (src/units/accounts/clients.py) — "size", never "qty"; the raw venue
    side string ("Buy"/"Sell"), never "long"/"short". Wallet-truth row: the
    REAL `WalletTruth.as_dict()` shape (src/runtime/bybit_wallet_truth.py) —
    "state" + window_start_ms/window_end_ms, never "read_state"/"window".
    REVIEW-14054: the invented shapes this replaced matched neither the
    route nor the underlying dataclass, and hid two real bugs (#1 bybit_2's
    pnl check permanently could_not_look; #2 a constant false divergence
    on every open bybit_2 position) behind a passing test suite.
    """
    venue_side = "Buy" if position_side == "long" else "Sell"
    j_pos = [{"account": "bybit_2", "symbol": "BTCUSDT", "side": "long", "qty": 1}]
    j_closed = [{"account": "bybit_2", "symbol": "BTCUSDT", "pnl": 10.0,
                "closedAt": "2026-09-28T00:00:00Z"}]
    broker = {"accounts": [{"account_id": "bybit_2",
              "result": {"positions": [{"symbol": "BTCUSDT", "side": venue_side, "size": 1.0,
                                        "entry_price": 60000.0, "stop_loss": stop,
                                        "take_profit": target, "tpsl_mode": "Full",
                                        "position_idx": 0}],
                         "orders": []}}]}
    wt = {"accounts": [{"account_id": "bybit_2", "state": "measured_api",
          "realized_usd": realized_usd, "window_start_ms": 1758000000000,
          "window_end_ms": 1759000000000}]}
    return j_pos, j_closed, broker, wt


def test_bybit_agree():
    j_pos, j_closed, broker, wt = _bybit_fixture()
    r = m.reconcile_bybit(j_pos, j_closed, broker, wt)
    assert r["positions_state"] == "agree"
    assert r["protection_state"] == "agree"
    assert r["pnl_state"] == "agree"
    # the known-partial-coverage caveat always rides along, per
    # PI-20260924-ZROYCDY4-0001 — never silently dropped even on a clean read.
    assert "ZROYCDY4" in r["known_caveat"]


def test_bybit_divergent_side_naked_and_pnl():
    j_pos, j_closed, broker, wt = _bybit_fixture(
        position_side="short", stop=None, target=None, realized_usd=-500.0)
    r = m.reconcile_bybit(j_pos, j_closed, broker, wt)
    assert r["positions_state"] == "divergent"
    assert r["positions"][0]["state"] == "side_mismatch"
    assert r["pnl_state"] == "divergent"


def test_bybit_naked_when_positions_agree():
    j_pos, j_closed, broker, wt = _bybit_fixture(stop=None, target=None)
    r = m.reconcile_bybit(j_pos, j_closed, broker, wt)
    assert r["positions_state"] == "agree"
    assert r["protection_state"] == "divergent"
    assert r["protection"]["naked"] == ["BTCUSDT"]


def test_bybit_could_not_look():
    j_pos, j_closed, _, _ = _bybit_fixture()
    r = m.reconcile_bybit(j_pos, j_closed, {"accounts": []}, None)
    assert r["positions_state"] == "could_not_look"
    assert r["pnl_state"] == "could_not_look"


def test_bybit_pnl_could_not_look_when_wallet_truth_unreadable():
    j_pos, j_closed, broker, _ = _bybit_fixture()
    # a wallet-truth read that could not look (unreadable) must not silently
    # grade the pnl check as `agree` just because journal_pnl_sum exists.
    wt_unreadable = {"accounts": [{"account_id": "bybit_2", "state": "unreadable",
                     "realized_usd": None}]}
    r = m.reconcile_bybit(j_pos, j_closed, broker, wt_unreadable)
    assert r["pnl_state"] == "could_not_look"


def test_bybit_no_closed_trades_is_agree_not_a_false_divergence():
    j_pos, _, broker, wt = _bybit_fixture()
    r = m.reconcile_bybit(j_pos, [], broker, wt)
    assert r["pnl_state"] == "agree"


# ---------------------------------------------------------------- breakout_1

def _breakout_status(*, broker_side="long", broker_qty=0.1, stop=60000, target=80000,
                     realized_today=5.0, missing_open_positions=False):
    raw = {} if missing_open_positions else {"open_positions": [
        {"symbol": "BTCUSD", "side": broker_side, "quantity": broker_qty,
         "stop_loss": stop, "take_profit": target}]}
    return {"present": True,
            "status": {"realized_today": realized_today, "raw": json.dumps(raw)},
            "rule_distance": {"open_risk": {"positions": [
                {"symbol": "BTCUSD", "direction": "long", "qty": 0.1}]}}}


def test_breakout_agree():
    status = _breakout_status()
    fills = [{"status": "closed", "closed_at": "2026-09-28T01:00:00Z", "pnl": 5.0}]
    r = m.reconcile_breakout(status, fills, now=datetime(2026, 9, 28, tzinfo=timezone.utc))
    assert r["positions_state"] == "agree"
    assert r["protection_state"] == "agree"
    assert r["pnl_state"] == "agree"
    assert r["pnl"]["scope"] == "daily, not last-N"


def test_breakout_divergent_broker_only_position_and_pnl():
    status = _breakout_status(broker_side="short", broker_qty=1, stop=None, target=None,
                              realized_today=500.0)
    # broker shows a position at a different symbol than the journal declares
    # -> the journal's BTCUSD becomes journal_only, ETHUSD (broker) is broker_only.
    # Findings are keyed on the canonical BOT symbol (src/prop/symbol_map), the
    # journal's own key, so the venue spellings read back as BTCUSDT / ETHUSDT.
    status["status"]["raw"] = json.dumps({"open_positions": [
        {"symbol": "ETHUSD", "side": "short", "quantity": 1,
         "stop_loss": None, "take_profit": None}]})
    fills = [{"status": "closed", "closed_at": "2026-09-28T01:00:00Z", "pnl": 5.0}]
    r = m.reconcile_breakout(status, fills, now=datetime(2026, 9, 28, tzinfo=timezone.utc))
    assert r["positions_state"] == "divergent"
    symbols_by_state = {p["symbol"]: p["state"] for p in r["positions"]}
    assert symbols_by_state == {"BTCUSDT": "journal_only", "ETHUSDT": "broker_only"}
    assert r["pnl_state"] == "divergent"


def test_breakout_could_not_look_when_feed_has_not_posted_positions_yet():
    status = _breakout_status(missing_open_positions=True)
    r = m.reconcile_breakout(status, [])
    assert r["positions_state"] == "could_not_look"
    assert r["protection_state"] == "could_not_look"


def test_breakout_could_not_look_when_status_absent():
    r = m.reconcile_breakout({"present": False, "status": None}, [])
    assert r["positions_state"] == "could_not_look"


def test_breakout_empty_read_is_could_not_look_not_flat():
    """PR #14005 (open, held for the manager as of 2026-09-29) fixes a
    measured blind-reader bug: the terminal returned 0 positions/orders
    rows even with a real filled position on the account. Until that
    lands, an EMPTY open_positions read must never be trusted as "flat" —
    manager instruction: state cannot-read explicitly, never 0."""
    status = _breakout_status()
    status["status"]["raw"] = json.dumps({"open_positions": []})
    r = m.reconcile_breakout(status, [])
    assert r["positions_state"] == "could_not_look"
    assert r["protection_state"] == "could_not_look"
    assert "#14005" in r["note"]


def test_breakout_nonempty_read_is_trusted_even_before_14005():
    # The blind-reader bug is a false NEGATIVE (0 rows). A non-empty read is
    # real data and must still be reconciled normally.
    status = _breakout_status()
    fills = [{"status": "closed", "closed_at": "2026-09-28T01:00:00Z", "pnl": 5.0}]
    r = m.reconcile_breakout(status, fills, now=datetime(2026, 9, 28, tzinfo=timezone.utc))
    assert r["positions_state"] == "agree"
    assert "note" not in r


# ------------------------------------------------------------- run/grade/fp

def test_grade_and_fingerprint_are_stable_across_captured_at_and_pnl_noise():
    j_pos = [{"account": "alpaca_live", "symbol": "AAPL", "side": "long", "qty": 10},
             {"account": "bybit_2", "symbol": "BTCUSDT", "side": "long", "qty": 1}]
    j_closed = [{"account": "bybit_2", "symbol": "BTCUSDT", "pnl": 10.0,
                "closedAt": "2026-09-28T00:00:00Z"}]
    alpaca_payload = {"accounts": [{"account_id": "alpaca_live", "read_state": "orders_read",
                      "result": {"positions": [{"symbol": "AAPL", "side": "long", "qty": 10}],
                                 "orders": [{"symbol": "AAPL", "order_type": "stop"},
                                            {"symbol": "AAPL", "order_type": "limit",
                                             "order_class": "bracket"}]}}]}
    bybit_payload = {"accounts": [{"account_id": "bybit_2",
                     "result": {"positions": [{"symbol": "BTCUSDT", "side": "Buy", "size": 1.0,
                                               "entry_price": 60000.0, "stop_loss": 50000.0,
                                               "take_profit": 70000.0, "tpsl_mode": "Full",
                                               "position_idx": 0}],
                                "orders": []}}]}
    prop_status = _breakout_status()
    fills = [{"status": "closed", "closed_at": "2026-09-28T01:00:00Z", "pnl": 5.0}]
    fixed_now = datetime(2026, 9, 28, tzinfo=timezone.utc)

    def _run(realized_usd):
        wt = {"accounts": [{"account_id": "bybit_2", "state": "measured_api",
              "realized_usd": realized_usd, "window_start_ms": 1758000000000,
              "window_end_ms": 1759000000000}]}
        return m.run(j_pos, j_closed, alpaca_payload, bybit_payload, wt, prop_status,
                     fills, now=fixed_now)

    res_a, res_b = _run(10.0), _run(10.4)  # both within the $1 tolerance -> both `agree`
    assert m.grade(res_a) == 0
    assert m.fingerprint(res_a).splitlines()[0] == m.fingerprint(res_b).splitlines()[0]

    res_div = _run(-500.0)
    assert m.grade(res_div) == 1
    assert m.fingerprint(res_div).splitlines()[0] != m.fingerprint(res_a).splitlines()[0]


def test_grade_prefers_divergent_over_could_not_look():
    # one account divergent, another could_not_look -> exit 1, not 3: a real
    # finding must never be masked by an unrelated read failure elsewhere.
    j_pos = [{"account": "alpaca_live", "symbol": "AAPL", "side": "long", "qty": 10}]
    alpaca_div = {"accounts": [{"account_id": "alpaca_live", "read_state": "orders_read",
                  "result": {"positions": [{"symbol": "AAPL", "side": "long", "qty": 1}],
                             "orders": []}}]}
    res = m.run(j_pos, [], alpaca_div, {"accounts": []}, None,
               {"present": False, "status": None}, [])
    assert m.grade(res) == 1


def test_self_test_exits_clean():
    assert m._self_test() == 0


# --------------------------------------------------- REVIEW-14054 regression

def test_bybit_position_size_field_is_read_not_qty():
    """REVIEW-14054 finding #2: _bybit_position_row (src/units/accounts/
    clients.py) names the field "size", never "qty". A broker row that ONLY
    carries "size" (no "qty" at all — the real shape) must still match the
    journal's declared quantity, not read as an unmeasured qty and fall
    into a permanent qty_mismatch on every account that actually holds the
    position it declares."""
    j_pos = [{"account": "bybit_2", "symbol": "BTCUSDT", "side": "long", "qty": 1}]
    broker = {"accounts": [{"account_id": "bybit_2",
              "result": {"positions": [{"symbol": "BTCUSDT", "side": "Buy", "size": 1.0,
                                        "stop_loss": 50000.0, "take_profit": 70000.0}],
                         "orders": []}}]}
    assert "qty" not in broker["accounts"][0]["result"]["positions"][0]  # prove the shape
    r = m.reconcile_bybit(j_pos, [], broker, None)
    assert r["positions_state"] == "agree"
    assert r["positions"][0]["broker_qty"] == 1.0


def test_bybit_wallet_truth_state_field_is_read_not_read_state():
    """REVIEW-14054 finding #1: WalletTruth.as_dict() (src/runtime/
    bybit_wallet_truth.py) names the field "state", never "read_state". A
    wallet-truth row with ONLY "state" (the real shape) must be usable as
    measured_api evidence, not permanently fall back to could_not_look."""
    j_pos, j_closed, broker, _ = _bybit_fixture()
    wt = {"accounts": [{"account_id": "bybit_2", "state": "measured_api",
          "realized_usd": 10.0, "window_start_ms": 1758000000000,
          "window_end_ms": 1759000000000}]}
    assert "read_state" not in wt["accounts"][0]  # prove the shape
    r = m.reconcile_bybit(j_pos, j_closed, broker, wt)
    assert r["pnl_state"] == "agree"


# ------------------------------------------- reduce legs (issue #14113, 5702)
# The REAL /api/bot/trades/closed wire shape (fetched live 2026-09-29): the
# P&L field is "realizedPnl" — never "pnl" — and it carries NO setup_type /
# notes, so a wire row is classified by id against the raw journal read
# (/api/diag/journal?table=trades — every column). Trade 5702 is an
# eth_pullback_2h intent_reduce leg whose pnl is NULL BY DESIGN
# (BL-20260711), written by src/units/accounts/execute.py's reduce audit row.

def _wire_close(tid, pnl, closed_at, pattern="trend_donchian_eth_4h", reason="sl"):
    return {"id": str(tid), "account": "bybit_2", "accountClass": "real_money",
            "isDemo": False, "symbol": "ETHUSDT", "assetClass": "crypto",
            "side": "sell", "pattern": pattern, "qty": 0.01, "entryPrice": 2500.0,
            "exitPrice": 2502.42, "realizedPnl": pnl,
            "pnlProvenance": None if pnl is None else "measured",
            "journalTrust": "known_divergent", "realizedPnlPct": None,
            "openedAt": closed_at, "closedAt": closed_at, "closeReason": reason}


# raw `trades` rows as /api/diag/journal returns them (columns per
# src/units/db/database.py; values per execute.py's reduce audit insert)
_RAW_5702 = {"id": 5702, "account_id": "bybit_2", "symbol": "ETHUSDT",
             "strategy_name": "eth_pullback_2h", "setup_type": "intent_reduce",
             "exit_reason": "intent_reduce_executed", "status": "closed",
             "position_size": 0.01, "pnl": None, "pnl_percent": None,
             "closed_at": "2026-09-11T20:02:01.225085+00:00",
             "notes": json.dumps({"pnl_source": "deferred_intent_reduce",
                                  "intent_reduce_allocations": [
                                      {"trade_id": 5682, "qty": 0.01}]})}
_RAW_5682 = {"id": 5682, "account_id": "bybit_2", "symbol": "ETHUSDT",
             "strategy_name": "trend_donchian_eth_4h", "setup_type": "trend",
             "exit_reason": "sl", "status": "closed", "exit_price": 2502.42,
             "pnl": -2.8395, "closed_at": "2026-09-13T08:31:31.454000+00:00",
             "notes": json.dumps({"pnl_source": "bybit_closed_pnl"})}


def _wt(realized):
    return {"accounts": [{"account_id": "bybit_2", "state": "measured_api",
            "realized_usd": realized, "window_start_ms": 1782902876782,
            "window_end_ms": 1790678876782}]}


def _live_closes():
    return [_wire_close(5682, -2.8395, "2026-09-13T08:31:31.454000+00:00"),
            _wire_close(5702, None, "2026-09-11T20:02:01.225085+00:00",
                        pattern="eth_pullback_2h", reason="other"),
            _wire_close(5644, -3.4241, "2026-09-11T08:30:35.831000+00:00")]


def test_bybit_reduce_leg_null_pnl_is_excluded_not_could_not_look():
    j_pos, _, broker, _ = _bybit_fixture()
    raw = [_RAW_5682, _RAW_5702]
    r = m.reconcile_bybit(j_pos, _live_closes(), broker, _wt(-6.2636), raw_trades=raw)
    assert r["pnl_state"] == "agree"
    assert r["pnl"]["journal_pnl_sum"] == -6.2636
    assert r["pnl"]["n_closes"] == 2
    assert r["pnl"]["reduce_legs_excluded"] == ["5702"]


def test_bybit_reduce_leg_via_run_and_diag_envelope():
    """run() threads the raw read through; the envelope=true shape
    ({"rows": [...]}) is accepted as well as the bare array."""
    j_pos, _, broker, _ = _bybit_fixture()
    res = m.run(j_pos, _live_closes(), None, broker, _wt(-6.2636), None, [],
                raw_trades={"rows": [_RAW_5702]})
    assert res["accounts"]["bybit_2"]["pnl_state"] == "agree"


def test_bybit_raw_journal_reduce_row_is_excluded_by_setup_type():
    """A raw journal row passed as the closed list is classified directly."""
    j_pos, _, broker, _ = _bybit_fixture()
    r = m.reconcile_bybit(j_pos, [_RAW_5682, _RAW_5702], broker, _wt(-2.8395))
    assert r["pnl_state"] == "agree"
    assert r["pnl"]["reduce_legs_excluded"] == ["5702"]


def test_bybit_reduce_leg_unclassifiable_without_raw_read_stays_could_not_look():
    """No raw read -> 5702 cannot be told from a real unknown -> could_not_look,
    naming it, and saying the reduce read was unavailable."""
    j_pos, _, broker, _ = _bybit_fixture()
    r = m.reconcile_bybit(j_pos, _live_closes(), broker, _wt(-6.2636))
    assert r["pnl_state"] == "could_not_look"
    assert r["pnl"]["null_pnl_trade_ids"] == ["5702"]
    assert r["pnl"]["reduce_leg_read"] == "unavailable"


def test_bybit_non_reduce_null_pnl_still_could_not_look():
    j_pos, _, broker, _ = _bybit_fixture()
    closes = [_wire_close(5682, -2.8395, "2026-09-13T08:31:31Z"),
              _wire_close(5703, None, "2026-09-12T00:00:00Z")]  # NOT a reduce leg
    raw = [_RAW_5682, _RAW_5702, dict(_RAW_5682, id=5703, pnl=None)]
    r = m.reconcile_bybit(j_pos, closes, broker, _wt(-2.8395), raw_trades=raw)
    assert r["pnl_state"] == "could_not_look"
    assert r["pnl"]["null_pnl_trade_ids"] == ["5703"]
    assert r["pnl"]["reduce_leg_read"] == "read"


def test_bybit_wire_realizedPnl_field_is_read_not_pnl():
    """First live run (#14113): reading "pnl" off the wire made all 20 rows
    None -> journal_pnl_sum None -> could_not_look, whatever the rows held."""
    j_pos, _, broker, _ = _bybit_fixture()
    closes = [_wire_close(5682, -2.8395, "2026-09-13T08:31:31Z")]
    assert "pnl" not in closes[0]  # prove the shape
    r = m.reconcile_bybit(j_pos, closes, broker, _wt(-2.8395))
    assert r["pnl_state"] == "agree"


# ----------------------------------- breakout_1 venue symbol alias (#14113)
# The REAL /api/bot/prop/status shape read live 2026-09-29: the journal side
# (rule_distance.open_risk.positions) keys the position SOLUSDT, the DXtrade
# terminal (status.raw.open_positions) names the SAME position SOLUSD — same
# side, qty, entry and stop. Exact-string matching split it into
# broker_only + journal_only (run 36579673814).

def _prop_status(journal_sym, broker_sym):
    return {"present": True,
            "status": {"realized_today": 0.0,
                       "raw": json.dumps({"open_positions": [
                           {"symbol": broker_sym, "side": "long", "quantity": 0.01,
                            "entry_price": 120.62, "stop_loss": 119.36,
                            "take_profit": 121.78, "unrealized_pnl": None}]})},
            "rule_distance": {"open_risk": {"positions": [
                {"fill_id": 47, "ticket_id": "roundtrip-solusd-20260929T125601Z",
                 "symbol": journal_sym, "direction": "long", "qty": 0.01,
                 "entry_price": 120.62, "sl": 119.36}]}}}


def test_breakout_venue_symbol_matches_journal_bot_symbol():
    r = m.reconcile_breakout(_prop_status("SOLUSDT", "SOLUSD"), [])
    assert r["positions_state"] == "agree"
    assert [p["state"] for p in r["positions"]] == ["position_match"]
    assert r["positions"][0]["symbol"] == "SOLUSDT"
    # the protection lookup must find the broker row through the alias too
    assert r["protection_state"] == "agree"
    assert r["positions"][0]["has_stop"] is True


def test_breakout_genuinely_different_symbol_still_divergent():
    r = m.reconcile_breakout(_prop_status("SOLUSDT", "ETHUSD"), [])
    assert r["positions_state"] == "divergent"
    assert sorted(p["state"] for p in r["positions"]) == ["broker_only", "journal_only"]
