"""AI Analyst generator — the writer-side process.

This module is invoked by the ``ict-insights-generator`` systemd
timer (lands in PR D). It is NEVER invoked from a FastAPI request
handler — the cache-only-read invariant in the router exists
because this process exists.

Flow per endpoint:

  1. Honour the ``INSIGHTS_ENABLED=0`` kill switch.
  2. Check the monthly budget (``usage.budget_check``). If over,
     record a ``budget_skipped`` usage row, leave the cache file
     untouched, and exit cleanly.
  3. Pull the joined data via ``data_sources``.
  4. Build the prompt via ``prompts``.
  5. Call Anthropic. On any exception, record an ``error`` usage row
     (with zero tokens), leave the cache untouched, and continue with
     the next endpoint.
  6. Parse the model output, build the response envelope.
  7. Write the cache atomically (``cache.write_cache``).
  8. Append the same payload to ``insights_history``.
  9. Record the successful usage row.

The Anthropic client is imported lazily inside the call site so the
module is importable in tests without the SDK installed and without
mock SDK pollution outside the test boundary.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable

from src.runtime.insights import (
    cache,
    data_sources,
    history,
    prompts,
    template_analyst,
    usage,
)

logger = logging.getLogger(__name__)


# Model selection per endpoint. Defaults follow the operator's
# 2026-05-26 decision: Haiku for high-cadence/low-nuance endpoints,
# Sonnet for the deeper grading endpoints. Per-endpoint env overrides
# let the operator dial cost vs quality without a code change.
_DEFAULT_MODELS = {
    "summary": "claude-haiku-4-5-20251001",
    "recent": "claude-haiku-4-5-20251001",
    "strategy": "claude-sonnet-4-6",
    "health": "claude-sonnet-4-6",
}
_MAX_OUTPUT_TOKENS = 800

# Default models per endpoint when INSIGHTS_MODEL_MODE=gemini. ALL endpoints
# use gemini-3.5-flash — the model that actually has free-tier quota on this
# project's key. (2026-07-14: gemini-2.0-flash returned HTTP 429 with
# "free_tier_requests limit: 0" for this key — Google grants NO free quota for
# 2.0-flash on this project, whereas 3.5-flash works, as the dashboard's
# course-generation on the same key proved.) Free-tier request/day caps are
# per-MODEL and can be 0 for a given model, so a single pinned model is fragile;
# `_call_gemini_with_fallback` tries the fallback chain below on 403/404/429 so
# a zero-quota or deprecated model can't dead-end the analyst. Per-endpoint
# INSIGHTS_MODEL_<ENDPOINT> env overrides still win when set (e.g. to pin a
# stronger model once billing raises the quota).
_DEFAULT_GEMINI_MODELS = {
    "summary": "gemini-3.5-flash",
    "recent": "gemini-3.5-flash",
    "health": "gemini-3.5-flash",
    "strategy": "gemini-3.5-flash",
}

# Ordered fallback for the gemini path: try the configured/default model first,
# then these, skipping any that 403/404 (not in this key's catalog) or 429 (no
# free-tier quota / rate-limited). First success wins and its id is recorded as
# the actual model on the envelope + usage row.
_GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

# Endpoints valid for the CLI / generate(). The strategy endpoint
# requires an extra --strategy arg.
_VALID_ENDPOINTS = {"summary", "recent", "strategy", "health"}

# Generator mode. `template` (default) is provider-free and produces
# deterministic rule-based prose; `anthropic` calls the Claude API;
# `gemini` calls the Google Generative Language API. Other providers
# can be added later by branching in generate() — the cache + history
# + usage surfaces are provider-agnostic.
_VALID_MODES = {"template", "anthropic", "gemini"}


def _mode() -> str:
    raw = os.environ.get("INSIGHTS_MODEL_MODE", "template").strip().lower()  # allow-silent: provider switch for the read-only analyst (M13 S2); default `template` so the analyst works without any API key
    if raw not in _VALID_MODES:
        logger.warning(
            "insights.generator: INSIGHTS_MODEL_MODE=%r is not one of %s; "
            "falling back to 'template'",
            raw,
            sorted(_VALID_MODES),
        )
        return "template"
    return raw


def _enabled() -> bool:
    raw = os.environ.get("INSIGHTS_ENABLED", "1").strip().lower()  # allow-silent: kill switch for the read-only analyst process; not on the live/dry path (M13 S1)
    return raw not in {"0", "false", "no", ""}


def _model_for(endpoint: str) -> str:
    mode = _mode()
    if mode == "template":
        return template_analyst.MODEL_ID
    env_key = f"INSIGHTS_MODEL_{endpoint.upper()}"
    explicit = os.environ.get(env_key)
    if explicit:
        return explicit
    if mode == "gemini":
        return _DEFAULT_GEMINI_MODELS[endpoint]
    return _DEFAULT_MODELS[endpoint]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Anthropic call (lazy-imported)
# ---------------------------------------------------------------------------


def _call_anthropic(
    model_id: str,
    system_blocks: list[dict[str, Any]],
    user_text: str,
) -> dict[str, Any]:
    """Call the Anthropic Messages API.

    Returns a dict with ``text``, ``input_tokens``, ``output_tokens``,
    ``cache_creation_input_tokens``, ``cache_read_input_tokens``. Lazy
    import means the SDK is not loaded at module-import time — this
    is what keeps ``tests/test_insights_router.py`` happy (the router
    must not pull in anthropic).
    """
    import anthropic  # noqa: F401  (lazy on purpose)

    client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY from env
    resp = client.messages.create(
        model=model_id,
        max_tokens=_MAX_OUTPUT_TOKENS,
        system=system_blocks,
        messages=[{"role": "user", "content": user_text}],
    )
    text = ""
    for block in resp.content or []:
        # SDK returns ContentBlock objects with a .type and .text on
        # text blocks; we only care about text output.
        if getattr(block, "type", None) == "text":
            text += getattr(block, "text", "") or ""
    u = resp.usage
    return {
        "text": text.strip(),
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        "cache_creation_input_tokens": int(
            getattr(u, "cache_creation_input_tokens", 0) or 0
        ),
        "cache_read_input_tokens": int(
            getattr(u, "cache_read_input_tokens", 0) or 0
        ),
    }


# ---------------------------------------------------------------------------
# Gemini call (REST via httpx — no SDK dependency)
# ---------------------------------------------------------------------------

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _flatten_system_blocks(blocks: list[dict[str, Any]]) -> str:
    """Collapse the cache_control-marked block list down to plain text.

    Gemini's REST API does not honour Anthropic's `cache_control` markers;
    its own context-caching is a separate API call keyed by cache id. We
    just concatenate the text and drop the marker — quality is unaffected.
    """
    parts = []
    for blk in blocks or []:
        if isinstance(blk, dict):
            t = blk.get("text")
            if isinstance(t, str):
                parts.append(t)
    return "\n\n".join(parts)


def _call_gemini(
    model_id: str,
    system_blocks: list[dict[str, Any]],
    user_text: str,
) -> dict[str, Any]:
    """Call the Google Generative Language API.

    Returns the same dict shape ``_call_anthropic`` returns:
    ``{text, input_tokens, output_tokens, cache_creation_input_tokens,
    cache_read_input_tokens}``. Gemini does not expose Anthropic-style
    per-call prompt-caching metrics, so the cache_* fields are always
    0 — the downstream cost calculation still works because the
    public price table prices Gemini's full token count uniformly.

    Auth: ``GEMINI_API_KEY`` env var, passed via the ``X-goog-api-key``
    header (NOT the URL ?key=…) so the key never enters a request
    URL that could be incidentally logged.
    """
    import httpx  # already in requirements; lazy for symmetry with anthropic path

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set — required for INSIGHTS_MODEL_MODE=gemini"
        )

    url = f"{_GEMINI_BASE_URL}/{model_id}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "systemInstruction": {"parts": [{"text": _flatten_system_blocks(system_blocks)}]},
        "generationConfig": {
            "maxOutputTokens": _MAX_OUTPUT_TOKENS,
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key,
    }

    # One retry on 429 with a 2s backoff — covers transient rate-limit
    # bursts on a freshly provisioned project key. The free tier's
    # per-minute window resets quickly, so a single retry is usually
    # enough. Persistent 429s indicate either the Generative Language
    # API isn't enabled in the project, or a real quota cap is being
    # hit — the error-body logging below makes both visible.
    import time as _time

    def _post_once() -> Any:
        with httpx.Client(timeout=30.0) as client:
            return client.post(url, json=body, headers=headers)

    resp = _post_once()
    if resp.status_code == 429:
        _time.sleep(2.0)
        resp = _post_once()

    if resp.status_code >= 400:
        # Log Google's actual error payload so the operator can see
        # which quota (RPM / TPM / RPD) was hit, or whether the API
        # is disabled / billing-required / project misconfigured.
        try:
            err_body = resp.json()
        except Exception:  # noqa: BLE001
            err_body = {"raw_text": resp.text[:500]}
        logger.error(
            "insights.generator: gemini %s returned HTTP %d: %s",
            model_id,
            resp.status_code,
            err_body,
        )
        resp.raise_for_status()  # bubbles up — generator.generate() catches it
    payload = resp.json()

    candidates = payload.get("candidates") or []
    text = ""
    if candidates:
        parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
        for p in parts:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                text += p["text"]

    usage = payload.get("usageMetadata") or {}
    return {
        "text": text.strip(),
        "input_tokens": int(usage.get("promptTokenCount", 0) or 0),
        "output_tokens": int(usage.get("candidatesTokenCount", 0) or 0),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def _call_gemini_with_fallback(
    model_id: str,
    system_blocks: list[dict[str, Any]],
    user_text: str,
) -> dict[str, Any]:
    """`_call_gemini` with a model-fallback chain.

    Free-tier request/day caps are per-MODEL and can be 0 for a given model on
    a given project (2026-07-14: gemini-2.0-flash was `free_tier limit: 0` for
    this key), so a single pinned model is fragile. Try the configured model
    first, then `_GEMINI_FALLBACK_MODELS`, skipping any that 403/404 (not in the
    key's catalog) or 429 (no quota / rate-limited). The returned dict carries
    ``model_id`` = the model that actually served, so the caller records the
    real model on the envelope + usage row. A non-quota error (400/5xx) on the
    configured model surfaces immediately (it's a real bug, not a bad model).
    """
    import httpx

    candidates = [model_id] + [m for m in _GEMINI_FALLBACK_MODELS if m != model_id]
    tried: list[str] = []
    last_exc: Exception | None = None
    for m in candidates:
        try:
            result = _call_gemini(m, system_blocks, user_text)
            result["model_id"] = m
            if m != model_id:
                logger.info("insights.generator: gemini fell back to %s (from %s)", m, model_id)
            return result
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            tried.append(f"{m}:{code}")
            last_exc = exc
            if code in (403, 404, 429):  # catalog / no-quota — try the next candidate
                logger.warning(
                    "insights.generator: gemini %s HTTP %d (catalog/quota) — trying next candidate",
                    m, code,
                )
                continue
            raise  # genuine error (400/5xx) — surface it
    logger.error("insights.generator: all gemini candidates failed: %s", ", ".join(tried))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("no gemini candidates available")


# ---------------------------------------------------------------------------
# Envelope assembly
# ---------------------------------------------------------------------------


def _parse_model_output(text: str) -> dict[str, Any]:
    """Parse the model's JSON response, falling back to a placeholder.

    The prompt mandates "JSON ONLY, no code fences" but models
    occasionally wrap output. We strip a single fenced block if
    present, then JSON-decode. A parse failure produces a neutral
    envelope with the raw text in ``summary_md`` rather than failing
    the whole run.
    """
    body = text.strip()
    if body.startswith("```"):
        # strip first and last fence lines
        lines = body.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    try:
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError("model output was not a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("insights.generator: parse failed (%s); raw=%r", exc, text[:200])
        return {
            "summary_md": text,
            "grade": "good",
            "signals": [],
        }
    return {
        "summary_md": str(parsed.get("summary_md", "") or ""),
        "grade": str(parsed.get("grade", "good") or "good"),
        "signals": parsed.get("signals") or [],
    }


def _envelope(
    endpoint: str,
    data: dict[str, Any],
    parsed: dict[str, Any],
    model_id: str,
) -> dict[str, Any]:
    return {
        "summary_md": parsed.get("summary_md", ""),
        "grade": parsed.get("grade", "good"),
        "signals": parsed.get("signals", []),
        "data_window": data.get("window"),
        "row_counts": data.get("row_counts"),
        "generated_at": _now_iso(),
        "model_id": model_id,
    }


def _cache_name_for(endpoint: str, strategy_name: str | None) -> str:
    if endpoint == "strategy":
        if not strategy_name:
            raise ValueError("strategy endpoint requires strategy_name")
        return f"strategy_{strategy_name}"
    return endpoint


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate(
    endpoint: str,
    *,
    strategy_name: str | None = None,
    limit: int = 20,
    anthropic_call: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Generate one endpoint's cache. Returns the written payload or None.

    ``anthropic_call`` is an injection seam used by tests — the
    timer/CLI path leaves it ``None`` so the real Anthropic SDK is
    called. None is also returned when the generator is disabled,
    the monthly budget is exhausted, or the API call raised.
    """
    if endpoint not in _VALID_ENDPOINTS:
        raise ValueError(f"unknown endpoint: {endpoint}")

    if not _enabled():
        logger.info("insights.generator: INSIGHTS_ENABLED=0, skipping %s", endpoint)
        return None

    mode = _mode()
    model_id = _model_for(endpoint)

    # Budget gate only applies to paid-provider modes. The template
    # mode never spends, so skipping the check keeps the analyst alive
    # even when the legacy budget row was set to 0.
    if mode != "template":
        under_budget, spent, budget = usage.budget_check()
        if not under_budget:
            logger.warning(
                "insights.generator: monthly budget exhausted ($%.2f / $%.2f); "
                "skipping %s (last-good cache preserved)",
                spent,
                budget,
                endpoint,
            )
            usage.record_usage(
                endpoint=endpoint,
                model_id=model_id,
                input_tokens=0,
                output_tokens=0,
                status="budget_skipped",
            )
            return None

    # Pull data (always — the template path consumes the same payload).
    if endpoint == "summary":
        data = data_sources.summary_data()
    elif endpoint == "recent":
        data = data_sources.recent_data(limit=limit)
    elif endpoint == "strategy":
        if not strategy_name:
            raise ValueError("strategy endpoint requires strategy_name")
        data = data_sources.strategy_data(strategy_name)
    else:  # health
        data = data_sources.health_data()

    if mode == "template":
        try:
            parsed = template_analyst.render(
                endpoint,
                data,
                strategy_name=strategy_name,
                limit=limit,
            )
        except Exception:  # noqa: BLE001 — template should never raise, but degrade gracefully if it does
            logger.exception("insights.generator: template render failed for %s", endpoint)
            usage.record_usage(
                endpoint=endpoint,
                model_id=model_id,
                input_tokens=0,
                output_tokens=0,
                status="error",
            )
            return None

        payload = _envelope(endpoint, data, parsed, model_id)
        # Durable canonical record FIRST (WC-5): insights_history is the source
        # of truth, the cache file is a derived hot-read. Writing history first
        # means a crash between the two leaves the canonical row intact and the
        # cache simply catches up next cycle (never the reverse).
        history.append_history(
            endpoint=endpoint,
            payload=payload,
            strategy_name=strategy_name,
        )
        cache.write_cache(_cache_name_for(endpoint, strategy_name), payload)
        usage.record_usage(
            endpoint=endpoint,
            model_id=model_id,
            input_tokens=0,
            output_tokens=0,
            status="ok",
        )
        return payload

    # Paid-provider path. Same prompt building for both Anthropic and
    # Gemini — the prompts module is provider-agnostic; differences in
    # SDK shape are absorbed by `_call_anthropic` / `_call_gemini`.
    if endpoint == "summary":
        system_blocks, user_text = prompts.summary_prompt(data)
    elif endpoint == "recent":
        system_blocks, user_text = prompts.recent_prompt(data, limit)
    elif endpoint == "strategy":
        system_blocks, user_text = prompts.strategy_prompt(strategy_name or "", data)
    else:  # health
        system_blocks, user_text = prompts.health_prompt(data)

    if anthropic_call is not None:
        caller = anthropic_call
    elif mode == "gemini":
        caller = _call_gemini_with_fallback
    else:
        caller = _call_anthropic

    try:
        result = caller(model_id, system_blocks, user_text)
    except Exception:  # noqa: BLE001 — generator never raises on API err
        logger.exception("insights.generator: %s call failed for %s", mode, endpoint)
        usage.record_usage(
            endpoint=endpoint,
            model_id=model_id,
            input_tokens=0,
            output_tokens=0,
            status="error",
        )
        return None

    # The fallback wrapper may have served a different model than requested —
    # record the one that actually produced the output.
    model_id = result.get("model_id", model_id)
    parsed = _parse_model_output(result.get("text", "") or "")
    payload = _envelope(endpoint, data, parsed, model_id)

    # Durable canonical record FIRST (WC-5), then the derived hot-read cache —
    # see the template-path note above. History is the source of truth.
    history.append_history(
        endpoint=endpoint,
        payload=payload,
        strategy_name=strategy_name,
    )
    cache_name = _cache_name_for(endpoint, strategy_name)
    cache.write_cache(cache_name, payload)

    usage.record_usage(
        endpoint=endpoint,
        model_id=model_id,
        input_tokens=int(result.get("input_tokens", 0)),
        output_tokens=int(result.get("output_tokens", 0)),
        cache_creation_tokens=int(result.get("cache_creation_input_tokens", 0)),
        cache_read_tokens=int(result.get("cache_read_input_tokens", 0)),
        status="ok",
    )
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m src.runtime.insights generate --endpoint summary ...``

    The systemd unit (PR D) invokes this with one
    ``--endpoint`` per timer fire, or with ``--all`` to refresh
    every endpoint sequentially.
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.runtime.insights",
        description="AI Analyst generator CLI",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="refresh one cache file")
    gen.add_argument(
        "--endpoint",
        choices=sorted(_VALID_ENDPOINTS),
        help="which endpoint to refresh",
    )
    gen.add_argument("--strategy", help="strategy name (when --endpoint=strategy)")
    gen.add_argument("--limit", type=int, default=20)
    gen.add_argument("--all", action="store_true", help="refresh all four endpoints")

    args = parser.parse_args(argv)

    if args.cmd != "generate":
        parser.print_help()
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.all:
        # Refresh every endpoint. Strategy endpoint is skipped here —
        # the timer drives per-strategy refresh through its own call
        # because the strategy roster is config-driven.
        for endpoint in ("summary", "recent", "health"):
            try:
                generate(endpoint)
            except Exception:  # noqa: BLE001 — never crash the timer
                logger.exception("insights.generator: %s failed", endpoint)
        return 0

    if not args.endpoint:
        parser.error("--endpoint is required unless --all is given")

    if args.endpoint == "strategy" and not args.strategy:
        parser.error("--strategy is required when --endpoint=strategy")

    generate(
        args.endpoint,
        strategy_name=args.strategy,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
