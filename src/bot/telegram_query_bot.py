import json
import os
import logging
import re
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Sprint S-001 PR-C..F: route data access through the data_loaders facade.
# Sprint S-002 M3: get_strategy_label is account-aware; load_account_env and
# format_target_options deleted.
from src.units.ui import data_loaders as dl
from src.bot.vm_runner import handle_vm_command, RunnerResult, MAX_PROMPT_CHARS
from src.bot.comms_handler import (
    GitPusher,
    GitPushError,
    install_comms_handlers,
)
# PR-4: trade formatting + cloud/VM helpers extracted to separate modules.
from src.bot.trade_notifier import (
    _duplicate_key_warning,
    _render_account_balance,
    _render_account_positions,
    fetch_open_positions_count,
    fetch_today_pnl,
    format_backtest_summary,
    get_strategy_label,
)
from src.bot.cloud_notifier import (
    PENDING_PINGS_DIR,
    PING_DRAIN_INTERVAL_S,
    _drain_pending_pings,
    get_service_status,
    toggle_service,
)
# D3 / PR-10: private helpers extracted to cohesive helper modules.
# Re-imported here so call sites (callback_handler, cmd_*) work unchanged.
from src.bot.signal_helpers import (  # noqa: E402
    _signals_strategy_keyboard,
    _signals_n_keyboard,
)
from src.bot.account_helpers import (  # noqa: E402
    _render_accounts_listing,
    _accounts_toggle_keyboard,
    _accounts_confirm_keyboard,
    _render_risk_check_for_account,
    _account_picker_keyboard,
    _render_smoke_test_result,
    _run_smoke_test,
)
from src.bot.trade_helpers import (  # noqa: E402
    _format_trade_row,  # noqa: F401 (re-export: tests access via bot._format_trade_row)
    _CLOSE_BUTTON_LABELS,
    _render_closeall_results,
    _do_closeall_strategy,
    _format_strategies_dashboard,
)

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
from src.utils.paths import repo_root as _repo_root  # noqa: E402
from src.utils.paths import trade_journal_db_path as _trade_journal_db_path  # noqa: E402
REPO_ROOT = _repo_root()
# DB_PATH: canonical resolver first (TRADE_JOURNAL_DB env →
# $DATA_DIR/trade_journal.db → repo-root), then repo root as the
# existence-check fallback.
_DB_CANDIDATES = [
    _trade_journal_db_path(),
    os.path.join(REPO_ROOT, "trade_journal.db"),
]
DB_PATH = next((p for p in _DB_CANDIDATES if p and os.path.exists(p)), _trade_journal_db_path())

# Fallback service name used when dl.list_accounts() returns no accounts.
# Multi-account deployments use per-account service keys from list_accounts().
LIVE_SERVICE_NAME = "ict-trader-live"

BACKTESTER_PATH = os.path.join(os.path.dirname(BASE_DIR), "backtest", "run_backtest.py")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HALT_FLAG_PATH = "/tmp/trader_halt.flag"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# S-017 T1 — match what the live trader (src/main.py) already does:
# install the token-redacting filter on the root logger, and demote
# httpx/httpcore to WARNING so the bot token doesn't appear in plaintext
# in ``journalctl -u ict-telegram-bot``. python-telegram-bot uses httpx
# under the hood and httpx logs every outgoing URL at INFO. Operator-
# flagged in CP-2026-04-30-05; until S-017 only the trader process had
# this protection — the bot process leaked.
from src.utils.log_redact import install_redacting_filter, suppress_httpx_logging  # noqa: E402
install_redacting_filter()
suppress_httpx_logging()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Coordinator singleton (S-008 PR #124 — Telegram Bot rewired)
# ---------------------------------------------------------------------------
# All cross-unit data flows through the Coordinator (TRANSLATOR).  The bot
# is a pure consumer: it reads from dashboard_stats() / recent_signals() and
# writes return commands through return_command().
# ---------------------------------------------------------------------------

_coordinator = None


def get_coordinator():
    """Return the module-level Coordinator singleton (lazy-initialised)."""
    global _coordinator
    if _coordinator is None:
        try:
            from src.core.coordinator import Coordinator
            _coordinator = Coordinator()
        except Exception as exc:
            logger.warning("get_coordinator: failed to initialise Coordinator: %s", exc)
    return _coordinator


BACKTEST_TASK = None
BACKTEST_STATUS = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "last_stdout_tail": None,
    "last_returncode": None,
}


async def run_backtest_in_background(application: Application):
    global BACKTEST_TASK, BACKTEST_STATUS
    BACKTEST_STATUS.update({
        "state": "running",
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "finished_at": None, "last_error": None,
        "last_stdout_tail": None, "last_returncode": None,
    })
    try:
        if not os.path.exists(BACKTESTER_PATH):
            raise FileNotFoundError(f"backtester.py not found at: {BACKTESTER_PATH}")

        process = await asyncio.create_subprocess_exec(
            sys.executable, BACKTESTER_PATH,
            cwd=os.path.dirname(BACKTESTER_PATH),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")

        BACKTEST_STATUS["last_returncode"] = process.returncode
        BACKTEST_STATUS["last_stdout_tail"] = (stdout_text or "")[-2000:]
        BACKTEST_STATUS["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        if process.returncode != 0:
            BACKTEST_STATUS["state"] = "failed"
            BACKTEST_STATUS["last_error"] = (stderr_text or stdout_text or "Unknown error")[-2000:]
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    f"⚠️ *Backtest failed*\n🕒 Finished: {BACKTEST_STATUS['finished_at']}\n"
                    f"🔢 Return code: {process.returncode}\n```{BACKTEST_STATUS['last_error']}```"
                ),
                parse_mode="Markdown",
            )
            return

        BACKTEST_STATUS["state"] = "completed"
        rows = dl.latest_backtests_per_model()
        latest = rows[0] if rows else None
        if latest:
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, text=format_backtest_summary(latest), parse_mode="Markdown"
            )
        else:
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    f"✅ *Backtest finished*\n🕒 {BACKTEST_STATUS['finished_at']}\n"
                    f"```{(stdout_text or 'No output')[-3000:]}```"
                ),
                parse_mode="Markdown",
            )
    except Exception as e:
        BACKTEST_STATUS["state"] = "failed"
        BACKTEST_STATUS["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        BACKTEST_STATUS["last_error"] = str(e)
        await application.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ *Backtest crashed*\n`{str(e)}`",
            parse_mode="Markdown",
        )
    finally:
        BACKTEST_TASK = None


def is_authorised(update: Update) -> bool:
    if update.effective_chat:
        chat_id = update.effective_chat.id
    elif update.callback_query:
        chat_id = update.callback_query.message.chat.id
    else:
        return False
    return str(chat_id) == str(TELEGRAM_CHAT_ID)


def is_halted() -> bool:
    return os.path.exists(HALT_FLAG_PATH)



def get_last_logs(lines: int = 20) -> str:
    """Return the most recent journalctl lines for the live trader service.

    Thin wrapper kept for backwards-compat with any importers; new call sites
    should use ``dl.recent_logs_for(service, n=...)`` directly.
    """
    return dl.recent_logs_for(LIVE_SERVICE_NAME, n=lines)


# ── Commands ──────────────────────────────────────────────────────────────────


# G2/G3 — Single source of truth for the operator-facing command surface.
#
# Each ``BotCommandSpec`` is one operator-facing slash command. The list is
# the canonical ordering, used by:
#
#   * ``BOT_COMMANDS`` — flat ``BotCommand`` list passed to
#     ``app.bot.set_my_commands(...)`` in ``post_init``. Determines the
#     hamburger menu Telegram displays in the chat composer.
#   * ``render_help_top`` / ``render_help_category`` — the G3 button-driven
#     ``/help`` flow. ``cmd_start`` (which is what /help calls) replies
#     with the top-level category buttons; tapping a category edits the
#     message to a drill-down listing every command in that category.
#   * ``tests/test_telegram_query_bot.py::TestHelpCommandParity`` — asserts
#     every registered ``CommandHandler`` has a matching spec, every spec
#     surfaces in the menu, and the union of all category drill-downs
#     matches the spec order.
#
# Categories ``"meta"`` and ``"help"`` are present so /start and /help
# themselves can be in BOT_COMMANDS (Telegram surfaces them in the
# hamburger menu) without polluting the categorized /help body.
class BotCommandSpec:
    __slots__ = ("name", "description", "category")

    def __init__(self, name: str, description: str, category: str) -> None:
        self.name = name
        self.description = description
        self.category = category

    def __repr__(self) -> str:
        return f"BotCommandSpec({self.name!r}, {self.category!r})"


# (id, button label) — display order for the top-level /help menu.
# Update HELP_CATEGORIES + the categories of BOT_COMMAND_SPECS together.
HELP_CATEGORIES: list[tuple[str, str]] = [
    ("trading",     "🚦 Trading control"),
    ("accounts",    "💼 Accounts & strategies"),
    ("signals",     "📈 Signals & history"),
    ("backtest",    "🧪 Backtesting & dashboard"),
    ("diagnostics", "🩺 Diagnostics & VM"),
    ("sprint",      "📋 Sprint / dev"),
]
HELP_CATEGORY_IDS = {cid for cid, _ in HELP_CATEGORIES}


BOT_COMMAND_SPECS: list[BotCommandSpec] = [
    # ``meta`` — surfaced in the hamburger menu but not in the /help body.
    BotCommandSpec("start", "Show help", "meta"),
    BotCommandSpec("help", "Show help", "meta"),
    # Trading control
    BotCommandSpec("status", "Kill-switch state, P&L summary, service status", "trading"),
    BotCommandSpec("halt", "Stop order placement immediately", "trading"),
    BotCommandSpec("resume", "Re-enable order placement", "trading"),
    BotCommandSpec("closeall", "Emergency close all positions", "trading"),
    BotCommandSpec("toggle", "Start or stop the trader service", "trading"),
    # Accounts & strategies
    BotCommandSpec("accounts", "List accounts (dry/live + PnL) or toggle mode", "accounts"),
    BotCommandSpec("accounts_status", "Per-account risk state (daily PnL, halted)", "accounts"),
    BotCommandSpec("set_all_live", "Flip every account out of dry-run into live mode", "accounts"),
    BotCommandSpec("set_keys", "Open the operator notebook (env / keys / VM restart)", "accounts"),
    BotCommandSpec("risk_check", "Risk details for an account (button picker)", "accounts"),
    BotCommandSpec("smoke_test", "Live-plumbing smoke (always LIVE): /smoke_test [account]", "accounts"),
    BotCommandSpec("strategies", "Per-strategy signals, PnL and positions", "accounts"),
    BotCommandSpec("reload_strats", "Reload strategies.yaml without restart", "accounts"),
    BotCommandSpec("balance", "Account balance", "accounts"),
    BotCommandSpec("trades", "Open positions", "accounts"),
    # Signals & history
    BotCommandSpec("last5", "Last 5 journal entries", "signals"),
    BotCommandSpec("packages", "Refusals + stuck packages: why didn't trades land?", "signals"),
    BotCommandSpec("signals", "Recent pipeline signals: /signals [N] [strategy]", "signals"),
    BotCommandSpec("alerts", "Recent unit alerts (coordinator queue)", "signals"),
    BotCommandSpec("log", "Recent trader logs", "signals"),
    BotCommandSpec("download_journal", "Download trade journal DB", "signals"),
    BotCommandSpec("price", "Current BTC price", "signals"),
    BotCommandSpec("hourly", "Send the hourly summary on demand (bypasses dedup)", "signals"),
    # Backtesting & dashboard
    BotCommandSpec("backtest", "Start backtest in background", "backtest"),
    BotCommandSpec("latest_backtest", "Latest backtest result; /latest_backtest [strategy] [N] for history", "backtest"),
    BotCommandSpec("backtest_ui", "How to launch the Streamlit backtesting dashboard", "backtest"),
    BotCommandSpec("webapp", "Open the secure web dashboard", "backtest"),
    # Diagnostics & VM
    BotCommandSpec("health", "Per-unit status + data-file freshness", "diagnostics"),
    BotCommandSpec("vmstats", "VM resource snapshot (uptime, load, mem, disk)", "diagnostics"),
    BotCommandSpec("ping_test", "Verify the pending-pings inbox drain loop", "diagnostics"),
    BotCommandSpec("vm", "Tier 1 read-only Claude on the VM", "diagnostics"),
    BotCommandSpec("vm_write", "Tier 2 mutating Claude on the VM (asks to confirm)", "diagnostics"),
    # Sprint / dev
    BotCommandSpec("checkpoint", "Latest entry from CHECKPOINT_LOG.md", "sprint"),
    BotCommandSpec("sprintlet_status", "Manual sprint milestone update", "sprint"),
    BotCommandSpec("sprintlet_complete", "Manual sprint-complete signal", "sprint"),
    BotCommandSpec("new_session", "Queue a new Claude session for a sprint: /new_session <sprint_id>", "sprint"),
    BotCommandSpec("test", "Queue a backtest for a strategy (M5): /test <strategy>", "sprint"),
]


# Flat BotCommand list for set_my_commands (Telegram hamburger menu).
BOT_COMMANDS: list[BotCommand] = [
    BotCommand(s.name, s.description) for s in BOT_COMMAND_SPECS
]


def _category_label(cat_id: str) -> str:
    for cid, label in HELP_CATEGORIES:
        if cid == cat_id:
            return label
    return cat_id


def _commands_in_category(cat_id: str) -> list[BotCommandSpec]:
    return [s for s in BOT_COMMAND_SPECS if s.category == cat_id]


def render_help_top():
    """Top-level /help: greeting + one button per category.

    Returns ``(text, InlineKeyboardMarkup)``. The keyboard arranges
    categories in two-column rows so it stays compact on phone screens.
    """
    label = get_strategy_label()
    text = (
        f"👋 *ICT Trading Bot* — {label}\n\n"
        "Pick a category to see commands. Tap *« Back* in any category "
        "to return here.\n\n"
        "_Power users:_ `/help <category>` jumps straight to one "
        "(e.g. `/help trading`)."
    )
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for cid, label_str in HELP_CATEGORIES:
        row.append(InlineKeyboardButton(
            label_str, callback_data=f"help_cat:{cid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return text, InlineKeyboardMarkup(rows)


def render_help_category(cat_id: str):
    """Drill-down: show every command in ``cat_id`` with descriptions.

    Returns ``(text, InlineKeyboardMarkup)``. The keyboard is a single
    "« Back" button so the operator can return to the top menu without
    re-typing /help.
    """
    cat_id = (cat_id or "").strip().lower()
    if cat_id not in HELP_CATEGORY_IDS:
        text = (
            f"⚠️ Unknown help category `{cat_id}`. Tap *« Back* for the menu."
        )
        rows = [[InlineKeyboardButton("« Back", callback_data="help_top")]]
        return text, InlineKeyboardMarkup(rows)
    cmds = _commands_in_category(cat_id)
    label = _category_label(cat_id)
    lines = [f"*{label}*", ""]
    for spec in cmds:
        # Markdown italic-escape underscores in command name.
        cmd_md = "/" + spec.name.replace("_", "\\_")
        lines.append(f"{cmd_md} — {spec.description}")
    text = "\n".join(lines)
    rows = [[InlineKeyboardButton("« Back", callback_data="help_top")]]
    return text, InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    # /help <category> typed shortcut (power-user path).
    if context.args:
        cat_id = context.args[0].strip().lower()
        text, kb = render_help_category(cat_id)
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=kb)
        return
    text, kb = render_help_top()
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=kb)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# Regex used by the parity test: walks rendered category text and yields
# each leading /<cmd> token. Anchored at line-start so descriptions like
# ``(dry/live + PnL)`` or ``Backtest status/result`` aren't misread as
# extra commands. Handles Markdown's ``\_`` underscore-escape.
_HELP_CMD_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9\\_]*)", re.MULTILINE)


def _commands_in_help_text(text: str) -> list[str]:
    """Return the list of /<cmd> names appearing in ``text``, in order.

    Strips Markdown backslash escapes (``/accounts\\_status`` →
    ``accounts_status``). Used by ``TestHelpCommandParity`` against the
    rendered category drill-downs.
    """
    return [m.group(1).replace("\\_", "_") for m in _HELP_CMD_RE.finditer(text)]


def _commands_across_help_categories() -> list[str]:
    """Concatenate every category drill-down's command list, in display
    order. The result is the canonical "what does /help expose" surface,
    used by the parity test against ``BOT_COMMAND_SPECS`` (excluding meta).
    """
    out: list[str] = []
    for cid, _label in HELP_CATEGORIES:
        text, _kb = render_help_category(cid)
        out.extend(_commands_in_help_text(text))
    return out


# ---------------------------------------------------------------------------
# /set_keys — open the Colab key-rotation notebook
# ---------------------------------------------------------------------------

# Hardcoded so the message works even if the bot can't reach the repo.
# Update this constant if the repo or notebook path moves.
_COLAB_NOTEBOOK_URL = (
    "https://colab.research.google.com/github/benbaichmankass/ict-trading-bot/"
    "blob/main/notebooks/operator/rotate_api_keys.ipynb"
)
_COLAB_DOC_URL = (
    "https://github.com/benbaichmankass/ict-trading-bot/blob/main/"
    "docs/operator/colab-key-rotation.md"
)


async def cmd_set_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply with the open-in-Colab link for the canonical operator notebook.

    ``notebooks/operator/rotate_api_keys.ipynb`` is the SINGLE notebook
    in the repo for env generation, settings updates, API key rotation,
    and VM service restart. It reads from the operator's Colab Secrets,
    writes a fresh ``.env`` to ``~/ict-trading-bot/.env`` on the VM
    (no profile suffix — per BUG-039 the dry/live toggle lives in
    ``config/accounts.yaml`` `mode`), and restarts both the trader and
    Telegram-bot systemd units. See
    ``docs/operator/colab-key-rotation.md`` for the full setup.
    """
    if not is_authorised(update):
        return
    msg = (
        "🔑 *Rotate API keys*\n\n"
        "Open in Colab:\n"
        f"{_COLAB_NOTEBOOK_URL}\n\n"
        "*Runtime → Run all*. The first run in a fresh session pops a "
        "one-click \"Allow Drive access\" dialog — click Allow and the "
        "rest is automatic.\n\n"
        f"Setup guide (one-time): {_COLAB_DOC_URL}\n\n"
        "Required Colab Secrets:\n"
        "• `BYBIT_API_KEY_1`, `BYBIT_API_SECRET_1`\n"
        "• `BYBIT_API_KEY_2`, `BYBIT_API_SECRET_2`\n"
        "• `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`\n"
        "• `VM_SSH_HOST`, `VM_SSH_USER`\n\n"
        "Required SSH key (in Google Drive):\n"
        "• Put your VM SSH private key in `My Drive/ICT_Bot_Secrets/` "
        "named `vm_ssh_key` (or `id_rsa` / `id_ed25519` / "
        "`ict-bot-ovm-private.key`).\n\n"
        "After Run all completes, run `/accounts_status` to verify."
    )
    await update.message.reply_text(
        msg, parse_mode="Markdown", disable_web_page_preview=True,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    halted = is_halted()

    try:
        accounts = dl.list_accounts() or []
    except Exception:
        accounts = []

    # S-telegram-format Phase 4: status renders as collapsable
    # sections. The operator sees the kill-switch state in the header,
    # one summary line per account ("📈 main — 12 trades, +$45 PnL,
    # 1 open"), and taps to expand for full per-account detail.
    from src.units.ui.telegram_format import Section, render_html

    sections: list = []
    sections.append(Section(
        summary=(
            "🛑 HALTED — orders blocked"
            if halted else "🟢 RUNNING — orders enabled"
        ),
        body=(
            f"Kill-switch flag: {HALT_FLAG_PATH}\n"
            f"Use /resume to re-enable trading."
            if halted else
            f"Kill-switch flag: not set ({HALT_FLAG_PATH})\n"
            f"Use /halt to stop placing orders."
        ),
        priority=5,
    ))

    if accounts:
        for idx, acc in enumerate(accounts):
            aid = acc.get("account_id", "?")
            label = get_strategy_label(acc)
            trade_count, total_pnl = fetch_today_pnl(account_id=aid)
            open_count = fetch_open_positions_count(account_id=aid)
            sections.append(Section(
                summary=(
                    f"📈 {label} ({aid}) — "
                    f"{trade_count} trades, ${total_pnl:+.2f}, "
                    f"{open_count} open"
                ),
                body=(
                    f"Strategy / label: {label}\n"
                    f"Account: {aid}\n"
                    f"Trades today: {trade_count}\n"
                    f"P&L today: ${total_pnl:+.2f}\n"
                    f"Open positions (DB): {open_count}"
                ),
                priority=10 + idx,
            ))
    else:
        # Aggregate fallback — no accounts discovered.
        trade_count, total_pnl = fetch_today_pnl()
        open_count = fetch_open_positions_count()
        label = get_strategy_label()
        sections.append(Section(
            summary=(
                f"📈 {label} — {trade_count} trades, ${total_pnl:+.2f}, "
                f"{open_count} open (aggregate)"
            ),
            body=(
                f"Service: {get_service_status(LIVE_SERVICE_NAME)}\n"
                f"Trades today: {trade_count}\n"
                f"P&L today: ${total_pnl:+.2f}\n"
                f"Open positions (DB): {open_count}"
            ),
            priority=10,
        ))

    sections.append(Section(
        summary=f"🤖 Telegram bot — {get_service_status('ict-telegram-bot')}",
        body=f"Service: ict-telegram-bot\nTimestamp: {now}",
        priority=90,
    ))

    text = render_html(
        header="✅ ICT Trading Bot Status",
        sections=sections,
        footer=f"🕐 {now}",
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_halt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        with open(HALT_FLAG_PATH, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        # Also pause accounts via Coordinator so in-process risk guard fires.
        try:
            coord = get_coordinator()
            if coord is not None:
                coord.return_command("halt")
        except Exception as exc:
            logger.warning("cmd_halt: coordinator.return_command failed: %s", exc)
        await update.message.reply_text(
            "🛑 *Trader HALTED*\nFlag file created. No new orders will be placed.\n"
            "Use /resume to re-enable trading.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed to create halt flag: {e}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    if not os.path.exists(HALT_FLAG_PATH):
        await update.message.reply_text("ℹ️ Trader is not halted — no flag file found.")
        return
    try:
        os.remove(HALT_FLAG_PATH)
        # Also resume accounts via Coordinator.
        try:
            coord = get_coordinator()
            if coord is not None:
                coord.return_command("resume")
        except Exception as exc:
            logger.warning("cmd_resume: coordinator.return_command failed: %s", exc)
        await update.message.reply_text(
            "✅ *Trader RESUMED*\nHalt flag removed. Orders will resume on the next cycle.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed to remove halt flag: {e}")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return

    # S-telegram-format Phase 4: wrap each account's balance block in
    # a collapsable section so the operator scans summaries and taps
    # to expand details. Dup-key warning rides on a top "Notes"
    # section.
    from src.units.ui.processor import render_per_account_collapsable
    dup_warning = _duplicate_key_warning(accounts)
    body = render_per_account_collapsable(
        accounts,
        body_fn=_render_account_balance,
        header="💰 Account balances",
        empty_message="No accounts configured. Edit config/accounts.yaml and restart the trader.",
        extra_top_lines=[dup_warning] if dup_warning else None,
    )
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return

    from src.units.ui.processor import render_per_account_collapsable
    body = render_per_account_collapsable(
        accounts,
        body_fn=_render_account_positions,
        header="📊 Open positions",
        empty_message="No accounts configured. Edit config/accounts.yaml and restart the trader.",
    )
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    from src.units.ui import processor
    price = processor.get_price("BTCUSDT")
    if price is None:
        await update.message.reply_text("⚠️ Could not fetch price.")
        return
    await update.message.reply_text(
        f"📈 *BTC/USDT:* ${price:,.2f}", parse_mode="Markdown",
    )


async def cmd_last5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return
    rows: list = []
    for acc in accounts:
        try:
            rows.extend(dl.recent_trades_for(acc, n=5) or [])
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(
                f"⚠️ {acc.get('account_id', '?')}: could not load trades: {e}"
            )
    if not rows:
        await update.message.reply_text("📭 No trades found in trade_journal.db.")
        return
    # S-telegram-format Phase 3: render all trades into ONE HTML
    # message with each trade collapsed into its own
    # ``<blockquote expandable>`` section. Pre-PR the bot sent one
    # message per trade plus a chart attachment per row (5 trades →
    # 5+ messages); the new shape consolidates to a single message
    # the operator can scan and selectively expand.
    from src.units.ui.processor import render_recent_trades_collapsable
    body = render_recent_trades_collapsable(rows, title="📒 Last 5 trades")
    try:
        await update.message.reply_text(
            body, parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(
            f"⚠️ Could not render trades: {e}")
        return

    # The chart attachment was per-row pre-PR; sending it once at the
    # end keeps the operator's previous behaviour available without
    # cluttering the trade list.
    chart_candidates = [
        os.path.join(BASE_DIR, "ict_complete_chart.html"),
        os.path.join(BASE_DIR, "ict_enhanced_chart.html"),
        os.path.join(BASE_DIR, "swing_chart.html"),
    ]
    available_chart = next(
        (p for p in chart_candidates if os.path.exists(p)), None)
    if available_chart:
        try:
            await update.message.reply_document(
                document=open(available_chart, "rb"))
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# /packages — refusals + open undispatched packages (CP-2026-05-03-15).
# ---------------------------------------------------------------------------
#
# Surfaces what /last5 + the hourly summary intentionally hide: rows in
# trade_journal.db::trades with status='rejected' / 'exchange_rejected'
# (RiskManager refusals + exchange-side errors) plus order_packages
# rows still in status='open' with no linked_trade_id (signals the
# strategy emitted that the dispatcher couldn't turn into a trade).
#
# Arguments:
#   /packages       → last 10 refusals + last 10 open packages
#   /packages 25    → last 25 of each
#
# This command exists because the success-path surfaces (/last5,
# /strategies, hourly report, liveness watchdog) all filter rejection
# rows out — counting refusals in those would silently neuter the
# watchdog (CP-2026-05-03-14). /packages is the dedicated diagnostic
# surface for "VWAP fired N signals but 0 trades placed — why?".

async def cmd_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    # Parse one optional positional N. Default 10.
    n = 10
    if context.args:
        try:
            n = max(1, min(50, int(context.args[0])))
        except (TypeError, ValueError):
            await update.message.reply_text(
                f"Usage: /packages [N] (1..50). Got {context.args[0]!r}."
            )
            return

    try:
        rejections = dl.recent_rejections(n=n) or []
        open_pkgs = dl.open_order_packages(n=n) or []
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(
            f"⚠️ Could not load packages diagnostics: {exc}"
        )
        return

    from src.units.ui.processor import render_packages_collapsable
    body = render_packages_collapsable(
        rejections, open_pkgs,
        title=f"📦 Order packages (last {n})",
    )
    try:
        await update.message.reply_text(
            body, parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(
            f"⚠️ Could not render packages: {exc}"
        )


# ---------------------------------------------------------------------------
# /signals — show recent pipeline signals from runtime_logs/signal_audit.jsonl
# ---------------------------------------------------------------------------
# Signal helpers (SIGNAL_AUDIT_PATH, _read_audit_tail, _format_signal_row,
# _signals_strategy_keyboard, _signals_n_keyboard, etc.) live in
# src/bot/signal_helpers.py (D3 / PR-10) and are re-imported above.

async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent signals from runtime_logs/signal_audit.jsonl.

    Sprint 025 T3 — no-args invocation is now a two-step button stepper:
    pick strategy first (vwap / turtle_soup / all), then pick N (10 /
    25 / 50 / 100). Typed ``/signals [N] [strategy]`` is preserved as
    a power-user shortcut so the operator can request arbitrary values.
    """
    if not is_authorised(update):
        return

    args = list(context.args or [])
    strategy_filter: str | None = None
    limit = 10
    has_arg = False
    for arg in args:
        has_arg = True
        if arg.isdigit():
            limit = max(1, min(int(arg), 100))
        else:
            strategy_filter = arg.strip().lower()

    if not has_arg:
        # Step 1: strategy picker.
        await update.message.reply_text(
            "📡 *Recent signals*\nPick a strategy first, then pick how "
            "many records to show.",
            parse_mode="Markdown",
            reply_markup=_signals_strategy_keyboard(),
        )
        return

    # S-telegram-format Phase 3: HTML mode groups signals by status
    # into collapsable sections so the operator sees the distribution
    # at a glance and taps the bucket they want to inspect.
    from src.units.ui.processor import get_signals_block
    body = get_signals_block(
        strategy_filter=strategy_filter, limit=limit, use_html=True,
    )
    await update.message.reply_text(
        body, parse_mode="HTML", disable_web_page_preview=True,
    )


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception:
        accounts = []

    # S-telegram-format Phase 4: render each account's log tail as a
    # collapsable section so the operator sees one-line summaries
    # ("📝 main (ict-trader-live) — 20 lines") and taps to expand the
    # full tail. With many accounts this replaces N separate
    # multi-screen replies with one scannable message.
    from src.units.ui.processor import render_per_account_collapsable
    from src.units.ui.telegram_format import Section, render_html

    if not accounts:
        # No-accounts fallback: show host-wide logs in a single
        # collapsable section.
        try:
            log_text = get_last_logs(lines=20)
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"⚠️ Could not read logs: {e}")
            return
        label = get_strategy_label()
        body = render_html(
            header=f"📝 {label} logs",
            sections=[Section(
                summary=f"{label} — 20 lines",
                body=log_text[-3500:],
            )],
        )
        await update.message.reply_text(body, parse_mode="HTML")
        return

    def _body(acc):
        svc = acc.get("service") or LIVE_SERVICE_NAME
        return dl.recent_logs_for(svc, n=20)[-3500:]

    def _summary(acc, body_text):
        svc = acc.get("service") or LIVE_SERVICE_NAME
        label = get_strategy_label(acc)
        line_count = body_text.count("\n") + 1 if body_text else 0
        return f"📝 {label} ({svc}) — {line_count} lines"

    body = render_per_account_collapsable(
        accounts,
        body_fn=_body,
        summary_fn=_summary,
        header="📝 Service logs (last 20 lines per account)",
    )
    await update.message.reply_text(body, parse_mode="HTML")


async def cmd_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception:
        accounts = []
    if not accounts:
        current = get_service_status(LIVE_SERVICE_NAME)
        action = "stop" if current == "active" else "start"
        result = toggle_service(LIVE_SERVICE_NAME, action)
        await update.message.reply_text(result, parse_mode="Markdown")
        return
    for acc in accounts:
        svc = acc.get("service") or LIVE_SERVICE_NAME
        current = get_service_status(svc)
        action = "stop" if current == "active" else "start"
        result = toggle_service(svc, action)
        await update.message.reply_text(result, parse_mode="Markdown")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    # /closeall <strategy> → filter by strategy
    if context.args:
        strategy = context.args[0].strip().lower()
        await _do_closeall_strategy(update.message.reply_text, strategy)
        return
    # No args → inline keyboard for per-strategy selection + close-all
    strategies = dl.list_live_strategies() or list(_CLOSE_BUTTON_LABELS.keys())
    buttons = [
        InlineKeyboardButton(
            f"Close {_CLOSE_BUTTON_LABELS.get(s, s.title())}",
            callback_data=f"closeall:{s}",
        )
        for s in strategies
    ]
    buttons.append(InlineKeyboardButton("🚨 Close ALL", callback_data="closeall:all"))
    # Arrange in rows of 2
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard = InlineKeyboardMarkup(rows)
    try:
        accounts = dl.list_accounts() or []
        bybit_accounts = [a for a in accounts if (a.get("exchange") or "").lower() == "bybit"]
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return
    if not bybit_accounts:
        await update.message.reply_text("⚠️ No Bybit accounts configured.")
        return
    await update.message.reply_text(
        "Select strategy to close positions for:",
        reply_markup=keyboard,
    )


async def cmd_strategies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        coord = get_coordinator()
        if coord is not None:
            stats = coord.dashboard_stats()
            rows = stats.get("strategies") or []
        else:
            rows = dl.strategy_dashboard_data()
        text = _format_strategies_dashboard(rows)
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load strategy dashboard: {e}")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the most recent alerts from all units (coordinator alerts queue)."""
    if not is_authorised(update):
        return
    try:
        coord = get_coordinator()
        if coord is None:
            await update.message.reply_text("⚠️ Coordinator unavailable.")
            return
        alerts = coord.list_alerts(n=10)
        if not alerts:
            await update.message.reply_text("📭 No alerts in queue.")
            return
        lines = ["🔔 *Recent Alerts* (last 10)\n"]
        for a in reversed(alerts):
            level_icon = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(a.get("level", "info"), "ℹ️")
            ts = (a.get("ts") or "")[:19].replace("T", " ")
            lines.append(f"{level_icon} `{ts}` [{a.get('source', '?')}] {a.get('message', '')}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load alerts: {e}")


_SPRINT_RE = re.compile(r"\bS-\d{3}(?:\.\d+)?\b")


def _latest_sprint_from_checkpoint_log() -> tuple[str, str]:
    """Back-compat wrapper around ``processor.get_latest_sprint``.

    S-031 PR5 (architecture-audit-2026-05-02 P1-6): file parsing moved
    to the UI unit; this wrapper preserves the old tuple shape so the
    sprintlet handlers below stay untouched.
    """
    from src.units.ui import processor
    info = processor.get_latest_sprint()
    return info.get("sprint_id", "unknown"), info.get("cp_id", "unknown")


async def cmd_sprintlet_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual sprint milestone update. Usage: ``/sprintlet_status <note>``.

    The sprint id is parsed from the topmost CP entry of
    ``CHECKPOINT_LOG.md``, so the command is no longer hardcoded to
    a long-dead sprint number (fixed in S-016 H1 — see the audit doc).
    """
    if not is_authorised(update):
        return
    sprint_id, _ = _latest_sprint_from_checkpoint_log()
    note = " ".join(context.args) if context.args else "update"
    await update.message.reply_text(f"✅ {sprint_id}: {note}")


async def cmd_sprintlet_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual sprint-complete signal. Usage:
    ``/sprintlet_complete [sprint]`` — defaults to the active sprint
    parsed from ``CHECKPOINT_LOG.md``."""
    if not is_authorised(update):
        return
    sprint_arg = context.args[0].strip().upper() if context.args else None
    if sprint_arg and _SPRINT_RE.fullmatch(sprint_arg):
        sprint_id = sprint_arg
        cp_id = "(see CHECKPOINT_LOG.md)"
    else:
        sprint_id, cp_id = _latest_sprint_from_checkpoint_log()
    await update.message.reply_text(
        f"🎉 {sprint_id} COMPLETE. Latest checkpoint: {cp_id}."
    )


# ── Operator-initiated comms requests (M1 P1-D) ────────────────────────────
#
# /new_session and /test queue work for downstream consumers (Claude,
# the M5 backtest workflow) by writing a comms/requests/REQ-…json
# artifact and committing it via the same GitPusher the comms response
# writeback uses. Templates live in src.comms.templates.

async def _queue_comms_ask(
    update: Update,
    *,
    request,
    summary: str,
) -> None:
    """Persist + commit an operator-initiated comms request.

    Shared by ``cmd_new_session`` and ``cmd_test_strategy``. The git push
    is gated by ``COMMS_PUSH_ENABLED`` per ``GitPusher.from_env``, so
    local/dev runs no-op the push. Telegram ack always includes the
    request id (P1-D acceptance).
    """
    from src.comms import RequestStore
    from src.comms.models import CommsValidationError
    from src.comms.templates import commit_subject_for

    repo_root = Path(REPO_ROOT)
    store = RequestStore(repo_root / "comms")
    try:
        path = store.create(request)
    except (CommsValidationError, FileExistsError) as exc:
        logger.error("comms ask: store.create failed: %s", exc)
        await update.message.reply_text(
            f"⚠️ Could not write comms request: {exc}"
        )
        return

    try:
        pusher = GitPusher.from_env(repo_root)
        pusher.commit_and_push(
            files=[path],
            message=commit_subject_for(request),
        )
    except GitPushError as exc:
        # Artifact is on disk; push retry is a separate concern. Surface
        # the failure so the operator knows the queue may not have
        # propagated yet, but don't tear down the artifact.
        logger.warning("comms ask: push failed for %s: %s", request.request_id, exc)
        await update.message.reply_text(
            f"⚠️ Wrote `{request.request_id}` but push failed: {exc}\n"
            f"{summary}",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        f"✅ Queued `{request.request_id}`.\n{summary}",
        parse_mode="Markdown",
    )


async def cmd_new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Queue a new-Claude-session command. Usage: ``/new_session <sprint_id>``.

    Writes ``comms/requests/REQ-…-ns<sprint>.json`` so the next Claude
    session can read it on startup and bootstrap onto the requested
    sprint. The artifact is operator-initiated; consumption lives in
    CLAUDE.md / sprint prompts (out of scope here per the M1 P1-D
    audit).
    """
    if not is_authorised(update):
        return
    args = list(context.args or [])
    if not args:
        await update.message.reply_text(
            "Usage: `/new_session <sprint_id>` (e.g. `/new_session S-099`)",
            parse_mode="Markdown",
        )
        return
    sprint_id = args[0].strip()

    from src.comms.models import CommsValidationError
    from src.comms.templates import make_new_session_request
    try:
        request = make_new_session_request(sprint_id)
    except CommsValidationError as exc:
        await update.message.reply_text(f"⚠️ Invalid sprint id: {exc}")
        return

    summary = f"Claude will pick up `{sprint_id}` on the next sync."
    await _queue_comms_ask(update, request=request, summary=summary)


async def cmd_test_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Queue a strategy-backtest request. Usage: ``/test <strategy>``.

    Writes ``comms/requests/REQ-…-ts<strategy>.json`` for the M5
    backtest consumer (``src.bot.test_strategy_consumer``) to pick up
    on the next poll cycle. The consumer runs the backtest, writes a
    formatted summary back via ``apply_answer``, and persists a row
    to ``runtime_logs/validation.jsonl``.

    The strategy name is validated against ``config/strategies.yaml``
    at dispatch time so a typo (``/test vwapp``) gets rejected
    immediately rather than after a full poll cycle.
    """
    if not is_authorised(update):
        return
    args = list(context.args or [])
    if not args:
        await update.message.reply_text(
            "Usage: `/test <strategy>` (e.g. `/test vwap`)",
            parse_mode="Markdown",
        )
        return
    strategy = args[0].strip()

    from src.comms.models import CommsValidationError
    from src.comms.templates import make_test_strategy_request
    from src.strategy_registry import load_strategies
    try:
        registered = {s["name"] for s in load_strategies()}
    except Exception as exc:  # noqa: BLE001
        # Registry read failure must not silently accept any string.
        logger.error("cmd_test_strategy: registry read failed: %s", exc)
        await update.message.reply_text(
            f"⚠️ Could not read strategy registry: {exc}"
        )
        return
    if strategy not in registered:
        roster = ", ".join(sorted(registered)) or "(none registered)"
        await update.message.reply_text(
            f"⚠️ Unknown strategy `{strategy}`. Registered: {roster}",
            parse_mode="Markdown",
        )
        return

    try:
        request = make_test_strategy_request(strategy)
    except CommsValidationError as exc:
        await update.message.reply_text(f"⚠️ Invalid strategy: {exc}")
        return

    summary = (
        f"M5 backtest consumer will run `{strategy}` and write results "
        "back into the artifact."
    )
    await _queue_comms_ask(update, request=request, summary=summary)



async def cmd_ping_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify the inbox-drain loop end-to-end.

    Drops a test ping in the inbox, waits one drain cycle, replies
    with success / diagnostic.
    """
    if not is_authorised(update):
        return
    note = " ".join(context.args) if context.args else "ping test"
    os.makedirs(PENDING_PINGS_DIR, exist_ok=True)
    test_id = f"test-{int(datetime.now(timezone.utc).timestamp())}"
    path = os.path.join(PENDING_PINGS_DIR, f"{test_id}.json")
    tmp = path + ".tmp"
    payload = {"priority": "normal", "body": f"ping_test from /ping_test: {note}"}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.rename(tmp, path)
    await update.message.reply_text(
        f"📨 Queued `{test_id}.json`. Should fire within "
        f"{PING_DRAIN_INTERVAL_S}s.",
        parse_mode="Markdown",
    )


async def cmd_checkpoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the latest checkpoint ID from CHECKPOINT_LOG.md.

    S-031 PR5 (architecture-audit-2026-05-02 P1-6): file read moved
    to ``processor.get_latest_checkpoint_header``.
    """
    if not is_authorised(update):
        return
    from src.units.ui import processor
    header = processor.get_latest_checkpoint_header()
    if header.startswith("⚠️"):
        await update.message.reply_text(header)
    else:
        await update.message.reply_text(f"Latest checkpoint: {header}")


# ── /health and /vmstats (S-016 H2) ──────────────────────────────────────────


_HEALTH_UNITS = (
    "ict-trader-live",
    "ict-telegram-bot",
    "ict-web-api",
    "ict-git-sync.timer",
)
_HEALTH_FILES: tuple[tuple[str, str], ...] = (
    # (display_label, repo_relative_path)
    ("runtime_status.json (last tick)", "runtime_logs/runtime_status.json"),
    ("signal_audit.jsonl (last signal)", "runtime_logs/signal_audit.jsonl"),
    ("trade_journal.db",                 "trade_journal.db"),
)


def _file_age(path: str) -> str:
    """Return a short freshness string for *path*, e.g. ``42s``, ``7m``,
    ``3h12m``. ``missing`` if the file isn't there. Used by /health."""
    if not os.path.exists(path):
        return "missing"
    try:
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
    except OSError as exc:
        return f"stat-err: {exc.__class__.__name__}"
    age = max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
    if age < 60:
        return f"{int(age)}s ({size}B)"
    if age < 3600:
        return f"{int(age / 60)}m ({size}B)"
    if age < 86400:
        h = int(age // 3600)
        m = int((age % 3600) // 60)
        return f"{h}h{m:02d}m ({size}B)"
    return f"{int(age / 86400)}d ({size}B)"


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Per-unit status snapshot — systemd units + key data files.

    S-031 PR5 (architecture-audit-2026-05-02 P1-6): the rendering and
    file-mtime + systemctl reads moved to
    ``processor.get_health_summary``.
    """
    if not is_authorised(update):
        return
    from src.units.ui import processor
    body = processor.get_health_summary(
        get_service_status=get_service_status, use_html=True,
    )
    await update.message.reply_text(body, parse_mode="HTML")



async def cmd_vmstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VM-side resource snapshot — uptime, load, memory, disk.

    S-031 PR5 (architecture-audit-2026-05-02 P1-6): the /proc + disk
    reads moved to ``processor.get_vm_stats``.
    """
    if not is_authorised(update):
        return
    from src.units.ui import processor
    body = processor.get_vm_stats()
    await update.message.reply_text(body, parse_mode="Markdown")


# ── VM-resident Claude runner (S-014.5) ──────────────────────────────────────
#
# /vm <prompt>        — Tier 1, read-only ops, no confirmation.
# /vm_write <prompt>  — Tier 2, mutating ops, requires Telegram YES/NO.
#
# Tier 3 actions are pre-screened in src.bot.vm_runner.screen_for_tier3 and
# refused here regardless of command. See docs/claude/vm-operator-mode.md.

# Pending /vm_write confirmations, keyed by chat id. Single-slot per chat —
# a second /vm_write while one is pending replaces the first (the first one
# is implicitly cancelled). Bot is single-operator anyway so this is fine.
_PENDING_VM_WRITE: dict[int, str] = {}

_VM_WRITE_BUTTONS = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Confirm", callback_data="vm_write_confirm"),
    InlineKeyboardButton("✖️ Cancel",  callback_data="vm_write_cancel"),
]])


async def _run_and_reply(
    update: Update, prompt: str, tier: int
) -> None:
    """Spawn the runner in a thread (it blocks on systemd-run --wait) and
    post the result back. Keeps the bot's event loop responsive."""
    progress = await update.message.reply_text(
        f"🤖 VM runner spawned (tier {tier}). Waiting for transcript…"
    )
    result: RunnerResult = await asyncio.to_thread(handle_vm_command, prompt, tier)
    icon = "✅" if result.ok else "⚠️"
    await progress.edit_text(f"{icon} {result.telegram_text()}")


async def cmd_vm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tier 1 — read-only VM diagnostics via Claude Code."""
    if not is_authorised(update):
        return
    prompt = " ".join(context.args or []).strip()
    if not prompt:
        await update.message.reply_text(
            "Usage: /vm <prompt>\n"
            "Example: /vm what is the trader uptime and last error?\n"
            f"Max {MAX_PROMPT_CHARS} chars. Tier 1 only — read-only ops."
        )
        return
    await _run_and_reply(update, prompt, tier=1)


async def cmd_vm_write(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tier 2 — mutating ops. Stages the prompt; user confirms via inline button."""
    if not is_authorised(update):
        return
    prompt = " ".join(context.args or []).strip()
    if not prompt:
        await update.message.reply_text(
            "Usage: /vm_write <prompt>\n"
            "Tier 2 — service restarts, file edits, branch push, PR open. "
            "You'll be asked to confirm before the runner spawns."
        )
        return
    chat_id = update.effective_chat.id
    _PENDING_VM_WRITE[chat_id] = prompt
    preview = prompt if len(prompt) <= 600 else prompt[:600] + "…"
    await update.message.reply_text(
        "⚠️ *Tier 2 (write) confirmation*\n\n"
        f"```\n{preview}\n```\n\n"
        "Confirm to spawn the runner with write permissions, or cancel.",
        parse_mode="Markdown",
        reply_markup=_VM_WRITE_BUTTONS,
    )


async def cmd_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply with a tap-to-open button for the secure web dashboard.

    Reads ``WEBAPP_URL`` from the environment. Unset/empty → clean
    "not configured yet" message. The dashboard requires the operator
    to log in (S-013 M3) before any data is shown.
    """
    if not is_authorised(update):
        return
    url = (os.environ.get("WEBAPP_URL") or "").strip()
    if not url:
        await update.message.reply_text(
            "🌐 Web dashboard not configured yet.\n"
            "Set WEBAPP_URL on the VM once the dashboard is reachable."
        )
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔐 Open dashboard", url=url)]]
    )
    await update.message.reply_text(
        "🌐 Web dashboard — log in with your allowlisted email.",
        reply_markup=keyboard,
    )


async def cmd_download_journal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    if not os.path.exists(DB_PATH):
        await update.message.reply_text("⚠️ trade_journal.db not found.")
        return
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f, filename="trade_journal.db",
                caption="📥 Latest trade_journal.db",
            )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not send journal: {e}")


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BACKTEST_TASK, BACKTEST_STATUS
    if not is_authorised(update):
        return
    if BACKTEST_STATUS["state"] == "running":
        await update.message.reply_text("⏳ Backtest already running. Use /latest_backtest to check.")
        return
    BACKTEST_TASK = asyncio.create_task(run_backtest_in_background(context.application))
    await update.message.reply_text("🚀 Backtest started. Use /latest_backtest to see status and results.")


async def cmd_latest_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """``/latest_backtest [strategy] [N]``.

    No-arg path: unchanged — surfaces the latest backtest_results row
    per ``strategy_version`` (or running/failed status when an active
    job is in flight).

    With args (CP-2026-05-?-??): show the last N backtest_results rows
    for one ``strategy_version`` with delta indicators on the latest
    run vs the prior. Lets the operator track whether a strategy is
    improving or regressing across consecutive backtest runs — useful
    when investigating "live trades aren't placing" against the
    backdrop of recent strategy tuning.
    """
    if not is_authorised(update):
        return

    args = list(context.args or [])

    # ---- New args path: /latest_backtest <strategy> [N] -----------------
    if args:
        strategy = str(args[0]).strip()
        n = 5
        if len(args) >= 2:
            try:
                n = max(1, min(20, int(args[1])))
            except (TypeError, ValueError):
                await update.message.reply_text(
                    "Usage: /latest_backtest [strategy] [N]\n"
                    f"Got N={args[1]!r}; expected an integer 1..20."
                )
                return

        try:
            rows = dl.backtest_history_for(strategy, n=n) or []
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(
                f"⚠️ Could not load backtest history: {exc}"
            )
            return

        if not rows:
            try:
                available = dl.list_backtest_strategies() or []
            except Exception:  # noqa: BLE001
                available = []
            msg = (
                f"ℹ️ No backtest history for strategy_version={strategy!r}."
            )
            if available:
                msg += "\nAvailable: " + ", ".join(available)
            await update.message.reply_text(msg)
            return

        from src.units.ui.processor import render_backtest_history_collapsable
        body = render_backtest_history_collapsable(rows, strategy)
        try:
            await update.message.reply_text(
                body, parse_mode="HTML", disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(
                f"⚠️ Could not render backtest history: {exc}"
            )
        return

    # ---- No-arg path: unchanged behaviour --------------------------------
    state = BACKTEST_STATUS["state"]
    if state == "running":
        await update.message.reply_text(
            f"⏳ *Backtest RUNNING*\nStarted: {BACKTEST_STATUS['started_at']}", parse_mode="Markdown"
        )
    elif state == "failed":
        await update.message.reply_text(
            f"⚠️ *Backtest FAILED*\nStarted: {BACKTEST_STATUS['started_at']}\n"
            f"Finished: {BACKTEST_STATUS['finished_at']}\nCode: {BACKTEST_STATUS['last_returncode']}\n"
            f"Error: {BACKTEST_STATUS['last_error']}",
            parse_mode="Markdown",
        )
    elif state == "completed":
        rows = dl.latest_backtests_per_model()
        latest = rows[0] if rows else None
        if latest:
            await update.message.reply_text(format_backtest_summary(latest), parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"✅ *Backtest COMPLETED*\nFinished: {BACKTEST_STATUS['finished_at']}", parse_mode="Markdown"
            )
    else:
        rows = dl.latest_backtests_per_model()
        latest = rows[0] if rows else None
        if latest:
            await update.message.reply_text(format_backtest_summary(latest), parse_mode="Markdown")
        else:
            await update.message.reply_text("ℹ️ No backtest running and no saved result found.")


# ── Inline button callback handler ───────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_authorised(update):
        await query.edit_message_text("⛔ Unauthorised.")
        return

    raw = query.data or ""
    if raw == "vm_write_confirm":
        chat_id = update.effective_chat.id
        prompt = _PENDING_VM_WRITE.pop(chat_id, "")
        if not prompt:
            await query.edit_message_text("⏰ Confirmation expired or already actioned.")
            return
        await query.edit_message_text("🤖 Tier 2 confirmed — runner spawned. Waiting for transcript…")
        result: RunnerResult = await asyncio.to_thread(handle_vm_command, prompt, 2)
        icon = "✅" if result.ok else "⚠️"
        await query.message.reply_text(f"{icon} {result.telegram_text()}")
        return
    if raw == "vm_write_cancel":
        chat_id = update.effective_chat.id
        _PENDING_VM_WRITE.pop(chat_id, None)
        await query.edit_message_text("✖️ Cancelled.")
        return

    parts = raw.split(":", 1)
    if not parts or not parts[0]:
        return
    action = parts[0]

    # G3 — /help button menu navigation.
    if action == "help_top":
        text, kb = render_help_top()
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=kb)
        return
    if action == "help_cat":
        cat_id = parts[1] if len(parts) > 1 else ""
        text, kb = render_help_category(cat_id)
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=kb)
        return

    # G4 — account picker for /risk_check.
    if action == "risk_check":
        account_name = parts[1] if len(parts) > 1 else ""
        try:
            coord = get_coordinator()
            if coord is None:
                await query.edit_message_text("⚠️ Coordinator unavailable.")
                return
            statuses = coord.accounts_status() or []
            if not statuses:
                await query.edit_message_text(
                    "ℹ️ No accounts found in accounts.yaml.")
                return
            text = _render_risk_check_for_account(statuses, account_name)
            await query.edit_message_text(text, parse_mode="Markdown")
        except Exception as e:  # noqa: BLE001
            await query.edit_message_text(
                f"⚠️ Could not check risk for '{account_name}': {e}")
        return

    # Sprint 025 T4 — /accounts mode-toggle confirm flow.
    if action == "acct_flip_ask":
        # acct_flip_ask:<name>:<target>
        rest = parts[1] if len(parts) > 1 else ""
        name, _, target = rest.rpartition(":")
        if not name or target not in {"dry", "live"}:
            await query.edit_message_text(
                "⚠️ Invalid flip request — please re-open /accounts.")
            return
        coord = get_coordinator()
        if coord is None:
            await query.edit_message_text("⚠️ Coordinator unavailable.")
            return
        target_icon = "🔴" if target == "live" else "🧪"
        warn = (
            "\n\n⚠️ *Flipping to LIVE means this account will place "
            "REAL orders on the next signal.*"
            if target == "live" else ""
        )
        await query.edit_message_text(
            f"❓ *Confirm flip*\n\n"
            f"Account: `{name}`\n"
            f"New mode: {target_icon} **{target.upper()}**"
            f"{warn}",
            parse_mode="Markdown",
            reply_markup=_accounts_confirm_keyboard(name, target),
        )
        return
    if action == "acct_flip_do":
        rest = parts[1] if len(parts) > 1 else ""
        name, _, target = rest.rpartition(":")
        if not name or target not in {"dry", "live"}:
            await query.edit_message_text(
                "⚠️ Invalid flip request — please re-open /accounts.")
            return
        coord = get_coordinator()
        if coord is None:
            await query.edit_message_text("⚠️ Coordinator unavailable.")
            return
        try:
            result = coord.set_account_dry_run(name, target == "dry")
            icon = "🧪" if result.get("dry_run") else "🔴"
            await query.edit_message_text(
                f"{icon} `{name}` → **{result.get('mode', target)} mode**",
                parse_mode="Markdown",
            )
        except Exception as exc:  # noqa: BLE001
            await query.edit_message_text(
                f"⚠️ Could not flip `{name}`: {exc}", parse_mode="Markdown")
        return
    if action == "acct_flip_cancel":
        await query.edit_message_text("✖️ Cancelled — no mode change applied.")
        return

    # Sprint 025 T3 — /signals stepper navigation.
    if action == "signals_top":
        await query.edit_message_text(
            "📡 *Recent signals*\nPick a strategy first, then pick how "
            "many records to show.",
            parse_mode="Markdown",
            reply_markup=_signals_strategy_keyboard(),
        )
        return
    if action == "signals_strat":
        strategy = parts[1] if len(parts) > 1 else "all"
        scope = "all strategies" if strategy == "all" else strategy
        await query.edit_message_text(
            f"📡 *Recent signals* — {scope}\nHow many?",
            parse_mode="Markdown",
            reply_markup=_signals_n_keyboard(strategy),
        )
        return
    if action == "signals_n":
        # signals_n:<strategy>:<N>
        rest = parts[1] if len(parts) > 1 else ""
        strat_part, _, n_part = rest.rpartition(":")
        try:
            limit = max(1, min(int(n_part), 200))
        except (TypeError, ValueError):
            await query.edit_message_text(
                "⚠️ Invalid N — tap a number on the stepper.")
            return
        strategy_filter = None if strat_part == "all" else strat_part
        from src.units.ui.processor import get_signals_block
        body = get_signals_block(
            strategy_filter=strategy_filter, limit=limit, use_html=True,
        )
        await query.edit_message_text(
            body, parse_mode="HTML", disable_web_page_preview=True,
        )
        return

    # Sprint 025 T2 — /smoke_test account picker.
    if action == "smoke":
        payload = parts[1] if len(parts) > 1 else ""
        target = None if payload == "all" else payload
        coord = get_coordinator()
        if coord is None:
            await query.edit_message_text("⚠️ Coordinator unavailable.")
            return
        await query.edit_message_text(
            ("🧪 Running smoke test (LIVE)"
             + (f" on `{target}`" if target else " on all accounts")
             + "…"),
            parse_mode="Markdown",
        )
        result = await _run_smoke_test(target, coord)
        if result.get("error") and not result.get("results"):
            await query.message.reply_text(
                f"⚠️ smoke_test failed: {result['error']}")
            return
        await query.message.reply_text(
            _render_smoke_test_result(result), parse_mode="Markdown")
        return

    if action == "log":
        try:
            accounts = dl.list_accounts() or []
        except Exception:
            accounts = []
        if not accounts:
            try:
                log_text = get_last_logs(lines=20)
                label = get_strategy_label()
                await query.edit_message_text(
                    f"📝 *{label} logs*\n```{log_text[-3500:]}```",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await query.edit_message_text(f"⚠️ Could not read logs: {e}")
        else:
            blocks = []
            for acc in accounts:
                svc = acc.get("service") or LIVE_SERVICE_NAME
                label = get_strategy_label(acc)
                log_text = dl.recent_logs_for(svc, n=10)
                blocks.append(f"📝 *{label}* (`{svc}`)\n```{log_text[-1200:]}```")
            try:
                await query.edit_message_text(
                    "\n\n".join(blocks)[:4000], parse_mode="Markdown"
                )
            except Exception as e:
                await query.edit_message_text(f"⚠️ Could not read logs: {e}")

    elif action == "toggle":
        try:
            accounts = dl.list_accounts() or []
        except Exception:
            accounts = []
        if not accounts:
            current = get_service_status(LIVE_SERVICE_NAME)
            act = "stop" if current == "active" else "start"
            result = toggle_service(LIVE_SERVICE_NAME, act)
            await query.edit_message_text(result, parse_mode="Markdown")
        else:
            results = []
            for acc in accounts:
                svc = acc.get("service") or LIVE_SERVICE_NAME
                current = get_service_status(svc)
                act = "stop" if current == "active" else "start"
                results.append(toggle_service(svc, act))
            await query.edit_message_text(
                "\n\n".join(results)[:4000], parse_mode="Markdown"
            )

    elif action == "closeall":
        payload = parts[1] if len(parts) > 1 else "all"
        if payload != "all":
            # Per-strategy close via inline button
            await _do_closeall_strategy(query.edit_message_text, payload)
        else:
            # Close ALL open trades — routed through the canonical
            # close path (processor → execute.close_open_position).
            from src.units.ui import processor
            try:
                rows = processor.close_open_positions()
            except Exception as e:
                await query.edit_message_text(
                    f"⚠️ Could not close positions: {e}"
                )
                return
            body = _render_closeall_results(rows, scope_label="all")
            await query.edit_message_text(body[:4000])


async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List accounts with dry/live mode + per-account toggle buttons.

    Usage:
        /accounts                          — listing + per-account toggle buttons
        /accounts dry|live <account_name>  — typed power-user shortcut

    Sprint 025 T4 — the button flow REQUIRES two taps to apply a
    flip (pick → confirm) so accidental switches between dry and live
    can't happen with one click. The typed path is preserved
    unchanged for operators who still want one-shot.
    """
    if not is_authorised(update):
        return
    coord = get_coordinator()
    if coord is None:
        await update.message.reply_text("⚠️ Coordinator unavailable.")
        return

    # /accounts dry bybit_1  or  /accounts live bybit_1
    if len(context.args) == 2:
        mode = context.args[0].strip().lower()
        acc_name = context.args[1].strip()
        if mode not in ("dry", "live"):
            await update.message.reply_text(
                "⚠️ Usage: `/accounts dry|live <account_name>`",
                parse_mode="Markdown",
            )
            return
        try:
            result = coord.set_account_dry_run(acc_name, mode == "dry")
            icon = "🧪" if result["dry_run"] else "🔴"
            await update.message.reply_text(
                f"{icon} `{acc_name}` → **{result['mode']} mode**",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Could not toggle account: {e}")
        return

    # /accounts (no args) → listing text + per-account toggle keyboard.
    try:
        statuses = coord.accounts_status() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not load accounts: {e}")
        return
    if not statuses:
        await update.message.reply_text("ℹ️ No accounts found in accounts.yaml.")
        return
    body = _render_accounts_listing(statuses)
    kb = _accounts_toggle_keyboard(statuses)
    await update.message.reply_text(
        body
        + "\n\nTap a button to flip an account's mode. You'll be asked "
        "to confirm before the change is applied.",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def cmd_reload_strats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reload strategy config from strategies.yaml and confirm via Coordinator."""
    if not is_authorised(update):
        return
    try:
        coord = get_coordinator()
        if coord is None:
            await update.message.reply_text("⚠️ Coordinator unavailable.")
            return
        result = coord.reload_strategy_config()
        if result.get("reloaded"):
            names = ", ".join(f"`{s}`" for s in result.get("strategies", []))
            await update.message.reply_text(
                f"✅ *Strategy config reloaded*\n"
                f"{result['strategy_count']} strategies: {names}",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"⚠️ Reload failed: {result.get('error', 'unknown error')}"
            )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not reload strategy config: {e}")


async def cmd_backtest_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tell the user how to launch the Streamlit backtesting dashboard."""
    if not is_authorised(update):
        return
    await update.message.reply_text(
        "📈 *Backtesting UI*\n\n"
        "Run locally:\n"
        "```\nstreamlit run src/web/backtest_ui.py\n```\n\n"
        "Data sources (in order):\n"
        "1. `BACKTEST_CSV` env var\n"
        "2. `data/backtests.csv`\n"
        "3. `data/backtest_candles.csv`\n"
        "4. Mock data (always available)\n\n"
        "Filters: strategy, symbol, date range.",
        parse_mode="Markdown",
    )


async def cmd_accounts_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show per-account risk state + live API balance via Coordinator.

    The live balance line is what proves the API is integrated for each
    account. Anything other than a USD figure means the bot can't reach
    that account's exchange (missing creds, network, etc.).
    """
    if not is_authorised(update):
        return
    try:
        coord = get_coordinator()
        if coord is None:
            await update.message.reply_text("⚠️ Coordinator unavailable.")
            return
        statuses = coord.accounts_status()
        if not statuses:
            await update.message.reply_text("ℹ️ No accounts found in accounts.yaml.")
            return

        # S-telegram-format follow-up: the page is now rendered with
        # collapsable per-account sections — operator sees the summary
        # line for every account at a glance, taps the one they want
        # to inspect for the full risk + API + prop block. Renderer
        # lives in the UI processor (CLAUDE.md rule 5 — bot is a thin
        # shell).
        from src.units.ui.processor import render_accounts_status_collapsable
        body = render_accounts_status_collapsable(statuses)
        await update.message.reply_text(body, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load accounts status: {e}")


async def cmd_smoke_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run a live-plumbing smoke trade: strategies → coordinator → accounts → exchange.

    Usage:
        /smoke_test               — button picker (all accounts + per-account)
        /smoke_test all           — every account in accounts.yaml
        /smoke_test <account>     — a single account (e.g. /smoke_test bybit_2)

    The smoke is **always LIVE** — there is no dry-run option. A
    ``smoke_test`` OrderPackage tagged ``meta.is_test=True`` is
    shipped through ``account_execute()`` with a 0.0001 BTC qty (below
    Bybit's min-lot), the risk gate is bypassed by design, Bybit's
    "qty invalid" rejection is captured as the success signal, and a
    row is written to ``trade_journal.db`` with
    ``strategy_name='smoke_test'``. The qty is the safety cap — it's
    intentionally too small to ever fill, so contacting the exchange
    is safe and proves the API integration is hot end-to-end.

    Sprint 025 T2 — no-args invocation now replies with an
    inline-keyboard account picker (reusing
    ``_account_picker_keyboard(include_all=True)``) so the operator
    doesn't have to remember exact account names. Tap → callback runs
    the smoke and edits the message in place.
    """
    if not is_authorised(update):
        return
    coord = get_coordinator()
    if coord is None:
        await update.message.reply_text("⚠️ Coordinator unavailable.")
        return

    args = list(context.args or [])
    account_id: str | None = None
    has_arg = False
    for arg in args:
        a = arg.strip().lower()
        has_arg = True
        if a in {"all", "*"}:
            account_id = None
            break
        if account_id is None:
            account_id = arg.strip()

    # Sprint 025 T2 — no args: show the picker and stop here.
    if not has_arg:
        try:
            statuses = coord.accounts_status() or []
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(
                f"⚠️ Could not list accounts: {exc}")
            return
        if not statuses:
            await update.message.reply_text(
                "ℹ️ No accounts found in accounts.yaml.")
            return
        kb = _account_picker_keyboard(
            "smoke", statuses, include_all=True,
            all_label="🌐 All accounts (LIVE smoke)",
        )
        await update.message.reply_text(
            "🧪 *Smoke test* (LIVE)\nPick an account, or run on every "
            "configured account:",
            parse_mode="Markdown", reply_markup=kb,
        )
        return

    # Typed-arg path — run immediately.
    await update.message.reply_text(
        ("🧪 Running smoke test (LIVE)"
         + (f" on `{account_id}`" if account_id else " on all accounts")
         + "…"),
        parse_mode="Markdown",
    )
    result = await _run_smoke_test(account_id, coord)
    if result.get("error") and not result.get("results"):
        await update.message.reply_text(
            f"⚠️ smoke_test failed: {result['error']}")
        return
    await update.message.reply_text(
        _render_smoke_test_result(result), parse_mode="Markdown")


async def cmd_risk_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Risk state for an account. /risk_check (no args) → button picker;
    /risk_check <name> still works as a typed shortcut."""
    if not is_authorised(update):
        return
    account_name = (context.args[0].strip() if context.args else "").lower()
    try:
        coord = get_coordinator()
        if coord is None:
            await update.message.reply_text("⚠️ Coordinator unavailable.")
            return
        statuses = coord.accounts_status()
        if not statuses:
            await update.message.reply_text("ℹ️ No accounts found in accounts.yaml.")
            return
        if not account_name:
            # G4 — no args: show inline-button picker so the operator
            # never has to remember exact account names.
            kb = _account_picker_keyboard("risk_check", statuses)
            await update.message.reply_text(
                "🔍 *Risk Check*\nPick an account:",
                parse_mode="Markdown", reply_markup=kb,
            )
            return
        text = _render_risk_check_for_account(statuses, account_name)
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not check risk for '{account_name}': {e}")


async def cmd_hourly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Build + send the hourly summary on demand.

    BUG-032: the in-process scheduler in `src/main.py` is the only path
    that emits the hourly summary; if the trader process is in a tick-
    loop crash spiral, a stuck `summary_markers.json`, or
    `send_via_alert_manager` is failing, the operator sees nothing.
    `/hourly` bypasses the dedup marker and reports the build/send
    result back over the same Telegram channel so the failure mode
    becomes visible.

    Optional first arg ``replay`` reuses the marker (same as the
    scheduled path); otherwise the dedup is bypassed.
    """
    if not is_authorised(update):
        return

    bypass_dedup = True
    args = list(context.args or [])
    if args and args[0].strip().lower() in {"replay", "scheduled"}:
        bypass_dedup = False

    try:
        from datetime import datetime, timezone
        from src.units.ui import processor
        from src.runtime.outcomes import send_scheduled

        now = datetime.now(timezone.utc)
        if not bypass_dedup:
            from src.utils.signal_audit_logger import should_send_summary
            if not should_send_summary(now):
                await update.message.reply_text(
                    "ℹ️ /hourly replay: marker already present for this hour. "
                    "Use plain /hourly to force-send."
                )
                return

        # Sprint 025 T1 (UI processor migration step 1, audit doc § 5):
        # /hourly used to call src.runtime.hourly_report.build_hourly_report
        # directly. It now goes through src.units.ui.processor — the same facade
        # the webapp will consume — so the bot and any future UI surface
        # render identical text.
        msg = processor.get_hourly_report(now_utc=now, tick_interval_s=900)
        send_scheduled(msg)

        # Plain text — the message contains identifiers with multiple
        # underscores ("send_via_alert_manager", "pending_pings.jsonl")
        # that Telegram's legacy Markdown parser interprets as
        # unbalanced italic and rejects with BadRequest. Same shape as
        # BUG-009 / BUG-030 for /signals and /last5.
        await update.message.reply_text(
            f"✅ Hourly report dispatched ({len(msg)} chars). "
            f"If you don't see it shortly, check "
            f"runtime_logs/pending_pings.jsonl on the VM "
            f"(send_via_alert_manager failure path)."
        )
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(
            f"⚠️ /hourly failed: {type(exc).__name__}: {exc}"
        )


async def cmd_set_all_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force every account out of dry-run mode.

    BUG-031 follow-up: when the operator wants to make sure every
    account is firing real orders, this is the single button that
    flips them all. Iterates accounts.yaml via the Coordinator and
    calls ``set_account_dry_run(name, False)`` on each.

    Reports a summary back over Telegram (account count, any failures).
    Per-account ``dry_run`` is the only dry/live toggle in the codebase
    (operator directive 2026-05-03). The override is in-memory and
    applies to the next ``load_accounts()`` call (no restart). For a
    persistent change, edit ``config/accounts.yaml`` ``mode`` field and
    let the trader reload.
    """
    if not is_authorised(update):
        return
    coord = get_coordinator()
    if coord is None:
        await update.message.reply_text("⚠️ Coordinator unavailable.")
        return
    try:
        statuses = coord.accounts_status()
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {exc}")
        return

    flipped, errors = [], []
    for s in statuses:
        name = s.get("name")
        if not name:
            continue
        try:
            coord.set_account_dry_run(name, False)
            flipped.append(name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")

    lines = ["🔴 <b>All accounts → LIVE</b>"]
    if flipped:
        lines.append(f"flipped: {', '.join(flipped)}")
    if errors:
        lines.append("errors:\n  - " + "\n  - ".join(errors))
    if not flipped and not errors:
        lines.append("(no accounts found)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # S-019 — pending-pings inbox drain. Job queue runs the coroutine
    # every PING_DRAIN_INTERVAL_S seconds. Any process can drop a JSON
    # file into runtime_logs/pending_pings/ and it'll be sent within
    # one tick. See _drain_pending_pings docstring above.
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            _drain_pending_pings,
            interval=PING_DRAIN_INTERVAL_S,
            first=2,   # 2 s grace at startup so post_init lands first
            name="drain_pending_pings",
        )
    else:
        logger.warning(
            "JobQueue unavailable — pending-pings inbox drain disabled. "
            "Install python-telegram-bot[job-queue] to enable.",
        )

    async def post_init(app):
        # G2 — single source of truth: BOT_COMMANDS mirrors /help in order.
        # See the BOT_COMMANDS docstring above the constant for the contract.
        await app.bot.set_my_commands(BOT_COMMANDS)

    application.post_init = post_init

    # S-027 PR2 — operator comms channel. Registers a CallbackQueryHandler
    # filtered to ``comms:`` data and a passive text handler for the
    # "Other" free-text path; spawns CommsPoller once Application is up.
    # Must run BEFORE the generic CallbackQueryHandler below so the
    # pattern-matched handler wins on ``comms:*`` callback_data.
    install_comms_handlers(application, repo_root=Path(REPO_ROOT))

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("set_keys", cmd_set_keys))
    application.add_handler(CommandHandler("halt", cmd_halt))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("trades", cmd_trades))
    application.add_handler(CommandHandler("closeall", cmd_closeall))
    application.add_handler(CommandHandler("strategies", cmd_strategies))
    application.add_handler(CommandHandler("last5", cmd_last5))
    application.add_handler(CommandHandler("packages", cmd_packages))
    application.add_handler(CommandHandler("signals", cmd_signals))
    application.add_handler(CommandHandler("backtest", cmd_backtest))
    application.add_handler(CommandHandler("latest_backtest", cmd_latest_backtest))
    application.add_handler(CommandHandler("log", cmd_log))
    application.add_handler(CommandHandler("toggle", cmd_toggle))
    application.add_handler(CommandHandler("download_journal", cmd_download_journal))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("alerts", cmd_alerts))
    application.add_handler(CommandHandler("reload_strats", cmd_reload_strats))
    application.add_handler(CommandHandler("backtest_ui", cmd_backtest_ui))
    application.add_handler(CommandHandler("accounts", cmd_accounts))
    application.add_handler(CommandHandler("accounts_status", cmd_accounts_status))
    application.add_handler(CommandHandler("set_all_live", cmd_set_all_live))
    application.add_handler(CommandHandler("hourly", cmd_hourly))
    application.add_handler(CommandHandler("risk_check", cmd_risk_check))
    application.add_handler(CommandHandler("smoke_test", cmd_smoke_test))
    application.add_handler(CommandHandler("sprintlet_status", cmd_sprintlet_status))
    application.add_handler(CommandHandler("sprintlet_complete", cmd_sprintlet_complete))
    application.add_handler(CommandHandler("checkpoint", cmd_checkpoint))
    application.add_handler(CommandHandler("new_session", cmd_new_session))
    application.add_handler(CommandHandler("test", cmd_test_strategy))
    application.add_handler(CommandHandler("health", cmd_health))
    application.add_handler(CommandHandler("vmstats", cmd_vmstats))
    application.add_handler(CommandHandler("ping_test", cmd_ping_test))
    application.add_handler(CommandHandler("webapp", cmd_webapp))
    application.add_handler(CommandHandler("vm", cmd_vm))
    application.add_handler(CommandHandler("vm_write", cmd_vm_write))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.run_polling()


if __name__ == "__main__":
    main()
