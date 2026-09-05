"""BL-20260905 — an EMPTY sizing map is refused ONCE, with a named cause.

Object: WO-20260905-EMPTY-SIZING-MAP-IS-RE-EMITTED-INSTEAD-OF-REFUSED (PR #11035).
Row: BL-20260905-MES-TREND-LONG-1D-RE-EMITTED-ONE-DAILY-SIGNAL-SEVEN-TIMES-IN-49-MINUTES-WHEN-SIZING-RETURNED-EMPTY

THE MEASURED DEFECT (population: all 8 ``order_packages`` rows for
``mes_trend_long_1d`` with ``filter_state='applied'``, read 2026-09-05):
seven of them were created on 2026-06-01 between 09:06:57 and 09:56:04 —
roughly every 8 minutes for 49 minutes — from ONE daily signal (all seven
share ``entry_time='2026-06-01 00:00:00'`` and ``donchian_hi=7611.75``).
All seven carry ``"sized_qty_by_account": {}`` and all seven were orphaned
unexecuted. The one package that worked carries ``{"ib_paper": 15.0}``.

Nothing reached a venue and no money moved. The harm is (a) a retry loop with
no ceiling and (b) seven phantom order packages per occurrence, polluting
exactly the order-package data the M7 review packets grade strategies on. It
corrupts the evidence base, not the book.

WHAT IS PINNED HERE
-------------------
1. ``test_empty_sizing_map_emits_one_package_and_names_the_cause`` — seven
   consecutive ticks of one signal whose sizing map comes back EMPTY produce
   exactly ONE order package, and that package records a cause that NAMES why
   nothing was sized (which account, which rule). A bare count is not a cause:
   the whole failure was seven identical attempts saying nothing about
   themselves.
2. ``test_positive_control_*`` — THE SAME PATH, same seven ticks, with a real
   sizing map still emits on every tick and places trades. Without this, "no
   packages emitted" passes trivially by breaking emission entirely.
3. ``test_a_different_signal_is_not_muted`` — the brake is keyed on the
   SIGNAL's identity, not on the strategy: a refusal of one signal must not
   silence the next one.
4. ``test_the_brake_never_suppresses_silently`` — the gate refuses to fire on
   a package that recorded no cause. A brake that quietly swallowed the
   re-emission would convert a noisy failure into a silent one, which is the
   exact class ``silent_refusal_alert`` exists to catch.

The harness is deliberately NOT MES-specific. A daily leg producing seven
packages in 49 minutes is prima facie a retry with no ceiling, and the same
shape applies to any leg whose sizing returns empty — so the fixture uses a
plain bybit/BTCUSDT dispatch and varies only the one thing under test: whether
any account reaches ``RiskManager.position_size``.
"""
from __future__ import annotations

# collapsed-state: applied — this file BRANCHES on nothing. The token appears
# once, in the module docstring, quoting the backlog row's POPULATION statement
# ("all 8 order_packages for mes_trend_long_1d, filter_state 'applied'") — which
# CLAUDE.md § "Always state the population" requires. The db_explorer
# filter_state states (not_requested / ignored_unknown_column) belong to the
# Data Explorer's read surface and are neither produced nor consumed here;
# enumerating them in prose would say nothing true about this test.

import json
import logging
import sys
import types
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

# pipeline.py needs matplotlib at import time via signal_notifications.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

from src.runtime.order_bridge import (  # noqa: E402
    _signal_to_order_package,
    signal_key_for_signal,
)
from src.runtime.strategy_monocle import (  # noqa: E402
    _empty_sizing_refusal_for_signal,
)
from src.units.accounts.risk import RiskManager  # noqa: E402

STRATEGY = "empty_sizing_probe"
SYMBOL = "BTCUSDT"
SETTINGS: Dict[str, Any] = {}

# The 2026-06-01 cadence: seven attempts from one signal.
TICKS = 7


class _Account:
    """Stub account mirroring tests/test_s026_g2_position_size.py's fixture.

    ``strategies`` is the ONLY field the two scenarios differ on — it is what
    ``Coordinator._dispatch_exclusion_reason`` consults, and therefore what
    decides whether any account reaches the sizer at all.
    """

    def __init__(self, name: str, strategies: Optional[List[str]]):
        self.name = name
        self.exchange = "bybit"
        self.account_type = "regular"
        self.risk_manager = RiskManager(
            {"risk_pct": 0.01, "min_balance_usd": 50, "leverage": 100}
        )
        self.dry_run = True
        self.configured = True
        self.api_key_env = ""
        self.market_type = "spot"
        self.strategies = strategies

    def place_order(self, pkg, *, dry_run=None):
        return f"dry-{self.name}-1"


def _signal(entry_time: str = "2026-06-01 00:00:00") -> Dict[str, Any]:
    """One bar-close signal, re-presented unchanged on every tick.

    ``entry_time`` is what made the seven 2026-06-01 packages provably ONE
    signal rather than seven, so it is what distinguishes signals here too.
    """
    return {
        "symbol": SYMBOL,
        "side": "buy",
        "entry_price": 50_000.0,
        "stop_loss": 49_500.0,
        "take_profit": 51_000.0,
        "meta": {
            "strategy_name": STRATEGY,
            "entry_time": entry_time,
            "confidence": 0.7,
            "account_balances_usd": {"acc_a": 10_000.0, "acc_b": 5_000.0},
        },
    }


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Point every ``trade_journal_db_path()`` caller at a fresh SQLite file.

    A REAL database, not a stub: the gate reads ``meta`` back as a JSON string
    from SQLite, so a dict-returning stub would not exercise the round-trip
    that has to work in production.
    """
    db_file = tmp_path / "trade_journal.db"
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "trade_journal_db_path", lambda: str(db_file))
    from src.units.db.database import Database
    Database(db_path=str(db_file))  # create_tables()
    return str(db_file)


def _packages(journal_path: str) -> List[Dict[str, Any]]:
    from src.units.db.database import Database
    db = Database(db_path=journal_path)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM order_packages ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _run_ticks(monkeypatch, tmp_path, journal_path, accounts, signal, ticks=TICKS):
    """Drive *ticks* consecutive dispatch attempts of the SAME signal.

    Mirrors the pipeline's last pre-dispatch gate and the dispatch itself:
    compute the signal's identity, ask the empty-sizing brake, and dispatch
    only when it does not block.
    """
    from src.core.coordinator import Coordinator

    monkeypatch.setattr("src.units.accounts.load_accounts", lambda _p: accounts)
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("accounts: {}\n")

    coord = Coordinator()
    blocks: List[Dict[str, Any]] = []
    dispatches: List[List[Dict[str, Any]]] = []
    sized_maps: List[Dict[str, float]] = []

    for _ in range(ticks):
        key = signal_key_for_signal(signal, SETTINGS)
        blocked = _empty_sizing_refusal_for_signal(
            STRATEGY, key, symbol=signal["symbol"],
        )
        if blocked is not None:
            blocks.append(blocked)
            continue
        pkg = _signal_to_order_package(signal, SETTINGS)
        results = coord.multi_account_execute(
            pkg, accounts_path=str(accounts_path), dry_run=True,
        )
        dispatches.append(results)
        sized_maps.append(dict((pkg.meta or {}).get("sized_qty_by_account") or {}))

    return blocks, dispatches, sized_maps


# ---------------------------------------------------------------------------
# 1. The brake
# ---------------------------------------------------------------------------

def test_empty_sizing_map_emits_one_package_and_names_the_cause(
    monkeypatch, tmp_path, journal, caplog,
):
    """Seven ticks, empty sizing map → ONE package, with a cause that says why.

    Both accounts declare a different strategy, so every one is dropped BEFORE
    ``position_size`` runs and ``sized_qty_by_account`` comes back ``{}`` —
    the 2026-06-01 shape.
    """
    accounts = [
        _Account("acc_a", strategies=["vwap"]),
        _Account("acc_b", strategies=["turtle_soup"]),
    ]
    signal = _signal()

    with caplog.at_level(logging.WARNING):
        blocks, dispatches, sized_maps = _run_ticks(
            monkeypatch, tmp_path, journal, accounts, signal,
        )

    # The dispatch really did size nothing — this is the condition under test,
    # not an incidental one.
    assert sized_maps == [{}], (
        "fixture must drive sizing to an EMPTY map, got %r" % (sized_maps,)
    )

    # ONE attempt, six refusals — not seven attempts.
    assert len(dispatches) == 1
    assert len(blocks) == TICKS - 1

    rows = _packages(journal)
    assert len(rows) == 1, (
        "one signal that sizes nothing must mint ONE order package, not %d "
        "(the 2026-06-01 burst minted 7)" % len(rows)
    )

    row = rows[0]

    # --- the cause is recorded on the package, and NAMES the reason ---------
    meta = json.loads(row["meta"])
    refusal = meta["empty_sizing_refusal"]
    cause = refusal["cause"]
    assert cause.startswith("all_accounts_excluded_pre_sizing:"), cause
    # It names WHICH account and WHICH rule — not a count.
    assert "acc_a=strategy_not_assigned" in cause
    assert "acc_b=strategy_not_assigned" in cause
    assert STRATEGY in cause
    assert refusal["accounts_loaded"] == 2
    assert refusal["accounts_reached_sizer"] == 0
    assert refusal["excluded_by_account"]["acc_a"].startswith(
        "strategy_not_assigned"
    )
    assert refusal["signal_key"] == signal_key_for_signal(signal, SETTINGS)
    assert refusal["refused_at"]

    # --- the package is terminal, and its close_reason carries the cause ----
    assert row["status"] == "rejected"
    assert row["close_reason"].startswith("empty_sizing_map: ")
    assert "strategy_not_assigned" in row["close_reason"]

    # --- the refusal is LOGGED, with the cause -----------------------------
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    emitted = [m for m in warnings if "EMPTY SIZING MAP" in m]
    assert len(emitted) == 1, (
        "the refusal must be logged exactly once, got %d: %r" % (len(emitted), emitted)
    )
    assert "strategy_not_assigned" in emitted[0]
    assert "refusing this signal ONCE rather than re-emitting" in emitted[0]

    # --- every block also carries the cause; none is silent -----------------
    assert all(b["cause"] == cause for b in blocks)
    assert all(b["order_package_id"] == row["order_package_id"] for b in blocks)


# ---------------------------------------------------------------------------
# 2. Positive control — the same path still emits when sizing works
# ---------------------------------------------------------------------------

def test_positive_control_real_sizing_map_still_emits_every_tick(
    monkeypatch, tmp_path, journal,
):
    """THE CONTROL. Same fixture, same seven ticks, accounts that DO declare
    the strategy: the brake never engages, every tick emits, and the packages
    carry a real per-account sizing map.

    Without this, ``test_empty_sizing_map_emits_one_package_and_names_the_cause``
    would pass just as well if emission were broken outright.
    """
    accounts = [
        _Account("acc_a", strategies=[STRATEGY]),
        _Account("acc_b", strategies=[STRATEGY]),
    ]
    signal = _signal()

    blocks, dispatches, sized_maps = _run_ticks(
        monkeypatch, tmp_path, journal, accounts, signal,
    )

    assert blocks == [], "the brake must not fire when sizing returns a real map"
    assert len(dispatches) == TICKS

    # Sizing produced real quantities on both accounts, every tick.
    assert len(sized_maps) == TICKS
    for sized in sized_maps:
        assert set(sized) == {"acc_a", "acc_b"}, sized
        assert all(q > 0 for q in sized.values()), sized

    # Orders were actually routed, so "still emits" means more than a row.
    for results in dispatches:
        assert results
        assert all(r["trade_id"] is not None for r in results), results
        assert all(r["error"] is None for r in results), results

    rows = _packages(journal)
    assert len(rows) == TICKS
    for row in rows:
        assert "empty_sizing_refusal" not in json.loads(row["meta"])


# ---------------------------------------------------------------------------
# 3. The brake is per-SIGNAL, not a strategy-wide mute
# ---------------------------------------------------------------------------

def test_a_different_signal_is_not_muted(monkeypatch, tmp_path, journal):
    """A refusal holds for the signal that caused it and lifts for the next.

    The 2026-06-01 burst was ONE signal re-emitted seven times. Suppressing
    the strategy instead of the signal would stop the leg trading the next
    bar — a worse failure than the one being fixed.
    """
    accounts = [_Account("acc_a", strategies=["vwap"])]

    first = _signal(entry_time="2026-06-01 00:00:00")
    _, dispatches_a, _ = _run_ticks(
        monkeypatch, tmp_path, journal, accounts, first, ticks=3,
    )
    assert len(dispatches_a) == 1

    # Next bar: a genuinely different signal.
    second = _signal(entry_time="2026-06-02 00:00:00")
    assert signal_key_for_signal(second, SETTINGS) != signal_key_for_signal(
        first, SETTINGS
    )
    blocks_b, dispatches_b, _ = _run_ticks(
        monkeypatch, tmp_path, journal, accounts, second, ticks=3,
    )

    assert len(dispatches_b) == 1, "the new signal must be allowed one attempt"
    assert len(blocks_b) == 2
    assert len(_packages(journal)) == 2, "one package per signal, two signals"


# ---------------------------------------------------------------------------
# 4. The brake cannot suppress silently
# ---------------------------------------------------------------------------

def test_the_brake_never_suppresses_silently(monkeypatch, tmp_path, journal):
    """The gate fires only on a package that recorded a named cause.

    A package with no ``empty_sizing_refusal`` stamp — anything the coordinator
    did not deliberately refuse — is not gate-blocking. Suppressing without a
    cause would trade a noisy failure for a silent one.
    """
    from src.units.db.database import Database

    signal = _signal()
    key = signal_key_for_signal(signal, SETTINGS)
    db = Database(db_path=journal)

    # A package for this exact signal, but carrying no refusal record.
    db.insert_order_package({
        "order_package_id": "pkg-no-cause",
        "strategy_name": STRATEGY,
        "symbol": SYMBOL,
        "direction": "long",
        "entry": 50_000.0,
        "sl": 49_500.0,
        "tp": 51_000.0,
        "status": "rejected",
        "meta": {"strategy_name": STRATEGY},
    })
    assert _empty_sizing_refusal_for_signal(STRATEGY, key, symbol=SYMBOL) is None

    # And a package whose refusal is for a DIFFERENT signal.
    db.insert_order_package({
        "order_package_id": "pkg-other-signal",
        "strategy_name": STRATEGY,
        "symbol": SYMBOL,
        "direction": "long",
        "entry": 50_000.0,
        "sl": 49_500.0,
        "tp": 51_000.0,
        "status": "rejected",
        "meta": {
            "empty_sizing_refusal": {
                "cause": "all_accounts_excluded_pre_sizing: acc_a=not_configured",
                "signal_key": "0000000000000000",
            },
        },
    })
    assert _empty_sizing_refusal_for_signal(STRATEGY, key, symbol=SYMBOL) is None

    # An un-keyable signal is never braked either.
    assert _empty_sizing_refusal_for_signal(STRATEGY, "", symbol=SYMBOL) is None


def test_kill_switch_disables_the_brake(monkeypatch, tmp_path, journal):
    """``STRATEGY_EMPTY_SIZING_BRAKE_DISABLED`` is the rollback knob, mirroring
    ``STRATEGY_BAR_DEBOUNCE_DISABLED``. Default-ON with a disable switch — not
    a default-off ``*_ENABLED`` gate (CLAUDE.md § the two execution gates)."""
    accounts = [_Account("acc_a", strategies=["vwap"])]
    signal = _signal()

    monkeypatch.setenv("STRATEGY_EMPTY_SIZING_BRAKE_DISABLED", "1")
    _, dispatches, _ = _run_ticks(
        monkeypatch, tmp_path, journal, accounts, signal, ticks=3,
    )
    assert len(dispatches) == 3, "kill-switch must restore the pre-fix behaviour"
    # ...and the pre-fix behaviour is exactly the defect: one signal, three
    # ticks, three packages. This is the measurement the brake removes.
    assert len(_packages(journal)) == 3
