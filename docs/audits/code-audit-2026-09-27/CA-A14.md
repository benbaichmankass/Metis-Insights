# CA-A14 — Code audit 2026-09-27: GitHub workflows, part 2

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> lane CA-A14 of the 2026-09-27 code audit · audited HEAD `f5353c3` (origin/main at 2026-09-27T13:00Z)

**Scope:** `.github/workflows/` files 75–149 in `ls .github/workflows` order
(`provision-live-vm.yml` through `yfinance-lane-proof.yml`; 149 files total in
the directory, so this lane covers the second (upper) half). Full list saved
during the run; the 75 files span provisioning/reset/terminate, research/backtest
dispatch, VM ops (Bybit/IB-gateway/Caddy/net diagnostics and fixes), trainer
offload, `system-actions.yml`, `sync-vm-secrets.yml`, `vm-diag-snapshot.yml`, and
the digest/decision-commit/session-reaper/sunset-pass housekeeping jobs.

Per docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md §3/§5: findings are only
findings when they carry evidence, a population, and a detector (or a stated
reason none exists); everything below tries to hold to that. Two HIGH findings
are also filed to `docs/claude/work/pipeline/` (ids noted per-finding).

## Findings by severity

- **Critical:** 0
- **High:** 2
  - `session-reaper.yml` has fired **zero times in 131.7+ hours** (since the
    2026-09-21 operating reset commented out its cron in the same commit that
    archived the file its push-trigger watches) — confirmed via full run
    history (116 runs, none after #116) and a direct `git cat-file -e` check
    that the watched file is genuinely absent from `main`. **Filed:
    `PI-20260927-NQRDRZWZ-0001`.**
  - `vm-ib-gateway-live-login-test.yml`'s remote-log grep intentionally
    captures any line containing the word "password" (among other keywords)
    from a throwaway live-IBKR container's docker logs and posts matched
    content verbatim into a **public** issue comment / step summary. Not a
    confirmed leak (no live login was triggered to test it — doing so would
    itself be an inappropriate live-credential probe), but a real,
    code-inspection-verified risk surface given this repo is public. **Filed:
    `PI-20260927-NQRDRZWZ-0002`.**
- **Medium:** 3 — `sunset-pass.yml` / `work-decision-commit.yml` share the
  same dead-schedule pattern as `session-reaper.yml` (not filed to the
  pipeline individually; recommend one follow-up decision covering all
  three); `research-queue-dispatch.yml`'s daily land-step routinely times out
  and reports as `cancelled` rather than `failure` (3 of last 5 scheduled
  runs); `reset-daily-risk-state.yml` combines three defense-in-depth gaps
  (no `permissions:` block, the only in-scope file to interpolate a secret
  directly in `run:` rather than via `env:`, and no actor check beyond the
  issue label) on a workflow that mutates live real-money risk state.
- **Low / verified-safe:** 2 — `vm-diag-snapshot.yml`'s allowlist cannot be
  walked to a write path (confirmed by full read); `system-actions.yml`'s
  `get-env` action and `sync-vm-secrets.yml`'s credential propagation both
  hold their documented "never print a raw secret value" contract on the
  traced code paths.

Full detail, evidence commands/output, and proposed fixes for each: see
`CA-A14.findings.jsonl` (one JSON object per line, schema per the audit
proposal doc §3).

## What was read fully / partly / not at all

**Read in full (file content, line-by-line):**
`vm-ib-gateway-live-login-test.yml`, `vm-diag-snapshot.yml` (852 lines),
`reset-daily-risk-state.yml`, `test-alpaca-creds.yml`, `sync-vm-secrets.yml`
(the secret-sync step and its header), `session-reaper.yml`, `sunset-pass.yml`,
`work-decision-commit.yml`, `work-digest.yml` (`on:` blocks + surrounding
comments for all four), `research-queue-dispatch.yml` (concurrency +
job-timeout section), `scripts/ops/get_env_action.sh`, `scripts/ops/get_env.py`
(in full), `scripts/ops/sync_vm_secrets.sh` (in full).

**Read partly (targeted sections, not the whole file):** `system-actions.yml`
(2018 lines) — only the `get-env` branch (workflow trigger/permissions,
`get_env` dispatch, and the shared secret-validation blocks) was traced
end-to-end. The other ~85 action branches on its allowlist (`rotate-account-keys`,
every `flatten-*`/`backfill-*`/`reconcile-*`/`repair-*` action, `set-account-mode`,
etc.) were **not** individually verified for the same "never prints a secret"
property in this pass — each is a plausible place for the same class of gap to
recur, and none is claimed clean here.

**Read via bulk extraction only (trigger + permissions + secrets-reference
lines, not full file bodies):** the remaining ~55 files in scope — all
`vm-*-deploy`/`recover`/`selftest`/`stop`/`watchdog-enable` one-shot VM-ops
files (these share one template closely enough that the extracted
trigger/permissions/secrets view was treated as representative, but none was
read end-to-end), all `research-*` backtest/harness-dispatch files, the OCI
`provision-*`/`reserve-live-ip`/`reset-instance`/`terminate-instance`/
`vm-resize-live` group (permissions and secrets extracted; the actual OCI CLI
invocations and confirmation gates were not individually traced), `rotate-account-keys.yml`,
`r4-demotion-gate.yml` (its own auto-land mechanics were already covered by a
sibling lane's CA-A09 findings, cross-checked via the live PR #13164, so not
re-derived here), `pytest-collect.yml`/`pytest-run.yml`/`repo-inventory.yml`
(CI plumbing, low blast radius, documented at length in their own comments),
`schedule-keeper.yml`, `stale-automation-sweep.yml`, `strategy-review-packets.yml`,
`trainer-*` files, `training-run.yml`, `vwap-backtest.yml`, `vix-term-backtest.yml`,
`yfinance-lane-proof.yml`, `set-diag-token.yml`, `purge-artifacts.yml`,
`prune-*` files, `scope-overlap-audit.yml`.

## What could not be settled

- **Whether the 4 files lacking a declared `permissions:` block
  (`reset-daily-risk-state.yml`, `test-alpaca-creds.yml`,
  `test-alpaca-from-vm.yml`, `training-rerun-5m.yml`) actually run with
  broader-than-needed permissions** — this depends on the repository's
  default Actions workflow-permissions setting (Settings → Actions →
  General), which no tool available in this session could read. Stated as
  unresolved rather than assumed either way.
- **Whether the `vm-ib-gateway-live-login-test.yml` password-grep risk has
  ever actually fired** — no run history exists to inspect (it is a rare,
  manually-dispatched one-shot workflow), and deliberately triggering a live
  IBKR login to test it would itself be an inappropriate use of live
  credentials. Left as a plausible, unproven risk with a proposed mitigation.
- **Whether `work-decision-commit.yml` has actually gone unused since its
  cron was disabled**, vs. `sunset-pass.yml` and `session-reaper.yml` where
  full run history was pulled — budget ran out before pulling its run
  history; the file-content evidence (commented-out cron, no other trigger)
  stands on its own regardless.
- **Whether the two allowlists in `vm-diag-snapshot.yml` (shell `case` vs.
  Python `BOT_ALLOWLIST_EXACT`/`_GLOB`) have ever actually drifted** — no
  discrepancy was found on a manual entry-by-entry comparison in this pass,
  but no automated test enforcing their equality was found either (not
  confirmed absent — just not found in the time available).
- **The other ~85 `system-actions.yml` action branches beyond `get-env`** —
  not traced for the same "never logs a secret" property; each is a
  plausible place for a recurrence of the same class of issue and none is
  asserted clean.

## Notable non-finding worth stating

Two of this scope's scheduled workflows (`r4-demotion-gate.yml`,
`research-queue-dispatch.yml`) **do** fire reliably on cron — this repo's
well-documented cron-unreliability history (cited in `sunset-pass.yml`'s own
header, re: `probes.yml`/`due-list.yml` never having fired on schedule) is
real but **not universal**; the dead-schedule findings above have a distinct,
verified cause (the trigger was deliberately commented out in a specific
commit, not a platform delivery failure) and should not be filed under the
same known issue without checking each file's actual trigger content, which
this pass did.
