"""Push a COMMITTED decision answer back to the session that asked it.

The last hop of the Phase H round-trip. Everything before it is a push and this
one was a **pull**: the session that raised a question learned the answer only
by looking. Operator, 2026-09-02: *"can we add … a push something on the end of
that so that, when the answer gets to the repo, it knows to push it to the
session instead of waiting for the session to pull?"*

Feasibility, with each claim marked TESTED / READ / RECORDED:
``docs/design/decision-push-back-FEASIBILITY.md``. The DESIGN that followed the
operator's 2026-09-02 decision, with PROVEN / NOT PROVEN per mechanism:
``docs/design/decision-push-back-DESIGN.md``.

⚠️ **THE DELIVERY MECHANISM CHANGED ON 2026-09-02 AND THIS DOCSTRING USED TO
DESCRIBE THE OLD ONE.** It said the runner delivers via
``claude -p "<msg>" --cloud <session-id>``. The operator ruled that out — it
needs a claude.ai OAuth credential with no long-lived CI form, and *"we
definitely can't have a flow that relies on my minting new tokens every month"*.
**Nothing is ever minted.** Delivery is now done by a Claude SESSION calling
``create_trigger(persistent_session_id=…)`` + ``fire_trigger``, because those
are ``mcp__*`` tools that only a session holds. This module is the pure decision
half; ``scripts/ops/push_decisions_back.py`` is the repo half.

Four facts shape every line below:

1. **A RUNNER CANNOT DELIVER AT ALL.** Not by Routine ``/fire`` (it starts a NEW
   session, and its token is minted per-routine from the web UI), not by
   ``watch_url`` (TESTED: its credential is sealed to the artifact service), and
   not by the CLI (no long-lived CI credential exists). The delivering party is
   always a session.
2. **The push must CARRY the answer, never a pointer to it.** A woken turn has
   no ``mcp__*`` tools (RECORDED: a session-bound Routine stores no MCP
   connectors), so *"go read the PR"* strands it. The message quotes the answer.
3. **It is ONE-WAY.** Nothing acknowledges receipt. The sender learns delivery
   worked by observing what the woken session does — never by a reply.
4. **It ADDS push; it does not replace pull.** ``/api/bot/work/decisions`` keeps
   grading ``committed`` from the repo. A push-only design loses answers exactly
   when the asking session died, which is when the answer matters most.

────────────────────────────────────────────────────────────────────────────
THREE DELIVERY STATES, NEVER COLLAPSED
────────────────────────────────────────────────────────────────────────────

``pushed``        the delivering session fired the Routine and it was accepted
``session_gone``  the platform said this session cannot receive — **a real
                  state, not an error.** The answer stays discoverable on the
                  pull path exactly as before; nothing is lost, and the record
                  says why nobody was woken
``unknown``       **we could not establish it.** The drain did not run, the
                  fire failed for a reason we cannot attribute, or nobody
                  looked. This is the DEFAULT for anything not positively
                  identified as one of the other two

⚠️ **The pair that matters is ``session_gone`` vs ``unknown``.** Collapsing an
unrecognised failure into ``session_gone`` would write *"that session is dead"*
into the repo on the strength of a blip, and — because a marker suppresses
further attempts — would permanently strand an answer for a session that was
alive the whole time. So only a positive *cannot receive*
signal produces ``session_gone``; everything else is ``unknown``, and
``unknown`` writes **no marker**, so it is retried. ⚠️ As of 2026-09-02
``session_gone`` is REACHABLE BUT UNEXERCISED — see the note above `plan_push`.

⚠️ **An EXPIRED session is NOT gone.** READ: a cloud session stops after
inactivity and its VM is reclaimed, but reopening it *"provisions a fresh VM
with your conversation history restored"*. Whether a message queued to an
expired session is delivered on reopen, dropped, or errors is **UNKNOWN and
untested** — which is precisely why the unrecognised case grades ``unknown``.

────────────────────────────────────────────────────────────────────────────
IDEMPOTENCE COMES FROM THE REPO
────────────────────────────────────────────────────────────────────────────

Never from the workflow remembering. A committed answer that has been delivered
carries a ``push:`` block nested inside its ``answer:``; ``plan_push`` sees it
and skips. A workflow-side memory would be a second record of a fact the repo
already holds — the same mistake the transit log deliberately avoids by keeping
no ``state`` column.

⚠️ **The residual is stated, not hidden.** If a delivery succeeds and the run
then fails to land its marker, the next run pushes again. That direction is
chosen: a duplicate wake tells the session the same true thing twice, whereas
writing the marker *before* delivering would make a failed delivery read as
pushed — the forward failure this whole subsystem refuses.

Observe-only from the trader's point of view: no order path, no config, no VM.
"""
from __future__ import annotations

from typing import Any

# ONE owner for the asked-by vocabulary. Imported, never re-derived: two
# definitions of "do we have an address" is how a deliverable answer starts
# grading as undeliverable.
from src.runtime.work_decisions import (
    ASKED_BY_MALFORMED,
    ASKED_BY_RECORDED,
    ASKED_BY_UNRECORDED,
)

# ── the three delivery states ────────────────────────────────────────────────
PUSHED = "pushed"
SESSION_GONE = "session_gone"
UNKNOWN = "unknown"

DELIVERY_STATES: tuple[str, ...] = (PUSHED, SESSION_GONE, UNKNOWN)

# ── what plan_push decides to do with one request ────────────────────────────
DELIVER = "deliver"
SKIP_NOT_COMMITTED = "skip_not_committed"
SKIP_ALREADY_PUSHED = "skip_already_pushed"
SKIP_NO_ASKER = "skip_no_asker"
SKIP_ASKER_MALFORMED = "skip_asker_malformed"

PLAN_ACTIONS: tuple[str, ...] = (
    DELIVER,
    SKIP_NOT_COMMITTED,
    SKIP_ALREADY_PUSHED,
    SKIP_NO_ASKER,
    SKIP_ASKER_MALFORMED,
)

# ⚠️ THERE IS DELIBERATELY NO OUTCOME CLASSIFIER HERE, AND ITS ABSENCE IS A
# STATED GAP RATHER THAN AN OVERSIGHT.
#
# An earlier version graded the stdout/stderr of `claude -p --cloud` into these
# three states. That mechanism was ruled out (no long-lived CI credential), so
# the classifier went with it rather than being left as unreachable code.
#
# The replacement mechanism is a Claude session calling `fire_trigger`, and
# **nobody has yet observed what that call returns when the target session is
# archived, expired, or gone.** Writing a mapping for failures no one has seen
# would be inventing the evidence — precisely the error this subsystem's own
# feasibility work refused to make about `watch_url` and the Routine `/fire`
# endpoint. So the delivering session REPORTS one of the three states and
# `push_decisions_back.py --record` validates it against this vocabulary;
# `unknown` remains the default for anything not positively identified, and it
# writes no marker, so it is retried.
#
# What would close this gap: one observed delivery to a session that is
# genuinely archived, and one to a session that is genuinely live. Until then
# the honest position is that `session_gone` is REACHABLE BUT UNEXERCISED.


def plan_push(request: dict[str, Any]) -> dict[str, Any]:
    """Decide what to do with one normalised decision request. **Pure.**

    Takes a request as ``work_decisions.normalise_requests`` returns it, so this
    module never re-derives the schema — two definitions of "what a request is"
    is how a committed answer starts failing to grade as committed.
    """
    req_id = request.get("id")
    object_id = request.get("objectId")
    base = {"objectId": object_id, "requestId": req_id}

    if not request.get("answer"):
        # Nothing to push. The question is still open, which is the ordinary
        # state of most requests most of the time.
        return {**base, "action": SKIP_NOT_COMMITTED,
                "reason": "no committed answer"}

    push = request.get("push")
    if push:
        return {**base, "action": SKIP_ALREADY_PUSHED,
                "reason": f"already pushed ({push.get('state')})",
                "priorState": push.get("state")}

    # The three asked-by states get three different outcomes, and the two
    # skips are NOT the same fact — see `work_decisions.ASKED_BY_STATES`.
    state = request.get("askedByState")
    if state == ASKED_BY_MALFORMED:
        # A FINDING, reported separately from `unrecorded` and failing the run:
        # someone recorded an asker that cannot be reached, so this answer will
        # never be delivered while looking as though it will be.
        return {**base, "action": SKIP_ASKER_MALFORMED,
                "reason": "asked_by is present and unusable"}
    if state == ASKED_BY_UNRECORDED:
        # Ordinary and not a defect: every request written before the field
        # existed reads this way, as does any question a human asked.
        return {**base, "action": SKIP_NO_ASKER,
                "reason": "no asking session recorded"}
    if state != ASKED_BY_RECORDED:
        # A grade outside the vocabulary. We do not know that there is an
        # address, so we do not act as though there is.
        return {**base, "action": SKIP_NO_ASKER,
                "reason": f"unrecognised askedByState {state!r}"}

    asked_by = request.get("askedBy") or {}
    return {**base, "action": DELIVER, "reason": "committed answer with a reachable asker",
            "sessionId": asked_by.get("sessionId")}


def render_push_message(request: dict[str, Any]) -> str:
    """The message delivered into the asking session.

    ⚠️ **It CARRIES the answer.** A woken turn may have no ``mcp__*`` tools, so
    a message telling it to go read a PR or a job log strands it. Everything the
    session needs to act is quoted in here, and the repo path is given as the
    place truth lives rather than as an errand it must run first.

    ⚠️ **It states that it is one-way.** The woken session must not wait for, or
    try to reply to, whoever sent this.
    """
    answer = request.get("answer") or {}
    chosen = answer.get("chosen")
    free_text = answer.get("freeText")
    object_id = request.get("objectId")
    request_id = request.get("id")

    chosen_label = None
    implication = None
    for opt in request.get("options") or []:
        if opt.get("key") == chosen:
            chosen_label = opt.get("label")
            implication = opt.get("implication")
            break

    lines = [
        "DECISION ANSWERED — this is a one-way push. You asked this question; "
        "the operator has answered it and the answer is now committed in the repo.",
        "",
        f"Object:   {object_id}",
        f"Request:  {request_id}",
    ]
    question = request.get("question")
    if question:
        lines += ["", f"Question: {question}"]

    lines += ["", "ANSWER"]
    if chosen:
        lines.append(f"  chosen: {chosen}" + (f" — {chosen_label}" if chosen_label else ""))
        if implication:
            lines.append(f"  implication (as the author wrote it): {implication}")
    else:
        lines.append("  chosen: (none — the operator answered in free text only)")
    if free_text:
        lines.append(f"  free text: {free_text}")
    if answer.get("answeredAt"):
        lines.append(f"  answered_at: {answer.get('answeredAt')}")
    if answer.get("answeredBy"):
        lines.append(f"  answered_by: {answer.get('answeredBy')}")

    lines += [
        "",
        "The answer is quoted above IN FULL so you do not need to fetch anything "
        "to act on it — this turn may be running without mcp__* tools.",
        f"Truth of record: docs/claude/work/objects/{object_id}.yaml, under "
        f"decision_requests[id={request_id}].answer",
        "",
        "Nothing is waiting on a reply from you — this channel is one-way and "
        "nobody is listening for an acknowledgement. Continue the work this "
        "decision was blocking, or, if it is no longer yours to carry, record "
        "where you left it.",
    ]
    return "\n".join(lines)


def render_push_yaml_block(
    *,
    state: str,
    attempted_at: str,
    session_id: str | None,
    detail: str | None,
    pushed_by: str,
) -> dict[str, Any]:
    """The exact mapping written into the answer's ``push:`` key.

    Defined here rather than in the committer so the writer and the reader
    (``work_decisions.normalise_push``) share one definition of the shape.
    """
    if state not in DELIVERY_STATES:
        raise ValueError(f"{state!r} is not one of {DELIVERY_STATES}")
    return {
        "state": state,
        "attempted_at": attempted_at,
        "session_id": session_id,
        "detail": detail,
        "pushed_by": pushed_by,
    }
