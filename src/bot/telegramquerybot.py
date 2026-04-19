import sqlite3
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import os

class TelegramQueryBot:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.db_path = "trades.db"
        self.init_db()
    
    def init_db(self):
        """Auto-create trades table if missing"""
        try:
            conn = sqlite3.connect(self.db_path)
            sql = """CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                qty REAL,
                status TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
            conn.execute(sql)
            conn.commit()
            print("✅ trades table ready")
        except Exception as e:
            print(f"DB error: {e}")
        finally:
            if conn:
                conn.close()
    
    async def last5(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Safe /last5 - no crash if empty"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 5")
            trades = cursor.fetchall()
            
            if not trades:
                await update.message.reply_text("🆕 No trades yet!")
                return
            
            msg = "📊 Last 5 trades:\n"
            for t in trades:
                msg += f"{t[1]} {t[2].upper()} x{t[3]:.4f} ({t[4]})\n"
            await update.message.reply_text(msg)
        except Exception as e:
            await update.message.reply_text(f"❌ Query error: {str(e)}")
        finally:
            if 'conn' in locals():
                conn.close()
    
    def run(self):
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("last5", self.last5))
        print("Telegram bot ready (/last5)")
        app.run_polling()

def load_env_fallback(target):
    candidates = [repo / f".env.{target}", repo.parent / f".env.{target}", Path.cwd() / f".env.{target}"]
    for p in candidates:
        if p.exists():
            return True
    return False
