#!/usr/bin/env python3
"""MUTATING: reassign a RESERVED public IP to the Ampere candidate's VNIC.

Used by .github/workflows/cutover-live.yml (live cutover, RESERVED case only).
Moving a reserved public IP to the candidate's primary private IP means every
external reference to the old IP keeps working with no change — the smoothest
cutover. (Ephemeral IPs cannot be reassigned, so this script is invoked only
when cutover_ip_describe.py reported lifetime == RESERVED.)

Sequence:
  1. find the candidate instance by display name (RUNNING),
  2. resolve its primary VNIC's primary PRIVATE IP OCID,
  3. update_public_ip(PUBLIC_IP_ID, private_ip_id=<that OCID>) — OCI unassigns
     the reserved IP from the micro and assigns it to the candidate.

Env: OCI_CLI_{USER,FINGERPRINT,TENANCY,REGION,KEY_CONTENT}, COMPARTMENT_ID,
     PUBLIC_IP_ID (the reserved IP's OCID), CANDIDATE_NAME.
Exits non-zero on failure so the workflow surfaces it (the trader is already
stopped on the micro at this point; the operator can roll back per the runbook).
"""
from __future__ import annotations

import os


def main() -> int:
    import oci

    cfg = {
        "user": os.environ["OCI_CLI_USER"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "region": os.environ["OCI_CLI_REGION"],
        "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
    }
    oci.config.validate_config(cfg)
    compute = oci.core.ComputeClient(cfg)
    net = oci.core.VirtualNetworkClient(cfg)
    comp = os.environ["COMPARTMENT_ID"]
    public_ip_id = os.environ["PUBLIC_IP_ID"]
    cand_name = os.environ.get("CANDIDATE_NAME", "ict-bot-arm")
    if not public_ip_id:
        print("ERROR: PUBLIC_IP_ID empty — cannot move a reserved IP without it.")
        return 2

    # 1. candidate instance by display name (must be RUNNING)
    cand = None
    for inst in oci.pagination.list_call_get_all_results(
            compute.list_instances, comp).data:
        if inst.display_name == cand_name and inst.lifecycle_state == "RUNNING":
            cand = inst
            break
    if cand is None:
        print(f"ERROR: no RUNNING instance named {cand_name!r} in compartment.")
        return 3

    # 2. primary VNIC -> primary private IP OCID
    atts = compute.list_vnic_attachments(
        compartment_id=comp, instance_id=cand.id).data
    if not atts:
        print(f"ERROR: candidate {cand_name} has no VNIC attachments.")
        return 4
    vnic_id = atts[0].vnic_id
    priv = None
    for p in net.list_private_ips(vnic_id=vnic_id).data:
        if p.is_primary:
            priv = p
            break
    if priv is None:
        print(f"ERROR: no primary private IP on candidate VNIC {vnic_id}.")
        return 5

    # 3. reassign the reserved public IP
    print(f"Moving reserved public IP {public_ip_id} -> candidate {cand_name} "
          f"private IP {priv.id} ({priv.ip_address}) ...")
    net.update_public_ip(
        public_ip_id,
        oci.core.models.UpdatePublicIpDetails(private_ip_id=priv.id),
    )
    updated = net.get_public_ip(public_ip_id).data
    print(f"OK: reserved IP {updated.ip_address} lifecycle={updated.lifecycle_state} "
          f"assigned_entity={updated.assigned_entity_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: reserved-IP move failed: {exc}")
        raise SystemExit(1)
