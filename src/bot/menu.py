"""Operator command-bot menu — the simplified, menu-driven surface.

This module is the **pure** half of the trader-bot menu (the 2026-05
overhaul, see ``docs/TELEGRAM-SPEC.md``). It builds:

* the inline keyboards for the 4-item main menu + close-all and the
  two-level kill switch (by account / by strategy), and
* the three collapsible snapshot views (system / accounts / strategies)
  via ``src.units.ui.telegram_format``.

Every view renderer takes an already-fetched data structure so it is
unit-testable offline. The I/O (coordinator + data-loader reads) and the
kill-switch *writes* live in the wiring layer
(``src.bot.telegram_query_bot``) — this module performs no I/O.

Callback-data namespace (routed in ``telegram_query_bot.callback_handler``):

* ``menu:home`` / ``menu:kill`` / ``menu:kill_accounts`` /
  ``menu:kill_strats`` / ``menu:system`` / ``menu:accounts`` /
  ``menu:strategies`` / ``menu:closeall`` / ``menu:closeall_confirm``
* ``killacct:<name>:<live|dry_run>`` (ask) →
  ``killacct_do:<name>:<live|dry_run>`` (execute)
* ``killstrat:<name>:<live|shadow>`` (ask) →
  ``killstrat_do:<name>:<live|shadow>`` (execute)
"""
from __future__ import annotations

from typing import Optional, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.units.ui.telegram_format import Section, kv_block, render_html

# ── callback-data constants ────────────────────────────────────────────
CB_HOME = "menu:home"
CB_KILL = "menu:kill"
CB_KILL_ACCOUNTS = "menu:kill_accounts"
CB_KILL_STRATS = "menu:kill_strats"
CB_SYSTEM = "menu:system"
CB_ACCOUNTS = "menu:accounts"
CB_STRATEGIES = "menu:strategies"
CB_CLOSEALL = "menu:closeall"
CB_CLOSEALL_CONFIRM = "menu:closeall_confirm"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


# ── keyboards ──────────────────────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """The 4 menu items + the emergency close-all (operator decision
    2026-05-24: close-all stays as a fifth, distinct action)."""
    return InlineKeyboardMarkup([
        [_btn("🛑 Kill switch", CB_KILL)],
        [_btn("🩺 System update", CB_SYSTEM)],
        [_btn("💼 Accounts snapshot", CB_ACCOUNTS),
         _btn("📈 Strategies snapshot", CB_STRATEGIES)],
        [_btn("🚨 Close all positions", CB_CLOSEALL)],
    ])


def kill_menu_keyboard() -> InlineKeyboardMarkup:
    """Two kill switches: per-account (mode) and per-strategy (execution)."""
    return InlineKeyboardMarkup([
        [_btn("By account  (live ⇄ dry-run)", CB_KILL_ACCOUNTS)],
        [_btn("By strategy  (live ⇄ shadow)", CB_KILL_STRATS)],
        [_btn("« Menu", CB_HOME)],
    ])


def _is_account_dry(acc: dict) -> bool:
    if "dry_run" in acc:
        return bool(acc["dry_run"])
    return str(acc.get("mode", "live")).strip().lower() == "dry_run"


def account_kill_keyboard(accounts: Sequence[dict]) -> InlineKeyboardMarkup:
    """One toggle per account. Label shows current → target mode.

    Each ``acc`` needs ``account_id`` (or ``name``) and either
    ``dry_run: bool`` or ``mode: live|dry_run``.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for acc in accounts:
        name = acc.get("account_id") or acc.get("name") or "?"
        dry = _is_account_dry(acc)
        cur = "dry_run" if dry else "live"
        target = "live" if dry else "dry_run"
        icon = "🟢" if target == "live" else "🧪"
        rows.append([_btn(f"{name}: {cur} → {icon} {target}",
                          f"killacct:{name}:{target}")])
    rows.append([_btn("« Back", CB_KILL)])
    return InlineKeyboardMarkup(rows)


def account_kill_confirm_keyboard(name: str, target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _btn(f"✅ Confirm {target.upper()}", f"killacct_do:{name}:{target}"),
        _btn("✖️ Cancel", CB_KILL_ACCOUNTS),
    ]])


def strategy_kill_keyboard(strategies: Sequence[dict]) -> InlineKeyboardMarkup:
    """One toggle per strategy. Label shows current → target execution.

    Each ``s`` needs ``name`` and ``execution: live|shadow``.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for s in strategies:
        name = s.get("name") or "?"
        cur = str(s.get("execution", "live")).strip().lower()
        cur = "shadow" if cur == "shadow" else "live"
        target = "live" if cur == "shadow" else "shadow"
        icon = "🟢" if target == "live" else "🌑"
        rows.append([_btn(f"{name}: {cur} → {icon} {target}",
                          f"killstrat:{name}:{target}")])
    rows.append([_btn("« Back", CB_KILL)])
    return InlineKeyboardMarkup(rows)


def strategy_kill_confirm_keyboard(name: str, target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _btn(f"✅ Confirm {target.upper()}", f"killstrat_do:{name}:{target}"),
        _btn("✖️ Cancel", CB_KILL_STRATS),
    ]])


def closeall_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _btn("🚨 Confirm CLOSE ALL", CB_CLOSEALL_CONFIRM),
        _btn("✖️ Cancel", CB_HOME),
    ]])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn("« Menu", CB_HOME)]])


# ── view renderers (pure; HTML collapsible sections) ───────────────────

MAIN_MENU_TEXT = (
    "🤖 ICT Trading Bot — choose an action.\n"
    "Kill switch stops NEW trades; Close all also flattens open positions."
)


def render_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    return MAIN_MENU_TEXT, main_menu_keyboard()


def _amount(value: object) -> str:
    """Plain currency (balances): ``$1,234.50``. ``—`` when unparseable."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _signed(value: object) -> str:
    """Signed currency (PnL deltas): ``+$45.00`` / ``-$10.00``. ``—`` if unset."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if v < 0 else "+"
    return f"{sign}${abs(v):,.2f}"


def render_system_view(
    *,
    services: dict[str, str],
    heartbeat: Optional[dict] = None,
    kill_summary: Optional[dict] = None,
    vm: Optional[dict] = None,
) -> str:
    """'Is the system holding and running properly?'

    ``services``: ``{unit: state}`` (e.g. ``{"ict-trader-live": "active"}``).
    ``heartbeat``: ``{label, age_seconds, last_tick}``.
    ``kill_summary``: ``{accounts_live, accounts_dry, strats_live, strats_shadow}``.
    ``vm``: ``{uptime, load, mem, disk}``.
    """
    heartbeat = heartbeat or {}
    kill_summary = kill_summary or {}
    vm = vm or {}

    def _svc_icon(state: str) -> str:
        return "🟢" if state == "active" else "🔴"

    all_ok = services and all(v == "active" for v in services.values())
    hb_label = heartbeat.get("label", "unknown")
    header_line = "🟢 Holding" if all_ok and hb_label == "running" else "🟡 Check"

    svc_body = kv_block([(u, f"{_svc_icon(st)} {st}") for u, st in services.items()]) \
        or "(no services reported)"

    sections = [
        Section(
            summary=f"Services — {header_line}",
            body=svc_body,
            priority=10,
        ),
        Section(
            summary=f"Trader liveness — {hb_label}",
            body=kv_block([
                ("Heartbeat", hb_label),
                ("Age (s)", heartbeat.get("age_seconds")),
                ("Last tick", heartbeat.get("last_tick")),
            ]),
            priority=20,
        ),
        Section(
            summary=(
                "Kill switches — "
                f"{kill_summary.get('accounts_live', '?')} live / "
                f"{kill_summary.get('accounts_dry', '?')} dry · "
                f"{kill_summary.get('strats_live', '?')} live / "
                f"{kill_summary.get('strats_shadow', '?')} shadow"
            ),
            body=kv_block([
                ("Accounts live", kill_summary.get("accounts_live")),
                ("Accounts dry_run", kill_summary.get("accounts_dry")),
                ("Strategies live", kill_summary.get("strats_live")),
                ("Strategies shadow", kill_summary.get("strats_shadow")),
            ]),
            priority=30,
        ),
        Section(
            summary="VM resources",
            body=kv_block([
                ("Uptime", vm.get("uptime")),
                ("Load", vm.get("load")),
                ("Memory", vm.get("mem")),
                ("Disk", vm.get("disk")),
            ]),
            priority=40,
        ),
    ]
    return render_html(header="🩺 System update", sections=sections)


def render_accounts_view(accounts: Sequence[dict]) -> str:
    """One collapsible section per account: mode, config, balance, 24h PnL, trades.

    Each ``acc`` may carry: ``account_id``/``name``, ``exchange``,
    ``mode``/``dry_run``, ``account_type``, ``max_daily_loss_usd``,
    ``max_dd_pct``, ``balance``, ``pnl_24h``,
    ``open_positions``, ``trades`` (list of one-line strings).
    """
    if not accounts:
        return render_html(
            header="💼 Accounts snapshot",
            sections=[Section(summary="No accounts found",
                              body="config/accounts.yaml has no accounts.")],
        )
    sections = []
    for idx, acc in enumerate(accounts):
        name = acc.get("account_id") or acc.get("name") or "?"
        dry = _is_account_dry(acc)
        mode_icon = "🧪 dry_run" if dry else "🟢 live"
        bal = acc.get("balance")
        bal_str = "⚠️ unavailable" if bal is None else _amount(bal)
        trades = acc.get("trades") or []
        trades_body = "\n".join(str(t) for t in trades) if trades else "(no trades today)"
        body = kv_block([
            ("Mode", mode_icon),
            ("Exchange", acc.get("exchange")),
            ("Type", acc.get("account_type")),
            ("Balance", bal_str),
            ("24h PnL", _signed(acc.get("pnl_24h")) if acc.get("pnl_24h") is not None else "—"),
            ("Open positions", acc.get("open_positions")),
            ("Max daily loss", acc.get("max_daily_loss_usd")),
            ("Max drawdown", acc.get("max_dd_pct")),
        ]) + f"\n\nTrades:\n{trades_body}"
        sections.append(Section(
            summary=(
                f"{mode_icon.split()[0]} {name} — {bal_str} · "
                f"24h {_signed(acc.get('pnl_24h')) if acc.get('pnl_24h') is not None else '—'}"
            ),
            body=body,
            priority=10 + idx,
        ))
    return render_html(header="💼 Accounts snapshot", sections=sections)


def render_strategies_view(strategies: Sequence[dict]) -> str:
    """One collapsible section per strategy: execution, running, last signal, 24h PnL, trades.

    Each ``s`` may carry: ``name``, ``label``, ``execution``,
    ``running``, ``last_signal``, ``pnl_24h``, ``open_positions``,
    ``trade_count``, ``accounts`` (list of account ids).
    """
    if not strategies:
        return render_html(
            header="📈 Strategies snapshot",
            sections=[Section(summary="No strategies loaded",
                              body="config/strategies.yaml has no strategies.")],
        )
    sections = []
    for idx, s in enumerate(strategies):
        name = s.get("name") or "?"
        execution = str(s.get("execution", "live")).strip().lower()
        exec_icon = "🌑 shadow" if execution == "shadow" else "🟢 live"
        running = s.get("running")
        run_str = "running" if running else ("stopped" if running is not None else "—")
        body = kv_block([
            ("Execution", exec_icon),
            ("Loaded/running", run_str),
            ("Accounts", ", ".join(s.get("accounts") or []) or "—"),
            ("Last signal", s.get("last_signal")),
            ("24h PnL", _signed(s.get("pnl_24h")) if s.get("pnl_24h") is not None else "—"),
            ("Open positions", s.get("open_positions")),
            ("Lifetime trades", s.get("trade_count")),
        ])
        sections.append(Section(
            summary=(
                f"{exec_icon.split()[0]} {s.get('label') or name} — "
                f"{run_str} · 24h "
                f"{_signed(s.get('pnl_24h')) if s.get('pnl_24h') is not None else '—'}"
            ),
            body=body,
            priority=10 + idx,
        ))
    return render_html(header="📈 Strategies snapshot", sections=sections)
