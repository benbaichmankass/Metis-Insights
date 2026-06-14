# Reserved (static) public IP for the live trader — stable egress across VM moves

**Why this exists.** The 2026-06-14 live→Ampere cutover left the live VM on an
**ephemeral** OCI public IP (`141.145.193.91`). Ephemeral IPs are bound to the
instance and are destroyed with it, so any future VM move forces a *new* address
— which silently breaks every external reference keyed to the old one:

- the **Bybit API-key bound-IP allowlist** → `ErrCode 10010 "Unmatched IP"`,
  a real-money outage on `bybit_2` (`BL-20260614-BYBIT-IP`);
- the workflow `VM_SSH_HOST` fallbacks (≈100 hardcoded literals across `.github/`);
- the dashboard `BOT_API_URL` (Streamlit secret);
- the PM-session `DIAG_BASE_URL`;
- the trainer `LIVE_VM_IP` drop-ins (`ict-trainer-publish`, `ict-promotion-readiness`).

A **reserved** public IP belongs to the *role*, not the box. `cutover-live.yml`
already detects `lifetime == RESERVED` and moves the *same* address to the new
VM at cutover — **"zero external ref changes."** Adopting a reserved IP once is
what makes that path fire on every future move, so the egress IP (hence the
Bybit binding and all the refs above) never changes again. This is the durable
fix for the class of failure, not just the one incident.

> **One-time cost.** OCI cannot convert the current ephemeral address into a
> reserved one in place — a reserved IP gets a *new* address from the pool. So
> adopting it requires **one final, brief Bybit rebind** to the new reserved
> address. After that, never again.

## Tooling

- `scripts/ops/reserve_live_ip.py` — `describe` / `allocate` / `assign` (OCI SDK).
- `.github/workflows/reserve-live-ip.yml` — issue/dispatch driven; discovers the
  live instance via IMDS-over-SSH, then runs the script. Auth + discovery mirror
  `cutover-live.yml`. Label: `reserve-live-ip`.

## Execution sequence (operator-gated — Tier-3, money-VM network)

Run in a **low-activity window**. Open Bybit positions stay protected by their
native exchange-side SL/TP brackets throughout (those fire on Bybit, not via the
client), so the only exposure is "can't open/manage new trades for a few seconds."

1. **describe** (read-only, no mutation). Dispatch `reserve-live-ip` with
   `mode: describe`. Confirm `public_ip_lifetime=EPHEMERAL` (if it already says
   `RESERVED`, you're done — nothing to do).

2. **allocate** (non-disruptive). Dispatch `mode: allocate`. This creates a
   RESERVED public IP that just *floats* (AVAILABLE, unassigned) — the running VM
   is untouched. Note the printed `reserved_ip` (the new address `X`) and
   `reserved_ip_id` (its OCID). Idempotent: re-running while already reserved is a
   no-op.

3. **Bind `X` on Bybit** (operator-only, broker-side — no workflow can do this).
   Bybit → API Management → the key for `BYBIT_API_KEY_2` (and `BYBIT_API_KEY_1`
   demo) → set the bound IP to `X`. *Do this right before step 4*: standard Bybit
   keys bind a single IP, so once you switch to `X` the calls from the still-live
   `141.145.193.91` egress fail until step 4 lands. Keep the gap small.

4. **assign** (brief swap, ~seconds). Dispatch `mode: assign` with
   `reserved_ip_id: <ocid from step 2>` and `confirm: yes`. The script deletes the
   ephemeral public IP and assigns `X` to the live VNIC's primary private IP.
   There is a few-second window where the VM has **no** public IP (a private IP
   holds at most one public IP at a time) — SSH, the dashboard, and egress blink,
   then come back on `X`.

5. **Update the single-sourced refs to `X`** (only the value changed; ideally do
   this as the Layer-2 single-source so it's one edit, not 100):
   - repo **variable** `VM_SSH_HOST` → `X`
   - dashboard `BOT_API_URL` (Streamlit secret) → `http://X:8001`
   - PM-session `DIAG_BASE_URL` → `http://X:8001`
   - trainer `LIVE_VM_IP` drop-ins → `X`

6. **Verify.** `reserve-live-ip mode: describe` shows `RESERVED`; the live VM is
   reachable on `X`; `bybit_2` reads positions again (diag relay
   `/api/diag/journal?table=trades` / no more 10010 in the audit tail).

## Rollback

If `assign` fails *after* deleting the ephemeral IP (VM left with no public IP →
SSH relays can't reach it), recover from the **OCI console** (operator; may hit a
CAPTCHA): attach the reserved IP `X` to the live VNIC's primary private IP
manually (Networking → the VNIC → IPv4 → Edit → Reserved public IP), or assign a
fresh ephemeral. Then re-point the refs. The reserved IP `X` persists across this
— it is not lost when unassigned.

## After adoption — future moves are free

Once the live IP is RESERVED, a future `cutover-live` run reports
`lifetime == RESERVED` and carries `X` to the new VM automatically (zero ref
changes, no Bybit rebind). The EPHEMERAL warning in `cutover-live.yml` (which now
explicitly lists the Bybit bound-IP) only fires if someone reverts to an
ephemeral IP.
