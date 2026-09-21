#!/usr/bin/env python3
"""A8 — one-time import of the archived registers into PIPELINE.jsonl.

Design of record: docs/plans/OPERATING-PLAN-2026-09-21.md § 3b, decision 3d.
Schema/validator: scripts/ops/pipeline.py.

WHAT THIS DOES
--------------
1. The 91 docs/archive/.../OPEN-ITEMS.json monitoring rows come across WHOLE
   (rule 1 — they already carry a `clears_when`, which is the one field the
   old system almost never had). `verified_at` maps to `due_when.last_checked`
   — 7 of 91 have none, which correctly leaves them immediately due rather
   than fabricating a checked date. `loud` and "never verified" are preserved
   as VISIBLE, distinguishable states (prefixed onto `what`), not collapsed.

2. The 1,065 open+kept_open rows across the four archived review backlogs are
   NOT bulk-imported. Decision 3d: "kill by default, promote by exception —
   no observation in 60 days and no named owner -> killed with a stated
   reason." Applied here as:

   - "last observation" = max(opened_at, every updates[].at) EXCLUDING the
     single bulk sweep authored '/system-review 2026-08-29 triage'. MEASURED:
     that one pass touched 430 of 563 items-with-updates (76%) without
     individualized re-verification — counting it would let one recency-bump
     reset every clock, which is exactly the "carried forward, no new
     evidence" anti-pattern CLAUDE-RULES-CANONICAL.md already names.
   - "named owner" = the `owner` field set to something other than the
     generic placeholders (unassigned/unowned/none/n/a/tbd/operator).
   - stale (no real observation in 60 days, no owner) -> killed,
     terminal_reason unworked_since_<date> (or _never if no date parses).
   - NOT stale, but no `resolution_criteria`/`why_it_matters` text to build a
     genuine `due_when.clears_when` from -> also killed
     (terminal_reason unworkable_no_resolution_criteria). Fabricating a
     clears_when from nothing would violate "never fabricate the reassuring
     value" — an unworkable row cannot be honestly promoted.
   - everything else -> promoted, `due_when.last_checked` set to TODAY
     (triaging it now IS this cycle's observation) with a severity-scaled
     `check_every_days`, so nothing from this import floods section 0 today —
     only the pre-existing due items do. Cadence establishes the NEXT check.

   origin.rerun for every migrated row points at a command that reprints the
   ORIGINAL archived record by id — the most honest "rerun" available for a
   legacy finding with no automated regenerating check of its own. It lets a
   successor re-read the full original evidence rather than trust a
   months-old sentence; it does not claim to re-execute a live probe.

Run: python3 scripts/ops/import_archived_registers.py [--dry-run]

# wiring: manual-only - a one-time A8 import, run once by hand this session
# against a store seeded empty; re-running it would re-append every id and
# violate the append-only log's one-current-record-per-id contract. Nothing
# should invoke this on a schedule or from a workflow.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pipeline  # noqa: E402

ARCHIVE = Path("docs/archive/2026-09-21-operating-reset/registers")
TODAY = date(2026, 9, 21)
BULK_SWEEP_AUTHOR = "/system-review 2026-08-29 triage"
GENERIC_OWNERS = {"unassigned", "unowned", "none", "n/a", "tbd", "operator", ""}

BACKLOGS = {
    "health-review-backlog.json": "health",
    "performance-review-backlog.json": "performance",
    "ml-review-backlog.json": "ml",
    "research-review-backlog.json": "research",
}

CLEARS_WHEN_MAXLEN = 1500


def _parse_date(s: object) -> date | None:
    if not isinstance(s, str) or not s.strip():
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _last_real_observation(item: dict) -> date | None:
    dates: list[date] = []
    d = _parse_date(item.get("opened_at"))
    if d:
        dates.append(d)
    for u in item.get("updates") or []:
        if not isinstance(u, dict):
            continue
        if (u.get("by") or "").strip() == BULK_SWEEP_AUTHOR:
            continue
        d = _parse_date(u.get("at") or u.get("date"))
        if d:
            dates.append(d)
    return max(dates) if dates else None


def _has_named_owner(item: dict) -> bool:
    o = item.get("owner")
    if not isinstance(o, str) or not o.strip():
        return False
    ol = o.strip().lower()
    if "unowned" in ol or "unassigned" in ol:
        return False
    return ol not in GENERIC_OWNERS


def _label(item: dict, item_id: str) -> str:
    """Not every row carries `title` (87/1065 across the four backlogs don't) —
    fall back to summary/detail rather than silently rendering the bare id."""
    for k in ("title", "summary", "detail"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return item_id


def _clears_when_text(item: dict) -> str | None:
    for k in ("resolution_criteria", "why_it_matters"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            t = v.strip()
            return t if len(t) <= CLEARS_WHEN_MAXLEN else t[:CLEARS_WHEN_MAXLEN] + " …[truncated]"
    return None


def _cadence_for_severity(severity: object) -> int:
    s = str(severity or "").lower()
    if "critical" in s:
        return 14
    if "high" in s:
        return 14
    if "medium" in s:
        return 30
    return 45  # low, missing, or an unrecognised spelling


def _next_action_for_tier(tier: object) -> str:
    t = str(tier or "").strip()
    if t.startswith("3") or "tier-3" in t.lower() or "tier 3" in t.lower():
        return "ask_operator"
    return "check_observation"


def _origin_ref_and_rerun(source_file: str, item_id: str) -> tuple[str, str]:
    path = f"docs/archive/2026-09-21-operating-reset/registers/{source_file}"
    ref = f"{path}#{item_id}"
    rerun = (
        "python3 -c \"import json; d=json.load(open('%s')); "
        "items=d['items'] if isinstance(d, dict) else d; "
        "print(json.dumps(next(i for i in items if i.get('id')=='%s'), indent=2))\""
        % (path, item_id)
    )
    return ref, rerun


def build_backlog_items() -> tuple[list[dict], list[dict], dict]:
    """Returns (promoted, killed, counts-by-source)."""
    promoted: list[dict] = []
    killed: list[dict] = []
    counts: dict[str, dict[str, int]] = {}

    for fname, tag in BACKLOGS.items():
        d = json.loads((ARCHIVE / fname).read_text())
        rows = [i for i in d["items"] if i.get("status") in ("open", "kept_open")]
        c = {"open": len(rows), "promote": 0, "kill_stale": 0, "kill_unworkable": 0}

        for it in rows:
            item_id = it["id"]
            last_obs = _last_real_observation(it)
            owner_named = _has_named_owner(it)
            stale = last_obs is None or ((TODAY - last_obs).days >= 60 and not owner_named)

            ref, rerun = _origin_ref_and_rerun(fname, item_id)
            origin = {"kind": "review", "ref": ref, "rerun": rerun}

            if stale:
                reason_date = last_obs.isoformat() if last_obs else "never"
                killed.append(
                    {
                        "id": item_id,
                        "what": _label(it, item_id),
                        "origin": origin,
                        "due_when": {"kind": "date", "due_date": TODAY.isoformat()},
                        "next_action": "check_observation",
                        "state": "killed",
                        "terminal_reason": f"unworked_since_{reason_date}",
                    }
                )
                c["kill_stale"] += 1
                continue

            clears_when = _clears_when_text(it)
            if not clears_when:
                killed.append(
                    {
                        "id": item_id,
                        "what": _label(it, item_id),
                        "origin": origin,
                        "due_when": {"kind": "date", "due_date": TODAY.isoformat()},
                        "next_action": "check_observation",
                        "state": "killed",
                        "terminal_reason": "unworkable_no_resolution_criteria",
                    }
                )
                c["kill_unworkable"] += 1
                continue

            promoted.append(
                {
                    "id": item_id,
                    "what": f"[{tag}] {_label(it, item_id)}",
                    "origin": origin,
                    "due_when": {
                        "kind": "observation",
                        "clears_when": clears_when,
                        "check_every_days": _cadence_for_severity(it.get("severity")),
                        "last_checked": TODAY.isoformat(),
                    },
                    "next_action": _next_action_for_tier(it.get("tier")),
                    "state": "queued",
                }
            )
            c["promote"] += 1

        counts[tag] = c

    return promoted, killed, counts


def build_open_items() -> list[dict]:
    d = json.loads((ARCHIVE / "OPEN-ITEMS.json").read_text())
    rows = d["items"] if isinstance(d, dict) else d
    out = []
    for it in rows:
        item_id = it["id"]
        loud = bool(it.get("loud"))
        verified_at = it.get("verified_at")
        never_verified = not (isinstance(verified_at, str) and verified_at.strip())
        tag = ("[LOUD] " if loud else "") + ("[NEVER VERIFIED] " if never_verified else "")
        every = it.get("check_every_days")
        if not (isinstance(every, int) and every >= 1):
            every = 7
        label = _label(it, item_id)

        ref, rerun = _origin_ref_and_rerun("OPEN-ITEMS.json", item_id)
        out.append(
            {
                "id": item_id,
                "what": f"{tag}{label}",
                "origin": {"kind": "audit", "ref": ref, "rerun": rerun},
                "due_when": {
                    "kind": "observation",
                    "clears_when": (it.get("clears_when") or "")[:CLEARS_WHEN_MAXLEN],
                    "check_every_days": every,
                    "last_checked": verified_at if not never_verified else None,
                },
                "next_action": "check_observation",
                "state": "queued",
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    promoted, killed, counts = build_backlog_items()
    open_items = build_open_items()

    print("=== backlog rows (1,065 open+kept_open) ===")
    total = {"open": 0, "promote": 0, "kill_stale": 0, "kill_unworkable": 0}
    for tag, c in counts.items():
        print(f"  {tag}: {c}")
        for k in total:
            total[k] += c[k]
    print(f"  TOTAL: {total}")
    print(f"=== OPEN-ITEMS monitoring rows: {len(open_items)} (whole, per rule 1) ===")

    if args.dry_run:
        print("\n--dry-run: nothing appended.")
        return 0

    n = 0
    for item in open_items + promoted + killed:
        pipeline.append(item)
        n += 1
    print(f"\nAppended {n} records to {pipeline.STORE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
