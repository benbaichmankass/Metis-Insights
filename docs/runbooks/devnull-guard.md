# Runbook — `/dev/null` guard (OCI agent strips its perms)

**Status: LIVE 2026-06-15.** Self-heals a recurring `/dev/null`
permission regression on the live trader VM (`ict-bot-arm`).

## Symptom

Non-root tooling on the live VM errors `bash: /dev/null: Permission denied`
on any `>/dev/null` / `2>/dev/null` redirect. The most damaging effect:
**auto-deploy silently wedges.** `scripts/deploy_pull_restart.sh` (run as
`ubuntu` by `ict-git-sync.timer`, under `set -euo pipefail`) aborts at its
first redirect — the `sudo -n systemctl --version >/dev/null 2>&1` sudo
probe (line ~28) — so it never fetches or restarts. The running trader then
pins to stale code.

This actually happened on **2026-06-15**: `ict-git-sync` failed every 5 min
from 06:13 UTC onward (`Cannot invoke systemctl` → exit 1), so a merged
monitor-routing fix (#3597) never reached the trader for ~16h, and MES/MGC/MHG
open positions ran unmonitored on the bot side.

## Root cause

`/dev/null` is the correct **character device** (major 1, minor 3) but its
**mode keeps getting reset to `0444`** — the write bit stripped for everyone.
Root processes are unaffected (root bypasses mode bits); only non-root users
(the trader + the deploy script, both `ubuntu`) hit EACCES.

Nothing in this repo chmods `/dev/null` (verified by grep). The culprit is an
**OS-level host agent** on the OCI image — suspected the
`oracle-cloud-agent` **`oci-wlp`** (workload-protection / file-integrity)
plugin "remediating" world-writable files, incorrectly including `/dev/null`.
It is **not** a boot/cloud-init issue (it recurs with no reboot) and it is
**not** a device-node recreation (the inode is unchanged — only the mode flips).

## The fix (this repo)

Defense in depth — all shipped, deploy via `ict-git-sync`:

1. **`ict-devnull-guard.{service,timer}`** (`deploy/`) — a root oneshot fired
   every 60 s that re-asserts `/dev/null` is the `1:3` char device with mode
   `0666`. Runs as root, so it checks the **mode bits via `stat`** (a root
   `[ -w ]` test is always true and useless). No-op + silent unless it drifted.
   Self-heals within ≤60 s — comfortably inside the 5-min git-sync cadence, so
   auto-deploy can never stay wedged on it again.
2. **`scripts/deploy_pull_restart.sh` self-heal** — restores `0666` at the top
   (best-effort `sudo -n chmod`) before any redirect, so even a deploy that
   races the guard recovers itself.
3. **`scripts/ops/_lib.sh::require_systemctl` self-heal (2026-06-29,
   BL-20260629)** — the operator-action wrappers (`pull-and-deploy` /
   `restart-bot-service` / `reboot-vm` via `system-actions.yml`) used to only
   *detect* a clobbered `/dev/null` and abort with an error telling the operator
   to SSH in and `mknod` by hand (an autonomy-contract violation: a runner holds
   `VM_SSH_KEY` and can repair it itself). They now self-heal in place like the
   deploy path — `sudo -n chmod 0666` (mode-strip variant) then
   `sudo -n sh -c 'rm -f /dev/null && mknod -m 666 /dev/null c 1 3'`
   (regular-file-clobber variant) — and only abort if `/dev/null` is *still*
   unwritable afterwards, pointing at the `vm-fix-devnull` workflow rather than
   a manual command.

### Guard `%`-specifier bug (found + fixed 2026-06-29, BL-20260629)

The guard's `ExecStart` ran `stat -c %t:%T` and `stat -c %a` **directly in the
systemd unit**. systemd expands `%`-specifiers in `ExecStart` *before* the shell
runs, so `%t`/`%T` became the runtime/tmp dirs and `%a` became the architecture
string — the tell-tale journal line was `chmod 0666 /dev/null (was arm64)`. The
drift checks (`!= "1:3"`, `!= "666"`) therefore **never matched**, so the guard
recreated + chmod'd `/dev/null` on **every** 60 s run unconditionally instead of
being a silent no-op, and it wasn't actually verifying health. Fixed by doubling
the specifiers (`%%t:%%T` / `%%a`) so systemd un-escapes them to the intended
`stat` formats. The guard still kept `/dev/null` a valid `1:3` device each
minute, so this was noise + a needless rm/mknod per minute, not an outage — but
it masked that the guard's detection was inert.

## Manual repair (one-shot, if ever needed)

`vm-fix-devnull` workflow (label `vm-fix-devnull`) does
`sudo rm -f /dev/null && sudo mknod /dev/null c 1 3 && sudo chmod 666 /dev/null`
+ verify. Use it for an immediate fix; the guard timer keeps it fixed.

## Killing it at the source (operator, optional)

The durable guard makes us resilient regardless, but to stop the perms-strip at
its origin, check the OCI **Cloud Guard / Workload Protection** config (or
whatever FIM/hardening profile is attached to the instance) for a rule that
"remediates" world-writable files and exclude `/dev`. To positively identify
the writer on the VM: `sudo auditctl -w /dev/null -p a -k devnull` then
`sudo ausearch -k devnull` after it next flips. (Requires root shell access —
not available through the restricted live-VM relays.)
