"""The exit-head replay's emitted `net_r` must be net-of-FULL-cost.

WHY THIS EXISTS
---------------
`BL-20260809-INPROCESS-HARNESS-RUNS-FEE-ONLY-SILENTLY`.

`scripts/backtest_trend.py` keeps its cost terms in module globals defaulting to
``slippage=0.0 / funding=0.0``, and only ``main()`` resolves the venue-aware
values. That default is **deliberate and load-bearing** (PR #8468 keeps the
confidence sweep, the ML recorder and the M30 panel bridge byte-identical) and is
NOT what these tests police.

What they police is the consequence for a module that imports the harness and
calls ``run_backtest`` directly: `exit_head_replay` wrote a field named ``net_r``
under a comment promising *fee + slippage + funding* while actually emitting
fee-only. Every CLI harness fills that same JSONL schema with net-of-full-cost,
and `scripts/ml/record_harness_trades.py` labels ``won = net_r > 0`` — so a leg
on a cheaper basis flips the label on any trade whose true net is marginally
negative.

Measured at the time of the fix: the exit-head emit was NOT wired into
`build_calibration_corpus.py` (a fixed list of six CLI harnesses), so this was
**latent, not active**. These tests exist so it stays that way rather than
becoming active the first time someone points the recorder at the file — the
schema is shared and `portfolio_combine --trades` accepts a *directory*.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.runtime import execution_costs  # noqa: E402


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# resolve_cost_policy — the ONE definition of "unset ⇒ venue, explicit wins"
# --------------------------------------------------------------------------

def test_unset_resolves_to_the_venue_policy():
    slip, fund = execution_costs.resolve_cost_policy("BTCUSDT")
    assert slip == execution_costs.slippage_bps_roundtrip_for("BTCUSDT")
    assert fund == execution_costs.funding_bps_per_window_for("BTCUSDT")
    # Non-vacuity: a perp must actually cost something, or this test would pass
    # against a resolver that returned zeros.
    assert slip > 0.0 and fund > 0.0


def test_an_explicit_zero_is_a_CHOICE_and_survives():
    """`0.0` is the fee-only comparison arm, not 'unset'.

    Collapsing the two is the defect the whole change is about: if an explicit
    zero resolved to the venue default, the fee-only arm would silently stop
    being fee-only.
    """
    slip, fund = execution_costs.resolve_cost_policy(
        "BTCUSDT", slippage_bps_roundtrip=0.0, funding_bps_per_window=0.0)
    assert slip == 0.0 and fund == 0.0


def test_an_explicit_nonzero_wins_over_the_venue_default():
    slip, fund = execution_costs.resolve_cost_policy(
        "BTCUSDT", slippage_bps_roundtrip=99.0, funding_bps_per_window=88.0)
    assert (slip, fund) == (99.0, 88.0)


def test_funding_is_perp_gated_but_slippage_is_not():
    """A non-perp pays slippage and never pays perp funding."""
    _, fund_perp = execution_costs.resolve_cost_policy("BTCUSDT")
    slip_fut, fund_fut = execution_costs.resolve_cost_policy("MES")
    assert fund_perp > 0.0, "a crypto perp must pay funding"
    assert fund_fut == 0.0, "a future must never pay perp funding"
    assert slip_fut > 0.0, "every venue pays slippage"


# --------------------------------------------------------------------------
# the replay applies it — the actual regression
# --------------------------------------------------------------------------

def test_replay_puts_the_harness_on_the_venue_basis():
    """THE REGRESSION TEST. Fails against the pre-fix code, which left the
    harness globals at their 0.0 defaults and emitted fee-only `net_r`."""
    pytest.importorskip("pandas")
    replay = _load("scripts/ml/exit_head_replay.py", "_ehr_costbasis")
    harness = replay._load_harness()

    # Pre-condition: a freshly imported harness IS fee-only. If this ever stops
    # holding, the deliberate #8468 default has been changed and THAT is the
    # thing to look at — this assertion is the tripwire for it.
    assert harness.SLIPPAGE_BPS_ROUNDTRIP == 0.0
    assert harness.FUNDING_BPS_PER_WINDOW == 0.0

    basis = replay._apply_venue_cost_policy(harness, "BTCUSDT")

    assert harness.SLIPPAGE_BPS_ROUNDTRIP == pytest.approx(
        execution_costs.slippage_bps_roundtrip_for("BTCUSDT"))
    assert harness.FUNDING_BPS_PER_WINDOW == pytest.approx(
        execution_costs.funding_bps_per_window_for("BTCUSDT"))
    # Non-vacuity: the change must be a real one, not 0 -> 0.
    assert harness.SLIPPAGE_BPS_ROUNDTRIP > 0.0
    assert basis["slippage_bps_roundtrip"] == harness.SLIPPAGE_BPS_ROUNDTRIP
    assert basis["funding_bps_per_window"] == harness.FUNDING_BPS_PER_WINDOW


def test_the_basis_is_REPORTED_not_just_applied():
    """A cost basis that isn't stated is one the next reader assumes."""
    pytest.importorskip("pandas")
    replay = _load("scripts/ml/exit_head_replay.py", "_ehr_costbasis_report")
    harness = replay._load_harness()
    basis = replay._apply_venue_cost_policy(harness, "BTCUSDT")
    for key in ("fee_bps_roundtrip", "slippage_bps_roundtrip",
                "funding_bps_per_window", "funding_window_hours"):
        assert key in basis, f"{key} must be reported alongside the numbers"
        assert basis[key] is not None


def test_a_non_perp_symbol_resolves_funding_to_zero_through_the_replay():
    """The venue policy is per-SYMBOL, not a flat uplift — an MES replay must not
    be charged perp funding just because a BTC replay is."""
    pytest.importorskip("pandas")
    replay = _load("scripts/ml/exit_head_replay.py", "_ehr_costbasis_mes")
    harness = replay._load_harness()
    basis = replay._apply_venue_cost_policy(harness, "MES")
    assert basis["funding_bps_per_window"] == 0.0
    assert basis["slippage_bps_roundtrip"] > 0.0


def test_applying_the_policy_does_not_move_the_TRADES():
    """Costs are applied post-hoc; the entry/exit loop reads no cost term.

    This is the safety claim the fix rests on — that switching basis changes
    `net_r` and nothing about which trades exist or where they exit. Asserted
    rather than argued, because if it were false the fix would silently alter
    every exit-head replay's population.
    """
    pd = pytest.importorskip("pandas")
    replay = _load("scripts/ml/exit_head_replay.py", "_ehr_costbasis_trades")
    harness = replay._load_harness()

    rows, price = [], 100.0
    for i in range(400):
        price += 0.35 if (i % 120) < 90 else -0.45
        rows.append({
            "timestamp": pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=i),
            "open": price, "high": price + 0.4,
            "low": price - 0.4, "close": price + 0.1, "volume": 100.0})
    df = pd.DataFrame(rows)

    def run(sym_basis):
        harness.SLIPPAGE_BPS_ROUNDTRIP = 0.0
        harness.FUNDING_BPS_PER_WINDOW = 0.0
        if sym_basis:
            replay._apply_venue_cost_policy(harness, "BTCUSDT")
        out = []
        harness.run_backtest(
            df.copy(), donchian=20, atr_period=14, atr_stop_mult=2.5,
            trail_mult=3.0, timeout_bars=200, cooldown_bars=1,
            timeframe="1h", symbol="BTCUSDT", trades_out=out)
        return [(str(t.entry_time), t.exit_index, t.outcome, t.r_multiple)
                for t in out]

    fee_only, venue = run(False), run(True)
    assert fee_only, "fixture produced no trades — the test would be vacuous"
    assert fee_only == venue, "changing cost basis must not move the trades"
