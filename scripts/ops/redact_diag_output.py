#!/usr/bin/env python3
"""Redact secret-shaped strings from relay output BEFORE it is posted.

FIX-SA-07 (audit 2026-09-29). ``vm-diag-snapshot`` and ``trainer-vm-diag``
post VM output verbatim into issue comments, run logs and artifacts of a
PUBLIC repo. The only redaction that existed was the Telegram filter on the
trader's own log handlers; free-text diag paths (journalctl, log_file, and any
trainer shell command) had none.

Rules (each is a named class so the counts say WHAT matched, never the value):
  telegram_url / telegram_token  -- the RedactingFilter regexes, imported
  aws_access_key                 -- AKIA/ASIA + 16
  github_token                   -- ghp_/gho_/ghu_/ghs_/ghr_ and github_pat_
  pem_block                      -- BEGIN..END, or BEGIN..end-of-text if the
                                    output was cut before END
  bearer                         -- ``Bearer <20+ chars>``
  long_hex                       -- >=64 contiguous hex chars (sha256-length
                                    and up: token_hex(32) secrets). 40-char git
                                    shas are deliberately NOT matched — they
                                    fill diag version output and are not secret.

The script NEVER prints a matched value: stdout is ``rule=count`` only.
Usage: ``redact_diag_output.py <file> [<file> ...]`` (in place). Exit 0 always
unless a file is unreadable, so a redaction pass cannot itself drop a result.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.utils.log_redact import _BARE_TOKEN_RE, _TELEGRAM_URL_RE  # noqa: E402

RULES = (
    ("pem_block", re.compile(
        r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE|PGP [A-Z ]*BLOCK)-----"
        r".*?(?:-----END [A-Z0-9 ]*-----|\Z)", re.DOTALL)),
    ("telegram_url", _TELEGRAM_URL_RE),
    ("telegram_token", _BARE_TOKEN_RE),
    ("aws_access_key", re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("long_hex", re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64,}(?![0-9A-Fa-f])")),
)


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for name, rx in RULES:
        text, n = rx.subn(f"[REDACTED:{name}]", text)
        if n:
            counts[name] = n
    return text, counts


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: redact_diag_output.py <file> [<file> ...]", file=sys.stderr)
        return 2
    rc = 0
    for path in argv[1:]:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError as e:
            print(f"{path}: unreadable ({type(e).__name__})", file=sys.stderr)
            rc = 1
            continue
        clean, counts = redact(raw)
        if clean != raw:
            with open(path, "w", encoding="utf-8") as f:
                f.write(clean)
        summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
        print(f"{path}: redacted {summary}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
