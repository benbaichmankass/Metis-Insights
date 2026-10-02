"""widget-menu-probe (TRADEIFY-GOLIVE, operator decision ~21:50Z 2026-10-01):
ONE click on the add-widget "+", a masked dump of the menu, Escape, and a
re-read that the layout is unchanged. Never a menu item, never an order /
price / delete control."""
from __future__ import annotations

import argparse

from src.prop.platform.dxtrade import DXtradeAdapter
from tests.test_prop_instrument_info_probe import browser  # noqa: F401  (fixture reuse)


def page(browser, *, one_click_on=False, escape_closes=True, add_button=True, adds_widget=False):  # noqa: F811
    toggle = (f'<div style="position:relative;height:17px">'
              f'<div data-test-id="one_click_trading" data-value="{"true" if one_click_on else "false"}" '
              f'style="position:absolute;left:0;top:0;width:26px;height:16px"></div>'
              f'<div style="position:absolute;left:{"12px" if one_click_on else "2px"};top:2px;width:12px;height:12px"></div>'
              f'<div style="margin-left:30px">One-click trading</div></div>')
    html = f"""<html><body>{toggle}
      <ul><li data-test-id="workspace_current"><span data-test-id="workspace_name">My Trading Account</span>
          <button data-test-id="workspace_close_button" onclick="window.__del=(window.__del||0)+1">x</button></li></ul>
      <div class="panel" style="margin-top:20px">
        <div data-test-id="widget_tab">Positions</div>
        {'<button data-test-id="widget_tab_add_button" style="width:20px;height:20px">+</button>' if add_button else ''}
        <button data-test-id="widget_close_button" onclick="window.__del=(window.__del||0)+1">x</button>
        <button data-test-id="BUY" onclick="window.__trade=(window.__trade||0)+1">Buy 12345.6</button>
        <table><tr><td>row</td></tr></table>
      </div>
      <div id="menu" style="display:none">
        <div role="menuitem" onclick="window.__item=(window.__item||0)+1">Watchlist</div>
        <div role="menuitem" onclick="window.__item=(window.__item||0)+1">New Order</div>
        <div role="menuitem" onclick="window.__item=(window.__item||0)+1">Chart 2</div>
        <div role="menuitem" data-test-id="user_profile">Jane Doe</div>
      </div>
      <script>
        const add = document.querySelector('[data-test-id=widget_tab_add_button]');
        if (add) add.addEventListener('click', () => {{
          window.__adds = (window.__adds || 0) + 1;
          document.getElementById('menu').style.display = 'block';
          if ({'true' if adds_widget else 'false'}) {{
            const t = document.createElement('div'); t.setAttribute('data-test-id', 'widget_tab'); t.textContent = 'X';
            document.querySelector('.panel').appendChild(t);
          }}
        }});
        document.addEventListener('keydown', e => {{
          window.__keys = (window.__keys || []).concat([e.key]);
          if (e.key === 'Escape' && {'true' if escape_closes else 'false'}) document.getElementById('menu').style.display = 'none';
        }});
      </script></body></html>"""
    p = browser.new_page()
    p.set_content(html)
    return p


def state(p):
    return p.evaluate("({adds: window.__adds||0, item: window.__item||0, trade: window.__trade||0, del: window.__del||0,"
                      " keys: window.__keys||[], tags: document.querySelectorAll('[data-metis-wadd]').length})")


def probe(p):
    return DXtradeAdapter(timeout_ms=3_000).widget_menu_probe(p, settle_ms=100)


def test_measures_the_menu_then_escapes_and_restores(browser):  # noqa: F811
    p = page(browser)
    got, st = probe(p), None
    st = state(p)
    p.close()
    texts = [i["text"] for i in got["menu"]["items"]]
    assert got["refused"] is None and got["clicked"] is True and got["restored"] is True
    assert "Watchlist" in texts and "New Order" in texts and "Chart #" in texts
    assert "Jane Doe" not in texts  # personal test id dropped
    assert got["before"]["workspace"] == "My Trading Account"
    assert st == {"adds": 1, "item": 0, "trade": 0, "del": 0, "keys": ["Escape"], "tags": 0}


def test_refused_with_nothing_clicked_when_one_click_reads_on(browser):  # noqa: F811
    p = page(browser, one_click_on=True)
    got = probe(p)
    st = state(p)
    p.close()
    assert got["refused"] == "one-click reads ON" and got["clicked"] is False
    assert st["adds"] == 0 and st["item"] == 0 and st["trade"] == 0


def test_refused_without_an_add_button(browser):  # noqa: F811
    p = page(browser, add_button=False)
    got = probe(p)
    p.close()
    assert got["refused"] == "no visible widget_tab_add_button" and got["clicked"] is False


def test_menu_left_open_is_not_restored(browser):  # noqa: F811
    p = page(browser, escape_closes=False)
    got = probe(p)
    st = state(p)
    p.close()
    assert got["restored"] is False and got["menu_left_open"] is True and got["escapes"] == 2
    assert st["item"] == 0 and st["trade"] == 0 and st["del"] == 0


def test_a_click_that_changes_the_layout_is_not_restored(browser):  # noqa: F811
    p = page(browser, adds_widget=True)
    got = probe(p)
    p.close()
    assert got["restored"] is False
    assert got["after"]["widget_tabs"] == got["before"]["widget_tabs"] + 1


def test_tick_resolves_the_mode():
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    args = argparse.Namespace(probe_ticket="", dry_run=False, watched_click=False, widget_menu_probe=True,
                              account="tradeify_1")
    assert resolve_mode(args, {}) == "widget_menu_probe"
    assert "widget_menu_probe" in YIELD_MODES
