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


def main() -> int:
    if os.environ.get("CONFIRM", "").strip().lower() != "yes":
        emit("refused", reason="CONFIRM != yes (deliberate-signal guard)")
        return 0
    try:
        compartment_id = os.environ["COMPARTMENT_ID"]
        display_name = os.environ["DISPLAY_NAME"]
        config = _config_from_env()
    except KeyError as exc:
        emit("config_error", missing_env=exc.args[0])
        return 2

    try:
        oci.config.validate_config(config)
        compute = oci.core.ComputeClient(config)
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
                 count=len(matches),
                 instance_ids=[i.id for i in matches])
            return 0
        inst = matches[0]
        emit("terminating", instance_id=inst.id, display_name=display_name,
             lifecycle_state=inst.lifecycle_state)
        compute.terminate_instance(inst.id, preserve_boot_volume=False)

        deadline = time.monotonic() + TERMINATE_TIMEOUT_S
        last = inst.lifecycle_state
        while time.monotonic() < deadline:
            last = compute.get_instance(inst.id).data.lifecycle_state
            emit("poll", instance_id=inst.id, lifecycle_state=last)
            if last == "TERMINATED":
                emit("terminated", instance_id=inst.id, display_name=display_name)
                return 0
            time.sleep(POLL_INTERVAL_S)
        emit("terminate_timeout", instance_id=inst.id, lifecycle_state=last)
        return 0
    except oci.exceptions.ServiceError as exc:  # type: ignore[attr-defined]
        emit("service_error", status_code=exc.status, code=exc.code, message=exc.message)
        return 1
    except Exception as exc:  # noqa: BLE001
        emit("unexpected_error", type=type(exc).__name__, message=str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
