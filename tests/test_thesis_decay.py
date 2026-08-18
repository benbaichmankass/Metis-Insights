"""`src/runtime/thesis_decay.py` — four states, never collapsed; default-off.

The module grades whether a leg's OWN entry filters still hold in-trade. Every
prior exit lever asks a question about the trade's PATH; this one asks about
the MARKET, which is why `unmeasured` must never read as `intact`: an exit
manufactured out of a warm-up NaN is a fabricated decision.
"""
import pytest

from src.runtime import thesis_decay as td

SPEC = {"trend_midline": True, "adx_band": {"min": 25}}
CFG = {"entry_thesis": SPEC}


# --- default-off ------------------------------------------------------------

@pytest.mark.parametrize("cfg", [
    None, {}, {"entry_thesis": None}, {"entry_thesis": {}},
    {"entry_thesis": []}, {"entry_thesis": "yes"}, {"other": 1},
])
def test_undeclared_is_not_declared_and_never_exits(cfg):
    v = td.evaluate(strategy_cfg=cfg, side="short", close=1.0, midline=2.0, adx=40)
    assert v.state == td.STATE_NOT_DECLARED
    assert v.should_exit is False
    # An undeclared leg must not even report component readings — nothing was
    # looked at, and a populated component list would imply otherwise.
    assert v.components == []


def test_undeclared_wins_over_a_frankly_decayed_market():
    """The declaration check is ordered FIRST, deliberately."""
    v = td.evaluate(strategy_cfg={}, side="long", close=1.0, midline=99.0, adx=1)
    assert v.state == td.STATE_NOT_DECLARED


# --- the four states --------------------------------------------------------

def test_intact_short_below_midline_in_band():
    v = td.evaluate(strategy_cfg=CFG, side="short", close=1.0, midline=2.0, adx=30)
    assert v.state == td.STATE_INTACT
    assert v.should_exit is False
    assert v.decayed_components == []


def test_decayed_when_short_price_crosses_above_midline():
    v = td.evaluate(strategy_cfg=CFG, side="short", close=3.0, midline=2.0, adx=30)
    assert v.state == td.STATE_DECAYED
    assert v.should_exit is True
    assert v.decayed_components == ["trend_midline"]
    assert v.close_reason == "thesis_decay_trend_midline"


def test_decayed_when_adx_falls_out_of_band():
    v = td.evaluate(strategy_cfg=CFG, side="short", close=1.0, midline=2.0, adx=10)
    assert v.state == td.STATE_DECAYED
    assert v.decayed_components == ["adx_band"]


def test_both_components_decayed_are_both_named():
    v = td.evaluate(strategy_cfg=CFG, side="short", close=3.0, midline=2.0, adx=10)
    assert sorted(v.decayed_components) == ["adx_band", "trend_midline"]
    assert v.close_reason == "thesis_decay_adx_band_trend_midline"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "abc"])
def test_unreadable_reading_is_unmeasured_never_intact(bad):
    v = td.evaluate(strategy_cfg=CFG, side="short", close=bad, midline=2.0, adx=30)
    assert v.state == td.STATE_UNMEASURED
    assert v.should_exit is False
    assert "trend_midline" in v.unmeasured_components


def test_unmeasured_is_distinct_from_not_declared():
    """Two different facts: a config fact and a runtime fact."""
    a = td.evaluate(strategy_cfg=CFG, side="short", close=None, midline=None, adx=None)
    b = td.evaluate(strategy_cfg=None, side="short", close=None, midline=None, adx=None)
    assert a.state == td.STATE_UNMEASURED
    assert b.state == td.STATE_NOT_DECLARED
    assert a.state != b.state


def test_a_partial_read_cannot_be_called_intact():
    """The unread component is exactly the one that might have broken."""
    v = td.evaluate(strategy_cfg=CFG, side="short", close=1.0, midline=2.0, adx=None)
    assert v.state == td.STATE_UNMEASURED
    assert v.unmeasured_components == ["adx_band"]


def test_a_decay_outranks_an_unmeasured_sibling():
    """One component definitively broken is a finding even if another is dark."""
    v = td.evaluate(strategy_cfg=CFG, side="short", close=3.0, midline=2.0, adx=None)
    assert v.state == td.STATE_DECAYED
    assert v.decayed_components == ["trend_midline"]
    assert v.unmeasured_components == ["adx_band"]


# --- component semantics ----------------------------------------------------

@pytest.mark.parametrize("side,close,midline,holds", [
    ("long", 3.0, 2.0, True), ("long", 1.0, 2.0, False),
    ("buy", 3.0, 2.0, True), ("sell", 1.0, 2.0, True),
    ("short", 1.0, 2.0, True), ("short", 3.0, 2.0, False),
])
def test_midline_is_side_aware_across_side_spellings(side, close, midline, holds):
    v = td.evaluate(strategy_cfg={"entry_thesis": {"trend_midline": True}},
                    side=side, close=close, midline=midline)
    assert (v.state == td.STATE_INTACT) is holds


def test_non_directional_side_is_unmeasured_not_decayed():
    v = td.evaluate(strategy_cfg={"entry_thesis": {"trend_midline": True}},
                    side="flat", close=1.0, midline=2.0)
    assert v.state == td.STATE_UNMEASURED


def test_adx_band_with_no_bounds_is_unmeasured_not_a_pass():
    """A component wired but grading nothing must not read as satisfied."""
    v = td.evaluate(strategy_cfg={"entry_thesis": {"adx_band": {}}},
                    side="short", adx=30)
    assert v.state == td.STATE_UNMEASURED


def test_adx_max_bound_is_honoured():
    cfg = {"entry_thesis": {"adx_band": {"max": 40}}}
    assert td.evaluate(strategy_cfg=cfg, side="short", adx=30).state == td.STATE_INTACT
    assert td.evaluate(strategy_cfg=cfg, side="short", adx=50).state == td.STATE_DECAYED


def test_unknown_component_is_unmeasured_never_silently_ignored():
    v = td.evaluate(strategy_cfg={"entry_thesis": {"vibes": True}}, side="short")
    assert v.state == td.STATE_UNMEASURED
    assert v.unmeasured_components == ["vibes"]
    assert v.components[0].reason == "unknown_component_vibes"


def test_explicitly_falsy_component_is_opted_out_not_failed():
    v = td.evaluate(strategy_cfg={"entry_thesis": {"trend_midline": False,
                                                   "adx_band": {"min": 25}}},
                    side="short", adx=30)
    assert v.state == td.STATE_INTACT
    assert [c.component for c in v.components] == ["adx_band"]


def test_all_components_opted_out_is_unmeasured_not_intact():
    v = td.evaluate(strategy_cfg={"entry_thesis": {"trend_midline": False}},
                    side="short", close=1.0, midline=2.0)
    assert v.state == td.STATE_UNMEASURED


# --- provenance -------------------------------------------------------------

def test_verdicts_carry_their_inputs():
    """A verdict that does not carry its input cannot be checked later."""
    v = td.evaluate(strategy_cfg=CFG, side="short", close=3.0, midline=2.0, adx=30)
    mid = [c for c in v.components if c.component == "trend_midline"][0]
    assert mid.readings == {"side": "short", "close": 3.0, "midline": 2.0}
    adx = [c for c in v.components if c.component == "adx_band"][0]
    assert adx.readings["adx"] == 30 and adx.readings["adx_min"] == 25


def test_no_order_path_caller_yet():
    """Observe-only by contract. A lever reading this verdict is Tier-3 and
    needs its own evidence column, which does not exist yet."""
    import subprocess
    out = subprocess.run(
        ["grep", "-rl", "thesis_decay", "src/units", "src/core", "src/main.py"],
        capture_output=True, text=True).stdout.strip()
    assert out == "", f"thesis_decay gained an order-path caller: {out}"
