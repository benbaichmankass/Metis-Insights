#!/usr/bin/env python3
"""A manager commit that does a worker's item FAILS — named by commit and path.

WHY THIS EXISTS, AND WHY IT IS NOT ANOTHER PARAGRAPH
----------------------------------------------------
`CLAUDE.md` has said this since 2026-09-01, verbatim:

    "the manager session only manages and does not take even small items,
     because that means they're not focused on what they're actually supposed
     to be doing."

The 2026-09-03 day manager read that line at session start and the operator
caught it doing items the same morning. So the prose is not the missing piece.
Adding emphasis to a rule that was read and disobeyed is the non-fix this repo
has now paid for three times — MI-15 twice on the sub-session registry, and
`BL-20260903-MANAGER-CHECKLIST-GOES-STALE-SILENTLY`. A reminder is not a
mechanism. This is the mechanism.

WHO IS "THE MANAGER" — DERIVED, NEVER DECLARED
-----------------------------------------------
The tempting implementations both fail on measurement, so neither is used:

  * **Branch name.** `origin/claude/risk-manager-backstop` matches every
    sensible `*manager*` pattern and is a WORKER branch about a risk-manager
    component (`src/runtime/order_monitor.py`, `src/units/accounts/risk.py`).
    Conversely `origin/claude/openprs-prune-merged-rows` carries no manager
    token at all and its first commit is `lease: heartbeat (manager sweep
    08:12Z)`. The name is not the role. (Population: 18 of 252 remote branches
    with a merge-base against `main` match `manager|workflow-overhaul`; 1 is a
    false positive and at least 1 manager branch is missed.)

  * **Writing the manager's registers.** Measured across all 252 branches, 30
    write one of the four register files. But 8 are `automation/reconcile-open-
    prs-*` bot branches touching `OPEN-PRS.json`, and `claude/mi94-register-id-
    uniqueness`, `claude/openprs-settled-reconciler` and
    `claude/subsession-registry-coupling-handoff-check` are WORKER sessions
    whose subject matter IS the registry. `OPEN-PRS.json` and `SESSIONS.json`
    are not manager-exclusive. `MANAGER-CHECKLIST.json` has at least one false
    positive from a merge union (`claude/system-actions-dispatch-unbound-var`).

What actually identifies the manager is the thing that MAKES it the manager:
**it holds the lease.** `docs/claude/work/MANAGER-LEASE.json` names the holder,
it must be pushed to be worth anything ("A CLAIM YOU DID NOT PUSH PROTECTS
NOTHING"), and its git history is therefore a complete, tamper-evident roster of
every session that has ever managed. Measured: **3 sessions across 59 revisions
of that file** (`session_011JWFxuYAaEQKCFCmG6gnHJ`,
`session_01Nopk1HcpvWBSEbZxEmALkd`, `session_01AYPxs3aDHwv3XBLRF4oK15`).

Commits carry `Claude-Session: https://claude.ai/code/session_…` trailers by
standing attribution rule. So the join is exact: a commit whose trailer names a
session that has held the lease is a commit the manager wrote.

⚠️ THIS IS WHY THE CHECK IS PER-COMMIT AND NOT PER-BRANCH-DIFF. The accused acts
that hurt most — *"resolved merge conflicts on #10893 and #10888, other sessions'
PRs"* — do not appear in the manager's own PR at all. They are manager commits
pushed onto a WORKER's branch, and a guard that only graded the manager's own
net diff would be structurally blind to exactly the failure it was built for.
Walking commits sees them wherever they were pushed.

⚠️ AND ON A MERGE COMMIT IT GRADES THE COMBINED DIFF (`git show -c`), which
lists only files that differ from BOTH parents — i.e. precisely the paths the
merger RESOLVED BY HAND, not the thousands a merge sweeps along. Resolving a
register conflict on the manager's own branch is in-surface and passes for free;
resolving a conflict in `src/` on someone else's PR is a hand-authored change to
a worker path and fails. One rule, no special case.

THE SURFACE IS AN ALLOWLIST, AND THAT POLARITY IS DELIBERATE
-------------------------------------------------------------
`MANAGER_SURFACE` is derived from what the three lease-holding sessions actually
committed (population: 263 commits carrying their trailers — 221 / 24 / 18).
A path it does not recognise is **not** thereby a worker item; it is a path this
guard cannot vouch for, and the manager must either route it to a session or
argue it in the exception file. Same reasoning `check_pr_landing.py` records for
`TIER1_SURFACE`: an unrecognised value must not arm anything.

The measurement also shows the guard has real work to do. `session_011JWFxu…`
(2026-09-02 day manager, 221 commits) committed `src/runtime/telegram_decisions.py`
(10 commits), `scripts/ci/check_collapsed_states.py` (10),
`tests/test_telegram_decisions.py` (8), `src/web/api/routers/diag.py` (8),
`src/bot/telegram_query_bot.py` (8), `scripts/install_systemd_units.sh` (3).
That is a manager building software, at scale, for a day.

⚠️ AND THE HONEST OTHER HALF: `session_01Nopk1HcpvWBSEbZxEmALkd` (2026-09-03 day
manager, 24 commits) committed **nothing** outside this surface — no `src/`, no
`tests/`, no `scripts/`. Its item-work was real but landed as REVIEW and CONFLICT
RESOLUTION on other sessions' branches and as backlog authoring, not as source
commits of its own. So this guard would not have blocked its morning wholesale,
and saying otherwise would be a claim the measurement does not support. What it
would have caught is stated per-action in this PR's body.

THE THREE CALLS THAT ARE JUDGEMENT, ARGUED RATHER THAN ASSUMED
---------------------------------------------------------------
**1. Merging a PR is management, and nothing here can block it.** A merge is a
GitHub action, not a commit this guard grades. Recording an operator decision is
likewise in-surface (`docs/claude/work/**`). Both are named here so a later
reader does not "tighten" the surface into breaking them.

**2. `CLAUDE.md` is BOTH, so it is graded by HUNK, not by path.** The file
carries a generated block between `<!-- SESSION-BRIEF:BEGIN -->` and
`<!-- SESSION-BRIEF:END -->` that `render_session_brief.py` writes from the
registers — rendering it is management. The other ~1,400 lines are the canonical
prose, and rewriting those is authoring. A manager diff confined to the brief
block passes; one that touches prose outside it does not. (The 2026-09-02
manager touched `CLAUDE.md` in 29 commits, which is why this cannot be a plain
allow.)

**3. FILING A BACKLOG ROW IS MANAGEMENT AND STAYS PERMITTED. This is the
genuinely contested one and it is decided, not defaulted.**
`docs/claude/health-review-backlog.json` is on the surface — unconditionally,
with no row cap.

    FOR: the repo's "if you see something, say something" rule *requires* the
    manager to file what it notices, and a backlog row is the artifact that
    ROUTES work to a session. Filing is the opposite of taking the item — it is
    the act of not taking it. A cap would mean a manager that noticed a fifth
    defect must either stay silent or spawn a session to write down a thing it
    already knows, and suppressed reporting is a worse failure than over-filing.
    `backlog_append.py` exists precisely to make filing one cheap row.

    AGAINST, and it is not nothing: the four rows the 2026-09-03 manager wrote
    carried full resolution criteria, and that is real authoring effort — hours
    that were not spent supervising. The operator's complaint covers it.

    WHY THE PERMISSIVE READING WINS ANYWAY: the cost being complained about is
    ATTENTION, and attention is not what a path allowlist measures. A row cap
    would be a proxy so loose it would mostly catch diligent filing while
    missing a manager that spent the same hours reading source. The attention
    cost is addressed by R6 below, which measures supervision directly. Filing
    is left alone on purpose, and R6 is where the manager's time actually gets
    graded.

    WHAT WOULD CHANGE THIS: evidence that backlog-authoring volume correlates
    with supervision lapses. That is measurable — rows filed per session against
    that session's R6 gaps — and this session did not have enough manager-days
    to measure it (n=3 sessions). Filed rather than guessed.

R6 — THE POSITIVE DUTY, AND ONLY BECAUSE IT CAN BE MEASURED HONESTLY
---------------------------------------------------------------------
A guard that only forbids leaves a manager idle-but-compliant, which is not what
the operator asked for. So: a commit that advances the lease heartbeat — an
affirmative claim "I am still managing" — must be accompanied by a sub-session
registry no staler than the lease it is holding.

The threshold is the lease's OWN `ttl_minutes` (90), read from the file rather
than invented, and it is loose on purpose. Measured over **50 lease revisions
where `state == held` and both timestamps are readable**: median gap 18 min,
p75 37 min, p90 66 min, max 480 min. A 30-minute threshold (the lease's
`heartbeat_target_minutes`) would fail **14 of 50** — 28% of every heartbeat this
repo has ever written — and a guard that reds a quarter of legitimate traffic is
a guard that gets deleted rather than fixed (`check_pr_queue_watch.py` records
that exact reasoning). At the TTL it fails **2 of 50**. Those two are the real
signal: a manager holding a lease longer than its own supervision record.

⚠️ WHAT R6 MEASURES IS THE RECORD OF SUPERVISION, NOT SUPERVISION. A manager
who unblocked three sessions over Telegram and wrote nothing down grades stale,
and that reading is deliberate — the registry rule already requires the writing,
because a successor arriving cold can only pick up what the file names. But it
is NOT a measure of whether help was given, and this guard must never be cited
as evidence that it was.

⚠️ AND R6 IS NOT A MEASURE OF ATTENTION EITHER. It fires on a stale registry,
not on a busy manager. The thing the operator actually described — two turns
ended with a sub-session blocked for 46 minutes — is a LIVE fact about session
state that CI cannot read (no MCP tools), and `scripts/ops/manager_view.py`
(MI-89) is where that lives. This is the offline half. Partial by construction,
and strictly more than the zero that existed before.

THE ESCAPE HATCH IS VISIBLE AND ARGUED, NEVER A FLAG
-----------------------------------------------------
An incident on the live trading system is not "an item", and a manager must be
able to touch the order path at 3am without waiting for a spawn. So
`docs/claude/work/manager-scope-exception.yaml` follows the
`spawn-priority-exception.yaml` pattern exactly: each entry NAMES the paths, is
DATED, carries a real reason, and EXPIRES. A `pending` status REFUSES — an
unfilled template grants nothing. There is no bypass flag and no environment
variable, because the lesson of `new-table-wiring-guard` is that a guard cheaper
to lie to than to satisfy is worse than no guard: the cheapest way to silence
this one must be to route the work to a session, which is the behaviour wanted.

STATES, NEVER COLLAPSED
-----------------------
  ``not_a_pr``               — no base ref to diff against. Nothing was graded.
                               NOT a pass.
  ``predates_guard``         — the branch was cut before this guard existed and
                               could not have known. Passes, counted, loud.
  ``no_manager_commits``     — no commit in range is attributable to a session
                               that has held the lease. The ordinary worker PR.
  ``unattributed``           — commits carry NO `Claude-Session:` trailer, so
                               authorship could not be established. **We did not
                               look is not the same as compliant**: reported
                               loudly on every run and never silently passed.
  ``clean``                  — manager commits found, all inside the surface.
  ``violation``              — a manager commit touched a worker path. FAILS.

Run standalone with ``--base origin/main``, or ``--self-test`` to plant each
defect in a throwaway repo and prove the guard fails on it.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]

GUARD_REL = "scripts/ci/check_manager_scope.py"
LEASE_REL = "docs/claude/work/MANAGER-LEASE.json"
SESSIONS_REL = "docs/claude/work/SESSIONS.json"
EXCEPTION_REL = "docs/claude/work/manager-scope-exception.yaml"

BRIEF_BEGIN = "<!-- SESSION-BRIEF:BEGIN"
BRIEF_END = "<!-- SESSION-BRIEF:END"

SESSION_TRAILER = re.compile(r"Claude-Session:\s*\S*?(session_[A-Za-z0-9]+)")

# Derived from what the three lease-holding sessions actually committed
# (population: 263 commits carrying their trailers). An ALLOWLIST — see the
# module docstring for why this polarity rather than a denylist of worker paths.
MANAGER_SURFACE = [
    # The manager's own instruments and the work store it manages through.
    "docs/claude/work/**",
    "docs/claude/OPEN-ITEMS.json",
    "docs/claude/CYCLE-PRIORITY.json",
    "docs/claude/pending-pings.jsonl",
    # Filing what it notices. Permitted unconditionally — see call (3) above.
    "docs/claude/health-review-backlog.json",
    # Landing and spawning: declaring, arming, relaying. Management verbs.
    ".github/pr-landing/**",
    ".github/pr-automerge-requests/**",
    "automation/**",
    # The operator-facing brief.
    "comms/**",
    # CLAUDE.md is NOT here. It is graded by hunk — see _claude_md_outside_brief.
]

# Named so a failure can say WHY a path is a worker item rather than only "not
# recognised", and so that widening MANAGER_SURFACE by mistake still trips a
# named check. These are the paths the 2026-09-02 manager actually committed.
WORKER_PATHS = {
    "src/**": "application source",
    "tests/**": "test authoring",
    "scripts/ci/**": "guard/CI logic",
    "scripts/ops/**": "ops tooling",
    "scripts/research/**": "research tooling",
    "scripts/reports/**": "report tooling",
    "ml/**": "model code",
    "config/**": "runtime configuration",
    "deploy/**": "deployment units",
    ".github/workflows/**": "CI workflow logic",
}

MIN_REASON = 30


def _match(path: str, globs) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
        if g.endswith("/**") and (path == g[:-3] or path.startswith(g[:-2])):
            return True
    return False


def _worker_label(path: str) -> Optional[str]:
    for g, label in WORKER_PATHS.items():
        if _match(path, [g]):
            return label
    return None


def _git(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


# --------------------------------------------------------------------------
# Identity: who has ever held the lease?
# --------------------------------------------------------------------------
def lease_holders(root: Path) -> set[str]:
    """Every session id that has ever appeared as holder of the manager lease.

    Read from the git history of the lease file, not from its current contents:
    a manager that stood down hours ago is still a manager for the commits it
    made while holding it. `previous_holder` is included because a release
    records the outgoing holder there and nowhere else.
    """
    holders: set[str] = set()
    rc, out = _git(root, "log", "--all", "--pretty=%H", "--", LEASE_REL)
    if rc != 0:
        return holders
    for sha in out.splitlines():
        rc2, blob = _git(root, "show", f"{sha.strip()}:{LEASE_REL}")
        if rc2 != 0:
            continue
        try:
            d = json.loads(blob)
        except Exception:
            continue          # an unreadable revision is skipped, not fatal
        for key in ("holder", "previous_holder"):
            v = d.get(key)
            if isinstance(v, str) and v.startswith("session_"):
                holders.add(v)
    return holders


def commit_session(root: Path, sha: str) -> Optional[str]:
    """The session id in the commit's `Claude-Session:` trailer, if any."""
    rc, body = _git(root, "log", "-1", "--pretty=%B", sha)
    if rc != 0:
        return None
    m = SESSION_TRAILER.findall(body)
    return m[-1] if m else None


def commit_paths(root: Path, sha: str) -> list[str]:
    """Paths this commit AUTHORED.

    On a merge commit this is the COMBINED diff (`-c`): only files differing
    from both parents, i.e. exactly what the merger resolved by hand. A merge
    that took one side wholesale authored nothing and lists nothing.
    """
    rc, parents = _git(root, "log", "-1", "--pretty=%P", sha)
    is_merge = rc == 0 and len(parents.split()) > 1
    args = ["show", "--name-only", "--pretty=", sha]
    if is_merge:
        args.insert(1, "-c")
    rc, out = _git(root, *args)
    if rc != 0:
        return []
    return sorted({ln.strip() for ln in out.splitlines() if ln.strip()})


def _claude_md_outside_brief(root: Path, sha: str) -> bool:
    """Did this commit change CLAUDE.md OUTSIDE the generated SESSION-BRIEF block?

    Grades the hunk headers of the post-image, so a diff confined to the
    rendered block passes and a prose edit does not. If the markers cannot be
    found the answer is True — an ungradeable CLAUDE.md edit is treated as
    prose, because failing closed here costs one exception line and failing open
    hands the manager the whole canonical doc.
    """
    rc, text = _git(root, "show", f"{sha}:CLAUDE.md")
    if rc != 0:
        return True
    lines = text.splitlines()
    begin = end = None
    for i, ln in enumerate(lines, start=1):
        if BRIEF_BEGIN in ln and begin is None:
            begin = i
        if BRIEF_END in ln:
            end = i
    if begin is None or end is None or end <= begin:
        return True

    rc, diff = _git(root, "show", "--unified=0", "--pretty=", sha, "--", "CLAUDE.md")
    if rc != 0:
        return True
    touched_outside = False
    for ln in diff.splitlines():
        m = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", ln)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2) or "1")
        # A pure deletion (count 0) anchors at `start`; grade that line.
        lo, hi = start, start + max(count, 1) - 1
        if lo < begin or hi > end:
            touched_outside = True
    return touched_outside


# --------------------------------------------------------------------------
# The exception file — verified, never presence-only
# --------------------------------------------------------------------------
def load_exceptions(root: Path, today: Optional[str] = None) -> tuple[list[dict], list[str]]:
    """Return (active exceptions, notes). A malformed entry grants nothing."""
    notes: list[str] = []
    p = root / EXCEPTION_REL
    if not p.exists():
        return [], notes
    try:
        import yaml
    except ImportError:
        return [], ["PyYAML absent — exception file could not be read, so NO "
                    "exception was applied (we did not look; not a pass)"]
    try:
        doc = yaml.safe_load(p.read_text()) or {}
    except Exception as exc:
        return [], [f"{EXCEPTION_REL} is unreadable ({exc}) — no exception applied"]

    entries = doc.get("exceptions") or []
    if not isinstance(entries, list):
        return [], [f"{EXCEPTION_REL}: `exceptions` is not a list — none applied"]

    now = today or datetime.now(timezone.utc).date().isoformat()
    active: list[dict] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            notes.append(f"exception[{i}] is not a mapping — ignored")
            continue
        name = e.get("id") or f"exception[{i}]"
        status = str(e.get("status") or "").strip()
        if status != "active":
            # `pending` is the template's own default and REFUSES, by design.
            notes.append(f"{name}: status={status!r} — grants nothing "
                         f"(only 'active' does)")
            continue
        paths = e.get("paths")
        if not isinstance(paths, list) or not paths or \
           not all(isinstance(x, str) and x.strip() for x in paths):
            notes.append(f"{name}: `paths` must be a non-empty list of globs "
                         f"— grants nothing")
            continue
        reason = str(e.get("reason") or "").strip()
        if len(reason) < MIN_REASON:
            notes.append(f"{name}: `reason` must be real text (>= {MIN_REASON} "
                         f"chars, got {len(reason)}) — grants nothing")
            continue
        expires = str(e.get("expires") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires):
            notes.append(f"{name}: `expires` must be YYYY-MM-DD — grants nothing")
            continue
        if expires < now:
            notes.append(f"{name}: EXPIRED {expires} (today {now}) — grants nothing")
            continue
        active.append({"id": name, "paths": paths, "reason": reason,
                       "expires": expires})
    return active, notes


# --------------------------------------------------------------------------
# R6 — supervision freshness
# --------------------------------------------------------------------------
def _parse_ts(v) -> Optional[datetime]:
    """Parse an ISO timestamp to a UTC-AWARE datetime.

    ⚠️ A naive timestamp is ASSUMED UTC rather than rejected. Measured: real
    rows in both files carry a mix — most end in `Z`, some carry no offset at
    all — and subtracting a naive from an aware one raises, which would have
    turned an honest staleness reading into a crash on live history. Assuming
    UTC is right for every row this repo writes (`datetime.now(timezone.utc)`)
    and is at worst hours-wrong on a hand-typed one, which the 90-minute TTL
    absorbs in the permissive direction.
    """
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def supervision_gap(root: Path, sha: str) -> tuple[Optional[int], Optional[int], list[str]]:
    """(gap_minutes, ttl_minutes, notes) at this commit, or (None, None, why-not).

    Only meaningful on a commit that ADVANCES `heartbeat_at` — that is the
    affirmative claim "I am still managing". A commit that leaves the heartbeat
    alone makes no such claim and is not graded here.
    """
    notes: list[str] = []
    rc, blob = _git(root, "show", f"{sha}:{LEASE_REL}")
    if rc != 0:
        return None, None, notes
    try:
        lease = json.loads(blob)
    except Exception:
        return None, None, [f"{LEASE_REL} unreadable at {sha[:8]} — not graded"]
    if lease.get("state") != "held":
        return None, None, notes

    rc, prev = _git(root, "show", f"{sha}~1:{LEASE_REL}")
    if rc == 0:
        try:
            if json.loads(prev).get("heartbeat_at") == lease.get("heartbeat_at"):
                return None, None, notes      # heartbeat not advanced here
        except Exception:
            pass

    hb = _parse_ts(lease.get("heartbeat_at"))
    ttl = lease.get("ttl_minutes")
    if hb is None or not isinstance(ttl, int):
        return None, None, [f"lease at {sha[:8]} lacks a readable heartbeat_at/"
                            f"ttl_minutes — NOT graded (we could not look)"]

    rc, sblob = _git(root, "show", f"{sha}:{SESSIONS_REL}")
    if rc != 0:
        return None, None, [f"{SESSIONS_REL} absent at {sha[:8]} — not graded"]
    try:
        up = _parse_ts(json.loads(sblob).get("updated_at"))
    except Exception:
        up = None
    if up is None:
        return None, None, [f"{SESSIONS_REL} has no readable updated_at at "
                            f"{sha[:8]} — NOT graded (we could not look)"]
    return int((hb - up).total_seconds() // 60), ttl, notes


# --------------------------------------------------------------------------
def guard_existed_at_merge_base(root: Path, base: str) -> Optional[bool]:
    rc, mb = _git(root, "merge-base", base, "HEAD")
    if rc != 0 or not mb:
        return None
    rc, _ = _git(root, "cat-file", "-e", f"{mb}:{GUARD_REL}")
    return rc == 0


def check(root: Path, base: str, today: Optional[str] = None):
    """Return (state, failures, notes)."""
    fails: list[str] = []
    notes: list[str] = []

    rc, mb = _git(root, "merge-base", base, "HEAD")
    if rc != 0 or not mb:
        return ("not_a_pr", [],
                [f"could not merge-base against {base} — NOTHING was checked "
                 f"here; this is not a pass"])

    rc, out = _git(root, "rev-list", f"{mb}..HEAD")
    shas = [s for s in out.splitlines() if s.strip()]
    if not shas:
        return ("not_a_pr", [], [f"no commits in {base}..HEAD — nothing graded"])

    holders = lease_holders(root)
    if not holders:
        return ("unattributed", [],
                [f"no lease holder could be derived from {LEASE_REL}'s history "
                 f"— authorship could NOT be established, so nothing was "
                 f"graded. This is 'we did not look', not 'compliant'."])
    notes.append(f"lease-holder roster (population: all revisions of "
                 f"{LEASE_REL}): {len(holders)} session(s)")

    active, exc_notes = load_exceptions(root, today=today)
    notes.extend(exc_notes)
    for e in active:
        notes.append(f"ACTIVE EXCEPTION {e['id']} until {e['expires']}: "
                     f"{', '.join(e['paths'])}")

    graded = 0
    untrailered = 0
    for sha in shas:
        sess = commit_session(root, sha)
        if sess is None:
            untrailered += 1
            continue
        if sess not in holders:
            continue
        graded += 1
        rc, subject = _git(root, "log", "-1", "--pretty=%s", sha)
        short = f"{sha[:8]} ({sess}) {subject[:72]}"

        for path in commit_paths(root, sha):
            if path == "CLAUDE.md":
                if _claude_md_outside_brief(root, sha):
                    if _match(path, [p for e in active for p in e["paths"]]):
                        notes.append(f"EXCEPTED: {short} -> CLAUDE.md prose")
                        continue
                    fails.append(
                        f"R2 {short}\n"
                        f"      -> CLAUDE.md, OUTSIDE the generated "
                        f"SESSION-BRIEF block. Rendering the brief is "
                        f"management; rewriting the canonical prose is "
                        f"authoring, and authoring is a session's job.")
                continue

            if _match(path, MANAGER_SURFACE):
                continue
            if _match(path, [p for e in active for p in e["paths"]]):
                notes.append(f"EXCEPTED: {short} -> {path}")
                continue

            label = _worker_label(path)
            why = (f"{path} is {label}" if label else
                   f"{path} is not on the manager surface this guard can "
                   f"vouch for")
            fails.append(
                f"R2 {short}\n"
                f"      -> {why}. A manager session that writes this is doing "
                f"an item instead of managing. Route it to a session, or "
                f"argue it in {EXCEPTION_REL}.")

        gap, ttl, gnotes = supervision_gap(root, sha)
        notes.extend(gnotes)
        if gap is not None and ttl is not None:
            if gap > ttl:
                fails.append(
                    f"R6 {short}\n"
                    f"      -> this commit advances the lease heartbeat — the "
                    f"claim 'I am still managing' — but {SESSIONS_REL} was last "
                    f"updated {gap} minutes before it, longer than the lease's "
                    f"own ttl_minutes ({ttl}). A manager holding a lease longer "
                    f"than its own supervision record is holding a lease it is "
                    f"not using. NOTE this measures the RECORD of supervision, "
                    f"never supervision itself.")
            else:
                notes.append(f"R6 ok: {sha[:8]} registry {gap}min before "
                             f"heartbeat (ttl {ttl})")

    if untrailered:
        notes.append(
            f"⚠️ {untrailered} of {len(shas)} commit(s) in range carry NO "
            f"`Claude-Session:` trailer, so their authorship could not be "
            f"established and they were NOT graded. This is a gap in coverage, "
            f"not evidence of compliance.")

    if fails:
        return ("violation", fails, notes)
    if graded == 0:
        state = "unattributed" if untrailered == len(shas) else "no_manager_commits"
        notes.append(f"{graded} manager commit(s) in {len(shas)} commit(s) graded")
        return (state, [], notes)
    notes.append(f"{graded} manager commit(s) graded, all inside the surface")
    return ("clean", [], notes)


# --------------------------------------------------------------------------
# Self-test: plant each defect and prove the guard fails on it.
# --------------------------------------------------------------------------
MANAGER = "session_01SELFTESTMANAGER0000"
WORKER = "session_01SELFTESTWORKER00000"


def _run(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _commit(root: Path, msg: str, session: Optional[str]) -> None:
    _run(root, "add", "-A")
    body = msg if session is None else \
        f"{msg}\n\nClaude-Session: https://claude.ai/code/{session}\n"
    _run(root, "commit", "-m", body)


def _lease(holder: str, hb: str, up_state: str = "held", ttl: int = 90) -> str:
    return json.dumps({"schema_version": 1, "state": up_state, "holder": holder,
                       "heartbeat_at": hb, "ttl_minutes": ttl,
                       "heartbeat_target_minutes": 30}, indent=2)


def _sessions(updated_at: str) -> str:
    return json.dumps({"schema_version": 1, "updated_at": updated_at,
                       "sessions": []}, indent=2)


def _fixture(tmp: Path) -> Path:
    """A throwaway repo whose `main` carries the guard and a lease history."""
    root = tmp / "repo"
    root.mkdir()
    _run(root, "init", "-q", "-b", "main")
    _run(root, "config", "user.email", "selftest@example.com")
    _run(root, "config", "user.name", "selftest")
    # The guard must exist at the merge-base or every branch grades
    # `predates_guard`.
    _write(root, GUARD_REL, "# stand-in for the guard under test\n")
    _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T08:00:00Z"))
    _write(root, SESSIONS_REL, _sessions("2026-09-03T07:50:00Z"))
    _write(root, "CLAUDE.md",
           "# prose before\n\n"
           f"{BRIEF_BEGIN} -->\nbrief line\n{BRIEF_END} -->\n\n"
           "# prose after\n")
    _write(root, "src/runtime/orders.py", "x = 1\n")
    _commit(root, "base", None)
    return root


def _branch(root: Path, name: str) -> None:
    _run(root, "checkout", "-q", "-b", name, "main")


def self_test() -> int:
    cases: list[tuple[str, str, str]] = []   # (name, expected_state, needle)
    tmp = Path(tempfile.mkdtemp())
    try:
        # -- 1. PLANTED: manager commits application source. Must FAIL. -------
        root = _fixture(tmp)
        _branch(root, "plant-src")
        _write(root, "src/runtime/orders.py", "x = 2\n")
        _commit(root, "manager: tweak the order path", MANAGER)
        st, fails, _ = check(root, "main")
        cases.append(("manager commits src/ -> violation", st,
                      "\n".join(fails)))
        assert st == "violation", st
        assert "application source" in "\n".join(fails), fails

        # -- 2. CONTROL: the SAME diff from a worker session. Must PASS. ------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-worker")
        _write(root, "src/runtime/orders.py", "x = 2\n")
        _commit(root, "worker: tweak the order path", WORKER)
        st, fails, _ = check(root, "main")
        assert st == "no_manager_commits", (st, fails)
        assert not fails, fails
        cases.append(("same diff, worker session -> no_manager_commits", st, ""))

        # -- 3. CONTROL: manager commits its own registers. Must PASS. --------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-surface")
        _write(root, "docs/claude/work/MANAGER-CHECKLIST.json", '{"items": []}\n')
        _write(root, "docs/claude/health-review-backlog.json", '{"rows": []}\n')
        _commit(root, "manager: checklist + file a row", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "clean", (st, fails)
        cases.append(("manager on its own surface (incl. backlog) -> clean",
                      st, ""))

        # -- 4. PLANTED: manager edits CLAUDE.md PROSE. Must FAIL. ------------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-prose")
        (root / "CLAUDE.md").write_text(
            "# prose before EDITED\n\n"
            f"{BRIEF_BEGIN} -->\nbrief line\n{BRIEF_END} -->\n\n"
            "# prose after\n")
        _commit(root, "manager: reword the canonical rule", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        assert "SESSION-BRIEF" in "\n".join(fails), fails
        cases.append(("manager edits CLAUDE.md prose -> violation", st,
                      "\n".join(fails)))

        # -- 5. CONTROL: manager renders the BRIEF BLOCK only. Must PASS. -----
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-brief")
        (root / "CLAUDE.md").write_text(
            "# prose before\n\n"
            f"{BRIEF_BEGIN} -->\nbrief line RERENDERED\n{BRIEF_END} -->\n\n"
            "# prose after\n")
        _commit(root, "manager: re-render the session brief", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "clean", (st, fails)
        cases.append(("manager re-renders the brief block -> clean", st, ""))

        # -- 6. PLANTED: manager resolves a src/ conflict on a WORKER branch. -
        #    The accused act. The manager's own PR contains none of this.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _run(root, "checkout", "-q", "-b", "worker-pr", "main")
        _write(root, "src/runtime/orders.py", "x = 'worker'\n")
        _commit(root, "worker: change the order path", WORKER)
        _run(root, "checkout", "-q", "main")
        _write(root, "src/runtime/orders.py", "x = 'main'\n")
        _commit(root, "someone else: change the order path", WORKER)
        _run(root, "checkout", "-q", "worker-pr")
        p = subprocess.run(["git", "-C", str(root), "merge", "main"],
                           capture_output=True, text=True)
        assert p.returncode != 0, "expected a conflict to resolve"
        _write(root, "src/runtime/orders.py", "x = 'manager resolved'\n")
        _commit(root, "manager: resolve the conflict for them", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        assert "orders.py" in "\n".join(fails), fails
        cases.append(("manager resolves a src/ conflict on a worker branch "
                      "-> violation", st, "\n".join(fails)))

        # -- 7. PLANTED: heartbeat advanced, registry staler than the TTL. ----
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-stale")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T12:00:00Z"))
        _commit(root, "manager: lease heartbeat", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        assert "R6" in "\n".join(fails), fails
        cases.append(("heartbeat with a registry staler than ttl -> violation",
                      st, "\n".join(fails)))

        # -- 8. CONTROL: heartbeat WITH a fresh registry. Must PASS. ----------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-fresh")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T09:00:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T08:45:00Z"))
        _commit(root, "manager: lease heartbeat + registry sweep", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "clean", (st, fails)
        cases.append(("heartbeat with a fresh registry -> clean", st, ""))

        # -- 9. PLANTED: a `pending` exception must GRANT NOTHING. ------------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-pending-exc")
        _write(root, EXCEPTION_REL,
               "exceptions:\n"
               "  - id: EXC-TEST\n"
               "    status: pending\n"
               "    paths: ['src/**']\n"
               "    reason: an unfilled template must not grant anything at all\n"
               "    expires: 2099-01-01\n")
        _write(root, "src/runtime/orders.py", "x = 3\n")
        _commit(root, "manager: touch src under a pending exception", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        cases.append(("pending exception grants nothing -> violation", st,
                      "\n".join(fails)))

        # -- 10. PLANTED: an EXPIRED exception must GRANT NOTHING. -----------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-expired-exc")
        _write(root, EXCEPTION_REL,
               "exceptions:\n"
               "  - id: EXC-TEST\n"
               "    status: active\n"
               "    paths: ['src/**']\n"
               "    reason: this was a real incident but the window has closed\n"
               "    expires: 2020-01-01\n")
        _write(root, "src/runtime/orders.py", "x = 4\n")
        _commit(root, "manager: touch src under an expired exception", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        cases.append(("expired exception grants nothing -> violation", st,
                      "\n".join(fails)))

        # -- 11. PLANTED: a one-word `reason` must GRANT NOTHING. ------------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-thin-exc")
        _write(root, EXCEPTION_REL,
               "exceptions:\n"
               "  - id: EXC-TEST\n"
               "    status: active\n"
               "    paths: ['src/**']\n"
               "    reason: x\n"
               "    expires: 2099-01-01\n")
        _write(root, "src/runtime/orders.py", "x = 5\n")
        _commit(root, "manager: touch src under a one-word reason", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        cases.append(("one-word exception reason grants nothing -> violation",
                      st, "\n".join(fails)))

        # -- 12. CONTROL: a COMPLETE exception DOES permit the named path. ---
        #    Without this the escape hatch could be broken and nobody would know.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-good-exc")
        _write(root, EXCEPTION_REL,
               "exceptions:\n"
               "  - id: EXC-INCIDENT\n"
               "    status: active\n"
               "    paths: ['src/runtime/orders.py']\n"
               "    reason: live incident 2026-09-03, the order path was "
               "rejecting every exit and no session was up\n"
               "    expires: 2099-01-01\n")
        _write(root, "src/runtime/orders.py", "x = 6\n")
        _commit(root, "manager: emergency fix under a named exception", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "clean", (st, fails)
        cases.append(("complete, dated, scoped exception -> clean", st, ""))

        # -- 13. PLANTED: an UNTRAILERED commit is never 'compliant'. --------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-untrailered")
        _write(root, "src/runtime/orders.py", "x = 7\n")
        _commit(root, "no trailer at all", None)
        st, fails, notes = check(root, "main")
        assert st == "unattributed", (st, fails)
        assert any("not evidence of compliance" in n for n in notes), notes
        cases.append(("untrailered commits -> unattributed (NOT a pass)", st, ""))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("check_manager_scope self-test")
    print("=" * 72)
    for name, state, detail in cases:
        print(f"  PASS  {name}")
        if detail:
            first = detail.splitlines()[0] if detail.splitlines() else ""
            print(f"        └─ {first.strip()[:96]}")
    print("=" * 72)
    print(f"{len(cases)} cases: every planted defect FAILED the guard, and every "
          f"control stayed quiet.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--today", default=None,
                    help="override today's date (YYYY-MM-DD) for expiry checks")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    predates = guard_existed_at_merge_base(REPO, args.base)
    if predates is False:
        print("manager-scope: undeclared_predates_guard — this branch was cut "
              "before the guard existed and could not have known the rule. "
              "PASS, loudly.")
        return 0

    state, fails, notes = check(REPO, args.base, today=args.today)
    print(f"manager-scope: {state}")
    for n in notes:
        print(f"  · {n}")
    if fails:
        print()
        print("THE MANAGER SESSION ONLY MANAGES. These commits do not:")
        for f in fails:
            print(f"  ✗ {f}")
        print()
        print("  Fix: route the work to a session "
              "(scripts/ops/session_registry.py), or — for a live incident "
              f"only — add a named, dated, scoped entry to {EXCEPTION_REL}.")
        return 1
    if state in ("not_a_pr", "unattributed"):
        # Loud, and never rendered the same as a real pass.
        print("  (nothing was graded — this is not a clean bill of health)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
