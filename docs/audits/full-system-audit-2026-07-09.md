# Full-System Audit — 2026-07-09

> **Program doc (the shared brain).** Per `.claude/skills/full-system-audit/SKILL.md`
> this is the multi-session audit's findings doc + per-file coverage map. Every
> session reads it on start and appends to it. Consistency **and** liveness axes;
> rules-first. Branch: `claude/full-system-audit-rmdf0t` (all three repos).
>
> **Predecessor:** `docs/audits/full-system-audit-2026-06-28.md` (M17). This is a
> fresh periodic pass requested by the operator 2026-07-09.

## Phase 0 — RULES audit (DONE, gate cleared)

**Method:** read the canonical corpus highest-precedence first
(`CLAUDE-RULES-CANONICAL` → `ARCHITECTURE-CANONICAL` → `ROADMAP` → latest sprint
log → both `CLAUDE.md`), ran `scripts/ci/check_canonical_doc_coherence.py` (all 4
checks PASS), and fanned out three parallel contradiction-hunt agents (rules-doc,
architecture change-log, roadmap cross-doc). Verified concrete claims against
config/code on disk.

**Verdict:** the top-precedence **operating-rules doc is internally consistent**;
where spot-checked the **system is compliant** (e.g. the auto-flip mode dead code
is genuinely deleted — Prime Directive holds). The real Phase-0 finding is
**material drift in the #2/#3 yardsticks** (`ARCHITECTURE-CANONICAL`, `ROADMAP`)
vs reality, plus two rule-wording ambiguities.

### Rule-level items — settled with operator 2026-07-09

| ID | Item | Decision | Status |
|---|---|---|---|
| R1 | Order path (`orders.py`, `execute.py`) + live-VM service units classified Tier-2 in canonical examples but Tier-3 in VM-authority-split | **Tier-3** (stricter, merge-gate sets the tier); struck from Tier-2 examples, added to Tier-3 examples | ✅ FIXED (this branch) |
| R2 | Prime Directive `*_ENABLED` rule stated as absolute but has carve-outs + a CI guard that rejects the suffix | **Narrow the wording**: forbidden = default-off `*_ENABLED` on a *required* capability; NEWS_VETO_ENABLED / M5_CONSUMER_ENABLED grandfathered; `*_MODE` is the sanctioned shape | ✅ FIXED (this branch) |
| R3 | Tier-1 "commit to `main`" vs the PR/merge-protocol + branch-protection | Clarified: "commit to main" = no operator-approval gate, still via PR | ✅ FIXED (this branch) |
| R4 | "Why no new mechanical guardrails" reads broadly vs the CI guards the same doc mandates | Scoped to the Tier-3 approval discipline; structure/wiring guards explicitly sanctioned | ✅ FIXED (this branch) |
| R5 | `ict-heartbeat` (retired 2026-07-08) still named in Tier-2 service-unit examples | Removed in the R1 edit | ✅ FIXED (this branch) |

### Material canonical-doc drift (yardstick stale) — feeds S-AUDIT-A

Verified against config/code on disk 2026-07-09:

| ID | Doc claim | Reality | Sev |
|---|---|---|---|
| D1 | ARCH: "Real-money Alpaca remains gated" | `alpaca_live`: `mode=live`, `real_money`, ~16–20 strategies routed | ⚠️ live-money |
| D2 | ARCH Step 2: `squeeze_breakout_4h` `execution: shadow` | config: `squeeze_breakout_4h` `execution: live` | ⚠️ live-gate |
| D3 | ARCH: "12 strategies registered (verified 2026-06-10)" + its own ~16-item enumeration | **48** in `config/strategies.yaml` | high |
| D4 | ARCH line ~104-105 & ~871: `_DRY_RUN_OVERRIDES`/`set_account_dry_run` "deletion never landed" | **Deleted** (docstring-only + regression test asserts absence); doc self-contradicts across 5 spots | high |
| D5 | ARCH Step 6: "IBKR offline pending new-user approval, MES not executing" (2026-05-24) | MES/MGC/MHG live; `ib_paper` also trades SPY/QQQ/IWM/TLT | med |
| D6 | ARCH Step 3: bybit_1/bybit_2 "mirrors, same roster" | bybit_2 winners-only (9) vs bybit_1 (20) | med |
| D7 | ROADMAP milestone table titled "M0..M15" | holds M17/M18/M19 rows; **M16 has no row**; `/api/bot/roadmap` parser keys on the literal heading | med (load-bearing) |
| D8 | ROADMAP "Active milestone queue (next 3)" lists M12-S1/M13-S1 as upcoming | both DONE; real active = M15/M17/M18/M19 | med |
| D9 | ROADMAP M15 row understates `alpaca_live` (SPLG/IAUM real-money + normalized caps) | header + S-PROXY ledger + config already carry it | low |
| D10 | ARCH: breaker line-nums (1048-1068 vs 1669-1689); 0.25 tick vs equity penny; repo-map omits IB connector + prop executor; ROADMAP WS5-B-PART-2 "next" though DONE; vwap/fade changelog gaps | assorted stale references | low tail |
| ENV1 | Session `DIAG_BASE_URL=http://158.178.210.252:8001` (terminated x86 micro) | live trader is `141.145.193.91`; direct diag broken → use issue relay. Env-config, not repo — note only | info |

## Phase 1 — Workstream plan

| WS | Scope | Mode | Status |
|---|---|---|---|
| **S-AUDIT-A** | Consistency / canonical-doc drift: fix D1–D10 in ARCHITECTURE-CANONICAL + ROADMAP; run `workplan-vs-architecture`. Add the M16 row + retitle the milestone table + add this audit's ROADMAP entry. | lead + 1 agent | pending |
| **S-AUDIT-B** | Liveness / zombie hunt (bot): integration inventory (brokers `EXCHANGE_MAP`, services/timers, workflows, env-gates, transports) → 3 probes each → LIVE/keep/ZOMBIE. | agent → lead PR | pending |
| **S-AUDIT-C** | Consumer wiring & display correctness (dashboard + android): every consumed endpoint exists + shapes match; null handling; 48-strategy/9-account reality renders; real/paper/prop isolation. | agent → lead PR | pending |
| **S-AUDIT-D** | Data audit (canonical store): `trade_journal.db` + `trainer_store.db` integrity, orphans/`reconcile_status`, real/paper/prop isolation, single-source-of-truth. Via diag relay. | lead (relay) | pending |
| **S-AUDIT-E** | Per-line code sweep (`src/`): fan out over directory slices for dead code / correctness / drift. Coverage map below. | agents → lead | pending |
| **S-AUDIT-F** | VM audit (live + trainer + gateway): services/timers state, `.env` inventory, running SHA vs main, disk, `/opt` symlink. Via issue relay (direct diag broken — ENV1). | lead (relay) | pending |
| **S-AUDIT-G** | Backlog drawdown: health (202) + performance (66) + ml (66) — triage, close resolved, action tractable. | agent → lead | pending |
| **S-AUDIT-H** | Stale PR/issue closeout + governance: open PRs, stale issues, session-board hygiene. | lead | pending |

## Per-file coverage map (append as read — "every line" is verifiable, not asserted)

Format: `path — reader — verdict`. Blank = not yet reached.

### Canonical docs (Phase 0)
- `docs/CLAUDE-RULES-CANONICAL.md` — lead — READ FULL, edited (R1–R5)
- `docs/ARCHITECTURE-CANONICAL.md` — lead + agent — READ FULL, drift D1–D10 logged (fixes pending S-AUDIT-A)
- `ROADMAP.md` — agent — READ (header + tables), drift D7–D10 logged
- `CLAUDE.md` (bot root) — lead — READ FULL
- `config/strategies.yaml` — lead — counted (48 cells)
- `config/accounts.yaml` — lead — counted (9 accounts)

_(subsequent sessions append their coverage here)_

## Phase 3 — discovery findings (agents B / C / E1 / G, 2026-07-09)

Overall: **the system is in strong shape.** No Prime-Directive violation; all
brokers LIVE or documented-dry; consumers wire cleanly; Cloudflare/Vercel purged
clean. The real yield is one latent live-money bypass, a handful of Tier-1
zombies, and 7 pre-existing backlog system-risks worth escalating.

### S-AUDIT-E (order/risk-path code sweep) — Prime-Directive CLEAN
- **E1-F1 (escalate, Tier-3):** the **legacy `safe_place_order` fallback**
  (`pipeline.py:736-754` → `orders.py:248`) can place an **unsized (qty=1.0),
  SL/TP-less live order** — bypassing `RiskManager.position_size` AND the SL/TP
  refusal `execute_pkg` enforces. Reached when an actionable signal lacks
  stop_loss/take_profit (builder bug / synthetic signal) or if
  `MULTI_ACCOUNT_DISPATCH=false`. Latent (multi-account fast-path is default) but
  a live-money bypass of the one sanctioned order path. FIX: gate the legacy live
  `place_order` behind the same valid-SL/TP guard, or route everything through
  `execute_pkg`. Confirm reachability against prod builders first.
- **E1-F2 (Tier-1, fold into A):** `intents.py::DEFAULT_PRIORITIES` comments call
  `squeeze_breakout_4h` "execution:shadow (never arbitrates a real order)" — it's
  live; `turtle_soup`/`vwap` comments also stale. Same D2 drift, second location.
- **E1-F3 (Tier-1 doc):** `account_state.yaml` dry-only override
  (`orders.py::account_state_dry_run` → `coordinator.py`) is a live,
  Prime-Directive-compliant belt-and-suspenders gate the "two gates / mode: is the
  only toggle" wording omits. Reconcile the wording (done in ARCH Known-gaps).
- **E1-F4 (Tier-1):** `orders.py::_as_bool` — confirmed dead (no callers).
- **E1-F5 (Tier-1 note):** two daily-loss computations over different columns
  (`risk_counters.py` `DATE(timestamp)` legacy-path vs `risk.py` `created_at`
  authoritative). Legacy copy retires with F1.
- Defensive design verified sound: SL/TP refusal, zero-qty refusal, no min-lot
  size-up, exchange-first close, fail-safe balance-read.

### S-AUDIT-B (liveness / zombie hunt) — net actionable Tier-1
- **B-Z1:** `cf-worker-deploy` label in `bootstrap-labels.yml` (+ `docs/github-actions-workflows.md`) — no consuming workflow, CF purged → remove.
- **B-Z2:** `stop-micro-zombie` label in `bootstrap-labels.yml` — micro terminated → remove.
- **B-Z3:** `oci-storage-verify.yml` `env: VM_HOST: 158.178.210.252` (terminated micro) → repoint to `141.145.193.91` or retire. (Backlog has BL-20260707-WORKFLOW-STALE-MICRO-DEFAULTS covering 4 such workflows.)
- **B-Z4:** `ict-mes-ibkr-pull.{service,timer}` enabled on trader but NOT in `diag.py::_CANONICAL_UNITS` → add (queryability gap; verify live first via relay).
- **B-Z5:** stale Vercel `DASHBOARD_ORIGIN` value + comment in `deploy/ict-web-api.service` → refresh comment (harmless, optional).
- Env-gate scan: 19 gates, no default-off `*_ENABLED` on a required capability (compliant). 7 undocumented non-trading kill-switches (COMMS_PUSH_ENABLED, INSIGHTS_*, FORECAST_LIVE_DISABLED, …) are candidates to add to the CLAUDE.md env table for completeness (Tier-1, optional).
- Confirmed clean: Cloudflare/Vercel purged (only the sanctioned `purge-cloudflared` cleanup tool remains); binance = comment residue only; tradovate = absent.

### S-AUDIT-C (consumer wiring) — clean; 5 cosmetic
- All endpoints exist; shapes match; base URLs = `141.145.193.91`; real/paper/prop isolated; live enumeration (no stale 48-strategy/9-account hardcodes).
- **C-1** android `network_security_config.xml` header comment self-contradicts (says micro entry "retained" but it's removed) — fix comment.
- **C-2** android `BotApi.kt:1031` NotificationBanner.kind comment omits `operator_warning`.
- **C-3** dashboard `CLAUDE.md` Insights row claims a "canonical 6-strategy fallback" the code doesn't have (S-AUDIT-A dashboard-doc).
- **C-4** narrow last-resort symbol fallbacks (`["BTCUSDT","MES"]`) — only hit when API unreachable; optional widen.
- **C-5** `breakout_1` hardcoded as prop default (fine today; derive from config for multi-prop-account future).

### S-AUDIT-G (backlog triage) — dispositions
- Structure: `{items:[...]}`. Health 202 (78 open / 117 resolved / 6 partial / 1 wont_fix); performance 66 (42 open); ml 66 (23 open). Well-maintained.
- **Close as stale-resolved:** PERF-20260601-006 (regime phase-3 shipped/enforcing). **Likely-obsolete (verify+close):** BL-20260610-AUDIT-7 (CF pages check), BL-20260607-005 (zombie Actions runs).
- **Merge dups:** BL-20260527-004⇄BL-20260528-FCM404; BL-20260629-DEVNULL-CLOBBERED-LIVE-VM⇄-OCI-SOURCE-KILL; BL-20260525-007⇄BL-20260527-002; MES-stale-data cluster (BL-20260526-002 / BL-20260626-MES-BASE-STALE / PB-20260707-NATIVE-MES-PULL).
- **~15 Tier-1 quick-wins** logged (incl. BL-20260707-WORKFLOW-STALE-MICRO-DEFAULTS, BL-20260628-PROP-ISPROP-PREDICATE-DRIFT, BL-20260618-CLOSEDFLAT-MALFORMED-JSON, tier normalization).
- **7 OPEN SYSTEM RISKS to escalate:**
  1. **BL-20260708-IB-WARMUP-WEDGE-RECUR** (T3) — IB exec client wedges on `account_warmup_timeout` after a fresh trader restart → MES/MGC/MHG couldn't execute. Root cause open.
  2. **BL-20260629-DEVNULL-OCI-SOURCE-KILL** — OCI host agent clobbers `/dev/null` on the live VM → blocks all operator-action deploy wrappers.
  3. **BL-20260707-ALPACA-PAPER-NEGATIVE-EQUITY** (T2) — reconciler mass false-close drove negative paper equity (root-cause confirmed).
  4. **BL-20260705-DASHBOARD-API-TOKEN-UNSET** (T2) — `DASHBOARD_API_TOKEN` unset on live VM → `POST /api/bot/prop/report` DB-write path unauthenticated.
  5. **BL-20260706-PROP-INSERT-FILL-IDEMPOTENCY** (T2) — `insert_fill` blind append → a re-reported fill creates a phantom prop position.
  6. **BL-20260618-CLOSEDFLAT-MALFORMED-JSON** (T1) — `closed_flat_invariant` integrity query fails "malformed JSON" on live VM → a DB-integrity invariant is blind.
  7. **BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB** (T1) — `/api/bot/health/*` + `/accounts/balances` intermittently non-200 under a blocking DB read.

### Pending (this program, not yet done)
- ROADMAP D7 (add M16 row) + D8 (fix "Active milestone queue" staleness). **D7 caution:** the `/api/bot/roadmap` parser keys on the literal "## M0..M15 Milestone Roadmap" heading — verify roadmap.py before retitling (retitle = coordinated code+doc change, or keep heading + just add the M16 row).
- Diag relays in flight: #6017 (snapshot), #6018 (db_info), #6019 (trainer) — S-AUDIT-D/F.
- S-AUDIT-E further slices (E2+: reconciler bodies in order_monitor.py, signal builders, web/api routers, prop/, ml/) — E1 covered the core money path only.
- S-AUDIT-H (stale PR/issue closeout) — not started.
- Apply the B zombie cleanups + G quick-wins + close-outs (Tier-1 batch).

## Phase 4 — structural-fix program (operator directive 2026-07-09: no backlog, no band-aid, verify live)

Elevated standard: every problem across all 3 repos is root-caused + structurally
fixed + verified LIVE. Tier-3 money-path merges pause for explicit operator OK.

**Determinations made by the lead (unblocking fixes):**
- **D7 ROADMAP heading is SAFE to retitle.** `roadmap.py:150` matches the milestone
  table on the substring `"Milestone Roadmap"` (NOT the literal `"M0..M15"`), and the
  sprint ledger on `"Sprint Ledger"`. So retitle `## M0..M15 Milestone Roadmap` →
  `## Milestone Roadmap` (keep the substring) + add the M16 row. The earlier
  "retitle breaks the parser" caution is withdrawn — verified against the parser.
  Note: filename→milestone rule maps `S-AUDIT-*` → M17 (roadmap.py:63).
- **RISK-4 (/dev/null clobber) root cause CONFIRMED** (`docs/runbooks/devnull-guard.md`):
  OCI `oracle-cloud-agent` `oci-wlp` (workload-protection/FIM) remediates
  world-writable files and clobbers `/dev/null`. 3 self-heal layers already prevent
  breakage (guard timer + deploy_pull_restart + _lib.sh::require_systemctl). Structural
  source-kill = exclude `/dev` from the FIM profile on the live VM. Requires a NEW
  allowlisted system-action (identify via `ausearch -k devnull`, then apply the
  exclusion) — Tier-2 VM-infra, one operator ack to run the mutating step. Three linked
  backlog items collapse here: BL-20260629-DEVNULL-OCI-SOURCE-KILL (root),
  -CLOBBERED-LIVE-VM (symptom), BL-20260706-PROP-REPORT-DEVNULL-NOISE (prop-relay symptom).

**Relay contract note (Tier-1 doc fix):** the `vm-diag-snapshot` relay resolves diag
paths from the issue **BODY** (one path per line), NOT the title — a prose body is
rejected as an "illegal path". CLAUDE.md's example implies title=path; clarify the doc.

**Structural-fix agents in flight (2026-07-09):** RISK-1 (reconciler false-close +
closed-flat integrity), RISK-2 (IB warm-up wedge), RISK-3 (web-api blocking-DB + prop
auth/idempotency + is_prop predicate), E1-F1 (order-path bypass). Each returns
root-cause + exact structural fix + regression test + tier + live-verify. Lead
implements (single-writer), presents Tier-3 for approval, deploys + diag-verifies.

## Honesty / coverage gaps so far
- VM/data state NOT yet pulled (direct diag broken per ENV1; issue relay pending in S-AUDIT-D/F).
- `src/` per-line sweep NOT started (S-AUDIT-E).
- Dashboard + Android repos NOT yet read (S-AUDIT-C).
- D2 (`squeeze_breakout_4h` live vs doc-shadow) needs a `git log -p` premise check before the doc is "fixed" — field-beats-comment says config wins, but confirm the live gate is intended, not an accidental flip.
