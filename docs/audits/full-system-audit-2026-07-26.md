# Full-System Audit — 2026-07-26

> **Program doc (the shared brain).** Per `.claude/skills/full-system-audit/SKILL.md`
> this is the audit's findings doc + per-file coverage map. Consistency **and**
> liveness axes; rules-first. Branch: `claude/full-system-audit-5v17vo` (all three repos).
>
> **Predecessors:** `full-system-audit-2026-06-28.md` (M17), `full-system-audit-2026-07-09.md`.
> This is a fresh periodic pass requested by the operator 2026-07-26 to vet the
> **recent infra push** (M28 macro/value subsystem, M27 15m scalp legs, M26 conflict
> taxonomy, M0a/M0b platform-layer guard, the repo rename, ~10 new workflows) for
> compliance, and re-check the older surfaces for drift/zombies.

## Phase 0 — RULES audit (gate)

- `scripts/ci/check_canonical_doc_coherence.py` — **PASS** (4/4: dead-VM-IP single-source, removed-gates-not-live, no-7-stage-ladder, instruction-hierarchy mirror).
- Canonical corpus read highest-precedence first: `CLAUDE-RULES-CANONICAL` → `ARCHITECTURE-CANONICAL` → `ROADMAP`/`ROADMAP_MACRO` → latest sprint log → both `CLAUDE.md`.
- Contradiction-hunt: delegated to the consistency agent (Phase 3A).

**Verdict:** rules corpus internally consistent (coherence 4/4 PASS). The Phase-0 findings are drift in the #2 yardstick (`ARCHITECTURE-CANONICAL`) vs reality — the entire M28/M29 macro subsystem undocumented + `alpaca_live` liveness overstated + strategy count stale — all Tier-1 doc, fixed this branch (WS-A). No rule-level contradiction that would bend the yardstick before auditing against it.

## Overall verdict

**NEW infra is fit for purpose and compliant.** M28 macro/value (isolated observe-only, no order path), M27 15m scalp legs (paper-only on bybit_1, proper gates), M26 conflict taxonomy (observe-only), M0a/M0b layer guard (genuinely enforcing) — **zero confirmed Prime-Directive / Tier-3 / isolation violations**. All 19 CI guards + ruff pass; 8117 tests collect; live VM on latest `main`; canonical store single-source-clean; MES connected; consumer wiring has no breaks.

**OLD infra mostly up to date**, with a focused set of Tier-1 hygiene items fixed this pass (ARCH doc drift; diag observability of 3 data-ingest timers; 2 workflow-liveness fixes) and a handful surfaced/backlogged. **Three live issues need operator attention** (F1 mes-ibkr-pull failed, F2 352 stranded pings, F3 bybit_2 smoke failures) — none is a live-money-loss risk, but F1/F2 are real observability/freshness gaps.

## Environment / session facts

- GitHub scope this session: `benbaichmankass/metis-insights` (lowercase) + the two consumer repos. `ict-trading-bot` string is denied at the session-scope layer — use `metis-insights`.
- Direct diag (`DIAG_BASE_URL`) still points at the retired micro `158.178.210.252` and egress is firewalled (Trusted network) → **VM audit is relay-only** (issue-driven `vm-diag-request` / `trainer-vm-diag-request`). Dispatched: #7626 (snapshot), #7627 (services), #7628 (db_info).
- Open PRs at audit start: **#7609** (weekly system-review report, draft, main-gated) · **#7621** (dataset-audit declare-optional, draft) — both other sessions' work, NOT in audit scope.

## Phase 1 — Workstream plan

| WS | Scope | Mode | Status |
|---|---|---|---|
| **A — Consistency/doc-drift** | ARCHITECTURE-CANONICAL + ROADMAP + ROADMAP_MACRO + CLAUDE.md vs reality since 07-09 (M28/M27/M26/M0a-b/rename). `workplan-vs-architecture` + `doc-freshness`. | agent | running |
| **B — Liveness/zombie hunt** | Integration inventory (brokers, services/timers, the 101 workflows incl. ~10 new, env-gates, transports) → 3 probes each → LIVE/keep/ZOMBIE. | agent | running |
| **C — New-infra compliance** | M28 macro/value subsystem + M27 scalp legs + M26 + M0a/b layer guard: Prime-Directive gates, Tier-3 discipline, canonical-store wiring, real/paper/prop isolation. | agent | running |
| **D — Consumer wiring** | dashboard `streamlit_app.py` + android `BotApi.kt`: every consumed endpoint exists + shapes match; null handling; new fields. | agent | running |
| **E — Code sweep + tests/CI** | Dead code / correctness smells in newest src; test-collection + lint green; CI-guard consistency. | agent | running |
| **F — VM audit** | Services/timers state, running SHA vs main, `.env` inventory, disk, `/opt` symlink. Via relay. | lead | running (relays out) |
| **G — Data audit** | `trade_journal.db` + `trainer_store.db` integrity, orphans/reconcile_status, real/paper/prop isolation. Via relay. | lead | running (relays out) |
| **H — Loose ends** | Stale PRs/issues, dispatch-issue closeout, session-board hygiene, backlog triage. | lead | pending |

## Findings (appended by axis + tier as they land)

### WS-F — VM audit (lead, via relay #7629) — mostly CLEAN
- **Live trader on latest `main`:** `/api/diag/version` git_sha `748dcbed` == `main` HEAD (`748dcbe`). ✅ `ict-git-sync` deploying current code. Heartbeat `running` (age ~140s), uptime ~43m.
- **Services/timers** (`/api/diag/services`): `ict-trader-live`, `ict-web-api`, `ict-telegram-bot`, `ict-claude-bridge` all **active**. Timers active: git-sync, liveness-watchdog, web-api-watchdog, db-integrity, insights-generator (×2), hourly-snapshot, health-snapshot, devnull-guard, shadow-log-rotate. `ict-heartbeat` **inactive** ✅ (correctly retired 2026-07-08). `ict-ib-gateway-watchdog.timer` **inactive** on the live VM ✅ (that watchdog runs on the *gateway* VM).
- **IB/MES** (`/api/diag/ib_state`): both clients (exec 498 + readonly 9488) `connected`, `account_data_ready`, no breaker, `likely_wedged:false`. MES trading live. ✅
- **Account live-map** (runtime_status): executing = bybit_1, bybit_2, bybit_portfolio, alpaca_paper, alpaca_portfolio, alpaca_options_paper, ib_paper, breakout_1(prop). **DRY** = `alpaca_live:false`, `ib_live:false`, `oanda_practice:false`. ⚠️ **Cross-check needed:** prior audit (07-09, D1) flagged `config/accounts.yaml::alpaca_live mode=live` (real money) — runtime now shows it **dry**. Either an operator Tier-3 flip since, or a config/runtime mismatch. WS-A to reconcile config vs this runtime truth. (info→med)
- **51 strategies loaded** at runtime (was 48 at 07-09) — feeds WS-A doc-drift (ARCH still says "12").

### WS-G — Data audit (lead, via relay #7629) — CLEAN single-source
- `trade_journal.db` resolves to canonical `/data/bot-data/trade_journal.db` (inode 3634988, 635 MB), `load_error:null`, no per-table errors. **No stray/duplicate journal.** ✅ Tables all expected (signals 1.20M, order_packages 3290, trades 4022, prop_tickets 51/fills 25/status 6, strategy_versions 64, balance_snapshots 18630). No unexpected tables.
- **Obs (low):** `learning_progress` = 0 rows and `device_tokens` = 0 rows — the two client-write surfaces have never been written to (nobody's marked a curriculum resource done / registered a device). Not a bug; note that those write paths are unexercised in prod.
- **Obs (low, demo-only):** orphaned order package `pkg-07d51e1d` (`ict_scalp_avax_5m`, bybit_1 **demo**, `BUG-049 — no linked_trade_id after 5 min; never executed`). Recurring never-executed-orphan on the AVAX 5m scalp on the demo account; no money impact, but the 5m-scalp intent-layer produces `aggregated_target_qty: 0.0` packages that then orphan — worth a WS-C/E look at whether a 0-qty aggregation should emit a package at all.

### WS-D — Consumer wiring & display correctness (agent) — CLEAN, no breaks
- **No high-sev breaks.** Every endpoint the dashboard (`streamlit_app.py`, ~110 fetch sites) and android (`BotApi.kt`, 55 routes) call resolves to a real, mounted bot route with matching shape. No consumed endpoint missing bot-side.
- Nullability contract honored both sides (null→em-dash; the past android `unrealizedPnl=0.0`/`error:String?` bugs are fixed and DTOs correctly nullable/typed). No hardcoded strategy/symbol list (live-discovered; android literals are `.ifEmpty` fallbacks only). ✅ BL-20260611-SYM-1 holds.
- **Low-sev display GAPs (Tier-1, → health-review backlog):** bot endpoints with no consumer yet: `/liquidity`, `/pnl/exchange`, `/positions/net`, `/strategy/attribution`, `/allocator/soak`, `/pairs/soak`, `/fc-geometry/soak`, `/health/{history,snapshot}`, `/shadow/predictions`; android Accounts lacks the `/pnl/broker-truth` + `/pnl/history` lines the dashboard shows. All observe-only/soak/attribution surfaces — safe incremental wiring, no correctness risk.
- Follow-up confirm (low): android `BotApi.kt` grep didn't show `GET/DELETE/PATCH /devices*` mgmt routes — confirm the Notifications screen's per-kind toggle path.

### WS-F/G relay notes
- Trainer-diag relay #7630 failed on my `cmd:` heredoc formatting (fence mangling); re-dispatched clean as #7631.
- Stale system-action dispatch issues #7025 (send-ping, completed 07-20) + #6874 (status-check, 07-18) ran successfully but were **not auto-closed** — unlike diag relays, `system-actions.yml` leaves its issues open. Minor infra housekeeping (dispatch issues accumulate). → close stale-done at wrap (WS-H).

### WS-A — Consistency / doc-drift (agent) — all Tier-1 doc fixes
- **A2 (HIGH, Tier-1):** the entire **M28 macro/value thesis + M29 sysdyn subsystem** (`src/units/strategies/macro_thesis/` 11+ modules, `src/sysdyn/`, `config/macro_*.yaml`, `scripts/macro/`) has **zero mention in `ARCHITECTURE-CANONICAL.md`** (`grep -c macro` = 0; change-log ends 2026-07-16). A substantial new subsystem undocumented in canonical doc #2 — the exact drift class the doc's own Update Rule forbids. Fix: add change-log row + Repo-Responsibility-Map entries. (No web-API router exists → CLAUDE.md API table correctly unaffected.)
- **A4 (MED, live-money-adjacent, Tier-1):** ARCH says `alpaca_live` "runs a real-money subset / went live 2026-06-25". Reality: `config/accounts.yaml:814` `mode: dry_run` "SHELVED 2026-07-15 (Tier-3, operator-directed)". Runtime confirms (WS-F: `alpaca_live:false`). **Config+runtime agree it's dry — only the doc overstates liveness.** Fix ARCH Step-2/6 + add change-log row. (Resolves my WS-F cross-check — not a mismatch, doc drift.)
- **A1 (MED, Tier-1):** ARCH "48 strategy cells (verified 2026-07-09)" → actually **54** (+6 M27 scalp legs). Prior D3 re-drifted. Fix count/date or point to `/api/bot/strategies`.
- **A3 (MED, Tier-1):** M0a/b import-linter/`layer-guard` absent from ARCH's CI-guard list + no note of the 4-layer target architecture. Add it.
- **A5–A8 (LOW, Tier-1):** stale `pos_size` cap ref (removed 2026-06-24); stale "5→12" snapshot; canonical docs #1/#2 headers lack the rename note (KEEP scope string, add prose line); optional pairs/macro Repo-Map rows.
- **Prior D1–D10:** D2/D4/D5/D6/D7/D8 confirmed FIXED & consistent. D1 re-drifted → A4; D3 re-drifted → A1; D9/D10 mostly done (pos_size ref survives → A5).
- ✅ Positives: ROADMAP well-maintained (M26–M29 rows current); `/api/bot/roadmap` parser heading-literal safe; pairs sleeve + account modes on disk all Prime-Directive-consistent.

### WS-C — New-infra compliance (agent) — ZERO confirmed violations
- **M28 macro/value:** CONFIRMED observe-only, fully isolated — no order path anywhere in `macro_thesis/`; sole live importer `src/main.py:679` `run_macro_thesis_tick` (best-effort, places no order). Gate `config/macro_theses.yaml` `sleeve.execution: shadow` (M22 pattern), no third gate. Eventual venue = `alpaca_options_paper` (paper). Stores file-backed JSONL via `src.utils.paths` (no stray/CWD DB). ✅
- **M27 15m legs:** CONFIRMED paper-only — routed only to `bybit_1` (paper), absent from real-money `bybit_2`/`bybit_portfolio`. Fully registered (builders + intent roster + monitor). No hidden `*_ENABLED` gate. Approval documented in-tree (`strategies.yaml` dated comments + `docs/research/M27-P1-...`). ✅
- **M26 conflict taxonomy:** CONFIRMED observe-only — `conflict_taxonomy.py`, sole call site inside the already-decided branch, return value discarded, write-only JSONL, no read-back. Flagless baseline-on (sanctioned shape). ✅
- **M0a/M0b layer guard:** CONFIRMED real + enforcing — `.importlinter` 6 forbidden contracts (grimp 307 files, 6 kept/0 broken), zero `ignore_imports`/`allow`/skip, `layer-guard.yml` blocks CI (no `|| true`). Positive-control test confirms teeth. ✅
- **Low obs:** M28/M26 file-backed (not DB) stores — deliberate/precedent-consistent (future-wiring candidate); M27 `strategy_changelog.json` lacks 15m-leg entries (C-9, backfill); layer-guard scope not yet whole-repo (by design).
- **Two honest gaps (lead to verify):** (1) Tier-3 approval **commit SHAs** for M26/M27/M28 unverifiable from git (working tree is a **shallow clone**, boundary `ef57de7`) — corroborate via PR search. (2) Whether `layer-guard` is a **required** branch-protection check — not visible in tree.

### WS-B — Liveness / zombie hunt (agent) — no Prime-Directive violations; Tier-1 hygiene
- **No Prime-Directive violations** (no required capability behind a default-off `*_ENABLED`); removed gates confirmed comment-only. CF/Vercel purge clean in `src/`.
- **Brokers:** every `EXCHANGE_MAP` key + IB factory routes from `accounts.yaml`; no exchange zombies. `breakout`/`oanda` documented-keep (stub/shelved-dry).
- **Diag observability gaps (FIXED this branch):** 3 data-ingest timers (`ict-exchange-fills-pull`, `ict-exchange-funding-pull`, `ict-mes-ibkr-pull`) were installed+enabled but **absent from `_CANONICAL_UNITS`** → invisible to `/api/diag/services` + health-review (the silent-skipped-job class). Retired `ict-heartbeat.service` was still listed. → **diag.py edited: added the 3 timer pairs, dropped ict-heartbeat.** Live-verified via status-check #7633 (all 3 timers `enabled`/`active-waiting`).
- **Workflows (FIXED this branch):** `replay-pregate-nightly.yml` defaulted its ref to the long-dead branch `claude/ml-strategies-deep-review-51n3cw` (×2) → changed to `main`. `replay-pregate-now` + `test-alpaca-from-vm` labels were filtered-on but never bootstrapped (undispatchable) → added to `bootstrap-labels.yml`.
- **Tier-1 deferred → backlog:** stale "Vercel React dashboard" comment + `DASHBOARD_ORIGIN` vercel URL in `deploy/ict-web-api.service` (unit file — don't touch in an audit PR; backlog). `cutover-live.yml` MICRO_HOST default = terminated micro IP (intentional rollback tooling).

### WS-E — Code sweep + tests/CI (agent) — ALL GREEN
- **19/19 CI guards + ruff PASS**; 8117 tests collect cleanly (collection "errors" were missing sandbox deps, not defects); `layer-guard` 6 contracts kept/0 broken.
- Newest code (M28/M26/M27/M29) unusually clean: no bare-except on data/order paths, no naive-tz on DB windows, no path-resolver bypass, 3 benign TODOs.
- **E-1 (LOW, Tier-1 → backlog):** `macro_thesis/fred_adapter.py:184-187,211-214` swallows FRED fetch/parse failure to `[]` with **no logging** → a FRED outage silently degrades a series. Not an order/data-integrity risk (M28 observe-only), but a legibility nit. Add a warning log.
- **E-2 (info):** `src/sysdyn/*` shipped + import-locked + tested but not yet tick-wired — intentional (P1a pure package), not dead code.

### ⚠️ WS-F — LIVE VM ISSUES SURFACED (status-check #7633) — need operator attention
- **F1 (MED-HIGH):** **`ict-mes-ibkr-pull.service` is in `failed`/`failed` state** on the live trader. The MES deep-history pull (keeps the trainer MES base fresh — `BL-20260626-MES-BASE-STALE`) last-ran failed; its timer is active but the run errors. Was invisible to monitoring (not in `_CANONICAL_UNITS`) — now exposed by the diag fix. → **operator: needs a look at the failing run (`journalctl -u ict-mes-ibkr-pull`); trainer MES base may be stale.** Backlog + surface.
- **F2 (MED):** **Claude-ping split-brain — 352 pings stranded.** `/data/bot-data/runtime_logs/pending_claude_pings` = 0 queued (drained), but the **repo-path** `/home/ubuntu/ict-trading-bot/runtime_logs/pending_claude_pings` = **352 queued .json**, undelivered. A writer is still emitting to the pre-migration repo path while the drainer reads the canonical DATA_DIR path. Relates to still-open #6874. Repo-path `signal_audit.jsonl` mtime is 2026-07-26 (recent) → active dual-path writing persists post data-dir migration. → surface + backlog (a writer not resolving via `src.utils.paths`/DATA_DIR).
- **F3 (LOW-MED):** recurring `smoke_test` `smoke_open_result status=failed_exchange reason="'NoneType' object has no attribute 'place_order'"` on **bybit_2** (real-money account) — 19:14 / 03:25 / 08:32. The S-017 plumbing smoke can't resolve a client on the smoke path (the live trader itself trades bybit_2 fine). Harness/plumbing defect, not live-trade impact. → backlog.
- **F4 (info):** `systemd-journald` at ~4.0G in cgtop (journal growth) — minor; note only. Live VM otherwise healthy (loadavg 0.12, mem 1.1/12G).
- **Confirmed OK:** `ict-heartbeat.timer` disabled ✅; ib-gateway reset/watchdog timers `masked` on live ✅ (they run on the gateway VM).

### WS-C gaps closed (lead)
- **Branch-protection required checks** (source of truth `branch-protection-sync.yml::REQUIRED_CONTEXTS`): 9 checks gate `main` — `pytest-collect, pytest-run, secret-scan, ruff-lint, dry-run-guard, env-gate-guard, silent-empty-guard, canonical-config-loaders, canonical-db-resolver` (strict + admin-enforced). **`layer-guard` is NOT required** — the M0a/b layering guard runs advisory-only. Whether to promote it to a required check is an operator call (could newly-block merges) → surface as a proposal, don't self-apply. Stale: the S-CANON-FU-3 sprint log + the operator notebook still say "4 required checks" / old owner `the-lizardking` (low doc drift).
- **Tier-3 approval verification:** working tree is a **shallow clone** (50 commits, graft `ef57de7`) → M26/M27/M28 approval SHAs not git-verifiable locally. Corroborated instead by: in-tree dated approval comments + proposal docs (WS-C), and the **live config matching** (bybit_2 real-money live, alpaca_live shelved-dry, 15m legs on bybit_1 paper only). No post-boundary commit touches a Tier-3 file. Assessed **compliant** on available evidence; noted honestly.

## Per-file coverage map (append as read)

Format: `path — reader — verdict`.

### Canonical docs / Phase 0
- `docs/CLAUDE-RULES-CANONICAL.md` — agent A — pending
- `docs/ARCHITECTURE-CANONICAL.md` — agent A — pending
- `ROADMAP.md` / `ROADMAP_MACRO.md` — agent A — pending
- `CLAUDE.md` (bot) + dashboard + android — lead — READ (session context)
