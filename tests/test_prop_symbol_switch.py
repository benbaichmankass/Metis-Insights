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
    assert got == {"target": "ETHUSD", "ok": True, "clicked": True, "before": "SOLUSD", "after": "ETHUSD"}
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
