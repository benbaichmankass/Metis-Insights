# Live-VM migration: x86 micro → Ampere A1.Flex (memory relief)

**Status:** active plan (2026-06-14). **Driver:** the live `E2.1.Micro` (2 vCPU /
**1 GB**) hit **90%+ memory with `kswapd` active** — memory, not CPU, is the
binding constraint (loadavg ~1.2 on 2 cores; the whole bot stack is only
~240 MB, so 1 GB is just too small for the grown trader + web-api + sidecars).
This migrates the live trader onto a roomier **Ampere A1.Flex** while staying
**Always-Free ($0)**.

> This is a money-path migration. Phases 1–2 are **non-trading and reversible**
> (they never touch the running trader on the micro). **Phase 3 (cutover) is the
> only money-path-down step** and is operator-gated for downtime coordination.

## Free-tier ceiling math (the load-bearing constraint)

Oracle Always-Free Ampere A1 = **4 OCPU / 24 GB tenancy-wide.** Current usage:

| VM | Shape | OCPU | RAM |
|---|---|---|---|
| Trainer (`ict-trainer-vm`) | A1.Flex | 1 | 6 GB |
| IB Gateway (`ict-ib-gateway`) | A1.Flex | 1 | 6 GB |
| **Remaining headroom** | — | **2** | **12 GB** |

→ The live Ampere VM must be **≤ 2 OCPU / 12 GB** to stay free. Target
**2 OCPU / 12 GB** — fills Ampere to exactly 4/24 ($0) and gives 12× the
micro's RAM. (An earlier note said "3 OCPU / 18 GB"; that predates the gateway
moving onto Ampere and would put the tenancy at 5 OCPU / 30 GB — over the
ceiling. The `provision_training_vm.py` quota guard would reject it anyway.)
The x86 micro is a **separate AMD Always-Free allocation**, so retiring it frees
no Ampere budget either way.

## Toolchain (all issue-label-driven, OCI creds in repo secrets)

| Workflow | Label | Role |
|---|---|---|
| `provision-live-vm.yml` | `provision-live-vm` | create the Ampere candidate (`OCPUS=2 MEMORY_GB=12`, `deploy/live-arm-cloud-init.yaml`; quota-guarded) |
| `arm-candidate-diag.yml` | `arm-candidate-diag` | inspect the candidate (verify aarch64 wheels, services, mounts) |
| `deploy-candidate.yml` | `deploy-candidate` | deploy the bot stack to the candidate |
| `vm-resize-live.yml` | `vm-resize-live` | in-place A1.Flex shape change (only AFTER live is on Ampere; not the x86→ARM step) |
| `terminate-instance.yml` | `terminate-instance` | terminate the micro after the soak |

## Phase 0 — pre-flight verification (do first; no mutations)

1. **Confirm `/data/bot-data` is a separate block volume** (detachable), not a
   directory on the boot volume. The `ict-trader-live` drop-in has
   `RequiresMountsFor=/data/bot-data` + `After=data-bot\x2ddata.mount`, which
   indicates a dedicated volume — verify via `status-check` /
   `lsblk` + `oci bv volume list`. If it is NOT a block volume, the data
   migration is `rsync`, not detach/attach (see Phase 3 alt).
2. **Determine the public IP type — reserved vs ephemeral.** Run
   `oci network public-ip list`. **If reserved**, the IP can be *moved* to the
   new VM at cutover and the entire IP-ripple below disappears — strongly
   preferred. **If ephemeral**, the new VM gets a new IP and every reference
   below must be updated.
3. **IP-reference checklist** (only if the IP changes):
   - `vars.VM_SSH_HOST` (repo variable; defaults to `158.178.210.252` across
     diag/system-action/provision workflows)
   - Dashboard `BOT_API_URL` (Streamlit secret, `ict-trader-dashboard`)
   - This session's `DIAG_BASE_URL` (cloud-env var) for the direct diag path
   - Any hardcoded `158.178.210.252` in docs/scripts (`grep -rn`)
   - The gateway reaches the trader over the private subnet, and the trader
     reaches the gateway at `10.0.0.251` (private) — **unchanged** by a public
     IP move, since both stay on the same VCN/subnet.

## Phase 1 — provision the Ampere candidate (non-trading, reversible)

Fire `provision-live-vm` (issue body `confirm: yes`). It creates
`ict-bot-arm` at **2 OCPU / 12 GB**, boots `live-arm-cloud-init.yaml` (installs
python3.11 venv + bot requirements — proves the aarch64 wheels build — + Docker
+ clones the repo), and starts **no** live services. It does **not** touch the
micro. Expected terminal states: `ready` (good), `quota_would_exceed` (shape too
big — shouldn't happen at 2/12), `provisioning_failed` / `service_error`
(usually **"Out of host capacity"** — Always-Free Ampere is capacity-constrained;
just retry later). **Rollback:** `terminate-instance` on `ict-bot-arm`.

## Phase 2 — verify + stage (still non-trading)

1. `arm-candidate-diag` → confirm the venv built, deps import on aarch64, Docker
   up, repo present, `/home` has space.
2. `deploy-candidate` → lay down the systemd units + scripts (units installed
   but **not** started for live trading).
3. **Dry-run data check:** stage a *copy* of `trade_journal.db` on the candidate
   and confirm the trader boots in `LOOP=false` single-tick mode without errors
   (`STRATEGY`/`mode` resolves, WAL enables, DB readable on aarch64). Do **not**
   point it at the live exchange with live keys yet.

## Phase 3 — CUTOVER (⛔ operator-gated; money path down ~minutes)

Schedule a low-activity window (weekend / off-killzone). Then:

1. `set-account-mode` all live accounts → `dry_run` **OR** stop
   `ict-trader-live` on the micro (trader down — orders cease).
2. **Move the data** — preferred: `oci compute volume-attachment detach` the
   `/data/bot-data` block volume from the micro, `attach` to the candidate, mount
   at `/data/bot-data`. *Alt (boot-volume case):* `rsync -a` `/data/bot-data` →
   candidate, then re-point.
3. **Move the IP** — reserved: reassign the reserved public IP to the candidate's
   VNIC (zero reference changes). Ephemeral: bring the candidate up on its new IP
   and update every reference in the Phase-0 checklist.
4. Start `ict-trader-live` + `ict-web-api` on the candidate. **Verify:**
   heartbeat fresh, ticks current, `boot_audit` clean (0 stranded packages),
   `/api/diag/status` reachable, the dashboard renders, one real tick executes.
5. Restore live `mode` if you flipped it in step 1.

**Rollback (any step before "verify passes"):** re-attach the volume + IP to the
micro, restart `ict-trader-live` there. The micro is **not** terminated until
Phase 4, so rollback is always one volume+IP move away.

## Phase 4 — decommission the micro

After a clean soak (≥24–48 h on the candidate: heartbeat steady, trades
flowing, memory comfortably under the new 12 GB), fire `terminate-instance` on
the micro's display name. Update CLAUDE.md's topology table + this runbook's
status to "complete."

## What this fixes / what it doesn't

- **Fixes:** the 1-GB memory pressure (→ 12 GB), with headroom for the sidecars
  and a future **per-bar-scoring sidecar split** (which adds ~+85 MB and was
  explicitly deferred to land *after* this resize, not on the micro).
- **Doesn't change:** strategy logic, risk caps, the gateway (stays on its own
  Ampere VM at `10.0.0.251`), or the trainer. Pure infra.
