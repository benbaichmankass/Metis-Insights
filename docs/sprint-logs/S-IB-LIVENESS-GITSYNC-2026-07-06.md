# Sprint Log: S-IB-LIVENESS-GITSYNC-2026-07-06

## Date Range
- Start: 2026-07-06 (continuation of the same-day IB pipeline stability review)
- End: 2026-07-06

## Objective
- Primary goal: fix and live-verify the IB post-connect liveness probe
  (BL-20260610-009), which had been disabled (`IB_PROBE_TIMEOUT_S=0`) since
  2026-06-10 because it false-tripped the circuit breaker over the cross-host
  socat relay to the isolated gateway VM.
- Secondary goals: none planned — a genuine infra blocker (the repo going
  private mid-session broke the live VM's `git fetch`) became a hard
  prerequisite and consumed most of the session.

## Tier
- Tier 2/3 mixed — the IB liveness-probe code fix + live env flip
  (`IB_PROBE_TIMEOUT_S=5`) touches the live order-adjacent IB connection path
  (operator pre-approved: "you have approval to flip the switch once the fix
  is verified, ping me with updates"). The deploy-pipeline fix
  (`vm-git-credential-bootstrap.yml` + `deploy_pull_restart.sh`) is Tier-2
  (deploy/CI infra, no strategy/risk/account-mode change).
- Justification: no `config/strategies.yaml`, `config/accounts.yaml`,
  `config/risk_caps.yaml`, `src/runtime/orders.py`, or
  `src/runtime/risk_counters.py` touched. `src/units/accounts/ib_client.py`
  changed (retry-tolerant liveness probe), operator-approved before the live
  flip.

## Starting Context
- Active roadmap items: none directly — this closes out backlog items
  `BL-20260610-009` (IB liveness probe) and, as an emergent blocker,
  `BL-20260706-GITSYNC-AUTH-BROKEN` (deploy pipeline auth).
- Prior sprint reference: same-day IB pipeline stability review
  (`docs/research/ib-pipeline-stability-review-2026-07-06.md`, PR #5654)
  that produced the retry-tolerant probe fix but had not yet been deployed
  or live-verified.
- Known risks at start: `IB_PROBE_TIMEOUT_S=0` meant a genuinely wedged IB
  gateway would not trip the breaker via the liveness probe (only the
  per-fetch `IB_FETCH_TIMEOUT_S=8` backstop caught it), which is weaker
  detection than intended.

## Repo State Checked
- Branch or commit reviewed: `main` at each step; final state `d5276f74`.
- Deployment state reviewed: `/api/diag/version` before/after every deploy
  step; `systemctl` state of `ict-trader-live.service` before/after restarts.
- Canonical docs reviewed: `CLAUDE.md` (root), `docs/claude/system-actions.md`,
  `docs/github-actions-workflows.md`.

## Files and Systems Inspected
- Code files inspected: `src/units/accounts/ib_client.py`,
  `scripts/deploy_pull_restart.sh`.
- Config files inspected: none (`.env` mutated only via the `set-env`
  system-action, not read/edited directly).
- Deployment files inspected: `.github/workflows/vm-git-credential-bootstrap.yml`
  (new), `.github/workflows/sync-vm-secrets.yml`, `.github/workflows/bootstrap-labels.yml`.
- Docs inspected/updated: `CLAUDE.md`, `docs/github-actions-workflows.md`,
  `docs/claude/health-review-backlog.json`.
- Services or timers inspected: `ict-trader-live.service`,
  `ict-git-sync.timer`, the `ib-gateway` Docker container on the isolated
  gateway VM (`10.0.0.251`).
- GitHub Actions workflows inspected/dispatched: `vm-git-credential-bootstrap.yml`,
  `system-actions.yml` (`restart-bot-service`, `set-env`),
  `vm-ib-gateway-recover.yml`, `vm-diag-snapshot.yml`.

## Work Completed
- Merged PR #5654 (retry-tolerant `IBClient._probe_liveness`, 2 new regression
  tests, `ee522ca`).
- Root-caused a live deploy-pipeline outage: the repo flipped public→private
  mid-session, breaking `ict-git-sync.timer`'s anonymous `git fetch`
  (`BL-20260706-GITSYNC-AUTH-BROKEN`).
- Shipped and then reverted a per-invocation credential approach
  (`deploy_pull_restart.sh` reading `VM_GIT_DEPLOY_TOKEN`) after discovering
  experimentally that `http.extraheader` is multi-valued — combining it with
  a global credential caused GitHub to reject the request
  (`Duplicate header: Authorization`, 400).
- Landed the durable fix: a single global git credential set once by the new
  one-shot `vm-git-credential-bootstrap.yml` (PR #5675), with
  `deploy_pull_restart.sh` reverted to a plain `git fetch --prune origin`
  (PR #5678).
- Hit and fixed a second recurrence of the same deadlock shape: the on-disk
  deploy script was itself still the broken pre-fix copy, so it could never
  pull its own fix. Extended the bootstrap workflow to `git reset --hard
  origin/main` + invoke `scripts/deploy_pull_restart.sh` directly when the
  worktree reads behind `origin/main` (PR #5681).
- Live-verified the full chain: dispatched the bootstrap (issue #5682, exit 0,
  correctly detected + repaired the stale worktree), caught that the deploy
  script itself skipped the service restart (zero diff after the outer
  reset), explicitly restarted `ict-trader-live.service` (issue #5683), and
  confirmed `/api/diag/version` reports `git_sha=d5276f74`.
- Enabled `IB_PROBE_TIMEOUT_S=5` on the live VM (issue #5685) and forced a
  real cold reconnect via `vm-ib-gateway-recover` (issue #5686).
- Confirmed via `journalctl` pulls (issues #5688, #5689) that the
  retry-tolerant probe absorbed four separate cold-TCP-flow misses (MES,
  MGC ×2, MHG) with zero false circuit-breaker trips, while a genuine trip
  still fired correctly the moment the gateway was actually down mid-restart.

## Validation Performed
- Tests run: `tests/test_ib_integration.py` (53 tests, incl. 2 new:
  cold-miss-then-recovers, still-hung-after-retry-trips-breaker) — all green
  in PR #5654 CI, and on every subsequent PR in this chain (#5665, #5675,
  #5678, #5681) alongside the full repo suite (`pytest-run`, ~6-7 min each).
- Dry-runs or staging checks: none — this is a live-VM-only fix with no
  local-repro path for the cross-host relay behavior; verification was done
  directly on the live gateway via a real forced restart.
- Manual code verification: read `deploy_pull_restart.sh` and
  `vm-git-credential-bootstrap.yml` in full before and after each edit;
  experimentally verified the `http.extraheader` multi-value behavior with
  `GIT_CURL_VERBOSE=1` against a real repo before committing to the
  single-global-credential architecture.
- Gaps not yet verified: the dedicated IB-Gateway VM's own `git clone`/fetch
  path (it has no `.env` by design) was flagged as a possible separate
  propagation gap in the original `BL-20260706-GITSYNC-AUTH-BROKEN` check_log
  entry — not confirmed affected or fixed this session; logged as a
  follow-up below.

## Documentation Updated
- Rules doc updates: none (`docs/CLAUDE-RULES-CANONICAL.md` unaffected).
- Architecture doc updates: none.
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): not applicable —
  this session touched deploy/ops infra and the IB liveness probe, not a
  pipeline stage.
- Roadmap updates: none — this work doesn't map to an existing M0–M19
  milestone row and doesn't change any milestone's status; tracked instead
  via the health-review backlog + this sprint log.
- GitHub Actions doc updates: `docs/github-actions-workflows.md` —
  `vm-git-credential-bootstrap.yml` row updated to describe the recovery-step
  extension.
- Subsystem doc updates: `CLAUDE.md` — `IB_PROBE_TIMEOUT_S` env-var row
  updated from "disabled, re-enabling is a deliberate follow-up, not yet
  done" to the resolved + live-verified state; "Live-VM git-fetch credential"
  bullet extended with the second-recurrence history and resolution.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1: `CLAUDE.md`'s `IB_PROBE_TIMEOUT_S` row still said
  re-enabling was "a deliberate follow-up step, not yet done" — stale as of
  this session's live flip. Fixed in place.
- Contradiction 2: none found elsewhere in the canonical set (`CLAUDE.md`,
  `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`,
  `ROADMAP.md`) touching this session's changes.
- Code/doc mismatch: none outstanding after the fixes above.

## Risks and Follow-Ups
- Remaining technical risks: the IB-Gateway VM's own git fetch path (separate
  from the live trader VM fixed here) may still be unauthenticated against
  the now-private repo — unconfirmed, logged to the health-review backlog
  (see below) rather than assumed fine.
- Remaining product decisions (Tier 3): none new — the eventual `ib_live`
  promotion decision this whole IB-stability thread feeds into remains
  future work, unaffected by this session.
- Blockers: none outstanding.

## Deferred Items
- Deferred item 1: confirm whether the dedicated IB-Gateway VM's `git
  clone`/fetch (it has no `.env`, uses a plain clone per
  `deploy/ib-gateway-cloud-init.yaml`) is affected by the repo going private,
  and fix if so — logged as a new health-review backlog item
  (`BL-20260706-GATEWAY-VM-GIT-AUTH-UNVERIFIED`).

## Next Recommended Sprint
- Suggested next sprint: verify/fix the IB-Gateway VM git-auth gap above, or
  continue toward the `ib_live` promotion decision now that IB observability
  is at parity with Bybit and the liveness probe is live-verified.
- Why next: closes the one known-unverified gap from this session before it
  can bite the same way `BL-20260706-GITSYNC-AUTH-BROKEN` did.
- Required verification before starting: confirm via the gateway VM's
  deploy/provisioning path whether it authenticates to GitHub at all today.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [ ] N/A — this sprint did not touch any trade pipeline stage.
- [x] Roadmap status was checked (no milestone row applies; confirmed via
      `ROADMAP.md` search).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
