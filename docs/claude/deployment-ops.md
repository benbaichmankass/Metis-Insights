# Deployment ops

> **Telegram command surface has changed (PR #1933, 2026-05-25).** The
> verification commands referenced below (`/halt`, `/resume`, `/set_keys`,
> `/accounts_status`, `/vm`, `/vm_write`) **no longer exist** — the
> trader bot is menu-driven (see [`docs/TELEGRAM-SPEC.md`](../TELEGRAM-SPEC.md))
> and the `/vm` + `/vm_write` VM-runner subsystem was removed. Today's
> equivalents: kill switch ⇒ menu's **🛑 Kill switch → By account**; mode
> flip ⇒ `set-account-mode` operator action; VM bash on the live VM ⇒
> `system-actions` workflow (see [`system-actions.md`](system-actions.md));
> arbitrary VM bash on the trainer ⇒ `trainer-vm-diag` relay (see
> [`trainer-vm-mode.md`](trainer-vm-mode.md) § 9). The verification
> sequences below are kept for archeology of how they used to be wired.

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

* **Accounts default to `mode: live`** — `_resolve_mode()` in
  `src/units/accounts/__init__.py` uses `cfg.get("mode", "live")` so any
  account without an explicit `mode:` field is live.  (Pre-BUG-039 the
  `TradingAccount.__init__` Python default was `dry_run=True`; that
  Python-level default is now overridden at load time by `_resolve_mode`.)
* **To put an account in dry-run:** set `mode: dry_run` in
  `config/accounts.yaml` and redeploy, or use Telegram
  `/accounts dry <account_id>` for a runtime override that persists
  in-process via `_DRY_RUN_OVERRIDES` and is re-applied on every
  `load_accounts()` call.

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

> **Historical (BUG-039)** — `DRY_RUN` and `ALLOW_LIVE_TRADING` env vars
> were removed 2026-05-03.  The section below describes the pre-BUG-039
> layering; it no longer applies.  The current contract is described in
> the "Trading mode (BUG-039)" section further down.

~~The global `DRY_RUN` env var and the per-account override are layered:~~
~~`DRY_RUN=true` (env) → all accounts are dry, regardless of override.~~
~~`DRY_RUN=false` (or unset) + `ALLOW_LIVE_TRADING=true` → per-account
`dry_run` applies.~~

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

- Confirm each account's `mode:` in `config/accounts.yaml` is `live`
  (or absent — default is live). Do **not** look for `MODE` / `DRY_RUN` /
  `ALLOW_LIVE_TRADING` — those env vars were removed (BUG-039).
- Confirm exchange API keys are set as env vars matching `api_key_env` /
  `api_secret_env` in `accounts.yaml`.
- Confirm Telegram emergency stop works (`/halt` → `/resume`).

## VM reset / redeploy

> **WARNING:** A successful env render is *not* a green light to reset or redeploy the VM.
> A VM reset/redeploy requires a **separate audit** — running processes, in-flight orders,
> log/state retention, deployment artifacts, and on-host config must all be reviewed
> manually first. Do **not** assume any tool, notebook, or script has performed that audit
> on your behalf. There is no auto-reset path.

The canonical operator workflow for `.env` generation, settings updates, key
rotation, and VM restart is `notebooks/operator/rotate_api_keys.ipynb` — the
single notebook the Telegram `/set_keys` command opens. It does not reset the
VM beyond restarting the trader and Telegram-bot systemd units.

## Trading mode (BUG-039)

The dry/live toggle is **per-account** in `config/accounts.yaml` (`mode: live | dry_run`),
applied via `RiskManager.dry_run` and checked inside `RiskManager.evaluate()`
(returns `reason="account_mode_dry_run"` so the executor logs the would-be
trade to the journal but never calls the exchange).

There is **no** process-level interlock. There is **no** strategy-level toggle.
There is **no** profile-level / env-variable toggle. The rendered `.env` carries
credentials, exchange selection, Telegram tokens, and per-account API-key env
vars — never `MODE` / `DRY_RUN` / `ALLOW_LIVE_TRADING`.

To flip an account: edit `config/accounts.yaml` `mode` field and let the
trader reload, or use Telegram `/accounts dry|live <name>` for a runtime
override.

## VWAP strategy runtime status (as of 2026-04-27)

`STRATEGY=vwap` is implemented and wired into the pipeline:

- Signal builder: `src/units/strategies/vwap.py` (pure VWAP mean-reversion,
  offline-safe, no ML dependency).
- Pipeline routing: `src/runtime/pipeline.py` dispatches to the multiplexer
  which iterates the registry in `config/strategies.yaml`.
- Safety gates: per-account `RiskManager.dry_run` (the only dry/live toggle
  in the codebase post-BUG-039) plus the `/halt` kill-switch.

## deploy_pull_restart.sh — VM deploy script

### Why no venv on the VM

The live trader is invoked directly by systemd as `/usr/bin/python3 -u -B -m src.main`.
There is no virtualenv on the production VM. Attempting `source .venv/bin/activate`
fails with "No such file or directory". The deploy script uses `/usr/bin/python3 -m pip`
to match the live runtime.

### Services restarted (S-067 follow-up #5: enumeration)

The script no longer carries a fixed unit list. Instead:

```
mapfile -t ICT_UNITS < <(systemctl list-units --all --type=service --plain --no-legend 'ict-*.service' | awk '{print $1}')
```

Every unit matching `ict-*.service` that systemd knows about is
restarted, *unless* its name appears in `DEPLOY_RESTART_SKIP` (a
space-separated env var). The default skip-list is:

```
ict-smoke-once.service        # oneshot — gated by run_smoke_once.flag
ict-env-check.service         # oneshot — bootup-only
ict-hourly-snapshot.service   # timer-driven
ict-heartbeat.service         # timer-driven
ict-git-sync.service          # would refuse to restart from inside its own run
ict-insights-generator.service  # M13 S1: oneshot, timer-driven (every 10 min)
ict-health-snapshot.service   # oneshot, timer-driven (every 15 min) — writes artifacts/health/* (BL-20260529-005)
ict-web-api-watchdog.service  # oneshot, timer-driven (every 2 min) — self-heals ict-web-api (BL-20260604-003)
ict-db-integrity.service      # oneshot, timer-driven (hourly) — DB-integrity check + Telegram alert (dashboard-truth Phase 4)
ict-mes-ibkr-pull.service     # oneshot, timer-driven (daily 23:30 UTC) — MES IBKR deep-history pull; in DEFAULT_SKIP so a deploy never fires an unscheduled ~20-30 min gateway pull (BL-20260626-MES-BASE-STALE)
```

**Why enumeration?** The 2026-05-09 24+h-stale-code incident shipped
because `ict-web-api.service` was added to the deploy unit inventory
*after* the script was last touched, and the script's explicit list
silently missed it. Enumeration closes that class of bug — any new
`ict-*.service` file dropped under `/etc/systemd/system/` is
automatically restarted on the next deploy.

**Override:** `DEPLOY_RESTART_SKIP="ict-foo.service ict-bar.service"`
fully replaces the default. To add to the default rather than replace,
include the defaults explicitly in the value.

**`ict-claude-bridge` boot-enable (2026-06-23):** the restart enumeration only
acts on *loaded* units, so a disabled-and-stopped service is never (re)started by
a deploy. `ict-claude-bridge` (the prop / Claude-comms bot) was left disabled
after the 2026-06-14 Ampere cutover and stayed dark across reboots. `scripts/install_systemd_units.sh`
now `enable --now`s it on the trader box (idempotent; gateway VM excluded by the
role-prune block; tolerant of a failed start when the token isn't synced yet), so
it survives reboots like the other core services.

### Post-deploy version round-trip assertion

After the restarts, the script asserts that `/api/diag/version` on
the local web-api advertises the same git SHA as `git rev-parse
--short HEAD`. If the SHA disagrees after up to 6 retries (5 s
each), the script exits 4 — the deploy fails loudly rather than
silently leaving stale code running. Same incident class as the
2026-05-09 stale-code event.

The assertion is a no-op (logs and continues) when:
- `curl` isn't installed.
- `ict-web-api.service` isn't installed.
- `DIAG_READ_TOKEN` is unset and `/etc/ict-trading-bot/diag_token`
  isn't readable.

Set `DIAG_READ_TOKEN` in the script's environment, or write the token
to `/etc/ict-trading-bot/diag_token` (root-owned, mode 0600).

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

**Root cause (2026-06-15): a broken `/dev/null` silently wedged auto-deploy.**
An OS host agent on the Ampere live VM (suspected `oracle-cloud-agent` `oci-wlp`
/ workload-protection FIM) intermittently chmods `/dev/null` to `0444`. The write
bit is stripped for non-root users, so `deploy_pull_restart.sh` — run as `ubuntu`
under `set -e` — EACCESes at its first `>/dev/null` redirect (the `sudo -n
systemctl … >/dev/null 2>&1` probe) and exits 1 *before fetching or restarting*.
`ict-git-sync` failed every 5 min for ~16h and a merged fix never reached the
trader. Durable fix shipped: **`ict-devnull-guard.{service,timer}`** (root oneshot,
every 60 s) re-asserts `/dev/null` is the `1:3` char device, mode `0666`; plus a
self-heal at the top of `deploy_pull_restart.sh`. Manual unwedge:
`vm-fix-devnull` (repair) then `vm-devnull-deploy-bootstrap` (chmod + deploy in
one fresh window). Full runbook: [`docs/runbooks/devnull-guard.md`](../runbooks/devnull-guard.md).

---

## VM-resident Claude (S-014.5 — meta workflow)

A separate doc, `docs/claude/vm-operator-mode.md`, defines the binding
tier policy and refusal protocol. This section covers the *operations*
side: install, smoke test, rollback.

### One-time bootstrap

The PM (or any operator with sudo on the VM) runs this **once** from
Oracle Cloud Shell or any local SSH session into `ubuntu@141.145.193.91`:

```bash
cd /home/ubuntu/ict-trading-bot
git pull origin main
bash scripts/vm_bootstrap.sh
sudo systemctl restart ict-telegram-bot
```

The script will:
1. Add a 2 GB swap file at `/swapfile` (idempotent — skipped if present).
2. Install Node.js 20 LTS + Claude Code via npm.
3. Drop the tier permission profiles to `/etc/claude/permissions.{read,write}.json`
   (mode 0644 root:root — the runner cannot mutate them).
4. Prompt for `ANTHROPIC_API_KEY` and write it to `/etc/ict-trader/claude.env`
   (mode 0640 root:ubuntu).
5. Create `/var/log/claude-vm/` and `/run/claude/prompts/` with the right
   ownership; register a tmpfiles.d entry so `/run/claude/` is recreated
   on every boot.
6. Install `deploy/claude-vm-runner@.service` to `/etc/systemd/system/`
   and `daemon-reload`.

### Smoke test (do this immediately after bootstrap)

From Telegram, in order:

1. `/vm what services are active and what is the trader uptime?` — expect a
   tier-1 transcript with `systemctl is-active` output and uptime. If the
   runner errors with "VM marker missing", the bootstrap didn't complete.
2. `/vm cat /etc/claude/vm-marker` — confirms file readability under the
   runner's effective user.
3. `/vm_write echo hello from tier 2 > /tmp/vm_smoke_test` — bot replies
   with the confirm/cancel buttons. Tap Confirm. Expect a tier-2
   transcript that succeeds. Then `/vm cat /tmp/vm_smoke_test` to verify.
4. `/vm rm -rf /home/ubuntu/ict-trading-bot` — expect an immediate
   `TIER 3 BLOCKED` refusal. The runner must NOT spawn for this.
5. Watch `journalctl -u 'claude-vm-runner@*'` during steps 1–4 to confirm
   each invocation logged with its id, exit code, and duration.

### Rollback

Disable the feature without uninstalling:
```bash
sudo systemctl mask 'claude-vm-runner@.service'
```
The Telegram commands will report "feature disabled by mask" on every
invocation. To re-enable: `sudo systemctl unmask 'claude-vm-runner@.service'`.

Full uninstall:
```bash
sudo rm -f /etc/systemd/system/claude-vm-runner@.service
sudo rm -rf /etc/claude /etc/ict-trader/claude.env
sudo rm -rf /var/log/claude-vm /run/claude
sudo rm -f /etc/tmpfiles.d/claude-vm.conf
sudo npm uninstall -g @anthropic-ai/claude-code
sudo systemctl daemon-reload
sudo systemctl restart ict-telegram-bot
```
The swap file at `/swapfile` is intentionally left in place — it benefits
the live trader even without the runner.

### Memory accounting

The runner unit caps at `MemoryMax=400M` and `MemoryHigh=300M`. Combined
with the live trader (~250 MB observed), web API (~120 MB), and
Telegram bot (~80 MB), total commit on the 1 GB box stays within the
2 GB swap budget even if Claude spills. A successful tier-1 invocation
typically peaks at 180–250 MB.

If `journalctl -u 'claude-vm-runner@*'` shows OOM kills, **do not** raise
`MemoryMax` from inside the runner (it's Tier 3). The right escalation
is a VM shape upgrade to A1.Flex (4 GB) — log it as a separate sprint.
