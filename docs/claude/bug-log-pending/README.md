# Bug-log pending entries

A staging area for bug-log rows that couldn't be folded directly into
`docs/claude/bug-log.md` from the session that filed them — typically
because the canonical bug-log file is too large (≈ 100 KB and growing)
to round-trip through the GitHub MCP `create_or_update_file` API in a
single tool-call payload from a remote-only Claude session.

Each pending entry lives as one file: `BUG-NNN.md`, containing the
full row in the bug-log.md table format plus a one-line
fold-in instruction at the top.

## Why this exists

Filed at the close of S-067 (sprint summary:
`docs/sprint-summaries/sprint-067-summary.md`). The CP-5 close PR
(#647) staged a comprehensive BUG-065 entry but couldn't push it into
`bug-log.md` because the 100 KB file exceeds the round-trip capacity
of a single MCP tool call. Rather than:

* (a) defer indefinitely (the row would have rotted in `/tmp` and
  vanished at session end), or
* (b) trim the row to fit (BUG-055 is one line, but the trimmed shape
  loses the architectural-lesson + cross-references that future
  sessions need), or
* (c) split `bug-log.md` itself (a refactor of the canonical doc that
  needs operator buy-in),

we stage the full row here as a standalone file. The next session with
local clone access folds it in (one-line `cat` append + delete the
staging file).

## Discoverability

`grep -ri "BUG-065" docs/claude/bug-log*` finds both forms:

* the canonical row (once it lands in `bug-log.md`), and
* the staged file (until then).

Keep the file name format `BUG-NNN.md` so the grep hit clearly
identifies the entry by ID.

## Fold-in workflow (for the next session that picks this up)

For each `BUG-NNN.md` in this directory:

1. Open `docs/claude/bug-log.md`. Insert the row from the staged file
   immediately after the table header (line 41 today; check first —
   newer pending entries land above older ones).
2. Verify the column count matches the table header
   (`| ID | Date | Sprint | Area | Symptom | Root cause | Fix (PR) | Concern | Notes |`,
   nine columns separated by `|`).
3. Run `grep -F "BUG-NNN" docs/claude/bug-log.md | wc -l` — should
   return at least 1 (the canonical row).
4. Delete the staged file: `git rm docs/claude/bug-log-pending/BUG-NNN.md`.
5. Commit as a single PR titled
   `docs(bug-log): fold-in BUG-NNN from pending`. Tier 1 / docs-only
   per `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers (canonical
   authority since 2026-05-10; the legacy
   `docs/claude/workplan.md` § Decision and merge authority is
   preserved as historical context).
6. If multiple pending entries are folded in the same session, do them
   in one PR and prefix the title with the count
   (`docs(bug-log): fold-in BUG-065 + BUG-066 from pending`).

## When to use this convention vs. just appending to bug-log.md

* **Local clone session:** always append directly to `bug-log.md`.
  This staging convention exists only as a fallback for remote-only
  sessions that hit the file-size limit.
* **Remote MCP session, file fits:** always append directly. If the
  file is at any size where a single tool call can carry the full
  content, do it inline.
* **Remote MCP session, file exceeds capacity:** stage here. Document
  the deferral in the closing checkpoint and the next-session prompt
  so the fold-in doesn't get lost.

If this convention starts seeing more than ≈3 staged entries at any
time, that's a signal that `bug-log.md` is overdue for a structural
refactor (e.g. split by year or by milestone). Filed as a future
follow-up sprint at that point.
