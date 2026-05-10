# S-AI-WS9-FU — `run_training_cycle.sh` (body of `ict-trainer.service`)

**Date:** 2026-05-10
**Authority:** [`docs/sprint-logs/S-AI-WS9.md`](S-AI-WS9.md), [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md)
**Status:** ✅ COMPLETE — script ships; cadence wiring (timer / cron) deferred to operator.

## Goal

Fill the gap left by WS9: the trainer VM bootstraps with
`ict-trainer.service` installed but disabled because its
`ExecStart=/bin/bash scripts/ops/run_training_cycle.sh` script
didn't exist on `main`. This PR ships that script. With it in
place, the operator can `systemctl enable --now ict-trainer.service`
on the trainer VM and a training cycle runs end-to-end.

## Decisions

- **One-shot, not a daemon.** `Type=oneshot` in the systemd unit
  means each invocation runs through the cycle once and exits.
  Cadence (run-on-boot? daily timer? on-merge to a `training`
  branch?) is the operator's call — the timer is already
  installed in `/etc/systemd/system/ict-trainer.timer` (also
  disabled), so cadence is a one-command enable when decided.
- **Pull-then-train, not "trust-the-checkout".** First step is
  `git fetch origin main && git reset --hard origin/main`. The
  trainer VM clones the repo on cloud-init, but cycles may run
  weeks apart; pinning to fresh `main` per cycle keeps
  manifests + trainer code aligned. Hard-reset is appropriate
  because no human edits files on the trainer (it's not a dev
  environment).
- **Default to "train every manifest under `ml/configs/`".**
  `TRAINING_MANIFESTS` env override accepts a space-separated
  list for targeted runs. Sorted alphabetically for
  deterministic ordering across cycles.
- **First failure short-circuits.** If manifest N fails, manifests
  N+1..end are skipped and the script exits 1. The journal
  carries the failing manifest's name + stderr tail. Operator
  decides whether to fix-forward or pin a known-good manifest list.
  Rationale: chained training runs that ignore failures produce
  noisy registries; better to halt + investigate.
- **JSONL event stream to `runtime_logs/training_cycle.jsonl`.**
  Same observability pattern as `signal_audit.jsonl` and
  `shadow_predictions.jsonl`. Events: `pulled`, `venv_created`,
  `cycle_start`, `manifest_ok`, `manifest_failed`,
  `manifest_missing`, `no_manifests`, `env_error`, `cycle_end`.
  Future WS8 work can surface this through the dashboard the
  same way `shadow_predictions.jsonl` does.
- **No dataset build step.** This is a pure training cycle; it
  assumes `DATASETS_ROOT` already has the families it needs. The
  cross-VM data-sync follow-up filed on WS9 will populate
  `DATASETS_ROOT` (or its source SQLite) from the live VM. Until
  then, the operator seeds manually (per
  `docs/runbooks/training-vm.md` § "Cross-VM data sync").
- **Venv lazily provisioned.** First run creates `.venv`,
  installs `requirements.txt`. Subsequent runs reuse. Logged as a
  `venv_created` event so journal carries the one-time cost.
- **No git-tag of trained model versions.** Append-only registry
  + experiment-dir timestamps are the canonical record; tagging
  commits adds noise without information.

## Deliverables

- `scripts/ops/run_training_cycle.sh` (new) — ~130 LOC, bash + inline
  Python for JSON event composition. Env-driven config; no flags.
- `tests/test_run_training_cycle_sh.py` (new) — env-error
  guardrail (missing repo), JSONL written to log path,
  `bash -n` syntax check.

## Acceptance

- [x] `bash -n scripts/ops/run_training_cycle.sh` clean.
- [x] `pytest tests/test_run_training_cycle_sh.py` — 3 / 3 pass.
- [x] Full regression: 323 tests pass.
- [x] Script exits 2 on missing repo with `env_error` JSONL.
- [x] JSONL events also persist to `TRAINING_LOG_PATH` (not just
      stdout) so the systemd journal isn't the only record.

## Out of scope (filed for follow-ups)

- **Cadence wiring.** Operator decides whether to
  `systemctl enable --now ict-trainer.timer` (daily) or
  invoke on demand.
- **Cross-VM data sync.** Trainer VM needs `trade_journal.db`
  + dataset families. Live VM owns them. Decision (rsync vs
  diag-API) still operator's. Manual `scp` per runbook until then.
- **Resource budget enforcement.** A misbehaving manifest could
  OOM the 6 GB trainer; `MemoryLimit=` in the systemd unit is a
  cheap mitigation but not yet wired.
- **Per-manifest selective re-run.** Once the registry surfaces
  "stale candidates", a script that picks manifests with
  no-recent-candidate-for-strategy could replace the
  "every-manifest" default.

## Live runtime impact

None until the operator enables the trainer service. The script
lives in the repo; cloud-init's `git clone` picks it up; the
systemd unit's `ExecStart` resolves correctly on next
`daemon-reload`. Until `systemctl start` is invoked the script
is dormant.
