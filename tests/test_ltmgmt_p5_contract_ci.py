"""P5 — CI guards enforcing the live-trade management contract.

Ref: docs/audits/live-trade-management-contract-2026-06-16.md § Design plan §3
(Enforcement); backlog BL-20260616-LTMGMT-P5CI.

Two guards that fail CI when the two-sided contract is broken *by construction*,
so a future strategy / integration can't ship non-compliant:

  (a) **Integration side** — every ``EXCHANGE_MAP`` integration must make an
      explicit ``EXCHANGE_MANAGEMENT_CAPS`` declaration (which of
      modify/close/partial_close/order_status/open_positions it implements, or
      an explicit empty set = "declares everything unsupported"). A NEW broker
      added to EXCHANGE_MAP without a caps entry fails this test — the gap is a
      failing test, not a silent default.

  (b) **Strategy side** — every registered strategy's ``monitor()`` returns
      **schema-valid verdicts** (``strategy_verdict.validate_verdict``) on
      representative input, beyond merely existing (the existence + signature
      guards live in tests/test_strategy_monitor_unit_resolution.py). A monitor
      that returns a malformed verdict dict fails this test. Raises on synthetic
      input are tolerated (the order-monitor wraps monitor() in try/except and
      the signature guard covers callability) — this guard targets the *shape*
      of what monitor() actually returns.
"""
from __future__ import annotations

import importlib

import pytest

from src.units.accounts import clients
from src.units.accounts.integrator import EXCHANGE_MAP
from src.runtime.strategy_verdict import validate_verdict
from src.runtime.exit_plan import (
    build_exit_plan_from_legacy,
    validate_exit_plan,
)

pytest.importorskip("pandas")
import pandas as pd  # noqa: E402

from src.runtime.pipeline import _STRATEGY_BUILDERS, monitor_unit_for  # noqa: E402
from src.runtime.intent_multiplexer import _resolve_builders  # noqa: E402


# ---------------------------------------------------------------------------
# (a) Integration side — EXCHANGE_MAP ⊆ EXCHANGE_MANAGEMENT_CAPS (explicit)
# ---------------------------------------------------------------------------

_MANAGEMENT_OPS = frozenset(
    {"modify", "close", "partial_close", "order_status", "open_positions"}
)


def test_every_exchange_map_integration_declares_management_caps():
    """A new integration in EXCHANGE_MAP must consciously declare its management
    capabilities (even if the empty set) — never default silently."""
    undeclared = [
        ex for ex in EXCHANGE_MAP if ex not in clients.EXCHANGE_MANAGEMENT_CAPS
    ]
    assert not undeclared, (
        "EXCHANGE_MAP integrations with no EXCHANGE_MANAGEMENT_CAPS entry "
        "(declare the wired ops, or frozenset() for 'unsupported', in "
        "src/units/accounts/clients.py::EXCHANGE_MANAGEMENT_CAPS): "
        + ", ".join(sorted(undeclared))
    )


def test_declared_management_caps_use_only_known_ops():
    """A caps declaration may only name ops the management interface knows —
    a typo'd op ('modyfy') would otherwise be a silently-dead declaration."""
    bad = []
    for ex, caps in clients.EXCHANGE_MANAGEMENT_CAPS.items():
        unknown = set(caps) - _MANAGEMENT_OPS
        if unknown:
            bad.append(f"{ex}: {sorted(unknown)}")
    assert not bad, (
        "EXCHANGE_MANAGEMENT_CAPS entries naming unknown ops "
        f"(known: {sorted(_MANAGEMENT_OPS)}): " + "; ".join(bad)
    )


def test_bybit_remains_fully_wired():
    """Backstop: the reference integration must keep every op declared, so a
    regression that drops one is caught here too."""
    assert clients.exchange_management_caps("bybit") == _MANAGEMENT_OPS


# ---------------------------------------------------------------------------
# (b) Strategy side — every monitor() returns schema-valid verdicts
# ---------------------------------------------------------------------------


def _all_registered_strategies():
    names = set(_STRATEGY_BUILDERS)
    names.update(_resolve_builders())
    return sorted(names)


def _candles(last_close: float, *, start: float, n: int = 260) -> pd.DataFrame:
    """A representative OHLCV frame trending linearly from *start* to
    *last_close* over *n* rows — enough history for indicator-based monitors
    (EMA/ATR/Donchian windows) and a controllable final close.
    """
    closes = [start + (last_close - start) * (i / (n - 1)) for i in range(n)]
    rows = []
    for c in closes:
        span = max(abs(c) * 0.002, 0.5)
        rows.append(
            {
                "open": c - span / 2,
                "high": c + span,
                "low": c - span,
                "close": c,
                "volume": 1000.0,
            }
        )
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return df


def _open_pkg(direction: str, *, entry: float, sl: float, tp: float) -> dict:
    """An open order-package dict carrying the fields monitors read (with the
    common synonyms so a strategy that reads stop_loss vs sl still resolves)."""
    return {
        "order_package_id": "pkg-ci-1",
        "strategy_name": "ci",
        "symbol": "BTCUSDT",
        "direction": direction,
        "side": "buy" if direction == "long" else "sell",
        "entry": entry,
        "entry_price": entry,
        "sl": sl,
        "stop_loss": sl,
        "tp": tp,
        "take_profit": tp,
        "qty": 1.0,
        "position_size": 1.0,
    }


# Scenarios are designed to elicit the adjust / close / no-op branches across
# the monitor family. entry=100; the final close drives the verdict.
def _scenarios():
    long_entry, long_sl, long_tp = 100.0, 95.0, 115.0
    short_entry, short_sl, short_tp = 100.0, 105.0, 85.0
    return [
        # long, price ran +4R above entry → break-even/trail adjust or TP close
        ("long_runup", {}, _candles(120.0, start=100.0),
         _open_pkg("long", entry=long_entry, sl=long_sl, tp=long_tp)),
        # long, price crashed below SL → sl_cross close
        ("long_slcross", {}, _candles(90.0, start=100.0),
         _open_pkg("long", entry=long_entry, sl=long_sl, tp=long_tp)),
        # long, price near entry → typically no-op
        ("long_flat", {}, _candles(100.5, start=100.0),
         _open_pkg("long", entry=long_entry, sl=long_sl, tp=long_tp)),
        # short, price dropped +4R below entry → adjust or TP close
        ("short_rundown", {}, _candles(80.0, start=100.0),
         _open_pkg("short", entry=short_entry, sl=short_sl, tp=short_tp)),
        # short, price spiked above SL → sl_cross close
        ("short_slcross", {}, _candles(110.0, start=100.0),
         _open_pkg("short", entry=short_entry, sl=short_sl, tp=short_tp)),
    ]


def test_every_strategy_monitor_returns_schema_valid_verdicts():
    bad = []
    returned_something = []
    for strategy_name in _all_registered_strategies():
        module_name = monitor_unit_for(strategy_name)
        try:
            mod = importlib.import_module(f"src.units.strategies.{module_name}")
        except Exception:  # noqa: BLE001 — resolution is covered by the sibling guard
            continue
        monitor_fn = getattr(mod, "monitor", None)
        if monitor_fn is None or not callable(monitor_fn):
            continue
        for scen_name, cfg, candles, open_pkg in _scenarios():
            try:
                verdict = monitor_fn(cfg, candles, open_pkg)
            except Exception:  # noqa: BLE001
                # A raise on synthetic input is tolerated — the order-monitor
                # catches it and the signature/existence guards cover
                # callability. This guard only validates what monitor() RETURNS.
                continue
            ok, reason = validate_verdict(verdict)
            if not ok:
                bad.append(
                    f"{strategy_name} ({scen_name}): invalid verdict "
                    f"{verdict!r}: {reason}"
                )
            elif verdict is not None:
                returned_something.append(strategy_name)
    assert not bad, (
        "Strategy monitor() returned a verdict that fails the canonical schema "
        "(strategy_verdict.validate_verdict): " + "; ".join(bad)
    )
    # Sanity: across the whole family + scenarios, at least one monitor must
    # actually RETURN a (valid) verdict — otherwise the guard is vacuously
    # green (e.g. every monitor silently raised) and isn't testing the shape.
    assert returned_something, (
        "No strategy monitor() returned a non-None verdict on any scenario — "
        "the representative input no longer exercises the verdict path; update "
        "_scenarios() so this guard keeps validating real verdicts."
    )


def test_validate_verdict_is_importable_and_pure():
    """The validator the guard (and the order-monitor) depend on stays pure +
    non-raising on hostile input."""
    for hostile in (None, {}, {"sl": -1}, {"action": "close"}, 42, "x",
                    {"action": "close", "reason": "ok"}, {"sl": 100.0}):
        ok, reason = validate_verdict(hostile)
        assert isinstance(ok, bool) and isinstance(reason, str)


# ---------------------------------------------------------------------------
# (c) Strategy side — every strategy yields a schema-valid ExitPlan
#     (explicit module-level exit_plan() OR the legacy derivation)
# ---------------------------------------------------------------------------


def test_every_strategy_yields_a_schema_valid_exit_plan():
    """Dynamic-take-profit consistency (P1): every registered strategy must
    either expose a schema-valid ``exit_plan()`` or produce a valid plan via
    ``build_exit_plan_from_legacy`` on a representative package — so the
    materializer (P2+) always has a valid plan to rest, by construction. A
    strategy whose explicit ``exit_plan()`` returns a malformed plan fails CI.
    """
    bad = []
    for strategy_name in _all_registered_strategies():
        module_name = monitor_unit_for(strategy_name)
        try:
            mod = importlib.import_module(f"src.units.strategies.{module_name}")
        except Exception:  # noqa: BLE001 — resolution covered by the sibling guard
            continue
        # Representative package (entry=100, long, distinct TP1/TP2 so the
        # ladder branch of the legacy derivation is exercised).
        pkg = _open_pkg("long", entry=100.0, sl=95.0, tp=110.0)
        pkg["strategy_name"] = strategy_name
        pkg["meta"] = {"tp2": 120.0, "timeframe": "1h"}
        candles = _candles(108.0, start=100.0)

        explicit = getattr(mod, "exit_plan", None)
        plan = None
        if callable(explicit):
            try:
                plan = explicit({}, candles, pkg)
            except Exception:  # noqa: BLE001
                # A raise on synthetic input is tolerated (the build path wraps
                # it); the legacy derivation below is the always-available floor.
                plan = None
            if plan is not None:
                ok, reason = validate_exit_plan(plan)
                if not ok:
                    bad.append(f"{strategy_name} exit_plan(): invalid plan {plan!r}: {reason}")
                    continue
        if plan is None:
            derived = build_exit_plan_from_legacy(pkg, {})
            ok, reason = validate_exit_plan(derived)
            if not ok:
                bad.append(
                    f"{strategy_name} derived plan invalid {derived!r}: {reason}"
                )
    assert not bad, (
        "Strategy ExitPlan contract violated (src/runtime/exit_plan.py): "
        + "; ".join(bad)
    )


def test_validate_exit_plan_is_importable_and_pure():
    """The ExitPlan validator stays pure + non-raising on hostile input."""
    for hostile in (None, {}, 42, "x", {"version": 1},
                    {"version": 1, "rungs": [], "final": {"kind": "fixed", "price": -1},
                     "stop": {"price": 1}}):
        ok, reason = validate_exit_plan(hostile)
        assert isinstance(ok, bool) and isinstance(reason, str)
        assert ok is False  # none of these are valid plans
