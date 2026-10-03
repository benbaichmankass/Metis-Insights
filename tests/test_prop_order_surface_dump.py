"""order-surface-dump (TRADEIFY-GOLIVE, manager 2026-10-02 22:25Z).

probe-ticket ETHUSD #15696 found no order form on tradeify_1. This READ-ONLY
mode reports the symbol's watchlist row cells (header, tid, class,
title/aria, children, elementFromPoint) and every order-surface term on the
current page. It clicks, hovers, focuses and types nothing."""
import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

from src.prop.platform.dxtrade import ORDER_SURFACE_DUMP_JS, DXtradeAdapter
from tests import test_prop_platform_dxtrade as _dx

REPO = Path(__file__).resolve().parents[1]
chromium_page = _dx.chromium_page

PAGE = """<html><body>
<div style="position:relative;height:17px">
 <div data-test-id="one_click_trading" data-value="false" style="position:absolute;left:0;top:0;width:26px;height:16px"></div>
 <div style="position:absolute;left:2px;top:2px;width:12px;height:12px"></div><div style="margin-left:30px">One-click trading</div></div>
<ul><li data-test-id="workspace_current"><span data-test-id="workspace_name">My Trading Account</span></li>
    <li><span data-test-id="workspace_name">Technical Analysis</span></li></ul>
<div class="widget__container___Ab1">
 <table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th><th>Change</th><th>Chg%</th><th>Description</th></tr></thead></table>
 <table data-test-id="table"><tbody>
  <tr class="instrument" data-row-id="0">
   <td class="cell sym9">ETH/USD</td>
   <td class="cell bid"><span data-test-id="bid_price" title="Double-click to trade">2,431.55</span></td>
   <td class="cell ask"><span data-test-id="ask_price">2,432.05</span></td>
   <td>1.2</td><td>0.05%</td><td>Ethereum vs United States dollar</td></tr>
  <tr class="instrument" data-row-id="1"><td>SOL/USD</td><td>1</td><td>2</td><td>0</td><td>0</td><td>Solana</td></tr>
 </tbody></table>
</div>
<button data-test-id="new_order_button" style="display:none">New Order</button>
<div class="chart-panel"><span>Sell</span><span>Buy</span></div>
<div class="user-profile"><span>Buy more coffee</span></div>
<script>window.__clicks = 0; document.addEventListener('click', () => window.__clicks++, true);
document.addEventListener('dblclick', () => window.__clicks++, true);</script>
</body></html>"""


def test_dump_reports_the_row_cells_and_order_terms_without_clicking(chromium_page):
    chromium_page.set_content(PAGE)
    got = DXtradeAdapter().order_surface_dump(chromium_page, "ETHUSD")
    assert got["one_click"]["state"] == "off"
    s = got["surface"]
    assert s["target"] == "ETH/USD" and s["n_rows"] == 1
    assert s["headers"] == ["Symbol", "Bid", "Ask", "Change", "Chg%", "Description"]
    cells = s["rows"][0]["cells"]
    bid = cells[1]
    assert bid["header"] == "Bid" and bid["cls"] == ["cell", "bid"]
    assert bid["children"][0]["tid"] == "bid_price" and bid["children"][0]["title"] == "Double-click to trade"
    assert bid["text"] == "#,###.##" and bid["at_point"]["inside"] is True     # every digit masked
    assert cells[0]["cls"] == ["cell", "sym#"]
    terms = {(t["tid"], t["text"], t["visible"]) for t in s["terms"]}
    assert ("new_order_button", "New Order", False) in terms                    # hidden, flagged
    assert any(t["text"] == "Buy" and t["visible"] for t in s["terms"])
    assert not any("coffee" in (t["text"] or "") for t in s["terms"])           # personal node skipped
    assert s["workspace"] == "My Trading Account" and "Technical Analysis" in s["workspaces"]
    assert chromium_page.evaluate("window.__clicks") == 0
    chromium_page.set_content(_dx.DIVGRID.read_text())


def test_js_never_clicks_dispatches_or_focuses():
    for bad in (".click(", "dispatchEvent", ".focus(", ".value"):
        assert bad not in ORDER_SURFACE_DUMP_JS


def test_tick_resolves_the_mode():
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    args = argparse.Namespace(probe_ticket="", dry_run=False, watched_click=False, account="tradeify_1",
                              order_surface_dump="ETHUSD")
    assert resolve_mode(args, {}) == "order_surface_dump" and "order_surface_dump" in YIELD_MODES


@pytest.mark.parametrize("acct,symbols,msg", [
    ("breakout_1", "ETHUSD", "order-surface-dump: refused for breakout_1"),
])
def test_action_refusals(tmp_path, acct, symbols, msg):
    from tests.test_prop_executor_clear_halt import STUB_LIB
    d = tmp_path / "ops"
    d.mkdir()
    shutil.copy(REPO / "scripts" / "ops" / "breakout_login_check_action.sh", d / "action.sh")
    (d / "_lib.sh").write_text(STUB_LIB)
    home = tmp_path / "home"
    (home / ".cache" / "metis-prop-browser").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "ACTION_APPLY": "order-surface-dump",
           "ACTION_SYMBOLS": symbols}
    if acct != "breakout_1":
        env["ACCOUNT_ID"] = acct
    p = subprocess.run(["bash", str(d / "action.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 1 and msg in p.stdout + p.stderr
