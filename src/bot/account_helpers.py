"""Account UI and smoke-test helpers extracted from telegram_query_bot.py (D3 / PR-10).

Contains pure account-listing renderers, mode-toggle keyboard builders,
risk-check renderer, account picker, and the smoke-test runner + renderer.
No bot state (TELEGRAM_CHAT_ID etc.) is referenced here — callers pass
what they need.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.units.ui import data_loaders as dl

logger = logging.getLogger(__name__)


def _render_accounts_listing(statuses: list[dict]) -> str:
    """Pure renderer for the /accounts listing body."""
    if not statuses:
        return "ℹ️ No accounts found in accounts.yaml."
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
    return "\n\n".join(lines)


def _accounts_toggle_keyboard(statuses: list[dict]) -> InlineKeyboardMarkup:
    """Sprint 025 T4 — one mode-toggle button per account.

    Each button label describes where the account is going (two taps
    required to apply — pick then confirm).
    """
    rows: list[list[InlineKeyboardButton]] = []
    for s in statuses:
        name = s["name"]
        dry = s.get("dry_run", True)
        target = "live" if dry else "dry"
        target_icon = "🔴" if target == "live" else "🧪"
        cur = "dry" if dry else "live"
        rows.append([InlineKeyboardButton(
            f"{name}: {cur} → {target_icon} {target}",
            callback_data=f"acct_flip_ask:{name}:{target}",
        )])
    return InlineKeyboardMarkup(rows)


def _accounts_confirm_keyboard(name: str, target: str) -> InlineKeyboardMarkup:
    """Sprint 025 T4 — confirm-or-cancel keyboard for a pending flip."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"✅ Confirm flip to {target.upper()}",
            callback_data=f"acct_flip_do:{name}:{target}",
        ),
        InlineKeyboardButton(
            "✖️ Cancel", callback_data="acct_flip_cancel"),
    ]])


def _render_risk_check_for_account(statuses: list[dict], account_name: str) -> str:
    """Format the /risk_check body for one account. Pure renderer."""
    match = next((s for s in statuses if s["name"].lower() == account_name.lower()), None)
    if match is None:
        names = ", ".join(f"`{s['name']}`" for s in statuses)
        return f"⚠️ Account `{account_name}` not found.\nAvailable: {names}"
    halted_icon = "🔴 HALTED" if match.get("halted") else "🟢 OK"
    pnl = float(match.get("daily_pnl", 0))
    limit = float(match.get("max_daily_loss_usd", 0))
    remaining = float(match.get("daily_loss_remaining", limit + pnl))
    pos_size = float(match.get("max_pos_size_usd", 0))
    dd_pct = float(match.get("max_dd_pct", 0)) * 100
    open_pos = match.get("open_positions", 0)
    return (
        f"🔍 *Risk Check: {match['name']}*\n\n"
        f"Status: {halted_icon}\n"
        f"Exchange: `{match.get('exchange', '?')}` | "
        f"Type: `{match.get('account_type', '?')}`\n\n"
        f"💵 Daily PnL: ${pnl:+.2f}\n"
        f"💰 Daily loss limit: ${limit:.0f}\n"
        f"🔋 Remaining budget: ${remaining:.2f}\n"
        f"📦 Max position size: ${pos_size:.0f}\n"
        f"📉 Max drawdown: {dd_pct:.1f}%\n"
        f"📂 Open positions: {open_pos}"
    )


def _account_picker_keyboard(
    callback_prefix: str,
    statuses: list[dict],
    *,
    include_all: bool = False,
    all_label: str = "🌐 All accounts",
) -> InlineKeyboardMarkup:
    """Build a 2-column InlineKeyboardMarkup of account-picker buttons.

    Used by /risk_check (include_all=False) and /smoke_test (include_all=True).
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for s in statuses:
        name = s["name"]
        exch = s.get("exchange", "?")
        row.append(InlineKeyboardButton(
            f"{name} ({exch})", callback_data=f"{callback_prefix}:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if include_all:
        rows.append([InlineKeyboardButton(
            all_label, callback_data=f"{callback_prefix}:all")])
    return InlineKeyboardMarkup(rows)


def _smoke_test_client_factory(account_cfg: dict):
    """Resolve a per-account exchange client for the smoke test."""
    exchange = str((account_cfg or {}).get("exchange", "")).lower()
    if exchange == "bybit":
        return dl.bybit_client_for(account_cfg)
    if exchange == "binance":
        try:
            return dl.binance_conn_for(account_cfg)
        except AttributeError:
            return None
    return None


def _render_smoke_test_result(result: dict) -> str:
    """Format the operator-facing smoke-test result. Pure renderer."""
    smoke_id = result.get("smoke_id", "?")
    pkg = result.get("package", {})
    lines = [
        f"🧪 *Smoke test* `{smoke_id}`",
        f"Symbol: `{pkg.get('symbol', '?')}` | Dir: `{pkg.get('direction', '?')}`"
        f" | Qty: `{pkg.get('qty', '?')}`",
        "",
    ]
    if not result.get("results"):
        lines.append("⚠️ No accounts evaluated. " + str(result.get("error") or ""))
    for r in result.get("results", []):
        status = r.get("status", "?")
        icon = {
            "rejected_too_small": "✅",
            "submitted":          "⚠️",
            "error":              "❌",
        }.get(status, "❌")
        reason = (r.get("reason") or "")[:160]
        logged = "📝" if r.get("logged") else "⚠️ not-logged"
        lines.append(
            f"{icon} `{r.get('account_id', '?')}` ({r.get('exchange', '?')})"
            f" — *{status}* {logged}\n  ↳ {reason}"
        )

    if result.get("ok"):
        lines.append("\n*Test successful*: order reached the exchange and was "
                     "rejected (below min-lot) — API integration is live.")
    else:
        lines.append("\n⚠️ *Test failed*: see per-account reasons above. "
                     "If reason mentions credentials, the bot's environment "
                     "is missing the per-account API key/secret env vars.")
    return "\n".join(lines)


async def _run_smoke_test(account_id: str | None, coord) -> dict:
    """Dispatch the coordinator's smoke-test runner off the bot's event loop."""
    try:
        return await asyncio.to_thread(
            coord.smoke_test_run,
            account_id,
            exchange_client_factory=_smoke_test_client_factory,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"smoke_test_run raised: {exc}", "results": []}
