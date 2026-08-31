"""Lane P/P3 — what per-ACCOUNT arbitration would change, measured, not assumed.

THE DEFECT. ``intents.aggregate_intents`` picks ONE winner per SYMBOL
**globally, before account fan-out**. Two accounts running the same strategy on
the same symbol therefore compete with each other, and the loser's account is
silently starved — it produces no order package at all, so it is invisible to
every per-account detector (that account places fine for its *other* legs) and
to the journal (it has no rows to grade).

MEASURED LIVE 2026-08-30, and this is why the module exists rather than a
backlog row: ``trend_donchian_sol`` (``bybit_1``) has emitted **144 actionable
buy signals since 08-01** and written **zero** journal rows on that account; its
newest row of any kind is 2026-06-29. It loses every tick to
``trend_donchian_sol_prop`` (``breakout_1``) — the *same* 1h Donchian on
SOLUSDT, routed to a different account. Over the whole ``allocator_soak``
(n=277): **113 of 137 disagreements (82.5%)** are this shape — ETH donchian 56,
SOL donchian 34, ETH pullback 23 — against only 24 genuine cross-strategy
contests.

⚠️ **THE COLLISION IS ACCOUNT-BLIND, NOT PROP-BIASED.** The prop leg wins both
donchian pairs and **loses** ETH pullback (``eth_pullback_prop_2h`` is the
allocator's choice; ``eth_pullback_2h`` is routed). Do not write, and do not
infer, a funding-class rule here.

WHAT THIS MEASURES AT ``annotate``, STATED PRECISELY BECAUSE IT IS NARROWER THAN
THE MODULE NAME SUGGESTS. It reports **starvation** — which accounts held a
candidate this tick and did not get the winner — NOT which candidate each
account would elect under a real fan-out. Electing a per-account winner means
re-running ``aggregate_intents`` on each account's subset, and that would
re-enter ``_hard_regime_gate``, **re-emitting a ``regime_hard_gate`` audit row
per account per tick**. Those rows are the only thing that cleanly partitions
"would have gated" from "did gate" in the audit log, so duplicating them would
corrupt the very evidence this lane depends on. Starvation is side-effect-free,
needs no second copy of the winner rule, and is sufficient to size the change:
an account that is never starved gains nothing from fanning out.

⚠️ **``starved`` MEANS *ANOTHER ACCOUNT TOOK THE WINNER FROM ME*, AND NOTHING
WIDER.** A tick on which NO strategy won the symbol at all is graded
``no_winner`` and reported as its own population — it has no other account to
have lost to, and its cause is upstream (candidates held, gated or flat) where
fanning arbitration out is not the remedy. Until 2026-08-30 those ticks were
graded ``starved``: on the whole live file (n=9 rows, 15 account-gradings) that
was **11 of the 13 starved gradings**, overstating the finding 6.5× in the sole
evidence base for the Tier-3 change. Do not re-merge the two.

⚠️ **SO DO NOT READ A ``starved`` ROW AS "THIS ACCOUNT WOULD HAVE TRADED".** It
would have had a *contest of its own*; whether its candidate then survives its
own regime gate and conflict resolution is unmeasured here, and asserting it
would be exactly the unprovenanced inference this repo keeps paying for.

Observe-only. This module reads no socket, opens no DB, places nothing, and
cannot refuse a trade. The remedy it exists to size — actually fanning
arbitration out per ``(account, symbol)`` — is **Tier-3** and stays behind the
operator's flip.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Per-account outcomes for one symbol on one tick. Never collapsed — each
#: routes to a different reader:
#:   * ``routed``       — this account holds the global winner. Fanning out
#:                        changes nothing for it this tick.
#:   * ``starved``      — THE FINDING. It held ≥1 candidate, a winner exists,
#:                        and the winner belongs to a DIFFERENT account — so it
#:                        produces no order package today purely because of the
#:                        global scope. It would have had a contest of its own.
#:   * ``no_winner``    — it held ≥1 candidate and **nothing won the symbol at
#:                        all** this tick. NOT starvation: no other account took
#:                        anything from it, so a per-account fan-out cannot be
#:                        credited with changing this row. See the ⚠️ below.
#:   * ``winner_unattributed`` — a winner exists but resolves to NO account in
#:                        the roster, so we cannot say this account lost to
#:                        another one. Either a roster gap or a strategy that
#:                        should not be winning; both are findings, neither is
#:                        starvation. (Never observed live as of 2026-08-30 —
#:                        it is representable, and before this state existed it
#:                        graded ``starved``.)
#:   * ``no_candidates`` — it held none. Not health, not a finding: there was
#:                        nothing to arbitrate, and reading this as "fan-out
#:                        would not help" over a quiet window is the
#:                        unstated-denominator error.
#:   * ``unknown``      — *we could not look*: the account roster was
#:                        unreadable, the winner's account could not be
#:                        resolved, or a count/scope we cannot read. Emphatically
#:                        NOT ``no_candidates``.
#:
#: ⚠️ **``no_winner`` WAS GRADED ``starved`` UNTIL 2026-08-30 AND THAT INFLATED
#: THE ONLY EVIDENCE THIS LANE HAS.** The original reasoning — *"nothing routed
#: at all while strategies were asking to, so that IS starvation"* — describes a
#: real condition, but not the one this soak exists to size. Starvation here
#: means **another account took the winner from me**; a tick with no winner has
#: no such other account, and its cause lives upstream (every candidate held,
#: gated, or flat), where fanning arbitration out per account is not the remedy.
#: Measured on the whole live file the day the soak shipped (n=9 rows,
#: 2026-08-30T14:25Z→19:03Z, 15 account-gradings): **13 graded ``starved``, of
#: which 11 were no-winner ticks and only 2 were the finding** — the headline
#: overstated it **6.5×**. The two populations are per-ROW disjoint (a winner
#: either exists or does not), so they separate cleanly and neither is dropped.
FANOUT_STATES = (
    "routed",
    "starved",
    "no_winner",
    "winner_unattributed",
    "no_candidates",
    "unknown",
)

#: Whether a winner exists this tick and whether it is attributable to an
#: account. Decided ONCE per symbol-tick in :func:`assess` and passed down, so
#: every account on a tick is graded against the same reading.
WINNER_SCOPES = ("attributed", "no_winner", "unattributed")

#: Bumped when the row shape changes in a way a reader of the accumulated log
#: must branch on. **A row with no ``fanout_schema`` key is a pre-2026-08-30
#: row and its ``starved_accounts`` CONFLATES starvation with no-winner ticks**
#: — do not pool it with a v2 row's ``starved_count`` without saying so.
FANOUT_SCHEMA = 2


def accounts_by_strategy(
    accounts: Optional[Mapping[str, Mapping[str, Any]]],
) -> Optional[Dict[str, Tuple[str, ...]]]:
    """``{strategy_name: (account_id, ...)}`` from the accounts roster.

    Returns ``None`` — *we could not look* — when the roster is missing or
    unreadable, so the caller renders ``unknown`` rather than an empty map that
    would grade every account ``no_candidates`` and report a clean negative on a
    file nobody read. That distinction is the whole point: a config-load failure
    and a genuinely quiet tick are opposite facts.
    """
    if not accounts:
        return None
    out: Dict[str, list] = {}
    try:
        for account_id, cfg in accounts.items():
            for strategy in ((cfg or {}).get("strategies") or []):
                out.setdefault(str(strategy), []).append(str(account_id))
    except Exception:  # noqa: BLE001 — an unreadable roster is "we did not look"
        logger.debug("arbitration_fanout: roster unreadable", exc_info=False)
        return None
    return {k: tuple(v) for k, v in out.items()}


def winner_scope_for(
    winning_strategy: Optional[str],
    winner_accounts: Any,
) -> str:
    """Grade the TICK's winner before any account is graded. See :data:`WINNER_SCOPES`.

    Three readings, never collapsed, because each sends the reader somewhere
    different: ``attributed`` (a contest happened and somebody won it — the only
    reading under which another account can have been starved), ``no_winner``
    (nothing won, so there is no other account to have lost to), and
    ``unattributed`` (something won but no account claims it — a roster gap, not
    a routing loss).
    """
    if not winning_strategy:
        return "no_winner"
    return "attributed" if winner_accounts else "unattributed"


def fanout_state_for(
    account_candidate_count: Any,
    holds_winner: Any,
    *,
    roster_known: bool = True,
    winner_scope: str = "attributed",
) -> str:
    """Grade ONE account for one symbol on one tick. See :data:`FANOUT_STATES`."""
    if not roster_known or holds_winner is None:
        return "unknown"
    if winner_scope not in WINNER_SCOPES:
        # A scope we cannot read is not a scope of "attributed". Defaulting the
        # other way would silently promote an unreadable tick into the finding,
        # which is the direction this module was just corrected for.
        return "unknown"
    try:
        n = int(account_candidate_count or 0)
    except (TypeError, ValueError):
        return "unknown"  # a count we cannot read is not a count of zero
    if n <= 0:
        return "no_candidates"
    if holds_winner:
        return "routed"
    if winner_scope == "no_winner":
        return "no_winner"
    if winner_scope == "unattributed":
        return "winner_unattributed"
    return "starved"


def assess(
    candidate_strategies: Sequence[str],
    winning_strategy: Optional[str],
    *,
    accounts: Optional[Mapping[str, Mapping[str, Any]]],
) -> Dict[str, Any]:
    """What per-account arbitration would change for one symbol on one tick.

    PURE. No I/O, no audit emission, no order path — so the policy is arguable
    in tests rather than against a live position, which is the lesson of
    ``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``.

    ⚠️ **``starved_count`` COUNTS ONLY ACCOUNTS THAT LOST TO ANOTHER ACCOUNT.**
    A tick where ``winning_strategy`` is ``None`` — nothing won the symbol at
    all — puts every candidate-holding account in ``no_winner_accounts``, a
    SEPARATE population reported beside it, because a fan-out has no other
    account to take the winner from and so cannot be credited with that row.
    Until 2026-08-30 those rows were counted as starvation and 11 of the live
    file's 13 starved gradings were them; see :data:`FANOUT_STATES`.

    ⚠️ **READ ``accounts_graded`` BESIDE ANY COUNT.** A short starved list over
    a tiny denominator is not a clean bill of health, and the three populations
    (``starved`` / ``no_winner`` / ``winner_unattributed``) are mutually
    exclusive per row but only sum to ``accounts_graded`` together with
    ``routed``.
    """
    by_strategy = accounts_by_strategy(accounts)
    roster_known = by_strategy is not None
    by_strategy = by_strategy or {}

    winner_accounts = set(by_strategy.get(winning_strategy or "", ()))
    # Decided ONCE for the tick, so every account on it is graded against the
    # same reading of the winner rather than each re-deriving it.
    scope = winner_scope_for(winning_strategy, winner_accounts)
    per_account: Dict[str, Dict[str, Any]] = {}
    unattributed: list = []

    for strategy in candidate_strategies:
        accts = by_strategy.get(str(strategy))
        if not accts:
            # A candidate whose strategy maps to no account. Recorded, never
            # silently dropped — it is either a roster gap or a strategy that
            # should not be emitting, and both are findings. When the WINNER is
            # the unmapped one it also lands here, which is the cross-check on
            # the `winner_unattributed` grade below.
            unattributed.append(str(strategy))
            continue
        for a in accts:
            row = per_account.setdefault(a, {"candidates": [], "state": "unknown"})
            row["candidates"].append(str(strategy))

    for account_id, row in per_account.items():
        row["state"] = fanout_state_for(
            len(row["candidates"]),
            (account_id in winner_accounts) if roster_known else None,
            roster_known=roster_known,
            winner_scope=scope,
        )
        row["holds_winner"] = account_id in winner_accounts if roster_known else None

    def _in(state: str) -> list:
        return sorted(a for a, r in per_account.items() if r["state"] == state)

    starved = _in("starved")
    no_winner = _in("no_winner")
    winner_unattributed = _in("winner_unattributed")
    return {
        "fanout_schema": FANOUT_SCHEMA,
        "roster_state": "read" if roster_known else "unreadable",
        "winning_strategy": winning_strategy,
        "winner_scope": scope,
        "winner_accounts": sorted(winner_accounts),
        "per_account": per_account,
        # THE FINDING — a winner existed and belonged to someone else.
        "starved_accounts": starved,
        "starved_count": len(starved),
        # NOT the finding, reported beside it rather than folded into it or
        # dropped: dropping it would remove the denominator that says how often
        # the symbol had contenders and still routed nothing.
        "no_winner_accounts": no_winner,
        "no_winner_count": len(no_winner),
        "winner_unattributed_accounts": winner_unattributed,
        "winner_unattributed_count": len(winner_unattributed),
        # STATE THE DENOMINATOR: how many accounts this tick could be graded at
        # all. A short starved list over a tiny denominator is not a clean
        # bill of health.
        "accounts_graded": len(per_account),
        "unattributed_strategies": sorted(set(unattributed)),
    }


PLAN_STATES = (
    "elected",         # this account elected a live winner from its OWN candidates
    "elected_flat",    # it has candidates, but its own election came out flat
    "no_candidates",   # it runs none of this tick's candidates — correctly silent
    "unknown",         # roster unreadable — we could not look
)


def plan_per_account_election(
    candidates: Sequence[Any],
    *,
    accounts: Optional[Mapping[str, Mapping[str, Any]]],
    elect_fn,
    intents_before_gate: Optional[int] = None,
) -> Dict[str, Any]:
    """Elect a winner PER ACCOUNT from one ALREADY-GATED candidate set.

    PURE, like :func:`assess` — no I/O, no audit emission, no order path. The
    election is **injected** (``elect_fn``) rather than imported, so this module
    stays free of order-path imports and the policy is arguable in tests rather
    than against a live position
    (``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``).

    ⚠️ ``candidates`` MUST already have been through ``intents.gate_intents``.
    ``elect_fn`` is ``intents.elect_from_gated``, which deliberately does NOT
    re-gate — passing un-gated intents here elects over candidates the regime
    router would have refused. Gating once and electing N times is the entire
    reason that split exists: re-running the aggregator per account re-emits a
    ``regime_hard_gate`` row per account per tick, corrupting the one signal
    that partitions "would have gated" from "did gate".

    ⚠️ **AN ACCOUNT ONLY EVER ELECTS FROM STRATEGIES IT DECLARES.** The whole
    defect being fixed is a strategy reaching an account that never asked for
    it; a planner that could route one would be a worse version of the same
    bug. The invariant is asserted below, not merely intended.

    ⚠️ **``no_candidates`` IS NOT A FAILURE.** An account that runs none of this
    tick's candidates is correctly silent, and folding it in with the accounts
    that DID contend would restate the unstated-denominator error this module
    was rewritten to remove. Read ``accounts_planned`` beside any count.

    Returns ``rounds`` — the dispatch plan, one entry per DISTINCT elected
    strategy with the accounts that elected it. Accounts electing the same
    strategy share a round so the fan-out is one dispatch per package, not one
    per account.
    """
    by_strategy = accounts_by_strategy(accounts)
    roster_known = by_strategy is not None
    by_strategy = by_strategy or {}

    # strategy -> the candidate object, so a round can carry real geometry.
    by_name: Dict[str, Any] = {}
    for cand in candidates or ():
        name = str(getattr(cand, "strategy", "") or "")
        if name:
            by_name.setdefault(name, cand)

    per_account: Dict[str, Dict[str, Any]] = {}
    for strategy, accts in by_strategy.items():
        if strategy not in by_name:
            continue
        for account_id in accts:
            row = per_account.setdefault(
                str(account_id), {"candidates": [], "elected": None, "state": "unknown"}
            )
            row["candidates"].append(str(strategy))

    if not roster_known:
        # We could not look. Emphatically NOT "no account had candidates".
        return {
            "fanout_schema": FANOUT_SCHEMA,
            "roster_state": "unreadable",
            "per_account": {},
            "rounds": [],
            "accounts_planned": 0,
            "accounts_elected": 0,
        }

    rounds_by_strategy: Dict[str, list] = {}
    for account_id, row in per_account.items():
        own = tuple(by_name[s] for s in row["candidates"])
        if not own:
            row["state"] = "no_candidates"
            continue
        try:
            desired = elect_fn(
                own, symbol=_symbol_of(own), intents_before_gate=intents_before_gate
            )
        except Exception:  # noqa: BLE001 — a planner failure must never strand a tick
            logger.debug(
                "arbitration_fanout: per-account election failed for %s",
                account_id, exc_info=False,
            )
            row["state"] = "unknown"
            continue
        winner = getattr(desired, "winning_intent", None)
        side = str(getattr(desired, "side", "flat") or "flat")
        if winner is None or side == "flat":
            row["state"] = "elected_flat"
            continue
        elected = str(getattr(winner, "strategy", "") or "")
        # THE INVARIANT: never route a strategy to an account that does not
        # declare it. Asserted, not assumed.
        if elected not in row["candidates"]:
            logger.warning(
                "arbitration_fanout: election returned %r for %s, which declares "
                "%s — refusing to plan it",
                elected, account_id, row["candidates"],
            )
            row["state"] = "unknown"
            continue
        row["elected"] = elected
        row["state"] = "elected"
        rounds_by_strategy.setdefault(elected, []).append(str(account_id))

    # Each round carries the ELECTED strategy's OWN geometry. It must not
    # inherit the global winner's entry/sl/tp — that would place a different
    # strategy's trade under this strategy's name, which is a worse defect
    # than the starvation being fixed. A candidate missing any leg of its
    # geometry is DROPPED from the plan rather than defaulted: a fabricated
    # stop is not a stop.
    rounds = []
    for strategy, accts in sorted(rounds_by_strategy.items()):
        cand = by_name.get(strategy)
        entry = getattr(cand, "entry", None)
        sl = getattr(cand, "sl", None)
        tp = getattr(cand, "tp", None)
        side = str(getattr(cand, "side", "") or "")
        if entry is None or sl is None or tp is None or side not in ("long", "short"):
            logger.warning(
                "arbitration_fanout: %s elected by %s but has incomplete "
                "geometry (side=%r entry=%r sl=%r tp=%r) — dropping the round",
                strategy, sorted(accts), side, entry, sl, tp,
            )
            for account_id in accts:
                per_account[account_id]["state"] = "unknown"
                per_account[account_id]["elected"] = None
            continue
        rounds.append({
            "strategy": strategy,
            "accounts": sorted(accts),
            "side": side,
            "entry": float(entry),
            "sl": float(sl),
            "tp": float(tp),
            "confidence": float(getattr(cand, "confidence", 0.0) or 0.0),
        })
    return {
        "fanout_schema": FANOUT_SCHEMA,
        "roster_state": "read",
        "per_account": per_account,
        "rounds": rounds,
        # STATE THE DENOMINATOR: how many accounts were considered at all, vs
        # how many actually came out with something to place.
        "accounts_planned": len(per_account),
        "accounts_elected": sum(
            1 for r in per_account.values() if r["state"] == "elected"
        ),
    }


def _symbol_of(candidates: Sequence[Any]) -> str:
    for cand in candidates or ():
        sym = str(getattr(cand, "symbol", "") or "")
        if sym:
            return sym
    return "BTCUSDT"


__all__ = [
    "FANOUT_STATES",
    "WINNER_SCOPES",
    "FANOUT_SCHEMA",
    "PLAN_STATES",
    "accounts_by_strategy",
    "winner_scope_for",
    "fanout_state_for",
    "assess",
    "plan_per_account_election",
]
