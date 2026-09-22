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
import shutil
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
        # E42 — a rostered leg's symbol must be REACHABLE, and the remedy is
        # always the PULL LIST, never the leg.
        #
        # ⚠️ THIS LANDS BEFORE THE FIX IT ANTICIPATES, ON PURPOSE (operator,
        # 2026-09-22). The union that stops `accounts.yaml::symbols` being a
        # de-facto third execution gate is held in PR #12736 behind a
        # shared-resolver change that must go first. Until it merges the pull
        # list IS the gate, so the operator's standing rule is that any leg
        # promoted in that window declares its symbol in the SAME PR — and an
        # unguarded window is exactly what shipping this guard with the union
        # would have left. The guard reads `src/main.py` to decide which
        # consequence to print, so its message corrects itself on the day the
        # union lands instead of waiting for someone to remember.
        #
        # `config/instruments.yaml` is in the globs because the third axis
        # grades against it: deleting a profile can strand a rostered symbol
        # with neither config file touched. `src/main.py` is in them because
        # the union-state read is a fact about that file.
        #
        # The self-test runs on EVERY invocation, before the tree check — a
        # guard whose green has never been shown capable of turning red is not
        # evidence (`check_guard_selftest_coverage.py`).
        "name": "roster-symbol-reachability",
        "when": {"globs": [
            "config/accounts.yaml",
            "config/strategies.yaml",
            "config/instruments.yaml",
            "src/main.py",
            "scripts/ci/check_roster_symbol_reachability.py",
        ]},
        "steps": [
            ["python3", "scripts/ci/check_roster_symbol_reachability.py",
             "--self-test"],
            ["python3", "scripts/ci/check_roster_symbol_reachability.py"],
        ],
    },
    {
        # E42 — ONE definition of "which symbols does this account concern".
        # Five sites derived it privately; four were known and the fifth
        # (`account_ib_venue_session`) was found by this very self-test's
        # bypass control rather than by reading.
        #
        # The self-test is the guard: it carries the planted bypass (both
        # directions, plus a verified-not-presence-only exemption) and asserts
        # over the REAL config that DECLARED and UNION still agree — so the day
        # a roster and a pull list diverge, CI says so instead of a data sweep
        # quietly skipping a symbol that is trading.
        "name": "symbol-resolver-guard",
        "when": {"globs": [
            "src/config/symbol_sets.py",
            "scripts/ci/check_symbol_resolver.py",
            "src/main.py",
            "src/units/accounts/clients.py",
            "src/runtime/exchange_accounts.py",
            "config/accounts.yaml",
            "config/strategies.yaml",
        ]},
        # Invoked by PATH, not `-m`: `-m src.config.symbol_sets` makes the
        # MODULE the registry's "runner", and every runner needs a
        # RUNNER_REMEDY entry answering "what does the operator install?"
        # — a question repo code has no true answer to
        # (`tests/ci/test_run_guards_runner_absent.py` caught that). And the
        # CONTROLS live in this script rather than in the module, because a
        # file that claims coverage in this registry while asserting nothing
        # itself is the presence-only marker `new-table-wiring-guard` already
        # cost us (`check_guard_selftest_coverage.py` caught THAT). Both
        # corrections are written up in the script's own docstring.
        "steps": [["python3", "scripts/ci/check_symbol_resolver.py",
                   "--self-test"]],
    },
    # ─────────────────────────────────────────────────────────────────────
    # ⚠️ 2026-09-21 OPERATING RESET — 40 GOVERNANCE GUARDS REMOVED FROM HERE.
    #
    # They guarded the eight-register operating model (the work store,
    # OPEN-ITEMS.json, the four review backlogs, SESSIONS.json, the lease, the
    # merge queue, the coordination board, DUE.*, the constraint readout, the
    # probe register and the session-brief generator), which is archived under
    # `docs/archive/2026-09-21-operating-reset/` and is not coming back.
    #
    # Their entries are DELETED rather than commented out, because a guard that
    # cannot pass is a guard somebody disables. Their scripts are still on disk
    # and in git history; the removed names are listed in the PR and in
    # `docs/plans/OPERATING-PLAN-2026-09-21.md` item A5, so re-arming any one of
    # them is a lookup rather than an excavation.
    #
    # Removed: artifact-validity-guard, backlog-unresolve-guard, board-coherence, capability-pull-guard, checklist-routing-age-guard, constraint-readout-guard, daily-brief-guard, decision-answer-consumers, decision-answers-guard, demote-budget-guard, digest-liveness-guard, due-list-guard, due-list-token-guard, error-feed-digest-guard, manager-checklist-vocabulary-guard, manager-lease-guard, manager-queue-watch-guard, manager-tooling-selftests, one-live-workplan, open-items-guard, operator-owed-guard, pr-queue-watch-guard, priority-fallback-distribution, probe-guard, recurrence-ledger-guard, register-field-loss-guard, register-id-guard, register-reserialization-guard, role-pack-operating-layer, rows-landed-guard, scope-overlap-guard, session-brief-guard, session-registry-guard, soak-registered-guard, spec-carrier-guard, stale-in-flight-guard, sunset-disposition-guard, uncarried-spec-guard, wip-ceiling-guard, work-digest-source-coverage
    #
    # ⚠️ 2026-09-22 (E45): `operator-owed-guard` and `soak-registered-guard` are
    # BACK, below, re-pointed at subjects that exist post-reset; `claim-basis-guard`
    # is back with them (it had been dropped from this registry earlier and is
    # not in the list above). `backlog-unresolve-guard`, `open-items-guard`,
    # `recurrence-ledger-guard` and `register-field-loss-guard` are now RETIRED
    # for good and their scripts deleted — see
    # `docs/archive/2026-09-21-operating-reset/guards/RETIRED-GUARDS-2026-09-22.md`
    # for the reason each one carries.
    # ─────────────────────────────────────────────────────────────────────
    {
        # SALVAGED FROM THREE REMOVED GOVERNANCE ENTRIES, 2026-09-21.
        #
        # `artifact-validity-guard`, `recurrence-ledger-guard` and
        # `due-list-guard` were grab-bags: each carried its own register check
        # AND a run of unrelated research/ops self-tests that had nowhere else
        # to live. Deleting the entries wholesale would have silently retired
        # 18 working controls with nothing to do with the operating model —
        # the "a guard stops looking because the text moved" failure this
        # harness's own header warns about.
        #
        # So the REGISTER steps are gone and the rest are here, verbatim and
        # verified passing on the day of the move. The backlog steps
        # (`backlog_append --check-live`, `check_backlog_refs`,
        # `check_backlog_criteria`, `backlog_union_merge`, `backlog_search`)
        # are deliberately NOT here: the four review backlogs they read are
        # archived, so those checks have no population left to grade.
        # A7 — the follow-through pipeline. Wired the day it was written,
        # because a module nothing runs is the "declared capability with no
        # consumer" antipattern this repo already names. --self-test asserts
        # the five documented drop-reasons; --check validates the real store.
        "name": "pipeline-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/pipeline.py", "--self-test"],
            ["python3", "scripts/ops/pipeline.py", "--check"],
        ],
    },
    # ─────────────────────────────────────────────────────────────────────
    # E45, 2026-09-22 — THREE GUARDS RE-ARMED AFTER BEING RE-POINTED.
    #
    # All three were dropped from this registry by the 2026-09-21 reset because
    # their subjects were archived, and all three are named in
    # `docs/CLAUDE-RULES-CANONICAL.md` — the #1 doc — as what ENFORCES a binding
    # rule. A canonical doc naming a mechanism that no longer runs is the
    # folklore failure that document has its own section about, so the choice
    # was re-point or retire, and the operator chose re-point for these three.
    #
    # Each one now grades a subject that EXISTS in the post-reset world, and
    # each ships a `--self-test` that PLANTS A VIOLATION against that new
    # subject and requires the guard to fail on it. A green re-point proves
    # nothing: these three spent a month green-or-quiet while grading zero rows,
    # which is the whole defect.
    # ─────────────────────────────────────────────────────────────────────
    {
        "name": "claim-basis-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/check_claim_basis.py", "--self-test"],
            ["python3", "scripts/check_claim_basis.py",
             "--base", "origin/{base_ref}"],
        ],
    },
    {
        "name": "soak-registered-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_soak_registered.py", "--self-test"],
            ["python3", "scripts/ci/check_soak_registered.py"],
        ],
    },
    {
        "name": "operator-owed-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_operator_owed.py", "--self-test"],
            ["python3", "scripts/ci/check_operator_owed.py"],
        ],
    },
    {
        # A1 — the spend meter. Wired the day it was written, same reasoning
        # as pipeline-guard: a module nothing runs is a declared capability
        # with no consumer. --self-test asserts the one sentence this module
        # exists to get right (never a fabricated reading when nobody has
        # looked); --check validates the real on-disk log of operator
        # readings.
        "name": "spend-meter-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/spend_meter.py", "--self-test"],
            ["python3", "scripts/ops/spend_meter.py", "--check"],
        ],
    },
    {
        # RE-ARMED 2026-09-21 (A3a) — `daily-brief-guard` was in the list of 40
        # governance guards deleted above, and its subject (the four-section
        # module keyed to the five archived registers) is genuinely gone. But
        # `scripts/ops/render_daily_brief.py` is not gone — A3 replaced it with
        # a six-section module reading only the pipeline store, the checklist
        # and `config/mandates.yaml`, and a module nothing runs is the
        # "declared capability with no consumer" antipattern this repo already
        # names (see pipeline-guard above, same reasoning). --self-test asserts
        # the six sections and the three-way read-state contract; --check
        # renders over the live tree and asserts the invariant sentences
        # without failing on an unreadable/absent input (grades the code, not
        # the data — the same discipline `pipeline-guard --check` follows).
        "name": "daily-brief-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/render_daily_brief.py", "--self-test"],
            ["python3", "scripts/ops/render_daily_brief.py", "--check"],
        ],
    },
    {
        # A9 — the work schedule. Same "wire it the day it's written" reasoning
        # as pipeline-guard/daily-brief-guard above: a module nothing runs is
        # the declared-capability-with-no-consumer antipattern. --self-test
        # asserts the decisions/monitoring split (both from pipeline.due(),
        # never re-derived) and the cron reader's negative control (a
        # commented-out `on.schedule` must not read as armed); --check
        # renders over the live tree (SCHEDULE.json, PIPELINE.jsonl,
        # .github/workflows/*.yml) and asserts the invariant sentences
        # without failing on an unreadable/absent input.
        "name": "schedule-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/render_schedule.py", "--self-test"],
            ["python3", "scripts/ops/render_schedule.py", "--check"],
        ],
    },
    {
        "name": "research-tooling-selftests",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_pending_pings_render.py", "--self-test"],
            ["python3", "scripts/ci/check_pending_pings_render.py"],
            ["python3", "scripts/ci/check_workflow_failure_swallow.py", "--self-test"],
            ["python3", "scripts/ci/check_workflow_failure_swallow.py"],
            # E45, 2026-09-22: the --self-test proves BOTH calls — a live
            # PIPELINE.jsonl row id PASSES (it could not be written before the
            # re-point) and a bad one FAILS as UNRESOLVED rather than as "no id
            # named". A guard that accepts everything is the same defect as one
            # that grades nothing, and this one printed OK while being
            # unsatisfiable.
            ["python3", "scripts/ops/check_allow_degraded.py", "--self-test"],
            ["python3", "scripts/ops/check_allow_degraded.py"],
            ["python3", "scripts/ops/check_research_index.py", "--list"],
            ["python3", "scripts/ops/check_workflow_shell.py"],
            ["python3", "scripts/ops/accrual_clock.py", "--self-test"],
            ["python3", "scripts/ops/accrual_clock.py", "--all"],
            ["python3", "scripts/ops/column_provenance.py", "--self-test"],
            ["python3", "scripts/ops/strategy_liveness.py", "--self-test"],
            ["python3", "scripts/ops/soak_alarm.py"],
            ["python3", "scripts/research/target_reachability_report.py"],
            ["python3", "scripts/research/e35_corpus_extract.py", "--selftest"],
            ["python3", "scripts/research/e35_verdicts_adapter.py", "--selftest"],
            ["python3", "scripts/research/bracket_expectation_census.py", "--selftest"],
            ["python3", "scripts/research/adx_entry_distribution.py", "--selftest"],
            ["python3", "scripts/research/bracket_reachability_audit.py", "--selftest"],
            ["python3", "-m", "pytest", "tests/test_check_research_index.py", "-q"],
        ],
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
        # The document register must not silently stop being true.
        #
        # UNGATED (`when: None`) deliberately, for the reason the api-tier-policy
        # guard above is: a diff-scoped run cannot see a row being DELETED from
        # the index, and it cannot see a document's header regress when an
        # unrelated PR edits that document. Both are the same hole the register
        # exists to close, just from opposite directions. It also cannot rely on
        # a `when`-gated step, which is skipped entirely under `--all` (push /
        # workflow_dispatch) — BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH.
        #
        # Costs 0.086s measured: one `git ls-files`, one table parse, and a
        # header read of 968 documents. Cheap enough that gating it would be the
        # more expensive decision.
        "name": "document-index-guard",
        "when": None,
        "steps": [
            # The planted-positive control runs on EVERY invocation. On a clean
            # tree this guard is only ever observed PASSING, which is the state
            # a guard is least useful in — and this repo has already shipped a
            # presence-only marker that was cheaper to lie to than to satisfy
            # (`new-table-wiring-guard`). Each of R1–R5 is fed a known-bad input
            # and the job fails unless the rule fires; clean inputs must stay
            # silent, so a rule that always fires is caught too.
            ["python3", "scripts/ci/check_document_index.py", "--self-test"],
            # ⚠️ `--base` is what makes R6 a RULE rather than a census. Without
            # it the guard cannot tell which rows this diff is responsible for,
            # so R6 reports nothing and only the standing count is printed —
            # which is "we could not look", not a pass. The census prints either
            # way, so dropping this flag degrades the guard silently; that is
            # why the reason is written here rather than only in the script.
            ["python3", "scripts/ci/check_document_index.py",
             "--base", "origin/{base_ref}"],
            # The whole-tree backstop, deliberately UNGATED — the
            # `diagnostic-provenance-guard` / `api-tier-policy-guard` pattern,
            # for the same reason and on the same evidence shape.
            #
            # ⚠️ THIS COULD NOT EXIST UNTIL 2026-09-13, and R6's own comment
            # says why: "There are 47 standing disagreements on `main` today;
            # an unscoped rule would fail every PR in the repo from the moment
            # it merged, which is not a guard, it is an outage." MEASURED
            # against a fresh `build_rows()`: **46 drifted rows at b209768c2
            # and 0 at `origin/main`** — the residue was drained by the
            # `document_index.py --write` that rode PR #12122. It is held at
            # zero here rather than re-measured by hand and found unchanged.
            #
            # ⚠️ WHY UNGATED AND NOT ONLY DIFF-SCOPED: the scoped step cannot
            # see a row drift that no PR touches, and that is the normal case
            # — a row drifts when `status_for` or `ACTIVE_DOCS` changes, i.e.
            # from a commit that touches neither the row nor its document. All
            # 46 accumulated exactly that way, invisibly, under a guard that
            # was reporting OK.
            #
            # ⚠️ THE COST IS REAL AND IS NOT HIDDEN: a change to `status_for`
            # or to `ACTIVE_DOCS` now reds every PR until someone runs
            # `--write`. That is the accepted trade in the two precedents
            # above, and it is the correct direction — the alternative is what
            # just happened, where the drift is discovered only when an
            # unrelated PR happens to run the writer. Rollback is deleting
            # this one step; the diff-scoped rule and the census are untouched
            # and keep working.
            ["python3", "scripts/ci/check_document_index.py", "--all"],
        ],
    },
    {
        # A GREEN `pytest-run` reports `N skipped` and nothing about which N,
        # so a whole test module going dark — every test under one
        # module-level skipif on an absent tool — reads identically to routine
        # conditional coverage. That is the "green that checked nothing" class,
        # arriving through the one door nobody watches.
        #
        # This registers only the SELF-TEST. The summariser itself runs inside
        # `pytest-run` (it needs that job's JUnit report, which does not exist
        # here), so what CI can check at guard time is that the renderer still
        # separates `fully_dark` from `partial` and still refuses to render an
        # unreadable report as a clean sheet.
        "name": "pytest-skip-attribution-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/summarize_pytest_skips.py", "--self-test"],
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
        # The map from an exit-relevant HARNESS FLAG to a matrix LEVER COLUMN.
        # BL-20260810-EXIT-LEVER-SPACE-UNDER-ENUMERATED asks that every such
        # flag map to a column OR carry a recorded n/a with a reason; that
        # answer was re-derived by hand three times and the three answers
        # disagree, because each used a different harness population without
        # saying so. This grades the recorded map for COMPLETENESS.
        #
        # `when` is scoped, unlike the unresolve guard's `when: None`: a new
        # flag can only appear by editing a harness, the map, or the matrix, so
        # there is nothing a broader scope would catch.
        #
        # ⚠️ It deliberately does NOT fail on `needs_column`. The criterion asks
        # that every flag have a RECORDED verdict, and "this needs a column" is
        # one. Failing on it would pressure the next session into re-labelling a
        # real gap as an n/a to get green — the guard-cheaper-to-lie-to-than-to-
        # satisfy shape this repo already paid for with `new-table-wiring-guard`.
        "name": "exit-lever-map-guard",
        "when": {"globs": ["scripts/backtest_*.py",
                           "docs/research/exit-lever-map.json",
                           "docs/research/exit-refinement-coverage.json",
                           "scripts/ops/exit_lever_map.py"]},
        "steps": [
            ["python3", "scripts/ops/exit_lever_map.py", "--self-test"],
            ["python3", "scripts/ops/exit_lever_map.py"],
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
        # THE DETECTOR, WIRED. THE REPORT, STILL NOT.
        #
        # check_guard_glob_coverage.py asks whether each guard is TRIGGERED by
        # every file its check actually reads -- the 2026-08-23 defect where
        # `exit-coverage-matrix-guard` joined config/strategies.yaml and did not
        # list it, so the one edit that could stale the matrix was the one edit
        # that would not run the guard.
        #
        # ⚠️ ONLY `--self-test` RUNS HERE, AND THAT IS THE WHOLE DESIGN. Its
        # report emits LEADS, not verdicts, and exits 1 on any un-triaged one --
        # its author declared it manual-only for exactly that reason, and was
        # right: a build failing on unconfirmed leads trains everyone to walk
        # past it, the desensitised-alarm P1. That reasoning is about the
        # REPORT. It was never an argument for leaving the DETECTOR unproven,
        # and the two were bundled.
        #
        # MEASURED 2026-09-12: `guard-selftest-coverage` graded this file
        # `none` -- "no failure-path evidence anywhere", the only one of 89 in
        # that bucket -- while its --self-test plants the real 2026-08-23 defect
        # (dropping config/strategies.yaml from that guard's globs) and requires
        # the audit to flag it, AND asserts the real table still reads clean. A
        # positive and a negative control, run by nothing. 0.4s, stdlib, no
        # network, no diff needed.
        #
        # `when: None`: the input it grades is the GUARDS table in this very
        # file plus the paths other guards' scripts open, so a PR that breaks it
        # need not touch anything a `when:` could name.
        #
        # ⚠️ THE LEADS STILL DO NOT GATE. Nothing here runs the report, so the
        # one live lead (exit-mechanism-coverage-guard reading
        # config/lever_reachability.json, re-confirmed non-verdict-bearing by
        # perturbation on 2026-09-12) cannot red a PR. If someone ever wires the
        # report too, that is a different decision and needs its own argument.
        "name": "guard-glob-coverage-detector",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_guard_glob_coverage.py", "--self-test"],
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
    # `edge-kind-vocabulary-guard` REMOVED 2026-09-21. It read the typed
    # `blocked_on` edge vocabulary off `docs/claude/work/README.md` and graded
    # the work store's edges. The work store is archived and the checklist's
    # `blocked_on` is a plain named blocker, not a typed edge — so the guard
    # now parses 0 files of 0 and reads its own missing marker as a finding.
    # Re-declaring a vocabulary nothing emits would be a marker cheaper to
    # satisfy than to mean.
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
    # `claim-basis-guard` REMOVED 2026-09-21. It scanned the four review
    # backlogs for basis-less claim rows and off-enum statuses. Those files
    # are archived, so it now reports "scanned NOTHING — an absent result,
    # not a clean one" and fails on its own empty denominator. Correctly:
    # the population is gone, not clean. The RULE it enforced is not gone —
    # "always state the population" is now top-level in CLAUDE.md § RULE ONE
    # and is still mechanically checked on prose by `stated-population-guard`.
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
        "name": "workflow-push-target-guard",
        # WHY THIS IS A GUARD AND NOT A NINTH CAREFUL FIX. `main` is
        # branch-protected, so a workflow's `git push origin HEAD:main` is
        # declined (GH006) and the run's entire artifact is discarded while the
        # job can still read green. This repo has fixed that ONE WORKFLOW AT A
        # TIME eight times — session-reaper, research-queue-dispatch,
        # gpu-burst-train, reconcile-open-prs, m20-exit-lever-sweep,
        # trainer-offload-train, replay-pregate-nightly, and sunset-pass.
        #
        # ⚠️ AND TWO HAND-WRITTEN CENSUSES OF THE CLASS WERE ALREADY WRONG,
        # which is the real argument: session-reaper.yml's own comment calls
        # itself "the ONLY workflow in the repo pushing straight to main"
        # (2026-09-02) while replay-pregate was doing it for ten more days, and
        # BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING
        # classifies training-rerun-5m as one that LANDS, on a predicate that
        # matches the PRESENCE of a push idiom. A census re-measured every PR
        # cannot go stale between being written and being quoted.
        #
        # UNGATED WHOLE-TREE, the api-tier-policy-guard / diagnostic-provenance
        # pattern — and it could only be ungated because the class is already
        # drained to ZERO. ⚠️ THIS GUARD DID NOT DRAIN IT AND MUST NOT BE READ
        # AS HAVING DONE SO: the last instance, sunset-pass.yml, was fixed by
        # #11900 (ce6f16b83) days before this landed, and re-measuring here is
        # what established that — `always_default 0, ungradeable 0` over 142
        # workflow files. An ungated guard landed over a live finding fails
        # every PR on day one and gets switched off, which is what the
        # diagnostic-provenance entry below records; this one lands green and
        # can therefore only ever catch a NEW violation.
        # The `conditional_default` rows are REPORTED and never fail: the relay
        # workflows' ordinary path is a feature branch where the push is
        # correct, and failing correct code is how a guard loses its reviewers.
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_workflow_push_target.py", "--self-test"],
            ["python3", "scripts/ci/check_workflow_push_target.py"],
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
        # Self-test FIRST. It EXISTED and nothing invoked it: the guard
        # shipped a passing 6-control `--self-test` that run_guards.py never
        # ran, which is the defect `check_selftest_wiring.py` was built for,
        # sitting outside its registry-shaped scope. Found by
        # check_guard_selftest_coverage.py on its first real run, 2026-09-09
        # (MI-226).
        "steps": [
            ["python3", "scripts/ci/check_lever_wiring.py", "--self-test"],
            ["python3", "scripts/ci/check_lever_wiring.py"],
        ],
    },
    {
        # MI-157. A committed candle file with no price variation makes every
        # backtest over it confidently meaningless. This runs UNGATED (`when:
        # None`) rather than on a data/ glob, because the class is defined by
        # the file's CONTENT and not by its location: the five that motivated
        # it sat under `data/ohlcv/`, but a flat corpus committed anywhere
        # would read exactly the same to a harness.
        "name": "candle-fixture-variance-guard",
        "when": None,
        "steps": [
            # The self-test runs on EVERY invocation. On a clean tree this
            # guard is silent, and silence from a detector nobody has seen
            # fail is the "green that checked nothing" this repo has a rule
            # about -- so it proves it can catch a flat series first.
            ["python3", "scripts/ci/check_candle_fixture_variance.py", "--self-test"],
            ["python3", "scripts/ci/check_candle_fixture_variance.py"],
        ],
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
        # Self-test FIRST: a guard whose planted controls no longer fire must
        # not report a clean scan. Added 2026-09-09 (MI-226) -- this guard ran
        # inside the REQUIRED `guards` context with no exercised failure path.
        "steps": [
            ["python3", "scripts/ci/check_news_feed_coverage.py", "--self-test"],
            ["python3", "scripts/ci/check_news_feed_coverage.py"],
        ],
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
        # ⚠️ A CHECK MUST BE ABLE TO SAY WHEN IT CANNOT SEE ITS SUBJECT. Three
        # independent instances in the week of 2026-09-22 reported a passing or
        # quiet state about a thing they could no longer see: check_manager_scope
        # (identity source archived, roster frozen, PASSED on 38 manager commits),
        # work_digest (five of six sources `absent`, self-reported healthy), and
        # check_manager_queue_watch (armed 479h, ~479 firings, ZERO receipts).
        # That is the collapsed-state rule applied to the CHECKS rather than to
        # the data they read — check_collapsed_states.py polices producers and
        # nothing policed the police.
        #
        # ⚠️ `when: None` BECAUSE ITS SUBJECT IS THE GUARD FLEET AND THE TREE, and
        # a diff-scoped version would pass vacuously on nearly every PR — the
        # reasoning check_soak_registered.py records for running whole-tree.
        # Measured cost: ~95 file parses plus ~92 `git cat-file -e` calls.
        #
        # ⚠️ IT IS REPORT-FIRST BY DESIGN. The 19 already-broken guards are
        # carried in a dated baseline that may only SHRINK; only a NEW dead or
        # degraded guard fails. Failing all 19 on day one would red-wall the repo
        # and get the guard reverted rather than the debt fixed.
        # ⚠️ FRESHNESS BELONGS IN CI, NOT ON A TIMER. The natural fix for a
        # silent scheduled check — "emit a receipt, and grade receipt freshness"
        # — IS ALREADY BUILT and is instance #3 of the class:
        # check_manager_queue_watch.py grades a receipt the Routine has never
        # written in 479 firings. A scheduled grader can always be the thing that
        # did not fire. CI cannot: it runs on every push, so its own liveness is
        # PROVEN BY PRs MERGING AT ALL.
        #
        # ⚠️ THE POPULATION IS DERIVED FROM THE TREE, NOT LISTED. Anything
        # declaring a cadence (workflow `schedule:`, deploy/*.timer) and absent
        # from CADENCE_REGISTRY FAILS — a hand-maintained list would reintroduce
        # the bug in new clothes. Measured 2026-09-22: 38 declarations, of which
        # exactly 1 is gradeable today, which is why it ships REPORT-FIRST with a
        # shrink-only baseline.
        "name": "cadence-liveness",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_cadence_liveness.py", "--self-test"],
            ["python3", "scripts/ci/check_cadence_liveness.py"],
        ],
    },
    {
        "name": "guard-liveness",
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_guard_liveness.py", "--self-test"],
            ["python3", "scripts/ci/check_guard_liveness.py"],
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
        "name": "strategy-decision-record",
        # C4 of the operating-layer build (Phase F). Compares the DECISION
        # RECORD (config/strategy_changelog.json) against the GATE it is a
        # record of (config/strategies.yaml). An execution flip is a Tier-3
        # decision written in two places and nothing has ever compared them, so
        # the record forked from the gate and nobody noticed for six weeks
        # (BL-20260901-DECISION-RECORD-SAYS-SHADOW-WHILE-CONFIG-SAYS-LIVE-SQUEEZE-BREAKOUT-4H).
        #
        # ⚠️ IT GRADES 0 OF 55 TODAY AND PASSES, DELIBERATELY. No changelog
        # entry carries the structured `execution_verdict` field yet, so there
        # is nothing to compare and that is the ACCURATE reading, not a bug.
        # Failing on zero coverage would red every PR in the repo on day one,
        # which is how a guard gets disabled instead of fixed — the
        # `check_pr_queue_watch.py` `never_ran` precedent. It ARMS ITSELF: the
        # first typed entry makes that leg gradeable and a divergence on it
        # fails. There is no flag to unset.
        #
        # It deliberately does NOT parse the 53 legacy entries' English —
        # sub-class A of the diagnostic-provenance defect. They are counted and
        # reported as ungradeable, never guessed at.
        #
        # Self-test FIRST, same posture as collapsed-state-guard: a guard that
        # reports a clean tree needs an exercised failure path, or "0
        # divergences" cannot be told from "stopped matching". The stronger
        # control is the POSITIVE one in tests/test_strategy_decision_record.py,
        # which types the real squeeze_breakout_4h demotion entry on a copy of
        # the real changelog and asserts the guard fires.
        "when": {"globs": [
            "config/strategies.yaml",
            "config/strategy_changelog.json",
            "scripts/ci/check_strategy_decision_record.py",
            "src/strategy_registry.py",
        ]},
        "steps": [
            ["python3", "scripts/ci/check_strategy_decision_record.py", "--self-test"],
            ["python3", "scripts/ci/check_strategy_decision_record.py"],
        ],
    },
    {
        "name": "roster-promotion-evidence-guard",
        # B1, THE LOAD-BEARING CHANGE. The counterpart that never existed:
        # `scripts/check_dry_run_in_diff.py` blocks turning a leg OFF and
        # NOTHING blocked turning one ON. That asymmetry is the mechanical fact
        # behind the roster going 36 -> 55 while every memo said cut.
        #
        # ⚠️ IT KEYS ON ROSTER MEMBERSHIP, NOT ON `mode:` / `execution:`. There
        # are TWO SPELLINGS OF OFF and the old guard sees only one: it matches
        # ADDED LINES, so removing a leg from an account's `strategies:` list is
        # invisible to it (verified against the A6 diff, which removed two legs
        # from alpaca_live and reported `clean`). A guard watching the fields is
        # walked around by editing the list, in EITHER direction; this one
        # compares the PARSED roster at the fork point against HEAD, so it also
        # sees a flow-style `strategies: [a, b]` edit that no line regex covers.
        #
        # ⚠️ DEMOTION STAYS FREE, and that is the entire point of inverting.
        # `--self-test` plants both directions: removing a leg, and moving an
        # account out of a risk-bearing class, must BOTH stay silent. A change
        # that makes removal harder has broken this guard's purpose.
        #
        # ⚠️ IT GRADES 0 OF 12 TODAY AND PASSES, DELIBERATELY — same posture as
        # strategy-decision-record above. It is DIFF-SCOPED: the existing roster
        # is grandfathered (`check_backlog_criteria.py`'s polarity — the past is
        # grandfathered, the future is not) because a whole-tree version would
        # red every PR in the repo over 12 legs nobody is proposing to promote,
        # which is how a guard gets disabled instead of fixed. The standing
        # census is reported by `--population`, which never fails a build.
        #
        # Self-test FIRST: this guard REFUSES work, so one that started failing
        # correct PRs would be worse than the problem it fixes — the
        # manager-scope-guard posture.
        #
        # The globs include `comms/strategy_evidence/**` and
        # `config/strategies.yaml` because the check READS them (the evidence
        # record, and the config the record's fingerprint is bound to). Scoping
        # a two-sided check to one side is the exit-coverage-matrix defect that
        # `check_guard_glob_coverage.py` exists to catch.
        "when": {"globs": [
            "config/accounts.yaml",
            "config/strategies.yaml",
            "config/pairs.yaml",
            "comms/strategy_evidence/**",
            "scripts/ci/check_roster_promotion_evidence.py",
        ]},
        "steps": [
            ["python3", "scripts/ci/check_roster_promotion_evidence.py", "--self-test"],
            {
                "argv": ["python3", "scripts/ci/check_roster_promotion_evidence.py",
                         "--base", "origin/{base_ref}"],
                "pr_only": True,
            },
        ],
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
        "name": "diag-relay-render-guard",
        # A guard that is TRUNCATED AWAY is not a guard. The relay cuts each
        # path's JSON to a byte budget, and the db-explorer envelope orders
        # `rows` BEFORE `total`/`filter_state`/`count` — so a head truncation
        # kept the data and dropped the fields that invalidate it
        # (BL-20260816-TRUNCATION-STRIPS-THE-FIELDS-THAT-CERTIFY-A-RESPONSE).
        #
        # The self-test runs on EVERY invocation, same reasoning as
        # exit-mechanism-coverage-guard: it carries NEGATIVE CONTROLS asserting
        # that the OLD head-truncation drops `filter_state` and the denominator,
        # and a probe that cannot show the defect proves nothing about the fix.
        #
        # The workflow is globbed too: this logic was inline YAML python and
        # therefore untestable, which is why it shipped wrong and stayed wrong.
        "when": {"globs": ["scripts/ops/diag_relay_render.py",
                           ".github/workflows/vm-diag-snapshot.yml"]},
        "steps": [
            ["python3", "scripts/ops/diag_relay_render.py", "--self-test"],
        ],
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
        # ⚠️ THE GLOBS MUST TRACK THE TOOL'S `_IMPL_DIRS`, NOT A SUBSET OF IT.
        # `exit_mechanism_coverage.py` decides "does this leg's unit implement
        # the lever?" by scanning `_IMPL_DIRS = (src/units/strategies,
        # src/runtime)` — and it scans src/runtime WHOLESALE on purpose, its
        # own comment saying an explicit module list "is exactly the move that
        # broke the source-only greps in
        # BL-20260818-CAPABILITY-AUDITS-GREP-ONE-FILE-AND-MISS-SHARED-LEVERS".
        # This trigger named ONE file out of that directory
        # (strategy_signal_builders.py), so the guard fired on strictly LESS
        # than its own input: measured 2026-09-13, none of the four shared
        # modules the tool actually reads —
        # `src/runtime/{exit_levers,exit_head_apply,trail_decay,exit_head_shadow}.py`
        # — was selected by any glob here. Removing `exit_head_verdict` from
        # `exit_head_apply.py` orphans every delegating leg's declare, and this
        # guard would not have run on that PR. `src/runtime/*.py` restores the
        # correspondence; an explicit four-module list would reproduce the very
        # bug the tool's wholesale scan exists to avoid
        # (BL-20260816-EXIT-HEAD-LEVER-HAS-NO-CONSUMER-IN-ICT-SCALP clause (a)).
        "when": {"globs": ["config/strategies.yaml",
                           "src/units/strategies/*.py",
                           "src/runtime/*.py",
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
        "name": "lever-evidence-flag-guard",
        # The sibling of lever-reachability-guard, and it grades what that one
        # cannot: reachability pins `arm_r` to config, so a verdict stays
        # "current" while its DENOMINATOR goes unexamined. `gld_pullback_1d`
        # carried `inert` / `recorded_inert` -- "no observed entry could reach
        # the arm" -- on 0 of 8, whose exact 95% upper bound is 36.9%.
        #
        # The self-test runs on EVERY invocation for the same reason its
        # siblings' do, and one of its cases is a NON-VACUITY control: a cell
        # that must come out `unsettled`. A flag that had quietly lost the
        # ability to say "I don't know" would otherwise look clean while
        # rubber-stamping every verdict it grades.
        "when": {"globs": ["config/lever_reachability.json",
                           "scripts/ops/lever_evidence_flag.py"]},
        "steps": [
            ["python3", "scripts/ops/lever_evidence_flag.py", "--self-test"],
            ["python3", "scripts/ops/lever_evidence_flag.py", "--check"],
        ],
    },
    {
        "name": "guard-selftest-coverage",
        # UNGATED (`when: None`): this measures the guard POPULATION, so a diff
        # filter would make it blind to exactly the change that matters -- a
        # guard landing with no failure path. Its own self-test runs FIRST for
        # the same reason every sibling's does: a coverage instrument whose
        # failure path is unexercised is the finding it exists to report, one
        # level up. Added 2026-09-09 (MI-226, audit F-01).
        "when": None,
        "steps": [
            ["python3", "scripts/ci/check_guard_selftest_coverage.py", "--self-test"],
            ["python3", "scripts/ci/check_guard_selftest_coverage.py"],
        ],
    },
    {
        "name": "provenance-consumer-guard",
        "when": {"regex": r"\.py$"},
        # Self-test FIRST: a guard whose planted controls no longer fire must
        # not report a clean scan. Added 2026-09-09 (MI-226) -- this guard ran
        # inside the REQUIRED `guards` context with no exercised failure path.
        "steps": [
            ["python3", "scripts/check_provenance_consumers.py", "--self-test"],
            ["python3", "scripts/check_provenance_consumers.py", "--verbose"],
        ],
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
            ["python3", "scripts/check_strategy_coverage.py", "--self-test"],
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


class CouldNotRun(str):
    """A reason meaning WE DID NOT LOOK — never "the diff is bad".

    ⚠️ THIS IS A THIRD STATE AND POOLING IT WITH `fail` COST TWO REAL DEFECTS.
    On 2026-09-10 a session ran this script in a container without `pytest`,
    read `FAIL 4, all four environmental, not the diff`, and shipped. CI — the
    one runner where the tools are installed — then named two load-bearing
    defects that were sitting inside those very guards: a `system-action`
    absent from `EXPECTED_ACTIONS`, which would have merged looking correct and
    aborted the first time an operator ran an approved real-money-adjacent
    repair; and a `notify_run.sh` with no priority mapping. Neither is
    reachable by any non-pytest guard.

    ⚠️ THE GAP WAS ROUTING, NOT KNOWLEDGE. The one-command remedy was already
    written down — in a 1298-row backlog `CLAUDE.md` itself calls too large to
    read at session start — while the coordination board, which every session
    DOES read, carried a true-and-incomplete note whose practical effect was to
    license discounting three guards. So the remedy has to arrive HERE, at the
    moment the condition is hit, and not in another document.

    ⚠️ AND IT STILL FAILS THE RUN. A guard that could not run is not a guard
    that passed, and in CI an absent runner means the image is broken. What
    changes is only that it can no longer be mistaken for a finding about the
    diff.
    """


# How to get each runner back. Keyed by the RUNNER, not by the guard, because
# a new guard using pytest must inherit the remedy without anyone remembering.
# POPULATION (measured over the registry, 2026-09-12): 212 steps — `python3`
# x210 of which 3 are `python3 -m pytest`, `lint-imports` x1, `ruff` x1.
RUNNER_REMEDY = {
    "pytest": "pip install pytest",
    "lint-imports": "pip install import-linter",
    "ruff": "pip install ruff",
}

_RUNNER_PRESENT: Dict[str, bool] = {}


def _module_importable(interpreter: str, module: str) -> bool:
    """Ask THE INTERPRETER THE STEP WILL USE, not this process.

    `importlib.util.find_spec` here answers about the process running
    run_guards.py, which need not be the same environment as `python3` on PATH.
    Getting that wrong would report a runner present that the step cannot find,
    which is the failure this whole change is about, one level down.
    """
    try:
        proc = subprocess.run(
            [interpreter, "-c",
             "import importlib.util,sys;"
             f"sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"],
            cwd=REPO, capture_output=True)
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def missing_runner(argv: Sequence[str]) -> Optional[str]:
    """The runner this step needs and does not have, or ``None``.

    ``None`` means "the runner is there", NOT "we did not check" — every
    supported shape is probed, and an unrecognised shape is reported present
    so this can never turn a real failure into a silent `could_not_run`.
    """
    if not argv:
        return None
    head = argv[0]
    if head in ("python3", "python") and len(argv) >= 3 and argv[1] == "-m":
        mod = argv[2]
        key = f"{head} -m {mod}"
        if key not in _RUNNER_PRESENT:
            _RUNNER_PRESENT[key] = _module_importable(head, mod)
        return None if _RUNNER_PRESENT[key] else mod
    if head not in _RUNNER_PRESENT:
        _RUNNER_PRESENT[head] = shutil.which(head) is not None
    return None if _RUNNER_PRESENT[head] else head


def _absent_runner_reason(argv: Sequence[str], runner: str) -> CouldNotRun:
    remedy = RUNNER_REMEDY.get(runner)
    fix = (f"    FIX, one command: {remedy}" if remedy else
           f"    `{runner}` is not on PATH and this script has no recorded "
           f"remedy for it — add one to RUNNER_REMEDY so the next session is "
           f"not left to work it out.")
    print(f"    ::error::COULD NOT RUN — `{runner}` is not available in this "
          f"environment, so this guard checked NOTHING. That is not a finding "
          f"about your diff.", flush=True)
    print(fix, flush=True)
    return CouldNotRun(
        f"`{' '.join(argv)}` could not run: {runner} is absent"
        + (f" — {remedy}" if remedy else ""))


def _child_env() -> dict:
    """The environment guards are spawned in — with their own dirty-tree notice OFF.

    ⚠️ THIS IS NOT A WEAKENING, IT IS DEDUPLICATION. Every `--base` guard now
    emits its own notice (`scripts/ci/_dirty_tree.py`), because a session chasing
    one failure types the guard and not this orchestrator. Inside THIS run the
    same fact is already stated ONCE, with more in it — the split between paths
    that are in the graded diff and paths that are not. Letting both speak was
    MEASURED at 4 copies from a single selected guard (its self-test and its real
    invocation each print), which over the full set is dozens of identical
    paragraphs around one true fact: the desensitised alarm this repo calls its
    own P1.

    ⚠️ It is set ONLY here, for children of this process. Nothing writes it to a
    shell profile or a workflow, so a session running a guard by hand — the case
    the per-guard notice exists for — is unaffected.
    """
    env = dict(os.environ)
    env["DIRTY_TREE_NOTICE"] = "0"
    return env


def _run(argv: Sequence[str]) -> int:
    print(f"    $ {' '.join(argv)}", flush=True)
    try:
        proc = subprocess.run(argv, cwd=REPO, env=_child_env())
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
        absent = missing_runner(argv)
        if absent is not None:
            print(f"    $ {' '.join(argv)}", flush=True)
            return _absent_runner_reason(argv, absent)
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


# ---------------------------------------------------------------------------
# ARM THE REGISTER MERGE DRIVER — for a session that never thought to
# ---------------------------------------------------------------------------
# BL-20260906-REGISTER-MERGE-DRIVER-SHIPS-UNARMED-AND-A-SESSION-PAID-ELEVEN-HAND-RESOLUTIONS
# asks for the driver to be "ARMED BY DEFAULT for a session that did not think
# to arm it", and its criterion (a) is "a repo bootstrap step runs
# install_merge_driver.sh, so a fresh container arms itself".
#
# ⚠️ WHY HERE AND NOT IN A HOOK OR A DOC. Project hooks DO NOT RUN on Claude
# Code on the web (CLAUDE.md records the measurement), so a SessionStart hook
# arms nothing in exactly the containers that are freshest. And the row itself
# forbids the doc answer: "DO NOT CLOSE THIS BY ADDING A REMINDER TO A DOC
# NOBODY READS MID-TASK -- that is the 'reminder is not a mechanism' non-fix
# this repo has already paid for on MI-15 (twice)." What every session DOES run,
# worker and manager alike, is this file, before every push.
#
# ⚠️ `manager_preflight.py` ALREADY GRADES THIS and is not duplicated: it FAILS
# an un-armed clone with three never-collapsed states. But only a MANAGER runs
# it, and the eleven hand-resolutions were paid by a worker. This reuses that
# module's `merge_driver_installed()` rather than re-deriving the read, because
# two answers to "is this clone armed?" are free to drift.
#
# ⚠️ IT NEVER FAILS THE RUN AND IT NEVER TOUCHES CI. A guard run that went red
# over a client-side convenience would be a red nobody can act on from a PR, and
# a CI clone is thrown away after one job -- arming it is a side effect with no
# beneficiary. `skipped_ci` is therefore its own state, not a silent no-op.
#
# ⚠️ AND IT CHANGES NOTHING ABOUT GITHUB. Custom merge drivers are client-side;
# a conflicted register PR still reports `dirty`. What this removes is the cost
# of resolving that BY HAND.
ARM_SKIPPED_CI = "skipped_ci"
ARM_ALREADY = "already_armed"
ARM_ARMED = "armed"
ARM_FAILED = "arm_failed"
ARM_UNKNOWN = "could_not_look"
ARM_NO_INSTALLER = "no_installer"

INSTALL_MERGE_DRIVER = REPO / "scripts" / "ops" / "install_merge_driver.sh"


def arm_decision(in_ci: bool, installed: Optional[bool],
                 installer_exists: bool) -> str:
    """PURE — what SHOULD happen. The doing is separate, so the policy is
    arguable in a test rather than against a real clone's git config.

    ⚠️ `installed is None` is *we could not read this clone's git config* and
    grades `could_not_look`. It must never be folded into `already_armed` (which
    would bank a claim nobody checked) nor into "arm it" (which would run an
    installer over a state we could not see).
    """
    if in_ci:
        return ARM_SKIPPED_CI
    if installed is None:
        return ARM_UNKNOWN
    if installed:
        return ARM_ALREADY
    return ARM_ARMED if installer_exists else ARM_NO_INSTALLER


def arm_register_merge_driver() -> str:
    """Best-effort. Returns the state; prints one line; never raises."""
    in_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    installed: Optional[bool] = None
    try:
        sys.path.insert(0, str(REPO / "scripts" / "ops"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_mp_arm", REPO / "scripts" / "ops" / "manager_preflight.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        installed = mod.merge_driver_installed()
    except Exception:  # noqa: BLE001 — any failure is `we could not look`
        installed = None

    state = arm_decision(in_ci, installed, INSTALL_MERGE_DRIVER.exists())
    if state == ARM_ARMED:
        try:
            rc = subprocess.run(["bash", str(INSTALL_MERGE_DRIVER)],
                                capture_output=True, text=True, timeout=60,
                                cwd=str(REPO)).returncode
        except (OSError, subprocess.SubprocessError):
            rc = 1
        if rc != 0:
            state = ARM_FAILED

    if state == ARM_SKIPPED_CI:
        return state
    msg = {
        ARM_ALREADY: "register merge driver: already armed in this clone.",
        ARM_ARMED: ("register merge driver: ARMED this clone "
                    "(scripts/ops/install_merge_driver.sh). Sibling register "
                    "appends now merge row-aware instead of by line. "
                    "Client-side only — GitHub still reports a conflicted PR "
                    "as dirty."),
        ARM_FAILED: ("register merge driver: could NOT be armed — "
                     "install_merge_driver.sh exited non-zero. Register "
                     "conflicts will need hand resolution."),
        ARM_NO_INSTALLER: ("register merge driver: scripts/ops/"
                           "install_merge_driver.sh is MISSING, so this clone "
                           "cannot be armed."),
        ARM_UNKNOWN: ("register merge driver: this clone's git config could NOT "
                      "be read, so whether it is armed is UNESTABLISHED — that "
                      "is `we did not look`, not `it is fine`."),
    }[state]
    print(msg)
    return state


def dirty_tree_lines(tree_dirty: List[str], changed: List[str]) -> List[str]:
    """The uncommitted-work notice, as lines. PURE, so it is testable.

    ⚠️ WHY THIS IS NOT ONLY IN THE ALL-PASSED FOOTER. Until 2026-09-13 the
    notice was built inside the `All SELECTED guards passed — but …` branch,
    which `main()` reaches only after returning early on `failures` and on
    `could_not_run`. So the ONE run where a stale verdict is most confusing —
    a red you have already fixed in the working tree and cannot clear — printed
    NOTHING about the tree. MEASURED that day on `main` @`950244f87`: a failing
    guard with a dirty tree produced `PASS 0 · FAIL 1 · COULD-NOT-RUN 0 ·
    SKIP 0` and **zero** occurrences of the word "uncommitted" anywhere in the
    output.

    ⚠️ IT DOES NOT FIX ANYTHING FOR YOU, and that is the row's instruction in
    terms: no stashing, no committing, no grading the worktree instead. The
    guards' committed-state reading is what CI does, and changing it would make
    the local run disagree with CI — worse than the trap.

    ⚠️ THE SPLIT IS THE POINT. A dirty path that is ALSO in the graded diff is
    the dangerous one: its guard RAN, passed, and was counted, having read the
    committed version rather than yours. A dirty path outside the diff at least
    tends to drop its guard from selection, which the `NOT GRADED` qualifier
    already reports.

    Returns `[]` for a clean tree — a notice that fires every run is walked
    past, which is this repo's own stated P1.
    """
    if not tree_dirty:
        return []
    in_diff = [f for f in tree_dirty if f in set(changed)]
    outside = [f for f in tree_dirty if f not in set(changed)]
    out = [
        "",
        f"UNCOMMITTED WORK ({len(tree_dirty)} path(s)) — every guard above is "
        f"scoped to a COMMIT RANGE, so NOTHING here read your working tree:",
    ]
    for f in in_diff:
        out.append(f"  - {f}  ← ALSO in the graded diff: its guard RAN and "
                   f"PASSED on the COMMITTED version, not on this one")
    for f in outside:
        out.append(f"  - {f}")
    out.append("  Commit them and re-run. This does NOT change the exit code — "
               "a dirty tree is not a guard failure, it is a verdict about a "
               "different tree than the one you are looking at.")
    return out


def counts_line(n_pass: int, n_fail: int, n_could_not_run: int,
                n_skip: int, n_not_graded: int = 0,
                n_dirty_paths: int = 0) -> str:
    """The headline. A PURE function, so what it CLAIMS is arguable in a test.

    ⚠️ **THE NOT-GRADED COUNT BELONGS HERE, NOT ONLY IN A FOOTER**
    (`BL-20260903-RUN-GUARDS-PRINTS-FAIL-0-ON-A-RUN-IT-KNOWS-WAS-INCOMPLETE`).
    Guard relevance is computed from a COMMIT RANGE, so a run started with
    uncommitted work silently drops every guard gated on those paths. The
    script DETECTS that and says so — under a skip list dozens of lines long,
    below the one line a reader actually scans for green.

    ⚠️ **THE FOOTER CAVEAT WAS ALREADY THERE AND WAS NOT ENOUGH.** "All
    SELECTED guards passed — but …" landed 2026-08-13 (#8948); the row was
    filed **2026-09-03, three weeks later**, by an author looking at that
    output. MEASURED then, same tree back to back: uncommitted 47 pass / 17 not
    selected, committed 64 pass / 0 not selected — and the seventeen included
    `canonical-doc-coherence`, `ruff-lint` and `collapsed-state-guard`,
    precisely the guards with something to say about that diff.

    ⚠️ **NOT a fifth bucket beside PASS/FAIL/SKIP.** These guards are ALREADY
    counted in `skipped`; adding them again would stop the counts summing to
    the number of guards considered. It is a QUALIFIER, and it renders only
    when there is something to qualify — a clean run's line is byte-identical
    to what it has always been, so this cannot become an always-on decoration
    a reader learns to skip past.

    ⚠️ **AND IT DOES NOT CHANGE THE EXIT CODE, deliberately.** The row says so
    in terms: local iteration on a dirty tree is the normal way to use this
    script, and failing it would train people to ignore the runner. What is
    fixed is the SUMMARY claiming more than the run established.

    ⚠️ **`n_dirty_paths` IS A SECOND, DIFFERENT FACT AND IS NEVER FOLDED INTO
    THE FIRST.** The row is
    `BL-20260913-A-GUARD-RUN-BEFORE-COMMITTING-GRADES-THE-WRONG-TREE-AND-ITS-VACUOUS-PASS-IS-INDISTINGUISHABLE-FROM-A-REAL-ONE`
    -- kept on ONE line despite the width, because `artifact-validity-guard`
    resolves ids by text and a WRAPPED id is a dangling reference. It caught
    exactly that here, on the third variant of the same mistake in one session
    (elided twice, then wrapped); an id reformatted for readability stops being
    an id.
    `n_not_graded` counts GUARDS that relevance DROPPED; `n_dirty_paths` counts
    PATHS that no guard read. They are not the same set and neither implies the
    other — relevance is a UNION, so if any COMMITTED file already made a guard
    relevant it RUNS, passes, is counted, and never appears in `n_not_graded`,
    while still having scanned a range without your edits.

    MEASURED 2026-09-13 on `main` @`950244f87`, one committed edit plus an
    UNCOMMITTED edit to the SAME file: `PASS 1 · FAIL 0 · COULD-NOT-RUN 0 ·
    SKIP 0` — no qualifier of any kind — while the footer did carry the caveat.
    Collapsing the two would have printed `NOT GRADED 0` there and been wrong
    in the reassuring direction.
    """
    line = (f"PASS {n_pass} · FAIL {n_fail} · "
            f"COULD-NOT-RUN {n_could_not_run} · SKIP {n_skip}")
    if n_not_graded:
        line += f" · {n_not_graded} NOT GRADED (uncommitted)"
    if n_dirty_paths:
        line += f" · {n_dirty_paths} PATH(S) UNCOMMITTED"
    return line


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

    # Before anything else, and never fatal. See `arm_register_merge_driver`.
    arm_register_merge_driver()

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

    # ⚠️ NAME THE TREE WE ARE GRADING, IN THE OUTPUT ITSELF.
    # Every line below this is a verdict ABOUT a commit, and until 2026-09-12
    # not one line said WHICH — the header carried `event` and `base` and
    # nothing identifying HEAD. A saved run therefore could not be attributed,
    # and a run whose tree MOVED under it (a background run while the session
    # checks out another branch) produced a confident verdict for a branch it
    # had not finished reading. MEASURED: a `pr-landing-guard` FAIL was
    # recorded against PR #11928 by exactly that route, and reproducing it by
    # hand on a stable tree returned OK. That is the implicit-input-selection
    # shape `check_diagnostic_provenance.py` exists to catch, in the harness
    # that runs it.
    #
    # Read from git rather than from the environment, so it is right when run
    # locally too; `unknown` when git cannot answer -- WE COULD NOT LOOK, never
    # a fabricated sha.
    def _git_say(*argv: str) -> str:
        try:
            r = subprocess.run(["git", *argv], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        out = (r.stdout or "").strip()
        return out if (r.returncode == 0 and out) else "unknown"

    head_sha = _git_say("rev-parse", "HEAD")
    head_branch = _git_say("rev-parse", "--abbrev-ref", "HEAD")
    head_dirty = "dirty" if tree_dirty_at_start else "clean"

    print("=" * 72)
    print(f"guards — {len(GUARDS)} registered · event={args.event_name} · base={args.base_ref}")
    print(f"grading {head_branch} @ {head_sha[:12] if head_sha != 'unknown' else 'unknown'} "
          f"· worktree {head_dirty} at start")
    # ⚠️ SAY IT AT THE START AS WELL AS AT THE END. The word "dirty" above is
    # a state, not a consequence, and a reader who does not already know that
    # guards read a COMMIT RANGE has no reason to act on it. This names the
    # count and what it costs them, at the first point they could still fix it
    # — before an eight-minute run, rather than after.
    #
    # ⚠️ IT IS NOT THE LOAD-BEARING HALF, and the row that asked for this says
    # so in terms: the output runs to hundreds of lines, so anything here
    # scrolls away. The half that survives is the UNCOMMITTED WORK block and
    # the counts-line qualifier in the summary. This is the cheap early warning
    # on top of it, never a substitute for it.
    #
    # Nothing prints on a clean tree — an alarm that fires every run is walked
    # past, which is this repo's own stated P1.
    if tree_dirty_at_start:
        shown = ", ".join(tree_dirty_at_start[:5])
        more = f" (+{len(tree_dirty_at_start) - 5} more)" if len(tree_dirty_at_start) > 5 else ""
        print(f"⚠️  {len(tree_dirty_at_start)} path(s) UNCOMMITTED — guards are "
              f"scoped to a COMMIT RANGE, so nothing below will read them: "
              f"{shown}{more}")
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
    # ⚠️ A THIRD BUCKET, NEVER POOLED WITH `failures`. A guard whose RUNNER is
    # absent checked nothing; reporting that as a failure is what let a session
    # read `FAIL 4, all four environmental` and ship two real defects on
    # 2026-09-10. It still fails the run — see `CouldNotRun` — it just stops
    # reading as a finding about the diff.
    could_not_run: List[tuple] = []
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
        elif isinstance(reason, CouldNotRun):
            could_not_run.append((name, reason))
            print(f"--- {name}: COULD NOT RUN ({dt:.1f}s) — {reason}",
                  flush=True)
        else:
            failures.append((name, reason))
            if guard.get("notify"):
                notify.append(name)
            print(f"--- {name}: FAIL ({dt:.1f}s) — {reason}", flush=True)

    # ── COMPUTED BEFORE THE HEADLINE, BECAUSE THE HEADLINE IS WHAT IS READ ──
    # Guards that WOULD have been relevant to the working tree and were not
    # selected, because relevance is computed from a COMMIT RANGE. This was
    # computed ~40 lines below, after the counts line had already printed
    # `FAIL 0` over a run the script itself knew was incomplete
    # (`BL-20260903-RUN-GUARDS-PRINTS-FAIL-0-ON-A-RUN-IT-KNOWS-WAS-INCOMPLETE`).
    #
    # ⚠️ THE FOOTER CAVEAT WAS ALREADY THERE AND WAS NOT ENOUGH. "All SELECTED
    # guards passed — but …" landed 2026-08-13 (#8948); the row was filed
    # 2026-09-03, THREE WEEKS LATER, by an author looking at that output. The
    # truth was in a footer under a 38-line skip list, and the counts line —
    # the thing a reader scans for green — said `FAIL 0` and nothing else.
    # MEASURED then, same tree back to back: uncommitted 47 pass / 17 not
    # selected, committed 64 pass / 0 not selected, and the seventeen included
    # canonical-doc-coherence, ruff-lint and collapsed-state-guard — precisely
    # the ones with something to say about that diff.
    unchecked = sorted({g["name"] for g in selected
                        if g["name"] in skipped and is_relevant(g["when"], dirty)})

    print("\n" + "=" * 72)
    print(counts_line(len(passed), len(failures), len(could_not_run),
                      len(skipped), len(unchecked),
                      len(tree_dirty_at_start)))
    if could_not_run:
        print()
        print("COULD NOT RUN — these guards CHECKED NOTHING. This is the "
              "ENVIRONMENT, not your diff, and it is not a pass either:")
        remedies = []
        for n, r in could_not_run:
            print(f"  ::error::{n}: {r}")
            for runner, cmd in RUNNER_REMEDY.items():
                if f"{runner} is absent" in r and cmd not in remedies:
                    remedies.append(cmd)
        if remedies:
            print("  Run this, then re-run the guards — it is one command and "
                  "it is the whole fix:")
            for cmd in remedies:
                print(f"      {cmd}")
        print("  ⚠️ DO NOT discount these guards instead. On 2026-09-10 a "
              "session did, on this exact condition, and CI then named TWO "
              "real defects sitting inside them — neither reachable by any "
              "guard that does not need this runner.")
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
    # `unchecked` is computed ABOVE, before the counts line — see the note there.
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
    for line in dirty_tree_lines(tree_dirty_at_start, changed):
        print(line)
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
        if could_not_run:
            print(f"  ⚠️ AND {len(could_not_run)} FURTHER GUARD(S) COULD NOT "
                  f"RUN AT ALL — listed above. This list is therefore a LOWER "
                  f"BOUND on what is wrong; install the missing runner before "
                  f"concluding anything from it.")
        return 1

    # ⚠️ A GUARD THAT COULD NOT RUN IS NOT A GUARD THAT PASSED, so this exits
    # non-zero even with zero failures. In CI it means the image is broken and
    # a green here would be a green that checked nothing — the one outcome this
    # repo treats as worse than a red.
    if could_not_run:
        print(f"\nNO GUARD FAILED — but {len(could_not_run)} could not run, "
              f"so this is NOT a pass. See the COULD NOT RUN block above for "
              f"the one-command fix.")
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
        # The paths themselves are NAMED in the UNCOMMITTED WORK block above,
        # which prints on every return path. Repeating the list here would be
        # the same information twice in ten lines; repeating the FACT is the
        # point, since this is the sentence a reader takes as the verdict.
        caveats.append(f"{len(tree_dirty)} path(s) are UNCOMMITTED and every "
                       f"guard is scoped to a commit range, so nothing here "
                       f"scanned them (named above)")
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
