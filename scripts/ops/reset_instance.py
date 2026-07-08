#!/usr/bin/env python3
"""Out-of-band OCI instance RESET by display name (hung-box recovery helper).

The counterpart to ``terminate_instance.py`` for the case where a VM is
**SSH-unreachable / hung** (e.g. the trainer VM OOM-wedged into a
banner-exchange timeout, MB-20260705-TRAINER-OOM) and therefore cannot be
rebooted in-band (the ``reboot-vm`` system-action SSHes + ``shutdown -r``,
which a dead box never answers). An OCI ``instance_action`` runs through the
control plane, so it works even when the guest OS is fully wedged — a hard
``RESET`` power-cycles the box back to RUNNING WITHOUT terminating it, so the
instance (and its Always-Free Ampere capacity) is preserved rather than risked
on a re-provision that may not reclaim scarce capacity.

Required env (same OCI_CLI_* contract as provision/terminate):
  OCI_CLI_USER / OCI_CLI_FINGERPRINT / OCI_CLI_TENANCY / OCI_CLI_REGION /
  OCI_CLI_KEY_CONTENT  — OCI API auth
  COMPARTMENT_ID       — compartment to search (for DISPLAY_NAME lookup / list)
  DISPLAY_NAME | INSTANCE_ID — the target (OCID wins; display name = exact
                               single non-terminated match)
  ACTION               — SOFTRESET (graceful) | RESET (hard, default; the one a
                         genuinely-hung box needs). STOP/START also accepted.
  CONFIRM=yes          — deliberate-signal guard (a reset interrupts the box)

Safety mirrors terminate_instance.py: single-match only, CONFIRM-gated,
MODE=list is a read-only enumerate needing no CONFIRM. A reset is recoverable
(the box comes back), so this is far less destructive than terminate — but the
guard keeps it deliberate. Emits one JSONL event per line; exit 0 on business
outcomes, non-zero only on hard infra/credential errors.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import oci

POLL_INTERVAL_S = 10
RESET_TIMEOUT_S = 600
_VALID_ACTIONS = {"SOFTRESET", "RESET", "SOFTSTOP", "STOP", "START"}


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


def _target_state(action: str) -> str:
    """Lifecycle state we poll toward for a given action."""
    return "STOPPED" if action in ("STOP", "SOFTSTOP") else "RUNNING"


def _action_and_poll(compute, inst_id: str, action: str, label: dict) -> int:
    emit("acting", instance_id=inst_id, action=action, **label)
    compute.instance_action(inst_id, action)
    want = _target_state(action)
    deadline = time.monotonic() + RESET_TIMEOUT_S
    last = None
    # A hard RESET briefly transitions through STOPPING/STARTING; poll until the
    # box reaches the target lifecycle state (RUNNING for a reset).
    while time.monotonic() < deadline:
        last = compute.get_instance(inst_id).data.lifecycle_state
        emit("poll", instance_id=inst_id, lifecycle_state=last)
        if last == want:
            emit("done", instance_id=inst_id, action=action,
                 lifecycle_state=last, **label)
            return 0
        time.sleep(POLL_INTERVAL_S)
    emit("action_timeout", instance_id=inst_id, action=action,
         lifecycle_state=last)
    return 0


def do_list(config: dict) -> int:
    """Read-only enumerate (reuse of terminate's list) — no CONFIRM needed."""
    compute = oci.core.ComputeClient(config)
    comp = os.environ["COMPARTMENT_ID"]
    insts = [i for i in compute.list_instances(compartment_id=comp).data
             if i.lifecycle_state != "TERMINATED"]
    emit("list", count=len(insts), instances=[
        {"instance_id": i.id, "display_name": i.display_name,
         "lifecycle_state": i.lifecycle_state, "shape": i.shape}
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

    if mode == "list":
        try:
            return do_list(config)
        except oci.exceptions.ServiceError as exc:  # type: ignore[attr-defined]
            emit("service_error", status_code=exc.status, code=exc.code, message=exc.message)
            return 1
        except Exception as exc:  # noqa: BLE001
            emit("unexpected_error", type=type(exc).__name__, message=str(exc))
            return 1

    action = (os.environ.get("ACTION", "RESET").strip().upper() or "RESET")
    if action not in _VALID_ACTIONS:
        emit("config_error", bad_action=action, allowed=sorted(_VALID_ACTIONS))
        return 2
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
                emit("terminated_cannot_reset", instance_id=instance_id,
                     display_name=inst.display_name)
                return 0
            return _action_and_poll(
                compute, instance_id, action,
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
        return _action_and_poll(
            compute, matches[0].id, action,
            {"display_name": display_name, "by": "display_name"})
    except oci.exceptions.ServiceError as exc:  # type: ignore[attr-defined]
        emit("service_error", status_code=exc.status, code=exc.code, message=exc.message)
        return 1
    except Exception as exc:  # noqa: BLE001
        emit("unexpected_error", type=type(exc).__name__, message=str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
