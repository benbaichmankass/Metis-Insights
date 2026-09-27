"""Prop-terminal platform adapters — slice 1 (read-only login check).

Spec: docs/research/prop-automation-options-2026-09-27.md § 2 and § 4.

What these tests prove, and what they do NOT:

- The login selectors resolve against the REAL DXtrade login markup Breakout
  serves (saved from https://wss.breakoutprop.com/ on 2026-09-27).
- The page-state classifier, number parser and table/label parsers behave on
  a fake post-login DOM.
- Platform selection is config-driven and fails loudly on an unknown value.
- Nothing in slice 1 can place, modify, cancel or flatten an order, and the
  check uses no anti-detection tooling.

They do NOT prove the post-login read path works: nobody has logged in from
code yet, so the post-login fixtures below are INVENTED to exercise the
parsers. The `breakout-login-check` system-action run on the live VM is the
only real proof.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from src.prop.platform import (
    FeasibilityError,
    adapter_for_platform,
    get_adapter,
    load_platform_config,
)
from src.prop.platform.breakout_terminal import BreakoutTerminalAdapter
from src.prop.platform.dxtrade import (
    SELECTORS,
    DXtradeAdapter,
    classify_login_state,
    orders_from_tables,
    parse_account_metrics,
    parse_number,
    positions_from_tables,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "prop_dxtrade" / "login_page.html.txt"


# ── a minimal matcher for the simple selectors we use ─────────────────────


class _Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list = []
        self.elements: list = []  # (tag, attrs, ancestors)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.elements.append((tag, a, list(self.stack)))
        if tag not in ("input", "br", "img", "meta", "link"):
            self.stack.append((tag, a))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _simple_match(sel: str, tag: str, attrs: dict) -> bool:
    m = re.fullmatch(r"([a-z]*)((?:[#.][\w-]+)*)(?:\[([\w-]+)=([\w-]+)\])?", sel)
    assert m, f"test matcher cannot parse {sel!r}"
    t, rest, ak, av = m.groups()
    if t and t != tag:
        return False
    for kind, name in re.findall(r"([#.])([\w-]+)", rest):
        if kind == "#" and attrs.get("id") != name:
            return False
        if kind == "." and name not in (attrs.get("class") or "").split():
            return False
    if ak and attrs.get(ak) != av:
        return False
    return True


def _matches(html: str, selector: str) -> int:
    parts = selector.split()
    p = _Collect()
    p.feed(html)
    n = 0
    for tag, attrs, anc in p.elements:
        if not _simple_match(parts[-1], tag, attrs):
            continue
        if len(parts) == 1 or any(_simple_match(parts[0], t, a) for t, a in anc):
            n += 1
    return n


@pytest.fixture(scope="module")
def login_html() -> str:
    return FIXTURE.read_text()


@pytest.mark.parametrize("key", [
    "login_form", "login_username", "login_password", "login_submit", "login_error",
    "twofa_form", "twofa_code", "twofa_backup_form", "password_expired_form",
])
def test_measured_login_selectors_resolve_exactly_once(login_html, key):
    assert _matches(login_html, SELECTORS[key]) == 1, SELECTORS[key]


def test_matcher_can_find_a_negative(login_html):
    # A probe that can only say "found" proves nothing; show it says "absent".
    assert _matches(login_html, "form.loginForm-main input#doesNotExist") == 0
    assert _matches(login_html, "form#totpSecure input#username") == 0


# ── page-state classification ──────────────────────────────────────────


def test_challenge_beats_everything():
    assert classify_login_state({"login_form": True}, "Just a moment...", "") == "challenge"
    assert classify_login_state({}, "", "Attention Required! | Cloudflare") == "challenge"


@pytest.mark.parametrize("visible,expected", [
    ({"captcha_frame": True, "login_form": True}, "captcha"),
    ({"twofa_form": True}, "2fa"),
    ({"twofa_backup_form": True}, "2fa"),
    ({"password_expired_form": True}, "password_expired"),
    ({"login_error": True, "login_form": True}, "login_error"),
    ({"login_form": True}, "login_form"),
    ({}, "logged_in"),
])
def test_classify_login_state(visible, expected):
    assert classify_login_state(visible, "Balance 4,724.00", "Breakout Terminal") == expected


class _FakeLocator:
    def __init__(self, visible: bool) -> None:
        self._v = visible

    def count(self):
        return 1 if self._v else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._v


class _FakeLoginPage:
    """Enough of a Playwright Page to drive DXtradeAdapter.login."""

    def __init__(self, after_submit: dict, text: str = "") -> None:
        self.visible = {"login_form": True}
        self.after = after_submit
        self.text = text
        self.filled: dict = {}
        self.clicked: list = []

    def goto(self, *a, **k):
        pass

    def wait_for_selector(self, *a, **k):
        pass

    def wait_for_timeout(self, *a):
        pass

    def title(self):
        return "Breakout Terminal"

    def inner_text(self, *a, **k):
        return self.text

    def locator(self, sel):
        key = next(k for k, v in SELECTORS.items() if v == sel)
        return _FakeLocator(self.visible.get(key, False))

    def fill(self, sel, value):
        self.filled[sel] = value

    def click(self, sel, **k):
        self.clicked.append(sel)
        self.visible = dict(self.after)


@pytest.mark.parametrize("after,reason", [
    ({"twofa_form": True}, "2fa"),
    ({"login_error": True, "login_form": True}, "login_rejected"),
    ({"captcha_frame": True}, "captcha"),
    ({"password_expired_form": True}, "password_expired"),
])
def test_login_stops_with_a_feasibility_reason(after, reason):
    page = _FakeLoginPage(after)
    with pytest.raises(FeasibilityError) as ei:
        DXtradeAdapter(timeout_ms=2_000).login(page, "https://app.example/", "u", "p")
    assert ei.value.reason == reason
    assert page.clicked == [SELECTORS["login_submit"]]


def test_login_ok_clicks_only_the_login_button():
    page = _FakeLoginPage({})
    DXtradeAdapter(timeout_ms=2_000).login(page, "https://app.example/", "u", "p")
    assert page.clicked == [SELECTORS["login_submit"]]
    assert page.filled == {SELECTORS["login_username"]: "u", SELECTORS["login_password"]: "p"}


def test_login_refuses_without_credentials():
    with pytest.raises(FeasibilityError) as ei:
        DXtradeAdapter().login(_FakeLoginPage({}), "https://app.example/", "", "p")
    assert ei.value.reason == "no_credentials"


# ── parsers (INVENTED post-login fixtures; the live run is the real proof) ─


@pytest.mark.parametrize("raw,val", [
    ("$4,724.50", 4724.5), ("4,724.50 USD", 4724.5), ("(12.30)", -12.3),
    ("\u221212.30", -12.3), ("-$5", -5.0), ("+3.2", 3.2), ("0.00", 0.0),
    ("abc", None), ("1.2.3", None), ("", None), (None, None),
])
def test_parse_number(raw, val):
    assert parse_number(raw) == val


def test_parse_account_metrics_same_line_and_next_line():
    text = "Account 823528\nBalance\n4,724.00 USD\nEquity: 4,731.20\nOpen P&L 7.20\nAvailable Funds 4,500.00\n"
    snap = parse_account_metrics(text)
    assert snap.balance == 4724.0
    assert snap.equity == 4731.2
    assert snap.unrealized == 7.2
    assert snap.available == 4500.0
    assert snap.currency == "USD"
    # Unread fields are None and NAMED — never zero.
    assert snap.realized_today is None and "realized_today" in snap.unparsed


def test_parse_account_metrics_nothing_readable_is_all_unparsed():
    snap = parse_account_metrics("Log In\nUsername\nPassword\n")
    assert snap.balance is None and snap.equity is None
    assert {"balance", "equity"} <= set(snap.unparsed)


POS_TABLE = {"headers": ["Symbol", "Side", "Quantity", "Open Price", "Stop Loss", "Take Profit", "P&L"],
             "rows": [["ETHUSD", "Buy", "0.5", "2,950.00", "2,900.00", "3,050.00", "12.40"],
                      ["SOLUSD", "Sell", "-3", "150.10", "", "", "(4.20)"]]}
ORD_TABLE = {"headers": ["Symbol", "Side", "Type", "Quantity", "Price"],
             "rows": [["ETHUSD", "Sell", "Stop", "0.5", "2,900.00"]]}


def test_positions_from_tables():
    pos = positions_from_tables([ORD_TABLE, POS_TABLE])
    assert [(p.symbol, p.side, p.quantity) for p in pos] == [("ETHUSD", "long", 0.5), ("SOLUSD", "short", 3.0)]
    assert pos[0].stop_loss == 2900.0 and pos[0].take_profit == 3050.0
    assert pos[1].stop_loss is None and pos[1].unrealized_pnl == -4.2


def test_orders_from_tables_ignores_the_positions_table():
    orders = orders_from_tables([POS_TABLE, ORD_TABLE])
    assert len(orders) == 1
    o = orders[0]
    assert (o.symbol, o.side, o.order_type, o.quantity, o.price) == ("ETHUSD", "short", "Stop", 0.5, 2900.0)


def test_could_not_look_is_distinct_from_none_open():
    empty_pos = {"headers": POS_TABLE["headers"], "rows": []}
    assert positions_from_tables([empty_pos]) == []          # looked, none open
    assert positions_from_tables([{"headers": ["Foo"], "rows": []}]) is None  # could not look
    assert orders_from_tables([]) is None


# ── platform selection ─────────────────────────────────────────────────


def test_breakout_1_is_dxtrade_from_config():
    cfg = load_platform_config("breakout_1")
    assert cfg["platform"] == "dxtrade"
    assert cfg["login_url"] == "https://app.breakoutprop.com/"
    assert (cfg["username_env"], cfg["password_env"]) == ("BREAKOUT_DX_USERNAME", "BREAKOUT_DX_PASSWORD")
    assert isinstance(get_adapter("breakout_1"), DXtradeAdapter)


def test_unknown_platform_and_account_raise(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("accounts:\n  x:\n    platform: mt5\n")
    with pytest.raises(ValueError):
        load_platform_config("x", p)
    with pytest.raises(KeyError):
        load_platform_config("nope", p)
    with pytest.raises(ValueError):
        adapter_for_platform("mt5")


def test_breakout_terminal_is_scoped_only():
    a = adapter_for_platform("breakout_terminal")
    assert isinstance(a, BreakoutTerminalAdapter)
    for call in (lambda: a.login(None, "", "u", "p"), lambda: a.read_account(None),
                 lambda: a.read_positions(None), lambda: a.read_orders(None)):
        with pytest.raises(NotImplementedError):
            call()


# ── slice-1 boundary: no order controls, no anti-detection ─────────────


@pytest.mark.parametrize("platform", ["dxtrade", "breakout_terminal"])
def test_order_controls_are_not_implemented(platform):
    a = adapter_for_platform(platform)
    for call in (lambda: a.place_bracket(None, {}), lambda: a.modify_bracket(None, None, 1.0, 2.0),
                 lambda: a.cancel_order(None, None), lambda: a.flatten(None)):
        with pytest.raises(NotImplementedError):
            call()


SLICE1_FILES = [
    REPO / "src" / "prop" / "platform" / "dxtrade.py",
    REPO / "src" / "prop" / "platform" / "base.py",
    REPO / "scripts" / "prop" / "breakout_login_check.py",
    REPO / "scripts" / "ops" / "breakout_login_check_action.sh",
]


@pytest.mark.parametrize("path", SLICE1_FILES, ids=lambda p: p.name)
def test_no_anti_detection_tooling(path):
    src = path.read_text().lower()
    for banned in ("playwright_stealth", "playwright-stealth", "undetected_", "puppeteer-extra",
                   "user_agent=", "--disable-blink-features", "navigator.webdriver", "add_init_script"):
        assert banned not in src, (path.name, banned)


def test_login_check_never_calls_an_order_method():
    src = (REPO / "scripts" / "prop" / "breakout_login_check.py").read_text()
    for name in ("place_bracket", "modify_bracket", "cancel_order", "flatten("):
        assert name not in src, name
