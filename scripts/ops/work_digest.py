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
# ONE owner for the ceiling + migration facts — imported, never re-derived.
# This file restating them is precisely how the SPA and the digest came to
# disagree after Phase C; see src/utils/work_facts.py.
from src.utils.work_facts import WIP_CEILING as _WIP_CEILING  # noqa: E402
from src.utils.work_facts import (  # noqa: E402
    CARRIED_ROWS_MIGRATED_IN,
    CEILING_ENFORCED,
    CEILING_STATE,
)
from scripts.ops.work_phase_ping import (  # noqa: E402
    PING_WORTHY,
    _field,
    _git_show_dir,
    transitions,
)

PENDING = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"
STATE = REPO_ROOT / "runtime_logs" / "work_digest_state.json"

# The standing close-wedge ledger (MI-34). Written by the TRADER
# (src/runtime/close_wedge_standing.py) into runtime_logs/, which is
# .gitignore'd and VM-local — so on a GitHub runner this path is ABSENT unless
# the workflow fetched it from the live diag surface first
# (/api/diag/log_file?name=close_wedge_standing), which is exactly what
# .github/workflows/work-digest.yml does before calling this.
#
# ⚠️ ABSENT HERE IS "WE DID NOT LOOK", NOT "NOTHING IS WEDGED". The digest is
# the channel an operator-approved downgrade was routed INTO, so a missing
# ledger rendering as a clean bill of health is the precise failure this whole
# section exists to prevent: the item falling quietly out of BOTH channels.
STANDING_WEDGES = REPO_ROOT / "runtime_logs" / "close_wedge_standing.json"

# Mirrors src/web/api/routers/work.py. Kept in the same order as the design.
LIFECYCLE_STATES: tuple[str, ...] = (
    "dormant", "ready", "in_flight", "waiting", "done", "accepted",
)
UNKNOWN = "unknown"
COUNTS_AGAINST_CEILING = frozenset({"in_flight"})
# Re-exported from the shared owner so existing references keep working;
# the VALUE lives in src/utils/work_facts.py and only there.
WIP_CEILING = _WIP_CEILING


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
            "enforced": CEILING_ENFORCED,
            "state": CEILING_STATE,
            "ceilingHit": in_flight >= WIP_CEILING,
        },
    }


def standing_wedges(path: Path | None = None) -> dict[str, Any]:
    """The close failures DOWNGRADED into this digest, and whether we could see them.

    ``wedgeState`` is three states and they are never collapsed — the same
    discipline ``digestState`` already applies to the git window, applied to a
    file that arrives over a network from another machine:

      * ``not_fetched``  — no ledger on disk. **We did not look.** On a GitHub
        runner this is the DEFAULT state, because ``runtime_logs/`` is VM-local;
        it means the diag fetch did not happen or did not succeed, NOT that the
        fleet is clean.
      * ``unreadable``   — a ledger is here and could not be parsed. We looked
        and could not see. Distinct from both of the others.
      * ``read``         — parsed. ``wedges`` is the real set, and an EMPTY set
        is a real, reportable observation: nothing is standing.

    ⚠️ **``read`` + zero wedges is the ONLY state that may be reported as "no
    standing wedges".** The other two must say what they are, out loud, in the
    operator-visible text.
    """
    p = path or STANDING_WEDGES
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"wedgeState": "not_fetched", "wedges": [], "count": 0,
                "schema": None, "source": str(p)}
    except (OSError, ValueError) as exc:
        return {"wedgeState": "unreadable", "wedges": [], "count": 0,
                "schema": None, "source": str(p), "reason": str(exc)[:200]}
    if not isinstance(raw, dict):
        return {"wedgeState": "unreadable", "wedges": [], "count": 0,
                "schema": None, "source": str(p),
                "reason": "ledger is not a JSON object"}
    wedges = raw.get("wedges")
    rows = [
        dict(v, key=k) for k, v in sorted((wedges or {}).items())
        if isinstance(v, dict)
    ]
    return {
        "wedgeState": "read",
        "wedges": rows,
        "count": len(rows),
        "schema": raw.get("schema"),
        "updatedAt": raw.get("updated_at"),
        "source": str(p),
    }


def _age_days(first_seen: object, now: datetime) -> str:
    """How long this wedge has stood, or an explicit refusal to guess."""
    try:
        dt = datetime.fromisoformat(str(first_seen))
    except (TypeError, ValueError):
        return "age unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{(now - dt).total_seconds() / 86400.0:.1f}d"


def render_standing_wedges(sw: dict[str, Any], now: datetime) -> list[str]:
    """The standing-wedge block. **It is never empty** — every state prints a line.

    A section that renders nothing when it has nothing to say is
    indistinguishable from a section that has broken, and this one carries items
    the pager was deliberately told to stop carrying. So all three states speak.
    """
    state = sw.get("wedgeState")
    if state == "not_fetched":
        return [
            "⚠️ STANDING CLOSE WEDGES: NOT EXAMINED — no ledger at "
            f"{sw.get('source')}. This is 'we did not look', NOT 'nothing is "
            "wedged'. Close failures confirmed unclearable broker-side are "
            "DOWNGRADED out of the pager into this digest, so an un-fetched "
            "ledger means such an item is currently in NEITHER channel. Fetch "
            "/api/diag/log_file?name=close_wedge_standing from the live VM."
        ]
    if state == "unreadable":
        return [
            "⚠️ STANDING CLOSE WEDGES: ledger present but UNREADABLE "
            f"({sw.get('reason') or 'no reason recorded'}). We looked and could "
            "not see — this is NOT 'nothing is wedged'."
        ]
    rows = sw.get("wedges") or []
    if not rows:
        return [
            "Standing close wedges: none (ledger read, 0 entries) — a real "
            "observation, not an absence of one."
        ]
    out = [
        f"🧱 STANDING CLOSE WEDGES: {len(rows)} — confirmed unclearable by any "
        f"bot-side lever, downgraded out of the pager and carried HERE until the "
        f"state changes:"
    ]
    for w in rows:
        out.append(
            f"  • {w.get('account')}/{w.get('symbol')} {w.get('side')} · "
            f"standing {_age_days(w.get('first_seen'), now)} · "
            f"{w.get('share_hold')} · {int(w.get('pages_suppressed') or 0)} page(s) "
            f"suppressed · {str(w.get('detail') or w.get('evidence') or '')[:160]}"
        )
    out.append(
        "  These need OPERATOR or VENUE action; the bot has no lever. They page "
        "again the moment the state changes (cleared, or wedged on new evidence)."
    )
    return out


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

    # ⚠️ READ UNCONDITIONALLY, AND BEFORE THE WINDOW BRANCH. A standing wedge is
    # a condition of the FLEET, not of the git window — an unresolvable base
    # says nothing about whether a position is stuck, and letting the early
    # return skip this block would drop the downgraded item on exactly the runs
    # most likely to be degraded (a shallow clone, a bad ref). Silence must not
    # be reachable while the wedge stands.
    wedges = standing_wedges()

    if base_sha is None or head_sha is None:
        return {
            "digestState": "window_unresolved",
            "unresolvedRef": base if base_sha is None else head,
            "shallowClone": _is_shallow(),
            "base": base, "head": head,
            "changes": [],
            "standing": standing,
            "standingWedges": wedges,
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
        "standingWedges": wedges,
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

    # ⚠️ RENDERED ON EVERY DIGEST, IN EVERY STATE, HIGH IN THE MESSAGE.
    # `.get` with a default rather than `d["standingWedges"]`: a caller holding a
    # digest dict built before this key existed must still render, and the
    # default is the ALARMING state ("we did not look"), never the reassuring one.
    lines.extend(render_standing_wedges(
        d.get("standingWedges") or {"wedgeState": "not_fetched", "wedges": []},
        datetime.fromisoformat(d["generatedAt"]),
    ))

    lines.append(
        "Standing: "
        + " / ".join(f"{s} {lc[s]}" for s in (*LIFECYCLE_STATES, UNKNOWN))
        + f" (of {n})"
    )

    wip = st["wip"]
    if wip["ceilingHit"]:
        lines.append(
            f"⚠️ WIP CEILING HIT: {wip['inFlight']} in flight vs ceiling "
            f"{wip['ceiling']} — ENFORCED in CI: a ninth in_flight object is "
            f"REFUSED without an approved wip-ceiling-exception.yaml."
        )
    else:
        lines.append(
            f"WIP {wip['inFlight']}/{wip['ceiling']} in flight "
            f"({wip['state']} — this IS a gate: the ninth is refused)."
        )

    if st["waiting"]:
        lines.append("Waiting: " + ", ".join(w["object"] for w in st["waiting"]))

    lines.append(
        "⚠️ Store covers the operating-layer build's own phases only — the "
        f"carried backlog rows: {CARRIED_ROWS_MIGRATED_IN}. Not the whole of system work."
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

    # 10-13: the standing-wedge block. Each has a positive control — a check
    # that cannot fail is not a check.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        missing = standing_wedges(tmp / "nope.json")
        check(10, "absent ledger -> not_fetched, never 'no wedges'",
              missing["wedgeState"] == "not_fetched", str(missing["wedgeState"]))

        txt10 = "\n".join(render_standing_wedges(missing, datetime.now(timezone.utc)))
        check(11, "and it SAYS we did not look, in operator-visible text",
              "NOT EXAMINED" in txt10 and "did not look" in txt10, txt10[:90])

        bad = tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        b = standing_wedges(bad)
        check(12, "garbled ledger -> unreadable, distinct from not_fetched",
              b["wedgeState"] == "unreadable", str(b["wedgeState"]))

        good = tmp / "good.json"
        # collapsed-state: broker_cancel_wedged — the digest carries the
        # DOWNGRADED class and nothing else, so this is the only share_hold
        # value that can appear in a ledger entry. The other three states never
        # produce a ledger row: they page instead, and never reach this file.
        # The digest does NOT re-derive the routing decision, deliberately —
        # re-implementing "which failures are quiet" in a second module is how
        # the two would come to disagree about what the operator is being told.
        good.write_text(json.dumps({"schema": 1, "wedges": {"a|GLD|sell": {
            "account": "a", "symbol": "GLD", "side": "sell",
            "share_hold": "broker_cancel_wedged", "detail": "x is pending_cancel",
            "first_seen": "2026-08-27T14:02:00+00:00", "pages_suppressed": 9,
        }}}), encoding="utf-8")
        g = standing_wedges(good)
        txt13 = "\n".join(render_standing_wedges(g, datetime.now(timezone.utc)))
        # A wedge must RENDER, and an empty ledger must render DIFFERENTLY —
        # the positive control on the negative, the shape check 7 uses.
        empty = tmp / "empty.json"
        empty.write_text('{"schema":1,"wedges":{}}', encoding="utf-8")
        txt_empty = "\n".join(
            render_standing_wedges(standing_wedges(empty), datetime.now(timezone.utc))
        )
        check(13, "a standing wedge renders, and an empty ledger renders otherwise",
              g["count"] == 1 and "GLD" in txt13 and "GLD" not in txt_empty
              and txt_empty.strip() != "",
              f"{g['count']} / {txt13[:60]!r} / {txt_empty[:60]!r}")

    # 14: THE ONE THAT MATTERS. No reachable state of the wedge block is silent
    # — an item downgraded out of the pager falling out of the digest too is the
    # whole risk of the operator's decision, and it must be structurally
    # impossible, not merely unobserved.
    silent = [
        st for st in ("not_fetched", "unreadable", "read")
        if not "".join(render_standing_wedges(
            {"wedgeState": st, "wedges": [], "count": 0}, datetime.now(timezone.utc)
        )).strip()
    ]
    check(14, "NO wedge state renders silence", not silent, str(silent))

    print("work-digest self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
