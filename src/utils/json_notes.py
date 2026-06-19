"""Length-bounded JSON encoding that NEVER emits invalid JSON.

The footgun this replaces is the ``json.dumps(payload)[:N]`` pattern —
serialize to JSON, then slice the resulting STRING by character count. The
slice cuts mid-token the moment the payload exceeds ``N`` (a dangling key,
an unterminated string, a missing brace), persisting **invalid JSON**.
Downstream ``json_extract`` / ``json.loads`` then choke on it.

Concrete incident (BL-20260619): a truncated ``trades.notes`` blob made
``closed_flat_invariant``'s ``json_extract(notes, '$.closed_at')`` raise
"malformed JSON", which aborts the whole query and silently disabled that
safety invariant on every tick. The same truncation also corrupts long
``signal_logic`` blobs on order packages.

``dump_capped(obj, max_len)`` is the drop-in replacement: it trims the
*values* (longest unprotected string first), guarantees the result both
parses as JSON and is ``<= max_len`` characters, and marks any lossy result
with ``"_truncated": true`` so a reader can tell. Keys the consumers depend on
(``closed_at`` et al.) are never trimmed or dropped while anything else can
still be shed.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

# Keys whose value we never trim or drop — consumers read these verbatim
# (e.g. closed_flat_invariant + trades_closed extract `closed_at`). Trimming
# them would defeat the whole point of preferring a valid, useful blob.
_DEFAULT_PROTECTED: tuple[str, ...] = (
    "closed_at", "closed_by", "closed_reason", "pnl_source",
    "exit_price_source", "trade_id",
)
_ELLIPSIS = "…"
# Hard stop on the trim loop so a pathological payload can never spin.
_MAX_TRIM_ITERS = 200


def _dumps(obj: Any, ensure_ascii: bool) -> str:
    return json.dumps(obj, ensure_ascii=ensure_ascii, default=str)


def dump_capped(
    obj: Any,
    max_len: int,
    *,
    ensure_ascii: bool = False,
    protected: Iterable[str] = _DEFAULT_PROTECTED,
) -> str:
    """JSON-encode *obj* so the result is valid JSON AND ``<= max_len`` chars.

    Unlike ``json.dumps(obj)[:max_len]``, this never returns a half-token: it
    shrinks the longest unprotected string value repeatedly, then (if still
    over budget) falls back to a minimal valid envelope that preserves the
    *protected* keys. ``max_len`` counts characters (matching the old slice).
    """
    s = _dumps(obj, ensure_ascii)
    if len(s) <= max_len:
        return s
    if isinstance(obj, dict):
        return _shrink_dict(obj, max_len, ensure_ascii, set(protected))
    # Non-dict payload over budget: wrap a trimmed repr in a valid envelope.
    return _minimal_repr(str(obj), max_len, ensure_ascii)


def _shrink_dict(
    obj: dict, max_len: int, ensure_ascii: bool, protected: set[str],
) -> str:
    work = dict(obj)
    work["_truncated"] = True
    for _ in range(_MAX_TRIM_ITERS):
        s = _dumps(work, ensure_ascii)
        if len(s) <= max_len:
            return s
        # Pick the longest trimmable (unprotected, non-empty) string value.
        key = None
        longest = 0
        for k, v in work.items():
            if k == "_truncated" or k in protected:
                continue
            if isinstance(v, str) and len(v) > longest:
                key, longest = k, len(v)
        if key is None or longest == 0:
            break  # nothing left to trim
        cur = work[key]
        # Halve (shedding at least 8 chars) and mark the cut with an ellipsis.
        new_len = max(0, min(len(cur) - 8, len(cur) // 2))
        work[key] = (cur[:new_len] + _ELLIPSIS) if new_len > 0 else ""
    # Strings exhausted but still over budget (protected keys / non-string
    # bloat dominate). Fall back to a minimal valid envelope keeping only the
    # protected keys present on the original object.
    minimal: dict[str, Any] = {k: obj[k] for k in obj if k in protected}
    minimal["_truncated"] = True
    s = _dumps(minimal, ensure_ascii)
    if len(s) <= max_len:
        return s
    # Even the protected set overflows — emit the barest valid marker.
    return _dumps({"_truncated": True}, ensure_ascii)


def _minimal_repr(text: str, max_len: int, ensure_ascii: bool) -> str:
    env = {"_truncated": True, "_repr": ""}
    overhead = len(_dumps(env, ensure_ascii))
    budget = max(0, max_len - overhead)
    env["_repr"] = text[:budget]
    s = _dumps(env, ensure_ascii)
    if len(s) <= max_len:
        return s
    return _dumps({"_truncated": True}, ensure_ascii)
