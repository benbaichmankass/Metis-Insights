# Milestone & session state

> **Purpose:** single quick-glance answer to "where is the program right now?"
> for future Claude sessions. Read this **after** `checkpoints/CHECKPOINT_LOG.md`
> (which tells you where to resume tactically) but **before** opening any sprint plan.
>
> **Authority:** the S-CANON-1 canonical doc set
> (`docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`,
> `ROADMAP.md`, current sprint log) is authoritative as of
> 2026-05-10. This file tracks execution state against the M0..M10
> roadmap captured in those docs. The legacy
> `docs/claude/workplan.md` is preserved for historical context but
> is no longer the decider. When this file conflicts with the
> canonical set, the canonical set wins.
>
> **Update rule:** the closing checkpoint of every sprint updates this file.
> If the file is stale, the resuming session should refresh it before doing
> any other work.

---

## How to read this file

1. **Active milestone** — the one milestone currently being worked.
2. **M0..M10 status table** — on-disk-verified status for every milestone.
3. **Recently closed milestones** — last three closed milestones.
4. **Queued milestones** — what's lined up next in workplan order.
5. **Standing / recurring sessions** — auto-task milestones on a cadence.
6. **Open blockers** — anything the operator owes the program.

When opening a session:

- If the **Active milestone** points at a sprint with an open checkpoint, resume
  that checkpoint per `checkpoint-workflow.md`.
- If the **Active milestone** has no open sprint, start the next sprint in its backlog.
- If a **Blocker** is listed, follow the ping-PR pattern in `telegram-pings.md`.

---

## Active milestone

| Field | Value |
|---|---|
| **Milestone** | (between sprints — S-047 closed 2026-05-10 with T6 + T7; M5 next per workplan queue) |
| **Title** | S-047 — bybit_2 Spot Margin enablement |
| **Type** | ad-hoc (live-trading priority on M3). T1–T5 shipped 2026-05-07 on the operator-merged branch chain. T6 + T7 closed 2026-05-10. |
| **Goal** | Enable Bybit V5 Spot Margin trading on `bybit_2` end-to-end — config routing, risk-manager sizing, exchange wiring, strategy close-path, reconciler awareness, mainnet smoke, operator runbook. Close out the BUG-046 / BUG-049 / BUG-048 family at the structural level. |
| **Status** | ✅ CLOSED 2026-05-10 — T6 (`docs/runbooks/spot-margin.md` + BUG-066 family-root-cause entry) shipped in PR #686; T7 (this PR) closes the paperwork. |
| **Active sprint** | (none — S-047 closed) |
| **Active checkpoint** | `CP-2026-05-10-05-s047-t6-t7-close` (this PR) |
| **Risk tier** | Tier 1 / 2 / 3 mixed across deliverables — full breakdown in `docs/sprint-logs/S-047.md` § Tier. T1–T5 operator-acked at land time; T6 + T7 are docs-only. |
| **Definition of done** | All 7 deliverables (T1..T7) merged. Mainnet smoke acceptance recorded in the sprint log. M5 unblocked. |

> **Previous sprint context (preserved for handoff):** S-067 — Silent-empty error path audit & hardening — closed 2026-05-10. The 8 Phase-2 close-out PRs (#661, #663, #664, #666, #668, #669, #672, #675) shipped that day.

| Field (S-067 archive) | Value |
|---|---|
| **Title** | S-067 — Silent-empty error path audit & hardening |
| **Type** | ad-hoc, triggered by 2026-05-10 24h trade-performance review. Tier 1 / infra (every PR self-merged) + 2 Tier 2 PRs (operator-acked) for the Phase-2 close-out. |
| **Goal** | Audit every `except Exception` / `except sqlite3.Error` / bare-except site under `src/web/api/`, `src/web/runtime_status.py`, `src/units/db/`, and the read-path slice of `src/runtime/`; convert trust-corroding sentinels to loud failures; add log calls to borderline sites; ship a CI guard so the pattern can't come back. Generalises the bug class hardened in PRs #627 (`/positions` returned `[]` for endpoint lifetime) and #629 (`/signals` dropped `price`). |
| **Status** | ✅ CLOSED 2026-05-10 — sprint (5 work-PRs #642/#643/#644/#645/#646 + sprint-close PR), follow-up queue (10 PRs #650-#660), and Phase-2 close-out (8 PRs #661/#663/#664/#666/#668/#669/#672/#675) all merged. |
| **Active sprint** | (none — S-067 Phase-2 closed) |
| **Active checkpoint** | `CP-2026-05-10-04-s067-phase2-followups` (standalone file in `docs/claude/checkpoints/` pending fold-in into `CHECKPOINT_LOG.md` by the next session with local clone access) |
| **Risk tier** | Tier 1 / infra throughout for the original sprint + 6 of 8 Phase-2 PRs. The 2 Tier-2 Phase-2 PRs (closed-flat invariant wiring helper #672, env-gate survivor regressions #675) shipped under operator ack 2026-05-10. |
| **Definition of done** | Audit doc filed; every § 1 trust-corroding site fixed with regression test; every § 2 borderline site logged; CI lint guard wired; sprint summary, bug-log entry, testing-policy update, milestone-state advance — all shipped. Phase-2 narrowings + FIFO P&L + closed-flat wiring helper + env-gate survivor regressions all merged. |

> **Phase-2 close-out (2026-05-10 evening):** the 4 Phase-2 follow-ups
> filed in `CP-2026-05-10-03` plus items A and B from the original
> follow-up backlog all shipped under operator ack. 8 PRs merged in
> the same session: D1 (#661), D2 (#663), D3 (#664), D4 (#666), C
> (#668), wrap-up CP (#669), A (#672), B (#675). Items A and B carry
> small operator-applied patch documents
> (`docs/claude/closed-flat-invariant-phase2-wiring.md`,
> `docs/claude/env-gate-purge-phase2-annotations.md`) for the
> remaining 1-3 line in-place edits to `order_monitor.py` /
> `pipeline.py` that the autonomous session couldn't push because of
> the ~100KB MCP `create_or_update_file` round-trip limit.

> **S-067 hand-off (2026-05-10):** the next session should pick the next
> queued milestone (S-047 T6 — live smoke + runbook) per workplan order.
> If picking from the S-067 follow-up list instead (per
> `docs/sprints/sprint-067-prompt.md` § 8 / `docs/sprint-summaries/sprint-067-summary.md`
> § Hand-off), the original 4 Phase-2 follow-ups (D + C) shipped in
> the 2026-05-10 evening close-out. **First action of the next
> session with local clone access:** apply the 2 patch docs above
> + fold `CP-2026-05-10-04-s067-phase2-followups.md` into
> `CHECKPOINT_LOG.md`.

> **S-065 deferred (2026-05-09):** dashboard sprint E (controls phase 1
> + login flow) deferred per operator. Login scope decision was
> escalated from option (a) email + shared secret to option (c) Google
> OAuth, which needs operator-side Google Cloud Console setup
> (CLIENT_ID + CLIENT_SECRET + authorised redirect URIs). When the
> operator is ready to do GCP setup, S-065 reopens with OAuth as the
> auth source; the JWT contract on the bot side stays as designed in
> `docs/sprints/sprint-065-prompt.md`.

> **S-064 close-out (2026-05-09):** dashboard side ships
> `LiquidityMapsTab` + `SettingsTab` consuming the two new bot Tier-1
> endpoints. Bot side ships in two PRs:
>
> 1. **Prereq PR** (`claude/bot-S-064-prereq-liquidity-state-writer`) —
>    `src/runtime/liquidity_state.py` writes
>    `runtime_logs/liquidity_state.json` per tick from the existing
>    `LiquidityDetector` (which had been unit-tested but never invoked
>    from the runtime). One-line hook from `turtle_soup_signal_builder`
>    + `vwap_signal_builder` after `fetch_candles`. 13 new tests
>    covering pure detection, atomic per-symbol merge, and the
>    no-raise-into-tick-loop contract.
> 2. **Main PR** — `GET /api/bot/liquidity?symbol=X&limit=N` reads
>    that file; `GET /api/bot/config` re-reads YAMLs + overlays the
>    pipeline's runtime live/dry state from `runtime_status.json` with
>    allowlist (accounts) + recursive secret-key denylist (strategy
>    params). 22 new tests across both endpoints, including a
>    secret-redaction battery covering api_key / api_secret / token /
>    signing_key / password / hash / credential field names at any
>    nesting depth. `docs/api-tier-policy.md` + `CLAUDE.md` updated
>    with both routes.
>
> `ict-trading-bot#557` (closed-trades endpoint with pattern
> attribution) — **partially shipped already** (discovered during the
> S-067 audit; see `src/web/api/routers/trades_closed.py`). Verify
> end-to-end + retire the dashboard's `deriveClosedTradesFromLogs`
> regex fallback in a follow-up.

> **Parallel:** S-047 T6 (bybit_2 Spot Margin live smoke + runbook) is still
> the live-trading priority and runs on its own branch. S-061..S-065 do not
> block S-047 T6 — both progress in parallel.

**S-048 (M1 comms audit) status:** ✅ CLOSED (fresh re-issue) on `claude/update-roadmap-status-ZnLM9` — see `docs/audits/M1-comms-audit-2026-05-07-fresh.md`.

**M1 P1-A..D follow-ups status:** ✅ CLOSED 2026-05-08 on `claude/review-roadmap-hIO75`. P1-A (workplan correction) was already landed pre-branch on `update-roadmap-status-ZnLM9`; P1-D (`/new_session` + `/test`), P1-B (stuck-request recovery alerts), P1-C (auto-hourly snapshot timer) all shipped here. P2 hygiene cluster remains filed for a future Janitor sprint per `docs/audits/M1-comms-audit-followups-fresh.md`. **M1 → ✅ CLOSED.**

---

## M0..M10 status table

> Last verified: 2026-05-10 (S-067 Phase-2 close-out — 8 PRs merged
> covering the 4 D narrowings, FIFO P&L, the wrap-up CP, the
> closed-flat invariant wiring helper, and the env-gate survivor
> regressions). Prior verification: 2026-05-10 (S-067 sprint close +
> follow-up queue close).
> "Verified" = on-disk artifacts checked before accepting any prior
> "done" label.

| Milestone | Focus | Status | Evidence / Notes |
|---|---|---|---|
| **M0** | Workflow foundation | ✅ CLOSED | S0 sprint done; `docs/sprint-summaries/sprint-S0-summary.md` exists; CP-2026-05-06-S0-02 in checkpoint log |
| **M1** | Comms infrastructure | ✅ CLOSED | S-048 fresh audit closed on `claude/update-roadmap-status-ZnLM9`. Audit verdict: PARTIAL, no P0. P1-A (workplan correction) landed there same-session. P1-B (stuck-request recovery alerts), P1-C (auto-hourly snapshot timer), P1-D (`/new_session` + `/test` commands) closed 2026-05-08 on `claude/review-roadmap-hIO75`. P2 hygiene cluster filed for a future Janitor sprint per `docs/audits/M1-comms-audit-followups-fresh.md`. |
| **M2** | Web app source of truth (backend) | ✅ CLOSED | S-013 FastAPI backend (`/api/status`, `/api/pnl`, JWT auth). S-014 added `/api/bot/{stats,logs,positions,signals}` for the Vercel dashboard + CORS middleware keyed to `DASHBOARD_ORIGIN`. Dashboard reachability fix landed 2026-05-07 (Vercel rewrite proxies `/api/bot/*` to the bot, defeats HTTPS→HTTP mixed-content block). M2 formally closed 2026-05-08 alongside the M1 P1-A..D follow-ups — backend work was already shipped, this close-out is paperwork-only (no new code). The diagnostic surface (`/api/diag/*`) is a separate workstream and stays out of M2 scope. **S-067 (2026-05-10)** swept silent-empty error paths across the M2 surface (`/api/bot/{stats,logs,positions,signals,config}`, `/api/diag/*`, `/api/pnl*`) — read-path integrity hardened, CI lint guard added. **S-067 Phase-2 (2026-05-10 close-out)** added FIFO realised + unrealised P&L to `/api/bot/pnl/exchange` (#668) — strictly additive wire-shape; existing dashboard readers unaffected. |
| **M3** | Risk controls foundation | ✅ CLOSED | S-043 closed 2026-05-06. Order-layer refusal tests now complete (28 new gap-closer tests in `tests/test_s043_order_refusal_paths.py`). Risk engine + kill switch + risk caps + reason-token contract all pinned. **S-067 Phase-2 B (2026-05-10)** added per-survivor static-AST regression tests pinning that `MULTI_ACCOUNT_DISPATCH` and `MONITOR_RECONCILE_ENABLED` env gates do **not** bypass `RiskManager.evaluate` (#675). |
| **M4** | Repo hygiene + CI | ✅ CLOSED | S-044 (CI suite) ✅; S-045 (conftest + pytest-collect blocking + ruff default) ✅; post-S-045 follow-up (auto-sync branch protection workflow) ✅; S-046 (2026-05-07) closed the three Janitor audits. **S-067 (2026-05-10)** added the `silent-empty-guard` workflow + `scripts/check_silent_empty_in_diff.py` lint script — read-path discipline now enforced in CI alongside `dry-run-guard`, `ruff-lint`, `secret-scan`. **S-067 Phase-2 (2026-05-10)** added the env-gate-guard workflow (#659) + the env-gate survivor static-AST tests (#675). M4 formally closed. |
| **M5** | Strategy testing workflow | ✅ CLOSED | Closed 2026-05-10 across four PRs: P1 #637 (closed loop), P2 #639 (subprocess hardening), P3 #640 (`docs/runbooks/strategy-testing.md` + close-out updates), P4 (this PR — Tier-1 `GET /api/bot/backtests` feed for the dashboard's optional backtest-history tab; companion dashboard UI tab still pending in `benbaichmankass/ict-trader-dashboard`). |
| **M6** | Web app UI | 🔄 IN PROGRESS (dashboard repo) | S-014 V1 React/Vite SPA shipped 2026-05-07 in `the-lizardking/ict-trader-dashboard`. **S-015 V2 plan scratched 2026-05-07** per operator. Dashboard connection fix (Vercel rewrite of `/api/bot/*` to bot VPS) landed the same day. **In active session 2026-05-08** on dashboard branch `claude/update-roadmap-status-ZnLM9` — wiring mock-data feeds (equity chart, Active ICT Strategies, Trading Conditions) to live `/api/bot/*` data; positions and signals to follow. |
| **M7** | Strategy review gate | 📋 NOT STARTED | |
| **M8** | Strategy tuning | 📋 NOT STARTED | |
| **M9** | AI / model roadmap | 📋 NOT STARTED | S-005 (model monitor) and S-006 (model registry) built under old framing; formal M9 not started. |
| **M10** | HF / data pipeline | 📋 NOT STARTED | S-004 (training pipeline) built under old framing; formal M10 not started. |

---

## Recently closed milestones

> Rolling window. Older entries pruned to `ROADMAP.md` and `docs/sprint-summaries/`.

| Milestone | Closed | Final checkpoint | Summary doc |
|---|---|---|---|
| M0 — Workflow Foundation (≈ S0) | 2026-05-06 | `CP-2026-05-06-S0-02` | `docs/sprint-summaries/sprint-S0-summary.md` |
| S-041 — workplan reconciliation sweep | 2026-05-06 | `CP-2026-05-06-12-s041-complete` | `docs/sprint-summaries/sprint-041-summary.md` |
| ~~M1 — Comms infrastructure (S-042)~~ | ~~2026-05-06~~ | ~~`CP-2026-05-06-14-s042-complete`~~ | ~~`docs/sprint-summaries/sprint-042-summary.md`~~ — **REOPENED 2026-05-07; audited fresh 2026-05-08 → 🔄 PARTIAL via `CP-2026-05-07-17-s048-fresh-m1-audit`** |
| M3 — Risk controls foundation (S-043) | 2026-05-06 | `CP-2026-05-06-15-s043-complete` | `docs/sprint-summaries/sprint-043-summary.md` |
| **M4 — Repo hygiene + CI (S-046)** | **2026-05-07** | `CP-2026-05-07-NN-s046-complete` | `docs/sprint-summaries/sprint-046-summary.md` |
| **S-048 — M1 comms audit (fresh re-issue)** | **2026-05-08** | `CP-2026-05-07-17-s048-fresh-m1-audit` | `docs/sprint-summaries/sprint-048-summary.md` |
| **M1 P1-A..D follow-ups + M2 close-out** | **2026-05-08** | (this PR's checkpoint) | `docs/sprint-summaries/m1-p1-followups-and-m2-close-summary.md` |
| **M2 — Web app source of truth (backend)** | **2026-05-08** | (this PR's checkpoint) | (paperwork-only close — work already shipped under S-013 + S-014) |
| **2026-05-08 all-models training run + S-050 (VWAP Phase 2)** | **2026-05-09** | `CP-2026-05-09-01-all-models-training` | `experiments/2026-05-08-all-models-training/RECOMMENDATIONS.md` (PR #558 squashed as `9a7bdf3`) |
| **S-061 — Dashboard sprint A (data-contract gap + nullable types)** | **2026-05-09** | (squash `a8eaad4`) | `docs/sprints/sprint-061-prompt.md` |
| **S-062 — Dashboard sprint B (Models + Time & Price tabs)** | **2026-05-09** | dashboard PR #8 squash `06ca19c` | `docs/sprints/sprint-062-prompt.md` |
| **S-063 — Dashboard sprint C (Performance tab + persistent equity; bot drops `/api/pnl/history` JWT gate, flattens response)** | **2026-05-09** | dashboard PR #9 squash `be85d10`; bot PR #595 squash `87d5ee1` | `docs/sprints/sprint-063-prompt.md` |
| **S-064 — Dashboard sprint D (Liquidity Maps + Settings tabs; new bot endpoints `/api/bot/{liquidity,config}`; pipeline writes per-tick `runtime_logs/liquidity_state.json` via prereq hook)** | **2026-05-09** | bot prereq PR #597 squash `1eb816b`; bot main PR #601 squash `14fe5d7a`; dashboard PR #10 squash `b7963b26` | `docs/sprints/sprint-064-prompt.md` |
| **S-066 — Janitor: M1 P2 hygiene cluster close-out (docs only)** | **2026-05-09** | (this PR's checkpoint) | `docs/audits/M1-comms-audit-followups-fresh.md` § P2 |
| **S-067 — Silent-empty error path audit & hardening** (5 work-PRs + sprint-close PR; trust-corroding fixes + log-only borderline batch + CI lint guard; BUG-065) | **2026-05-10** | `CP-2026-05-10-01-s067-complete` (folded into `CHECKPOINT_LOG.md`) | `docs/sprint-summaries/sprint-067-summary.md` |
| **S-CANON-1 + S-CANON-FU-1/2/3** — Canonical doc set adoption + post-canon follow-ups (legacy workplan superseded; closed-flat invariant tick-loop wiring + Phase-1 alert-only soak; branch-protection PAT enabled) | **2026-05-10** | (this session) | `docs/sprint-logs/S-CANON-1.md`, `docs/sprint-logs/S-CANON-FU-1-workplan-superseded.md`, `docs/sprint-logs/S-CANON-FU-2-cfi-wiring.md`, `docs/sprint-logs/S-CANON-FU-3-branch-protection.md` |
| **S-047 — bybit_2 Spot Margin enablement** (T1 routing + T2 sizing + T3 wiring + T4 VWAP monitor + T5 reconciler + T6 runbook + BUG-066 + T7 close) | **2026-05-10** | `CP-2026-05-10-05-s047-t6-t7-close` (this PR) | `docs/sprint-logs/S-047.md` |
| **M5 — Strategy testing workflow** (P1 #637 closed loop + P2 #639 subprocess hardening + P3 #640 runbook + close-out docs + P4 `GET /api/bot/backtests` dashboard feed) | **2026-05-10** | (this PR's checkpoint) | `docs/runbooks/strategy-testing.md` |
| **S-067 follow-up queue (10 items)** — fixture extraction (#650), trades-closed verify (#650 + dashboard #11), deploy restart contract + `/api/diag/version` (#651), exchange-fills attribution Phase 1 (#652), BUG-065 fold-in (#653), `_vm_health` consolidation (#654), daily one-trade audit auto-task (#655), hourly_report/boot_audit audit (#656), closed-flat invariant Phase 1 Tier 2 (#658), env-gate purge Phase 1 Tier 2 (#659). 4 Phase-2 follow-ups filed; one operator ack collected mid-session for the two Tier 2 DRAFTs. | **2026-05-10** | `CP-2026-05-10-03-s067-followups-wrap-up` | (no sprint summary — queue closure documented in CP) |
| **S-067 Phase-2 close-out (8 PRs)** — D1 boot_audit None-on-failure (#661), D2 list_accounts narrowing (#663), D3 strategy_dashboard narrowing (#664), D4 run_all_checks unknown sentinel (#666), C exchange-fills FIFO realised + unrealised P&L (#668), wrap-up CP (#669), A closed-flat invariant wiring helper + patch doc Tier 2 (#672), B env-gate survivor regressions + patch doc Tier 2 (#675). Items A + B include patch documents for the small in-place edits to `order_monitor.py` and `pipeline.py` that the autonomous session's MCP push couldn't apply directly (file size limit). | **2026-05-10** | `CP-2026-05-10-04-s067-phase2-followups` (standalone file pending fold-in) | `docs/claude/checkpoints/CP-2026-05-10-04-s067-phase2-followups.md` |

> Pre-M0..M10 roadmap progress (S-000 through S-040) is captured in `ROADMAP.md`
> under "Historical Sprint Ledger". From M0 forward, every closed milestone gets a row here.

---

## Queued milestones

In execution order. Each row lists the gating condition to start.

| Order | Milestone / sprint | Type | Gating condition |
|---|---|---|---|
| 1 | M6 — Web app UI (dashboard repo) | auto-claude | **In active session 2026-05-08** in `benbaichmankass/ict-trader-dashboard`. The bot-side `GET /api/bot/backtests` feed for the new tab is now live (M5 P4, 2026-05-10) — the dashboard side wires the consumer + UI tab. |
| 2 | M9 — AI / model roadmap | auto-claude | Independent of M6. Could run in parallel. |
| 3 | M10 — HF / data pipeline | auto-claude | Independent of M6. Could run in parallel. |
| 4 | **S-050-followup — Phase-3 HTF reference 4h → 1h EMA-200** (Tier 2, PM-review) | strategy-improvement | ≥ 30 days of Phase-2 live metrics on the HTF gate (S-050 shipped 2026-05-09). Expected +0.4 Sharpe lift on top of Phase-2 per V3 in `experiments/2026-05-08-all-models-training/`. |
| 5 | **Closed-flat invariant auto-flatten promotion** (Tier 2 → Tier 3 for the per-account flag) | observability → live action | ≥ 7 days clean alert-only soak. Soak started 2026-05-10 via the `enable-closed-flat-invariant` operator action (S-CANON-FU-2 wiring landed in PR #679). After clean soak, file the per-account `closed_flat_auto_flatten` flag PR. |

> S-067 Phase-2 follow-ups (the original row 8) — closed 2026-05-10
> evening with the 8-PR Phase-2 close-out. Replaced in row 8 above with
> the auto-flatten promotion follow-on (gated on the 7-day soak after
> the operator applies the closed-flat invariant wiring patch).

> M2 (Web app source of truth) — closed 2026-05-08 (paperwork-only;
> backend had already shipped under S-013 + S-014). Not a blocker for
> any queued milestone.
>
> S-050 (VWAP Phase 2 HTF gate) — shipped early on 2026-05-09 via PR
> #558 after the 2026-05-08 all-models training run showed the
> 38-month baseline was structurally unprofitable (Sharpe -0.39).
> The originally-gated "≥ 30 days live metrics" condition was
> waived by operator decision — Phase-2 was no longer a quality
> lift but the difference between profitable and not. The 30-day
> gate now applies to the Phase-3 follow-up instead (HTF reference
> 4h → 1h EMA-200).

---

## Standing / recurring sessions

| Cadence | Session | Prompt |
|---|---|---|
| Bi-daily | Hardening & Stability Audit | `docs/sprints/recurring-hardening-prompt.md` |
| Weekly | Strategy Improvement Review | `docs/sprints/recurring-strategy-improvement-prompt.md` |
| Weekly (HF cron) | Model Training & Evaluation | `docs/sprints/recurring-model-training-prompt.md` |

Full spec: `docs/claude/recurring-sessions.md`.

---

## Open blockers

| Blocker | Owner | Opened | Notes |
|---|---|---|---|
| BUG-057 diagnostic review | VM logs | 2026-05-06 | Diagnostic logging shipped PR #424. Awaiting next live VWAP rejection with `BUG-057-DIAG` log lines in `journalctl`. |

> **Resolved 2026-05-07/08:**
> - **S-047 T1..T5 + S-049 fast-followup** all operator-merged 2026-05-07.
> - **PR #463 (stale S-048 audit) + PR #467 (contradictory S-047 T3 close-checkpoint)** — both closed 2026-05-07/08 in favour of the fresh S-048 re-issue on `claude/update-roadmap-status-ZnLM9`.

---

## Update protocol

The closing checkpoint of every sprint must:

1. Refresh **Active milestone** (status, active sprint, active checkpoint).
2. If the milestone closed, move it to **Recently closed milestones** and advance
   the next queued milestone into **Active**.
3. Update **M0..M10 status table** for any changed milestones.
4. Refresh the **Queued milestones** rolling window (1–3 ahead).
5. Add or remove **Open blockers** rows as state changes.
6. Commit this file alongside the `CHECKPOINT_LOG.md` append in the same PR so
   the program's state moves atomically.

If a session discovers this file is out of date relative to `CHECKPOINT_LOG.md`,
the first action of the session is to reconcile the two.
