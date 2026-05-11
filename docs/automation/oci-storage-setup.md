# OCI Block Storage Setup — Operator Runbook

Fully automated provisioning of the 100 GB `ict-bot-data-vol` block volume
for the live trading VM, including format/mount and repo migration to
`/data/bot-data`. Everything mutating is gated by GitHub environment
approval; the operator never opens the OCI console.

- Workflow: `.github/workflows/oci-storage.yml`
- Scripts: `scripts/oci_volume_status.sh`, `oci_create_volume.sh`,
  `oci_attach_volume.sh`, `oci_vm_ssh.sh`, `verify_storage_setup.sh`
- Reused: `scripts/migrate_to_data_dir.sh`,
  `scripts/check_data_dir.sh`, `deploy/dropins/data-dir.conf`

## 1. One-time prerequisites

### Secrets (already configured unless noted)

GitHub repository secrets:

| Secret | Status | Notes |
|---|---|---|
| `OCI_CLI_USER` | existing | user OCID |
| `OCI_CLI_TENANCY` | existing | tenancy OCID; also reused as compartment id |
| `OCI_CLI_REGION` | existing | should be `eu-paris-1` |
| `OCI_CLI_FINGERPRINT` | existing | key fingerprint |
| `OCI_CLI_KEY_CONTENT` | existing | PEM body of the API signing key |
| `VM_SSH_PRIVATE_KEY` | **add once** | contents of `ict-bot-ovm-private.key` |

Add `VM_SSH_PRIVATE_KEY` to the **production-oci** environment (not the
repository scope) so it inherits the same manual-approval gate as the
mutating job. Settings → Environments → `production-oci` → Add secret.

### Environment protection

Create the environment if it does not exist:

Settings → Environments → New environment → `production-oci` →
  - Required reviewers: add the operator(s).
  - Deployment branches: only `claude/automate-oci-storage-Yplx9`
    and `main` (extend as needed).

The `preflight` job runs without the environment; the `storage-setup`
job will pause until a reviewer approves.

## 2. Triggering the workflow

### Web UI

Actions → **oci-storage** → Run workflow → choose:
  - `mode = dry-run` (default) — shows every command without touching OCI/VM.
  - `mode = execute` — runs create/attach/mount/migrate after approval.

### GitHub CLI

```bash
gh workflow run oci-storage.yml -f mode=dry-run
gh workflow run oci-storage.yml -f mode=execute -f volume_size_gb=100
```

### Telegram `/storage-setup`

The Telegram bot dispatches the workflow via the GitHub API. Add a
handler that posts to:

```
POST /repos/benbaichmankass/ict-trading-bot/actions/workflows/oci-storage.yml/dispatches
body: { "ref": "main", "inputs": { "mode": "dry-run" } }
```

The handler must accept only the configured operator chat id and forward
the run URL back to the chat. Reference implementation lives in
`automation/` (see existing operator-actions wiring for the pattern).

## 3. What each step does

| Step | Mutates? | Gate |
|---|---|---|
| `preflight` job | no | none |
| `Create volume` | yes (OCI) | `production-oci` approval |
| `Attach volume` | yes (OCI) | same approval |
| `Format + mount + fstab` | yes (VM) | same approval; only runs in `execute` mode |
| `Repo migration` | yes (VM) | same approval; calls `scripts/migrate_to_data_dir.sh --execute` |
| `Install systemd drop-ins` | yes (VM) | same approval; copies `deploy/dropins/data-dir.conf` |
| `Verify` | no | same approval; runs `scripts/verify_storage_setup.sh` |

The approval prompt fires **once** per run and covers every mutating
step that follows. There is no per-step approval in GitHub Actions —
the operator approves the job, not each command. Operators who want
finer control should run `mode=dry-run` first, inspect the planned
commands, then re-trigger with `mode=execute`.

Idempotency:
  - `oci_create_volume.sh` returns the existing volume's OCID if
    `ict-bot-data-vol` already exists.
  - `oci_attach_volume.sh` is a no-op if the volume is already
    attached to the instance.
  - `mkfs.ext4` only runs if `blkid` reports no filesystem.
  - `mount` only runs if `/data` is not already a mountpoint.
  - `fstab` only appends if the UUID is not already present.
  - `migrate_to_data_dir.sh` uses `rsync -a`; re-runs only copy deltas.

## 4. Rollback

1. Stop the trader so it stops writing under `/data/bot-data`:
   ```bash
   sudo systemctl stop ict-trader-live ict-web-api ict-claude-bridge
   ```
2. Remove the systemd drop-ins (returns services to repo-relative paths):
   ```bash
   sudo rm -f /etc/systemd/system/ict-trader-live.service.d/data-dir.conf
   sudo rm -f /etc/systemd/system/ict-web-api.service.d/data-dir.conf
   sudo rm -f /etc/systemd/system/ict-claude-bridge.service.d/data-dir.conf
   sudo systemctl daemon-reload
   ```
3. Unmount and remove the fstab entry:
   ```bash
   sudo umount /data || true
   sudo sed -i '/\sUUID=.*\s\/data\s/d' /etc/fstab
   ```
4. Restart services on repo-resident data:
   ```bash
   sudo systemctl start ict-trader-live ict-web-api
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

The `migrate_to_data_dir.sh` script does not delete the repo-resident
sources, so steps 1–4 alone are a complete rollback for live traffic.

## 5. Monitoring after setup

- Confirm DATA_DIR is read by the trader:
  `ssh ubuntu@$VM_HOST 'cd /home/ubuntu/ict-trading-bot && ./scripts/print_runtime_profile.py'`
- Run the layer-2 health review (`/health-review`); the snapshot under
  `artifacts/health/latest.json` will now resolve to `/data/bot-data`.
- `df -h /data` should report ≥ 99 GB and grow slowly with the journal.
- `journalctl -u ict-trader-live -n 200 --no-pager` should show no
  `check_data_dir.sh` preflight failures.

## 6. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `preflight` step fails with `--auth not authorized` | `OCI_CLI_*` secret missing or wrong fingerprint | re-upload the signing key, then re-run |
| `Attach volume` step says `no AVAILABLE volume` | create step did not complete; volume in `PROVISIONING` | re-run the workflow — the step is idempotent |
| `Format + mount` step fails with `no candidate block device found` | OCI attached via iSCSI; the kernel needs `oci-iscsi-config` | follow `docs/runbooks/iscsi-attach.md` (existing) to attach iSCSI before re-running |
| `Verify` warns `DATA_DIR=... missing` | drop-in not yet active because service unit not installed | run `scripts/install_systemd_units.sh` on the VM, then re-run the workflow |
| `migrate_to_data_dir.sh` says `target /data/bot-data does not exist` | mount step skipped (likely `dry-run` mode) | re-run in `mode=execute` |
