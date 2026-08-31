"""Observe-only soak for the Lane P/P3 per-account arbitration fan-out.

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
  * ``applied`` + ``rounds_applied``: the EFFECT. ``applied: false`` with a
    non-empty ``rounds_planned`` is the staged state -- the fan-out decided and
    was held back by the allowlist, which is what ``apply_scope`` explains.
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

_MODES = ("off", "annotate", "apply")
_LOG_NAME = "arbitration_fanout_soak.jsonl"


def resolve_mode() -> str:
    """``off`` | ``annotate`` (default) | ``apply``.

    An unparseable or unknown value falls back to **``annotate``**, never to
    ``off`` and never to ``apply``: a typo must not silently switch the
    observation off, and certainly must not switch an order path on.

    ``apply`` is accepted by the parser but **refused at the call site** — see
    the module docstring. It is spelled here so the vocabulary matches its
    siblings and so a future Tier-3 change has a name to flip, not because it
    does anything today.
    """
    raw = (os.environ.get("ARBITRATION_FANOUT_MODE") or "").strip().lower()
    if raw in _MODES:
        return raw
    if raw:
        logger.warning(
            "ARBITRATION_FANOUT_MODE=%r is not one of %s — falling back to "
            "'annotate' (a typo must not disable the observation)", raw, _MODES,
        )
    return "annotate"


def allowlisted_accounts() -> frozenset:
    """Accounts an ``apply`` would be permitted to bind. EMPTY MEANS NONE."""
    raw = (os.environ.get("ARBITRATION_FANOUT_ACCOUNTS") or "").strip()
    if not raw:
        return frozenset()
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def apply_scope_for(account_id: str, mode: str) -> str:
    """Why an account's EFFECTIVE outcome differs from the requested ``mode``.

    Three states, never collapsed, so a held-back row can never read as an
    applied one — the distinction ``NETTING_ATTRIBUTION_MODE`` had to be
    corrected to make.
    """
    if mode != "apply":
        return "not_apply"
    return "allowlisted" if account_id in allowlisted_accounts() else "not_allowlisted"


def _log_path() -> pathlib.Path:
    try:
        from src.utils.paths import runtime_logs_dir
        return pathlib.Path(runtime_logs_dir()) / _LOG_NAME
    except Exception:  # noqa: BLE001
        return pathlib.Path("runtime_logs") / _LOG_NAME


def record(
    candidate_strategies: Sequence[str],
    winning_strategy: Optional[str],
    *,
    symbol: str,
    accounts: Optional[Mapping[str, Mapping[str, Any]]] = None,
    plan: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Assess and append one row. Returns the row, or ``None``.

    ``plan`` is the per-account election from
    ``arbitration_fanout.plan_per_account_election``. **Pass it whenever the
    caller computed one** — without it this row records only what the GLOBAL
    election would have starved, which is what the fan-out was built to fix and
    says nothing about whether the fix RAN.

    ⚠️ THIS ROW IS THE ONLY DURABLE RECORD THAT THE FAN-OUT ACTED. Until
    2026-08-31 it hardcoded ``mode: "annotate"`` and ``apply_implemented:
    False``, which was true when written and became a LIE the moment the apply
    path shipped: a review session reading the log would have concluded the
    fan-out was never built. That is this repo's signature failure — a
    mechanism ships and nothing reads it back
    (``provenance-consumer-guard``'s whole reason for existing) — and it is
    worse here than a missing field, because the field was present and
    confidently wrong.

    Best-effort throughout: this runs on the live tick, so **no failure here may
    reach the caller**. Silence costs an observation; a raise would cost a tick.
    """
    mode = resolve_mode()
    if mode == "off":
        return None
    try:
        from src.runtime.arbitration_fanout import assess
        if accounts is None:
            from src.config.accounts_loader import load_accounts_dict
            accounts = load_accounts_dict()
        verdict = assess(candidate_strategies, winning_strategy, accounts=accounts)
        # A tick where every candidate-holding account got the winner anyway is
        # the ordinary case and would bury the finding under noise. Everything
        # else is worth a row — INCLUDING a no-winner tick, which is not the
        # finding but IS its denominator (see the module docstring).
        # A tick the fan-out ACTED on is always notable, whatever the global
        # election would have done. Without this an applied round could be
        # dropped by the quiet-tick gate, and the one durable record that the
        # mechanism ran would be silently incomplete — exactly the shape of
        # under-reporting this module already carries two warnings about.
        notable = (
            bool((plan or {}).get("apply_rounds"))
            or verdict["starved_accounts"]
            or verdict["no_winner_accounts"]
            or verdict["winner_unattributed_accounts"]
        )
        if not notable and verdict["roster_state"] == "read":
            return None
        # WHAT THE FAN-OUT ACTUALLY DID, never inferred from the mode.
        # `applied` is the EFFECT (rounds reached the dispatcher); `global_mode`
        # is the REQUEST; `apply_scope` says why they differ. A held-back row
        # can therefore never read as an applied one — the distinction
        # NETTING_ATTRIBUTION_MODE had to be corrected into existence on
        # 2026-08-09.
        rounds = list((plan or {}).get("apply_rounds") or [])
        applied = bool(rounds)
        row = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            # The EFFECTIVE outcome beside what was REQUESTED, so the two can
            # never be conflated by a reader.
            "mode": "apply" if applied else "annotate",
            "global_mode": mode,
            # TRUE since 2026-08-31. It was hardcoded False for the whole life
            # of the annotate-only build and would have kept asserting the
            # capability does not exist.
            "apply_implemented": True,
            # ⚠️ `plan_state` is never collapsed. `absent` means the caller
            # passed no plan — WE DID NOT LOOK — and is emphatically NOT
            # "the fan-out elected nothing"; `planned` means it ran.
            "plan_state": "planned" if plan else "absent",
            "applied": applied,
            "rounds_applied": rounds,
            "rounds_planned": list((plan or {}).get("rounds") or []),
            # STATE THE DENOMINATOR: how many accounts the planner considered
            # at all, beside how many came out with something to place.
            "accounts_planned": (plan or {}).get("accounts_planned"),
            "accounts_elected": (plan or {}).get("accounts_elected"),
            "plan_roster_state": (plan or {}).get("roster_state"),
            "elected_by_account": {
                a: r.get("elected")
                for a, r in ((plan or {}).get("per_account") or {}).items()
            },
            # Keyed on the STARVED set only: those are the accounts whose
            # routing a real fan-out would change. A no-winner account is not
            # one of them, so listing it here would re-imply the very
            # conflation this row's schema exists to undo.
            "apply_scope": {
                a: apply_scope_for(a, mode) for a in verdict["starved_accounts"]
            },
            **verdict,
        }
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return row
    except Exception:  # noqa: BLE001 — observe-only must never break a tick
        logger.debug("arbitration_fanout_soak: record failed", exc_info=False)
        return None


__all__ = ["resolve_mode", "allowlisted_accounts", "apply_scope_for", "record"]
