#!/usr/bin/env python3
"""One rolled-up DAILY summary of autonomous work — Phase B, the digest half.

The notification contract has two halves. ``work_phase_ping.py`` (Phase A) ships
the first: an event **per state change**, as it happens. This ships the second:
**one rolled-up daily summary**, so the operator can see a day's movement in a
single message instead of reconstructing it from the repo.

⚠️ **STATE CHANGES ONLY, NEVER ACTIVITY.** A verdict written, a decision
recorded, a deployment made, a WIP ceiling hit — those are events. A sweep
started, a file edited, a session opened — those are not, and must never reach
the operator. This is not a stylistic preference: **202 of 376 CRITICAL/ERROR
rows in one measured window were a single un-latched alarm**, which trained the
operator past the one channel reserved for an unprotected position. A digest that
narrates activity would rebuild that failure on a daily cadence.

**The event definition is IMPORTED, not re-derived.** ``PING_WORTHY`` and
``transitions()`` come from ``work_phase_ping`` so the per-event path and the
roll-up can never drift on what counts as an event. Two copies of that predicate
is exactly how they would.

⚠️ **IT WRITES TO ``docs/claude/pending-pings.jsonl``, NOT TO TELEGRAM** — the
same queue and the same failure direction as its sibling. The VM's
``scripts/notify_on_pull.py`` drains it on the next ``ict-git-sync`` pull. So a
digest is truth in transit between the commit and the send, and it fails BACK:
an un-committed row is a digest that never happened, never one wrongly shown as
delivered.

⚠️ **UNSCHEDULED AS SHIPPED.** There is no cron behind this. It is a plain script
so an existing daily job can call it; wiring the trigger is a
``.github/workflows/`` change owned by another session. **A digest that has never
fired has not been observed to work** — and that is not hypothetical here:
``probes.yml`` and ``due-list.yml`` were both merged, enabled, and have never
fired on cron (``OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON``).

Usage::

    python3 scripts/ops/work_digest.py --base origin/main~20 --head HEAD
    python3 scripts/ops/work_digest.py --base <ref> --head HEAD --write
    python3 scripts/ops/work_digest.py --self-test
"""
# wiring: manual-only - no cron ships with this. The trigger is a
# `.github/workflows/` concern declared by another session, so this ships as a
# callable script with a pure `build_digest()` rather than guessing at a
# scheduler. Claiming a cadence the repo does not have would be the same
# "a green run is not an observation" error the module docstring warns about.

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# ONE owner for "what counts as an event" — imported, never re-derived.
from scripts.ops.work_phase_ping import (  # noqa: E402
    PING_WORTHY,
    _field,
    _git_show_dir,
    transitions,
)

PENDING = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"
STATE = REPO_ROOT / "runtime_logs" / "work_digest_state.json"

# Mirrors src/web/api/routers/work.py. Kept in the same order as the design.
LIFECYCLE_STATES: tuple[str, ...] = (
    "dormant", "ready", "in_flight", "waiting", "done", "accepted",
)
UNKNOWN = "unknown"
COUNTS_AGAINST_CEILING = frozenset({"in_flight"})
WIP_CEILING = 8


def _resolve(ref: str) -> str | None:
    """Resolve a ref to a sha, or None if it does not exist HERE.

    ⚠️ Returning None is load-bearing on a SHALLOW clone, which is the normal
    state of a session's checkout: a ref that is simply not in this clone's
    history must read as *we could not look at that window*, never as *nothing
    changed in it*. Those are opposite statements and the second is the
    dangerous one — it would report a quiet day for a window nobody examined.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return out or None
    except subprocess.CalledProcessError:
        return None


def _is_shallow() -> bool:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip() == "true"
    except subprocess.CalledProcessError:
        return False


def standing_state(ref: str = "HEAD") -> dict[str, Any]:
    """The store's CURRENT shape at ``ref`` — the denominator for any change.

    Every lifecycle state ships as an explicit key with an explicit zero, plus
    ``unknown`` for a row whose state could not be read. They sum to the object
    count by construction, so the partition is checkable rather than trusted.
    """
    files = _git_show_dir(ref)
    counts = {s: 0 for s in (*LIFECYCLE_STATES, UNKNOWN)}
    blocked: list[dict[str, str]] = []
    for path, text in sorted(files.items()):
        state = _field(text, "lifecycle")
        counts[state if state in LIFECYCLE_STATES else UNKNOWN] += 1
        if state == "waiting":
            blocked.append({
                "object": _field(text, "id") or Path(path).stem,
                "title": _field(text, "title") or "",
            })
    in_flight = sum(counts[s] for s in COUNTS_AGAINST_CEILING)
    return {
        "objectCount": len(files),
        "lifecycle": counts,
        "waiting": blocked,
        "wip": {
            "ceiling": WIP_CEILING,
            "inFlight": in_flight,
            # ⚠️ DECLARED, not enforced — Phase C enforces it alongside the
            # migration. `ceilingHit` is a real event worth pinging; "under the
            # ceiling" is NOT a clean bill of health while nothing checks it.
            "enforced": False,
            "state": "declared_not_enforced",
            "ceilingHit": in_flight >= WIP_CEILING,
        },
    }


def build_digest(base: str, head: str = "HEAD", now: datetime | None = None) -> dict[str, Any]:
    """Assemble the digest. Pure apart from git reads — no writes, no network.

    ``digestState`` is three states and they are never collapsed:

      * ``window_unresolved`` — ``base`` is not in this clone. **We could not
        look.** Emphatically NOT "nothing changed"; on a shallow clone this is
        the common case and reporting it as a quiet day would be a false
        negative delivered with confidence.
      * ``no_changes``        — the window resolved and held no ping-worthy
        transition. A real, reportable observation.
      * ``changes_observed``  — one or more state changes.
    """
    now = now or datetime.now(timezone.utc)
    base_sha, head_sha = _resolve(base), _resolve(head)
    standing = standing_state(head if head_sha else "HEAD")

    if base_sha is None or head_sha is None:
        return {
            "digestState": "window_unresolved",
            "unresolvedRef": base if base_sha is None else head,
            "shallowClone": _is_shallow(),
            "base": base, "head": head,
            "changes": [],
            "standing": standing,
            "generatedAt": now.isoformat(),
            "coverageComplete": False,
        }

    changes = [t for t in transitions(base, head) if t["to"] in PING_WORTHY]
    return {
        "digestState": "changes_observed" if changes else "no_changes",
        "unresolvedRef": None,
        "shallowClone": _is_shallow(),
        "base": base, "head": head,
        "baseSha": base_sha[:8], "headSha": head_sha[:8],
        "changes": changes,
        "standing": standing,
        "generatedAt": now.isoformat(),
        "coverageComplete": False,
    }


def render(d: dict[str, Any]) -> str:
    """One operator-readable message. States its population on every number."""
    st = d["standing"]
    lc = st["lifecycle"]
    n = st["objectCount"]
    lines = [f"[work digest] {d['generatedAt'][:10]}"]

    if d["digestState"] == "window_unresolved":
        lines.append(
            f"⚠️ window NOT examined — ref {d['unresolvedRef']!r} is not in this clone"
            + (" (shallow)" if d["shallowClone"] else "")
            + ". This is 'we could not look', NOT 'nothing changed'."
        )
    elif d["digestState"] == "no_changes":
        lines.append(
            f"No lifecycle change in {d['baseSha']}..{d['headSha']} "
            f"(population: {n} work objects)."
        )
    else:
        lines.append(
            f"{len(d['changes'])} state change(s) in {d['baseSha']}..{d['headSha']} "
            f"(population: {n} work objects):"
        )
        for t in d["changes"]:
            origin = t["from"] or "new"
            title = f" · {t['title']}" if t["title"] else ""
            lines.append(f"  • {t['object']}: {origin} → {t['to']}{title}")

    lines.append(
        "Standing: "
        + " / ".join(f"{s} {lc[s]}" for s in (*LIFECYCLE_STATES, UNKNOWN))
        + f" (of {n})"
    )

    wip = st["wip"]
    if wip["ceilingHit"]:
        lines.append(
            f"⚠️ WIP CEILING HIT: {wip['inFlight']} in flight vs ceiling "
            f"{wip['ceiling']} — DECLARED, not enforced (Phase C enforces)."
        )
    else:
        lines.append(
            f"WIP {wip['inFlight']}/{wip['ceiling']} in flight "
            f"({wip['state']} — this is a reading, not a gate)."
        )

    if st["waiting"]:
        lines.append("Waiting: " + ", ".join(w["object"] for w in st["waiting"]))

    lines.append(
        "⚠️ Store covers the operating-layer build's own phases only — the "
        "carried backlog rows migrate in Phase C. Not the whole of system work."
    )
    return "\n".join(lines)


def _already_sent_today(day: str) -> bool:
    """One digest per UTC day. A latch, so a double invocation cannot double-ping."""
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("lastDigestDay") == day
    except (OSError, ValueError):
        # ⚠️ An unreadable latch SENDS rather than suppresses. Failing loud is
        # the only safe direction on a notification path, and it makes a broken
        # latch announce itself as a duplicate instead of as silence — the
        # reasoning the target-naked cooldown had to be corrected to.
        return False


def _record_sent(day: str) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"lastDigestDay": day}), encoding="utf-8")
    except OSError as exc:
        print(f"work-digest: WARNING could not write latch {STATE}: {exc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main",
                    help="ref to compare FROM (the start of the window)")
    ap.add_argument("--head", default="HEAD", help="ref to compare TO")
    ap.add_argument("--write", action="store_true",
                    help="append the digest to pending-pings.jsonl (default: print only)")
    ap.add_argument("--force", action="store_true",
                    help="write even if a digest was already recorded for today")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    digest = build_digest(a.base, a.head)
    message = render(digest)
    print(message)

    if not a.write:
        return 0

    day = digest["generatedAt"][:10]
    if _already_sent_today(day) and not a.force:
        print(f"work-digest: a digest is already recorded for {day} — not queuing "
              f"a second (use --force to override)")
        return 0

    row = {
        "at": digest["generatedAt"],
        "target": "claude",
        "priority": "normal",
        "event": "work_digest",
        "digest_state": digest["digestState"],
        "message": message,
    }
    try:
        with PENDING.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"work-digest: FAILED to queue: {exc}")
        return 1
    _record_sent(day)
    print("work-digest: queued — COMMIT pending-pings.jsonl or the digest never "
          "happened (transit fails back, by design)")
    return 0


def _self_test() -> int:
    """A detector whose failure path is never exercised is indistinguishable
    from one that always passes. Each check here has a positive control."""
    ok = True

    def check(n: int, label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  self-test {n} ({label}): {'PASS' if passed else f'FAIL {detail}'}")

    # 1-2: an unresolvable base must read as "did not look", never "no changes".
    d = build_digest("definitely-not-a-ref-000", "HEAD")
    check(1, "unresolvable base -> window_unresolved",
          d["digestState"] == "window_unresolved", str(d["digestState"]))
    check(2, "and NEVER no_changes", d["digestState"] != "no_changes")

    # 3: that state must SAY it did not look, in the operator-visible text.
    txt = render(d)
    check(3, "message says 'could not look', not a quiet day",
          "could not look" in txt.lower() or "NOT examined" in txt, txt[:80])

    # 4: the standing partition must sum to the object count.
    st = standing_state("HEAD")
    total = sum(st["lifecycle"].values())
    check(4, "lifecycle buckets sum to objectCount",
          total == st["objectCount"], f"{total} != {st['objectCount']}")

    # 5: every state key present, including explicit zeros.
    missing = [s for s in (*LIFECYCLE_STATES, UNKNOWN) if s not in st["lifecycle"]]
    check(5, "no lifecycle key ever vanishes", not missing, str(missing))

    # 6: dormant/ready are NOT events — imported from the one owner.
    noisy = [s for s in ("dormant", "ready") if s in PING_WORTHY]
    check(6, "dormant/ready never ping (imported predicate)", not noisy, str(noisy))

    # 7: a ceiling hit renders as an event; positive control on the negative.
    hit = dict(st, wip=dict(st["wip"], inFlight=WIP_CEILING, ceilingHit=True))
    hit_txt = render({**d, "standing": hit})
    quiet_txt = render(d)
    check(7, "ceiling hit is loud, and not-hit is not",
          "WIP CEILING HIT" in hit_txt and "WIP CEILING HIT" not in quiet_txt)

    # 8: the digest never claims the store is complete.
    check(8, "coverage is declared incomplete", d["coverageComplete"] is False)

    # 9: an unreadable latch must SEND, not suppress.
    check(9, "unreadable latch fails loud (sends)",
          _already_sent_today("not-a-day-that-was-recorded") is False)

    print("work-digest self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
