#!/usr/bin/env python3
"""Path-traversal guard for the diag relay (FIX-SA-07, audit 2026-09-29).

``vm-diag-snapshot`` validates a requested path against the charset
``^[A-Za-z0-9/?&=_.:%-]+$`` and, for ``api/bot/*``, an allowlist. Both let a
``..`` segment through: ``.`` and ``/`` are in the charset, and the diag
default prefix has no allowlist at all, so ``../bot/x`` or ``%2e%2e/x`` is
interpolated into ``http://127.0.0.1:8001/api/diag/<path>`` and the VM-side
HTTP client normalises it out of the ``/api/diag/`` prefix.

The rule here is deliberately about the DECODED form, because ``%`` is
legitimately allowed (ISO-8601 ``:`` arrives as ``%3A``): the path is
percent-decoded to a fixpoint and then checked segment-wise. Encoded dots and
encoded separators are refused OUTRIGHT, not decoded-and-accepted — nothing
the relay legitimately serves needs them, and a refusal costs one re-request.

Usage (workflow):  ``python3 scripts/ops/diag_path_guard.py "<path>"``
exit 0 = allowed, exit 1 = refused (reason on stderr).
Import:            ``from scripts.ops.diag_path_guard import traversal_reason``
"""
from __future__ import annotations

import re
import sys
from urllib.parse import unquote

_ENC_DOT = re.compile(r"%(?:25)*2e", re.IGNORECASE)  # %2e, %252e, %25252e, ...
_ENC_SEP = re.compile(r"%(?:25)*(?:2f|5c)", re.IGNORECASE)  # encoded / and \
_SEGMENT_SPLIT = re.compile(r"[/\\?&=;]")


def traversal_reason(path: str) -> str | None:
    """Return why *path* is refused, or None if it is allowed."""
    if _ENC_DOT.search(path):
        return "encoded dot (%2e) in path"
    if _ENC_SEP.search(path):
        return "encoded path separator (%2f / %5c) in path"
    decoded = path
    for _ in range(5):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
    else:
        return "path is percent-encoded more than 5 levels deep"
    if "\\" in decoded:
        return "backslash in path"
    for seg in _SEGMENT_SPLIT.split(decoded):
        if seg.strip() == "..":
            return "'..' path segment"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: diag_path_guard.py <path>", file=sys.stderr)
        return 2
    reason = traversal_reason(argv[1])
    if reason:
        print(reason, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
