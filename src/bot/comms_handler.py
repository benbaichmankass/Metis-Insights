"""
Telegram bot integration for the comms channel (Sprint S-027 PR 2).

This module is the bot-side counterpart to ``src/comms``. It does three
things:

1. **Polls** ``comms/requests/`` every ``poll_interval`` seconds and
   delivers each ``status=pending`` request to the operator as an
   inline-keyboard menu.
2. **Routes** Telegram callback queries (button taps) and free-text
   replies (the ``Other`` path) back into the request artifact's
   ``.response`` sub-document.
3. **Writes back** the answered artifact to git, committing with the
   ``comms(response):`` prefix that ``scripts/notify_on_pull.py``
   filters out — so an operator answer does not retrigger a
   checkpoint ping.

The poller runs as a single asyncio task spawned from
``Application.post_init``. There is no second timer: the existing
5-minute ``ict-git-sync.timer`` continues to deliver new request
artifacts to the VM, and the in-process poll merely scans the working
tree.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.comms import (
    ANSWER_STATUS,
    Answer,
    Question,
    Request,
    RequestStore,
    Response,
    STATUS,
    log_event,
    next_status_after_answer,
)
from src.comms.models import required_answered_count

if TYPE_CHECKING:  # circular-safe forward ref for the M5 consumer.
    from src.bot.test_strategy_consumer import BacktestConsumer

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 60.0
COMMS_CALLBACK_PREFIX = "comms:"
OTHER_CHOICE_ID = "__OTHER__"


def _is_health_review_topic(topic: Optional[str]) -> bool:
    """True if a comms request's topic identifies it as a health-review
    request (now-deprecated, see 2026-05-12 cleanup).

    The health-snapshot workflow used to mint these every cron tick with
    a topic of ``"Health review needed — run <run_id> (<STATUS>)"``.
    That entire flow has been removed; the operator now pulls the
    snapshot artifact from the Action UI directly. This guard keeps any
    in-flight backlog silent (no Telegram noise on deliver or expiry)
    while the bot's normal lifecycle drains them.

    Conservative substring match — ``topic`` is operator-set free-form
    text but the workflow's emitter (deleted in the same PR) is the
    only known producer, so the prefix is stable across the backlog.
    """
    return bool(topic) and topic.lower().startswith("health review")
COMMS_COMMIT_PREFIX = "comms(response):"

# Keys into context.user_data for the "Other" free-text capture flow.
USERDATA_AWAITING_KEY = "comms_awaiting_other"


# ----------------------------------------------------------------------
# Keyboard / message building

def build_keyboard(request_id: str, question: Question) -> Optional[InlineKeyboardMarkup]:
    """Build an inline keyboard for one question.

    Returns ``None`` for pure ``free_text`` questions — the operator
    just types a reply, no buttons.
    """
    rows: list[list[InlineKeyboardButton]] = []

    if question.input_type == "yes_no":
        rows.append([
            InlineKeyboardButton(
                "Yes", callback_data=_cb(request_id, question.question_id, "yes"),
            ),
            InlineKeyboardButton(
                "No", callback_data=_cb(request_id, question.question_id, "no"),
            ),
        ])
    elif question.input_type in ("choice", "multi_choice"):
        # Two columns per row keeps menus tidy; tg-button labels are <= ~30 chars.
        row: list[InlineKeyboardButton] = []
        for choice in (question.choices or []):
            row.append(InlineKeyboardButton(
                choice.label,
                callback_data=_cb(request_id, question.question_id, choice.id),
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

    if question.allow_other and question.input_type != "free_text":
        rows.append([InlineKeyboardButton(
            "✏️ Other (type a reply)",
            callback_data=_cb(request_id, question.question_id, OTHER_CHOICE_ID),
        )])

    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def render_question_text(request: Request, question: Question, *, idx: int, total: int) -> str:
    """Plain-text body for the menu. Plain text only — no parse_mode.

    Per BUG-009/030/031 and CLAUDE.md, dynamic content (request_id,
    operator-supplied prompts) must never be rendered through Markdown
    or HTML parsers because a stray ``*``, ``_``, ``<`` rejects the
    entire message.
    """
    header = f"📨 Comms request {request.request_id}"
    if total > 1:
        header += f"  (Q{idx + 1}/{total})"
    parts = [header]
    if request.topic:
        parts.append(f"Topic: {request.topic}")
    if request.context and idx == 0:
        parts.append("")
        parts.append(request.context)
    parts.append("")
    parts.append(question.prompt)
    if question.input_type == "free_text":
        parts.append("")
        parts.append("Reply with your answer as plain text.")
    return "\n".join(parts)


# ----------------------------------------------------------------------
# Poll loop

class CommsPoller:
    """Periodic deliver/expire pass over ``comms/requests/``.

    Designed to be spawned once via ``Application.post_init``. Owns no
    state beyond the store handle + chat target — every cycle is fully
    derived from the filesystem so a bot restart re-syncs cleanly.
    """

    def __init__(
        self,
        *,
        store: Optional[RequestStore] = None,
        repo_root: Optional[Path] = None,
        chat_id: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        backtest_consumer: Optional["BacktestConsumer"] = None,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.store = store or RequestStore(self.repo_root / "comms")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.poll_interval = float(poll_interval)
        # Optional M5 consumer. Runs as an extra pass inside
        # poll_once before the deliver/expiry/archive sweep so a
        # ``test_strategy:*`` artifact is answered + transitioned
        # past PENDING in the same cycle it was minted.
        self.backtest_consumer = backtest_consumer
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def start(self, application: Application) -> None:
        """Spawn the poll task. Idempotent: re-calls are no-ops."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(application))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _run(self, application: Application) -> None:
        logger.info("CommsPoller started (interval=%.1fs, root=%s)", self.poll_interval, self.repo_root)
        while not self._stop.is_set():
            try:
                await self.poll_once(application)
            except Exception:  # noqa: BLE001
                logger.exception("CommsPoller: unexpected error in poll cycle")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                continue

    async def poll_once(self, application: Application) -> None:
        """One pass: M5 consume, deliver pending, alert stuck, alert+expire stale, archive terminal."""
        if self.chat_id is None:
            logger.warning("CommsPoller: no chat_id configured; skipping cycle")
            return
        bot = application.bot

        # 0. M5: run the strategy-test consumer first so a /test
        #    artifact is transitioned past PENDING (to ANSWERED) in
        #    the same cycle it was minted. The consumer skips any
        #    artifact whose task != "test_strategy:*"; everything
        #    else falls through to the deliver pass below unchanged.
        if self.backtest_consumer is not None:
            try:
                self.backtest_consumer.scan_and_run(self.store)
            except Exception:  # noqa: BLE001
                logger.exception("CommsPoller: backtest consumer pass failed")

        # 1. Deliver any pending requests.
        for request in self.store.list_pending():
            try:
                await self._deliver(bot, request)
            except Exception:  # noqa: BLE001
                logger.exception("CommsPoller: failed to deliver %s", request.request_id)

        # 2. Stuck-request + expiry sweep (M1 P1-B).
        #    A request that sits in `sent` past its
        #    ``stuck_alert_threshold`` fires a one-time advisory alert;
        #    a request that hits ``expires_at`` fires a final alert
        #    BEFORE transitioning to EXPIRED so silent expiry is
        #    impossible.
        for request in self.store.list_awaiting_response():
            try:
                if request.is_expired():
                    await self._alert_expired(bot, request)
                    self.store.transition(
                        request,
                        to_status=STATUS.EXPIRED,
                        actor="bot",
                        note="ttl elapsed",
                    )
                    log_event(
                        "request_expired",
                        request_id=request.request_id,
                        actor="bot",
                    )
                elif request.is_stuck() and not request.stuck_alert_already_sent():
                    await self._alert_stuck(bot, request)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "CommsPoller: stuck/expiry sweep failed for %s",
                    request.request_id,
                )

        # 3. Archive terminal artifacts.
        for request in list(self.store.list_active()):
            if request.is_terminal():
                try:
                    self.store.archive(request)
                except Exception:  # noqa: BLE001
                    logger.exception("CommsPoller: failed to archive %s", request.request_id)

    async def _alert_stuck(self, bot, request: Request) -> None:
        """Fire the one-time stuck-request alert for ``request``.

        Persists ``delivery.stuck_alert_sent_at`` after a successful
        send so subsequent poll cycles don't re-alert. A failed
        Telegram send leaves the marker unset, so the next cycle
        retries — which is the right behaviour for an advisory
        notification (we'd rather double-alert than silently drop).
        """
        sent_at = (request.delivery or {}).get("sent_at") or "(unknown)"
        threshold_h = request.effective_stuck_alert_threshold_s() / 3600
        text = (
            f"⚠️ Comms request {request.request_id} is stuck in 'sent' "
            f"for >= {threshold_h:.1f}h (sent_at {sent_at}). Reply in "
            f"Telegram, or edit the artifact and set status back to "
            f"'pending' to re-deliver."
        )
        try:
            await bot.send_message(chat_id=self.chat_id, text=text)
        except TelegramError as exc:
            logger.error(
                "CommsPoller stuck-alert: telegram error for %s: %s",
                request.request_id, exc,
            )
            return
        delivery = dict(request.delivery)
        delivery["stuck_alert_sent_at"] = _utcnow_iso()
        request.delivery = delivery
        self.store.save(request)
        log_event(
            "stuck_alert_sent",
            request_id=request.request_id,
            actor="bot",
            details={"threshold_s": request.effective_stuck_alert_threshold_s()},
        )

    async def _alert_expired(self, bot, request: Request) -> None:
        """Fire a final alert just before the EXPIRED transition.

        Best-effort: a Telegram send failure does not block the
        transition — silent expiry is bad, but a transient network
        blip should not strand a request in ``sent`` forever. The
        transition log entry plus ``request_expired`` event remain
        the auditable record either way.

        Health-review topics short-circuit silently. The health-snapshot
        workflow no longer creates these requests (see 2026-05-12
        cleanup); any in-flight backlog should drain to EXPIRED without
        firing Telegram noise. Audit trail (transition log + request_expired
        event) is preserved.
        """
        if _is_health_review_topic(request.topic):
            logger.debug(
                "CommsPoller expiry-alert: skipping Telegram for health-review topic (%s)",
                request.request_id,
            )
            return
        text = (
            f"⏰ Comms request {request.request_id} expired without an "
            f"answer (expires_at {request.expires_at}). Marking EXPIRED."
        )
        try:
            await bot.send_message(chat_id=self.chat_id, text=text)
        except TelegramError as exc:
            logger.error(
                "CommsPoller expiry-alert: telegram error for %s: %s",
                request.request_id, exc,
            )

    async def _deliver(self, bot, request: Request) -> None:
        """Send each question as its own message; mark the request sent on success.

        Health-review topics short-circuit: the health-snapshot workflow
        no longer creates these requests (operator-driven flow per
        2026-05-12 cleanup — operator pulls the snapshot artifact from
        the Action UI and pastes into Claude directly). Any in-flight
        backlog gets marked sent without Telegram noise so the poll
        loop stops re-trying it; expiry then routes through
        _alert_expired which is also silent for this topic.
        """
        if _is_health_review_topic(request.topic):
            logger.info(
                "CommsPoller deliver: skipping Telegram for health-review topic "
                "(%s); marking sent without notification.",
                request.request_id,
            )
            self.store.mark_sent(
                request,
                telegram_chat_id=str(self.chat_id),
                telegram_message_id=None,
            )
            log_event(
                "request_sent",
                request_id=request.request_id,
                actor="bot",
                details={"questions": len(request.questions), "skipped_telegram": True},
            )
            return
        last_message_id: Optional[int] = None
        total = len(request.questions)
        for idx, question in enumerate(request.questions):
            text = render_question_text(request, question, idx=idx, total=total)
            keyboard = build_keyboard(request.request_id, question)
            try:
                msg = await bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
            except TelegramError as exc:
                logger.error("comms deliver: telegram error for %s/%s: %s",
                             request.request_id, question.question_id, exc)
                return
            last_message_id = msg.message_id

        self.store.mark_sent(
            request,
            telegram_chat_id=str(self.chat_id),
            telegram_message_id=last_message_id,
        )
        log_event(
            "request_sent",
            request_id=request.request_id,
            actor="bot",
            details={"questions": total},
        )


# ----------------------------------------------------------------------
# Callback parsing

def _cb(request_id: str, question_id: str, choice_id: str) -> str:
    """Build callback_data with the ``comms:`` prefix."""
    return f"{COMMS_CALLBACK_PREFIX}{request_id}:{question_id}:{choice_id}"


def parse_callback_data(data: str) -> Optional[tuple[str, str, str]]:
    """Inverse of ``_cb``. Returns ``(request_id, question_id, choice_id)`` or None.

    Defensive: a corrupt callback string never raises; the handler logs
    and ignores. ``request_id`` validates against the store on lookup
    so we don't trust this string for authorization.
    """
    if not data.startswith(COMMS_CALLBACK_PREFIX):
        return None
    rest = data[len(COMMS_CALLBACK_PREFIX):]
    parts = rest.split(":")
    if len(parts) != 3:
        return None
    request_id, question_id, choice_id = parts
    if not (request_id and question_id and choice_id):
        return None
    return request_id, question_id, choice_id


# ----------------------------------------------------------------------
# Authorization

def _resolve_allowed_chat_id(explicit: Optional[str] = None) -> Optional[int]:
    """The single operator chat the comms handlers accept answers from.

    Resolved from the explicit ``chat_id`` passed to ``install_comms_handlers``
    (which itself defaults to ``TELEGRAM_CHAT_ID``). Returns ``None`` when not
    configured (dev / tests) — see ``_chat_authorized`` for the fail-open note.
    """
    raw = explicit if explicit is not None else os.environ.get("TELEGRAM_CHAT_ID")
    raw = (str(raw) if raw is not None else "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "TELEGRAM_CHAT_ID=%r is not an integer — comms chat-id guard disabled",
            raw,
        )
        return None


def _chat_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True if the update comes from the configured operator chat.

    Closes the gap where the comms callback/text handlers accepted answers from
    ANY chat that reached the bot (the callback handler validated the
    ``request_id`` against the store but never the responder's identity — an
    unauthorized user who reached the bot could answer operator decision
    prompts). Fail-OPEN only when no chat id is configured (tests / a dev box
    that never set ``TELEGRAM_CHAT_ID``); in production the id is always set, so
    a foreign chat is rejected.
    """
    allowed = context.bot_data.get("comms_allowed_chat_id")
    if allowed is None:
        return True  # unconfigured → don't break tests/dev
    chat = update.effective_chat
    return chat is not None and chat.id == allowed


# ----------------------------------------------------------------------
# Callback + text handlers

async def comms_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle a button-tap on a comms menu.

    Expected callback_data shape: ``comms:<request_id>:<question_id>:<choice_id>``.
    Special ``choice_id == __OTHER__`` puts the chat into free-text capture
    mode; any other value is recorded as the answer immediately.
    """
    query = update.callback_query
    if query is None or query.data is None:
        return
    if not _chat_authorized(update, context):
        # An answer from an unauthorized chat is ignored (never recorded).
        await _safe_answer(query, "Not authorized.")
        return
    parsed = parse_callback_data(query.data)
    if parsed is None:
        await _safe_answer(query)
        return
    request_id, question_id, choice_id = parsed

    store: RequestStore = context.bot_data.get("comms_store") or RequestStore()
    try:
        request = store.load(request_id)
    except FileNotFoundError:
        await _safe_answer(query, "This question is no longer active.")
        return

    question = _find_question(request, question_id)
    if question is None:
        await _safe_answer(query, "Unknown question. Skipped.")
        return

    if choice_id == OTHER_CHOICE_ID:
        # Stash the awaiting state in the user_data scope and prompt for text.
        context.user_data[USERDATA_AWAITING_KEY] = {
            "request_id": request_id,
            "question_id": question_id,
        }
        await _safe_answer(query, "OK — type your reply.")
        await query.message.reply_text(
            f"Type your reply for question '{question_id}'."
        )
        return

    # Validate the choice is real (yes/no or one of the choices.id).
    valid_ids = _valid_choice_ids(question)
    if choice_id not in valid_ids:
        await _safe_answer(query, "Invalid choice.")
        return

    answer = Answer(
        question_id=question_id,
        answer_type=question.input_type if question.input_type != "free_text" else "choice",
        received_at=_utcnow_iso(),
        selected_ids=[choice_id],
    )
    operator = _operator_from_update(update)
    apply_answer(
        store=store,
        request=request,
        answer=answer,
        operator=operator,
    )
    await _safe_answer(query, "Got it ✅")
    await query.message.reply_text(
        f"Recorded: {question_id} → {choice_id}"
    )


async def comms_text_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Capture a free-text reply when the user is mid-``Other`` flow.

    No-ops if no comms flow is active for this user — does NOT consume
    the message, so other text-based features remain unaffected.
    """
    if not _chat_authorized(update, context):
        # Defense-in-depth: the MessageHandler is also chat-filtered at
        # registration, but guard here too in case it's wired without it.
        return
    awaiting = context.user_data.get(USERDATA_AWAITING_KEY)
    if not awaiting:
        return
    if update.message is None or update.message.text is None:
        return
    text = update.message.text.strip()
    if not text:
        return

    request_id = awaiting["request_id"]
    question_id = awaiting["question_id"]
    store: RequestStore = context.bot_data.get("comms_store") or RequestStore()
    try:
        request = store.load(request_id)
    except FileNotFoundError:
        context.user_data.pop(USERDATA_AWAITING_KEY, None)
        await update.message.reply_text("That request is no longer active.")
        return

    question = _find_question(request, question_id)
    if question is None:
        context.user_data.pop(USERDATA_AWAITING_KEY, None)
        return

    answer_type = "free_text" if question.input_type == "free_text" else "other"
    answer = Answer(
        question_id=question_id,
        answer_type=answer_type,
        received_at=_utcnow_iso(),
        free_text=text[:4000],  # tg message cap; defensive
    )
    operator = _operator_from_update(update)
    apply_answer(
        store=store,
        request=request,
        answer=answer,
        operator=operator,
    )
    context.user_data.pop(USERDATA_AWAITING_KEY, None)
    await update.message.reply_text(f"Recorded: {question_id} → free text ✅")


# ----------------------------------------------------------------------
# Apply answer + writeback

def apply_answer(
    *,
    store: RequestStore,
    request: Request,
    answer: Answer,
    operator: Optional[dict] = None,
    pusher: Optional["GitPusher"] = None,
) -> Request:
    """Merge a single answer into the request and decide the next state.

    Last-write-wins per question_id. Once every required question has
    an answer, transitions ``→ answered`` and triggers the git
    writeback. Otherwise transitions ``→ partially_answered``. The
    request is saved to disk before the git push so a push failure
    leaves a recoverable on-disk state.
    """
    existing_answers = list(request.response.answers) if request.response else []
    # Replace any previous answer for the same question_id.
    existing_answers = [a for a in existing_answers if a.question_id != answer.question_id]
    existing_answers.append(answer)

    answered_required = required_answered_count(request, existing_answers)
    target_status = next_status_after_answer(
        total_required=len(request.required_question_ids()),
        answered_required=answered_required,
    )
    answer_status = (
        ANSWER_STATUS.COMPLETE
        if target_status == STATUS.ANSWERED else ANSWER_STATUS.PARTIAL
    )
    response = Response(
        request_id=request.request_id,
        answered_at=answer.received_at,
        answers=existing_answers,
        status=answer_status,
        operator_telegram_user_id=(operator or {}).get("user_id"),
        operator_telegram_username=(operator or {}).get("username"),
    )
    request.response = response
    if target_status == request.status:
        # Re-answer of a question on a request that is already in the same
        # bucket (answered/partially_answered). No state transition — just
        # persist the updated response. The state machine forbids self-edges.
        store.save(request)
    else:
        store.attach_response(request, response, new_status=target_status)
    log_event(
        "answer_received",
        request_id=request.request_id,
        actor="operator",
        details={"question_id": answer.question_id, "status": answer_status},
    )

    if target_status == STATUS.ANSWERED:
        log_event("request_answered", request_id=request.request_id, actor="bot")
        pusher = pusher or GitPusher.from_env(store.root.parent)
        try:
            pusher.commit_and_push(
                files=[store.path_for(request.request_id)],
                message=f"{COMMS_COMMIT_PREFIX} {request.request_id}",
            )
        except GitPushError as exc:
            logger.error("comms writeback push failed for %s: %s", request.request_id, exc)
            log_event(
                "error",
                request_id=request.request_id,
                actor="bot",
                details={"stage": "push", "error": str(exc)},
            )
    return request


# ----------------------------------------------------------------------
# Git push helper (writeback)

class GitPushError(RuntimeError):
    pass


class GitPusher:
    """Subprocess wrapper around ``git add / commit / pull --rebase / push``.

    Designed for the comms writeback path on the VM. Disabled by
    default everywhere else (``COMMS_PUSH_ENABLED=0``) so unit tests,
    Claude's own sandbox, and any non-VM caller cannot accidentally
    push from a side-effect.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        enabled: bool = False,
        remote: str = "origin",
        branch: str = "main",
        max_retries: int = 3,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.enabled = bool(enabled)
        self.remote = remote
        self.branch = branch
        self.max_retries = int(max_retries)

    @classmethod
    def from_env(cls, repo_root: Path) -> "GitPusher":
        return cls(
            repo_root=repo_root,
            enabled=os.environ.get("COMMS_PUSH_ENABLED", "0") == "1",
            remote=os.environ.get("COMMS_GIT_REMOTE", "origin"),
            branch=os.environ.get("COMMS_GIT_BRANCH", "main"),
        )

    def commit_and_push(self, *, files: list[Path], message: str) -> None:
        if not self.enabled:
            logger.info("GitPusher disabled (COMMS_PUSH_ENABLED!=1); skipping push of %s", message)
            return
        rels = [str(p.resolve().relative_to(self.repo_root.resolve())) for p in files]
        self._run(["git", "add", "--", *rels])
        # Nothing-to-commit is fine — it just means the file is unchanged.
        try:
            self._run(["git", "commit", "-m", message])
        except GitPushError as exc:
            if "nothing to commit" in str(exc):
                logger.info("GitPusher: nothing to commit for %s; skipping push", message)
                return
            raise
        for attempt in range(1, self.max_retries + 1):
            try:
                self._run(["git", "pull", "--rebase", self.remote, self.branch])
                self._run(["git", "push", self.remote, self.branch])
                return
            except GitPushError as exc:
                if attempt >= self.max_retries:
                    raise
                logger.warning("GitPusher: push attempt %d failed (%s); retrying", attempt, exc)

    def _run(self, cmd: list[str]) -> None:
        try:
            subprocess.run(
                cmd,
                cwd=str(self.repo_root),
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            raise GitPushError(
                f"{' '.join(cmd)} failed: {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitPushError(f"{' '.join(cmd)} timed out") from exc


# ----------------------------------------------------------------------
# Wiring helpers

def install_comms_handlers(
    application: Application,
    *,
    repo_root: Optional[Path] = None,
    chat_id: Optional[str] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    backtest_consumer: Optional["BacktestConsumer"] = None,
) -> CommsPoller:
    """Register comms handlers + a poll task on the running Application.

    The store is stashed in ``application.bot_data["comms_store"]`` so
    handler functions can pick it up without import-time globals.
    Returns the ``CommsPoller`` so the caller can stop it on shutdown.

    ``backtest_consumer`` is the M5 strategy-test consumer; when
    omitted the wrapper instantiates a default one. Tests pass an
    explicit consumer (or ``None``-by-passing ``False`` is not
    supported here on purpose — to disable in tests, construct the
    poller directly).
    """
    repo_root = Path(repo_root) if repo_root else Path.cwd()
    store = RequestStore(repo_root / "comms")
    application.bot_data["comms_store"] = store

    # Authorization: only the configured operator chat may answer comms
    # prompts. Stashed for the callback handler (which can't take a `filters=`)
    # and AND-ed into the text handler's filter. Unset → fail-open (tests/dev).
    allowed_chat_id = _resolve_allowed_chat_id(chat_id)
    application.bot_data["comms_allowed_chat_id"] = allowed_chat_id
    if allowed_chat_id is None:
        logger.warning(
            "install_comms_handlers: no TELEGRAM_CHAT_ID configured; comms "
            "chat-id authorization is OPEN (expected only in tests/dev)."
        )

    application.add_handler(
        CallbackQueryHandler(comms_callback_handler, pattern=rf"^{COMMS_CALLBACK_PREFIX}"),
    )
    # Group 1 keeps the comms text handler from clashing with /commands or
    # other text consumers — it's a passive observer that no-ops unless
    # USERDATA_AWAITING_KEY is set.
    text_filter = filters.TEXT & ~filters.COMMAND
    if allowed_chat_id is not None:
        text_filter = text_filter & filters.Chat(chat_id=allowed_chat_id)
    application.add_handler(
        MessageHandler(text_filter, comms_text_handler),
        group=1,
    )

    if backtest_consumer is None:
        # Lazy import keeps the comms_handler module importable in
        # contexts that don't have the backtester deps available
        # (e.g. minimal CI test images).
        from src.bot.test_strategy_consumer import (
            BacktestConsumer as _BC,
            M5_CONSUMER_ENABLED_ENV,
        )
        # P2 env gate: only auto-install the consumer when the
        # operator has explicitly enabled it on this VM (default off
        # so a fresh checkout / dev box never auto-runs backtests).
        if os.environ.get(M5_CONSUMER_ENABLED_ENV, "0").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            backtest_consumer = _BC()
        else:
            logger.info(
                "install_comms_handlers: %s not set; M5 backtest consumer disabled",
                M5_CONSUMER_ENABLED_ENV,
            )

    poller = CommsPoller(
        store=store,
        repo_root=repo_root,
        chat_id=chat_id,
        poll_interval=poll_interval,
        backtest_consumer=backtest_consumer,
    )

    async def _post_init(app: Application) -> None:
        await poller.start(app)

    # Application.post_init is set on the builder; if one is already set,
    # chain it. Otherwise just register ours.
    existing = getattr(application, "post_init", None)
    if callable(existing):
        async def _chained(app):
            try:
                await existing(app)
            finally:
                await poller.start(app)
        application.post_init = _chained
    else:
        application.post_init = _post_init
    return poller


# ----------------------------------------------------------------------
# Helpers

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _find_question(request: Request, question_id: str) -> Optional[Question]:
    for q in request.questions:
        if q.question_id == question_id:
            return q
    return None


def _valid_choice_ids(question: Question) -> set[str]:
    if question.input_type == "yes_no":
        return {"yes", "no"}
    return {c.id for c in (question.choices or [])}


def _operator_from_update(update: Update) -> dict:
    user = update.effective_user
    if user is None:
        return {}
    return {
        "user_id": user.id,
        "username": user.username,
    }


async def _safe_answer(query, text: Optional[str] = None) -> None:
    """Call query.answer() defensively — it can fail on stale callbacks."""
    try:
        if text:
            await query.answer(text=text)
        else:
            await query.answer()
    except TelegramError as exc:
        logger.warning("comms callback answer failed: %s", exc)
