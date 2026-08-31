"""The same-side election's name tiebreak, and the prefix defect it had.

THE DEFECT. The key was ``max()`` over
``(target_qty, effective_priority, -timestamp, tuple(-ord(c) for c in name))``
and its own comment said *"max() wants 'earlier alphabet' to win, so we negate
by sorting descending"*. Per-character negation reverses the ALPHABET but
cannot reverse LENGTH: a shorter tuple that is a prefix of a longer one still
compares SMALLER, so ``max()`` returned the LONGER name. **A strategy whose
name is a strict prefix of a competitor's could never win this branch.**

WHY IT MATTERED RATHER THAN BEING A CURIOSITY. Every SOLUSDT contender is
``DEFAULT_PRIORITIES`` 0 and ``target_qty`` is the inert 0.0 sentinel
(``BL-20260810-INTENT-TARGET-QTY-ALWAYS-ZERO-TWO-CONSEQUENCES``), so on a
debounced closed-bar tick the NAME is the whole decision — deterministically,
every tick, forever. Measured live: ``trend_donchian_sol`` won **0 of 60**
SOLUSDT buy-side ticks 2026-08-01..08-27 and wrote zero journal rows on
``bybit_1`` (``BL-20260827-PROP-ONLY-TWIN-WINS-THE-GLOBAL-SYMBOL-SLOT-AND-STARVES-ITS-PAPER-SIBLING``).
That had been read as small-sample bad luck; it was arithmetic.

⚠️ THIS IS WHY PER-ACCOUNT ARBITRATION ALONE IS NOT ENOUGH, and the point is
easy to miss: the fan-out stops ``trend_donchian_sol_prop`` (``breakout_1``)
taking the slot across accounts, but ``bybit_1`` ALSO runs
``trend_donchian_sol_4h``, of which ``trend_donchian_sol`` is again a prefix.
Both fixes are needed for the motivating symptom to clear.

⚠️ THE FIX DOES NOT MAKE THE ORDERING GOOD. Ranking by name is arbitrary;
``StrategyIntent.confidence`` reaches this decision and is read by neither key
(``BL-20260831-CONFIDENCE-IS-CARRIED-TO-THE-ELECTION-AND-READ-BY-NEITHER-SORT-KEY``).
This only makes the code do what it always claimed to.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from src.runtime.intents import StrategyIntent, elect_from_gated


def _intent(strategy: str, side: str = "long", symbol: str = "SOLUSDT",
            timestamp: float | None = None,
            priority: int | None = None) -> StrategyIntent:
    kw: Dict[str, Any] = dict(
        strategy=strategy, symbol=symbol, side=side, target_qty=0.0,
        regime="trending", adx_14=30.0, vol_regime=None,
        entry=100.0, sl=95.0, tp=115.0,
    )
    if timestamp is not None:
        kw["timestamp"] = timestamp
    if priority is not None:
        kw["priority"] = priority
    return StrategyIntent(**kw)


def _winner(*names, timestamp=1000.0):
    cands = tuple(_intent(n, timestamp=timestamp) for n in names)
    desired = elect_from_gated(cands, symbol="SOLUSDT", intents_before_gate=len(cands))
    return desired.winning_intent.strategy


# --- the defect, pinned both ways ------------------------------------------


@pytest.mark.parametrize("shorter,longer", [
    ("trend_donchian_sol", "trend_donchian_sol_prop"),   # the cross-account twin
    ("trend_donchian_sol", "trend_donchian_sol_4h"),     # the SAME-account sibling
    ("trend_donchian_eth", "trend_donchian_eth_prop"),
    ("a", "ab"),
    ("abc", "abcd"),
])
def test_a_prefix_name_can_win(shorter, longer):
    """The regression. Before the fix `max()` returned `longer` in every row.

    Order-independent: asserted both ways round so the result cannot come from
    input ordering rather than from the key.
    """
    assert _winner(shorter, longer) == shorter
    assert _winner(longer, shorter) == shorter


def test_the_two_real_sol_donchians_no_longer_shut_out_the_base_leg():
    """The live case, with all five bybit_1 SOL legs contending at once."""
    assert _winner(
        "trend_donchian_sol_4h",
        "sol_pullback_2h",
        "trend_donchian_sol",
        "ict_scalp_sol_5m",
        "ict_scalp_sol_15m",
    ) == "ict_scalp_sol_15m"   # genuinely earliest alphabetically, not a prefix artifact


# --- the documented intent, on every axis ----------------------------------


def test_earlier_alphabet_wins_when_nothing_else_separates():
    assert _winner("aaa", "bbb", "ccc") == "aaa"
    assert _winner("ccc", "bbb", "aaa") == "aaa"


def test_priority_outranks_name():
    """Name is the LAST tiebreak — a higher priority must still win."""
    hi = _intent("zzz_last_alphabetically", priority=9)
    lo = _intent("aaa_first_alphabetically", priority=1)
    desired = elect_from_gated((lo, hi), symbol="SOLUSDT", intents_before_gate=2)
    assert desired.winning_intent.strategy == "zzz_last_alphabetically"


def test_earlier_timestamp_outranks_name():
    """Timestamp sits above name, so the earlier emission wins regardless."""
    early = _intent("zzz_last_alphabetically", timestamp=1000.0)
    late = _intent("aaa_first_alphabetically", timestamp=2000.0)
    desired = elect_from_gated((late, early), symbol="SOLUSDT", intents_before_gate=2)
    assert desired.winning_intent.strategy == "zzz_last_alphabetically"


# --- the two keys must agree ------------------------------------------------


def test_same_side_and_conflict_keys_agree_on_a_prefix_pair():
    """The two branches expressed ONE ordering in two ways and disagreed.

    `_conflict_sort_key` sorts ascending on the name directly and never had the
    defect, so before the fix the same-side branch picked
    `trend_donchian_sol_prop` while the conflict branch picked
    `trend_donchian_sol` — opposite answers about the same pair. They are now
    structurally the same key.
    """
    import inspect
    import io
    import tokenize

    from src.runtime import intents as _m

    src = inspect.getsource(_m.elect_from_gated)
    # Scan CODE, not prose. The comment above the fix quotes the old
    # expression verbatim, so a raw substring check matches its own
    # documentation — the "annotation excluded from its own evidence" rule
    # `collapsed-state-guard` already applies to its `# provenance:` override.
    code = "".join(
        tok.string
        for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type not in (tokenize.COMMENT, tokenize.STRING)
    )
    assert "ord(" not in code, (
        "the per-character negation is back in CODE; it cannot reverse LENGTH "
        "and silently shuts out every prefix name"
    )
    # Same-side winner and conflict-branch ordering must agree.
    same_side = _winner("trend_donchian_sol", "trend_donchian_sol_prop")
    conflict_first = sorted(
        ["trend_donchian_sol", "trend_donchian_sol_prop"], key=str.lower
    )[0]
    assert same_side == conflict_first == "trend_donchian_sol"
