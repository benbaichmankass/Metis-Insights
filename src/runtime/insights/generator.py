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

# Endpoints valid for the CLI / generate(). The strategy endpoint
# requires an extra --strategy arg.
_VALID_ENDPOINTS = {"summary", "recent", "strategy", "health"}

# Generator mode. `template` (default) is provider-free and produces
# deterministic rule-based prose; `anthropic` calls the Claude API.
# Other providers (groq, openai, …) can be added later by branching
# in generate() — the cache + history + usage surfaces are
# provider-agnostic.
_VALID_MODES = {"template", "anthropic"}


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
    if _mode() == "template":
        return template_analyst.MODEL_ID
    env_key = f"INSIGHTS_MODEL_{endpoint.upper()}"
    return os.environ.get(env_key) or _DEFAULT_MODELS[endpoint]


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
        cache.write_cache(_cache_name_for(endpoint, strategy_name), payload)
        history.append_history(
            endpoint=endpoint,
            payload=payload,
            strategy_name=strategy_name,
        )
        usage.record_usage(
            endpoint=endpoint,
            model_id=model_id,
            input_tokens=0,
            output_tokens=0,
            status="ok",
        )
        return payload

    # Paid-provider path (mode == "anthropic" today; groq/openai later).
    if endpoint == "summary":
        system_blocks, user_text = prompts.summary_prompt(data)
    elif endpoint == "recent":
        system_blocks, user_text = prompts.recent_prompt(data, limit)
    elif endpoint == "strategy":
        system_blocks, user_text = prompts.strategy_prompt(strategy_name or "", data)
    else:  # health
        system_blocks, user_text = prompts.health_prompt(data)

    caller = anthropic_call or _call_anthropic
    try:
        result = caller(model_id, system_blocks, user_text)
    except Exception:  # noqa: BLE001 — generator never raises on API err
        logger.exception("insights.generator: anthropic call failed for %s", endpoint)
        usage.record_usage(
            endpoint=endpoint,
            model_id=model_id,
            input_tokens=0,
            output_tokens=0,
            status="error",
        )
        return None

    parsed = _parse_model_output(result.get("text", "") or "")
    payload = _envelope(endpoint, data, parsed, model_id)

    cache_name = _cache_name_for(endpoint, strategy_name)
    cache.write_cache(cache_name, payload)

    history.append_history(
        endpoint=endpoint,
        payload=payload,
        strategy_name=strategy_name,
    )
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
