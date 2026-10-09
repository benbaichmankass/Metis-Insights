"""The prop trail for PHONE-executed accounts (PROP-TRAIL-PHONE, PI-20261006-KX6ZKFNA-0002).

Why this exists. ``prop_trail.run_trail_step`` runs only inside the VM executor tick
(``scripts/prop/prop_executor_tick.py::run_cycle_and_trail``); a phone-executed account
(``config/prop_platforms.yaml::phone_accounts``, today breakout_2) has no VM-side
terminal, so before this module its ``*_prop`` legs traded a STATIC SL+TP bracket on
real prop money while their Stage-0 record assumed the chandelier trail.

What it does. The SERVER computes the trail with the ONE replay
(:func:`src.prop.prop_trail.plan_trail`, not a second implementation) and publishes an
**amend** for the phone through the existing claim channel. The phone edits the
position on the terminal, reads it back, and reports the result here.

Contract (every rule fails closed):

* **State lives on the parent ticket** (``prop_tickets.meta.phone_trail``), so there is
  no new table, and ONE slot means at most ONE amend outstanding per ticket by
  construction. Every write is a compare-and-swap on the meta text.
* **Planned once per closed bar** of the leg's timeframe, only for tickets the phone
  reported ``placed`` (or a human reported ``filled``), never for a test ticket.
* **Served only to an app that declares it** (``accepts: ["amend"]`` on the claim):
  an older app never receives an amend it would misread as an entry.
* The amend carries the stop the server believes is resting (``from_sl``) and the TP
  to keep (``tp``); the phone refuses before any click unless the terminal shows
  exactly those (a stop a human moved is never overridden: ``human_moved``).
* **Results** (``kind: amend_result``): ``amended`` (read back on the terminal, re-checked
  here against the asked levels), ``dry_amended`` (walked, typed, read back in the dialog,
  cancelled), ``refused`` (no click), ``no_position``, ``human_moved``, ``mismatch`` (a
  click whose result did not read back).
* **No halting** (operator directive 2026-10-09, Prime Directive rule 7: *"There is no
  halting."*). A ``refused``, a ``mismatch`` or a claimed amend with no report is THAT
  ATTEMPT failing: the ticket backs off (1, 2, 4 ... capped at :data:`BACKOFF_CAP_BARS`
  closed bars) and is re-planned. The next amend still carries the stop the server believes
  is resting, so the phone's pre-click check decides whether the terminal agrees before any
  click. The first failure of a streak pings; :data:`FLAG_AFTER` consecutive failures raise
  ONE red flag; the first success after a flag announces recovery once. A ``no_position``
  read is a miss, never an end: it backs off and re-checks (a position that is really gone
  is simply never amended again; the ticket ages out after :data:`MAX_AGE_DAYS`).
* **A human's stop is never fought.** ``human_moved`` holds the ticket's trail
  (``human_hold``) until the human's levels are on record (a ``{kind: amend}`` report,
  :func:`note_human_amend`); the trail then resumes from them. That is the operator acting,
  not a self-halt. A ``locked``/``ended`` state persisted by the pre-2026-10-09 code is
  ignored on load with a log line (a human-moved lock is carried over as ``human_hold``).
* ``submit`` follows the entry path's own decision (:func:`phone_executor.submit_mode`);
  there is no device-local switch on top (ARMED-GATE). No new gate.
* TP: :func:`request_tp_amend` publishes a TP revision through the same slot for lane
  TP-DOCTRINE's verdicts; the trail itself never moves the TP.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.prop import prop_journal

logger = logging.getLogger(__name__)

KEY = "phone_trail"
AMEND_VALID_S = 20 * 60          # an emitted amend not claimed in time expires (re-planned next bar)
CLAIM_TIMEOUT_S = 180            # same watchdog as an entry claim (phone_executor.CLAIM_TIMEOUT_S)
FLAG_AFTER = 3                   # consecutive failed attempts on one ticket -> ONE red flag (never a stop)
BACKOFF_CAP_BARS = 8             # retry backoff after a failed attempt: 1, 2, 4, 8, 8 ... closed bars
MAX_NO_POSITION = 6              # never-seen position reads -> ONE red flag (re-checked with backoff, never ended)
MAX_AGE_DAYS = 14                # parent tickets older than this are not planned
PARENT_STATUSES = ("placed", "filled")
RESULTS = ("amended", "dry_amended", "refused", "no_position", "human_moved", "mismatch")
QUOTE_BUFFER_ATR = 0.1           # prop_trail.QUOTE_BUFFER_ATR: the new stop clears the price by this many ATRs
_HISTORY_MAX = 12


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


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _meta(raw: Any) -> Dict[str, Any]:
    try:
        m = json.loads(raw or "{}")
        return m if isinstance(m, dict) else {}
    except (TypeError, ValueError):
        return {}


def _round_near(v: float, step: Optional[float]) -> float:
    return round(round(v / step) * step, 10) if step and step > 0 else v


def _price_step(account_id: str, symbol: str) -> Optional[float]:
    from src.prop.phone_executor import phone_config
    inst = (phone_config(account_id).get("instruments") or {}).get(str(symbol or "").upper()) or {}
    return _f(inst.get("price_step"))


def _send(msg: str, send: Optional[Callable[..., Any]] = None) -> None:
    try:
        if send is None:
            from src.runtime.notify import send_telegram_direct
            send = send_telegram_direct
        send(msg, parse_mode=None)
    except Exception:  # noqa: BLE001  # allow-silent: logged; the state write already happened
        logger.warning("phone_trail: ping failed", exc_info=True)


def _cas(conn: Any, ticket_id: str, old_raw: Any, new_meta: Mapping[str, Any]) -> bool:
    """Write ``new_meta`` only if the row's meta is still ``old_raw`` (one writer wins)."""
    cur = conn.execute("UPDATE prop_tickets SET meta = ? WHERE ticket_id = ? AND meta IS ?",
                       (json.dumps(new_meta), ticket_id, old_raw))
    conn.commit()
    return cur.rowcount == 1


def _levels(row: Mapping[str, Any], tr: Mapping[str, Any], step: Optional[float]) -> tuple:
    """(resting SL, resting TP) as the server believes them: the last verified amend, else the
    ticket's own levels rounded to the venue step the way the phone typed them."""
    sl = _f(tr.get("resting_sl"))
    tp = _f(tr.get("resting_tp"))
    if sl is None and _f(row["sl"]) is not None:
        sl = _round_near(float(row["sl"]), step)
    if tp is None and _f(row["tp"]) is not None:
        tp = _round_near(float(row["tp"]), step)
    return sl, tp


_HUMAN_LOCK_PREFIX = "the terminal's stop differs"


def _load_trail(m: Mapping[str, Any], tid: str) -> Dict[str, Any]:
    """The ticket's trail state. A permanent ``locked``/``ended`` written by the pre-2026-10-09
    code is dropped (no halting); a human-moved lock is carried over as ``human_hold``."""
    tr = dict(m.get(KEY) or {})
    old_lock = tr.pop("locked", None)
    old_end = tr.pop("ended", None)
    if old_lock and str(old_lock).startswith(_HUMAN_LOCK_PREFIX):
        tr.setdefault("human_hold", str(old_lock))
    elif old_lock or old_end:
        logger.info("phone_trail: %s: ignoring stale %s from the old code (no halting): %s", tid,
                    "lock" if old_lock else "end", old_lock or old_end)
    return tr


def _backoff_bars(n: int) -> int:
    return min(2 ** max(int(n) - 1, 0), BACKOFF_CAP_BARS)


def _fail(tr: Dict[str, Any], tid: str, at: str, what: str, alerts: List[str], *, counter: str = "fails",
          flag_at: int = FLAG_AFTER, first: Optional[str] = None) -> None:
    """One attempt failed: count it, back off, ping on the first of a streak, ONE red flag at
    ``flag_at`` (0 = never flag). Never ends or locks the trail."""
    n = int(tr.get(counter) or 0) + 1
    tr[counter] = n
    tr["backoff"] = {"n": n, "since": at}
    if n == 1 and first:
        alerts.append(f"{tid}: {first}")
    if flag_at and n == flag_at and not tr.get("flagged"):
        tr["flagged"] = what
        alerts.append(f"🚩 {tid}: {n} consecutive failed trail attempts ({what}). The trail keeps retrying "
                      f"with backoff (up to every {BACKOFF_CAP_BARS} bars); it needs help to succeed. CHECK the "
                      "stop and target on the terminal")
    elif n > 1:
        logger.warning("phone_trail: %s: attempt failed again (%s x%d): %s", tid, counter, n, what)


def _ok(tr: Dict[str, Any], tid: str, alerts: List[str]) -> None:
    """An attempt succeeded: the streak and its backoff clear; a raised flag is announced recovered once."""
    tr["fails"], tr["no_position"], tr["absent"] = 0, 0, 0
    tr.pop("backoff", None)
    flagged = tr.pop("flagged", None)
    if flagged:
        alerts.append(f"✅ {tid}: trail RECOVERED (was flagged: {str(flagged)[:120]})")


def _hist(tr: Dict[str, Any], entry: Mapping[str, Any]) -> None:
    h = list(tr.get("history") or [])
    h.append(dict(entry))
    tr["history"] = h[-_HISTORY_MAX:]


def _closed_bar_start(now: datetime, tf_min: int) -> str:
    """ISO of the latest bar boundary at/before ``now``: the bar the next plan may first see closed."""
    epoch = int(now.timestamp()) // 60
    return datetime.fromtimestamp((epoch - epoch % tf_min) * 60, tz=timezone.utc).isoformat()


def plan_amends(account_id: str, *, now: Optional[datetime] = None,
                candles_fn: Optional[Callable[[str, str], Any]] = None,
                legs: Optional[Mapping[str, Mapping[str, Any]]] = None,
                send: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """One planning pass for the account: watchdog stale amends, then at most one plan per
    ticket per closed bar. Never raises past a per-ticket failure. Returns the log."""
    from src.prop import prop_trail

    now = now or _now()
    out: Dict[str, Any] = {"emitted": [], "log": [], "alerts": []}
    legs = legs if legs is not None else prop_trail._load_legs()
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM prop_tickets WHERE account_id = ? AND status IN (?, ?) ORDER BY created_at ASC",
            (account_id, *PARENT_STATUSES)).fetchall()
        for r in rows:
            try:
                _plan_one(conn, r, account_id, now, legs, candles_fn, out)
            except Exception as exc:  # noqa: BLE001 — one bad ticket never stops the others
                out["alerts"].append(f"{r['ticket_id']}: trail plan failed ({type(exc).__name__})")
                logger.warning("phone_trail: plan failed for %s", r["ticket_id"], exc_info=True)
    finally:
        conn.close()
    for a in out["alerts"]:
        _send(f"📱 trail {account_id}: {a}", send)
    return out


def _plan_one(conn: Any, r: Any, account_id: str, now: datetime, legs: Mapping[str, Mapping[str, Any]],
              candles_fn: Optional[Callable[[str, str], Any]], out: Dict[str, Any]) -> None:
    from src.prop import prop_trail

    tid, raw = r["ticket_id"], r["meta"]
    m = _meta(raw)
    if m.get("test"):
        return
    created = _parse(r["created_at"])
    if created and (now - created) > timedelta(days=MAX_AGE_DAYS):
        return
    leg = legs.get(str(r["strategy"] or ""))
    if not leg:
        return
    tr = _load_trail(m, tid)
    if tr.get("human_hold"):
        return  # a human's stop is on the terminal: never fought; resumes on their {kind: amend} report
    am = dict(tr.get("amend") or {})
    st = am.get("status")
    # Watchdog: a CLAIMED amend with no report means the stop on the terminal is UNKNOWN. That attempt
    # failed; the next one re-checks the terminal against the server's resting stop before any click.
    if st == "claimed":
        ca = _parse(am.get("claimed_at"))
        if ca and (now - ca).total_seconds() > CLAIM_TIMEOUT_S:
            am["status"] = "unreported"
            tr["amend"] = am
            _hist(tr, {**am, "at": now.isoformat()})
            wd_alerts: List[str] = []
            _fail(tr, tid, str(am.get("claimed_at")), "claimed amend got no report", wd_alerts,
                  first=f"amend {am.get('id')} to SL {am.get('sl')} was claimed and never reported; the stop on "
                        "the terminal is UNKNOWN until the next attempt reads it. Retrying with backoff")
            if _cas(conn, tid, raw, {**m, KEY: tr}):
                out["alerts"].extend(wd_alerts)
        return
    if st == "emitted":
        vu = _parse(am.get("valid_until"))
        if vu and vu > now:
            return  # one outstanding per ticket
        am["status"] = "expired"
        tr["amend"] = am
        _hist(tr, {**am, "at": now.isoformat()})
        m2 = {**m, KEY: tr}
        if not _cas(conn, tid, raw, m2):
            return
        raw, m = json.dumps(m2), m2
    tf = str(leg.get("timeframe") or "1h")
    tf_min = prop_trail._TF_MINUTES.get(tf)
    if tf_min is None:
        return
    bo = tr.get("backoff") or {}
    since = _parse(bo.get("since"))
    if since is not None and now < since + timedelta(minutes=tf_min * _backoff_bars(int(bo.get("n") or 1))):
        return  # backing off after a failed attempt: retried once the wait has passed, never dropped
    bar = _closed_bar_start(now, tf_min)
    if tr.get("last_bar") == bar:
        return
    step = _price_step(account_id, r["symbol"])
    resting_sl, resting_tp = _levels(r, tr, step)
    entry, isl, sig = _f(r["entry"]), _f(r["sl"]), _parse(r["signal_time"])
    direction = str(r["direction"] or "").lower()
    if entry is None or isl is None or sig is None:
        out["log"].append({"ticket_id": tid, "why": "ticket has no entry/sl/signal_time"})
        return
    candles = None
    try:
        candles = (candles_fn or prop_trail.default_candles_fn())(str(r["symbol"]), tf)
    except Exception as exc:  # noqa: BLE001
        out["log"].append({"ticket_id": tid, "why": f"candles failed ({type(exc).__name__})"})
        return  # last_bar not advanced: re-tried on the next claim
    plan = prop_trail.plan_trail(leg=leg, direction=direction, entry=entry, initial_sl=isl, signal_time=sig,
                                 resting_sl=resting_sl, candles=candles, now=now, price_step=step)
    tr["last_bar"] = bar
    tr["last_plan"] = {"at": now.isoformat(), "action": plan.action, "why": plan.why, "replay_sl": plan.replay_sl,
                       "bars": plan.bars, "close_lever": plan.close_lever, "mfe_r": plan.detail.get("mfe_r")}
    out["log"].append({"ticket_id": tid, **tr["last_plan"]})
    alerts: List[str] = []
    if plan.close_lever and not tr.get("close_lever_alerted"):
        alerts.append(f"{tid}: declared close lever `{plan.close_lever}` WOULD exit now "
                      f"(open_r={plan.detail.get('open_r')}); a resting-SL amend cannot reproduce a bar-close "
                      "exit, so the phone trail does NOT apply it. Operator decides")
        tr["close_lever_alerted"] = True
    new = None
    if plan.action == "tighten" and plan.sl is not None and candles is not None and len(candles):
        # Through-price guard on the feed's latest price (the phone path has no server-side venue quote):
        # the new stop clears it by max(2 steps, QUOTE_BUFFER_ATR x entry ATR), as prop_trail does on the venue.
        px = float(candles["close"].iloc[-1])
        buf = max(2 * (step or 0.0), QUOTE_BUFFER_ATR * float(plan.detail.get("atr") or 0.0))
        if (direction == "long" and plan.sl <= px - buf) or (direction == "short" and plan.sl >= px + buf):
            new = plan.sl
        else:
            tr["last_plan"]["why"] = f"new stop {plan.sl} within {buf:.4g} of the price {px}: not amended"
    if new is not None:
        seq = int(tr.get("seq") or 0) + 1
        tr["seq"] = seq
        tr["amend"] = {"id": f"{tid}#a{seq}", "status": "emitted", "kind": "sl", "from_sl": resting_sl,
                       "sl": new, "from_tp": resting_tp, "tp": resting_tp, "emitted_at": now.isoformat(),
                       "valid_until": (now + timedelta(seconds=AMEND_VALID_S)).isoformat(),
                       "reason": f"trail: {tf} chandelier replay (bars={plan.bars}, mfe_r={plan.detail.get('mfe_r')})"}
    if _cas(conn, tid, raw, {**m, KEY: tr}):
        out["alerts"].extend(alerts)
        if new is not None:
            out["emitted"].append(tr["amend"]["id"])


def request_tp_amend(account_id: str, ticket_id: str, tp: float, *, reason: str,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """Publish a TP revision for one open phone ticket through the amend slot (for lane
    TP-DOCTRINE's revision verdicts). Refused, never queued, when an amend is outstanding or a
    human's stop is held; the SL is carried unchanged and the phone verifies it."""
    now = now or _now()
    tpf = _f(tp)
    if tpf is None or tpf <= 0:
        raise ValueError("tp must be a positive price")
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        r = conn.execute("SELECT * FROM prop_tickets WHERE ticket_id = ? AND account_id = ?",
                         (ticket_id, account_id)).fetchone()
        if r is None or r["status"] not in PARENT_STATUSES:
            return {"ok": False, "why": "no placed/filled ticket with that id on this account"}
        m = _meta(r["meta"])
        if m.get("test"):
            return {"ok": False, "why": "test ticket"}
        tr = _load_trail(m, ticket_id)
        if tr.get("human_hold"):
            return {"ok": False, "why": f"a human's stop is held: {tr['human_hold']}"}
        if (tr.get("amend") or {}).get("status") in ("emitted", "claimed"):
            return {"ok": False, "why": "an amend is outstanding for this ticket"}
        step = _price_step(account_id, r["symbol"])
        sl, cur_tp = _levels(r, tr, step)
        new_tp = _round_near(tpf, step)
        long_ = str(r["direction"] or "").lower() == "long"
        entry = _f(r["entry"])
        if sl is None or entry is None or (long_ and not new_tp > entry) or (not long_ and not new_tp < entry):
            return {"ok": False, "why": "TP on the wrong side of the entry"}
        seq = int(tr.get("seq") or 0) + 1
        tr["seq"] = seq
        tr["amend"] = {"id": f"{ticket_id}#a{seq}", "status": "emitted", "kind": "tp", "from_sl": sl, "sl": sl,
                       "from_tp": cur_tp, "tp": new_tp, "emitted_at": now.isoformat(),
                       "valid_until": (now + timedelta(seconds=AMEND_VALID_S)).isoformat(),
                       "reason": str(reason or "tp revision")[:200]}
        ok = _cas(conn, ticket_id, r["meta"], {**m, KEY: tr})
        return {"ok": ok, "amend_id": tr["amend"]["id"] if ok else None, "why": "" if ok else "concurrent write"}
    finally:
        conn.close()


def pending_amends(account_id: str, now: Optional[datetime] = None) -> int:
    """How many emitted, still-valid amends the next amend-capable claim would find. Read-only."""
    now = now or _now()
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        rows = conn.execute("SELECT meta FROM prop_tickets WHERE account_id = ? AND status IN (?, ?)",
                            (account_id, *PARENT_STATUSES)).fetchall()
    finally:
        conn.close()
    n = 0
    for r in rows:
        am = (_meta(r["meta"]).get(KEY) or {}).get("amend") or {}
        vu = _parse(am.get("valid_until"))
        if am.get("status") == "emitted" and vu and vu > now:
            n += 1
    return n


def claim_amend(device: Any, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Atomically claim the oldest emitted, still-valid amend for the device's account."""
    from src.prop import phone_executor as pe

    now = now or _now()
    acct = device.account_id
    if pe.kill_switch(acct) == "off":
        return None
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        rows = conn.execute("SELECT * FROM prop_tickets WHERE account_id = ? AND status IN (?, ?) "
                            "ORDER BY created_at ASC", (acct, *PARENT_STATUSES)).fetchall()
        for r in rows:
            m = _meta(r["meta"])
            tr = _load_trail(m, r["ticket_id"])
            am = dict(tr.get("amend") or {})
            vu = _parse(am.get("valid_until"))
            if am.get("status") != "emitted" or vu is None or vu <= now or tr.get("human_hold"):
                continue
            am.update(status="claimed", claimed_at=now.isoformat(), device_id=device.device_id)
            tr["amend"] = am
            if not _cas(conn, r["ticket_id"], r["meta"], {**m, KEY: tr}):
                continue  # lost the race: never act on it
            return {"kind": "amend", "ticket_id": r["ticket_id"], "amend_id": am["id"], "amend_kind": am.get("kind"),
                    "symbol": r["symbol"], "venue_symbol": pe.venue_symbol(acct, r["symbol"]),
                    "direction": r["direction"], "qty": r["qty"], "from_sl": am.get("from_sl"), "sl": am.get("sl"),
                    "from_tp": am.get("from_tp"), "tp": am.get("tp"), "reason": am.get("reason"),
                    "submit": pe.submit_mode(acct, test=False), "claimed_at": now.isoformat()}
        return None
    finally:
        conn.close()


def record_amend_result(device: Any, body: Mapping[str, Any], *, now: Optional[datetime] = None,
                        send: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """The phone's report of one claimed amend. The server re-checks an ``amended`` claim
    against the levels it asked for; anything it cannot verify fails THAT attempt (backoff, then
    retried; ONE red flag after :data:`FLAG_AFTER` in a row). Only ``human_moved`` holds the trail."""
    now = now or _now()
    acct = device.account_id
    tid = str(body.get("ticket_id") or "")
    aid = str(body.get("amend_id") or "")
    result = str(body.get("result") or "")
    if result not in RESULTS:
        raise ValueError("amend_result.result invalid")
    reason = str(body.get("reason") or "")[:300]
    form = body.get("form") if isinstance(body.get("form"), dict) else {}
    from src.utils.json_notes import dump_capped

    conn = prop_journal._connect()
    alerts: List[str] = []
    try:
        prop_journal.ensure_tables(conn)
        r = conn.execute("SELECT * FROM prop_tickets WHERE ticket_id = ? AND account_id = ?", (tid, acct)).fetchone()
        if r is None:
            return {"ok": False, "why": "no such ticket on this account"}
        m = _meta(r["meta"])
        tr = _load_trail(m, tid)
        am = dict(tr.get("amend") or {})
        if am.get("id") != aid or am.get("status") != "claimed":
            return {"ok": False, "why": "amend is not the claimed one"}
        step = _price_step(acct, r["symbol"])
        tol = (step or 0.0) / 2 + 1e-9
        sl_read, tp_read = _f(body.get("sl_read")), _f(body.get("tp_read"))
        if result == "amended":
            sl_ok = sl_read is not None and abs(sl_read - float(am["sl"])) <= tol
            tp_ok = am.get("tp") is None or (tp_read is not None and abs(tp_read - float(am["tp"])) <= tol)
            if not (sl_ok and tp_ok):
                reason = f"phone said amended but read back sl={sl_read} tp={tp_read} (asked sl={am['sl']} tp={am.get('tp')}); " + reason
                result = "mismatch"
        am.update(status=result, reported_at=now.isoformat(), reason_reported=reason, sl_read=sl_read, tp_read=tp_read)
        if form:
            am["form"] = json.loads(dump_capped(form, 12000))
        tr["amend"] = am
        _hist(tr, {k: v for k, v in am.items() if k != "form"})
        at = str(am.get("claimed_at") or now.isoformat())  # backoff runs on the planner's clock
        if result == "amended":
            tr["resting_sl"], tr["resting_tp"] = sl_read, (tp_read if tp_read is not None else tr.get("resting_tp"))
            tr["seen"] = True
            _ok(tr, tid, alerts)
            if not tr.get("first_amend_pinged"):
                tr["first_amend_pinged"] = True
                alerts.append(f"{tid}: SL {am.get('from_sl')} -> {sl_read} amended and READ BACK on the terminal "
                              f"({am.get('reason')})")
        elif result == "dry_amended":
            tr["seen"] = True
            _ok(tr, tid, alerts)
            if not tr.get("dry_pinged"):
                tr["dry_pinged"] = True
                alerts.append(f"{tid}: DRY amend walked to SL {am.get('sl')} and read back in the edit dialog, "
                              "NOT saved (submit dry or app not armed)")
        elif result == "no_position":
            # A miss, never an end: a Positions read can fail transiently. Re-checked with backoff; a position
            # that is really gone is simply never amended (the ticket ages out after MAX_AGE_DAYS).
            if tr.get("seen"):
                _fail(tr, tid, at, "position no longer seen on the terminal", alerts, counter="absent", flag_at=0,
                      first="the position is no longer seen on the terminal (closed, or a transient read); "
                            "the trail re-checks with backoff")
            else:
                _fail(tr, tid, at, f"position never seen on the terminal in {MAX_NO_POSITION} reads",
                      alerts, counter="no_position", flag_at=MAX_NO_POSITION,
                      first="the phone found no matching row in Positions (limit not filled yet, or a layout it "
                            "cannot read; rows dump in the ticket's phone_trail); re-checked with backoff")
        elif result == "refused":
            _fail(tr, tid, at, f"refused before any click: {reason[:120]}", alerts,
                  first=f"amend to SL {am.get('sl')} refused before any click ({reason[:120]}); retried with backoff")
        elif result == "human_moved":
            tr["human_hold"] = f"{_HUMAN_LOCK_PREFIX} from the server's: a human moved it"
            alerts.append(f"{tid}: the terminal shows a stop/target other than the server's ({reason[:120]}); "
                          "the trail will not override a human: HELD for this ticket. Report the move with "
                          "{kind: amend} so the journal holds it and the trail resumes from it")
        else:  # mismatch: the click did not verify. A stop the terminal showed after it is now the truth.
            if sl_read is not None:
                tr["resting_sl"] = sl_read
            if tp_read is not None:
                tr["resting_tp"] = tp_read
            _fail(tr, tid, at, f"amend clicked but not verified: {reason[:120]}", alerts,
                  first=f"amend to SL {am.get('sl')} NOT verified after the click ({reason[:120]}). CHECK the stop "
                        "and target on the terminal; the next attempt re-reads them before any click")
        if not _cas(conn, tid, r["meta"], {**m, KEY: tr}):
            return {"ok": False, "why": "concurrent write; report again"}
    finally:
        conn.close()
    journal = None
    if result == "amended":
        # Best effort: the fill journal moves only when it holds the position as open (a phone fill is
        # journaled "placed"; the trail state above is the authority for the next plan either way).
        try:
            from src.prop.prop_report import ingest_report
            journal = ingest_report({"kind": "amend", "account_id": acct, "ticket_id": tid, "symbol": r["symbol"],
                                     "direction": r["direction"], "sl": sl_read,
                                     **({"tp": tp_read} if tp_read is not None else {}),
                                     "reason": am.get("reason"), "source": "phone_trail"})
        except Exception as exc:  # noqa: BLE001
            journal = {"ok": False, "why": f"{type(exc).__name__}: {str(exc)[:160]}"}
    for a in alerts:
        _send(f"📱 trail {acct}: {a}", send)
    return {"ok": True, "kind": "amend_result", "result": result, "journal": journal}


def note_human_amend(account_id: str, ticket_id: str, sl: Any = None, tp: Any = None) -> bool:
    """A human ``{kind: amend}`` report on a phone ticket: the server's resting levels follow it,
    so the next plan starts from the stop a person set (and never loosens it)."""
    slf, tpf = _f(sl), _f(tp)
    if not ticket_id or (slf is None and tpf is None):
        return False
    conn = prop_journal._connect()
    try:
        prop_journal.ensure_tables(conn)
        r = conn.execute("SELECT * FROM prop_tickets WHERE ticket_id = ? AND account_id = ?",
                         (ticket_id, account_id)).fetchone()
        if r is None:
            return False
        m = _meta(r["meta"])
        tr = _load_trail(m, ticket_id)
        if slf is not None:
            tr["resting_sl"] = slf
        if tpf is not None:
            tr["resting_tp"] = tpf
        tr.pop("human_hold", None)  # the human's move is now on record: the trail resumes from it
        _hist(tr, {"status": "human_amend", "sl": slf, "tp": tpf})
        return _cas(conn, ticket_id, r["meta"], {**m, KEY: tr})
    finally:
        conn.close()


__all__ = ["plan_amends", "claim_amend", "record_amend_result", "request_tp_amend", "pending_amends",
           "note_human_amend", "RESULTS"]
