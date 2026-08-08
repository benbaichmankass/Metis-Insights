"""The calibrator must CONSUME the producing harness's fidelity claim (P2).

The defect this pins, measured 2026-08-07:

* `regime_debt_matrix.build_harness_cmd` already computes `faithful` +
  `omitted_levers` honestly per leg — `trend_donchian`, the PRIMARY calibration
  target, is `faithful=False`, omitting five EXIT levers
  (`exit_head_{action,model,threshold}`, `trail_decay_{arm_r,tight_mult}`).
* `backtest_augment_runner` printed that into a human summary and **did not
  persist it**.
* `backtest_fidelity_calibrate` therefore could not read it *even in principle*
  and would stamp such a leg `calibrated` — "TRUSTED OOS evidence" — on rows the
  producing harness itself disowned.

Written-and-never-read is the exact class `provenance-consumer-guard` exists
for, and this one sat directly under the P3 promotion gate that is meant to
replace "live-holdout only".
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from ml.datasets.backtest_recorder import (  # noqa: E402
    encode_backtest_notes, parse_backtest_notes,
)
from scripts.research.backtest_fidelity_calibrate import (  # noqa: E402
    agreement, backtest_fidelity,
)


def _clearing_samples():
    """Two samples that clear every metric gate, so the ONLY thing that can
    change the verdict in these tests is the fidelity input."""
    live = [1.0, -1.0] * 20
    backtest = [1.0, -1.0] * 20
    return live, backtest


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def test_metrics_alone_still_certify_when_the_harness_is_faithful():
    live, bt = _clearing_samples()
    out = agreement(live, bt, harness_faithful=True)
    assert out["verdict"] == "calibrated"
    assert out["harness_faithful"] is True


def test_an_approximate_harness_can_never_be_calibrated():
    """THE REGRESSION. Same numbers, same clearing metrics — the only
    difference is that the producing harness declared itself incomplete."""
    live, bt = _clearing_samples()
    out = agreement(live, bt, harness_faithful=False,
                    omitted_levers=["exit_head_action", "trail_decay_arm_r"])
    assert out["verdict"] == "approximate-harness"
    assert out["verdict"] != "calibrated"
    # the reason must NAME what was missing, not just refuse
    assert "exit_head_action" in out["reason"]
    assert out["omitted_levers"] == ["exit_head_action", "trail_decay_arm_r"]


def test_unlabeled_rows_are_unknown_not_faithful():
    """A legacy row carries no label. `None` must NOT be read as faithful — the
    same `UNVERIFIED != MEASURED` rule the provenance module enforces one level
    up. It stays certifiable (nothing contradicts it) but the output SAYS the
    claim is absent rather than asserting completeness."""
    live, bt = _clearing_samples()
    out = agreement(live, bt, harness_faithful=None)
    assert out["verdict"] == "calibrated"
    assert out["harness_faithful"] is None, (
        "absent label must round-trip as None, never coerced to True/False")


def test_fidelity_is_reported_even_when_it_does_not_bite():
    """A drifting leg still states the harness claim, so a reader never has to
    go find out whether the drift was measured against a complete model."""
    out = agreement([1.0] * 40, [-1.0] * 40, harness_faithful=False,
                    omitted_levers=["trail_decay_arm_r"])
    assert out["verdict"] == "drifts"          # metrics fail first
    assert out["harness_faithful"] is False    # …and the claim is still stated


def test_insufficient_live_is_unaffected():
    out = agreement([1.0] * 3, [1.0] * 40, harness_faithful=False)
    assert out["verdict"] == "insufficient-live"


# --------------------------------------------------------------------------
# the row-level label round-trip
# --------------------------------------------------------------------------

def test_legacy_notes_stay_byte_identical():
    """No migration: with no fidelity the encoder returns the bare run_tag, so
    every pre-existing row is untouched and still parses."""
    assert encode_backtest_notes("a1-crypto-weekly") == "a1-crypto-weekly"
    assert parse_backtest_notes("a1-crypto-weekly") == {
        "run_tag": "a1-crypto-weekly", "fidelity": None, "omitted_levers": []}


def test_labeled_notes_round_trip():
    raw = encode_backtest_notes("a1", fidelity="approximate",
                                omitted_levers=["exit_head_action"])
    assert parse_backtest_notes(raw) == {
        "run_tag": "a1", "fidelity": "approximate",
        "omitted_levers": ["exit_head_action"]}


@pytest.mark.parametrize("bad", ["{not json", "{}", None, 42, ""])
def test_parse_never_raises(bad):
    out = parse_backtest_notes(bad)
    assert set(out) == {"run_tag", "fidelity", "omitted_levers"}
    assert out["fidelity"] is None


# --------------------------------------------------------------------------
# leg-level aggregation
# --------------------------------------------------------------------------

def _row(fid=None, levers=None):
    return {"notes": encode_backtest_notes("a1", fidelity=fid,
                                           omitted_levers=levers)}


def test_one_approximate_row_makes_the_whole_sample_approximate():
    """A sample MIXING complete and incomplete rows cannot be graded complete.
    Conservative on purpose."""
    rows = [_row("faithful"), _row("faithful"),
            _row("approximate", ["exit_head_action"])]
    faithful, levers = backtest_fidelity(rows)
    assert faithful is False
    assert levers == ["exit_head_action"]


def test_all_faithful_rows_grade_faithful():
    faithful, levers = backtest_fidelity([_row("faithful"), _row("faithful")])
    assert faithful is True and levers == []


def test_no_labels_grades_unknown():
    faithful, levers = backtest_fidelity([{"notes": "a1"}, {"notes": "a1"}])
    assert faithful is None and levers == []


def test_omitted_levers_are_unioned_and_sorted():
    rows = [_row("approximate", ["trail_decay_arm_r"]),
            _row("approximate", ["exit_head_action", "trail_decay_arm_r"])]
    _, levers = backtest_fidelity(rows)
    assert levers == ["exit_head_action", "trail_decay_arm_r"]


# --------------------------------------------------------------------------
# the live roster keeps this honest
# --------------------------------------------------------------------------

def test_trend_donchian_trail_levers_are_modelled_now():
    """The trail levers ARE modelled — asserted unconditionally, because this
    is a property of the harness, not of where the test runs.

    History: this assertion used to be `omitted >= {exit_head_action,
    trail_decay_arm_r}`, and it was correct until the 15 research-only levers
    were ported into `scripts/backtest_trend.py` (2026-08-08). `trail_decay_*`
    is now forwarded, so the omitted set shrank 5 -> 3. The old test's own
    docstring prescribed exactly this: "if this ever flips ... update the
    design doc rather than deleting this test."

    The direction matters. Asserting the levers are ABSENT from `omitted`
    (rather than just loosening the old superset) is what catches a regression
    that un-ports them — a weaker assertion would pass either way.
    """
    from scripts.research import regime_debt_matrix as rdm

    cfg = rdm.resolve_strategy("trend_donchian")
    if not cfg:
        pytest.skip("trend_donchian not declared in config/strategies.yaml")
    harness = rdm.classify(cfg)
    _, _faithful, omitted = rdm.build_harness_cmd(
        "trend_donchian", cfg, harness, "/tmp/x.csv", "1h",
        "/tmp/e.jsonl", "/tmp/j.json")
    assert not ({"trail_decay_arm_r", "trail_decay_tight_mult"} & set(omitted)), (
        f"a trail_decay lever is omitted again: {sorted(omitted)} — "
        "scripts/backtest_trend.py models both, so this means the flag "
        "wiring in regime_debt_matrix._TREND_LEVER_FLAG regressed")


def test_trend_donchian_stays_approximate_because_the_HARNESS_omits_exit_head():
    """`fidelity` grades the HARNESS RUN, and the harness has no exit-head flag.

    So `trend_donchian` is `approximate` with the three `exit_head_*` levers
    omitted — everywhere, including the trainer. This is asserted flatly and on
    purpose: an earlier revision of this PR tried to make the fold conditional
    on a head being loadable, which would have upgraded the leg to `faithful`
    on the trainer. That is an overclaim — the row's numbers come from the
    harness, which never applied the head; the replay is a separate pass.
    """
    from scripts.research import regime_debt_matrix as rdm

    cfg = rdm.resolve_strategy("trend_donchian")
    if not cfg:
        pytest.skip("trend_donchian not declared in config/strategies.yaml")
    harness = rdm.classify(cfg)
    _, faithful, omitted = rdm.build_harness_cmd(
        "trend_donchian", cfg, harness, "/tmp/x.csv", "1h",
        "/tmp/e.jsonl", "/tmp/j.json")
    assert faithful is False
    assert {"exit_head_action", "exit_head_model",
            "exit_head_threshold"} <= set(omitted)


@pytest.mark.parametrize("replayable", [True, False])
def test_exit_head_replayability_is_RECORDED_and_actually_varies(replayable):
    """Both branches, because the bug this replaces was an INERT conditional.

    The previous attempt gated the `_UNREPLAYABLE` fold on `exit_head_replayable()`
    and produced byte-identical output either way — `build_harness_cmd` already
    lists every unflagged cfg key in `omitted`, so a union could never remove
    one. The single-branch test passed and proved nothing.

    Parametrizing over the probe is what makes "this field actually varies" an
    assertion rather than an assumption, so a future refactor that quietly
    re-inerts it fails here.
    """
    from unittest import mock
    from scripts.research import regime_debt_matrix as rdm

    cfg = rdm.resolve_strategy("trend_donchian")
    if not cfg:
        pytest.skip("trend_donchian not declared in config/strategies.yaml")
    row: dict = {}
    omitted = ["exit_head_action", "exit_head_model", "exit_head_threshold"]
    with mock.patch.object(rdm, "exit_head_replayable", return_value=replayable):
        rdm.annotate_exit_head_replayability(cfg, row, omitted)

    assert row["exit_head_replayable"] is replayable
    if replayable:
        assert row["exit_head_deferred_to_replay"] == omitted, (
            "a head loads here, so the exit-head gap is MEASURABLE by the "
            "replay and must be named as deferred rather than silently left "
            "looking like a permanent omission"
        )
    else:
        assert row["exit_head_deferred_to_replay"] == [], (
            "no head here, so nothing is deferred-to-replay — the levers are "
            "genuinely unmeasured and only omitted_levers should carry them"
        )


def test_deferred_to_replay_is_empty_when_the_leg_declares_no_exit_head():
    """A leg with no exit head has nothing to defer — the field must not
    manufacture entries from a config that never asked for one."""
    from scripts.research import regime_debt_matrix as rdm

    row: dict = {}
    rdm.annotate_exit_head_replayability({"donchian": 20}, row, [])
    assert row["exit_head_replayable"] is True   # nothing declared → nothing to replay
    assert row["exit_head_deferred_to_replay"] == []
