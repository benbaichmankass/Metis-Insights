# OCI Block Storage Setup — Operator Runbook

Automated provisioning of the 100 GB `ict-bot-data-vol` block volume for the
live trading VM (create → attach → format → mount → migrate runtime data
→ migrate `trade_journal.db` → systemd drop-ins → restart). The operator
never opens the OCI console; mutating steps are gated by a single
`production-oci` environment approval, and the state of the mount + DB
is surfaced in every 6-hourly health snapshot.

This runbook documents the **mature, post-sprint** state. Sprint history
in PRs #853, #854, #857, #866, #872, #875, #886, then the data-layer
extension in #892, #898, #902, #909.

- **Mutating workflow:** `.github/workflows/oci-storage.yml`
  (`workflow_dispatch`, `mode = dry-run | execute`)
- **Verify workflow:** `.github/workflows/oci-storage-verify.yml`
  (`workflow_dispatch`, no approval gate, posts a labelled issue)
- **Scheduled health:** `=== STORAGE ===` + `=== DB ===` +
  `=== AUDIT_LOG ===` sections in `scripts/collect_health_snapshot.sh`
  — included in every `health-snapshot-pr.yml` run (cron `0 */6 * * *`)
  and surfaced to the `/health-review` layer-2 skill (which grades the
  new `db_integrity` and `audit_log_freshness` dimensions).
- **Helper scripts:** `scripts/oci_volume_status.sh`,
  `oci_create_volume.sh`, `oci_attach_volume.sh`, `oci_vm_ssh.sh`,
  `verify_storage_setup.sh`. All accept `--dry-run`.
- **Migration scripts:** `scripts/migrate_to_data_dir.sh` (runtime
  dirs; safe while trader is running), `scripts/migrate_journal_db.sh`
  (the SQLite journal; stops services, copies, integrity-checks).
- **Reused:** `scripts/check_data_dir.sh`, `deploy/dropins/data-dir.conf`.

## 1. One-time prerequisites

### Secrets

Two scopes (intentional split):

| Secret | Scope | Used by | Notes |
|---|---|---|---|
| `OCI_CLI_USER` | repo | `oci-storage` | user OCID |
| `OCI_CLI_TENANCY` | repo | `oci-storage` | tenancy OCID; also reused as compartment id |
| `OCI_CLI_REGION` | repo | `oci-storage` | `eu-paris-1` |
| `OCI_CLI_FINGERPRINT` | repo | `oci-storage` | OCI API signing key fingerprint |
| `OCI_CLI_KEY_CONTENT` | repo | `oci-storage` | PEM body of the OCI API signing key |
| `VM_SSH_KEY` | repo | `oci-storage-verify`, `health-snapshot-pr`, every read-only VM SSH workflow | OpenSSH private key for `ubuntu@141.145.193.91`. Same value as `VM_SSH_PRIVATE_KEY` below; both are present because they're keyed differently in different workflows. Stripping CRs handled in-workflow. |
| `VM_SSH_PRIVATE_KEY` | environment `production-oci` | `oci-storage` (mutating job only) | Same key material as `VM_SSH_KEY`. Lives on the environment so it inherits the approval gate. |

The duplication is deliberate: read-only verification should be one-click,
but the mutating job must require a human approval before any OCI / VM
mutation. Two secrets, two scopes — one approval gate.

### Environment protection

Settings → Environments → `production-oci` →

- **Required reviewers:** add the operator (otherwise the mutating job
  runs unattended, which is the bug we are guarding against).
- **Deployment branches:** `main` (extend to other branches only for
  ad-hoc test runs).

Only `oci-storage.yml`'s `storage-setup` job references the
`production-oci` environment. `preflight` (read-only) and the entire
`oci-storage-verify.yml` workflow are environment-less by design.

## 2. Triggering the workflows

### Provision (mutating)

Actions → **oci-storage** → Run workflow → choose:

- `mode = dry-run` (default) — prints every planned command without
  touching OCI or the VM. Read-only OCI lookups still run so the
  printed commands are populated with real OCIDs.
- `mode = execute` — actually performs the create / attach / mount /
  migrate / DB-migrate / drop-in install / restart. Requires
  `production-oci` approval. Idempotent: safe to re-run.

```bash
# Optional, from a local gh CLI:
gh workflow run oci-storage.yml -f mode=dry-run
gh workflow run oci-storage.yml -f mode=execute -f volume_size_gb=100
```

### Verify (read-only, no approval)

Actions → **oci-storage-verify** → Run workflow → green button.

The workflow SSHes to the VM, runs the storage health checks, posts the
full report as a GitHub Issue labelled `oci-verify`, and ALSO writes the
same report to the run-summary panel. Use the issue when a Claude
session needs the contents without scraping the run page; use the run
summary when an operator is looking from the Actions UI.

There is no approval gate on this workflow — read-only checks shouldn't
pause for review. Anyone who can dispatch the workflow gets the report.

### Scheduled (every 6h, automatic)

No dispatch needed. `health-snapshot-pr.yml` runs on cron and its
VM-side collector now includes `=== STORAGE ===`, `=== DB ===`, and
`=== AUDIT_LOG ===` sections. The resulting snapshot lands on the
`auto/health-check-review` PR and the `/health-review` skill picks it
up automatically. The skill grades `db_integrity` (PRAGMA result, row
totals, mtime age, WAL size) and `audit_log_freshness` (mtime age and
events-in-last-hour, with a heartbeat cross-check).

## 3. Step-by-step (mutating workflow)

| Step | Mutates? | Notes |
|---|---|---|
| `preflight` job | no | Lists OCI volumes; no env gate. |
| `Install OCI CLI` (storage-setup) | no | `pip install oci-cli` on the runner. Auth via `OCI_CLI_*` env vars (no `~/.oci/config`). |
| `Install VM SSH key` | no | Reads `VM_SSH_PRIVATE_KEY` from `production-oci`, writes to `~/.ssh/`. |
| `Create volume` | yes (OCI) | `dry-run`: prints; `execute`: creates if absent, returns existing OCID if present. |
| `Attach volume` | yes (OCI) | Uses `VM_INSTANCE_OCID` (hard-coded in workflow env). Idempotent. |
| `Probe VM connectivity` | no | `ssh ... 'uname -a && lsblk'` to confirm reachability. Runs in both modes. |
| `Sync VM repo to current main` | yes (VM, fast-forward only) | `git fetch + reset --hard origin/main` so all VM-side script invocations that follow see the same code as this workflow file. Prevents the race against the slower `ict-git-sync.timer` cadence. |
| `Format + mount + fstab` | yes (VM) | Only in `execute` mode. `mkfs.ext4` skipped if `blkid` reports an existing FS; `mount` skipped if already mounted; `fstab` line only appended if the UUID isn't already there. |
| `Repo migration` | yes (VM) | Only in `execute` mode. Calls `scripts/migrate_to_data_dir.sh --execute` on the VM (rsync, non-destructive, runtime-safe). |
| `Migrate journal DB` | yes (VM) | Only in `execute` mode. Calls `scripts/migrate_journal_db.sh`: stops the three trader services, `cp -p` of `trade_journal.db` (and `-wal` / `-shm` if present) to `/data/bot-data/`, chowns, runs `PRAGMA integrity_check`. Idempotent (no-op when destination is newer). On integrity failure, restarts services on the OLD path so the trader stays alive. |
| `Install systemd drop-ins` | yes (VM) | Only in `execute` mode. Copies `deploy/dropins/data-dir.conf` to `/etc/systemd/system/<svc>.service.d/` for `ict-trader-live`, `ict-web-api`, `ict-claude-bridge`. `daemon-reload` + `restart`. Gates on `systemctl list-unit-files` (NOT `is-enabled`) so "linked" units get restarted too. Belt-and-braces: post-restart, fails the workflow if any service isn't `active`, dumping `systemctl status` + `journalctl -u`. `ict-telegram-bot` is intentionally omitted (no DATA_DIR-resident state). |
| `Verify` | no | Only in `execute` mode. Calls `scripts/verify_storage_setup.sh` over SSH. |
| `Summary` | no | Writes a per-run summary to `$GITHUB_STEP_SUMMARY`. |

Approval fires **once** per run and covers every mutating step that
follows. If you want finer control, run `dry-run` first, read the
printed commands, then re-dispatch with `execute`.

All helper scripts are idempotent and safe to retry. A re-run of
`execute` against an already-provisioned VM is a no-op for the volume
and mount steps, copies only deltas via rsync, and skips the journal
DB copy entirely if the destination is up to date.

### What `migrate_journal_db.sh` does

1. Idempotency check: if `/data/bot-data/trade_journal.db` mtime ≥ the
   source's, exit 0 immediately (no service stop, no copy).
2. Stop `ict-trader-live`, `ict-web-api`, `ict-claude-bridge` (SQLite
   can't be safely copied with open writers).
3. `cp -p` source + `-wal` + `-shm` (whatever exists) to the
   destination on `/data/bot-data`.
4. `chown ubuntu:ubuntu` so the trader can write.
5. `sqlite3 dest 'PRAGMA integrity_check;'`. If anything other than
   `ok`, restart services on the OLD path (trader stays alive) and
   exit non-zero.
6. Leave services STOPPED on success — the workflow's next step
   installs the updated drop-in (with `TRADE_JOURNAL_DB` env) and
   `systemctl restart` performs the clean cold-start onto the new
   path.

Source files are NOT renamed or deleted. Rollback (§ 4) just removes
the drop-in; the trader falls back to the repo-resident DB which still
has the pre-migration state.

## 4. Rollback

The data on the volume is preserved across each rollback step;
`migrate_to_data_dir.sh` is non-destructive (rsync only, no deletes
from the repo subdirs), and `migrate_journal_db.sh` also leaves the
source DB in place. So backing out is fast.

1. Stop the trader so it stops writing under `/data/bot-data`:
   ```bash
   sudo systemctl stop ict-trader-live ict-web-api ict-claude-bridge
   ```
2. Remove the systemd drop-ins (returns services to repo-relative
   paths AND the repo-resident DB):
   ```bash
   for svc in ict-trader-live ict-web-api ict-claude-bridge; do
     sudo rm -f "/etc/systemd/system/${svc}.service.d/data-dir.conf"
   done
   sudo systemctl daemon-reload
   ```
3. Unmount and remove the fstab entry:
   ```bash
   sudo umount /data || true
   sudo sed -i '/\s\/data\s/d' /etc/fstab
   ```
4. Restart services on repo-resident data:
   ```bash
   sudo systemctl start ict-trader-live ict-web-api ict-claude-bridge
   ```
5. Detach in OCI (idempotent — safe to retry):
   ```bash
   COMP=$OCI_CLI_TENANCY
   VOL=$(oci bv volume list --compartment-id "$COMP" --region eu-paris-1 \
     --lifecycle-state AVAILABLE \
     --query "data[?\"display-name\"=='ict-bot-data-vol'].id | [0]" --raw-output)
   ATT=$(oci compute volume-attachment list --compartment-id "$COMP" \
     --region eu-paris-1 --volume-id "$VOL" \
     --query "data[?\"lifecycle-state\"=='ATTACHED'].id | [0]" --raw-output)
   oci compute volume-attachment detach --volume-attachment-id "$ATT" --force
   ```
6. Optional — delete the volume (destructive; data lost):
   ```bash
   oci bv volume delete --volume-id "$VOL" --force
   ```

Steps 1–4 are a complete rollback for live traffic — the DB is
automatically picked up from the repo path because `TRADE_JOURNAL_DB`
is no longer set. Any writes made since the migration land in
`/data/bot-data/trade_journal.db` (still on the volume, snapshot-only).
Steps 5–6 release the OCI resources.

## 5. Monitoring after setup

Nothing operator-facing is required — the storage health rides along with
the normal health-check loop:

- **Every 6h:** the next scheduled `health-snapshot-pr.yml` run includes
  `=== STORAGE ===`, `=== DB ===`, and `=== AUDIT_LOG ===` blocks in
  `artifacts/health/health_snapshot.txt`. The `/health-review` skill
  grades them as `db_integrity` and `audit_log_freshness` findings.
  Specific signals that escalate to `concern`:
  - DB `integrity_check` returns anything other than `ok`.
  - DB mtime > 1h while the audit log shows recent ticks.
  - Audit log `events_last_hour == 0` while the heartbeat is fresh.
- **On demand:** dispatch `oci-storage-verify` for an immediate snapshot;
  the report lands on a labelled GitHub Issue you (or Claude) can
  fetch programmatically.
- **Service preflight:** every `systemctl restart ict-trader-live`
  (and friends) runs `scripts/check_data_dir.sh` via
  `ExecStartPre` — a missing mount or a permissions regression keeps
  the unit in `activating` instead of writing to the wrong filesystem.

Quick spot-check from a local shell with the VM SSH key:

```bash
ssh ubuntu@141.145.193.91 'df -h /data && systemctl show -p Environment --value ict-trader-live | tr " " "\n" | grep -E "DATA_DIR|TRADE_JOURNAL_DB"'
```

## 6. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `oci-storage-verify` errors `VM_SSH_KEY secret is empty` | repo-scope secret missing or named differently | Verify Settings → Secrets → Actions has `VM_SSH_KEY` exactly. |
| `oci-storage` (mutating) errors at `Install VM SSH key` | `VM_SSH_PRIVATE_KEY` not added to the `production-oci` environment | Settings → Environments → `production-oci` → Add secret. |
| Workflow fails at a VM-side step with `No such file or directory` for a script that exists in the repo | `ict-git-sync.timer` hadn't pulled yet — the new "Sync VM repo to current main" step should make this impossible going forward. If it persists, the VM repo's remote is mis-configured. | Check `git remote -v` in `/home/ubuntu/ict-trading-bot`. |
| Attach step says `no AVAILABLE volume` | create step did not finish | Re-run — idempotent. |
| Attach step says `no RUNNING instance` | `VM_INSTANCE_OCID` in the workflow env is wrong | Fix the OCID in `.github/workflows/oci-storage.yml` and re-run. |
| `Format + mount` fails with `no candidate block device found` | OCI attached via iSCSI; kernel needs `oci-iscsi-config` | Follow `docs/runbooks/iscsi-attach.md` (if applicable) before re-running. |
| `Migrate journal DB` exits 4 with `integrity_check failed` | DB on the source was already corrupt, or filesystem ran out of space mid-copy | Investigate source DB first (`sqlite3 $REPO_DIR/trade_journal.db 'PRAGMA integrity_check;'`); workflow has already restarted services on the OLD path so the trader is alive. |
| `Install systemd drop-ins` exits 5 with `$svc is inactive after restart attempt` | Drop-in installed but the unit refused to come up. Workflow dumped `systemctl status` and `journalctl -u $svc -n 50` immediately above the failure. | Read the dumped logs; most common cause is `ExecStartPre=check_data_dir.sh` failing (the mount or perms regressed). |
| `Verify` warns `DATA_DIR=(unset)` for `ict-telegram-bot` | Expected. The telegram bot intentionally has no drop-in. | None. |
| `Verify` warns `$svc has DATA_DIR but no TRADE_JOURNAL_DB` | Drop-in installed pre-#898 (data-layer extension). | Re-run `mode=execute` to reinstall the updated drop-in. |
| `migrate_to_data_dir.sh` says `target /data/bot-data does not exist` | mount step was skipped (likely `dry-run`) | Re-run in `mode=execute`. |
| `/health-review` flags `db_integrity` as `concern` with mtime age ≫ 1h | Trader has stopped writing to the DB — either a service is down or the resolver isn't honouring `TRADE_JOURNAL_DB`. | Cross-check `systemctl is-active ict-trader-live` and `lsof -p <pid> | grep trade_journal.db` to confirm the process is holding the right file. |
