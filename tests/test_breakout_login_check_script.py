"""Every line ``scripts/prop/breakout_login_check.py`` prints lands in a PUBLIC
issue comment. Drive ``main()`` end to end with a fake browser and a fake
adapter whose every surface echoes the credentials (in another case, inside
exceptions and URLs) and prove none of it reaches stdout.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


from src.prop.platform.base import AccountSnapshot, FeasibilityError, InstrumentSpec, Position

REPO = Path(__file__).resolve().parents[1]
USER, PASSWORD = "bo-jdoe77", "Pa55-word!x"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "breakout_login_check", REPO / "scripts" / "prop" / "breakout_login_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Page:
    url = f"https://app.example.com/session/{USER.upper()}?sid=abc123"

    def wait_for_timeout(self, *_a):
        pass


class _LeakyAdapter:
    timeout_ms = 1_000

    def login(self, page, url, username, password):
        pass

    def wait_ready(self, page, timeout_ms=None):
        return False

    def read_account(self, page):
        return AccountSnapshot(unparsed=["balance", "equity"])

    def read_positions(self, page):
        return [Position(symbol=f"ETHUSD {USER.upper()}")]

    def read_orders(self, page):
        raise LookupError(f"no orders table for {USER.upper()} at https://x.example/p/{PASSWORD}")

    def structure(self, page, secrets=()):
        raise RuntimeError(f"probe died for {USER.upper()} pw={PASSWORD.lower()} "
                           f"url=https://x.example/s/tok9?u={USER}")


def _fake_playwright():
    class _Browser:
        def new_context(self):
            return types.SimpleNamespace(new_page=lambda: _Page())

        def close(self):
            pass

    class _PW:
        chromium = types.SimpleNamespace(launch=lambda **k: _Browser())

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: _PW()
    pkg = types.ModuleType("playwright")
    pkg.sync_api = sync_api
    return pkg, sync_api


def test_main_never_prints_credentials_even_from_exceptions(monkeypatch, capsys):
    mod = _load_script()
    pkg, sync_api = _fake_playwright()
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setenv("BREAKOUT_DX_USERNAME", USER)
    monkeypatch.setenv("BREAKOUT_DX_PASSWORD", PASSWORD)
    monkeypatch.setattr(mod, "adapter_for_platform", lambda _p: _LeakyAdapter())

    rc = mod.main(["--account", "breakout_1"])
    out = capsys.readouterr().out

    assert rc == mod.EXIT_UNPARSED
    # The paths that were exercised, so a quiet stdout is not mistaken for safety.
    assert "structure: FAILED (RuntimeError" in out
    assert "orders: UNPARSED" in out and "landed: https://app.example.com/<path>" in out
    low = out.lower()
    for leaked in (USER.lower(), PASSWORD.lower(), "sid=abc123", "tok9"):
        assert leaked not in low, leaked


class _LeakyInstrumentAdapter(_LeakyAdapter):
    """A symbol whose spec panel never parsed leaks the raw snippet dump —
    prove the same redaction path covers it (PI-20260927-ODDTM5QY-0002)."""

    def read_instrument_specs(self, page, symbols, secrets=()):
        return [InstrumentSpec(
            symbol=symbols[0] if symbols else "ETHUSD",
            unparsed=["digits", "contract_size", "min_qty", "qty_step"],
            raw_snippet=(f"session for {USER.upper()} pw={PASSWORD} "
                        f"tok=eyJhbGciOiJIUzI1NiJ9{USER}abcdefghijklmnop"),
        )]


def test_instrument_output_is_redacted_even_when_the_snippet_leaks_credentials(monkeypatch, capsys):
    mod = _load_script()
    pkg, sync_api = _fake_playwright()
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setenv("BREAKOUT_DX_USERNAME", USER)
    monkeypatch.setenv("BREAKOUT_DX_PASSWORD", PASSWORD)
    monkeypatch.setattr(mod, "adapter_for_platform", lambda _p: _LeakyInstrumentAdapter())

    rc = mod.main(["--account", "breakout_1", "--symbols", "ETHUSD"])
    out = capsys.readouterr().out

    assert rc == mod.EXIT_UNPARSED
    assert "instrument_raw_snippet[ETHUSD]" in out
    assert "instrument_unparsed: ETHUSD" in out
    low = out.lower()
    for leaked in (USER.lower(), PASSWORD.lower(), "eyjhbgcioijiuzi1niJ9".lower()):
        assert leaked not in low, leaked


def test_instrument_specs_skipped_with_empty_symbols_flag(monkeypatch, capsys):
    mod = _load_script()
    pkg, sync_api = _fake_playwright()
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setenv("BREAKOUT_DX_USERNAME", USER)
    monkeypatch.setenv("BREAKOUT_DX_PASSWORD", PASSWORD)
    monkeypatch.setattr(mod, "adapter_for_platform", lambda _p: _LeakyInstrumentAdapter())

    rc = mod.main(["--account", "breakout_1", "--symbols", ""])
    out = capsys.readouterr().out

    assert "instrument:" not in out and "instrument_unparsed" not in out
    assert rc == mod.EXIT_UNPARSED  # balance/equity still unread, from _LeakyAdapter


class _UnknownPageAdapter(_LeakyAdapter):
    def login(self, page, url, username, password):
        raise FeasibilityError("unknown_page", f"login form never rendered (at https://x.example/{USER})")

    def page_shape(self, page, secrets=()):
        return ["page_shape: BEGIN", f"page_shape.buttons: ['{USER.upper()}']", "page_shape: END"]


def test_unknown_page_prints_the_page_shape_redacted(monkeypatch, capsys):
    mod = _load_script()
    pkg, sync_api = _fake_playwright()
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setenv("BREAKOUT_DX_USERNAME", USER)
    monkeypatch.setenv("BREAKOUT_DX_PASSWORD", PASSWORD)
    monkeypatch.setattr(mod, "adapter_for_platform", lambda _p: _UnknownPageAdapter())

    rc = mod.main(["--account", "breakout_1"])
    out = capsys.readouterr().out

    assert rc == mod.EXIT_FEASIBILITY
    assert "feasibility: unknown_page" in out and "page_shape: BEGIN" in out
    assert USER.lower() not in out.lower()


class _LongErrorAdapter(_LeakyAdapter):
    def login(self, page, url, username, password):
        # The username starts at index 495, so the old str(exc)[:500] cut
        # kept "bo-jd" and dropped the rest: no longer a match for redaction.
        raise RuntimeError("z" * 494 + " " + USER + " trailing")


def test_long_exception_is_redacted_before_it_is_truncated(monkeypatch, capsys):
    mod = _load_script()
    pkg, sync_api = _fake_playwright()
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setenv("BREAKOUT_DX_USERNAME", USER)
    monkeypatch.setenv("BREAKOUT_DX_PASSWORD", PASSWORD)
    monkeypatch.setattr(mod, "adapter_for_platform", lambda _p: _LongErrorAdapter())

    rc = mod.main(["--account", "breakout_1"])
    out = capsys.readouterr().out

    assert rc == mod.EXIT_ERROR and "login: ERROR (RuntimeError" in out
    assert "bo-jd" not in out.lower()
