"""
Tests for src/bot/comms_handler.py.

The telegram-stub pattern matches tests/test_telegram_query_bot.py: install
fake telegram + telegram.ext modules into sys.modules before importing the
handler so the module loads without ``python-telegram-bot`` being available.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Stub telegram + telegram.ext before importing the handler.
# ---------------------------------------------------------------------------
for _mod in ("telegram", "telegram.ext", "telegram.error", "dotenv", "requests"):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: SimpleNamespace(args=a, kwargs=kw)
_tg.InlineKeyboardMarkup = lambda rows: SimpleNamespace(inline_keyboard=rows)

# tests/conftest.py establishes a shared ``_StubTelegramError`` on
# ``telegram.error.TelegramError``. Reuse it (s052 pattern) so
# comms_handler's frozen import binding stays consistent across the
# whole suite — overriding here re-binds the name AFTER comms_handler
# captured it, which strands the ``except TelegramError`` clause
# whenever another file raises a different class.
_tg_err = sys.modules["telegram.error"]
_FakeTelegramError = getattr(_tg_err, "TelegramError", None)
if not isinstance(_FakeTelegramError, type) or not issubclass(_FakeTelegramError, BaseException):
    class _FakeTelegramError(Exception):  # type: ignore[no-redef]
        pass

    _tg_err.TelegramError = _FakeTelegramError
_tg.error = _tg_err

_tg_ext = sys.modules["telegram.ext"]
_tg_ext.Application = MagicMock
_tg_ext.CallbackQueryHandler = MagicMock
_tg_ext.MessageHandler = MagicMock
_tg_ext.ContextTypes = MagicMock()
_tg_ext.ContextTypes.DEFAULT_TYPE = object


class _FakeFilters:
    TEXT = MagicMock()
    COMMAND = MagicMock()


_tg_ext.filters = _FakeFilters
sys.modules["telegram.ext"].filters = _FakeFilters

# Now import.
import pytest  # noqa: E402

from src.bot import comms_handler as ch  # noqa: E402
from src.comms import (  # noqa: E402
    ANSWER_STATUS,
    Answer,
    Choice,
    Question,
    Request,
    RequestStore,
    STATUS,
)


# ----------------------------------------------------------------------
# Fixtures

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_request(
    *,
    request_id: str = "REQ-20260502-143015-acctmode",
    questions: list[Question] | None = None,
    status: str = STATUS.PENDING,
) -> Request:
    return Request(
        request_id=request_id,
        questions=questions or [
            Question(
                question_id="mode",
                prompt="Default mode for the new account?",
                input_type="choice",
                choices=[Choice("live", "Live"), Choice("paper", "Paper")],
                allow_other=True,
            )
        ],
        topic="acct mode",
        status=status,
    )


@pytest.fixture
def store(tmp_path: Path) -> RequestStore:
    s = RequestStore(tmp_path / "comms")
    return s


# ----------------------------------------------------------------------
# Callback-data parsing

class TestParseCallbackData:
    def test_round_trip_choice(self):
        data = ch._cb("REQ-20260502-143015-acctmode", "mode", "live")
        assert data == "comms:REQ-20260502-143015-acctmode:mode:live"
        parsed = ch.parse_callback_data(data)
        assert parsed == ("REQ-20260502-143015-acctmode", "mode", "live")

    def test_round_trip_other(self):
        data = ch._cb("REQ-20260502-143015-acctmode", "mode", ch.OTHER_CHOICE_ID)
        parsed = ch.parse_callback_data(data)
        assert parsed[2] == ch.OTHER_CHOICE_ID

    @pytest.mark.parametrize("bad", [
        "",
        "comms:",
        "comms:REQ:Q",  # only 2 parts
        "comms:REQ:Q:C:extra",  # 4 parts
        "help_top",
        "comms:REQ::C",  # empty middle
        "signals_strat:vwap",
    ])
    def test_invalid_returns_none(self, bad):
        assert ch.parse_callback_data(bad) is None


# ----------------------------------------------------------------------
# Keyboard building

class TestBuildKeyboard:
    def test_yes_no(self):
        q = Question(question_id="ok", prompt="OK?", input_type="yes_no")
        kb = ch.build_keyboard("REQ-20260502-143015-acctmode", q)
        assert kb is not None
        # 1 row of 2 buttons
        assert len(kb.inline_keyboard) == 1
        assert len(kb.inline_keyboard[0]) == 2

    def test_choice_with_other(self):
        q = Question(
            question_id="mode",
            prompt="?",
            input_type="choice",
            choices=[Choice("a", "A"), Choice("b", "B"), Choice("c", "C")],
            allow_other=True,
        )
        kb = ch.build_keyboard("REQ-1", q)
        # 3 choices in 2 rows (2+1) + 1 row for Other = 3 rows
        assert len(kb.inline_keyboard) == 3
        # Last row is Other
        last = kb.inline_keyboard[-1][0]
        assert ch.OTHER_CHOICE_ID in last.kwargs["callback_data"]

    def test_free_text_returns_none(self):
        q = Question(question_id="why", prompt="Why?", input_type="free_text")
        assert ch.build_keyboard("REQ-1", q) is None

    def test_multi_choice(self):
        q = Question(
            question_id="venues",
            prompt="?",
            input_type="multi_choice",
            choices=[Choice("bb", "Bybit"), Choice("bn", "Binance")],
        )
        kb = ch.build_keyboard("REQ-1", q)
        assert len(kb.inline_keyboard) == 1
        assert len(kb.inline_keyboard[0]) == 2


# ----------------------------------------------------------------------
# render_question_text

class TestRenderQuestionText:
    def test_includes_request_id_and_topic(self):
        req = _build_request()
        txt = ch.render_question_text(req, req.questions[0], idx=0, total=1)
        assert req.request_id in txt
        assert "acct mode" in txt
        assert req.questions[0].prompt in txt

    def test_multi_question_shows_progress(self):
        req = _build_request(questions=[
            Question(question_id="a", prompt="A?", input_type="yes_no"),
            Question(question_id="b", prompt="B?", input_type="yes_no"),
        ])
        txt = ch.render_question_text(req, req.questions[1], idx=1, total=2)
        assert "Q2/2" in txt

    def test_context_only_on_first_question(self):
        req = _build_request(questions=[
            Question(question_id="a", prompt="A?", input_type="yes_no"),
            Question(question_id="b", prompt="B?", input_type="yes_no"),
        ])
        req.context = "long context block"
        first = ch.render_question_text(req, req.questions[0], idx=0, total=2)
        second = ch.render_question_text(req, req.questions[1], idx=1, total=2)
        assert "long context block" in first
        assert "long context block" not in second

    def test_free_text_hint(self):
        req = _build_request(questions=[
            Question(question_id="why", prompt="Why?", input_type="free_text"),
        ])
        txt = ch.render_question_text(req, req.questions[0], idx=0, total=1)
        assert "plain text" in txt.lower()


# ----------------------------------------------------------------------
# apply_answer — partial / complete transitions, last-write-wins

class TestApplyAnswer:
    def _sent_request(self, store: RequestStore, *, questions=None) -> Request:
        req = _build_request(questions=questions)
        store.create(req)
        store.mark_sent(req)
        return req

    def test_single_required_completes(self, store):
        req = self._sent_request(store)
        ans = Answer(
            question_id="mode",
            answer_type="choice",
            received_at=_now_iso(),
            selected_ids=["live"],
        )
        ch.apply_answer(store=store, request=req, answer=ans)
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.ANSWERED
        assert loaded.response.status == ANSWER_STATUS.COMPLETE
        assert loaded.response.answers[0].selected_ids == ["live"]

    def test_two_required_partial_then_complete(self, store):
        req = self._sent_request(store, questions=[
            Question(question_id="a", prompt="?", input_type="yes_no"),
            Question(question_id="b", prompt="?", input_type="yes_no"),
        ])
        first = Answer(question_id="a", answer_type="yes_no",
                       received_at=_now_iso(), selected_ids=["yes"])
        ch.apply_answer(store=store, request=req, answer=first)
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.PARTIALLY_ANSWERED
        assert loaded.response.status == ANSWER_STATUS.PARTIAL

        second = Answer(question_id="b", answer_type="yes_no",
                        received_at=_now_iso(), selected_ids=["no"])
        # Re-load before passing in (handler does this in production).
        ch.apply_answer(store=store, request=loaded, answer=second)
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.ANSWERED
        assert loaded.response.status == ANSWER_STATUS.COMPLETE
        assert {a.question_id for a in loaded.response.answers} == {"a", "b"}

    def test_last_write_wins_per_question(self, store):
        req = self._sent_request(store)
        first = Answer(question_id="mode", answer_type="choice",
                       received_at=_now_iso(), selected_ids=["live"])
        ch.apply_answer(store=store, request=req, answer=first)
        loaded = store.load(req.request_id)

        second = Answer(question_id="mode", answer_type="choice",
                        received_at=_now_iso(), selected_ids=["paper"])
        ch.apply_answer(store=store, request=loaded, answer=second)
        loaded = store.load(req.request_id)

        # Only one answer for the question_id, last write wins.
        mode_answers = [a for a in loaded.response.answers if a.question_id == "mode"]
        assert len(mode_answers) == 1
        assert mode_answers[0].selected_ids == ["paper"]

    def test_optional_questions_dont_block_completion(self, store):
        req = self._sent_request(store, questions=[
            Question(question_id="a", prompt="?", input_type="yes_no", required=True),
            Question(question_id="b", prompt="?", input_type="yes_no", required=False),
        ])
        first = Answer(question_id="a", answer_type="yes_no",
                       received_at=_now_iso(), selected_ids=["yes"])
        ch.apply_answer(store=store, request=req, answer=first)
        loaded = store.load(req.request_id)
        # Required satisfied even though optional 'b' is unanswered.
        assert loaded.status == STATUS.ANSWERED


# ----------------------------------------------------------------------
# GitPusher

class TestGitPusher:
    def test_disabled_is_noop(self, tmp_path: Path, monkeypatch):
        called = []

        def fake_run(cmd, **kw):
            called.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)
        pusher = ch.GitPusher(tmp_path, enabled=False)
        pusher.commit_and_push(files=[tmp_path / "x.json"], message="comms(response): X")
        assert called == []

    def test_from_env_default_disabled(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("COMMS_PUSH_ENABLED", raising=False)
        pusher = ch.GitPusher.from_env(tmp_path)
        assert pusher.enabled is False

    def test_from_env_enabled_when_flag_set(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("COMMS_PUSH_ENABLED", "1")
        pusher = ch.GitPusher.from_env(tmp_path)
        assert pusher.enabled is True


# ----------------------------------------------------------------------
# Poller delivery + expiry — wrap async tests in asyncio.run since
# pytest-asyncio is not installed in this sandbox.


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class TestCommsPollerDeliver:
    async def _impl_delivers_pending_and_marks_sent_async(self, store, monkeypatch):
        req = _build_request()
        store.create(req)
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))

        application = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(application)

        assert bot.send_message.called
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.SENT
        assert loaded.delivery["telegram_chat_id"] == "123"
        assert loaded.delivery["telegram_message_id"] == 42

    def test_delivers_pending_and_marks_sent(self, store, monkeypatch):
        _run(self._impl_delivers_pending_and_marks_sent_async(store, monkeypatch))

    async def _impl_skips_when_no_chat_id_async(self, store):
        req = _build_request()
        store.create(req)
        bot = MagicMock()
        bot.send_message = AsyncMock()

        application = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id=None)
        await poller.poll_once(application)

        assert not bot.send_message.called
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.PENDING

    def test_skips_when_no_chat_id(self, store):
        _run(self._impl_skips_when_no_chat_id_async(store))

    async def _impl_does_not_resend_already_sent_request_async(self, store):
        req = _build_request()
        store.create(req)
        store.mark_sent(req)  # → sent

        bot = MagicMock()
        bot.send_message = AsyncMock()
        application = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(application)

        assert not bot.send_message.called

    def test_does_not_resend_already_sent_request(self, store):
        _run(self._impl_does_not_resend_already_sent_request_async(store))

    async def _impl_expires_stale_request_async(self, store):
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        req = _build_request()
        req.expires_at = past
        store.create(req)
        store.mark_sent(req)

        bot = MagicMock()
        bot.send_message = AsyncMock()
        application = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(application)

        # Expired requests are archived.
        archived = store.load(req.request_id)
        assert archived.status == STATUS.EXPIRED

    def test_expires_stale_request(self, store):
        _run(self._impl_expires_stale_request_async(store))

    async def _impl_archives_terminal_requests_async(self, store):
        req = _build_request()
        store.create(req)
        store.transition(req, to_status=STATUS.CANCELLED, actor="claude")

        bot = MagicMock()
        bot.send_message = AsyncMock()
        application = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(application)

        # Cancelled requests should move to archive.
        active_files = list((store.root / "requests").glob("REQ-*.json"))
        archive_files = list((store.root / "archive").glob("REQ-*.json"))
        assert active_files == []
        assert len(archive_files) == 1


    def test_archives_terminal_requests(self, store):
        _run(self._impl_archives_terminal_requests_async(store))


# ----------------------------------------------------------------------
# Callback handler — happy path + invalid

class TestCallbackHandler:
    def _ctx(self, store: RequestStore) -> SimpleNamespace:
        return SimpleNamespace(
            bot_data={"comms_store": store},
            user_data={},
        )

    def _update_with_query(self, callback_data: str, *, message=None) -> SimpleNamespace:
        query = SimpleNamespace(
            data=callback_data,
            answer=AsyncMock(),
            message=message or SimpleNamespace(reply_text=AsyncMock()),
        )
        return SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=99, username="ben"),
        )

    def test_choice_records_answer(self, store):
        req = _build_request()
        store.create(req)
        store.mark_sent(req)

        ctx = self._ctx(store)
        update = self._update_with_query(ch._cb(req.request_id, "mode", "live"))
        _run(ch.comms_callback_handler(update, ctx))

        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.ANSWERED
        assert loaded.response.answers[0].selected_ids == ["live"]
        assert loaded.response.operator_telegram_user_id == 99
        assert update.callback_query.answer.called

    def test_invalid_choice_rejected(self, store):
        req = _build_request()
        store.create(req)
        store.mark_sent(req)

        ctx = self._ctx(store)
        update = self._update_with_query(ch._cb(req.request_id, "mode", "not_a_choice"))
        _run(ch.comms_callback_handler(update, ctx))

        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.SENT  # untouched
        assert loaded.response is None

    def test_other_button_starts_capture_state(self, store):
        req = _build_request()
        store.create(req)
        store.mark_sent(req)

        ctx = self._ctx(store)
        update = self._update_with_query(
            ch._cb(req.request_id, "mode", ch.OTHER_CHOICE_ID),
        )
        _run(ch.comms_callback_handler(update, ctx))

        # User data records the awaiting state.
        assert ctx.user_data[ch.USERDATA_AWAITING_KEY] == {
            "request_id": req.request_id,
            "question_id": "mode",
        }
        # Status unchanged until the operator types text.
        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.SENT

    def test_unknown_request_id_acks_and_returns(self, store):
        ctx = self._ctx(store)
        update = self._update_with_query(
            "comms:REQ-20260502-999999-missing0:mode:live"
        )
        _run(ch.comms_callback_handler(update, ctx))
        # Should call query.answer with a friendly message but not raise.
        assert update.callback_query.answer.called

    def test_malformed_callback_acks_silently(self, store):
        ctx = self._ctx(store)
        update = self._update_with_query("comms:garbage")
        _run(ch.comms_callback_handler(update, ctx))
        assert update.callback_query.answer.called


# ----------------------------------------------------------------------
# Free-text capture

class TestTextHandler:
    def test_free_text_captured_when_awaiting(self, store):
        req = _build_request()
        store.create(req)
        store.mark_sent(req)

        ctx = SimpleNamespace(
            bot_data={"comms_store": store},
            user_data={ch.USERDATA_AWAITING_KEY: {
                "request_id": req.request_id,
                "question_id": "mode",
            }},
        )
        update = SimpleNamespace(
            message=SimpleNamespace(text=" futures-only ", reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=99, username="ben"),
        )
        _run(ch.comms_text_handler(update, ctx))

        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.ANSWERED
        ans = loaded.response.answers[0]
        assert ans.answer_type == "other"
        assert ans.free_text == "futures-only"
        # Awaiting state is cleared.
        assert ch.USERDATA_AWAITING_KEY not in ctx.user_data

    def test_no_op_when_not_awaiting(self, store):
        req = _build_request()
        store.create(req)
        store.mark_sent(req)

        ctx = SimpleNamespace(bot_data={"comms_store": store}, user_data={})
        update = SimpleNamespace(
            message=SimpleNamespace(text="random chatter", reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=99, username="ben"),
        )
        _run(ch.comms_text_handler(update, ctx))

        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.SENT  # untouched

    def test_pure_free_text_question_records_as_free_text_type(self, store):
        req = _build_request(questions=[
            Question(question_id="ideas", prompt="What ideas?", input_type="free_text"),
        ])
        store.create(req)
        store.mark_sent(req)

        ctx = SimpleNamespace(
            bot_data={"comms_store": store},
            user_data={ch.USERDATA_AWAITING_KEY: {
                "request_id": req.request_id,
                "question_id": "ideas",
            }},
        )
        update = SimpleNamespace(
            message=SimpleNamespace(text="more vwap", reply_text=AsyncMock()),
            effective_user=SimpleNamespace(id=99, username="ben"),
        )
        _run(ch.comms_text_handler(update, ctx))

        loaded = store.load(req.request_id)
        assert loaded.response.answers[0].answer_type == "free_text"
        assert loaded.response.answers[0].free_text == "more vwap"
