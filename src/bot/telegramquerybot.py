import sqlite3
from pathlib import Path
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _candidate_env_dirs() -> list[Path]:
    return [_repo_root(), Path('/home/ubuntu/ict-trading-bot'), Path.cwd()]

def load_account_env_file(target: str) -> tuple[dict, str | None]:
    filename = f'.env.{str(target).strip().lower()}'
    for base in _candidate_env_dirs():
        env_path = base / filename
        if env_path.exists():
            values = {}
            for raw in env_path.read_text(encoding='utf-8').splitlines():
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
            return values, str(env_path)
    return {}, None

def resolve_trade_journal_db() -> Path | None:
    root = _repo_root()
    override = os.environ.get('TRADE_JOURNAL_DB') or os.environ.get('TRADEJOURNALDB')
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.extend([
        root / 'trade_journal.db',
        root / 'tradejournal.db',
        root / 'data' / 'trade_journal.db',
        root / 'data' / 'tradejournal.db',
        root / 'trades.db',
    ])
    for path in candidates:
        if path.exists():
            return path
    return None

class TelegramQueryBot:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id

    def env_message(self, target: str) -> str:
        values, used = load_account_env_file(target)
        if used:
            return f'Loaded {target} env from: {used}'
        return f'Environment file not found for {target}: checked repo root, /home/ubuntu/ict-trading-bot, and current working directory'

    async def last5(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db_path = resolve_trade_journal_db()
        if db_path is None:
            await update.message.reply_text('Could not load last 5 trades: no trade journal DB file found')
            return
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if 'trades' not in tables:
                await update.message.reply_text(f"Could not load last 5 trades: no such table: trades in {db_path}")
                conn.close()
                return
            rows = cur.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 5').fetchall()
            conn.close()
            if not rows:
                await update.message.reply_text(f'No trades found in {db_path}')
                return
            lines = [f'Last 5 trades from: {db_path}']
            for row in rows:
                d = dict(row)
                symbol = d.get('symbol', '?')
                side = d.get('side', d.get('direction', '?'))
                qty = d.get('qty', d.get('position_size', d.get('positionsize', '?')))
                status = d.get('status', '')
                ts = d.get('timestamp', d.get('created_at', d.get('createdat', '')))
                lines.append(f"- {ts} | {symbol} | {side} | qty={qty} | {status}")
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            await update.message.reply_text(f'Could not load last 5 trades: {e}')

    def run(self):
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler('last5', self.last5))
        print('Telegram bot ready (/last5)')
        app.run_polling()
