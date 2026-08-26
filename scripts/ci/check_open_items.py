#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (open-items-guard)
"""Keep ``docs/claude/OPEN-ITEMS.json`` SHORT, WORKABLE and HONEST.

WHY THIS EXISTS
---------------
Operator directive, 2026-08-26: *"we need to improve the mechanisms for
following up on items that need to be resolved/verified across sessions —
there needs to be some sort of log that new sessions know to check to see what
open items they need to be aware of, whether for verification/updates or just
to know about processes going on in the background that could affect their
work."*

The register is the log. **This guard is what stops it becoming the thing it
replaced.** `docs/claude/health-review-backlog.json` is 951 rows and 5.1 MB —
nobody reads that at session start, which is precisely why items were being
lost between sessions. A register with no cap follows it there, and a register
nobody reads is worse than none: it *looks* like the follow-up mechanism
exists.

So the cap is the feature, not a limitation of it. Adding a 13th item means
clearing one first, and that pressure is the whole design.

THREE CHECKS, and each maps to a way the register dies:

* **cap**          — it grows into a second backlog and stops being read.
* **workability**  — a row with no `clears_when` names no observable end
                     condition, so nobody can ever tell it is finished and it
                     is carried forever. Same failure `check_backlog_criteria`
                     exists for, and the same remedy.
* **staleness**    — rows silently outlive their relevance and the register
                     becomes a museum. A row older than `_STALE_DAYS` must be
                     re-affirmed (bump `reaffirmed`) or cleared. Re-affirming
                     is cheap; that is the point — the cost is *looking*, not
                     typing.

⚠️ **This guard does NOT check whether an item is resolved.** It cannot: the
whole class of item here is one whose resolution is only observable on the live
fleet. A guard that pretended otherwise would be cheaper to satisfy than to
honour, which is the `new-table-wiring-guard` lesson (a presence-only marker
made the cheapest way to silence a real finding *naming a table that does not
exist*).
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

_REGISTER = Path("docs/claude/OPEN-ITEMS.json")

#: Hard ceiling. See the module docstring — this is the mechanism.
MAX_ITEMS = 12

#: How long a row may sit without being re-affirmed. Chosen, not measured:
#: long enough that an ordinary week of sessions does not churn it, short
#: enough that a row cannot quietly outlive a milestone.
_STALE_DAYS = 21

_REQUIRED = ("id", "opened", "kind", "summary", "clears_when")
_KINDS = {"awaiting_verification", "background_awareness", "pending_decision"}


def _parse_day(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def check(path: Path, today: date | None = None) -> list[str]:
    """Return a list of human-readable problems. Empty == clean."""
    today = today or datetime.now(timezone.utc).date()
    problems: list[str] = []

    if not path.is_file():
        return [f"{path} is MISSING. It is named in CLAUDE.md § 'Every session' "
                f"as the first thing a session reads; a session that cannot "
                f"find it has no follow-up surface at all."]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"{path} did not parse: {exc}"]

    items = data.get("items")
    if not isinstance(items, list):
        return [f"{path}: 'items' is not a list"]

    if len(items) > MAX_ITEMS:
        problems.append(
            f"{len(items)} items — the cap is {MAX_ITEMS}. THE CAP IS THE "
            f"FEATURE: clear one before adding another. A register that grows "
            f"becomes a second backlog and stops being read, which is the "
            f"failure it exists to fix, not a rule to route around."
        )

    seen: set[str] = set()
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            problems.append(f"item[{i}] is not an object")
            continue
        rid = str(row.get("id") or f"<no id, index {i}>")

        for field in _REQUIRED:
            val = row.get(field)
            if not isinstance(val, str) or not val.strip():
                problems.append(f"{rid}: missing or empty '{field}'")

        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)

        kind = row.get("kind")
        if isinstance(kind, str) and kind not in _KINDS:
            problems.append(
                f"{rid}: kind '{kind}' is not one of {sorted(_KINDS)}")

        clears = row.get("clears_when")
        if isinstance(clears, str) and clears.strip():
            # A clears_when that restates the fix is not an observable
            # condition. This catches the laziest form only, deliberately —
            # a checker that tried to judge observability would be guessing.
            lowered = clears.strip().lower()
            if lowered in ("the fix works", "it is fixed", "resolved", "done",
                           "when it works", "n/a", "tbd"):
                problems.append(
                    f"{rid}: clears_when '{clears}' names no observable "
                    f"condition — nobody can tell when this row is finished, "
                    f"so it will be carried forever")

        opened = _parse_day(row.get("opened"))
        reaffirmed = _parse_day(row.get("reaffirmed"))
        anchor = reaffirmed or opened
        if anchor is None:
            problems.append(
                f"{rid}: 'opened' is not a readable date, so staleness cannot "
                f"be judged — that is 'we did not look', not 'it is fresh'")
        else:
            age = (today - anchor).days
            if age > _STALE_DAYS:
                problems.append(
                    f"{rid}: {age} days since it was last affirmed (limit "
                    f"{_STALE_DAYS}). Re-check it and set 'reaffirmed' to "
                    f"today, or clear the row. The cost is LOOKING, not typing."
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=str(_REGISTER))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    problems = check(Path(args.path))
    if problems:
        print("::error::docs/claude/OPEN-ITEMS.json is the register EVERY "
              "session reads at start. It is not workable as it stands:")
        for p in problems:
            print(f"  - {p}")
        return 1
    n = len(json.loads(Path(args.path).read_text(encoding="utf-8"))["items"])
    print(f"open-items-guard: OK — {n}/{MAX_ITEMS} items, every one workable "
          f"and affirmed within {_STALE_DAYS} days.")
    return 0


def _self_test() -> int:
    """Prove the guard can find a positive before its silence is trusted."""
    import tempfile

    ok = True
    with tempfile.TemporaryDirectory() as d:
        base = {"schema_version": 1, "items": []}
        good = {"id": "OI-1", "opened": "2026-08-26", "kind": "background_awareness",
                "summary": "s", "clears_when": "a named observable thing happens"}

        def run(items, today="2026-08-26"):
            p = Path(d) / "r.json"
            p.write_text(json.dumps({**base, "items": items}))
            return check(p, today=date.fromisoformat(today))

        cases = [
            ("clean register passes", run([good]), False),
            ("over the cap is a finding", run([{**good, "id": f"OI-{i}"}
                                               for i in range(MAX_ITEMS + 1)]), True),
            ("missing clears_when is a finding",
             run([{k: v for k, v in good.items() if k != "clears_when"}]), True),
            ("a non-observable clears_when is a finding",
             run([{**good, "clears_when": "the fix works"}]), True),
            ("a stale row is a finding", run([good], today="2026-10-01"), True),
            ("re-affirming clears staleness",
             run([{**good, "reaffirmed": "2026-09-28"}], today="2026-10-01"), False),
            ("an undateable row is a finding, not a pass",
             run([{**good, "opened": "whenever"}]), True),
            ("a duplicate id is a finding", run([good, good]), True),
            ("a missing register is a finding",
             check(Path(d) / "nope.json"), True),
        ]
        for label, problems, want_problem in cases:
            got = bool(problems)
            status = "PASS" if got == want_problem else "FAIL"
            if got != want_problem:
                ok = False
            print(f"  self-test ({label}): {status}"
                  + (f" -- {problems}" if got != want_problem else ""))
    print("open-items-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
