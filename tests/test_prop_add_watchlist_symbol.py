"""add-watchlist-symbol(-dry) (TRADEIFY-GOLIVE, manager GO 2026-10-02 17:39Z).

MEASURED (#15602): typing ETH into tradeify_1's watchlist search shows a result
panel whose header (Symbol / Description) and rows are two SEPARATE tables.
The mode resolves the ONE row whose Symbol cell is exactly the slash form
(ETH/USD -- never ETC/USD or ENA/USD). DRY reports it and clicks nothing; the
armed run makes ONE click, Escapes, and verifies no ticket/dialog opened and
only that symbol was added. Refused unless one-click is OFF; tradeify-only.
"""
import argparse
import inspect
import shutil
import subprocess
from pathlib import Path

import pytest

from src.prop.platform.dxtrade import ADD_SYMBOL_RESOLVE_JS, DXtradeAdapter
from tests import test_prop_platform_dxtrade as _dx

REPO = Path(__file__).resolve().parents[1]
chromium_page = _dx.chromium_page


def _page(*, one_click_on=False, rows=("ETH/USD", "ENA/USD", "ETC/USD"), on_click="add", present=()):
    toggle = (f'<div style="position:relative;height:17px">'
              f'<div data-test-id="one_click_trading" data-value="{"true" if one_click_on else "false"}" '
              f'style="position:absolute;left:0;top:0;width:26px;height:16px"></div>'
              f'<div style="position:absolute;left:{"12px" if one_click_on else "2px"};top:2px;width:12px;height:12px"></div>'
              f'<div style="margin-left:30px">One-click trading</div></div>')
    wl_rows = "".join(f'<tr class="instrument" data-row-id="{i}"><td>{s}</td><td>1</td><td>2</td><td>0</td></tr>'
                      for i, s in enumerate(present))
    desc = {"ETH/USD": "Ethereum vs United States dollar", "ENA/USD": "Ethena vs United States dollar",
            "ETC/USD": "Ethereum Classic vs United States dollar"}
    js_rows = repr([[s, desc.get(s, s + " thing")] for s in rows])
    return f"""<html><body>{toggle}
<div class="widget__container___Ab1 widgetNew__container">
<div class="widget__header"><input id="wl-search" placeholder="Symbol..." data-test-id="watchlist_public_search_9" type="text"></div>
<div class="watchlist-panel">
  <table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th><th>Change</th></tr></thead><tbody id="wl-body">{wl_rows}</tbody></table>
</div>
</div>
<div id="dd" style="display:none">
  <div><div>All</div><div>Cryptocurrencies</div><div>Stocks</div></div>
  <div class="hdr"><table><thead><tr><th><span>Symbol</span></th><th><span>Description</span></th><th><span>Asset Class</span></th></tr></thead><tbody></tbody></table></div>
  <div class="bdy"><table><tbody id="dd-rows"></tbody></table></div>
</div>
<script>
(() => {{  // an IIFE: set_content reuses the realm, so no top-level const
window.__clicks = [];
const ROWS = {js_rows};
document.getElementById('wl-search').addEventListener('keyup', e => {{
  const v = (e.target.value || '').toUpperCase(), body = document.getElementById('dd-rows');
  document.getElementById('dd').style.display = v ? '' : 'none';
  body.innerHTML = '';
  if (!v) return;
  for (const [s, d] of ROWS) {{
    if (!s.startsWith(v) && !d.toUpperCase().includes(v)) continue;
    const tr = document.createElement('tr');
    tr.innerHTML = '<td><mark>' + s.slice(0, v.length) + '</mark><span>' + s.slice(v.length) + '</span></td>'
      + '<td>' + d + '</td><td>Cryptocurrencies</td>';
    tr.cells[0].addEventListener('click', () => {{
      window.__clicks.push(s);
      if ('{on_click}' === 'add') {{
        document.getElementById('wl-body').insertAdjacentHTML('beforeend',
          '<tr class="instrument" data-row-id="9"><td>' + s + '</td><td>1</td><td>2</td><td>0</td></tr>');
      }} else if ('{on_click}' === 'dialog') {{
        document.body.insertAdjacentHTML('beforeend', '<div role="dialog" style="width:200px;height:100px">New Order</div>');
      }}
    }});
    body.appendChild(tr);
  }}
}});
}})();
</script>
</body></html>"""


def _run(chromium_page, html, sym="ETHUSD", arm=False):
    chromium_page.set_content(html)
    return DXtradeAdapter().add_watchlist_symbol(chromium_page, sym, arm=arm, settle_ms=300, key_delay_ms=10,
                                                 after_ms=200)


def _done(chromium_page):
    chromium_page.set_content(_dx.DIVGRID.read_text())


def test_dry_resolves_exactly_eth_usd_and_clicks_nothing(chromium_page):
    got = _run(chromium_page, _page())
    assert got["refused"] is None and got["dry"] == "resolved; nothing clicked"
    res = got["resolve"]
    assert res["ok"] is True and res["n_target"] == 1 and res["ncols"] == 3
    assert res["target_cells"] == ["ETH/USD", "Ethereum vs United States dollar", "Cryptocurrencies"]
    assert [r[0] for r in res["rows"]] == ["ETH/USD", "ENA/USD", "ETC/USD"]   # as MEASURED (#15602)
    assert res["at_point"]["inside_target"] is True and len(res["box"]) == 4
    assert chromium_page.evaluate("window.__clicks") == []
    assert got["clicks"] == [] and got["added"] is False
    assert got["reset"] is True and chromium_page.input_value("#wl-search") == ""
    assert chromium_page.locator("[data-metis-add-target],[data-metis-search-hit]").count() == 0
    _done(chromium_page)


def test_armed_makes_one_click_and_verifies_only_that_symbol_added(chromium_page):
    got = _run(chromium_page, _page(), arm=True)
    assert got["refused"] is None and got["added"] is True
    assert got["clicks"] == ["suggestion:ETH/USD"]
    assert chromium_page.evaluate("window.__clicks") == ["ETH/USD"]
    assert got["watchlist_diff"]["added"] == ["ETHUSD"] and got["watchlist_diff"]["removed"] == []
    assert got["guard"]["form_after"] is False and got["guard"]["dialogs_after"] == 0
    _done(chromium_page)


def test_armed_is_refused_unless_one_click_is_off(chromium_page):
    got = _run(chromium_page, _page(one_click_on=True), arm=True)
    assert got["refused"].startswith("one-click not confirmed OFF")
    assert chromium_page.evaluate("window.__clicks") == [] and chromium_page.input_value("#wl-search") == ""
    _done(chromium_page)


def test_armed_stops_when_the_click_opens_a_dialog(chromium_page):
    got = _run(chromium_page, _page(on_click="dialog"), arm=True)
    assert got["refused"] == "an order ticket or dialog opened after the click (Escaped; stopped)"
    assert got["escaped"] == 2 and got["added"] is False
    assert chromium_page.evaluate("window.__clicks") == ["ETH/USD"]
    _done(chromium_page)


def test_refuses_when_the_target_row_is_not_unique(chromium_page):
    got = _run(chromium_page, _page(rows=("ETH/USD", "ETH/USD", "ETC/USD")), arm=True)
    assert got["refused"] == "2 rows whose Symbol cell is exactly ETH/USD (need exactly 1)"
    assert chromium_page.evaluate("window.__clicks") == []
    _done(chromium_page)


def test_refuses_when_the_target_row_is_absent(chromium_page):
    got = _run(chromium_page, _page(rows=("ETC/USD",)), arm=True)
    assert got["refused"] == "0 rows whose Symbol cell is exactly ETH/USD (need exactly 1)"
    assert chromium_page.evaluate("window.__clicks") == []
    _done(chromium_page)


def test_already_present_symbol_is_a_no_op(chromium_page):
    got = _run(chromium_page, _page(present=("ETH/USD",)), arm=True)
    assert got["already_present"] is True and got["clicks"] == []
    assert chromium_page.evaluate("window.__clicks") == []
    _done(chromium_page)


def test_resolver_masks_every_digit(chromium_page):
    chromium_page.set_content(_page(rows=("ETH/USD", "ETH2/USD")))
    chromium_page.locator("#wl-search").press_sequentially("ETH", delay=5)
    chromium_page.wait_for_timeout(100)
    got = chromium_page.evaluate(ADD_SYMBOL_RESOLVE_JS, ["ETH/USD"])
    assert any(r[0] == "ETH#/USD" for r in got["rows"])
    assert not any(ch.isdigit() for r in got["rows"] for c in r for ch in (c or ""))
    _done(chromium_page)


def test_method_never_presses_enter_and_clicks_only_the_tagged_cell():
    src = inspect.getsource(DXtradeAdapter.add_watchlist_symbol)
    assert '"Enter"' not in src and "get_by_role" not in src and "get_by_text" not in src
    assert src.count(".click(") == 1 and "[data-metis-add-target='1']" in src
    assert ".click(" not in ADD_SYMBOL_RESOLVE_JS and "dispatchEvent" not in ADD_SYMBOL_RESOLVE_JS


@pytest.mark.parametrize("flag,mode", [("add_watchlist_symbol_dry", "add_watchlist_symbol_dry"),
                                       ("add_watchlist_symbol", "add_watchlist_symbol")])
def test_tick_resolves_the_modes(flag, mode):
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    args = argparse.Namespace(probe_ticket="", dry_run=False, watched_click=False, account="tradeify_1",
                              **{flag: "ETHUSD"})
    assert resolve_mode(args, {}) == mode and mode in YIELD_MODES


@pytest.mark.parametrize("mode", ["add-watchlist-symbol-dry", "add-watchlist-symbol"])
def test_action_refuses_both_modes_for_breakout_1(tmp_path, mode):
    from tests.test_prop_executor_clear_halt import STUB_LIB
    d = tmp_path / "ops"
    d.mkdir()
    shutil.copy(REPO / "scripts" / "ops" / "breakout_login_check_action.sh", d / "action.sh")
    (d / "_lib.sh").write_text(STUB_LIB)
    home = tmp_path / "home"
    (home / ".cache" / "metis-prop-browser").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "ACTION_APPLY": mode, "ACTION_SYMBOLS": "ETHUSD"}
    p = subprocess.run(["bash", str(d / "action.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 1 and f"{mode}: refused for breakout_1" in p.stdout + p.stderr


def test_symbol_cell_rendered_with_a_space_after_the_mark_still_resolves(chromium_page):
    # MEASURED live (#15618): innerText of the venue's Symbol cell read
    # "ETH /USD" (<mark>ETH</mark> then "/USD"); the dry run refused 0 rows.
    # Whitespace inside the cell is ignored; ETC/USD and ENA/USD still never match.
    html = _page().replace("'</mark><span>'", "'</mark> <span>'")
    got = _run(chromium_page, html)
    assert got["refused"] is None and got["resolve"]["n_target"] == 1
    assert got["resolve"]["rows"][0][0] == "ETH /USD"          # reported as the venue renders it
    assert got["resolve"]["at_point"]["inside_target"] is True
    assert chromium_page.evaluate("window.__clicks") == []
    _done(chromium_page)
