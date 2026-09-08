✅ **DONE (port half)** — MI-196 · `claude/mi196-due-readout-consolidation`

**#11369 MERGED** `0e03be9d` 11:09:03Z — the due-list's source classes are ported
into the constraint readout as **§5 · Everything else that is due**.

**VERIFIED ON MAIN, not inferred from the merge:** `READOUT.md` carries the §5
heading with all nine sources; `CONSTRAINT.json` carries the `due` block at
`read_state: read` / `completeness: partial` / `due_count: 79` /
`unreadable_sources: [red_crons, unlanded_automation]`. The two unreadable
sources render `—`, never `0`.

⚠️ **It did NOT improve §1.** Still `insufficient_basis` at 49/682 = 7.2%, still
names no stage. The port added ROWS, not assessed `blocked_on` BASIS. Please do
not cite the longer readout as better-evidenced.

---

### 🔔 THREE THINGS OTHER SESSIONS SHOULD READ BEFORE TOUCHING THE DUE LIST

**1. `render_due_list.py` CANNOT be deleted.** The retirement was framed as "two
surfaces". There are **THREE renderers over ONE collector**, and the third
predates all of this: `scripts/ops/render_daily_brief.py:150` imports
`render_due_list` and line 519 calls `_due.build(_due.collect(...))` wholesale.
Its nine `src_*` functions are a **library with two live importers**. Only the
`DUE.md`/`DUE.json` rendering half, `due-list.yml`, the `due-list-guard`
registration and the `duty` skill's pointer are retirable. **14 live consumers**
name it, including `.claude/skills/duty/SKILL.md` and
`docs/CLAUDE-RULES-CANONICAL.md`. Filed:
`BL-20260908-THREE-RENDERERS-OVER-ONE-DUE-COLLECTOR-AND-THE-DAILY-BRIEF-HAS-NO-WORKFLOW-CARRIER`.

**2. The recurrence ledger is NOT a due-list source.** `WO-20260901-PHASE-D`'s
note says it is; the only `RECURRENCE-LEDGER` mention in `render_due_list.py` is
a docstring pointer to its sibling `render_session_brief.py`. Corrected in the
object. Positive control: `PROBES` appears 13× in the same file.

**3. `git branch -r --merged` LIES HERE — it cannot see a squash-merge.** It
misled me twice in one hour: `claude/mi191-r13-slot-release-corollary` and
`claude/mi193-operating-model-measurement` both read "not merged" while both
sit in main's log. This repo squash-merges by default, so the branch tip never
becomes an ancestor. Use the PR state (`search_pull_requests head:<branch>`) or
`git merge-base --is-ancestor`. Filed:
`BL-20260908-GIT-BRANCH-MERGED-CANNOT-SEE-A-SQUASH-MERGE-...`. I put the wrong
answer in a commit message before catching it; the correction is in `f85c7b03`.

---

**Still open:** #11375 (records only — the §5 observation + the finding above).
**Not started, deliberately:** the retirement half. It is unblocked, but the
measurement changed what it is, and it touches the level-1 canonical doc and a
binding skill — that scope goes to a human before a multi-file change is planned
off a premise now shown to be wrong.

**Merge slot:** I hold it for `claude/mi196-port-observed`. Per MI-191's finding
it cannot be released autonomously — displace it, don't wait on me.
