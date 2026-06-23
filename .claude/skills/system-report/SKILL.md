---
name: system-report
description: Back-compat alias for /system-review — the master SYSTEM REVIEW session (the work is the review; the report is its deliverable). Use when the operator says "/system-report", "run the system report", or "give me the daily/weekly/monthly report". Invoke /system-review and follow its SKILL.md.
---

# /system-report — alias of /system-review

The session was reframed (2026-06-23, operator directive): the **work is the
SYSTEM REVIEW**, and the **system report is just its deliverable**. The artifact
name stays "report" everywhere it's load-bearing (`/api/bot/reports`,
`comms/reports/`, the dashboard + Android Reports tabs), so `/system-report`
remains a valid entry point — but it runs the same session as `/system-review`.

**Do this:** invoke **`/system-review`** and follow
[`.claude/skills/system-review/SKILL.md`](../system-review/SKILL.md) verbatim
(establish the window → run the three reviews incl. mandatory grading →
strategy-promotion / ML-training / soak coverage with the review-coverage guard →
diagnose + propose/fix → assemble → render the report → one consolidated ping).
There is no separate procedure here; this file exists only so the old command
still resolves.
