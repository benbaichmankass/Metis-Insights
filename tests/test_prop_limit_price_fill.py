"""TRADEIFY-PRICE-FILL: the opt-in key-by-key LIMIT price fill.

Tradeify's limit price field kept its own price after page.fill on every
pass of round-trip-dry,limit #15975 (run 37132594478: typed 2679.725, shown
2,679.719) while SL / TP took the same call exactly. The browser tests below
run against an INVENTED field that models the INFERRED cause (it re-renders
its own price on blur unless it saw key events): they prove the mechanism
on that model, not on the live terminal -- the dry run on the VM does that.
"""
from __future__ import annotations

import glob
from pathlib import Path

import pytest

from src.prop.platform import load_platform_config
from src.prop.platform.dxtrade import DXtradeAdapter
from scripts.prop.prop_executor_tick import limit_price_fill_mode

REPO = Path(__file__).resolve().parents[1]

# A field that behaves like the inferred Tradeify one: page.fill's lone
# `input` event is overwritten by the field's own price on blur; a keydown
# marks it as user-typed and the typed value then sticks. A plain SL input
# beside it, and a "Track" toggle in the price row for the context read.
STICKY = """
<html><body><div id=form>
  <div class=row><label for=px>Price</label><input id=px data-metis-field=price value="2,679.719">
    <button aria-pressed="true" title="Track market">Track 1</button></div>
  <div class=row><label for=sl>Stop Loss</label><input id=sl data-metis-field=stop_loss value=""></div>
  <input id=dis data-metis-field=disabled_one disabled value="1">
</div>
<script>
  const px = document.getElementById('px');
  let typed = false;
  px.addEventListener('keydown', () => { typed = true; });
  px.addEventListener('blur', () => { if (!typed) px.value = '2,679.719'; });
</script></body></html>
"""


@pytest.fixture()
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    candidates = [None] + sorted(glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"))
    with sync_api.sync_playwright() as pw:
        browser, errors = None, []
        for exe in candidates:
            try:
                browser = pw.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
                break
            except Exception as exc:  # no usable browser build here
                errors.append(str(exc).splitlines()[0][:120])
        if browser is None:
            pytest.skip(f"chromium unavailable: {errors}")
        p = browser.new_page()
        p.set_content(STICKY)
        yield p
        browser.close()


def test_page_fill_reproduces_the_field_keeping_its_own_price(page):
    DXtradeAdapter._fill_field(page, "price", 2679.725)
    assert page.input_value("#px") == "2,679.719"
    # ...while a plain field takes the same call (SL / TP read back exactly)
    DXtradeAdapter._fill_field(page, "stop_loss", 2652.928)
    assert page.input_value("#sl") == "2652.928"


def test_keys_fill_holds_the_typed_price_and_logs_every_step(page):
    got = DXtradeAdapter._fill_field_keys(page, "price", 2679.725)
    assert page.input_value("#px") == "2679.725"
    assert got["typed"] == "2679.725" and got["focused"] is True
    assert got["before"] == "2,679.719" and got["after_clear"] == ""
    assert got["after_keys"] == got["after_blur"] == got["after_settle"] == "2679.725"
    ctx = got["context"]
    assert ctx["found"] and ctx["tag"] == "input"
    # the toggle beside the field is NAMED, its digits masked; nothing clicked
    assert {"text": "Track #", "title": "Track market", "state": "true"}.items() <= ctx["controls"][0].items()
    assert page.get_attribute("button", "aria-pressed") == "true"


def test_keys_fill_types_nothing_when_the_field_will_not_take_focus(page):
    got = DXtradeAdapter._fill_field_keys(page, "disabled_one", 5)
    assert got["focused"] is False and "nothing typed" in got["why"]
    assert "after_keys" not in got and page.input_value("#dis") == "1"


def test_only_the_price_field_of_an_opted_in_adapter_is_typed_by_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(DXtradeAdapter, "_fill_field", staticmethod(lambda p, k, v: calls.append(("fill", k))))
    monkeypatch.setattr(DXtradeAdapter, "_fill_field_keys",
                        staticmethod(lambda p, k, v: calls.append(("keys", k)) or {"field": k}))
    default = DXtradeAdapter()
    assert default.limit_price_fill == "fill"
    trace: list = []
    for k in ("quantity", "stop_loss", "take_profit", "price"):
        default._fill_one(None, k, 1.0, trace)
    assert calls == [("fill", "quantity"), ("fill", "stop_loss"), ("fill", "take_profit"), ("fill", "price")]
    assert trace == []          # the default path adds nothing to the run log

    calls.clear()
    opted = DXtradeAdapter()
    opted.limit_price_fill = "keys"
    for k in ("stop_loss", "take_profit", "price"):
        opted._fill_one(None, k, 1.0, trace)
    assert calls == [("fill", "stop_loss"), ("fill", "take_profit"), ("keys", "price")]
    assert trace == [{"price_fill": {"field": "price"}}]


def test_limit_price_fill_mode_defaults_to_fill_and_refuses_a_typo():
    assert limit_price_fill_mode({}) == "fill"
    assert limit_price_fill_mode({"limit_price_fill": " Keys "}) == "keys"
    with pytest.raises(ValueError, match="limit_price_fill"):
        limit_price_fill_mode({"limit_price_fill": "type"})


def test_only_tradeify_1_opts_in():
    path = REPO / "config" / "prop_platforms.yaml"
    assert limit_price_fill_mode(load_platform_config("tradeify_1", path)) == "keys"
    assert "limit_price_fill" not in load_platform_config("breakout_1", path)
    assert limit_price_fill_mode(load_platform_config("breakout_1", path)) == "fill"
