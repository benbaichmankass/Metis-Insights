"""instrument-page-dump (TRADEIFY-GOLIVE (b), manager 2026-10-02 12:58Z).

Every key-by-key form of ETH / SOL / XRP left tradeify_1's suggestion table
EMPTY (#15491/#15493/#15494). This READ-ONLY mode types the base asset key by
key (never Enter), dumps every visible text leaf on the page -- tbody and
role=row included -- masked and capped, then resets and blurs the field.
Clicks nothing.
"""
import argparse
import inspect
import shutil
import subprocess
from pathlib import Path

from src.prop.platform.dxtrade import PAGE_LEAF_DUMP_JS, DXtradeAdapter
from tests import test_prop_platform_dxtrade as _dx
from tests.test_prop_platform_dxtrade import DIVGRID, EMPTY_WATCHLIST_PAGE

# The real-Chromium fixture lives with the adapter's other browser tests.
chromium_page = _dx.chromium_page

REPO = Path(__file__).resolve().parents[1]

# Results render OUTSIDE the header's table, in a virtualized role=grid of
# divs, and only on key events (fill() alone never fires keyup).
VIRTUAL_RESULTS_PAGE = EMPTY_WATCHLIST_PAGE.replace("</body>", """
<table class="positions"><thead><tr><th>Symbol</th><th>Side</th></tr></thead>
<tbody><tr><td>BTC/USD</td><td>Buy</td></tr></tbody></table>
<div class="user-profile"><span>ETH whale 1234567</span></div>
<div id="dd">
  <table><thead><tr><th><span>Symbol</span></th><th><span>Description</span></th><th><span>Asset Class</span></th></tr></thead><tbody></tbody></table>
  <div role="grid" data-test-id="search_results_9876543" id="vgrid"></div>
</div>
<script>
document.getElementById('wl-search').addEventListener('keyup', function (e) {
  var v = (e.target.value || '').toUpperCase(), g = document.getElementById('vgrid');
  g.innerHTML = '';
  if (v === 'ETH') {
    g.innerHTML = '<div role="row"><span>ETH/USD</span><span>Ethereum</span><span>Cryptocurrencies</span></div>'
      + '<div role="row"><span>ETH/BTC</span><span>Ethereum Bitcoin</span><span>Cryptocurrencies</span></div>'
      + '<div role="row"><span>Ref 9876543</span></div>';
  }
});
</script>
</body>""")


def test_page_dump_finds_results_rendered_outside_the_header_table(chromium_page):
    chromium_page.set_content(VIRTUAL_RESULTS_PAGE)
    res = DXtradeAdapter().probe_page_leaf_dump(chromium_page, "ETHUSD", settle_ms=300, key_delay_ms=10)
    assert res["searched"] is True and res["query"] == "ETH" and res["readback_matches"] is True
    page = res["page"]
    texts = [m["text"] for m in page["matches"]]
    assert "ETH/USD" in texts and "ETH/BTC" in texts
    eth = next(m for m in page["matches"] if m["text"] == "ETH/USD")
    assert eth["grid"]["role"] == "row" and eth["grid"]["in_tbody"] is False
    assert len(eth["box"]) == 4
    # The positions table's tbody rows ARE in the leaves (tbody included) ...
    assert any(lf["text"] == "BTC/USD" and lf["grid"]["in_tbody"] for lf in page["leaves"])
    # ... a personal-looking node is skipped, and digit runs are masked.
    assert not any("whale" in (lf["text"] or "") for lf in page["leaves"])
    assert any(lf["text"] == "Ref #######" for lf in page["leaves"])
    assert not any("9876543" in (lf["text"] or "") or "9876543" in str(lf["grid"]) for lf in page["leaves"])
    # Reset, blurred, untagged.
    assert res["reset"] is True and res["blurred"] is True
    assert chromium_page.input_value("#wl-search") == ""
    assert chromium_page.locator("[data-metis-search-hit]").count() == 0
    chromium_page.set_content(DIVGRID.read_text())


def test_page_dump_caps_leaves_but_still_collects_matches(chromium_page):
    filler = "".join(f"<div>row {i}</div>" for i in range(450))
    chromium_page.set_content(f"<html><body>{filler}<div>ETH/USD</div></body></html>")
    got = chromium_page.evaluate(PAGE_LEAF_DUMP_JS, "ETH")
    assert got["n"] == 400 and got["capped"] is True and got["n_seen"] == 451
    assert got["n_matches"] == 1 and got["matches"][0]["text"] == "ETH/USD"
    chromium_page.set_content(DIVGRID.read_text())


def test_page_dump_never_clicks_presses_enter_or_reads_values():
    src = inspect.getsource(DXtradeAdapter.probe_page_leaf_dump)
    assert ".click(" not in src and ".press(" not in src and "keyboard" not in src
    assert "press_sequentially(q," in src
    assert ".click(" not in PAGE_LEAF_DUMP_JS and "dispatchEvent" not in PAGE_LEAF_DUMP_JS
    assert ".value" not in PAGE_LEAF_DUMP_JS


def test_tick_resolves_the_mode():
    from scripts.prop.prop_executor_tick import YIELD_MODES, resolve_mode
    args = argparse.Namespace(probe_ticket="", dry_run=False, watched_click=False, instrument_page_dump="ETHUSD",
                              account="tradeify_1")
    assert resolve_mode(args, {}) == "instrument_page_dump"
    assert "instrument_page_dump" in YIELD_MODES


def _run_action(tmp_path, env_extra):
    from tests.test_prop_executor_clear_halt import STUB_LIB
    d = tmp_path / "ops"
    d.mkdir()
    shutil.copy(REPO / "scripts" / "ops" / "breakout_login_check_action.sh", d / "action.sh")
    (d / "_lib.sh").write_text(STUB_LIB)
    home = tmp_path / "home"
    (home / ".cache" / "metis-prop-browser").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", **env_extra}
    return subprocess.run(["bash", str(d / "action.sh")], env=env, capture_output=True, text=True, timeout=30)


def test_action_refuses_the_page_dump_for_breakout_1(tmp_path):
    p = _run_action(tmp_path, {"ACTION_APPLY": "instrument-page-dump", "ACTION_SYMBOLS": "ETHUSD"})
    assert p.returncode == 1
    assert "instrument-page-dump: refused for breakout_1" in p.stdout + p.stderr


def test_page_dump_masks_every_digit_so_balances_never_reach_the_log(chromium_page):
    # Manager review of #15550 (BLOCK): a page-wide dump must not print
    # balances, P&L, prices or sizes. Every digit becomes '#', in leaves AND
    # matches; the query still matches on the raw text.
    chromium_page.set_content(
        "<html><body><div>Balance 98,432.10</div><div>P&L -1,416.42</div>"
        "<div>ETH/USD 2,431.5</div><div>Size 3</div></body></html>")
    got = chromium_page.evaluate(PAGE_LEAF_DUMP_JS, "ETH")
    texts = [lf["text"] for lf in got["leaves"]] + [m["text"] for m in got["matches"]]
    assert "Balance ##,###.##" in texts and "P&L -#,###.##" in texts and "Size #" in texts
    assert not any(ch.isdigit() for t in texts for ch in t)
    assert got["n_matches"] == 1 and got["matches"][0]["text"] == "ETH/USD #,###.#"
    chromium_page.set_content(DIVGRID.read_text())
