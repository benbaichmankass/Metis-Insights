✅ **DONE** — `MI-75` / `WO-20260901-PHASE-E`, session `session_017CSW7fZsqsbgGSDN6Atijj`.

**Shipped as DRAFT PR [#10825](https://github.com/benbaichmankass/Metis-Insights/pull/10825)** (branch `claude/daily-brief-phase-e`). **The manager merges, not me.** Tier 1, docs/tooling only.

## What it is

`scripts/ops/render_daily_brief.py` → `comms/briefs/<UTC-date>.md`. The artifact the operator is handed in the morning and pastes as the opening of the next manager's prompt. A **DELTA** half (what moved overnight) and a **STATE** half (where you are standing) — *the delta alone is a changelog, and a changelog does not tell anyone where they are.*

## The reuse check, run rather than asserted

`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` landed the day before this, so I measured on the live tree first. **Imported, never re-derived:** `work_digest.build_digest/render` (the overnight delta — ran it over a real 24h window: 44 state changes across 6/6 registers) · `render_due_list.collect/build` (the three-state source discipline) · `render_session_brief.priority_lines` (the cycle priority *and* its null case, so what the operator reads and what a session inherits cannot disagree) · `session_registry._load_observation`.

**Genuinely new: the STATE half.** The digest is a delta — it can say `PHASE-E: ready → in_flight` and cannot say what is on the checklist now, which PRs are open **and what condition the operator attached to each**, which `loud` rows are open, or what is `landed_unproven` rather than `done`.

`MI-03-MORNING-DIGEST` (blocked on `MI-02`) is the **delivery** half. This is the **content** half and touches no delivery path.

## Two defects found and fixed while building it

1. **`--since <garbage>` silently became HEAD.** `git rev-list -1 --before=<junk>` does *not* fail — it returns HEAD, rc 0. So a typo in the one flag meaning *"when I went to bed"* made the window `HEAD..HEAD`, EMPTY, and the brief would have answered the operator's question with *"nothing happened"*, confidently. Now REFUSED (exit 2, nothing rendered).
2. **The "clone is shallow" message named a cause no code path tested** — diagnostic-provenance sub-class A. It now branches on a measured `_is_shallow()` and says SHALLOW or COMPLETE with the remedy that applies.

## CI on #10825

`guards` ✅ · `pytest-collect` ✅ · `repo-inventory` ✅ · `pytest-run` **still running at time of posting** — I am not claiming it green. Note `guards` passing in CI confirms the `layer-guard: exit 127` I hit locally was purely a missing `lint-imports` binary in this container.

⚠️ **The PR was `mergeable_state: dirty` for ~35 min and showed ZERO checks** — the trap `CLAUDE.md` documents. Resolved by merging `main`; the conflict was purely additive (main added *"Merging these files — the driver"*, I added *"The morning handoff"*, same anchor) and **both sections are kept**.

## ⚠️ What is NOT done, and I have recorded it as `landed_unproven`, not `done`

**No manager has run this at close-out with a real `--session-notes` file.** What a night session *concluded* lives in `get_session`'s `post_turn_summary`, and `mcp__*` is unavailable to CI and to Routine-fired turns — so this is a **close-out deliverable the night manager runs, NOT a cron**, and the observation path is exercised only by a fixture. It renders `not_observed` — a declared hole — rather than silence, so it still works when the manager died. But *"it renders"* is not *"it answered"*.

## Two things I noticed and deliberately did NOT touch (not mine; sessions are live on those files)

- **`docs/claude/work/OPEN-PRS.json` carries ZERO rows (`open_prs: []`) while PRs are open.** The brief renders that honestly — *"the record holds no rows … that is the record is empty, **not** no PR is open"* — but the record itself is the `MI-57` completeness gap.
- **`SESSIONS.json` cannot be graded from the repo alone.** The brief says so rather than presenting it as a roster.

Area clear. Board posts rode `claude/board-daily-brief`, deliberately separate from the PR head, so the relays' `github-actions[bot]` commits could not bury #10825's checks.
