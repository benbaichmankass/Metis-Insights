#!/usr/bin/env python3
"""Automated machine-side health check (layer 1 of 2).

Reads a snapshot file produced by ``scripts/collect_health_snapshot.sh``,
asks Claude Haiku 4.5 to classify it against the schema in
``.claude/health_check_prompt.md``, and writes the structured result to
``runtime_logs/health_checks/health_check_<UTC-ISO>.json`` plus a stable
``runtime_logs/health_checks/latest.json`` symlink-ish copy.

A non-HEALTHY machine result fires a Telegram alert via the bot's
existing ``src.runtime.notify.send_telegram_direct`` helper.

This script is **only** the machine layer. The Claude review (layer 2)
is requested separately by ``scripts/write_health_review_request.py``,
unconditionally on every workflow run — see ``docs/runbooks/health-check.md``.

Fallback behaviour
------------------
If the Anthropic call itself fails (rate limit, billing, network, or
malformed JSON in the response), we synthesize an ``UNKNOWN``-status
stub report instead of erroring out, write it to disk, and still fire a
Telegram alert. This preserves the design contract that the layer-2
Claude review request is emitted on **every** run — even when layer 1
is unavailable, the routine still gets the raw snapshot.

Exit codes:
  0 — a report was written (any verdict status, including UNKNOWN, and
      whether or not the Telegram alert succeeded).
  1 — could not read snapshot or prompt file; fundamentally broken
      input. The downstream layer-2 step is skipped.

Env vars:
  ANTHROPIC_API_KEY   required
  HEALTH_CHECK_MODEL  override model id (default: claude-haiku-4-5-20251001)
  TELEGRAM_BOT_TOKEN  optional — if absent, alert step is skipped silently
  TELEGRAM_CHAT_ID    optional
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROMPT_PATH = _REPO_ROOT / ".claude" / "health_check_prompt.md"
_OUT_DIR = _REPO_ROOT / "runtime_logs" / "health_checks"
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_SNAPSHOT_CHARS = 60_000  # tail-truncate to keep token cost bounded

# Section keys mirrored in .claude/health_check_prompt.md so the stub
# report keeps the same shape consumers expect.
_CHECK_KEYS = (
    "processes", "heartbeat", "ticks", "signals", "orders",
    "trades", "monitoring", "api", "errors", "resources",
)


# ---------------------------------------------------------------------------
# Anthropic call
# ---------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """Tolerate ```json ... ``` wrappers if the model adds them despite
    the prompt telling it not to."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def call_claude(snapshot: str, model: str, prompt: str) -> Dict[str, Any]:
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    if len(snapshot) > _MAX_SNAPSHOT_CHARS:
        snapshot = (
            snapshot[: _MAX_SNAPSHOT_CHARS // 2]
            + f"\n\n... [truncated {len(snapshot) - _MAX_SNAPSHOT_CHARS} chars] ...\n\n"
            + snapshot[-_MAX_SNAPSHOT_CHARS // 2 :]
        )
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": snapshot}],
    )
    parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    raw = _strip_code_fence("".join(parts))
    return json.loads(raw)


def build_unknown_stub(exc: BaseException) -> Dict[str, Any]:
    """Synthesize a layer-1 report when the Anthropic call fails.

    Mirrors the model's expected output shape exactly so downstream
    consumers (write_health_review_request.py, the routine, the
    artifact uploader) handle it without special-casing.
    """
    return {
        "status": "UNKNOWN",
        "summary": (
            f"Layer-1 analysis unavailable: {type(exc).__name__}: {exc}"
        )[:240],
        "checks": {
            k: {"status": "warn", "note": "layer-1 verdict unavailable"}
            for k in _CHECK_KEYS
        },
        "action_required": (
            "Manual review required — automated layer-1 analysis was "
            "not produced for this run. The raw snapshot is the source "
            "of truth for the layer-2 review."
        ),
        "error": {
            "type": type(exc).__name__,
            "message": str(exc)[:500],
        },
    }


# ---------------------------------------------------------------------------
# Telegram alert (reuses the same stdlib-only helper the bot uses)
# ---------------------------------------------------------------------------


def maybe_alert(report: Dict[str, Any], run_url: str | None) -> None:
    """Best-effort Telegram alert on any non-HEALTHY status (WARNING,
    CRITICAL, UNKNOWN). Never raises; failures are logged to stderr
    only — the layer-2 review request must run on every execution
    regardless of alert delivery."""
    status = report.get("status", "")
    if status == "HEALTHY":
        return
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get(
        "TELEGRAM_CHAT_ID"
    ):
        return
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from src.runtime.notify import send_telegram_direct  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[run_health_check] notify-import-failed: {exc}", file=sys.stderr)
        return

    icon = {
        "WARNING": "\U0001F7E1",
        "CRITICAL": "\U0001F534",
        "UNKNOWN": "⚪",
    }.get(status, "⚠️")
    summary = str(report.get("summary", ""))[:200]
    action = str(report.get("action_required") or "").strip()
    parts = [f"{icon} ICT bot health: {status}", summary]
    if action:
        parts.append(f"Action: {action}")
    if run_url:
        parts.append(run_url)
    try:
        send_telegram_direct("\n".join(parts))
    except Exception as exc:  # noqa: BLE001
        print(f"[run_health_check] telegram-send-failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot", type=Path, help="path to health_snapshot.txt")
    p.add_argument(
        "--model",
        default=os.environ.get("HEALTH_CHECK_MODEL", _DEFAULT_MODEL),
        help="Anthropic model id (default: %(default)s)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=_OUT_DIR,
        help="directory for the JSON report (default: %(default)s)",
    )
    p.add_argument(
        "--run-url",
        default=os.environ.get("GITHUB_SERVER_URL", "")
        + (
            f"/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            if os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID")
            else ""
        ),
        help="link to embed in the Telegram alert",
    )
    args = p.parse_args(argv)

    if not args.snapshot.is_file():
        print(f"[run_health_check] snapshot missing: {args.snapshot}", file=sys.stderr)
        return 1
    if not _PROMPT_PATH.is_file():
        print(f"[run_health_check] prompt missing: {_PROMPT_PATH}", file=sys.stderr)
        return 1

    snapshot = args.snapshot.read_text(encoding="utf-8", errors="replace")
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    try:
        report = call_claude(snapshot, args.model, prompt)
    except Exception as exc:  # noqa: BLE001
        # Layer 1 unavailable — synthesize an UNKNOWN-status stub and
        # carry on so the layer-2 review request still gets emitted.
        # See module docstring ("Fallback behaviour") for rationale.
        print(f"[run_health_check] claude-call-failed, using UNKNOWN stub: {exc}",
              file=sys.stderr)
        report = build_unknown_stub(exc)

    now = _dt.datetime.now(_dt.timezone.utc)
    report["timestamp"] = now.isoformat()
    report["model"] = args.model

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out_dir / f"health_check_{stamp}.json"
    latest_path = args.out_dir / "latest.json"
    payload = json.dumps(report, indent=2, sort_keys=True)
    out_path.write_text(payload + "\n", encoding="utf-8")
    latest_path.write_text(payload + "\n", encoding="utf-8")

    icon = {
        "HEALTHY": "\U0001F7E2",
        "WARNING": "\U0001F7E1",
        "CRITICAL": "\U0001F534",
        "UNKNOWN": "⚪",
    }.get(report.get("status", ""), "⚪")
    print(f"{icon} {report.get('status', 'UNKNOWN')}: {report.get('summary', '')}")
    print(f"report: {out_path}")
    print(f"latest: {latest_path}")

    # Forward path to GitHub Actions output if present.
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"report_path={out_path}\n")
            fh.write(f"latest_path={latest_path}\n")
            fh.write(f"status={report.get('status', 'UNKNOWN')}\n")

    maybe_alert(report, args.run_url or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
