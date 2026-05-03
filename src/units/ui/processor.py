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

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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
    import os
    import sqlite3
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db_path = os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"
    try:
        conn = sqlite3.connect(db_path)
        try:
            if account_id is not None:
                row = conn.execute(
                    "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                    "WHERE DATE(timestamp) = ? AND is_backtest = 0 "
                    "AND account_id = ?",
                    (today, account_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                    "WHERE DATE(timestamp) = ? AND is_backtest = 0",
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
    import os
    import sqlite3

    db_path = os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"
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
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        audit_path = os.environ.get("SIGNAL_AUDIT_PATH") or os.path.join(
            repo_root, "runtime_logs", "signal_audit.jsonl",
        )
        return (
            f"📭 No signals logged yet{scope}.\n"
            f"Audit file: {audit_path}"
        )

    header = (
        f"📡 Last {len(rows)} signals"
        + (f" — {strategy_filter}" if strategy_filter else "")
    )
    body = "\n".join(_format_signal_row(r) for r in rows)
    return f"{header}\n{body}"


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
            params={"category": "linear", "symbol": symbol},
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

import re as _re

_SPRINT_RE = _re.compile(r"\bS-\d{3}(?:\.\d+)?\b")
_CP_HEADER_RE = _re.compile(r"^##\s+(CP-\d{4}-\d{2}-\d{2}-\d+)\b")


def _checkpoint_log_path() -> str:
    import os
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
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
    *, get_service_status=None, repo_root: Optional[str] = None,
) -> str:
    """Return the rendered /health Telegram block.

    Pre-PR (S-031 PR5) ``cmd_health`` did the systemd lookups + file
    age strings inline. Per CLAUDE.md § Architecture rules § 5 the UI
    unit owns rendering; the bot becomes a one-liner.

    Parameters
    ----------
    get_service_status : callable, optional
        Injectable systemd lookup. When ``None`` we resolve
        ``src.bot.telegram_query_bot.get_service_status`` lazily —
        avoids forcing every test to spin up the bot module.
    repo_root : str, optional
        Test override. Defaults to the repo root.
    """
    import os
    from datetime import datetime, timezone

    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )

    if get_service_status is None:
        try:
            from src.bot.telegram_query_bot import (
                get_service_status as _gss,
            )
            get_service_status = _gss
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_health_summary: bot import failed: %s", exc)
            get_service_status = lambda _u: "unknown"

    lines = ["🩺 *ICT Trading Bot — health*\n", "*Services*"]
    for unit in _HEALTH_UNITS:
        try:
            status = get_service_status(unit)
        except Exception as exc:  # noqa: BLE001
            status = f"err: {type(exc).__name__}"
        icon = (
            "🟢" if status == "active"
            else "🔴" if status == "failed"
            else "⚪️"
        )
        lines.append(f"  {icon} `{unit}` — {status}")
    lines.append("\n*Data freshness*")
    for label, rel_path in _HEALTH_FILES:
        full = os.path.join(repo_root, rel_path)
        lines.append(f"  • {label}: `{_file_age_str(full)}`")
    lines.append(
        "\n🕐 "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    )
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
        os.path.join(os.path.dirname(__file__), "..", "..")
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
        os.path.join(os.path.dirname(__file__), "..", "..")
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
    import os
    import sqlite3

    db_path = os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"
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
