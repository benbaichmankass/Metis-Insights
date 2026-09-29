"""Breakout's proprietary terminal (dashboard → "Open Terminal").

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.4 (scoped),
built by lane PROP-TERM (2026-09-28) behind the SAME
:class:`~src.prop.platform.base.PropPlatformAdapter` interface as
``dxtrade`` — including the step-3 order controls PROP-EXEC added (``arm``
defaults to False and stops before the final control).

Why it exists: Breakout sells no new DXtrade accounts (spec S3), so the next
account lands here. Switching an account is ONE config line in
``config/prop_platforms.yaml`` (``platform: breakout_terminal``; the login URL
defaults per platform, :data:`DEFAULT_LOGIN_URL`).

**What is MEASURED and what is not — read this before trusting any selector.**

- ``app.breakoutprop.com`` (the dashboard) answers ``curl`` with a Cloudflare
  403 challenge (MEASURED 2026-09-27, spec § 2.4). From the live VM a default
  headless Chromium got a page that was **neither a challenge nor the DXtrade
  login form** (run 36337076971, issue #13279: ``unknown_page``); its shape was
  not captured. ``scripts/prop/breakout_terminal_probe.py`` is the
  measurement: it records that page's redacted shape and, on request, logs in
  and opens the terminal.
- The dashboard login form, the "Open Terminal" hand-off, the terminal DOM
  and the order ticket are **NOT MEASURED**. Everything below is written
  against **visible labels, ARIA roles and column-header text**, never
  measured class names, and every read that does not find what it expects
  says so (``None`` / ``LookupError`` / ``refused``), never ``0``.
- Tests run on SYNTHETIC fixtures (``tests/fixtures/prop_breakout_terminal/``)
  written to the vocabulary below; they prove the parsers, not the layout.
  The first probe run replaces them with captured, redacted ones.

**Feasibility stops (reported as ``feasibility: <reason>``, never worked
around):** ``challenge`` / ``captcha`` (bot check), ``email_code`` (an
emailed code or number-match at sign-in: spec S27 — if it appears on every
login this adapter is infeasible), ``2fa``, ``login_rejected``,
``no_account`` (logged in, but no proprietary-terminal account to open),
``canvas_ticket`` (order ticket with no DOM inputs), ``no_credentials``,
``unknown_page``, ``timeout``.

**Order-control safety** (same six rules as the dxtrade adapter, plus two
that exist because this layout is unmeasured):

7. A form whose side buttons ARE the submit (a single "Buy / Long" that
   places the order) is refused: a separate, uniquely-named submit control
   is required, so choosing a side can never be the click that trades.
8. No "one-click trading" control found reads as ``unknown`` and refuses.
   Until the probe shows how this terminal arms instant orders, nothing in
   its order area is touched.

No anti-detection of any kind: default headless Chromium, real typing via
``fill``, no stealth plugin, no fingerprint change, no fake jitter.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from src.prop.platform.base import (
    AccountSnapshot,
    BracketSpec,
    FeasibilityError,
    PlaceAttempt,
    Position,
    PropPlatformAdapter,
    WorkingOrder,
)
# Platform-neutral pieces of the dxtrade module: pure parsers, redaction,
# and read-only page JS that takes its vocabulary as an argument. Nothing
# DXtrade-specific (SELECTORS, labels, opener names) is imported.
from src.prop.platform.dxtrade import (
    EXTRACT_TABLES_JS as _DX_EXTRACT_TABLES_JS,
    ONE_CLICK_JS,
    ORDER_FORM_JS,
    PAGE_SHAPE_JS,
    ROW_ACTION_JS as _DX_ROW_ACTION_JS,
    STRUCTURE_JS,
    _fmt_num,
    _label_value_pairs,
    _strip_url,
    check_bracket_spec,
    classify_ticket_surface,
    parse_number,
    render_page_shape,
    render_structure,
    verify_form_values,
)

PLATFORM = "breakout_terminal"
# The dashboard the operator logs in at; "Open Terminal" is reached from it
# (spec S23). NOT MEASURED whether the terminal has its own login URL.
DEFAULT_LOGIN_URL = "https://app.breakoutprop.com/"

# ── page classification vocabulary (NOT MEASURED: generic markers) ────────

_CHALLENGE_MARKERS = (
    "just a moment", "checking your browser", "verify you are human",
    "attention required", "cf-chl", "challenge-platform",
    "enable javascript and cookies to continue",
)
# An emailed code / number match / magic link at sign-in (spec S27).
_EMAIL_CODE_MARKERS = (
    "check your email", "check your inbox", "sent you an email", "we sent a code",
    "we've sent", "we have sent", "verification code", "enter the code",
    "confirmation code", "magic link", "number matching", "match the number",
    "confirm it's you", "confirm it is you", "verify your email",
)
_TWOFA_MARKERS = ("authenticator app", "two-factor", "2fa code", "totp")
_LOGIN_ERROR_MARKERS = (
    "invalid email or password", "invalid username or password", "incorrect password",
    "invalid credentials", "wrong password", "login failed", "user not found",
)
# Logged in, but nothing to open on this platform.
_NO_ACCOUNT_MARKERS = (
    "no accounts", "you don't have any account", "you do not have any account",
    "no active account", "no trading account", "purchase an account", "buy a challenge",
    "get funded", "start a challenge",
)
# Accessible names of the dashboard control that opens the terminal (S23).
OPEN_TERMINAL_NAMES: Sequence[str] = ("Open Terminal", "Open terminal", "Launch Terminal",
                                      "Launch terminal", "Trade Now", "Trade now")
# Text only a trading surface renders. ``terminal`` needs a parsed balance or
# equity AND one of these, AND no Open-Terminal control (the dashboard may
# also show balance/equity per account).
_TERMINAL_SURFACE_MARKERS = ("positions", "open orders", "order book", "orderbook",
                             "place order", "trade history", "order history")

# Account-metric labels, most specific first. ``_label_value_pairs`` anchors
# each at the START of a line, so "Balance" never reads "Available Balance".
ACCOUNT_LABELS: Dict[str, Sequence[str]] = {
    "balance": ("Account Balance", "Wallet Balance", "Cash Balance", "Balance"),
    "equity": ("Account Equity", "Account Value", "Net Liquidation", "Equity"),
    "unrealized": ("Unrealized PnL", "Unrealized P&L", "Unrealised PnL", "Unrealised P&L",
                   "Open PnL", "Open P&L", "uPnL"),
    "realized_today": ("Today's PnL", "Today's P&L", "Daily PnL", "Daily P&L", "Day PnL",
                       "Realized PnL Today"),
    "margin_used": ("Used Margin", "Margin Used", "Position Margin", "Initial Margin"),
    "available": ("Available Margin", "Available Balance", "Free Margin", "Available"),
}

# ── tables (NOT MEASURED: generic perp-terminal header vocabulary) ────────

SYMBOL_COLS = ("Symbol", "Instrument", "Market", "Contract", "Pair", "Asset")
SIDE_COLS = ("Side", "Direction")
QTY_COLS = ("Size", "Quantity", "Qty", "Amount", "Position Size", "Position", "Volume")
ENTRY_COLS = ("Entry Price", "Avg. Entry Price", "Avg Entry Price", "Avg. Entry", "Avg Entry",
              "Open Price", "Average Price", "Avg Price")
PNL_COLS = ("Unrealized PnL", "Unrealized P&L", "Unrealised PnL", "uPnL", "PnL", "P&L")
SL_COLS = ("Stop Loss", "SL")
TP_COLS = ("Take Profit", "TP")
TPSL_COLS = ("TP/SL", "TP / SL")
ORDER_ID_COLS = ("Order ID", "Order Id", "ID")
ORDER_TYPE_COLS = ("Order Type", "Type")
ORDER_PRICE_COLS = ("Limit Price", "Order Price", "Price", "Trigger Price")

# Headers only an orders table carries (exact, case-insensitive).
_ORDER_ONLY = {"order id", "order type", "type", "limit price", "order price", "trigger price",
               "filled", "filled size", "status", "time in force", "reduce only"}

# The dxtrade JS finds DIV grids from a header leaf reading "Symbol" or
# "Instrument"; this terminal may say "Market" / "Contract" / "Pair".
_DX_HEADER_LEAF_RE = "/^(symbol|instrument)$/i"
EXTRACT_TABLES_JS = _DX_EXTRACT_TABLES_JS.replace(
    _DX_HEADER_LEAF_RE, "/^(symbol|instrument|market|contract|pair|asset)$/i")

# The dxtrade row-action JS keys tables on DXtrade headers; widen them to this
# vocabulary. Still exactly-one-row / exactly-one-control, and never clicks.
_DX_ROW_WANT = ("const want = kind === 'orders' ? /^(order id|sts)$/ : "
                "/^(position volume|position id|open price|avg fill price|open p&l|fill price)$/;")
ROW_ACTION_JS = _DX_ROW_ACTION_JS.replace(
    _DX_ROW_WANT,
    "const want = kind === 'orders' ? /^(order id|order type|limit price|trigger price|filled)$/ : "
    "/^(entry price|avg\\. entry price|avg entry price|avg\\. entry|open price|average price|avg price)$/;")

STRUCTURE_LABELS: List[str] = sorted({
    *(lb for lbs in ACCOUNT_LABELS.values() for lb in lbs),
    *SYMBOL_COLS, *SIDE_COLS, *QTY_COLS, *ENTRY_COLS, *SL_COLS, *TP_COLS, *TPSL_COLS,
    "Positions", "Open Orders", "Orders", "Order Book", "Buy", "Sell", "Long", "Short",
    "Place Order", "Open Terminal", "One-click trading",
})

# ── order ticket (NOT MEASURED) ───────────────────────────────────────────

TICKET_OPENER_NAMES: Sequence[str] = ("New Order", "New order", "Place Order", "Place order",
                                      "Order Entry", "Trade")
FORM_FIELD_PATTERNS: Dict[str, str] = {
    "quantity": r"^(quantity|qty|size|amount|order size|lots?)\b",
    "price": r"^(price|limit price|order price|entry price)\b",
    "stop_loss": r"^(stop loss|stop-loss|sl)\b",
    "take_profit": r"^(take profit|take-profit|tp)\b",
}
# ``submit`` deliberately EXCLUDES a bare side label ("Buy", "Buy / Long",
# "Long"): rule 7 — a side button that is also the submit is refused.
FORM_BUTTON_PATTERNS: Dict[str, str] = {
    "side_buy": r"^(buy|long|buy\s*/\s*long)$",
    "side_sell": r"^(sell|short|sell\s*/\s*short)$",
    "type_market": r"^market$",
    "type_limit": r"^limit$",
    "submit": r"^(place order|submit|submit order|confirm order|place buy order|place sell order|"
              r"place long order|place short order|open long|open short)$",
    "close": r"^(cancel|close|×|✕|x)$",
}


# ── pure helpers ─────────────────────────────────────────────────────────


def parse_account_metrics(page_text: str) -> AccountSnapshot:
    """Labelled account metrics out of visible text; unread fields are None
    and listed in ``unparsed``."""
    text = page_text or ""
    all_labels = sorted({lb for lbs in ACCOUNT_LABELS.values() for lb in lbs}, key=len, reverse=True)
    # "…4,724.00Equity4,731.20": split before a label that follows a digit.
    text = re.sub(r"(?<=[\d)])(?=(?:" + "|".join(re.escape(lb) for lb in all_labels) + r"))", "\n", text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    snap = AccountSnapshot()
    for fld, labels in ACCOUNT_LABELS.items():
        raw = _label_value_pairs(lines, labels)
        val = parse_number(raw)
        if val is None:
            snap.unparsed.append(fld)
            continue
        setattr(snap, fld, val)
        m = re.search(r"\b(USD|USDT|USDC|EUR|GBP)\b", raw or "")
        if m and not snap.currency:
            snap.currency = m.group(1)
    return snap


def classify_page(visible: Mapping[str, bool], page_text: str = "", title: str = "") -> str:
    """One of ``challenge``, ``captcha``, ``email_code``, ``2fa``,
    ``login_error``, ``login_form``, ``no_account``, ``terminal``,
    ``dashboard``, ``unknown``. Pure.

    ``visible`` keys: ``captcha_frame``, ``password_input``, ``code_input``,
    ``open_terminal``. Order matters: a challenge beats everything; a visible
    password field is the login form (never logged in, never a code stop);
    ``terminal`` needs a POSITIVE
    reading (balance or equity parsed AND a trading-surface marker) and no
    Open-Terminal control on the page.
    """
    low = f"{title}\n{page_text}".lower()
    if any(m in low for m in _CHALLENGE_MARKERS):
        return "challenge"
    if visible.get("captcha_frame"):
        return "captcha"
    # A visible password field is the login page, whatever else it says (a
    # "we'll send a verification code" reset link must not read as a stop).
    if visible.get("password_input"):
        return "login_error" if any(m in low for m in _LOGIN_ERROR_MARKERS) else "login_form"
    if any(m in low for m in _EMAIL_CODE_MARKERS):
        return "email_code"
    if any(m in low for m in _TWOFA_MARKERS) or visible.get("code_input"):
        return "2fa"
    if visible.get("open_terminal"):
        return "dashboard"
    snap = parse_account_metrics(page_text)
    if (snap.balance is not None or snap.equity is not None) and any(m in low for m in _TERMINAL_SURFACE_MARKERS):
        return "terminal"
    if any(m in low for m in _NO_ACCOUNT_MARKERS):
        return "no_account"
    return "unknown"


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _find_col(headers: Sequence[str], names: Iterable[str]) -> Optional[int]:
    """Exact (case/space-insensitive) header match first, in ``names`` order;
    no substring fallback — "Price" must never read "Entry Price"."""
    norm = [_norm(h) for h in headers]
    for name in names:
        n = _norm(name)
        if n in norm:
            return norm.index(n)
    return None


def _cell(row: Sequence[str], idx: Optional[int]) -> Optional[str]:
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _side(text: Optional[str]) -> Optional[str]:
    t = _norm(text or "")
    if t.startswith("buy") or t.startswith("long"):
        return "long"
    if t.startswith("sell") or t.startswith("short"):
        return "short"
    return None


def split_tpsl(text: Optional[str]) -> "tuple[Optional[float], Optional[float]]":
    """``"105.5 / 95.25"`` → (105.5, 95.25) for a combined TP/SL cell (TP first,
    the header's order). A dash, empty half or a cell that is not exactly two
    parts reads None for the unreadable half — never 0."""
    if text is None:
        return None, None
    parts = [p.strip() for p in re.split(r"\s*/\s*", str(text).strip())]
    if len(parts) != 2:
        return None, None
    return parse_number(parts[0]), parse_number(parts[1])


def _has_any(headers: Sequence[str], names: Iterable[str]) -> bool:
    norm = {_norm(h) for h in headers}
    return any(_norm(n) in norm for n in names)


def _is_positions_table(headers: Sequence[str]) -> bool:
    return (_find_col(headers, SYMBOL_COLS) is not None
            and _find_col(headers, QTY_COLS) is not None
            and _find_col(headers, ENTRY_COLS) is not None
            and not _has_any(headers, _ORDER_ONLY))


def _is_orders_table(headers: Sequence[str]) -> bool:
    return (_find_col(headers, SYMBOL_COLS) is not None
            and _find_col(headers, ENTRY_COLS) is None
            and _has_any(headers, _ORDER_ONLY)
            and _find_col(headers, ORDER_PRICE_COLS) is not None)


def _sl_tp(headers: Sequence[str], row: Sequence[str]) -> "tuple[Optional[float], Optional[float]]":
    c_sl, c_tp = _find_col(headers, SL_COLS), _find_col(headers, TP_COLS)
    if c_sl is not None or c_tp is not None:
        return parse_number(_cell(row, c_sl)), parse_number(_cell(row, c_tp))
    tp, sl = split_tpsl(_cell(row, _find_col(headers, TPSL_COLS)))
    return sl, tp


def positions_from_tables(tables: Sequence[Mapping[str, Any]]) -> Optional[List[Position]]:
    """``None`` = no positions table found (could not look); ``[]`` = found, empty."""
    found, out = False, []
    for t in tables:
        headers = list(t.get("headers") or [])
        if not _is_positions_table(headers):
            continue
        found = True
        c_sym, c_side = _find_col(headers, SYMBOL_COLS), _find_col(headers, SIDE_COLS)
        c_qty, c_entry = _find_col(headers, QTY_COLS), _find_col(headers, ENTRY_COLS)
        c_pnl = _find_col(headers, PNL_COLS)
        for row in t.get("rows") or []:
            sym = (_cell(row, c_sym) or "").strip()
            if not sym:
                continue
            qty = parse_number(_cell(row, c_qty))
            side = _side(_cell(row, c_side))
            if side is None and qty is not None and qty != 0:
                side = "short" if qty < 0 else "long"
            sl, tp = _sl_tp(headers, row)
            out.append(Position(
                symbol=sym, side=side, quantity=abs(qty) if qty is not None else None,
                entry_price=parse_number(_cell(row, c_entry)), stop_loss=sl, take_profit=tp,
                unrealized_pnl=parse_number(_cell(row, c_pnl)),
                raw={h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)},
            ))
    return out if found else None


def orders_from_tables(tables: Sequence[Mapping[str, Any]]) -> Optional[List[WorkingOrder]]:
    """Working orders; same ``None`` vs ``[]`` contract as positions."""
    found, out = False, []
    for t in tables:
        headers = list(t.get("headers") or [])
        if not _is_orders_table(headers):
            continue
        found = True
        c_sym, c_side = _find_col(headers, SYMBOL_COLS), _find_col(headers, SIDE_COLS)
        c_type, c_qty = _find_col(headers, ORDER_TYPE_COLS), _find_col(headers, QTY_COLS)
        c_px, c_id = _find_col(headers, ORDER_PRICE_COLS), _find_col(headers, ORDER_ID_COLS)
        for row in t.get("rows") or []:
            sym = (_cell(row, c_sym) or "").strip()
            if not sym:
                continue
            qty = parse_number(_cell(row, c_qty))
            sl, tp = _sl_tp(headers, row)
            out.append(WorkingOrder(
                symbol=sym, side=_side(_cell(row, c_side)),
                order_type=(_cell(row, c_type) or "").strip() or None,
                quantity=abs(qty) if qty is not None else None,
                price=parse_number(_cell(row, c_px)),
                raw={h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)},
                order_id=(_cell(row, c_id) or "").strip() or None,
                stop_loss=sl, take_profit=tp,
            ))
    return out if found else None


BID_COLS = ("Bid", "Bid Price", "Best Bid")
ASK_COLS = ("Ask", "Ask Price", "Best Ask")


def quote_from_tables(tables: Sequence[Mapping[str, Any]], venue_symbol: str) -> Optional[Dict[str, float]]:
    """``{"bid": .., "ask": ..}`` for ``venue_symbol`` from any table carrying a
    symbol-like column plus Bid and Ask (NOT MEASURED on this terminal).
    ``None`` when no such table, no exact row, or the numbers do not parse or
    are crossed — never a guessed price."""
    for t in tables:
        headers = list(t.get("headers") or [])
        c_sym, c_bid, c_ask = _find_col(headers, SYMBOL_COLS), _find_col(headers, BID_COLS), _find_col(headers, ASK_COLS)
        if c_sym is None or c_bid is None or c_ask is None:
            continue
        for row in t.get("rows") or []:
            if (_cell(row, c_sym) or "").strip().upper() == venue_symbol.upper():
                bid, ask = parse_number(_cell(row, c_bid)), parse_number(_cell(row, c_ask))
                if bid is not None and ask is not None and 0 < bid <= ask:
                    return {"bid": bid, "ask": ask}
    return None


def check_form_shape(form: Mapping[str, Any], spec: BracketSpec) -> Optional[str]:
    """Pure pre-typing check of a discovered order form (rules 3 and 7).
    Returns the refusal reason, or None when the form may be typed into."""
    if form.get("ambiguous"):
        return f"ambiguous form controls: {form['ambiguous']}"
    if not re.search(r"(?<![A-Z0-9])" + re.escape(spec.venue_symbol.upper()) + r"(?![A-Z0-9])",
                     str(form.get("form_text") or "").upper()):
        return f"the open form does not name {spec.venue_symbol}"
    need = ["quantity", "stop_loss", "take_profit"] + (["price"] if spec.order_type == "limit" else [])
    missing = [k for k in need if k not in (form.get("fields") or {})]
    if missing:
        return f"form fields not found: {missing}"
    buttons = form.get("buttons") or {}
    if "submit" not in buttons:
        return ("no distinct submit control (a side button that also places the order is refused "
                "by rule 7; unmeasured layout)")
    side_btn = "side_buy" if spec.side == "long" else "side_sell"
    if side_btn not in buttons:
        return f"no {side_btn} selector in the form"
    return None


# ── the adapter ──────────────────────────────────────────────────────────


class BreakoutTerminalAdapter(PropPlatformAdapter):
    platform = PLATFORM

    def __init__(self, timeout_ms: int = 30_000) -> None:
        self.timeout_ms = timeout_ms
        # "Open Terminal" may open a new tab. The terminal page is remembered
        # per caller page so every method keeps the interface's ``page`` arg.
        self._terminal: Dict[int, Any] = {}

    def _t(self, page: Any) -> Any:
        return self._terminal.get(id(page), page)

    # ---- page probes (read-only) ----------------------------------------
    @staticmethod
    def _frames(page: Any) -> List[Any]:
        try:
            return list(page.frames) or [page]
        except Exception:
            return [page]

    def _page_text(self, page: Any, timeout_ms: Optional[int] = None) -> str:
        parts = []
        for fr in self._frames(page):
            try:
                parts.append(fr.inner_text("body", timeout=timeout_ms or self.timeout_ms))
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

    @staticmethod
    def _count_visible(page: Any, selector: str) -> bool:
        try:
            loc = page.locator(selector)
            return loc.count() > 0 and loc.first.is_visible()
        except Exception:
            return False

    def _open_terminal_locator(self, page: Any) -> Optional[Any]:
        """Exactly one visible control named like "Open Terminal" (button or
        link), else None. Never a partial-text match."""
        for role in ("button", "link"):
            for name in OPEN_TERMINAL_NAMES:
                try:
                    loc = page.get_by_role(role, name=name, exact=True)
                    if loc.count() == 1 and loc.first.is_visible():
                        return loc.first
                except Exception:
                    continue
        return None

    def page_state(self, page: Any) -> str:
        visible = {
            "captcha_frame": self._count_visible(page, (
                "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], "
                "iframe[src*='challenges.cloudflare.com'], .cf-turnstile, .g-recaptcha, .h-captcha")),
            "password_input": self._count_visible(page, "input[type=password]"),
            "code_input": self._count_visible(page, "input[autocomplete=one-time-code], input[name*=code i]"),
            "open_terminal": self._open_terminal_locator(page) is not None,
        }
        try:
            title = page.title()
        except Exception:
            title = ""
        return classify_page(visible, self._page_text(page, 5_000)[:20_000], title)

    @staticmethod
    def _where(page: Any) -> str:
        try:
            return _strip_url(page.url)
        except Exception:
            return "?"

    # ---- login ------------------------------------------------------------
    _STOPS = {"challenge": "challenge", "captcha": "captcha", "email_code": "email_code",
              "2fa": "2fa", "no_account": "no_account"}

    def _wait_state(self, page: Any, until: Sequence[str], budget_ms: int) -> str:
        """Poll ``page_state`` until it is one of ``until`` or a feasibility
        stop (raised), or the wall-clock budget runs out (returns the last
        state)."""
        deadline = time.monotonic() + budget_ms / 1000.0
        state = "unknown"
        while True:
            state = self.page_state(page)
            if state in self._STOPS:
                raise FeasibilityError(self._STOPS[state], f"at {self._where(page)}")
            if state in until or time.monotonic() >= deadline:
                return state
            page.wait_for_timeout(1_000)

    def _submit_login(self, page: Any, username: str, password: str) -> None:
        """Fill the ONE visible password field and the identity field in its
        form, then press that form's submit. Raises ``unknown_page`` when the
        form is not unambiguous."""
        pw = page.locator("input[type=password]")
        if pw.count() != 1:
            raise FeasibilityError("unknown_page", f"{pw.count()} password fields (at {self._where(page)})")
        form = pw.first.locator("xpath=ancestor::form[1]")
        scope = form if form.count() == 1 else page
        ident = scope.locator("input[type=email], input[autocomplete=username], input[name*=email i], "
                              "input[name*=user i], input[type=text]")
        if ident.count() < 1:
            raise FeasibilityError("unknown_page", f"no identity field beside the password (at {self._where(page)})")
        ident.first.fill(username)
        pw.first.fill(password)
        btn = scope.locator("button[type=submit], input[type=submit]")
        if btn.count() != 1:
            btn = scope.get_by_role("button", name=re.compile(r"^\s*(log ?in|sign ?in|continue)\s*$", re.I))
        if btn.count() != 1:
            raise FeasibilityError("unknown_page", f"no single login submit control (at {self._where(page)})")
        btn.first.click(timeout=self.timeout_ms)

    def open_terminal(self, page: Any) -> Any:
        """From the dashboard, press "Open Terminal" (a navigation control,
        not an order control) and remember the page the terminal renders in
        — a new tab if one opens. Returns that page."""
        loc = self._open_terminal_locator(page)
        if loc is None:
            raise FeasibilityError("unknown_page", f"no single Open Terminal control (at {self._where(page)})")
        target = page
        try:
            with page.context.expect_page(timeout=10_000) as info:
                loc.click(timeout=self.timeout_ms)
            target = info.value
        except Exception:
            # No new tab within 10 s: the terminal opened in place, or the
            # click raised before any tab — the wait below tells which.
            pass
        try:
            target.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            pass
        self._terminal[id(page)] = target
        return target

    def _reach_terminal(self, page: Any) -> None:
        state = self._wait_state(page, ("terminal", "dashboard", "login_form", "login_error"), self.timeout_ms)
        if state == "login_error":
            raise FeasibilityError("login_rejected", "the page showed a login error")
        if state == "login_form":
            raise FeasibilityError("timeout", "still on the login form")
        if state == "dashboard":
            term = self.open_terminal(page)
            state = self._wait_state(term, ("terminal",), self.timeout_ms)
            page = term
        if state != "terminal":
            raise FeasibilityError("unknown_page", f"no terminal reading (state={state}, at {self._where(page)})")

    def login(self, page: Any, login_url: str, username: str, password: str) -> None:
        if not username or not password:
            raise FeasibilityError("no_credentials", "username/password env vars are empty on the VM")
        page.goto(login_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        state = self._wait_state(page, ("login_form", "terminal", "dashboard"), self.timeout_ms)
        if state == "login_form":
            self._submit_login(page, username, password)
        elif state not in ("terminal", "dashboard"):
            raise FeasibilityError("unknown_page", f"login form never rendered (state={state}, at {self._where(page)})")
        self._reach_terminal(page)

    def resume_session(self, page: Any, login_url: str, login_form_grace_ms: int = 5_000) -> str:
        """Open ``login_url`` with a saved session; fill nothing. Returns
        ``logged_in`` only on a POSITIVE terminal reading (pressing Open
        Terminal on the dashboard if that is where it lands), else the state
        it ended on. Feasibility stops raise."""
        page.goto(login_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        state = self._wait_state(page, ("terminal", "dashboard", "login_error"), self.timeout_ms)
        if state == "dashboard":
            term = self.open_terminal(page)
            state = self._wait_state(term, ("terminal",), self.timeout_ms)
        return "logged_in" if state == "terminal" else state

    def wait_ready(self, page: Any, timeout_ms: Optional[int] = None) -> bool:
        limit = self.timeout_ms if timeout_ms is None else timeout_ms
        deadline = time.monotonic() + limit / 1000.0
        tp = self._t(page)
        while True:
            snap = parse_account_metrics(self._page_text(tp, 5_000))
            if snap.balance is not None or snap.equity is not None:
                return True
            if time.monotonic() >= deadline:
                return False
            tp.wait_for_timeout(1_000)

    # ---- redacted dumps (read-only) ---------------------------------------
    def page_shape(self, page: Any, secrets: Sequence[str] = ()) -> List[str]:
        tp = self._t(page)
        try:
            shape = tp.evaluate(PAGE_SHAPE_JS) or {}
        except Exception as exc:
            shape = {"title": f"(page-shape probe failed: {type(exc).__name__})"}
        return render_page_shape(shape, self._page_text(tp, 5_000), secrets)

    def structure(self, page: Any, secrets: Sequence[str] = ()) -> List[str]:
        tp = self._t(page)
        frames, struct = [], {}
        for i, fr in enumerate(self._frames(tp)):
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
        return render_structure(struct, frames, self._page_text(tp), self._tables(tp), secrets)

    def start_response_capture(self, page: Any) -> List[Any]:
        """Instrument specs off the network are a DXtrade-measured path; this
        terminal's JSON is unmeasured, so nothing is captured (an empty list,
        which the login check reports as no fields — never a number)."""
        return []

    # ---- read path ----------------------------------------------------------
    def _show_tab(self, page: Any, names: Sequence[str]) -> bool:
        """Click a VIEW tab (``role=tab`` only, exact name, optional count
        suffix). Never a button, so no order control can be the target."""
        for name in names:
            try:
                loc = page.get_by_role("tab", name=re.compile(r"^\s*" + re.escape(name) + r"(?:\s*\(\d+\))?\s*$"))
                if loc.count() >= 1:
                    loc.first.click(timeout=5_000)
                    page.wait_for_timeout(1_000)
                    return True
            except Exception:
                continue
        return False

    def read_account(self, page: Any) -> AccountSnapshot:
        return parse_account_metrics(self._page_text(self._t(page)))

    def read_positions(self, page: Any) -> List[Position]:
        tp = self._t(page)
        self._show_tab(tp, ("Positions",))
        got = positions_from_tables(self._tables(tp))
        if got is None:
            raise LookupError("no positions table found (breakout_terminal layout unmeasured)")
        return got

    def read_orders(self, page: Any) -> List[WorkingOrder]:
        tp = self._t(page)
        self._show_tab(tp, ("Open Orders", "Orders"))
        got = orders_from_tables(self._tables(tp))
        if got is None:
            raise LookupError("no orders table found (breakout_terminal layout unmeasured)")
        return got

    def read_quote(self, page: Any, venue_symbol: str) -> Optional[Dict[str, float]]:
        """REFUSES: returns ``None`` (could not look). This terminal's
        watchlist / quote DOM is UNMEASURED, so no bid/ask is read from it;
        the executor's round-trip test then stops with "no bid/ask" by
        design, not via an exception. ``quote_from_tables`` is the parser
        to wire in once the probe has measured a quote table."""
        return None

    # ---- order controls (rules in the module docstring) ---------------------
    def read_one_click(self, page: Any) -> Dict[str, Any]:
        try:
            got = self._t(page).evaluate(ONE_CLICK_JS) or {}
        except Exception as exc:
            return {"state": "unknown", "why": f"probe failed ({type(exc).__name__})"}
        if got.get("state") not in ("on", "off"):
            got["state"] = "unknown"
        return got

    def _find_form(self, page: Any) -> Dict[str, Any]:
        try:
            return self._t(page).evaluate(ORDER_FORM_JS, [FORM_FIELD_PATTERNS, FORM_BUTTON_PATTERNS]) or {}
        except Exception as exc:
            return {"found": False, "error": type(exc).__name__}

    def open_order_ticket(self, page: Any, venue_symbol: str) -> Dict[str, Any]:
        tp = self._t(page)
        oc = self.read_one_click(page)
        out: Dict[str, Any] = {"opened": False, "via": None, "one_click": oc, "form": {}}
        if oc.get("state") != "off":
            out["refused"] = f"one-click trading is {oc.get('state')} ({oc.get('why')}); nothing touched (rule 8)"
            return out
        form = self._find_form(page)
        if form.get("found"):
            out.update(opened=True, via="already_open", form=form)
            return out
        for name in TICKET_OPENER_NAMES:
            try:
                loc = tp.get_by_role("button", name=name, exact=True)
                if loc.count() == 1:
                    loc.first.click(timeout=5_000)
                    tp.wait_for_timeout(1_000)
                    form = self._find_form(page)
                    if form.get("found"):
                        out.update(opened=True, via=f"button:{name}", form=form)
                        return out
            except Exception:
                continue
        out["form"] = form
        out["refused"] = "no opener produced an order form (TICKET_OPENER_NAMES; unmeasured layout)"
        return out

    def close_order_ticket(self, page: Any) -> bool:
        tp = self._t(page)
        try:
            loc = tp.locator("[data-metis-btn=close]")
            if loc.count() == 1:
                loc.first.click(timeout=5_000)
                return True
        except Exception:
            pass
        try:
            tp.keyboard.press("Escape")
            return True
        except Exception:
            return False

    def probe_order_ticket(self, page: Any, venue_symbol: str) -> Dict[str, Any]:
        """READ-ONLY: open the form (only with one-click OFF), record its
        shape, classify it, close it. Types nothing; no side/type/submit."""
        opened = self.open_order_ticket(page, venue_symbol)
        form = opened.get("form") or {}
        surface = classify_ticket_surface(form) if opened.get("opened") else (
            "canvas_ticket" if (form.get("canvases_in_form") or 0) > 0 else "not_opened")
        result = {
            "surface": surface, "one_click": opened.get("one_click"), "via": opened.get("via"),
            "refused": opened.get("refused"),
            "fields": {k: {"label": v.get("label"), "disabled": v.get("disabled")}
                       for k, v in (form.get("fields") or {}).items()},
            "buttons": form.get("buttons") or {}, "checkboxes": form.get("checkboxes") or [],
            "ambiguous": form.get("ambiguous") or [],
            "canvases_in_form": form.get("canvases_in_form"),
            "canvases_in_page": form.get("canvases_in_page"),
            "inputs_in_page": form.get("inputs_in_page"), "form_text": form.get("form_text", ""),
        }
        if opened.get("opened"):
            result["closed"] = self.close_order_ticket(page)
        return result

    def place_bracket(self, page: Any, spec: BracketSpec, *, arm: bool = False) -> PlaceAttempt:
        """One order with SL AND TP attached at entry; ``arm=False`` stops at
        ``form_verified``. ``submitted`` means only that submit was clicked."""
        bad = check_bracket_spec(spec)
        if bad:
            return PlaceAttempt(stage="refused", detail="; ".join(bad))
        tp = self._t(page)
        opened = self.open_order_ticket(page, spec.venue_symbol)
        if not opened.get("opened"):
            return PlaceAttempt(stage="refused", detail=opened.get("refused") or "form not opened",
                                form={"one_click": opened.get("one_click")})
        form = opened["form"]

        def refuse(why: str, f: Optional[Mapping[str, Any]] = None) -> PlaceAttempt:
            self.close_order_ticket(page)
            return PlaceAttempt(stage="refused", detail=why, form=dict(f or form))

        why = check_form_shape(form, spec)
        if why:
            return refuse(why)
        want = {"quantity": spec.quantity, "stop_loss": spec.stop_loss, "take_profit": spec.take_profit}
        if spec.order_type == "limit":
            want["price"] = float(spec.limit_price)
        try:
            tp.click(f"[data-metis-btn={'side_buy' if spec.side == 'long' else 'side_sell'}]", timeout=5_000)
            type_btn = "type_limit" if spec.order_type == "limit" else "type_market"
            if type_btn in (form.get("buttons") or {}):
                tp.click(f"[data-metis-btn={type_btn}]", timeout=5_000)
            form = self._find_form(page)
            # Choosing a side must not have consumed the form (rule 7).
            if check_form_shape(form, spec):
                return refuse("form changed after choosing side/type: " + str(check_form_shape(form, spec)), form)
            for leg in ("stop_loss", "take_profit"):
                fld = (form.get("fields") or {}).get(leg) or {}
                if fld.get("disabled"):
                    tog = [c for c in form.get("checkboxes") or [] if c.get("field") == leg]
                    if len(tog) != 1 or tog[0].get("checked"):
                        return refuse(f"{leg} field is disabled and has no single enabling toggle")
                    tp.click(f"[data-metis-field={leg}_toggle]", timeout=5_000)
            form = self._find_form(page)
            for k, v in want.items():
                tp.fill(f"[data-metis-field={k}]", _fmt_num(v), timeout=5_000)
            form = self._find_form(page)
        except Exception as exc:
            return refuse(f"typing into the form failed ({type(exc).__name__})")
        mism = verify_form_values(form.get("fields") or {}, want)
        if mism:
            return refuse("form read-back mismatch: " + "; ".join(mism), form)
        if not arm:
            self.close_order_ticket(page)
            return PlaceAttempt(stage="form_verified", detail="disarmed: stopped before submit", form=form)
        oc = self.read_one_click(page)
        if oc.get("state") != "off":
            return refuse(f"one-click trading changed to {oc.get('state')} before submit")
        try:
            tp.click("[data-metis-btn=submit]", timeout=5_000)
        except Exception as exc:
            return PlaceAttempt(stage="submitted", submitted=True,
                                detail=f"submit click raised {type(exc).__name__}; outcome unknown", form=form)
        self._confirm_dialog(tp)
        return PlaceAttempt(stage="submitted", submitted=True, detail="submit clicked", form=form)

    @staticmethod
    def _confirm_dialog(tp: Any) -> bool:
        try:
            tp.wait_for_timeout(1_000)
            loc = tp.locator("[role=dialog], [role=alertdialog]").get_by_role(
                "button", name=re.compile(r"^(confirm|ok|yes|place order)$", re.IGNORECASE))
            if loc.count() == 1:
                loc.first.click(timeout=5_000)
                return True
        except Exception:
            pass
        return False

    def _row_action(self, page: Any, kind: str, key_col: str, key: str,
                    action_re: str, arm: bool) -> Dict[str, Any]:
        tp = self._t(page)
        try:
            got = tp.evaluate(ROW_ACTION_JS, [kind, key_col, key, action_re]) or {}
        except Exception as exc:
            return {"ok": False, "clicked": False, "why": f"probe failed ({type(exc).__name__})"}
        if got.get("rows") != 1 or got.get("controls") != 1:
            return {"ok": False, "clicked": False,
                    "why": f"need exactly 1 row and 1 control (rows={got.get('rows')}, controls={got.get('controls')})"}
        if not arm:
            return {"ok": True, "clicked": False, "why": "disarmed: stopped before the click"}
        try:
            tp.click("[data-metis-row-action]", timeout=5_000)
        except Exception as exc:
            return {"ok": True, "clicked": True, "why": f"click raised {type(exc).__name__}; outcome unknown"}
        self._confirm_dialog(tp)
        return {"ok": True, "clicked": True, "why": "clicked"}

    def cancel_order(self, page: Any, order: WorkingOrder, *, arm: bool = False) -> Dict[str, Any]:
        if not order or not order.order_id:
            return {"ok": False, "clicked": False, "why": "no order_id: refusing to guess which row"}
        self._show_tab(self._t(page), ("Open Orders", "Orders"))
        return self._row_action(page, "orders", "Order ID", str(order.order_id),
                                r"^(cancel|cancel order|×|✕|x)$", arm)

    def flatten(self, page: Any, symbol: Optional[str] = None, *, arm: bool = False) -> Dict[str, Any]:
        """Close ONE symbol's position (never close-all). Matches the row by
        the symbol cell under the first symbol-like header the table uses."""
        if not symbol:
            return {"ok": False, "clicked": False, "why": "symbol required (no close-all)"}
        tp = self._t(page)
        self._show_tab(tp, ("Positions",))
        key_col = next((c for c in SYMBOL_COLS
                        if any(_find_col(t.get("headers") or [], (c,)) is not None
                               for t in self._tables(tp) if _is_positions_table(t.get("headers") or []))), None)
        if key_col is None:
            return {"ok": False, "clicked": False, "why": "no positions table found"}
        return self._row_action(page, "positions", key_col, symbol,
                                r"^(close|close position|market close|×|✕|x)$", arm)

    def modify_bracket(self, page: Any, position: Position,
                       stop_loss: Optional[float], take_profit: Optional[float],
                       *, arm: bool = False) -> Dict[str, Any]:
        """Not built: the SL/TP edit flow of this terminal is unmeasured, and
        a guessed edit control on a live position is the wrong risk to take.
        The executor treats this as "could not amend" (alert, no click)."""
        return {"ok": False, "clicked": False,
                "why": "modify_bracket not built for breakout_terminal (edit flow unmeasured)"}
