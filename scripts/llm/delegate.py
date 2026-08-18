"""Run one bounded delegated subtask against an OpenAI-compatible LLM.

Executes inside the ``llm-delegate`` GitHub Actions job — the worker's whole
lifecycle IS the job, so there is no server to start, health-check, idle out or
stop. Backend is swappable by env var (Cerebras by default, Groq as failover);
nothing here is provider-specific beyond the base URL.

The result envelope is **three-state and never bare-empty**:

    completed      — the model answered; ``output`` is that answer
    failed         — we tried and it did not work (HTTP error, quota, timeout)
    not_attempted  — we never called the model (scope refusal, missing key)

That distinction is the whole point. An empty ``output`` under a bare success
reads as "the model found nothing", which is a confident wrong answer when the
truth is "we never asked" — the unasserted-denominator failure this repo has
already been bitten by. ``reason`` is mandatory for the two non-completed
states.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.llm.scope_guard import resolve_paths  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-3.6-flash"
# Backend chosen 2026-08-18 after Cerebras returned HTTP 402 payment_required on
# this account: the widely-quoted "1M tokens/day free" figure came from
# third-party blogs, not Cerebras docs, and does not hold here. Gemini is the
# default because its key is already in use by this repo's course-generation
# workflows, so it is proven working AND proven free-tier on this account.
# Cerebras stays selectable via the workflow's `backend` input.
# Thinking models (Gemini 3.x, gpt-oss) spend reasoning tokens against this
# same budget BEFORE emitting a visible answer. Measured 2026-08-18: a 1200
# cap yielded 1149 reasoning tokens and 47 visible ones, truncated mid-word.
# The default is therefore sized for reasoning + answer, not answer alone.
DEFAULT_MAX_OUTPUT_TOKENS = 8000
REQUEST_TIMEOUT_S = 120

SYSTEM_PROMPT = (
    "You are a delegated worker for a software engineering task. You are given "
    "read-only excerpts of a public repository and one instruction. Answer only "
    "from the provided files. If the files do not contain enough information to "
    "answer, say so explicitly rather than guessing — a stated gap is useful, a "
    "confident invention is not. Be concise and concrete."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(task_id, status, reason=None, **extra):
    env = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "status": status,
        "reason": reason,
        "generated_at": _now(),
    }
    env.update(extra)
    if status in ("failed", "not_attempted") and not reason:
        env["reason"] = "unspecified (bug: a non-completed status must carry a reason)"
    return env


def build_prompt(instruction: str, files: list[tuple[str, str]]) -> str:
    parts = [f"## Instruction\n{instruction}\n", "## Files"]
    for path, content in files:
        parts.append(f"\n### `{path}`\n```\n{content}\n```")
    return "\n".join(parts)


def call_model(prompt: str, *, base_url, model, api_key, max_tokens) -> tuple[dict | None, str | None]:
    """Returns (payload, error). Exactly one is non-None."""
    import httpx

    try:
        r = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
            timeout=REQUEST_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — surface the class, never swallow
        return None, f"transport error calling {base_url}: {type(exc).__name__}: {exc}"

    if r.status_code == 429:
        # Loud on purpose: a quota-exhausted run must never look like an
        # answered task with nothing to say.
        return None, f"rate limited / quota exhausted (HTTP 429): {r.text[:400]}"
    if r.status_code >= 400:
        return None, f"backend returned HTTP {r.status_code}: {r.text[:400]}"

    try:
        return r.json(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"backend returned non-JSON: {type(exc).__name__}: {r.text[:200]}"


def run(spec: dict, repo_root: Path) -> dict:
    task_id = spec.get("task_id") or "unnamed"
    instruction = (spec.get("instruction") or "").strip()
    paths = spec.get("paths") or []
    max_tokens = int(spec.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS)

    if not instruction:
        return envelope(task_id, "not_attempted", "spec has no 'instruction'")

    base_url = os.environ.get("LLM_DELEGATE_BASE_URL", DEFAULT_BASE_URL)
    model = spec.get("model") or os.environ.get("LLM_DELEGATE_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("LLM_DELEGATE_API_KEY", "")

    backend = {"base_url": base_url, "model": model}

    if not api_key:
        return envelope(
            task_id, "not_attempted",
            "LLM_DELEGATE_API_KEY is empty — the backend secret is not wired to this job",
            backend=backend,
        )

    verdicts, refusal = resolve_paths(paths, repo_root)
    scope = {
        "requested": len(paths),
        "verdicts": [
            {"path": v.path, "verdict": v.verdict, "reason": v.reason, "size_bytes": v.size_bytes}
            for v in verdicts
        ],
        "total_bytes": sum(v.size_bytes or 0 for v in verdicts if v.ok),
    }
    if refusal:
        # Fail closed on the WHOLE batch — never send the allowed subset.
        return envelope(task_id, "not_attempted", f"scope guard refused: {refusal}",
                        backend=backend, scope=scope)

    files = [(v.path, (repo_root / v.path).read_text(errors="replace")) for v in verdicts if v.ok]
    prompt = build_prompt(instruction, files)

    t0 = time.monotonic()
    payload, err = call_model(prompt, base_url=base_url, model=model,
                              api_key=api_key, max_tokens=max_tokens)
    elapsed = round(time.monotonic() - t0, 2)

    if err:
        return envelope(task_id, "failed", err, backend=backend, scope=scope,
                        duration_s=elapsed)

    try:
        choice = payload["choices"][0]
        output = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return envelope(task_id, "failed",
                        f"backend response missing choices[0].message.content: "
                        f"{json.dumps(payload)[:300]}",
                        backend=backend, scope=scope, duration_s=elapsed)

    if not (output or "").strip():
        # An empty completion is a FAILURE, not an empty answer.
        return envelope(task_id, "failed",
                        "backend returned an empty completion",
                        backend=backend, scope=scope, duration_s=elapsed,
                        usage=payload.get("usage"))

    usage = payload.get("usage") or {}

    # A response cut off at the token ceiling is NOT a completed answer. Saying
    # "completed" over a mid-sentence truncation is the same class of error as
    # an empty completion reading as "found nothing" — the reader cannot tell
    # the model finished from the model being silenced. The partial text is
    # still returned so nothing is lost, but the status is honest.
    if finish_reason == "length":
        visible = usage.get("completion_tokens")
        total = usage.get("total_tokens")
        detail = f" (visible completion_tokens={visible}, total_tokens={total};" \
                 " a thinking model spends reasoning tokens against the same" \
                 " budget — raise max_output_tokens)" if visible is not None else ""
        return envelope(task_id, "failed",
                        f"response truncated at the token ceiling{detail}",
                        backend=backend, scope=scope, duration_s=elapsed,
                        usage=usage, finish_reason=finish_reason, output=output)

    return envelope(task_id, "completed", None, backend=backend, scope=scope,
                    duration_s=elapsed, usage=usage,
                    finish_reason=finish_reason, output=output)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one delegated LLM subtask.")
    ap.add_argument("--spec", help="path to a task-spec JSON file")
    ap.add_argument("--spec-json", help="task spec as an inline JSON string")
    ap.add_argument("--out", help="write the result envelope here")
    ap.add_argument("--repo-root", default=".", help="repo root for path resolution")
    args = ap.parse_args()

    if args.spec:
        spec = json.loads(Path(args.spec).read_text())
    elif args.spec_json:
        spec = json.loads(args.spec_json)
    else:
        ap.error("one of --spec / --spec-json is required")

    result = run(spec, Path(args.repo_root).resolve())
    text = json.dumps(result, indent=2, ensure_ascii=False)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    print(text)

    # Exit non-zero only when we genuinely failed; a scope refusal is a
    # correct, intended outcome and should not read as a broken workflow.
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
