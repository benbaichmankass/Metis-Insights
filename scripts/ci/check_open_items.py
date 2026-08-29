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
lost between sessions. A register nobody reads is worse than none: it *looks*
like the follow-up mechanism exists.

⚠️ **THE CAP IS GONE, and this paragraph used to argue for it.** It read "the
cap is the feature, not a limitation of it. Adding a 13th item means clearing
one first, and that pressure is the whole design" — describing a `MAX_ITEMS`
that was set to `None` on 2026-08-26 by operator direction (*"we don't want to
cap the number of bugs we can track, we want to ensure that they are actually
being tracked, fixed, and learned from"*). See the `MAX_ITEMS` comment below
for the reasoning; FIELD BEATS COMMENT, and this comment was the field's
loudest contradiction. Corrected 2026-08-29 by /system-review, together with
the same false claim in `CLAUDE.md`'s SESSION BRIEF. It is not a cosmetic
edit: a session that believes a cap is enforced either declines to file a row
it should file, or DELETES a live row to make room — a register of known
problems that deletes knowledge to stay short is the bandaid the operator
removed.

WHAT BOUNDS THE REGISTER INSTEAD is that a `monitoring` row must be RE-OBSERVED
on its own cadence: it cannot be carried by doing nothing.

TWO CHECKS, and each maps to a way the register dies:
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

#: DELIBERATELY NO CAP (operator-directed 2026-08-26: "we don't want to cap the
#: number of bugs we can track, we want to ensure that they are actually being
#: tracked, fixed, and learned from"). An earlier version capped this at 12 and
#: that was a bandaid: it bounded the LIST rather than making anything get read
#: or fixed, and a cap on a register of KNOWN PROBLEMS just deletes knowledge.
#: What bounds the register instead is that a `monitoring` row must be
#: RE-OBSERVED on its own cadence — it cannot be carried by doing nothing.
MAX_ITEMS = None

#: How long a row may sit without being re-affirmed. Chosen, not measured:
#: long enough that an ordinary week of sessions does not churn it, short
#: enough that a row cannot quietly outlive a milestone.
_STALE_DAYS = 21

_REQUIRED = ("id", "opened", "kind", "summary", "clears_when")
#: `monitoring` is the enforced kind: it must be re-OBSERVED on a cadence and is
#: rendered into CLAUDE.md when due. The others are context a session should
#: know but need not act on.
_KINDS = {"monitoring", "awaiting_verification", "background_awareness",
          "pending_decision"}


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

        if row.get("kind") == "monitoring":
            # A monitoring row is only worth anything if it records WHAT WAS SEEN.
            # `loud: true` used to stand here and it enforced nothing — it was an
            # adjective, and an alarm nobody must answer is one more alarm to walk
            # past (operator, 2026-08-26). The cadence + observation pair is what
            # makes carrying the row cost an honest look.
            try:
                every = int(row.get("check_every_days"))
            except (TypeError, ValueError):
                every = 0
            if every <= 0:
                problems.append(
                    f"{rid}: kind 'monitoring' needs a positive 'check_every_days' "
                    f"— without a cadence it can be carried forever by doing nothing")
            obs = row.get("observation")
            if not isinstance(obs, str) or len(obs.strip()) < 40:
                problems.append(
                    f"{rid}: kind 'monitoring' needs an 'observation' saying what was "
                    f"actually SEEN at the last check. A claim of progress is not an "
                    f"observation, and an empty one makes the cadence decorative")

        opened = _parse_day(row.get("opened"))
        reaffirmed = _parse_day(row.get("reaffirmed")) or _parse_day(row.get("verified_at"))
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
    items = json.loads(Path(args.path).read_text(encoding="utf-8"))["items"]
    mon = sum(1 for i in items if i.get("kind") == "monitoring")
    print(f"open-items-guard: OK — {len(items)} items ({mon} monitoring), every one "
          f"workable and affirmed within {_STALE_DAYS} days.")
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
            ("there is NO cap — many rows is fine",
             run([{**good, "id": f"OI-{i}"} for i in range(40)]), False),
            ("a monitoring row with no cadence is a finding",
             run([{**good, "kind": "monitoring",
                   "observation": "x" * 50, "verified_at": "2026-08-26"}]), True),
            ("a monitoring row with no observation is a finding",
             run([{**good, "kind": "monitoring", "check_every_days": 2,
                   "verified_at": "2026-08-26"}]), True),
            ("a complete monitoring row passes",
             run([{**good, "kind": "monitoring", "check_every_days": 2,
                   "verified_at": "2026-08-26", "observation": "x" * 50}]), False),
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
