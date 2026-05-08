"""Tests for the M1 P1-B stuck-request recovery alerts.

Pins:

  - ``Request.is_stuck`` honours per-request override + package default
    + missing-sent_at safety;
  - ``Request.stuck_alert_already_sent`` reads ``delivery``;
  - ``CommsPoller`` fires a one-time stuck alert and persists
    ``delivery.stuck_alert_sent_at``;
  - the stuck alert is *not* re-fired in subsequent poll cycles;
  - a request that hits ``expires_at`` fires a final alert and
    transitions to ``EXPIRED`` in the same pass;
  - the schema's ``stuck_alert_threshold`` field round-trips through
    ``Request.from_dict`` / ``to_dict``.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Stub telegram + telegram.ext before importing the handler. Mirrors
# tests/test_s027_comms_handler.py so the file is importable even when
# python-telegram-bot is not installed.
# ---------------------------------------------------------------------------
for _mod in ("telegram", "telegram.ext", "telegram.error", "dotenv", "requests"):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: SimpleNamespace(args=a, kwargs=kw)
_tg.InlineKeyboardMarkup = lambda rows: SimpleNamespace(inline_keyboard=rows)

# Reuse whatever ``TelegramError`` class already lives in
# ``sys.modules["telegram.error"]`` — another test module
# (e.g. ``test_s027_comms_handler``) may have stubbed it before us,
# and ``comms_handler`` imports the class at module load. If we
# defined a *new* class here, ``except TelegramError`` in
# comms_handler wouldn't catch our raised instance.
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

import pytest  # noqa: E402

from src.bot import comms_handler as ch  # noqa: E402
from src.comms import (  # noqa: E402
    Choice,
    Question,
    Request,
    RequestStore,
    STATUS,
)
from src.comms.models import (  # noqa: E402
    DEFAULT_STUCK_ALERT_THRESHOLD_S,
    MIN_STUCK_ALERT_THRESHOLD_S,
    CommsValidationError,
)


# ----------------------------------------------------------------------
# Helpers

def _build_request(**overrides) -> Request:
    kwargs: dict = dict(
        request_id="REQ-20260502-143015-acctmode",
        questions=[
            Question(
                question_id="mode",
                prompt="?",
                input_type="choice",
                choices=[Choice("live", "Live"), Choice("paper", "Paper")],
            )
        ],
        topic="acct mode",
    )
    kwargs.update(overrides)
    return Request(**kwargs)


@pytest.fixture
def store(tmp_path: Path) -> RequestStore:
    return RequestStore(tmp_path / "comms")


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# Schema / model — stuck_alert_threshold field

class TestStuckAlertThresholdField:
    def test_default_when_unset(self):
        req = _build_request()
        assert req.stuck_alert_threshold is None
        assert req.effective_stuck_alert_threshold_s() == DEFAULT_STUCK_ALERT_THRESHOLD_S

    def test_per_request_override(self):
        req = _build_request(stuck_alert_threshold=3600)
        assert req.effective_stuck_alert_threshold_s() == 3600

    def test_round_trip_to_dict(self):
        req = _build_request(stuck_alert_threshold=3600)
        d = req.to_dict()
        assert d["stuck_alert_threshold"] == 3600
        loaded = Request.from_dict(d)
        assert loaded.stuck_alert_threshold == 3600

    def test_omitted_when_default(self):
        req = _build_request()
        assert "stuck_alert_threshold" not in req.to_dict()

    def test_rejects_below_minimum(self):
        with pytest.raises(CommsValidationError):
            _build_request(stuck_alert_threshold=MIN_STUCK_ALERT_THRESHOLD_S - 1)

    def test_rejects_non_integer(self):
        with pytest.raises(CommsValidationError):
            _build_request(stuck_alert_threshold="3600")  # type: ignore[arg-type]


class TestIsStuck:
    def _sent(self, sent_at_iso: str, **req_kw) -> Request:
        req = _build_request(**req_kw)
        req.delivery = {"sent_at": sent_at_iso, "send_attempts": 1}
        req.status = STATUS.SENT
        return req

    def test_no_sent_at_means_not_stuck(self):
        req = _build_request()
        assert req.is_stuck() is False

    def test_below_threshold(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        req = self._sent(recent, stuck_alert_threshold=3600)
        assert req.is_stuck() is False

    def test_at_or_past_threshold(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=4000)).isoformat()
        req = self._sent(old, stuck_alert_threshold=3600)
        assert req.is_stuck() is True

    def test_uses_default_threshold_when_unset(self):
        # 24h + 1s = stuck under the package default.
        old = (
            datetime.now(timezone.utc)
            - timedelta(seconds=DEFAULT_STUCK_ALERT_THRESHOLD_S + 1)
        ).isoformat()
        req = self._sent(old)
        assert req.is_stuck() is True

    def test_malformed_sent_at_means_not_stuck(self):
        req = self._sent("not-a-date", stuck_alert_threshold=3600)
        assert req.is_stuck() is False


# ----------------------------------------------------------------------
# Poller integration

def _stuck_request(store: RequestStore, *, threshold_s: int = 3600) -> Request:
    req = _build_request(stuck_alert_threshold=threshold_s)
    store.create(req)
    store.mark_sent(req, telegram_chat_id="123", telegram_message_id=1)
    # Force sent_at into the past so the next poll sees a stuck request.
    req = store.load(req.request_id)
    req.delivery["sent_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=threshold_s + 60)
    ).isoformat(timespec="seconds")
    store.save(req)
    return req


class TestPollerStuckAlert:
    async def _impl_fires_stuck_alert_once(self, store: RequestStore):
        req = _stuck_request(store, threshold_s=3600)

        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=99))
        app = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(app)

        assert bot.send_message.called, "expected a stuck-request Telegram alert"
        sent_text = bot.send_message.call_args.kwargs["text"]
        assert req.request_id in sent_text
        assert "stuck" in sent_text.lower()

        loaded = store.load(req.request_id)
        assert loaded.delivery.get("stuck_alert_sent_at"), \
            "stuck_alert_sent_at must be persisted after a successful alert"
        assert loaded.status == STATUS.SENT, \
            "stuck alert must NOT advance status (M1 P1-B: advisory only)"

    def test_fires_stuck_alert_once(self, store: RequestStore):
        _run(self._impl_fires_stuck_alert_once(store))

    async def _impl_does_not_realert(self, store: RequestStore):
        _stuck_request(store, threshold_s=3600)

        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=99))
        app = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(app)
        await poller.poll_once(app)  # second cycle must be silent

        assert bot.send_message.call_count == 1, (
            "stuck alert is one-time-per-request; second poll must not re-alert"
        )

    def test_does_not_realert(self, store: RequestStore):
        _run(self._impl_does_not_realert(store))

    async def _impl_telegram_failure_leaves_marker_unset(self, store: RequestStore):
        req = _stuck_request(store, threshold_s=3600)

        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=_FakeTelegramError("boom"))
        app = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(app)

        loaded = store.load(req.request_id)
        assert "stuck_alert_sent_at" not in loaded.delivery, (
            "marker must NOT be persisted if the Telegram send failed"
        )

    def test_telegram_failure_leaves_marker_unset(self, store: RequestStore):
        _run(self._impl_telegram_failure_leaves_marker_unset(store))


class TestPollerExpiryAlert:
    async def _impl_fires_alert_then_expires(self, store: RequestStore):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        req = _build_request(expires_at=past)
        store.create(req)
        store.mark_sent(req, telegram_chat_id="123", telegram_message_id=1)

        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))
        app = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(app)

        # Expiry alert must have been sent.
        assert bot.send_message.called
        sent_text = bot.send_message.call_args.kwargs["text"]
        assert req.request_id in sent_text
        assert "expired" in sent_text.lower()

        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.EXPIRED, \
            "expiry alert must always be followed by the EXPIRED transition"

    def test_fires_alert_then_expires(self, store: RequestStore):
        _run(self._impl_fires_alert_then_expires(store))

    async def _impl_telegram_failure_still_expires(self, store: RequestStore):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        req = _build_request(expires_at=past)
        store.create(req)
        store.mark_sent(req, telegram_chat_id="123", telegram_message_id=1)

        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=_FakeTelegramError("boom"))
        app = SimpleNamespace(bot=bot, bot_data={})
        poller = ch.CommsPoller(store=store, repo_root=store.root.parent, chat_id="123")
        await poller.poll_once(app)

        loaded = store.load(req.request_id)
        assert loaded.status == STATUS.EXPIRED, (
            "transient Telegram failure must not strand a request in 'sent' "
            "(silent expiry would be worse than a missed alert)"
        )

    def test_telegram_failure_still_expires(self, store: RequestStore):
        _run(self._impl_telegram_failure_still_expires(store))
