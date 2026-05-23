"""UI processor — single facade between any UI surface and the data layer.

Background (CP-2026-05-02): the operator flagged that some Telegram
formatting and balance-rendering had grown into the bot module itself,
making it impossible to guarantee that the webapp and the bot would
return the same answer to the same question. The remediation is the
unit specified in CLAUDE.md:

  * The bot and the webapp are both UIs.
  * They MUST go through the same processor.
  * The processor is the only thing that talks to the units (accounts,
    strategies, runtime, journal) and to the DB.

This module is the processor. It exposes plain-Python read APIs that
return shape-stable dicts/lists. Renderers (Telegram strings, JSON for
the webapp) live in their own UI surfaces and consume these dicts.

The first read APIs cover the user-visible bugs reported in this
session:

  * ``get_account_balances()`` — per-account balance, account_id-first
    labelling, includes the resolved API-key fingerprint so duplicate
    keys are visible at the data layer (not buried in the formatter).
  * ``get_recent_signals(limit, strategy=None)`` — pipeline signals
    with explicit ``strategy`` field every consumer can show.
  * ``get_hourly_report()`` — the structured hourly text. Both bot and
    webapp render the same string.

The processor never raises. Failures are encoded as fields in the
return value so renderers can show a sane error to the operator
without try/except in every UI handler.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Account balances
# ---------------------------------------------------------------------------


def _safe_key_fingerprint(account: Dict[str, Any]) -> Optional[str]:
    """Return the last 4 chars of the resolved API key, or None.

    Source of truth lives in ``src.units.accounts.clients.resolve_credentials``
    so the processor doesn't reach into env vars itself.
    """
    try:
        from src.units.accounts.clients import resolve_credentials
        creds = resolve_credentials(account) or {}
        key = creds.get("api_key") or ""
        if not key:
            return None
        return f"…{str(key)[-4:]}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("_safe_key_fingerprint: %s", exc)
        return None


def get_account_balances() -> List[Dict[str, Any]]:
    """Return one row per account.

    Shape:
      [
        {
          "account_id": str,
          "exchange": str,
          "strategies": [str, ...],
          "key_fingerprint": str | None,   # "…xxxx"
          "status": "ok" | "missing_creds" | "api_error" | "unsupported",
          "total_usdt": float | None,
          "raw": dict | None,              # exchange-native payload
          "error": str | None,
        },
        ...
      ]

    Renderers MUST label rows by ``account_id`` first; the strategies
    list is informational. Showing two accounts under the same strategy
    label was the root cause of the operator's "balances wired to
    strategies" complaint.
    """
    try:
        from src.units.ui.data_loaders import (
            account_balance_with_diagnostic,
            list_accounts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_account_balances: data_loaders import failed: %s", exc)
        return []

    try:
        accounts = list_accounts() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_account_balances: list_accounts failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for acc in accounts:
        try:
            diag = account_balance_with_diagnostic(acc) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_account_balances: %s raised %s",
                (acc or {}).get("account_id", "?"), exc,
            )
            diag = {"status": "api_error", "total_usdt": None, "raw": None,
                    "error": f"{type(exc).__name__}: {exc}"}
        out.append({
            "account_id": acc.get("account_id"),
            "exchange": acc.get("exchange"),
            "strategies": list(acc.get("strategies") or []),
            "key_fingerprint": _safe_key_fingerprint(acc),
            "status": diag.get("status"),
            "total_usdt": diag.get("total_usdt"),
            "raw": diag.get("raw"),
            "error": diag.get("error"),
        })
    return out


# ---------------------------------------------------------------------------
# Recent signals (pipeline audit log)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /accounts_status block formatter (Velotrade phase-2b)
# ---------------------------------------------------------------------------


def _h(value: object) -> str:
    """HTML-escape *value* for Telegram parse_mode='HTML'."""
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def format_account_status_block(status: Dict[str, Any]) -> str:
    """Render one account's `/accounts_status` block as HTML.

    Pulls the per-account dict produced by
    ``Coordinator.accounts_status`` (which in turn drains
    ``TradingAccount.status()`` + ``PropRiskManager.report()``) into
    the operator-facing block. Centralising this in the processor
    means the bot stays a thin shell (CLAUDE.md architecture rule 5)
    and the same renderer is unit-testable without importing the bot.

    Velotrade phase-2 / 2b additions:

    - **Configured / not-configured.** When ``status['configured']``
      is ``False``, a "⚙️ Not configured: <reason>" line shows the
      missing env var so the operator sees instantly which account is
      inert.
    - **Prop fields.** Prop accounts render an extra two-line block
      with the phase (evaluation / funded), mission progress
      (``cumulative_pnl_pct`` vs ``target_profit_pct``), active-day
      count, and the mission-complete flag.

    Returns
    -------
    str
        HTML block (no surrounding header). Caller joins blocks
        with ``"\\n\\n"`` and prepends the page header.
    """
    halted_icon = "🔴" if status.get("halted") else "🟢"
    pnl = float(status.get("daily_pnl", 0))
    limit = float(status.get("max_daily_loss_usd", 0))
    pos_size = float(status.get("max_pos_size_usd", 0))
    open_pos = status.get("open_positions", 0)
    bal = status.get("live_balance_usdt")
    bal_err = status.get("live_balance_error")
    strategies = status.get("strategies") or []
    strat_line = (
        f"  🎯 Strategy: {_h(', '.join(strategies))}\n"
        if strategies else
        "  🎯 Strategy: <i>(none assigned)</i>\n"
    )
    key_fp = status.get("api_key_fingerprint") or "—"
    fp_line = f"  🔑 Key: …{_h(key_fp)}\n"

    # Velotrade phase-2: not-configured surface.
    cfg_line = ""
    if status.get("configured") is False:
        reason = status.get("configured_reason") or "credentials not set"
        cfg_line = f"  ⚙️ Not configured: {_h(reason)}\n"

    if bal_err:
        api_line = f"  🔌 API: ❌ {_h(bal_err)}"
    elif bal is not None:
        api_line = f"  🔌 API: ✅ Balance ${float(bal):,.2f} USDT"
    else:
        api_line = "  🔌 API: ⚠️ no balance returned"

    # Velotrade phase-2b: prop fields. Only shown when the dict
    # actually carries them (PropRiskManager.report() supplies them
    # for prop accounts).
    prop_lines = ""
    if status.get("account_type") == "prop" and "account_state" in status:
        state = status.get("account_state") or "?"
        cumul = float(status.get("cumulative_pnl_pct") or 0.0)
        target = float(status.get("target_profit_pct") or 0.0)
        days = int(status.get("active_days") or 0)
        min_days = int(status.get("min_active_days") or 0)
        mission = bool(status.get("mission_complete"))
        mission_icon = "🏁" if mission else "🛤️"
        prop_lines = (
            f"  🏷️ Phase: <code>{_h(state)}</code>"
            f" {mission_icon} mission_complete="
            f"{'✅' if mission else '⏳'}\n"
            f"  📈 Mission PnL: {cumul * 100:+.2f}%"
            f" / target {target * 100:.2f}%"
            f" | Active days: {days}/{min_days}\n"
        )

    return (
        f"{halted_icon} <b>{_h(status['name'])}</b> "
        f"(<code>{_h(status.get('exchange', '?'))}</code> / "
        f"{_h(status.get('account_type', '?'))})\n"
        f"{strat_line}"
        f"{fp_line}"
        f"{cfg_line}"
        f"{prop_lines}"
        f"{api_line}\n"
        f"  💵 Daily PnL: ${pnl:+.2f} / limit ${limit:.0f}\n"
        f"  📦 Max pos: ${pos_size:.0f} | Open: {open_pos}"
    )


def render_accounts_status_collapsable(statuses: List[Dict[str, Any]]) -> str:
    """Render the full ``/accounts_status`` page with collapsable
    per-account sections.

    S-telegram-format follow-up: the operator asked for uniform
    formatting where the *summary line* of each account is always
    visible (account_id + halted/healthy + balance) and the deeper
    detail (strategies, key fingerprint, prop fields, daily PnL)
    collapses into an expandable blockquote. This renderer wraps
    ``format_account_status_block`` per-account in that shape.

    Returns an HTML string suitable for ``parse_mode="HTML"``.
    """
    from src.units.ui.telegram_format import Section, render_html

    if not statuses:
        return render_html(
            header="📋 Accounts Status",
            sections=[Section(summary="No accounts configured", body="")],
        )

    sections: List[Section] = []
    healthy = down = 0
    for idx, status in enumerate(statuses):
        if status.get("halted"):
            down += 1
            icon = "🔴"
        else:
            healthy += 1
            icon = "🟢"

        name = status.get("name") or "?"
        bal = status.get("live_balance_usdt")
        bal_err = status.get("live_balance_error")
        if bal_err:
            bal_part = "API error"
        elif bal is not None:
            bal_part = f"${float(bal):,.2f}"
        else:
            bal_part = "no balance"

        # Use the existing per-account renderer for the detailed body —
        # it already returns sanitised HTML, so set body_is_html=True
        # to skip the formatter's escape step (which would render
        # the &lt;b&gt; tags as visible markup).
        body = format_account_status_block(status)

        sections.append(Section(
            summary=f"{icon} {name} — {bal_part}",
            body=body,
            body_is_html=True,
            priority=10 + idx,
        ))

    return render_html(
        header=(
            f"📋 Accounts Status — {len(statuses)} configured"
            f" / {healthy} healthy"
            + (f" / {down} halted" if down else "")
        ),
        sections=sections,
    )


def get_recent_signals(
    limit: int = 10,
    strategy: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the last ``limit`` records from the pipeline audit log.

    Each row is a flat dict with at least:
      ``logged_at_utc``, ``strategy``, ``symbol``, ``side``, ``qty``,
      ``status``, ``reason``.

    UI surfaces that render this MUST display the ``strategy`` field;
    the operator explicitly asked for it on every row.
    """
    import json
    import os
    from collections import deque

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    audit = os.environ.get("SIGNAL_AUDIT_PATH") or os.path.join(
        repo_root, "runtime_logs", "signal_audit.jsonl",
    )
    if not os.path.exists(audit):
        return []
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 10

    wanted = max(limit * 4, 50)
    try:
        with open(audit, "r", encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=wanted)
    except OSError as exc:
        logger.warning("get_recent_signals: read failed: %s", exc)
        return []

    rows: List[Dict[str, Any]] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rec.setdefault("strategy", "unknown")
        rows.append(rec)

    if strategy:
        s = strategy.strip().lower()
        rows = [r for r in rows if str(r.get("strategy", "")).lower() == s]
    return rows[-limit:]


# ---------------------------------------------------------------------------
# Hourly report passthrough
# ---------------------------------------------------------------------------


def get_hourly_report(
    *,
    now_utc: Optional[Any] = None,
    tick_interval_s: int = 900,
) -> str:
    """Return the structured hourly report string, or a clear error.

    Single source of truth so the bot's ``/hourly``, the in-process
    scheduler in ``src/main.py``, and the webapp's "summary" widget all
    render identical text.

    ``now_utc`` and ``tick_interval_s`` forward to
    ``src.runtime.hourly_report.build_hourly_report``. Callers that
    want "now / 15-min ticks" can omit both. The bot's ``/hourly``
    handler passes an explicit ``now_utc`` so the report's window
    matches the operator's invocation time exactly.
    """
    try:
        from src.runtime.hourly_report import build_hourly_report
        kwargs: Dict[str, Any] = {"tick_interval_s": tick_interval_s}
        if now_utc is not None:
            kwargs["now_utc"] = now_utc
        return build_hourly_report(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_hourly_report: build failed")
        return f"⚠️ hourly report unavailable: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Status helpers — S-031 PR1 (architecture-audit-2026-05-02 P1-6)
# ---------------------------------------------------------------------------


def get_today_pnl(account_id: Optional[str] = None) -> Dict[str, Any]:
    """Return today's trade count + total realised PnL.

    Pre-PR (BUG-shaped): ``src/bot/telegram_query_bot.py::fetch_today_pnl``
    opened ``trade_journal.db`` directly. Per CLAUDE.md § Architecture
    rules § 5 the bot is a thin shell — DB access belongs to the UI
    unit. Both the Telegram bot and the webapp call this helper.

    Parameters
    ----------
    account_id : str, optional
        Filter to a single account's rows. None → all accounts.

    Returns
    -------
    dict
        ``{"trade_count": int, "total_pnl_usd": float, "as_of_utc_date": str}``.
        Never raises; on a DB failure returns zero counts with the
        same shape so renderers can show ``"N/A"`` without try/except.
    """
    import sqlite3
    from datetime import datetime, timezone

    from src.utils.paths import trade_journal_db_path

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db_path = trade_journal_db_path()
    try:
        conn = sqlite3.connect(db_path)
        try:
            # Refusal rows (status='rejected' / 'exchange_rejected') must
            # be excluded so trade_count reflects real exchange
            # submissions only. PnL aggregation is also safe because
            # rejected rows have pnl IS NULL, but the filter is explicit
            # for documentation. (CP-2026-05-03-14.)
            status_filter = (
                "AND COALESCE(status, 'open') "
                "NOT IN ('rejected', 'exchange_rejected', 'rejected_too_small', 'orphaned')"
            )
            if account_id is not None:
                row = conn.execute(
                    "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                    "WHERE DATE(timestamp) = ? AND is_backtest = 0 "
                    f"{status_filter} "
                    "AND account_id = ?",
                    (today, account_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                    f"WHERE DATE(timestamp) = ? AND is_backtest = 0 "
                    f"{status_filter}",
                    (today,),
                ).fetchone()
        finally:
            conn.close()
        return {
            "trade_count": int(row[0] or 0) if row else 0,
            "total_pnl_usd": float(row[1] or 0.0) if row else 0.0,
            "as_of_utc_date": today,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_today_pnl: %s", exc)
        return {
            "trade_count": 0,
            "total_pnl_usd": 0.0,
            "as_of_utc_date": today,
        }


def get_open_positions_count(account_id: Optional[str] = None) -> int:
    """Return the count of open, non-backtest trade rows.

    Pre-PR: ``src/bot/telegram_query_bot.py::fetch_open_positions_count``
    opened ``trade_journal.db`` directly. Now goes through the UI
    unit per CLAUDE.md § Architecture rules § 5.

    Returns 0 on any error (logged) so renderers can show a sane value.
    """
    import sqlite3

    from src.utils.paths import trade_journal_db_path

    db_path = trade_journal_db_path()
    try:
        conn = sqlite3.connect(db_path)
        try:
            if account_id is not None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM trades "
                    "WHERE status = 'open' AND is_backtest = 0 "
                    "AND account_id = ?",
                    (account_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM trades "
                    "WHERE status = 'open' AND is_backtest = 0"
                ).fetchone()
        finally:
            conn.close()
        return int(row[0] or 0) if row else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_open_positions_count: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Signals block — S-031 PR2 (architecture-audit-2026-05-02 P1-6)
# ---------------------------------------------------------------------------

# Status → emoji mapping, used by the rendered signals block. Lives in
# the UI unit so the bot, webapp, and any future surface render the
# same thing.
_SIGNAL_STATUS_EMOJI = {
    "submitted": "🟢",
    "dry_run":   "🟡",
    "skipped":   "⚪️",
    "halted":    "🛑",
    "failed_validation": "🔴",
    "failed_exchange":   "❌",
    "refused":   "🚫",
    "blocked":   "🚫",
    "multi_account_dispatched": "🟢",
}


def _format_signal_row(rec: Dict[str, Any]) -> str:
    """Render one signal_audit.jsonl record for a Telegram block.

    Plain text only — pipeline statuses (``no_signal``,
    ``halt_flag_active``, ``failed_validation``) contain underscores
    that break Telegram's legacy Markdown italic parsing, so the
    formatter never wraps with ``_…_`` or ``*…*``.
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


def get_signals_block(
    strategy_filter: Optional[str] = None,
    limit: int = 10,
    *,
    use_html: bool = False,
) -> str:
    """Return a fully-rendered Telegram-ready block for ``/signals``.

    Pre-PR (S-031 PR2) the rendering lived inline in
    ``src/bot/telegram_query_bot.py`` (``_render_signals_block`` +
    ``_format_signal_row`` + ``_SIGNAL_STATUS_EMOJI``). Per
    CLAUDE.md § Architecture rules § 5 the UI unit owns the
    rendering; the bot just sends what comes back.

    Parameters
    ----------
    strategy_filter : str, optional
        Lowercased filter; ``None`` means all strategies.
    limit : int
        How many rows to render. Capped at 200 internally.
    use_html : bool, default False
        When True, render with the unified collapsable HTML formatter:
        signals grouped by status, one expandable section per bucket.
        The summary line of each section names the count
        ("🔴 failed_validation — 12 signals") so the operator sees the
        distribution at a glance and taps the bucket they want to
        inspect. Default False keeps the legacy plain-text format.

    Returns
    -------
    str
        A header + N body lines. Empty-state replaces the body with
        a "no signals" hint and points at the audit-file path so
        the operator can grep manually.
    """
    import os

    rows = get_recent_signals(limit=limit, strategy=strategy_filter)

    if not rows:
        scope = f" for {strategy_filter}" if strategy_filter else ""
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        audit_path = os.environ.get("SIGNAL_AUDIT_PATH") or os.path.join(
            repo_root, "runtime_logs", "signal_audit.jsonl",
        )
        empty_msg = (
            f"📭 No signals logged yet{scope}.\n"
            f"Audit file: {audit_path}"
        )
        if use_html:
            from src.units.ui.telegram_format import Section, render_html
            return render_html(
                header="📡 Recent signals",
                sections=[Section(summary=empty_msg, body="")],
            )
        return empty_msg

    if use_html:
        from src.units.ui.telegram_format import Section, render_html

        # Group rows by status so the collapsable buckets give the
        # operator a one-glance distribution. Within each bucket the
        # newest signals are first (rows already arrive in reverse-
        # chronological order from get_recent_signals).
        by_status: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            st = str(r.get("status", "?"))
            by_status.setdefault(st, []).append(r)

        # Failure-shaped statuses sort first so the operator's eye
        # lands on them before the happy path.
        priority_order = {
            "failed_validation": 5,
            "failed_exchange":   6,
            "failed_dispatch":   7,
            "error":             8,
            "refused":           10,
            "blocked":           11,
            "halted":            12,
            "news_veto":         13,
            "submitted":         20,
            "multi_account_dispatched": 21,
            "dry_run":           30,
            "skipped":           40,
        }
        sections: List[Section] = []
        for st, bucket in by_status.items():
            emoji = _SIGNAL_STATUS_EMOJI.get(st, "•")
            body_lines = [_format_signal_row(r) for r in bucket]
            sections.append(Section(
                summary=f"{emoji} {st} — {len(bucket)} signals",
                body="\n".join(body_lines),
                priority=priority_order.get(st, 50),
            ))

        header = (
            f"📡 Last {len(rows)} signals"
            + (f" — {strategy_filter}" if strategy_filter else "")
        )
        return render_html(header=header, sections=sections)

    header = (
        f"📡 Last {len(rows)} signals"
        + (f" — {strategy_filter}" if strategy_filter else "")
    )
    body = "\n".join(_format_signal_row(r) for r in rows)
    return f"{header}\n{body}"


def render_per_account_collapsable(
    accounts: List[Dict[str, Any]],
    body_fn,
    *,
    header: str,
    summary_fn=None,
    empty_message: str = "(no accounts configured)",
    extra_top_lines: Optional[List[str]] = None,
) -> str:
    """Generic helper: wrap a per-account renderer in a collapsable
    HTML envelope.

    Used by ``/balance``, ``/trades``, and ``/log`` to apply the
    unified collapsable shape uniformly. ``body_fn(account)`` returns
    the per-account body string (legacy formatters that already emit
    Markdown are fine — the formatter HTML-escapes by default so the
    `*` characters render as literal asterisks inside the
    blockquote, which is acceptable since the section's value is the
    summary line + tap-to-expand UX, not in-blockquote rich
    formatting).

    ``summary_fn(account)``, when provided, returns the summary line.
    Default summary uses ``account_id`` plus the first non-empty line
    of the body so the operator sees the headline figure (balance /
    position count / log activity) without expanding.

    ``extra_top_lines`` are appended to the header section verbatim
    (after HTML escape) — useful for dup-key warnings on /balance.
    """
    from src.units.ui.telegram_format import Section, render_html

    if not accounts:
        return render_html(
            header=header,
            sections=[Section(summary=empty_message, body="")],
        )

    sections: List[Section] = []
    if extra_top_lines:
        sections.append(Section(
            summary="ℹ️ Notes",
            body="\n".join(extra_top_lines),
            priority=1,
        ))

    for idx, acc in enumerate(accounts):
        aid = acc.get("account_id") or "?"
        try:
            body = body_fn(acc) or ""
        except Exception as exc:  # noqa: BLE001
            body = f"⚠️ {type(exc).__name__}: {exc}"
        if summary_fn is not None:
            try:
                summary = summary_fn(acc, body) or aid
            except Exception:  # noqa: BLE001
                summary = aid
        else:
            # Default summary: ``{account_id} — {first body line}``
            # so the operator can identify the section even when the
            # body's first line doesn't already name the account.
            # Legacy Markdown decorations (``*x*``, ``` `x` ```) are
            # stripped to literal text — the summary is plain text by
            # design (HTML escape happens below).
            first = next(
                (ln for ln in body.split("\n") if ln.strip()),
                "",
            )
            stripped = first.replace("*", "").replace("`", "").strip()
            if not stripped or stripped == aid:
                summary = aid
            else:
                summary = f"{aid} — {stripped[:100]}"

        sections.append(Section(
            summary=summary, body=body, priority=10 + idx,
        ))

    return render_html(header=header, sections=sections)


def render_recent_trades_collapsable(
    rows: List[Dict[str, Any]],
    *,
    title: str = "📒 Recent trades",
) -> str:
    """Render trade-journal rows as one HTML message with each trade
    in its own collapsable section.

    Pre-S-telegram-format the bot's ``cmd_last5`` sent ONE Telegram
    message per trade (5 trades → 5 messages, plus a chart per row).
    The new shape consolidates everything into a single message: the
    operator sees one summary line per trade ("Trade #42 BTCUSDT long
    +$23.45") and taps to expand the full SL/TP/setup/notes block.

    Returns an HTML string suitable for ``parse_mode="HTML"``. Empty
    input returns a "no trades" message wrapped in the same envelope
    so callers don't need a separate branch.
    """
    from src.units.ui.telegram_format import Section, render_html

    if not rows:
        return render_html(
            header=title,
            sections=[Section(summary="📭 No trades found", body="")],
        )

    sections: List[Section] = []
    for idx, row in enumerate(rows):
        trade_id = row.get("id", "?")
        symbol = row.get("symbol", "?")
        direction = row.get("direction", "?")
        pnl = row.get("pnl")
        try:
            pnl_str = f"{float(pnl):+.2f}" if pnl is not None else "?"
        except (TypeError, ValueError):
            pnl_str = str(pnl)
        status = row.get("status", "?")
        summary = (
            f"Trade #{trade_id} — {symbol} {direction} "
            f"PnL ${pnl_str} ({status})"
        )

        # Body mirrors the legacy /last5 layout but plain-text so the
        # formatter's HTML escape keeps DB-sourced free-text fields
        # safe (notes / entry_reason / exit_reason can contain any
        # character — see BUG-009 / BUG-030 / BUG-031).
        body_lines = [
            f"🕒 {row.get('timestamp', '?')}",
            f"💰 Entry: {row.get('entry_price', '?')} | "
            f"🛑 SL: {row.get('stop_loss', '?')}",
            f"🎯 TP1: {row.get('take_profit_1', '?')} | "
            f"TP2: {row.get('take_profit_2', '?')} | "
            f"TP3: {row.get('take_profit_3', '?')}",
            f"📦 Size: {row.get('position_size', '?')}",
            f"🧠 {row.get('setup_type', '?')} | "
            f"{row.get('bias', '?')} | "
            f"{row.get('killzone', '?')}",
            f"📝 {row.get('entry_reason', '')}",
            f"🚪 {row.get('exit_reason', '')}",
            f"💵 PnL: {pnl_str} ({row.get('pnl_percent', '?')}%)",
            f"📓 {row.get('notes', '')}",
        ]
        if row.get("is_backtest"):
            body_lines.append("🧪 BACKTEST row")

        sections.append(Section(
            summary=summary, body="\n".join(body_lines), priority=10 + idx,
        ))

    return render_html(
        header=f"{title} — {len(rows)} rows", sections=sections,
    )


# ---------------------------------------------------------------------------
# /packages — rejection rows + open-but-undispatched order packages.
# ---------------------------------------------------------------------------


def _strip_reason_prefix(entry_reason: Optional[str]) -> str:
    """Return the bare refusal token (no ``REJECTED:`` / ``EXCHANGE_REJECTED:``
    prefix) from a rejection row's ``entry_reason`` column.

    See ``src/units/accounts/execute.py::_log_trade_to_journal`` for the
    prefix shape. Falls back to the raw value when neither prefix matches.
    """
    if not entry_reason:
        return ""
    s = str(entry_reason)
    for prefix in ("REJECTED: ", "EXCHANGE_REJECTED: "):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


def render_packages_collapsable(
    rejections: List[Dict[str, Any]],
    open_packages: List[Dict[str, Any]],
    *,
    title: str = "📦 Order packages — diagnostics",
) -> str:
    """Render rejection trade-rows + open undispatched packages as one
    HTML message with each row in its own collapsable section.

    Two sub-headers in the body — one for refusals (so the operator sees
    *why* trades didn't land) and one for open packages with no linked
    trade (so the operator sees *what's stuck*). Per the bug-log § BUG-009
    /BUG-030 / BUG-031 pattern: HTML mode (the formatter escapes ``&``,
    ``<``, ``>``); never Markdown — DB-sourced free-text fields routinely
    carry ``*`` / ``_`` / ``[`` / backticks.
    """
    from src.units.ui.telegram_format import Section, render_html

    if not rejections and not open_packages:
        return render_html(
            header=title,
            sections=[Section(
                summary="✅ No refusals + no stuck packages",
                body=("Every actionable signal in the recent window placed "
                      "a trade or had no matching account. Nothing to "
                      "diagnose."),
            )],
        )

    sections: List[Section] = []
    priority = 0

    # --- Refusal rows -----------------------------------------------------
    if rejections:
        sections.append(Section(
            summary=f"🚫 {len(rejections)} recent refusal(s)",
            body=("Each row below is a trade that did NOT reach the "
                  "exchange. The reason token names the gate that "
                  "fired (RiskManager refusal or exchange-side error)."),
            priority=priority,
        ))
        priority += 1
        for row in rejections:
            status = row.get("status", "?")
            reason_token = _strip_reason_prefix(row.get("entry_reason"))
            symbol = row.get("symbol", "?")
            direction = row.get("direction", "?")
            strategy = row.get("strategy_name") or "?"
            account = row.get("account_id") or "?"
            qty = row.get("position_size", "?")
            ts = row.get("created_at") or row.get("timestamp") or "?"
            badge = "🛑" if status == "rejected" else "💥"
            summary = (
                f"{badge} {strategy} → {account}: {reason_token or status}"
            )
            body_lines = [
                f"🕒 {ts}",
                f"💱 {symbol} {direction} qty={qty}",
                f"🏷️  status={status}",
                f"📝 reason={reason_token or '(none)'}",
                f"🛑 SL: {row.get('stop_loss', '?')} | "
                f"🎯 TP: {row.get('take_profit_1', '?')}",
            ]
            sections.append(Section(
                summary=summary, body="\n".join(body_lines),
                priority=priority,
            ))
            priority += 1

    # --- Open undispatched packages --------------------------------------
    if open_packages:
        sections.append(Section(
            summary=f"⏳ {len(open_packages)} open package(s) with no linked trade",
            body=("Packages logged by the strategy that the dispatcher "
                  "couldn't route to a successful trade. Compare to the "
                  "refusals above for the matching reason token."),
            priority=priority,
        ))
        priority += 1
        for pkg in open_packages:
            pkg_id = pkg.get("order_package_id", "?")
            strategy = pkg.get("strategy_name", "?")
            symbol = pkg.get("symbol", "?")
            direction = pkg.get("direction", "?")
            entry = pkg.get("entry", "?")
            sl = pkg.get("sl", "?")
            tp = pkg.get("tp", "?")
            confidence = pkg.get("confidence")
            updated_at = pkg.get("updated_at") or pkg.get("created_at") or "?"
            try:
                conf_str = f"{float(confidence):.2f}" if confidence is not None else "?"
            except (TypeError, ValueError):
                conf_str = str(confidence)
            short_id = str(pkg_id)[-12:] if pkg_id else "?"
            summary = f"📂 {strategy} {symbol} {direction} (#{short_id})"
            body_lines = [
                f"🆔 {pkg_id}",
                f"💱 {symbol} {direction}",
                f"💰 entry: {entry}",
                f"🛑 SL: {sl} | 🎯 TP: {tp}",
                f"📊 confidence: {conf_str}",
                f"🕒 updated_at: {updated_at}",
            ]
            sections.append(Section(
                summary=summary, body="\n".join(body_lines),
                priority=priority,
            ))
            priority += 1

    counts = []
    if rejections:
        counts.append(f"{len(rejections)} refusal(s)")
    if open_packages:
        counts.append(f"{len(open_packages)} open package(s)")
    return render_html(
        header=f"{title} — {' + '.join(counts)}", sections=sections,
    )


# ---------------------------------------------------------------------------
# /latest_backtest <strategy> [N] — backtest history with delta indicators.
# ---------------------------------------------------------------------------


# Metric → (display label, "good" direction). The renderer surfaces a
# 📈 / 📉 indicator on the LATEST run when its value moved in the
# good / bad direction relative to the prior run.
_BACKTEST_DELTA_METRICS = (
    ("win_rate",         "WR",     "up"),
    ("sharpe_ratio",     "Sharpe", "up"),
    ("profit_factor",    "PF",     "up"),
    ("expectancy",       "Exp",    "up"),
    ("max_drawdown_pct", "DD%",    "down"),
    ("total_pnl",        "PnL",    "up"),
)


def _compute_backtest_deltas(
    latest: Dict[str, Any], prior: Dict[str, Any],
) -> Dict[str, Optional[float]]:
    """Return {metric: signed delta} where positive=latest higher, None
    when either side is missing or non-numeric.

    Caller decides which direction is "good" per metric.
    """
    deltas: Dict[str, Optional[float]] = {}
    for key, _label, _dir in _BACKTEST_DELTA_METRICS:
        try:
            cur = latest.get(key)
            pre = prior.get(key)
            if cur is None or pre is None:
                deltas[key] = None
                continue
            deltas[key] = float(cur) - float(pre)
        except (TypeError, ValueError):
            deltas[key] = None
    return deltas


def render_backtest_history_collapsable(
    rows: List[Dict[str, Any]],
    strategy_version: str,
    *,
    title: Optional[str] = None,
) -> str:
    """Render N backtest_results rows as one HTML message.

    Each run gets its own collapsable section with the full metric set.
    On the *latest* row the summary line carries 📈 / 📉 deltas vs the
    prior run for the metrics in ``_BACKTEST_DELTA_METRICS`` so the
    operator immediately sees whether the most-recent run improved or
    regressed against the previous one.

    Empty input renders a friendly message in the same envelope so
    callers don't need a separate branch.
    """
    from src.units.ui.telegram_format import Section, render_html

    base_title = title or f"📊 Backtest history — {strategy_version}"

    if not rows:
        return render_html(
            header=base_title,
            sections=[Section(
                summary="📭 No backtest history",
                body=(f"No rows in backtest_results for "
                      f"strategy_version={strategy_version!r}."),
            )],
        )

    deltas: Dict[str, Optional[float]] = {}
    if len(rows) >= 2:
        deltas = _compute_backtest_deltas(rows[0], rows[1])

    sections: List[Section] = []
    for idx, row in enumerate(rows):
        run_date = row.get("run_date") or row.get("created_at") or "?"
        win_rate = row.get("win_rate")
        sharpe = row.get("sharpe_ratio")
        pnl = row.get("total_pnl")

        try:
            wr_str = f"{float(win_rate)*100:.1f}%" if win_rate is not None else "?"
        except (TypeError, ValueError):
            wr_str = str(win_rate)
        try:
            sh_str = f"{float(sharpe):.2f}" if sharpe is not None else "?"
        except (TypeError, ValueError):
            sh_str = str(sharpe)
        try:
            pnl_str = f"{float(pnl):+.2f}" if pnl is not None else "?"
        except (TypeError, ValueError):
            pnl_str = str(pnl)

        delta_tags = []
        if idx == 0 and deltas:
            for key, label, good_dir in _BACKTEST_DELTA_METRICS:
                d = deltas.get(key)
                if d is None or abs(d) < 1e-9:
                    continue
                improved = (good_dir == "up" and d > 0) or (
                    good_dir == "down" and d < 0
                )
                delta_tags.append(f"{'📈' if improved else '📉'}{label}")

        marker = "🆕 LATEST" if idx == 0 else f"#{idx + 1}"
        delta_suffix = (" — " + " ".join(delta_tags)) if delta_tags else ""
        summary = (
            f"{marker} {run_date} — WR={wr_str} Sharpe={sh_str} "
            f"PnL={pnl_str}{delta_suffix}"
        )

        body_lines = [
            f"🗓 Run: {run_date}",
            f"📅 Period: {row.get('start_date', '?')} → "
            f"{row.get('end_date', '?')}",
            f"🔢 Trades: {row.get('total_trades', '?')} "
            f"({row.get('winning_trades', '?')}W / "
            f"{row.get('losing_trades', '?')}L)",
            f"🎯 Win rate: {wr_str}",
            f"⚖️ Profit factor: {row.get('profit_factor', '?')}",
            f"📈 Expectancy: {row.get('expectancy', '?')}",
            f"📐 Sharpe: {sh_str}",
            f"📉 Max DD: {row.get('max_drawdown', '?')} "
            f"({row.get('max_drawdown_pct', '?')}%)",
            f"💵 Total PnL: {pnl_str} ({row.get('total_pnl_pct', '?')}%)",
            f"🥇 Avg win: {row.get('avg_win', '?')} | "
            f"🥀 Avg loss: {row.get('avg_loss', '?')}",
            f"🚀 Largest win: {row.get('largest_win', '?')} | "
            f"💥 Largest loss: {row.get('largest_loss', '?')}",
            f"🕒 Saved: {row.get('created_at', '?')}",
        ]
        sections.append(Section(
            summary=summary, body="\n".join(body_lines), priority=10 + idx,
        ))

    return render_html(
        header=f"{base_title} — {len(rows)} run(s)", sections=sections,
    )


# ---------------------------------------------------------------------------
# Price helper — S-031 PR3 (architecture-audit-2026-05-02 P1-6)
# ---------------------------------------------------------------------------


def get_price(symbol: str = "BTCUSDT") -> Optional[float]:
    """Return the latest spot/linear last-price for *symbol*, or ``None``.

    Pre-PR (S-031 PR3) ``src/bot/telegram_query_bot.py::cmd_price``
    made a raw HTTP call to Bybit's public ticker endpoint. Per
    CLAUDE.md § Architecture rules § 5 the bot doesn't talk to
    exchanges; the UI unit owns reads of public market data. The
    public-tickers endpoint is keyless so this helper does not need
    a per-account client — the call is identical for every operator.

    Returns
    -------
    float | None
        The last-trade price as a float, or ``None`` on any
        failure (network error, non-200 response, missing fields).
        Renderers should branch on ``None`` and show a sane
        ``"unavailable"`` message rather than try/except.
    """
    try:
        import requests  # local import — keep the module light
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_price: requests import failed: %s", exc)
        return None
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "spot", "symbol": symbol},
            timeout=10,
        )
        payload = resp.json()
        last = payload["result"]["list"][0]["lastPrice"]
        return float(last)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_price(%s): %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Sprint / checkpoint readers — S-031 PR5 (architecture-audit-2026-05-02 P1-6)
# ---------------------------------------------------------------------------

import re as _re  # noqa: E402

_SPRINT_RE = _re.compile(r"\bS-\d{3}(?:\.\d+)?\b")
_CP_HEADER_RE = _re.compile(r"^##\s+(CP-\d{4}-\d{2}-\d{2}-\d+)\b")


def _checkpoint_log_path() -> str:
    import os
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    return os.path.join(repo_root, "docs", "claude", "checkpoints",
                        "CHECKPOINT_LOG.md")


def get_latest_sprint() -> Dict[str, str]:
    """Return ``(sprint_id, cp_id)`` parsed from the topmost CP entry of
    ``docs/claude/checkpoints/CHECKPOINT_LOG.md``.

    Pre-PR (S-031 PR5) ``src/bot/telegram_query_bot.py``'s
    ``_latest_sprint_from_checkpoint_log`` did this work inline. Per
    CLAUDE.md § Architecture rules § 5 the bot is a thin shell — file
    parsing belongs to the UI unit.

    Returns
    -------
    dict
        ``{"sprint_id": str, "cp_id": str}``. Both default to
        ``"unknown"`` when the log is missing or malformed.
    """
    path = _checkpoint_log_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return {"sprint_id": "unknown", "cp_id": "unknown"}
    cp_id = "unknown"
    sprint_id = "unknown"
    in_entry = False
    for line in text.splitlines():
        m = _CP_HEADER_RE.match(line)
        if m and not in_entry:
            cp_id = m.group(1)
            in_entry = True
            continue
        if in_entry and line.startswith("- **Sprint:**"):
            ms = _SPRINT_RE.search(line)
            if ms:
                sprint_id = ms.group(0)
            break
        if in_entry and line.startswith("## "):
            break
    return {"sprint_id": sprint_id, "cp_id": cp_id}


def get_latest_checkpoint_header() -> str:
    """Return the topmost ``## CP-…`` header line from the log.

    Pre-PR ``cmd_checkpoint`` opened ``CHECKPOINT_LOG.md`` and grepped
    for the first ``## CP-`` line. Now goes through the UI unit.

    Returns
    -------
    str
        The header line trimmed of leading/trailing whitespace, or a
        shape-stable error string starting with ``"⚠️"`` on failure.
    """
    path = _checkpoint_log_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("## CP-"):
                    return stripped
    except OSError as exc:
        return f"⚠️ Could not read checkpoint log: {exc}"
    return "No checkpoint found"


# ---------------------------------------------------------------------------
# /health, /vmstats — VM-side observability helpers (S-031 PR5)
# ---------------------------------------------------------------------------

_HEALTH_UNITS = (
    "ict-trader-live",
    "ict-telegram-bot",
    "ict-web-api",
    "ict-git-sync.timer",
)
_HEALTH_FILES: tuple = (
    ("runtime_status.json (last tick)", "runtime_logs/runtime_status.json"),
    ("signal_audit.jsonl (last signal)", "runtime_logs/signal_audit.jsonl"),
    ("trade_journal.db",                 "trade_journal.db"),
)


def _file_age_str(path: str) -> str:
    """Return a freshness string (``42s (12B)`` / ``7m (…)`` / ``3h12m``).

    ``missing`` if the file isn't there. Used by the /health helper.
    """
    import os
    from datetime import datetime, timezone
    if not os.path.exists(path):
        return "missing"
    try:
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
    except OSError as exc:
        return f"stat-err: {exc.__class__.__name__}"
    age = max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
    if age < 60:
        return f"{int(age)}s ({size}B)"
    if age < 3600:
        return f"{int(age / 60)}m ({size}B)"
    if age < 86400:
        h = int(age // 3600)
        m = int((age % 3600) // 60)
        return f"{h}h{m:02d}m ({size}B)"
    return f"{int(age / 86400)}d ({size}B)"


def get_health_summary(
    *,
    get_service_status=None,
    repo_root: Optional[str] = None,
    use_html: bool = False,
) -> str:
    """Return the rendered /health Telegram block.

    Pre-PR (S-031 PR5) ``cmd_health`` did the systemd lookups + file
    age strings inline. Per CLAUDE.md § Architecture rules § 5 the UI
    unit owns rendering; the bot becomes a one-liner.

    S-telegram-format follow-up: when ``use_html=True`` the body is
    rendered through the unified collapsable formatter
    (``src/units/ui/telegram_format.py``). The summary line ("Services
    — N up / M down") is always visible; per-service detail collapses
    into an expandable blockquote. Default ``use_html=False`` preserves
    the legacy Markdown shape so existing callers + their snapshot
    tests stay green until they migrate one at a time.

    Parameters
    ----------
    get_service_status : callable, optional
        Injectable systemd lookup. When ``None`` we resolve
        ``src.bot.telegram_query_bot.get_service_status`` lazily —
        avoids forcing every test to spin up the bot module.
    repo_root : str, optional
        Test override. Defaults to the repo root.
    use_html : bool, default False
        When True, render with the unified collapsable HTML formatter
        for use with ``parse_mode='HTML'``.
    """
    import os
    from datetime import datetime, timezone

    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )

    if get_service_status is None:
        try:
            from src.bot.telegram_query_bot import (
                get_service_status as _gss,
            )
            get_service_status = _gss
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_health_summary: bot import failed: %s", exc)

            def get_service_status(_u: str) -> str:
                return "unknown"

    # Gather service statuses + freshness rows once, regardless of
    # output format.
    service_rows = []
    services_up = services_down = services_unknown = 0
    for unit in _HEALTH_UNITS:
        try:
            status = get_service_status(unit)
        except Exception as exc:  # noqa: BLE001
            status = f"err: {type(exc).__name__}"
        if status == "active":
            services_up += 1
            icon = "🟢"
        elif status == "failed":
            services_down += 1
            icon = "🔴"
        else:
            services_unknown += 1
            icon = "⚪️"
        service_rows.append((icon, unit, status))

    freshness_rows = []
    for label, rel_path in _HEALTH_FILES:
        full = os.path.join(repo_root, rel_path)
        freshness_rows.append((label, _file_age_str(full)))

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if use_html:
        from src.units.ui.telegram_format import Section, render_html
        services_summary = (
            f"Services — {services_up} up"
            + (f" / {services_down} down" if services_down else "")
            + (f" / {services_unknown} unknown" if services_unknown else "")
        )
        services_body = "\n".join(
            f"{icon} {unit} — {status}" for icon, unit, status in service_rows
        )
        freshness_summary = f"Data freshness — {len(freshness_rows)} files"
        freshness_body = "\n".join(
            f"{label}: {age}" for label, age in freshness_rows
        )
        return render_html(
            header="🩺 ICT Trading Bot — health",
            sections=[
                Section(summary=services_summary, body=services_body, priority=10),
                Section(summary=freshness_summary, body=freshness_body, priority=20),
            ],
            footer=f"🕐 {now_str}",
        )

    # Legacy Markdown rendering — unchanged so existing snapshot tests
    # and callers that still pass parse_mode='Markdown' keep working.
    lines = ["🩺 *ICT Trading Bot — health*\n", "*Services*"]
    for icon, unit, status in service_rows:
        lines.append(f"  {icon} `{unit}` — {status}")
    lines.append("\n*Data freshness*")
    for label, age in freshness_rows:
        lines.append(f"  • {label}: `{age}`")
    lines.append(f"\n🕐 {now_str}")
    return "\n".join(lines)


def _read_loadavg() -> str:
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as fh:
            parts = fh.read().split()
        return " ".join(parts[:3]) if len(parts) >= 3 else "unknown"
    except OSError:
        return "unknown"


def _read_uptime_human() -> str:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            secs = float(fh.read().split()[0])
    except (OSError, ValueError):
        return "unknown"
    d, secs = divmod(int(secs), 86400)
    h, secs = divmod(secs, 3600)
    m, _ = divmod(secs, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _read_meminfo_mb() -> tuple:
    """Return (total_mb, available_mb). (0, 0) on read error."""
    total = avail = 0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) // 1024
                if total and avail:
                    break
    except (OSError, ValueError, IndexError):
        return 0, 0
    return total, avail


def _disk_usage_repo(repo_root: str) -> tuple:
    """Return (free_gb, total_gb) for the partition holding the repo."""
    try:
        import shutil
        total, _, free = shutil.disk_usage(repo_root)
        return free // (1024 ** 3), total // (1024 ** 3)
    except OSError:
        return 0, 0


def get_vm_stats() -> str:
    """Return the rendered /vmstats Telegram block.

    Pre-PR (S-031 PR5) ``cmd_vmstats`` opened ``/proc/loadavg``,
    ``/proc/uptime``, ``/proc/meminfo``, and ``shutil.disk_usage``
    inline. Per CLAUDE.md § Architecture rules § 5 the UI unit owns
    those reads; the bot becomes a one-liner.
    """
    import os
    from datetime import datetime, timezone

    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    load = _read_loadavg()
    uptime = _read_uptime_human()
    mem_total, mem_avail = _read_meminfo_mb()
    mem_used_pct = (
        int(100 * (mem_total - mem_avail) / mem_total)
        if mem_total else 0
    )
    disk_free_gb, disk_total_gb = _disk_usage_repo(repo_root)
    cpus = os.cpu_count() or 0
    lines = [
        "🖥️ *VM stats*\n",
        f"⏱️ Uptime: `{uptime}`",
        f"📈 Load (1/5/15 m): `{load}` on `{cpus}` CPU"
        f"{'s' if cpus != 1 else ''}",
        (f"🧠 Memory: `{mem_total - mem_avail}` / `{mem_total}` MB "
         f"used (`{mem_used_pct}%`)" if mem_total else "🧠 Memory: unknown"),
        (f"💾 Disk (repo partition): `{disk_free_gb}` / `{disk_total_gb}` "
         "GB free" if disk_total_gb else "💾 Disk: unknown"),
        "\n🕐 "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Roadmap — S-031 PR5 (claude_bridge handler P1-6)
# ---------------------------------------------------------------------------


def get_roadmap_summary() -> str:
    """Return the rendered roadmap-status block, or a clear error.

    Pre-PR (S-031 PR5) ``src/bot/claude_bridge.py::cmd_roadmap`` opened
    ``ROADMAP.md`` and called ``recurring_dispatch.render_roadmap_summary``
    inline. Per CLAUDE.md § Architecture rules § 5 the bot doesn't
    open files — UI unit reads + renders.
    """
    import os
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    roadmap_path = os.path.join(repo_root, "ROADMAP.md")
    try:
        with open(roadmap_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return "⚠️ Could not read ROADMAP.md from the repo."
    except OSError as exc:
        return f"⚠️ Could not read ROADMAP.md: {exc}"
    try:
        from src.bot.recurring_dispatch import render_roadmap_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_roadmap_summary: render import failed: %s", exc)
        return "⚠️ Could not render roadmap summary."
    return render_roadmap_summary(text)


# ---------------------------------------------------------------------------
# Close open positions — S-031 PR4 (architecture-audit-2026-05-02 P1-6)
# ---------------------------------------------------------------------------


def close_open_positions(
    strategy: Optional[str] = None,
    account: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Close every open trade matching the filter via the canonical close path.

    Pre-PR (S-031 PR4) ``cmd_closeall`` called
    ``dl.close_all_bybit_positions_for_strategy`` which queried Bybit
    for live positions and placed reduce-only market orders directly,
    bypassing ``execute_pkg``'s single-entry contract for live-order
    placement. Per CLAUDE.md § Architecture rules § 3 every exchange
    placement (including closes) MUST route through the same canonical
    helper — ``src.units.accounts.execute.close_open_position`` is the
    one added in S-030 PR4 for the monitor loop's close path.

    This helper:
      1. Reads open trade rows from the trade log
         (``trade_journal.db::trades``) filtered by ``strategy`` /
         ``account`` (case-insensitive on strategy; exact match on
         account_id).
      2. For each row, resolves the per-account exchange client via
         ``bybit_client_for(account_cfg)`` and dispatches to
         ``close_open_position``.
      3. On a successful close, marks the trade row
         ``status='closed'`` with ``exit_reason='manual_closeall'``
         so the next /signals + /last5 + hourly-report tick reflects
         the new state.
      4. Returns a per-trade result dict so the UI surface
         (Telegram, webapp) can render the outcome without doing any
         business logic of its own.

    Parameters
    ----------
    strategy : str, optional
        Filter to a single strategy_name (case-insensitive). ``None``
        means all strategies.
    account : str, optional
        Filter to a single account_id (exact match). ``None`` means
        all accounts.

    Returns
    -------
    list[dict]
        One entry per open trade in the filter scope, in the order
        they were processed. Shape:
        ``{"trade_id": int, "account_id": str, "strategy": str,
        "symbol": str, "direction": str, "qty": float,
        "ok": bool, "error": str | None,
        "exchange_order_id": str | None}``.
        Empty list when no rows match.

    Notes
    -----
    The helper never raises. Per-trade failures (missing creds,
    exchange refusal, DB write error) are encoded as ``ok=False``
    rows with an ``error`` string so the bot can render them.
    """
    import sqlite3

    from src.utils.paths import trade_journal_db_path

    db_path = trade_journal_db_path()
    where = ["status = 'open'", "is_backtest = 0"]
    params: list = []
    if strategy is not None:
        where.append("LOWER(strategy_name) = ?")
        params.append(str(strategy).lower())
    if account is not None:
        where.append("account_id = ?")
        params.append(str(account))
    sql = (
        "SELECT id, symbol, direction, position_size, strategy_name, "
        "account_id FROM trades WHERE " + " AND ".join(where)
    )

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("close_open_positions: DB read failed: %s", exc)
        return []

    if not rows:
        return []

    # Resolve per-account configs once (used for client + filter check).
    accounts_by_id: Dict[str, Dict[str, Any]] = {}
    try:
        from src.units.ui import data_loaders as _dl
        for acc in (_dl.list_accounts() or []):
            aid = acc.get("account_id")
            if aid:
                accounts_by_id[str(aid)] = acc
    except Exception as exc:  # noqa: BLE001
        logger.warning("close_open_positions: list_accounts failed: %s", exc)

    from src.units.accounts.clients import bybit_client_for
    from src.units.accounts.execute import close_open_position
    from datetime import datetime, timezone

    results: List[Dict[str, Any]] = []
    for row in rows:
        trade_id = int(row["id"])
        aid = str(row["account_id"])
        symbol = str(row["symbol"])
        direction = str(row["direction"] or "").lower()
        qty = float(row["position_size"] or 0.0)
        strat = str(row["strategy_name"] or "")

        account_cfg = accounts_by_id.get(aid)
        if account_cfg is None:
            results.append({
                "trade_id": trade_id, "account_id": aid, "strategy": strat,
                "symbol": symbol, "direction": direction, "qty": qty,
                "ok": False, "error": "account not found in config",
                "exchange_order_id": None,
            })
            continue

        exchange = (account_cfg.get("exchange") or "").lower()
        if exchange != "bybit":
            results.append({
                "trade_id": trade_id, "account_id": aid, "strategy": strat,
                "symbol": symbol, "direction": direction, "qty": qty,
                "ok": False,
                "error": f"unsupported exchange {exchange!r} (bybit only in v1)",
                "exchange_order_id": None,
            })
            continue

        client = bybit_client_for(account_cfg)
        if client is None:
            results.append({
                "trade_id": trade_id, "account_id": aid, "strategy": strat,
                "symbol": symbol, "direction": direction, "qty": qty,
                "ok": False, "error": "no exchange_client (missing creds?)",
                "exchange_order_id": None,
            })
            continue

        outcome = close_open_position(
            client, account_cfg,
            symbol=symbol, side=direction, qty=qty,
        )
        ok = bool(outcome.get("ok"))
        order_id = outcome.get("exchange_order_id")
        err = outcome.get("error")

        if ok:
            try:
                conn = sqlite3.connect(db_path)
                try:
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "UPDATE trades SET status = 'closed', "
                        "exit_reason = 'manual_closeall', notes = ? "
                        "WHERE id = ?",
                        (f"closed_at={now}", trade_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "close_open_positions: trade %s close-row update failed: %s",
                    trade_id, exc,
                )

        results.append({
            "trade_id": trade_id, "account_id": aid, "strategy": strat,
            "symbol": symbol, "direction": direction, "qty": qty,
            "ok": ok, "error": err if not ok else None,
            "exchange_order_id": order_id,
        })

    return results
