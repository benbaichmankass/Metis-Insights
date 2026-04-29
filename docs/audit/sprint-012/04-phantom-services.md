# § 4 — Phantom service investigation

> **Symptom (2026-04-29 Telegram output):**
> ```
> ✅ ict-trader-live started. Status: active
> ❌ Failed to start ict-trader-bak: Unit ict-trader-bak.service not found.
> ❌ Failed to start ict-trader-example: Unit ict-trader-example.service not found.
> ```

## 4.1 Repo-wide search

`grep -rn "ict-trader-bak\|ict-trader-example\|trader-bak\|trader-example" .`
across the working tree (excluding `.git/`):

- `docs/sprints/sprint-012-prompt.md:18-19` — the prompt text describing
  the symptom.

That is the **only** match. No code, config, test, doc, or fixture in the
current tree references either name.

## 4.2 Git history search

```
git log --all -p -S "ict-trader-bak"
git log --all -p -S "ict-trader-example"
```

No commits introduce, modify, or remove those strings. The names have
**never been in the repo**.

## 4.3 Telegram start-services handler

The bot file is `src/bot/telegram_query_bot.py`. The relevant code path:

- `LIVE_SERVICE_NAME = "ict-trader-live"` (line 36)
- `get_service_status(service_name)` (line 215) — wraps
  `systemctl is-active <service_name>`.
- `toggle_service(service_name, action)` (line 222) — wraps
  `sudo systemctl <action> <service_name>`.
- `cmd_toggle(update, context)` (line 750) — the `/toggle` handler.

`cmd_toggle` flow (lines 750-769):
1. `accounts = dl.list_accounts()`.
2. If `accounts` is empty → toggle `LIVE_SERVICE_NAME` only.
3. Else iterate accounts, calling `toggle_service(svc, action)` where
   `svc = acc.get("service") or LIVE_SERVICE_NAME`.

The only service names this can ever try to toggle are:
- `LIVE_SERVICE_NAME` (constant), and
- per-account `service` field from `units.yaml::accounts[*].service`.

Today `units.yaml::accounts[*]` has one entry (`live`) with **no `service`
field**, so `svc` falls back to `ict-trader-live`. There is no path inside
this repo that produces the names `ict-trader-bak` or `ict-trader-example`.

## 4.4 Where the phantom output comes from (hypotheses)

Since the names exist nowhere in the repo, the source must be **VM-side
state outside the repo**. Plausible sources, in decreasing likelihood:

1. **Manual `systemctl start ict-trader-bak ict-trader-example`** typed in
   shell (perhaps a dotfile, alias, or operator habit) and surfaced via
   `journalctl` or a wrapper script. Telegram output then echoes the
   underlying systemctl error verbatim.
2. **A sibling Telegram bot or wrapper script not in this repo** —
   e.g. `~/bin/start_all.sh` on the VM that hardcodes a list including
   `ict-trader-bak` and `ict-trader-example`.
3. **Stale systemd `*.wants/` symlinks** under
   `/etc/systemd/system/multi-user.target.wants/` pointing at unit files
   that no longer exist. `systemctl start` invoked by name still surfaces
   "Unit not found".
4. **A snapshot of an older Telegram bot** running on the VM (predating
   commit `e37e60a`) with hardcoded service names. The current bot file
   does not match the symptom, so the bot the PM interacted with may not
   be the one in this repo's HEAD.

## 4.5 Recommended action

This investigation is at the limit of repo-side evidence. **PM input
required (decision-request item #5 in § 8)**: please run on the VM and
share output:

```bash
# 1. Find any unit files (real, masked, or symlinks) referencing the names
sudo find /etc/systemd /lib/systemd -iname "*trader*bak*" -o -iname "*trader*example*"
sudo systemctl list-unit-files | grep -Ei 'trader-(bak|example)'

# 2. Find any wrapper script with the names hardcoded
sudo grep -rn "ict-trader-bak\|ict-trader-example" /usr/local/bin /home/ubuntu /etc 2>/dev/null

# 3. Recently-failed systemd jobs
journalctl --since "1 day ago" | grep -Ei 'trader-(bak|example)'

# 4. Confirm the running Telegram bot's source path
systemctl cat ict-telegram-bot.service | grep ExecStart
ps -ef | grep telegram_query_bot
```

## 4.6 Repo-side regression test (PR D3)

Independent of where the phantoms originate, PR D3 hardens the start-services
handler so the symptom cannot recur from inside the repo:

- The handler reads the registry (`strategy_registry` post-D2 — or
  `units.yaml::accounts[*]`), and **fails loudly** if any service it is
  about to toggle has no corresponding unit file in `deploy/` or no entry
  in the active config.
- A new test asserts: any service name issued by `cmd_toggle` is one of
  the names in `deploy/*.service`. Hardcoded lists outside the registry
  are forbidden.

That test will not catch VM-only phantoms (out of repo by definition), but
it pins the contract for any future bot drift.
