# Full-System Audit — 2026-08-04

> Program doc (the shared brain) per `.claude/skills/full-system-audit/SKILL.md`.
> Session `system-review-audit-krgr52`, branch `claude/system-review-audit-krgr52`.
> **Predecessor:** `full-system-audit-2026-07-31.md` (4 days prior). This is a
> fresh pass building on it: verify which 07-31 open items resolved, find what's
> new, re-check VM/data/consumer state as of 2026-08-04.

## Phase 0 — Rules audit (DONE)

- Canonical corpus read highest-precedence first. `check_canonical_doc_coherence.py`
  → **all 4 checks PASS** (dead VM IP single-source · removed-gates-not-live ·
  no-7-stage-ladder · instruction-hierarchy mirror).
- Clone arrived **SHALLOW (50 commits)** — the known
  `BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE` hazard. Unshallowed
  (`git fetch --unshallow` → 3093 commits) before any history claim.
- No rule-level contradiction found that bends the yardstick. The corpus already
  absorbed the 07-30/07-31 incident-cluster rules (provenance, "always state the
  population", "green is not evidence"). **Yardstick current.**

## Workstreams

| WS | Scope | Mode | Status |
|---|---|---|---|
| A — Verify 07-31 open items (promotion gate, outcome-labels, netting, guards, bybit smoke, backlogs) | agent | running |
| B — Bot repo liveness/zombie + env-gate + service/workflow inventory | agent | running |
| C — Consumer repos (Streamlit + Svelte webapp + android): provenance surfacing, doc drift, parity | agent | running |
| D — VM + data via diag relays (live #8439, trainer #8440) | lead | in progress |
| E — Loose ends (open PRs/issues) + audit report | lead | in progress |

## Findings (running)

### WS-E — Loose ends (lead, partial)
- **Open PRs across all 3 repos: ZERO.** No PR debt. ✅ (matches 07-31's clean-PR state.)
- **35 open issues in metis-insights**, ~34 are research/system-action DISPATCH
  relays; oldest `#7769` (research-panel-build, 2026-07-27, **zero comments**),
  a cluster of 07-30 regime re-audit dispatches (`#7955`–`#7990`), 07-31/08-02/08-03
  waves. This is **issue-tracker accumulation** — the same alarm-fatigue-on-a-queue
  pattern the 07-31 audit named on the review *backlogs*, now visible on the *issue
  tracker*. Whether each dispatch actually completed is not verifiable from the
  session; several carry only the 1 acknowledgement comment. **Disposition:** MED,
  Tier-1 — a triage/close-out sweep of completed dispatch issues is warranted; a
  dispatch issue with no result comment after >5 days is a dead run to re-drive or close.
- **`#8208` — Tier-2 `set-env TELEGRAM_CLAUDE_BOT_TOKEN` + restart `ict-claude-bridge`
  (token rotation after `BL-20260801-TELEGRAM-BOT-TOKEN-COMPROMISE`) is OPEN since
  2026-08-01 with ZERO comments** — the system-action never reported back. The
  compromised-token rotation's application to the running bridge is **UNVERIFIED**.
  A revoked token still loaded ⇒ Claude/prop Telegram pings silently 401-fail.
  **Disposition:** HIGH, Tier-2 — verify on the VM whether the new token is live in
  `ict-claude-bridge.service`; if not, re-dispatch. (Security-adjacent: rotation was
  the remediation for a confirmed bot-token compromise.)

### WS-D — VM + data (lead, via relays #8441/#8442)

**Live VM (2026-08-04 05:13Z) — HEALTHY:**
- Heartbeat `running` (age 50s), on `main` (`0ed8a5a6`), uptime ~5.5h.
- Services all `active`: `ict-trader-live`, `ict-web-api`, `ict-telegram-bot`,
  `ict-claude-bridge`. Every timer `active`. **No failed unit** in the enumeration.
- IB: all 3 MES clients `connected`, no breaker open, `account_data_ready:true`;
  last failures were 00:00–00:05Z (inside IBKR's reset window) and recovered. ✅
- Account modes as expected: `bybit_2` (real money) live=true; `alpaca_live`/
  `ib_live`/`oanda_practice` dry. ✅
- **`ict-claude-bridge.service` is active** — a mild positive for the #8208
  token-rotation question, but service-up does NOT prove the *rotated* token is
  loaded (still needs a functional-send check). #8208 stays HIGH/Tier-2 open.
- **0-qty intent pattern PERSISTS** (STILL-OPEN from 07-31): live order pkg
  `pkg-6ad8…` (`ada_pullback_2h`) shows `aggregated_target_qty: 0.0` while
  `sized_qty_by_account bybit_1: 258303`, and carries a `stuck_alert_emitted_at:
  2026-08-03T08:24`. The 0-qty-intent→stuck/orphan class the 07-31 audit named is
  still producing rows. MED, Tier-2 (order-path adjacent → propose, don't self-fix).

**Trainer VM (2026-08-04 05:14Z):**
- On `main` (`0ed8a5a6`) ✅. **Disk 79%** (36G/45G) — improved from 86% on 07-31.
- 7 trainer timers all firing: forecast, publish (22s ago), git-sync, trainer
  (last 00:58, next 08-05 00:51), promotion-readiness (04:15), drift-retrain
  (05:04), catchup (05:00). ✅
- **OUTCOME-LABEL FAMILIES: NOT stale — my first reading was a measurement
  artifact, now WITHDRAWN.** My probe #1 (`ls -td datasets-out/<fam>/*/`) reported
  `trade_outcomes`/`setup_labels`/`execution_quality` → `MES/` May-22 and read that
  as ~74-day staleness. **Definitive re-probe (#8444):
  `datasets-out/trade_outcomes/all/all/v002/data.jsonl` mtime = 2026-08-04 05:00**
  (17 min before the probe), 275 KB — rebuilt TODAY. The real data lives under
  `all/all/v002/*.jsonl`; the `MES/` per-symbol dir is the genuinely-stale empty
  leg. Probe #1 measured the wrong subdir + a directory mtime (doesn't update on
  in-place file rewrite) + the wrong extension (`.parquet`, when it's `.jsonl`) —
  **the exact same diagnostic-lie shape (#8185) WS-A independently code-verified as
  fixed.** The families are ALIVE and building daily. *(Recorded as an audit
  self-catch: the "always state the population / measure the right thing" rule
  applied to my own probe.)*
- (registry stage counts + training_cycle log: my grep pattern missed the
  `list-models` output format / log is `.jsonl` not `.log` — not re-fetched;
  registry stage census last known from 07-31: 93 models, 2 advisory / 28 shadow /
  62 candidate / 1 legacy `research_only`.)

### WS-A — 07-31 open-item delta verification (agent; 107 commits since 07-31)

| # | Item (07-31) | Status now | Evidence |
|---|---|---|---|
| A | Promotion gate reads unfiltered `pnl>0` (highest structural risk) | **RESOLVED** | `ml/promotion/attribution.py::load_closed_trades` now `exclude_untrusted_pnl=True` default → drops non-MEASURED/ESTIMATED via `provenance.pnl_is_trustworthy`; `compute_attribution` inherits it. PR #8163-arc (`f598bc18`). |
| B | Outcome-label families "0 rows for 10 weeks" | **RESOLVED — it was a DIAGNOSTIC LIE, not starvation** | `build_trainer_datasets.sh::build_family` read `row_count` from the alphabetically-first glob (empty May-22 `MES/all/v001`) not the written `all/all/v002` (rebuilt daily). Fixed #8185 (`5efd7762`, `BL-20260731-TRAINER-BUILDLOG-ROWCOUNT-LIE`). MES-scoped families genuinely empty (MES never traded live) → backtest-augmented builds #8319/#8326/#8434. **This retroactively downgrades my WS-D dir-mtime "HEADLINE" below — same measurement artifact.** |
| C | created_at/closed_at format bug | **RESOLVED** | `c71c7dd0` (#8397): `_closed_at.py` re-exports single normaliser + new `timestamp-comparison-guard`; writer stamps ISO on INSERT + migration. 5 live raw-comparison sites in `insights/data_sources.py` were dropping every trades row — fixed. |
| D | Netting partial-close phantom reconcile | **SHIPPED, operator-gated (NOT auto-applied)** | `reconcile_netting_rows.py` pure planner, dry-run default (#8401); `reconcile-netting-rows` **Tier-2 operator-gated system-action** (#8403). `BL-20260801-NETTING-…` correctly stays open pending an operator `--apply` run. |
| E | Guard enforcement (merge_group base_ref, fail-open, vacuity) | **RESOLVED (4/6 spot-checked)** | #8198 (`f0a4ac97`): branch-protection-sync fails RED on missing PAT; macro-producer-liveness vacuity wired + `set -uo pipefail`; `github.base_ref \|\| 'main'`; diag-unit-allowlist + claim-basis guards exist. |
| F | bybit_2 smoke `place_order` NoneType (real-money) + 0-qty intents | **STILL-OPEN (alarm-fatigue candidate)** | No fix commit since 07-31; `health-review-backlog.json:6835` — recurring `smoke_open_result failed_exchange 'NoneType'.place_order` on real-money `bybit_2`, re-validated as **harness/smoke client-resolution defect only, no live-trade impact** (trader trades bybit_2 fine). 0-qty ghost-package half resolved (#4339); bare `target_qty=0.0` is by-design reinforcement. LOW severity but exactly the normalization pattern the operator's "if you see something" rule targets. |
| G | Backlog counts | health **126→97 (−29 drained)**, perf 51→52, ml 37→39 | `/health-review` 08-03 "97/97 triaged". No new CRITICAL. Perf/ml flat, still `kept_open`-standing-watch-inflated (the structural non-drain the 07-31 audit named). |
| — | **NEW incident (RESOLVED): `BL-20260801-TELEGRAM-BOT-TOKEN-COMPROMISE`** | operator command-bot renamed to a spam funnel, token burned | Code remediation via 5-PR arc #8204→#8246 (redact committed tokens, secret-scan blind-spot fix, runtime redaction, fail-secret-free before PTB echo; liveness watchdog decoupled from Telegram so a bad token can't crashloop the money loop). **The VM-side token swap `#8208` is the open residual (WS-E).** |

### WS-C — Consumer repos (agent; static/source, no render-level runtime check)

- **Provenance surfacing LANDED in all 3 clients** (dashboard `c1848f7`, android
  `d77f982`): `/trades/closed pnlProvenance` glyph + caveat, `/performance
  pnlCoverage/…Count` on exec summary; graceful on an older bot. ✅ **RESIDUAL
  (MED, Tier-1):** headline numbers still *numerically* fold fabricated PnL —
  net P&L, win-rate, expectancy, **equity curve, P&L calendar, per-asset/symbol
  bars carry NO caveat**; `totalPnlMeasured` (the honest figure the R4 gate reads)
  is read by NO client (grep empty ×3). Backlog: surface `totalPnlMeasured` or
  extend the caveat to calendar/equity/asset surfaces.
- **Dashboard `CLAUDE.md` omits the Svelte SPA (HIGH, Tier-1, doc) — CONFIRMED,
  still unfixed.** It calls the Streamlit URL "canonical … the Telegram
  system-report deep-link target" and claims "one Streamlit app only", both
  **contradicted by the bot's authoritative `CLAUDE.md`** which says the SPA
  (`benbaichmankass.github.io/ict-trader-dashboard/?report=`) is the deep-link
  target and `webapp/` (20 routes, own `pages.yml` GH-Pages deploy) is a second
  production frontend. Also line 175 says `streamlit_app.py ~6500 lines`; actual
  **9,462**. → lead fixes this in-session (Tier-1 doc).
- **Webapp "Paper" view blends soak accounts — violates S-PAPER-PORTFOLIO (MED,
  Tier-1).** Android + Streamlit scope "Paper" to `paper_role: portfolio`; the
  webapp does not (`paper_role`/`paperPortfolio` absent in `webapp/src/`). **Root
  cause: commit `e823647` "webapp: 'Paper' funding view = live-portfolio mirror"
  actually changed only `streamlit_app.py` + `CLAUDE.md` (verified `--stat`) — the
  webapp never got the fix; the commit subject is a mislabel.** → log for a
  consumer session (needs render-verify).
- **Webapp Learning is an undocumented "coming soon" placeholder** (`App.svelte`
  `<Placeholder page="Learning"/>`; no route/api) while Streamlit+Android fully
  implement it. MED, Tier-1 (record the parity gap). Models/Promotion DO render
  (lighter feeds, depth gap only — prior concern refuted). LOW.
- **Endpoint contract CLEAN** across all three clients (android 50 routes,
  Streamlit 52, webapp ~40 + `/ws/market`) — every hit documented; no
  field-misread; em-dash null rule honored. Real/paper/prop never blended into a
  combined total (legacy Streamlit `_control_bar` "All" path is verified dead
  code). ✅

### WS-B — Bot repo liveness/zombie + env-gate + service/workflow (agent, static)

**Verdict: markedly healthier than 07-31. Zero Prime-Directive / Tier-3 violations.**
- **Env-gates CLEAN.** No required-capability-behind-default-off `*_ENABLED`
  remains; every historically-dangerous gate (`MULTI_SYMBOL_ENABLED`,
  `POSITION_NETTING_GUARD_*`, `NAKED_POSITION_AUTOPROTECT`, `NEWS_ENABLED`,
  `MONITOR_RECONCILE_ENABLED`, `MOBILE_PUSH_ENABLED`) is comment-only, 0 runtime
  reads. `env-gate-guard` REQUIRED + diff-scoped.
- **Services/timers: the 07-31 P2.4 fix landed + passes.** `diag-unit-allowlist-guard`
  ran clean → **45 deploy units, 36 allowlisted, 9 exempt, 0 failures**.
  `_CANONICAL_UNITS` now includes `ict-ib-executions-pull` (07-31 F-1 gap) +
  `ict-research-results-gate`. Installed-but-invisible-unit class closed.
- **W2 guard fixes VERIFIED LANDED**: branch-protection-sync fails RED on missing
  PAT; **required contexts 9→15**; merge_group empty-base_ref fixed comprehensively;
  macro-producer-liveness vacuity → `exit 1` + alert.
- **Broker reachability: no gap.** `EXCHANGE_MAP` is vestigial-but-documented-keep
  (real routing is `execute_pkg`). Not a zombie.
- **Dead scripts:** `dataset_unchanged_check.py` now WIRED (07-31 flag stale);
  `provenance_exposure_audit.py` a cited manual tool (documented-keep);
  `m15_candle_fidelity.py` closest to a true zombie (M15 complete) — eventual cleanup.
- **B-1 (LOW, Tier-1):** `deploy/trainer/*` units covered by NO inventory/liveness
  guard (guard globs top-level `deploy/*` only). → health backlog.
- **B-2 (LOW, Tier-1):** `diag-unit-allowlist-guard` still ADVISORY though passing +
  block-worthy → promotion candidate. → health backlog.
- **B-3 (MED, Tier-1, doc drift):** `docs/github-actions-workflows.md` still
  publishes the **9-entry** required list while live `REQUIRED_CONTEXTS` is **15**
  (field-beats-comment). → **lead fixes in-session.**

## Coverage map
- Lead: CLAUDE-RULES-CANONICAL coherence check, ROADMAP audit rows, prior audit doc
  (07-31, full), coordination board #6927 (tail), open PRs ×3 repos, open issues
  (metis-insights, 35), regime-debt-matrix workflow-run conclusions (skipped=label noise).
- Agents A/B/C: appended on completion.
- NOT yet reached: live VM services/heartbeat (relay #8439 pending), trainer state
  (relay #8440 pending), gateway VM, per-line src/ sweep, prop-journal deep audit.
