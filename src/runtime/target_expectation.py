"""Does this trade's take-profit express an EXPECTATION, or a venue limit?

Operator directive, 2026-08-20 and again 2026-08-23:

    "brackets ALWAYS represent our prediction of where the trade should end —
    e.g. if it's momentum driven, the TP is where we expect momentum to run
    out, and the SL represents where we think we can consider ourselves wrong
    and cut our losses. Then the active management adjusts the brackets based
    on the ongoing monitoring."

    "at least know in the beginning where we think that momentum is gonna burn
    out."

`docs/design/exit-mechanism-construction-PROCESS.md` § 2 states the same thing
as a requirement — *"A bracket must carry an expectation at entry, or it is not
a bracket"* — and records that **this is not what the fleet does today**.

WHAT THIS MODULE IS. The reader that makes that claim machine-checkable per
trade, and the decision half of *extending* a target — which
`_base.monitor`'s docstring has declared as `{"tp": float}` since it was
written and **no strategy has ever produced**. The rest of that chain is
already live and needs nothing: `monitor_verdict.interpret_verdict` parses a
`tp` delta independently of `sl` and applies the meaningful-change filter,
`order_monitor._apply_update` routes it, `_send_modify_to_exchange` forwards
it, and `execute.modify_open_order` amends the resting leg on Bybit / IB /
Alpaca. **Declared, plumbed end-to-end, never produced** — the third instance
of this repo's signature failure shape, and the only missing piece is a
producer.

⚠️ **THIS MODULE DECIDES NOTHING ON ITS OWN AND MOVES NO ORDER.** It is pure:
no I/O, no imports beyond stdlib, never raises. A caller that wants to act on
it must do so explicitly, and flipping a leg from observe to live is Tier-3.

FOUR STATES, NEVER COLLAPSED — and the third is the one this exists for
--------------------------------------------------------------------------
`sentinel_no_expectation`
    The config declares `tp_r >= SENTINEL_R_FLOOR`. **There was never a
    prediction.** The placed take-profit is `entry × 1.099` — the exchange's
    rejection threshold wearing the label of a price target.

    ⚠️ **DO NOT QUOTE A COUNT FROM THIS DOCSTRING.** The authority is
    `scripts/research/bracket_expectation_census.py`; run it. A snapshot
    embedded here drifted three times in two days as unrelated PRs demoted a
    leg or changed one `tp_r`
    (BL-20260823-TARGET-EXPECTATION-DOCSTRING-COUNTS-STALE), and a figure that
    moves one commit at a time is how a quoted population becomes wrong without
    anyone deciding it should.

    ⚠️ **AND A `tp_r` SCAN OF THE YAML IS THE WRONG MEASURE ANYWAY** — this is
    the more important half, and it is why the previous snapshot understated
    the very problem this module exists to name. Some legs declare no target
    key at all and INHERIT `tp_r = 50.0` from their strategy class, so they
    behave exactly as sentinels while being invisible to `grep tp_r`. The
    census reports the two separately as `sent_DECL` and `sent_EFF` and never
    sums or collapses them — a leg that writes `tp_r: 50.0` made a choice, a
    leg that writes nothing inherited one; same runtime behaviour, different
    remedy, and conflating them "would accuse legs of a defect they may not
    have" (which is what `STATE_NO_TARGET_KEY` exists to keep apart).

    For orientation only, measured at 814d019b: enabled 52 -> 28 declared /
    38 effective; enabled+live 44 -> 23 declared / 33 effective. The declared
    figure is the one that drifts AND the one that under-reports.
`clamped`
    A real `tp_r` was declared and the venue cap binds, so the level that
    actually rests is the **cap**, not the expectation. ⚠️ This is NOT the same
    as the sentinel and folding them together loses the distinction that
    matters: a clamped leg *had* an expectation the venue refused to place; a
    sentinel leg never had one. And it is not hypothetical for non-sentinel
    legs either — `tp_r: 6.0` against a 14.4%-of-entry stop asks for 86% and
    gets 9.9%.
`declared`
    A target that expresses an expectation AND survives to the venue.
`no_target_key`
    The config declares no target key at all (`ict_scalp`'s siblings that
    compute their target elsewhere, the `*_pullback_1d` family). This module
    cannot grade them and says so — it is **not** the sentinel, and calling it
    one would accuse 20 legs of a defect they may not have.
`unmeasurable`
    Risk or entry could not be read. *We could not look* — never "no
    expectation", and never `no_target_key`: one is a missing INPUT, the other
    a missing DECLARATION, and they have different remedies.

WHY THE CLAMP IS THE TEST, not a `tp_r` threshold alone. A target is a
prediction only if the level that RESTS is the level that was predicted. The
venue cap (`_TP_SENTINEL_CAP_PCT`, ~9.9%, Bybit's ErrCode 10001 boundary)
decides that, and it binds as a function of `risk/entry` — i.e. of volatility
at entry — so the same config is a prediction on a calm bar and a venue limit
on a violent one. Measured 2026-08-23: 3 of 3,665 non-pairs order packages hit
it, and the two open at the time were `xrp_pullback_2h` at R:R **0.687** (real
money) and `ada_pullback_2h` at **0.843** — a target NEARER than the stop,
which no one had checked because `cap_r` is published and read only as a
ceiling for the trail-decay arm.

EXTENSION — conditioned on the strategy's OWN thesis, per § 4
--------------------------------------------------------------
`evaluate_extension` answers *"price is nearing the declared target; may it
move out?"* and requires the caller to supply a `thesis_intact` verdict rather
than inventing one, because § 4 is explicit that a revision rule reading only
the trade's own path is the eleven-endogenous-feature substrate already
identified as the root cause. A caller passing `None` gets `thesis_unknown`,
which **never extends** — an unread thesis is not an intact one.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

__all__ = [
    "SENTINEL_R_FLOOR", "TP_VENUE_CAP_PCT",
    "STATE_DECLARED", "STATE_CLAMPED", "STATE_SENTINEL", "STATE_UNMEASURABLE",
    "STATE_NO_TARGET_KEY", "TARGET_KEYS",
    "EXT_EXTEND", "EXT_NOT_APPROACHING", "EXT_THESIS_BROKEN",
    "EXT_THESIS_UNKNOWN", "EXT_CAP_REACHED", "EXT_NO_EXPECTATION",
    "EXT_UNMEASURABLE",
    "resolve_expectation", "evaluate_extension",
]

# A `tp_r` at or above this is the fleet's "far sentinel" idiom — the config
# saying "there is no real target, the trail is the exit". Mirrors the value
# every such leg actually declares (50.0); it is a RECOGNISER for that idiom,
# not a tuning knob, which is why it is a module constant and not an env var.
SENTINEL_R_FLOOR = 50.0

# The venue take-profit clamp -- ONE owner, imported rather than mirrored.
# This module's dependency-free property is preserved in substance: the owner
# imports only `typing`, and src/__init__.py + src/runtime/__init__.py are
# empty, so this costs no heavy dependency. Re-exported via __all__ below,
# so every existing `from src.runtime.target_expectation import
# TP_VENUE_CAP_PCT` caller keeps working unchanged.
from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT  # noqa: E402,F401

STATE_DECLARED = "declared"
STATE_CLAMPED = "clamped"
STATE_SENTINEL = "sentinel_no_expectation"
STATE_UNMEASURABLE = "unmeasurable"
STATE_NO_TARGET_KEY = "no_target_key"

# Read in order. `target_r` is the EXPLICIT expectation key this milestone
# introduces; `tp_r` and `tp_at_r` are what the fleet declares today (the
# donchian/pullback families and the ict_scalp family respectively), read so a
# leg with a genuine 1.5R target is never mis-graded as having no target.
TARGET_KEYS = ("target_r", "tp_r", "tp_at_r")

EXT_EXTEND = "extend"
EXT_NOT_APPROACHING = "not_approaching"
EXT_THESIS_BROKEN = "thesis_broken_hold"
EXT_THESIS_UNKNOWN = "thesis_unknown"
EXT_CAP_REACHED = "extension_cap_reached"
EXT_NO_EXPECTATION = "no_expectation_declared"
EXT_UNMEASURABLE = "unmeasurable"


def _f(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def resolve_expectation(
    cfg: Optional[Dict[str, Any]],
    *,
    entry: Any,
    sl: Any,
    direction: Any,
    cap_pct: float = TP_VENUE_CAP_PCT,
) -> Dict[str, Any]:
    """Grade one trade's declared take-profit. Pure; never raises.

    Reads `target_r` first (the explicit expectation key) and falls back to
    `tp_r` (what the fleet declares today), so a leg can be given a real
    expectation without touching the legacy key that other code paths read.

    Returns a dict carrying `state` plus, where derivable: `target_r` (what was
    asked for), `expectation_price` (where that lands), `placed_price` (where
    it would actually rest after the venue cap), `cap_price`, `cap_r` (the
    ceiling in R — i.e. the trade's own reward-to-risk if the cap binds), and
    `risk_over_entry`.
    """
    out: Dict[str, Any] = {
        "state": STATE_UNMEASURABLE,
        "target_r": None,
        "expectation_price": None,
        "placed_price": None,
        "cap_price": None,
        "cap_r": None,
        "risk_over_entry": None,
        "source_key": None,
    }
    e, s = _f(entry), _f(sl)
    if e is None or s is None or e <= 0:
        return out
    risk = abs(e - s)
    if risk <= 0:
        return out
    out["risk_over_entry"] = risk / e

    cfg = cfg or {}
    target_r = None
    for key in TARGET_KEYS:
        candidate = _f(cfg.get(key))
        if candidate is not None:
            target_r, out["source_key"] = candidate, key
            break
    if target_r is None or target_r <= 0:
        # No target key at all — the strategy computes its target elsewhere.
        # A missing DECLARATION, distinct from a missing INPUT (unmeasurable)
        # and emphatically not the sentinel.
        out["source_key"] = None
        out["state"] = STATE_NO_TARGET_KEY
        return out
    out["target_r"] = target_r

    is_long = str(direction or "").lower() in ("long", "buy")
    cap_price = e * (1.0 + cap_pct) if is_long else e * (1.0 - cap_pct)
    out["cap_price"] = cap_price
    out["cap_r"] = (cap_pct * e) / risk

    want = e + target_r * risk if is_long else e - target_r * risk
    out["expectation_price"] = want
    out["placed_price"] = min(cap_price, want) if is_long else max(cap_price, want)

    if target_r >= SENTINEL_R_FLOOR:
        out["state"] = STATE_SENTINEL
        return out
    # The clamp binds when the asked-for target is further than the cap.
    binds = want > cap_price if is_long else want < cap_price
    out["state"] = STATE_CLAMPED if binds else STATE_DECLARED
    return out


def evaluate_extension(
    expectation: Optional[Dict[str, Any]],
    *,
    price: Any,
    entry: Any,
    direction: Any,
    thesis_intact: Optional[bool],
    extends_so_far: int = 0,
    approach_frac: float = 0.85,
    extend_r: float = 1.0,
    max_extends: int = 3,
) -> Dict[str, Any]:
    """*Price is nearing the declared target — may it move out?* Pure.

    `thesis_intact` is the STRATEGY's own verdict and must be supplied by the
    caller. `None` means the caller could not read it and yields
    `thesis_unknown`, which never extends — per § 4 a revision rule that reads
    only the trade's own path is the substrate this whole milestone identified
    as the root cause, so "we did not check the thesis" must not become
    "the thesis holds".

    `approach_frac` is how much of the entry→target distance must be travelled
    before extension is even considered; `extend_r` is how far each extension
    pushes the target, in R; `max_extends` bounds the ratchet so a trade cannot
    chase forever.
    """
    out: Dict[str, Any] = {
        "state": EXT_UNMEASURABLE,
        "new_target": None,
        "progress_frac": None,
        "extends_so_far": extends_so_far,
    }
    if not expectation:
        out["state"] = EXT_NO_EXPECTATION
        return out
    if expectation.get("state") == STATE_SENTINEL:
        # There is nothing to extend FROM — a sentinel was never a prediction,
        # and pushing it out would dress a venue limit up as one.
        out["state"] = EXT_NO_EXPECTATION
        return out

    p, e = _f(price), _f(entry)
    target = _f(expectation.get("expectation_price"))
    risk_over_entry = _f(expectation.get("risk_over_entry"))
    if p is None or e is None or target is None or risk_over_entry is None:
        return out
    risk = risk_over_entry * e
    if risk <= 0:
        return out

    span = abs(target - e)
    if span <= 0:
        return out
    out["progress_frac"] = (p - e) / (target - e) if (target - e) != 0 else None

    is_long = str(direction or "").lower() in ("long", "buy")
    travelled = (p - e) if is_long else (e - p)
    if travelled < approach_frac * span:
        out["state"] = EXT_NOT_APPROACHING
        return out

    if extends_so_far >= max_extends:
        out["state"] = EXT_CAP_REACHED
        return out
    if thesis_intact is None:
        out["state"] = EXT_THESIS_UNKNOWN
        return out
    if not thesis_intact:
        out["state"] = EXT_THESIS_BROKEN
        return out

    out["state"] = EXT_EXTEND
    out["new_target"] = (target + extend_r * risk if is_long
                         else target - extend_r * risk)
    out["extends_so_far"] = extends_so_far + 1
    return out
