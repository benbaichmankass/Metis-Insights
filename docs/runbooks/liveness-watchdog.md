# Runbook — Liveness watchdog (`ict-liveness-watchdog`)

External per-minute dead-man switch for `ict-trader-live.service`.
Shipped 2026-05-11 (PRs #950, #953, #956) after a 5-hour silent-failure
window where the trader process kept running but its heartbeat /
audit / DB writes all stopped — and nobody knew until the next 6-hourly
health check picked it up.

## What this is (and isn't)

| | This runbook | `health-check.md` |
|---|---|---|
| Scope | Per-minute liveness of the trader process | 6-hourly full audit of the trading pipeline |
| Trigger | `ict-liveness-watchdog.timer` (`OnUnitActiveSec=60s`) | `health-snapshot.yml` cron `0 2 * * *` |
| What it watches | `heartbeat.txt` mtime | full VM snapshot + manual Claude review |
| Alert latency | ~5 min from stall | up to 24 h from stall (operator-driven) |
| Recovery action | optional `systemctl restart` after ~8 min stall | none (operator decides) |
| Code | `scripts/check_heartbeat.py` (stdlib-only) | `scripts/collect_health_snapshot.sh` + `/health-review` skill |

Both layers coexist. The watchdog is the **fast** alert; the 6-hourly
health check is the **deep** audit. If the watchdog Telegrams you, the
trader is wedged *right now*; if the 6-hourly review flags a concern,
it usually means a degradation pattern over the window.

## The dead-man switch contract

`scripts/check_heartbeat.py` runs every 60 s and reads
`heartbeat.txt`'s mtime (the trader rewrites this file every 60 s from
inside `src/main.py`'s sleep loop, regardless of tick cadence).

| heartbeat age | Action |
|---|---|
| ≤ 300 s (5 min) | `status=0`, no Telegram |
| > 300 s, first stale check | Telegram `[CRITICAL] Trader heartbeat stale` |
| > 300 s, subsequent stale checks | no re-Telegram (deduped) until age worsens by another full threshold |
| 3 consecutive stale checks (~8 min total stall) | `sudo -n systemctl restart ict-trader-live.service` + Telegram autoheal-result |
| heartbeat fresh again after stale | Telegram `[OK] Trader heartbeat recovered` |

The 5-min grace is `--interval 60 --grace 5` in the service file. It
comfortably covers a normal `RestartSec=10` + ~30 s startup so routine
trader restarts (operator deploys, systemd auto-restarts) don't trip
the alert.

State lives in `runtime_logs/heartbeat_check_state.json` —
`last_status`, `last_alert_age_s`, `stale_streak`,
`last_autoheal_streak`. Idempotent across runs.

## When Telegram fires

Three possible messages — each lands once per state transition:

### `[CRITICAL] Trader heartbeat stale`
```
[CRITICAL] Trader heartbeat stale
Last beat 6m ago (>5m threshold). Detected 2026-MM-DD HH:MM:SS UTC.
Process may be stuck or dead.
```

What to do:
1. Check the dashboard's "Status" tile — if it says `stopped`, the
   trader's process probably actually died; systemd Restart=always
   should recover within 10s. If the autoheal fires next, you'll see
   the recovery Telegram below.
2. If autoheal is enabled and fires successfully, you'll get a second
   Telegram tagged `[ACTION] Autoheal dispatched` and within ~30 s a
   third tagged `[OK] Trader heartbeat recovered`. No further action.
3. If autoheal fires and FAILS (rc≠0), you'll get a
   `[CRITICAL] Autoheal restart returned rc=N` Telegram. SSH in:
   `sudo systemctl status ict-trader-live`. Likely causes: unit
   masked, sudo permission revoked, or systemd itself unresponsive.
4. If you didn't enable autoheal (or 8 min hasn't passed), restart
   manually: dispatch the `restart-bot-service` operator-action via
   `[operator-action] restart-bot-service` issue.

### `[ACTION] Autoheal dispatched`
```
[ACTION] Autoheal dispatched: systemctl restart ict-trader-live.service
Trigger: heartbeat stale 8m, 3 consecutive checks.
systemctl exit=0. Next heartbeat in ~30 s should confirm recovery.
```

The watchdog already restarted the trader. No action unless this
recurs frequently (which would indicate a regression — see "Tuning"
below).

### `[OK] Trader heartbeat recovered`
```
[OK] Trader heartbeat recovered
Resumed at 2026-MM-DD HH:MM:SS UTC. Latest beat is fresh.
```

Trader is back. The next 6-hourly health-review may still want to
look at what caused the stall — usually a journal pull of the
stall window (`journalctl?unit=ict-trader-live.service&since=...`)
surfaces the trigger.

## Tuning

All knobs live in `deploy/ict-liveness-watchdog.service`'s
`ExecStart=`:

```
/usr/bin/python3 -u scripts/check_heartbeat.py \
    --interval 60 \
    --grace 5 \
    --auto-restart-after 3
```

| Flag | Effect | Adjust if |
|---|---|---|
| `--interval` | base tick interval in seconds | trader cadence changes (rare) |
| `--grace` | threshold multiplier (alert at `interval × grace`) | you want a tighter / looser alert window. ≥3 recommended to cover a normal restart. |
| `--auto-restart-after N` | autoheal after N consecutive stale checks. `0` disables. | set to `0` to revert to alert-only; raise to `5` for a more forgiving threshold |

Per-environment overrides (no service-file edit needed):

```
# /home/ubuntu/ict-trading-bot/.env
LIVENESS_AUTO_RESTART_AFTER=0     # turn autoheal off
LIVENESS_RESTART_UNIT=ict-trader-live.service   # default
```

After editing the service file, deploy via the standard
`pull-and-deploy` operator-action or `scripts/install_systemd_units.sh`;
both call `daemon-reload` and re-trigger the timer.

After editing `.env`: `sudo systemctl restart ict-liveness-watchdog.service`
(or wait — the next timer fire 60 s later will pick up the new env).

## Verifying the watchdog is alive

From a Claude session (or any operator with the diag relay):

```
[diag-request] journalctl?unit=ict-liveness-watchdog.service&lines=20
```

Expected output — 60 s apart `Started` / `Finished` cycles, each
`(code=exited, status=0/SUCCESS)`:

```
21:35:01 Started ICT Trading Bot — Liveness Watchdog (heartbeat + autoheal)...
21:35:02 ict-liveness-watchdog.service: Deactivated successfully.
21:35:02 Finished ICT Trading Bot — Liveness Watchdog (heartbeat + autoheal).
21:36:03 Started ICT Trading Bot — Liveness Watchdog (heartbeat + autoheal)...
21:36:03 ict-liveness-watchdog.service: Deactivated successfully.
21:36:03 Finished ICT Trading Bot — Liveness Watchdog (heartbeat + autoheal).
```

If the cycles are not appearing every 60 s, the timer is broken.
Check `systemctl list-timers ict-liveness-watchdog.timer` on the VM.

## Internal architecture

```
┌─────────────────────────────────┐         ┌───────────────────────────┐
│ ict-trader-live.service         │ writes  │ runtime_logs/heartbeat.txt │
│   src/runtime/heartbeat.py      ├────────►│   mtime + tick=N line      │
│   (every HEARTBEAT_INTERVAL_S)  │         └───────────┬───────────────┘
└─────────────────────────────────┘                     │ reads mtime
                                                        ▼
                                            ┌────────────────────────────┐
                                            │ ict-liveness-watchdog.timer│
                                            │ (every OnUnitActiveSec=60s)│
                                            └───────────┬────────────────┘
                                                        │ triggers
                                                        ▼
                                            ┌────────────────────────────┐
                                            │ check_heartbeat.py         │
                                            │   evaluate() → action      │
                                            │   send_alert() → Telegram  │
                                            │   try_autoheal_restart()   │
                                            │     → systemctl restart    │
                                            └────────────────────────────┘
```

The watchdog is **stdlib-only by design** — no `requests`, no
`anthropic`, no `src.*` imports beyond `src.runtime.notify`. If the
trader's venv breaks (e.g., a bad `pip install` mid-deploy), the
watchdog still runs and still alerts.

## Why the design looks the way it does

- **External, not in-process.** A wedged trader can't reliably
  Telegram about its own wedge (the alerts queue and drainer both
  live in the trader process). External keeps the alert path
  independent of what failed.
- **systemd timer, not cron.** The timer's `Persistent=true` means
  a missed run after a reboot fires immediately on startup, instead
  of waiting for the next scheduled tick.
- **60 s cadence + 5× grace.** Cadence matches the trader's own
  heartbeat-write loop (60 s default). Five missed beats is enough
  to absorb a normal restart (RestartSec=10s + ~30 s startup) without
  false-positive Telegrams.
- **Autoheal after 3 streaks, not 1.** A single 5-min stall is the
  first alert; restart-after-3 means total stall ≥ 8 min before
  intervention. Long enough to avoid restart-looping on a slow tick;
  short enough that an actually-wedged trader recovers in < 10 min.
- **Streak counter independent of alert dedup.** Alert dedup means
  we don't Telegram twice for the same stall, but the autoheal
  threshold should still accumulate. `stale_streak` and
  `last_autoheal_streak` in the state JSON are intentionally
  decoupled from `last_status`.

## Incident history

| Date | Incident | Outcome |
|---|---|---|
| 2026-05-11T10:01Z → ~19:31Z | Heartbeat writer silent-failed for ~9 h while the trader continued ticking; only caught by the next 6-hourly /health-review | Drove PR #950 — external watchdog created |
| 2026-05-11T22:00Z onward | Watchdog deployed, autoheal enabled | Verified firing on 60 s cadence, no false positives observed |

The root cause of the 2026-05-11 silent failure (why `write_heartbeat()`
returned False without raising) is tracked under FU-20260511-008 in
`comms/follow_ups.json` — the watchdog catches the symptom regardless,
and the next stall will surface the exception via the `logger.exception`
that PR #950 added to `src/runtime/heartbeat.py`.

## Related code + files

- Service: `deploy/ict-liveness-watchdog.service`
- Timer: `deploy/ict-liveness-watchdog.timer`
- Script: `scripts/check_heartbeat.py`
- Heartbeat write path: `src/runtime/heartbeat.py::write_heartbeat`
- Heartbeat label helper (dashboard): `src/runtime/heartbeat.py::heartbeat_label`
- State: `runtime_logs/heartbeat_check_state.json`
- Tests: `tests/test_heartbeat.py`
- Telegram helper: `src/runtime/notify.py::send_telegram_direct`
