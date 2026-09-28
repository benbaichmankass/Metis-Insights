"""The broker's own ``shorting_enabled`` gates shorts on every flagged account.

BL-20260823-ALPACA-SHORTING-FLAG-READ-NEVER-CONSUMED; operator "Build the gate
now", 2026-09-28. ``side_filter: long`` is config and covers only the accounts
it is declared on (``alpaca_live``); this reads the venue flag for EVERY alpaca
account, folded into the same ``effective_dry`` demotion.
"""
from __future__ import annotations

import textwrap

import pytest

from src.core.coordinator import Coordinator, OrderPackage
from src.runtime import broker_shorting_gate as G


class _Client:
    def __init__(self, flag, raises=False):
        self.flag, self.raises, self.calls = flag, raises, 0

    def account_status(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("boom")
        return {} if self.flag is None else {"shorting_enabled": self.flag}


@pytest.fixture(autouse=True)
def _fresh_cache():
    G._clear_cache()
    yield
    G._clear_cache()


# ------------------------------------------------------------- unit: states


@pytest.mark.parametrize("flag,state,refused", [
    (False, "disabled", True), (True, "enabled", False), (None, "unknown", False),
])
def test_short_is_refused_only_when_the_broker_says_disabled(flag, state, refused):
    c = _Client(flag)
    assert G.refuses_short("a", "alpaca", "short", lambda: c) == (refused, state)


def test_a_read_failure_is_unknown_and_fail_permissive():
    assert G.refuses_short("a", "alpaca", "short",
                           lambda: _Client(False, raises=True)) == (False, "unknown")


def test_no_client_is_unknown_not_disabled():
    assert G.refuses_short("a", "alpaca", "short", lambda: None) == (False, "unknown")


@pytest.mark.parametrize("direction", ["long", "", None, "flat"])
def test_a_non_short_never_reads_the_broker(direction):
    c = _Client(False)
    assert G.refuses_short("a", "alpaca", direction, lambda: c)[0] is False
    assert c.calls == 0


@pytest.mark.parametrize("exchange", ["bybit", "breakout", "interactive_brokers", None])
def test_unflagged_exchange_is_not_applicable(exchange):
    c = _Client(False)
    assert G.refuses_short("a", exchange, "short", lambda: c) == (False, "not_applicable")
    assert c.calls == 0


def test_a_real_reading_is_cached_and_unknown_is_retried():
    c = _Client(False)
    G.shorting_state("a", "alpaca", lambda: c, now=0.0)
    G.shorting_state("a", "alpaca", lambda: c, now=10.0)
    assert c.calls == 1
    G.shorting_state("a", "alpaca", lambda: c, now=G.CACHE_TTL_S + 1)
    assert c.calls == 2
    u = _Client(None)
    G.shorting_state("b", "alpaca", lambda: u, now=0.0)
    G.shorting_state("b", "alpaca", lambda: u, now=1.0)
    assert u.calls == 2


# -------------------------------------------------- through the coordinator


_YAML = textwrap.dedent("""\
    accounts:
      alpaca_x:
        type: regular
        exchange: alpaca
        api_key_env: ALPACA_KEY_X
        strategies: [spy_trend_long_1d]
        risk:
          max_dd_pct: 0.05
          daily_usd: 200
          pos_size: 1000
""")


def _pkg(direction):
    return OrderPackage(
        strategy="spy_trend_long_1d", symbol="SPY", direction=direction,
        entry=500.0, sl=490.0 if direction == "long" else 510.0,
        tp=530.0 if direction == "long" else 470.0, confidence=0.7,
        meta={"strategy_name": "spy_trend_long_1d"},
    )


@pytest.fixture()
def run(tmp_path, monkeypatch):
    p = tmp_path / "accounts.yaml"
    p.write_text(_YAML)
    monkeypatch.setenv("ALPACA_KEY_X", "k")
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "tj.db"))
    import src.core.coordinator as cm
    monkeypatch.setattr(cm, "_log_new_order_package", lambda pkg: None)
    monkeypatch.setattr(cm, "_has_open_position", lambda *a, **k: False)
    units = tmp_path / "units.yaml"
    units.write_text("units: {}\n")
    coord = Coordinator(units_path=str(units))

    def _go(direction, flag, *, net=0.0, side_filter="both"):
        client = _Client(flag)
        import src.units.accounts.clients as clients
        monkeypatch.setattr(clients, "alpaca_client_for", lambda cfg: client)
        import src.runtime.positions as positions
        monkeypatch.setattr(positions, "current_net_position_qty",
                            lambda *a, **k: net)
        import src.runtime.account_side_filter as asf
        monkeypatch.setattr(asf, "account_side_filter", lambda _id: side_filter)
        out = {"executed": [], "rejections": []}
        import src.units.accounts.execute as ex

        def fake_execute(pkg, acc, exchange_client=None, dry_run=None, **kw):
            out["executed"].append(bool(dry_run))
            return "dry-1" if dry_run else "live-1"

        def fake_reject(pkg, cfg, *, reason, status, **kw):
            out["rejections"].append((cfg.get("account_id"), reason, status))
            return True

        monkeypatch.setattr(ex, "execute_pkg", fake_execute)
        monkeypatch.setattr(ex, "log_rejection_to_journal", fake_reject)
        out["results"] = coord.multi_account_execute(
            _pkg(direction), accounts_path=str(p), dry_run=False,
            balance_fetcher=lambda _a: 10_000.0)
        out["client"] = client
        return out

    return _go


def test_short_on_a_shorting_disabled_account_is_refused_with_a_journal_row(run):
    """The blocker from review: the cause must survive in the DB, not only in
    a ~30-minute journald line."""
    out = run("short", False)
    assert out["client"].calls >= 1
    assert out["executed"] == []  # nothing sent, not even a dry would-be row
    assert out["rejections"] == [("alpaca_x", G.REFUSAL_TOKEN, "rejected")]
    assert out["results"][0]["error"] == G.REFUSAL_TOKEN


def test_long_only_control_is_unchanged_and_never_reads_the_flag(run, monkeypatch):
    reads = []
    real = G.shorting_state
    monkeypatch.setattr(G, "shorting_state",
                        lambda *a, **k: reads.append(a[0]) or real(*a, **k))
    out = run("long", False)
    assert out["executed"] == [False]
    assert out["rejections"] == []
    assert reads == []


def test_short_on_a_shorting_enabled_account_still_executes(run):
    out = run("short", True)
    assert out["executed"] == [False]
    assert out["rejections"] == []


@pytest.mark.parametrize("net", [3.0, None])
def test_a_short_that_may_close_a_held_long_is_never_blocked(run, net):
    """FLIP_POLICY=flat turns a short on a long-holding account into a close,
    and that close only runs on a non-dry account. A short gate must never
    block a close — nor treat an unreadable position as flat."""
    out = run("short", False, net=net)
    assert out["rejections"] == []
    assert out["executed"] == [False]


def test_a_short_adding_to_an_existing_short_is_still_refused(run):
    out = run("short", False, net=-2.0)
    assert out["rejections"] == [("alpaca_x", G.REFUSAL_TOKEN, "rejected")]


def test_both_gates_side_filter_long_and_shorting_disabled(run):
    """Precedence, pinned: side_filter demotes to dry FIRST, so the broker
    gate (inside `if not effective_dry:`) is not reached and the flag is not
    read. The short is still never sent. The side_filter dry row carrying no
    cause is the same gap, filed separately."""
    out = run("short", False, side_filter="long")
    assert out["executed"] == [True]
    assert out["rejections"] == []
    assert out["client"].calls == 0


def test_the_refusal_is_a_declared_policy_skip_not_a_dispatch_failure():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "core" / "coordinator.py").read_text()
    start = src.index("def _is_benign_noop")
    assert '"broker_shorting_disabled"' in src[start:start + 2500]


def test_gate_never_turns_an_account_live():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "core" / "coordinator.py").read_text()
    start = src.index("Per-ACCOUNT BROKER short gate")
    block = src[start:src.index("broker shorting gate lookup failed")]
    assert "if not effective_dry:" in block
    assert "effective_dry = False" not in block
    assert "log_rejection_to_journal(" in block


def test_the_cache_ttl_is_documented_and_600s():
    assert G.CACHE_TTL_S == 600.0
