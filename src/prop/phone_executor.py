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
* **Server read-back + go-token (§ 3.4).** Before a live click the phone posts what the filled form shows;
  :func:`verify_readback` re-checks it against the ticket and the instrument steps and issues ONE 30 s token
  bound to the read-back hash; :func:`redeem_go_token` consumes it right before the click. See the section at
  the end of this module.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
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
    "mismatch", "flattened", "dry_fill_ok", "submitted", "app_started", "error", "terminal_miss", "heartbeat",
}
# terminal_miss carries the control texts the page showed (our own UI labels, scrubbed like a reason) so the
# next "terminal did not load" is self-diagnosing; only the LATEST one per account is kept, beside the journal.
_DIAG_MAX = 40


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
    if body.get("kind") == "account_status":
        # MEASURED off the terminal's account panel (read-only). A value that was not read stays None (absent, never 0);
        # a report with neither is refused so "we did not look" never lands as a snapshot.
        clean: dict[str, Any] = {}
        for k in ("balance", "equity"):
            v = body.get(k)
            if v is None:
                clean[k] = None
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ValueError(f"account_status.{k} invalid")
            clean[k] = float(v)
        if clean["balance"] is None and clean["equity"] is None:
            raise ValueError("account_status needs balance or equity")
        label = "portfolio" if str(body.get("equity_label") or "") == "portfolio" else "equity"
        body = {"kind": "account_status", "account_id": device.account_id, "balance": clean["balance"],
                "equity": clean["equity"], "source": "phone_executor", "provenance": "MEASURED",
                "equity_label": label}
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


def _unserved_dry_test_request(account_id: str) -> Optional[str]:
    """The configured ``dry_test_request`` id when no ticket carries it yet, else None. Read-only."""
    req = str(phone_config(account_id).get("dry_test_request") or "").strip()
    if not req:
        return None
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        seen = conn.execute("SELECT 1 FROM prop_tickets WHERE account_id = ? AND meta LIKE ? LIMIT 1",
                            (account_id, f'%"dry_test_request": "{req}"%')).fetchone()
    finally:
        conn.close()
    return None if seen else req


def pending_count(device: PhoneDevice, now: Optional[datetime] = None) -> int:
    """READ-ONLY peek for the backgrounded app (PI-20261006-APBY4NTV-0006): how many tickets the next claim
    would find (still-valid ``emitted`` tickets, plus one for an unserved ``dry_test_request``). Claims nothing,
    writes nothing; 0 when the kill switch is off, matching ``claim_next``."""
    now = now or _now()
    acct = device.account_id
    if kill_switch(acct) == "off":
        return 0
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        rows = conn.execute("SELECT valid_until FROM prop_tickets WHERE account_id = ? AND status = 'emitted'",
                            (acct,)).fetchall()
    finally:
        conn.close()
    n = sum(1 for r in rows if (vu := _parse(r["valid_until"])) is not None and vu > now)
    return n + (1 if _unserved_dry_test_request(acct) else 0)


def serve_dry_test_request(device: PhoneDevice) -> Optional[str]:
    """One-shot, git-visible way to run the dry end-to-end check WITHOUT the operator.

    ``config/prop_platforms.yaml::phone_accounts.<acct>.dry_test_request: <request id>``. On the next claim, if no
    ticket carries that request id yet, ONE always-dry test ticket is written (meta.test, so submit is forced dry
    on the server and the phone never submits it). The id makes it idempotent; a new id requests a new test."""
    req = _unserved_dry_test_request(device.account_id)
    if not req:
        return None
    try:
        tid = make_test_ticket(device, request_id=req)["ticket_id"]
    except ValueError:
        logger.warning("phone_executor: dry_test_request %s could not be served (no reference price)", req)
        return None
    logger.info("phone_executor: served dry_test_request %s as %s", req, tid)
    return tid


def write_dry_test_ticket(account_id: str, symbol: str = "ETHUSDT", *, source: str = "phone-dry-test",
                          entry: Optional[float] = None) -> Dict[str, Any]:
    """The ``phone-dry-test`` system-action's writer (PI-20261006-APBY4NTV-0009): ONE always-dry test
    ticket through the SAME :func:`make_test_ticket` the in-app Dry test button and ``dry_test_request``
    use, so ``meta.test`` is set and :func:`submit_mode` forces ``dry`` whatever the account mode.

    Fail-closed on both inputs, because this runs from an issue body: the account must be declared under
    ``phone_accounts`` in ``config/prop_platforms.yaml`` (a VM-driven prop account or a typo is refused,
    never written) and the symbol must be one of that account's ``instruments`` (nothing else has a venue
    symbol the phone could type). ``meta.source`` records who asked. Cannot write a non-test ticket:
    there is no code path from here that omits ``test=True``."""
    acct = str(account_id or "").strip()
    sym = str(symbol or "").strip().upper()
    if not acct or not is_phone_account(acct):
        raise ValueError(f"{acct or '<empty>'} is not a phone_accounts entry in config/prop_platforms.yaml")
    instruments = phone_config(acct).get("instruments") or {}
    if sym not in instruments:
        raise ValueError(f"{sym or '<empty>'} is not in phone_accounts.{acct}.instruments "
                         f"(allowed: {', '.join(sorted(instruments)) or 'none'})")
    out = make_test_ticket(PhoneDevice(f"system-action:{source}", acct), symbol=sym, entry=entry,
                           source=str(source or "phone-dry-test")[:80])
    logger.info("phone_executor: %s wrote dry test ticket %s for %s/%s", source, out["ticket_id"], acct, sym)
    return {**out, "account_id": acct, "symbol": sym, "submit": "dry"}


def make_test_ticket(device: PhoneDevice, *, symbol: str = "ETHUSDT",
                     entry: Optional[float] = None, request_id: Optional[str] = None,
                     source: Optional[str] = None) -> Dict[str, Any]:
    """A synthetic, ALWAYS-dry ticket for the end-to-end dry check. ``meta.test``
    forces ``submit=dry`` in :func:`claim_next` whatever the account mode, and
    the phone also refuses to submit any test ticket. ``source`` (optional) names
    the caller in ``meta.source`` — the ``phone-dry-test`` system-action sets it."""
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
        "meta": {"test": True, **({"dry_test_request": request_id} if request_id else {}),
                 **({"source": source} if source else {})},
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
    if kind == "terminal_miss":
        _write_diag(device.account_id, reason, body.get("controls"))
    if kind == "heartbeat":
        _write_heartbeat(device.account_id, reason, body.get("state"))
    quiet = (kind == "app_started" and not body.get("ping")) or kind in ("terminal_miss", "heartbeat")
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


def _scrub(s: Any, n: int) -> str:
    s = re.sub(r"[^A-Za-z0-9 _.:/()=+&%-]", "", str(s or ""))[:n]
    return re.sub(r"https?\S*|\S+@\S+|\d{6,}", "*", s)


def _diag_path(account_id: str) -> Path:
    return Path(prop_journal._db_path()).parent / f"prop_phone_diag_{re.sub(r'[^a-z0-9_]', '', account_id)}.json"


def _write_diag(account_id: str, reason: str, controls: Any) -> None:
    ctl = [_scrub(c, 40) for c in (controls if isinstance(controls, list) else [])][:_DIAG_MAX]
    try:
        _diag_path(account_id).write_text(json.dumps(
            {"at": _now().isoformat(), "reason": reason, "controls": [c for c in ctl if c]}))
    except OSError:  # allow-silent: diagnostics only; the event is still acknowledged
        logger.warning("phone_executor: diag write failed", exc_info=True)


# heartbeat (2026-10-06 08:15Z: the phone went silent for an hour with no claim and no event, because every
# no-claim branch but one only set the on-screen status). The app posts its status line + gate state on every
# tick path at most every 2 min; only the latest is kept. Keys are a fixed allowlist; values are bools, small
# ints or scrubbed short strings.
_HB_KEYS = {"st", "paused", "hold", "host", "onAccount", "path_depth", "ready", "probe", "panels", "orderControl",
            "ticketOpen", "buySell", "tabs", "inputs", "armed", "build", "fg", "jsTimeouts", "pending", "acct"}


def _write_heartbeat(account_id: str, reason: str, state: Any) -> None:
    st: Dict[str, Any] = {}
    for k, v in (state.items() if isinstance(state, dict) else []):
        if k not in _HB_KEYS:
            continue
        if isinstance(v, bool) or (isinstance(v, int) and abs(v) < 100000):
            st[k] = v
        else:
            st[k] = _scrub(v, 60)
    try:
        _heartbeat_path(account_id).write_text(json.dumps({"at": _now().isoformat(), "status": reason, "state": st}))
    except OSError:  # allow-silent: diagnostics only; the event is still acknowledged
        logger.warning("phone_executor: heartbeat write failed", exc_info=True)


def _heartbeat_path(account_id: str) -> Path:
    return _diag_path(account_id).with_name(f"prop_phone_hb_{re.sub(r'[^a-z0-9_]', '', account_id)}.json")


def last_heartbeat(account_id: str) -> Optional[Dict[str, Any]]:
    """The phone's latest heartbeat (status line + gate state), or None when none was ever posted."""
    try:
        return json.loads(_heartbeat_path(account_id).read_text())
    except (OSError, ValueError):
        return None


def last_diag(account_id: str) -> Optional[Dict[str, Any]]:
    """The latest terminal_miss diagnostic for the account, or None when none was ever posted."""
    try:
        return json.loads(_diag_path(account_id).read_text())
    except (OSError, ValueError):
        return None


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


# ---------------------------------------------------------------------------
# Server-side read-back + go-token (design § 3.4, PI-20261005-YUVCGTMJ-0003).
#
# The phone already checks its own fill (labels, values within one step, the submit label carries the side). This is
# the SECOND, independent check, on the server: before a LIVE click the phone posts what it READ on the page (raw
# strings, never its own computed numbers) to ``/phone/verify``; the server re-verifies them against the stored ticket
# and the venue's instrument steps in ``config/prop_platforms.yaml`` and answers with a one-shot token bound to the
# ticket id and a hash of the verified read-back, valid GO_TOKEN_TTL_S. Right before the click the phone re-reads the
# form, re-hashes it, and redeems the token at ``/phone/go``; the click happens only on ``go: true``. The redeem is a
# conditional UPDATE on the exact meta text, so a token is consumed at most once (a replay is refused). A test ticket,
# a dry account or the kill switch never get a LIVE token: they get a ``dry`` token whose redeem always says no.
# Every mismatch fails closed (``ok: false`` + reasons, kept on the ticket, operator pinged).
# ---------------------------------------------------------------------------

GO_TOKEN_TTL_S = 30  # design § 3.4: "valid 30 s"
# The read-back fields, in hash order. The phone builds the SAME "key=value" lines in the same order (MainActivity.kt
# readBackLines); the server recomputes the hash from the posted fields and refuses when the phone's hash differs.
READBACK_FIELDS = ("ticket_id", "symbol", "side", "order_type", "price", "qty", "qty_unit", "tp", "sl",
                   "submit_label", "submit_disabled", "tpsl")
_EPS = 1e-9


def readback_hash(fields: Dict[str, Any]) -> str:
    """SHA-256 over ``key=value`` lines in READBACK_FIELDS order (values as the phone read them, trimmed)."""
    lines = "\n".join(f"{k}={str(fields.get(k) if fields.get(k) is not None else '').strip()}" for k in READBACK_FIELDS)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _num(s: Any) -> Optional[float]:
    try:
        v = float(str(s).replace(",", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _base_asset(venue: str) -> str:
    v = re.sub(r"[^A-Z0-9]", "", str(venue or "").upper())
    return re.sub(r"(USDT|USDC|USD|PERP)$", "", v)


def check_readback(ticket: Dict[str, Any], fields: Dict[str, Any], instrument: Dict[str, Any]) -> list:
    """Every reason the phone's read-back does not match the ticket ([] = verified). Pure; no I/O.

    Tolerances are ONE venue step from the TICKET's own value (the phone rounds to the step and reads back within
    half a step, so a correct fill is always inside one step). Quantity may never exceed the ticket (sizing is the
    risk), and may sit at most one step below it (the phone floors to the step)."""
    reasons = []
    venue = str(instrument.get("venue") or "")
    q_step, p_step = _num(instrument.get("qty_step")), _num(instrument.get("price_step"))
    if not venue or not q_step or not p_step or q_step <= 0 or p_step <= 0:
        return ["instrument venue/qty_step/price_step not declared in prop_platforms phone_accounts"]
    base = _base_asset(venue)
    for k in READBACK_FIELDS:
        if "\n" in str(fields.get(k) if fields.get(k) is not None else ""):
            reasons.append(f"{k} contains a newline")
    if str(fields.get("ticket_id") or "") != str(ticket.get("ticket_id") or ""):
        reasons.append("ticket_id differs from the ticket")
    long = str(ticket.get("direction") or "").lower() == "long"
    side_re = re.compile(r"long|buy", re.I) if long else re.compile(r"short|sell", re.I)
    opp_re = re.compile(r"short|sell", re.I) if long else re.compile(r"long|buy", re.I)
    sym = re.sub(r"[^A-Z0-9]", "", str(fields.get("symbol") or "").upper())
    if not base or base not in sym:
        reasons.append(f"symbol '{str(fields.get('symbol') or '')[:24]}' does not name {base}")
    label = str(fields.get("submit_label") or "")
    if base not in re.sub(r"[^A-Z0-9]", "", label.upper()):
        reasons.append(f"submit label does not name {base}")
    if not side_re.search(label) or opp_re.search(label):
        reasons.append("submit label does not carry the ticket's side")
    side = str(fields.get("side") or "").strip().lower()
    if side != ("buy" if long else "sell"):
        reasons.append(f"side tab '{side[:12]}' is not {'buy' if long else 'sell'}")
    if str(fields.get("order_type") or "").strip().lower() != "limit":
        reasons.append(f"order type '{str(fields.get('order_type') or '')[:12]}' is not limit")
    if str(fields.get("submit_disabled") or "").strip().lower() != "false":
        reasons.append("submit not read as enabled")
    if str(fields.get("tpsl") or "").strip().lower() != "true":
        reasons.append("TP/SL not read as on")
    unit = re.sub(r"[^A-Z0-9]", "", str(fields.get("qty_unit") or "").upper())
    if not unit or base not in unit:
        reasons.append(f"quantity unit '{str(fields.get('qty_unit') or '')[:24]}' does not name {base}")

    want = {k: _num(ticket.get(k)) for k in ("entry", "qty", "tp", "sl")}
    got = {"entry": _num(fields.get("price")), "qty": _num(fields.get("qty")),
           "tp": _num(fields.get("tp")), "sl": _num(fields.get("sl"))}
    for k, name in (("entry", "price"), ("tp", "tp"), ("sl", "sl")):
        if want[k] is None or got[k] is None:
            reasons.append(f"{name} unreadable")
        elif abs(got[k] - want[k]) > p_step + _EPS:
            reasons.append(f"{name} {got[k]} is more than one step ({p_step}) from the ticket's {want[k]}")
    if want["qty"] is None or got["qty"] is None or got["qty"] <= 0:
        reasons.append("qty unreadable or not positive")
    elif got["qty"] > want["qty"] + _EPS:
        reasons.append(f"qty {got['qty']} exceeds the ticket's {want['qty']}")
    elif want["qty"] - got["qty"] >= q_step - _EPS:
        reasons.append(f"qty {got['qty']} is a full step ({q_step}) or more below the ticket's {want['qty']}")
    e, tp, sl = got["entry"], got["tp"], got["sl"]
    if None not in (e, tp, sl) and not ((sl < e < tp) if long else (tp < e < sl)):
        reasons.append("bracket geometry on the form is wrong for the side")
    return reasons


def _token_sha(token: str) -> str:
    return hashlib.sha256(("go:" + str(token)).encode("utf-8")).hexdigest()


def _ping(msg: str, send=None) -> bool:
    try:
        if send is None:
            from src.runtime.notify import send_telegram_direct
            send = send_telegram_direct
        return bool(send(msg, parse_mode=None))
    except Exception:  # noqa: BLE001  # allow-silent: logged; the refusal itself is the safety property
        logger.warning("phone_executor: go-token ping failed", exc_info=True)
        return False


def _claimed_ticket(conn: sqlite3.Connection, device: PhoneDevice, ticket_id: str):
    r = conn.execute("SELECT * FROM prop_tickets WHERE ticket_id = ? AND account_id = ?",
                     (ticket_id, device.account_id)).fetchone()
    if r is None:
        return None, "ticket not found for this account"
    if r["status"] != "claimed":
        return None, f"ticket is {r['status']}, not claimed"
    if str((_meta(r).get("phone") or {}).get("device_id") or "") != device.device_id:
        return None, "ticket was claimed by another device"
    return r, ""


def verify_readback(device: PhoneDevice, body: Dict[str, Any], *, now: Optional[datetime] = None,
                    env: Optional[Dict[str, str]] = None, send=None) -> Dict[str, Any]:
    """``POST /phone/verify``: re-verify the phone's read-back; issue ONE token per ticket.

    ``mode`` on the answer is ``live`` only when :func:`submit_mode` says live NOW (account live, kill switch not
    off/dry, not a test ticket); otherwise ``dry``. Refusals are kept in ``meta.phone.verify`` and pinged."""
    now = now or _now()
    fields = body.get("readback") if isinstance(body.get("readback"), dict) else {}
    tid = str(body.get("ticket_id") or fields.get("ticket_id") or "")[:80]
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        r, why = _claimed_ticket(conn, device, tid)
        if r is None:
            return _verify_refused(device, tid, [why], None, send=send)
        m = _meta(r)
        ph = m.get("phone") or {}
        if ph.get("go"):
            return _verify_refused(device, tid, ["a go-token was already issued for this ticket (one per ticket)"],
                                   None, send=send)
        ticket = prop_journal._ticket_row(r)
        inst = (phone_config(device.account_id).get("instruments") or {}).get(str(ticket.get("symbol") or "").upper())
        reasons = check_readback(ticket, fields, inst or {})
        rb_hash = readback_hash(fields)
        if str(body.get("readback_sha256") or "") != rb_hash:
            reasons.append("phone's read-back hash differs from the server's (canonical form drift)")
        test = bool(m.get("test"))
        mode = submit_mode(device.account_id, test=test, env=env)
        vu = _parse(r["valid_until"])
        if mode == "live" and (vu is None or vu <= now):
            reasons.append("ticket validity expired before the live submit")
        if reasons:
            ph["verify"] = {"at": now.isoformat(), "ok": False, "reasons": reasons[:12]}
            m["phone"] = ph
            conn.execute("UPDATE prop_tickets SET meta = ? WHERE ticket_id = ? AND status = 'claimed'",
                         (json.dumps(m), tid))
            conn.commit()
            return _verify_refused(device, tid, reasons, rb_hash, send=send)
        token = secrets.token_urlsafe(24)
        expires = now + timedelta(seconds=GO_TOKEN_TTL_S)
        ph["go"] = {"token_sha256": _token_sha(token), "readback_sha256": rb_hash, "mode": mode,
                    "issued_at": now.isoformat(), "expires_at": expires.isoformat(), "used": False}
        ph["verify"] = {"at": now.isoformat(), "ok": True, "mode": mode}
        m["phone"] = ph
        cur = conn.execute("UPDATE prop_tickets SET meta = ? WHERE ticket_id = ? AND status = 'claimed' AND meta = ?",
                           (json.dumps(m), tid, r["meta"]))
        conn.commit()
        if cur.rowcount != 1:
            return _verify_refused(device, tid, ["ticket changed while verifying"], rb_hash, send=send)
    finally:
        conn.close()
    logger.info("phone_executor: go-token issued %s %s mode=%s", device.account_id, tid, mode)
    return {"ok": True, "ticket_id": tid, "mode": mode, "token": token, "readback_sha256": rb_hash,
            "expires_at": expires.isoformat(), "ttl_s": GO_TOKEN_TTL_S}


def _verify_refused(device: PhoneDevice, tid: str, reasons: list, rb_hash: Optional[str], *, send=None) -> Dict[str, Any]:
    logger.warning("phone_executor: read-back REFUSED %s %s: %s", device.account_id, tid, "; ".join(reasons)[:300])
    _ping(f"📱 phone {device.account_id}: SERVER read-back refused [{_scrub(tid, 64)}] — "
          f"{_scrub('; '.join(reasons), 300)}. No go-token; the phone does not submit.", send)
    return {"ok": False, "ticket_id": tid, "mode": None, "token": None, "readback_sha256": rb_hash,
            "reasons": reasons[:12]}


def redeem_go_token(device: PhoneDevice, body: Dict[str, Any], *, now: Optional[datetime] = None,
                    env: Optional[Dict[str, str]] = None, send=None) -> Dict[str, Any]:
    """``POST /phone/go``: consume the ticket's token right before the click. ``go: true`` only for an unexpired,
    unused, LIVE token whose hash matches the form the phone re-read just now, while submit_mode is still live.
    Any redeem attempt consumes the token (a refused redeem cannot be retried), so a replay is always refused."""
    now = now or _now()
    tid = str(body.get("ticket_id") or "")[:80]
    token = str(body.get("token") or "")
    rb_hash = str(body.get("readback_sha256") or "")
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        r, why = _claimed_ticket(conn, device, tid)
        if r is None:
            return _go_refused(device, tid, why, send=send)
        m = _meta(r)
        ph = m.get("phone") or {}
        go = ph.get("go") or {}
        if not go:
            return _go_refused(device, tid, "no go-token was issued for this ticket", send=send)
        if not token or not hmac.compare_digest(_token_sha(token), str(go.get("token_sha256") or "")):
            return _go_refused(device, tid, "token does not match this ticket", send=send)
        if go.get("used"):
            return _go_refused(device, tid, "token already used (replay)", send=send)
        reason = ""
        exp = _parse(go.get("expires_at"))
        if exp is None or exp <= now:
            reason = "token expired"
        elif not hmac.compare_digest(rb_hash, str(go.get("readback_sha256") or "")):
            reason = "form changed since it was verified (hash differs)"
        elif go.get("mode") != "live":
            reason = "dry token (test ticket, dry account or kill switch): never a live submit"
        elif submit_mode(device.account_id, test=bool(m.get("test")), env=env) != "live":
            reason = "account or kill switch is no longer live"
        go.update({"used": True, "used_at": now.isoformat(), "go": not reason, "refused": reason or None})
        ph["go"] = go
        m["phone"] = ph
        cur = conn.execute("UPDATE prop_tickets SET meta = ? WHERE ticket_id = ? AND status = 'claimed' AND meta = ?",
                           (json.dumps(m), tid, r["meta"]))
        conn.commit()
        if cur.rowcount != 1:
            return _go_refused(device, tid, "token already used (concurrent redeem)", send=send)
    finally:
        conn.close()
    if reason:
        # a dry token's refusal is the expected dry path: logged, not pinged
        return _go_refused(device, tid, reason, send=send, quiet=go.get("mode") != "live")
    logger.info("phone_executor: GO %s %s", device.account_id, tid)
    return {"ok": True, "go": True, "ticket_id": tid}


def _go_refused(device: PhoneDevice, tid: str, reason: str, *, send=None, quiet: bool = False) -> Dict[str, Any]:
    logger.warning("phone_executor: go REFUSED %s %s: %s", device.account_id, tid, reason)
    if not quiet:
        _ping(f"📱 phone {device.account_id}: go-token refused [{_scrub(tid, 64)}] — {_scrub(reason, 160)}. "
              f"The phone does not submit.", send)
    return {"ok": True, "go": False, "ticket_id": tid, "reason": reason}
