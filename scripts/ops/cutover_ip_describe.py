#!/usr/bin/env python3
"""Read-only: describe the live micro's public IP for the cutover plan.

Used by .github/workflows/cutover-live.yml (Phase 3 of the live-VM Ampere
migration, docs/runbooks/live-vm-migration-ampere.md). Determines whether the
micro's public IP is RESERVED (movable to the candidate at cutover, so zero
external-reference changes) or EPHEMERAL (the candidate keeps its own IP and
the external refs — VM_SSH_HOST var, dashboard BOT_API_URL, DIAG_BASE_URL —
must be updated).

NO mutation. Prints ``key=value`` lines to stdout:
    micro_public_ip=<ip|None>
    micro_ip_lifetime=<RESERVED|EPHEMERAL|UNKNOWN>
    micro_public_ip_id=<ocid|>

Env: OCI_CLI_{USER,FINGERPRINT,TENANCY,REGION,KEY_CONTENT},
     MICRO_INSTANCE_ID, COMPARTMENT_ID. Best-effort — never raises; prints
UNKNOWN on any failure so the workflow can still report a partial plan.
"""
from __future__ import annotations

import os
import sys


def _emit(ip="None", lifetime="UNKNOWN", ip_id=""):
    print(f"micro_public_ip={ip}")
    print(f"micro_ip_lifetime={lifetime}")
    print(f"micro_public_ip_id={ip_id}")


def main() -> int:
    try:
        import oci
    except Exception as exc:  # noqa: BLE001
        print(f"# oci import failed: {exc}", file=sys.stderr)
        _emit()
        return 0
    try:
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
        micro = os.environ["MICRO_INSTANCE_ID"]
        comp = os.environ["COMPARTMENT_ID"]
        atts = compute.list_vnic_attachments(
            compartment_id=comp, instance_id=micro).data
        if not atts:
            print("# no VNIC attachments on micro", file=sys.stderr)
            _emit()
            return 0
        vnic = net.get_vnic(atts[0].vnic_id).data
        pub = vnic.public_ip
        if not pub:
            _emit(ip="None", lifetime="NONE")
            return 0
        try:
            details = oci.core.models.GetPublicIpByIpAddressDetails(ip_address=pub)
            pip = net.get_public_ip_by_ip_address(details).data
            _emit(ip=pub, lifetime=pip.lifetime, ip_id=pip.id or "")
        except Exception as exc:  # noqa: BLE001
            print(f"# public-ip lookup failed: {exc}", file=sys.stderr)
            _emit(ip=pub, lifetime="UNKNOWN")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"# describe failed: {exc}", file=sys.stderr)
        _emit()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
