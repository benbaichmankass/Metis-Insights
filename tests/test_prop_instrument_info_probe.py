"""PROP-ETH-DOM (2026-09-30): the operator-authorized instrument INFO-PANEL
probe (``DXtradeAdapter.probe_instrument_info``), exercised in real Chromium
against a fixture page built from the MEASURED DOM shapes:

- the watchlist widget (issue #14551): Symbol/Bid/Ask header table,
  ``tr.instrument`` rows; Bid/Ask cells hold price BUTTONS here, so a click
  on one would be caught (``window.__trade``);
- a row DOUBLE-click opens the order ticket (``open_order_ticket``), caught
  as ``window.__ticket``;
- the widget-header toolbar with ``[data-test-id=symbol_input]`` (the linked
  symbol) beside ``[data-test-id=instrument_info_button]`` (issue #14551);
- the account metrics (``Used Margin``), the visible Orders table and the
  ``One-click trading`` toggle (issue #14612's dump).

The info panel's real DOM is UNMEASURED; the fixture's panel is a stand-in
that exercises the identity checks, not a claim about the live layout.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.prop.platform.base import AccountSnapshot
from src.prop.platform.dxtrade import (DXtradeAdapter, info_probe_flat_guard,
                                     info_probe_restore_latch_reason)

REPO = Path(__file__).resolve().parents[1]

SPECS = {"BTCUSD": "0.001", "ETHUSD": "0.01", "SOLUSD": "0.1", "AVAXUSD": "1"}


def page_html(*, margin="$0", order_rows="", one_click="", panel_names=None, dialog_on="",
              close_btn=True, no_close=False, sym_cell_extra="", link_breaks_for="",
              panel_role="", orders_hidden=False, one_click_unreadable=False, panel_extra="",
              hover_button_for="", outside_table="", orders_headers=None, no_panel_for="",
              toggle_attrs=""):
    rows = "".join(
        f'<tr class="instrument" data-row-id="{i}"><td class="sym">{s}{sym_cell_extra if s == "SOLUSD" else ""}</td>'
        f'<td><button class="px" onclick="window.__trade=(window.__trade||0)+1">100.1</button></td>'
        f'<td><button class="px" onclick="window.__trade=(window.__trade||0)+1">100.2</button></td></tr>'
        for i, s in enumerate(["ETHUSD", "SOLUSD", "BTCUSD", "AVAXUSD"]))
    toggle = (f'<div {toggle_attrs}><span>One-click trading</span></div>' if one_click_unreadable else
              f'<label><input type="checkbox" {one_click}><span>One-click trading</span></label>')
    heads = "".join(f"<th>{h}</th>" for h in (orders_headers or ["Symbol", "Side", "Order Type", "Order ID"]))
    return f"""<html><body>
<div class="metrics"><div><span>Balance</span><div>$5,024</div></div>
  <div><span>Used Margin</span><div>{margin}</div></div>
  <div><span>Free Margin</span><div>$4,724</div></div>
  {toggle}</div>
<div class="widget__container___Ab1 widgetNew__container">
  <table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead><tbody>{rows}</tbody></table>
</div>
<div class="widget-header__container"><div class="toolbar__item">
  <input data-test-id="symbol_input" placeholder="Symbol..." value="SOLUSD">
  <div class="instrument-details"><button data-test-id="instrument_info_button"><svg><use href="#icon-info"></use></svg></button></div>
</div></div>
<div class="widget__container___Or1 widgetNew__container orders-widget" {'style="display:none"' if orders_hidden else ''}>
  <button data-test-id="widget_menu_ORDERS">Orders</button>
  <table><thead><tr>{heads}</tr></thead><tbody>{order_rows}</tbody></table></div>
{outside_table}
<script>
const SPECS = {json.dumps(SPECS)};
const PANEL_NAMES = {json.dumps(panel_names)};
window.__clicks = [];
document.addEventListener('click', e => window.__clicks.push(
  (e.target.closest('[data-test-id]') || e.target).getAttribute('data-test-id') || e.target.className || e.target.tagName), true);
document.querySelectorAll('tr.instrument').forEach(tr => {{
  tr.addEventListener('dblclick', () => {{ window.__ticket = 1; }});
  const cell = tr.querySelector('td.sym');
  if (cell.firstChild.textContent.trim() === {json.dumps(hover_button_for)})
    cell.addEventListener('mouseenter', () => {{
      if (!cell.querySelector('.hoverbtn')) {{
        const b = document.createElement('button'); b.className = 'hoverbtn';
        b.addEventListener('click', () => {{ window.__trade = (window.__trade || 0) + 1; }});
        cell.appendChild(b);
      }}
    }});
  tr.querySelector('td.sym').addEventListener('click', () => {{
    const s = tr.querySelector('td.sym').firstChild.textContent.trim();
    if (s === {json.dumps(dialog_on)}) {{
      const d = document.createElement('div'); d.setAttribute('role', 'dialog'); d.textContent = 'Are you sure?';
      d.style.cssText = 'width:200px;height:100px'; document.body.appendChild(d); return;
    }}
    if (s === {json.dumps(link_breaks_for)} && document.querySelector('[data-test-id=symbol_input]').value !== s) return;
    document.querySelector('[data-test-id=symbol_input]').value = s;
  }});
}});
document.querySelector('[data-test-id=instrument_info_button]').addEventListener('click', () => {{
  const open = document.querySelector('.info-panel');
  if (open) {{ if (!{json.dumps(no_close)}) open.remove(); return; }}
  const s = document.querySelector('[data-test-id=symbol_input]').value;
  if (s === {json.dumps(no_panel_for)}) return;
  const name = PANEL_NAMES ? PANEL_NAMES : s;
  const p = document.createElement('div'); p.className = 'info-panel';
  if ({json.dumps(panel_role)}) p.setAttribute('role', {json.dumps(panel_role)});
  p.style.cssText = 'width:300px;height:200px';
  p.innerHTML = '<div class="title">' + name + '</div><div>Lot size</div><div>' + (SPECS[s] || '?')
    + '</div><div>Tick size</div><div>0.01</div>' + {json.dumps(panel_extra)}
    + ({json.dumps(close_btn)} ? '<button class="close" aria-label="Close">x</button>' : '');
  const btn = p.querySelector('button.close');
  if (btn) btn.addEventListener('click', () => {{ if (!{json.dumps(no_close)}) p.remove(); }});
  document.body.appendChild(p);
}});
// Escape dismisses an open info panel (a refused panel is closed this way,
// never by a click).
document.addEventListener('keydown', e => {{
  window.__keys = (window.__keys || []).concat([e.key]);
  const open = document.querySelector('.info-panel');
  if (e.key === 'Escape' && open && !{json.dumps(no_close)}) open.remove();
}});
</script></body></html>"""


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


def run(browser, html, symbols=("BTCUSD", "ETHUSD", "AVAXUSD"), click=True):
    p = browser.new_page()
    p.set_content(html)
    got = DXtradeAdapter(timeout_ms=3_000).probe_instrument_info(p, list(symbols), click=click, settle_ms=50)
    state = p.evaluate("({clicks: window.__clicks, trade: window.__trade || 0, ticket: window.__ticket || 0,"
                       " keys: window.__keys || [],"
                       " linked: document.querySelector('[data-test-id=symbol_input]').value,"
                       " tags: document.querySelectorAll('[data-metis-row-cell],[data-metis-info-btn],"
                       "[data-metis-sym-input],[data-metis-info-panel],[data-metis-close]').length,"
                       " panels: document.querySelectorAll('.info-panel').length})")
    p.close()
    return got, state


def never_traded(state):
    assert state["trade"] == 0 and state["ticket"] == 0
    assert all(c not in ("px",) for c in state["clicks"])


# ── the pure guards ────────────────────────────────────────────────────────


def test_flat_guard_refuses_unless_it_could_look_and_saw_flat():
    empty = {"found": True, "n_rows": 0}
    assert info_probe_flat_guard(AccountSnapshot(margin_used=0.0), empty) is None
    assert "not readable" in info_probe_flat_guard(AccountSnapshot(), empty)
    assert "12.5" in info_probe_flat_guard(AccountSnapshot(margin_used=12.5), empty)
    assert "could not confirm" in info_probe_flat_guard(AccountSnapshot(margin_used=0.0),
                                                        {"found": False, "why": "x"})
    assert "1 working order" in info_probe_flat_guard(AccountSnapshot(margin_used=0.0),
                                                      {"found": True, "n_rows": 1})


def test_restore_latch_reason_is_set_only_for_an_unverified_click_mode_restore():
    assert info_probe_restore_latch_reason({"mode": "dry"}) is None
    assert info_probe_restore_latch_reason({"mode": "click"}) is None           # nothing clicked
    assert info_probe_restore_latch_reason({"mode": "click", "restore": {"verified": True}}) is None
    why = info_probe_restore_latch_reason({"mode": "click", "restore": {
        "original": "SOLUSD", "attempted": False, "verified": False}})
    assert why.startswith("AUTO-REVERT") and "'SOLUSD'" in why and "executor-clear-halt" in why


# ── dry mode clicks nothing ────────────────────────────────────────────────


def test_dry_resolves_everything_and_clicks_nothing(browser):
    got, st = run(browser, page_html(), click=False)
    assert got["mode"] == "dry" and got["refused"] is None and got["alerts"] == []
    assert got["one_click"]["state"] == "off" and "one_click_dump" not in got
    assert got["working_orders"]["found"] is True and got["working_orders"]["n_rows"] == 0
    r = got["resolve"]
    assert r["linked_symbol"] == "SOLUSD" and r["n_info_buttons"] == 1 and r["n_linked_inputs"] == 1
    assert all(r["targets"][s]["clean"] for s in ("BTCUSD", "ETHUSD", "AVAXUSD"))
    assert got["results"] == {} and st["clicks"] == [] and st["tags"] == 0
    assert got["watchlist_diff"]["changed"] is False


def test_symbols_are_deduplicated():
    class P:  # never reached: the first read already refuses
        def evaluate(self, *a, **k):
            raise RuntimeError("no page")
    got = DXtradeAdapter(timeout_ms=1).probe_instrument_info(P(), ["btcusd", "BTCUSD ", "ETHUSD"])
    assert got["symbols"] == ["BTCUSD", "ETHUSD"]


# ── the click mode, happy path ─────────────────────────────────────────────


def test_probe_dumps_each_symbols_own_panel_and_restores(browser):
    got, st = run(browser, page_html())
    assert got["alerts"] == [] and got["refused"] is None
    for sym in ("BTCUSD", "ETHUSD", "AVAXUSD"):
        r = got["results"][sym]
        assert r["linked_after_select"] == sym and r["closed"] is True and r["closed_via"] == "close_control"
        assert r["leaves"][0] == sym and SPECS[sym] in r["leaves"]
    assert got["restore"] == {"original": "SOLUSD", "attempted": True, "clicked": True, "verified": True}
    assert st["linked"] == "SOLUSD" and st["panels"] == 0 and st["tags"] == 0
    assert got["watchlist_diff"]["changed"] is False
    never_traded(st)
    # only the three allowlisted targets were ever clicked
    assert set(st["clicks"]) == {"sym", "instrument_info_button", "close"}, st["clicks"]


def test_probe_falls_back_to_the_info_toggle_when_the_panel_has_no_close_control(browser):
    got, st = run(browser, page_html(close_btn=False), symbols=("BTCUSD",))
    assert got["alerts"] == [] and got["results"]["BTCUSD"]["closed_via"] == "info_toggle"
    assert st["panels"] == 0 and st["linked"] == "SOLUSD"


def test_a_dialog_typed_info_panel_is_dumped_and_closed(browser):
    # The live panel's type is UNMEASURED; a modal that passes the identity
    # AND confirm-wording checks is the expected panel, not the "unexpected
    # dialog" (which is the one after a ROW click).
    got, st = run(browser, page_html(panel_role="dialog"), symbols=("BTCUSD",))
    r = got["results"]["BTCUSD"]
    assert got["alerts"] == [] and r["panel"]["is_dialog"] is True and SPECS["BTCUSD"] in r["leaves"]
    assert r["closed"] is True and st["panels"] == 0 and st["linked"] == "SOLUSD"


# ── refusals before any click ──────────────────────────────────────────────


@pytest.mark.parametrize("kw,why", [
    ({"margin": "$12.50"}, "Used Margin reads 12.5"),
    ({"order_rows": "<tr><td>SOLUSD</td><td>Buy</td><td>Limit</td><td>77</td></tr>"}, "1 working order"),
    ({"one_click": "checked"}, "one-click trading does not read OFF (reads 'on')"),
    ({"sym_cell_extra": '<a href="#"></a>'}, "original symbol SOLUSD's watchlist cell is not cleanly clickable"),
])
def test_refuses_before_any_click(browser, kw, why):
    got, st = run(browser, page_html(**kw))
    assert why in (got["refused"] or ""), got["refused"]
    assert st["clicks"] == [] and got["results"] == {} and "restore" not in got
    never_traded(st)


def test_fix1_an_unreadable_one_click_toggle_refuses_and_records_its_structure(browser):
    # Review fix 1: the live terminal reads 'unknown' (#13711). The guard
    # fails CLOSED -- only a positive "off" passes -- and dry mode records the
    # toggle's structure so a reader can be built from it.
    got, st = run(browser, page_html(one_click_unreadable=True))
    assert got["one_click"]["state"] == "unknown"
    assert "does not read OFF (reads 'unknown')" in got["refused"]
    assert "one_click_dump" in got and st["clicks"] == []
    dry, _ = run(browser, page_html(one_click_unreadable=True), click=False)
    assert dry["refused"] and "one_click_dump" in dry


def test_refuses_without_the_orders_widget(browser):
    html = re.sub(r'<div class="widget__container___Or1.*?</table></div>', "", page_html(), flags=re.S)
    assert "widget_menu_ORDERS" not in html
    got, st = run(browser, html)
    assert "0 visible widget_menu_ORDERS" in got["refused"] and st["clicks"] == []


HISTORY_TABLE = ('<div class="history"><table><thead><tr><th>Symbol</th><th>Order ID</th><th>Order Type</th>'
                 '<th>Close Time</th></tr></thead><tbody></tbody></table></div>')


def test_fix2_a_hidden_orders_widget_beside_a_visible_empty_history_table_refuses(browser):
    # Review fix 2: the working-Orders widget is hidden (another tab is
    # showing) while an EMPTY Orders-shaped history table is visible. Only
    # the ONE identified widget counts, so this is "could not look".
    got, st = run(browser, page_html(orders_hidden=True, outside_table=HISTORY_TABLE))
    assert got["working_orders"]["found"] is False
    assert "working Orders not readable" in got["refused"] and st["clicks"] == []


def test_a_history_shaped_table_inside_the_orders_widget_refuses(browser):
    got, st = run(browser, page_html(orders_headers=["Symbol", "Order ID", "Close Time"]))
    assert "history-shaped" in got["refused"] and st["clicks"] == []


def test_two_orders_tables_inside_the_widget_refuse(browser):
    html = page_html().replace('<button data-test-id="widget_menu_ORDERS">Orders</button>',
                               '<button data-test-id="widget_menu_ORDERS">Orders</button>'
                               '<table><thead><tr><th>Symbol</th><th>Order ID</th></tr></thead></table>')
    got, st = run(browser, html)
    assert "2 working-orders header rows" in got["refused"] and st["clicks"] == []


# ── aborts mid-run: nothing more is clicked, and the restore still runs ────


def test_a_dialog_after_a_row_click_aborts_and_restore_is_skipped_loudly(browser):
    got, st = run(browser, page_html(dialog_on="ETHUSD"), symbols=("BTCUSD", "ETHUSD", "AVAXUSD"))
    assert any("dialog(s) appeared after selecting ETHUSD" in a for a in got["alerts"])
    assert "AVAXUSD" not in got["results"]
    assert got["restore"]["attempted"] is False and got["restore"]["verified"] is False
    assert any("restore NOT attempted" in a for a in got["alerts"])
    assert info_probe_restore_latch_reason(got).startswith("AUTO-REVERT")
    never_traded(st)


def test_fix3_an_aborted_run_still_restores_the_linked_symbol(browser):
    # Review fix 3: BTCUSD completes (linked symbol moved, panel verified
    # gone), then ETHUSD's cell grows a control on hover -> abort. The
    # restore runs on that exit path too (it used to run only after a clean
    # finish).
    got, st = run(browser, page_html(hover_button_for="ETHUSD"), symbols=("BTCUSD", "ETHUSD", "AVAXUSD"))
    assert any("control appeared" in a for a in got["alerts"]) and "AVAXUSD" not in got["results"]
    assert got["results"]["BTCUSD"]["panel_open"] is False
    assert got["restore"]["attempted"] is True and got["restore"]["verified"] is True
    assert st["linked"] == "SOLUSD"
    assert info_probe_restore_latch_reason(got) is None


def _no_restore_click(st):
    # exactly one symbol-cell click (the target's); the restore clicked nothing
    assert st["clicks"].count("sym") == 1, st["clicks"]


def test_r3a_a_not_found_panel_gets_no_restore_click_and_latches(browser, tmp_path):
    # Round-3 review: found:false after the info click can mean unidentified
    # new elements -- an open panel. No further click; the latch is written.
    from scripts.prop.prop_executor_tick import latch_info_probe
    got, st = run(browser, page_html(no_panel_for="BTCUSD"), symbols=("BTCUSD", "ETHUSD"))
    assert any("aborted before any close click" in a for a in got["alerts"])
    assert got["results"]["BTCUSD"]["panel_open"] is True
    assert got["restore"]["attempted"] is False and "unverified" in got["restore"]["why"]
    _no_restore_click(st)
    assert latch_info_probe(got, tmp_path) and (tmp_path / "halted").exists()


def test_r3b_an_exception_in_the_close_step_gets_no_restore_click_and_latches(browser, tmp_path):
    from scripts.prop.prop_executor_tick import latch_info_probe

    class CloseBoom(DXtradeAdapter):
        def _info_click(self, page, selector):
            if selector == "[data-metis-close='1']":
                raise RuntimeError("close failed")
            return super()._info_click(page, selector)

    p = browser.new_page()
    p.set_content(page_html())
    got = CloseBoom(timeout_ms=3_000).probe_instrument_info(p, ["BTCUSD", "ETHUSD"], click=True, settle_ms=50)
    clicks = p.evaluate("window.__clicks")
    p.close()
    assert "probe raised RuntimeError (code=probe_exception)" in got["alerts"]
    assert got["results"]["BTCUSD"]["panel_open"] is True and "ETHUSD" not in got["results"]
    assert got["restore"]["attempted"] is False
    assert clicks == ["sym", "instrument_info_button"], clicks
    assert latch_info_probe(got, tmp_path) and (tmp_path / "halted").exists()


def test_an_unclosed_panel_skips_the_restore_and_latches(browser):
    # Re-review A: a panel that did not close may still be on screen, so no
    # further click -- the restore is skipped loudly and the latch is due.
    got, st = run(browser, page_html(no_close=True), symbols=("BTCUSD", "ETHUSD"))
    assert any("did not close" in a for a in got["alerts"]) and "ETHUSD" not in got["results"]
    assert got["restore"]["attempted"] is False and "unclosed" in got["restore"]["why"]
    assert info_probe_restore_latch_reason(got).startswith("AUTO-REVERT")
    assert st["clicks"].count("sym") == 1                      # no restore click


def test_fix3_a_failed_restore_writes_the_executor_halt_latch(browser, tmp_path):
    from scripts.prop.prop_executor_tick import latch_info_probe
    from src.prop import prop_executor as pe
    got, st = run(browser, page_html(link_breaks_for="SOLUSD"), symbols=("BTCUSD",))
    assert got["restore"]["verified"] is False
    assert any(a.startswith("RESTORE FAILED") for a in got["alerts"])
    reason = latch_info_probe(got, tmp_path)
    halted = pe.ExecutorState(tmp_path).halted()
    assert reason and halted and "AUTO-REVERT: instrument-info-probe" in halted
    assert any(a.startswith("executor halt latch written") for a in got["alerts"])
    # a verified restore writes nothing
    ok, _ = run(browser, page_html(), symbols=("BTCUSD",))
    assert latch_info_probe(ok, tmp_path / "clean") is None
    assert pe.ExecutorState(tmp_path / "clean").halted() is None


def test_fix4_a_panel_naming_another_symbol_is_escaped_never_click_closed(browser):
    got, st = run(browser, page_html(panel_names="ETHUSD BTCUSD"), symbols=("BTCUSD", "ETHUSD"))
    r = got["results"]["BTCUSD"]
    assert "also names ['ETHUSD']" in r["refused"] and "leaves" not in r
    assert r["closed_via"] == "escape" and r["closed"] is True and "Escape" in st["keys"]
    assert "close" not in st["clicks"] and "ETHUSD" not in got["results"]        # aborted
    # a REFUSED panel stops all further clicks, the restore included (re-review A)
    assert got["restore"]["attempted"] is False and st["panels"] == 0
    assert st["clicks"] == ["sym", "instrument_info_button"], st["clicks"]
    assert info_probe_restore_latch_reason(got).startswith("AUTO-REVERT")


def test_fix4_a_panel_with_a_confirm_button_is_refused_and_its_buttons_never_clicked(browser):
    # Review fix 4: a dialog-typed panel that passes identity but carries an
    # OK / Confirm control reads like an order confirmation.
    for extra in ('<button class="ok">OK</button>', '<button class="ok">Confirm</button>',
                  '<div>Are you sure?</div>'):
        got, st = run(browser, page_html(panel_role="dialog", panel_extra=extra), symbols=("BTCUSD",))
        r = got["results"]["BTCUSD"]
        assert r["panel"]["confirm_like"] is True and "order confirmation" in r["refused"], extra
        assert r["closed_via"] == "escape" and "leaves" not in r
        assert "close" not in st["clicks"] and "ok" not in st["clicks"], st["clicks"]
        never_traded(st)


def test_fix5_a_control_that_appears_on_hover_aborts_before_the_click(browser):
    # Review fix 5: the cell was clean at resolve time; a button renders in
    # it on hover. It is re-checked after hover and nothing is clicked.
    got, st = run(browser, page_html(hover_button_for="BTCUSD"), symbols=("BTCUSD", "ETHUSD"))
    assert any("control appeared in its Symbol cell on hover" in a for a in got["alerts"])
    assert st["clicks"] == [] and "ETHUSD" not in got["results"]
    assert got["restore"]["verified"] is True and st["linked"] == "SOLUSD"
    never_traded(st)


def test_a_panel_that_never_names_the_symbol_is_refused_and_aborts(browser):
    got, st = run(browser, page_html(panel_names="SAMEPANEL"), symbols=("BTCUSD", "ETHUSD"))
    r = got["results"]["BTCUSD"]
    assert "does not name" in r["refused"] and "leaves" not in r and r["closed"] is True
    assert "ETHUSD" not in got["results"]
    assert st["panels"] == 0 and got["restore"]["attempted"] is False


def test_reA_a_non_dialog_buy_sell_panel_that_ignores_escape_gets_no_further_click(browser, tmp_path):
    # Re-review A (a regression on abd0f25ef): the panel is NOT a role=dialog,
    # holds BUY/SELL and Escape does not close it. Nothing more is clicked --
    # no restore -- and the executor latch is written.
    from scripts.prop.prop_executor_tick import latch_info_probe
    from src.prop import prop_executor as pe
    buy_sell = ('<button data-test-id="BUY" onclick="window.__trade=(window.__trade||0)+1">Buy</button>'
                '<button data-test-id="SELL" onclick="window.__trade=(window.__trade||0)+1">Sell</button>')
    got, st = run(browser, page_html(panel_extra=buy_sell, no_close=True), symbols=("BTCUSD", "ETHUSD"))
    r = got["results"]["BTCUSD"]
    assert "BUY/SELL" in r["refused"] and r["closed"] is False and r["panel"]["is_dialog"] is False
    assert st["clicks"] == ["sym", "instrument_info_button"], st["clicks"]
    assert got["restore"]["attempted"] is False and "ETHUSD" not in got["results"]
    never_traded(st)
    assert latch_info_probe(got, tmp_path)
    assert "AUTO-REVERT: instrument-info-probe" in pe.ExecutorState(tmp_path).halted()


def test_reB_the_one_click_dump_masks_ids_beside_the_toggle(browser):
    got, st = run(browser, page_html(one_click_unreadable=True,
                                     toggle_attrs='data-account="12345678" data-uid="a1b2c3d4e5" '
                                                  'data-state="off" class="acct-98765432"'))
    dump = json.dumps(got["one_click_dump"])
    assert "12345678" not in dump and "a1b2c3d4e5" not in dump and "98765432" not in dump
    assert '"data-state": "off"' in dump and "########" in dump      # still useful, just masked


def test_pre_click_latch_is_armed_then_removed_or_replaced(tmp_path):
    from scripts.prop.prop_executor_tick import INFO_PROBE_ARMED, arm_info_probe_latch, latch_info_probe
    from src.prop import prop_executor as pe
    st = pe.ExecutorState(tmp_path)
    # clean run: armed before the click, removed after a verified restore
    assert arm_info_probe_latch(tmp_path) is True and INFO_PROBE_ARMED in st.halted()
    assert latch_info_probe({"mode": "click", "restore": {"verified": True}}, tmp_path, armed=True) is None
    assert st.halted() is None
    # unverified: the in-progress text is REPLACED by the reason
    arm_info_probe_latch(tmp_path)
    why = latch_info_probe({"mode": "click", "restore": {"original": "SOLUSD", "verified": False}},
                           tmp_path, armed=True)
    assert why and why in st.halted() and INFO_PROBE_ARMED not in st.halted()
    # a killed run never reaches latch_info_probe: the in-progress latch stays
    st.halt_file.unlink()
    arm_info_probe_latch(tmp_path)
    assert INFO_PROBE_ARMED in st.halted()
    # a REAL executor trip already present is never touched or removed
    st.halt_file.write_text("2026-09-30T00:00:00+00:00 AUTO-REVERT: real trip\n")
    assert arm_info_probe_latch(tmp_path) is False
    assert latch_info_probe({"mode": "click", "restore": {"verified": True}}, tmp_path, armed=False) is None
    assert "real trip" in st.halted()


def test_tick_docstring_names_the_latch_exception():
    from scripts.prop.prop_executor_tick import resolve_mode
    assert "writes the executor's\n    AUTO-REVERT ``halted`` latch" in resolve_mode.__doc__


SPEC_VALUES = ["100000", "Max qty 10000", "0.00001", "25.00000", "0.01-1000.00", "1000000.00", "tick 0.01"]
ACCOUNT_SHAPES = ["1234567", "12345678", "1234-5678", "1234 5678", "deadbeef01"]


def test_r4_panel_mask_keeps_spec_values_and_masks_account_ids(browser):
    # Round-4 review: the spec values this probe exists to read must survive
    # the panel mask (the round-1/3 5+-digit and dotted-group rules destroyed
    # them); account-ID shapes are still masked.
    extra = "".join(f"<div>{v}</div>" for v in SPEC_VALUES + [f"acct {x}" for x in ACCOUNT_SHAPES])
    got, _ = run(browser, page_html(panel_extra=extra), symbols=("BTCUSD",))
    leaves = got["results"]["BTCUSD"]["leaves"]
    for v in SPEC_VALUES:
        assert v in leaves, (v, leaves)
    for x in ACCOUNT_SHAPES:
        assert not any(x in v for v in leaves), (x, leaves)
        assert f"acct {'#' * len(x)}" in leaves, (x, leaves)


def test_one_click_dump_keeps_the_strict_mask(browser):
    # The one-click dump sits near account chrome, not the spec surface: it
    # keeps 5+-digit runs and comma/dot-separated groups masked too.
    strict = ACCOUNT_SHAPES + ["12345", "12,345,678", "1.234.567.8"]
    for x in strict:
        dry, _ = run(browser, page_html(one_click_unreadable=True, toggle_attrs=f'data-acct="{x}"'), click=False)
        assert x not in json.dumps(dry["one_click_dump"]), x


def test_an_exception_logs_only_its_type(browser):
    class Boom(DXtradeAdapter):
        def _info_click_cell(self, page, sym):
            raise RuntimeError("secret-looking DOM text 1234567890")
    p = browser.new_page()
    p.set_content(page_html())
    got = Boom(timeout_ms=3_000).probe_instrument_info(p, ["BTCUSD"], click=True, settle_ms=50)
    p.close()
    assert "probe raised RuntimeError (code=probe_exception)" in got["alerts"]
    assert not any("secret" in a or "1234567890" in a for a in got["alerts"])


# ── tick + action wiring ───────────────────────────────────────────────────


def test_tick_modes_and_exit_code(capsys):
    from scripts.prop.prop_executor_tick import EXIT_OK, EXIT_UNPARSED, emit_info_probe, resolve_mode
    base = dict(probe_ticket="", instrument_probe="", instrument_search_dump=False,
                instrument_info_dry="", instrument_info_probe="", dry_run=False, watched_click=False)
    assert resolve_mode(SimpleNamespace(**{**base, "instrument_info_dry": "BTCUSD"}), {}) == "instrument_info_dry"
    assert resolve_mode(SimpleNamespace(**{**base, "instrument_info_probe": "BTCUSD"}),
                        {"PROP_EXECUTOR_MODE": "off"}) == "instrument_info_probe"
    assert emit_info_probe({"results": {"BTCUSD": {"leaves": ["BTCUSD"]}}, "alerts": []}) == EXIT_OK
    assert emit_info_probe({"results": {}, "alerts": ["RESTORE FAILED"]}) == EXIT_UNPARSED
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    assert "instrument_info" in lines[0] and "instrument_info_summary" in lines[1]   # summary LAST


def test_action_and_workflow_accept_the_modes():
    action = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert 'instrument-info-dry)    EARGS+=(--instrument-info-dry "${ACTION_SYMBOLS}") ;;' in action
    assert 'instrument-info-probe)  EARGS+=(--instrument-info-probe "${ACTION_SYMBOLS}") ;;' in action
    wf = (REPO / ".github" / "workflows" / "system-actions.yml").read_text()
    assert wf.count("|instrument-info-dry|instrument-info-probe|") == 2
    assert '*",instrument-info-dry,"*|*",instrument-info-probe,"*)' in wf
