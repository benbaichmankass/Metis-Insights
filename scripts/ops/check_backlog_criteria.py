#!/usr/bin/env python3
"""A NEW backlog row must be WORKABLE: what done looks like, how much it
matters, and who may fix it.

WHY THIS EXISTS. Two high-severity rows were found finished-but-open on
2026-08-12, and both had the same cause — no ``resolution_criteria``:

* ``BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE`` — the fork had been closed by convergence
  (the 15 levers ported into the canonical harness, the other copy reduced to a
  shim) and the row sat ``open``/``high`` for four days after the fix landed.
* ``BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS`` — 29 of 33 suspect rows had
  already been marked, i.e. the substance was done, and the row still read as
  live work.

A row nobody can *tell* is finished never gets closed. It then accumulates with
its peers until the backlog reads as noise, which this repo already names as a
P1 in its own right (the desensitized-alarm rule): an alarm routinely walked
past is worse than no alarm, because it trains everyone to walk past the real
ones too. The cost is not tidiness — it is that a genuine finding filed into a
noisy backlog is indistinguishable from the stale rows around it.

SCOPE — deliberately diff-scoped, and that is not timidity. Applied to the whole
tree this fails on the large pre-existing population immediately, and a guard
that fails everywhere on day one gets switched off or routed around, which is
strictly worse than no guard. Grandfathering the past and holding the FUTURE to
the rule is what makes it survivable. ``--all`` is the standing advisory census
so the debt stays visible rather than forgotten.

NOT CHEAP TO LIE TO. The direct lesson from ``new-table-wiring-guard``, whose
presence-only marker made the cheapest way to silence a real finding *naming a
table that does not exist*: a guard easier to fool than to satisfy is worse than
no guard. So a placeholder (``TBD``, ``n/a``, ``see above``, ``unknown``) is
rejected, and criteria must be long enough to name an observable condition. It
is still trivially possible to write a *bad* criterion — this guard cannot judge
quality, and does not pretend to. It only refuses the empty and the obviously
vacuous, which is exactly the failure that produced the two incidents above.

EXTENDED 2026-08-13 (operator-directed) from one field to three, after the
backlog was measured rather than described. Over 269 open rows: **38% had no
``resolution_criteria``, 44% no ``severity``, 24% no ``tier``** — and the
backlog grew **+129 net in 30 days** while **25% of it sat >=45 days old having
never once been advanced by evidence**. Those are not three tidiness problems,
they are three different ways for a row to be un-workable:

* no criteria  -> the row can only be RE-READ, never closed
* no severity  -> it cannot be sorted, so it is picked by recency, not importance
* no tier      -> nothing says whether a session may fix it or must ask, so it
  is safest to do neither, and neither is what happens

The corroborating measurement: **zero** Tier-1 high/critical rows were older
than 14 days. When a row says it matters AND says a session may act, it gets
fixed. The rot is entirely in rows that say neither.

``snoozed_until`` is validated but never required — it is the DEFER path
(set on 2 of 269 rows at the time of writing, i.e. effectively unused), and a
row genuinely blocked on accrual belongs behind a date rather than in front of
every review pass forever.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Iterable

# `scripts/ops` is on sys.path when this file is RUN directly, but not when it
# is imported by a harness from elsewhere. Add it explicitly so the guard cannot
# fail on an ImportError that depends on how it was invoked.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _backlog import UnsupportedCriteriaShape, criteria_text  # noqa: E402

BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
)

#: Values that are present but say nothing. Compared case-folded and stripped.
_PLACEHOLDERS = {
    "", "tbd", "tba", "n/a", "na", "none", "null", "-", "--", "?", "???",
    "see above", "see below", "unknown", "todo", "to do", "pending", "wip",
}

#: A criterion has to name an observable condition. Anything shorter than this
#: cannot ("fixed", "works", "green" are not criteria). Chosen to be permissive
#: — the point is to catch the empty and the vacuous, not to police prose.
_MIN_LEN = 40


def _load(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return d["items"] if isinstance(d, dict) and "items" in d else (d if isinstance(d, list) else [])


def _load_at_ref(ref: str, rel: str) -> list[dict[str, Any]]:
    """The file's rows as of *ref*. A file absent at the base is simply new."""
    try:
        out = subprocess.run(
            ["git", "show", f"{ref}:{rel}"],
            capture_output=True, text=True, check=False,
        )
        if out.returncode != 0:
            return []
        d = json.loads(out.stdout)
    except (OSError, json.JSONDecodeError):
        return []
    return d["items"] if isinstance(d, dict) and "items" in d else (d if isinstance(d, list) else [])


#: The four severities a new row may declare. Deliberately NOT accepting the
#: historical variants (`P1`/`P2`/`P3`, `low-medium`, `medium-high`,
#: `needs-triage`): five spellings of "medium" is why 44% of the open backlog
#: could not be sorted at all. Old rows are grandfathered; new ones normalise.
_SEVERITIES = {"critical", "high", "medium", "low"}

#: Tier must resolve to 1/2/3. A trailing annotation is allowed and useful
#: ("1 (research; promotion past candidate is Tier-3)") — what is refused is a
#: value from which no tier can be read at all, because that is the field that
#: decides whether a session may fix the row itself or must ask the operator.
_TIER_RE = re.compile(r"^\s*(?:tier[\s\-_]*)?([123])\b", re.I)


def _verdict(row: dict[str, Any]) -> str | None:
    """Return a human reason this row fails, or None when it passes.

    THREE FIELDS, one question each, and a row missing any of them is
    structurally un-workable rather than merely untidy:

      resolution_criteria  what does DONE look like?   -> without it the row can
                           only ever be RE-READ, never closed
      severity             how much does this matter?  -> without it the row
                           cannot be sorted, so it is picked by recency
      tier                 who is allowed to fix it?   -> without it the row
                           cannot be routed autonomous-vs-operator

    Measured 2026-08-13 over the 269 open rows: 38% had no criteria, 44% no
    severity, 24% no tier. The consequence is arithmetic, not aesthetic — the
    backlog grew +129 net over 30 days while 25% of it sat >=45 days old having
    never once been advanced. `severity`/`tier` were added to this guard that
    day (it already held the criteria line since 2026-08-12).
    """
    try:
        text = criteria_text(row).strip()
    except UnsupportedCriteriaShape as exc:
        # A dict / an int / a nested list stringifies happily to something that
        # clears the length floor, so the OLD `str(raw)` accepted it. That was a
        # pass by accident, not by design — reject the shape explicitly.
        return str(exc)
    if not text:
        if row.get("resolution_criteria") is None:
            return "no resolution_criteria field"
        return "resolution_criteria is empty"
    if text.casefold() in _PLACEHOLDERS:
        return f"resolution_criteria is a placeholder ({text!r})"
    if len(text) < _MIN_LEN:
        return (
            f"resolution_criteria is {len(text)} chars, under the {_MIN_LEN}-char "
            f"floor — too short to name an observable condition ({text!r})"
        )

    sev = row.get("severity")
    if sev is None or str(sev).strip().casefold() in _PLACEHOLDERS:
        return "no severity — the row cannot be sorted, so it gets picked by recency"
    if str(sev).strip().casefold() not in _SEVERITIES:
        return (
            f"severity {str(sev)!r} is not one of {sorted(_SEVERITIES)} — five "
            f"spellings of 'medium' is why 44% of the open backlog could not be "
            f"sorted at all"
        )

    tier = row.get("tier")
    if tier is None or str(tier).strip().casefold() in _PLACEHOLDERS:
        return (
            "no tier — nothing can tell whether a session may fix this itself or "
            "must ask the operator, so it is safest to do neither (and nobody does)"
        )
    if not _TIER_RE.match(str(tier)):
        return (
            f"tier {str(tier)[:60]!r} does not begin with 1, 2 or 3 — a trailing "
            f"annotation is fine, an unreadable tier is not"
        )

    # `snoozed_until` is OPTIONAL — but when set it must be a real date, because
    # its whole job is to drop the row out of review passes until then. An
    # unparseable value would silently either hide the row forever or not at all,
    # and the reader could not tell which.
    snooze = row.get("snoozed_until")
    if snooze is not None and str(snooze).strip():
        if not re.match(r"^\d{4}-\d{2}-\d{2}", str(snooze).strip()):
            return (
                f"snoozed_until {str(snooze)!r} is not an ISO date — a snooze that "
                f"cannot be parsed hides the row forever or not at all, and nothing "
                f"reveals which"
            )
    return None


def _report(bad: list[tuple[str, str, str]], *, advisory: bool) -> int:
    if not bad:
        return 0
    lead = "::warning::" if advisory else "::error::"
    print(
        f"{lead}backlog row(s) that are not WORKABLE — missing or unusable "
        f"resolution_criteria / severity / tier. A row nobody can tell is FINISHED "
        f"never gets closed; a row with no severity cannot be sorted; a row with no "
        f"tier cannot be routed. Measured 2026-08-13: the backlog grew +129 net in "
        f"30 days with 25% of it >=45 days old and never once advanced."
    )
    for path, rid, why in bad:
        print(f"  {path}: {rid} — {why}")
    print(
        "\nFix: (1) `resolution_criteria` naming the OBSERVABLE condition that ends "
        "the row — 'the bug is fixed' is not one, 'endpoint X returns field Y for a "
        "rotated log, verified on the live fleet' is; (2) `severity` in "
        "critical|high|medium|low; (3) `tier` starting 1, 2 or 3 (a trailing "
        "annotation is fine). If the row is real but blocked on accrual, set "
        "`snoozed_until` to an ISO date instead of leaving it in every review pass."
    )
    return 0 if advisory else 1


def _check_new_rows(base_ref: str) -> int:
    bad: list[tuple[str, str, str]] = []
    for rel in BACKLOGS:
        path = pathlib.Path(rel)
        if not path.exists():
            continue
        before = {str(r.get("id")) for r in _load_at_ref(base_ref, rel) if r.get("id")}
        for row in _load(path):
            rid = str(row.get("id") or "")
            if not rid or rid in before:
                continue  # pre-existing rows are grandfathered, by design
            why = _verdict(row)
            if why:
                bad.append((rel, rid, why))
    if not bad:
        print("backlog-criteria guard: OK — every NEW backlog row states what done looks like.")
    return _report(bad, advisory=False)


def _census() -> int:
    bad: list[tuple[str, str, str]] = []
    total = 0
    for rel in BACKLOGS:
        path = pathlib.Path(rel)
        if not path.exists():
            continue
        for row in _load(path):
            rid = str(row.get("id") or "")
            if not rid:
                continue
            # Only OPEN rows matter: a closed row's criteria are moot.
            #
            # THROUGH THE CANONICAL PREDICATE, NOT A LOCAL SET
            # (BL-20260825-TWO-DEFINITIONS-OF-OPEN-DISAGREE-ABOUT-SEVEN-ROWS --
            # kept on one line so the id stays greppable). This was a hand-rolled
            # `{"resolved", "closed", "wontfix"}`, a SECOND definition of "is
            # this row open" living beside `backlog_counts.is_open_status`, and
            # the two disagreed. It missed `superseded`, `invalid`, `duplicate`,
            # `fixed`, `measured_no_action` and the `resolved_*` family — and,
            # precisely, it spelled the one closed status it DID try to handle
            # as `wontfix` while the corpus writes `wont_fix`, so the underscore
            # form fell straight through. That is the `WARNING` vs `WARN` shape
            # this repo has already paid for once.
            #
            # Measured 2026-08-25: SEVEN terminal rows (4 `wont_fix`, 2
            # `superseded`, 1 `invalid`) were being demanded to carry an exit
            # condition they will never need — 6.4% of a 109-finding census,
            # inflating the number a reader uses to judge whether the backlog is
            # improving.
            if not is_open_status(row.get("status")):
                continue
            total += 1
            why = _verdict(row)
            if why:
                bad.append((rel, rid, why))
    print(f"backlog-criteria census: {len(bad)} of {total} OPEN row(s) lack usable criteria.")
    return _report(bad, advisory=True)


def _load_is_open_status():
    """The ONE predicate for "is this backlog row still open".

    Imported from `scripts/reports/backlog_counts.py` rather than re-derived:
    that module's own header carries the incident record for why the token sets
    are shaped the way they are, and a copy here would be free to drift from it
    (it already had, silently).

    RAISES rather than falling back to a local approximation. A guard that
    quietly degrades to a weaker predicate when its canonical one is missing is
    the "cheaper to lie to than to satisfy" failure this file's own header
    names — the census would keep printing a number while measuring something
    else.
    """
    import importlib.util

    root = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_backlog_counts", root / "scripts" / "reports" / "backlog_counts.py")
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise RuntimeError(
            "check_backlog_criteria: cannot load the canonical open-status "
            "predicate from scripts/reports/backlog_counts.py. Refusing to "
            "re-derive it locally — that is how the two definitions drifted."
        )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.is_open_status


is_open_status = _load_is_open_status()


_GOOD_CRIT = ("Endpoint /api/bot/x returns field y for a rotated log, verified "
              "on the live fleet via the diag relay.")


def _self_test() -> int:
    """Prove the guard fails CLOSED on known-bad input, and passes a good row.

    A guard is only worth its green if it has been shown to go red. Both
    directions are asserted so a future edit that neuters `_verdict` is caught
    here rather than by the next row that slips through.
    """
    cases = [
        ({"id": "X"}, True, "missing field"),
        ({"id": "X", "resolution_criteria": ""}, True, "empty"),
        ({"id": "X", "resolution_criteria": "TBD"}, True, "placeholder"),
        ({"id": "X", "resolution_criteria": "  n/a  "}, True, "placeholder w/ whitespace"),
        ({"id": "X", "resolution_criteria": "fixed"}, True, "too short"),
        # `severity`/`tier` added 2026-08-13. NOTE the case below used to be the
        # "good row" and now correctly REJECTS — criteria alone is no longer
        # enough. Keeping it as a reject case is the regression test that the two
        # new fields are actually load-bearing and not merely declared.
        (
            {"id": "X", "resolution_criteria":
                "A relay shadow_stats read carries soak_start_basis per row and the "
                "registry_soak_source envelope, verified on the live fleet."},
            True, "good criteria but NO severity/tier",
        ),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "tier": 1},
         True, "criteria+tier but no severity"),
        # SHAPE CASES (added 2026-08-25, closing criterion 2 of
        # BL-20260823-RESOLUTION-CRITERIA-HAS-TWO-TYPES-AND-ITERATING-IT-YIELDS-CHARACTERS).
        # `_verdict` used to do `str(raw)`, which is type-agnostic BY LUCK: a
        # dict or an int stringifies to something that clears the 40-char floor,
        # so a malformed row passed the guard whose whole job is malformed rows.
        ({"id": "X", "resolution_criteria": {"a": _GOOD_CRIT},
          "severity": "high", "tier": 1},
         True, "criteria is a DICT — must be rejected, not stringified"),
        ({"id": "X", "resolution_criteria": 12345678901234567890123456789012345678901234,
          "severity": "high", "tier": 1},
         True, "criteria is an INT long enough to clear the floor as a repr"),
        ({"id": "X", "resolution_criteria": [{"a": 1}],
          "severity": "high", "tier": 1},
         True, "criteria LIST containing a dict"),
        # THE ACCIDENTAL-PASS CASE, and the reason criteria_text joins on a
        # newline instead of using repr: the PROSE here is 21 chars, under the
        # 40-char floor, but `str(['too', 'short', 'x'])` is 26 and the older
        # bracket-and-quote punctuation is what a repr-based floor was counting.
        ({"id": "X", "resolution_criteria": ["too short", "also short"],
          "severity": "high", "tier": 1},
         True, "list whose PROSE is under the floor"),
        # ...and the list shape still passes on real prose, so the fix did not
        # simply outlaw the minority shape.
        ({"id": "X", "resolution_criteria": [_GOOD_CRIT, "And a second one."],
          "severity": "high", "tier": 1},
         False, "LIST of real criteria is accepted"),
        ({"id": "X", "resolution_criteria": [None, "", _GOOD_CRIT],
          "severity": "high", "tier": 1},
         False, "list with empty/None entries dropped, real prose kept"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high"},
         True, "criteria+severity but no tier"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "P1", "tier": 1},
         True, "legacy severity spelling (P1) is refused on NEW rows"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "low-medium", "tier": 1},
         True, "hyphenated severity refused — five spellings of medium is the defect"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": "mixed"},
         True, "tier from which no 1/2/3 can be read"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "snoozed_until": "soon"}, True, "unparseable snoozed_until"),
        # ── accepts ────────────────────────────────────────────────────────
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1},
         False, "complete row"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "MEDIUM", "tier": "Tier-2"},
         False, "case-insensitive severity + Tier- prefixed tier"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "low",
          "tier": "1 (research; any promotion past candidate is Tier-3/operator)"},
         False, "trailing tier annotation is allowed — only unreadable is refused"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "snoozed_until": "2026-09-30"}, False, "valid ISO snooze"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "snoozed_until": None}, False, "snoozed_until null is fine — the field is optional"),
    ]
    failures = 0
    for row, should_fail, label in cases:
        got = _verdict(row) is not None
        ok = got == should_fail
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: "
              f"expected {'reject' if should_fail else 'accept'}, got "
              f"{'reject' if got else 'accept'}")
        failures += 0 if ok else 1
    if failures:
        print("self-test FAILED — the guard does not fail closed.")
        return 1
    print("self-test OK — rejects empty/placeholder/too-short criteria, missing or\n           legacy severity, unreadable tier, and unparseable snooze; accepts a\n           complete row.")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="git ref to diff against; only NEW rows are checked")
    ap.add_argument("--all", action="store_true", help="advisory census over all OPEN rows")
    ap.add_argument("--self-test", action="store_true", help="prove the guard fails closed")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        return _self_test()
    if args.all:
        return _census()
    if args.base:
        return _check_new_rows(args.base)
    ap.error("one of --base, --all or --self-test is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
