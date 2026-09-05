#!/usr/bin/env python3
"""Run every fast static CI guard in ONE job.

BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES.

WHY THIS EXISTS. Each guard used to be its own workflow, so a single PR asked
GitHub for ~29 separate hosted runners. That fan-out is free when the runner
pool is healthy and *catastrophic* when it is not: on 2026-08-06 an Actions
incident (15:22Z onward) left 28 of 29 jobs `queued` with no runner for over an
hour, and because almost none of those workflows declared a ``concurrency:``
group, each re-run **stacked** instead of superseding the last — 75 queued runs
accumulated from ~3 attempts at the same PR. The PR was red for reasons that had
nothing to do with its diff, and there was no way to tell "CI failed" from "CI
never ran" without opening 29 job pages.

One job means one runner acquisition, one checkout, one dependency install, and
one place to read. It also means a run reports **every** failing guard at once
rather than making you re-run to find the next one.

WHAT THIS IS NOT. It is a PACKAGING change. Every guard runs the same command,
against the same relevance condition, with the same assertions as the workflow
it replaces. A guard that used a trigger-level ``paths:`` filter now carries the
equivalent glob in :data:`GUARDS`; a guard that short-circuited inside its job
on a ``grep -Eq`` keeps that regex. Nothing was softened, skipped, or made
advisory in the move — if you are editing this file, that constraint is the
point of it.

RELEVANCE. A guard runs when its ``when`` predicate matches the PR's changed
files (``when: None`` = always). Two escape hatches keep this honest:

* changing ``guards.yml`` or this file makes **every** guard relevant — the old
  workflows each self-referenced in their ``paths:`` for the same reason;
* ``--all`` ignores relevance entirely (what ``workflow_dispatch`` and a
  local audit run should use).

Skipping a guard is always announced, never silent — a guard that quietly
declines to run is the "green that checked nothing" this repo already has a
rule about.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parents[2]

# Changing the guard harness itself makes every guard relevant (mirrors the
# self-referencing `paths:` entry each retired workflow carried).
HARNESS_PATHS = (
    ".github/workflows/guards.yml",
    "scripts/ci/run_guards.py",
    "scripts/ci/guard_selftests.py",
)


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------
#
# Each entry:
#   name    — the guard's identity in the log + the failure summary. Keep it
#             equal to the retired workflow's job id so backlog rows, docs and
#             muscle memory still resolve.
#   when    — relevance. None = always run. Otherwise a dict with any of:
#               globs:  GitHub `paths:`-style patterns (** supported)
#               regex:  a regex applied to each changed path (the in-job
#                       `grep -Eq` short-circuits used the same expressions)
#   steps   — ordered commands. A step is either an argv list, or a dict:
#               argv        — the command
#               allow_fail  — report but never fail the job (advisory step)
#               pr_only     — only on a pull_request event
#               git_clean   — after running argv, assert this path is unmodified
#               hint        — extra operator guidance printed on failure
#             `{changed_files}` in an argv element is replaced with the
#             space-joined changed-file list.
#   notify  — True if tripping this guard used to send an operator Telegram
#             ping. The driver records these; guards.yml sends ONE message
#             naming all of them (previously one message per guard).
#
# `python3` everywhere: the runner's `python` and `python3` are the same
# setup-python interpreter, and pinning one spelling removes a class of
# "works in one workflow, not the other" drift.

GUARDS: List[Dict[str, Any]] = [
    {
        "name": "account-class-guard",
        "when": {"globs": ["config/accounts.yaml", "scripts/check_account_class.py"]},
        "steps": [["python3", "scripts/check_account_class.py", "--list"]],
    },
    {
        "name": "api-tier-policy-guard",
        # The self-test runs on EVERY invocation of this guard — including when
        # the scan is not diff-relevant — because a guard whose failure path is
        # never exercised is indistinguishable from one that always passes.
        "when": None,
        "steps": [
            ["python3", "scripts/ci/guard_selftests.py", "api-tier-policy"],
            # Diff-scoped: names the specific route a PR added without a row,
            # which is the actionable message. Scoped to router changes.
            {
                "argv": ["python3", "scripts/check_api_tier_policy.py", "{pr_diff}"],
                "when": {"globs": ["src/web/api/routers/**"]},
            },
            # The completeness backstop, deliberately UNGATED. Two reasons:
            #   1. A diff-scoped check cannot see a row being DELETED from the
            #      inventory — the drift that produced the 60%-incomplete state
            #      in the first place was routes arriving, but a row leaving is
            #      the same hole in the other direction.
            #   2. A per-STEP `when` is evaluated against `changed`, which is
            #      EMPTY under `--all` (push / workflow_dispatch) — so a
            #      `when`-gated step never runs on exactly the events
            #      guards.yml intends to run everything. Leaving this ungated
            #      is what makes the push-time audit real rather than nominal,
            #      and it is the pattern BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH
            #      settled on: the skip itself is CORRECT (a diff-consuming step
            #      given an empty diff would report a green that scanned
            #      nothing), so a guard wanting push coverage carries a
            #      whole-tree step instead of relying on the gated one.
            # Costs ~0.15s: an AST pass over ~40 router files plus one regex
            # pass over the doc. Cheap enough that gating it would be the more
            # expensive decision.
            ["python3", "scripts/check_api_tier_policy.py", "--all"],
        ],
    },
    {
        # An operator's WRITTEN answer must be READABLE by the grader.
        # UNGATED (`when: None`) deliberately: the failure is a decision object
        # going quiet, and a diff-scoped guard cannot see a row regress when an
        # unrelated PR edits it. The tree was MEASURED clean in the change that
        # added this (614 objects, 0 unparseable, 0 findings), so nothing is
        # grandfathered and there is no separate standing audit to forget.
        "name": "decision-answers-guard",
        "when": None,
        "steps": [
            # The self-test runs on EVERY invocation: on a clean tree this guard
            # is only ever observed PASSING, which is the state a guard is least
            # useful in. It exercises both rules AND the false-positive shape R2
            # was tightened for (a request answered-and-nested beside a second
            # that is genuinely open).
            ["python3", "scripts/ci/check_decision_answers.py", "--self-test"],
            ["python3", "scripts/ci/check_decision_answers.py"],
        ],
    },
    {
        # (c) of the demote-and-tune design: at budget expiry a demotion CANNOT
        # stay demoted. UNGATED, like its sunset sibling: the failure is about a
        # budget ACCRUING over time, which no diff is relevant to — a demotion
        # carried past its budget becomes a failure on a PR that touched nothing
        # near it, and that is the point.
        "name": "demote-budget-guard",
        "when": None,
        "steps": [
            # The self-test runs on EVERY invocation because the interesting
            # branches are unreachable in production today: no leg has been
            # demoted under this flow yet, so without it the forcing function
            # would be untested until the first demotion expired — two months
            # after anyone could still remember writing it.
            ["python3", "scripts/ops/demote_budget.py", "--self-test"],
            ["python3", "scripts/ops/demote_budget.py"],
        ],
    },
    {
        # E3 — Phase G. The forcing function that makes the system REMOVE.
        # UNGATED: the escalation is about candidates ACCRUING over time, which
        # no diff can be relevant to — a candidate carried past its threshold
        # becomes a failure on a PR that touched nothing near it, and that is
        # the point. Costs ~0.05s: two small JSON reads.
        "name": "sunset-disposition-guard",
        "when": None,
        "steps": [
            # The self-test runs on EVERY invocation — this guard's escalation
            # branch is unreachable in production until three passes have
            # accrued, so without it the teeth would be untested for a fortnight.
            ["python3", "scripts/ci/check_sunset_dispositions.py", "--self-test"],
            ["python3", "scripts/ci/check_sunset_dispositions.py"],
        ],
    },
    {
        # E2 — Phase G. Capability build is PULLED by a held-up stage.
        # ⚠️ ADVISORY IN PRODUCTION TODAY, BY MEASUREMENT, NOT BY SOFTENING: the
        # constraint readout REFUSES (1.0% assessed coverage against a 50%
        # floor), so no stage can be named and no pull claim can be verified.
        # The `enforcing` branch is real and is exercised by the self-test on
        # every run, so the teeth are known to work on the day true `blocked_on`
        # edges make them reachable. The way to switch it on is to write those
        # edges — not to edit the guard.
        "name": "capability-pull-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_capability_pull.py", "--self-test"],
            {
                "argv": ["python3", "scripts/ci/check_capability_pull.py",
                         "--base", "origin/{base_ref}"],
                "pr_only": True,
            },
        ],
    },
    {
        "name": "workflow-catalog",
        # UNGATED (`when: None`), for the same reason api-tier-policy's
        # completeness backstop is: a diff-scoped check cannot see a row being
        # DELETED from the index, and a per-step `when` is evaluated against a
        # `changed` list that is EMPTY under `--all` (push / workflow_dispatch)
        # — so a gated step would skip on exactly the events meant to run
        # everything (BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH).
        #
        # Costs ~0.2s: one directory listing, one regex pass over the doc, and
        # one `git ls-files`. Cheap enough that gating it would be the more
        # expensive decision.
        "when": None,
        "steps": [
            # The self-test runs on EVERY invocation — a guard whose failure
            # path is never exercised is indistinguishable from one that always
            # passes, and this guard's whole subject is a claim that was green
            # and false for 45.9% of its scope.
            ["python3", "scripts/ci/check_workflow_catalog.py", "--self-test"],
            ["python3", "scripts/ci/check_workflow_catalog.py", "--all"],
        ],
    },
    {
        "name": "arch-doc-guard",
        "when": {
            "globs": [
                "src/pipeline/**",
                "src/core/coordinator.py",
                "src/core/dispatcher*.py",
                "src/runtime/pipeline.py",
                "src/runtime/shadow_adapter.py",
                "src/runtime/health.py",
                "src/units/strategies/**",
                "src/units/dashboards/**",
                "src/web/api/main.py",
                "src/web/api/routers/**",
                "ml/registry/**",
                "ml/predictors/**",
                "ml/promotion/**",
                "ml/shadow/**",
                "ml/trainers/**",
                "ml/evaluators/**",
                "ml/datasets/**",
                "config/strategies.yaml",
                "config/accounts.yaml",
                "config/units.yaml",
                "docs/ARCHITECTURE-CANONICAL.md",
                "docs/architecture/**",
                "docs/pipeline/stage-contracts.md",
                "docs/CLAUDE-RULES-CANONICAL.md",
                "CLAUDE.md",
                "scripts/arch_doc_guard.py",
            ]
        },
        "steps": [["python3", "scripts/arch_doc_guard.py", "--changed-files={changed_files}"]],
    },
    {
        # The register EVERY session reads at start. This guard is what stops
        # it becoming the 951-row backlog it exists to replace — see the
        # script's docstring: the cap is the mechanism, not a limitation.
        # `when: None` so it runs on every diff: a register that is only
        # checked when someone happens to touch it is not a register.
        "name": "open-items-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_open_items.py", "--self-test"],
            ["python3", "scripts/ci/check_open_items.py"],
        ],
    },
    {
        # ID UNIQUENESS + IDENTITY across the shared registers — the half of
        # the register-collision problem `merge_json_register.py` CANNOT do.
        # That driver resolves the TEXTUAL collision (and only client-side);
        # this catches the SEMANTIC one, where a branch files new work under an
        # id `main` has already given to something else. To git that is one
        # changed value at one key, so the merge deletes the existing row and
        # reports success. It happened on 2026-09-03 (MI-86) and was caught by
        # a human reading a three-way diff, which is not a mechanism.
        #
        # `when: None` — every diff, for the same reason as open-items-guard
        # above: the registers are written by many concurrent sessions, and the
        # colliding write is by definition made by someone who did not know the
        # id was taken. A guard that only fires when someone happens to touch a
        # register would be checking the one case that needs no checking.
        #
        # ⚠️ `--base` is what makes R2/R3 diff-scoped. WITHOUT it the script
        # runs R1 only and SAYS SO in its report rather than printing a clean
        # verdict it did not earn.
        "name": "register-id-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_register_ids.py", "--self-test"],
            ["python3", "scripts/ci/check_register_ids.py",
             "--base", "origin/{base_ref}"],
        ],
    },
    {
        # A5 — the WIP ceiling of 8 work objects IN FLIGHT (operating-layer
        # Phase C). ⚠️ THIS IS A DIFFERENT POPULATION FROM open-items-guard
        # ABOVE, and the distinction is load-bearing: the REGISTER is uncapped
        # (check_open_items.MAX_ITEMS is None, operator-reversed 2026-08-26)
        # while the IN-FLIGHT SET is capped. Conflating them re-introduces the
        # eviction rule that told sessions to delete knowledge to satisfy a rule
        # nothing enforced — so this guard's self-test asserts MAX_ITEMS is
        # still None and fails loudly if someone caps the register believing
        # they are implementing the ceiling.
        #
        # `when: None` so it runs on every diff, for the same reason the
        # register guard does: a ceiling that is only checked when someone
        # happens to touch the work store is not a ceiling. The store is filled
        # by many sessions, and the ninth parent is added by whoever is last.
        "name": "wip-ceiling-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_wip_ceiling.py", "--self-test"],
            # The migration that FILLS the store ships with the ceiling that
            # bounds it, so its mapping is exercised here too — a migration that
            # silently started emitting `in_flight` or `accepted` rows would
            # defeat the ceiling from the inside.
            ["python3", "scripts/ops/migrate_backlog_to_work_objects.py", "--self-test"],
            ["python3", "scripts/ci/check_wip_ceiling.py"],
        ],
    },
    {
        # The registers only work if their contents reach a session BEFORE it
        # acts. Hooks do not run on Claude Code on the web (verified
        # 2026-08-26) and CI fires at merge, so CLAUDE.md's inlined SESSION
        # BRIEF is the only channel that arrives in time. This guard keeps that
        # block in sync — a STALE brief is worse than none, because a session
        # would read something no longer true and act on it.
        "name": "constraint-readout-guard",
        # E1's whole job is to REFUSE rather than name a stage over unassessed
        # edges, and every distinction that refusal rests on lives in one
        # module's --self-test: `declared_none` vs `unstated`, a stale hold vs a
        # live one, money that is `None` rather than 0.0, and the imported
        # operator-owed vocabulary. The last of those is not hypothetical — the
        # first run of that file re-derived it and invented five decisions for
        # the operator. Unregistered, the assertions run only when someone
        # remembers to type the command.
        #
        # `when: None` so it runs on every PR: the self-test is a few
        # milliseconds and reads no network, and diff-scoping it to the script's
        # own path would miss the case that actually breaks it — a change to
        # `src/runtime/operator_owed.py`'s vocabulary or to
        # `check_wip_ceiling.py`'s CEILING, both of which this module IMPORTS.
        "name_note": "self-test only; the readout itself is generated on demand, not in CI",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/constraint_readout.py", "--self-test"],
        ],
    },
    {
        # `context = work object + role pack` is the operating model's anti-silo
        # mechanism, and on 2026-09-01 its two halves were wired to DIFFERENT
        # systems: the object half shipped and not one role pack was updated to
        # know it exists. A prose edit alone decays back to zero on the next
        # rewrite — this is what keeps it true.
        #
        # Two directions, and only the second has teeth: a situating pack must
        # NAME a live operating-layer path, and EVERY layer path ANY pack names
        # must exist. So renaming the store reddens the packs pointing at the
        # old place. Deliberately NOT all 32 — most packs are domain procedure
        # and are correctly indifferent to where work is tracked.
        #
        # `when: None`: the thing that breaks it is usually a path MOVING
        # elsewhere in the repo, which touches no skill file, so a diff-scoped
        # version would go quiet exactly when it should speak.
        "name": "role-pack-operating-layer",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_role_pack_operating_layer.py",
             "--self-test"],
            ["python3", "scripts/ci/check_role_pack_operating_layer.py"],
        ],
    },
    {
        # CAN THIS WORKFLOW'S PUSH TRIGGER FIRE AT ALL? A narrow, deterministic
        # slice of the audit's own biggest blind spot: 87 of 129 workflows
        # cannot be graded on dormancy (their history is skipped label-filter
        # evaluations), so "is this thing dead?" had no cheap surface. This does
        # not answer that — it answers the reachability half, which needs no run
        # history: a `push` trigger pinned to a branch that no longer exists is
        # unreachable by construction.
        #
        # It found THREE on its first run (ict-scalp-exit-sweep,
        # m20-capture-census, m20-exit-lever-sweep); the audit had flagged one,
        # and graded it as a CI failure to repair rather than a dead trigger.
        #
        # ⚠️ `when: None` so it runs on every PR, NOT diff-scoped to
        # `.github/workflows/**`. The thing that breaks a trigger is usually a
        # BRANCH DELETION, which touches no file in the diff — so a diff-scoped
        # version would go quiet at exactly the moment it should speak.
        # ⚠️ An unreadable `origin` PASSES (loudly). Failing on a network blip
        # would red every open PR — BL-20260830.
        "name": "workflow-trigger-reachability",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_workflow_trigger_reachability.py",
             "--self-test"],
            ["python3", "scripts/ci/check_workflow_trigger_reachability.py"],
        ],
    },
    {
        "name": "session-brief-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/render_session_brief.py", "--self-test"],
            # ⚠️ `--base` is what stops this guard failing a PR for a staleness
            # it did not introduce. The brief goes stale on a CLOCK (render()
            # calls datetime.now()), so without diff-scoping every open PR reds
            # at a UTC-midnight cadence boundary and a branch cut inside that
            # window is stranded permanently — measured 2026-08-31 on two
            # automation PRs that were green on everything else
            # (BL-20260830-A-TRANSIENT-RED-BASE-PERMANENTLY-STRANDS-AN-AUTOMERGE-BRANCH).
            ["python3", "scripts/ops/render_session_brief.py", "--check",
             "--base", "origin/main"],
        ],
    },
    {
        # EXACTLY ONE MANAGEMENT SESSION AT A TIME — an operator requirement
        # (2026-09-01), not a convention. The lease's refusal paths are the whole
        # mechanism, so a lease whose `held_fresh` and `unreadable` branches never
        # run is indistinguishable from no lease: this runs them every CI pass.
        # ⚠️ Self-test ONLY. It deliberately does NOT read the live lease file:
        # CI is not a management session, and a guard that graded the live lease
        # would red every PR opened while a manager legitimately holds it.
        "name": "manager-lease-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/manager_lease.py", "--self-test"],
        ],
    },
    {
        # THE SUB-SESSION REGISTRY. A manager arriving COLD can only pick up the
        # sub-sessions `docs/claude/work/SESSIONS.json` names; one it does not
        # name is, to that successor, one that does not exist.
        #
        # ⚠️ THIS IS A RECURRENCE, WHICH IS WHY IT IS A GUARD AND NOT ANOTHER
        # REMINDER. `MI-15-SESSIONS-REGISTRY-INCOMPLETE` recorded 3 of 6 spawned
        # sessions absent on 2026-09-01 and applied the remedy "remember to
        # register". On 2026-09-02T05:56Z it was 6 of 9, five of them LIVE, with
        # the MI-15 row still sitting at `landed_unproven`. The moment a manager
        # spawns a session is exactly the moment it is least likely to stop and
        # write a record, so the remedy cannot be another reminder.
        #
        # ⚠️ WHAT THIS GUARD CAN AND CANNOT SEE — stated because the gap is the
        # point. Enumerating what is actually RUNNING needs the `list_sessions`
        # MCP tool, and CI holds no MCP tools. So the live detector is NOT here;
        # it runs when the manager passes an observation to
        # `scripts/ops/handoff_check.py`, which REFUSES to grade a handoff
        # `ready` without one. What IS here is the offline half: the manager
        # already writes a session id into `MANAGER-CHECKLIST.json::items[].owner`
        # when it assigns work, so an owner id absent from the registry is a lost
        # session detectable from two file reads. Partial by construction — a
        # session in neither file is invisible to it — and strictly more than the
        # zero either file caught alone.
        #
        # `when: None` for the same reason the register and ceiling guards use
        # it: the registry is written by whichever session spawns next, so a
        # check that only runs when someone happens to touch the work store is
        # not a check. Two small file reads, no network.
        #
        # NOT A WALL — measured before wiring. At the head this shipped on,
        # `--strict` exits 0: 32 rows all well-formed, and every owner on an
        # `in_flight` item is registered. Enforcement is scoped to `in_flight`
        # deliberately (that is where losing a session costs LIVE work); the 3
        # owners still absent on `landed_unproven` items are CENSUSED and
        # printed on every run, so the narrow enforcement can never hide the
        # wider number.
        "name": "session-registry-guard",
        "when": None,
        "steps": [
            # Both detectors, asserted in BOTH directions — a planted defect
            # fires, a clean input stays quiet. One direction proves a check
            # runs, never that it discriminates.
            ["python3", "scripts/ops/session_registry.py", "--self-test"],
            ["python3", "scripts/ops/handoff_check.py", "--self-test"],
            # The live tree: structural integrity + the offline cross-check.
            ["python3", "scripts/ops/session_registry.py", "status", "--strict"],
            # The OPEN-PR half of the same handoff (MI-43 scope extension).
            ["python3", "scripts/ops/open_pr_record.py", "--self-test"],
            # ⚠️ `--strict` here grades DECISIONS ONLY, deliberately. Whether
            # every open PR has a row needs a live list from GitHub, and CI's
            # `GITHUB_TOKEN` could fetch it — but the check would then be
            # measuring a moving target that changes between the run and the
            # merge, reddening PRs for a row nobody could have written yet. The
            # decision half is a property of the FILE, so it is stable, and it
            # is the half that carries the danger: a row recording a verdict
            # without its condition reads as complete, which is the state that
            # could merge a demo-only Tier-2 approval onto a real-money account.
            # Completeness is enforced where it belongs — at the handoff, by
            # `handoff_check.py`, which refuses `ready` without the observation.
            ["python3", "scripts/ops/open_pr_record.py", "--strict"],
        ],
    },
    {
        # A repeated mistake must produce a PREVENTION, not another row.
        # GATE 0 item G4. Operator-approved 2026-08-26 on the test "if it's
        # affecting things that are being read or filed before they're actually
        # merged, then that's worth keeping" -- a population-less number lands in
        # a backlog row or doc and is then READ by later sessions as fact.
        "name": "stated-population-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_stated_population.py", "--self-test"],
            ["python3", "scripts/ci/check_stated_population.py", "{pr_diff}"],
        ],
    },
    {
        # THE MANAGER-SIDE GUARD. `pr-queue-watch.yml` times how long an open,
        # unmerged PR has sat with no push -- the MCP-free half of the question
        # `queue_latency.py` can only answer with `list_sessions`. This entry is
        # what makes that watcher a GUARD rather than a script somebody could
        # have run: the watcher is not invokable from a prompt, a skill or a
        # checklist step, and its DEADNESS fails here, on every PR. Measured
        # across this cycle -- every mechanism the manager had to CHOOSE to run
        # went unused; every mechanism that STOOD IN THE WAY worked.
        #
        # ⚠️ IT GRADES THE WATCHER'S LIVENESS, NEVER THE BACKLOG'S SIZE. A
        # contributor's PR must not go red because the manager has four others
        # unmerged -- the same objection this file already records against
        # fetching the live open-PR list in `open_pr_record.py --strict`
        # ("reddening PRs for a row nobody could have written yet"). The backlog
        # is PRINTED here and escalated by the watcher's own run.
        #
        # ⚠️ `never_ran` PASSES and that is correct rather than lenient: it is
        # the accurate reading until the workflow first fires, and failing on it
        # would red every PR on the day this merges -- which is how a guard gets
        # disabled instead of fixed. It arms itself on the first real run.
        #
        # `when: None`: a watcher can die without any PR touching its files,
        # which is precisely the case that must be caught.
        "name": "pr-queue-watch-guard",
        "when": None,
        "steps": [
            # Both directions, on both halves -- a planted defect fires and a
            # clean input stays quiet. One direction proves a check runs, never
            # that it discriminates.
            ["python3", "scripts/ops/pr_queue_latency.py", "--self-test"],
            ["python3", "scripts/ci/check_pr_queue_watch.py", "--self-test"],
            ["python3", "scripts/ci/check_pr_queue_watch.py"],
        ],
    },
    {
        # THE MANAGER'S OWN TOOLING MUST BE ABLE TO GRADE.
        #
        # ⚠️ THIS ENTRY EXISTS BECAUSE ITS ABSENCE WAS MEASURED, not on principle.
        # On the morning of 2026-09-03 `manager_preflight.py --self-test` refused
        # to grade -- the manager's own gate was unusable -- and NOTHING in CI
        # noticed, because no guard ran it. The cause was environmental: an
        # assertion read `count_autonomous_actions(2020) > 100`, a claim about
        # CLONE DEPTH rather than about the behaviour under test, so it passed on
        # a full clone (4043 commits) and failed on a `--depth=50` one (50). The
        # assertion is now an equality against the count git itself reports, which
        # is both stronger and depth-independent -- verified passing at depth 1,
        # depth 50 and full.
        #
        # ⚠️ DEPTH-INDEPENDENCE IS WHAT MAKES THIS SAFE TO WIRE. A guard that
        # assumed full history would fail under `actions/checkout`'s default
        # `fetch-depth: 1`, i.e. it would red every PR for an environmental
        # reason -- exactly the shape that gets a guard deleted instead of fixed.
        #
        # ⚠️ IT GRADES THE TOOLING, NEVER THE MANAGER. A contributor's PR does not
        # go red because a preflight CHECK fails on live state -- only the bare
        # `--self-test` runs here, which is a pure planted-failure suite over pure
        # functions. The preflight's live verdict stays the manager's to run.
        #
        # `when: None`: this tooling can break from a change to anything it
        # imports (`session_registry`, `manager_lease`), not only from a diff that
        # touches its own file.
        "name": "manager-tooling-selftests",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/manager_preflight.py", "--self-test"],
            ["python3", "scripts/ops/manager_view.py", "--self-test"],
            ["python3", "scripts/ops/handoff_check.py", "--self-test"],
            # The REFUSAL half of the same pair (MI-93). Its suite is pure —
            # planted inputs through pure functions, no clone depth, no network,
            # no MCP — so it is depth-independent for the same reason the three
            # above are, and cannot red a PR for an environmental reason.
            #
            # ⚠️ ONLY THE `--self-test` RUNS HERE, NEVER THE GATE. The gate needs
            # a live `list_sessions` read that CI cannot make, so wiring the gate
            # itself would grade every PR `unknown` forever — a permanently amber
            # check is one everyone learns to walk past, which is the
            # desensitised-alarm failure its own docstring argues against.
            ["python3", "scripts/ops/pr_action_gate.py", "--self-test"],
        ],
    },
    {
        # DOES ANYTHING NOTICE IF THE MANAGER QUEUE WATCH ROUTINE DIES?
        # `trig_01TWdAvrwFLe6T9XFoNopTeo` (cron `56 * * * *`) spawns a FRESH
        # session hourly to check whether the manager is sitting on blocked
        # sub-sessions -- a check NOT invoked by the actor it checks, which is
        # why it works. Measured 2026-09-03 over all 25 Routines `list_triggers`
        # returned for this account, it is the ONLY cron-driven one; the other 24
        # are one-shot pokes at `next_run_at: 0001-01-01`. So there was exactly
        # one recurring watcher and nothing watching IT.
        #
        # ⚠️ THE EXISTING LATCH COULD NOT HAVE ANSWERED THIS, which is why a new
        # receipt exists rather than a new read of an old file.
        # `QUEUE-WATCH-STATE.json` is written only when a page FIRES, so on a
        # quiet queue it is never written and its absence collapses "the Routine
        # never ran" into "the Routine ran and had nothing to say" -- opposite
        # facts, one value. `queue_latency.py --write-receipt` now writes
        # `MANAGER-QUEUE-WATCH.json` on EVERY run, and this grades its age.
        #
        # ⚠️ IT GRADES THE ROUTINE'S LIVENESS, NEVER THE QUEUE'S DEPTH. A
        # contributor's PR must not go red because the manager is sitting on
        # blocked sub-sessions -- the same objection this file already records
        # against the pr-queue-watch and trainer-capture entries. The depth is
        # PRINTED here and escalated by the Routine's own run.
        #
        # ⚠️ `never_ran` PASSES and that is correct rather than lenient: it is the
        # accurate reading until the Routine next fires with `--write-receipt`,
        # and failing on it would red every PR on the day this merges -- which is
        # how a guard gets disabled instead of fixed. It arms itself on the first
        # receipt.
        #
        # `when: None`: a Routine can die without any PR touching its files,
        # which is precisely the case that must be caught.
        "name": "manager-queue-watch-guard",
        "when": None,
        "steps": [
            # Both directions, on both halves -- a planted defect fires and a
            # clean input stays quiet. One direction proves a check runs, never
            # that it discriminates.
            ["python3", "scripts/ops/queue_latency.py", "--self-test"],
            ["python3", "scripts/ops/manager_state_watch.py", "--self-test"],
            ["python3", "scripts/ci/check_manager_queue_watch.py", "--self-test"],
            ["python3", "scripts/ci/check_manager_queue_watch.py"],
        ],
    },
    {
        # THE ALARM ON THE TRAINER'S FORWARD-ONLY ORDER-FLOW CAPTURE.
        # `trainer-capture-watch.yml` grades the mtime of the capture's own
        # output file. This entry is what makes that watcher a GUARD rather than
        # a cron somebody hopes is firing: a watcher that quietly stopped would
        # leave the capture in exactly the unmonitored state
        # OI-20260829-TRAINER-IS-NOW-A-DECIDED-DEPENDENCY-AND-IS-UNMONITORED was
        # filed for, while everything looked fine.
        #
        # ⚠️ IT GRADES THE WATCHER'S LIVENESS, NEVER THE CAPTURE'S HEALTH. A
        # contributor's PR must not go red because the trainer's capture stalled
        # -- same objection as the pr-queue-watch entry above. The capture's own
        # verdict is escalated by the watcher's own run (which fails, and pages).
        #
        # ⚠️ `never_ran` PASSES and that is correct rather than lenient: it is
        # the accurate reading until the workflow first fires, and failing on it
        # would red every PR on the day this merges. It arms itself on the first
        # real run.
        #
        # `when: None`: a watcher can die without any PR touching its files,
        # which is precisely the case that must be caught.
        "name": "trainer-capture-watch-guard",
        "when": None,
        "steps": [
            # Both directions, on both halves -- a planted defect fires and a
            # clean input stays quiet. One direction proves a check runs, never
            # that it discriminates.
            ["python3", "-m", "pytest",
             "tests/test_orderflow_capture_freshness.py", "-q"],
            ["python3", "scripts/ci/check_trainer_capture_watch.py", "--self-test"],
            ["python3", "scripts/ci/check_trainer_capture_watch.py"],
        ],
    },
    {
        # IS THE OPERATOR'S DIGEST STILL ARRIVING? On 2026-09-02 the operator
        # asked "no pings for 3 hours?" -- it was four -- and nothing in the
        # repo knew. F6 makes operator notification the CONDITION the autonomy
        # grant rests on, so that precondition had been unmet all day unnoticed.
        #
        # `src_red_crons` cannot cover this: it grades the latest scheduled
        # run's CONCLUSION, so a cron that never fires leaves a stale-but-green
        # latest run and reads clean -- a missed slot and a quiet hour are
        # indistinguishable from it. Worse, it rides `due-list.yml`, itself a
        # cron, itself measured landing 4h07m late; a cron watchdog for crons
        # cannot report its own carrier dying.
        #
        # `when: None`: the digest can stop without any PR touching its files,
        # which is precisely the case that must be caught. And `pull_request`
        # is an event this repo has measured firing within seconds.
        #
        # It PASSES on `never_ran` and arms itself on the first landed receipt,
        # for the reason the pr-queue-watch guard above records: failing on it
        # would red every PR the day this merges, which is how a guard gets
        # disabled instead of fixed.
        "name": "digest-liveness-guard",
        "when": None,
        "steps": [
            # Both directions, on both halves -- a planted defect fires and a
            # clean input stays quiet. One direction proves a check runs, never
            # that it discriminates.
            ["python3", "scripts/ops/digest_due.py", "--self-test"],
            ["python3", "scripts/ci/check_digest_liveness.py", "--self-test"],
            ["python3", "scripts/ci/check_digest_liveness.py"],
        ],
    },
    {
        # The hand-maintained cron watch list in claude-run-failure-alert.yml
        # has been asserted-complete and been false TWICE (2026-08-21 count
        # said 12 and "ALL 12 are now listed"; measured 2026-08-31 there were
        # 14, with `research-queue-dispatch` — the research queue's own
        # scheduler — unwatched after already failing twice). A scheduled run
        # that dies notifies NOBODY, so an unwatched cron is not merely
        # un-alerted, it is unobservable. `when: None` so it runs on every
        # diff: a cron added in a PR that touches no workflow file still
        # changes the population this guard is about.
        "name": "cron-failure-watch",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_cron_failure_watch.py", "--self-test"],
            ["python3", "scripts/ci/check_cron_failure_watch.py"],
        ],
    },
    {
        # `docs/claude/INDEX.md` is the surface a session reads to answer "is
        # there already a skill for this?". Measured 2026-08-31 it named 12 of
        # 31 — a negative read off it had no denominator, so a session would
        # improvise a capability that already existed
        # (RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED). `when: None`: a PR that
        # adds a skill need not touch the index, which is precisely the case
        # that must fail.
        "name": "skills-index",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_skills_index.py", "--self-test"],
            ["python3", "scripts/ci/check_skills_index.py"],
        ],
    },
    {
        # The scope-overlap detector's LIVE check needs the coordination board,
        # so it cannot run here — a guard whose verdict depends on a GitHub read
        # reds on an outage rather than on a defect. Only the self-test runs, and
        # that is the part worth pinning: its 28 planted controls include the
        # real 2026-08-31 comment whose "Not touching:" line the first version
        # read as a DECLARATION, firing on the one file the other session had
        # promised to avoid. An inverted alarm is worse than no alarm.
        "name": "scope-overlap-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_scope_overlap.py", "--self-test"],
        ],
    },
    {
        # Every `monitoring` row must declare a probe OR why it has none, so
        # "nothing probes this" stays distinguishable from "a probe ran and was
        # quiet". `--check` validates DECLARATIONS ONLY — it runs no probe and
        # opens no socket, because a guard whose verdict depends on the live VM
        # reds on an outage rather than on a defect. The probes themselves run
        # on the `probes` schedule.
        "name": "probe-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/run_probes.py", "--self-test"],
            # One self-test per probe BINARY. probe_lib holds the shared
            # predicate engine + the three-state exit contract, and every probe
            # binary re-runs its controls before its own — so a change that
            # broke `could_not_look` reds here rather than being discovered as a
            # confident negative on a schedule.
            ["python3", "scripts/ops/probe_lib.py"],
            ["python3", "scripts/ops/probe_soak.py", "--self-test"],
            ["python3", "scripts/ops/probe_file.py", "--self-test"],
            ["python3", "scripts/ops/probe_api.py", "--self-test"],
            ["python3", "scripts/ops/probe_actions_log.py", "--self-test"],
            ["python3", "scripts/ops/run_probes.py", "--check"],
        ],
    },
    {
        # The due-list is the one surface that answers "what is due right now?"
        # across every structured register. This does NOT check freshness — a
        # committed snapshot is stale by construction and a clock-based failure
        # would red every unrelated PR (the lesson session-brief-guard already
        # learned, OI-20260831-SESSION-BRIEF-DIFF-SCOPING-...). It checks the
        # one thing that is never acceptable: a list that claims completeness
        # it never had. A `partial` verdict MUST name the source it could not
        # read, or an empty section reads as "nothing is due" when it means
        # "nobody looked" — the `curl … || echo '{}'` failure in CLAUDE.md.
        # The executable half of the 2026-09-02 standing operator directive:
        # "anything soaking needs to be logged with an alarm that has either a
        # timer or a soak threshold, so that we know to get back to it when the
        # soak is ready."
        #
        # `when: None` — it runs on EVERY diff, deliberately. A diff-scoped
        # version would pass vacuously on every PR that touches no soak writer,
        # which is nearly all of them: a green that checked nothing. The
        # pre-2026-09-02 debt is carried in an explicit dated BASELINE inside
        # the script instead, so adding to it is a visible line in a PR diff
        # rather than a silent skip — the `new-table-wiring-guard` lesson, where
        # a presence-only marker made lying cheaper than complying.
        #
        # Its self-test runs first and carries a PLANTED POSITIVE (a new soak
        # writer with no register row must FAIL): a guard that has only ever
        # reported clean is indistinguishable from one that scans nothing.
        "name": "soak-registered-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_soak_registered.py", "--self-test"],
            ["python3", "scripts/ci/check_soak_registered.py"],
        ],
    },
    {
        "name": "due-list-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/render_due_list.py", "--self-test"],
            ["python3", "scripts/ops/render_due_list.py", "--check"],
            # The soak grader the due-list's `soaks` source imports. Its own
            # controls prove the four states are reachable and DISTINCT —
            # `not_writing` (the soak is dead) must never render as `accruing`
            # (it is alive and waiting) or as `unknown` (we could not look).
            ["python3", "scripts/ops/soak_alarm.py"],
        ],
    },
    {
        # The error feed the `duty` pass triages. Same posture as
        # due-list-guard above and for the same reason: it does NOT check
        # freshness — a committed digest is stale by construction and a
        # clock-based failure would red every unrelated PR. It checks the one
        # thing that is never acceptable, that a digest claims a completeness
        # it never had: a `partial` verdict MUST name the feed it could not
        # read, or a quiet section reads as "nothing fired" when it means
        # "nobody looked".
        #
        # The self-test runs FIRST, deliberately. Its controls are the ones a
        # reader's conclusion depends on — an unreachable feed not rendering as
        # empty, a digit-varying flood collapsing to one row, and the watermark
        # never advancing over a window nobody read. A grouper that regressed
        # would land an artifact a session then triages, and the artifact
        # itself would look fine.
        "name": "error-feed-digest-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/error_feed_digest.py", "--self-test"],
            ["python3", "scripts/ops/error_feed_digest.py", "--check"],
        ],
    },
    {
        # The DAILY BRIEF — the artifact the operator is handed in the morning
        # and pastes as the opening of the next manager's prompt (MI-75,
        # WO-20260901-PHASE-E). The acceptance criterion is the operator's own
        # sentence: it must say "what was done overnight and what was wrapped
        # up after I went to bed, SO THAT I KNOW WHERE I'M STARTING OFF FROM."
        #
        # ⚠️ `--check` GRADES THE CODE, NOT THE DATA, and that is deliberate. An
        # unreadable register is a real problem, but failing here on it would
        # red every open PR for a defect none of them introduced — the lesson
        # session-brief-guard already learned
        # (BL-20260830-A-TRANSIENT-RED-BASE-PERMANENTLY-STRANDS-AN-AUTOMERGE-BRANCH),
        # and the same polarity as workflow-trigger-reachability's "an
        # unreadable origin PASSES (loudly)". A broken register is printed as a
        # ::NOTICE:: and passes; what FAILS is the renderer raising over the
        # live registers, or an invariant SENTENCE going missing in a refactor
        # — the brief still rendering while quietly no longer saying the thing
        # it exists to say is the only failure a smoke test would miss.
        #
        # ⚠️ It is deliberately WINDOWLESS and OFFLINE: no git window (a shallow
        # checkout is the normal state of a session's clone) and no due-list
        # collection (it reaches api.github.com). A guard that can fail on clone
        # depth or an API blip reds unrelated PRs.
        #
        # NOT A WALL — measured before wiring. At the head this shipped on,
        # `--check` exits 0 over all 6 registers, and prints the checklist's
        # `done` (11) and `landed_unproven` (17) as SEPARATE numbers, which is
        # the invariant the whole artifact turns on: a merge is a deploy, not an
        # observation, and an item reported finished whose effect was never seen
        # actively misinforms the person starting the day.
        "name": "daily-brief-guard",
        "when": None,
        "steps": [
            # Planted controls in BOTH directions — a defect fires and a clean
            # input stays quiet. One direction proves a check runs, never that
            # it discriminates.
            ["python3", "scripts/ops/render_daily_brief.py", "--self-test"],
            ["python3", "scripts/ops/render_daily_brief.py", "--check"],
        ],
    },
    {
        "name": "recurrence-ledger-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_recurrence_ledger.py", "--self-test"],
            ["python3", "scripts/ci/check_recurrence_ledger.py"],
            # The prevention named by RC-STORED-FIELD-READ-AS-ITS-NAME. Its
            # self-test proves it can find a positive — a provenance probe that
            # silently matches nothing would make every column look unambiguous.
            ["python3", "scripts/ops/column_provenance.py", "--self-test"],
            ["python3", "scripts/ops/strategy_liveness.py", "--self-test"],
        ],
    },
    {
        "name": "artifact-validity-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/check_artifact_validity.py", "--allow-missing"],
            # The duplicate pre-check `backlog_append` refuses on. Its
            # self-test proves it can find a positive — a similarity probe
            # that silently matches nothing would make every filing look novel.
            ["python3", "scripts/ops/backlog_search.py", "--self-test"],
            # The round-trip check that pytest-run CANNOT run on a backlog-only
            # PR: the three backlogs are deliberately excluded from its relevance
            # filter (they change on nearly every PR), so the test that catches a
            # hand-spliced row could not fire on the PR that introduced one.
            # Measured 2026-09-01: append_row then refused EVERY write repo-wide
            # and the signal surfaced hours later on three unrelated PRs. This job
            # never short-circuits, which is the entire reason the check lives here.
            ["python3", "scripts/ops/backlog_append.py", "--check-live"],
            # Same shape, same reason, different file: every row in
            # docs/claude/pending-pings.jsonl must render an operator-visible
            # BODY, and pytest-run CANNOT check that — it short-circuits on a
            # docs/-only diff, which is precisely what a queued ping is. The
            # pytest version was refused by
            # tests/test_pytest_run_filter.py::test_docs_committed_readers_are_all_covered,
            # correctly. Widening pytest-run's filter instead was rejected: the
            # work-digest workflow appends here every 4h via an auto-merge PR,
            # so it would put a ~15-minute suite on a routine generated commit.
            # Self-test first — a probe that cannot find a planted positive
            # would make every queue look clean.
            ["python3", "scripts/ci/check_pending_pings_render.py", "--self-test"],
            ["python3", "scripts/ci/check_pending_pings_render.py"],
            ["python3", "scripts/ci/check_workflow_failure_swallow.py"],
            ["python3", "scripts/ops/check_allow_degraded.py"],
            ["python3", "scripts/ops/check_research_index.py", "--list"],
            # WARN-ONLY target reachability (operator decision 2026-08-24:
            # "warn, do not refuse"). Its MECHANISM is gated — a broken
            # self-test or an unreadable config exits 1 — while its FINDINGS
            # never fail the build. That split is the whole point: a cosmetic
            # target is a decision for the operator, but a report that has
            # silently stopped being able to SEE one is a defect.
            ["python3", "scripts/research/target_reachability_report.py"],
            # The e35 corpus extractor is the durable half of a sweep whose
            # evidence was previously write-only. Its self-test pins the
            # distinctions that make the corpus trustworthy — an ungated cell
            # never reads as a passing one, a re-extract supersedes rather
            # than appends, and a foreign report.json is refused — none of
            # which move when the data does.
            ["python3", "scripts/research/e35_corpus_extract.py", "--selftest"],
            # The e35->verdicts adapter is what lets the CANONICAL
            # `m20_banking_risk_adjusted.py` read a bracket sweep, instead of a
            # second implementation of MAR free to drift from it. Its self-test
            # pins the distinctions a reader depends on: the NET (not gross)
            # column lands in the gated slot, a cell the sweep never split is
            # counted rather than emitted with a null half, a corrupt row is a
            # refusal rather than a smaller sample, and `dd_per_r` is null for a
            # return-GAINING cell by construction.
            ["python3", "scripts/research/e35_verdicts_adapter.py", "--selftest"],
            # The backlog union-merge resolver. Three conflicts on
            # health-review-backlog.json in one evening (2026-08-23) across two
            # PRs, and a hand-resolved one once silently reverted six items
            # (BL-20260814-HAND-RESOLVED-BACKLOG-MERGE-SILENTLY-REVERTED-SIX-ITEMS-INCLUDING-A-RESOLUTION).
            # Its self-test pins the REFUSALS, which are the whole value: a
            # divergent both-side edit, a duplicate new id, and a deletion on
            # either side must all refuse rather than union — while an
            # IDENTICAL both-side edit must NOT refuse, since there is nothing
            # to pick.
            ["python3", "scripts/ops/backlog_union_merge.py", "--selftest"],
            # The bracket-expectation census is manual-RUN (no cadence should
            # re-count the fleet automatically, and a CI job pinning a count
            # would fail on every legitimate retune) — but its INVARIANTS do not
            # move: an explicit target always beats a class default, a family
            # with no default stays ungradeable rather than silently becoming a
            # sentinel, and cap_r stays inversely proportional to the stop. A
            # self-test nobody invokes is worse than a missing one, so it runs here.
            ["python3", "scripts/research/bracket_expectation_census.py", "--selftest"],
            ["python3", "scripts/research/adx_entry_distribution.py", "--selftest"],
            # The bracket-reachability audit answers whether a DECLARED target
            # is the operative exit or whether the 9.9% venue clamp gets there
            # first. Its self-test pins the distinctions that make the answer
            # trustworthy and that a data refresh cannot move: the DERIVED
            # median-basis truncation label never merges with the OBSERVED
            # byte-identity cosmetic label; a cell matching a no-target baseline
            # on net_R but NOT on drawdown is not "changed nothing"; a cell with
            # no same-stop baseline is `no_baseline` rather than `not_cosmetic`;
            # and — the regression that fired on the real corpus — a cosmetic
            # cell on a leg with NO measured cap_r is UNVERIFIABLE, not a
            # violation. It also pins the positive control, without which a
            # zero-pass filter reads as a measured negative.
            ["python3", "scripts/research/bracket_reachability_audit.py", "--selftest"],
            # A NEW tracking reference that resolves to nothing is a PR-scoped
            # question (it needs a base to diff against); the whole-repo sweep
            # below stays advisory exactly as it was.
            {
                "argv": ["python3", "scripts/ops/check_backlog_refs.py", "--base", "origin/{base_ref}"],
                "pr_only": True,
            },
            # A NEW row must say what DONE looks like. Diff-scoped on purpose:
            # the standing debt is 114 of 262 open rows (measured 2026-08-12),
            # so a whole-tree gate would fail on day one and get switched off —
            # strictly worse than grandfathering the past and holding the
            # future to the rule. Motivated by two HIGH rows found
            # finished-but-open the same day, both for want of criteria.
            {
                "argv": ["python3", "scripts/ops/check_backlog_criteria.py", "--base", "origin/{base_ref}"],
                "pr_only": True,
            },
            # The debt stays VISIBLE rather than forgotten — advisory, never
            # blocking, so it cannot become the reason someone disables the
            # blocking half above.
            {
                "argv": ["python3", "scripts/ops/check_backlog_criteria.py", "--all"],
                "allow_fail": True,
            },
            ["python3", "scripts/ops/check_backlog_criteria.py", "--self-test"],
            # The accrual-clock module the guard above imports. Its five clock
            # states (can_run / gated_shadow / gated_disabled / not_routed /
            # absent_from_config) are what let a row waiting for trades be told
            # apart from one waiting for trades THAT CANNOT ARRIVE — measured
            # 2026-09-02, four of eleven accrual rows in the performance
            # backlog named a leg that is shadow-gated or has zero journal rows
            # ever. A state nothing can produce is a state nobody can rely on,
            # so the self-test asserts every one of the five is reachable.
            ["python3", "scripts/ops/accrual_clock.py", "--self-test"],
            # The standing census, advisory like its sibling above so it cannot
            # become the reason the blocking half gets switched off.
            {
                "argv": ["python3", "scripts/ops/accrual_clock.py", "--all"],
                "allow_fail": True,
            },
            {
                "argv": ["python3", "scripts/ops/check_backlog_refs.py", "--all"],
                "allow_fail": True,
                "hint": "pre-existing dangling references are advisory, not gating",
            },
            ["python3", "scripts/ops/check_workflow_shell.py"],
            [
                "python3", "-m", "pytest",
                "tests/test_check_artifact_validity.py",
                "tests/test_check_research_index.py",
                "tests/test_check_backlog_refs.py",
                "-q",
            ],
        ],
    },
    {
        "name": "operator-owed-guard",
        # ⚠️ UNGATED (`when: None`), and that is the whole design. This is
        # part (d) of
        # BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION:
        # it fails when an item is CARRIED without moving. The
        # condition is the passage of register commits and of time, so a
        # diff-scoped run would be blind to exactly the case it exists for — an
        # item rotting while nobody edits the register. Same reasoning as
        # api-tier-policy's completeness backstop, and the reason a per-step
        # `when` is wrong here too (BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH).
        "when": None,
        "steps": [
            # Self-test FIRST: a guard whose failure path is never exercised is
            # indistinguishable from one that always passes.
            ["python3", "scripts/ci/check_operator_owed.py", "--self-test"],
            ["python3", "scripts/ci/check_operator_owed.py"],
            ["python3", "-m", "pytest", "tests/test_operator_owed.py",
             "tests/test_over_cover_decision.py", "-q"],
        ],
        # ⚠️ DELIBERATELY **NOT** `notify: True`, and the reason is the whole
        # subject of this guard — do not "fix" this by adding it.
        #
        # The first draft set it, on the reasoning that an escalation nobody is
        # told about is the re-listing it replaces. `tests/ci/test_run_guards.py
        # ::test_notify_set_is_preserved` caught that as an undeclared
        # behaviour change, correctly, and looking at the notify path settles
        # it the other way: `guards.yml`'s ping fires **once per PR run, with
        # no latch and no per-condition dedupe**.
        #
        # Every one of the six notify-class guards is DIFF-SCOPED — it trips on
        # something the PR introduced, so the ping fires once, to the author who
        # can fix it. This guard is UNGATED and its condition PERSISTS ACROSS
        # PRs: an aged-out item would ping the operator on every PR from every
        # session until somebody moved it. That is the shape that put 202
        # CRITICALs on the operator's channel for two already-filed positions
        # (BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART), and this
        # register exists to END alarm-fatigue, not to add a source of it.
        #
        # So the CI failure IS the escalation. It reaches the session, which is
        # the right first responder and has all four printed dispositions
        # available. An operator ping is a legitimate future addition, but it
        # needs a DURABLE per-item latch (the `_cooldown_admits` shape) that
        # this notify path does not have — adding the flag without one would be
        # trading a silent list for a noisy one.
    },
    {
        "name": "async-route-blocking-guard",
        "when": {"globs": ["src/web/api/**/*.py", "scripts/ci/check_async_route_blocking.py"]},
        "steps": [["python3", "scripts/ci/check_async_route_blocking.py"]],
    },
    {
        "name": "canonical-config-loaders",
        "when": {"regex": r"\.py$|config/accounts\.yaml$"},
        "steps": [["python3", "scripts/check_canonical_config_loaders.py", "--list"]],
    },
    {
        "name": "canonical-db-resolver",
        "when": {"regex": r"\.py$|\.sh$"},
        "steps": [["python3", "scripts/check_canonical_db_resolver.py", "--list"]],
    },
    {
        # M20's done-condition lives in the coverage matrix, and the matrix is
        # only as good as its statuses. A `status: null` sat in it undetected
        # from the 2026-08-09 explosion until 2026-08-12 — not a legend value,
        # so nothing could grade the cell, and no reader saw it because the
        # roll-up was hand-counted. Validates: every status is a legend value,
        # and every CLOSED live cell carries the evidence ref the matrix's own
        # `_doc` requires ("statuses only from verified evidence").
        "name": "exit-coverage-matrix-guard",
        # `config/strategies.yaml` is in this list because the check JOINS the
        # matrix against it (`execution` must agree). Without it the guard was
        # scoped to one side of its own join, so the single edit that can make
        # the matrix stale -- flipping a leg's `execution` -- was the one edit
        # that would not run it. Measured 2026-08-23: demoting
        # htf_pullback_trend_2h to shadow left the matrix declaring it `live`,
        # the guard reported SKIP (not relevant to this diff), and the defect
        # reached CI, where the test that invokes it with `--all` caught it.
        # A guard scoped to one side of a two-sided check is quiet exactly when
        # it should not be.
        "when": {"globs": ["docs/research/exit-refinement-coverage.json",
                           "config/strategies.yaml",
                           "scripts/research/m20_coverage_rollup.py",
                           "scripts/research/m20_explode_coverage_rows.py"]},
        "steps": [["python3", "scripts/research/m20_coverage_rollup.py", "--check"]],
    },
    {
        # The CROSS-ARTIFACT sibling of the guard above: that one validates the
        # matrix against ITSELF (legend values, refs present); this one validates
        # it against the CORPUS the dispositions rest on. Both files can be
        # internally valid and disagree with each other, and nothing checked
        # that — measured 2026-08-14, 100 of 186 stale cells already had a
        # live-parity corpus row and 9 of those PASSED against a recorded
        # negative (BL-20260814-STALE-CELL-BACKLOG-IS-HALF-ANSWERED-BY-THE-CORPUS-ALREADY).
        #
        # NOT a subset of the staleness pass: the first run of this guard found
        # TWO disagreements that are NOT in `stale_cells` at all, because their
        # refs carry post-cutover dates so the date-proxy reads them as current
        # while the STATUS rests on an older negative. A staleness scan
        # structurally cannot reach those.
        "name": "matrix-corpus-agreement",
        "when": {"globs": ["docs/research/exit-refinement-coverage.json",
                           "docs/research/m20-sweep-corpus.jsonl",
                           "scripts/ci/check_matrix_corpus_agreement.py"]},
        # Self-test FIRST, so a guard that silently stopped matching cannot read
        # as a clean pass — it proves it catches a planted disagreement, clears
        # on an acknowledgement, and honours supersession.
        "steps": [["python3", "scripts/ci/check_matrix_corpus_agreement.py", "--self-test"],
                  ["python3", "scripts/ci/check_matrix_corpus_agreement.py"]],
    },
    {
        # The sibling of matrix-corpus-agreement, one axis over: that one checks
        # the matrix against the EVIDENCE, this one against the FIELD. Config is
        # what the trader loads; the matrix is prose about it, so a disagreement
        # is always a stale RECORD and never a reason to touch a declare.
        #
        # Found six cells on its first run, five reading `honest_negative`
        # -- "measured, did not work" -- about a trail_decay running live on
        # that leg. The reverse direction was clean, which is why the guard
        # checks BOTH: a guard that only ever looked one way would report that
        # clean as evidence when it had never looked.
        "name": "matrix-config-agreement",
        "when": {"globs": ["docs/research/exit-refinement-coverage.json",
                           "config/strategies.yaml",
                           "scripts/ci/check_matrix_config_agreement.py",
                           "scripts/research/m20_fleet_exit_sweep.py"]},
        # Relevance follows config/strategies.yaml AND the sweep, not just the
        # matrix: a DECLARE landing in config falsifies a cell without anyone
        # editing the matrix, which is exactly how these six drifted.
        "steps": [["python3", "scripts/ci/check_matrix_config_agreement.py", "--self-test"],
                  ["python3", "scripts/ci/check_matrix_config_agreement.py"]],
    },
    {
        # matrix-bracket-values — the SIBLING of matrix-config-agreement, on the
        # column that guard structurally cannot cover.
        #
        # matrix-config-agreement grades whether a lever is ARMED, over exactly four
        # levers. `bracket_geometry` is not one, and correctly so: its `_arms()` tests
        # key PRESENCE and every leg always declares tp_r/atr_stop_mult, so including
        # the column would demand `shipped` everywhere. The cost was a column with NO
        # staleness detector: #10419 declared validated geometry on 8 LIVE legs, real
        # money, and the matrix carried all 8 as `passed_unshipped` for the rest of
        # the day while matrix-config-agreement stayed GREEN — because arming was
        # never the question.
        #
        # This asks the question that IS falsifiable there: the cell id encodes the
        # geometry (`tp3_sm2` => tp_r 3.0 AND atr_stop_mult 2.0), so a `shipped` cell
        # is a checkable claim about the declare. Registered in the same change that
        # reconciled the 8 cells, so its first CI run is green — the discipline this
        # guard's own sibling header argues for.
        "name": "matrix-bracket-values",
        "when": {"globs": ["docs/research/exit-refinement-coverage.json",
                           "config/strategies.yaml",
                           "scripts/ci/check_matrix_bracket_values.py"]},
        # Relevance follows config/strategies.yaml as well as the matrix: a DECLARE
        # landing in config falsifies a `shipped` cell with nobody editing the matrix,
        # which is exactly the drift this exists to catch.
        "steps": [["python3", "scripts/ci/check_matrix_bracket_values.py", "--self-test"],
                  ["python3", "scripts/ci/check_matrix_bracket_values.py"]],
    },
    {
        "name": "canonical-doc-coherence",
        # The `declared values` check reads .github/workflows/ + src/web/api/
        # sources, so a change THERE can falsify a doc without touching one —
        # which is exactly how the 2026-08-10 drift happened (a workflow value
        # flipped; five docs became wrong; no doc was edited). Relevance must
        # follow the SOURCES, not just the docs.
        "when": {"globs": ["CLAUDE.md", "docs/**", ".claude/**",
                           ".github/workflows/branch-protection-sync.yml",
                           "src/web/api/routers/prop.py",
                           "src/web/api/routers/devices.py",
                           "scripts/ci/check_canonical_doc_coherence.py"]},
        "steps": [
            # Runs on EVERY invocation: this guard is otherwise only ever seen
            # passing, and a doc-drift check that cannot fail IS the drift.
            ["python3", "scripts/ci/guard_selftests.py", "canonical-doc-values"],
            ["python3", "scripts/ci/check_canonical_doc_coherence.py"],
        ],
    },
    {
        "name": "claim-basis-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/check_claim_basis.py", "--base", "origin/{base_ref}"],
            ["python3", "scripts/ci/guard_selftests.py", "claim-basis"],
        ],
    },
    {
        "name": "impossibility-claim-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/check_impossibility_claims.py", "--base", "origin/{base_ref}"],
            ["python3", "scripts/ci/guard_selftests.py", "impossibility-claim"],
            # The STANDING half. The --base run above is diff-scoped, which is
            # right for new lines and structurally blind to rows nobody edits --
            # 36 unsubstantiated claims across 14 files sat un-reported because
            # of exactly that. The ratchet grades every tracked file against a
            # committed per-file baseline, so it never fails a PR for the
            # pre-existing 36 and always fails one that ADDS to a file.
            ["python3", "scripts/check_impossibility_claims.py", "--all", "--ratchet"],
        ],
    },
    {
        "name": "diag-unit-allowlist-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/check_diag_unit_allowlist.py"],
            ["python3", "scripts/ci/guard_selftests.py", "diag-unit-allowlist"],
        ],
    },
    {
        "name": "roadmap-status-glyph-guard",
        # F-28 (full-system-audit 2026-08-20): this guard was WRITTEN and never
        # REGISTERED — referenced by no workflow, no unit, no script. A guard
        # that has never run is "green is not evidence" one step earlier than
        # check_selftest_wiring catches (that one finds registered-but-never-
        # invoked; this was written-but-never-registered). Verified passing on
        # main before wiring, so registering it blocks nothing.
        # NOTE: it carries no failure-path self-test, so it joins the 31 guards
        # that are still unproven instruments — tracked separately.
        "when": {"regex": r"^ROADMAP.*\.md$"},
        "steps": [
            ["python3", "scripts/check_roadmap_status_glyphs.py"],
        ],
    },
    {
        "name": "test-schema-fidelity-guard",
        # A fixture that declares a money-table column production does NOT have
        # lets a query against that column pass CI and raise in production. That
        # is BL-20260810 exactly: `order_packages.id` in the pairs tests, so
        # `max_hold_bars: 20` was never once evaluated and legs ran 300-595 bars.
        # The fix at the time swept the pairs tests; 20 other files still declare
        # it (measured 2026-08-20). Self-test runs on EVERY invocation.
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_test_schema_fidelity.py", "--self-test"],
            {
                "argv": ["python3", "scripts/ci/check_test_schema_fidelity.py",
                         "{pr_diff}"],
                "when": {"regex": r"^tests/.*\.py$"},
            },
        ],
    },
    {
        "name": "unwired-artifact-guard",
        # "We don't keep building things out half way and then leaving them to
        # rust" (operator, 2026-08-20). A capability that ships without a runner
        # is the class behind trainer_dataset_gc.py sitting unrun while its disk
        # reached 93%.
        #
        # ⚠️ THIS RAN SELF-TEST-ONLY UNTIL 2026-08-22 (workplan item 0.3), and
        # the comment here CLAIMED it was diff-scoped while no scan step existed
        # at all — a guard registered, described as blocking, and blocking
        # nothing. That is the same shape as the thing it hunts: something built
        # and never wired to anything that runs it. The scan step below is the
        # blocking half; the self-test stays because a guard whose failure path
        # is never exercised is indistinguishable from one that always passes.
        #
        # Diff-scoped ON ADDED FILES ONLY: the repo carries ~161 pre-existing
        # unwired tools, and failing every PR for that debt would be the
        # desensitized alarm this repo names as a P1 in its own right. Judging
        # only what a change INTRODUCES stops the debt GROWING without blocking
        # on its existence; `--dir` remains the report-only standing audit.
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_unwired_artifacts.py", "--self-test"],
            ["python3", "scripts/ci/check_unwired_artifacts.py",
             "--base", "origin/{base_ref}"],
        ],
    },
    {
        "name": "diagnostic-provenance-guard",
        # The self-test runs on EVERY invocation of this guard — including when
        # the scan itself is skipped — because a guard whose failure path is
        # never exercised is indistinguishable from one that always passes.
        "when": None,
        "steps": [
            ["python3", "scripts/ci/guard_selftests.py", "diagnostic-provenance"],
            {
                "argv": ["python3", "scripts/check_diagnostic_provenance.py", "{pr_diff}"],
                "when": {"regex": r"^scripts/.*\.py$"},
            },
            # The whole-tree backstop, deliberately UNGATED — the
            # api-tier-policy-guard pattern, for the same reason.
            #
            # This could not exist until 2026-09-02: `--all` reported 52
            # grandfathered findings, so an ungated step would have failed
            # every PR on day one and been switched off. The standing audit is
            # now at ZERO (all 52 triaged and fixed; the `# inert:` override
            # was tightened from presence-only to verified in the same change),
            # so the residue can be held there instead of being re-measured by
            # hand every few weeks and found unchanged.
            #
            # Why UNGATED rather than diff-scoped: the diff-scoped step above
            # cannot see a regression it does not touch — a site becomes
            # unprovenanced when an unrelated PR adds the probability-shaped
            # LABEL, or removes the `print` that made an input selection
            # visible, three lines from code it never edited. That invisibility
            # is exactly what let the residue sit at 52 for 26 days across five
            # review passes (BL-20260807-DIAGPROV-STANDING-AUDIT-NEVER-DRAINED).
            ["python3", "scripts/check_diagnostic_provenance.py", "--all"],
        ],
    },
    {
        "name": "dry-run-guard",
        "when": {"regex": r"\.py$|config/accounts\.yaml$|config/strategies\.yaml$"},
        "steps": [["python3", "scripts/check_dry_run_in_diff.py", "{pr_diff}"]],
        "notify": True,
    },
    {
        "name": "env-gate-guard",
        "when": {"regex": r"\.py$|\.sh$"},
        "steps": [["python3", "scripts/check_env_gate_in_diff.py", "{pr_diff}"]],
        "notify": True,
    },
    {
        # The COMPLEMENT of lever-wiring-guard below, not a duplicate: this
        # one starts from a YAML key and asks whether the debt matrix
        # classifies it; that one starts from a lever and asks whether its
        # consumers exist. rr_floor is out of THIS guard's scope entirely (no
        # enabled strategy declares it), which is how it shipped unrunnable.
        "name": "harness-lever-coupling-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/check_harness_lever_coupling.py"],
            ["python3", "scripts/ci/guard_selftests.py", "harness-lever-coupling"],
        ],
    },
    {
        # The LEVER analogue of provenance-consumer-guard. That guard fails CI
        # when a declared provenance FIELD gains a writer and no reader; this
        # one fails when an exit LEVER can be run but nothing grades it, or is
        # graded but no unit can run it. Four findings on 2026-08-18 were that
        # one shape (IB broker-PnL reader with no caller, attach_ib_target
        # never executed, rr_floor shipped with no sweep cell, both coverage
        # audits blind to a shared lever) and nothing asserted the relationship.
        # Found a real gap on its first run: rr_floor, units=0.
        "name": "lever-wiring-guard",
        "when": {"globs": ["src/units/strategies/*.py",
                           "src/runtime/exit_levers.py",
                           "scripts/research/m20_fleet_exit_sweep.py",
                           "scripts/ops/exit_mechanism_coverage.py",
                           "scripts/ci/check_lever_wiring.py"]},
        "steps": [["python3", "scripts/ci/check_lever_wiring.py"]],
    },
    {
        "name": "json-extract-guard",
        "when": {"regex": r"\.py$|\.sh$"},
        "steps": [["python3", "scripts/ci/check_json_extract_guarded.py", "--verbose"]],
    },
    {
        "name": "json-notes-cap-guard",
        "when": {"globs": ["src/**/*.py", "scripts/ci/check_json_notes_cap.py"]},
        "steps": [["python3", "scripts/ci/check_json_notes_cap.py"]],
    },
    {
        "name": "layer-guard",
        "when": {"regex": r"\.py$|\.importlinter$|requirements-dev\.txt$"},
        "steps": [["lint-imports", "--config", ".importlinter"]],
    },
    {
        "name": "news-feed-coverage-guard",
        # Runs on the two registries that decide the answer + the resolver +
        # the guard itself. instruments.yaml is in the list because ADDING an
        # instrument is now what grants news coverage — that is the whole point
        # of deriving it, and it is also the moment coverage can regress.
        "when": {
            "globs": [
                "config/instruments.yaml",
                "config/news_feeds.yaml",
                "config/accounts.yaml",
                "config/strategies.yaml",
                "src/core/instrument_class.py",
                "src/news/news_feeds.py",
                "scripts/ci/check_news_feed_coverage.py",
            ]
        },
        "steps": [["python3", "scripts/ci/check_news_feed_coverage.py"]],
        # Deliberately NOT in the notify set. `tests/ci/test_run_guards.py::
        # test_notify_set_is_preserved` pins that set and caught this guard
        # being added to it — correctly: "that is a behaviour change, not
        # packaging."
        #
        # The six guards that DO ping (dry-run, env-gate, new-table-wiring,
        # silent-empty, strategy-risk, writer-conformance) all police defects
        # that could reach production silently. This one cannot: a coverage
        # gap fails the build, so the PR is already red and unmergeable. A
        # Telegram on top adds an alarm for something that is impossible to
        # miss — and this repo treats a desensitised alarm as itself a P1.
    },
    {
        "name": "new-table-wiring-guard",
        "when": {"globs": ["**/*.py", "**/*.sql"]},
        "steps": [["python3", "scripts/check_new_table_wiring.py", "{pr_diff}"]],
        "notify": True,
    },
    {
        "name": "pairs-sizing-basis-guard",
        "when": {
            "globs": [
                "config/pairs.yaml",
                "src/units/strategies/pairs_executor.py",
                "scripts/ci/check_pairs_sizing_basis.py",
            ]
        },
        "steps": [
            ["python3", "scripts/ci/check_pairs_sizing_basis.py", "--self-test"],
            ["python3", "scripts/ci/check_pairs_sizing_basis.py"],
        ],
    },
    {
        "name": "prop-identity-guard",
        "when": {"globs": ["src/**/*.py", "scripts/ci/check_prop_identity_single_home.py"]},
        "steps": [["python3", "scripts/ci/check_prop_identity_single_home.py"]],
    },
    {
        "name": "artifact-caveat-guard",
        # Registered 2026-08-27 on operator decision. Fires on the matrix, on
        # every tool that PRODUCES it, and on the three backlogs — the backlogs
        # deliberately, because a NEW row filed against a producer is exactly
        # the event that must reach the artifact, and that is the direction the
        # drift actually travels (the same reasoning risk-basis-agreement uses
        # for putting config/accounts.yaml in its own trigger set).
        "when": {"globs": [
            "docs/research/exit-refinement-coverage.json",
            "docs/claude/*-backlog.json",
            "scripts/backtest_system.py", "scripts/capital_efficiency.py",
            "scripts/research/m20_*.py", "scripts/research/e35_*.py",
            "src/research/risk_basis.py",
            "scripts/ci/check_artifact_caveats.py",
            "scripts/research/m20_coverage_base_counts.py",
        ]},
        # Self-test FIRST — a guard whose planted controls no longer fire must
        # not report a clean scan.
        "steps": [["python3", "scripts/ci/check_artifact_caveats.py", "--self-test"],
                  ["python3", "scripts/ci/check_artifact_caveats.py"],
                  # The denominator half: a base count stated in prose must also
                  # be a FIELD, or the extraction silently rots back to prose.
                  ["python3", "scripts/research/m20_coverage_base_counts.py", "--check"]],
    },
    {
        "name": "rows-landed-guard",
        # Registered 2026-08-27. This guard runs the SELF-TEST only — the live
        # assertion belongs INSIDE the producing workflow (e35-bracket-sweep's
        # corpus job), because the question "did THIS run's rows arrive" can only
        # be asked by the run that produced them. What CI protects here is the
        # instrument: a landing check whose planted controls stopped firing would
        # report every run clean, which is worse than not having it.
        #
        # Fires on the tool and on the workflows that call it, so removing the
        # call site or breaking the tool both re-run the controls.
        "when": {"globs": [
            "scripts/ci/assert_rows_landed.py",
            ".github/workflows/e35-bracket-sweep.yml",
            ".github/workflows/m20-exit-lever-sweep.yml",
            # R3's offload publish job asserts its drop landed, so it is a call
            # site and belongs here by this guard's own rule.
            # ⚠️ THIS LIST IS HAND-MAINTAINED AND SILENTLY DRIFTS. The R3 PR
            # added a call site and this guard went on SKIPping until the list
            # was updated by hand — i.e. the omission is invisible, which is the
            # failure mode the guard itself exists to prevent one level down.
            # BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING
            # will add ~18 more call sites; that lane should replace this list
            # with a scan for the tool's name across .github/workflows/ rather
            # than extend it eighteen times.
            ".github/workflows/trainer-offload-train.yml",
        ]},
        "steps": [["python3", "scripts/ci/assert_rows_landed.py", "--self-test"]],
    },
    {
        "name": "risk-basis-agreement",
        # Fires on the harness fleet, the live risk config, and itself. The
        # SOURCE of truth (config/accounts.yaml) is in the trigger set
        # deliberately: a live risk_pct change must re-grade every harness
        # default, which is the direction the drift actually travelled.
        "when": {"globs": [
            "scripts/backtest_*.py", "scripts/walkforward_*.py",
            "scripts/research/*.py", "scripts/ml/*.py", "scripts/prop/*.py",
            "src/backtest/*.py", "src/research/risk_basis.py",
            "config/accounts.yaml",
            "scripts/ci/check_risk_basis_agreement.py",
        ]},
        # Self-test FIRST — a guard whose planted controls no longer fire must
        # not report a clean scan (the collapsed-state-guard lesson below).
        "steps": [["python3", "scripts/ci/check_risk_basis_agreement.py", "--self-test"],
                  ["python3", "scripts/ci/check_risk_basis_agreement.py", "--all"]],
    },
    {
        "name": "cost-model-single-owner",
        # Fires on the harness fleet + the owner itself. The OWNER is in the
        # trigger set deliberately, mirroring risk-basis-agreement: changing
        # DEFAULT_FEE_BPS_ROUNDTRIP must re-grade every registered duplicate,
        # which is the direction the drift actually travels.
        "when": {"globs": [
            "scripts/backtest_*.py", "scripts/research/*.py", "scripts/ml/*.py",
            "src/backtest/*.py", "src/runtime/execution_costs.py",
            "src/runtime/allocator_ev.py", "src/runtime/trade_costs.py",
            "scripts/ci/check_cost_model_single_owner.py",
        ]},
        # Self-test FIRST — a guard whose planted controls no longer fire must
        # not report a clean scan.
        "steps": [["python3", "scripts/ci/check_cost_model_single_owner.py",
                   "--self-test"],
                  ["python3", "scripts/ci/check_cost_model_single_owner.py"]],
    },
    {
        "name": "tp-venue-cap-single-owner",
        "when": {"globs": [
            "src/runtime/tp_venue_cap.py", "src/units/strategies/*.py",
            "src/runtime/position_telemetry.py", "src/runtime/target_expectation.py",
            "scripts/research/*.py", "scripts/ops/*.py",
            "scripts/ci/check_tp_venue_cap_single_owner.py",
        ]},
        # Self-test FIRST, same reasoning as the cost-model sibling above: a guard
        # whose planted controls no longer fire must not report a clean scan. Its
        # controls earned that placement -- they caught a regex that could never
        # match the owner's own constant name.
        "steps": [["python3", "scripts/ci/check_tp_venue_cap_single_owner.py",
                   "--self-test"],
                  ["python3", "scripts/ci/check_tp_venue_cap_single_owner.py"]],
    },
    {
        "name": "automerge-trigger-guard",
        # UNGATED: `when: None`, so it runs on every PR regardless of the diff.
        # The registry's convention for "always" is an EXPLICIT `None`, never an
        # absent key — every one of the other 73 entries carries the key, and
        # `run_guards` itself dereferences `g["when"]` directly in three places
        # (the `--list` render, the diff-scoped selection, and the dirty-worktree
        # warning). This entry shipped without it and broke all three.
        #
        # ⚠️ THAT IS DELIBERATE AND IS THE POINT OF THE GUARD. A diff-scoped
        # version would only fire when someone edits the relay — and nobody was
        # editing the relay on 2026-09-02 when it un-drafted and armed three PRs
        # that had asked for nothing. The regression vector is a path landing on
        # `main`, not an edit to the workflow, so a guard that waits to be
        # triggered by an edit is a guard that would have stayed silent through
        # the whole incident. Same reasoning as `diagnostic-provenance-guard`'s
        # ungated `--all` step.
        #
        # It is cheap (two file reads, no network) and the tree passes today, so
        # an ungated step is survivable — which is exactly the precondition that
        # made the diagnostic-provenance one survivable too.
        #
        # Self-test FIRST: the guard reports a CLEAN tree, so without an
        # exercised failure path a green here is indistinguishable from a guard
        # that stopped matching. Declared in `guard_selftests.py`'s
        # COVERED_BY_CHECKER, which `check_selftest_wiring.py` VERIFIES rather
        # than takes on trust.
        "when": None,
        "steps": [["python3", "scripts/ci/check_automerge_trigger.py", "--self-test"],
                  ["python3", "scripts/ci/check_automerge_trigger.py"]],
    },
    {
        "name": "pr-landing-guard",
        # Every PR declares its TIER and how it means to LAND, and the
        # declaration is checked against the diff rather than taken on trust.
        #
        # WHY IT IS UNGATED. The condition is a property of the PR — did this
        # branch declare, and does the declaration match what it changed — not
        # of any particular file it touched. A diff-scoped version would fire
        # only when someone edits the landing machinery, which is exactly the
        # PR least in need of it and never the ordinary PR that quietly asks to
        # merge without declaring. Same reasoning as `automerge-trigger-guard`
        # directly above.
        #
        # WHY IT IS SURVIVABLE ON DAY ONE. Requiring a declaration on every PR
        # would otherwise red every branch already open when this merges — 6 of
        # them (population: every open PR from `list_pull_requests` state=open,
        # 2026-09-03), whose authoring sessions are mostly dead and cannot add
        # the file. Failing them is how a guard gets disabled instead of fixed
        # (`check_pr_queue_watch.py` records that reasoning). So the checker
        # asks whether ITSELF existed at the branch's merge-base: a branch cut
        # before the rule passes `undeclared_predates_guard`, loudly and
        # counted. It arms itself as those branches drain and there is no flag
        # to unset. ⚠️ The DANGEROUS direction is NOT grandfathered — arming
        # auto-merge with no valid declaration fails at any age.
        #
        # THE TEETH ARE THE MERGE GATE, NOT AN ALARM. Auto-merge merges only on
        # green and this is a required check, so a branch that arms while
        # under-declaring its tier holds itself out of `main` by failing its
        # own guard.
        #
        # Self-test FIRST, and it carries POSITIVE controls as well as plants:
        # the failure paths here refuse work, so a guard that started refusing
        # correct PRs would be worse than the problem it fixes.
        #
        # The real check is `pr_only` — a push/`--all` run has no branch to
        # grade, and the checker reports `not_a_pr` rather than a pass.
        # Costs ~2s: a few `git` plumbing calls plus one small JSON read.
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_pr_landing.py", "--self-test"],
            {
                "argv": ["python3", "scripts/ci/check_pr_landing.py",
                         "--base", "origin/{base_ref}"],
                "pr_only": True,
            },
        ],
    },
    {
        # THE MANAGER SESSION ONLY MANAGES — as a check, not a paragraph.
        # `CLAUDE.md` has carried the operator's rule verbatim since
        # 2026-09-01; the 2026-09-03 day manager read it at session start and
        # was caught doing items the same morning. Adding emphasis to a rule
        # that was read and disobeyed is the non-fix this repo has paid for
        # three times (MI-15 twice, and
        # BL-20260903-MANAGER-CHECKLIST-GOES-STALE-SILENTLY-AND-STATUS-REPORTS-IT-AS-CURRENT).
        # So: a manager COMMIT touching a worker path fails, named.
        #
        # WHO IS THE MANAGER IS DERIVED, NOT DECLARED — from the git history of
        # MANAGER-LEASE.json (3 sessions across 59 revisions) joined to each
        # commit's `Claude-Session:` trailer. Branch name was measured and
        # REJECTED: `claude/risk-manager-backstop` is a worker branch and
        # `claude/openprs-prune-merged-rows` is a manager one.
        #
        # ⚠️ PER-COMMIT, NOT PER-BRANCH-DIFF, and that is the whole point. The
        # accused acts — resolving conflicts on OTHER sessions' PRs — never
        # appear in the manager's own PR. A branch-diff check would be blind to
        # exactly the failure it exists for.
        #
        # NOT A WALL — measured before wiring. Replayed over commits on
        # origin/main since 2026-09-01: the 2026-09-02 manager grades 75 clean
        # / 31 failing; the night manager 2 / 0; the 2026-09-03 manager
        # (the one the directive is about) 5 / 0. It bites where the building
        # actually happened.
        #
        # `when: None` for check_pr_landing's reason: what trips this is a
        # COMMIT, which may touch no file the predicate would match. Costs a few
        # `git` plumbing calls over the branch's own commits only.
        "name": "manager-scope-guard",
        "when": None,
        "steps": [
            # Self-test FIRST, with plants AND controls: this guard REFUSES
            # work, so one that started failing correct PRs would be worse than
            # the problem it fixes.
            ["python3", "scripts/ci/check_manager_scope.py", "--self-test"],
            {
                "argv": ["python3", "scripts/ci/check_manager_scope.py",
                         "--base", "origin/{base_ref}"],
                "pr_only": True,
            },
        ],
    },
    {
        "name": "collapsed-state-guard",
        "when": {"regex": r"\.py$"},
        # Self-test FIRST, so a guard that silently stopped matching cannot read
        # as a clean pass. This step was MISSING until 2026-08-17:
        # `selftest_collapsed_state` had been registered in
        # `guard_selftests.py::SELFTESTS` and nothing ever invoked it — the
        # dispatcher takes a name (there is an `--all`, but no caller anywhere
        # passes it), and `check_collapsed_states.py` has no `--self-test` of its
        # own, so unlike `matrix-corpus-agreement` there was no second path
        # covering it. A registered-and-never-executed control is exactly the
        # written-and-never-read shape this guard family exists to catch, one
        # level up: the guard was real, its self-test was real, and the wiring
        # between them was the gap. It passes and its failure path verifies when
        # run by hand, so this is a wiring fix, not a behaviour change.
        "steps": [["python3", "scripts/ci/guard_selftests.py", "collapsed-state"],
                  ["python3", "scripts/ci/check_collapsed_states.py", "--verbose"]],
    },
    {
        "name": "manifest-scope-constants",
        # The ML manifest<->dataset contract, at COMMIT time. It was previously
        # validated ONLY at train time, on the trainer, inside a cycle that
        # returns rc=0 — so a manifest merged clean and then silently never
        # trained. `mes-regime-1d-lgbm-v2` declared `hour_of_day` on a DAILY bar
        # and sat 34.0 days untrained against a 7.0-day threshold while the
        # cycle reported green
        # (MB-20260829-MES-1D-DECLARES-A-FEATURE-THAT-CANNOT-VARY-AT-ITS-OWN-TIMEFRAME).
        #
        # The trainer files are in the trigger set because C3 mirrors an
        # invariant that lives in `lightgbm_multiclass.py` (a categorical not in
        # feature_columns RAISES); if that raise moves, this guard must re-grade.
        "when": {"globs": [
            "ml/configs/*.yaml",
            "ml/datasets/**",
            "ml/trainers/**",
            "scripts/ci/check_manifest_scope_constants.py",
        ]},
        # Self-test FIRST, same posture as collapsed-state-guard: the guard
        # currently reports a CLEAN fleet, so without an exercised failure path a
        # green run here is indistinguishable from a guard that stopped matching.
        "steps": [["python3", "scripts/ci/guard_selftests.py",
                   "manifest-scope-constants"],
                  ["python3", "scripts/ci/check_manifest_scope_constants.py"]],
    },
    {
        # The GENERALISATION of the fix recorded immediately above: that wiring
        # gap was found by hand, and nothing would have found the next one.
        # This resolves every registered self-test to a covering path —
        # invoked-by-name here, or the checker's own `--self-test` declared in
        # `guard_selftests.py::COVERED_BY_CHECKER` — and VERIFIES that path
        # (the declared script must really be run with the flag, and must
        # really declare it), so a mapping cannot be satisfied by naming
        # something that cannot self-test.
        "name": "selftest-wiring-guard",
        # `when: None` — always. A guard that proves other guards' failure
        # paths execute must not itself be diff-scoped: the whole defect class
        # is a control that is present but never runs.
        "when": None,
        "steps": [["python3", "scripts/ci/check_selftest_wiring.py", "--self-test"],
                  ["python3", "scripts/ci/check_selftest_wiring.py"]],
    },
    {
        "name": "exit-mechanism-coverage-guard",
        # Catches the ORPHANED DECLARE: a leg declares an exit lever its own
        # unit module never reads. Silently inert, and INVISIBLE to
        # lever-reachability-guard below, which only compares arm_r to cap_R
        # and so cannot see a lever that is not implemented at all.
        #
        # It needs a guard rather than a hand-run script because the backtest
        # harness implements some of these levers IN THE ENGINE
        # (scripts/backtest_trend.py applies stale_exit_bars directly, not via
        # the leg's monitor()). So a sweep can return a clean PASS for a lever
        # the live module cannot run, and the resulting declare would ship
        # inert wearing that PASS — the arm-above-cap shape, one level up.
        #
        # The self-test runs on EVERY invocation, same reasoning as
        # lever-reachability-guard: a coverage probe that cannot find a known
        # positive proves nothing, and "no orphans" is exactly the answer a
        # reader acts on by not looking further.
        "when": {"globs": ["config/strategies.yaml",
                           "src/units/strategies/*.py",
                           "src/runtime/strategy_signal_builders.py",
                           "scripts/ops/exit_mechanism_coverage.py"]},
        "steps": [
            ["python3", "scripts/ops/exit_mechanism_coverage.py", "--self-test"],
            ["python3", "scripts/ops/exit_mechanism_coverage.py", "--orphans-only"],
        ],
    },
    {
        "name": "lever-reachability-guard",
        # The self-test runs on EVERY invocation, same reasoning as
        # trainer-heavy-lock-guard: this guard's whole design point is that
        # editing the registry to match a changed arm_r must NOT be free, and
        # that is only demonstrable by running the negatives.
        "when": {"globs": ["config/strategies.yaml",
                           "config/lever_reachability.json",
                           "scripts/ci/check_lever_reachability.py",
                           "scripts/ops/lever_reachability_audit.py"]},
        "steps": [
            ["python3", "scripts/ci/check_lever_reachability.py", "--self-test"],
            ["python3", "scripts/ci/check_lever_reachability.py"],
        ],
    },
    {
        "name": "provenance-consumer-guard",
        "when": {"regex": r"\.py$"},
        "steps": [["python3", "scripts/check_provenance_consumers.py", "--verbose"]],
    },
    {
        "name": "trainer-heavy-lock-guard",
        # The self-test runs on EVERY invocation, same reasoning as
        # api-tier-policy-guard: a guard whose failure path is never exercised
        # is indistinguishable from one that always passes — and this guard's
        # whole design point is that a MENTION of the helper must not satisfy
        # it, which is only demonstrable by running the negative.
        "when": {"regex": r"^scripts/(ml|research)/.*\.py$"},
        "steps": [
            ["python3", "scripts/ci/check_trainer_heavy_lock.py", "--self-test"],
            ["python3", "scripts/ci/check_trainer_heavy_lock.py", "--list"],
        ],
    },
    {
        "name": "qty-legalization-guard",
        "when": {"globs": ["**/*.py"]},
        "steps": [["python3", "scripts/check_qty_legalization_guard.py"]],
    },
    {
        "name": "ruff-lint",
        "when": {"regex": r"\.py$|ruff\.toml$|requirements-dev\.txt$"},
        "steps": [["ruff", "check", "."]],
    },
    {
        "name": "secret-scan",
        "when": None,
        "steps": [["python3", "scripts/secret_scan.py"]],
    },
    {
        "name": "silent-empty-guard",
        "when": {"regex": r"\.py$|\.ya?ml$|\.sh$"},
        "steps": [["python3", "scripts/check_silent_empty_in_diff.py", "{pr_diff}"]],
        "notify": True,
    },
    {
        "name": "soak-doctrine-guard",
        "when": None,
        "steps": [["python3", "scripts/check_soak_doctrine.py"]],
    },
    {
        "name": "strategy-coverage-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/check_strategy_coverage.py", "--check"],
            {
                "argv": ["python3", "scripts/check_strategy_coverage.py", "--matrix"],
                "git_clean": "docs/strategy-coverage-matrix.md",
                "hint": "run `python3 scripts/check_strategy_coverage.py --matrix` and commit the result",
            },
        ],
    },
    {
        "name": "strategy-risk-guard",
        "when": {"globs": ["**/*.py", "config/strategies.yaml"]},
        "steps": [["python3", "scripts/check_strategy_risk_field_in_diff.py", "{pr_diff}"]],
        "notify": True,
    },
    {
        "name": "timestamp-comparison-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/guard_selftests.py", "timestamp-comparison"],
            ["python3", "scripts/check_timestamp_comparisons.py", "--all"],
        ],
    },
    {
        "name": "training-population-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/check_training_population.py", "--check"],
            {
                "argv": ["python3", "scripts/check_training_population.py", "--matrix"],
                "git_clean": "docs/training-population-matrix.md",
                "hint": "run `python3 scripts/check_training_population.py --matrix` and commit the result",
            },
        ],
    },
    {
        # BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-TEST-COUPLING.
        # `when: None` (always) on purpose: a second engine can be re-introduced
        # by ADDING a file, and a globs filter scoped to the paths we know about
        # would not fire on a copy planted somewhere new — the same
        # population-you-cannot-see blind spot that let the fork survive a
        # consumer sweep in the first place.
        "name": "trend-engine-convergence-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/research/trend_harness_divergence.py", "--self-test"],
            ["python3", "scripts/research/trend_harness_divergence.py"],
        ],
    },
    {
        # M31 P4. THE SELF-TEST IS THE GUARD — deliberately NOT the parity run.
        # Running the real check in CI would green on `harness_absent` /
        # `live_no_final_rows` (the live table is ~1 day old and holds no
        # closed rows yet), i.e. a pass that checked nothing — the exact
        # anti-pattern `docs/CLAUDE-RULES-CANONICAL.md` § "Green is not
        # evidence" names. What CI can honestly protect is the INSTRUMENT: the
        # 10 cases assert the probe still flags a ceiling breach, still refuses
        # an uncapped harness, and still abstains rather than passing when the
        # lifecycle is unknown. The abstention states protect the conclusion;
        # this guard protects their ability to fire.
        "name": "mfe-parity-instrument-guard",
        # BOTH halves of Check B's instrumentation. The aggregator that WRITES
        # the committed harness distribution is registered here beside the
        # checker that reads it, and its own glob is listed, so editing either
        # file runs both self-tests. A control that is written and never
        # invoked is the defect this repo hit twice on 2026-08-17
        # (BL-20260817-COLLAPSED-STATE-SELFTEST-REGISTERED-BUT-NEVER-INVOKED);
        # the aggregator's refusals are exactly the kind of control that would
        # rot silently, because nothing else fails when they stop firing.
        "when": {"globs": ["scripts/research/m31_mfe_parity.py",
                           "scripts/research/m31_harness_mfe_dist.py"]},
        "steps": [["python3", "scripts/research/m31_mfe_parity.py", "--self-test"],
                  ["python3", "scripts/research/m31_harness_mfe_dist.py",
                   "--self-test"]],
    },
    {
        "name": "writer-conformance-guard",
        "when": {"globs": ["**/*.py", "**/*.sql"]},
        "steps": [["python3", "scripts/check_writer_conformance.py", "{pr_diff}"]],
        "notify": True,
    },
]


# ---------------------------------------------------------------------------
# the registry's own shape, asserted at import
# ---------------------------------------------------------------------------
#
# WHY THIS IS HERE AND NOT IN A TEST. `automerge-trigger-guard` was added on
# 2026-09-02 with no `when` key at all, intending "ungated" — for which this
# registry's convention is an explicit `None`. Nothing said so at the point of
# writing, and the omission surfaced as a bare `KeyError: 'when'` raised seven
# tests deep inside `tests/test_guards_uncommitted_work.py`, a file whose
# subject is uncommitted work and not registry shape. The message named neither
# the guard nor the key.
#
# ⚠️ AND THE `guards` CI JOB WAS GREEN THROUGHOUT. It invokes the driver in a
# mode that short-circuits every `g["when"]` read, so the guard runner was
# broken on `--list` and on the ordinary diff-scoped (local / pre-commit) path
# while its own job reported success — green over a thing it did not check.
#
# This is not a second definition of the registry's shape. It asserts exactly
# the three keys THIS MODULE dereferences with `[]` — `name` (the `--only`
# filter, the skipped-set, the failure summary), `when` (the `--list` render at
# the scope column, the relevance filter, the dirty-worktree warning) and
# `steps` (the executor). A key the module indexes and does not require is the
# defect; adding a field here without adding a dereference does not make it
# required. Per `docs/CLAUDE-RULES-CANONICAL.md` § RULE ONE, the assertion goes
# inside the transform — that mechanism caught 3 of the 10 verification
# failures in the ledger there, where prose caught 0.

_REQUIRED_GUARD_KEYS = ("name", "when", "steps")


def _validate_registry(guards: List[Dict[str, Any]]) -> None:
    """Refuse a malformed registry at import, naming the entry and the key.

    Raises rather than warns: every consumer of `GUARDS` reads these keys, so a
    registry that is missing one has no correct behaviour left to degrade to.
    """
    problems: List[str] = []
    for i, g in enumerate(guards):
        missing = [k for k in _REQUIRED_GUARD_KEYS if k not in g]
        if missing:
            who = g.get("name") or f"<entry {i} has no name>"
            problems.append(
                f"  {who} (index {i}): missing {', '.join(repr(k) for k in missing)}"
            )
    if problems:
        raise ValueError(
            "run_guards.GUARDS is malformed — every entry must declare "
            + ", ".join(repr(k) for k in _REQUIRED_GUARD_KEYS)
            + ".\n"
            + "\n".join(problems)
            + "\n\nFor a guard that should always run, the value is an EXPLICIT "
              "`\"when\": None`. An absent key is not the same thing: the driver "
              "indexes `g[\"when\"]` directly and raises."
        )


_validate_registry(GUARDS)


# ---------------------------------------------------------------------------
# relevance
# ---------------------------------------------------------------------------


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a GitHub `paths:` glob into a regex.

    Mirrors the subset of GitHub's filter syntax the retired workflows actually
    used: ``**`` (any depth, including none), ``*`` (within one segment) and
    ``?``. Anything else is matched literally.
    """
    out: List[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def is_relevant(when: Optional[Dict[str, Any]], changed: Sequence[str]) -> bool:
    if when is None:
        return True
    globs = when.get("globs")
    if globs:
        pats = [glob_to_regex(g) for g in globs]
        if any(p.match(f) for f in changed for p in pats):
            return True
    rx = when.get("regex")
    if rx:
        crx = re.compile(rx)
        if any(crx.search(f) for f in changed):
            return True
    return False


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def _subst(argv: Sequence[str], ctx: Dict[str, str]) -> List[str]:
    return [a.format(**ctx) for a in argv]


def _run(argv: Sequence[str]) -> int:
    print(f"    $ {' '.join(argv)}", flush=True)
    try:
        proc = subprocess.run(argv, cwd=REPO)
    except FileNotFoundError:
        print(f"    ::error::command not found: {argv[0]}", flush=True)
        return 127
    return proc.returncode


def _git_is_clean(path: str) -> bool:
    rc = subprocess.run(["git", "diff", "--quiet", "--", path], cwd=REPO).returncode
    return rc == 0


def run_guard(
    guard: Dict[str, Any],
    ctx: Dict[str, str],
    changed: Sequence[str],
    no_diff_scope: bool = False,
    unscoped: Optional[List[str]] = None,
) -> Optional[str]:
    """Run one guard. Returns None on pass, or a failure reason string.

    ``no_diff_scope`` says there is no PR diff to scope relevance by (push /
    workflow_dispatch / --all). ``unscoped`` collects the steps skipped for
    that reason so the summary can report them instead of burying them.
    """
    if unscoped is None:
        unscoped = []
    for step in guard["steps"]:
        if isinstance(step, list):
            step = {"argv": step}
        if step.get("pr_only") and ctx.get("event_name") != "pull_request":
            print(f"    (skipped: pull_request-only step) {' '.join(step['argv'])}", flush=True)
            continue
        # A per-STEP relevance clause: used where a guard must always exercise
        # its self-test but only scan on a relevant diff.
        #
        # BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH. `changed` is EMPTY under
        # --all (push / workflow_dispatch), so a globs/regex `when` can never
        # match there and the step is always skipped. That surprised a reader
        # of guards.yml, which claims push runs everything "never weaker".
        #
        # MEASURED before changing anything, and the measurement killed the
        # obvious fix. Both steps carrying a `when` today consume `{pr_diff}`,
        # and on push that file is EMPTY: forcing them to run makes
        # `check_diagnostic_provenance.py` print "OK — every scanned diagnostic
        # states what it computed" and exit 0 having scanned nothing. Making
        # the comment true would have made the CHECK false — a green that
        # checked nothing, which is the one outcome this repo treats as worse
        # than a red. Substituting the whole-tree `--all` equivalent is no
        # better: it exits 1 on 52 pre-existing grandfathered sites, so it
        # would redden `main` on every push.
        #
        # So the SKIP IS CORRECT and stays. What was wrong is that it was
        # indistinguishable from an ordinary not-relevant skip. It now names
        # the real reason, and the run summary counts these separately, so
        # nobody has to re-derive this. A guard that wants genuine push-time
        # coverage carries an UNGATED whole-tree step — see
        # `api-tier-policy-guard`, whose `--all` step is deliberately unguarded.
        if "when" in step and not is_relevant(step["when"], changed):
            if no_diff_scope:
                reason = ("skipped: no PR diff to scope by on this event — this "
                          "step consumes {pr_diff}, which is EMPTY here, so "
                          "running it would report a green that scanned nothing "
                          "(BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH)")
                unscoped.append(f"{guard['name']}: {' '.join(step['argv'])}")
            else:
                reason = "skipped: step not relevant to this diff"
            print(f"    ({reason}) {' '.join(step['argv'])}", flush=True)
            continue
        argv = _subst(step["argv"], ctx)
        rc = _run(argv)
        if rc != 0:
            if step.get("allow_fail"):
                print(f"    ::notice::advisory step returned {rc} — not gating"
                      f"{' (' + step['hint'] + ')' if step.get('hint') else ''}", flush=True)
                continue
            hint = f" — {step['hint']}" if step.get("hint") else ""
            return f"`{' '.join(argv)}` exited {rc}{hint}"
        gc = step.get("git_clean")
        if gc and not _git_is_clean(gc):
            subprocess.run(["git", "--no-pager", "diff", "--", gc], cwd=REPO)
            return f"{gc} is stale — {step.get('hint', 'regenerate and commit it')}"
    return None


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def changed_files(base_ref: str, event_name: str) -> List[str]:
    """The PR's changed files, or [] when there is no base to diff against."""
    if event_name not in ("pull_request", "merge_group"):
        return []
    rng = f"origin/{base_ref}...HEAD"
    proc = subprocess.run(
        ["git", "diff", "--name-only", rng], cwd=REPO, capture_output=True, text=True
    )
    if proc.returncode != 0:
        # Never silently degrade to "nothing changed" — that would skip every
        # relevance-gated guard and report a green that checked nothing.
        raise SystemExit(
            f"::error::could not compute changed files for {rng}: {proc.stderr.strip()}"
        )
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def worktree_files() -> List[str]:
    """Paths dirty in the WORKING TREE — staged, unstaged, or untracked.

    WHY THIS EXISTS. `changed_files` diffs a COMMIT RANGE, so uncommitted work
    is invisible to it and every guard gated on those paths is skipped — while
    the run still prints "All relevant guards passed." That is the same
    green-that-checked-nothing the `changed_files` error branch already refuses,
    reached by a different route: there the diff FAILS, here it SUCCEEDS and is
    simply answering a question about commits when the developer asked about
    their tree.

    Measured 2026-08-13: five status flips staged in
    `docs/research/exit-refinement-coverage.json`, `exit-coverage-matrix-guard`
    SKIPPED, summary green. The guard only ran once the work was committed.

    Two plumbing commands rather than `--porcelain`, deliberately: they emit one
    clean path per line, so there is no status-prefix or rename-arrow parsing to
    get wrong.

    ⚠️ A PR THAT EDITS THIS FILE DOES NOT EXERCISE THIS FUNCTION. `run_guards.py`
    is in `HARNESS_PATHS`, so touching it sets `harness_touched -> force_all`,
    and `dirty` is computed as `[] if force_all else ...`. That is correct — a
    harness change should run every guard — but it means the `guards` job going
    green on such a PR is NOT evidence about this code path. The coverage lives
    in `tests/test_guards_uncommitted_work.py`, which calls this directly and
    drives the script end-to-end on a non-`force_all` event. Read `pytest-run`,
    not `guards`, when changing this.
    """
    out: List[str] = []
    for cmd in (["git", "diff", "--name-only", "HEAD"],
                ["git", "ls-files", "--others", "--exclude-standard"]):
        # BOUNDED. This wraps CI; an unbounded subprocess here is the shape
        # that turns a slow git into a hung job with no diagnosis. Measured at
        # 88ms locally, so 60s is ~700x headroom and only a genuine wedge
        # trips it.
        try:
            proc = subprocess.run(cmd, cwd=REPO, capture_output=True,
                                  text=True, timeout=60)
        except subprocess.TimeoutExpired:
            # Raise rather than degrade: "we could not look" must never be
            # reported as "nothing is dirty", which is the false green this
            # whole function exists to prevent.
            raise SystemExit(
                f"::error::timed out reading the working tree "
                f"({' '.join(cmd)}) — cannot confirm what is uncommitted"
            )
        if proc.returncode != 0:
            # Never silently degrade to "nothing is dirty" — that restores the
            # exact false green this function exists to prevent.
            raise SystemExit(
                f"::error::could not read the working tree "
                f"({' '.join(cmd)}): {proc.stderr.strip()}"
            )
        out.extend(ln.strip() for ln in proc.stdout.splitlines() if ln.strip())
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-ref", default=os.environ.get("GUARDS_BASE_REF", "main"))
    ap.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", "pull_request"))
    ap.add_argument("--pr-diff", default=os.environ.get("GUARDS_PR_DIFF", "/tmp/pr.diff"))
    ap.add_argument("--only", action="append", default=None, help="run only these guards")
    ap.add_argument("--all", action="store_true", help="ignore relevance; run every guard")
    ap.add_argument("--list", action="store_true", help="print the registry and exit")
    ap.add_argument("--notify-file", default=os.environ.get("GUARDS_NOTIFY_FILE"))
    args = ap.parse_args(argv)

    if args.list:
        for g in GUARDS:
            scope = "always" if g["when"] is None else json.dumps(g["when"])
            print(f"{g['name']:34s} {scope}")
        print(f"\n{len(GUARDS)} guards")
        return 0

    # GENERATE THE DIFF WE CONSUME, unless a caller supplied one.
    #
    # Eight guards take `{pr_diff}` and scan ONLY that file. CI writes it in a
    # separate workflow step (`guards.yml`: `git diff origin/<base>...HEAD >
    # /tmp/pr.diff`) and passes GUARDS_PR_DIFF; nothing wrote it locally, and
    # the default path is a fixed `/tmp/pr.diff`. So a local run silently
    # rescanned whatever STALE diff a previous run had left there and printed
    # "All relevant guards passed" over content that had nothing to do with the
    # current branch — a file absent from that stale diff is never scanned at
    # all.
    #
    # Measured 2026-08-14: three consecutive local runs reported
    # diagnostic-provenance-guard PASS on a commit where CI failed it, on the
    # same command and the same path, because /tmp/pr.diff was stale. This is
    # the mechanism behind a failure this session had already logged as "guards
    # were run on uncommitted work" — that diagnosis was incomplete, and a
    # stale diff is strictly worse than a missing one because an absent file
    # errors while a stale file passes.
    #
    # A guard that cannot see the change it is scoped to is not a guard, so a
    # failure to produce the diff is a hard error, never a quiet continue.
    argv_seq = list(sys.argv[1:] if argv is None else argv)
    explicit_diff = bool(os.environ.get("GUARDS_PR_DIFF")) or any(
        a == "--pr-diff" or a.startswith("--pr-diff=") for a in argv_seq
    )
    if not explicit_diff and args.event_name != "push":
        rng = f"origin/{args.base_ref}...HEAD"
        proc = subprocess.run(["git", "diff", rng], cwd=REPO,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"::error::could not generate the PR diff for {rng}: "
                  f"{proc.stderr.strip()} — refusing to scan a stale or absent "
                  f"{args.pr_diff}, which would report a green having checked "
                  f"nothing.")
            return 2
        Path(args.pr_diff).write_text(proc.stdout)
        print(f"generated {args.pr_diff} from {rng} "
              f"({len(proc.stdout.splitlines())} lines)")

    changed = [] if args.all else changed_files(args.base_ref, args.event_name)
    harness_touched = any(f in HARNESS_PATHS for f in changed)
    force_all = args.all or harness_touched
    # Captured BEFORE any guard runs: guards WRITE files (training-population
    # -guard rewrites docs/training-population-matrix.md), so reading the tree
    # afterwards would report the harness's own output as the developer's
    # uncommitted work. Skipped under force_all, where nothing is relevance-
    # gated and coverage is already complete.
    dirty = [] if force_all else sorted(set(worktree_files()) - set(changed))
    # Same capture, WITHOUT subtracting `changed`, for the end-of-run caveat.
    # Two differences from `dirty`, both deliberate:
    #   * no subtraction — a file both COMMITTED-changed and dirty is the case
    #     that bites (it reads as covered while the edits on top of the commit
    #     went unscanned), and subtracting it is exactly what hid it;
    #   * not gated on force_all — `--all` disables RELEVANCE, not the commit
    #     range, so diff-scoped guards are equally blind under it.
    # Captured HERE for the reason the comment above gives: guards WRITE files
    # (two `--matrix` steps rewrite docs/*-matrix.md), so sampling the tree
    # after they run would report the harness's own output as the developer's
    # uncommitted work. My first version of this caveat did sample afterwards
    # and only escaped a false positive because those writes happened to be
    # byte-identical that run.
    tree_dirty_at_start = sorted(worktree_files())

    print("=" * 72)
    print(f"guards — {len(GUARDS)} registered · event={args.event_name} · base={args.base_ref}")
    if force_all:
        why = "--all" if args.all else "the guard harness itself changed"
        print(f"relevance DISABLED ({why}) — running every guard")
    else:
        print(f"{len(changed)} changed file(s) drive relevance")
    print("=" * 72, flush=True)

    ctx = {
        "base_ref": args.base_ref,
        "event_name": args.event_name,
        "pr_diff": args.pr_diff,
        "changed_files": " ".join(changed),
    }

    selected = [g for g in GUARDS if not args.only or g["name"] in args.only]
    if args.only:
        missing = set(args.only) - {g["name"] for g in GUARDS}
        if missing:
            print(f"::error::unknown guard(s): {', '.join(sorted(missing))}")
            return 2

    failures: List[tuple] = []
    skipped: List[str] = []
    passed: List[str] = []
    notify: List[str] = []
    # Steps skipped because this event carries no PR diff to scope by. Counted
    # separately from ordinary not-relevant skips so push-time coverage is
    # legible instead of assumed (BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH).
    unscoped: List[str] = []
    no_diff_scope = args.all or args.event_name not in ("pull_request", "merge_group")

    for guard in selected:
        name = guard["name"]
        if not force_all and not is_relevant(guard["when"], changed):
            skipped.append(name)
            print(f"\n--- {name}: SKIP (not relevant to this diff)", flush=True)
            continue
        print(f"\n--- {name}", flush=True)
        t0 = time.time()
        reason = run_guard(guard, ctx, changed, no_diff_scope, unscoped)
        dt = time.time() - t0
        if reason is None:
            passed.append(name)
            print(f"--- {name}: PASS ({dt:.1f}s)", flush=True)
        else:
            failures.append((name, reason))
            if guard.get("notify"):
                notify.append(name)
            print(f"--- {name}: FAIL ({dt:.1f}s) — {reason}", flush=True)

    print("\n" + "=" * 72)
    print(f"PASS {len(passed)} · FAIL {len(failures)} · SKIP {len(skipped)}")
    if skipped:
        print("skipped (not relevant): " + ", ".join(skipped))
    if unscoped:
        # Never let this read as coverage. On push the diff is empty, so these
        # steps CANNOT scan anything; running them would print a green that
        # checked nothing, and their whole-tree equivalents are not
        # drop-in. (This used to read "diagnostic-provenance --all exits 1 on
        # 52 grandfathered sites" — no longer true as of 2026-09-02: that
        # residue was drained to zero and the guard now CARRIES an ungated
        # whole-tree step, so it is no longer an example of this problem. The
        # general point stands for the guards still listed below.)
        print(f"\nNOT SCANNED on this event ({len(unscoped)}) — no PR diff to "
              f"scope by; these steps consume {{pr_diff}}, which is empty here:")
        for item in unscoped:
            print(f"  - {item}")
        print("  A guard needing real push-time coverage must carry an UNGATED "
              "whole-tree step (see api-tier-policy-guard).")
    unchecked = sorted({g["name"] for g in selected
                        if g["name"] in skipped and is_relevant(g["when"], dirty)})
    # A guard named in --only that relevance then skipped. Distinct from
    # `unchecked`: nothing is dirty and no commit is missing — the caller
    # ASKED FOR THIS GUARD BY NAME and it did not run. `PASS 0` is printed,
    # but "All relevant guards passed" + exit 0 is what a wrapper script reads.
    asked_but_skipped = sorted(set(args.only or []) & set(skipped))
    if unchecked:
        # The skip list above already named these, and that was not enough —
        # a reader scanning for the green line does not audit 23 skip names.
        # State the CAUSE next to them.
        print(f"\nNOT SELECTED because the work is UNCOMMITTED ({len(unchecked)}) — "
              f"guard relevance is computed from a COMMIT RANGE, so these did "
              f"NOT run against your working tree:")
        for name in unchecked:
            print(f"  - {name}")
        print(f"  {len(dirty)} dirty path(s) drove this; commit them (or use "
              f"--all) for real coverage.")
    if asked_but_skipped:
        print(f"\nYOU ASKED FOR THESE BY NAME AND THEY DID NOT RUN "
              f"({len(asked_but_skipped)}) — --only selects, it does not "
              f"override relevance:")
        for name in asked_but_skipped:
            print(f"  - {name}")
        print("  Add --all to run them regardless of the diff.")
    print("=" * 72)

    if args.notify_file and notify:
        Path(args.notify_file).write_text("\n".join(notify) + "\n", encoding="utf-8")

    if failures:
        print("\nFAILING GUARDS — every one is listed here, so one run is enough:")
        for name, reason in failures:
            print(f"  ::error::{name}: {reason}")
        return 1

    # The line that lied. "All relevant guards passed" is true of what RAN,
    # and the reader takes it as a statement about their change. Both routes
    # below end with a guard the reader believes ran and which did not.
    caveats = []
    # A guard that RAN is not evidence about work that is not committed. Every
    # guard here is scoped to a COMMIT RANGE — either `{pr_diff}`, generated
    # above from `origin/<base>...HEAD`, or its own `--base origin/<base>`.
    # Neither range contains the working tree, so a dirty file is invisible to
    # a guard that ran, passed, and was counted.
    #
    # `unchecked` above does NOT cover this. It reports guards RELEVANCE
    # skipped, and relevance is a union: if any COMMITTED file already made a
    # guard relevant, it runs, is counted as passed, and never appears in
    # `unchecked` — while still having scanned a range without your edits.
    # That is the hole this closes, and it is not hypothetical: on 2026-08-14
    # a local run of this script printed "All relevant guards passed" over a
    # truncated backlog id in an UNCOMMITTED comment; the same commit failed
    # `artifact-validity-guard` in CI minutes later, because CI necessarily
    # scans committed code. The sprint log already recorded "committing first
    # was necessary and never sufficient" — this is the converse half, where
    # the commit was simply skipped and the harness said green anyway.
    #
    # Computed independently of `force_all`: `--all` disables RELEVANCE, not
    # the commit range, so the diff-scoped guards are just as blind under it.
    #
    # Sampled BEFORE any guard ran (see `tree_dirty_at_start` above) — guards
    # write files, so sampling here would blame the harness's own output on the
    # developer.
    tree_dirty = tree_dirty_at_start
    if tree_dirty:
        caveats.append(f"{len(tree_dirty)} path(s) are UNCOMMITTED and every "
                       f"guard is scoped to a commit range, so nothing here "
                       f"scanned them ({', '.join(tree_dirty[:5])}"
                       f"{' …' if len(tree_dirty) > 5 else ''})")
    if unchecked:
        caveats.append(f"{len(unchecked)} guard(s) were not selected because "
                       f"your work is uncommitted")
    if asked_but_skipped:
        caveats.append(f"{len(asked_but_skipped)} guard(s) you named with "
                       f"--only were skipped as not relevant")
    if caveats:
        print("\nAll SELECTED guards passed — but " + "; and ".join(caveats)
              + ". This is NOT a clean bill of health for your change.")
    else:
        print("\nAll relevant guards passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
