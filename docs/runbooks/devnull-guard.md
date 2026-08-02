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
**OS-level host agent** on the OCI image, but **it is not yet attributed** — the
long-standing `oracle-cloud-agent` **`oci-wlp`** guess was *disproven* on
2026-08-02 (see "Culprit NOT attributed" below: oci-wlp is disabled+exited, no
reboot, yet the clobber recurs). It is **not** a boot/cloud-init issue (it
recurs with no reboot) and it is **not** a device-node recreation (the inode is
unchanged — only the mode flips). The mode-strip is invisible to every b64 chmod
syscall class auditd has watched across 7 forensic rounds.

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

## Culprit NOT attributed — `oci-wlp` was EXONERATED (2026-08-02)

**History + correction.** On 2026-07-28 `oci-wlp` "Cloud Guard Workload
Protection" was *circumstantially* named the culprit (inspect issue #7831), the
operator disabled it in the OCI Console, and an 8.75h clean soak was read as a
confirmed source-kill (`BL-20260629` marked resolved). **That attribution was
wrong.** The clobber recurred on the 2026-08-02 deploy (#8330/#8339), and a
round-8 deep inspect (`vm-devnull-source-diagnose` enhanced form, PR #8348;
issues #8346/#8349) proved `oci-wlp` is not the cause:

- **`oci-wlp` is disabled and exited, continuously.** Every 10-min OCA health
  check (`/var/log/oracle-cloud-agent/agent.log`) reports
  `policy.go:100: Plugin [oci-wlp] has [Disable] desired state`,
  `currentState:[exited]`, `No process found for oci-wlp`. The OCI-Console
  disable from 2026-07-28 **held and propagated** — the control-plane policy
  overrides the shipped `agent.yml` `oci-wlp.disabled: false` default (that flag
  is a **red herring**; the live desired-state is `Disable`).
- **No FIM / scan / workload-protection process is running at all** (`ps`). The
  only *running* OCA plugins are `gomon` (metrics) + `unifiedmonitoring`
  (Fluentd) — and `unifiedmonitoring` is itself a **victim** of the clobber
  (`open /dev/null: permission denied`), so it cannot be the mutator.
- **No reboot** (uptime ~3w, boot 2026-07-10) — so it is not a reboot
  re-enabling a console-disabled plugin.
- Snap confinement is `classic` (a plugin *could* chmod the host `/dev/null`) —
  but the only plausible one (`oci-wlp`) is off.

So `oci-wlp` has been off continuously with no reboot, yet the clobber returned.
The 8.75h "clean soak" was coincidence (the clobber is intermittent, ~1–2×/day).
After **7 prior syscall-audit rounds** (chmod/fchmod/fchmodat/fchmodat2/setxattr/
mount all silent) + udev + this deep inspect, the true `/dev/null` mutator
**remains unattributed**, and no FIM process is even running to blame. The
mode-strip is an in-place chmod (inode + mtime unmoved), invisible to every b64
syscall class audited so far.

### Durable mitigation (source-kill blocked on attribution)

Because the mutator cannot be named, there is no targeted source-kill to apply
(disabling `oci-wlp` was tried and did nothing; disabling the whole
`oracle-cloud-agent` would lose OCI metrics on an *unproven* attribution — not
justified). The accepted **permanent mitigation** is therefore the existing
defense-in-depth, which fully contains the symptom:

- **`ict-devnull-guard.timer`** re-asserts `/dev/null` = `1:3` mode `0666` on a
  short cadence (belt).
- **Per-consumer `heal_devnull`** re-heals in-flight before every load-bearing
  redirect (deploy path, operator-action wrappers, MES pull) — so nothing breaks
  even mid-clobber (suspenders).

Optional, if a definitive attribution is wanted: a **fanotify `FAN_ATTRIB`**
watcher soak names the pid/comm/exe on the next attribute change regardless of
syscall path (the one net not yet tried) — a Tier-2 observing process on the
live VM, multi-hour soak. Tracked in `BL-20260629-DEVNULL-OCI-SOURCE-KILL`.
