# Hourly snapshot timer runbook

Operator runbook for `ict-hourly-snapshot.{timer,service}`. M1 P1-C
makes the hourly operator summary fire automatically — the operator no
longer has to press `/hourly` to see the report.

---

## What the timer does

Once an hour, with a 60 s randomised delay, systemd runs:

```
/usr/bin/python3 /home/ubuntu/ict-trading-bot/scripts/send_hourly_now.py
```

The script:

1. Acquires an `fcntl.flock` exclusive lock on
   `/tmp/ict-hourly-snapshot.lock`. A second instance running while
   the first still holds the lock exits with `EX_TEMPFAIL` (75) — the
   service unit treats 75 as success so systemd doesn't mark the unit
   failed on a benign race.
2. Calls `src.runtime.hourly_report.build_hourly_report` to render the
   structured operator summary.
3. Calls `src.runtime.outcomes.send_scheduled` to dispatch the report
   over Telegram. `send_scheduled` bypasses the per-fingerprint rate
   limit and the hourly cap (it's the same path the in-process
   scheduler uses), and falls through to
   `runtime_logs/pending_pings.jsonl` if Telegram is unreachable.
4. Releases the lock and exits.

The timer + manual `/hourly` are now interchangeable. Both end up at
the same `send_scheduled` dispatch.

---

## What this does NOT do

- It does not change the report contents (handled by S-022 / future
  hardening sprints).
- It does not produce per-account variants.
- It does not retry on Telegram failure — the pending-pings drainer
  inside the trader bot is the retry path.

---

## Install on the VM

```bash
# 1. Place the units (already in repo's deploy/ dir).
sudo cp /home/ubuntu/ict-trading-bot/deploy/ict-hourly-snapshot.service \
        /etc/systemd/system/
sudo cp /home/ubuntu/ict-trading-bot/deploy/ict-hourly-snapshot.timer \
        /etc/systemd/system/

# 2. Reload + enable.
sudo systemctl daemon-reload
sudo systemctl enable --now ict-hourly-snapshot.timer

# 3. Confirm.
systemctl list-timers ict-hourly-snapshot.timer
journalctl -u ict-hourly-snapshot.service -n 50 --no-pager
```

The timer is `Persistent=true` so a missed firing (e.g. during a VM
reboot that crosses the hour boundary) replays once on next boot. The
flock guards against the replay racing the next on-time firing.

---

## Verifying delivery

After install, you should see:

  - `systemctl list-timers` listing `ict-hourly-snapshot.timer` with a
    NEXT value ≤ 1 h away.
  - Within ≤ 1 h + 60 s jitter, a Telegram message from
    `@bict_trading_bot` with the hourly report body.
  - `journalctl -u ict-hourly-snapshot.service` showing
    `dispatching (N chars) ...` followed by `dispatched.` on each run.
  - `runtime_logs/pending_pings.jsonl` *unchanged* (only populated
    when Telegram is unreachable).

---

## Troubleshooting

### Timer is enabled but the hourly Telegram never arrives

  - `journalctl -u ict-hourly-snapshot.service -n 50` — look for
    Python tracebacks. Common cause: `.env` missing
    `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`.
  - `tail runtime_logs/pending_pings.jsonl` — if rows are
    accumulating, Telegram is reachable but the bot drainer isn't
    firing. Check `ict-telegram-bot.service`.
  - `cat /tmp/ict-hourly-snapshot.lock` — if it exists and is
    non-empty, a previous run held the lock. The flock releases on
    process exit, so a stale lock indicates the process was killed
    mid-flight; safe to delete the file.

### Two reports arrive in the same hour

The flock should prevent this; check whether
`OnCalendar=hourly` was changed to a sub-hour cadence in
`/etc/systemd/system/ict-hourly-snapshot.timer`. If the units were
edited in place, run `sudo systemctl daemon-reload` and re-enable.

### `journalctl` shows `EX_TEMPFAIL` (exit code 75)

That's the documented "another instance held the lock" path — not a
failure. The service unit's `SuccessExitStatus=0 75` line treats it
as success. Repeated EX_TEMPFAIL exits indicate the lock is being
held longer than the inter-fire gap; investigate what's stuck before
disabling the timer.

---

## References

  - [`scripts/send_hourly_now.py`](../../scripts/send_hourly_now.py)
  - [`deploy/ict-hourly-snapshot.timer`](../../deploy/ict-hourly-snapshot.timer)
  - [`deploy/ict-hourly-snapshot.service`](../../deploy/ict-hourly-snapshot.service)
  - [`docs/audits/M1-comms-audit-followups-fresh.md`](../audits/M1-comms-audit-followups-fresh.md) — P1-C scope
  - [`src/runtime/hourly_report.py`](../../src/runtime/hourly_report.py)
  - [`src/runtime/outcomes.py`](../../src/runtime/outcomes.py) (`send_scheduled`)
