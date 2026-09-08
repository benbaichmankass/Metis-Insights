#!/usr/bin/env python3
"""The four RANKING-KEY ARMS for the trade-prioritisation A/B, and the
in-process installer that makes one of them the election key for a replay.

WHY THIS EXISTS
---------------
`src/runtime/intents.py::_election_sort_key` has been the LIVE primary ranking
key on REAL MONEY since 2026-08-31 (PR #10544) and **has never been shown to
pick the better-performing trade**. It went live on a CORRECTNESS argument —
confidence is evidence about the trade, unlike the strategy NAME it replaced —
not on an outcome result. Operator, 2026-08-31: *"the confidence score is a good
start, but it isn't proven that it's necessarily what's gonna give us the best
trade every time."*

The A/B that would settle it could not be EXPRESSED: the harness always used
whatever key was compiled in. This module is the missing arm.
Design: `docs/research/trade-prioritisation-research-DESIGN.yaml`.

TIER-1, AND THE BOUNDARY IS STRUCTURAL RATHER THAN PROMISED
-----------------------------------------------------------
Nothing here edits `src/runtime/intents.py`. An arm is installed by PATCHING
the module attribute for the duration of one replay and restoring it on exit —
the same technique `scripts/backtest_system.py` already uses for
`_decision_vol_regime`, `_emit_ml_vol_shadow_rows` and `_REGIME_POLICY_PATH`,
and for the same reason: the harness must measure the arm that actually runs.
Measuring the question is not answering it; flipping the live key is Tier-3.

⚠️ `confidence_first` IS THE LIVE FUNCTION, IMPORTED, NEVER RESTATED.
A hand-copied "shipped key" is free to drift from the key it claims to measure,
and an A/B whose baseline arm is a copy measures the copy. So the shipped arm
installs nothing at all — it is `_election_sort_key` itself. The three
counterfactual arms are local, because they have no live original to import.

⚠️ THE TRACK-RECORD TIER IS REPOINTED AT THE REPLAY, NOT AT THE LIVE JOURNAL.
`_election_sort_key` term 3 calls `_track_record_rank`, which reads
`trade_journal.db` — the LIVE trader's realised PnL. In a replay of 2024 price
history that is both look-ahead and a dependency on a machine's DB file, so a
run would be irreproducible AND contaminated. `install_ranking_key` therefore
also patches `_track_record_rank` to a REPLAY-LOCAL recorder fed by the
harness's own closed trades. This applies to EVERY arm including
`confidence_first`, so all four share one definition of "recent PnL".

⚠️ `priority_first` IS THE DECLARED PRE-2026-08-31 ORDERING, NOT A BUG REPLAY.
The real pre-change reinforcement branch carried a defect — a `max()` over a
per-character-negated name tuple, which reverses the alphabet but cannot reverse
LENGTH, so the LONGER name won and a strategy whose name was a strict prefix of
a competitor's could never win that branch (measured 2026-08-31:
`trend_donchian_sol_prop` beat `trend_donchian_sol`). Reproducing that would
measure the bug, not the baseline the switch was made against. This arm is the
ordering the old code DOCUMENTED — priority, then timestamp, then name
ascending — and the difference is stated rather than silently smoothed over.

⚠️ `random_tiebreak` IS THE CONTROL AND IS THE ARM MOST LIKELY TO BE SKIPPED.
If `confidence_first` does not beat it, the honest finding is that arbitration
ORDER does not drive outcome — publishable, and the signal to look at the
confidence SCORE itself instead. M18's adjacent allocator already answered NO
for a neighbouring question (edge -$7, ranker OOS AUC ~0.51), which is why the
control is DECLARED here rather than added if someone remembers.

It is deterministic given `--seed`: a nondeterministic control cannot be
re-run, and a result nobody can reproduce is not evidence. The digest is taken
over `(seed, symbol, strategy, intent timestamp)` — the timestamp is what makes
it re-roll per tick — and over NOTHING evidence-bearing, which is the whole
point of a control.

WHY `ELECTION_TERMS` IS PATCHED TOO
-----------------------------------
`deciding_term()` zips the key tuple against the module-level `ELECTION_TERMS`
names to report `decided_by` — the field the whole result must be stratified by.
An arm that reorders the tuple without reordering the names would emit a
`decided_by` that NAMES THE WRONG TERM: unprovenanced diagnostic output,
sub-class A (`CLAUDE.md` § "Diagnostic provenance"), in the one field the
analysis keys on. Each arm therefore ships its names beside its key.

Self-test: `python3 scripts/research/ranking_keys.py --self-test`
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime import intents as _intents  # noqa: E402

#: The declared arms. `confidence_first` is the SHIPPED key and the baseline;
#: `random_tiebreak` is the CONTROL and the floor the others must clear.
ARMS: Tuple[str, ...] = (
    "confidence_first",
    "priority_first",
    "recent_pnl_first",
    "random_tiebreak",
)
SHIPPED_ARM = "confidence_first"
CONTROL_ARM = "random_tiebreak"

# --------------------------------------------------------------------------
# The replay-local track record
# --------------------------------------------------------------------------
#: Live default (`election_track_record._DEFAULT_WINDOW_DAYS`), imported by
#: VALUE rather than re-chosen so the replay's tier-3 window is the live one.
try:  # pragma: no cover - trivial
    from src.runtime.election_track_record import _DEFAULT_WINDOW_DAYS as _W
    TRACK_RECORD_WINDOW_DAYS = float(_W)
except Exception:  # noqa: BLE001
    TRACK_RECORD_WINDOW_DAYS = 3.0

MEASURED = "measured"
NO_TRADES = "no_trades"
UNREADABLE = "unreadable"


class ReplayTrackRecord:
    """Recent realised PnL per strategy, from the REPLAY's own closed trades.

    The offline analogue of `src.runtime.election_track_record`, and it keeps
    that module's three states rather than inventing its own:

      * ``measured``   — this strategy closed a trade inside the window.
      * ``no_trades``  — we looked; it closed none. **Not a PnL of 0.0.**
      * ``unreadable`` — unreachable here by construction (the recorder is an
        in-memory list), kept so the vocabulary matches the live one and a
        caller cannot collapse two states by accident.

    Rank semantics are the live ones, deliberately: ``-pnl`` for a measured
    strategy (higher recent PnL sorts first under a `min()` key) and ``+inf``
    for an ungraded one — never ``0.0``, which would place a strategy that has
    demonstrated nothing ABOVE every strategy with a losing record.
    """

    def __init__(self, window_days: Optional[float] = None) -> None:
        self.window_days = float(
            TRACK_RECORD_WINDOW_DAYS if window_days is None else window_days)
        # (exit_epoch_seconds, strategy, pnl)
        self._closes: List[Tuple[float, str, float]] = []
        self._now: float = 0.0

    def record_close(self, *, strategy: str, pnl: float, exit_epoch_s: float) -> None:
        self._closes.append((float(exit_epoch_s), str(strategy), float(pnl)))

    def set_now(self, epoch_s: float) -> None:
        """Advance the replay clock. The window is measured back from HERE, not
        from wall-clock time — a replay whose window ended at `time.time()`
        would grade 2024 trades against a 2026 cutoff and always read empty."""
        self._now = float(epoch_s)

    def pnl_map(self) -> Dict[str, float]:
        floor = self._now - self.window_days * 86400.0
        out: Dict[str, float] = {}
        for ts, strat, pnl in self._closes:
            if ts >= floor:
                out[strat] = out.get(strat, 0.0) + pnl
        return out

    def state_for(self, strategy: str) -> str:
        return MEASURED if str(strategy) in self.pnl_map() else NO_TRADES

    def rank(self, strategy: str) -> float:
        """Sort term: LOWER is better. Mirrors `track_record_rank` exactly."""
        entry = self.pnl_map().get(str(strategy))
        if entry is None:
            return float("inf")
        return -entry


# --------------------------------------------------------------------------
# The arms
# --------------------------------------------------------------------------
def _confidence(intent) -> float:  # noqa: ANN001 — mirrors the live signature
    """Delegates to the live coercion so an unreadable confidence sorts LAST
    here for the same reason and in the same way it does in production."""
    return _intents._election_confidence(intent)


def _random_digest(intent, *, seed: int) -> str:  # noqa: ANN001
    """A stable pseudo-random ordering token for the CONTROL arm.

    Deliberately a digest of `(seed, symbol, strategy, timestamp)` and of
    NOTHING evidence-bearing: a control that peeked at confidence, priority or
    track record would not be a control. The timestamp is what makes the draw
    re-roll per tick instead of freezing one permanent ordering of the roster,
    which would be a *name* ranking wearing a random label.
    """
    raw = "|".join((
        str(seed),
        str(getattr(intent, "symbol", "")),
        str(getattr(intent, "strategy", "")),
        str(getattr(intent, "timestamp", "")),
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


#: Term NAMES per arm, in tuple order, for the CONFLICT key (no target_qty).
#: `deciding_term` zips these against the key, so they are the `decided_by`
#: vocabulary for that arm. Keeping them beside the key is what stops
#: `decided_by` naming a term the key no longer carries in that slot.
ARM_TERMS: Dict[str, Tuple[str, ...]] = {
    "confidence_first": _intents.ELECTION_TERMS,
    "priority_first": ("declared_priority", "timestamp", "name"),
    "recent_pnl_first": ("recent_pnl", "confidence", "declared_priority",
                         "timestamp", "name"),
    "random_tiebreak": ("random",),
}


def make_key(arm: str, *, seed: int = 0) -> Callable:
    """Return the `_election_sort_key`-shaped callable for *arm*.

    Signature matches the live one exactly — `(intent, *, include_target_qty)`
    — because the patched attribute is called from BOTH election branches with
    different values of that keyword, and a mismatched arity there is swallowed
    by a fail-permissive caller rather than raising (the failure mode
    `backtest_system.py`'s `_stamped_decision` comment records).
    """
    if arm not in ARMS:
        raise ValueError(f"unknown ranking-key arm {arm!r}; declared arms: {list(ARMS)}")

    if arm == SHIPPED_ARM:
        # THE LIVE FUNCTION. Not a copy, not a reimplementation.
        return _intents._election_sort_key

    if arm == "priority_first":
        def _key(intent, *, include_target_qty: bool = True) -> tuple:  # noqa: ANN001
            terms: tuple = (-intent.target_qty,) if include_target_qty else ()
            return terms + (
                -intent.effective_priority(),
                intent.timestamp,
                intent.strategy.lower(),
            )
        return _key

    if arm == "recent_pnl_first":
        def _key(intent, *, include_target_qty: bool = True) -> tuple:  # noqa: ANN001
            terms: tuple = (-intent.target_qty,) if include_target_qty else ()
            return terms + (
                _intents._track_record_rank(intent.strategy),
                -_confidence(intent),
                -intent.effective_priority(),
                intent.timestamp,
                intent.strategy.lower(),
            )
        return _key

    def _key(intent, *, include_target_qty: bool = True) -> tuple:  # noqa: ANN001
        # ⚠️ A CONSTANT PLACEHOLDER OCCUPIES THE `target_qty` SLOT, AND IT IS
        # NOT DECORATION. `deciding_term` builds its name list as
        # `(target_qty,) + ELECTION_TERMS` whenever `include_target_qty` is
        # true, then ZIPS it against the key. A control that simply omitted the
        # slot would misalign the two and label its random draw `target_qty` —
        # MEASURED before this was fixed: 100 of 208 contested trades on the
        # 2026-09-08 primary run came back `decided_by=target_qty` on an arm
        # that ranks on nothing but a digest. That is unprovenanced diagnostic
        # output (sub-class A) in the one field the whole analysis stratifies
        # by, so it is fixed at the source rather than reworded downstream.
        #
        # The placeholder is a CONSTANT, so it can never differentiate two
        # candidates and can never be reported as the deciding term — the
        # control stays evidence-blind, which is the property that matters.
        terms: tuple = (0.0,) if include_target_qty else ()
        return terms + (_random_digest(intent, seed=seed),)
    return _key


@contextlib.contextmanager
def install_ranking_key(arm: str, *, seed: int = 0,
                        track_record: Optional[ReplayTrackRecord] = None):
    """Make *arm* the election key for the duration of the block, then restore.

    Patches THREE module attributes on `src.runtime.intents`, and each one is
    load-bearing:

      * ``_election_sort_key`` — the ranking itself.
      * ``ELECTION_TERMS``     — so `decided_by` names the arm's own terms
        rather than the shipped arm's (see the module docstring).
      * ``_track_record_rank`` — repointed at the REPLAY's closed trades, so
        the tier-3 term is not the live trader's journal.

    Restoration is unconditional (`finally`), so a raising replay cannot leave
    a counterfactual key installed for whatever runs next in the process.
    """
    prev_key = _intents._election_sort_key
    prev_terms = _intents.ELECTION_TERMS
    prev_rank = _intents._track_record_rank
    try:
        _intents._election_sort_key = make_key(arm, seed=seed)
        _intents.ELECTION_TERMS = ARM_TERMS[arm]
        if track_record is not None:
            _intents._track_record_rank = track_record.rank
        yield
    finally:
        _intents._election_sort_key = prev_key
        _intents.ELECTION_TERMS = prev_terms
        _intents._track_record_rank = prev_rank


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def _mk(strategy: str, *, confidence: float, priority: int = 0,
        side: str = "long", ts: float = 0.0, symbol: str = "BTCUSDT"):
    from src.runtime.intents import StrategyIntent
    # Frozen dataclass — set every field at construction. (It is frozen so a
    # strategy cannot mutate its own intent after emission.)
    return StrategyIntent(strategy=strategy, symbol=symbol, side=side,
                          target_qty=1.0, entry=100.0, sl=99.0, tp=110.0,
                          confidence=confidence, priority=priority,
                          timestamp=ts)


def _self_test() -> int:
    from src.runtime.intents import elect_from_gated

    # --- the shipped arm is the live function, by IDENTITY not by behaviour ---
    assert make_key(SHIPPED_ARM) is _intents._election_sort_key, (
        "the shipped arm must BE the live key; a copy measures the copy")

    a = _mk("aaa_strategy", confidence=0.9, ts=1.0)
    b = _mk("zzz_strategy", confidence=0.5, ts=2.0)

    # --- confidence_first prefers the higher confidence ----------------------
    with install_ranking_key("confidence_first"):
        d = elect_from_gated((a, b))
        assert d.winning_intent.strategy == "aaa_strategy", d.winning_intent
        assert d.meta["decided_by"] == "confidence", d.meta

    # --- priority_first ignores confidence entirely ---------------------------
    # Equal priority (both 0) => it falls through to timestamp, so the EARLIER
    # intent wins regardless of the confidence gap. That is the pre-2026-08-31
    # behaviour and the point of the arm.
    with install_ranking_key("priority_first"):
        d = elect_from_gated((b, a))          # b is the LOWER-confidence one
        assert d.winning_intent.strategy == "aaa_strategy", d.winning_intent
        assert d.meta["decided_by"] == "timestamp", d.meta
        # ...and a declared priority DOES decide, above confidence.
        hi = _mk("low_conf_but_priority", confidence=0.1, priority=5, ts=9.0)
        d2 = elect_from_gated((a, hi))
        assert d2.winning_intent.strategy == "low_conf_but_priority", d2.winning_intent
        assert d2.meta["decided_by"] == "declared_priority", d2.meta

    # --- recent_pnl_first puts the track record ABOVE confidence -------------
    tr = ReplayTrackRecord(window_days=3.0)
    tr.set_now(86400.0 * 10)
    tr.record_close(strategy="zzz_strategy", pnl=500.0, exit_epoch_s=86400.0 * 9)
    with install_ranking_key("recent_pnl_first", track_record=tr):
        d = elect_from_gated((a, b))
        assert d.winning_intent.strategy == "zzz_strategy", (
            "the graded loser-on-confidence must win on recent PnL")
        assert d.meta["decided_by"] == "recent_pnl", d.meta
    # ...and confidence_first, with the SAME track record, still prefers a.
    with install_ranking_key("confidence_first", track_record=tr):
        d = elect_from_gated((a, b))
        assert d.winning_intent.strategy == "aaa_strategy", d.winning_intent

    # --- an UNGRADED strategy sorts LAST in the track-record tier, not first --
    assert tr.rank("aaa_strategy") == float("inf"), (
        "ungraded must be +inf; 0.0 would rank it above every losing record")
    assert tr.rank("zzz_strategy") == -500.0
    assert tr.state_for("aaa_strategy") == NO_TRADES
    assert tr.state_for("zzz_strategy") == MEASURED

    # ...and a close OUTSIDE the window is not counted (the window is measured
    # from the REPLAY clock, not from wall-clock now).
    tr2 = ReplayTrackRecord(window_days=3.0)
    tr2.set_now(86400.0 * 10)
    tr2.record_close(strategy="zzz_strategy", pnl=500.0, exit_epoch_s=86400.0 * 1)
    assert tr2.rank("zzz_strategy") == float("inf"), "a stale close must not grade"

    # --- the control is deterministic, seed-sensitive, and evidence-blind ----
    k0 = make_key("random_tiebreak", seed=1)
    k1 = make_key("random_tiebreak", seed=2)
    assert k0(a) == k0(a), "the control must be reproducible within a seed"
    assert k0(a) != k1(a), "a different seed must draw differently"
    # Evidence-blind: same strategy+timestamp, wildly different evidence => the
    # SAME key. This is the assertion that would fail if someone 'improved' the
    # control by peeking at confidence.
    a_rich = _mk("aaa_strategy", confidence=0.01, priority=99, ts=1.0)
    assert k0(a) == k0(a_rich), "the control must not read any evidence term"
    # ...and it re-rolls per tick rather than freezing one roster ordering.
    a_later = _mk("aaa_strategy", confidence=0.9, ts=2.0)
    assert k0(a) != k0(a_later), "the control must re-roll per tick"

    # --- arity: BOTH branches call the key, with different keywords ----------
    for arm in ARMS:
        k = make_key(arm, seed=7)
        assert isinstance(k(a), tuple)
        assert isinstance(k(a, include_target_qty=False), tuple)

    # --- decided_by names a term the arm actually carries --------------------
    for arm in ARMS:
        with install_ranking_key(arm, seed=3, track_record=tr):
            d = elect_from_gated((a, b))
            term = d.meta["decided_by"]
            assert term in set(ARM_TERMS[arm]) | {
                _intents.ELECTION_TERM_UNCONTESTED,
                _intents.ELECTION_TERM_UNKNOWN,
                _intents.ELECTION_TERM_TARGET_QTY,
            }, f"{arm} emitted decided_by={term!r} which it does not carry"

    # --- restoration, including on an exception ------------------------------
    before = _intents._election_sort_key
    before_terms = _intents.ELECTION_TERMS
    before_rank = _intents._track_record_rank
    try:
        with install_ranking_key("random_tiebreak"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert _intents._election_sort_key is before
    assert _intents.ELECTION_TERMS is before_terms
    assert _intents._track_record_rank is before_rank

    # --- an unknown arm is REFUSED, never silently defaulted -----------------
    try:
        make_key("confidence_second")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("an unknown arm must be refused, not defaulted")

    print("ranking_keys self-test: OK "
          f"({len(ARMS)} arms, control={CONTROL_ARM}, shipped={SHIPPED_ARM})")
    return 0


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-arms", action="store_true")
    args = p.parse_args(argv[1:])
    if args.list_arms:
        for arm in ARMS:
            print(f"{arm}\t{','.join(ARM_TERMS[arm])}")
        return 0
    if args.self_test:
        return _self_test()
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
