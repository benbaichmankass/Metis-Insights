"""B6 — a gross-R backtest row must not be byte-identical to a net-R one.

`record_harness_trades.harness_row_to_sim_trade` fell back
``net_r -> gross_r -> r_multiple`` and wrote ``float(r)`` with NO record of
which key supplied it. Gross R has not had fees or slippage deducted, so it is
systematically optimistic — and the trainer's nightly pooled build merges these
rows as ``is_backtest=1`` evidence, unable to tell the two apart.

Notably the SIBLING defect in the same function (strategy-label precedence) was
found and fixed in the 2026-07-19 audit; this one survived that pass. A fix that
closed the instance and not the class.

Four states, never collapsed — and the fourth is the one that matters:
``net_r`` · ``gross_r`` · ``r_multiple`` (the producer used a key that says
NEITHER) · ``None`` (the row PREDATES the stamp — nobody recorded it). The last
two are both "we cannot show this is net", for different reasons, and neither
may be read as net.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ml.datasets.backtest_recorder import (  # noqa: E402
    R_COST_BASIS_AMBIGUOUS,
    R_COST_BASIS_GROSS,
    R_COST_BASIS_NET,
    R_COST_BASIS_STATES,
    encode_backtest_notes,
    parse_backtest_notes,
    sim_trade_to_trade_row,
)

sys.path.insert(0, str(REPO / "scripts" / "ml"))
from record_harness_trades import harness_row_to_sim_trade  # noqa: E402

_BASE = {"entry_time": "2026-08-01T00:00:00Z", "exit_time": "2026-08-01T04:00:00Z",
         "direction": "long", "entry": 100.0, "exit_price": 101.0, "sl": 99.0}


def _row(**kw):
    return {**_BASE, **kw}


# ---------------------------------------------------------------------------
# the producer stamps which key it used
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key,expected", [
    ("net_r", R_COST_BASIS_NET),
    ("gross_r", R_COST_BASIS_GROSS),
    ("r_multiple", R_COST_BASIS_AMBIGUOUS),
])
def test_each_source_key_is_stamped(key, expected):
    out = harness_row_to_sim_trade(_row(**{key: 1.5}), symbol="BTCUSDT")
    assert out is not None
    assert out["r_multiple"] == 1.5
    assert out["meta"]["r_cost_basis"] == expected


def test_precedence_is_unchanged_and_the_stamp_follows_it():
    """net beats gross beats ambiguous — and the STAMP must track the VALUE.

    A stamp that drifted from the number it describes would be worse than no
    stamp: it would assert costs were deducted from a figure they were not.
    """
    out = harness_row_to_sim_trade(
        _row(net_r=1.0, gross_r=2.0, r_multiple=3.0), symbol="BTCUSDT")
    assert (out["r_multiple"], out["meta"]["r_cost_basis"]) == (1.0, R_COST_BASIS_NET)
    out = harness_row_to_sim_trade(_row(gross_r=2.0, r_multiple=3.0), symbol="BTCUSDT")
    assert (out["r_multiple"], out["meta"]["r_cost_basis"]) == (2.0, R_COST_BASIS_GROSS)


def test_a_row_with_no_r_at_all_is_still_skipped():
    """Negative control: the stamp must not resurrect an unlabeled row."""
    assert harness_row_to_sim_trade(_row(), symbol="BTCUSDT") is None


def test_a_zero_r_row_is_kept_not_treated_as_missing():
    """0.0 is a real result. `if not r` would drop every scratch trade —
    the falsy-vs-absent collapse."""
    out = harness_row_to_sim_trade(_row(net_r=0.0), symbol="BTCUSDT")
    assert out is not None and out["r_multiple"] == 0.0
    assert out["meta"]["r_cost_basis"] == R_COST_BASIS_NET


# ---------------------------------------------------------------------------
# it survives the trip into the row the trainer reads
# ---------------------------------------------------------------------------
def test_the_stamp_reaches_the_persisted_row_notes():
    """A stamp the recorder drops is a stamp that does not exist."""
    trade = harness_row_to_sim_trade(_row(gross_r=1.5), symbol="BTCUSDT")
    row = sim_trade_to_trade_row(trade, run_tag="b6-test")
    assert row is not None
    assert parse_backtest_notes(row["notes"])["r_cost_basis"] == R_COST_BASIS_GROSS


def test_a_net_row_is_distinguishable_from_a_gross_one_in_the_persisted_row():
    """The whole finding, as one assertion."""
    net = sim_trade_to_trade_row(
        harness_row_to_sim_trade(_row(net_r=1.5), symbol="BTCUSDT"), run_tag="t")
    gross = sim_trade_to_trade_row(
        harness_row_to_sim_trade(_row(gross_r=1.5), symbol="BTCUSDT"), run_tag="t")
    assert net["pnl_percent"] == gross["pnl_percent"], (
        "sanity: the two rows carry the SAME number — which is exactly why "
        "they used to be indistinguishable"
    )
    assert net["notes"] != gross["notes"]


def test_an_unknown_basis_in_meta_is_not_written_through():
    """Only declared states persist; anything else is `unknown`."""
    trade = harness_row_to_sim_trade(_row(net_r=1.0), symbol="BTCUSDT")
    trade["meta"]["r_cost_basis"] = "definitely_net_trust_me"
    row = sim_trade_to_trade_row(trade, run_tag="t")
    assert parse_backtest_notes(row["notes"])["r_cost_basis"] is None


# ---------------------------------------------------------------------------
# a consumer actually branches on it
# ---------------------------------------------------------------------------
def _grade(rows):
    from scripts.research.backtest_fidelity_calibrate import backtest_fidelity
    return backtest_fidelity(rows)


def test_a_gross_sample_grades_APPROXIMATE_even_with_no_fidelity_label():
    faithful, levers = _grade([
        {"notes": encode_backtest_notes("t", r_cost_basis=R_COST_BASIS_GROSS)}])
    assert faithful is False, (
        "an un-costed sample graded as trustworthy — the stamp is written and "
        "never read, which is the class this fix exists to close"
    )


def test_an_ambiguous_sample_also_grades_APPROXIMATE():
    faithful, _ = _grade([
        {"notes": encode_backtest_notes("t", r_cost_basis=R_COST_BASIS_AMBIGUOUS)}])
    assert faithful is False, "'cannot be shown to be net' was read as 'is net'"


def test_NEGATIVE_CONTROL_a_net_sample_still_grades_faithful():
    """Proves the grader discriminates on the BASIS, not on its own presence."""
    faithful, levers = _grade([
        {"notes": encode_backtest_notes("t", fidelity="faithful",
                                        r_cost_basis=R_COST_BASIS_NET)}])
    assert faithful is True
    assert levers == []


def test_a_legacy_unlabeled_row_still_grades_UNKNOWN_not_approximate():
    """`None` must not be coerced either way: unknown is its own answer."""
    assert _grade([{"notes": "a1-crypto-weekly"}]) == (None, [])


def test_the_cost_basis_does_NOT_leak_into_the_omitted_lever_list():
    """`levers` names EXIT LEVERS the harness omitted. A cost basis is not a
    lever, and putting it there would make the list describe something it is
    not — the semantic substitution this repo files as sub-class A."""
    faithful, levers = _grade([
        {"notes": encode_backtest_notes("t", r_cost_basis=R_COST_BASIS_GROSS)}])
    assert faithful is False
    assert levers == []


def test_the_states_are_declared_once():
    assert R_COST_BASIS_STATES == (R_COST_BASIS_NET, R_COST_BASIS_GROSS,
                                   R_COST_BASIS_AMBIGUOUS)
    assert None not in R_COST_BASIS_STATES, (
        "None is deliberately NOT a member — it means the row predates the "
        "stamp, which is a different fact from any state a producer can emit"
    )


def test_the_name_does_not_collide_with_the_calibrator_s_own_r_basis():
    """`backtest_fidelity_calibrate` owns an `r_basis` meaning WHICH R AXIS
    (stop_distance vs sign_proxy). Ours means COST TREATMENT. One name for two
    concepts, in modules that talk to each other, is the F-113 defect measured
    in this same audit — so assert the two stay apart."""
    from scripts.research.backtest_fidelity_calibrate import R_BASES
    assert set(R_BASES).isdisjoint(set(R_COST_BASIS_STATES))
