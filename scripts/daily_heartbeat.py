#!/usr/bin/env python3
"""
Daily operational heartbeat for the ICT trading bot.

Posts a single Telegram message to the operator every day (triggered by
ict-heartbeat.timer) summarising:
  - Kill-switch state
  - Open position count (DB-only — no exchange call)
  - Today's realised PnL
  - News layer status
  - Last pipeline tick time

Requires only stdlib + requests (already a bot dependency).
Does NOT import any src.* module — intentionally self-contained so it runs
even if the main bot virtualenv is broken.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Env loading (python-dotenv if available, else manual parse).
#
# The canonical live env file is `.env` (what the trader + ict-heartbeat.service
# read). `.env.live` is a legacy filename kept only as a back-compat fallback —
# on the Ampere live VM only `.env` exists, so preferring it is what makes this
# digest work post-cutover (BL-20260615-HEARTBEAT-ENV). Note: when launched by
# ict-heartbeat.service the systemd EnvironmentFile already injects `.env` into
# the process environment, so this is belaboured belt-and-suspenders for manual
# invocations.
# ---------------------------------------------------------------------------

_ENV_CANDIDATES = (".env", ".env.live")


def _env_files() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return [root / name for name in _ENV_CANDIDATES if (root / name).exists()]


def _load_env() -> None:
    paths = _env_files()
    if not paths:
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        for env_path in paths:
            load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass

    # Fallback: parse manually (KEY=VALUE lines, skip comments). First file
    # wins per key (setdefault), matching .env-preferred precedence above.
    for env_path in paths:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)


# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------

HALT_FLAG = "/tmp/trader_halt.flag"


def _kill_switch_state() -> str:
    return "\U0001f534 HALTED" if Path(HALT_FLAG).exists() else "\U0001f7e2 RUNNING"


# ---------------------------------------------------------------------------
# DB queries (stdlib sqlite3 only)
# ---------------------------------------------------------------------------

def _db_path() -> str:
    # Self-contained resolver (stdlib only — this digest must run even when the
    # venv/src is wedged, so it deliberately cannot import src.utils.paths).
    # Mirrors the canonical chain trade_journal_db_path() uses: explicit env
    # first, then $DATA_DIR/trade_journal.db (live VM: /data/bot-data), then the
    # repo-root canonical file. NOT the old bare "data/trades.db" (wrong name +
    # CWD-relative) which silently read off the live path → "DB unavailable"
    # (S-AUDIT-H H-3). Allowlisted in check_canonical_db_resolver.py for the
    # self-contained-stdlib reason (same carve-out as risk_counters.py).
    env = os.environ.get("TRADE_JOURNAL_DB")
    if env:
        return env
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return str(Path(data_dir) / "trade_journal.db")
    return str(Path(__file__).resolve().parents[1] / "trade_journal.db")


def _open_positions(db: str) -> str:
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM trades WHERE is_backtest = 0 AND status = 'open'"
        )
        row = cur.fetchone()
        conn.close()
        return str(int(row[0])) if row else "0"
    except Exception:
        return "DB unavailable"


def _today_pnl(db: str) -> str:
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades "
            "WHERE is_backtest = 0 AND status = 'closed' "
            "AND DATE(timestamp) = DATE('now')"
        )
        row = cur.fetchone()
        conn.close()
        val = float(row[0] or 0.0)
        sign = "+" if val >= 0 else ""
        return f"${sign}{val:.2f}"
    except Exception:
        return "DB unavailable"


def _last_tick(db: str) -> str:
    """Return the most recent signal timestamp and how long ago it was."""
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        # Try signals table first; fall back to trades table
        for table, col in (("signals", "timestamp"), ("trades", "timestamp")):
            try:
                cur.execute(f"SELECT MAX({col}) FROM {table}")  # noqa: S608
                row = cur.fetchone()
                if row and row[0]:
                    conn.close()
                    ts_str = str(row[0])
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        delta_min = int((now - ts).total_seconds() // 60)
                        h, m = divmod(delta_min, 60)
                        ago = f"{h:02d}:{m:02d} ago"
                        return f"{ts_str[:19]}  ({ago})"
                    except Exception:
                        return ts_str[:19]
            except sqlite3.OperationalError:
                continue
        conn.close()
        return "no data"
    except Exception:
        return "DB unavailable"


# ---------------------------------------------------------------------------
# News layer status
# ---------------------------------------------------------------------------

def _news_status() -> str:
    enabled = os.environ.get("NEWS_ENABLED", "false").strip().lower()
    api_key = os.environ.get("NEWS_API_KEY", "").strip()
    if enabled != "true":
        return "disabled"
    if not api_key:
        return "enabled-no-key"
    return "enabled-active"


# ---------------------------------------------------------------------------
# Telegram POST
# ---------------------------------------------------------------------------

def _send_telegram(token: str, chat_id: str, text: str) -> None:
    import urllib.request
    import json

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_message(
    kill_switch: str,
    open_positions: str,
    today_pnl: str,
    news_status: str,
    last_tick: str,
) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"\U0001f4ca Daily heartbeat — {date_str}\n"
        f"\U0001f6a6 Kill-switch: {kill_switch}\n"
        f"\U0001f4c2 Open positions: {open_positions}\n"
        f"\U0001f4b0 Today's PnL: {today_pnl}\n"
        f"\U0001f4f0 News layer: {news_status}\n"
        f"\U0001f550 Last tick: {last_tick}"
    )


def main() -> int:
    _load_env()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "365546917").strip()

    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set — heartbeat not sent.", file=sys.stderr)
        return 1

    db = _db_path()
    msg = build_message(
        kill_switch=_kill_switch_state(),
        open_positions=_open_positions(db),
        today_pnl=_today_pnl(db),
        news_status=_news_status(),
        last_tick=_last_tick(db),
    )

    try:
        _send_telegram(token, chat_id, msg)
        print("Heartbeat sent.")
    except Exception as exc:
        print(f"ERROR: Telegram POST failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
