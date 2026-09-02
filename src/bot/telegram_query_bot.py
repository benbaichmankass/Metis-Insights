"""Operator command bot (@bict_trading_bot) — menu-driven control plane.

The 2026-05 overhaul (see ``docs/TELEGRAM-SPEC.md``) replaced the old
~40-command surface with a single inline-button menu:

    🛑 Kill switch · 🩺 System update · 💼 Accounts · 📈 Strategies ·
    🚨 Close all positions

``/start`` and ``/menu`` are the only slash commands. Every view reads
live state (accounts.yaml, strategies.yaml, the journal, runtime_status,
systemd) so adding an account or strategy needs no bot code change.

The pure presentation half (keyboards + collapsible snapshot renderers)
lives in ``src.bot.menu``; this module is the I/O + wiring layer: it
fetches state, routes the ``menu:*`` / ``killacct*`` / ``killstrat*``
callbacks, and performs the two kill-switch writes through their
sanctioned writers (account → ``scripts/ops/set_account_mode.sh``;
strategy → ``src.bot.strategy_execution_writer`` + a coordinator reload).
"""
import asyncio
import logging
import os
import subprocess
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.bot import menu
from src.bot.cloud_notifier import (
    PENDING_CLAUDE_PINGS_DIR,
    PING_DRAIN_INTERVAL_S,
    _disk_usage_repo,
    _drain_pending_pings,
    _read_loadavg,
    _read_meminfo_mb,
    _read_uptime_human,
    get_service_status,
)
from src.bot.comms_handler import install_comms_handlers
from src.bot.strategy_execution_writer import (
    StrategyExecutionWriteError,
    set_strategy_execution,
)
from src.utils.paths import repo_root as _repo_root
from src.utils.paths import runtime_logs_dir as _runtime_logs_dir
from src.utils.paths import trade_journal_db_path as _trade_journal_db_path

load_dotenv()

REPO_ROOT = _repo_root()

# Canonical journal-DB path (resolver first, repo-root as existence fallback).
_DB_CANDIDATES = [
    _trade_journal_db_path(),
    os.path.join(REPO_ROOT, "trade_journal.db"),
]
DB_PATH = next(
    (p for p in _DB_CANDIDATES if p and os.path.exists(p)), _trade_journal_db_path()
)

STRATEGIES_YAML = os.path.join(REPO_ROOT, "config", "strategies.yaml")
SET_ACCOUNT_MODE_SH = os.path.join(REPO_ROOT, "scripts", "ops", "set_account_mode.sh")

# systemd units surfaced in the System view (spec §3.2).
SYSTEM_UNITS = ("ict-trader-live", "ict-web-api", "ict-claude-bridge")
LIVE_SERVICE_NAME = "ict-trader-live"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Match the trader process: redact the bot token from logs and silence
# httpx's per-request URL logging so the token never lands in journalctl.
from src.utils.log_redact import (  # noqa: E402
    assert_telegram_token_shape,
    install_redacting_filter,
    suppress_httpx_logging,
)

install_redacting_filter()
suppress_httpx_logging()

logger = logging.getLogger(__name__)


# ── Coordinator singleton ───────────────────────────────────────────────────

_coordinator = None


def get_coordinator():
    """Return the module-level Coordinator singleton (lazy-initialised)."""
    global _coordinator
    if _coordinator is None:
        try:
            from src.core.coordinator import Coordinator

            _coordinator = Coordinator()
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_coordinator: failed to initialise Coordinator: %s", exc)
    return _coordinator


def is_authorised(update: Update) -> bool:
    if update.effective_chat:
        chat_id = update.effective_chat.id
    elif update.callback_query:
        chat_id = update.callback_query.message.chat.id
    else:
        return False
    return str(chat_id) == str(TELEGRAM_CHAT_ID)


def is_halted() -> bool:
    # Single-homed 2026-08-13 (see runtime_flags.halt_flag_path). This used to
    # be a module constant hardcoded to /tmp/trader_halt.flag while the pipeline
    # checked /data/bot-data/ -- so the operator's halt readout could contradict
    # the trader. The constant is gone rather than re-pointed: it was never read
    # anywhere else, and keeping it would just re-create a second definition of
    # the path. Resolved per call, so an env change needs no bot restart.
    from src.runtime.runtime_flags import is_halted as _is_halted
    return _is_halted()


# ── Operator command surface (just the menu openers) ────────────────────────
#
# BOT_COMMANDS is the flat list handed to ``set_my_commands`` — it IS the
# hamburger menu Telegram shows in the composer. Per the overhaul it
# carries only the two menu openers; there is no stale command wall.

# (name, description) — the only operator-facing slash commands. Kept as
# plain tuples (not telegram.BotCommand) so the surface is assertable even
# when a test stubs ``telegram.BotCommand`` with a bare MagicMock.
_MENU_OPENERS: list[tuple[str, str]] = [
    ("start", "Open the menu"),
    ("menu", "Open the menu"),
]
BOT_COMMAND_SPECS: list[BotCommand] = [
    BotCommand(name, desc) for name, desc in _MENU_OPENERS
]
BOT_COMMANDS = BOT_COMMAND_SPECS


# ── Menu openers ────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """``/start`` / ``/menu`` — open the main menu."""
    if not is_authorised(update):
        return
    text, kb = menu.render_main_menu()
    await update.message.reply_text(text, reply_markup=kb)


# ``/menu`` is an alias for ``/start`` — both open the main menu.
cmd_menu = cmd_start


# ── State fetchers (resilient: every read degrades to a friendly value) ──────


def _load_strategies_config() -> list[dict]:
    """Return ``[{name, enabled, execution, ...}, ...]`` from strategies.yaml.

    Best-effort: a missing/unreadable file yields an empty list rather
    than raising. The raw per-strategy mapping is flattened with the
    strategy key folded in as ``name``.
    """
    try:
        with open(STRATEGIES_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("_load_strategies_config: read failed: %s", exc)
        return []
    strategies = data.get("strategies") or {}
    out: list[dict] = []
    for name, cfg in strategies.items():
        cfg = cfg if isinstance(cfg, dict) else {}
        entry = dict(cfg)
        entry["name"] = name
        entry.setdefault("execution", "live")
        out.append(entry)
    return out


def _runtime_status() -> dict:
    """Read ``runtime_logs/runtime_status.json`` best-effort."""
    import json

    # Canonical runtime_logs (DATA_DIR-aware) — the trader writes here.
    # repo_root()/runtime_logs is the wrong, stale dir on a DATA_DIR VM.
    path = str(_runtime_logs_dir() / "runtime_status.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def _accounts_status() -> list[dict]:
    """Return ``coord.accounts_status()`` or ``[]`` (never raises)."""
    try:
        coord = get_coordinator()
        if coord is None:
            return []
        return coord.accounts_status() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("_accounts_status: failed: %s", exc)
        return []


def _account_is_dry(acc: dict) -> bool:
    if "dry_run" in acc:
        return bool(acc["dry_run"])
    return str(acc.get("mode", "live")).strip().lower() == "dry_run"


def _accounts_view_data(statuses: list[dict]) -> list[dict]:
    """Map ``accounts_status()`` dicts onto ``menu.render_accounts_view`` shape."""
    out: list[dict] = []
    for s in statuses:
        out.append(
            {
                "account_id": s.get("name") or s.get("account_id"),
                "exchange": s.get("exchange"),
                "dry_run": _account_is_dry(s),
                "account_type": s.get("account_type"),
                "max_daily_loss_usd": s.get("max_daily_loss_usd"),
                "max_dd_pct": s.get("max_dd_pct"),
                "balance": s.get("live_balance_usdt"),
                "pnl_24h": s.get("daily_pnl"),
                "open_positions": s.get("open_positions"),
            }
        )
    return out


def _strategies_view_data(strategies: list[dict]) -> list[dict]:
    """Map strategy config + runtime status onto ``render_strategies_view``."""
    status = _runtime_status()
    loaded = set(status.get("loaded_strategies") or [])
    out: list[dict] = []
    for s in strategies:
        name = s.get("name")
        out.append(
            {
                "name": name,
                "label": s.get("label") or name,
                "execution": s.get("execution", "live"),
                "running": (name in loaded) if loaded else None,
            }
        )
    return out


def _kill_summary(statuses: list[dict], strategies: list[dict]) -> dict:
    dry = sum(1 for a in statuses if _account_is_dry(a))
    shadow = sum(
        1
        for s in strategies
        if str(s.get("execution", "live")).strip().lower() == "shadow"
    )
    return {
        "accounts_live": len(statuses) - dry,
        "accounts_dry": dry,
        "strats_live": len(strategies) - shadow,
        "strats_shadow": shadow,
    }


def _heartbeat() -> dict:
    """Best-effort trader liveness from ``runtime_logs/heartbeat.txt`` mtime.

    Reads the CANONICAL runtime_logs (DATA_DIR-aware) — the same file the
    trader writes via src.runtime.heartbeat. repo_root()/runtime_logs is a
    stale dir on a DATA_DIR VM (it froze at the 2026-05-11 10:01:30 incident
    mtime), which made the System view falsely report the trader 'stopped'.
    """
    path = str(_runtime_logs_dir() / "heartbeat.txt")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"label": "unknown", "age_seconds": None, "last_tick": "missing"}
    age = max(0, int(datetime.now(timezone.utc).timestamp() - mtime))
    try:
        from src.runtime.heartbeat import heartbeat_label

        label = heartbeat_label(age)
    except Exception:  # noqa: BLE001
        label = "running" if age < 180 else ("paused" if age < 600 else "stopped")
    last_tick = datetime.fromtimestamp(mtime, timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    return {"label": label, "age_seconds": age, "last_tick": last_tick}


def _vm_resources() -> dict:
    total_mb, avail_mb = _read_meminfo_mb()
    free_gb, total_gb = _disk_usage_repo()
    mem = f"{avail_mb}/{total_mb} MB free" if total_mb else "unknown"
    disk = f"{free_gb}/{total_gb} GB free" if total_gb else "unknown"
    return {
        "uptime": _read_uptime_human(),
        "load": _read_loadavg(),
        "mem": mem,
        "disk": disk,
    }


# ── View builders (return rendered HTML) ─────────────────────────────────────


def build_system_view() -> str:
    services = {u: get_service_status(u) for u in SYSTEM_UNITS}
    statuses = _accounts_status()
    strategies = _load_strategies_config()
    kill_summary = _kill_summary(statuses, strategies)
    if is_halted():
        kill_summary["accounts_live"] = f"{kill_summary['accounts_live']} (HALT flag set)"
    return menu.render_system_view(
        services=services,
        heartbeat=_heartbeat(),
        kill_summary=kill_summary,
        vm=_vm_resources(),
    )


def build_accounts_view() -> str:
    return menu.render_accounts_view(_accounts_view_data(_accounts_status()))


def build_strategies_view() -> str:
    return menu.render_strategies_view(
        _strategies_view_data(_load_strategies_config())
    )


# ── Kill-switch writers (one sanctioned writer per gate) ─────────────────────


async def _persist_account_mode(name: str, mode: str) -> str:
    """Run the sanctioned ``set_account_mode.sh`` for *name* → *mode*.

    Returns a short operator-facing result string. The script edits
    accounts.yaml AND restarts the trader so the change survives a
    restart and wipes any in-memory override.
    """
    env = dict(os.environ)
    env["ACCOUNT_ID"] = name
    env["MODE"] = mode
    proc = await asyncio.to_thread(
        subprocess.run,
        ["bash", SET_ACCOUNT_MODE_SH],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode == 0:
        return f"✅ Account {name} → {mode}. Trader restarted; change persisted."
    tail = (proc.stderr or proc.stdout or "no output").strip()[-400:]
    return f"⚠️ set-account-mode failed (exit {proc.returncode}):\n{tail}"


def _persist_strategy_execution(name: str, execution: str) -> str:
    """Edit strategies.yaml execution gate + reload via the coordinator."""
    previous, new = set_strategy_execution(STRATEGIES_YAML, name, execution)
    reloaded = False
    try:
        coord = get_coordinator()
        if coord is not None:
            result = coord.reload_strategy_config()
            reloaded = bool(result.get("reloaded"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("_persist_strategy_execution: reload failed: %s", exc)
    suffix = " (reloaded)" if reloaded else " (edit saved; reload on next restart)"
    return f"✅ Strategy {name}: {previous} → {new}{suffix}"


# ── Inline-button callback router ────────────────────────────────────────────


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_authorised(update):
        await query.edit_message_text("⛔ Unauthorised.")
        return

    raw = query.data or ""
    parts = raw.split(":")
    action = parts[0] if parts else ""

    try:
        if raw == menu.CB_HOME:
            text, kb = menu.render_main_menu()
            await query.edit_message_text(text, reply_markup=kb)

        elif raw == menu.CB_KILL:
            await query.edit_message_text(
                "🛑 Kill switch — stops NEW trades (does not close open "
                "positions). Pick a gate:",
                reply_markup=menu.kill_menu_keyboard(),
            )

        elif raw == menu.CB_KILL_ACCOUNTS:
            statuses = _accounts_status()
            accounts = [
                {"account_id": s.get("name"), "dry_run": _account_is_dry(s)}
                for s in statuses
            ]
            if not accounts:
                await query.edit_message_text(
                    "ℹ️ No accounts found.", reply_markup=menu.kill_menu_keyboard()
                )
            else:
                await query.edit_message_text(
                    "🛑 By account — toggle live ⇄ dry_run (persists across "
                    "restart):",
                    reply_markup=menu.account_kill_keyboard(accounts),
                )

        elif raw == menu.CB_KILL_STRATS:
            strategies = [
                {"name": s.get("name"), "execution": s.get("execution", "live")}
                for s in _load_strategies_config()
            ]
            if not strategies:
                await query.edit_message_text(
                    "ℹ️ No strategies found.",
                    reply_markup=menu.kill_menu_keyboard(),
                )
            else:
                await query.edit_message_text(
                    "🛑 By strategy — toggle live ⇄ shadow (persists across "
                    "restart):",
                    reply_markup=menu.strategy_kill_keyboard(strategies),
                )

        elif raw == menu.CB_SYSTEM:
            await query.edit_message_text(
                build_system_view(),
                parse_mode="HTML",
                reply_markup=menu.back_to_menu_keyboard(),
            )

        elif raw == menu.CB_ACCOUNTS:
            await query.edit_message_text(
                build_accounts_view(),
                parse_mode="HTML",
                reply_markup=menu.back_to_menu_keyboard(),
            )

        elif raw == menu.CB_STRATEGIES:
            await query.edit_message_text(
                build_strategies_view(),
                parse_mode="HTML",
                reply_markup=menu.back_to_menu_keyboard(),
            )

        elif raw == menu.CB_CLOSEALL:
            await query.edit_message_text(
                "🚨 Close ALL open positions across all accounts? This "
                "flattens live positions and cannot be undone.",
                reply_markup=menu.closeall_confirm_keyboard(),
            )

        elif raw == menu.CB_CLOSEALL_CONFIRM:
            await _do_close_all(query)

        elif action == "killacct":
            # killacct:<name>:<target>
            name, target = parts[1], parts[2]
            warn = (
                "\n\n⚠️ This will place REAL orders on the next signal."
                if target == "live"
                else ""
            )
            await query.edit_message_text(
                f"❓ Confirm: account {name} → {target}.{warn}",
                reply_markup=menu.account_kill_confirm_keyboard(name, target),
            )

        elif action == "killacct_do":
            name, target = parts[1], parts[2]
            await query.edit_message_text(f"⏳ Setting {name} → {target}…")
            result = await _persist_account_mode(name, target)
            await query.edit_message_text(
                result, reply_markup=menu.back_to_menu_keyboard()
            )

        elif action == "killstrat":
            # killstrat:<name>:<target>
            name, target = parts[1], parts[2]
            warn = (
                "\n\n⚠️ This re-enables LIVE order execution for this strategy."
                if target == "live"
                else ""
            )
            await query.edit_message_text(
                f"❓ Confirm: strategy {name} → {target}.{warn}",
                reply_markup=menu.strategy_kill_confirm_keyboard(name, target),
            )

        elif action == "killstrat_do":
            name, target = parts[1], parts[2]
            try:
                result = _persist_strategy_execution(name, target)
            except StrategyExecutionWriteError as exc:
                result = f"⚠️ Could not flip {name}: {exc}"
            await query.edit_message_text(
                result, reply_markup=menu.back_to_menu_keyboard()
            )

        elif action == "wdec":
            # Work-decision answer — the two-way half of the operator's
            # decision channel (operator ask, 2026-09-02). The button carries
            # DIGESTS of (object_id, request_id) and of the option KEY, never
            # the ids and never a positional index: 64 bytes is the whole
            # budget, and an index would silently select a different option if
            # the request were edited between the send and the tap.
            #
            # ⚠️ The reply this renders must never say `committed`. The route
            # returns `in_transit`; the answer is not the decision until a
            # committer writes it into the repo.
            from src.runtime.telegram_decisions import handle_decision_callback

            result = await asyncio.to_thread(handle_decision_callback, raw)
            if result is not None:
                await query.edit_message_text(result["reply"])

        elif action == "propexp":
            # Prop ticket-expiry Yes/No answer. The prompt is normally sent to
            # (and answered on) the prop bot, but breakout_notify._prop_bot_token
            # falls back to THIS trader-bot token when the prop token is unset
            # (the post-cutover degraded case), so handle it here too rather than
            # leave a dead button. Same shared handler as the prop bot.
            from src.prop.prop_expiry_prompt import handle_expiry_callback
            from src.prop.telegram_commands import REPORT_PROMPT

            result = await asyncio.to_thread(handle_expiry_callback, raw)
            if result is not None:
                await query.edit_message_text(result["ack"])
                if result.get("send_prompt"):
                    await query.message.reply_text(REPORT_PROMPT)

        # Unknown callback data is ignored (comms:* handled by its own handler).
    except Exception as exc:  # noqa: BLE001
        logger.warning("callback_handler: %s failed: %s", raw, exc)
        try:
            await query.edit_message_text(
                f"⚠️ Action failed: {exc}",
                reply_markup=menu.back_to_menu_keyboard(),
            )
        except Exception:  # noqa: BLE001
            pass


async def _do_close_all(query) -> None:
    from src.units.ui import processor

    try:
        rows = await asyncio.to_thread(processor.close_open_positions)
    except Exception as exc:  # noqa: BLE001
        await query.edit_message_text(
            f"⚠️ Could not close positions: {exc}",
            reply_markup=menu.back_to_menu_keyboard(),
        )
        return
    rows = rows or []
    closed = sum(1 for r in rows if (r or {}).get("ok"))
    failed = len(rows) - closed
    who = query.message.chat.id if query.message else "?"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info("close-all by chat=%s at %s: closed=%d failed=%d", who, ts, closed, failed)
    await query.edit_message_text(
        f"🚨 Close all complete — {closed} closed, {failed} failed.\n{ts}",
        reply_markup=menu.back_to_menu_keyboard(),
    )


# ── Entry point ──────────────────────────────────────────────────────────────


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment")
    # Shape check BEFORE handing the value to PTB — its InvalidToken
    # exception echoes the full token into the crash traceback (journald →
    # historically committed health snapshots). Secret-free failure instead.
    assert_telegram_token_shape(TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Pending-pings inbox drain (trade/diagnostic alerts dropped by any
    # process into runtime_logs/pending_pings/).
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            _drain_pending_pings,
            interval=PING_DRAIN_INTERVAL_S,
            first=2,
            name="drain_pending_pings",
        )
        # The Claude/operational-update inbox (/system-report + review pings).
        #
        # HISTORY, because it decides the failure posture. The separate
        # ict-claude-bridge sat inactive on the Ampere VM (its token never
        # carried over the cutover), so these pings were silently NEVER
        # DELIVERED; 2026-06-22 folded them into THIS bot's drain, which fixed
        # delivery at the cost of putting operational pings among trade alerts.
        #
        # The operator provisioned a dedicated Claude bot (2026-09-01) to split
        # them back apart. So: prefer the dedicated bot, and ⚠️ FALL BACK TO
        # THIS ONE rather than to silence — a bad or missing Claude token must
        # degrade to the 2026-06-22 behaviour (delivered, less specific), never
        # re-create the outage that fold-in was written to end. Separation is a
        # nice-to-have; delivery is not.
        #
        # ⚠️ The CHAT ID is unchanged. In a DM Telegram's chat.id IS the
        # operator's user id, identical for every bot, so a different token is
        # what makes a different conversation (src/bot/telegram_routes.py).
        _claude_bot_cell: dict = {}

        async def _resolve_claude_bot():
            """Return the dedicated bot, or None to fall back. Resolved ONCE.

            Validated with get_me() before first use: an unusable token that
            merely *exists* would otherwise make every send fail and leave the
            pings on disk, retried every 5s forever — stranded in a way that
            looks identical to a quiet inbox. Checking once converts that into
            a single loud line and a working fallback.
            """
            if "bot" in _claude_bot_cell:
                return _claude_bot_cell["bot"]
            try:
                from src.bot.telegram_routes import claude_route
                route = claude_route()
            except Exception as exc:  # noqa: BLE001
                logger.warning("claude ping route unresolvable (%s) — using the trader bot", exc)
                _claude_bot_cell["bot"] = None
                return None
            # `isolated` is the right predicate, NOT `deliverable`: a route can be
            # deliverable via the shared trader token, which is precisely the
            # un-separated state this change exists to leave behind. It keys on the
            # TOKEN alone by design — at a DM the chat id is the operator's and is
            # shared by every bot, so requiring a dedicated chat would report a
            # correctly-separated route as un-separated forever.
            if not route.isolated:
                # describe() names the VARIABLE that answered, never its value.
                logger.warning(
                    "claude pings: no dedicated bot (token_state=%s; %s) — delivering "
                    "via the trader bot, so they land among trade alerts",
                    route.token_state, route.describe(),
                )
                _claude_bot_cell["bot"] = None
                return None
            try:
                from telegram import Bot
                candidate = Bot(token=route.token)
                me = await candidate.get_me()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "claude pings: the DEDICATED bot token is set but unusable (%s) — "
                    "falling back to the trader bot so pings are still delivered", exc,
                )
                _claude_bot_cell["bot"] = None
                return None
            logger.info("claude pings: delivering via the dedicated bot @%s", me.username)
            _claude_bot_cell["bot"] = candidate
            return candidate

        async def _drain_claude_pings(context) -> None:
            bot = await _resolve_claude_bot()
            await _drain_pending_pings(
                context, pings_dir=PENDING_CLAUDE_PINGS_DIR, bot=bot)

        application.job_queue.run_repeating(
            _drain_claude_pings,
            interval=PING_DRAIN_INTERVAL_S,
            first=4,
            name="drain_pending_claude_pings",
        )

        # Work-decision prompts — the ASK half of the two-way decision channel.
        # Sweeps GET /api/bot/work/decisions and sends ONE inline-keyboard
        # prompt per un-prompted, unanswered request. It lives HERE rather than
        # on the trader tick because a button only works in a bot some process
        # POLLS, and this process is the one that polls the token these prompts
        # are sent on (see telegram_decisions.answerable_route).
        from src.runtime.telegram_decisions import (
            prompt_interval_seconds,
            run_decision_prompt_sweep,
        )

        async def _sweep_work_decisions(context) -> None:
            # Off the event loop: the sweep does blocking loopback HTTP and a
            # file write, and polling must never stall behind it.
            stats = await asyncio.to_thread(run_decision_prompt_sweep)
            if stats.get("prompted_choice") or stats.get("prompted_free_text"):
                logger.info("work-decision prompts: %s", stats)

        _wd_interval = prompt_interval_seconds()
        if _wd_interval > 0:
            application.job_queue.run_repeating(
                _sweep_work_decisions,
                interval=_wd_interval,
                first=20,
                name="sweep_work_decisions",
            )
        else:
            logger.info(
                "work-decision prompts PAUSED "
                "(WORK_DECISION_PROMPT_SECONDS <= 0)",
            )
    else:
        logger.warning(
            "JobQueue unavailable — pending-pings inbox drain disabled. "
            "Install python-telegram-bot[job-queue] to enable.",
        )

    async def post_init(app):
        # The hamburger menu shows only the menu openers (spec §2.1).
        await app.bot.set_my_commands(BOT_COMMANDS)

    application.post_init = post_init

    # Operator comms channel (Claude → operator questions answered via
    # buttons / free text). Registered BEFORE the generic callback handler
    # so pattern-matched ``comms:*`` data wins.
    from pathlib import Path

    install_comms_handlers(application, repo_root=Path(REPO_ROOT))

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # ── declare what THIS process polls, now that the handlers are attached ───
    # Registered here and not earlier, deliberately: a claim made before
    # `add_handler` would be true of the intent rather than of the process, and
    # the state this registry exists to catch is precisely a bot that is polled
    # with no handler for the prefix — a tap that produces a `callback_query`
    # nobody collects, with no error anywhere.
    #
    # The trader bot is the FALLBACK destination for work-decision prompts (the
    # dedicated Claude bot is preferred once ict-claude-decision-bot.service is
    # confirmed polling it), so `telegram_decisions.answerable_route()` needs
    # positive evidence that a tap here is received before it will send buttons.
    # CB_PREFIX rather than a literal "wdec", so the claim tracks the prefix
    # `telegram_decisions` actually encodes into callback_data.
    # ⚠️ `callback_handler`'s own branch above still tests the LITERAL "wdec",
    # so a rename of CB_PREFIX would move this claim and the encoder together
    # while leaving that branch behind — the claim would then name a prefix the
    # dispatcher no longer routes. Left as-is rather than fixed here because a
    # concurrent session (claude/claude-ping-double-delivery-x4mq2p) has declared
    # this file; filed instead so it is not lost.
    from src.runtime.telegram_decisions import CB_PREFIX
    from src.runtime.telegram_poll_registry import (
        heartbeat_interval_seconds,
        log_poll_banner,
        record_poll,
    )

    _polled_prefixes = (CB_PREFIX, "propexp")

    async def _register_poll(_ctx) -> None:
        await asyncio.to_thread(
            record_poll, "TELEGRAM_BOT_TOKEN", _polled_prefixes,
            service="ict-telegram-bot",
        )

    # In-process first, so the sweep that runs in THIS process has its answer
    # without waiting a heartbeat interval or touching the filesystem.
    record_poll("TELEGRAM_BOT_TOKEN", _polled_prefixes, service="ict-telegram-bot")
    if application.job_queue is not None:
        # A HEARTBEAT, so the claim EXPIRES on its own if this process dies —
        # a claim written once and never refreshed outlives the thing it asserts.
        application.job_queue.run_repeating(
            _register_poll,
            interval=heartbeat_interval_seconds(),
            first=heartbeat_interval_seconds(),
            name="telegram_poll_heartbeat",
        )
    log_poll_banner(
        "TELEGRAM_BOT_TOKEN", _polled_prefixes, service="ict-telegram-bot",
        log=logger,
    )

    application.run_polling()


if __name__ == "__main__":
    main()
