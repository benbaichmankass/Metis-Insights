# S-AI-WS7-FU — Shadow-prediction audit log rotation

**Date:** 2026-05-10
**Authority:** [`docs/sprint-logs/S-AI-WS7-PART-6.md`](S-AI-WS7-PART-6.md), [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md)
**Status:** ✅ COMPLETE — script + systemd unit + timer; **disabled by default**.

## Goal

Resolve the long-standing filed follow-up: `runtime_logs/shadow_predictions.jsonl` grows unbounded once shadow mode is active. With multiple predictors per strategy, each tick appends N JSON lines; the file can hit GB scale in weeks. Ship a rotation script + systemd timer so the operator can `systemctl enable --now ict-shadow-log-rotate.timer` and forget about it.

## Decisions

- **External cron via systemd timer, not in-process rotation.**
  Mutating the shared log file mid-write inside `ShadowPredictor.predict` (which is called per-tick from the strategy hot path) adds I/O and complexity for marginal benefit. Daily timer + atomic rename is simpler and matches the repo's existing systemd pattern (`ict-trader-live.service`, `ict-web-api.service`).
- **Two thresholds: size OR age.** Size keeps file from blowing up under a busy model; age keeps weekly traces compact even if the model is quiet. Defaults: 100 MiB OR 7 days. Operator can tune via flags.
- **Atomic rename, then touch fresh log.** `os.replace()` is atomic within a filesystem. The writer (`ShadowPredictor`) re-creates the file on next append via the standard `open("a")` semantics — no special handling needed in the predictor.
- **Disabled by default.** Same posture as `ict-trainer.service` and `ict-trainer.timer` from WS9. Operator opts in. If shadow mode isn't in production yet, rotation runs against an empty file daily for no value — opt-in keeps the noise out.
- **Gzip optional.** `--gzip` flag toggles compression of the rotated copy. Default is plain JSONL so the operator can `tail`/`grep` rotated files without `zcat`. Disk pressure is the trigger for enabling `--gzip` (mirrored in the systemd unit's `ExecStart=` default, which passes `--gzip` because long-term storage benefits more than ad-hoc grep).
- **Always exits 0.** Log rotation must never crash the system. Errors (permission denied, disk full, gzip failure) are reported as JSONL `error` / `warning` events and the script returns 0 so the timer continues to fire.
- **Date-suffixed filenames + numeric collision suffix.** `shadow_predictions.2026-05-10.jsonl` → `…1.jsonl` → `…2.jsonl` etc. when rotation fires multiple times per UTC day. Avoids overwriting prior rotations.
- **JSONL events to stdout, journalled by systemd.** Same observability surface as the trainer cycle script. Operator can `journalctl -u ict-shadow-log-rotate.service` to see the history.

## Deliverables

- `scripts/ops/rotate_shadow_log.py` (new) — ~150 LOC. `rotate(...)` is the testable surface; `main(argv)` is the CLI entrypoint. Flags: `--log`, `--max-bytes`, `--max-age-days`, `--gzip`.
- `deploy/ict-shadow-log-rotate.service` (new) — `Type=oneshot`. `ExecStart=/usr/bin/python3 scripts/ops/rotate_shadow_log.py --gzip`. User `ubuntu`, `WorkingDirectory=/opt/ict-trading-bot`.
- `deploy/ict-shadow-log-rotate.timer` (new) — `OnCalendar=daily` with `RandomizedDelaySec=15m`. `Persistent=true` so a missed run catches up at next boot.
- `tests/test_rotate_shadow_log.py` (new) — 13 tests:
  - No-op paths (missing file, empty file, fresh small file, missing dir).
  - Size rotation (oversize → rotate, date suffix, collision → numeric suffix).
  - Age rotation (old mtime → rotate).
  - Gzip mode (rotated file gzipped, plain file removed, gzip roundtrips).
  - `main(argv)` entrypoint with all flags.

## Acceptance

- [x] `pytest tests/test_rotate_shadow_log.py` — 13 / 13 pass.
- [x] `ruff check` clean.
- [x] No-op when log is missing / empty / fresh.
- [x] Rotates on size threshold.
- [x] Rotates on age threshold.
- [x] Date-suffixed filename; numeric suffix on collision.
- [x] `--gzip` produces `.gz` + removes plain.
- [x] Errors (missing dir, permission-denied) emit JSONL events without raising.
- [x] systemd unit + timer ship to `deploy/` — operator workflow is `cp deploy/ict-shadow-log-rotate.* /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now ict-shadow-log-rotate.timer`.

## Out of scope (filed for follow-ups)

- **Auto-install of the systemd unit + timer.** Currently the unit lives in `deploy/` and is copied to `/etc/systemd/system/` by `scripts/deploy_diag.sh` (or equivalent). A future operator-actions workflow could automate the copy-and-reload, but doing so on the live VM is Tier-3 and not in scope here.
- **Rotation for other audit logs.** `signal_audit.jsonl`, `training_cycle.jsonl`, `validation.jsonl` all have the same unbounded-growth issue. The script is generic enough (`--log` arg) to handle any of them; a follow-up could ship per-log timer units pointing at this script.
- **Retention policy.** Rotation keeps every rotated file forever. A `--keep-days N` flag that deletes files older than N days would close the disk-pressure loop. Filed.
- **Compression of existing un-gzipped rotated files.** If the operator runs without `--gzip` initially and later switches on, the older `.jsonl` rotations stay plain. A one-shot batch-compress script would catch them up; not yet shipped.

## Live runtime impact

None until the operator enables the timer:

```bash
sudo systemctl enable --now ict-shadow-log-rotate.timer
```

After that, the timer fires daily and rotates `runtime_logs/shadow_predictions.jsonl` if it exceeds 100 MiB or 7 days. The writer (`ShadowPredictor`) is unmodified — it transparently re-creates the file on next append after rotation. Shadow predictions continue to flow without interruption.

## On the trainer VM

If the trainer VM ever runs shadow-mode evaluation (e.g. against held-out backtest data), the same timer should be enabled on the trainer VM. The systemd unit reads `/opt/ict-trading-bot/scripts/ops/rotate_shadow_log.py` which is part of the standard repo clone — both VMs get it from the same `git pull`.
