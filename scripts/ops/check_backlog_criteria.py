#!/usr/bin/env python3
"""A NEW backlog row must say what DONE looks like.

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
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any, Iterable

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


def _verdict(row: dict[str, Any]) -> str | None:
    """Return a human reason this row fails, or None when it passes."""
    raw = row.get("resolution_criteria")
    if raw is None:
        return "no resolution_criteria field"
    text = str(raw).strip()
    if text.casefold() in _PLACEHOLDERS:
        return f"resolution_criteria is a placeholder ({text!r})"
    if len(text) < _MIN_LEN:
        return (
            f"resolution_criteria is {len(text)} chars, under the {_MIN_LEN}-char "
            f"floor — too short to name an observable condition ({text!r})"
        )
    return None


def _report(bad: list[tuple[str, str, str]], *, advisory: bool) -> int:
    if not bad:
        return 0
    lead = "::warning::" if advisory else "::error::"
    print(
        f"{lead}backlog row(s) without usable resolution_criteria. A row nobody can "
        f"tell is FINISHED never gets closed — that is how two high-severity rows sat "
        f"open for days after their fixes landed (2026-08-12), and how a backlog "
        f"degrades into noise that hides real findings."
    )
    for path, rid, why in bad:
        print(f"  {path}: {rid} — {why}")
    print(
        "\nFix: add `resolution_criteria` naming the OBSERVABLE condition that ends "
        "the row — what a future session must see to close it. 'the bug is fixed' is "
        "not one; 'endpoint X returns field Y for a rotated log, verified on the live "
        "fleet' is."
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
            if str(row.get("status", "open")).lower() in {"resolved", "closed", "wontfix"}:
                continue
            total += 1
            why = _verdict(row)
            if why:
                bad.append((rel, rid, why))
    print(f"backlog-criteria census: {len(bad)} of {total} OPEN row(s) lack usable criteria.")
    return _report(bad, advisory=True)


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
        (
            {"id": "X", "resolution_criteria":
                "A relay shadow_stats read carries soak_start_basis per row and the "
                "registry_soak_source envelope, verified on the live fleet."},
            False, "a real criterion",
        ),
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
    print("self-test OK — rejects empty/placeholder/too-short, accepts a real criterion.")
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
