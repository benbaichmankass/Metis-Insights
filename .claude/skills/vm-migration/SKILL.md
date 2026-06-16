---
name: vm-migration
description: Migrate or decommission a production OCI VM (live trader, trainer, or IB gateway) — provision a candidate, cut over, retire the old box — without leaving loose ends. Use when the operator says "migrate the VM", "move the live trader", "resize the VM", "decommission/terminate the old box", or any change that swaps a VM's identity or public IP. Wraps the runbook + the 2026-06-14 live→Ampere retrospective: its hard lesson is that the *environment contract* (egress IP → broker allowlists, host references, storage topology, decommission hygiene) breaks, not the box copy. Composes with vm-ops, git-actions, diag-data, credentials-and-vm-mutations.
---

# /vm-migration — move or retire a production VM cleanly

Migrating a VM is **not** a box copy. The mechanical part (provision → verify
wheels → dry-boot → copy data → start) is reliable. **Every** incident in the
2026-06-14 live→Ampere cutover came from things keyed to the OLD VM's
*identity* — its egress IP, host references, storage topology, and decommission
hygiene — none of which a box copy touches. Treat the **environment contract**
below as the real work.

Canonical procedure (phases, rollback): [`docs/runbooks/live-vm-migration-ampere.md`](../../../docs/runbooks/live-vm-migration-ampere.md).
Full retrospective: [`docs/sprint-logs/S-VM-CUTOVER-RETRO-2026-06-15.md`](../../../docs/sprint-logs/S-VM-CUTOVER-RETRO-2026-06-15.md).

## Do this BEFORE the next switch (don't wait for the retrospective)

The single biggest fragility is that the live VM runs on an **ephemeral** OCI
public IP, so a move forces a new address and breaks every external reference.
**Build the "one-switch" setup firmly BEFORE you migrate, not afterward:**

- **Adopt a reserved IP first** (`reserve-live-ip.yml`: `allocate` → operator
  binds it on Bybit → `assign`). `cutover-live.yml` then moves that SAME address
  to the new box → **zero external ref changes**. OCI **cannot** convert an
  existing ephemeral IP in place (reserving = a new address + a brief swap +
  re-binding Bybit), so the time to do it is during a calm window before the
  move — not mid-cutover.
- **OR** put the bot API behind a **DNS hostname** and point every consumer at
  the name, so an IP change is one A-record edit. A GitHub Actions Variable/Secret
  does **NOT** propagate to the dashboard / Android / `DIAG_BASE_URL` / trainer —
  those are separate platforms; only a reserved IP or a DNS name single-sources
  across all of them.

If neither is in place, accept that the move will touch each platform once and
enumerate them up front (next section).

## The environment-contract checklist (each line is a real 2026-06-14 failure)

1. **Egress IP → broker allowlists.** The new VM's egress IP must be on every
   broker API-key IP allowlist BEFORE/at cutover (operator-only, broker-side).
   Missing it blinded real-money `bybit_2` with `ErrCode 10010`
   (`BL-20260614-BYBIT-IP`). Binding alone isn't enough — **restart
   `ict-trader-live` + `ict-web-api`** after, since they cache the key in
   `os.environ`.
2. **Host references** — `vars.VM_SSH_HOST` (a repo **Variable**, not a Secret —
   a Secret resolves empty in `vars.*` and silently drops to the hardcoded
   fallback, the cutover bug), dashboard `BOT_API_URL`, session `DIAG_BASE_URL`,
   trainer `LIVE_VM_IP` drop-ins, IB-gateway recovery/MES-pull hosts. Verify the
   SoT actually resolves; don't trust the fallback.
3. **Decommission hygiene — "stop the trader" ≠ "stop the box".** Watchdogs,
   `ict-web-api`, and every Bybit-calling timer revive or keep calling from the
   old (de-allowlisted) IP — the **micro-zombie** that spammed 10010 and masked
   the diagnosis (`BL-20260615-MICRO-ZOMBIE`). Disable the ENTIRE `ict-*` fleet
   (watchdogs/git-sync first) or power the box off.
4. **Deploy/observability portability** — in dry-boot confirm: the installer
   doesn't wedge on storage topology (mount vs boot-volume dir →
   `data-dir-nomount.conf`); `enable --now` doesn't start units that belong on
   another box (gateway timers → `/etc/ict-vm-role`); auto-deploy actually finds
   + restarts units (`list-units 'ict-*'` returned 0 matches on Ampere → silent
   no-op); the full observability/self-heal fleet + every recovery workflow's
   target host are present and re-verified.
5. **Latent-bug amplification** — a clean rebuild surfaces bugs the old box hid
   (a dep installed only by hand; `/dev/null` perms stripped by an OCI FIM agent;
   monitor candle fetch hardcoded to one exchange; paper trades polluting
   real-money PnL). A periodic **fresh-VM rehearsal** catches these without a
   real money-path outage.

## Tooling (all issue-label-driven; OCI creds in repo secrets)

| Workflow / label | Purpose |
|---|---|
| `provision-live-vm` | create the Ampere candidate (quota-guarded) |
| `arm-candidate-diag` | verify aarch64 wheels / services / mounts on the candidate |
| `deploy-candidate` | lay down the unit fleet (not started for live) |
| `cutover-live` (`cutover-live`) | Phase 3 money-path cutover; dry-run first (prints IP type + plan, no mutation) |
| `reserve-live-ip` (`reserve-live-ip`) | `describe` / `allocate` / `assign` / **`release`** a reserved public IP |
| `terminate-instance` (`terminate-instance`) | **`mode: list`** (read-only enumerate: id/name/state/shape/public_ip) → terminate by **`instance_id:` (OCID, rename-proof)** or `display_name:` (exact), `confirm: yes` gated |
| `stop-micro-zombie` (`stop-micro-zombie`) | disable the ENTIRE `ict-*` fleet on a retired box |

**Decommission an old box (no human needed):** dispatch `terminate-instance`
with `mode: list` to get the exact OCID (match on `public_ip` / `shape`), then
dispatch `terminate-instance` with `instance_id: <ocid>` + `confirm: yes`.
Terminate by **OCID**, not display name — a box's OS hostname ≠ its OCI display
name (the 2026-06-15 `not_found` lesson: the micro's hostname was
`instance-20260414-1555` but its display name was `ict-bot`).

## After any migration

- Update `CLAUDE.md` topology table + the runbook status + write a sprint log
  (`sprint-format`). Run `doc-freshness`. Log any leftover to the
  health-review backlog.
- Re-verify the observability units actually ran (e.g. the daily
  `ict-heartbeat` digest fired) and the dashboard renders against the new host.
