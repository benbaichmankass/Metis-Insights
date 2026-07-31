# Full-System Audit — 2026-07-31 (post-incident-cluster robustness pass)

> **Program doc (the shared brain)** per `.claude/skills/full-system-audit/SKILL.md`.
> Operator-directed audit session with a specific emphasis: the 2026-07-25→31 arc
> ("roadmap work → broken infrastructure → broken protocols / cloud sessions
> misbehaving"), whether the many fixes are STRUCTURAL or AD-HOC, and the
> robustness of research + training + pipeline + data feeds. Branch:
> `claude/system-infrastructure-audit-5zzdr8`.
>
> **Predecessors:** `full-system-audit-2026-07-26.md` (5 days prior; verdict
> "new infra compliant", surfaced F1–F4) · `full-system-audit-2026-07-09.md` ·
> `full-system-audit-2026-06-28.md` (M17).

## Phase 0 — Rules audit

- Canonical corpus read highest-precedence first (CLAUDE-RULES-CANONICAL in
  full, both CLAUDE.md, ROADMAP head + recent sessions, skill).
- The rules corpus has absorbed the 2026-07-30 incident cluster ("Green is not
  evidence" §, number-provenance §, diagnostic-provenance §) — the yardstick is
  current. No rule-level contradiction found that bends the yardstick.
- **Session-environment note:** this session's clone arrived SHALLOW (50
  commits) — the exact `BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE` hazard.
  Unshallowed via `git fetch --unshallow` before any history-dependent claim.

## Workstreams

| WS | Scope | Mode | Status |
|---|---|---|---|
| A — Recent-arc reconstruction (07-25→31): incidents, fixes, structural-vs-ad-hoc | agent | running |
| B — Backlog triage (3 backlogs, 226 open items) | agent | running |
| C — CI-guard fleet liveness + PR-CI-attachment claim | agent | running |
| D — Research/training pipeline robustness | agent | running |
| E — Consumer repos quick pass (dashboard/android) | agent | running |
| F — VM state (live + trainer) via relays | lead | in progress |
| G — Loose ends (open PRs, stale dispatch issues, board) | lead | in progress |

## Lead findings (WS-F/G) — as they land

### Live VM (diag relay #8173, 2026-07-31T11:01Z) — HEALTHY
- Trader active, heartbeat `running` (19s), `git_sha 0c8faa2b` == `main` HEAD. 52 strategies loaded.
- Account live-map as expected (alpaca_live / ib_live / oanda_practice dry; 8 executing incl. prop).
- IB: 3 clients (497/498 exec + 9229 readonly) all `connected`, `account_data_ready`, no breaker, no wedge.
- DB canonical single-source: `/data/bot-data/trade_journal.db` (658 MB), no table errors. trades 4261 · order_packages 3438 · signals 1.29M.
- `ict-mes-ibkr-pull.service` now `inactive` (07-26 audit had it `failed`) with timer active — apparently recovered; verify last-run success via status-check #8175.
- vm_health: cpu ~0, mem 11.7%, disk 38.1%.

### F-1 (MED, Tier-1 + verify): `ict-ib-executions-pull` invisible to diag — recurrence of the invisible-unit class
- `deploy/ict-ib-executions-pull.{service,timer}` exist and are **load-bearing since 2026-07-30**
  (IBKR broker-truth realized PnL reads `exchange_fills_ib` fed by this hourly timer; see CLAUDE.md
  § number provenance). But the pair is **absent from `diag.py::_CANONICAL_UNITS`** — the same
  "installed but invisible to /api/diag/services + journalctl" class fixed for 3 other timers on
  2026-07-26 and 2 more on 2026-06-28. Live enable-state unverifiable via diag; dispatched
  status-check #8175 to enumerate. **Fix: add the pair to `_CANONICAL_UNITS` (Tier-1).**
  Meta-observation: this class has now recurred on 3 consecutive audits — the structural fix is a
  guard that diffs `deploy/*.{service,timer}` against `_CANONICAL_UNITS`, not another manual add.

### Trainer VM (trainer-diag relay #8174, 2026-07-31T11:01Z) — DEGRADED, needs follow-up
- **Last training cycle FAILED**: `last_cycle.overall_rc = 1` at 10:53:48Z; `manifests_24h: 1 failed, 16 skipped, 157 ok`. A new `run_training_cycle.sh` (pid 790437) was running at probe time. Follow-up relay #8176 dispatched (cause + parentage).
- **The 08:04Z board-warned orphan/lock incident is CLEARED** (pid 784413 gone) — but its trap
  fired as designed: the 07:53 cycle logged `cycle_resumed to_run:0` → `cycle_already_complete` →
  `overall_rc:0` — i.e. **rc=0 while training nothing** ("green is not evidence" in the trainer's
  own exit code; the catch-up semantics make rc=0 ambiguous between "trained" and "nothing to do").
- **`trade_outcomes` and `setup_labels` families built 0 rows** at 07:44 build (also
  `execution_quality` 0, `review_journal` 0) — immediately after the PnL-trustworthiness label
  filter landed. A board comment claims "trade_outcomes 506 rows, verified 07:44". **0 vs 506
  contradiction unresolved** → relay #8176 checks the on-disk dataset. If the filter starves the
  outcome families, outcome-model training is silently dead (the M30 journal-starvation shape).
- **Trainer root disk 86%** (6.6G free of 45G) — approaching the failure zone for dataset builds.
- Registry: 93 models — 2 advisory, 28 shadow, 62 candidate, **1 literal `research_only`** (legacy
  stage name persisted in a row; aliases resolve, cosmetic).
- Trainer on `main` head (`0c8faa2b`) ✅; mirror publish + forecast + git-sync timers all firing ✅.

### G — Loose ends
- **Open PRs: exactly 1** — #8163 (draft, provenance backfill claim-correction, correctly
  operator-gated Tier-2; `--apply` not run). No stale PR debt. ✅
- #8163's content is a material audit fact: **fabricated-exit recovery is capped at ~4%** (13 own-fill
  + 11 mirror-estimated of 327 unmeasured rows) because the exchange-fills store only exists from
  2026-07-13 while fabrication started 2026-06-08 → **~5 weeks of exit PnL permanently UNVERIFIED;
  the exclusion filter is the remedy**, plus the puller's 200-fill no-pagination trap
  (`BL-20260731-FILLS-STORE-PREDATES-THE-FABRICATION`).
- Coordination board: no live concurrent sessions at audit start; last sessions wrapped cleanly with
  DONE posts (protocol functioning, including self-owned protocol misses posted as corrections).

### Status-check #8175 (live VM, 11:03Z) — prior-audit F-items re-verified
- **F1 (mes-ibkr-pull failed) → RESOLVED**: unit `inactive/dead` between fires, timer enabled+active; no failed ict-* unit anywhere in the full enumeration.
- **F2 (352 stranded Claude pings) → RESOLVED**: both inbox dirs (`/data/bot-data/...` and repo-path) read **0 queued**.
- **F3 (bybit_2 smoke `place_order` NoneType) → STILL OPEN + RECURRING**: 3 more failures 07-30T19:39 / 07-31T03:24 / 07-31T08:55. A failing smoke on a real-money account firing ~every 5–6h and being walked past is an **alarm-fatigue candidate** per the operator's own rule.
- **`ict-ib-executions-pull.timer` VERIFIED enabled + active/waiting** on the live VM — the IBKR broker-truth feed is alive; only the diag allowlist gap (F-1 above) remains.
- **NEW OBS (check): `ib_paper` MGC position = 99 contracts** (~$4.0M notional, uPnL −$15.2K, IB paper acct DUQ…) visible in `updatePortfolio` journal line. Consistent with the historical orphan-flap accumulation class (the +$284K fabricated `orphaned` ib_paper rows). Needs a journal-vs-broker reconciliation check — paper-only, but it poisons ML/trainer data and analytics denominators.
- **NEW OBS (low): `mgc_pullback_1d` emitted intent `target_qty=0.000000`** — the 0-qty intent → orphaning-package class noted 07-26 (WS-G) is still producing 0-qty intents.
- journald cgroup memory ~4.0G (F4, unchanged, low).

### Trainer follow-up #8176 (11:03Z)
- **rc=1 cause**: manifest `btc-regime-15m-lgbm-vt004-pcv-v1.yaml` fails `ManifestDatasetMismatchError` — `dataset.build_params` disagree with recorded metadata at `datasets-out/market_features/BTCUSDT/15m/`. The new declared-build-params guard is WORKING (refusing a mismatched pair); the manifest/dataset needs reconciling so the head can train again.
- **Outcome-label families effectively DEAD in the nightly build**: `datasets-out/trade_outcomes/` + `setup_labels/` newest content is **2026-05-22**; the nightly build logs `"ok" row_count 0` for trade_outcomes, setup_labels, execution_quality, review_journal. A green build over 0 rows for ~10 weeks = sub-class-C in the trainer's own pipeline. (The 07-31 provenance session's "506 rows verified" claim vs the nightly's 0 needs reconciling — likely different invocation/args; the on-disk truth is stale-May.)
- **Trainer cycle-dispatch discipline**: 3× `cycle_locked` no-op exits + overlapping starts (09:21, 10:09) from different sessions this morning; `rc=0` is ambiguous between "trained" and "lock-skipped/nothing-to-do" — sessions must check `to_run`/`cycle_locked`, and a structural fix (distinct rc or a `trained_count` assertion) is warranted.
- **Trainer root disk 86%** (6.6G free) — approaching dataset-build failure territory.
- Registry stage row with literal legacy `research_only` (1 model) — cosmetic alias cleanup.

### WS-C — CI guard fleet (agent + lead verification) — WIRING GOOD, ENFORCEMENT PARTIAL
- **Branch protection VERIFIED LIVE** (bp-report #8177, live `gh api` read): 11 required contexts
  (pytest-collect/run, secret-scan, ruff-lint, dry-run, env-gate, silent-empty,
  canonical-config-loaders, canonical-db-resolver, provenance-consumer, diagnostic-provenance),
  `strict: true`, `enforce_admins: true`. The required-check spine is REAL.
- **BL-20260730-PR-CI-NOT-ATTACHING largely resolves**: the "no PR CI on any PR" claim was already
  retracted in-repo; the bp-report confirms protection applied. Residual: one confirmed dropped
  `pull_request` webhook delivery (GitHub transient) + the `get_status` legacy-API tooling trap
  (`total_count: 0` always — use `get_check_runs`). → update the backlog item with #8177 evidence.
- **Latent hazard (MED, Tier-1): `branch-protection-sync.yml` fails OPEN on a missing
  `BRANCH_PROTECTION_TOKEN`** (green run, no PUT, `::notice` only) — protection would silently
  drift cosmetic. Fix: make the missing-secret path a red run (or an alert).
- **MED, Tier-1: `macro-producer-liveness.yml` vacuity half is wired to NOTHING** — `steps.validity.outputs.rc`
  referenced nowhere; a vacuous artifact on the daily run produces no alert/issue/red. (Also the
  `rc=$?` after a `| tee` pipeline captures tee's status.) This is the daily dead-man for the
  BL-20260730-M1-PRICE-JOIN-DEAD class failing exactly as silently as its target class.
- **Enforcement census: 13 of 24 guard workflows are advisory** (can merge red), including 2 of the
  6 the rules doc names "in force" (`new-table-wiring`, `canonical-doc-coherence` — the latter also
  `paths:`-filtered so it doesn't even run on most PRs). `soak-doctrine-guard` header FALSELY claims
  it is required. `arch-doc-guard` always exits 0 by design.
- **merge-queue time bomb: 8 guards `git fetch origin ${{ github.base_ref }}`** under `set -e` —
  empty `base_ref` on `merge_group` events → the queue stalls on day one if enabled (each workflow's
  comment claims the opposite).
- **Doc drift**: `docs/github-actions-workflows.md` still publishes the 9-entry required list
  (missing the 2 provenance guards promoted 2026-07-30).
- Dead scripts (unwired anywhere): `scripts/ops/provenance_exposure_audit.py`,
  `scripts/ops/dataset_unchanged_check.py`. All hook-referenced scripts exist; all workflow-referenced
  scripts exist (2 stale comment-prose refs in `health-snapshot.yml` only).

### WS-E — Consumer repos (agent) — 2 HIGH findings
- **HIGH (doc drift): the dashboard repo's own CLAUDE.md does not mention the Svelte SPA at all**
  (0 hits for svelte/webapp) while `webapp/` (21 routes, Svelte 5 + Vite) has been auto-deploying to
  GitHub Pages on every push since 2026-07-16 (`.github/workflows/pages.yml`) and is the Telegram
  deep-link target. The doc still calls the Streamlit URL "canonical", claims a single-app deploy
  model, and falsely claims mirroring with the bot repo's CLAUDE.md (which IS correct). `webapp/`
  has NEVER been audited (both consumer audits predate it).
- **HIGH (provenance-blindness): both consumers render fabricated realized PnL as truth.**
  Provenance discipline exists for UNREALIZED PnL only (`unrealizedPnlSource` surfaced in all 3
  frontends). `ClosedTrade` carries NO provenance field in either client; android's
  `realizedPnlOrZero` and the webapp's `filter(pnl != null)` fold fabricated rows into win rates,
  net P&L, calendars, per-asset breakdowns; `/api/bot/performance` headline numbers (computed over
  the ~65%-fabricated July population) render with no caveat. Any bot-side provenance field would be
  silently dropped by both clients (kotlin `ignoreUnknownKeys`; webapp normalizer reshapes).
  **A bot-side + both-consumers coordinated change is required** (e.g. `pnlProvenance` +
  measured-coverage % on `/trades/closed` + `/performance`).
- Webapp parity gaps vs Streamlit undocumented (no Learning route; `Promotion.svelte`/`Models.svelte`
  ship without the `/shadow/drift` + `/ml/*` feeds — render check needed). `/api/bot/pnl/broker-truth`
  consumed by Streamlit only. Cross-repo paired commits show parity is otherwise deliberate.

### WS-A — Recent-arc reconstruction (agent; 21 sprint logs 07-26→31 + 217 commits)
The operator's three-phase description maps cleanly: roadmap work (07-26→28: M28/M30/M36 +
S-ROADMAP-RECONCILE) → broken infrastructure (07-29→30: 25× venue-fee over-charge, missing harness
levers, bracket/exit plumbing, the fabricated-exit-PnL root cause) → broken protocols/clouds
(07-30→31: research skills mis-routing, PR-CI attachment scare, trainer-diag executing issue prose,
claim-surface defects). **28 distinct incidents catalogued** (see agent detail; timeline retained in
session record). Highlights: the "−$6,358 scalp exit leak" did not exist (mark-at-sweep-time
fabrication; real 7d figure −$1.91); `BYBIT_TPSL_MODE=partial` had been live 9 days while a
/system-review deep-dive assumed `full`; a `# _UNREPLAYABLE` comment scoped to one harness was read
as a law of nature and nearly closed off six live cells as "permanently unmeasurable".

**Structural-vs-ad-hoc verdict: roughly 2/3 structural.** The window produced 2 new REQUIRED CI
guards (provenance-consumer, diagnostic-provenance), 4+ advisory guards, 6 owned single-source
modules (`provenance.py`, `exit_anchor.py`, `_regime_score_semantics.py`, `git_history_check.py`,
`roundtrip_fee_bps_for`, RESEARCH-CAPABILITY-INDEX), 4 hooks, and 2 binding rule sections. The
recurring failure patterns are now NAMED in canonical docs: (a) green-but-measured-nothing,
(b) label≠computation, (c) correct-local-fix-silently-generalised, (d) confident-doc-never-verified,
(e) small-window generalisation, (f) the-enforcement-tool-subject-to-its-own-rule.
**Where it stays ad hoc / uncoordinated:** the claim surface (numbers in backlog rows/ROADMAP cells
that Tier-3 decisions are made from) has NO preventer despite being named critical; cells shipped
live on evidence now known defective have no retraction path (re-audit register untouched);
detect-only shipped where remediation is needed (leg over-accumulation 444.7%→830%/41min); lever/
harness coupling still hand-maintained; and the largest single output of 07-30/31 is prose — the
same control class that already failed that week. Model to copy: the provenance chain + the
S-OFFLINE-VOL-AXIS self-refutation chain (root cause → owned module → required guard → honest
population statements).

### WS-B — Backlog triage (agent)
- **True open counts: health 126 / perf 51 / ml 37 = 214** (541 total items). **63% of open items
  carry NO severity or priority** — triage-by-severity is not possible as filed.
- **Accumulating, not draining: net-open grew EVERY month in ALL three backlogs** (July: health +66,
  perf +13, ml +21). Resolution latency is fast (median 3–9 days) — the system is out-FILED, not
  slow. The 07-30 session alone filed 78 health items (60 still open) = **48% of the open health
  backlog is one day's output** — "not a work queue; an unread transcript."
- 5 explicit critical/P1 open items; ~19 more high items that are substantively equal.
  `BL-20260730-PR-CI-NOT-ATTACHING` titles itself P1 but is filed medium (mis-triage; now largely
  resolved by bp-report #8177 — needs an update entry).
- **Perf backlog structurally cannot drain**: 59% of open items predate July, mostly "monitor/revisit
  when n accrues" with no trigger automation; standing watches parked as backlog rows permanently
  inflate the count (alarm fatigue operating on the backlog itself).
- Confirmed duplicates (2 pairs, the OPEN copy is the stale one), 7+ near-duplicate clusters, 5-way
  status vocabulary (`open/kept_open/partially-resolved/fix_landed_monitoring/in_progress`),
  namespace leaks (BL- ids in the ml backlog), 9 items with empty titles.
- **Contradiction triangle (Bybit brackets)**: three open items give incompatible pictures — naked
  position possible / 830% leg over-coverage / journal 50× the exchange position (SOLUSDT) — and the
  audit tool that should adjudicate is the one known to print a false all-clear. + the lead's MGC
  ib_paper 99-contract observation: one reconciliation pass must settle all of these together.

### WS-D — Research/training robustness (agent) — the highest-consequence gaps
- **G3/THE headline structural risk: `ml/promotion/attribution.py` — the PROMOTION GATE reads
  unfiltered `pnl > 0`** while the trainer (bc75dd1) now drops 39.3% of those rows as untrustworthy:
  a model can be trained on the clean population and promoted on the 65%-fabricated one. Nothing
  reconciles the two.
- Provenance filter is per-family, not per-boundary: `setup_candidates` (M23 real-money holdout, 10
  manifests), `conviction_meta`, `regime_alignment` calibrators, `build_research_panel`,
  `strategy_review_packet` (KILL/PROMOTE badges) all read raw `pnl > 0`. Only 6 of 54 research
  scripts reference provenance at all. **Fix shape: one shared `trusted_closed_trades()` reader +
  extend provenance-consumer-guard to new `FROM trades…pnl` sites.**
- Dataset-audit alarm fatigue (62/86) is genuinely FIXED and now enforced — but the enforcement
  introduces **silent-skip drift**: 4 independent "correctly skip this manifest" paths, no
  "manifest X hasn't trained in N cycles" escalation, no test on the FLAGGED→skip branch.
- Gates: regime profile correctly has NO required calendar-edge gate (CI-pinned). Fragility: profile
  auto-detect fallback ("≥2 f1_ metrics") can route any multiclass head into the looser profile.
- `_regime_score_semantics.py` adoption partial: the parity probe still carries a second
  implementation; `regime_alignment.py` + `conviction_inputs.py` consume `max(proba)` unlabelled.
- **G11: 25 of 38 `market_features` dirs record no `vol_threshold`** — including `BTCUSDT/15m/v520`,
  the dataset pinned by the LIVE real-money BTC vol-gate head. Two label definitions
  (0.003 → 16.04% volatile vs 0.005 → 4.6%) with nothing on disk distinguishing them.
- G7: no test asserts `build_trainer_datasets.sh` passes `exclude_fabricated_pnl` — the exact
  half-registration class that bit on 07-31.

## Consolidated verdict

1. **The live trading spine is healthy and the recent fixes there are real**: trader on current
   `main`, IB connected, canonical DB single-source, branch protection genuinely enforcing 11
   contexts, no failed units, prior audit's F1/F2 resolved. Zero Prime-Directive/Tier-3 violations
   found this pass.
2. **The crisis of the last week was epistemic, not mechanical** — the system's numbers, labels and
   diagnostics were lying in correlated ways (fabricated PnL 0%→65% of July closes; inverted probe
   labels; vacuous green artifacts). The response was MOSTLY structural (owned modules + 2 required
   guards + named failure classes) — this is the most coordinated fix-arc in the repo's history, not
   scattered patching. The provenance chain is the model.
3. **But the fixes stop one boundary short in three places**: (a) the promotion/research read-side
   still consumes poisoned PnL (G1–G6); (b) consumers render fabricated realized PnL with no
   caveat and would drop any provenance field silently; (c) the claim surface (the numbers Tier-3
   decisions quote) has no preventer. Until those close, the same class of mind-boggling bug WILL
   recur — visible earlier, but recurring.
4. **Decisions made on pre-provenance evidence have no retraction path** — the authored-cell
   re-audit register is open/untouched while three separate defect classes (venue fees, self-erasing
   queues, feed sensitivity) implicate already-shipped cells.
5. **The backlog system is saturated** — 214 open items, growing every month, 63% unranked,
   duplicates and contradictions live; it is becoming the place findings go to be forgotten, which
   is the alarm-fatigue failure mode applied to the fix pipeline itself.
6. **Trainer needs attention**: failed vt004 cycle (manifest/dataset mismatch), outcome families
   dead-but-green since May, disk 86%, rc=0 ambiguity, morning lock contention between sessions.

## Prioritized work plan → see the operator summary (chat) — mirrored here

**P0 — close the poisoned-number loop (1–2 sessions, mostly Tier-1/2):**
P0.1 boundary-level `trusted_closed_trades()` reader + wire into attribution/setup_candidates/
conviction_meta/regime_alignment/research_panel/strategy_review_packet + guard extension.
P0.2 operator decision on PR #8163 + the Tier-2 relabel pass (accept ~5wk unrecoverable).
P0.3 coordinated 3-repo provenance surfacing (`pnlProvenance` + coverage on /trades/closed +
/performance; consumers render the caveat; the field must land in BOTH clients or it is dropped).
P0.4 authored-cell re-audit register + re-grades (venue-fee, gld_pullback_1h feed-sensitivity).

**P1 — trainer honesty (1 session, Tier-1/2):**
P1.1 fix vt004 ManifestDatasetMismatch; P1.2 backfill/annotate the 25 vol_threshold-less dataset
dirs (or mark UNKNOWN); P1.3 outcome-family starvation: reconcile 0-vs-506, decide fate of the
May-dead families, add "manifest untrained N cycles" escalation + FLAGGED-skip test; P1.4 disk
cleanup; P1.5 distinct rc/assertion for "cycle trained nothing".

**P2 — enforcement coherence (1 session, Tier-1):**
P2.1 fix `merge_group` base_ref bug in 8 guards THEN promote artifact-validity(+ soak-doctrine,
json-extract, layer-guard) to required; P2.2 wire `macro-producer-liveness` vacuity output (orphaned
`steps.validity.outputs.rc`); P2.3 make `branch-protection-sync` fail red on missing token;
P2.4 diag allowlist: add ib-executions-pull + a deploy/*.timer↔_CANONICAL_UNITS diff guard (3rd
recurrence of the class); P2.5 claim-surface preventer (BL-20260731-CLAIM-SURFACE-UNGUARDED P1–P4).

**P3 — hygiene (1 session, Tier-1 + one Tier-2 batch):**
P3.1 backlog consolidation sprint (severity-normalize, merge dupes, extract standing watches,
define status vocabulary, structured triage of the 07-30 dump); P3.2 doc reconciliation (dashboard
CLAUDE.md Svelte SPA, github-actions-workflows.md 9→11 required list, soak-doctrine header claim);
P3.3 ONE Bybit/IB reconciliation pass settling the bracket-contradiction triangle + journal-phantom
rows (SOL 50×, BNB, ib_paper MGC 99-lot) with Tier-2 remediations; P3.4 bybit_2 smoke NoneType +
0-qty intents.

## Coverage honesty
Agents read: 21 sprint logs (07-26→31), 217 commits, 3 backlogs in full, 120 workflows (24 guard-
type in depth), research/ML gate + family code, consumer repos (endpoint sweep + provenance sweep;
`webapp/` first-ever audit pass — render-level check NOT done). Lead verified: live VM + trainer VM
via 4 relays + bp-report, board, open PRs. NOT reached this pass: per-line sweep of `src/` (last
done 07-26, unchanged verdict assumed NOT re-verified), gateway VM state, dashboard Streamlit
render-level checks, prop journal deep-audit.

## Coverage map

- Lead: CLAUDE-RULES-CANONICAL.md (full), CLAUDE.md ×3 (session context), ROADMAP.md head +
  recent-sessions, full-system-audit-2026-07-26.md, board #6927 tail (60 comments), diag relays
  #8173/#8174/#8175/#8176, PR #8163, `src/web/api/routers/diag.py` (allowlist region),
  `scripts/ops/status_check.sh` (header).
- Agents A–E: appended on completion.
