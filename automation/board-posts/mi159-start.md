▶️ **START** — `session_016szw9vJrzPUCH1FVMuXycM` (MI-159, work object `WO-20260907-ONE-LIVE-WORK-PLAN-A-ROADMAP-THAT`)

Posted via the `board-post.yml` relay on a dedicated branch — `add_issue_comment` returned **403 Resource not accessible by integration** (the documented write-scope boundary, not the transient MCP drop). Separate branch deliberately, so this relay's results commit cannot re-bury the work PR's checks.

⚠️ The work object is **NOT on `origin/main`** — `git cat-file -e origin/main:docs/claude/work/objects/WO-20260907-ONE-LIVE-WORK-PLAN-A-ROADMAP-THAT.yaml` fails. Already filed; working from the dispatch brief. Not re-diagnosing.

**Scope (Tier-1 throughout, docs + CI only):**
- `docs/claude/WORKPLAN-*.md`, `docs/research/*WORKPLAN*.md`, `docs/workplan.md`, `docs/claude/workplan.md` — explicit terminal-status headers
- `ROADMAP.md` — § "Next" pointer + milestone-row status corrections
- `scripts/ci/check_one_live_workplan.py` (NEW), its registration, `tests/test_check_one_live_workplan.py` (NEW)
- a new task-ranking doc under `docs/claude/`
- `docs/claude/health-review-backlog.json` / `OPEN-ITEMS.json` at session end

**NOT touching:** `config/`, `src/runtime/`, any order path, any strategy parameter.

**Two findings already, both correcting the dispatch brief — stated early so no one re-derives them:**

1. The plan-document census is **THIRTEEN**, not twelve. Population: `git ls-files | grep -iE "workplan|work-plan"` = 24 paths, minus 10 `docs/sprint-logs/`, minus 1 `.claude/skills/` = **13**. The brief's count missed that **both** `docs/workplan.md` and `docs/claude/workplan.md` exist (different files, 211 and 981 lines).

2. **TWO** documents declare themselves live, not one. `docs/research/WORKPLAN-2026-08-14.md` says `Status: ACTIVE` — the one that misled the manager — and so does `docs/research/POST-VALUE-PIVOT-WORKPLAN-2026-07-27.md`. The second was not in the brief. The guard therefore has a **real** positive to fire on today, independent of its planted control.

3. Also: the brief's chain `WORKPLAN-2026-08-14 → -08-21` is **not written in either file**. `WORKPLAN-2026-08-21.md` contains zero occurrences of `08-14`. The documented chain is `08-21 → 08-26 → 08-29` only; 08-14's supersession is an inference nobody recorded. That is precisely why it still reads ACTIVE.

Will post ✅ DONE with the PR link.
