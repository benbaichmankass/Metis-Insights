# Sprint Log: S-OPERATING-MACHINERY-AUDIT-2026-08-31

## Date Range
- Start: 2026-08-30
- End: 2026-08-31

## Objective
- Primary goal: **Audit how the operator works, then close the gaps the audit found** — the skills, the GitHub Actions, the guards, the registers, the review sessions. Operator-directed: produce the report/audit FIRST, plan second, build third. The organising ask: *"I expect Claudes to do as many actions autonomously as possible; operator-initiated actions are always a last resort, but decisions can always come to me."*
- Secondary goals:
  - A durable, readable map of the machinery the operator can refer back to (not a chat message that scrolls away).
  - Land the first work-plan items rather than only cataloguing them.
  - Answer the standing question of whether the half-built local LLM should carry weight — by measuring it, not by predicting.

## Tier
- **Tier 1**, with two Tier-2 exceptions carried out under explicit operator approval in-conversation.
- Justification: everything shipped is CI, tooling, docs, observability and read paths — no `src/`, no `config/`, no order path, no live-VM mutation. The two Tier-2 actions were (a) the **trainer VM reboot** (a service-affecting mutation on the autonomous-territory VM, approved via popup) and (b) nothing else — the live trader was never touched this session.

## Starting Context
- Active roadmap items: none of this sprint's work was on the roadmap at start — it was originated by the operator's audit directive.
- Prior sprint reference: `docs/sprint-logs/S-WORKPLAN-G1-2026-08-26.md`, `S-WORKPLAN-GATE0-2026-08-26.md`.
- Known risks at start (stated by the operator, all confirmed real):
  - Detected problems land in a backlog and then **nobody owns the disposition**.
  - Concurrent sessions still race despite infrastructure meant to prevent it.
  - Review sessions re-derive live-VM state by hand instead of reading a probe that already checked.
  - Skills lack checklists.

## Repo State Checked
- Branch or commit reviewed: `origin/main` at session start; every claim below re-verified against `origin/main` at close (`git merge-base --is-ancestor`, not the merge API's response).
- Deployment state reviewed: live trader NOT touched. Trainer VM read + rebooted (below). `/api/diag/*` read over the Caddy HTTPS host.
- Canonical docs reviewed: root `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `ROADMAP.md`, `docs/claude/OPEN-ITEMS.json`, `docs/claude/coordination-board.md`, coordination board issue #6927.

## Files and Systems Inspected
- Code files inspected: `scripts/ci/check_scope_overlap.py`, `scripts/ops/render_due_list.py`, `scripts/ops/llm_capacity_benchmark.sh`, `scripts/ops/backlog_append.py`, `scripts/ci/run_guards.py`.
- Config files inspected: none changed (deliberately — no Tier-3 surface was in scope).
- Deployment files inspected: none changed.
- Docs inspected: root `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `docs/claude/OPEN-ITEMS.json`, `docs/claude/PROBES.json`, `docs/claude/DUE.md`, the three review backlogs, `.claude/skills/*/SKILL.md` (full catalog).
- Services or timers inspected: on the trainer VM, the 9 enabled `ict-*` units (before and after reboot), `systemctl --failed`, `/proc/uptime`, kernel version, disk free.
- GitHub Actions workflows inspected: **124 workflow files enumerated**; changed `pytest-run.yml`, `pytest-collect.yml`, `guards.yml`, `scope-overlap-audit.yml`. Read `merge-claim-audit.yml`, `probes.yml`, the research/corpus workflows (artifact-retention question, below).

## Work Completed

### The audit itself
- **The central finding — the disposition gap.** The lifecycle is *detect → land → come due → get a disposition*. Steps 1 and 2 are genuinely strong here (64 guards, 13 registers, 501 scripts, 124 workflows). **Step 4 had no owner.** A signal that fires, lands in a backlog, and then comes due has nothing that routes it to a decision. That is one description covering the operator's four separate complaints.
- **Four lanes** as the organising frame — Clock / Event / Session-start / Human — with the design rule that work should move *leftward* toward autonomous, and the human lane reserved for **decisions**, never for fetching.
- **Published artifact**: https://claude.ai/code/artifact/929c5267-a297-40a3-9b91-b8c3b39bd813 — masthead + TOC, "Which session should I run?" (recurring-cadence table + build table), full skill catalog split guardrails/libraries, the four lanes, the UTC day rail, the automation catalog, the disposition chain, a status board, and the 5-item work plan.
- **Linked from the SPA** so it is reachable without the chat: new **Admin → Runbooks** page (dashboard PR #209, `25556fab3`). It makes **no API call, deliberately** — it is the page you reach for when the bot is down.

### Shipped (all eight verified as ancestors of `origin/main`)
| PR | SHA | What |
|---|---|---|
| #10582 | `dca4af6df` | Give every detected signal an owner: one due-list, and probes that report. |
| #10584 | `dbed6a655` | W3 — tell a PR when it touches a file another live session declared. |
| #10588 | `9282fd533` | Make the LLM-capacity benchmark's failure paths legible. |
| #10590 | `1a0b644af` | Drop `ready_for_review` from the three required checks. |
| #10592 | `9cec69d5e` | scope-overlap: take the base **tip**, and make a missing tool `could_not_check`. |
| #10594 | `76da74d77` | scope-overlap: three-state attribution — stop reporting a session's own START back at it. |
| #10597 | `65c7806a2` | llmbench: probe for the single-shot flag; stop printing `benchmarked` over a dead step. |
| #10600 | `906da68f5` | due-list: grade the probe report's AGE — **work-plan item 1**. |

Plus dashboard `benbaichmankass/ict-trader-dashboard` #209 (`25556fab3`), and **#10605 in flight** at close (`LLMBENCH_MODEL_URL` — makes the different-size arm dispatchable).

### W3 — the plan was wrong, and the measurement said so
W3 was planned as a **merge serializer**. It was not built, because the premise was refuted. Over `2026-08-30T19:13Z → 2026-08-31T12:53Z`, **39 merges** landed on main — one every 27.3 min, **median 15.9 min** between merges from different sources. Nothing was racing for the merge button, and `require-up-to-date` has been off since 2026-08-10, so one merge does not invalidate another PR's checks. The one real collision (#10582 going `dirty`) was caused by #10579 and #10580 landing under it — **23 minutes apart, already serial**. Serialising merges would not have prevented it; the branch was simply old.

Nor was it under-declaration: the other session's 11:41Z START named `docs/claude/OPEN-ITEMS.json` explicitly. **What fails is that a declared scope never reaches a session that is already running** — the PreToolUse guard that would catch it is never invoked on Claude Code on the web (`BL-20260820-PROJECT-HOOKS-INERT-ON-WEB`). So the shipped thing carries information that already exists to the one surface a running session cannot miss: **its own PR**. It gates nothing, deliberately.

### The CI-waste measurement
`ready_for_review` was in `types:` for `pytest-run`, `pytest-collect` and `guards`, so every draft→ready transition re-ran all three from scratch. Removed. `REQUIRED_CONTEXTS` untouched — the checks still run on `opened`/`synchronize`/`reopened`, which is every code change. Each file carries the measurement and the RE-ADD condition inline.

### W6 P1 — the local LLM, measured
Ran `scripts/ops/llm_capacity_benchmark.sh` on the trainer VM after fixing two legibility defects in it (#10588, #10597). Post-reboot run reached `RESULT: benchmarked`; the flag probe selected `--single-turn`.
- **Throughput**: `pp128 22.35 ± 0.24`, `tg64 7.19 ± 0.34` t/s (pre-reboot `22.53 / 7.38` — identical within noise).
- **Footprint**: 36.4 s wall clock, **2.86 GB peak RSS on a 5.9 GB box**.
- **Quality, against a real repo question** — and this is the finding, not the speed. The sample **restated the prompt**, **fabricated a causal claim** (*"margin was calculated from the per-coin block, indicating that the available balance is less than the required margin"* — two unrelated facts joined by an invented "indicating"), **misattributed the fault to venue liquidity** when the defect was our own sizer reading an empty account-level field, and **truncated mid-sentence**, never answering the third of the prompt that asked what to check next.
- **Conclusion**: throughput is adequate; the answer is not. A model that confidently invents a cause is worse than no model on a system whose canonical rule is *always verify*.

### Trainer VM reboot (Tier-2, operator-approved via popup)
Kernel `6.8.0-1057` → **`6.8.0-1060-oracle`**; boot at 15:54:28Z. **9/9 enabled `ict-*` units returned active**, `systemctl --failed` empty, `NEEDRESTART-KSTA: 1`, no reboot pending. Disk free **6.2G → 7.4G** (~1.2 G released from handles held across a 6½-week uptime). `ict-orderflow-capture.service` was active throughout.

## Validation Performed
- **Tests run**: `scripts/ci/check_scope_overlap.py --self-test` **28 → 35** cases; `scripts/ops/render_due_list.py --self-test` **13 → 26** cases. Full CI (`pytest-run`, `pytest-collect`, `guards`, `repo-inventory`) green on every merged PR.
- **Dry-runs / staging checks**:
  - `render_due_list.py` live-graded against the real repo: `freshness=fresh age=3.6h cadence=daily (cron 20 5 * * *)`.
  - `llm_capacity_benchmark.sh`: `bash -n` clean, and **all three override paths exercised against a stub `curl`** — (a) no override → default serves, no warning; (b) live override → serves, default never tried; (c) **dead override → falls back AND emits the warning naming the requested URL** (the load-bearing case).
  - Trainer post-reboot state read back from the VM, not assumed.
- **Manual code verification**: every merged SHA re-checked as an ancestor of `origin/main` with `git merge-base --is-ancestor` rather than trusting the merge API response.
- **Reproduce-then-fix, twice**: the scope-overlap false positive was reproduced (`overlap`, 3 hits) before the fix and re-run after (`no_overlap`, 0 hits). The #10588 `audit` red was root-caused to `base.sha` predating the script before #10592 changed it.
- **#10590's claim verified twice**: (a) on its own head — zero new runs of the three, one new passing `audit`; (b) on #10594, whose merge ref postdated the change — again zero new runs. The two readings **together** establish that the benefit reaches PRs branched after the change and not before.
- **Gaps not yet verified**:
  - **#10605 was still in flight at the moment this log was written** — see *Risks and Follow-Ups*; the merged state is asserted only if the closing board comment says so.
  - The **different-size LLM arm has NOT been run**. #10605 only makes it dispatchable. The 3B-halves-the-speed / 0.5B-does-the-reverse expectation is **arithmetic, not a measurement**, and must not be quoted as a result.
  - **Superseded during this session's own wrap-up — the detector's first genuine catch landed at 17:26:46Z on this sprint's own PR #10607**, correctly naming `claude/research-queue-landing-v4yfq7` and `claude/research-disposition-resweep-cplgnv` as having declared `docs/claude/OPEN-ITEMS.json`, `ROADMAP.md` and `docs/sprint-logs/`. The overlap was verified harmless (their work had already merged as #10602/#10604; `git merge-tree` against `origin/main` was CLEAN). **The same comment also exposed a residual defect**: it reported this session's *own* START back at it, because `attribution()` compares the START's branch to the PR's branch and a session posts ONE START while opening PRs from SEVERAL branches — the same class as #10594 in a narrower form, and the common shape rather than an edge case. Filed as `BL-20260831-SCOPE-OVERLAP-ATTRIBUTOR-GRADES-A-SESSIONS-OWN-START-AS-ANOTHERS` with the proposed session-suffix fix, and as `OI-20260831-SCOPE-OVERLAP-ATTRIBUTOR-MISREADS-A-SESSIONS-OWN-START` so the next session reads an overlap comment expecting one line of it to be its own. Not fixed here: the fix must only ever ADD to `mine` and never suppress an `other`, which needs a negative control, and that is more than a wrap-up should build.
  - Probe coverage is **3 of 11** monitored items. The 8 uncovered ones still require a manual pull. Unquantified: how much review-session time that actually costs.

## Documentation Updated
- Rules doc updates: none — no rule changed.
- Architecture doc updates: none — nothing shipped affects a schema, boundary, stage or contract.
- Trade pipeline doc updates: none — no pipeline stage was touched.
- Roadmap updates: this log is the record; the sprint is not a milestone.
- GitHub Actions doc updates: the three CI workflows and `scope-overlap-audit.yml` each carry the reasoning **inline as comments** — including, in `scope-overlap-audit.yml`, the full record of why W3 was *not* built as planned, so the next session does not re-propose a merge serializer.
- Subsystem doc updates: dashboard `webapp/src/lib/nav.ts` + `App.svelte` wiring for the new Runbooks page.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **W4's premise was refuted by measurement.** I expected research findings to be trapped in expiring GitHub artifacts. All 16 checked land readably — because the research workflows `tee` to `$GITHUB_STEP_SUMMARY` rather than `>>` it (`BL-20260824-A-STEP-SUMMARY-IS-INVISIBLE-TO-THE-ACTIONS-API`). **The published artifact was corrected in place** rather than left carrying a disproved claim.
- **`pull_request_target` reads the workflow definition from the BASE branch**, never the PR head — so porting the `scope-overlap-audit` fix onto #10588's own branch would have been inert for #10588's own run. One standing comment was posted on #10588 saying exactly that, so the next reader does not try it.
- **For `pull_request` events GitHub evaluates workflows from the merge ref**, which is not recomputed the instant base moves. This is why merging #10590 first did **not** prevent the three from re-running on #10592's ready transition.
- **Two things I told the operator were wrong and were corrected in-session and on the board**: (1) that merging #10590 first would prevent #10592's re-runs (it did not, per the merge-ref behaviour above); (2) that #10594's `pytest-run` was running at ~2× the norm — it ran 15:30:08→15:44:01 ≈ 14 min, which is normal. The second was my misjudging wall-clock time, not a data problem.

## Risks and Follow-Ups
- **Remaining technical risks**:
  - **Probe coverage 3 of 11.** The disposition chain is only as autonomous as its measured fraction; 8 monitored items still bottom out in a manual VM pull. This is the single largest remaining gap and is work-plan **item 3**.
  - The scope-overlap detector is **non-blocking by design** and has not yet caught a real cross-session overlap. It could be correct and never fire; that is not the same as being verified.
- **Remaining product decisions (Tier 3)**: none opened by this sprint.
- **Blockers**: none.

## Deferred Items
- **W5 — splitting the root `CLAUDE.md`.** Deliberately not started. It is a large, high-blast-radius doc change and the audit did not establish that its size is currently costing anything measurable.
- **W7 — backlog schema.** Deliberately not started, same reasoning.
- **Widening `llm-delegate`'s scope guard.** Offered and **declined by the operator**. It is default-deny and denies `*.db`, `*.sqlite`, `runtime_logs/*`, `runtime_state/*`, `comms/*`, `config/*`; one denied path refuses the whole batch. Widening it is **a security decision, not a convenience one** — it stays as is.
- **The different-size LLM benchmark arm** — enabled by #10605, not run.

## Next Recommended Sprint
- **Suggested next sprint**: **work-plan item 3 — probe coverage, 3 of 11 → more.** Operator-selected via popup.
- **Why next**: it is the load-bearing half of the operator's original ask (*"instead of the session review having to do a whole pull of the live VM, we can just see that a test we set up … passed or didn't pass"*). Items 1 and 2 built the reporting; item 3 is what makes it cover anything.
- **Required verification before starting**:
  1. Confirm **#10605 is merged** on `origin/main` before building on this branch — and start from a **new** branch off `origin/main`, not this one.
  2. Read `docs/claude/PROBES.json` and `docs/claude/OPEN-ITEMS.json` **first** — the 8 uncovered items are named there; do not re-derive the list.
  3. For each candidate probe, the bar is the one this repo already sets: it must be able to say **"we did not look"** distinctly from **"we looked and found nothing."** A probe that collapses those two is worse than no probe.
  4. The second, separable arm: dispatch the different-size LLM run via `LLMBENCH_MODEL_URL`. Compare on **output quality against a real repo question**, not tokens/sec — the 1.5B's throughput was adequate and its answer was not.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded — including the two claims of mine that were wrong.
- [x] Remaining unknowns were stated clearly, in *Gaps not yet verified*.
