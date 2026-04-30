# S-016 H4 — Unit-independence check (systemd graph), 2026-04-30

**Scope:** verify that the four production-relevant systemd units run
independently — i.e. a crash or hang in one doesn't cascade into the
others. Read-only inspection of `deploy/*.service` + `deploy/*.timer`.

The H0 audit reported no `Requires=` / `BindsTo=` / `PartOf=` between
the long-running units; this doc confirms it line by line and surfaces
adjacent risks the operator should know about before adding new units.

## Units inventoried

| Unit | Purpose | Lifetime |
|---|---|---|
| `ict-trader-live.service` | The strategy multiplexer + order pipeline | long-running, `Restart=always` |
| `ict-telegram-bot.service` | Telegram UI process (handlers + polling) | long-running, `Restart=always` |
| `ict-web-api.service` | Read-only dashboard API on `127.0.0.1:8001` | long-running, `Restart=always` |
| `ict-heartbeat.service` + `.timer` | Daily 13:00 UTC operator ping | one-shot, fires daily |
| `ict-git-sync.service` + `.timer` | Pulls origin/main every 5 min, restarts services on advance | one-shot, fires every 5 min |
| `claude-vm-runner@.service` | Per-invocation `/vm` Telegram-dispatched Claude session | one-shot, on-demand |
| `ict-env-check.service` | Env-vars sanity check | one-shot, on boot |

## Dependency graph

The actual `[Unit]` directives across the long-running services:

```
ict-trader-live   After=network-online.target           Wants=network-online.target  Restart=always RestartSec=10
ict-telegram-bot  After=network.target ict-trader-live  Wants=network-online.target  Restart=always RestartSec=15
ict-web-api       After=network-online.target ict-trader-live  Wants=network-online.target  Restart=always RestartSec=5
ict-heartbeat     After=network-online.target           Wants=network-online.target  (oneshot, no Restart)
ict-git-sync      After=network.target                  (oneshot, no Restart)
```

### What's there

- **`After=` on bot + web-api** — these names provide *ordering only*,
  not a dependency. `man systemd.unit`: "If a unit is started up at the
  same time as one of its `After=` units, then the After= unit is
  started first." They do **not** propagate failures.
- **No `Requires=`, `BindsTo=`, `PartOf=`** anywhere across the three
  long-running units. Confirmed by `grep -E "^(Requires|BindsTo|PartOf)="
  deploy/*.service` returning empty.
- `Restart=always` on all three long-running units → independent crash
  recovery. Different `RestartSec` values (`5` / `10` / `15`) means they
  won't herd-restart in lockstep after a network blip.

### What this means in practice

| Failure scenario | Cascading impact? |
|---|---|
| `ict-trader-live` crashes (e.g. exchange-API exception, bug) | ❌ no — bot + web-api stay up. systemd restarts trader after 10 s. |
| `ict-trader-live` won't start at all (env file missing, syntax error) | ❌ no — bot + web-api still start (just delayed by the `After=` ordering wait). |
| `ict-telegram-bot` crashes | ❌ no — trader + web-api unaffected. Operator loses the Telegram surface but live trading continues. |
| `ict-web-api` crashes | ❌ no — trader + bot unaffected. Dashboard returns 502 until restart. |
| `ict-git-sync.timer` fails to fire | ❌ no — running services keep running on their existing code; deploys just stop until the timer recovers. |

### One real coupling worth documenting

`ict-telegram-bot` and `ict-web-api` won't *start* until
`ict-trader-live` has at least *attempted* to start. If the trader is
in a long crash-restart loop on boot, the bot may be delayed by up to
~10 s + bot start-up. **This is by design** — the bot loads
`coordinator` state from the trader's working directory, and starting
the bot before the trader has its env loaded once was historically a
flaky timing path.

This is *ordering*, not a *dependency*. Once the trader has tried even
once, the bot starts and stays up regardless of what the trader does
next.

## Adjacent risks surfaced (not changed in this PR)

These are flagged for inclusion in a future architecture sprint, not
fixed here:

### R1 — Three different `EnvironmentFile=` paths

- `ict-trader-live` → `/home/ubuntu/ict-trading-bot/.env`
- `ict-telegram-bot` → `/home/ubuntu/ict-trading-bot/.env`
- `ict-web-api` → `/etc/ict-trader/web-api.env`
- `ict-heartbeat` → `/home/ubuntu/ict-trading-bot/.env.live`
- `claude-vm-runner@` → `/etc/ict-trader/claude.env`

Five distinct env locations. An operator updating credentials has to
remember which file each service consumes. Worth consolidating to a
single `/etc/ict-trader/*.env` tree in a future cleanup so a credential
rotation touches one path.

Already on the operator-action list in `docs/claude/bug-log.md` BUG-004
(OAuth token rotation) — this is the same shape.

### R2 — `ict-web-api` has `WorkingDirectory=/opt/ict-trading-bot`

Every other unit uses `/home/ubuntu/ict-trading-bot`. The web-api unit
documents *"Save to /etc/systemd/system"* but points at `/opt`. If
that's not a symlink on the live VM, the web-api would fail to start.
**Operator: please verify** with `systemctl status ict-web-api` on the
VM. If `/opt/ict-trading-bot` doesn't exist, change the unit's
`WorkingDirectory` to match the rest, OR create the symlink.

This wasn't observable from inside the sandbox (no VM access). Logging
as an explicit operator-check item rather than auto-fixing because the
right answer depends on what the live VM actually has installed.

### R3 — No `WatchdogSec=` anywhere

If a process hangs (e.g. blocked on a network read with no timeout)
without crashing, systemd will NOT restart it — `Restart=always` only
fires on process exit. Hangs would manifest as the bot being
unresponsive while `systemctl is-active` reports `active`.

Adding `WatchdogSec=` requires the process to call
`sd_notify(WATCHDOG=1)` periodically; that's a small SDK integration
change in each unit. Worth doing eventually but not free.

Mitigation today: the `/health` command (S-016 H2) surfaces
`runtime_status.json (last tick)` mtime — if it stops advancing while
the trader is "active", that's a hang signal the operator can spot
manually.

### R4 — `ict-git-sync.timer` 5-min cadence

Already partially fixed by BUG-008 (S-014.5 PR #188): a no-op pull no
longer restarts the services. But the *deploy_pull_restart.sh* script
itself still runs every 5 min, and it now also fires the
`notify_on_pull.py` ping fanout (S-016 H3). If the script ever takes
> 5 min on the VM (e.g. because pip install hangs), the timer's next
fire will overlap.

Mitigation: `Type=oneshot` on `ict-git-sync.service` means systemd
won't start a second instance while the first is running. So in
practice this is safe; documenting in case future work changes the
unit type.

## H4 deliverable summary

- This audit doc, committed to
  `docs/audit/2026-04-30-unit-independence.md`.
- **Verification result:** units are independent. No cascading
  failures between the three long-running units.
- 4 adjacent risks (R1..R4) flagged for future work — none are
  blocking for the current housekeeping sprint.
- One **operator-check item** (R2 — web-api WorkingDirectory) that the
  operator should verify on the live VM. Logging as explicit handoff
  rather than auto-fixing.

Self-merge per the housekeeping plan: pure docs, no code changes.
