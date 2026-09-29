# AUD-8 — operating-model audit (E75, 2026-09-29)

## 1. The question and the answer

**Question:** is the 2026-09-21 reset's promise true today, measured — that an item comes due on the operator's own page and stays there until routed?

**Answer:** the first half is true and the second half is only half true. Established: the live `/api/bot/work/brief` (HTTP 200, 2026-09-29T09:52Z) renders section 0 through `render_section_0()` and lists all 167 due items (167 of 167). So CLAUDE.md's "built but not connected" paragraph is stale. What the promise does not deliver is routing. 143 of the 167 due items are unrouted, up from 85 when row A10 was closed as done. 36 were filed on 09-28 alone, and the oldest unrouted item dates from 09-24. Nothing fails or pings on that growth. Section 0 is also 289 KB of full prose, which cannot be read in one sitting. Four `in_flight` rows name archived, completed lanes. The SessionStart hook and three command files still point sessions at review backlogs that do not exist. Merged, deployed and observed: the brief is observed live. The checklist page serves current main content (175 of 175 rows by id, treeHeadSha equal to main).

## 2. Findings (9; full schema in `AUD-8.findings.jsonl`)

| id | sev | claim |
|---|---|---|
| s0-reaches-operator-page | info | NON-ISSUE: §0 lists 167 of 167 due items live |
| unrouted-backlog-143 | medium | 143 of 167 due unrouted, up from 85 at A10 close; no alarm |
| brief-payload-too-large | medium | brief is 828 KB, §0 289 KB |
| session-hook-stale-health-review | medium | hook and commands cite nonexistent review backlogs and a "MASTER SYSTEM REVIEW" |
| in-flight-rows-archived-lanes | medium | 4 in_flight rows have archived, completed lanes |
| claude-md-pull-not-connected-stale | low | CLAUDE.md:129 says not connected; field says connected (PI-…CAB09-0007 already filed) |
| checklist-freshness-stamp-lag | low | served page says the file was committed 12.8h ago; main has a newer touching commit |
| archived-register-refs-count | low | 2274 files / 4643 lines reference archived registers; not comparable to E29's 40 paths / ~60 files; most are tombstones |
| landed-unproven-observations-named | info | NON-ISSUE: 10 of 10 sampled name the closing observation |

**Stale-instruction question, answered:** yes. The SessionStart hook's "/health-review — MASTER SYSTEM REVIEW … works the three review backlogs" contradicts CLAUDE.md, which retires the roll-up. It also names files that are absent (`ls docs/claude/*-review-backlog.json` finds none).

## 3. Coverage

**Behavioural: 5 of 6 asserted capabilities exercised against real data.**
- Live brief fetched and section 0 counted.
- `pipeline.py --due` and `--stats` run over the store.
- Live checklist fetched and compared to main by id.
- `get_session` on 4 lanes.
- `check_document_index` run.
- Not exercised: a routing round trip, i.e. filing an item and watching it leave section 0.

**Reading:**
- Read: `scripts/ops/pipeline.py` (grep and call sites), `scripts/ops/render_daily_brief.py` (header), `src/web/api/routers/work.py` (brief route), checklist rows A1, A6, A10, B5, E5, E6, E50, E57, E77, FIX-CA-01 and DASH-TRADES (notes), the .claude/settings.json hook text, `.claude/commands/health-review.md`, and health-review SKILL.md lines 664–674.
- Not read: the CA-B09 lane files (cited via the pipeline item only), the operating plan, `.github/workflows/due-list.yml` beyond its header, the 1606 docs and 71 scripts that matched the archived-register grep, and 26 of the 36 landed_unproven rows.

## 4. Could not look

- Lane state for in_flight rows CA, PROMOTE, PROP-EXEC and PROP-TERM (not queried).
- Why `check_stale_in_flight.py` did not flag the four archived-lane rows (not run).
- Commit `5b74155` (the served checklist stamp) is absent from this shallow clone.
- The unrouted trend rests on filing dates from the pipeline/ filenames, not on repeated live reads.
- Diag relay not used; none was needed for this question.

## 5. Spend

Well under the $15 ceiling. About 25 tool calls, no subagents and no relays.
