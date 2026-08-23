"""Per-ACCOUNT directional gate — ``accounts.yaml::side_filter``.

Why this exists
---------------
``config/strategies.yaml`` already carries ``side_filter: long|short|both``
(legacy ``long_only: true``), resolved by
:func:`src.runtime.strategy_signal_builders._resolve_side_filter` and enforced
in the SIGNAL BUILDER. That key is keyed on the STRATEGY, and a strategy routes
to many accounts — so it cannot express *"long-only on the real-money book,
both sides on the paper soak book"*.

Measured on 2026-08-23 (``BL-20260823-ALPACA-LONG-ONLY-CANNOT-BE-SCOPED-TO-ONE-ACCOUNT``):
all 11 two-sided legs ``alpaca_live`` routes ALSO route to ``alpaca_paper``
(the ML soak book), 9 to ``alpaca_portfolio``, one to ``ib_paper``. Declaring
``side_filter: long`` on those strategies would have permanently stopped the
soak accruing the short-side data that GRADES the long-only decision — turning
off the measurement that would tell you whether the policy was right.

So the account is where this belongs. Operator disposition, 2026-08-23:
*"alpaca long only for real money, everything else stays the same."*

⚠️ THE SUPPRESSION PREDICATE IS NOT RE-DERIVED HERE
---------------------------------------------------
``_side_filter_suppresses`` is imported from ``strategy_signal_builders``. A
second copy of *"does this filter suppress this direction?"* is free to drift
from the first, and the two would then disagree about a live order. One module
owns the question; this module only answers *which filter applies to which
account*.

Four states, never collapsed
----------------------------
``long`` · ``short`` · ``both`` (declared, or the permissive default) ·
``unknown`` — **we could not look**: ``accounts.yaml`` was unreadable, or the
account id is not declared in it. ``unknown`` is NOT ``both``; it is reported
distinctly so a caller can log it, even though both PLACE the order.

Fail-permissive, deliberately. An unreadable ``accounts.yaml`` or an
unrecognised value never strands a side — it degrades to two-sided and warns,
the same discipline ``_resolve_side_filter`` uses for a bad value and
``_regime_router_active`` uses for a policy-load failure. This is a POLICY
preference, not a safety interlock: the safety interlocks (account ``mode:``,
strategy ``execution:``) are unaffected and still decide whether anything is
sent at all.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: The resolved states. ``unknown`` means *we did not look*, never *both*.
SIDE_FILTER_STATES = ("long", "short", "both", "unknown")

_VALID_DECLARED = ("long", "short", "both")


def account_side_filter(account_id: str) -> str:
    """Resolve *account_id*'s directional gate.

    Returns one of :data:`SIDE_FILTER_STATES`. Reads ``config/accounts.yaml``
    through the canonical :func:`src.config.accounts_loader.load_accounts_dict`
    — never a second bespoke YAML read.
    """
    if not account_id:
        return "unknown"
    try:
        from src.config.accounts_loader import load_accounts_dict
        accounts = load_accounts_dict() or {}
    except Exception as exc:  # noqa: BLE001
        # We could not look. Distinct from "declared both".
        logger.warning(
            "account_side_filter: accounts.yaml load failed (%s) — "
            "treating %s as ungated", exc, account_id,
        )
        return "unknown"

    cfg = accounts.get(account_id)
    if not isinstance(cfg, dict):
        logger.debug(
            "account_side_filter: %s not declared in accounts.yaml", account_id,
        )
        return "unknown"

    raw = cfg.get("side_filter")
    if raw is None:
        # Declared account, no directional policy — the permissive default.
        return "both"
    if isinstance(raw, str):
        val = raw.strip().lower()
        if val in _VALID_DECLARED:
            return val
    logger.warning(
        "account_side_filter: %s side_filter=%r unrecognised "
        "(expected long|short|both) — falling back to two-sided",
        account_id, raw,
    )
    return "both"


def account_suppresses_direction(
    account_id: str, direction: Optional[str]
) -> tuple[bool, str]:
    """``(suppressed, resolved_filter)`` for *direction* on *account_id*.

    *direction* is the order package's ``"long"``/``"short"``. A missing or
    unrecognised direction is never suppressed — refusing an order because we
    could not read its side would be a gate acting on absence of evidence.
    """
    resolved = account_side_filter(account_id)
    if resolved in ("both", "unknown"):
        return False, resolved
    # An empty or unrecognised direction is UNREAD, not "the other side".
    # ``""`` is a str and would sail past an isinstance check, then compare
    # unequal to "long" and suppress — refusing an order on absence of
    # evidence. The membership test is the check that matters, not the type.
    norm = direction.strip().lower() if isinstance(direction, str) else None
    if norm not in ("long", "short"):
        logger.warning(
            "account_side_filter: %s has side_filter=%s but direction is %r "
            "— not suppressing (cannot gate on an unread side)",
            account_id, resolved, direction,
        )
        return False, resolved
    # One module owns "does this filter suppress this direction" — do not
    # re-derive it here (a second copy is free to drift from the enforcing one).
    from src.runtime.strategy_signal_builders import _side_filter_suppresses
    return bool(_side_filter_suppresses(norm, resolved)), resolved
