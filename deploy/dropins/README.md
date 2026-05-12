# systemd drop-ins for OCI block-storage externalization

Drop-in files override or extend an existing systemd unit without
editing the unit file itself. We use them here because the rule in
`CLAUDE.md` is **never merge a PR that modifies a `.service` file the
live VM consumes** — drop-ins are a parallel, additive layer.

## What's here

| File | Target service | Purpose |
|---|---|---|
| [`data-dir.conf`](./data-dir.conf) | `ict-trader-live`, `ict-web-api`, `ict-claude-bridge` | Binds to `/data/bot-data` mount: `RequiresMountsFor`, `ExecStartPre=check_data_dir.sh`, `Environment=DATA_DIR=...`. Installed manually (one-time). |
| [`watchdog-data-dir.conf`](./watchdog-data-dir.conf) | `ict-liveness-watchdog` | Minimal `Environment=DATA_DIR=/data/bot-data` only — no mount guard since the watchdog should alert, not block, when the mount is absent. **Auto-installed by `scripts/install_systemd_units.sh` on every pull-and-deploy.** |

## How to install data-dir.conf (one-time, per service)

For each service that should read/write to the mount (`ict-trader-live`,
`ict-web-api`, `ict-claude-bridge`):

```bash
sudo mkdir -p /etc/systemd/system/ict-trader-live.service.d
sudo cp /home/ubuntu/ict-trading-bot/deploy/dropins/data-dir.conf \
        /etc/systemd/system/ict-trader-live.service.d/data-dir.conf
sudo systemctl daemon-reload
sudo systemctl restart ict-trader-live
```

Repeat for `ict-web-api` and `ict-claude-bridge`.

## How watchdog-data-dir.conf gets installed

`scripts/install_systemd_units.sh` (called by every `pull-and-deploy`)
automatically installs `watchdog-data-dir.conf` to
`/etc/systemd/system/ict-liveness-watchdog.service.d/data-dir.conf`
if missing or changed. No manual step required. No service restart
needed — the watchdog is a oneshot fired by its timer; the next timer
tick (≤60 s) picks up the new environment.

## How to undo

```bash
sudo rm /etc/systemd/system/ict-trader-live.service.d/data-dir.conf
sudo systemctl daemon-reload
sudo systemctl restart ict-trader-live
```

The base `.service` files are unchanged, so removing the drop-in
fully reverts to repo-relative paths.

## Verifying it took effect

```bash
# Resolved unit shows the merged config.
systemctl cat ict-trader-live.service

# DATA_DIR is set in the service's environment.
systemctl show ict-trader-live.service | grep DATA_DIR

# Verify watchdog drop-in is installed and active.
systemctl cat ict-liveness-watchdog.service | grep DATA_DIR
systemctl show ict-liveness-watchdog.service | grep DATA_DIR

# Process is reading from the mount.
sudo lsof -p "$(systemctl show -p MainPID --value ict-trader-live)" \
    | grep -E '/data/bot-data|runtime_logs|runtime_state'
```

## Pre-flight check

Before installing the drop-in, run the preflight by hand:

```bash
sudo -u ubuntu /home/ubuntu/ict-trading-bot/scripts/check_data_dir.sh /data/bot-data
```

It exits 0 if the mount exists, is writable, and the four subdirs
(`data/`, `runtime_logs/`, `runtime_state/`, `artifacts/`) are present
or creatable. Non-zero means **do not install the drop-in yet** — the
service would fail to start.

## Migration order on a live host

1. Mount the OCI block volume at `/data/bot-data` and confirm with
   `mountpoint /data/bot-data`.
2. `./scripts/check_data_dir.sh` — preflight passes.
3. `./scripts/migrate_to_data_dir.sh` — dry-run; review output.
4. `./scripts/migrate_to_data_dir.sh --execute` — copy data.
5. Install the drop-in for each unit (commands above).
6. `systemctl restart ict-trader-live ict-web-api ict-claude-bridge`.
7. Watch `runtime_logs/heartbeat.txt` mtime tick under
   `/data/bot-data/runtime_logs/heartbeat.txt` for ≥2 minutes.
8. Tail `journalctl -u ict-trader-live` for warnings about path
   resolution.

If anything looks wrong, removing the drop-in and reloading reverts
the service to repo-relative paths — no data is lost because the
migration script only copies; it doesn't delete source files.
