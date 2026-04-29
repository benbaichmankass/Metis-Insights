"""
S-005 M5: VM deploy verification script.

Checks that all required env vars and S-005 multi-strategy config are
present before the live trader is started.  Exits 0 on success, 1 on
any issue.

Usage:
    python scripts/verify_deploy.py [--env /path/to/.env]

Output is printed to stdout and optionally sent to Telegram when
TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set.
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    from dotenv import load_dotenv
    _DOTENV = True
except ImportError:
    _DOTENV = False


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

_REQUIRED_STRINGS = ["MODE", "SYMBOL", "TIMEFRAME", "EXCHANGE"]
_REQUIRED_FLOATS = ["RISK_PER_TRADE", "MAX_QTY"]
_SAFETY_FLAGS = ["DRY_RUN", "ALLOW_LIVE_TRADING", "BYBIT_TESTNET"]

# S-005 multi-strategy config
_S005_FLOAT_CAPS = ["MAX_POS_PER_STRATEGY", "MAX_DAILY_LOSS_PER_STRATEGY_USD"]


def _check(label: str, ok: bool, detail: str = "") -> tuple[bool, str]:
    prefix = "OK  " if ok else "ERR "
    msg = f"{prefix} {label}"
    if detail:
        msg += f" = {detail}"
    return ok, msg


def run_checks(env: dict) -> tuple[list[str], list[str]]:
    """Return (lines, issues) where issues is a list of failed check names."""
    lines: list[str] = []
    issues: list[str] = []

    lines.append("── Core env vars ──────────────────────────────")
    for key in _REQUIRED_STRINGS:
        val = env.get(key, "")
        ok, msg = _check(key, bool(val), val or "NOT SET")
        lines.append(msg)
        if not ok:
            issues.append(key)

    lines.append("")
    for key in _REQUIRED_FLOATS:
        val = env.get(key, "")
        try:
            float(val)
            ok, msg = _check(key, True, val)
        except (TypeError, ValueError):
            ok, msg = _check(key, False, val or "NOT SET / invalid")
        lines.append(msg)
        if not ok:
            issues.append(key)

    lines.append("")
    lines.append("── Safety flags ────────────────────────────────")
    for key in _SAFETY_FLAGS:
        val = env.get(key, "NOT SET")
        _, msg = _check(key, True, val)
        lines.append(msg)

    lines.append("")
    lines.append("── S-005 multi-strategy caps (optional) ────────")
    for key in _S005_FLOAT_CAPS:
        val = env.get(key, "")
        if not val:
            lines.append(f"--   {key} = not set (cap disabled)")
        else:
            try:
                float(val)
                _, msg = _check(key, True, val)
                lines.append(msg)
            except ValueError:
                _, msg = _check(key, False, f"{val!r} — not a number")
                lines.append(msg)
                issues.append(key)

    lines.append("")
    lines.append("── Pipeline import check ───────────────────────")
    try:
        from src.runtime.pipeline import STRATEGIES, STRATEGY_RISK_PCT  # type: ignore
        if len(STRATEGIES) < 4:
            _, msg = _check(
                "STRATEGIES",
                False,
                f"only {len(STRATEGIES)} entries — expected ≥4",
            )
            lines.append(msg)
            issues.append("STRATEGIES")
        else:
            _, msg = _check("STRATEGIES", True, str(STRATEGIES))
            lines.append(msg)
        total = sum(STRATEGY_RISK_PCT.values())
        pct_ok = abs(total - 1.0) < 1e-6
        _, msg = _check(
            "STRATEGY_RISK_PCT sum",
            pct_ok,
            f"{total:.3f} ({'ok' if pct_ok else 'must be 1.0'})",
        )
        lines.append(msg)
        if not pct_ok:
            issues.append("STRATEGY_RISK_PCT_SUM")
    except Exception as exc:
        _, msg = _check("pipeline import", False, str(exc))
        lines.append(msg)
        issues.append("pipeline_import")

    return lines, issues


def _notify_telegram(token: str, chat_id: str, text: str) -> None:
    try:
        import requests  # type: ignore
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as exc:
        print(f"Telegram notify failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify deploy environment (S-005)")
    parser.add_argument("--env", default="", help="Path to .env file to load")
    args = parser.parse_args(argv)

    env_path = args.env or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.live"
    )
    if _DOTENV and os.path.exists(env_path):
        load_dotenv(env_path, override=False)

    env = dict(os.environ)

    header = ["*ICT Trading Bot — Deploy Verification (S-005)*", ""]
    lines, issues = run_checks(env)

    summary_lines: list[str] = [""]
    if issues:
        summary_lines.append(
            f"FAIL: {len(issues)} issue(s) — {', '.join(issues)}"
        )
        summary_lines.append("Trader should NOT start until issues are resolved.")
    else:
        summary_lines.append("PASS: All required checks passed. Trader is safe to start.")

    full = "\n".join(header + lines + summary_lines)
    print(full)

    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        _notify_telegram(token, chat_id, full[:4000])
    else:
        print("\n(Telegram notification skipped — no token/chat_id in env)",
              file=sys.stderr)

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
