▶️ **START** — MI-76: the monolithic registers re-conflict every sibling PR (`session_015r4U4QhowTzXAmr5KQ6f8L`, parent `WO-20260901-PHASE-C`)

Measuring before designing, per the brief. All figures MEASURED on `origin/main` @ `1b82ab7`.

**The dominant conflict mechanism is not the rows — it is a shared header scalar.** Over adjacent register-touching commit pairs since 2026-08-26, the share where BOTH sides bump the same `updated_at`/`as_of` line: `MANAGER-CHECKLIST.json` **29/39 (74%)**, `OPEN-PRS.json` 8/12 (67%), `SESSIONS.json` 14/23 (61%).

- **PR #10815's entire conflict in `MANAGER-CHECKLIST.json` is one line — `as_of`.** Nothing else.
- `health-review-backlog.json` is 6.2 MB and **91% fully disjoint** (8/91 pairs row-contested). Size is not the problem, confirming the brief's hypothesis.
- ⚠️ **This bears directly on shard-vs-driver: sharding rows one-file-per-row does NOT fix a shared `as_of`** — the container file keeps it and keeps conflicting.

Reporting the full measurement, the choice, and why the alternative lost, on a **draft** PR. I do not merge.
