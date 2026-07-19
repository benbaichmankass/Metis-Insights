# Live-trading plumbing smoke test

How to verify the live-trading chain end-to-end (signal →
`safe_place_order` → exchange → fill → trade journal → Telegram ping).
Designed to be invokable autonomously per CLAUDE.md "Autonomous
live-trading rule" — no per-trade operator confirmation.

## Mechanism

Three pieces:

1. **`scripts/smoke_test_trade.py`** — constructs a tagged signal
   (`meta.strategy_name="smoke_test"`) and dispatches it through
   `safe_place_order`. Hard `qty <= 0.001 BTC` cap; refuses if
   `ALLOW_LIVE_TRADING` is not set.
2. **`deploy/ict-smoke-once.service`** — one-shot systemd unit. Runs
   `scripts/run_smoke_once.sh` which fires the smoke four times:
   - `bybit_1` sub-min qty (expect rejection — proves plumbing-on-
     rejection works)
   - `bybit_1` real qty 0.001 BTC (expect fill + immediate close)
   - `bybit_2` sub-min qty
   - `bybit_2` real qty
3. **`runtime_flags/run_smoke_once.flag`** — trigger file. The
   `scripts/deploy_pull_restart.sh` git-sync hook checks for this
   after a HEAD-advancing pull and starts the systemd unit if
   present. The wrapper deletes the flag so a no-op re-pull doesn't
   refire.

## One-time install on the VM

**No manual step required since S-018.** `scripts/deploy_pull_restart.sh`
runs `scripts/install_systemd_units.sh` after every HEAD-advancing
pull, which auto-copies every `deploy/*.service` and `deploy/*.timer`
into `/etc/systemd/system/` and runs `daemon-reload` if anything
changed. The first pull after S-017 PR #223 + S-018 PR landed on the
VM installed `ict-smoke-once.service` automatically.

If you want to verify or force-install:

```bash
cd /home/ubuntu/ict-trading-bot
bash scripts/install_systemd_units.sh        # idempotent
sudo systemctl status ict-smoke-once.service --no-pager | head -3
```

The unit is one-shot — never enabled. The flag file triggers it.

## How to fire the smoke autonomously (from anywhere)

From the sandbox / a dev box / a phone with git access — anywhere
that can `git push`:

```bash
mkdir -p runtime_flags
touch runtime_flags/run_smoke_once.flag
git add runtime_flags/run_smoke_once.flag
git commit -m "smoke: trigger one-shot live smoke"
git push origin main
```

The VM's `ict-git-sync.timer` picks the commit up within ~5 min,
fires `ict-smoke-once.service` from the deploy script, the service
runs the four-step smoke, deletes the flag, and exits. Telegram
pings fire automatically (S-016 H3 wiring).

## How to fire the smoke manually on the VM

If you have shell access on the VM:

```bash
sudo systemctl start ict-smoke-once.service
sudo journalctl -u ict-smoke-once.service -f
```

Or via the legacy `/vm` Telegram runner (removed #1933 — see the note below; run it through `system-actions` or a direct ops SSH instead):

```
/vm sudo systemctl start ict-smoke-once.service && journalctl -u ict-smoke-once.service -n 100 --no-pager
```

(**Note (2026-05, #1933):** the `/vm` / `/vm_write` Telegram command-runner
referenced here was **removed** — VM state mutations now run through the
**`system-actions`** workflow (labelled issue; Tier-2 actions need an operator
OK in chat) or a direct ops SSH. The `scripts/smoke_test_trade.py` +
`ict-smoke-once.service` mechanism below is unchanged.)

## What success looks like

- Two of the four steps return exit 1 (rejection — sub-min qty).
  Plumbing-on-rejection verified.
- Two return exit 0 (fill + close round-trip). Plumbing-on-success
  verified.
- `signal_audit.jsonl` has 8 entries (open_attempt + open_result for
  each of the 4 steps; close_attempt + close_result for the 2 that
  filled).
- `trade_journal.db` has at least 4 rows tagged `smoke_test` (2 per
  filled round-trip).
- `/balance` on each account moved by ≤ a few dollars (fees +
  slippage on the round-trips).
- `/trades` shows no open positions after the smoke completes (close
  legs flatten the position).
- Telegram pings fired automatically on `signal_audit.jsonl` updates
  (per the live trader's existing notification path, not the H3 ping
  wiring).

## What failure looks like

- `ict-smoke-once.service` exits non-zero — `journalctl -u
  ict-smoke-once.service` for the last run shows which step failed.
- All four steps exit 2 (script-level error) — most likely missing
  Bybit creds in the env or `ALLOW_LIVE_TRADING` unset.
- Some steps exit 1 unexpectedly (a real fill rejected) — likely
  account de-risked, balance insufficient, or symbol not tradeable
  on the configured testnet/mainnet.

If the close leg fails on a filled step, the position is left open.
**Flatten immediately via the `flatten-{ib,bybit,alpaca}-position` system-action**
(open a `system-action` labelled issue with `action: flatten-<venue>-position`,
`account: <id>`, `symbol: <SYM>`, `apply: true`; dry-run is the default). The
legacy `/closeall` Telegram command was removed in #1933.

## Re-running

The wrapper deletes the trigger flag after running. To re-run:
re-commit + push the flag file.

## What this is NOT

- Not a P&L test — slippage on a round-trip costs $1-2 in fees, by
  design.
- Not a strategy test — every trade is tagged `smoke_test`, never
  `vwap` or `turtle_soup`. The `/strategies` aggregations exclude
  these (filtering in `data_loaders.py` is by `strategy` field).
- Not a load test — fires four orders sequentially, not in parallel.
