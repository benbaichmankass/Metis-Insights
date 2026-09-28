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

import json
import math
import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
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
EXTRACT_TABLES_JS = r"""
() => {
  const txt = el => (el.innerText || el.textContent || '').trim();
  const out = [];
  const seen = new Set();
  const push = (kind, headers, rows) => {
    const sig = kind === 'divgrid' ? headers.join('|') : null;
    if (sig && seen.has(sig)) return;
    if (sig) seen.add(sig);
    out.push({kind, headers, rows});
  };
  for (const t of document.querySelectorAll('table')) {
    const headers = [...t.querySelectorAll('thead th, tr:first-child th')].map(txt);
    const rows = [...t.querySelectorAll('tbody tr')].map(r => [...r.querySelectorAll('td')].map(txt));
    if (headers.length) push('table', headers, rows);
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
    not a match either way, so it is skipped rather than guessed."""
    hits = set()
    for key in _SYMBOL_KEYS:
        val = obj.get(key)
        if not isinstance(val, str):
            continue
        hit = symbols_upper.get(val.strip().upper())
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
# 1. ONE-CLICK TRADING must read OFF before anything in the order area is
#    touched. With it on, a click on a chart Buy/Sell button IS an order, with
#    no SL and no TP. "Could not read it" is treated as ON.
# 2. The chart's Buy/Sell PRICE buttons are never an opener: a ticket is opened
#    only by an explicitly named control (:data:`TICKET_OPENER_NAMES`) or a
#    double-click on the watchlist row of the symbol.
# 3. Fields are found INSIDE one form container by their visible label, and
#    each must be unique there; an ambiguous or missing field refuses.
# 4. Every field is READ BACK after typing and compared to what was meant.
#    A mismatch refuses (and closes the form) before submit is ever reached.
# 5. ``arm=False`` (the default) stops before the final control. Only
#    ``src/prop/prop_executor.py`` in ``live`` mode passes ``arm=True``.
# 6. A click reports that it CLICKED, never that an order exists; existence is
#    the executor's re-read.

# Accessible names that open an order ticket, exact match only. NOT MEASURED:
# the probe prints every button name so this list can be fixed from the log.
TICKET_OPENER_NAMES: Sequence[str] = ("New Order", "New order", "Create Order", "Create order",
                                      "Place Order", "Place order", "Order Entry", "Trade")

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
  const inputs = [...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), [role=spinbutton]')].filter(vis);
  const hits = {};
  for (const inp of inputs) {
    const lab = labelOf(inp);
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
  for (const [k, r] of Object.entries(BP)) {
    const m = btns.filter(b => r.test(txt(b) || b.getAttribute('aria-label') || b.value || ''));
    if (m.length === 1) { m[0].setAttribute('data-metis-btn', k); out.buttons[k] = txt(m[0]) || m[0].getAttribute('aria-label') || ''; }
    else if (m.length > 1) out.ambiguous.push('btn:' + k);
  }
  for (const cb of form.querySelectorAll('input[type=checkbox], [role=checkbox], [role=switch]')) {
    const lab = labelOf(cb);
    for (const k of ['stop_loss', 'take_profit']) {
      if (FP[k].test(lab)) {
        cb.setAttribute('data-metis-field', k + '_toggle');
        out.checkboxes.push({field: k, checked: !!(cb.checked || cb.getAttribute('aria-checked') === 'true')});
      }
    }
  }
  out.form_text = txt(form).slice(0, 2000);
  return out;
}
"""

# Reads the "One-click trading" control. Returns "on" / "off" / "unknown" plus
# the redacted shape of what it looked at.
ONE_CLICK_JS = r"""
() => {
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').trim();
  const lab = [...document.querySelectorAll('body *')].find(el =>
    el.children.length === 0 && /^one[- ]click trading$/i.test(txt(el)));
  if (!lab) return {state: 'unknown', why: 'label not found'};
  const states = [];
  for (let e = lab, i = 0; e && i < 4; e = e.parentElement, i++) {
    const cands = [e, ...e.querySelectorAll('input[type=checkbox], [role=switch], [role=checkbox], [aria-pressed], [aria-checked]')];
    for (const c of cands) {
      if (c.type === 'checkbox') states.push(c.checked ? 'on' : 'off');
      const ac = c.getAttribute && (c.getAttribute('aria-checked') || c.getAttribute('aria-pressed'));
      if (ac === 'true') states.push('on'); else if (ac === 'false') states.push('off');
    }
    if (states.length) break;
  }
  const uniq = [...new Set(states)];
  return {state: uniq.length === 1 ? uniq[0] : 'unknown',
          why: uniq.length === 1 ? 'read' : (uniq.length ? 'conflicting controls' : 'no checkbox/switch/aria state near the label'),
          chain: (() => { const c = []; for (let e = lab, i = 0; e && e.tagName && i < 4; e = e.parentElement, i++)
            c.push(e.tagName.toLowerCase() + ((typeof e.className === 'string' && e.className) ? '.' + e.className.trim().split(/\s+/).join('.') : '')
                   + [...e.attributes].map(a => a.name).filter(n => n.startsWith('aria-') || n.startsWith('data-')).map(n => '[' + n + ']').join(''));
            return c; })()};
}
"""


# Finds ONE row of the orders or positions table by an exact key cell and ONE
# control in it by an anchored pattern (text, aria-label or title), and tags
# that control data-metis-row-action. Never clicks.
ROW_ACTION_JS = r"""
(args) => {
  const [kind, keyCol, key, actionPat] = args;
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').trim();
  const norm = s => s.replace(/\s+/g, ' ').trim().toLowerCase();
  document.querySelectorAll('[data-metis-row-action]').forEach(e => e.removeAttribute('data-metis-row-action'));
  const want = kind === 'orders' ? /^(order id|sts)$/ : /^(position volume|position id|open price|avg fill price|open p&l)$/;
  const act = new RegExp(actionPat, 'i');
  let rows = [], ctl = [];
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, tr:first-child th')].map(h => norm(txt(h)));
    if (!hs.some(h => want.test(h))) continue;
    const ci = hs.indexOf(norm(keyCol));
    if (ci < 0) continue;
    for (const r of t.querySelectorAll('tbody tr')) {
      const cells = [...r.querySelectorAll('td')];
      if (cells[ci] && txt(cells[ci]) === key) rows.push(r);
    }
  }
  if (rows.length === 1) {
    ctl = [...rows[0].querySelectorAll('button, [role=button], [title], [aria-label]')].filter(b =>
      act.test(txt(b)) || act.test(b.getAttribute('aria-label') || '') || act.test(b.getAttribute('title') || ''));
    if (ctl.length === 1) ctl[0].setAttribute('data-metis-row-action', '1');
  }
  return {rows: rows.length, controls: ctl.length};
}
"""


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
                       rel_tol: float = 1e-9) -> List[str]:
    """Pure: compare what the form SHOWS against what we meant to type.
    Returns the list of mismatches (empty = verified). A field we meant to set
    that the form does not show, or shows unparseable, is a mismatch."""
    bad: List[str] = []
    for k, v in want.items():
        shown = parse_number((fields.get(k) or {}).get("value"))
        if shown is None:
            bad.append(f"{k}: not readable back")
        elif not math.isclose(shown, float(v), rel_tol=rel_tol, abs_tol=1e-12):
            bad.append(f"{k}: typed {_fmt_num(v)}, form shows {_fmt_num(shown)}")
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
    def read_one_click(self, page: Any) -> Dict[str, Any]:
        """``{"state": "on"|"off"|"unknown", ...}``. Read-only. Anything but a
        definite ``off`` is treated as ON by every caller."""
        try:
            got = page.evaluate(ONE_CLICK_JS) or {}
        except Exception as exc:
            return {"state": "unknown", "why": f"probe failed ({type(exc).__name__})"}
        if got.get("state") not in ("on", "off"):
            got["state"] = "unknown"
        return got

    def _find_form(self, page: Any) -> Dict[str, Any]:
        try:
            return page.evaluate(ORDER_FORM_JS, [FORM_FIELD_PATTERNS, FORM_BUTTON_PATTERNS]) or {}
        except Exception as exc:
            return {"found": False, "error": type(exc).__name__}

    def open_order_ticket(self, page: Any, venue_symbol: str) -> Dict[str, Any]:
        """Open the order-entry form for ``venue_symbol`` WITHOUT pressing any
        order control. Returns ``{"opened": bool, "via": ..., "form": ...,
        "one_click": ...}``. Refuses (``opened: False``) unless one-click
        trading reads OFF. Never uses a chart Buy/Sell price button."""
        oc = self.read_one_click(page)
        out: Dict[str, Any] = {"opened": False, "via": None, "one_click": oc, "form": {}}
        if oc.get("state") != "off":
            out["refused"] = f"one-click trading is {oc.get('state')} ({oc.get('why')}); nothing touched"
            return out
        form = self._find_form(page)
        if form.get("found"):
            out.update(opened=True, via="already_open", form=form)
            return out
        for name in TICKET_OPENER_NAMES:
            try:
                loc = page.get_by_role("button", name=name, exact=True)
                if loc.count() == 1:
                    loc.first.click(timeout=5_000)
                    page.wait_for_timeout(1_000)
                    form = self._find_form(page)
                    if form.get("found"):
                        out.update(opened=True, via=f"button:{name}", form=form)
                        return out
            except Exception:
                continue
        # Double-click the symbol's WATCHLIST row (the table MEASURED with
        # headers Symbol/Bid/Ask/...). A row, never a price button.
        try:
            rows = page.locator("tr.instrument, tr[data-row-id]").filter(
                has=page.locator("td", has_text=re.compile(r"^\s*" + re.escape(venue_symbol) + r"\s*$")))
            if rows.count() == 1:
                rows.first.dblclick(timeout=5_000)
                page.wait_for_timeout(1_000)
                form = self._find_form(page)
                if form.get("found"):
                    out.update(opened=True, via="watchlist_dblclick", form=form)
                    return out
        except Exception:
            pass
        out["form"] = form
        out["refused"] = "no opener produced an order form (TICKET_OPENER_NAMES / watchlist row)"
        return out

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
            "fields": {k: {"label": v.get("label"), "disabled": v.get("disabled")}
                       for k, v in (form.get("fields") or {}).items()},
            "buttons": form.get("buttons") or {},
            "checkboxes": form.get("checkboxes") or [],
            "ambiguous": form.get("ambiguous") or [],
            "canvases_in_form": form.get("canvases_in_form"),
            "canvases_in_page": form.get("canvases_in_page"),
            "inputs_in_page": form.get("inputs_in_page"),
            "form_text": form.get("form_text", ""),
        }
        if opened.get("opened"):
            result["closed"] = self.close_order_ticket(page)
        return result

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
                                form={"one_click": opened.get("one_click")})
        form = opened["form"]

        def refuse(why: str, f: Optional[Mapping[str, Any]] = None) -> PlaceAttempt:
            self.close_order_ticket(page)
            return PlaceAttempt(stage="refused", detail=why, form=dict(f or form))

        if form.get("ambiguous"):
            return refuse(f"ambiguous form controls: {form['ambiguous']}")
        # The form must NAME the instrument, as a whole word: an order typed
        # into a ticket for the wrong symbol is the worst silent failure here.
        if not re.search(r"(?<![A-Z0-9])" + re.escape(spec.venue_symbol.upper()) + r"(?![A-Z0-9])",
                         str(form.get("form_text") or "").upper()):
            return refuse(f"the open form does not name {spec.venue_symbol}")
        need = ["quantity", "stop_loss", "take_profit"] + (["price"] if spec.order_type == "limit" else [])
        missing = [k for k in need if k not in (form.get("fields") or {})]
        if missing:
            return refuse(f"form fields not found: {missing}")
        if "submit" not in (form.get("buttons") or {}):
            return refuse("no unique submit control in the form")
        try:
            side_btn = "side_buy" if spec.side == "long" else "side_sell"
            if side_btn in form.get("buttons", {}):
                page.click(f"[data-metis-btn={side_btn}]", timeout=5_000)
            elif form.get("buttons", {}).get("side_buy") is None and form.get("buttons", {}).get("side_sell") is None:
                return refuse("no side selector in the form")
            type_btn = "type_limit" if spec.order_type == "limit" else "type_market"
            if type_btn in form.get("buttons", {}):
                page.click(f"[data-metis-btn={type_btn}]", timeout=5_000)
            form = self._find_form(page)
            for leg in ("stop_loss", "take_profit"):
                fld = (form.get("fields") or {}).get(leg) or {}
                if fld.get("disabled"):
                    tog = [c for c in form.get("checkboxes") or [] if c.get("field") == leg]
                    if len(tog) != 1 or tog[0].get("checked"):
                        return refuse(f"{leg} field is disabled and has no single enabling toggle")
                    page.click(f"[data-metis-field={leg}_toggle]", timeout=5_000)
            want = {"quantity": spec.quantity, "stop_loss": spec.stop_loss, "take_profit": spec.take_profit}
            if spec.order_type == "limit":
                want["price"] = float(spec.limit_price)
            form = self._find_form(page)
            for k, v in want.items():
                page.fill(f"[data-metis-field={k}]", _fmt_num(v), timeout=5_000)
            form = self._find_form(page)
        except Exception as exc:
            return refuse(f"typing into the form failed ({type(exc).__name__})")
        mism = verify_form_values(form.get("fields") or {}, want)
        if mism:
            return refuse("form read-back mismatch: " + "; ".join(mism), form)
        if not arm:
            self.close_order_ticket(page)
            return PlaceAttempt(stage="form_verified", detail="disarmed: stopped before submit", form=form)
        # ── the one click that changes the account ──
        oc = self.read_one_click(page)
        if oc.get("state") != "off":
            return refuse(f"one-click trading changed to {oc.get('state')} before submit")
        try:
            page.click("[data-metis-btn=submit]", timeout=5_000)
        except Exception as exc:
            # The click may or may not have landed: report submitted so the
            # caller treats it as UNCONFIRMED and re-reads; never resubmit.
            return PlaceAttempt(stage="submitted", submitted=True,
                                detail=f"submit click raised {type(exc).__name__}; outcome unknown", form=form)
        self._confirm_dialog(page)
        return PlaceAttempt(stage="submitted", submitted=True, detail="submit clicked", form=form)

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

    def flatten(self, page: Any, symbol: Optional[str] = None, *, arm: bool = False) -> Dict[str, Any]:
        """Close ONE symbol's position at market (never 'close all': a
        symbol is required so a flatten can never widen past its target)."""
        if not symbol:
            return {"ok": False, "clicked": False, "why": "symbol required (no close-all)"}
        self._show_tab(page, "tab_positions")
        return self._row_action(page, "positions", "Symbol", symbol,
                                r"^(close|close position|×|✕|x)$", arm)

    def modify_bracket(self, page: Any, position: Position,
                       stop_loss: Optional[float], take_profit: Optional[float],
                       *, arm: bool = False) -> Dict[str, Any]:
        """Set a position's SL/TP through its row's edit control and the same
        read-back-verified form as ``place_bracket``."""
        if stop_loss is None and take_profit is None:
            return {"ok": False, "clicked": False, "why": "nothing to modify"}
        oc = self.read_one_click(page)
        if oc.get("state") != "off":
            return {"ok": False, "clicked": False, "why": f"one-click trading is {oc.get('state')}"}
        self._show_tab(page, "tab_positions")
        opened = self._row_action(page, "positions", "Symbol", position.symbol,
                                  r"^(edit|modify|✎|sl/tp|edit position)$", True)
        if not opened.get("clicked"):
            return {"ok": False, "clicked": False, "why": f"edit control: {opened.get('why')}"}
        page.wait_for_timeout(1_000)
        form = self._find_form(page)
        want = {k: v for k, v in (("stop_loss", stop_loss), ("take_profit", take_profit)) if v is not None}
        missing = [k for k in want if k not in (form.get("fields") or {})]
        if missing or "submit" not in (form.get("buttons") or {}):
            self.close_order_ticket(page)
            return {"ok": False, "clicked": False, "why": f"edit form incomplete (missing {missing})"}
        for k, v in want.items():
            page.fill(f"[data-metis-field={k}]", _fmt_num(v), timeout=5_000)
        form = self._find_form(page)
        mism = verify_form_values(form.get("fields") or {}, want)
        if mism or not arm:
            self.close_order_ticket(page)
            return {"ok": not mism, "clicked": False,
                    "why": ("read-back mismatch: " + "; ".join(mism)) if mism else "disarmed: stopped before submit"}
        page.click("[data-metis-btn=submit]", timeout=5_000)
        self._confirm_dialog(page)
        return {"ok": True, "clicked": True, "why": "submit clicked"}
