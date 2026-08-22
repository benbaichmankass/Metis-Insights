"""Tests for the PURE decision core of src/units/strategies/pairs_executor.py.
The live I/O layer (run_pairs_tick) is exercised on the VM paper soak, not here."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.units.strategies import pairs_engine as pe  # noqa: E402
from src.units.strategies import pairs_executor as px  # noqa: E402


def _params():
    # hedge_beta="one" so the engine spread == the synthetic construction (beta=1),
    # making the jammed last bar a deterministic high-z entry.
    return pe.PairParams("SOLUSDT", "BTCUSDT", lookback=15, entry_z=2.0,
                         exit_z=0.5, stop_z=2.0, max_hold_bars=20, hedge_beta="one")


def _open_from_entry(ca, cb, bars_held):
    sig = pe.entry_signal(ca, cb, _params())
    assert sig is not None
    return pe.OpenPair(direction=sig["direction"], entry_spread=sig["entry_spread"],
                       stop_spread=sig["stop_spread"], bars_held=bars_held)


def _extended_spread(n=120, seed=1):
    """Series whose LATEST bar has a large |z| (extended spread → entry fires)."""
    rng = np.random.default_rng(seed)
    lb = np.cumsum(rng.normal(0, 0.005, n)) + np.log(50000.0)
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = 0.9 * s[i - 1] + rng.normal(0, 0.01)
    s[-1] = s[-2] + 0.15  # jam the last bar far from the mean → high z
    la = lb + s
    return np.exp(la), np.exp(lb)


def test_skip_flat_when_low_z():
    ca = np.full(120, 100.0)
    cb = np.full(120, 50000.0)
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "skip_flat" and not d.legs


def test_open_produces_two_legs_opposite_directions():
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "open", d.soak
    assert len(d.legs) == 2
    dirs = {leg.symbol: leg.direction for leg in d.legs}
    assert set(dirs.values()) == {"long", "short"}          # market-neutral
    assert all(leg.qty > 0 and leg.sl > 0 and leg.tp > 0 for leg in d.legs)
    assert d.soak["pairs_group_id"].startswith("pair-")


def test_skip_concurrency_when_leg_held():
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols={"BTCUSDT"},
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "skip_concurrency" and not d.legs


def test_skip_size_when_budget_zero():
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=0.0, correlation_open=0)
    assert d.event == "skip_size" and not d.legs


def test_shadow_mode_downgrades_open():
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0, execution_mode="shadow")
    assert d.event == "shadow_open"
    assert len(d.legs) == 2 and d.close is False           # computed but NOT placed


def test_hold_when_open_and_no_exit():
    # open on the jammed high-z bar: spread still extended (no revert), stop not
    # breached (sj == entry), within max_hold → hold.
    ca, cb = _extended_spread()
    pos = _open_from_entry(ca, cb, bars_held=1)
    d = px.decide_pair(_params(), ca, cb, open_state=pos, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "hold" and not d.close


def test_close_on_timeout():
    ca, cb = _extended_spread()
    pos = _open_from_entry(ca, cb, bars_held=99)  # past max_hold → timeout
    d = px.decide_pair(_params(), ca, cb, open_state=pos, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0)
    assert d.event == "close" and d.close is True
    assert d.soak["outcome"] == "timeout"


def test_correlation_haircut_reduces_budget():
    ca, cb = _extended_spread()
    d0 = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                        risk_budget_usd=100.0, correlation_open=0)
    d2 = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                        risk_budget_usd=100.0, correlation_open=2, corr_factor=0.5)
    # 2 correlated open → 0.25× budget → smaller qty
    assert d2.legs[0].qty < d0.legs[0].qty


def test_monitor_always_none():
    assert px.monitor({}, None, {}) is None


# --------------------------------------------------------------------------
# Live-layer PURE helpers (config plumbing / open-state / dedup). The
# placement + close I/O is exercised on the VM paper soak, not here.
# --------------------------------------------------------------------------

def test_bar_seconds_and_params_defaults():
    assert px._bar_seconds("1h") == 3600
    assert px._bar_seconds("15m") == 900
    assert px._bar_seconds("weird") == 3600          # unknown → 1h default
    p = px._params_from_cfg({"symbol_a": "SOLUSDT", "symbol_b": "BTCUSDT"})
    assert (p.lookback, p.entry_z, p.exit_z, p.stop_z, p.max_hold_bars) == (15, 2.0, 0.5, 2.0, 20)
    assert p.hedge_beta == "rolling"


def test_leg_strats_naming():
    assert px._leg_strats({"name": "pairs_sol_btc"}) == ("pairs_sol_btc_a", "pairs_sol_btc_b")


def test_load_pairs_config_missing_is_noop():
    assert px._load_pairs_config("/nonexistent/pairs.yaml") == {}


def test_load_real_pairs_config_on_bybit_1():
    cfg = px._load_pairs_config("config/pairs.yaml")
    pairs = cfg.get("pairs") or []
    assert len(pairs) == 4
    # RE-FLIPPED to live 2026-07-16 (operator-approved, Tier-3) after the executor
    # placement bugs were fixed: (1) qty=0 re-size (_place_pair passes qty_override),
    # (2) blown-up leg SL/TP (the pairs_sizing backstop clamp, #6549), and
    # (3) the hardcoded risk_budget_usd -> derived from balance x risk_pct (#6554).
    # Rollback is a one-line flip back to shadow (accept either here so a defensive
    # rollback doesn't turn this test red — the #6552 lesson).
    assert cfg.get("account_id") == "bybit_1"
    assert all(str(p.get("execution")).lower() in ("live", "shadow") for p in pairs)


def test_skip_size_when_leg_notional_below_min():
    """A pair sized below the per-leg exchange minimum skips (skip_size), never
    places a sub-min / hedge-breaking order (BL-20260716-PAIRS-EXEC)."""
    ca, cb = _extended_spread()
    # tiny budget → leg notionals well under a $50 min → skip_size (not open).
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=100.0, correlation_open=0, min_leg_notional_usd=1e12)
    assert d.event == "skip_size" and not d.legs
    assert d.soak.get("notional_a_usd") is not None


def test_open_legs_have_bounded_protective_levels():
    """Regression: the placed SL/TP must be sane (within ±100% of entry), never
    the astronomical exp(spread) values that the exchange rejected."""
    ca, cb = _extended_spread()
    d = px.decide_pair(_params(), ca, cb, open_state=None, held_symbols=set(),
                       risk_budget_usd=100000.0, correlation_open=0, min_leg_notional_usd=0.0)
    assert d.event == "open" and len(d.legs) == 2
    for leg in d.legs:
        assert 0 < leg.sl < leg.entry_ref * 2 and 0 < leg.tp < leg.entry_ref * 2


def test_decision_bars_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(px, "_decision_bars_path", lambda: tmp_path / "bars.json")
    assert px._load_decision_bars() == {}
    px._save_decision_bars({"pairs_sol_btc": "111|222"})
    assert px._load_decision_bars() == {"pairs_sol_btc": "111|222"}


def _real_order_packages_ddl() -> str:
    """The PRODUCTION `order_packages` DDL, lifted from the module that owns it.

    Deliberately NOT hand-written. The previous version of these tests declared
    its own table as ``(id INTEGER PRIMARY KEY, strategy_name, account_id, meta)``
    — a schema production does not have — so the tests passed against a
    fictional table while the real query raised ``no such column: account_id``
    on every live tick for 2,471 decisions
    (BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED). A green suite over a
    fabricated schema is the canonical "green is not evidence" shape; reading
    the DDL from source is what makes these tests able to fail.
    """
    src = Path(__file__).resolve().parents[1] / "src/units/db/database.py"
    text = src.read_text(encoding="utf-8")
    i = text.index("CREATE TABLE IF NOT EXISTS order_packages")
    return text[i:text.index("''')", i)]


def _seed_pkg(db, strategy, meta_json, created_at="2026-07-16T00:00:00+00:00"):
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.execute(_real_order_packages_ddl())
    conn.execute(
        "INSERT INTO order_packages (order_package_id, strategy_name, symbol, "
        "direction, entry, sl, tp, created_at, updated_at, status, meta) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"op-{strategy}", strategy, "SOLUSDT", "buy", 1.0, 0.9, 1.2,
         created_at, created_at, "open", meta_json))
    conn.commit()
    conn.close()


def test_real_order_packages_schema_has_no_account_id_or_id():
    """Pins the fact the production bug turned on. If a migration ever adds
    either column, this fails and the query in `_open_pkg_meta` can be
    revisited deliberately rather than by assumption."""
    import sqlite3 as _sq
    conn = _sq.connect(":memory:")
    conn.execute(_real_order_packages_ddl())
    cols = {r[1] for r in conn.execute("PRAGMA table_info(order_packages)")}
    assert "strategy_name" in cols and "meta" in cols       # positive control
    assert "account_id" not in cols
    assert "id" not in cols


def test_reconstruct_open_state_from_pkg_meta(tmp_path):
    from datetime import datetime, timedelta, timezone
    import json as _j
    db = tmp_path / "j.db"
    opened = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _seed_pkg(db, "pairs_sol_btc_a", _j.dumps(
        {"pair_direction": "long_spread", "entry_spread": 0.5,
         "stop_spread": 0.3, "opened_at_utc": opened, "bar_seconds": 3600}))
    pair = {"name": "pairs_sol_btc", "symbol_a": "SOLUSDT", "symbol_b": "BTCUSDT"}
    status, st = px._reconstruct_open_state(pair, "bybit_1", str(db))
    assert status == "found"
    assert st is not None
    assert st.direction == "long_spread"
    assert st.entry_spread == 0.5 and st.stop_spread == 0.3
    assert st.bars_held == 3                          # ~3h at 1h bars


def test_reconstruct_open_state_three_states_are_distinguishable(tmp_path):
    """`error` (we could not look) and `absent` (we looked; nothing there) must
    never collapse — that collapse is what disabled the sleeve's close path."""
    import json as _j
    pair = {"name": "pairs_x", "symbol_a": "A", "symbol_b": "B"}

    # (1) ERROR — the DB file does not exist. We could not look.
    assert px._reconstruct_open_state(pair, "acct", str(tmp_path / "no.db")) == ("error", None)

    # (2) ABSENT — a real, readable table with no package for this strategy.
    db = tmp_path / "empty.db"
    _seed_pkg(db, "some_other_strategy", _j.dumps({"pair_direction": "long_spread"}))
    assert px._reconstruct_open_state(pair, "acct", str(db)) == ("absent", None)

    # (3) ERROR — the package exists but its bookkeeping is malformed. We
    #     looked and cannot use what we found; that is not "absent".
    db2 = tmp_path / "bad.db"
    _seed_pkg(db2, "pairs_x_a", _j.dumps({"pair_direction": "long_spread"}))  # missing spreads
    assert px._reconstruct_open_state(pair, "acct", str(db2)) == ("error", None)


def test_open_pkg_meta_query_runs_against_the_real_schema(tmp_path):
    """THE REGRESSION TEST. The old query named two columns that do not exist,
    the broad `except` swallowed the OperationalError at DEBUG, and every open
    pair read as unreadable — 29 opens, 0 closes. Any re-introduction of an
    `account_id` / `id` predicate makes this return ("error", ...) again."""
    import json as _j
    db = tmp_path / "j.db"
    _seed_pkg(db, "pairs_bnb_btc_a", _j.dumps({"pair_direction": "short_spread"}))
    status, meta = px._open_pkg_meta("pairs_bnb_btc_a", "bybit_1", str(db))
    assert status == "found", f"query failed against the real schema: {status}"
    assert meta["pair_direction"] == "short_spread"


def test_open_pkg_meta_picks_the_newest_package(tmp_path):
    """`ORDER BY id` was replaced with created_at/rowid; prove the ordering is
    still newest-first, or a stale spread would be reconstructed."""
    import json as _j
    db = tmp_path / "j.db"
    _seed_pkg(db, "pairs_bnb_btc_a", _j.dumps({"pair_direction": "old"}),
              created_at="2026-07-01T00:00:00+00:00")
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.execute(
        "INSERT INTO order_packages (order_package_id, strategy_name, symbol, "
        "direction, entry, sl, tp, created_at, updated_at, status, meta) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("op-new", "pairs_bnb_btc_a", "SOLUSDT", "buy", 1.0, 0.9, 1.2,
         "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00", "open",
         _j.dumps({"pair_direction": "new"})))
    conn.commit()
    conn.close()
    status, meta = px._open_pkg_meta("pairs_bnb_btc_a", "bybit_1", str(db))
    assert status == "found" and meta["pair_direction"] == "new"


def test_state_unreadable_alert_is_rate_limited(monkeypatch):
    """The condition can fire every tick. An alert that fires every tick is the
    desensitized-alarm P1, so it must be deduped per (pair, reason)."""
    sent = []
    import src.runtime.outcomes as _out
    monkeypatch.setattr(_out, "report", lambda *a, **k: sent.append((a, k)))
    px._state_alert_last.clear()
    for _ in range(5):
        px._alert_state_unreadable("A/B", "acct", state_read="error")
    assert len(sent) == 1, f"expected 1 alert, got {len(sent)}"
    # A DIFFERENT fault on the same pair is a different alarm, not a duplicate.
    px._alert_state_unreadable("A/B", "acct", state_read="absent")
    assert len(sent) == 2


def test_unwind_legs_reports_naked_on_failed_close(monkeypatch):
    """close_open_position is best-effort (returns ok:False, doesn't raise); the
    unwind must SURFACE the still-naked leg, not silently swallow it
    (BL-20260716-PAIRS-MINQTY — the naked BNB leg incident)."""
    import src.units.accounts.execute as _exec
    monkeypatch.setattr(_exec, "close_open_position",
                        lambda *a, **k: {"ok": False, "error": "position not found"})
    naked = px._unwind_legs(object(), {"exchange": "bybit"},
                            [("BNBUSDT", "short", 1.01)])
    assert len(naked) == 1 and naked[0]["symbol"] == "BNBUSDT"
    assert naked[0]["error"] == "position not found"


def test_unwind_legs_clean_close_returns_empty(monkeypatch):
    import src.units.accounts.execute as _exec
    monkeypatch.setattr(_exec, "close_open_position", lambda *a, **k: {"ok": True})
    assert px._unwind_legs(object(), {"exchange": "bybit"},
                           [("BNBUSDT", "short", 1.01)]) == []


def test_unwind_legs_raised_close_is_naked(monkeypatch):
    import src.units.accounts.execute as _exec
    monkeypatch.setattr(_exec, "close_open_position",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down")))
    naked = px._unwind_legs(object(), {"exchange": "bybit"}, [("BNBUSDT", "short", 1.01)])
    assert len(naked) == 1 and "api down" in naked[0]["error"]


def test_alert_partial_placement_never_raises(monkeypatch):
    """The half-placement alert must never break the tick, even if outcomes fails."""
    import src.runtime.outcomes as _out
    calls = []
    monkeypatch.setattr(_out, "report", lambda *a, **k: calls.append((a, k)))
    px._alert_partial_placement("BNBUSDT/BTCUSDT", "bybit_1", [("BNBUSDT", "short", 1.01)],
                                failed_leg="BTCUSDT", err="rejected",
                                naked=[{"symbol": "BNBUSDT", "direction": "short", "qty": 1.01}])
    assert calls and calls[0][0][0] == "pairs_naked_leg"      # CRITICAL naked-leg alert
    # a clean unwind (no naked) → the WARNING path
    calls.clear()
    px._alert_partial_placement("BNBUSDT/BTCUSDT", "bybit_1", [("BNBUSDT", "short", 1.01)],
                                failed_leg="BTCUSDT", err="rejected", naked=[])
    assert calls and calls[0][0][0] == "pairs_partial_placement"
    # outcomes.report exploding must be swallowed (never breaks the tick)
    monkeypatch.setattr(_out, "report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    px._alert_partial_placement("X/Y", "acct", [], failed_leg="Y", err="e", naked=[])  # no raise


def test_legs_below_min_qty_blocks_submin_leg(monkeypatch):
    """The pre-placement gate flags a leg whose qty floors below the venue min
    (BL-20260716-PAIRS-MINQTY) so the pair skips instead of half-placing."""
    legs = [px.LegOrder("BNBUSDT", "short", 1.0, 580.0, 1160.0, 290.0),
            px.LegOrder("BTCUSDT", "long", 0.00037, 64000.0, 32000.0, 128000.0)]
    import src.units.accounts.qty_legalize as _ql

    def _fake_legalize(qty, *, account_cfg, symbol, client=None, prefer_live=False):
        ok = not (symbol == "BTCUSDT" and qty < 0.001)   # BTC sub-min fails
        return _ql.LegalizedQty(qty=qty, ok=ok,
                                reason="" if ok else "below_venue_min_qty",
                                venue_min=0.001 if symbol == "BTCUSDT" else 0.01,
                                step=0.001, source="instrument_profile")
    monkeypatch.setattr(_ql, "legalize_qty", _fake_legalize)
    blocked = px._legs_below_min_qty(object(), {"exchange": "bybit"}, legs)
    assert len(blocked) == 1 and blocked[0]["symbol"] == "BTCUSDT"
    assert blocked[0]["venue_min"] == 0.001


def test_legs_below_min_qty_passes_when_both_clear(monkeypatch):
    legs = [px.LegOrder("SOLUSDT", "long", 4.3, 77.0, 38.0, 153.0),
            px.LegOrder("ETHUSDT", "short", 0.29, 1920.0, 3840.0, 960.0)]
    import src.units.accounts.qty_legalize as _ql
    monkeypatch.setattr(_ql, "legalize_qty",
                        lambda qty, **k: _ql.LegalizedQty(qty=qty, ok=True, reason="",
                                                          venue_min=0.01, step=0.01,
                                                          source="instrument_profile"))
    assert px._legs_below_min_qty(object(), {"exchange": "bybit"}, legs) == []


def test_legs_below_min_qty_fail_open_on_lookup_error(monkeypatch):
    """A resolution error passes the leg (fail-open) — never blocks a placeable
    pair on a lookup miss; the submit pre-flight stays the backstop."""
    legs = [px.LegOrder("SOLUSDT", "long", 4.3, 77.0, 38.0, 153.0)]
    import src.units.accounts.qty_legalize as _ql

    def _boom(*a, **k):
        raise RuntimeError("lot lookup down")
    monkeypatch.setattr(_ql, "legalize_qty", _boom)
    assert px._legs_below_min_qty(object(), {"exchange": "bybit"}, legs) == []


def test_run_pairs_tick_live_submin_leg_skips_no_placement(tmp_path, monkeypatch):
    """A LIVE pair whose leg floors below the venue min is refused pre-placement
    (skip_size) and NEVER calls execute_pkg — no half-placement / orphan."""
    ca, cb = _extended_spread()
    captured = []
    monkeypatch.setattr(px, "_load_pairs_config", lambda path=None: {
        "account_id": "bybit_1", "pairs_risk_fraction": 1.0,
        "pairs": [{"name": "pairs_sol_btc", "symbol_a": "SOLUSDT",
                   "symbol_b": "BTCUSDT", "execution": "live",
                   "timeframe": "1h", "hedge_beta": "one"}],
    })
    monkeypatch.setattr(px, "_fetch_leg",
                        lambda sym, tf, lim, s: (list(ca) if sym == "SOLUSDT" else list(cb), "T1"))
    monkeypatch.setattr(px, "_pair_is_open", lambda *a, **k: False)
    monkeypatch.setattr(px, "_held_leg_symbols", lambda *a, **k: set())
    monkeypatch.setattr(px, "_count_correlated_open", lambda *a, **k: 0)
    monkeypatch.setattr(px, "_save_decision_bars", lambda state: None)
    monkeypatch.setattr(px, "_load_decision_bars", lambda: {})
    import src.units.accounts.clients as _clients
    monkeypatch.setattr(_clients, "bybit_client_for", lambda acct: object())
    import src.units.accounts.execute as _exec
    monkeypatch.setattr(_exec, "_fetch_balance", lambda *a, **k: 100000.0)
    monkeypatch.setattr(_exec, "execute_pkg",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not place a sub-min pair")))
    # Gate: fail the BTC leg's min-qty check.
    import src.units.accounts.qty_legalize as _ql
    monkeypatch.setattr(_ql, "legalize_qty",
                        lambda qty, *, symbol, **k: _ql.LegalizedQty(
                            qty=qty, ok=(symbol != "BTCUSDT"),
                            reason="" if symbol != "BTCUSDT" else "below_venue_min_qty",
                            venue_min=0.001, step=0.001, source="instrument_profile"))
    import src.config.accounts_loader as _al
    monkeypatch.setattr(_al, "load_accounts_dict",
                        lambda *a, **k: {"bybit_1": {"exchange": "bybit",
                                                     "account_class": "paper",
                                                     "risk": {"risk_pct": 0.015}}})
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "trade_journal_db_path", lambda: str(tmp_path / "j.db"))
    import src.runtime.pairs_soak as _soak
    monkeypatch.setattr(_soak, "record_pairs_soak", lambda rec: captured.append(rec) or True)

    px.run_pairs_tick({})
    assert len(captured) == 1
    assert captured[0]["event"] == "skip_size"       # blocked, not placed
    assert captured[0].get("min_qty_block")           # carries the blocked-leg detail


def test_run_pairs_tick_shadow_places_nothing(tmp_path, monkeypatch):
    """A shadow-execution pair with a live entry signal writes a shadow_open soak
    row and NEVER touches the PLACEMENT path (execute_pkg). It DOES read the
    account balance (a read, not an order) so the would-be budget is faithful —
    derived from the canonical basis (balance × risk_pct), never a hardcoded $."""
    ca, cb = _extended_spread()
    captured = []

    monkeypatch.setattr(px, "_load_pairs_config", lambda path=None: {
        "account_id": "bybit_1", "pairs_risk_fraction": 1.0,
        "pairs": [{"name": "pairs_sol_btc", "symbol_a": "SOLUSDT",
                   "symbol_b": "BTCUSDT", "execution": "shadow",
                   "timeframe": "1h", "hedge_beta": "one"}],
    })
    monkeypatch.setattr(px, "_fetch_leg",
                        lambda sym, tf, lim, s: (list(ca) if sym == "SOLUSDT" else list(cb), "T1"))
    monkeypatch.setattr(px, "_pair_is_open", lambda *a, **k: False)
    monkeypatch.setattr(px, "_held_leg_symbols", lambda *a, **k: set())
    monkeypatch.setattr(px, "_count_correlated_open", lambda *a, **k: 0)
    monkeypatch.setattr(px, "_save_decision_bars", lambda state: None)
    monkeypatch.setattr(px, "_load_decision_bars", lambda: {})
    # The budget derive builds a READ client + reads the balance (allowed — a read,
    # not an order). Stub both so no real socket opens.
    import src.units.accounts.clients as _clients
    monkeypatch.setattr(_clients, "bybit_client_for", lambda acct: object())
    import src.units.accounts.execute as _exec
    monkeypatch.setattr(_exec, "_fetch_balance", lambda *a, **k: 100000.0)
    # PLACEMENT is the bug in shadow mode — make execute_pkg explode if ever called.
    def _boom(*a, **k):
        raise AssertionError("shadow mode must not place an order (execute_pkg)")
    monkeypatch.setattr(_exec, "execute_pkg", _boom)
    # Pass the pre-placement min-qty gate so this test exercises the shadow path
    # (the gate itself is covered by test_legs_below_min_qty_* + the live test).
    import src.units.accounts.qty_legalize as _ql
    monkeypatch.setattr(_ql, "legalize_qty",
                        lambda qty, **k: _ql.LegalizedQty(qty=qty, ok=True, reason="",
                                                          venue_min=0.0, step=0.0,
                                                          source="instrument_profile"))

    import src.config.accounts_loader as _al
    monkeypatch.setattr(_al, "load_accounts_dict",
                        lambda *a, **k: {"bybit_1": {"exchange": "bybit",
                                                     "account_class": "paper",
                                                     "risk": {"risk_pct": 0.015}}})
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "trade_journal_db_path", lambda: str(tmp_path / "j.db"))

    import src.runtime.pairs_soak as _soak
    monkeypatch.setattr(_soak, "record_pairs_soak", lambda rec: captured.append(rec) or True)

    px.run_pairs_tick({})
    assert len(captured) == 1
    assert captured[0]["event"] == "shadow_open"       # computed, not placed
    assert captured[0]["pair"] == "SOLUSDT/BTCUSDT"


# ── the third leg-state: half-open ──────────────────────────────────────────
# `_close_pair` is best-effort PER LEG and deliberately leaves a leg that failed
# to flatten open. The predicate the tick used asked only "are BOTH legs open?",
# so leg-A-closed / leg-B-stranded answered False — indistinguishable from a pair
# that was never opened. The tick then saw no position and was free to open a
# fresh pair, stacking a second journal row on the stranded leg's symbol over one
# netted exchange position. Nothing else owns the cleanup: the netting reconciler
# skips pairs rows by design, precisely because this executor is supposed to own
# its own legs (BL-20260808-PAIRS-DIVERGENCE-UNOWNED).

def _leg_state_pair():
    return {"name": "pairs_sol_btc", "symbol_a": "SOLUSDT", "symbol_b": "BTCUSDT"}


def _patch_leg_openness(monkeypatch, open_symbols):
    import src.runtime.positions as _pos
    monkeypatch.setattr(
        _pos, "has_open_trade_for_strategy",
        lambda account_id, symbol, strategy, **k: symbol in open_symbols)


def test_leg_state_names_all_three_cases(monkeypatch):
    pair = _leg_state_pair()
    _patch_leg_openness(monkeypatch, {"SOLUSDT", "BTCUSDT"})
    assert px._pair_leg_state(pair, "bybit_1", None) == "open"
    _patch_leg_openness(monkeypatch, set())
    assert px._pair_leg_state(pair, "bybit_1", None) == "flat"
    # The case that had no name and was being read as "flat".
    _patch_leg_openness(monkeypatch, {"SOLUSDT"})
    assert px._pair_leg_state(pair, "bybit_1", None) == "half_open"
    _patch_leg_openness(monkeypatch, {"BTCUSDT"})
    assert px._pair_leg_state(pair, "bybit_1", None) == "half_open"


def test_half_open_is_not_reported_as_the_pair_being_on(monkeypatch):
    """`_pair_is_open` keeps its meaning — which is exactly why it must not be
    the thing that DECIDES. It answers False for half-open, same as for flat."""
    _patch_leg_openness(monkeypatch, {"SOLUSDT"})
    assert px._pair_is_open(_leg_state_pair(), "bybit_1", None) is False


def test_half_open_pair_cleans_up_and_places_NOTHING(tmp_path, monkeypatch):
    """The regression this exists to prevent.

    One leg stranded open. The tick must NOT read that as flat and open a fresh
    pair on top of it; it must flatten the stranded leg and emit a `half_open`
    soak row instead.
    """
    ca, cb = _extended_spread()   # a spread wide enough to WANT to open
    captured = []
    monkeypatch.setattr(px, "_load_pairs_config", lambda path=None: {
        "account_id": "bybit_1", "pairs_risk_fraction": 1.0,
        "pairs": [{"name": "pairs_sol_btc", "symbol_a": "SOLUSDT",
                   "symbol_b": "BTCUSDT", "execution": "live",
                   "timeframe": "1h", "hedge_beta": "one"}],
    })
    monkeypatch.setattr(
        px, "_fetch_leg",
        lambda sym, tf, lim, s: (list(ca) if sym == "SOLUSDT" else list(cb), "T1"))
    monkeypatch.setattr(px, "_save_decision_bars", lambda state: None)
    monkeypatch.setattr(px, "_load_decision_bars", lambda: {})
    # Exactly one leg open => half_open.
    _patch_leg_openness(monkeypatch, {"SOLUSDT"})
    import src.units.accounts.clients as _clients
    monkeypatch.setattr(_clients, "bybit_client_for", lambda acct: object())
    import src.units.accounts.execute as _exec
    monkeypatch.setattr(_exec, "_fetch_balance", lambda *a, **k: 100000.0)
    monkeypatch.setattr(
        _exec, "execute_pkg",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must NOT open a new pair over a stranded leg")))
    import src.config.accounts_loader as _al
    monkeypatch.setattr(
        _al, "load_accounts_dict",
        lambda *a, **k: {"bybit_1": {"exchange": "bybit", "account_class": "paper",
                                     "risk": {"risk_pct": 0.015}}})
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "trade_journal_db_path", lambda: str(tmp_path / "j.db"))
    import src.runtime.pairs_soak as _soak
    monkeypatch.setattr(_soak, "record_pairs_soak", lambda rec: captured.append(rec) or True)
    closes = []
    monkeypatch.setattr(
        px, "_close_pair",
        lambda *a, **k: closes.append(a[3]) or {"closed": True, "outcome": a[3]})
    alerts = []
    monkeypatch.setattr(px, "_alert_half_open_pair",
                        lambda *a, **k: alerts.append((a, k)))

    px.run_pairs_tick({})

    assert len(captured) == 1
    assert captured[0]["event"] == "half_open"
    assert captured[0]["stranded_legs"] == ["SOLUSDT"]
    assert captured[0]["cleanup_confirmed"] is True
    assert closes == ["half_open_cleanup"], "the stranded leg must be flattened"
    assert alerts, "a naked leg in a market-neutral sleeve must be alerted"


def test_half_open_in_shadow_mode_alerts_but_places_no_order(tmp_path, monkeypatch):
    """A `shadow` pair has no order authority, so it must not try to close —
    but it must still say the state exists rather than silently re-deciding."""
    ca, cb = _extended_spread()
    captured = []
    monkeypatch.setattr(px, "_load_pairs_config", lambda path=None: {
        "account_id": "bybit_1", "pairs_risk_fraction": 1.0,
        "pairs": [{"name": "pairs_sol_btc", "symbol_a": "SOLUSDT",
                   "symbol_b": "BTCUSDT", "execution": "shadow",
                   "timeframe": "1h", "hedge_beta": "one"}],
    })
    monkeypatch.setattr(
        px, "_fetch_leg",
        lambda sym, tf, lim, s: (list(ca) if sym == "SOLUSDT" else list(cb), "T1"))
    monkeypatch.setattr(px, "_save_decision_bars", lambda state: None)
    monkeypatch.setattr(px, "_load_decision_bars", lambda: {})
    _patch_leg_openness(monkeypatch, {"BTCUSDT"})
    import src.config.accounts_loader as _al
    monkeypatch.setattr(
        _al, "load_accounts_dict",
        lambda *a, **k: {"bybit_1": {"exchange": "bybit", "account_class": "paper",
                                     "risk": {"risk_pct": 0.015}}})
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "trade_journal_db_path", lambda: str(tmp_path / "j.db"))
    import src.runtime.pairs_soak as _soak
    monkeypatch.setattr(_soak, "record_pairs_soak", lambda rec: captured.append(rec) or True)
    monkeypatch.setattr(
        px, "_close_pair",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("shadow must not place a closing order")))
    alerts = []
    monkeypatch.setattr(px, "_alert_half_open_pair",
                        lambda *a, **k: alerts.append((a, k)))

    px.run_pairs_tick({})

    assert captured and captured[0]["event"] == "half_open"
    assert captured[0]["cleanup_confirmed"] is False
    assert captured[0]["stranded_legs"] == ["BTCUSDT"]
    assert alerts


def test_half_open_alert_never_raises(monkeypatch):
    import src.runtime.outcomes as _out
    monkeypatch.setattr(
        _out, "report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    px._alert_half_open_pair("A/B", "bybit_1", stranded=["A"], cleaned=False)


def test_half_open_alert_severity_reflects_whether_it_is_still_naked(monkeypatch):
    """CRITICAL while the leg stands, WARN once flattened — the exposure is the
    thing being graded, not the event."""
    import src.runtime.outcomes as _out
    calls = []
    monkeypatch.setattr(_out, "report",
                        lambda *a, **k: calls.append((a, k.get("level"))))
    px._alert_half_open_pair("A/B", "bybit_1", stranded=["A"], cleaned=True)
    px._alert_half_open_pair("A/B", "bybit_1", stranded=["A"], cleaned=False)
    assert calls[0][0][1] == "cleaned"
    assert calls[1][0][1] == "unresolved"
    assert calls[0][1] != calls[1][1], "an un-flattened naked leg is more severe"


# ── the half-open safety check runs EVERY TICK, not once per bar ────────────
# BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN. The leg-state read used to
# sit BELOW the once-per-bar `decision_bars` dedup, so a naked leg was not even
# looked at until the next bar — measured at 62/63/64 minutes on the live
# journal for a condition this module's own alert grades CRITICAL.

def _half_open_tick_env(tmp_path, monkeypatch, *, closed_ok=True):
    """Wire run_pairs_tick for a LIVE pair whose leg-state reads half_open."""
    ca, cb = _extended_spread()
    soak, closes = [], []
    monkeypatch.setattr(px, "_load_pairs_config", lambda path=None: {
        "account_id": "bybit_1", "pairs_risk_fraction": 1.0,
        "pairs": [{"name": "pairs_sol_btc", "symbol_a": "SOLUSDT",
                   "symbol_b": "BTCUSDT", "execution": "live",
                   "timeframe": "1h", "hedge_beta": "one"}],
    })
    # Same bar on every tick — this is precisely what the dedup suppresses.
    monkeypatch.setattr(px, "_fetch_leg",
                        lambda sym, tf, lim, s: (
                            list(ca) if sym == "SOLUSDT" else list(cb), "SAME_BAR"))
    monkeypatch.setattr(px, "_pair_leg_state", lambda *a, **k: "half_open")
    # exactly one leg stranded
    import src.runtime.positions as _pos
    monkeypatch.setattr(_pos, "has_open_trade_for_strategy",
                        lambda acct, sym, strat, **k: sym == "SOLUSDT")
    monkeypatch.setattr(px, "_close_pair",
                        lambda *a, **k: closes.append(a) or {"closed": closed_ok})
    monkeypatch.setattr(px, "_held_leg_symbols", lambda *a, **k: set())
    monkeypatch.setattr(px, "_count_correlated_open", lambda *a, **k: 0)
    # ⚠️ THE DEDUP STATE MUST PERSIST ACROSS TICKS or this harness cannot test
    # the thing it exists to test. run_pairs_tick calls _load_decision_bars() on
    # EVERY invocation, so the usual `lambda: {}` stub hands back a fresh empty
    # dict each tick and the once-per-bar dedup never fires between calls —
    # which made an earlier version of these tests pass against the very bug
    # they were written to catch. Mirror the real file-backed round-trip.
    bars: dict = {}
    monkeypatch.setattr(px, "_load_decision_bars", lambda: dict(bars))
    monkeypatch.setattr(px, "_save_decision_bars", lambda state: bars.update(state))
    import src.units.accounts.clients as _clients
    monkeypatch.setattr(_clients, "bybit_client_for", lambda acct: object())
    import src.units.accounts.execute as _exec
    monkeypatch.setattr(_exec, "_fetch_balance", lambda *a, **k: 100000.0)
    monkeypatch.setattr(_exec, "execute_pkg",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not place while half-open")))
    import src.config.accounts_loader as _al
    monkeypatch.setattr(_al, "load_accounts_dict",
                        lambda *a, **k: {"bybit_1": {"exchange": "bybit",
                                                     "account_class": "paper",
                                                     "risk": {"risk_pct": 0.015}}})
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "trade_journal_db_path", lambda: str(tmp_path / "j.db"))
    import src.runtime.pairs_soak as _soak
    monkeypatch.setattr(_soak, "record_pairs_soak", lambda rec: soak.append(rec) or True)
    px._state_alert_last.clear()
    return soak, closes


def test_half_open_cleanup_retries_on_every_tick_of_the_same_bar(tmp_path, monkeypatch):
    """THE REGRESSION TEST FOR THE REORDER. Three ticks land on ONE bar with a
    stranded leg; the cleanup must be attempted on all three. Before the fix the
    dedup swallowed ticks 2 and 3, so a naked directional position in a
    market-neutral sleeve stood until the next bar."""
    soak, closes = _half_open_tick_env(tmp_path, monkeypatch, closed_ok=False)
    for _ in range(3):
        px.run_pairs_tick({})
    assert len(closes) == 3, (
        f"cleanup ran {len(closes)}x on 3 ticks of one bar — the safety check is "
        "back below the once-per-bar dedup")


def test_half_open_never_places_and_consumes_the_bar(tmp_path, monkeypatch):
    """Moving the check above the dedup must NOT weaken 'place nothing this
    bar': execute_pkg raises if called, and the branch marks the bar consumed."""
    soak, closes = _half_open_tick_env(tmp_path, monkeypatch, closed_ok=True)
    px.run_pairs_tick({})           # raises via execute_pkg if it ever places
    assert [r["event"] for r in soak] == ["half_open"]
    assert soak[0]["cleanup_confirmed"] is True
    assert soak[0]["stranded_legs"] == ["SOLUSDT"]


def test_half_open_unresolved_alert_is_rate_limited_but_cleanup_is_not(
        tmp_path, monkeypatch):
    """The per-tick branch must not become a per-tick CRITICAL — that is the
    desensitized alarm CLAUDE.md calls a P1 in its own right. The ALERT is a
    level and is cooled down; the CLEANUP is a safety action and is not."""
    sent = []
    import src.runtime.outcomes as _out
    monkeypatch.setattr(_out, "report", lambda *a, **k: sent.append((a, k)))
    soak, closes = _half_open_tick_env(tmp_path, monkeypatch, closed_ok=False)
    for _ in range(5):
        px.run_pairs_tick({})
    assert len(closes) == 5, "cleanup must retry every tick"
    assert len(sent) == 1, f"expected 1 CRITICAL, got {len(sent)} — alarm fatigue"
    assert sent[0][0][1] == "unresolved"
    # the soak log follows the alert, so a reviewer's by_event count is not
    # inflated 5x for one standing strand
    assert len(soak) == 1, f"expected 1 soak row, got {len(soak)}"


def test_half_open_cleaned_alert_is_not_suppressed(monkeypatch):
    """`cleaned` is an EDGE (condition resolved), not a level. Suppressing it
    would hide a genuinely NEW strand landing inside a cooldown window."""
    sent = []
    import src.runtime.outcomes as _out
    monkeypatch.setattr(_out, "report", lambda *a, **k: sent.append((a, k)))
    px._state_alert_last.clear()
    for _ in range(4):
        assert px._half_open_should_report("A/B", cleaned=True) is True
        px._alert_half_open_pair("A/B", "acct", stranded=["A"], cleaned=True)
    assert len(sent) == 4, "a resolved strand must alert every time"
    assert all(s[0][1] == "cleaned" for s in sent)


def test_resolved_strand_clears_the_unresolved_cooldown(monkeypatch):
    """After a strand is fixed, the NEXT unresolved strand must alert
    immediately rather than inherit the fixed one's suppression window."""
    sent = []
    import src.runtime.outcomes as _out
    monkeypatch.setattr(_out, "report", lambda *a, **k: sent.append((a, k)))
    px._state_alert_last.clear()
    assert px._half_open_should_report("A/B", cleaned=False) is True
    assert px._half_open_should_report("A/B", cleaned=False) is False
    assert px._half_open_should_report("A/B", cleaned=True) is True          # resolved
    assert px._half_open_should_report("A/B", cleaned=False) is True, \
        "a fresh strand after a fix must not be swallowed by the old cooldown"


def test_soak_row_survives_an_alert_failure(tmp_path, monkeypatch):
    """THE DURABLE LOG MUST NOT BE A CASUALTY OF A BROKEN PING. An alert that
    raises must not break the tick AND must not cost the soak row — the log is
    what a reviewer reads back, so it matters more than the alert, not less."""
    import src.runtime.outcomes as _out
    monkeypatch.setattr(_out, "report",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    soak, closes = _half_open_tick_env(tmp_path, monkeypatch, closed_ok=True)
    px.run_pairs_tick({})                       # must not raise
    assert len(soak) == 1, "the soak row was lost when the alert failed"
    assert soak[0]["event"] == "half_open"
