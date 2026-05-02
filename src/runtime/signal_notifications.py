import sqlite3
import pandas as pd
import matplotlib.pyplot as plt

def load_db(path):
    return sqlite3.connect(path)

def fetch_df(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)

def get_last_signals(conn, table='signals', limit=5):
    sql = f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT ?"
    return fetch_df(conn, sql, (limit,))

def format_signals(df):
    if df is None or df.empty:
        return "No signals generated yet."
    lines = ["📊 Last 5 Signals\n"]
    for _, r in df.iterrows():
        lines.append(
            f"• {str(r.get('timestamp',''))[:19]} | "
            f"{r.get('symbol','?')} | "
            f"{r.get('signal_type', r.get('type','?'))} | "
            f"{r.get('direction', r.get('side',''))} | "
            f"{r.get('price', r.get('price_level', r.get('entry_price','')))}"
        )
    return "\n".join(lines)

def summarize_trades(df):
    if df is None or df.empty:
        return {"wins": 0, "losses": 0, "pnl": 0.0, "balance": None}
    pnl_col = "pnl" if "pnl" in df.columns else ("profit" if "profit" in df.columns else None)
    pnl = float(df[pnl_col].fillna(0).sum()) if pnl_col else 0.0
    wins = int((df[pnl_col] > 0).sum()) if pnl_col else 0
    losses = int((df[pnl_col] <= 0).sum()) if pnl_col else 0
    balance_col = "balance" if "balance" in df.columns else None
    balance = float(df[balance_col].dropna().iloc[-1]) if balance_col and df[balance_col].notna().any() else None
    return {"wins": wins, "losses": losses, "pnl": pnl, "balance": balance}

def _plot_base(df, title, out):
    fig, ax = plt.subplots(figsize=(12, 6))
    if df is not None and len(df):
        sdf = df.copy()
        if "timestamp" in sdf.columns:
            sdf["timestamp"] = pd.to_datetime(sdf["timestamp"], errors="coerce")
        if "price" in sdf.columns:
            ax.plot(sdf["timestamp"], sdf["price"], color="black", linewidth=1.2)
        if "signal_type" in sdf.columns and "price" in sdf.columns and "timestamp" in sdf.columns:
            palette = {"fvg": "green", "order_block": "blue", "momentum": "orange"}
            for sig, color in palette.items():
                part = sdf[sdf["signal_type"].astype(str).str.contains(sig, case=False, na=False)]
                if len(part):
                    ax.scatter(part["timestamp"], part["price"], s=55, color=color, label=sig.upper())
    ax.set_title(title)
    ax.set_xlabel("Time")
    ax.set_ylabel("Price")
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out

def plot_signal_summary(df, out="summary.png"):
    return _plot_base(df, "Signals & Trades - Last 24h", out)

def plot_trade_chart(df, trade=None, out="trade_chart.png"):
    fig, ax = plt.subplots(figsize=(12, 6))
    if df is not None and len(df):
        sdf = df.copy()
        if "timestamp" in sdf.columns:
            sdf["timestamp"] = pd.to_datetime(sdf["timestamp"], errors="coerce")
        if "price" in sdf.columns:
            ax.plot(sdf["timestamp"], sdf["price"], color="black", linewidth=1.2)
    if trade:
        if trade.get("entry_time") and trade.get("entry_price") is not None:
            ax.scatter([pd.to_datetime(trade["entry_time"])], [trade["entry_price"]],
                       color="limegreen", s=120, marker="^", label="ENTRY")
            ax.axhline(trade["entry_price"], color="limegreen", linestyle="--", alpha=0.7)
        if trade.get("stop_loss") is not None:
            ax.axhline(trade["stop_loss"], color="red", linestyle="--", alpha=0.7, label="SL")
        if trade.get("take_profit") is not None:
            ax.axhline(trade["take_profit"], color="royalblue", linestyle="--", alpha=0.7, label="TP")
        if trade.get("exit_time") and trade.get("exit_price") is not None:
            ax.scatter([pd.to_datetime(trade["exit_time"])], [trade["exit_price"]],
                       color="darkorange", s=120, marker="x", label="EXIT")
            ax.axhline(trade["exit_price"], color="darkorange", linestyle=":", alpha=0.8)
    ax.set_title("Trade View")
    ax.set_xlabel("Time")
    ax.set_ylabel("Price")
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out

def msg_started():
    return "🚀 Bot started. Runtime monitoring is active."

def msg_stopped():
    return "🛑 Bot shut down. Runtime monitoring has stopped."

def msg_bi_daily(stats):  # noqa: ARG001 — kept as a hard error for old call sites
    """Removed CP-2026-05-02. The twice-a-day summary was replaced by the
    hourly report (``src/runtime/hourly_report.build_hourly_report``) in
    S-022 PR2. Calling this raises so any remaining importer fails loudly
    instead of silently sending the legacy format the operator stopped
    expecting.
    """
    raise RuntimeError(
        "msg_bi_daily was removed — use "
        "src.runtime.hourly_report.build_hourly_report() instead. "
        "If you saw a 'Bi-daily summary' message after CP-2026-05-02, "
        "the VM has not pulled latest main yet."
    )

def msg_trade_open(trade):
    return (
        "🟢 Trade opened\n\n"
        f"{trade.get('symbol','?')} {trade.get('side','?')}\n"
        f"Entry: {trade.get('entry_price')} @ {trade.get('entry_time')}\n"
        f"SL: {trade.get('stop_loss')}\n"
        f"TP: {trade.get('take_profit')}"
    )

def msg_trade_close(trade):
    return (
        "🔔 Trade closed\n\n"
        f"{trade.get('symbol','?')} {trade.get('side','?')}\n"
        f"Entry: {trade.get('entry_price')} @ {trade.get('entry_time')}\n"
        f"Exit: {trade.get('exit_price')} @ {trade.get('exit_time')}\n"
        f"P/L: {trade.get('pnl')}"
    )


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
