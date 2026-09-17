"""Is the operator's DECISION CHANNEL actually delivering? — the reader.

MI-303. The sweep in :mod:`src.runtime.telegram_decisions` writes a durable
receipt on every run. **Nothing read it**, and that is the whole reason a
critical defect stood for days looking healthy.

MEASURED 2026-09-17
(``BL-20260917-THE-WORK-DECISION-SWEEP-REPORTS-ITSELF-HEALTHY-AND-ROUTED-WHILE-FAILING-TO-SEND-EVERY-CANDIDATE-ON-47-OF-47-RUNS``):
over the 47 complete records recoverable from the receipt ring, **46 read
``candidates: 4 / failed: 4 / prompted_choice: 0``** while every health field
read good — ``checked: true``, ``reason: null``, ``paused: false``,
``destination: claude``, ``poll_state: polled_with_handler``, and all three
``held_*`` counters at **zero**. Four operator decisions had reached nobody.
The cause was Telegram's 4096-character body cap; the reason it was invisible
for days is this module's absence.

────────────────────────────────────────────────────────────────────────────
WHY A LOG LINE WAS NOT ENOUGH, AND WHY THIS IS A SEPARATE MODULE
────────────────────────────────────────────────────────────────────────────

The sweep *did* log a warning per failed send. It went to the systemd journal,
**whose measured retention on this VM is ~30 minutes** (MI-109) against a
300-second cadence — so the evidence was gone before anyone was told there was
a problem. An operator-facing page must reach ``outcomes.jsonl``, which is what
feeds Telegram, ``/api/bot/notifications`` and ``/api/bot/logs?level=error``.
That is the same correction ``_emit_stop_over_cover_alert`` and
``_emit_bybit_over_cover_alert`` each needed, and this module deliberately
copies their shape rather than inventing a third.

It is a SEPARATE FILE from the producer on purpose, and not for tidiness:
``collapsed-state-guard`` refuses a contract whose only consumer is the
producing module, because *a module branching on its own constants proves
nothing about whether anyone acts on the distinction*. The split IS the fix.

────────────────────────────────────────────────────────────────────────────
THE SIX OUTCOMES TAKE FOUR DIFFERENT ACTIONS — none is decoration
────────────────────────────────────────────────────────────────────────────

``all_failed`` / ``partial_failure``   PAGE. Sends are being attempted and
                                       refused; the remedy is in the code or at
                                       Telegram, and ``failure_kinds`` names
                                       which.
``all_held``                           PAGE, with a DIFFERENT remedy — set a
                                       token, open the write gate, start a
                                       poller. ⚠️ Never pooled with
                                       ``all_failed``: a session told only
                                       "nothing went out" chases the wrong one.
``nothing_pending``                    QUIET, and reported as the
                                       EMPTY-DENOMINATOR state. It says nothing
                                       about whether a send would work, so it
                                       must never be rendered as health.
``not_graded``                         QUIET but DISTINCT — *we did not look*.
                                       A paused channel, an unreadable inbox
                                       and a crashed sweep are not a working
                                       channel.
``delivered``                          QUIET, and clears a standing latch.

⚠️ **THE CADENCE IS WHY THIS NEEDS A COOLDOWN AT ALL.** The sweep runs every
``WORK_DECISION_PROMPT_SECONDS`` (default 300), so an un-latched page on four
stuck decisions is ~12 an hour — the desensitised-alarm P1 this repo names in
its own right. It therefore rides the SHARED durable latch
(:mod:`src.runtime.alert_cooldown`), never a copy and never
``time.monotonic()``: the condition outlives every process, and a per-process
latch is the exact defect that put 202 CRITICALs on the operator's channel
(``BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART``).

**SEVERITY IS THE FAILING-CANDIDATE COUNT**, so a channel that gets WORSE
breaks the window while one merely standing stays quiet. A fifth decision
joining four stuck ones is a new fact; the same four still stuck is not.

⚠️ **LEVEL IS WARNING, NOT CRITICAL.** CRITICAL is reserved in this repo for a
position UNPROTECTED or REVERSED. Nothing here is money at risk — it is a
decision the operator has not been asked. Both levels reach Telegram, so
nothing is lost in delivery, and spending the top channel on a comms fault is
how the top channel stops being read.

⚠️ **THIS READS A FILE. IT MAKES NO NETWORK CALL**, takes no broker round-trip
and touches no order path — the ``account_reachability_alert`` invariant.

KNOBS (cadence/threshold, never a default-off ``*_ENABLED`` gate — Prime
Directive — and an unparseable value falls back to its DEFAULT, never to zero):

``DECISION_CHANNEL_ALERT_COOLDOWN_HOURS``  per-condition page window, default 6.
``DECISION_CHANNEL_ALERT_STALE_MINUTES``   how old the newest receipt row may be
                                           before the sweep is graded as having
                                           STOPPED, default 30 (6 missed runs at
                                           the 300s default).
``DECISION_CHANNEL_ALERT_SKIP``            truthy disables paging. The verdict is
                                           still GRADED and still readable — the
                                           allowlist discipline this repo holds
                                           to: scope the BINDING, never the
                                           MEASUREMENT.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.runtime import alert_cooldown as _alert_cooldown
from src.runtime.telegram_decisions import (
    OUTCOME_ALL_FAILED,
    OUTCOME_ALL_HELD,
    OUTCOME_DELIVERED,
    OUTCOME_NOT_GRADED,
    OUTCOME_NOTHING_PENDING,
    OUTCOME_PARTIAL,
    SWEEP_OUTCOMES,
    read_sweep_receipt,
)

logger = logging.getLogger(__name__)

ALERT_KIND = "decision_channel"

#: The single latch key. There is ONE decision channel, so unlike the
#: per-account alerts this has no natural key dimension — the severity is what
#: carries "it got worse".
_LATCH_KEY = "work_decision_sweep"

_DEFAULT_COOLDOWN_HOURS = 6.0
_DEFAULT_STALE_MINUTES = 30.0

# ── the receipt's own read states, from `read_sweep_receipt` ─────────────────
#: The receipt has never been written on this host. ⚠️ NOT health: it means the
#: sweep has never run here, which is exactly the state a fresh or mis-deployed
#: VM is in.
RECEIPT_ABSENT = "absent"
#: We could not read it. *We did not look* — never "the channel is fine".
RECEIPT_UNREADABLE = "unreadable"

#: Which outcomes are the operator's problem RIGHT NOW. `all_held` is in here
#: too, with its own message: a held prompt is an undelivered decision however
#: correct the hold was.
PAGING_OUTCOMES = (OUTCOME_ALL_FAILED, OUTCOME_PARTIAL, OUTCOME_ALL_HELD)


def _float_knob(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "decision_channel_alert: %s=%r is unparseable — falling back to "
            "the default %s rather than to zero, so a typo cannot silently "
            "switch the only reader of this channel off", name, raw, default)
        return default
    return max(value, minimum)


def cooldown_seconds() -> float:
    return _float_knob(
        "DECISION_CHANNEL_ALERT_COOLDOWN_HOURS", _DEFAULT_COOLDOWN_HOURS,
        minimum=0.0) * 3600.0


def stale_seconds() -> float:
    return _float_knob(
        "DECISION_CHANNEL_ALERT_STALE_MINUTES", _DEFAULT_STALE_MINUTES,
        minimum=1.0) * 60.0


def _skip() -> bool:
    return (os.environ.get("DECISION_CHANNEL_ALERT_SKIP") or "").strip().lower() \
        in {"1", "true", "yes", "on"}


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def assess(
    receipt: Dict[str, Any],
    read_state: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Grade the channel from the receipt. PURE — no I/O, no alerting.

    ``outcome`` is one of :data:`SWEEP_OUTCOMES`, or ``not_graded`` when the
    receipt itself could not be read. ⚠️ An ABSENT or UNREADABLE receipt grades
    ``not_graded`` and NEVER ``nothing_pending``: we did not look, which is a
    different fact from having looked and found the inbox empty.
    """
    ref = now or datetime.now(timezone.utc)
    if read_state != "read" or not receipt:
        return {
            "outcome": OUTCOME_NOT_GRADED,
            "receipt_state": read_state,
            "failing": 0, "candidates": 0, "sweep_stale": None,
            "age_seconds": None, "failure_kinds": {}, "failure_detail": [],
            "note": (
                "the sweep receipt has never been written on this host, so the "
                "decision sweep has never run here"
                if read_state == RECEIPT_ABSENT else
                "the sweep receipt could not be read — we did not look; this "
                "is NOT evidence the channel is working"),
        }

    last = receipt.get("last")
    if not isinstance(last, dict):
        return {
            "outcome": OUTCOME_NOT_GRADED, "receipt_state": read_state,
            "failing": 0, "candidates": 0, "sweep_stale": None,
            "age_seconds": None, "failure_kinds": {}, "failure_detail": [],
            "note": "the receipt carries no `last` run to grade",
        }

    # ⚠️ AN OLD ROW IS NOT A CURRENT VERDICT. A sweep that STOPPED leaves its
    # last row frozen — and a frozen `delivered` reads byte-identically to a
    # live one, which is the same trap the coordination board hit when a
    # comment-capped issue served successful reads for ~20h.
    ref_at = _parse_iso(last.get("run_at")) or _parse_iso(receipt.get("updated_at"))
    age = (ref - ref_at).total_seconds() if ref_at else None
    stale = None if age is None else age > stale_seconds()

    raw = str(last.get("sweep_outcome") or "")
    if raw in SWEEP_OUTCOMES:
        outcome = raw
    else:
        # A receipt written before `sweep_outcome` existed, or carrying a value
        # we do not recognise. Graded as *we did not look* rather than
        # re-derived here: a second definition of the verdict living in the
        # consumer is how the two drift, and `grade_sweep_outcome` owns it.
        outcome = OUTCOME_NOT_GRADED
        raw = raw or "(absent)"

    candidates = int(last.get("candidates") or 0)
    failing = int(last.get("failed") or 0)
    held = (int(last.get("held_write_gate") or 0)
            + int(last.get("held_route") or 0)
            + int(last.get("held_not_polled") or 0))
    kinds = last.get("failure_kinds")
    detail = last.get("failure_detail")
    return {
        "outcome": outcome,
        "raw_outcome": raw,
        "receipt_state": read_state,
        "candidates": candidates,
        "failing": failing,
        "held": held,
        "sweep_stale": stale,
        "age_seconds": age,
        "run_at": last.get("run_at"),
        "destination": last.get("destination"),
        "poll_state": last.get("poll_state"),
        "failure_kinds": kinds if isinstance(kinds, dict) else {},
        "failure_detail": detail if isinstance(detail, list) else [],
        "note": "",
    }


def describe(a: Dict[str, Any]) -> str:
    """The operator-facing body. States the population, always.

    ⚠️ EVERY OUTCOME GETS ITS OWN SENTENCE AND ITS OWN REMEDY. A single
    "decision prompts are not going out" message would re-collapse exactly the
    states this contract exists to keep apart — the two faults take different
    actions, and a body that does not say which one you have sends the reader
    to the wrong place.
    """
    outcome = a.get("outcome")
    cand, failing, held = a.get("candidates", 0), a.get("failing", 0), a.get("held", 0)
    kinds = a.get("failure_kinds") or {}
    kind_text = (", ".join(f"{k}×{v}" for k, v in sorted(kinds.items()))
                 or "no typed cause recorded")
    errs = [str(d.get("error")) for d in (a.get("failure_detail") or [])
            if isinstance(d, dict) and d.get("error")]
    err_text = f" First error: {errs[0]}" if errs else ""
    stale_text = ""
    if a.get("sweep_stale"):
        age = a.get("age_seconds")
        stale_text = (
            f" ⚠️ AND THE SWEEP ITSELF LOOKS STOPPED — its newest receipt row "
            f"is {age / 60.0:.0f} min old, so this verdict describes a run "
            f"that is no longer happening." if age is not None else
            " ⚠️ AND THE SWEEP ITSELF LOOKS STOPPED.")

    if outcome == OUTCOME_ALL_FAILED:
        return (
            f"\U0001F7E1 [WARN] THE OPERATOR DECISION CHANNEL IS DELIVERING "
            f"NOTHING. All {failing} of {cand} pending decision prompt(s) "
            f"FAILED to send on the last sweep — so {failing} question(s) you "
            f"are being asked have reached nobody, and the inbox will keep "
            f"reading as though they were asked. Typed cause: {kind_text}."
            f"{err_text} Remedy is in the send path, not in routing: read "
            f"/api/diag/log_file?name=work_decision_sweep_receipt.{stale_text}")
    if outcome == OUTCOME_PARTIAL:
        return (
            f"\U0001F7E1 [WARN] The operator decision channel is PARTLY "
            f"failing: {failing} of {cand} prompt(s) failed to send on the "
            f"last sweep. Typed cause: {kind_text}.{err_text} The ones that "
            f"failed have reached nobody.{stale_text}")
    if outcome == OUTCOME_ALL_HELD:
        return (
            f"\U0001F7E1 [WARN] All {held} of {cand} pending decision "
            f"prompt(s) are being HELD, not refused — so the remedy is "
            f"CONFIGURATION, not the send path: the API write gate is closed "
            f"(DASHBOARD_API_TOKEN), no destination resolved, or nothing is "
            f"polling the answerable bot (enable "
            f"ict-claude-decision-bot.service). destination={a.get('destination')} "
            f"poll_state={a.get('poll_state')}.{stale_text}")
    if outcome == OUTCOME_NOT_GRADED:
        why = a.get("note") or (
            f"the receipt's verdict reads {a.get('raw_outcome')!r}, which is "
            f"not one of the declared outcomes")
        return (
            f"\U0001F7E1 [WARN] The decision channel could NOT be graded — "
            f"{why}. This is *we did not look*, NOT a report that the channel "
            f"is healthy.{stale_text}")
    if outcome == OUTCOME_NOTHING_PENDING:
        return (
            f"\U0001F7E2 [OK] No decision was pending on the last sweep. "
            f"⚠️ EMPTY DENOMINATOR: this says nothing about whether a send "
            f"would have worked.{stale_text}")
    return (
        f"\U0001F7E2 [OK] The decision channel delivered on the last sweep "
        f"({cand} candidate(s), 0 failed).{stale_text}")


def _send_alert(message: str) -> None:
    """One Telegram + one typed WARNING push — the same shape (and the same
    channel) ``losing_streak_alert`` / ``silent_refusal_alert`` /
    ``account_reachability_alert`` use, so this lands beside its siblings
    rather than inventing a second style.

    ⚠️ THIS IS THE POINT OF THE MODULE. ``logger.warning`` reaches the systemd
    journal and NOTHING ELSE (~30 min retention, measured), which is how the
    sweep's own per-send warnings were lost for days.
    """
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception as exc:  # noqa: BLE001 — an alert never kills the caller
        logger.warning("decision_channel_alert: telegram send failed: %s", exc)
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": message})
    except Exception as exc:  # noqa: BLE001
        logger.debug("decision_channel_alert: fcm publish failed: %s", exc)


def status() -> Dict[str, Any]:
    """What the channel was last graded as — the read surface.

    Returned to the review skills and the ``/api/bot/notifications`` banner.
    Mirrors ``losing_streak_alert.status()``. Best-effort: never raises.
    """
    try:
        receipt, read_state = read_sweep_receipt()
        return assess(receipt, read_state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("decision_channel_alert: status failed: %s", exc)
        return {"outcome": OUTCOME_NOT_GRADED, "receipt_state": RECEIPT_UNREADABLE,
                "note": f"status read failed: {exc}"}


def run_decision_channel_check(
    *, now: Optional[datetime] = None, alert: Any = None,
) -> Dict[str, Any]:
    """Grade the channel and page if it is failing. Never raises.

    Returns the assessment with ``alerted`` / ``alert_disposition`` added.

    ⚠️ READ ``alert_disposition``, NEVER A BARE ``alerted: False``. That
    boolean collapses four different facts — we paged, the latch suppressed it,
    the outcome is not a paging one, and paging is switched off — and "we are
    not alerting" must be distinguishable from "we found nothing". That is the
    correction ``silent_refusal_alert`` needed for the same field.
    """
    emit = alert or _send_alert
    try:
        receipt, read_state = read_sweep_receipt()
        a = assess(receipt, read_state, now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("decision_channel_alert: check failed: %s", exc)
        return {"outcome": OUTCOME_NOT_GRADED, "alerted": False,
                "alert_disposition": "check_failed", "note": str(exc)}

    outcome = a.get("outcome")
    a["alerted"] = False

    # ── the quiet outcomes, each graded distinctly ───────────────────────────
    if outcome == OUTCOME_DELIVERED:
        a["alert_disposition"] = "healthy_delivered"
        return a
    if outcome == OUTCOME_NOTHING_PENDING:
        # ⚠️ Not health. Recorded as the empty denominator so a reader cannot
        # mistake a quiet inbox for a proven channel.
        a["alert_disposition"] = "empty_denominator"
        return a
    if outcome == OUTCOME_NOT_GRADED and not a.get("sweep_stale"):
        # *We did not look.* Deliberately does not page on its own: a paused
        # channel is the operator's own declared choice
        # (WORK_DECISION_PROMPT_SECONDS <= 0), and paging every cadence for a
        # condition nobody intends to change is the desensitised-alarm P1.
        a["alert_disposition"] = "ungraded_not_paged"
        return a

    if _skip():
        # The BINDING is scoped, never the MEASUREMENT: the verdict above is
        # graded and readable regardless, so the rows a reviewer needs exist.
        a["alert_disposition"] = "paging_disabled"
        return a

    # ── the paging outcomes ─────────────────────────────────────────────────
    if outcome in PAGING_OUTCOMES or a.get("sweep_stale"):
        # SEVERITY IS THE UNDELIVERED COUNT, so a channel getting WORSE breaks
        # the window while one merely standing stays quiet.
        severity = int(a.get("failing") or 0) + int(a.get("held") or 0)
        if _alert_cooldown.cooldown_admits(
            ALERT_KIND, _LATCH_KEY, cooldown_seconds(),
            severity=severity or None,
        ):
            emit(describe(a))
            a["alerted"] = True
            a["alert_disposition"] = "alerting"
        else:
            a["alert_disposition"] = "suppressed_cooldown"
        return a

    a["alert_disposition"] = "not_a_finding"
    return a


__all__ = [
    "ALERT_KIND",
    "PAGING_OUTCOMES",
    "RECEIPT_ABSENT",
    "RECEIPT_UNREADABLE",
    "assess",
    "cooldown_seconds",
    "describe",
    "run_decision_channel_check",
    "stale_seconds",
    "status",
]
