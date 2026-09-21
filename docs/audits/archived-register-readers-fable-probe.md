# Archived-register readers — independent probe (E11, second pass)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · lane **E11**, measured 2026-09-21 at `53436a1` (report-only; no code changed). `unknown` is the generator's own value for an unreviewed audit: nobody but its author has read it yet.
> **Population of record:** every git-tracked file outside `docs/archive/` at `53436a1`, case-folded, fixed-string search for every basename under `docs/archive/2026-09-21-operating-reset/` plus the ten retired skill names plus the six retired work-store subdirectories.

## Headline

**The retired operating model is still being *read* from four live surfaces, and three of them are user-facing.** In order of cost:

1. **The session-start hooks in `.claude/settings.json`** print, at the top of *every* session, instructions to read the coordination board via `docs/claude/board-pointer.json`, register in `docs/claude/session-board.json`, pull `.claude/skills/delegate-work/SKILL.md`, log findings to the three review backlogs, and run `/system-review`. Every one of those targets is archived. Verified this session: all three SessionStart hooks and the PreToolUse nudge printed exactly that text before my first tool call. This is the purest instance of the class the lane exists to find — a reader that has degraded to "could not check" (`scripts/ci/board_pointer.py --number` exits 4, `could_not_check`) and keeps issuing the advice anyway.
2. **`docs/CLAUDE-RULES-CANONICAL.md`**, the #1 document in the instruction hierarchy, still binds sessions to the `session-coordination` skill, `session-board.json::merge_slot`, the `session-handoff` skill, `research-driver`, the four review backlogs as filing targets, `operator-owed-register.json`, `OPEN-ITEMS.json` soak rows and `RECURRENCE-LEDGER.json` (13 hit lines, listed in Table A). `CLAUDE.md` § "Instruction hierarchy" says the two must agree; on this subject they do not.
3. **The live-money runtime tells the operator to use a retired skill.** `src/runtime/execution_diagnostics.py:353` composes the ORPHAN-TRADE red-flag Telegram ping with "▶️ Initiate a /system-review to reconcile this". `/system-review` is archived. Two tests (`tests/test_trade_pings.py:209`, `tests/test_monitor_reconciler.py:379`) assert that string, so the fix is not a one-liner.
4. **The Workflow page's API (`GET /api/bot/work`, `/checklist`, `/decisions`, `POST /decision`) reads `SESSIONS.json`, `MANAGER-LEASE.json`, `work/objects|intents|steps` and `OPEN-ITEMS.json`.** `manager_status.py` renders "⚠️ SESSIONS.json absent — owner registration could not be checked (shown as '?')" and keeps grading; `_yaml_files()` returns `[]` for the three archived directories, so the work index renders as *empty*, not as *archived*; the `/decision` response tells the operator the answer becomes truth "when a committer writes it into `docs/claude/work/objects/<id>.yaml`", a directory that no longer exists.

Also material, one tier down:

- **Five CI-registered guards silently pass over an empty population.** `accrual_clock.py --all` prints `SKIPPING … not present` for all four backlogs and exits 0; `check_soak_doctrine.py` reports `clean` (its own comment at L138 says absent paths are skipped by design); `check_artifact_caveats.py` reports `clean — every open row naming one is adjudicated` over zero rows; `check_impossibility_claims.py --all` silently drops its three backlog globs and scans only `docs/research`; `check_stated_population.py` and `check_manager_scope.py` keep the archived paths in their watch lists (harmless for the diff-scoped one, a degraded "unattributed" grade for the manager-scope one). Run outputs are quoted in § Positive control. None of these lies; each one measures nothing and says `clean`.
- **Thirteen workflows still read or write an archived path** (a further ten only mention one in a comment). Six are `workflow_dispatch`-only now — their crons were removed, which is the disarm the plan called for (`board-heartbeat`, `constraint-readout`, `error-feed-digest`, `pr-queue-watch`, `scope-overlap-audit`, `work-decision-commit`). Seven remain armed: `merge-claim-audit.yml` (`pull_request_target: closed` — fires on every PR close, reads the pointer, carries "did not look"); `reconcile-open-prs.yml` (`push: main` — fires on every push to `main`, reads and re-commits `docs/claude/work/OPEN-PRS.json`; absence behaviour **not determined**, see § Could not determine); `work-digest.yml` (`push: main`, gated on a `due` output; reads `WORK-DIGEST.json` in a `try/except` and commits it back); `board-post.yml` and `board-rotate.yml` (push-sentinels under `automation/`, read/rewrite `board-pointer.json`); `due-list.yml` and `probes.yml` (`issues: opened` labels — would re-create `DUE.json`/`DUE.md`/`PROBES.json`/`STUCK-BRANCHES.json` on `main` if anyone opens a labelled issue). No resurrection has happened in the five commits since the reset (`git log --since=2026-09-20` over those paths shows only pre-reset refreshes), but the mechanism is intact.
- **Six live skills and three slash commands instruct the session to drain or file into the archived backlogs** (`health-review`, `ml-review`, `performance-review`, `doc-freshness`, `sprint-format`, and `system-report`, whose *only* instruction is "invoke `/system-review` and follow `.claude/skills/system-review/SKILL.md`", a dead link). `check_skills_index.py` reports "every reference resolves" — it does not check that link.

The previous sweep's known miss (`src/web/api/routers/work.py`) is confirmed as a reader (Table A, 12 lines). Both other known positives were found.

## Population

| quantity | value | how measured |
|---|---|---|
| Needle set A — archived file basenames | **282** | `find docs/archive/2026-09-21-operating-reset -type f`, basenames, de-duplicated, minus `SKILL.md` and `README.md` (generic; would match every skill and readme in the repo) |
| Needle set B — retired skill names | **10** | `ls docs/archive/2026-09-21-operating-reset/skills/` |
| Needle set C — retired work-store subdirectories | **6** | `work/objects`, `work/intents`, `work/steps`, `work/receipts`, `work/spawn-prompts`, `work/approvals` (the work store CLAUDE.md names as retired; its subdirectory names are not basenames and would otherwise be missed) |
| Files searched | **8540** | `git ls-files` minus `docs/archive/**` (8,834 tracked files total; 294 under the archive) |
| Directories included | all tracked | `.github/` (3,658 files, of which 3,506 are `pr-landing`, `pr-automerge-requests`, `merge-slots` records), `scripts/`, `src/`, `tests/`, `config/`, `deploy/`, `.claude/`, `docs/` (live), `automation/`, `comms/`, `ml/`, `runtime_logs/`, root files |
| Excluded | `docs/archive/**` and untracked files | the archive is the needle source, not the haystack; untracked files (`.git`, venvs) are not repository content |
| Hit lines (all three needle sets, de-duplicated) | **2931** | |
| Files with ≥1 hit | **1197** | of which **900** are in the historical-record populations (Table C) and **297** are code, CI, config, tests, skills or live docs (Tables A and B) |

**Two archived basenames still exist at their live path** and are therefore *not* degraded readers wherever they are read: `docs/claude/work/REAPER-OBSERVATIONS.json` and `docs/claude/work/TRAINER-CAPTURE-WATCH.json` (both were copied to the archive *and* left in place). Hits on those two are marked in the tables.

**Set B needs context to be a needle at all.** `duty` is an English word; a bare case-folded whole-word grep for it returns 104 lines in 67 files, most of them unrelated (measured). Set B was therefore searched only in skill-reference shapes: `skills/<name>`, `/<name>` (the slash-command form), `<name> skill`, `` `<name>` ``, `"<name>"`, `'<name>'`, `<name>/SKILL`. A retired skill mentioned in plain prose without any of those shapes is **not** in this report.

## Commands (copy-paste; run from the repo root at `53436a1`)

```bash
S=/tmp/e11probe; mkdir -p $S
# 1. Needles, DERIVED from the archive
find docs/archive/2026-09-21-operating-reset -type f | sed 's|.*/||' | sort -u \
  | grep -vxE 'SKILL\.md|README\.md' > $S/needles_basenames.txt          # 282
ls docs/archive/2026-09-21-operating-reset/skills/ > $S/needles_skills.txt  # 10
printf 'work/objects\nwork/intents\nwork/steps\nwork/receipts\nwork/spawn-prompts\nwork/approvals\n' > $S/needles_dirs.txt
# 2. Population: tracked files outside the archive
git ls-files | grep -v '^docs/archive/' > $S/population.txt                 # 8540
# 3. Probe A + C: fixed-string, CASE-INSENSITIVE (-i), with file:line
xargs -a $S/population.txt -d '\n' grep -niHF -f $S/needles_basenames.txt > $S/hits_A.txt   # 2090 lines
xargs -a $S/population.txt -d '\n' grep -niHF -f $S/needles_dirs.txt      > $S/hits_C.txt   # 199 lines
# 4. Probe B: skill names, in skill-reference shapes only, case-insensitive
: > $S/hits_B.txt
while read n; do
  xargs -a $S/population.txt -d '\n' grep -niHE -- \
    "skills/$n\b|/$n\b|\b$n skill|\`$n\`|\"$n\"|'$n'|\b$n/SKILL" | sed "s/^/[$n] /" >> $S/hits_B.txt
done < $S/needles_skills.txt                                                # 642 lines
# 5. Positive control — the probe is broken if any count is 0
for f in .github/workflows/scope-overlap-audit.yml scripts/ops/render_daily_brief.py src/web/api/routers/work.py; do
  printf '%s: ' $f; grep -c "^$f:" $S/hits_A.txt; done
# 6. Which archived basenames still exist live (a reader of these is NOT degraded)
for n in $(cat $S/needles_basenames.txt); do for p in docs/claude/$n docs/claude/work/$n; do [ -e "$p" ] && echo "STILL LIVE: $p"; done; done
# 7. Which hit workflows are still armed (trigger table)
for f in $(grep -oE '^\.github/workflows/[^:]+' $S/hits_A.txt $S/hits_C.txt | sort -u); do
  printf '%s => ' $f; python3 -c "import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); print(list((d.get('on') or d.get(True)).keys()))" $f; done
# 8. Which CI-registered guards name an archived path
grep -oE 'scripts/[a-z_/]+\.py' scripts/ci/run_guards.py | sort -u > $S/guards.txt
grep -hoE '^scripts/[^:]+' $S/hits_A.txt $S/hits_C.txt | sort -u > $S/scripts_hit.txt
comm -12 $S/guards.txt $S/scripts_hit.txt
# 9. Observe what those guards print now
python3 scripts/ops/accrual_clock.py --all; python3 scripts/check_soak_doctrine.py
python3 scripts/check_impossibility_claims.py --all --ratchet; python3 scripts/ci/check_artifact_caveats.py
python3 scripts/ci/board_pointer.py --number; echo "rc=$?"
```

The line-level classifier used to build Tables A/B (a ~60-line Python heuristic: write-signal → WRITER; read/resolve signal or bare path constant in a list → READER; comment/docstring/prose → MENTION; instruction docs with an imperative near the needle → READER) is reproducible from the description but is a heuristic, which is why Table A is hand-verified and Table B is labelled mechanical.

## Positive control

| known positive | found by probe A | lines | hand-verified class |
|---|---|---|---|
| `.github/workflows/scope-overlap-audit.yml` | **yes** | 4 (`board-pointer.json` ×3, `OPEN-ITEMS.json` ×1) | READER (`jq -r .stale_after_hours docs/claude/board-pointer.json` at L166); `workflow_dispatch` only |
| `scripts/ops/render_daily_brief.py` | **yes** | 15 | READER — module constants `_SESSIONS`, `_OPEN_PRS`, `_LEASE`, `_OPEN_ITEMS`, `_CYCLE` at L161–165 name five archived registers; the brief's own text (L737, L779) still tells the reader `SESSIONS.json`/`DUE.md` are where state lives |
| `src/web/api/routers/work.py` (the one the previous sweep missed) | **yes** | 12 (A: 9, C: 3) | READER, user-facing — see Table A |

Nothing was changed to make the controls pass. The probe found all three on the first run.

**Negative control, for the denominator:** the same probe over `docs/archive/` itself (the population it is supposed to exclude) returns hits in 152 of 294 files (`git ls-files docs/archive/2026-09-21-operating-reset | xargs grep -liF -f needles_basenames.txt | wc -l`), so a quiet directory elsewhere is quiet because the needles are absent, not because the probe is.

**Observed guard behaviour** (§ Commands step 9), quoted so nobody has to re-run it to believe it:

```
accrual-clock census: SKIPPING docs/claude/health-review-backlog.json — not present.   (×4, rc=0)
soak-doctrine guard: clean                                                             (rc=0)
impossibility-claim-guard (--all --ratchet): 377 file(s) scanned, 22 standing claim(s)… (rc=0; the 3 backlog globs contribute 0)
artifact-caveat-guard: clean — 1 artifact(s), 7 registered producer(s); every open row naming one is adjudicated.  (rc=0, over 0 rows)
could_not_check — …/docs/claude/board-pointer.json does not exist — the board cannot be resolved   (board_pointer.py, rc=4)
```

## Classification rules as applied

- **READER** — code that opens, walks, `jq`s or `cat`s the archived path, or a module-level constant that a read in the same file resolves; **or** instruction text (a skill, slash command, hook, canonical doc, API payload, Telegram ping) that tells a session or a human to read, edit, drain, register in, or follow the archived thing.
- **WRITER** — a `commit-to-main` `paths:` line, `write_text`/`json.dump`/`git add` on the archived path. Test fixtures that write the *name* under `tmp_path` are **MENTION**, not WRITER: they write a fixture, not the register.
- **MENTION** — comments, docstrings, changelog rows, historical plans, PR/landing records, and the plan-of-record's own description of the retirement.
- **User-facing = yes** when the text is rendered to the operator (SPA payload, Telegram ping, GitHub issue/PR comment, `::error` annotation) **or** is handed to a session as an instruction it is expected to act on (hooks, skills, slash commands, the two canonical docs). Code comments are **no** even when they are wrong.

## Table A — hand-verified hits in executable and instruction surfaces

Every row below was opened and read this session. "Trigger" for workflows is from the file's `on:` block at `53436a1`.

| file:line | needle | class | user-facing | what it does with the archived path |
|---|---|---|---|---|
| `.claude/settings.json:30` | `board-pointer.json`, `session-board.json`, `session-coordination`, `system-review` | READER | **yes** | SessionStart hook; prints "resolve the number (python3 scripts/ci/board_pointer.py --number)… issue_read… register in session-board.json… Protocol: .claude/skills/session-coordination/SKILL.md" to every session. Verified printed this session. |
| `.claude/settings.json:36` | `health-review-backlog.json`, `performance-review-backlog.json`, `ml-review-backlog.json`, `system-review` | READER | **yes** | SessionStart hook; prints "log to docs/claude/health-review-backlog.json" and the three `Backlog:` lines for the review skills, and the `/system-review` master-review paragraph. |
| `.claude/settings.json:42` | `delegate-work`, `session-board.json`, `session-coordination` | READER | **yes** | SessionStart hook; prints "PULL .claude/skills/delegate-work/SKILL.md" and "register in docs/claude/session-board.json (active_sessions)… CLAIM merge_slot". |
| `.claude/settings.json:61` | `board-pointer.json`, `session-board.json` | READER | **yes** | PreToolUse nudge on first Edit/Write/Bash; printed "resolve its number from docs/claude/board-pointer.json… register your session in docs/claude/session-board.json" at my first Bash call. |
| `.claude/settings.json:72` | `board-pointer.json`, `session-coordination` | READER | **yes** | PreToolUse merge-slot guard (denies merge until a board claim marker exists); its own `_comment` says it is inert on Claude Code on the web. |
| `.claude/settings.json:28,40,59,70,81` | `session-coordination` | MENTION | no | `_comment` fields describing the hooks. |
| `.claude/commands/health-review.md:39` | `health-review-backlog.json` | READER | **yes** | Slash-command text: the review "drains `docs/claude/health-review-backlog.json`". |
| `.claude/commands/ml-review.md:41` | `ml-review-backlog.json` | READER | **yes** | Same for the ML review. |
| `.claude/commands/performance-review.md:39` | `performance-review-backlog.json` | READER | **yes** | Same for the performance review. |
| `.claude/skills/health-review/SKILL.md:113,396,486,514,606,727,739-740` | `health-review-backlog.json`, `performance-review-backlog.json`, `ml-review-backlog.json` | READER | **yes** | L113 "look at… the newest backlog_drain action timestamp in health-review-backlog.json"; L606 "NOT complete until every open item in docs/claude/health-review-backlog.json has been triaged THIS run"; L727 "Edit … to drain it". L3 description also names the backlog. |
| `.claude/skills/ml-review/SKILL.md:339,416,483,494,507-508` | `ml-review-backlog.json`, `health-review-backlog.json`, `performance-review-backlog.json` | READER | **yes** | Same shape: file `[refinement]` items into, triage every open item of, and edit/drain `ml-review-backlog.json`. |
| `.claude/skills/performance-review/SKILL.md:437,440,523,713,740,752-753` | `research-review-backlog.json`, `performance-review-backlog.json`, `health-review-backlog.json`, `ml-review-backlog.json` | READER | **yes** | Owns and links `research-review-backlog.json` (L437, a dead relative link); "has been triaged THIS run" (L523); "Edit … to drain + add" (L740). |
| `.claude/skills/doc-freshness/SKILL.md:43` | `READOUT.md`, `CONSTRAINT.json` | READER | **yes** | Step 9 tells the session `docs/claude/READOUT.md` + `CONSTRAINT.json` are "regenerated". |
| `.claude/skills/doc-freshness/SKILL.md:138-139,172-176` | `performance-review-backlog.json`, `ml-review-backlog.json`, `health-review-backlog.json` | READER | **yes** | The routing table and the "where a finding goes" list route findings into the three archived backlogs. |
| `.claude/skills/doc-freshness/SKILL.md:165` | `research-driver` | READER | **yes** | "see [`research-driver`](../research-driver/SKILL.md) Step 6" — dead link. |
| `.claude/skills/sprint-format/SKILL.md:68` | `health-review-backlog.json` | READER | **yes** | "minor leftovers to `docs/claude/health-review-backlog.json`". L71 (`session-handoff`) is a MENTION. |
| `.claude/skills/system-report/SKILL.md:3,8,14,16,17` | `system-review` | READER | **yes** | A live skill whose entire body is "invoke `/system-review` and follow `.claude/skills/system-review/SKILL.md` verbatim" — a link into an archived directory. `check_skills_index.py` passes. |
| `.claude/skills/macro-research/SKILL.md:3,11,28` | `research-driver` | MENTION | yes | Skill description says "`research-driver` dispatches here"; describes a dispatcher that no longer exists. |
| `.claude/skills/manager/SKILL.md:11-12`, `.claude/skills/close-out/SKILL.md:15-16` | `duty`, `delegate-work`, `session-coordination`, `session-handoff`, `research-driver`, `session-receipt` | MENTION | yes | Both say the skills are retired. Correct. |
| `.claude/skills/{backtesting:408,exit-refinement:300,macro-research:304,model-training:232,new-broker:247,new-strategy:717}/SKILL.md` | `system-review` | MENTION | yes | Identical boilerplate line "`/system-review` now enumerates everything shipped since the previous review". Stale claim, not an instruction. |
| `.claude/skills/{diag-data:64,llm-delegate:213,health-review:504-506}/SKILL.md` | `system-review`, `delegate-work` | MENTION | yes | Prose references. |
| `docs/CLAUDE-RULES-CANONICAL.md:1663-1676,1707` | `session-coordination`, `session-board.json` | READER | **yes** | "the binding workflow is the `session-coordination` skill… the live state is `docs/claude/session-board.json`… mirror it into `session-board.json::merge_slot` as the durable record". Binding text in the #1 doc. |
| `docs/CLAUDE-RULES-CANONICAL.md:168,1784,1831,1841` | `session-handoff` | READER | **yes** | "workflow is `session-handoff` (`.claude/skills/session-handoff/SKILL.md`)… Mechanics live in the `session-handoff` skill." |
| `docs/CLAUDE-RULES-CANONICAL.md:1218,1787,1801` | `research-driver`, `full-system-audit`, `delegate-work` | READER | **yes** | "binding on all `research-driver` work"; long-running-session list names two retired skills; `delegate-work` named as the parallel-axis counterpart. |
| `docs/CLAUDE-RULES-CANONICAL.md:1099,1583,1586,1589,1594` | `health-review-backlog.json`, `performance-review-backlog.json`, `ml-review-backlog.json`, `operator-owed-register.json` | READER | **yes** | Filing instructions: "specific drift to `docs/claude/health-review-backlog.json`", and the three backlogs plus the operator-owed register as the places a finding goes. |
| `docs/CLAUDE-RULES-CANONICAL.md:606,790,830` | `OPEN-ITEMS.json`, `RECURRENCE-LEDGER.json` | READER | **yes** | Soak rows are declared as living in `OPEN-ITEMS.json`; the recurrence ledger is cited as the record of a recurring class. |
| `docs/CLAUDE-RULES-CANONICAL.md:494,1394` | `system-review` | MENTION | yes | References to the `/system-review` coverage guard and to `/system-review` as a session type. |
| `docs/claude/INDEX.md:32,46,168` | `health-review-backlog.json` | READER | **yes** | "minor issue noticed but not fixed → `health-review-backlog.json` (the autonomous `/health-review` drains it)". L59-62,72 correctly say the skills are archived. |
| `docs/claude/coordination-board.md` (20 lines: 6,19,25,33,39,43,77,163,205,208,249,269,317,377,405,445,504,566,575,609) | `board-pointer.json`, `session-board.json`, `session-coordination`, `SESSIONS.json`, `OPEN-PRS.json`, `system-review` | READER | **yes** | The live protocol doc for the coordination board: "register in `session-board.json::active_sessions`", "the number lives in one file, `board-pointer.json`", "rewrite `board-pointer.json` and commit it". The hooks above point sessions here. |
| `docs/claude/board-body-template.md:33,95` | `board-pointer.json`, `session-coordination` | READER | **yes** | Template for the board issue body; embeds the pointer path and links the protocol. |
| `docs/claude/model-refinement-lifecycle.md:10,81` | `ml-review-backlog.json` | READER | **yes** | "Each lifecycle item lives in [`ml-review-backlog.json`]" — dead relative link. |
| `docs/claude/vm-resource-management.md:156` | `session-coordination` | READER | **yes** | "`.claude/skills/session-coordination/SKILL.md` — the binding workflow". |
| `docs/runbooks/merge-queue.md:38-39,55` | `session-coordination`, `health-review-backlog.json` | READER | **yes** | "This is the binding serializer. Full contract: the `session-coordination` skill (`.claude/skills/session-coordination/SKILL.md`) §2". |
| `docs/reference/session-capabilities.md:269,320,344` | `session-coordination`, `session-board.json`, `board-pointer.json` | READER | **yes** | Tells a web session `session-board.json::merge_slot` "still works" and that the board resolves from `board-pointer.json`. |
| `docs/reference/bot-api-reference.md:164,165,343` | `work/objects`, `OPEN-ITEMS.json` | MENTION | yes | Documents `GET /api/bot/work/object/{id}` and `/decisions` as returning work objects; describes the reader, is not itself one. |
| `docs/reference/diag-access.md:90` | `OPEN-ITEMS.json` | MENTION | yes | "do not add it to `OPEN-ITEMS.json`" — a negative instruction, consistent with the archive. |
| `docs/reference/env-vars.md:44,82,89,91` | `BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md`, `system-review`, `EXIT-EVAL-60S-ROOTCAUSE-2026-09-10.md`, `work/objects` | MENTION | yes | Cites archived memos as provenance for a knob; L91 says the Telegram decision channel writes into `work/objects`. |
| `docs/api-tier-policy.md:154,156,157` | `work/objects`, `OPEN-PRS.json`, `session-board.json`, `session-receipt`, `work/receipts` | MENTION | yes | Route table rows for `/api/bot/work/*`; CI-enforced tier policy, describes the reader routes. |
| `docs/github-actions-workflows.md:1175-1285` (13 rows) | `DUE.md`, `OPEN-ITEMS.json`, `PROBES.json`, `work/objects`, `OPEN-PRS.json`, `CONSTRAINT.json`, `READOUT.md`, `board-pointer.json`, `PR-QUEUE-WATCH.json`, `SESSIONS.json`, `SUNSET-DISPOSITIONS.json`, `duty` | MENTION | yes | Workflow catalog. Several rows still state a cron ("`due-list.yml` … cron 05:50 UTC", "`constraint-readout.yml` … cron 06:05 UTC", "`pr-queue-watch.yml` … cron `41 */6 * * *`") that the workflow file no longer carries — a stale claim, checked against the `on:` blocks. |
| `docs/DOCUMENT-INDEX.md:22,163,164` | `CYCLE-PRIORITY.json`, `DUE.md`, `READOUT.md` | MENTION | yes | Index rows: L163-164 say "verified: generator exists and names the file" for `docs/claude/DUE.md` and `READOUT.md` — both archived; the generator still exists. L244-285 correctly index the archive paths themselves. |
| `docs/claude/work/README.md:15-16` | `MANAGER-LEASE.json`, `SESSIONS.json`, `MERGE-QUEUE.json`, `OPEN-PRS.json` | MENTION | yes | Says they are archived. Correct. |
| `docs/plans/OPERATING-PLAN-2026-09-21.md`, `CLAUDE.md`, `ROADMAP.md`, `docs/ARCHITECTURE-CANONICAL.md` (all hits) | many | MENTION | yes | The plan of record and the changelog rows describing the retirement or past sessions. `ROADMAP.md:734-735,1113-1115` still read as instructions ("claim the `merge_slot` in `docs/claude/session-board.json`") but `ROADMAP.md` is rank 8, context only. |
| `docs/claude/{WORKPLAN-2026-08-21,WORKPLAN-2026-08-26,WORKPLAN-2026-08-29,WORKPLAN-NIGHT-2026-08-29,TASK-PRIORITY-2026-09-07,high-severity-class-map-2026-09-12,blocked-lane-watch,closed-flat-invariant,m20-m31-operator-decisions-2026-08-16,session-workflow,system-actions,trainer-resource-protocol,diag-relay}.md` | various | MENTION | yes | Dated plans and memos in a live directory; each names a register as it stood at the time. `session-workflow.md:59` links `docs/claude/session-handoff.md`, a live doc, not the skill. |
| `src/runtime/execution_diagnostics.py:353` | `system-review` | READER | **yes** | The ORPHAN-TRADE red-flag Telegram ping body ends "▶️ Initiate a /system-review to reconcile this to its real trade/order package". Sent to the operator on every new orphan. |
| `src/runtime/execution_diagnostics.py:246,311,315` | `system-review` | MENTION | no | Comments/docstring. |
| `src/runtime/manager_status.py:444` | `SESSIONS.json` | READER | **yes** | `SESSIONS_RELPATH`; read at L1508 (`read_json_file`) for the manager status view and by `work.py` (below). |
| `src/runtime/manager_status.py:796` | `MANAGER-LEASE.json` | READER | **yes** | `LEASE_RELPATH`; read by `work.py::_sessions_panel` to set the observation-stale window. |
| `src/runtime/manager_status.py:1541` | `SESSIONS.json` | READER | **yes** | Renders "⚠️ SESSIONS.json absent — owner registration could not be checked (shown as '?'), NOT that owners are unregistered" and continues grading. The exact degrade the lane describes. |
| `src/runtime/manager_status.py:725,742` | `SESSIONS.json` | MENTION | no | Measurement comments. |
| `src/web/api/routers/work.py:930-931` | `SESSIONS.json`, `MANAGER-LEASE.json` | READER | **yes** | `_sessions_panel()` behind `GET /api/bot/work`; returns `present: false, readState: "absent"`. |
| `src/web/api/routers/work.py:1076` | `SESSIONS.json` | READER | **yes** | `GET /api/bot/work/checklist` (the SPA Workflow page) reads `SESSIONS.json` to mark each owner registered; every owner now reads unregistered. |
| `src/web/api/routers/work.py:394-395,502-518` | `work/objects`, `work/intents`, `work/steps` | READER | **yes** | `_build_index()` walks the three archived directories via `_yaml_files()`, which returns `[]` when a directory is absent — the index renders empty, not archived. |
| `src/web/api/routers/work.py:342,357,373` | `work/objects`, `work/intents`, `work/steps` | MENTION | yes | Payload `path` fields naming where a row would live. |
| `src/web/api/routers/work.py:489` | `OPEN-ITEMS.json` | MENTION | yes | Payload note: "what a session must KNOW before it plans is still OPEN-ITEMS.json". |
| `src/web/api/routers/work.py:1001` | `SESSIONS.json` | READER | **yes** | `_SESSIONS_NOTE` payload text tells the operator session state "is only as fresh as the last MANAGER OBSERVATION written into docs/claude/work/SESSIONS.json". |
| `src/web/api/routers/work.py:1705` | `work/objects` | READER | **yes** | `POST /api/bot/work/decision` response: the answer "becomes the decision only when a committer writes it into docs/claude/work/objects/{id}.yaml". |
| `src/web/api/routers/work.py:1718,1860` | `session-receipt` | MENTION | yes | `/receipts` payload names `.claude/skills/session-receipt/SKILL.md` as owner. |
| `src/web/api/routers/work.py:1724` | `session-board.json` | MENTION | no | Comment. |
| `src/runtime/decision_subject.py:179-182` | `health-review-backlog.json`, `performance-review-backlog.json`, `ml-review-backlog.json`, `research-review-backlog.json` | READER | no | `_BACKLOG_FILES` — the universe for `backlog_row` decision subjects; `_json_ids` over absent files yields `None` ("could not look"), which the decisions inbox then reports. |
| `src/runtime/decision_subject.py:273` | `work/objects` | READER | no | `_work_ids()` walks `docs/claude/work/objects`; returns `None` when not a directory. |
| `src/runtime/decision_subject.py:318` | `OPEN-ITEMS.json` | READER | no | `open_item` subject universe. |
| `src/runtime/decision_subject.py:7,25,36` | `OPEN-ITEMS.json`, `work/objects`, `WO-20260903-SUNSET-DISPOSITIONS-OWED.yaml` | MENTION | no | Docstring measurements. |
| `src/runtime/telegram_decisions.py:557,856` | `work/objects` | READER | **yes** | Telegram decision text: "where the FULL text of this question lives" and the submitted-answer confirmation both point the operator at `docs/claude/work/objects/<id>.yaml`. |
| `src/runtime/telegram_decisions.py:280` | `work/objects` | MENTION | no | Comment. |
| `src/runtime/decision_push.py:246` | `work/objects` | READER | **yes** | Push text sent into a session: "Truth of record: docs/claude/work/objects/{id}.yaml". |
| `src/runtime/work_decisions.py:100,720` | `work/objects`, `OPEN-PRS.json` | MENTION | no | Comments. |
| `src/runtime/order_monitor.py:3657,6686` | `system-review` | MENTION | no | Comments describing the ping at `execution_diagnostics.py:353`. |
| `src/main.py:530`, `src/runtime/bybit_position_book.py:28`, `src/units/accounts/execute.py:1770`, `src/prop/*.py`, `src/runtime/*_reachability_alert.py`, `src/utils/trainer_manifest_health.py:31`, `src/web/api/routers/{diag.py:879,970; reports.py:16}` | archived memos, `system-review`, `full-system-audit` | MENTION | no | Comments and docstrings citing archived memos or the retired review as provenance. |
| `src/bot/recurring_dispatch.py:6,27` | `SESSIONS.json` | FALSE POSITIVE | — | `runtime_logs/recurring_sessions.jsonl` contains the substring `sessions.json`. |
| `.github/workflows/board-heartbeat.yml:93,98-101` | `board-pointer.json` | READER | yes (`::error`) | `workflow_dispatch` only. `jq -r … docs/claude/board-pointer.json`; `board_pointer.py` exits 4 → `::error … could_not_check`. |
| `.github/workflows/board-post.yml:114` | `board-pointer.json` | READER | yes (`::error`) | Armed: `push` on `automation/board-posts/**`. Reads the pointer from the default branch via the API and refuses with `::error` when empty. |
| `.github/workflows/board-rotate.yml:124,354` | `board-pointer.json` | READER + WRITER | no | Armed: `workflow_dispatch` + `push` on `automation/board-requests/**`. Rewrites and commits the pointer. |
| `.github/workflows/merge-claim-audit.yml:83,114` | `board-pointer.json` | READER | yes (issue comment) | **Armed: `pull_request_target: closed`.** Runs on every PR close; `board_pointer.py` rc≠0 → "no live coordination board resolved" carried as the third state. |
| `.github/workflows/scope-overlap-audit.yml:166,210,365` | `board-pointer.json` | READER | yes (issue comment) | `workflow_dispatch` only. Known positive. |
| `.github/workflows/constraint-readout.yml:224,228,255` | `CONSTRAINT.json`, `READOUT.md`, `CHECKLIST-ROUTING-AGE.json` | READER + WRITER | no | `workflow_dispatch` only. Runs `constraint_readout.py --write`, `render_session_brief.py` (the retired brief generator), then `commit-to-main` of the three archived paths. |
| `.github/workflows/due-list.yml:146,154` | `DUE.md`, `DUE.json` | READER + WRITER | yes (run summary) | **Armed: `issues: opened`** (label `due-list-now`) + dispatch. `cat docs/claude/DUE.md`, then `commit-to-main` of `DUE.json DUE.md`. |
| `.github/workflows/probes.yml:187,201` | `STUCK-BRANCHES.json`, `PROBES.json`, `DUE.json`, `DUE.md` | READER + WRITER | no | **Armed: `issues: opened`** (label `probes-now`) + dispatch. Writes the receipt and commits four archived paths to `main`. |
| `.github/workflows/error-feed-digest.yml:166-169` | `ERROR-FEED-DIGEST.json`, `DUE.json`, `DUE.md` | WRITER | no | `workflow_dispatch` only. Commits three archived paths. |
| `.github/workflows/pr-queue-watch.yml:359-360,478,509` | `PR-QUEUE-WATCH.json`, `BLOCKED-LANE-WATCH.json` | READER + WRITER | yes (`::error`) | `workflow_dispatch` only. Commits both receipts; the escalation `::error` tells the reader "See … docs/claude/work/PR-QUEUE-WATCH.json". L138-319 (`/tmp/open-prs.json`) are a FALSE POSITIVE on `OPEN-PRS.json`. |
| `.github/workflows/reconcile-open-prs.yml:130-135` | `OPEN-PRS.json` | READER + WRITER | yes (PR body) | **Armed: `push: main`** + dispatch. Runs `reconcile_open_prs.py --json` / `--apply` then `commit-to-main` of `docs/claude/work/OPEN-PRS.json`. What the script does when the file is absent is **not determined** (see below). |
| `.github/workflows/work-digest.yml:168,367` | `WORK-DIGEST.json` | READER + WRITER | no | **Armed: `push: main`**, job gated on a `due` output. Reads `docs/claude/work/WORK-DIGEST.json` inside `try/except` (degrades to `''`); commits it back with `pending-pings.jsonl`. |
| `.github/workflows/work-decision-commit.yml:155` | `work/objects` | WRITER | no | `workflow_dispatch` only. `commit-to-main` of `docs/claude/work/objects`. |
| `.github/workflows/session-reaper.yml:70,132` | `SESSIONS.json`, `REAPER-OBSERVATIONS.json` | WRITER | no | `push` path filter on `docs/claude/work/SESSIONS.json` (dormant — the path cannot change) + dispatch. Writes `REAPER-OBSERVATIONS.json`, which is **still live**. |
| `.github/workflows/trainer-capture-watch.yml:293,303` | `TRAINER-CAPTURE-WATCH.json` | READER + WRITER | no | Scheduled (`23 */6 * * *`). Target is **still live**; not degraded. |
| `.github/workflows/sunset-pass.yml:186` | `SUNSET-DISPOSITIONS.json` | READER | yes (issue text) | `workflow_dispatch` only. Message text: "Disposition candidates in `docs/claude/SUNSET-DISPOSITIONS.json`". |
| `.github/workflows/diag-relay-sweep.yml:113` | `health-review-backlog.json` | MENTION | yes (issue comment) | Scheduled daily. Posts a close-comment ending "Background: BL-20260527-001 in `docs/claude/health-review-backlog.json`". |
| `.github/workflows/{armed-branch-push-watch:9, error-feed-digest:18-136, probes:10-125, prune-merged-claude-branch:14, pytest-run:197-241, session-reaper:13-37, trainer-diag-relay:127, trainer-vm-diag:24,104, stale-automation-sweep:51}.yml` | various | MENTION | no | Comments. `pytest-run.yml:257` and `stale-automation-sweep.yml:135-145` are FALSE POSITIVES (`system-review-checklist.json`, a live file; `/tmp/open-prs.json`, a temp file). |
| `.github/actions/commit-to-main/action.yml:558-564` | `session-board.json` | READER + WRITER | no | Conflict-resolution branch: if the *only* conflicted file is `docs/claude/session-board.json`, `checkout --theirs` and `git add` it. Dormant unless that file conflicts; used by ~27 workflows. |
| `.gitattributes:35-65` | `health-review-backlog.json`, `performance-review-backlog.json`, `ml-review-backlog.json`, `research-review-backlog.json`, `OPEN-ITEMS.json`, `SESSIONS.json`, `OPEN-PRS.json`, `operator-owed-register.json`, `SUNSET-DISPOSITIONS.json`, `RECURRENCE-LEDGER.json`, `MERGE-QUEUE.json` | MENTION | no | `merge=jsonregister` driver assignments for archived paths. Inert while the paths do not exist. |
| `scripts/ci/board_pointer.py:49,69,76` | `board-pointer.json` | READER | yes (stderr) | `POINTER_PATH`; used by 4 workflows and the hooks. Observed: `could_not_check — …/board-pointer.json does not exist`, rc 4. Honest. |
| `scripts/ci/check_board_coherence.py:67` | `session-coordination` | READER | no | Lists `.claude/skills/session-coordination/SKILL.md` among the docs it checks; invoked by `board-rotate.yml` and named in `settings.json`. |
| `scripts/ops/accrual_clock.py:193-196` | four backlogs | READER | yes (`::` output) | **In CI** (`accrual-clock` guard, `--all`). Observed: `SKIPPING … not present` ×4, rc 0. Census now measures nothing. |
| `scripts/check_soak_doctrine.py:142-145` | four backlogs | READER | no | **In CI** (`soak-doctrine-guard`). `_load_backlog_ids` skips absent paths by design (comment at L138); observed `clean`. |
| `scripts/check_impossibility_claims.py:74-76` | three backlogs | READER | no | **In CI** (`impossibility-claim-guard`). `SCAN_GLOBS` silently absent; observed "377 file(s) scanned" — all from `docs/research`. |
| `scripts/ci/check_artifact_caveats.py:101-103` | three backlogs | READER | no | **In CI** (`artifact-caveat-guard`). Observed `clean — … every open row naming one is adjudicated` over zero backlog rows. L317,367 are self-test fixtures (MENTION). |
| `scripts/ci/check_stated_population.py:77-81` | three backlogs, `RECURRENCE-LEDGER.json`, `OPEN-ITEMS.json` | READER | no | **In CI**, diff-scoped `WATCHED_FILES`. Harmless: an archived path cannot appear in a diff. |
| `scripts/ci/check_manager_scope.py:339-341,372,388-392,1084` | `MANAGER-LEASE.json`, `SESSIONS.json`, `manager-scope-exception.yaml`, `session-board.json`, `OPEN-ITEMS.json`, `CYCLE-PRIORITY.json`, `health-review-backlog.json`, `MERGE-QUEUE.json` | READER | no | **In CI** (`manager-scope-guard`). Reads the lease to decide whether the manager "is in office"; with no lease the self-test's own third case applies: manager commits grade `unattributed`, not `violation`. L1767 is a self-test fixture. |
| `scripts/ci/check_pr_landing.py:268,333,974,1481` | `session-board.json`, `work/approvals`, `work/objects` | READER | no | **In CI** (`pr-landing-guard`). Reads `session-board.json` for merge-slot claims, `work/approvals/<slug>.json` for Tier-2 approval records, `work/objects/<wo>.yaml` for parent objects. Self-test passes; behaviour against a real PR with the paths absent **not determined**. |
| `scripts/ci/check_canonical_doc_coherence.py:539` | `session-board.json` | READER | no | **In CI.** `_VALUE_DOC_EXTRAS` filtered by `exists()` — silently drops the archived path. Fine. |
| `scripts/ci/check_trainer_capture_watch.py:63` | `TRAINER-CAPTURE-WATCH.json` | READER | no | **In CI.** Target still live. |
| `scripts/ci/{check_collapsed_states.py:284,487,1335; guard_selftests.py:183-331; check_skills_index.py:8-9; check_unwired_artifacts.py:257; check_guard_selftest_coverage.py:9}` | `SESSIONS.json`, `work/objects`, `health-review-backlog.json`, retired skills | MENTION | no | Contract strings, self-test fixtures and docstrings. |
| `scripts/ops/render_daily_brief.py:161-165` | `SESSIONS.json`, `OPEN-PRS.json`, `MANAGER-LEASE.json`, `OPEN-ITEMS.json`, `CYCLE-PRIORITY.json` | READER | yes (brief text) | Known positive. Module constants read for the daily brief; L737/779 brief text still says state lives in `SESSIONS.json`/`DUE.md`. Invoked by no workflow at `53436a1` (tests only). L116-117 are a FALSE POSITIVE (`/tmp/night-sessions.json`). |
| `scripts/ops/render_due_list.py:67,259-261,341-343,676` | `OPEN-ITEMS.json` | READER | no | Returns `SourceResult(…, "not_applicable", note="… absent")` — honest. Invoked by `due-list`, `probes`, `error-feed-digest`, `constraint-readout` workflows (see their triggers above). |
| `scripts/ops/reconcile_open_prs.py:2,204,264` | `OPEN-PRS.json` | READER | yes (`::error`) | Handles a *parse* failure with `::error`; the *absent* path was not exercised — **not determined**. Runs on every push to `main`. |
| `scripts/ops/open_pr_record.py:147,380,457,496` | `OPEN-PRS.json` | READER | no | `RECORD_PATH`; grades the record; `unreadable` state on parse failure. Absent path not exercised. |
| `scripts/ops/work_digest.py:551-553,565-623` | `OPEN-ITEMS.json`, `health-review-backlog.json` | READER | no | Declares three read states (`read`, `absent`, `unreadable`) and reports `absent` explicitly — honest. Invoked by `work-digest.yml` on `push: main`. |
| `scripts/ops/pipeline.py:31,40`, `scripts/ops/soak_alarm.py:2,17` | `OPEN-ITEMS.json`, `DUE.md`, `duty` | MENTION | no | `pipeline.py` docstring explains why the pipeline replaces `DUE.md`; `soak_alarm.py` (in CI) header comment says it is wired to `OPEN-ITEMS.json` — the code reads via `render_due_list.py`, which reports absent. |
| `scripts/ops/error_feed_digest.py:6-579` (9 lines) | `duty` | MENTION | no | Docstrings say the digest is rendered "for the `duty` pass". Invoked by `error-feed-digest.yml` (dispatch only). |
| `tests/test_open_items_observation_loss.py:30`, `tests/test_predicted_register_integrity.py:56`, `tests/test_register_reserialization.py:312,404`, `tests/test_operator_owed.py:41`, `tests/test_stuck_branch_carrier.py:33`, `tests/test_decision_subject.py:254`, `tests/test_decision_inbox_both_channels.py:146`, `tests/test_commit_to_main_branch_slot.py:35`, `tests/test_commit_to_main_callers.py:148` | `OPEN-ITEMS.json`, `health-review-backlog.json`, `operator-owed-register.json`, `STUCK-BRANCHES.json`, `work/objects`, `session-board.json` | READER | no | Tests that name the real repo path. Ran them: 282 passed, 6 skipped, 0 failed on this population. Skips are honest ("SKIPPED IS NOT PASSED — the real-history control did not run"; "no decision requests in the store here"). The rest pass over an empty or fixture population. |
| `tests/test_trade_pings.py:191,209`, `tests/test_monitor_reconciler.py:376,379` | `system-review` | READER | no | Assert `"/system-review" in body` of the orphan ping — they pin the retired-skill instruction at `execution_diagnostics.py:353`. |
| `tests/test_{check_allow_degraded,check_backlog_refs,decision_subject,manager_status,probe_file_whole_json,session_brief_diff_scope,work_digest}.py` (writes) | various | MENTION | no | Fixture writes under `tmp_path`; not the register. |
| `config/strategies.yaml:879,2089,2233`, `config/accounts.yaml:1418`, `ml/configs/*.yaml` (3) | `performance-review-backlog.json`, `ml-review-backlog.json`, `system-review` | MENTION | no | YAML comments citing a backlog row or a review session as provenance. |
| `comms/schema/{ml_review,performance_review}_response.template.json:65,72` | `ml-review-backlog.json`, `performance-review-backlog.json` | MENTION | yes | `_comment`: "Result of draining docs/claude/…-backlog.json this run". The template a review session fills. |
| `docs/claude/pending-pings.jsonl`, `docs/claude/work/MANAGER-CHECKLIST.json`, `docs/claude/work/PIPELINE.jsonl`, `docs/claude/work/REAPER-OBSERVATIONS.json` | various | MENTION | yes (checklist/pipeline are operator-facing) | Live data files whose rows cite archived paths: checklist notes recording the archive itself (L136) and a guard census (L682, "67 guard scripts unreachable from run_guards.py, kept because 30+ live files still name their paths"); `PIPELINE.jsonl:19-20` are two items already filed about `board-pointer.json` readers; `REAPER-OBSERVATIONS.json` rows are branch names. Data, not readers. |

## Table B — every other hit file outside the record populations (MECHANICAL classification, not hand-verified)

Classification here is the line-level heuristic described in § Commands, rolled up to the file's strongest class. I did **not** open these files; a `READER` here means a path constant or a read call names the archived path, and says nothing about what happens when the path is absent. Treat the class as a lead, not a finding. A few files appear in both tables because Table A groups them under a brace pattern the exclusion did not parse; where they disagree, Table A (hand-read) wins.

| file | needles matched | hit lines | mechanical class | note |
|---|---|---|---|---|
| `comms/claude_strategy_scores.jsonl` | `system-review` | 2509, 2510, 2511, 2512, 2513, 2514, 2515, 2516, 2517, 2518, 2519, 2520 … | MENTION | prose |
| `comms/schema/ml_review_response.template.json` | `ml-review-backlog.json` | 65 | MENTION | comment or docstring |
| `comms/schema/performance_review_response.template.json` | `performance-review-backlog.json` | 72 | MENTION | comment or docstring |
| `docs/audit/held-pr-automerge-2026-09-02.md` | `OPEN-ITEMS.json` | 82 | MENTION | prose |
| `docs/claude/pruned-branches-manifest.txt` | `full-system-audit` | 61 | MENTION | prose in an instruction doc |
| `docs/claude/strategy-refinement-queue.json` | `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json` | 2 | MENTION | comment or docstring |
| `docs/claude/system-review-checklist.json` | `research-review-backlog.json`, `session-board.json`, `work/objects` | 36, 84, 164 | MENTION | code string / message text |
| `docs/claude/workplan.md` | `SESSIONS.json` | 591 | FALSE POSITIVE | same substring match on `recurring_sessions.jsonl` |
| `docs/live-exit-monitor-cadence-DESIGN.md` | `system-review` | 27 | MENTION | prose |
| `docs/ml/optimization-roadmap.md` | `ml-review-backlog.json` | 12, 779 | MENTION | prose |
| `docs/reports/system-report-DESIGN.md` | `system-review` | 22 | MENTION | prose |
| `docs/runbooks/alpaca-integration.md` | `session-handoff` | 12 | MENTION | prose in an instruction doc |
| `docs/runbooks/oanda-integration.md` | `session-handoff` | 10 | READER | instruction text directs a session/human at the file |
| `docs/sizing-legalization-DESIGN.md` | `full-system-audit`, `system-review` | 260, 260 | MENTION | prose |
| `docs/sprint-plans/M18-CAPITAL-ALLOCATOR-EXECUTION-PLAN.md` | `delegate-work`, `session-board.json`, `session-coordination` | 7, 9, 15, 54, 62, 70, 77 | MENTION | prose |
| `ml/configs/mes-regime-1d-lgbm-v2.yaml` | `system-review` | 38 | MENTION | comment or docstring |
| `ml/configs/setup-candidates-metalabel-xsym-v1.yaml` | `ml-review-backlog.json` | 43 | MENTION | comment or docstring |
| `ml/configs/setup-candidates-metalabel-xsym-yz-v1.yaml` | `ml-review-backlog.json` | 55 | MENTION | comment or docstring |
| `scripts/check_claim_basis.py` | `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json` | 41, 42, 43, 53 | READER | path constant in a list the file reads |
| `scripts/ci/check_armed_branch_push.py` | `SESSIONS.json` | 27 | MENTION | code string / message text |
| `scripts/ci/check_backlog_unresolve.py` | `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json` | 81, 82, 83, 84, 330 | READER | path assigned to a name; path constant in a list the file reads |
| `scripts/ci/check_capability_pull.py` | `CONSTRAINT.json`, `work/objects` | 52, 69, 70, 95 | READER | code string / message text; path assigned to a name; path constant in a list the file reads |
| `scripts/ci/check_digest_liveness.py` | `WORK-DIGEST.json` | 373 | MENTION | code string / message text |
| `scripts/ci/check_due_list_token.py` | `DUE.json` | 17, 56, 139 | MENTION | code string / message text |
| `scripts/ci/check_edge_kind_vocabulary.py` | `WO-20260908-RE-DISPATCH-THE-MGC-REMEDIATION-AGAINST-THE.yaml`, `work/objects` | 41, 117 | MENTION | code string / message text |
| `scripts/ci/check_guard_selftest_coverage.py` | `full-system-audit` | 9 | MENTION | code string / message text |
| `scripts/ci/check_manager_queue_watch.py` | `PR-QUEUE-WATCH.json` | 91 | MENTION | code string / message text |
| `scripts/ci/check_one_live_workplan.py` | `workplan-vs-architecture` | 24, 89, 326 | MENTION | code string / message text; comment or docstring |
| `scripts/ci/check_open_items.py` | `OPEN-ITEMS.json`, `health-review-backlog.json`, `system-review` | 3, 15, 27, 67, 456 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ci/check_operator_owed.py` | `operator-owed-register.json` | 13, 97 | READER | code string / message text; path assigned to a name |
| `scripts/ci/check_pr_queue_watch.py` | `PR-QUEUE-WATCH.json` | 93 | READER | read/resolve signal |
| `scripts/ci/check_priority_fallback_distribution.py` | `PRIORITY-FALLBACK-BASELINE.json` | 82 | MENTION | code string / message text |
| `scripts/ci/check_recurrence_ledger.py` | `RECURRENCE-LEDGER.json` | 46, 66, 82 | READER | comment or docstring; read/resolve signal |
| `scripts/ci/check_register_field_loss.py` | `OPEN-ITEMS.json`, `SESSIONS.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json`, ` | 7, 16, 55, 104, 109, 110, 111, 112, 113, 114, 235 | MENTION | code string / message text; comment or docstring |
| `scripts/ci/check_register_ids.py` | `OPEN-ITEMS.json`, `OPEN-PRS.json`, `SESSIONS.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review | 33, 84, 85, 86, 95, 96, 153, 158, 163, 166, 168, 170 … | MENTION | code string / message text; comment or docstring |
| `scripts/ci/check_register_reserialization.py` | `OPEN-ITEMS.json`, `ml-review-backlog.json`, `performance-review-backlog.json` | 7, 20, 292, 606, 607 | MENTION | code string / message text |
| `scripts/ci/check_role_pack_operating_layer.py` | `CONSTRAINT.json`, `CYCLE-PRIORITY.json`, `READOUT.md`, `SESSIONS.json`, `SUNSET-DISPOSITIONS.json`, `delegate-work`, `duty`, `research-driver`, `session-coordi | 17, 17, 17, 67, 68, 69, 70, 95, 96, 98, 99, 100 … | READER | code string / message text; comment or docstring; path constant in a list the file reads |
| `scripts/ci/check_scope_overlap.py` | `MANAGER-LEASE.json`, `OPEN-ITEMS.json`, `SESSIONS.json`, `board-pointer.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-back | 19, 134, 214, 215, 669, 915, 917, 919, 929, 939, 940, 948 … | READER | code string / message text; comment or docstring; path assigned to a name; path constant in a list the file reads |
| `scripts/ci/check_skills_index.py` | `backlog-drain`, `full-system-audit`, `research-driver`, `session-coordination`, `system-review` | 8, 8, 9, 9, 9 | MENTION | code string / message text |
| `scripts/ci/check_soak_registered.py` | `OPEN-ITEMS.json` | 19, 60, 79, 92 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ci/check_spec_carrier.py` | `OPEN-ITEMS.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json`, `work/intents`, `w | 72, 73, 74, 77, 79, 80, 81, 82, 236, 265, 269 | WRITER | code string / message text; path constant in a list the file reads; write signal |
| `scripts/ci/check_stale_in_flight.py` | `SESSIONS.json` | 36 | MENTION | code string / message text |
| `scripts/ci/check_sunset_dispositions.py` | `SUNSET-DISPOSITIONS.json` | 7, 61, 140 | READER | code string / message text; read/resolve signal |
| `scripts/ci/check_uncarried_specs.py` | `RETIRED-MIRRORS-2026-09-11.md`, `UNCARRIED-SPEC-BASELINE.json` | 14, 20, 33, 74 | MENTION | code string / message text |
| `scripts/ci/check_unwired_artifacts.py` | `system-review` | 257 | MENTION | comment or docstring |
| `scripts/ci/check_wip_ceiling.py` | `OPEN-ITEMS.json` | 12 | MENTION | comment or docstring |
| `scripts/ci/guard_selftest_exemptions.json` | `OPEN-ITEMS.json`, `SESSIONS.json` | 6, 13, 20, 27, 34, 41, 48, 55, 62, 69, 76 | MENTION | comment or docstring |
| `scripts/ci/guard_selftests.py` | `health-review-backlog.json` | 183, 199, 208, 217, 331 | READER | code string / message text; path constant in a list the file reads |
| `scripts/ci/run_guards.py` | `MANAGER-LEASE.json`, `OPEN-ITEMS.json`, `SESSIONS.json` | 102, 1145 | MENTION | comment or docstring |
| `scripts/notify_on_pull.py` | `system-review` | 509 | MENTION | comment or docstring |
| `scripts/ops/backlog_append.py` | `OPEN-ITEMS.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json`, `session-board.jso | 700, 800, 813, 814, 815, 816, 842, 843 | READER | comment or docstring; path assigned to a name; path constant in a list the file reads |
| `scripts/ops/backlog_drain_candidates.py` | `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json` | 63, 64, 65 | READER | read/resolve signal |
| `scripts/ops/backlog_search.py` | `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json` | 12, 47, 48, 49 | READER | code string / message text; path constant in a list the file reads |
| `scripts/ops/backlog_union_merge.py` | `health-review-backlog.json`, `ml-review-backlog.json` | 15, 80, 97, 117, 553 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/blocked_lane_watch.py` | `BLOCKED-LANE-WATCH.json`, `SESSIONS.json` | 7, 26, 29, 113, 158, 160, 286, 309, 311, 371, 607, 613 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/calendar_edge_census.py` | `OPEN-ITEMS.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json` | 44, 428, 429, 430, 431, 432 | MENTION | code string / message text |
| `scripts/ops/capture_idea.py` | `CYCLE-PRIORITY.json` | 37 | MENTION | code string / message text |
| `scripts/ops/check_backlog_criteria.py` | `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json`, `system-review` | 87, 88, 89, 91, 110, 261, 340, 693, 718, 815, 1203 | READER | code string / message text; comment or docstring; path constant in a list the file reads |
| `scripts/ops/check_backlog_refs.py` | `health-review-backlog.json` | 116 | MENTION | code string / message text |
| `scripts/ops/check_wake_liveness.py` | `MANAGER-WAKE.json` | 78 | READER | read/resolve signal |
| `scripts/ops/checklist_routing_age.py` | `CHECKLIST-ROUTING-AGE.json`, `DUE.md` | 85, 99, 161, 530 | READER | code string / message text; comment or docstring; path constant in a list the file reads; read/resolve signal |
| `scripts/ops/claim_merge_slot.py` | `session-board.json` | 2, 8, 17, 40, 129, 236, 423 | READER | code string / message text; comment or docstring; path assigned to a name; read/resolve signal |
| `scripts/ops/commit_work_decisions.py` | `work/objects` | 56 | MENTION | code string / message text |
| `scripts/ops/constraint_readout.py` | `CONSTRAINT.json`, `CYCLE-PRIORITY.json`, `DUE.md`, `READOUT.md`, `health-review-backlog.json`, `operator-owed-register.json`, `work/intents`, `work/objects` | 5, 43, 90, 91, 92, 93, 94, 95, 369, 568, 713, 828 … | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/demote_budget.py` | `SUNSET-DISPOSITIONS.json` | 312 | READER | path assigned to a name |
| `scripts/ops/digest_due.py` | `WORK-DIGEST.json` | 91 | READER | read/resolve signal |
| `scripts/ops/document_index.py` | `BACKLOG-TRIAGE-2026-09-17.md`, `BYBIT-SYMBOL-DEDUPE-REPAIR-2026-09-12.md`, `CYCLE-PRIORITY.json`, `DUE.md`, `READOUT.md`, `SYSTEM-REVIEW-2026-09-17.md`, `syste | 17, 304, 346, 357, 359, 365, 365, 467, 468, 840 | MENTION | code string / message text; comment or docstring |
| `scripts/ops/grade_closed_trades_action.sh` | `system-review` | 11 | MENTION | comment or docstring |
| `scripts/ops/grade_closed_trades_from_diag.py` | `system-review` | 19 | MENTION | code string / message text |
| `scripts/ops/handoff_check.py` | `MANAGER-LEASE.json`, `OPEN-PRS.json`, `SESSIONS.json` | 26, 45, 124, 126, 130, 258 | READER | code string / message text; path constant in a list the file reads |
| `scripts/ops/in_flight_owner_liveness.py` | `SESSIONS.json`, `work/objects` | 10, 15, 40, 44, 87, 97, 204, 231, 238, 240, 245 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/journal_venue_audit.py` | `JOURNAL-VENUE-FLEET-AUDIT-2026-09-08.md` | 7, 19 | MENTION | code string / message text; comment or docstring |
| `scripts/ops/manager_lease.py` | `MANAGER-LEASE.json`, `SESSIONS.json`, `WO-20260901-PHASE-E.yaml`, `work/objects` | 17, 123, 362, 363, 363 | READER | code string / message text; path constant in a list the file reads; read/resolve signal |
| `scripts/ops/manager_preflight.py` | `CONCURRENCY-CAPS.json`, `MANAGER-LEASE.json`, `OPEN-ITEMS.json`, `OPEN-PRS.json`, `SESSIONS.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `per | 13, 50, 150, 159, 160, 162, 163, 164, 165, 166, 167, 173 … | READER | code string / message text; comment or docstring; path assigned to a name; path constant in a list the file reads; read/ |
| `scripts/ops/manager_state_watch.py` | `MANAGER-LEASE.json`, `SESSIONS.json` | 87, 125, 175 | READER | comment or docstring; path constant in a list the file reads |
| `scripts/ops/manager_view.py` | `SESSIONS.json` | 14, 35, 45, 81, 95, 167, 635 | MENTION | code string / message text; comment or docstring |
| `scripts/ops/manager_wake.py` | `MANAGER-LEASE.json`, `MANAGER-WAKE.json`, `MERGE-QUEUE.json`, `SESSIONS.json`, `manager-wake-routine-prompt.md` | 6, 53, 59, 142, 144, 145, 146, 244, 424, 426, 454, 456 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/merge_json_register.py` | `OPEN-ITEMS.json`, `OPEN-PRS.json`, `SESSIONS.json`, `health-review-backlog.json` | 9, 10, 11, 33 | MENTION | code string / message text |
| `scripts/ops/merge_slot_state.py` | `session-board.json` | 11, 15, 289 | READER | code string / message text; path assigned to a name |
| `scripts/ops/migrate_backlog_to_work_objects.py` | `OPEN-ITEMS.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json` | 19, 28, 29, 42, 115, 116, 117, 118, 334 | READER | code string / message text; comment or docstring; path assigned to a name |
| `scripts/ops/over_cover_proposal.py` | `operator-owed-register.json` | 32 | MENTION | comment or docstring |
| `scripts/ops/owner_liveness.py` | `SESSIONS.json`, `work/objects` | 8, 67, 136, 137, 382, 408 | READER | code string / message text; comment or docstring; path assigned to a name; read/resolve signal |
| `scripts/ops/pr_action_gate.py` | `CYCLE-PRIORITY.json`, `SESSIONS.json`, `pr-action-exception.yaml`, `spawn-priority-exception.yaml` | 11, 57, 147, 206, 260, 271, 290, 295, 420, 427, 437, 471 … | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/pr_queue_latency.py` | `OPEN-PRS.json`, `PR-QUEUE-WATCH.json`, `REAPER-OBSERVATIONS.json` | 49, 51, 142 | READER | code string / message text; target still exists live; comment or docstring; read/resolve signal |
| `scripts/ops/probe_actions_log.py` | `OPEN-ITEMS.json`, `PROBES.json` | 2, 50 | MENTION | code string / message text; comment or docstring |
| `scripts/ops/probe_api.py` | `OPEN-ITEMS.json` | 2 | MENTION | comment or docstring |
| `scripts/ops/probe_file.py` | `DUE.json`, `OPEN-ITEMS.json` | 2, 68 | MENTION | comment or docstring |
| `scripts/ops/probe_lib.py` | `OPEN-ITEMS.json` | 3 | MENTION | comment or docstring |
| `scripts/ops/probe_soak.py` | `OPEN-ITEMS.json` | 2, 7, 87 | MENTION | code string / message text; comment or docstring |
| `scripts/ops/pull_mes_ibkr_history.sh` | `ml-review-backlog.json` | 18 | MENTION | comment or docstring |
| `scripts/ops/push_decisions_back.py` | `MANAGER-LEASE.json` | 98 | MENTION | comment or docstring |
| `scripts/ops/queue_latency.py` | `MANAGER-LEASE.json`, `SESSIONS.json` | 78, 270, 324, 704 | READER | code string / message text; path assigned to a name |
| `scripts/ops/render_session_brief.py` | `CHECKLIST-ROUTING-AGE.json`, `CONSTRAINT.json`, `CYCLE-PRIORITY.json`, `OPEN-ITEMS.json`, `READOUT.md`, `RECURRENCE-LEDGER.json` | 60, 61, 62, 63, 65, 81, 194, 220, 238, 243, 358, 359 … | READER | code string / message text; path constant in a list the file reads; read/resolve signal |
| `scripts/ops/run_probes.py` | `OPEN-ITEMS.json`, `PROBES.json`, `system-review` | 3, 16, 28, 81, 82 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/run_training_cycle.sh` | `system-review` | 606 | MENTION | comment or docstring |
| `scripts/ops/session_reaper.py` | `REAPER-OBSERVATIONS.json`, `SESSIONS.json`, `WO-20260901-PHASE-E.yaml`, `work/objects` | 7, 7, 40, 123, 124, 385 | MENTION | code string / message text; code string / message text; target still exists live; comment or docstring |
| `scripts/ops/session_receipt.py` | `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-backlog.json`, `session-board.json`, `work/receipts` | 104, 109, 110, 119, 120, 121, 122 | READER | comment or docstring; path assigned to a name; path constant in a list the file reads |
| `scripts/ops/session_registry.py` | `SESSIONS.json`, `board-pointer.json`, `session-board.json`, `spawn-priority-exception.yaml` | 7, 80, 101, 209, 323, 385, 458, 475, 535, 570, 769, 831 … | READER | code string / message text; comment or docstring; path assigned to a name; path constant in a list the file reads; read/ |
| `scripts/ops/spawn_gate.py` | `CYCLE-PRIORITY.json`, `SESSIONS.json`, `spawn-priority-exception.yaml`, `work/intents`, `work/objects` | 12, 80, 105, 106, 129, 191, 205, 210, 215, 221 | READER | code string / message text; comment or docstring; path constant in a list the file reads; read/resolve signal |
| `scripts/ops/stuck_automation_branches.py` | `STUCK-BRANCHES.json` | 6 | MENTION | comment or docstring |
| `scripts/ops/sunset_pass.py` | `SUNSET-DISPOSITIONS.json` | 146, 644, 675, 714, 723, 910 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/sweep_stale_automation_prs.py` | `OPEN-PRS.json`, `WORK-DIGEST.json`, `session-board.json` | 37, 58, 59, 67, 128, 132, 479 | READER | code string / message text; comment or docstring; read/resolve signal |
| `scripts/ops/system_review_checklist.py` | `research-review-backlog.json`, `system-review` | 2, 39, 214 | FALSE POSITIVE | same: the live checklist JSON, not the skill |
| `scripts/ops/uncarried_specs.py` | `DUE.json`, `OPEN-ITEMS.json`, `WORK-DIGEST.json`, `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `research-review-b | 125, 126, 127, 128, 130, 131, 136, 137, 138, 139, 204, 206 … | READER | path constant in a list the file reads |
| `scripts/ops/work_phase_ping.py` | `work/objects` | 56 | READER | read/resolve signal |
| `scripts/reports/backlog_counts.py` | `health-review-backlog.json`, `ml-review-backlog.json`, `performance-review-backlog.json`, `system-review` | 7, 8, 9, 17, 56, 118, 119, 120 | MENTION | code string / message text; comment or docstring |
| `scripts/reports/render_system_report.py` | `full-system-audit`, `system-review` | 7, 586, 931, 967 | READER | code string / message text; comment or docstring; path assigned to a name |
| `scripts/research/regime_tag_emitted.py` | `session-handoff` | 16 | FALSE POSITIVE | `docs/research/session-handoff-2026-06-01.md` is a research memo sharing the skill's name |
| `scripts/research/tp_recovery_counterfactual.py` | `system-review` | 4, 10 | MENTION | code string / message text; comment or docstring |
| `src/prop/prop_fills_staleness.py` | `full-system-audit` | 5 | MENTION | code string / message text |
| `src/prop/prop_risk_gate.py` | `system-review` | 5, 324 | MENTION | code string / message text |
| `src/runtime/account_reachability_alert.py` | `system-review` | 10 | MENTION | code string / message text |
| `src/runtime/trainer_reachability_alert.py` | `system-review` | 5 | MENTION | code string / message text |
| `src/web/api/routers/reports.py` | `system-review` | 16 | MENTION | code string / message text |
| `tests/ops/test_merge_json_register.py` | `OPEN-ITEMS.json`, `OPEN-PRS.json`, `SESSIONS.json`, `health-review-backlog.json` | 23, 24, 34, 347 | MENTION | code string / message text; comment or docstring |
| `tests/test_accounts_clients_position_read_state_collapse.py` | `BYBIT2-ETH-PHANTOM-CLOSE-2026-09-09.md` | 28 | MENTION | comment or docstring |
| `tests/test_armed_branch_push.py` | `SESSIONS.json` | 14 | MENTION | code string / message text |
| `tests/test_backlog_append.py` | `health-review-backlog.json`, `research-review-backlog.json` | 240, 520, 652 | READER | code string / message text; comment or docstring; path assigned to a name |
| `tests/test_backlog_counts.py` | `system-review` | 1, 5 | MENTION | code string / message text; comment or docstring |
| `tests/test_backlog_drain_candidates.py` | `backlog-drain` | 7 | MENTION | code string / message text |
| `tests/test_backlog_splice.py` | `OPEN-ITEMS.json`, `session-board.json` | 10, 204, 215 | MENTION | code string / message text; comment or docstring |
| `tests/test_blocked_lane_watch.py` | `BLOCKED-LANE-WATCH.json`, `SESSIONS.json` | 359, 425 | MENTION | code string / message text; comment or docstring |
| `tests/test_breakout_prop_wiring.py` | `system-review` | 68 | MENTION | comment or docstring |
| `tests/test_bybit_hedge_book_flat_read.py` | `BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md` | 17 | MENTION | code string / message text |
| `tests/test_bybit_position_book.py` | `BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md` | 17 | MENTION | code string / message text |
| `tests/test_canonical_doc_conflict_markers.py` | `health-review-backlog.json` | 14 | MENTION | code string / message text |
| `tests/test_claim_merge_slot.py` | `session-board.json` | 5, 32, 73, 98, 111, 152, 162, 179 | MENTION | code string / message text; comment or docstring |
| `tests/test_constraint_readout_one_line_id_safe.py` | `READOUT.md` | 92 | MENTION | comment or docstring |
| `tests/test_decision_push.py` | `work/objects` | 151 | MENTION | code string / message text |
| `tests/test_deploy_pull_restart_runtime_gate.py` | `health-review-backlog.json` | 161 | READER | path constant in a list the file reads |
| `tests/test_diag_log_file_allowlist_coherence.py` | `system-review` | 157, 191 | MENTION | code string / message text |
| `tests/test_diag_token_workflows.py` | `system-review` | 340 | MENTION | comment or docstring |
| `tests/test_due_list_id_safe_truncation.py` | `DUE.json`, `DUE.md` | 6, 15, 16 | MENTION | code string / message text |
| `tests/test_error_feed_digest.py` | `duty` | 2, 194 | MENTION | code string / message text |
| `tests/test_etf_intraday_pilot_wiring.py` | `system-review` | 98 | MENTION | comment or docstring |
| `tests/test_guards_uncommitted_work.py` | `health-review-backlog.json` | 77 | MENTION | code string / message text |
| `tests/test_handoff_check.py` | `OPEN-PRS.json` | 179, 186 | MENTION | code string / message text |
| `tests/test_ib_per_pass_breaker.py` | `EXIT-EVAL-60S-ROOTCAUSE-2026-09-10.md` | 6 | MENTION | code string / message text |
| `tests/test_json_notes_shed_before_envelope.py` | `system-review` | 44 | MENTION | code string / message text |
| `tests/test_manager_wake.py` | `MANAGER-LEASE.json`, `MANAGER-WAKE.json`, `SESSIONS.json` | 40, 61, 211, 268, 277 | MENTION | code string / message text |
| `tests/test_merge_slot_guard.py` | `board-pointer.json` | 93, 96 | MENTION | code string / message text; comment or docstring |
| `tests/test_merge_slot_state.py` | `session-board.json` | 7, 157 | MENTION | code string / message text |
| `tests/test_open_prs_settled_regress.py` | `OPEN-PRS.json` | 3 | MENTION | code string / message text |
| `tests/test_order_monitor_close_pnl.py` | `health-review-backlog.json` | 16 | MENTION | code string / message text |
| `tests/test_prop_risk_gate.py` | `system-review` | 3 | MENTION | code string / message text |
| `tests/test_prop_risk_gate_soak.py` | `system-review` | 4 | MENTION | code string / message text |
| `tests/test_pytest_run_filter.py` | `CONSTRAINT.json`, `OPEN-ITEMS.json`, `OPEN-PRS.json`, `SESSIONS.json`, `SUNSET-DISPOSITIONS.json`, `WO-20260901-PHASE-G.yaml`, `WO-20260908-BUILD-THE-N-BOOK-RA | 126, 133, 133, 140, 152, 159, 186, 198, 198, 232, 244, 247 … | READER | code string / message text; comment or docstring; path constant in a list the file reads |
| `tests/test_recurrence_ledger_headline_names_its_cause.py` | `RECURRENCE-LEDGER.json` | 7, 22, 104 | MENTION | code string / message text |
| `tests/test_register_field_loss.py` | `SESSIONS.json` | 11, 18 | MENTION | code string / message text |
| `tests/test_register_ids_census_is_derived.py` | `health-review-backlog.json` | 6 | MENTION | code string / message text |
| `tests/test_reverse_reconciler.py` | `system-review` | 944 | MENTION | code string / message text |
| `tests/test_review_coverage_template_complete.py` | `system-review` | 4 | MENTION | code string / message text |
| `tests/test_review_coverage_triage_gate.py` | `system-review` | 4 | MENTION | code string / message text |
| `tests/test_scope_overlap_attribution.py` | `OPEN-ITEMS.json`, `health-review-backlog.json`, `manager-scope-exception.yaml`, `ml-review-backlog.json`, `performance-review-backlog.json` | 115, 136, 138, 190, 192, 204, 206, 252, 360, 376 | READER | code string / message text; comment or docstring; path constant in a list the file reads |
| `tests/test_scope_overlap_per_branch_files.py` | `OPEN-ITEMS.json`, `session-board.json` | 14, 60, 80, 173, 188, 191 | MENTION | code string / message text |
| `tests/test_session_reaper.py` | `SESSIONS.json` | 164 | MENTION | code string / message text |
| `tests/test_session_receipt.py` | `work/receipts` | 198, 275 | MENTION | code string / message text |
| `tests/test_session_registry.py` | `SESSIONS.json` | 180, 188, 218, 229, 261 | MENTION | code string / message text |
| `tests/test_sweep_stale_automation_prs.py` | `session-board.json` | 392 | MENTION | code string / message text |
| `tests/test_system_review_checklist.py` | `system-review` | 104, 112 | FALSE POSITIVE | `docs/claude/system-review-checklist.json` is a live file that shares the retired skill's name |
| `tests/test_telegram_decisions.py` | `WO-20260901-PHASE-H.yaml`, `work/objects` | 184, 184, 212, 212, 1234, 1234, 1285, 1285 | MENTION | code string / message text |
| `tests/test_uncarried_specs.py` | `health-review-backlog.json` | 37 | MENTION | code string / message text |
| `tests/test_workflow_python_deps.py` | `DUE.json` | 27 | MENTION | code string / message text |

## Table C — the historical record populations (classified at directory level as MENTION)

These directories hold landing records, merge-slot claims, board posts, sprint logs, research memos, audits and rendered reports. Every hit in them is a past PR description, a past claim, or a past finding naming a register that existed at the time. Nothing in them executes or instructs a session. I sampled none of them line-by-line; the class is asserted from what the directory *is*, and that is stated here so a successor can disagree.

| directory | files with hits | hit lines | most-hit needles |
|---|---|---|---|
| `.github/pr-landing/` | 455 | 518 | `health-review-backlog.json` (196), `OPEN-ITEMS.json` (85), `SESSIONS.json` (84), `work/objects` (55) |
| `docs/sprint-logs/` | 164 | 352 | `health-review-backlog.json` (105), `system-review` (79), `OPEN-ITEMS.json` (47), `ml-review-backlog.json` (38) |
| `automation/` | 103 | 177 | `OPEN-ITEMS.json` (56), `health-review-backlog.json` (41), `work/objects` (19), `SESSIONS.json` (18) |
| `docs/research/` | 57 | 130 | `work/objects` (22), `system-review` (20), `research-driver` (15), `health-review-backlog.json` (12) |
| `.github/pr-automerge-requests/` | 54 | 64 | `session-board.json` (15), `health-review-backlog.json` (11), `backlog-drain` (8), `OPEN-ITEMS.json` (7) |
| `comms/reports/` | 27 | 53 | `system-review` (27), `full-system-audit` (14), `health-review-backlog.json` (4), `PROBES.json` (4) |
| `docs/audits/` | 18 | 96 | `full-system-audit` (26), `system-review` (16), `workplan-vs-architecture` (8), `health-review-backlog.json` (7) |
| `docs/design/` | 7 | 30 | `OPEN-ITEMS.json` (10), `SUNSET-DISPOSITIONS.json` (3), `work/objects` (3), `DUE.json` (2) |
| `.github/merge-slots/` | 6 | 8 | `session-board.json` (3), `OPEN-PRS.json` (2), `health-review-backlog.json` (1), `OPEN-ITEMS.json` (1) |
| `.github/register-removals/` | 3 | 6 | `OPEN-ITEMS.json` (4), `SESSIONS.json` (1), `DUE.json` (1) |
| `docs/claude/diagnoses/` | 2 | 3 | `OPEN-ITEMS.json` (1), `SESSIONS.json` (1), `SUNSET-DISPOSITIONS.json` (1) |
| `comms/briefs/` | 2 | 14 | `SESSIONS.json` (4), `CYCLE-PRIORITY.json` (2), `READOUT.md` (2), `SUNSET-DISPOSITIONS.json` (1) |
| `runtime_logs/` | 1 | 1 | `performance-review-backlog.json` (1) |
| `docs/claude/dispositions/` | 1 | 4 | `ERROR-FEED-DIGEST.json` (2), `SESSIONS.json` (1), `OPEN-PRS.json` (1) |

## False positives found and excluded from the counts above

| pattern | where | why it is not a hit |
|---|---|---|
| `sessions.json` inside `recurring_sessions.jsonl` | `src/bot/recurring_dispatch.py:6,27`, `docs/claude/workplan.md:591` | different file; substring match from case-folding |
| `open-prs.json` as `/tmp/open-prs.json` | `.github/workflows/pr-queue-watch.yml:138-319`, `.github/workflows/stale-automation-sweep.yml:135-145` | a temp file the job fetches from the API, not `docs/claude/work/OPEN-PRS.json` |
| `/tmp/night-sessions.json`, `/tmp/list_sessions.json` | `scripts/ops/render_daily_brief.py:116-117`, `comms/briefs/README.md:49-50` | temp files |
| `system-review` inside `system-review-checklist.json` | `.github/workflows/pytest-run.yml:257`, `scripts/ops/system_review_checklist.py:39`, `tests/test_system_review_checklist.py:104,112`, `tests/test_pytest_run_filter.py:294` | `docs/claude/system-review-checklist.json` is a live file that shares the retired skill's name |
| `session-handoff` as `docs/claude/session-handoff.md` / `docs/research/session-handoff-2026-06-01.md` | `docs/claude/session-workflow.md:59`, `scripts/research/regime_tag_emitted.py:16` | live doc / research memo sharing the name |
| `1A-observation-sweep.md` etc. | `docs/DOCUMENT-INDEX.md:282-284` | index rows that point *into* the archive by full path — correct, not stale |

## What I could not determine

- **What `reconcile-open-prs.yml` does on the next push to `main`.** It is armed on `push: main`, runs `scripts/ops/reconcile_open_prs.py --json` then `--apply`, then `commit-to-main` of `docs/claude/work/OPEN-PRS.json`. The script handles a *parse* failure with `::error`; I did not run it against the absent file (it needs a GitHub token to list PRs) and did not find an explicit absent-path branch by grep. It may error, no-op, or write a fresh `OPEN-PRS.json` onto `main`. Five pushes to `main` have happened since the reset without a resurrection commit, which is weak evidence for "no-op or error", not proof.
- **Whether `work-digest.yml` will re-create `docs/claude/work/WORK-DIGEST.json` on `main`.** Its commit step is gated on a `due` output whose computation I did not trace. Same five-push evidence, same caveat.
- **`scripts/ci/check_pr_landing.py` against a real PR.** It is a required CI guard and reads `session-board.json`, `work/approvals/` and `work/objects/`. Its `--self-test` passes (fixtures), but I did not run it against a live diff with those paths absent. Whether a Tier-2 approval record can now be *declared* at all is the question a successor should ask.
- **The ~90 scripts in Table B classified READER mechanically.** They carry a path constant or a read call naming an archived register. I opened none of them. Reachability (which workflow, guard or test still invokes each) was computed by name-grep and is in my working notes, not in this file: of the 94 hit scripts, the ones that no workflow, `run_guards.py` entry, or test references at all are `scripts/ops/backlog_search.py`, `backlog_union_merge.py`, `demote_budget.py`, `in_flight_owner_liveness.py`, `journal_venue_audit.py`, `manager_wake.py`, `pr_action_gate.py` (dead readers; nothing runs them).
- **Set B coverage.** Retired skill names were searched only in the seven reference shapes listed under § Population. Plain-prose mentions ("the duty pass", "a full system audit") are not counted. A successor wanting the prose population can drop the shape constraint and hand-filter the 104 bare `duty` lines.
- **Table C is classified by directory, not by line.** I did not read the 2,000-odd hit lines in landing records, sprint logs and research memos. If a sprint log contains a forward-looking instruction ("next session: drain the health backlog"), it is in Table C as MENTION and I have not seen it.
- **Whether the other lane's list agrees with this one.** By design I did not read it. The diff is the manager's.
- **Line numbers are at `53436a1`.** Anything the fixing lane has merged since will shift them.

## Where this goes

This file is the deliverable. The two readers that cost the most (`.claude/settings.json` hooks; `execution_diagnostics.py:353`) are not filed anywhere by this lane because filing is the other lane's job and double-filing is the failure the plan names. If the manager wants them in the pipeline, the `origin.rerun` is the command block in § Commands.
