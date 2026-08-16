"""The regime-flip exit predicate — ONE definition, shared by replay and runtime.

WHAT A FLIP EXIT IS. A trade is opened while its (strategy, direction) sits in
an ON cell of `config/regime_policy.yaml`. If, on some later in-trade bar, the
frozen ADX-14 `regime_label` moves that same (strategy, direction) into an OFF
cell, the regime that justified the entry has flipped and the position is
closed at that bar's close.

WHY THIS MODULE EXISTS (operator decision (c), 2026-08-14).

`regime_flip_exit` is a column in the M20 coverage matrix carrying 43
`honest_negative` dispositions, and it has **no runtime implementation at all**
— verified 2026-08-14: zero references under `src/`, zero in `config/`. The
only implementations are the offline replays
`scripts/research/m20_regime_flip_replay.py` and `m20_flip_replay_sweep.py`.
Building it as a YAML-declared, **default-off** close path is the approved work;
this module is its first half, and it is deliberately NOT yet called by the
order path.

THE DUPLICATE THAT MOTIVATED STARTING HERE. The replay does not call the live
gate — it reimplements the cell predicate as a local `off_cell()` whose comment
says it "mirrors policy._evaluate_trend_cell". Two copies of a decision
predicate is the drift shape this repo keeps paying for, and here it is worse
than usual: the replay's copy is what GRADED all 43 matrix cells, so a
divergence would mean the recorded dispositions describe a rule the live gate
does not apply.

MEASURED BEFORE WRITING ANY CODE, rather than assumed: the two agree on **all
100** (label, strategy, side) triples of the committed policy — 4 labels x 10
strategy keys (plus the absent-key default path) x 2 sides, **0
disagreements**. So the 43 gradings stand. But they agree by coincidence of two
implementations rather than by construction, which is precisely the state that
drifts silently, so the remedy is to collapse them rather than to test them
against each other forever.

DEFAULT-OFF IS THE CONTRACT, AND IT IS NOT A `*_ENABLED` GATE. The Prime
Directive forbids hiding a *required* capability behind a default-off flag. A
flip exit is not a required capability — it is an additional, unvalidated exit
lever whose own evidence column is 43 negatives, so it must be declared per
strategy to do anything, exactly like `stale_exit_bars` / `trail_decay_arm_r`
in `config/strategies.yaml`. A leg that declares nothing gets byte-for-byte its
current behaviour.

THREE STATES, NEVER COLLAPSED. `evaluate` returns a `FlipExitVerdict` whose
`state` is:

  * ``not_declared`` — this leg has no flip-exit declaration. Not "we looked and
    the regime is fine": we did not look, and nothing may act on it.
  * ``no_flip``      — declared, evaluated, and the regime still permits the
                       position.
  * ``flip``         — declared, evaluated, and the regime that justified the
                       entry is now OFF. `close_reason` is set.

Collapsing ``not_declared`` into ``no_flip`` would make an unconfigured leg
indistinguishable from a leg actively judged safe, which is the collapsed-state
bug this repo registers contracts for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.runtime.regime.policy import _evaluate_trend_cell

#: `config/strategies.yaml` key a leg sets to opt in. Absent => `not_declared`.
FLIP_EXIT_DECLARED_KEY = "regime_flip_exit"

STATE_NOT_DECLARED = "not_declared"
STATE_NO_FLIP = "no_flip"
STATE_FLIP = "flip"


@dataclass(frozen=True)
class FlipExitVerdict:
    """Why this module did or did not ask for an exit.

    `state` is the field to branch on. `regime` and `cell` are echoed so a soak
    row or an audit line records WHAT was read, not merely the conclusion — the
    diagnostic-provenance discipline: a verdict that does not carry its input
    cannot be checked later.
    """

    state: str
    regime: Optional[str] = None
    cell: Optional[str] = None
    close_reason: Optional[str] = None

    @property
    def should_exit(self) -> bool:
        """True ONLY on a positive flip. `not_declared` is never an exit."""
        return self.state == STATE_FLIP


def is_declared(strategy_cfg: Optional[Dict[str, Any]]) -> bool:
    """Has this leg opted in?

    Default OFF: a missing key, a `None`, or an explicit falsy value all mean
    the lever does not run. Only a truthy declaration arms it.
    """
    if not isinstance(strategy_cfg, dict):
        return False
    return bool(strategy_cfg.get(FLIP_EXIT_DECLARED_KEY))


def cell_is_off(
    *,
    policy: Dict[str, Any],
    regime: Optional[str],
    strategy_key: str,
    direction: str,
) -> "tuple[bool, Optional[str]]":
    """Does (strategy_key, direction) sit in an OFF cell under `regime`?

    Returns `(is_off, cell_label)`. The label rides along deliberately: a caller
    that records only the boolean cannot later distinguish an `off` cell from a
    `default-on` or `unknown-regime` one, and those are different reasons for
    the same answer.

    Delegates to the LIVE gate's own `_evaluate_trend_cell` rather than reading
    the policy dict directly. That is the whole point of the module: the flip
    exit must mean the same thing as the gate that refused the entry, and the
    only way to guarantee that is to ask the same function.

    `_evaluate_trend_cell` is permissive by construction — an unknown regime, an
    unlisted strategy, or a non-directional side all return `gated=False` — so a
    detector warm-up or a policy gap can never manufacture an exit.
    """
    verdict = _evaluate_trend_cell(
        strategy=strategy_key,
        side=direction,
        regime=regime,
        policy=policy,
    )
    return bool(verdict.get("gated")), verdict.get("cell")


def evaluate(
    *,
    strategy_cfg: Optional[Dict[str, Any]],
    policy: Dict[str, Any],
    regime: Optional[str],
    strategy_key: str,
    direction: str,
) -> FlipExitVerdict:
    """The single decision. Observe-only today — nothing calls this on the order path.

    Order of checks is load-bearing: the declaration is tested FIRST, so an
    undeclared leg never even reads the policy and can never be reported as
    "evaluated, regime fine".
    """
    if not is_declared(strategy_cfg):
        return FlipExitVerdict(state=STATE_NOT_DECLARED)

    gated, cell = cell_is_off(
        policy=policy,
        regime=regime,
        strategy_key=strategy_key,
        direction=direction,
    )
    if not gated:
        return FlipExitVerdict(state=STATE_NO_FLIP, regime=regime, cell=cell)
    return FlipExitVerdict(
        state=STATE_FLIP,
        regime=regime,
        cell=cell,
        close_reason=f"regime_flip_{regime}",
    )
