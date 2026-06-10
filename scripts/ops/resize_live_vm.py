#!/usr/bin/env python3
"""Resize the LIVE trader VM (Ampere A1.Flex) within the OCI Always-Free pool.

Ampere A1.Flex shape changes (OCPU / memory) require the instance to be
STOPPED, so this is a stop -> update-shape -> start sequence. The live trader
is DOWN for the duration (typically 2-5 min); open positions sit on their
broker-side SL/TP, and the trader's systemd units bring it back automatically
on START (DB is on the /data/bot-data block volume, WAL is crash-safe).

Always-Free Ampere ceiling is 4 OCPU / 24 GB tenancy-wide, shared with the
trainer VM (1 OCPU / 6 GB). The intended max for the live VM that keeps the
trainer alive is therefore 3 OCPU / 18 GB. The OCI API itself rejects an
over-quota update, so this script does not need to see the trainer's
allocation — it surfaces whatever the API says.

Auth: standard OCI_CLI_* env vars (same secrets the provision workflow uses).
Inputs (env):
  LIVE_INSTANCE_ID   required — OCID of the live trader instance
  TARGET_OCPUS       required — e.g. 3
  TARGET_MEMORY_GBS  required — e.g. 18
  DRY_RUN            optional — "1"/"true" => report current shape + plan, do
                     NOT stop/resize/start (a safe pre-flight)
  STOP_WAIT_S        optional — max seconds to wait for STOPPED  (default 360)
  START_WAIT_S       optional — max seconds to wait for RUNNING  (default 360)

Emits one JSON object per phase to stdout (newline-delimited) plus a final
{"event":"resize_result", ...}. Exit 0 on success, non-zero on failure.
"""
from __future__ import annotations

import json
import os
import sys
import time


def emit(event: str, **fields) -> None:
    rec = {"event": event, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rec.update(fields)
    print(json.dumps(rec), flush=True)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    try:
        import oci  # noqa: F401
    except Exception as exc:  # pragma: no cover - import guard
        emit("error", phase="import", message=f"oci SDK import failed: {exc}")
        return 2

    instance_id = os.environ.get("LIVE_INSTANCE_ID", "").strip()
    if not instance_id:
        emit("error", phase="input", message="LIVE_INSTANCE_ID is empty")
        return 2
    try:
        target_ocpus = float(os.environ["TARGET_OCPUS"])
        target_mem = float(os.environ["TARGET_MEMORY_GBS"])
    except (KeyError, ValueError) as exc:
        emit("error", phase="input", message=f"bad TARGET_OCPUS/TARGET_MEMORY_GBS: {exc}")
        return 2

    dry_run = _truthy(os.environ.get("DRY_RUN"))
    stop_wait = int(os.environ.get("STOP_WAIT_S", "360"))
    start_wait = int(os.environ.get("START_WAIT_S", "360"))

    cfg = {
        "user": os.environ["OCI_CLI_USER"],
        "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "region": os.environ["OCI_CLI_REGION"],
    }
    oci.config.validate_config(cfg)
    compute = oci.core.ComputeClient(cfg)

    # --- pre-flight: current shape -------------------------------------------
    inst = compute.get_instance(instance_id).data
    cur_ocpus = getattr(inst.shape_config, "ocpus", None)
    cur_mem = getattr(inst.shape_config, "memory_in_gbs", None)
    emit(
        "preflight",
        display_name=inst.display_name,
        shape=inst.shape,
        lifecycle_state=inst.lifecycle_state,
        current_ocpus=cur_ocpus,
        current_memory_gbs=cur_mem,
        target_ocpus=target_ocpus,
        target_memory_gbs=target_mem,
    )

    if "Flex" not in (inst.shape or ""):
        emit("error", phase="preflight", message=f"shape {inst.shape!r} is not Flex — not resizable")
        return 3

    if cur_ocpus == target_ocpus and cur_mem == target_mem:
        emit("noop", message="instance already at target shape; nothing to do")
        emit("resize_result", status="noop", ocpus=cur_ocpus, memory_gbs=cur_mem)
        return 0

    if dry_run:
        emit("dry_run", message="DRY_RUN set — not stopping/resizing/starting")
        emit("resize_result", status="dry_run", current_ocpus=cur_ocpus,
             current_memory_gbs=cur_mem, target_ocpus=target_ocpus, target_memory_gbs=target_mem)
        return 0

    # --- stop ----------------------------------------------------------------
    try:
        if inst.lifecycle_state not in ("STOPPED", "STOPPING"):
            emit("stop", message="issuing SOFTSTOP")
            compute.instance_action(instance_id, "SOFTSTOP")
        get_resp = compute.get_instance(instance_id)
        oci.wait_until(compute, get_resp, "lifecycle_state", "STOPPED",
                       max_wait_seconds=stop_wait, max_interval_seconds=10)
        emit("stopped", message="instance reached STOPPED")
    except Exception as exc:
        emit("error", phase="stop", message=f"{type(exc).__name__}: {exc}")
        # Try a hard STOP as a fallback if SOFTSTOP stalled.
        try:
            emit("stop", message="SOFTSTOP stalled — issuing hard STOP")
            compute.instance_action(instance_id, "STOP")
            oci.wait_until(compute, compute.get_instance(instance_id), "lifecycle_state",
                           "STOPPED", max_wait_seconds=stop_wait, max_interval_seconds=10)
            emit("stopped", message="instance reached STOPPED (after hard STOP)")
        except Exception as exc2:
            emit("error", phase="stop_hard", message=f"{type(exc2).__name__}: {exc2}")
            emit("resize_result", status="failed_stop")
            return 4

    # --- update shape --------------------------------------------------------
    try:
        details = oci.core.models.UpdateInstanceDetails(
            shape_config=oci.core.models.UpdateInstanceShapeConfigDetails(
                ocpus=target_ocpus, memory_in_gbs=target_mem,
            )
        )
        compute.update_instance(instance_id, details)
        emit("updated", message="shape_config update accepted",
             ocpus=target_ocpus, memory_gbs=target_mem)
    except Exception as exc:
        emit("error", phase="update", message=f"{type(exc).__name__}: {exc}")
        # Best-effort: bring it back up at the OLD shape so we never leave the
        # trader stopped on a failed resize.
        try:
            compute.instance_action(instance_id, "START")
            oci.wait_until(compute, compute.get_instance(instance_id), "lifecycle_state",
                           "RUNNING", max_wait_seconds=start_wait, max_interval_seconds=10)
            emit("recovered", message="restarted at original shape after failed update")
        except Exception as exc2:
            emit("error", phase="recover_start", message=f"{type(exc2).__name__}: {exc2}")
        emit("resize_result", status="failed_update")
        return 5

    # --- start ---------------------------------------------------------------
    try:
        compute.instance_action(instance_id, "START")
        get_resp = compute.get_instance(instance_id)
        oci.wait_until(compute, get_resp, "lifecycle_state", "RUNNING",
                       max_wait_seconds=start_wait, max_interval_seconds=10)
        emit("started", message="instance reached RUNNING")
    except Exception as exc:
        emit("error", phase="start", message=f"{type(exc).__name__}: {exc}")
        emit("resize_result", status="failed_start")
        return 6

    final = compute.get_instance(instance_id).data
    emit(
        "resize_result",
        status="ok",
        ocpus=getattr(final.shape_config, "ocpus", None),
        memory_gbs=getattr(final.shape_config, "memory_in_gbs", None),
        lifecycle_state=final.lifecycle_state,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
