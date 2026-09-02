"""The decision round-trip's TELEGRAM half — ask in the chat, answer with a tap.

Operator ask, 2026-09-02: *"is it possible for the telegram channel to become
2-way ... so that if there's decisions that are pop-ups they can be given to me
as pop-ups in the telegram chat? I can answer them there and send them back from
there instead of having to see the ping and then open the session."*

This is an INTEGRATION of two things that already worked, not new capability:

* ``src/runtime/work_decisions.py`` + ``GET/POST /api/bot/work/decision(s)`` —
  the decision inbox and its fail-closed write route (Phase H).
* ``src/prop/prop_expiry_prompt.py`` — a LIVE inline-keyboard Yes/No round-trip
  whose ``callback_data`` is ``propexp:<y|n>:<ticket_id>``, dispatched from a
  bot's ``CallbackQueryHandler``.

Everything below follows that precedent's shape deliberately.

────────────────────────────────────────────────────────────────────────────
IT DECIDES NOTHING, AND SAYS SO
────────────────────────────────────────────────────────────────────────────

``POST /api/bot/work/decision`` appends ONE submission to the live layer's
transit log and returns ``answerState: in_transit`` — **never** ``committed``.
Truth lands only when a committer writes the answer into the work object in the
repo. So the confirmation this module renders after a tap says *submitted, not
yet decided*, in those words. A UI that reported "answered!" on a 200 would
produce the one outcome the transit contract exists to refuse: a question that
reads as dealt with while nothing landed.

Every failure path here says **nothing was submitted** — except the one case
where we genuinely cannot know (no HTTP response at all), which is its own state
and says so. Manufacturing certainty in either direction is the failure.

────────────────────────────────────────────────────────────────────────────
THE 64-BYTE BOUND, AND WHY THE BUTTON CARRIES A DIGEST
────────────────────────────────────────────────────────────────────────────

Telegram caps ``callback_data`` at **64 bytes**. A work-object id like
``WO-20260901-PHASE-H`` plus a request id like
``DEC-20260901-READ-GATE-SEQUENCING`` plus an option key is far past that, so
the button cannot carry the identifiers themselves.

    callback_data = "wdec:<12 hex>:<8 hex>"      # 26 bytes, FIXED

``<12 hex>`` digests ``object_id \\x00 request_id``; ``<8 hex>`` digests the
option ``key``. Both are recomputed at tap time over the CURRENT inbox and
matched — so there is **no mapping file to persist, expire, or lose across a
restart**, which is the whole reason a digest was chosen over a server-side
token table.

⚠️ **The option token is a digest of the KEY, never a positional INDEX.** An
index is stable only while nobody edits the object file; if a request's options
are reordered between the send and the tap, an index silently selects a
DIFFERENT answer than the one whose label the operator read. A key digest fails
loudly instead (``option_gone``), which is the only safe direction for a control
that records what a human decided.

⚠️ **A digest collision is REFUSED, never resolved by taking the first match.**
Two requests (or two options within one request) that digest identically make
the tap ambiguous, and guessing which one the operator meant is exactly the
class of error this module must not commit. Ambiguity grades ``ambiguous`` and
submits nothing.

────────────────────────────────────────────────────────────────────────────
MULTIPLE CHOICE ONLY — free text is a SECOND step, deliberately not half-built
────────────────────────────────────────────────────────────────────────────

Inline buttons cannot carry arbitrary text. A free-text answer needs the reply
flow the prop bridge already uses for pasting a fill, and wiring half of it
would leave a request that LOOKS answerable in Telegram and is not.

So: a request that declares options gets buttons. A request that declares
**none** is still sent — the operator seeing a question they are blocking on is
most of the value, and silently skipping it would recreate the
``unanswerableOperatorEdges`` gap one layer down — but it is sent with a plain
statement that it needs a free-text answer through the SPA or the repo. The two
are counted separately in the sweep stats and never pooled.

────────────────────────────────────────────────────────────────────────────
WHICH BOT, AND WHY IT MATTERS MORE THAN IT LOOKS
────────────────────────────────────────────────────────────────────────────

A button only works in a bot some process is POLLING. Measured 2026-09-02:

* ``ict-telegram-bot.service`` polls ``TELEGRAM_BOT_TOKEN`` (the trader bot) and
  already has a ``CallbackQueryHandler``.
* ``ict-claude-bridge.service`` polls the **prop** token, despite its name.
* ``TELEGRAM_CLAUDE_BOT_SECRET`` — the dedicated Claude bot — **is polled by
  nothing.** A prompt sent on that token would render buttons that go nowhere.

So this module resolves its destination through
:func:`answerable_route`, which returns only a route whose bot is actually
polled, and states plainly when it cannot. That is deliberately NOT
``telegram_routes.claude_route()``: that route is correct for a one-way ping and
wrong for a two-way control, and using it because the name matches would ship
dead buttons that look healthy. (Recorded for
``OI-20260901-CLAUDE-CHANNEL-SEPARATION-SHIPPED-BUT-UNPROVEN``: separating the
ping channel does not by itself make it answerable.)

────────────────────────────────────────────────────────────────────────────
KNOBS
────────────────────────────────────────────────────────────────────────────

``WORK_DECISION_PROMPT_SECONDS``   sweep cadence, default 300. ``<= 0`` PAUSES
                                   prompting. An unparseable value falls back to
                                   the DEFAULT, never to zero — a typo must not
                                   silently switch the channel off.
``WORK_DECISION_API_BASE``         where the sweep reads/writes, default
                                   ``http://127.0.0.1:8001`` (loopback on the
                                   VM: the bot and the API share a host).
``WORK_DECISION_PROMPT_RETAIN_DAYS`` how long a prompted-marker is kept once its
                                   request has left the inbox, default 30.

Cadence + scope knobs, not a default-off ``*_ENABLED`` gate (Prime Directive).

────────────────────────────────────────────────────────────────────────────
NOT REGISTERED WITH ``collapsed-state-guard``, AND WHY
────────────────────────────────────────────────────────────────────────────

Two never-collapsed vocabularies live here — :data:`CALLBACK_OUTCOMES` and
:func:`read_prompt_state`'s ``read``/``absent``/``unreadable`` — and neither is
registered as a contract, deliberately.

:data:`CALLBACK_OUTCOMES` is fully branched on by exactly one consumer
(:func:`render_callback_reply`, which gives each outcome its own sentence and is
asserted state-by-state in the tests), so the guard would add nothing it does
not already have.

The prompt-state trio is the interesting one: the SWEEP branches only on
``unreadable`` (hold), because ``read`` and ``absent`` genuinely lead to the
same action — an absent marker file and an empty one both mean *nothing has been
prompted yet*. The distinction is for the READER of
``/api/diag/log_file?name=work_decision_prompted``, not for the sweep.
Registering it would demand a branch the sweep does not need, which is the
decorative branch ``BYBIT_HEDGE_MODE_SYMBOLS`` declined to add for the same
reason. Stated here rather than left to be rediscovered.

(The sweep's stat key is ``prompt_state_read``, and the prefix is load-bearing:
the unprefixed spelling is the consumer token that
``pairs_executor.open_state_read`` registers in
``scripts/ci/check_collapsed_states.py``, so a module using it bare has its
states attributed to THAT contract. Renamed rather than annotated — an override
naming someone else's contract is exactly the marker that is cheaper to lie to
than to satisfy. Not spelled out above for the same reason: the guard reads
prose, and this paragraph tripped it once already.)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

# ── the callback wire format ─────────────────────────────────────────────────
CB_PREFIX = "wdec"
#: Telegram's hard cap on `callback_data`. Asserted, not assumed.
TELEGRAM_CALLBACK_DATA_MAX_BYTES = 64
REQUEST_DIGEST_LEN = 12
OPTION_DIGEST_LEN = 8

# ── the outcome of one tap. NEVER COLLAPSED. ─────────────────────────────────
# The pair that matters most is `refused` vs `unknown`: the first is a server
# decision we received (nothing was appended), the second is that we never got
# an answer at all and therefore CANNOT say whether the submission landed.
# Reporting `unknown` as "nothing was submitted" would be manufacturing a
# certainty nobody has — the same sin as reporting a 200 as `committed`.
SUBMITTED = "submitted"
ALREADY_ANSWERED = "already_answered"
OPTION_GONE = "option_gone"
REQUEST_GONE = "request_gone"
AMBIGUOUS = "ambiguous"
WRITE_CLOSED = "write_closed"
UNAUTHORIZED = "unauthorized"
NOT_PERSISTED = "not_persisted"
REFUSED = "refused"
INBOX_UNREADABLE = "inbox_unreadable"
UNKNOWN = "unknown"

CALLBACK_OUTCOMES: tuple[str, ...] = (
    SUBMITTED, ALREADY_ANSWERED, OPTION_GONE, REQUEST_GONE, AMBIGUOUS,
    WRITE_CLOSED, UNAUTHORIZED, NOT_PERSISTED, REFUSED, INBOX_UNREADABLE,
    UNKNOWN,
)

_DEFAULT_API_BASE = "http://127.0.0.1:8001"
_DEFAULT_PROMPT_SECONDS = 300.0
_DEFAULT_RETAIN_DAYS = 30.0
_HTTP_TIMEOUT_S = 15.0
_PROMPT_STATE_BASENAME = "work_decision_prompted.json"


# ═════════════════════════════════════════════════════════════════════════════
# Pure encoding — no I/O, so the wire format is arguable in tests rather than
# against a live chat.
# ═════════════════════════════════════════════════════════════════════════════


def request_digest(object_id: str, request_id: str) -> str:
    """Short stable token for ``(object_id, request_id)``.

    NUL-joined so ``("ab", "c")`` and ``("a", "bc")`` cannot collide by
    concatenation — a separator that can appear in an id would make two distinct
    requests share a button.
    """
    raw = f"{object_id}\x00{request_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:REQUEST_DIGEST_LEN]


def option_digest(option_key: str) -> str:
    """Short stable token for one option KEY (never its label, never its index)."""
    return hashlib.sha256(option_key.encode("utf-8")).hexdigest()[:OPTION_DIGEST_LEN]


def encode_callback(object_id: str, request_id: str, option_key: str) -> str:
    """``wdec:<req12>:<opt8>`` — fixed 26 bytes, checked against the cap."""
    data = f"{CB_PREFIX}:{request_digest(object_id, request_id)}:{option_digest(option_key)}"
    encoded = len(data.encode("utf-8"))
    if encoded > TELEGRAM_CALLBACK_DATA_MAX_BYTES:  # pragma: no cover - structurally impossible
        raise ValueError(
            f"callback_data is {encoded} bytes, over Telegram's "
            f"{TELEGRAM_CALLBACK_DATA_MAX_BYTES}-byte cap: {data!r}"
        )
    return data


def decode_callback(callback_data: str) -> Optional[tuple[str, str]]:
    """``wdec:<req12>:<opt8>`` → ``(req_digest, opt_digest)``.

    ``None`` when this is not one of ours, so a caller falls through to its
    other handlers exactly as ``handle_expiry_callback`` does.
    """
    if not callback_data or not callback_data.startswith(CB_PREFIX + ":"):
        return None
    parts = callback_data.split(":")
    if len(parts) != 3:
        return None
    _, req_d, opt_d = parts
    req_d, opt_d = req_d.strip(), opt_d.strip()
    if len(req_d) != REQUEST_DIGEST_LEN or len(opt_d) != OPTION_DIGEST_LEN:
        return None
    if not all(c in "0123456789abcdef" for c in req_d + opt_d):
        return None
    return req_d, opt_d


# ═════════════════════════════════════════════════════════════════════════════
# Resolution — find the request/option a tapped button names, over the CURRENT
# inbox. Ambiguity is refused, never resolved by first match.
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Resolution:
    """What a tapped button resolved to. ``outcome`` is empty on success."""
    request: Optional[dict[str, Any]]
    option: Optional[dict[str, Any]]
    outcome: str


def resolve_callback(
    requests: Iterable[dict[str, Any]], req_digest: str, opt_digest: str
) -> Resolution:
    """Match a button's digests against the inbox's requests and options."""
    matches = [
        r for r in requests
        if request_digest(str(r.get("objectId")), str(r.get("id"))) == req_digest
    ]
    if not matches:
        return Resolution(None, None, REQUEST_GONE)
    if len(matches) > 1:
        # Never pick one. Two questions behind one button is not answerable.
        return Resolution(None, None, AMBIGUOUS)
    req = matches[0]
    opts = [
        o for o in (req.get("options") or [])
        if isinstance(o, dict) and isinstance(o.get("key"), str)
        and option_digest(o["key"]) == opt_digest
    ]
    if not opts:
        # The option was renamed or removed since the prompt was sent. Fail
        # loudly: this is exactly what a positional index would have hidden.
        return Resolution(req, None, OPTION_GONE)
    if len(opts) > 1:
        return Resolution(req, None, AMBIGUOUS)
    return Resolution(req, opts[0], "")


# ═════════════════════════════════════════════════════════════════════════════
# Rendering — the message the operator reads, and the keyboard they tap.
# Plain text throughout (no parse_mode): question/option text is authored freely
# in YAML and an HTML parse_mode would reject a stray `<` as a bad entity, which
# is the same reason `claude_bridge.on_callback` sends REPORT_PROMPT unparsed.
# ═════════════════════════════════════════════════════════════════════════════


def _option_button_text(option: dict[str, Any]) -> str:
    label = option.get("label") or option.get("key") or "?"
    return str(label)[:60]


def build_decision_keyboard(request: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Telegram ``reply_markup`` — one row per declared option, or ``None``.

    ``None`` for a request with no options: there is nothing to tap, and an
    empty keyboard would render as a question that looks answerable.
    """
    object_id = str(request.get("objectId"))
    request_id = str(request.get("id"))
    rows = []
    for opt in request.get("options") or []:
        if not isinstance(opt, dict) or not isinstance(opt.get("key"), str):
            continue
        rows.append([{
            "text": _option_button_text(opt),
            "callback_data": encode_callback(object_id, request_id, opt["key"]),
        }])
    if not rows:
        return None
    return {"inline_keyboard": rows}


def render_decision_prompt(request: dict[str, Any]) -> str:
    """The prompt body. States the question, the options, and where truth lands."""
    urgency = request.get("urgency")
    head = "🛑 DECISION NEEDED" if urgency == "blocking" else "❓ DECISION"
    lines = [
        f"{head} — {request.get('objectTitle') or request.get('objectId')}",
        "",
        str(request.get("question") or "(the request declares no question text)"),
    ]
    context = request.get("context")
    if context:
        lines += ["", str(context)]
    options = [
        o for o in (request.get("options") or [])
        if isinstance(o, dict) and isinstance(o.get("key"), str)
    ]
    if options:
        lines += ["", "Options:"]
        for opt in options:
            label = opt.get("label") or opt["key"]
            implication = opt.get("implication")
            lines.append(f"• {label}" + (f" — {implication}" if implication else ""))
        lines += ["", "Tap an option below. Tapping SUBMITS your answer; it is "
                       "not the decision until it is written into the repo."]
    else:
        # Honest about the boundary rather than pretending the button flow
        # covers it. Free text is a second step; see the module docstring.
        lines += ["", "⚠️ This question takes a FREE-TEXT answer, which cannot be "
                       "sent from a Telegram button. Answer it on the dashboard "
                       "or in the repo — it is shown here so it is not invisible."]
    lines += [
        "",
        f"object:  {request.get('objectId')}",
        f"request: {request.get('id')}",
    ]
    return "\n".join(lines)


def render_callback_reply(
    outcome: str,
    *,
    request: Optional[dict[str, Any]] = None,
    option: Optional[dict[str, Any]] = None,
    detail: Optional[str] = None,
) -> str:
    """What the operator sees after a tap.

    ⚠️ The success text must never say ``committed`` / "decided" / "answered".
    The route returns ``in_transit`` and this text says exactly that, because a
    confirmation that overstates the state is the forward failure the whole
    transit contract refuses.
    """
    object_id = str((request or {}).get("objectId") or "?")
    label = (option or {}).get("label") or (option or {}).get("key") or "?"
    where = f"docs/claude/work/objects/{object_id}.yaml"

    if outcome == SUBMITTED:
        return (
            "✅ Submitted — NOT yet decided.\n\n"
            f"Your answer: {label}\n\n"
            "This is truth in transit. It becomes the decision only when it is "
            f"written into {where} in the repo. Until then the question still "
            "reads UNANSWERED — transit fails back, never forward.\n"
            "I'll confirm here once it has landed in the repo."
        )
    if outcome == ALREADY_ANSWERED:
        return (
            "ℹ️ Already answered — this decision is already recorded in the "
            "repo, so nothing was submitted.\n\n"
            f"To change it, edit {where}. A committed decision is changed there, "
            "never by queueing a second answer behind one that landed."
        )
    if outcome == OPTION_GONE:
        return (
            "⚠️ That option is no longer offered on this question — the request "
            "was edited since this message was sent. Nothing was submitted.\n"
            "Ask for a fresh prompt, or answer on the dashboard."
        )
    if outcome == REQUEST_GONE:
        return (
            "⚠️ I can't find that question any more — it was removed or renamed "
            "since this message was sent. Nothing was submitted."
        )
    if outcome == AMBIGUOUS:
        return (
            "⚠️ That button matches more than one question or option, so I will "
            "not guess which you meant. Nothing was submitted. Please answer on "
            "the dashboard."
        )
    if outcome == WRITE_CLOSED:
        return (
            "⚠️ Answering is closed right now: the server holds no write token "
            "(DASHBOARD_API_TOKEN is unset), so it refuses every submission. "
            "Nothing was submitted. This is fail-closed by design, not an "
            "outage."
        )
    if outcome == UNAUTHORIZED:
        return (
            "⚠️ The bot's decision token was rejected by the API. Nothing was "
            "submitted. (DASHBOARD_API_TOKEN differs between this bot and the "
            "web API.)"
        )
    if outcome == NOT_PERSISTED:
        return (
            "⚠️ The API accepted the request but could not persist it, so "
            "nothing was submitted" + (f": {detail}" if detail else ".")
        )
    if outcome == INBOX_UNREADABLE:
        return (
            "⚠️ I couldn't read the decision inbox, so nothing was submitted"
            + (f": {detail}" if detail else ".")
        )
    if outcome == REFUSED:
        return (
            "⚠️ The API refused that answer, so nothing was submitted"
            + (f": {detail}" if detail else ".")
        )
    # UNKNOWN — the one case where we must NOT claim either way.
    return (
        "⚠️ I could not get a reply from the decision API, so I do NOT know "
        "whether your answer was recorded" + (f" ({detail})" if detail else "")
        + ".\nCheck the dashboard's decision inbox before tapping again — a "
        "second tap could queue a duplicate."
    )


# ═════════════════════════════════════════════════════════════════════════════
# The API client. Loopback HTTP on purpose: the ROUTE stays the single owner of
# every refusal (unknown option, empty submission, 409-already-answered, the
# fail-closed bearer gate). Re-implementing those checks in the bot would be a
# second copy of the policy, which is how two copies drift.
# ═════════════════════════════════════════════════════════════════════════════


def api_base() -> str:
    raw = (os.environ.get("WORK_DECISION_API_BASE") or "").strip()
    return (raw or _DEFAULT_API_BASE).rstrip("/")


def _write_token() -> Optional[str]:
    tok = (os.environ.get("DASHBOARD_API_TOKEN") or "").strip()
    return tok or None


@dataclass(frozen=True)
class HttpResult:
    """``status`` is ``None`` when no HTTP response was received at all."""
    status: Optional[int]
    body: Any
    error: Optional[str]


def _http_json(
    url: str, *, method: str = "GET", payload: Optional[dict] = None,
    headers: Optional[dict] = None, timeout: float = _HTTP_TIMEOUT_S,
) -> HttpResult:
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed loopback base
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return HttpResult(resp.status, json.loads(raw), None)
            except ValueError:
                return HttpResult(resp.status, None, "response was not JSON")
    except urllib.error.HTTPError as exc:
        # A STATUS was received: the server decided. Distinct from the branch
        # below, where nothing came back and we cannot say what happened.
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001  # allow-silent: the body is a nicety; the STATUS is what decides the outcome and we already have it.
            pass
        body: Any = None
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = {"detail": raw[:400]} if raw else None
        return HttpResult(exc.code, body, None)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return HttpResult(None, None, str(exc))


def fetch_inbox() -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """``GET /api/bot/work/decisions``. Unauthenticated (Tier-1 read).

    Returns ``(inbox, error)``. ``None`` inbox is *we could not look* — never an
    empty one, which would read as "no decisions are waiting".
    """
    res = _http_json(f"{api_base()}/api/bot/work/decisions")
    if res.status == 200 and isinstance(res.body, dict):
        return res.body, None
    if res.status is None:
        return None, res.error or "no response"
    return None, f"HTTP {res.status}"


def _detail(body: Any) -> str:
    if isinstance(body, dict):
        d = body.get("detail")
        if isinstance(d, str):
            return d
        if d is not None:
            return str(d)
    return ""


def submit_answer(
    *, object_id: str, request_id: str, chosen: str, submitted_by: str = "telegram",
) -> tuple[str, Optional[str]]:
    """``POST /api/bot/work/decision``. Returns ``(outcome, detail)``.

    Maps the route's OWN refusals onto this module's outcome vocabulary. It
    never re-implements them: the checks live in one place and this only
    translates the answer for a human.
    """
    token = _write_token()
    if not token:
        # We can see the gate is closed without asking, and saying so is more
        # useful than a 503 the operator has to interpret.
        return WRITE_CLOSED, "DASHBOARD_API_TOKEN is unset in the bot's environment"
    res = _http_json(
        f"{api_base()}/api/bot/work/decision",
        method="POST",
        payload={
            "object_id": object_id,
            "request_id": request_id,
            "chosen": chosen,
            "submitted_by": submitted_by,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    if res.status is None:
        return UNKNOWN, res.error or "no response"
    detail = _detail(res.body)
    if res.status == 200:
        return SUBMITTED, None
    if res.status == 409:
        return ALREADY_ANSWERED, detail or None
    if res.status == 401:
        return UNAUTHORIZED, detail or None
    if res.status == 503:
        # The route has exactly two 503s and they mean opposite things about
        # the operator's next move, so they are not collapsed.
        if "not configured to accept writes" in detail:
            return WRITE_CLOSED, detail
        return NOT_PERSISTED, detail or None
    if res.status in (400, 404):
        return REFUSED, detail or f"HTTP {res.status}"
    return REFUSED, detail or f"HTTP {res.status}"


# ═════════════════════════════════════════════════════════════════════════════
# The tap handler — transport-agnostic, exactly like handle_expiry_callback.
# ═════════════════════════════════════════════════════════════════════════════


def handle_decision_callback(callback_data: str) -> Optional[dict[str, Any]]:
    """Process one ``wdec:*`` button press.

    Returns ``{"outcome": str, "reply": str, "objectId": .., "requestId": ..,
    "chosen": ..}`` or ``None`` when this is not one of ours (the caller falls
    through to its other handlers). Never raises.
    """
    decoded = decode_callback(callback_data)
    if decoded is None:
        return None
    req_digest, opt_digest = decoded

    inbox, error = fetch_inbox()
    if inbox is None:
        return {
            "outcome": INBOX_UNREADABLE,
            "reply": render_callback_reply(INBOX_UNREADABLE, detail=error),
            "objectId": None, "requestId": None, "chosen": None,
        }
    resolution = resolve_callback(inbox.get("requests") or [], req_digest, opt_digest)
    if resolution.outcome:
        return {
            "outcome": resolution.outcome,
            "reply": render_callback_reply(
                resolution.outcome, request=resolution.request),
            "objectId": (resolution.request or {}).get("objectId"),
            "requestId": (resolution.request or {}).get("id"),
            "chosen": None,
        }

    req, opt = resolution.request or {}, resolution.option or {}
    object_id, request_id, chosen = str(req.get("objectId")), str(req.get("id")), str(opt.get("key"))

    # A request already carrying a committed answer would 409 at the route
    # anyway; short-circuiting it here only avoids a pointless round trip, and
    # the route stays the authority (we do not decide it is answered, we read
    # the grade the route computed off the repo).
    if req.get("answerState") == "committed":
        return {
            "outcome": ALREADY_ANSWERED,
            "reply": render_callback_reply(ALREADY_ANSWERED, request=req, option=opt),
            "objectId": object_id, "requestId": request_id, "chosen": chosen,
        }

    outcome, detail = submit_answer(
        object_id=object_id, request_id=request_id, chosen=chosen)
    logger.info(
        "telegram_decisions: tap on %s/%s -> %s (chosen=%s)",
        object_id, request_id, outcome, chosen,
    )
    return {
        "outcome": outcome,
        "reply": render_callback_reply(outcome, request=req, option=opt, detail=detail),
        "objectId": object_id, "requestId": request_id, "chosen": chosen,
    }


# ═════════════════════════════════════════════════════════════════════════════
# The prompt sweep — ask ONCE per request, on a cadence.
# ═════════════════════════════════════════════════════════════════════════════


def prompt_state_path() -> Path:
    """Durable prompted-marker. Wall-clock keyed and on disk, NOT a module global.

    A per-process marker would re-ask every question on every bot restart, which
    is the defect `BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART`
    records putting 202 CRITICALs on the operator's channel.
    """
    return Path(runtime_logs_dir()) / _PROMPT_STATE_BASENAME


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _float_env(name: str, default: float) -> float:
    """Unparseable falls back to the DEFAULT, never to zero.

    Zero PAUSES this channel, so a typo must not be able to switch it off.
    """
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        logger.warning("telegram_decisions: %s=%r is not a number — using %s",
                       name, raw, default)
        return default


def prompt_interval_seconds() -> float:
    return _float_env("WORK_DECISION_PROMPT_SECONDS", _DEFAULT_PROMPT_SECONDS)


def marker_key(object_id: str, request_id: str) -> str:
    return f"{object_id}::{request_id}"


def read_prompt_state(path: Optional[Path] = None) -> tuple[dict[str, Any], str]:
    """Return ``(prompted_map, read_state)``.

    ``read_state`` is ``read`` / ``absent`` / ``unreadable`` — never collapsed.
    An ABSENT file genuinely means nothing has been prompted; one we could not
    open is *we did not look*, and the caller must not re-prompt on it (a
    re-prompt on an unreadable marker file would spam every open question every
    cadence, which is the desensitised-alarm failure).
    """
    p = path or prompt_state_path()
    try:
        if not p.exists():
            return {}, "absent"
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("telegram_decisions: prompt state unreadable: %s", exc)
        return {}, "unreadable"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.warning("telegram_decisions: prompt state malformed: %s", exc)
        return {}, "unreadable"
    prompted = data.get("prompted") if isinstance(data, dict) else None
    return (prompted if isinstance(prompted, dict) else {}), "read"


def write_prompt_state(prompted: dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or prompt_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": 1, "updated_at": _iso(_now()), "prompted": prompted}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _prune(
    prompted: dict[str, Any], live_keys: set[str], *, now: datetime,
) -> dict[str, Any]:
    """Drop markers that are BOTH gone from the inbox AND past the retain window.

    Both conditions, deliberately. Pruning on absence alone would re-ask a
    question that vanished for a moment (an object file that failed to parse
    drops out of the inbox), and re-asking a question the operator already
    answered is the noise this marker exists to prevent.
    """
    retain = timedelta(days=_float_env("WORK_DECISION_PROMPT_RETAIN_DAYS",
                                       _DEFAULT_RETAIN_DAYS))
    out: dict[str, Any] = {}
    for key, row in prompted.items():
        if key in live_keys:
            out[key] = row
            continue
        at = _parse_iso((row or {}).get("prompted_at") if isinstance(row, dict) else None)
        # Undateable is KEPT: we cannot show it is old, and the fail-safe
        # reading of a marker is that it still suppresses a re-ask.
        if at is None or (now - at) < retain:
            out[key] = row
    return out


def run_decision_prompt_sweep(
    *,
    sender: Optional[Callable[[str, Optional[dict[str, Any]]], bool]] = None,
    now: Optional[datetime] = None,
    state_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Send ONE prompt per un-prompted, unanswered decision request.

    Returns a stats dict. Never raises — this runs on a bot's job queue and a
    prompt bug must not kill the bot (the ``run_prop_expiry_prompts`` contract).

    The populations are counted separately and never pooled:
      ``prompted_choice``       questions sent WITH tappable options
      ``prompted_free_text``    questions sent as text only (no options declared)
      ``held_write_gate``       not sent because the API refuses writes right now
      ``held_route``            not sent because no POLLED bot could carry it
      ``failed``                send attempted and not confirmed
    """
    stats: dict[str, Any] = {
        "checked": False, "reason": None, "candidates": 0,
        "prompted_choice": 0, "prompted_free_text": 0,
        "held_write_gate": 0, "held_route": 0, "failed": 0, "paused": False,
        "prompt_state_read": None,
    }
    try:
        if prompt_interval_seconds() <= 0:
            stats["paused"] = True
            return stats

        inbox, error = fetch_inbox()
        if inbox is None:
            stats["reason"] = f"inbox unreadable: {error}"
            return stats
        if inbox.get("present") is False:
            stats["reason"] = f"inbox not present: {inbox.get('reason')}"
            return stats

        prompted, read_state = read_prompt_state(state_path)
        stats["prompt_state_read"] = read_state
        if read_state == "unreadable":
            # We did not look. Re-prompting here would re-ask everything.
            stats["reason"] = "prompt-state unreadable — holding rather than re-asking"
            return stats

        requests = [r for r in (inbox.get("requests") or []) if isinstance(r, dict)]
        live_keys = {
            marker_key(str(r.get("objectId")), str(r.get("id"))) for r in requests
        }
        stats["checked"] = True

        write_open = bool(((inbox.get("writeGate") or {}).get("acceptsWrites")))
        route = answerable_route()
        ref = now or _now()

        if sender is None:
            sender = _default_sender

        for req in requests:
            # Only a question NOBODY has answered. `in_transit` has an open
            # window and re-asking would invite a duplicate; `unreadable` means
            # we could not read the transit channel, which is not "unanswered".
            if req.get("answerState") != "not_submitted":
                continue
            key = marker_key(str(req.get("objectId")), str(req.get("id")))
            if key in prompted:
                continue
            stats["candidates"] += 1

            keyboard = build_decision_keyboard(req)
            if keyboard is not None and not write_open:
                # A tappable prompt whose taps will 503 is the "reads as dealt
                # with while nothing landed" failure. Hold and say so.
                stats["held_write_gate"] += 1
                logger.warning(
                    "telegram_decisions: holding prompt for %s — the API write "
                    "gate is closed (DASHBOARD_API_TOKEN unset on the web-api)",
                    key,
                )
                continue
            if not route.deliverable:
                stats["held_route"] += 1
                logger.warning(
                    "telegram_decisions: holding prompt for %s — %s",
                    key, route.note,
                )
                continue

            try:
                sent = bool(sender(render_decision_prompt(req), keyboard))
            except Exception as exc:  # noqa: BLE001 — a send bug never kills the bot
                logger.warning("telegram_decisions: send failed for %s: %s", key, exc)
                sent = False
            if not sent:
                # Marker NOT written, so it retries next cadence — the
                # `run_prop_expiry_prompts` rule: flip only on a confirmed send.
                stats["failed"] += 1
                continue
            prompted[key] = {
                "prompted_at": _iso(ref),
                "object_id": req.get("objectId"),
                "request_id": req.get("id"),
                "kind": "choice" if keyboard is not None else "free_text_only",
            }
            if keyboard is not None:
                stats["prompted_choice"] += 1
            else:
                stats["prompted_free_text"] += 1

        try:
            write_prompt_state(_prune(prompted, live_keys, now=ref), state_path)
        except OSError as exc:
            logger.warning("telegram_decisions: prompt state write failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 — the sweep must never kill the bot
        logger.warning("telegram_decisions: sweep failed: %s", exc, exc_info=True)
        stats["reason"] = f"sweep failed: {exc}"
    return stats


# ═════════════════════════════════════════════════════════════════════════════
# Destination — only a bot that is actually POLLED can carry a button.
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AnswerableRoute:
    token: Optional[str]
    token_from: Optional[str]
    chat_id: Optional[str]
    chat_from: Optional[str]
    note: str

    @property
    def deliverable(self) -> bool:
        return bool(self.token) and bool(self.chat_id)

    def describe(self) -> str:
        return (f"answerable: token={self.token_from or '(none)'} "
                f"chat={self.chat_from or '(none)'} "
                f"deliverable={self.deliverable}")


def answerable_route() -> AnswerableRoute:
    """The bot a decision prompt may be sent on, because a process POLLS it.

    ⚠️ This is NOT ``telegram_routes.claude_route()`` and the difference is the
    whole point. That route resolves ``TELEGRAM_CLAUDE_BOT_SECRET`` when set —
    a bot **no process polls**, so a prompt sent there would render buttons that
    silently go nowhere. Delivery and answerability are different properties,
    and the existing router only reasons about the first.

    ``TELEGRAM_BOT_TOKEN`` is the trader bot, which
    ``ict-telegram-bot.service`` polls with a ``CallbackQueryHandler`` — the
    only destination whose taps this repo can currently receive.
    """
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip() or None
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip() or None
    if token and chat:
        note = "trader bot (TELEGRAM_BOT_TOKEN) — polled by ict-telegram-bot.service"
    elif not token:
        note = ("no TELEGRAM_BOT_TOKEN: the only POLLED bot has no token, so a "
                "prompt would have no answerable destination")
    else:
        note = "no TELEGRAM_CHAT_ID: nowhere to send"
    return AnswerableRoute(
        token=token, token_from="TELEGRAM_BOT_TOKEN" if token else None,
        chat_id=chat, chat_from="TELEGRAM_CHAT_ID" if chat else None, note=note,
    )


def _default_sender(text: str, reply_markup: Optional[dict[str, Any]]) -> bool:
    """Send one prompt on the answerable route. ``True`` only on a CONFIRMED send."""
    from src.runtime.notify import send_telegram_direct

    route = answerable_route()
    if not route.deliverable:
        logger.warning("telegram_decisions: %s", route.note)
        return False
    return bool(send_telegram_direct(
        text,
        parse_mode=None,          # arbitrary YAML text; HTML would reject a stray '<'
        mirror_to_fcm=False,
        bot_token=route.token,
        chat_id=route.chat_id,
        reply_markup=reply_markup,
    ))


__all__ = [
    "CB_PREFIX",
    "TELEGRAM_CALLBACK_DATA_MAX_BYTES",
    "CALLBACK_OUTCOMES",
    "AnswerableRoute",
    "Resolution",
    "answerable_route",
    "api_base",
    "build_decision_keyboard",
    "decode_callback",
    "encode_callback",
    "fetch_inbox",
    "handle_decision_callback",
    "marker_key",
    "option_digest",
    "prompt_interval_seconds",
    "prompt_state_path",
    "read_prompt_state",
    "render_callback_reply",
    "render_decision_prompt",
    "request_digest",
    "resolve_callback",
    "run_decision_prompt_sweep",
    "submit_answer",
    "write_prompt_state",
]
