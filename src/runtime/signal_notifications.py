"""Signal-table I/O + the recent-signals formatter used by the Telegram bot.

This module used to host a pile of legacy notification helpers
(matplotlib chart renderers, twice-daily summaries, per-trade open/close
formatters) that were superseded by ``src/runtime/hourly_report.py`` in
S-022 and the multi-account renderer overhaul in S-023. They had zero
callers across the live codebase, so they were removed in CP-2026-05-02-08
(G6 cleanup) along with the matplotlib transitive dependency they pulled
in.

Surviving surface:

* ``get_last_signals(conn, table, limit)`` / ``format_signals(df)`` —
  consumed by ``src/bot/telegram_query_bot.py`` for the
  one-line-per-signal text view.
* ``ensure_signals_table(conn)`` / ``insert_signal(...)`` — consumed by
  ``src/runtime/signal_writer.py`` (and any future code path that needs
  to append to ``signals.db``).
* ``fetch_df`` — internal helper used by ``get_last_signals``.
"""
import pandas as pd


def fetch_df(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)


def get_last_signals(conn, table='signals', limit=5):
    sql = f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT ?"
    return fetch_df(conn, sql, (limit,))


def format_signals(df):
    if df is None or df.empty:
        return "No signals generated yet."
    lines = ["\U0001f4ca Last 5 Signals\n"]
    for _, r in df.iterrows():
        lines.append(
            f"• {str(r.get('timestamp',''))[:19]} | "
            f"{r.get('symbol','?')} | "
            f"{r.get('signal_type', r.get('type','?'))} | "
            f"{r.get('direction', r.get('side',''))} | "
            f"{r.get('price', r.get('price_level', r.get('entry_price','')))}")
    return "\n".join(lines)


def ensure_signals_table(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        direction TEXT,
        price REAL,
        timeframe TEXT,
        reason TEXT,
        metadata TEXT
    )
    """)
    # Migration: add signal_type to tables created before the column was
    # required. Deployed DBs that pre-date the column hit the warning:
    #   data_loaders._count_signals_today: no such column: signal_type
    # ALTER TABLE cannot add NOT NULL without a default on existing rows,
    # so we add as TEXT DEFAULT '' and rely on new rows being inserted
    # with an explicit value via insert_signal().
    cur.execute("PRAGMA table_info(signals)")
    cols = {row[1] for row in cur.fetchall()}
    if "signal_type" not in cols:
        cur.execute("ALTER TABLE signals ADD COLUMN signal_type TEXT DEFAULT ''")
    conn.commit()


def insert_signal(
    conn,
    timestamp,
    symbol,
    signal_type,
    direction=None,
    price=None,
    timeframe=None,
    reason=None,
    metadata="{}",
):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO signals
        (timestamp, symbol, signal_type, direction, price, timeframe, reason, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp, symbol, signal_type, direction, price, timeframe, reason, metadata),
    )
    conn.commit()
