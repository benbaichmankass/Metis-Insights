"""Does this prop ticket's risk fit inside the account's remaining cushion?

WHY THIS EXISTS
---------------
Measured live 2026-08-25 (``/system-review``, report
``RPT-20260825-092500-since-last``): ``breakout_1`` sat **$64.00** above its
**$4,700** static-drawdown floor, and the ticket the bot emitted at
``2026-08-25T00:15:23Z`` (``prop-manual-1a29db54154e``, 17.305 SOL) suggested
**``risk_usd: 75.00``** — *more than the entire remaining cushion*. Its own
"RECOMPUTE at 1.5% of your live balance" instruction yields **$71.46**, which
also overshoots. Placing that ticket at either size and losing would have
breached the floor, which **permanently disables the account**.

The cushion was not unknown to the system. ``prop_reconcile.compute_rule_distance``
had already computed ``distance_to_dd_floor_usd: 64.0`` and served it on
``/api/bot/prop/status``. It has **three consumers and all three are DISPLAY** —
``web/api/routers/prop.py``, ``prop/telegram_report_handler.py``,
``prop/prop_report.py``. **Zero decision consumers.** Meanwhile
``breakout_ticket.render_ticket`` computes ``dd_floor = 0.06 *
account_size_usd`` and uses it only to *print* the rule as static text.

So the emitter and the safety panel never spoke, and the number that exists
precisely to prevent this had no path to the decision. That is the same
written-and-never-read class ``provenance-consumer-guard`` polices for
provenance keys — which does not cover safety measurements. This module is the
missing consumer.

⚠️ **STALENESS IS NOT THE WHOLE DEFECT, AND FIXING IT ALONE WOULD NOT HELP.**
The snapshot behind that $64 was also 40.7 h old, and it is tempting to file
this as a freshness bug. It is not: a perfectly **fresh** $4,764 snapshot still
produces a $75 suggestion against a $64 cushion, because nothing compares the
two. Freshness and this gate fix different halves; both are needed.

FOUR STATES, NEVER COLLAPSED
----------------------------
(``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states".) A gate on a money
path must distinguish *we could not look* from *we looked and it fits* — they
call for opposite operator actions, and on this account the wrong one is
terminal:

  ``within_cushion``    — both known; the risk fits with room to spare.
  ``exceeds_cushion``   — both known; the risk is at or past the binding limit.
  ``cushion_unknown``   — **we could not look.** No snapshot, an unreadable one,
                          or — the case that actually fired — a snapshot too
                          STALE to be a live cushion. Emphatically NOT
                          ``within_cushion``.
  ``no_risk_declared``  — the ticket carries no ``risk_usd`` (a suppressed or
                          shadow ticket). There is nothing to grade.

⚠️ **A STALE CUSHION IS ``cushion_unknown``, NOT A CUSHION.** This is the whole
reason the module reads ``status_freshness`` rather than just the distance. A
40.7 h-old $64 describes an account state long gone; grading it ``within`` or
``exceeds`` would assert a fact nobody measured. It grades ``cushion_unknown``
and says so loudly — which on a manual bridge is the honest, actionable answer
("send a balance"), and matches ``prop_balance``'s refusal on the same row.

THE BINDING LIMIT IS THE SMALLER OF THE TWO, AND EITHER MAY BE ABSENT
--------------------------------------------------------------------
A prop account dies two ways — the daily-loss limit and the static-DD floor —
so the cushion is the **minimum** of the two distances. They fail independently:
on the measured account ``distance_to_daily_loss_usd`` was ``None``
(``day_pnl_state: realized_unreported``) while the DD distance was known. A
partial read is reported as such via ``limits_known``; it is never silently
treated as "only the DD floor matters", because the daily limit being unread is
a different fact from the daily limit being comfortable.

PURE FUNCTION, DELIBERATELY
---------------------------
:func:`grade_ticket_risk` takes values and returns a verdict — no DB, no clock,
no environment. The policy is therefore arguable in tests rather than against a
live position, which is exactly the discipline
``src/runtime/protection_reassert.py`` adopted after 2026-08-20, when a
remediation reasoned about live state and cancelled the wrong leg.
:func:`grade_account_ticket_risk` is the thin impure wrapper that reads the
snapshot for callers that only have an ``account_id``.

MODE
----
``PROP_TICKET_RISK_GATE_MODE`` — ``off`` / ``annotate`` (default) / ``enforce``.
A ``*_MODE`` knob, not a default-off ``*_ENABLED`` gate (Prime Directive; the
shape ``NEWS_INFLUENCE_MODE`` and ``NETTING_ATTRIBUTION_MODE`` already use), and
an unparseable value falls back to the default rather than to ``off`` — a typo
must not silently disarm the only thing comparing a ticket to the line that
kills the account.

  ``off``       — byte-for-byte the pre-2026-08-25 ticket. The rollback path.
  ``annotate``  — the ticket carries a loud caveat block and a soak row is
                  written to ``runtime_logs/prop_ticket_risk_soak.jsonl``
                  (read it at ``/api/diag/log_file?name=prop_ticket_risk_soak``;
                  see :func:`record_ticket_risk_soak`).
                  **The suggested SIZE is unchanged.** This is not a
                  silent annotate: withholding the warning from the one human
                  who can act on it would help nobody, and the ticket text is
                  operator information, not an order.
  ``enforce``   — additionally caps the suggested risk at the cushion. **Tier-3
                  and NOT the default** — it changes the number the executor
                  places, so it is the operator's call, and it wants a fresh
                  snapshot behind it (an ``enforce`` that caps off a stale
                  cushion would be worse than no cap).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

WITHIN = "within_cushion"
EXCEEDS = "exceeds_cushion"
UNKNOWN = "cushion_unknown"
NO_RISK = "no_risk_declared"

_ENV_MODE = "PROP_TICKET_RISK_GATE_MODE"
_DEFAULT_MODE = "annotate"
_MODES = ("off", "annotate", "enforce")


def mode() -> str:
    """``off`` / ``annotate`` / ``enforce``; unparseable falls back to default.

    Falling back to the DEFAULT rather than to ``off`` is deliberate: a typo in
    the env must not silently disarm the gate. Same reasoning as
    ``prop_balance.max_age_hours`` and ``CANDLE_CACHE_TTL_FRACTION``.
    """
    raw = (os.environ.get(_ENV_MODE) or "").strip().lower()
    return raw if raw in _MODES else _DEFAULT_MODE


def grade_ticket_risk(
    *,
    risk_usd: Optional[float],
    distance_to_dd_floor_usd: Optional[float] = None,
    distance_to_daily_loss_usd: Optional[float] = None,
    status_freshness: Optional[str] = None,
) -> Dict[str, Any]:
    """Grade one ticket's risk against the account's remaining cushion.

    Pure: values in, verdict out. ``status_freshness`` is the four-state string
    ``compute_rule_distance`` already returns (``ok`` / ``stale`` / ``absent`` /
    ``unchecked``); anything other than ``ok`` makes the cushion UNKNOWN,
    because a cushion we cannot show to be current is not a cushion.

    ``unchecked`` deserves its own mention: it means the freshness threshold is
    switched off, i.e. *nobody is checking*. That is emphatically not evidence
    the snapshot is fresh, so it lands on the unknown side with the rest.
    """
    if risk_usd is None:
        return {
            "state": NO_RISK, "risk_usd": None, "cushion_usd": None,
            "binding_limit": None, "limits_known": [], "overshoot_usd": None,
            "reason": "the ticket declares no risk_usd — nothing to grade "
                      "(a suppressed or shadow ticket carries no size)",
        }

    fresh_ok = status_freshness == "ok"
    known: list[str] = []
    candidates: list[tuple[str, float]] = []
    if fresh_ok and distance_to_dd_floor_usd is not None:
        known.append("dd_floor")
        candidates.append(("dd_floor", float(distance_to_dd_floor_usd)))
    if fresh_ok and distance_to_daily_loss_usd is not None:
        known.append("daily_loss")
        candidates.append(("daily_loss", float(distance_to_daily_loss_usd)))

    if not candidates:
        # WE DID NOT LOOK. Never `within`. Say WHY, because "send a balance"
        # and "fix the reader" are different operator actions.
        if not fresh_ok:
            why = (f"the account-status snapshot is {status_freshness or 'unreadable'}"
                   f" — a cushion that cannot be shown to be current is not a cushion")
        else:
            why = ("neither the DD-floor nor the daily-loss distance could be "
                   "derived from the reported snapshot")
        # ⚠️ CARRY THE LAST-KNOWN CUSHION THROUGH, LABELLED AS LAST-KNOWN.
        #
        # Measured against the LIVE payload while building this (2026-08-25,
        # snapshot 42.6 h old): every risk graded `cushion_unknown`, including
        # the $75 one — and the snapshot was ALREADY 32 h old at the moment of
        # the real incident, so a gate that stops at "unknown" would not have
        # said anything about $75 vs $64 on the night it mattered. "Unknown" is
        # the honest STATE and a bare "unknown" is a weaker WARNING than the
        # evidence supports.
        #
        # The two are not symmetric, which is what makes the last-known figure
        # worth quoting: the DD floor is a STATIC line, so a stale cushion moves
        # only as the balance does — and the direction that matters (the account
        # lost since) makes the true cushion SMALLER, not larger. Quoting it
        # asserts nothing about currency; it says "as of the last report this
        # risk did not fit", which is a fact, and leaves the operator to refresh.
        stale_cands = [
            ("dd_floor", float(distance_to_dd_floor_usd))
            if distance_to_dd_floor_usd is not None else None,
            ("daily_loss", float(distance_to_daily_loss_usd))
            if distance_to_daily_loss_usd is not None else None,
        ]
        stale_cands = [c for c in stale_cands if c is not None]
        last_known = min(stale_cands, key=lambda kv: kv[1]) if stale_cands else None
        return {
            "state": UNKNOWN, "risk_usd": float(risk_usd), "cushion_usd": None,
            "binding_limit": None, "limits_known": known, "overshoot_usd": None,
            "status_freshness": status_freshness, "reason": why,
            # Explicitly NOT `cushion_usd` — a stale number must never be read
            # as the live one by a consumer that only checks for a value.
            "last_known_cushion_usd": last_known[1] if last_known else None,
            "last_known_limit": last_known[0] if last_known else None,
            "last_known_exceeded": (
                float(risk_usd) >= last_known[1] if last_known else None),
        }

    binding, cushion = min(candidates, key=lambda kv: kv[1])
    risk = float(risk_usd)
    # `>=` not `>`: risking EXACTLY the cushion breaches on a full loss, and the
    # boundary is the case this gate exists for.
    exceeds = risk >= cushion
    return {
        "state": EXCEEDS if exceeds else WITHIN,
        "risk_usd": risk,
        "cushion_usd": cushion,
        "binding_limit": binding,
        "limits_known": known,
        "overshoot_usd": round(risk - cushion, 2) if exceeds else None,
        "status_freshness": status_freshness,
        "reason": (
            f"suggested risk ${risk:,.2f} is at or past the remaining "
            f"${cushion:,.2f} to the {binding.replace('_', ' ')} limit"
            if exceeds else
            f"suggested risk ${risk:,.2f} fits inside the remaining "
            f"${cushion:,.2f} to the {binding.replace('_', ' ')} limit"
        ),
    }


def grade_account_ticket_risk(
    account_id: str, *, risk_usd: Optional[float],
) -> Dict[str, Any]:
    """:func:`grade_ticket_risk` against ``account_id``'s latest snapshot.

    The impure wrapper. Best-effort by construction: a read failure grades
    ``cushion_unknown`` — never ``within_cushion`` — so a broken reader can
    never be mistaken for a comfortable account.
    """
    try:
        from src.prop import prop_reconcile
        rd = prop_reconcile.compute_rule_distance(account_id) or {}
    except Exception as exc:  # noqa: BLE001 — a read failure is not a cushion
        logger.warning(
            "prop_risk_gate: could not read the rule distance for %s (%s) — "
            "grading cushion_unknown, NOT within_cushion", account_id, exc,
        )
        return grade_ticket_risk(
            risk_usd=risk_usd, status_freshness="unreadable",
        )
    return grade_ticket_risk(
        risk_usd=risk_usd,
        distance_to_dd_floor_usd=rd.get("distance_to_dd_floor_usd"),
        distance_to_daily_loss_usd=rd.get("distance_to_daily_loss_usd"),
        status_freshness=rd.get("status_freshness"),
    )


def caveat_lines(verdict: Dict[str, Any]) -> list[str]:
    """Operator-facing caveat block for a ticket, or ``[]`` when nothing to say.

    ``within_cushion`` and ``no_risk_declared`` render nothing — a warning on
    every ticket is the desensitised-alarm P1 this repo treats as its own bug.
    """
    state = verdict.get("state")
    if state == EXCEEDS:
        risk = verdict.get("risk_usd") or 0.0
        cushion = verdict.get("cushion_usd") or 0.0
        limit = str(verdict.get("binding_limit") or "").replace("_", " ")
        return [
            "",
            "  🛑 DO NOT PLACE AT THE SUGGESTED SIZE — THIS RISKS MORE THAN THE "
            "ACCOUNT HAS LEFT.",
            f"     Suggested risk ${risk:,.2f} vs ${cushion:,.2f} remaining to "
            f"the {limit} limit.",
            "     Breaching that limit PERMANENTLY DISABLES the account. Either "
            "size so the",
            f"     risk at the stop is well under ${cushion:,.2f}, or skip this "
            "setup and reply",
            "     'skipped: insufficient cushion'.",
        ]
    if state == UNKNOWN:
        out = [
            "",
            "  ⚠️  CUSHION UNKNOWN — the account's distance to its account-killer "
            "limits could",
            f"     not be established ({verdict.get('reason')}).",
        ]
        # The last-known figure, when we have one, is strictly more actionable
        # than a bare "unknown" — and on the measured incident it is the ONLY
        # thing that would have said anything at all, since the snapshot was
        # already 32 h stale when the $75 ticket went out.
        lk = verdict.get("last_known_cushion_usd")
        if lk is not None and verdict.get("last_known_exceeded"):
            risk = verdict.get("risk_usd") or 0.0
            limit = str(verdict.get("last_known_limit") or "").replace("_", " ")
            out += [
                f"     ⛔ AS OF THE LAST REPORT this risk did NOT fit: "
                f"${risk:,.2f} vs ${lk:,.2f}",
                f"     remaining to the {limit} limit. That figure is STALE, not "
                "live — but the",
                "     account can only have moved, and a loss since would make it "
                "SMALLER.",
            ]
        out += [
            "     Send a fresh balance (`bal <balance> <equity>`) before placing, "
            "and size off",
            "     YOUR live balance — the suggested size below is not checked "
            "against any limit.",
        ]
        return out
    return []


def record_ticket_risk_soak(
    account_id: Optional[str], verdict: Dict[str, Any], *,
    annotated: bool = False,
) -> None:
    """Append one graded ticket to ``runtime_logs/prop_ticket_risk_soak.jsonl``.

    WHY THIS EXISTS (2026-08-29, ``/system-review``)
    ------------------------------------------------
    The ``MODE`` block above has promised since 2026-08-25 that at ``annotate``
    "a soak row is written". **No row was ever written.** This module shipped
    318 lines with no file write of any kind — ``soak`` appeared exactly once in
    the whole file, in that docstring. Every sibling ``*_MODE`` gate in this repo
    writes one and registers a diag read surface for it
    (``netting_attribution_soak``, ``protection_reassert_soak``,
    ``cash_settlement_soak``, ``exit_lever_soak``); the gate gating the one
    account that can be **permanently disabled** had neither.

    That is not a cosmetic gap. ``enforce`` is Tier-3 and the operator is
    supposed to decide it by reading what the gate WOULD have done — which is
    exactly the review this file's own MODE block calls for, and exactly the
    evidence that did not exist. Measured on the day this was written,
    ``breakout_1`` sat **$55.00** from its $4,700 floor (down from $64.00 at the
    08-25 review that built this module), and there was no way to ask how many
    tickets had been graded ``exceeds_cushion`` in between.

    ⚠️ **``off`` WRITES NOTHING**, so that mode stays byte-for-byte the
    pre-2026-08-25 behaviour, including on disk.

    ⚠️ **``annotated`` IS THE EFFECT, ``global_mode`` IS THE REQUEST.** They are
    recorded separately for the reason ``NETTING_ATTRIBUTION_MODE`` had to be
    corrected on 2026-08-09: a row that was graded but whose caveat never
    reached the operator must never read as one that did.

    ⚠️ **An unknown cushion is ``null``, never ``0.0``.** Zero is a real and
    terminal reading (the account is AT its floor); *we could not look* is not.
    The four ``state`` values pass through verbatim — this writer grades nothing
    of its own.

    Best-effort by construction: any failure returns silently. A ticket must
    never be lost, delayed, or altered because its observability row could not
    be written.
    """
    import json
    import os
    from datetime import datetime, timezone

    try:
        current = mode()
        if current == "off":
            return
        state = verdict.get("state")
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "account_id": account_id,
            "state": state,
            "global_mode": current,
            # The EFFECT: did a caveat actually reach the ticket text?
            "annotated": bool(annotated),
            # `enforce` is Tier-3 and not the default; recorded so a later
            # reader can tell an annotate-era row from an enforce-era one
            # without inferring it from the date.
            "would_have_capped": bool(state == EXCEEDS),
            "risk_usd": verdict.get("risk_usd"),
            "cushion_usd": verdict.get("cushion_usd"),
            "overshoot_usd": verdict.get("overshoot_usd"),
            "binding_limit": verdict.get("binding_limit"),
            "limits_known": verdict.get("limits_known"),
            "status_freshness": verdict.get("status_freshness"),
            "last_known_cushion_usd": verdict.get("last_known_cushion_usd"),
            "last_known_exceeded": verdict.get("last_known_exceeded"),
            "reason": verdict.get("reason"),
        }
        from src.utils.paths import runtime_logs_dir
        path = os.path.join(str(runtime_logs_dir()), "prop_ticket_risk_soak.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001 - never lose a ticket over its soak row
        return


__all__ = [
    "grade_ticket_risk", "grade_account_ticket_risk", "caveat_lines", "mode",
    "record_ticket_risk_soak",
    "WITHIN", "EXCEEDS", "UNKNOWN", "NO_RISK",
]
