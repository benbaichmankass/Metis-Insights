"""Trade formatting and closeall helpers extracted from telegram_query_bot.py (D3 / PR-10).

Contains trade-row formatter, per-strategy close helpers, and the strategies
dashboard formatter. Pure functions — no bot state, no Telegram imports.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Display labels for per-strategy close buttons.
_CLOSE_BUTTON_LABELS = {
    "breakout_confirmation": "Breakout",
    "vwap": "VWAP",
    "ict": "ICT",
    "killzone": "KillZone",
}


def _format_trade_row(row: dict) -> str:
    """Render one trade-journal row using the /last5 emoji template.

    Plain text only — DB-sourced fields routinely contain ``*``, ``_``, ``[``,
    or backticks that crash Telegram's legacy Markdown parser.
    """
    return (
        f"🔔 Trade #{row['id']}\n"
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


def _render_closeall_results(rows: list, scope_label: str) -> str:
    """Render the per-trade outcomes from ``processor.close_open_positions``.

    Plain text — rows include account_id and symbol that may contain
    underscores; the legacy Markdown parser would crash on those.
    """
    if not rows:
        return f"ℹ️ No open trades found for {scope_label}."
    ok_count = sum(1 for r in rows if r.get("ok"))
    fail_count = len(rows) - ok_count
    lines = [f"🚨 CLOSE {scope_label.upper()}", ""]
    lines.append(f"✅ Closed {ok_count} | ❌ Failed {fail_count}")
    lines.append("")
    for r in rows:
        icon = "✅" if r.get("ok") else "❌"
        aid = r.get("account_id", "?")
        sym = r.get("symbol", "?")
        side = r.get("direction", "?")
        qty = r.get("qty", 0.0)
        suffix = ""
        if r.get("ok"):
            oid = r.get("exchange_order_id") or ""
            if oid:
                suffix = f" (id={oid})"
        else:
            err = r.get("error") or "unknown error"
            suffix = f" — {err}"
        lines.append(f"{icon} {aid} | {sym} {side} qty={qty}{suffix}")
    return "\n".join(lines)


async def _do_closeall_strategy(reply_fn, strategy_name: str) -> None:
    """Close positions for all accounts that run *strategy_name*.

    Thin shell over ``processor.close_open_positions``.
    """
    from src.units.ui import processor
    try:
        rows = processor.close_open_positions(strategy=strategy_name)
    except Exception as exc:  # noqa: BLE001
        await reply_fn(f"⚠️ Could not close positions: {exc}")
        return
    body = _render_closeall_results(rows, scope_label=strategy_name)
    await reply_fn(body[:4000])


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
