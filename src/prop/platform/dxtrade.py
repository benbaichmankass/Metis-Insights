"""DXtrade adapter — Breakout's DXtrade white-label ("Breakout Terminal").

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.3.

Slice 1 is the READ path only: ``login``, ``read_account``, ``read_positions``,
``read_orders``. The order-control methods inherit the base class's
``NotImplementedError``. Nothing in this module clicks an order control; the
only clicks are the login button and (optionally) the Positions / Orders
*view tabs*, which change what is displayed and nothing else.

Every DXtrade selector lives in :data:`SELECTORS` so the order-placing slice
reuses them. Provenance of each selector:

- ``login_*``, ``twofa_*``, ``password_expired_form``: **MEASURED** — the
  login markup Breakout serves (``https://wss.breakoutprop.com/``, curl,
  2026-09-27), saved as ``tests/fixtures/prop_dxtrade/login_page.html.txt``.
- Everything after login: **NOT MEASURED**. Nobody has logged in from code
  yet. The read path therefore keys on visible LABELS and table HEADER TEXT,
  not class names, and reports what it could not parse (``unparsed``) instead
  of returning zeros. The first live run is the only proof.

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

# Account-metric labels as they are likely to appear in the DXtrade account
# summary. NOT MEASURED — first live run confirms or corrects them.
ACCOUNT_LABELS: Dict[str, Sequence[str]] = {
    "balance": ("Balance",),
    "equity": ("Equity", "Net Liquidation", "NAV"),
    "unrealized": ("Open P&L", "Open PnL", "Unrealized P&L", "Unrealized PnL", "Floating P&L", "P&L"),
    "realized_today": ("Today's P&L", "Daily P&L", "Realized P&L", "Closed P&L"),
    "margin_used": ("Used Margin", "Margin Used", "Margin"),
    "available": ("Available Funds", "Available Margin", "Free Margin", "Available"),
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
    t = t.replace("\u2212", "-").replace(",", "").replace(" ", "")
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
    ``login_error``, ``login_form``, ``logged_in``.
    Order matters: a challenge beats everything, and a visible login form is
    never read as logged in.
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
    return "logged_in"


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
            if not rest:
                for nxt in lines[i + 1:i + 3]:
                    if nxt.strip() and parse_number(nxt.strip()) is not None:
                        return nxt.strip()
    return None


def parse_account_metrics(page_text: str) -> AccountSnapshot:
    """Read labelled account metrics out of the page's visible text."""
    lines = [ln for ln in (page_text or "").splitlines() if ln.strip()]
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


def _is_positions_table(headers: Sequence[str]) -> bool:
    return (_find_col(headers, "Symbol", "Instrument") is not None
            and _find_col(headers, "Quantity", "Qty", "Size", "Volume", "Amount") is not None
            and _find_col(headers, "Open Price", "Entry Price", "Avg Price", "Average Price") is not None)


def _is_orders_table(headers: Sequence[str]) -> bool:
    return (_find_col(headers, "Symbol", "Instrument") is not None
            and _find_col(headers, "Type", "Order Type") is not None
            and _find_col(headers, "Price", "Limit Price", "Stop Price") is not None
            and _find_col(headers, "Open Price", "Entry Price", "Avg Price") is None)


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
        c_qty = _find_col(headers, "Quantity", "Qty", "Size", "Volume", "Amount")
        c_open = _find_col(headers, "Open Price", "Entry Price", "Avg Price", "Average Price")
        c_sl = _find_col(headers, "Stop Loss", "SL")
        c_tp = _find_col(headers, "Take Profit", "TP")
        c_pnl = _find_col(headers, "P&L", "PnL", "Profit", "Unrealized")
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
        c_qty = _find_col(headers, "Quantity", "Qty", "Size", "Volume", "Amount")
        c_px = _find_col(headers, "Limit Price", "Stop Price", "Price")
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


# JS run in the page to extract every HTML table and ARIA grid as plain data.
# Read-only: it walks the DOM and returns text; it does not click or type.
EXTRACT_TABLES_JS = r"""
() => {
  const txt = el => (el.innerText || el.textContent || '').trim();
  const out = [];
  for (const t of document.querySelectorAll('table')) {
    const headers = [...t.querySelectorAll('thead th, tr:first-child th')].map(txt);
    const rows = [...t.querySelectorAll('tbody tr')].map(r => [...r.querySelectorAll('td')].map(txt));
    if (headers.length) out.push({kind: 'table', headers, rows});
  }
  for (const g of document.querySelectorAll('[role=grid], [role=treegrid], [role=table]')) {
    const headers = [...g.querySelectorAll('[role=columnheader]')].map(txt);
    const rows = [...g.querySelectorAll('[role=row]')]
      .map(r => [...r.querySelectorAll('[role=gridcell], [role=cell]')].map(txt))
      .filter(r => r.length);
    if (headers.length) out.push({kind: 'grid', headers, rows});
  }
  return out;
}
"""


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
        try:
            text = page.inner_text("body", timeout=5_000)
        except Exception:
            text = ""
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
            raise FeasibilityError("unknown_page", f"login form never rendered (state={state})")
        state = self.page_state(page)
        if state in ("challenge", "captcha"):
            raise FeasibilityError(state, "bot challenge on the login page")

        page.fill(SELECTORS["login_username"], username)
        page.fill(SELECTORS["login_password"], password)
        page.click(SELECTORS["login_submit"], timeout=self.timeout_ms)

        # Wait for the form to go away, or for something that is not a login.
        deadline_ms, step_ms, waited = self.timeout_ms, 1_000, 0
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
        raise FeasibilityError("timeout", "still on the login form after submit")

    def _page_text(self, page: Any) -> str:
        return page.inner_text("body", timeout=self.timeout_ms)

    def _tables(self, page: Any) -> List[Dict[str, Any]]:
        return page.evaluate(EXTRACT_TABLES_JS) or []

    def _show_tab(self, page: Any, key: str) -> bool:
        """Click a VIEW tab (Positions / Orders) if one exists. View only."""
        try:
            tab = page.get_by_role("tab", name=SELECTORS[key], exact=False)
            if tab.count() > 0:
                tab.first.click(timeout=5_000)
                page.wait_for_timeout(1_000)
                return True
        except Exception:
            pass
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
