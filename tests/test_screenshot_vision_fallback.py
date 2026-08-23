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


def test_a_provider_level_ScreenshotParseError_DOES_fall_back(monkeypatch):
    """REVERSES a prior deliberate decision — read this before changing it back.

    This test previously asserted the opposite (`..._is_NOT_retried`), on the
    reasoning that "burning the fallback on a missing key would turn one clear
    message into a confusing second failure from a different provider".

    Two things retired that reasoning on 2026-08-23:

    1. **The premise no longer holds.** `_call_vision` now reports BOTH causes
       when the fallback also fails, so a retry can no longer replace a clear
       message with a confusing one — see
       `test_when_BOTH_providers_fail_the_operator_is_told_BOTH`.
    2. **Every `ScreenshotParseError` these two callers raise is
       provider-level and retryable on the OTHER provider**: SDK not installed,
       API key absent, HTTP non-200, unreadable response, empty completion.
       None of them says anything about the image. Short-circuiting on them
       meant a missing `ANTHROPIC_API_KEY` took out the whole manual bridge
       while a working `GEMINI_API_KEY` sat unused on the same VM — which is
       precisely the single-point-of-failure this fallback was built for.

    The genuinely non-retryable checks (empty image, unsupported media type)
    live in `parse_screenshot`, above this call, and are unaffected.
    """
    calls = []

    def unusable(model_id, b64, mt):
        calls.append(model_id)
        raise sp.ScreenshotParseError("no ANTHROPIC_API_KEY set")

    def good(model_id, b64, mt):
        calls.append(model_id)
        return '{"reports": []}'

    monkeypatch.setattr(sp, "_call_vision_anthropic", unusable)
    monkeypatch.setattr(sp, "_call_vision_gemini", good)
    monkeypatch.setenv("PROP_SCREENSHOT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("PROP_SCREENSHOT_FALLBACK_MODEL", "gemini-2.5-flash")

    assert sp._call_vision("Zm9v", "image/png") == '{"reports": []}'
    assert calls == ["claude-sonnet-5", "gemini-2.5-flash"], calls


def test_when_BOTH_providers_fail_the_operator_is_told_BOTH(monkeypatch):
    """The message must name the PRIMARY's cause, not only the fallback's.

    OBSERVED LIVE 2026-08-23 on the prop bot. The operator sent a Breakout
    terminal screenshot and got back::

        ⚠️ screenshot reading failed (gemini-2.5-flash HTTP 404) — type the
        report instead.

    The fallback's error propagated ALONE, because the fallback call was not
    wrapped. So the message named Gemini — the provider that was merely SECOND
    — while whatever took out the primary appeared only in a VM log line the
    operator never sees. A reader trusting that message would go debug Gemini.

    That is a failure message naming a cause no code path tested: CLAUDE.md
    § "Diagnostic provenance", sub-class A (semantic substitution). Both causes
    now travel, and the assertions below check for BOTH — a message carrying
    only one of them fails this test whichever one it is.
    """
    def anthropic_broken(model_id, b64, mt):
        raise RuntimeError("credit balance is too low")

    def gemini_broken(model_id, b64, mt):
        raise sp.ScreenshotParseError(
            f"screenshot reading failed ({model_id} HTTP 404)")

    monkeypatch.setattr(sp, "_call_vision_anthropic", anthropic_broken)
    monkeypatch.setattr(sp, "_call_vision_gemini", gemini_broken)
    monkeypatch.setenv("PROP_SCREENSHOT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("PROP_SCREENSHOT_FALLBACK_MODEL", "gemini-2.5-flash")

    with pytest.raises(sp.ScreenshotParseError) as ei:
        sp._call_vision("Zm9v", "image/png")
    msg = str(ei.value)
    # The fallback's cause — what the operator DID see.
    assert "gemini-2.5-flash" in msg and "404" in msg, msg
    # The primary's cause — what they did NOT, and the whole point of this row.
    assert "claude-sonnet-5" in msg, msg
    assert "credit balance is too low" in msg, msg


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
