#!/usr/bin/env python3
# Best-effort secret scan for tracked text files. Prints locations, not secret values.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = [
    ("telegram_bot_token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("api_secret_assignment", re.compile(r"(?i)\b(api_secret|secret_key|bybit_secret|binance_secret)\b\s*=\s*['\"][^'\"]{12,}['\"]")),
    ("api_key_assignment", re.compile(r"(?i)\b(api_key|bybit_key|binance_key)\b\s*=\s*['\"][A-Za-z0-9_-]{12,}['\"]")),
]

ALLOW_WORDS = {
    "example", "placeholder", "changeme", "your_", "os.getenv", "userdata.get",
    "getpass", "env", "dummy", "fake", "test_value", "not_displayed",
}

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".db", ".sqlite", ".joblib", ".pkl", ".pyc"}

def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line.strip()]

def is_allowed(line: str) -> bool:
    low = line.lower()
    return any(word in low for word in ALLOW_WORDS)

def main() -> int:
    findings = []
    for path in tracked_files():
        if not path.exists():
            # File may be tracked but deleted in the working tree before git add -A.
            # That is expected during cleanup; skip it.
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if is_allowed(line):
                continue
            for name, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append((path.relative_to(ROOT), idx, name))

    if findings:
        print("Potential secrets found. Values are intentionally hidden.")
        for rel, line, kind in findings:
            print(f"  {rel}:{line} — {kind}")
        return 1

    print("No obvious tracked-file secrets found.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
