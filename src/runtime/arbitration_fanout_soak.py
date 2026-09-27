"""The per-tick record of the PER-ACCOUNT ELECTION that routes (E35), v4 rows.

⚠️ **E35 (2026-09-25) CHANGED WHAT THIS FILE RECORDS, AND KEPT IT DELIBERATELY.**
The operator's order was to retire the fan-out writer unless E34's findings
said some part must stay. This part must, for two reasons: (1) it is the ONLY
durable per-tick record of what each account elected — a package row exists
only for a round that passed its gates, so without it a tick where an account
elected and was gated, or elected and had no dispatchable round, leaves no
trace; and (2) ``starved_account_alert`` reads it, and under v4 that alert
becomes the live tripwire for the property E35 builds (**no account that
elected a winner is left without a round**). What is RETIRED is everything
that made it a staging instrument: ``ARBITRATION_FANOUT_MODE`` (there is no
``off``/``annotate``/``apply`` any more — the per-account election is the
routing and is always recorded), ``ARBITRATION_FANOUT_ACCOUNTS`` (no allowlist
exists to scope anything), and the ``applied``/``rounds_written``/
``rounds_applied``/``apply_state``/``apply_scope`` fields. Both env names are
now IGNORED; :func:`record` warns once if either is still set, because a
leftover variable reading like a routing control is a doc describing a
mechanism that no longer exists.

v4 row, per ``(tick, symbol)`` worth recording:

  * ``per_account[a].state`` — graded against the PER-ACCOUNT election:
    ``routed`` (in a dispatch round), ``no_winner`` (its own election came out
    flat), ``starved`` (it ELECTED and is in NO round — structurally only an
    undispatchable winner; any non-zero count is a defect), ``unknown`` (its
    election raised, or the roster was unreadable).
  * ``global_would_drop`` — accounts ROUTED now that the pre-E35 global
    election would have dropped (they did not hold its one winner). This is
    the running version of ``scripts/research/e35_per_account_election_replay.py``.
  * ``rounds_planned`` — the rounds as planned (strategy, accounts, geometry).

A quiet tick — every candidate-holding account routed, and every one of them
held the global winner, so old and new routing coincide — writes no row.

---- the pre-E35 account (v1–v3), kept for readers of the accumulated log ----

Observe-only soak for the Lane P/P3 per-account arbitration fan-out.

At the shipped default (``annotate``) this writes one row per symbol-tick on
which at least one account held a candidate and did not get an order out of it —
whether because another account took the winner (``starved``, THE FINDING), or
because **nothing won the symbol at all** (``no_winner``), or because the winner
resolved to no account (``winner_unattributed``). Routing is byte-for-byte
unchanged; nothing reads these rows back.

⚠️ **THE THREE POPULATIONS ARE REPORTED SEPARATELY AND MUST NOT BE POOLED.**
Until 2026-08-30 a no-winner tick graded every candidate-holding account
``starved``, and on the whole live file to that point — n=9 rows, 15
account-gradings — that was **11 of the 13 starved gradings**, so the headline
``starved_count`` overstated the finding **6.5×** in the sole evidence base for
a Tier-3 routing change. A no-winner tick has no other account to have lost to;
its cause is upstream (every candidate held, gated or flat) and a per-account
fan-out is not its remedy.

⚠️ **A ``no_winner`` ROW IS STILL WRITTEN, DELIBERATELY.** It is the
DENOMINATOR: dropping it would leave a reader with only the finding and no way
to see how often the symbol had contenders and still routed nothing — the
unstated-denominator error this repo keeps paying for. ``fanout_schema`` marks
a row as post-split; **a row with no ``fanout_schema`` key is a pre-2026-08-30
row whose ``starved_accounts`` conflates both**, and pooling the two without
saying so re-creates the overstatement in the analysis instead of the code.

⚠️ ``ARBITRATION_FANOUT_ACCOUNTS`` — **AN EMPTY ALLOWLIST MEANS *NONE*,
deliberately the OPPOSITE of ``CONVICTION_SIZING_ACCOUNTS`` and
``NETTING_ATTRIBUTION_ACCOUNTS``.** Those widen a size and a DB write and read
empty as ALL, which ``CLAUDE.md`` already calls *"not a safe default, it is the
widest one"*. This one would arm a change to **which account an order routes
to**, so an unset variable must not arm it everywhere. It copies
``PROTECTION_REASSERT_ACCOUNTS`` / ``PROTECTION_STRAY_GROUP_ACCOUNTS`` polarity
on purpose; do not "harmonise" it back.

⚠️ **The allowlist scopes the BINDING, never the MEASUREMENT.** Every account is
assessed and annotated regardless, so the rows a reviewer needs before widening
actually exist — the correction ``NETTING_ATTRIBUTION_ACCOUNTS`` had to be given
on 2026-08-09, where intersecting the account set at the top of the pass made
the very account being staged toward invisible.

⚠️ ``apply`` IS IMPLEMENTED AS OF 2026-08-31 (Tier-3, operator-approved). This
docstring said the opposite for the whole life of the annotate-only build --
*"apply IS NOT IMPLEMENTED AND THE MODE DOES NOT PRETEND IT IS"* -- and that
sentence, plus the row's hardcoded ``apply_implemented: False``, would have
told a review session the capability does not exist. Do not re-quote it.

WHAT A REVIEW SESSION SHOULD LOOK FOR HERE, now that it does exist:

  * ``apply_implemented: true`` on every row -- a ``false`` means the deployed
    code predates the fan-out.
  * ``plan_state``: ``planned`` (the per-account election ran) vs ``absent``
    (**we did not look** -- the caller passed no plan; NOT "it elected
    nothing").
  * ``applied`` + ``rounds_applied``: the EFFECT -- what the DISPATCHER will
    act on, graded through the same ``accepted_rounds`` validator the pipeline
    uses. ``applied: false`` with a non-empty ``rounds_planned`` and an EMPTY
    ``rounds_written`` is the staged state: the fan-out decided and was held
    back by the allowlist, which is what ``apply_scope`` explains.
  * ``rounds_written`` + ``apply_state``: NEW IN v3 (2026-09-12), and the pair
    that makes the twelve-day failure sayable. ``rounds_written`` non-empty
    with ``rounds_applied`` EMPTY and ``apply_state:
    refused_by_dispatcher`` means the writer produced rounds the dispatcher
    throws away -- the fan-out is degrading silently to the global dispatch.
    ⚠️ **ON A v2 ROW (``fanout_schema: 2``) ``applied`` AND ``rounds_applied``
    ARE COSMETIC AND MUST NOT BE POOLED WITH v3.** ``applied`` was
    ``bool(apply_rounds)``, set from what the writer produced rather than from
    what the dispatcher accepted, so it read ``true`` on every armed tick while
    nothing dispatched -- MEASURED on the live log
    (``/api/diag/log_file?name=arbitration_fanout_soak``, read 2026-09-12):
    **93 of 93** ``rounds_applied`` entries carried exactly
    ``('accounts', 'strategy')``.
  * ``starved_count`` should FALL toward zero on symbols whose accounts are
    allowlisted, while ``no_winner_count`` is unaffected (a different
    population -- see the warning above and never pool them).
  * The end-to-end proof is not in this file: it is a starved account writing
    a JOURNAL ROW on a tick this log shows it was elected on. A soak row alone
    proves the decision, never the order.

Arming is TWO coordinated settings (``ARBITRATION_FANOUT_MODE=apply`` AND a
non-empty ``ARBITRATION_FANOUT_ACCOUNTS``); either alone routes nothing.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

_LOG_NAME = "arbitration_fanout_soak.jsonl"

#: Env names that USED to scope/arm the fan-out and are now ignored (E35).
RETIRED_ENV = ("ARBITRATION_FANOUT_MODE", "ARBITRATION_FANOUT_ACCOUNTS")
_retired_env_warned = False


def _warn_retired_env_once() -> None:
    global _retired_env_warned
    if _retired_env_warned:
        return
    _retired_env_warned = True
    present = [k for k in RETIRED_ENV if (os.environ.get(k) or "").strip()]
    if present:
        logger.warning(
            "%s set but IGNORED since E35 (2026-09-25): every account elects "
            "per account and there is no allowlist. Unset them so nothing "
            "reads as a routing control that is not one.", present,
        )


def _log_path() -> pathlib.Path:
    try:
        from src.utils.paths import runtime_logs_dir
        return pathlib.Path(runtime_logs_dir()) / _LOG_NAME
    except Exception:  # noqa: BLE001
        return pathlib.Path("runtime_logs") / _LOG_NAME


#: v4 plan state -> soak grade. `no_candidates` accounts are not graded (the
#: planner only lists accounts that hold a candidate).
_GRADE = {
    "elected": "routed",
    "elected_flat": "no_winner",
    "elected_undispatchable": "starved",
    "unknown": "unknown",
}


def grade_plan(plan: Optional[Mapping[str, Any]], global_verdict: Mapping[str, Any]
               ) -> Dict[str, Any]:
    """Grade each account against the per-account election. PURE.

    ``global_verdict`` is ``arbitration_fanout.assess`` over the same tick —
    the OLD routing — used only to name ``global_would_drop``.
    """
    routed_in_round = {
        str(a): str(r.get("strategy"))
        for r in ((plan or {}).get("rounds") or [])
        for a in (r.get("accounts") or ())
    }
    per_account: Dict[str, Dict[str, Any]] = {}
    for a, cell in ((plan or {}).get("per_account") or {}).items():
        state = _GRADE.get(str(cell.get("state")), "unknown")
        if state == "routed" and a not in routed_in_round:
            # Elected, yet in no round: the one thing the design forbids.
            state = "starved"
        per_account[str(a)] = {
            "candidates": list(cell.get("candidates") or []),
            "elected": cell.get("elected"),
            "state": state,
        }
    g_per = global_verdict.get("per_account") or {}
    global_would_drop = sorted(
        a for a, c in per_account.items()
        if c["state"] == "routed" and not (g_per.get(a) or {}).get("holds_winner")
    )

    def _in(state: str) -> list:
        return sorted(a for a, c in per_account.items() if c["state"] == state)

    return {
        "per_account": per_account,
        "starved_accounts": _in("starved"),
        "starved_count": len(_in("starved")),
        "no_winner_accounts": _in("no_winner"),
        "no_winner_count": len(_in("no_winner")),
        "unknown_accounts": _in("unknown"),
        "routed_accounts": _in("routed"),
        "accounts_graded": len(per_account),
        "global_would_drop": global_would_drop,
        "global_would_drop_count": len(global_would_drop),
    }


def record(
    candidate_strategies: Sequence[str],
    winning_strategy: Optional[str],
    *,
    symbol: str,
    accounts: Optional[Mapping[str, Mapping[str, Any]]] = None,
    plan: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Grade and append one v4 row. Returns the row, or ``None``.

    ``plan`` is the per-account election the tick ROUTED on
    (``intent_multiplexer._attach_account_election``). ``None`` means no plan
    was made — a flat headline (nothing to route) or the election was
    unavailable; the row then records ``plan_state: absent`` (*we did not look*)
    only when the roster itself was unreadable, and nothing on a flat tick.

    Best-effort throughout: this runs on the live tick, so **no failure here may
    reach the caller**. Silence costs an observation; a raise would cost a tick.
    """
    _warn_retired_env_once()
    try:
        from src.runtime.arbitration_fanout import FANOUT_SCHEMA, assess
        if accounts is None:
            from src.config.accounts_loader import load_accounts_dict
            accounts = load_accounts_dict()
        verdict = assess(candidate_strategies, winning_strategy, accounts=accounts)
        roster_read = (plan or {}).get("roster_state") == "read"
        if plan is None and verdict["roster_state"] == "read":
            return None  # flat headline: no account could elect
        graded = grade_plan(plan if roster_read else None, verdict)
        notable = (
            not roster_read
            or graded["global_would_drop"]
            or graded["starved_accounts"]
            or graded["no_winner_accounts"]
            or graded["unknown_accounts"]
            or len((plan or {}).get("rounds") or []) > 1
            or (plan or {}).get("double_round_accounts")
        )
        if not notable:
            return None
        row = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "fanout_schema": FANOUT_SCHEMA,
            "election": "per_account",
            # ⚠️ never collapsed: `absent`/`unreadable` = WE DID NOT LOOK.
            "plan_state": "planned" if roster_read else "absent",
            "plan_roster_state": (plan or {}).get("roster_state"),
            "rounds_planned": list((plan or {}).get("rounds") or []),
            "double_round_accounts": list(
                (plan or {}).get("double_round_accounts") or []),
            # STATE THE DENOMINATOR beside every count.
            "accounts_planned": (plan or {}).get("accounts_planned"),
            "accounts_elected": (plan or {}).get("accounts_elected"),
            # The OLD routing, for comparison only — it no longer decides.
            "winning_strategy": winning_strategy,
            "winner_accounts": verdict.get("winner_accounts"),
            "unattributed_strategies": verdict.get("unattributed_strategies"),
            **graded,
        }
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return row
    except Exception:  # noqa: BLE001 — observe-only must never break a tick
        logger.debug("arbitration_fanout_soak: record failed", exc_info=False)
        return None


__all__ = ["RETIRED_ENV", "grade_plan", "record"]
