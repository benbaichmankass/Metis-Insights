"""Trade notification helpers — extracted from telegram_query_bot.py (PR-4).

Pure formatting + DB-query functions for trade, balance, and position
notifications. No Telegram framework dependency; no async code.
All data access goes through src.units.ui.data_loaders (the canonical
facade) or lazy-imports of src.units.ui.processor.
"""
from __future__ import annotations

import os
from typing import Any

from dotenv import dotenv_values

from src.units.ui import data_loaders as dl

# ── Strategy label mapping ─────────────────────────────────────────────────

_STRATEGY_DISPLAY = {
    "killzone": "ICT",
    "ict": "ICT",
    "vwap": "VWAP",
    "breakout": "Breakout",
    "breakout_confirmation": "Breakout",
    "turtle_soup": "Turtle Soup",
    "multiplexed": "Multi",
}

_DEFAULT_STRATEGY_LABEL = "Strategy"


def get_strategy_label(account: dict | None = None) -> str:
    """Return the display name for the active strategy.

    Resolution order:
      1. STRATEGY (or legacy STRATEGY_NAME) in the account's .env file.
      2. account["strategies"] from accounts.yaml.
      3. Process-wide STRATEGY env var.
      4. _DEFAULT_STRATEGY_LABEL.
    """
    try:
        if account is None:
            accounts = dl.list_accounts() or []
            account = accounts[0] if accounts else {}

        env_vars = _account_env(account)
        raw = str(env_vars.get("STRATEGY", env_vars.get("STRATEGY_NAME", ""))).strip().lower()
        if raw:
            label = _STRATEGY_DISPLAY.get(raw)
            if label:
                return label

        strategies = account.get("strategies") if isinstance(account, dict) else None
        if isinstance(strategies, list) and strategies:
            normalized = [str(s).strip().lower() for s in strategies if s]
            if len(normalized) == 1:
                label = _STRATEGY_DISPLAY.get(normalized[0])
                if label:
                    return label
            elif len(normalized) > 1:
                return _STRATEGY_DISPLAY["multiplexed"]

        proc_raw = str(os.environ.get("STRATEGY", "")).strip().lower()
        if proc_raw:
            label = _STRATEGY_DISPLAY.get(proc_raw)
            if label:
                return label

        return _DEFAULT_STRATEGY_LABEL
    except Exception:
        return _DEFAULT_STRATEGY_LABEL


# ── Trade DB helpers ───────────────────────────────────────────────────────

def fetch_today_pnl(account_id: str | None = None) -> tuple:
    """Return (trade_count, total_pnl_usd) for today.

    Back-compat wrapper around src.units.ui.processor.get_today_pnl.
    """
    from src.units.ui.processor import get_today_pnl
    result = get_today_pnl(account_id)
    return (result["trade_count"], result["total_pnl_usd"])


def fetch_open_positions_count(account_id: str | None = None) -> int:
    """Return count of open positions. Back-compat wrapper."""
    from src.units.ui.processor import get_open_positions_count
    return get_open_positions_count(account_id)


# ── Backtest formatter ─────────────────────────────────────────────────────

def format_backtest_summary(latest: Any) -> str:
    return (
        f"✅ *Latest backtest result*\n"
        f"🆔 Row ID: {latest['id']}\n"
        f"🗓 Run Date: {latest['run_date']}\n"
        f"📈 Strategy: {latest.get('strategy_version', 'N/A')}\n"
        f"📊 Total Trades: {latest.get('total_trades', 'N/A')}\n"
        f"💰 Total PnL: ${latest.get('total_pnl', 0):.2f}\n"
        f"🏆 Win Rate: {latest.get('win_rate', 0):.1f}%\n"
        f"📉 Max Drawdown: {latest.get('max_drawdown', 0):.1f}%"
    )


# ── Account credential / key helpers ──────────────────────────────────────

def _account_env(account: dict) -> dict:
    """Best-effort load of the account's .env file. Empty dict on failure."""
    path = (account or {}).get("env_path") or ""
    if not path or not os.path.exists(path):
        return {}
    try:
        return {k: v for k, v in dotenv_values(path).items() if v is not None}
    except Exception:  # noqa: BLE001
        return {}


def _bybit_creds_diagnostic(account: dict) -> str | None:
    """Return a diagnostic string when an account is missing Bybit creds.

    Returns None when both API key + secret env vars are present.
    """
    return dl.credentials_check(account or {})


def _account_key_fingerprint(account: dict) -> str | None:
    """Last-4 of the resolved API key, or None if unresolvable."""
    try:
        from src.units.accounts.clients import resolve_credentials
        creds = resolve_credentials(account or {}) or {}
        key = creds.get("api_key") or ""
        return f"…{str(key)[-4:]}" if key else None
    except Exception:  # noqa: BLE001
        return None


def _account_balance_header(account: dict, *, exchange_suffix: str = "") -> str:
    """Build the balance block header with account id, strategy, and key fingerprint."""
    aid = (account or {}).get("account_id", "?")
    strat = get_strategy_label(account)
    fp = _account_key_fingerprint(account)
    env_name = (account or {}).get("api_key_env") or ""
    base = f"`{aid}`" + (f" ({strat})" if strat and strat != _DEFAULT_STRATEGY_LABEL else "")
    suffix = f" {exchange_suffix}" if exchange_suffix else ""
    fp_part = ""
    if env_name and fp:
        fp_part = f"\n🔑 env `{env_name}` → {fp}"
    elif fp:
        fp_part = f"\n🔑 key {fp}"
    return f"💰 *{base} Balance{suffix}*{fp_part}"


def _duplicate_key_warning(accounts: list[dict]) -> str | None:
    """Return a warning string when ≥ 2 accounts resolve to the same API key."""
    by_fp: dict[str, list[str]] = {}
    for acc in accounts:
        fp = _account_key_fingerprint(acc)
        if not fp:
            continue
        by_fp.setdefault(fp, []).append(str((acc or {}).get("account_id", "?")))
    dup_lines: list[str] = []
    for fp, ids in by_fp.items():
        if len(ids) > 1:
            dup_lines.append(f"`{', '.join(sorted(ids))}` share key {fp}")
    if not dup_lines:
        return None
    return (
        "⚠️ *DUPLICATE API KEY DETECTED* — accounts below resolve to the\n"
        "same Bybit/Binance wallet, so identical balances are expected.\n"
        + "\n".join(f"  • {ln}" for ln in dup_lines)
        + "\n→ fix: edit the env file so each `api_key_env` in\n"
        "`config/accounts.yaml` points at a *distinct* key, then\n"
        "restart the trader + bot."
    )


# ── Per-exchange balance / position formatters ─────────────────────────────

def format_bybit_balance(account: dict) -> str:
    """Render the per-coin Bybit balance block for one account."""
    header = _account_balance_header(account)
    payload = dl.account_balance(account)
    if payload is None:
        diag = _bybit_creds_diagnostic(account)
        suffix = f"\n→ {diag}" if diag else ""
        return f"{header}\n⚠️ Bybit error: balance unavailable.{suffix}"
    raw = (payload or {}).get("raw") or {}
    result_list = (raw.get("result") or {}).get("list") or []
    if not result_list:
        return f"{header}\nNo balance data returned from Bybit."
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
    return f"{header}\n{text}"


def format_bybit_positions(account: dict) -> str:
    """Render the open-positions block for one Bybit account."""
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


def format_binance_balance(account: dict) -> str:
    """Render the Binance Futures USDT balance block for one account."""
    header = _account_balance_header(account, exchange_suffix="(Binance)")
    payload = dl.account_balance(account)
    if payload is None:
        return f"{header}\n⚠️ Error: balance unavailable."
    raw = (payload or {}).get("raw") or {}
    if not raw:
        return f"{header}\nNo data returned."
    usdt = raw.get("USDT", {}) if isinstance(raw, dict) else {}
    total = float((usdt or {}).get("total", 0) or 0)
    free = float((usdt or {}).get("free", 0) or 0)
    used = float((usdt or {}).get("used", 0) or 0)
    return (
        f"{header}\n"
        f"USDT Total: {total:.2f}\n"
        f"USDT Free: {free:.2f}\n"
        f"USDT Used: {used:.2f}"
    )


def format_binance_positions(account: dict) -> str:
    """Render the Binance open-positions block for one account."""
    label = get_strategy_label(account)
    rows = dl.account_open_positions(account)
    if rows is None:
        return f"📊 *{label} Positions*\n⚠️ Binance error: positions unavailable."
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
