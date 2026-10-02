"""watchlist-submenu-probe (TRADEIFY-GOLIVE): add-watchlist-widget #15373
found the menu's "Watchlist" entry opens a Private/Public SUBMENU. This
measures it: "+", "Watchlist" (both measured), then HOVER -- never click --
each submenu entry, dump, Escape, layout re-read."""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from src.prop.platform.dxtrade import DXtradeAdapter
from tests.test_prop_instrument_info_probe import browser  # noqa: F401  (fixture reuse)

REPO = Path(__file__).resolve().parents[1]
MENU = ["Chart", "Positions", "Watchlist", "Orders", "Order History", "Trade History", "Cash Movements",
        "Messages", "Market Depth", "Time and Sales", "Alerts", "Trading Journal", "Trading Dashboard",
        "TradingView"]


def page(browser, *, one_click_on=False, escape_closes=True):  # noqa: F811
    toggle = (f'<div style="position:relative;height:17px">'
              f'<div data-test-id="one_click_trading" data-value="{"true" if one_click_on else "false"}" '
              f'style="position:absolute;left:0;top:0;width:26px;height:16px"></div>'
              f'<div style="position:absolute;left:{"12px" if one_click_on else "2px"};top:2px;width:12px;height:12px"></div>'
              f'<div style="margin-left:30px">One-click trading</div></div>')
    items = "".join(f'<div class="mi"><span data-label="{m}">{m}</span></div>' for m in MENU)
    html = f"""<html><body>{toggle}
      <ul><li data-test-id="workspace_current"><span data-test-id="workspace_name">My Trading Account</span>
          <button data-test-id="workspace_close_button" onclick="window.__del=(window.__del||0)+1">x</button></li></ul>
      <div class="panel"><div data-test-id="widget_tab">Positions</div>
        <button data-test-id="widget_tab_add_button" style="width:20px;height:20px">+</button>
        <button data-test-id="widget_close_button" onclick="window.__del=(window.__del||0)+1">x</button></div>
      <div id="menu" style="display:none">{items}</div>
      <div id="sub" style="display:none"><span data-s="Private">Private</span><span data-s="Public">Public</span></div>
      <div id="priv" style="display:none"><span>Default Watchlist</span><span>Create New</span></div>
      <div id="pub" style="display:none"><span>Crypto 24</span></div>
      <script>
        window.__clicked = [];
        const show = (id, on) => document.getElementById(id).style.display = on ? 'block' : 'none';
        document.querySelector('[data-test-id=widget_tab_add_button]').addEventListener('click', () => show('menu', 1));
        document.querySelectorAll('#menu span').forEach(sp => sp.addEventListener('click', () => {{
          window.__clicked.push(sp.dataset.label); if (sp.dataset.label === 'Watchlist') show('sub', 1); }}));
        document.querySelectorAll('#sub span, #priv span, #pub span').forEach(sp =>
          sp.addEventListener('click', () => window.__clicked.push('SUB:' + sp.textContent)));
        document.querySelector('[data-s=Private]').addEventListener('mouseenter', () => {{ show('priv', 1); show('pub', 0); }});
        document.querySelector('[data-s=Public]').addEventListener('mouseenter', () => {{ show('pub', 1); show('priv', 0); }});
        document.addEventListener('keydown', e => {{
          window.__keys = (window.__keys || []).concat([e.key]);
          if (e.key === 'Escape' && {'true' if escape_closes else 'false'}) ['menu','sub','priv','pub'].forEach(i => show(i, 0));
        }});
      </script></body></html>"""
    p = browser.new_page()
    p.set_content(html)
    return p


def state(p):
    return p.evaluate("({clicked: window.__clicked, del: window.__del||0, keys: window.__keys||[],"
                      " tags: document.querySelectorAll('[data-metis-wadd],[data-metis-wpick],[data-metis-wsub]').length})")


def probe(p):
    return DXtradeAdapter(timeout_ms=3_000).watchlist_submenu_probe(p, settle_ms=100)


def test_hovers_each_entry_and_dumps_what_it_opened_clicking_nothing_past_watchlist(browser):  # noqa: F811
    p = page(browser)
    got = probe(p)
    st = state(p)
    p.close()
    assert got["refused"] is None and got["restored"] is True
    assert got["clicks"] == ["add_widget_plus", "menu:Watchlist"]
    priv = [i["text"] for i in got["hovers"]["Private"]["appeared"]["items"]]
    pub = [i["text"] for i in got["hovers"]["Public"]["appeared"]["items"]]
    assert "Default Watchlist" in priv and "Create New" in priv
    assert pub == ["Crypto ##"]  # digits masked
    assert st == {"clicked": ["Watchlist"], "del": 0, "keys": ["Escape"], "tags": 0}


def test_refused_with_nothing_clicked_unless_one_click_reads_off(browser):  # noqa: F811
    p = page(browser, one_click_on=True)
    got = probe(p)
    st = state(p)
    p.close()
    assert got["refused"].startswith("one-click not confirmed OFF") and got["clicks"] == []
    assert st["clicked"] == []


def test_a_menu_that_will_not_close_is_not_restored(browser):  # noqa: F811
    p = page(browser, escape_closes=False)
    got = probe(p)
    st = state(p)
    p.close()
    assert got["restored"] is False and got["menu_left_open"] is True and got["escapes"] == 3
    assert st["clicked"] == ["Watchlist"] and st["del"] == 0


def test_tick_resolves_the_mode():
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    args = argparse.Namespace(probe_ticket="", dry_run=False, watched_click=False, watchlist_submenu_probe=True,
                              account="tradeify_1")
    assert resolve_mode(args, {}) == "watchlist_submenu_probe"
    assert "watchlist_submenu_probe" in YIELD_MODES


def test_action_refuses_the_probe_for_breakout_1(tmp_path):
    from tests.test_prop_executor_clear_halt import STUB_LIB
    d = tmp_path / "ops"
    d.mkdir()
    shutil.copy(REPO / "scripts" / "ops" / "breakout_login_check_action.sh", d / "action.sh")
    (d / "_lib.sh").write_text(STUB_LIB)
    home = tmp_path / "home"
    (home / ".cache" / "metis-prop-browser").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "ACTION_APPLY": "watchlist-submenu-probe"}
    p = subprocess.run(["bash", str(d / "action.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 1
    assert "watchlist-submenu-probe: refused for breakout_1" in p.stdout + p.stderr
