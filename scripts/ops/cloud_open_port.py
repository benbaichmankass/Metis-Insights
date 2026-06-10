#!/usr/bin/env python3
"""
Idempotently ensure an Oracle Cloud Security List ingress rule exists
for a TCP port on every Security List attached to the trading-bot VM's
primary VNIC subnet.

Configurable via env:
  * DASHBOARD_PORT       — TCP port to open (default 8001, the dashboard API).
  * INGRESS_SOURCE_CIDR  — source CIDR for the rule (default 0.0.0.0/0, the
                           public dashboard port). For non-public services
                           (e.g. the IB Gateway API on 4002) pass a private
                           subnet like 10.0.0.0/24 so the port is NEVER exposed
                           to the internet.
  * INGRESS_DESC         — human description stored on the rule.

The (port, source) pair is the idempotency key: a rule for the SAME port from
a DIFFERENT source is not treated as a match, so opening 4002 to 10.0.0.0/24
never silently relies on (or is suppressed by) a pre-existing public rule.

Reads OCI credentials from the standard env vars (OCI_CLI_USER,
OCI_CLI_TENANCY, OCI_CLI_FINGERPRINT, OCI_CLI_REGION, OCI_CLI_KEY_CONTENT).

Reads the target instance OCID + compartment OCID from argv (the
calling workflow scrapes them from the VM's instance metadata service
via SSH; doing the IMDS read on the runner directly is impossible
because IMDS is link-local to the VM).

Exits 0 on success (rule present after run), nonzero on failure.
Output is JSON-line-per-action so the workflow can parse / log it.
"""
from __future__ import annotations

import json
import os
import sys

import oci

PORT = int(os.environ.get("DASHBOARD_PORT", "8001"))
SOURCE_CIDR = os.environ.get("INGRESS_SOURCE_CIDR", "0.0.0.0/0")
DESC = os.environ.get("INGRESS_DESC", "ict-web-api dashboard")


def emit(action: str, **kw: object) -> None:
    print(json.dumps({"action": action, **kw}, default=str), flush=True)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: cloud_open_port.py <instance_ocid> <compartment_ocid>", file=sys.stderr)
        return 2
    instance_id, compartment_id = sys.argv[1], sys.argv[2]

    config = {
        "user": os.environ["OCI_CLI_USER"],
        "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "region": os.environ["OCI_CLI_REGION"],
    }
    oci.config.validate_config(config)

    vnet = oci.core.VirtualNetworkClient(config)
    compute = oci.core.ComputeClient(config)

    attachments = compute.list_vnic_attachments(
        compartment_id=compartment_id, instance_id=instance_id
    ).data
    if not attachments:
        emit("error", message="no VNIC attachments found", instance_id=instance_id)
        return 1
    primary_vnic_id = attachments[0].vnic_id
    vnic = vnet.get_vnic(primary_vnic_id).data
    subnet = vnet.get_subnet(vnic.subnet_id).data
    emit(
        "context",
        instance_id=instance_id,
        vnic_id=primary_vnic_id,
        subnet_id=subnet.id,
        security_list_ids=subnet.security_list_ids,
    )

    rule_added_anywhere = False
    for sl_id in subnet.security_list_ids:
        sl = vnet.get_security_list(sl_id).data
        rules = sl.ingress_security_rules

        def matches(r: oci.core.models.IngressSecurityRule) -> bool:
            if r.protocol != "6":  # 6 = TCP
                return False
            if r.source != SOURCE_CIDR:  # same port, different source ≠ match
                return False
            if not r.tcp_options or not r.tcp_options.destination_port_range:
                return False
            pr = r.tcp_options.destination_port_range
            return pr.min <= PORT <= pr.max

        if any(matches(r) for r in rules):
            emit(
                "skip",
                security_list_id=sl_id,
                reason=f"rule for TCP/{PORT} from {SOURCE_CIDR} already present",
            )
            continue

        new_rule = oci.core.models.IngressSecurityRule(
            protocol="6",
            source=SOURCE_CIDR,
            source_type="CIDR_BLOCK",
            is_stateless=False,
            tcp_options=oci.core.models.TcpOptions(
                destination_port_range=oci.core.models.PortRange(min=PORT, max=PORT)
            ),
            description=DESC,
        )
        vnet.update_security_list(
            sl_id,
            oci.core.models.UpdateSecurityListDetails(
                ingress_security_rules=rules + [new_rule]
            ),
        )
        emit("added", security_list_id=sl_id, port=PORT, source=SOURCE_CIDR)
        rule_added_anywhere = True

    if rule_added_anywhere:
        emit("done", status="rule_added")
    else:
        emit("done", status="noop_already_present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
