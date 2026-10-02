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

import json
import re
import threading
from typing import Any, List
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
    CapturedResponse,
    DXtradeAdapter,
    classify_login_state,
    extract_instrument_specs_from_responses,
    orders_from_tables,
    parse_account_metrics,
    parse_number,
    positions_from_tables,
    redact_text,
    render_page_shape,
    render_structure,
    submit_not_visible_why,
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


def test_no_login_form_without_a_terminal_marker_is_unknown_not_logged_in():
    # Run 36329607152 printed "login: ok" and parsed nothing: the classifier
    # used to call ANY page without a login form "logged_in".
    assert classify_login_state({}, "", "") == "unknown"
    assert classify_login_state({}, "Welcome to Breakout\nDashboard", "Breakout") == "unknown"
    assert classify_login_state({}, "Balance\n4,724.00\nEquity\n4,724.00", "") == "logged_in"
    assert classify_login_state({}, "Account metrics", "") == "logged_in"


def test_login_that_lands_on_an_unknown_page_is_a_feasibility_stop():
    page = _FakeLoginPage({}, text="Some other page")
    with pytest.raises(FeasibilityError) as ei:
        DXtradeAdapter(timeout_ms=2_000).login(page, "https://app.example/", "u", "p")
    assert ei.value.reason == "unknown_page"


def test_login_ok_clicks_only_the_login_button():
    page = _FakeLoginPage({}, text="Account metrics\nBalance 4,724.00")
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
    ("USD 4,724.00", 4724.0), ("4\u00a0724.00", 4724.0), ("\u2014", None),
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


def test_parse_account_metrics_on_the_dictionary_labels():
    # Labels are the MEASURED strings of the served i18n dictionary
    # (metric.name.short.*); the one-label-per-line layout is invented.
    text = ("Account metrics\nBalance\n4,724.00\nEquity\n4,731.20\nP&L\n7.20\n"
            "Day RPL\n-12.30\nUsed Margin\n150.00\nFree Margin\n4,581.20\nMargin Level, %\n3,154.13\n")
    snap = parse_account_metrics(text)
    assert (snap.balance, snap.equity, snap.unrealized, snap.realized_today,
            snap.margin_used, snap.available) == (4724.0, 4731.2, 7.2, -12.3, 150.0, 4581.2)
    assert snap.unparsed == []


def test_parse_account_metrics_on_concatenated_inline_text():
    # innerText joins adjacent inline spans with no separator.
    snap = parse_account_metrics("Balance4,724.00Equity4,731.20\nFree Margin 4,581.20 USD\nBalance free of holdings")
    assert (snap.balance, snap.equity, snap.available) == (4724.0, 4731.2, 4581.2)


# ── instrument specs via network response sniffing (W6-PROP-E1, re-scoped
# 2026-09-28: three rounds of UI-panel scraping never converged, so this
# reads captured network responses instead — see the dxtrade.py module
# note). INVENTED response bodies — NOT MEASURED against this terminal. ──


def test_extract_instrument_specs_exact_match_yields_allowlisted_fields():
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/instruments",
        body=json.dumps({"symbol": "ETHUSD", "contractSize": 1.0, "digits": 2,
                         "minQty": 0.01, "qtyStep": 0.01, "displayName": "Ethereum"}))
    result = extract_instrument_specs_from_responses([resp], ["ETHUSD"])
    assert result["specs"]["ETHUSD"] == {"contractSize": 1.0, "digits": 2,
                                         "minQty": 0.01, "qtyStep": 0.01}
    assert result["discovery"] == []


def test_extract_instrument_specs_substring_never_satisfies_exact_match():
    # BTCUSDT must never satisfy a request for BTCUSD.
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/instruments",
        body=json.dumps({"symbol": "BTCUSDT", "contractSize": 1.0, "digits": 2}))
    result = extract_instrument_specs_from_responses([resp], ["BTCUSD"])
    assert result["specs"]["BTCUSD"] == {}
    assert len(result["discovery"]) == 1


def test_extract_instrument_specs_slash_named_symbol_matches_its_unslashed_request():
    # TRADEIFY-WIRE T4: tradeify_1's instrument objects name "ETH/USD".
    resp = CapturedResponse(
        url="https://dx.tradeify247.co/api/instruments",
        body=json.dumps([{"symbol": "ETH/USD", "lotSize": 1.0, "minVolume": 0.01, "volumeStep": 0.01,
                          "pricePrecision": 2},
                         {"symbol": "BTC/USDT", "lotSize": 9.0}]))
    result = extract_instrument_specs_from_responses([resp], ["ETHUSD", "BTCUSD"])
    assert result["specs"]["ETHUSD"] == {"lotSize": 1.0, "minVolume": 0.01, "volumeStep": 0.01,
                                         "pricePrecision": 2}
    assert result["specs"]["BTCUSD"] == {}            # BTC/USDT is still not BTCUSD


def test_extract_instrument_specs_conflicting_duplicates_report_conflict():
    resps = [
        CapturedResponse(url="https://wss.breakoutprop.com/a",
                         body=json.dumps({"symbol": "ETHUSD", "contractSize": 1.0})),
        CapturedResponse(url="https://wss.breakoutprop.com/b",
                         body=json.dumps({"symbol": "ETHUSD", "contractSize": 2.0})),
        # A third, later object matching the FIRST value must not clear it.
        CapturedResponse(url="https://wss.breakoutprop.com/c",
                         body=json.dumps({"symbol": "ETHUSD", "contractSize": 1.0})),
    ]
    result = extract_instrument_specs_from_responses(resps, ["ETHUSD"])
    assert result["specs"]["ETHUSD"] == {"contractSize": "conflict"}


def test_extract_instrument_specs_never_returns_a_sensitive_key_even_when_matching():
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/instruments",
        body=json.dumps({"symbol": "ETHUSD", "contractSize": 1.0, "accountId": 12345,
                         "sessionToken": 99999, "userId": 7}))
    result = extract_instrument_specs_from_responses([resp], ["ETHUSD"])
    assert result["specs"]["ETHUSD"] == {"contractSize": 1.0}


def test_extract_instrument_specs_never_leaks_a_non_matching_objects_values():
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/watchlist",
        body=json.dumps({"symbol": "BTCUSD", "contractSize": 999.0, "secretNote": "leak-me"}))
    result = extract_instrument_specs_from_responses([resp], ["ETHUSD"])
    assert result["specs"]["ETHUSD"] == {}
    assert result["specs"].get("BTCUSD") is None  # not even requested
    blob = json.dumps(result)
    assert "999.0" not in blob and "leak-me" not in blob


def test_extract_instrument_specs_never_attributes_a_position_or_orders_own_size():
    # Round-4 finding: the old SUBSTRING allowlist ("tick" matching
    # ticketNumber, "lot" matching lots/closedLots/pilotSlot) let a
    # position/order object's own size or id fields be printed as if they
    # were instrument specs. An object carrying a position/order-shaped
    # key is never read as a spec at all, however spec-shaped its other
    # fields look.
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/positions",
        body=json.dumps({"instrument": "BTCUSD", "positionId": "p-1",
                         "lots": 2.5, "ticketNumber": 998877, "openPrice": 65000.0}))
    result = extract_instrument_specs_from_responses([resp], ["BTCUSD"])
    assert result["specs"]["BTCUSD"] == {}


def test_extract_instrument_specs_anchored_allowlist_yields_a_real_specs_fields():
    # The positive control for the previous test: a genuine spec object
    # (no position/order-shaped key) still yields its allowlisted fields.
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/instruments",
        body=json.dumps({"symbol": "BTCUSD", "lotSize": 1.0, "tickSize": 0.5,
                         "digits": 2, "minQty": 0.001}))
    result = extract_instrument_specs_from_responses([resp], ["BTCUSD"])
    assert result["specs"]["BTCUSD"] == {"lotSize": 1.0, "tickSize": 0.5,
                                         "digits": 2, "minQty": 0.001}


def test_extract_instrument_specs_ambiguous_symbol_keys_are_skipped():
    # The same object's symbol-like keys naming TWO DIFFERENT requested
    # symbols is ambiguous, not a match either way.
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/instruments",
        body=json.dumps({"symbol": "BTCUSD", "name": "ADAUSD", "contractSize": 1.0}))
    result = extract_instrument_specs_from_responses([resp], ["BTCUSD", "ADAUSD"])
    assert result["specs"]["BTCUSD"] == {} and result["specs"]["ADAUSD"] == {}
    # "Ambiguous" is treated as "not a match either way" -- it still
    # contributes a (redacted, key-names-only) discovery line, same as any
    # other non-matching object.
    assert len(result["discovery"]) == 1


def test_extract_instrument_specs_drops_non_finite_values_never_a_false_conflict():
    resps = [
        CapturedResponse(url="https://wss.breakoutprop.com/a",
                         body=json.dumps({"symbol": "ETHUSD", "contractSize": float("nan")})),
        CapturedResponse(url="https://wss.breakoutprop.com/b",
                         body=json.dumps({"symbol": "ETHUSD", "contractSize": float("nan")})),
    ]
    result = extract_instrument_specs_from_responses(resps, ["ETHUSD"])
    assert result["specs"]["ETHUSD"] == {}  # never "conflict", never NaN itself


def test_extract_instrument_specs_discovery_prints_key_names_only_never_values():
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/ping",
        body=json.dumps({"serverTime": 1234567890, "secretNote": "leak-me", "status": "ok"}))
    result = extract_instrument_specs_from_responses([resp], ["ETHUSD"])
    assert len(result["discovery"]) == 1
    line = result["discovery"][0]
    assert "leak-me" not in line and "1234567890" not in line
    # "secretNote" is itself dropped (sensitive-looking key name), never
    # just its value -- key names are filtered too, not only values.
    assert "serverTime" in line and "status" in line and "secretNote" not in line
    assert "(+1 keys suppressed)" in line


def test_extract_instrument_specs_discovery_drops_id_shaped_and_digit_run_keys():
    # Round-4 finding: a raw account number or similar spelled as a "key"
    # survived the old rule because it didn't end in "id" and wasn't
    # otherwise sensitive-shaped.
    resp = CapturedResponse(
        url="https://wss.breakoutprop.com/api/ping",
        body=json.dumps({"12345678": "x", "acct-1210012345": "y", "status": "ok"}))
    result = extract_instrument_specs_from_responses([resp], ["ETHUSD"])
    line = result["discovery"][0]
    assert "12345678" not in line and "1210012345" not in line
    assert "status" in line


def test_extract_instrument_specs_discovery_and_capture_are_capped():
    resps = [CapturedResponse(url=f"https://wss.breakoutprop.com/r{i}",
                              body=json.dumps({"n": i})) for i in range(50)]
    result = extract_instrument_specs_from_responses(resps, ["ETHUSD"], max_discovery=5)
    assert len(result["discovery"]) == 5


def test_extract_instrument_specs_leak_test_credentials_email_and_query_url():
    # Plant the username, password, an e-mail and a URL with a query
    # string in BOTH the response body (a non-matching object, so it
    # only ever reaches the discovery path) and the response URL itself.
    user, pw = "BO-JDOE77", "Pa55-word!x"
    leaky_url = f"https://wss.breakoutprop.com/api/session?u={user}&sid=abc123token"
    resp = CapturedResponse(
        url=leaky_url,
        body=json.dumps({"note": f"user={user} pw={pw} mail=victim@example.com",
                         "authToken": "should-never-print-anyway"}))
    result = extract_instrument_specs_from_responses([resp], ["ETHUSD"], secrets=(user, pw))
    assert result["specs"]["ETHUSD"] == {}
    assert len(result["discovery"]) == 1
    line = result["discovery"][0]
    low = line.lower()
    assert user.lower() not in low and pw.lower() not in low
    assert "abc123token" not in line and "victim@example.com" not in line
    # The body's values never reach discovery at all (key names only).
    assert "should-never-print-anyway" not in line


class _FakeResponse:
    def __init__(self, url, body, content_type="application/json", fail=False):
        self.url = url
        self.headers = {"content-type": content_type}
        self._body = body
        self._fail = fail

    def text(self):
        if self._fail:
            raise RuntimeError("body unavailable (fake)")
        return self._body


class _FakePage:
    def __init__(self, url="https://wss.breakoutprop.com/"):
        self.url = url
        self._handlers = []

    def on(self, event, handler):
        self._handlers.append((event, handler))

    def fire(self, response):
        for event, handler in self._handlers:
            if event == "response":
                handler(response)


def test_start_response_capture_records_same_origin_json_and_ignores_the_rest():
    page = _FakePage()
    captured = DXtradeAdapter().start_response_capture(page)
    page.fire(_FakeResponse("https://wss.breakoutprop.com/api/instruments", '{"symbol": "ETHUSD"}'))
    page.fire(_FakeResponse("https://other.example.com/tracker", '{"x": 1}'))  # cross-origin
    page.fire(_FakeResponse("https://wss.breakoutprop.com/broken", "", fail=True))  # unreadable body
    page.fire(_FakeResponse("https://wss.breakoutprop.com/img.png", "binary",
                            content_type="image/png"))  # non-JSON content-type
    assert [c.url for c in captured] == ["https://wss.breakoutprop.com/api/instruments"]
    assert captured[0].body == '{"symbol": "ETHUSD"}'


def test_start_response_capture_fails_closed_on_an_unconfirmed_page_origin():
    for bad_url in ("", "about:blank", "ABOUT:BLANK"):
        page = _FakePage(url=bad_url)
        captured = DXtradeAdapter().start_response_capture(page)
        page.fire(_FakeResponse("https://wss.breakoutprop.com/api/instruments", '{"symbol": "ETHUSD"}'))
        assert captured == [], bad_url

    class _RaisingUrlPage(_FakePage):
        @property
        def url(self):
            raise RuntimeError("url unavailable (fake)")

        @url.setter
        def url(self, value):
            pass

    page = _RaisingUrlPage()
    captured = DXtradeAdapter().start_response_capture(page)
    page.fire(_FakeResponse("https://wss.breakoutprop.com/api/instruments", '{"symbol": "ETHUSD"}'))
    assert captured == []


def test_start_response_capture_caps_a_bodys_size():
    page = _FakePage()
    captured = DXtradeAdapter().start_response_capture(page)
    huge = json.dumps({"symbol": "ETHUSD", "pad": "x" * 300_000})
    page.fire(_FakeResponse("https://wss.breakoutprop.com/api/instruments", huge))
    assert len(captured[0].body) <= 200_000


# Column headers as the served dictionary spells them.
MULTIASSET_POS = {"headers": ["Symbol", "Side", "Position Qty", "Avg Fill Price", "Open P/L, acc", "Type"],
                  "rows": [["MNQ", "Sell", "2", "20,100.25", "-15.00", "Future"]]}
MULTIASSET_ORD = {"headers": ["Symbol", "Side", "Order Type", "Quantity", "Limit Price", "Stop Price",
                              "Avg Price", "Status", "Order ID"],
                  "rows": [["MNQ", "Buy", "Stop", "2", "", "20,200.00", "", "Working", "123"]]}


def test_dictionary_spelled_tables_are_told_apart():
    pos = positions_from_tables([MULTIASSET_ORD, MULTIASSET_POS])
    assert [(p.symbol, p.side, p.quantity, p.entry_price, p.unrealized_pnl) for p in pos] == [
        ("MNQ", "short", 2.0, 20100.25, -15.0)]
    orders = orders_from_tables([MULTIASSET_POS, MULTIASSET_ORD])
    assert [(o.symbol, o.side, o.order_type, o.quantity) for o in orders] == [("MNQ", "long", "Stop", 2.0)]


def test_redact_text_strips_credentials_emails_and_tokens_but_keeps_balances():
    out = redact_text("user bob@example.com pw hunter2 tok eyJhbGciOiJIUzI1NiJ9abcdefgh bal 4,724.00",
                      "hunter2")
    assert "bob@example.com" not in out and "hunter2" not in out and "eyJhbGci" not in out
    assert "4,724.00" in out


def test_redact_text_matches_secrets_case_insensitively():
    # Pre-merge review of #13248: the terminal may render the login in a
    # different case than the env var holds.
    out = redact_text("Account: BO-JDOE77 | bo-jdoe77 | Bo-JDoe77", "bo-jdoe77")
    assert "jdoe77" not in out.lower() and out.count("<redacted>") == 3


def test_redact_text_keeps_url_origin_only():
    # A short session id in a path or query is below the 24-char token rule.
    out = redact_text("at https://app.example.com/s/ab12CD?sid=q1#f and wss://x.example/t9")
    assert "ab12CD" not in out and "sid" not in out and "t9" not in out
    assert "https://app.example.com/<path>" in out and "wss://x.example/<path>" in out


def test_redact_text_strips_a_scheme_less_query_string():
    # _URL_RE only matches scheme://host -- a bare "host.tld/path?sid=..."
    # (no scheme in front) reached the public log unredacted until this
    # was added, independent of URL/host detection.
    out = redact_text("see host.example.com/p?sid=q1abcd&other=2 for details")
    assert "sid=q1abcd" not in out and "other=2" not in out
    assert "host.example.com/p?<query>" in out
    # Normal prose without a "?key=value" shape survives untouched.
    prose = redact_text("Contract Size 1.00, Digits 2, is that right?")
    assert prose == "Contract Size 1.00, Digits 2, is that right?"


def test_render_structure_is_redacted_and_carries_the_layout():
    struct = {"title": "Breakout Terminal", "location": "https://app.example/t?session=abc#x",
              "counts": {"tables": 0}, "roles": {"tab": 3},
              "hits": [{"label": "Balance", "chain": ["span.metric-name", "div.metric"],
                        "parent_text": "Balance 4,724.00", "grandparent_text": "me@example.com",
                        "next_sibling_text": "4,724.00", "row_cells": ["Balance", "4,724.00"]}]}
    lines = render_structure(struct, [{"url": "https://app.example/f?t=1", "text_len": 10}],
                             "Balance\n4,724.00\nlogin me@example.com\npw s3cr3t", [], secrets=("s3cr3t",))
    blob = "\n".join(lines)
    assert "session=abc" not in blob and "t=1" not in blob
    assert "me@example.com" not in blob and "s3cr3t" not in blob
    assert "span.metric-name < div.metric" in blob and "4,724.00" in blob
    assert lines[0].startswith("structure: BEGIN") and lines[-1] == "structure: END"


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
    # The DXtrade terminal's HTTP host, where the login selectors were measured;
    # app.breakoutprop.com is the dashboard (run 36337076971: unknown_page).
    assert cfg["login_url"] == "https://wss.breakoutprop.com/"
    assert (cfg["username_env"], cfg["password_env"]) == ("BREAKOUT_DX_USERNAME", "BREAKOUT_DX_PASSWORD")
    assert isinstance(get_adapter("breakout_1"), DXtradeAdapter)


def test_login_url_must_be_https(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("accounts:\n  x:\n    platform: dxtrade\n    login_url: http://wss.example/\n")
    with pytest.raises(ValueError):
        load_platform_config("x", p)
    p.write_text("accounts:\n  x:\n    platform: dxtrade\n    login_url: ''\n")
    with pytest.raises(ValueError):
        load_platform_config("x", p)


def test_render_page_shape_is_redacted():
    shape = {"title": "Sign in", "location": "https://app.example.com/login?next=/x",
             "forms": [{"id": "f", "cls": "auth", "action": "/api/login", "visible": True}],
             "inputs": [{"id": "email", "name": "email", "type": "email"}],
             "buttons": ["Log in", "BO-JDOE77"], "iframes": ["https://challenges.example/t/abc?x=1"]}
    lines = render_page_shape(shape, "Welcome bo-jdoe77\nme@example.com", secrets=("bo-jdoe77",))
    blob = "\n".join(lines)
    assert "jdoe77" not in blob.lower() and "me@example.com" not in blob and "next=" not in blob
    assert "page_shape.form: id='f' class='auth' action='/api/login' visible=True" in blob
    assert "page_shape.iframe: https://challenges.example" in blob
    assert lines[0].startswith("page_shape: BEGIN") and lines[-1] == "page_shape: END"


def test_unknown_platform_and_account_raise(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("accounts:\n  x:\n    platform: mt5\n    login_url: https://x.example/\n")
    with pytest.raises(ValueError):
        load_platform_config("x", p)
    with pytest.raises(KeyError):
        load_platform_config("nope", p)
    with pytest.raises(ValueError):
        adapter_for_platform("mt5")


def test_breakout_terminal_is_selectable():
    # Built by PROP-TERM (2026-09-28); its behaviour is tested in
    # tests/test_prop_platform_breakout_terminal.py.
    assert isinstance(adapter_for_platform("breakout_terminal"), BreakoutTerminalAdapter)


# ── order-control boundary, no anti-detection ──────────────────────────
# Step 3 (PROP-EXEC 2026-09-28) built the order controls on the dxtrade
# adapter only; their behaviour is tested in tests/test_prop_executor.py.


@pytest.mark.parametrize("platform", ["dxtrade", "breakout_terminal"])
def test_order_controls_default_to_disarmed(platform):
    import inspect
    a = adapter_for_platform(platform)
    for name in ("place_bracket", "modify_bracket", "cancel_order", "flatten"):
        assert inspect.signature(getattr(a, name)).parameters["arm"].default is False, name


SLICE1_FILES = [
    REPO / "src" / "prop" / "platform" / "dxtrade.py",
    REPO / "src" / "prop" / "platform" / "base.py",
    REPO / "scripts" / "prop" / "breakout_login_check.py",
    REPO / "scripts" / "ops" / "breakout_login_check_action.sh",
    REPO / "src" / "prop" / "prop_executor.py",
    REPO / "scripts" / "prop" / "prop_executor_tick.py",
    REPO / "scripts" / "ops" / "prop_executor_tick.sh",
    REPO / "src" / "prop" / "platform" / "breakout_terminal.py",
    REPO / "scripts" / "prop" / "breakout_terminal_probe.py",
    REPO / "scripts" / "ops" / "breakout_terminal_probe_action.sh",
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


# ── the in-page JS, run in a real Chromium against an INVENTED div layout ──

DIVGRID = REPO / "tests" / "fixtures" / "prop_dxtrade" / "terminal_divgrid.html.txt"


@pytest.fixture(scope="module")
def chromium_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    import glob
    # Playwright's own build first, then any pre-installed headless shell.
    candidates = [None] + sorted(glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"))
    with sync_api.sync_playwright() as pw:
        browser, errors = None, []
        for exe in candidates:
            try:
                browser = pw.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
                break
            except Exception as exc:  # no usable browser build here
                errors.append(str(exc).splitlines()[0][:120])
        if browser is None:
            pytest.skip(f"chromium unavailable: {errors}")
        page = browser.new_page()
        page.set_content(DIVGRID.read_text())
        yield page
        browser.close()


def test_extract_tables_js_reads_a_div_grid(chromium_page):
    from src.prop.platform.dxtrade import EXTRACT_TABLES_JS
    tables = chromium_page.evaluate(EXTRACT_TABLES_JS)
    pos = positions_from_tables(tables)
    assert [(p.symbol, p.side, p.quantity, p.entry_price, p.stop_loss, p.take_profit, p.unrealized_pnl)
            for p in pos] == [("ETHUSD", "long", 0.5, 2950.0, 2900.0, 3050.0, 12.4)]
    assert orders_from_tables(tables) == []  # looked; none working


def test_adapter_reads_account_and_tables_from_a_real_dom(chromium_page):
    a = DXtradeAdapter(timeout_ms=5_000)
    snap = a.read_account(chromium_page)
    assert (snap.balance, snap.equity, snap.unrealized, snap.realized_today,
            snap.margin_used, snap.available) == (4724.0, 4731.2, 7.2, -12.3, 150.0, 4581.2)
    assert a.wait_ready(chromium_page, timeout_ms=0) is True


def test_structure_dump_from_a_real_dom_never_leaks_input_values(chromium_page):
    lines = DXtradeAdapter(timeout_ms=5_000).structure(chromium_page, ("hunter2-secret",))
    blob = "\n".join(lines)
    assert "hunter2-secret" not in blob          # input value is never read
    assert "someone@example.com" not in blob     # e-mail redacted
    assert "structure.label: 'Balance'" in blob and "4,724.00" in blob
    assert "div.grid-header" in blob             # header-row shape is reported


def test_page_shape_on_the_measured_login_page_never_reads_input_values(chromium_page):
    # The REAL login markup Breakout serves, with values typed into both fields.
    chromium_page.set_content(FIXTURE.read_text())
    chromium_page.fill(SELECTORS["login_username"], "bo-jdoe77")
    chromium_page.fill(SELECTORS["login_password"], "Pa55-word!x")
    blob = "\n".join(DXtradeAdapter(timeout_ms=5_000).page_shape(chromium_page))
    assert "bo-jdoe77" not in blob and "Pa55-word!x" not in blob
    assert "class='loginForm loginForm-main'" in blob and "action='api/auth/login'" in blob
    assert "page_shape.input: id='password'" in blob
    chromium_page.set_content(DIVGRID.read_text())  # leave the shared page as the other tests expect


# ── redact BEFORE truncating (pre-merge review of #13297) ─────────────────
LONG_USER = "bo-jdoe77longusername"


def test_page_shape_never_leaks_a_secret_prefix_split_by_a_cut(chromium_page):
    # The reviewer's exact case: the JS used to cut button text at 40 chars
    # BEFORE redaction, printing ['Welcome back, you are signed in as bo-jd'].
    title = "T" * 110 + " " + LONG_USER            # straddles the old 120-char title cut
    chromium_page.set_content(
        f"<html><head><title>{title}</title></head><body>"
        f"<a href='#'>Welcome back, you are signed in as {LONG_USER}</a></body></html>")
    blob = "\n".join(DXtradeAdapter(timeout_ms=5_000).page_shape(chromium_page, (LONG_USER,)))
    assert "bo-jd" not in blob.lower(), blob
    assert "Welcome back, you are signed in as" in blob
    chromium_page.set_content(DIVGRID.read_text())


def test_structure_never_leaks_a_secret_prefix_split_by_a_cut(chromium_page):
    # parent_text reads "Balance4,724.00 <pad> <user>": the user starts at
    # index 17 + 98 = 115, inside the old 120-char parent_text cut.
    pad = "x" * 98
    chromium_page.set_content(
        f"<html><body><div><span>Balance</span><span>4,724.00 {pad} {LONG_USER}</span></div></body></html>")
    blob = "\n".join(DXtradeAdapter(timeout_ms=5_000).structure(chromium_page, (LONG_USER,)))
    assert "bo-jd" not in blob.lower(), blob
    assert "structure.label: 'Balance'" in blob
    chromium_page.set_content(DIVGRID.read_text())


@pytest.mark.parametrize("pad", [0, 20, 37, 115, 155])
def test_renderers_cap_after_redaction(pad):
    # A guard on the Python side (the old renderers already redacted first;
    # the defect was in the JS). Keeps a future cap from moving before r().
    text = "y" * pad + LONG_USER
    shape = {"title": text, "buttons": [text], "forms": [], "inputs": []}
    struct = {"hits": [{"label": "Balance", "chain": [text], "parent_text": text,
                        "grandparent_text": text, "next_sibling_text": text, "row_cells": [text]}]}
    blob = "\n".join(render_page_shape(shape, text, secrets=(LONG_USER,))
                     + render_structure(struct, [], text, [{"kind": "divgrid", "headers": [text],
                                                           "rows": [[text]]}], secrets=(LONG_USER,)))
    assert "bo-jd" not in blob.lower()


# ── bottom-panel VIEW tabs as Breakout's DXtrade renders them ─────────────
# MEASURED (run 36351578802, issue #13345): tabs are a span inside a
# [data-active] element, not role=tab; the positions table headers are the
# ones below. The orders table's headers are MEASURED too (run 36358563148,
# issue #13385); only the one order row in it is invented.
BOTTOM_PANEL = """<html><body>
<script>window.__clicks = [];</script>
<button onclick="window.__clicks.push('Place Order')">Place Order</button>
<div class="tabs">
  <div data-active="true" onclick="window.__clicks.push('Positions');show('pos')"><span>Positions</span></div>
  <div data-active="false" onclick="window.__clicks.push('Orders');show('ord')"><span>Orders</span></div>
  <div data-active="false" onclick="window.__clicks.push('Order History')"><span>Order History</span></div>
</div>
<table id="pos"><thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Open P&amp;L</th><th>Take profit</th>
<th>Stop loss</th><th>Position ID</th><th>Fill Price</th><th>Current Price</th><th>Date and Time</th></tr></thead>
<tbody></tbody></table>
<script>
function show(which) {
  document.getElementById('pos').style.display = which === 'pos' ? '' : 'none';
  if (which === 'ord' && !document.getElementById('ord')) {
    document.body.insertAdjacentHTML('beforeend',
      '<table id="ord"><thead><tr><th>Sts</th><th>Status</th><th>Symbol</th><th>Side</th><th>Size</th>' +
      '<th>Price</th><th>Type</th><th>Stop loss</th><th>Take profit</th><th>Current Price</th>' +
      '<th>Date and Time Modified</th><th>Expiration</th><th>Order ID</th><th>Fill Price</th></tr></thead>' +
      '<tbody><tr><td></td><td>Working</td><td>ETHUSD</td><td>Sell</td><td>0.5</td><td>2,600.00</td>' +
      '<td>Stop</td><td></td><td></td><td>2,682.48</td><td></td><td>GTC</td><td>77</td><td></td></tr>' +
      '</tbody></table>');
  }
}
</script></body></html>"""


def test_view_tabs_click_only_the_exact_data_active_tab(chromium_page):
    chromium_page.set_content(BOTTOM_PANEL)
    a = DXtradeAdapter(timeout_ms=5_000)
    assert a.read_positions(chromium_page) == []          # measured headers; looked, none open
    orders = a.read_orders(chromium_page)
    assert [(o.symbol, o.side, o.order_type, o.quantity, o.price) for o in orders] == [
        ("ETHUSD", "short", "Stop", 0.5, 2600.0)]
    # Only VIEW tabs were clicked -- never the button, never "Order History".
    assert chromium_page.evaluate("window.__clicks") == ["Positions", "Orders"]
    chromium_page.set_content(DIVGRID.read_text())


# Both tables with the MEASURED headers (runs 36351578802 and 36358563148).
MEASURED_POS = {"headers": ["Symbol", "Side", "Size", "Open P&L", "Take profit", "Stop loss", "Position ID",
                            "Fill Price", "Current Price", "Date and Time", "", ""], "rows": []}
MEASURED_ORD = {"headers": ["Sts", "Status", "Symbol", "Side", "Size", "Price", "Type", "Stop loss",
                            "Take profit", "Current Price", "Date and Time Modified", "Expiration",
                            "Order ID", "Fill Price", "", ""], "rows": []}


def test_measured_breakout_tables_are_told_apart():
    # Run 36358563148 showed the orders table and still read "UNPARSED": its
    # "Current Price" column was on the positions-only list.
    assert positions_from_tables([MEASURED_ORD, MEASURED_POS]) == []
    assert orders_from_tables([MEASURED_POS, MEASURED_ORD]) == []
    assert orders_from_tables([MEASURED_POS]) is None       # not shown: could not look
    assert positions_from_tables([MEASURED_ORD]) is None


# ── resume_session (W6-PROP-FEED session reuse, 2026-09-28) ──────────────


def test_resume_session_accepts_only_a_positive_terminal_marker_and_never_types():
    page = _FakeLoginPage({}, text="Account metrics\nBalance 4,724.00")
    page.visible = {}
    assert DXtradeAdapter(timeout_ms=3_000).resume_session(page, "https://app.example/") == "logged_in"
    assert page.filled == {} and page.clicked == []


def test_resume_session_believes_a_login_form_only_after_the_grace():
    page = _FakeLoginPage({}, text="")
    waits = []
    page.wait_for_timeout = lambda ms: waits.append(ms)
    got = DXtradeAdapter(timeout_ms=60_000).resume_session(page, "https://app.example/",
                                                           login_form_grace_ms=3_000)
    assert got == "login_form"
    assert sum(waits) >= 3_000
    assert page.filled == {} and page.clicked == []


def test_resume_session_unknown_page_times_out_as_unknown():
    page = _FakeLoginPage({}, text="Some other page")
    page.visible = {}
    assert DXtradeAdapter(timeout_ms=2_000).resume_session(page, "https://app.example/") == "unknown"


@pytest.mark.parametrize("visible,reason", [
    ({"captcha_frame": True}, "captcha"),
    ({"twofa_form": True}, "2fa"),
    ({"password_expired_form": True}, "password_expired"),
])
def test_resume_session_raises_on_a_challenge_and_never_types(visible, reason):
    page = _FakeLoginPage({}, text="")
    page.visible = visible
    with pytest.raises(FeasibilityError) as ei:
        DXtradeAdapter(timeout_ms=2_000).resume_session(page, "https://app.example/")
    assert ei.value.reason == reason
    assert page.filled == {} and page.clicked == []


def test_modify_bracket_disarmed_never_clicks_the_edit_control():
    from src.prop.platform.base import Position
    calls = []

    class P:
        def evaluate(self, js, *a):
            return {"state": "off"} if "one" in js[:200].lower() else {"rows": 1, "controls": 1}

        def click(self, *a, **k):
            calls.append(a)

        def wait_for_timeout(self, *a):
            pass

    a = DXtradeAdapter()
    a._show_tab = lambda *x: True
    a.read_one_click = lambda page: {"state": "off"}
    a._locate_edit_control = lambda *x: {"ok": True, "row": {}, "controls": [], "chosen": 1}
    r = a.modify_bracket(P(), Position(symbol="SOLUSD"), 1.0, 2.0)
    assert r["clicked"] is False and calls == []
    assert r["one_click"] == {"state": "off"}


def test_modify_bracket_records_an_unknown_one_click_and_does_not_gate_on_it():
    # One-click trading is a diagnostic, not a gate (operator 2026-09-28): the
    # live terminal reads "unknown" and the modify path must still proceed.
    from src.prop.platform.base import Position
    calls = []

    class P:
        def evaluate(self, js, *a):
            return {"rows": 1, "controls": 1}

        def click(self, *a, **k):
            calls.append(a)

        def wait_for_timeout(self, *a):
            pass

    a = DXtradeAdapter()
    a._show_tab = lambda *x: True
    a.read_one_click = lambda page: {"state": "unknown", "why": "label not found"}
    a._locate_edit_control = lambda *x: {"ok": True, "row": {}, "controls": [], "chosen": 1}
    r = a.modify_bracket(P(), Position(symbol="SOLUSD"), 1.0, 2.0)
    assert r["ok"] is True and r["clicked"] is False and calls == []
    assert r["one_click"]["state"] == "unknown" and "one-click" not in r["why"]


def test_modify_bracket_armed_refuses_before_any_click_while_the_dialog_is_unmeasured():
    # An armed walk could fill and SUBMIT the docked sidebar order ticket
    # (PROP-TRAIL go-live review): until the edit dialog is measured, arm=True
    # must not click anything at all.
    from src.prop.platform.base import Position
    calls = []

    class P:
        def evaluate(self, js, *a):
            return {"rows": 1, "controls": 1}

        def click(self, *a, **k):
            calls.append(a)

        def fill(self, *a, **k):
            calls.append(a)

        def wait_for_timeout(self, *a):
            pass

    a = DXtradeAdapter()
    a._show_tab = lambda *x: True
    a.read_one_click = lambda page: {"state": "off"}
    located = []
    a._locate_edit_control = lambda *x: located.append(x) or {"ok": True, "row": {}, "controls": [], "chosen": 1}
    a._open_edit_dialog = lambda *x: calls.append(("open", x)) or {"ok": False}
    r = a.modify_bracket(P(), Position(symbol="SOLUSD"), 1.0, 2.0, arm=True)
    assert r["ok"] is False and r["clicked"] is False and calls == []
    assert located                                             # located, never clicked
    assert "unmeasured" in r["why"]


# ── instrument-details probe (PROP-ETH, 2026-09-29) — real Chromium ───────
# INVENTED layout (no run has measured a real Instrument Details panel; that
# is the whole reason this probe stops at a structure DUMP rather than
# parsing named fields — see the docstring on probe_instrument_details).

# A measured-shape watchlist panel (header table naming Symbol/Bid/Ask, one
# ``tr.instrument`` row -- the same selectors WATCHLIST_ROWS_JS/open_order_ticket
# use) that FIND_INSTRUMENT_SEARCH_JS anchors on, wrapped in the watchlist
# WIDGET (``widget__container... widgetNew__container``) whose header holds
# the ``placeholder="Symbol..."`` search input -- the shape MEASURED on the
# live terminal 2026-09-30 (issue #14551). No order ticket, as on landing.
INSTRUMENT_SEARCH_PAGE = """<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_1" type="text">
</div>
</div>
<div id="details" style="display:none">
  <div>Symbol</div><div id="details-symbol"></div>
  <div>Lot Size</div><div id="details-lot">1</div>
  <div>Tick Size</div><div id="details-tick">0.01</div>
  <div>Min Order</div><div id="details-min">10</div>
  <div>Account Ref</div><div id="details-ref">1234567</div>
</div>
<script>
document.getElementById('watchlist-search').addEventListener('input', function (e) {
  var v = (e.target.value || '').toUpperCase();
  var d = document.getElementById('details');
  if (v === 'ADAUSD') {
    document.getElementById('details-symbol').textContent = 'ADAUSD';
    d.style.display = '';
  } else {
    d.style.display = 'none';
  }
});
</script>
</body></html>"""


def test_probe_instrument_details_searches_dumps_and_resets(chromium_page):
    chromium_page.set_content(INSTRUMENT_SEARCH_PAGE)
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "ADAUSD")
    assert res["searched"] is True
    assert res["readback_matches"] is True
    assert res["symbol_echoed"] is True          # the panel now names ADAUSD
    rows = res["dump"]["rows"]
    texts = [r.get("text") for r in rows if r.get("text")]
    assert any(t == "1" for t in texts)           # short numbers survive the mask
    assert any(t == "0.01" for t in texts)
    assert any(t == "10" for t in texts)
    assert any(t == "#######" for t in texts)      # the 7-digit run is masked
    assert not any(t and "1234567" in t for t in texts)   # never the raw run
    # Reset: the field is empty and the details panel is hidden again.
    assert res["reset"] is True
    assert chromium_page.input_value("#watchlist-search") == ""
    chromium_page.set_content(DIVGRID.read_text())


def test_probe_instrument_details_never_touches_the_order_ticket_symbol_input(chromium_page):
    # A page carrying BOTH a watchlist search AND an order-ticket sidebar
    # (BUY/SELL + its own symbol_input). The probe must find the search box
    # and must NEVER type into, or even tag, the ticket's symbol_input.
    chromium_page.set_content("""<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_2" type="text">
</div>
</div>
<div class="ticket">
  <input data-test-id="symbol_input" value="">
  <button data-test-id="BUY">Buy</button>
  <button data-test-id="SELL">Sell</button>
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "ETHUSD")
    assert res["searched"] is True
    assert res["via"] == "placeholder+tid"
    assert chromium_page.input_value("[data-test-id=symbol_input]") == ""
    assert chromium_page.evaluate(
        "document.querySelector('[data-test-id=symbol_input]').hasAttribute('data-metis-search-hit')") is False
    chromium_page.set_content(DIVGRID.read_text())


def test_probe_instrument_details_reports_not_found_rather_than_guessing(chromium_page):
    # No watchlist table/rows on the page at all: refuse honestly, touch nothing.
    chromium_page.set_content("<html><body><div>Positions</div></body></html>")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False
    assert res["found"] is False
    assert "Symbol/Bid/Ask" in res.get("why", "")
    chromium_page.set_content(DIVGRID.read_text())


def test_probe_instrument_details_refuses_an_ambiguous_search_field(chromium_page):
    # Two inputs both carrying the measured placeholder AND test-id: never guess which one.
    chromium_page.set_content("""<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <input id="s1" placeholder="Symbol..." data-test-id="watchlist_public_search_3">
  <input id="s2" placeholder="Symbol..." data-test-id="watchlist_public_search_4">
</div>
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False
    assert res["found"] is False
    assert "need exactly 1" in res.get("why", "")     # refused for ambiguity, not a missing anchor
    assert chromium_page.input_value("#s1") == "" and chromium_page.input_value("#s2") == ""
    chromium_page.set_content(DIVGRID.read_text())


def test_instrument_details_dump_masks_only_digit_runs_of_five_or_more():
    from src.prop.platform.dxtrade import INSTRUMENT_DETAILS_DUMP_JS  # noqa: F401 (imported for existence)
    # Direct regex-shape check on the mask, mirrored from the JS (both sides
    # tested so a future JS edit that drifts the threshold is caught by the
    # chromium test above, not just this one).
    import re as _re

    def mask(v):
        return _re.sub(r"\d{5,}", lambda m: "#" * len(m.group()), v)

    assert mask("0.01") == "0.01"
    assert mask("425") == "425"
    assert mask("118.02") == "118.02"
    assert mask("1234567") == "#######"
    assert mask("order 987654 filled") == "order ###### filled"


# ── independent-review fixes on the instrument probe (2026-09-29) ─────────
# 1. (superseded below, 2026-09-29 second pass) FIND_INSTRUMENT_SEARCH_JS
#    originally refused whenever the BUY-button count wasn't exactly 1. Live
#    run #14437 showed this refuses EVERY real call: the terminal's landing
#    state has 0 BUY buttons (no order ticket open), which is normal, not an
#    error. The anchor is now POSITIVE (the measured watchlist panel) rather
#    than negative (excluding an undecidable order panel) -- see the tests
#    under "watchlist-anchored locator" below, which replace this one.
# 2. An ambiguous match on one candidate must refuse outright, never fall
#    through to a later candidate that happens to match a single input.
# 3. probe_instrument_details's finally block must only fill/reset when the
#    tag-count check actually passed (searched=True), never on a refusal.
# 4. INSTRUMENT_DETAILS_DUMP_JS's text-leaf loop must apply the same
#    personal-shaped exclusion the control loop already applies.
# 5. data-metis-search-hit must be cleared after every probe call so a stale
#    tag from one symbol never blocks the next symbol's probe.


def test_find_instrument_search_refuses_on_the_first_ambiguous_candidate_rather_than_falling_through(chromium_page):
    # Two inputs both match the FIRST candidate ("symbol..."); a third input
    # uniquely matches a LATER candidate ("watchlist_public"). The ambiguity on
    # the first candidate must refuse outright -- never fall through to the
    # later, unambiguous candidate and guess that one instead.
    chromium_page.set_content("""<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <input id="s1" placeholder="Symbol..." data-test-id="watchlist_public_search_5" type="text">
  <input id="s2" placeholder="Symbol..." data-test-id="watchlist_public_search_6" type="text">
  <input id="s3" data-test-id="watchlist_public_s3" type="text">
</div>
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False and res["found"] is False
    assert "2 inputs have placeholder 'symbol...'" in res.get("why", "")
    assert chromium_page.input_value("#s1") == ""
    assert chromium_page.input_value("#s2") == ""
    assert chromium_page.input_value("#s3") == ""
    assert chromium_page.evaluate(
        "document.querySelector('#s3').hasAttribute('data-metis-search-hit')") is False
    chromium_page.set_content(DIVGRID.read_text())


# ── watchlist-anchored locator (fix for live run #14437, 2026-09-29) ──────
# Manager-relayed follow-up after #14134 merged (823e475) and the manager
# dispatched the probe live (issue #14437, code_sha 4ef6b3340): all four
# candidate symbols refused with "0 BUY buttons (need exactly 1 to locate
# the order panel)" -- correct refuse-not-guess behaviour, but it means the
# BUY-count precondition made the probe refuse on the terminal's NORMAL
# landing state (no order ticket open), so it could never actually run.
# FIND_INSTRUMENT_SEARCH_JS now anchors POSITIVELY on the MEASURED watchlist
# panel (WATCHLIST_ROWS_JS's own header/row selectors, dry run #13898/run
# 36507086110) instead of negatively excluding an order panel that may not
# exist. These four tests are the regression coverage for that redesign;
# the BUY/SELL exclusion above is retained as defense in depth, not as the
# precondition -- see test #4 below.


def test_find_instrument_search_succeeds_with_zero_buy_buttons_when_the_watchlist_is_present(chromium_page):
    # 1. THE regression itself: the terminal's real landing state (no order
    # ticket, so 0 BUY buttons) must not block the probe when a measured
    # watchlist panel is present -- this is exactly what #14437 measured live.
    chromium_page.set_content("""<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_7" type="text">
</div>
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is True
    assert res["via"] == "placeholder+tid"
    chromium_page.set_content(DIVGRID.read_text())


def test_find_instrument_search_refuses_when_no_watchlist_table_is_measured(chromium_page):
    # 2. A candidate-shaped input exists, but there is no measured watchlist
    # table/rows anywhere on the page -- refuse rather than fall back to
    # admitting every input (the earlier BUY-count rule's own mistake, just
    # inverted: the fix must not become "admit everything when unsure").
    chromium_page.set_content("""<html><body>
<input id="only-search" placeholder="Symbol..." data-test-id="watchlist_public_search_8" type="text">
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False and res["found"] is False
    assert "Symbol/Bid/Ask" in res.get("why", "")
    assert chromium_page.input_value("#only-search") == ""
    assert chromium_page.evaluate(
        "document.querySelector('#only-search').hasAttribute('data-metis-search-hit')") is False
    chromium_page.set_content(DIVGRID.read_text())


def test_find_instrument_search_never_admits_an_input_outside_the_watchlist_panel(chromium_page):
    # 3. A candidate-matching input exists on the page but OUTSIDE the
    # watchlist panel entirely (e.g. an unrelated page-level search box).
    # Containment must actually restrict, not just prove a matching string
    # exists somewhere on the page.
    chromium_page.set_content("""<html><body>
<input id="global-search" placeholder="Symbol..." data-test-id="watchlist_public_search_9" type="text">
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
</div>
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False and res["found"] is False
    assert res.get("n_candidate_inputs") == 0      # anchors resolved; the outside input is not eligible
    assert chromium_page.input_value("#global-search") == ""
    assert chromium_page.evaluate(
        "document.querySelector('#global-search').hasAttribute('data-metis-search-hit')") is False
    chromium_page.set_content(DIVGRID.read_text())


def test_find_instrument_search_excludes_inputs_inside_any_order_panel_defense_in_depth(chromium_page):
    # 4. Even with the watchlist anchor as the primary containment, an input
    # that also happens to sit inside a BUY/SELL-holding panel -- whatever
    # the panel COUNT, two here -- must still be excluded. This holds even
    # though the "exactly 1 BUY button" precondition the earlier version
    # needed for this exclusion to work at all is gone.
    chromium_page.set_content("""<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <div class="ticket-a">
    <input id="ticket-search-a" placeholder="Symbol..." data-test-id="watchlist_public_search_10" type="text">
    <button data-test-id="BUY">Buy</button>
    <button data-test-id="SELL">Sell</button>
  </div>
  <div class="ticket-b">
    <button data-test-id="BUY">Buy</button>
    <button data-test-id="SELL">Sell</button>
  </div>
</div>
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False and res["found"] is False
    assert "order" in res.get("why", "")
    assert chromium_page.input_value("#ticket-search-a") == ""
    assert chromium_page.evaluate(
        "document.querySelector('#ticket-search-a').hasAttribute('data-metis-search-hit')") is False
    chromium_page.set_content(DIVGRID.read_text())


# ── manager review of #14442, before merge: harden the anchor further ─────
# rows[0] (the first tr.instrument/[data-row-id] anywhere in the document)
# was not necessarily a row of the watchlist's own header table -- whether
# the Positions/Orders grids also use this row selector is UNMEASURED (the
# one recorded run with a live Positions row, issue #14198, run
# 36572236094, prints parsed cells and hover-control markup, never the
# <tr>'s own class/attributes). These two tests are the required regression
# coverage: the anchor row must ALIGN with the watchlist header (never just
# "whichever row is first"), and the resolved panel must never include a
# second Symbol-headed table or a positions/orders-shaped one.


def test_find_instrument_search_resolves_the_true_watchlist_panel_even_when_a_positions_row_precedes_it(chromium_page):
    # A positions-shaped row using tr[data-row-id] (a 4-cell row: Symbol/
    # Side/Size/Open P&L) precedes the watchlist's own row in document
    # order, and both share a wrapping ancestor -- the exact shape that
    # would anchor on the wrapper (far wider than the watchlist) if the
    # anchor row were simply "the first match in the document". The cell-
    # count-alignment rule must skip the mismatched positions row (4 cells
    # against the watchlist header's 3) and anchor on the watchlist's own
    # row instead, so the resolved panel stays NARROW: the positions
    # panel's own candidate-matching input must never become eligible.
    chromium_page.set_content("""<html><body>
<div class="app-shell">
  <div class="positions-panel">
    <table>
      <thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Open P&L</th></tr></thead>
      <tbody><tr data-row-id="pos-1"><td>SOLUSD</td><td>Buy</td><td>0.01</td><td>-0.01</td></tr></tbody>
    </table>
    <input id="wrong-search" placeholder="Symbol..." data-test-id="watchlist_public_search_11" type="text">
  </div>
  <div class="widget__container___Ab1 widgetNew__container">
  <div class="watchlist-panel">
    <table>
      <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
      <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
    </table>
    <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_12" type="text">
  </div>
  </div>
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    # If scoping had failed wide, BOTH inputs (identical matching text)
    # would be eligible and the candidate would be ambiguous -- refused,
    # not found. Succeeding, with exactly this one candidate, IS the proof
    # the panel resolved narrow.
    assert res["searched"] is True
    assert res["via"] == "placeholder+tid"
    assert chromium_page.input_value("#wrong-search") == ""
    chromium_page.set_content(DIVGRID.read_text())


def test_find_instrument_search_refuses_when_the_panel_also_holds_a_positions_or_orders_shaped_table(chromium_page):
    # Two Symbol-headed tables in the same resolved panel: refuse, never
    # treat either as unambiguously "the" watchlist.
    chromium_page.set_content("""<html><body>
<div class="mixed-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <table>
    <thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Status</th></tr></thead>
    <tbody><tr><td>SOLUSD</td><td>Buy</td><td>0.01</td><td>Open</td></tr></tbody>
  </table>
  <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_13" type="text">
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False and res["found"] is False
    assert "Symbol-headed tables" in res.get("why", "")
    assert chromium_page.input_value("#watchlist-search") == ""

    # A positions/orders-SHAPED table (no Symbol column of its own) sharing
    # the same panel as the watchlist -- refuse rather than admit an input
    # from a panel that also holds trading-position data.
    chromium_page.set_content("""<html><body>
<div class="mixed-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <table>
    <thead><tr><th>Side</th><th>Quantity</th><th>Status</th></tr></thead>
    <tbody><tr><td>Buy</td><td>0.01</td><td>Open</td></tr></tbody>
  </table>
  <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_14" type="text">
</div>
</body></html>""")
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False and res["found"] is False
    assert "positions/orders-shaped" in res.get("why", "")
    assert chromium_page.input_value("#watchlist-search") == ""
    chromium_page.set_content(DIVGRID.read_text())


def test_find_instrument_search_refuses_when_the_widget_also_holds_a_positions_shaped_table(chromium_page):
    # 2026-09-30 (PROP-ETH-DOM): containment widened from the grid panel to
    # the watchlist WIDGET, so the widget gets the panel's own checks -- a
    # positions/orders-shaped table anywhere in it refuses.
    chromium_page.set_content("""<html><body>
<div class="widget__container___Ab1 widgetNew__container">
  <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_15" type="text">
  <div class="watchlist-panel">
    <table>
      <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
      <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
    </table>
  </div>
  <div class="other"><table><thead><tr><th>Side</th><th>Quantity</th><th>Status</th></tr></thead>
    <tbody><tr><td>Buy</td><td>0.01</td><td>Open</td></tr></tbody></table></div>
</div>
</body></html>""")
    res = DXtradeAdapter().probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False and "widget also contains a positions/orders-shaped table" in res.get("why", "")
    assert chromium_page.input_value("#watchlist-search") == ""
    chromium_page.set_content(DIVGRID.read_text())


def test_probe_instrument_details_never_fills_when_the_tag_count_is_not_exactly_one(chromium_page):
    # A stale data-metis-search-hit tag (as findings 3/5 describe) makes the
    # post-search tag count 2. Must refuse -- and must never reach the
    # .fill("") reset call on an element nobody verified as the search field.
    chromium_page.set_content("""<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_16" type="text">
  <input id="stale" data-metis-search-hit="1" value="leftover">
</div>
</div>
</body></html>""")
    a = DXtradeAdapter()
    res = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert res["searched"] is False
    assert "2 tagged candidates" in res.get("why", "")
    # Neither element was ever filled/reset.
    assert chromium_page.input_value("#watchlist-search") == ""
    assert chromium_page.input_value("#stale") == "leftover"
    # The stale tag (and the freshly-set one) are both cleared regardless.
    assert chromium_page.evaluate("document.querySelectorAll('[data-metis-search-hit]').length") == 0
    chromium_page.set_content(DIVGRID.read_text())


def test_probe_instrument_details_clears_its_tag_so_a_later_probe_is_not_blocked_by_a_stale_one(chromium_page):
    chromium_page.set_content("""<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="watchlist-panel">
  <table>
    <thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
    <tbody><tr class="instrument"><td>ETHUSD</td><td>2950.00</td><td>2950.50</td></tr></tbody>
  </table>
  <input id="watchlist-search" placeholder="Symbol..." data-test-id="watchlist_public_search_17" type="text">
</div>
</div>
</body></html>""")
    a = DXtradeAdapter()
    first = a.probe_instrument_details(chromium_page, "ETHUSD")
    assert first["searched"] is True
    assert chromium_page.evaluate(
        "document.querySelector('#watchlist-search').hasAttribute('data-metis-search-hit')") is False
    second = a.probe_instrument_details(chromium_page, "BTCUSD")
    assert second["searched"] is True
    chromium_page.set_content(DIVGRID.read_text())


def test_instrument_details_dump_masks_personal_classed_text_leaves_too(chromium_page):
    # Comma/space-grouped digits (e.g. an account number rendered in groups)
    # never form a single run of 5+ digits, so the digit-run mask alone would
    # let them through; the personal-classed leaf must be dropped entirely.
    chromium_page.set_content("""<html><body>
<div class="account-number">4128 8812 9911 2234</div>
<div>Lot Size</div><div>1</div>
</body></html>""")
    a = DXtradeAdapter()
    dump = a.instrument_details_dump(chromium_page)
    blob = " ".join((r.get("text") or "") for r in dump.get("rows", []))
    assert "8812" not in blob and "9911" not in blob and "4128" not in blob
    assert any(r.get("text") == "1" for r in dump.get("rows", []))
    chromium_page.set_content(DIVGRID.read_text())


# ── DIALOG-MEASURE (2026-10-01): the position edit dialog ─────────────────
# INVENTED layout (no run has measured the real dialog yet: that is what
# edit-dialog-probe is for). It carries the hazard the go-live review of
# #15316 named: a docked sidebar order ticket with its own quantity input,
# Buy/Sell and submit, always on the page beside the dialog.

EDIT_DIALOG_HTML = """
<html><body>
<div id="sidebar"><input data-test-id="symbol_input" value="SOLUSD">
  <span>Lots</span><input id="sq" value="1"><span>Stop Loss:</span><input id="ssl" value="">
  <span>Take Profit:</span><input id="stp" value="">
  <button data-test-id="BUY" onclick="window.__log.push('sidebar-buy')">Buy</button>
  <button data-test-id="SELL" onclick="window.__log.push('sidebar-sell')">Sell</button>
  <button class="close" onclick="window.__log.push('sidebar-close')">Cancel</button></div>
<table><thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Open Price</th><th>Stop Loss</th><th>Take Profit</th><th></th></tr></thead>
<tbody><tr><td>ETHUSD</td><td>Buy</td><td>0.5</td><td>2950</td><td>2900</td><td>3050</td>
<td class="sticky--actions-cell"><button class="btn"><i class="icon-reverse"></i></button><button class="btn"
 onclick="window.__log.push('pencil'); document.getElementById('dlg').style.display='block'"><i class="icon-pencil"></i></button><button
 class="btn" onclick="window.__log.push('row-close')"><i class="icon-close"></i></button></td></tr></tbody></table>
<div id="dlg" role="dialog" style="display:none; position:absolute; left:400px; top:100px; width:300px; height:260px">
  <h3>Modify ETHUSD Buy Position</h3>
  <span>Quantity</span><input id="dq" value="0.5" readonly>
  <span>Stop Loss</span><input id="dsl" value="2900"><select><option selected>Price</option><option>Pips</option></select>
  <span>Take Profit</span><input id="dtp" value="3050">
  <button onclick="window.__log.push('dlg-save')">Save</button>
  <button onclick="window.__log.push('dlg-cancel'); document.getElementById('dlg').style.display='none'">Cancel</button>
</div></body></html>
"""


@pytest.fixture()
def edit_page(chromium_page):
    chromium_page.set_content(EDIT_DIALOG_HTML)
    chromium_page.evaluate("window.__log = []")
    yield chromium_page
    chromium_page.set_content(DIVGRID.read_text())     # leave the module page as the other tests expect


def _edit_adapter():
    a = DXtradeAdapter(timeout_ms=2_000)
    a._show_tab = lambda *x: True
    a.read_one_click = lambda page: {"state": "off"}
    return a


def test_edit_probe_dry_finds_the_pencil_by_icon_name_and_clicks_nothing(edit_page):
    got = _edit_adapter().probe_edit_dialog(edit_page, "ETHUSD", click=False)
    assert got["locate"]["ok"] is True and got["locate"]["chosen"] == 1     # reverse . PENCIL . close
    assert "before any click" in got["stopped"]
    assert edit_page.evaluate("window.__log") == []


def test_edit_probe_measures_the_dialog_and_presses_only_its_own_cancel(edit_page):
    got = _edit_adapter().probe_edit_dialog(edit_page, "ETHUSD", click=True)
    dlg = got["dialog"]
    assert dlg["ok"] and dlg["names_symbol"] and dlg["fields"]["quantity"]["readonly"] is True
    assert [m["value"] for m in dlg["modes"]] == ["Price"] and dlg["submit_in_box"] is True
    assert got["would_refuse"] == [] and got["cancel_pressed"] is True and got["closed_after_cancel"] is True
    assert edit_page.evaluate("window.__log") == ["pencil", "dlg-cancel"]


def test_edit_probe_with_no_position_row_stops_before_any_click(edit_page):
    got = _edit_adapter().probe_edit_dialog(edit_page, "SOLUSD", click=True)
    assert got["locate"]["ok"] is False and "clicked" not in got
    assert edit_page.evaluate("window.__log") == []


_GOOD_DIALOG = {"ok": True, "names_symbol": True, "fields": {
    "quantity": {"value": "0.5", "readonly": True}, "stop_loss": {"value": "1", "readonly": False},
    "take_profit": {"value": "2", "readonly": False}}, "ambiguous": [], "modes": [{"value": "Price"}],
    "submit": 1, "submit_in_box": True, "submit_enabled": True, "cancel": 1,
    "buy_sell_buttons": 0, "sidebar_ticket_inside": False}


def test_edit_dialog_mismatch_passes_only_the_positions_own_dialog():
    from src.prop.platform.dxtrade import edit_dialog_mismatch
    assert edit_dialog_mismatch(_GOOD_DIALOG, "ETHUSD", 0.5) == []
    cases = {
        "does not name": {"names_symbol": False},
        "order-ticket controls": {"buy_sell_buttons": 2},
        "editable": {"fields": {**_GOOD_DIALOG["fields"], "quantity": {"value": "0.5", "readonly": False}}},
        "!= the position's": {"fields": {**_GOOD_DIALOG["fields"], "quantity": {"value": "1", "readonly": True}}},
        "need Price": {"modes": [{"value": "Pips"}]},
        "mode not readable": {"modes": []},
        "not boxed": {"submit_in_box": False},
        "submit buttons": {"submit": 2},
        "cancel controls": {"cancel": 0},
    }
    for needle, patch in cases.items():
        got = edit_dialog_mismatch({**_GOOD_DIALOG, **patch}, "ETHUSD", 0.5)
        assert any(needle in g for g in got), (needle, got)
    assert edit_dialog_mismatch(_GOOD_DIALOG, "ETHUSD", None)                     # unknown size refuses
    assert edit_dialog_mismatch({"ok": False, "why": "0 new dialogs"}, "ETHUSD", 0.5) == ["0 new dialogs"]


def test_the_tick_resolves_the_edit_dialog_modes_and_defers_them_to_a_live_ticket():
    from types import SimpleNamespace
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    base = dict(probe_ticket="", dry_run=False, watched_click=False, round_trip="", close_position="")
    assert resolve_mode(SimpleNamespace(**base, edit_dialog_dry="ETHUSD"), {"PROP_EXECUTOR_MODE": "off"}) == "edit_dialog_dry"
    assert resolve_mode(SimpleNamespace(**base, edit_dialog_probe="ETHUSD"), {}) == "edit_dialog_probe"
    assert {"edit_dialog_dry", "edit_dialog_probe"} <= YIELD_MODES


# ── EMPTY watchlist (TRADEIFY-GOLIVE, #15426/#15431): tradeify_1's restored
# "Favourites" list reads 0 symbols, so there is no row to align; the header
# table anchors instead and every widget-level check still applies. ─────────
EMPTY_WATCHLIST_PAGE = """<html><body>
<div class="widget__container___Ab1 widgetNew__container">
<div class="widget__header"><input id="wl-search" placeholder="Symbol..." data-test-id="watchlist_public_search_9" type="text"></div>
<div class="watchlist-panel">
  <table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th><th>Change</th></tr></thead><tbody></tbody></table>
</div>
</div>
</body></html>"""


def test_find_instrument_search_anchors_an_empty_watchlist_on_its_header(chromium_page):
    chromium_page.set_content(EMPTY_WATCHLIST_PAGE)
    res = DXtradeAdapter().probe_instrument_details(chromium_page, "ETHUSD")
    assert res["searched"] is True
    assert res["via"] == "placeholder+tid (empty watchlist: header anchor)"
    assert res["reset"] is True and chromium_page.input_value("#wl-search") == ""
    chromium_page.set_content(DIVGRID.read_text())


def test_empty_watchlist_header_anchor_still_refuses_a_widget_with_a_positions_table(chromium_page):
    # The positions-shaped table sits in the WIDGET, outside the grid panel,
    # so the widget-level check is the one exercised.
    chromium_page.set_content(EMPTY_WATCHLIST_PAGE.replace(
        '</div>\n</div>\n</body>',
        '</div>\n<table><thead><tr><th>Status</th><th>Side</th><th>Quantity</th></tr></thead></table>'
        '\n</div>\n</body>'))
    res = DXtradeAdapter().probe_instrument_details(chromium_page, "ETHUSD")
    assert res["searched"] is False and res["found"] is False
    assert res.get("why") == "widget also contains a positions/orders-shaped table"
    assert chromium_page.input_value("#wl-search") == ""
    chromium_page.set_content(DIVGRID.read_text())


# ── SLASH-form query (TRADEIFY-GOLIVE, #15444): typing ``SOLUSD`` on
# tradeify_1 opened the result panel with NO rows; the terminal names symbols
# ``SOL/USD``. The account's ``search_query_style: slash`` types that form. ──
def test_search_query_for_slash_style_types_the_display_form():
    q = DXtradeAdapter.search_query_for
    assert q("SOLUSD", "slash") == "SOL/USD"
    assert q("ethusd", "slash") == "ETH/USD"
    assert q("XRPUSDT", "slash") == "XRP/USDT"
    assert q("SOL/USD", "slash") == "SOL/USD"      # already slashed: unchanged
    assert q("USD", "slash") == "USD"              # nothing to split
    assert q("MNQ", "slash") == "MNQ"              # no USD quote: unchanged


def test_search_query_for_without_a_style_types_the_venue_symbol_unchanged():
    # breakout_1 declares no style: its query is exactly what it typed before.
    q = DXtradeAdapter.search_query_for
    assert q("SOLUSD") == "SOLUSD"
    assert q("SOLUSD", None) == "SOLUSD"
    assert q("SOLUSD", "something-else") == "SOLUSD"


SLASH_RESULT_PAGE = EMPTY_WATCHLIST_PAGE.replace("</body>", """<div id="results"></div>
<script>
document.getElementById('wl-search').addEventListener('input', function (e) {
  var v = (e.target.value || '').toUpperCase();
  var r = document.getElementById('results');
  r.innerHTML = '';
  if (v === 'SOL/USD') {
    setTimeout(function () {   // async, like the venue's result panel
      r.innerHTML = '<div class="row"><span>SOL/USD</span><span>Solana</span>'
        + '<span>Cryptocurrencies</span></div>';
    }, 400);
  }
});
</script>
</body>""")


def test_probe_instrument_details_types_the_given_query_and_reads_it_back(chromium_page):
    chromium_page.set_content(SLASH_RESULT_PAGE)
    a = DXtradeAdapter()
    res = a.probe_instrument_details(
        chromium_page, "SOLUSD", query=a.search_query_for("SOLUSD", "slash"), settle_ms=1_500)
    assert res["searched"] is True
    assert res["query"] == "SOL/USD"
    assert res["readback_matches"] is True        # compared against what was TYPED
    assert res["reset"] is True and chromium_page.input_value("#wl-search") == ""
    chromium_page.set_content(DIVGRID.read_text())


def test_probe_instrument_details_default_query_is_the_venue_symbol(chromium_page):
    chromium_page.set_content(SLASH_RESULT_PAGE)
    res = DXtradeAdapter().probe_instrument_details(chromium_page, "SOLUSD")
    assert res["searched"] is True
    assert res["query"] == "SOLUSD"
    assert res["readback_matches"] is True
    chromium_page.set_content(DIVGRID.read_text())


def test_only_tradeify_1_declares_the_slash_query_style():
    # breakout_1 is LIVE and shares this adapter: its search must type the
    # venue symbol exactly as before, so it declares no style.
    from src.prop.platform import load_platform_config
    assert load_platform_config("tradeify_1").get("search_query_style") == "slash"
    assert load_platform_config("breakout_1").get("search_query_style") is None


# ── SUGGESTION rows (TRADEIFY-GOLIVE, #15457): the result panel's rows sit in
# a <tbody>, which INSTRUMENT_DETAILS_DUMP_JS skips, so the probe reads them
# with a scoped SUGGESTION_ROWS_JS anchored on the "Asset Class" header. ────
SUGGESTION_PAGE = EMPTY_WATCHLIST_PAGE.replace("</body>", """
<table id="positions"><thead><tr><th>Symbol</th><th>Side</th><th>Quantity</th></tr></thead>
<tbody><tr><td>BTC/USD</td><td>Buy</td><td>1</td></tr></tbody></table>
<div id="dd" style="display:none">
  <div><div>All</div><div>Cryptocurrencies</div><div>Stocks</div></div>
  <table><thead><tr><th><span>Symbol</span></th><th><span>Description</span></th><th><span>Asset Class</span></th></tr></thead>
  <tbody id="dd-rows"></tbody></table>
</div>
<script>
document.getElementById('wl-search').addEventListener('input', function (e) {
  var v = (e.target.value || '').toUpperCase();
  var dd = document.getElementById('dd'), rows = document.getElementById('dd-rows');
  rows.innerHTML = '';
  dd.style.display = v ? '' : 'none';
  if (v === 'ETH/USD') {
    setTimeout(function () {
      rows.innerHTML = '<tr data-test-id="search_row_1234567"><td>ETH/USD</td><td>Ethereum</td><td>Cryptocurrencies</td></tr>'
        + '<tr><td>ETH/USDT</td><td>Ethereum Tether</td><td>Cryptocurrencies</td></tr>';
    }, 300);
  }
});
</script>
</body>""")


def test_probe_reads_the_suggestion_rows_the_details_dump_skips(chromium_page):
    chromium_page.set_content(SUGGESTION_PAGE)
    a = DXtradeAdapter()
    res = a.probe_instrument_details(
        chromium_page, "ETHUSD", query=a.search_query_for("ETHUSD", "slash"), settle_ms=1_000)
    sug = res["suggestions"]
    assert sug["found"] is True and sug["n_panels"] == 1
    panel = sug["panels"][0]
    assert panel["via"] == "tbody tr" and panel["n_visible"] == 2
    assert panel["rows"][0]["cells"] == ["ETH/USD", "Ethereum", "Cryptocurrencies"]
    assert panel["rows"][0]["tid"] == "search_row_#######"     # digit run masked
    # The positions table has no "Asset Class" header: never read.
    assert not any("BTC/USD" in (c or "") for r in panel["rows"] for c in r["cells"])
    assert res["symbol_echoed"] is True                          # echoed via the rows
    assert res["reset"] is True and chromium_page.input_value("#wl-search") == ""
    chromium_page.set_content(DIVGRID.read_text())


def test_suggestion_rows_report_not_found_when_no_result_panel_is_open(chromium_page):
    chromium_page.set_content(EMPTY_WATCHLIST_PAGE)
    res = DXtradeAdapter().probe_instrument_details(chromium_page, "ETHUSD")
    assert res["suggestions"]["found"] is False
    assert res["suggestions"]["why"] == 'no visible "Asset Class" header'
    chromium_page.set_content(DIVGRID.read_text())


def test_suggestion_rows_read_only_reads_no_input_value_and_clicks_nothing():
    from src.prop.platform.dxtrade import SUGGESTION_ROWS_JS
    assert ".click(" not in SUGGESTION_ROWS_JS
    assert ".value" not in SUGGESTION_ROWS_JS
    assert "dispatchEvent" not in SUGGESTION_ROWS_JS


# ── KEY-BY-KEY variants (TRADEIFY-GOLIVE, #15472): ETH/USD typed with fill()
# left the suggestion table's tbody empty. The variant probe types ETH,
# ETHUSD and ETH/USD key by key and reads only the suggestion rows. ─────────
def test_search_query_variants_base_venue_slash():
    v = DXtradeAdapter.search_query_variants
    assert v("ETHUSD") == ["ETH", "ETHUSD", "ETH/USD"]
    assert v("xrpusdt") == ["XRP", "XRPUSDT", "XRP/USDT"]
    assert v("MNQ") == ["MNQ"]


KEYUP_SUGGESTION_PAGE = SUGGESTION_PAGE.replace(
    "document.getElementById('wl-search').addEventListener('input',",
    "document.getElementById('wl-search').addEventListener('keyup',").replace(
    "if (v === 'ETH/USD') {", "if (v === 'ETH') {").replace(
    # Like a debounced venue search: a late result only lands if the field
    # still holds the query it was fetched for.
    "      rows.innerHTML = '<tr", "      if ((document.getElementById('wl-search').value || '').toUpperCase() !== 'ETH') return;\n"
    "      rows.innerHTML = '<tr")


def test_probe_search_variants_types_key_by_key_and_reads_rows(chromium_page):
    chromium_page.set_content(KEYUP_SUGGESTION_PAGE)
    res = DXtradeAdapter().probe_search_variants(chromium_page, "ETHUSD", settle_ms=800, key_delay_ms=10)
    assert res["searched"] is True
    qs = [v["query"] for v in res["variants"]]
    assert qs == ["ETH", "ETHUSD", "ETH/USD"]
    eth = res["variants"][0]
    assert eth["readback_matches"] is True
    rows = eth["suggestions"]["panels"][0]["rows"]
    assert rows[0]["cells"] == ["ETH/USD", "Ethereum", "Cryptocurrencies"]
    assert res["variants"][1]["suggestions"]["panels"][0]["n_rows"] == 0   # ETHUSD: no rows
    assert res["reset"] is True and res["blurred"] is True
    assert chromium_page.input_value("#wl-search") == ""
    assert chromium_page.locator("[data-metis-search-hit]").count() == 0
    chromium_page.set_content(DIVGRID.read_text())


def test_probe_search_variants_never_presses_enter_or_clicks():
    import inspect
    src = inspect.getsource(DXtradeAdapter.probe_search_variants)
    assert ".click(" not in src and ".press(" not in src and "keyboard" not in src
    assert "press_sequentially(q," in src           # characters of the query only


def test_suggestion_rows_never_widen_into_another_table_when_results_are_empty(chromium_page):
    # #15472 fix: an EMPTY suggestion tbody must read as 0 rows, never as the
    # positions table's rows found by widening past it.
    chromium_page.set_content(SUGGESTION_PAGE)
    chromium_page.fill("#wl-search", "ZZZ")          # opens the panel, no results
    from src.prop.platform.dxtrade import SUGGESTION_ROWS_JS
    got = chromium_page.evaluate(SUGGESTION_ROWS_JS)
    assert got["found"] is True and got["n_panels"] == 1
    assert got["panels"][0]["n_rows"] == 0 and got["panels"][0]["rows"] == []
    chromium_page.set_content(DIVGRID.read_text())


# ── BREAKOUT-SUBMIT-VIS: the submit hit-test says WHICH state blocked it ───


def _spec(**kw):
    from src.prop.platform.base import BracketSpec
    base = dict(ticket_id="t1", venue_symbol="ETHUSD", side="long", quantity=0.01,
                stop_loss=2696.51, take_profit=2750.99, order_type="limit",
                limit_price=2723.74, price_step=0.01)
    base.update(kw)
    return BracketSpec(**base)


class _SubmitPage:
    """A page whose ``check`` reports ``visible`` from a scripted list, so the
    poll in ``_ready_submit`` is exercised without a browser. Records every
    op; ``click``/``fill`` raise, because this path must press nothing."""

    def __init__(self, visibles, text="Buy 0.01 ETHUSD at 2,723.74", why_not=None):
        self.visibles = list(visibles)
        self.text = text
        self.why_not = why_not or {}
        self.ops = []

    def evaluate(self, js, args=None):
        op = (args or [None])[0]
        self.ops.append(op)
        if op in ("mark", "reveal"):
            return {"ok": True, "scrolled": True, "text": self.text, "moved": []}
        if op == "check":
            vis = self.visibles.pop(0) if self.visibles else False
            out = {"ok": True, "same": True, "visible": vis, "enabled": True, "text": self.text}
            if not vis:
                out["why_not"] = self.why_not
            return out
        return {}

    def wait_for_timeout(self, *a):
        pass

    def click(self, *a, **k):            # pragma: no cover - must never run
        raise AssertionError("_ready_submit clicked something")

    def fill(self, *a, **k):             # pragma: no cover - must never run
        raise AssertionError("_ready_submit filled something")


def _ready(page, **kw):
    a = DXtradeAdapter()
    a._find_form = lambda p: {"found": True, "fields_box": [1543, 103, 330, 708],
                              "button_boxes": {"submit": [1559, 1537, 298, 48]}}
    a._read_back = lambda form, spec, want: []
    return a._ready_submit(page, _spec(), {"quantity": 0.01}, **kw)


def test_ready_submit_polls_a_transient_blocker_and_scrolls_the_buttons_own_container():
    # MEASURED 2026-10-02: the same submit box [1559, 1537, 298, 48] refused six
    # live ETH tickets 04:34-05:15Z and passed the dry walk at 06:14Z (#15464),
    # so a single instantaneous hit-test turns a transient blocker into a lost
    # ticket. The retry reveals the button's OWN scroll ancestors, not the
    # form's -- the live submit is a footer outside the fields' container.
    page = _SubmitPage([False, False, True])
    ready, why, _form, info = _ready(page)
    assert ready is True and why == ""
    assert info["tries"] == 3 and "why_not" not in info
    assert page.ops.count("reveal") == 2


def test_ready_submit_refusal_names_the_occluder_instead_of_only_not_visible():
    page = _SubmitPage([False], why_not={
        "viewport": [1920, 1600], "rect": [1559, 1537, 298, 48], "centre": [1708, 1561],
        "in_viewport": True, "clip_chain": [], "dialogs": 1,
        "occluder": {"tag": "div", "cls": ["toast"], "tid": "notification", "box": [1500, 1500, 420, 100],
                     "rel": "unrelated"}})
    ready, why, _form, info = _ready(page, attempts=1)
    assert ready is False
    assert why.startswith("submit: not visible at its centre after scrolling")
    assert "div[notification]" in why and "1 dialog(s) open" in why
    assert info["why_not"]["centre"] == [1708, 1561]


def test_ready_submit_still_refuses_a_control_that_changed_and_a_disabled_one():
    class _Changed(_SubmitPage):
        def evaluate(self, js, args=None):
            got = super().evaluate(js, args)
            if (args or [None])[0] == "check":
                got["same"] = False
            return got

    ready, why, _f, _i = _ready(_Changed([True]))
    assert ready is False and why == "submit: the control changed after scrolling"

    class _Disabled(_SubmitPage):
        def evaluate(self, js, args=None):
            got = super().evaluate(js, args)
            if (args or [None])[0] == "check":
                got["enabled"] = False
            return got

    ready, why, _f, _i = _ready(_Disabled([True]))
    assert ready is False and why == "submit: disabled"


def test_ready_submit_refuses_the_wrong_side_after_the_poll():
    page = _SubmitPage([True], text="Sell 0.01 ETHUSD at 2,723.74")
    ready, why, _f, _i = _ready(page)
    assert ready is False and "does not name the intended side (buy)" in why


def test_submit_not_visible_why_tells_the_three_states_apart():
    assert submit_not_visible_why(None) == ""
    assert submit_not_visible_why({}) == ""
    off = submit_not_visible_why({"in_viewport": False, "centre": [1708, 1561], "viewport": [1920, 900]})
    assert "outside the [1920, 900] viewport" in off
    over = submit_not_visible_why({"in_viewport": True, "centre": [1708, 1561],
                                   "occluder": {"tag": "section", "box": [0, 0, 10, 10], "rel": "ancestor"}})
    assert "paints section at [0, 0, 10, 10] (ancestor)" in over and "dialog" not in over
    none = submit_not_visible_why({"in_viewport": True, "centre": [1708, 1561], "occluder": None})
    assert none == "nothing is painted at its centre [1708, 1561]"


# A real Chromium against a page that REPRODUCES the live geometry measured on
# breakout_1 (panel [1543, 103, 330, 708], submit [1559, 1537, 298, 48], viewport
# 1920x1600): the ticket's clip box is 708 tall, the submit is a footer in its
# overflowing content, and the only difference between the two cases is whether
# something is painted on top. Skipped where playwright / Chromium is absent.
_SUBMIT_PAGE = """<!doctype html><meta charset=utf-8><style>
 html,body{margin:0;height:100%;overflow:hidden;font:14px sans-serif}
 #col{position:absolute;left:1543px;top:103px;width:330px;height:1497px;background:#eee}
 #clip{height:708px;overflow:hidden;position:relative}
 #content{height:1482px}
 #fields{height:708px}
 #submit{position:absolute;left:16px;top:1434px;width:298px;height:48px}
 OVERLAY_CSS
</style>
<div id=col><div id=clip><div id=content>
 <div id=fields data-metis-form=1>
  <button data-test-id=BUY data-metis-btn=side_buy>Buy</button>
  <button data-test-id=SELL data-metis-btn=side_sell>Sell</button>
  <input data-test-id=symbol_input value=ETHUSD></div>
 <button id=submit data-metis-btn=submit>Buy 0.01 ETHUSD at 2,723.74</button>
</div></div></div>OVERLAY_HTML"""


def _submit_js_on(html: str):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from src.prop.platform.dxtrade import SUBMIT_JS
    exe = next((str(c) for c in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")), None)
    if exe is None:
        pytest.skip("no Chromium under /opt/pw-browsers")
    # In a WORKER THREAD: an earlier test in this file can leave a running
    # asyncio loop (the anyio plugin), and Playwright's sync API refuses to
    # start inside one -- which made these two pass alone and fail in the full
    # file. A fresh thread has no running loop.
    out: List[Any] = []
    thread = threading.Thread(target=lambda: out.append(_drive(sync_playwright, SUBMIT_JS, exe, html)))
    thread.start()
    thread.join(120)
    assert out, "the browser thread produced nothing"
    if isinstance(out[0], BaseException):
        raise out[0]
    return out[0]


def _drive(sync_playwright: Any, SUBMIT_JS: str, exe: str, html: str):
    try:
        return _drive_inner(sync_playwright, SUBMIT_JS, exe, html)
    except BaseException as exc:                 # handed back to the test thread
        return exc


def _drive_inner(sync_playwright: Any, SUBMIT_JS: str, exe: str, html: str):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=exe)
        try:
            page = browser.new_context(viewport={"width": 1920, "height": 1600}).new_page()
            page.set_content(html)
            box = page.evaluate("() => { const r = document.querySelector('#submit').getBoundingClientRect();"
                                " return [r.left, r.top, r.width, r.height].map(Math.round); }")
            page.evaluate(SUBMIT_JS, ["mark", "tok"])
            first = page.evaluate(SUBMIT_JS, ["check", "tok"])
            revealed = page.evaluate(SUBMIT_JS, ["reveal", ""])
            return box, first, revealed, page.evaluate(SUBMIT_JS, ["check", "tok"])
        finally:
            browser.close()


def test_submit_js_reaches_the_live_footer_geometry_when_nothing_is_on_top():
    box, first, _rev, _after = _submit_js_on(
        _SUBMIT_PAGE.replace("OVERLAY_CSS", "").replace("OVERLAY_HTML", ""))
    assert box == [1559, 1537, 298, 48]      # the live measurement, reproduced
    assert first["visible"] is True and "why_not" not in first


def test_submit_js_names_what_is_painted_over_the_live_footer_geometry():
    box, first, _rev, after = _submit_js_on(
        _SUBMIT_PAGE
        .replace("OVERLAY_CSS", "#over{position:fixed;inset:0;background:rgba(0,0,0,.2)}")
        .replace("OVERLAY_HTML", "<div id=over data-test-id=order_warning></div>"))
    assert box == [1559, 1537, 298, 48]
    assert first["visible"] is False
    why = submit_not_visible_why(first["why_not"])
    assert "paints div[order_warning]" in why and "(unrelated)" in why
    # An overlay is not something a scroll can clear: it must still refuse.
    assert after["visible"] is False


# ── edit-surface diff probe (DIALOG-MEASURE, after #15628) ─────────────────
# INVENTED layout carrying the measured hazard: the row's modify control
# opens NO new dialog; it switches the docked sidebar ticket into a modify
# mode (heading, read-only qty, SL/TP, Save + Cancel), which Escape does not
# leave.

SURFACE_HTML = """
<html><head><style>table { border-collapse: collapse; }</style></head><body>
<div id="side"><h3 id="hd">Order Ticket</h3>
  <div id="entry"><button data-test-id="BUY" onclick="window.__log.push('buy')">Buy</button>
  <button data-test-id="SELL" onclick="window.__log.push('sell')">Sell</button>
  <button>Market</button><button>Limit</button><button>Stop</button>
  <span>Lots</span><input id="q" value="1">
  <button id="sub" onclick="window.__log.push('submit')">Buy 1 ETHUSD at 2657.5</button></div>
  <div id="mod" style="display:none"><span>Quantity</span><input value="1.22" readonly>
  <span>Stop Loss</span><input id="msl" value="2718.46"><select><option selected>Price</option></select>
  <button onclick="window.__log.push('save')">Save</button>
  <button id="mcancel" onclick="window.__log.push('cancel'); window.__exit()">Cancel</button></div></div>
<table><thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Open Price</th><th></th></tr></thead>
<tbody><tr><td>ETHUSD</td><td>Sell</td><td>1.22</td><td>2657.17</td>
<td><button class="button-closeBy" disabled><i class="icon-close-by"></i></button><button data-test-id="position-table-modify"
 onclick="window.__log.push('modify'); window.__enter()"><i class="icon-replace-context"></i></button><button
 class="button-closePosition" onclick="window.__log.push('close')"><i class="icon-close-position"></i></button></td></tr></tbody></table>
<script>
window.__log = [];
window.__enter = () => { document.getElementById('entry').style.display = 'none';
  document.getElementById('mod').style.display = 'block'; document.getElementById('hd').textContent = 'Modify Position'; };
window.__exit = () => { document.getElementById('entry').style.display = 'block';
  document.getElementById('mod').style.display = 'none'; document.getElementById('hd').textContent = 'Order Ticket'; };
</script></body></html>
"""


@pytest.fixture()
def surface_page(chromium_page):
    chromium_page.set_content(SURFACE_HTML)
    yield chromium_page
    chromium_page.set_content(DIVGRID.read_text())


def test_surface_probe_measures_a_sidebar_modify_mode_and_leaves_by_its_own_cancel(surface_page):
    got = _edit_adapter().probe_edit_surface(surface_page, "ETHUSD")
    assert got["locate"]["ok"] and got["clicked"] is True
    assert got["baseline_order_entry"]["side_buttons"] is True and got["baseline_order_entry"]["submit_label"] is True
    assert got["after_order_entry"]["side_buttons"] is False and not got["diff"]["same"]
    assert any(i["name"] == "Save" for i in got["diff"]["added"])
    assert got["cancel"]["ok"] and got["restored"] is True and got["alerts"] == []
    assert surface_page.evaluate("window.__log") == ["modify", "cancel"]       # never Save / submit / close


def test_surface_probe_alerts_and_presses_nothing_when_the_surface_has_no_cancel(surface_page):
    surface_page.evaluate("() => { document.getElementById('mcancel').remove(); }")
    got = _edit_adapter().probe_edit_surface(surface_page, "ETHUSD")
    assert got["restored"] is False and got["cancel"]["ok"] is False
    assert any("NOT BACK AT BASELINE" in a for a in got["alerts"])
    assert surface_page.evaluate("window.__log") == ["modify"]                  # Save never pressed


def test_surface_probe_with_no_position_row_clicks_nothing(surface_page):
    got = _edit_adapter().probe_edit_surface(surface_page, "SOLUSD")
    assert got["locate"]["ok"] is False and "clicked" not in got
    assert surface_page.evaluate("window.__log") == []


def test_surface_diff_never_calls_an_unread_snapshot_the_same():
    from src.prop.platform.dxtrade import surface_diff
    snap = {"ok": True, "items": [{"key": "a", "value": "1", "readonly": False, "disabled": False}]}
    assert surface_diff(snap, snap)["same"] is True
    assert surface_diff(snap, {"ok": False, "why": "x"})["same"] is False
    moved = {"ok": True, "items": [{"key": "a", "value": "2", "readonly": False, "disabled": False}]}
    assert surface_diff(snap, moved)["changed"] and not surface_diff(snap, moved)["same"]


def test_the_tick_resolves_the_edit_surface_probe_and_defers_it_to_a_live_ticket():
    from types import SimpleNamespace
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    base = dict(probe_ticket="", dry_run=False, watched_click=False, round_trip="", close_position="")
    assert resolve_mode(SimpleNamespace(**base, edit_surface_probe="ETHUSD"), {}) == "edit_surface_probe"
    assert "edit_surface_probe" in YIELD_MODES


# ── modify_bracket on the MEASURED "Position Details" panel (#15657) ──────
# INVENTED layout shaped like the live readout: the row's modify control
# opens a docked panel ("Position Details", the symbol, "Protection",
# "Stop Loss:" / "Take Profit:" each beside a "Price" button, then
# "Close Position" (enabled) beside "Modify Position" (disabled until a value
# changes), "Discard" below). A docked ORDER-ENTRY sidebar with its own
# SL/TP inputs and submit sits on the page throughout.

PANEL_HTML = """
<html><head><style>table { border-collapse: collapse; } #pp { position: absolute; left: 900px; top: 0; width: 330px; }</style></head><body>
<div id="side"><input data-test-id="symbol_input" value="ETHUSD">
  <button data-test-id="BUY" onclick="window.__log.push('buy')">Buy</button>
  <button data-test-id="SELL" onclick="window.__log.push('sell')">Sell</button>
  <span>Stop Loss</span><input id="ssl" value=""><button>Price</button>
  <span>Take Profit</span><input id="stp" value=""><button>Price</button>
  <button id="ssub" onclick="window.__log.push('sidebar-submit')">Sell 1.22 ETHUSD at 2,657.11</button></div>
<table><thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Open Price</th><th>Stop Loss</th><th>Take Profit</th><th></th></tr></thead>
<tbody id="rows"><tr><td>ETHUSD</td><td>Sell</td><td>1.22</td><td>2,657.17</td><td>2,718.46</td><td>2,394.06</td>
<td><button class="button-closeBy" disabled><i class="icon-close-by"></i></button><button data-test-id="position-table-modify"
 onclick="window.__log.push('modify'); window.__open()"><i class="icon-replace-context"></i></button><button
 class="button-closePosition" onclick="window.__log.push('row-close')"><i class="icon-close-position"></i></button></td></tr></tbody></table>
<div id="pp" style="display:none"><div><h3>Position Details</h3></div>
  <div><div>ETHUSD</div><div>ETH</div></div><div>Protection</div>
  <div><div>Stop Loss:</div><div><input id="psl" value="2718.46" oninput="window.__dirty()"><button id="slmode">Price</button></div></div>
  <div><div>Take Profit:</div><div><input id="ptp" value="2394.06" oninput="window.__dirty()"><button>Price</button></div></div>
  <div><button id="pclose" onclick="window.__log.push('panel-close')">Close Position</button>
       <button id="pmod" disabled onclick="window.__log.push('panel-modify')">Modify Position</button></div>
  <button id="pdis" onclick="window.__log.push('discard'); window.__shut()">Discard</button></div>
<script>
window.__log = [];
window.__open = () => { document.getElementById('pp').style.display = 'block'; };
window.__shut = () => { document.getElementById('pp').style.display = 'none'; };
window.__dirty = () => { if (!window.__stuckModify) document.getElementById('pmod').disabled = false; };
</script></body></html>
"""


@pytest.fixture()
def panel_page(chromium_page):
    chromium_page.set_content(PANEL_HTML)
    yield chromium_page
    chromium_page.set_content(DIVGRID.read_text())


def _armed():
    a = _edit_adapter()
    a.EDIT_DIALOG_MEASURED = True
    return a


def _eth_short(qty=1.22):
    from src.prop.platform.base import Position
    return Position(symbol="ETHUSD", side="short", quantity=qty)


def test_panel_modify_types_into_the_panel_and_presses_only_modify_position(panel_page):
    r = _armed().modify_bracket(panel_page, _eth_short(), 2700.0, None, arm=True)
    assert r["ok"] is True, r["why"]
    assert panel_page.evaluate("window.__log") == ["modify", "panel-modify"]
    assert panel_page.evaluate("document.getElementById('psl').value") == "2700"
    assert panel_page.evaluate("document.getElementById('ssl').value") == ""        # sidebar untouched


def test_panel_modify_refuses_when_close_position_is_the_only_enabled_button(panel_page):
    # Modify Position never enables; Close Position sits beside it, enabled.
    panel_page.evaluate("() => { window.__stuckModify = true; }")
    r = _armed().modify_bracket(panel_page, _eth_short(), 2700.0, None, arm=True)
    assert r["ok"] is False and "disabled after the values read back" in r["why"]
    assert panel_page.evaluate("window.__log") == ["modify", "discard"]              # never Close Position
    assert r["exit"]["closed"] is True


def test_panel_modify_refuses_a_mode_other_than_price(panel_page):
    panel_page.evaluate("() => { document.getElementById('slmode').textContent = 'Pips'; }")
    r = _armed().modify_bracket(panel_page, _eth_short(), 2700.0, None, arm=True)
    assert r["ok"] is False and "stop_loss mode reads 'Pips'" in r["why"]
    assert panel_page.evaluate("window.__log") == ["modify", "discard"]
    assert panel_page.evaluate("document.getElementById('psl').value") == "2718.46"  # nothing typed


def test_panel_modify_refuses_a_modify_control_that_opens_no_panel(panel_page):
    panel_page.evaluate("() => { window.__open = () => {}; }")
    r = _armed().modify_bracket(panel_page, _eth_short(), 2700.0, None, arm=True)
    assert r["ok"] is False and "0 Position Details panels" in r["why"]
    assert panel_page.evaluate("window.__log") == ["modify"]                         # no sidebar control touched
    assert panel_page.evaluate("document.getElementById('ssl').value") == ""


def test_panel_modify_refuses_several_rows_for_the_symbol_before_any_click(panel_page):
    panel_page.evaluate("() => { const r = document.querySelector('#rows tr'); r.parentElement.appendChild(r.cloneNode(true)); }")
    r = _armed().modify_bracket(panel_page, _eth_short(), 2700.0, None, arm=True)
    assert r["ok"] is False and r["clicked"] is False and "found 2" in r["why"]
    assert panel_page.evaluate("window.__log") == []


def test_panel_modify_refuses_when_the_row_size_is_not_the_position(panel_page):
    r = _armed().modify_bracket(panel_page, _eth_short(qty=2.0), 2700.0, None, arm=True)
    assert r["ok"] is False and r["clicked"] is False and "size" in r["why"]
    assert panel_page.evaluate("window.__log") == []


def test_panel_modify_reports_a_panel_that_discard_does_not_close(panel_page):
    panel_page.evaluate("() => { window.__shut = () => {}; window.__stuckModify = true; }")
    r = _armed().modify_bracket(panel_page, _eth_short(), 2700.0, None, arm=True)
    assert r["ok"] is False and "PANEL STILL OPEN" in r["why"]


def test_panel_modify_disarmed_and_unflagged_never_click(panel_page):
    r = _edit_adapter().modify_bracket(panel_page, _eth_short(), 2700.0, None, arm=True)
    assert r["ok"] is False and r["clicked"] is False and "EDIT_DIALOG_MEASURED" in r["why"]
    r = _armed().modify_bracket(panel_page, _eth_short(), 2700.0, None, arm=False)
    assert r["ok"] is True and r["clicked"] is False
    assert panel_page.evaluate("window.__log") == []


def test_panel_modify_refuses_an_edit_click_of_unknown_outcome():
    calls = []

    class P:
        keyboard = type("K", (), {"press": staticmethod(lambda *a: calls.append(("escape",)))})()

        def click(self, *a, **k):
            raise TimeoutError("x")

        def fill(self, *a, **k):
            calls.append(("fill", a))

        def wait_for_timeout(self, *a):
            pass

        def locator(self, *a):
            return type("L", (), {"count": staticmethod(lambda: 0)})()

        def evaluate(self, *a):
            return {"ok": False, "panels": 0}

    a = _armed()
    a._locate_edit_control = lambda *x: {"ok": True, "row": {}, "controls": [], "chosen": 1}
    r = a.modify_bracket(P(), _eth_short(), 2700.0, None, arm=True)
    assert r["ok"] is False and "outcome unknown" in r["why"]
    assert ("fill",) not in [c[:1] for c in calls] and calls == [("escape",)]


_GOOD_PANEL = {"ok": True, "panels": 1, "text": "Position Details ETHUSD ETH Protection", "ambiguous": [],
               "fields": {"stop_loss": {"value": "1", "mode": "Price", "readonly": False},
                          "take_profit": {"value": "2", "mode": "Price", "readonly": False}},
               "submit": 1, "submit_name": "Modify Position", "submit_in_box": True, "submit_enabled": True, "discard": 1}


def test_position_panel_mismatch_passes_only_the_positions_own_panel():
    from src.prop.platform.dxtrade import position_panel_mismatch
    assert position_panel_mismatch(_GOOD_PANEL, "ETHUSD", require_submit_enabled=True) == []
    cases = {
        "does not name": {"text": "Position Details SOLUSD"},
        "mode reads 'Pips'": {"fields": {**_GOOD_PANEL["fields"], "stop_loss": {"value": "1", "mode": "Pips", "readonly": False}}},
        "no take_profit field": {"fields": {"stop_loss": _GOOD_PANEL["fields"]["stop_loss"]}},
        "read-only": {"fields": {**_GOOD_PANEL["fields"], "take_profit": {"value": "2", "mode": "Price", "readonly": True}}},
        "need exactly 1": {"submit": 2},
        "names Close": {"submit_name": "Close Position"},
        "not boxed": {"submit_in_box": False},
        "disabled after": {"submit_enabled": False},
        "'Discard' buttons": {"discard": 0},
    }
    for needle, patch in cases.items():
        got = position_panel_mismatch({**_GOOD_PANEL, **patch}, "ETHUSD", require_submit_enabled=True)
        assert any(needle in g for g in got), (needle, got)
    assert position_panel_mismatch({"ok": False, "why": "2 Position Details panels (need exactly 1)"}, "ETHUSD")


def test_surface_probe_ignores_live_prices_in_control_names(surface_page):
    # #15657: the quick-trade Bid/Ask buttons carry ticking prices; they must
    # not read as a change (the probe restored, no alert).
    surface_page.evaluate("() => { const b = document.createElement('button'); b.id = 'bid'; b.textContent = 'Sell 2,665.99';"
                          " document.body.prepend(b); let n = 0; setInterval(() => { b.textContent = 'Sell 2,66' + (n++ % 10) + '.73'; }, 5); }")
    got = _edit_adapter().probe_edit_surface(surface_page, "ETHUSD")
    assert got["restored"] is True and got["alerts"] == [], got.get("residual")
