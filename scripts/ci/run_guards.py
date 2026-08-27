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
        # The registers only work if their contents reach a session BEFORE it
        # acts. Hooks do not run on Claude Code on the web (verified
        # 2026-08-26) and CI fires at merge, so CLAUDE.md's inlined SESSION
        # BRIEF is the only channel that arrives in time. This guard keeps that
        # block in sync — a STALE brief is worse than none, because a session
        # would read something no longer true and act on it.
        "name": "session-brief-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/render_session_brief.py", "--self-test"],
            ["python3", "scripts/ops/render_session_brief.py", "--check"],
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
        # drop-in (diagnostic-provenance --all exits 1 on 52 grandfathered
        # sites). Stated plainly so the next reader does not re-derive it.
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
