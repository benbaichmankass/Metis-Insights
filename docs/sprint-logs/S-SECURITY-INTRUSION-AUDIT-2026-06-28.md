# Sprint Log: S-SECURITY-INTRUSION-AUDIT-2026-06-28

## Date Range

2026-06-28 (single session, `session_019D3m44FeSEj95rxaTnWHy3`).

## Objective

Investigate a confirmed external-intrusion attempt against the repo's
issue-driven GitHub Actions automation and harden the surface. Trigger: an
external non-collaborator (`danleejames23`, `author_association: NONE`) opened
issues #2680/#2681/#2688 on the **public** `ict-trading-bot` repo trying to get
a privileged workflow to fire (#2688: "Apply `system-action` label to deploy").
Scope: read-only investigation across all three repos + written hardening plan +
ship the safe Tier-1 hardening.

## Tier

Mixed. Tier-1 (CI/`.github/workflows`, docs, the read-only `list-listening-ports`
action) shipped autonomously. One Tier-2 item (`src/bot/comms_handler.py` chat
auth) shipped **only after explicit operator approval**. Tier-2/3 infra/API
items (firewall, fail-closed API auth, branch protection, key rotation, owner
2FA) were **written up for operator approval, not enacted**.

## Starting Context

The two probe issues were already closed `not_planned`, un-acted-on. The
question was whether the containment was defense-in-depth or luck of the GitHub
permission model, and what else could break the system.

## Repo State Checked

- `docs/CLAUDE-RULES-CANONICAL.md` (tiers, autonomy, merge protocol),
  `docs/claude/session-board.json` (registered to avoid colliding with the M17
  audit lead session).
- Live GitHub state via the GitHub MCP (repo visibility, collaborators, the 3
  probe issues + their events, the 57 probe workflow runs, branch-protection
  flags, secret-scanning availability) — read-only, authenticated as the owner.

## Files and Systems Inspected

- All 70 workflow files across the 3 repos (`.github/workflows/`): triggers,
  job `if:` guards, secret usage, SSH usage, untrusted-body handling.
- `src/web/api/main.py`, `auth.py`, every `routers/*.py` write/read path (API
  auth posture) — via a subagent.
- `src/bot/comms_handler.py` + `claude_bridge.py` (Telegram auth).
- `scripts/install_ib_gateway_docker.sh` (IB gateway bind), `scripts/ops/`
  wrappers + `system-actions.yml` wiring + `tests/ops/test_system_actions_workflow.py`.

## Work Completed

**Investigation + docs (Tier-1):**
- `docs/security/intrusion-surface-audit-2026-06-28.md` — findings + risk-ranked
  threat model + prioritized plan. Key finding: **no** issue-triggered workflow
  guarded on actor identity; **six gated only on issue TITLE** (author-controlled
  → any public user could trigger to full effect, incl. two using live Alpaca
  secrets, one deleting git refs, one exercising a PAT). Verified negatives: no
  `pull_request_target` anywhere; untrusted bodies routed via `env:` (no shell
  injection); IB gateway socket defaults to loopback.
- `docs/security/api-network-hardening-PLAN-2026-06-28.md` — Tier-2 API-auth +
  network plan (incl. the blocker: `DASHBOARD_API_TOKEN` VM state could not be
  confirmed from the firewalled sandbox; Streamlit Cloud has no stable egress IPs
  so IP-allowlisting is not viable → loopback + TLS + bearer).

**Hardening shipped (all merged to `main`):**
- Author-identity guard (`issue.user.login == github.repository_owner ||
  'github-actions[bot]'`, AND-ed onto each existing gate → monotonic) on **all
  44 issue-triggered workflows** in the bot repo (#4965 P0: 6 title-gated +
  `system-actions`; #4967 P1: the remaining 38) + the dashboard's
  `delete-merged-branches` (dashboard #129).
- `external-issue-alert.yml` detection workflow (#4970) — flags/labels/Telegram-
  pings non-owner issues, escalating on dispatch-pattern titles.
- Read-only `list-listening-ports` `system-action` (#4972) for live port-exposure
  inventory.
- `src/bot/comms_handler.py` chat-auth (#4971, Tier-2, operator-approved) —
  rejects answers to operator decision prompts from any non-operator chat
  (fail-open only when `TELEGRAM_CHAT_ID` unset).
- Logged the remaining operator-only + Tier-2 follow-ups to the health-review
  backlog (#4975, `BL-20260628-SEC-HARDENING-FOLLOWUPS`).

## Validation Performed

- Every edited workflow validated as parseable YAML; guard present on each gated
  job (scripted + spot-checked diffs). 35 of the P1 sweep done via a single
  enforced-one-match literal replace; 3 special shapes by hand.
- The `list-listening-ports` wrapper: `bash -n` clean, runs read-only and exits
  0 under `set -euo pipefail`; the `system-actions` allowlist/doc/test triplet
  consistency replicated locally before push. CI later caught two gaps
  (strict-mode + `notify_run.sh` priority map) — both fixed (#4972 follow-up
  commit), CI green.
- `comms_handler` chat-auth: `py_compile` + auth-logic assertions locally; full
  suite in CI green.
- All 8 PRs went green in CI and merged (squash) under branch-protection
  require-up-to-date (serial update-branch + auto-merge).

## Documentation Updated

- New: `docs/security/intrusion-surface-audit-2026-06-28.md`,
  `docs/security/api-network-hardening-PLAN-2026-06-28.md`.
- `docs/claude/system-actions.md` (+ the Tier-1 list) for the new action.
- `docs/claude/health-review-backlog.json` — follow-up item.
- This sprint log; session-board entry pruned on exit.

## Contradictions or Drift Found

None in the canonical docs. No canonical doc (`CLAUDE-RULES-CANONICAL`,
`ARCHITECTURE-CANONICAL`, `ROADMAP`) was touched or contradicted; the
`system-actions.yml` ↔ `system-actions.md` ↔ test triplet was kept in lockstep
(CI-enforced).

## Risks and Follow-Ups

The actor-guard model now keys the entire issue-driven trust on
`repository_owner` — so the **owner's GitHub account is the new single point of
failure**; hardware-2FA/passkey is the highest-leverage remaining control. Full
checklist (operator-only + Tier-2) is logged at
`BL-20260628-SEC-HARDENING-FOLLOWUPS` for the next `/system-review`.

## Deferred Items

Operator-only / Tier-2 (NOT done this session): owner 2FA/passkey; fork-PR
approval + read-only default `GITHUB_TOKEN`; branch-protect android/dashboard
`main` + protect `.github/`; secret-scanning push protection; confirm
`DASHBOARD_API_TOKEN` on the VM then the Tier-2 fail-closed API auth +
auth-on-`devices/register` + TLS reverse proxy (#4968 plan); pin
`appleboy/ssh-action` to a SHA (SHA unverifiable from the sandbox);
precautionary `BRANCH_PROTECTION_TOKEN`/Alpaca-key review.

## Next Recommended Sprint

Operator actions the `BL-20260628-SEC-HARDENING-FOLLOWUPS` checklist; then a
Tier-2 sprint to implement the #4968 API/network hardening once
`DASHBOARD_API_TOKEN` is confirmed set on the VM.
