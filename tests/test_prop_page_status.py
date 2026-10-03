"""page-status (manager 2026-10-03 ~07:25Z): a ZERO-INTERACTION read of what
the DXtrade terminal is saying -- breach / liquidation / disabled banners,
alert regions, and whether the Buy / Sell controls render enabled. Exercised
in real Chromium on invented markup, with a recorder that fails the test on
ANY pointer, mouse, focus or key event."""
from __future__ import annotations

import argparse

from src.prop.platform.dxtrade import DXtradeAdapter

from tests.test_prop_instrument_info_probe import browser  # noqa: F401  (fixture reuse)

RECORDER = """<script>
window.__events = [];
for (const t of ['click', 'mousedown', 'mouseup', 'mouseover', 'mouseenter', 'pointerdown', 'pointerover',
                 'focusin', 'keydown', 'keyup', 'input', 'change', 'scroll', 'wheel'])
  document.addEventListener(t, e => window.__events.push(t), true);
</script>"""

TICKET = ('<div class="order-ticket"><span>ETHUSD</span>'
          '<button data-test-id="BUY" {buy}>Buy 2,657.17</button>'
          '<button data-test-id="SELL" {sell}>Sell 2,657.05</button><input value="0.01"></div>')


def page(*, banner="", body="", buy="", sell="", extra=""):
    return (f"<html><body>{RECORDER}<div id='top'>{banner}</div><main>"
            f"<p>Balance 4,698.00</p><p>Account 123456789 owner a.b@example.com</p>{body}</main>"
            f"{TICKET.format(buy=buy, sell=sell)}{extra}</body></html>")


def read(browser, html, cap=None):  # noqa: F811
    p = browser.new_page()
    p.set_content(html)
    # Chromium fires synthetic pointer/mouse "over" events when content
    # renders under the idle cursor: settle, then record only what the READ does.
    p.wait_for_timeout(100)
    p.evaluate("() => { window.__events = []; }")
    a = DXtradeAdapter(timeout_ms=2_000)
    if cap is not None:
        a.PAGE_STATUS_TEXT_CAP = cap
    got = a.page_status(p)
    events = p.evaluate("window.__events")
    p.close()
    return got, events


def test_a_breach_banner_and_alert_region_are_read_without_any_interaction(browser):  # noqa: F811
    got, events = read(browser, page(
        banner='<div class="account-banner warning">Account disabled: Max drawdown rule violated</div>',
        body='<div role="alert">Position closed by stop out</div>'))
    assert events == []
    texts = [n["text"] for n in got["notices"]]
    assert "Account disabled: Max drawdown rule violated" in texts
    assert "Position closed by stop out" in texts
    assert any("violated" in line for line in got["flagged_lines"])
    assert any("stop out" in line for line in got["flagged_lines"])


def test_ids_and_emails_are_masked_and_the_text_is_capped(browser):  # noqa: F811
    got, events = read(browser, page(body="<p>" + "lorem " * 200 + "</p>"), cap=300)
    assert events == []
    flat = str(got)
    assert "123456789" not in flat and "#########" in flat
    assert "a.b@example.com" not in flat and "<email>" in flat
    assert len(got["body_text"]) == 300 and got["body_truncated"] is True and got["body_chars"] > 300


def test_trade_buttons_report_their_disabled_state(browser):  # noqa: F811
    got, events = read(browser, page(buy="disabled", sell='aria-disabled="true"'))
    assert events == []
    state = {b["tid"]: b["disabled"] for b in got["trade_buttons"]}
    assert state == {"BUY": True, "SELL": True}
    got, _ = read(browser, page())
    assert {b["tid"]: b["disabled"] for b in got["trade_buttons"]} == {"BUY": False, "SELL": False}


def test_a_quiet_terminal_reports_nothing_flagged(browser):  # noqa: F811
    got, events = read(browser, page())
    assert events == [] and got["notices"] == [] and got["flagged_lines"] == [] and got["dialogs"] == []
    assert "Balance 4,698.00" in got["body_text"]


def test_nested_notices_are_reported_once_and_open_dialogs_are_listed(browser):  # noqa: F811
    got, events = read(browser, page(
        banner='<div role="status" class="toast"><div class="toast-body">Order rejected: trading disabled</div></div>',
        extra='<div role="dialog">Your account has breached the daily loss limit</div>'))
    assert events == []
    assert [n["text"] for n in got["notices"]] == ["Order rejected: trading disabled"]
    assert got["dialogs"] == ["Your account has breached the daily loss limit"]


def test_a_page_error_is_returned_not_raised():
    class Broken:
        def evaluate(self, *a, **k):
            raise TimeoutError("x")
    got = DXtradeAdapter().page_status(Broken())
    assert got == {"error": "TimeoutError (code=page_status_exception)"}


def test_the_tick_maps_page_status_to_its_own_read_only_yielding_mode():
    from scripts.prop import prop_executor_tick as tick
    args = argparse.Namespace(probe_ticket=False, instrument_probe="", instrument_search_dump=False,
                              instrument_info_dry="", instrument_info_probe="", symbol_switch_dry="",
                              link_state_dump=False, page_status=True, dry_run=False, watched_click=False,
                              round_trip="", close_position="", live=False, account="breakout_1")
    assert tick.resolve_mode(args, {"PROP_EXECUTOR_MODE": "live"}) == "page_status"
    assert "page_status" in tick.YIELD_MODES


def test_the_action_and_workflow_allow_page_status():
    import re
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    sh = (repo / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert "page-status)         EARGS+=(--page-status) ;;" in sh
    wf = (repo / ".github" / "workflows" / "system-actions.yml").read_text()
    assert len(re.findall(r"\|page-status\|", wf)) == 2 and ",page-status," in wf


def test_the_recorder_does_see_a_real_interaction(browser):  # noqa: F811
    # Positive control: an empty event list above means "nothing happened",
    # not "the recorder is blind".
    p = browser.new_page()
    p.set_content(page())
    p.wait_for_timeout(100)
    p.evaluate("() => { window.__events = []; }")
    p.click("[data-test-id=BUY]")
    events = p.evaluate("window.__events")
    p.close()
    assert "click" in events and "mousedown" in events
