# Repo public → private: feasibility and transition plan — 2026-10-08

> **Doc status:** `live` · category `evidence` · last verified `2026-10-08` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> Lane `REPO-PRIVATE-SCOPE` (Sonnet, ceiling $15), dispatched by the manager. **Memo only: no workflow, secret, VM or setting was changed.** No IPs, account ids or secret values appear here (public repo).

## 0. Bottom line

1. **Going private with GitHub-hosted runners is not affordable.** Sampled load is ~**520k wall-clock Actions minutes/month** against **2,000 included** on Free (3,000 on Pro). Overage at $0.006/min would be ~$3,000/month. Actions are free here only because the repo is public.
2. **Self-hosted runners are the free path** and are the answer to research compute and to the manager's VM access. They are free for private repos *today*. GitHub announced a $0.002/min self-hosted fee for 2026-03-01, then **postponed** it (not cancelled), so a re-introduction risk remains. At the sampled load that would be ~$1,000/month, which is the reason for the exit ramp in phase 3.
3. **A personal Free plan loses branch protection, code owners and (probably) merge queue on private repos.** This repo relies on them: `branch-protection-sync`, 4 `merge_group` workflows, the per-branch merge-slot model. Restoring them is GitHub Pro (published price not re-verified this session, believed ~$4/month). That is the one likely unavoidable paid item. Plan not determinable from the API with this token (`plan: null`); treated as Free.
4. **We have been here before.** The repo went private 2026-07-06 and was flipped back public 2026-07-07 "to keep the free unlimited Actions budget" (`docs/reference/session-capabilities.md`). Several private-safe fixes from that week are still in the tree (VM git credential, RunPod tarball upload). Not everything was undone, which lowers the work.
5. **Secrets:** GitHub secrets remain available on private. The recommended direction is to shrink what lives there, not to bolt on a Colab flow (§3c).

## 1. Inventory (measured)

### 1a. Workflows (`.github/workflows`, 167 files)

By trigger (a workflow can have several): `workflow_dispatch` 146 · `issues` 93 (the issue-label relay) · `schedule` 32 · `push` 29 · `pull_request` 5 · `merge_group` 4 · `workflow_run` 2 · `issue_comment` 1 · `repository_dispatch` 1. **All 167 use `runs-on: ubuntu-latest`; none uses a self-hosted runner today** (grep for `self-hosted` returns nothing).

Purpose clusters: CI/guards (`pytest-run`, `pytest-collect`, `guards`, `repo-inventory`); PR/merge machinery (`claude-pr-automerge`, `pr-*`, `branch-protection-sync`, `armed-branch-push-watch`, `work-digest`, `schedule-keeper`, `session-reaper`); issue-driven relays (`system-actions`, `vm-*`, `trainer-vm-diag`, `vm-diag-snapshot`, `oci-*`, `provision-*`); research compute (`research-queue-dispatch`, `research-script-run`, `research-harness-dispatch`, `m20-exit-lever-sweep`, `trainer-offload-train`, backtests/sweeps); scheduled monitors (health snapshot, grading, reconcile, macro producers); `gpu-burst-train` (RunPod spot GPU, ledger-capped at $10/month, gated by `GPU_BURST_ARMED`).

### 1b. Minutes per month — method and caveats

`gh api repos/<repo>/actions/runs` returns at most 1,000 runs per query, so I sampled **4 whole days** (2026-09-15, 09-24, 10-01, 10-07) in 3-hour windows: **15,837 runs, 69,956 minutes**, i.e. ~3,960 runs/day and ~17.5k min/day, ×30 ≈ **525k min/month**. Minutes are `updated_at − run_started_at` per run (wall-clock, ceil to 1 min). Biases: wall-clock includes queueing and parallel jobs are not summed (undercounts job-minutes); per-job round-up is not modelled; 11 of 32 windows hit the 1,000-run cap (undercount); the four days are not a random sample (09-15 was a light day at 533 runs). Treat as order of magnitude: **hundreds of thousands of minutes, ~250× the Free allowance.**

| Workflow | runs (4d) | min (4d) | ≈ min/30d |
|---|---|---|---|
| pytest-run | 1,042 | 14,273 | 107k |
| Guards | 1,041 | 9,062 | 68k |
| armed-branch-push-watch | 275 | 8,743 | 66k |
| pytest-collect | 1,041 | 4,684 | 35k |
| claude-run-failure-alert | 1,500 | 3,992 | 30k |
| repo-inventory | 1,038 | 3,715 | 28k |
| work-digest | 355 | 3,434 | 26k |
| branch-protection-sync | 351 | 1,948 | 15k |
| pr-landing hold-merge alarm | 284 | 1,876 | 14k |
| claude-pr-automerge | 101 | 1,834 | 14k |
| research-queue-dispatch | 23 | 1,456 | 11k |
| session-reaper | 168 | 1,185 | 9k |
| research-harness-dispatch | 24 | 1,100 | 8k |

Reading: **~75% of minutes are push/PR machinery** (CI, guards, PR bookkeeping), not research. Research compute is a minority (~8% by the sample) but has the long jobs (`timeout-minutes` up to 360 in `research-script-run`, `m20-exit-lever-sweep`, `trainer-offload-train`). `trainer-offload-train` exists specifically because the trainer VM (1 OCPU / 6 GB) OOMs on heavy manifests, so it borrows the free hosted runner. That is the dependency to replace.

### 1c. Relays, Pages, secrets, public-dependent items

- **Issue-driven relays:** 93 workflows fire on `issues` labels. On a public repo the guard against outsiders is the repo interaction limit plus `external-issue-alert` / `external-comment-alert`. On private the attack surface is collaborators only. `get-diag-token` currently *refuses* on public; on private it works again (see §2).
- **Pages:** this repo has no Pages workflow (the `pages` grep hits are prose). The SPA lives in the separate `ict-trader-dashboard` repo on Pages, called browser-direct to the API via Caddy. **Out of scope unless that repo also goes private**, in which case Pages is unavailable on a personal account (§2), so *keep the dashboard repo public* (it holds no secrets; verify before leaving it that way).
- **Actions secret names in use** (names only, from `secrets.*` in workflows): `BRANCH_PROTECTION_TOKEN`, `VM_SSH_KEY`, `VM_SSH_PRIVATE_KEY`, `VM_GIT_DEPLOY_TOKEN`, `DIAG_READ_TOKEN`, `DIAG_BASE_URL`, `OCI_CLI_{TENANCY,USER,REGION,KEY_CONTENT,FINGERPRINT}`, `OCI_COMPARTMENT_OCID`, `TELEGRAM_{BOT_TOKEN,CHAT_ID}`, `CLAUDE_TELEGRAM_BOT_TOKEN`, `TELEGRAM_CLAUDE_{BOT_TOKEN,CHAT_ID,BOT_SECRET}`, `TELEGRAM_PROP_TRADE_BOT`, `BYBIT_API_{KEY,SECRET}_{1,2,3}`, `ALPACA_API_{KEY_ID,SECRET_KEY}` plus `_LIVE`, `_PAPER_PORTFOLIO`, `_OPTIONS` variants, `OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID`, `IB_USERNAME`, `IB_PASSWORD`, `{VELOTRADE,TRADEIFY,BREAKOUT}_DX_{USERNAME,PASSWORD}`, `GEMINI_API_KEY`, `CEREBRAS_API_KEY`, `NEWS_API_KEY`, `EIA_API_KEY`, `HF_TOKEN`, `RUNPOD_API_KEY`, `RUNPOD_SSH_KEY`, `VAST_API_KEY`, `JWT_SIGNING_KEY`, `DASHBOARD_API_TOKEN`, `WEBAPP_PASSWORD_SHA256`, `FCM_SERVICE_ACCOUNT_JSON`, `PHONE_EXEC_KEYSTORE_{B64,PASSWORD}`, `EGRESS_PROBE_PROXY`. (Matches also include `NAME`, `secrets.yml`, `secrets.sh`, which are not secrets.) Heaviest consumers: `BRANCH_PROTECTION_TOKEN` (99 refs) and `VM_SSH_KEY` (61 refs).
- **Things that rely on the repo being public:**
  1. `deploy/{live-arm,ib-gateway,training-vm}-cloud-init.yaml` do an **anonymous `git clone`** of the repo. Fresh-VM provisioning breaks on private.
  2. The live VM's `git fetch` is already authenticated by the global `extraheader` set by `vm-git-credential-bootstrap` with `VM_GIT_DEPLOY_TOKEN` (fine-grained, Contents:read). Documented as "harmless on a public repo", i.e. **already private-ready** for the running VMs.
  3. `scripts/ml/gpu_burst/_remote.py` already ships a `git archive` tarball instead of cloning (comment: "repo is private (2026-07-06)"): **private-ready**.
  4. The SPA/API CORS path and Caddy do not depend on repo visibility.
  5. `raw.githubusercontent` / `api.github.com` use in ~10 `scripts/ops/*.py` helpers (PR/CI tooling) goes through the API with the Actions or MCP token; verify unauthenticated calls case by case in phase 1 (not exhaustively checked here).
  6. **Claude web sessions** reach the repo via the GitHub MCP/App, which is scoped to it. This worked while private in July. Also `get-diag-token` and the secret-delivery paths that were disabled for public can be re-enabled.

## 2. What changes on private (GitHub docs)

Sources: [Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions), [plans](https://docs.github.com/en/get-started/learning-about-github/githubs-plans), [self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners), [self-hosted fee postponement coverage](https://www.techzine.eu/news/devops/137396/github-bends-to-criticism-and-delays-paid-self-hosting-of-runners/) (no GitHub primary source retrieved; re-check the changelog before phase 3).

| Item | Public (now) | Private, Free | Private, Pro |
|---|---|---|---|
| Hosted-runner minutes | free | 2,000/mo | 3,000/mo |
| Artifact storage | free | 500 MB | 1 GB |
| Cache | 10 GB/repo | 10 GB/repo | 10 GB/repo |
| Overage | n/a | $0.006/min (Linux 2-core) | same |
| Self-hosted runners | free | free (fee postponed, see above) | free (same caveat) |
| Protected branches, code owners | yes | **no** | yes |
| Merge queue | yes | not listed in the plans page; **assume no and verify** | not listed |
| Pages | yes | **no** | personal accounts: no (private Pages needs an org on Enterprise Cloud) |
| Env. required reviewers | yes | no | not listed |

Security implications: **good** — code, `config/accounts.yaml`, research and every historic commit stop being world-readable; outsider issues/PRs/comments disappear as a vector; the interaction-limit workaround and `get-diag-token` refusal become unnecessary. **Not solved by going private** — anything ever committed stays in clones/forks/caches made while public (treat leaked values as burned; `gitleaks-history-weekly` exists, run it before and after). A private repo does not make a self-hosted runner safe from malicious *workflow code*, only from outsiders' PRs.

## 3. Options

### 3a. Research compute (ranked: cost, then risk)

1. **Self-hosted runner(s) on a new/spare Oracle Always Free Ampere VM, ephemeral, no secrets.** $0 if capacity remains inside the Always Free limits (published limit is 4 OCPU / 24 GB for A1 in total; confirm what the two existing VMs already use before assuming spare). Moves the 6-hour sweeps and `trainer-offload-train` off the 6 GB trainer. Risk: a persistent runner is a standing shell; use an unprivileged user, ephemeral/JIT registration, firewall egress to only what the jobs need.
2. **Self-hosted runner on the trainer VM.** $0, but 1 OCPU / 6 GB OOM-wedged it before (18.7h in D-state). Suitable only for light jobs. Not a fix for the thing that motivated offload.
3. **Trim the minute hogs on hosted runners** (path filters on the 78-run/day CI trio, drop `armed-branch-push-watch`, coalesce `claude-run-failure-alert`). Would cut maybe 60–70% of minutes but still lands at ~150k+/month, far over 2,000. **Not sufficient alone**, necessary alongside 1.
4. **GPU burst** (`gpu-burst-train`, RunPod). Already works privately (tarball upload), $0.2164 lifetime spend in the ledger as of July, capped $10/month. Keep for GPU-only work; no change.
5. **Other free burst sources** (Colab free tier, Kaggle kernels, HF Spaces ZeroGPU): free but interactive/quota-limited, no scheduling API fit for the queue dispatcher, and data/secret handling would need new plumbing. Backup for GPU only; I did not verify current quotas.
6. **Pay for hosted minutes / Pro**: rejected, ~$3k/month at current load.

### 3b. Manager's autonomous VM access

| | Cost | Operator work | Least privilege | Audit | Risk |
|---|---|---|---|---|---|
| **Today:** `VM_SSH_KEY` in Actions secrets, GitHub-hosted runner SSHes in (61 refs) | $0 public; needs minutes on private | none | key is broad (works on both VMs) | run log per action | key is exfiltratable by any workflow that runs on a runner; public-repo workflow edits were gated only by single-owner push access |
| **Runner on trader VM, label `ops`, no inbound SSH** | $0 | one-time install, done by us via OCI/SSH | can be a dedicated unprivileged user + sudoers for a short allowlist | run log + local journald | runs repo-controlled code on the live-trading box; mitigations below |
| **Separate CI runner + narrow SSH key per action** (hybrid) | $0 | none | best: split keys by target (trader/trainer), `command=` forced in `authorized_keys` | per-key | more moving parts |

Recommendation: **hybrid.** `ci` runners on a non-trading VM for everything heavy; a tightly scoped `ops` path to the trader VM that only the `system-actions` allowlist can reach (runner group restricted to `main`, labels, no PR-triggered jobs, forced-command SSH key or a minimal-privilege local user). Do **not** run pytest/sweeps on the box that trades.

### 3c. Secrets (judged: no technical steps for the operator, least privilege, rotation, audit)

| | Operator steps | Least privilege | Rotation | Audit |
|---|---|---|---|---|
| **GitHub secrets (private)** | paste value in GitHub's Settings UI, or via existing `init-actions-secrets` / `sync-vm-secrets` / `rotate-account-keys` workflows | repo-wide: any workflow on a runner can read any secret; no per-secret scoping short of environments (not on Free) | manual, workflows exist | GitHub audit log, workflow logs |
| **VM-side encrypted store (sops+age)** | needs a front end: a CLI is technical. Pair with a small authenticated "key list" page on the existing API (password already exists) that encrypts on receipt; list shows names and last-rotated, never values | best: secret only on the box that uses it; CI never sees trading keys | page action or scheduled reminder per key | append-only log of name/time/actor |
| **Colab one-click notebook** | open notebook, paste into fields, run one cell | needs a credential to reach the VM or repo from Google's environment (a long-lived token in Colab Secrets, or a one-time upload URL); values transit and sit in a third-party notebook session | manual | Colab has no audit trail we control |

Judgement: **the Colab flow meets the "no technical steps" bar but introduces a third-party hop and a standing credential; I would not choose it first.** The same operator experience ("I just manage the key list") is achievable with a page we host (VM-side) with a one-time-token or existing password and no third party. Build cost: Tier-2 feature, small. Interim: keep GitHub secrets, which already work on private, and move only the **runtime trading keys** (Bybit, Alpaca, IB, prop DX) so they exist only on the VM; CI keeps `VM_SSH_KEY`/OCI/diag/Telegram. That shrinks the blast radius of any runner compromise to the infra keys.

## 4. Phased plan (each step reversible, each with a go/no-go)

**Phase 0 — prepare, change nothing live (days).** Run `gitleaks-history-weekly` and a manual full-history scan; list which secrets ever appeared in a public commit and rotate those regardless. Decide the Pro question (§0.3). Confirm Oracle spare capacity and the current GitHub changelog on the self-hosted fee. *Go if:* no known live secret in history, spare A1 capacity ≥ 2 OCPU, fee still postponed.

**Phase 1 — reversible first step: stand up one ephemeral self-hosted `ci` runner on a non-trading VM and run one low-value workflow on it, repo still public.** Opt-in per workflow via `runs-on: [self-hosted, ci]`. Revert = change the label back. *Go if:* `pytest-run` passes on it with comparable wall time, no runner crash/OOM across 48 h, outbound allowlist works. *Public-repo caveat:* do **not** let fork PRs reach it while public (that is the documented self-hosted danger); restrict to push-to-`main`/branch workflows until phase 3.

**Phase 2 — move load, still public.** Shift CI trio, PR machinery and research sweeps to the runner(s); keep hosted as fallback. Add `ops` path for the `system-actions` allowlist per §3b. Fix cloud-init anonymous clone to use the deploy credential. *Go if:* two weeks with no merge-queue stall and measured hosted minutes < ~1,500/month (the number that makes the flip free).

**Phase 3 — flip to private (reversible: visibility can be flipped back, as in July).** Pre-checks: dashboard repo stays public (Pages); SPA/API unaffected; Claude sessions still reach the repo; branch protection decision applied (Pro or accept the loss); `get-diag-token`/secret-delivery re-enabled deliberately. *No-go if:* hosted minutes > 2,000/mo on the sample, or branch protection is required and Pro is refused. Exit ramp: if GitHub revives the $0.002/min fee (~$1k/month at current load), revert to public or reduce push machinery.

**Phase 4 — secrets redesign.** Runtime keys VM-only; key-list page (Tier-2) with audit log; GitHub secrets retained only for CI/infra.

**Security improvements to do regardless of visibility:**
- Replace the one broad `VM_SSH_KEY` with per-target keys and forced commands; rotate on a calendar.
- Trading-key values should not live in Actions secrets at all if the VM can hold them (4 workflows hold Bybit/Alpaca live keys).
- Pin third-party actions by SHA; set default `GITHUB_TOKEN` to read-only and grant per job.
- Treat anything exposed while public (e.g. 2026-05 issue comment with a bearer, noted in `diag-access.md`) as burned and confirm it was rotated.
- Scope `BRANCH_PROTECTION_TOKEN` (99 refs) to the minimum repo permissions and one workflow.
- Keep external-comment alerts until the flip, then retire.
- Log secret changes (name/time/actor) to an append-only file.

## 5. Operator decisions needed

1. Accept GitHub Pro (~$4/mo, unverified price) for branch protection/code owners on private, or accept losing them? Merge queue support on private personal accounts is unconfirmed and should be tested before deciding.
2. Approve building one `ci` runner VM and an `ops` path (Tier-2, uses Oracle Always Free capacity; confirm what is spare).
3. Approve the VM-side key-list page over the Colab notebook (Tier-2 build).
4. Is a possible future $0.002/min self-hosted fee (~$1k/mo at current load) a reason to stop at "public + self-hosted runners" instead of going private?

## 6. Verification limits

Measured: workflow inventory, triggers, `runs-on`, secret-name references, the four-day run sample, the repo's `private: false` state. Taken from docs fetched today: quotas, prices, plan feature lists, the fee postponement (secondary sources only). **Not verified:** the operator's actual GitHub plan, Pro's current price, Oracle's spare capacity, merge-queue behaviour on private personal repos, and whether the 10 `scripts/ops` helpers make unauthenticated GitHub calls.
