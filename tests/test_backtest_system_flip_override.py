"""Flip-confidence-override arm in scripts/backtest_system.py (BL-20260811).

Hermetic: synthetic candles + a monkeypatched two-strategy signal stream — no
network, no registry. Covers the arm that mirrors the LIVE knobs
``FLIP_CONFIDENCE_THRESHOLD`` / ``FLIP_MIN_POSITION_AGE_HOURS``
(``src/runtime/intents.py::_evaluate_confidence_override``).

Why these specific tests exist — each pins a defect that was either FOUND in
this change or would render as a plausible-looking result rather than a crash:

* ``test_override_reopens_it_does_not_stand_aside`` — the override under
  ``hold`` must behave as ``reverse`` (close AND reopen). The first draft of
  the harness change closed without reopening, i.e. silently measured ``flat``
  while labelled as the override arm. PnL would still have printed.
* ``test_inherited_env_cannot_arm_the_baseline_arm`` — the live VM exports
  ``FLIP_CONFIDENCE_THRESHOLD=0.15``. If the harness merely *read* the
  environment, a baseline run on that box would BE the override arm and the
  A/B would report the two as identical.
* ``test_conflict_ledger_is_the_denominator`` — a zero-fire run and a
  fired-but-neutral run have opposite meanings and identical headline PnL.
"""
from __future__ import annotations

import importlib
import os

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

bs = importlib.import_module("scripts.backtest_system")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _flat_base5m(n: int = 1600, start_px: float = 20_000.0) -> pd.DataFrame:
    """A gently-drifting 5m series. Deliberately low-slope so neither injected
    signal's SL/TP fires before the conflict bar — the test is about the
    conflict decision, not about exit geometry."""
    ts = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    px = start_px + np.arange(n, dtype=float) * 0.05
    return pd.DataFrame({
        "timestamp": ts,
        "open": px,
        "high": px + 2.0,
        "low": px - 2.0,
        "close": px + 1.0,
        "volume": np.full(n, 10.0),
    })


_STREAM_COLS = ["ts", "side", "entry", "sl", "tp", "confidence", "meta_json"]


def _row(clock, bar, side, conf, stop_frac=0.02):
    c = float(clock["close"].iloc[bar])
    return {
        "ts": clock["timestamp"].iloc[bar],
        "side": side,
        "entry": c,
        # Wide stops so the position survives to the conflict bar.
        "sl": c * (1 - stop_frac) if side == "long" else c * (1 + stop_frac),
        "tp": c * (1 + 20 * stop_frac) if side == "long" else c * (1 - 20 * stop_frac),
        "confidence": conf,
        "meta_json": "{}",
    }


def _inject_conflict(monkeypatch, *, holder="trend_donchian", holder_bar=3,
                     holder_conf=0.50, challenger="ict_scalp_5m",
                     challenger_bar=80, challenger_conf=0.80):
    """`holder` opens LONG early; `challenger` votes SHORT later — the
    opposite-direction conflict the flip policy resolves.

    Default clocks are 1h (trend_donchian) vs 5m (ict_scalp_5m) => tf_ratio 12
    => M26 `cross_clock`. ``challenger_bar`` is far enough out (80 × 15m = 20h)
    that the 4h live age gate is satisfiable.
    """
    base = _flat_base5m()
    clock = bs._date_filter(bs._resample(base, bs._PANDAS_TF["15m"]), None, None)
    clock = clock.reset_index(drop=True)

    held = pd.DataFrame([_row(clock, holder_bar, "long", holder_conf)],
                        columns=_STREAM_COLS)
    chal = pd.DataFrame([_row(clock, challenger_bar, "short", challenger_conf)],
                        columns=_STREAM_COLS)

    def _fake_stream(name, base5m, *, start, end, overrides, refresh=False,
                     symbol="BTCUSDT"):
        if name == holder:
            return held
        if name == challenger:
            return chal
        return pd.DataFrame(columns=_STREAM_COLS)

    monkeypatch.setattr(bs, "generate_signal_stream", _fake_stream)
    return base, [holder, challenger]


def _run(base, roster, **kw):
    kw.setdefault("flip_policy", "hold")
    return bs.run_system_backtest(
        base, roster=roster, start=None, end=None,
        initial_balance=10_000.0, risk_pct=0.3, daily_loss_pct=3.0,
        signal_ttl_bars=2, overrides={}, refresh=True, clock_tf="15m",
        **kw)


def _ov(out):
    return out["evidence"]["flip_override"]


# ---------------------------------------------------------------------------
# The conflict actually happens — every other test depends on this
# ---------------------------------------------------------------------------
def test_fixture_produces_a_real_conflict(monkeypatch):
    """A negative result is only meaningful against a positive control. If the
    fixture never creates an opposite-direction conflict, every 'override did
    not fire' assertion below would pass vacuously."""
    base, roster = _inject_conflict(monkeypatch)
    out = _run(base, roster)
    assert _ov(out)["conflicts_observed"] >= 1, (
        "fixture produced no opposite-direction conflict — the rest of this "
        "module would be asserting against an empty population")


# ---------------------------------------------------------------------------
# Default = disabled
# ---------------------------------------------------------------------------
def test_default_is_disabled_and_holds(monkeypatch):
    base, roster = _inject_conflict(monkeypatch)
    out = _run(base, roster)  # no threshold => arm inert

    ov = _ov(out)
    assert ov["arm"]["flip_confidence_threshold"] == 0.0
    assert ov["overrides_fired"] == 0
    assert out["by_exit_reason"].get("flip_confgap", 0) == 0


def test_disabled_arm_matches_plain_hold_exactly(monkeypatch):
    """Threshold 0 must be byte-identical to the pre-change `hold` branch."""
    base, roster = _inject_conflict(monkeypatch)
    a = _run(base, roster)
    _inject_conflict(monkeypatch)
    b = _run(base, roster, flip_confidence_threshold=0.0,
             flip_min_position_age_hours=0.0)
    assert a["net_pnl"] == b["net_pnl"]
    assert a["total_trades"] == b["total_trades"]


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------
def test_override_fires_when_both_gates_pass(monkeypatch):
    base, roster = _inject_conflict(monkeypatch,
                                    holder_conf=0.50, challenger_conf=0.80)
    out = _run(base, roster, flip_confidence_threshold=0.15,
               flip_min_position_age_hours=0.0)

    ov = _ov(out)
    assert ov["overrides_fired"] >= 1, (
        f"expected a fire on a 0.30 gap vs a 0.15 threshold; ledger={ov}")
    assert out["by_exit_reason"].get("flip_confgap", 0) >= 1


def test_gap_below_threshold_does_not_fire(monkeypatch):
    # gap = 0.05, threshold = 0.15
    base, roster = _inject_conflict(monkeypatch,
                                    holder_conf=0.70, challenger_conf=0.75)
    out = _run(base, roster, flip_confidence_threshold=0.15,
               flip_min_position_age_hours=0.0)

    ov = _ov(out)
    assert ov["conflicts_observed"] >= 1      # positive control
    assert ov["overrides_fired"] == 0
    assert ov["passed_confidence_gap"] == 0


def test_age_gate_blocks_an_otherwise_qualifying_flip(monkeypatch):
    """BOTH gates must hold — a large gap on a young position must not flip."""
    base, roster = _inject_conflict(monkeypatch,
                                    holder_conf=0.50, challenger_conf=0.80)
    out = _run(base, roster, flip_confidence_threshold=0.15,
               flip_min_position_age_hours=10_000.0)  # unreachable age

    ov = _ov(out)
    assert ov["conflicts_observed"] >= 1
    assert ov["passed_confidence_gap"] >= 1, "gap gate should have passed"
    assert ov["passed_min_age"] == 0
    assert ov["overrides_fired"] == 0, "age gate must veto despite the gap"


# ---------------------------------------------------------------------------
# The two defects this change actually contained
# ---------------------------------------------------------------------------
def test_override_reopens_it_does_not_stand_aside(monkeypatch):
    """Live falls through to `action="flip"` (close + open). A close-without-
    reopen is `flat`, a DIFFERENT arm that would still print a plausible PnL.

    Verified to catch the planted defect: reverting the `or _override_reason
    is not None` clause in the reopen condition fails this test.
    """
    base, roster = _inject_conflict(monkeypatch,
                                    holder_conf=0.50, challenger_conf=0.80)
    out = _run(base, roster, flip_confidence_threshold=0.15,
               flip_min_position_age_hours=0.0, attach_full=True)

    assert _ov(out)["overrides_fired"] >= 1
    trades = out["closed_trades"]   # _ClosedTrade dataclasses
    confgap = [t for t in trades if t.reason == "flip_confgap"]
    assert confgap, "no confgap close recorded"

    # The discriminator is ATOMICITY, not merely "a reopen happened".
    #
    # This test initially asserted only that some opposite-side trade opened
    # at-or-after the close, and it did NOT catch the planted defect: with the
    # reopen clause removed, the challenger's signal is still within its TTL on
    # the FOLLOWING bar, so the normal open branch fires anyway and `flat`
    # self-heals into something that looks like a flip. The two arms differ in
    # WHEN: `reverse` reopens on the conflict bar itself (`fill = c[i]`),
    # `flat` cannot reopen before the next bar.
    closed_at = min(t.exit_ts for t in confgap)
    closed_side = confgap[0].side
    same_bar = [t for t in trades
                if str(t.entry_ts) == str(closed_at) and t.side != closed_side]
    assert same_bar, (
        "override closed the position but did not reopen on the SAME bar — "
        "that is the `flat` arm (or a next-bar TTL re-entry), not the atomic "
        "close+open the live override performs")


def test_inherited_env_cannot_arm_the_baseline_arm(monkeypatch):
    """The live VM exports FLIP_CONFIDENCE_THRESHOLD=0.15. A baseline run on
    such a box must stay the baseline — otherwise the A/B silently compares an
    arm against itself and reports 'no difference'."""
    monkeypatch.setenv("FLIP_CONFIDENCE_THRESHOLD", "0.15")
    monkeypatch.setenv("FLIP_MIN_POSITION_AGE_HOURS", "0.0")

    base, roster = _inject_conflict(monkeypatch,
                                    holder_conf=0.50, challenger_conf=0.80)
    out = _run(base, roster)  # baseline: threshold param left at 0

    ov = _ov(out)
    assert ov["conflicts_observed"] >= 1
    assert ov["overrides_fired"] == 0, (
        "an inherited env var armed the baseline arm — the harness must pin "
        "both knobs from its parameters, not read the ambient environment")


def test_env_is_restored_after_the_run(monkeypatch):
    monkeypatch.setenv("FLIP_CONFIDENCE_THRESHOLD", "0.99")
    base, roster = _inject_conflict(monkeypatch)
    _run(base, roster, flip_confidence_threshold=0.15)
    assert os.environ["FLIP_CONFIDENCE_THRESHOLD"] == "0.99"


# ---------------------------------------------------------------------------
# The denominator + the M26 taxonomy
# ---------------------------------------------------------------------------
def test_conflict_ledger_is_the_denominator(monkeypatch):
    """Every run — including the disabled baseline — must publish the conflict
    count, so 'the arm did nothing' is distinguishable from 'the arm never had
    the chance'."""
    base, roster = _inject_conflict(monkeypatch)
    out = _run(base, roster)
    ov = _ov(out)
    for key in ("conflicts_observed", "overrides_fired", "passed_confidence_gap",
                "passed_min_age", "by_tf_class", "max_confidence_gap_seen"):
        assert key in ov, f"missing denominator field {key!r}"


def test_tf_taxonomy_classifies_the_conflict(monkeypatch):
    """trend_donchian (1h) vs ict_scalp_5m (5m) => ratio 12 => cross_clock."""
    base, roster = _inject_conflict(monkeypatch, holder="trend_donchian",
                                    challenger="ict_scalp_5m")
    out = _run(base, roster, attach_full=True)
    assert _ov(out)["by_tf_class"].get("cross_clock", {}).get("conflicts", 0) >= 1
    rec = out["flip_conflicts"][0]
    assert rec["tf_ratio"] == pytest.approx(12.0)
    assert rec["tf_class"] == "cross_clock"


def test_same_clock_pair_is_classified_same_clock():
    """fvg_range_15m (15m) vs turtle_soup (15m) => ratio 1 => same_clock, the
    class M26 P0 measured as money-losing BOTH ways (held −$3.0k AND flip
    −$7.1k).

    Asserted on the classifier directly rather than through a run: whether
    those two strategies happen to hold a position long enough to collide
    depends on their monitors, not on the taxonomy, and an integration
    assertion here would fail for reasons unrelated to what it claims to test.
    """
    assert bs._tf_ratio("turtle_soup", "fvg_range_15m") == pytest.approx(1.0)
    assert bs._m26_tf_class(bs._tf_ratio("turtle_soup", "fvg_range_15m")) == "same_clock"
    # And a genuinely cross-clock pair from the same roster.
    assert bs._m26_tf_class(bs._tf_ratio("fade_breakout_4h", "trend_donchian")) == "cross_clock"


def test_unknown_tf_class_is_its_own_state(monkeypatch):
    """A conflict whose clocks cannot be resolved is `unknown` — never folded
    into either real class, since that would assert coexistence we did not
    measure."""
    assert bs._m26_tf_class(None) == "unknown"
    assert bs._tf_ratio("not_a_strategy", "trend_donchian") is None
    assert bs._m26_tf_class(3.99) == "same_clock"
    assert bs._m26_tf_class(4.0) == "cross_clock"


# ---------------------------------------------------------------------------
# M26 P1 counterfactual arm — restrict the override to one TF class
# ---------------------------------------------------------------------------
# The blind arm's measured loss (-$1,280 over 34 fires, run 31523739722) is one
# number over a MIXED population, and M26 P0 says the split is where the signal
# lives. These arms make the split an A/B on the same cells instead of a
# post-hoc PnL attribution, which would need an unverifiable counterfactual per
# fire. NOTHING LIVE IMPLEMENTS THEM — unrestricted is the deployed shape.
def test_parse_tf_classes_treats_unrestricted_as_the_live_shape():
    """None / empty / 'all' must all mean UNRESTRICTED, because that is what
    production does. A parser that turned an omitted flag into an empty set
    would silently run a never-fires arm and label it as the live override."""
    assert bs._parse_tf_classes(None) is None
    assert bs._parse_tf_classes("") is None
    assert bs._parse_tf_classes("  ") is None
    assert bs._parse_tf_classes("all") is None
    assert bs._parse_tf_classes("cross_clock") == frozenset({"cross_clock"})
    assert bs._parse_tf_classes("Cross_Clock, same_clock") == frozenset(
        {"cross_clock", "same_clock"})


def test_parse_tf_classes_refuses_an_unknown_class_rather_than_dropping_it():
    """Silently dropping an unrecognised name would run a DIFFERENT arm than the
    one requested and report it under the requested label — the unprovenanced-
    diagnostic shape. Fail loudly instead."""
    with pytest.raises(ValueError) as e:
        bs._parse_tf_classes("cross_clock,crossclock")
    assert "crossclock" in str(e.value)


def test_unknown_class_is_never_implied_by_either_real_class():
    """`unknown` fires only when named EXPLICITLY. Folding it into cross_clock
    would assert coexistence the run did not measure; folding it into same_clock
    would assert a transition it did not measure either."""
    assert bs._tf_class_allowed("unknown", None) is True          # unrestricted
    assert bs._tf_class_allowed("unknown", frozenset({"cross_clock"})) is False
    assert bs._tf_class_allowed("unknown", frozenset({"same_clock"})) is False
    assert bs._tf_class_allowed("unknown", frozenset({"unknown"})) is True
    assert bs._tf_class_allowed("cross_clock", frozenset({"cross_clock"})) is True


def test_matching_class_filter_is_identical_to_the_unrestricted_arm(monkeypatch):
    """The fixture's conflict is cross_clock (1h vs 5m, ratio 12). Restricting
    to cross_clock must therefore change NOTHING — a filter that also perturbed
    the matching case would make the same/cross comparison uninterpretable."""
    base, roster = _inject_conflict(monkeypatch)
    blind = _run(base, roster, flip_confidence_threshold=0.15,
                 flip_min_position_age_hours=4.0)
    same = _run(base, roster, flip_confidence_threshold=0.15,
                flip_min_position_age_hours=4.0,
                flip_confgap_tf_classes=frozenset({"cross_clock"}))
    assert _ov(blind)["overrides_fired"] >= 1, "positive control did not fire"
    assert _ov(same)["overrides_fired"] == _ov(blind)["overrides_fired"]
    assert same["net_pnl"] == pytest.approx(blind["net_pnl"])
    assert _ov(same)["suppressed_by_tf_filter"] == 0


def test_non_matching_filter_suppresses_the_fire_AND_records_that_it_would_have(
        monkeypatch):
    """The measurement IS the difference between the two fields.

    Restricting to same_clock on a cross_clock conflict must suppress the fire —
    and must still record `would_fire_tf_blind`, because a suppressed conflict
    and one the predicate never wanted are indistinguishable otherwise, and the
    set of live fires a TF gate would remove is exactly the quantity of interest.

    NOT asserted: that the two arms see the SAME number of would-fire conflicts.
    They do not, and the first version of this test wrongly demanded it (2 vs 1).
    Once the blind arm flips, its position history diverges from the suppressed
    arm's, so the two stop observing the same conflict population — the arm that
    keeps holding simply meets the still-in-TTL challenger again next bar. That
    is inherent to any policy A/B (and is stated as a limit in
    docs/research/flip-override-walkforward-2026-08-11.md), so the cross-arm
    equality is not a property to test. What IS testable is each arm's INTERNAL
    consistency, which is what the assertions below check.
    """
    base, roster = _inject_conflict(monkeypatch)
    blind = _run(base, roster, flip_confidence_threshold=0.15,
                 flip_min_position_age_hours=4.0)
    other = _run(base, roster, flip_confidence_threshold=0.15,
                 flip_min_position_age_hours=4.0, attach_full=True,
                 flip_confgap_tf_classes=frozenset({"same_clock"}))
    assert _ov(blind)["overrides_fired"] >= 1, "positive control did not fire"
    # The blind arm's two fields agree by construction — nothing is filtered.
    assert _ov(blind)["would_fire_tf_blind"] == _ov(blind)["overrides_fired"]
    assert _ov(blind)["suppressed_by_tf_filter"] == 0

    assert _ov(other)["overrides_fired"] == 0
    # ...but the run still KNOWS live would have flipped here, and says how often.
    assert _ov(other)["would_fire_tf_blind"] >= 1
    assert _ov(other)["suppressed_by_tf_filter"] == _ov(other)["would_fire_tf_blind"]
    rec = other["flip_conflicts"][0]
    assert rec["override_fired"] is False
    assert rec["would_fire_tf_blind"] is True
    assert rec["suppressed_by_tf_filter"] is True
    # Suppressed => it behaves as the incumbent `hold`: no confgap exit at all.
    assert other["by_exit_reason"].get("flip_confgap", 0) == 0


def test_a_suppressed_arm_equals_plain_hold(monkeypatch):
    """The strongest statement of the above: when the filter suppresses every
    fire, the arm must be the incumbent, not a third thing. If these diverge the
    filter is doing something beyond gating the override."""
    base, roster = _inject_conflict(monkeypatch)
    hold = _run(base, roster)  # arm inert
    suppressed = _run(base, roster, flip_confidence_threshold=0.15,
                      flip_min_position_age_hours=4.0,
                      flip_confgap_tf_classes=frozenset({"same_clock"}))
    assert suppressed["net_pnl"] == pytest.approx(hold["net_pnl"])
    assert suppressed["total_trades"] == hold["total_trades"]


def test_the_filter_is_declared_in_the_payload(monkeypatch):
    """A restricted result must never be mistakable for the deployed one, so the
    arm block states the filter. `None` is the live shape and says so."""
    base, roster = _inject_conflict(monkeypatch)
    live_shape = _run(base, roster, flip_confidence_threshold=0.15,
                      flip_min_position_age_hours=4.0)
    assert _ov(live_shape)["arm"]["tf_class_filter"] is None
    restricted = _run(base, roster, flip_confidence_threshold=0.15,
                      flip_min_position_age_hours=4.0,
                      flip_confgap_tf_classes=frozenset({"cross_clock"}))
    assert _ov(restricted)["arm"]["tf_class_filter"] == ["cross_clock"]
    # The K that defines the split ships too — a reader cannot reproduce the
    # classification without it.
    assert _ov(restricted)["arm"]["tf_ratio_k"] == bs._M26_TF_RATIO_K
