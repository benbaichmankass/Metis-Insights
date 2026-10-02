"""add-watchlist-widget + the feed's layout canary (TRADEIFY-GOLIVE option B
step 2; operator ~21:50Z 2026-10-01 "Runner does it"; menu MEASURED by
widget-menu-probe #15350). Exactly two clicks -- the "+" and the measured
"Watchlist" entry -- and never a delete / order / price control."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from src.prop.platform.dxtrade import WIDGET_MENU_MEASURED, DXtradeAdapter
from tests.test_prop_instrument_info_probe import browser  # noqa: F401  (fixture reuse)

REPO = Path(__file__).resolve().parents[1]
MENU = ["Chart", "Positions", "Watchlist", "Orders", "Order History", "Trade History", "Cash Movements",
        "Messages", "Market Depth", "Time and Sales", "Alerts", "Trading Journal", "Trading Dashboard",
        "TradingView"]
WATCHLIST = ('<div data-test-id="widget_tab">Watchlist</div>'
             '<table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead></table>'
             '<table><tbody>'
             '<tr class="instrument" data-row-id="0"><td>ETH/USD</td><td><button>1.1</button></td><td><button>1.2</button></td></tr>'
             '<tr class="instrument" data-row-id="1"><td>BTC/USD</td><td><button>2.1</button></td><td><button>2.2</button></td></tr>'
             '</tbody></table>')


def page(browser, *, one_click_on=False, menu=MENU, workspace="My Trading Account",  # noqa: F811
         present=False, watchlist_opens="widget"):
    toggle = (f'<div style="position:relative;height:17px">'
              f'<div data-test-id="one_click_trading" data-value="{"true" if one_click_on else "false"}" '
              f'style="position:absolute;left:0;top:0;width:26px;height:16px"></div>'
              f'<div style="position:absolute;left:{"12px" if one_click_on else "2px"};top:2px;width:12px;height:12px"></div>'
              f'<div style="margin-left:30px">One-click trading</div></div>')
    items = "".join(f'<div class="mi"><span data-label="{m}">{m}</span></div>' for m in menu)
    html = f"""<html><body>{toggle}
      <ul><li data-test-id="workspace_current"><span data-test-id="workspace_name">{workspace}</span>
          <button data-test-id="workspace_close_button" onclick="window.__del=(window.__del||0)+1">x</button></li></ul>
      <div class="panel" id="top">
        <div data-test-id="widget_tab">Positions</div>
        <button data-test-id="widget_tab_add_button" style="width:20px;height:20px">+</button>
        <button data-test-id="widget_close_button" onclick="window.__del=(window.__del||0)+1">x</button>
        {WATCHLIST if present else ''}
      </div>
      <div id="menu" style="display:none">{items}</div>
      <div id="sub" style="display:none"><span data-s="Private">Private</span><span data-s="Public">Public</span></div>
      <div id="other" style="display:none"><span>Default Watchlist</span><span>Create New</span></div>
      <div id="third" style="display:none"><span>Default Watchlist</span><span>Create New</span></div>
      <script>
        window.__picked = [];
        document.querySelector('[data-test-id=widget_tab_add_button]').addEventListener('click', () => {{
          window.__adds = (window.__adds || 0) + 1;
          document.getElementById('menu').style.display = 'block';
        }});
        document.querySelectorAll('#menu span').forEach(sp => sp.addEventListener('click', () => {{
          window.__picked.push(sp.dataset.label);
          document.getElementById('menu').style.display = 'none';
          if (sp.dataset.label !== 'Watchlist') return;
          if ('{watchlist_opens}' === 'widget') document.getElementById('top').insertAdjacentHTML('beforeend', `{WATCHLIST}`);
          else if ('{watchlist_opens}' === 'other') document.getElementById('other').style.display = 'block';
          else document.getElementById('sub').style.display = 'block';
        }}));
        document.querySelectorAll('#sub span').forEach(sp => sp.addEventListener('click', () => {{
          window.__picked.push('SUB:' + sp.dataset.s);
          document.getElementById('sub').style.display = 'none';
          document.getElementById('menu').style.display = 'none';
          if ('{watchlist_opens}' === 'submenu') document.getElementById('top').insertAdjacentHTML('beforeend', `{WATCHLIST}`);
          else if ('{watchlist_opens}' === 'third') document.getElementById('third').style.display = 'block';
        }}));
        document.querySelectorAll('#other span, #third span').forEach(sp => sp.addEventListener('click', () =>
          window.__picked.push('X:' + sp.textContent)));
        document.addEventListener('keydown', e => {{
          window.__keys = (window.__keys || []).concat([e.key]);
          if (e.key === 'Escape') ['menu', 'sub', 'other', 'third'].forEach(i =>
            document.getElementById(i).style.display = 'none');
        }});
      </script></body></html>"""
    p = browser.new_page()
    p.set_content(html)
    return p


def state(p):
    return p.evaluate("({adds: window.__adds||0, picked: window.__picked, del: window.__del||0, keys: window.__keys||[],"
                      " tags: document.querySelectorAll('[data-metis-wadd],[data-metis-wpick]').length})")


def add(p):
    return DXtradeAdapter(timeout_ms=3_000).add_watchlist_widget(p, settle_ms=100, ready_ms=1_500)


def test_adds_the_watchlist_with_exactly_two_measured_clicks(browser):  # noqa: F811
    p = page(browser)
    got = add(p)
    st = state(p)
    p.close()
    assert got["refused"] is None and got["added"] is True
    assert got["clicks"] == ["add_widget_plus", "menu:Watchlist"]
    assert got["watchlist_after"]["symbols"] == ["BTCUSD", "ETHUSD"]
    assert st == {"adds": 1, "picked": ["Watchlist"], "del": 0, "keys": [], "tags": 0}


def test_refused_with_nothing_clicked_unless_one_click_reads_off(browser):  # noqa: F811
    p = page(browser, one_click_on=True)
    got = add(p)
    st = state(p)
    p.close()
    assert got["refused"].startswith("one-click not confirmed OFF") and got["clicks"] == []
    assert st["adds"] == 0 and st["picked"] == []


def test_noop_when_a_watchlist_is_already_present(browser):  # noqa: F811
    p = page(browser, present=True)
    got = add(p)
    st = state(p)
    p.close()
    assert got["already_present"] is True and got["clicks"] == [] and st["adds"] == 0


def test_refused_on_another_workspace(browser):  # noqa: F811
    p = page(browser, workspace="Trading Dashboard")
    got = add(p)
    st = state(p)
    p.close()
    assert "not 'My Trading Account'" in got["refused"] and st["adds"] == 0


def test_an_unmeasured_menu_is_escaped_with_nothing_picked(browser):  # noqa: F811
    p = page(browser, menu=["Watchlist", "Something Else"])
    got = add(p)
    st = state(p)
    p.close()
    assert got["refused"] == "menu did not match the measured menu (nothing picked)"
    assert got["pick"]["missing_siblings"] and got["clicks"] == ["add_widget_plus"]
    assert st["picked"] == [] and st["del"] == 0 and "Escape" in st["keys"]


def test_the_measured_submenu_adds_the_watchlist_via_private(browser):  # noqa: F811
    # #15373/#15390: "Watchlist" opens Private/Public; Private is clicked.
    p = page(browser, watchlist_opens="submenu")
    got = add(p)
    st = state(p)
    p.close()
    assert got["added"] is True and got["clicks"] == ["add_widget_plus", "menu:Watchlist", "submenu:Private"]
    assert got["watchlist_after"]["symbols"] == ["BTCUSD", "ETHUSD"]
    assert st["picked"] == ["Watchlist", "SUB:Private"] and st["del"] == 0 and st["keys"] == []


def test_a_third_level_after_private_is_reported_never_clicked_into(browser):  # noqa: F811
    p = page(browser, watchlist_opens="third")
    got = add(p)
    st = state(p)
    p.close()
    assert got["added"] is False and got["watchlist_after"]["readable"] is False
    assert "Default Watchlist" in [i["text"] for i in got["new_after"]["items"]]
    assert st["picked"] == ["Watchlist", "SUB:Private"] and st["del"] == 0
    assert st["keys"] == ["Escape"] and got["menu_left_open"] is False


def test_an_unmeasured_submenu_is_escaped_with_nothing_picked(browser):  # noqa: F811
    p = page(browser, watchlist_opens="other")
    got = add(p)
    st = state(p)
    p.close()
    assert got["refused"] == "Watchlist submenu did not match the measured submenu (nothing picked)"
    assert got["clicks"] == ["add_widget_plus", "menu:Watchlist"]
    assert st["picked"] == ["Watchlist"] and st["del"] == 0 and "Escape" in st["keys"]


def test_measured_menu_constant_matches_the_measurement():
    assert set(WIDGET_MENU_MEASURED) <= set(MENU)


def test_tick_resolves_the_mode():
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    args = argparse.Namespace(probe_ticket="", dry_run=False, watched_click=False, add_watchlist_widget=True,
                              account="tradeify_1")
    assert resolve_mode(args, {}) == "add_watchlist_widget"
    assert "add_watchlist_widget" in YIELD_MODES


class _FakeAdapter:
    def __init__(self, reads):
        self.reads = list(reads)

    def watchlist_symbols(self, page):
        return self.reads.pop(0)


class _FakePage:
    def wait_for_timeout(self, ms):
        pass


def test_layout_line_ok_after_a_slow_render_and_missing_with_its_reason():
    from scripts.prop.breakout_login_check import layout_watchlist_line
    slow = _FakeAdapter([{"readable": False, "why": "0 Symbol/Bid/Ask tables (need exactly 1)"},
                         {"readable": True, "symbols": ["ETHUSD", "SOLUSD"]}])
    assert layout_watchlist_line(slow, _FakePage()) == "layout_watchlist: ok n=2 symbols=ETHUSD,SOLUSD"
    gone = _FakeAdapter([{"readable": False, "why": "0 Symbol/Bid/Ask tables (need exactly 1)"}] * 3)
    assert layout_watchlist_line(gone, _FakePage()) == \
        "layout_watchlist: MISSING (0 Symbol/Bid/Ask tables (need exactly 1))"


def _canary_block() -> str:
    s = (REPO / "scripts/ops/prop_feed_tick.sh").read_text()
    a = s.index('LAYOUT_MARK="${STATE_DIR}/layout-missing"')
    b = s.index('case "${rc}" in')
    return s[a:b]


def _run_canary(tmp_path: Path, line: str) -> str:
    out = tmp_path / "out.txt"
    out.write_text(f"session: reused\n{line}\n")
    script = f"""set -euo pipefail
STATE_DIR={tmp_path}; OUT={out}; ACCOUNT=tradeify_1; REPO_DIR={REPO}; PING_PY={tmp_path}/ping.sh
log() {{ echo "LOG $*"; }}
record_audit() {{ echo "AUDIT $*"; }}
{_canary_block()}
echo END"""
    (tmp_path / "ping.sh").write_text("#!/bin/sh\necho PING >> \"$(dirname \"$0\")/pings\"\n")
    (tmp_path / "ping.sh").chmod(0o755)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout


def test_feed_canary_pings_once_on_missing_and_logs_recovery(tmp_path):
    miss = "layout_watchlist: MISSING (0 Symbol/Bid/Ask tables (need exactly 1))"
    first = _run_canary(tmp_path, miss)
    second = _run_canary(tmp_path, miss)
    assert "first tick missing; pinging once" in first and "first tick missing" not in second
    assert (tmp_path / "pings").read_text().count("PING") == 1
    back = _run_canary(tmp_path, "layout_watchlist: ok n=3 symbols=ETHUSD,SOLUSD,XRPUSD")
    assert "watchlist back" in back and not (tmp_path / "layout-missing").exists()
    assert _run_canary(tmp_path, "").endswith("END\n")  # no line: nothing decided


def test_feed_canary_is_off_for_breakout_1():
    s = (REPO / "scripts/ops/prop_feed_tick.sh").read_text()
    assert '[ "${ACCOUNT}" != "breakout_1" ] && CANARY_ARG="--layout-canary"' in s


def test_action_refuses_add_watchlist_widget_for_breakout_1(tmp_path):
    # Manager review of #15354: no layout change on breakout_1's live terminal.
    import shutil

    from tests.test_prop_executor_clear_halt import STUB_LIB
    d = tmp_path / "ops"
    d.mkdir()
    shutil.copy(REPO / "scripts" / "ops" / "breakout_login_check_action.sh", d / "action.sh")
    (d / "_lib.sh").write_text(STUB_LIB)
    home = tmp_path / "home"
    (home / ".cache" / "metis-prop-browser").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "ACTION_APPLY": "add-watchlist-widget"}
    p = subprocess.run(["bash", str(d / "action.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 1
    assert "add-watchlist-widget: refused for breakout_1" in p.stdout + p.stderr
