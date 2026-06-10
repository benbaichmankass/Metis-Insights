#!/usr/bin/env python3
"""Provision the ICT training-center VM on Oracle Cloud (S-AI-WS9).

Counterpart to the live trader VM. Runs the model training center
(data → features → trainer → registry → promotion) so heavy
training never touches the live trader VM. This script provisions
ONLY the infrastructure — it does NOT start any trainer service.
Cloud-init lands the bootstrap script on the VM but leaves
`ict-trainer.service` disabled until the operator explicitly
enables it. Live trading safety > training convenience.

Reads OCI credentials from the standard env vars set by the
``provision-training-vm.yml`` workflow:

  - ``OCI_CLI_USER``           User OCID
  - ``OCI_CLI_FINGERPRINT``    API key fingerprint
  - ``OCI_CLI_TENANCY``        Tenancy OCID
  - ``OCI_CLI_REGION``         Region identifier (e.g. ``eu-paris-1``)
  - ``OCI_CLI_KEY_CONTENT``    PEM private key content (multi-line)
  - ``COMPARTMENT_ID``         Compartment OCID where the VM lives
  - ``SUBNET_ID``              Subnet OCID for the VM's primary VNIC
  - ``SSH_AUTHORIZED_KEY``     Public key text injected into ``authorized_keys``
  - ``CLOUD_INIT_PATH`` (opt)  Path to cloud-init user-data file
  - ``DISPLAY_NAME`` (opt)     Instance display name (default ``ict-trainer-vm``)
  - ``IMAGE_OCID`` (opt)       Ubuntu 22.04 ARM image OCID; auto-resolved if absent

Idempotency contract:

- If an instance with ``DISPLAY_NAME`` and ``lifecycle_state != TERMINATED``
  already exists in the compartment, the script EXITS 0 with a
  ``status=already_exists`` event. Re-running cannot create a duplicate.
- If no such instance exists, provision a fresh one.

Quota safety:

- Computes the sum of OCPUs across all RUNNING / PROVISIONING /
  STARTING instances in the compartment + adds the new instance's
  OCPU. If the total exceeds ``ALWAYS_FREE_OCPU_QUOTA`` (default
  4 OCPU — OCI's Ampere A1 Always Free ceiling) the script refuses
  with a ``status=quota_would_exceed`` event and exits 0 (advisory,
  not failed — the operator sees the result on the issue comment).

Output: one JSON object per line on stdout (``status=...`` events).
The workflow parses these to compose the issue comment.

Exit status: 0 on every outcome (success, already_exists, quota
guardrail). Non-zero only on hard infrastructure errors (bad
credentials, unreachable API). The workflow distinguishes outcomes
by parsing the JSONL stream.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import oci

# --- Defaults -------------------------------------------------------

DEFAULT_DISPLAY_NAME = "ict-trainer-vm"
DEFAULT_SHAPE = "VM.Standard.A1.Flex"
DEFAULT_OCPUS = 1
DEFAULT_MEMORY_GB = 6
ALWAYS_FREE_OCPU_QUOTA = 4  # Ampere A1 free-tier ceiling per tenancy
PROVISIONING_TIMEOUT_S = 600
POLL_INTERVAL_S = 10

# Tags applied to the instance so the live VM's monitoring can
# distinguish the training center from the live trader.
INSTANCE_TAGS = {
    "ict-role": "training-center",
    "ict-managed-by": "provision_training_vm.py",
    "ict-workstream": "S-AI-WS9",
}


def emit(status: str, **fields: Any) -> None:
    """Emit a JSONL event to stdout."""
    payload = {"status": status, **fields}
    print(json.dumps(payload))


def _config_from_env() -> dict[str, str]:
    return {
        "user": os.environ["OCI_CLI_USER"],
        "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "region": os.environ["OCI_CLI_REGION"],
    }


def find_existing_instance(
    compute: oci.core.ComputeClient,
    compartment_id: str,
    display_name: str,
) -> Any | None:
    """Return the first non-terminated instance matching display_name,
    or None."""
    page = compute.list_instances(
        compartment_id=compartment_id,
        display_name=display_name,
    ).data
    for inst in page:
        if inst.lifecycle_state != "TERMINATED":
            return inst
    return None


def running_ocpu_total(
    compute: oci.core.ComputeClient,
    compartment_id: str,
) -> int:
    """Sum **Ampere A1.Flex** OCPUs across all non-TERMINATED instances in
    the compartment. Conservative — counts PROVISIONING / STARTING /
    STOPPED as 'consuming quota' because they do.

    Only `VM.Standard.A1.Flex` OCPUs count against the 4-OCPU Always-Free
    Ampere ceiling. x86 shapes (e.g. the live trader's
    `VM.Standard.E2.1.Micro`) draw from a *separate* free allowance, so
    including them here would let a 1-OCPU micro falsely trip the guard and
    block a legitimate Ampere launch (e.g. the live-VM migration: trainer 1
    + new live 3 = 4 ≤ 4, but +1 for the micro would read 5 and refuse)."""
    total = 0
    page = compute.list_instances(compartment_id=compartment_id).data
    for inst in page:
        if inst.lifecycle_state == "TERMINATED":
            continue
        if "A1.Flex" not in (inst.shape or ""):
            continue
        if inst.shape_config and inst.shape_config.ocpus:
            total += int(inst.shape_config.ocpus)
    return total


def resolve_availability_domain(
    identity: oci.identity.IdentityClient,
    tenancy_id: str,
) -> str:
    """Return the first AD name in the region. eu-paris-1 has a
    single AD; multi-AD regions could be enhanced to round-robin."""
    ads = identity.list_availability_domains(tenancy_id).data
    if not ads:
        raise RuntimeError("no availability domains visible in region")
    return ads[0].name


def resolve_ubuntu_arm_image(
    compute: oci.core.ComputeClient,
    compartment_id: str,
) -> str:
    """Pick the latest Ubuntu 22.04 aarch64 image OCID in the region."""
    images = compute.list_images(
        compartment_id=compartment_id,
        operating_system="Canonical Ubuntu",
        operating_system_version="22.04",
        shape=DEFAULT_SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    for img in images:
        # Skip images that aren't ARM (Ampere requires aarch64).
        if "aarch64" in (img.display_name or "").lower():
            return img.id
    # Fallback: take the first image; OCI's shape filter already
    # constrained to A1-compatible.
    if images:
        return images[0].id
    raise RuntimeError("no Ubuntu 22.04 ARM image found in region")


def load_cloud_init(path: str | None) -> str | None:
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def wait_for_running(
    compute: oci.core.ComputeClient,
    instance_id: str,
    timeout_s: int = PROVISIONING_TIMEOUT_S,
) -> dict[str, Any]:
    """Poll until the instance is RUNNING or timeout. Returns a
    dict describing the final state."""
    deadline = time.monotonic() + timeout_s
    last_state = "UNKNOWN"
    while time.monotonic() < deadline:
        inst = compute.get_instance(instance_id).data
        last_state = inst.lifecycle_state
        emit(
            "poll",
            instance_id=instance_id,
            lifecycle_state=last_state,
            elapsed_s=int(time.monotonic() - (deadline - timeout_s)),
        )
        if last_state == "RUNNING":
            return {"lifecycle_state": "RUNNING", "instance": inst}
        if last_state in ("TERMINATED", "TERMINATING"):
            return {"lifecycle_state": last_state, "instance": inst}
        time.sleep(POLL_INTERVAL_S)
    return {"lifecycle_state": last_state, "instance": None, "timed_out": True}


def fetch_public_ip(
    compute: oci.core.ComputeClient,
    vnet: oci.core.VirtualNetworkClient,
    compartment_id: str,
    instance_id: str,
) -> str | None:
    """Resolve the primary VNIC's public IP, if any."""
    attachments = compute.list_vnic_attachments(
        compartment_id=compartment_id, instance_id=instance_id
    ).data
    if not attachments:
        return None
    vnic = vnet.get_vnic(attachments[0].vnic_id).data
    return vnic.public_ip


def provision(
    config: dict[str, str],
    compartment_id: str,
    subnet_id: str,
    ssh_pub_key: str,
    *,
    display_name: str = DEFAULT_DISPLAY_NAME,
    cloud_init_path: str | None = None,
    image_ocid: str | None = None,
    ocpus: int = DEFAULT_OCPUS,
    memory_gb: int = DEFAULT_MEMORY_GB,
) -> int:
    oci.config.validate_config(config)
    identity = oci.identity.IdentityClient(config)
    compute = oci.core.ComputeClient(config)
    vnet = oci.core.VirtualNetworkClient(config)

    # --- Idempotency ---
    existing = find_existing_instance(compute, compartment_id, display_name)
    if existing is not None:
        public_ip = fetch_public_ip(compute, vnet, compartment_id, existing.id)
        emit(
            "already_exists",
            display_name=display_name,
            instance_id=existing.id,
            lifecycle_state=existing.lifecycle_state,
            public_ip=public_ip,
        )
        return 0

    # --- Quota guardrail ---
    current_ocpu = running_ocpu_total(compute, compartment_id)
    if current_ocpu + ocpus > ALWAYS_FREE_OCPU_QUOTA:
        emit(
            "quota_would_exceed",
            current_ocpu=current_ocpu,
            requested_ocpu=ocpus,
            quota=ALWAYS_FREE_OCPU_QUOTA,
            display_name=display_name,
            advice=(
                "Free-tier Ampere A1 quota is 4 OCPU per tenancy. "
                "Terminate an unused VM or request a paid shape."
            ),
        )
        return 0

    # --- Resolve image + AD ---
    ad_name = resolve_availability_domain(identity, config["tenancy"])
    if image_ocid is None:
        image_ocid = resolve_ubuntu_arm_image(compute, compartment_id)
    user_data = load_cloud_init(cloud_init_path)

    emit(
        "launching",
        display_name=display_name,
        availability_domain=ad_name,
        image_ocid=image_ocid,
        shape=DEFAULT_SHAPE,
        ocpus=ocpus,
        memory_gb=memory_gb,
        subnet_id=subnet_id,
    )

    # --- Launch ---
    details = oci.core.models.LaunchInstanceDetails(
        availability_domain=ad_name,
        compartment_id=compartment_id,
        display_name=display_name,
        shape=DEFAULT_SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=ocpus, memory_in_gbs=memory_gb,
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image", image_id=image_ocid,
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id,
            assign_public_ip=True,
        ),
        metadata={
            "ssh_authorized_keys": ssh_pub_key,
            **({"user_data": _b64_user_data(user_data)} if user_data else {}),
        },
        freeform_tags=INSTANCE_TAGS,
    )
    launched = compute.launch_instance(details).data
    emit("launched", instance_id=launched.id)

    # --- Wait for RUNNING ---
    final = wait_for_running(compute, launched.id)
    if final["lifecycle_state"] != "RUNNING":
        emit(
            "provisioning_failed",
            instance_id=launched.id,
            lifecycle_state=final["lifecycle_state"],
            timed_out=final.get("timed_out", False),
        )
        return 0  # informational, not a hard failure

    public_ip = fetch_public_ip(compute, vnet, compartment_id, launched.id)
    emit(
        "ready",
        instance_id=launched.id,
        display_name=display_name,
        public_ip=public_ip,
        ssh_command=(
            f"ssh -i <path-to-private-key> ubuntu@{public_ip}"
            if public_ip else "<no public IP assigned>"
        ),
        tags=INSTANCE_TAGS,
    )
    return 0


def _b64_user_data(user_data: str) -> str:
    """OCI expects cloud-init user-data base64-encoded."""
    import base64
    return base64.b64encode(user_data.encode("utf-8")).decode("ascii")


def main() -> int:
    try:
        compartment_id = os.environ["COMPARTMENT_ID"]
        subnet_id = os.environ["SUBNET_ID"]
        ssh_pub_key = os.environ["SSH_AUTHORIZED_KEY"]
        config = _config_from_env()
    except KeyError as exc:
        emit("config_error", missing_env=exc.args[0])
        return 2
    display_name = os.environ.get("DISPLAY_NAME") or DEFAULT_DISPLAY_NAME
    cloud_init_path = os.environ.get("CLOUD_INIT_PATH") or None
    image_ocid = os.environ.get("IMAGE_OCID") or None
    # OCPU / memory are env-overridable so the same provisioner serves both
    # the trainer (default 1 / 6) and a larger live-trader Ampere VM (3 / 18,
    # the live-VM migration). Defaults preserve the original trainer shape.
    try:
        ocpus = int(os.environ.get("OCPUS") or DEFAULT_OCPUS)
        memory_gb = int(os.environ.get("MEMORY_GB") or DEFAULT_MEMORY_GB)
    except ValueError:
        emit("config_error", missing_env="OCPUS/MEMORY_GB (must be integers)")
        return 2
    try:
        return provision(
            config,
            compartment_id=compartment_id,
            subnet_id=subnet_id,
            ssh_pub_key=ssh_pub_key,
            display_name=display_name,
            cloud_init_path=cloud_init_path,
            image_ocid=image_ocid,
            ocpus=ocpus,
            memory_gb=memory_gb,
        )
    except oci.exceptions.ServiceError as exc:  # type: ignore[attr-defined]
        emit(
            "service_error",
            status_code=exc.status,
            code=exc.code,
            message=exc.message,
        )
        return 1
    except Exception as exc:  # noqa: BLE001 — surface raw error
        emit("unexpected_error", type=type(exc).__name__, message=str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
