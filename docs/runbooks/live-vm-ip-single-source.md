# Live VM IP — single source of truth

The live trader's address must live in **one** place, so a VM move (or any
future IP change) is a single edit, not a sweep across ~100 files. This is the
config-layer companion to the **reserved static IP**
(`docs/runbooks/reserved-ip-stable-egress.md`), which makes the *value itself*
stop changing. Together: the value never changes (reserved IP), and if it ever
must, you change it once (this).

## The single source: the `VM_SSH_HOST` repo variable

Every live-VM workflow resolves its target as:

```yaml
VM_SSH_HOST: ${{ vars.VM_SSH_HOST || '141.145.193.91' }}
```

- **`vars.VM_SSH_HOST`** (a repo **Actions variable** — *not* a secret) is the
  single source of truth. **Set it once** and every workflow uses it; the
  hardcoded literal is then just a safety net that is never consulted.
- The hardcoded fallback exists only for when the variable is unset. As of the
  2026-06-14 Ampere cutover all live-VM fallbacks point at **`141.145.193.91`**
  (they previously pointed at the retired micro `158.178.210.252`, which — once
  the micro was stopped — silently broke any workflow that fell through to the
  fallback; fixed in the single-source PR).

### Operator: set the variable (one-time, ~20s)

```bash
gh variable set VM_SSH_HOST --repo benbaichmankass/ict-trading-bot --body '141.145.193.91'
```

or **Settings → Secrets and variables → Actions → Variables → New variable**:
`VM_SSH_HOST = 141.145.193.91`. After this, a future IP change is **one** edit
of that variable (and the dashboard `BOT_API_URL` + session `DIAG_BASE_URL`,
which live outside this repo's Actions). With a reserved IP adopted, even that
never needs to change.

## Consumers of `vars.VM_SSH_HOST` (live trader)

All repointed to the `141.145.193.91` fallback and wired to the variable:
`provision-live-vm`, `provision-gateway-vm`, `provision-ib-gateway`,
`provision-training-vm`, `provision-training-vm-auto-retry`,
`terminate-instance`, `health-snapshot`, plus the diag/system-action relays
already repointed in PR #3581 (`vm-diag-snapshot`, `system-actions`,
`vm-web-api-recover`, `sync-vm-secrets`, `get-diag-token`, `set-diag-token`).

`cutover-live.yml`'s `MICRO_HOST` intentionally stays `158.178.210.252` — there
it is the migration **source**, not the live target.

## Known gaps (deliberately NOT blind-repointed — need a proper review)

These reference the old micro but a blind IP swap would be wrong; tracked for a
follow-up:

- **`oci-storage.yml` / `oci-storage-verify.yml`** — pin the **micro's instance
  OCID** (`VM_INSTANCE_OCID`) alongside `VM_HOST`, and concern the old block-volume
  topology. Post-cutover `/data/bot-data` is a boot-volume directory on the
  Ampere VM (no separate block volume), so these are likely stale wholesale, not
  just IP-stale. Needs a topology review, not an IP edit.
- **Gateway workflows** — `vm-ib-gateway-recover`, `vm-ib-gateway-watchdog-enable`,
  `vm-ib-gateway-stop`, `vm-cloud-open-ib-port` default `VM_SSH_HOST` to the micro.
  Post gateway-isolation (2026-06-10) the gateway lives on its **own** VM
  (`10.0.0.251`, private), so the right target is a dedicated **gateway** host
  variable (e.g. `GATEWAY_SSH_HOST`), not the trader's `VM_SSH_HOST`. Repointing
  them to the trader would run gateway docker ops on the money box. Needs its own
  change to introduce the gateway host var.
