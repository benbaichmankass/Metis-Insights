"""One dead provider key must not take out the whole prop report-back path.

WHY THIS EXISTS
---------------
Measured live 2026-08-20: the operator sent a Breakout terminal screenshot to
the prop bot and got back a raw provider 400 ::

    ⚠ couldn't read that screenshot: Error code: 400 - {'type': 'error',
    'error': {'type': 'invalid_request_error', 'message': 'Your credit balance
    is too low to access the Anthropic API. …'}}

Nothing was wrong with the code, the image, or the VM. A single-provider vision
path means one BILLING event silently removes the screenshot half of the manual
bridge — and the manual bridge is the only way a prop fill ever reaches the
journal, so the failure lands on the money path's data entry.

The fallback is Gemini's free tier: already called elsewhere in this repo
(`src/runtime/insights/generator.py::_call_gemini`), already multimodal, and its
`GEMINI_API_KEY` is ALREADY synced to the live VM by `sync-vm-secrets.yml`. No
new infrastructure — this is a provider branch in one function.

WHAT IS ASSERTED
----------------
Planted failures only; no network. The controls matter more than the happy path:
a fallback that fires on the wrong errors, or falls back to the provider that
just failed, would be worse than none.
"""
from __future__ import annotations

import pytest

from src.prop import screenshot_parse as sp


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("PROP_SCREENSHOT_MODEL", "PROP_SCREENSHOT_FALLBACK_MODEL",
              "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_provider_is_chosen_by_the_model_id():
    assert sp._is_gemini("gemini-2.5-flash")
    assert sp._is_gemini("gemini-2.0-flash")
    assert not sp._is_gemini("claude-sonnet-5")


def test_the_live_billing_failure_reaches_the_gemini_fallback(monkeypatch):
    """The EXACT 2026-08-20 failure, replayed."""
    calls = []

    def dead_key(model_id, b64, mt):
        calls.append(("anthropic", model_id))
        raise RuntimeError(
            "Error code: 400 - {'type': 'error', 'error': {'type': "
            "'invalid_request_error', 'message': 'Your credit balance is too "
            "low to access the Anthropic API.'}}")

    def gemini_ok(model_id, b64, mt):
        calls.append(("gemini", model_id))
        return '{"reports": []}'

    monkeypatch.setattr(sp, "_call_vision_anthropic", dead_key)
    monkeypatch.setattr(sp, "_call_vision_gemini", gemini_ok)
    monkeypatch.setenv("PROP_SCREENSHOT_MODEL", "claude-sonnet-5")

    assert sp._call_vision("Zm9v", "image/png") == '{"reports": []}'
    assert calls == [("anthropic", "claude-sonnet-5"),
                     ("gemini", "gemini-2.5-flash")], calls


def test_it_never_falls_back_to_the_provider_that_just_failed(monkeypatch):
    """A Gemini-primary failure must NOT retry Gemini.

    Without this the 'fallback' would double every quota failure into two
    identical calls and report the same error twice as if it were a second
    opinion.
    """
    calls = []

    def gemini_down(model_id, b64, mt):
        calls.append(model_id)
        raise RuntimeError("429 quota exhausted")

    monkeypatch.setattr(sp, "_call_vision_gemini", gemini_down)
    monkeypatch.setenv("PROP_SCREENSHOT_MODEL", "gemini-2.5-flash")

    with pytest.raises(sp.ScreenshotParseError):
        sp._call_vision("Zm9v", "image/png")
    assert len(calls) == 1, calls


def test_an_operator_readable_error_is_NOT_retried(monkeypatch):
    """`ScreenshotParseError` means 'this environment cannot read images'.

    Burning the fallback on a missing key would turn one clear message into a
    confusing second failure from a different provider.
    """
    calls = []

    def unusable(model_id, b64, mt):
        calls.append(model_id)
        raise sp.ScreenshotParseError("no ANTHROPIC_API_KEY set")

    monkeypatch.setattr(sp, "_call_vision_anthropic", unusable)
    monkeypatch.setenv("PROP_SCREENSHOT_MODEL", "claude-sonnet-5")

    with pytest.raises(sp.ScreenshotParseError, match="no ANTHROPIC_API_KEY"):
        sp._call_vision("Zm9v", "image/png")
    assert len(calls) == 1, calls


def test_gemini_without_a_key_says_so_rather_than_calling(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(sp.ScreenshotParseError, match="GEMINI_API_KEY"):
        sp._call_vision_gemini("gemini-2.5-flash", "Zm9v", "image/png")


def test_an_empty_completion_is_not_read_as_an_empty_screenshot(monkeypatch):
    """A blank model response must RAISE, never return 'no trade found'.

    Reporting an empty extraction here would be the unasserted-denominator
    defect on the money path: the operator would read 'nothing in that image'
    when the truth is 'the model returned nothing'.
    """
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"text": "   "}]}}]}

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k): return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _Client())
    with pytest.raises(sp.ScreenshotParseError, match="no text"):
        sp._call_vision_gemini("gemini-2.5-flash", "Zm9v", "image/png")
