# Runbook — migrate the live trader from the AMD micro to an Ampere A1.Flex

**Status: PLAN — operator review required before any execution. Nothing in
here has been run.**

## Why

The live trader runs on **`VM.Standard.E2.1.Micro` — 1 OCPU / 1 GB RAM**
(Always-Free AMD x86). Verified 2026-06-10 via the `vm-resize-live` dry-run
(issue #3254). That box runs ~15 services (trader, web-api, two watchdogs,
the IB-Gateway Java container, regime scoring, two insights generators,
trainer-mirror ingest, telegram bot, …) and **swap-thrashes** on 1 GB — the
root cause of the 2026-06-10 wedge cascade. Micro shapes are **fixed (not
resizable)**, so the only way to get real capacity within Always-Free is to
**migrate the trader to an Ampere `VM.Standard.A1.Flex`**.

| | Now (micro) | Target (A1.Flex) |
|---|---|---|
| Shape | VM.Standard.E2.1.Micro | VM.Standard.A1.Flex |
| Arch | **x86_64 (amd64)** | **aarch64 (ARM64)** |
| OCPU | 1 (2 vCPU) | **3** |
| RAM | **1 GB** | **18 GB** |
| Cost | Always-Free | Always-Free (pool: 4 OCPU / 24 GB − trainer 1/6 = 3/18 left) |

## Critical pre-migration unknowns — VERIFY FIRST (these can block the whole move)

The migration is an **architecture change (x86 → ARM64)**, not a like-for-like
move. Resolve all four before scheduling a cutover:

1. **IB Gateway on ARM64 — the biggest risk (now de-risked to "test it").**
   The MES path uses the `gnzsnz/ib-gateway` Docker image (IBKR's Java Gateway
   under Xvfb). **Researched 2026-06-10:** the image ships **experimental
   aarch64 support** ("expects bugs") since **v10.37.1l / 10.39.1e** — the live
   VM runs **10.45.1g**, so an arm64 variant *is* published for our version.
   So ARM64 is **viable but unproven** for live MES; the plan is to **soak-test
   it on an ARM box before cutover**, with a ready fallback.
   - **Verify:** on an A1.Flex (the trainer is ARM), pull + run the arm64
     image, log into `ib_paper`, confirm `ib_connect_check` returns
     `net_liquidation` populated (not just a socket accept) and that
     `reqHistoricalData` for MES returns bars — over a multi-hour soak
     (the IBKR nightly-reset re-login is exactly where ARM bugs would surface).
   - **Fallback if the arm64 Gateway is flaky:** keep the Gateway on the
     **2nd free x86 E2.1.Micro** in the pool and point the ARM trader at it
     over the private subnet (`IB_HOST`/`ib_port` → the micro's private IP).
     This fully decouples the MES broker session from the arch change.
   - **Do not cut over to ARM for live MES until the soak passes** — losing
     MES is not acceptable. (Bybit/BTCUSDT is unaffected — pure Python/ccxt.)
2. **Python deps on aarch64.** ccxt, ib_insync, pandas, numpy, oci, fastapi,
   uvicorn all publish aarch64 wheels — low risk, but do a clean
   `pip install -r requirements.txt` on an ARM box (the trainer) and run the
   test suite there before cutover.
3. **Public IP cutover.** The dashboard (`BOT_API_URL`) and diag
   (`DIAG_BASE_URL`) + the diag/system-action relays all target
   `158.178.210.252`. Determine whether that is an **ephemeral or reserved**
   public IP:
   - Reserved → it can be **reassigned** to the new VNIC (cleanest; no
     consumer changes). `oci network public-ip update`.
   - Ephemeral → cannot move; we update every reference (Streamlit
     `BOT_API_URL` secret, `DIAG_BASE_URL`, `vars.LIVE_VM_IP`, the SSH host in
     the relays, CLAUDE.md). Prefer converting to a reserved IP first.
4. **`/data/bot-data` block volume.** The canonical DBs + runtime_logs live on
   an OCI block volume. Two move options:
   - **Detach + reattach** (preferred, no copy, atomic): stop the trader,
     detach the volume from the micro, attach to the A1.Flex (same AD
     required — confirm the A1.Flex is provisioned in the micro's AD). The
     block volume carries `trade_journal.db` intact.
   - **rsync** (fallback if AD differs): `rsync` `/data/bot-data` over the
     private subnet during the window. Slower; needs the trader stopped to be
     consistent.

## Phased plan

### Phase 0 — verify (no outage)
- Resolve the four unknowns above. Build an ARM box (reuse the trainer or a
  throwaway A1.Flex slice) and: pip install, run pytest, test the IB-Gateway
  arm64 image. **Gate: all four green before Phase 1.**

### Phase 1 — provision the A1.Flex (no outage)
- Reuse the `provision-training-vm` machinery (it already wires the OCI auth +
  cloud-init). Provision a `VM.Standard.A1.Flex` **3 OCPU / 18 GB** in the
  micro's compartment + AD + subnet, named e.g. `ict-bot-arm`.
- Attach a fresh boot volume; do **not** touch the micro's `/data` volume yet.
- Install: clone the repo, `scripts/install_systemd_units.sh`, create the
  `/opt/ict-trading-bot` symlink, install Docker (for the Gateway, if arm64
  works), set up the `/data/bot-data` mountpoint + the data-dir drop-in.

### Phase 2 — seed config + secrets (no outage)
- Repopulate `.env` via the `sync-vm-secrets` workflow (Actions → VM), so no
  secret is hand-copied. Verify `render_env_from_master` / the required keys.
- Dry-boot the trader on the A1.Flex with `mode: dry_run` (or pointed at an
  empty scratch DB) to confirm the stack starts clean on ARM — **without**
  touching live state.

### Phase 3 — cutover (the only outage; ~5–15 min, scheduled)
1. Announce; confirm no open-trade-sensitive moment.
2. Stop `ict-trader-live` on the micro (positions sit on broker SL/TP).
3. Move data: detach `/data/bot-data` from the micro → attach to the A1.Flex
   (or final rsync). Verify `trade_journal.db` integrity (`PRAGMA
   integrity_check`).
4. Move the IP: reassign the reserved public IP to the A1.Flex VNIC (or update
   all references if ephemeral).
5. Start `ict-trader-live` + `ict-web-api` on the A1.Flex. Verify: heartbeat
   advancing, `/api/diag/status` reachable, accounts live, MES gateway healthy.
6. Re-point relays: `vars.LIVE_VM_IP`, the SSH host in the diag/system-action
   workflows, `DIAG_BASE_URL`, the dashboard `BOT_API_URL`.

### Phase 4 — verify + decommission
- Soak 1–2 h: heartbeat, ticks completing at cadence, loadavg sane (now with
  headroom), MES + Bybit both trading, dashboard live.
- **Keep the micro STOPPED but intact for 24–48 h as the rollback.**
- Only after the soak: terminate the micro (frees nothing in the Ampere pool —
  it's a separate AMD allocation — but stops the duplicate).

## Rollback
At any point before decommission: stop the trader on the A1.Flex, re-attach
`/data/bot-data` to the micro, reassign the IP back, start the trader on the
micro. Because the micro is left intact + stopped, rollback is minutes.

## Interim (until migration)
The micro stays alive via: the cgroup priority (#3232), the IB-gateway cap,
and **load-shedding the observe-only sidecars** — regime-bar-scoring
(`REGIME_BAR_SCORING_DISABLED=1`) and the M13 insights generators
(`disable-insights-generator`) — to free RAM. Re-enable both after migration.

## Open decisions for the operator
- **IB Gateway**: arm64 image if it works, else keep the Gateway on the 2nd
  free x86 micro and have the ARM trader reach it over the private subnet.
- **IP**: convert to a reserved public IP before cutover (strongly preferred)
  vs update all references.
- **A1.Flex size**: 3 OCPU / 18 GB (max within pool, trainer intact) vs leave
  some pool headroom for a future side-car.
