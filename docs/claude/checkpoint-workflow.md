# Checkpoint workflow

How Claude Code sessions execute the sprint in small, resumable steps.
Read this at the start of **every** session, before touching code.

## Core rules

1. **One task per session.** Do not chain tasks silently.
2. **Start from the latest checkpoint**, not from the top of the sprint plan.
3. **Keep every change PR-sized.** If a task is too large, split it into the
   smallest safe subtask and complete only that subtask.
4. **Stop and hand off if limits are near** — usage limits, context limits,
   or session timeout. Stop at the first safe checkpoint.
5. **Do not wait for a human merge** before starting the next task. The repo
   keeps moving via small checkpoints.
6. **End every session with a handoff note** in the checkpoint log (see below).

## Where state lives

- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — the live, append-only log.
  This is the source of truth for "what is the next thing to do".
- `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` — the template every session
  copies into the log at the end.
- `docs/sprint-plans/sprint-plan-YYYY-MM-DD.md` — the sprint backlog.
  Authoritative for *what* to build, not *where to resume*.

## Resume rule (start of session)

1. `cat docs/claude/checkpoints/CHECKPOINT_LOG.md` — read the most recent entry.
2. If the last checkpoint is **complete**, pick up the `Next checkpoint`
   listed there and start that as the current task.
3. If the last checkpoint is **partial**, continue from the exact partial
   step described in the entry. Do not restart the task.
4. Only consult the sprint plan to expand on the *content* of the next
   checkpoint — never to choose a different task.

## Mandatory stop rule

- If a task is not fully finished by the time you hit a stopping signal
  (limits near, blocker found, ambiguity that needs the PM), do **not**
  silently continue into the next task.
- Stop at the first safe checkpoint:
  - tests passing or skipped with explanation,
  - working tree committed or cleanly stashable,
  - no half-edited file left dangling.
- Then write the handoff entry and exit.

## End of session checklist

1. Run the lightweight checks from `session-workflow.md`.
2. Commit your changes on a focused branch
   (`workflow/<topic>` or `feat/<scope>`), push, open a PR.
   Do **not** wait for the PR to be merged before the next session starts.
3. Append a new entry to `docs/claude/checkpoints/CHECKPOINT_LOG.md`
   using `HANDOFF_TEMPLATE.md`. The entry must contain exactly:
   1. **Completed** — what got done this session.
   2. **Files changed** — list of paths.
   3. **Tests run** — commands + pass/fail.
   4. **Remaining** — what is left for this checkpoint, or "none".
   5. **Next checkpoint** — the very next task the next session should pick up.
4. Send the Telegram session-complete notification (REQUIRED — if you skip
   this step, the entry is incomplete — re-open the session and run it before
   exiting):

   ```bash
   PYTHONPATH=. python scripts/notify_session.py session \
     --checkpoint "<CP-ID>" \
     --summary "<one-line summary>"
   ```

   If env vars `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are not set in the
   session, log it in the checkpoint entry under `Telegram sent: no (no creds)`
   and continue. Do **not** paste secrets to enable it.

   **Alerts:** Whenever you are blocked waiting on Ben (a PR that must be merged,
   a question that can't be resolved without him, or any other blocker), send an
   alert **before** pausing. See the "Alert path" section in
   `docs/claude/session-workflow.md` for the exact command.

5. If this checkpoint completes the **entire sprint**, additionally send
   the sprint-complete notification:

   ```bash
   PYTHONPATH=. python scripts/notify_session.py sprint \
     --sprint "<sprint-id>" \
     --summary "<one-line sprint summary>"
   ```

## Checkpoint ID convention

`CP-<sprint-date>-<NN>` — e.g. `CP-2026-04-28-03` is the third checkpoint
of the 2026-04-28 sprint. Increment monotonically; never reuse an ID.

## Anti-patterns

- ❌ Re-reading the full sprint plan and starting from M1 every session.
- ❌ Bundling two milestones into one PR because "they're related".
- ❌ Skipping the handoff entry because "the diff speaks for itself".
- ❌ Pasting Telegram tokens into the log to make notifications work.
- ❌ Blocking on a human merge before the next checkpoint.
