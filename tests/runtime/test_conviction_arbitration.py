"""P3 — conviction-driven competing-trade arbitration (advisory / observe-only).

Ref: docs/unified-confidence-risk-DESIGN.md § 3.4 / § 5 P3.

These tests pin the contract: ``annotate_conviction_arbitration`` LOGS what
conviction arbitration would pick but **never changes ``aggregate_intents``'s
decision** (the priority winner / max-qty target is returned unchanged), there
is **no gate / env read**, and it is fail-permissive.
"""
from __future__ import annotations

import inspect

from src.runtime import conviction_arbitration as ca
from src.runtime import intents as intents_mod
from src.runtime.intents import StrategyIntent, aggregate_intents


def _intent(strategy, side, *, target_qty=0.01, confidence=0.0, priority=None,
            timestamp=1000.0):
    return StrategyIntent(
        strategy=strategy, symbol="BTCUSDT", side=side, target_qty=target_qty,
        confidence=confidence, priority=priority, timestamp=timestamp,
        entry=70000.0, sl=69000.0, tp=72000.0,
    )


def _capture(monkeypatch):
    recs = []
    monkeypatch.setattr(ca, "_log_conviction_arbitration",
                        lambda symbol, record: recs.append((symbol, record)))
    return recs


# ---------------------------------------------------------------------------
# Decision is NEVER changed by the observe-only annotation
# ---------------------------------------------------------------------------


def test_conflict_decision_is_identical_with_and_without_the_soak(monkeypatch):
    """The soak is INERT — it annotates, it never decides.

    REWRITTEN 2026-08-31. This was ``test_conflict_decision_unchanged_priority
    _still_wins`` and proved inertness by asserting the PRIORITY winner came
    back (``vwap``, priority 100, confidence 0.10). That conflated two
    different claims: *the soak does not change the decision* — this file's
    actual contract, still true, and what this still asserts — and *declared
    priority outranks confidence in the live election*, a property of
    ``aggregate_intents`` which PR #10544 deliberately REVERSED when it
    graduated conviction into the live sort key. Pinning a specific winner
    made a test of module A fail on an intended change to module B.

    Inertness is now proved DIFFERENTIALLY: same inputs, soak stubbed out vs.
    soak active, identical decision. That is invariant to any future ranking
    change and is a strictly stronger statement of the property than naming a
    winner ever was.
    """
    longi = _intent("vwap", "long", priority=100, confidence=0.10)
    shorti = _intent("turtle_soup", "short", priority=1, confidence=0.90)

    # Arm 1 — the soak is stubbed out ENTIRELY at the call site in `intents`,
    # so the aggregator runs as if the module did not exist. Stubbing only
    # `_log_conviction_arbitration` would be too weak: it would compare two
    # runs that BOTH execute the real annotator, and so could never catch an
    # annotator that mutated the intents it was handed.
    monkeypatch.setattr(intents_mod, "annotate_conviction_arbitration",
                        lambda *a, **k: None)
    without = aggregate_intents([longi, shorti], symbol="BTCUSDT")

    # Arm 2 — the real annotator runs, with its logger captured.
    monkeypatch.undo()
    recs = _capture(monkeypatch)
    with_soak = aggregate_intents([longi, shorti], symbol="BTCUSDT")

    assert with_soak.side == without.side
    assert with_soak.winning_intent.strategy == without.winning_intent.strategy
    assert with_soak.target_qty == without.target_qty

    # The soak still RAN and still recorded its own view of the contest.
    assert len(recs) == 1
    _sym, rec = recs[0]
    assert rec["resolution"] == "priority_conflict"

    # POST-GRADUATION the soak and the live election use the SAME rule, so they
    # agree here where they used to disagree. That is not this test passing by
    # accident — it IS the graduation, and it is why
    # BL-20260831-TRADE-PRIORITISATION-IS-UNPROVEN... records that this soak's
    # agreement rate has now largely degenerated and can no longer be read as
    # independent evidence about the ranking.
    assert rec["actual_winner"] == with_soak.winning_intent.strategy
    assert rec["conviction_winner"] == "turtle_soup"
    assert rec["agrees_with_actual"] is True


def test_conflict_logged_agree_when_priority_matches_conviction(monkeypatch):
    recs = _capture(monkeypatch)
    longi = _intent("vwap", "long", priority=100, confidence=0.90)   # both top
    shorti = _intent("turtle_soup", "short", priority=1, confidence=0.10)

    out = aggregate_intents([longi, shorti], symbol="BTCUSDT")
    assert out.side == "long"
    _sym, rec = recs[0]
    assert rec["agrees_with_actual"] is True
    assert rec["conviction_winner"] == "vwap"


def test_reinforcement_decision_unchanged_maxqty_still_wins(monkeypatch):
    recs = _capture(monkeypatch)
    # Same side; the BIGGER target_qty has the LOWER conviction.
    big = _intent("vwap", "long", target_qty=0.05, confidence=0.10)
    small = _intent("turtle_soup", "long", target_qty=0.01, confidence=0.90)

    out = aggregate_intents([big, small], symbol="BTCUSDT")

    # Max-qty rule unchanged — the 0.05 target wins.
    assert out.side == "long"
    assert out.target_qty == 0.05
    assert out.winning_intent.strategy == "vwap"
    _sym, rec = recs[0]
    assert rec["resolution"] == "same_direction"
    assert rec["actual_winner"] == "vwap"
    assert rec["conviction_winner"] == "turtle_soup"           # by confidence
    assert rec["agrees_with_actual"] is False
    # conviction-weighted blended target = Σ(conf·qty)/Σconf.
    expected = (0.10 * 0.05 + 0.90 * 0.01) / (0.10 + 0.90)
    assert abs(rec["conviction_weighted_target_qty"] - expected) < 1e-9


# ---------------------------------------------------------------------------
# The PRODUCTION path is unsized — qty is NULL + declared, never a measured 0.0
# (BL-20260810-INTENT-TARGET-QTY-ALWAYS-ZERO-TWO-CONSEQUENCES)
# ---------------------------------------------------------------------------


def test_unsized_intents_report_null_qty_not_zero(monkeypatch):
    """target_qty=0.0 is the 'RiskManager sizes per account' sentinel — the
    live value for EVERY directional intent. Writing it out as a literal 0.0
    claims a measured zero target, which is the opposite statement."""
    recs = _capture(monkeypatch)
    out = aggregate_intents(
        [_intent("vwap", "long", target_qty=0.0, confidence=0.10),
         _intent("turtle_soup", "long", target_qty=0.0, confidence=0.90)],
        symbol="BTCUSDT",
    )
    assert out.side == "long"
    _sym, rec = recs[0]
    assert rec["qty_measured"] is False
    assert "not_sized_at_intent_layer" in rec["qty_unmeasured_reason"]
    assert rec["conviction_winner_target_qty"] is None
    assert rec["conviction_weighted_target_qty"] is None
    assert all(row["target_qty"] is None for row in rec["per_intent"])


def test_unsized_conflict_record_declares_unmeasured(monkeypatch):
    recs = _capture(monkeypatch)
    aggregate_intents(
        [_intent("vwap", "long", target_qty=0.0, priority=100, confidence=0.10),
         _intent("turtle_soup", "short", target_qty=0.0, priority=1,
                 confidence=0.90)],
        symbol="BTCUSDT",
    )
    _sym, rec = recs[0]
    assert rec["resolution"] == "priority_conflict"
    assert rec["qty_measured"] is False
    assert "not_sized_at_intent_layer" in rec["qty_unmeasured_reason"]
    assert all(row["target_qty"] is None for row in rec["per_intent"])


def test_sized_intents_still_report_the_qty_and_no_reason(monkeypatch):
    """A caller that genuinely pre-sizes (tests / the backtest harness) must
    still get real numbers — the null is 'unsized', not 'never reported'."""
    recs = _capture(monkeypatch)
    aggregate_intents(
        [_intent("vwap", "long", target_qty=0.05, confidence=0.10),
         _intent("turtle_soup", "long", target_qty=0.01, confidence=0.90)],
        symbol="BTCUSDT",
    )
    _sym, rec = recs[0]
    assert rec["qty_measured"] is True
    assert "qty_unmeasured_reason" not in rec
    assert rec["conviction_winner_target_qty"] == 0.01   # highest confidence
    assert sorted(row["target_qty"] for row in rec["per_intent"]) == [0.01, 0.05]


def test_unsized_winner_selection_is_byte_for_byte_unchanged(monkeypatch):
    """The null-qty change must not move the would-be conviction winner: the
    ordering key still coerces an unsized intent to 0.0 (``_sort_qty``)."""
    recs = _capture(monkeypatch)
    # Equal confidence on both, so the qty key is the discriminator that
    # ordering falls through — with every qty unsized the tiebreaker below it
    # (timestamp) decides, exactly as it did when 0.0 was used directly.
    aggregate_intents(
        [_intent("vwap", "long", target_qty=0.0, confidence=0.5,
                 timestamp=2000.0),
         _intent("turtle_soup", "long", target_qty=0.0, confidence=0.5,
                 timestamp=1000.0)],
        symbol="BTCUSDT",
    )
    _sym, rec = recs[0]
    assert rec["conviction_winner"] == "vwap"  # later timestamp wins the max()


# ---------------------------------------------------------------------------
# Nothing to arbitrate → no record
# ---------------------------------------------------------------------------


def test_single_intent_no_record(monkeypatch):
    recs = _capture(monkeypatch)
    out = aggregate_intents([_intent("vwap", "long", confidence=0.5)],
                            symbol="BTCUSDT")
    assert out.side == "long"
    assert recs == []  # one intent is its own winner — no arbitration happened


def test_all_zero_confidence_no_record(monkeypatch):
    recs = _capture(monkeypatch)
    out = aggregate_intents(
        [_intent("vwap", "long", priority=100, confidence=0.0),
         _intent("turtle_soup", "short", priority=1, confidence=0.0)],
        symbol="BTCUSDT",
    )
    assert out.side == "long"
    assert recs == []  # no conviction signal to arbitrate on


# ---------------------------------------------------------------------------
# Pure compute + fail-permissive
# ---------------------------------------------------------------------------


def test_compute_returns_none_below_two_intents():
    assert ca.compute_conviction_arbitration(
        [_intent("vwap", "long", confidence=0.5)],
        resolution="same_direction", actual_winner_strategy="vwap",
        actual_target_qty=0.01,
    ) is None


def test_annotate_never_raises_on_garbage(monkeypatch):
    # A non-intent object in the list must not break the (observe-only) hook.
    monkeypatch.setattr(ca, "_log_conviction_arbitration",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    # Even if logging blows up, annotate swallows it and returns None.
    assert ca.annotate_conviction_arbitration(
        [_intent("vwap", "long", confidence=0.5),
         _intent("x", "short", confidence=0.5)],
        symbol="BTCUSDT", resolution="priority_conflict",
        actual_winner_strategy="vwap", actual_target_qty=0.01,
    ) is None


def test_aggregate_intents_survives_logging_failure(monkeypatch):
    # The aggregator's decision must be returned even if the soak log raises.
    intents = [_intent("vwap", "long", priority=100, confidence=0.1),
               _intent("turtle_soup", "short", priority=1, confidence=0.9)]
    monkeypatch.setattr(ca, "_log_conviction_arbitration",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    out = aggregate_intents(list(intents), symbol="BTCUSDT")

    # Asserted against the SAME inputs decided with a working (no-op) logger,
    # never against a hardcoded side. What this test is about is that a RAISING
    # soak neither swallows nor alters the decision; that claim must not
    # re-break every time the live ranking legitimately changes (it did, in
    # PR #10544, which is what previously turned this green test red).
    monkeypatch.setattr(ca, "_log_conviction_arbitration", lambda *a, **k: None)
    control = aggregate_intents(list(intents), symbol="BTCUSDT")
    assert out.side == control.side
    assert out.winning_intent.strategy == control.winning_intent.strategy


# ---------------------------------------------------------------------------
# No gate / no env read — advisory is baseline (Prime Directive / design § 8)
# ---------------------------------------------------------------------------


def test_no_env_gate_in_module():
    src = inspect.getsource(ca)
    for forbidden in (
        "CONVICTION_ARBITRATION_MODE", "CONVICTION_ARBITRATION_ENABLED",
        "CONVICTION_ARBITRATION_DISABLED", "_ACCOUNTS",
        "os.environ", "os.getenv", "import os",
    ):
        assert forbidden not in src, f"unexpected gate/env-read: {forbidden}"
