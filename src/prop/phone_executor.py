"""Server half of the Breakout PHONE executor (PHONE-EXEC-1B, 2026-10-05).

Design: ``docs/integrations/breakout-phone-executor-DESIGN.md`` § 3. The VM
cannot reach Breakout's terminal (Cloudflare blocks its egress, § 7.9), so an
app on the operator's own phone drives the terminal and talks to this module
over the existing HTTPS API (``/api/bot/prop/phone/*``).

What this module owns, and why each piece is here and not on the phone:

* **Per-device auth (§ 3.2).** The phone mints a 256-bit random token on first
  run and keeps it in Android Keystore-backed storage. It shows only the
  token's SHA-256 *fingerprint*, which is not a secret (a 256-bit pre-image is
  not recoverable from it), so it can travel through chat and be committed.
  ``config/prop_phone_devices.yaml`` pins each fingerprint to ONE account.
  Revoking is a one-line, git-visible edit (``revoked: true``). No shared
  secret is ever put on the phone, and no secret is in git or chat.
* **Atomic claim (§ 3.3).** ``emitted -> claimed`` in one conditional UPDATE,
  so two devices or a retry can never both act on a ticket. A claim is the only
  way the phone gets a ticket. One attempt per ticket: a claimed ticket is
  never re-offered; a claim with no report inside ``CLAIM_TIMEOUT_S`` is
  marked ``skipped`` and alerted (the watchdog runs on every phone call).
* **The submit decision.** ``submit`` is ``live`` only when the account's
  ``config/accounts.yaml`` mode is ``live`` AND the kill switch
  ``PROP_PHONE_MODE_<ACCOUNT>`` is not ``off``/``dry`` AND the ticket is not a
  test ticket. Everything else is ``dry``: the phone fills the form, reads it
  back, and does NOT submit. The phone has its own "armed" switch on top
  (default off), so a live submit needs both sides.
* **Report** wraps :func:`src.prop.prop_report.ingest_report`, overwriting
  ``account_id`` from the token so a device can never write another account.
* **Events** (login ok / failed, logout seen, refusal, mismatch) ping the
  operator on Telegram. Event text is a fixed vocabulary plus a short reason;
  the phone never sends a link, token, email body or account number.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from src.prop import prop_journal
from src.utils.json_notes import dump_capped

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEVICES_PATH = _REPO_ROOT / "config" / "prop_phone_devices.yaml"
ACCOUNTS_PATH = _REPO_ROOT / "config" / "accounts.yaml"
PLATFORMS_PATH = _REPO_ROOT / "config" / "prop_platforms.yaml"

PHONE_PLATFORM = "breakout_phone"
CLAIM_TIMEOUT_S = 180  # design § 3.3: "suggest 3 minutes without a report"
_FP_RE = re.compile(r"^[0-9a-f]{64}$")
_EVENT_KINDS = {
    "login_ok", "login_failed", "logout_seen", "login_started", "refusal",
    "mismatch", "flattened", "dry_fill_ok", "submitted", "app_started", "error",
}


class PhoneAuthError(Exception):
    """401: unknown, malformed or revoked device token."""


class PhoneDevice:
    __slots__ = ("device_id", "account_id")

    def __init__(self, device_id: str, account_id: str) -> None:
        self.device_id = device_id
        self.account_id = account_id


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_devices(path: Optional[Path] = None) -> list:
    p = Path(path) if path else DEVICES_PATH
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    out = []
    for d in data.get("devices") or []:
        if not isinstance(d, dict):
            continue
        fp = str(d.get("token_sha256") or "").strip().lower()
        if not _FP_RE.match(fp):
            continue
        out.append({**d, "token_sha256": fp})
    return out


def authenticate(authorization: Optional[str], path: Optional[Path] = None) -> PhoneDevice:
    """Resolve a ``Bearer <token>`` to its pinned device, or raise."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PhoneAuthError("bearer required")
    token = authorization[7:].strip()
    if len(token) < 40:
        raise PhoneAuthError("bad token")
    fp = fingerprint(token)
    for d in load_devices(path):
        if hmac.compare_digest(fp, d["token_sha256"]):
            if d.get("revoked"):
                raise PhoneAuthError("revoked")
            acct = str(d.get("account_id") or "").strip()
            if not acct:
                raise PhoneAuthError("device has no account")
            return PhoneDevice(str(d.get("device_id") or fp[:12]), acct)
    raise PhoneAuthError("unknown device")


def _account_block(account_id: str, path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else ACCOUNTS_PATH
    data = yaml.safe_load(p.read_text()) or {}
    return ((data.get("accounts") or data).get(account_id)) or {}


def is_phone_account(account_id: str, path: Optional[Path] = None) -> bool:
    p = Path(path) if path else PLATFORMS_PATH
    data = yaml.safe_load(p.read_text()) or {}
    entry = (data.get("phone_accounts") or {}).get(account_id) or {}
    return str(entry.get("platform") or "") == PHONE_PLATFORM


def phone_config(account_id: str, path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else PLATFORMS_PATH
    data = yaml.safe_load(p.read_text()) or {}
    return dict((data.get("phone_accounts") or {}).get(account_id) or {})


def kill_switch(account_id: str, env: Optional[Dict[str, str]] = None) -> str:
    """``PROP_PHONE_MODE_<ACCOUNT>``: off | dry | live (default live = defer to
    accounts.yaml). Anything unparseable is ``dry`` (fail closed)."""
    e = os.environ if env is None else env
    raw = str(e.get(f"PROP_PHONE_MODE_{account_id.upper()}", "live")).strip().lower()
    return raw if raw in ("off", "dry", "live") else "dry"


def submit_mode(account_id: str, *, test: bool, env: Optional[Dict[str, str]] = None,
                accounts_path: Optional[Path] = None) -> str:
    if test:
        return "dry"
    if kill_switch(account_id, env) != "live":
        return "dry"
    mode = str(_account_block(account_id, accounts_path).get("mode") or "").strip()
    return "live" if mode == "live" else "dry"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _meta(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        m = json.loads(row["meta"] or "{}")
        return m if isinstance(m, dict) else {}
    except (TypeError, ValueError):
        return {}


def expire_stale_claims(account_id: str, now: Optional[datetime] = None) -> list:
    """Watchdog (§ 3.3): a claim with no report within CLAIM_TIMEOUT_S becomes
    ``skipped`` (never re-emitted) and is returned so the caller alerts."""
    now = now or _now()
    conn = prop_journal._connect()
    out = []
    try:
        prop_journal.ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM prop_tickets WHERE account_id = ? AND status = 'claimed'",
            (account_id,)).fetchall()
        for r in rows:
            m = _meta(r)
            claimed = _parse((m.get("phone") or {}).get("claimed_at"))
            if claimed and (now - claimed).total_seconds() > CLAIM_TIMEOUT_S:
                m.setdefault("phone", {})["skipped_reason"] = "claimed_no_report"
                cur = conn.execute(
                    "UPDATE prop_tickets SET status = 'skipped', meta = ? "
                    "WHERE ticket_id = ? AND status = 'claimed'",
                    (json.dumps(m), r["ticket_id"]))
                if cur.rowcount:
                    out.append(r["ticket_id"])
        conn.commit()
    finally:
        conn.close()
    return out


def claim_next(device: PhoneDevice, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Atomically claim the oldest still-valid emitted ticket for the device's
    account. ``None`` when there is nothing to do."""
    now = now or _now()
    acct = device.account_id
    if kill_switch(acct) == "off":
        return None
    serve_dry_test_request(device)
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM prop_tickets WHERE account_id = ? AND status = 'emitted' "
            "ORDER BY created_at ASC LIMIT 20", (acct,)).fetchall()
        for r in rows:
            vu = _parse(r["valid_until"])
            if vu is None or vu <= now:
                continue
            m = _meta(r)
            m["phone"] = {"device_id": device.device_id, "claimed_at": now.isoformat()}
            cur = conn.execute(
                "UPDATE prop_tickets SET status = 'claimed', meta = ? "
                "WHERE ticket_id = ? AND account_id = ? AND status = 'emitted'",
                (json.dumps(m), r["ticket_id"], acct))
            conn.commit()
            if cur.rowcount != 1:
                continue  # lost the race to another claimer: never act on it
            t = prop_journal._ticket_row(r)
            test = bool(m.get("test"))
            t["status"] = "claimed"
            t["meta"] = m
            t["submit"] = submit_mode(acct, test=test)
            t["venue_symbol"] = venue_symbol(acct, t.get("symbol"))
            t["claimed_at"] = now.isoformat()
            return t
        return None
    finally:
        conn.close()


def venue_symbol(account_id: str, symbol: Any) -> Optional[str]:
    inst = (phone_config(account_id).get("instruments") or {}).get(str(symbol or "").upper()) or {}
    return inst.get("venue")


def record_report(device: PhoneDevice, body: Dict[str, Any]) -> Dict[str, Any]:
    """Phone report → ``ingest_report`` with the account forced from the token.

    ``kind=ticket_result`` is the phone's own close-out of a claim that placed
    nothing (dry fill, refusal, mismatch): it moves the ticket to a terminal
    status without writing a fill row."""
    body = dict(body)
    body["account_id"] = device.account_id
    body.pop("account", None)
    if body.get("kind") == "ticket_result":
        tid = str(body.get("ticket_id") or "")
        status = str(body.get("result") or "")
        if status not in ("dry_filled", "refused", "skipped", "mismatch_flattened"):
            raise ValueError("ticket_result.result invalid")
        form = body.get("form") if isinstance(body.get("form"), dict) else {}
        n = _close_claim(device.account_id, tid, status, {
            "result": status, "reason": str(body.get("reason") or "")[:300],
            "form": json.loads(dump_capped(form, 20000)),
            "at": _now().isoformat()})
        return {"ok": n == 1, "kind": "ticket_result", "updated": n}
    from src.prop.prop_report import ingest_report
    res = ingest_report(body)
    tid = body.get("ticket_id")
    if tid and str(body.get("status") or "") in ("placed", "open", "filled"):
        prop_journal.set_ticket_status(str(tid), "placed", account_id=device.account_id)
    return res


def _close_claim(account_id: str, ticket_id: str, status: str, result: Dict[str, Any]) -> int:
    """Move a CLAIMED ticket to a terminal status and keep the phone's read-back (form dump: control labels
    and our own typed values) in ``meta.phone.result``: that dump is how unread labels (TP/SL) get read."""
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        r = conn.execute("SELECT * FROM prop_tickets WHERE ticket_id = ? AND account_id = ?",
                         (ticket_id, account_id)).fetchone()
        if r is None or r["status"] != "claimed":
            return 0
        m = _meta(r)
        m.setdefault("phone", {})["result"] = result
        cur = conn.execute("UPDATE prop_tickets SET status = ?, meta = ? WHERE ticket_id = ? "
                           "AND account_id = ? AND status = 'claimed'",
                           (status, json.dumps(m), ticket_id, account_id))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def serve_dry_test_request(device: PhoneDevice) -> Optional[str]:
    """One-shot, git-visible way to run the dry end-to-end check WITHOUT the operator.

    ``config/prop_platforms.yaml::phone_accounts.<acct>.dry_test_request: <request id>``. On the next claim, if no
    ticket carries that request id yet, ONE always-dry test ticket is written (meta.test, so submit is forced dry
    on the server and the phone never submits it). The id makes it idempotent; a new id requests a new test."""
    req = str(phone_config(device.account_id).get("dry_test_request") or "").strip()
    if not req:
        return None
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        seen = conn.execute("SELECT 1 FROM prop_tickets WHERE account_id = ? AND meta LIKE ? LIMIT 1",
                            (device.account_id, f'%"dry_test_request": "{req}"%')).fetchone()
    finally:
        conn.close()
    if seen:
        return None
    try:
        sym = str(phone_config(device.account_id).get("dry_test_symbol") or "ETHUSDT").upper()
        tid = make_test_ticket(device, symbol=sym, request_id=req)["ticket_id"]
    except ValueError:
        logger.warning("phone_executor: dry_test_request %s could not be served (no reference price)", req)
        return None
    logger.info("phone_executor: served dry_test_request %s as %s", req, tid)
    return tid


def make_test_ticket(device: PhoneDevice, *, symbol: str = "ETHUSDT",
                     entry: Optional[float] = None, request_id: Optional[str] = None) -> Dict[str, Any]:
    """A synthetic, ALWAYS-dry ticket for the end-to-end dry check. ``meta.test``
    forces ``submit=dry`` in :func:`claim_next` whatever the account mode, and
    the phone also refuses to submit any test ticket."""
    if entry is None:
        entry = _bybit_last(symbol)
    if not entry or entry <= 0:
        raise ValueError("no reference price for the test ticket")
    tid = f"phone-test-{uuid.uuid4().hex[:12]}"
    now = _now()
    # Geometry off the POSTED limit (a long rests 3% below the last price, so the dry fill can never be marketable):
    # sl < limit < tp. The first build put sl/tp off the LAST price, so sl sat above the limit and the phone
    # correctly refused every test ticket ("bracket geometry wrong for the side"), 2026-10-05 21:23Z.
    limit = round(entry * 0.97, 2)
    sl = round(limit * 0.98, 2)
    tp = round(limit * 1.04, 2)
    prop_journal.record_ticket({
        "ticket_id": tid, "account_id": device.account_id, "strategy": "phone_test",
        "symbol": symbol, "direction": "long", "side": "buy",
        "entry": limit, "sl": sl, "tp": tp, "qty": 0.01,
        "risk_usd": None, "signal_time": now.isoformat(),
        "valid_until": (now + timedelta(minutes=15)).isoformat(),
        "status": "emitted", "message": "phone dry end-to-end test ticket",
        "meta": {"test": True, **({"dry_test_request": request_id} if request_id else {})},
    })
    return {"ok": True, "ticket_id": tid}


def _bybit_last(symbol: str) -> Optional[float]:
    import urllib.request
    url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 (fixed https host)
            d = json.loads(r.read().decode())
        return float(d["result"]["list"][0]["lastPrice"])
    except Exception:  # noqa: BLE001  # allow-silent: caller refuses on None with a 400
        logger.warning("phone_executor: bybit reference price read failed", exc_info=True)
        return None


def record_event(device: PhoneDevice, body: Dict[str, Any], *, send=None) -> Dict[str, Any]:
    kind = str(body.get("event") or "")
    if kind not in _EVENT_KINDS:
        raise ValueError("unknown event")
    reason = re.sub(r"[^A-Za-z0-9 _.:/()=+-]", "", str(body.get("reason") or ""))[:160]
    # Never forward anything that looks like a link, an email or a long number.
    reason = re.sub(r"https?\S*|\S+@\S+|\d{6,}", "*", reason)
    ticket = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("ticket_id") or ""))[:64]
    msg = f"📱 phone {device.account_id}: {kind}" + (f" [{ticket}]" if ticket else "") + (
        f" — {reason}" if reason else "")
    logger.info("phone_executor event %s %s %s", device.account_id, kind, ticket)
    quiet = kind == "app_started" and not body.get("ping")
    sent = False
    if not quiet:
        try:
            if send is None:
                from src.runtime.notify import send_telegram_direct
                send = send_telegram_direct
            sent = bool(send(msg, parse_mode=None))
        except Exception:  # noqa: BLE001  # allow-silent: logged; the event is still acknowledged
            logger.warning("phone_executor: event ping failed", exc_info=True)
    return {"ok": True, "pinged": sent}


def alert_expired(account_id: str, ids: list, *, send=None) -> None:
    if not ids:
        return
    msg = (f"📱 phone {account_id}: {len(ids)} claimed ticket(s) got no report in "
           f"{CLAIM_TIMEOUT_S}s and were marked skipped (never re-offered). Check the terminal "
           f"for an unreported position: {', '.join(ids)[:300]}")
    try:
        if send is None:
            from src.runtime.notify import send_telegram_direct
            send = send_telegram_direct
        send(msg, parse_mode=None)
    except Exception:  # noqa: BLE001  # allow-silent: logged
        logger.warning("phone_executor: expiry alert failed", exc_info=True)
