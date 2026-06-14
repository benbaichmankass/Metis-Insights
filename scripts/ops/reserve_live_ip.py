#!/usr/bin/env python3
"""Make the LIVE trader's public IP a RESERVED (static) IP so it survives VM moves.

Root cause this closes (2026-06-14, BL-20260614-BYBIT-IP): the live->Ampere
cutover gave the new VM an *ephemeral* OCI public IP. Ephemeral IPs are glued to
the instance and die with it, so a future move forces a new address — which
breaks every external reference keyed to the old one: the Bybit API-key bound-IP
allowlist (ErrCode 10010), the workflow ``VM_SSH_HOST`` fallbacks, the dashboard
``BOT_API_URL``, the session ``DIAG_BASE_URL``, and the trainer ``LIVE_VM_IP``.

A RESERVED public IP belongs to the *role*, not the box: ``cutover-live.yml``
already detects ``lifetime == RESERVED`` and moves the SAME address to the new
VM at cutover ("zero external ref changes"). Adopting a reserved IP once is what
makes that path fire forever after, so the egress IP (hence the Bybit binding)
never changes again.

Used by .github/workflows/reserve-live-ip.yml. Three modes:

  describe   read-only. Print the live VNIC's primary private-IP OCID, the
             current public IP, its lifetime (RESERVED|EPHEMERAL|...), and OCID.
             No mutation.
  allocate   idempotent + NON-disruptive. If the live IP is already RESERVED,
             no-op. Otherwise create a RESERVED public IP *unassigned* (it just
             floats, AVAILABLE) and print its address + OCID. The running VM is
             untouched; allocate lets the operator pre-bind the new address on
             Bybit before the swap.
  assign     MUTATING + briefly disruptive (gated by CONFIRM=yes). Delete the
             VNIC's current ephemeral public IP and assign RESERVED_IP_ID to the
             primary private IP. There is an unavoidable few-second window where
             the VM has no public IP (a private IP holds at most one public IP at
             a time), so SSH / dashboard / egress blink. Run it in a low-activity
             window; open Bybit positions stay protected by their native
             exchange-side SL/TP brackets throughout.

Env (all modes): OCI_CLI_{USER,FINGERPRINT,TENANCY,REGION,KEY_CONTENT},
                 COMPARTMENT_ID, LIVE_INSTANCE_ID (OCID, from IMDS-over-SSH).
Extra for assign: RESERVED_IP_ID (the reserved IP's OCID from ``allocate``),
                  CONFIRM=yes.

Mirrors the OCI SDK idioms in cutover_ip_describe.py / cutover_move_reserved_ip.py.
"""
from __future__ import annotations

import os
import sys


def _cfg():
    import oci

    cfg = {
        "user": os.environ["OCI_CLI_USER"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "region": os.environ["OCI_CLI_REGION"],
        "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
    }
    oci.config.validate_config(cfg)
    return cfg


def _primary_vnic_and_private_ip(compute, net, comp, instance_id):
    """Return (vnic, primary_private_ip) for the instance's primary VNIC."""
    atts = compute.list_vnic_attachments(
        compartment_id=comp, instance_id=instance_id).data
    if not atts:
        raise RuntimeError(f"instance {instance_id} has no VNIC attachments")
    vnic = net.get_vnic(atts[0].vnic_id).data
    priv = None
    for p in net.list_private_ips(vnic_id=vnic.id).data:
        if p.is_primary:
            priv = p
            break
    if priv is None:
        raise RuntimeError(f"no primary private IP on VNIC {vnic.id}")
    return vnic, priv


def _public_ip_meta(net, ip_address):
    """(lifetime, ocid) for a public IP address, or (UNKNOWN, '') on failure."""
    import oci

    try:
        details = oci.core.models.GetPublicIpByIpAddressDetails(ip_address=ip_address)
        pip = net.get_public_ip_by_ip_address(details).data
        return (pip.lifetime or "UNKNOWN", pip.id or "")
    except Exception as exc:  # noqa: BLE001
        print(f"# public-ip lookup failed: {exc}", file=sys.stderr)
        return ("UNKNOWN", "")


def _clients():
    import oci

    cfg = _cfg()
    return oci.core.ComputeClient(cfg), oci.core.VirtualNetworkClient(cfg)


def do_describe() -> int:
    compute, net = _clients()
    comp = os.environ["COMPARTMENT_ID"]
    inst = os.environ["LIVE_INSTANCE_ID"]
    vnic, priv = _primary_vnic_and_private_ip(compute, net, comp, inst)
    pub = vnic.public_ip
    print(f"live_instance_id={inst}")
    print(f"private_ip_id={priv.id}")
    print(f"private_ip={priv.ip_address}")
    if not pub:
        print("public_ip=None")
        print("public_ip_lifetime=NONE")
        print("public_ip_id=")
        return 0
    lifetime, ip_id = _public_ip_meta(net, pub)
    print(f"public_ip={pub}")
    print(f"public_ip_lifetime={lifetime}")
    print(f"public_ip_id={ip_id}")
    return 0


def do_allocate() -> int:
    import oci

    compute, net = _clients()
    comp = os.environ["COMPARTMENT_ID"]
    inst = os.environ["LIVE_INSTANCE_ID"]
    vnic, _priv = _primary_vnic_and_private_ip(compute, net, comp, inst)
    pub = vnic.public_ip
    if pub:
        lifetime, ip_id = _public_ip_meta(net, pub)
        if lifetime == "RESERVED":
            print(f"already RESERVED: {pub} ({ip_id}) — nothing to do (idempotent).")
            print(f"reserved_ip={pub}")
            print(f"reserved_ip_id={ip_id}")
            return 0
        print(f"current public IP {pub} is {lifetime} — allocating a reserved IP "
              f"(floating, NOT yet assigned; the running VM is untouched).")
    create = oci.core.models.CreatePublicIpDetails(
        compartment_id=comp,
        lifetime="RESERVED",
        display_name="ict-live-reserved-ip",
        # No private_ip_id -> created AVAILABLE / unassigned. Zero disruption.
    )
    pip = net.create_public_ip(create).data
    print(f"OK: allocated RESERVED public IP {pip.ip_address} "
          f"lifecycle={pip.lifecycle_state} ocid={pip.id}")
    print(f"reserved_ip={pip.ip_address}")
    print(f"reserved_ip_id={pip.id}")
    print("# NEXT: bind this address on Bybit (API Management -> the BYBIT_API_KEY_2"
          " key) BEFORE running mode=assign, then run assign in a low-activity window.")
    return 0


def do_assign() -> int:
    import oci

    if os.environ.get("CONFIRM", "").strip().lower() != "yes":
        print("ERROR: mode=assign is mutating + briefly disruptive — set CONFIRM=yes.")
        return 2
    reserved_ip_id = os.environ.get("RESERVED_IP_ID", "").strip()
    if not reserved_ip_id:
        print("ERROR: RESERVED_IP_ID empty — run mode=allocate first and pass its OCID.")
        return 2

    compute, net = _clients()
    comp = os.environ["COMPARTMENT_ID"]
    inst = os.environ["LIVE_INSTANCE_ID"]
    vnic, priv = _primary_vnic_and_private_ip(compute, net, comp, inst)

    reserved = net.get_public_ip(reserved_ip_id).data
    if (reserved.lifetime or "").upper() != "RESERVED":
        print(f"ERROR: {reserved_ip_id} is not a RESERVED public IP "
              f"(lifetime={reserved.lifetime}).")
        return 3
    if reserved.private_ip_id == priv.id:
        print(f"already assigned: reserved IP {reserved.ip_address} is on the live "
              f"primary private IP — nothing to do (idempotent).")
        return 0

    cur_pub = vnic.public_ip
    if cur_pub:
        cur_lifetime, cur_id = _public_ip_meta(net, cur_pub)
        if cur_lifetime == "EPHEMERAL" and cur_id:
            print(f"deleting ephemeral public IP {cur_pub} ({cur_id}) to free the "
                  f"primary private IP ...")
            net.delete_public_ip(cur_id)
        elif cur_lifetime == "RESERVED":
            print(f"WARNING: live VNIC already has a RESERVED IP {cur_pub}; unassign "
                  f"it first if you really mean to replace it. Aborting.")
            return 4

    print(f"assigning reserved public IP {reserved.ip_address} -> live primary "
          f"private IP {priv.id} ({priv.ip_address}) ...")
    net.update_public_ip(
        reserved_ip_id,
        oci.core.models.UpdatePublicIpDetails(private_ip_id=priv.id),
    )
    updated = net.get_public_ip(reserved_ip_id).data
    print(f"OK: live public IP is now RESERVED {updated.ip_address} "
          f"lifecycle={updated.lifecycle_state} assigned_entity={updated.assigned_entity_id}")
    print(f"reserved_ip={updated.ip_address}")
    print("# NEXT: confirm Bybit is bound to this address; update the VM_SSH_HOST repo"
          " variable + dashboard BOT_API_URL + DIAG_BASE_URL if the address changed.")
    return 0


_MODES = {"describe": do_describe, "allocate": do_allocate, "assign": do_assign}


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE", "describe")).strip()
    fn = _MODES.get(mode)
    if fn is None:
        print(f"ERROR: unknown mode {mode!r}; expected one of {sorted(_MODES)}")
        return 2
    try:
        import oci  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: oci sdk import failed: {exc}")
        return 1
    return fn()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: reserve_live_ip failed: {exc}")
        raise SystemExit(1)
