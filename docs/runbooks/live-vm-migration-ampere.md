# Live-VM migration: x86 micro → Ampere A1.Flex (memory relief)

**Status:** ✅ **CUTOVER COMPLETE (2026-06-14); micro DECOMMISSIONED (2026-06-16).**
The live trader runs on the Ampere candidate `ict-bot-arm` (`141.145.193.91`); the
retired x86 micro (`ict-bot`, `158.178.210.252`) was terminated 2026-06-16 via
`terminate-instance` by OCID after a clean soak. See "Cutover completed" at the
bottom for the verified post-state + remaining follow-ups. The plan below is
retained as the procedure record. **Original driver:** the live `E2.1.Micro` (2 vCPU /
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

## Lessons from the 2026-06-14 move — the environment contract (fold into ANY future move)

The mechanical migration (provision → verify wheels → dry-boot → data copy →
start) went fine. **Every** incident came from things keyed to the OLD VM's
*identity* that weren't enumerated before going live. Treat this as the
pre-cutover checklist; each line is a real 2026-06-14 failure:

1. **Egress IP → broker allowlists.** The new VM's egress IP must be added to
   **every** broker API-key IP allowlist BEFORE/at cutover. Missing this blinded
   real-money `bybit_2` with `ErrCode 10010` from cutover (`BL-20260614-BYBIT-IP`).
   Broker-side + operator-only — schedule it. Binding is necessary but **not
   sufficient**: restart `ict-trader-live` + `ict-web-api` after binding (they
   cache the key in `os.environ`).
2. **Prefer a RESERVED IP** (`reserve-live-ip.yml`). A reserved IP belongs to the
   role, not the box, so `cutover-live.yml` moves it with **zero external ref
   changes** and items 1/3/4 below largely disappear. An ephemeral IP forces a
   new address on every move.
3. **Host references.** `vars.VM_SSH_HOST` (and its hardcoded fallbacks across
   ~100 workflows), dashboard `BOT_API_URL`, session `DIAG_BASE_URL`, trainer
   `LIVE_VM_IP` drop-ins, and the IB-gateway recovery/MES-pull hosts all point at
   an IP. `vars.*` resolved **empty** at cutover, so everything used the dead
   fallback — verify the SoT actually resolves, don't trust the fallback.
4. **Decommission hygiene — "stop the trader" ≠ "stop the box".** Watchdogs,
   `ict-web-api`, and every timer that calls a broker will revive or keep calling
   from the old (now de-allowlisted) IP — the **micro-zombie** that spammed 10010
   into the shared Telegram channel and masked the diagnosis for hours
   (`BL-20260615-MICRO-ZOMBIE`). Use the `stop-micro-zombie` workflow (disable the
   ENTIRE `ict-*` fleet, watchdogs/git-sync first) or power the box off.
5. **Deploy/observability portability.** Confirm on the new box, in dry-boot,
   that: the installer doesn't wedge on storage topology (mount vs boot-volume
   dir — `data-dir-nomount.conf`); `enable --now` doesn't start units that belong
   on another box (gateway timers — `/etc/ict-vm-role`); the auto-deploy actually
   *finds and restarts* units (`list-units 'ict-*'` returned 0 matches on Ampere →
   silent no-op); and the full observability/self-heal fleet + every recovery
   workflow's target host are present and re-verified.
6. **Latent-bug amplification.** A clean rebuild surfaces bugs the old box hid
   (`pybit` in no requirements file; `/dev/null` stripped by an OCI FIM agent;
   monitor candle fetch hardcoded to Bybit; `ib_paper` polluting real-money PnL).
   A periodic **fresh-VM rehearsal** catches these without a real money-path
   outage.

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
4. **Record the OLD box's OCI display name** (`oci compute instance list` /
   console) in the cutover issue AND in Phase 4 below. `terminate-instance`
   matches on the exact display name; if it isn't written down, decommission
   stalls. (The 2026-06-14 micro's display name was never recorded — a real
   gap that blocked its later termination.)

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
   **⚠️ Stopping `ict-trader-live` alone is NOT enough — `stop + disable` the
   ENTIRE `ict-*` unit fleet on the old VM** (`systemctl disable --now` every
   `ict-*.service` + `ict-*.timer`, **watchdogs/`git-sync` first** so they can't
   re-arm). The timers (`ict-hourly-snapshot`, `ict-insights-generator`,
   `ict-heartbeat`, …) each call Bybit account endpoints, and `ict-web-api`
   keeps answering `:8001`. If left running, the old VM becomes a **zombie**:
   it spams `bybit_2` `ErrCode 10010` from its now-unbound IP to the shared
   Telegram channel and serves stale data to the Android app — the
   `BL-20260615-MICRO-ZOMBIE` incident. Use the `stop-micro-zombie` workflow
   (label `stop-micro-zombie`) which does exactly this, or power the VM off.
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

## Cutover completed — verified post-state (2026-06-14)

Live trader is now `ict-bot-arm` (`141.145.193.91`), `VM.Standard.A1.Flex`
**2 OCPU / 12 GB**, aarch64. Ampere pool is full: trainer (1/6) + gateway (1/6)
+ live (2/12) = **4 OCPU / 24 GB**. Verified this session:

- **Money path:** `ict-trader-live` / `ict-web-api` / `ict-liveness-watchdog`
  active; fresh `/data/bot-data/runtime_logs/heartbeat.txt`; Bybit live (retCode
  0), open positions intact. (`/data/bot-data` is a **boot-volume directory, not
  a mount** — units use the env-only `data-dir-nomount.conf` drop-in.)
- **Relays repointed** (PR #3581): all live-trader workflow `VM_SSH_HOST`
  fallbacks → `141.145.193.91` (the `vars.VM_SSH_HOST` repo variable resolves
  empty at runtime, so the hardcoded fallback is the live target).
- **Trainer→live ML mirror** repointed via `LIVE_VM_IP=141.145.193.91` systemd
  drop-ins on `ict-trainer-publish` + `ict-promotion-readiness` (trainer reaches
  the candidate with the existing `ict-bot-ovm` key). Verified publishing to
  `…@141.145.193.91:/data/bot-data/runtime_logs/trainer_mirror`.
- **Observability + self-heal restored** on the candidate (were missing):
  installed base units (no mount drop-in) for `ict-insights-generator(+-strategies)`,
  `ict-health-snapshot`, `ict-hourly-snapshot`, `ict-heartbeat`,
  `ict-web-api-watchdog`; all timers active; insights + health write to `/data`.
- **Deploy tooling** made mount-topology-aware (PR #3588) so a future deploy
  won't wedge the candidate.

### Remaining follow-ups

Most closed 2026-06-14 (same-day follow-up session):

1. ✅ **`ict-git-sync` re-enabled on the candidate** (2026-06-14) — auto-deploys
   from `main` every 5 min. The candidate was fast-forwarded to `main` first, so
   the first sync was a no-op (no trader restart). The IB-gateway timers
   (`ict-ib-gateway-watchdog`, `ict-ib-gateway-reset`) are **masked** on the
   candidate so the installer's blanket `enable --now` can't activate them on the
   trader box — they belong on the dedicated gateway VM (see
   `BL-20260614-INSTALLER-GATEWAY-TIMERS`).
2. ✅ **`ib_insync` was never missing** — `ib_insync 0.9.86` is in the trader venv
   (`.venv`) and MES/MGC/MHG trade on live IB data. The earlier "not installed"
   alarm came from `ict-health-snapshot` running under `/usr/bin/python3` (system),
   not the venv — cosmetic, tracked as `BL-20260614-HEALTHSNAP-PY`. The
   investigation surfaced + fixed a real pre-existing bug: the order monitor
   fetched IB candles from Bybit (PR #3597, per-symbol connector routing).
3. ✅ **`ict-shadow-log-rotate`** enabled + the `DATA_DIR` gap fixed (PR #3596) so
   it rotates the real `/data/bot-data` log.
4. **Optional dedicated `/data` block volume** for the candidate (today it's a
   boot-volume dir; fine, but a separate volume matches the micro's posture).
5. ✅ **Micro decommissioned (2026-06-16).** `terminate-instance` terminated the
   micro (`ict-bot`, OCID `…anrwiljrnpsiupacbpuojiksc3dl4twmqlxndtc36jgx5sjq3452vflqr32q`,
   `158.178.210.252`) **by OCID** after a clean soak (`BL-20260615-DECOMMISSION-MICRO`,
   resolved). NB the terminate was done by **OCID, not display name** — the micro's
   OS hostname (`instance-20260414-1555`) ≠ its OCI display name (`ict-bot`), so a
   hostname-derived name returned `not_found` first. The `terminate-instance`
   workflow gained a read-only `mode: list` + an `instance_id:` (OCID) path for
   exactly this; see the `vm-migration` skill.
6. ⚠️ **Bybit API-key IP allowlist must include the new VM egress IP**
   (`BL-20260614-BYBIT-IP`, surfaced 2026-06-14 ~18:37 UTC). **This step was
   MISSING from the cutover checklist and caused a real-money outage on
   `bybit_2`.** The Bybit API keys (`BYBIT_API_KEY_2` real-money, and
   `BYBIT_API_KEY_1` demo) have a **bound-IP allowlist** that was tied to the
   micro's IP `158.178.210.252`; after the cutover the trader calls from the
   Ampere VM's egress IP `141.145.193.91`, which Bybit rejects with
   `ErrCode 10010 "Unmatched IP, please check your API key's bound IP addresses"`
   on `get_positions` / `get_order_status` (and any order placement) — i.e. the
   `bybit_2` money path was effectively blind since cutover. **This is an
   operator-only, broker-side action** (no workflow can edit Bybit's API-key IP
   settings — it lives behind the operator's Bybit login): on Bybit → API
   Management → edit the key bound to `BYBIT_API_KEY_2` → **add** `141.145.193.91`
   to the bound-IP list (Bybit allows multiple IPs). **Keep `158.178.210.252` in
   the list during the 24–48h rollback soak** so a rollback still works; drop it
   when the micro is decommissioned (item 5). Repeat the check for
   `BYBIT_API_KEY_1` (demo, `api-demo.bybit.com`). Native exchange-side SL/TP
   brackets keep protecting open positions while the API IP is mismatched (they
   fire on Bybit, not via the client), but the bot cannot reconcile, monitor, or
   open new `bybit_2` trades until the IP is bound. **Add this step to any future
   VM-migration checklist** — egress IP changes whenever the live VM moves.
   **⚠️ Binding the IP is necessary but NOT sufficient — after rebinding on
   Bybit, RESTART `ict-trader-live` + `ict-web-api`** (`restart-bot-service` +
   `vm-web-api-recover`). Both hold `BYBIT_API_KEY_2` in `os.environ` from their
   last start and never re-read creds at runtime, so a process started before the
   binding was corrected keeps failing `10010` indefinitely (`BL-20260614-BYBIT-IP`).
   Verify with the `vm-bybit-diag` workflow (reproduces the bot's own authed call).
