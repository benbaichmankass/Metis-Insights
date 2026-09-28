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
    r = a.modify_bracket(P(), Position(symbol="SOLUSD"), 1.0, 2.0)
    assert r["ok"] is True and r["clicked"] is False and calls == []
    assert r["one_click"]["state"] == "unknown" and "one-click" not in r["why"]
