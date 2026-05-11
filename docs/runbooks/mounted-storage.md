# Mounted storage runbook

**Audience:** VM operator (Tier 3). The person doing this is either the human operator or Claude with operator approval. Either way, the steps below are the exact contract — don't extrapolate.

## What this runbook covers

- Verifying that `/data/bot-data` is mounted and writable.
- Confirming the trader is actually writing there (not silently falling back to repo subdirs).
- Migrating an existing live VM from repo-resident data to the mount.
- Rolling back if anything goes wrong.
- What to monitor for the first 24 hours after a flip.

## Pre-conditions

- The OCI block volume has been created and attached to the instance. As of S-OCI (PRs #853–#875) this is automated end-to-end via [`.github/workflows/oci-storage.yml`](../../.github/workflows/oci-storage.yml) (`mode = execute`). Run [`docs/automation/oci-storage-setup.md`](../automation/oci-storage-setup.md) instead of doing the steps below by hand on a fresh VM; the workflow drives sections 1–6 with idempotent operations and a single `production-oci` approval gate.
- `/data/bot-data/` exists as a mount point on the VM.
- The repo at `/home/ubuntu/ict-trading-bot` is on a commit ≥ `7b5ec02` (PR 1 merged).

The rest of this runbook is the **manual fallback** — useful when
you're debugging a single step, recovering from a partial run, or
working on a VM where the workflow hasn't been wired up yet.

## 1. Verify the mount

```bash
mountpoint /data/bot-data            # exits 0 if it's a real mount
df -h /data/bot-data                 # shows the volume's capacity, not the root fs
ls -ld /data/bot-data                # should be owned by ubuntu:ubuntu, perms 755 or 775
```

If `mountpoint` exits non-zero, **stop here** and resolve the mount first. The rest of the runbook assumes the volume is up.

For the automated equivalent of this section plus everything that
follows, dispatch `oci-storage-verify` from the Actions tab and read
the resulting `oci-verify`-labelled issue.

## 2. Run the preflight

```bash
sudo -u ubuntu /home/ubuntu/ict-trading-bot/scripts/check_data_dir.sh /data/bot-data
```

What you want to see:

```
  [OK] exists: /data/bot-data
  [OK] is a mountpoint
  [OK] writable by ubuntu
  [OK] subdir created: data/
  [OK] subdir created: runtime_logs/
  [OK] subdir created: runtime_state/
  [OK] subdir created: artifacts/
  [OK] N GiB free
check_data_dir: all checks passed.
```

If any `FAIL` line appears, **stop here**. The script exits non-zero so a subsequent `ExecStartPre` would also fail; fix the underlying issue (perms, mount, ownership) before continuing.

## 3. Migrate existing data (dry-run first)

```bash
cd /home/ubuntu/ict-trading-bot
./scripts/migrate_to_data_dir.sh                  # dry-run, prints what would copy
./scripts/migrate_to_data_dir.sh --execute        # actual copy
```

The migration is **non-destructive** — `rsync -a` preserves perms/timestamps and doesn't delete source files. The trader can keep running during the dry-run; it will not see the new copies because `DATA_DIR` isn't set yet.

For larger datasets, run the migrate during a quiet window and re-run with `--execute` immediately before flipping `DATA_DIR` so the delta is small.

## 4. Install the systemd drop-in

```bash
for unit in ict-trader-live ict-web-api ict-claude-bridge; do
  sudo mkdir -p /etc/systemd/system/${unit}.service.d
  sudo cp /home/ubuntu/ict-trading-bot/deploy/dropins/data-dir.conf \
          /etc/systemd/system/${unit}.service.d/data-dir.conf
done
sudo systemctl daemon-reload
```

The drop-in adds three directives without modifying the base unit:

- `RequiresMountsFor=/data/bot-data` — won't start if the mount is down.
- `ExecStartPre=scripts/check_data_dir.sh` — preflight gates every restart.
- `Environment=DATA_DIR=/data/bot-data` — opts the trader's path helpers in.

`ict-telegram-bot` is intentionally **not** in the loop — it has no
DATA_DIR-resident state. The verify workflow flags it as
`DATA_DIR=(unset)` and that is expected.

## 5. Restart the trader

```bash
sudo systemctl restart ict-trader-live
sudo systemctl restart ict-web-api
sudo systemctl restart ict-claude-bridge
```

Each restart fires `check_data_dir.sh` first; if the script fails, systemd holds the unit in `activating` and does not start the process. That's the safety net — better to be down for one tick than to write to the wrong filesystem.

## 6. Verify the trader is writing to the mount

```bash
# (a) Environment is correct.
systemctl show ict-trader-live | grep DATA_DIR
# Expected: Environment=...DATA_DIR=/data/bot-data...

# (b) Process holds files on the volume.
sudo lsof -p "$(systemctl show -p MainPID --value ict-trader-live)" \
    | grep -E '/data/bot-data|runtime_logs|runtime_state' | head

# (c) Heartbeat is being touched on the volume.
ls -la /data/bot-data/runtime_logs/heartbeat.txt
# Expected: mtime within the last ~60s.
date -u
```

If `lsof` shows the trader still holding the **old** `/home/ubuntu/ict-trading-bot/runtime_logs/` paths, the drop-in didn't take effect — `daemon-reload` was skipped, or there's a typo in `data-dir.conf`. Re-check, restart, repeat.

The one-shot equivalent of all of section 6 is `scripts/verify_storage_setup.sh` (or dispatching the `oci-storage-verify` workflow).

## Rollback procedure

If anything looks wrong (warnings in `journalctl`, dashboard 500s, missing files), revert in this order:

```bash
# 1. Remove the drop-in.
for unit in ict-trader-live ict-web-api ict-claude-bridge; do
  sudo rm -f /etc/systemd/system/${unit}.service.d/data-dir.conf
done
sudo systemctl daemon-reload

# 2. Restart.
sudo systemctl restart ict-trader-live ict-web-api ict-claude-bridge

# 3. Confirm the trader is back to repo-relative paths.
sudo lsof -p "$(systemctl show -p MainPID --value ict-trader-live)" \
    | grep -E 'runtime_logs|runtime_state' | head
# Expected: paths under /home/ubuntu/ict-trading-bot/runtime_logs etc.
```

The migrate script **never deleted** the source files, so the repo subdirs still contain the pre-migration state. The trader picks them up again as soon as `DATA_DIR` is unset.

The data that was written to `/data/bot-data/` during the brief flip-on window is not lost — it's still on the volume. Decide later whether to merge it back into the repo subdirs (rare) or keep it as a snapshot.

For the full automated rollback (including OCI detach) see
[`docs/automation/oci-storage-setup.md`](../automation/oci-storage-setup.md) § 4.

## What to monitor in the first 24 hours

- `runtime_logs/heartbeat.txt` mtime on the mount, every 60 s. Use `watch -n 30 ls -la /data/bot-data/runtime_logs/heartbeat.txt`.
- `journalctl -u ict-trader-live -f` for any path-resolution warnings ("paths: repo-relative ... not writable").
- Disk usage on the volume (`df -h /data/bot-data`) — sanity check that growth rate matches what the repo subdirs were doing.
- `/api/bot/health/latest` returns the snapshot bundle (proves `artifacts_dir()` is reading the right place).
- Hourly Telegram report fires at the top of the hour (proves `runtime_logs/outcomes.jsonl` aggregation works against the mount).
- The 6-hourly health-snapshot PR includes a `=== STORAGE ===` section; spot-check it for fstab persistence and DATA_DIR drift across all three trader services.

## Common failure modes and quick fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `Job for ict-trader-live.service failed because the control process exited with error code.` | `ExecStartPre` failed — mount gone or perms wrong. | Run preflight manually; resolve before re-enabling. |
| `journalctl` shows `paths: repo-relative ... not writable; falling back to ~/.ict-trading-bot/...` | Trader running, but `DATA_DIR` unset and the repo subdir is read-only. | Set `DATA_DIR` or fix the repo perms; never let this be the steady state in prod. |
| Heartbeat in the mount is stale; repo heartbeat is fresh | Drop-in not installed or `daemon-reload` missed; trader still reading repo paths. | Re-run step 4 + 5, verify with step 6(a). |
| Dashboard `/api/bot/health/latest` returns 404 | `artifacts/health/` not migrated. | Re-run migrate script; `latest.json` symlink may need recreating from `health_check_*.json`. |

## Cross-references

- [`docs/automation/oci-storage-setup.md`](../automation/oci-storage-setup.md) — the automated provisioning + verify pipeline (workflow-driven).
- [`docs/architecture/oci-block-storage.md`](../architecture/oci-block-storage.md) — why this exists.
- [`docs/security/permissions-tiers.md`](../security/permissions-tiers.md) — who can run which step.
- [`deploy/dropins/README.md`](../../deploy/dropins/README.md) — drop-in installation reference.
- [`scripts/check_data_dir.sh`](../../scripts/check_data_dir.sh), [`scripts/migrate_to_data_dir.sh`](../../scripts/migrate_to_data_dir.sh), [`scripts/verify_storage_setup.sh`](../../scripts/verify_storage_setup.sh) — the tools this runbook drives.
