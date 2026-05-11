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
**no path to live influence on its own** — the live VM's `Coordinator`
only loads shadow models that the operator has wired into
`config/strategies.yaml` via the `shadow_model_ids` field. Until that
YAML edit lands and the live VM reloads, anything written to the
model registry is just metadata.

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
   touching the live-VM IP `158.178.210.252` or its diag surface
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
- **Data:** sync `trade_journal.db` read-only from the live VM (via
  rsync over SSH using `VM_SSH_KEY`, or via the diag API at
  `:8001/api/diag/journal`); build / rebuild datasets under
  `ml/datasets/`; cache market data fetches from `bybit_offvm`;
  delete + rebuild any artifact under `artifacts/` (except
  `artifacts/health/`, which is live-VM owned).
- **Training:** run any `python -m ml.train …` invocation; trigger
  `scripts/ops/run_training_cycle.sh` ad-hoc or via timer; write
  manifests under `ml/configs/`; benchmark predictors; spin up
  one-off Hugging Face Spaces / Hub queries.
- **Registry:** call `python -m ml.registry register | promote_stage
  | demote_stage` for **any** stage in the canonical ladder
  (`research_only → candidate → backtest_approved → shadow →
  advisory → limited_live → live_approved`). The registry's
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
- **Promotion past `advisory`:** when promoting a model to
  `limited_live` or `live_approved`, Claude additionally writes
  a sprint-log entry under `docs/sprint-logs/S-AI-WS5-PROMOTION-*`
  with: model id, dataset hash, eval metrics, the decision rule
  that justified the promotion, and a "live wiring still
  operator-gated" reminder pointing to the `shadow_model_ids`
  YAML.
- **Workflow dispatch:** when Claude fires
  `provision-training-vm`, `vm-web-api-recover`, or any other
  trainer-scoped workflow, the issue body it authors counts as
  the audit record. Always-include `reason: <one-line>` in the
  body so the run is grepable.

---

## 4. Hard limits — still off-limits on the trainer

The trainer's autonomous authority **does not** extend to:

- **Live VM filesystem.** Never SSH into `158.178.210.252` with
  intent to mutate. Read-only is fine (`/api/diag/*`, rsync `--dry-run`,
  `trade_journal.db` pull). Anything that would write to the live
  VM — including `apt`, `systemctl restart`, `git push` to a path on
  the live VM, edits to `/etc/ict-trader/`, edits to live unit files
  — is **Tier 3** under [`vm-operator-mode.md`](vm-operator-mode.md)
  and remains there.
- **Strategy YAML wiring on the live VM.** `config/strategies.yaml`
  is checked into the repo, but the *deployed* copy on the live VM
  is what the live `Coordinator` reads. Adding a `shadow_model_ids`
  entry, even via a PR, is a live-trading decision: the operator
  reviews + merges + the live VM pulls + restarts. Claude can
  *propose* the edit in a PR, but **never** merge it to `main`
  itself. PRs that touch `config/strategies.yaml` or
  `config/accounts.yaml` are marked draft + operator-review-required
  in the PR body.
- **Risk caps / account flags.** `config/risk_caps.yaml`,
  `config/accounts.yaml` `mode` field flips (`live ↔ dry_run`),
  exchange API keys, JWT signing keys — all immutable from the
  trainer context. PRs touching these files require operator merge.
- **Live VM service control.** `restart-bot-service`, `reboot-vm`,
  any systemctl action against `ict-trader-live.service` /
  `ict-web-api.service` — those route through
  `operator-actions.yml` and the Tier-2 ack contract in
  [`docs/claude/operator-actions.md`](operator-actions.md). The
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

The promotion ladder is Claude's to manage end-to-end on the
registry side. The live-trading wiring is the operator's.

| Step | Actor | Why |
|---|---|---|
| Train + eval a model | Claude (trainer) | Pure trainer workload. |
| Register at `research_only` / `candidate` / `backtest_approved` | Claude (trainer) | Metadata only. |
| Promote to `shadow` | Claude (trainer) | Eligible to load; no live influence. |
| Promote to `advisory` | Claude (trainer) | Eligible to surface in dashboard panels; still no live influence. |
| Promote to `limited_live` / `live_approved` | Claude (trainer) | Metadata only — the registry stage doesn't wire the model anywhere. Sprint-log entry mandatory (§ 3.b). |
| Add model id to `shadow_model_ids` in a strategy's YAML | Operator (live VM) | This is the live-trading switch. PR proposing the edit is fine; merging it is not. |
| Reload the live `Coordinator` to pick up the new YAML | Operator (Telegram `/vm_write` or `operator-actions.yml` `restart-bot-service`) | Live-VM action, Tier 2. |

The boundary is the YAML, not the registry. A `live_approved` model
that's not in any strategy's `shadow_model_ids` list influences
nothing.

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
  `operator-actions.yml`) — those are live-VM trust surface even
  though they live in the bot repo.

When in doubt: open a PR, mark it draft, leave a comment naming
the boundary concern, and ping the operator. The cost of a 60-second
clarification is lower than the cost of accidentally crossing into
live-VM scope.

---

## 8. Related docs

- [`vm-operator-mode.md`](vm-operator-mode.md) — live VM trust contract (the restrictive counterpart).
- [`operator-actions.md`](operator-actions.md) — narrow mutating bridge for live-VM actions from PM-side sessions.
- [`docs/sprint-plans/ai-traders/ws9-runtime-split.md`](../sprint-plans/ai-traders/ws9-runtime-split.md) — the policy of record for the two-VM split.
- [`docs/runbooks/training-vm.md`](../runbooks/training-vm.md) — provisioning + bootstrap runbook for the trainer.
- [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md) — master plan; § "Non-negotiable rules" defers the live-wiring gate to this doc.
- [`docs/ml/model-registry-policy.md`](../ml/model-registry-policy.md) — registry append-only semantics + the promotion ladder.
