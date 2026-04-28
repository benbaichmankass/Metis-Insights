# Handoff template

Copy this block into the **top** of `CHECKPOINT_LOG.md` at the end of every
session. Fill in every field. Do not skip sections — write "none" if empty.

---

## CP-YYYY-MM-DD-NN — <short title>

- **Session date:** YYYY-MM-DD
- **Sprint:** sprint-plan-YYYY-MM-DD
- **Current sprint phase:** <e.g. Phase 2 — risk caps>
- **Last completed checkpoint:** CP-YYYY-MM-DD-NN (or "none")
- **Next checkpoint:** **CP-YYYY-MM-DD-NN — <title>** — <one-line description
  of what the next session should pick up first>
- **Blockers:** <none | external dep | waiting on PM | ...>

### 1. Completed
- <bullet list of what got done this session>

### 2. Files changed
- <path/to/file>
- <path/to/file>

### 3. Tests run
- `<command>` — pass/fail
- `<command>` — pass/fail/skipped (reason)

### 4. Remaining
- <what is still left for this checkpoint, or "none">
- If the task was split, name the subtasks left for future checkpoints.

### 5. Next checkpoint
**CP-YYYY-MM-DD-NN** — <concrete first action for the next session>.
List the docs/files the next session should read in order.

**Telegram sent:** yes / no (reason if no, e.g. "no creds in env")
