"""A thin-OOS refusal must say WHICH WINDOW produced the count.

`insufficient_base_reason` exists because the old message named a COUNT over a
window it did not name, and that single sentence covered two opposite
conditions:

  * a **trade-starved leg** -- the strategy really has only a handful of
    closed trades, and the remedy is to wait (or to stop grading it); and
  * a **badly-placed boundary** -- the leg has hundreds of trades and the
    derived split handed it a thin slice, where the remedy is to move the
    split and re-grade today.

Measured 2026-08-14 on the two legs that motivated it
(`BL-20260814-SPLIT-TARGETS-EXACTLY-THE-FLOOR-SO-BOUNDARY-LOSS-ALWAYS-FAILS`):
`htf_pullback_trend_2h` was refused at OOS n=24 under the derived split and
graded at n=95 under the corpus-standard one -- same leg, same config, same day.
Both printed the identical sentence, so telling the two apart cost a fresh
trainer-relay run instead of a read.

So what is pinned here is not the wording. It is that the message CAN
DISCRIMINATE -- that a reader handed only the string can tell the two cases
apart -- plus the two ways a provenance string quietly lies:

  1. **An absent input is omitted, never fabricated.** Under `--split-mode date`
     no emit run happens, so the leg's lifetime is unknown; printing `0` would
     turn "we did not count" into "the leg has no trades".
  2. **A fallback is never silent.** A split that fell back to the fixed date is
     a different derivation from one that was computed, and a refusal that hides
     it invites the reader to blame the leg for the fallback's window.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "m20_fleet_exit_sweep", REPO / "scripts/research/m20_fleet_exit_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m20_fleet_exit_sweep"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


SWEEP = _load()
REASON = SWEEP.insufficient_base_reason


# The real derived-split refusal, verbatim from trainer relay #9211.
DERIVED_META = {
    "split_mode": "oos-trades",
    "split_target_oos": 25,
    "split_lifetime_trades": 407,
    "split": "2026-04-03",
}

# A genuinely trade-starved leg -- mes_trend_long_1d, measured at 5 OOS of a
# 33-trade lifetime. Same verdict, opposite cause.
STARVED_META = {
    "split_mode": "oos-trades",
    "split_target_oos": 25,
    "split_lifetime_trades": 33,
    "split": "2025-07-01",
}


def test_the_count_alone_is_not_the_message():
    """The window is named, so the count is never reported bare."""
    msg = REASON(24, 25, "2026-04-03", DERIVED_META)
    assert "24" in msg and "25" in msg          # the arithmetic survives
    assert "2026-04-03" in msg                  # ...and so does its window
    assert "oos-trades" in msg


def test_a_badly_placed_boundary_is_distinguishable_from_a_starved_leg():
    """THE POINT OF THE FUNCTION.

    Both legs are refused at a thin OOS count. A reader handed only these two
    strings must be able to say which one is worth re-grading today. The
    discriminator is the leg's LIFETIME sitting beside the windowed count.
    """
    boundary = REASON(24, 25, "2026-04-03", DERIVED_META)
    starved = REASON(5, 25, "2025-07-01", STARVED_META)

    assert boundary != starved
    # 407 lifetime trades against a 24-trade window: the leg is not starved.
    assert "407" in boundary
    # 33 lifetime against a 5-trade window: waiting is the only remedy.
    assert "33" in starved

    # And the discrimination is not an accident of the split dates differing --
    # it survives when both legs share a boundary date.
    same_date_a = REASON(24, 25, "2026-04-03", DERIVED_META)
    same_date_b = REASON(24, 25, "2026-04-03",
                         {**STARVED_META, "split": "2026-04-03"})
    assert same_date_a != same_date_b


def test_an_uncounted_lifetime_is_omitted_not_fabricated():
    """`--split-mode date` runs no emit, so the lifetime is UNKNOWN.

    Reporting it as 0 would say the leg has no trades -- the opposite claim, and
    the permissive-looking one (it makes a refusal look deserved).
    """
    msg = REASON(24, 25, "2025-07-01",
                 {"split_mode": "date", "split": "2025-07-01"})
    assert "lifetime" not in msg
    assert "leg lifetime 0" not in msg
    # The target is equally meaningless here -- nothing was targeted.
    assert "targeting" not in msg
    assert "date" in msg                        # the mode is still stated


def test_a_fallback_is_never_silent():
    """A split that FELL BACK was derived differently from one that computed."""
    msg = REASON(8, 25, "2025-07-01",
                 {**STARVED_META, "split_fallback": "leg_too_thin",
                  "split": "2025-07-01"})
    assert "FELL BACK" in msg
    assert "leg_too_thin" in msg


def test_the_target_is_never_presented_as_the_achieved_count():
    """`resolve_split` is explicit that the two differ (candle vs trade windows).

    A message carrying both must not let the target be mistaken for what OOS
    actually held: the achieved count is the leading figure compared to the
    floor, and the target is labelled as a target.
    """
    msg = REASON(24, 25, "2026-04-03", DERIVED_META)
    assert msg.startswith("OOS base 24 trades < floor 25")
    assert "targeting 25" in msg
    # The two 25s here mean different things (floor, target) and both are
    # labelled; the achieved 24 is the only bare number, and it leads.


def test_the_wired_verdict_block_calls_this_function():
    """A pure function nothing calls would be provenance theatre.

    The verdict block lives inside `main()` and is not otherwise reachable, so
    this is the join that keeps the tested string and the emitted string the
    same one.
    """
    src = (REPO / "scripts/research/m20_fleet_exit_sweep.py").read_text()
    assert 'entry["insufficient_base_why"] = insufficient_base_reason(' in src


def test_the_cell_entry_records_the_split_it_was_graded_on():
    """Recorded on EVERY cell, not only refused ones.

    A boundary that decides a PASS deserves the same audit trail as one that
    decides a refusal -- and the assignments sit above the `if _thin:` branch
    precisely so a graded cell carries them too.
    """
    src = (REPO / "scripts/research/m20_fleet_exit_sweep.py").read_text()
    for key in ("split", "split_mode", "split_target_oos",
                "split_lifetime_trades", "split_fallback"):
        assert f'entry["{key}"] = ' in src, key
    thin = src.index("            if _thin:")
    for key in ("split_mode", "split_fallback"):
        assert src.index(f'entry["{key}"] = ') < thin, key
