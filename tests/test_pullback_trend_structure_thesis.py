"""The trend-structure thesis predicate for `htf_pullback_trend_2h`.

Most legs in this family declare no `adx_min` -- 13 of the 19 ENABLED legs the
intent multiplexer routes here, measured 2026-08-23 -- so `_pullback_thesis_intact`
returned `None` -> `thesis_unknown` -> never extends. That was correct
REPORTING (§ 7.5 measured that porting the family's crypto ADX values would
refuse 53-86% of historical entries -- the predicate was wrong for them, not
the value), but it left those legs with no thesis at all.

These tests pin the three things that make the replacement trustworthy:

  * it grades on the TREND, not the pullback -- the inversion that would read
    every winning trade as thesis-broken;
  * the midline it grades against is the ENTRY's own, one definition;
  * it stays OBSERVE-ONLY, so widening it is Tier-1 rather than Tier-3.
"""
from __future__ import annotations

import ast
import pathlib

import pandas as pd
import pytest

from src.units.strategies import htf_pullback_trend_2h as mod

SRC = pathlib.Path(mod.__file__)


def _df(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs if highs is not None else [c + 1 for c in closes]
    lows = lows if lows is not None else [c - 1 for c in closes]
    return pd.DataFrame({
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [1.0] * n,
    })


# --- 1. the trend half, both directions -----------------------------------
def test_long_above_midline_is_intact():
    # rising series: last close sits well above the prior-window midline
    df = _df([float(i) for i in range(1, 41)])
    ok, detail = mod._pullback_thesis_intact(
        {"trend_lookback": 10}, df, direction="long")
    assert ok is True
    assert detail["predicate"] == "trend_structure"
    assert detail["close"] > detail["midline"]


def test_long_below_midline_is_broken():
    # rise then collapse: last close is under the midline of the prior window
    df = _df([float(i) for i in range(1, 41)] + [5.0])
    ok, _ = mod._pullback_thesis_intact(
        {"trend_lookback": 10}, df, direction="long")
    assert ok is False


def test_short_is_the_mirror_not_a_copy():
    """A falling book is INTACT for a short and BROKEN for a long, same bars."""
    df = _df([float(i) for i in range(40, 0, -1)])
    meta = {"trend_lookback": 10}
    assert mod._pullback_thesis_intact(meta, df, direction="short")[0] is True
    assert mod._pullback_thesis_intact(meta, df, direction="long")[0] is False


# --- 2. THE INVERSION THIS EXISTS TO AVOID --------------------------------
def test_a_winning_long_is_not_graded_broken_by_the_pullback_condition():
    """The entry needed a PULLBACK; a working trade has left that zone.

    Grading the pullback would call a winner thesis-broken. This asserts the
    opposite: a strong uptrend whose close is at the TOP of the recent range
    (i.e. maximally far from the entry's lower-third pullback zone) is INTACT.
    """
    df = _df([float(i) for i in range(1, 41)])
    close = float(df["close"].iloc[-1])
    lo = float(df["low"].rolling(10).min().shift(1).iloc[-1])
    hi = float(df["high"].rolling(10).max().shift(1).iloc[-1])
    # positive control: this bar really is in the TOP of the range, so a
    # pullback-based predicate would have to grade it broken.
    assert close > lo + 0.66 * (hi - lo)
    assert mod._pullback_thesis_intact(
        {"trend_lookback": 10}, df, direction="long")[0] is True


# --- 3. states are never collapsed ----------------------------------------
@pytest.mark.parametrize("kwargs,meta,reason", [
    ({"direction": None}, {"trend_lookback": 10}, "direction_unreadable"),
    ({"direction": "sideways"}, {"trend_lookback": 10}, "direction_unreadable"),
    ({"direction": "long"}, {}, "no_trend_lookback_declared"),
])
def test_unreadable_inputs_are_unknown_not_false(kwargs, meta, reason):
    """`None` (we could not look) must never be reported as `False` (broken)."""
    df = _df([float(i) for i in range(1, 41)])
    ok, detail = mod._pullback_thesis_intact(meta, df, **kwargs)
    assert ok is None, "an unreadable input must be unknown, never 'broken'"
    assert detail["reason"] == reason


def test_insufficient_bars_is_unknown():
    df = _df([1.0, 2.0, 3.0])
    ok, detail = mod._pullback_thesis_intact(
        {"trend_lookback": 50}, df, direction="long")
    assert ok is None
    assert detail["reason"] == "insufficient_bars"


# --- 4. the ADX branch is unchanged and still wins ------------------------
def test_declared_adx_still_takes_priority():
    """The 6 legs that DO declare adx_min must be byte-identical in behaviour."""
    df = _df([float(i) for i in range(1, 61)])
    ok, detail = mod._pullback_thesis_intact(
        {"adx_min": 25, "adx_period": 14, "trend_lookback": 10},
        df, direction="long")
    assert detail["predicate"] == "adx_floor", (
        "a leg declaring adx_min must still be graded on ADX, not the fallback")
    assert ok in (True, False)


def test_fallback_records_why_adx_did_not_apply():
    """A trend_structure verdict must say why it is not an adx_floor one."""
    df = _df([float(i) for i in range(1, 41)])
    _, detail = mod._pullback_thesis_intact(
        {"trend_lookback": 10}, df, direction="long")
    assert detail["adx_fallback_reason"] == "no_adx_min_declared"


# --- 5. one midline, and it stays observe-only ---------------------------
def test_thesis_grades_against_the_entrys_own_midline():
    """Not a re-derivation: the same helper the entry condition calls."""
    df = _df([float(i) for i in range(1, 41)])
    expected = float(mod._trend_midline(df, 10).iloc[-1])
    _, detail = mod._pullback_thesis_intact(
        {"trend_lookback": 10}, df, direction="long")
    assert detail["midline"] == expected


def test_entry_and_thesis_share_one_midline_definition():
    """Source-level, name-agnostic: exactly ONE place computes the midline.

    Asserted two ways rather than by function name (the entry point here is
    `order_package`, and a rename must not silently disarm this):
      * the midline arithmetic appears exactly once in the file;
      * `_trend_midline` is called from at least two distinct functions.
    """
    src = SRC.read_text()
    assert src.count("(dc_hi + dc_lo) / 2.0") == 1, (
        "the midline arithmetic must exist in exactly one place, or the entry "
        "and the thesis can drift apart")

    tree = ast.parse(src)
    callers = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if fn.name == "_trend_midline":
            continue
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_trend_midline"):
                callers.add(fn.name)
    assert "_pullback_thesis_intact" in callers, "the thesis must use the helper"
    assert len(callers) >= 2, (
        f"only {sorted(callers)} call _trend_midline — the ENTRY must use it too, "
        f"otherwise the thesis grades against a midline the entry never used")


def test_predicate_stays_observe_only():
    """Its ONLY consumer may be the annotate soak.

    If this predicate ever feeds a close/verdict path it changes what a live
    trade DOES, and widening it stops being Tier-1. Parsed, not grepped.
    """
    tree = ast.parse(SRC.read_text())
    uses = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_pullback_thesis_intact"]
    assert len(uses) == 1, f"expected exactly one call site, found {len(uses)}"
    src = SRC.read_text()
    # the one call site must sit in the annotate block
    idx = src.index("_thesis_ok, _thesis_detail = _pullback_thesis_intact")
    window = src[idx:idx + 700]
    assert "annotate_from_monitor" in window, (
        "the thesis predicate's result must flow to the observe-only soak; "
        "any other consumer makes this an order-path change (Tier-3)")


# --- 6. both branches are live, measured from config ----------------------
def test_both_predicates_are_reachable_on_the_real_roster():
    """Neither branch may be dead code — measured, not asserted from memory.

    Deliberately NOT a pinned count. The 6/13 split measured 2026-08-23 moves
    every time a leg is added, and a test that pins it would fail on a roster
    change that is not a defect. What must hold is the INVARIANT: both branches
    are exercised by real legs, so neither is dead. It reports the split it
    measured so a reader sees the population rather than trusting the prose.
    """
    yaml = pytest.importorskip("yaml")
    cfg_path = pathlib.Path(__file__).resolve().parents[1] / "config" / "strategies.yaml"
    if not cfg_path.exists():          # a checkout without config is not a defect
        pytest.skip("config/strategies.yaml absent")
    strategies = (yaml.safe_load(cfg_path.read_text()) or {}).get("strategies") or {}

    from src.runtime import intent_multiplexer as im
    src = pathlib.Path(im.__file__).read_text()
    family = sorted(
        name for name in strategies
        if (name == "htf_pullback_trend_2h" or "_pullback_" in name)
        and f'"{name}": {name}_signal_builder' in src        # actually routed
        and strategies[name].get("enabled") is True
    )
    assert family, "no enabled pullback leg found — the probe cannot find a positive"

    with_floor = [n for n in family if strategies[n].get("adx_min") is not None]
    without = [n for n in family if strategies[n].get("adx_min") is None]
    split = f"{len(with_floor)} with / {len(without)} without, of {len(family)} enabled"

    assert with_floor, (
        f"no enabled leg declares adx_min ({split}) — the adx_floor branch is "
        f"dead code and should be removed rather than left as decoration")
    assert without, (
        f"every enabled leg declares adx_min ({split}) — the trend_structure "
        f"fallback is dead code; delete it rather than leave it unexercised")
