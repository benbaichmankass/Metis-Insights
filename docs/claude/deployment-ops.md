# Deployment ops

## Default stance

Do not deploy, restart, or live-trade unless explicitly asked.

## Before live changes

```bash
git status -sb
python scripts/secret_scan.py
PYTHONPATH=. pytest --collect-only -q tests
```

## Paper to live checklist

- Confirm `MODE`.
- Confirm `DRY_RUN`.
- Confirm `ALLOW_LIVE_TRADING`.
- Confirm exchange keys are environment variables.
- Confirm Telegram emergency stop works.

## VM reset / redeploy

> **WARNING:** A successful env render is *not* a green light to reset or redeploy the VM.
> A VM reset/redeploy requires a **separate audit** — running processes, in-flight orders,
> log/state retention, deployment artifacts, and on-host config must all be reviewed
> manually first. Do **not** assume any tool, notebook, or script has performed that audit
> on your behalf. There is no auto-reset path.

The `notebooks/setup/test_vwap_env_and_vm_readiness.ipynb` notebook only checks that
the VWAP env renders and that safety flags are correct. It does not call Bybit, place
orders, SSH, or restart the VM.

## VWAP BTCUSD profiles

Two profiles target the Bybit `vwap_strategy` subaccount:

- `vwap_btcusd_dry_run` — `MODE=PAPER`, `DRY_RUN=true`, `ALLOW_LIVE_TRADING=false`,
  uses live Bybit endpoint keys but never places orders. Default for VM dry-runs.
- `vwap_btcusd_live` — `MODE=LIVE`, `DRY_RUN=false`, `ALLOW_LIVE_TRADING=true`.
  Requires `--allow-live` on the renderer CLI.

Both pull credentials from `bybit.vwap_strategy.api_key` / `api_secret` in the master
secrets file.

## VWAP strategy runtime status (as of 2026-04-27)

`STRATEGY=vwap` is implemented and wired into the pipeline:

- Signal builder: `strategies/vwap_signal_builder.py` (pure VWAP mean-reversion,
  offline-safe, no ML dependency).
- Pipeline routing: `src/runtime/pipeline.py` dispatches to `vwap_signal_builder`
  when `STRATEGY=vwap`.
- Safety gates: `DRY_RUN=true` prevents order placement inside `safe_place_order`.
  `ALLOW_LIVE_TRADING=false` provides a second block. `MODE=LIVE` without
  `ALLOW_LIVE_TRADING=true` is rejected by `validate_startup`.

**`vwap_btcusd_dry_run` is intended for safe runtime testing only.**
- It must not and cannot place live orders (DRY_RUN=true + ALLOW_LIVE_TRADING=false).
- BYBIT_TESTNET=false in this profile is safe because orders never reach the exchange.
- VM reset is not approved until env rendering, tests, and VM readiness audit are green.

**`vwap_btcusd_live` is not approved for use yet.**
- Do not render or deploy this profile without a full audit and explicit user approval.
- Requires `--allow-live` on the renderer CLI, `DRY_RUN=false`, and `ALLOW_LIVE_TRADING=true`.

## deploy_pull_restart.sh — VM deploy script

### Why no venv on the VM

The live trader is invoked directly by systemd as `/usr/bin/python3 -u -B -m src.main`.
There is no virtualenv on the production VM. Attempting `source .venv/bin/activate`
fails with "No such file or directory". The deploy script uses `/usr/bin/python3 -m pip`
to match the live runtime.

### Services restarted (in order)

1. `ict-trader-live.service` — the live ICT trader
2. `ict-telegram-bot.service` — Telegram notification bot

**Not restarted:**
- `ict-vwap-dry-run.service` — intentionally stopped, pending sprint completion
- `ict-git-sync.service` — this is the service running the script itself
- `ict-bot.service` — stale unit file; not the live trader

### No-op fast path

If `git pull` reports "Already up to date.", the script logs
`">>> No new commits. Skipping deploy."` and exits 0 without running
`pip install` or restarting any services. This prevents unnecessary
churn on polling triggers with nothing to deploy.

### sudo detection logic

At startup the script picks the right invocation once and stores it in a
bash array `SYSTEMCTL`:

```
if running as root    → SYSTEMCTL=(systemctl)
elif sudo -n works    → SYSTEMCTL=(sudo systemctl)
else                  → print error + exit 1
```

All `systemctl` calls in the script use `"${SYSTEMCTL[@]}"` so the
detection is applied consistently. If neither path is available, the
script exits 1 with a clear message pointing to the required sudoers line:

```
ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl
```

## Auto-deploy schedule

`ict-git-sync.service` is driven by `ict-git-sync.timer` (added in the
"Add systemd timer for ict-git-sync.service" PR). The timer triggers the
service every 5 minutes with a 30-second randomised jitter and a 2-minute
boot delay so the system settles before the first pull.

### Install the timer on the VM

```bash
sudo cp /home/ubuntu/ict-trading-bot/deploy/ict-git-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ict-git-sync.timer
```

### Inspect

```bash
# Confirm next trigger time
systemctl list-timers --all | grep git-sync

# Recent service log
journalctl -u ict-git-sync.service --since "1 hour ago"
```

### Pause auto-deploy temporarily

```bash
sudo systemctl stop ict-git-sync.timer
```

### Resume

```bash
sudo systemctl start ict-git-sync.timer
```

### Legacy cron note

A zombie cron entry `* * * * * /home/ubuntu/ict-trading-bot/scripts/auto_update.sh`
was found and removed from the VM in April 2026. The script it referenced
(`auto_update.sh`) never existed in the repo. The systemd timer supersedes
that intent entirely — do not recreate the cron entry.

## Recovery from broken git-sync

**Symptom:** `ict-git-sync.service` shows `ActiveState=failed` or a restart counter in the thousands
(`systemctl status ict-git-sync.service`), and the live trader has fallen behind `origin/main`.

**Root cause (April 2026):** The live unit's `ExecStart` pointed to
`/home/ubuntu/ict-trading-bot/deploy_git_sync.sh`, which never existed.
The correct script is `scripts/deploy_pull_restart.sh`.
The unit file was also absent from the repo entirely; it has now been added at
`deploy/ict-git-sync.service`. The service type was later changed to `Type=oneshot`
(removing the earlier `Restart=on-failure`/`RestartSec=60`) when the systemd timer
was introduced — the timer handles re-runs, so service-level restart is redundant.

**To redeploy the fixed unit on the VM:**

```bash
# 1. Pull the fix (if the trader is far behind, do a manual pull first)
cd /home/ubuntu/ict-trading-bot
git pull origin main

# 2. Install the corrected unit file and timer
sudo cp /home/ubuntu/ict-trading-bot/deploy/ict-git-sync.service /etc/systemd/system/
sudo cp /home/ubuntu/ict-trading-bot/deploy/ict-git-sync.timer /etc/systemd/system/

# 3. Reload systemd and enable the timer (the timer starts the service)
sudo systemctl daemon-reload
sudo systemctl enable --now ict-git-sync.timer

# 4. Confirm both are active
sudo systemctl status ict-git-sync.timer
systemctl list-timers --all | grep git-sync
```

**Enable the timer, not the service directly.** `ict-git-sync.service` is started
on demand by the timer. Do not `enable` the service unit itself — doing so would run
the deploy script at every boot before the timer's `OnBootSec=2min` delay applies.
