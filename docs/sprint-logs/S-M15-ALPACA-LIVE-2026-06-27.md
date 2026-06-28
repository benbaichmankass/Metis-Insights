# Sprint Log: S-M15-ALPACA-LIVE-2026-06-27 (ict-git-sync revert loop fix + Alpaca auth investigation)

## Date Range
- Start: 2026-06-27
- End: 2026-06-27 (Alpaca live auth blocked; operator action pending)

## Objective

Follow-up on the 2026-06-25 `alpaca_live` activation session: resolve the
`alpaca_live: false` status that persisted after set-account-mode was run,
confirm credentials on the VM, and diagnose the Alpaca "request is not
authorized" balance error. Close out with documentation.

## Tier
- PR #4726 (`config/accounts.yaml` `mode: live` committed): Tier 3 — operator
  pre-approved in-chat as part of the activation approval 2026-06-25.
- sync-vm-secrets #4746 (no-op confirmation run): Tier 1 — read/observe.
- Diag reads (#4739, #4747): Tier 1 — read-only.

## Root Cause Analysis

### Issue 1 — `alpaca_live: false` in runtime_status after set-account-mode

`set-account-mode` writes `mode: live` directly to the disk file
`config/accounts.yaml` on the VM. `ict-git-sync` runs
`scripts/deploy_pull_restart.sh` every ~5 min, which executes
`git reset --hard origin/main` — hard-resetting ALL tracked files to the
repo's `main` HEAD. Since PR #4551 had committed `mode: dry_run` (the
then-intended safe default), every git-sync run was silently reverting the
set-account-mode disk edit within 5 minutes, before the next trader tick
could observe it.

**Fix**: PR #4726 — committed `alpaca_live.mode: live` to `main` so git-sync
now PULLS the `live` state instead of reverting it. The comment on the field
documents both approvals (Tier-3 2026-06-25 strategy assignment + live-flip
2026-06-26 set-account-mode issue #4725). Merged SHA
`1a674618a99d080d2775a76e64dffbb52e5aa917`. Confirmed via diag #4739:
`git_sha: 1a674618` + `status.live.alpaca_live: true`.

### Issue 2 — `alpaca balance: request is not authorized` (4 occurrences at 05:00 UTC)

After the ict-git-sync loop was fixed, the `ict-hourly-snapshot.service`
attempted a balance read for `alpaca_live` and logged:

```
alpaca balance: request is not authorized
```

**Investigation**: Dispatched sync-vm-secrets run #28280398958 (issue #4746).
Result: **exit 0, all 12 secrets "unchanged (skip write)"** — the idempotent
compare-before-patch confirmed that the credentials present on the VM's `.env`
are exactly the values stored in GitHub Actions secrets. No credential gap.

**Root cause (diagnosis, not yet confirmed by operator)**: The credentials
themselves are valid for one Alpaca environment but rejected by another. Alpaca
has two distinct API endpoints:
- Paper: `https://paper-api.alpaca.markets`
- Live: `https://api.alpaca.markets`

`alpaca_live` uses `alpaca_env: live`, which routes to the live endpoint.
Paper-account keys (generated on the paper dashboard) are rejected by the live
endpoint with "request is not authorized". The most likely explanation is that
`ALPACA_API_KEY_ID_LIVE` / `ALPACA_API_SECRET_KEY_LIVE` hold paper-account
keys rather than live-account keys.

**Blocked**: This is a genuine operator hand-off — a human must log into the
Alpaca console and verify that the keys stored under `ALPACA_API_KEY_ID_LIVE`
/ `ALPACA_API_SECRET_KEY_LIVE` in GitHub Actions are from the **live** (funded,
real-money) Alpaca account, not the paper account. Once confirmed (or corrected
and re-synced), `api_ok: true` should appear in the next hourly snapshot.

## Work Completed

### PR #4726 — Commit `alpaca_live.mode: live` to repo (Tier 3)
- Updated `config/accounts.yaml::alpaca_live.mode` from `dry_run` to `live`.
- Added inline comment documenting both the Tier-3 strategy-assignment approval
  (2026-06-25) and the live-flip approval (2026-06-26, issue #4725).
- Merged to `main` SHA `1a674618a99d080d2775a76e64dffbb52e5aa917`.
- CI: all checks green.
- Effect: `ict-git-sync` now syncs `mode: live` on every pull instead of
  reverting it.

### Diag #4739 — Confirm `status.live.alpaca_live: true`
- Dispatched `vm-diag-snapshot` issue; confirmed `git_sha: 1a674618`,
  `status.live.alpaca_live: true`. Gate-1 of the ict-git-sync revert loop
  is resolved.

### Issue #4746 — sync-vm-secrets no-op confirmation
- Dispatched `sync-vm-secrets-request` to verify credential state.
- Result: all 12 secrets "unchanged (skip write)". Credentials ARE on the VM
  and match GitHub Actions secrets exactly.
- Implication: credentials are not missing — they are present but rejected by
  the live Alpaca endpoint.

### Diag #4747 — Confirm auth error in journalctl
- Dispatched `vm-diag-snapshot`; `bot_uptime_s: 23482` (~6.5h, no restart —
  correct since sync was a no-op). Observed `alpaca balance: request is not
  authorized` in the hourly snapshot logs.

## Validation Performed

- PR #4726 CI: all checks green.
- `status.live.alpaca_live: true` confirmed via diag #4739.
- Credentials confirmed present on VM (sync-vm-secrets issue #4746, all
  unchanged).
- Auth error confirmed: `alpaca balance: request is not authorized` — live
  endpoint rejects current keys.

## Risks and Follow-Ups

- **Operator action required (genuine hand-off)**: Verify in the Alpaca console
  that `ALPACA_API_KEY_ID_LIVE` / `ALPACA_API_SECRET_KEY_LIVE` in GitHub
  Actions are live-account keys, not paper-account keys. If wrong: update the
  secret values, then re-run `sync-vm-secrets` (label `sync-vm-secrets-request`
  in ict-trading-bot repo). Once correct, `api_ok: true` should appear in the
  next hourly snapshot.
- **Verification gate (PB-20260626-001 gate 1)**: `/api/bot/accounts/balances`
  showing `alpaca_live: {api_ok: true, balance: ~150}` — blocked until above.
- **Verification gate (PB-20260626-001 gate 2)**: First live `order_packages`
  row for `alpaca_live` at US market open (13:30 UTC) — blocked until gate 1.
- `alpaca_live` is `mode: live`, 12 strategies routed, `account_class:
  real_money` — the only remaining barrier is the credential/auth issue above.
  No code change or config change needed on this side.

## Doc Freshness (end-of-session sweep)

- `canonical-doc-coherence.py`: 4/4 checks PASS.
- Dead IP grep: no new hits.
- Removed gates grep: no new hits.
- Instruction hierarchy: unchanged, consistent.
- Decision-landing:
  - PR #4726 (Tier-3 account change): landed in ROADMAP.md M15 row + this
    sprint log.
  - ict-git-sync root cause finding: this sprint log.
  - sync-vm-secrets no-op + auth error: this sprint log.
  - Alpaca auth failure (active, operator-blocked): backlog entry
    `BL-20260626-ALPACA-LIVE-KEY-VERIFY` updated with 2026-06-27 findings.

## Contradictions or Drift Found

- None in the canonical set.
- The ROADMAP.md M15 row was missing this session's PR #4726 and the Alpaca
  auth finding — added this session.

## Backlog Items

- `BL-20260626-ALPACA-LIVE-KEY-VERIFY` (health-review): updated with the
  2026-06-27 finding that sync was a no-op and the auth error persists.
  Operator action remains pending.
