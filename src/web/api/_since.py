"""Shared parser for the ``?since=`` query parameter on journal read routes.

WHY THIS EXISTS (PI-20261005-MWOP8C4X-0002 / -0003). Both
``/api/bot/order-packages`` and ``/api/bot/trades/closed`` handed the raw
string to SQLite ``datetime(?)``. SQLite returns NULL for anything it cannot
parse, ``col >= NULL`` is never true, and the route answered ``200`` with zero
rows -- *"we could not parse your filter"* rendered identically to *"we looked
and nothing matches"*. The commonest trigger is an ISO offset typed in a URL:
``+00:00`` arrives as `` 00:00`` because ``+`` decodes to a space.

Contract: a value that parses is normalised to ``YYYY-MM-DD HH:MM:SS`` (UTC,
what SQLite ``datetime()`` compares against); a value that does not is a
**422** with ``filter_state: "unparsed"`` -- never an empty result.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import HTTPException

PARSED = "parsed"
NONE = "none"          # no ``since`` was supplied
UNPARSED = "unparsed"  # supplied but not understood (only ever seen in a 422)

# "...T00:00:00 00:00" is "...T00:00:00+00:00" after the URL decoded the plus.
_SPACE_OFFSET = re.compile(r"(?<=\d) (\d{2}:?\d{2})$")


def parse_since(raw: Optional[str]) -> Tuple[Optional[str], str]:
    """``(sqlite_datetime_or_None, filter_state)``; raises 422 when unparseable."""
    if raw is None or not str(raw).strip():
        return None, NONE
    text = str(raw).strip()
    text = _SPACE_OFFSET.sub(r"+\1", text)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_since",
                "filter_state": UNPARSED,
                "value": str(raw)[:64],
                "expected": "ISO-8601, e.g. 2026-10-05T00:00:00Z or "
                            "2026-10-05T00:00:00%2B00:00 (URL-encode '+' as %2B)",
            },
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), PARSED
