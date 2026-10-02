"""DXtrade adapter — Breakout's DXtrade white-label ("Breakout Terminal").

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.3.

Slice 1 is the READ path only: ``login``, ``read_account``, ``read_positions``,
``read_orders``. The order-control methods inherit the base class's
``NotImplementedError``. Nothing in this module clicks an order control; the
only clicks are the login button and the Positions / Orders *view tabs*,
which change what is displayed and nothing else.

**Instrument specs (W6-PROP-E1, re-scoped 2026-09-28) are read PASSIVELY off
the network, never off the UI.** Three rounds of scraping an accessible-name
info panel and scoping the parse to it (search box, panel click, label/value
scan) never converged — each fix produced a new way for the wrong symbol's
numbers to be read as the requested one's. ``start_response_capture`` instead
registers a read-only ``page.on("response", ...)`` hook (attached before
login, so it also sees whatever the login/account/positions/orders flow
itself triggers) and records every same-origin JSON response body; nothing
is clicked, filled or navigated for this path. :func:`extract_instrument_specs_from_responses`
then walks the recorded bodies for objects whose symbol-like key EXACTLY
equals (case-insensitive, trimmed — never a substring: ``BTCUSDT`` does not
satisfy ``BTCUSD``, and an object matching two DIFFERENT requested symbols
across its symbol-like keys is ambiguous and skipped) one of the requested
symbols, and prints only the ANCHORED, whole-key allowlist of spec-shaped
field names (never a substring rule — round 4 of review found "tick" also
matching "ticketNumber" and "lot" also matching "lots"), under the key name
as served. An object that also carries a position/order-shaped key
(``positionId``, ``quantity``, …) is never read as a spec at all — that is
trade data, never a spec, however spec-shaped one of its other fields
looks. A symbol that appears in two matching objects with a DIFFERING value
for the same field reports ``conflict`` for that field rather than either
number — a wrong number is worse than none; non-finite values (``NaN``/
``inf``) are dropped before comparison so a repeated ``NaN`` is never read
as a conflict. A response with no matching object contributes only a
discovery line (its redacted origin+path and the set of SAFE-LOOKING
top-level key names, never a value, never a sensitive or id-shaped key) so
selectors can be extended from the public log. The response capture itself
fails CLOSED on an unconfirmed page origin, reads a body only when its
content-type says JSON, and caps how much of one body it keeps.

Every DXtrade selector lives in :data:`SELECTORS` so the order-placing slice
reuses them. Provenance of each selector:

- ``login_*``, ``twofa_*``, ``password_expired_form``: **MEASURED** — the
  login markup Breakout serves (``https://wss.breakoutprop.com/``, curl,
  2026-09-27), saved as ``tests/fixtures/prop_dxtrade/login_page.html.txt``.
- Post-login LABELS and COLUMN HEADERS: **MEASURED from the served i18n
  dictionary** — the ``var dictionary={...}`` the terminal page embeds
  (``https://wss.breakoutprop.com/``, curl, 2026-09-27, 5,571 keys; e.g.
  ``metric.name.short.cashBalance = Balance``, ``metric.name.short.openPl =
  P&L``, ``metric.name.short.dayClosedPl = Day RPL``,
  ``position.column.name.quantity = Position Volume``). That is the text the
  UI renders, not the DOM it renders it into.
- Post-login DOM: **NOT MEASURED**. Run 36329607152 (issue #13239) printed
  ``login: ok`` and parsed nothing. The first cut of ``classify_login_state``
  returned ``logged_in`` whenever no login/2FA/CAPTCHA element was visible, so
  that ``login: ok`` did not prove a logged-in terminal. ``logged_in`` now
  needs a POSITIVE marker (:data:`TERMINAL_MARKERS` in the page text), and an
  unparsed read prints :func:`render_structure` — a redacted structural dump
  (labels, header rows, ancestor classes; never cookies, storage or input
  values) — so the next fix is made against the real layout.

No anti-detection of any kind: default headless Chromium, real typing via
``fill``, no stealth plugins, no fingerprint changes, no fake jitter
(operator boundary, 2026-09-27).

The parsing helpers (``classify_login_state``, ``parse_account_metrics``,
``parse_number``, ``positions_from_tables``, ``orders_from_tables``) are pure
functions over plain data so they are unit-tested without a browser.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from src.prop.platform.base import (
    AccountSnapshot,
    BracketSpec,
    FeasibilityError,
    PlaceAttempt,
    Position,
    PropPlatformAdapter,
    WorkingOrder,
)

SELECTORS: Dict[str, str] = {
    # ── login page (MEASURED 2026-09-27) ──────────────────────────────────
    "login_form": "form.loginForm-main",
    "login_username": "form.loginForm-main input#username",
    "login_password": "form.loginForm-main input#password",
    "login_submit": "form.loginForm-main button#submitLogin",
    "login_error": "form.loginForm-main [data-test-id=login_error]",
    "twofa_form": "form#totpSecure",
    "twofa_code": "form#totpSecure input#securityCode",
    "twofa_backup_form": "form#totpBackup",
    "password_expired_form": "form#changePasswordForm",
    # ── bot challenges / CAPTCHA (generic, platform-independent markers) ──
    "captcha_frame": ("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], "
                      "iframe[src*='challenges.cloudflare.com'], .cf-turnstile, "
                      ".g-recaptcha, .h-captcha"),
    # ── post-login view tabs (NOT MEASURED: matched by accessible name) ──
    "tab_positions": "Positions",
    "tab_orders": "Orders",
    # Bottom-panel history tabs (operator screenshot 2026-09-29: Positions |
    # Orders | Order History | Trade History). Read by dump-tables only, as
    # observation, e.g. to journal a close the operator made by hand.
    "tab_order_history": "Order History",
    "tab_trade_history": "Trade History",
}

# Text markers of an interstitial bot challenge (Cloudflare and similar).
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "verify you are human",
    "attention required",
    "cf-chl",
    "challenge-platform",
    "enable javascript and cookies to continue",
)

# Text that only a logged-in terminal renders. ``logged_in`` requires one of
# these; "no login form visible" alone is not proof (run 36329607152).
TERMINAL_MARKERS = (
    "account metrics", "free margin", "used margin", "available funds",
    "position volume", "day rpl", "day realized pl",
)
# Weaker markers: count only when TWO of them appear together.
_TERMINAL_WEAK_MARKERS = ("balance", "equity", "positions", "orders", "p&l")

# Account-metric labels. MEASURED from the served i18n dictionary (see module
# docstring); the key each one comes from is noted. Most specific first.
ACCOUNT_LABELS: Dict[str, Sequence[str]] = {
    # metric.name.short.cashBalance / multicurrency ... cashBalance
    "balance": ("Balance", "Total Balance"),
    # metric.name.short.equity / netLiquidation / stock ... equity(long)
    "equity": ("Equity", "Net Liquidation", "Net Value"),
    # metric.name.long.openPl / short.openPl / dayOpenPl
    "unrealized": ("Unrealized P&L", "Open P&L", "Open P/L", "P&L", "Day UPL", "Day Unrealized PL"),
    # metric.name.short.dayClosedPl / long.dayClosedPl
    "realized_today": ("Day RPL", "Day Realized PL", "Day Closed P&L", "Day closed P/L"),
    # metric.name.margin.used / short.initialMargin(.otc)
    "margin_used": ("Used Margin", "Initial Margin", "IM", "Margin"),
    # metric.name.short.availableFunds (= "Free Margin") / multiasset.availableFunds
    "available": ("Free Margin", "Available Funds", "Available"),
}


# ── pure helpers ─────────────────────────────────────────────────────────


def parse_number(text: Optional[str]) -> Optional[float]:
    """``"$4,724.50"`` → 4724.5; ``"(12.30)"``/``"−12.30"`` → -12.3; junk → None."""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    t = t.replace("\u2212", "-").replace(",", "").replace(" ", "").replace("\u00a0", "")
    t = re.sub(r"^(USD|USDT|EUR|GBP)", "", t)
    t = re.sub(r"(USD|USDT|EUR|GBP)$", "", t)
    t = re.sub(r"[$€£]", "", t)
    m = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    v = float(m.group(2))
    if m.group(1) == "-":
        neg = not neg
    return -v if neg else v


def classify_login_state(visible: Mapping[str, bool], page_text: str = "", title: str = "") -> str:
    """Classify the page after navigation or login submit.

    ``visible`` maps SELECTORS keys to whether that element is visible.
    Returns one of: ``challenge``, ``captcha``, ``2fa``, ``password_expired``,
    ``login_error``, ``login_form``, ``logged_in``, ``unknown``.
    Order matters: a challenge beats everything, and a visible login form is
    never read as logged in. ``logged_in`` needs a POSITIVE terminal marker;
    a page that is none of the known states is ``unknown``, never logged in.
    """
    low = f"{title}\n{page_text}".lower()
    if any(m in low for m in _CHALLENGE_MARKERS):
        return "challenge"
    if visible.get("captcha_frame"):
        return "captcha"
    if visible.get("twofa_form") or visible.get("twofa_backup_form"):
        return "2fa"
    if visible.get("password_expired_form"):
        return "password_expired"
    if visible.get("login_error"):
        return "login_error"
    if visible.get("login_form"):
        return "login_form"
    if looks_like_terminal(page_text):
        return "logged_in"
    return "unknown"


def looks_like_terminal(page_text: str) -> bool:
    """True when the text carries a marker only a logged-in terminal renders."""
    low = (page_text or "").lower()
    if any(m in low for m in TERMINAL_MARKERS):
        return True
    snap = parse_account_metrics(page_text)
    if snap.balance is not None or snap.equity is not None:
        return True
    return sum(1 for m in _TERMINAL_WEAK_MARKERS if m in low) >= 2


_LEADING_NUMBER_RE = re.compile(
    r"(?:USD|USDT|EUR|GBP)?\s?[(+\-\u2212]?[$€£]?\d[\d,]*(?:\.\d+)?\)?(?:\s?(?:USD|USDT|EUR|GBP))?")


def _label_value_pairs(lines: List[str], labels: Iterable[str]) -> Optional[str]:
    """Value text for the first label found: ``Label  1,234.00`` on one line,
    ``Label: 1,234.00``, or the label on its own line followed by the value."""
    for label in labels:
        pat = re.compile(r"^\s*" + re.escape(label) + r"\s*[:：]?\s*(.*)$", re.IGNORECASE)
        for i, line in enumerate(lines):
            m = pat.match(line)
            if not m:
                continue
            rest = m.group(1).strip()
            if rest and parse_number(rest) is not None:
                return rest
            # Adjacent inline elements concatenate in innerText with no
            # separator ("Balance4,724.00Equity4,731.20"): take the leading
            # number only.
            lead = _LEADING_NUMBER_RE.match(rest)
            if lead and parse_number(lead.group(0)) is not None:
                return lead.group(0)
            if not rest:
                for nxt in lines[i + 1:i + 3]:
                    if nxt.strip() and parse_number(nxt.strip()) is not None:
                        return nxt.strip()
    return None


def parse_account_metrics(page_text: str) -> AccountSnapshot:
    """Read labelled account metrics out of the page's visible text."""
    text = page_text or ""
    # Split "…4,724.00Equity4,731.20" before any label that follows a digit.
    all_labels = sorted({lb for lbs in ACCOUNT_LABELS.values() for lb in lbs}, key=len, reverse=True)
    text = re.sub(r"(?<=[\d)])(?=(?:" + "|".join(re.escape(lb) for lb in all_labels) + r"))", "\n", text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    snap = AccountSnapshot()
    for fld, labels in ACCOUNT_LABELS.items():
        raw = _label_value_pairs(lines, labels)
        val = parse_number(raw)
        if val is None:
            snap.unparsed.append(fld)
        else:
            setattr(snap, fld, val)
            m = re.search(r"\b(USD|EUR|GBP|USDT)\b", raw or "")
            if m and not snap.currency:
                snap.currency = m.group(1)
    return snap


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _find_col(headers: Sequence[str], *names: str) -> Optional[int]:
    norm = [_norm(h) for h in headers]
    for name in names:
        n = name.lower()
        for i, h in enumerate(norm):
            if h == n:
                return i
    for name in names:
        n = name.lower()
        for i, h in enumerate(norm):
            if n in h:
                return i
    return None


def _side(text: Optional[str]) -> Optional[str]:
    t = _norm(text or "")
    if t in {"buy", "long", "b"} or t.startswith("buy") or t.startswith("long"):
        return "long"
    if t in {"sell", "short", "s"} or t.startswith("sell") or t.startswith("short"):
        return "short"
    return None


def _cell(row: Sequence[str], idx: Optional[int]) -> Optional[str]:
    if idx is None or idx >= len(row):
        return None
    return row[idx]


# Column headers only one of the two tables carries (exact, case-insensitive),
# from the served dictionary's position.column.* / table.header.*order* keys.
# MEASURED (run 36358563148, issue #13385): Breakout's working-orders table
# carries "Current Price" and "Fill Price" too, so neither may mark a table as
# positions-only. Its headers: Sts, Status, Symbol, Side, Size, Price, Type,
# Stop loss, Take profit, Current Price, Date and Time Modified, Expiration,
# Order ID, Fill Price.
_POSITION_ONLY = {"position id", "position volume", "position qty", "open p&l", "open p/l",
                  "open p/l, acc", "avg fill price", "open price", "entry price",
                  "average price", "open cost"}
_ORDER_ONLY = {"order id", "order type", "limit price", "stop price", "order volume",
               "order size", "left qty", "filled qty", "trigger price", "status", "sts",
               "time in force", "duration", "order group id", "oco/bracket"}

_QTY_NAMES = ("Position Volume", "Position Qty", "Quantity", "Qty", "Size", "Volume",
              "Order Volume", "Order Size", "Amount")
_OPEN_PRICE_NAMES = ("Open Price", "Entry Price", "Average Price", "Avg Fill Price",
                     "Avg Price", "Fill price")


def _has_any(headers: Sequence[str], names: Iterable[str]) -> bool:
    norm = {_norm(h) for h in headers}
    return any(n in norm for n in names)


def _is_positions_table(headers: Sequence[str]) -> bool:
    return (_find_col(headers, "Symbol", "Instrument") is not None
            and _find_col(headers, *_QTY_NAMES) is not None
            and _find_col(headers, *_OPEN_PRICE_NAMES) is not None
            and not _has_any(headers, _ORDER_ONLY))


def _is_orders_table(headers: Sequence[str]) -> bool:
    if _find_col(headers, "Symbol", "Instrument") is None or _has_any(headers, _POSITION_ONLY):
        return False
    return (_has_any(headers, _ORDER_ONLY)
            or (_find_col(headers, "Type", "Order Type") is not None
                and _find_col(headers, "Price", "Limit Price", "Stop Price") is not None))


def positions_from_tables(tables: Sequence[Mapping[str, Any]]) -> Optional[List[Position]]:
    """Positions from extracted tables (``{"headers": [...], "rows": [[...]]}``).

    Returns ``None`` when no table looks like a positions table (we could not
    look), and ``[]`` when one does and it is empty (we looked; none open).
    """
    found = False
    out: List[Position] = []
    for t in tables:
        headers = list(t.get("headers") or [])
        if not _is_positions_table(headers):
            continue
        found = True
        c_sym = _find_col(headers, "Symbol", "Instrument")
        c_side = _find_col(headers, "Side", "Direction", "Type")
        c_qty = _find_col(headers, *_QTY_NAMES)
        c_open = _find_col(headers, *_OPEN_PRICE_NAMES)
        c_sl = _find_col(headers, "Stop Loss", "SL")
        c_tp = _find_col(headers, "Take Profit", "TP")
        c_pnl = _find_col(headers, "Open P&L", "Open P/L", "P&L", "PnL", "Unrealized")
        for row in t.get("rows") or []:
            sym = (_cell(row, c_sym) or "").strip()
            if not sym:
                continue
            qty = parse_number(_cell(row, c_qty))
            side = _side(_cell(row, c_side))
            if side is None and qty is not None:
                side = "short" if qty < 0 else "long"
            out.append(Position(
                symbol=sym, side=side,
                quantity=abs(qty) if qty is not None else None,
                entry_price=parse_number(_cell(row, c_open)),
                stop_loss=parse_number(_cell(row, c_sl)),
                take_profit=parse_number(_cell(row, c_tp)),
                unrealized_pnl=parse_number(_cell(row, c_pnl)),
                raw={h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)},
            ))
    return out if found else None


def orders_from_tables(tables: Sequence[Mapping[str, Any]]) -> Optional[List[WorkingOrder]]:
    """Working orders; same ``None`` vs ``[]`` contract as positions."""
    found = False
    out: List[WorkingOrder] = []
    for t in tables:
        headers = list(t.get("headers") or [])
        if not _is_orders_table(headers):
            continue
        found = True
        c_sym = _find_col(headers, "Symbol", "Instrument")
        c_side = _find_col(headers, "Side", "Direction")
        c_type = _find_col(headers, "Order Type", "Type")
        c_qty = _find_col(headers, "Left Qty", *_QTY_NAMES)
        c_px = _find_col(headers, "Price", "Limit Price", "Stop Price", "Trigger Price")
        c_id = _find_col(headers, "Order ID")
        c_sl = _find_col(headers, "Stop loss", "SL")
        c_tp = _find_col(headers, "Take profit", "TP")
        for row in t.get("rows") or []:
            sym = (_cell(row, c_sym) or "").strip()
            if not sym:
                continue
            qty = parse_number(_cell(row, c_qty))
            oid = (_cell(row, c_id) or "").strip() or None
            out.append(WorkingOrder(
                symbol=sym, side=_side(_cell(row, c_side)),
                order_type=(_cell(row, c_type) or "").strip() or None,
                quantity=abs(qty) if qty is not None else None,
                price=parse_number(_cell(row, c_px)),
                raw={h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)},
                order_id=oid,
                stop_loss=parse_number(_cell(row, c_sl)),
                take_profit=parse_number(_cell(row, c_tp)),
            ))
    return out if found else None


# JS run in the page to extract every HTML table, ARIA grid and DIV grid as
# plain data. Read-only: it walks the DOM and returns text; it does not click
# or type. A "div grid" is found from its header row: a leaf element reading
# "Symbol"/"Instrument", walked up to the first ancestor with >= 3 short-text
# children (the header row); its data rows are later elements with the same
# tag and child count under the nearest ancestor that holds more than one.
# The terminal renders EVERY grid as TWO <table> elements: a header table
# whose own body carries ZERO rows, followed by a header-less table holding
# the (virtualised) rows. MEASURED for the watchlist (dry run #13898, run
# 36507086110: "Symbol/Bid/Ask… rows: 0" beside the tr.instrument rows PR
# #13908 then read directly), and the cause of the blind positions reader
# (live test #13987 filled 0.01 SOLUSD, the operator saw the row under
# Symbol | Side | Size | Open P&L | Take profit | Stop loss | Position ID |
# Fill Price | Current Price | Date and Time, and every read returned 0
# positions). This helper PAIRS a header table that has no rows of its own
# with the first header-less table that follows it in document order,
# before the next headed table, and only when that table is empty or its
# first row has exactly as many cells as there are headers (alignment is
# proven, never assumed, as in #13908). A headed table with its own rows is
# self-contained. Shared by EXTRACT_TABLES_JS and ROW_ACTION_JS so the
# reader and the row controls see the same rows.
_PAIRED_TABLES_HELPER_JS = r"""
  const txt = el => (el.innerText || el.textContent || '').trim();
  const allTables = [...document.querySelectorAll('table')];
  const tinfo = allTables.map(t => ({
    t,
    headers: [...t.querySelectorAll('thead th, tr:first-child th')].filter(h => h.closest('table') === t).map(txt),
    trs: [...t.querySelectorAll('tr')].filter(r => r.closest('table') === t && r.querySelector('td')),
  }));
  const paired = [];
  tinfo.forEach((h, i) => {
    if (!h.headers.length) return;
    let trs = h.trs, body = null, unpaired = 0;
    if (!trs.length) {
      for (let j = i + 1; j < tinfo.length; j++) {
        const b = tinfo[j];
        if (b.headers.length) break;
        if (!b.trs.length || b.trs[0].querySelectorAll('td').length === h.headers.length) { body = b.t; trs = b.trs; break; }
        unpaired += b.trs.length;
      }
    }
    paired.push({t: h.t, headers: h.headers, trs, body, own_rows: h.trs.length, unpaired_body_rows: unpaired});
  });
  const headerless = tinfo.filter(x => !x.headers.length).map(x => x.trs.length);
"""

EXTRACT_TABLES_JS = r"""
() => {
""" + _PAIRED_TABLES_HELPER_JS + r"""
  const out = [];
  const seen = new Set();
  const push = (kind, headers, rows, extra) => {
    const sig = kind === 'divgrid' ? headers.join('|') : null;
    if (sig && seen.has(sig)) return;
    if (sig) seen.add(sig);
    out.push(Object.assign({kind, headers, rows}, extra || {}));
  };
  const ctlDesc = r => [...r.querySelectorAll('button, [role=button], [title], [aria-label]')].slice(0, 8).map(c =>
    (txt(c) || c.getAttribute('title') || c.getAttribute('aria-label') || c.tagName.toLowerCase()).slice(0, 30));
  // The same controls' markup (whitespace folded, digit runs of 5+ masked):
  // live test #14191 read the position row's trio as three text-less
  // buttons, so what they are CALLED is the only way to tell them apart.
  const ctlHtml = r => [...r.querySelectorAll('button, [role=button], [title], [aria-label]')].slice(0, 8).map(c =>
    (c.outerHTML || '').replace(/\s+/g, ' ').replace(/(data-[\w-]*id[\w-]*=")[^"]*(")/gi, '$1#####$2')
      .replace(/\d{5,}/g, '#####').replace(/[0-9a-f]{8,}/gi, '########').slice(0, 220));
  for (const p of paired) {
    const rows = p.trs.map(r => [...r.querySelectorAll('td')].filter(c => c.closest('table') === r.closest('table')).map(txt));
    push('table', p.headers, rows, {paired: !!p.body, own_rows: p.own_rows, unpaired_body_rows: p.unpaired_body_rows,
                                    headerless_tables: headerless,
                                    first_row_controls: p.trs.length ? ctlDesc(p.trs[0]) : [],
                                    first_row_control_html: p.trs.length ? ctlHtml(p.trs[0]) : []});
  }
  for (const g of document.querySelectorAll('[role=grid], [role=treegrid], [role=table]')) {
    const headers = [...g.querySelectorAll('[role=columnheader]')].map(txt);
    const rows = [...g.querySelectorAll('[role=row]')]
      .map(r => [...r.querySelectorAll('[role=gridcell], [role=cell]')].map(txt))
      .filter(r => r.length);
    if (headers.length) push('grid', headers, rows);
  }
  const leaves = [...document.querySelectorAll('body *')].filter(el =>
    el.children.length === 0 && /^(symbol|instrument)$/i.test(txt(el)) &&
    !el.closest('table, [role=grid], [role=treegrid], [role=table]'));
  for (const leaf of leaves) {
    let row = leaf, hdr = null;
    for (let i = 0; i < 5 && row && row.parentElement; i++) {
      row = row.parentElement;
      const kids = [...row.children];
      if (kids.length >= 3 && kids.every(k => txt(k).length <= 40)) { hdr = row; break; }
    }
    if (!hdr) continue;
    const headers = [...hdr.children].map(txt);
    const n = hdr.children.length, tag = hdr.tagName;
    let scope = hdr.parentElement, rows = [];
    for (let i = 0; i < 4 && scope; i++, scope = scope.parentElement) {
      const cands = [...scope.querySelectorAll(tag)].filter(r =>
        r !== hdr && r.children.length === n && !r.contains(hdr) && !hdr.contains(r) &&
        (hdr.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (cands.length) { rows = cands.map(r => [...r.children].map(txt)); break; }
    }
    push('divgrid', headers, rows);
  }
  return out;
}
"""

# JS for the redacted STRUCTURE dump (printed when a read does not parse).
# It returns element SHAPE and visible label text only: tag, id, class, role,
# data-* attribute NAMES (never their values), ancestor chains, and the short
# visible text of the label's row. It never reads cookies, storage, input or
# textarea values, or any attribute value other than class/id/role. It does
# NOT truncate text: truncating before redact_text can split a secret so it no
# longer matches. render_structure redacts first, then truncates (_cap).
STRUCTURE_JS = r"""
(labels) => {
  const txt = el => (el.innerText || el.textContent || '').trim();
  const shape = el => {
    if (!el || !el.tagName) return null;
    const cls = (typeof el.className === 'string' ? el.className : '').trim();
    const data = [...el.attributes].map(a => a.name).filter(n => n.startsWith('data-')).slice(0, 6);
    return [el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
            (cls ? '.' + cls.split(/\s+/).join('.') : ''),
            el.getAttribute('role') || '', data.join(',')].filter(Boolean).join(' ');
  };
  const chain = el => { const c = []; for (let e = el, i = 0; e && e.tagName && i < 6; e = e.parentElement, i++) c.push(shape(e)); return c; };
  const roles = {};
  for (const el of document.querySelectorAll('[role]')) { const r = el.getAttribute('role'); roles[r] = (roles[r] || 0) + 1; }
  const want = new Set(labels.map(l => l.toLowerCase()));
  const hits = [], perLabel = {};
  for (const el of document.querySelectorAll('body *')) {
    if (el.children.length !== 0 || el.closest('input, textarea, script, style')) continue;
    const t = txt(el);
    if (!t || !want.has(t.toLowerCase())) continue;
    const key = t.toLowerCase();
    perLabel[key] = (perLabel[key] || 0) + 1;
    if (perLabel[key] > 2) continue;
    const par = el.parentElement, gp = par && par.parentElement;
    hits.push({label: t, chain: chain(el),
               parent_text: par ? txt(par) : '',
               grandparent_text: gp ? txt(gp) : '',
               next_sibling_text: el.nextElementSibling ? txt(el.nextElementSibling) : '',
               row_cells: par ? [...par.children].map(k => txt(k)).slice(0, 30) : []});
    if (hits.length >= 40) break;
  }
  return {
    title: document.title,
    location: location.origin + location.pathname,
    counts: {tables: document.querySelectorAll('table').length,
             iframes: document.querySelectorAll('iframe').length,
             canvases: document.querySelectorAll('canvas').length,
             elements: document.querySelectorAll('*').length},
    roles, hits,
  };
}
"""

# Labels whose elements the structure dump describes: every account label,
# plus the column headers and tab names the table parsers key on.
STRUCTURE_LABELS: List[str] = sorted({
    *(lb for lbs in ACCOUNT_LABELS.values() for lb in lbs),
    "Symbol", "Instrument", "Side", "Qty", "Quantity", "Position Volume", "Position Qty",
    "Open Price", "Average Price", "Avg Fill Price", "Price", "Order Type", "Type",
    "Positions", "Orders", "Account metrics", "Stop loss", "Take profit",
})

# JS for the PAGE-SHAPE dump printed when login ends ``unknown_page`` or
# ``timeout``: what page did we land on? Title, each form's id/class/action
# path, each input's id/name/type (NEVER its value), and button/link text.
PAGE_SHAPE_JS = r"""
() => {
  // Whitespace only. NO truncation here: a cut before the Python-side
  // redaction can split a secret so redact_text no longer matches it and its
  // prefix leaks. Python redacts FIRST, then truncates (_cap).
  const cut = (s) => String(s || '').trim().replace(/\s+/g, ' ');
  const forms = [...document.querySelectorAll('form')].slice(0, 10).map(f => ({
    id: cut(f.id), cls: cut(typeof f.className === 'string' ? f.className : ''),
    action: cut((f.getAttribute('action') || '').split(/[?#]/)[0]),
    visible: !!(f.offsetWidth || f.offsetHeight || f.getClientRects().length),
  }));
  const inputs = [...document.querySelectorAll('input, select, textarea')].slice(0, 30).map(i => ({
    id: cut(i.id), name: cut(i.getAttribute('name')),
    type: cut(i.getAttribute('type') || i.tagName.toLowerCase()),
  }));
  const buttons = [...document.querySelectorAll('button, [role=button], input[type=submit], a')]
    .map(b => cut(b.innerText || b.getAttribute('aria-label') || '')).filter(Boolean).slice(0, 30);
  const iframes = [...document.querySelectorAll('iframe')].slice(0, 10)
    .map(f => cut((f.getAttribute('src') || '').split(/[?#]/)[0]));
  return {title: cut(document.title), location: location.origin, forms, inputs, buttons, iframes};
}
"""


def render_page_shape(shape: Mapping[str, Any], page_text: str,
                      secrets: Sequence[str] = (), max_lines: int = 40) -> List[str]:
    """Format the redacted page-shape dump as printable lines (pure; tested)."""
    # Redact FIRST, then cap: a cut before redaction can split a secret.
    r = lambda t, n=120: _cap(redact_text(t, *secrets), n)  # noqa: E731
    lines = ["page_shape: BEGIN (redacted: no input values, cookies, storage or tokens)"]
    lines.append(f"page_shape.title: {r(shape.get('title', ''))}")
    lines.append(f"page_shape.location: {r(_strip_url(shape.get('location', '')))}")
    for f in shape.get("forms") or []:
        lines.append(f"page_shape.form: id={r(f.get('id', ''), 40)!r} class={r(f.get('cls', ''), 80)!r} "
                     f"action={r(f.get('action', ''))!r} visible={f.get('visible')}")
    for i in shape.get("inputs") or []:
        lines.append(f"page_shape.input: id={r(i.get('id', ''), 40)!r} name={r(i.get('name', ''), 40)!r} "
                     f"type={r(i.get('type', ''))!r}")
    lines.append(f"page_shape.buttons: {[r(b, 40) for b in shape.get('buttons') or []]}")
    for src in shape.get("iframes") or []:
        lines.append(f"page_shape.iframe: {r(_strip_url(src)) if '://' in src else r(src)}")
    text_lines = [ln.strip() for ln in (page_text or "").splitlines() if ln.strip()]
    lines.append(f"page_shape.text: {len(text_lines)} non-empty lines; first {min(max_lines, len(text_lines))}:")
    for ln in text_lines[:max_lines]:
        lines.append(f"  | {r(ln)}")
    lines.append("page_shape: END")
    return lines


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TOKENISH_RE = re.compile(r"[A-Za-z0-9_\-.=+/]{24,}")
_URL_RE = re.compile(r"((?:https?|wss?)://[^\s/?#'\"]+)([/?#][^\s'\"]*)?", re.IGNORECASE)
# A query string can carry a session id even with no scheme in front of it
# (a bare "host.tld/path?sid=..." — _URL_RE only matches scheme://...).
# Matched and redacted independently of any URL/host detection, since that
# is the narrowest rule that catches the leak without eating normal prose:
# ordinary text essentially never contains a literal "?key=value" run.
_QUERY_STRING_RE = re.compile(r"\?[A-Za-z0-9_]+=[^\s'\"]+")


def _cap(text: str, n: int) -> str:
    """Truncate text that has ALREADY been through redact_text. Never call it
    before redaction: a cut can split a secret so it no longer matches."""
    return text if len(text) <= n else text[:n] + "…"


def redact_text(text: str, *secrets: str) -> str:
    """Strip anything credential-shaped from text bound for a PUBLIC log:
    the given secrets (username, password), e-mail addresses, and any 24+
    character token-like run. Numbers (balances) survive; they are allowed."""
    out = str(text or "")
    for sec in secrets:
        if sec:
            # Case-insensitive: a terminal may render the login upper-cased.
            out = re.sub(re.escape(sec), "<redacted>", out, flags=re.IGNORECASE)
    # URLs keep their origin only: a path or query can carry a session id
    # shorter than the token rule below.
    out = _URL_RE.sub(lambda m: m.group(1) + "/<path>" if m.group(2) else m.group(1), out)
    # A scheme-less "host/path?query" never matched the rule above; strip
    # any leftover query string on its own.
    out = _QUERY_STRING_RE.sub("?<query>", out)
    out = _EMAIL_RE.sub("<email>", out)
    return _TOKENISH_RE.sub("<token>", out)


def render_structure(struct: Mapping[str, Any], frames: Sequence[Mapping[str, Any]],
                     page_text: str, tables: Sequence[Mapping[str, Any]],
                     secrets: Sequence[str] = (), max_lines: int = 120) -> List[str]:
    """Format the redacted structure dump as printable lines (pure; tested).

    ``struct`` is :data:`STRUCTURE_JS`'s result for the main frame, ``frames``
    is ``[{"url": ..., "text_len": ...}]``, ``page_text`` the all-frame text
    and ``tables`` :data:`EXTRACT_TABLES_JS`'s result. Every string passes
    :func:`redact_text`; URLs lose their query and fragment.
    """
    # Redact FIRST, then cap: a cut before redaction can split a secret.
    r = lambda t, n=120: _cap(redact_text(t, *secrets), n)  # noqa: E731
    lines = ["structure: BEGIN (redacted: no cookies, storage, input values or tokens)"]
    lines.append(f"structure.title: {r(struct.get('title', ''))}")
    lines.append(f"structure.location: {r(_strip_url(struct.get('location', '')))}")
    lines.append(f"structure.counts: {struct.get('counts', {})}")
    lines.append(f"structure.roles: {struct.get('roles', {})}")
    for f in frames:
        lines.append(f"structure.frame: {r(_strip_url(f.get('url', '')))} text_len={f.get('text_len')}")
    for t in tables:
        lines.append(f"structure.table[{t.get('kind')}]: headers={[r(h) for h in t.get('headers') or []]} "
                     f"rows={len(t.get('rows') or [])}")
        for row in (t.get("rows") or [])[:5]:
            lines.append(f"structure.table.row: {[r(c, 40) for c in row]}")
    for h in struct.get("hits") or []:
        lines.append(f"structure.label: {r(h.get('label', ''))!r}")
        lines.append(f"  chain: {' < '.join(r(c, 100) for c in h.get('chain') or [])}")
        lines.append(f"  parent_text: {r(h.get('parent_text', ''))!r}")
        lines.append(f"  grandparent_text: {r(h.get('grandparent_text', ''), 160)!r}")
        lines.append(f"  next_sibling_text: {r(h.get('next_sibling_text', ''), 80)!r}")
        lines.append(f"  row_cells: {[r(c, 40) for c in h.get('row_cells') or []]}")
    text_lines = [ln.strip() for ln in (page_text or "").splitlines() if ln.strip()]
    lines.append(f"structure.text: {len(text_lines)} non-empty lines; first {min(max_lines, len(text_lines))}:")
    for ln in text_lines[:max_lines]:
        lines.append(f"  | {r(ln)}")
    lines.append("structure: END")
    return lines


def _strip_url(url: str) -> str:
    """Origin only (scheme://host[:port]): paths and queries never reach a
    public log, since either can carry a session id."""
    m = _URL_RE.match(str(url or ""))
    return m.group(1) if m else re.split(r"[/?#]", str(url or ""), maxsplit=1)[0]


# ── instrument specs via network response sniffing (W6-PROP-E1, re-scoped
# 2026-09-28 after three rounds of UI-panel scraping never converged: each
# fix produced a new way the WRONG symbol's numbers could be read as the
# requested one's — see PR #13416. NOT MEASURED against this terminal: no
# live run has confirmed the terminal's own JSON shape, key names, or
# whether Playwright's response bodies are even readable for it. A symbol
# that never turns up this way returns no fields, never a fabricated
# number, exactly like every other unmeasured path in this module.) ──────

_SYMBOL_KEYS: Sequence[str] = ("symbol", "name", "instrument", "code")

# Explicit, ANCHORED allowlist of spec-shaped key names (whole-key match,
# case-insensitive) — NOT a substring rule. A substring rule ("tick" also
# matching "ticketNumber", "lot" also matching "lots"/"closedLots"/
# "pilotSlot") let a position or order object's own size/id fields be
# printed as if they were instrument specs (round-4 review finding).
# Printed under the key name AS SERVED (whichever spelling matched) — this
# is not a fixed schema, since the terminal's own field names are unmeasured.
_SPEC_FIELD_NAMES = frozenset({
    "contractsize", "contract_size", "contractvalue", "lotsize", "lot_size",
    "lotstep", "lot_step", "minlot", "maxlot", "minquantity", "minqty",
    "min_quantity", "maxquantity", "quantitystep", "qtystep", "volumestep",
    "minvolume", "ticksize", "tick_size", "tickvalue", "pointvalue",
    "point_value", "pipvalue", "multiplier", "contractmultiplier",
    "precision", "priceprecision", "digits", "quantityprecision",
})
# A position/order-shaped key on the SAME object disqualifies it from being
# read as an instrument spec at all — a position/order row commonly ALSO
# carries an allowlisted-looking field (its own size), and that is data
# about a trade, never a spec.
_POSITION_OR_ORDER_KEYS = frozenset({
    "positionid", "orderid", "ticket", "side", "openprice", "opentime",
    "fillprice", "quantity",
})
# Never printed — a matching object's field, or a discovery line's key name.
_SENSITIVE_KEY_RE = re.compile(
    r"(token|auth|session|pass|secret|key|account|login|user|email|id$)", re.IGNORECASE)
# A key name safe to print bare (discovery lines): an identifier shape,
# capped at 40 chars, with no run of 4+ digits (rules out an account
# number or similar spelled as a "key", which a bare `id$` suffix check
# would miss) and not sensitive-looking.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
_DIGIT_RUN_RE = re.compile(r"\d{4,}")
# Safety caps: how many response bodies one capture list holds (independent
# of the discovery-line cap below — a long-lived page could otherwise grow
# this without bound), and how much of one body is ever read/kept.
_MAX_CAPTURED_RESPONSES = 500
_MAX_CAPTURED_BODY_CHARS = 200_000


def _safe_key_name(k: str) -> bool:
    """True if ``k`` is safe to print as a bare key name: an identifier
    shape, no run of 4+ digits, and not sensitive-looking."""
    return bool(_SAFE_KEY_RE.match(k)) and not _DIGIT_RUN_RE.search(k) and not _SENSITIVE_KEY_RE.search(k)


def _is_spec_field(k: str) -> bool:
    """Whole-key, case-insensitive membership in :data:`_SPEC_FIELD_NAMES`
    — never a substring match."""
    return k.strip().lower() in _SPEC_FIELD_NAMES


def _looks_like_position_or_order(obj: Mapping[str, Any]) -> bool:
    return any(str(k).strip().lower() in _POSITION_OR_ORDER_KEYS for k in obj.keys())


class CapturedResponse:
    """One same-origin response :meth:`DXtradeAdapter.start_response_capture`
    recorded. ``body`` is the raw response text; it may not be valid JSON —
    :func:`extract_instrument_specs_from_responses` skips what doesn't parse."""

    __slots__ = ("url", "body")

    def __init__(self, url: str, body: str) -> None:
        self.url = url
        self.body = body


def _origin_and_path(url: str) -> str:
    """``scheme://host/path`` — never the query string, which can carry a
    session id or other sensitive value."""
    try:
        parts = urlsplit(str(url or ""))
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}{parts.path}"
    except Exception:
        pass
    return str(url or "")


def _walk_json_objects(node: Any) -> Iterable[Mapping[str, Any]]:
    """Every ``dict`` anywhere inside a parsed JSON structure, depth-first."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_json_objects(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json_objects(item)


def _matching_symbol(obj: Mapping[str, Any], symbols_upper: Mapping[str, str]) -> Optional[str]:
    """The requested symbol (as originally given, not upper-cased) this
    object's symbol-like key(s) (:data:`_SYMBOL_KEYS`) EXACTLY equal after
    trimming — never a substring match: ``BTCUSDT`` must never satisfy
    ``BTCUSD``. ``None`` if no symbol-like key is present, none matches
    exactly, or the object's symbol-like keys match TWO DIFFERENT requested
    symbols (e.g. ``symbol=BTCUSD``, ``name=ADAUSD``) — that is ambiguous,
    not a match either way, so it is skipped rather than guessed.

    A slash-named value (``ETH/USD``) matches its unslashed request
    (``ETHUSD``) through :func:`canonical_symbol` -- still an EXACT equality
    of canonical forms, never a substring (TRADEIFY-WIRE T4: tradeify_1's
    instrument objects carried lotSize/minVolume yet matched no requested
    symbol in #14993; the operator's screenshots show slash names)."""
    hits = set()
    for key in _SYMBOL_KEYS:
        val = obj.get(key)
        if not isinstance(val, str):
            continue
        hit = symbols_upper.get(val.strip().upper())
        if hit is None:
            hit = symbols_upper.get(canonical_symbol(val) or "\0")
        if hit is not None:
            hits.add(hit)
    return next(iter(hits)) if len(hits) == 1 else None


def extract_instrument_specs_from_responses(
        responses: Sequence[CapturedResponse], symbols: Sequence[str],
        secrets: Sequence[str] = (), max_discovery: int = 30, max_keys: int = 40,
    ) -> Dict[str, Any]:
    """Pure function: walk captured JSON response bodies for objects whose
    symbol-like key EXACTLY equals one of ``symbols`` (unambiguously — see
    :func:`_matching_symbol`), and pull only the ANCHORED allowlist of
    spec-shaped fields (:data:`_SPEC_FIELD_NAMES`, a whole-key match, never
    a substring) out of each match, keyed by the field name AS SERVED. An
    object that also carries a position/order-shaped key
    (:data:`_POSITION_OR_ORDER_KEYS`) is never read as a spec at all — a
    position/order row commonly carries an allowlisted-looking field too
    (its own size), and that is trade data, never a spec. A sensitive-
    looking key (:data:`_SENSITIVE_KEY_RE`) or a non-identifier-shaped one
    is never returned either way. Non-finite values (``NaN``/``inf`` —
    Python's ``json`` module accepts the literal spelling) are dropped
    before comparison or printing, so a repeated ``NaN`` is not read as a
    conflict. A field whose value DIFFERS across matching objects for the
    same symbol reports ``"conflict"`` instead of either number — a wrong
    number is worse than none, and the conflict is sticky (a later object
    matching one of the earlier values does not clear it).

    Returns ``{"specs": {<symbol as given>: {field: value_or_"conflict"}},
    "discovery": [line, ...]}``. A response whose body parses as JSON but
    has no matching object contributes ONE (already redacted) discovery
    line — its origin+path (never the query string, which can carry a
    session id) and the SET of its SAFE-LOOKING top-level key names (never
    a value, never a sensitive or id-shaped key), noting how many were
    suppressed — capped at ``max_discovery`` lines / ``max_keys`` keys
    each. A response that isn't valid JSON at all is silently skipped (no
    discovery line either — there are no "top-level keys" to report).
    """
    symbols_upper = {s.strip().upper(): s for s in symbols}
    specs: Dict[str, Dict[str, Any]] = {s: {} for s in symbols}
    discovery: List[str] = []

    for resp in responses:
        try:
            payload = json.loads(resp.body)
        except Exception:
            continue
        response_matched = False
        for obj in _walk_json_objects(payload):
            sym = _matching_symbol(obj, symbols_upper)
            if sym is None:
                continue
            response_matched = True
            if _looks_like_position_or_order(obj):
                continue
            slot = specs[sym]
            for k, v in obj.items():
                if not _is_spec_field(k) or not _safe_key_name(k):
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                if isinstance(v, float) and not math.isfinite(v):
                    continue
                if k in slot and slot[k] != v:
                    slot[k] = "conflict"
                elif k not in slot:
                    slot[k] = v
        if not response_matched and len(discovery) < max_discovery:
            keys = sorted(payload.keys()) if isinstance(payload, dict) else []
            safe_keys = [k for k in keys if _safe_key_name(k)]
            suppressed = len(keys) - len(safe_keys)
            suffix = f" (+{suppressed} keys suppressed)" if suppressed else ""
            discovery.append(
                f"instrument_discovery: {redact_text(_origin_and_path(resp.url), *secrets)} "
                f"keys={safe_keys[:max_keys]}{suffix}")

    return {"specs": specs, "discovery": discovery}


# ── order entry (step 3, PROP-EXEC 2026-09-28) ─────────────────────────────
#
# ⚠️ THE ORDER-ENTRY DOM IS NOT MEASURED. Nothing here has run against the
# terminal's ticket form. What IS measured (run 36358563148, issue #13385):
# the page renders the text "One-click trading", the chart carries
# "Sell <bid>" / "<ask> Buy" price buttons, the app is React/styled-components
# (sc-* classes), the tables are real DOM, and there are 9 canvases (the chart).
# So the form is PROBABLY DOM, not canvas, but that is an inference.
# ``probe_order_ticket`` is the measurement; ``classify_ticket_surface`` turns
# its result into ``dom`` / ``canvas_ticket`` / ``not_found``.
#
# Safety rules every control below keeps, whatever the DOM turns out to be:
# 1. ONE-CLICK TRADING is a DIAGNOSTIC, not a gate (DECIDED, operator
#    2026-09-28 ~17:50Z, verbatim: "As long we know we're placing trades
#    correctly on the sidebar ticket, we don't need to consider the one click
#    toggle at all - that's only relevant for placing trades on the chart
#    itself"). The toggle only changes what a click on a chart Buy/Sell PRICE
#    button does, and rule 2 means no control here ever presses one. Its
#    state is read and RECORDED (``one_click`` in every result) so a run log
#    shows it, but nothing refuses on it. Until this change anything but a
#    definite ``off`` refused every order control, and the live probe (issue
#    #13711) read ``unknown`` — the toggle is custom-styled — which blocked
#    probe-ticket, watched-click and round-trip-dry outright.
# 2. The chart's Buy/Sell PRICE buttons are never an opener and never a
#    submit: a ticket is opened only by an explicitly named control
#    (:data:`TICKET_OPENER_NAMES`) or a double-click on the watchlist row of
#    the symbol, and the only control that changes the account is the form's
#    own unique ``submit`` button (:data:`FORM_BUTTON_PATTERNS`), found INSIDE
#    the ticket container. That is the sidebar ticket the operator uses.
# 3. Fields are found INSIDE one form container by their visible label, and
#    each must be unique there; an ambiguous or missing field refuses.
# 4. Every field is READ BACK after typing and compared to what was meant —
#    all six: symbol (the form names it, :func:`form_names_symbol`), side and
#    order type (the form's selected control, :func:`verify_form_selection`),
#    quantity, stop loss and take profit (:func:`verify_form_values`; plus
#    price for a limit). A mismatch, or a field that cannot be read back,
#    refuses (and closes the form) before submit is ever reached.
# 5. ``arm=False`` (the default) stops before the final control. Only
#    ``src/prop/prop_executor.py`` in ``live`` mode passes ``arm=True``.
# 6. A click reports that it CLICKED, never that an order exists; existence is
#    the executor's re-read.

# Accessible names that open an order ticket, exact match only. NOT MEASURED:
# the probe prints every button name so this list can be fixed from the log.
TICKET_OPENER_NAMES: Sequence[str] = ("New Order", "New order", "Create Order", "Create order",
                                      "Place Order", "Place order", "Order Entry", "Trade")

# The watchlist symbol SEARCH input, as (placeholder, data-test-id prefix).
# MEASURED 2026-09-30 (PROP-ETH-DOM, instrument-search-dump run 36675129051,
# issue #14551, code 2058a2f1b): the live terminal's watchlist symbol search
# is ONE ``<input type=text placeholder="Symbol..." data-test-id="watchlist_public...">``
# inside a ``multiasset-suggest`` control in the watchlist widget's header
# toolbar. FIND_INSTRUMENT_SEARCH_JS admits an input only when BOTH hold on
# the SAME element -- placeholder equal to the first (case-insensitive,
# trimmed) AND data-test-id starting with the second -- and exactly one such
# input exists, or it refuses (manager review of #14563: either attribute
# alone, or the old bare ``type=search`` fallback, is a guess). The five
# pre-measurement guesses ("search", "find symbol", ...) are gone.
INSTRUMENT_SEARCH_MATCH: Tuple[str, str] = ("symbol...", "watchlist_public")

# Form-field label patterns (anchored, case-insensitive), matched against the
# label text the discovery JS derives for each control.
FORM_FIELD_PATTERNS: Dict[str, str] = {
    "quantity": r"^(quantity|qty|size|volume|amount|lots?)\b",
    "price": r"^(price|limit price|entry price|order price|at price)\b",
    "stop_loss": r"^(stop loss|stop-loss|sl)\b",
    "take_profit": r"^(take profit|take-profit|tp)\b",
}
# Buttons inside the form container. ``submit`` is the ONLY control that
# changes the account; ``close`` dismisses the form.
FORM_BUTTON_PATTERNS: Dict[str, str] = {
    "side_buy": r"^buy$",
    "side_sell": r"^sell$",
    "type_market": r"^market$",
    "type_limit": r"^limit$",
    "submit": r"^(place order|place|submit|confirm order|place buy order|place sell order|buy\s.+|sell\s.+)$",
    "close": r"^(cancel|close|×|✕|x)$",
}

# Finds the order form, tags its controls with data-metis-field / data-metis-btn
# (a DOM attribute only this page load sees; it changes nothing on the
# account), and returns their shape. It NEVER returns an input's value except
# for the four order fields, which are our own typed numbers, not account data.
ORDER_FORM_JS = r"""
(args) => {
  const [fieldPats, buttonPats] = args;
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').trim().replace(/\s+/g, ' ');
  const vis = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const re = o => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, new RegExp(v, 'i')]));
  const FP = re(fieldPats), BP = re(buttonPats);
  document.querySelectorAll('[data-metis-field],[data-metis-btn],[data-metis-form]').forEach(e => {
    e.removeAttribute('data-metis-field'); e.removeAttribute('data-metis-btn'); e.removeAttribute('data-metis-form');
  });
  const labelOf = inp => {
    const a = inp.getAttribute('aria-label'); if (a) return a.trim();
    if (inp.id) { const l = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`); if (l) return txt(l); }
    const wrap = inp.closest('label'); if (wrap) { const t = txt(wrap); if (t) return t; }
    let e = inp;
    for (let i = 0; i < 3 && e.parentElement; i++) {
      e = e.parentElement;
      const t = [...e.childNodes].filter(n => n !== inp && !(n.contains && n.contains(inp)))
        .map(n => (n.nodeType === 3 ? n.textContent : txt(n))).join(' ').trim();
      if (t) return t.split(/\n/)[0].trim();
    }
    return (inp.getAttribute('placeholder') || '').trim();
  };
  // An INPUT's label on the live sidebar (probe #13816) is the nearest
  // earlier sibling with text of the input or one of its ancestors ("Lots x 1
  // SOL", "Stop Loss:"); the parent-text walk would find the SL/TP "Price"
  // mode select first. A sibling that holds another input ends the search at
  // that level (never borrow the previous field's label).
  const prevLabel = inp => {
    let e = inp;
    for (let i = 0; i < 6 && e && e !== document.body; i++, e = e.parentElement) {
      for (let s = e.previousElementSibling; s; s = s.previousElementSibling) {
        if (s.matches('input, select, textarea') || s.querySelector('input, select, textarea')) break;
        const t = txt(s); if (t) return t.split(/\n/)[0].trim();
      }
    }
    return '';
  };
  const labelOfInput = inp => {
    const a = inp.getAttribute('aria-label'); if (a) return a.trim();
    if (inp.id) { const l = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`); if (l) return txt(l); }
    const wrap = inp.closest('label'); if (wrap) { const t = txt(wrap); if (t) return t; }
    return prevLabel(inp) || labelOf(inp);
  };
  const inputs = [...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), [role=spinbutton]')].filter(vis);
  const hits = {};
  for (const inp of inputs) {
    const lab = labelOfInput(inp);
    for (const [k, r] of Object.entries(FP)) if (r.test(lab)) (hits[k] = hits[k] || []).push({el: inp, label: lab});
  }
  // The form container: the smallest ancestor of the quantity input that also
  // holds a stop-loss and a take-profit input.
  let form = null;
  const q = (hits.quantity || [])[0];
  if (q) {
    for (let e = q.el.parentElement; e && e !== document.body; e = e.parentElement) {
      const has = k => (hits[k] || []).some(h => e.contains(h.el));
      if (has('stop_loss') && has('take_profit')) { form = e; break; }
    }
  }
  // On the live sidebar the fields' smallest common container can stop short
  // of the ticket's own symbol input and order-type row (probe #13816: three
  // sibling sections). Widen it to the smallest ancestor that also holds the
  // ticket's BUY, SELL and symbol_input: the same anchor the probe's panel
  // dump uses. Terminals without those test ids keep the fields' container.
  const fieldsForm = form;
  if (form) {
    for (let e = form; e && e !== document.body; e = e.parentElement) {
      if (e.querySelector('[data-test-id=BUY]') && e.querySelector('[data-test-id=SELL]')
          && e.querySelector('[data-test-id=symbol_input]')) { form = e; break; }
      if (!document.querySelector('[data-test-id=BUY]')) break;
    }
  }
  const out = {found: !!form, fields: {}, buttons: {}, ambiguous: [], checkboxes: [],
               canvases_in_page: document.querySelectorAll('canvas').length,
               canvases_in_form: form ? form.querySelectorAll('canvas').length : 0,
               inputs_in_page: inputs.length};
  if (!form) return out;
  form.setAttribute('data-metis-form', '1');
  for (const [k, list] of Object.entries(hits)) {
    const inside = list.filter(h => form.contains(h.el));
    if (inside.length !== 1) { if (inside.length > 1) out.ambiguous.push(k); continue; }
    const el = inside[0].el;
    el.setAttribute('data-metis-field', k);
    out.fields[k] = {label: inside[0].label, disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
                     value: (el.value !== undefined ? String(el.value) : txt(el))};
  }
  const btns = [...form.querySelectorAll('button, [role=button], input[type=submit]')].filter(vis);
  const bname = b => txt(b) || b.getAttribute('aria-label') || b.value || '';
  for (const [k, r] of Object.entries(BP)) {
    let m = btns.filter(b => r.test(bname(b)));
    if (k === 'submit' && m.length === 0) {
      // The live sidebar's submit sits OUTSIDE the fields' container (probe
      // #13816: not in the panel, below y 735). Look in the form's ancestors,
      // only within the form's own column, and require exactly one.
      const fr = form.getBoundingClientRect();
      // The form's VISIBLE bottom: on the live sidebar the fields are scroll
      // content (y 105-735) inside a viewport ending at y 640, and the submit
      // is a fixed footer at y 659 -- below what is visible but above the
      // content box's own bottom. Clip to every scrolling/clipping ancestor
      // (dry run #13917 refused "no unique submit" on exactly this).
      let visBottom = fr.bottom;
      for (let a = form.parentElement; a && a !== document.body; a = a.parentElement) {
        const oy = getComputedStyle(a).overflowY;
        if (oy === 'scroll' || oy === 'auto' || oy === 'hidden') visBottom = Math.min(visBottom, a.getBoundingClientRect().bottom);
      }
      // Document-wide, like the probe's panel dump that DID find it (#13855):
      // the footer can sit more than a few ancestors above the fields.
      const diag = {form: [Math.round(fr.left), Math.round(fr.top), Math.round(fr.width), Math.round(fr.height)],
                    vis_bottom: Math.round(visBottom), matches: []};
      for (const b of document.querySelectorAll('button, [role=button], input[type=submit]')) {
        if (!r.test(bname(b))) continue;
        const br = b.getBoundingClientRect();
        const f = {text: bname(b).slice(0, 60), box: [Math.round(br.left), Math.round(br.top), Math.round(br.width), Math.round(br.height)],
                   in_form: form.contains(b), in_table: !!b.closest('table, tr'),
                   in_column: br.width > 0 && br.left >= fr.left - 10 && br.right <= fr.right + 10,
                   below: br.top >= visBottom - 10};
        if (diag.matches.length < 10) diag.matches.push(f);
        if (!f.in_form && !f.in_table && f.in_column && f.below) m.push(b);
      }
      out.submit_search = diag;
      if (m.length) out.submit_outside_form = true;
    }
    if (m.length === 1) { m[0].setAttribute('data-metis-btn', k); out.buttons[k] = txt(m[0]) || m[0].getAttribute('aria-label') || ''; }
    else if (m.length > 1) out.ambiguous.push('btn:' + k);
  }
  // Geometry of the ticket panel and of every tagged button (operator
  // 2026-09-29): the chart toolbar carries its own "Sell <price>" /
  // "<price> Buy" buttons which, with one-click trading ON, execute a full
  // lot instantly. A side / type / submit control this run would click must
  // sit inside the ticket panel: the Python side refuses one that does not
  // (verify_buttons_in_panel). Boxes only; never text.
  const box = el => { const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  out.panel_box = box(form);
  out.fields_box = box(fieldsForm);
  out.button_boxes = {};
  for (const b of document.querySelectorAll('[data-metis-btn]')) out.button_boxes[b.getAttribute('data-metis-btn')] = box(b);
  // The venue symbol the ticket is set to: the sidebar's own symbol_input
  // value (an instrument name, never account data; letters/digits only).
  const symIn = form.querySelector('[data-test-id=symbol_input]');
  if (symIn && /^[A-Za-z0-9._\/-]{1,20}$/.test(String(symIn.value || ''))) out.symbol_value = String(symIn.value);
  // SL / TP enabling switches: a checkbox / switch role, or (MEASURED on the
  // live sidebar, probe #13760) a div whose data-value reads "true"/"false".
  const toggleSel = 'input[type=checkbox], [role=checkbox], [role=switch], [data-value="true"], [data-value="false"]';
  out.toggle_candidates = form.querySelectorAll(toggleSel).length;
  out.data_value_toggles = form.querySelectorAll('[data-value="true"], [data-value="false"]').length;
  for (const cb of form.querySelectorAll(toggleSel)) {
    const lab = labelOf(cb);
    for (const k of ['stop_loss', 'take_profit']) {
      if (FP[k].test(lab)) {
        cb.setAttribute('data-metis-field', k + '_toggle');
        out.checkboxes.push({field: k, checked: !!(cb.checked || cb.getAttribute('aria-checked') === 'true'
                                                  || cb.getAttribute('data-value') === 'true')});
      }
    }
  }
  // Each bracket leg's entry MODE (the live sidebar shows a "Price" dropdown
  // beside each SL / TP input): the one mode-like button near the input.
  for (const k of ['stop_loss', 'take_profit']) {
    const inp = form.querySelector('[data-metis-field=' + k + ']');
    if (!inp || !out.fields[k]) continue;
    for (let e = inp.parentElement, i = 0; e && e !== form && i < 3; e = e.parentElement, i++) {
      const m = [...e.querySelectorAll('button, [role=button]')].filter(b =>
        /^(price|pips?|points?|ticks?|%|percent|amount|usd|\$)$/i.test(txt(b)));
      if (m.length === 1) { out.fields[k].mode = txt(m[0]); break; }
      if (m.length > 1) { out.fields[k].mode = 'ambiguous'; break; }
    }
  }
  // Which side / order-type control the form shows as SELECTED, read back
  // after our click: an explicit pressed/checked/selected state on the
  // button (aria-*, data-*, class, a radio inside), a <select> whose chosen
  // option names it, or — for the side only — a submit button that starts
  // with "Buy"/"Sell". null = not readable back (refuses, never assumed).
  const isOn = el => {
    if (!el) return false;
    for (const a of ['aria-pressed', 'aria-checked', 'aria-selected', 'aria-current']) {
      const v = el.getAttribute(a); if (v === 'true' || v === 'page') return true;
    }
    for (const a of ['data-active', 'data-state', 'data-selected', 'data-checked', 'data-pressed']) {
      const v = el.getAttribute(a); if (v !== null && /^(true|active|on|checked|selected|pressed|1)$/i.test(v)) return true;
    }
    if (typeof el.className === 'string' && /(^|[\s_-])(active|selected|checked|pressed|is-active|is-selected)([\s_-]|$)/i.test(el.className)) return true;
    if (el.tagName === 'INPUT' && el.type === 'radio') return !!el.checked;
    return [...el.querySelectorAll('input[type=radio], input[type=checkbox]')].some(i => i.checked);
  };
  // The live sidebar marks the chosen side / order type ONLY by background
  // (probe #13816: Market rgb(80,87,179), BUY rgb(26,143,109), every other
  // toggle rgb(64,66,94)). The "unselected" colour is the one most of the
  // form's side/type toggles share (unique, held by 2+); a tagged button in
  // any other colour is the selected one.
  const toggles = [...form.querySelectorAll('button, [role=button]')].filter(b =>
    vis(b) && /^(buy|sell|market|limit|stop|oco)$/i.test(txt(b)));
  const bgOf = b => getComputedStyle(b).backgroundColor;
  const counts = {};
  toggles.forEach(b => { counts[bgOf(b)] = (counts[bgOf(b)] || 0) + 1; });
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const unsel = (ranked.length && ranked[0][1] >= 2 && (ranked.length === 1 || ranked[0][1] > ranked[1][1])) ? ranked[0][0] : null;
  const pick = (keys) => {
    const on = keys.filter(k => out.buttons[k] !== undefined && isOn(form.querySelector('[data-metis-btn=' + k + ']')));
    if (on.length === 1) return on[0];
    if (on.length > 1) return 'ambiguous';
    for (const sel of form.querySelectorAll('select')) {
      const o = sel.options[sel.selectedIndex]; const t = o ? txt(o) : '';
      const hit = keys.filter(k => BP[k].test(t));
      if (hit.length === 1) return hit[0];
    }
    if (unsel) {
      const styled = keys.filter(k => out.buttons[k] !== undefined
        && bgOf(form.querySelector('[data-metis-btn=' + k + ']')) !== unsel);
      if (styled.length === 1) return styled[0];
      if (styled.length > 1) return 'ambiguous';
    }
    return null;
  };
  const side = pick(['side_buy', 'side_sell']);
  const submitText = out.buttons.submit || '';
  out.selected = {
    side: side === 'side_buy' ? 'buy' : side === 'side_sell' ? 'sell' : side === 'ambiguous' ? 'ambiguous'
          : (/^buy\b/i.test(submitText) ? 'buy' : /^sell\b/i.test(submitText) ? 'sell' : null),
    order_type: (t => t === 'type_market' ? 'market' : t === 'type_limit' ? 'limit' : t)(pick(['type_market', 'type_limit'])),
  };
  out.form_text = txt(form).slice(0, 2000);
  return out;
}
"""

# Reads the "One-click trading" control. Returns "on" / "off" / "unknown" plus
# the redacted shape of what it looked at.
#
# MEASURED 2026-09-30 (instrument-info-dry, issue #14714, run 36710165302,
# its one_click_dump): the live toggle is a custom ``div`` with
# ``data-test-id="one_click_trading"`` and ``data-value="false"`` (knob at
# left:2px), a sibling of the label -- no checkbox, switch or aria state, which
# is why this read ``unknown`` (#13711). It is now read from that attribute:
# exactly ONE ``[data-test-id=one_click_trading]`` in the whole document
# (hidden ones COUNT), really visible (size, and no display:none /
# visibility:hidden / opacity 0 up the tree), INSIDE the label's nearest 3
# ancestors, ``data-value`` exactly ``true`` / ``false``, AND exactly one
# knob whose position agrees (false = knob left, true = knob right). ⚠️ Only
# the OFF state has ever been observed; ON is UNMEASURED, so the knob check
# guards against an inverted attribute -- nobody toggles one-click to observe
# it. A toggle that is present but fails any check makes the whole reading
# ``unknown`` (it is never out-voted by an aria state), and every reading
# found must agree -- the info probe gates on a positive ``off`` and fails
# closed on ``unknown`` (manager reviews of #14723).
ONE_CLICK_JS = r"""
() => {
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').trim();
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  // Exactly ONE label leaf (manager note on #14723): the first match in DOM
  // order is not trusted when there are several.
  const labs = [...document.querySelectorAll('body *')].filter(el =>
    el.children.length === 0 && /^one[- ]click trading$/i.test(txt(el)));
  if (!labs.length) return {state: 'unknown', why: 'label not found'};
  if (labs.length > 1) return {state: 'unknown', why: `${labs.length} one-click labels (need exactly 1)`};
  const lab = labs[0];
  const states = [];
  let via = null;
  // REALLY visible: a size, and no display:none / visibility:hidden /
  // opacity 0 on the element or any ancestor (a size-only check passes an
  // opacity:0 element -- manager review of #14723).
  const shown = el => {
    if (!vis(el)) return false;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse'
          || parseFloat(cs.opacity) === 0) return false;
    }
    return true;
  };
  // EVERY toggle counts, visible or not: a hidden duplicate is not filtered
  // away before the exactly-one check (a hidden 'true' beside a visible
  // 'false' must not read 'off'). A toggle that is present but fails any
  // check blocks every other reading too.
  const toggles = [...document.querySelectorAll('[data-test-id=one_click_trading]')];
  let toggleWhy = null, knob = null;
  if (toggles.length > 1) toggleWhy = `${toggles.length} one_click_trading toggles (need exactly 1)`;
  else if (toggles.length === 1) {
    const t = toggles[0];
    let near = false;
    for (let e = lab.parentElement, i = 0; e && i < 3; e = e.parentElement, i++) if (e.contains(t)) { near = true; break; }
    const dv = t.getAttribute('data-value');
    // The KNOB must agree with data-value: only ONE state (false, knob left)
    // has ever been MEASURED (#14714) -- ON never was, so an inverted
    // attribute is not ruled out. Knob = the one really-visible element in
    // the toggle's parent subtree, smaller than the toggle, horizontally
    // inside it. Its centre left of the toggle's centre = left.
    const tr = t.getBoundingClientRect();
    const knobs = t.parentElement ? [...t.parentElement.querySelectorAll('*')].filter(k => {
      if (k === t || k.contains(t) || !shown(k)) return false;
      const r = k.getBoundingClientRect();
      return r.width >= 4 && r.height >= 4 && r.width < tr.width && r.height <= tr.height + 1
          && r.left >= tr.left - 1 && r.right <= tr.right + 1 && r.bottom > tr.top && r.top < tr.bottom;
    }) : [];
    if (knobs.length === 1) {
      const r = knobs[0].getBoundingClientRect(), kc = r.left + r.width / 2, tc = tr.left + tr.width / 2;
      knob = kc < tc ? 'left' : kc > tc ? 'right' : 'centre';
    }
    if (!shown(t)) toggleWhy = 'the one_click_trading toggle is not really visible';
    else if (!near) toggleWhy = "the toggle is not within the label's nearest 3 ancestors";
    else if (dv !== 'true' && dv !== 'false') toggleWhy = 'data-value is not exactly true/false';
    else if (knobs.length !== 1) toggleWhy = `${knobs.length} knob candidates (need exactly 1)`;
    else if (!((dv === 'false' && knob === 'left') || (dv === 'true' && knob === 'right')))
      toggleWhy = `data-value ${dv} disagrees with the knob (${knob})`;
    else { states.push(dv === 'true' ? 'on' : 'off'); via = 'data-value+knob'; }
  }
  // Checkbox / switch / aria state near the label (the pre-measurement
  // reader; other terminals and fixtures use it). Only REALLY visible
  // controls count, and there must be exactly one at the first level that
  // has any -- a hidden unchecked checkbox never reads 'off' (manager note on
  // #14723). The info probe accepts only via 'data-value+knob' anyway.
  let ctlWhy = null;
  for (let e = lab, i = 0; e && i < 4; e = e.parentElement, i++) {
    const cands = [e, ...e.querySelectorAll('input[type=checkbox], [role=switch], [role=checkbox], [aria-pressed], [aria-checked]')]
      .filter(c => c.type === 'checkbox' || (c.getAttribute && (c.getAttribute('aria-checked') || c.getAttribute('aria-pressed'))));
    if (!cands.length) continue;
    const vis_c = cands.filter(shown);
    if (vis_c.length !== 1) { ctlWhy = `${vis_c.length} visible checkbox/aria controls near the label (need exactly 1)`; break; }
    const c = vis_c[0];
    const ac = c.type === 'checkbox' ? (c.checked ? 'true' : 'false') : (c.getAttribute('aria-checked') || c.getAttribute('aria-pressed'));
    if (ac === 'true') states.push('on'); else if (ac === 'false') states.push('off');
    via = via ? via + '+aria' : 'aria';
    break;
  }
  const uniq = [...new Set(states)];
  const ok = uniq.length === 1 && !toggleWhy && !ctlWhy;
  return {state: ok ? uniq[0] : 'unknown', via, n_toggles: toggles.length, knob,
          why: ok ? 'read' : toggleWhy || ctlWhy || (uniq.length ? 'conflicting controls' : 'no data-value toggle or checkbox/switch/aria state near the label'),
          chain: (() => { const c = []; for (let e = lab, i = 0; e && e.tagName && i < 4; e = e.parentElement, i++)
            c.push(e.tagName.toLowerCase() + ((typeof e.className === 'string' && e.className) ? '.' + e.className.trim().split(/\s+/).join('.') : '')
                   + [...e.attributes].map(a => a.name).filter(n => n.startsWith('aria-') || n.startsWith('data-')).map(n => '[' + n + ']').join(''));
            return c; })()};
}
"""


# READ-ONLY structural dump around the "One-click trading" label, for building
# a reader of its on/off state (MEASURED 2026-09-28, issue #13711: the label
# is found but no checkbox / switch / aria state sits near it, so ONE_CLICK_JS
# reads `unknown`). For the label's nearest 3 ancestors it lists up to 30
# descendants: tag, class, role, attribute NAMES, short safe values of state-
# like attributes (aria-*, data-*, type, checked), short visible text, and a
# few computed styles a toggle usually encodes its state in. Never reads an
# input's value, cookies or storage. Clicks nothing.
ONE_CLICK_DUMP_JS = r"""
() => {
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').trim().replace(/\s+/g, ' ');
  const lab = [...document.querySelectorAll('body *')].find(el =>
    el.children.length === 0 && /^one[- ]click trading$/i.test(txt(el)));
  if (!lab) return {found: false};
  // Module masking convention (#14216; manager re-review of #14645): runs of
  // 5+ digits and runs of 8+ hex characters containing a digit become '#',
  // so an account id beside the toggle never reaches the public log.
  const mask = v => v.replace(/(?<![0-9a-f])[0-9a-f]{8,}(?![0-9a-f])/gi, m => /\d/.test(m) ? '#'.repeat(m.length) : m)
                     .replace(/\d(?:[\s,.-]?\d){7,}/g, m => '#'.repeat(m.length))
                     .replace(/\d{5,}/g, m => '#'.repeat(m.length));
  const safe = v => (typeof v === 'string' && v.length <= 24 && /^[A-Za-z0-9 _.:#%()-]*$/.test(v)) ? mask(v) : null;
  const desc = el => {
    const cs = getComputedStyle(el);
    const attrs = {};
    for (const a of el.attributes) {
      if (/^(aria-|data-)/.test(a.name) || a.name === 'type' || a.name === 'role') attrs[a.name] = safe(a.value);
    }
    if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) attrs['checked'] = String(el.checked);
    return {tag: el.tagName.toLowerCase(),
            cls: mask((typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '').trim()),
            attrs, text: el.children.length === 0 ? (safe(txt(el)) || '') : '',
            is_label: el === lab,
            style: {bg: cs.backgroundColor, color: cs.color, transform: cs.transform, left: cs.left,
                    justify: cs.justifyContent, opacity: cs.opacity, w: cs.width, h: cs.height,
                    cursor: cs.cursor, border: cs.borderColor}};
  };
  const levels = [];
  for (let e = lab.parentElement, i = 0; e && e !== document.body && i < 3; e = e.parentElement, i++) {
    levels.push({level: i + 1, self: desc(e), children: [...e.querySelectorAll('*')].slice(0, 30).map(desc)});
  }
  return {found: true, levels};
}
"""


# Read-only map of the page's CONTROLS, to find how the order-ticket sidebar
# opens and how it shows side / order type (probe 2026-09-28: no opener name
# matched, and the terminal tags controls with ``data-test-id``). Lists inputs,
# buttons, tabs and every ``data-test-id`` element outside table bodies. Never
# reads an input's value, and masks EVERY digit as ``#`` so no account number,
# balance or price can reach the public run log; emails become ``<email>``, and
# a user/profile/account/login control is recorded by its test id alone (a
# name can carry no digit). Clicks nothing.
CONTROLS_DUMP_JS = r"""
() => {
  const mask = v => (typeof v === 'string')
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>').replace(/\d/g, '#').slice(0, 40) : null;
  // A user / profile / account menu can carry the holder's name: its test id only.
  const personal = /user|profile|account|login|email/i;
  const txt = el => (el ? (el.innerText || el.textContent || '') : '');
  const sel = 'input, select, textarea, button, [role=button], [role=tab], [role=radio], [role=switch], [data-test-id]';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (el.closest('tbody')) continue;
    const r = el.getBoundingClientRect();
    const cls = typeof el.className === 'string' ? el.className : '';
    if (personal.test(el.getAttribute('data-test-id') || '') || personal.test(cls)) {
      out.push({tag: el.tagName.toLowerCase(), tid: mask(el.getAttribute('data-test-id')), personal: true});
      if (out.length >= 250) break;
      continue;
    }
    const d = {tag: el.tagName.toLowerCase(), tid: mask(el.getAttribute('data-test-id')),
               role: mask(el.getAttribute('role')), type: mask(el.getAttribute('type')),
               aria: mask(el.getAttribute('aria-label')), ph: mask(el.getAttribute('placeholder')),
               title: mask(el.getAttribute('title')),
               vis: r.width > 0 && r.height > 0,
               // Layout only (the ticket opener is an icon-only button, top
               // right): rounded CSS pixels, never page content.
               box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
               right_gap: Math.round(window.innerWidth - r.right),
               svg: !!el.querySelector('svg') || el.tagName === 'svg'};
    for (const a of el.attributes) {
      if (/^(aria-(selected|checked|pressed|disabled)|data-(value|selected|active|state|side|type))$/.test(a.name)) {
        (d.state = d.state || {})[a.name] = mask(a.value);
      }
    }
    if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && el.children.length <= 3) d.text = mask(txt(el));
    if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) d.checked = el.checked;
    const lab = el.closest('label') || el.parentElement;
    if (el.tagName === 'INPUT' && lab) d.label = mask(txt(lab));
    out.push(d);
    if (out.length >= 250) break;
  }
  return {found: out.length > 0, n: out.length, controls: out};
}
"""

# Read-only structure of the order-ticket SIDEBAR, anchored on the test ids the
# live terminal was MEASURED to carry (probe #13760, 2026-09-28: the sidebar was
# already open with ``symbol_input``, ``BUY`` / ``SELL`` buttons, Market / Limit
# / Stop / OCO buttons, a quantity input and SL / TP inputs behind
# ``data-value`` toggles). Lists every element in the panel in document order
# with its depth, OWN text (text nodes directly under it, so labels appear once),
# class, state attributes and, for controls, the computed colours that mark
# the selected side / order type. Same redaction as CONTROLS_DUMP_JS: every
# digit is '#', emails are '<email>', input values are never read, and a
# user/profile/account control is recorded by its test id alone. Clicks nothing.
TICKET_PANEL_DUMP_JS = r"""
() => {
  const mask = v => (typeof v === 'string')
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>').replace(/\d/g, '#').slice(0, 40) : null;
  const personal = /user|profile|account|login|email/i;
  // Anchor on the ticket's own BUY button: the chart widget carries a
  // symbol_input too (probe #13797: at x 328, first in DOM order), so
  // climbing from symbol_input reached the whole layout. The SMALLEST
  // ancestor of the one BUY that also holds SELL and a symbol_input is the
  // sidebar (probe #13775: no input-count condition).
  const buys = document.querySelectorAll('[data-test-id=BUY]');
  if (buys.length !== 1) return {found: false, why: buys.length + ' [data-test-id=BUY] elements (need exactly 1)'};
  let panel = null;
  for (let e = buys[0].parentElement; e && e !== document.body; e = e.parentElement) {
    if (e.querySelector('[data-test-id=SELL]') && e.querySelector('[data-test-id=symbol_input]')) { panel = e; break; }
  }
  if (!panel) return {found: false, why: 'no ancestor of BUY holds SELL and symbol_input'};
  const depthOf = el => { let d = 0; for (let e = el; e && e !== panel; e = e.parentElement) d++; return d; };
  const own = el => mask([...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ')) || '';
  const ctl = el => /^(BUTTON|INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || el.hasAttribute('data-value')
    || /^(button|switch|checkbox|radio|tab|spinbutton)$/.test(el.getAttribute('role') || '');
  const rows = [];
  for (const el of panel.querySelectorAll('*')) {
    if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') continue;
    if (el.closest('table')) continue;                       // tables are not the ticket
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0 && !ctl(el) && !el.hasAttribute('data-test-id')) continue;
    const tid = el.getAttribute('data-test-id');
    const cls = typeof el.className === 'string' ? el.className.trim() : '';
    const d = {d: depthOf(el), tag: el.tagName.toLowerCase(), tid: mask(tid),
               box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]};
    if (personal.test(tid || '') || personal.test(cls)) { d.personal = true; rows.push(d); continue; }
    const t = own(el); if (t) d.text = t;
    if (cls) d.cls = mask(cls);
    for (const a of ['role', 'type', 'placeholder', 'aria-label', 'title', 'name', 'inputmode']) {
      const v = el.getAttribute(a); if (v !== null) d[a] = mask(v);
    }
    for (const a of el.attributes) {
      if (/^(aria-(selected|checked|pressed|disabled|expanded)|data-(value|selected|active|state|side|type|checked))$/.test(a.name)) {
        (d.state = d.state || {})[a.name] = mask(a.value);
      }
    }
    if (el.disabled) d.disabled = true;
    if (el.readOnly) d.readonly = true;
    if (ctl(el)) {
      const cs = getComputedStyle(el);
      d.style = {bg: cs.backgroundColor, color: cs.color, border: cs.borderColor, fw: cs.fontWeight, op: cs.opacity};
    }
    rows.push(d);
    if (rows.length >= 300) break;
  }
  // Where the submit lives (probe #13816: not in the panel) and what scrolls:
  // every button in the panel's column OUTSIDE the panel, and the panel's
  // parent chain with its scroll sizes.
  const pr = panel.getBoundingClientRect();
  const submit_candidates = [...document.querySelectorAll('button, [role=button], input[type=submit]')].filter(b => {
    if (panel.contains(b)) return false;
    const r = b.getBoundingClientRect();
    return r.left >= pr.left - 10 && r.right <= pr.right + 10 && !(r.width === 0 && r.height === 0 && !b.textContent.trim());
  }).slice(0, 30).map(b => {
    const r = b.getBoundingClientRect();
    return {tag: b.tagName.toLowerCase(), tid: mask(b.getAttribute('data-test-id')), text: mask(b.innerText || b.textContent || ''),
            aria: mask(b.getAttribute('aria-label')), disabled: !!(b.disabled || b.getAttribute('aria-disabled') === 'true'),
            box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]};
  });
  const panel_parents = [];
  for (let e = panel, i = 0; e && e !== document.body && i < 6; e = e.parentElement, i++) {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    panel_parents.push({level: i, tag: e.tagName.toLowerCase(), cls: mask(typeof e.className === 'string' ? e.className : ''),
                        box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
                        scroll: [e.scrollHeight, e.clientHeight], overflow_y: cs.overflowY, children: e.children.length});
  }
  // The quantity input's own numeric constraints (UI limits, not account
  // data): the only digits this dump lets through.
  const num = /^-?[0-9]*[.]?[0-9]+(e-?[0-9]+)?$/i;
  const qty_constraints = [...panel.querySelectorAll('input')].map(i => {
    const c = {};
    for (const a of ['min', 'max', 'step', 'aria-valuemin', 'aria-valuemax', 'data-min', 'data-step', 'data-precision']) {
      const v = i.getAttribute(a); if (v !== null && num.test(v.trim())) c[a] = v.trim();
    }
    return Object.keys(c).length ? {tid: mask(i.getAttribute('data-test-id')), ...c} : null;
  }).filter(Boolean);
  return {found: true, n: rows.length, truncated: rows.length >= 300,
          scroll: [panel.scrollHeight, panel.clientHeight], rows, submit_candidates, panel_parents, qty_constraints};
}
"""

# Discovery for the watchlist symbol SEARCH input (PROP-ETH, 2026-09-29;
# revised after live run issue #14437 -- see below; MEASURED and narrowed
# 2026-09-30, PROP-ETH-DOM, issue #14551). Admits exactly ONE visible input
# inside the watchlist widget carrying BOTH INSTRUMENT_SEARCH_MATCH
# attributes (placeholder equal to "symbol..." AND data-test-id starting
# "watchlist_public"); there is no fallback of any kind, and zero or several
# matches refuse with a stated ``why``. Tags the one match with
# ``data-metis-search-hit`` for the Python side to locate; reads nothing,
# types nothing, clicks nothing.
#
# ⚠️ THE ANCHOR IS NOW POSITIVE, NOT NEGATIVE, and IS MEASURED. The original
# version admitted every input EXCEPT what it could prove was inside the
# order ticket (a BUY/SELL panel) -- which meant with 0 BUY buttons (no
# ticket open, the terminal's actual landing state) it had nothing to exclude
# from, so it refused outright. Live run #14437 (2026-09-29, issue #14437,
# code_sha 4ef6b3340) hit exactly this: all four symbols (BTC/ADA/AVAX/XRP)
# refused with "0 BUY buttons", correctly (refuse-not-guess held), but the
# probe can then only ever work while an order ticket happens to be open,
# which this probe never opens -- so it could never actually run.
#
# The fix anchors POSITIVELY on the watchlist panel instead: the same header
# table (Symbol/Bid/Ask columns) and row selector (``tr.instrument,
# tr[data-row-id]``) that :data:`WATCHLIST_ROWS_JS` already reads -- MEASURED
# 2026-09-29, dry run #13898, run 36507086110, and reused verbatim by
# :meth:`DXtradeAdapter.open_order_ticket`'s watchlist double-click. The
# watchlist needs no order ticket at all, so this anchor exists on exactly
# the landing state #14437 measured. A candidate input must be CONTAINED
# WITHIN that watchlist panel; if no watchlist table+row can be found at
# all, refuse (the same honest "could not look" this file uses everywhere).
# The order-ticket exclusion is KEPT as defense in depth (never admit an
# input inside a BUY+SELL-holding container, whatever the count -- the
# ORDER TICKET's own presence, not its count, is what matters), but it is no
# longer a PRECONDITION for the whole probe to run.
#
# ⚠️ HARDENED again 2026-09-29 (manager review of #14442, before merge): the
# first version's ``rows[0]`` was the first ``tr.instrument``/``[data-row-id]``
# ANYWHERE IN THE DOCUMENT, not necessarily a row of the watchlist's own
# header table. Whether the Positions/Orders grids ALSO use this same row
# selector is UNMEASURED -- the one recorded dump-tables run that shows a
# live Positions row (issue #14198, run 36572236094) prints each row's
# PARSED CELLS and its hover-control markup (button/title/aria-label
# elements only), never the ``<tr>`` element's own class or attributes, so
# it neither confirms nor rules this out. If it turned out true and a
# Positions/Orders row happened to precede the watchlist in document order,
# ``rows[0]`` could anchor on a container far wider than the watchlist --
# possibly the app root -- and containment would then admit almost anything.
# Three layers now guard against exactly that, refusing rather than trusting
# an unmeasured assumption:
#   1. The anchor row must ALIGN with the watchlist's OWN header: the same
#      cell count, and its cell under the header's own Symbol column index
#      reads a symbol-shaped token (never just "whatever tr matches first").
#   2. The resolved panel must hold exactly one Symbol-headed table (itself);
#      more than one, or any OTHER table in the panel whose headers look
#      like a positions/orders grid (side/quantity/p&l/profit/order type/
#      status), refuses -- a watchlist panel holds one quote table.
#   3. The upward walk excludes ``document.body`` by construction (the loop
#      never assigns it to ``e``), so the panel can never resolve to it.
#   4. (2026-09-30, PROP-ETH-DOM, MEASURED on issue #14551) The search input
#      is in the watchlist WIDGET's header, 6 levels above that panel, so the
#      containment scope is the nearest ``widget__container`` /
#      ``widgetNew__container`` ancestor (at most 8 levels up). That widget
#      must itself hold exactly one Symbol-headed table, no
#      positions/orders-shaped table, and no BUY+SELL order panel, and must
#      sit inside none -- otherwise refuse. Live run #14457 (pre-change)
#      found 0 inputs because it searched only inside the grid panel.
FIND_INSTRUMENT_SEARCH_JS = r"""
([[wantPlaceholder, wantTidPrefix]]) => {
  const txt = el => (el.innerText || el.textContent || '').trim();
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const symLike = /^[A-Z0-9]{2,15}$/i;
  const posOrdHeaderRe = /\b(side|quantity|p&l|profit|order type|status)\b/;

  // The watchlist's own header table (Symbol/Bid/Ask). Refuse if more than
  // one table on the page shares a Symbol header -- which one is "the"
  // watchlist is then undecidable, never guessed.
  const symbolTables = [];
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h)));
    if (hs.includes('symbol')) symbolTables.push({t, hs});
  }
  const watchlistTables = symbolTables.filter(x => x.hs.includes('bid') && x.hs.includes('ask'));
  if (watchlistTables.length !== 1) {
    return {found: false, why: `${watchlistTables.length} tables with Symbol/Bid/Ask headers (need exactly 1)`};
  }
  const headerTable = watchlistTables[0].t;
  const headers = watchlistTables[0].hs;
  const symbolIdx = headers.indexOf('symbol');

  // The anchor row must ALIGN with that header -- same cell count, symbol-
  // shaped Symbol cell -- never just the first tr.instrument/[data-row-id]
  // found anywhere in the document (see the block comment above).
  let anchorRow = null;
  for (const r of document.querySelectorAll('tr.instrument, tr[data-row-id]')) {
    const cells = [...r.querySelectorAll('td')].map(txt);
    if (cells.length !== headers.length) continue;
    if (symLike.test((cells[symbolIdx] || '').trim())) { anchorRow = r; break; }
  }
  // An EMPTY watchlist has no row to align (tradeify_1's restored
  // "Favourites" list, #15426/#15431: readable, 0 symbols). Then the header
  // table itself is the anchor; every widget-level check below still applies
  // (one Symbol-headed table, no positions/orders table, no BUY+SELL overlap,
  // the input must carry BOTH measured attributes), and the widget walk gets
  // EMPTY_EXTRA_UP more levels because it starts lower than a row's panel.
  const EMPTY_EXTRA_UP = 4;
  const headerAnchor = !anchorRow;

  let watchlistPanel = null;
  if (headerAnchor) {
    watchlistPanel = headerTable.parentElement;
  } else {
    for (let e = headerTable.parentElement; e && e !== document.body; e = e.parentElement) {
      if (e.contains(anchorRow)) { watchlistPanel = e; break; }
    }
  }
  if (!watchlistPanel) {
    return {found: false, why: 'no common ancestor of the watchlist header and its row'};
  }

  // The panel must hold exactly one Symbol-headed table (itself), and no
  // OTHER table inside it may look like a positions/orders grid.
  const tablesInPanel = [...watchlistPanel.querySelectorAll('table')];
  const symbolTablesInPanel = tablesInPanel.filter(t =>
    [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h))).includes('symbol'));
  if (symbolTablesInPanel.length !== 1) {
    return {found: false, why: `panel holds ${symbolTablesInPanel.length} Symbol-headed tables (need exactly 1)`};
  }
  for (const t of tablesInPanel) {
    if (t === headerTable) continue;
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h))).join(' ');
    if (posOrdHeaderRe.test(hs)) {
      return {found: false, why: 'panel also contains a positions/orders-shaped table'};
    }
  }

  // Defense in depth: never admit an input inside ANY BUY+SELL-holding
  // container, whatever the count -- an order ticket, open or not, is never
  // where this probe searches.
  const orderPanels = [];
  for (const b of document.querySelectorAll('[data-test-id=BUY]')) {
    for (let e = b.parentElement; e && e !== document.body; e = e.parentElement) {
      if (e.querySelector('[data-test-id=SELL]')) { orderPanels.push(e); break; }
    }
  }
  const inAnyOrderPanel = el => orderPanels.some(p => p.contains(el));

  // The search input lives in the watchlist WIDGET's header, not inside the
  // grid panel above (MEASURED 2026-09-30, issue #14551: wl_level 6 -- the
  // panel's 6th ancestor, ``div .widget__container... .widgetNew__container``).
  // So containment widens to that ONE widget: the nearest ancestor of the
  // panel, at most WIDGET_MAX_UP levels up, carrying a class token that
  // starts ``widget__container`` or ``widgetNew__container``. It must pass
  // the same checks the panel did (exactly one Symbol-headed table, no
  // positions/orders-shaped table) and must hold NO BUY+SELL order panel and
  // sit inside none -- otherwise refuse.
  const WIDGET_MAX_UP = 8;
  const clsTokens = e => (typeof e.className === 'string' ? e.className : '').split(/\s+/);
  let widget = null;
  let up = 0;
  const maxUp = WIDGET_MAX_UP + (headerAnchor ? EMPTY_EXTRA_UP : 0);
  for (let e = watchlistPanel.parentElement; e && e !== document.body && up < maxUp;
       e = e.parentElement, up++) {
    if (clsTokens(e).some(c => /^widget(New)?__container/.test(c))) { widget = e; break; }
  }
  if (!widget) {
    return {found: false, why: `no widget__container ancestor within ${maxUp} levels of the watchlist panel`};
  }
  const tablesInWidget = [...widget.querySelectorAll('table')];
  const symbolTablesInWidget = tablesInWidget.filter(t =>
    [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h))).includes('symbol'));
  if (symbolTablesInWidget.length !== 1) {
    return {found: false, why: `widget holds ${symbolTablesInWidget.length} Symbol-headed tables (need exactly 1)`};
  }
  for (const t of tablesInWidget) {
    if (t === headerTable) continue;
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h))).join(' ');
    if (posOrdHeaderRe.test(hs)) {
      return {found: false, why: 'widget also contains a positions/orders-shaped table'};
    }
  }
  if (orderPanels.some(p => widget.contains(p) || p.contains(widget))) {
    return {found: false, why: 'watchlist widget overlaps an order (BUY+SELL) panel'};
  }

  const inputs = [...widget.querySelectorAll('input')].filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.getAttribute('data-test-id') !== 'symbol_input'
      && !inAnyOrderPanel(el);
  });
  // BOTH measured attributes on the SAME input; no fallback of any kind.
  const hit = inputs.filter(el =>
    norm(el.getAttribute('placeholder')) === wantPlaceholder
    && (el.getAttribute('data-test-id') || '').toLowerCase().startsWith(wantTidPrefix));
  if (hit.length > 1) {
    return {found: false, why: `${hit.length} inputs have placeholder '${wantPlaceholder}' and a `
                               + `${wantTidPrefix}* data-test-id (need exactly 1)`};
  }
  if (hit.length === 1) {
    hit[0].setAttribute('data-metis-search-hit', '1');
    return {found: true, via: headerAnchor ? 'placeholder+tid (empty watchlist: header anchor)' : 'placeholder+tid'};
  }
  return {found: false, n_candidate_inputs: inputs.length,
          why: `no visible input in the watchlist widget has placeholder '${wantPlaceholder}' AND a `
               + `${wantTidPrefix}* data-test-id (${inputs.length} inputs checked)`};
}
"""

# Cleanup for FIND_INSTRUMENT_SEARCH_JS's own tag. Strips
# ``data-metis-search-hit`` from every element that carries it, run
# unconditionally at the end of probe_instrument_details (success, refusal
# or exception alike) so a tag from one symbol's probe can never linger and
# throw off the tag-count check on the NEXT symbol's probe. Clicks nothing,
# reads nothing, changes no value -- removes only the marker this file adds.
# Read-only dump of the watchlist search's SUGGESTION table (TRADEIFY-GOLIVE,
# MEASURED #15444/#15457: typing opens a panel whose header reads Symbol /
# Description / Asset Class). INSTRUMENT_DETAILS_DUMP_JS deliberately skips
# everything inside a <tbody> (positions/orders tables), and that would hide
# the suggestion rows too. So this anchors ONLY on a visible "Asset Class"
# header leaf, walks up (at most 8 levels) to the smallest container that
# also shows a "Description" and a "Symbol" header. A header inside a real
# <table> reads THAT table's own tbody rows (empty = no results; #15472's
# fix: the first cut widened past an empty tbody and could reach another
# table's rows). A div grid reads [role=row], widening only over containers
# that hold no other table. Each row gives its
# tag / data-test-id / role and its cells' text, with digit runs masked
# (5+), emails masked and personal-looking controls skipped, like the other
# dumps. It clicks nothing and reads no input value. At most 40 rows.
SUGGESTION_ROWS_JS = r"""
() => {
  const maskRuns = v => (typeof v === 'string')
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>')
        .replace(/\d{5,}/g, m => '#'.repeat(m.length)).slice(0, 80) : null;
  const personal = /user|profile|account|login|email/i;
  const vis = el => { const r = el.getBoundingClientRect(); return !(r.width === 0 && r.height === 0); };
  const leafText = el => (el.children.length === 0) ? (el.innerText || el.textContent || '').trim() : '';
  const hasLeaf = (root, t) => Array.from(root.querySelectorAll('*')).some(e => leafText(e) === t && vis(e));
  const anchors = Array.from(document.querySelectorAll('*')).filter(e => leafText(e) === 'Asset Class' && vis(e));
  if (anchors.length === 0) return {found: false, why: 'no visible "Asset Class" header'};
  const panels = [];
  for (const a of anchors) {
    let el = a.parentElement, hops = 0;
    while (el && hops < 8 && !(hasLeaf(el, 'Description') && hasLeaf(el, 'Symbol'))) { el = el.parentElement; hops++; }
    if (!el || hops >= 8) continue;
    // A header inside a real <table>: that table IS the panel; its own tbody
    // holds the rows (empty tbody = no results). Never widen past it.
    const tbl = a.closest('table');
    if (tbl) { if (!panels.includes(tbl)) panels.push(tbl); continue; }
    // A div grid: widen until the container also holds role=row rows, but
    // never over a container that holds ANOTHER table (positions/orders).
    let box = el, up = 0;
    while (box && up < 4 && !box.querySelector('[role=row]')) {
      const nxt = box.parentElement;
      if (!nxt || nxt.querySelector('table')) break;
      box = nxt; up++;
    }
    if (box && box.querySelector('[role=row]')) el = box;
    if (!panels.includes(el)) panels.push(el);
  }
  if (panels.length === 0) return {found: false, n_anchors: anchors.length, why: 'no container shows Symbol + Description + Asset Class'};
  const out = [];
  for (const panel of panels) {
    let rows = (panel.tagName === 'TABLE')
      ? Array.from(panel.tBodies).flatMap(b => Array.from(b.rows)) : [];
    let via = 'tbody tr';
    if (rows.length === 0) { rows = Array.from(panel.querySelectorAll('[role=row]')); via = 'role=row'; }
    const got = [];
    for (const r of rows) {
      if (!vis(r)) continue;
      const cls = typeof r.className === 'string' ? r.className : '';
      const tid = r.getAttribute('data-test-id') || '';
      if (personal.test(cls) || personal.test(tid)) continue;
      const cells = Array.from(r.querySelectorAll('td, [role=cell], [role=gridcell]'));
      const texts = (cells.length ? cells : [r]).map(c => maskRuns(c.innerText || c.textContent || ''));
      got.push({tag: r.tagName.toLowerCase(), tid: maskRuns(tid), role: maskRuns(r.getAttribute('role')), cells: texts});
      if (got.length >= 40) break;
    }
    out.push({via, n_rows: rows.length, n_visible: got.length, rows: got});
  }
  return {found: true, n_panels: panels.length, panels: out};
}
"""

# Read-only FULL-PAGE leaf-text dump (TRADEIFY-GOLIVE (b), manager 12:58Z
# 2026-10-02). Every key-by-key form of ETH / SOL / XRP left the suggestion
# table under "Asset Class" with an EMPTY tbody (#15491/#15493/#15494). This
# reads every visible text LEAF on the page, INCLUDING ones inside a <tbody>,
# a [role=row] or a virtualized grid, to show where (if anywhere) results
# render. Per leaf: tag, its box (rounded px), its nearest table/grid-like
# ancestor (tag, role, masked data-test-id, whether it sits in a tbody) and
# its masked text: emails masked and EVERY digit replaced by '#' (manager
# review of #15550: balances, P&L, prices and sizes never reach the public
# run log), 80 chars. A node whose own
# or parent class/data-test-id looks personal (user/profile/account/login/
# email) is skipped. At most 400 leaves in document order, plus up to 50
# ``matches``: leaves whose text contains the typed query, collected whatever
# the cap. It clicks nothing and reads no input value.
PAGE_LEAF_DUMP_JS = r"""
(query) => {
  const maskRuns = v => (typeof v === 'string')
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>')
        .replace(/\d{5,}/g, m => '#'.repeat(m.length)).slice(0, 80) : null;
  // EVERY digit masked in leaf text (manager review of #15550): a page-wide
  // dump would otherwise print balances, P&L, prices and sizes into public
  // run logs. Matching uses the RAW text, so the query still matches.
  const maskAll = v => { const m = maskRuns(v); return (m === null) ? null : m.replace(/\d/g, '#'); };
  const personal = /user|profile|account|login|email/i;
  const looksPersonal = el => {
    for (const e of [el, el.parentElement]) {
      if (!e) continue;
      const cls = typeof e.className === 'string' ? e.className : '';
      if (personal.test(cls) || personal.test(e.getAttribute('data-test-id') || '')) return true;
    }
    return false;
  };
  const gridSel = 'table, [role=grid], [role=treegrid], [role=table], [role=listbox], [role=rowgroup], [role=row], [role=list]';
  const q = String(query || '').trim().toUpperCase();
  const leaves = [], matches = [];
  let seen = 0;
  for (const el of document.body.querySelectorAll('*')) {
    if (el.children.length > 0) continue;
    const tag = el.tagName.toLowerCase();
    if (tag === 'script' || tag === 'style' || tag === 'input' || tag === 'textarea') continue;
    const t = (el.innerText || el.textContent || '').trim();
    if (!t) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    if (looksPersonal(el)) continue;
    seen++;
    const g = el.closest(gridSel);
    const row = {tag, box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                 grid: g ? {tag: g.tagName.toLowerCase(), role: maskRuns(g.getAttribute('role')),
                            tid: maskAll(g.getAttribute('data-test-id') || ''), in_tbody: !!el.closest('tbody')} : null,
                 text: maskAll(t)};
    if (leaves.length < 400) leaves.push(row);
    if (q && t.toUpperCase().includes(q) && matches.length < 50) matches.push(row);
  }
  return {found: leaves.length > 0, query: q, n_seen: seen, n: leaves.length, capped: seen > leaves.length,
          n_matches: matches.length, matches, leaves};
}
"""

# Resolve the ONE suggestion row to add (TRADEIFY-GOLIVE, manager GO 17:39Z
# 2026-10-02). MEASURED (#15602): the search's result panel draws its header
# (Symbol / Description) and its rows as two SEPARATE tables; the header
# table's own tbody is empty. This anchors on the visible header table (a
# <th> text "Symbol" AND one "Description"), takes the FIRST table that
# FOLLOWS it inside the nearest shared ancestor (at most 6 levels up) and has
# <tbody> rows, and requires that table's rows to have as many cells as the
# header has columns (so a positions/orders table never qualifies). The target
# is the ONE row whose FIRST cell's whole text, with ALL whitespace removed
# (live #15618 read "ETH /USD": <mark>ETH</mark> + "/USD"), equals ``target``
# exactly (ETH/USD, never ETC/USD or ENA/USD); 0 or >1 is refused.
# That cell is tagged data-metis-add-target. Returned for review: every row's
# cells (EVERY digit masked, as in PAGE_LEAF_DUMP_JS), the target cell's box,
# its centre, and what document.elementFromPoint returns there (tag, masked
# text, whether it lies inside the target cell). Clicks nothing.
ADD_SYMBOL_RESOLVE_JS = r"""
([target]) => {
  const mask = v => (typeof v === 'string')
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>').replace(/\d/g, '#').slice(0, 80) : null;
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const norm = el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  document.querySelectorAll('[data-metis-add-target]').forEach(e => e.removeAttribute('data-metis-add-target'));
  const heads = [...document.querySelectorAll('table')].filter(t => vis(t)).filter(t => {
    const ths = [...t.querySelectorAll('th')].map(norm);
    return ths.includes('Symbol') && ths.includes('Description');
  });
  if (heads.length !== 1) return {ok: false, why: heads.length + ' visible Symbol+Description header tables (need exactly 1)'};
  const head = heads[0];
  const ncols = head.querySelectorAll('th').length;
  let body = null, anc = head.parentElement, up = 0;
  // 6 levels (manager review 18:41Z 2026-10-02): live #15618 found the body at
  // ancestor_hops 4, the old limit, so one layout level would have refused.
  while (anc && up < 6 && !body) {
    for (const t of anc.querySelectorAll('table')) {
      if (t === head || !vis(t)) continue;
      if (!(head.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
      if (!t.tBodies.length || !t.tBodies[0].rows.length) continue;
      body = t; break;
    }
    anc = anc.parentElement; up++;
  }
  if (!body) return {ok: false, why: 'no results table follows the header table (nothing to add)', ncols};
  const rows = [...body.tBodies].flatMap(b => [...b.rows]).filter(vis);
  const shape_ok = rows.every(r => r.cells.length === ncols);
  const listed = rows.slice(0, 30).map(r => [...r.cells].map(c => mask(norm(c))));
  const out = {ok: false, ncols, n_rows: rows.length, shape_ok, rows: listed, ancestor_hops: up};
  if (!shape_ok) { out.why = 'results rows do not have the header column count'; return out; }
  // MEASURED live (#15618): the venue renders the Symbol cell as
  // <mark>ETH</mark> + "/USD" and innerText reads "ETH /USD". Compare with ALL
  // whitespace removed on both sides; anything else must still match exactly
  // (ETC/USD, ENA/USD never equal ETH/USD).
  const compact = v => String(v || '').replace(/\s+/g, '');
  const hits = rows.filter(r => r.cells.length && compact(norm(r.cells[0])) === compact(target));
  out.n_target = hits.length;
  if (hits.length !== 1) { out.why = hits.length + ' rows whose Symbol cell is exactly ' + target + ' (need exactly 1)'; return out; }
  const cell = hits[0].cells[0];
  cell.setAttribute('data-metis-add-target', '1');
  const r = cell.getBoundingClientRect();
  const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
  const at = document.elementFromPoint(cx, cy);
  out.target_cells = [...hits[0].cells].map(c => mask(norm(c)));
  out.box = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
  out.point = [cx, cy];
  out.at_point = at ? {tag: at.tagName.toLowerCase(), text: mask(norm(at)), inside_target: cell.contains(at)} : null;
  out.ok = !!(at && cell.contains(at));
  if (!out.ok) out.why = 'elementFromPoint at the cell centre is not inside the target cell';
  return out;
}
"""

CLEAR_INSTRUMENT_SEARCH_HIT_JS = r"""
() => {
  document.querySelectorAll('[data-metis-search-hit]').forEach(
    el => el.removeAttribute('data-metis-search-hit'));
}
"""

# MEASUREMENT for the symbol search/add control (PROP-ETH-DOM, 2026-09-30,
# pipeline PI-20260929-AQRK6CL1-0014). Live run #14457 (code bd340d6ae)
# resolved the watchlist panel through FIND_INSTRUMENT_SEARCH_JS's anchor but
# found n_candidate_inputs 0 for all four symbols: the control is outside that
# panel, is not an <input>, or carries labels INSTRUMENT_SEARCH_MATCH
# does not name. Guessing a fourth candidate list is how the earlier rounds
# drifted, so this dump records the shape and the next change derives the
# candidates and the anchor FROM it.
#
# What it lists, from the whole document (open shadow roots included): every
# visible input/textarea/select, [role=searchbox|combobox|textbox],
# contenteditable, and every button / [role=button] / element whose own or
# icon-descendant attributes read search-, find-, filter-, add- or plus-like.
# Each row: tag, type, role, placeholder, aria-label, data-test-id, title,
# name, class, rounded rect, whether it sits INSIDE the resolved watchlist
# panel, how many ancestor levels above that panel first contain it
# (``wl_level``, the "near" measure), and its ancestor chain with class names.
# Sorted nearest-first, capped at MAX (50) rows so the run log's tail keeps it.
#
# What it NEVER does: read an input's value, read the text of an editable
# control (only its length), type, click, focus, scroll, or tag anything. It
# skips password fields, personal-looking controls, the order ticket's own
# symbol_input, and anything inside a BUY+SELL-holding container (counted,
# never listed), so the order ticket is not read. Runs of 5+ digits and
# e-mails are masked in every string it returns.
INSTRUMENT_SEARCH_DUMP_JS = r"""
() => {
  const MAX = 50, CHAIN = 6;
  // Any 24+ run of [A-Za-z0-9_-.=+/] is handled HERE, because the run-log
  // redactor (redact_text's _TOKENISH_RE) would otherwise replace a long CSS
  // class name with "<token>" and erase the very measurement this dump
  // exists for. A run that LOOKS like a token (3+ digits, or upper AND lower
  // case with no -_. word separators: base64 / JWT / hex-ish) is dropped
  // WHOLE as "<tok>" -- no prefix of it is published (manager review of
  // #14527, note c). A readable identifier (a kebab/snake/dotted class name)
  // keeps a 16-char prefix + an ellipsis. The redactor itself is unchanged.
  const tokenLike = m => (m.match(/\d/g) || []).length >= 3
    || (/[a-z]/.test(m) && /[A-Z]/.test(m) && !/[-_.]/.test(m));
  const mask = (v, n) => (typeof v === 'string' && v.trim())
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>')
        .replace(/\d{5,}/g, m => '#'.repeat(m.length))
        .replace(/[A-Za-z0-9_\-.=+\/#]{24,}/g, m => tokenLike(m) ? '<tok>' : m.slice(0, 16) + '\u2026')
        .slice(0, n || 60) : null;
  const txt = el => (el.innerText || el.textContent || '').trim();
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const clsOf = el => (typeof el.className === 'string' ? el.className
                       : (el.className && el.className.baseVal) || '');
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const personal = /user|profile|account|login|email|password/i;
  // Token match, never substring: "padding" / "address" must not read as
  // "add". camelCase and kebab/snake names are split into words first.
  const words = s => (s || '').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase().split(/[^a-z]+/).filter(Boolean);
  const SEARCHY = new Set(['search', 'searchbox', 'magnifier', 'magnifying', 'magnify', 'loupe', 'find', 'lookup',
                           'filter', 'add', 'plus', 'instrument', 'instruments', 'symbol', 'symbols']);
  const SEARCH_NAMED = new Set(['search', 'searchbox', 'magnifier', 'magnifying', 'magnify', 'loupe']);
  const has = (s, set) => words(s).some(w => set.has(w));

  // Every element, open shadow roots included (a closed root is invisible to
  // page script; the count of hosts tells a reader whether that matters).
  const all = [];
  let shadowHosts = 0;
  const walk = root => {
    for (const el of root.querySelectorAll('*')) {
      all.push(el);
      if (el.shadowRoot) { shadowHosts++; walk(el.shadowRoot); }
    }
  };
  walk(document);

  // The watchlist panel, resolved as FIND_INSTRUMENT_SEARCH_JS does (header
  // table with Symbol/Bid/Ask + an aligned row + their common ancestor below
  // body). Not a precondition: when it does not resolve, the dump still runs
  // document-wide and says why.
  let panel = null, panelWhy = null;
  const wl = [];
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h)));
    if (hs.includes('symbol') && hs.includes('bid') && hs.includes('ask')) wl.push({t, hs});
  }
  if (wl.length !== 1) {
    panelWhy = `${wl.length} tables with Symbol/Bid/Ask headers (need exactly 1)`;
  } else {
    const {t, hs} = wl[0];
    const si = hs.indexOf('symbol');
    let row = null;
    for (const r of document.querySelectorAll('tr.instrument, tr[data-row-id]')) {
      const cells = [...r.querySelectorAll('td')].map(txt);
      if (cells.length === hs.length && /^[A-Z0-9]{2,15}$/i.test((cells[si] || '').trim())) { row = r; break; }
    }
    if (!row) panelWhy = 'no watchlist row aligned with the header';
    for (let e = t.parentElement; row && e && e !== document.body; e = e.parentElement) {
      if (e.contains(row)) { panel = e; break; }
    }
    if (row && !panel) panelWhy = 'no common ancestor of the watchlist header and its row';
  }
  // Ancestors of the panel, nearest first, for the wl_level measure.
  const panelUp = [];
  for (let e = panel; e; e = e.parentElement) panelUp.push(e);
  const wlLevel = el => {
    if (!panel) return null;
    for (let k = 0; k < panelUp.length; k++) if (panelUp[k].contains(el)) return k;
    return null;   // inside a shadow root no light-DOM ancestor contains
  };

  const orderPanels = [];
  for (const b of document.querySelectorAll('[data-test-id=BUY]')) {
    for (let e = b.parentElement; e && e !== document.body; e = e.parentElement) {
      if (e.querySelector('[data-test-id=SELL]')) { orderPanels.push(e); break; }
    }
  }
  const inOrderPanel = el => orderPanels.some(p => p.contains(el));

  // Space-separated parts ("div #id .a .b [role=x] [tid=y]"), never one
  // dotted run the redactor would read as a token.
  const desc = e => {
    const parts = [e.tagName.toLowerCase()];
    if (e.id) parts.push('#' + mask(e.id, 30));
    for (const c of clsOf(e).split(/\s+/).filter(Boolean).slice(0, 3)) parts.push('.' + mask(c, 30));
    const role = e.getAttribute('role'); if (role) parts.push(`[role=${mask(role, 20)}]`);
    const tid = e.getAttribute('data-test-id'); if (tid) parts.push(`[tid=${mask(tid, 30)}]`);
    if (e === panel) parts.push('<WATCHLIST_PANEL>');
    return parts.join(' ');
  };
  const chain = el => {
    const out = [];
    for (let e = el.parentElement; e && e !== document.body && out.length < CHAIN; e = e.parentElement) out.push(desc(e));
    return out;
  };
  // An icon's href keeps only its #fragment (a sprite id): a path or query
  // is never read.
  const frag = h => (h && h.includes('#')) ? '#' + h.split('#').pop() : null;
  const iconAttrs = el => [...el.querySelectorAll('svg, use, i, img, [class*=icon], [class*=Icon]')].slice(0, 4)
    .map(i => [clsOf(i), i.getAttribute('aria-label'), i.getAttribute('title'), i.getAttribute('data-test-id'),
               frag(i.getAttribute('href')), frag(i.getAttribute('xlink:href')), i.getAttribute('alt')]
               .filter(Boolean).join(' '))
    .join(' ');

  const kindOf = el => {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return 'field';
    if (/^(searchbox|combobox|textbox)$/.test(role)) return 'role_' + role;
    if (el.getAttribute('contenteditable') !== null && el.isContentEditable) return 'contenteditable';
    const own = [el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('data-test-id'),
                 el.getAttribute('placeholder'), clsOf(el)].filter(Boolean).join(' ');
    const clickable = tag === 'button' || role === 'button' || el.hasAttribute('onclick') || tag === 'a';
    if (clickable) {
      const t = el.children.length === 0 ? txt(el) : '';
      if (has(own, SEARCHY) || has(iconAttrs(el), SEARCHY) || /^\+$/.test(t)) return 'search_like_button';
      return null;
    }
    // A non-button element NAMED like a search control (a div-based search).
    if (has(own, SEARCH_NAMED)) return 'search_named';
    return null;
  };

  const rows = [];
  let nOrder = 0, nPersonal = 0, nHiddenInPanel = 0;
  for (const el of all) {
    const kind = kindOf(el);
    if (!kind) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (!visible(el)) { if (panel && panel.contains(el)) nHiddenInPanel++; continue; }
    if (inOrderPanel(el)) { nOrder++; continue; }
    const tid = el.getAttribute('data-test-id') || '';
    if (type === 'password' || tid === 'symbol_input'
        || personal.test([tid, el.getAttribute('name') || '', el.getAttribute('aria-label') || '',
                          el.getAttribute('placeholder') || '', clsOf(el)].join(' '))) { nPersonal++; continue; }
    const editable = kind === 'field' || kind.startsWith('role_') || kind === 'contenteditable';
    const r = el.getBoundingClientRect();
    rows.push({
      kind, tag, type: mask(type, 20), role: mask(el.getAttribute('role'), 20),
      placeholder: mask(el.getAttribute('placeholder')), aria_label: mask(el.getAttribute('aria-label')),
      data_test_id: mask(tid), title: mask(el.getAttribute('title')), name: mask(el.getAttribute('name'), 30),
      cls: mask(clsOf(el), 80),
      // Never a value: an editable control reports only its text LENGTH.
      text: editable ? null : mask(el.children.length ? txt(el).split('\n')[0] : txt(el), 40),
      text_len: editable && tag !== 'input' && tag !== 'select' ? txt(el).length : null,
      n_options: tag === 'select' ? el.options.length : null,
      icon: kind === 'search_like_button' ? mask(iconAttrs(el), 80) : null,
      rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      in_wl_panel: !!(panel && panel.contains(el)),
      wl_level: wlLevel(el),
      chain: chain(el),
    });
  }
  const rank = x => (x.in_wl_panel ? 0 : 1) * 1000 + (x.wl_level === null ? 999 : x.wl_level);
  rows.sort((a, b) => rank(a) - rank(b));   // stable: document order within a rank
  const panelInfo = panel ? {
    desc: desc(panel), chain: chain(panel), rect: (r => [Math.round(r.left), Math.round(r.top),
      Math.round(r.width), Math.round(r.height)])(panel.getBoundingClientRect()),
    headers: wl[0].hs.slice(0, 12).map(h => mask(h, 20)),
    children: [...panel.children].slice(0, 15).map(c => ({desc: desc(c), n_inputs: c.querySelectorAll('input').length,
      n_buttons: c.querySelectorAll('button, [role=button]').length, has_table: !!c.querySelector('table')})),
  } : null;
  return {found: true, panel: panelInfo, panel_why: panelWhy, n_rows: rows.length,
          truncated: rows.length > MAX, rows: rows.slice(0, MAX),
          excluded_order_panel: nOrder, excluded_personal: nPersonal, hidden_in_panel: nHiddenInPanel,
          ticket_holds_watchlist: !!(panel && orderPanels.some(p => p.contains(panel))),
          n_iframes: document.querySelectorAll('iframe').length, n_shadow_hosts: shadowHosts,
          n_elements: all.length};
}
"""

# Read-only, DIGIT-RUN-masked (runs of 5+ digits only -- deliberately looser
# than CONTROLS_DUMP_JS's every-digit mask, because the numbers THIS dump
# exists to surface -- lot size, tick/price step, a venue minimum -- are
# exactly the short ones an every-digit mask would destroy; a 5+ run catches
# account numbers, order ids and timestamps instead) dump of controls plus
# short static text leaves (an Instrument Details panel is plausibly plain
# label/value divs, not "controls" in the CONTROLS_DUMP_JS sense). Same
# personal-control exclusion as CONTROLS_DUMP_JS. Reads nothing from an
# input's VALUE. Bounded to 300 rows.
INSTRUMENT_DETAILS_DUMP_JS = r"""
() => {
  const maskRuns = v => (typeof v === 'string')
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>')
        .replace(/\d{5,}/g, m => '#'.repeat(m.length)).slice(0, 80) : null;
  const personal = /user|profile|account|login|email/i;
  const out = [];
  const ctlSel = 'input, select, textarea, button, [role=button], [role=tab], [role=radio], [role=switch], [data-test-id]';
  for (const el of document.querySelectorAll(ctlSel)) {
    if (el.closest('tbody')) continue;
    const cls = typeof el.className === 'string' ? el.className : '';
    const tid = el.getAttribute('data-test-id') || '';
    if (personal.test(tid) || personal.test(cls)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    out.push({kind: 'control', tag: el.tagName.toLowerCase(), tid: maskRuns(tid),
              role: maskRuns(el.getAttribute('role')), ph: maskRuns(el.getAttribute('placeholder')),
              aria: maskRuns(el.getAttribute('aria-label')),
              text: (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && el.children.length === 0)
                ? maskRuns(el.innerText || el.textContent || '') : null});
    if (out.length >= 300) return {found: out.length > 0, n: out.length, rows: out};
  }
  for (const el of document.querySelectorAll('div, span, td, dt, dd, li')) {
    if (el.children.length > 0) continue;
    if (el.closest('tbody')) continue;
    const cls = typeof el.className === 'string' ? el.className : '';
    const tid = el.getAttribute('data-test-id') || '';
    if (personal.test(cls) || personal.test(tid)) continue;
    const t = (el.innerText || el.textContent || '').trim();
    if (!t || t.length > 60) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    out.push({kind: 'text', tag: el.tagName.toLowerCase(), text: maskRuns(t)});
    if (out.length >= 300) break;
  }
  return {found: out.length > 0, n: out.length, rows: out};
}
"""

# The ticket's submit control, which on the live sidebar is a FOOTER OUTSIDE
# the fields' container (`submit_search` has reported `in_form: false` on
# every live measurement) and can sit below the fold once the SL / TP rows
# are switched on (operator 2026-09-28 ~19:55Z). Ops:
#  "scroll_step": scroll the SUBMIT's own scroll container when it is already
#                 tagged, else the form's (never the page or the chart), down
#                 by ~80% of its height; returns whether it moved.
#  "mark":        tag the current [data-metis-btn=submit] with a one-time token
#                 and bring it to the centre of its scroll container.
#  "reveal":      scroll every scrollable ancestor of the BUTTON so the button
#                 lands on that ancestor's centre, then one scrollIntoView.
#  "check":       is the element carrying the submit tag the SAME (token) one,
#                 visible at its centre point, enabled; and its text. When it
#                 is NOT visible, also WHY -- the viewport, the button's rect,
#                 what is painted at its centre, and its clip chain.
# Clicks nothing, fills nothing, dismisses nothing.
SUBMIT_JS = r"""
(args) => {
  const [op, token] = args;
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').trim().replace(/\s+/g, ' ');
  const mask = s => String(s || '').replace(/\d{5,}/g, d => '#'.repeat(d.length)).slice(0, 40);
  const bx = el => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round); };
  const cls = el => (typeof el.className === 'string' ? el.className.split(/\s+/) : [])
                      .filter(Boolean).slice(0, 6).map(c => mask(c.replace(/\d/g, '#')));
  // Tag / class / test-id / box only: never the occluder's TEXT, which on this
  // terminal can carry account numbers (the public run log rule).
  const desc = el => el ? {tag: el.tagName.toLowerCase(), cls: cls(el), tid: mask(el.getAttribute('data-test-id')), box: bx(el)} : null;
  const clips = el => { const out = [];
    for (let e = el && el.parentElement; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (!/(auto|scroll|hidden|clip)/.test(cs.overflowY + ' ' + cs.overflowX)) continue;
      out.push(Object.assign(desc(e), {overflow_y: cs.overflowY, scroll_top: Math.round(e.scrollTop),
               scroll_height: Math.round(e.scrollHeight), client_height: Math.round(e.clientHeight)}));
      if (out.length >= 6) break;
    }
    return out; };
  const form = document.querySelector('[data-metis-form]');
  if (!form) return {ok: false, why: 'no tagged form'};
  const tagged = document.querySelector('[data-metis-btn=submit]');
  if (op === 'scroll_step') {
    const anchor = tagged || form;
    let sc = null;
    for (let e = anchor; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (e.scrollHeight > e.clientHeight + 1 && /(auto|scroll)/.test(cs.overflowY)) { sc = e; break; }
    }
    if (!sc) return {ok: true, moved: false, why: 'no scrollable container'};
    const before = sc.scrollTop;
    sc.scrollTop = before + Math.max(40, Math.floor(sc.clientHeight * 0.8));
    return {ok: true, moved: sc.scrollTop !== before, top: sc.scrollTop, max: sc.scrollHeight - sc.clientHeight};
  }
  const btn = tagged;
  if (!btn) return {ok: false, why: 'no tagged submit'};
  if (op === 'reveal') {
    const moved = [];
    for (let e = btn.parentElement; e && e !== document.body; e = e.parentElement) {
      if (e.scrollHeight <= e.clientHeight + 1) continue;
      const er = e.getBoundingClientRect(), br = btn.getBoundingClientRect();
      const delta = Math.round((br.top + br.height / 2) - (er.top + er.height / 2));
      if (Math.abs(delta) < 2) continue;
      const before = e.scrollTop;
      e.scrollTop = before + delta;
      if (e.scrollTop !== before) moved.push(Object.assign(desc(e), {from: Math.round(before), to: Math.round(e.scrollTop)}));
      if (moved.length >= 6) break;
    }
    btn.scrollIntoView({block: 'center', inline: 'nearest'});
    return {ok: true, moved: moved};
  }
  if (op === 'mark') {
    document.querySelectorAll('[data-metis-submit-token]').forEach(e => e.removeAttribute('data-metis-submit-token'));
    btn.setAttribute('data-metis-submit-token', token);
    const r0 = btn.getBoundingClientRect();
    btn.scrollIntoView({block: 'center', inline: 'nearest'});
    const r1 = btn.getBoundingClientRect();
    return {ok: true, scrolled: Math.round(r0.top) !== Math.round(r1.top), text: txt(btn)};
  }
  const r = btn.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const inView = r.width > 0 && r.height > 0 && cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight;
  const hit = inView ? document.elementFromPoint(cx, cy) : null;
  const visible = !!(hit && (hit === btn || btn.contains(hit)));
  const out = {ok: true, same: btn.getAttribute('data-metis-submit-token') === token, visible: visible,
               enabled: !(btn.disabled || btn.getAttribute('aria-disabled') === 'true'), text: txt(btn)};
  if (!visible) {
    out.why_not = {viewport: [window.innerWidth, window.innerHeight], rect: bx(btn),
                   centre: [Math.round(cx), Math.round(cy)], in_viewport: inView,
                   occluder: hit ? Object.assign(desc(hit),
                     {rel: hit.contains(btn) ? 'ancestor' : (btn.contains(hit) ? 'descendant' : 'unrelated')}) : null,
                   clip_chain: clips(btn),
                   dialogs: [...document.querySelectorAll('[role=dialog], [role=alertdialog], [aria-modal=true]')]
                              .filter(e => { const q = e.getBoundingClientRect(); return q.width > 0 && q.height > 0; }).length};
  }
  return out;
}
"""

# What the submit click PRODUCED (live test #13987, 2026-09-29: submit was
# clicked, then no position and no order appeared, and the run log could not
# say what the terminal showed). "tag": mark every visible button present
# BEFORE the click. "diff": every visible button that was NOT there before
# (a confirmation modal's Confirm / Cancel, a rejection notice's OK), each
# with its text and box, plus the text of the smallest element holding all
# of them. Digit runs of 5+ are masked so an account number can never reach
# the run log (our own price / quantity are shorter); emails are masked.
# Clicks nothing.
POST_SUBMIT_JS = r"""
(args) => {
  const [op] = args;
  const vis = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const btnSel = 'button, [role=button], input[type=submit]';
  if (op === 'tag') {
    document.querySelectorAll('[data-metis-pre]').forEach(e => e.removeAttribute('data-metis-pre'));
    let n = 0;
    for (const b of document.querySelectorAll(btnSel)) if (vis(b)) { b.setAttribute('data-metis-pre', '1'); n++; }
    return {ok: true, tagged: n};
  }
  const red = s => String(s || '').replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>').replace(/\d{5,}/g, '#####').trim();
  const txt = el => red(el ? (el.innerText || el.textContent || '') : '');
  const box = el => { const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  const fresh = [...document.querySelectorAll(btnSel)].filter(b => vis(b) && !b.hasAttribute('data-metis-pre'));
  document.querySelectorAll('[data-metis-new]').forEach(e => e.removeAttribute('data-metis-new'));
  const out = {ok: true, new_buttons: [], overlay_text: null, dialogs: document.querySelectorAll('[role=dialog], [role=alertdialog]').length};
  fresh.forEach((b, i) => {
    b.setAttribute('data-metis-new', String(i));
    out.new_buttons.push({i, text: (txt(b) || red(b.getAttribute('aria-label'))).slice(0, 80), box: box(b),
                          disabled: !!(b.disabled || b.getAttribute('aria-disabled') === 'true')});
  });
  if (fresh.length) {
    let e = fresh[0];
    while (e && e !== document.body && !fresh.every(b => e.contains(b))) e = e.parentElement;
    if (e && e !== document.body) out.overlay_text = txt(e).slice(0, 600);
  }
  return out;
}
"""

# Finds ONE row of the orders or positions table by an exact key cell and ONE
# control in it by an anchored pattern (text, aria-label or title), and tags
# that control data-metis-row-action. Never clicks.
ROW_ACTION_JS = r"""
(args) => {
  const [kind, keyCol, key, actionPat] = args;
""" + _PAIRED_TABLES_HELPER_JS + r"""
  const norm = s => s.replace(/\s+/g, ' ').trim().toLowerCase();
  document.querySelectorAll('[data-metis-row-action]').forEach(e => e.removeAttribute('data-metis-row-action'));
  const want = kind === 'orders' ? /^(order id|sts)$/ : /^(position volume|position id|open price|avg fill price|open p&l|fill price)$/;
  const act = new RegExp(actionPat, 'i');
  let rows = [], ctl = [], why = null;
  for (const p of paired) {
    const hs = p.headers.map(norm);
    if (!hs.some(h => want.test(h))) continue;
    const ci = hs.indexOf(norm(keyCol));
    if (ci < 0) continue;
    for (const r of p.trs) {
      const cells = [...r.querySelectorAll('td')].filter(c => c.closest('table') === r.closest('table'));
      if (cells[ci] && txt(cells[ci]) === key) rows.push(r);
    }
  }
  if (rows.length === 1) {
    const row = rows[0];
    ctl = [...row.querySelectorAll('button, [role=button], [title], [aria-label]')].filter(b =>
      act.test(txt(b)) || act.test(b.getAttribute('aria-label') || '') || act.test(b.getAttribute('title') || ''));
    // The chart draws the position and its legs as overlays with their own
    // x controls (close / cancel a leg): a row control must be INSIDE this
    // row of the positions table, boxed within it, and nowhere near a
    // canvas (operator 2026-09-29). Anything else is refused, never clicked.
    if (ctl.length === 1) {
      const c = ctl[0], rb = row.getBoundingClientRect(), cb = c.getBoundingClientRect();
      const inRow = c.closest('tr') === row && cb.width > 0 && cb.height > 0
        && cb.left >= rb.left - 4 && cb.right <= rb.right + 4 && cb.top >= rb.top - 4 && cb.bottom <= rb.bottom + 4;
      const nearCanvas = !!(c.closest('canvas') || [...c.parentElement ? c.parentElement.children : []].some(e => e.tagName === 'CANVAS'));
      if (!inRow || nearCanvas) { why = inRow ? 'control sits beside a canvas' : 'control is not boxed inside its row'; ctl = []; }
      else c.setAttribute('data-metis-row-action', '1');
    }
  }
  return {rows: rows.length, controls: ctl.length, why};
}
"""


# The watched CLOSE, to the operator's flow (screenshots 2026-09-29, relayed
# by the manager 07:20Z): hover the Positions row -> three icons appear at
# its right end, reverse (two arrows) . modify (pencil) . close (x, orange)
# -> click ONLY the close, the LAST control of the row -> a "Close Position"
# modal: heading "Close <SYMBOL> <Buy|Sell> Position", a "Lots to Close"
# input pre-filled with the full size and a caption "<n> out of <n>", buttons
# Discard and Close Position (+ an x in the corner) -> click Close Position
# only after every read-back passes, else Discard.
#  "locate":   tag the ONE row of the positions table whose Symbol is the
#              venue symbol and return its facts (side, size, fill, SL, TP).
#  "controls": after the hover, list the row's controls; tag the close
#              control only when exactly one x-type control exists, it is the
#              LAST control in the row, no control reads as reverse/modify
#              is chosen, and it is boxed inside the row away from any canvas.
#  "modal":    read the modal back (heading, lots-to-close value, caption,
#              buttons) and tag confirm / discard / dismiss. Clicks nothing.
CLOSE_ROW_JS = r"""
(args) => {
  const [op, symbol] = args;
""" + _PAIRED_TABLES_HELPER_JS + r"""
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const box = el => { const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  // What a control SAYS (its own and its descendants' text / title /
  // aria-label) and what it is CALLED (class, href, data-icon, data-test-id,
  // name — its own and its descendants', split on - _ . / # : so that
  // "icon-close" reads as the word close). Live test #14191: the row's icon
  // trio is three text-less <button>s (no title, no aria-label), so the
  // name of the icon inside is the only thing that says which one closes.
  const attr = (d, a) => d.getAttribute ? (d.getAttribute(a) || '') : '';
  // Split on - _ . / # : AND on camelCase (review of #14216, round 5: the
  // ancestor rule reads by word, so "reverseBtn", "btnReverse",
  // "Row_reverseButton__a1b2c", "modifyOrder" and "closeAll" hid their
  // word from it): "reverseBtn" reads as "reverse Btn", "XMLHttp" as "XML Http".
  const tokens = s => (s || '').replace(/[-_./#:]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2');
  // The attributes an element is CALLED by. data-testid / data-action / id
  // added in round 5: <div data-action="reverse"> behind a framework
  // listener names itself in no other attribute.
  const NAME_ATTRS = ['class', 'href', 'xlink:href', 'data-icon', 'data-test-id', 'data-testid', 'data-action', 'name', 'id'];
  const names = d => NAME_ATTRS.map(a => attr(d, a)).filter(Boolean);
  // Kept as PARTS (one per attribute of each element) as well as joined: the
  // qualified-close rule below reads each part on its own, so a class
  // "icon-close" followed by an href "#i-close" is not "close i".
  const labelParts = el => [el, ...el.querySelectorAll('*')].flatMap(d =>
    [d === el ? txt(el) : '', attr(d, 'title'), attr(d, 'aria-label')].filter(Boolean).map(norm));
  const calledParts = el => [el, ...el.querySelectorAll('*')].flatMap(d => names(d).map(v => norm(tokens(v))));
  const label = el => labelParts(el).join(' ');
  const called = el => calledParts(el).join(' ');
  const hint = el => norm(label(el) + ' ' + called(el));
  // Markup for the log: whitespace folded; digit runs of 5+, hex / uuid runs
  // of 8+ and any data-*id value masked (a position id may be any of those).
  const snippet = el => (el.outerHTML || '').replace(/\s+/g, ' ')
    .replace(/(data-[\w-]*id[\w-]*=")[^"]*(")/gi, '$1#####$2').replace(/\d{5,}/g, '#####').replace(/[0-9a-f]{8,}/gi, '########').slice(0, 200);
  // The glyph / x spellings count from the LABEL only: a class token such
  // as "x-small" is not a close. A name says close only as the word close
  // (or cross); reverse / modify words anywhere disqualify, and so does a
  // QUALIFIED close (review of #14216: "icon-close-all" tokenised to "icon
  // close all" and read as a close — a close-all could flatten every
  // position): the token "all" anywhere, or any word right after "close"
  // other than icon / btn / button / svg / position / x, disqualifies.
  const CLOSE_RE = /(^|\s)(×|✕|✖|⨯|x|close)(\s|$)/i, CLOSE_NAME_RE = /(^|\s)(close|cross)(\s|$)/i,
        BAD_RE = /(reverse|flip|swap|⇄|⇆|↔|edit|modify|pencil|✎|✏)/i,
        QUAL_RE = /(^|\s)all(\s|$)|(^|\s)close\s+(?!(icon|btn|button|svg|position|x|×|✕)(\s|$))\S/i;
  const isQualified = el => [...labelParts(el), ...calledParts(el)].some(s => QUAL_RE.test(s));
  const isClose = el => (CLOSE_RE.test(label(el)) || CLOSE_NAME_RE.test(called(el))) && !BAD_RE.test(hint(el)) && !isQualified(el);
  if (op === 'locate') {
    document.querySelectorAll('[data-metis-close-row]').forEach(e => e.removeAttribute('data-metis-close-row'));
    const posWords = /^(position volume|position id|open price|avg fill price|open p&l|fill price)$/;
    const rows = [];
    for (const p of paired) {
      const hs = p.headers.map(norm);
      if (!hs.some(h => posWords.test(h))) continue;
      const ci = hs.indexOf('symbol'); if (ci < 0) continue;
      for (const r of p.trs) {
        const cells = [...r.querySelectorAll('td')].filter(c => c.closest('table') === r.closest('table')).map(txt);
        if ((cells[ci] || '').toUpperCase() === String(symbol).toUpperCase()) rows.push({r, hs, cells});
      }
    }
    if (rows.length !== 1) return {ok: false, rows: rows.length, why: 'need exactly 1 row for ' + symbol + ' (found ' + rows.length + ')'};
    const {r, hs, cells} = rows[0];
    const get = names => { for (const n of names) { const i = hs.indexOf(n); if (i >= 0) return cells[i]; } return null; };
    r.setAttribute('data-metis-close-row', '1');
    return {ok: true, rows: 1, facts: {side: get(['side', 'direction']),
            size: get(['size', 'position volume', 'position qty', 'qty', 'quantity', 'volume']),
            fill: get(['fill price', 'open price', 'avg fill price', 'entry price', 'average price']),
            sl: get(['stop loss', 'sl']), tp: get(['take profit', 'tp'])}};
  }
  if (op === 'controls') {
    const row = document.querySelector('[data-metis-close-row]');
    if (!row) return {ok: false, why: 'no located row'};
    document.querySelectorAll('[data-metis-row-action]').forEach(e => e.removeAttribute('data-metis-row-action'));
    // Clickable-looking things in the row. A CONTAINER — ANY element that
    // holds a pressable (button / role=button / a), whatever it matches
    // itself: the "sticky--actions-cell" of live test #14191 (class only),
    // and equally a <div class="row-icons">, a [title] or a role=button
    // wrapper around the trio (review of #14216: a wrapper that survived as
    // the outermost control read "close" from its descendants and its
    // CENTRE was the modify button) — is descended into and never counted.
    // Among the rest, outermost only (an icon inside its button counts
    // once). The control that gets pressed must itself be a pressable.
    const PRESS = 'button, [role=button], a';
    const INTERACTIVE = PRESS + ', [title], [aria-label], svg, [class*=icon], [class*=close]';
    const all = [...row.querySelectorAll(INTERACTIVE + ', [class*=action]')].filter(vis);
    const presses = all.filter(el => el.matches(PRESS));
    const isContainer = el => presses.some(p => p !== el && el.contains(p));
    const cands = all.filter(el => !isContainer(el));
    const ctls = cands.filter(el => !cands.some(o => o !== el && o.contains(el)));
    const desc = ctls.map(el => ({hint: hint(el).slice(0, 80), tag: el.tagName.toLowerCase(), pressable: el.matches(PRESS),
                                  box: box(el), html: snippet(el)}));
    const closeIdx = ctls.map((el, i) => isClose(el) ? i : -1).filter(i => i >= 0);
    let why = null, chosen = null;
    if (!ctls.length) why = 'the row shows no control';
    else if (closeIdx.length !== 1) why = closeIdx.length + ' close-type controls in the row (need exactly 1)';
    else if (closeIdx[0] !== ctls.length - 1) why = 'the close-type control is not the LAST control of the row';
    else if (!ctls[closeIdx[0]].matches(PRESS)) why = 'the close-type control is not a button (' + ctls[closeIdx[0]].tagName.toLowerCase() + ')';
    else {
      const c = ctls[closeIdx[0]], rb = row.getBoundingClientRect(), cb = c.getBoundingClientRect();
      const inRow = c.closest('tr') === row && cb.width > 0 && cb.height > 0
        && cb.left >= rb.left - 4 && cb.right <= rb.right + 4 && cb.top >= rb.top - 4 && cb.bottom <= rb.bottom + 4;
      const nearCanvas = !!(c.closest('canvas') || [...(c.parentElement ? c.parentElement.children : [])].some(e => e.tagName === 'CANVAS'));
      // A close-named pressable NESTED in another pressable (review of
      // #14216, F3: <button class="btn-reverse"><span role=button
      // class="icon-close"/></button>): the click bubbles to the outer
      // control, so it is never pressed. And every ancestor up to the row
      // is read by its own attributes: a reverse / modify / qualified name
      // on the way up disqualifies, whatever the chosen element says.
      // Anything CLICKABLE above the chosen control counts as an outer control
      // (review of #14216, round 4: a <div onclick>, a role=menuitem with a
      // tabindex, a role=link span all received the bubbled click): the
      // pressables plus every attribute-visible way an element takes a
      // click. A React / framework handler leaves NO attribute, so this
      // cannot see every clickable ancestor — the ancestor-NAME check below
      // (a reverse / modify / qualified name anywhere up to the row) stays
      // the main defence, and the modal read-back the last one.
      const CLICKY = PRESS + ', [onclick], [tabindex]:not([tabindex="-1"]), [role=link], [role=menuitem], [role=option], '
        + 'input[type=button], input[type=submit], summary, label';
      const outer = c.parentElement ? c.parentElement.closest(CLICKY) : null;
      const ancestors = []; for (let a = c.parentElement; a && a !== row.parentElement; a = a.parentElement) ancestors.push(a);
      const ownParts = a => [
        norm([attr(a, 'title'), attr(a, 'aria-label')].filter(Boolean).join(' ')),
        norm(tokens(names(a).join(' ')))].filter(Boolean);
      // The ROW itself is read by WORD (a row class such as "editable" or
      // "swappable" must not refuse every row). Every ancestor BETWEEN the
      // control and the row is read by SUBSTRING, like the chosen control
      // (review of #14216, round 5: at the word rule a wrapper "reverseBtn"
      // or "modifyOrder" behind a framework listener — no onclick, tabindex
      // or role to see — hid its word and the close under it was pressed).
      // tokens() now splits camelCase too, so the word rule on the row sees
      // "reverseButton" as well. A qualified close ("close all", "closeAll")
      // anywhere on the way up disqualifies.
      const ANCESTOR_BAD_RE = /(^|\s)(reverse|flip|swap|⇄|⇆|↔|edit|modify|pencil|✎|✏)(\s|$)/i;
      const badAncestor = ancestors.flatMap(a => ownParts(a).filter(s =>
        (a === row ? ANCESTOR_BAD_RE : BAD_RE).test(s) || QUAL_RE.test(s)))[0];
      // Bound for a public log (round 4: a data-test-id "pos-<id>" on the row
      // reached `why`): digit runs of 5+ and hex runs of 8+ masked here too.
      const maskText = s => (s || '').replace(/\d{5,}/g, '#####').replace(/[0-9a-f]{8,}/gi, '########');
      if (outer && row.contains(outer)) why = 'close control nested in another pressable (' + outer.tagName.toLowerCase() + ')';
      else if (badAncestor) why = 'close control sits under a reverse / modify / qualified ancestor ("' + maskText(badAncestor).slice(0, 40) + '")';
      else if (!inRow) why = 'the close control is not boxed inside its row';
      else if (nearCanvas) why = 'the close control sits beside a canvas';
      else { c.setAttribute('data-metis-row-action', '1'); chosen = closeIdx[0]; }
    }
    return {ok: chosen !== null, controls: desc, chosen, why};
  }
  if (op === 'modal') {
    document.querySelectorAll('[data-metis-modal-btn]').forEach(e => e.removeAttribute('data-metis-modal-btn'));
    // The modal: the smallest visible element holding both a "Lots to Close"
    // text and a "Close Position" button.
    const lotsLabel = [...document.querySelectorAll('body *')].filter(el => vis(el) && el.children.length <= 2 && /lots to close/i.test(txt(el)));
    if (!lotsLabel.length) return {ok: false, why: 'no "Lots to Close" in the page'};
    let modal = lotsLabel[0];
    while (modal && modal !== document.body && ![...modal.querySelectorAll('button, [role=button]')].some(b => /^close position$/i.test(txt(b)))) modal = modal.parentElement;
    if (!modal || modal === document.body) return {ok: false, why: 'no "Close Position" button near "Lots to Close"'};
    const btns = [...modal.querySelectorAll('button, [role=button]')].filter(vis);
    const confirm = btns.filter(b => /^close position$/i.test(txt(b)));
    const discard = btns.filter(b => /^(discard|cancel)$/i.test(txt(b)));
    // The dismiss fallback (the modal's own x) is NEVER the confirm button and
    // never anything that reads "position": an aria-label of "Close position"
    // on the "Close Position" button also matches /close/ (review of #14013:
    // with no Discard present, that button was tagged discard and a read-back
    // mismatch pressed it — a partial close).
    const aria = b => b.getAttribute('aria-label') || '';
    const dismiss = btns.filter(b => !confirm.includes(b) && !discard.includes(b)
      && !/position/i.test(txt(b) + ' ' + aria(b))
      && (/^(×|✕|✖|x)$/i.test(txt(b)) || /close|dismiss/i.test(aria(b))));
    const heading = [...modal.querySelectorAll('h1, h2, h3, h4, [class*=title], [class*=heading]')].map(txt).find(t => /position/i.test(t))
      || (modal.innerText || '').split('\n').map(x => x.trim()).find(t => /^close\s+\S+\s+(buy|sell)\s+position/i.test(t)) || null;
    const inputs = [...modal.querySelectorAll('input:not([type=hidden]):not([type=checkbox]), [role=spinbutton]')].filter(vis);
    const lots = inputs.length ? String(inputs[0].value !== undefined ? inputs[0].value : txt(inputs[0])) : null;
    const cap = ((modal.innerText || '').match(/([0-9]+(?:\.[0-9]+)?)\s*out of\s*([0-9]+(?:\.[0-9]+)?)/i) || []);
    if (confirm.length === 1) confirm[0].setAttribute('data-metis-modal-btn', 'confirm');
    if (discard.length === 1) discard[0].setAttribute('data-metis-modal-btn', 'discard');
    else if (dismiss.length === 1) dismiss[0].setAttribute('data-metis-modal-btn', 'discard');
    return {ok: true, heading, lots, caption: cap.length ? [cap[1], cap[2]] : null, inputs: inputs.length,
            buttons: btns.map(b => txt(b).slice(0, 30)), confirm: confirm.length, discard: discard.length + dismiss.length,
            confirm_enabled: confirm.length === 1 && !(confirm[0].disabled || confirm[0].getAttribute('aria-disabled') === 'true'),
            text: (modal.innerText || '').replace(/\s+/g, ' ').replace(/\d{5,}/g, '#####').slice(0, 300)};
  }
  return {ok: false, why: 'unknown op'};
}
"""


# The position EDIT dialog (PROP-TRAIL / DIALOG-MEASURE, 2026-10-01). The
# row's icon trio (reverse . modify . close, live test #14191) is text-less,
# so the pencil is chosen by the NAME of its icon, exactly as CLOSE_ROW_JS
# chooses the close. The dialog it opens has never been measured: these ops
# READ it and tag only controls INSIDE it, so modify_bracket never touches
# the docked sidebar order ticket (go-live review of #15316, blocker 2).
#  "controls": on the row CLOSE_ROW_JS "locate" tagged (after a hover), tag
#              the ONE modify-type pressable, boxed inside the row, away from
#              any canvas, with no close / reverse / qualified name.
#  "snapshot": mark every dialog-like element visible BEFORE the click, so
#              "dialog" can require one that APPEARED after it.
#  "dialog":   the ONE new visible dialog-like element: its text (masked),
#              whether it names the symbol, its inputs (label, value,
#              read-only), its mode selects, its buttons with geometry; tags
#              stop_loss / take_profit / quantity fields and submit / cancel
#              buttons, all inside it. Clicks nothing.
EDIT_DIALOG_JS = r"""
(args) => {
  const [op, symbol] = args;
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').trim().replace(/\s+/g, ' ');
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const box = el => { const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  const attr = (d, a) => d.getAttribute ? (d.getAttribute(a) || '') : '';
  const tokens = s => (s || '').replace(/[-_./#:]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2');
  const NAME_ATTRS = ['class', 'href', 'xlink:href', 'data-icon', 'data-test-id', 'data-testid', 'data-action', 'name', 'id'];
  const called = el => [el, ...el.querySelectorAll('*')].flatMap(d => NAME_ATTRS.map(a => attr(d, a)).filter(Boolean))
    .map(v => norm(tokens(v))).join(' ');
  const label = el => [el, ...el.querySelectorAll('*')].flatMap(d =>
    [d === el ? txt(el) : '', attr(d, 'title'), attr(d, 'aria-label')].filter(Boolean)).map(norm).join(' ');
  const mask = s => (s || '').replace(/\d{5,}/g, '#####').replace(/[0-9a-f]{8,}/gi, '########');
  const snippet = el => mask((el.outerHTML || '').replace(/\s+/g, ' ')
    .replace(/(data-[\w-]*id[\w-]*=")[^"]*(")/gi, '$1#####$2')).slice(0, 200);
  const PRESS = 'button, [role=button], a';
  const inBox = (inner, outer, pad) => inner[2] > 0 && inner[3] > 0 && inner[0] >= outer[0] - pad && inner[1] >= outer[1] - pad
    && inner[0] + inner[2] <= outer[0] + outer[2] + pad && inner[1] + inner[3] <= outer[1] + outer[3] + pad;
  if (op === 'controls') {
    const row = document.querySelector('[data-metis-close-row]');
    if (!row) return {ok: false, why: 'no located row'};
    document.querySelectorAll('[data-metis-edit-ctl]').forEach(e => e.removeAttribute('data-metis-edit-ctl'));
    const presses = [...row.querySelectorAll(PRESS)].filter(vis);
    const ctls = presses.filter(el => !presses.some(o => o !== el && o.contains(el)));
    const MOD_NAME = /(^|\s)(edit|modify|pencil)(\s|$)/i, MOD_GLYPH = /(✎|✏)/,
          BAD = /(close|cross|reverse|flip|swap|⇄|⇆|↔|(^|\s)all(\s|$)|(^|\s)(×|✕|✖|x)(\s|$))/i;
    const isMod = el => (MOD_NAME.test(called(el)) || MOD_NAME.test(label(el)) || MOD_GLYPH.test(label(el)))
      && !BAD.test(called(el) + ' ' + label(el));
    const desc = ctls.map(el => ({hint: mask(norm(label(el) + ' ' + called(el))).slice(0, 80),
                                  tag: el.tagName.toLowerCase(), box: box(el), html: snippet(el)}));
    const idx = ctls.map((el, i) => isMod(el) ? i : -1).filter(i => i >= 0);
    let why = null, chosen = null;
    if (!ctls.length) why = 'the row shows no control';
    else if (idx.length !== 1) why = idx.length + ' modify-type controls in the row (need exactly 1)';
    else {
      const c = ctls[idx[0]], rb = box(row), cb = box(c);
      const nearCanvas = !!(c.closest('canvas') || [...(c.parentElement ? c.parentElement.children : [])].some(e => e.tagName === 'CANVAS'));
      const outer = c.parentElement ? c.parentElement.closest(PRESS + ', [onclick]') : null;
      if (c.closest('tr') !== row || !inBox(cb, rb, 4)) why = 'the modify control is not boxed inside its row';
      else if (nearCanvas) why = 'the modify control sits beside a canvas';
      else if (outer && row.contains(outer)) why = 'the modify control is nested in another pressable';
      else { c.setAttribute('data-metis-edit-ctl', '1'); chosen = idx[0]; }
    }
    return {ok: chosen !== null, controls: desc, chosen, why};
  }
  const DLG = '[role=dialog], [role=alertdialog], [aria-modal=true], [class*=modal], [class*=Modal], [class*=dialog], [class*=Dialog], [class*=popup], [class*=Popup]';
  const outermost = els => els.filter(el => !els.some(o => o !== el && o.contains(el)));
  if (op === 'snapshot') {
    document.querySelectorAll('[data-metis-pre-dialog]').forEach(e => e.removeAttribute('data-metis-pre-dialog'));
    const pre = outermost([...document.querySelectorAll(DLG)].filter(vis));
    pre.forEach(e => e.setAttribute('data-metis-pre-dialog', '1'));
    return {ok: true, visible_before: pre.length};
  }
  if (op === 'dialog') {
    ['data-metis-edit-dialog', 'data-metis-edit-field', 'data-metis-edit-btn'].forEach(a =>
      document.querySelectorAll('[' + a + ']').forEach(e => e.removeAttribute(a)));
    const all = outermost([...document.querySelectorAll(DLG)].filter(vis));
    const fresh = all.filter(e => !e.hasAttribute('data-metis-pre-dialog') && !e.querySelector('[data-metis-pre-dialog]'));
    if (fresh.length !== 1) return {ok: false, why: fresh.length + ' new dialogs after the click (need exactly 1)',
                                    visible: all.length, fresh: fresh.length};
    const d = fresh[0], db = box(d);
    d.setAttribute('data-metis-edit-dialog', '1');
    const text = (d.innerText || '').replace(/\s+/g, ' ');
    const sym = String(symbol || '').toUpperCase();
    const names_symbol = !!sym && new RegExp('(^|[^A-Z0-9])' + sym + '([^A-Z0-9]|$)').test(text.toUpperCase());
    const prevLabel = inp => {
      const a = attr(inp, 'aria-label'); if (a) return a.trim();
      if (inp.id) { const l = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`); if (l) return txt(l); }
      let e = inp;
      for (let i = 0; i < 6 && e && e !== d; i++, e = e.parentElement) {
        for (let s = e.previousElementSibling; s; s = s.previousElementSibling) {
          if (s.matches('input, select, textarea') || s.querySelector('input, select, textarea')) break;
          const t = txt(s); if (t) return t.split(/\n/)[0].trim();
        }
      }
      return (attr(inp, 'placeholder') || '').trim();
    };
    const FIELDS = {stop_loss: /(stop\s*loss|^s\/?l\b)/i, take_profit: /(take\s*profit|^t\/?p\b)/i,
                    quantity: /(qty|quantity|lots?|volume|size|amount)/i};
    const inputs = [...d.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), [role=spinbutton]')].filter(vis);
    const ins = inputs.map(el => ({el, label: prevLabel(el), value: el.value !== undefined ? String(el.value) : txt(el),
      readonly: !!(el.readOnly || el.disabled || attr(el, 'aria-readonly') === 'true' || attr(el, 'aria-disabled') === 'true')}));
    const fields = {}, ambiguous = [];
    for (const [k, r] of Object.entries(FIELDS)) {
      const m = ins.filter(i => r.test(i.label) && !(k === 'quantity' && (FIELDS.stop_loss.test(i.label) || FIELDS.take_profit.test(i.label))));
      if (m.length === 1) { m[0].el.setAttribute('data-metis-edit-field', k);
        fields[k] = {label: mask(m[0].label).slice(0, 40), value: m[0].value, readonly: m[0].readonly}; }
      else if (m.length > 1) ambiguous.push(k);
    }
    // A quantity the dialog shows as TEXT (not an input) is read from it.
    const qtyText = (text.match(/(?:qty|quantity|lots?|volume|size|amount)\s*:?\s*([0-9]+(?:[.,][0-9]+)?)/i) || [])[1] || null;
    // The SL/TP Price-or-offset mode controls (probe #13816 saw one on the
    // sidebar): <select>s, comboboxes and checked radios, with what they read.
    const modeEls = [...d.querySelectorAll('select, [role=combobox], [role=listbox], input[type=radio]:checked, [aria-haspopup=listbox]')].filter(vis);
    const modes = modeEls.map(el => ({label: mask(prevLabel(el)).slice(0, 40),
      value: mask(el.tagName === 'SELECT' ? (el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '') :
                  (el.type === 'radio' ? (prevLabel(el) || el.value) : txt(el))).slice(0, 30)}));
    const btns = [...d.querySelectorAll('button, [role=button], input[type=submit]')].filter(vis);
    const bname = b => txt(b) || attr(b, 'aria-label') || b.value || '';
    const SUBMIT = /^(save|apply|modify|modify position|update|confirm|ok|place|submit)$/i, CANCEL = /^(cancel|discard)$/i;
    const sub = btns.filter(b => SUBMIT.test(bname(b).trim()));
    const can = btns.filter(b => CANCEL.test(bname(b).trim()));
    // The dialog's own x, never anything that reads "position" or buy / sell.
    const xs = btns.filter(b => !sub.includes(b) && !can.includes(b) && !/position|buy|sell/i.test(bname(b))
      && (/^(×|✕|✖|x)$/i.test(bname(b).trim()) || /(^|\s)(close|dismiss)(\s|$)/i.test(norm(attr(b, 'aria-label') + ' ' + tokens(attr(b, 'class'))))));
    if (sub.length === 1) sub[0].setAttribute('data-metis-edit-btn', 'submit');
    if (can.length === 1) can[0].setAttribute('data-metis-edit-btn', 'cancel');
    else if (!can.length && xs.length === 1) xs[0].setAttribute('data-metis-edit-btn', 'cancel');
    const sb = sub.length === 1 ? box(sub[0]) : null;
    return {ok: true, box: db, role: attr(d, 'role') || null, text: mask(text).slice(0, 400), names_symbol,
            inputs: ins.map(i => ({label: mask(i.label).slice(0, 40), value: mask(i.value).slice(0, 20), readonly: i.readonly})),
            fields, ambiguous, qty_text: qtyText, modes,
            buttons: btns.map(b => ({name: mask(bname(b)).slice(0, 30), box: box(b)})),
            submit: sub.length, cancel: can.length + (can.length ? 0 : xs.length),
            submit_in_box: sb ? inBox(sb, db, 0) : false,
            submit_enabled: sub.length === 1 && !(sub[0].disabled || attr(sub[0], 'aria-disabled') === 'true'),
            buy_sell_buttons: btns.filter(b => /^(buy|sell)\b/i.test(bname(b).trim())).length,
            sidebar_ticket_inside: !!d.querySelector('[data-test-id=BUY], [data-test-id=SELL], [data-test-id=symbol_input]')};
  }
  return {ok: false, why: 'unknown op'};
}
"""

def _mask_controls(controls: Any) -> List[Dict[str, Any]]:
    """The row controls' markup, bound for a PUBLIC log: through
    ``redact_text`` (credential-shaped runs, e-mails), then digit runs of 5+,
    hex / uuid runs of 8+ and data-*id values masked — a position id may be
    any of those (review of #14216: a hex or UUID id survives ``\\d{5,}``).
    The JS already masks; this is the belt on top of it."""
    out: List[Dict[str, Any]] = []
    for c in controls or []:
        c = dict(c) if isinstance(c, Mapping) else {"hint": str(c)}
        # The hint carries data-test-id / name / href values too (review of
        # #14216: a data-test-id "close-<positionid>" would reach the log).
        for key in ("html", "hint"):
            if c.get(key) is not None:
                c[key] = _mask_public_text(str(c[key]))
        out.append(c)
    return out


def _mask_public_text(s: Any) -> str:
    """Text bound for a PUBLIC log (a refusal reason that quotes a row's
    attributes, a control's markup or hint): ``redact_text`` first, then
    ``data-*id`` values, digit runs of 5+ and hex / uuid runs of 8+ masked
    (review of #14216, round 4: a data-test-id "pos-<id>" on the row reached
    the ``why`` string unmasked)."""
    t = redact_text(str(s if s is not None else ""))
    t = re.sub(r'(data-[\w-]*id[\w-]*=")[^"]*(")', r"\1#####\2", t, flags=re.I)
    return re.sub(r"[0-9a-f]{8,}", "########", re.sub(r"\d{5,}", "#####", t), flags=re.I)


def parse_price(text: Optional[str]) -> Optional[float]:
    """A watchlist price. DXtrade renders one price as several spans (the
    sidebar's ask measured as ``"###."`` + a raised ``"##"``, #13855), so
    innerText can carry a line break INSIDE the number (``"184.\n25"``,
    pipette ``"184.25\n3"``). Whitespace is dropped only when the text holds
    exactly one decimal point with digits around it; ``"184\n25"`` (no point)
    stays None rather than being read as 18425."""
    v = parse_number(text)
    if v is not None or text is None:
        return v
    t = str(text)
    if t.count(".") != 1:
        return None
    joined = re.sub(r"\s+", "", t)
    return float(joined) if re.fullmatch(r"\d+\.\d+", joined) else None


#: FALLBACK watchlist resolver for a terminal whose watchlist <table> carries
#: no <th> text (TRADEIFY-WIRE T4, MEASURED on tradeify_1, issue #15067 run
#: 36824837314: 0 Symbol/Bid/Ask header tables, one visible header-less table
#: with 23 ``tr.instrument`` rows, and a ``table_column_symbol`` test-id on the
#: page). Consulted ONLY when the <th> search finds NO Symbol/Bid/Ask table,
#: so a layout that has one (breakout_1) never reaches it. A candidate is a
#: table with ``tr.instrument`` rows and no texted <th>; its columns are the
#: ``[data-test-id^=table_column_]`` elements of its nearest ancestor (<= 8 up)
#: that holds any, excluding those inside ANOTHER table, named by the test-id
#: suffix. Alignment is PROVEN, never assumed: as many columns as the first
#: row has cells, and each column's horizontal span covers its cell's centre
#: (the table and its symbol/bid/ask columns must have width). The candidate
#: must sit in a ``widget(New)__container`` (<= 12 up; refused when none) that
#: holds no Orders / Positions widget menu (manager review of #15075, BLOCK 1:
#: the downstream scope check alone let a container-less Positions table's
#: Symbol cell be tagged clickable).
#: Returns ``{found: [{t, hs}], why: [...]}``; ``why`` says per candidate why
#: it was not taken (shapes and counts only, no cell text).
_SYMBOL_TEXT_JS = r"""
  function __metisSym(s) {
    const u = String(s == null ? '' : s).trim().toUpperCase();
    const m = /^([A-Z0-9]{2,10})\/([A-Z0-9]{2,10})$/.exec(u);
    const c = m ? m[1] + m[2] : u;
    return /^[A-Z0-9]{2,15}$/.test(c) ? c : '';
  }
"""

#: Every resolver that carries the fallback also carries ``__metisSym`` (above).
_COLUMN_HEADER_TABLES_JS = _SYMBOL_TEXT_JS + r"""
  function __metisColumnTables() {
    const n_ = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const t_ = el => (el.innerText || el.textContent || '').trim();
    const found = [], why = [];
    const trade = '[data-test-id=widget_menu_ORDERS],[data-test-id=widget_menu_POSITIONS]';
    for (const t of document.querySelectorAll('table')) {
      if ([...t.querySelectorAll('th')].some(h => h.closest('table') === t && n_(t_(h)))) continue;
      const row = [...t.querySelectorAll('tr.instrument, tr[data-row-id]')].find(r => r.closest('table') === t);
      if (!row) continue;
      const tds = [...row.querySelectorAll('td')].filter(c => c.closest('tr') === row);
      let box = null;
      for (let e = t.parentElement, i = 0; e && e !== document.body && i < 12; e = e.parentElement, i++) {
        const cls = typeof e.className === 'string' ? e.className.split(/\s+/) : [];
        if (cls.some(c => /^widget(New)?__container/.test(c))) { box = e; break; }
      }
      if (!box) { why.push(`${tds.length}-cell rows: no widget container within 12 ancestors`); continue; }
      if (box.querySelector(trade)) { why.push(`${tds.length}-cell rows: widget holds an Orders / Positions menu`); continue; }
      if (!t.getBoundingClientRect().width) { why.push(`${tds.length}-cell rows: table has no width`); continue; }
      let cols = null, colHost = null;
      for (let e = t, i = 0; e && e !== document.body && i < 8; e = e.parentElement, i++) {
        const cs = [...e.querySelectorAll('[data-test-id^="table_column_"]')]
          .filter(c => { const ct = c.closest('table'); return !ct || ct === t; });
        if (cs.length) { cols = cs; colHost = e; break; }
      }
      if (!cols) { why.push(`${tds.length}-cell rows: no table_column_* headers within 8 ancestors`); continue; }
      // The headers must belong to THIS table (manager review 2026-10-01
      // 09:18Z, reviewer's variant (b) on main: a header-less Positions table
      // sharing a widget container with an EMPTY watchlist borrowed the
      // watchlist's headers and was clicked). Both must hold: the ancestor
      // where the headers were found holds exactly one table, and the header
      // row's bottom edge sits directly above this table's top (<= 8 px gap).
      const nTables = colHost.querySelectorAll('table').length + (colHost.tagName === 'TABLE' ? 1 : 0);
      if (nTables !== 1) { why.push(`${tds.length}-cell rows: the table_column headers' ancestor holds ${nTables} tables (need exactly 1)`); continue; }
      const hBottom = Math.max(...cols.map(c => c.getBoundingClientRect().bottom)), tTop = t.getBoundingClientRect().top;
      if (!(hBottom <= tTop + 1 && tTop - hBottom <= 8)) {
        why.push(`${tds.length}-cell rows: the table_column header row does not sit directly above the table`); continue; }
      const hs = cols.map(c => n_((c.getAttribute('data-test-id') || '').slice('table_column_'.length)));
      if (cols.length !== tds.length) { why.push(`${cols.length} columns [${hs.join(',')}] vs ${tds.length} cells`); continue; }
      const need = new Set(['symbol', 'bid', 'ask']);
      const off = cols.findIndex((c, i) => {
        const a = c.getBoundingClientRect(), b = tds[i].getBoundingClientRect();
        if (!a.width || !b.width) return need.has(hs[i]) || a.width !== b.width;
        const mid = b.x + b.width / 2;
        return mid < a.x - 1 || mid > a.x + a.width + 1;
      });
      if (off >= 0) { why.push(`column ${off} (${hs[off]}) does not sit over its cell`); continue; }
      if (!(hs.includes('symbol') && hs.includes('bid') && hs.includes('ask'))) {
        why.push(`columns [${hs.join(',')}] lack symbol/bid/ask`); continue;
      }
      // Manager review of 221c459, BLOCK 2 (the variant with the empty
      // watchlist's <table> GONE): the header host must sit inside THIS
      // table's own widget, and the row under the bid / ask headers must hold
      // PRICES -- a Positions row ("Buy", "0.01") is not a quote row.
      if (!(box === colHost || box.contains(colHost))) {
        why.push(`${tds.length}-cell rows: the table_column headers sit outside the table's own widget`); continue; }
      const price = v => /^(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$/.test(String(v || '').trim());
      const bi = hs.indexOf('bid'), ai = hs.indexOf('ask');
      if (!price(t_(tds[bi])) || !price(t_(tds[ai]))) {
        why.push(`${tds.length}-cell rows: the bid / ask cells are not prices`); continue; }
      found.push({t, hs});
    }
    return {found, why};
  }
"""


#: The watchlist's ROWS, read directly. MEASURED 2026-09-29 (dry run #13898,
#: run 36507086110): the table carrying the Symbol/Bid/Ask/Change/Chg%/
#: Description headers has ZERO body rows. The rows live in a separate
#: header-less table (a virtualised grid), which EXTRACT_TABLES_JS skips, so
#: every quote read returned None. The rows are the same
#: ``tr.instrument, tr[data-row-id]`` the ticket opener double-clicks.
WATCHLIST_ROWS_JS = r"""
([venue]) => {
  const txt = el => (el.innerText || el.textContent || '').trim();
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
""" + _COLUMN_HEADER_TABLES_JS + r"""
  let headers = null;
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(txt);
    const n = hs.map(norm);
    if (n.includes('symbol') && n.includes('bid') && n.includes('ask')) { headers = hs; break; }
  }
  if (!headers) { const fb = __metisColumnTables().found; if (fb.length === 1) headers = fb[0].hs; }
  const want = __metisSym(venue);
  const rows = [...document.querySelectorAll('tr.instrument, tr[data-row-id]')]
    .map(r => [...r.querySelectorAll('td')].map(txt))
    .filter(cells => want && cells.some(c => __metisSym(c) === want));
  return {headers, rows: rows.slice(0, 5)};
}
"""

# The SET of symbols the watchlist table shows, read-only (PROP-ETH-DOM,
# 2026-09-30, manager review of #14563): read before and after an
# instrument-probe so the run log shows whether typing into the watchlist's
# search box persisted anything to the (server-side) watchlist. Same header
# table + row selectors as WATCHLIST_ROWS_JS; the Symbol cell is taken from
# the header's own Symbol column and a row must align with the header (same
# cell count) to count. ``readable: false`` when there is not exactly one
# Symbol/Bid/Ask table -- "could not look", never an empty list.
WATCHLIST_SYMBOLS_JS = r"""
() => {
  const txt = el => (el.innerText || el.textContent || '').trim();
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
""" + _COLUMN_HEADER_TABLES_JS + r"""
  const tables = [];
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h)));
    if (hs.includes('symbol') && hs.includes('bid') && hs.includes('ask')) tables.push(hs);
  }
  if (!tables.length) tables.push(...__metisColumnTables().found.map(x => x.hs));
  if (tables.length !== 1) return {readable: false, why: `${tables.length} Symbol/Bid/Ask tables (need exactly 1)`};
  const hs = tables[0], si = hs.indexOf('symbol');
  const syms = [];
  for (const r of document.querySelectorAll('tr.instrument, tr[data-row-id]')) {
    const cells = [...r.querySelectorAll('td')].map(txt);
    if (cells.length !== hs.length) continue;
    const s = __metisSym(cells[si]);
    if (s) syms.push(s);
  }
  return {readable: true, symbols: [...new Set(syms)].sort()};
}
"""


def watchlist_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    """Compare two WATCHLIST_SYMBOLS_JS reads. ``changed`` is None when either
    read could not look (never collapsed into "no change")."""
    if not (before.get("readable") and after.get("readable")):
        return {"changed": None, "why": before.get("why") or after.get("why") or "not readable"}
    b, a = set(before.get("symbols") or []), set(after.get("symbols") or [])
    return {"changed": b != a, "n_before": len(b), "n_after": len(a),
            "added": sorted(a - b), "removed": sorted(b - a)}


# ── instrument INFO-PANEL probe (PROP-ETH-DOM, 2026-09-30) ───────────────
#
# OPERATOR DECISIONS, verbatim, relayed by the manager: popup ~08:00Z
# 2026-09-30 "Build an automated probe" (select a watchlist row, click
# ``instrument_info_button``, dump the panel read-only, close it) and ~08:20Z
# "I'll approve it in the lane". Pipeline PI-20260929-AQRK6CL1-0014. Live run
# issue #14612 MEASURED that the watchlist already holds the candidate symbols
# and that the search box surfaces no specs; issue #14551 MEASURED
# ``[data-test-id=instrument_info_button]`` (icon ``#icon-info``) beside a
# ``[data-test-id=symbol_input]`` in a widget-header toolbar.
#
# ⚠️ THIS IS THE SURFACE THREE REVIEW ROUNDS REJECTED (#13416; see the module
# docstring). Each rejection reason, and what answers it here:
#   R1 "page-wide text / a stale panel from the previous symbol / the symbol
#      never verified": nothing reads page text. The panel is the ONE new,
#      panel-sized element that appears after the info click (new = not in a
#      pre-click WeakSet of every element); it must name the requested symbol
#      as a whole token and NO OTHER watchlist symbol; it must be verified
#      GONE before the next symbol is selected.
#   R2 "wrong symbol's numbers (e.g. the order ticket's own Lot Size) / a
#      panel resolved wide / identical panels reused / link roles clicked":
#      the panel may hold no BUY or SELL control; its text is hashed and an
#      identical hash to an earlier symbol refuses; the linked symbol input
#      must read the requested symbol BEFORE the info click; every click is on
#      an element THIS probe tagged after checking it (a watchlist Symbol
#      CELL holding no control, the one info button, one close control inside
#      the verified panel) -- nothing is clicked by role or name.
#   R3 "it never converges": no named-field parser. The panel's own text
#      leaves are dumped in order; turning them into lot/tick numbers is a
#      reviewed follow-up against the measured shape.
# Defensive rules (manager brief, 2026-09-30 08:02Z): the action holds
# login.lock, so the executor tick skips while this runs; refuse unless the
# account reads FLAT without clicking a tab (Used Margin parsed and 0, and the
# ONE identified working-Orders widget found and empty); refuse UNLESS
# one-click positively reads OFF (fail-closed, manager review of #14645 -- the
# live reading is 'unknown', #13711, so -probe refuses there until a reader is
# built from the dump -dry records); refuse on any dialog open before the run
# or appearing after a row click; re-check the Symbol cell for controls AFTER
# hover, immediately before the click; a REFUSED panel is never click-closed
# (one Escape, then abort); record the linked symbol first and RESTORE it on
# every exit path while no dialog is open -- a skipped or failed restore is an
# alert, a non-zero exit AND the executor's AUTO-REVERT latch, so no executor
# tick trades until it is re-selected and cleared; the watchlist symbol set
# must read the same before and after. A DOUBLE-click on a watchlist row
# opens the order ticket (open_order_ticket): this probe only ever issues a
# single click, on the Symbol cell, never on a Bid/Ask cell.

# Resolve every target WITHOUT clicking. Tags (only when every check passes):
# ``data-metis-row-cell=<SYM>`` on each requested symbol's Symbol cell (exactly
# one aligned watchlist row, a visible cell holding no control, not inside a
# BUY+SELL panel), ``data-metis-info-btn`` on the one visible info button,
# ``data-metis-sym-input`` on its linked symbol input (the one visible
# ``symbol_input`` in the nearest ancestor of the info button that holds
# any, walked at most 6 levels). Refuses when either sits in a BUY+SELL panel.
# CLICK-FREE measurement of the watchlist's shape, attached to an
# instrument-info DRY run when the resolver above does not find exactly one
# Symbol/Bid/Ask <table> (TRADEIFY-WIRE T4, issue #15033: tradeify_1 read 0
# such tables while its API lists the symbols). HEADER WORDS AND COUNTS ONLY:
# no cell value, no row text, no input value; digit runs masked. No click, no
# hover, no focus, no tag. Covers <table>s, ARIA grids, div-grids whose header
# row holds a "Symbol"/"Instrument" leaf, the document-wide instrument-row
# count, iframes, and data-test-id names mentioning a watchlist.
WATCHLIST_DUMP_JS = r"""
() => {
  const txt = el => (el.innerText || el.textContent || '').trim();
  const w = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase()
    .replace(/\d{3,}/g, m => '#'.repeat(m.length)).slice(0, 20);
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const tid = el => (el.getAttribute && el.getAttribute('data-test-id') || '').replace(/\d{3,}/g, '#').slice(0, 40);
  const chain = el => { const out = []; for (let e = el.parentElement; e && e !== document.body && out.length < 3; e = e.parentElement) { const t = tid(e); if (t) out.push(t); } return out; };
  const tables = [...document.querySelectorAll('table')].slice(0, 25).map(t => ({
    headers: [...t.querySelectorAll('thead th, tr:first-child th')].filter(h => h.closest('table') === t).map(h => w(txt(h))).slice(0, 14),
    body_rows: [...t.querySelectorAll('tr')].filter(r => r.closest('table') === t && r.querySelector('td')).length,
    instrument_rows: t.querySelectorAll('tr.instrument, tr[data-row-id]').length,
    visible: vis(t), test_ids: chain(t),
  }));
  const grids = [...document.querySelectorAll('[role=grid], [role=treegrid], [role=table]')].slice(0, 10).map(g => ({
    role: g.getAttribute('role'),
    headers: [...g.querySelectorAll('[role=columnheader]')].map(h => w(txt(h))).slice(0, 14),
    rows: g.querySelectorAll('[role=row]').length, visible: vis(g), test_ids: [tid(g), ...chain(g)].filter(Boolean),
  }));
  const divgrids = [];
  for (const leaf of document.querySelectorAll('body *')) {
    if (divgrids.length >= 10) break;
    if (leaf.children.length || !/^(symbol|instrument|name|market)$/i.test(txt(leaf))) continue;
    if (leaf.closest('table, [role=grid], [role=treegrid], [role=table]')) continue;
    let row = leaf;
    for (let i = 0; i < 5 && row && row.parentElement; i++) {
      row = row.parentElement;
      const kids = [...row.children];
      if (kids.length >= 3 && kids.every(k => txt(k).length <= 40)) {
        divgrids.push({tag: row.tagName.toLowerCase(), headers: kids.map(k => w(txt(k))).slice(0, 14),
                       visible: vis(row), test_ids: [tid(row), ...chain(row)].filter(Boolean)});
        break;
      }
    }
  }
  const named = [...document.querySelectorAll('[data-test-id]')].map(tid)
    .filter(t => /watch|instrument|symbol|quote|market/i.test(t));
  // Column-header elements and the cell geometry of each header-less table's
  // first row (TRADEIFY-WIRE T4, #15067): test-ids, tags and x/width only --
  // no cell text -- so a failed _COLUMN_HEADER_TABLES_JS match is diagnosable.
  const allT = [...document.querySelectorAll('table')];
  const xw = el => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.width)]; };
  const columns = [...document.querySelectorAll('[data-test-id^="table_column_"]')].slice(0, 40).map(c => ({
    tid: tid(c), tag: c.tagName.toLowerCase(), table: allT.indexOf(c.closest('table')),
    text: /^[a-z][a-z %&./()-]{0,19}$/i.test(txt(c)) ? w(txt(c)) : null,  // header words only
    in_thead: !!c.closest('thead'), xw: xw(c), parent: c.parentElement ? c.parentElement.tagName.toLowerCase() : null,
  }));
  const header_less = allT.map((t, i) => ({i, t})).filter(({t}) =>
    ![...t.querySelectorAll('th')].some(h => h.closest('table') === t && txt(h))).map(({i, t}) => {
    const row = [...t.querySelectorAll('tr.instrument, tr[data-row-id]')].find(r => r.closest('table') === t);
    if (!row) return null;
    let depth = null, box = null;
    for (let e = t.parentElement, k = 0; e && e !== document.body && k < 12; e = e.parentElement, k++) {
      const cls = typeof e.className === 'string' ? e.className.split(/\s+/) : [];
      if (cls.some(c => /^widget(New)?__container/.test(c))) { depth = k + 1; box = e; break; }
    }
    let wmDepth = null, wm = null;
    for (let e = t.parentElement, k = 0; e && e !== document.body && k < 12; e = e.parentElement, k++) {
      if (e.querySelector('[data-test-id=widget_menu_WATCHLIST]')) { wmDepth = k + 1; wm = e; break; }
    }
    return {table: i, widget_depth: depth, watchlist_menu_depth: wmDepth,
            watchlist_menu_scope_has_trade_menu: wm ? !!wm.querySelector('[data-test-id=widget_menu_ORDERS],[data-test-id=widget_menu_POSITIONS]') : null,
            widget_has_trade_menu: box ? !!box.querySelector('[data-test-id=widget_menu_ORDERS],[data-test-id=widget_menu_POSITIONS]') : null,
            cells: [...row.querySelectorAll('td')].filter(c => c.closest('tr') === row)
              .slice(0, 16).map(c => ({xw: xw(c), tid: tid(c)}))};
  }).filter(Boolean);
  // Row shapes (TRADEIFY-WIRE T4, #15096: a Symbol/Bid/Ask table with 0
  // accepted rows): per table holding instrument rows, each of its first 12
  // rows' td count and its first symbol-like cell (index + text; public
  // instrument names only, e.g. "ETH/USD" -- no price, no other cell).
  // A symbol-like cell must hold a LETTER and is digit-masked like every other
  // dump field; a table whose widget holds an Orders / Positions menu is never
  // read (manager review of #15101: an id-shaped Positions cell leaked).
  const symLike = /^(?=.*[A-Z])[A-Z0-9]{2,10}(\/[A-Z0-9]{2,10})?$/i;
  const dmask = s => s.replace(/\d{3,}/g, m => '#'.repeat(m.length));
  const tradeScoped = t => {
    for (let e = t.parentElement, k = 0; e && e !== document.body && k < 12; e = e.parentElement, k++) {
      const cls = typeof e.className === 'string' ? e.className.split(/\s+/) : [];
      if (cls.some(c => /^widget(New)?__container/.test(c)))
        return !!e.querySelector('[data-test-id=widget_menu_ORDERS],[data-test-id=widget_menu_POSITIONS]');
    }
    return false;
  };
  const row_diag = allT.map((t, i) => ({i, t, th: [...t.querySelectorAll('thead th, tr:first-child th')]
      .filter(h => h.closest('table') === t).map(h => w(txt(h))).slice(0, 14)}))
    .filter(({t}) => t.querySelector('tr.instrument, tr[data-row-id]') && !tradeScoped(t)).slice(0, 6).map(({i, t, th}) => ({
      table: i, th, rows: [...t.querySelectorAll('tr.instrument, tr[data-row-id]')].slice(0, 12).map(r => {
        const tds = [...r.querySelectorAll('td')].filter(c => c.closest('tr') === r);
        const k = tds.findIndex(c => symLike.test(txt(c)));
        return [tds.length, k, k >= 0 ? dmask(txt(tds[k]).toUpperCase()) : null];
      })}));
  return {
    columns, header_less, row_diag,
    tables, grids, divgrids,
    instrument_rows_total: document.querySelectorAll('tr.instrument, tr[data-row-id]').length,
    iframes: document.querySelectorAll('iframe').length,
    watchlist_test_ids: [...new Set(named)].slice(0, 30),
  };
}
"""

INFO_PROBE_RESOLVE_JS = r"""
([symbols]) => {
  const txt = el => (el.innerText || el.textContent || '').trim();
""" + _COLUMN_HEADER_TABLES_JS + r"""
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  for (const a of ['data-metis-row-cell', 'data-metis-info-btn', 'data-metis-sym-input', 'data-metis-wl-row'])
    document.querySelectorAll('[' + a + ']').forEach(e => e.removeAttribute(a));
  const out = {ok: false, targets: {}};
  const orderPanels = [];
  for (const b of document.querySelectorAll('[data-test-id=BUY]')) {
    for (let e = b.parentElement; e && e !== document.body; e = e.parentElement) {
      if (e.querySelector('[data-test-id=SELL]')) { orderPanels.push(e); break; }
    }
  }
  const inOrder = el => orderPanels.some(p => p.contains(el));
  out.dialogs = [...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true]')].filter(vis).length;
  const wl = [];
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h)));
    if (hs.includes('symbol') && hs.includes('bid') && hs.includes('ask')) wl.push({t, hs});
  }
  if (!wl.length) {
    const fb = __metisColumnTables();
    wl.push(...fb.found);
    out.column_headers = {found: fb.found.length, why: fb.why.slice(0, 5)};
  }
  if (wl.length !== 1) { out.why = `${wl.length} Symbol/Bid/Ask tables (need exactly 1)`; return out; }
  const hs = wl[0].hs, si = hs.indexOf('symbol');
  // Rows are scanned ONLY inside the watchlist's own widget (manager review of
  // #14885, finding a): the nearest widget(New)__container ancestor of the
  // Symbol/Bid/Ask table (<= 8 up; else the table itself). That scope must
  // hold no Orders / Positions widget menu, so a working-order or position
  // row can never be taken for a watchlist Symbol cell.
  let scope = wl[0].t, foundBox = false;
  for (let e = wl[0].t.parentElement, i = 0; e && e !== document.body && i < 8; e = e.parentElement, i++) {
    const cls = typeof e.className === 'string' ? e.className.split(/\s+/) : [];
    if (cls.some(c => /^widget(New)?__container/.test(c))) { scope = e; foundBox = true; break; }
  }
  // No widget(New)__container class (tradeify_1, #15126: the Symbol/Bid/Ask
  // header table and its 9-row body table are separate, as on Breakout, but
  // no ancestor carries that class): the nearest ancestor (<= 12 up) holding
  // exactly ONE widget_menu_WATCHLIST is the watchlist widget. The trade-menu
  // refusal below still applies to it.
  if (!foundBox) {
    for (let e = wl[0].t.parentElement, i = 0; e && e !== document.body && i < 12; e = e.parentElement, i++) {
      const n = e.querySelectorAll('[data-test-id=widget_menu_WATCHLIST]').length;
      if (n === 1) { scope = e; break; }
      if (n > 1) break;
    }
  }
  if (scope.querySelector('[data-test-id=widget_menu_ORDERS],[data-test-id=widget_menu_POSITIONS]')) {
    out.why = 'the watchlist scope also holds an Orders / Positions widget'; return out;
  }
  const rows = [];
  for (const r of scope.querySelectorAll('tr.instrument, tr[data-row-id]')) {
    const tds = [...r.querySelectorAll('td')];
    if (tds.length !== hs.length) continue;
    const sym = __metisSym(txt(tds[si]));
    if (!sym) continue;
    rows.push({sym, cell: tds[si]});
  }
  out.watchlist = [...new Set(rows.map(r => r.sym))].sort();
  const ctl = 'button, [role=button], a, input, select, textarea, [onclick]';
  for (const sym of [...new Set(symbols.map(s => __metisSym(s)).filter(Boolean))]) {
    const hit = rows.filter(r => r.sym === sym);
    const t = {n_rows: hit.length, clean: false};
    if (hit.length === 1) {
      const c = hit[0].cell;
      // The ONE resolved WATCHLIST row, tagged whatever the cell's state: the
      // opener's last resort double-clicks only this row, never a row found
      // by a page-wide selector (manager review 5932126267: a Positions /
      // Orders row can share tr.instrument / tr[data-row-id]).
      const wlRow = c.closest('tr');
      if (wlRow) wlRow.setAttribute('data-metis-wl-row', sym);
      t.cell_has_control = c.matches(ctl) || !!c.querySelector(ctl);
      t.cell_in_order_panel = inOrder(c);
      t.clean = vis(c) && !t.cell_has_control && !t.cell_in_order_panel;
      if (t.clean) c.setAttribute('data-metis-row-cell', sym);
    }
    out.targets[sym] = t;
  }
  const btns = [...document.querySelectorAll('[data-test-id=instrument_info_button]')].filter(vis);
  out.n_info_buttons = btns.length;
  if (btns.length !== 1) { out.why = `${btns.length} visible instrument_info_button (need exactly 1)`; return out; }
  const btn = btns[0];
  let host = null;
  for (let e = btn.parentElement, i = 0; e && e !== document.body && i < 6; e = e.parentElement, i++) {
    if (e.querySelector('[data-test-id=symbol_input]')) { host = e; break; }
  }
  const inputs = host ? [...host.querySelectorAll('[data-test-id=symbol_input]')].filter(vis) : [];
  out.n_linked_inputs = inputs.length;
  if (inputs.length !== 1) { out.why = `${inputs.length} symbol_input beside the info button (need exactly 1)`; return out; }
  if (inOrder(btn) || inOrder(inputs[0])) { out.why = 'the info button or its symbol input sits in a BUY+SELL panel'; return out; }
  btn.setAttribute('data-metis-info-btn', '1');
  inputs[0].setAttribute('data-metis-sym-input', '1');
  // The linked symbol is that input's value: a SYMBOL, the thing this probe
  // must restore afterwards -- not account data.
  out.linked_symbol = (inputs[0].value || '').trim().toUpperCase();
  out.ok = true;
  return out;
}
"""

# READ-ONLY state dump behind a symbol switch that did not take (PROP-ETH-DOM,
# manager 2026-10-01 04:33Z option (c)): live #15022 clicked SOLUSD's watchlist
# Symbol cell with one-click OFF and the linked symbol stayed ETHUSD. CLICKS,
# HOVERS, FOCUSES AND TYPES NOTHING; tags nothing. Per watchlist row: the
# symbol, row/cell class tokens and aria-selected (is a row already "selected"?),
# the cell rect and the element actually hit at the cell's centre
# (document.elementFromPoint -- an overlay such as the sidebar ticket would show
# here instead of the cell). The linked-symbol source: every
# [data-test-id=symbol_input] with its value (a symbol), visibility and a
# 4-level ancestor class chain. The sidebar ticket: every BUY+SELL panel's rect,
# which watchlist symbols its text names, and its buttons (data-test-id,
# aria-label, title, text; close / dismiss / cancel wording flagged). Dialogs
# counted. Every class token has its digits masked; every text value is capped
# and runs of 5+ digits are masked (the log is public).
LINK_STATE_DUMP_JS = r"""
() => {
  const txt = el => (el.innerText || el.textContent || '').trim();
""" + _COLUMN_HEADER_TABLES_JS + r"""
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const m = v => String(v == null ? '' : v).replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>')
                   .replace(/\d{5,}/g, d => '#'.repeat(d.length)).slice(0, 60);
  const cls = el => (typeof el.className === 'string' ? el.className.split(/\s+/) : [])
                      .filter(Boolean).slice(0, 8).map(c => c.replace(/\d/g, '#'));
  const rect = el => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round); };
  const desc = el => el ? {tag: el.tagName.toLowerCase(), cls: cls(el), tid: m(el.getAttribute('data-test-id'))} : null;
  const chain = (el, n) => { const out = []; for (let e = el && el.parentElement, i = 0; e && e !== document.body && i < n; e = e.parentElement, i++) out.push(desc(e)); return out; };
  const out = {dialogs: [...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true]')].filter(vis).length,
               viewport: [window.innerWidth, window.innerHeight]};
  const wl = [];
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h)));
    if (hs.includes('symbol') && hs.includes('bid') && hs.includes('ask')) wl.push({t, hs});
  }
  if (!wl.length) wl.push(...__metisColumnTables().found);
  out.n_watchlist_tables = wl.length;
  // Why rows may not match (#15046 / #15064 read the table with ZERO rows):
  // the raw shape of every Symbol/Bid/Ask table -- its header texts, how many
  // <tr> it holds, how many match the row selector, a td-count histogram, and
  // up to 8 rows' masked class tokens, td count and (only when it is
  // symbol-shaped) the text under the Symbol header. Plus the [role=row]
  // count in the table's own widget, in case the rows moved to a grid.
  out.table_diag = wl.map(({t, hs}) => {
    const trs = [...t.querySelectorAll('tr')].filter(r => r.closest('table') === t);
    const si = hs.indexOf('symbol'), hist = {};
    for (const r of trs) { const n = r.querySelectorAll('td').length; hist[n] = (hist[n] || 0) + 1; }
    let scope = t;
    for (let e = t.parentElement, i = 0; e && e !== document.body && i < 8; e = e.parentElement, i++) {
      if (cls(e).some(c => /^widget(New)?__container/.test(c))) { scope = e; break; }
    }
    return {headers: hs.map(h => m(h).slice(0, 24)), n_tr: trs.length,
            n_tr_selector: trs.filter(r => r.matches('tr.instrument, tr[data-row-id]')).length,
            td_count_hist: hist, n_role_row_in_widget: scope.querySelectorAll('[role=row]').length,
            n_tr_selector_in_widget: scope.querySelectorAll('tr.instrument, tr[data-row-id]').length,
            sample: trs.slice(0, 8).map(r => { const tds = [...r.querySelectorAll('td')];
              const sv = si >= 0 && tds[si] ? txt(tds[si]).toUpperCase() : '';
              return {cls: cls(r), n_td: tds.length, n_th: r.querySelectorAll('th').length,
                      selector: r.matches('tr.instrument, tr[data-row-id]'),
                      sym: /^[A-Z0-9]{2,15}$/.test(sv) ? sv : (sv ? `<${sv.length} chars>` : '')}; })};
  });
  const syms = [];
  if (wl.length === 1) {
    const hs = wl[0].hs, si = hs.indexOf('symbol');
    out.rows = [];
    // Rows are read in the watchlist's own widget, exactly as
    // INFO_PROBE_RESOLVE_JS reads them: on Breakout the Symbol/Bid/Ask header
    // table has ZERO body rows and the rows live in a separate table (#13898)
    // -- which is why #15046 / #15064 (table-only) read rows: [].
    let scope = wl[0].t;
    for (let e = wl[0].t.parentElement, i = 0; e && e !== document.body && i < 8; e = e.parentElement, i++) {
      if (cls(e).some(c => /^widget(New)?__container/.test(c))) { scope = e; break; }
    }
    out.rows_scope = scope === wl[0].t ? 'table' : 'widget';
    for (const r of scope.querySelectorAll('tr.instrument, tr[data-row-id]')) {
      const tds = [...r.querySelectorAll('td')];
      if (tds.length !== hs.length) continue;
      const c = tds[si], sym = __metisSym(txt(c));
      if (!sym) continue;
      syms.push(sym);
      const rr = c.getBoundingClientRect(), cx = rr.x + rr.width / 2, cy = rr.y + rr.height / 2;
      const inView = cx >= 0 && cy >= 0 && cx < window.innerWidth && cy < window.innerHeight;
      const hit = inView ? document.elementFromPoint(cx, cy) : null;
      out.rows.push({sym, row_cls: cls(r), row_aria_selected: r.getAttribute('aria-selected'),
                     cell_cls: cls(c), cell_rect: rect(c), cell_visible: vis(c), center_in_viewport: inView,
                     hit: desc(hit), hit_is_cell: !!hit && (hit === c || c.contains(hit)),
                     hit_in_row: !!hit && r.contains(hit), hit_chain: hit && !r.contains(hit) ? chain(hit, 4) : []});
    }
  }
  out.symbol_inputs = [...document.querySelectorAll('[data-test-id=symbol_input]')].map(i => ({
    value: m((i.value || '').trim().toUpperCase()), visible: vis(i), rect: rect(i), chain: chain(i, 4)}));
  const panels = [];
  for (const b of document.querySelectorAll('[data-test-id=BUY]')) {
    for (let e = b.parentElement; e && e !== document.body; e = e.parentElement) {
      if (e.querySelector('[data-test-id=SELL]')) { if (!panels.includes(e)) panels.push(e); break; }
    }
  }
  const closeWord = /\b(close|dismiss|cancel|hide|collapse)\b|^[x\u00d7\u2715]$/i;
  out.order_panels = panels.map(p => {
    // Tokens per text LEAF (innerText joins inline siblings: "ETHUSDBuySell").
    const tokens = new Set([...p.querySelectorAll('*')].filter(x => x.children.length === 0)
      .flatMap(x => txt(x).toUpperCase().match(/[A-Z0-9]+/g) || []));
    const btns = [...p.querySelectorAll('button, [role=button]')].filter(vis).slice(0, 30).map(b => {
      const lab = [b.getAttribute('aria-label'), b.getAttribute('title'), txt(b)].filter(Boolean).map(m);
      return {tid: m(b.getAttribute('data-test-id')), labels: lab, close_like: lab.some(v => closeWord.test(String(v).trim()))};
    });
    return {visible: vis(p), rect: rect(p), cls: cls(p), names_symbols: syms.filter(s => tokens.has(s)),
            n_inputs: p.querySelectorAll('input').length, buttons: btns,
            n_close_like: btns.filter(b => b.close_like).length};
  });
  return out;
}
"""

#: SHARED EXCLUSION PREDICATE (manager review of #15070 / #15075): defines
#: ``__metisIsWatchlistOrTradeTable(el)`` -- true when ``el`` sits in
#:   * the <th>-text watchlist: the widget(New)__container (<= 8 up, else the
#:     table) of any table with Symbol/Bid/Ask <th> texts -- that scope also
#:     holds Breakout's SEPARATE rows table (#13898);
#:   * a table_column watchlist (Tradeify, #15067 / #15075): the
#:     widget(New)__container (<= 12 up, else the 6th ancestor) of every
#:     ``[data-test-id^=table_column_]`` header, and ANY <table> holding
#:     ``tr.instrument`` / ``tr[data-row-id]`` rows -- which also covers a
#:     Positions / Orders table with no widget_menu_* button (unmeasured on
#:     Tradeify; manager review 2026-10-01 08:31Z);
#:   * an Orders / Positions widget: the widget(New)__container (<= 12 up)
#:     of a widget_menu_ORDERS / _POSITIONS button, else its 6th ancestor;
#:   * a quick-trade bar (Sell | qty | Buy beside the chart's symbol box,
#:     operator screenshot 2026-10-01): the smallest ancestor (<= 6 up) of a
#:     Buy-side control that also holds a Sell-side control.
#: A JS SNIPPET (a function declaration) for concatenation into any
#: evaluate body: ``r"""() => {""" + WATCHLIST_OR_TRADE_JS + r"""...}"""``.
#: The scopes are computed once, on first use, per enclosing evaluate call;
#: ``__metisWatchlistOrTradeScopes()`` returns them (the ticket-field pick
#: exempts only a scope that also holds the ticket field itself).
WATCHLIST_OR_TRADE_JS = r"""
  let __metisWOTScopes = null;
  function __metisIsWatchlistOrTradeTable(el) {
    return __metisWatchlistOrTradeScopes().some(s => s === el || s.contains(el));
  }
  function __metisWatchlistOrTradeScopes() {
    if (!__metisWOTScopes) {
      const n_ = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
      const t_ = e => (e.innerText || e.textContent || '').trim();
      const c_ = e => (typeof e.className === 'string' ? e.className.split(/\s+/) : []);
      const box = (e, n) => { for (let x = e.parentElement, i = 0; x && x !== document.body && i < n; x = x.parentElement, i++)
                                if (c_(x).some(c => /^widget(New)?__container/.test(c))) return x; return null; };
      const scopes = [];
      for (const t of document.querySelectorAll('table')) {
        const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => n_(t_(h)));
        if (hs.includes('symbol') && hs.includes('bid') && hs.includes('ask')) scopes.push(box(t, 8) || t);
        if (t.querySelector('tr.instrument, tr[data-row-id]')) scopes.push(t);
      }
      // table_column headers (Tradeify, #15075's __metisColumnTables layout):
      // their widget, else the header's 6th ancestor -- a container with no
      // widget class is still excluded.
      for (const c of document.querySelectorAll('[data-test-id^="table_column_"]')) {
        let b = box(c, 12);
        if (!b) { b = c; for (let i = 0; i < 6 && b.parentElement && b.parentElement !== document.body; i++) b = b.parentElement; }
        scopes.push(b);
      }
      // Quick-trade bars (manager 2026-10-01 08:58Z, operator screenshot: the
      // Chart widget shows "Sell 2,685.83 | qty 0.01 | 2,685.84 Buy" beside the
      // linked symbol box): the SMALLEST ancestor (<= 6 up) of any Buy-side
      // control that also holds a Sell-side control -- buttons, its qty field
      // and everything else inside it are excluded.
      const side = e => { const s = n_([e.getAttribute('data-test-id'), e.getAttribute('aria-label'),
                                        e.getAttribute('title'), t_(e)].filter(Boolean).join(' '));
                          return /\bbuy\b/.test(s) ? 'buy' : /\bsell\b/.test(s) ? 'sell' : ''; };
      const ctl = 'button, [role=button], [data-test-id=BUY], [data-test-id=SELL]';
      for (const c of document.querySelectorAll(ctl)) {
        if (side(c) !== 'buy') continue;
        for (let x = c.parentElement, i = 0; x && x !== document.body && i < 6; x = x.parentElement, i++)
          if ([...x.querySelectorAll(ctl)].some(o => side(o) === 'sell')) { scopes.push(x); break; }
      }
      for (const b of document.querySelectorAll('[data-test-id=widget_menu_ORDERS],[data-test-id=widget_menu_POSITIONS]')) {
        let w = box(b, 12);
        if (!w) { w = b; for (let i = 0; i < 6 && w.parentElement && w.parentElement !== document.body; i++) w = w.parentElement; }
        scopes.push(w);
      }
      __metisWOTScopes = scopes;
    }
    return __metisWOTScopes;
  }
"""


# THE ORDER TICKET'S OWN SYMBOL FIELD -- the PRIMARY per-ticket switch
# (operator, by hand on breakout_1 and tradeify_1, ~09:00Z 2026-10-01;
# manager comment 5928196365 on #15070). Typing into the New Order panel's
# ``Symbol`` input opens a dropdown (tabs All | Cryptocurrencies | ...;
# columns Symbol | Description | Asset Class); picking the row whose Symbol
# cell is EXACTLY the target sets the ticket, and the submit label then names
# it. INFERRED from screenshots until a run measures the live shape.
#
# Resolve (no clicks; tags ``data-metis-ticket-sym``): the ONE visible
# ``[data-test-id=symbol_input]`` inside the ONE order panel (the smallest
# ancestor of a BUY that also holds a SELL and a symbol_input -- the same
# anchor ORDER_FORM_JS uses). Returns its value, the description text shown
# with it (the field's container text minus the value, capped), and the
# symbol the submit label names ("Buy 0.01 SOLUSD at ...") or null.
TICKET_SYMBOL_RESOLVE_JS = r"""
() => {
""" + _SYMBOL_TEXT_JS + r"""
  const txt = el => (el.innerText || el.textContent || '').trim();
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  document.querySelectorAll('[data-metis-ticket-sym]').forEach(e => e.removeAttribute('data-metis-ticket-sym'));
  const panels = [];
  for (const b of document.querySelectorAll('[data-test-id=BUY]')) {
    for (let e = b.parentElement; e && e !== document.body; e = e.parentElement) {
      if (e.querySelector('[data-test-id=SELL]') && e.querySelector('[data-test-id=symbol_input]')) {
        if (!panels.includes(e)) panels.push(e); break; }
    }
  }
  const out = {ok: false, n_panels: panels.length, dialogs: [...document.querySelectorAll(
    '[role=dialog],[role=alertdialog],[aria-modal=true]')].filter(vis).length};
  if (panels.length !== 1) { out.why = `${panels.length} order panels (need exactly 1)`; return out; }
  const ins = [...panels[0].querySelectorAll('[data-test-id=symbol_input]')].filter(vis);
  if (ins.length !== 1) { out.why = `${ins.length} visible symbol_input in the order panel (need exactly 1)`; return out; }
  const box = ins[0];
  box.setAttribute('data-metis-ticket-sym', '1');
  out.value = String(box.value || '').trim().toUpperCase();
  out.value_canon = __metisSym(out.value);
  // The description line: the first short visible text leaf AFTER the field
  // in document order inside the panel (the measured sidebar shows "SOL"
  // right under its Symbol input, probe #13816).
  out.desc = '';
  const after = [...panels[0].querySelectorAll('*')].filter(e => e.children.length === 0 && vis(e)
    && (box.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING) && txt(e));
  if (after.length) out.desc = txt(after[0]).replace(/\s+/g, ' ').replace(/\d{5,}/g, d => '#'.repeat(d.length)).slice(0, 40);
  // The submit label naming the ticket's symbol ("Buy 0.01 SOLUSD at 117.82",
  // or "Buy SOLUSD"): exactly one such button in the panel or its <= 3
  // ancestors (the measured submit is a footer OUTSIDE the panel); else null.
  const re = /^(buy|sell)\b(?:\s+[\d.,]+)?\s+([A-Za-z0-9][A-Za-z0-9.\/_-]*)(?:\s+at\b.*)?$/i;
  let scope = panels[0], verbs = [];
  for (let i = 0; scope && scope !== document.body && i <= 3; scope = scope.parentElement, i++) {
    verbs = [...scope.querySelectorAll('button, [role=button]')].filter(vis)
      .map(b => re.exec(txt(b).replace(/\s+/g, ' '))).filter(Boolean);
    if (verbs.length) break;
  }
  out.submit_symbol = verbs.length === 1 ? verbs[0][2].toUpperCase() : null;
  out.submit_canon = out.submit_symbol ? __metisSym(out.submit_symbol) : null;
  out.ok = true;
  return out;
}
"""

#: The dropdown row to pick after typing into the tagged ticket field. A
#: candidate is the row (nearest tr / [role=row] / [role=option] / li) of a
#: VISIBLE element whose FULL text names the target (canonical, __metisSym --
#: ETH/USD == ETHUSD; a highlighted prefix is read on the whole row), inside a
#: dropdown: an ancestor (<= 8 up) carrying role listbox / grid / menu / option,
#: or one whose short text leaves include the column titles "symbol" AND
#: "description" (the measured-by-eye Symbol | Description | Asset Class
#: header), that is itself NEW since the pre-typing snapshot WHATEVER its role.
#: Never inside a control (button / [role=button] / a / [onclick]), never the
#: ticket field itself, never in a watchlist /
#: Orders / Positions / quick-trade scope (WATCHLIST_OR_TRADE_JS; a scope that
#: also holds the ticket field is the ticket's own), never a buy/sell control. Exactly one distinct row
#: is tagged ``data-metis-ticket-pick`` (on its Symbol leaf); otherwise nothing
#: is tagged and every exact-text leaf's shape and rejection reason is
#: returned (masked) so the live dropdown can be measured.
TICKET_SYMBOL_PICK_JS = r"""
([target]) => {
""" + WATCHLIST_OR_TRADE_JS + _SYMBOL_TEXT_JS + r"""
  const txt = el => (el.innerText || el.textContent || '').trim();
  const n_ = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const cls = el => (typeof el.className === 'string' ? el.className.split(/\s+/) : [])
                      .filter(Boolean).slice(0, 6).map(c => c.replace(/\d/g, '#'));
  document.querySelectorAll('[data-metis-ticket-pick]').forEach(e => e.removeAttribute('data-metis-ticket-pick'));
  const want = __metisSym(target);
  const field = document.querySelector('[data-metis-ticket-sym]');
  const pre = window.__metisPre;
  const isDropdown = e => {
    for (let x = e, i = 0; x && x !== document.body && i < 8; x = x.parentElement, i++) {
      if (/^(listbox|grid|menu|option)$/.test(x.getAttribute('role') || '')) return x;
      const leaves = [...x.querySelectorAll('*')].filter(y => y.children.length === 0 && vis(y)).slice(0, 400)
        .map(y => n_(txt(y))).filter(t => t && t.length <= 20);
      if (leaves.includes('symbol') && leaves.includes('description')) return x;
    }
    return null;
  };
  const sideCtl = e => !!e.closest('button, [role=button]') && /\b(buy|sell)\b/.test(n_(txt(e.closest('button, [role=button]'))));
  // A scope that also holds the ticket FIELD is the ticket's own region (its
  // BUY+SELL panel matches the quick-trade rule): a dropdown rendered inside
  // it is still the field's own dropdown. Every other scope -- a watchlist,
  // Orders / Positions, a quick-trade bar -- excludes the row.
  const blocked = r => __metisWatchlistOrTradeScopes().some(sc => (sc === r || sc.contains(r))
                                                           && !(field && sc.contains(field)));
  const found = [], rejected = [];
  // The SMALLEST visible element whose FULL text names the target (canonical,
  // __metisSym: ETH/USD == ETHUSD, SOLUSD.X is not SOLUSD) -- a highlighted
  // prefix (<span>ETH</span>USD) is matched on the whole row text.
  const names = e => want && __metisSym(txt(e)) === want;
  const ctl = 'button, [role=button], a, [onclick]';
  for (const el of document.querySelectorAll('body *')) {
    if (!names(el) || !vis(el) || [...el.children].some(c => names(c))) continue;
    if (field && (el === field || field.contains(el))) continue;
    if (el.closest(ctl)) {
      if (rejected.length < 6) rejected.push({tag: el.tagName.toLowerCase(), cls: cls(el), role: el.getAttribute('role') || '',
                                              why: 'inside_a_control', new_since_typing: !!pre && !pre.has(el)});
      continue;
    }
    const row = el.closest('tr, [role=row], [role=option], li') || el.parentElement || el;
    const dd = isDropdown(row);
    // The dropdown must be what the TYPING produced: its container is NEW
    // since the pre-typing snapshot, WHATEVER its role (manager review of
    // 221c459, BLOCK 1: a pre-existing role=grid Positions row was picked).
    // No snapshot, no pick.
    const why = blocked(row) ? 'watchlist_or_trade'
      : sideCtl(el) ? 'buy_sell_control'
      : !dd ? 'not_in_a_dropdown'
      : !pre || pre.has(dd) ? 'dropdown_not_new' : '';
    if (why) {
      if (rejected.length < 6) rejected.push({tag: row.tagName.toLowerCase(), cls: cls(row), role: row.getAttribute('role') || '',
                                              why, new_since_typing: !!pre && !pre.has(row)});
      continue;
    }
    if (!found.some(f => f.row === row)) found.push({row, leaf: el, dd});
  }
  const out = {n_candidates: found.length, rejected, dialogs: [...document.querySelectorAll(
    '[role=dialog],[role=alertdialog],[aria-modal=true]')].filter(vis).length,
    candidates: found.slice(0, 6).map(f => ({tag: f.row.tagName.toLowerCase(), cls: cls(f.row),
                                             role: f.row.getAttribute('role') || '', new_since_typing: !!pre && !pre.has(f.row)}))};
  // DIAGNOSTIC (PROP-ETH-DOM #15139: typing produced no dropdown row on
  // breakout_1): what the field reads after typing, and the TOP-LEVEL visible
  // elements new since the snapshot -- shape only, never their text.
  out.field_value = field ? (field.value || '') : null;
  out.new_tops = !pre ? null : [...document.querySelectorAll('body *')]
    .filter(e => !pre.has(e) && e.parentElement && pre.has(e.parentElement) && vis(e)).slice(0, 6)
    .map(e => { const r = e.getBoundingClientRect();
      return {tag: e.tagName.toLowerCase(), cls: cls(e), role: e.getAttribute('role') || '',
              n_leaves: [...e.querySelectorAll('*')].filter(y => y.children.length === 0).length,
              w: Math.round(r.width), h: Math.round(r.height)}; });
  if (found.length === 1) found[0].leaf.setAttribute('data-metis-ticket-pick', '1');
  return out;
}
"""

# Remember every element that exists now (a WeakSet on window -- no DOM
# attribute) so the panel can be identified as NEW after the info click.
# Returns the number of visible dialogs.
INFO_PROBE_SNAPSHOT_JS = r"""
() => {
  window.__metisPre = new WeakSet(document.querySelectorAll('*'));
  return [...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true]')]
    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).length;
}
"""

# Find the ONE new panel-sized element: not in the pre-click WeakSet, parent
# not itself new, >= 120x60 px, >= 3 text leaves. Tag it
# ``data-metis-info-panel`` and return its OWN text leaves (never page text)
# with the identity checks. Masking is DELIBERATELY looser than the module's
# 5+-digit convention (#14216), because the spec values this probe exists to
# read (100000, Max qty 10000, 0.00001, 0.01-1000.00, 1000000.00) are exactly
# what that rule destroys (manager round-4 review of #14645, measured in
# node). Masked here: integer runs of 7+ digits not touching a '.', digit
# groups split by spaces / hyphens totalling 8+ digits not preceded by a '.',
# runs of 8+ hex characters containing a digit not preceded by a '.', and
# e-mails. ONE_CLICK_DUMP_JS keeps the STRICT mask -- it sits near account
# chrome and is not the spec surface. ``confirm_like`` flags an element that reads like an order
# CONFIRMATION (a confirm / submit / OK / place / buy / sell control or
# wording) -- such a panel is refused and never clicked.
INFO_PROBE_PANEL_JS = r"""
([target, others]) => {
  const pre = window.__metisPre;
  if (!pre) return {found: false, why: 'no pre-click snapshot'};
  document.querySelectorAll('[data-metis-info-panel]').forEach(e => e.removeAttribute('data-metis-info-panel'));
  const txt = el => (el.innerText || el.textContent || '').trim();
  const mask = v => v.replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>')
                     .replace(/(?<![0-9a-f.])[0-9a-f]{8,}(?![0-9a-f])/gi, m => /\d/.test(m) ? '#'.repeat(m.length) : m)
                     .replace(/(?<![.\d])\d(?:[\s-]?\d){7,}/g, m => '#'.repeat(m.length))
                     .replace(/(?<![.\d])\d{7,}(?![.\d])/g, m => '#'.repeat(m.length)).slice(0, 80);
  const leaves = el => [...el.querySelectorAll('*')].filter(x => x.children.length === 0 && txt(x)).map(x => mask(txt(x)));
  const fresh = [...document.querySelectorAll('body *')]
    .filter(el => !pre.has(el) && !(el.parentElement && !pre.has(el.parentElement)));
  const panels = fresh.filter(el => {
    const r = el.getBoundingClientRect();
    return r.width >= 120 && r.height >= 60 && leaves(el).length >= 3;
  });
  const out = {found: false, n_new_top: fresh.length, n_panels: panels.length};
  if (panels.length !== 1) { out.why = `${panels.length} new panel-sized elements after the info click (need exactly 1)`; return out; }
  const p = panels[0];
  const tokens = new Set(txt(p).toUpperCase().match(/[A-Z0-9]+/g) || []);
  out.names_target = tokens.has(String(target).toUpperCase());
  out.names_others = others.filter(o => tokens.has(String(o).toUpperCase()));
  out.has_order_controls = !!p.querySelector('[data-test-id=BUY],[data-test-id=SELL]');
  out.is_dialog = p.matches('[role=dialog],[role=alertdialog],[aria-modal=true]');
  const actionWord = /^(confirm|submit|ok|yes|place|send|buy|sell)\b/i;
  out.confirm_like = [...p.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')]
      .some(b => [b.getAttribute('aria-label'), b.getAttribute('title'), b.value, (b.innerText || '').trim()]
                   .filter(Boolean).some(v => actionWord.test(String(v).trim())))
    || /\b(confirm|place order|submit order|are you sure)\b/i.test(txt(p));
  const all = leaves(p);
  out.leaves = all.slice(0, 120);
  out.truncated = all.length > 120;
  p.setAttribute('data-metis-info-panel', '1');
  out.found = true;
  return out;
}
"""

# Inside the tagged panel ONLY: tag its one close control (aria-label / title
# / whole text reads close, ×, ✕, or a data-test-id with a whole "close" word).
INFO_PROBE_CLOSE_JS = r"""
() => {
  const p = document.querySelector('[data-metis-info-panel]');
  if (!p) return {n: 0, why: 'panel not tagged'};
  document.querySelectorAll('[data-metis-close]').forEach(e => e.removeAttribute('data-metis-close'));
  const re = /^(close|close panel|close dialog|×|✕|x)$/i;
  const c = [...p.querySelectorAll('button, [role=button]')].filter(b => {
    const t = [b.getAttribute('aria-label'), b.getAttribute('title'), (b.innerText || '').trim()].filter(Boolean);
    return t.some(v => re.test(v.trim())) || /(^|[_-])close($|[_-])/i.test(b.getAttribute('data-test-id') || '');
  });
  if (c.length === 1) c[0].setAttribute('data-metis-close', '1');
  return {n: c.length};
}
"""

# The WORKING-ORDERS count, read from ONE positively identified widget --
# never from "any Orders-shaped table" (manager review of #14645: a hidden
# Orders grid plus a visible order-HISTORY table must not read as "empty").
# MEASURED anchor (issue #14612 dump): the Orders widget carries a
# ``[data-test-id=widget_menu_ORDERS]`` button. Required: exactly ONE such
# visible button; its widget container (nearest ``widget__container`` /
# ``widgetNew__container`` ancestor, <= 12 up -- MEASURED at level 10 by
# the 2nd dry run, issue #14754 run 36726271098, which is why the original
# 8-level walk read "no visible widget container") visible; inside it a header row
# naming Symbol plus Order ID or Order Type and NO history-shaped column
# (close / closed / execution / filled time -- INFERRED names); body rows are
# the ``tr`` whose cell count matches that header and whose Symbol cell is
# non-empty. Anything else is ``found: false`` ("could not look").
# VISIBILITY, fail-closed (manager review of #14764, fixture S3b: a visible
# container holding a display:none Orders table with a working order read
# found:true, n_rows:0 -- a hidden grid as "no working orders"): "shown" is a
# size AND no display:none / visibility:hidden / opacity 0 up the tree. Every
# widget_menu_ORDERS counts (exactly 1, shown); the container must be shown
# and hold no OTHER widget_menu_* button; exactly ONE matching header row,
# counted visible or not, and it must be shown; body rows are counted visible
# or not, and ANY hidden order-shaped row reads "could not look", never
# "empty".
INFO_PROBE_ORDERS_JS = r"""
() => {
  const txt = el => (el.innerText || el.textContent || '').trim().toLowerCase();
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const shown = el => {
    if (!vis(el)) return false;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse'
          || parseFloat(cs.opacity) === 0) return false;
    }
    return true;
  };
  const menus = [...document.querySelectorAll('[data-test-id=widget_menu_ORDERS]')];
  if (menus.length !== 1) return {found: false, why: `${menus.length} widget_menu_ORDERS (need exactly 1)`};
  if (!shown(menus[0])) return {found: false, why: 'widget_menu_ORDERS is not shown'};
  let w = null;
  for (let e = menus[0].parentElement, i = 0; e && e !== document.body && i < 12; e = e.parentElement, i++) {
    const cls = typeof e.className === 'string' ? e.className.split(/\s+/) : [];
    if (cls.some(c => /^widget(New)?__container/.test(c))) { w = e; break; }
  }
  if (!w || !shown(w)) return {found: false, why: 'no visible widget container around widget_menu_ORDERS'};
  const others = [...w.querySelectorAll('[data-test-id^=widget_menu_]')].filter(m => m !== menus[0]);
  if (others.length) return {found: false, why: `the Orders widget container also holds ${others.length} other widget_menu_* (not one widget)`};
  const hrows = [...w.querySelectorAll('tr')].map(r => ({r, h: [...r.querySelectorAll('th')].map(txt)})).filter(x => x.h.length);
  const hdr = hrows.filter(x => x.h.includes('symbol') && (x.h.includes('order id') || x.h.includes('order type')));
  if (hdr.length !== 1) return {found: false, why: `${hdr.length} working-orders header rows in the Orders widget (need exactly 1)`};
  if (!shown(hdr[0].r)) return {found: false, why: 'the working-orders header row is not shown (a hidden grid is could-not-look, never empty)'};
  const hs = hdr[0].h;
  if (hs.some(h => /\b(close|closed|execution|filled)\b.*\btime\b|\btime\b.*\b(close|closed)\b/.test(h)))
    return {found: false, why: 'the Orders widget shows a history-shaped table'};
  const si = hs.indexOf('symbol');
  const rows = [...w.querySelectorAll('tr')].filter(r => {
    const tds = [...r.querySelectorAll('td')];
    return tds.length === hs.length && txt(tds[si]);
  });
  const hidden = rows.filter(r => !shown(r)).length;
  if (hidden) return {found: false, why: `${hidden} order-shaped row(s) are not shown (could not look)`, n_rows_hidden: hidden};
  return {found: true, n_rows: rows.length, headers: hs};
}
"""

# READ-ONLY measurement behind INFO_PROBE_ORDERS_JS. MEASURED 2026-09-30
# (instrument-info-dry, issue #14714): exactly one visible
# ``widget_menu_ORDERS`` was found but "no visible widget container around"
# it -- the Orders widget's own container has never been dumped. For each
# ``widget_menu_*`` button (visible or not) it walks 12 ancestors and records,
# per level: tag, class tokens (5+ digit runs, separated 8+ digit groups and
# 8+ hex runs with a digit masked; 24+ char tokens dropped), whether it is
# visible, its size, how many tables it holds and whether any header row
# names Symbol / Order ID / Order Type / a close-type time column. Header
# words only -- never a cell, never a value. Clicks, focuses and tags nothing.
INFO_PROBE_ORDERS_DUMP_JS = r"""
() => {
  const txt = el => (el.innerText || el.textContent || '').trim().toLowerCase();
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const mask = v => v.replace(/(?<![0-9a-f])[0-9a-f]{8,}(?![0-9a-f])/gi, m => /\d/.test(m) ? '#'.repeat(m.length) : m)
                     .replace(/\d(?:[\s,.-]?\d){7,}/g, m => '#'.repeat(m.length))
                     .replace(/\d{5,}/g, m => '#'.repeat(m.length));
  const cls = e => (typeof e.className === 'string' ? e.className : '').split(/\s+/)
                     .filter(t => t && t.length < 24).map(mask).slice(0, 8);
  const heads = e => [...e.querySelectorAll('tr')].map(r => [...r.querySelectorAll('th')].map(txt)).filter(h => h.length);
  const menus = [...document.querySelectorAll('[data-test-id^=widget_menu_]')];
  return {n_menus: menus.length, menus: menus.slice(0, 12).map(m => {
    const chain = [];
    for (let e = m.parentElement, i = 0; e && e !== document.body && i < 12; e = e.parentElement, i++) {
      const r = e.getBoundingClientRect(), hs = heads(e);
      chain.push({level: i + 1, tag: e.tagName.toLowerCase(), cls: cls(e), visible: vis(e),
                  w: Math.round(r.width), h: Math.round(r.height), n_tables: e.querySelectorAll('table').length,
                  symbol_header: hs.some(h => h.includes('symbol')),
                  order_header: hs.some(h => h.includes('order id') || h.includes('order type')),
                  history_header: hs.some(h => h.some(x => /\b(close|closed|execution|filled)\b.*\btime\b/.test(x)))});
    }
    return {menu: mask(m.getAttribute('data-test-id') || '').slice(0, 40), visible: vis(m), chain};
  })};
}
"""

# READ-ONLY, after a not-found panel (live -probe issue #14831: the info
# click left ONE new top-level element that was not panel-sized, n_panels 0,
# so the run aborted and could not restore). Lists the NEW top-level elements
# still in the page -- the same "new" test as INFO_PROBE_PANEL_JS -- with tag,
# role, class tokens (every digit masked), size, whether it is visible and
# its text-leaf COUNT, so the info view's real shape is MEASURED from the run
# log instead of guessed. NO TEXT is emitted (independent review of #14841,
# nit d: the run log is PUBLIC and a short balance or account label would
# pass the panel mask). ``n_visible`` is
# what the recovery reads: 0 means nothing unidentified is still on screen.
# Clicks, focuses and tags nothing.
INFO_PROBE_NEW_ELEMENTS_JS = r"""
() => {
  const pre = window.__metisPre;
  if (!pre) return {readable: false, why: 'no pre-click snapshot'};
  const txt = el => (el.innerText || el.textContent || '').trim();
  const mask = v => v.replace(/\d/g, '#').slice(0, 24);
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const fresh = [...document.querySelectorAll('body *')]
    .filter(el => !pre.has(el) && !(el.parentElement && !pre.has(el.parentElement)));
  const leafCount = el => [...el.querySelectorAll('*')].concat([el])
    .filter(x => x.children.length === 0 && txt(x)).length;
  return {readable: true, n_new_top: fresh.length, n_visible: fresh.filter(vis).length,
          elements: fresh.slice(0, 10).map(el => {
            const r = el.getBoundingClientRect();
            return {tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
                    cls: (typeof el.className === 'string' ? el.className : '').split(/\s+/)
                           .filter(t => t && t.length < 24).map(mask).slice(0, 6),
                    w: Math.round(r.width), h: Math.round(r.height), visible: vis(el),
                    n_leaves: leafCount(el)};
          })};
}
"""

# The target's RAW watchlist quote cells for symbol-switch-dry (price_step
# decimals). Scoped EXACTLY like INFO_PROBE_RESOLVE_JS -- the watchlist's own
# widget, refusing when it also holds an Orders / Positions widget menu -- and
# returning ONLY the Symbol, Bid and Ask cells (manager review of #14885, nit
# b: the run log is PUBLIC, and a page-wide row scan could capture a position
# or working-order row's qty / entry / P&L). Read-only; clicks nothing.
WATCHLIST_QUOTE_RAW_JS = r"""
([symbol]) => {
  const txt = el => (el.innerText || el.textContent || '').trim();
""" + _COLUMN_HEADER_TABLES_JS + r"""
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const wl = [];
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h)));
    if (hs.includes('symbol') && hs.includes('bid') && hs.includes('ask')) wl.push({t, hs});
  }
  if (!wl.length) wl.push(...__metisColumnTables().found);
  if (wl.length !== 1) return {error: `${wl.length} Symbol/Bid/Ask tables (need exactly 1)`};
  const hs = wl[0].hs, si = hs.indexOf('symbol'), bi = hs.indexOf('bid'), ai = hs.indexOf('ask');
  let scope = wl[0].t, foundBox = false;
  for (let e = wl[0].t.parentElement, i = 0; e && e !== document.body && i < 8; e = e.parentElement, i++) {
    const cls = typeof e.className === 'string' ? e.className.split(/\s+/) : [];
    if (cls.some(c => /^widget(New)?__container/.test(c))) { scope = e; foundBox = true; break; }
  }
  // No widget(New)__container class (tradeify_1, #15126: the Symbol/Bid/Ask
  // header table and its 9-row body table are separate, as on Breakout, but
  // no ancestor carries that class): the nearest ancestor (<= 12 up) holding
  // exactly ONE widget_menu_WATCHLIST is the watchlist widget. The trade-menu
  // refusal below still applies to it.
  if (!foundBox) {
    for (let e = wl[0].t.parentElement, i = 0; e && e !== document.body && i < 12; e = e.parentElement, i++) {
      const n = e.querySelectorAll('[data-test-id=widget_menu_WATCHLIST]').length;
      if (n === 1) { scope = e; break; }
      if (n > 1) break;
    }
  }
  if (scope.querySelector('[data-test-id=widget_menu_ORDERS],[data-test-id=widget_menu_POSITIONS]'))
    return {error: 'the watchlist scope also holds an Orders / Positions widget'};
  const want = __metisSym(symbol), rows = [];
  for (const r of scope.querySelectorAll('tr.instrument, tr[data-row-id]')) {
    const tds = [...r.querySelectorAll('td')];
    if (!want || tds.length !== hs.length || __metisSym(txt(tds[si])) !== want) continue;
    rows.push([txt(tds[si]), txt(tds[bi]), txt(tds[ai])].map(v => v.slice(0, 24)));
  }
  return {headers: ['symbol', 'bid', 'ask'], rows: rows.slice(0, 2)};
}
"""

INFO_PROBE_PANEL_GONE_JS = r"""
() => {
  const p = document.querySelector('[data-metis-info-panel]');
  if (!p || !p.isConnected) return true;
  const r = p.getBoundingClientRect();
  return !(r.width > 0 && r.height > 0);
}
"""

#: Click-free: the visible workspace tabs, ``[{index, name, current}]``, each
#: tab's NAME span tagged ``data-metis-ws-name=<index>`` -- the only element a
#: workspace switch clicks (never ``workspace_close_button`` "Delete
#: Workspace"). MEASURED tradeify_1 probe #15217: ``li[data-test-id=
#: workspace_current|workspace_tab] > span[data-test-id=workspace_name]``.
WORKSPACE_TABS_JS = r"""
() => {
  document.querySelectorAll('[data-metis-ws-name]').forEach(e => e.removeAttribute('data-metis-ws-name'));
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const tabs = [...document.querySelectorAll('li[data-test-id=workspace_current], li[data-test-id=workspace_tab]')].filter(vis);
  return tabs.map((li, index) => {
    const names = [...li.querySelectorAll('[data-test-id=workspace_name]')].filter(vis);
    const name = names.length === 1 ? (names[0].innerText || names[0].textContent || '').trim().slice(0, 40) : null;
    if (names.length === 1) names[0].setAttribute('data-metis-ws-name', String(index));
    return {index, name, current: li.getAttribute('data-test-id') === 'workspace_current'};
  });
}
"""

#: Click-free, digit-masked summary of what the CURRENT workspace shows, recorded
#: per workspace by the opener's workspace fallback so a probe that finds no
#: order form says WHY (tradeify_1 probe #15296: all three other workspaces
#: "held no form", with nothing recorded about what they did hold). Counts and
#: masked labels only -- no values, no table body text (the log is public).
WORKSPACE_SUMMARY_JS = r"""
() => {
  const mask = v => (typeof v === 'string')
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>').replace(/\d/g, '#').slice(0, 30) : null;
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const personal = /user|profile|account|login|email/i;
  const n = s => [...document.querySelectorAll(s)].filter(vis).length;
  const lab = inp => {
    for (let e = inp, i = 0; i < 4 && e && e !== document.body; i++, e = e.parentElement) {
      for (let s = e.previousElementSibling; s; s = s.previousElementSibling) {
        if (s.matches('input, select, textarea') || s.querySelector('input, select, textarea')) break;
        const t = (s.innerText || s.textContent || '').trim(); if (t) return t.split(/\n/)[0];
      }
    }
    return inp.getAttribute('aria-label') || inp.getAttribute('placeholder') || '';
  };
  const inputs = [...document.querySelectorAll('input:not([type=hidden]), [role=spinbutton]')].filter(vis)
    .filter(i => !i.closest('tbody'));
  const tids = new Set();
  for (const el of document.querySelectorAll('[data-test-id]')) {
    if (!vis(el) || el.closest('tbody')) continue;
    const t = el.getAttribute('data-test-id') || '';
    if (personal.test(t)) continue;
    if (/order|ticket|buy|sell|stop|take|profit|loss|limit|market|lot|quantity|qty|side|symbol_input|sl|tp/i.test(t)) tids.add(mask(t));
    if (tids.size >= 40) break;
  }
  return {
    buy: n('[data-test-id=BUY]'), sell: n('[data-test-id=SELL]'), symbol_input: n('[data-test-id=symbol_input]'),
    inputs: inputs.length,
    input_labels: inputs.slice(0, 20).map(i => ({type: mask(i.getAttribute('type')), label: mask(lab(i))})),
    widget_tabs: [...document.querySelectorAll('[data-test-id=widget_tab]')].filter(vis).slice(0, 20)
      .map(e => mask(e.innerText || e.textContent || '')),
    order_tids: [...tids],
    canvases: n('canvas'),
  };
}
"""

#: Add-widget menu measurement (TRADEIFY-GOLIVE, operator decision ~21:50Z
#: 2026-10-01, "Runner does it"). tradeify_1's "My Trading Account" lost its
#: Watchlist and has no New Order panel (#15319/#15322). Before a runner adds
#: those widgets, the "+" (``widget_tab_add_button``) menu must be MEASURED.
#: ``WIDGET_MENU_STATE_JS(snapshot)``: click-free. The current workspace, the
#: visible "+" buttons (top-most first; the first is tagged
#: ``data-metis-wadd``) and the layout counts a menu must not change. With
#: ``snapshot`` it also remembers every visible element so
#: ``WIDGET_MENU_NEW_JS`` can list only what the click made appear.
WIDGET_MENU_STATE_JS = r"""
(snapshot) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const n = s => [...document.querySelectorAll(s)].filter(vis).length;
  const box = el => { const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  document.querySelectorAll('[data-metis-wadd]').forEach(e => e.removeAttribute('data-metis-wadd'));
  if (snapshot) window.__metisWmPre = new WeakSet([...document.querySelectorAll('body *')].filter(vis));
  const adds = [...document.querySelectorAll('[data-test-id=widget_tab_add_button]')].filter(vis)
    .sort((a, b) => (a.getBoundingClientRect().top - b.getBoundingClientRect().top)
                    || (a.getBoundingClientRect().left - b.getBoundingClientRect().left));
  if (snapshot && adds.length) adds[0].setAttribute('data-metis-wadd', '1');
  const cur = document.querySelector('li[data-test-id=workspace_current] [data-test-id=workspace_name]');
  return {
    workspace: cur ? (cur.innerText || cur.textContent || '').trim().slice(0, 40) : null,
    add_buttons: adds.map(box),
    widget_tabs: n('[data-test-id=widget_tab]'),
    widget_close: n('[data-test-id=widget_close_button]'),
    tables: n('table'),
  };
}
"""

#: Click-free: the elements that became visible since WIDGET_MENU_STATE_JS's
#: snapshot (the open menu), as short masked text items (digits -> '#',
#: emails -> '<email>', 40 chars). Personal-looking test ids are dropped.
WIDGET_MENU_NEW_JS = r"""
() => {
  const pre = window.__metisWmPre;
  if (!pre) return {error: 'no snapshot'};
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const mask = v => (typeof v === 'string')
    ? v.trim().replace(/\s+/g, ' ').replace(/\S+@\S+/g, '<email>').replace(/\d/g, '#').slice(0, 40) : null;
  const personal = /user|profile|account|login|email/i;
  const fresh = [...document.querySelectorAll('body *')].filter(el => vis(el) && !pre.has(el));
  const items = [], seen = new Set();
  for (const el of fresh) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 40 || t.includes('\n')) continue;
    if ([...el.children].some(c => (c.innerText || '').trim() === t)) continue;
    const tid = el.getAttribute('data-test-id') || '';
    if (personal.test(tid)) continue;
    const key = t + '|' + (el.getAttribute('role') || '');
    if (seen.has(key)) continue;
    seen.add(key);
    const r = el.getBoundingClientRect();
    items.push({text: mask(t), tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), tid: mask(tid) || null,
                box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]});
    if (items.length >= 80) break;
  }
  return {n_new: fresh.length, items};
}
"""

#: Click-free pick of ONE item in the open add-widget menu (TRADEIFY-GOLIVE
#: option B step 2). Among the elements that appeared since
#: WIDGET_MENU_STATE_JS's snapshot, the text LEAVES whose own text is exactly
#: ``label``; the menu must also show every MEASURED sibling (``siblings``,
#: widget-menu-probe #15350), so a different menu is never clicked into. The
#: one leaf is tagged ``data-metis-wpick``; anything else tags nothing.
WIDGET_MENU_PICK_JS = r"""
(args) => {
  const [label, siblings] = args;
  const pre = window.__metisWmPre;
  if (!pre) return {error: 'no snapshot'};
  document.querySelectorAll('[data-metis-wpick]').forEach(e => e.removeAttribute('data-metis-wpick'));
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const own = el => (el.innerText || '').trim();
  const fresh = [...document.querySelectorAll('body *')].filter(el => vis(el) && !pre.has(el));
  const leaves = fresh.filter(el => { const t = own(el); return t && !t.includes('\n')
    && ![...el.children].some(c => own(c) === t); });
  const texts = new Set(leaves.map(own));
  const missing = siblings.filter(s => !texts.has(s));
  const danger = '[data-test-id=BUY],[data-test-id=SELL],table,[data-test-id=widget_close_button],[data-test-id=workspace_close_button]';
  const hits = leaves.filter(el => own(el) === label);
  const safe = hits.filter(el => !el.closest(danger));
  const out = {n: hits.length, n_safe: safe.length, missing_siblings: missing, tagged: false};
  if (safe.length === 1 && hits.length === 1 && !missing.length) {
    safe[0].setAttribute('data-metis-wpick', '1');
    out.tagged = true;
  }
  return out;
}
"""

#: MEASURED add-widget menu (widget-menu-probe #15350, tradeify_1, code
#: 6750a9451): the sibling entries the menu must show before any item is
#: picked. "Market Depth" (DOM trading) and every other entry is never clicked.
WIDGET_MENU_MEASURED: Tuple[str, ...] = ("Chart", "Positions", "Watchlist", "Orders", "Order History",
                                         "Trade History", "Messages", "Market Depth", "Alerts")

#: Click-free: how many visible text leaves read exactly one of ``labels``
#: (labels that only ever appear inside the add-widget menu / its Watchlist
#: submenu), so "is a menu still open?" needs no snapshot.
MENU_LABELS_VISIBLE_JS = r"""
(labels) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const own = el => (el.innerText || '').trim();
  const want = new Set(labels);
  const hits = [...document.querySelectorAll('body *')].filter(el => vis(el) && want.has(own(el))
    && ![...el.children].some(c => own(c) === own(el)));
  return {n: hits.length, labels: [...new Set(hits.map(own))]};
}
"""

#: Labels MEASURED only inside the add-widget menu (#15350) and the Watchlist
#: submenu (#15373) -- never a widget tab or panel title on the terminal.
MENU_ONLY_LABELS: Tuple[str, ...] = ("Cash Movements", "Market Depth", "Time and Sales", "Private", "Public")

INFO_PROBE_CLEANUP_JS = r"""
() => {
  for (const a of ['data-metis-row-cell', 'data-metis-info-btn', 'data-metis-sym-input', 'data-metis-wl-row',
                   'data-metis-info-panel', 'data-metis-close', 'data-metis-ticket-sym', 'data-metis-ticket-pick'])
    document.querySelectorAll('[' + a + ']').forEach(e => e.removeAttribute(a));
  try { delete window.__metisPre; } catch (e) {}
}
"""


def info_probe_flat_guard(account: AccountSnapshot, orders: Mapping[str, Any]) -> Optional[str]:
    """Why the info probe must NOT click, or None when the account reads FLAT.

    Flat = Used Margin parsed and exactly 0 (an open position holds margin)
    AND the ONE positively identified working-Orders widget
    (INFO_PROBE_ORDERS_JS) was read WITHOUT clicking a tab and holds no row.
    "Could not look" refuses; it never passes."""
    if account.margin_used is None:
        return "Used Margin not readable (could not confirm no open position)"
    if account.margin_used != 0:
        return f"Used Margin reads {account.margin_used} (a position may be open)"
    if not orders.get("found"):
        return f"working Orders not readable ({orders.get('why') or 'unknown'}; could not confirm no working order)"
    if orders.get("n_rows"):
        return f"{orders['n_rows']} working order(s) on the account"
    return None


def info_probe_one_click_off(one_click: Optional[Mapping[str, Any]]) -> bool:
    """True only for a positive OFF read from the MEASURED toggle
    (``via == "data-value+knob"``, optionally ``+aria`` agreeing). The
    checkbox/aria fallback alone never passes the info probe's gate -- it is
    not how the live terminal renders the toggle (issues #14714, #14754)."""
    oc = one_click or {}
    return oc.get("state") == "off" and str(oc.get("via") or "").startswith("data-value+knob")


def info_probe_restore_latch_reason(got: Mapping[str, Any]) -> Optional[str]:
    """The AUTO-REVERT latch reason to write when the probe may have left
    the terminal's linked symbol changed, else None. Written by the tick
    into the executor's own ``halted`` latch, which every executor tick
    reads before trading (refuses every new entry, alerts) until the
    manager/operator clears it with ``executor-clear-halt``."""
    rest = got.get("restore") or {}
    if got.get("mode") != "click" or not rest:
        return None
    if rest.get("verified") is True:
        return None
    # MEASURED after #14831 (click-free dry #14836): the terminal's linked
    # symbol lives in the run's own PAGE -- a fresh page loads with the
    # original linked again. So when a CLICK-FREE read on a FRESH page (the
    # tick's ``fresh_page_check``, manager-approved 2026-09-30 18:00Z) reads
    # the original symbol with no dialog open, the run alerts only and no
    # latch is written. Anything else -- no check, unreadable, another
    # symbol, a dialog -- latches as before.
    fp = got.get("fresh_page_check") or {}
    if (fp.get("readable") is True and rest.get("original")
            and fp.get("linked_symbol") == rest.get("original") and fp.get("dialogs") == 0):
        return None
    return ("AUTO-REVERT: instrument-info-probe left the linked symbol unverified "
            f"(should be {rest.get('original')!r}; restore attempted={rest.get('attempted')}); "
            "re-select it on the terminal, then executor-clear-halt")


#: A quote whose spread is wider than this is not believed (a mis-aligned
#: column would pair two unrelated numbers).
MAX_QUOTE_SPREAD_FRAC = 0.02


def quote_from_watchlist_rows(got: Mapping[str, Any], venue_symbol: str) -> Optional[Dict[str, float]]:
    """``{"bid", "ask"}`` from :data:`WATCHLIST_ROWS_JS`'s result, or None.

    The header table and the row table are separate elements, so their
    alignment is PROVEN per row rather than assumed: the row must have exactly
    as many cells as there are headers, the cell under "Symbol" must be the
    venue symbol, 0 < bid <= ask, and the spread must be under
    :data:`MAX_QUOTE_SPREAD_FRAC`. Anything else is None (could not look)."""
    headers = list(got.get("headers") or [])
    if not headers:
        return None
    rows = [r for r in (got.get("rows") or []) if len(r) == len(headers)]
    return quote_from_tables([{"headers": headers, "rows": rows}], venue_symbol)


def quote_diagnostics(tables: Sequence[Mapping[str, Any]], venue_symbol: str) -> Dict[str, Any]:
    """Why :func:`quote_from_tables` found nothing, for the run log. Market
    data only: the headers of every table with a Symbol column, and the raw
    cells (``repr``, so a line break shows) of rows naming the symbol in a
    table that also has Bid and Ask columns."""
    out: Dict[str, Any] = {"symbol_tables": [], "rows": []}
    for t in tables:
        norm = [_norm(h) for h in (t.get("headers") or [])]
        if "symbol" not in norm:
            continue
        out["symbol_tables"].append({"kind": t.get("kind"), "headers": [str(h)[:30] for h in t.get("headers") or []],
                                     "rows": len(t.get("rows") or [])})
        if "bid" not in norm or "ask" not in norm:
            continue
        c_sym = norm.index("symbol")
        for row in t.get("rows") or []:
            if venue_symbol.upper() in (_cell(row, c_sym) or "").upper():
                out["rows"].append([repr(str(c))[:40] for c in row][:12])
    return out


_SLASH_SYMBOL_RE = re.compile(r"^([A-Z0-9]{2,10})/([A-Z0-9]{2,10})$")


def canonical_symbol(text: Any) -> str:
    """The venue symbol a watchlist cell names, or ``""``. Python twin of
    ``__metisSym`` (_SYMBOL_TEXT_JS): upper-cased and trimmed, with ONE slash
    between two alphanumeric parts dropped -- tradeify_1 displays ``ETH/USD``
    for the instrument whose API ``symbol`` is ``ETHUSD`` (operator
    screenshots 2026-10-01, login check #14993) -- while Breakout's
    ``ETHUSD`` is unchanged. Anything else that is not ``[A-Z0-9]{2,15}`` is
    ``""`` (not a symbol)."""
    u = str(text if text is not None else "").strip().upper()
    m = _SLASH_SYMBOL_RE.match(u)
    c = m.group(1) + m.group(2) if m else u
    return c if re.fullmatch(r"[A-Z0-9]{2,15}", c) else ""


def quote_from_tables(tables: Sequence[Mapping[str, Any]], venue_symbol: str) -> Optional[Dict[str, float]]:
    """``{"bid": .., "ask": ..}`` for ``venue_symbol`` from the watchlist table
    (headers Symbol/Bid/Ask, MEASURED run 36358563148). ``None`` when no
    table carries both, the symbol has no row, or the numbers do not parse.

    The plausibility rules of #13908 apply on EVERY path, because the rows
    of a headed table can now come from a paired header-less body table
    (EXTRACT_TABLES_JS): a row must have exactly as many cells as there are
    headers, 0 < bid <= ask, and the spread must be under
    :data:`MAX_QUOTE_SPREAD_FRAC`; otherwise the row is not a quote.

    The symbol cell is compared through :func:`canonical_symbol`; a venue
    symbol with no canonical form is None (an empty canonical never matches
    an empty one), and EXACTLY ONE distinct matching row is required --
    ``ETH/USD`` and ``ETHUSD`` both present refuse rather than take the first
    (manager review of #15101). The same row read twice (a table that is
    also a role=grid) is one distinct row."""
    want = canonical_symbol(venue_symbol)
    if not want:
        return None
    hits: Dict[Tuple[str, ...], Tuple[int, int, int]] = {}
    for t in tables:
        headers = list(t.get("headers") or [])
        norm = [_norm(h) for h in headers]
        if "symbol" not in norm or "bid" not in norm or "ask" not in norm:
            continue
        c_sym, c_bid, c_ask = norm.index("symbol"), norm.index("bid"), norm.index("ask")
        for row in t.get("rows") or []:
            if len(row) != len(headers):
                continue
            if canonical_symbol(_cell(row, c_sym)) == want:
                hits.setdefault(tuple(str(c) for c in row), (c_sym, c_bid, c_ask))
    if len(hits) != 1:
        return None
    (row, (c_sym, c_bid, c_ask)), = hits.items()
    bid, ask = parse_price(_cell(row, c_bid)), parse_price(_cell(row, c_ask))
    if (bid is not None and ask is not None and 0 < bid <= ask
            and (ask - bid) / ask <= MAX_QUOTE_SPREAD_FRAC):
        return {"bid": bid, "ask": ask}
    return None


def classify_ticket_surface(form: Mapping[str, Any]) -> str:
    """``dom`` / ``canvas_ticket`` / ``not_found`` from :data:`ORDER_FORM_JS`'s
    result. Pure. ``canvas_ticket`` is the design's feasibility stop (§ 4): an
    opened ticket area with canvas and no labelled inputs to type into."""
    if form.get("found"):
        return "dom"
    if (form.get("canvases_in_form") or 0) > 0:
        return "canvas_ticket"
    return "not_found"


def _fmt_num(x: float) -> str:
    """A number as the form should receive it: no exponent, no trailing zeros."""
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def verify_form_values(fields: Mapping[str, Mapping[str, Any]], want: Mapping[str, float],
                       rel_tol: float = 1e-9, abs_tol: Optional[Mapping[str, float]] = None) -> List[str]:
    """Pure: compare what the form SHOWS against what we meant to type.
    Returns the list of mismatches (empty = verified). A field we meant to set
    that the form does not show, or shows unparseable, is a mismatch.

    ``abs_tol`` names, per field, the venue's price increment: a PRICE field
    the terminal rounds to its tick (dry run #13965: 119.2002 shown as 119.2)
    is verified when it is within one increment of what was typed (criterion
    D3, "within one tick"). Quantity never gets a tolerance."""
    bad: List[str] = []
    for k, v in want.items():
        shown = parse_number((fields.get(k) or {}).get("value"))
        tol = float((abs_tol or {}).get(k) or 0.0)
        if shown is None:
            bad.append(f"{k}: not readable back")
        elif tol > 0 and abs(shown - float(v)) > tol + 1e-9:
            bad.append(f"{k}: typed {_fmt_num(v)}, form shows {_fmt_num(shown)} (more than one tick of {_fmt_num(tol)} away)")
        elif tol <= 0 and not math.isclose(shown, float(v), rel_tol=rel_tol, abs_tol=1e-12):
            bad.append(f"{k}: typed {_fmt_num(v)}, form shows {_fmt_num(shown)}")
    return bad


def price_tolerances(spec: "BracketSpec", want: Mapping[str, float]) -> Dict[str, float]:
    """The per-field read-back tolerance for a spec: one ``price_step`` on
    each PRICE field (stop loss, take profit, limit price), nothing on the
    quantity. Empty when the venue's increment is not declared."""
    ps = getattr(spec, "price_step", None)
    if not ps or not (float(ps) > 0):
        return {}
    return {k: float(ps) for k in ("stop_loss", "take_profit", "price") if k in want}


def verify_buttons_in_panel(form: Mapping[str, Any], keys: Sequence[str] = (
        "side_buy", "side_sell", "type_market", "type_limit", "submit"), slack: int = 10) -> List[str]:
    """Pure: every tagged control this run may CLICK sits inside the ticket
    panel (operator 2026-09-29: the chart toolbar's own "Sell <price>" /
    "<price> Buy" buttons execute a full lot instantly with one-click ON).
    A control is inside when it has a size and lies within the fields'
    COLUMN (``fields_box`` left..right): the chart sits beside the sidebar,
    never in its column. Vertically nothing is judged, because the sidebar
    scrolls internally (the side / type row scrolls above the panel's top
    once the brackets are on, the submit is a footer below the fields) and
    the DOM containment that found the control already holds. A form that
    reports no geometry (a terminal whose discovery predates this) is not
    refused here; the box checks on the submit itself still apply."""
    fb, boxes = form.get("fields_box"), form.get("button_boxes") or {}
    if not fb:
        return []
    bad: List[str] = []
    for k in keys:
        b = boxes.get(k)
        if not b:
            continue
        left, _top, width, height = b
        inside = (width > 0 and height > 0
                  and left >= fb[0] - slack and left + width <= fb[0] + fb[2] + slack)
        if not inside:
            bad.append(f"{k}: at {list(b)} is outside the ticket panel (fields column {list(fb)})")
    return bad


def submit_not_visible_why(why_not: Optional[Mapping[str, Any]]) -> str:
    """Pure: render ``SUBMIT_JS`` ``check``'s ``why_not`` block as ONE clause
    naming WHICH state blocked the submit.

    ``"submit: not visible at its centre"`` on its own collapses three states
    with three different fixes: the centre is OFF the viewport (scroll / size
    the viewport), a clipping ancestor does not paint it (scroll that
    ancestor), or something else is painted ON TOP of it (nothing to scroll --
    find out what). MEASURED 2026-10-02 on breakout_1: two live ETH LIMIT
    tickets refused six times between 04:34Z and 05:15Z with the submit box
    [1559, 1537, 298, 48], and the round-trip dry walk at 06:14Z (system-action
    issue #15464) reached ``form_verified`` on the IDENTICAL box -- so the
    geometry was never the problem and the refusal could not say what was.
    Returns "" when there is nothing to say."""
    if not why_not:
        return ""
    centre = list(why_not.get("centre") or [])
    if why_not.get("in_viewport") is False:
        return f"its centre {centre} is outside the {list(why_not.get('viewport') or [])} viewport"
    occ = why_not.get("occluder") or {}
    if occ:
        tid = f"[{occ.get('tid')}]" if occ.get("tid") else ""
        dialogs = why_not.get("dialogs") or 0
        extra = f", {dialogs} dialog(s) open" if dialogs else ""
        return (f"its centre {centre} paints {occ.get('tag')}{tid} at "
                f"{list(occ.get('box') or [])} ({occ.get('rel')}){extra}")
    return f"nothing is painted at its centre {centre}"


def form_names_symbol(form: Mapping[str, Any], venue_symbol: str) -> bool:
    """Pure: the form's visible text names ``venue_symbol`` as a whole word.
    An order typed into a ticket for the wrong symbol is the worst silent
    failure here, so this is checked at open AND again at read-back.

    When the form carries its own symbol input (the live sidebar's
    ``symbol_input``, probe #13816: its visible text says only "SOL"), THAT
    value decides: it must equal ``venue_symbol`` (letters/digits compared),
    and a mismatch refuses even if the text happens to name the symbol."""
    # One canonical spelling everywhere (canonical_symbol / __metisSym,
    # #15101): tradeify_1 shows ETH/USD for ETHUSD; SOLUSD.X is NOT SOLUSD.
    want = canonical_symbol(venue_symbol)
    if not want:
        return False
    if form.get("symbol_value"):
        return canonical_symbol(form["symbol_value"]) == want
    tokens = re.findall(r"(?<![A-Za-z0-9./_-])([A-Za-z0-9]+(?:/[A-Za-z0-9]+)?)(?![A-Za-z0-9./_-])",
                        str(form.get("form_text") or ""))
    return any(canonical_symbol(t) == want for t in tokens)


def verify_form_selection(form: Mapping[str, Any], side: str, order_type: str) -> List[str]:
    """Pure: compare the side and order type the form SHOWS as selected
    (``form["selected"]`` from :data:`ORDER_FORM_JS`) against what we meant.
    ``None`` (not readable back) and ``"ambiguous"`` are mismatches: a side
    the form cannot show is never assumed."""
    want = {"side": "buy" if side == "long" else "sell", "order_type": order_type}
    shown = form.get("selected") or {}
    bad: List[str] = []
    for k, v in want.items():
        got = shown.get(k)
        if got is None:
            bad.append(f"{k}: not readable back")
        elif got != v:
            bad.append(f"{k}: chose {v}, form shows {got}")
    return bad


#: The measured DXtrade submit label (probe #13855, 2026-09-28):
#: "Buy <qty> SOLUSD at <price>" -- the TERMINAL's own statement of what it is
#: about to send.
_SUBMIT_LABEL = re.compile(r"\b(buy|sell)\s+([0-9]+(?:[.,][0-9]+)?)\s+([A-Za-z0-9._/-]+)", re.IGNORECASE)


def submit_label_mismatch(text: str, spec: "BracketSpec") -> str:
    """'' when the submit label agrees with the spec, else why not.

    When the label states a quantity, it must equal ``spec.quantity`` and the
    symbol after it must be ``spec.venue_symbol``. This is the only place the
    terminal says what quantity it ACCEPTED: a venue that clamps a
    below-minimum size up (or rounds it) changes this number, and the dry run
    at the lot step is only a measurement of the venue minimum if that change
    would be caught. A label with no quantity passes (the check is then the
    input read-back alone).
    """
    m = _SUBMIT_LABEL.search(text or "")
    if not m:
        return ""
    try:
        qty = float(m.group(2).replace(",", "."))
    except ValueError:
        return f"its text {text!r} states an unparseable quantity"
    if abs(qty - float(spec.quantity)) > 1e-9:
        return f"its text {text!r} states quantity {qty}, not the typed {spec.quantity}"
    if canonical_symbol(m.group(3)) != canonical_symbol(spec.venue_symbol) or not canonical_symbol(m.group(3)):
        return f"its text {text!r} names {m.group(3)!r}, not {spec.venue_symbol}"
    return ""


def verify_bracket_legs(form: Mapping[str, Any]) -> List[str]:
    """Pure: both bracket legs are ARMED in the form. A leg whose enabling
    toggle is off would submit a NAKED order, the worst outcome here, so:
    when the form has any toggle-like control, each of SL and TP must have
    exactly one toggle and it must read on; when a leg shows an entry mode
    (the live sidebar's "Price" dropdown), both legs must read "Price"."""
    bad: List[str] = []
    toggles = form.get("checkboxes") or []
    fields = form.get("fields") or {}
    for leg in ("stop_loss", "take_profit"):
        mine = [t for t in toggles if t.get("field") == leg]
        if (form.get("toggle_candidates") or 0) > 0 or mine:
            if len(mine) != 1:
                bad.append(f"{leg}: {len(mine)} enabling toggles found (need exactly 1)")
            elif not mine[0].get("checked"):
                bad.append(f"{leg}: enabling toggle is OFF (would submit without the {leg})")
    modes = {leg: (fields.get(leg) or {}).get("mode") for leg in ("stop_loss", "take_profit")}
    # The live sidebar's shape (data-value toggles) always shows a mode
    # select: there it must be READABLE, so a changed label can never make
    # the Price check silently disappear (review of #13822).
    if (form.get("data_value_toggles") or 0) > 0 or any(m is not None for m in modes.values()):
        for leg, m in modes.items():
            if str(m or "").strip().lower() != "price":
                bad.append(f"{leg}: entry mode is {m!r}, need 'Price'")
    return bad


def _f_or_none(x: Any) -> Optional[float]:
    return parse_number(x) if x is not None else None


def _row_facts_mismatch(facts: Mapping[str, Any], side: Optional[str], quantity: Optional[float],
                        entry_price: Optional[float], rel_tol: float = 0.02) -> List[str]:
    """Pure: the located row's Side / Size / Fill Price against the position
    the caller means to close. A fact the caller did not give is not judged;
    a fact the row cannot show is a mismatch (never assumed)."""
    bad: List[str] = []
    if side is not None:
        shown = _side(facts.get("side"))
        if shown != side:
            bad.append(f"side: row shows {facts.get('side')!r}, want {side}")
    if quantity is not None:
        shown = parse_number(facts.get("size"))
        if shown is None or not math.isclose(shown, float(quantity), rel_tol=rel_tol, abs_tol=1e-9):
            bad.append(f"size: row shows {facts.get('size')!r}, want {_fmt_num(quantity)}")
    if entry_price is not None:
        shown = parse_number(facts.get("fill"))
        if shown is None or not math.isclose(shown, float(entry_price), rel_tol=rel_tol):
            bad.append(f"fill: row shows {facts.get('fill')!r}, want {_fmt_num(entry_price)}")
    return bad


def _close_modal_mismatch(modal: Mapping[str, Any], symbol: str, side: Optional[str],
                          quantity: Optional[float]) -> List[str]:
    """Pure: the "Close Position" modal read-back. It must name OUR symbol
    and side in its heading, offer exactly the full size as lots-to-close
    (input value and the "<n> out of <n>" caption agree with the position's
    size), and carry exactly one enabled "Close Position" button."""
    bad: List[str] = []
    if not modal.get("ok"):
        return [str(modal.get("why") or "modal not read")]
    head = str(modal.get("heading") or "")
    if symbol.upper() not in head.upper():
        bad.append(f"heading {head!r} does not name {symbol}")
    if side is not None:
        want = "buy" if side == "long" else "sell"
        other = "sell" if want == "buy" else "buy"
        if not re.search(rf"\b{want}\b", head, re.IGNORECASE) or re.search(rf"\b{other}\b", head, re.IGNORECASE):
            bad.append(f"heading {head!r} does not name our side ({want})")
    lots = parse_number(modal.get("lots"))
    cap = modal.get("caption") or []
    cap_n, cap_of = (parse_number(cap[0]), parse_number(cap[1])) if len(cap) == 2 else (None, None)
    if lots is None:
        bad.append("lots-to-close not readable")
    if cap_of is None:
        bad.append('"<n> out of <n>" caption not readable')
    if quantity is not None:
        for name, v in (("lots-to-close", lots), ("caption lots", cap_n), ("caption total", cap_of)):
            if v is not None and not math.isclose(v, float(quantity), rel_tol=1e-6, abs_tol=1e-9):
                bad.append(f"{name} {_fmt_num(v)} != the full size {_fmt_num(quantity)}")
    elif lots is not None and cap_of is not None and not math.isclose(lots, cap_of, rel_tol=1e-6, abs_tol=1e-9):
        bad.append(f"lots-to-close {_fmt_num(lots)} != the position size {_fmt_num(cap_of)}")
    if modal.get("confirm") != 1:
        bad.append(f"{modal.get('confirm')} \"Close Position\" buttons (need exactly 1)")
    elif not modal.get("confirm_enabled"):
        bad.append('"Close Position" is disabled')
    return bad


def edit_dialog_mismatch(dlg: Mapping[str, Any], symbol: str, quantity: Optional[float],
                         want: Sequence[str] = ("stop_loss", "take_profit")) -> List[str]:
    """Pure: is the dialog EDIT_DIALOG_JS "dialog" read the position's own
    SL/TP edit dialog, and safe to type into and submit? Every reason
    returned is a refusal (go-live review of #15316: a form that is not
    PROVABLY the position's could be the docked sidebar order ticket, whose
    submit places a NEW order). It must: have APPEARED after the click; name
    the symbol; show the quantity read-only and equal to the position's; hold
    no sidebar-ticket control and no Buy/Sell button; hold every SL/TP field
    we mean to type, editable and unambiguous; read its SL/TP mode as Price
    (an offset mode would turn an absolute price into a wrong stop); and own
    exactly one enabled submit, boxed inside it, and one cancel."""
    if not dlg.get("ok"):
        return [str(dlg.get("why") or "dialog not read")]
    bad: List[str] = []
    if not dlg.get("names_symbol"):
        bad.append(f"dialog does not name {symbol}")
    if dlg.get("sidebar_ticket_inside") or dlg.get("buy_sell_buttons"):
        bad.append("dialog holds order-ticket controls (Buy/Sell or the ticket's symbol input)")
    q = (dlg.get("fields") or {}).get("quantity")
    shown = parse_number(q.get("value")) if q else parse_number(dlg.get("qty_text"))
    if q and not q.get("readonly"):
        bad.append("quantity field is editable (a position's edit dialog shows it read-only)")
    if shown is None:
        bad.append("quantity not readable")
    elif quantity is None:
        bad.append("position size unknown: cannot match the dialog's quantity")
    elif not math.isclose(shown, float(quantity), rel_tol=1e-6, abs_tol=1e-9):
        bad.append(f"dialog quantity {_fmt_num(shown)} != the position's {_fmt_num(quantity)}")
    if dlg.get("ambiguous"):
        bad.append(f"ambiguous fields: {sorted(dlg['ambiguous'])}")
    for k in want:
        f = (dlg.get("fields") or {}).get(k)
        if not f:
            bad.append(f"no {k} field in the dialog")
        elif f.get("readonly"):
            bad.append(f"{k} field is read-only")
    modes = [str(m.get("value") or "").strip() for m in (dlg.get("modes") or [])]
    if not modes:
        bad.append("SL/TP mode not readable (need Price)")
    elif any(m.lower() != "price" for m in modes):
        bad.append(f"SL/TP mode reads {modes} (need Price)")
    if dlg.get("submit") != 1:
        bad.append(f"{dlg.get('submit')} submit buttons in the dialog (need exactly 1)")
    else:
        if not dlg.get("submit_in_box"):
            bad.append("submit is not boxed inside the dialog")
        if not dlg.get("submit_enabled"):
            bad.append("submit is disabled")
    if dlg.get("cancel") != 1:
        bad.append(f"{dlg.get('cancel')} cancel controls in the dialog (need exactly 1)")
    return bad

def check_bracket_spec(spec: BracketSpec) -> List[str]:
    """Pure structural check of one bracket before any click: both legs, a
    positive size, and SL/TP on the correct sides of the entry. Anything
    returned is a refusal."""
    bad: List[str] = []
    if spec.side not in ("long", "short"):
        bad.append(f"side {spec.side!r} is not long/short")
    for name in ("quantity", "stop_loss", "take_profit"):
        v = getattr(spec, name)
        if v is None or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            bad.append(f"{name} must be a positive number (got {v!r})")
    if spec.order_type not in ("limit", "market"):
        bad.append(f"order_type {spec.order_type!r} is not limit/market")
    if spec.order_type == "limit":
        if spec.limit_price is None or not (spec.limit_price > 0):
            bad.append("a limit bracket needs a positive limit_price")
        elif not bad:
            e, sl, tp = spec.limit_price, spec.stop_loss, spec.take_profit
            if spec.side == "long" and not (sl < e < tp):
                bad.append(f"long needs sl < entry < tp (got {sl} / {e} / {tp})")
            if spec.side == "short" and not (tp < e < sl):
                bad.append(f"short needs tp < entry < sl (got {tp} / {e} / {sl})")
    return bad


# ── the adapter ──────────────────────────────────────────────────────────


class DXtradeAdapter(PropPlatformAdapter):
    platform = "dxtrade"

    def __init__(self, timeout_ms: int = 30_000) -> None:
        self.timeout_ms = timeout_ms

    # ---- page probes ---------------------------------------------------
    @staticmethod
    def _visible(page: Any, key: str) -> bool:
        try:
            loc = page.locator(SELECTORS[key])
            return loc.count() > 0 and loc.first.is_visible()
        except Exception:
            return False

    def page_state(self, page: Any) -> str:
        keys = ("login_form", "login_error", "twofa_form", "twofa_backup_form",
                "password_expired_form", "captcha_frame")
        visible = {k: self._visible(page, k) for k in keys}
        parts = []
        for fr in self._frames(page):
            try:
                parts.append(fr.inner_text("body", timeout=5_000))
            except Exception:
                continue
        text = "\n".join(parts)
        try:
            title = page.title()
        except Exception:
            title = ""
        return classify_login_state(visible, text[:5_000], title)

    # ---- slice 1 -------------------------------------------------------
    def login(self, page: Any, login_url: str, username: str, password: str) -> None:
        if not username or not password:
            raise FeasibilityError("no_credentials", "username/password env vars are empty on the VM")
        page.goto(login_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        try:
            page.wait_for_selector(SELECTORS["login_form"], state="visible", timeout=self.timeout_ms)
        except Exception:
            state = self.page_state(page)
            if state in ("challenge", "captcha"):
                raise FeasibilityError(state, "bot challenge before the login form rendered")
            if state == "logged_in":
                return  # an existing session (not expected on a fresh context)
            raise FeasibilityError("unknown_page", f"login form never rendered (state={state}, "
                                                   f"at {self._where(page)})")
        state = self.page_state(page)
        if state in ("challenge", "captcha"):
            raise FeasibilityError(state, "bot challenge on the login page")

        page.fill(SELECTORS["login_username"], username)
        page.fill(SELECTORS["login_password"], password)
        page.click(SELECTORS["login_submit"], timeout=self.timeout_ms)

        # Wait for the terminal to render (a POSITIVE marker), or for a stop.
        deadline_ms, step_ms, waited = self.timeout_ms, 1_000, 0
        state = "login_form"
        while waited <= deadline_ms:
            page.wait_for_timeout(step_ms)
            waited += step_ms
            state = self.page_state(page)
            if state == "logged_in":
                return
            if state in ("challenge", "captcha", "2fa", "password_expired"):
                raise FeasibilityError(state, "after submitting the login form")
            if state == "login_error":
                raise FeasibilityError("login_rejected", "the terminal showed a login error")
        if state == "unknown":
            # The form went away but nothing a logged-in terminal renders
            # appeared. Not proof of a login, so not reported as one.
            raise FeasibilityError("unknown_page", f"no terminal marker after submit (at {self._where(page)})")
        raise FeasibilityError("timeout", "still on the login form after submit")

    def resume_session(self, page: Any, login_url: str, login_form_grace_ms: int = 5_000) -> str:
        """Open ``login_url`` in a context that already carries a SAVED session
        (Playwright storage_state) and report whether it is still logged in.

        Never fills, clicks or submits anything: with a live session the
        terminal renders on its own; without one the login form does. Returns
        ``"logged_in"`` only on the same POSITIVE terminal marker ``login``
        requires, otherwise the state it ended on (``login_form``,
        ``login_error``, ``unknown``). A login form is only believed after it
        has stayed up for ``login_form_grace_ms`` (a terminal may flash it
        before restoring the session). A challenge / CAPTCHA / 2FA /
        password-expired page RAISES :class:`FeasibilityError`: the caller must
        stop there, never answer it with a credential login.
        """
        page.goto(login_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        # Wall-clock budget: page_state() itself can take seconds (it reads
        # every frame), so counting only the sleeps could overrun the tick's
        # hard timeout.
        deadline = time.monotonic() + self.timeout_ms / 1000.0
        step_ms, form_ms, state = 1_000, 0, "unknown"
        while True:
            state = self.page_state(page)
            if state == "logged_in":
                return state
            if state in ("challenge", "captcha", "2fa", "password_expired"):
                raise FeasibilityError(state, "on resuming a saved session")
            if state == "login_error":
                return state
            form_ms = form_ms + step_ms if state == "login_form" else 0
            if form_ms > login_form_grace_ms or time.monotonic() >= deadline:
                return state
            page.wait_for_timeout(step_ms)

    @staticmethod
    def _where(page: Any) -> str:
        """Origin of the current page (no path, query or fragment)."""
        try:
            return _strip_url(page.url)
        except Exception:
            return "?"

    @staticmethod
    def _frames(page: Any) -> List[Any]:
        """Main frame first, then child frames. Fakes without frames: [page]."""
        try:
            frames = list(page.frames)
            return frames or [page]
        except Exception:
            return [page]

    def _page_text(self, page: Any) -> str:
        """Visible text of every frame (the terminal may render in an iframe)."""
        parts = []
        for fr in self._frames(page):
            try:
                parts.append(fr.inner_text("body", timeout=self.timeout_ms))
            except Exception:
                continue
        return "\n".join(parts)

    def _tables(self, page: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for fr in self._frames(page):
            try:
                out.extend(fr.evaluate(EXTRACT_TABLES_JS) or [])
            except Exception:
                continue
        return out

    def wait_ready(self, page: Any, timeout_ms: Optional[int] = None) -> bool:
        """Poll (read-only) until a balance or equity value is readable, up to
        ``timeout_ms``. Returns whether it became readable; never raises."""
        limit = self.timeout_ms if timeout_ms is None else timeout_ms
        waited = 0
        while True:
            try:
                snap = parse_account_metrics(self._page_text(page))
                if snap.balance is not None or snap.equity is not None:
                    return True
            except Exception:
                pass
            if waited >= limit:
                return False
            page.wait_for_timeout(1_000)
            waited += 1_000

    def page_shape(self, page: Any, secrets: Sequence[str] = ()) -> List[str]:
        """The redacted page-shape dump for a login that landed somewhere
        unrecognised (``unknown_page`` / ``timeout``). Read-only."""
        try:
            shape = page.evaluate(PAGE_SHAPE_JS) or {}
        except Exception as exc:
            shape = {"title": f"(page-shape probe failed: {type(exc).__name__})"}
        try:
            text = self._page_text(page)
        except Exception:
            text = ""
        return render_page_shape(shape, text, secrets)

    def structure(self, page: Any, secrets: Sequence[str] = ()) -> List[str]:
        """The redacted structure dump for a read that did not parse."""
        frames, struct = [], {}
        for i, fr in enumerate(self._frames(page)):
            try:
                n = len(fr.inner_text("body", timeout=5_000))
            except Exception:
                n = None
            frames.append({"url": getattr(fr, "url", ""), "text_len": n})
            try:
                got = fr.evaluate(STRUCTURE_JS, STRUCTURE_LABELS) or {}
            except Exception as exc:
                got = {"title": f"(structure probe failed: {type(exc).__name__})"}
            if i == 0:
                struct = got
            else:
                struct.setdefault("hits", []).extend(got.get("hits") or [])
        return render_structure(struct, frames, self._page_text(page), self._tables(page), secrets)

    @staticmethod
    def _tab_name_re(name: str) -> "re.Pattern[str]":
        # "Orders" or "Orders (2)" -- never "Order History" or "Place Order".
        return re.compile(r"^\s*" + re.escape(name) + r"(?:\s*\(\d+\))?\s*$")

    def _show_tab(self, page: Any, key: str) -> bool:
        """Click a VIEW tab (Positions / Orders) if one exists. View only.

        MEASURED (run 36351578802, issue #13345): Breakout's DXtrade renders
        the bottom-panel tabs as a span inside an element carrying a
        ``data-active`` attribute, NOT as ``role=tab`` (the page's 4
        ``role=tab`` elements are elsewhere). So: an ARIA tab whose name is
        exactly the tab name first, then a non-button ``[data-active]``
        element whose whole text is exactly the tab name. Never a button,
        never a partial-text match, so "Place Order" / "Buy" / "Sell" can
        never be the thing clicked.
        """
        name_re = self._tab_name_re(SELECTORS[key])
        candidates = []
        try:
            candidates.append(page.get_by_role("tab", name=name_re))
        except Exception:
            pass
        try:
            candidates.append(page.locator(
                "[data-active]:not(button):not([role=button]):not(input)").filter(has_text=name_re))
        except Exception:
            pass
        for loc in candidates:
            try:
                if loc.count() > 0:
                    loc.first.click(timeout=5_000)
                    page.wait_for_timeout(1_000)
                    return True
            except Exception:
                continue
        return False

    def read_account(self, page: Any) -> AccountSnapshot:
        return parse_account_metrics(self._page_text(page))

    def read_positions(self, page: Any) -> List[Position]:
        self._show_tab(page, "tab_positions")
        got = positions_from_tables(self._tables(page))
        if got is None:
            raise LookupError("no positions table found on the page (selector drift or unmeasured layout)")
        return got

    def read_orders(self, page: Any) -> List[WorkingOrder]:
        self._show_tab(page, "tab_orders")
        got = orders_from_tables(self._tables(page))
        if got is None:
            raise LookupError("no orders table found on the page (selector drift or unmeasured layout)")
        return got

    # ---- instrument specs via network response sniffing (see the
    # module-level note above start_response_capture's docstring) ------
    def start_response_capture(self, page: Any) -> List[CapturedResponse]:
        """Register a READ-ONLY ``page.on("response", ...)`` hook and
        return the (initially empty, then live-appended) list it fills.

        Call this BEFORE :meth:`login` so it also sees whatever the
        login/account/positions/orders flow itself triggers -- the
        terminal may only serve instrument metadata once, on session
        setup. This never clicks, fills or navigates anything -- it is
        purely passive, so it can be attached at any point in the flow
        without affecting what the balance/position/order reads see.

        The origin check fails CLOSED: if the page's own URL is empty,
        ``about:blank``, or raises, NOTHING is captured for that event --
        an unconfirmed origin is never treated as a match. Only a
        response whose ``content-type`` header contains ``"json"`` has its
        body read at all (``.text()`` is never called on anything else,
        so a large binary/streamed asset is never touched); a body is
        capped at :data:`_MAX_CAPTURED_BODY_CHARS`, and the whole list at
        :data:`_MAX_CAPTURED_RESPONSES`.
        """
        captured: List[CapturedResponse] = []

        def _on_response(response: Any) -> None:
            if len(captured) >= _MAX_CAPTURED_RESPONSES:
                return
            try:
                page_url = str(page.url or "").strip()
            except Exception:
                page_url = ""
            if not page_url or page_url.lower() == "about:blank":
                return  # fail CLOSED: no confirmed origin, capture nothing
            try:
                page_origin = urlsplit(page_url).netloc
                resp_origin = urlsplit(str(response.url or "")).netloc
            except Exception:
                return
            if not page_origin or not resp_origin or page_origin != resp_origin:
                return
            try:
                headers = response.headers
                if callable(headers):
                    headers = headers()
            except Exception:
                headers = {}
            ctype = str((headers or {}).get("content-type", "")).lower()
            if "json" not in ctype:
                return
            try:
                body = response.text()
            except Exception:
                return
            if len(body) > _MAX_CAPTURED_BODY_CHARS:
                body = body[:_MAX_CAPTURED_BODY_CHARS]
            captured.append(CapturedResponse(url=response.url, body=body))

        try:
            page.on("response", _on_response)
        except Exception:
            pass
        return captured

    # ---- step 3: order entry (see the ORDER ENTRY block above) ----------
    def _watchlist_rows(self, page: Any, venue_symbol: str) -> Dict[str, Any]:
        try:
            return page.evaluate(WATCHLIST_ROWS_JS, [venue_symbol]) or {}
        except Exception as exc:
            return {"error": type(exc).__name__}

    def read_quote(self, page: Any, venue_symbol: str) -> Optional[Dict[str, float]]:
        """Bid/ask off the watchlist. Read-only. A table carrying its own rows
        first; else the virtualised grid's rows (#13898)."""
        return (quote_from_tables(self._tables(page), venue_symbol)
                or quote_from_watchlist_rows(self._watchlist_rows(page, venue_symbol), venue_symbol))

    def quote_diagnostics(self, page: Any, venue_symbol: str) -> Dict[str, Any]:
        """Read-only: what the watchlist looked like when no quote parsed."""
        try:
            out = quote_diagnostics(self._tables(page), venue_symbol)
        except Exception as exc:
            out = {"error": type(exc).__name__}
        got = self._watchlist_rows(page, venue_symbol)
        out["watchlist_rows"] = {"headers": got.get("headers"), "error": got.get("error"),
                                 "rows": [[repr(str(c))[:40] for c in r][:12] for r in got.get("rows") or []]}
        return out

    def read_one_click(self, page: Any) -> Dict[str, Any]:
        """``{"state": "on"|"off"|"unknown", ...}``. Read-only DIAGNOSTIC,
        gated on by NOTHING (operator directive ~11:00Z 2026-10-01, manager
        comment 5930025023 on #14947): one-click arms only the INSTANT-trade
        controls (the chart's quick Buy/Sell and the Bid/Ask price cells),
        which no path here ever clicks (WATCHLIST_OR_TRADE_JS, the Symbol cell
        only, verify_buttons_in_panel); a ticket needs its own submit click
        whatever the toggle reads. An ``on`` read is remembered for ONE alert
        per run (``take_one_click_alert``); ``unknown`` is only recorded."""
        try:
            got = page.evaluate(ONE_CLICK_JS) or {}
        except Exception as exc:
            return {"state": "unknown", "why": f"probe failed ({type(exc).__name__})"}
        if got.get("state") not in ("on", "off"):
            got["state"] = "unknown"
        if got["state"] == "on":
            self._one_click_seen_on = True
        return got

    def take_one_click_alert(self) -> Optional[str]:
        """The ONE alert for this run when any read saw one-click ON, else
        None; later calls return None (alert once, never a refusal)."""
        if getattr(self, "_one_click_seen_on", False) and not getattr(self, "_one_click_alerted", False):
            self._one_click_alerted = True
            return ("one-click trading reads ON (informational, never a refusal: the executor clicks no "
                    "instant-trade control; operator directive 2026-10-01)")
        return None

    def _find_form(self, page: Any) -> Dict[str, Any]:
        try:
            return page.evaluate(ORDER_FORM_JS, [FORM_FIELD_PATTERNS, FORM_BUTTON_PATTERNS]) or {}
        except Exception as exc:
            return {"found": False, "error": type(exc).__name__}

    def open_order_ticket(self, page: Any, venue_symbol: str) -> Dict[str, Any]:
        """Open the order-entry form for ``venue_symbol`` WITHOUT pressing any
        order control. Returns ``{"opened": bool, "via": ..., "form": ...,
        "one_click": ...}``. ``one_click`` is the toggle's reading, recorded
        as a diagnostic only (ORDER ENTRY rule 1); nothing refuses on it.
        Never uses a chart Buy/Sell price button. Openers in order, each
        logged in ``tried``: a form already open; ONE click on the Symbol cell
        (kept only when the form then names the symbol); a ticket-opener
        button; the RESOLVED watchlist row's double-click (#13898 / #15139),
        skipped only on a positive one-click ON read; a Symbol-cell
        double-click."""
        out: Dict[str, Any] = {"opened": False, "via": None, "one_click": self.read_one_click(page), "form": {}}
        form = self._find_form(page)
        if form.get("found"):
            out.update(opened=True, via="already_open", form=form)
            return out
        # OPENERS, in order (operator directive ~12:35Z 2026-10-01, manager
        # comment 5931584062 on #14947): first what the operator does by hand
        # -- ONE single click on the symbol's watchlist Symbol CELL (the docked
        # New Order panel follows the clicked row), accepted only when the form
        # then NAMES the symbol. Every later opener is a FALLBACK so a ticket is
        # never missed because the new one did not take. ``tried`` logs each.
        sym = str(venue_symbol or "").strip().upper()
        out["tried"] = []
        if self._symbol_cell_click(page, sym, double=False):
            out["tried"].append("symbol_click")
            page.wait_for_timeout(1_000)
            form = self._find_form(page)
            if form.get("found") and form_names_symbol(form, sym):
                out.update(opened=True, via="symbol_click", form=form)
                return out
            self._dismiss_dialogs(page, out)
        for name in TICKET_OPENER_NAMES:
            try:
                loc = page.get_by_role("button", name=name, exact=True)
                if loc.count() == 1:
                    out["tried"].append(f"button:{name}")
                    loc.first.click(timeout=5_000)
                    page.wait_for_timeout(1_000)
                    form = self._find_form(page)
                    if form.get("found"):
                        out.update(opened=True, via=f"button:{name}", form=form)
                        return out
            except Exception:
                continue
        # The MEASURED opener: the watchlist-ROW double-click (row selector MEASURED in
        # dry run #13898; it opened the live ticket in #15139). Its centre can
        # be a Bid/Ask PRICE cell (fixture: both price buttons hit), an INSTANT
        # order with one-click ON -- the instant-trade exclusion the operator
        # kept (5930025023) -- so it never runs on a positive one-click ON
        # read. And only on the ONE row INFO_PROBE_RESOLVE_JS resolved inside
        # the watchlist (``data-metis-wl-row``), never a page-wide row search
        # that could hit a Positions / Orders row (manager review 5932126267).
        # It is SKIPPED only on a positive one-click ON read (the instant-trade
        # case). An unreadable toggle does NOT skip it: live #15187 (dry run on
        # 9f51c275) never reached it with one-click reading OFF at the start,
        # and a trade must never be missed for want of an opener (manager
        # 5931584062; before #15138 this opener ran with no one-click check).
        # Why it ran or not is RECORDED in ``last_resort``. It runs BEFORE the
        # Symbol-cell double-click: live #15214 (on 0e8d6e3de) resolved the
        # target row cleanly for both Symbol openers, then found it gone
        # (n_rows 0) after the Symbol-cell double-click, so the measured
        # opener had nothing left to click.
        oc = self.read_one_click(page)
        lr: Dict[str, Any] = {"one_click": {k: oc.get(k) for k in ("state", "via")}}
        out["last_resort"] = lr
        if oc.get("state") == "on":
            lr["skipped"] = "one-click reads ON: the row centre may be an instant-trade price cell"
        else:
            try:
                res = page.evaluate(INFO_PROBE_RESOLVE_JS, [[sym]]) or {}
                lr["resolve"] = {"ok": res.get("ok"), "why": res.get("why"),
                                 "target": (res.get("targets") or {}).get(sym),
                                 "watchlist": list(res.get("watchlist") or [])[:12]}
                row = page.locator(f"tr[data-metis-wl-row='{sym}']")
                lr["rows"] = row.count()
                if lr["rows"] == 1:
                    out["tried"].append("watchlist_dblclick")
                    row.first.dblclick(timeout=5_000)
                    page.wait_for_timeout(1_000)
                    form = self._find_form(page)
                    if form.get("found"):
                        out.update(opened=True, via="watchlist_dblclick", form=form)
                        return out
            except Exception as exc:
                lr["error"] = type(exc).__name__
            finally:
                try:
                    page.evaluate(INFO_PROBE_CLEANUP_JS)
                except Exception:
                    pass
        # Then: double-click the Symbol CELL (clean, hover-re-checked), never a
        # price. Unmeasured as an opener, and it may change the row, so it
        # runs only after the measured one.
        if not out.get("opened") and self._symbol_cell_click(page, sym, double=True):
            out["tried"].append("symbol_dblclick")
            page.wait_for_timeout(1_000)
            form = self._find_form(page)
            if form.get("found"):
                out.update(opened=True, via="symbol_dblclick", form=form)
                return out
        # FINAL fallback: another WORKSPACE (tradeify_1, probe #15217: the session sits on
        # a workspace with no New Order panel at all). A navigation click on a
        # workspace tab's NAME -- never its close / delete control -- kept only
        # when that workspace shows an order form; else the original workspace
        # is restored. Never reached when any opener above found the form.
        ws = self._open_ticket_on_another_workspace(page, out)
        if ws is not None:
            out.update(opened=True, via=f"workspace:{ws['name']}", form=ws["form"])
            return out
        out["form"] = form
        out["refused"] = f"no opener produced an order form (tried {out['tried']})"
        return out

    def _workspaces(self, page: Any) -> List[Dict[str, Any]]:
        """Click-free: the workspace tabs (``WORKSPACE_TABS_JS``)."""
        try:
            return list(page.evaluate(WORKSPACE_TABS_JS) or [])
        except Exception:
            return []

    def _switch_workspace(self, page: Any, index: int) -> bool:
        """ONE click on workspace tab ``index``'s NAME span (tagged by
        WORKSPACE_TABS_JS), then wait. Never its close / delete control."""
        try:
            self._workspaces(page)
            loc = page.locator(f"[data-metis-ws-name='{int(index)}']")
            if loc.count() != 1:
                return False
            loc.first.click(timeout=5_000)
            page.wait_for_timeout(1_500)
            return True
        except Exception:
            return False

    def _open_ticket_on_another_workspace(self, page: Any, out: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tabs = self._workspaces(page)
        current = next((t for t in tabs if t.get("current")), None)
        out["workspaces"] = [t.get("name") for t in tabs]
        if current is None or len(tabs) < 2:
            return None
        for t in tabs:
            if t.get("current"):
                continue
            out["tried"].append(f"workspace:{t.get('name')}")
            if not self._switch_workspace(page, t["index"]):
                continue
            form = self._find_form(page)
            if form.get("found"):
                return {"name": t.get("name"), "form": form}
            # Diagnostic only (read-only, masked): what this workspace DID show.
            try:
                summ = page.evaluate(WORKSPACE_SUMMARY_JS) or {}
            except Exception as exc:
                summ = {"error": type(exc).__name__}
            out.setdefault("workspace_summary", {})[str(t.get("name"))] = summ
        # none had a ticket: put the operator's workspace back
        back = next((x for x in self._workspaces(page) if x.get("name") == current.get("name")), None)
        out["workspace_restored"] = bool(back and self._switch_workspace(page, back["index"]))
        return None

    def _dismiss_dialogs(self, page: Any, out: Dict[str, Any]) -> None:
        """A dialog the rejected single click opened is closed (Escape)
        before the next opener runs. Recorded in ``dismissed``."""
        try:
            n = page.evaluate("() => [...document.querySelectorAll('[role=dialog],[role=alertdialog],"
                              "[aria-modal=true]')].filter(e => { const r = e.getBoundingClientRect();"
                              " return r.width > 0 && r.height > 0; }).length")
            if n:
                page.keyboard.press("Escape")
                out["dismissed"] = n
        except Exception:
            pass

    def _symbol_cell_click(self, page: Any, sym: str, *, double: bool) -> bool:
        """Click (or double-click) ``sym``'s ONE clean watchlist Symbol cell
        (INFO_PROBE_RESOLVE_JS), after a hover re-check for controls -- never
        the row, never a Bid/Ask cell. True when the click was made."""
        try:
            res = page.evaluate(INFO_PROBE_RESOLVE_JS, [[sym]]) or {}
            if not (res.get("targets") or {}).get(sym, {}).get("clean"):
                return False
            cell = page.locator(f"[data-metis-row-cell='{sym}']")
            if cell.count() != 1:
                return False
            cell.first.hover(timeout=5_000)
            page.wait_for_timeout(300)
            if cell.first.evaluate("(c, s) => c.matches(s) || !!c.querySelector(s)", self._CELL_CONTROLS):
                return False
            (cell.first.dblclick if double else cell.first.click)(timeout=5_000)
            return True
        except Exception:
            return False
        finally:
            try:
                page.evaluate(INFO_PROBE_CLEANUP_JS)
            except Exception:
                pass

    def close_order_ticket(self, page: Any) -> bool:
        """Dismiss the form (its Cancel/Close button, else Escape). Safe."""
        try:
            loc = page.locator("[data-metis-btn=close]")
            if loc.count() == 1:
                loc.first.click(timeout=5_000)
                return True
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
            return True
        except Exception:
            return False

    def one_click_dump(self, page: Any) -> Dict[str, Any]:
        """Read-only structure around the one-click label (see ONE_CLICK_DUMP_JS)."""
        try:
            return page.evaluate(ONE_CLICK_DUMP_JS) or {"found": False}
        except Exception as exc:
            return {"found": False, "error": type(exc).__name__}

    def ticket_panel_dump(self, page: Any) -> Dict[str, Any]:
        """Read-only, redacted structure of the order-ticket sidebar (TICKET_PANEL_DUMP_JS)."""
        try:
            return page.evaluate(TICKET_PANEL_DUMP_JS) or {"found": False}
        except Exception as exc:
            return {"found": False, "error": type(exc).__name__}

    def controls_dump(self, page: Any) -> Dict[str, Any]:
        """Read-only, digit-masked map of the page's controls (CONTROLS_DUMP_JS)."""
        try:
            return page.evaluate(CONTROLS_DUMP_JS) or {"found": False}
        except Exception as exc:
            return {"found": False, "error": type(exc).__name__}

    def probe_order_ticket(self, page: Any, venue_symbol: str) -> Dict[str, Any]:
        """READ-ONLY feasibility measurement of the order ticket: open the
        form, record its shape (labels, buttons, disabled state, canvas
        counts), classify it, close it. Types nothing and clicks no side,
        type or submit control."""
        opened = self.open_order_ticket(page, venue_symbol)
        form = opened.get("form") or {}
        surface = classify_ticket_surface(form) if opened.get("opened") else (
            "canvas_ticket" if (form.get("canvases_in_form") or 0) > 0 else "not_opened")
        result = {
            "surface": surface,
            "one_click": opened.get("one_click"),
            "via": opened.get("via"),
            "refused": opened.get("refused"),
            "tried": opened.get("tried"),
            "workspaces": opened.get("workspaces"),
            "workspace_restored": opened.get("workspace_restored"),
            "workspace_summary": opened.get("workspace_summary"),
            "fields": {k: {"label": v.get("label"), "disabled": v.get("disabled")}
                       for k, v in (form.get("fields") or {}).items()},
            "buttons": form.get("buttons") or {},
            "checkboxes": form.get("checkboxes") or [],
            # Which side / order type the untouched form shows as selected
            # (null = not readable: place_bracket would refuse on this DOM).
            "selected": form.get("selected"),
            "ambiguous": form.get("ambiguous") or [],
            "canvases_in_form": form.get("canvases_in_form"),
            "canvases_in_page": form.get("canvases_in_page"),
            "inputs_in_page": form.get("inputs_in_page"),
            # Length only: the raw form text can carry the account number and
            # this result is printed to a PUBLIC run log.
            "form_text_len": len(form.get("form_text") or ""),
        }
        # When the one-click state could not be read, include the read-only
        # structure around its label so a reader can be built from it.
        if (opened.get("one_click") or {}).get("state") not in ("on", "off"):
            result["one_click_dump"] = self.one_click_dump(page)
        # When the ticket did not open, or it opened but side / order type is
        # not readable (place_bracket would refuse), include the digit-masked
        # control map so the opener and the side/type reader can be built.
        result["symbol_value"] = form.get("symbol_value")
        result["submit_outside_form"] = bool(form.get("submit_outside_form"))
        # A probe is a measurement: always attach the sidebar's own structure
        # (compact) when it is on the page, and the whole-page control map
        # only when it is not (the run log keeps its last ~50k characters).
        panel = self.ticket_panel_dump(page)
        if panel.get("found"):
            result["ticket_panel"] = panel
        else:
            result["ticket_panel_why"] = panel.get("why") or panel.get("error")
            result["controls_dump"] = self.controls_dump(page)
        if opened.get("opened"):
            result["closed"] = self.close_order_ticket(page)
        return result

    # ── instrument-details probe (PROP-ETH, 2026-09-29) ───────────────────
    #
    # The passive network capture (extract_instrument_specs_from_responses,
    # start_response_capture) only sees instrument data for symbols the
    # terminal's OWN traffic already requested -- MEASURED 2026-09-29 (issue
    # #14038, run 36550488339): a login-check served fields for ETHUSD/SOLUSD
    # (breakout_1's routed strategies) and NOTHING for BTCUSD/ADAUSD/XRPUSD,
    # matching how ADA/XRP's existing lot values were actually obtained (the
    # operator reading the terminal's Instrument Details panel BY HAND,
    # 2026-09-28) rather than this passive method. Reaching a symbol outside
    # the account's own traffic needs an ACTIVE step: search for it.
    #
    # ⚠️ THIS IS THE SAME SURFACE THREE EARLIER ROUNDS OF REVIEW REJECTED --
    # see this module's top-level docstring: "search box, panel click,
    # label/value scan ... each fix produced a new way for the WRONG symbol's
    # numbers to be read as the requested one's." This probe does NOT repeat
    # that mistake by naming and parsing specific fields from an unmeasured
    # panel. It TYPES the search and DUMPS the resulting structure
    # (digit-run-masked) -- the same "measure the shape, THEN write the
    # parser" sequence CONTROLS_DUMP_JS / TICKET_PANEL_DUMP_JS already use
    # elsewhere in this file. Extracting NAMED fields (lot size, price step,
    # a venue minimum) from a live run's real dump is deliberately a
    # FOLLOW-UP once that shape is actually measured against this probe's own
    # output -- writing a field parser against a panel nobody has seen yet is
    # exactly how the earlier attempts drifted onto the wrong symbol's
    # numbers. `symbol_echoed` is reported as a quality signal (does the
    # dump's own text contain the exact requested symbol as a whole word?)
    # but is NOT a hard gate: the dump is attached either way, honestly
    # labelled with whether the echo held.
    #
    # Never touches BUY/SELL, a watchlist row double-click, a submit control,
    # the chart, or TICKET_OPENER_NAMES -- this method never calls
    # open_order_ticket or anything that reaches the order form. The search
    # field it types into must lie INSIDE the MEASURED watchlist panel
    # (FIND_INSTRUMENT_SEARCH_JS's positive containment anchor) and, as
    # defense in depth, is also excluded from ever being inside a BUY/SELL
    # order panel, whatever the panel count.
    def _find_instrument_search(self, page: Any) -> Dict[str, Any]:
        """Locate a symbol SEARCH/FILTER input. Discovery only: clicks and
        types nothing. See FIND_INSTRUMENT_SEARCH_JS."""
        try:
            return page.evaluate(FIND_INSTRUMENT_SEARCH_JS, [list(INSTRUMENT_SEARCH_MATCH)]) or {}
        except Exception as exc:
            return {"found": False, "why": f"probe failed ({type(exc).__name__})"}

    def instrument_search_dump(self, page: Any) -> Dict[str, Any]:
        """READ-ONLY measurement of where the symbol search/add control is
        (INSTRUMENT_SEARCH_DUMP_JS): run in every frame, origin only per
        frame. Types, clicks, focuses and tags nothing; never reads a value."""
        out: List[Dict[str, Any]] = []
        for i, fr in enumerate(self._frames(page)):
            try:
                got = fr.evaluate(INSTRUMENT_SEARCH_DUMP_JS) or {"found": False}
            except Exception as exc:
                got = {"found": False, "error": type(exc).__name__}
            try:
                where = _strip_url(fr.url)
            except Exception:
                where = "?"
            out.append({"frame": i, "origin": where, **got})
        return {"frames": out}

    def watchlist_symbols(self, page: Any) -> Dict[str, Any]:
        """Read-only: the watchlist's symbol set (WATCHLIST_SYMBOLS_JS)."""
        try:
            return page.evaluate(WATCHLIST_SYMBOLS_JS) or {"readable": False, "why": "no result"}
        except Exception as exc:
            return {"readable": False, "why": f"read failed ({type(exc).__name__})"}

    # ── instrument INFO-PANEL probe (see INFO_PROBE_RESOLVE_JS's block) ──
    @staticmethod
    def _info_click(page: Any, selector: str) -> None:
        """Click exactly one element carrying a tag THIS probe set after
        checking it. Any other count raises -- nothing is clicked by role,
        name or text."""
        loc = page.locator(selector)
        n = loc.count()
        if n != 1:
            raise LookupError(f"{n} elements match {selector} (need exactly 1)")
        loc.first.click(timeout=5_000)

    #: What counts as a control inside a watchlist Symbol cell (same set as
    #: INFO_PROBE_RESOLVE_JS's ``ctl``).
    _CELL_CONTROLS = "button, [role=button], a, input, select, textarea, [onclick]"

    def _info_click_cell(self, page: Any, sym: str) -> bool:
        """Hover the tagged Symbol cell, RE-CHECK it for controls (a row can
        render buttons on hover), and click it only if it is still clean.
        Returns False -- nothing clicked -- when a control appeared."""
        loc = page.locator(f"[data-metis-row-cell='{sym}']")
        n = loc.count()
        if n != 1:
            raise LookupError(f"{n} tagged Symbol cells for {sym} (need exactly 1)")
        loc.first.hover(timeout=5_000)
        page.wait_for_timeout(300)
        if loc.first.evaluate("(c, s) => c.matches(s) || !!c.querySelector(s)", self._CELL_CONTROLS):
            return False
        loc.first.click(timeout=5_000)
        return True

    @staticmethod
    def _linked_symbol(page: Any) -> Optional[str]:
        try:
            v = page.locator("[data-metis-sym-input='1']").first.input_value(timeout=3_000)
            return (v or "").strip().upper()
        except Exception:
            return None

    def probe_instrument_info(self, page: Any, symbols: Sequence[str], *, click: bool = False,
                              settle_ms: int = 1_500) -> Dict[str, Any]:
        """Operator-authorized (2026-09-30) instrument INFO-PANEL probe.

        ``click=False`` (dry mode) resolves every target, runs every guard,
        reports the plan and CLICKS NOTHING. ``click=True`` then, per symbol:
        single-click that symbol's watchlist Symbol cell; confirm no dialog
        appeared and the linked symbol input reads the symbol; click the info
        button; dump the ONE new panel's own text leaves (identity checked);
        close it and confirm it is gone. On EVERY exit path (success, abort,
        exception) it re-selects the originally linked symbol and verifies
        it, unless a dialog is open. ``alerts`` is non-empty whenever the
        terminal may have been left changed; an unverified restore is also
        the executor's AUTO-REVERT latch (info_probe_restore_latch_reason,
        written by the tick).
        """
        want = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
        out: Dict[str, Any] = {"mode": "click" if click else "dry", "symbols": want,
                               "results": {}, "alerts": [], "refused": None}
        wl_before = self.watchlist_symbols(page)
        clicked_any = False
        try:
            out["one_click"] = self.read_one_click(page)
            if not info_probe_one_click_off(out["one_click"]):
                # Recorded, never a refusal (operator directive 2026-10-01,
                # manager comment 5930025023 on #14947): the structure dump
                # stays as a diagnostic; an ON read is ONE alert per run.
                out["one_click_dump"] = self.one_click_dump(page)
            oc_alert = self.take_one_click_alert()
            if oc_alert:
                out["alerts"].append(oc_alert)
            acct = self.read_account(page)
            out["account_margin_used"] = acct.margin_used
            orders = page.evaluate(INFO_PROBE_ORDERS_JS) or {"found": False, "why": "no result"}
            out["working_orders"] = orders
            if not orders.get("found"):
                # Read-only measurement so the Orders locator can be fixed
                # from the public run log (issue #14714).
                try:
                    out["orders_dump"] = page.evaluate(INFO_PROBE_ORDERS_DUMP_JS)
                except Exception as exc:
                    out["orders_dump"] = {"error": type(exc).__name__}
            res = page.evaluate(INFO_PROBE_RESOLVE_JS, [want]) or {}
            out["resolve"] = {k: v for k, v in res.items() if k != "ok"}
            original = res.get("linked_symbol")
            why: Optional[str] = None
            if not res.get("ok"):
                why = res.get("why") or "resolve failed"
            elif res.get("dialogs"):
                why = f"{res['dialogs']} dialog(s) already open"
            else:
                why = info_probe_flat_guard(acct, orders)
            if why is None and original not in (res.get("watchlist") or []):
                why = f"linked symbol {original!r} is not a watchlist row (it could not be restored)"
            if why is None:
                # The ORIGINAL symbol's cell must be cleanly clickable too, for
                # the restore -- checked before the first click, not after.
                chk = page.evaluate(INFO_PROBE_RESOLVE_JS, [[original]]) or {}
                if not (chk.get("targets") or {}).get(original, {}).get("clean"):
                    why = f"original symbol {original}'s watchlist cell is not cleanly clickable"
            out["refused"] = why
            if why is not None:
                # Any refusal records the watchlist's shape and row shapes,
                # click-free (WATCHLIST_DUMP_JS; issues #15033, #15096).
                try:
                    out["watchlist_dump"] = page.evaluate(WATCHLIST_DUMP_JS)
                except Exception as exc:
                    out["watchlist_dump"] = {"error": type(exc).__name__}
            if why is not None or not click:
                return out

            others = list(res.get("watchlist") or [])
            seen: Dict[str, str] = {}
            for sym in want:
                r: Dict[str, Any] = {}
                out["results"][sym] = r
                tag = page.evaluate(INFO_PROBE_RESOLVE_JS, [[sym]]) or {}
                if not (tag.get("ok") and (tag.get("targets") or {}).get(sym, {}).get("clean")):
                    r["skipped"] = "no single clean watchlist Symbol cell"
                    continue
                # Set BEFORE the click: a click that raised half-way still
                # gets the restore (a no-op when the linked symbol is intact).
                clicked_any = True
                if not self._info_click_cell(page, sym):
                    out["alerts"].append(f"{sym}: a control appeared in its Symbol cell on hover; "
                                         f"aborted before clicking it")
                    return out
                page.wait_for_timeout(settle_ms)
                dialogs = page.evaluate(INFO_PROBE_SNAPSHOT_JS)
                if dialogs:
                    out["alerts"].append(f"{dialogs} dialog(s) appeared after selecting {sym}; "
                                         f"aborted, nothing more clicked")
                    return out
                linked = self._linked_symbol(page)
                r["linked_after_select"] = linked
                if linked != sym:
                    r["skipped"] = f"linked symbol reads {linked!r} after selecting {sym} (linkage not confirmed)"
                    continue
                # From here until the panel is VERIFIED gone, an info panel
                # may be open (manager round-3 review of #14645): a not-found
                # abort (found:false can mean unidentified new elements) or
                # any exception leaves this set, and the restore is skipped.
                r["panel_open"] = True
                self._info_click(page, "[data-metis-info-btn='1']")
                page.wait_for_timeout(settle_ms)
                panel = page.evaluate(INFO_PROBE_PANEL_JS, [sym, [o for o in others if o != sym]]) or {}
                if not panel.get("found"):
                    # Live -probe #14831: n_new_top 1, n_panels 0 after the
                    # settle. The panel may render late, so read ONCE more
                    # after a longer wait before giving up (recorded).
                    page.wait_for_timeout(settle_ms * 3)
                    panel = page.evaluate(INFO_PROBE_PANEL_JS, [sym, [o for o in others if o != sym]]) or {}
                    r["panel_retry"] = True
                r["panel"] = {k: v for k, v in panel.items() if k != "leaves"}
                if not panel.get("found"):
                    # Nothing verified opened, so nothing is clicked to close
                    # it, and no further symbol is attempted. RECOVERY (manager
                    # step 5 after #14831, whose restore was skipped with no
                    # way back): record what DID appear (read-only, masked),
                    # press ONE Escape -- never a click, the refused-panel
                    # precedent -- and re-read. Only when no unidentified new
                    # element is still VISIBLE is the panel treated as closed,
                    # so the restore (the measured Symbol-cell click) may run;
                    # otherwise panel_open stays set and the run latches as
                    # before (round-3 review of #14645).
                    r["new_elements"] = page.evaluate(INFO_PROBE_NEW_ELEMENTS_JS) or {"readable": False}
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(settle_ms)
                    left = page.evaluate(INFO_PROBE_NEW_ELEMENTS_JS) or {"readable": False}
                    r["after_escape"] = {"readable": left.get("readable"), "n_visible": left.get("n_visible")}
                    if left.get("readable") is True and left.get("n_visible") == 0:
                        r["panel_open"] = False
                        r["closed_via"] = "escape_nothing_left"
                    after = ("recovered: nothing unidentified left visible, restore allowed"
                             if r.get("closed_via") == "escape_nothing_left" else
                             f"NOT recovered: unidentified element(s) still visible: {left.get('n_visible')}")
                    out["alerts"].append(f"{sym}: {panel.get('why') or 'no panel'}; aborted before any "
                                         f"close click; Escape pressed, {after}")
                    return out
                # A dialog-typed panel is accepted only when it passes the
                # identity checks AND reads nothing like an order
                # confirmation (confirm / submit / OK / place / buy / sell).
                # The info click's own panel may be a modal (UNMEASURED).
                bad = None
                if panel.get("has_order_controls"):
                    bad = "the new element holds BUY/SELL controls"
                elif panel.get("confirm_like"):
                    bad = "the new element reads like an order confirmation"
                elif not panel.get("names_target"):
                    bad = f"the panel does not name {sym}"
                elif panel.get("names_others"):
                    bad = f"the panel also names {panel['names_others']}"
                else:
                    h = hashlib.sha256("\n".join(panel.get("leaves") or []).encode()).hexdigest()[:16]
                    dup = [k for k, v in seen.items() if v == h]
                    if dup:
                        bad = f"panel text identical to {dup[0]}'s"
                    seen[sym] = h
                if bad:
                    # A REFUSED panel is never click-closed (manager review of
                    # #14645): one Escape key, confirm it is gone, then abort
                    # the run -- a refused panel means the terminal is not in
                    # the state this probe was built against.
                    r["refused"] = bad
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(settle_ms)
                    r["closed_via"] = "escape"
                    r["closed"] = bool(page.evaluate(INFO_PROBE_PANEL_GONE_JS))
                    out["alerts"].append(f"{sym}: {bad}; Escape pressed (panel gone: {r['closed']}); aborted")
                    return out
                r["leaves"] = panel.get("leaves")
                r["truncated"] = panel.get("truncated")
                c = page.evaluate(INFO_PROBE_CLOSE_JS) or {}
                if c.get("n") == 1:
                    self._info_click(page, "[data-metis-close='1']")
                    r["closed_via"] = "close_control"
                else:
                    self._info_click(page, "[data-metis-info-btn='1']")
                    r["closed_via"] = "info_toggle"
                page.wait_for_timeout(settle_ms)
                r["closed"] = bool(page.evaluate(INFO_PROBE_PANEL_GONE_JS))
                if r["closed"]:
                    r["panel_open"] = False
                if not r["closed"]:
                    out["alerts"].append(f"{sym}'s info panel did not close; aborted")
                    return out
            return out
        except Exception as exc:
            # Type + a fixed code only: a Playwright message can echo DOM text
            # into the public run log (manager review of #14645).
            out["alerts"].append(f"probe raised {type(exc).__name__} (code=probe_exception)")
            return out
        finally:
            # RESTORE ON EVERY EXIT PATH once anything was clicked -- abort,
            # exception or success -- unless a dialog is open, where no
            # further click is safe. A skipped or failed restore is recorded;
            # the tick turns it into the executor's AUTO-REVERT latch
            # (info_probe_restore_latch_reason).
            if click and clicked_any:
                # A REFUSED panel (BUY/SELL, confirm-like, wrong identity) or
                # one that did not close may still be on screen whether or not
                # it is a role=dialog: no further click (manager re-review of
                # #14645). Skipped -> unverified -> the tick writes the latch.
                unsafe = [s for s, r in out["results"].items()
                          if r.get("refused") or r.get("closed") is False or r.get("panel_open")]
                try:
                    open_dialogs = page.evaluate(INFO_PROBE_SNAPSHOT_JS)
                except Exception:
                    open_dialogs = None
                if open_dialogs == 0 and not unsafe:
                    self._info_restore(page, out)
                else:
                    why = (f"refused, unclosed or unverified panel for {unsafe}" if unsafe
                           else f"dialogs open: {open_dialogs}")
                    out["restore"] = {"original": (out.get("resolve") or {}).get("linked_symbol"),
                                      "attempted": False, "verified": False, "why": why}
                    out["alerts"].append(f"restore NOT attempted ({why}); no further click")
            wl_after = self.watchlist_symbols(page)
            out["watchlist_diff"] = {"before": wl_before, "after": wl_after,
                                     **watchlist_diff(wl_before, wl_after)}
            if click and out["watchlist_diff"].get("changed") is not False:
                out["alerts"].append("watchlist changed, or could not be re-read")
            try:
                page.evaluate(INFO_PROBE_CLEANUP_JS)
            except Exception:
                pass

    def fresh_page_linked_check(self, context: Any, login_url: str, original: Optional[str]) -> Dict[str, Any]:
        """CLICK-FREE read of the linked symbol on a FRESH page of the same
        browser context (manager 2026-09-30 18:00Z): the terminal's linked
        symbol does not survive the page (#14831 / #14836), so an unverified
        in-run restore is re-checked here before the tick latches. Loads the
        terminal through ``resume_session`` (the saved session, never a
        credential login), waits for it, and reads INFO_PROBE_RESOLVE_JS --
        which clicks nothing -- for the linked symbol and open dialogs.
        ``readable`` is False on any failure; that latches."""
        out: Dict[str, Any] = {"readable": False, "original": original}
        page = None
        try:
            page = context.new_page()
            st = self.resume_session(page, login_url)
            if st != "logged_in":
                out["why"] = f"session {st}"
                return out
            page.wait_for_timeout(5_000)
            self.wait_ready(page, timeout_ms=20_000)
            res = page.evaluate(INFO_PROBE_RESOLVE_JS, [[original] if original else []]) or {}
            out.update(readable=bool(res.get("ok")), linked_symbol=res.get("linked_symbol"),
                       dialogs=res.get("dialogs"), why=res.get("why"))
            page.evaluate(INFO_PROBE_CLEANUP_JS)
        except Exception as exc:
            out["why"] = f"{type(exc).__name__} (code=fresh_page_exception)"
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
        return out

    def read_linked_symbol(self, page: Any) -> Optional[str]:
        """The terminal's linked symbol, read CLICK-FREE (INFO_PROBE_RESOLVE_JS with no targets, then the
        cleanup), or None when it cannot be read. Used by the dry round trip to restore the link it moved
        (manager review of #15002)."""
        try:
            res = page.evaluate(INFO_PROBE_RESOLVE_JS, [[]]) or {}
            return res.get("linked_symbol") if res.get("ok") else None
        except Exception:
            return None
        finally:
            try:
                page.evaluate(INFO_PROBE_CLEANUP_JS)
            except Exception:
                pass

    def link_state_dump(self, page: Any) -> Dict[str, Any]:
        """READ-ONLY (LINK_STATE_DUMP_JS): watchlist rows with the element hit at each Symbol cell's
        centre, every symbol_input, and the sidebar ticket's buttons. Clicks nothing.

        Polls (read-only) until the watchlist table shows rows, up to
        SWITCH_READY_MS, and records ``rows_waited_ms`` / ``polls`` -- the
        05:28Z run (#15046) read the table with zero rows once and could not
        tell "slow to render" from "gone" (manager go 2026-10-01 06:12Z)."""
        try:
            waited, polls = 0, 0
            while True:
                got = page.evaluate(LINK_STATE_DUMP_JS) or {"error": "no result"}
                polls += 1
                if "error" in got or got.get("rows") or waited >= self.SWITCH_READY_MS:
                    got.update(rows_waited_ms=waited, polls=polls)
                    return got
                page.wait_for_timeout(500)
                waited += 500
        except Exception as exc:
            return {"error": f"{type(exc).__name__} (code=link_state_dump_exception)"}

    def widget_menu_probe(self, page: Any, *, settle_ms: int = 800) -> Dict[str, Any]:
        """MEASURE the current workspace's add-widget ("+") menu: ONE click on
        the top-most visible ``widget_tab_add_button``, dump what appeared
        (WIDGET_MENU_NEW_JS, masked), then Escape. Clicks NOTHING else -- never
        a menu item, an order/position/price/instant-trade control or a
        workspace/widget delete control. Refused when one-click reads ON. The
        layout counts are re-read after Escape and must match (``restored``);
        a menu still open gets one more Escape.

        Never reached by any trading path; the dump is masked like #15304's."""
        out: Dict[str, Any] = {"clicked": False, "refused": None}
        oc = self.read_one_click(page)
        out["one_click"] = {k: oc.get(k) for k in ("state", "via")}
        if oc.get("state") == "on":
            out["refused"] = "one-click reads ON"
            return out
        try:
            pre = page.evaluate(WIDGET_MENU_STATE_JS, True) or {}
            out["before"] = pre
            if not pre.get("add_buttons"):
                out["refused"] = "no visible widget_tab_add_button"
                return out
            loc = page.locator("[data-metis-wadd='1']")
            if loc.count() != 1:
                out["refused"] = f"{loc.count()} tagged add buttons (need exactly 1)"
                return out
            loc.first.click(timeout=5_000)
            out["clicked"] = True
            page.wait_for_timeout(settle_ms)
            out["menu"] = page.evaluate(WIDGET_MENU_NEW_JS) or {}
        except Exception as exc:
            out["error"] = type(exc).__name__
        finally:
            if out.get("clicked"):
                try:
                    for i in range(2):
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(400)
                        left = page.evaluate(WIDGET_MENU_NEW_JS) or {}
                        out["escapes"] = i + 1
                        if not left.get("items"):
                            break
                    out["menu_left_open"] = bool(left.get("items"))
                    post = page.evaluate(WIDGET_MENU_STATE_JS, False) or {}
                    out["after"] = post
                    keys = ("workspace", "widget_tabs", "widget_close", "tables")
                    out["restored"] = (not out["menu_left_open"]
                                       and all(post.get(k) == out["before"].get(k) for k in keys))
                except Exception as exc:
                    out["restore_error"] = type(exc).__name__
                    out["restored"] = False
            try:
                page.evaluate("() => { document.querySelectorAll('[data-metis-wadd]')"
                              ".forEach(e => e.removeAttribute('data-metis-wadd')); delete window.__metisWmPre; }")
            except Exception:
                pass
        return out

    #: MEASURED by add-watchlist-widget #15373 (code aaa7ff4ee): the menu's
    #: "Watchlist" entry opens a SUBMENU, not a widget.
    WATCHLIST_SUBMENU: Tuple[str, ...] = ("Private", "Public")
    #: The submenu entry add-watchlist-widget clicks (manager review of the
    #: #15390 dumps): the user's own (editable) lists, where the removed
    #: "Default Watchlist" lived, so missing symbols can be added later.
    WATCHLIST_SUBMENU_PICK = "Private"
    #: The ONE list under Private (MEASURED by add-watchlist-widget #15414,
    #: code 5b0b486bb): clicking "Private" opened a third level holding only
    #: "Favourites".
    WATCHLIST_LIST_PICK = "Favourites"

    def _escape_menus(self, page: Any, out: Dict[str, Any], *, tries: int = 3) -> None:
        """Escape (a key, never a click) until no menu-only label is visible,
        at most ``tries`` times; records ``escapes`` / ``menu_left_open``."""
        left: Dict[str, Any] = {}
        for i in range(tries):
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            out["escapes"] = i + 1
            left = page.evaluate(MENU_LABELS_VISIBLE_JS, list(MENU_ONLY_LABELS)) or {}
            if not left.get("n"):
                break
        out["menu_left_open"] = bool(left.get("n"))

    def watchlist_submenu_probe(self, page: Any, *, expect_workspace: str = "My Trading Account",
                                settle_ms: int = 800) -> Dict[str, Any]:
        """MEASURE the Watchlist submenu (TRADEIFY-GOLIVE option B, manager
        step 3 "measure first"). Two clicks, both already measured: the
        top-most "+" and the menu leaf "Watchlist" (#15350/#15373). Then it
        HOVERS -- never clicks -- each submenu entry in ``WATCHLIST_SUBMENU``
        in turn and dumps what appeared (WIDGET_MENU_NEW_JS, masked), then
        Escapes (at most three times) and re-reads the layout: ``restored`` =
        menus gone and workspace / widget-tab / widget-close / table counts
        unchanged. Refused unless one-click reads OFF and the workspace is
        ``expect_workspace``."""
        out: Dict[str, Any] = {"clicks": [], "hovers": {}, "refused": None}
        oc = self.read_one_click(page)
        out["one_click"] = {k: oc.get(k) for k in ("state", "via")}
        if oc.get("state") != "off":
            out["refused"] = f"one-click not confirmed OFF ({oc.get('state')})"
            return out
        try:
            pre = page.evaluate(WIDGET_MENU_STATE_JS, True) or {}
            out["before"] = pre
            if pre.get("workspace") != expect_workspace:
                out["refused"] = f"workspace is {pre.get('workspace')!r}, not {expect_workspace!r}"
                return out
            plus = page.locator("[data-metis-wadd='1']")
            if not pre.get("add_buttons") or plus.count() != 1:
                out["refused"] = "no single visible widget_tab_add_button"
                return out
            plus.first.click(timeout=5_000)
            out["clicks"].append("add_widget_plus")
            page.wait_for_timeout(settle_ms)
            pick = page.evaluate(WIDGET_MENU_PICK_JS, ["Watchlist", list(WIDGET_MENU_MEASURED)]) or {}
            out["pick"] = pick
            item = page.locator("[data-metis-wpick='1']")
            if not pick.get("tagged") or item.count() != 1:
                out["refused"] = "menu did not match the measured menu (nothing picked)"
                return out
            item.first.click(timeout=5_000)
            out["clicks"].append("menu:Watchlist")
            page.wait_for_timeout(settle_ms)
            # Tag each submenu entry against the pre-"+" snapshot (exact text,
            # siblings required, never inside BUY/SELL / a table / a close).
            for label in self.WATCHLIST_SUBMENU:
                others = [x for x in self.WATCHLIST_SUBMENU if x != label] + ["Chart", "Positions"]
                got = page.evaluate(WIDGET_MENU_PICK_JS, [label, others]) or {}
                if got.get("tagged"):
                    page.evaluate("(l) => { const e = document.querySelector('[data-metis-wpick]');"
                                  " e.removeAttribute('data-metis-wpick'); e.setAttribute('data-metis-wsub', l); }",
                                  label)
                out["hovers"][label] = {"pick": got}
            # A fresh snapshot with both menus open: each hover dump lists only
            # what THAT hover made appear.
            page.evaluate(WIDGET_MENU_STATE_JS, True)
            for label in self.WATCHLIST_SUBMENU:
                loc = page.locator(f"[data-metis-wsub='{label}']")
                if loc.count() != 1:
                    out["hovers"][label]["hovered"] = False
                    continue
                loc.first.hover(timeout=5_000)
                page.wait_for_timeout(settle_ms)
                out["hovers"][label].update(hovered=True, appeared=page.evaluate(WIDGET_MENU_NEW_JS) or {})
        except Exception as exc:
            out["error"] = type(exc).__name__
        finally:
            if out["clicks"]:
                try:
                    for i in range(3):
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(400)
                        out["escapes"] = i + 1
                        still = page.evaluate(MENU_LABELS_VISIBLE_JS, list(MENU_ONLY_LABELS)) or {}
                        if not still.get("n"):
                            break
                    post = page.evaluate(WIDGET_MENU_STATE_JS, False) or {}
                    out["after"] = post
                    out["menu_left_open"] = bool(still.get("n"))
                    keys = ("workspace", "widget_tabs", "widget_close", "tables")
                    out["restored"] = (not out["menu_left_open"]
                                       and all(post.get(k) == out["before"].get(k) for k in keys))
                except Exception as exc:
                    out["restore_error"] = type(exc).__name__
                    out["restored"] = False
            try:
                page.evaluate("() => { document.querySelectorAll('[data-metis-wadd],[data-metis-wpick],[data-metis-wsub]')"
                              ".forEach(e => { e.removeAttribute('data-metis-wadd'); e.removeAttribute('data-metis-wpick');"
                              " e.removeAttribute('data-metis-wsub'); }); delete window.__metisWmPre; }")
            except Exception:
                pass
        return out

    def add_watchlist_widget(self, page: Any, *, expect_workspace: str = "My Trading Account",
                             settle_ms: int = 800, ready_ms: int = 6_000) -> Dict[str, Any]:
        """Add the Watchlist widget to ``expect_workspace`` (TRADEIFY-GOLIVE
        option B step 2; operator ~21:50Z 2026-10-01 "Runner does it").

        At most THREE clicks, all on MEASURED targets: the top-most add-widget
        "+", the ONE menu leaf whose text is exactly ``Watchlist`` in a menu
        that shows every measured sibling (#15350), and -- because that entry
        opens a Private/Public submenu (#15373; hover opens nothing, #15390) --
        the ONE submenu leaf ``WATCHLIST_SUBMENU_PICK`` with its sibling
        present, and -- because Private opens a list level (#15414) -- the ONE
        list leaf ``WATCHLIST_LIST_PICK`` with Private/Public still showing
        (at most FOUR clicks). Anything that opens after that is dumped and
        Escaped, never clicked into. Refused
        unless one-click reads OFF, the workspace is ``expect_workspace`` and
        no Symbol/Bid/Ask table is already present (then it is a no-op).
        Verified by the click-free WATCHLIST_SYMBOLS_JS read (exactly one
        table) and a widget-tab count that grew. On a pick that does not
        match, Escape and abort with nothing picked. It never clicks a menu
        entry other than Watchlist, an order / price / instant-trade /
        position control, or a widget / workspace close (delete) control --
        so a wrong widget is REPORTED, never removed."""
        out: Dict[str, Any] = {"added": False, "refused": None, "clicks": []}
        oc = self.read_one_click(page)
        out["one_click"] = {k: oc.get(k) for k in ("state", "via")}
        if oc.get("state") != "off":
            out["refused"] = f"one-click not confirmed OFF ({oc.get('state')})"
            return out
        wl = self.watchlist_symbols(page)
        out["watchlist_before"] = wl
        if wl.get("readable"):
            out["refused"] = "watchlist already present (nothing to add)"
            out["already_present"] = True
            return out
        try:
            pre = page.evaluate(WIDGET_MENU_STATE_JS, True) or {}
            out["before"] = pre
            if pre.get("workspace") != expect_workspace:
                out["refused"] = f"workspace is {pre.get('workspace')!r}, not {expect_workspace!r}"
                return out
            plus = page.locator("[data-metis-wadd='1']")
            if not pre.get("add_buttons") or plus.count() != 1:
                out["refused"] = "no single visible widget_tab_add_button"
                return out
            plus.first.click(timeout=5_000)
            out["clicks"].append("add_widget_plus")
            page.wait_for_timeout(settle_ms)
            pick = page.evaluate(WIDGET_MENU_PICK_JS, ["Watchlist", list(WIDGET_MENU_MEASURED)]) or {}
            out["pick"] = pick
            item = page.locator("[data-metis-wpick='1']")
            if not pick.get("tagged") or item.count() != 1:
                out["refused"] = "menu did not match the measured menu (nothing picked)"
                out["menu"] = page.evaluate(WIDGET_MENU_NEW_JS) or {}
                for _ in range(2):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(400)
                return out
            item.first.click(timeout=5_000)
            out["clicks"].append("menu:Watchlist")
            page.wait_for_timeout(settle_ms)
            # MEASURED (#15373, #15390): "Watchlist" opens a Private/Public
            # SUBMENU whose entries open nothing on hover. Click the ONE exact
            # ``WATCHLIST_SUBMENU_PICK`` leaf (the user's own lists -- the
            # removed "Default Watchlist" was one), with its measured sibling
            # present. A submenu that does not match is Escaped, nothing picked.
            if not self.watchlist_symbols(page).get("readable"):
                sub = page.evaluate(WIDGET_MENU_PICK_JS, [self.WATCHLIST_SUBMENU_PICK,
                                    [x for x in self.WATCHLIST_SUBMENU if x != self.WATCHLIST_SUBMENU_PICK]]) or {}
                out["submenu_pick"] = sub
                sub_item = page.locator("[data-metis-wpick='1']")
                if not sub.get("tagged") or sub_item.count() != 1:
                    out["refused"] = "Watchlist submenu did not match the measured submenu (nothing picked)"
                    out["menu"] = page.evaluate(WIDGET_MENU_NEW_JS) or {}
                    self._escape_menus(page, out)
                    return out
                sub_item.first.click(timeout=5_000)
                out["clicks"].append(f"submenu:{self.WATCHLIST_SUBMENU_PICK}")
                page.wait_for_timeout(settle_ms)
                # MEASURED (#15414): "Private" opens a third level holding ONE
                # list, "Favourites". Click that exact leaf (new since "+",
                # with Private/Public still showing); anything else -> Escape.
                if not self.watchlist_symbols(page).get("readable"):
                    lst = page.evaluate(WIDGET_MENU_PICK_JS, [self.WATCHLIST_LIST_PICK,
                                        list(self.WATCHLIST_SUBMENU)]) or {}
                    out["list_pick"] = lst
                    lst_item = page.locator("[data-metis-wpick='1']")
                    if not lst.get("tagged") or lst_item.count() != 1:
                        out["refused"] = "Private submenu did not match the measured list level (nothing picked)"
                        out["menu"] = page.evaluate(WIDGET_MENU_NEW_JS) or {}
                        self._escape_menus(page, out)
                        return out
                    lst_item.first.click(timeout=5_000)
                    out["clicks"].append(f"list:{self.WATCHLIST_LIST_PICK}")
            waited = 0
            while True:
                page.wait_for_timeout(500)
                waited += 500
                wl = self.watchlist_symbols(page)
                if wl.get("readable") or waited >= ready_ms:
                    break
            post = page.evaluate(WIDGET_MENU_STATE_JS, False) or {}
            out.update(after=post, watchlist_after=wl, waited_ms=waited)
            grew = (post.get("widget_tabs") or 0) > (pre.get("widget_tabs") or 0)
            out["added"] = bool(wl.get("readable")) and grew and post.get("workspace") == expect_workspace
            if not out["added"]:
                # Whatever opened instead is dumped, then one Escape; it is
                # REPORTED, never closed by a click (a close control is a delete).
                out["new_after"] = page.evaluate(WIDGET_MENU_NEW_JS) or {}
                self._escape_menus(page, out)
        except Exception as exc:
            out["error"] = type(exc).__name__
            # A menu (or whatever the click opened) may still be up: one
            # Escape, like the mismatch path; never a close click.
            if out["clicks"]:
                try:
                    page.keyboard.press("Escape")
                    out["escaped_after_error"] = True
                except Exception:
                    out["escaped_after_error"] = False
        finally:
            try:
                page.evaluate("() => { document.querySelectorAll('[data-metis-wadd],[data-metis-wpick]')"
                              ".forEach(e => { e.removeAttribute('data-metis-wadd');"
                              " e.removeAttribute('data-metis-wpick'); }); delete window.__metisWmPre; }")
            except Exception:
                pass
        return out

    #: Symbol-switch bounds (PROP-ETH-DOM, operator directive 2026-10-01
    #: 06:13Z). Live #15022 clicked SOLUSD's Symbol cell and the link stayed
    #: ETHUSD; the 05:28Z dump (#15046) found the watchlist table with ZERO
    #: rows -- a race against a terminal still rendering is the working
    #: hypothesis, so the switch waits for rows, verifies by polling instead of
    #: one fixed settle, retries a click that did not take, and falls back to
    #: the toolbar symbol box. Instance-overridable (tests shorten them).
    SWITCH_READY_MS = 10_000
    SWITCH_VERIFY_MS = 4_000
    SWITCH_POLL_MS = 250
    SWITCH_CLICK_ATTEMPTS = 3

    def _resolve_link(self, page: Any, targets: Sequence[str]) -> Dict[str, Any]:
        """INFO_PROBE_RESOLVE_JS (clicks nothing; tags the targets' clean
        Symbol cells and the linked input). Never raises."""
        try:
            return page.evaluate(INFO_PROBE_RESOLVE_JS, [list(targets)]) or {}
        except Exception as exc:
            return {"ok": False, "why": f"{type(exc).__name__} (code=resolve_exception)"}

    def _wait_watchlist_rows(self, page: Any, target: str) -> Dict[str, Any]:
        """Poll the click-free resolve until the watchlist has rows (or the
        link already reads ``target``), up to SWITCH_READY_MS. Returns the
        last resolve with ``ready_waited_ms`` / ``ready_polls`` recorded."""
        waited, polls = 0, 0
        while True:
            res = self._resolve_link(page, [target])
            polls += 1
            if (res.get("ok") and (res.get("watchlist") or res.get("linked_symbol") == target)) \
                    or res.get("dialogs") or waited >= self.SWITCH_READY_MS:
                res.update(ready_waited_ms=waited, ready_polls=polls)
                return res
            page.wait_for_timeout(self.SWITCH_POLL_MS * 2)
            waited += self.SWITCH_POLL_MS * 2

    def _await_link(self, page: Any, target: str, min_ms: int) -> Dict[str, Any]:
        """After an action: poll the linked symbol (click-free resolve) until
        it reads ``target`` or a dialog appears, waiting at least ``min_ms``
        before the first read and at most SWITCH_VERIFY_MS in all."""
        first = max(0, min(int(min_ms), self.SWITCH_VERIFY_MS))
        page.wait_for_timeout(first)
        waited = first
        while True:
            res = self._resolve_link(page, [])
            if res.get("linked_symbol") == target or res.get("dialogs") or waited >= self.SWITCH_VERIFY_MS:
                return {"after": res.get("linked_symbol"), "dialogs": res.get("dialogs") or 0,
                        "verify_waited_ms": waited}
            page.wait_for_timeout(self.SWITCH_POLL_MS)
            waited += self.SWITCH_POLL_MS

    def _note_one_click(self, page: Any, out: Dict[str, Any]) -> None:
        """Read and RECORD one-click before a click. Never refuses (operator
        directive 2026-10-01, manager comment 5930025023 on #14947)."""
        oc = self.read_one_click(page)
        out["one_click"] = {k: oc.get(k) for k in ("state", "via")}

    def _ticket_symbol(self, page: Any) -> Dict[str, Any]:
        try:
            return page.evaluate(TICKET_SYMBOL_RESOLVE_JS) or {"ok": False, "why": "no result"}
        except Exception as exc:
            return {"ok": False, "why": f"{type(exc).__name__} (code=ticket_symbol_exception)"}

    def _await_ticket_symbol(self, page: Any, want: str, min_ms: int) -> Dict[str, Any]:
        """Poll the ticket field (click-free) until it reads ``want`` or a
        dialog shows, at least ``min_ms`` then up to SWITCH_VERIFY_MS."""
        first = max(0, min(int(min_ms), self.SWITCH_VERIFY_MS))
        page.wait_for_timeout(first)
        waited = first
        while True:
            got = self._ticket_symbol(page)
            if got.get("value_canon") == canonical_symbol(want) or got.get("dialogs") or waited >= self.SWITCH_VERIFY_MS:
                got["verify_waited_ms"] = waited
                return got
            page.wait_for_timeout(self.SWITCH_POLL_MS)
            waited += self.SWITCH_POLL_MS

    def select_ticket_symbol(self, page: Any, venue_symbol: str, *, settle_ms: int = 1_500,
                             restore_on_failure: bool = True) -> Dict[str, Any]:
        """Set the OPEN order ticket's own Symbol field to ``venue_symbol``,
        VERIFIED -- the PRIMARY per-ticket switch (operator by hand, manager
        comment 5928196365 on #15070). Never an order control.

        Resolve the ONE ticket field (TICKET_SYMBOL_RESOLVE_JS); a field that
        already reads the target does nothing. Otherwise: record one-click
        (informational, never a refusal); snapshot; fill the target (no Enter, ever); find the
        ONE dropdown row whose Symbol text EXACTLY equals it
        (TICKET_SYMBOL_PICK_JS) -- when the full name lists none, re-type its
        first three characters once; record one-click; click that row's
        Symbol cell; poll the field. ``ok`` only when the field reads the
        target, no dialog is open, the description under it CHANGED and the
        submit label NAMES the target -- never the typed field alone. With
        no single exact row nothing is clicked and the field is put back to
        the original (filled, then blurred: no key); when a row WAS picked but
        did not verify, the original is re-picked through this same guarded
        route (``restore``). Symbols compare canonically (canonical_symbol /
        __metisSym: ETH/USD == ETHUSD). ``picked`` says whether a row was
        clicked. A SIGTERM mid-route skips the restore (accepted, manager
        review of 221c459); the next ticket's switch re-reads the field."""
        target = str(venue_symbol or "").strip().upper()
        want = canonical_symbol(target)
        out: Dict[str, Any] = {"target": target, "ok": False, "picked": False, "route": "ticket_field"}
        typed = False
        original = None
        try:
            res = self._ticket_symbol(page)
            out["before"] = {k: res.get(k) for k in ("value", "desc", "submit_symbol")}
            if not res.get("ok"):
                out["why"] = res.get("why") or "ticket symbol field not resolvable"
                return out
            if res.get("dialogs"):
                out["why"] = f"{res['dialogs']} dialog(s) open"
                return out
            original = res.get("value")
            if not want:
                out["why"] = f"{target!r} is not a symbol"
                return out
            if res.get("value_canon") == want:
                out.update(ok=True, after=out["before"])
                return out
            self._note_one_click(page, out)
            if page.evaluate(INFO_PROBE_SNAPSHOT_JS):
                out["why"] = "dialog(s) open before typing"
                return out
            box = page.locator("[data-metis-ticket-sym='1']")
            pick: Dict[str, Any] = {}
            for typed_text in dict.fromkeys([target, target[:3].lower()]):
                box.first.fill(typed_text, timeout=5_000)
                typed = True
                page.wait_for_timeout(max(settle_ms, 800))
                pick = page.evaluate(TICKET_SYMBOL_PICK_JS, [target]) or {}
                pick["typed"] = typed_text
                if pick.get("dialogs") or pick.get("n_candidates") == 1:
                    break
            out["pick"] = pick
            if pick.get("dialogs"):
                out["why"] = f"{pick['dialogs']} dialog(s) open after typing {target}"
                return out
            if pick.get("n_candidates") != 1:
                out["why"] = f"{pick.get('n_candidates')} dropdown rows read exactly {target} (need exactly 1)"
                return out
            self._note_one_click(page, out)
            page.locator("[data-metis-ticket-pick='1']").first.click(timeout=5_000)
            out["picked"] = True
            typed = False
            got = self._await_ticket_symbol(page, target, settle_ms)
            out["after"] = {k: got.get(k) for k in ("value", "desc", "submit_symbol")}
            out["verify_waited_ms"] = got.get("verify_waited_ms")
            # Recorded, not gated on: whether the chart's linked box followed
            # the ticket pick is unmeasured.
            out["chart_after"] = self._resolve_link(page, []).get("linked_symbol")
            # NEVER verified by the field alone -- the field is the box we just
            # typed into (manager 2026-10-01 09:22Z: the old toolbar route read
            # its own typing back and reported ok with nothing linked). Success
            # needs the field AND two signals the typing cannot produce: the
            # description line under the field CHANGED, and the submit label
            # NAMES the target (an unreadable label is not a pass).
            before_desc = (out.get("before") or {}).get("desc") or ""
            if got.get("dialogs"):
                out["why"] = f"{got['dialogs']} dialog(s) appeared after picking {target}"
            elif got.get("value_canon") != want:
                out["why"] = f"ticket symbol reads {got.get('value')!r} after picking {target}"
            elif got.get("submit_canon") != want:
                out["why"] = f"submit label names {got.get('submit_symbol')!r}, not {target}"
            elif not got.get("desc") or (got.get("desc") == before_desc
                                         and canonical_symbol(res.get("submit_symbol") or "") != want):
                # The description must change -- unless the ticket was already
                # committed to the target before the pick (its submit label
                # named it; only the field text had drifted, e.g. a restore after
                # an unverified pick), when an unchanged description is right.
                out["why"] = f"the description under the field did not change (reads {got.get('desc')!r})"
            else:
                out["ok"] = True
            if not out["ok"] and restore_on_failure and original:
                # Picked but NOT verified: the ticket may now hold another
                # symbol. Put the ORIGINAL back through the same guarded route
                # (manager review of 221c459, item 4); once, never recursively.
                rest = self.select_ticket_symbol(page, original, settle_ms=settle_ms, restore_on_failure=False)
                out["restore"] = {k: rest.get(k) for k in ("ok", "picked", "why", "after")}
                if not rest.get("ok"):
                    out["why"] += f"; RESTORE to {original} FAILED: {rest.get('why')}"
                    typed = True             # last resort below: fill the original back (no key)
            return out
        except Exception as exc:
            out["why"] = f"{type(exc).__name__} (code=ticket_switch_exception)"
            return out
        finally:
            if typed and original:
                # Nothing was picked (or the guarded restore failed): put the
                # field back the way it was. No key press -- a fill and a DOM
                # blur only.
                try:
                    self._ticket_symbol(page)            # re-tag: a nested restore's cleanup removed the tag
                    box = page.locator("[data-metis-ticket-sym='1']")
                    box.first.fill(original, timeout=3_000)
                    box.first.evaluate("el => el.blur()")
                    out["field_reset"] = self._ticket_symbol(page).get("value_canon") == canonical_symbol(original)
                except Exception:
                    out["field_reset"] = False
            try:
                page.evaluate(INFO_PROBE_CLEANUP_JS)
            except Exception:
                pass

    def select_linked_symbol(self, page: Any, venue_symbol: str, *, settle_ms: int = 1_500) -> Dict[str, Any]:
        """Make ``venue_symbol`` the terminal's linked symbol, VERIFIED.

        1. READY: poll the click-free resolve (INFO_PROBE_RESOLVE_JS) until
           the watchlist has rows, up to SWITCH_READY_MS (10 s). A link that already
           reads the target clicks nothing.
        2. WATCHLIST ROUTE, up to SWITCH_CLICK_ATTEMPTS: re-resolve, record
           one-click (informational), ONE single click on the target's clean
           watchlist Symbol cell (never Bid/Ask, never a double-click -- a
           double-click opens the ticket) after the hover re-check, then poll
           the linked symbol up to SWITCH_VERIFY_MS.
        The ALTERNATIVE route: the primary per-ticket switch is the open
        ticket's own Symbol field (select_ticket_symbol). The chart-toolbar
        typing route was DROPPED (manager comment 5928196365 on #15070: a
        quick-trade bar sits beside that box and a second "Symbol..." box
        exists).
        ``ok`` is True only when the linked symbol reads the target and no
        dialog is open. Any dialog stops everything at once. MEASURED live:
        #14831 (BTCUSD), #14870 and B2 #14999 (ETHUSD) read the clicked symbol
        back; #15022 (SOLUSD) did not."""
        target = str(venue_symbol or "").strip().upper()
        out: Dict[str, Any] = {"target": target, "ok": False, "clicked": False, "attempts": 0}
        try:
            res = self._wait_watchlist_rows(page, target)
            out["before"] = res.get("linked_symbol")
            out["ready"] = {"waited_ms": res.get("ready_waited_ms"), "polls": res.get("ready_polls"),
                            "watchlist_n": len(res.get("watchlist") or [])}
            if not res.get("ok"):
                out["why"] = res.get("why") or "could not resolve the watchlist / linked symbol"
                return out
            if res.get("dialogs"):
                out["why"] = f"{res['dialogs']} dialog(s) open"
                return out
            if res.get("linked_symbol") == target:
                out.update(ok=True, after=target)
                return out
            route_why: Optional[str] = None
            for attempt in range(1, self.SWITCH_CLICK_ATTEMPTS + 1):
                res = self._resolve_link(page, [target])
                if res.get("dialogs"):
                    out["why"] = f"{res['dialogs']} dialog(s) open"
                    return out
                if res.get("linked_symbol") == target:
                    out.update(ok=True, after=target)
                    return out
                if not (res.get("targets") or {}).get(target, {}).get("clean"):
                    route_why = f"{target} has no single clean watchlist Symbol cell"
                    break
                # Gate every click like the info probe (manager decision
                # 2026-09-30 20:17Z, review of #14885). With one-click ON a
                # geometry shift or a hover control the check misses could
                # turn this click into an order. Order controls stay un-gated.
                self._note_one_click(page, out)
                if not self._info_click_cell(page, target):
                    out["why"] = f"a control appeared in {target}'s Symbol cell on hover; not clicked"
                    return out
                out["clicked"] = True
                out["attempts"] = attempt
                got = self._await_link(page, target, settle_ms)
                out["after"] = got["after"]
                out["verify_waited_ms"] = got["verify_waited_ms"]
                if got["dialogs"]:
                    out["why"] = f"{got['dialogs']} dialog(s) appeared after selecting {target}"
                    return out
                if got["after"] == target:
                    # When a ticket is open, its own Symbol field must follow
                    # the click too (operator screenshots: a watchlist click
                    # moves the chart box AND the New Order field).
                    if self._ticket_symbol(page).get("ok"):
                        tk = self._await_ticket_symbol(page, target, 0)
                        out["ticket_after"] = tk.get("value")
                        if tk.get("value_canon") != canonical_symbol(target):
                            out["why"] = f"ticket field reads {tk.get('value')!r} after the watchlist click"
                            return out
                    out["ok"] = True
                    out["route"] = "watchlist"
                    return out
                route_why = f"linked symbol reads {got['after']!r} after selecting {target}"
            out["after"] = self._resolve_link(page, []).get("linked_symbol")
            out["why"] = route_why or f"linked symbol reads {out['after']!r} after selecting {target}"
            return out
        except Exception as exc:
            out["why"] = f"{type(exc).__name__} (code=symbol_switch_exception)"
            return out
        finally:
            try:
                page.evaluate(INFO_PROBE_CLEANUP_JS)
            except Exception:
                pass

    def switch_ticket_symbol(self, page: Any, venue_symbol: str, *, settle_ms: int = 1_500,
                             alternative: bool = True) -> Dict[str, Any]:
        """The per-ticket switch the live placement uses, and symbol-switch-dry
        exercises the SAME one (PROP-ETH-DOM #15139: the dry run tested the
        ticket route alone and alerted while the live path's alternative
        succeeded). PRIMARY: the ticket's own Symbol field (manager comment
        5928196365 on #15070). ALTERNATIVE, only when that route picked
        nothing: the verified watchlist-row click, which the ticket field must
        follow; the ticket route's outcome is kept under ``ticket_route``.
        ``alternative=False`` (switch-dry with no readable chart link, e.g.
        tradeify_1, #15136) runs the ticket route alone."""
        link = self.select_ticket_symbol(page, venue_symbol, settle_ms=settle_ms)
        if alternative and not link.get("ok") and not link.get("picked"):
            wl = self.select_linked_symbol(page, venue_symbol, settle_ms=settle_ms)
            wl["ticket_route"] = {k: link.get(k) for k in ("why", "before", "pick")}
            # The link may ALREADY read the target (no click, so no follow
            # check inside select_linked_symbol) while the ticket still names
            # another symbol: the alternative is ok only when the open
            # ticket's own field reads the target.
            if wl.get("ok"):
                try:
                    tk = self._ticket_symbol(page)
                    if tk.get("ok"):
                        wl["ticket_after"] = tk.get("value")
                        if tk.get("value_canon") != canonical_symbol(venue_symbol):
                            wl["ok"] = False
                            wl["why"] = f"ticket field reads {tk.get('value')!r} after the watchlist route"
                except Exception as exc:
                    wl["ok"] = False
                    wl["why"] = f"{type(exc).__name__} (code=ticket_follow_check_exception)"
                finally:
                    try:
                        page.evaluate(INFO_PROBE_CLEANUP_JS)
                    except Exception:
                        pass
            link = wl
        return link

    def symbol_switch_dry(self, page: Any, venue_symbol: str, *, settle_ms: int = 1_500,
                          home: Optional[str] = None) -> Dict[str, Any]:
        """DRY check of the per-ticket switch. NO order control is touched and
        nothing is submitted. One-click is recorded, never a refusal; an ON
        read is ONE alert (operator directive 2026-10-01).

        1. Read the linked symbol and the target's quote (click-free).
        2. TICKET ROUTE (primary): open the order ticket (open_order_ticket --
           a ticket-opener button or a double-click on the LINKED symbol's own
           watchlist row, never a price button), switch it to the target
           through the SAME route the live placement uses
           (switch_ticket_symbol: the ticket's Symbol field, else the
           verified watchlist click), verify, then to ``home`` and verify;
           close the ticket. A field-route miss that the watchlist route
           covers is a ``notes`` entry, not an alert.
        3. LINK: set the terminal's linked symbol to ``home`` with the
           verified watchlist click (select_linked_symbol) -- click-free when
           it already reads ``home``.
        ``home`` is where the terminal is left: the caller passes the
        account's resting symbol (the tick: the original link when it is an
        enabled venue symbol, else the first enabled one), defaulting to the
        original link."""
        out: Dict[str, Any] = {"target": str(venue_symbol or "").strip().upper(), "alerts": [], "refused": None}
        try:
            # Recorded, never a refusal (operator directive 2026-10-01); an ON
            # read anywhere in this run is ONE alert, added at the end.
            out["one_click"] = self.read_one_click(page)
            res = page.evaluate(INFO_PROBE_RESOLVE_JS, [[]]) or {}
            page.evaluate(INFO_PROBE_CLEANUP_JS)
            original = res.get("linked_symbol")
            out["original"] = original
            link_route = bool(res.get("ok") and original)
            if not link_route:
                # No readable chart link (tradeify_1 has no toolbar
                # symbol_input and no info button, #15096/#15126): the TICKET
                # route alone is exercised -- the one place_bracket uses first
                # -- with the ticket field's own value as home, and no link
                # restore. Refused only when the ticket field is unreadable too.
                page.evaluate(INFO_PROBE_CLEANUP_JS)
                out["link_unavailable"] = res.get("why") or "linked symbol not readable"
                # tradeify_1 keeps no New Order ticket open (#15146: "0 order
                # panels"): open it first with the same opener place_bracket
                # uses (a named opener button, else the target's watchlist row).
                pre_open = self.open_order_ticket(page, out["target"])
                out["ticket_preopen"] = {k: pre_open.get(k) for k in ("opened", "via", "refused")}
                tk = self._ticket_symbol(page)
                if not tk.get("ok") or not tk.get("value"):
                    out["refused"] = (f"{out['link_unavailable']}; ticket symbol field: {tk.get('why') or 'empty'}"
                                      f"; ticket open: {pre_open.get('via') or pre_open.get('refused')}")
                    if pre_open.get("opened") and pre_open.get("via") != "already_open":
                        out["ticket_closed"] = self.close_order_ticket(page)
                    return out
                original = tk.get("value")
                out["original"] = original
            home_sym = str(home or original).strip().upper()
            out["home"] = home_sym
            # CLICK-FREE quote read of the target BEFORE any click (manager
            # 2026-09-30 20:39Z): its bid/ask DECIMALS measure the price_step.
            out["quote"] = self.read_quote(page, out["target"])
            out["quote_raw"] = page.evaluate(WATCHLIST_QUOTE_RAW_JS, [out["target"]]) or {"error": "no result"}
            opened = self.open_order_ticket(page, original)
            out["ticket_open"] = {k: opened.get(k) for k in ("opened", "via", "refused", "tried", "last_resort")}
            if opened.get("opened"):
                try:
                    for key, sym, label in (("switch", out["target"], "ticket switch to"),
                                            ("ticket_home", home_sym, "ticket back to")):
                        out[key] = self.switch_ticket_symbol(page, sym, settle_ms=settle_ms,
                                                             alternative=link_route)
                        if not out[key].get("ok"):
                            out["alerts"].append(f"{label} {sym} failed: {out[key].get('why')}")
                        elif out[key].get("ticket_route"):
                            # Not an alert: the live placement takes the same
                            # alternative. Recorded so the field route's miss
                            # stays visible.
                            out.setdefault("notes", []).append(
                                f"{label} {sym}: ticket field picked nothing "
                                f"({out[key]['ticket_route'].get('why')}); watchlist route used")
                finally:
                    out["ticket_closed"] = self.close_order_ticket(page)
            else:
                out["alerts"].append(f"no order ticket opened: {opened.get('refused')}")
            if link_route:
                out["restore"] = self.select_linked_symbol(page, home_sym, settle_ms=settle_ms)
                if not out["restore"].get("ok"):
                    out["alerts"].append(f"link to {home_sym} failed: {out['restore'].get('why')}")
            oc_alert = self.take_one_click_alert()
            if oc_alert:
                out["alerts"].append(oc_alert)
            return out
        except Exception as exc:
            out["alerts"].append(f"symbol-switch-dry raised {type(exc).__name__} (code=switch_dry_exception)")
            return out

    def _info_restore(self, page: Any, out: Dict[str, Any]) -> None:
        """Re-select the ORIGINALLY linked symbol and verify it reads back.
        A failure is an alert: the terminal's linked symbol may be changed."""
        original = (out.get("resolve") or {}).get("linked_symbol")
        rest: Dict[str, Any] = {"original": original, "attempted": True}
        out["restore"] = rest
        try:
            if self._linked_symbol(page) == original:
                rest.update(clicked=False, verified=True)
                return
            chk = page.evaluate(INFO_PROBE_RESOLVE_JS, [[original]]) or {}
            if not (chk.get("targets") or {}).get(original, {}).get("clean"):
                raise LookupError("original symbol's watchlist cell is not cleanly clickable")
            if not self._info_click_cell(page, original):
                raise LookupError("a control appeared in the original symbol's cell on hover")
            page.wait_for_timeout(1_500)
            rest["clicked"] = True
            rest["verified"] = self._linked_symbol(page) == original
        except Exception as exc:
            rest["verified"] = False
            rest["error"] = type(exc).__name__
        if not rest.get("verified"):
            out["alerts"].append(f"RESTORE FAILED: the linked symbol should read {original!r}, "
                                 f"reads {self._linked_symbol(page)!r}")

    def instrument_details_dump(self, page: Any) -> Dict[str, Any]:
        """Read-only, digit-run-masked (runs >= 5) dump of controls + short
        static text leaves on the page (INSTRUMENT_DETAILS_DUMP_JS)."""
        try:
            return page.evaluate(INSTRUMENT_DETAILS_DUMP_JS) or {"found": False}
        except Exception as exc:
            return {"found": False, "error": type(exc).__name__}

    @staticmethod
    def search_query_for(venue_symbol: str, style: Optional[str] = None) -> str:
        """The text typed into the watchlist search for ``venue_symbol``.
        ``style`` comes from the account's ``config/prop_platforms.yaml``
        entry (``search_query_style``); ``slash`` types the terminal's own
        display form, ``SOL/USD`` for ``SOLUSD`` (MEASURED on tradeify_1,
        #15444: typing ``SOLUSD`` opened the result panel with NO rows).
        Anything else, or no style, types the venue symbol unchanged."""
        sym = str(venue_symbol or "").strip().upper()
        if style == "slash" and len(sym) > 3 and sym.endswith(("USD", "USDT")) and "/" not in sym:
            quote = "USDT" if sym.endswith("USDT") else "USD"
            return f"{sym[:-len(quote)]}/{quote}"
        return sym

    def probe_instrument_details(self, page: Any, venue_symbol: str, *, query: Optional[str] = None,
                                 settle_ms: int = 1_000) -> Dict[str, Any]:
        """READ-ONLY: search for ``venue_symbol`` in a search field found
        INSIDE the measured watchlist panel (never the order ticket's
        ``symbol_input``, never anything inside a BUY/SELL panel), dump
        whatever the search surfaces, then RESET the field so the next
        symbol probes cleanly. Never opens the order ticket; never clicks
        BUY/SELL/submit/chart. Needs no order ticket to be open.
        """
        loc = self._find_instrument_search(page)
        if not loc.get("found"):
            return {"searched": False, "reset": None, **loc}
        hit = page.locator("[data-metis-search-hit='1']")
        result: Dict[str, Any] = {"searched": False, "via": loc.get("via"), "reset": None}
        try:
            if hit.count() != 1:
                result["why"] = f"{hit.count()} tagged candidates (need exactly 1)"
                return result
            typed = (query or venue_symbol).strip()
            result["query"] = typed
            hit.first.fill(typed, timeout=5_000)
            page.wait_for_timeout(settle_ms)
            readback = hit.first.input_value(timeout=5_000)
            result["searched"] = True
            result["readback_matches"] = (readback.strip().upper() == typed.upper())
            dump = self.instrument_details_dump(page)
            result["dump"] = dump
            # The suggestion rows sit in a <tbody>, which the dump above
            # skips (#15457), so they get a scoped read of their own.
            try:
                result["suggestions"] = page.evaluate(SUGGESTION_ROWS_JS) or {"found": False}
            except Exception as exc:
                result["suggestions"] = {"found": False, "error": type(exc).__name__}
            blob = " ".join((r.get("text") or "") for r in (dump.get("rows") or []))
            for panel in (result["suggestions"].get("panels") or []):
                for row in (panel.get("rows") or []):
                    blob += " " + " ".join(c or "" for c in (row.get("cells") or []))
            result["symbol_echoed"] = any(bool(re.search(
                r"(?<![A-Z0-9/])" + re.escape(form.upper()) + r"(?![A-Z0-9/])", blob.upper()))
                for form in {venue_symbol.strip(), typed})
        except Exception as exc:
            result["error"] = type(exc).__name__
        finally:
            # Only reached a real search field when the tag count check
            # above passed (searched=True) -- an early refusal (ambiguous
            # or missing tag) must never fall into resetting/filling an
            # element nobody verified is the intended search input.
            if result["searched"]:
                try:
                    hit.first.fill("", timeout=5_000)
                    result["reset"] = (hit.first.input_value(timeout=2_000) == "")
                except Exception:
                    result["reset"] = False
                # Drop focus so no suggestion dropdown lingers (manager
                # review of #14563): a DOM blur() on the same verified
                # element -- no Escape key, no click anywhere.
                try:
                    result["blurred"] = bool(hit.first.evaluate(
                        "el => { el.blur(); return document.activeElement !== el; }"))
                except Exception:
                    result["blurred"] = False
            # Unconditional: strip our own tag so it can never linger into
            # the next symbol's probe, whatever happened above.
            try:
                page.evaluate(CLEAR_INSTRUMENT_SEARCH_HIT_JS)
            except Exception:
                pass
        return result

    @staticmethod
    def search_query_variants(venue_symbol: str) -> List[str]:
        """The query forms the variant probe types for ``venue_symbol``: the
        base asset alone (a prefix every naming matches), the venue symbol,
        then the slash display form. ``ETHUSD`` -> ``["ETH", "ETHUSD",
        "ETH/USD"]``. De-duplicated, order kept."""
        sym = str(venue_symbol or "").strip().upper()
        slash = DXtradeAdapter.search_query_for(sym, "slash")
        base = slash.split("/", 1)[0] if "/" in slash else sym
        out: List[str] = []
        for q in (base, sym, slash):
            if q and q not in out:
                out.append(q)
        return out

    def probe_search_variants(self, page: Any, venue_symbol: str, *, queries: Optional[List[str]] = None,
                              settle_ms: int = 3_000, key_delay_ms: int = 80) -> Dict[str, Any]:
        """READ-ONLY (TRADEIFY-GOLIVE, #15472): typing ``ETH/USD`` with
        ``fill()`` left the suggestion table's tbody EMPTY. This types each
        query form KEY BY KEY (``press_sequentially``: characters only, never
        Enter) into the same verified watchlist search, waits ``settle_ms``,
        reads only the scoped suggestion rows (SUGGESTION_ROWS_JS), and
        clears the field before the next form. It ends with the field
        cleared, blurred and the search tag removed, like
        ``probe_instrument_details``. It never clicks anything."""
        loc = self._find_instrument_search(page)
        if not loc.get("found"):
            return {"searched": False, "reset": None, **loc}
        hit = page.locator("[data-metis-search-hit='1']")
        result: Dict[str, Any] = {"searched": False, "via": loc.get("via"), "reset": None, "variants": []}
        try:
            if hit.count() != 1:
                result["why"] = f"{hit.count()} tagged candidates (need exactly 1)"
                return result
            result["searched"] = True
            for q in (queries or self.search_query_variants(venue_symbol)):
                row: Dict[str, Any] = {"query": q}
                try:
                    hit.first.fill("", timeout=5_000)
                    hit.first.press_sequentially(q, delay=key_delay_ms, timeout=10_000)
                    page.wait_for_timeout(settle_ms)
                    row["readback_matches"] = (hit.first.input_value(timeout=5_000).strip().upper() == q.upper())
                    row["suggestions"] = page.evaluate(SUGGESTION_ROWS_JS) or {"found": False}
                except Exception as exc:
                    row["error"] = type(exc).__name__
                result["variants"].append(row)
        except Exception as exc:
            result["error"] = type(exc).__name__
        finally:
            if result["searched"]:
                try:
                    hit.first.fill("", timeout=5_000)
                    result["reset"] = (hit.first.input_value(timeout=2_000) == "")
                except Exception:
                    result["reset"] = False
                try:
                    result["blurred"] = bool(hit.first.evaluate(
                        "el => { el.blur(); return document.activeElement !== el; }"))
                except Exception:
                    result["blurred"] = False
            try:
                page.evaluate(CLEAR_INSTRUMENT_SEARCH_HIT_JS)
            except Exception:
                pass
        return result

    def probe_page_leaf_dump(self, page: Any, venue_symbol: str, *, query: Optional[str] = None,
                             settle_ms: int = 3_000, key_delay_ms: int = 80) -> Dict[str, Any]:
        """READ-ONLY (TRADEIFY-GOLIVE (b)): type ``query`` (default: the base
        asset, ``ETH`` for ``ETHUSD``) KEY BY KEY into the verified watchlist
        search (characters only, never Enter), wait ``settle_ms``, take
        PAGE_LEAF_DUMP_JS of the whole page while the text is in, then clear,
        blur and untag the field like ``probe_search_variants``. Clicks
        nothing."""
        loc = self._find_instrument_search(page)
        if not loc.get("found"):
            return {"searched": False, "reset": None, **loc}
        hit = page.locator("[data-metis-search-hit='1']")
        q = (query or self.search_query_variants(venue_symbol)[0]).strip()
        result: Dict[str, Any] = {"searched": False, "via": loc.get("via"), "reset": None, "query": q}
        try:
            if hit.count() != 1:
                result["why"] = f"{hit.count()} tagged candidates (need exactly 1)"
                return result
            result["searched"] = True
            hit.first.fill("", timeout=5_000)
            hit.first.press_sequentially(q, delay=key_delay_ms, timeout=10_000)
            page.wait_for_timeout(settle_ms)
            result["readback_matches"] = (hit.first.input_value(timeout=5_000).strip().upper() == q.upper())
            result["page"] = page.evaluate(PAGE_LEAF_DUMP_JS, q) or {"found": False}
        except Exception as exc:
            result["error"] = type(exc).__name__
        finally:
            if result["searched"]:
                try:
                    hit.first.fill("", timeout=5_000)
                    result["reset"] = (hit.first.input_value(timeout=2_000) == "")
                except Exception:
                    result["reset"] = False
                try:
                    result["blurred"] = bool(hit.first.evaluate(
                        "el => { el.blur(); return document.activeElement !== el; }"))
                except Exception:
                    result["blurred"] = False
            try:
                page.evaluate(CLEAR_INSTRUMENT_SEARCH_HIT_JS)
            except Exception:
                pass
        return result

    @staticmethod
    def _visible_dialogs(page: Any) -> Optional[int]:
        """Visible [role=dialog]/[role=alertdialog]/[aria-modal] count (the
        INFO_PROBE_SNAPSHOT_JS detector); None when it could not look."""
        try:
            return int(page.evaluate(INFO_PROBE_SNAPSHOT_JS))
        except Exception:
            return None

    def add_watchlist_symbol(self, page: Any, venue_symbol: str, *, arm: bool = False,
                             settle_ms: int = 3_000, key_delay_ms: int = 80,
                             after_ms: int = 2_000) -> Dict[str, Any]:
        """Add ``venue_symbol`` to the (Favourites) watchlist through its own
        search's suggestion list (TRADEIFY-GOLIVE, manager GO 2026-10-02
        17:39Z). One symbol per call.

        Types the base asset key by key into the verified watchlist search
        (never Enter), then resolves with ADD_SYMBOL_RESOLVE_JS the ONE result
        row whose Symbol cell reads exactly the slash form (``ETH/USD``).
        ``arm=False`` (the DRY run) reports that row, its box and what
        ``elementFromPoint`` returns at the click point, then clears the field
        and clicks NOTHING. ``arm=True`` additionally requires one-click OFF
        and a clean resolve, makes ONE click on that Symbol cell, presses
        Escape once, and verifies: no order ticket and no new dialog opened
        (if one did: Escape, report, stop), and the watchlist re-read shows
        exactly ``venue_symbol`` added and nothing else changed. It never
        clicks an order / price / instant-trade / position control, or any
        close / delete control."""
        sym = str(venue_symbol or "").strip().upper()
        target = self.search_query_for(sym, "slash")
        out: Dict[str, Any] = {"symbol": sym, "target": target, "armed": bool(arm),
                               "added": False, "refused": None, "clicks": []}
        oc = self.read_one_click(page)
        out["one_click"] = {k: oc.get(k) for k in ("state", "via")}
        if arm and oc.get("state") != "off":
            out["refused"] = f"one-click not confirmed OFF ({oc.get('state')})"
            return out
        before = self.watchlist_symbols(page)
        out["watchlist_before"] = before
        if arm and not before.get("readable"):
            out["refused"] = "watchlist not readable before the click (could not verify an add)"
            return out
        if sym in (before.get("symbols") or []):
            out["refused"] = f"{sym} already in the watchlist (nothing to add)"
            out["already_present"] = True
            return out
        loc = self._find_instrument_search(page)
        out["search"] = {k: loc.get(k) for k in ("found", "via", "why")}
        if not loc.get("found"):
            out["refused"] = "watchlist search not found"
            return out
        hit = page.locator("[data-metis-search-hit='1']")
        typed = False
        try:
            if hit.count() != 1:
                out["refused"] = f"{hit.count()} tagged search candidates (need exactly 1)"
                return out
            query = self.search_query_variants(sym)[0]
            out["query"] = query
            hit.first.fill("", timeout=5_000)
            hit.first.press_sequentially(query, delay=key_delay_ms, timeout=10_000)
            typed = True
            page.wait_for_timeout(settle_ms)
            res = page.evaluate(ADD_SYMBOL_RESOLVE_JS, [target]) or {"ok": False, "why": "no resolve result"}
            out["resolve"] = res
            if not res.get("ok"):
                out["refused"] = res.get("why") or "resolve failed"
                return out
            if not arm:
                out["dry"] = "resolved; nothing clicked"
                return out
            form_before = bool(self._find_form(page).get("found"))
            dialogs_before = self._visible_dialogs(page)
            page.locator("[data-metis-add-target='1']").first.click(timeout=5_000)
            out["clicks"].append(f"suggestion:{target}")
            page.wait_for_timeout(after_ms)
            page.keyboard.press("Escape")
            out["escaped"] = 1
            page.wait_for_timeout(500)
            form_after = bool(self._find_form(page).get("found"))
            dialogs_after = self._visible_dialogs(page)
            out["guard"] = {"form_before": form_before, "form_after": form_after,
                            "dialogs_before": dialogs_before, "dialogs_after": dialogs_after}
            if (form_after and not form_before) or (
                    dialogs_after is None or (dialogs_before is not None and dialogs_after > dialogs_before)):
                page.keyboard.press("Escape")
                out["escaped"] = 2
                out["refused"] = "an order ticket or dialog opened after the click (Escaped; stopped)"
                return out
            waited, after = 0, self.watchlist_symbols(page)
            while sym not in (after.get("symbols") or []) and waited < 6_000:
                page.wait_for_timeout(500)
                waited += 500
                after = self.watchlist_symbols(page)
            out["watchlist_after"] = after
            out["watchlist_diff"] = watchlist_diff(before, after)
            d = out["watchlist_diff"]
            out["added"] = (d.get("changed") is True and d.get("added") == [sym] and not d.get("removed"))
            if not out["added"]:
                out["refused"] = "watchlist re-read does not show exactly the one symbol added"
        except Exception as exc:
            out["error"] = type(exc).__name__
            if out["clicks"]:
                try:
                    page.keyboard.press("Escape")
                    out["escaped_after_error"] = True
                except Exception:
                    out["escaped_after_error"] = False
        finally:
            if typed:
                try:
                    if hit.count() == 1:
                        hit.first.fill("", timeout=5_000)
                        out["reset"] = (hit.first.input_value(timeout=2_000) == "")
                        out["blurred"] = bool(hit.first.evaluate(
                            "el => { el.blur(); return document.activeElement !== el; }"))
                except Exception:
                    out["reset"] = False
            try:
                page.evaluate("() => document.querySelectorAll('[data-metis-add-target]')"
                              ".forEach(e => e.removeAttribute('data-metis-add-target'))")
                page.evaluate(CLEAR_INSTRUMENT_SEARCH_HIT_JS)
            except Exception:
                pass
        return out

    def place_bracket(self, page: Any, spec: BracketSpec, *, arm: bool = False) -> PlaceAttempt:
        """One order with SL AND TP attached at entry.

        Opens the form, sets side / type / quantity / price / SL / TP, reads
        every field back and compares, and — only with ``arm=True`` — clicks
        the form's single submit control (plus one confirmation dialog if the
        terminal shows one). With ``arm=False`` it closes the form at
        ``form_verified``. ``submitted=True`` means only that submit was
        clicked; the caller confirms by re-read.
        """
        bad = check_bracket_spec(spec)
        if bad:
            return PlaceAttempt(stage="refused", detail="; ".join(bad))
        opened = self.open_order_ticket(page, spec.venue_symbol)
        if not opened.get("opened"):
            return PlaceAttempt(stage="refused", detail=opened.get("refused") or "form not opened",
                                form={"one_click": opened.get("one_click"),
                                      "opener": {k: opened.get(k) for k in ("tried", "last_resort")}})
        form = opened["form"]
        # PER-TICKET SYMBOL SWITCH (manager 2026-09-30 19:40Z; routes per
        # manager comment 5928196365 on #15070): the open form is the sidebar
        # ticket, set to the terminal's linked symbol, so an ETHUSD ticket on a
        # SOLUSD-linked terminal was refused ("the open form does not name
        # ETHUSD"). Only when the form names ANOTHER symbol: set the ticket's
        # own Symbol field (exact dropdown row, verified), else the verified
        # watchlist-row click (MEASURED live: #14831 BTCUSD, #14870 ETHUSD);
        # re-read the form; refuse on any mismatch. A form already naming the
        # symbol is untouched. form_names_symbol below still guards.
        if not form.get("ambiguous") and not form_names_symbol(form, spec.venue_symbol):
            link = self.switch_ticket_symbol(page, spec.venue_symbol)
            if not link.get("ok"):
                detail = f"symbol switch: {link.get('why')}"
                # A switch that CLICKED but did not verify may have moved the
                # link: ONE guarded restore to the prior linked symbol (itself
                # gated and verified), alerting if it fails (manager review of
                # #14885, finding b).
                if link.get("clicked") and link.get("before"):
                    rest = self.select_linked_symbol(page, link["before"])
                    link["restore"] = {k: rest.get(k) for k in ("ok", "clicked", "after", "why")}
                    if not rest.get("ok"):
                        detail += f"; RESTORE to {link['before']} FAILED: {rest.get('why')}"
                self.close_order_ticket(page)
                return PlaceAttempt(stage="refused", detail=detail,
                                    form={**dict(form), "symbol_switch": link})
            form = self._find_form(page)
            form["symbol_switch"] = link

        trace: List[Dict[str, Any]] = []

        def refuse(why: str, f: Optional[Mapping[str, Any]] = None) -> PlaceAttempt:
            self.close_order_ticket(page)
            return PlaceAttempt(stage="refused", detail=why, form={**dict(f or form), "fill_trace": trace})

        if form.get("ambiguous"):
            return refuse(f"ambiguous form controls: {form['ambiguous']}")
        # The form must NAME the instrument, as a whole word: an order typed
        # into a ticket for the wrong symbol is the worst silent failure here.
        if not form_names_symbol(form, spec.venue_symbol):
            return refuse(f"the open form does not name {spec.venue_symbol}")
        # The PRICE field is not required here: the live Breakout form opens in
        # MARKET mode and shows no price input until LIMIT is selected
        # (2026-09-30 12:44Z, ticket prop-manual-b573aecb5d47 refused
        # "form fields not found: ['price']" before the order-type click ever
        # ran). It is required right after that click, below.
        need = ["quantity", "stop_loss", "take_profit"]
        missing = [k for k in need if k not in (form.get("fields") or {})]
        if missing:
            return refuse(f"form fields not found: {missing}")
        if "submit" not in (form.get("buttons") or {}):
            form = self._scroll_until_submit(page)
            if "submit" not in (form.get("buttons") or {}):
                return refuse("no unique submit control in the form (panel scrolled to the end)")
        # Never a control outside the ticket panel (the chart's own price
        # buttons execute instantly with one-click ON). Checked before any
        # click, and again in every read-back.
        outside = verify_buttons_in_panel(form)
        if outside:
            return refuse("control outside the ticket panel: " + "; ".join(outside))
        # Each step is NAMED so a timeout says which one (dry run #13942:
        # "typing into the form failed (TimeoutError)" could not), and carries
        # Playwright's own reason (e.g. "element is not enabled"), capped.
        step = "start"
        try:
            # Click a side / order-type button only when the form does not
            # ALREADY show it selected (dry run #13953: the click on an
            # already-selected BUY timed out). The read-back below verifies
            # the selection either way, so skipping never skips the check.
            shown = form.get("selected") or {}
            step = "side click"
            side_btn = "side_buy" if spec.side == "long" else "side_sell"
            if shown.get("side") == ("buy" if spec.side == "long" else "sell"):
                pass
            elif side_btn in form.get("buttons", {}):
                page.click(f"[data-metis-btn={side_btn}]", timeout=5_000)
            elif form.get("buttons", {}).get("side_buy") is None and form.get("buttons", {}).get("side_sell") is None:
                return refuse("no side selector in the form")
            step = "order-type click"
            type_btn = "type_limit" if spec.order_type == "limit" else "type_market"
            if shown.get("order_type") == spec.order_type:
                pass
            elif type_btn in form.get("buttons", {}):
                page.click(f"[data-metis-btn={type_btn}]", timeout=5_000)
                page.wait_for_timeout(300)
            form = self._find_form(page)
            if spec.order_type == "limit" and "price" not in (form.get("fields") or {}):
                return refuse("form fields not found after selecting LIMIT: ['price']", form)
            # Switch each leg's enabling toggle ON (the live sidebar's SL / TP
            # toggles read "false" by default): the operator's flow sets the
            # brackets BEFORE execution. Never switched off; read back below.
            for leg in ("stop_loss", "take_profit"):
                step = f"{leg} toggle click"
                fld = (form.get("fields") or {}).get(leg) or {}
                tog = [c for c in form.get("checkboxes") or [] if c.get("field") == leg]
                if len(tog) > 1:
                    return refuse(f"{leg}: {len(tog)} enabling toggles (ambiguous)")
                if len(tog) == 1 and not tog[0].get("checked"):
                    page.click(f"[data-metis-field={leg}_toggle]", timeout=5_000)
                    page.wait_for_timeout(300)
                elif not tog and fld.get("disabled"):
                    return refuse(f"{leg} field is disabled and has no enabling toggle")
            want = {"quantity": spec.quantity, "stop_loss": spec.stop_loss, "take_profit": spec.take_profit}
            if spec.order_type == "limit":
                want["price"] = float(spec.limit_price)
            step = "re-read after toggles"
            form = self._find_form(page)
            still = [k for k in ("stop_loss", "take_profit") if ((form.get("fields") or {}).get(k) or {}).get("disabled")]
            if still:
                return refuse(f"the enabling toggle did not enable {still} (inputs still disabled after the click)", form)
            # Quantity FIRST, then let the terminal settle: a size change
            # makes it recompute its own SL / TP defaults asynchronously, and
            # in dry run #13965 that recompute landed on the stop loss typed
            # right after the quantity (read back as the terminal's default,
            # 117.96, five ticks under the bid) while the take profit typed
            # last stuck. Each field is blurred after the fill so the
            # terminal commits and formats it before the read-back.
            step = "quantity fill"
            self._fill_field(page, "quantity", want["quantity"])
            page.wait_for_timeout(600)
            for k, v in want.items():
                if k == "quantity":
                    continue
                step = f"{k} fill"
                self._fill_field(page, k, v)
            page.wait_for_timeout(300)
            form = self._find_form(page)
            # Verify every typed value; re-fill only the fields the form
            # does not show as typed (within one tick for prices), at most
            # twice, after a settle. Each pass is traced so the run log says
            # which field the terminal reset and whether the re-fill stuck.
            tol = price_tolerances(spec, want)
            for pass_no in range(1, 4):
                fields = form.get("fields") or {}
                bad = [k for k in want if verify_form_values(fields, {k: want[k]}, abs_tol=tol)]
                trace.append({"pass": pass_no, "shown": {k: (fields.get(k) or {}).get("value") for k in want},
                              "refilled": bad})
                if not bad or pass_no == 3:
                    break
                page.wait_for_timeout(500)
                for k in bad:
                    step = f"{k} re-fill (pass {pass_no + 1})"
                    self._fill_field(page, k, want[k])
                page.wait_for_timeout(300)
                form = self._find_form(page)
        except Exception as exc:
            # Playwright puts the REASON at the end of its call log ("...
            # intercepts pointer events"); keep the head (the call) and the
            # tail (the reason) -- #13953 cut the reason off at 400 chars.
            msg = re.sub(r"\s+", " ", str(exc))
            why = msg if len(msg) <= 600 else msg[:160] + " ... " + msg[-420:]
            return refuse(f"typing into the form failed at {step} ({type(exc).__name__}: {why})")
        # Read-back of all six fields (ORDER ENTRY rule 4): symbol, side,
        # order type, quantity, stop loss, take profit (+ price for a limit).
        mism = self._read_back(form, spec, want)
        if mism:
            return refuse("form read-back mismatch: " + "; ".join(mism), form)
        # Bring the submit control into view (it can sit below the fold once
        # the brackets are on), then re-run the FULL read-back: scrolling must
        # not have changed any field. Done disarmed too, so a dry run measures
        # exactly what an armed run would click.
        ready, why, form, submit_info = self._ready_submit(page, spec, want)
        if not ready:
            return refuse(why, form)
        # Diagnostic only: the toggle's reading right before submit travels
        # with the attempt so the run log shows it. Nothing is gated on it.
        form = {**form, "one_click": self.read_one_click(page), "submit": submit_info, "fill_trace": trace}
        if not arm:
            # Criterion D7 (ticket reset / closed afterwards) is READ BACK, not
            # assumed: what the dismiss did and what the form shows after it.
            form["ticket_after"] = self._ticket_after(page)
            return PlaceAttempt(stage="form_verified", detail="disarmed: stopped before submit", form=form)
        # ── the one click that changes the account: the ticket's own submit ──
        # Click the element PROVEN a moment ago (its one-time token): a node
        # re-rendered since then lacks it, the click times out, and the
        # attempt is reported unconfirmed (review of #13822).
        submit_sel = f"[data-metis-btn=submit][data-metis-submit-token={submit_info['token']}]"
        try:
            page.evaluate(POST_SUBMIT_JS, ["tag"])
        except Exception:
            pass
        try:
            page.click(submit_sel, timeout=5_000)
        except Exception as exc:
            # The click may or may not have landed: report submitted so the
            # caller treats it as UNCONFIRMED and re-reads; never resubmit.
            return PlaceAttempt(stage="submitted", submitted=True,
                                detail=f"submit click raised {type(exc).__name__}; outcome unknown", form=form)
        confirmed = self._confirm_dialog(page)
        # What the click produced, RECORDED on the attempt (live test #13987:
        # the click filled 0.01 SOLUSD with both legs attached, the terminal
        # asked for no confirmation, and the run log said nothing about the
        # page after the click). Nothing here is pressed: this terminal places
        # on the submit alone, so a second click could only ever be a second
        # order.
        form = {**form, "after_submit": self._after_submit(page, dialog_confirmed=confirmed)}
        return PlaceAttempt(stage="submitted", submitted=True, detail="submit clicked", form=form)

    def _after_submit(self, page: Any, *, dialog_confirmed: bool) -> Dict[str, Any]:
        """Read-only: every visible control that appeared after the submit
        click (text, box, disabled) and the redacted text of the element
        holding them (POST_SUBMIT_JS). Never clicks."""
        out: Dict[str, Any] = {"dialog_confirmed": dialog_confirmed, "new_buttons": [], "overlay_text": None,
                               "dialogs": None, "why": None}
        try:
            page.wait_for_timeout(300)
            got = page.evaluate(POST_SUBMIT_JS, ["diff"]) or {}
        except Exception as exc:
            out["why"] = f"post-submit read failed ({type(exc).__name__})"
            return out
        out.update({k: got.get(k) for k in ("new_buttons", "overlay_text", "dialogs")})
        out["why"] = "recorded only; nothing pressed"
        return out

    def dump_tables(self, page: Any, secrets: Sequence[str] = ()) -> List[str]:
        """READ-ONLY diagnostic of the positions / orders read path (live test
        #13987: a position the operator could see, 0.01 SOLUSD with both
        legs, read back as 0 positions for 7 minutes, so the reader is blind
        somewhere between the tab click, the table extraction and the
        classification). For each view tab: the tab-like controls the page
        shows, whether the tab click landed, then every table
        EXTRACT_TABLES_JS finds (kind, headers, row count, first 3 rows) and
        how the readers classify it. Every string is redacted; digit runs of
        5+ are masked so a position id never reaches a public log. Clicks
        only the view tabs, never a row control."""
        def r(t: Any, n: int = 40) -> str:
            return _cap(re.sub(r"\d{5,}", "#####", redact_text(str(t or ""), *secrets)), n)

        lines: List[str] = ["dump_tables: BEGIN (read-only; ids masked)"]
        try:
            tabs = page.evaluate(
                "() => [...document.querySelectorAll('[role=tab], [data-active]:not(button):not([role=button])')]"
                ".map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 30)).filter(Boolean).slice(0, 20)")
            lines.append(f"dump_tables.tab_like: {[r(t, 30) for t in (tabs or [])]}")
        except Exception as exc:
            lines.append(f"dump_tables.tab_like: FAILED ({type(exc).__name__})")
        for key in ("tab_positions", "tab_orders", "tab_order_history", "tab_trade_history"):
            shown = self._show_tab(page, key)
            # The row's own controls appear on HOVER (operator 2026-09-29: a
            # reverse / modify / close icon trio at the row's right end), so
            # hover the first body row of each table before reading. A hover
            # is not a click; nothing is pressed.
            try:
                page.evaluate("() => { document.querySelectorAll('[data-metis-hover]').forEach(e => e.removeAttribute('data-metis-hover'));"
                              " const r = [...document.querySelectorAll('table tr')].find(r => r.querySelector('td'));"
                              " if (r) r.setAttribute('data-metis-hover', '1'); }")
                if page.locator("[data-metis-hover]").count() == 1:
                    page.hover("[data-metis-hover]", timeout=3_000)
                    page.wait_for_timeout(300)
            except Exception:
                pass
            tables = self._tables(page)
            lines.append(f"dump_tables[{SELECTORS[key]}]: tab_click={shown} tables={len(tables)}")
            for i, t in enumerate(tables):
                headers = list(t.get("headers") or [])
                reads_as = ("positions" if _is_positions_table(headers)
                            else "orders" if _is_orders_table(headers) else "neither")
                rows = t.get("rows") or []
                lines.append(f"dump_tables.table[{i}] kind={t.get('kind')} reads_as={reads_as} rows={len(rows)} "
                             f"headers={[r(h) for h in headers]}")
                if t.get("kind") == "table":
                    lines.append(f"dump_tables.table[{i}].pairing: paired_body={t.get('paired')} own_rows={t.get('own_rows')} "
                                 f"unpaired_body_rows={t.get('unpaired_body_rows')} "
                                 f"headerless_tables_rows={t.get('headerless_tables')} "
                                 f"first_row_controls={[r(c, 30) for c in (t.get('first_row_controls') or [])]}")
                    # The controls' own markup, for a positions / orders row
                    # only (a watchlist row's Buy / Sell buttons are not the
                    # question): text-less icon buttons are told apart by
                    # what they are called, and that is only visible here.
                    if reads_as in ("positions", "orders") and t.get("first_row_control_html"):
                        lines.append(f"dump_tables.table[{i}].first_row_control_html: "
                                     f"{[r(h, 240) for h in t.get('first_row_control_html')]}")
                # First 3 rows, and the last 3 when there are more (a history
                # tab may list the newest fill at either end).
                shown_rows = rows[:3] + (rows[-3:] if len(rows) > 6 else rows[3:6])
                for row in shown_rows:
                    lines.append(f"dump_tables.row[{i}]: {[r(c) for c in row]}")
        lines.append("dump_tables: END")
        return lines

    def _ticket_after(self, page: Any) -> Dict[str, Any]:
        """Dismiss the ticket, then MEASURE what is left: whether a dismiss
        control was clicked (else Escape), whether the form is still found,
        and, if so, its typed fields' values and the SL / TP toggle states.
        Our own numbers only, never account data. Nothing is judged here: a
        sidebar that stays open with our values is reported, so the next
        dry run's log shows what "reset" means on this terminal."""
        closed = self.close_order_ticket(page)
        try:
            page.wait_for_timeout(300)
        except Exception:
            pass
        after = self._find_form(page)
        fields = after.get("fields") or {}
        return {"dismissed": closed, "found": bool(after.get("found")),
                "values": {k: (fields.get(k) or {}).get("value") for k in ("quantity", "stop_loss", "take_profit", "price")
                           if k in fields},
                "toggles": {t.get("field"): t.get("checked") for t in (after.get("checkboxes") or [])
                            if t.get("field")},
                "selected": after.get("selected")}

    @staticmethod
    def _fill_field(page: Any, key: str, value: float) -> None:
        """Type one value into a tagged field, then blur it so the terminal
        commits (and formats) it before anything reads it back."""
        sel = f"[data-metis-field={key}]"
        page.fill(sel, _fmt_num(value), timeout=5_000)
        try:
            page.locator(sel).blur()
        except Exception:
            pass          # an older Playwright without Locator.blur: the read-back still decides

    @staticmethod
    def _read_back(form: Mapping[str, Any], spec: BracketSpec, want: Mapping[str, float]) -> List[str]:
        """The full pre-submit read-back: symbol, side, order type, every typed
        value (a price within one venue tick), both bracket legs armed (toggle
        ON, mode Price), and every clickable control inside the ticket panel."""
        mism = [] if form_names_symbol(form, spec.venue_symbol) else [f"symbol: form no longer names {spec.venue_symbol}"]
        mism += verify_form_selection(form, spec.side, spec.order_type)
        mism += verify_form_values(form.get("fields") or {}, want, abs_tol=price_tolerances(spec, want))
        mism += verify_bracket_legs(form)
        mism += verify_buttons_in_panel(form)
        return mism

    def _scroll_until_submit(self, page: Any, max_steps: int = 8) -> Dict[str, Any]:
        """Scroll the form's own container step by step until the submit
        control is in the DOM (a virtualised panel renders it only then)."""
        form: Dict[str, Any] = {}
        for _ in range(max_steps):
            try:
                step = page.evaluate(SUBMIT_JS, ["scroll_step", ""]) or {}
            except Exception:
                break
            page.wait_for_timeout(200)
            form = self._find_form(page)
            if "submit" in (form.get("buttons") or {}) or not step.get("moved"):
                break
        return form

    def _ready_submit(self, page: Any, spec: BracketSpec, want: Mapping[str, float],
                      *, attempts: int = 4, settle_ms: int = 500
                      ) -> Tuple[bool, str, Dict[str, Any], Dict[str, Any]]:
        """Centre the submit control, then prove, right before the click
        point: it is the SAME element (a one-time token), visible at its
        centre, enabled, its text names the intended side, and the full
        read-back still holds after the scroll.

        The centre hit-test is POLLED -- up to ``attempts`` tries, ``settle_ms``
        apart, with a scroll-only ``reveal`` of the BUTTON's own scroll
        ancestors between tries -- and a final refusal NAMES what the centre
        hit (``submit_not_visible_why``). Both come from the same measurement:
        on 2026-10-02 the identical submit box refused six live ETH tickets
        between 04:34Z and 05:15Z and passed the dry walk at 06:14Z, so what
        blocked it was TRANSIENT and the refusal could not say what it was.
        ``reveal`` scrolls the button's ancestors rather than the form's
        because the live submit is a footer OUTSIDE the fields' container
        (``in_form: false`` on every measurement), so the form's chain is not
        necessarily the scroller that moves it.

        Clicks nothing, fills nothing, dismisses nothing -- a dry walk measures
        exactly what an armed run would click."""
        token = f"t{time.time_ns()}"
        tries = max(1, int(attempts))
        form: Dict[str, Any] = {}
        info: Dict[str, Any] = {"token": token}
        chk: Dict[str, Any] = {}
        for attempt in range(1, tries + 1):
            try:
                marked = page.evaluate(SUBMIT_JS, ["mark", token]) or {}
            except Exception as exc:
                return False, f"submit: could not mark ({type(exc).__name__})", form, info
            if not marked.get("ok"):
                return False, f"submit: {marked.get('why')}", form, info
            page.wait_for_timeout(300)
            form = self._find_form(page)      # re-tags; the token stays on the element
            try:
                chk = page.evaluate(SUBMIT_JS, ["check", token]) or {}
            except Exception as exc:
                return False, f"submit: could not check ({type(exc).__name__})", form, info
            info.update({"scrolled": bool(marked.get("scrolled")), "text": chk.get("text"), "tries": attempt})
            if not chk.get("same"):
                return False, "submit: the control changed after scrolling", form, info
            if chk.get("visible"):
                info.pop("why_not", None)
                break
            info["why_not"] = chk.get("why_not") or {}
            if attempt >= tries:
                why = submit_not_visible_why(info["why_not"])
                return (False, "submit: not visible at its centre after scrolling"
                        + (f" \u2014 {why}" if why else ""), form, info)
            try:
                info.setdefault("reveal", []).append(page.evaluate(SUBMIT_JS, ["reveal", ""]) or {})
            except Exception:
                pass
            page.wait_for_timeout(settle_ms)
        if not chk.get("enabled"):
            return False, "submit: disabled", form, info
        text = str(chk.get("text") or "").lower()
        mine, other = ("buy", "sell") if spec.side == "long" else ("sell", "buy")
        if not re.search(rf"\b{mine}\b", text) or re.search(rf"\b{other}\b", text):
            return False, f"submit: its text {chk.get('text')!r} does not name the intended side ({mine})", form, info
        why = submit_label_mismatch(str(chk.get("text") or ""), spec)
        if why:
            return False, f"submit: {why}", form, info
        mism = self._read_back(form, spec, want)
        if mism:
            return False, "read-back after scrolling to submit: " + "; ".join(mism), form, info
        return True, "", form, info

    @staticmethod
    def _confirm_dialog(page: Any) -> bool:
        """Press a confirmation dialog's single Confirm/OK button if one shows.
        Only ever called after an ARMED action."""
        try:
            page.wait_for_timeout(1_000)
            loc = page.locator("[role=dialog], [role=alertdialog]").get_by_role(
                "button", name=re.compile(r"^(confirm|ok|yes|place order)$", re.IGNORECASE))
            if loc.count() == 1:
                loc.first.click(timeout=5_000)
                return True
        except Exception:
            pass
        return False

    def _row_action(self, page: Any, kind: str, key_col: str, key: str,
                    action_re: str, arm: bool) -> Dict[str, Any]:
        """Find exactly one row of the orders/positions table whose ``key_col``
        cell equals ``key`` and exactly one control in it matching
        ``action_re``; click it only when ``arm``."""
        try:
            got = page.evaluate(ROW_ACTION_JS, [kind, key_col, key, action_re]) or {}
        except Exception as exc:
            return {"ok": False, "clicked": False, "why": f"probe failed ({type(exc).__name__})"}
        if got.get("rows") != 1 or got.get("controls") != 1:
            return {"ok": False, "clicked": False,
                    "why": f"need exactly 1 row and 1 control (rows={got.get('rows')}, controls={got.get('controls')})"}
        if not arm:
            return {"ok": True, "clicked": False, "why": "disarmed: stopped before the click"}
        try:
            page.click("[data-metis-row-action]", timeout=5_000)
        except Exception as exc:
            return {"ok": True, "clicked": True, "why": f"click raised {type(exc).__name__}; outcome unknown"}
        self._confirm_dialog(page)
        return {"ok": True, "clicked": True, "why": "clicked"}

    def cancel_order(self, page: Any, order: WorkingOrder, *, arm: bool = False) -> Dict[str, Any]:
        """Cancel ONE working order, identified by its terminal Order ID."""
        if not order or not order.order_id:
            return {"ok": False, "clicked": False, "why": "no order_id: refusing to guess which row"}
        self._show_tab(page, "tab_orders")
        return self._row_action(page, "orders", "Order ID", str(order.order_id),
                                r"^(cancel|cancel order|×|✕|x|remove)$", arm)

    def flatten(self, page: Any, symbol: Optional[str] = None, *, arm: bool = False,
                side: Optional[str] = None, quantity: Optional[float] = None,
                entry_price: Optional[float] = None, rel_tol: float = 0.02) -> Dict[str, Any]:
        """Close ONE symbol's position through the terminal's own flow, every
        step read back (operator 2026-09-29): the one Positions row for
        ``symbol`` (its side / size / fill must match what the caller means
        to close, when given) -> hover -> the row's LAST control, the only
        close-type one, boxed inside the row and away from any canvas ->
        the "Close Position" modal must name OUR symbol and side and offer
        the FULL size as lots-to-close (never edited) -> "Close Position".
        Any mismatch presses Discard (or the modal's x) and refuses. Never
        "close all": a symbol is required. With ``arm=False`` it locates the
        row and its close control and stops before any click."""
        if not symbol:
            return {"ok": False, "clicked": False, "why": "symbol required (no close-all)"}
        self._show_tab(page, "tab_positions")
        try:
            loc = page.evaluate(CLOSE_ROW_JS, ["locate", symbol]) or {}
        except Exception as exc:
            return {"ok": False, "clicked": False, "why": f"locate failed ({type(exc).__name__})"}
        if not loc.get("ok"):
            return {"ok": False, "clicked": False, "why": _mask_public_text(loc.get("why") or "row not located"), "rows": loc.get("rows")}
        facts = loc.get("facts") or {}
        bad = _row_facts_mismatch(facts, side, quantity, entry_price, rel_tol)
        if bad:
            return {"ok": False, "clicked": False, "why": "row does not match the position to close: " + "; ".join(bad),
                    "row": facts}
        try:
            page.hover("[data-metis-close-row]", timeout=5_000)
            page.wait_for_timeout(400)
            ctl = page.evaluate(CLOSE_ROW_JS, ["controls", symbol]) or {}
        except Exception as exc:
            return {"ok": False, "clicked": False, "why": f"hover / controls failed ({type(exc).__name__})", "row": facts}
        # What the row showed and which control was chosen ride EVERY result
        # from here on, the armed success included (live test #14344: the pass
        # proved the rules held but its log carried no control markup, so the
        # trio's names could not be read back from the run — PI-20260929-2PNSPDNU-0004).
        seen = {"controls": _mask_controls(ctl.get("controls")), "chosen": ctl.get("chosen")}
        if not ctl.get("ok"):
            return {"ok": False, "clicked": False, "why": _mask_public_text(f"close control: {ctl.get('why')}"), "row": facts,
                    **seen}
        if not arm:
            return {"ok": True, "clicked": False, "why": "disarmed: stopped before the row's close control",
                    "row": facts, **seen}
        try:
            page.click("[data-metis-row-action]", timeout=5_000)
        except Exception as exc:
            return {"ok": False, "clicked": True, "why": f"close control click raised {type(exc).__name__}; outcome unknown",
                    "row": facts, **seen}
        page.wait_for_timeout(800)
        try:
            modal = page.evaluate(CLOSE_ROW_JS, ["modal", symbol]) or {}
        except Exception as exc:
            modal = {"ok": False, "why": f"modal read failed ({type(exc).__name__})"}
        bad = _close_modal_mismatch(modal, symbol, side, quantity if quantity is not None else _f_or_none(facts.get("size")))
        if bad:
            discarded = self._discard_modal(page)
            return {"ok": False, "clicked": True, "why": _mask_public_text("close modal refused: " + "; ".join(bad)) + (
                "; Discard pressed" if discarded else "; no Discard control found (modal may still be open)"),
                    "row": facts, "modal": modal, **seen}
        try:
            page.click("[data-metis-modal-btn=confirm]", timeout=5_000)
        except Exception as exc:
            return {"ok": False, "clicked": True, "why": f"Close Position click raised {type(exc).__name__}; outcome unknown",
                    "row": facts, "modal": modal, **seen}
        return {"ok": True, "clicked": True, "why": "Close Position confirmed", "row": facts, "modal": modal, **seen}

    @staticmethod
    def _discard_modal(page: Any) -> bool:
        """Press the modal's Discard (or its x) — never the confirm button.
        Belt and braces on top of CLOSE_ROW_JS's tagging: whatever carries the
        discard tag is read back first, and anything that reads "Close
        Position" is left alone (Escape is the only fallback)."""
        try:
            loc = page.locator("[data-metis-modal-btn=discard]")
            if loc.count() == 1:
                label = " ".join(str(x or "") for x in (loc.inner_text(timeout=2_000), loc.get_attribute("aria-label")))
                if not re.search(r"close\s+position|position", label, re.I):
                    page.click("[data-metis-modal-btn=discard]", timeout=5_000)
                    return True
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    # False until a measured run (edit-dialog-probe, DIALOG-MEASURE) has read
    # the real dialog and edit_dialog_mismatch passes on it. While False an
    # ARMED modify_bracket locates the edit control and refuses before any
    # click. Flipping it is a held Tier-2 change (it arms a live click).
    EDIT_DIALOG_MEASURED = False

    def _locate_edit_control(self, page: Any, symbol: str, side: Optional[str],
                             quantity: Optional[float]) -> Dict[str, Any]:
        """The ONE Positions row for ``symbol`` (its side / size must match
        when given) -> hover -> the row's ONE modify-type control, chosen by
        the NAME of its icon (the trio is text-less, live test #14191). Clicks
        nothing; tags the control ``data-metis-edit-ctl``."""
        self._show_tab(page, "tab_positions")
        try:
            loc = page.evaluate(CLOSE_ROW_JS, ["locate", symbol]) or {}
        except Exception as exc:
            return {"ok": False, "why": f"locate failed ({type(exc).__name__})"}
        if not loc.get("ok"):
            return {"ok": False, "rows": loc.get("rows"),
                    "why": _mask_public_text(loc.get("why") or "row not located")}
        facts = loc.get("facts") or {}
        bad = _row_facts_mismatch(facts, side, quantity, None, rel_tol=1e-6)
        if bad:
            return {"ok": False, "row": facts, "why": "row does not match the position: " + "; ".join(bad)}
        try:
            page.hover("[data-metis-close-row]", timeout=5_000)
            page.wait_for_timeout(400)
            ctl = page.evaluate(EDIT_DIALOG_JS, ["controls", symbol]) or {}
        except Exception as exc:
            return {"ok": False, "row": facts, "why": f"hover / controls failed ({type(exc).__name__})"}
        return {"ok": bool(ctl.get("ok")), "row": facts, "controls": _mask_controls(ctl.get("controls")),
                "chosen": ctl.get("chosen"),
                "why": _mask_public_text(str(ctl.get("why") or "edit control located"))}

    def _open_edit_dialog(self, page: Any, symbol: str) -> Dict[str, Any]:
        """Mark the dialogs already showing, click the tagged edit control,
        read the ONE dialog that appeared. A click that raised is OUTCOME
        UNKNOWN (``clicked: "unknown"``): never treated as opened."""
        try:
            page.evaluate(EDIT_DIALOG_JS, ["snapshot", symbol])
        except Exception as exc:
            return {"ok": False, "clicked": False, "why": f"dialog snapshot failed ({type(exc).__name__})"}
        try:
            page.click("[data-metis-edit-ctl]", timeout=5_000)
        except Exception as exc:
            return {"ok": False, "clicked": "unknown", "why": f"edit control click raised {type(exc).__name__}; outcome unknown"}
        page.wait_for_timeout(800)
        return {"ok": True, "clicked": True, "dialog": self._read_edit_dialog(page, symbol)}

    @staticmethod
    def _read_edit_dialog(page: Any, symbol: str) -> Dict[str, Any]:
        try:
            return page.evaluate(EDIT_DIALOG_JS, ["dialog", symbol]) or {"ok": False, "why": "empty dialog read"}
        except Exception as exc:
            return {"ok": False, "why": f"dialog read failed ({type(exc).__name__})"}

    @staticmethod
    def _cancel_edit_dialog(page: Any) -> bool:
        """Press the edit dialog's OWN cancel, fenced to INSIDE the dialog
        EDIT_DIALOG_JS tagged (never ``close_order_ticket``'s page-wide
        ``[data-metis-btn=close]``, which can be the sidebar ticket's: review
        of #15327). Escape is the only fallback; it presses nothing."""
        try:
            loc = page.locator("[data-metis-edit-dialog] [data-metis-edit-btn=cancel]")
            if loc.count() == 1:
                loc.first.click(timeout=5_000)
                return True
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    def probe_edit_dialog(self, page: Any, symbol: str, *, click: bool = False) -> Dict[str, Any]:
        """MEASURE the position edit dialog (DIALOG-MEASURE). Submits nothing.
        Locates our symbol's ONE Positions row and its pencil by icon name;
        with ``click`` (and only when that row exists, i.e. a position is
        open) clicks THAT pencil, reads the dialog that appeared, reports what
        ``edit_dialog_mismatch`` would say about it, then presses the
        dialog's own Cancel and re-reads that it closed. Never Bid/Ask,
        Buy/Sell, a close control or the sidebar ticket."""
        out: Dict[str, Any] = {"symbol": symbol, "click": click, "one_click": self.read_one_click(page)}
        loc = self._locate_edit_control(page, symbol, None, None)
        out["locate"] = loc
        if not loc.get("ok") or not click:
            out["stopped"] = "before any click" + ("" if loc.get("ok") else f" ({loc.get('why')})")
            return out
        opened = self._open_edit_dialog(page, symbol)
        out["clicked"] = opened.get("clicked")
        if not opened.get("ok"):
            out["why"] = opened.get("why")
            out["escape"] = self._cancel_edit_dialog(page) if opened.get("clicked") else None
            return out
        dlg = opened["dialog"]
        out["dialog"] = dlg
        out["would_refuse"] = edit_dialog_mismatch(dlg, symbol, parse_number((loc.get("row") or {}).get("size")))
        out["cancel_pressed"] = self._cancel_edit_dialog(page)
        page.wait_for_timeout(600)
        after = self._read_edit_dialog(page, symbol)
        out["closed_after_cancel"] = (not after.get("ok")) and after.get("fresh") == 0
        return out

    def modify_bracket(self, page: Any, position: Position,
                       stop_loss: Optional[float], take_profit: Optional[float],
                       *, arm: bool = False) -> Dict[str, Any]:
        """Set a position's SL/TP through its row's pencil and the dialog it
        opens, every step read back (go-live review of #15316):
        the ONE row for the symbol, side and size matching the position ->
        the pencil chosen by icon name -> the ONE dialog that APPEARED,
        passing ``edit_dialog_mismatch`` (names the symbol, read-only qty ==
        the position's, mode Price, its own boxed submit) -> type SL/TP into
        ITS fields -> read back -> ITS submit. Any refusal presses the
        dialog's own Cancel. A click that raised is refused as outcome
        unknown. ``_find_form`` / ``close_order_ticket`` are never used here:
        they anchor on the sidebar order ticket."""
        if stop_loss is None and take_profit is None:
            return {"ok": False, "clicked": False, "why": "nothing to modify"}
        # Diagnostic only (ORDER ENTRY rule 1): recorded on every result below,
        # gated on by nothing.
        oc = self.read_one_click(page)
        want = {k: v for k, v in (("stop_loss", stop_loss), ("take_profit", take_profit)) if v is not None}
        loc = self._locate_edit_control(page, position.symbol, position.side, position.quantity)
        seen = {"one_click": oc, "row": loc.get("row"), "controls": loc.get("controls"), "chosen": loc.get("chosen")}
        if not loc.get("ok"):
            return {"ok": False, "clicked": False, **seen, "why": f"edit control: {loc.get('why')}"}
        if not arm:
            return {"ok": True, "clicked": False, **seen, "why": "disarmed: stopped before the edit control"}
        if not self.EDIT_DIALOG_MEASURED:
            # PROP-TRAIL go-live review (manager, 2026-10-01): refuse before
            # any click until a measured run has proven the dialog.
            return {"ok": False, "clicked": False, **seen,
                    "why": "refused: the SL/TP edit dialog is unmeasured (an armed walk could "
                           "submit the sidebar order ticket instead)"}
        if position.quantity is None:
            return {"ok": False, "clicked": False, **seen, "why": "position size unknown: refusing"}
        opened = self._open_edit_dialog(page, position.symbol)
        if not opened.get("ok"):
            if opened.get("clicked"):
                self._cancel_edit_dialog(page)
            return {"ok": False, "clicked": bool(opened.get("clicked")), **seen, "why": opened.get("why")}
        dlg = opened["dialog"]
        bad = edit_dialog_mismatch(dlg, position.symbol, position.quantity, tuple(want))
        if bad:
            self._cancel_edit_dialog(page)
            return {"ok": False, "clicked": True, **seen, "dialog": dlg,
                    "why": _mask_public_text("edit dialog refused: " + "; ".join(bad)) + "; Cancel pressed"}
        try:
            for k, v in want.items():
                page.fill(f"[data-metis-edit-dialog] [data-metis-edit-field={k}]", _fmt_num(v), timeout=5_000)
        except Exception as exc:
            self._cancel_edit_dialog(page)
            return {"ok": False, "clicked": True, **seen, "why": f"fill raised {type(exc).__name__}; Cancel pressed"}
        dlg = self._read_edit_dialog(page, position.symbol)
        bad = edit_dialog_mismatch(dlg, position.symbol, position.quantity, tuple(want)) \
            + verify_form_values(dlg.get("fields") or {}, want)
        if bad:
            self._cancel_edit_dialog(page)
            return {"ok": False, "clicked": True, **seen, "dialog": dlg,
                    "why": _mask_public_text("read-back refused: " + "; ".join(bad)) + "; Cancel pressed"}
        try:
            page.click("[data-metis-edit-dialog] [data-metis-edit-btn=submit]", timeout=5_000)
        except Exception as exc:
            return {"ok": False, "clicked": True, **seen,
                    "why": f"submit click raised {type(exc).__name__}; outcome unknown"}
        self._confirm_dialog(page)
        return {"ok": True, "clicked": True, **seen, "why": "submit clicked"}
