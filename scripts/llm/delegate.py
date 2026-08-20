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
# Raised 8000 -> 32000 on 2026-08-18: a bug-find over a 10.8 KB file still
# truncated at 8000, spending 7.7k reasoning tokens for 317 visible ones.
DEFAULT_MAX_OUTPUT_TOKENS = 32000
REQUEST_TIMEOUT_S = 120

# Retried ONLY for classes that plausibly resolve on their own. A 4xx that is
# not 429 means the request itself is wrong (bad model id, bad key, payment
# required) and retrying it just burns quota and hides the cause.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
BACKOFF_S = (2, 6)

SYSTEM_PROMPT = (
    "You are a delegated worker for a software engineering task. You are given "
    "read-only excerpts of a public repository and one instruction. Answer only "
    "from the provided files. If the files do not contain enough information to "
    "answer, say so explicitly rather than guessing — a stated gap is useful, a "
    "confident invention is not.\n\n"
    "CRITICAL: absence of something in the provided files is NOT evidence it "
    "does not exist. The repository contains many files you have not been "
    "given. If a claim depends on a file that is not provided, say that the "
    "file was not provided and that you cannot verify the claim — never "
    "conclude the claim is false because you cannot see its implementation.\n\n"
    # Added 2026-08-20. Without this the model must COUNT lines to answer a
    # "quote the line number" instruction, and it does that confidently and
    # wrongly: graded over 4 tasks on unfamiliar code, every quoted snippet was
    # verbatim correct and 0 of 11 line citations landed (off by 3, 24, and
    # ~214). A file:line cite is exactly what this repo's conventions teach a
    # reader to trust, so a fabricated one is worse than none.
    "Each file below is presented WITH LINE NUMBERS: every line is prefixed "
    "with its number and a tab. That prefix is presentation, not file content. "
    "When you cite a line, cite the number shown on that line — do not count "
    "or estimate line numbers yourself, and do not include the number prefix "
    "inside code you quote.\n\n"
    "Be concise and concrete."
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


def number_lines(content: str) -> str:
    """1-based `<number>\\t<line>`, the `cat -n` shape.

    The model cannot cite a line number it was never shown, so before this it
    ESTIMATED them — see the SYSTEM_PROMPT note. Right-aligned to a constant
    width so the code column stays aligned and the prefix is visually separable
    from the content; the separator is a TAB so a model quoting code can strip
    it unambiguously (a space would be indistinguishable from indentation).
    """
    lines = content.split("\n")
    width = len(str(len(lines)))
    return "\n".join(f"{n:>{width}}\t{line}" for n, line in enumerate(lines, 1))


def build_prompt(instruction: str, files: list[tuple[str, str]]) -> str:
    parts = [f"## Instruction\n{instruction}\n", "## Files"]
    for path, content in files:
        parts.append(f"\n### `{path}`\n```\n{number_lines(content)}\n```")
    return "\n".join(parts)


def call_model(prompt: str, *, base_url, model, api_key, max_tokens):
    """Returns (payload, error, attempts). Exactly one of payload/error is None."""
    import httpx

    url = f"{base_url.rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last = "no attempt was made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = httpx.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — surface the class, never swallow
            last = f"transport error calling {base_url}: {type(exc).__name__}: {exc}"
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_S[attempt - 1])
                continue
            return None, f"{last} (after {attempt} attempts)", attempt

        if r.status_code in RETRYABLE_STATUS:
            kind = "rate limited / quota exhausted" if r.status_code == 429 else "backend unavailable"
            last = f"{kind} (HTTP {r.status_code}): {r.text[:300]}"
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_S[attempt - 1])
                continue
            return None, f"{last} (after {attempt} attempts)", attempt

        if r.status_code >= 400:
            # Not retryable: the request itself is wrong. Fail on attempt 1.
            return None, f"backend returned HTTP {r.status_code}: {r.text[:400]}", attempt

        try:
            return r.json(), None, attempt
        except Exception as exc:  # noqa: BLE001
            return None, f"backend returned non-JSON: {type(exc).__name__}: {r.text[:200]}", attempt

    return None, last, MAX_ATTEMPTS


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
    payload, err, attempts = call_model(prompt, base_url=base_url, model=model,
                                        api_key=api_key, max_tokens=max_tokens)
    elapsed = round(time.monotonic() - t0, 2)

    if err:
        return envelope(task_id, "failed", err, backend=backend, scope=scope,
                        duration_s=elapsed, attempts=attempts)

    try:
        choice = payload["choices"][0]
        output = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return envelope(task_id, "failed",
                        f"backend response missing choices[0].message.content: "
                        f"{json.dumps(payload)[:300]}",
                        backend=backend, scope=scope, duration_s=elapsed, attempts=attempts)

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
                        usage=usage, finish_reason=finish_reason,
                        attempts=attempts, output=output)

    return envelope(task_id, "completed", None, backend=backend, scope=scope,
                    duration_s=elapsed, usage=usage,
                    finish_reason=finish_reason, attempts=attempts, output=output)


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
