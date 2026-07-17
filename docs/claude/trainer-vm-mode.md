# Trainer VM mode — the autonomous-Claude charter

This doc binds any Claude Code session that operates against the
**trainer VM** (`ict-trainer-vm` — `VM.Standard.A1.Flex`, 1 OCPU /
6 GB, same OCI compartment + subnet as the live trader, tagged
`ict-role=training-center`).

It is the counterpart to [`vm-operator-mode.md`](vm-operator-mode.md),
which governs the **live trader VM** (`instance-20260414-1555`). The
two contracts are mutually exclusive: a session is either acting on
the live VM (restricted) or the trainer VM (autonomous). Never both
in the same chain of actions.

---

## 1. Why this exists

The live trader's trust contract is conservative because every
mutation has direct money-at-risk consequences. The trainer VM has
**no path to live order influence on its own**. As of the 2026-05-19
shadow-default flip the boundary is the **stage gate**, not the
YAML wire-up:

- Models registered at `target_deployment_stage: shadow` (the
  current default) auto-wire onto every strategy via
  `Coordinator._get_shadow_predictors`. They log predictions to
  `runtime_logs/shadow_predictions.jsonl` on every signal but
  **never change the order package** — that's the WS7
  non-negotiable.
- Models at `advisory` are the only stage that can influence the
  order package. Promotion past `shadow` requires operator approval
  — that's the live-trading switch.
- Models at `candidate` are refused by the shadow factory entirely.
  This stage is the explicit operator-demotion / pre-shadow park,
  not the default path.

> **Stage ladder collapsed 7→3 (2026-06-16).** Canonical stages are
> now `candidate → shadow → advisory`. The legacy names still resolve:
> `research_only`/`backtest_approved → candidate` and
> `limited_live`/`live_approved → advisory`, normalized by
> `ml.manifest.canonical_stage` (existing registry rows / manifests
> never strand). `advisory` is the single influence stage.

A strategy can opt out of auto-wire by setting
`shadow_model_ids: []` in its YAML; a non-empty list pins specific
ids. Missing/None triggers auto-wire.

That separation lets Claude run the trainer autonomously without
risking the live trader, which is the point of the two-VM topology
([`docs/sprint-plans/ai-traders/ws9-runtime-split.md`](../sprint-plans/ai-traders/ws9-runtime-split.md)).

---

## 2. Detection — how Claude knows it's targeting the trainer VM

Three signals, any one of which is sufficient:

1. `/etc/ict-trainer-vm.role` exists on the host and contains
   `training-center` (set by `deploy/training-vm-cloud-init.yaml`).
2. The hostname matches `ict-trainer-vm` or the OCI display-name
   filter in `scripts/ops/provision_training_vm.py::DISPLAY_NAME`.
3. The session is running an MCP-orchestrated action against the
   provisioned trainer instance (SSH, OCI compute, etc.) without
   touching the live-VM IP `141.145.193.91` or its diag surface
   at `:8001`.

The **live VM marker** at `/etc/claude/vm-marker` is distinct and
takes precedence: if both markers somehow appear on the same host,
the live-VM rules win and the runner aborts. The two roles never
co-exist on one VM by construction.

---

## 3. Authority — autonomous within trainer scope

Within the trainer VM's blast radius, Claude operates without
operator approval. There are no tiers, no Telegram acks, no
`/vm_write` gating. Specifically:

### 3.a Allowed without asking

- **Infrastructure:** provision the trainer instance via the
  `provision-training-vm` workflow (Claude opens the labelled issue
  and includes its own `confirm: yes`); terminate + re-provision;
  resize within the Always Free quota ceiling (≤ 1 OCPU / 6 GB for
  the trainer); attach / detach storage; configure security-list
  ingress for SSH.
- **OS-level:** install packages (`apt`, `pip`, `npm`, `uv`); create
  / delete files anywhere under `/home/ubuntu/`, `/opt/`, `/var/log/`,
  `/tmp/`; manage systemd units that live on the trainer (`ict-trainer.service`,
  `ict-trainer.timer`, log-rotate timers, side-car services); rotate
  log files; change crontabs.
- **Code:** edit / push to any path under `ml/`, `scripts/ops/`,
  `docs/ml/`, `docs/sprint-plans/ai-traders/`,
  `docs/sprint-logs/S-AI-*`, `tests/ml/`, `tests/test_*shadow*`,
  `tests/test_*train*`. Branch + PR + merge if CI is green.
- **Data:** sync `trade_journal.db`, `signal_audit.jsonl`,
  `shadow_predictions.jsonl` (+ `_backfill`, for the `gate-check`
  realized join — S-MLOPT-S8-FU), and the IBKR MES `market_raw`
  shards read-only from the live VM (via `scripts/ops/sync_trainer_data.sh`,
  rsync over SSH using `VM_SSH_KEY`, or via the diag API at
  `:8001/api/diag/journal`); build / rebuild datasets under
  `ml/datasets/`; cache market data fetches from `bybit_offvm`;
  delete + rebuild any artifact under `artifacts/` (except
  `artifacts/health/`, which is live-VM owned).
- **Training:** run training + build datasets; trigger
  `scripts/ops/run_training_cycle.sh` ad-hoc or via timer; write
  manifests under `ml/configs/`; benchmark predictors; spin up
  one-off Hugging Face Spaces / Hub queries. **BUT — the trainer VM is
  6 GB and memory-constrained, so any memory-heavy run MUST go through the
  shared job QUEUE, never a bare `python -m ml train …`:**
  `scripts/ops/trainer_run.sh python -m ml train ml/configs/<manifest>.yaml`
  (it serializes against the training cycle / promotion-readiness /
  drift-retrain instead of thrashing the box). For a large / experimental /
  long run, prefer the **GPU-burst platform** (`gpu-burst-train.yml`) when it's
  within the $10/mo budget, so it doesn't block the queue at all. **Never
  `sudo systemctl stop ict-trainer` to "make room"** — the queue handles
  contention. Full rules: [`trainer-resource-protocol.md`](trainer-resource-protocol.md).
- **Registry:** call `python -m ml.registry register | promote_stage
  | demote_stage` for **any** stage in the canonical 3-stage ladder
  (`candidate → shadow → advisory`; the legacy 7-stage names alias
  to these via `ml.manifest.canonical_stage`). The registry's
  append-only `StatusEvent` history is the audit trail — `--by`
  is set to `claude-trainer` and `--reason` carries the
  promotion rationale verbatim from the training summary.
- **Side-cars:** provision additional Always Free VMs in the same
  compartment for HF-runtime / inference / batch evaluation, up to
  the 4-OCPU tenancy ceiling minus 1 OCPU reserved for the live
  trader.

### 3.b Allowed with audit-trail formality only

These actions are permitted but must produce an artifact someone
can review later. Claude does them without asking, then commits the
artifact in the same PR / pushes the JSONL line in the same step.

- **Cross-VM read:** pulling `trade_journal.db` from the live VM
  via SSH. Always uses `mode=ro` on the SQLite open. Logs the
  fetch as a JSONL row in `runtime_logs/trainer/db_pulls.jsonl`
  (path on the trainer VM, not the live VM).
- **Promotion to `advisory`:** when promoting a model to `advisory`
  (the single live-influence stage), Claude additionally writes
  a sprint-log entry under `docs/sprint-logs/S-AI-WS5-PROMOTION-*`
  with: model id, dataset hash, eval metrics, the decision rule
  that justified the promotion, and a "still operator-gated"
  reminder (the `shadow → advisory` edge is where live influence
  begins, per the 2026-05-19 default flip). The former
  `limited_live` / `live_approved` tiers were collapsed into
  `advisory` on 2026-06-16.
- **Workflow dispatch:** when Claude fires
  `provision-training-vm`, `vm-web-api-recover`, or any other
  trainer-scoped workflow, the issue body it authors counts as
  the audit record. Always-include `reason: <one-line>` in the
  body so the run is grepable.

---

## 4. Hard limits — still off-limits on the trainer

The trainer's autonomous authority **does not** extend to:

- **Live VM filesystem.** Never SSH into `141.145.193.91` with
  intent to mutate. Read-only is fine (`/api/diag/*`, rsync `--dry-run`,
  `trade_journal.db` pull). Anything that would write to the live
  VM — including `apt`, `systemctl restart`, `git push` to a path on
  the live VM, edits to `/etc/ict-trader/`, edits to live unit files
  — is **Tier 3** under [`vm-operator-mode.md`](vm-operator-mode.md)
  and remains there.
- **Strategy YAML edits on the live VM.** `config/strategies.yaml`
  is checked into the repo, but the *deployed* copy on the live VM
  is what the live `Coordinator` reads. Since the 2026-05-19
  shadow-default flip the YAML no longer controls *whether* shadow
  predictions log (auto-wire handles that), but every other
  parameter in the file — risk_pct, timeframes, gate thresholds,
  shadow opt-out via `shadow_model_ids: []`, or pinning a specific
  list — is still a live-trading decision. Claude can *propose* an
  edit in a PR, but **never** merge it to `main` itself. PRs that
  touch `config/strategies.yaml` or `config/accounts.yaml` are
  marked draft + operator-review-required in the PR body.
- **Risk caps / account flags.** `config/risk_caps.yaml`,
  `config/accounts.yaml` `mode` field flips (`live ↔ dry_run`),
  exchange API keys, JWT signing keys — all immutable from the
  trainer context. PRs touching these files require operator merge.
- **Live VM service control.** `restart-bot-service`, `reboot-vm`,
  any systemctl action against `ict-trader-live.service` /
  `ict-web-api.service` — those route through
  `system-actions.yml` and the Tier-2 ack contract in
  [`docs/claude/system-actions.md`](system-actions.md). The
  trainer charter doesn't override that.
- **Master secrets.** `config/master-secrets.template.yaml` is a
  template only; populated secrets live outside the repo and
  outside the trainer VM. Never copy production secrets to the
  trainer.
- **OCI quota beyond 1 OCPU / 6 GB for the trainer.** The Always
  Free tenancy ceiling is 4 OCPU / 24 GB. Live trader holds 1 / 6.
  Trainer holds 1 / 6. Up to 2 / 12 remains for side-cars (HF
  runtime, inference, batch eval). Claude tracks the running total
  via OCI `list_instances` before any new provision and refuses
  to exceed the ceiling.

---

## 5. Promotion-to-live workflow (the one place autonomy meets caution)

Updated 2026-05-19 (shadow-default flip). The promotion ladder is
Claude's to manage end-to-end on the registry side up to `shadow`.
The `shadow → advisory` transition (and every step beyond) is the
operator-approved gate where autonomy ends.

| Step | Actor | Why |
|---|---|---|
| Train + eval a model | Claude (trainer) | Pure trainer workload. |
| Register at `shadow` (the default since 2026-05-19) | Claude (trainer) | Auto-wired onto every strategy via `Coordinator._get_shadow_predictors` on the next reload; predictions log but never influence the order package. |
| Register at `candidate` (the pre-shadow park; only when explicitly demoted) | Claude (trainer) | Operator-park stage — refused by the shadow factory; predictions don't log. (Legacy `research_only`/`backtest_approved` alias to `candidate`.) |
| Promote `shadow → advisory` | Operator-approved (Claude proposes via PR + sprint-log entry) | **This is the live-trading switch.** `advisory` is the single influence stage (the former `limited_live`/`live_approved` tiers collapsed into it 2026-06-16). Sprint-log entry mandatory (§ 3.b). |
| (Optional) Pin or opt-out via `shadow_model_ids` in a strategy YAML | Operator (live VM) | Override the auto-wire default for a specific strategy. PR proposing the edit is fine; merging it requires operator approval per § 4. |
| Reload the live `Coordinator` to pick up registry or YAML changes | Operator (Telegram `/vm_write` or `system-actions.yml` `restart-bot-service` / `pull-and-deploy`) | Live-VM action, Tier 2. |

The boundary is the stage, not the YAML. A `shadow` model
auto-wires onto every strategy with no manual YAML edit; a model
at `candidate` is refused even if pinned. Operator approval lives at
the `shadow → advisory` transition.

---

## 6. Audit trail

Every autonomous-Claude action on the trainer produces at least one
of:

- A git commit on the working branch (default branch name pattern:
  `claude/<sprint-or-task>-<short-hash>`).
- A JSONL row under `runtime_logs/trainer/<topic>.jsonl` (e.g.
  `db_pulls.jsonl`, `training_runs.jsonl`, `registry_promotions.jsonl`).
- A `StatusEvent` row in the model registry (append-only, never
  edited).
- A GitHub issue or PR comment that the workflow auto-closes.

A trainer-side action that produces *none* of those is forbidden —
even a one-shot `rm`. If Claude needs to clean up an artifact, it
records the deletion in `runtime_logs/trainer/cleanup.jsonl` first.

---

## 7. Boundary checks — when in doubt

If an action is ambiguous between trainer-autonomous and
live-VM-restricted, default to the **live-VM** rules and ask the
operator. Examples that look trainer-side but aren't:

- Editing a path under `config/` — looks like config, but the
  `config/` tree is read by the live trader; treat all of it as
  live-VM scope.
- Editing `deploy/ict-trader-live.service` or any unit file the
  live VM runs — even though the file lives in the repo, its
  effect is live-VM scope.
- Adding an entry to `requirements.txt` or `pyproject.toml` —
  the live VM consumes these files at deploy time, so dependency
  bumps that aren't isolated to the trainer's pinned set need
  operator review.
- Modifying `.github/workflows/*` that the live VM consumes
  (`vm-diag-snapshot.yml`, `vm-web-api-recover.yml`,
  `system-actions.yml`) — those are live-VM trust surface even
  though they live in the bot repo.

When in doubt: open a PR, mark it draft, leave a comment naming
the boundary concern, and ping the operator. The cost of a 60-second
clarification is lower than the cost of accidentally crossing into
live-VM scope.

---

## 9. Trainer VM visibility — diag relay (autonomous permission)

Claude has **full, unconditional visibility** into everything on the
trainer VM. No operator approval is required to pull any log, run any
read or diagnostic command, or inspect any artifact.

> **Anti-pattern check.** If you are about to write any of these phrases,
> stop and fire `trainer-vm-diag-request` first:
> "I cannot directly observe the trainer VM" /
> "no SSH from this session" /
> "I don't have live access" /
> "I can only see what's in the repo" /
> "to know status, an operator…" /
> "could you SSH in and paste…".
> See root `CLAUDE.md` banner for the full list. The relay below is the
> remedy in every case.

### 9.a The `trainer-vm-diag` workflow

`.github/workflows/trainer-vm-diag.yml` is an issue-driven SSH relay.
Claude opens a `trainer-vm-diag-request`-labelled issue with a `cmd:`
block and the workflow SSHes into the trainer VM, runs the command, and
posts the full output back as an issue comment.

**Issue format:**

```
Title: [trainer-diag] <short description>
Body:
cmd: <any bash command or script>
```

Multi-line commands:
```
cmd: |
  cd /home/ubuntu/ict-trading-bot
  tail -n 500 runtime_logs/trainer/dataset_builds.jsonl
  systemctl status ict-trainer.timer
```

The command is sent via SSH stdin — it is never interpolated in the
shell invocation, so it is safe to include paths, flags, and pipes.

### 9.b What Claude may pull without asking

Everything. There are no restricted paths on the trainer VM. Claude
is autonomous here. Examples of things Claude pulls routinely:

| What | Command |
|---|---|
| Trainer service status | `systemctl status ict-trainer.service ict-trainer.timer` |
| Recent training logs | `tail -n 500 runtime_logs/trainer/training_runs.jsonl` |
| Dataset build log | `tail -n 300 runtime_logs/trainer/dataset_builds.jsonl` |
| DB sync audit trail | `tail -n 100 runtime_logs/trainer/db_pulls.jsonl` |
| Disk + memory | `df -h && free -h` |
| Python / venv state | `ls .venv/lib/python*/site-packages/ \| grep -E 'ccxt\|torch\|sklearn'` |
| Systemd journal (last N lines) | `journalctl -u ict-trainer.service -n 200 --no-pager` |
| Registry state | `python -m ml.registry list --all` |
| Dataset output | `ls -lh datasets-out/` |
| Any log file | `cat runtime_logs/trainer/<file>.jsonl` |
| Any script output | `bash scripts/ops/<script>.sh` (read-only scripts) |

### 9.c GitHub Actions workflow run logs

Claude cannot read GitHub Actions run logs directly via MCP tools in
the current toolset. The operator pastes failing step output into
chat, or Claude uses the diag relay to check what happened on the VM
side. When `.mcp.json` is loaded (next session start after it was
added to main), `workflow_dispatch` + `list_workflow_runs` may become
available.

### 9.d Usage contract

- Claude opens `trainer-vm-diag-request` issues autonomously whenever
  it needs visibility, without asking the operator first.
- Every such issue is self-documenting: the title names the intent,
  the body carries the command, the workflow posts the full output.
  No separate audit entry is needed.
- Claude closes the loop by reacting to the issue comment in the same
  conversation turn — it reads the output, diagnoses, and acts.

---

## 10. Running an interactive backtest sweep

Distinct from a training cycle. Use this when the operator (or a
session in this thread) wants the trainer to evaluate one or more
strategy variants against the persistent BTCUSDT 5m parquet cache and
return a comparable Sharpe / cadence / max-DD table.

**One-issue dispatch:**

Open a `trainer-vm-diag-request`-labelled issue with body:

```yaml
cmd: |
  cd /home/ubuntu/ict-trading-bot && git pull --ff-only && \
    bash scripts/ops/run_backtest_sweep.sh
```

The orchestrator handles the venv bootstrap, the parquet cache
refresh, the variant harness, and the ict_scalp re-validation. Output
is posted as an issue comment (SUMMARY.md tail) and persists on the
trainer at `/home/ubuntu/ict-trader-data/backtests/<UTC-date>/`.

Full procedure, file layout, gate criteria, and failure modes:
[`docs/runbooks/trainer-backtest.md`](../runbooks/trainer-backtest.md).

This is autonomous trainer-VM work per § 3.a. Adopted 2026-05-17 in
S-TRAINER-BT-1, after the PR #1358 incident exposed that the trainer's
venv had never been bootstrapped (the cloud-init's claim that
`run_training_cycle.sh` "does not yet exist" was itself stale —
the script existed, but no session had ever fired it on this trainer
instance, so the venv was missing).

## 8. Related docs

- [`vm-operator-mode.md`](vm-operator-mode.md) — live VM trust contract (the restrictive counterpart).
- [`system-actions.md`](system-actions.md) — narrow mutating bridge for live-VM actions from PM-side sessions.
- [`docs/sprint-plans/ai-traders/ws9-runtime-split.md`](../sprint-plans/ai-traders/ws9-runtime-split.md) — the policy of record for the two-VM split.
- [`docs/runbooks/training-vm.md`](../runbooks/training-vm.md) — provisioning + bootstrap runbook for the trainer.
- [`docs/runbooks/trainer-backtest.md`](../runbooks/trainer-backtest.md) — backtest sweep orchestrator + per-step infrastructure (S-TRAINER-BT-1).
- [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) — master plan; § "Non-negotiable rules" defers the live-wiring gate to this doc.
- [`docs/ml/model-registry-policy.md`](../ml/model-registry-policy.md) — registry append-only semantics + the promotion ladder.
