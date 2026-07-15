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


def test_load_real_pairs_config_live_on_bybit_1():
    cfg = px._load_pairs_config("config/pairs.yaml")
    pairs = cfg.get("pairs") or []
    assert len(pairs) == 4
    # Operator-approved 2026-07-15: all 4 live on bybit_1 (Bybit demo / paper venue).
    assert all(str(p.get("execution")).lower() == "live" for p in pairs)
    assert cfg.get("account_id") == "bybit_1"


def test_decision_bars_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(px, "_decision_bars_path", lambda: tmp_path / "bars.json")
    assert px._load_decision_bars() == {}
    px._save_decision_bars({"pairs_sol_btc": "111|222"})
    assert px._load_decision_bars() == {"pairs_sol_btc": "111|222"}


def test_reconstruct_open_state_from_pkg_meta(tmp_path, monkeypatch):
    import sqlite3 as _sq
    from datetime import datetime, timedelta, timezone
    db = tmp_path / "j.db"
    conn = _sq.connect(db)
    conn.execute("CREATE TABLE order_packages (id INTEGER PRIMARY KEY, "
                 "strategy_name TEXT, account_id TEXT, meta TEXT)")
    opened = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    import json as _j
    meta = _j.dumps({"pair_direction": "long_spread", "entry_spread": 0.5,
                     "stop_spread": 0.3, "opened_at_utc": opened, "bar_seconds": 3600})
    conn.execute("INSERT INTO order_packages (strategy_name, account_id, meta) VALUES (?,?,?)",
                 ("pairs_sol_btc_a", "bybit_1", meta))
    conn.commit()
    conn.close()
    pair = {"name": "pairs_sol_btc", "symbol_a": "SOLUSDT", "symbol_b": "BTCUSDT"}
    st = px._reconstruct_open_state(pair, "bybit_1", str(db))
    assert st is not None
    assert st.direction == "long_spread"
    assert st.entry_spread == 0.5 and st.stop_spread == 0.3
    assert st.bars_held == 3                          # ~3h at 1h bars


def test_reconstruct_open_state_absent_meta_is_none(tmp_path):
    assert px._reconstruct_open_state(
        {"name": "pairs_x", "symbol_a": "A", "symbol_b": "B"}, "acct", str(tmp_path / "no.db")) is None


def test_run_pairs_tick_shadow_places_nothing(tmp_path, monkeypatch):
    """A shadow-execution pair with a live entry signal writes a shadow_open soak
    row and NEVER touches an exchange client / placement path."""
    ca, cb = _extended_spread()
    captured = []

    monkeypatch.setattr(px, "_load_pairs_config", lambda path=None: {
        "account_id": "bybit_1", "risk_budget_usd": 20.0,
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
    # A live client build would be a bug in shadow mode — make it explode if called.
    def _boom(_):
        raise AssertionError("shadow mode must not place / build a client")
    import src.units.accounts.clients as _clients
    monkeypatch.setattr(_clients, "bybit_client_for", _boom)

    import src.config.accounts_loader as _al
    monkeypatch.setattr(_al, "load_accounts_dict",
                        lambda *a, **k: {"bybit_1": {"exchange": "bybit", "account_class": "paper"}})
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "trade_journal_db_path", lambda: str(tmp_path / "j.db"))

    import src.runtime.pairs_soak as _soak
    monkeypatch.setattr(_soak, "record_pairs_soak", lambda rec: captured.append(rec) or True)

    px.run_pairs_tick({})
    assert len(captured) == 1
    assert captured[0]["event"] == "shadow_open"       # computed, not placed
    assert captured[0]["pair"] == "SOLUSDT/BTCUSDT"
