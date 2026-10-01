"""PROP-ETH-DOM (2026-09-30, manager 19:40Z): the PER-TICKET SYMBOL SWITCH.

The order form is the sidebar ticket linked to the terminal's linked symbol,
so an ETHUSD ticket on a SOLUSD-linked terminal was refused ("the open form
does not name ETHUSD") and could never trade. ``select_linked_symbol`` links
the ticket's symbol first with ONE verified single click on its watchlist
Symbol cell (MEASURED live: #14831 BTCUSD, #14870 ETHUSD), and
``place_bracket`` refuses on any mismatch before the form is touched.
Exercised in real Chromium on the info probe's MEASURED fixture page.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.prop.platform.base import BracketSpec
from src.prop.platform.dxtrade import DXtradeAdapter

from tests.test_prop_instrument_info_probe import browser, never_traded, page_html  # noqa: F401  (fixture reuse)

DIALOG = '<div role="dialog" style="width:200px;height:100px">Are you sure?</div>'


def state(p):
    return p.evaluate("({clicks: window.__clicks, trade: window.__trade || 0, ticket: window.__ticket || 0,"
                      " linked: document.querySelector('[data-test-id=symbol_input]').value,"
                      " tags: document.querySelectorAll('[data-metis-row-cell],[data-metis-info-btn],"
                      "[data-metis-sym-input]').length})")


def switch(browser, html, target):  # noqa: F811
    p = browser.new_page()
    p.set_content(html)
    got = DXtradeAdapter(timeout_ms=3_000).select_linked_symbol(p, target, settle_ms=50)
    st = state(p)
    p.close()
    return got, st


def test_switches_the_linked_symbol_with_one_verified_symbol_cell_click(browser):  # noqa: F811
    got, st = switch(browser, page_html(), "ETHUSD")
    assert got == {"target": "ETHUSD", "ok": True, "clicked": True, "before": "SOLUSD", "after": "ETHUSD",
                   "one_click": {"state": "off", "via": "data-value+knob"}, "attempts": 1, "route": "watchlist",
                   "ready": {"waited_ms": 0, "polls": 1, "watchlist_n": 4}, "verify_waited_ms": 50}
    assert st["clicks"] == ["sym"] and st["linked"] == "ETHUSD" and st["tags"] == 0
    never_traded(st)                                   # no Bid/Ask, no double-click ticket


def test_an_already_linked_symbol_clicks_nothing(browser):  # noqa: F811
    got, st = switch(browser, page_html(), "SOLUSD")
    assert got["ok"] is True and got["clicked"] is False and st["clicks"] == []


def test_an_open_dialog_refuses_before_any_click(browser):  # noqa: F811
    got, st = switch(browser, page_html(outside_table=DIALOG), "ETHUSD")
    assert got["ok"] is False and "dialog(s) open" in got["why"] and st["clicks"] == []


def test_a_link_that_does_not_follow_the_click_refuses(browser):  # noqa: F811
    got, st = switch(browser, page_html(link_breaks_for="ETHUSD"), "ETHUSD")
    assert got["ok"] is False and got["clicked"] is True
    assert "linked symbol reads 'SOLUSD' after selecting ETHUSD" in got["why"]
    never_traded(st)


def test_a_control_on_hover_is_never_clicked(browser):  # noqa: F811
    got, st = switch(browser, page_html(hover_button_for="ETHUSD"), "ETHUSD")
    assert got["ok"] is False and got["clicked"] is False and "on hover" in got["why"]
    assert st["clicks"] == []
    never_traded(st)


def test_a_symbol_not_in_the_watchlist_refuses(browser):  # noqa: F811
    got, st = switch(browser, page_html(), "BNBUSD")
    assert got["ok"] is False and "no single clean watchlist Symbol cell" in got["why"] and st["clicks"] == []


class _Stub(DXtradeAdapter):
    """place_bracket's switch decision, with the form reads stubbed."""

    def __init__(self, first_form, link, after_form=None):
        super().__init__(timeout_ms=1_000)
        self.first_form, self.link, self.after_form, self.calls = first_form, link, after_form, []

    def open_order_ticket(self, page, venue_symbol):
        self.calls.append("open")
        return {"opened": True, "via": "already_open", "form": dict(self.first_form)}

    def select_linked_symbol(self, page, venue_symbol, *, settle_ms=1_500):
        self.calls.append(("switch", venue_symbol))
        return dict(self.link)

    def _find_form(self, page):
        self.calls.append("reread")
        return dict(self.after_form or {})

    def close_order_ticket(self, page):
        self.calls.append("close")
        return True


SPEC = BracketSpec(ticket_id="t1", venue_symbol="ETHUSD", side="long", quantity=0.01,
                   stop_loss=1900.0, take_profit=2100.0, order_type="limit", limit_price=2000.0)


def test_place_bracket_never_switches_when_the_form_already_names_the_symbol():
    ad = _Stub({"found": True, "symbol_value": "ETHUSD", "fields": {}}, {"ok": True})
    att = ad.place_bracket(object(), SPEC, arm=False)
    assert not any(isinstance(c, tuple) for c in ad.calls)          # no switch
    assert "symbol switch" not in (att.detail or "")


def test_place_bracket_refuses_and_closes_the_form_when_the_switch_fails():
    ad = _Stub({"found": True, "symbol_value": "SOLUSD", "fields": {}},
               {"ok": False, "why": "linked symbol reads 'SOLUSD' after selecting ETHUSD"})
    att = ad.place_bracket(object(), SPEC, arm=False)
    assert att.stage == "refused" and att.detail == "symbol switch: linked symbol reads 'SOLUSD' after selecting ETHUSD"
    assert ad.calls == ["open", ("switch", "ETHUSD"), "close"]


def test_place_bracket_still_refuses_when_the_reread_form_does_not_name_the_symbol():
    ad = _Stub({"found": True, "symbol_value": "SOLUSD", "fields": {}}, {"ok": True},
               after_form={"found": True, "symbol_value": "SOLUSD", "fields": {}})
    att = ad.place_bracket(object(), SPEC, arm=False)
    assert ad.calls[:3] == ["open", ("switch", "ETHUSD"), "reread"]
    assert att.stage == "refused" and "does not name ETHUSD" in att.detail


# ── the DRY mode: select, verify, restore, verify; no order form ─────────


def test_symbol_switch_dry_refuses_unless_one_click_reads_off(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html(one_click="checked"))
    got = DXtradeAdapter(timeout_ms=3_000).symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    st = state(p)
    p.close()
    assert "does not read OFF" in got["refused"] and st["clicks"] == []


def test_the_tick_decides_symbol_switch_dry_before_the_kill_switch():
    from scripts.prop.prop_executor_tick import resolve_mode
    base = dict(probe_ticket="", dry_run=False, watched_click=False, round_trip="", close_position="",
                instrument_probe="", instrument_search_dump=False, instrument_info_dry="",
                instrument_info_probe="", symbol_switch_dry="ETHUSD")
    assert resolve_mode(SimpleNamespace(**base), {"PROP_EXECUTOR_MODE": "off"}) == "symbol_switch_dry"


# ── independent review of #14885 (manager 20:17Z) ─────────────────────────


def test_review_the_switch_click_refuses_when_one_click_reads_on(browser):  # noqa: F811
    got, st = switch(browser, page_html(one_click="checked"), "ETHUSD")
    assert got["ok"] is False and got["clicked"] is False
    assert got["why"].startswith("symbol switch refused: one-click not confirmed OFF (reads 'on'")
    assert st["clicks"] == [] and st["linked"] == "SOLUSD"


def test_review_the_switch_click_refuses_when_one_click_is_unknown(browser):  # noqa: F811
    got, st = switch(browser, page_html(one_click_unreadable=True), "ETHUSD")
    assert got["ok"] is False and "one-click not confirmed OFF (reads 'unknown'" in got["why"]
    assert st["clicks"] == []


def test_review_one_click_off_lets_the_switch_click(browser):  # noqa: F811
    got, _ = switch(browser, page_html(), "ETHUSD")
    assert got["ok"] is True and got["one_click"]["state"] == "off"


def test_review_an_already_linked_symbol_reads_no_one_click_and_clicks_nothing(browser):  # noqa: F811
    class Counting(DXtradeAdapter):
        reads = 0

        def read_one_click(self, page):
            Counting.reads += 1
            return super().read_one_click(page)

    p = browser.new_page()
    p.set_content(page_html(one_click="checked"))              # even with one-click ON
    got = Counting(timeout_ms=3_000).select_linked_symbol(p, "SOLUSD", settle_ms=50)
    st = state(p)
    p.close()
    assert got["ok"] is True and got["clicked"] is False and Counting.reads == 0 and st["clicks"] == []


ORDER_ROW_OUTSIDE = ('<div class="widget__container___Or2 widgetNew__container">'
                     '<button data-test-id="widget_menu_POSITIONS">Positions</button><table><tbody>'
                     '<tr class="instrument" data-row-id="p1"><td class="sym">ETHUSD</td><td>Buy</td><td>1</td></tr>'
                     '</tbody></table></div>')


def test_review_a_positions_row_outside_the_watchlist_is_never_a_target(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html(outside_table=ORDER_ROW_OUTSIDE))
    from src.prop.platform.dxtrade import INFO_PROBE_RESOLVE_JS
    res = p.evaluate(INFO_PROBE_RESOLVE_JS, [["ETHUSD"]])
    p.close()
    assert res["ok"] is True and res["targets"]["ETHUSD"]["n_rows"] == 1 and res["targets"]["ETHUSD"]["clean"]
    got, st = switch(browser, page_html(outside_table=ORDER_ROW_OUTSIDE), "ETHUSD")
    assert got["ok"] is True and st["clicks"] == ["sym"]


def test_review_a_watchlist_scope_holding_the_orders_widget_refuses(browser):  # noqa: F811
    html = page_html().replace('<table><thead><tr><th>Symbol</th><th>Bid</th>',
                               '<button data-test-id="widget_menu_ORDERS">x</button>'
                               '<table><thead><tr><th>Symbol</th><th>Bid</th>', 1)
    got, st = switch(browser, html, "ETHUSD")
    assert got["ok"] is False and "Orders / Positions widget" in got["why"] and st["clicks"] == []


def test_review_a_failed_live_switch_that_clicked_restores_the_prior_symbol_once():
    calls = []

    class Restore(_Stub):
        def select_linked_symbol(self, page, venue_symbol, *, settle_ms=1_500):
            calls.append(venue_symbol)
            if venue_symbol == "ETHUSD":
                return {"ok": False, "clicked": True, "before": "SOLUSD", "why": "link did not follow"}
            return {"ok": False, "clicked": False, "why": "1 dialog(s) open"}

    ad = Restore({"found": True, "symbol_value": "SOLUSD", "fields": {}}, {})
    att = ad.place_bracket(object(), SPEC, arm=False)
    assert calls == ["ETHUSD", "SOLUSD"]                                  # exactly one restore attempt
    assert att.stage == "refused" and "RESTORE to SOLUSD FAILED: 1 dialog(s) open" in att.detail
    assert att.form["symbol_switch"]["restore"]["ok"] is False and ad.calls[-1] == "close"


def test_review_a_failed_switch_that_never_clicked_attempts_no_restore():
    calls = []

    class NoClick(_Stub):
        def select_linked_symbol(self, page, venue_symbol, *, settle_ms=1_500):
            calls.append(venue_symbol)
            return {"ok": False, "clicked": False, "before": "SOLUSD", "why": "symbol switch refused: one-click"}

    att = NoClick({"found": True, "symbol_value": "SOLUSD", "fields": {}}, {}).place_bracket(object(), SPEC, arm=False)
    assert calls == ["ETHUSD"] and "RESTORE" not in att.detail


def test_review_symbol_switch_dry_never_raises():
    class Boom:
        def evaluate(self, *a, **k):
            raise RuntimeError("page gone")

    class OffThenBoom(DXtradeAdapter):
        def read_one_click(self, page):
            return {"state": "off", "via": "data-value+knob"}

    got = OffThenBoom(timeout_ms=1_000).symbol_switch_dry(Boom(), "ETHUSD", settle_ms=1)
    assert got["alerts"] == ["symbol-switch-dry raised RuntimeError (code=switch_dry_exception)"]


POSITION_ROW = ('<div class="widget__container___Ps9 widgetNew__container">'
                '<button data-test-id="widget_menu_POSITIONS">Positions</button><table>'
                '<thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead><tbody></tbody></table><table><tbody>'
                '<tr class="instrument" data-row-id="pos1"><td class="sym">ETHUSD</td><td>0.37</td><td>-48.12</td></tr>'
                '</tbody></table></div>')


def test_review_nit_b_a_positions_row_never_reaches_quote_raw(browser):  # noqa: F811
    from src.prop.platform.dxtrade import WATCHLIST_QUOTE_RAW_JS
    # a same-shaped ETHUSD row in a Positions widget (qty / P&L), placed on the page
    html = page_html(outside_table=POSITION_ROW.replace('<thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead>', ''))
    p = browser.new_page()
    p.set_content(html)
    raw = p.evaluate(WATCHLIST_QUOTE_RAW_JS, ["ETHUSD"])
    got = DXtradeAdapter(timeout_ms=3_000).symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    p.close()
    assert raw == {"headers": ["symbol", "bid", "ask"], "rows": [["ETHUSD", "100.1", "100.2"]]}
    assert got["quote_raw"] == raw
    assert "0.37" not in str(got) and "-48.12" not in str(got)


def test_review_nit_b_quote_raw_keeps_only_symbol_bid_ask(browser):  # noqa: F811
    from src.prop.platform.dxtrade import WATCHLIST_QUOTE_RAW_JS
    html = page_html().replace('<th>Ask</th></tr>', '<th>Ask</th><th>Chg</th></tr>').replace(
        '100.2</button></td></tr>', '100.2</button></td><td>+1.5%</td></tr>')
    p = browser.new_page()
    p.set_content(html)
    raw = p.evaluate(WATCHLIST_QUOTE_RAW_JS, ["ETHUSD"])
    p.close()
    assert raw["rows"] == [["ETHUSD", "100.1", "100.2"]]


# ── link-state-dump (manager 2026-10-01 04:33Z, option (c)): READ-ONLY ──────


OVERLAY = ('<div class="ticket-overlay" style="position:fixed;left:0;top:0;width:100vw;height:100vh;'
           'background:rgba(0,0,0,0.01)"></div>')
TICKET = ('<div class="order-ticket"><span>ETHUSD</span><button data-test-id="BUY">Buy</button>'
          '<button data-test-id="SELL">Sell</button><input value="0.01">'
          '<button aria-label="Close">x</button><button title="acct 123456789">i</button></div>')


def dump(browser, html):  # noqa: F811
    p = browser.new_page()
    p.set_content(html)
    got = DXtradeAdapter(timeout_ms=3_000).link_state_dump(p)
    st = state(p)
    p.close()
    return got, st


def test_link_state_dump_reads_rows_hits_and_the_linked_input_without_clicking(browser):  # noqa: F811
    got, st = dump(browser, page_html())
    assert [r["sym"] for r in got["rows"]] == ["ETHUSD", "SOLUSD", "BTCUSD", "AVAXUSD"]
    assert all(r["hit_is_cell"] and r["hit_in_row"] for r in got["rows"])
    assert [i["value"] for i in got["symbol_inputs"]] == ["SOLUSD"] and got["dialogs"] == 0
    assert got["order_panels"] == []
    assert st["clicks"] == [] and st["tags"] == 0
    never_traded(st)


def test_link_state_dump_shows_an_overlay_covering_the_symbol_cells(browser):  # noqa: F811
    got, st = dump(browser, page_html(outside_table=OVERLAY))
    sol = next(r for r in got["rows"] if r["sym"] == "SOLUSD")
    assert sol["hit_is_cell"] is False and sol["hit"]["cls"] == ["ticket-overlay"]
    assert st["clicks"] == []


def test_link_state_dump_describes_the_sidebar_ticket_and_masks_long_digit_runs(browser):  # noqa: F811
    got, st = dump(browser, page_html(outside_table=TICKET))
    (panel,) = got["order_panels"]
    assert panel["names_symbols"] == ["ETHUSD"] and panel["n_close_like"] == 1
    flat = str(panel["buttons"])
    assert "123456789" not in flat and "#########" in flat
    assert st["clicks"] == []
    never_traded(st)


def test_the_tick_maps_link_state_dump_to_its_own_read_only_mode():
    import argparse
    from scripts.prop import prop_executor_tick as tick
    args = argparse.Namespace(probe_ticket=False, instrument_probe="", instrument_search_dump=False,
                              instrument_info_dry="", instrument_info_probe="", symbol_switch_dry="",
                              link_state_dump=True, dry_run=False, watched_click=False, round_trip="",
                              close_position="", live=False, account="breakout_1")
    assert tick.resolve_mode(args, {"PROP_EXECUTOR_MODE": "live"}) == "link_state_dump"


# ── robust switch (operator directive 2026-10-01 06:13Z: "there's no way that
# we are unable to switch between symbols"). Live #15022 clicked SOLUSD and the
# link stayed ETHUSD; the 05:28Z dump (#15046) read the watchlist with ZERO
# rows. The switch now waits for rows, polls the link instead of one fixed
# settle, and retries a click that did not take (one-click OFF re-read before
# EVERY click). It is now the ALTERNATIVE route: the primary per-ticket switch
# is the ticket's own Symbol field (tests at the end of this file). ────────

# Rows rendered late: the SAME row nodes (listeners intact) re-attached later.
LATE_ROWS_JS = """ms => { const tb = document.querySelector('tbody'); const rows = [...tb.children];
  rows.forEach(r => r.remove()); setTimeout(() => rows.forEach(r => tb.appendChild(r)), ms); }"""
# The first click on <sym>'s row never reaches the cell (a click that did not take).
SWALLOW_FIRST_JS = """sym => { const tr = [...document.querySelectorAll('tr.instrument')]
  .find(r => r.querySelector('td.sym').firstChild.textContent.trim() === sym); let n = 0;
  tr.addEventListener('click', e => { if (n++ === 0) e.stopPropagation(); }, true); }"""
# The link follows the click only after ``ms`` (an async terminal).
LATE_LINK_JS = """([sym, ms]) => { const tr = [...document.querySelectorAll('tr.instrument')]
  .find(r => r.querySelector('td.sym').firstChild.textContent.trim() === sym);
  tr.addEventListener('click', e => { e.stopPropagation();
    setTimeout(() => { document.querySelector('[data-test-id=symbol_input]').value = sym; }, ms); }, true); }"""

def fast_adapter():
    a = DXtradeAdapter(timeout_ms=3_000)
    a.SWITCH_READY_MS, a.SWITCH_VERIFY_MS, a.SWITCH_POLL_MS = 3_000, 600, 50
    return a


def robust(browser, html, target, *setup):  # noqa: F811
    p = browser.new_page()
    p.set_content(html)
    for js, arg in setup:
        p.evaluate(js, arg)
    got = fast_adapter().select_linked_symbol(p, target, settle_ms=50)
    st = state(p)
    st["keys"] = p.evaluate("window.__keys || []")
    p.close()
    return got, st


def test_robust_waits_for_watchlist_rows_that_render_late(browser):  # noqa: F811
    got, st = robust(browser, page_html(), "ETHUSD", (LATE_ROWS_JS, 700))
    assert got["ok"] is True and got["route"] == "watchlist" and got["attempts"] == 1
    assert got["ready"]["waited_ms"] >= 500 and got["ready"]["watchlist_n"] == 4
    assert st["clicks"] == ["sym"] and st["linked"] == "ETHUSD" and st["tags"] == 0
    never_traded(st)


def test_robust_retries_a_click_that_did_not_take(browser):  # noqa: F811
    got, st = robust(browser, page_html(), "ETHUSD", (SWALLOW_FIRST_JS, "ETHUSD"))
    assert got["ok"] is True and got["route"] == "watchlist" and got["attempts"] == 2
    assert st["clicks"] == ["sym", "sym"] and st["linked"] == "ETHUSD"
    never_traded(st)


def test_robust_polls_a_link_that_follows_late_and_clicks_once(browser):  # noqa: F811
    got, st = robust(browser, page_html(), "ETHUSD", (LATE_LINK_JS, ["ETHUSD", 400]))
    assert got["ok"] is True and got["attempts"] == 1 and got["verify_waited_ms"] >= 350
    assert st["clicks"] == ["sym"]                       # verified by polling, never re-clicked
    never_traded(st)


def test_robust_stops_after_the_bounded_attempts(browser):  # noqa: F811
    got, st = robust(browser, page_html(link_breaks_for="ETHUSD"), "ETHUSD")
    assert got["ok"] is False and got["attempts"] == 3 and st["clicks"] == ["sym", "sym", "sym"]
    assert "linked symbol reads 'SOLUSD' after selecting ETHUSD" in got["why"]
    assert "toolbar" not in got and st["linked"] == "SOLUSD" and st["keys"] == []   # no typing route any more
    never_traded(st)


def test_robust_an_empty_watchlist_refuses_without_a_cell_click(browser):  # noqa: F811
    got, st = robust(browser, page_html(), "ETHUSD", (LATE_ROWS_JS, 60_000))
    assert got["ok"] is False and got["attempts"] == 0 and got["ready"]["watchlist_n"] == 0
    assert got["ready"]["waited_ms"] >= 3_000 and "no single clean watchlist Symbol cell" in got["why"]
    assert st["clicks"] == [] and st["keys"] == [] and st["linked"] == "SOLUSD"
    never_traded(st)


def test_robust_one_click_on_refuses_before_any_click(browser):  # noqa: F811
    got, st = robust(browser, page_html(one_click="checked", link_breaks_for="ETHUSD"), "ETHUSD")
    assert got["ok"] is False and "one-click not confirmed OFF" in got["why"]
    assert st["clicks"] == [] and st["linked"] == "SOLUSD"
    never_traded(st)


def test_link_state_dump_waits_for_rows_that_render_late(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html())
    p.evaluate(LATE_ROWS_JS, 1_200)
    got = fast_adapter().link_state_dump(p)
    st = state(p)
    p.close()
    assert [r["sym"] for r in got["rows"]] == ["ETHUSD", "SOLUSD", "BTCUSD", "AVAXUSD"]
    assert got["rows_waited_ms"] >= 1_000 and got["polls"] >= 3
    assert st["clicks"] == [] and st["tags"] == 0


def test_link_state_dump_records_the_full_wait_when_rows_never_render(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html())
    p.evaluate(LATE_ROWS_JS, 60_000)
    got = fast_adapter().link_state_dump(p)
    p.close()
    assert got["rows"] == [] and got["rows_waited_ms"] >= 3_000


def test_link_state_dump_table_diag_explains_rows_the_filter_rejects(browser):  # noqa: F811
    # A watchlist that grew a 4th <td> per row without a 4th header: every
    # row is rejected by the td-count filter (rows == []); the diag says why.
    p = browser.new_page()
    p.set_content(page_html())
    p.evaluate("() => document.querySelectorAll('tr.instrument').forEach(r => r.appendChild(document.createElement('td')))")
    a = fast_adapter()
    a.SWITCH_READY_MS = 0
    got = a.link_state_dump(p)
    st = state(p)
    p.close()
    (d,) = got["table_diag"]
    assert got["rows"] == [] and d["headers"] == ["symbol", "bid", "ask"]
    assert d["n_tr"] == 5 and d["n_tr_selector"] == 4 and d["td_count_hist"] == {"0": 1, "4": 4}
    assert [r["sym"] for r in d["sample"][1:]] == ["ETHUSD", "SOLUSD", "BTCUSD", "AVAXUSD"]
    assert st["clicks"] == [] and st["tags"] == 0


# ── manager review of #15070: a Positions / Orders row that reads the target
# is never a switch target. ─────────────────────────────────────────────────

POS_FLAG_JS = """() => document.querySelectorAll('[data-row-id=p1]').forEach(r =>
  r.addEventListener('click', () => { window.__posClicked = (window.__posClicked || 0) + 1; }, true))"""


# ── manager review of #15070 / #15075: ONE shared exclusion predicate
# (WATCHLIST_OR_TRADE_JS) for both watchlist layouts and the trade widgets,
# and the dump reads rows in the watchlist's widget (Breakout's rows live in
# a separate table from the Symbol/Bid/Ask headers, #13898). ───────────────

TRADEIFY_WL = ('<div class="widgetNew__container tradeify-wl">'
               '<div data-test-id="table_column_symbol" style="display:inline-block;width:60px">&nbsp;</div>'
               '<div data-test-id="table_column_bid" style="display:inline-block;width:60px">&nbsp;</div>'
               '<table><tbody><tr class="instrument" data-row-id="t1"><td>BTCUSD</td><td>1</td></tr></tbody>'
               '</table></div>')
# Re-render rows on typing: a NEW row reading <target> appears in the Tradeify
# watchlist and in the Positions widget -- "new since typing" alone would let
# them through; only the shared predicate keeps them out.


def test_shared_predicate_covers_both_watchlists_and_trade_widgets_but_not_a_dropdown(browser):  # noqa: F811
    from src.prop.platform.dxtrade import WATCHLIST_OR_TRADE_JS
    p = browser.new_page()
    p.set_content(page_html(outside_table=ORDER_ROW_OUTSIDE + TRADEIFY_WL
                            + '<ul role="listbox"><li role="option"><span>ETHUSD</span></li></ul>'))
    got = p.evaluate("() => {" + WATCHLIST_OR_TRADE_JS + """
      const f = __metisIsWatchlistOrTradeTable;
      return {watchlist_cell: f(document.querySelector('td.sym')),
              positions_row: f(document.querySelector('[data-row-id=p1] td')),
              tradeify_cell: f(document.querySelector('.tradeify-wl td')),
              tradeify_header: f(document.querySelector('[data-test-id=table_column_symbol]')),
              dropdown_option: f(document.querySelector('[role=option] span')),
              toolbar_input: f(document.querySelector('[data-test-id=symbol_input]'))}; }""")
    p.close()
    assert got == {"watchlist_cell": True, "positions_row": True, "tradeify_cell": True, "tradeify_header": True,
                   "dropdown_option": False, "toolbar_input": False}


# Breakout's measured split (#13898): the Symbol/Bid/Ask <th> table holds no
# rows; the rows sit in a SEPARATE table in the same widget.
SPLIT_WL_JS = """() => { const t = document.querySelector('table'); const tb = t.querySelector('tbody');
  const rows = document.createElement('table'); rows.className = 'rows'; rows.appendChild(tb);
  t.parentElement.appendChild(rows); }"""


def test_link_state_dump_reads_rows_from_breakouts_separate_rows_table(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html())
    p.evaluate(SPLIT_WL_JS)
    got = fast_adapter().link_state_dump(p)
    st = state(p)
    p.close()
    assert got["rows_scope"] == "widget" and got["rows_waited_ms"] == 0
    assert [r["sym"] for r in got["rows"]] == ["ETHUSD", "SOLUSD", "BTCUSD", "AVAXUSD"]
    (d,) = got["table_diag"]
    assert d["n_tr_selector"] == 0 and d["n_tr_selector_in_widget"] == 4      # what #15046/#15064 could not see
    assert st["clicks"] == [] and st["tags"] == 0


def test_the_switch_works_on_breakouts_separate_rows_table(browser):  # noqa: F811
    got, st = robust(browser, page_html(), "ETHUSD", (SPLIT_WL_JS, None))
    assert got["ok"] is True and got["route"] == "watchlist" and got["attempts"] == 1
    assert got["ready"]["watchlist_n"] == 4 and st["clicks"] == ["sym"] and st["linked"] == "ETHUSD"
    never_traded(st)


# ── manager review 2026-10-01 08:31Z: Positions tables sitting in a container
# with NO widget_menu_* button (Tradeify's are unmeasured). ────────────────

POS_NO_MENU = ('<div class="pos-box"><table><tbody><tr class="instrument" data-row-id="q1">'
               '<td class="posrow">ETHUSD</td><td>Buy</td></tr></tbody></table></div>'
               '<div class="pos-box2"><table><tbody><tr><td class="posrow2">ETHUSD</td><td>Sell</td></tr>'
               '</tbody></table></div>')


def test_shared_predicate_excludes_a_menu_less_positions_table_with_instrument_rows(browser):  # noqa: F811
    from src.prop.platform.dxtrade import WATCHLIST_OR_TRADE_JS
    p = browser.new_page()
    p.set_content(page_html(outside_table=POS_NO_MENU))
    got = p.evaluate("() => {" + WATCHLIST_OR_TRADE_JS + """
      return [__metisIsWatchlistOrTradeTable(document.querySelector('td.posrow')),
              __metisIsWatchlistOrTradeTable(document.querySelector('td.posrow2'))]; }""")
    p.close()
    # tr.instrument rows: excluded by the predicate; a plain-<tr> table is not a known shape, so the
    # ticket pick's dropdown-shape requirement is what keeps it out (ticket-route tests below).
    assert got == [True, False]


# ── manager 2026-10-01 08:58Z (operator screenshot of breakout_1): a chart
# QUICK-TRADE BAR (Sell | qty ± | Buy) sits beside the linked symbol box, and
# the watchlist header has its own "Symbol..." SEARCH box. The ticket route
# types only into the ticket's own Symbol field and never touches either.

QUICK_BAR_JS = """() => { const item = document.querySelector('.toolbar__item');
  const bar = document.createElement('div'); bar.className = 'quick-trade';
  bar.innerHTML = '<button class="qt-sell">Sell 2,685.83</button><button class="qt-minus">-</button>'
    + '<input class="qt-qty" value="0.01"><button class="qt-plus">+</button><button class="qt-buy">2,685.84 Buy</button>';
  item.appendChild(bar);
  const qty = bar.querySelector('.qt-qty');
  for (const ev of ['focus', 'input', 'keydown']) qty.addEventListener(ev, () => { window.__qtyTouched = (window.__qtyTouched || 0) + 1; });
  bar.addEventListener('click', () => { window.__barClicked = (window.__barClicked || 0) + 1; }, true); }"""
# The watchlist header's own search box -- FIRST [data-test-id=symbol_input] in DOM order.
WL_SEARCH_JS = """() => { const wl = document.querySelector('.widgetNew__container');
  const s = document.createElement('input'); s.setAttribute('data-test-id', 'symbol_input');
  s.className = 'wl-search'; s.placeholder = 'Symbol...'; wl.insertBefore(s, wl.firstChild);
  for (const ev of ['focus', 'input', 'keydown']) s.addEventListener(ev, () => { window.__searchTouched = (window.__searchTouched || 0) + 1; }); }"""


def test_shared_predicate_excludes_the_chart_quick_trade_bar_but_not_the_symbol_box(browser):  # noqa: F811
    from src.prop.platform.dxtrade import WATCHLIST_OR_TRADE_JS
    p = browser.new_page()
    p.set_content(page_html())
    p.evaluate(QUICK_BAR_JS)
    got = p.evaluate("() => {" + WATCHLIST_OR_TRADE_JS + """
      const f = __metisIsWatchlistOrTradeTable;
      return ['.qt-sell', '.qt-buy', '.qt-qty', '.qt-plus', '.toolbar__item [data-test-id=symbol_input]',
              '[data-test-id=instrument_info_button]'].map(q => f(document.querySelector(q))); }""")
    p.close()
    assert got == [True, True, True, True, False, False]



# ── THE TICKET'S OWN SYMBOL FIELD: the PRIMARY per-ticket switch (operator by
# hand on breakout_1 and tradeify_1, manager comment 5928196365 on #15070).
# The fixture is the MEASURED sidebar (tests.test_prop_executor) dropped into
# the watchlist page, plus a dropdown modelled on the screenshots: tabs All |
# Cryptocurrencies | Stocks, header Symbol | Description | Asset Class, one row
# per instrument sharing the typed first letter; a row click commits the
# symbol, the description under the field and the submit label; blur shows
# the committed symbol (typing alone never sets the ticket). ───────────────

BREAKOUT_INSTRUMENTS = [["SOLUSD", "SOL"], ["SOLUSD.X", "SOL x"], ["ETHUSD", "ETH"], ["ETHUSD.X", "ETH x"],
                        ["BTCUSD", "BTC"], ["ADAUSD", "ADA"]]
TRADEIFY_INSTRUMENTS = [["ETH/USD", "ETH"], ["ENA/USD", "ENA"], ["ETC/USD", "ETC"], ["TRX/USD", "TRX"],
                        ["SOL/USD", "SOL"]]


def ticket_sidebar(sym="SOLUSD", desc="SOL"):
    from tests.test_prop_executor import _measured
    doc = _measured(sym=sym, symval=sym)
    inner = doc[doc.index('<body style="margin:0">') + len('<body style="margin:0">'):doc.index("</body>")]
    inner = inner.replace("left:903px", "left:1300px").replace("<div>SOL</div>", f'<div class="tk-desc">{desc}</div>', 1)
    return inner.replace(f">Buy {sym}</button>", f">Buy 0.01 {sym} at 1.00</button>", 1)


TICKET_DD_JS = """({inside, instruments, dialog, broken, highlight}) => {
  const box = document.querySelector('#panel [data-test-id=symbol_input]');
  const desc = document.querySelector('#panel .tk-desc'), sub = document.querySelector('#sub');
  window.__tk = {committed: box.value, keys: [], tabs: 0, picks: 0};
  box.addEventListener('keydown', e => window.__tk.keys.push(e.key));
  box.addEventListener('input', () => {
    document.querySelectorAll('.tk-dd').forEach(e => e.remove());
    const v = box.value.trim().toUpperCase(); if (!v) return;
    const dd = document.createElement('div'); dd.className = 'tk-dd';
    dd.innerHTML = '<div class="tabs"><button class="tab">All</button><button class="tab">Cryptocurrencies</button>'
      + '<button class="tab">Stocks</button></div>'
      + '<div class="hdr"><div>Symbol</div><div>Description</div><div>Asset Class</div></div>';
    for (const [s, d] of instruments) {
      if (s[0] !== v[0]) continue;
      const r = document.createElement('div'); r.className = 'ddrow';
      const symHtml = highlight ? '<span class="hl">' + s.slice(0, 3) + '</span>' + s.slice(3) : s;
      r.innerHTML = '<div class="c-sym">' + symHtml + '</div><div class="c-desc">' + d + '</div><div class="c-cls">Cryptocurrencies</div>';
      r.addEventListener('click', () => { window.__tk.picks++;
        if (s === broken) { box.value = s; dd.remove(); return; }           // the field only: nothing committed
        window.__tk.committed = s; box.value = s; desc.textContent = d;
        sub.textContent = 'Buy 0.01 ' + s + ' at 1.00'; dd.remove(); });
      dd.appendChild(r);
    }
    dd.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => window.__tk.tabs++));
    (inside ? document.querySelector('#s1') : document.body).appendChild(dd);
    if (dialog) { const d = document.createElement('div'); d.setAttribute('role', 'dialog');
      d.textContent = 'Confirm?'; d.style.cssText = 'width:200px;height:100px'; document.body.appendChild(d); }
  });
  box.addEventListener('blur', () => { box.value = window.__tk.committed; }); }"""


def ticket_page(browser, *, sym="SOLUSD", desc="SOL", instruments=BREAKOUT_INSTRUMENTS, inside=False,  # noqa: F811
                dialog=False, dropdown=True, extra="", base=None, setup=(), broken="", highlight=False):
    p = browser.new_page()
    p.set_content((base or page_html)(outside_table=ticket_sidebar(sym, desc) + extra))
    if dropdown:
        p.evaluate(TICKET_DD_JS, {"inside": inside, "instruments": instruments, "dialog": dialog,
                                  "broken": broken, "highlight": highlight})
    else:
        p.evaluate("() => { const b = document.querySelector('#panel [data-test-id=symbol_input]');"
                   " window.__tk = {committed: b.value, keys: [], tabs: 0, picks: 0};"
                   " b.addEventListener('keydown', e => window.__tk.keys.push(e.key));"
                   " b.addEventListener('blur', () => { b.value = window.__tk.committed; }); }")
    for js in setup:
        p.evaluate(js)
    return p


def tk_state(p):
    return p.evaluate("""() => ({clicks: window.__clicks, trade: window.__trade || 0, ticket: window.__ticket || 0,
      field: document.querySelector('#panel [data-test-id=symbol_input]').value,
      chart: document.querySelector('.toolbar__item [data-test-id=symbol_input]').value,
      desc: document.querySelector('#panel .tk-desc').textContent, submit: document.querySelector('#sub').textContent,
      submits: window.__submits || 0, tk: window.__tk, bar: window.__barClicked || 0, qty: window.__qtyTouched || 0,
      search: window.__searchTouched || 0,
      tags: document.querySelectorAll('[data-metis-ticket-sym],[data-metis-ticket-pick]').length})""")


def ticket_switch(browser, target, **kw):  # noqa: F811
    p = ticket_page(browser, **kw)
    got = fast_adapter().select_ticket_symbol(p, target, settle_ms=50)
    st = tk_state(p)
    p.close()
    return got, st


def no_order(st):
    assert st["submits"] == 0 and st["trade"] == 0 and st["ticket"] == 0
    assert not any(c in ("BUY", "SELL", "px") or str(c).startswith("qt-") for c in st["clicks"])


@pytest.mark.parametrize("inside", [False, True])
def test_ticket_route_picks_the_exact_row_and_verifies_field_description_and_submit(browser, inside):  # noqa: F811
    got, st = ticket_switch(browser, "ETHUSD", inside=inside)
    assert got["ok"] is True and got["picked"] is True and got["route"] == "ticket_field"
    assert got["before"] == {"value": "SOLUSD", "desc": "SOL", "submit_symbol": "SOLUSD"}
    assert got["after"] == {"value": "ETHUSD", "desc": "ETH", "submit_symbol": "ETHUSD"}
    assert got["pick"]["n_candidates"] == 1 and got["pick"]["typed"] == "ETHUSD"
    assert st["clicks"] == ["c-sym"] and st["tk"]["tabs"] == 0 and "Enter" not in st["tk"]["keys"]
    assert st["field"] == "ETHUSD" and st["chart"] == "SOLUSD" and st["tags"] == 0
    no_order(st)


def test_ticket_route_takes_the_exact_symbol_never_a_near_miss(browser):  # noqa: F811
    got, st = ticket_switch(browser, "SOLUSD", sym="ETHUSD", desc="ETH")
    assert got["ok"] is True and st["field"] == "SOLUSD" and st["desc"] == "SOL"   # never SOLUSD.X
    assert got["pick"]["n_candidates"] == 1 and st["clicks"] == ["c-sym"]
    no_order(st)


def test_ticket_route_tradeify_slash_names_exact_match_and_never_the_stocks_tab(browser):  # noqa: F811
    got, st = ticket_switch(browser, "ETH/USD", sym="TRX/USD", desc="TRX", instruments=TRADEIFY_INSTRUMENTS)
    assert got["ok"] is True and got["after"]["value"] == "ETH/USD" and got["after"]["submit_symbol"] == "ETH/USD"
    assert st["tk"]["tabs"] == 0 and st["tk"]["picks"] == 1 and st["clicks"] == ["c-sym"]    # not ENA/USD, ETC/USD
    no_order(st)


def test_ticket_route_on_the_tradeify_layout(browser):  # noqa: F811
    from tests.test_prop_instrument_info_probe import tradeify_layout
    got, st = ticket_switch(browser, "ETH/USD", sym="SOL/USD", desc="SOL", instruments=TRADEIFY_INSTRUMENTS,
                            base=lambda **kw: tradeify_layout(page_html(**kw)))
    assert got["ok"] is True and st["field"] == "ETH/USD" and st["clicks"] == ["c-sym"]
    no_order(st)


RERENDER_ROWS_ON_TICKET_TYPING_JS = """() => {
  const box = document.querySelector('#panel [data-test-id=symbol_input]');
  box.addEventListener('input', () => {
    window.__rerendered = 0;
    document.querySelectorAll('tbody tr').forEach(tr => { tr.replaceWith(tr.cloneNode(true)); window.__rerendered++; });
  }); }"""


@pytest.mark.parametrize("dropdown", [False, True])
def test_ticket_route_never_takes_positions_or_watchlist_rows_even_rerendered_during_typing(browser, dropdown):  # noqa: F811
    # Positions rows reading the target -- in a widget WITH a menu button, and in menu-less tables with
    # instrument and plain rows -- and every body row cloned-and-replaced while the field is typed into.
    p = ticket_page(browser, sym="SOLUSD", dropdown=dropdown, extra=ORDER_ROW_OUTSIDE + POS_NO_MENU,
                    setup=[RERENDER_ROWS_ON_TICKET_TYPING_JS])
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    rerendered = p.evaluate("window.__rerendered || 0")
    p.close()
    assert rerendered >= 6
    whys = {r["why"] for r in got["pick"]["rejected"]}
    assert whys and whys <= {"watchlist_or_trade", "not_in_a_dropdown"}
    if dropdown:
        assert got["ok"] is True and got["pick"]["n_candidates"] == 1 and st["clicks"] == ["c-sym"]
    else:
        assert got["ok"] is False and got["picked"] is False and got["pick"]["n_candidates"] == 0
        assert st["clicks"] == [] and got["field_reset"] is True and st["field"] == "SOLUSD"
    no_order(st)


def test_ticket_route_with_no_dropdown_puts_the_field_back_and_presses_no_key(browser):  # noqa: F811
    got, st = ticket_switch(browser, "ETHUSD", dropdown=False)
    assert got["ok"] is False and got["picked"] is False and "need exactly 1" in got["why"]
    assert got["pick"]["typed"] == "eth"                          # retried once with the first three letters
    assert got["field_reset"] is True and st["field"] == "SOLUSD" and st["tk"]["keys"] == []
    assert st["clicks"] == [] and st["tags"] == 0
    no_order(st)


def test_ticket_route_refuses_before_typing_unless_one_click_reads_off(browser):  # noqa: F811
    p = ticket_page(browser, base=lambda **kw: page_html(one_click="checked", **kw))
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    p.close()
    assert got["ok"] is False and "one-click not confirmed OFF" in got["why"] and "pick" not in got
    assert st["field"] == "SOLUSD" and st["tk"]["keys"] == [] and st["clicks"] == []
    no_order(st)


def test_ticket_route_stops_on_a_dialog_after_typing_without_picking(browser):  # noqa: F811
    got, st = ticket_switch(browser, "ETHUSD", dialog=True)
    assert got["ok"] is False and got["picked"] is False and "dialog(s) open after typing" in got["why"]
    assert st["clicks"] == [] and st["field"] == "SOLUSD"
    no_order(st)


def test_ticket_route_never_touches_the_quick_trade_bar_the_chart_box_or_the_watchlist_search(browser):  # noqa: F811
    p = ticket_page(browser, setup=[QUICK_BAR_JS, WL_SEARCH_JS])
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    p.close()
    assert got["ok"] is True and st["field"] == "ETHUSD"
    assert st["bar"] == 0 and st["qty"] == 0 and st["search"] == 0 and st["chart"] == "SOLUSD"
    assert st["clicks"] == ["c-sym"]
    no_order(st)


def test_ticket_route_a_field_already_on_the_target_does_nothing(browser):  # noqa: F811
    got, st = ticket_switch(browser, "SOLUSD")
    assert got["ok"] is True and got["picked"] is False and "one_click" not in got
    assert st["clicks"] == [] and st["tk"]["keys"] == []


# ── symbol-switch-dry now drives the ticket route, then leaves the LINK on
# ``home`` (the tick: the original link when enabled, else the first enabled
# venue symbol -- a link stuck on ETHUSD comes back to SOLUSD). ────────────

def switch_dry(browser, target, *, home=None, link="SOLUSD", ticket="SOLUSD", desc="SOL", base=None,  # noqa: F811
               instruments=BREAKOUT_INSTRUMENTS):
    p = ticket_page(browser, sym=ticket, desc=desc, base=base, instruments=instruments)
    p.evaluate("v => { document.querySelector('.toolbar__item [data-test-id=symbol_input]').value = v; }", link)
    got = fast_adapter().symbol_switch_dry(p, target, settle_ms=50, home=home)
    st = tk_state(p)
    p.close()
    return got, st


def test_symbol_switch_dry_ticket_round_and_link_left_on_the_original(browser):  # noqa: F811
    got, st = switch_dry(browser, "ETHUSD")
    assert got["refused"] is None and got["alerts"] == [] and got["original"] == "SOLUSD" and got["home"] == "SOLUSD"
    assert got["ticket_open"] == {"opened": True, "via": "already_open", "refused": None}
    assert got["switch"]["ok"] is True and got["switch"]["after"]["value"] == "ETHUSD"
    assert got["ticket_home"]["ok"] is True and got["ticket_home"]["after"]["value"] == "SOLUSD"
    assert got["restore"]["ok"] is True and got["restore"]["clicked"] is False       # the link never moved
    assert st["clicks"][:2] == ["c-sym", "c-sym"] and st["field"] == "SOLUSD" and st["chart"] == "SOLUSD"
    no_order(st)


def test_symbol_switch_dry_relinks_a_stuck_link_to_home(browser):  # noqa: F811
    # The live state: link and ticket stuck on ETHUSD (not enabled); home = SOLUSD.
    got, st = switch_dry(browser, "SOLUSD", home="SOLUSD", link="ETHUSD", ticket="ETHUSD", desc="ETH")
    assert got["alerts"] == [] and got["original"] == "ETHUSD" and got["home"] == "SOLUSD"
    assert got["switch"]["ok"] is True and got["ticket_home"]["ok"] is True and got["ticket_home"]["picked"] is False
    assert got["restore"]["ok"] is True and got["restore"]["clicked"] is True and got["restore"]["route"] == "watchlist"
    assert st["chart"] == "SOLUSD" and st["field"] == "SOLUSD"
    assert st["clicks"].count("c-sym") == 1 and st["clicks"].count("sym") == 1
    no_order(st)


def test_symbol_switch_dry_on_the_tradeify_layout(browser):  # noqa: F811
    from tests.test_prop_instrument_info_probe import tradeify_layout
    got, st = switch_dry(browser, "ETHUSD", base=lambda **kw: tradeify_layout(page_html(**kw)))
    assert got["refused"] is None and got["alerts"] == [] and got["original"] == "SOLUSD"
    assert got["quote_raw"]["rows"] == [["ETHUSD", "100.1", "100.2"]]
    assert got["switch"]["ok"] is True and got["ticket_home"]["ok"] is True and got["restore"]["ok"] is True
    no_order(st)


def test_symbol_switch_dry_reads_the_target_quote_click_free_before_any_click(browser):  # noqa: F811
    order = []

    class Recording(DXtradeAdapter):
        def read_quote(self, page, venue_symbol):
            order.append(("quote", venue_symbol, page.evaluate("window.__clicks.length")))
            return super().read_quote(page, venue_symbol)

        def select_ticket_symbol(self, page, venue_symbol, *, settle_ms=1_500):
            order.append(("ticket", venue_symbol))
            return super().select_ticket_symbol(page, venue_symbol, settle_ms=settle_ms)

    p = ticket_page(browser)
    a = Recording(timeout_ms=3_000)
    a.SWITCH_READY_MS, a.SWITCH_VERIFY_MS, a.SWITCH_POLL_MS = 3_000, 600, 50
    got = a.symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    p.close()
    assert order[0] == ("quote", "ETHUSD", 0) and ("ticket", "ETHUSD") in order
    assert got["quote"]["bid"] == 100.1 and got["alerts"] == []
    no_order(st)


def test_symbol_switch_dry_alerts_when_no_ticket_opens_and_still_restores_the_link(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html())
    got = fast_adapter().symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    p.close()
    assert any(a.startswith("no order ticket opened") for a in got["alerts"])
    assert got["restore"]["ok"] is True and "switch" not in got


# PROP-ETH-DOM #15139 (MEASURED live 2026-10-01 11:30Z on breakout_1): typing in
# the ticket's Symbol field produced NO dropdown row; the watchlist click then
# re-linked SOLUSD with the ticket field following. The dry run now takes the
# SAME route as the live placement (switch_ticket_symbol), so that is a note,
# not an alert -- and a ticket field that does NOT follow still alerts.
TICKET_FOLLOWS_WATCHLIST_JS = """() => {
  document.addEventListener('click', e => {
    const c = e.target.closest('.sym'); if (!c) return;
    const s = (c.innerText || '').trim();
    setTimeout(() => { const b = document.querySelector('#panel [data-test-id=symbol_input]');
      b.value = s; if (window.__tk) window.__tk.committed = s;
      document.querySelector('#panel .tk-desc').textContent = s.slice(0, 3);
      document.querySelector('#sub').textContent = 'Buy 0.01 ' + s + ' at 1.00'; }, 0);
  }, true); }"""


@pytest.mark.parametrize("follows", [True, False])
def test_symbol_switch_dry_takes_the_live_watchlist_alternative_when_the_field_lists_nothing(browser, follows):  # noqa: F811
    p = ticket_page(browser, sym="ETHUSD", desc="ETH", dropdown=False,
                    setup=[TICKET_FOLLOWS_WATCHLIST_JS] if follows else [])
    p.evaluate("v => { document.querySelector('.toolbar__item [data-test-id=symbol_input]').value = v; }", "ETHUSD")
    got = fast_adapter().symbol_switch_dry(p, "SOLUSD", settle_ms=50, home="SOLUSD")
    st = tk_state(p)
    p.close()
    sw = got["switch"]
    assert sw["ticket_route"]["pick"]["n_candidates"] == 0 and sw["ticket_route"]["pick"]["field_value"] == "sol"
    assert st["chart"] == "SOLUSD" and st["tk"]["keys"] == [] and st["clicks"] == ["sym"]
    if follows:
        assert got["alerts"] == [] and sw["ok"] is True and sw["route"] == "watchlist"
        assert sw["ticket_after"] == "SOLUSD" and st["field"] == "SOLUSD"
        assert len(got["notes"]) == 1 and "watchlist route used" in got["notes"][0]
        assert got["ticket_home"]["ok"] is True and got["restore"]["clicked"] is False
    else:
        # Both legs alert: the click's follow check, and the click-free leg
        # whose link ALREADY reads SOLUSD while the ticket still names ETHUSD.
        assert sw["ok"] is False and got["ticket_home"]["ok"] is False and st["field"] == "ETHUSD"
        assert "after the watchlist route" in got["ticket_home"]["why"]
        assert [a.split(" failed")[0] for a in got["alerts"]] == ["ticket switch to SOLUSD", "ticket back to SOLUSD"]
    no_order(st)


def test_ticket_pick_reports_what_typing_produced_by_shape_only(browser):  # noqa: F811
    got, _ = ticket_switch(browser, "ETHUSD")
    tops = got["pick"]["new_tops"]
    assert got["pick"]["field_value"] == "ETHUSD"
    assert any(t["cls"] == ["tk-dd"] and t["n_leaves"] > 0 for t in tops)
    assert all(set(t) == {"tag", "cls", "role", "n_leaves", "w", "h"} for t in tops)   # never text


def test_switch_dry_home_rules():
    from types import SimpleNamespace as NS

    from scripts.prop.prop_executor_tick import switch_dry_home

    class Ad:
        def __init__(self, linked):
            self.linked = linked

        def read_linked_symbol(self, page):
            return self.linked

    assert switch_dry_home(NS(enabled_venue_symbols=["SOLUSD"]), Ad("ETHUSD"), None) == "SOLUSD"
    assert switch_dry_home(NS(enabled_venue_symbols=["SOLUSD", "ETHUSD"]), Ad("ETHUSD"), None) == "ETHUSD"
    assert switch_dry_home(NS(enabled_venue_symbols=[]), Ad("ETHUSD"), None) is None
    assert switch_dry_home(NS(enabled_venue_symbols=["SOLUSD"]), Ad(None), None) is None


# ── place_bracket: the ticket field FIRST; the watchlist click only when the
# ticket route picked nothing. ──────────────────────────────────────────────

class _RouteStub(_Stub):
    def __init__(self, ticket):
        super().__init__({"found": True, "symbol_value": "SOLUSD", "fields": {}}, {},
                         after_form={"found": True, "symbol_value": "ETHUSD", "fields": {}})
        self.ticket = ticket
        self.watchlist_calls = 0

    def select_ticket_symbol(self, page, sym, *, settle_ms=1_500):
        return dict(self.ticket)

    def select_linked_symbol(self, page, sym, *, settle_ms=1_500):
        self.watchlist_calls += 1
        return {"ok": False, "clicked": False, "why": "watchlist stub"}


def test_place_bracket_uses_the_ticket_route_and_skips_the_watchlist_when_it_works():
    ad = _RouteStub({"ok": True, "picked": True})
    got = ad.place_bracket(object(), SPEC)
    assert ad.watchlist_calls == 0 and "symbol switch" not in (got.detail or "")


def test_place_bracket_never_falls_back_after_a_ticket_pick_that_did_not_verify():
    ad = _RouteStub({"ok": False, "picked": True, "why": "ticket symbol reads 'SOLUSD' after picking ETHUSD"})
    got = ad.place_bracket(object(), SPEC)
    assert got.stage == "refused" and ad.watchlist_calls == 0
    assert got.detail.startswith("symbol switch: ticket symbol reads")


def test_place_bracket_falls_back_to_the_watchlist_only_when_the_ticket_route_picked_nothing():
    ad = _RouteStub({"ok": False, "picked": False, "why": "0 dropdown rows read exactly ETHUSD (need exactly 1)"})
    got = ad.place_bracket(object(), SPEC)
    assert got.stage == "refused" and ad.watchlist_calls == 1 and got.detail == "symbol switch: watchlist stub"
    assert got.form["symbol_switch"]["ticket_route"]["why"].startswith("0 dropdown rows")


# A Positions widget whose table SHOWS Symbol | Description headers (so it looks
# like the dropdown) -- with a menu button, and menu-less with instrument rows --
# RE-CREATED whole during the typing (so it is also "new"). Only the shared
# watchlist/trade exclusion can keep it out.
POS_DESC = ('<div class="widget__container___Pq1 widgetNew__container pos-desc">'
            '<button data-test-id="widget_menu_POSITIONS">Positions</button><table><thead><tr><th>Symbol</th>'
            '<th>Description</th><th>Side</th></tr></thead><tbody><tr class="instrument" data-row-id="pd1">'
            '<td class="pdrow">ETHUSD</td><td>ETH</td><td>Buy</td></tr></tbody></table></div>'
            '<div class="pos-desc"><table><thead><tr><th>Symbol</th><th>Description</th></tr></thead><tbody>'
            '<tr class="instrument" data-row-id="pd2"><td class="pdrow2">ETHUSD</td><td>ETH</td></tr></tbody></table></div>')
RECREATE_POSITIONS_ON_TYPING_JS = """() => {
  const box = document.querySelector('#panel [data-test-id=symbol_input]');
  box.addEventListener('input', () => {
    document.querySelectorAll('.pos-desc').forEach(w => w.replaceWith(w.cloneNode(true)));
    window.__recreated = document.querySelectorAll('.pos-desc').length;
  }); }"""


@pytest.mark.parametrize("dropdown", [False, True])
def test_ticket_route_never_takes_a_recreated_positions_table_that_looks_like_the_dropdown(browser, dropdown):  # noqa: F811
    p = ticket_page(browser, dropdown=dropdown, extra=POS_DESC, setup=[RECREATE_POSITIONS_ON_TYPING_JS])
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    recreated = p.evaluate("window.__recreated || 0")
    p.close()
    assert recreated == 2
    rej = [r for r in got["pick"]["rejected"] if r["new_since_typing"]]
    assert rej and {r["why"] for r in rej} == {"watchlist_or_trade"}
    assert "pdrow" not in st["clicks"] and "pdrow2" not in st["clicks"]
    assert got["ok"] is dropdown and got["pick"]["n_candidates"] == (1 if dropdown else 0)
    no_order(st)


def test_ticket_route_never_takes_a_pre_existing_table_that_merely_shows_dropdown_headers(browser):  # noqa: F811
    # Plain rows, no menu button, NOT re-created: only "the dropdown must be new" keeps it out.
    plain = ('<div class="plain-pos"><table><thead><tr><th>Symbol</th><th>Description</th></tr></thead>'
             '<tbody><tr><td class="plainrow">ETHUSD</td><td>ETH</td></tr></tbody></table></div>')
    got, st = ticket_switch(browser, "ETHUSD", dropdown=False, extra=plain)
    assert got["ok"] is False and got["pick"]["n_candidates"] == 0
    assert "dropdown_not_new" in {r["why"] for r in got["pick"]["rejected"]}       # the plain table's row
    assert {r["why"] for r in got["pick"]["rejected"]} <= {"dropdown_not_new", "watchlist_or_trade"}
    assert "plainrow" not in st["clicks"]
    no_order(st)


# ── manager review 2026-10-01 09:18Z (reviewer's variant (b), reproduced on
# main): __metisColumnTables took the watchlist's table_column headers from a
# shared widgetNew__container for a header-less POSITIONS table while the
# watchlist was EMPTY -- equal cell count, aligned -- and the switch clicked the
# position row. The headers' ancestor must hold exactly one table, and the
# header row must sit directly above the table. ────────────────────────────

VARIANT_B_JS = """() => {
  const wl = document.querySelector('table.wl');
  wl.querySelectorAll('tr').forEach(r => r.remove());                     // the watchlist is empty right now
  const pos = document.createElement('table'); pos.className = 'pos';
  pos.style.cssText = 'border-spacing:0;table-layout:fixed';
  pos.innerHTML = '<tbody><tr data-row-id="pos1"><td class="psym" style="width:120px;padding:0">ETHUSD</td>'
    + '<td style="width:120px;padding:0">Buy</td><td style="width:120px;padding:0">0.01</td></tr></tbody>';
  wl.parentElement.appendChild(pos);                                        // same widget container, no menu button
  pos.querySelector('tr').addEventListener('click', () => { window.__posClicked = (window.__posClicked || 0) + 1; }); }"""


def variant_b_page(browser):  # noqa: F811
    from tests.test_prop_instrument_info_probe import tradeify_layout
    p = browser.new_page()
    p.set_content(tradeify_layout(page_html()))
    p.evaluate(VARIANT_B_JS)
    return p


def test_review_variant_b_column_headers_are_never_borrowed_by_a_positions_table(browser):  # noqa: F811
    p = variant_b_page(browser)
    a = fast_adapter()
    a.SWITCH_READY_MS = 300
    got = a.select_linked_symbol(p, "ETHUSD", settle_ms=50)
    from src.prop.platform.dxtrade import INFO_PROBE_RESOLVE_JS
    res = p.evaluate(INFO_PROBE_RESOLVE_JS, [["ETHUSD"]])
    st = state(p)
    pos = p.evaluate("window.__posClicked || 0")
    p.close()
    assert got["ok"] is False and got["clicked"] is False and got["attempts"] == 0
    assert st["clicks"] == [] and pos == 0
    whys = " ".join(res.get("column_headers", {}).get("why", []))
    assert "holds 2 tables (need exactly 1)" in whys
    never_traded(st)


def test_review_variant_b_switch_dry_clicks_nothing_on_the_positions_table(browser):  # noqa: F811
    p = variant_b_page(browser)
    a = fast_adapter()
    a.SWITCH_READY_MS = 300
    got = a.symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    st = state(p)
    pos = p.evaluate("window.__posClicked || 0")
    p.close()
    assert "psym" not in st["clicks"] and pos == 0
    assert got["refused"] or got["alerts"]                           # nothing to switch with; never the position row
    never_traded(st)


def test_review_column_headers_not_directly_above_the_table_are_refused(browser):  # noqa: F811
    from tests.test_prop_instrument_info_probe import tradeify_layout
    p = browser.new_page()
    p.set_content(tradeify_layout(page_html()))
    p.evaluate("() => { const sp = document.createElement('div'); sp.style.height = '40px';"
               " document.querySelector('table.wl').before(sp); }")
    a = fast_adapter()
    a.SWITCH_READY_MS = 300
    got = a.select_linked_symbol(p, "ETHUSD", settle_ms=50)
    st = state(p)
    p.close()
    assert got["ok"] is False and st["clicks"] == []
    never_traded(st)


# ── manager 2026-10-01 09:22Z: verification is never the box we typed into.
# The ticket route passes only when the description under the field CHANGED
# and the submit label NAMES the target. ───────────────────────────────────

# A dropdown whose row click sets ONLY the field (no description, no submit
# label), and a page whose submit label is unreadable.
FIELD_ONLY_PICK_JS = """() => {
  const box = document.querySelector('#panel [data-test-id=symbol_input]');
  box.addEventListener('input', () => {
    document.querySelectorAll('.tk-dd').forEach(e => e.remove());
    const dd = document.createElement('div'); dd.className = 'tk-dd';
    dd.innerHTML = '<div class="hdr"><div>Symbol</div><div>Description</div></div>'
      + '<div class="ddrow"><div class="c-sym">ETHUSD</div><div class="c-desc">ETH</div></div>';
    dd.querySelector('.ddrow').addEventListener('click', () => { box.value = 'ETHUSD'; dd.remove(); });
    document.body.appendChild(dd);
  }); }"""


def test_review_a_field_set_without_desc_or_submit_change_is_not_a_switch(browser):  # noqa: F811
    # The pick "takes" in the field only (typed text kept, nothing committed): never ok.
    p = ticket_page(browser, dropdown=False, setup=[FIELD_ONLY_PICK_JS])
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    p.close()
    assert got["picked"] is True and got["after"]["value"] == "ETHUSD"
    assert got["ok"] is False and got["why"].startswith("submit label names 'SOLUSD', not ETHUSD")
    # Picked but unverified -> the original is put back (manager review of 221c459, item 4): the guarded
    # re-pick finds no SOLUSD row in this dropdown, so the last-resort fill-back restores the field.
    assert got["restore"]["ok"] is False and "RESTORE to SOLUSD FAILED" in got["why"]
    assert got["field_reset"] is True and st["field"] == "SOLUSD"
    no_order(st)


def test_review_an_unreadable_submit_label_is_not_a_pass(browser):  # noqa: F811
    p = ticket_page(browser, setup=["() => { const s = document.querySelector('#sub');"
                                    " const o = new MutationObserver(() => { if (s.textContent !== 'Place order')"
                                    " s.textContent = 'Place order'; }); o.observe(s, {childList: true, characterData: true,"
                                    " subtree: true}); s.textContent = 'Place order'; }"])
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    p.close()
    assert got["picked"] is True and got["after"]["value"] == "ETHUSD" and got["after"]["desc"] == "ETH"
    assert got["after"]["submit_symbol"] is None and got["ok"] is False
    assert got["why"].startswith("submit label names None, not ETHUSD")
    assert got["restore"]["picked"] is True and got["field_reset"] is True


def test_review_a_description_that_does_not_change_is_not_a_pass(browser):  # noqa: F811
    p = ticket_page(browser, setup=["() => { const d = document.querySelector('#panel .tk-desc');"
                                    " new MutationObserver(() => { if (d.textContent !== 'SOL') d.textContent = 'SOL'; })"
                                    ".observe(d, {childList: true, characterData: true, subtree: true}); }"])
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    p.close()
    assert got["picked"] is True and got["after"]["submit_symbol"] == "ETHUSD"
    assert got["ok"] is False and "description under the field did not change" in got["why"]



# ── manager review of 221c459 (BLOCK 1-2, items 3-4) ─────────────────────────

# The reviewer's repro: a PRE-EXISTING div grid (role=grid) Positions row with a
# Close button, no <table>, no widget_menu_*.
GRID_POS = ('<div role="grid" class="pos-grid"><div role="row" class="g-row"><div class="g-sym">ETHUSD</div>'
            '<div>Buy</div><button class="g-close">Close</button></div></div>')
GRID_FLAG_JS = """() => document.querySelector('.g-row').addEventListener('click',
  () => { window.__gridClicked = (window.__gridClicked || 0) + 1; }, true)"""


@pytest.mark.parametrize("dropdown", [False, True])
def test_review_a_pre_existing_role_grid_positions_row_is_never_picked(browser, dropdown):  # noqa: F811
    p = ticket_page(browser, dropdown=dropdown, extra=GRID_POS, setup=[GRID_FLAG_JS])
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    grid = p.evaluate("window.__gridClicked || 0")
    p.close()
    assert grid == 0 and "g-sym" not in st["clicks"] and "g-close" not in st["clicks"]
    assert "dropdown_not_new" in {r["why"] for r in got["pick"]["rejected"]}
    assert got["picked"] is dropdown and got["ok"] is dropdown            # only the real dropdown row, if any
    no_order(st)


def test_review_a_dropdown_entry_inside_a_control_is_never_clicked(browser):  # noqa: F811
    # A new, dropdown-shaped container whose only ETHUSD entry is a <button>.
    js = """() => { const box = document.querySelector('#panel [data-test-id=symbol_input]');
      box.addEventListener('input', () => { const dd = document.createElement('div'); dd.className = 'tk-dd';
        dd.innerHTML = '<div><div>Symbol</div><div>Description</div></div><div class="ddrow">'
          + '<button class="dd-btn">ETHUSD</button><div>ETH</div></div>';
        document.body.appendChild(dd); }); }"""
    p = ticket_page(browser, dropdown=False, setup=[js])
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    p.close()
    assert got["picked"] is False and "dd-btn" not in st["clicks"]
    assert "inside_a_control" in {r["why"] for r in got["pick"]["rejected"]}
    no_order(st)


def test_review_canonical_compare_tradeify_display_eth_slash_usd_for_venue_ethusd(browser):  # noqa: F811
    # tradeify_1 shows ETH/USD; the executor's venue symbol is ETHUSD. Exactly one row: never ENA/USD, ETC/USD.
    got, st = ticket_switch(browser, "ETHUSD", sym="TRX/USD", desc="TRX", instruments=TRADEIFY_INSTRUMENTS)
    assert got["ok"] is True and st["field"] == "ETH/USD" and got["pick"]["n_candidates"] == 1
    assert st["tk"]["picks"] == 1 and st["tk"]["tabs"] == 0
    no_order(st)


def test_review_a_prefix_highlighted_dropdown_row_is_matched_on_its_full_text(browser):  # noqa: F811
    got, st = ticket_switch(browser, "ETHUSD", highlight=True)
    assert got["ok"] is True and got["pick"]["n_candidates"] == 1 and st["field"] == "ETHUSD"
    assert st["clicks"] == ["c-sym"]                                   # the whole cell, not the <span>ETH</span>
    no_order(st)


def test_review_canonical_form_names_symbol_and_submit_label():
    from src.prop.platform.dxtrade import form_names_symbol, submit_label_mismatch
    assert form_names_symbol({"symbol_value": "ETH/USD"}, "ETHUSD") is True
    assert form_names_symbol({"symbol_value": "ETHUSD"}, "ETH/USD") is True
    assert form_names_symbol({"symbol_value": "SOLUSD.X"}, "SOLUSD") is False      # a near-miss is not SOLUSD
    assert form_names_symbol({"form_text": "Buy 0.01 ETH/USD at 1"}, "ETHUSD") is True
    assert form_names_symbol({"form_text": "Buy 0.01 ENA/USD at 1"}, "ETHUSD") is False
    spec = SimpleNamespace(quantity=0.01, venue_symbol="ETHUSD")
    assert submit_label_mismatch("Buy 0.01 ETH/USD at 2,685.84", spec) == ""
    assert "names 'ENA/USD'" in submit_label_mismatch("Buy 0.01 ENA/USD at 0.5", spec)


def test_review_picked_but_unverified_restores_the_original_through_the_guarded_route(browser):  # noqa: F811
    # The ETHUSD row "takes" in the field only; the guarded re-pick of SOLUSD then works.
    p = ticket_page(browser, broken="ETHUSD")
    got = fast_adapter().select_ticket_symbol(p, "ETHUSD", settle_ms=50)
    st = tk_state(p)
    p.close()
    assert got["ok"] is False and got["picked"] is True
    assert got["restore"]["ok"] is True and got["restore"]["picked"] is True, (got["why"], got["restore"])
    assert st["field"] == "SOLUSD" and st["desc"] == "SOL" and st["submit"] == "Buy 0.01 SOLUSD at 1.00"
    assert st["clicks"] == ["c-sym", "c-sym"] and st["tk"]["keys"] == []
    no_order(st)


# BLOCK 2: Tradeify layout with the empty watchlist's <table> GONE and a header-less Positions table
# (3 aligned cells, tr[data-row-id]) directly under the table_column_* header divs.
VARIANT_B_NO_TABLE_JS = """() => {
  const wl = document.querySelector('table.wl'); const host = wl.parentElement;
  const pos = document.createElement('table'); pos.className = 'pos';
  pos.style.cssText = 'border-spacing:0;table-layout:fixed';
  pos.innerHTML = '<tbody><tr data-row-id="pos1"><td class="psym" style="width:120px;padding:0">ETHUSD</td>'
    + '<td style="width:120px;padding:0">Buy</td><td style="width:120px;padding:0">0.01</td></tr></tbody>';
  wl.replaceWith(pos);
  pos.querySelector('tr').addEventListener('click', () => { window.__posClicked = (window.__posClicked || 0) + 1; }); }"""


def test_review_variant_b_without_the_watchlist_table_never_clicks_the_positions_row(browser):  # noqa: F811
    from tests.test_prop_instrument_info_probe import tradeify_layout

    from src.prop.platform.dxtrade import INFO_PROBE_RESOLVE_JS
    p = browser.new_page()
    p.set_content(tradeify_layout(page_html()))
    p.evaluate(VARIANT_B_NO_TABLE_JS)
    a = fast_adapter()
    a.SWITCH_READY_MS = 300
    got = a.select_linked_symbol(p, "ETHUSD", settle_ms=50)
    res = p.evaluate(INFO_PROBE_RESOLVE_JS, [["ETHUSD"]])
    st = state(p)
    pos = p.evaluate("window.__posClicked || 0")
    p.close()
    assert got["ok"] is False and got["clicked"] is False and st["clicks"] == [] and pos == 0
    assert "the bid / ask cells are not prices" in " ".join(res.get("column_headers", {}).get("why", []))
    never_traded(st)
