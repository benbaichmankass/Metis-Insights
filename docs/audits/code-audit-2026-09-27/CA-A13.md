# Code audit CA-A13 — GitHub workflows part 1

> Lane of the 2026-09-27 code audit (row CA-A13, `docs/claude/work/MANAGER-CHECKLIST.json`,
> added by PR #13156). Read-only analysis + this docs-only PR; no code, config or live
> state changed. Budget: ~$35, Sonnet.

**Scope:** `.github/workflows/` files 1–74 in `ls .github/workflows` order (through
`provision-ib-gateway.yml`), excluding `claude-pr-automerge.yml` and `pr-opener.yml`
(another lane's scope) — 72 files audited. For each: trigger (and whether recent runs
show it firing), permissions vs. what its steps use, secrets it reads, whether it can
mutate a VM or `main` and behind what gate, and scheduled-job run health.

## Findings by severity

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 1 |
| Medium | 0 |
| Low | 0 |

Full record: [`CA-A13.findings.jsonl`](CA-A13.findings.jsonl). Pipeline follow-up filed:
`PI-20260927-CAA13-0001`.

### High

- **`gpu-burst-train.yml` is missing the repo's own established actor-guard.** It
  triggers on `issues: opened` (public repo — anyone who can open an issue starts the
  run) and its job gates only on the `gpu-burst-train` label, with no
  `github.event.issue.user.login == github.repository_owner` check. Every other
  issues-triggered workflow in scope that reads a sensitive secret (27 of 28) carries
  that guard, per `docs/security/intrusion-surface-audit-2026-06-28.md`'s own P1
  fast-follow plan and its 2026-07-07 addendum's claim that "every issue-triggered
  workflow (~30)" has it. `gpu-burst-train.yml` was added after that addendum
  (references M19 / `PI-20260924-...`) and never got the pattern — the claim is stale
  for this one file, and nothing mechanically enforces the convention on new
  workflows. The job's env holds `RUNPOD_API_KEY`, `VAST_API_KEY`, `RUNPOD_SSH_KEY`,
  `VM_SSH_KEY` (SSH to the trainer VM) and `BRANCH_PROTECTION_TOKEN` (a PAT). **Not
  currently exploited** — confirmed via recent run history (all `triggering_actor` =
  repo owner, all `conclusion: skipped`) and via issue history (no non-owner-opened
  issue since 2026-07-07, consistent with the repo's "limit interactions to
  collaborators" setting still being active) — but I could not read that repository
  setting directly with any tool available to this session, so the current exposure
  rests on an unverified control with zero defense-in-depth behind it, same as every
  other workflow before it got the guard. See the finding record for the exact fix
  (one AND-ed condition, matching the established pattern) and a proposed CI guard
  (`check_workflow_actor_guard.py`) so this class doesn't recur silently on the next
  new privileged workflow.

## What I read fully / partly / not at all

- **Fully read:** all 72 in-scope workflow files (concatenated and read end-to-end),
  `docs/security/intrusion-surface-audit-2026-06-28.md` (the repo's own prior security
  audit — directly load-bearing for this lane's highest finding),
  `docs/reference/diag-access.md` (DIAG_READ_TOKEN section), `scripts/ci/` guard list
  (`check_workflow_catalog.py`, `check_workflow_trigger_reachability.py`,
  `check_workflow_push_target.py`, `check_workflow_failure_swallow.py`,
  `check_guard_liveness.py`), `scripts/ops/pipeline.py` (to file the follow-up
  correctly against its actual directory-store schema, not the flat-file shape
  `CLAUDE.md`'s prose still describes).
- **Partly read:** run history for ~12 actively-scheduled workflows in scope
  (`alpaca-settlement-soak-watch`, `broker-bracket-reconcile`, `dashboard-edge-watch`,
  `diag-relay-sweep`, `doc-audit-weekly`, `econ-calendar-produce`, `econ-event-study`,
  `health-snapshot`, `macro-producer-liveness`, `macro-valuation-snapshot`,
  `main-tree-watch`, `oci-inventory`) — last 5 `event:schedule` runs per workflow via
  the GitHub Actions API, not full history. `claude-run-failure-alert.yml`'s
  workflow-name list, checked only for whether it covers the ones I sampled (it does).
- **Not read at all:** the trainer-VM / live-VM side of what these workflows SSH into
  (`scripts/ops/*.sh` invoked by them), the `.github/actions/*` composite actions they
  call, and the repository's own Settings UI (interaction limits, default
  `GITHUB_TOKEN` permissions, fork-PR approval, branch protection rule contents,
  secret-scanning status) — none of these are readable through any tool available to
  this session; the 2026-06-28 audit hit the identical wall and flagged it the same
  way.

## What I checked and did NOT re-file (respecting existing operator decisions)

- **`BL-20260818-DIAG-READ-TOKEN-PUBLIC-EXPOSURE-UNREMEDIATED`** —
  `get-diag-token.yml` carries the refusal logic this closed decision describes
  (line ~140: refuses to deliver `DIAG_READ_TOKEN` on a public repo). Re-read
  `docs/reference/diag-access.md`'s current text: operator decision 2026-08-30,
  explicitly `wont_fix`, with a standing instruction *"do not raise it again"* /
  *"do not put it in a review's `flags_raised[]`"*. I confirmed the code matches the
  doc's description and did not re-file it, per that instruction.
- **`macro-valuation-snapshot.yml` run #2234 (2026-09-26) failed** (the "Land the
  appended log on main" step timed out after ~40 min; the data-producing step itself
  succeeded). This is exactly the class of failure `claude-run-failure-alert.yml` is
  built to catch — confirmed `macro-valuation-snapshot` is in that listener's watched
  list (`.github/workflows/claude-run-failure-alert.yml:210`), so the operator was
  Telegram-alerted rather than this being a silent scheduled-job failure. Not filed.

## What I could not settle

- **The repository's live "Interaction limits" setting** (owner/collaborators-only
  commenting/issue-opening), which is the actual control standing behind the High
  finding above. No tool in this session's toolset reads GitHub repo Settings pages —
  the 2026-06-28 audit noted the identical gap. Population evidence (no non-owner issue
  opened since 2026-07-07) is consistent with it still being on, but is not direct
  confirmation.
- **Whether `gpu-burst-train.yml`'s creation date is exactly 2026-09-24 or later** —
  the working tree is a shallow clone (50 commits); I corroborated the date from the
  file's own header comments (`M19`, `PI-20260924-5V6UVR9J-0002`) rather than
  `git log`, which is unreliable here per the session's own shallow-clone warning.
- **Files 75–149** and the excluded `claude-pr-automerge.yml` / `pr-opener.yml` are out
  of this lane's scope (covered by CA-A14 and another lane respectively).
