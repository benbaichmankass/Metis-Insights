#!/usr/bin/env python3
# wiring: CARRIED by .github/workflows/probes.yml (daily, `--receipt`), which
# is the one scheduled workflow that already checks out with `fetch-depth: 0`
# — mandatory here, because on a shallow clone containment is not computable
# and this probe manufactures a full population of false findings. It still
# REPORTS and gates nothing: the carrier writes docs/claude/STUCK-BRANCHES.json
# and pages only on a TRANSITION, and the judgement about what to do with a
# stuck branch stays a human one.
#
# ⚠️ IT WAS `# wiring: manual-only` UNTIL 2026-09-12 AND THAT MARKER WAS TRUE,
# which is precisely why nobody saw the 181. Do not restore it while the
# workflow step exists: `check_unwired_artifacts.py` accepts the marker as a
# justification, so a stale one would excuse the carrier away again.
"""Which `automation/*` branches never landed, and how far behind they are.

WHY THIS EXISTS
---------------
Measured 2026-08-30: **7 of 7 `automation/*` branches were unmerged into
`main`**, two of them carrying open PRs a day old, and four carrying no open PR
at all. Nobody had noticed, because the only way to see it is to list branches
by hand.

THE MECHANISM, established rather than assumed. PRs #10398 and #10407 were both
red on exactly two checks:

    guards      -> session-brief-guard: CLAUDE.md's SESSION-BRIEF block is STALE
    pytest-run  -> 5 failures, all in tests/test_exit_reason_reclassify_on_late_price.py

Neither had anything to do with what those PRs changed (a calendar snapshot and
a 9-line append). Both were conditions of their BASE, `76d14af5`, and both were
fixed on `main` afterwards — verified by running each at each commit:

    76d14af5  session-brief --check -> exit 1 ;  that test file -> 5 failed
    15c192bc  session-brief --check -> exit 0 ;  that test file -> 11 passed

So the PRs inherited a transient red base. **And they can never recover**, because
nothing updates an automation branch after it is opened: on both, `updated_at`
was byte-equal to `created_at`. Checks never re-run, so auto-merge waits forever
on a frozen snapshot of a base that no longer exists. `main` got fixed; the PRs
never found out.

That is the class: **a transient red base permanently strands an auto-merge
branch, silently.** It is the M40 R2 failure — the producer ran, the rows exist,
nobody can read them on `main` — wearing different clothes.

WHAT THIS DOES *NOT* DO, DELIBERATELY
-------------------------------------
It does **not** diagnose *why* a branch is stuck, and it does not claim a red
check is base-inherited. Establishing that took running the failing check at two
commits; a script guessing it from `behind_by` would be asserting a cause it
never measured — the UNPROVENANCED DIAGNOSTIC OUTPUT class this repo already
enforces against. This surfaces candidates and states the denominator. A human
or a session diagnoses.

⚠️ `landed` WAS UNREACHABLE IN THIS REPO, AND THAT IS WHY THE PROBE ALARMED ON
EVERYTHING (fixed 2026-09-12, MI-280 U6)
-----------------------------------------------------------------------------
``classify`` decided ``landed`` from ``git merge-base --is-ancestor`` ALONE.
**This repo squash-merges every PR** — `claude-pr-automerge` uses
``mergeMethod: SQUASH`` and ``pulls.merge(merge_method='squash')``, and
`CLAUDE.md` states the trap in terms: *"`merge-base --is-ancestor` answers NO for
every correctly-merged commit"*. So a branch that landed perfectly fell through
to the age test and was reported ``stuck``.

MEASURED against the live remote, 2026-09-12: **POPULATION 184 branches ->
stuck 184 · landed 0 · unknown 0.** A probe whose healthy state fires ZERO times
cannot distinguish a stranded branch from a landed one.

⚠️ AND THE OBVIOUS CONCLUSION FROM THAT IS WRONG — CORRECTED AGAINST MY OWN
EXPECTATION RATHER THAN PUBLISHED. "landed fires zero times" does NOT mean 184
branches were misgraded. Re-measured on a FULL clone, deployed vs fixed over the
same 184: **184 stuck -> 181 stuck + 3 landed.** The squash trap explains
**3 of 184 (1.6%)**, not the pile: merged automation branches are normally
pruned, so the survivors are mostly genuinely unlanded. The three are
`automation/grade-order-packages-{28025099518,28027973367,28037490394}`, each
`all 1 commit(s) patch-equivalent`. **181 automation branches are genuinely
stranded**, which corroborates the class rather than dissolving it.

⚠️ WHERE THE DEFECT IS TOTAL RATHER THAN 1.6% IS ON A SHALLOW CLONE, AND THAT IS
THE CASE ANY CARRIER WOULD HIT. `actions/checkout@v4` defaults to
`fetch-depth: 1`. Measured on this container while shallow: the deployed code
reports **stuck 184 · unknown 0** — 184 CONFIDENT findings over a population
whose containment is not knowable from a truncated history — while the fixed
code reports **stuck 0 · unknown 184**, each row naming the remedy. So the
honest reading is: a small real misgrade today, and a 100% false-confidence
failure the moment this probe is put on a runner with a default checkout.

ESTABLISHED BY A CONTROLLED EXPERIMENT, not by reading the code — one branch,
one payload, squash-merged into a local `main`:

    git merge-base --is-ancestor branch main  -> exit 1   ("not landed")
    git cat-file -e main:payload.json         -> present  (it IS landed)
    git diff --name-only main branch          -> 0 files
    git cherry main branch                    -> "- <sha>" (patch-equivalent)

and the same branch merged with a MERGE COMMIT returns exit 0, which is the case
ancestry does handle.

⚠️ AND THE SELF-TEST'S OWN DOCSTRING PROMISED THE CONTROL THAT WOULD HAVE CAUGHT
THIS — *"The load-bearing one is the POSITIVE control: if a known-landed branch
stops classifying as `landed` ... it short-circuits"* — while the body contained
**no such control**. Prose describing a check that is not there is worse than
silence, because it is read as coverage. The control now exists and builds a
REAL squash-merged repository rather than asserting the rule.

CONTAINMENT IS NOW A FOUR-STATE READ (``containment``):
  ``ancestor``          — an ancestor of the shared ref. Decisive.
  ``patch_equivalent``  — every commit's patch-id is already upstream, which is
                          what a squash merge produces. Decisive.
  ``not_contained``     — computed, and the branch carries commits upstream does
                          not. Only THIS state may become `stuck`.
  ``undecidable``       — ⚠️ **we could not look.** Includes a SHALLOW clone,
                          where `git cherry`'s `+` means *"the upstream range is
                          truncated"* at least as often as *"not contained"*.
                          Never folded into `not_contained`; a shallow container
                          must report `unknown`, not manufacture 184 findings.

FIVE STATES, NEVER COLLAPSED
----------------------------
  ``landed``        — an ancestor of the shared ref. Nothing to do.
  ``in_flight``     — unmerged and YOUNGER than --stale-hours. Not a finding:
                      a branch opened ten minutes ago is supposed to be here.
  ``stuck``         — unmerged and older than --stale-hours. The candidate.
  ``no_remote``     — named but absent from the remote (already deleted).
  ``unknown``       — WE COULD NOT LOOK (a git call failed). Never folded into
                      any of the above, and never reported as "not stuck".
"""
from __future__ import annotations

import argparse
import json
import pathlib
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

LANDED, IN_FLIGHT, STUCK, NO_REMOTE, UNKNOWN = (
    "landed", "in_flight", "stuck", "no_remote", "unknown")

DEFAULT_PREFIX = "automation/"
DEFAULT_STALE_HOURS = 6.0


@dataclass
class Row:
    branch: str
    state: str
    age_hours: Optional[float]
    behind: Optional[int]
    detail: str


def _git(*args: str) -> Optional[str]:
    """Run git; return None on failure — 'we could not look', never ''."""
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def list_branches(prefix: str) -> Optional[List[str]]:
    out = _git("ls-remote", "--heads", "origin", f"refs/heads/{prefix}*")
    if out is None:
        return None
    names = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            names.append(parts[1].removeprefix("refs/heads/"))
    return sorted(names)


#: Why a `no_remote` row is what it is. Three states, never collapsed — and
#: the distinction decides whether the whole receipt is trustworthy.
REFETCH_NOT_NEEDED = "not_needed"   # every branch resolved on the first pass
REFETCH_FETCHED = "fetched"         # we re-fetched; what is still absent is GONE
REFETCH_FAILED = "fetch_failed"     # we could not re-fetch — we did not look


def fetch_prefix(prefix: str) -> bool:
    """Fetch every `prefix*` head into `refs/remotes/origin/`. True on success.

    ⚠️ THIS EXISTS BECAUSE OF A MEASURED RACE, not a hypothetical one. Running
    the probe live on 2026-09-12, `automation/work-digest-34702634206-1` came
    back from `git ls-remote` and had no remote-tracking ref: it was pushed
    after this clone's last fetch. One such branch is enough to make the whole
    population read `partial` — so without this, a single branch created in the
    seconds between the checkout and the run would block the receipt FOREVER,
    which is a carrier that looks armed and never produces: the exact failure
    class this receipt was built to end.

    The refspec is EXPLICIT rather than relying on `remote.origin.fetch`,
    because `actions/checkout` narrows that config to the ref it checked out,
    so a bare `git fetch origin` can legitimately bring back nothing.
    """
    return _git("fetch", "--quiet", "origin",
                f"+refs/heads/{prefix}*:refs/remotes/origin/{prefix}*") is not None


def state_for_age(age_hours: float, stale_hours: float) -> str:
    """`stuck` once a branch is at least `stale_hours` old, else `in_flight`.

    Extracted so the self-test can CALL it. The first version inlined this in
    ``classify`` and the self-test recomputed the same expression itself — which
    passes whatever the real code does, the vacuous-control shape this repo
    keeps catching. ruff spotted the leftover unused fixture that gave it away.
    """
    return STUCK if age_hours >= stale_hours else IN_FLIGHT


ANCESTOR = "ancestor"
PATCH_EQUIVALENT = "patch_equivalent"
NOT_CONTAINED = "not_contained"
UNDECIDABLE = "undecidable"
CONTAINMENT_STATES = (ANCESTOR, PATCH_EQUIVALENT, NOT_CONTAINED, UNDECIDABLE)
CONTAINED = (ANCESTOR, PATCH_EQUIVALENT)


def repo_is_shallow() -> Optional[bool]:
    """True / False / None — and None is 'we could not look', not 'full'."""
    out = _git("rev-parse", "--is-shallow-repository")
    if out is None:
        return None
    return out.strip() == "true"


def containment(sha: str, shared_ref: str,
                shallow: Optional[bool] = None) -> tuple[str, str]:
    """Is this branch's WORK already on `shared_ref`? Four states, never merged.

    Ancestry alone is not the question, because a squash merge lands the content
    and discards the commit — see the module docstring for the experiment. The
    patch-id check (`git cherry`) is what recognises that, and it is decisive
    ONLY on a full clone: on a shallow one the upstream range is truncated, so a
    `+` cannot be told apart from *"we cannot see that far back"*.
    """
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", sha, shared_ref],
                         capture_output=True, text=True)
    if anc.returncode == 0:
        return ANCESTOR, f"ancestor of {shared_ref}"
    if anc.returncode not in (0, 1):
        # ⚠️ DEFENCE IN DEPTH, AND A MUTATION RUN PROVED IT IS EXACTLY THAT.
        # Removing this line does not change the VERDICT: a sha that breaks
        # `merge-base` also breaks `git cherry` below, which refuses with the
        # same `undecidable`. Measured side by side — original
        # "git merge-base failed", mutant "git cherry failed", both undecidable.
        # So that mutation is WEAK (behaviourally a no-op) and is recorded as
        # neither an escape nor a catch. The line stays because it names the
        # failing call, and a refusal that says WHICH read failed is worth one
        # line; it is not load-bearing and must not be scored as if it were.
        return UNDECIDABLE, "git merge-base failed — we could not look"

    out = _git("cherry", shared_ref, sha)
    if out is None:
        return UNDECIDABLE, "git cherry failed — we could not look"
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # NOTE: there is deliberately no special case for an EMPTY range. A branch
    # that is not an ancestor always has at least one commit `git cherry` can
    # list, so the empty case is unreachable — and a mutation run proved it:
    # flipping its verdict changed nothing any control could see. Dead code that
    # no control can pin is worse than no code, because it reads as handled.
    # `all([])` is True, which is the same (correct) verdict anyway.
    if all(ln.startswith("-") for ln in lines):
        return (PATCH_EQUIVALENT,
                f"all {len(lines)} commit(s) patch-equivalent to {shared_ref} "
                f"(the shape a squash merge leaves)")

    if shallow is None:
        shallow = repo_is_shallow()
    if shallow is not False:
        why = ("the clone is SHALLOW" if shallow
               else "we could not establish whether the clone is shallow")
        return (UNDECIDABLE,
                f"{sum(1 for ln in lines if ln.startswith('+'))} commit(s) read "
                f"as absent from {shared_ref}, but {why}, so the upstream range "
                f"is truncated and `+` cannot be told apart from 'we cannot see "
                f"that far back'. Re-run with a full clone (fetch-depth: 0)")
    return (NOT_CONTAINED,
            f"{sum(1 for ln in lines if ln.startswith('+'))} commit(s) absent "
            f"from {shared_ref} on a FULL clone")


def classify(branch: str, shared_ref: str, stale_hours: float,
             now: Optional[datetime] = None,
             shallow: Optional[bool] = None) -> Row:
    sha = _git("rev-parse", f"refs/remotes/origin/{branch}")
    if sha is None:
        return Row(branch, NO_REMOTE, None, None,
                   "no remote-tracking ref (fetch first, or it was deleted)")

    cstate, cwhy = containment(sha, shared_ref, shallow=shallow)
    if cstate in CONTAINED:
        return Row(branch, LANDED, None, 0, cwhy)
    if cstate == UNDECIDABLE:
        # ⚠️ NOT `stuck`. Before this existed, every branch on a squash-merging
        # repo fell straight through to the age test and 184 of 185 were
        # reported stranded.
        return Row(branch, UNKNOWN, None, None, cwhy)

    iso = _git("show", "-s", "--format=%cI", sha)
    if iso is None:
        return Row(branch, UNKNOWN, None, None,
                   "could not read the commit date — we could not look")
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return Row(branch, UNKNOWN, None, None, f"unparseable commit date {iso!r}")

    ref_now = now or datetime.now(timezone.utc)
    age = (ref_now - when).total_seconds() / 3600.0

    behind = None
    cnt = _git("rev-list", "--count", f"{sha}..{shared_ref}")
    if cnt is not None and cnt.isdigit():
        behind = int(cnt)

    state = state_for_age(age, stale_hours)
    b = "unknown" if behind is None else str(behind)
    return Row(branch, state, age, behind,
               f"unmerged; {age:.1f}h old; {b} commit(s) behind {shared_ref}")


# ── THE RECEIPT: what a CARRIER writes, and what a READER grades ────────────
#
# The probe above answers "which branches are stranded RIGHT NOW". That answer
# was knowable and known by nobody, because nothing ran it
# (BL-20260912-THE-STUCK-BRANCH-PROBE-IS-WIRED-TO-NOTHING-SO-181-STRANDED-AUTOMATION-BRANCHES-ARE-SEEN-BY-NOBODY): measured
# 2026-09-12 on a full clone, POPULATION 184 -> 181 stuck, and the only thing
# on any cadence that looks at `automation/*` at all is
# ``render_due_list.src_unlanded_automation``, which lists **open PRs** — a
# different population, blind by construction to a branch whose PR was closed
# or never opened.
#
# ⚠️ THE CARRIER MUST NOT PAGE ON THE STANDING COUNT. 181 rows every morning is
# the desensitised alarm this repo has already paid for twice, so what is worth
# reporting is the TRANSITION: a branch that newly stranded, or one that
# cleared. That is why the receipt carries the SET and not just the number — a
# branch clearing while another strands leaves the count at 181 and is still a
# real change.

RECEIPT_SCHEMA = 1

#: Receipt read states. `partial` is *we could not see the whole population*,
#: and it is NEVER folded into `complete`: a receipt written from a truncated
#: read would become the baseline, and the next full read would then report
#: every branch it could not see last time as newly stranded.
READ_COMPLETE, READ_PARTIAL = "complete", "partial"

#: Transition states, never collapsed.
FIRST_RUN = "first_run"            # no baseline exists — NOT "nothing changed"
NO_CHANGE = "no_change"
CHANGED = "changed"
DEGRADED = "degraded"              # the CURRENT read is partial — we could not look
BASELINE_UNREADABLE = "baseline_unreadable"  # a baseline exists and will not parse
TRANSITION_STATES = (FIRST_RUN, NO_CHANGE, CHANGED, DEGRADED, BASELINE_UNREADABLE)

#: The transitions a carrier should make noise about. `first_run` and
#: `no_change` are deliberately absent — see the warning above.
PAGING_STATES = (CHANGED, DEGRADED, BASELINE_UNREADABLE)


def build_receipt(rows: List[Row], *, generated_at: str, prefix: str,
                  shared_ref: str, stale_hours: float,
                  refetch: str = REFETCH_NOT_NEEDED) -> dict:
    """The committed artifact. Carries the SET, the counts, and the read state.

    ``read_state`` is ``partial`` — *we could not see the whole population* —
    under EITHER of two conditions, and they are different facts:

    * any ``unknown`` row. On a shallow clone containment is not computable, so
      every branch lands here; that is the documented trap, which is why
      `probes.yml` pins ``fetch-depth: 0``.
    * any ``no_remote`` row **whose absence we did not confirm**. A name that
      ``git ls-remote`` returned and the clone cannot resolve is either a
      branch pushed since the last fetch or one deleted since the listing, and
      those are opposite readings. ``refetch=REFETCH_FETCHED`` says we asked
      the remote again and it is still absent, i.e. genuinely gone — that does
      not degrade the read. ``REFETCH_FAILED`` and ``REFETCH_NOT_NEEDED`` with
      ``no_remote`` rows present do, because then we never established which.
    """
    counts = {st: 0 for st in (STUCK, IN_FLIGHT, LANDED, NO_REMOTE, UNKNOWN)}
    for r in rows:
        counts[r.state] = counts.get(r.state, 0) + 1
    unresolved_unconfirmed = 0 if refetch == REFETCH_FETCHED else counts[NO_REMOTE]
    degraded = counts[UNKNOWN] + unresolved_unconfirmed
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": generated_at,
        "prefix": prefix,
        "shared_ref": shared_ref,
        "stale_hours": stale_hours,
        "population": len(rows),
        "refetch": refetch,
        "read_state": READ_COMPLETE if degraded == 0 else READ_PARTIAL,
        "read_note": (
            ("every branch in the population was graded"
             + (f"; {counts[NO_REMOTE]} confirmed absent from the remote after "
                "a re-fetch (deleted between the listing and the resolve)"
                if counts[NO_REMOTE] else ""))
            if degraded == 0 else
            f"{counts[UNKNOWN]} unknown + {unresolved_unconfirmed} unconfirmed "
            f"no_remote of {len(rows)} (refetch={refetch}) — we could not look "
            f"at all of them; the usual cause is a shallow checkout "
            f"(needs fetch-depth: 0)"
        ),
        "counts": counts,
        "stuck": sorted(r.branch for r in rows if r.state == STUCK),
    }


def _stuck_set(receipt: Any) -> Optional[set]:
    """The stuck SET out of a receipt, or None — *we could not read it*."""
    if not isinstance(receipt, dict):
        return None
    got = receipt.get("stuck")
    if not isinstance(got, list) or not all(isinstance(x, str) for x in got):
        return None
    return set(got)


def transition(previous: Any, current: dict) -> dict:
    """Grade this run against the last committed receipt. Five states.

    ⚠️ THE ORDER OF THESE TESTS IS LOAD-BEARING. A ``partial`` CURRENT read is
    graded ``degraded`` BEFORE any comparison, because comparing a truncated
    set against a complete baseline reports every branch we failed to fetch as
    ``cleared`` — a confident, wrong, and reassuring answer, which is the worst
    of the three available outcomes.
    """
    if current.get("read_state") != READ_COMPLETE:
        return {"state": DEGRADED, "added": [], "cleared": [],
                "note": ("this run could not see the whole population: "
                         + str(current.get("read_note", "no note")) +
                         ". No comparison is possible and the baseline is left "
                         "untouched.")}
    if previous is None:
        return {"state": FIRST_RUN, "added": [], "cleared": [],
                "note": (f"no baseline receipt yet; recording "
                         f"{len(current.get('stuck', []))} stuck branch(es) as "
                         f"the first one. This is NOT a claim that nothing "
                         f"changed.")}
    prev = _stuck_set(previous)
    if prev is None:
        return {"state": BASELINE_UNREADABLE, "added": [], "cleared": [],
                "note": ("a baseline receipt exists and its `stuck` list will "
                         "not parse, so nothing can be compared against it. "
                         "This is a broken instrument, not a quiet one.")}
    cur = set(current.get("stuck", []))
    added, cleared = sorted(cur - prev), sorted(prev - cur)
    if not added and not cleared:
        return {"state": NO_CHANGE, "added": [], "cleared": [],
                "note": f"the same {len(cur)} branch(es) as the last receipt"}
    return {"state": CHANGED, "added": added, "cleared": cleared,
            "note": (f"{len(added)} newly stranded, {len(cleared)} cleared "
                     f"(count {len(prev)} -> {len(cur)})")}


def _selftest() -> int:
    """Planted controls. The load-bearing one is the POSITIVE control: if a
    known-landed branch stops classifying as `landed`, every other verdict here
    is meaningless and the probe is broken, so it short-circuits."""
    fails: List[str] = []

    # Boundary, exercising the REAL function rather than a copy of its rule.
    for age, want in ((0.0, IN_FLIGHT), (5.9, IN_FLIGHT),
                      (6.0, STUCK), (48.0, STUCK)):
        got = state_for_age(age, DEFAULT_STALE_HOURS)
        if got != want:
            fails.append(f"age {age}h should be {want}, got {got}")

    # `unknown` must never be reported as a clean state.
    if UNKNOWN in (LANDED, IN_FLIGHT):
        fails.append("`unknown` collapsed into a clean state")

    # An absent branch is NOT the same as a stuck one.
    if NO_REMOTE == STUCK:
        fails.append("`no_remote` collapsed into `stuck`")

    # ── THE POSITIVE CONTROL THIS DOCSTRING PROMISED AND DID NOT HAVE ───────
    # A REAL repository with a REAL squash merge. Asserting the rule instead is
    # what let `landed` sit unreachable: the rule was never the thing in doubt,
    # the repo's merge STYLE was.
    import shutil
    import tempfile
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="stuckbranch-selftest-")
    try:
        def g(*a):
            return subprocess.run(["git", *a], cwd=tmp, capture_output=True,
                                  text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        pathlib.Path(tmp, "f.txt").write_text("base\n")
        g("add", "-A")
        g("commit", "-qm", "base")

        g("checkout", "-qb", "landed-branch")
        pathlib.Path(tmp, "payload.json").write_text("the payload\n")
        g("add", "-A")
        g("commit", "-qm", "payload")
        landed_sha = g("rev-parse", "HEAD").stdout.strip()

        g("checkout", "-qb", "stranded-branch", "main")
        pathlib.Path(tmp, "other.json").write_text("never landed\n")
        g("add", "-A")
        g("commit", "-qm", "stranded")
        stranded_sha = g("rev-parse", "HEAD").stdout.strip()

        g("checkout", "-q", "main")
        g("merge", "-q", "--squash", "landed-branch")
        g("commit", "-qm", "chore(ops): payload (#999)")

        # `classify` reads refs/remotes/origin/<branch> — the fixture must
        # provide them, or every row grades `no_remote` and the controls pass
        # for the wrong reason.
        g("update-ref", "refs/remotes/origin/landed-branch", landed_sha)
        g("update-ref", "refs/remotes/origin/stranded-branch", stranded_sha)
        os.chdir(tmp)
        # (a) ancestry alone must FAIL on the squash-merged branch — this is the
        #     defect, asserted so nobody "simplifies" containment back to it.
        anc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", landed_sha, "main"],
            capture_output=True)
        if anc.returncode == 0:
            fails.append("the squash fixture did not reproduce: ancestry passed, "
                         "so this control proves nothing about the real defect")
        if not pathlib.Path(tmp, "payload.json").exists():
            fails.append("the squash fixture did not land the payload")

        # (b) THE POSITIVE CONTROL: the squash-merged branch must read LANDED.
        st, why = containment(landed_sha, "main", shallow=False)
        if st != PATCH_EQUIVALENT:
            fails.append(f"a SQUASH-MERGED branch must be {PATCH_EQUIVALENT}, "
                         f"got {st} ({why}) — `landed` is unreachable again")
        row = classify("landed-branch", "main", 0.0, shallow=False)
        if row.state != LANDED:
            fails.append(f"classify() on a squash-merged branch must be {LANDED}, "
                         f"got {row.state} ({row.detail})")

        # (c) THE NEGATIVE CONTROL: a genuinely stranded branch is NOT landed.
        #     Without this, returning `landed` unconditionally would pass (b).
        st2, _ = containment(stranded_sha, "main", shallow=False)
        if st2 != NOT_CONTAINED:
            fails.append(f"a genuinely unmerged branch must be {NOT_CONTAINED}, "
                         f"got {st2} — the probe cannot find anything any more")
        row2 = classify("stranded-branch", "main", 0.0, shallow=False)
        if row2.state != STUCK:
            fails.append(f"classify() on an unmerged branch must be {STUCK}, "
                         f"got {row2.state}")

        # (d) SHALLOWNESS REFUSES rather than manufacturing a finding.
        st3, why3 = containment(stranded_sha, "main", shallow=True)
        if st3 != UNDECIDABLE:
            fails.append(f"on a SHALLOW clone an absent commit must be "
                         f"{UNDECIDABLE}, got {st3} — this is the 184-of-185 "
                         f"false-alarm path")
        if st3 == UNDECIDABLE and "SHALLOW" not in why3:
            fails.append("the shallow refusal does not say it is about shallowness")
        # …and shallowness must NOT suppress a decisive answer.
        st4, _ = containment(landed_sha, "main", shallow=True)
        if st4 != PATCH_EQUIVALENT:
            fails.append("shallowness suppressed a DECISIVE patch-equivalent "
                         "read; only the ambiguous case may be refused")

        # (f) UNDECIDABLE must reach `unknown` THROUGH classify, not just out of
        #     containment. Without this, letting `undecidable` fall through to
        #     the age test — the exact 184-of-185 path — escaped the suite.
        row3 = classify("stranded-branch", "main", 0.0, shallow=True)
        if row3.state != UNKNOWN:
            fails.append(f"classify() must turn an undecidable containment into "
                         f"{UNKNOWN}, got {row3.state} — a shallow clone would "
                         f"manufacture findings again")

        # (g) a git call that FAILS (not merely answers 'no') must refuse.
        st6, why6 = containment("0000000000000000000000000000000000000000",
                                "main", shallow=False)
        if st6 != UNDECIDABLE:
            fails.append(f"an unreadable ancestry check must be {UNDECIDABLE}, "
                         f"got {st6} ({why6}) — 'we could not look' collapsed")

        # (e) a merge-COMMIT branch is still `ancestor` — the old path kept.
        g("checkout", "-qb", "mc-main", "HEAD~1")
        g("merge", "-q", "--no-ff", "landed-branch", "-m", "merge")
        st5, _ = containment(landed_sha, "mc-main", shallow=False)
        if st5 != ANCESTOR:
            fails.append(f"a merge-commit branch must still be {ANCESTOR}, got {st5}")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    # ── RECEIPT + TRANSITION CONTROLS ──────────────────────────────────────
    # (h) THE LOAD-BEARING ONE. A PARTIAL current read must be graded
    #     `degraded` BEFORE any set comparison. If this ordering is lost, a
    #     shallow carrier reports every branch it could not fetch as CLEARED —
    #     a confident, wrong, reassuring answer.
    _complete = {"read_state": READ_COMPLETE, "stuck": ["a", "b", "c"]}
    _partial = {"read_state": READ_PARTIAL, "read_note": "shallow",
                "stuck": ["a"]}
    if transition(_complete, _partial)["state"] != DEGRADED:
        fails.append("a PARTIAL current read must grade `degraded` before any "
                     "comparison — a truncated set was compared and 2 branches "
                     "would have been reported as cleared")

    # (i) no baseline is NOT "nothing changed".
    if transition(None, _complete)["state"] != FIRST_RUN:
        fails.append(f"an absent baseline must be {FIRST_RUN}")

    # (j) a baseline that will not parse is a BROKEN instrument, not a quiet one.
    if transition({"stuck": "not-a-list"}, _complete)["state"] != BASELINE_UNREADABLE:
        fails.append(f"an unparseable baseline must be {BASELINE_UNREADABLE}")

    # (k) / (l) the actual comparison, both directions.
    if transition(_complete, dict(_complete))["state"] != NO_CHANGE:
        fails.append(f"an identical set must be {NO_CHANGE}")
    _moved = {"read_state": READ_COMPLETE, "stuck": ["a", "b", "d"]}
    _t = transition(_complete, _moved)
    if _t["state"] != CHANGED or _t["added"] != ["d"] or _t["cleared"] != ["c"]:
        fails.append(f"a changed set must report its added/cleared, got {_t}")

    # (m) THE DESENSITISED-ALARM CONTROL. Paging on the standing count is the
    #     failure this carrier exists to avoid, so it is asserted, not assumed.
    if FIRST_RUN in PAGING_STATES or NO_CHANGE in PAGING_STATES:
        fails.append("the carrier would page on the STANDING COUNT — 181 rows "
                     "every morning is the alarm everyone walks past")

    # (n) `unknown` and `no_remote` must both degrade the receipt. A name that
    #     ls-remote returned and the clone has no ref for means we did not
    #     FETCH it, which is not evidence that branch is fine.
    for _st in (UNKNOWN, NO_REMOTE):
        _r = build_receipt([Row("x", STUCK, 9.0, 0, ""), Row("y", _st, None, None, "")],
                           generated_at="2026-09-12T00:00:00+00:00",
                           prefix="automation/", shared_ref="origin/main",
                           stale_hours=6.0)
        if _r["read_state"] != READ_PARTIAL:
            fails.append(f"a receipt containing a `{_st}` row must be "
                         f"{READ_PARTIAL}, got {_r['read_state']}")

    # (o) A CONFIRMED absence must NOT degrade the read, and an UNCONFIRMED
    #     one must. Without the first half, one branch pushed in the seconds
    #     between the checkout and the run blocks the receipt forever (measured
    #     live: automation/work-digest-34702634206-1). Without the second, an
    #     under-fetched clone reads as a clean empty answer.
    _one_absent = [Row("x", STUCK, 9.0, 0, ""), Row("y", NO_REMOTE, None, None, "")]
    _kw = dict(generated_at="2026-09-12T00:00:00+00:00", prefix="automation/",
               shared_ref="origin/main", stale_hours=6.0)
    if build_receipt(_one_absent, refetch=REFETCH_FETCHED, **_kw)["read_state"] != READ_COMPLETE:
        fails.append("an absence CONFIRMED by a re-fetch must not degrade the "
                     "read — one racing branch would block the receipt forever")
    if build_receipt(_one_absent, refetch=REFETCH_FAILED, **_kw)["read_state"] != READ_PARTIAL:
        fails.append("an UNCONFIRMED absence must degrade the read — a "
                     "under-fetched clone would read as a clean empty answer")

    total = 4 + 2 + 11 + 10
    for f in fails:
        print("FAIL " + f)
    print(f"selftest: {total - len(fails)}/{total} passed")
    return 1 if fails else 0


def _emit_receipt(rows: List[Row], path: pathlib.Path, *, prefix: str,
                  shared_ref: str, stale_hours: float,
                  refetch: str = REFETCH_NOT_NEEDED) -> int:
    """Grade, report, and refresh the committed receipt.

    ⚠️ THE STANDING COUNT IS NOT AN ANNOTATION. Only `changed`, `degraded` and
    `baseline_unreadable` reach the log as a warning; `first_run` and
    `no_change` are recorded and stay quiet, because 181 rows every morning is
    the alarm everyone learns to walk past.

    ⚠️ AND A DEGRADED RUN NEVER OVERWRITES THE BASELINE. Writing a truncated
    set would make the next COMPLETE run report every unseen branch as newly
    stranded, and would destroy the only evidence a reader has that the last
    good read happened. The receipt then ages instead, which is a state the
    due-list grades — so a carrier that has silently gone shallow announces
    itself rather than going quiet.

    Returns 0 even on a finding: this rides the `probes` cadence, whose own
    contract is that the run fails only when the RUNNER is broken.
    """
    current = build_receipt(
        rows, generated_at=datetime.now(timezone.utc).isoformat(),
        prefix=prefix, shared_ref=shared_ref, stale_hours=stale_hours,
        refetch=refetch)
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous = None
    except (OSError, ValueError):
        # A receipt that exists and will not parse is NOT a missing one: saying
        # `first_run` here would silently re-baseline over a broken instrument.
        previous = {"stuck": None}

    verdict = transition(previous, current)
    state = verdict["state"]
    print(f"\ntransition: {state} — {verdict['note']}")
    for b in verdict["added"]:
        print(f"  + newly stranded: {b}")
    for b in verdict["cleared"]:
        print(f"  - cleared: {b}")

    if state == DEGRADED:
        print(f"::error::stuck-branch receipt NOT refreshed — {verdict['note']} "
              f"The carrier must check out with fetch-depth: 0.")
        return 0
    if state in PAGING_STATES:
        print(f"::warning::stuck automation branches CHANGED — "
              f"{verdict['note']}. See {path}.")

    # The transition is carried INTO the artifact, not only into the run log.
    # A verdict that exists only in a job log is one a session cannot reach —
    # this repo's own `probe_actions_log` rows exist because two clears_when
    # literals were printed nowhere else. `render_due_list.src_stuck_branches`
    # reads this field. ⚠️ IT IS THE LAST RUN'S DELTA, NOT A LEDGER: a
    # `changed` is superseded by the next run's `no_change`, so the durable
    # record of every change is this file's own git history, and the due-list
    # surfaces a new stranding for one cadence period. That is stated rather
    # than papered over.
    current["last_transition"] = {
        "state": state,
        "added": verdict["added"],
        "cleared": verdict["cleared"],
        "note": verdict["note"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path} (read_state={current['read_state']}, "
          f"stuck={len(current['stuck'])})")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--shared-ref", default="origin/main")
    ap.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--receipt", metavar="PATH", default=None,
                    help="write/refresh the committed receipt at PATH and "
                         "grade this run against the one already there")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()

    names = list_branches(a.prefix)
    if names is None:
        # An unreadable remote is NOT "no stuck branches".
        print("::error::could not list remote branches — we could not look. "
              "This is NOT a clean result.", file=sys.stderr)
        return 2
    if not names:
        print(f"POPULATION: 0 branch(es) matching {a.prefix}* on origin — "
              f"nothing to classify.")
        return 0

    rows = [classify(n, a.shared_ref, a.stale_hours) for n in names]

    # A name ls-remote returned that this clone cannot resolve is ambiguous —
    # pushed since our last fetch, or deleted since the listing. Ask once,
    # then the answer is established rather than assumed. ONE call for the
    # whole prefix, not one per branch.
    refetch = REFETCH_NOT_NEEDED
    if any(r.state == NO_REMOTE for r in rows):
        refetch = REFETCH_FETCHED if fetch_prefix(a.prefix) else REFETCH_FAILED
        if refetch == REFETCH_FETCHED:
            rows = [classify(r.branch, a.shared_ref, a.stale_hours)
                    if r.state == NO_REMOTE else r for r in rows]
        else:
            print("::warning::could not re-fetch "
                  f"{a.prefix}* — branches that do not resolve stay "
                  "UNCONFIRMED, which degrades the read rather than reading "
                  "as 'not stuck'.", file=sys.stderr)

    by = {}
    for r in rows:
        by.setdefault(r.state, []).append(r)

    print(f"POPULATION: {len(rows)} branch(es) matching {a.prefix}* on origin, "
          f"graded against {a.shared_ref} (stale after {a.stale_hours}h).")
    for state in (STUCK, IN_FLIGHT, LANDED, NO_REMOTE, UNKNOWN):
        got = by.get(state, [])
        if not got:
            continue
        print(f"\n{state}: {len(got)}")
        for r in sorted(got, key=lambda x: -(x.age_hours or 0)):
            print(f"  {r.branch}\n      {r.detail}")

    n_stuck = len(by.get(STUCK, []))
    n_unknown = len(by.get(UNKNOWN, []))
    print(f"\nstuck={n_stuck} · unknown={n_unknown} (unknown is 'we could not "
          f"look', NOT 'not stuck')")
    if a.receipt:
        return _emit_receipt(rows, pathlib.Path(a.receipt), prefix=a.prefix,
                             shared_ref=a.shared_ref, stale_hours=a.stale_hours,
                             refetch=refetch)
    if n_stuck:
        print("::warning::" + f"{n_stuck} automation branch(es) have not landed. "
              "Check each one's PR: a red check on a base that has since been "
              "fixed will NEVER re-run on its own. Updating the branch re-runs it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
