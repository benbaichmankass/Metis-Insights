"""breakout_terminal adapter (lane PROP-TERM, 2026-09-28).

Every fixture under tests/fixtures/prop_breakout_terminal/ is SYNTHETIC
(see its README): these tests prove the parsers, the in-page JS and the
safety refusals against an invented layout — not Breakout's real DOM, which
is unmeasured until ``breakout-terminal-probe`` runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.prop.platform import DEFAULT_LOGIN_URLS, FeasibilityError, adapter_for_platform, load_platform_config  # noqa: E402
from src.prop.platform import breakout_terminal as bt  # noqa: E402
from src.prop.platform.base import BracketSpec, PropPlatformAdapter  # noqa: E402
from src.prop.platform.dxtrade import DXtradeAdapter  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "prop_breakout_terminal"


# ── interface parity ──────────────────────────────────────────────────


def test_flatten_takes_the_executors_row_facts():
    """The executor's watched close calls ``flatten(page, venue, arm=...,
    side=..., quantity=..., entry_price=...)`` (prop_executor
    _close_and_confirm / close_position). Before 2026-09-29 this adapter's
    flatten took only ``symbol`` and ``arm``: a TypeError on switch."""
    import inspect
    for cls in (bt.BreakoutTerminalAdapter, DXtradeAdapter):
        params = inspect.signature(cls.flatten).parameters
        for kw in ("arm", "side", "quantity", "entry_price", "rel_tol"):
            assert kw in params and params[kw].kind is inspect.Parameter.KEYWORD_ONLY, (cls.__name__, kw)


def test_same_public_surface_as_dxtrade():
    """Everything the tick scripts call on an adapter exists here too, so
    switching platform needs no caller change."""
    used_by_callers = {"login", "resume_session", "wait_ready", "read_account", "read_positions",
                       "read_orders", "read_quote", "page_shape", "structure", "start_response_capture",
                       "probe_order_ticket", "place_bracket", "modify_bracket", "cancel_order",
                       "flatten", "timeout_ms", "dump_tables", "read_one_click", "page_state"}
    a = adapter_for_platform("breakout_terminal")
    assert isinstance(a, PropPlatformAdapter) and a.platform == "breakout_terminal"
    for name in used_by_callers:
        assert hasattr(a, name), name
        assert hasattr(DXtradeAdapter(), name), name
    # Nothing overridden on the base class is left raising NotImplementedError.
    for name in ("login", "read_account", "read_positions", "read_orders", "place_bracket",
                 "modify_bracket", "cancel_order", "flatten", "probe_order_ticket"):
        assert getattr(type(a), name) is not getattr(PropPlatformAdapter, name), name


# ── config selection: one line ────────────────────────────────────────


def test_switch_is_one_line(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("accounts:\n  acct:\n    platform: dxtrade\n")
    assert load_platform_config("acct", p)["login_url"] == DEFAULT_LOGIN_URLS["dxtrade"]
    p.write_text("accounts:\n  acct:\n    platform: breakout_terminal\n")
    cfg = load_platform_config("acct", p)
    assert cfg["login_url"] == "https://app.breakoutprop.com/"
    assert isinstance(adapter_for_platform(cfg["platform"]), bt.BreakoutTerminalAdapter)


def test_leftover_url_of_the_other_terminal_raises(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("accounts:\n  acct:\n    platform: breakout_terminal\n"
                 "    login_url: https://wss.breakoutprop.com/\n")
    with pytest.raises(ValueError, match="dxtrade URL"):
        load_platform_config("acct", p)


def test_breakout_1_unchanged_and_probe_entry_present():
    cfg = load_platform_config("breakout_1")
    assert (cfg["platform"], cfg["login_url"]) == ("dxtrade", "https://wss.breakoutprop.com/")
    probe = load_platform_config("breakout_terminal", section="probes")
    assert probe["platform"] == "breakout_terminal"
    assert probe["login_url"] == "https://app.breakoutprop.com/"


def test_executor_venue_symbol_key_follows_the_platform(tmp_path, monkeypatch):
    from src.prop import prop_executor as pe
    plat = tmp_path / "plat.yaml"
    monkeypatch.setattr(pe, "PLATFORMS_PATH", plat)
    plat.write_text("accounts:\n  breakout_1:\n    platform: dxtrade\n")
    assert "SOLUSDT" in pe.load_config("breakout_1").symbols
    # No breakout_terminal_symbol rows exist: nothing maps, every ticket is
    # refused by the structure guard rather than sent under a guessed name.
    plat.write_text("accounts:\n  breakout_1:\n    platform: breakout_terminal\n")
    assert pe.load_config("breakout_1").symbols == {}


# ── pure parsers ──────────────────────────────────────────────────────


def test_account_metrics_labels_are_line_anchored():
    snap = bt.parse_account_metrics("Available Balance\n4,700.00\nBalance 5,012.40 USD\nEquity: 5,020.15\n"
                                    "Unrealized PnL -7.75")
    assert (snap.balance, snap.equity, snap.unrealized, snap.available) == (5012.4, 5020.15, -7.75, 4700.0)
    assert snap.currency == "USD"
    assert "realized_today" in snap.unparsed and snap.realized_today is None


def test_unread_metrics_are_none_never_zero():
    snap = bt.parse_account_metrics("Positions\nOrders")
    assert snap.balance is None and snap.equity is None
    assert set(snap.unparsed) == set(bt.ACCOUNT_LABELS)


@pytest.mark.parametrize("visible,text,title,want", [
    ({}, "Just a moment...", "", "challenge"),
    ({"captcha_frame": True}, "", "", "captcha"),
    ({"password_input": True}, "Email Password Forgot password? We'll send a verification code", "", "login_form"),
    ({"password_input": True}, "Invalid email or password", "", "login_error"),
    ({}, "Check your email. We sent a code.", "", "email_code"),
    ({"code_input": True}, "Enter code", "", "2fa"),
    ({"open_terminal": True}, "Balance 5,000.00 Equity 5,000.00 Positions", "", "dashboard"),
    ({}, "Balance 5,000.00\nPositions (0)", "", "terminal"),
    ({}, "Balance 5,000.00", "", "unknown"),          # no trading surface: not proof
    ({}, "Positions\nOrders", "", "unknown"),         # no balance: not proof
    ({}, "You don't have any accounts yet", "", "no_account"),
])
def test_classify_page(visible, text, title, want):
    assert bt.classify_page(visible, text, title) == want


def test_tpsl_split():
    assert bt.split_tpsl("67,000.0 / 64,000.0") == (67000.0, 64000.0)
    assert bt.split_tpsl("-- / 64,000") == (None, 64000.0)
    assert bt.split_tpsl("67000") == (None, None)
    assert bt.split_tpsl(None) == (None, None)


POS = {"headers": ["Market", "Side", "Size", "Entry Price", "Mark Price", "TP/SL", "Unrealized PnL"],
       "rows": [["BTCUSD", "Long", "0.01", "65,100.0", "65,875.0", "67,000.0 / 64,000.0", "7.75"],
                ["ETHUSD", "", "-0.5", "3,000", "2,990", "-- / --", "5"]]}
ORD = {"headers": ["Market", "Side", "Type", "Size", "Price", "Stop Loss", "Take Profit", "Order ID"],
       "rows": [["ETHUSD", "Sell", "Limit", "0.5", "3,100.0", "3,180", "2,950", "A77"]]}


def test_positions_parse_and_could_not_look():
    pos = bt.positions_from_tables([ORD, POS])
    assert [(p.symbol, p.side, p.quantity, p.entry_price, p.stop_loss, p.take_profit, p.unrealized_pnl)
            for p in pos] == [("BTCUSD", "long", 0.01, 65100.0, 64000.0, 67000.0, 7.75),
                              ("ETHUSD", "short", 0.5, 3000.0, None, None, 5.0)]
    assert bt.positions_from_tables([{"headers": POS["headers"], "rows": []}]) == []
    assert bt.positions_from_tables([ORD]) is None


def test_orders_parse_ignores_positions_table():
    orders = bt.orders_from_tables([POS, ORD])
    assert len(orders) == 1
    o = orders[0]
    assert (o.symbol, o.side, o.order_type, o.quantity, o.price, o.stop_loss, o.take_profit, o.order_id) == \
        ("ETHUSD", "short", "Limit", 0.5, 3100.0, 3180.0, 2950.0, "A77")
    assert bt.orders_from_tables([POS]) is None


def test_quote_from_tables():
    wl = {"headers": ["Market", "Bid Price", "Ask Price", "Change"],
          "rows": [["BTCUSD", "65,000.5", "65,001.0", "+1%"], ["ETHUSD", "3,001", "3,000", "0"]]}
    assert bt.quote_from_tables([POS, wl], "btcusd") == {"bid": 65000.5, "ask": 65001.0}
    assert bt.quote_from_tables([wl], "ETHUSD") is None      # crossed: never a guessed price
    assert bt.quote_from_tables([wl], "SOLUSD") is None      # no row
    assert bt.quote_from_tables([POS], "BTCUSD") is None     # no bid/ask table: could not look


def test_read_quote_refuses_while_unmeasured():
    assert bt.BreakoutTerminalAdapter().read_quote(None, "BTCUSD") is None


def test_price_column_never_reads_entry_price():
    # Exact-header match only: "Price" must not match "Entry Price".
    assert bt._find_col(["Entry Price", "Price"], ("Price",)) == 1
    assert bt._find_col(["Entry Price"], ("Price",)) is None


def test_dxtrade_js_vocabulary_was_widened():
    """The replaced snippets must actually have been found; a silent no-op
    would leave DXtrade's header vocabulary in place."""
    assert bt.EXTRACT_TABLES_JS != bt._DX_EXTRACT_TABLES_JS and "market|contract" in bt.EXTRACT_TABLES_JS
    assert bt.ROW_ACTION_JS != bt._DX_ROW_ACTION_JS and "entry price" in bt.ROW_ACTION_JS


def _spec(**kw):
    base = dict(ticket_id="t1", venue_symbol="BTCUSD", side="long", quantity=0.01,
                stop_loss=64000.0, take_profit=67000.0, order_type="limit", limit_price=65000.0)
    base.update(kw)
    return BracketSpec(**base)


def test_form_shape_refuses_side_button_that_is_the_submit():
    form = {"fields": {"quantity": {}, "price": {}, "stop_loss": {}, "take_profit": {}},
            "buttons": {"side_buy": "Buy / Long", "side_sell": "Sell / Short"}, "form_text": "BTCUSD"}
    assert "no distinct submit" in bt.check_form_shape(form, _spec())
    form["buttons"]["submit"] = "Place Order"
    assert bt.check_form_shape(form, _spec()) is None
    assert "does not name" in bt.check_form_shape(dict(form, form_text="ETHUSD"), _spec())
    assert "ambiguous" in bt.check_form_shape(dict(form, ambiguous=["price"]), _spec())


def test_submit_pattern_never_matches_a_bare_side_label():
    import re
    sub = re.compile(bt.FORM_BUTTON_PATTERNS["submit"], re.I)
    for label in ("Buy", "Sell", "Long", "Short", "Buy / Long", "Sell / Short", "Buy/Long"):
        assert not sub.match(label), label


def test_modify_bracket_is_an_explicit_refusal():
    got = bt.BreakoutTerminalAdapter().modify_bracket(None, None, 1.0, 2.0)
    assert got["ok"] is False and got["clicked"] is False and "unmeasured" in got["why"]


def test_no_order_method_in_the_probe_script():
    src = (REPO / "scripts" / "prop" / "breakout_terminal_probe.py").read_text()
    body = src.split('"""', 2)[2]  # skip the docstring, which names them to forbid them
    for name in ("place_bracket", "modify_bracket", "cancel_order", "flatten("):
        assert name not in body, name


def test_probe_redaction():
    sys.path.insert(0, str(REPO / "scripts" / "prop"))
    import breakout_terminal_probe as probe
    assert probe.redact_ids("Account BO-1234567 · Balance 4,724.50") == "Account <id> · Balance 4,724.50"
    assert probe.redact_ids("#88231") == "<id>"
    assert probe.origins_line(["https://app.x.com/a?sid=1", "wss://ws.x.com/s", "about:blank"]) == \
        "probe.request_origins: ['https://app.x.com', 'wss://ws.x.com']"


# ── the adapter in a real Chromium, against SYNTHETIC pages ────────────


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    import glob
    candidates = [None] + sorted(glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"))
    with sync_api.sync_playwright() as pw:
        b, errors = None, []
        for exe in candidates:
            try:
                b = pw.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
                break
            except Exception as exc:
                errors.append(str(exc).splitlines()[0][:120])
        if b is None:
            pytest.skip(f"chromium unavailable: {errors}")
        yield b
        b.close()


def _site(browser, pages):
    """A context whose every request to app.breakoutprop.com is answered from
    ``pages`` (path → fixture file). Nothing reaches the network."""
    ctx = browser.new_context()

    def handle(route):
        path = "/" + route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        name = pages.get(path)
        if name is None:
            return route.fulfill(status=404, body="not found")
        route.fulfill(status=200, content_type="text/html", body=(FIX / name).read_text())

    ctx.route("https://app.breakoutprop.com/**", handle)
    ctx.route(lambda url: "breakoutprop.com" not in url, lambda r: r.abort())
    return ctx


FLOW = {"/": "login.html.txt", "/dashboard": "dashboard.html.txt", "/terminal": "terminal.html.txt"}


def test_login_dashboard_open_terminal_in_new_tab_and_read(browser):
    ctx = _site(browser, FLOW)
    page = ctx.new_page()
    a = bt.BreakoutTerminalAdapter(timeout_ms=8_000)
    a.login(page, "https://app.breakoutprop.com/", "user@example.com", "pw")
    term = a._t(page)
    assert term is not page and term.url.endswith("/terminal")  # the new tab was adopted
    snap = a.read_account(page)
    assert (snap.balance, snap.equity, snap.unrealized, snap.available) == (5012.4, 5020.15, 7.75, 4700.0)
    pos = a.read_positions(page)
    assert [(p.symbol, p.side, p.quantity, p.entry_price, p.stop_loss, p.take_profit) for p in pos] == \
        [("BTCUSD", "long", 0.01, 65100.0, 64000.0, 67000.0)]
    orders = a.read_orders(page)
    assert [(o.symbol, o.side, o.price, o.order_id, o.stop_loss, o.take_profit) for o in orders] == \
        [("ETHUSD", "short", 3100.0, "A77", 3180.0, 2950.0)]
    # The redacted dumps run and never carry the identity typed at login.
    blob = "\n".join(a.structure(page, ("user@example.com", "pw")) + a.page_shape(page, ("user@example.com",)))
    assert "user@example.com" not in blob and "structure: END" in blob
    ctx.close()


def test_email_code_is_a_feasibility_stop(browser):
    ctx = _site(browser, {"/": "email_code.html.txt"})
    page = ctx.new_page()
    with pytest.raises(FeasibilityError) as ei:
        bt.BreakoutTerminalAdapter(timeout_ms=4_000).login(page, "https://app.breakoutprop.com/", "u", "p")
    assert ei.value.reason == "email_code"
    ctx.close()


def test_no_account_is_a_feasibility_stop(browser):
    ctx = _site(browser, {"/": "no_account.html.txt"})
    page = ctx.new_page()
    with pytest.raises(FeasibilityError) as ei:
        bt.BreakoutTerminalAdapter(timeout_ms=4_000).resume_session(page, "https://app.breakoutprop.com/")
    assert ei.value.reason == "no_account"
    ctx.close()


def _terminal_page(browser, one_click_off=True):
    ctx = _site(browser, {"/terminal": "terminal.html.txt"})
    page = ctx.new_page()
    page.goto("https://app.breakoutprop.com/terminal")
    if not one_click_off:
        page.evaluate("document.getElementById('oc').checked = true")
    return ctx, page


def test_place_bracket_disarmed_fills_verifies_and_never_submits(browser):
    ctx, page = _terminal_page(browser)
    a = bt.BreakoutTerminalAdapter(timeout_ms=5_000)
    got = a.place_bracket(page, _spec())
    assert got.stage == "form_verified" and got.submitted is False, got.detail
    clicks = page.evaluate("window.__clicks")
    assert "submit" not in clicks and clicks[:2] == ["b", "l"]  # side + type only
    ctx.close()


def test_place_bracket_refuses_when_one_click_is_on(browser):
    ctx, page = _terminal_page(browser, one_click_off=False)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).place_bracket(page, _spec())
    assert got.stage == "refused" and "one-click" in got.detail
    assert page.evaluate("window.__clicks") == []
    ctx.close()


def test_place_bracket_refuses_without_a_one_click_control(browser):
    ctx, page = _terminal_page(browser)
    page.evaluate("document.getElementById('oc').closest('div').remove()")
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).place_bracket(page, _spec())
    assert got.stage == "refused" and "unknown" in got.detail
    ctx.close()


def test_place_bracket_refuses_side_as_submit_layout(browser):
    ctx, page = _terminal_page(browser)
    page.evaluate("document.getElementById('submit').remove()")
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).place_bracket(page, _spec())
    assert got.stage == "refused" and "no distinct submit" in got.detail
    assert page.evaluate("window.__clicks") == []  # not even the side was chosen
    ctx.close()


def test_probe_ticket_types_nothing(browser):
    ctx, page = _terminal_page(browser)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).probe_order_ticket(page, "BTCUSD")
    assert got["surface"] == "dom" and set(got["fields"]) == {"quantity", "price", "stop_loss", "take_profit"}
    assert got["buttons"].get("submit") == "Place Order"
    assert page.evaluate("window.__clicks") == []
    assert page.evaluate("document.getElementById('qty').value") == ""
    ctx.close()


def test_cancel_disarmed_finds_one_row_and_does_not_click(browser):
    from src.prop.platform.base import WorkingOrder
    ctx, page = _terminal_page(browser)
    a = bt.BreakoutTerminalAdapter(timeout_ms=5_000)
    got = a.cancel_order(page, WorkingOrder(symbol="ETHUSD", order_id="A77"))
    assert got == {"ok": True, "clicked": False, "why": "disarmed: stopped before the click"}
    assert a.cancel_order(page, WorkingOrder(symbol="ETHUSD", order_id=None))["ok"] is False
    ctx.close()


def test_ticket_opener_can_never_be_a_submit():
    import re
    sub = re.compile(bt.FORM_BUTTON_PATTERNS["submit"], re.I)
    for name in bt.TICKET_OPENER_NAMES:
        assert not sub.match(name), name


def test_replace_once_fails_loudly_on_a_reworded_snippet():
    with pytest.raises(RuntimeError, match="found 0 times"):
        bt._replace_once("abc", "xyz", "q")
    with pytest.raises(RuntimeError, match="found 2 times"):
        bt._replace_once("xyzxyz", "xyz", "q")
    assert "entry price" in bt.CLOSE_ROW_JS and "market|contract" in bt.CLOSE_ROW_JS


def test_normalise_row_facts_reads_side_from_a_signed_size():
    assert bt.normalise_row_facts({"side": "", "size": "-0.5"}) == {"side": "short", "size": "0.5"}
    assert bt.normalise_row_facts({"side": "Long", "size": "0.01"}) == {"side": "Long", "size": "0.01"}
    assert bt.normalise_row_facts({"side": None, "size": None}) == {"side": None, "size": None}


def test_place_bracket_armed_walks_the_path_then_refuses(browser):
    ctx, page = _terminal_page(browser)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).place_bracket(page, _spec(), arm=True)
    assert got.stage == "refused" and got.submitted is False
    assert "armed submit refused" in got.detail and "DRBVUUDJ-0001" in got.detail
    clicks = page.evaluate("window.__clicks")
    assert "submit" not in clicks and clicks[:2] == ["b", "l"]   # the whole dry walk ran first
    ctx.close()


def test_place_bracket_refuses_when_the_selection_is_not_readable_back(browser):
    ctx, page = _terminal_page(browser)
    page.evaluate("window.__noPress = true")
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).place_bracket(page, _spec())
    assert got.stage == "refused" and "side: not readable back" in got.detail
    assert "submit" not in page.evaluate("window.__clicks")
    ctx.close()


def test_place_bracket_skips_a_side_already_shown_selected(browser):
    ctx, page = _terminal_page(browser)
    page.evaluate("document.getElementById('open-ticket').click();"
                  "document.getElementById('b').setAttribute('aria-pressed','true');"
                  "document.getElementById('s').setAttribute('aria-pressed','false')")
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).place_bracket(page, _spec())
    assert got.stage == "form_verified", got.detail
    assert page.evaluate("window.__clicks")[:1] == ["l"]           # BUY not re-clicked
    ctx.close()


def test_probe_ticket_never_returns_the_raw_form_text(browser):
    ctx, page = _terminal_page(browser)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).probe_order_ticket(page, "BTCUSD")
    assert "form_text" not in got and got["form_text_len"] > 0 and got["names_symbol"] is True
    ctx.close()


def test_probe_ticket_refused_still_measures_the_controls(browser):
    ctx, page = _terminal_page(browser, one_click_off=False)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).probe_order_ticket(page, "BTCUSD")
    assert got["surface"] == "not_opened" and "one-click" in got["refused"]
    assert got["controls_dump"]["found"] is True
    assert page.evaluate("window.__clicks") == []
    ctx.close()


def test_cancel_armed_refuses_without_clicking(browser):
    from src.prop.platform.base import WorkingOrder
    ctx, page = _terminal_page(browser)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).cancel_order(page, WorkingOrder(symbol="ETHUSD", order_id="A77"),
                                                                    arm=True)
    assert got["ok"] is False and got["clicked"] is False and "armed orders row action refused" in got["why"]
    assert page.evaluate("window.__clicks") == []
    ctx.close()


def _positions_page(browser):
    ctx = _site(browser, {"/terminal": "positions_table.html.txt"})
    page = ctx.new_page()
    page.goto("https://app.breakoutprop.com/terminal")
    return ctx, page


def test_flatten_disarmed_picks_the_rows_last_close_control(browser):
    ctx, page = _positions_page(browser)
    a = bt.BreakoutTerminalAdapter(timeout_ms=5_000)
    got = a.flatten(page, "BTCUSD", side="long", quantity=0.01, entry_price=65100.0)
    assert got["ok"] is True and got["clicked"] is False and got["chosen"] == 2, got
    assert page.evaluate("document.querySelector('[data-metis-row-action]').id") == "close"
    # The close button's data-test-id carries an 8-digit id: masked in both
    # the markup and the hint that reach a public log.
    assert "99887766" not in str(got["controls"]) and "#####" in str(got["controls"])
    # A signed-size row with no side cell reads short.
    got = a.flatten(page, "ETHUSD", side="short", quantity=0.5, entry_price=3000.0)
    assert got["ok"] is True, got
    assert page.evaluate("window.__clicks") == []
    ctx.close()


def test_flatten_armed_refuses_before_the_click(browser):
    ctx, page = _positions_page(browser)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).flatten(page, "BTCUSD", arm=True, side="long",
                                                              quantity=0.01, entry_price=65100.0)
    assert got["ok"] is False and got["clicked"] is False and "armed close refused" in got["why"]
    assert page.evaluate("window.__clicks") == []
    ctx.close()


@pytest.mark.parametrize("mutate,why", [
    # A close-ALL icon is a qualified close: refused (it could flatten everything).
    ("document.getElementById('close').className = 'icon-close-all'", "0 close-type controls"),
    # The close is not the row's LAST control.
    ("document.getElementById('close').after(document.getElementById('edit'))", "not the LAST control"),
    # A close nested inside a reverse button: the click would bubble to it.
    ("const s = document.createElement('span'); s.setAttribute('role','button'); s.className='icon-close';"
     "document.getElementById('close').remove(); document.getElementById('edit').after(document.getElementById('rev'));"
     "document.getElementById('rev').appendChild(s)", "nested in another pressable"),
    # A reverse-named row: refused, and its id is masked in the PUBLIC refusal.
    ("document.querySelector('tr[data-test-id]').setAttribute('data-test-id', 'reverse-99887766')",
     'qualified ancestor ("reverse #####")'),
])
def test_flatten_hardened_refusals(browser, mutate, why):
    ctx, page = _positions_page(browser)
    page.evaluate(mutate)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).flatten(page, "BTCUSD", side="long", quantity=0.01)
    assert got["ok"] is False and got["clicked"] is False and why in got["why"], got
    assert "99887766" not in got["why"]
    ctx.close()


def test_flatten_refuses_a_row_that_is_not_the_position_meant(browser):
    ctx, page = _positions_page(browser)
    got = bt.BreakoutTerminalAdapter(timeout_ms=5_000).flatten(page, "BTCUSD", side="short", quantity=0.01)
    assert got["ok"] is False and "does not match the position" in got["why"]
    ctx.close()


def test_dump_tables_is_read_only_and_masks_ids(browser):
    ctx, page = _positions_page(browser)
    lines = bt.BreakoutTerminalAdapter(timeout_ms=5_000).dump_tables(page, ("user@example.com",))
    blob = "\n".join(lines)
    assert lines[0].startswith("dump_tables: BEGIN") and "reads_as=positions" in blob
    assert "first_row_control_html" in blob and "99887766" not in blob
    assert page.evaluate("window.__clicks") == []
    ctx.close()


def test_flatten_on_a_div_grid_refuses_rather_than_guessing(browser):
    # The row-action JS matches HTML tables only; the synthetic positions
    # list is a DIV grid, so flatten must refuse explicitly (the executor
    # then alerts) — never click something else.
    ctx, page = _terminal_page(browser)
    a = bt.BreakoutTerminalAdapter(timeout_ms=5_000)
    got = a.flatten(page, "BTCUSD", arm=False)
    assert got["ok"] is False and got["clicked"] is False
    assert a.flatten(page, None)["why"] == "symbol required (no close-all)"
    ctx.close()
