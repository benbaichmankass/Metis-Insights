"""Tests for the `/status` + `/decisions` command wiring (`src.bot.operator_commands`).

Telegram is stubbed centrally by ``tests/conftest.py``, so these assert on the
TEXT that reaches the operator and on which handlers get registered — not on
keyboard internals.

The claims worth pinning here are the wiring ones, because the two readouts are
tested where they live (`test_manager_status.py`, `test_telegram_decisions.py`):

* the repo is PUBLIC and the bot username is discoverable, so an unrecognised
  chat gets a REFUSAL and never a status dump — and the refusal is LOGGED;
* the auth check is the bot's EXISTING one, not a second notion of authorised;
* a route that does not resolve still REGISTERS the commands, because a
  silently absent command is indistinguishable from a broken one;
* the raw keyboard dict is adapted for transport WITHOUT this module ever
  constructing a `callback_data`.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot import operator_commands as oc


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _update(chat_id="365546917"):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.callback_query = None
    upd.message.reply_text = AsyncMock()
    return upd


def _texts(update):
    return [c.args[0] for c in update.message.reply_text.call_args_list]


ALLOW = lambda update: True          # noqa: E731
DENY = lambda update: False          # noqa: E731


# ═════════════════════════════════════════════════════════════════════════════
# Authorization — both commands expose internal state on a PUBLIC repo
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("command", ["status", "decisions"])
def test_an_unrecognised_chat_gets_a_refusal_not_a_dump(command, caplog):
    handlers = oc.build_handlers(DENY)
    upd = _update(chat_id="99999")
    with caplog.at_level(logging.WARNING):
        _run(handlers[command](upd, MagicMock()))

    assert _texts(upd) == ["⛔ Unauthorised."]
    # The refusal is LOGGED, and names the command and the chat that made it.
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "REFUSED" in joined
    assert command in joined and "99999" in joined


@pytest.mark.parametrize("command", ["status", "decisions"])
def test_a_refused_command_never_reads_any_state(command, monkeypatch):
    """The auth gate must come BEFORE the read, not after it."""
    import src.runtime.manager_status as ms
    import src.runtime.telegram_decisions as td

    def boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("state was read for an unauthorised chat")

    monkeypatch.setattr(ms, "build_status", boom)
    monkeypatch.setattr(td, "build_on_demand_decisions", boom)

    handlers = oc.build_handlers(DENY)
    _run(handlers[command](_update(chat_id="99999"), MagicMock()))


def test_the_auth_predicate_is_the_callers_and_is_not_re_derived():
    """A second notion of 'authorised' is one that can drift from the first."""
    seen = []

    def predicate(update):
        seen.append(update)
        return False

    handlers = oc.build_handlers(predicate)
    upd = _update()
    _run(handlers["status"](upd, MagicMock()))
    assert seen == [upd], "the injected predicate decided, nothing else"


def test_the_bots_own_is_authorised_is_what_gets_wired():
    """`main()` passes `claude_decision_bot._is_authorised`, not a second copy.

    Checked at the CALL SITE by AST rather than by calling `main()` (which
    would start polling). An independently-written auth predicate here is the
    drift this asserts against: `on_callback` already gates on
    `_is_authorised`, and these commands must gate on the same one.

    ⚠️ The call site is the CLAUDE bot, not the trader bot — see
    `test_the_trader_bot_does_not_register_the_operator_commands`.
    """
    import ast
    import inspect

    from src.bot import claude_decision_bot as bot

    tree = ast.parse(inspect.getsource(bot))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "install_operator_commands"
    ]
    assert len(calls) == 1, "wired exactly once"
    kwargs = {kw.arg: kw.value for kw in calls[0].keywords}
    assert isinstance(kwargs["is_authorised"], ast.Name)
    assert kwargs["is_authorised"].id == "_is_authorised", (
        "the commands must gate on the bot's existing predicate")
    # And it is the same object `on_callback` uses.
    assert callable(bot._is_authorised)


def test_the_commands_ride_the_token_this_process_actually_polls():
    """`polled_token` must be the token passed to `Application.builder()`.

    The whole `answerable_here` / `answerable_elsewhere` distinction is only
    as true as this argument. A `polled_token` naming some OTHER token would
    make the module report a route state that is not this process's, which is
    precisely how #10793 shipped a WARNING nobody acted on.
    """
    import ast
    import inspect

    from src.bot import claude_decision_bot as bot

    tree = ast.parse(inspect.getsource(bot))
    call = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "install_operator_commands"
    )
    polled = {kw.arg: kw.value for kw in call.keywords}["polled_token"]
    # `route.token` — the same expression handed to Application.builder().token(...)
    assert isinstance(polled, ast.Attribute) and polled.attr == "token"
    assert isinstance(polled.value, ast.Name) and polled.value.id == "route"

    builder_tokens = [
        node.args[0] for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "token"
        and node.args
    ]
    assert any(
        isinstance(a, ast.Attribute) and a.attr == "token"
        and isinstance(a.value, ast.Name) and a.value.id == "route"
        for a in builder_tokens
    ), "the polled token and the built token must be the same expression"


def test_the_trader_bot_does_not_register_the_operator_commands():
    """⚠️ THE CONSTRAINT (MI-92). #10793: "HELD, must not reach the trader bot."

    PR #10793 registered `/status` and `/decisions` on the trader bot and was
    auto-merged with no human decision. The handlers went live on the trader
    bot at 2026-09-02T18:59:03Z — this module's own `answerable_elsewhere`
    WARNING naming the violation as it did it — and were re-observed still
    live at 2026-09-03T07:07:08Z. The operator: *"that's supposed to be
    showing up in Cloudbot. Right? Not on the trader one, the decisions."*

    Asserted by AST over the trader bot's source, so it fails on the IMPORT
    too: a module that imports the installer is one line from calling it, and
    the next session to add "just one command" should meet this test rather
    than the live bot. Deleting this test to re-add the call is the shape this
    guards against — the constraint is the operator's, and only a recorded
    operator decision withdraws it.
    """
    import ast
    import inspect

    from src.bot import telegram_query_bot as trader

    tree = ast.parse(inspect.getsource(trader))

    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "install_operator_commands" not in called, (
        "the trader bot must NOT register /status or /decisions — #10793's own "
        "title says so"
    )

    imported = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) for alias in node.names
    }
    assert "install_operator_commands" not in imported
    assert "OPERATOR_COMMANDS" not in imported

    # …and the commands are absent from the surface the operator is SHOWN.
    assert {name for name, _ in trader._COMMAND_SURFACE} == {"start", "menu"}


def test_the_trader_bot_still_handles_the_decision_tap():
    """The `wdec` FALLBACK is deliberately untouched — do not "finish the job".

    #10793's violation was the COMMAND registration. The trader bot remains
    the declared fallback destination for decision prompts when the Claude bot
    is not confirmed polling, and `telegram_decisions.answerable_route()`
    needs positive evidence a tap here is received before it will send buttons
    there. Removing this branch would ship dead buttons that look healthy —
    strictly worse than the wrong chat, per `claude_decision_bot`'s docstring.
    """
    import inspect

    from src.bot import telegram_query_bot as trader

    src = inspect.getsource(trader)
    assert 'elif action == "wdec":' in src
    assert "propexp" in src


# ═════════════════════════════════════════════════════════════════════════════
# `/status`
# ═════════════════════════════════════════════════════════════════════════════


def test_status_sends_every_message_of_a_chunked_readout(monkeypatch):
    # collapsed-state: synced — this file tests command WIRING, not tree
    # grading. The TreeProvenance below is an inert fixture value for a stubbed
    # readout, so it names one state without branching on any. The three states
    # are exercised where they mean something: reachability and rendering in
    # tests/test_manager_status.py, and their operator-facing consequences in
    # tests/test_telegram_decisions.py (a behind_main tree warns that an
    # answered question may still read unanswered).
    import src.runtime.manager_status as ms

    readout = ms.StatusReadout(
        messages=["part one", "part two"], omissions=[],
        tree=ms.TreeProvenance(state=ms.TREE_SYNCED), checklist_read="read",
        sessions_read="read",
    )
    monkeypatch.setattr(ms, "build_status", lambda *a, **k: readout)

    upd = _update()
    _run(oc.build_handlers(ALLOW)["status"](upd, MagicMock()))
    assert _texts(upd) == ["part one", "part two"]


def test_status_reports_the_real_checklist_without_raising():
    """An end-to-end smoke over the repo's own checklist."""
    upd = _update()
    _run(oc.build_handlers(ALLOW)["status"](upd, MagicMock()))
    bodies = _texts(upd)
    assert bodies, "a status must always reply"
    assert "MANAGER STATUS" in bodies[0]
    for body in bodies:
        from src.runtime.manager_status import TELEGRAM_MESSAGE_LIMIT
        assert len(body) <= TELEGRAM_MESSAGE_LIMIT


# ═════════════════════════════════════════════════════════════════════════════
# `/decisions`
# ═════════════════════════════════════════════════════════════════════════════


def test_decisions_sends_the_summary_and_each_prompt(monkeypatch):
    import src.runtime.telegram_decisions as td

    outbound = [
        ("summary", None),
        ("question one", {"inline_keyboard": [[
            {"text": "Yes", "callback_data": "wdec:aaaaaaaaaaaa:bbbbbbbb"}]]}),
    ]
    monkeypatch.setattr(td, "build_on_demand_decisions", lambda *a, **k: outbound)

    upd = _update()
    _run(oc.build_handlers(ALLOW)["decisions"](upd, MagicMock()))
    assert _texts(upd) == ["summary", "question one"]
    # The summary carries no markup; the question does.
    calls = upd.message.reply_text.call_args_list
    assert calls[0].kwargs["reply_markup"] is None
    assert calls[1].kwargs["reply_markup"] is not None


# ═════════════════════════════════════════════════════════════════════════════
# Keyboard transport adaptation
# ═════════════════════════════════════════════════════════════════════════════


def test_none_and_empty_keyboards_stay_none():
    """A request with no options must not render an empty, tappable-looking row."""
    assert oc._to_markup(None) is None
    assert oc._to_markup({}) is None
    assert oc._to_markup({"inline_keyboard": []}) is None


def test_the_adapter_never_constructs_a_callback_data():
    """`build_decision_keyboard` stays the single owner of every callback_data.

    Asserted against the SOURCE: a second author for these 26 bytes is how the
    64-byte budget and the option key-digest scheme drift apart.
    """
    import inspect

    src = inspect.getsource(oc)
    assert "CB_PREFIX" not in src
    assert "encode_callback" not in src
    # It only ever passes through a value it was handed.
    assert 'callback_data=b["callback_data"]' in src


# ═════════════════════════════════════════════════════════════════════════════
# Registration — the route is resolved, never hardcoded
# ═════════════════════════════════════════════════════════════════════════════


def _app():
    app = MagicMock()
    app.handlers = []
    app.add_handler = app.handlers.append
    return app


def test_registration_resolves_the_answerable_route(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "trader-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "365546917")
    app = _app()
    result = oc.install_operator_commands(
        app, is_authorised=ALLOW, polled_token="trader-token")
    assert result["route_state"] == "answerable_here"
    assert result["registered"] == ["decisions", "status"]
    assert len(app.handlers) == 2


def test_a_route_on_another_bot_is_reported_not_assumed_away(monkeypatch, caplog):
    """MI-58 is deliberately changing which bot is polled."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "answerable-elsewhere")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "365546917")
    app = _app()
    with caplog.at_level(logging.WARNING):
        result = oc.install_operator_commands(
            app, is_authorised=ALLOW, polled_token="this-process-polls-this")
    assert result["route_state"] == "answerable_elsewhere"
    # Still registered — the command must answer wherever it is received.
    assert len(app.handlers) == 2
    assert "NOT the bot this process polls" in " ".join(
        r.getMessage() for r in caplog.records)


def test_no_resolvable_route_warns_and_still_registers(monkeypatch, caplog):
    """`could_not_look`: logged as a WARNING naming it, never silently nothing."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    app = _app()
    with caplog.at_level(logging.WARNING):
        result = oc.install_operator_commands(app, is_authorised=ALLOW)

    assert result["route_state"] == "could_not_look"
    assert len(app.handlers) == 2, (
        "a silently absent command is indistinguishable from a broken one")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "no answerable bot resolved" in logged
    assert "registering" in logged


def test_a_raising_route_resolver_never_blocks_registration(monkeypatch):
    import src.runtime.telegram_decisions as td

    def boom():
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(td, "answerable_route", boom)
    app = _app()
    result = oc.install_operator_commands(app, is_authorised=ALLOW)
    assert result["route_state"] == "could_not_look"
    assert len(app.handlers) == 2


def test_registration_never_hardcodes_a_token_or_uses_claude_route():
    """A prompt sent to `claude_route()` renders buttons that go nowhere.

    Asserted over CODE only. The module docstring names `claude_route` to
    explain why it is not used, and a naive substring check over the whole
    source would forbid documenting the very trap this test guards — the shape
    where a guard is satisfied by deleting the explanation.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(oc))
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "claude_route" not in called
    assert "answerable_route" in called

    imported = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) for alias in node.names
    }
    assert "claude_route" not in imported
    assert "answerable_route" in imported

    # No token value is ever read directly — the resolver owns that.
    env_reads = {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("get", "getenv")
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    assert not {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CLAUDE_BOT_SECRET"} & env_reads


def test_the_commands_appear_in_the_hamburger_menu():
    """A command absent from `set_my_commands` is one the operator must already
    know exists in order to use.

    On the CLAUDE bot — the menu moved with the handlers. Leaving the menu
    entry on the trader bot would advertise a command that is no longer there,
    which is the same "silently broken" state this whole change exists to
    avoid, just pointed the other way.
    """
    from src.bot import claude_decision_bot as bot

    names = {name for name, _ in bot._COMMAND_SURFACE}
    assert names == {"start", "status", "decisions"}
    for _name, desc in bot._COMMAND_SURFACE:
        assert 1 <= len(desc) <= 80, "Telegram's set_my_commands limit"


# ═════════════════════════════════════════════════════════════════════════════
# The ClaudeBot command MENU (MI-92 follow-up, 2026-09-03)
#
# Operator: "I want the menu to open up on the menu button in the chat, not just
# by typing." `/status` ANSWERED at that point — the handlers were fine and the
# discoverable surface was not.
# ═════════════════════════════════════════════════════════════════════════════


class _FakeBot:
    """Records the calls `publish_command_surface` makes and what it read back.

    Deliberately NOT a MagicMock: the whole point of these tests is WHICH api
    was called with WHICH scope, and an auto-speccing mock answers every
    attribute, so a typo'd method name would pass silently.
    """

    def __init__(self, menu_type="default", fail_commands=False, fail_menu=False):
        self.fail_commands = fail_commands
        self.fail_menu = fail_menu
        self._menu_type = menu_type
        self.set_calls = []          # (commands, scope-type-or-None)
        self.menu_set_calls = []

    async def set_my_commands(self, commands, scope=None, **kw):
        if self.fail_commands:
            raise RuntimeError("telegram said no")
        self.set_calls.append(
            ([c.command for c in commands], getattr(scope, "type", None)))

    async def get_my_commands(self, scope=None, **kw):
        if self.fail_commands:
            raise RuntimeError("telegram said no")
        from telegram import BotCommand
        return [BotCommand(n, d) for n, d in _claudebot()._COMMAND_SURFACE]

    async def get_chat_menu_button(self, chat_id=None, **kw):
        if self.fail_menu:
            raise RuntimeError("telegram said no")
        from telegram import (
            MenuButtonCommands,
            MenuButtonDefault,
            MenuButtonWebApp,
            WebAppInfo,
        )
        if self._menu_type == "commands":
            return MenuButtonCommands()
        if self._menu_type == "web_app":
            # The state that ACTUALLY hides the command list, and the reason
            # the `before` read exists: a BotFather setting no code overrode.
            return MenuButtonWebApp(
                text="open", web_app=WebAppInfo(url="https://example.invalid"))
        return MenuButtonDefault()

    async def set_chat_menu_button(self, chat_id=None, menu_button=None, **kw):
        if self.fail_menu:
            raise RuntimeError("telegram said no")
        self.menu_set_calls.append((chat_id, getattr(menu_button, "type", None)))
        self._menu_type = getattr(menu_button, "type", None)


def _claudebot():
    from src.bot import claude_decision_bot as bot
    return bot


def test_the_menu_button_is_set_to_commands_not_left_to_a_default():
    """The operator's actual complaint: the button did not OFFER the commands.

    Publishing a command list and making the composer's Menu button show it are
    two different Bot-API calls. Nothing in this repo had ever called
    `setChatMenuButton`, so the button was whatever BotFather left it as — and a
    `web_app` button lists no commands however correctly they are published.
    """
    bot = _claudebot()
    fake = _FakeBot()
    out = _run(bot.publish_command_surface(fake))

    assert fake.menu_set_calls, "setChatMenuButton was never called"
    chat_id, button_type = fake.menu_set_calls[-1]
    assert button_type == "commands"
    assert chat_id is None, (
        "omitting chat_id sets the DEFAULT button for every private chat; "
        "passing one would fix a single chat and leave the default untouched")
    assert out["menu_state"] == "set"


def test_commands_are_published_to_all_private_chats_not_only_the_default_scope():
    """Telegram resolves a private chat most-specific-first.

    `all_private_chats` is MORE specific than the default scope, and an empty
    specific scope does not fall back — it wins. Publishing only to the default
    scope is therefore not equivalent.
    """
    bot = _claudebot()
    fake = _FakeBot()
    _run(bot.publish_command_surface(fake))

    scopes = {scope for _cmds, scope in fake.set_calls}
    assert "all_private_chats" in scopes, (
        "the operator's DM is governed by all_private_chats when it is set")
    assert None in scopes, "the default scope is still set, for non-DM surfaces"
    for cmds, _scope in fake.set_calls:
        assert set(cmds) == {"start", "status", "decisions"}


def test_the_outcome_is_read_BACK_from_telegram_never_asserted():
    """The defect that made this un-diagnosable was silence on the success path.

    A startup that never ran the call and one that ran it perfectly rendered
    IDENTICALLY in the journal. The `before` read is what names the cause.
    """
    bot = _claudebot()
    fake = _FakeBot(menu_type="web_app")
    out = _run(bot.publish_command_surface(fake))

    assert out["commands_live"] == ["start", "status", "decisions"], (
        "commands_live must come from get_my_commands, not from what we sent")
    assert out["menu_before"] == "web_app"
    assert out["menu_after"] == "commands"


@pytest.mark.parametrize(
    "kwargs,failed_key,intact_key",
    [
        ({"fail_menu": True}, "menu_state", "commands_state"),
        ({"fail_commands": True}, "commands_state", "menu_state"),
    ],
)
def test_one_call_failing_never_takes_down_the_other_or_the_bot(
    kwargs, failed_key, intact_key, caplog
):
    """⚠️ THE SAFETY PROPERTY. `post_init` runs inside `run_polling`.

    Raising there aborts startup, and under `Restart=always` that turns a
    COSMETIC menu problem into a crash-loop on the channel carrying the
    operator's DECISIONS. So a failure is logged at ERROR and swallowed — and
    the two calls are independently wrapped so one cannot suppress the other.
    """
    bot = _claudebot()
    fake = _FakeBot(**kwargs)
    with caplog.at_level(logging.ERROR):
        out = _run(bot.publish_command_surface(fake))   # must not raise

    assert out[failed_key].endswith("_failed")
    assert not out[intact_key].endswith("_failed"), (
        "the two API calls must be wrapped independently")
    assert caplog.records, "a swallowed failure must still be LOUD"


def test_post_init_is_actually_reachable_and_calls_the_published_surface():
    """`app.post_init = ...` after `.build()` must really be honoured.

    This was the branch that could not be told apart from a silent success, so
    it is pinned rather than assumed: `post_init` is a plain slot attribute on
    `Application` (not a read-only property), and `run_polling` awaits it.
    """
    import ast
    import inspect

    from telegram.ext import Application

    assert not isinstance(getattr(Application, "post_init", None), property), (
        "a property without a setter would make the assignment silently useless")

    bot = _claudebot()
    tree = ast.parse(inspect.getsource(bot))
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "publish_command_surface" in called, (
        "main()'s post_init must delegate to the tested function, not inline a "
        "second copy of the logic")


def test_both_command_scopes_are_read_BEFORE_writing_so_a_shadow_is_visible():
    """Which scope was already populated is the thing that names the cause.

    Telegram resolves a private chat most-specific-first and takes the first
    NON-EMPTY list, so an empty `all_private_chats` falls back to default
    correctly — but a NON-empty one SHADOWS default, and from inside this
    process that is invisible until it is read. Reading both before writing is
    what turns "the menu is empty" into a diagnosis instead of a guess.
    """
    bot = _claudebot()
    fake = _FakeBot()
    out = _run(bot.publish_command_surface(fake))

    assert set(out["commands_before"]) == {"default", "all_private_chats"}, (
        "both scopes in the private-chat resolution chain must be recorded")
