"""Signal-viewer helpers extracted from telegram_query_bot.py (D3 / PR-10).

Contains signal-audit log reader, signal row formatter, and the /signals
stepper keyboard builders. Pure helpers — no Telegram bot state, no
coordinator, no exchange calls.
"""
from __future__ import annotations

import json
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.utils.paths import repo_root as _repo_root

logger = logging.getLogger(__name__)

_REPO_ROOT = str(_repo_root())

_SIG_AUDIT_CANDIDATES = [
    os.environ.get("SIGNAL_AUDIT_PATH", ""),
    os.path.join(_REPO_ROOT, "runtime_logs", "signal_audit.jsonl"),
]
SIGNAL_AUDIT_PATH = next(
    (p for p in _SIG_AUDIT_CANDIDATES if p and os.path.exists(p)),
    os.path.join(_REPO_ROOT, "runtime_logs", "signal_audit.jsonl"),
)

_SIGNAL_STATUS_EMOJI = {
    "submitted": "🟢",
    "dry_run":   "🟡",
    "skipped":   "⚪️",
    "halted":    "🛑",
    "failed_validation": "🔴",
    "failed_exchange":   "❌",
    "refused":   "🚫",
    "blocked":   "🚫",
}

# Sprint 025 T3 — pre-defined N buckets the operator can pick with one tap.
_SIGNALS_N_CHOICES: list[int] = [10, 25, 50, 100]


def _read_audit_tail(path: str, limit: int) -> list[dict]:
    """Return the last ``limit`` JSON records from ``path`` (newest LAST)."""
    if not os.path.exists(path):
        return []
    try:
        from collections import deque
        wanted = max(limit * 4, 50)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=wanted)
        out: list[dict] = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("_read_audit_tail(%s): %s", path, exc)
        return []


def _format_signal_row(rec: dict) -> str:
    """Render one signal_audit.jsonl record for a Telegram block.

    Plain text only — pipeline statuses/reasons contain underscores that
    break Telegram's legacy Markdown italic parsing.
    """
    ts = str(rec.get("logged_at_utc", ""))[:19].replace("T", " ")
    strategy = str(rec.get("strategy", "?"))
    symbol = str(rec.get("symbol", "?"))
    side = str(rec.get("side", "?"))
    qty = rec.get("qty")
    qty_s = f"{float(qty):.4f}" if isinstance(qty, (int, float)) else "?"
    status = str(rec.get("status", "?"))
    emoji = _SIGNAL_STATUS_EMOJI.get(status, "•")
    reason = str(rec.get("reason") or "")
    reason_s = f" — {reason[:60]}" if reason else ""
    return (
        f"{emoji} {ts} | strategy={strategy} | {symbol} {side} {qty_s} "
        f"→ {status}{reason_s}"
    )


def _list_known_strategies_for_picker() -> list[str]:
    """Strategy names for the /signals first-step picker."""
    try:
        from src.units.ui.data_loaders import list_live_strategies
        names = list_live_strategies() or []
        if names:
            return list(names)
    except Exception:  # noqa: BLE001
        pass
    return ["turtle_soup", "vwap"]


def _signals_strategy_keyboard() -> InlineKeyboardMarkup:
    """Step 1 — pick strategy. Includes an 'all' option."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for name in _list_known_strategies_for_picker():
        row.append(InlineKeyboardButton(
            name, callback_data=f"signals_strat:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        "🌐 All strategies", callback_data="signals_strat:all")])
    return InlineKeyboardMarkup(rows)


def _signals_n_keyboard(strategy: str) -> InlineKeyboardMarkup:
    """Step 2 — pick N. Strategy encoded in callback_data; 'Back' returns to step 1."""
    row = [
        InlineKeyboardButton(str(n), callback_data=f"signals_n:{strategy}:{n}")
        for n in _SIGNALS_N_CHOICES
    ]
    rows = [row, [InlineKeyboardButton("« Back", callback_data="signals_top")]]
    return InlineKeyboardMarkup(rows)


def _render_signals_block(strategy_filter: str | None, limit: int) -> str:
    """Back-compat wrapper around processor.get_signals_block."""
    from src.units.ui.processor import get_signals_block
    return get_signals_block(strategy_filter=strategy_filter, limit=limit)
