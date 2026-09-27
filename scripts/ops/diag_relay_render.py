#!/usr/bin/env python3
"""Render one diag-relay section so TRUNCATION CANNOT REMOVE THE FIELDS THAT
CERTIFY THE RESPONSE.

WHY THIS EXISTS
---------------
``vm-diag-snapshot`` pretty-prints each path's JSON and cuts it to a per-path
byte budget with ``pretty[:budget]`` — a HEAD truncation. The db-explorer
envelope orders its keys ``{table, db, columns, rows, total, limit, offset,
filter_state, order_state, ...}`` and ``/api/bot/order-packages`` puts
``count`` after ``rows``. So on any response over budget the certification
fields are the FIRST casualties while ``rows`` — the part that LOOKS like
data — survives intact.

That is silent in exactly the dangerous direction: a reader sees rows, sees no
``filter_state``, and rows look like data. You cannot tell a filtered read from
an unfiltered one, or a complete population from a page.

MEASURED, both 2026-08-16
(``BL-20260816-TRUNCATION-STRIPS-THE-FIELDS-THAT-CERTIFY-A-RESPONSE``):

* #9753 — ``filter_col=strategy`` on ``order_packages``. The real column is
  ``strategy_name``, so the filter was silently ignored, the query ran
  UNFILTERED, and 829,977 bytes were cut to 55,000. ``filter_state`` would
  have read ``ignored_unknown_column`` and was truncated away. What survived
  was well-formed rows of a DIFFERENT strategy. It was caught only because
  XRP does not trade at 78,810; on a BTC leg the reader would have computed a
  distribution over the entire table and reported it as one leg's history.
* #9755 — correct filter, 321,146 bytes cut to 55,000. Right population, but
  ``count`` was truncated away: 7 visible rows and NO denominator. The true
  total was 37.

``BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN`` added
``filter_state`` as the remedy for the silent ignore. This is that remedy
DEFEATED BY TRANSPORT — the field exists, is correct, and never reaches the
reader. **A guard that is truncated away is not a guard.**

THE RULE
--------
Scalars survive; containers get cut. The certification fields are scalars
(``total``, ``count``, ``filter_state``, ``read_state``, ``limit`` …) and the
bulk is containers (``rows``, ``columns``, ``accounts`` …).

⚠️ **NO HARDCODED KEY LIST**, deliberately. An allow-list of "important" keys
would have to be updated for every new envelope and would silently omit the
one that mattered — the same "explicit list reproduces the bug" argument
``exit_mechanism_coverage.py`` makes for scanning its impl dirs wholesale.
Partitioning on TYPE covers envelopes nobody has written yet.

⚠️ **The truncation still happens.** This does not make a large response fit;
it changes WHAT SURVIVES it, and says so in the marker.

A LIST IS CUT AT WHOLE ELEMENTS, AND THE MARKER SAYS WHICH ONES SURVIVED
------------------------------------------------------------------------
Until 2026-09-24 the bulk was cut with ``json.dumps(bulk)[:remaining]`` — a
character slice that kept the FIRST elements of every list and ended mid-row,
while the marker said only that the container was "cut". On a TAIL endpoint
(``log_file``, ``journalctl``, ``audit`` — all oldest → newest) the elements
that survive a head cut are the OLDEST, so the last row a reader can see is
not the latest row, and nothing said so.

MEASURED (E50, ``PI-20260924-JN54P2HH-0004``): issue #12820 read
``log_file?name=arbitration_fanout_soak`` (the default 100-line tail; its
``limit=40`` is not a parameter the endpoint takes). 149,082 bytes were cut to
fit, 35 of the 100 rows survived, and the 35th — ``2026-09-23T01:52:51Z`` —
was reported as the newest row. It was recorded as *"the soak has written
nothing for 32 hours while armed"*, filed high-severity, and blocked E17. A
direct read of the same file shows **65 rows** written inside that "silent"
window. The soak never stopped; the transport hid its newest 65 rows.

So a list is now cut at a whole-element boundary (what is shown is valid JSON,
never a half row), and the marker states, per container, **how many elements
of how many survived and that the final element is not among them** — the one
fact the #12820 reader needed and could not see.
"""
from __future__ import annotations

import json
from typing import Any, Tuple

#: Below this, splitting the envelope costs more than it buys.
_MIN_BUDGET = 200


def split_envelope(data: Any) -> Tuple[dict, dict]:
    """Partition a JSON envelope into (certifying scalars, bulk containers).

    A scalar (or ``None``) certifies; a list/dict is bulk. Non-dict payloads
    have no envelope at all and come back ``({}, {})`` — *we could not
    partition it* is a different fact from *it had no certifying fields*, and
    the caller branches on the dict being empty.
    """
    if not isinstance(data, dict):
        return {}, {}
    certifying = {k: v for k, v in data.items()
                  if not isinstance(v, (list, dict))}
    bulk = {k: v for k, v in data.items() if isinstance(v, (list, dict))}
    return certifying, bulk


def render_section(payload: str, budget: int) -> str:
    """Render one path's payload within *budget* bytes, certification first.

    Returns the body to place inside the fenced block, plus its own truncation
    marker where one applies. The marker NAMES what was dropped rather than
    saying only that something was.
    """
    try:
        data = json.loads(payload)
    except Exception:
        # Not JSON (a curl-level error string, say). Nothing to certify; the
        # old behaviour is the honest one.
        if len(payload) > budget:
            return (payload[:budget]
                    + f"\n_(truncated — {len(payload) - budget} of "
                      f"{len(payload)} bytes not shown; not JSON, so no "
                      f"certification fields could be preserved)_")
        return payload

    pretty = json.dumps(data, indent=2, default=str)
    if len(pretty) <= budget:
        return pretty

    certifying, bulk = split_envelope(data)
    if not certifying or budget < _MIN_BUDGET:
        # No envelope to hoist (a bare list, or a budget too small to split).
        # Say which, so a reader is not left guessing why nothing was hoisted.
        why = ("the payload is not a keyed envelope"
               if not certifying else "the per-path budget is too small to split")
        return (pretty[:budget]
                + f"\n_(truncated — {len(pretty) - budget} of {len(pretty)} "
                  f"bytes not shown, and NO certification fields were hoisted "
                  f"because {why}. Treat every number below as UNCERTIFIED.)_")

    head = json.dumps(certifying, indent=2, default=str)
    marker = (
        f"\n_(**TRUNCATED** — the {len(bulk)} container field(s) "
        f"{sorted(bulk)} were cut to fit this path's share of the comment "
        f"budget; {len(pretty)} bytes total. The scalar/certification fields "
        f"above are COMPLETE and were hoisted ahead of the data so truncation "
        f"cannot remove them — read `filter_state`/`total`/`count` there "
        f"before trusting any row below. Re-request this path alone for the "
        f"full result.)_"
    )
    # Room for the per-container accounting, which is written after the body
    # is fitted (its text depends on how much survived).
    remaining = budget - len(head) - len(marker) - _ACCOUNTING_RESERVE * len(bulk)
    if remaining <= 0:
        # The certification alone fills the budget. That is the right thing to
        # keep: rows without their denominator are what this module exists to
        # stop shipping.
        return head + marker + _accounting(
            [(k, v, 0) for k, v in bulk.items()])
    body, shown = fit_bulk(bulk, remaining)
    return head + "\n" + body + marker + _accounting(shown)


#: Bytes held back per container for its accounting line.
_ACCOUNTING_RESERVE = 400


def fit_bulk(bulk: dict, budget: int) -> Tuple[str, list]:
    """Fit *bulk* into *budget* bytes, cutting LISTS AT WHOLE ELEMENTS.

    Returns ``(body, shown)`` where ``shown`` is ``[(key, value, n_shown)]`` in
    the envelope's own key order. A list keeps its first ``n_shown`` elements
    (the envelope's order is preserved, never re-sorted, because the renderer
    cannot know whether a list is oldest-first or newest-first — it SAYS what
    it kept instead of guessing). A dict that does not fit whole is shown as a
    character-cut fragment after the valid JSON, and counted as 0 whole
    entries, which is what it is.
    """
    kept: dict = {}
    shown: list = []
    fragments: list = []

    def size(d: dict) -> int:
        return len(json.dumps(d, indent=2, default=str))

    for key, value in bulk.items():
        if size({**kept, key: value}) <= budget:
            kept[key] = value
            shown.append((key, value, len(value)))
            continue
        if isinstance(value, list):
            lo, hi = 0, len(value)
            while lo < hi:  # largest k whose whole-element prefix fits
                mid = (lo + hi + 1) // 2
                if size({**kept, key: value[:mid]}) <= budget:
                    lo = mid
                else:
                    hi = mid - 1
            kept[key] = value[:lo]
            shown.append((key, value, lo))
        else:
            room = budget - size(kept) - len(key) - 8
            if room > 0:
                fragments.append(
                    f'"{key}" (FRAGMENT, cut mid-object): '
                    + json.dumps(value, indent=2, default=str)[:room])
            shown.append((key, value, 0))
    body = json.dumps(kept, indent=2, default=str)
    if fragments:
        body += "\n" + "\n".join(fragments)
    return body, shown


def _accounting(shown: list) -> str:
    """One line per container: how many elements of how many are shown.

    ⚠️ This is the line the #12820 reader needed. It says in words that the
    FINAL element is not shown, because on a tail endpoint the final element
    is the newest one — and "the last row I can see" reads as "the latest
    row" unless something says otherwise.
    """
    lines = []
    for key, value, n in shown:
        total = len(value) if isinstance(value, (list, dict)) else 0
        kind = "element(s)" if isinstance(value, list) else "key(s)"
        if n >= total:
            lines.append(f"`{key}`: all {total} {kind} shown.")
            continue
        cut = total - n
        lines.append(
            f"`{key}`: ONLY THE FIRST {n} of {total} {kind} are shown; the "
            f"LAST {cut} — including the final one — are NOT. If this list "
            f"is chronological oldest→newest (log_file, journalctl and audit "
            f"tails are), the NEWEST {cut} entries are the ones cut: the last "
            f"entry visible here is NOT the latest. Re-request with a smaller "
            f"`lines`/`limit` to see the tail.")
    return "\n_(" + " ".join(lines) + ")_" if lines else ""


def _self_test() -> int:
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        ok = ok and bool(cond)

    # The #9753 shape: certification AFTER rows, response over budget.
    env = {"table": "order_packages", "db": "trade_journal",
           "columns": ["a", "b"],
           "rows": [{"strategy_name": "vwap", "n": i} for i in range(400)],
           "total": 37, "limit": 1000, "offset": 0,
           "filter_state": "ignored_unknown_column",
           "order_state": "applied"}
    payload = json.dumps(env)
    full = json.dumps(env, indent=2)
    budget = 1200
    check("the fixture is actually over budget (else this proves nothing)",
          len(full) > budget)
    out = render_section(payload, budget)
    check("filter_state SURVIVES truncation", "ignored_unknown_column" in out)
    check("the denominator SURVIVES truncation", '"total": 37' in out)
    check("the marker NAMES the dropped containers", "'rows'" in out or '"rows"' in out)
    check("it still fits the budget (plus the marker it owes the reader)",
          len(out) <= budget + 700)

    # NEGATIVE CONTROL — the old code path would have failed the two above.
    old = full[:budget]
    check("control: the OLD head-truncation drops filter_state",
          "ignored_unknown_column" not in old)
    check("control: the OLD head-truncation drops the total",
          '"total": 37' not in old)

    # THE #12820 SHAPE (E50): a TAIL endpoint, chronological oldest→newest,
    # over budget. The reader must be told that the newest rows were cut.
    log = {"name": "arbitration_fanout_soak", "present": True,
           "size_bytes": 1413493,
           "lines": [json.dumps({"logged_at_utc": f"2026-09-23T{h:02d}:00:00",
                                 "pad": "x" * 300}) for h in range(24)]}
    lbudget = 4000
    check("control: the tail fixture is over budget",
          len(json.dumps(log, indent=2)) > lbudget)
    out5 = render_section(json.dumps(log), lbudget)
    newest = "2026-09-23T23:00:00"
    check("the newest row is genuinely cut (else the next check proves nothing)",
          newest not in out5)
    check("the marker SAYS the final element is not shown",
          "including the final one — are NOT" in out5
          and "of 24 element(s)" in out5)
    check("rows are cut at whole elements — every shown row is complete",
          '"lines": [' in out5 and out5.count('logged_at_utc') ==
          int(out5.split("ONLY THE FIRST ")[1].split(" ")[0]))
    check("it still fits the budget (plus the accounting it owes the reader)",
          len(out5) <= lbudget + 1200)
    # NEGATIVE CONTROL — the pre-E50 renderer cut `json.dumps(bulk)[:n]`,
    # which says nothing about how many rows survived or which end was lost.
    old5 = json.dumps({"lines": log["lines"]}, indent=2)[:lbudget]
    check("control: the OLD bulk cut carries no element count",
          "of 24" not in old5 and "final" not in old5)

    # Everything fits after hoisting: accounting says so, nothing is claimed cut.
    shown_all = fit_bulk({"rows": [1, 2, 3]}, 10_000)[1]
    check("fit_bulk keeps a list whole when it fits",
          shown_all == [("rows", [1, 2, 3], 3)])

    # Under budget: untouched, no marker.
    small = json.dumps({"total": 1, "rows": [{"a": 1}]})
    out2 = render_section(small, 10_000)
    check("an under-budget payload is returned whole, with no marker",
          "TRUNCATED" not in out2 and '"total": 1' in out2)

    # A bare list has no envelope — say so rather than implying certification.
    out3 = render_section(json.dumps([{"a": i} for i in range(500)]), 300)
    check("a bare list says NO certification fields were hoisted",
          "NO certification fields" in out3)

    # Non-JSON is passed through and marked honestly.
    out4 = render_section("curl: (28) Operation timed out" * 50, 100)
    check("non-JSON is marked as uncertifiable, not silently cut",
          "not JSON" in out4)

    # split_envelope's own contract.
    c, b = split_envelope({"n": 1, "s": "x", "z": None, "rows": [], "m": {}})
    check("scalars and None certify; containers are bulk",
          set(c) == {"n", "s", "z"} and set(b) == {"rows", "m"})
    check("a non-dict payload partitions to empty, not to a guess",
          split_envelope([1, 2, 3]) == ({}, {}))

    print("self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    print(__doc__)
