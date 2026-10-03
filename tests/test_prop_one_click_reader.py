"""PROP-ETH-DOM (2026-09-30): the one-click reader and the Orders-widget
ancestor dump, exercised in real Chromium against the MEASURED live shape.

MEASURED (instrument-info-dry, issue #14714, run 36710165302, one_click_dump):
the toggle is a ``div`` with ``data-test-id="one_click_trading"`` and
``data-value="false"``, a sibling of the "One-click trading" label inside a
~134x17 px row -- no checkbox, switch or aria state. The same run found one
visible ``widget_menu_ORDERS`` but no ``widget(New)__container`` around it.
"""
from __future__ import annotations

import json
import re

import pytest

from src.prop.platform.dxtrade import INFO_PROBE_ORDERS_DUMP_JS, ONE_CLICK_JS, DXtradeAdapter

from tests.test_prop_instrument_info_probe import browser, page_html, run  # noqa: F401  (fixture reuse)


def toggle_row(value="false", test_id="one_click_trading", extra="", knob=None, toggle_style=""):
    """The measured row: toggle div + knob div + label div, as siblings. The
    knob sits at left:2px when off (MEASURED) and, by construction here, at
    the right end when on (ON is UNMEASURED on the live terminal)."""
    knob = knob or ("left" if value == "false" else "right")
    kx = {"left": "2px", "right": "12px", "none": None}[knob]
    knob_div = (f'<div class="sc-ikngxL fUCwLL" style="position:absolute;left:{kx};top:2px;'
                f'width:12px;height:12px"></div>') if kx else ""
    return (f'<div class="sc-geoRQH cnXJbC"><div class="sc-jlHfjz brrBxs" style="position:relative;height:17px">'
            f'<div class="sc-gFtjaa gXkbVK" data-value="{value}" data-test-id="{test_id}" '
            f'style="position:absolute;left:0;top:0;width:26px;height:16px;{toggle_style}"></div>'
            f'{knob_div}'
            f'<div class="sc-jwaPLR gXSbnX" style="margin-left:30px">One-click trading</div>{extra}</div></div>')


def read(browser, body):  # noqa: F811
    p = browser.new_page()
    p.set_content(f"<html><body>{body}</body></html>")
    got = p.evaluate(ONE_CLICK_JS)
    p.close()
    return got


@pytest.mark.parametrize("value,state", [("false", "off"), ("true", "on")])
def test_the_measured_toggle_reads_from_its_data_value(browser, value, state):  # noqa: F811
    got = read(browser, toggle_row(value))
    assert got["state"] == state and got["via"] == "data-value+knob" and got["n_toggles"] == 1


@pytest.mark.parametrize("body,why", [
    (toggle_row("off"), "a data-value other than exactly true/false"),
    (toggle_row("") , "an empty data-value"),
    (toggle_row("false") + toggle_row("false").replace("One-click trading", "Other"), "two toggles"),
    ('<div><div><div><div class="far"><div data-test-id="one_click_trading" data-value="false" '
     'style="width:26px;height:16px"></div></div></div></div></div>'
     '<div><div><div><div>One-click trading</div></div></div></div>', "a toggle outside the label's 3 ancestors"),
    (toggle_row("false", extra='<span role="switch" aria-checked="true">x</span>'), "a conflicting aria state"),
    ('<div><span>One-click trading</span></div>', "no toggle at all (the pre-reader live reading)"),
])
def test_anything_but_one_clean_toggle_reads_unknown(browser, body, why):  # noqa: F811
    assert read(browser, body)["state"] == "unknown", why


def test_a_hidden_toggle_is_not_read(browser):  # noqa: F811
    got = read(browser, toggle_row("false", toggle_style="display:none"))
    assert got["state"] == "unknown" and got["n_toggles"] == 1 and "not really visible" in got["why"]


def test_an_agreeing_aria_state_still_reads(browser):  # noqa: F811
    got = read(browser, toggle_row("false", extra='<span role="switch" aria-checked="false">x</span>'))
    assert got["state"] == "off" and got["via"] == "data-value+knob+aria"


# ── manager review of #14723: no false 'off' ───────────────────────────


def test_a_hidden_true_duplicate_beside_a_visible_false_reads_unknown(browser):  # noqa: F811
    hidden_true = ('<div data-test-id="one_click_trading" data-value="true" style="display:none"></div>')
    got = read(browser, toggle_row("false") + hidden_true)
    assert got["state"] == "unknown" and got["n_toggles"] == 2 and "need exactly 1" in got["why"]


@pytest.mark.parametrize("style", ["opacity:0", "visibility:hidden"])
def test_a_toggle_that_has_a_size_but_is_not_really_visible_reads_unknown(browser, style):  # noqa: F811
    got = read(browser, toggle_row("false", toggle_style=style))
    assert got["state"] == "unknown" and "not really visible" in got["why"]


def test_an_invisible_ancestor_also_hides_the_toggle(browser):  # noqa: F811
    got = read(browser, f'<div style="opacity:0">{toggle_row("false")}</div>')
    assert got["state"] == "unknown"


@pytest.mark.parametrize("value,knob", [("false", "right"), ("true", "left"), ("false", "none"), ("true", "none")])
def test_the_knob_must_agree_with_data_value(browser, value, knob):  # noqa: F811
    # Only OFF has ever been observed (#14714); an inverted attribute is not
    # ruled out, so a disagreeing or missing knob reads unknown.
    got = read(browser, toggle_row(value, knob=knob))
    assert got["state"] == "unknown", got
    assert ("disagrees" in got["why"]) if knob != "none" else ("0 knob candidates" in got["why"])


def test_a_failing_toggle_is_never_outvoted_by_an_aria_state(browser):  # noqa: F811
    got = read(browser, toggle_row("false", knob="right", extra='<span role="switch" aria-checked="false">x</span>'))
    assert got["state"] == "unknown" and "disagrees" in got["why"]


# ── the info probe now passes its one-click gate on the measured shape ─────


def measured_page(**kw):
    # page_html renders the MEASURED toggle itself since the 2nd live dry run.
    return page_html(**kw)


def test_the_info_probe_dry_run_passes_the_one_click_gate_on_the_measured_toggle(browser):  # noqa: F811
    html = measured_page()
    assert 'data-test-id="one_click_trading"' in html and 'type="checkbox"' not in html
    got, st = run(browser, html, click=False)
    assert got["one_click"]["state"] == "off" and got["one_click"]["via"] == "data-value+knob"
    assert got["refused"] is None and "one_click_dump" not in got and st["clicks"] == []


def test_the_measured_toggle_reading_on_is_one_alert_never_a_refusal(browser):  # noqa: F811
    # Operator directive 2026-10-01 (manager comment 5930025023 on #14947).
    html = measured_page(one_click="checked")
    assert 'data-value="true"' in html
    got, st = run(browser, html, click=False)
    assert got["one_click"]["state"] == "on" and got["refused"] is None
    assert sum("one-click trading reads ON" in a for a in got["alerts"]) == 1 and st["clicks"] == []


# ── the Orders-widget ancestor dump (read-only measurement) ────────────────


def test_orders_dump_is_attached_only_when_orders_could_not_be_read(browser):  # noqa: F811
    got, _ = run(browser, measured_page(), click=False)
    assert got["working_orders"]["found"] is True and "orders_dump" not in got
    got, st = run(browser, measured_page(orders_hidden=True), click=False)
    assert got["working_orders"]["found"] is False and st["clicks"] == []
    d = got["orders_dump"]
    assert d["n_menus"] == 1 and d["menus"][0]["menu"] == "widget_menu_ORDERS" and d["menus"][0]["visible"] is False
    lvl1 = d["menus"][0]["chain"][0]
    assert lvl1["visible"] is False and lvl1["n_tables"] == 1
    assert lvl1["symbol_header"] and lvl1["order_header"] and not lvl1["history_header"]
    assert "widgetNew__container" in lvl1["cls"]


def test_orders_dump_masks_ids_and_prints_no_cell_text(browser):  # noqa: F811
    html = measured_page(orders_hidden=True,
                         order_rows="<tr><td>SOLUSD</td><td>Buy</td><td>Limit</td><td>77123456</td></tr>")
    html = html.replace('class="widget__container___Or1', 'data-acct="x" class="acct-12345678 widget__container___Or1')
    got, _ = run(browser, html, click=False)
    dump = json.dumps(got["orders_dump"])
    assert "12345678" not in dump and "77123456" not in dump and "SOLUSD" not in dump and "Limit" not in dump
    assert "acct-########" in dump


def test_orders_dump_js_clicks_and_tags_nothing(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(measured_page())
    before = p.evaluate("document.body.innerHTML")
    p.evaluate(INFO_PROBE_ORDERS_DUMP_JS)
    assert p.evaluate("document.body.innerHTML") == before and p.evaluate("window.__clicks") == []
    p.close()


def test_other_order_paths_still_only_record_one_click(browser):  # noqa: F811
    # ORDER ENTRY rule 1 (operator 2026-09-28): one-click is a DIAGNOSTIC for
    # every order path; only the info probe gates on it. The reader change
    # alters the recorded reading, not any other path's behavior.
    p = browser.new_page()
    p.set_content(f"<html><body>{toggle_row('true')}</body></html>")
    assert DXtradeAdapter(timeout_ms=1_000).read_one_click(p)["state"] == "on"
    p.close()


# ── 2nd live dry run (issue #14754): Orders container at ancestor level 10 ─


def test_the_orders_container_ten_levels_up_is_found(browser):  # noqa: F811
    # MEASURED: widget_menu_ORDERS sits 9 wrappers below its visible
    # widgetNew__container (level 10); the original 8-level walk missed it.
    got, st = run(browser, page_html(orders_depth=10), click=False)
    assert got["working_orders"]["found"] is True and got["working_orders"]["n_rows"] == 0
    assert got["refused"] is None and "orders_dump" not in got and st["clicks"] == []


def test_a_container_beyond_twelve_levels_is_still_could_not_look(browser):  # noqa: F811
    got, _ = run(browser, page_html(orders_depth=14), click=False)
    assert got["working_orders"]["found"] is False and "working Orders not readable" in got["refused"]


def test_a_working_order_ten_levels_deep_still_refuses(browser):  # noqa: F811
    got, st = run(browser, page_html(orders_depth=10,
                                     order_rows="<tr><td>SOLUSD</td><td>Buy</td><td>Limit</td><td>77</td></tr>"),
                  click=False)
    assert "1 working order" in got["refused"] and st["clicks"] == []


# ── manager notes on #14723 (a), (b) ───────────────────────────────────────


def test_two_one_click_labels_read_unknown(browser):  # noqa: F811
    got = read(browser, toggle_row("false") + "<div>One-click trading</div>")
    assert got["state"] == "unknown" and "2 one-click labels" in got["why"]


def test_the_checkbox_fallback_ignores_hidden_controls(browser):  # noqa: F811
    hidden = '<label><input type="checkbox" style="display:none"><span>One-click trading</span></label>'
    assert read(browser, hidden)["state"] == "unknown"
    shown_ = '<label><input type="checkbox"><span>One-click trading</span></label>'
    got = read(browser, shown_)
    assert got["state"] == "off" and got["via"] == "aria"
    two = '<label><input type="checkbox"><input type="checkbox"><span>One-click trading</span></label>'
    assert read(browser, two)["state"] == "unknown"


def test_the_info_probe_gate_accepts_only_the_measured_toggle(browser):  # noqa: F811
    from src.prop.platform.dxtrade import info_probe_one_click_off
    assert info_probe_one_click_off({"state": "off", "via": "data-value+knob"})
    assert info_probe_one_click_off({"state": "off", "via": "data-value+knob+aria"})
    assert not info_probe_one_click_off({"state": "off", "via": "aria"})
    assert not info_probe_one_click_off({"state": "on", "via": "data-value+knob"})
    assert not info_probe_one_click_off(None)
    # a checkbox-only terminal reads 'off' via aria: recorded, never a refusal
    # (operator directive 2026-10-01, manager comment 5930025023 on #14947)
    html = re.sub(r'<div style="position:relative;height:17px">.*?One-click trading</div></div>',
                  '<label><input type="checkbox"><span>One-click trading</span></label>', page_html(), flags=re.S)
    assert 'data-test-id="one_click_trading"' not in html
    got, st = run(browser, html, click=False)
    assert got["one_click"]["state"] == "off" and got["one_click"]["via"] == "aria"
    assert got["refused"] is None and not any("one-click" in a for a in got["alerts"]) and st["clicks"] == []


# ── manager review of #14764: a hidden Orders grid is never "no orders" ────

ORDER = "<tr><td>SOLUSD</td><td>Buy</td><td>Limit</td><td>77</td></tr>"
MENU = '<button data-test-id="widget_menu_ORDERS">Orders</button>'


def orders_table_styled(style, **kw):
    html = page_html(**kw)
    anchor = MENU + "\n  <table>"
    assert html.count(anchor) == 1
    return html.replace(anchor, MENU + f'\n  <table style="{style}">')


@pytest.mark.parametrize("style", ["display:none", "visibility:hidden", "opacity:0"])
def test_s3b_a_hidden_orders_table_with_a_working_order_is_could_not_look(browser, style):  # noqa: F811
    # Reviewer fixture S3b, rebuilt: a VISIBLE container holding a hidden
    # Orders table that has one working order. It used to read found:true,
    # n_rows:0 -- the flat guard passed on the orders half.
    got, st = run(browser, orders_table_styled(style, order_rows=ORDER), click=False)
    assert got["working_orders"]["found"] is False, got["working_orders"]
    assert "working Orders not readable" in got["refused"] and st["clicks"] == []


def test_a_visible_header_with_a_hidden_order_row_is_could_not_look(browser):  # noqa: F811
    hidden_row = ORDER.replace("<tr>", '<tr style="display:none">')
    got, st = run(browser, page_html(order_rows=hidden_row), click=False)
    assert got["working_orders"]["found"] is False and got["working_orders"]["n_rows_hidden"] == 1
    assert "working Orders not readable" in got["refused"] and st["clicks"] == []


def test_a_hidden_duplicate_orders_menu_is_refused(browser):  # noqa: F811
    html = page_html().replace(MENU, MENU + '<button data-test-id="widget_menu_ORDERS" style="display:none"></button>')
    got, _ = run(browser, html, click=False)
    assert got["working_orders"]["found"] is False and "2 widget_menu_ORDERS" in got["working_orders"]["why"]


def test_a_container_holding_another_widget_menu_is_refused(browser):  # noqa: F811
    html = page_html().replace(MENU, MENU + '<button data-test-id="widget_menu_POSITIONS">Positions</button>')
    got, _ = run(browser, html, click=False)
    assert got["working_orders"]["found"] is False and "other widget_menu_" in got["working_orders"]["why"]


def test_the_visible_empty_orders_table_still_reads_empty(browser):  # noqa: F811
    for depth in (1, 10):
        got, _ = run(browser, page_html(orders_depth=depth), click=False)
        assert got["working_orders"] == {"found": True, "n_rows": 0, "headers": got["working_orders"]["headers"]}
        assert got["refused"] is None
