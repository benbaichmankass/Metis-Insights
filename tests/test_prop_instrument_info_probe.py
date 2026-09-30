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

from src.prop.platform.base import AccountSnapshot, WorkingOrder
from src.prop.platform.dxtrade import DXtradeAdapter, info_probe_flat_guard

REPO = Path(__file__).resolve().parents[1]

SPECS = {"BTCUSD": "0.001", "ETHUSD": "0.01", "SOLUSD": "0.1", "AVAXUSD": "1"}


def page_html(*, margin="$0", order_rows="", one_click="", panel_names=None, dialog_on="",
              close_btn=True, no_close=False, sym_cell_extra="", link_breaks_for="",
              panel_role="", orders_hidden=False):
    rows = "".join(
        f'<tr class="instrument" data-row-id="{i}"><td class="sym">{s}{sym_cell_extra if s == "SOLUSD" else ""}</td>'
        f'<td><button class="px" onclick="window.__trade=(window.__trade||0)+1">100.1</button></td>'
        f'<td><button class="px" onclick="window.__trade=(window.__trade||0)+1">100.2</button></td></tr>'
        for i, s in enumerate(["ETHUSD", "SOLUSD", "BTCUSD", "AVAXUSD"]))
    return f"""<html><body>
<div class="metrics"><div><span>Balance</span><div>$5,024</div></div>
  <div><span>Used Margin</span><div>{margin}</div></div>
  <div><span>Free Margin</span><div>$4,724</div></div>
  <label><input type="checkbox" {one_click}><span>One-click trading</span></label></div>
<div class="widget__container___Ab1 widgetNew__container">
  <table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th></tr></thead><tbody>{rows}</tbody></table>
</div>
<div class="widget-header__container"><div class="toolbar__item">
  <input data-test-id="symbol_input" placeholder="Symbol..." value="SOLUSD">
  <div class="instrument-details"><button data-test-id="instrument_info_button"><svg><use href="#icon-info"></use></svg></button></div>
</div></div>
<div class="orders-widget" {'style="display:none"' if orders_hidden else ''}><table><thead><tr><th>Symbol</th><th>Side</th><th>Order Type</th><th>Order ID</th></tr></thead>
  <tbody>{order_rows}</tbody></table></div>
<script>
const SPECS = {json.dumps(SPECS)};
const PANEL_NAMES = {json.dumps(panel_names)};
window.__clicks = [];
document.addEventListener('click', e => window.__clicks.push(
  (e.target.closest('[data-test-id]') || e.target).getAttribute('data-test-id') || e.target.className || e.target.tagName), true);
document.querySelectorAll('tr.instrument').forEach(tr => {{
  tr.addEventListener('dblclick', () => {{ window.__ticket = 1; }});
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
  const name = PANEL_NAMES ? PANEL_NAMES : s;
  const p = document.createElement('div'); p.className = 'info-panel';
  if ({json.dumps(panel_role)}) p.setAttribute('role', {json.dumps(panel_role)});
  p.style.cssText = 'width:300px;height:200px';
  p.innerHTML = '<div class="title">' + name + '</div><div>Lot size</div><div>' + (SPECS[s] || '?')
    + '</div><div>Tick size</div><div>0.01</div>'
    + ({json.dumps(close_btn)} ? '<button class="close" aria-label="Close">x</button>' : '');
  const btn = p.querySelector('button');
  if (btn) btn.addEventListener('click', () => {{ if (!{json.dumps(no_close)}) p.remove(); }});
  document.body.appendChild(p);
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
                       " linked: document.querySelector('[data-test-id=symbol_input]').value,"
                       " tags: document.querySelectorAll('[data-metis-row-cell],[data-metis-info-btn],"
                       "[data-metis-sym-input],[data-metis-info-panel],[data-metis-close]').length,"
                       " panels: document.querySelectorAll('.info-panel').length})")
    p.close()
    return got, state


def never_traded(state):
    assert state["trade"] == 0 and state["ticket"] == 0
    assert all(c not in ("px",) for c in state["clicks"])


# ── the pure flat guard ────────────────────────────────────────────────────


def test_flat_guard_refuses_unless_it_could_look_and_saw_flat():
    assert info_probe_flat_guard(AccountSnapshot(margin_used=0.0), []) is None
    assert "not readable" in info_probe_flat_guard(AccountSnapshot(), [])
    assert "12.5" in info_probe_flat_guard(AccountSnapshot(margin_used=12.5), [])
    assert "could not confirm" in info_probe_flat_guard(AccountSnapshot(margin_used=0.0), None)
    assert "1 working order" in info_probe_flat_guard(AccountSnapshot(margin_used=0.0),
                                                      [WorkingOrder(symbol="SOLUSD")])


# ── dry mode clicks nothing ────────────────────────────────────────────────


def test_dry_resolves_everything_and_clicks_nothing(browser):
    got, st = run(browser, page_html(), click=False)
    assert got["mode"] == "dry" and got["refused"] is None and got["alerts"] == []
    r = got["resolve"]
    assert r["linked_symbol"] == "SOLUSD" and r["n_info_buttons"] == 1 and r["n_linked_inputs"] == 1
    assert all(r["targets"][s]["clean"] for s in ("BTCUSD", "ETHUSD", "AVAXUSD"))
    assert got["results"] == {} and st["clicks"] == [] and st["tags"] == 0
    assert got["watchlist_diff"]["changed"] is False


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


# ── refusals before any click ──────────────────────────────────────────────


@pytest.mark.parametrize("kw,why", [
    ({"margin": "$12.50"}, "Used Margin reads 12.5"),
    ({"order_rows": "<tr><td>SOLUSD</td><td>Buy</td><td>Limit</td><td>77</td></tr>"}, "1 working order"),
    ({"one_click": "checked"}, "one-click trading reads ON"),
    ({"sym_cell_extra": '<a href="#"></a>'}, "original symbol SOLUSD's watchlist cell is not cleanly clickable"),
])
def test_refuses_before_any_click(browser, kw, why):
    got, st = run(browser, page_html(**kw))
    assert why in (got["refused"] or ""), got["refused"]
    assert st["clicks"] == [] and got["results"] == {} and "restore" not in got
    never_traded(st)


def test_refuses_without_a_visible_orders_table(browser):
    html = re.sub(r'<div class="orders-widget"\s*>.*?</div>', "", page_html(), flags=re.S)
    assert "Order ID" not in html
    got, st = run(browser, html)
    assert "no Orders table visible" in got["refused"] and st["clicks"] == []


def test_a_hidden_orders_table_is_could_not_look_never_empty(browser):
    got, st = run(browser, page_html(orders_hidden=True))
    assert got["n_visible_orders_tables"] == 0 and got["n_working_orders"] is None
    assert "no Orders table visible" in got["refused"] and st["clicks"] == []


def test_a_dialog_typed_info_panel_is_dumped_and_closed(browser):
    # The live panel's type is UNMEASURED; a modal that passes the identity
    # checks is the expected panel, not the "unexpected dialog" (which is the
    # one after a ROW click).
    got, st = run(browser, page_html(panel_role="dialog"), symbols=("BTCUSD",))
    r = got["results"]["BTCUSD"]
    assert got["alerts"] == [] and r["panel"]["is_dialog"] is True and SPECS["BTCUSD"] in r["leaves"]
    assert r["closed"] is True and st["panels"] == 0 and st["linked"] == "SOLUSD"


# ── aborts mid-run: nothing more is clicked ────────────────────────────────


def test_a_dialog_after_a_row_click_aborts_everything(browser):
    got, st = run(browser, page_html(dialog_on="ETHUSD"), symbols=("BTCUSD", "ETHUSD", "AVAXUSD"))
    assert any("dialog(s) appeared after selecting ETHUSD" in a for a in got["alerts"])
    assert "AVAXUSD" not in got["results"] and got["restore"] == {"attempted": False}
    never_traded(st)


def test_a_panel_naming_another_symbol_is_refused_and_still_closed(browser):
    got, st = run(browser, page_html(panel_names="ETHUSD BTCUSD"), symbols=("BTCUSD",))
    r = got["results"]["BTCUSD"]
    assert "also names ['ETHUSD']" in r["refused"] and "leaves" not in r and r["closed"] is True
    assert got["restore"]["verified"] is True and st["panels"] == 0


def test_a_panel_that_does_not_close_aborts_with_an_alert(browser):
    got, st = run(browser, page_html(no_close=True), symbols=("BTCUSD", "ETHUSD"))
    assert any("did not close" in a for a in got["alerts"]) and "ETHUSD" not in got["results"]


def test_a_failed_restore_is_an_alert(browser):
    got, st = run(browser, page_html(link_breaks_for="SOLUSD"), symbols=("BTCUSD",))
    assert got["restore"]["verified"] is False
    assert any(a.startswith("RESTORE FAILED") for a in got["alerts"])


def test_a_panel_that_never_names_the_symbol_is_refused_but_closed(browser):
    got, st = run(browser, page_html(panel_names="SAMEPANEL"), symbols=("BTCUSD", "ETHUSD"))
    for s in ("BTCUSD", "ETHUSD"):
        r = got["results"][s]
        assert "does not name" in r["refused"] and "leaves" not in r and r["closed"] is True
    assert st["panels"] == 0 and got["restore"]["verified"] is True


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
