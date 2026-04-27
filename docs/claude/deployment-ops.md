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

## Recovery from broken git-sync

**Symptom:** `ict-git-sync.service` shows `ActiveState=failed` or a restart counter in the thousands
(`systemctl status ict-git-sync.service`), and the live trader has fallen behind `origin/main`.

**Root cause (April 2026):** The live unit's `ExecStart` pointed to
`/home/ubuntu/ict-trading-bot/deploy_git_sync.sh`, which never existed.
The correct script is `scripts/deploy_pull_restart.sh`.
The unit file was also absent from the repo entirely; it has now been added at
`deploy/ict-git-sync.service` with `Restart=on-failure` and `RestartSec=60` to prevent
future restart-counter explosions.

**To redeploy the fixed unit on the VM:**

```bash
# 1. Pull the fix (if the trader is far behind, do a manual pull first)
cd /home/ubuntu/ict-trading-bot
git pull origin main

# 2. Install the corrected unit file
sudo cp /home/ubuntu/ict-trading-bot/deploy/ict-git-sync.service /etc/systemd/system/

# 3. Reload systemd and restart the service
sudo systemctl daemon-reload
sudo systemctl restart ict-git-sync.service

# 4. Confirm it started cleanly
sudo systemctl status ict-git-sync.service
```

**Do NOT** run `systemctl enable ict-git-sync.service` unless you intend it to start on every
boot — the deploy script restarts `ict-trader-live.service` and `ict-telegram-bot.service`, so
an unexpected boot-time run could interrupt a live session.
