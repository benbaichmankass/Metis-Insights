"""The ONE definition of what identifies a prop position.

WHY THIS MODULE EXISTS
----------------------
Operator report, 2026-08-20: *"long after I close a prop trade, I'm still getting
monitoring things on Telegram … it's a recurring problem that we've come across
many times, and we keep on fixing the one sequence but not fixing the root
problem."*

The root problem is an **unenforced contract between two modules.**
``prop_report.ingest_report`` is the single chokepoint every report-back passes
through; it validated ``account_id`` and ``symbol`` and passed ``direction``
through **unvalidated**. ``prop_monitor_pulse._position_key`` keys a position on
all three. So a fill admitted without a direction is **permanently unclosable**:
no future close can land under its key, because a close that carries a direction
keys somewhere else. One module owned IDENTITY, another owned ADMISSION, and
nothing asserted they covered the same fields.

Reproduced from the live journal (population: all 32 prop fills):

    id  account     symbol   direction  status  reported_at
    32  breakout_1  SOLUSDT  long       closed  2026-08-19T21:31:42
    31  breakout_1  SOLUSDT  long       closed  2026-08-19T21:31:28
    30  breakout_1  SOLUSDT  **None**   open    2026-08-19T12:52:40

Row 30 keyed as ``akd:breakout_1|SOLUSDT|``; rows 31/32 keyed as
``…|long``. Different keys — so the newest fill under row 30's key was still
``open``, and the hourly pulse reported a phantom 83-SOL position indefinitely.

WHY THE PREVIOUS FIXES DID NOT HOLD
-----------------------------------
This is the **third** attempt at this class, and the first two each closed an
instance:

* keying on ``ticket_id`` was abandoned when an open fill (no ticket) and its
  close (matched to a ticket) split into two keys;
* ``BL-20260708-PROP-PULSE-DIRECTION-ALIAS`` hardened the **normalizer**
  (``buy``→``long``) and left **admission** open.

Both fixed the sequence in front of them. Neither made the two modules agree,
so the next gap in the identity contract reopened the same symptom. The fix is
therefore not another normalizer or another filter: it is to give the identity
contract **one owner**, have admission validate against it, and lock the two
together with a test that fails if they drift.

⚠️ **THE ALIAS MAP IS LOAD-BEARING ON LIVE DATA RIGHT NOW.** Measured
2026-08-20 over all 32 live fills: ``long`` 23, ``short`` 8, and **``buy`` 1** —
a real row that only shares a key with its siblings because of the mapping
below. Do not "simplify" it away.
"""
from __future__ import annotations

from typing import Any, Mapping

#: Broker vocabulary -> order-package vocabulary.
_DIRECTION_ALIASES = {"buy": "long", "sell": "short"}

#: The fields that TOGETHER identify a prop position. Admission validates
#: exactly these for a position-bearing fill; the key is built from exactly
#: these. Adding a field here without teaching admission about it is what
#: `tests/test_prop_position_identity.py` refuses.
IDENTITY_FIELDS: tuple[str, ...] = ("account_id", "symbol", "direction")

#: Statuses that create, hold, or close a POSITION, and therefore need a full
#: identity. `skipped` is deliberately excluded: it records a ticket the
#: operator did not place, so it never becomes a position and requiring a
#: direction for it would reject a legitimate report. Narrower than
#: `prop_report._FILL_STATUSES` on purpose.
POSITION_BEARING_STATUSES: frozenset[str] = frozenset(
    {"placed", "open", "filled", "closed"}
)


#: Every token that NAMES a direction — the canonical pair plus every alias.
#: Derived from `_DIRECTION_ALIASES` rather than typed out again, so a new
#: alias becomes typeable in the Telegram grammar automatically and the parser
#: can never recognise a word this module does not, or miss one it does.
DIRECTION_WORDS: frozenset[str] = frozenset({"long", "short"}) | frozenset(
    _DIRECTION_ALIASES
)


def canonical_direction(direction: Any) -> str:
    """Map any long/short vocabulary to canonical ``long``/``short``.

    An already-canonical or unrecognised value passes through lowercased, so
    nothing is silently dropped — an unknown direction must remain visible as
    itself rather than being coerced into one of the two real sides.
    """
    d = str(direction or "").strip().lower()
    return _DIRECTION_ALIASES.get(d, d)


def is_direction_word(token: Any) -> bool:
    """Is this token a direction, as opposed to a reason word?

    A MEMBERSHIP test, deliberately separate from :func:`canonical_direction`,
    which passes unknown values through unchanged — so `canonical_direction("tp")`
    returns `"tp"` and cannot answer this question. The Telegram parser needs to
    decide whether a bare word IS a direction before it consumes it out of the
    reason tail; asking the canonicaliser would classify every reason word as a
    direction.
    """
    return str(token or "").strip().lower() in DIRECTION_WORDS


def position_key(fill: Mapping[str, Any]) -> str:
    """Stable identity for a prop position across its fill rows.

    Keyed on (account_id, symbol, canonical direction) — never on ``ticket_id``
    (see the module docstring for why that was abandoned).
    """
    return (
        f"akd:{fill.get('account_id') or ''}|"
        f"{str(fill.get('symbol') or '').upper()}|"
        f"{canonical_direction(fill.get('direction'))}"
    )


def missing_identity_fields(fill: Mapping[str, Any]) -> list[str]:
    """Which IDENTITY_FIELDS this fill cannot supply. ``[]`` when complete.

    Blank and whitespace-only count as missing: a key built from ``" "`` is
    just as unmatchable as one built from ``None``, and the whole defect is a
    key nothing else can ever produce.
    """
    out = []
    for field in IDENTITY_FIELDS:
        raw = fill.get(field)
        value = canonical_direction(raw) if field == "direction" else str(raw or "").strip()
        if not value:
            out.append(field)
    return out
