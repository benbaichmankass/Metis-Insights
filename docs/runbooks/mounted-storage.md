# Mounted storage runbook

**Audience:** VM operator (Tier 3). The person doing this is either the human operator or Claude with operator approval. Either way, the steps below are the exact contract — don't extrapolate.

## What this runbook covers

- Verifying that `/data/bot-data` is mounted and writable.
- Confirming the trader is actually writing there (not silently falling back to repo subdirs).
- Migrating an existing live VM from repo-resident data to the mount, **including the SQLite journal database** (`trade_journal.db`).
- Rolling back if anything goes wrong.
- What to monitor for the first 24 hours after a flip.

## Pre-conditions

- The OCI block volume has been created and attached to the instance. As of S-OCI (PRs #853–#909) this is automated end-to-end via [`.github/workflows/oci-storage.yml`](../../.github/workflows/oci-storage.yml) (`mode = execute`). Run [`docs/automation/oci-storage-setup.md`](../automation/oci-storage-setup.md) instead of doing the steps below by hand on a fresh VM; the workflow drives sections 1–7 with idempotent operations and a single `production-oci` approval gate.
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

## 3. Migrate runtime data (dry-run first)

```bash
cd /home/ubuntu/ict-trading-bot
./scripts/migrate_to_data_dir.sh                  # dry-run, prints what would copy
./scripts/migrate_to_data_dir.sh --execute        # actual copy
```

The migration is **non-destructive** — `rsync -a` preserves perms/timestamps and doesn't delete source files. The trader can keep running during the dry-run; it will not see the new copies because `DATA_DIR` isn't set yet.

This covers `runtime_logs/`, `runtime_state/`, `artifacts/`, and `data/`. It does **not** cover `trade_journal.db` — the SQLite file needs its own stop-and-copy routine (next step).

## 4. Migrate the journal DB

```bash
sudo /home/ubuntu/ict-trading-bot/scripts/migrate_journal_db.sh
```

This stops `ict-trader-live`, `ict-web-api`, and `ict-claude-bridge`,
`cp -p`s `trade_journal.db` + any `-wal` / `-shm` files to
`/data/bot-data/`, chowns to `ubuntu`, runs `PRAGMA integrity_check`,
and leaves the services stopped. The next step (drop-in install)
restarts them with `TRADE_JOURNAL_DB` set so they cold-start onto the
new path.

Idempotent: re-running when the destination is up to date is a no-op
(no service stop). On integrity failure the script restarts services
on the OLD path before exiting non-zero.

## 5. Install the systemd drop-in

```bash
for unit in ict-trader-live ict-web-api ict-claude-bridge; do
  sudo mkdir -p /etc/systemd/system/${unit}.service.d
  sudo cp /home/ubuntu/ict-trading-bot/deploy/dropins/data-dir.conf \
          /etc/systemd/system/${unit}.service.d/data-dir.conf
done
sudo systemctl daemon-reload
```

The drop-in adds four directives without modifying the base unit:

- `RequiresMountsFor=/data/bot-data` — won't start if the mount is down.
- `ExecStartPre=scripts/check_data_dir.sh` — preflight gates every restart.
- `Environment=DATA_DIR=/data/bot-data` — opts the trader's path helpers into the mount.
- `Environment=TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db` — pins the SQLite journal to the volume.

`ict-telegram-bot` is intentionally **not** in the loop — it has no
DATA_DIR-resident state. The verify workflow flags it as
`DATA_DIR=(unset)` and that is expected.

## 6. Restart the trader

```bash
for svc in ict-trader-live ict-web-api ict-claude-bridge; do
  sudo systemctl restart "$svc"
  state=$(systemctl is-active "$svc")
  echo "$svc: $state"
done
```

**Do NOT gate the restart on `systemctl is-enabled`** — that returns
exit 1 for "linked" units (those installed via `systemctl link <abs-path>`
rather than `enable`), which silently skips them. The automated
workflow gates on `systemctl list-unit-files` for the same reason
(see PR #909 for the regression that motivated this). The post-restart
`is-active` check above is the belt-and-braces; if any service prints
anything other than `active`, see `systemctl status` and `journalctl -u`
before proceeding.

Each restart fires `check_data_dir.sh` first; if the script fails, systemd holds the unit in `activating` and does not start the process. That's the safety net — better to be down for one tick than to write to the wrong filesystem.

## 7. Verify the trader is writing to the mount

```bash
# (a) Environment is correct.
systemctl show ict-trader-live | grep -E 'DATA_DIR|TRADE_JOURNAL_DB'
# Expected: both lines present, pointing under /data/bot-data.

# (b) Process holds files on the volume.
sudo lsof -p "$(systemctl show -p MainPID --value ict-trader-live)" \
    | grep -E '/data/bot-data' | head
# Expected: at least one runtime_logs / trade_journal.db handle there.

# (c) Heartbeat is being touched on the volume.
ls -la /data/bot-data/runtime_logs/heartbeat.txt
# Expected: mtime within the last ~60s.

# (d) DB is on the volume and growing.
ls -lh /data/bot-data/trade_journal.db
sqlite3 /data/bot-data/trade_journal.db 'PRAGMA integrity_check;'
```

If `lsof` shows the trader still holding the **old** `/home/ubuntu/ict-trading-bot/runtime_logs/` or `trade_journal.db` paths, the drop-in didn't take effect — `daemon-reload` was skipped, or there's a typo in `data-dir.conf`. Re-check, restart, repeat.

The one-shot equivalent of all of section 7 is `scripts/verify_storage_setup.sh` (or dispatching the `oci-storage-verify` workflow).

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

# 3. Confirm the trader is back to repo-relative paths AND the
#    repo-resident DB.
sudo lsof -p "$(systemctl show -p MainPID --value ict-trader-live)" \
    | grep -E 'runtime_logs|runtime_state|trade_journal' | head
# Expected: paths under /home/ubuntu/ict-trading-bot/, NOT /data/bot-data/.
```

Neither `migrate_to_data_dir.sh` nor `migrate_journal_db.sh` deletes
the source files, so the repo subdirs and the repo-resident DB still
contain the pre-migration state. The trader picks them up again as
soon as `DATA_DIR` and `TRADE_JOURNAL_DB` are unset.

The data that was written to `/data/bot-data/` during the brief flip-on window is not lost — it's still on the volume. Decide later whether to merge it back into the repo subdirs (rare) or keep it as a snapshot.

For the full automated rollback (including OCI detach) see
[`docs/automation/oci-storage-setup.md`](../automation/oci-storage-setup.md) § 4.

## What to monitor in the first 24 hours

- `runtime_logs/heartbeat.txt` mtime on the mount, every 60 s. Use `watch -n 30 ls -la /data/bot-data/runtime_logs/heartbeat.txt`.
- `journalctl -u ict-trader-live -f` for any path-resolution warnings ("paths: repo-relative ... not writable").
- Disk usage on the volume (`df -h /data/bot-data`) — sanity check that growth rate matches what the repo subdirs were doing.
- `/api/bot/health/latest` returns the snapshot bundle (proves `artifacts_dir()` is reading the right place).
- Hourly Telegram report fires at the top of the hour (proves `runtime_logs/outcomes.jsonl` aggregation works against the mount).
- The 6-hourly health-snapshot PR includes `=== STORAGE ===`, `=== DB ===`, and `=== AUDIT_LOG ===` sections; spot-check them for fstab persistence, DATA_DIR + TRADE_JOURNAL_DB drift across all three trader services, DB integrity, and audit log freshness.

## Common failure modes and quick fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `Job for ict-trader-live.service failed because the control process exited with error code.` | `ExecStartPre` failed — mount gone or perms wrong. | Run preflight manually; resolve before re-enabling. |
| `journalctl` shows `paths: repo-relative ... not writable; falling back to ~/.ict-trading-bot/...` | Trader running, but `DATA_DIR` unset and the repo subdir is read-only. | Set `DATA_DIR` or fix the repo perms; never let this be the steady state in prod. |
| Heartbeat in the mount is stale; repo heartbeat is fresh | Drop-in not installed or `daemon-reload` missed; trader still reading repo paths. | Re-run step 5 + 6, verify with step 7(a). |
| `lsof` shows trader holding `/home/ubuntu/.../trade_journal.db` after migration | `TRADE_JOURNAL_DB` env not set (drop-in installed pre-PR #898) | Re-install the drop-in from `deploy/dropins/data-dir.conf` (now includes the env line) and restart. |
| `ict-web-api` / `ict-claude-bridge` left `inactive` after an automated run | Earlier workflow gated on `systemctl is-enabled`, which returns exit 1 for "linked" units. Fixed in PR #909. | `sudo systemctl start ict-web-api ict-claude-bridge`; re-run `oci-storage execute` once the fix is on `main`. |
| Dashboard `/api/bot/health/latest` returns 404 | `artifacts/health/` not migrated. | Re-run migrate script; `latest.json` symlink may need recreating from `health_check_*.json`. |

## Cross-references

- [`docs/automation/oci-storage-setup.md`](../automation/oci-storage-setup.md) — the automated provisioning + verify pipeline (workflow-driven).
- [`docs/architecture/oci-block-storage.md`](../architecture/oci-block-storage.md) — why this exists.
- [`docs/security/permissions-tiers.md`](../security/permissions-tiers.md) — who can run which step.
- [`deploy/dropins/README.md`](../../deploy/dropins/README.md) — drop-in installation reference.
- [`scripts/check_data_dir.sh`](../../scripts/check_data_dir.sh), [`scripts/migrate_to_data_dir.sh`](../../scripts/migrate_to_data_dir.sh), [`scripts/migrate_journal_db.sh`](../../scripts/migrate_journal_db.sh), [`scripts/verify_storage_setup.sh`](../../scripts/verify_storage_setup.sh) — the tools this runbook drives.
