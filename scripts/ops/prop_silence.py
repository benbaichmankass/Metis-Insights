# wiring: imported by scripts/ops/attention_watch.py::build, which runs inside the
# hourly deploy/ict-work-digest.timer carrier (no unit of its own, by the
# operator's 2026-10-04 one-carrier decision). Guard: scripts/ci/run_guards.py
# ::attention-watch (--self-test). Tests: tests/test_prop_silence.py.
"""Prop silence probes — does someone get pinged when a prop account stops
trading, or when breakout_2's phone executor dies?

Pipeline records PI-20261005-XJSGZG1X-0001 (operator priority 1, 2026-10-05:
live inactivity alert) and PI-20261005-YUVCGTMJ-0001 (phone-dead alert).

TWO PROBE FAMILIES, both returning the attention watch's ``ok`` / ``breached`` /
``unknown`` triple so the existing edge class (breach -> one ping, re-alert
every REALERT_HOURS, one clear line, ``unknown`` never clears) carries them:

* ``prop_idle_<account>`` — days since the account's last FILL on record
  (``GET /api/bot/prop/fills``). ``warn`` at 14 d, ``urgent`` at 21 d. Tradeify
  breaches an account after 30 consecutive days with no trade and Velotrade
  needs a trade every 30 calendar days; both limits are UNCONFIRMED desk
  research (ruleset comments), which is why the alarm fires at 14/21 and not
  at 30. Backtest basis: the ETH leg alone had a max entry gap of 7.5 d over
  730 d, the ETH+SOL book 6.6 d.
* ``phone_hb_breakout_2`` — the phone executor's heartbeat
  (``GET /api/bot/prop/status?account_id=breakout_2`` ``.phone_heartbeat.at``,
  posted every ~30 s). Older than 10 min while the account is ``mode: live``
  -> urgent. The carrier is HOURLY, so detection latency is up to ~1 h on top
  of the 10 min threshold; a faster path needs its own timer (not built).

NULL IS NOT ZERO. A read that could not be made (API down, row unreadable, a
timestamp that will not parse) is ``unknown`` with ``days = None``; it is never
rendered as 0 days and never clears a standing alarm. An account with NO fill
on record is also ``unknown`` — the clock has no anchor, and it is not "0 days
idle" — and says so. Fills on this bridge are OPERATOR-REPORTED, so "no fill
on record" can also mean "traded but not reported back"; the detail says
"on record" for that reason.

Read-only. Reads go to the local API (same source the SPA reads) rather than
the journal directly: the digest unit carries no data-dir drop-in, so a
direct DB read could silently look at the wrong path.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]

OK, BREACHED, UNKNOWN = "ok", "breached", "unknown"

PROP_ACCOUNTS = ("tradeify_1", "velotrade_1", "breakout_2")
PHONE_ACCOUNT = "breakout_2"
IDLE_WARN_DAYS = 14
IDLE_URGENT_DAYS = 21
HEARTBEAT_MAX_MIN = 10
FILLS_LIMIT = 50
API_BASE = os.environ.get("PROP_SILENCE_API_BASE", "http://127.0.0.1:8001")

Fetch = Callable[[str], "tuple[str, Any]"]


def idle_key(account: str) -> str:
    return f"prop_idle_{account}"


def heartbeat_key(account: str = PHONE_ACCOUNT) -> str:
    return f"phone_hb_{account}"


LABELS: dict[str, str] = {
    **{idle_key(a): f"{a} has no recent fill" for a in PROP_ACCOUNTS},
    heartbeat_key(): f"{PHONE_ACCOUNT} phone heartbeat missing",
}


def api_get(path: str) -> tuple[str, Any]:
    """``("read", json)`` or ``("unreadable", reason)``. Never raises."""
    try:
        with urllib.request.urlopen(API_BASE + path, timeout=10) as r:  # noqa: S310
            return "read", json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "unreadable", f"{type(exc).__name__}: {exc}"


def parse_ts(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        t = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


#: Statuses that mean a position really traded. ``skipped`` (ticket expired
#: unsubmitted), ``placed`` (resting, unfilled), ``refused``, ``dry_filled`` and
#: ``mismatch_flattened`` are NOT activity. Measured 2026-10-07 on tradeify_1: a
#: ``skipped`` row from 2026-10-05 would otherwise have reset the idle clock.
TRADED_STATUSES = ("open", "closed", "filled")


def last_fill_time(fills: list[dict]) -> datetime | None:
    """Newest TRADE instant among real fills: max of opened_at / closed_at, and
    created_at only for a row carrying neither (``reported_at`` is rewritten on
    every re-report, so it is not a trade time). Rows whose status is not in
    ``TRADED_STATUSES`` are ignored."""
    best = None
    for f in fills:
        if not isinstance(f, dict) or str(f.get("status") or "closed") not in TRADED_STATUSES:
            continue
        ts = [parse_ts(f.get("opened_at")), parse_ts(f.get("closed_at"))]
        ts = [t for t in ts if t]
        if not ts:
            c = parse_ts(f.get("created_at"))
            ts = [c] if c else []
        for t in ts:
            if best is None or t > best:
                best = t
    return best


def _probe(status: str, detail: str, *, level: str | None = None,
           priority: str = "high", days: float | None = None) -> dict:
    return {"status": status, "detail": detail, "level": level,
            "priority": priority, "days": days}


def probe_prop_idle(account: str, now: datetime, fetch: Fetch = api_get) -> dict:
    st, data = fetch(f"/api/bot/prop/fills?account_id={account}&limit={FILLS_LIMIT}")
    if st != "read" or not isinstance(data, dict):
        return _probe(UNKNOWN, f"{account}: could not read fills ({data})")
    if not data.get("present"):
        return _probe(UNKNOWN, f"{account}: prop fills table not present / unreadable")
    fills = data.get("fills") or []
    last = last_fill_time(fills)
    if last is None:
        return _probe(UNKNOWN, f"{account}: no traded fill on record ({len(fills)} rows read, "
                               f"none open/closed/filled) — idle clock has no anchor")
    days = max(0.0, (now - last).total_seconds() / 86400)
    msg = f"{account}: last fill {last.date()} — {days:.1f} d ago"
    if days >= IDLE_URGENT_DAYS:
        return _probe(BREACHED, f"{msg} (urgent >= {IDLE_URGENT_DAYS} d)",
                      level="urgent", priority="urgent", days=days)
    if days >= IDLE_WARN_DAYS:
        return _probe(BREACHED, f"{msg} (warn >= {IDLE_WARN_DAYS} d)",
                      level="warn", priority="high", days=days)
    return _probe(OK, msg, days=days)


def account_mode(account: str) -> str | None:
    """``config/accounts.yaml::<account>.mode`` or None when it cannot be read."""
    try:
        from src.config.accounts_loader import load_accounts_dict  # noqa: PLC0415
        mode = load_accounts_dict()[account].get("mode")
        return str(mode) if mode else None
    except Exception:  # noqa: BLE001 — unreadable (empty dict / KeyError) == unknown
        return None


def probe_phone_heartbeat(now: datetime, fetch: Fetch = api_get,
                          mode_reader: Callable[[str], str | None] = account_mode) -> dict:
    mode = mode_reader(PHONE_ACCOUNT)
    if mode is None:
        return _probe(UNKNOWN, f"{PHONE_ACCOUNT}: mode unreadable — cannot tell if the "
                               f"heartbeat is expected")
    if mode != "live":
        return _probe(OK, f"{PHONE_ACCOUNT}: mode {mode} — heartbeat not required")
    st, data = fetch(f"/api/bot/prop/status?account_id={PHONE_ACCOUNT}")
    if st != "read" or not isinstance(data, dict) or "phone_heartbeat" not in data:
        # a missing key is the route's own error envelope — we did not look
        return _probe(UNKNOWN, f"{PHONE_ACCOUNT}: could not read prop status ({data})")
    hb = data["phone_heartbeat"]
    if hb is None:
        return _probe(BREACHED, f"{PHONE_ACCOUNT} is live and NO phone heartbeat was ever posted",
                      level="urgent", priority="urgent")
    at = parse_ts(hb.get("at")) if isinstance(hb, dict) else None
    if at is None:
        return _probe(UNKNOWN, f"{PHONE_ACCOUNT}: heartbeat present but its 'at' is unreadable")
    age_min = max(0.0, (now - at).total_seconds() / 60)
    msg = f"{PHONE_ACCOUNT}: last phone heartbeat {age_min:.0f} min ago"
    if age_min > HEARTBEAT_MAX_MIN:
        return _probe(BREACHED, f"{msg} (urgent > {HEARTBEAT_MAX_MIN} min while live)",
                      level="urgent", priority="urgent")
    return _probe(OK, msg)


def all_probes(now: datetime, fetch: Fetch = api_get) -> dict[str, dict]:
    out = {idle_key(a): probe_prop_idle(a, now, fetch) for a in PROP_ACCOUNTS}
    out[heartbeat_key()] = probe_phone_heartbeat(now, fetch)
    return out
