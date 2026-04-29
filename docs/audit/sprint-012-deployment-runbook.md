# Sprint S-012 — Deployment runbook

> **Owner:** PM Ben (or Claude via Colab SSH).
> **Target VM:** the live Oracle VM running `ict-trader-live.service`.
> **Goal:** Pull S-012 changes, reload systemd units, restart services
> in the safe order, and verify the new strategy roster is live.
>
> **Guardrail #1 (sprint prompt):** Live trader uptime is preserved
> until the very last step. `ict-trader-live` is restarted **last** so
> the live-trading window is bounded.

This runbook is paired with the post-PR-E4 codebase. Run from the repo
root on the VM. Every command is copy-paste ready; no flags need
editing unless explicitly noted.

## 0. Pre-flight

Confirm the VM is on the expected branch and clean.

```bash
cd ~/ict-trading-bot          # or /home/ubuntu/ict-trading-bot
git status -sb                 # must be clean; no uncommitted changes
git rev-parse HEAD             # record the pre-deploy SHA for rollback
```

Snapshot the running services so you have something to compare with after.

```bash
systemctl is-active ict-trader-live ict-telegram-bot \
                    ict-heartbeat.service ict-git-sync.service
ls -la deploy/                 # canonical 5 .service files + 2 timers
```

Expected `.service` files:
`ict-env-check`, `ict-git-sync`, `ict-heartbeat`, `ict-telegram-bot`,
`ict-trader-live`. (Per S-012 PR D2's structural test.)

If any extra `ict-trader-*.service` appears, **stop and investigate**
— the regression test should have caught it.

## 1. Pull S-012 changes

```bash
git fetch origin main
git log --oneline HEAD..origin/main | head -25    # review what's about to land
git pull --ff-only origin main
```

If `--ff-only` fails, the VM has divergent state. Stop, snapshot, and
escalate before forcing — guardrail #1 says do not lose the running
session.

## 2. Reload systemd units (no service restarts yet)

```bash
sudo systemctl daemon-reload
```

This rereads the `.service` files in `/etc/systemd/system/`. If your
deploy flow installs from `deploy/` via `scripts/deploy_pull_restart.sh`,
that step does the install + reload itself; otherwise:

```bash
sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

## 3. Restart sequence (live LAST)

The non-trader units run on timers or daily — restart them first so any
config-load errors surface before the live trader is touched.

### 3a. Telegram bot

```bash
sudo systemctl restart ict-telegram-bot.service
sleep 2
systemctl is-active ict-telegram-bot.service     # → active
journalctl -u ict-telegram-bot -n 30 --no-pager | tail -30
```

Smoke-test from Telegram:
* `/strategies` — should list `turtle_soup` and `vwap`, both
  `enabled: true`.
* `/accounts_status` — every account should report `dry_run: True`
  by default (per PR E2 contract).

### 3b. Heartbeat + git-sync timers

```bash
sudo systemctl restart ict-heartbeat.timer ict-git-sync.timer
systemctl list-timers ict-heartbeat.timer ict-git-sync.timer --no-pager
```

### 3c. Live trader — LAST

```bash
sudo systemctl restart ict-trader-live.service
sleep 3
systemctl is-active ict-trader-live.service     # → active
journalctl -u ict-trader-live -n 50 --no-pager
```

### 3d. Confirm both strategies are running

The pipeline default is now `STRATEGY=multiplexed` (PR C5). The audit
log line `runtime_logs/signal_audit.jsonl` includes the `strategy`
field per ticker (PR E4):

```bash
tail -n 20 runtime_logs/signal_audit.jsonl | jq -r '"\(.event)\t\(.strategy)\t\(.symbol)\t\(.side)"'
```

Expected: at least one row attributed to `turtle_soup` and one to
`vwap` within ~30 minutes (each strategy emits per its timeframe).

If only one strategy attribute appears after a full hour, capture
`journalctl -u ict-trader-live --since "30 min ago"` and escalate
before promoting any account to live.

## 4. Verification checklist

Run from the VM. All five must pass before the deploy is considered
green.

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | Live trader is up | `systemctl is-active ict-trader-live` | `active` |
| 2 | Telegram bot is up | `systemctl is-active ict-telegram-bot` | `active` |
| 3 | Strategies list matches roster | Telegram `/strategies` | `turtle_soup`, `vwap` (both `enabled: true`); no others |
| 4 | Accounts default dry-run | Telegram `/accounts_status` | every account `dry_run: True` |
| 5 | Both strategies emitting signals | `tail -n 50 runtime_logs/signal_audit.jsonl \| jq -r .strategy \| sort -u` | includes `turtle_soup` AND `vwap` (within ~1 hour) |

## 5. Promote accounts to live (operator-driven, optional)

Per PM § 8 #2 + § 8 #4: turtle_soup ships `enabled: true` but every
account starts in dry-run. To promote an account:

```text
Telegram → /accounts live <account_id>
```

Verify the override stuck:

```text
Telegram → /accounts_status
```

The target account should now show `dry_run: False`.

**Recommended order:**
1. Promote `prop_breakout_1` (smallest pos_size cap = $200) first;
   watch one full session.
2. Promote `bybit_1` next.
3. Promote `bybit_2` only after the first two have produced clean
   signal-audit rows for ≥ 24 hours.

Revert any account at any time with `/accounts dry <account_id>`.

## 6. Rollback procedure

If any verification step fails or the live trader misbehaves:

```bash
# 1. Stop the live trader to bound the damage.
sudo systemctl stop ict-trader-live.service

# 2. Roll the repo back to the pre-deploy SHA recorded in step 0.
git fetch origin main
git checkout <PRE_DEPLOY_SHA>     # the SHA captured in step 0
sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# 3. Restart in the same safe order: bot first, trader last.
sudo systemctl restart ict-telegram-bot.service
sleep 2
sudo systemctl restart ict-trader-live.service

# 4. Verify rollback.
systemctl is-active ict-trader-live ict-telegram-bot
```

If rollback completes cleanly, post `/sprintlet_status S-012 rolled
back: <reason>` to Telegram and pause the sprint until PM weighs in.

If rollback itself fails, the VM-side fallback is the legacy
`ict-trader-live` from before the rollback target — kill the process
manually (`pkill -f "src.main"`) and start it via `python -m src.main`
from a known-good checkout in a tmux session while Claude diagnoses.

## 7. Open items (not blocking deploy)

* **Equity wiring for `max_dd_pct`:** PR E3a implements the cap but the
  orchestrator must call `RiskManager.update_equity(<usd>)` after each
  balance refresh for the cap to fire. Until that wiring lands
  (separate sprint), the drawdown check is silently skipped. See
  `docs/sprint-summaries/sprint-012-summary.md` § "Deferred items".
* **Pre-existing test failures (17):** `test_runtime_validation.py`
  signature mismatch from S-009. Out of S-012 scope; PR F5 logs the
  rewrite as a follow-up.
* **VM-side phantom investigation (PM § 4.5):** if `ict-trader-bak`
  or `ict-trader-example` ever reappear in Telegram output after this
  deploy, run:
  ```bash
  sudo find /etc/systemd /lib/systemd -iname "*trader*bak*" -o -iname "*trader*example*"
  sudo systemctl list-unit-files | grep -Ei 'trader-(bak|example)'
  sudo grep -rn "ict-trader-bak\|ict-trader-example" /usr/local/bin /home/ubuntu /etc 2>/dev/null
  journalctl --since "1 day ago" | grep -Ei 'trader-(bak|example)'
  ```
  to confirm nothing on the VM still produces those names. Repo-side
  is locked by PR D3's regression test.
