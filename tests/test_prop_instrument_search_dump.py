"""PROP-ETH-DOM (2026-09-30, pipeline PI-20260929-AQRK6CL1-0014): the
read-only dump that MEASURES where the DXtrade symbol search/add control is,
after live run #14457 found n_candidate_inputs 0 inside the watchlist panel.

The browser tests run INSTRUMENT_SEARCH_DUMP_JS in real Chromium against a
fixture page and pin the safety contract: it lists the controls with their
attributes and ancestor chain, and it never reads a value, never types,
clicks or tags anything, and never lists anything inside the order ticket.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.prop.platform.dxtrade import INSTRUMENT_SEARCH_DUMP_JS, DXtradeAdapter

REPO = Path(__file__).resolve().parents[1]


def test_tick_mode_resolves_and_is_read_only_whatever_the_env():
    from scripts.prop.prop_executor_tick import resolve_mode
    ns = SimpleNamespace(probe_ticket="", instrument_probe="", instrument_search_dump=True,
                         dry_run=False, watched_click=False)
    assert resolve_mode(ns, {}) == "instrument_search_dump"
    assert resolve_mode(ns, {"PROP_EXECUTOR_MODE": "live"}) == "instrument_search_dump"
    # Documented exception (resolve_mode docstring): probes run under off too.
    assert resolve_mode(ns, {"PROP_EXECUTOR_MODE": "off"}) == "instrument_search_dump"


def test_action_and_workflow_accept_the_mode():
    action = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert "instrument-search-dump) EARGS+=(--instrument-search-dump) ;;" in action
    assert re.search(r"for m in [^;]*\binstrument-search-dump\b", action)
    wf = (REPO / ".github" / "workflows" / "system-actions.yml").read_text()
    # both alternations of the apply allowlist regex
    assert wf.count("|instrument-search-dump|") == 2


def test_tick_prints_rows_farthest_first_and_the_summary_last(capsys):
    from scripts.prop.prop_executor_tick import emit_search_dump
    emit_search_dump({"frames": [{"frame": 0, "found": True, "n_rows": 2,
                                  "rows": [{"kind": "field", "placeholder": "near"},
                                           {"kind": "field", "placeholder": "far"}]}]}, "s3cret")
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    assert [ln.get("search_dump_row", {}).get("placeholder") for ln in lines[:2]] == ["far", "near"]
    assert lines[1]["search_dump_row"]["rank"] == 0
    assert "rows" not in lines[2]["instrument_search_dump"] and lines[2]["instrument_search_dump"]["n_rows"] == 2


def test_js_never_reads_a_value_or_acts():
    # Static backstop for the contract the browser test checks dynamically.
    js = INSTRUMENT_SEARCH_DUMP_JS
    for forbidden in (".value", ".click(", ".focus(", "setAttribute", "dispatchEvent",
                      "scrollIntoView", "innerHTML =", ".fill("):
        assert forbidden not in js, forbidden


FIXTURE = """<html><body>
<div class="app">
  <div class="wl-toolbar watchlist-toolbar-container-outer" title="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9">
    <input class="padding address" placeholder="Search instruments" data-test-id="wl_search" value="ALREADYTYPED">
    <button class="icon-btn" title="Add"><svg><use href="#icon-plus"></use></svg></button>
    <div class="padding address">not a control</div>
  </div>
  <div class="wl-panel">
    <div class="wl-head"><div role="combobox" aria-label="Watchlist">My list 1234567</div></div>
    <table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>
      <tbody><tr class="instrument" data-row-id="1"><td>ETHUSD</td><td>2600.1</td><td>2600.2</td></tr></tbody></table>
    <input type="text" class="filter" value="SECRETVALUE">
    <input type="text" style="display:none" placeholder="hidden search">
  </div>
</div>
<div class="ticket">
  <button data-test-id="BUY">Buy</button><button data-test-id="SELL">Sell</button>
  <input data-test-id="symbol_input" placeholder="Search symbol" value="SOLUSD">
  <input aria-label="Quantity" value="0.01">
</div>
<div class="side"><input type="password" placeholder="search pass" value="hunter2">
  <input aria-label="user email" placeholder="search" value="me@example.com">
  <div contenteditable="true" class="note-search">typed note text</div></div>
<script>document.addEventListener('click', () => { window.__clicks = (window.__clicks || 0) + 1; }, true);
document.addEventListener('focusin', () => { window.__focus = (window.__focus || 0) + 1; }, true);</script>
</body></html>"""


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


@pytest.fixture
def dump(browser):
    p = browser.new_page()
    p.set_content(FIXTURE)
    before = p.evaluate("document.documentElement.outerHTML")
    got = DXtradeAdapter(timeout_ms=3_000).instrument_search_dump(p)
    after = p.evaluate("document.documentElement.outerHTML")
    yield p, got, before, after
    p.close()


def test_dump_measures_the_panel_and_lists_nearest_first(dump):
    _, got, _, _ = dump
    fr = got["frames"][0]
    assert fr["found"] is True and fr["panel_why"] is None
    assert "<WATCHLIST_PANEL>" in fr["panel"]["desc"] and fr["panel"]["headers"] == ["symbol", "bid", "ask"]
    rows = fr["rows"]
    # in-panel controls first, then the toolbar one level above the panel
    assert [r["in_wl_panel"] for r in rows[:2]] == [True, True]
    search = next(r for r in rows if r["data_test_id"] == "wl_search")
    assert search["in_wl_panel"] is False and search["wl_level"] == 1
    assert search["placeholder"] == "Search instruments" and search["kind"] == "field"
    assert any("wl-toolbar" in c for c in search["chain"])
    plus = next(r for r in rows if r["kind"] == "search_like_button")
    assert plus["title"] == "Add" and "icon-plus" in plus["icon"]
    assert fr["hidden_in_panel"] == 1
    # an editable combobox reports its length, never its text
    combo = next(r for r in rows if r["kind"] == "role_combobox")
    assert combo["text"] is None and combo["text_len"] > 0


def test_dump_never_reads_values_or_the_ticket_and_changes_nothing(dump):
    p, got, before, after = dump
    blob = json.dumps(got)
    for secret in ("ALREADYTYPED", "SECRETVALUE", "SOLUSD", "0.01", "hunter2", "me@example.com",
                   "typed note text", "1234567"):
        assert secret not in blob, secret
    fr = got["frames"][0]
    assert fr["excluded_order_panel"] == 2          # symbol_input + Quantity, counted, never listed
    assert fr["excluded_personal"] == 2             # password + user email
    assert not any(r["data_test_id"] == "symbol_input" for r in fr["rows"])
    # "padding address" is not "add": the plain div is not listed
    assert not any(r["tag"] == "div" and r["cls"] == "padding address" for r in fr["rows"])
    # a contenteditable reports its length, never its text
    ce = next(r for r in fr["rows"] if r["kind"] == "contenteditable")
    assert ce["text"] is None and ce["text_len"] == len("typed note text")
    # nothing typed, clicked, focused or tagged
    assert before == after
    assert p.evaluate("window.__clicks") is None and p.evaluate("window.__focus") is None
    assert p.evaluate("document.querySelector('[data-test-id=wl_search]').value") == "ALREADYTYPED"


def test_dump_survives_the_public_log_redactor_intact(dump):
    # redact_text replaces any 24+ [A-Za-z0-9_-.=+/] run with "<token>"; a
    # long CSS class or a dotted ancestor descriptor must reach the log as a
    # readable prefix, not be erased by it.
    from src.prop.platform.dxtrade import redact_text
    _, got, _, _ = dump
    blob = json.dumps(got)
    assert redact_text(blob) == blob
    search = next(r for r in got["frames"][0]["rows"] if r["data_test_id"] == "wl_search")
    assert any(".watchlist-toolba\u2026" in c or ".watchlist-toolba…" in c for c in search["chain"])
    # a token-looking run is dropped whole, never published as a prefix
    assert "eyJhbGciOiJIUzI1" not in blob


def test_mask_drops_token_like_runs_and_keeps_readable_class_prefixes(browser):
    p = browser.new_page()
    p.set_content("<html><body><div class='toolbar'>"
                  "<input placeholder='Search' class='watchlist-search-input-container-x' "
                  "title='a1b2c3d4e5f6a7b8c9d0e1f2a3b4' aria-label='AbCdEfGhIjKlMnOpQrStUvWxYz'>"
                  "</div></body></html>")
    row = DXtradeAdapter(timeout_ms=3_000).instrument_search_dump(p)["frames"][0]["rows"][0]
    assert row["cls"] == "watchlist-search\u2026"
    assert row["title"] == "<tok>" and row["aria_label"] == "<tok>"
    p.close()


def test_dump_still_runs_document_wide_when_the_panel_does_not_resolve(browser):
    p = browser.new_page()
    p.set_content("<html><body><input placeholder='Find symbol'></body></html>")
    fr = DXtradeAdapter(timeout_ms=3_000).instrument_search_dump(p)["frames"][0]
    assert fr["panel"] is None and "0 tables" in fr["panel_why"]
    assert fr["rows"][0]["placeholder"] == "Find symbol" and fr["rows"][0]["wl_level"] is None
    p.close()


# ── STEP 3: the locator, re-anchored on the MEASURED widget (issue #14551) ──
#
# Shape copied from the live dump: the watchlist widget
# (``widget__container... widgetNew__container``) holds a header toolbar with
# ``form > ... > input[placeholder="Symbol..."][data-test-id^=watchlist_public]``
# inside a ``multiasset-suggest`` control, and, 6 levels down, the grid panel
# ``div.grid.grid-watchlist`` with the Symbol/Bid/Ask header table.

WIDGET = """<html><body><main class="main"><section class="app">
<div class="widget__container___Ab1 widgetNew__container">
  <div class="widget-header__container"><div class="toolbar__item">
    <div class="multiasset-suggest"><div class="control control-textInput"><div class="control--wrap"><div>
      <form onsubmit="window.__submit=1;return false"><input type="text" placeholder="Symbol..."
        data-test-id="watchlist_public_search_input" class="sc-ixGGxD"
        oninput="window.__typed=(window.__typed||'')+this.value"></form>
    </div></div></div></div>
  </div></div>
  <div class="droppable-body"><div class="widget__body"><div class="widget__body"><div><div>
    <div class="grid grid-watchlist">
      <div class="grid--head"><table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead></table></div>
      <div class="grid--body"><table><tbody><tr class="instrument" data-row-id="1">
        <td>ETHUSD</td><td>2600.1</td><td>2600.2</td></tr></tbody></table></div>
    </div>
  </div></div></div></div></div>
</div>
%EXTRA%
</section></main>
<script>document.addEventListener('click', () => { window.__clicks = (window.__clicks || 0) + 1; }, true);</script>
</body></html>"""

TICKET = """<div class="ticket"><button data-test-id="BUY">Buy</button><button data-test-id="SELL">Sell</button>
<input data-test-id="symbol_input" placeholder="Symbol..."></div>"""


def _page(browser, extra="", html=None):
    p = browser.new_page()
    p.set_content(html or WIDGET.replace("%EXTRA%", extra))
    return p


def test_locator_finds_the_measured_widget_header_input(browser):
    from src.prop.platform.dxtrade import FIND_INSTRUMENT_SEARCH_JS, INSTRUMENT_SEARCH_MATCH
    p = _page(browser, TICKET)       # an order ticket elsewhere on the page is excluded
    got = p.evaluate(FIND_INSTRUMENT_SEARCH_JS, [list(INSTRUMENT_SEARCH_MATCH)])
    assert got == {"found": True, "via": "placeholder+tid"}
    assert p.evaluate("document.querySelector('[data-metis-search-hit]').dataset.testId") \
        == "watchlist_public_search_input"
    p.close()


def test_probe_types_resets_and_never_clicks_or_submits(browser):
    p = _page(browser)
    got = DXtradeAdapter(timeout_ms=3_000).probe_instrument_details(p, "BTCUSD")
    assert got["searched"] is True and got["readback_matches"] is True and got["reset"] is True
    assert got["via"] == "placeholder+tid" and got["blurred"] is True
    assert p.evaluate("window.__clicks") is None and p.evaluate("window.__submit") is None
    assert p.evaluate("document.querySelectorAll('[data-metis-search-hit]').length") == 0
    p.close()


def test_locator_refuses_without_a_widget_container(browser):
    from src.prop.platform.dxtrade import FIND_INSTRUMENT_SEARCH_JS, INSTRUMENT_SEARCH_MATCH
    html = WIDGET.replace("%EXTRA%", "").replace("widget__container___Ab1 widgetNew__container", "plain")
    p = _page(browser, html=html)
    got = p.evaluate(FIND_INSTRUMENT_SEARCH_JS, [list(INSTRUMENT_SEARCH_MATCH)])
    assert got["found"] is False and "widget__container" in got["why"]
    p.close()


def test_locator_refuses_when_the_widget_holds_an_order_panel(browser):
    from src.prop.platform.dxtrade import FIND_INSTRUMENT_SEARCH_JS, INSTRUMENT_SEARCH_MATCH
    html = WIDGET.replace("%EXTRA%", "").replace('<div class="droppable-body">',
                                                 TICKET + '<div class="droppable-body">')
    p = _page(browser, html=html)
    got = p.evaluate(FIND_INSTRUMENT_SEARCH_JS, [list(INSTRUMENT_SEARCH_MATCH)])
    assert got["found"] is False and "order" in got["why"]
    assert p.evaluate("document.querySelectorAll('[data-metis-search-hit]').length") == 0
    p.close()


def test_locator_refuses_two_matching_inputs(browser):
    from src.prop.platform.dxtrade import FIND_INSTRUMENT_SEARCH_JS, INSTRUMENT_SEARCH_MATCH
    html = WIDGET.replace("%EXTRA%", "").replace(
        '<div class="droppable-body">', '<input placeholder="Symbol..." data-test-id="watchlist_public_2"><div class="droppable-body">')
    p = _page(browser, html=html)
    got = p.evaluate(FIND_INSTRUMENT_SEARCH_JS, [list(INSTRUMENT_SEARCH_MATCH)])
    assert got["found"] is False and "need exactly 1" in got["why"]
    p.close()


# ── manager review of #14563: both attributes on one input, no fallback ──


@pytest.mark.parametrize("inp", [
    '<input type="text" placeholder="Symbol...">',                          # placeholder only
    '<input type="text" data-test-id="watchlist_public_x">',                # test-id only
    '<input type="search">',                                                # the removed type=search fallback
    '<input placeholder="Symbol..." data-test-id="other_x">',               # right placeholder, wrong test-id
])
def test_locator_refuses_anything_short_of_both_measured_attributes(browser, inp):
    from src.prop.platform.dxtrade import FIND_INSTRUMENT_SEARCH_JS, INSTRUMENT_SEARCH_MATCH
    html = WIDGET.replace("%EXTRA%", "").replace(
        '<form onsubmit="window.__submit=1;return false"><input type="text" placeholder="Symbol..."\n'
        '        data-test-id="watchlist_public_search_input" class="sc-ixGGxD"\n'
        '        oninput="window.__typed=(window.__typed||\'\')+this.value"></form>', "<form>" + inp + "</form>")
    assert inp in html                                   # the fixture really swapped the input
    p = _page(browser, html=html)
    got = p.evaluate(FIND_INSTRUMENT_SEARCH_JS, [list(INSTRUMENT_SEARCH_MATCH)])
    assert got["found"] is False and got["n_candidate_inputs"] == 1
    assert "placeholder 'symbol...' AND a watchlist_public* data-test-id" in got["why"]
    assert p.evaluate("document.querySelectorAll('[data-metis-search-hit]').length") == 0
    p.close()


def test_watchlist_symbols_read_and_diff(browser):
    from src.prop.platform.dxtrade import watchlist_diff
    p = _page(browser)
    a = DXtradeAdapter(timeout_ms=3_000)
    before = a.watchlist_symbols(p)
    assert before == {"readable": True, "symbols": ["ETHUSD"]}
    a.probe_instrument_details(p, "BTCUSD")
    after = a.watchlist_symbols(p)
    assert watchlist_diff(before, after) == {"changed": False, "n_before": 1, "n_after": 1,
                                             "added": [], "removed": []}
    # a symbol that DID persist would be reported, not hidden
    assert watchlist_diff(before, {"readable": True, "symbols": ["BTCUSD", "ETHUSD"]})["added"] == ["BTCUSD"]
    # "could not look" is never folded into "no change"
    assert watchlist_diff(before, {"readable": False, "why": "0 Symbol/Bid/Ask tables"})["changed"] is None
    p.close()
