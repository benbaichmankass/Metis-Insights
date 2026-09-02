#!/usr/bin/env python3
"""POST-MERGE RECONCILER for `docs/claude/work/OPEN-PRS.json`.

⚠️ WHY THIS RUNS OUTSIDE THE COMMIT IT IS RECONCILING — THE WHOLE POINT
----------------------------------------------------------------------
A commit cannot record its own merge. That single fact is what made the old
shape non-terminating, and it is why this is a workflow on `push: main` rather
than another thing a session remembers to do:

  * every open PR must have a row                    -> `unrecorded`, FAIL
  * a row naming a PR no longer open is stale        -> `stale_row`, FAIL
  * the PR that MAINTAINS the rows is itself open, so it needs a row
  * merging it makes THAT row stale seconds later

Measured 2026-09-02: #10775 merged (`d08cac48`) about ninety seconds after a
branch recorded a row for it. The handoff gate could therefore never read
`ready` on the merge that closed it, and the manager pruned-and-reopened in a
loop. Whatever writes a row's TERMINAL state has to run after the merge, from
outside it. That is this.

⚠️ IT MOVES ROWS. IT NEVER DELETES ONE
--------------------------------------
`open_prs[]` and `settled_prs[]` hold two different populations, and only the
first is graded against the live open list. The second is HISTORY, and it is
*more* load-bearing after the merge than before: #10746 carries a Tier-2
approval conditional on `bybit_1` (demo) ONLY, with real-money `bybit_2`
explicitly accepted as exposed. Under the old shape, satisfying the freshness
rule meant DELETING that. A record that destroys an operator decision to look
current is the same failure as the OPEN-ITEMS cap that evicted a valid row
(CLAUDE.md § "Every session").

⚠️ IT NEVER ADDS A ROW FOR AN UNRECORDED OPEN PR
------------------------------------------------
Structurally, not by policy: it iterates `open_prs[]` and asks GitHub about
those numbers. It never enumerates what is open. Only a SESSION knows a PR's
owner, intent and operator decision; a stub carrying none of them would
manufacture completeness nobody established, and would be byte-indistinguishable
from the real row it imitates. An open PR with no row must keep FAILING — that
finding is the check's entire reason to exist.

THREE STATES, NEVER COLLAPSED
-----------------------------
  ``reconciled``      at least one row's PR is no longer open; those rows were
                      MOVED to `settled_prs[]` and the record was rewritten.
  ``no_change``       every recorded row's PR is still open. Nothing to do.
                      ⚠️ Writes NOTHING — not even the liveness stamp. A stamp
                      would be a commit to `main`, which retriggers this
                      workflow, which stamps again: the stamp is deliberately
                      tied to a MOVE so the loop cannot start.
  ``could_not_look``  the GitHub API was unreachable, unauthenticated, or
                      answered non-200 for ANY row. ⚠️ **WE COULD NOT LOOK IS
                      NOT NOTHING HAD MERGED.** Nothing moves, nothing is
                      stamped, and the run does not report success.

⚠️ `could_not_look` IS ALL-OR-NOTHING, DELIBERATELY. One unreadable row fails
the whole pass rather than settling the rows that did answer. A partial pass
would stamp `last_reconciled_sha` — asserting "the reconciler has run against
this sha" — while an unknown row sat unreconciled, and that stamp is exactly
what `open_pr_record.grade_completeness` reads to tell *the reconciler is dead*
apart from *a session forgot to prune*. Half a look must not clear a liveness
signal.

Tier-1: reads the record and the GitHub API, writes one JSON file. No order
path, no config, no live-trading surface.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import open_pr_record as opr  # noqa: E402

RECORD_PATH = opr.RECORD_PATH

#: The reconcile verdict vocabulary. Collapsing `could_not_look` into
#: `no_change` is the dangerous direction — it reports an unlooked-at record as
#: a clean one — and that collapse is mutation-tested in
#: tests/ops/test_reconcile_open_prs.py rather than merely asserted.
#:
#: ⚠️ DELIBERATELY **NOT** REGISTERED WITH `collapsed-state-guard`, AND THE
#: REASON IS THE GUARD'S OWN. It was trial-registered and MEASURED during
#: MI-57: the guard reported `ok ... 1 consumer(s), all states read`, and the
#: single credited consumer was `tests/ops/test_reconcile_open_prs.py` — the
#: test written alongside this module. The producer file is excluded from the
#: consumer scan by design, the workflow is YAML (the scan is `*.py`), and no
#: other module branches on these states, so the contract would have been
#: satisfied by its own test. That is the 2026-08-31
#: registry-self-satisfaction shape one level over
#: (BL-20260831-COLLAPSED-STATE-GUARD-SATISFIED-ITSELF-SO-ITS-CENTRAL-CHECK-WAS-VACUOUS),
#: and the only ways to make it pass honestly would be to count a test as a
#: consumer or to invent a consumer module — the guard's own docstring calls
#: that "writing worse code to satisfy a guard".
#:
#: ⚠️ SO DO NOT REGISTER THIS LATER WITHOUT FIRST ADDING A REAL CONSUMER. The
#: states are genuinely three and genuinely uncollapsed; what is missing is a
#: second module that BRANCHES on them. If one ever reads this verdict (a duty
#: pass, a digest, a handoff check), register it then — a green guard line
#: bought with a decorative entry is worth less than this comment.
RECONCILE_STATES_REGISTERED_WITH_GUARD = False
RECONCILED = "reconciled"
NO_CHANGE = "no_change"
COULD_NOT_LOOK = "could_not_look"
RECONCILE_STATES = (RECONCILED, NO_CHANGE, COULD_NOT_LOOK)

API = "https://api.github.com"


class LookupFailure(Exception):
    """A PR's state could not be READ. Never raised to mean 'not open'."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# The observation. Isolated behind a callable so the pure core below is
# testable without a network and without a token.
# --------------------------------------------------------------------------- #
def github_fetch(repo: str, token: Optional[str]) -> Callable[[int], Dict[str, Any]]:
    """Return a fetcher for one PR. Any non-200 raises `LookupFailure`.

    ⚠️ A 404 raises too. A PR number that 404s is not a merged PR — it is a
    number this token cannot see, and treating "invisible" as "settled" would
    move a row out of the graded population on no evidence at all.
    """
    def fetch(pr: int) -> Dict[str, Any]:
        req = urllib.request.Request(f"{API}/repos/{repo}/pulls/{pr}")
        req.add_header("Accept", "application/vnd.github+json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status != 200:
                    raise LookupFailure(f"#{pr}: HTTP {resp.status}")
                return json.loads(resp.read().decode("utf-8"))
        except LookupFailure:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, OSError,
                json.JSONDecodeError, ValueError) as exc:
            raise LookupFailure(f"#{pr}: {type(exc).__name__}: {exc}") from exc
    return fetch


def observe(prs: List[int], fetch: Callable[[int], Dict[str, Any]]
            ) -> Tuple[Optional[Dict[int, Dict[str, Any]]], Optional[str]]:
    """Look up every row's PR. ALL of them, or none.

    Returns ``(observations, None)`` or ``(None, reason)``. The second is
    `could_not_look` — see the module docstring for why one bad row fails the
    whole pass rather than settling the rest.
    """
    out: Dict[int, Dict[str, Any]] = {}
    for pr in prs:
        try:
            out[pr] = fetch(pr)
        except LookupFailure as exc:
            return None, str(exc)
        except Exception as exc:  # noqa: BLE001 - a surprise is still a non-look
            return None, f"#{pr}: unexpected {type(exc).__name__}: {exc}"
    return out, None


def terminal_of(payload: Dict[str, Any]) -> Optional[str]:
    """`merged` | `closed_unmerged` | None (still open).

    ⚠️ Reads `merged_at`, not `merged`. GitHub's `merged` boolean is absent from
    some list-shaped payloads, and a missing boolean read as False would file a
    merged PR under `closed_unmerged` — where the new disposition check would
    then demand a reason for an abandonment that never happened.
    """
    if payload.get("state") == "open":
        return None
    return "merged" if payload.get("merged_at") else "closed_unmerged"


# --------------------------------------------------------------------------- #
# The pure core.
# --------------------------------------------------------------------------- #
def reconcile(doc: Any, observations: Optional[Dict[int, Dict[str, Any]]],
              reason: Optional[str] = None,
              head_sha: Optional[str] = None) -> Dict[str, Any]:
    """Move every settled row out of `open_prs[]`. PURE — no clock beyond
    `_now()`, no network, no file. Returns a verdict dict; `doc` is untouched.
    """
    if observations is None:
        return {"state": COULD_NOT_LOOK, "moved": [], "doc": doc,
                "message": (
                    f"the GitHub API could not be read ({reason or 'no reason given'}). "
                    f"NOTHING was moved and no liveness stamp was written. ⚠️ WE "
                    f"COULD NOT LOOK is not 'nothing had merged' — a row may well "
                    f"be settled and this pass cannot say.")}
    if not isinstance(doc, dict):
        return {"state": COULD_NOT_LOOK, "moved": [], "doc": doc,
                "message": ("OPEN-PRS.json is not a JSON object, so no row could "
                            "be reconciled. WE COULD NOT LOOK.")}

    open_rows = [r for r in doc.get("open_prs") or [] if isinstance(r, dict)]
    settled = [r for r in doc.get("settled_prs") or [] if isinstance(r, dict)]

    keep, moved = [], []
    for row in open_rows:
        pr = row.get("pr")
        payload = observations.get(pr) if isinstance(pr, int) else None
        term = terminal_of(payload) if payload else None
        if payload is None or term is None:
            keep.append(row)
            continue
        # A MOVE: every field the session wrote survives verbatim. The terminal
        # stamp is added alongside it, never in place of it.
        settled_row = dict(row)
        settled_row.update({
            "terminal": term,
            "merge_sha": payload.get("merge_commit_sha") if term == "merged" else None,
            "observed_at": _now(),
            "settled_by": "reconciler",
        })
        moved.append(settled_row)
        settled.append(settled_row)

    if not moved:
        return {"state": NO_CHANGE, "moved": [], "doc": doc,
                "message": (f"all {len(open_rows)} recorded row(s) name a PR that is "
                            f"still open. Nothing to move, and nothing written — the "
                            f"liveness stamp is tied to a MOVE so a no-op pass cannot "
                            f"commit to main and retrigger itself.")}

    new = dict(doc)
    new["open_prs"] = keep
    new["settled_prs"] = settled
    new["last_reconciled_at"] = _now()
    new["last_reconciled_sha"] = head_sha
    return {"state": RECONCILED, "moved": moved, "doc": new,
            "message": (f"moved {len(moved)} row(s) to settled_prs[]: "
                        f"{', '.join('#%s (%s)' % (r['pr'], r['terminal']) for r in moved)}. "
                        f"{len(keep)} row(s) remain in flight. No row was deleted and no "
                        f"row was invented.")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                    help="owner/name. Defaults to $GITHUB_REPOSITORY.")
    ap.add_argument("--head-sha", default=os.environ.get("GITHUB_SHA"),
                    help="the main sha this pass observed; stamped as "
                         "`last_reconciled_sha` on a MOVE.")
    ap.add_argument("--apply", action="store_true",
                    help="write the record. Without it this is a dry run.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    doc, readable = opr.read_record()
    if not readable:
        print("::error::reconcile-open-prs: OPEN-PRS.json could not be parsed. "
              "WE COULD NOT LOOK — this is not evidence that nothing had merged.")
        print(f"reconcile-open-prs: state={COULD_NOT_LOOK}")
        return 4

    prs = [r.get("pr") for r in (doc.get("open_prs") or [])
           if isinstance(r, dict) and isinstance(r.get("pr"), int)]
    if not a.repo:
        obs, reason = None, "no --repo / $GITHUB_REPOSITORY, so nothing was queried"
    else:
        obs, reason = observe(prs, github_fetch(
            a.repo, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")))

    v = reconcile(doc, obs, reason, head_sha=a.head_sha)
    print(f"reconcile-open-prs: state={v['state']} — {v['message']}")
    if a.json:
        print(json.dumps({k: v[k] for k in ("state", "message", "moved")},
                         indent=2, ensure_ascii=False))

    if v["state"] == COULD_NOT_LOOK:
        print("::error::reconcile-open-prs: REFUSED. Nothing was moved and no "
              "liveness stamp was written.")
        return 4
    if v["state"] == RECONCILED and a.apply:
        RECORD_PATH.write_text(
            json.dumps(v["doc"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"reconcile-open-prs: wrote {RECORD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
