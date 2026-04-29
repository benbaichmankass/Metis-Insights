# Deployment ops

## Default stance

Do not deploy, restart, or live-trade unless explicitly asked.

## Canonical entrypoint (S-012)

The live trader is launched by systemd unit `ict-trader-live.service`,
which runs `python3 -u -B -m src.main`. **There are no other live trader
entrypoints.** Manual launches go through the same module:
`PYTHONPATH=. python3 -m src.main`.

The Telegram bot is launched by `ict-telegram-bot.service` running
`python3 -u -B -m src.bot.telegram_query_bot`.

S-012 PR C5 / C6 removed the legacy alternatives:

| Removed | Replacement |
|---|---|
| `src/core/automated_trading_loop.py` (orphan module) | `src/main.py` |
| `run_trader.sh` (called the orphan) | `systemctl start ict-trader-live` (or `python -m src.main` directly) |
| `check_bots.sh` (greped for the orphan name) | rewritten to grep `src.main` and `src.bot.telegram_query_bot` |
| `strategies/` directory and `src/runtime/strategies/` | `src/units/strategies/` (canonical) |
| `src/strategies_manager.py` (in-memory dict) | `src/strategy_registry.py` (YAML-driven) |

Single-process architecture (PM § 8 #1): every strategy in the active
roster (`turtle_soup`, `vwap`) runs inside the same `ict-trader-live`
process, dispatched by `src/runtime/pipeline.py::multiplexed_signal_builder`
through `src/core/coordinator.py::Coordinator.strategy_order_pkg`.

Per-strategy systemd units do **not** exist and must not be re-introduced
without an explicit sprint to author the unit files and refactor the
dispatcher. The `service:` field has been dropped from
`config/strategies.yaml` and `config/units.yaml`; the registry defaults
any missing service entry to `ict-trader-live`.

## /accounts dry/live toggle (S-012)

PM decision § 8 #4 confirmed: the per-account dry/live toggle introduced
in S-011 PR #141 stays. It is the staging escape hatch for prop-account
configuration and account-by-account go-live promotion.

### Defaults

* **Every newly-loaded account starts in dry-run mode.**
  `src/units/accounts/account.py::TradingAccount.__init__` sets
  `dry_run=True` by default. Loading `config/accounts.yaml` does not
  flip any account to live.
* **Live trading is opt-in, per account, and persistent across reloads.**
  Flipping requires the operator to issue an explicit Telegram
  `/accounts dry|live <account_id>` command (S-011 PR #141), which
  records the override in the in-process `_DRY_RUN_OVERRIDES` dict and
  re-applies it on every `load_accounts()` call.

### Operator workflow

1. Edit `config/accounts.yaml` to declare the account (api_key_env,
   risk caps, `strategies: [turtle_soup, vwap]`).
2. `git add` + `git commit` + `git push`. Pull on the VM and reload —
   the new account loads in **dry-run** by default.
3. Telegram `/accounts_status` confirms the account is present and
   shows `dry_run: True`.
4. When ready to promote: Telegram `/accounts live <account_id>`.
5. To revert: `/accounts dry <account_id>`.

### Interaction with the global DRY_RUN env

The global `DRY_RUN` env var and the per-account override are layered:

* `DRY_RUN=true` (env) → **all** accounts are dry, regardless of override.
  Useful for whole-fleet staging.
* `DRY_RUN=false` (or unset) + `ALLOW_LIVE_TRADING=true` (the only path
  the startup interlock allows for live execution per PR E1) → each
  account's `dry_run` attribute applies.

The interlock and the per-account toggle are independent guards:
both must permit live execution before any real order is placed.

### Why this is safe

Risk caps (`pos_size`, `daily_usd`, `max_dd_pct` per S-012 PR E3a) fire
**before** the dry/live decision in `TradingAccount.place_order()`, so
even an account flipped to live with the caps misconfigured cannot
exceed the limits in `accounts.yaml`. The dry-run toggle only suppresses
exchange submission; risk gating is unaffected.

## Before live changes

```bash
git status -sb
python scripts/secret_scan.py
PYTHONPATH=. pytest --collect-only -q tests
```

## Pre-live checklist

- Confirm `MODE` is `LIVE` or `BACKTEST` (paper is not a supported mode).
- Confirm `DRY_RUN` (use `true` for short-window staging on a small live
  account; flip to `false` only when promoting to real order placement).
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

## VWAP BTCUSD profile

One profile targets the Bybit `vwap_strategy` subaccount:

- `vwap_btcusd_live` — `MODE=LIVE`, `DRY_RUN=false`, `ALLOW_LIVE_TRADING=true`.
  Requires `--allow-live` on the renderer CLI.

It pulls credentials from `bybit.vwap_strategy.api_key` / `api_secret` in the
master secrets file. (Paper-trading variants of this profile have been
removed from the renderer; see `scripts/render_env_from_master.py`.)

## VWAP strategy runtime status (as of 2026-04-27)

`STRATEGY=vwap` is implemented and wired into the pipeline:

- Signal builder: `strategies/vwap_signal_builder.py` (pure VWAP mean-reversion,
  offline-safe, no ML dependency).
- Pipeline routing: `src/runtime/pipeline.py` dispatches to `vwap_signal_builder`
  when `STRATEGY=vwap`.
- Safety gates: `DRY_RUN=true` prevents order placement inside
  `safe_place_order` (the order is logged with status `"dry_run"` and never
  reaches the exchange). `ALLOW_LIVE_TRADING=false` provides a second
  block. `MODE=LIVE` without `ALLOW_LIVE_TRADING=true` is rejected by
  `validate_startup`. `MODE=PAPER` is rejected outright (paper trading is
  not a supported mode).

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
