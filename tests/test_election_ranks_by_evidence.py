"""The election ranks by EVIDENCE. Name is a last resort, not a ranking.

OPERATOR DIRECTIVE 2026-08-31, twice. First: *"doing it alphabetically is
stupid and not the right way to do it"* — and the first attempt at this merely
made the name sort *correctly*, preserving the exact thing objected to. Then:
*"the deterministic fallback shouldn't be the fucking name. It should be some
other metric that we use that actually shows which one might be better ... what
is the PNL for each of the strategies over the past three days"*.

So the key is now, in order: target size (reinforcement spec only) -> CONFIDENCE
-> declared priority -> RECENT 3-DAY PnL -> timestamp -> name. The last two are
determinism, reached only when every evidence term ties exactly.

MEASURED BASIS (`conviction_arbitration` soak, n=371, 2026-06-17 -> 2026-08-30):
confidence picks a different winner than the old key on 51.5% of contests, and
differentiates on 49.9% — the other 50.1% are exact ties, which is why a
further tier is needed at all rather than optional.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from src.runtime import election_track_record as tr
from src.runtime.intents import StrategyIntent, aggregate_intents, elect_from_gated


@pytest.fixture(autouse=True)
def _no_track_record(monkeypatch):
    """Default: the track-record tier is INERT, so tests isolate other terms.

    Neutral (0.0 for everyone) rather than absent, so it reorders nothing.
    """
    monkeypatch.setattr(tr, "recent_pnl_map", lambda force=False: ({}, tr.UNREADABLE))


def _i(strategy, *, confidence=0.0, side="long", priority=None,
       timestamp=1000.0, target_qty=0.0) -> StrategyIntent:
    kw: Dict[str, Any] = dict(
        strategy=strategy, symbol="SOLUSDT", side=side, target_qty=target_qty,
        regime="trending", adx_14=30.0, vol_regime=None,
        entry=100.0, sl=95.0, tp=115.0, timestamp=timestamp,
        confidence=confidence,
    )
    if priority is not None:
        kw["priority"] = priority
    return kw and StrategyIntent(**kw)


def _win(*intents):
    return elect_from_gated(
        tuple(intents), symbol="SOLUSDT", intents_before_gate=len(intents)
    ).winning_intent.strategy


# --- confidence is the primary discriminator -------------------------------


def test_higher_confidence_wins_regardless_of_name():
    """Both orderings, so the result cannot come from input order."""
    lo, hi = _i("aaa_first", confidence=0.2), _i("zzz_last", confidence=0.9)
    assert _win(lo, hi) == "zzz_last"
    assert _win(hi, lo) == "zzz_last"


def test_confidence_beats_the_alphabet_on_the_real_pair():
    """The pair this whole workstream is about."""
    assert _win(
        _i("trend_donchian_sol", confidence=0.9),
        _i("trend_donchian_sol_prop", confidence=0.4),
    ) == "trend_donchian_sol"
    # ...and the other way, which the name could never express.
    assert _win(
        _i("trend_donchian_sol", confidence=0.2),
        _i("trend_donchian_sol_prop", confidence=0.8),
    ) == "trend_donchian_sol_prop"


def test_confidence_outranks_declared_priority():
    """The real semantic change: evidence above declaration."""
    assert _win(
        _i("high_priority_low_conf", confidence=0.3, priority=9),
        _i("low_priority_high_conf", confidence=0.8, priority=1),
    ) == "low_priority_high_conf"


def test_priority_still_decides_when_confidence_ties():
    """Priority is BELOW confidence, not removed."""
    assert _win(
        _i("aaa_low_priority", confidence=0.5, priority=1),
        _i("zzz_high_priority", confidence=0.5, priority=9),
    ) == "zzz_high_priority"


def test_an_unreadable_confidence_sorts_last_not_first():
    """A candidate we cannot grade must not out-rank one that published a score."""
    class _Broken:
        strategy, symbol, side = "broken", "SOLUSDT", "long"
        target_qty, timestamp = 0.0, 1000.0
        entry, sl, tp = 100.0, 95.0, 115.0
        confidence = "not-a-number"
        regime = vol_regime = None
        adx_14 = 30.0

        def effective_priority(self):
            return 0

    from src.runtime.intents import _election_sort_key
    good = _i("aaa_good", confidence=0.5)
    assert _election_sort_key(good) < _election_sort_key(_Broken())


# --- the track-record tier breaks what confidence cannot -------------------


def test_recent_pnl_breaks_a_confidence_tie(monkeypatch):
    """The 50.1% case: equal confidence, so the RECORD decides — not the name.

    `aaa_loser` is alphabetically first and would have won the old fallback.
    """
    monkeypatch.setattr(tr, "recent_pnl_map", lambda force=False: (
        {"aaa_loser": (-250.0, tr.MEASURED), "zzz_winner": (+400.0, tr.MEASURED)},
        tr.MEASURED,
    ))
    assert _win(
        _i("aaa_loser", confidence=0.5),
        _i("zzz_winner", confidence=0.5),
    ) == "zzz_winner", "the alphabet decided a tie that a track record could break"


def test_an_ungraded_strategy_does_not_beat_a_measured_losing_one(monkeypatch):
    """+inf, never 0.0.

    A zero would rank "no track record" ABOVE every strategy with a losing
    record — asserting an observation nobody made, the fabricated-zero defect
    this repo avoids in exposure_soak and conviction_arbitration.
    """
    monkeypatch.setattr(tr, "recent_pnl_map", lambda force=False: (
        {"has_record": (-10.0, tr.MEASURED)}, tr.MEASURED,
    ))
    assert _win(
        _i("has_record", confidence=0.5),
        _i("aaa_no_record", confidence=0.5),
    ) == "has_record"


def test_an_unreadable_track_record_reorders_nothing(monkeypatch):
    """A tiebreak that cannot be read must never change a routing decision."""
    monkeypatch.setattr(tr, "recent_pnl_map", lambda force=False: ({}, tr.UNREADABLE))
    # Falls through to timestamp: the earlier emission wins, as before.
    assert _win(
        _i("zzz_earlier", confidence=0.5, timestamp=10.0),
        _i("aaa_later", confidence=0.5, timestamp=20.0),
    ) == "zzz_earlier"


def test_a_raising_track_record_never_breaks_the_election(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("journal exploded")

    monkeypatch.setattr(tr, "track_record_rank", _boom)
    assert _win(_i("aaa", confidence=0.9), _i("bbb", confidence=0.1)) == "aaa"


# --- name is reached only as a last resort ---------------------------------


def test_name_decides_only_when_every_evidence_term_ties():
    """It is still deterministic — just no longer a RANKING."""
    assert _win(
        _i("bbb", confidence=0.5, timestamp=1000.0),
        _i("aaa", confidence=0.5, timestamp=1000.0),
    ) == "aaa"


def test_the_conflict_branch_does_not_rank_by_target_size():
    """Long-vs-short has no 'keep the larger target' rule.

    Including target size there would let a bigger-target SHORT beat a
    better-supported LONG on size alone.
    """
    desired = aggregate_intents([
        _i("big_short", side="short", target_qty=0.05, confidence=0.2),
        _i("small_long", side="long", target_qty=0.01, confidence=0.9),
    ], symbol="SOLUSDT")
    assert desired.winning_intent.strategy == "small_long"
    assert desired.side == "long"


# --- the ranking must be falsifiable from the audit log --------------------


def _decided(*intents):
    return aggregate_intents(list(intents), symbol="SOLUSDT").meta.get("decided_by")


def test_decided_by_names_the_term_that_actually_separated_the_winner():
    """"Ranked by confidence" is a claim about the CODE.

    Whether confidence ever DECIDES anything is a claim about the data, and on
    the measured population it decides only ~half the time. Without this field
    a reader cannot tell a system where confidence drives routing from one
    where every contest falls through to the last-resort terms.
    """
    assert _decided(_i("a", confidence=0.9), _i("b", confidence=0.2)) == "confidence"
    assert _decided(
        _i("a", confidence=0.5, priority=1), _i("b", confidence=0.5, priority=9)
    ) == "declared_priority"
    # Everything ties -> the honest answer is the last-resort term, named.
    assert _decided(_i("bbb", confidence=0.5), _i("aaa", confidence=0.5)) == "name"


def test_decided_by_reports_recent_pnl_when_that_is_what_broke_the_tie(monkeypatch):
    monkeypatch.setattr(tr, "recent_pnl_map", lambda force=False: (
        {"aaa_loser": (-250.0, tr.MEASURED), "zzz_winner": (+400.0, tr.MEASURED)},
        tr.MEASURED,
    ))
    assert _decided(
        _i("aaa_loser", confidence=0.5), _i("zzz_winner", confidence=0.5)
    ) == "recent_pnl"


def test_decided_by_on_the_conflict_branch_too():
    """The branch tag says 'priority_conflict'; the FIELD says what decided."""
    desired = aggregate_intents([
        _i("long_high_conf", side="long", confidence=0.9),
        _i("short_low_conf", side="short", confidence=0.1),
    ], symbol="SOLUSDT")
    assert desired.meta["resolution"] == "priority_conflict"   # legacy branch tag
    assert desired.meta["decided_by"] == "confidence"          # what actually decided


def test_decided_by_never_raises_into_the_election():
    """Observability must never break a tick."""
    from src.runtime.intents import ELECTION_TERM_UNKNOWN, deciding_term

    class _Bad:
        strategy = "bad"

    assert deciding_term(_Bad(), [_Bad(), _Bad()]) == ELECTION_TERM_UNKNOWN


def test_a_single_candidate_is_uncontested_not_a_decision():
    """One intent had no contest — saying a term 'decided' it would be false."""
    from src.runtime.intents import ELECTION_TERM_UNCONTESTED, deciding_term

    only = _i("solo", confidence=0.5)
    assert deciding_term(only, [only]) == ELECTION_TERM_UNCONTESTED
