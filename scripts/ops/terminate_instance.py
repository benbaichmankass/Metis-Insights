#!/usr/bin/env python3
"""Terminate an OCI instance by display name (migration / decommission helper).

Used to free OCI Always-Free Ampere budget — e.g. tearing down the
3-OCPU migration candidate (ict-bot-arm) so a small dedicated IB-Gateway
VM can be provisioned in its place (the 2026-06-10 gateway-isolation plan,
Plan B). Counterpart to provision_training_vm.py; reuses the same
OCI_CLI_* env contract.

Required env:
  OCI_CLI_USER / OCI_CLI_FINGERPRINT / OCI_CLI_TENANCY / OCI_CLI_REGION /
  OCI_CLI_KEY_CONTENT  — OCI API auth (same as the provision workflow)
  COMPARTMENT_ID       — compartment to search
  DISPLAY_NAME         — exact display name of the instance to terminate

Safety:
  * Refuses unless CONFIRM=yes (the workflow sets it from a `confirm: yes`
    issue-body line — an explicit, grep-able deliberate signal).
  * Terminates ONLY a single instance matching DISPLAY_NAME and only when
    exactly one non-terminated match exists. Two matches → abort (never
    guess which to kill).
  * preserve_boot_volume=False (full teardown; the candidate has no data
    worth keeping — the live trader's /data volume is NOT attached to it).

Emits one JSONL event per line; exit 0 on every business outcome, non-zero
only on hard infra/credential errors.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import oci

POLL_INTERVAL_S = 10
TERMINATE_TIMEOUT_S = 600


def emit(status: str, **fields: Any) -> None:
    print(json.dumps({"status": status, **fields}))


def _config_from_env() -> dict[str, str]:
    return {
        "user": os.environ["OCI_CLI_USER"],
        "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "region": os.environ["OCI_CLI_REGION"],
    }


def _terminate_and_poll(compute, inst_id: str, label: dict) -> int:
    emit("terminating", instance_id=inst_id, **label)
    compute.terminate_instance(inst_id, preserve_boot_volume=False)
    deadline = time.monotonic() + TERMINATE_TIMEOUT_S
    last = None
    while time.monotonic() < deadline:
        last = compute.get_instance(inst_id).data.lifecycle_state
        emit("poll", instance_id=inst_id, lifecycle_state=last)
        if last == "TERMINATED":
            emit("terminated", instance_id=inst_id, **label)
            return 0
        time.sleep(POLL_INTERVAL_S)
    emit("terminate_timeout", instance_id=inst_id, lifecycle_state=last)
    return 0


def _public_ip_for(compute, net, comp: str, inst_id: str):
    """Best-effort primary public IP for an instance (None on any failure)."""
    try:
        for att in compute.list_vnic_attachments(
                compartment_id=comp, instance_id=inst_id).data:
            vnic = net.get_vnic(att.vnic_id).data
            if vnic.public_ip:
                return vnic.public_ip
    except Exception:  # noqa: BLE001
        return None
    return None


def do_list(config: dict) -> int:
    """Read-only: enumerate non-terminated instances so an OCID is discoverable
    without a human (no CONFIRM needed). Carries public_ip to identify a box by
    address (e.g. the retired micro at 158.178.210.252)."""
    compute = oci.core.ComputeClient(config)
    net = oci.core.VirtualNetworkClient(config)
    comp = os.environ["COMPARTMENT_ID"]
    insts = [i for i in compute.list_instances(compartment_id=comp).data
             if i.lifecycle_state != "TERMINATED"]
    emit("list", count=len(insts), instances=[
        {"instance_id": i.id, "display_name": i.display_name,
         "lifecycle_state": i.lifecycle_state, "shape": i.shape,
         "public_ip": _public_ip_for(compute, net, comp, i.id)}
        for i in insts
    ])
    return 0


def main() -> int:
    mode = os.environ.get("MODE", "").strip().lower()
    try:
        config = _config_from_env()
        oci.config.validate_config(config)
    except KeyError as exc:
        emit("config_error", missing_env=exc.args[0])
        return 2
    except Exception as exc:  # noqa: BLE001
        emit("config_error", message=str(exc))
        return 2

    # Read-only LIST mode — no CONFIRM required.
    if mode == "list":
        try:
            return do_list(config)
        except oci.exceptions.ServiceError as exc:  # type: ignore[attr-defined]
            emit("service_error", status_code=exc.status, code=exc.code, message=exc.message)
            return 1
        except Exception as exc:  # noqa: BLE001
            emit("unexpected_error", type=type(exc).__name__, message=str(exc))
            return 1

    # Destructive: terminate by INSTANCE_ID (OCID, exact + rename-proof) or
    # DISPLAY_NAME (exact single-match). CONFIRM=yes gated.
    if os.environ.get("CONFIRM", "").strip().lower() != "yes":
        emit("refused", reason="CONFIRM != yes (deliberate-signal guard)")
        return 0
    instance_id = os.environ.get("INSTANCE_ID", "").strip()
    display_name = os.environ.get("DISPLAY_NAME", "").strip()
    if not instance_id and not display_name:
        emit("config_error", missing_env="INSTANCE_ID or DISPLAY_NAME")
        return 2

    try:
        compute = oci.core.ComputeClient(config)
        if instance_id:
            inst = compute.get_instance(instance_id).data
            if inst.lifecycle_state == "TERMINATED":
                emit("already_terminated", instance_id=instance_id,
                     display_name=inst.display_name)
                return 0
            return _terminate_and_poll(
                compute, instance_id,
                {"display_name": inst.display_name, "by": "instance_id"})

        compartment_id = os.environ["COMPARTMENT_ID"]
        matches = [
            i for i in compute.list_instances(
                compartment_id=compartment_id, display_name=display_name
            ).data
            if i.lifecycle_state != "TERMINATED"
        ]
        if not matches:
            emit("not_found", display_name=display_name)
            return 0
        if len(matches) > 1:
            emit("ambiguous", display_name=display_name,
                 count=len(matches), instance_ids=[i.id for i in matches])
            return 0
        return _terminate_and_poll(
            compute, matches[0].id,
            {"display_name": display_name, "by": "display_name"})
    except oci.exceptions.ServiceError as exc:  # type: ignore[attr-defined]
        emit("service_error", status_code=exc.status, code=exc.code, message=exc.message)
        return 1
    except Exception as exc:  # noqa: BLE001
        emit("unexpected_error", type=type(exc).__name__, message=str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
