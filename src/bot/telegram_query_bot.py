from src.runtime.signal_notifications import get_last_signals, format_signals
import os
import logging
import sqlite3
import asyncio
import sys
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv, dotenv_values
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import requests

# Sprint S-001 PR-C..F: route data access through the data_loaders facade.
# Sprint S-002 M2: migrated close_all_bybit_positions to (account: dict) and
# deleted get_bybit_client_from_env.
# Sprint S-002 M3: get_strategy_label is account-aware; load_account_env and
# format_target_options deleted.
from src.bot import data_loaders as dl

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
# DB_PATH: check env override, then repo root, then src/bot/ (legacy).
_DB_CANDIDATES = [
    os.environ.get("TRADE_JOURNAL_DB", ""),
    os.path.join(REPO_ROOT, "trade_journal.db"),
    os.path.join(BASE_DIR, "trade_journal.db"),
]
DB_PATH = next((p for p in _DB_CANDIDATES if p and os.path.exists(p)), os.path.join(REPO_ROOT, "trade_journal.db"))

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


def fetch_today_pnl(account_id: str | None = None) -> tuple:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        if account_id is not None:
            cur.execute(
                "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                "WHERE DATE(timestamp) = ? AND is_backtest = 0 AND account_id = ?",
                (today, account_id),
            )
        else:
            cur.execute(
                "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                "WHERE DATE(timestamp) = ? AND is_backtest = 0",
                (today,),
            )
        row = cur.fetchone()
        conn.close()
        return (row[0] or 0, float(row[1] or 0.0))
    except Exception:
        return (0, 0.0)


def fetch_open_positions_count(account_id: str | None = None) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        if account_id is not None:
            cur.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE status = 'open' AND is_backtest = 0 AND account_id = ?",
                (account_id,),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) FROM trades WHERE status = 'open' AND is_backtest = 0"
            )
        row = cur.fetchone()
        conn.close()
        return row[0] or 0
    except Exception:
        return 0


_STRATEGY_DISPLAY = {
    "killzone": "ICT",
    "ict": "ICT",
    "vwap": "VWAP",
    "breakout": "Breakout",
    "multiplexed": "Multi",
}

# Default label when STRATEGY env var is missing or unrecognised. The bot is
# live-trading only; this fallback should rarely be visible.
_DEFAULT_STRATEGY_LABEL = "Strategy"


def get_strategy_label(account: dict | None = None) -> str:
    """Return the display name for the active strategy.

    Reads ``STRATEGY`` (or legacy ``STRATEGY_NAME``) from the account's .env
    file. When called with no argument, uses the first account returned by
    ``dl.list_accounts()``. Falls back to ``_DEFAULT_STRATEGY_LABEL`` when
    STRATEGY is unset, unknown, or the env file is missing. Defensive against
    missing/malformed env files because this is called at ``post_init`` time
    and must never crash the bot.
    """
    try:
        if account is None:
            accounts = dl.list_accounts() or []
            account = accounts[0] if accounts else {}
        env_vars = _account_env(account)
        raw = str(env_vars.get("STRATEGY", env_vars.get("STRATEGY_NAME", ""))).strip().lower()
        return _STRATEGY_DISPLAY.get(raw, _DEFAULT_STRATEGY_LABEL)
    except Exception:
        return _DEFAULT_STRATEGY_LABEL



# fetch_last_5_trades and fetch_latest_backtest_result were removed in PR-F
# (Sprint S-001). /last5 now reads via dl.recent_trades_for; /latest_backtest
# and the post-backtest broadcast read via dl.latest_backtests_per_model().


def format_backtest_summary(latest):
    return (
        f"✅ *Latest backtest result*\n"
        f"🆔 Row ID: {latest['id']}\n"
        f"🗓 Run Date: {latest['run_date']}\n"
        f"🏷 Strategy Version: {latest['strategy_version']}\n"
        f"📅 Period: {latest['start_date']} → {latest['end_date']}\n"
        f"🔢 Total Trades: {latest['total_trades']}\n"
        f"✅ Winners: {latest['winning_trades']}\n"
        f"❌ Losers: {latest['losing_trades']}\n"
        f"🎯 Win Rate: {latest['win_rate']}\n"
        f"⚖️ Profit Factor: {latest['profit_factor']}\n"
        f"📈 Expectancy: {latest['expectancy']}\n"
        f"📉 Max Drawdown: {latest['max_drawdown']}\n"
        f"📉 Max Drawdown %: {latest['max_drawdown_pct']}\n"
        f"📐 Sharpe Ratio: {latest['sharpe_ratio']}\n"
        f"💵 Total PnL: {latest['total_pnl']}\n"
        f"💹 Total PnL %: {latest['total_pnl_pct']}\n"
        f"🥇 Avg Win: {latest['avg_win']}\n"
        f"🥀 Avg Loss: {latest['avg_loss']}\n"
        f"🚀 Largest Win: {latest['largest_win']}\n"
        f"💥 Largest Loss: {latest['largest_loss']}\n"
        f"🕒 Saved At: {latest['created_at']}"
    )


def run_shell_command(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    return ((result.stdout or "") + (result.stderr or "")).strip()


def get_service_status(service_name: str) -> str:
    try:
        return run_shell_command(["systemctl", "is-active", service_name]) or "unknown"
    except Exception as e:
        return f"error: {e}"


def toggle_service(service_name: str, action: str) -> str:
    try:
        result = subprocess.run(
            ["sudo", "systemctl", action, service_name],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            new_status = get_service_status(service_name)
            return f"✅ `{service_name}` {action}ed. Status: `{new_status}`"
        err = (result.stderr or result.stdout or "unknown error").strip()
        return f"❌ Failed to {action} `{service_name}`:\n{err}"
    except Exception as e:
        return f"❌ Exception toggling `{service_name}`: {e}"


def get_last_logs(lines: int = 20) -> str:
    """Return the most recent journalctl lines for the live trader service.

    Thin wrapper kept for backwards-compat with any importers; new call sites
    should use ``dl.recent_logs_for(service, n=...)`` directly.
    """
    return dl.recent_logs_for(LIVE_SERVICE_NAME, n=lines)


def _account_env(account: dict) -> dict:
    """Best-effort load of the account's .env file (for strategy-label
    rendering). Empty dict on any failure — the caller's label fallback
    handles the “unknown” case."""
    path = (account or {}).get("env_path") or ""
    if not path or not os.path.exists(path):
        return {}
    try:
        return {k: v for k, v in dotenv_values(path).items() if v is not None}
    except Exception:  # noqa: BLE001
        return {}


def format_bybit_balance(account: dict) -> str:
    """Render the per-coin Bybit balance block for one account.
    Data is sourced via ``dl.account_balance``; this function only formats."""
    label = get_strategy_label(account)
    payload = dl.account_balance(account)
    if payload is None:
        return f"💰 *{label} Balance*\n⚠️ Bybit error: balance unavailable."
    raw = (payload or {}).get("raw") or {}
    result_list = (raw.get("result") or {}).get("list") or []
    if not result_list:
        return f"💰 *{label} Balance*\nNo balance data returned from Bybit."
    coins = result_list[0].get("coin", []) or []
    lines = []
    for c in coins:
        try:
            wb = float(c.get("walletBalance", 0) or 0)
        except (TypeError, ValueError):
            wb = 0.0
        if wb <= 0:
            continue
        try:
            usd = float(c.get("usdValue", "0") or 0)
        except (TypeError, ValueError):
            usd = 0.0
        lines.append(f"{c.get('coin', '?')}: {wb:.4f} (≈ ${usd:.2f})")
    text = "\n".join(lines) if lines else "No non-zero balances found."
    return f"💰 *{label} Balance*\n{text}"


def format_bybit_positions(account: dict) -> str:
    """Render the open-positions block for one Bybit account using
    ``dl.account_open_positions`` output."""
    label = get_strategy_label(account)
    rows = dl.account_open_positions(account)
    if rows is None:
        return f"📊 *{label} Positions*\n⚠️ Bybit error: positions unavailable."
    if not rows:
        return f"📊 *{label} Positions*\nNo open positions."
    lines = []
    for p in rows:
        sym = p.get("symbol") or "?"
        side = p.get("side") or "?"
        size = p.get("size") or 0
        entry = float(p.get("entry_price") or 0)
        pnl = float(p.get("unrealised_pnl") or 0)
        lines.append(f"{sym} {side} | Size: {size} | Entry: ${entry:,.2f} | PnL: ${pnl:+.2f}")
    return f"📊 *{label} Positions*\n" + "\n".join(lines)


def close_all_bybit_positions(account: dict) -> str:
    aid = account.get("account_id", "?")
    client = dl.bybit_client_for(account)
    if client is None:
        return f"⚠️ {aid}: Bybit credentials not found."
    resp = client.get_positions(category="linear", settleCoin="USDT")
    positions = [p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0]
    if not positions:
        return f"🟢 {aid}: No open positions to close."
    closed_count = 0
    errors = []
    for p in positions:
        try:
            side = "Sell" if p["side"] == "Buy" else "Buy"
            client.place_order(
                category="linear", symbol=p["symbol"], side=side,
                orderType="Market", qty=p["size"], reduceOnly=True,
            )
            closed_count += 1
        except Exception as e:
            errors.append(f"{p['symbol']}: {str(e)}")
    msg = f"🚨 *{aid} CLOSE ALL*\n\n✅ Closed {closed_count} position(s)\n"
    if errors:
        msg += f"❌ Failed: {len(errors)}\nErrors:\n" + "\n".join(errors[:5])
    return msg


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
        # PR-C: pull the freshest backtest row through data_loaders. The loader
        # returns one row per strategy_version; we surface the newest entry —
        # which matches the legacy single-row behaviour for today's pipeline.
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



def format_binance_balance(account: dict) -> str:
    """Render the Binance Futures USDT balance block for one account.
    Total/free/used are derived from the loader's ``raw`` ccxt-style
    balance map (preserves today's UX)."""
    label = get_strategy_label(account)
    payload = dl.account_balance(account)
    if payload is None:
        return f"💰 *{label} Balance (Binance)*\n⚠️ Error: balance unavailable."
    raw = (payload or {}).get("raw") or {}
    if not raw:
        return f"💰 *{label} Balance (Binance)*\nNo data returned."
    usdt = raw.get("USDT", {}) if isinstance(raw, dict) else {}
    total = float((usdt or {}).get("total", 0) or 0)
    free = float((usdt or {}).get("free", 0) or 0)
    used = float((usdt or {}).get("used", 0) or 0)
    return (
        f"💰 *{label} Balance (Binance Futures)*\n"
        f"USDT Total: {total:.2f}\n"
        f"USDT Free: {free:.2f}\n"
        f"USDT Used: {used:.2f}"
    )


def format_binance_positions(account: dict) -> str:
    """Render the Binance open-positions block for one account using
    ``dl.account_open_positions`` output."""
    label = get_strategy_label(account)
    rows = dl.account_open_positions(account)
    if rows is None:
        return f"📊 *{label} Positions (Binance)*\n⚠️ Error: positions unavailable."
    if not rows:
        return f"📊 *{label} Positions (Binance)*\nNo open positions."
    lines = []
    for p in rows:
        sym = p.get("symbol") or "?"
        side = p.get("side") or "?"
        size = p.get("size") or 0
        entry = float(p.get("entry_price") or 0)
        pnl = float(p.get("unrealised_pnl") or 0)
        lines.append(f"{sym} {side} | Size: {size} | Entry: ${entry:,.2f} | PnL: ${pnl:+.2f}")
    return f"📊 *{label} Positions (Binance)*\n" + "\n".join(lines)


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    label = get_strategy_label()
    text = (
        f"👋 *ICT Trading Bot* — {label}\n\n"
        "Commands:\n"
        "/halt — Stop order placement immediately\n"
        "/resume — Re-enable order placement\n"
        "/status — Kill-switch state, P&L summary, service status\n"
        "/balance — Account balance\n"
        "/trades — Open positions\n"
        "/closeall — Emergency close all positions\n"
        "/strategies — Per-strategy signals, PnL and positions\n"
        "/log — Recent trader logs\n"
        "/toggle — Start or stop the trader service\n"
        "/download\\_journal — Download trade journal DB\n"
        "/last5 — Last 5 trade signals\n"
        "/backtest — Start backtest in background\n"
        "/latest\\_backtest — Backtest status/result\n"
        "/price — Current BTC price\n"
        "/accounts — List accounts (dry/live + PnL) or toggle mode\n"
        "/accounts\\_status — Per-account risk state\n"
        "/risk\\_check <account> — Risk details for one account\n"
        "/help — Show this menu"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    halted = is_halted()
    halt_line = "🔴 *HALTED* — orders blocked" if halted else "🟢 *RUNNING* — orders enabled"

    try:
        accounts = dl.list_accounts() or []
    except Exception:
        accounts = []

    account_lines: list[str] = []
    for acc in accounts:
        aid = acc.get("account_id", "?")
        label = get_strategy_label(acc)
        trade_count, total_pnl = fetch_today_pnl(account_id=aid)
        open_count = fetch_open_positions_count(account_id=aid)
        svc = acc.get("service") or LIVE_SERVICE_NAME
        svc_status = get_service_status(svc)
        account_lines.append(
            f"*{label}* (`{aid}`)\n"
            f"  📊 Trades today: {trade_count} | P&L: ${total_pnl:+.2f}\n"
            f"  📂 Open (DB): {open_count} | `{svc}`: {svc_status}"
        )

    if not account_lines:
        # Fallback: no accounts discovered — show aggregate totals as before.
        trade_count, total_pnl = fetch_today_pnl()
        open_count = fetch_open_positions_count()
        label = get_strategy_label()
        account_lines.append(
            f"*{label}* trader: `{get_service_status(LIVE_SERVICE_NAME)}`\n"
            f"  📊 Trades today: {trade_count} | P&L: ${total_pnl:+.2f}\n"
            f"  📂 Open (DB): {open_count}"
        )

    accounts_block = "\n\n".join(account_lines)
    text = (
        "✅ *ICT Trading Bot Status*\n\n"
        f"🚦 Kill-switch: {halt_line}\n\n"
        f"{accounts_block}\n\n"
        f"🤖 Telegram bot: `{get_service_status('ict-telegram-bot')}`\n"
        f"🕐 {now}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


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


def _render_account_balance(account: dict) -> str:
    """Dispatch a single account to the right balance formatter."""
    exchange = str((account or {}).get("exchange", "")).lower()
    if exchange == "bybit":
        return format_bybit_balance(account)
    if exchange == "binance":
        return format_binance_balance(account)
    label = get_strategy_label(account)
    return (
        f"💰 *{label} Balance*\n"
        f"Exchange=`{exchange or 'not set'}` — unsupported exchange."
    )


def _render_account_positions(account: dict) -> str:
    """Dispatch a single account to the right positions formatter."""
    exchange = str((account or {}).get("exchange", "")).lower()
    if exchange == "bybit":
        return format_bybit_positions(account)
    if exchange == "binance":
        return format_binance_positions(account)
    label = get_strategy_label(account)
    return (
        f"📊 *{label} Positions*\n"
        f"Exchange=`{exchange or 'not set'}` — unsupported exchange."
    )


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return
    if not accounts:
        await update.message.reply_text(
            "⚠️ No accounts configured. Add `.env` (legacy) or `.env.<id>` files.",
            parse_mode="Markdown",
        )
        return
    blocks = []
    for acc in accounts:
        try:
            blocks.append(_render_account_balance(acc))
        except Exception as e:  # noqa: BLE001
            blocks.append(f"💰 *{acc.get('account_id', '?')} Balance*\n⚠️ {e}")
    await update.message.reply_text("\n\n".join(blocks), parse_mode="Markdown")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return
    if not accounts:
        await update.message.reply_text(
            "⚠️ No accounts configured. Add `.env` (legacy) or `.env.<id>` files.",
            parse_mode="Markdown",
        )
        return
    blocks = []
    for acc in accounts:
        try:
            blocks.append(_render_account_positions(acc))
        except Exception as e:  # noqa: BLE001
            blocks.append(f"📊 *{acc.get('account_id', '?')} Positions*\n⚠️ {e}")
    await update.message.reply_text("\n\n".join(blocks), parse_mode="Markdown")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": "BTCUSDT"}, timeout=10,
        )
        price = float(resp.json()["result"]["list"][0]["lastPrice"])
        await update.message.reply_text(f"📈 *BTC/USDT:* ${price:,.2f}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch price: {e}")


def _format_trade_row(row: dict) -> str:
    """Render one trade-journal row using the /last5 emoji template."""
    return (
        f"🔔 *Trade #{row['id']}*\n"
        f"🕒 {row['timestamp']}\n💱 {row['symbol']}\n📈 {row['direction']}\n"
        f"💰 Entry: {row['entry_price']}\n🛑 SL: {row['stop_loss']}\n"
        f"🎯 TP1: {row['take_profit_1']} | TP2: {row['take_profit_2']} | TP3: {row['take_profit_3']}\n"
        f"📦 Size: {row['position_size']}\n"
        f"🧠 {row['setup_type']} | {row['bias']} | {row['killzone']}\n"
        f"📝 {row['entry_reason']}\n🚪 {row['exit_reason']}\n"
        f"💵 PnL: {row['pnl']} ({row['pnl_percent']}%)\n"
        f"📌 {row['status']}\n📓 {row['notes']}\n"
        f"🧪 Backtest: {bool(row['is_backtest'])}\n🕒 {row['created_at']}"
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
    chart_candidates = [
        os.path.join(BASE_DIR, "ict_complete_chart.html"),
        os.path.join(BASE_DIR, "ict_enhanced_chart.html"),
        os.path.join(BASE_DIR, "swing_chart.html"),
    ]
    available_chart = next(
        (p for p in chart_candidates if os.path.exists(p)), None)
    for row in rows:
        try:
            await update.message.reply_text(
                _format_trade_row(row), parse_mode="Markdown")
            if available_chart:
                await update.message.reply_document(
                    document=open(available_chart, "rb"))
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(
                f"⚠️ Could not render trade: {e}")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception:
        accounts = []
    if not accounts:
        try:
            log_text = get_last_logs(lines=20)
            label = get_strategy_label()
            await update.message.reply_text(
                f"📝 *{label} logs*\n```{log_text[-3500:]}```",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Could not read logs: {e}")
        return
    for acc in accounts:
        svc = acc.get("service") or LIVE_SERVICE_NAME
        label = get_strategy_label(acc)
        try:
            log_text = dl.recent_logs_for(svc, n=20)
            await update.message.reply_text(
                f"📝 *{label}* (`{svc}`)\n```{log_text[-3500:]}```",
                parse_mode="Markdown",
            )
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"⚠️ Could not read logs for `{svc}`: {e}")


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


# Display labels for per-strategy close buttons.
_CLOSE_BUTTON_LABELS = {
    "breakout_confirmation": "Breakout",
    "vwap": "VWAP",
    "ict": "ICT",
    "killzone": "KillZone",
}


async def _do_closeall_strategy(reply_fn, strategy_name: str) -> None:
    """Close positions for all Bybit accounts that run *strategy_name*."""
    try:
        accounts = dl.list_accounts() or []
        bybit_accounts = [a for a in accounts if (a.get("exchange") or "").lower() == "bybit"]
    except Exception as e:
        await reply_fn(f"⚠️ Could not list accounts: {e}")
        return
    if not bybit_accounts:
        await reply_fn("⚠️ No Bybit accounts configured.")
        return
    results = []
    for account in bybit_accounts:
        try:
            msg = dl.close_all_bybit_positions_for_strategy(account, strategy_name)
            if msg is not None:
                results.append(msg)
        except Exception as e:
            aid = account.get("account_id", "?")
            results.append(f"⚠️ Error ({aid}): {e}")
    if not results:
        await reply_fn(f"ℹ️ No accounts configured to run strategy '{strategy_name}'.")
        return
    await reply_fn("\n\n".join(results)[:4000], parse_mode="Markdown")


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


def _format_strategies_dashboard(rows: list) -> str:
    if not rows:
        return "📊 *Strategy Dashboard*\nNo strategies configured."
    lines = ["📊 *Strategy Dashboard*\n"]
    for r in rows:
        pnl = float(r.get("pnl", 0) or 0)
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        status = r.get("status", "active")
        icon = "✅" if status == "active" else "⏸"
        model_str = f" | 🧠 `{r['model']}`" if r.get("model") else ""
        lines.append(
            f"{icon} *{r['strategy']}*\n"
            f"  🔧 `{r.get('service', '?')}`{model_str}\n"
            f"  📡 {r.get('signals_today', 0)} signals | "
            f"💵 {pnl_str} | "
            f"📂 {r.get('open_pos', 0)} open"
        )
    return "\n\n".join(lines)


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


async def cmd_sprintlet_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Report sprintlet milestone status. Usage: /sprintlet_status <milestone>"""
    if not is_authorised(update):
        return
    milestone = " ".join(context.args) if context.args else "update"
    await update.message.reply_text(f"✅ Sprintlet S-008.5: {milestone}")


async def cmd_sprintlet_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Signal sprintlet completion."""
    if not is_authorised(update):
        return
    await update.message.reply_text(
        "🎉 Sprintlet S-008.5 COMPLETE. Resume at CP-2026-04-29-58. Ready for S-009."
    )


async def cmd_checkpoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the latest checkpoint ID from CHECKPOINT_LOG.md."""
    if not is_authorised(update):
        return
    log_path = os.path.join(REPO_ROOT, "docs", "claude", "checkpoints", "CHECKPOINT_LOG.md")
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            cp_lines = [ln.strip() for ln in fh if ln.strip().startswith("## CP-")]
        latest = cp_lines[0] if cp_lines else "No checkpoint found"
        await update.message.reply_text(f"Latest checkpoint: {latest}")
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Could not read checkpoint log: {exc}")


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
    if not is_authorised(update):
        return
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

    parts = (query.data or "").split(":", 1)
    if not parts or not parts[0]:
        return
    action = parts[0]

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
            # Close ALL positions across all Bybit accounts
            try:
                accounts = dl.list_accounts() or []
                bybit_accounts = [
                    a for a in accounts if (a.get("exchange") or "").lower() == "bybit"
                ]
            except Exception as e:
                await query.edit_message_text(f"⚠️ Could not list accounts: {e}")
                return
            if not bybit_accounts:
                await query.edit_message_text("⚠️ No Bybit accounts configured.")
                return
            await query.edit_message_text(
                f"🚨 Closing positions across {len(bybit_accounts)} Bybit account(s)…"
            )
            results = []
            for account in bybit_accounts:
                try:
                    results.append(close_all_bybit_positions(account))
                except Exception as e:
                    aid = account.get("account_id", "?")
                    results.append(f"⚠️ Error ({aid}): {e}")
            await query.edit_message_text(
                "\n\n".join(results)[:4000], parse_mode="Markdown"
            )


async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List accounts with dry/live mode, or toggle: /accounts dry|live <name>"""
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

    # /accounts → list all with dry/live status
    try:
        statuses = coord.accounts_status()
        if not statuses:
            await update.message.reply_text("ℹ️ No accounts found in accounts.yaml.")
            return
        lines = ["📋 *Accounts* (dry/live + risk)\n"]
        for s in statuses:
            dry = s.get("dry_run", True)
            mode_icon = "🧪 dry" if dry else "🔴 live"
            halted_icon = " 🛑HALTED" if s.get("halted") else ""
            pnl = float(s.get("daily_pnl", 0))
            limit = float(s.get("max_daily_loss_usd", 0))
            lines.append(
                f"{mode_icon}{halted_icon} — *{s['name']}* (`{s.get('exchange', '?')}`)\n"
                f"  💵 PnL ${pnl:+.2f} / ${limit:.0f} | Type: {s.get('account_type', '?')}"
            )
        lines.append("\nUse `/accounts dry|live <name>` to toggle.")
        await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load accounts: {e}")


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
    """Show per-account risk state from config/accounts.yaml via Coordinator."""
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
        lines = ["📋 *Accounts Risk Status*\n"]
        for s in statuses:
            halted_icon = "🔴" if s.get("halted") else "🟢"
            pnl = float(s.get("daily_pnl", 0))
            limit = float(s.get("max_daily_loss_usd", 0))
            pos_size = float(s.get("max_pos_size_usd", 0))
            open_pos = s.get("open_positions", 0)
            lines.append(
                f"{halted_icon} *{s['name']}* (`{s.get('exchange', '?')}` / {s.get('account_type', '?')})\n"
                f"  💵 Daily PnL: ${pnl:+.2f} / limit ${limit:.0f}\n"
                f"  📦 Max pos: ${pos_size:.0f} | Open: {open_pos}"
            )
        await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load accounts status: {e}")


async def cmd_risk_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check risk state for a specific account. Usage: /risk_check <account_name>"""
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
            names = ", ".join(f"`{s['name']}`" for s in statuses)
            await update.message.reply_text(
                f"ℹ️ Specify an account name. Available: {names}",
                parse_mode="Markdown",
            )
            return
        match = next((s for s in statuses if s["name"].lower() == account_name), None)
        if match is None:
            names = ", ".join(f"`{s['name']}`" for s in statuses)
            await update.message.reply_text(
                f"⚠️ Account `{account_name}` not found.\nAvailable: {names}",
                parse_mode="Markdown",
            )
            return
        halted_icon = "🔴 HALTED" if match.get("halted") else "🟢 OK"
        pnl = float(match.get("daily_pnl", 0))
        limit = float(match.get("max_daily_loss_usd", 0))
        remaining = float(match.get("daily_loss_remaining", limit + pnl))
        pos_size = float(match.get("max_pos_size_usd", 0))
        dd_pct = float(match.get("max_dd_pct", 0)) * 100
        open_pos = match.get("open_positions", 0)
        text = (
            f"🔍 *Risk Check: {match['name']}*\n\n"
            f"Status: {halted_icon}\n"
            f"Exchange: `{match.get('exchange', '?')}` | Type: `{match.get('account_type', '?')}`\n\n"
            f"💵 Daily PnL: ${pnl:+.2f}\n"
            f"💰 Daily loss limit: ${limit:.0f}\n"
            f"🔋 Remaining budget: ${remaining:.2f}\n"
            f"📦 Max position size: ${pos_size:.0f}\n"
            f"📉 Max drawdown: {dd_pct:.1f}%\n"
            f"📂 Open positions: {open_pos}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not check risk for '{account_name}': {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def post_init(app):
        label = get_strategy_label()
        commands = [
            BotCommand("start", "Show help"),
            BotCommand("help", "Show help"),
            BotCommand("halt", "Stop order placement immediately"),
            BotCommand("resume", "Re-enable order placement"),
            BotCommand("status", "Kill-switch state, P&L summary, service status"),
            BotCommand("balance", "Account balance"),
            BotCommand("trades", "Open positions"),
            BotCommand("closeall", f"Close all {label} positions"),
            BotCommand("strategies", "Per-strategy signals, PnL and positions"),
            BotCommand("last5", "Last 5 journal entries"),
            BotCommand("backtest", "Run backtest"),
            BotCommand("latest_backtest", "Latest backtest result"),
            BotCommand("log", f"Show {label} trader logs"),
            BotCommand("toggle", f"Start/stop {label} trader"),
            BotCommand("download_journal", "Download trade journal DB"),
            BotCommand("price", "Current BTC price"),
            BotCommand("alerts", "Recent unit alerts (coordinator queue)"),
            BotCommand("backtest_ui", "How to launch the Streamlit backtesting dashboard"),
            BotCommand("accounts", "List accounts or toggle dry/live: /accounts dry|live <name>"),
            BotCommand("accounts_status", "Per-account risk state (daily PnL, halted)"),
            BotCommand("risk_check", "Risk details for one account: /risk_check <name>"),
        BotCommand("sprintlet_status", "Report sprintlet milestone status"),
        BotCommand("sprintlet_complete", "Signal sprintlet completion"),
        BotCommand("checkpoint", "Show latest checkpoint from CHECKPOINT_LOG.md"),
        ]
        await app.bot.set_my_commands(commands)

    application.post_init = post_init
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("halt", cmd_halt))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("trades", cmd_trades))
    application.add_handler(CommandHandler("closeall", cmd_closeall))
    application.add_handler(CommandHandler("strategies", cmd_strategies))
    application.add_handler(CommandHandler("last5", cmd_last5))
    application.add_handler(CommandHandler("backtest", cmd_backtest))
    application.add_handler(CommandHandler("latest_backtest", cmd_latest_backtest))
    application.add_handler(CommandHandler("log", cmd_log))
    application.add_handler(CommandHandler("toggle", cmd_toggle))
    application.add_handler(CommandHandler("download_journal", cmd_download_journal))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("alerts", cmd_alerts))
    application.add_handler(CommandHandler("backtest_ui", cmd_backtest_ui))
    application.add_handler(CommandHandler("accounts", cmd_accounts))
    application.add_handler(CommandHandler("accounts_status", cmd_accounts_status))
    application.add_handler(CommandHandler("risk_check", cmd_risk_check))
    application.add_handler(CommandHandler("sprintlet_status", cmd_sprintlet_status))
    application.add_handler(CommandHandler("sprintlet_complete", cmd_sprintlet_complete))
    application.add_handler(CommandHandler("checkpoint", cmd_checkpoint))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.run_polling()


if __name__ == "__main__":
    main()
