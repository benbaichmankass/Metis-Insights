"""Every `commit-to-main` caller must confirm its rows LANDED, and must budget
enough time to wait for that.

WHY THIS IS GENERIC AND NOT ELEVEN ONE-LINE OPT-INS
(`BL-20260830-TWELVE-OF-THIRTEEN-COMMIT-TO-MAIN-CALLERS-NEVER-CHECK-THEIR-ROWS-LANDED`).

The row's own framing was "each is a one-line opt-in PLUS a `timeout-minutes`
check". Doing exactly that would have left the real defect in place, and the
proof is that it had ALREADY bitten by the time this file was written:

`gld-compat-matrix` shipped `timeout-minutes: 30` when the action's wait was 18
— a genuine 12-minute margin. The wait default was then widened 18 -> 30 in a
later change, and `30 > 30` is no margin at all: that job would be killed at
exactly the wait's deadline and report a timeout for a merge that was about to
land, which is verbatim the failure the backlog row warns about. Nobody edited
gld-compat-matrix. Widening a SHARED DEFAULT silently consumed every caller's
margin at once, and the change's author checked only the caller being edited.

So the invariant belongs here, where one edit to the action is checked against
ALL of its callers, rather than in eleven places that each looked fine.
"""
from __future__ import annotations

import glob
import re
import os

import yaml

ACTION = ".github/actions/commit-to-main/action.yml"
USES = "./.github/actions/commit-to-main"

#: Minimum slack between a caller's budget and the wait, so a caller has room to
#: do its actual WORK. `budget > wait` alone is not enough: a job whose budget
#: merely exceeds the wait has zero time left for the thing it exists to do.
#:
#: ⚠️ THIS IS A NECESSARY CONDITION, NOT A SUFFICIENT ONE, and reading a pass
#: here as "every budget is adequate" is exactly the over-trust this repo keeps
#: paying for. The invariant that actually matters is `budget > work + wait`,
#: and `work` is not knowable from the YAML — so a job doing 40 minutes of work
#: under a 45-minute budget passes this test and still dies mid-wait.
#: `econ-calendar-survey-backfill` was precisely that case: 45 cleared this bar
#: and was still raised to 80, because its 45 had been sized for the WORK before
#: any wait existed. When adding a caller, size the budget as work + wait +
#: slack; this test only catches the floor.
MIN_SLACK_MIN = 5

#: GitHub's own default when a job declares no `timeout-minutes`.
GITHUB_DEFAULT_TIMEOUT_MIN = 360


def _wait_minutes() -> int:
    d = yaml.safe_load(open(ACTION).read())
    return int(d["inputs"]["verify-timeout-minutes"]["default"])


def _caller_jobs():
    """(workflow basename, job name, job dict) for every commit-to-main caller."""
    for f in sorted(glob.glob(".github/workflows/*.yml")):
        text = open(f).read()
        if f"uses: {USES}" not in text:
            continue
        doc = yaml.safe_load(text)
        for job_name, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            if any(s.get("uses") == USES for s in steps):
                yield os.path.basename(f), job_name, job


def test_there_are_callers_to_check():
    """A positive control on the FINDER. Every assertion below iterates this
    generator, so if the `uses:` string were renamed they would all pass
    vacuously over an empty list — green while checking nothing, which is the
    exact shape this whole backlog row is about."""
    found = list(_caller_jobs())
    assert len(found) >= 10, (
        f"only {len(found)} commit-to-main callers found — the detector is "
        f"probably broken, not the repo suddenly clean"
    )


def test_every_caller_verifies_its_rows_merged():
    """`verify-merged` absent means the step exits 0 when the PR OPENS, so a
    push that never merges reads as a success and the rows are unreadable."""
    missing = []
    for wf, job_name, job in _caller_jobs():
        for s in job["steps"]:
            if s.get("uses") != USES:
                continue
            if str((s.get("with") or {}).get("verify-merged", "")).lower() != "true":
                missing.append(f"{wf}:{job_name}")
    assert not missing, (
        "these callers land rows and never confirm they arrived: "
        + ", ".join(missing)
    )


def test_every_caller_budget_outlasts_the_merge_wait():
    """THE ONE THAT CATCHES A WIDENED DEFAULT.

    Read from the action rather than hardcoded, so raising
    `verify-timeout-minutes` fails here instead of silently killing callers
    mid-wait.
    """
    wait = _wait_minutes()
    too_tight = []
    for wf, job_name, job in _caller_jobs():
        budget = job.get("timeout-minutes", GITHUB_DEFAULT_TIMEOUT_MIN)
        if int(budget) < wait + MIN_SLACK_MIN:
            too_tight.append(f"{wf}:{job_name} budget={budget}m wait={wait}m")
    assert not too_tight, (
        f"budget must be >= wait + {MIN_SLACK_MIN}m of slack for the job's own "
        f"work; these would be killed mid-wait and report a timeout for a merge "
        f"that was about to land: " + "; ".join(too_tight)
    )


def test_no_caller_overrides_the_wait_with_its_own_timeout():
    """The action owns the wait. A caller passing its own
    `verify-timeout-minutes` re-creates the per-caller drift this file exists to
    prevent — and would not be re-checked when the shared default moves."""
    overriders = []
    for wf, job_name, job in _caller_jobs():
        for s in job["steps"]:
            if s.get("uses") == USES and "verify-timeout-minutes" in (s.get("with") or {}):
                overriders.append(f"{wf}:{job_name}")
    assert not overriders, (
        "these callers override the shared wait: " + ", ".join(overriders)
    )


# ─────────────────────────────────────────────────────────────────────────────
# R13: ARMING TAKES THE MERGE SLOT — and this action arms for every producer.
#
# `MI-208`. The action wrote the landing declaration (R11) and the arming file
# (R6) and stopped there, so every PR it opened failed `pr-landing-guard` R13 —
# a REQUIRED check — and could never merge. Measured 2026-09-09: 47 open PRs, 46
# automation, PR #11487's guards job reading `PASS 53 · FAIL 1` on this guard.
#
# ⚠️ THE BLAST RADIUS IS WHY THE TEST IS HERE AND NOT IN A CALLER. The open
# queue showed ELEVEN distinct `automation/*` prefixes, which reads like eleven
# bugs; they are `branch-prefix` inputs to this ONE action, which 27 workflows
# call. And the damage was not confined to the queue: an unlanded receipt makes
# `check_digest_liveness` grade `stale`, and that guard is time-based and
# repo-wide, so it then reds EVERY open PR — trading PRs included.
# ─────────────────────────────────────────────────────────────────────────────

SESSION_BOARD = "docs/claude/session-board.json"
BRANCH_SLOT_DIR = ".github/merge-slots"
CLAIM_SCRIPT = "scripts/ops/claim_merge_slot.py"


def _action_script() -> str:
    with open(ACTION, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    steps = doc["runs"]["steps"]
    return "\n".join(s.get("run", "") for s in steps)


def test_the_action_arms_the_landing_route():
    """A positive control. Every assertion below is of the form 'because it
    arms, it must also X' — so if it ever STOPS arming, those become vacuous
    truths and this file would pass while checking nothing."""
    assert ".github/pr-automerge-requests/" in _action_script(), (
        "the action no longer writes an arming file — the R13 assertions below "
        "have quietly become vacuous; re-derive what this action does.")


def test_the_action_writes_the_merge_slot_claim():
    script = _action_script()
    assert CLAIM_SCRIPT in script, (
        f"the action arms auto-merge but never writes the R13 merge-slot claim. "
        f"Arming IS the merge, so `pr-landing-guard` fails every PR this action "
        f"opens and none of them can ever merge. Call {CLAIM_SCRIPT}.")


def test_the_claim_is_staged_in_the_same_commit_as_the_arming_file():
    """R13 reads the claim out of the branch's OWN DIFF (`_added_or_modified`
    against the base), so a claim written to the working tree but never `git
    add`ed satisfies nothing — and would fail in exactly the way that looks
    like the fix is present."""
    script = _action_script()
    add_block = script.split('git add -- ".github/pr-landing/')[-1].split("git commit")[0]
    # ⚠️ THIS ASSERTED `SESSION_BOARD in add_block` UNTIL 2026-09-12, WHICH PINNED
    #    THE ROUTE RATHER THAN THE INVARIANT. R13 accepts two routes and the
    #    action moved to the per-branch one, so naming the shared file made a
    #    correct action fail. The invariant is unchanged and is what is asserted
    #    now: whatever route the claim takes, it is STAGED in the same commit as
    #    the arming file, because R13 reads the claim out of the branch's own
    #    diff. Accepting EITHER route is not a weakening — staging NEITHER still
    #    fails, which is the only thing this test ever protected.
    assert (BRANCH_SLOT_DIR in add_block) or (SESSION_BOARD in add_block), (
        f"no R13 claim is staged alongside the arming file — neither "
        f"{BRANCH_SLOT_DIR} (the per-branch route) nor {SESSION_BOARD} (the "
        f"legacy shared field). R13 grades the branch's own diff; an unstaged "
        f"claim is not a claim.")


def test_a_missing_claim_script_refuses_rather_than_opening_a_doomed_pr():
    """The failure mode this whole work item is about: a mechanism reporting
    success for something it did not achieve. If the script is absent the action
    must FAIL, not open a PR that can never merge."""
    script = _action_script()
    assert f"! -f {CLAIM_SCRIPT}" in script, (
        "the action does not check that the claim script exists; without that "
        "check a missing script silently re-creates the landing deadlock.")


def test_the_slot_conflict_is_resolved_not_aborted():
    """R13 has every arming branch rewrite the SAME four lines, and R13 does not
    serialize — so two automation runs in flight collide by construction
    (verified: `CONFLICT (content): Merge conflict in session-board.json`).
    Aborting there would trade the deadlock for a permanent conflict-strand."""
    script = _action_script()
    assert "refreshed_after_slot_conflict" in script, (
        "the stale-branch refresh still aborts on a merge-slot conflict; that "
        "strands the branch permanently on any overlapping pair of runs.")
    assert "--theirs" in script, (
        "a slot conflict must be resolved by taking MAIN's board and "
        "re-asserting our own claim over it, so another branch's claim and any "
        "`active_sessions` edits survive.")


def test_every_caller_checks_out_with_a_pat():
    """A caller that checks out with the default token opens a PR NOTHING CHECKS.

    ⚠️ THIS FAILS SILENTLY AND FOREVER, which is why it belongs beside
    `verify-merged` rather than in a reviewer's head. `commit-to-main` pushes its
    branch with whatever credential the checkout configured, and GitHub does not
    fire workflow triggers for actions taken with the built-in `GITHUB_TOKEN`
    (recursion prevention). So a caller checking out without a PAT opens a PR
    that never receives the `on: pull_request` required checks — and auto-merge
    then waits for checks that will never arrive. The action's own docstring
    states the PAT as a REQUIREMENT of calling it.

    The two failure shapes are different and both are bad: WITHOUT
    `verify-merged` the step exits 0 the moment the PR opens, so the run is
    green and the artifact never lands; WITH it, the job burns its whole merge
    wait and reports a timeout. Neither names the cause.

    MEASURED 2026-09-12: `sunset-pass.yml` was being ported onto
    `commit-to-main` and checked out with a bare `actions/checkout@v4`. The
    existing suite passed on it — every other invariant here is about the
    `uses:` step, and this one is about a step three above it — so the port
    would have swapped a loud weekly failure (a protected-branch push declined
    with GH006, which at least errors) for a silent permanent one.

    ⚠️ IT CHECKS THE BINDING, NOT THE SECRET. A `token:` naming a secret that is
    unset, or one whose PAT lacks the scopes, passes here and fails on the
    runner. That is stated rather than implied: this catches the omission an
    author makes, never a credential problem.
    """
    missing = []
    for wf, job_name, job in _caller_jobs():
        checkouts = [s for s in job["steps"]
                     if str(s.get("uses", "")).startswith("actions/checkout@")]
        if not checkouts:
            # No checkout at all in the job that calls the action — the action
            # could not run. Reported rather than skipped: a silent pass here
            # would be a verdict over an empty population.
            missing.append(f"{wf}:{job_name} (no actions/checkout step at all)")
            continue
        if not any("token" in (s.get("with") or {}) for s in checkouts):
            missing.append(f"{wf}:{job_name}")
    assert not missing, (
        "these callers check out with the default GITHUB_TOKEN, so the PR "
        "`commit-to-main` opens for them never receives the required checks and "
        "auto-merge waits forever: " + ", ".join(missing)
    )


#: Which file each `scripts/ops/<tool>.py --write` invocation MUTATES.
#: ⚠️ IT IS A DECLARED REGISTRY AND AN UNREGISTERED TOOL FAILS, never passes.
#: A tool invoked with `--write` whose output nobody declared is exactly the
#: case this test exists to catch — silence there would be "we did not look"
#: wearing a green tick. Same shape as `check_collapsed_states.CONTRACTS`:
#: registering a tool here is how its output becomes enforced.
WRITE_OUTPUTS = {
    "constraint_readout.py": ["docs/claude/CONSTRAINT.json", "docs/claude/READOUT.md"],
    "checklist_routing_age.py": ["docs/claude/work/CHECKLIST-ROUTING-AGE.json"],
    "render_session_brief.py": ["CLAUDE.md"],
    "render_due_list.py": ["docs/claude/DUE.json", "docs/claude/DUE.md"],
    "run_probes.py": ["docs/claude/PROBES.json"],
    "document_index.py": ["docs/DOCUMENT-INDEX.md"],
    "error_feed_digest.py": ["docs/claude/ERROR-FEED-DIGEST.json"],
    "stuck_automation_branches.py": ["docs/claude/STUCK-BRANCHES.json"],
    # A DIRECTORY, and `commit-to-main` takes it as one path. Declared as the
    # directory rather than a file so the check asks the question the workflow
    # actually answers; naming a file inside it would fail on a correct config.
    "sunset_pass.py": ["comms/sunset"],
}

def audit_write_outputs(where: str, committed: set, tools, registry) -> tuple:
    """PURE. `(missing, unregistered)` for one job's committed paths.

    ⚠️ IT IS A SEPARATE FUNCTION BECAUSE A PLANTED DEFECT PROVED IT HAD TO BE.
    While the rule was inlined in the test, deleting the line that records an
    UNREGISTERED tool left the whole suite green — every tool in the repo is
    registered today, so there was nothing unregistered for the deletion to
    skip, and the branch had no control on it at all. Hoisting it lets a test
    feed it a tool that is not in the registry and assert it refuses. The same
    reasoning `verdict_of` carries in `check_register_field_loss`.
    """
    missing, unregistered = [], []
    for tool in sorted(tools):
        if tool not in registry:
            unregistered.append(f"{where} runs {tool} --write")
            continue
        for out in registry[tool]:
            if out not in committed:
                missing.append(f"{where} writes {out} ({tool}) and does not commit it")
    return missing, unregistered


_WRITE_CALL = re.compile(r"scripts/ops/([A-Za-z0-9_]+\.py)\b[^\n]*--write")


def _write_tools(job) -> set:
    """Tool basenames this job invokes with `--write`."""
    out = set()
    for s in job.get("steps") or []:
        run = s.get("run")
        if isinstance(run, str):
            out |= set(_WRITE_CALL.findall(run))
    return out


def test_every_written_register_is_in_the_paths_it_is_committed_with():
    """⚠️ A WORKFLOW THAT WRITES A REGISTER AND DOES NOT COMMIT IT IS BROKEN IN
    A WAY THAT LOOKS LIKE SOMETHING ELSE ENTIRELY.

    `constraint-readout.yml` ran `checklist_routing_age.py --write`, rendered
    the CLAUDE.md session brief FROM that mutated register, and committed the
    brief WITHOUT the register. `session-brief-guard` then re-rendered from the
    OLD register, got a different block, and graded `introduced_block_edited`
    -> exit 1. Every scheduled run therefore opened a PR that could not merge,
    waited 30 minutes and failed —
    `BL-20260911-THE-CONSTRAINT-READOUT-CRON-NOW-CLEARS-ITS-SELF-TEST-AND-DIES-ON-SESSION-BRIEF-GUARD-REJECTING-THE-BRIEF-IT-JUST-RENDERED`,
    whose own criterion demands the cause be REPRODUCED before it is fixed.

    Reproduced on both surviving branches: as committed the guard exits 1, and
    with the register present in the tree it exits 0. The register had also
    been landed exactly ONCE in its life, by the PR that created it.

    ⚠️ AN UNREGISTERED `--write` TOOL FAILS THIS TEST. A tool whose output is
    undeclared cannot be checked, and reporting that as a pass is the
    clean-negative this repo has a rule about.
    """
    missing, unregistered = [], []
    for wf, job_name, job in _caller_jobs():
        committed = set()
        for s in job["steps"]:
            if s.get("uses") != USES:
                continue
            committed |= set(str((s.get("with") or {}).get("paths", "")).split())
        m, u = audit_write_outputs(f"{wf}:{job_name}", committed,
                                   _write_tools(job), WRITE_OUTPUTS)
        missing += m
        unregistered += u
    assert not unregistered, (
        "a workflow invokes a --write tool whose output is not declared in "
        "WRITE_OUTPUTS, so this test cannot check it. Declare it rather than "
        "leaving the check silent: " + "; ".join(unregistered))
    assert not missing, (
        "these workflows MUTATE a register and throw the change away, so "
        "anything rendered from it cannot be reproduced by a re-render: "
        + "; ".join(missing))


def test_an_unregistered_write_tool_is_refused_rather_than_skipped():
    """THE CONTROL THE INLINE VERSION DID NOT HAVE. Deleting the `unregistered`
    branch left the suite green, because nothing in the repo is unregistered
    today — so the rule would have silently stopped checking any tool added
    later. A tool whose output nobody declared cannot be checked, and reporting
    that as a pass is the clean-negative this repo has a rule about."""
    missing, unregistered = audit_write_outputs(
        "wf.yml:job", {"docs/a.json"}, {"brand_new_tool.py"}, WRITE_OUTPUTS)
    assert unregistered and "brand_new_tool.py" in unregistered[0]
    assert not missing, "an unregistered tool must not also be reported as missing"


def test_a_written_output_absent_from_paths_is_reported():
    missing, unregistered = audit_write_outputs(
        "wf.yml:job", {"docs/other.json"}, {"t.py"}, {"t.py": ["docs/a.json"]})
    assert not unregistered
    assert missing and "docs/a.json" in missing[0]


def test_a_committed_output_is_silent():
    """The negative control: the rule must not fire on a correct config."""
    missing, unregistered = audit_write_outputs(
        "wf.yml:job", {"docs/a.json"}, {"t.py"}, {"t.py": ["docs/a.json"]})
    assert not missing and not unregistered


def test_the_write_output_finder_is_not_vacuous():
    """The positive control on the FINDER, not on the rule. If the regex stopped
    matching, the test above would iterate an empty set on every workflow and
    pass while checking nothing."""
    seen = set()
    for _wf, _job_name, job in _caller_jobs():
        seen |= _write_tools(job)
    assert len(seen) >= 3, (
        f"only {len(seen)} --write tool(s) found across every commit-to-main "
        f"caller; the finder is probably broken, not the repo suddenly clean")
    assert "checklist_routing_age.py" in seen, (
        "the finder does not see the invocation this test was written for")


def test_the_caller_population_is_not_empty():
    """A guard with no population is not a clean guard.

    If `USES` is ever renamed, every test above iterates an empty list and the
    whole file goes green while checking nothing — the vacuous-verdict shape
    this repo enforces against elsewhere.
    """
    assert list(_caller_jobs()), (
        f"no workflow calls `{USES}` — either the action was renamed (fix this "
        f"file) or every producer stopped landing rows (a much larger finding). "
        f"Either way the tests above just passed over NOTHING."
    )


# ──────────────────────────────────────────────────────────────────────────────
# A CALLER THAT LANDS A GENERATED ARTIFACT MUST BE ABLE TO RECOMPUTE IT
# ──────────────────────────────────────────────────────────────────────────────


def generated_paths_in_commit(committed: set, tools, registry) -> set:
    """PURE. The subset of `committed` that this same job GENERATES in-job.

    ⚠️ SEPARATE AND PURE FOR THE REASON `audit_write_outputs` ALREADY RECORDS:
    a rule inlined in a test has no control on its own branches. This one can be
    fed a planted job and asserted to refuse.

    An UNREGISTERED tool contributes nothing here — deliberately, and it is not a
    silent pass: `test_an_unregistered_write_tool_is_refused_rather_than_skipped`
    already fails the build for it. Duplicating that refusal here would report one
    omission twice and say nothing new.
    """
    out = set()
    for tool in tools:
        for produced in registry.get(tool, []):
            for path in committed:
                if path == produced or path.startswith(produced.rstrip("/") + "/"):
                    out.add(path)
    return out


#: Tools whose `--write` lands on a path that is UNIQUE PER RUN, so two runs of
#: the same caller write DISJOINT paths and cannot conflict with each other.
#: Such a caller needs no recompute, and demanding one would be worse than
#: useless: the only recompute available for `sunset_pass.py` is its degraded
#: fallback (`--write` with no `--lifetime-json`, the job's own `else` arm), so
#: wiring it would silently swap a real artifact for a lesser one on conflict.
#:
#: ⚠️ VERIFIED, NOT PRESENCE-ONLY. `test_the_self_disjoint_exemptions_are_real`
#: fails on a name that is not a registered write tool, so the cheapest way past
#: the invariant above is not to invent an entry. Same reasoning as
#: `collapsed-state-guard`'s override: a guard cheaper to lie to than to satisfy
#: is worse than no guard.
#:
#: ⚠️ THE RESIDUAL IS NAMED RATHER THAN HIDDEN: two runs on the SAME UTC date
#: DO share a path and could conflict. `sunset-pass` is a weekly cron
#: (`10 5 * * 1`), so that needs a same-day manual dispatch; if it ever happens
#: the branch strands exactly as before and the remedy is to re-run the cron.
SELF_DISJOINT_OUTPUTS = {
    # MEASURED 2026-09-17: writes `comms/sunset/<UTC-date>/` (its own --write
    # help text), and `comms/sunset/` on disk holds `2026-09-01`, `2026-09-14`.
    "sunset_pass.py": "writes comms/sunset/<UTC-date>/ — a fresh directory per run",
}


def test_the_self_disjoint_exemptions_are_real():
    """⚠️ The exemption above must name a tool that genuinely writes, and whose
    output is genuinely a directory — a per-run-unique FILE would be a different
    claim. An entry that is not a registered write tool is a typo or an
    invention, and it silently switches the invariant off for that caller."""
    for tool, why in SELF_DISJOINT_OUTPUTS.items():
        assert tool in WRITE_OUTPUTS, (
            f"{tool} is exempted from the recompute rule but is not a registered "
            "write tool — nothing verifies what it writes"
        )
        assert why.strip(), f"{tool} is exempted with no stated reason"


def _with_block(job):
    """The `with:` mapping of this job's commit-to-main step."""
    for s in job.get("steps") or []:
        if str(s.get("uses", "")) == USES:
            return s.get("with") or {}
    return {}


def test_every_caller_landing_a_generated_artifact_can_recompute_it():
    """⚠️ A GENERATED ARTIFACT HAS NO SIDE TO TAKE, SO A CONFLICT ON ONE IS NOT
    A DISAGREEMENT — IT IS A RENDERER RACING ITSELF, AND ABORTING STRANDS IT.

    MEASURED 2026-09-17 against `origin/main` 19e59662, over the COMPLETE
    population of 70 open PRs (both API pages), probed with
    `git merge-tree --write-tree` on an unshallowed clone, with BOTH controls
    passing (positive: a whole-file rewrite off an old commit -> conflicted,
    rc=1; negative: main vs main -> clean):

        56 of 70 open PRs (80.0%) merge-conflicted
        44 of those were `automation/**` -- commit-to-main's OWN PRs
        ZERO conflicts anywhere in src/, tests/, scripts/, config/

    and the blocked paths were led by generated files: `DUE.json`/`DUE.md` in 28
    PRs, `ERROR-FEED-DIGEST.json`/`.md` in 23. **23 `error-feed-digest` PRs had
    stacked up since 2026-09-13** -- an hourly run whose every instance
    conflicted with every earlier unmerged instance of ITSELF.

    The action's `refresh-command` is the remedy and it was reachable only when
    the merge SUCCEEDED; a merge that CONFLICTED aborted before it. It was also
    barely used: MEASURED over all 27 call sites, **1** set `refresh-command`
    while **27** set `verify-merged` -- the latter being the positive control
    that this probe can see a real input at all.

    ⚠️ THIS TEST IS THE FLOOR, NOT THE RULE. It asks only whether a recompute is
    DECLARED. It cannot know whether the command actually reproduces the
    artifact -- that is answered by running it, which is what
    `docs/claude/work/REGISTER-CONTENTION-2026-09-17.md` records doing against
    the 33 real stranded branches (30 CONFLICT -> CLEAN, 3 correctly refused).
    """
    missing = []
    for wf, job_name, job in _caller_jobs():
        w = _with_block(job)
        committed = set((w.get("paths") or "").split())
        tools = _write_tools(job)
        gen = generated_paths_in_commit(committed, tools, WRITE_OUTPUTS)
        if gen and tools <= set(SELF_DISJOINT_OUTPUTS):
            continue  # cannot self-conflict — see SELF_DISJOINT_OUTPUTS
        if gen and not str(w.get("refresh-command") or "").strip():
            missing.append(f"{wf}:{job_name} lands {sorted(gen)} which it generates in-job")
    assert not missing, (
        "these callers land an artifact they GENERATE, with no `refresh-command`, "
        "so a conflict on it aborts the stale-branch refresh and strands the "
        "branch permanently instead of recomputing it: " + "; ".join(missing)
    )


def test_a_generated_path_is_detected():
    """POSITIVE CONTROL — the finder sees a generated path."""
    assert generated_paths_in_commit(
        {"docs/claude/DUE.md", "docs/claude/other.md"},
        {"render_due_list.py"},
        WRITE_OUTPUTS,
    ) == {"docs/claude/DUE.md"}


def test_a_directory_output_covers_files_beneath_it():
    """A registry entry naming a DIRECTORY covers paths under it (`sunset_pass.py`)."""
    assert generated_paths_in_commit(
        {"comms/sunset/2026-09-01/INDEX.json"}, {"sunset_pass.py"}, WRITE_OUTPUTS
    ) == {"comms/sunset/2026-09-01/INDEX.json"}


def test_a_hand_authored_path_is_not_treated_as_generated():
    """NEGATIVE CONTROL — a path nothing generates must not demand a recompute."""
    assert generated_paths_in_commit(
        {"docs/claude/health-review-backlog.json"}, {"render_due_list.py"}, WRITE_OUTPUTS
    ) == set()


def test_the_recompute_invariant_actually_checks_callers():
    """⚠️ NON-VACUITY OF THE RULE, WHICH IS NOT THE SAME AS NON-VACUITY OF THE
    FINDER — and the difference was found by mutation, not by reasoning.

    Widening `SELF_DISJOINT_OUTPUTS` to cover every caller left the whole suite
    GREEN: the finder still matched, so the finder's own non-vacuity test passed,
    while the invariant it feeds had been exempted into inertness. That is the
    `a guard cheaper to lie to than to satisfy` shape this repo names, so the
    count of callers ACTUALLY SUBJECT to the rule is asserted here.
    """
    checked = []
    for wf, _jn, job in _caller_jobs():
        w = _with_block(job)
        tools = _write_tools(job)
        gen = generated_paths_in_commit(set((w.get("paths") or "").split()), tools, WRITE_OUTPUTS)
        if gen and not tools <= set(SELF_DISJOINT_OUTPUTS):
            checked.append(wf)
    assert len(checked) >= 3, (
        "the recompute invariant is subject to almost no callers, so it is "
        f"passing vacuously — check SELF_DISJOINT_OUTPUTS has not been widened "
        f"into an off-switch (actually checked: {sorted(checked)})"
    )


def test_the_generated_path_finder_is_not_vacuous():
    """⚠️ NON-VACUITY, because a finder that returns nothing makes the test above
    pass over an empty population — the `green that checked nothing` shape."""
    found = {
        wf
        for wf, _jn, job in _caller_jobs()
        if generated_paths_in_commit(
            set((_with_block(job).get("paths") or "").split()),
            _write_tools(job),
            WRITE_OUTPUTS,
        )
    }
    assert len(found) >= 3, (
        "the generated-path finder matched almost nothing across real callers, so "
        f"the invariant above is passing vacuously (matched: {sorted(found)})"
    )


def test_the_action_recomputes_a_generated_conflict_rather_than_aborting():
    """The action must carry the recompute branch, and its three outcomes must be
    distinguishable. `refreshed_after_generated_conflict` (we recomputed),
    `failed_conflict_rederive` (we tried and could not) and `failed_conflict` (we
    refused, because a conflicted path was outside the caller's declared set) are
    three different facts about the head about to be pushed, and collapsing any
    two reintroduces the strand."""
    s = _action_script()
    for token in (
        "refreshed_after_generated_conflict",
        "failed_conflict_rederive",
        "failed_conflict",
    ):
        assert token in s, f"the action no longer emits `{token}`"
    # ⚠️ PIN THE EXACT LINE, NOT THE TOKEN. A bare `"--theirs" in s` PASSED a
    # mutation that flipped the recompute to `--ours`, because the unrelated
    # session-board block above it also contains `--theirs` — the assertion was
    # satisfied by a different occurrence. `--ours` would keep the BRANCH's
    # stale artifact and discard main's, which is the opposite of the merge.
    assert "git checkout --theirs -- ${CONFLICTED}" in s, (
        "the generated-conflict recompute must take MAIN's side (`--theirs`) for "
        "the conflicted paths before re-deriving over them"
    )
