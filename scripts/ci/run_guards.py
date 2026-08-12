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
        "name": "artifact-validity-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/ops/check_artifact_validity.py", "--allow-missing"],
            ["python3", "scripts/ci/check_workflow_failure_swallow.py"],
            ["python3", "scripts/ops/check_allow_degraded.py"],
            ["python3", "scripts/ops/check_research_index.py", "--list"],
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
        "name": "harness-lever-coupling-guard",
        "when": None,
        "steps": [
            ["python3", "scripts/check_harness_lever_coupling.py"],
            ["python3", "scripts/ci/guard_selftests.py", "harness-lever-coupling"],
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
        "name": "collapsed-state-guard",
        "when": {"regex": r"\.py$"},
        "steps": [["python3", "scripts/ci/check_collapsed_states.py", "--verbose"]],
    },
    {
        "name": "provenance-consumer-guard",
        "when": {"regex": r"\.py$"},
        "steps": [["python3", "scripts/check_provenance_consumers.py", "--verbose"]],
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

    changed = [] if args.all else changed_files(args.base_ref, args.event_name)
    harness_touched = any(f in HARNESS_PATHS for f in changed)
    force_all = args.all or harness_touched

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
    print("=" * 72)

    if args.notify_file and notify:
        Path(args.notify_file).write_text("\n".join(notify) + "\n", encoding="utf-8")

    if failures:
        print("\nFAILING GUARDS — every one is listed here, so one run is enough:")
        for name, reason in failures:
            print(f"  ::error::{name}: {reason}")
        return 1

    print("\nAll relevant guards passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
