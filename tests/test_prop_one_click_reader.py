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
    html = page_html(**kw)
    return re.sub(r"<label><input type=\"checkbox\" ?><span>One-click trading</span></label>",
                  toggle_row("false"), html)


def test_the_info_probe_dry_run_passes_the_one_click_gate_on_the_measured_toggle(browser):  # noqa: F811
    html = measured_page()
    assert 'data-test-id="one_click_trading"' in html and 'type="checkbox"' not in html
    got, st = run(browser, html, click=False)
    assert got["one_click"]["state"] == "off" and got["one_click"]["via"] == "data-value+knob"
    assert got["refused"] is None and "one_click_dump" not in got and st["clicks"] == []


def test_the_measured_toggle_reading_on_still_refuses(browser):  # noqa: F811
    html = measured_page().replace(toggle_row("false"), toggle_row("true"))
    assert 'data-value="true"' in html
    got, st = run(browser, html, click=False)
    assert "does not read OFF (reads 'on')" in got["refused"] and st["clicks"] == []


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
