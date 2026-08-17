"""M31 P5 candidate — the `rr_from_here` FLOOR lever in the trend harness.

`PB-20260817-RR-FROM-HERE-LEVER-ABSENT-FROM-HARNESS`: the candidate P5 lever
could not be backtested at all. `scripts/backtest_trend.py` implemented
`stale_exit_bars`, `giveback_min_mfe_r`/`giveback_r` and `trail_decay_*` — a
probe finds all three as a POSITIVE CONTROL — and nothing for `rr_from_here`,
`r_to_target` or `r_to_stop`. So P5 precondition 3 was implement-then-measure,
not measure.

WHAT THESE TESTS PIN, and why each one earns its place
------------------------------------------------------
1. **One definition.** The harness IMPORTS `r_distances` from the live
   `src/runtime/position_telemetry.py`. A second derivation is the exact defect
   M31 exists to close (*the harness measured a book production does not run*)
   and would be invisible, since both copies would look right in isolation.
   `test_harness_and_live_share_one_definition` fails if they ever diverge.
2. **Measurable vs inert are not the same state.** With no capped TP there is
   no `r_to_target`, so the lever CANNOT fire and the run returns exactly-zero
   deltas — byte-identical to a lever that was measured and does nothing. That
   is `BL-20260817-A-SHIPPED-LEVER-RE-SWEPT-AGAINST-ITSELF-READS-AS-A-MEASURED-NO-OP`
   one class over. `rr_floor_state` keeps them apart and the CLI refuses the
   combination outright.
3. **The lever is not dead.** A floor above the observed `rr_min` distribution
   must actually fire. A test suite that only ever asserts "no change" cannot
   tell a correct no-op from a lever wired to nothing.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

pd = pytest.importorskip("pandas")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.runtime.position_telemetry import r_distances  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = _load("_bt_trend_rr_floor", "scripts/backtest_trend.py")

BASE = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
            timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT")

#: Production's Bybit TP-distance clamp; the only setting under which the lever
#: is measurable at all.
LIVE_TP_CAP = 0.099


def _candles(rule="5min"):
    df = pd.read_csv(os.path.join(_REPO, "data/backtest_candles.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


def _run(df, **kw):
    return bt.run_backtest(df.copy(), **{**BASE, **kw})


# --- 1. ONE DEFINITION ---------------------------------------------------- #

def test_harness_and_live_share_one_definition():
    """The harness must not own a second copy of the rr arithmetic.

    Object IDENTITY, not equality of results: two independent implementations
    agreeing on the cases a test happens to pick is exactly how a drifted
    definition survives review. This fails the moment the harness stops using
    the live module's own function.
    """
    assert bt.r_distances is r_distances
    # And the live writer must route through it too — extracting the function
    # while leaving `build_record` on its own inlined copy would satisfy the
    # assertion above and still leave two definitions.
    import inspect

    from src.runtime import position_telemetry

    assert "r_distances(" in inspect.getsource(position_telemetry.build_record)


def test_live_definition_guards_the_crossed_level_case():
    """`rr_from_here` is None — never 0.0 — once a level is already crossed.

    A negative leg makes the ratio a sign artefact rather than a decision input,
    and a fabricated 0.0 would fire every floor on exactly the trades where the
    quantity is meaningless.
    """
    # Stop already crossed (price below the stop on a long).
    _, _, rr = r_distances(price=90.0, stop=95.0, target=110.0, risk=5.0,
                           is_long=True)
    assert rr is None
    # Target already crossed.
    _, _, rr = r_distances(price=115.0, stop=95.0, target=110.0, risk=5.0,
                           is_long=True)
    assert rr is None
    # Both the correct side of price -> a real ratio.
    r_stop, r_tgt, rr = r_distances(price=100.0, stop=95.0, target=110.0,
                                    risk=5.0, is_long=True)
    assert (r_stop, r_tgt) == (1.0, 2.0)
    assert rr == 2.0


def test_the_motivating_live_trade_reproduces():
    """The XRP trade M31 was created to answer: rr_from_here = 0.71.

    `docs/design/m31-p5-telemetry-reading-lever-PROPOSAL.md` § 3: 1.04 / 1.46.
    A regression that silently inverted the ratio would still return a float.
    """
    _, _, rr = r_distances(price=100.0, stop=100.0 - 1.46, target=100.0 + 1.04,
                           risk=1.0, is_long=True)
    assert round(rr, 2) == 0.71


def test_short_side_is_mirrored_not_sign_flipped():
    """A short's stop sits ABOVE price and its target BELOW.

    The cheapest wrong implementation negates one leg and still returns a
    plausible positive ratio on longs, so the short case is the one that catches it.
    """
    r_stop, r_tgt, rr = r_distances(price=100.0, stop=105.0, target=90.0,
                                    risk=5.0, is_long=False)
    assert (r_stop, r_tgt) == (1.0, 2.0)
    assert rr == 2.0


# --- 2. MEASURABLE vs INERT ARE DIFFERENT STATES -------------------------- #

def test_state_is_off_when_no_floor_requested():
    assert _run(_candles(), tp_cap_pct=LIVE_TP_CAP)["rr_floor_state"] == "off"


def test_state_is_unmeasurable_without_a_capped_tp():
    """The state that stops an inert run reading as a measured no-op.

    Without a TP cap there is no `r_to_target`, so the lever cannot fire however
    the floor is set — and the summary must SAY so rather than report a clean
    zero delta against this lever's name.
    """
    s = _run(_candles(), rr_floor=1.0)          # note: no tp_cap_pct
    assert s["rr_floor_state"] == "unmeasurable_no_tp_cap"
    assert s["rr_floor_exits"] == 0
    assert s["rr_min_n"] == 0


def test_state_is_measurable_with_a_capped_tp():
    s = _run(_candles(), tp_cap_pct=LIVE_TP_CAP, rr_floor=1.0)
    assert s["rr_floor_state"] == "measurable"


def test_cli_refuses_the_unmeasurable_combination():
    """Refusing costs one command line; a silent inert row costs a wrong verdict."""
    rc = bt.main(["backtest_trend.py",
                  "--data", os.path.join(_REPO, "data/backtest_candles.csv"),
                  "--rr-floor", "1.0"])          # no --tp-cap-pct
    assert rc == 2


def test_cli_accepts_the_measurable_combination():
    """The positive control for the refusal above — it must not refuse everything."""
    rc = bt.main(["backtest_trend.py",
                  "--data", os.path.join(_REPO, "data/backtest_candles.csv"),
                  "--tp-cap-pct", str(LIVE_TP_CAP), "--rr-floor", "1.0"])
    assert rc == 0


def test_rr_min_is_none_not_zero_when_unmeasured():
    """"We did not look" and "the ratio reached zero" are opposite statements.

    A fabricated 0.0 here would report every floor as trivially reachable.
    """
    s = _run(_candles())                        # no TP cap -> nothing measurable
    assert s["rr_min_n"] == 0
    for k in ("rr_min_p10", "rr_min_median", "rr_min_p90"):
        assert s[k] is None


# --- 3. THE LEVER IS NOT DEAD --------------------------------------------- #

def test_a_floor_above_the_observed_distribution_fires():
    """POSITIVE CONTROL. Without this, every 'no change' below proves nothing.

    The floor is derived from the run's OWN measured `rr_min_p90` rather than
    hardcoded, so the test cannot rot into asserting a stale constant.
    """
    df = _candles()
    base = _run(df, tp_cap_pct=LIVE_TP_CAP)
    assert base["rr_min_n"] > 0, "fixture must measure rr or this proves nothing"
    high = float(base["rr_min_p90"]) * 2.0
    fired = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=high)
    assert fired["rr_floor_exits"] > 0
    assert fired["by_outcome"].get("rr_floor_exit", 0) == fired["rr_floor_exits"]


def test_more_fires_at_a_higher_floor():
    """Monotonicity — a higher floor exits at least as often.

    A lever that fires but ignores its own threshold would pass the control above.
    """
    df = _candles()
    base = _run(df, tp_cap_pct=LIVE_TP_CAP)
    p90 = float(base["rr_min_p90"])
    low = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=p90 * 0.5)["rr_floor_exits"]
    high = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=p90 * 2.0)["rr_floor_exits"]
    assert high > low


def test_a_floor_below_the_distribution_changes_nothing():
    """The honest negative: a floor no trade ever reaches is a real no-op.

    Distinguishable from the inert case ONLY by `rr_floor_state`, which is why
    that field exists.
    """
    df = _candles()
    base = _run(df, tp_cap_pct=LIVE_TP_CAP)
    tiny = float(base["rr_min_p10"]) * 0.01
    s = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=tiny)
    assert s["rr_floor_exits"] == 0
    assert s["rr_floor_state"] == "measurable"   # NOT "unmeasurable_no_tp_cap"
    assert s["total_trades"] == base["total_trades"]


def test_params_echo_only_when_declared():
    df = _candles()
    assert "rr_floor" not in _run(df, tp_cap_pct=LIVE_TP_CAP)["params"]
    assert _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=2.0)["params"]["rr_floor"] == 2.0


# --- precedence: the lever is LAST, so it cannot re-grade an existing one -- #

def test_stale_stop_takes_precedence_over_rr_floor():
    """Chain is stop -> tp -> giveback -> stale -> rr_floor.

    Composing the new lever with an already-declared one must not change which
    lever gets the credit for an exit, or every recorded verdict for that lever
    would silently move.
    """
    df = _candles()
    p90 = float(_run(df, tp_cap_pct=LIVE_TP_CAP)["rr_min_p90"])
    s = _run(df, tp_cap_pct=LIVE_TP_CAP, rr_floor=p90 * 2.0,
             stale_exit_bars=1, stale_exit_below_r=99.0)   # stale always fires
    assert s["by_outcome"].get("rr_floor_exit", 0) == 0
    assert s["by_outcome"].get("stale_stop", 0) > 0
