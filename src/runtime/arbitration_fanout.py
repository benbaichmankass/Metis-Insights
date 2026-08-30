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
#:   * ``starved``      — THE FINDING. It held ≥1 candidate and the winner
#:                        belongs to a DIFFERENT account, so it produces no
#:                        order package today purely because of the global
#:                        scope. It would have had a contest of its own.
#:   * ``no_candidates`` — it held none. Not health, not a finding: there was
#:                        nothing to arbitrate, and reading this as "fan-out
#:                        would not help" over a quiet window is the
#:                        unstated-denominator error.
#:   * ``unknown``      — *we could not look*: the account roster was
#:                        unreadable, or the winner's account could not be
#:                        resolved. Emphatically NOT ``no_candidates``.
FANOUT_STATES = ("routed", "starved", "no_candidates", "unknown")


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


def fanout_state_for(
    account_candidate_count: Any,
    holds_winner: Any,
    *,
    roster_known: bool = True,
) -> str:
    """Grade ONE account for one symbol on one tick. See :data:`FANOUT_STATES`."""
    if not roster_known or holds_winner is None:
        return "unknown"
    try:
        n = int(account_candidate_count or 0)
    except (TypeError, ValueError):
        return "unknown"  # a count we cannot read is not a count of zero
    if n <= 0:
        return "no_candidates"
    return "routed" if holds_winner else "starved"


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

    ``winning_strategy`` is ``None`` on a flat tick; every account with
    candidates is then ``starved`` — correctly, because nothing routed at all
    while strategies were asking to.
    """
    by_strategy = accounts_by_strategy(accounts)
    roster_known = by_strategy is not None
    by_strategy = by_strategy or {}

    winner_accounts = set(by_strategy.get(winning_strategy or "", ()))
    per_account: Dict[str, Dict[str, Any]] = {}
    unattributed: list = []

    for strategy in candidate_strategies:
        accts = by_strategy.get(str(strategy))
        if not accts:
            # A candidate whose strategy maps to no account. Recorded, never
            # silently dropped — it is either a roster gap or a strategy that
            # should not be emitting, and both are findings.
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
        )
        row["holds_winner"] = account_id in winner_accounts if roster_known else None

    starved = sorted(a for a, r in per_account.items() if r["state"] == "starved")
    return {
        "roster_state": "read" if roster_known else "unreadable",
        "winning_strategy": winning_strategy,
        "winner_accounts": sorted(winner_accounts),
        "per_account": per_account,
        "starved_accounts": starved,
        "starved_count": len(starved),
        # STATE THE DENOMINATOR: how many accounts this tick could be graded at
        # all. A short starved list over a tiny denominator is not a clean
        # bill of health.
        "accounts_graded": len(per_account),
        "unattributed_strategies": sorted(set(unattributed)),
    }


__all__ = ["FANOUT_STATES", "accounts_by_strategy", "fanout_state_for", "assess"]
