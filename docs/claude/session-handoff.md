# Session handoff + continue-work routine

## What this is

A bounded routine for continuing **a single pre-planned sprint** across
more than one Claude Code session. When the active session can't
comfortably finish the sprint in one window — context window getting
tight, session has run too long, state has fragmented, blocked on input,
or simply at a clean checkpoint — Claude:

1. stops at the checkpoint,
2. writes a structured handoff artifact into the repo,
3. commits + pushes,
4. triggers a GitHub Actions workflow,
5. exits.

The next session pulls the branch, reads the artifact, and resumes the
same sprint from there.

## What this is **not**

- Not a perpetual roadmap runner.
- Not a self-directed planner that picks new work.
- Not a substitute for the human-readable
  `docs/claude/checkpoints/CHECKPOINT_LOG.md` narrative — that log
  still gets a normal entry at session end. The handoff JSON is the
  **machine-readable** counterpart that automation can act on.

The routine ends when the sprint ends. Set
`ready_for_continue: false` in the handoff and the workflow will
refuse to dispatch.

## When Claude should use it

Trigger the handoff flow at the **first safe checkpoint** after any of:

| Signal | `handoff_reason` value |
|---|---|
| Approaching context-window limit | `context_limit_near` |
| Session has run too long to manage cleanly | `session_too_long` |
| State has fragmented (many edits, hard to summarise mentally) | `fragmented_state` |
| Blocked on operator / external input | `blocked_on_input` |
| Reached a natural break point | `natural_checkpoint` |
| Operator asked Claude to wrap up | `operator_requested` |
| Anything else (requires a non-empty `handoff_reason_note`) | `other` |

Claude must **not** keep working past a hand-off signal. Stop, hand
off, exit.

## Files this routine writes

| Path | Role |
|---|---|
| `automation/session_handoff/next_session.json` | The single live handoff for the active sprint |
| `automation/session_handoff/schema/handoff.schema.json` | JSON Schema (v1) the validator enforces |
| `automation/session_handoff/examples/example_handoff.json` | Canonical happy-path example |
| `automation/session_handoff/README.md` | In-tree TL;DR for the area |
| `scripts/session_handoff/close_session.py` | Helper Claude runs at session end |
| `scripts/session_handoff/validate_handoff.py` | Validator (used by helper, workflow, and tests) |
| `.github/workflows/continue-work.yml` | The continue-work workflow |

The handoff file accumulates an **append-only** `history[]` so every
update and every workflow dispatch leaves an audit trail in git.

## How Claude invokes the flow at session end

The minimum sequence is one helper invocation. The helper validates
the handoff, commits, pushes, and dispatches the workflow:

```bash
python scripts/session_handoff/close_session.py \
    --sprint-id S-061-session-handoff \
    --reason context_limit_near \
    --append-completed "Wired up handoff schema" \
    --append-completed "Added continue-work workflow" \
    --append-next-action "Implement helper script tests" \
    --commit --push --dispatch
```

For a dry run that only validates the file (no git, no dispatch):

```bash
python scripts/session_handoff/close_session.py --validate-only
```

The helper is idempotent: re-running with no edit flags is a safe
no-op. The validator is invoked both before commit and inside the
workflow run, so a bad handoff can never make it onto the branch.

## How the continue-work workflow is triggered

Two equivalent paths:

1. **Automatic, from a finishing session** — `close_session.py
   --dispatch` calls `gh workflow run continue-work.yml --ref <branch>
   -f sprint_id=… -f handoff_file=… -f branch=…`. If `gh` is not on
   the PATH, the helper prints the exact `repository_dispatch` payload
   so it can be sent manually.
2. **Manual operator click** — open the repository's **Actions** tab,
   pick **continue-work**, click **Run workflow**, fill in `sprint_id`
   (and optionally `handoff_file` / `branch`), submit.

The workflow:

- checks out the requested branch,
- validates the handoff file with `--require-ready` and
  `--expect-sprint-id`,
- prints the checkpoint summary, next actions, blocked items, and the
  continuation prompt to the run summary so the next session (or the
  operator) sees them at a glance,
- appends a `continue_dispatched` event to the handoff `history[]`,
  commits the update back to the branch, and uploads the handoff file
  as a build artifact for 30-day retention.

## Resuming a session

The next Claude Code session, when it starts on the handoff branch:

1. reads `automation/session_handoff/next_session.json`,
2. acts on the first item under `next_actions`,
3. respects everything in `guardrails`,
4. stops at the next safe checkpoint (and either repeats this routine
   or, if the sprint is now finished, writes
   `ready_for_continue: false` and a `sprint_closed` history event).

The `continuation_prompt` field is the briefing the resuming session
should be primed with. Keep it self-contained but don't restate the
full sprint plan — point at the plan instead.

## Manual restart / recovery

If a continue-work run dispatches but fails (transient runner error,
push race, etc.):

1. Inspect the run page for the failure mode.
2. Fix the underlying cause (usually nothing — it's typically a
   network blip).
3. Re-run via the Actions UI: **Run workflow** with the same
   `sprint_id`. The workflow's `concurrency: continue-work-<sprint>`
   group serialises runs, so two won't race on the history commit.

If the handoff file itself is wrong (malformed, missing field,
`ready_for_continue: false`), the workflow exits non-zero **before**
committing anything. Edit the file locally, run
`python scripts/session_handoff/close_session.py --validate-only` to
confirm, then commit and dispatch again.

## Manual GitHub setup

The workflow needs:

- **Permissions** — already declared in the workflow:
  `contents: write` (so the runner can append history events back to
  the branch). No extra repo-level configuration required if the
  default `GITHUB_TOKEN` is allowed to write — verify under
  **Settings → Actions → General → Workflow permissions**.
- **No new secrets.** This workflow does not talk to the VM and does
  not need `VM_SSH_KEY` or `DIAG_READ_TOKEN`.
- **No new labels.** Unlike the diag relay, this workflow is
  dispatch-only — no `issues.opened` filter.

If a session's `--dispatch` path is ever used from a host without
`gh`, the helper prints the `repository_dispatch` JSON payload; an
operator can POST it with a personal-access token, or just click
**Run workflow** in the Actions UI with the same inputs.

## Validation

Tests live in `tests/test_session_handoff_validate.py` and
`tests/test_session_handoff_close_helper.py`. They cover:

- malformed JSON,
- missing required fields,
- wrong `schema_version`,
- `ready_for_continue: false` rejection under `--require-ready`,
- `sprint_id` mismatch,
- `handoff_reason: other` without a note,
- happy-path round-trip on the example artifact,
- helper idempotency (no-op re-runs),
- `--validate-only` short-circuit,
- repo-containment guard for commit/push/dispatch.

`tests/test_workflow_yaml_valid.py` already parses every workflow
file in `.github/workflows/`, so the new `continue-work.yml` is
shape-checked alongside the rest of CI.

## Out of scope

- Mutating live-trading code (`src/runtime/`, `src/units/`) inside a
  handoff sprint.
- Strategy / risk / account-mode parameter changes.
- Spawning new sprints autonomously.
- Anything that requires Claude to run inside the GitHub Actions
  runner — the runner only validates, surfaces, and audits. The
  resuming Claude session runs on the operator's machine or wherever
  Claude Code is hosted, just like every other session.
