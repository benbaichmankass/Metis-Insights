import sqlite3
from datetime import datetime, timezone
from src.runtime.signal_notifications import ensure_signals_table, insert_signal

DB_PATH = "data/trades.db"

def write_signal(symbol, signal_type, direction=None, price=None, timeframe=None, reason=None, metadata="{}"):
    conn = sqlite3.connect(DB_PATH)
    ensure_signals_table(conn)
    insert_signal(
        conn=conn,
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol=symbol,
        signal_type=signal_type,
        direction=direction,
        price=price,
        timeframe=timeframe,
        reason=reason,
        metadata=metadata,
    )
    conn.close()
