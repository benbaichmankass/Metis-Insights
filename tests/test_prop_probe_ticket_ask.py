"""probe-ticket-ask (TRADEIFY-GOLIVE, operator 2026-10-03 "Approve the guarded click").

ONE click on the ETHUSD watchlist Ask "Buy" price button (MEASURED by
order-surface-dump #15743) to open the order ticket. It aborts unless one-click
re-reads OFF right before the click, refuses unless exactly one button matches,
submits nothing, closes via the ticket's own Cancel/Close, and checks the
post-state."""
import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

from src.prop.platform.dxtrade import ASK_BUTTON_RESOLVE_JS, DXtradeAdapter
from tests import test_prop_platform_dxtrade as _dx

REPO = Path(__file__).resolve().parents[1]
chromium_page = _dx.chromium_page


def _row(sym, n_buttons=1, label="Buy"):
    btns = "".join(
        f'<button data-test-id="watchlist_cell_button" type="submit" class="button button-priceValue" '
        f'data-k="{sym}-{i}"><span class="priceLabel" style="display:none">{label}</span>'
        f'<span class="priceValue">2,432.05</span></button>' for i in range(n_buttons))
    return (f'<tr class="table--row instrument"><td><span>{sym}</span></td>'
            f'<td><div class="table--cell-bid price"><button data-test-id="watchlist_cell_button" type="submit">'
            f'<span class="priceLabel" style="display:none">Sell</span><span class="priceValue">2,431.55</span>'
            f'</button></div></td>'
            f'<td><div class="table--cell-ask price">{btns}</div></td><td>Ethereum</td></tr>')


def _page(rows):
    return f"""<html><body>
<table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th><th>Description</th></tr></thead></table>
<table data-test-id="table"><tbody>{rows}</tbody></table>
<div id="ticket" style="display:none"><button data-metis-btn="close" id="cancel">Cancel</button>
 <button id="place">Place Buy Order</button></div>
<script>(() => {{
  window.__clicks = [];
  document.addEventListener('click', e => {{
    const b = e.target.closest('button');
    window.__clicks.push(b ? (b.id || b.getAttribute('data-k') || b.getAttribute('data-test-id')) : 'other');
    if (b && b.getAttribute('data-k')) document.getElementById('ticket').style.display = 'block';
    if (b && b.id === 'cancel') document.getElementById('ticket').style.display = 'none';
  }}, true);
}})();</script>
</body></html>"""


FLAT = {"margin_used": 0.0, "orders": {"found": True, "n_rows": 0, "why": None}, "dialogs": 0, "flat_why": None}
OFF = {"state": "off", "via": "data-value+knob"}


def _adapter(monkeypatch, page, one_click=OFF):
    ad = DXtradeAdapter()
    monkeypatch.setattr(ad, "_post_state", lambda p: dict(FLAT))
    monkeypatch.setattr(ad, "read_one_click", lambda p: dict(one_click))
    monkeypatch.setattr(ad, "ticket_panel_dump", lambda p: {"found": False})

    def form(p):
        shown = p.evaluate("getComputedStyle(document.getElementById('ticket')).display") != "none"
        return ({"found": True, "fields": {"quantity": {"label": "Lots", "value": "0.01", "disabled": False}},
                 "buttons": {"close": "Cancel", "submit": "Place Buy Order"}, "symbol_value": "ETHUSD"}
                if shown else {"found": False})
    monkeypatch.setattr(ad, "_find_form", form)
    return ad


def test_resolver_tags_exactly_one_buy_button(chromium_page):
    chromium_page.set_content(_page(_row("ETH/USD") + _row("SOL/USD")))
    got = chromium_page.evaluate(ASK_BUTTON_RESOLVE_JS, ["ETH/USD"])
    assert got["ok"] is True and got["n_rows"] == 1 and got["n_buttons"] == 1 and got["label"] == "Buy"
    assert got["inside"] is True
    assert chromium_page.locator("[data-metis-ask-btn='1']").get_attribute("data-k") == "ETH/USD-0"
    assert chromium_page.evaluate("window.__clicks") == []
    chromium_page.set_content(_dx.DIVGRID.read_text())


@pytest.mark.parametrize("rows,why", [
    (_row("SOL/USD"), "0 watchlist row(s)"),
    (_row("ETH/USD") + _row("ETH/USD"), "2 watchlist row(s)"),
    (_row("ETH/USD", n_buttons=0), "0 watchlist_cell_button(s)"),
    (_row("ETH/USD", n_buttons=2), "2 watchlist_cell_button(s)"),
    (_row("ETH/USD", label="Sell"), "not Buy"),
])
def test_refuses_on_zero_or_two_matches_and_clicks_nothing(chromium_page, monkeypatch, rows, why):
    chromium_page.set_content(_page(rows))
    got = _adapter(monkeypatch, chromium_page).probe_ask_ticket(chromium_page, "ETHUSD")
    assert why in (got["refused"] or "")
    assert got["clicks"] == [] and got["opened"] is False
    assert chromium_page.evaluate("window.__clicks") == []
    chromium_page.set_content(_dx.DIVGRID.read_text())


@pytest.mark.parametrize("oc", [{"state": "on", "via": "data-value+knob"},
                                {"state": "unknown", "via": None},
                                {"state": "off", "via": "checkbox"}])
def test_aborts_when_one_click_is_not_confirmed_off(chromium_page, monkeypatch, oc):
    chromium_page.set_content(_page(_row("ETH/USD")))
    got = _adapter(monkeypatch, chromium_page, one_click=oc).probe_ask_ticket(chromium_page, "ETHUSD")
    assert got["aborted"] and "no click made" in got["aborted"]
    assert got["clicks"] == [] and chromium_page.evaluate("window.__clicks") == []
    assert chromium_page.locator("[data-metis-ask-btn]").count() == 0      # tag cleaned up
    chromium_page.set_content(_dx.DIVGRID.read_text())


def test_one_click_opens_records_and_closes_via_cancel_only(chromium_page, monkeypatch):
    chromium_page.set_content(_page(_row("ETH/USD")))
    got = _adapter(monkeypatch, chromium_page).probe_ask_ticket(chromium_page, "ETHUSD")
    assert got["refused"] is None and got["aborted"] is None and got["alerts"] == []
    assert got["opened"] is True and got["closed_via"] == "close_button"
    assert got["form"]["fields"]["quantity"] == {"label": "Lots", "value": "0.01", "disabled": False}
    assert chromium_page.evaluate("window.__clicks") == ["ETH/USD-0", "cancel"]   # never 'place'
    assert got["clicks"] == ["ask:ETH/USD", "close"] and got["post"]["flat_why"] is None
    chromium_page.set_content(_dx.DIVGRID.read_text())


def test_refuses_before_any_click_when_not_flat_or_form_open(chromium_page, monkeypatch):
    chromium_page.set_content(_page(_row("ETH/USD")))
    ad = _adapter(monkeypatch, chromium_page)
    monkeypatch.setattr(ad, "_post_state", lambda p: {**FLAT, "margin_used": 12.0,
                                                      "flat_why": "Used Margin reads 12.0"})
    got = ad.probe_ask_ticket(chromium_page, "ETHUSD")
    assert "not confirmed flat" in got["refused"] and chromium_page.evaluate("window.__clicks") == []
    ad2 = _adapter(monkeypatch, chromium_page)
    monkeypatch.setattr(ad2, "_find_form", lambda p: {"found": True})
    got = ad2.probe_ask_ticket(chromium_page, "ETHUSD")
    assert "already open" in got["refused"] and chromium_page.evaluate("window.__clicks") == []
    chromium_page.set_content(_dx.DIVGRID.read_text())


def test_unrecognised_ticket_is_dumped_and_escaped_never_a_ticket_button(chromium_page, monkeypatch):
    chromium_page.set_content(_page(_row("ETH/USD")))
    ad = _adapter(monkeypatch, chromium_page)
    monkeypatch.setattr(ad, "_find_form", lambda p: {"found": False})
    got = ad.probe_ask_ticket(chromium_page, "ETHUSD")
    assert got["opened"] is False and got["closed_via"] == "escape"
    assert any("did not open a recognised order form" in a for a in got["alerts"])
    assert "controls_dump" in got
    assert chromium_page.evaluate("window.__clicks") == ["ETH/USD-0"]          # no cancel/place click
    chromium_page.set_content(_dx.DIVGRID.read_text())


def test_post_state_difference_is_an_alert(chromium_page, monkeypatch):
    chromium_page.set_content(_page(_row("ETH/USD")))
    ad = _adapter(monkeypatch, chromium_page)
    states = iter([dict(FLAT), {**FLAT, "orders": {"found": True, "n_rows": 1, "why": None},
                                "flat_why": "1 working order(s) on the account"}])
    monkeypatch.setattr(ad, "_post_state", lambda p: next(states))
    got = ad.probe_ask_ticket(chromium_page, "ETHUSD")
    assert any("post-state not flat" in a for a in got["alerts"])
    chromium_page.set_content(_dx.DIVGRID.read_text())


def test_js_never_clicks_dispatches_or_types():
    for bad in (".click(", "dispatchEvent", ".focus(", ".value ="):
        assert bad not in ASK_BUTTON_RESOLVE_JS


def test_tick_resolves_the_mode():
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    args = argparse.Namespace(probe_ticket="", dry_run=False, watched_click=False, account="tradeify_1",
                              probe_ticket_ask="ETHUSD")
    assert resolve_mode(args, {}) == "probe_ticket_ask" and "probe_ticket_ask" in YIELD_MODES


@pytest.mark.parametrize("acct,symbols,msg", [
    ("breakout_1", "ETHUSD", "probe-ticket-ask: refused for breakout_1"),
])
def test_action_refusals(tmp_path, acct, symbols, msg):
    from tests.test_prop_executor_clear_halt import STUB_LIB
    d = tmp_path / "ops"
    d.mkdir()
    shutil.copy(REPO / "scripts" / "ops" / "breakout_login_check_action.sh", d / "action.sh")
    (d / "_lib.sh").write_text(STUB_LIB)
    home = tmp_path / "home"
    (home / ".cache" / "metis-prop-browser").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "ACTION_APPLY": "probe-ticket-ask",
           "ACTION_SYMBOLS": symbols}
    if acct != "breakout_1":
        env["ACCOUNT_ID"] = acct
    p = subprocess.run(["bash", str(d / "action.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 1 and msg in p.stdout + p.stderr
