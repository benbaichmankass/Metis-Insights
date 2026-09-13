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
    remaining = budget - len(head) - len(marker)
    if remaining <= 0:
        # The certification alone fills the budget. That is the right thing to
        # keep: rows without their denominator are what this module exists to
        # stop shipping.
        return head + marker
    body = json.dumps(bulk, indent=2, default=str)[:remaining]
    return head + "\n" + body + marker


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
