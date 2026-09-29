# Code audit 2026-09-27 — deploy verification (2026-09-29)

> **Doc status:** `live` · category `evidence` · last verified `2026-09-29` ·
> linked from [`docs/audits/code-audit-2026-09-27.md`](code-audit-2026-09-27.md) §5.

**Why this exists.** The audit's §5 (turn 3, 17:25Z) recorded that FIX-CA-01
through 25 were merged and that "the manager reports them deployed and live,"
but that the lead "could **not** confirm deployment independently, because
`/api/diag/version` answers 401." Merged ≠ deployed ≠ observed (`CLAUDE.md`).
This doc closes that gap for all 32 evidence-settled fixes plus FIX-CA-01b/01c,
with each of the three facts established independently this session
(2026-09-29, ~09:30–09:45Z) rather than taken on report.

## Method

1. **Merged** — for each PR, the merge commit was located directly in
   `git log origin/main --grep '#<PR>'` (all 11 PRs squash-merged to a single
   commit each) and its commit hash/timestamp read with `git log -1`.
2. **Deployed** — the merge commit's ancestry was checked with
   `git merge-base --is-ancestor <commit> <deployed_sha>` against the git SHA
   each VM is actually running, read live:
   - **Live VM (`ict-bot-arm`):** direct HTTPS diag transport,
     `GET /api/diag/version` → `git_sha=b9d518516`, `git_sha_on_disk` identical,
     `restart_pending: false`, captured `2026-09-29T09:39:52Z`. `git_sha` equals
     `origin/main` HEAD (`b9d5185162194f9c016daa350be7b94e6927fbba`) exactly. A
     second read (`/api/diag/status`) showed `bot_uptime_s: 169` at
     `09:40:51Z` — the trading process itself had restarted onto this SHA
     roughly 3 minutes earlier, so the *running* process, not just the
     on-disk tree, holds this code.
   - **Trainer VM (`ict-trainer-vm`):** `trainer-vm-diag` relay (issue #14044,
     run [36550950020](https://github.com/benbaichmankass/Metis-Insights/actions/runs/36550950020)),
     `git rev-parse HEAD` in `/home/ubuntu/ict-trading-bot` →
     `84d6867126e42bd7b6633a4e00832e4101b364d8`, captured `2026-09-29T09:43Z`.
   - **GitHub-Actions-only surfaces** (workflow trigger conditions, CI guards
     that run from a fresh `git checkout` of `main` on every invocation, no
     VM install step): "deployed" is merge to `main` itself. Confirmed by
     showing the workflow has actually *run* since the merge (Actions run
     history), not merely that the YAML parses.
3. **Observed** — a live runtime signature was pulled for every fix where one
   is currently producible without waiting for a rare trigger condition
   (diag reads, `/api/bot/*` reads, `journalctl`, GH Actions run history,
   `systemctl list-timers`). Where the fix's signature needs a specific event
   that has not recurred since deploy (a DB write failure, a stuck leg, a
   cross-day loss, an unauthorized workflow-trigger attempt, …), this is
   stated honestly as **not yet observed**, together with the exact event
   that would produce one. A last-600-record pull of `/api/diag/audit` was
   grepped for the runtime keywords several fixes would emit
   (`protection_refused`, `net_position_unreadable`, `could_not_look`,
   `test_ping`, `UNDELIVERED`, `cancel_in_flight`, `DAILY_LOSS_CAP`,
   `unreadable_symbols`, `held_open_for_leg`, `ADVERSE`/`FAVORABLE`) — **zero**
   hits for all of them, i.e. none of those trigger conditions have recurred
   in the last ~600 audited events, not that the fixes are absent.

All 32 fixes' merge commits, and FIX-CA-01c's, were independently confirmed as
ancestors of the deployed SHA on the VM their code runs on
(`git merge-base --is-ancestor` — exit 0 for every one checked; see per-row
evidence below). **Everything in §5 that has code to deploy is merged and
deployed. Nothing found here contradicts the manager's "deployed and live"
report.**

## Summary table

| Fix | Finding | Tier | Merge commit (PR) | Deployed | Observed |
|---|---|---|---|---|---|
| FIX-CA-01 (Part B) | CA-A01-001 | 2 | `245b45d16` (#13187) | ✅ live VM | ⏳ not yet (needs a new flat-close through one of the 3 patched paths) |
| FIX-CA-01 (Part A, ops) | CA-A01-001 remediation | 2 (ops) | n/a — `cancel-ib-order` system action | n/a | ✅ **observed** — `oca-protect-t5836` / orders 906,907 absent from `ib_open_orders` |
| FIX-CA-01c | git-sync stop-marker + 10148 grading | 2 | `e3af98f9b` (#13241) | ✅ live VM | ⏳ not yet (needs a stop→git-sync window) |
| FIX-CA-02 | CA-A01-006 | 2 | `f1ce43611` (#13251) | ✅ live VM | ⏳ not yet |
| FIX-CA-03 | CA-A01-007 | 2 | `f1ce43611` (#13251) | ✅ live VM | ⏳ not yet |
| FIX-CA-04 | CA-A01-018 | 2 | `f1ce43611` (#13251) | ✅ live VM | ⏳ not yet |
| FIX-CA-05 | CA-A01-033 | 2 | `f1ce43611` (#13251) | ✅ live VM | ⏳ not yet |
| FIX-CA-06 | CA-A01-073 | 2 | `f1ce43611` (#13251) | ✅ live VM | ⏳ not yet (`protection_refused` = 0 hits in last 600 audit rows) |
| FIX-CA-07 | net-position-read-failure-reads-flat | 2 | `3f60a7e53` (#13244) | ✅ live VM | ⏳ not yet (`net_position_unreadable` = 0 hits) |
| FIX-CA-08 | closed-flat-partial-read-grades-flat | 2 | `3f60a7e53` (#13244) | ✅ live VM | ⏳ not yet (`could_not_look`/`unreadable_symbols` = 0 hits) |
| FIX-CA-09 | daily-loss-cap-by-open-date | 3 | `a334a23e2` (#13245) | ✅ live VM | ⏳ not yet (`DAILY_LOSS_CAP` = 0 hits; needs a cross-day realized loss past cap) |
| FIX-CA-10 | closeall-leg-id-not-forwarded | 2 | `3f60a7e53` (#13244) | ✅ live VM | ⏳ not yet (needs an operator `/closeall` on a partial-TP/SL Bybit account) |
| FIX-CA-11 | testping-journals-and-can-suppress-real-trade | 2 | `dda726d8d` (#13246) | ✅ live VM | ⏳ not yet (`test_ping` = 0 hits; needs a `send-prop-test-ping` dispatch) |
| FIX-CA-12 | diag-venue-reads-collapse-could-not-look | 2 | `dda726d8d` (#13246) | ✅ live VM | ✅ **observed** — see below |
| FIX-CA-13 | critical-alert-dropped-on-send-failure | 2 | `dda726d8d` (#13246) | ✅ live VM | ⏳ not yet (`UNDELIVERED` = 0 hits; needs a Telegram send failure) |
| FIX-CA-14 | CA-A10-200 (rebuild-pnl apply gate) | 2 | `dda726d8d` (#13246) | ✅ live VM | ⏳ not yet (needs a `rebuild-pnl-from-bybit` dispatch) |
| FIX-CA-15 | CA-A10-402 (status_check failed-unit gate) | 1 | `9a9f9e7d7` (#13247) | ✅ live VM | ⏳ not yet (needs a failed one-shot unit to flip the gate) |
| FIX-CA-16 | gpu-burst-actor-guard | 1 | `9a9f9e7d7` (#13247) | ✅ GH Actions (merged to `main`) | ⏳ not yet (no unauthorized-issue attempt has occurred; guard's own self-test passes in CI) |
| FIX-CA-17 | session-reaper-dead-trigger | 1 | `9a9f9e7d7` (#13247) | ✅ GH Actions | ✅ **observed** — see below |
| FIX-CA-18 | ib-login-test-password-grep | 2 | `dda726d8d` (#13246) | ✅ GH Actions | ⏳ not yet (needs the next `vm-ib-gateway-live-login-test` run to see the allowlist in effect) |
| FIX-CA-19 | backfill-monitor-sign-guard | 2 | `dda726d8d` (#13246) | ✅ live VM | ⏳ not yet (`ADVERSE`/`FAVORABLE` = 0 hits; needs a sign-mismatched backfill candidate) |
| FIX-CA-20 | shadow-gates-read-active-log-only | 1 | `011e12212` (#13252) | ✅ trainer VM | 🟡 partial — see below |
| FIX-CA-21 | oos-edge-discards-manifest-purge-horizon | 1 | `011e12212` (#13252) | ✅ trainer VM | ⏳ not yet (needs the next `gate-check`/`_oos-edge-one` run) |
| FIX-CA-22 | advisory-hold-when-nothing-measured | 1 | `011e12212` (#13252) | ✅ trainer VM | ⏳ not yet (needs the next promotion-readiness sweep on an advisory head with `drift=None`) |
| FIX-CA-23 | regime-scoring-uses-forming-bar | 2 | `1ea037457` (#13253) | ✅ live VM | ⏳ not yet |
| FIX-CA-24 | fc-live-no-staleness-check | 2 | `1ea037457` (#13253) | ✅ live VM | ✅ **observed** — see below |
| FIX-CA-25 | fc-producer-forecasts-forming-bar | 2 | `1ea037457` (#13253) | ✅ trainer VM (timer) + live VM (scorer) | ✅ **observed** — see below |
| FIX-CA-26 | m21-entry-sweep-inert-fold-inflation | 1 | `471e37ce8` (#13314) | ✅ trainer VM (offline tooling) | ✅ **observed** — the PR's own offline re-grade already ran (see below) |
| FIX-CA-27 | fc-geometry-join-ignores-strategy | 1 | `471e37ce8` (#13314) | ✅ trainer VM (offline tooling) | ⏳ not yet (needs a concurrent same-symbol multi-strategy close to resolve) |
| FIX-CA-28 | exit-head-workflow-never-applies-net-of-fee-cost | 1 | `471e37ce8` (#13314) | ✅ GH Actions | ⏳ not yet (no real `research-exit-head-build` dispatch has run since merge — see below) |
| FIX-CA-29 | exit-sweep-shared-tmp-race | 1 | `471e37ce8` (#13314) | ✅ trainer VM (offline tooling) | ⏳ not yet (needs a concurrent `m20_exit_sweep`/`m20_trail_resweep` run) |
| FIX-CA-30 | ci-guard-env-gate-blind-to-core-and-main | 1 | `4ed9505af` (#13316) | ✅ GH Actions (runs every PR) | ⏳ not yet (nobody has added a new `*_ENABLED` gate to `src/core/`/`src/main.py` since) |
| FIX-CA-31 | pull-alpaca-fills-collapses-api-failure-as-empty | 2 | `4ed9505af` (#13316) | ✅ live VM | ✅ **observed** — see below |
| FIX-CA-OPS2 | fills-pull unit split (manager follow-up) | 2 | `4ed9505af` (#13316) | ✅ live VM | ✅ **observed** — see below |
| FIX-CA-32 | third-execution-gate-undocumented | 1 | `4ed9505af` (#13316) | ✅ live VM | ✅ **observed** — see below |

## Observed — direct evidence

- **FIX-CA-01 Part A (ops remediation).** `diag_fetch.sh 'ib_open_orders'` on
  `ib_paper` now returns exactly 2 resting orders (both `MHG`, OCA group
  `248275453`). No `oca-protect-t5836` group, no order 906/907. The stray this
  finding was about is gone.
- **FIX-CA-12.** `GET /api/diag/exchange_positions?account_id=nonexistent_test_account_xyz`
  (with a valid bearer) → **HTTP 404**,
  `{"detail":{"error":"unknown_account_id","account_id":"nonexistent_test_account_xyz","configured":[...]}}`.
  This is exactly the new contract the fix describes (previously: HTTP 200,
  `accounts: []`).
- **FIX-CA-17.** `session-reaper.yml` has fired on essentially every push to
  `main` since the fix — 700+ runs, most recent completed run (#740,
  `success`) at `2026-09-29T09:34:22Z`≈`09:40:35Z`, another in progress
  (#741) at capture time. Zero-runs-for-days is gone.
- **FIX-CA-24.** `GET /api/bot/shadow/stats` →
  `forecast_serve.read_state: "observed"`, `counters.fc_served: 2`,
  `counters.fc_stale: 0`, `max_lag_bars: 1`, both live advisory symbols
  (`BTCUSDT`, `SOLUSDT`) show `lag_bars: 0`, `served: true`. The staleness
  plumbing is live and currently serving fresh forecasts.
- **FIX-CA-25.** Trainer `systemctl list-timers 'ict-trainer-forecast.timer'`
  → `LAST 2026-09-29 09:30:30 UTC`, `NEXT 2026-09-29 09:45:30 UTC` — exactly
  the `:00:30/:15:30/:30:30/:45:30` bar-aligned grid the fix installed
  (`OnCalendar=*:0/15:30`), not the old free-running 15-min interval.
- **FIX-CA-26.** The offline re-grade the PR performed is itself the
  observation: `docs/audits/code-audit-2026-09-27/FIX-CA-26-m21-regrade.json`
  shows the 3 real-money cells (`trend_donchian_xrp_4h` × `skip_h0`/`vol_hi90`,
  `ada_pullback_2h` × `vol_lo10`) hold at effective PASS with 0 inert folds;
  3 non-roster/shadow cells flip to `wf_fail` once inert folds are excluded.
  No config change was needed.
- **FIX-CA-31 / FIX-CA-OPS2.** `journalctl -u ict-alpaca-fills-pull` shows the
  unit running on its own hourly schedule (`05:24:44Z`, `06:26:26Z`, …),
  logging the new `ok=4 failed=0 skipped=0 of 4 total_inserted=0` line
  literally naming `(FIX-CA-OPS2)` in its own systemd description, and
  `/api/diag/services` confirms `ict-alpaca-fills-pull.service`/`.timer` exist
  as units independent of `ict-exchange-fills-pull` (the Bybit wallet-ledger
  unit they were split from so an Alpaca failure can no longer block it).
- **FIX-CA-32.** `GET /api/bot/config` → every account object now carries
  `"account_state_dry_run": false` (checked on `bybit_1`; present repo-wide
  per the fix). The third-gate fold is now visible on the operator's own
  config surface, not just in code.

### FIX-CA-20 — partial

`ml/shadow/inspector.py::iter_records_with_archives` (added at line 293) is
present and deployed on the trainer VM's working tree. `runtime_logs/drift_retrain.jsonl`
was rewritten at `2026-09-29T09:20Z` (this session's capture), i.e. a
`drift_retrain` consumer of this helper *has run* since deploy — but the relay
read did not separately confirm that a specific invocation actually pulled
rows from a rotated archive file (vs. having nothing to pull from the archive
window this run). Recorded as partial rather than fully observed for that
reason.

## Not deployed — none found

Every fix's merge commit is an ancestor of the SHA its target actually runs:
all `src/`, `scripts/ops/*_action.sh`, `deploy/*` fixes are ancestors of the
live VM's `b9d518516` (== `origin/main` HEAD); all `ml/`, `scripts/ml/`,
`scripts/research/` fixes are ancestors of the trainer VM's `84d6867126e...`;
the CI-guard and workflow-trigger fixes are on `main` and their workflows have
demonstrably run since. **No deploy gap, no un-restarted service, and no
un-pulled trainer were found in this pass.** This corrects nothing in the
audit's own §5 landing-status note — it independently confirms it.

## The six judgment calls — current status (out of scope to re-litigate; status only)

All six already carry an operator decision as of this check — **none is
still awaiting one**, though three have not finished landing cleanly:

| JC | Operator decision | Status |
|---|---|---|
| JC-CA-01 | Option A — durable commit to `main` on `set-account-mode` | **Decided, `landed_unproven`.** #13508 merged (`3deb2385`); deploy/observe convergence tracked separately (issue #13572, `PI-20260928-TURC5MJC-0006`), not by this doc. |
| JC-CA-02 | Option B — remove `htf_trend_filter_enabled` from the 7 legs | **Decided and landed.** #13505 merged (`6a299600`). |
| JC-CA-03 | Re-decided 2026-09-28 12:10Z: "Keep PAT, verify run" (API-verified provenance over Option A/B/C) | **Decided, blocked in review.** Implementation PR #13609 sent back by an independent review (B1: A7 grades the synthetic merge commit so the route refuses every genuine PR; B2: nothing binds PR content to the run id) — not yet re-landed. |
| JC-CA-04 | Option B — post-merge `hold`-violation alarm | **Decided and landed.** #13507 merged (`78d00c89`). |
| JC-CA-05 | Option B — fail loud, no auto-revert | **Decided and landed.** #13509 merged (`3c441d37`). |
| JC-CA-06 | Option A (retire the `account_state.yaml` fold), contingent on JC-CA-01's fix being observed converged | **Decided in principle, execution gated on JC-CA-01.** Not yet actioned; FIX-CA-32 (documenting the fold) already landed regardless of this call. |

## Evidence trail

- Live-VM reads: direct HTTPS diag transport, `/api/diag/version`,
  `/api/diag/status`, `/api/diag/services`, `/api/diag/audit?limit=600`,
  `/api/diag/ib_open_orders`, `/api/diag/exchange_positions` (negative-account
  probe), `journalctl?unit=ict-alpaca-fills-pull`, all captured
  `2026-09-29T09:39–09:46Z`.
- Live-VM `/api/bot/*` reads (no diag token, public dashboard API):
  `/api/bot/config`, `/api/bot/shadow/stats`.
- Trainer-VM read: `trainer-vm-diag` relay, issue #14044, run
  [36550950020](https://github.com/benbaichmankass/Metis-Insights/actions/runs/36550950020),
  captured `2026-09-29T09:43Z`.
- GitHub: `pull_request_read` (get) on PRs #13187, #13241, #13244, #13245,
  #13246, #13247, #13251, #13252, #13253, #13314, #13316; `actions_list`
  (`list_workflow_runs`) on `session-reaper.yml`, `guards.yml`,
  `research-exit-head-build.yml`.
- Local repo: `git fetch --deepen=2000 origin` (this clone started shallow —
  see `BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE`), then
  `git log origin/main --grep '#<PR>'` to find each squash-merge commit and
  `git merge-base --is-ancestor <commit> <deployed_sha>` to prove deployment.
