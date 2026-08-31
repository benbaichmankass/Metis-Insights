#!/usr/bin/env python3
"""SHORTLIST open backlog rows whose resolution criteria may ALREADY be met.

WHY (operator, 2026-08-31): *"we need to come up with some other ideas for how
we drain the backlog"* — and net-positive filing was explicitly ruled NOT a
failure; the missing piece is a workflow that drains at scale.

THE OBSERVATION THIS IS BUILT ON. Rows are filed by the session that finds the
defect and are closed by whoever later notices the criterion is satisfied — and
usually nobody does, because checking 476 rows by hand is not a thing anyone
does. On 2026-08-31 a session hand-checked four rows and closed **two**: one
whose read surface had shipped nine days earlier, one whose sibling route had
shipped six days earlier. Both had sat open purely because no one re-read them
against current reality.

⚠️ **THIS TOOL SHORTLISTS. IT NEVER DECIDES, AND IT MUST NEVER AUTO-CLOSE.**
A criterion is prose written by a human; matching a filename in it proves the
file exists, never that the defect is fixed. Its output is a QUEUE for a
session to verify one row at a time, with the tool's evidence as the starting
point rather than the verdict. Closing a row on this tool's say-so alone would
manufacture exactly the "reported done, never verified" state the backlog exists
to prevent.

WHAT IT CHECKS — deliberately only TWO signals, after the first version's
were MEASURED and found to be noise:
  * a diag `log_file` name in the criteria is now allowlisted in diag.py;
  * a path named in the criteria now exists in the repo.

⚠️ **THE FIRST VERSION SHORTLISTED 108 ROWS AND WAS WRONG, AND THAT RESULT IS
KEPT HERE ON PURPOSE.** It also matched bare identifiers and test names in the
criteria prose. A six-row sample was hand-checked and essentially NONE was
genuinely closable: a row about a stray DB on the trainer matched because the
word `signals` appears somewhere in the tree; a row needing a LIVE Bybit
verification matched because `tp_order_id` exists in code; a row about a
`bot_log` file missing ON DISK matched because the string appears in the
allowlist. `identifier_present` alone carried **75 of the 108**.

A shortlist that is mostly false is worse than no shortlist, because it
manufactures confident-looking work and a session that trusts it closes rows
that are not fixed. So those two signal kinds were REMOVED rather than
weighted. The remaining two are narrow and were the ones that actually found
the two real closes on 2026-08-31.

Everything else is `not_checkable` — the honest state, and by far the largest
bucket. A criterion demanding a live measurement cannot be settled from a
checkout, and pretending otherwise is the whole failure above.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKLOGS = {
    "health": ROOT / "docs/claude/health-review-backlog.json",
    "perf": ROOT / "docs/claude/performance-review-backlog.json",
    "ml": ROOT / "docs/claude/ml-review-backlog.json",
}
OPEN = {"open", "kept_open"}

_PATH = re.compile(r"`([a-zA-Z0-9_./-]+\.(?:py|json|yaml|yml|sh|md))`")
_IDENT = re.compile(r"`([a-z_][a-z0-9_]{6,})`")
_LOGNAME = re.compile(r"log_file\?name=([a-z0-9_]+)")
_TEST = re.compile(r"(test_[a-z0-9_]+)")

# States, never collapsed.
MET = "likely_met"          # every decidable signal now checks out
UNMET = "likely_unmet"      # at least one decidable signal does not
NONE = "not_checkable"      # nothing in the criteria is mechanically decidable


def _rows() -> list[tuple[str, dict[str, Any]]]:
    out = []
    for dom, path in BACKLOGS.items():
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        key = next(k for k, v in doc.items()
                   if isinstance(v, list) and v and isinstance(v[0], dict))
        for r in doc[key]:
            if r.get("status") in OPEN:
                out.append((dom, r))
    return out


def _grep(needle: str) -> bool:
    """Is *needle* present anywhere in the tracked tree?"""
    try:
        res = subprocess.run(
            ["git", "grep", "-l", "-F", needle], cwd=ROOT,
            capture_output=True, text=True, timeout=25,
        )
        return res.returncode == 0 and bool(res.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


def _is_shallow() -> bool:
    try:
        r = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                           cwd=ROOT, capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() == "true"
    except Exception:  # noqa: BLE001
        return True  # cannot establish it is deep -> assume we cannot look


_SHALLOW = _is_shallow()


def _added_after(rel: str, opened_at: str) -> bool | None:
    """Was *rel* first added to the tree AFTER the row was opened?

    This is the signal `path_exists` should have been. A criterion naming a file
    that already existed tells you nothing; one naming a file that has appeared
    SINCE the row was filed is real evidence that something shipped.

    ⚠️ **RETURNS ``None`` -- "we could not look" -- ON A SHALLOW CLONE**, which
    is what CI and every sandbox session actually has (measured here: 124
    commits, `is-shallow-repository` true). `git log --diff-filter=A` then
    reports the SHALLOW BOUNDARY rather than the true creation, so
    `docs/CLAUDE-RULES-CANONICAL.md` -- in the tree for months -- read as "added
    2026-08-26" and turned a row into a false candidate. Emitting that as a
    verdict is precisely the fabricated-measurement class this repo keeps
    paying for, so the signal declares itself unavailable instead.
    """
    if _SHALLOW:
        return None
    if not opened_at:
        return False
    try:
        res = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%cI", "-1", "--", rel],
            cwd=ROOT, capture_output=True, text=True, timeout=25,
        )
        first = (res.stdout or "").strip()
        return bool(first) and first > opened_at
    except Exception:  # noqa: BLE001
        return False


def assess(row: dict[str, Any], diag_src: str) -> dict[str, Any]:
    crit = str(row.get("resolution_criteria") or "")
    signals: list[dict[str, Any]] = []

    for m in set(_LOGNAME.findall(crit)):
        signals.append({"kind": "diag_log_name", "needle": m,
                        "ok": f'"{m}"' in diag_src})
    for m in set(_PATH.findall(crit)):
        # A path merely EXISTING proves nothing -- most criteria name files that
        # already existed when the row was filed (docs/CLAUDE-RULES-CANONICAL.md
        # is in dozens of them). The discriminating question is whether it was
        # CREATED AFTER the row was opened, i.e. something shipped since.
        exists = (ROOT / m).exists()
        added = _added_after(m, str(row.get("opened_at") or "")) if exists else False
        signals.append({"kind": "path_added_since_filing", "needle": m, "ok": added})
    decidable = [s for s in signals if s["ok"] is not None]
    if not decidable:
        # Either nothing was extractable, or every extracted signal is
        # unavailable in this checkout. Both mean: not shortlistable here.
        state = NONE
    elif all(s["ok"] for s in decidable):
        state = MET
    else:
        state = UNMET
    return {"state": state, "signals": signals}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", choices=[MET, UNMET, NONE], default=MET,
                    help="which bucket to list (default: the shortlist)")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    diag_src = (ROOT / "src/web/api/routers/diag.py").read_text(encoding="utf-8")
    rows = _rows()
    buckets: dict[str, list[Any]] = {MET: [], UNMET: [], NONE: []}
    for dom, r in rows:
        v = assess(r, diag_src)
        buckets[v["state"]].append((dom, r, v))

    if a.json:
        print(json.dumps({
            "open_total": len(rows),
            "counts": {k: len(v) for k, v in buckets.items()},
            "rows": [{"domain": d, "id": r["id"], "signals": v["signals"]}
                     for d, r, v in buckets[a.state][: a.limit]],
        }, indent=2))
        return 0

    print(f"OPEN rows scanned: {len(rows)}")
    for k in (MET, UNMET, NONE):
        print(f"  {k:16s} {len(buckets[k])}")
    print(f"\n--- {a.state} (showing up to {a.limit}) ---")
    print("VERIFY EACH ONE. This tool shortlists; it never decides.\n")
    for dom, r, v in buckets[a.state][: a.limit]:
        print(f"[{dom}] {r['id']}")
        print(f"    {str(r.get('title'))[:100]}")
        for s in v["signals"]:
            mark = "?? " if s["ok"] is None else ("OK " if s["ok"] else "NO ")
            print(f"      {mark}{s['kind']}: {s['needle']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
