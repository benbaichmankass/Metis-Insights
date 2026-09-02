▶️ **START** — sub-session of manager `session_011JWFxuYAaEQKCFCmG6gnHJ`, under `WO-20260901-PHASE-E`.

**Scope:** the DAILY BRIEF — a *generated* artifact answering the operator's stated criterion: *"what was done overnight and what was wrapped up after I went to bed, so that I know where I'm starting off from."* Tier 1, docs/tooling only.

**Files I expect to touch** (branch `claude/daily-brief-phase-e`; this board post rides a separate branch `claude/board-daily-brief` so the relay's bot commit cannot bury my PR's checks):
- `scripts/ops/render_daily_brief.py` — new
- `tests/test_render_daily_brief.py` — new
- `scripts/ci/run_guards.py` — one guard entry appended
- `docs/claude/work/objects/WO-20260901-PHASE-E.yaml` — an evidence note only
- `docs/claude/work/README.md` — one section

**NOT touching:** any order path, `src/`, `config/`, strategy config, risk caps, the three review backlogs, `OPEN-ITEMS.json`, `MANAGER-CHECKLIST.json`, `SESSIONS.json`, `OPEN-PRS.json`, `MANAGER-LEASE.json`. No VM action, no workflow dispatch, no deploy, no merge.

**Measured before writing anything** — the `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` check, run rather than asserted:
- `scripts/ops/work_digest.py` **already renders the overnight DELTA** (register events, work-store lifecycle transitions, standing wedges, three-state source reads). I ran it over a real 24h window and it produced 44 state changes across 6/6 registers. I am **importing** it, not re-deriving it.
- `scripts/ops/render_due_list.py` **already owns the three-state source discipline** (`read` / `could_not_read` / `not_applicable`, with a `partial` verdict that must name the source). I am **importing** its collector.
- `MI-03-MORNING-DIGEST` already exists and is `blocked` on `MI-02-CLAUDE-CHANNEL` — that is the **delivery** half. This is the **content** half and does not touch delivery.

**What is genuinely absent, and is what I am building:** the STATE half. The digest is a *delta*; it can say `PHASE-E: ready → in_flight` and cannot say what is on the checklist now, which sessions are live, which PRs are open **and what condition the operator attached to each**, which `loud` open-items are open, whether the lease is held, or what is `landed_unproven` rather than `done`. *"Where I'm starting off from"* is that, and nothing produces it.

⚠️ One thing I will state plainly in the PR rather than fake around: **what a night session CONCLUDED lives in `get_session`'s `post_turn_summary`, and `mcp__*` tools are unavailable to CI and to Routine-fired turns.** So that half is an *input the night manager supplies at close-out*, and its absence renders as **"not observed"**, never as "nothing was concluded".
