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
`BL-20260903-MANAGER-CHECKLIST-GOES-STALE-SILENTLY-AND-STATUS-REPORTS-IT-AS-CURRENT`. A reminder is not a
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

**3. FILING A BACKLOG ROW IS MANAGEMENT AND STAYS PERMITTED.**
`docs/claude/health-review-backlog.json` is on the surface — unconditionally,
with no row cap.

⚠️ **THIS IS AN OPERATOR DECISION (2026-09-03), NOT A SESSION'S JUDGEMENT CALL.
DO NOT REOPEN IT AS AN OPEN QUESTION.** It was argued here first as a session's
contested call; the 2026-09-03 day manager then put the case AGAINST it to the
operator directly — naming that it had filed four detailed rows that morning and
that those were hours not spent supervising — and offered both a row CAP and a
disclose-the-count middle option. **The operator chose this version, as
written.** So the reasoning below is the recorded basis of a decision, not a
proposal. The only thing that reopens it is the revisit condition at the end of
this section, and that needs evidence nobody has yet.

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

⚠️ A LEASE **CLAIM** IS GRADED TOO, AND THAT IS A DECISION, NOT AN OVERSIGHT.
A claim sets `heartbeat_at` fresh, so it advances and R6 runs. The objection is
real — an incoming manager inherits whatever staleness the outgoing one left,
and failing it for someone else's gap blames the wrong session. It is graded
anyway, because the cold start is exactly when the registry matters: a successor
can only pick up the sessions the file names, which is the whole reason it lives
in the repo rather than in a session's context. Requiring the incoming manager
to sweep the registry BEFORE its claim lands makes "read your sessions" a
precondition of taking the lease rather than a good intention. It is cheap to
satisfy — one `session_registry.py` run — and this is the single R6 fire on the
2026-09-03 day manager (its claim `f550f833` sat 479 minutes after the registry
was last touched).

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

DUTY 3 — FEEDBACK BECOMES CANON. **NOT MECHANIZED HERE, AND THAT IS THE
FINDING RATHER THAN A GAP I RAN OUT OF TIME ON.**
--------------------------------------------------------------------------
The operator's 2026-09-03 ruling names three manager duties. Two of them are
above: R8 is the merge queue, R7 is the check-in cadence. The third — *"we need
to make sure that these types of feedbacks are turning into actual rules when
applicable"* — is deliberately NOT a check, and the manager who commissioned it
pre-authorised this answer: *"Refusing to mechanize it is a legitimate outcome;
pretending to is not."*

WHY IT CANNOT BE A CHECK. CI cannot read a conversation, so it cannot know that
feedback was ever given. Every design that survives contact with that fact needs
the graded party to first WRITE DOWN that it received something — a field, a
marker, a row. And **omitting that write is free**. So the check would grade
exactly the subset of cases where the manager already did the right thing, and
would be structurally blind to the case that matters: feedback received and
never recorded. That is the `new-table-wiring-guard` presence-only failure with
an extra step, and this repo has paid for it once already.

⚠️ IT IS WEAKER THAN IT LOOKS, EVEN COMPARED TO THIS REPO'S OTHER PARTIAL
CHECKS. `session-registry-guard` is also partial, but it joins TWO
independently-written records (the checklist's `owner` and the registry), so it
catches the overlap of two incomplete sets. Operator feedback in chat has NO
second record anywhere — the decision round-trip covers operator DECISIONS, not
operator REMARKS — so there is nothing to join against and no denominator.

WHERE IT GOES INSTEAD: the session-end `doc-freshness` discipline, which already
owns *"this session's material decisions actually landed in every durable
surface they belong in"*. That is the same question one step earlier, it already
runs at the right moment, and it is performed by a reader rather than a grep.

⚠️ AND THE ONE THING THAT *IS* CHECKABLE IS ALREADY CHECKED ELSEWHERE, WHICH IS
WHY NOTHING NEW IS ADDED: the last mile of duty 3 is *"a rule lands as canon
WITH a mechanism"*, and a canon change naming a mechanism that does not exist is
exactly what `scripts/ops/check_backlog_refs.py` catches for tracking ids and
what R8's absence-report catches for the queue. **That is not hypothetical: this
guard's own R8 exists because `docs/claude/work/MERGE-QUEUE.json` was cited to a
sub-session as "the state of record … created today" and a search of every
branch and every commit found it nowhere.** The mechanism was named and did not
exist. Grading the NAME against the filesystem is the honest half of duty 3, and
it needs no new marker anybody could set.

STATES, NEVER COLLAPSED
-----------------------
  ``not_a_pr``               — no base ref to diff against. Nothing was graded.
                               NOT a pass.
  ``predates_guard``         — the branch was cut before this guard existed and
                               could not have known. Passes, counted, loud.
  ``no_manager_commits``     — no commit in range is attributable to a session
                               that has held the lease. The ordinary worker PR.
  ``unattributed``           — at least one commit's AUTHORSHIP could not be
                               established, so it was not graded: it carries no
                               `Claude-Session:` trailer, or it carries one the
                               lease register CONTRADICTS. **We did not look is
                               not the same as compliant**: reported loudly on
                               every run, never silently passed, and it
                               DOMINATES ``clean`` — a range holding one
                               unattributable commit has not been fully looked
                               at.
  ``clean``                  — manager commits found, all inside the surface,
                               and every commit in range was attributable.
  ``violation``              — a manager commit touched a worker path. FAILS.

AUTHORSHIP IS A CLAIM, AND THE THIRD STATE IS THE WHOLE POINT (MI-106)
----------------------------------------------------------------------
Who wrote a commit is read from its `Claude-Session:` trailer — **which the
author writes themselves**. Until 2026-09-04 that produced two different bugs
from one root, and only one of them was visible:

  * **The false positive, measured.** #10895 was blocked because `5504200c`
    touches `scripts/notify_on_pull.py` under a trailer naming
    `session_011JWFxuYAaEQKCFCmG6gnHJ` — the PREVIOUS DAY's manager. The commit
    body is in the MI-83 *worker's* voice about MI-83's own PR, and the head
    commit of that same branch says outright that the spawn dispatch handed the
    sub-session the MANAGER's session URL to stamp. So the guard produced a TRUE
    finding about what it could see and a FALSE one about what happened — and
    rendered it as `violation`, a graded verdict, when the fact was *we cannot
    establish who wrote this*.
  * **The evasion, unguarded.** A manager that wants to do an item can stamp a
    worker's session id and this guard vouches for it. The `unattributed`
    disclosure covered UNTRAILERED commits only; a MIS-trailered one fell
    outside it and read as graded. **A guard cheaper to lie to than to satisfy
    is worse than no guard** — the `new-table-wiring-guard` lesson.

THE FIX, AND EXACTLY HOW FAR IT REACHES. `office_windows` derives, from the git
history of `MANAGER-LEASE.json`, the intervals in which each session actually
HELD the lease, and a trailer naming a holder OUTSIDE its own window is graded
``unattributed`` rather than ``violation``. This is not a workaround: R2's rule
is *a MANAGER commit touching a worker path*, and manager-ness is a property of
a MOMENT. A session that stood down yesterday is not the manager today, so
grading its commits as management was wrong on the guard's own terms.

⚠️ **IT CONTRADICTS; IT NEVER CONFIRMS.** The register is evidence the commit's
author cannot reach back and change, but the author DATE it is joined against is
ordinary git metadata and is author-settable. So this catches the CARELESS
mis-stamp and not the deliberate one, and the deliberate one — a manager
stamping a live worker's id inside that worker's window — remains open. It is
named in a disclosure printed on EVERY verdict, including clean ones, because a
limit stated is a limit a reader can price and a limit implied is not. Closing
it honestly needs a per-session credential this repo does not have; nothing here
pretends otherwise.

THE RULES
---------
  R2  a manager commit touching a worker path
  R6  a heartbeat whose supervision RECORD is staler than the lease TTL
  R7  a heartbeat more than one TTL after the manager's previous one
  R8  the merge queue's one invariant: at most one entry `rebasing`

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
import tempfile
from datetime import datetime, timedelta, timezone
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

# --------------------------------------------------------------------------
# THE ATTRIBUTION CONTRACT — three states, and collapsing any two is the defect
# --------------------------------------------------------------------------
#: Whether this guard can say WHO was managing when a commit was authored.
#:
#: ⚠️ THIS CONTRACT IS DELIBERATELY *NOT* REGISTERED IN
#: `scripts/ci/check_collapsed_states.py`, AND THAT IS A MEASURED CHOICE, NOT
#: AN OVERSIGHT. That registry's consumer model assumes a producer module and
#: consumers in OTHER files: it skips `f == prod_path` outright, so a
#: single-file CI guard whose only reader is its own `check()` fails check (2)
#: — "state(s) [...] are produced but NO consumer branches on them" — however
#: correctly the states are used. Registering it would have left exactly two
#: ways to green: grandfather it, or invent a consumer module that exists to be
#: consumed. That second one is the failure the registry itself records against
#: its own earlier evidence model — "the only way to 'fix' it was to write
#: worse code". So the PATTERN is followed here in full (named constants, the
#: three states distinct in the guard's own output, and the collapse named
#: below) and the registry entry is not faked. If the registry later grows a
#: same-file consumer notion, this belongs in it.
#:
#: ⚠️ COLLAPSING ANY TWO OF THESE REINTRODUCES MI-106 VERBATIM. Until
#: 2026-09-04 there were effectively two: a commit carrying a trailer was
#: graded, and one without a trailer was not. A MIS-stamped commit therefore
#: rendered as a graded verdict — `violation` — when the true fact was "we
#: cannot establish who wrote this". `#10895` is the measured instance:
#: `5504200c` touches `scripts/notify_on_pull.py` under a trailer naming
#: `session_011JWFxuYAaEQKCFCmG6gnHJ`, the PREVIOUS DAY's manager, which the
#: lease register shows released the lease at 2026-09-02T21:38:05Z — eight
#: hours before the commit was authored. "We cannot establish who wrote this"
#: and "a manager did an item" are different facts and must not render the
#: same.
ATTR_ATTRIBUTED = "attributed"       # trailer names a session the lease
                                     # register independently places IN OFFICE
                                     # at the commit's author time. GRADED.
ATTR_UNATTRIBUTED = "unattributed"   # no trailer, OR a trailer the register
                                     # CONTRADICTS. NOT graded, reported loudly
                                     # on every run, never a pass.
ATTR_NOT_MANAGER = "not_manager"     # trailer names a session that has never
                                     # held the lease. R2 does not reach it.


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

    # ⚠️ "ADVANCED" IS DECIDED AGAINST EVERY PARENT, NOT `sha~1`.
    #
    # This is R7's MI-122 root, and it was left in R6. `sha~1` is the FIRST
    # parent, which on a MERGE is the branch being merged INTO. When a manager
    # merges `main` into a worker branch the lease's prior state arrives along
    # the SECOND parent, so comparing against `sha~1` sees a heartbeat that
    # "changed" and grades a commit that ADVANCED NOTHING — the affirmative
    # claim "I am still managing" was never made here.
    #
    # MEASURED before the fix (MI-125), on the R6 face of the 8g plant: the
    # merge was `GRADED` with gap=75min under a 90min TTL, and the SAME shape
    # with a registry 160min stale FAILED. So the defect was not latent — it
    # was one arithmetic step from a false red, and R7's own 8g plant could
    # never have caught it because that plant keeps the registry FRESH on the
    # manager line so only R7 can fire.
    #
    # ⚠️ AND NOT GRADING A MERGE AT ALL IS THE WRONG FIX — it would let a
    # manager launder any lapse through a merge commit. A merge that GENUINELY
    # advances the heartbeat past a stale registry must still fail, which is
    # what the paired control asserts.
    now_hb_raw = lease.get("heartbeat_at")
    now_hb_ts = _parse_ts(now_hb_raw)
    parent_hbs = []
    for p in _parents(root, sha):
        pl, _pstate = _lease_at(root, p)
        if pl is None:
            continue
        if pl.get("heartbeat_at") == now_hb_raw:
            return None, None, notes      # heartbeat not advanced here
        phb = _parse_ts(pl.get("heartbeat_at"))
        if phb is not None:
            parent_hbs.append((phb, p))
    if now_hb_ts is not None and parent_hbs:
        newest, newest_sha = max(parent_hbs, key=lambda t: t[0])
        if now_hb_ts <= newest:
            notes.append(
                f"R6 {sha[:8]}: heartbeat NOT advanced here — parent "
                f"{newest_sha[:8]} already carries {newest.isoformat()}; the "
                f"claim arrived along that parent, not at this commit")
            return None, None, notes

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
# R7 — the CHECK-IN CADENCE as a duty, graded on what HAPPENED
# --------------------------------------------------------------------------
#: A gap longer than this between one manager heartbeat and the next FAILS.
#:
#: CHOSEN FROM THE MEASURED DISTRIBUTION, and anchored, not tuned. Population:
#: **51 distinct heartbeat instants across every revision of MANAGER-LEASE.json,
#: yielding 48 consecutive SAME-HOLDER gaps** (a holder change is a handover,
#: not a lapse, and is excluded):
#:
#:     min 3 · p25 22 · MEDIAN 30 · p75 57 · p90 65 · max 215   (minutes)
#:     > 30min: 24 of 48 (50%)   > 45min: 18 of 48 (38%)
#:     > 60min:  8 of 48 (17%)   > 90min:  2 of 48 (4%)
#:
#: 30 is the MEDIAN, so a 30-minute rule fails half of everything this repo has
#: ever done and would be deleted rather than fixed — the same reasoning R6
#: records, and the reason `heartbeat_target_minutes: 30` is a TARGET and not
#: this threshold. 90 fails 4%, and it has an independent anchor that does not
#: come from the data at all: **the lease TTL is 90 minutes and takeover is
#: time-based**, so a manager silent for longer than one TTL has gone quiet for
#: longer than the window in which another session may seize the lease from it.
#: The threshold is read from `ttl_minutes` in the file, never hard-coded here.
#:
#: ⚠️⚠️ AND THE HONEST PART, WHICH MUST NOT BE BURIED: **R7 WOULD NOT HAVE
#: CAUGHT THE FAILURE THAT MOTIVATED IT.** The 2026-09-03 manager asked for this
#: after its 11:04Z sweep silently never fired and #10923 sat green and mergeable
#: from 11:01:05Z to 11:11Z — ten minutes — with the OPERATOR noticing before the
#: mechanism did. Measured over that same day: **10 same-holder gaps, every one
#: between 15 and 35 minutes**, only 1 above 30. The heartbeat cadence that day
#: was FINE. R7 grades whether the manager touched its lease, which is NOT the
#: same fact as whether the manager acted on what was waiting, and reporting the
#: first as though it covered the second would be precisely the collapsed state
#: this repo files findings about.
#:
#: What R7 does catch is real and separate: the 2 gaps of 90+ minutes, one of
#: them 215 minutes — a manager that stopped managing and did not stand down.
#: The 10-minute case needs `scripts/ops/pr_queue_latency.py`, and see
#: BL-20260903-THE-PR-QUEUE-WATCHER-CANNOT-SEE-A-TEN-MINUTE-STALL for why that
#: watcher cannot see it either as currently configured.
#:
#: ⚠️ MI-122 RE-MEASURED THE RULE'S ACTUAL REACH, because "90 minutes fails 4% of
#: gaps" is a claim about the DISTRIBUTION and not about what this guard does to
#: real commits. Population: the 46 commits touching MANAGER-LEASE.json reachable
#: on `main` in a shallow clone at --depth=1000, graded by the pre-MI-122 guard
#: and by this one. They disagree on 2, and R7 FAILs go 3 -> 1. Both changed
#: verdicts are same-holder re-CLAIMS over state=expired -- 448f086a (dead
#: interval 100 min) and 2e5ef601 (214 min) -- and BOTH still REPORT that
#: interval rather than losing it. cb9f0bbb still FAILS at 131 minutes: that is
#: the rule's real case, and it is why the exemption is keyed on the lease RUN
#: rather than on the size of the gap. State the population when re-quoting this.
def _lease_at(root: Path, ref: str) -> tuple[Optional[dict], str]:
    """(lease, state) at `ref`, state one of `ok` / `absent` / `unreadable`.

    Three states, never collapsed: a lease that is not there and a lease we
    could not parse are different facts, and only the second is worth a note.
    """
    rc, blob = _git(root, "show", f"{ref}:{LEASE_REL}")
    if rc != 0:
        return None, "absent"
    try:
        doc = json.loads(blob)
    except Exception:
        return None, "unreadable"
    return (doc, "ok") if isinstance(doc, dict) else (None, "unreadable")


def _parents(root: Path, sha: str) -> list[str]:
    """Every parent of `sha`, in git's order (first parent first)."""
    rc, out = _git(root, "rev-list", "--parents", "-n", "1", sha)
    if rc != 0 or not out.strip():
        return []
    return out.split()[1:]


def _latest_heartbeat(cands: list[tuple[str, dict]]):
    """The (heartbeat, sha) with the NEWEST heartbeat_at, or None."""
    best = None
    for p, pl in cands:
        hb = _parse_ts(pl.get("heartbeat_at"))
        if hb is None:
            continue
        if best is None or hb > best[0]:
            best = (hb, p)
    return best


def heartbeat_cadence_gap(root: Path, sha: str) -> tuple[Optional[int], Optional[int], list[str]]:
    """(gap_minutes_since_previous_heartbeat, ttl_minutes, notes).

    Graded only on a commit that ADVANCES `heartbeat_at`, and only against a
    previous heartbeat that belongs to the SAME LEASE RUN, reached along
    whichever parent actually carries that run's prior state.

    ⚠️ **A "previous heartbeat" is not simply whatever blob sits at `sha~1`.**
    Two things must be established before a gap means anything, and R7
    established NEITHER until MI-122. Both measured instances share this one
    root: R7 selected a previous heartbeat without establishing that it
    belonged to the same run, reached along the right parent.

    **1. THE SAME RUN, not merely the same holder.** A lease RUN is identified
    by `(holder, claimed_at)`: `manager_lease.py::cmd_claim` stamps a fresh
    `claimed_at` on every claim and `cmd_heartbeat` preserves it, so the pair
    names the run and nothing else does. A CLAIM over a lease that EXPIRED
    opens a NEW run for the same session, and charging the dead interval to it
    grades the incoming manager for the silence of a lease that was already
    gone. That is exactly the reasoning this function already used to exempt a
    HANDOVER — *"grading one would fail the incoming manager for the outgoing
    one's silence"* — and it transfers verbatim: a lease that DIED and was
    re-claimed is a handover to oneself. Measured instance: `cc984fec` failed
    at *"746 minutes since this manager's previous heartbeat"*.

    ⚠️⚠️ **THE DEAD INTERVAL IS STILL REPORTED, AND THAT IS THE POINT.** What
    moves is WHERE the silence is charged, never WHETHER it is seen. 746
    minutes with no manager is the most valuable thing this guard said that
    night, and a silent pass would be strictly worse than the false red it
    replaces. A re-claim emits a note naming the interval and the state it was
    claimed over, and `--self-test` ASSERTS that note is present — so a future
    change that "fixes" this rule by simply not looking fails the test rather
    than passing quietly.

    ⚠️ **AND THE EXEMPTION IS NOT A BYPASS.** `cmd_claim` returns early on
    `held_by_me` WITHOUT rewriting `claimed_at`, so a manager holding a live
    lease cannot re-claim its way out of a cadence grade. Reaching a new run
    costs either letting the lease actually expire — which IS the silence, and
    it is reported — or `--force --reason`, which the file records. The
    cheapest way past R7 is still to check in.

    **2. THE RIGHT PARENT.** `sha~1` is the FIRST parent, which on a MERGE is
    the branch being merged INTO. A manager merging `main` into a worker
    branch therefore read the WORKER branch's stale lease as its own previous
    heartbeat and graded a lapse that never happened, blocking #10895. Filed as
    BL-20260904-MANAGER-SCOPE-R7-MEASURES-CADENCE-ALONG-THE-FIRST-PARENT-SO-A-MANAGER-MERGE-INTO-A-WORKER-BRANCH-READS-AS-A-LAPSE
    — that id is deliberately on ONE line: wrapped across two, `artifact-validity-guard`
    reads the fragment and the reference resolves to NOTHING, which reads as
    tracked while being tracked by nobody.
    Every parent is read, and the LATEST same-run heartbeat among them wins:
    the manager's silence is WALL-CLOCK silence, so the most recent check-in
    on any line the commit just brought together is the last time it was
    heard from.

    ⚠️ **AN ABSENT `claimed_at` STILL GRADES, deliberately.** When either side
    omits it the run cannot be established, and the choice is between dropping
    enforcement and keeping it. This keeps it, and says so in a note. R7's real
    case — a manager ALIVE and silent past one TTL — is the measured 4% of gaps
    the rule exists for, and a guard that stops grading whenever a field is
    missing is one hand-edit away from being switched off.
    """
    lease, lstate = _lease_at(root, sha)
    if lstate != "ok" or lease is None:
        return None, None, []
    if lease.get("state") != "held":
        return None, None, []

    now_hb = _parse_ts(lease.get("heartbeat_at"))
    ttl = lease.get("ttl_minutes")
    if now_hb is None or not isinstance(ttl, int):
        return None, None, []

    holder = lease.get("holder")
    claimed = lease.get("claimed_at")

    parents = _parents(root, sha)
    if not parents:
        return None, None, []                  # root commit: nothing to compare

    notes: list[str] = []
    readable: list[tuple[str, dict]] = []
    unreadable: list[str] = []
    for p in parents:
        pl, pstate = _lease_at(root, p)
        if pstate == "unreadable":
            unreadable.append(p)
        elif pl is not None:
            readable.append((p, pl))

    if unreadable:
        notes.append(
            f"{LEASE_REL} unreadable at {', '.join(s[:8] for s in unreadable)} "
            f"(parent of {sha[:8]}) — that parent NOT used as the previous "
            f"heartbeat (we could not look)")
    if not readable:
        return None, None, notes               # no predecessor to compare to

    if len(parents) > 1:
        notes.append(
            f"{sha[:8]} is a MERGE ({len(parents)} parents) — the previous "
            f"heartbeat is the LATEST same-run parent, NOT `{sha[:8]}~1`; the "
            f"first parent is the branch merged INTO, whose lease is stale "
            f"when a manager merges main into a worker branch")

    same_holder = [(p, pl) for p, pl in readable if pl.get("holder") == holder]
    if not same_holder:
        notes.append(f"{sha[:8]} is a HANDOVER (holder changed) — cadence "
                     f"not graded; a handover is not a lapse")
        return None, None, notes

    same_run: list[tuple[str, dict]] = []
    other_run: list[tuple[str, dict]] = []
    unestablished = False
    for p, pl in same_holder:
        pc = pl.get("claimed_at")
        if pc is None or claimed is None:
            unestablished = True
            same_run.append((p, pl))
        elif pc == claimed:
            same_run.append((p, pl))
        else:
            other_run.append((p, pl))

    if unestablished:
        notes.append(
            f"{sha[:8]}: `claimed_at` absent on one side — the lease RUN could "
            f"NOT be established, so cadence is graded on holder alone. "
            f"Enforcement is KEPT rather than dropped: R7's real case is a "
            f"manager ALIVE and silent past one TTL, and a rule that stops "
            f"grading on a missing field is one hand-edit from being off.")

    if not same_run:
        # Every same-holder parent belongs to an EARLIER run, so this commit
        # OPENS a run: it is a re-CLAIM, not a late check-in. The silence is
        # not charged as cadence -- and it is REPORTED, loudly, because the
        # dead interval is the finding even when the lapse is not.
        best = _latest_heartbeat(other_run)
        over = lease.get("claimed_over_state")
        if best is None:
            notes.append(
                f"⚠️ {sha[:8]} is a re-CLAIM (a NEW lease run for the same "
                f"holder) — cadence not graded; no readable previous heartbeat "
                f"to measure the dead interval against (we could not look)")
            return None, None, notes
        dead = int((now_hb - best[0]).total_seconds() // 60)
        notes.append(
            f"⚠️ {sha[:8]} is a re-CLAIM, not a late check-in: it opens a NEW "
            f"lease run (claimed_at {claimed}) for the same holder"
            + (f", over state={over}" if over else "")
            + f", so the gap is NOT charged as this run's cadence. THE DEAD "
              f"INTERVAL IS REAL AND IS REPORTED: {dead} minutes with no "
              f"manager heartbeat before this claim. A lease that DIED and was "
              f"re-claimed is a handover to oneself; charging it would fail "
              f"the incoming manager for the silence of a lease already gone.")
        return None, None, notes

    best = _latest_heartbeat(same_run)
    if best is None:
        return None, None, notes
    was_hb, was_sha = best
    if now_hb <= was_hb:
        return None, None, notes               # heartbeat not advanced here
    if len(parents) > 1:
        notes.append(f"{sha[:8]}: previous heartbeat taken from parent "
                     f"{was_sha[:8]} ({was_hb.isoformat()})")
    return int((now_hb - was_hb).total_seconds() // 60), ttl, notes


# --------------------------------------------------------------------------
# R8 — THE MERGE QUEUE'S ONE INVARIANT
# --------------------------------------------------------------------------
QUEUE_REL = "docs/claude/work/MERGE-QUEUE.json"
QUEUE_STATES = {"waiting", "rebasing", "green", "merged", "blocked"}


def queue_findings(root: Path) -> tuple[list[str], list[str]]:
    """(failures, notes) for the merge queue's structural invariant.

    ⚠️ ONE INVARIANT, NOT A SCHEMA POLICE. **At most one entry may be
    `rebasing`.** Two sessions rebasing against the same moving `main` is the
    thundering herd the queue exists to prevent, and it is a state that looks
    fine to each session individually — which is exactly why it needs a check
    rather than a convention.

    ⚠️ AN ABSENT FILE IS NOT GRADED, AND THAT IS DELIBERATE. This guard runs on
    every PR in a repo where the queue is the manager's instrument; failing a
    contributor's PR because the manager has not written a queue row would
    punish the one actor who cannot fix it — the reasoning
    `check_pr_queue_watch.py` records for refusing to fail PRs on backlog size.
    The file's ABSENCE is reported loudly on every run instead, which is how
    this file came to exist at all: it was cited as "the state of record …
    created today" while existing on no branch and in no commit.
    """
    fails: list[str] = []
    notes: list[str] = []
    p = root / QUEUE_REL
    if not p.exists():
        notes.append(
            f"⚠️ {QUEUE_REL} is ABSENT. The merge queue has no state of record, "
            f"so it lives in whichever session is running it and dies with that "
            f"session. NOT graded (and not failed) — reported.")
        return fails, notes
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        fails.append(
            f"R8 {QUEUE_REL} is unreadable ({exc}). An unparseable queue is "
            f"worse than none: a session reading it gets no order and cannot "
            f"tell that it got none.")
        return fails, notes

    entries = doc.get("entries")
    if not isinstance(entries, list):
        fails.append(f"R8 {QUEUE_REL}: `entries` must be a list.")
        return fails, notes

    rebasing = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            fails.append(f"R8 {QUEUE_REL}: entries[{i}] is not a mapping.")
            continue
        st = e.get("state")
        if st not in QUEUE_STATES:
            fails.append(
                f"R8 {QUEUE_REL}: entries[{i}] (pr={e.get('pr')}) has state "
                f"{st!r}; must be one of {', '.join(sorted(QUEUE_STATES))}. A "
                f"closed vocabulary, so 'not my turn' (waiting) and 'cannot go' "
                f"(blocked) stay different facts.")
        if st == "blocked" and not str(e.get("blocked_on") or "").strip():
            fails.append(
                f"R8 {QUEUE_REL}: entries[{i}] (pr={e.get('pr')}) is `blocked` "
                f"with an empty `blocked_on`. A blocker nobody named is a row "
                f"that stalls silently.")
        if st == "rebasing":
            rebasing.append(e.get("pr"))

    if len(rebasing) > 1:
        fails.append(
            f"R8 {QUEUE_REL}: {len(rebasing)} entries are `rebasing` at once "
            f"(PRs {', '.join(str(x) for x in rebasing)}). AT MOST ONE. Two "
            f"sessions rebasing against the same moving `main` re-conflict each "
            f"other by construction — measured 2026-09-03: 26 merges to main, "
            f"19 of them touching a shared register, #10918 dirtied three times "
            f"and ~16-minute CI runs voided before they finished.")
    else:
        notes.append(f"R8 ok: {len(entries)} queue entrie(s), "
                     f"{len(rebasing)} rebasing (at most one permitted)")
    return fails, notes


# --------------------------------------------------------------------------
def office_windows(root: Path) -> tuple[dict[str, list[tuple[datetime, datetime]]], list[str]]:
    """When did each session actually HOLD the lease, per the register itself?

    WHY THIS EXISTS. `lease_holders` answers "has this session EVER managed?",
    and that is the wrong question for R2. R2's rule is *a manager commit
    touching a worker path*, and manager-ness is a property of a MOMENT, not a
    property a session keeps forever. Grading a commit authored on 2026-09-03
    against a session that stood down on 2026-09-02 is wrong on the guard's own
    terms, whether or not its trailer is honest.

    THE EVIDENCE IS THE REGISTER, NOT THE COMMIT. Windows are read from the git
    history of `MANAGER-LEASE.json` — dozens of separate commits written at
    cadence by whoever held the lease, merged to `main` before this one existed.
    The author of the commit being graded cannot reach back into that history.
    That is the whole point: it is the one authorship signal here that is not
    the author's own say-so.

    ⚠️ IT CONTRADICTS, IT NEVER CONFIRMS. The commit's author date is git
    metadata, and git metadata is settable by whoever makes the commit
    (`GIT_AUTHOR_DATE`). So a window MISS is real evidence — the register
    disagrees with the trailer — while a window HIT proves only that the two
    are consistent, which a determined forger gets for free. This catches the
    CARELESS mis-stamp (the measured one: a spawn template that handed
    sub-sessions the manager's session URL) and not the deliberate one. The
    deliberate one is named in the guard's standing disclosure instead, because
    a limit stated is a limit a reader can price and a limit implied is not.

    THE WINDOWS ARE DELIBERATELY GENEROUS. Each contiguous run of same-holder
    observations is widened by one lease TTL at BOTH ends — the register's own
    unit of uncertainty about who is managing, since takeover is time-based.
    Widening can only ever cause MORE commits to be graded, never fewer, so the
    error it risks is a false violation a reader can argue with, not a missed
    one they never see.

    Returns ({session_id: [(start, end), ...]}, notes).
    """
    notes: list[str] = []
    rc, out = _git(root, "log", "--all", "--reverse", "--pretty=%H", "--", LEASE_REL)
    if rc != 0:
        return {}, ["lease register history unreadable — no office window "
                    "could be derived"]

    #: (timestamp, session, is_release) in register order.
    obs: list[tuple[datetime, str, bool]] = []
    ttls: list[int] = []
    revisions = 0
    for line in out.splitlines():
        sha = line.strip()
        if not sha:
            continue
        rc2, blob = _git(root, "show", f"{sha}:{LEASE_REL}")
        if rc2 != 0:
            continue
        try:
            d = json.loads(blob)
        except Exception:
            continue          # an unreadable revision is skipped, not fatal
        revisions += 1
        ttl = d.get("ttl_minutes")
        if isinstance(ttl, int) and ttl > 0:
            ttls.append(ttl)
        holder = d.get("holder")
        hb = _parse_ts(d.get("heartbeat_at"))
        if isinstance(holder, str) and holder.startswith("session_") and hb:
            obs.append((hb, holder, False))
            continue
        prev = d.get("previous_holder")
        rel = _parse_ts(d.get("released_at"))
        if isinstance(prev, str) and prev.startswith("session_") and rel:
            obs.append((rel, prev, True))

    if not obs:
        notes.append(
            f"⚠️ {revisions} revision(s) of {LEASE_REL} carry no usable "
            f"(holder, timestamp) pair, so NO office window could be derived. "
            f"Attribution falls back to the trailer alone for this run — the "
            f"pre-MI-106 behaviour, stated rather than silent.")
        return {}, notes

    ttl = max(ttls) if ttls else 90
    obs.sort(key=lambda o: o[0])

    #: Contiguous runs of the same holder. A release CLOSES the run it names.
    runs: list[tuple[str, datetime, datetime]] = []
    cur_sess = None
    cur_lo = cur_hi = None
    for ts, sess, is_release in obs:
        if sess != cur_sess:
            if cur_sess is not None:
                runs.append((cur_sess, cur_lo, cur_hi))
            cur_sess, cur_lo, cur_hi = sess, ts, ts
        else:
            cur_hi = ts
        if is_release:
            runs.append((cur_sess, cur_lo, cur_hi))
            cur_sess = cur_lo = cur_hi = None
    if cur_sess is not None:
        runs.append((cur_sess, cur_lo, cur_hi))

    grace = timedelta(minutes=ttl)
    windows: dict[str, list[tuple[datetime, datetime]]] = {}
    for sess, lo, hi in runs:
        windows.setdefault(sess, []).append((lo - grace, hi + grace))

    notes.append(
        f"office windows derived from {revisions} revision(s) of {LEASE_REL} "
        f"(population: every revision reachable from --all): "
        f"{len(runs)} lease-holding run(s) across {len(windows)} session(s), "
        f"each widened by one ttl ({ttl}min) at both ends")
    return windows, notes


def commit_authored_at(root: Path, sha: str) -> Optional[datetime]:
    """The commit's author timestamp. ⚠️ Author-settable — see office_windows."""
    rc, out = _git(root, "log", "-1", "--pretty=%aI", sha)
    if rc != 0:
        return None
    return _parse_ts(out.strip())


def attribute(root: Path, sha: str, holders: set[str],
              windows: dict[str, list[tuple[datetime, datetime]]]
              ) -> tuple[str, Optional[str], str]:
    """Grade one commit against the attribution contract.

    Returns (state, session_or_None, why). See ATTR_* for why three states.
    """
    sess = commit_session(root, sha)
    if sess is None:
        return (ATTR_UNATTRIBUTED, None,
                "carries no `Claude-Session:` trailer, so authorship could not "
                "be established at all")
    if sess not in holders:
        return (ATTR_NOT_MANAGER, sess, "names a session that has never held "
                                        "the manager lease")
    if not windows:
        # No window could be derived. Fall back to the trailer alone rather
        # than blinding the guard — an unreadable register must not become the
        # cheapest way past R2.
        return (ATTR_ATTRIBUTED, sess,
                "names a lease holder; no office window was derivable, so this "
                "is the trailer's unchecked word")
    when = commit_authored_at(root, sha)
    if when is None:
        return (ATTR_UNATTRIBUTED, sess,
                f"names {sess}, but this commit has no readable author date, "
                f"so the lease register could not be asked whether that "
                f"session was managing when it was written")
    for lo, hi in windows.get(sess, []):
        if lo <= when <= hi:
            return (ATTR_ATTRIBUTED, sess, f"in office at {when.isoformat()}")
    held = windows.get(sess, [])
    span = (", ".join(f"{lo.isoformat()}..{hi.isoformat()}" for lo, hi in held)
            if held else "never, per the register")
    return (ATTR_UNATTRIBUTED, sess,
            f"names {sess}, but {LEASE_REL}'s own history places that session "
            f"in office {span} — NOT at {when.isoformat()}, when this commit "
            f"was authored. The register CONTRADICTS the trailer, so who wrote "
            f"this is unestablished. It is NOT thereby excused: it is ungraded "
            f"and said so out loud")


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

    windows, wnotes = office_windows(root)
    notes.extend(wnotes)

    graded = 0
    untrailered = 0
    contradicted = 0
    for sha in shas:
        attr, sess, why = attribute(root, sha, holders, windows)
        if attr == ATTR_NOT_MANAGER:
            continue
        if attr == ATTR_UNATTRIBUTED:
            rc, subj = _git(root, "log", "-1", "--pretty=%s", sha)
            if sess is None:
                untrailered += 1
            else:
                contradicted += 1
                notes.append(
                    f"⚠️ UNATTRIBUTED {sha[:8]} {subj[:64]} — {why}")
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

        cad, cttl, cnotes = heartbeat_cadence_gap(root, sha)
        notes.extend(cnotes)
        if cad is not None and cttl is not None:
            if cad > cttl:
                fails.append(
                    f"R7 {short}\n"
                    f"      -> {cad} minutes since this manager's previous "
                    f"heartbeat, longer than the lease's own ttl_minutes "
                    f"({cttl}). Takeover is TIME-BASED, so a manager silent for "
                    f"longer than one TTL has been quiet for longer than the "
                    f"window in which another session may seize the lease from "
                    f"it. The check-in cadence is a DUTY, not a tuning knob. "
                    f"NOTE this grades whether the manager CHECKED IN, never "
                    f"whether it ACTED on what was waiting — see the R7 note in "
                    f"this module for the failure it does not cover.")
            else:
                notes.append(f"R7 ok: {sha[:8]} {cad}min since the previous "
                             f"heartbeat (ttl {cttl})")

    qfails, qnotes = queue_findings(root)
    fails.extend(qfails)
    notes.extend(qnotes)

    if untrailered:
        notes.append(
            f"⚠️ {untrailered} of {len(shas)} commit(s) in range carry NO "
            f"`Claude-Session:` trailer, so their authorship could not be "
            f"established and they were NOT graded. This is a gap in coverage, "
            f"not evidence of compliance.")
    if contradicted:
        notes.append(
            f"⚠️ {contradicted} of {len(shas)} commit(s) in range name a "
            f"lease holder the register places OUT OF OFFICE when they were "
            f"authored, so who was managing could not be established and they "
            f"were NOT graded. Same footing as an untrailered commit: a gap in "
            f"coverage, not evidence of compliance, and not an excuse either.")

    # The standing disclosure. Printed whenever this guard reaches a verdict at
    # all, including a clean one, so that no reader prices a graded verdict
    # higher than the evidence under it.
    notes.append(
        "⚠️ WHAT THIS GUARD CANNOT SEE: the `Claude-Session:` trailer is "
        "written by the commit's own author, and so is the author date the "
        "office-window check reads. Together they let the lease register "
        "CONTRADICT a trailer; nothing here can CONFIRM one. A manager that "
        "stamps a live worker's session id inside that worker's own window is "
        "NOT detected — that evasion is open, named, and unguarded, and the "
        "cheapest honest fix is a per-session credential this repo does not "
        "have. Read `clean` as `no violation among the commits whose "
        "authorship held up`, never as `no manager did an item`.")

    if fails:
        return ("violation", fails, notes)
    # `unattributed` DOMINATES `clean` on purpose. A range holding even one
    # commit whose authorship could not be established has not been fully
    # looked at, and rendering that as a clean bill of health is the collapse
    # MI-106 is about.
    if untrailered or contradicted:
        notes.append(f"{graded} manager commit(s) graded of {len(shas)}; "
                     f"{untrailered + contradicted} could not be attributed")
        return ("unattributed", [], notes)
    if graded == 0:
        notes.append(f"{graded} manager commit(s) in {len(shas)} commit(s) graded")
        return ("no_manager_commits", [], notes)
    notes.append(f"{graded} manager commit(s) graded, all inside the surface")
    return ("clean", [], notes)


# --------------------------------------------------------------------------
# Self-test: plant each defect and prove the guard fails on it.
# --------------------------------------------------------------------------
MANAGER = "session_01SELFTESTMANAGER0000"
WORKER = "session_01SELFTESTWORKER00000"

#: A day AFTER the fixture's lease history places MANAGER in office. Chosen to
#: echo the live instance: `5504200c` was authored 2026-09-03T06:13:07Z under a
#: trailer naming a session the register had released the previous evening.
OUT_OF_OFFICE = "2026-09-04T06:13:07+00:00"


def _run(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


#: Every fixture commit is authored HERE unless a case says otherwise: inside
#: the window `_fixture`'s lease history puts MANAGER in office. Before MI-106
#: the author date was irrelevant and the fixture left it as "now"; the
#: office-window check reads it, so it is now part of the fixture's contract.
FIXTURE_WHEN = "2026-09-03T08:15:00+00:00"


def _commit(root: Path, msg: str, session: Optional[str],
            when: str = FIXTURE_WHEN) -> None:
    _run(root, "add", "-A")
    body = msg if session is None else \
        f"{msg}\n\nClaude-Session: https://claude.ai/code/{session}\n"
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "-C", str(root), "commit", "-m", body],
                   check=True, capture_output=True, text=True,
                   env={**os.environ, **env})


#: The `claimed_at` the base fixture's lease carries, so an ordinary heartbeat
#: written by a test CONTINUES that one run rather than accidentally opening a
#: new one. A test that means to write a re-CLAIM passes a different value.
FIXTURE_RUN = "2026-09-03T07:00:00Z"


def _lease(holder: str, hb: str, up_state: str = "held", ttl: int = 90,
           claimed_at: Optional[str] = FIXTURE_RUN,
           claimed_over_state: Optional[str] = None) -> str:
    """One lease revision.

    `claimed_at` defaults to the fixture's single run. Pass a DIFFERENT value
    to write a re-CLAIM (a new run by the same holder), or `None` to omit the
    field entirely — the pre-MI-122 shape, which must still be GRADED rather
    than silently exempted.
    """
    doc = {"schema_version": 1, "state": up_state, "holder": holder,
           "heartbeat_at": hb, "ttl_minutes": ttl,
           "heartbeat_target_minutes": 30}
    if claimed_at is not None:
        doc["claimed_at"] = claimed_at
    if claimed_over_state is not None:
        doc["claimed_over_state"] = claimed_over_state
    return json.dumps(doc, indent=2)


def _sessions(updated_at: str) -> str:
    return json.dumps({"schema_version": 1, "updated_at": updated_at,
                       "sessions": []}, indent=2)


def _fixture(tmp: Path, lease: Optional[str] = None) -> Path:
    """A throwaway repo whose `main` carries the guard and a lease history.

    `lease` overrides the base lease revision — used by the MI-106 case that
    proves an UNTIMESTAMPED register does not blind the guard.
    """
    root = tmp / "repo"
    root.mkdir()
    _run(root, "init", "-q", "-b", "main")
    _run(root, "config", "user.email", "selftest@example.com")
    _run(root, "config", "user.name", "selftest")
    # The guard must exist at the merge-base or every branch grades
    # `predates_guard`.
    _write(root, GUARD_REL, "# stand-in for the guard under test\n")
    _write(root, LEASE_REL,
           lease if lease is not None else _lease(MANAGER, "2026-09-03T08:00:00Z"))
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


def _merge_no_commit(root: Path, other: str) -> None:
    """Stage a merge of `other` WITHOUT committing it.

    The caller then writes whatever the merge resolves by hand and calls
    `_commit`, which finds MERGE_HEAD and produces a real two-parent commit.
    That is the only way to plant the shape MI-122 is about: a commit whose
    FIRST parent is not the line carrying the lease's prior state.
    """
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "--no-commit",
                    other], check=True, capture_output=True, text=True)


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

        # -- 2b. MI-106 PLANT: a MIS-STAMPED trailer. Must NOT be a violation.
        #    The live instance's exact shape: the diff is a worker's, the
        #    trailer names the manager, and the manager was NOT in office when
        #    it was authored. The fact is "we cannot establish who wrote this",
        #    and rendering that as a graded `violation` is MI-106.
        #    ⚠️ THIS IS THE CASE THAT MUST NOT PASS BY BEING QUIET: it asserts
        #    the state is `unattributed` AND that the reason names the register,
        #    so a future change that simply stops grading would fail it.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-misstamp")
        _write(root, "src/runtime/orders.py", "x = 2\n")
        _commit(root, "worker: tweak the order path", MANAGER,
                when=OUT_OF_OFFICE)
        st, fails, notes = check(root, "main")
        assert st == "unattributed", (st, fails)
        assert not fails, ("a contradicted trailer must not produce a graded "
                           "finding", fails)
        joined = "\n".join(notes)
        assert "UNATTRIBUTED" in joined, notes
        assert "CONTRADICTS the trailer" in joined, notes
        cases.append(("manager trailer, manager NOT in office -> unattributed "
                      "(not violation)", st, ""))

        # -- 2c. MI-106 CONTROL: the SAME diff, the SAME trailer, authored
        #    INSIDE the manager's office window. Must still be a VIOLATION.
        #    Without this the plant above is satisfiable by a guard that has
        #    simply stopped enforcing R2.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-in-office")
        _write(root, "src/runtime/orders.py", "x = 2\n")
        _commit(root, "worker: tweak the order path", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        assert "application source" in "\n".join(fails), fails
        cases.append(("same diff, same trailer, manager IN office -> violation",
                      st, "\n".join(fails)))

        # -- 2d. MI-106 CONTROL: an UNTIMESTAMPED register must not BLIND the
        #    guard. If "no office window derivable" meant "nothing graded", the
        #    cheapest way past R2 would be to break the lease file — the
        #    `new-table-wiring-guard` failure with an extra step. It falls back
        #    to the trailer alone and SAYS so.
        shutil.rmtree(root)
        root = _fixture(tmp, lease=json.dumps(
            {"schema_version": 1, "state": "held", "holder": MANAGER,
             "ttl_minutes": 90}, indent=2))
        _branch(root, "control-no-window")
        _write(root, "src/runtime/orders.py", "x = 2\n")
        _commit(root, "manager: tweak the order path", MANAGER,
                when=OUT_OF_OFFICE)
        st, fails, notes = check(root, "main")
        assert st == "violation", (st, fails)
        assert "no office window could be derived" in "\n".join(notes) or \
               "NO office window could be derived" in "\n".join(notes), notes
        cases.append(("register carries no timestamps -> still graded, and "
                      "said out loud", st, "\n".join(fails)))

        # -- 2e. MI-106 CONTROL: the standing disclosure is on EVERY verdict,
        #    including a clean one. A limit printed only on failures is a limit
        #    no reader of a green run ever sees.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-disclosure")
        _write(root, "docs/claude/work/MANAGER-CHECKLIST.json", '{"items": []}\n')
        _commit(root, "manager: keep the checklist", MANAGER)
        st, _, notes = check(root, "main")
        assert st == "clean", st
        assert "WHAT THIS GUARD CANNOT SEE" in "\n".join(notes), notes
        assert "stamps a live worker's session id" in "\n".join(notes), notes
        cases.append(("the trailer's limits are disclosed on a CLEAN run too",
                      st, ""))

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

        # -- 8b. PLANTED (R7): the manager went QUIET for longer than the TTL.
        #    The fixture's base lease heartbeats at 08:00; this one at 11:30 is
        #    210 minutes later, past the 90-minute TTL. The registry is kept
        #    FRESH deliberately so R6 cannot fire and only R7 can — a plant that
        #    trips two rules proves neither.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-cadence")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T11:30:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T11:25:00Z"))
        _commit(root, "manager: lease heartbeat after a long silence", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        joined = "\n".join(fails)
        assert "R7" in joined, fails
        assert "R6" not in joined, ("R6 fired too — the plant is not isolated", fails)
        cases.append(("210min since the previous heartbeat -> violation (R7 only)",
                      st, joined))

        # -- 8c. CONTROL: an ON-CADENCE heartbeat. Must PASS. -----------------
        #    30 minutes — the repo's measured MEDIAN gap. If this ever fails,
        #    the threshold has drifted onto ordinary behaviour.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-cadence")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T08:30:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T08:25:00Z"))
        _commit(root, "manager: lease heartbeat, on cadence", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "clean", (st, fails)
        cases.append(("30min cadence (the measured median) -> clean", st, ""))

        # -- 8d. CONTROL: a HANDOVER is not a lapse. Must PASS. ---------------
        #    A new holder inheriting a long-silent lease must not be failed for
        #    its predecessor's silence.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-handover")
        _write(root, LEASE_REL, _lease(WORKER, "2026-09-03T11:30:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T11:25:00Z"))
        _commit(root, "manager: a new holder claims the lease", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "clean", (st, fails)
        cases.append(("handover after a long silence -> clean (not a lapse)",
                      st, ""))

        # -- 8e. MI-122 PLANT (face A): a re-CLAIM over an EXPIRED lease is
        #    NOT a late check-in. Must PASS -- and must still REPORT the dead
        #    interval. The live instance's exact shape and its exact number:
        #    the base fixture last heartbeats at 08:00 and this claim is at
        #    20:26, so the silence is 746 minutes, the figure cc984fec failed
        #    on. The holder is UNCHANGED, so the pre-existing handover
        #    exemption cannot reach it -- only the RUN can.
        #    ⚠️ THIS CASE MUST NOT PASS BY BEING QUIET. It asserts the note is
        #    present AND names the interval, so a change that "fixes" R7 by
        #    simply not looking fails here instead of passing silently. That
        #    is the whole difference between moving where silence is charged
        #    and making it disappear.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-reclaim")
        _write(root, LEASE_REL,
               _lease(MANAGER, "2026-09-03T20:26:00Z",
                      claimed_at="2026-09-03T20:26:00Z",
                      claimed_over_state="expired"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T20:21:00Z"))
        _commit(root, "manager-lease: claim by the manager over an expired lease",
                MANAGER)
        st, fails, notes = check(root, "main")
        assert st == "clean", (st, fails)
        joined = "\n".join(notes)
        assert "re-CLAIM" in joined, notes
        assert "746 minutes" in joined, ("the dead interval must still be "
                                         "REPORTED, not made to disappear", notes)
        assert "expired" in joined, notes
        cases.append(("re-claim over an expired lease -> clean, dead interval "
                      "still reported", st, ""))

        # -- 8f. MI-122 CONTROL (face A): the SAME 746-minute gap, the SAME
        #    holder, but the SAME RUN -- an ALIVE manager that simply went
        #    quiet. Must still be a VIOLATION. Without this the plant above is
        #    satisfiable by a guard that has stopped grading cadence at all,
        #    and R7's real case (the measured 4% of gaps past one TTL) would
        #    be gone.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-same-run-silence")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T20:26:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T20:21:00Z"))
        _commit(root, "manager: lease heartbeat after a very long silence",
                MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        joined = "\n".join(fails)
        assert "R7" in joined, fails
        assert "R6" not in joined, ("R6 fired too -- the plant is not isolated",
                                    fails)
        cases.append(("746min silence WITHIN one run -> violation (R7 only)",
                      st, joined))

        # -- 8g. MI-122 PLANT (face B): a manager MERGE whose FIRST parent is
        #    the worker branch, carrying a lease 140 minutes stale, while the
        #    SECOND parent carries the manager's actual latest heartbeat. Must
        #    PASS. Reading `sha~1` grades 140 minutes against a 90-minute TTL
        #    and fails a lapse that never happened -- the #10895 blocker.
        #    The manager line steps 08:00 -> 09:10 -> 10:20, each 70 minutes,
        #    deliberately UNDER the TTL so those commits cannot fail on their
        #    own and only the merge is under test.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "manager-line")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T09:10:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T09:05:00Z"))
        _commit(root, "manager: lease heartbeat", MANAGER)
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T10:20:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T10:15:00Z"))
        _commit(root, "manager: lease heartbeat", MANAGER)
        _branch(root, "worker-line")
        _write(root, "src/runtime/orders.py", "x = 9\n")
        _commit(root, "worker: an ordinary change on the worker branch", WORKER)
        _merge_no_commit(root, "manager-line")
        _commit(root, "manager: merge main into the worker branch", MANAGER)
        st, fails, notes = check(root, "main")
        assert st == "clean", (st, fails)
        assert "MERGE" in "\n".join(notes), notes
        cases.append(("manager merge, stale FIRST parent -> clean (previous "
                      "heartbeat read from the right parent)", st, ""))

        # -- 8h. MI-122 CONTROL (face B): the SAME merge shape, but the merge
        #    itself advances the heartbeat to 12:30 -- 130 minutes past the
        #    NEWEST parent (10:20), not merely past the stale one. Must still
        #    be a VIOLATION. Without this, reading every parent could be
        #    "fixed" into never grading a merge at all, and a manager could
        #    launder any silence through a merge commit.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "manager-line")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T09:10:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T09:05:00Z"))
        _commit(root, "manager: lease heartbeat", MANAGER)
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T10:20:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T10:15:00Z"))
        _commit(root, "manager: lease heartbeat", MANAGER)
        _branch(root, "worker-line")
        _write(root, "src/runtime/orders.py", "x = 9\n")
        _commit(root, "worker: an ordinary change on the worker branch", WORKER)
        _merge_no_commit(root, "manager-line")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T12:30:00Z"))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T12:25:00Z"))
        _commit(root, "manager: merge main into the worker branch and check in",
                MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        joined = "\n".join(fails)
        assert "R7" in joined, fails
        assert "R6" not in joined, ("R6 fired too -- the plant is not isolated",
                                    fails)
        cases.append(("merge that is genuinely 130min past its NEWEST parent "
                      "-> violation (R7 only)", st, joined))

        # -- 8g6. MI-125 PLANT (R6 face): the SAME wrong-parent shape MI-122
        #    fixed for R7, on R6. R7's 8g plant keeps the registry FRESH on the
        #    manager line, so R6 is never reached there and its `sha~1` read
        #    went unexercised -- which is exactly why this was still open.
        #
        #    main carries a CLEAN manager heartbeat (09:10 over a 09:05
        #    registry). The worker branch is cut BEFORE it and writes its own
        #    registry row at 06:30, while the manager line touches ONLY the
        #    lease -- so git auto-merges and the worker's row survives.
        #    The merged tree therefore reads lease 09:10 against registry 06:30,
        #    160 minutes, over the 90-minute TTL -- and THE MERGE ADVANCED NO
        #    HEARTBEAT: the claim arrived along the SECOND parent. Must be CLEAN.
        #
        #    MEASURED before the fix: `supervision_gap` returned GRADED gap=160
        #    and R6 FAILED. A gentler variant (registry 07:55, gap 75) returned
        #    GRADED and merely passed on the arithmetic -- so a plant that only
        #    read `check()` would have reported "clean" for the wrong reason.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "manager-line")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T09:10:00Z"))
        _commit(root, "manager: lease heartbeat", MANAGER)
        _run(root, "checkout", "-q", "main")
        _run(root, "merge", "-q", "--ff-only", "manager-line")
        _run(root, "checkout", "-q", "-b", "worker-line", "HEAD~1")
        _write(root, "src/runtime/orders.py", "x = 9\n")
        _write(root, SESSIONS_REL, _sessions("2026-09-03T06:30:00Z"))
        _commit(root, "worker: register this sub-session", WORKER)
        _merge_no_commit(root, "main")
        _commit(root, "manager: merge main into the worker branch", MANAGER)
        tip = _git(root, "rev-parse", "HEAD")[1].strip()
        gap_seen, _ttl_seen, _n = supervision_gap(root, tip)
        assert gap_seen is None, (
            "R6 GRADED a merge that advanced no heartbeat -- the wrong-parent "
            "read is back; reading check() alone would hide this whenever the "
            "arithmetic happens to land under the TTL", gap_seen)
        st, fails, _ = check(root, "main")
        assert st == "clean", (st, fails)
        cases.append(("R6: manager merge, stale FIRST parent, registry 160min "
                      "stale -> clean (no heartbeat was advanced here)", st, ""))

        # -- 8g7. MI-125 CONTROL (R6 face): the SAME merge, except the merge
        #    ITSELF advances the heartbeat to 10:30 over that 240-minute-stale
        #    registry. Must STILL be a VIOLATION. Without this, 8g6 could be
        #    "satisfied" by never grading a merge commit at all, and a manager
        #    could launder any supervision lapse through a merge.
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "manager-line")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T09:10:00Z"))
        _commit(root, "manager: lease heartbeat", MANAGER)
        _run(root, "checkout", "-q", "main")
        _run(root, "merge", "-q", "--ff-only", "manager-line")
        _run(root, "checkout", "-q", "-b", "worker-line", "HEAD~1")
        _write(root, "src/runtime/orders.py", "x = 9\n")
        _write(root, SESSIONS_REL, _sessions("2026-09-03T06:30:00Z"))
        _commit(root, "worker: register this sub-session", WORKER)
        _merge_no_commit(root, "main")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T10:30:00Z"))
        _commit(root, "manager: merge main into the worker branch and check in",
                MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        joined = "\n".join(fails)
        assert "R6" in joined, fails
        cases.append(("R6: merge that DOES advance the heartbeat over a "
                      "240min-stale registry -> violation", st, joined))

        # -- 8i. MI-122 CONTROL: a lease with NO `claimed_at` on either side
        #    still GRADES. The run cannot be established, and the deliberate
        #    choice is to keep enforcement rather than drop it -- otherwise
        #    deleting one field from the lease would switch R7 off, which is
        #    cheaper than checking in and is exactly the incentive this unit
        #    exists to close.
        shutil.rmtree(root)
        root = _fixture(tmp, lease=_lease(MANAGER, "2026-09-03T08:00:00Z",
                                          claimed_at=None))
        _branch(root, "control-no-claimed-at")
        _write(root, LEASE_REL, _lease(MANAGER, "2026-09-03T11:30:00Z",
                                       claimed_at=None))
        _write(root, SESSIONS_REL, _sessions("2026-09-03T11:25:00Z"))
        _commit(root, "manager: lease heartbeat after a long silence", MANAGER)
        st, fails, notes = check(root, "main")
        assert st == "violation", (st, fails)
        assert "R7" in "\n".join(fails), fails
        assert "could NOT be established" in "\n".join(notes), notes
        cases.append(("no claimed_at -> still graded (enforcement kept)",
                      st, "\n".join(fails)))

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

        # -- 12b. PLANTED (R8): two entries `rebasing` at once. --------------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-queue-herd")
        _write(root, QUEUE_REL, json.dumps({"schema_version": 1, "entries": [
            {"pr": 1, "state": "rebasing"}, {"pr": 2, "state": "rebasing"}]}))
        _commit(root, "manager: two rebasers", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        assert "AT MOST ONE" in "\n".join(fails), fails
        cases.append(("two entries rebasing at once -> violation", st,
                      "\n".join(fails)))

        # -- 12c. PLANTED (R8): `blocked` with no `blocked_on`. --------------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "plant-queue-blocked")
        _write(root, QUEUE_REL, json.dumps({"schema_version": 1, "entries": [
            {"pr": 1, "state": "blocked", "blocked_on": ""}]}))
        _commit(root, "manager: a nameless blocker", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "violation", (st, fails)
        cases.append(("`blocked` with no blocked_on -> violation", st,
                      "\n".join(fails)))

        # -- 12d. CONTROL: exactly one rebaser is the WORKING state. ---------
        shutil.rmtree(root)
        root = _fixture(tmp)
        _branch(root, "control-queue")
        _write(root, QUEUE_REL, json.dumps({"schema_version": 1, "entries": [
            {"pr": 1, "state": "rebasing"}, {"pr": 2, "state": "waiting"},
            {"pr": 3, "state": "blocked", "blocked_on": "depends on #1"},
            {"pr": 4, "state": "merged"}]}))
        _commit(root, "manager: a healthy queue", MANAGER)
        st, fails, _ = check(root, "main")
        assert st == "clean", (st, fails)
        cases.append(("one rebaser, mixed states -> clean", st, ""))

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
        # Loud, and never rendered the same as a real pass. `unattributed` is
        # the THIRD state MI-106 exists for: it says we could not establish who
        # wrote something, which is a different fact from `clean` (nobody broke
        # the rule) and from `violation` (someone did). Collapsing it into
        # either is the defect.
        print("  (authorship could not be established for part of this range — "
              "this is NOT a clean bill of health, and NOT an excuse either)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
