"""Breakout PHONE executor: the journal reconciled against the terminal's own Positions list (PROP-TRACKING).

WHY (operator, 2026-10-12, verbatim): *"we need to be able to track the prop trades - no upnl isn't a solution
to the issue"*. breakout_2 held two live positions the journal never saw (BREAKOUT-UNRECORDED, journaled by hand
as fills 106/107), and a close on the terminal (SL/TP hit, manual close) wrote nothing back, so a journal row
could stay "open" forever. The phone now reads the terminal's Positions and Closed orders views on every account
read (``tools/phone-executor``: ``positionsRead`` / ``closedRead``) and posts them inside ``account_status``;
this module turns that into journal truth. Pure journal writes through :func:`src.prop.prop_report.ingest_report`
(the one fill chokepoint); no order is placed, cancelled or amended here.

Rules (each one is a way a reconcile can lie, refused):

* **Only a CLEAN read reconciles.** ``positions_state == "read"`` is the phone saying the Positions table was
  found, every row parsed (symbol, side, size), the tab's own count agreed, and an empty list carried positive
  evidence of flat. Anything else (``no_table``, ``unparsed``, ``empty_unconfirmed``, ``not_read``...) changes
  nothing: "we could not look" is never "the position is gone".
* **A journal-open row absent from the venue is CLOSED, never left open** -- but only once it has been absent on
  :data:`MISS_READS` consecutive clean reads at least :data:`MISS_MIN_S` apart (one glitched read must not close a
  live trade), and never within :data:`GRACE_S` of being journaled. The exit comes from the Closed orders read
  (one unused closing-side row for the symbol, size within :data:`QTY_TOL`); with none, or more than one, the row
  is closed with ``exit_price`` null and reason ``closed_unpriced`` and the operator is pinged. A later priced
  close corrects it in place (``prop_journal.insert_fill``'s VELO-CLOSE-RECON branch).
* **A position the terminal shows under a symbol we cannot map freezes every close on that read** (it may be a
  journal row under an unmapped label) and is flagged once; it is never journaled.
* **A venue position with no journal row is JOURNALED, as unconfirmed.** If a ``placed`` working order of the same
  symbol and side exists, the position is that order having filled (``open`` on its ticket). Otherwise it is an
  ``open`` row on a synthetic id (``venue-<account>-<symbol>-<side>-<entry>``, no ticket row, so it can never
  advance an unrelated ticket) with reason ``unconfirmed`` and ONE operator ping.
* **REPORT-ONLY until a live read is observed** (review of #17745): no capture of Breakout's Positions DOM exists
  yet, so ``config/prop_platforms.yaml`` declares ``reconcile: report_only`` for the account. Every decision is
  computed and returned, and ONE alert per decision says what WOULD be written; the journal is untouched. Flipping
  the key to ``write`` is follow-up PI-20261012-GNRBKIGG-0004, after the first clean live read.
* **FILL-IN of an already-closed unpriced row WRITES even while ``report_only``** (operator, 2026-10-12: *"we need
  to verify that the app can fulfill this function"* -- the exits of two Breakout closes are to come off the
  terminal, not from the operator). :func:`fill_unpriced_closes` only adds the venue's exit price + realized P&L
  (``MEASURED``) to a journal row that is ALREADY ``closed`` with ``exit_price`` NULL. It decides nothing about
  whether a trade is open or closed, so it cannot be the false close the report-only mode guards against. It
  demands exactly ONE matching closing-side row (symbol, opposite side, size within :data:`QTY_TOL`, a PARSED time
  not before the row opened, status not cancelled), a CLEAN Closed-orders read, and an unused venue row; any doubt
  writes nothing.
* A closing order is a row's exit only if it was first listed no earlier than the row went missing (minus one read
  gap) and its own time, when it parses, is not before the row opened: a re-entry never takes an old close's price.
* Provenance: venue values are ``MEASURED`` (read off the venue's own terminal); a close with no readable exit is
  ``UNVERIFIED``. P&L is the venue's realized figure or null -- never computed here (fee-blind).
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.runtime.provenance import MEASURED, UNVERIFIED

logger = logging.getLogger(__name__)

MISS_READS = 2        # consecutive clean reads a journal-open row must be absent from before it is closed
MISS_MIN_S = 60       # ...spanning at least this long
GRACE_S = 120         # a row journaled this recently is never closed (the venue list may lag the submit)
QTY_TOL = 0.02        # relative size tolerance when matching a venue position / a closing order to a journal row
_MAX_ROWS = 40
SEEN_SLACK = timedelta(minutes=10)   # a closing order seen up to one account-read gap (5 min) before the row went missing
TZ_SLACK = timedelta(hours=14)       # the terminal's time zone is unmeasured: any UTC offset
PING_REPEAT = timedelta(hours=24)    # a deduplicated alert may repeat after this long if its condition still holds
_LOCK = threading.Lock()
_DEAD = re.compile(r"cancel|reject|expire|fail", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(v: Any, *, signed: bool = True) -> Optional[float]:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v) if (signed or v >= 0) else None


def _side(v: Any) -> Optional[str]:
    s = str(v or "").strip().lower()
    return "long" if s in ("long", "buy") else "short" if s in ("short", "sell") else None


def _norm(s: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def bot_symbol(account_id: str, venue_text: Any) -> Optional[str]:
    """The terminal's symbol text ("ETH", "ETHUSD", "ETH-PERP") -> the bot symbol of a DECLARED phone instrument.
    None when it names none of them (never guessed: an unknown instrument is reported, not journaled)."""
    from src.prop import phone_executor

    s = _norm(venue_text)
    s = re.sub(r"PERP$", "", s)
    if not s:
        return None
    insts = phone_executor.phone_config(account_id).get("instruments") or {}
    for bot, inst in insts.items():
        venue = _norm((inst or {}).get("venue"))
        base = re.sub(r"(USDT|USDC|USD)$", "", venue)
        if s in (_norm(bot), venue, base):
            return str(bot).upper()
    return None


# BREAKOUT-POS-UNPARSED (1b.193 live dump, 2026-10-14): Breakout's Positions Side cell carries NO text (the side is
# drawn), so the phone sends "side_unread": every row parsed except its side. The side may then be taken from the
# journal: the open fills with the same bot symbol and an entry within ENTRY_TOL_FRAC of the terminal's Opening price
# must all be on ONE side, and their summed size must match the row's (QTY_TOL; the venue nets same-side entries). That side is ESTIMATED (the journal's, not the venue's); anything else leaves the read
# un-reconciled. Never guessed from a sign, a colour or a P&L.
# 0.5% ~ entry slippage: the live venue avg 2461.52 vs the journal's 2458.24 is 0.13% (operator screenshot 21:41Z).
# The size match is what keeps a wider band from pairing the wrong fill.
ENTRY_TOL_FRAC = 0.005


def resolve_sides(account_id: str, rows: Any) -> Optional[List[Dict[str, Any]]]:
    """The side_unread rows with each missing side taken from the journal (``side_source: journal``), or None when any
    row cannot be matched to exactly one side."""
    from src.prop import prop_journal, phone_executor
    from src.prop.prop_overview import split_fills

    if not isinstance(rows, list) or not rows or len(rows) > _MAX_ROWS:
        return None
    open_rows = [f for f in split_fills(prop_journal.list_fills(account_id=account_id, limit=500))[0]
                 if f.get("account_id") == account_id]
    insts = phone_executor.phone_config(account_id).get("instruments") or {}
    out = []
    for r in rows:
        if not isinstance(r, dict):
            return None
        if _side(r.get("side")):
            out.append(r)
            continue
        sym, entry = bot_symbol(account_id, r.get("symbol")), _num(r.get("entry"), signed=False)
        if sym is None or not entry:
            return None
        step = _num((insts.get(sym) or {}).get("price_step"), signed=False) or 0.0
        tol = max(2 * step, entry * ENTRY_TOL_FRAC)
        hits = [f for f in open_rows if str(f.get("symbol") or "").upper() == sym
                and _num(f.get("entry_price"), signed=False) is not None and abs(float(f["entry_price"]) - entry) <= tol]
        sides = {_side(f.get("direction")) for f in hits} - {None}
        if len(sides) != 1 or not _qty_close(sum(_num(f.get("qty")) or 0.0 for f in hits), _num(r.get("qty"), signed=False)):
            return None
        out.append({**r, "side": sides.pop(), "side_source": "journal"})
    return out


def clean_positions(account_id: str, rows: Any) -> List[Dict[str, Any]]:
    """Validate the phone's Positions rows. Raises ValueError on a structurally bad row (the report is refused,
    so a half-read list can never reconcile). Unknown instruments keep ``symbol: None`` and their venue text."""
    if not isinstance(rows, list) or len(rows) > _MAX_ROWS:
        raise ValueError("account_status.positions must be a list of at most %d rows" % _MAX_ROWS)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("account_status.positions row must be an object")
        side = _side(r.get("side"))
        qty = _num(r.get("qty"), signed=False)
        if side is None or qty is None or qty <= 0:
            raise ValueError("account_status.positions row needs side and a positive qty")
        venue = re.sub(r"[^A-Za-z0-9 ./-]", "", str(r.get("symbol") or ""))[:24]
        out.append({"symbol": bot_symbol(account_id, venue), "venue_symbol": venue, "direction": side, "qty": qty,
                    "entry": _num(r.get("entry"), signed=False), "mark": _num(r.get("mark"), signed=False),
                    "upnl": _num(r.get("upnl"))})
        if r.get("side_source") in ("journal", "sheet", "row"):
            out[-1]["side_source"] = r["side_source"]
        if r.get("side_source") == "sheet":
            # BREAKOUT-SL-AMEND-CLICK: side / qty / entry read off the row's position sheet (MEASURED). A uPnL the sheet
            # showed without a sign arrives as null (a sign-less loss is never a measured number); the flag says which.
            out[-1]["upnl_signed"] = r.get("upnl_signed") is True
    return out


def clean_closed(account_id: str, rows: Any) -> List[Dict[str, Any]]:
    """Validate the phone's Closed orders rows (tolerant: a row that cannot be used is dropped, not fatal)."""
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows[:_MAX_ROWS]:
        if not isinstance(r, dict):
            continue
        side = _side(r.get("side"))
        sym = bot_symbol(account_id, r.get("symbol"))
        if side is None or sym is None:
            continue
        out.append({"symbol": sym, "side": side, "qty": _num(r.get("qty"), signed=False),
                    "price": _num(r.get("price"), signed=False),
                    "pnl": _num(r.get("pnl")) if r.get("pnl") is not None else _num(r.get("netpnl")),   # the venue's own realized figure, never computed
                    "type": str(r.get("type") or "")[:30], "status": str(r.get("status") or "")[:20],
                    # "Long (Reduce)" / "Short (Reduce)" = a closing order (None when the page did not say)
                    "reduce": r["reduce"] if isinstance(r.get("reduce"), bool) else None,
                    "time": str(r.get("time") or "")[:40]})
    return out


def account_unrealized(positions: List[Dict[str, Any]]) -> Optional[float]:
    """The account's unrealized P&L from a CLEAN Positions read: the venue's own per-position figures summed, 0.0
    when the venue says flat, None when any position carries no figure (a partial sum is not the account's)."""
    if not positions:
        return 0.0
    vals = [p.get("upnl") for p in positions]
    if any(v is None for v in vals):
        return None
    return round(sum(vals), 2)


# ── state (debounce + used closing orders), one small file per account beside the phone diag files ─────────────
def _state_path(account_id: str) -> Path:
    from src.prop import phone_executor

    return phone_executor._diag_path(account_id).with_name(
        f"prop_phone_recon_{re.sub(r'[^a-z0-9_]', '', account_id)}.json")


def _load_state(account_id: str) -> Dict[str, Any]:
    try:
        st = json.loads(_state_path(account_id).read_text())
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(account_id: str, st: Dict[str, Any]) -> None:
    path = _state_path(account_id)
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(path)
    except OSError:  # allow-silent: logged; the debounce restarts (fails toward NOT closing), never a false close
        logger.warning("phone_reconcile: state write failed for %s", account_id, exc_info=True)


def _row_id(f: Dict[str, Any]) -> str:
    return str(f.get("id"))


def _close_sig(c: Dict[str, Any]) -> str:
    return f"{c['symbol']}|{c['side']}|{c.get('qty')}|{c.get('price')}|{c.get('time')}"


def _qty_close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= QTY_TOL * max(abs(a), abs(b))


def _parse_venue_time(s: Any) -> Optional[datetime]:
    """The terminal's own time text ("2026-10-12 12:01:05"); its zone is UNMEASURED, so a naive value is read as UTC
    and only used with :data:`TZ_SLACK` either side."""
    m = re.search(r"(\d{4})[-/.](\d{2})[-/.](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", str(s or ""))
    if not m:
        return None
    y, mo, d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc)
    except ValueError:
        return None


def _pick_exit(row: Dict[str, Any], closed: List[Dict[str, Any]], used: List[str],
               seen: Dict[str, str], first_absent: Optional[datetime]) -> tuple:
    """-> (closing order or None, n candidates). The closing side is the opposite of the position.

    A closing order is this row's exit only if it could not belong to an EARLIER trade (review of #17745: a re-entry
    could take an old close's price, labelled MEASURED):
      * it was first SEEN no earlier than one read-gap before the row went missing (a close already listed while the
        position was still on the venue is someone else's), and
      * when its own time text parses, it is not before the row opened (minus :data:`TZ_SLACK`, the terminal's zone
        being unmeasured).
    Anything that fails a bound is not a candidate; with no candidate the close is journaled unpriced, never guessed."""
    want = "short" if _side(row.get("direction")) == "long" else "long"
    opened = _parse(row.get("opened_at")) or _parse(row.get("reported_at"))

    def ok(c: Dict[str, Any]) -> bool:
        sig = _close_sig(c)
        if sig in used:
            return False
        fs = _parse(seen.get(sig))
        if first_absent is not None and fs is not None and fs < first_absent - SEEN_SLACK:
            return False
        vt = _parse_venue_time(c.get("time"))
        if vt is not None and opened is not None and vt < opened - TZ_SLACK:
            return False
        return True

    cands = [c for c in closed if c["symbol"] == str(row.get("symbol") or "").upper() and c["side"] == want
             and c.get("price") is not None and _is_exit_row(c)
             and _qty_close(c.get("qty"), _num(row.get("qty"))) and ok(c)]
    return (cands[0] if len(cands) == 1 else None), len(cands)


def _is_exit_row(c: Dict[str, Any]) -> bool:
    """A Closed-orders row that can be a position's EXIT: not a cancelled/rejected one, FILLED when the page says a
    status (a bracket leaves both its TP and its SL listed, one Filled and one Cancelled), and a reduce order when the
    page says so (the plain "Long Limit" row is an ENTRY, never an exit)."""
    st = c.get("status") or ""
    if _DEAD.search(st) or (st and not re.search(r"fill", st, re.I)):
        return False
    return c.get("reduce") is not False


def _close_reason(row: Dict[str, Any], c: Optional[Dict[str, Any]]) -> str:
    if c is None:
        return "closed_unpriced"
    typ = (c.get("type") or "").lower()
    if re.search(r"take|tp\b|profit", typ):
        return "tp"
    if re.search(r"stop|sl\b|loss", typ):
        return "sl"
    px = c["price"]
    for lvl, name in ((_num(row.get("tp")), "tp"), (_num(row.get("sl")), "sl")):
        if lvl and abs(px - lvl) <= 0.0025 * lvl:
            return name
    return "venue_close"


def _ping(msg: str, send=None) -> None:
    from src.prop import phone_executor

    phone_executor._ping(msg, send)


def reconcile_mode(account_id: str) -> str:
    """``write`` or ``report_only`` from ``config/prop_platforms.yaml`` ``phone_accounts.<acct>.reconcile``.

    Declared in YAML, never an env flag: review of #17745 asked that auto-close and auto-open stay REPORT-ONLY
    until one clean live Positions read of Breakout's terminal is observed (no capture of its DOM exists yet).
    An absent or unknown value reads as ``report_only`` -- the mode that writes nothing -- and the account's
    YAML states it explicitly; flipping it to ``write`` is the follow-up PI-20261012-GNRBKIGG-0004."""
    from src.prop import phone_executor

    v = str((phone_executor.phone_config(account_id) or {}).get("reconcile") or "").strip().lower()
    return "write" if v == "write" else "report_only"


def _once(st: Dict[str, Any], key: str, now: datetime) -> bool:
    """True when the alert ``key`` has not fired in the last :data:`PING_REPEAT` (and records it)."""
    sent = st.setdefault("pinged", {})
    last = _parse(sent.get(key))
    if last is not None and now - last < PING_REPEAT:
        return False
    sent[key] = now.isoformat()
    return True


def reconcile(account_id: str, positions: List[Dict[str, Any]], closed: List[Dict[str, Any]], *,
              now: Optional[datetime] = None, send=None, mode: Optional[str] = None) -> Dict[str, Any]:
    """Reconcile the account's journal against ONE clean venue Positions read. Returns a summary (also kept in the
    snapshot's raw by the caller). Never raises past a logged warning: the account_status itself is already stored.

    ``report_only`` (the default until the follow-up flips it): every decision is computed exactly as in ``write``
    mode and returned, and ONE alert per decision says what WOULD be written ("would close row N ..."); nothing
    is written to the journal."""
    from src.prop import prop_journal
    from src.prop.prop_overview import split_fills
    from src.prop.prop_report import ingest_report

    now = now or _now()
    mode = mode or reconcile_mode(account_id)
    write = mode == "write"
    out: Dict[str, Any] = {"mode": mode, "closed": [], "closed_unpriced": [], "pending_close": [], "opened": [],
                           "unconfirmed": [], "unmapped": [], "matched": 0}
    with _LOCK:
        fills = prop_journal.list_fills(account_id=account_id, limit=500)
        open_rows, closed_rows = split_fills(fills)
        open_rows = [f for f in open_rows if f.get("account_id") == account_id]
        st = _load_state(account_id)
        missing = st.get("missing") if isinstance(st.get("missing"), dict) else {}
        used: List[str] = [u for u in (st.get("used_closes") or []) if isinstance(u, str)]
        seen: Dict[str, str] = st.get("seen_closes") if isinstance(st.get("seen_closes"), dict) else {}
        for c in closed:   # when each closing order was FIRST listed (bounds which trade it can belong to)
            seen.setdefault(_close_sig(c), now.isoformat())

        venue = [p for p in positions if p.get("symbol")]
        out["unmapped"] = [p["venue_symbol"] for p in positions if not p.get("symbol")]
        claimed: set = set()   # venue positions accounted for by journal rows

        def act(report: Dict[str, Any], alert_key: str, alert: str) -> bool:
            """Write the report (write mode) or alert what would be written (report-only). False = write failed."""
            if write:
                try:
                    ingest_report(report)
                except Exception:  # noqa: BLE001  # allow-silent: logged + kept pending; the next read retries
                    logger.warning("phone_reconcile: write failed %s %s", account_id, alert_key, exc_info=True)
                    return False
                if alert:
                    _ping(alert, send)
                return True
            if _once(st, "would:" + alert_key, now):
                _ping(f"📱 phone {account_id} [reconcile REPORT-ONLY, nothing written]: WOULD " + alert_key.split("|", 1)[-1], send)
            return True

        # 1. journal-open rows vs the venue list (grouped by symbol + side: the venue nets same-side entries)
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for f in open_rows:
            groups.setdefault((str(f.get("symbol") or "").upper(), _side(f.get("direction"))), []).append(f)
        new_missing: Dict[str, Any] = {}
        for key, rows in groups.items():
            hit = next((i for i, p in enumerate(venue) if (p["symbol"], p["direction"]) == key and i not in claimed), None)
            if hit is not None:
                claimed.add(hit)
                out["matched"] += len(rows)
                jq = sum(_num(f.get("qty")) or 0.0 for f in rows)
                vq = venue[hit]["qty"]
                if not _qty_close(jq, vq):
                    # a partial close or an add the journal did not see: reported + ONE alert, never auto-resized
                    out.setdefault("qty_mismatch", []).append({"symbol": key[0], "side": key[1],
                                                               "journal_qty": jq, "venue_qty": vq})
                    if _once(st, f"qty:{key[0]}:{key[1]}:{vq:g}", now):
                        _ping(f"📱 phone {account_id}: {key[0]} {key[1]} size differs: journal {jq:g} vs terminal "
                              f"{vq:g} (partial close or unjournaled add?). CHECK; nothing was resized.", send)
                continue
            for f in rows:
                rid = _row_id(f)
                if out["unmapped"]:
                    # the venue shows a position we could not name: it may BE this row under a label we do not
                    # map, so nothing is closed on this read (the absence count is held, not advanced)
                    if rid in missing:
                        new_missing[rid] = missing[rid]
                    out["pending_close"].append(rid)
                    continue
                opened = _parse(f.get("opened_at")) or _parse(f.get("reported_at"))
                if opened is not None and (now - opened).total_seconds() < GRACE_S:
                    continue
                m = missing.get(rid) or {"first": now.isoformat(), "n": 0}
                m["n"] = int(m.get("n", 0)) + 1
                first = _parse(m.get("first")) or now
                if m["n"] < MISS_READS or (now - first).total_seconds() < MISS_MIN_S:
                    new_missing[rid] = m
                    out["pending_close"].append(rid)
                    continue
                if not (f.get("ticket_id") or f.get("external_order_id")):
                    # split_fills keys a close on ticket/external id; a row with neither cannot be closed by any
                    # report. Kept visible, flagged once, never silently left looking open-and-fine.
                    new_missing[rid] = m
                    if _once(st, f"noid:{rid}", now):
                        _ping(f"📱 phone {account_id}: journal row {rid} ({f.get('symbol')} {f.get('direction')}) is "
                              f"not on the terminal's Positions list but has no ticket/order id to close it under. "
                              f"CHECK and report the close.", send)
                    continue
                c, n = _pick_exit(f, closed, used, seen, first)
                reason = _close_reason(f, c)
                report = {
                    "kind": "fill", "status": "closed", "account_id": account_id,
                    "ticket_id": f.get("ticket_id"), "external_order_id": f.get("external_order_id"),
                    "symbol": f.get("symbol"), "direction": f.get("direction"), "qty": f.get("qty"),
                    "entry_price": f.get("entry_price"), "sl": f.get("sl"), "tp": f.get("tp"),
                    "exit_price": c["price"] if c else None, "pnl": c.get("pnl") if c else None,
                    "closed_at": now.isoformat(), "reason": reason,
                    "source": "phone_reconcile", "provenance": MEASURED if c else UNVERIFIED,
                    "reconcile": {"absent_reads": m["n"], "first_absent": m["first"], "exit_candidates": n,
                                  "closing_order": c},
                }
                what = (f"close|close journal row {rid} ({f.get('symbol')} {f.get('direction')}) at "
                        + (f"{c['price']:g} ({reason})" if c else f"NO exit price ({n} matching closing orders read)"))
                alert = "" if c else (f"📱 phone {account_id}: {f.get('symbol')} {f.get('direction')} (journal row {rid}) "
                                      f"is no longer on the terminal's Positions list; journaled CLOSED without an exit "
                                      f"price ({n} matching closing orders read). Report the exit if you can see it.")
                if not act(report, what, alert):
                    new_missing[rid] = m
                    continue
                if not write:
                    new_missing[rid] = m   # nothing written: the row stays open, and stays counted as missing
                if c:
                    if write:
                        used.append(_close_sig(c))
                    out["closed"].append({"id": rid, "exit": c["price"], "reason": reason})
                else:
                    out["closed_unpriced"].append(rid)

        # 2. venue positions with no journal row
        placed = [f for f in fills if str(f.get("status") or "").lower() == "placed" and f.get("ticket_id")
                  and not any(o.get("ticket_id") == f.get("ticket_id") for o in open_rows + closed_rows)]
        synth = st.get("synthetic") if isinstance(st.get("synthetic"), dict) else {}
        live_keys = set()
        for i, p in enumerate(venue):
            pkey = f"{p['symbol']}|{p['direction']}"
            live_keys.add(pkey)
            if i in claimed:
                continue
            w = next((f for f in placed if (str(f.get("symbol") or "").upper(), _side(f.get("direction")))
                      == (p["symbol"], p["direction"])), None)
            base = {"kind": "fill", "status": "open", "account_id": account_id, "symbol": p["symbol"],
                    "direction": p["direction"], "qty": p["qty"], "entry_price": p.get("entry"),
                    "opened_at": now.isoformat(), "source": "phone_reconcile", "provenance": MEASURED,
                    "venue_position": p}
            if w is not None:
                rep = {**base, "ticket_id": w["ticket_id"], "sl": w.get("sl"), "tp": w.get("tp"),
                       "reason": "filled: the placed working order is now on the terminal's Positions list"}
                bucket, what, alert = "opened", f"open|journal ticket {w['ticket_id']} as OPEN (its working order filled)", ""
            else:
                # one synthetic id per POSITION LIFETIME (review of #17745): it is minted when the position first
                # appears unmatched and dropped when it leaves the venue list, so a later position with the same
                # entry is a new row, and an already-journaled one is never re-alerted on every read
                sid = synth.get(pkey) or (f"venue-{account_id}-{p['symbol']}-{p['direction']}-"
                                          f"{now.strftime('%Y%m%dT%H%M')}")
                synth[pkey] = sid
                rep = {**base, "ticket_id": sid,
                       "reason": "unconfirmed: on the terminal's Positions list with no journal row"}
                bucket = "unconfirmed"
                what = (f"open|journal {p['symbol']} {p['direction']} qty {p['qty']:g} (entry {p.get('entry')}) as an "
                        f"UNCONFIRMED open row {sid}")
                alert = (f"📱 phone {account_id}: {p['symbol']} {p['direction']} qty {p['qty']:g} is OPEN on the terminal "
                         f"with no journal row; journaled as UNCONFIRMED (entry {p.get('entry')}). CHECK it is ours."
                         if _once(st, f"unconf:{sid}", now) else "")
            if not act(rep, what, alert):
                continue
            if w is not None and write:
                placed.remove(w)
            out[bucket].append(f"{p['symbol']} {p['direction']}")
        st["synthetic"] = {k: v for k, v in synth.items() if k in live_keys}
        for vs in out["unmapped"]:
            if _once(st, f"unmapped:{vs}", now):
                _ping(f"📱 phone {account_id}: the terminal shows a position in '{vs}', which is not a declared phone "
                      f"instrument; NOT journaled. CHECK the terminal.", send)

        st["missing"] = new_missing
        st["used_closes"] = used[-200:]   # insertion order = recency
        st["seen_closes"] = dict(sorted(seen.items(), key=lambda kv: kv[1])[-500:])
        st["last_read"] = now.isoformat()
        _save_state(account_id, st)
    return out


def fill_unpriced_closes(account_id: str, closed: List[Dict[str, Any]], *, now: Optional[datetime] = None,
                         send=None) -> Dict[str, Any]:
    """Fill the exit price + realized P&L of journal rows ALREADY closed without an exit from a CLEAN Closed orders
    read (``closed_orders`` only reaches here when the phone's Closed read was ``read``). See the module doc: this
    writes regardless of ``reconcile`` mode because it never decides open-vs-closed. Returns a summary; never raises
    past a logged warning (the account_status is already stored)."""
    from src.prop import prop_journal
    from src.prop.prop_report import ingest_report

    now = now or _now()
    out: Dict[str, Any] = {"filled": [], "ambiguous": [], "no_match": []}
    if not closed:
        return out
    with _LOCK:
        fills = prop_journal.list_fills(account_id=account_id, limit=500)
        from src.prop.fill_inference import is_inferred
        # rows closed WITHOUT an exit, plus closes the server only INFERRED from candles (ESTIMATED exit = the SL/TP
        # level): the terminal's own Filled closing order overrides those with the MEASURED exit and realized P&L
        rows = [f for f in fills if f.get("account_id") == account_id and str(f.get("status") or "").lower() == "closed"
                and (f.get("exit_price") is None or is_inferred(f)) and f.get("ticket_id")]
        st = _load_state(account_id)
        used: List[str] = [u for u in (st.get("used_closes") or []) if isinstance(u, str)]
        for f in rows:
            rid = _row_id(f)
            want = "short" if _side(f.get("direction")) == "long" else "long"
            jq = _num(f.get("qty"))
            opened = _parse(f.get("opened_at")) or _parse(f.get("reported_at"))

            def ok(c: Dict[str, Any]) -> bool:
                vt = _parse_venue_time(c.get("time"))
                return (_close_sig(c) not in used and c.get("price") is not None and c.get("qty") is not None
                        and jq is not None and _qty_close(c.get("qty"), jq) and vt is not None
                        and opened is not None and vt >= opened - TZ_SLACK and vt <= now + TZ_SLACK)

            cands = [c for c in closed if c["symbol"] == str(f.get("symbol") or "").upper() and c["side"] == want
                     and _is_exit_row(c) and ok(c)]
            if len(cands) != 1:
                (out["ambiguous"] if cands else out["no_match"]).append(rid)
                if cands and _once(st, f"fillin_ambig:{rid}", now):
                    _ping(f"📱 phone {account_id}: {len(cands)} Closed-orders rows could be the exit of journal row "
                          f"{rid} ({f.get('symbol')} {f.get('direction')}); NOT filled in. CHECK.", send)
                continue
            c = cands[0]
            report = {
                "kind": "fill", "status": "closed", "account_id": account_id, "ticket_id": f.get("ticket_id"),
                "external_order_id": f.get("external_order_id"), "symbol": f.get("symbol"),
                "direction": f.get("direction"), "qty": f.get("qty"), "entry_price": f.get("entry_price"),
                "sl": f.get("sl"), "tp": f.get("tp"), "exit_price": c["price"], "pnl": c.get("pnl"),
                "opened_at": f.get("opened_at"),
                "closed_at": f.get("closed_at"),   # kept: the terminal's time zone is unmeasured, its text is in raw
                "reason": _close_reason(f, c), "source": "phone_reconcile_fill_in", "provenance": MEASURED,
                "reconcile": {"fills_in_row": rid, "closing_order": c,
                              "note": "exit + realized P&L read off the terminal's Closed orders; closed_at kept"},
            }
            try:
                ingest_report(report)
            except Exception:  # noqa: BLE001  # allow-silent: logged; the next clean read retries
                logger.warning("phone_reconcile: fill-in failed %s row %s", account_id, rid, exc_info=True)
                continue
            used.append(_close_sig(c))
            out["filled"].append({"id": rid, "exit": c["price"], "pnl": c.get("pnl")})
            _ping(f"📱 phone {account_id}: filled the exit of {f.get('symbol')} {f.get('direction')} (journal row "
                  f"{rid}) from the terminal's Closed orders: exit {c['price']:g}"
                  + (f", realized P&L {c['pnl']:g}" if c.get("pnl") is not None else "") + " (MEASURED).", send)
        st["used_closes"] = used[-200:]
        _save_state(account_id, st)
    return out
