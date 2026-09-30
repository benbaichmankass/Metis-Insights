# AUD-7 — Security: credential privilege and secret exposure (E75, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

## 1. Question and answer

Does every credential-bearing path have minimum privilege, and is secret-shaped material reachable from a surface that should not have it?

Mostly yes on exposure, no on privilege granularity. Nothing in 10,341 commits looks like a live GitHub, AWS, Alpaca or PEM credential. Two old credential-shaped strings remain in public history (a Telegram bot token, a Bybit testnet key); their revocation cannot be verified from the repo. Every issue-triggered job is owner-gated, so outsiders cannot reach the relays. The weaknesses are: one PAT (`BRANCH_PROTECTION_TOKEN`) used by 43 workflows with contradictory scope docs; a `..` path bypass of the diag relay's read-only allowlist (static and local-curl evidence; the live run was still queued); no redaction between raw journal output and public issue comments; and about 11 redundant `contents: write` grants.

## 2. Findings

| id | sev | claim |
|---|---|---|
| SA-AUD-7-diag-relay-dotdot-allowlist-bypass | medium | `..` segments escape the /api/bot allowlist |
| SA-AUD-7-branch-protection-token-single-pat | medium | one PAT, 43 workflows, scope unknown |
| SA-AUD-7-history-telegram-bot-token | medium | token-shaped string in history, rotation unverified |
| SA-AUD-7-diag-output-unredacted-public-comments | medium | no redaction before public comments |
| SA-AUD-7-history-bybit-testnet-key | low | testnet key in initial commit |
| SA-AUD-7-secret-scan-head-only | low | guard cannot see history |
| SA-AUD-7-commit-to-main-redundant-contents-write | low | ~11 unused write grants |
| SA-AUD-7-workflows-without-permissions-block | low | 4 workflows, default token scope |
| 3 × NON-ISSUE (info) | info | owner gating; no event text in run blocks; vendor-shape history sweep clean |

Full schema: `AUD-7.findings.jsonl` (11 rows). Counts: critical 0, high 0, medium 4, low 4, info 3.

## 3. Coverage

Behavioural: asserted 4 capabilities, exercised 3 end to end against real data (all-history gitleaks run; 12-pattern vendor sweep; scripted parse of all 154 workflows; local curl dot-segment probe). The live relay probe (#14080 traversal, #14081 control) was queued behind runner backlog at report time: 0 of 1 completed, so the bypass is NOT observed on the fleet.
Reading: read in full or in part: trainer-vm-diag.yml, vm-diag-snapshot.yml (both validators), external-comment-alert.yml, commit-to-main action, scripts/secret_scan.py, src/utils/log_redact.py, diag.py (journalctl, log_file, auth), 4 committer workflows. Scripted only (not read line by line): the other ~145 workflows. Not read: system-actions.yml allowlist, sync-vm-secrets.yml, set-diag-token.yml, get-diag-token.yml (secret-handling workflows), src/web routes outside diag.py, Actions repo settings (default token scope, secret list). CA findings not re-derived; CA-A08 and JC-CA-03 cited.

## 4. Could not look

- BRANCH_PROTECTION_TOKEN's real scopes (needs GitHub settings).
- Whether the history Telegram and Bybit-testnet strings were revoked (needs BotFather / Bybit; I did not call either API with them).
- Live relay output for secret shapes: pulling journal/log would publish it on a public issue.
- Live traversal test #14080/#14081: queued. Close both once read.
- Repo default GITHUB_TOKEN permission setting.
- ~295 SESSIONS.json 24-26 char hits: shape-only, assumed session ids.

## 5. Spend

About one session of shell and read work; 2 relay issues; no VM or order-path action. Well under the $20 ceiling.
