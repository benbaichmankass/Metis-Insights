#!/usr/bin/env python3
"""Every PR declares its TIER and how it intends to LAND — and the declaration is checked.

WHY THIS EXISTS
---------------
Measured on the morning of 2026-09-03: seven of the night shift's PRs sat open,
green and unlanded, waiting on the manager. Three of their bodies blamed the
tooling — *"pr-opener.yml creates every PR as a draft regardless of
`draft:false`"*. **That claim is false.** `pr-opener.yml`'s draft line is
``[ "$(jq -r '.draft // true' "$req")" = "true" ] && draft_flag="--draft"`` —
`draft:false` is honoured; `true` is merely the DEFAULT, and the request files
those sessions wrote asked for `"draft": true`. They asked for drafts and got
drafts.

The real cause is a permissions asymmetry on top of a blanket convention:

  * a sub-session frequently 403s on `update_pull_request`, so it often cannot
    un-draft its own PR, while the manager can; and
  * `scripts/ops/session_registry.py`'s spawn prompt ended, unconditionally and
    at every tier, with *"Open the PR as a DRAFT; the manager merges."*

So Tier-1 work — which `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers says
needs **no** human OK to merge — was routed through a human anyway, by
convention, on every single session. The route that lands it without a manager
already existed and was simply not being used.

TWO MECHANISMS, AND THEY ARE NOT ALTERNATIVES
---------------------------------------------
Read from the two workflows rather than assumed. They answer different
questions, and either one alone leaves the work sitting:

  * ``"draft": false`` (via `pr-opener.yml`, or a direct `create_pull_request`)
    decides READINESS — "this is approved to land". On its own it produces a
    ready, green PR that waits for somebody to click Merge. That is exactly the
    failure being fixed, not a fix for it.
  * ``.github/pr-automerge-requests/<slug>.txt`` decides LANDING —
    `claude-pr-automerge.yml` enables native auto-merge, and GitHub then merges
    **only when the required checks pass**. On its own, against a DRAFT PR, it
    is REFUSED by that workflow's draft refusal — correctly, and this guard does
    not weaken that refusal.

Tier-1 self-landing therefore needs BOTH, and this guard requires both together
(R4/R5/R6). Tier-2 and Tier-3 need a human, so they may have NEITHER (R4, R10).

AND ARMING TAKES THE MERGE SLOT (R13)
-------------------------------------
Arming is not a request to merge, it IS the merge: `claude-pr-automerge.yml`
enables auto-merge and GitHub lands the PR on green with no further act by
anybody. The `PreToolUse` merge-slot guard in `.claude/settings.json` cannot see
that happen — it matches `mcp__github__merge_pull_request` and
`mcp__github__enable_pr_auto_merge`, and arming calls NEITHER, so no hook fires
even in the CLI/desktop runtimes that do load project hooks. R6 therefore
mandates the one landing route on which the slot guard is structurally silent.

R13 puts an ENFORCED CLAIM on that silent route without weakening either rule:
R6 still requires arming, the hook still guards the MCP route unchanged, and the
claim travels by the same `git push` that arms. Being a CI check rather than a
hook, it also holds on Claude Code on the web, where no project hook loads at all
(1,379 consecutive `Hooks: Found 0 total hooks in registry` lines, 2026-08-18 ->
2026-08-20).

  ⚠️ R13 DOES NOT SERIALIZE, and nothing here should be read as claiming it does.
  Per `BL-20260810-MERGE-SLOT-MIRROR-UNWRITABLE-PRE-MERGE`, `merge_slot` lives in
  a committed file, so a claim written on a branch reaches no other session until
  that branch MERGES — by which point the claim is over. Two branches can each
  arm, each write a valid claim, and never see one another. What R13 buys is that
  an armed merge carries an ATTRIBUTABLE, TIMESTAMPED claim where the route
  previously recorded nothing, and that arming with none — or with someone else's
  — FAILS CI rather than being exhorted against. The real-time half needs a slot
  store not gated on merging (that row's option (a), and the same artifact MI-182
  needs); concurrent-merge safety meanwhile rests on branch-protection required
  status checks, where it already rested.

WHY THIS GUARD CAN BITE AT ALL
------------------------------
Auto-merge merges on GREEN. This guard is a required check. So a branch that
arms auto-merge while under-declaring its tier holds *itself* out of `main` by
failing its own guard — no manager, no reminder, no race. The teeth are not an
alarm; they are the merge gate the branch already asked to be judged by.

THE OVERRIDE IS VERIFIED, NOT PRESENCE-ONLY
-------------------------------------------
The lesson of `new-table-wiring-guard`, whose presence-only `# data-wiring:`
marker made the cheapest way to silence a real finding *naming a table that does
not exist*: a guard cheaper to lie to than to satisfy is worse than no guard.
So nothing here is satisfied by a field merely being present.

  * ``tier`` is checked against the DIFF, not taken on trust (R5). Declaring
    tier 1 while touching `config/strategies.yaml` fails.
  * ``hold_reason: changes_landing_machinery`` is checked against the diff too
    (R8) — and the branch's OWN declaration file is EXCLUDED from the machinery
    set, because every branch touches that file and an excuse every branch
    satisfies is not an excuse.
  * ``hold_reason: depends_on_unmerged_pr`` must name a PR number (R9).
  * ``why`` / ``hold_text`` must carry real text (R3/R7). A one-word `"why": "x"`
    is refused.

AND A TIER-2 PR LANDS ON A RECORDED APPROVAL (R15, 2026-09-11)
--------------------------------------------------------------
Operator directive, 2026-09-11: *"the operator does not click things."*

R4 used to bar a Tier-2 diff from `landing: "self"` **whatever its approval
state**. Measured cost: PR #11738 green, mergeable and operator-approved since
2026-09-10T07:52Z — the operator chose `r2_only` on
`DEC-20260910-EXIT-EVAL-60S-REMEDY` from a four-option popup — and unlanded ever
since, waiting on a click that will not come. The decision was recorded; only
the click was missing.

⚠️ **R4'S REASON IS NOT WEAKENED AND MUST NOT BE.** The rule it protects is that
**a change and its own approval must not be the same act** — otherwise a session
declares itself approved and the gate is theatre. What R4 got wrong is the
PROXY: it used *"a human clicks Merge"* to stand for that separation. R15
replaces the proxy with the property itself — **an approval this branch
demonstrably could not have written** — and refuses wherever that cannot be
established.

The record lives at ``docs/claude/work/approvals/<slug>.json`` and must clear
all of:

  (a) the declaration NAMES it (`approved_by`);
  (b) it lives under ``APPROVAL_DIR`` — **the path is the mechanism**, see below;
  (c) it EXISTS at the branch's merge-base with `main`;
  (d) it is BYTE-IDENTICAL at HEAD, and is PARSED FROM THE MERGE-BASE BLOB —
      never from the worktree, so the guard is not one `sed` from a different
      answer;
  (e) NO path under ``APPROVAL_DIR`` appears in the diff at all;
  (f) it names THIS branch — an approval binds to the change it was given for;
  (g) `tier: 2` only. **TIER-3 NEVER SELF-LANDS**, and a `scope_paths` entry
      naming a Tier-3 path does not buy one;
  (h) typed, non-empty `verdict` / `channel` / `decision_id` / `work_object`
      (which must EXIST) / `decided_on` / `decided_by` / `text`;
  (i) `scope_paths` — a **non-empty glob list, checked against the diff** the
      way R5 checks tier. This is the field that makes a recorded scope
      MECHANICAL. A scope in prose grants everything, which is how an `r2_only`
      approval lands an `r1` change.

⚠️ **WHY THE DIRECTORY IS IN `LANDING_MACHINERY`, AND WHY THAT IS THE DESIGN.**
An approval is now a landing CREDENTIAL, so writing one IS changing the landing
route: R12 holds any PR that writes a record for a human read. **Creating a
credential costs one read; using it costs nothing.** That asymmetry is what
closes the TWO-PR CHAIN — a session could otherwise self-land PR A (Tier-1,
docs-only) writing an approval for its own PR B, approving itself in two acts
instead of one.

⚠️ **WHAT R15 DOES *NOT* ESTABLISH, SAID PLAINLY.** It proves an approval was
SEPARATE from the branch it lands — **not that the operator originated it**.
Measured 2026-09-11: every squash-merge on `main` carries the same author
(`Ben`) and committer (`GitHub`) identity, so commit metadata cannot distinguish
a session-written record from an operator-written one. `channel` records a
CLAIM about provenance and is checked only for being in the vocabulary. The
thing that actually stands behind the claim is R12: a human read the PR that
created the record. This sentence travels with the grant, in the note R15 prints
on every admission, so a reader of a landed Tier-2 PR meets it rather than
having to find it here.

AND THE ROUTE MAY NOT LAND A CHANGE TO ITSELF (R12)
---------------------------------------------------
A PR that edits the landing machinery is Tier-1 by the canonical doc's own list
(*"GitHub Actions workflow fixes"*, *"CI scripts"*), so tier is not the
objection — but it may not self-land, because the change and its own approval
would be the same act and a mistake in it disarms the very check that would
have caught the mistake. Such a PR holds, with `changes_landing_machinery`,
and a human reads it once. This file's own PR is the first instance.

⚠️ THE TIER-1 SURFACE IS AN ALLOWLIST, AND THAT POLARITY IS DELIBERATE
----------------------------------------------------------------------
`TIER1_SURFACE` enumerates the Tier-1 EXAMPLES from
`docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers — docs, tests, CI, GitHub
Actions, `comms/`, lint config. A path it does not recognise is **not** thereby
Tier-1; it is a path this guard cannot vouch for, and it blocks self-landing.

That is the opposite polarity from a denylist of dangerous paths, and it is
chosen for the reason this repo keeps writing down about `PROTECTION_REASSERT_ACCOUNTS`
and `BYBIT_GRADED_COVERAGE_ACCOUNTS`: an unrecognised value must not arm
anything. A denylist would let a path nobody thought of self-land onto `main`;
an allowlist makes the unknown case fail closed and cost one line of review.

`TIER2_PATHS` / `TIER3_PATHS` exist ON TOP of that, so the failure message can
say *why* a path is barred by name ("config/strategies.yaml is Tier-3") rather
than only "not in the Tier-1 surface", and so that widening the allowlist by
mistake still trips the named check.

⚠️ AND THE GUARD NEVER CLAIMS A DIFF *IS* TIER-1. It reports the paths it could
not vouch for. Silence from the Tier-2/3 name lists is this guard not
recognising anything — a negative with no denominator — never proof the change
is safe. The session's own `why` is where that judgement is recorded, and a
human reads it on any PR that does not self-land.

SELF-ARMING, WITH NO FLAG TO UNSET
----------------------------------
Requiring a declaration on EVERY PR would red every branch already open on the
day this merges — measured at **6 open PRs** (population: every open PR
returned by `list_pull_requests` state=open, 2026-09-03), whose authoring
sessions are mostly dead and cannot add the file. Failing them is how a guard
gets disabled instead of fixed; `check_pr_queue_watch.py` records that exact
reasoning.

So R11 asks a question no flag can fake: **did this guard exist at the branch's
merge-base with `main`?** A branch cut from a `main` that already carried the
rule had the rule available and must declare. One cut before it reports
`undeclared_predates_guard` — a PASS, printed loudly and counted, never a
silent one. The guard arms itself as those branches drain, and there is nothing
to switch on.

⚠️ THE DANGEROUS DIRECTION IS NOT GRANDFATHERED. R10 — arming auto-merge with no
valid Tier-1 self-land declaration — fires even on a branch that predates the
guard, because arming is an affirmative act performed today through a file the
branch adds today. Age excuses not knowing the rule; it does not excuse asking
to merge without approval.

STATES, NEVER COLLAPSED
-----------------------
  ``not_a_pr``                    — no base ref to diff against (a push /
                                    `--all` run). Nothing was graded. NOT a pass.
  ``undeclared_predates_guard``   — no declaration, and the branch could not
                                    have known. Passes, counted, loud.
  ``undeclared``                  — no declaration and the branch could have
                                    known. FAILS.
  ``declared_self_land``          — Tier-1, armed, diff inside the Tier-1
                                    surface. Lands itself on green.
  ``declared_hold``               — a typed, verified reason to stay held.
  ``declared_needs_approval``     — Tier-2/3, correctly not armed.

Run standalone with ``--base origin/main``, or ``--self-test`` to plant each
defect and prove the guard fails on it.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_pointer  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

LANDING_DIR = ".github/pr-landing"
AUTOMERGE_DIR = ".github/pr-automerge-requests"
GUARD_REL = "scripts/ci/check_pr_landing.py"

# R13. The DURABLE home of the merge slot (docs/claude/coordination-board.md
# § Scope + limits). That doc calls the `🔒 MERGE SLOT CLAIM` comment on the live
# coordination board the authoritative live claim and this file its mirror. R13
# enforces the reachable one; it does not redefine which is authoritative.
#
# ⚠️ WHICH board is live is RESOLVED, never hardcoded — see
# `_board_claim_sentence` below and `scripts/ci/board_pointer.py`. When this
# rule shipped (MI-182 day) #6927 was at GitHub's hard 2500-comment cap and
# writing to it 403'd, so the remedy text below said flatly that the
# authoritative home was unreachable. The board then rotated, that sentence
# became false, and the number it named became a `board-coherence` R3 failure
# on `main` — which is precisely the half-finished-sweep failure R3 exists to
# stop. The remedy now reads the pointer at message time and says which of the
# three board states it found.
#
# ⚠️ Deliberately NOT in LANDING_MACHINERY below. Every self-landing branch must
# now touch this file, so listing it there would make R12 fire on every one of
# them and nothing could ever self-land again — the same trap the
# `.github/pr-automerge-requests/**` note guards against.
SESSION_BOARD = "docs/claude/session-board.json"

# The Tier-1 EXAMPLES from docs/CLAUDE-RULES-CANONICAL.md § Permission Tiers.
# An allowlist: a path not matched here cannot self-land. See the module
# docstring for why this polarity rather than a denylist.
TIER1_SURFACE = [
    "docs/**",
    "tests/**",
    "comms/**",
    ".github/**",
    "scripts/ci/**",
    "scripts/ops/**",
    "scripts/research/**",
    "scripts/reports/**",
    "*.md",
    ".ruff.toml",
    "ruff.toml",
    "pyproject.toml",
    ".gitignore",
]

# Named so a failure can say WHY a path is barred, and so that widening
# TIER1_SURFACE by mistake still trips a named check.
TIER3_PATHS = [
    "config/strategies.yaml",
    "config/accounts.yaml",
    "config/risk_caps.yaml",
    "config/regime_policy.yaml",
    "config/pairs.yaml",
    "config/prop_rulesets/**",
    "src/runtime/orders.py",
    "src/runtime/risk_counters.py",
    "src/units/accounts/execute.py",
    "src/units/accounts/risk.py",
    "src/units/strategies/**",
    "deploy/ict-trader-live.*",
    "deploy/ict-web-api.*",
]
TIER2_PATHS = [
    "src/**",
    "config/**",
    "deploy/**",
    "ml/**",
]

# R15. Where a Tier-2 landing CREDENTIAL lives. One file per approved branch.
#
# ⚠️ THE DIRECTORY IS NOT A CONVENIENCE — ITS PATH IS THE MECHANISM. It is
# listed in LANDING_MACHINERY below, which is what makes an approval cost one
# human read to CREATE and nothing to USE. Moving these records under an
# ordinary `docs/**` path would silently re-open the two-PR forgery chain.
APPROVAL_DIR = "docs/claude/work/approvals"

# A closed vocabulary, the `operator_decision` discipline in
# docs/claude/work/OPEN-PRS.json. `pending` / `none_recorded` are deliberately
# ABSENT: this file is a credential, and a credential that can say "pending" is
# one a reader will misread as a grant.
APPROVAL_VERDICTS = ("approved", "approved_with_conditions")

# HOW the operator answered. Closed, because "how do we know the operator said
# this" is exactly the question a reader of a landed Tier-2 PR needs answered,
# and free text lets it be answered with a shrug.
#
# ⚠️ NEITHER VALUE IS CHECKED AGAINST ANYTHING, AND THAT IS STATED RATHER THAN
# HIDDEN. See `_APPROVAL_RESIDUAL` — the repo cannot distinguish an
# operator-originated record from a session-written one by content, so this
# field records a CLAIM about provenance. What the guard enforces is the
# SEPARATION (clauses c/d/e), not the origin.
APPROVAL_CHANNELS = {
    "decision_round_trip":
        "answered through POST /api/bot/work/decision and committed by "
        "work-decision-commit.yml",
    "operator_conversational_relayed":
        "answered to the operator directly and TRANSCRIBED by a manager "
        "session; `text` must carry the operator's wording verbatim",
    "operator_wrote_it":
        "the operator edited this file themselves",
}

# ⚠️ AN AMBIGUITY IN THE CANONICAL DOC THAT R15 DOES NOT RESOLVE, AND MUST NOT
# RESOLVE SILENTLY IN THE PERMISSIVE DIRECTION.
#
# docs/CLAUDE-RULES-CANONICAL.md § Permission Tiers says the enumerated
# order-path files are "hard-blocked from a self-merge" AND adds "**any unit file
# the live VM consumes on the trading path**", with the merge classified Tier-3
# "set by the merge gate, not the prep". `TIER3_PATHS` enumerates the named
# files; that trailing phrase is BROADER than any list here, and covers files
# like `src/main.py` and `src/runtime/order_monitor.py` that this guard grades
# Tier-2.
#
# Deciding which files are "on the trading path" from a path glob is exactly the
# unvouchable judgement `TIER1_SURFACE`'s allowlist polarity exists to refuse, so
# this guard does NOT invent that list and does NOT claim to have settled the
# question. It is printed on every admission instead, so the person writing the
# record meets it rather than having to find it — and a record whose scope
# reaches the trading path is one whose author has to make that call explicitly.
_TRADING_PATH_CAVEAT = (
    "⚠️ R15 grades Tier-3 by TIER3_PATHS. docs/CLAUDE-RULES-CANONICAL.md "
    "§ Permission Tiers ALSO Tier-3s the merge of 'any unit file the live VM "
    "consumes on the trading path', which is broader than that list and is NOT "
    "resolved here. If this diff touches the live trading path, the approval "
    "record's author owns that judgement — the guard has not made it for them."
)

# Stated here so it is read at the point of use and cannot be lost to a PR body.
_APPROVAL_RESIDUAL = (
    "R15 establishes that an approval was SEPARATE from the branch it lands, "
    "NOT that the operator originated it. Measured 2026-09-11: every "
    "squash-merge on `main` carries the same author and committer identity, so "
    "commit metadata cannot tell a session-written record from an "
    "operator-written one. What closes the gap is that writing a record is "
    "itself a held, human-read PR (R12 via LANDING_MACHINERY)."
)

# For R8 and R12. The branch's OWN declaration file is excluded by the caller —
# every branch writes one, and an excuse every branch satisfies excuses nothing.
#
# ⚠️ `.github/pr-automerge-requests/**` is deliberately NOT here. Writing a
# request file is USING the landing route, not CHANGING it, and every
# self-landing branch writes one — including it would hand the
# `changes_landing_machinery` excuse to exactly the branches that must not have
# it. Editing that directory's README is not machinery either; the machinery is
# the code and the workflows that decide.
LANDING_MACHINERY = [
    ".github/workflows/claude-pr-automerge.yml",
    ".github/workflows/pr-opener.yml",
    ".github/workflows/board-post.yml",
    ".github/pr-landing/*.json",
    "scripts/ci/check_pr_landing.py",
    "scripts/ci/check_automerge_trigger.py",
    "scripts/ops/session_registry.py",
    # ⚠️ ADDED 2026-09-09 (MI-208). This action is a THIRD landing route and was
    # missing from this list, so a change to it could self-land by the very
    # route it edits — which is the one thing R12 exists to prevent. It writes
    # `.github/pr-landing/{slug}.json`, writes the arming file, writes the R13
    # merge-slot claim and calls `gh pr merge --auto --squash` for all 27
    # workflows that use it. That is more landing authority than
    # `pr-opener.yml`, which was already listed.
    #
    # ⚠️ Safe to list, unlike SESSION_BOARD above: an automation branch never
    # MODIFIES these two files, it only runs them. R12 grades the diff, so this
    # fires on a change TO the route and not on every branch that uses it.
    ".github/actions/commit-to-main/action.yml",
    "scripts/ops/claim_merge_slot.py",
    # ⚠️ ADDED 2026-09-09 (WO-20260909-EVERY-PR-IN-THE-REPO-IS-RED). A FOURTH
    # landing route, listed for exactly the reason `commit-to-main` was: it
    # pushes merge commits to `automation/*` branches that already have
    # auto-merge ARMED and re-asserts their R13 merge-slot claims, so a mistake
    # in it LANDS a pull request rather than merely failing to. A change to it
    # must not be approved by the route it changes.
    #
    # ⚠️ Safe to list for the same reason those two are, and NOT the trap
    # SESSION_BOARD above documents: no ordinary branch MODIFIES these files, it
    # is only ever swept BY them. R12 grades the diff, so this fires on a change
    # TO the sweeper and not on every branch the sweeper touches.
    ".github/workflows/stale-automation-sweep.yml",
    "scripts/ops/sweep_stale_automation_prs.py",
    # ⚠️ ADDED 2026-09-11 (WO-20260911-LET-AN-APPROVED-TIER-2-PR-LAND). A Tier-2
    # approval record is now a landing CREDENTIAL: R15 lets a Tier-2 PR
    # self-land against one. So writing one IS changing the landing route, and
    # a PR that writes an approval must not be merged by the route that
    # approval unlocks.
    #
    # ⚠️ THIS IS THE CLAUSE THAT CLOSES THE TWO-PR CHAIN, and it is the whole
    # reason the directory is not simply left under `docs/**`. Without it, a
    # session could self-land PR A (Tier-1, docs-only) writing an approval for
    # its own PR B, then self-land B against it — approving itself in two acts
    # instead of one. With it, PR A holds and a human reads it.
    #
    # ⚠️ Safe to list, and NOT the `SESSION_BOARD` trap above: an ordinary
    # branch never MODIFIES an approval record — R15 forbids it outright
    # (clause d) — so R12 fires on a PR that WRITES an approval and on nothing
    # else. It cannot make every branch un-landable.
    f"{APPROVAL_DIR}/**",
]

HOLD_REASONS = {
    "changes_landing_machinery":
        "this PR edits the landing route itself, so it wants a human read "
        "(VERIFIED against the diff — see R8)",
    "depends_on_unmerged_pr":
        "must land after another PR (must NAME it as #N — see R9)",
    "awaiting_evidence":
        "the change is prepared but an observation must land first; say WHICH",
    "operator_asked_to_hold":
        "an explicit operator instruction; quote it",
    "tier_2_3_needs_approval":
        "Tier-2/Tier-3 work; say what approval is being sought and from whom",
    # ADDED 2026-09-09 (MI-226). R5's own remedy text says "Either narrow the
    # PR, or set `landing: \"hold\"` and let a human read it" -- and until this
    # entry existed there was NO hold_reason that said that. Every other value
    # in this set would have been a FALSE statement about a Tier-1 PR whose only
    # sin is touching a path the allowlist does not enumerate, so the guard's
    # own advice was unfollowable and the only passing move was to lie about
    # why. Found by a PR editing `scripts/check_provenance_consumers.py`: 26
    # guard scripts live in bare `scripts/`, their sibling `scripts/ci/**` is
    # allowlisted, and no truthful declaration existed for any of them.
    #
    # It grants NOTHING. It does not widen TIER1_SURFACE and does not self-land
    # -- it is a hold, so a human still merges. And it is VERIFIED against the
    # diff (R14), so it cannot become a blanket excuse: a PR whose paths are all
    # inside the allowlist may not claim it, because such a PR can simply
    # self-land.
    "unvouchable_paths":
        "Tier-1 work whose diff touches a path outside TIER1_SURFACE, so the "
        "guard cannot certify self-landing and a human merges (VERIFIED against "
        "the diff -- see R14); name the paths",
}

MIN_TEXT = 20


def _match(path: str, globs: list[str]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
        # `dir/**` should match `dir/a` as well as `dir/a/b`.
        if g.endswith("/**") and (path == g[:-3] or path.startswith(g[:-2])):
            return True
    return False


def _git(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def _board_claim_sentence() -> str:
    """Where the live `🔒 MERGE SLOT CLAIM` comment goes — RESOLVED, not hardcoded.

    ⚠️ THE THREE BOARD STATES ARE NEVER COLLAPSED, and the reason is the one
    `board_pointer` itself is built on: a frozen board READS identically to a
    live one, so "we could not look" must never render as "there is no board",
    and neither may render as "no coordination is needed".

    This sentence used to name the board as a literal issue number. That was
    true when it was written and false about twenty hours later, and the stale
    literal is what turned `board-coherence` R3 red on `main`. Resolving it
    here means a rotation stays ONE edit to `docs/claude/board-pointer.json`.

    (Note for anyone editing this docstring: R3 treats a docstring as an
    EXECUTABLE line — only a leading `#` or `//` counts as a comment — so a
    retired board number may not be named here even in prose. The `#`-comment
    block at `SESSION_BOARD` above is where that history lives.)
    """
    code, num, _ptr, why = board_pointer.resolve()
    if code == board_pointer.RESOLVED:
        return (f"This does NOT replace the `🔒 MERGE SLOT CLAIM` comment on the "
                f"live coordination board — #{num}, resolved from "
                f"{board_pointer.POINTER_PATH}. That comment is the authoritative "
                f"live claim and this file is its durable mirror; post both.")
    if code == board_pointer.UNPROVISIONED:
        return (f"⚠️ There is NO live coordination board to post the "
                f"`🔒 MERGE SLOT CLAIM` comment on right now ({why}), so this "
                f"committed claim is the only one anybody can make. That is the "
                f"board being MISSING — it is NOT permission to skip coordinating. "
                f"Rotate one (`board-rotate.yml`) and repoint "
                f"{board_pointer.POINTER_PATH}.")
    return (f"⚠️ WHICH issue is the live coordination board could not be "
            f"resolved ({why}) — that is *we did not look*, NOT *there is no "
            f"board*. Repair {board_pointer.POINTER_PATH}, then post the "
            f"`🔒 MERGE SLOT CLAIM` comment on the board it names.")


def branch_slug(branch: str) -> str:
    """Same derivation `claude-pr-automerge.yml` uses, so the two agree."""
    return re.sub(r"^claude/", "", branch).replace("/", "-")


def current_branch(root: Path) -> Optional[str]:
    # On a `pull_request` event the checkout is a detached merge ref, so
    # `rev-parse --abbrev-ref HEAD` reads `HEAD`. GITHUB_HEAD_REF is the branch.
    for env in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME"):
        v = (os.environ.get(env) or "").strip()
        if v and v != "HEAD":
            return v
    rc, out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if rc == 0 and out and out != "HEAD":
        return out
    return None


def changed_paths(root: Path, base: str) -> Optional[list[str]]:
    rc, mb = _git(root, "merge-base", base, "HEAD")
    if rc != 0 or not mb:
        return None
    rc, out = _git(root, "diff", "--name-only", f"{mb}...HEAD")
    if rc != 0:
        return None
    return [ln for ln in out.splitlines() if ln.strip()]


def guard_existed_at_merge_base(root: Path, base: str) -> Optional[bool]:
    """Could this branch have known the rule? None = we could not look."""
    rc, mb = _git(root, "merge-base", base, "HEAD")
    if rc != 0 or not mb:
        return None
    rc, _ = _git(root, "cat-file", "-e", f"{mb}:{GUARD_REL}")
    return rc == 0


def _added_or_modified(root: Path, base: str, rel: str) -> bool:
    """Present at HEAD and not byte-identical to `base` — the automerge gate's own test."""
    rc_head, head_sha = _git(root, "rev-parse", f"HEAD:{rel}")
    if rc_head != 0:
        return False
    rc_base, base_sha = _git(root, "rev-parse", f"{base}:{rel}")
    if rc_base != 0:
        return True          # absent on base, added here
    return head_sha != base_sha


def slot_claim_state(root: Path, base: str, branch: str) -> tuple[bool, str]:
    """Does THIS branch hold the merge slot, and did it claim it in THIS diff?

    Added-or-modified is load-bearing, for exactly the reason
    `claude-pr-automerge.yml`'s REQUEST GATE gives: a branch that merely merged
    `main` while somebody else's claim sat on it has asked for nothing, and by
    presence alone its diff is indistinguishable from a real claim.
    """
    path = root / SESSION_BOARD
    if not path.exists():
        return (False, f"{SESSION_BOARD} does not exist")
    if not _added_or_modified(root, base, SESSION_BOARD):
        return (False, f"{SESSION_BOARD} is unchanged from `{base}` — this branch "
                       f"claimed nothing; it is carrying whatever `main` already had")
    try:
        slot = json.loads(path.read_text(encoding="utf-8")).get("merge_slot")
    except (OSError, json.JSONDecodeError) as exc:
        return (False, f"{SESSION_BOARD} is unreadable/invalid JSON: {exc}")
    if not isinstance(slot, dict):
        return (False, f"{SESSION_BOARD} carries no `merge_slot` object")
    if slot.get("branch") != branch:
        return (False, f"`merge_slot.branch` is {slot.get('branch')!r}, not "
                       f"{branch!r} — the slot is held by someone else. That is "
                       f"the serialization working, not a technicality to route "
                       f"around; wait for the release")
    for field in ("held_by", "claimed_at"):
        if not str(slot.get(field) or "").strip():
            return (False, f"`merge_slot.{field}` is empty — a claim nobody can "
                           f"attribute or time out is not a claim")
    return (True, "held by this branch")


def _merge_base(root: Path, base: str) -> Optional[str]:
    rc, mb = _git(root, "merge-base", base, "HEAD")
    return mb if rc == 0 and mb else None


def _blob_sha(root: Path, rev: str, rel: str) -> Optional[str]:
    rc, out = _git(root, "rev-parse", f"{rev}:{rel}")
    return out if rc == 0 and out else None


def _blob_text(root: Path, rev: str, rel: str) -> Optional[str]:
    p = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{rel}"],
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


# THE TWO LOAD-BEARING PREDICATES OF R15, extracted so each can be MUTATED in
# isolation and the suite proved to notice (tests/test_pr_landing_tier2_approval.py).
# A negative control that would still pass with the property removed is not
# testing the property — it is testing whatever else happened to refuse.
SEPARATION_STATES = (
    "separate",              # on `main` before this branch, untouched by it
    "touched_by_branch",     # the branch wrote/edited/added SOMETHING here
    "absent_at_merge_base",  # not on `main` — a claim, not an approval
    "merge_base_unreadable", # *we did not look* — never a grant
)


def approval_separation(root: Path, base: str, branch: str, norm: str,
                        changed: list[str]) -> tuple[str, str]:
    """Could THIS branch have written the approval it cites? Never collapsed.

    ⚠️ `merge_base_unreadable` is *we did not look*, and is deliberately its own
    state rather than folded into `absent_at_merge_base`. Both refuse — the
    refusal is the safe direction for a grant — but a reader must be able to
    tell "there is no approval" from "we could not establish whether there is".
    """
    mb = _merge_base(root, base)
    if mb is None:
        return ("merge_base_unreadable", f"no merge-base with `{base}`")
    touched = sorted(p for p in changed if _match(p, [f"{APPROVAL_DIR}/**"]))
    if touched:
        return ("touched_by_branch", ", ".join(touched[:5]))
    sha_mb = _blob_sha(root, mb, norm)
    if sha_mb is None:
        return ("absent_at_merge_base", mb)
    if _blob_sha(root, "HEAD", norm) != sha_mb:
        return ("touched_by_branch", norm)
    return ("separate", mb)


_SESSION_TRAILER = re.compile(r"^Claude-Session:\s*(\S+)\s*$", re.M)


def _commit_sessions(root: Path, commit: str) -> set[str]:
    """The `Claude-Session:` trailers on ONE commit."""
    rc, out = _git(root, "log", "-1", "--format=%B", commit)
    return {m.group(1) for m in _SESSION_TRAILER.finditer(out)} if rc == 0 else set()


def _sessions_in(root: Path, rev_range: str) -> set[str]:
    """`Claude-Session:` trailers over a commit range — WHO wrote these commits.

    ⚠️ MEASURED COVERAGE, 2026-09-11: 10 of the 15 most recent commits on `main`
    carry the trailer (66.7%) — automation commits carry none. So this CANNOT be
    a requirement without refusing legitimate records, and clause (j) is
    therefore built to ADD a refusal on a KNOWN collision and to stay silent on
    the unknown case. It can only ever tighten, never block. Same polarity as
    `CAUSE_MIN_ROWS` in `silent_refusal_alert`, and for the same reason: a check
    that must guess should not be able to deny.

    ⚠️ AND IT IS NOT AN IDENTITY PROOF. A trailer is free text a session writes
    about itself. It is corroboration on top of R12 and R15(c)-(e), not a
    substitute — `check_manager_scope.py` already leans on the same trailer at
    the same level of trust.
    """
    rc, out = _git(root, "log", "--format=%B%x00", rev_range)
    if rc != 0:
        return set()
    return {m.group(1) for m in _SESSION_TRAILER.finditer(out)}


def paths_outside_scope(paths: list[str], scope: list[str]) -> list[str]:
    """Which changed paths the approval does NOT cover. R15(i)'s whole content."""
    return [p for p in paths if not _match(p, scope)]


def approval_state(root: Path, base: str, branch: str, decl: dict,
                   changed: list[str], decl_rel: str,
                   arm_rel: str) -> tuple[list[str], list[str]]:
    """R15 — may this Tier-2 branch land itself? Returns (failures, notes).

    THE PROPERTY THIS IS BUILT ON
    -----------------------------
    R4's reason is sound and is NOT relaxed here: **the change and its own
    approval must not be the same act.** What R4 got wrong is the proxy it used
    for that — it assumed the only way to separate them is a human clicking
    Merge, so it barred every Tier-2 diff from self-landing *whatever its
    approval state*. The live cost, measured 2026-09-11: PR #11738 green,
    mergeable and operator-approved since 2026-09-10T07:52Z, unlanded, waiting
    on a click the operator has said will not come.

    So the test is not "is a human in the loop" but **"could this branch have
    written its own approval?"** — and that is answerable from git alone:

      (c) the record EXISTS at the branch's merge-base with `main`, so it was on
          `main` before this branch contributed anything; and
      (d) it is BYTE-IDENTICAL at HEAD, so the branch did not edit it; and
      (e) NO path under the approvals directory appears in the diff at all, so
          the branch did not add a sibling record either.

    A branch that carries its own approval has not been approved — it has
    ASSERTED approval — and all three clauses refuse exactly that.

    ⚠️ THE RECORD IS PARSED FROM THE MERGE-BASE BLOB, NEVER FROM THE WORKTREE.
    Clause (d) already forbids a diff, but reading the worktree would make the
    verdict depend on a file the branch physically controls, and a guard should
    not be one `sed` away from a different answer. `git show <merge-base>:<path>`
    is the only read.

    ⚠️ AND THE TWO-PR CHAIN IS CLOSED ELSEWHERE, NOT HERE. A session could
    otherwise land PR A (Tier-1, docs-only, self-landing) writing an approval
    for its own PR B. What stops it is that ``APPROVAL_DIR`` is in
    ``LANDING_MACHINERY``, so R12 holds PR A for a human. Creating a credential
    costs one read; using it costs nothing. That asymmetry is the whole design.

    ⚠️ WHAT IT DOES NOT ESTABLISH is stated in `_APPROVAL_RESIDUAL` and repeated
    in the failure text, because a guard that overstates its own reach is how a
    reviewer stops reading the thing it does not cover.
    """
    fails: list[str] = []
    notes: list[str] = []

    # (a) the declaration must POINT at a record.
    ref = str(decl.get("approved_by") or "").strip()
    if not ref:
        fails.append(
            f"R15(a) {decl_rel} declares tier 2 with `landing: \"self\"` but names "
            f"no approval record. Tier-2 work does not land on a session's own "
            f"judgement — add `\"approved_by\": \"{APPROVAL_DIR}/<slug>.json\"` "
            f"pointing at a record ALREADY ON `main`, or set `landing: \"hold\"` "
            f"with `hold_reason: \"tier_2_3_needs_approval\"`. "
            f"See {LANDING_DIR}/README.md.")
        return fails, notes

    # (b) it must live in the protected directory — that path IS the mechanism.
    norm = ref.lstrip("./")
    if not norm.startswith(APPROVAL_DIR + "/") or "/.." in norm or norm.endswith("/"):
        fails.append(
            f"R15(b) `approved_by` is {ref!r}, which is not a file under "
            f"{APPROVAL_DIR}/. That directory is not a convention — it is listed "
            f"in LANDING_MACHINERY, which is what makes WRITING an approval a "
            f"held, human-read PR while USING one is free. A record anywhere "
            f"else could be self-landed by its own author as an ordinary docs "
            f"change, which is the forgery this rule exists to refuse.")
        return fails, notes

    # (c)/(d)/(e) THE MECHANICAL SEPARATION — the heart of the rule.
    sep, detail = approval_separation(root, base, branch, norm, changed)
    if sep == "merge_base_unreadable":
        fails.append(
            f"R15(c) the merge-base with `{base}` could not be read ({detail}), "
            f"so whether {norm} predates this branch is UNKNOWN — *we did not "
            f"look*, which is not the same as approved. Self-landing is refused "
            f"on the unreadable case rather than granted by default; "
            f"`landing: \"hold\"` still works.")
        return fails, notes
    if sep == "touched_by_branch":
        fails.append(
            f"R15(e) this branch's diff touches the approvals directory — "
            f"{detail}. A branch may not write, edit or add ANY approval record "
            f"in the same PR it asks to land: that is a branch approving itself, "
            f"and whether the file it wrote is the one it cites is beside the "
            f"point. Land the record in its own PR first — it will hold under "
            f"R12 for a human read, which is what makes a credential cost "
            f"something to CREATE and nothing to USE — then cite it from a "
            f"clean branch.")
        return fails, notes
    if sep == "absent_at_merge_base":
        fails.append(
            f"R15(c) `approved_by` names {norm}, which does NOT exist at this "
            f"branch's merge-base with `{base}` ({detail[:9]}). An approval that "
            f"is not already on `main` is not an approval this branch can rely "
            f"on — it is a claim the branch is making about itself. Land the "
            f"record first, then merge `main` in and cite it.")
        return fails, notes
    mb = detail

    rc_commit, intro = _git(root, "log", "-1", "--format=%H", mb, "--", norm)
    if rc_commit == 0 and intro:
        notes.append(f"R15 approval read from `main` at {norm} "
                     f"(last touched on main by {intro[:9]}, merge-base {mb[:9]})")

    raw = _blob_text(root, mb, norm)
    if raw is None:
        fails.append(f"R15 {norm} exists at {mb[:9]} but could not be read.")
        return fails, notes
    try:
        rec = json.loads(raw)
    except json.JSONDecodeError as exc:
        fails.append(f"R15 {norm} is not valid JSON at the merge-base: {exc}")
        return fails, notes
    if not isinstance(rec, dict):
        fails.append(f"R15 {norm} must be a JSON object.")
        return fails, notes

    # ---- the record must name THIS branch -----------------------------------
    named = str(rec.get("branch") or "").strip()
    if named != branch:
        fails.append(
            f"R15(f) {norm} approves branch {named!r}, not {branch!r}. An "
            f"approval binds to the change it was given for. A record that "
            f"landed for one branch must not be re-pointed at another by a "
            f"second branch citing it — that is the same act as writing a new "
            f"approval, and it must cost the same human read.")

    # ---- tier: 2 only, and never 3 -----------------------------------------
    rtier = rec.get("tier")
    if rtier == 3:
        fails.append(
            f"R15(g) {norm} declares `tier: 3`. TIER-3 IS OUT OF SCOPE FOR "
            f"SELF-LANDING ENTIRELY — strategy logic, risk caps, sizing, "
            f"account-mode flips and live promotion stay human-gated "
            f"(docs/CLAUDE-RULES-CANONICAL.md § Permission Tiers). There is no "
            f"record that unlocks them and this rule must never be widened to "
            f"create one.")
    elif rtier != 2:
        fails.append(
            f"R15(g) {norm} declares `tier: {rtier!r}`; a landing credential is "
            f"for Tier-2 work only. Tier-1 self-lands under R5 and needs no "
            f"record; Tier-3 never self-lands.")

    verdict = str(rec.get("verdict") or "").strip()
    if verdict not in APPROVAL_VERDICTS:
        fails.append(
            f"R15(h) {norm} `verdict` is {verdict!r}; must be one of "
            + ", ".join(APPROVAL_VERDICTS)
            + ". `pending` and `none_recorded` are deliberately absent from the "
              "vocabulary: this file is a credential, and one that can say "
              "\"pending\" is one a reader will misread as a grant.")

    channel = str(rec.get("channel") or "").strip()
    if channel not in APPROVAL_CHANNELS:
        fails.append(
            f"R15(h) {norm} `channel` is {channel!r}; must be one of "
            + ", ".join(sorted(APPROVAL_CHANNELS))
            + ". It records HOW the operator answered, which is the question a "
              "reader of a landed Tier-2 PR needs answered.")

    for field in ("decision_id", "work_object", "decided_on", "decided_by"):
        if not str(rec.get(field) or "").strip():
            fails.append(f"R15(h) {norm} `{field}` is empty — an approval nobody "
                         f"can trace back to a decision is not auditable.")

    text = str(rec.get("text") or "").strip()
    if len(text) < MIN_TEXT:
        fails.append(
            f"R15(h) {norm} `text` is {len(text)} chars; needs at least "
            f"{MIN_TEXT}. It carries the operator's own wording, which is the "
            f"only thing the typed `verdict` stays checkable against — the "
            f"`operator_decision` discipline in docs/claude/work/OPEN-PRS.json.")

    wo = str(rec.get("work_object") or "").strip()
    if wo and not _blob_sha(root, mb, f"docs/claude/work/objects/{wo}.yaml"):
        fails.append(
            f"R15(h) {norm} names `work_object: {wo}`, which is not a file under "
            f"docs/claude/work/objects/ at the merge-base. A parent that does "
            f"not exist is not a parent — the same refusal "
            f"scripts/ops/session_registry.py makes on a spawn.")

    # ---- (j) WHO wrote the approval, where that is knowable -----------------
    # The commit that last touched the RECORD on `main` — not the merge-base
    # commit, which is merely where this branch happens to be anchored. A
    # root-commit merge-base has no `~1`, so the range form is avoided entirely.
    rc_a, approver_commit = _git(root, "log", "-1", "--format=%H", mb, "--", norm)
    approving = (_commit_sessions(root, approver_commit)
                 if rc_a == 0 and approver_commit else set())
    landing_sessions = _sessions_in(root, f"{mb}..HEAD")
    shared = approving & landing_sessions
    if shared:
        fails.append(
            f"R15(j) the session that landed {norm} is also a session that "
            f"committed on this branch — " + ", ".join(sorted(shared))
            + ". One session cannot both grant and spend a credential, even "
              "across two PRs. (This clause only ever ADDS a refusal: trailers "
              "cover 10 of the 15 most recent commits on `main`, so their "
              "ABSENCE is *we did not look* and is never treated as a pass — "
              "the separation that does the work is R15(c)-(e) plus R12.)")
    elif approving:
        notes.append("R15(j) approval landed by session(s) "
                     + ", ".join(sorted(approving))
                     + "; none of them committed on this branch")
    else:
        notes.append("R15(j) the commit that landed the approval carries no "
                     "`Claude-Session:` trailer — *we did not look*, not a pass. "
                     "The separation that does the work is R15(c)-(e) plus R12.")

    # ---- SCOPE, checked against the diff ------------------------------------
    scope = rec.get("scope_paths")
    if not isinstance(scope, list) or not scope or not all(
            isinstance(s, str) and s.strip() for s in scope):
        fails.append(
            f"R15(i) {norm} `scope_paths` must be a non-empty list of path "
            f"globs. A scope recorded only in prose cannot be compared to a "
            f"diff, so it grants everything — which is how an `r2_only` "
            f"approval would land an `r1` change. This is the field that makes "
            f"the recorded scope MECHANICAL.")
        return fails, notes

    # The three files a branch MUST write to land at all are not 'scope'; they
    # are the landing paperwork R6/R11/R13 demand. Requiring an approval to
    # enumerate them would make every record carry the same three lines and
    # would tempt an author to write a wildcard that swallows the real diff.
    paperwork = {decl_rel, arm_rel, SESSION_BOARD}
    substantive = [p for p in changed if p not in paperwork]
    out_of_scope = paths_outside_scope(substantive, scope)
    if out_of_scope:
        fails.append(
            f"R15(i) {norm} approves "
            + ", ".join(sorted(scope)[:5])
            + f" but the diff changes {len(out_of_scope)} path(s) outside that "
              f"scope — "
            + ", ".join(sorted(out_of_scope)[:5])
            + ". The approval is for a change, not for a branch. Narrow the PR "
              "to what was approved, or obtain a record whose scope covers it.")

    # Tier-3 paths are barred ABSOLUTELY — a scope_paths entry cannot buy one.
    barred3 = [p for p in substantive if _match(p, TIER3_PATHS)]
    if barred3:
        fails.append(
            f"R15(g) the diff touches {len(barred3)} Tier-3 path(s) — "
            + ", ".join(sorted(barred3)[:5])
            + f". No approval record admits these, and {norm} listing them in "
              f"`scope_paths` does not change that: Tier-3 is out of scope for "
              f"self-landing entirely, whatever a record says.")

    if not fails:
        notes.append(
            f"R15 OK — tier-2 self-land admitted by {norm} "
            f"(decision {rec.get('decision_id')}, verdict {verdict}, channel "
            f"{channel}, {len(substantive)} substantive path(s) inside scope). "
            + _APPROVAL_RESIDUAL)
        notes.append(_TRADING_PATH_CAVEAT)
    return fails, notes


def check(root: Path, base: str, branch: Optional[str]) -> tuple[str, list[str], list[str]]:
    """Return (state, failures, notes)."""
    fails: list[str] = []
    notes: list[str] = []

    if not branch:
        return ("not_a_pr", [], ["no branch to grade — nothing was checked here"])

    slug = branch_slug(branch)
    decl_rel = f"{LANDING_DIR}/{slug}.json"
    arm_rel = f"{AUTOMERGE_DIR}/{slug}.txt"

    changed = changed_paths(root, base)
    if changed is None:
        return ("not_a_pr", [],
                [f"could not diff against {base} — nothing was checked here"])

    armed = _added_or_modified(root, base, arm_rel)
    decl_path = root / decl_rel

    # ---------------------------------------------------------------- R11 / R10
    if not decl_path.exists():
        # R10 fires regardless of age: arming is an affirmative act done today.
        if armed:
            return ("undeclared", [
                f"R10 this branch ARMS auto-merge ({arm_rel} is added/modified) "
                f"but carries no landing declaration at {decl_rel}. Arming asks "
                f"GitHub to merge this PR with no human in the loop; the tier "
                f"that permits that is declared, or it is not permitted. "
                f"Write {decl_rel} (see {LANDING_DIR}/README.md)."], notes)
        knew = guard_existed_at_merge_base(root, base)
        if knew is False:
            return ("undeclared_predates_guard", [], [
                f"NO DECLARATION at {decl_rel}, and {GUARD_REL} did not exist at "
                f"this branch's merge-base — the branch was cut before the rule, "
                f"so it is not failed for it. This is a PASS on age, NOT a "
                f"finding that the PR is fine. Merge `main` and declare."])
        if knew is None:
            return ("undeclared_predates_guard", [], [
                f"NO DECLARATION at {decl_rel}, and the merge-base could not be "
                f"read, so whether this branch could have known is UNKNOWN — not "
                f"established as either. Passing on the unreadable case rather "
                f"than failing a branch we did not establish anything about."])
        return ("undeclared", [
            f"R11 no landing declaration at {decl_rel}. This branch was cut from "
            f"a `main` that already carried {GUARD_REL}, so the rule was "
            f"available to it. Every PR states its tier and how it means to "
            f"land; see {LANDING_DIR}/README.md for the four-line file."], notes)

    # ---------------------------------------------------------------- parse
    try:
        decl = json.loads(decl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ("undeclared", [f"R0 {decl_rel} is unreadable/invalid JSON: {exc}"], notes)
    if not isinstance(decl, dict):
        return ("undeclared", [f"R0 {decl_rel} must be a JSON object."], notes)

    tier = decl.get("tier")
    landing = decl.get("landing")
    why = str(decl.get("why") or "").strip()

    # R1 / R2 / R3
    if tier not in (1, 2, 3):
        fails.append(f"R1 {decl_rel} `tier` is {tier!r}; must be 1, 2 or 3.")
    if landing not in ("self", "hold"):
        fails.append(f"R2 {decl_rel} `landing` is {landing!r}; must be "
                     f'"self" (lands itself on green) or "hold" (a human merges).')
    if len(why) < MIN_TEXT:
        fails.append(
            f"R3 {decl_rel} `why` is {len(why)} chars; needs at least {MIN_TEXT}. "
            f"It is the tier judgement in the author's own words — the part no "
            f"path list can make for them, and the part a reviewer reads.")
    if fails:
        return ("undeclared", fails, notes)

    # ---------------------------------------------------------------- diff floor
    barred3 = [p for p in changed if _match(p, TIER3_PATHS)]
    barred2 = [p for p in changed if _match(p, TIER2_PATHS) and p not in barred3]
    unvouched = [p for p in changed
                 if not _match(p, TIER1_SURFACE) and p not in barred3 and p not in barred2]

    if barred3:
        notes.append(f"diff touches {len(barred3)} path(s) named Tier-3: "
                     + ", ".join(sorted(barred3)[:5]))
    if barred2:
        notes.append(f"diff touches {len(barred2)} path(s) named Tier-2: "
                     + ", ".join(sorted(barred2)[:5]))

    if landing == "self":
        # R4 — TIER-3 NEVER SELF-LANDS. Unchanged in force and unchanged in
        # reason: strategy logic, risk caps, sizing, account-mode flips and live
        # promotion are human-gated by docs/CLAUDE-RULES-CANONICAL.md
        # § Permission Tiers, and no record unlocks them.
        #
        # ⚠️ WHAT CHANGED ON 2026-09-11 IS TIER-2 ONLY, AND R4'S REASON IS NOT
        # WEAKENED. R4 held that a change and its own approval must not be the
        # same act — right — but used "a human clicks Merge" as the proxy for
        # that, so it barred Tier-2 self-landing WHATEVER the approval state.
        # Measured cost: PR #11738 green, mergeable and operator-approved since
        # 2026-09-10T07:52Z, unlanded, waiting on a click the operator has said
        # will not come ("the operator does not click things", 2026-09-11).
        # R15 replaces the proxy with the property itself — an approval record
        # this branch DEMONSTRABLY COULD NOT HAVE WRITTEN — and refuses in every
        # case where that cannot be established.
        if tier == 3:
            fails.append(
                f"R4 {decl_rel} declares tier 3 with `landing: \"self\"`. "
                f"TIER-3 NEVER SELF-LANDS — strategy logic, risk caps, sizing, "
                f"account-mode flips and live promotion need explicit operator "
                f"approval at the merge itself (docs/CLAUDE-RULES-CANONICAL.md "
                f"§ Permission Tiers). There is no approval record that admits "
                f"a Tier-3 diff and R15 must never be widened to create one. "
                f"Set `landing: \"hold\"`.")
        elif tier == 2:
            afails, anotes = approval_state(
                root, base, branch, decl, changed, decl_rel, arm_rel)
            fails.extend(afails)
            notes.extend(anotes)
        # R5 — the Tier-1 surface allowlist. Scoped to tier 1 deliberately: a
        # Tier-2 PR is by definition outside it, and its allowlist is the
        # approval record's own `scope_paths`, which R15 checks against the diff
        # the same way this checks against TIER1_SURFACE. Running both would
        # make every Tier-2 approval unsatisfiable.
        if tier == 1 and (barred3 or barred2 or unvouched):
            bits = []
            if barred3:
                bits.append("Tier-3 by name: " + ", ".join(sorted(barred3)[:5]))
            if barred2:
                bits.append("Tier-2 by name: " + ", ".join(sorted(barred2)[:5]))
            if unvouched:
                bits.append("outside the Tier-1 surface: "
                            + ", ".join(sorted(unvouched)[:5]))
            fails.append(
                f"R5 {decl_rel} declares tier 1 and asks to self-land, but the "
                f"diff contains paths this guard cannot vouch for — "
                + "; ".join(bits)
                + ". A path outside TIER1_SURFACE is not thereby dangerous; it is "
                  "one the guard cannot certify, and self-landing is refused on it "
                  "rather than granted by default. Either narrow the PR, or set "
                  "`landing: \"hold\"` and let a human read it.")
        # R12 — a change to the landing route may not land itself by that route.
        machinery = [p for p in changed
                     if _match(p, LANDING_MACHINERY) and p != decl_rel]
        if machinery:
            fails.append(
                f"R12 {decl_rel} asks to self-land, but the diff CHANGES THE "
                f"LANDING MACHINERY itself — "
                + ", ".join(sorted(machinery)[:5])
                + ". A PR that edits the rules by which PRs land must not be "
                  "merged by those rules unread: the change and its own approval "
                  "would be the same act, and a mistake in it disarms the check "
                  "that would have caught the mistake. This is Tier-1 work and "
                  "the tier is not the objection — set `landing: \"hold\"` with "
                  "`hold_reason: \"changes_landing_machinery\"` and let a human "
                  "read it once.")
        # R6
        if not armed:
            fails.append(
                f"R6 {decl_rel} says `landing: \"self\"` but this branch has not "
                f"armed the route: {arm_rel} is absent or unchanged from `{base}`. "
                f"A readiness declaration lands nothing on its own — that is "
                f"precisely how 7 green PRs sat unmerged on 2026-09-03. Add "
                f"{arm_rel} (any contents; its PATH is the signal) and push. "
                f"The PR must also be OPEN AND NOT A DRAFT — `claude-pr-automerge` "
                f"refuses to un-draft a PR it did not itself open, by design.")
        # R13 — arming IS the merge, so arming takes the slot.
        if armed:
            ok, detail = slot_claim_state(root, base, branch)
            if not ok:
                fails.append(
                    f"R13 {decl_rel} arms the landing route while this branch does "
                    f"not hold the merge slot in {SESSION_BOARD}: {detail}. Arming "
                    f"is not a request to merge, it IS the merge — "
                    f"`claude-pr-automerge.yml` enables auto-merge and GitHub lands "
                    f"the PR on green with no further act by anybody. "
                    f"⚠️ The `PreToolUse` merge-slot guard in `.claude/settings.json` "
                    f"CANNOT see this route: it matches "
                    f"`mcp__github__merge_pull_request` and "
                    f"`mcp__github__enable_pr_auto_merge`, and arming calls NEITHER "
                    f"— the merge is performed by a workflow under GITHUB_TOKEN, so "
                    f"no hook fires even in the CLI/desktop runtimes that do load "
                    f"hooks. R6 therefore mandates the one landing route on which "
                    f"the slot guard is structurally silent, so the claim is made "
                    f"HERE, in the same push that arms, or it is not made at all. "
                    f"(R13 records an ATTRIBUTABLE claim and fails closed without "
                    f"one; it does NOT serialize — a committed claim reaches no "
                    f"other session until this branch merges, "
                    f"BL-20260810-MERGE-SLOT-MIRROR-UNWRITABLE-PRE-MERGE.) "
                    f"Set `merge_slot` "
                    f"in {SESSION_BOARD} to this branch (`held_by`, `branch`, "
                    f"`claimed_at`) and commit it alongside the arming file. "
                    + _board_claim_sentence())
    else:  # landing == "hold"
        # R10 — the bite.
        if armed:
            fails.append(
                f"R10 this branch ARMS auto-merge ({arm_rel} is added/modified) "
                f"while {decl_rel} declares `landing: \"hold\"`"
                + (f" at tier {tier}" if tier != 1 else "")
                + ". Those are opposite instructions and the arming is the one "
                  "that would take effect. Remove the request file, or declare "
                  "tier 1 with `landing: \"self\"` and satisfy R5.")
        # R7
        reason = str(decl.get("hold_reason") or "").strip()
        text = str(decl.get("hold_text") or "").strip()
        if reason not in HOLD_REASONS:
            fails.append(
                f"R7 {decl_rel} `hold_reason` is {reason!r}; must be one of: "
                + ", ".join(sorted(HOLD_REASONS))
                + ". A closed vocabulary, so a hold is a stated kind of hold and "
                  "not an adjective — the `operator_decision` discipline in "
                  "docs/claude/work/OPEN-PRS.json.")
        if len(text) < MIN_TEXT:
            fails.append(
                f"R7 {decl_rel} `hold_text` is {len(text)} chars; needs at least "
                f"{MIN_TEXT}. The typed reason stays checkable against the "
                f"author's own wording only if the wording is there.")
        # R8 — verified, not presence-only.
        if reason == "changes_landing_machinery":
            machinery = [p for p in changed
                         if _match(p, LANDING_MACHINERY) and p != decl_rel]
            if not machinery:
                fails.append(
                    f"R8 {decl_rel} claims `changes_landing_machinery`, but no "
                    f"changed path is landing machinery. Recognised: "
                    + ", ".join(LANDING_MACHINERY)
                    + f". (This branch's own {decl_rel} is deliberately EXCLUDED "
                      "— every branch writes one, so counting it would make this "
                      "excuse free for everybody, which is the presence-only "
                      "marker `new-table-wiring-guard` was bitten by.)")
            else:
                notes.append("R8 verified — landing machinery in the diff: "
                             + ", ".join(sorted(machinery)[:5]))
        # R14 -- verified, not presence-only, exactly as R8 is. Without this,
        # `unvouchable_paths` would be the cheapest way past R5 for ANY diff,
        # which is the presence-only marker failure `new-table-wiring-guard` was
        # bitten by and that this guard cites twice elsewhere.
        if reason == "unvouchable_paths":
            unvouchable = [p for p in changed
                           if not _match(p, TIER1_SURFACE) and p != decl_rel]
            if not unvouchable:
                fails.append(
                    f"R14 {decl_rel} claims `unvouchable_paths`, but every "
                    f"changed path IS inside TIER1_SURFACE. Such a PR can "
                    f"self-land: declare `landing: \"self\"` and arm it. This "
                    f"reason exists for the case R5 names and must not become a "
                    f"way to avoid landing work that is ready.")
            elif tier != 1:
                fails.append(
                    f"R14 {decl_rel} claims `unvouchable_paths` at tier {tier}. "
                    f"That reason is for TIER-1 work the guard cannot certify; "
                    f"Tier-2/3 work is held under `tier_2_3_needs_approval`, "
                    f"which says what approval is being sought and from whom.")
            else:
                notes.append("R14 verified — paths outside TIER1_SURFACE: "
                             + ", ".join(sorted(unvouchable)[:5]))
        # R9
        if reason == "depends_on_unmerged_pr" and not re.search(r"#\d+", text):
            fails.append(
                f"R9 {decl_rel} claims `depends_on_unmerged_pr` but `hold_text` "
                f"names no PR (`#123`). A dependency nobody can look up is not a "
                f"dependency a reader can clear.")

    if fails:
        return ("undeclared" if landing == "self" else "declared_hold", fails, notes)
    if landing == "self":
        return ("declared_self_land", [], notes)
    return ("declared_needs_approval" if tier != 1 else "declared_hold", [], notes)


# ---------------------------------------------------------------------------
# self-test: plant each defect, prove the guard FAILS on it.
# ---------------------------------------------------------------------------

def _sandbox(tmp: Path, *, tier1_only: bool = True, with_guard_at_base: bool = True) -> Path:
    """A real git repo with a `main` and a branch, so merge-base logic is exercised."""
    root = tmp / "repo"
    root.mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    (root / "docs").mkdir()
    (root / "docs/seed.md").write_text("seed\n", encoding="utf-8")
    # Seeded at the BASE commit, unclaimed. A branch that arms without touching
    # it is then indistinguishable from one that never tried — which is exactly
    # the state R13 must refuse.
    (root / SESSION_BOARD).parent.mkdir(parents=True, exist_ok=True)
    (root / SESSION_BOARD).write_text(json.dumps({"merge_slot": {
        "held_by": None, "branch": None, "pr": None, "claimed_at": None}},
        indent=2) + "\n", encoding="utf-8")
    if with_guard_at_base:
        (root / "scripts/ci").mkdir(parents=True)
        (root / GUARD_REL).write_text("# the guard\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)

    subprocess.run(["git", "-C", str(root), "checkout", "-qb", "claude/demo"], check=True)
    (root / "docs/change.md").write_text("a documentation change\n", encoding="utf-8")
    if not tier1_only:
        (root / "config").mkdir(exist_ok=True)
        (root / "config/strategies.yaml").write_text("x: 1\n", encoding="utf-8")
    (root / LANDING_DIR).mkdir(parents=True, exist_ok=True)
    return root


def _declare(root: Path, **fields) -> None:
    (root / LANDING_DIR).mkdir(parents=True, exist_ok=True)
    (root / f"{LANDING_DIR}/demo.json").write_text(
        json.dumps(fields, indent=2) + "\n", encoding="utf-8")


def _arm(root: Path) -> None:
    (root / AUTOMERGE_DIR).mkdir(parents=True, exist_ok=True)
    (root / f"{AUTOMERGE_DIR}/demo.txt").write_text("land it\n", encoding="utf-8")


def _claim_slot(root: Path, branch: str = "claude/demo") -> None:
    (root / SESSION_BOARD).parent.mkdir(parents=True, exist_ok=True)
    (root / SESSION_BOARD).write_text(json.dumps({"merge_slot": {
        "held_by": "session_selftest", "branch": branch, "pr": 1,
        "claimed_at": "2026-09-08T00:00:00Z"}}, indent=2) + "\n",
        encoding="utf-8")


def _commit(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "work"], check=True)


_GOOD_WHY = "documentation-only change to the landing route contract"

# --- R15 fixtures ----------------------------------------------------------
_APPROVAL_OK = {
    "decision_id": "DEC-20260911-SELFTEST",
    "work_object": "WO-20260911-SELFTEST",
    "branch": "claude/demo",
    "tier": 2,
    "verdict": "approved",
    "channel": "operator_conversational_relayed",
    "scope_paths": ["src/runtime/**"],
    "decided_on": "2026-09-11",
    "decided_by": "operator",
    "text": "the operator chose this remedy from a four-option popup, verbatim",
}
_T2_WHY = "runtime exit-loop change, Tier-2 by path, landing on a recorded approval"


def _write_approval(root: Path, rec: dict, name: str = "demo") -> None:
    d = root / APPROVAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")


def _t2_sandbox(tmp: Path, *, approval: Optional[dict] = _APPROVAL_OK,
                approval_on_branch: bool = False,
                branch_files: Optional[dict] = None) -> Path:
    """A Tier-2 sandbox: the approval lands on `main` BEFORE the branch is cut.

    `approval_on_branch=True` is the FORGERY case — the identical record, added
    by the branch itself instead of found on `main`. That one difference is the
    whole property R15 tests, so the two paths share every other byte.
    """
    root = tmp / "repo"
    root.mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    (root / "docs").mkdir()
    (root / "docs/seed.md").write_text("seed\n", encoding="utf-8")
    (root / SESSION_BOARD).parent.mkdir(parents=True, exist_ok=True)
    (root / SESSION_BOARD).write_text(json.dumps({"merge_slot": {
        "held_by": None, "branch": None, "pr": None, "claimed_at": None}},
        indent=2) + "\n", encoding="utf-8")
    (root / "scripts/ci").mkdir(parents=True)
    (root / GUARD_REL).write_text("# the guard\n", encoding="utf-8")
    # The parent work object R15(h) insists on, present on `main`.
    objs = root / "docs/claude/work/objects"
    objs.mkdir(parents=True, exist_ok=True)
    (objs / "WO-20260911-SELFTEST.yaml").write_text("id: WO-20260911-SELFTEST\n",
                                                    encoding="utf-8")
    if approval is not None and not approval_on_branch:
        _write_approval(root, approval)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)

    subprocess.run(["git", "-C", str(root), "checkout", "-qb", "claude/demo"], check=True)
    for rel, body in (branch_files or {"src/runtime/exit_loop.py": "x = 1\n"}).items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding="utf-8")
    if approval is not None and approval_on_branch:
        _write_approval(root, approval)
    (root / LANDING_DIR).mkdir(parents=True, exist_ok=True)
    return root


def _t2_declare(root: Path, **over) -> None:
    fields = dict(tier=2, landing="self", why=_T2_WHY,
                  approved_by=f"{APPROVAL_DIR}/demo.json")
    fields.update(over)
    _declare(root, **fields)


def _t2_full(root: Path, **over) -> None:
    """Declaration + arming + slot claim — everything R6/R13 also demand."""
    _t2_declare(root, **over)
    _arm(root)
    _claim_slot(root)


def self_test() -> int:
    # ---- positive controls: the shapes that MUST pass ----------------------
    positives = {
        "tier-1 armed self-land on a docs-only diff": (
            lambda r: (_declare(r, tier=1, landing="self", why=_GOOD_WHY), _arm(r),
                       _claim_slot(r)),
            True, "declared_self_land"),
        "tier-3 diff held with a typed reason": (
            lambda r: _declare(r, tier=3, landing="hold",
                               hold_reason="tier_2_3_needs_approval",
                               hold_text="touches config/strategies.yaml; needs "
                                         "explicit operator approval before merge",
                               why="a strategy parameter change, Tier-3 by path"),
            False, "declared_needs_approval"),
        "verified unvouchable_paths hold on a tier-1 diff": (
            lambda r: ((r / "scripts").mkdir(parents=True, exist_ok=True),
                       (r / "scripts/check_something.py").write_text(
                           "x\n", encoding="utf-8"),
                       _declare(r, tier=1, landing="hold",
                                hold_reason="unvouchable_paths",
                                hold_text="touches scripts/check_something.py, a CI "
                                          "guard outside the allowlist; a human merges",
                                why="CI guard tooling the allowlist does not enumerate")),
            True, "declared_hold"),
        "verified changes_landing_machinery hold": (
            lambda r: ((r / "scripts/ci").mkdir(parents=True, exist_ok=True),
                       (r / "scripts/ci/check_automerge_trigger.py").write_text(
                           "x\n", encoding="utf-8"),
                       _declare(r, tier=1, landing="hold",
                                hold_reason="changes_landing_machinery",
                                hold_text="edits the automerge guard itself, so a "
                                          "human should read it before it lands",
                                why="changes the landing machinery")),
            True, "declared_hold"),
    }
    bad = 0
    for name, (setup, tier1_only, want) in positives.items():
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(Path(td), tier1_only=tier1_only)
            setup(root)
            _commit(root)
            state, fails, _ = check(root, "main", "claude/demo")
            if fails or state != want:
                print(f"::error::self-test FAILED — positive control '{name}' did "
                      f"not pass cleanly (state={state}, fails={fails}). A guard "
                      f"that fails correct work is worse than none.")
                bad += 1
            else:
                print(f"self-test: positive control '{name}' passes (state={state})")

    # ---- the escape hatch, and the hole it must NOT open -------------------
    with tempfile.TemporaryDirectory() as td:
        root = _sandbox(Path(td), with_guard_at_base=False)
        _commit(root)
        state, fails, _ = check(root, "main", "claude/demo")
        if fails or state != "undeclared_predates_guard":
            print(f"::error::self-test FAILED — a branch cut before the guard "
                  f"existed should PASS undeclared (state={state}, fails={fails}).")
            bad += 1
        else:
            print("self-test: pre-guard branch passes undeclared, loudly")

    with tempfile.TemporaryDirectory() as td:
        root = _sandbox(Path(td), with_guard_at_base=False)
        _arm(root)
        _commit(root)
        state, fails, _ = check(root, "main", "claude/demo")
        if not fails:
            print("::error::self-test FAILED — a branch that ARMS auto-merge with "
                  "no declaration passed because it predates the guard. Age "
                  "excuses not knowing the rule; it must not excuse asking to "
                  "merge without approval.")
            bad += 1
        else:
            print("self-test: arming without a declaration fails even when the "
                  "branch predates the guard (R10 is not grandfathered)")

    # ---- planted defects: each MUST fail -----------------------------------
    plants = {
        "R10 armed while declaring hold": (
            lambda r: (_declare(r, tier=1, landing="hold",
                                hold_reason="operator_asked_to_hold",
                                hold_text="the operator asked to hold this one back",
                                why=_GOOD_WHY), _arm(r)), True),
        "R4 tier-2 asking to self-land": (
            lambda r: (_declare(r, tier=2, landing="self", why=_GOOD_WHY), _arm(r),
                       _claim_slot(r)), True),
        "R5 tier-1 self-land over a Tier-3 path": (
            lambda r: (_declare(r, tier=1, landing="self", why=_GOOD_WHY), _arm(r),
                       _claim_slot(r)), False),
        "R6 self-land declared but route not armed": (
            lambda r: _declare(r, tier=1, landing="self", why=_GOOD_WHY), True),
        "R3 a one-word `why`": (
            lambda r: (_declare(r, tier=1, landing="self", why="x"), _arm(r)), True),
        "R7 hold_reason outside the vocabulary": (
            lambda r: _declare(r, tier=1, landing="hold", hold_reason="because",
                               hold_text="a perfectly reasonable sounding excuse",
                               why=_GOOD_WHY), True),
        "R8 unverified changes_landing_machinery": (
            lambda r: _declare(r, tier=1, landing="hold",
                               hold_reason="changes_landing_machinery",
                               hold_text="claims to touch the landing route but "
                                         "the diff is a docs file",
                               why=_GOOD_WHY), True),
        "R14 unvouchable_paths on an all-Tier-1 diff": (
            lambda r: _declare(r, tier=1, landing="hold",
                               hold_reason="unvouchable_paths",
                               hold_text="claims the guard cannot vouch for this, "
                                         "but every path is inside the allowlist",
                               why="a docs-only diff pretending it cannot self-land"),
            True),
        "R9 depends_on_unmerged_pr naming no PR": (
            lambda r: _declare(r, tier=1, landing="hold",
                               hold_reason="depends_on_unmerged_pr",
                               hold_text="waiting on the other one to land first",
                               why=_GOOD_WHY), True),
        "R12 self-landing a change to the landing machinery": (
            lambda r: ((r / "scripts/ci").mkdir(parents=True, exist_ok=True),
                       (r / "scripts/ci/check_automerge_trigger.py").write_text(
                           "edited\n", encoding="utf-8"),
                       _declare(r, tier=1, landing="self", why=_GOOD_WHY),
                       _arm(r), _claim_slot(r)), True),
        "R13 armed self-land holding no merge slot": (
            lambda r: (_declare(r, tier=1, landing="self", why=_GOOD_WHY),
                       _arm(r)), True),
        "R13 armed self-land riding another branch's slot claim": (
            lambda r: (_declare(r, tier=1, landing="self", why=_GOOD_WHY), _arm(r),
                       _claim_slot(r, branch="claude/somebody-else")), True),
        "R11 no declaration on a branch that could have known": (
            lambda r: None, True),
        "R1 a tier outside 1-3": (
            lambda r: _declare(r, tier=0, landing="self", why=_GOOD_WHY), True),
        "R0 unparseable declaration": (
            lambda r: (r / LANDING_DIR / "demo.json").write_text("{nope", encoding="utf-8"),
            True),
    }
    for name, (plant, tier1_only) in plants.items():
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(Path(td), tier1_only=tier1_only)
            plant(root)
            _commit(root)
            _, fails, _ = check(root, "main", "claude/demo")
            if not fails:
                print(f"::error::self-test FAILED — planted '{name}' and the guard "
                      f"still passed. Its failure path is broken, so a green from "
                      f"it means nothing.")
                bad += 1
            else:
                print(f"self-test: '{name}' correctly caught")

    # ---- R15: the Tier-2 approved-self-land path ---------------------------
    # The POSITIVE control first, because a rule that only ever refuses is
    # indistinguishable from a rule that is broken.
    with tempfile.TemporaryDirectory() as td:
        root = _t2_sandbox(Path(td))
        _t2_full(root)
        _commit(root)
        state, fails, _ = check(root, "main", "claude/demo")
        if fails or state != "declared_self_land":
            print(f"::error::self-test FAILED — R15 positive control: a Tier-2 PR "
                  f"whose approval is ON `main`, names this branch, and covers "
                  f"the diff should self-land (state={state}, fails={fails}). "
                  f"A gate that never admits anything has not fixed the click.")
            bad += 1
        else:
            print("self-test: R15 positive control — an approved Tier-2 PR "
                  "self-lands (state=declared_self_land)")

    # ---- R15 negative controls. THESE ARE THE DELIVERABLE -------------------
    # Anything can be made to pass; what matters is that the gate still REFUSES.
    r15_plants = {
        # (1) no approval at all.
        "R15(a) tier-2 self-land with NO approval record": (
            lambda r: (_t2_declare(r, approved_by=None), _arm(r), _claim_slot(r)),
            {}),
        # (2) THE FORGERY CASE — the identical record, written by the branch
        #     that benefits from it. Byte-for-byte the record that PASSES
        #     above; the only difference is which commit carries it.
        "R15 the approval lives ON THE PR'S OWN BRANCH (the forgery case)": (
            lambda r: (_t2_full(r),), {"approval_on_branch": True}),
        # (3) an approval for somebody else's change.
        "R15(f) the approval names a DIFFERENT branch": (
            lambda r: (_t2_full(r),),
            {"approval": {**_APPROVAL_OK, "branch": "claude/somebody-else"}}),
        # (4) scope is checked against the diff, the way R5 checks tier.
        "R15(i) the diff EXCEEDS the approved scope": (
            lambda r: (_t2_full(r),),
            {"branch_files": {"src/runtime/exit_loop.py": "x = 1\n",
                              "src/units/accounts/ib_client.py": "y = 2\n"}}),
        # (5) Tier-3 is out of scope for self-landing ENTIRELY.
        "R4 a tier-3 declaration self-landing on a valid tier-2 approval": (
            lambda r: (_t2_full(r, tier=3),), {}),
        "R15(g) a tier-2 declaration whose diff touches a Tier-3 path": (
            lambda r: (_t2_full(r),),
            {"approval": {**_APPROVAL_OK,
                          "scope_paths": ["src/runtime/**", "config/**"]},
             "branch_files": {"src/runtime/exit_loop.py": "x = 1\n",
                              "config/strategies.yaml": "a: 1\n"}}),
        # --- and the ways a record could be hollowed out --------------------
        "R15(b) approved_by pointing outside the protected directory": (
            lambda r: ((r / "docs").mkdir(parents=True, exist_ok=True),
                       (r / "docs/my-own-approval.json").write_text(
                           json.dumps(_APPROVAL_OK), encoding="utf-8"),
                       _t2_full(r, approved_by="docs/my-own-approval.json")), {}),
        "R15(e) the branch EDITS the approval it cites": (
            lambda r: (_write_approval(r, {**_APPROVAL_OK,
                                           "scope_paths": ["**"]}),
                       _t2_full(r)), {}),
        "R15(i) scope recorded only in prose, no scope_paths": (
            lambda r: (_t2_full(r),),
            {"approval": {k: v for k, v in _APPROVAL_OK.items()
                          if k != "scope_paths"}}),
        "R15(h) verdict `pending` read as a grant": (
            lambda r: (_t2_full(r),),
            {"approval": {**_APPROVAL_OK, "verdict": "pending"}}),
        "R15(h) an approval naming a work object that does not exist": (
            lambda r: (_t2_full(r),),
            {"approval": {**_APPROVAL_OK, "work_object": "WO-NOPE"}}),
        "R15(g) a record declaring tier 3": (
            lambda r: (_t2_full(r),),
            {"approval": {**_APPROVAL_OK, "tier": 3}}),
    }
    for name, (plant, kw) in r15_plants.items():
        with tempfile.TemporaryDirectory() as td:
            root = _t2_sandbox(Path(td), **kw)
            plant(root)
            _commit(root)
            _, fails, _ = check(root, "main", "claude/demo")
            if not fails:
                print(f"::error::self-test FAILED — planted '{name}' and the "
                      f"guard still ADMITTED a Tier-2 self-land. R15's whole "
                      f"value is what it refuses.")
                bad += 1
            else:
                print(f"self-test: '{name}' correctly refused")

    if bad:
        return 1
    print(f"self-test OK — {len(positives) + 3} positive controls hold and all "
          f"{len(plants) + len(r15_plants)} planted defects fail the guard")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main",
                    help="base ref to diff against (default origin/main)")
    ap.add_argument("--branch", default=None,
                    help="override the branch name (default: GITHUB_HEAD_REF or git)")
    ap.add_argument("--self-test", action="store_true",
                    help="plant each defect and prove the guard fails on it")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    branch = args.branch or current_branch(REPO)
    state, fails, notes = check(REPO, args.base, branch)

    for n in notes:
        print(f"  note: {n}")
    if fails:
        print(f"::error::pr-landing-guard FAILED (state={state})")
        for f in fails:
            print(f"  - {f}")
        return 1
    if state == "not_a_pr":
        print("pr-landing: SKIPPED — not a PR context, so nothing was graded. "
              "This is not a pass.")
        return 0
    if state == "undeclared_predates_guard":
        print("pr-landing: PASSED ON AGE — this branch predates the rule and was "
              "not graded against it. This is not a clean bill of health.")
        return 0
    print(f"pr-landing: OK — state={state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
