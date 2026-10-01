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
                   "one_click": {"state": "off", "via": "data-value+knob"}}
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


def test_symbol_switch_dry_selects_verifies_and_restores_without_a_form(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html())
    got = DXtradeAdapter(timeout_ms=3_000).symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    st = state(p)
    p.close()
    assert got["refused"] is None and got["alerts"] == [] and got["original"] == "SOLUSD"
    assert got["switch"]["ok"] is True and got["switch"]["after"] == "ETHUSD"
    assert got["restore"]["ok"] is True and got["restore"]["after"] == "SOLUSD"
    assert st["clicks"] == ["sym", "sym"] and st["linked"] == "SOLUSD"
    never_traded(st)


def test_symbol_switch_dry_refuses_unless_one_click_reads_off(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html(one_click="checked"))
    got = DXtradeAdapter(timeout_ms=3_000).symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    st = state(p)
    p.close()
    assert "does not read OFF" in got["refused"] and st["clicks"] == []


def test_symbol_switch_dry_alerts_when_the_switch_does_not_take(browser):  # noqa: F811
    p = browser.new_page()
    p.set_content(page_html(link_breaks_for="ETHUSD"))
    got = DXtradeAdapter(timeout_ms=3_000).symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    p.close()
    assert any("switch to ETHUSD failed" in a for a in got["alerts"])
    assert got["restore"]["ok"] is True                       # still linked to SOLUSD


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


def test_symbol_switch_dry_reads_the_target_quote_click_free_before_any_click(browser):  # noqa: F811
    # Manager 2026-09-30 20:39Z (option A): the quote's decimals measure ETHUSD's price_step.
    order = []

    class Recording(DXtradeAdapter):
        def read_quote(self, page, venue_symbol):
            order.append(("quote", venue_symbol, page.evaluate("window.__clicks.length")))
            return super().read_quote(page, venue_symbol)

        def select_linked_symbol(self, page, venue_symbol, *, settle_ms=1_500):
            order.append(("switch", venue_symbol))
            return super().select_linked_symbol(page, venue_symbol, settle_ms=settle_ms)

    p = browser.new_page()
    p.set_content(page_html())
    got = Recording(timeout_ms=3_000).symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    st = state(p)
    p.close()
    assert order[0] == ("quote", "ETHUSD", 0)                      # first, with zero clicks so far
    assert got["quote"] and got["quote"]["bid"] == 100.1 and got["quote"]["ask"] == 100.2
    assert "quote_raw" in got and got["alerts"] == [] and st["clicks"] == ["sym", "sym"]
    never_traded(st)


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


# ── TRADEIFY-WIRE T4: the same switch on Tradeify's column-header watchlist ──


def test_tradeify_layout_symbol_switch_dry_selects_verifies_and_restores(browser):  # noqa: F811
    from tests.test_prop_instrument_info_probe import tradeify_layout

    p = browser.new_page()
    p.set_content(tradeify_layout(page_html()))
    got = DXtradeAdapter(timeout_ms=3_000).symbol_switch_dry(p, "ETHUSD", settle_ms=50)
    st = state(p)
    p.close()
    assert got["refused"] is None and got["alerts"] == [] and got["original"] == "SOLUSD"
    assert got["quote_raw"]["rows"] == [["ETHUSD", "100.1", "100.2"]]
    assert got["switch"]["ok"] is True and got["restore"]["ok"] is True
    assert st["clicks"] == ["sym", "sym"] and st["linked"] == "SOLUSD"
    never_traded(st)
