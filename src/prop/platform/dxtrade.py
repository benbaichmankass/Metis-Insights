"""DXtrade adapter — Breakout's DXtrade white-label ("Breakout Terminal").

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.3.

Slice 1 is the READ path only: ``login``, ``read_account``, ``read_positions``,
``read_orders``, ``read_instrument_specs``. The order-control methods
inherit the base class's ``NotImplementedError``. Nothing in this module
clicks an order control; the only clicks are the login button, the
Positions / Orders *view tabs*, and (for ``read_instrument_specs``) a
symbol search fill plus an instrument-info panel open by accessible name —
none of them change anything, only what is displayed.

``read_instrument_specs`` (W6-PROP-E1, 2026-09-28) is a best-effort probe:
:data:`INSTRUMENT_SPEC_LABELS` and :data:`_INSTRUMENT_INFO_NAMES` are **NOT
MEASURED** against this terminal — no served i18n dictionary entry for
instrument specs has been found, unlike the account/position/order labels
below. A symbol the terminal does not expose specs for (via this search+
info-panel path) returns every field ``None`` with a short redacted
``raw_snippet`` for debugging, never a fabricated number.

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

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from src.prop.platform.base import (
    AccountSnapshot,
    FeasibilityError,
    InstrumentSpec,
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
    # ── instrument search / info (NOT MEASURED: best-effort guesses at
    # common DXtrade white-label conventions, unverified against THIS
    # terminal — see read_instrument_specs and its module-level note) ──
    "symbol_search": ('input[type=search], input[placeholder*="Search" i], '
                      'input[placeholder*="Symbol" i], [role=searchbox]'),
}

# Accessible names tried, in order, to open a per-symbol specification
# panel. NOT MEASURED against this terminal — common DXtrade/MT-style
# labels, tried across several ARIA roles since the panel's real markup is
# unknown until a live run reports which (if any) matched.
_INSTRUMENT_INFO_NAMES: Sequence[str] = (
    "Instrument Info", "Symbol Info", "Contract Specification",
    "Specification", "Instrument Details", "Info",
)
_INSTRUMENT_INFO_ROLES: Sequence[str] = ("tab", "button", "menuitem", "link")

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

# Per-instrument specification labels. **NOT MEASURED** — unlike
# ACCOUNT_LABELS these are not sourced from a served i18n dictionary (no
# such dictionary entry for instrument specs has been found yet); they are
# best-effort guesses at common DXtrade/MT-style terminology, most specific
# first. A field this misses stays ``None`` and lands in
# ``InstrumentSpec.unparsed`` rather than being fabricated.
INSTRUMENT_SPEC_LABELS: Dict[str, Sequence[str]] = {
    "digits": ("Digits", "Price Precision", "Decimal Places"),
    "contract_size": ("Contract Size", "Lot Size", "Unit Size", "Contract Unit"),
    "min_qty": ("Min Volume", "Minimum Volume", "Min Lot", "Min Quantity", "Min Qty"),
    "qty_step": ("Volume Step", "Lot Step", "Qty Step", "Volume Increment", "Step"),
}



# ── pure helpers ─────────────────────────────────────────────────────────


_DECIMAL_COMMA_RE = re.compile(r"[+-]?\d+,\d{1,2}")


def parse_number(text: Optional[str]) -> Optional[float]:
    """``"$4,724.50"`` → 4724.5; ``"(12.30)"``/``"−12.30"`` → -12.3;
    ``"0,01"`` → 0.01 (a LONE comma before 1-2 digits with no decimal
    point already present is a European-style decimal comma, not a
    thousands separator -- dropping it silently turned "0,01" into 1.0);
    junk → None."""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    t = t.replace("\u2212", "-").replace(" ", "").replace("\u00a0", "")
    t = re.sub(r"^(USD|USDT|EUR|GBP)", "", t)
    t = re.sub(r"(USD|USDT|EUR|GBP)$", "", t)
    t = re.sub(r"[$€£]", "", t)
    if "." not in t and _DECIMAL_COMMA_RE.fullmatch(t):
        t = t.replace(",", ".")
    else:
        t = t.replace(",", "")
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


def parse_instrument_spec(symbol: str, page_text: str, raw_snippet: Optional[str] = None) -> InstrumentSpec:
    """Read labelled instrument-spec fields out of the visible page text.

    Same label/value scanning as :func:`parse_account_metrics`, over
    :data:`INSTRUMENT_SPEC_LABELS` instead of ``ACCOUNT_LABELS``. ``digits``
    is coerced to ``int`` when parseable; every other field stays a float.
    Never fabricates a number for a label it did not find.
    """
    text = page_text or ""
    all_labels = sorted({lb for lbs in INSTRUMENT_SPEC_LABELS.values() for lb in lbs},
                        key=len, reverse=True)
    text = re.sub(r"(?<=[\d)])(?=(?:" + "|".join(re.escape(lb) for lb in all_labels) + r"))", "\n", text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    spec = InstrumentSpec(symbol=symbol, raw_snippet=raw_snippet)
    for fld, labels in INSTRUMENT_SPEC_LABELS.items():
        raw = _label_value_pairs(lines, labels)
        val = parse_number(raw)
        if val is None:
            spec.unparsed.append(fld)
        elif fld == "digits":
            spec.digits = int(val)
        else:
            setattr(spec, fld, val)
    return spec


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
        for row in t.get("rows") or []:
            sym = (_cell(row, c_sym) or "").strip()
            if not sym:
                continue
            qty = parse_number(_cell(row, c_qty))
            out.append(WorkingOrder(
                symbol=sym, side=_side(_cell(row, c_side)),
                order_type=(_cell(row, c_type) or "").strip() or None,
                quantity=abs(qty) if qty is not None else None,
                price=parse_number(_cell(row, c_px)),
                raw={h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)},
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

    # ---- instrument specs (NOT MEASURED — best-effort; see the module note
    # on INSTRUMENT_SPEC_LABELS) -----------------------------------------
    def _search_symbol(self, page: Any, symbol: str) -> bool:
        """Type ``symbol`` into a search box, if one exists. Read-only: it
        fills an input and never submits an order form."""
        try:
            loc = page.locator(SELECTORS["symbol_search"])
            if loc.count() == 0:
                return False
            loc.first.fill(symbol, timeout=3_000)
            page.wait_for_timeout(800)
            return True
        except Exception:
            return False

    def _open_instrument_info(self, page: Any) -> bool:
        """Click a specification/info panel by accessible name, if one
        exists among :data:`_INSTRUMENT_INFO_NAMES`. View-only, like
        :meth:`_show_tab` — never a button whose text is not an exact name
        from that list."""
        for name in _INSTRUMENT_INFO_NAMES:
            name_re = self._tab_name_re(name)
            for role in _INSTRUMENT_INFO_ROLES:
                try:
                    loc = page.get_by_role(role, name=name_re)
                    if loc.count() > 0:
                        loc.first.click(timeout=3_000)
                        page.wait_for_timeout(500)
                        return True
                except Exception:
                    continue
        return False

    def read_instrument_specs(self, page: Any, symbols: Sequence[str],
                              secrets: Sequence[str] = ()) -> List[InstrumentSpec]:
        """One :class:`InstrumentSpec` per symbol. ``secrets`` (username,
        password) are redacted into the WHOLE page text before any
        substring of it is kept or sliced — never after — so a slice
        boundary landing inside a secret, a URL or an e-mail cannot leak a
        fragment redaction would otherwise have caught whole (the #13297
        leak class: redact first, cap/slice after).

        Every symbol is graded independently and never trusted on a stale
        page: if the search box couldn't be filled, no info panel opened,
        the requested symbol never appears in the resulting text, or that
        text is byte-identical to the previous symbol's (the search/open
        reported success but nothing actually changed), every field is
        ``unparsed`` — a leftover panel from a PRIOR symbol is never read
        as this one's. ``search_ok``/``panel_ok`` are always set so a
        caller can print the per-symbol outcome, not just the parse.
        """
        out: List[InstrumentSpec] = []
        prev_text: Optional[str] = None
        for sym in symbols:
            search_ok = self._search_symbol(page, sym)
            panel_ok = self._open_instrument_info(page)
            text = self._page_text(page)
            stale = prev_text is not None and text == prev_text
            prev_text = text
            sym_seen = sym.lower() in text.lower()

            if search_ok and panel_ok and sym_seen and not stale:
                # Best-effort scoping to "the opened panel's text": a window
                # around the symbol's own mention rather than the whole
                # page, so a generic label elsewhere (an order ticket's own
                # "Qty"/"Step" fields) is less likely to be read as this
                # symbol's spec. Still NOT MEASURED against a real panel
                # boundary — see the module note.
                idx = text.lower().find(sym.lower())
                window = text[max(0, idx - 400):idx + 1_200]
                spec = parse_instrument_spec(sym, window)
            else:
                spec = InstrumentSpec(symbol=sym, unparsed=list(INSTRUMENT_SPEC_LABELS.keys()))
            spec.search_ok, spec.panel_ok = search_ok, panel_ok

            if spec.unparsed == list(INSTRUMENT_SPEC_LABELS.keys()):
                # Nothing at all parsed for this symbol: keep a short
                # excerpt around the symbol's own mention, if it appears,
                # to help fix selectors from the (redacted) public log.
                # Redact the WHOLE text, then slice the redacted result —
                # never the other way round.
                redacted = redact_text(text, *secrets)
                idx = redacted.lower().find(sym.lower())
                if idx >= 0:
                    spec.raw_snippet = redacted[max(0, idx - 80):idx + 200].strip()
            out.append(spec)
        return out
