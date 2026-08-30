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

The OTHER way ``json.dumps`` silently produces invalid JSON is a non-finite
float: it defaults to ``allow_nan=True`` and emits the bare tokens ``NaN`` /
``Infinity`` / ``-Infinity``, which ``json_valid()`` rejects. :func:`_dumps`
runs every payload through :func:`sanitize_nonfinite` first (non-finite float →
``null``) so the "never invalid JSON" guarantee holds for that case too — the
root cause of the BL-20260709 ``order_packages.signal_logic`` json_valid=0
population (a ``std_dev`` / z-score with a zero denominator).
"""
from __future__ import annotations

import json
import math
from typing import Any, Iterable

# Keys whose value we never trim or drop — consumers read these verbatim
# (e.g. closed_flat_invariant + trades_closed extract `closed_at`). Trimming
# them would defeat the whole point of preferring a valid, useful blob.
#
# ⚠️ EVERY PROVENANCE MARKER BELONGS HERE, AND `exit_reason_source` DID NOT
# (added 2026-08-26, BL-20260826-EXIT-REASON-SOURCE-TRUNCATED-BY-THE-NOTES-CAP).
# A provenance marker is a SENTINEL: a consumer compares it for equality against
# a fixed vocabulary, so a trimmed value is not a shorter answer — it is an
# unreadable third state that matches nothing. `_shrink_dict` picks the longest
# *unprotected* string, and `price_vs_pkg_bracket` (20 chars) is routinely the
# longest thing left on a `trades.notes` blob once `signal_logic` has been shed.
# Its two siblings `pnl_source` and `exit_price_source` were protected from the
# start; this one was added later (2026-08-23/25) and nobody extended the set.
#
# Measured on the live journal 2026-08-26 — population: the 25 `trades` rows
# whose notes carry a RESOLVED `exit_reason_source` — **2 (8.0%) stored
# `"price_vs_p…"`** (trades 4961 `bybit_1`/AVAXUSDT and 4978
# `bybit_portfolio`/BTCUSDT, both also carrying `_truncated: true`). That value
# equals neither `price_vs_pkg_bracket` nor `unresolved`, so a reader testing
# for the former counts the row as never-classified and one testing `is None`
# counts it as classified-but-unknown. Both readings are wrong, and the ABSENCE
# of this key is load-bearing evidence elsewhere (the 562/589 never-reached-the-
# classifier signature), so corrupting it corrupts that denominator too.
_DEFAULT_PROTECTED: tuple[str, ...] = (
    "closed_at", "closed_by", "closed_reason", "pnl_source",
    "exit_price_source", "exit_reason_source", "trade_id",
    # THIRD INSTANCE of the class the comment below describes, measured
    # 2026-08-30. `closed_by_operator` is the flag that says a close was an
    # OPERATIONAL flatten rather than a strategy exit, and `pre_mark_exit_reason`
    # is what makes that marking reversible. Both were stored unprotected, so on
    # a row already near the cap the shrink dropped them outright.
    #
    # NOT a hypothesis — an exact match. Of the 6 rows back-marked in one batch,
    # trades 5238 (`bybit_1`/BNBUSDT) and 5239 (`bybit_1`/BTCUSDT) came back
    # carrying `exit_reason='operator_flatten_reconciled'` (a COLUMN, so it
    # survived) with BOTH notes keys absent, and their surviving key set was
    # exactly `_DEFAULT_PROTECTED` + `_truncated` — the `_shrink_dict` minimal
    # envelope, reached once the trimmable strings were exhausted.
    #
    # `operator_close_reason` is deliberately NOT protected: it is long free
    # text, it is the right thing to shed first, and a trimmed reason is still
    # readable prose. The FLAG is what a consumer branches on, so the flag is
    # what must survive — the same distinction the sentinel note below draws.
    "closed_by_operator", "pre_mark_exit_reason",
)
_ELLIPSIS = "…"
# Hard stop on the trim loop so a pathological payload can never spin.
_MAX_TRIM_ITERS = 200


def sanitize_nonfinite(obj: Any) -> Any:
    """Recursively replace non-finite floats (``NaN`` / ``Infinity`` /
    ``-Infinity``) with ``None`` so the object serializes to STRICT, valid JSON.

    ``json.dumps`` defaults to ``allow_nan=True`` and emits the bare tokens
    ``NaN`` / ``Infinity`` / ``-Infinity`` for non-finite floats — which are
    **not** valid JSON: ``sqlite3 json_valid()`` returns 0 and a strict parser
    rejects them. A strategy meta dict routinely carries a non-finite float (a
    ``std_dev`` / ``deviation`` / z-score computed with a zero denominator), so
    persisting it verbatim wrote ``json_valid=0`` blobs into
    ``order_packages.signal_logic`` (the BL-20260709 legacy population: ~1036
    rows, dwarfing the 49 truncated ``trades.notes`` rows). This walk is the
    root-cause fix — the reason the char-slice migration to ``dump_capped``
    alone did NOT make every persisted blob valid. ``default=str`` in
    :func:`_dumps` handles non-float exotica (datetimes, Decimals); this handles
    the one thing ``default`` can't reach (a genuine ``float`` value).

    A no-op on all-finite data — the rebuilt structure serializes byte-for-byte
    identically, so the passthrough guarantee for valid payloads is preserved.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: sanitize_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_nonfinite(v) for v in obj]
    return obj


def _dumps(obj: Any, ensure_ascii: bool) -> str:
    return json.dumps(sanitize_nonfinite(obj), ensure_ascii=ensure_ascii, default=str)


def load_notes(notes_raw: Any) -> dict:
    """Best-effort decode of a ``trades.notes`` / ``signal_logic`` JSON blob.

    Returns ``{}`` for missing, malformed, or non-dict content — a caller that
    is about to ADD a key needs a dict to add it to, and a decode failure must
    not lose the write. It never raises.

    THE SYMMETRIC HALF OF :func:`dump_capped`, and it lives here for that
    reason. The decode existed only as a private ``_decode_notes`` inside
    ``order_monitor``, so the second site that needed it (the pairs close, M39
    track B) faced a choice between a third copy and importing the order path
    into a strategy module. Neither is acceptable: this repo's own record says
    a fourth copy is how the existing three drift apart, and the encode side
    was already centralised here precisely so notes handling has ONE home.
    ``order_monitor._decode_notes`` now delegates to this.
    """
    if not notes_raw:
        return {}
    try:
        loaded = json.loads(notes_raw)
    except Exception:  # noqa: BLE001
        return {}
    return loaded if isinstance(loaded, dict) else {}


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
