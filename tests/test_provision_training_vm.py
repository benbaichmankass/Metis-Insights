"""Tests for `scripts/ops/provision_training_vm.py` (S-AI-WS9).

The provisioning script talks to the OCI API; tests mock the
client at the module level so we exercise the control flow
(idempotency, quota guardrail, error paths) without making
network calls.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# Make `scripts.ops` importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

oci = pytest.importorskip("oci")

from scripts.ops import provision_training_vm as ptv  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeInstance:
    def __init__(
        self,
        *,
        id: str = "ocid1.instance.oc1..AAA",
        display_name: str = "ict-trainer-vm",
        lifecycle_state: str = "RUNNING",
        ocpus: int = 1,
    ) -> None:
        self.id = id
        self.display_name = display_name
        self.lifecycle_state = lifecycle_state
        self.shape_config = mock.SimpleNamespace(ocpus=ocpus, memory_in_gbs=6)


class _FakeResponse:
    def __init__(self, data: Any) -> None:
        self.data = data


def _make_compute_client(
    *,
    existing_instances: list[_FakeInstance] | None = None,
    list_images_data: list[Any] | None = None,
    launched_instance: _FakeInstance | None = None,
    instance_state_progression: list[str] | None = None,
    vnic_attachments: list[Any] | None = None,
) -> mock.MagicMock:
    """Build a MagicMock that satisfies the ComputeClient surface
    `provision()` uses."""
    client = mock.MagicMock(spec=oci.core.ComputeClient)
    client.list_instances.return_value = _FakeResponse(existing_instances or [])
    if list_images_data is None:
        list_images_data = [
            mock.SimpleNamespace(
                id="ocid1.image.oc1..IMG", display_name="Ubuntu-22.04-aarch64",
            )
        ]
    client.list_images.return_value = _FakeResponse(list_images_data)
    if launched_instance is None:
        launched_instance = _FakeInstance(lifecycle_state="PROVISIONING")
    client.launch_instance.return_value = _FakeResponse(launched_instance)
    if instance_state_progression is None:
        instance_state_progression = ["PROVISIONING", "RUNNING"]
    states = iter(instance_state_progression)

    def _get_instance(_id: str) -> _FakeResponse:
        try:
            state = next(states)
        except StopIteration:
            state = "RUNNING"
        return _FakeResponse(
            _FakeInstance(id=launched_instance.id, lifecycle_state=state)
        )

    client.get_instance.side_effect = _get_instance
    client.list_vnic_attachments.return_value = _FakeResponse(
        vnic_attachments
        or [mock.SimpleNamespace(vnic_id="ocid1.vnic.oc1..VNIC")]
    )
    return client


def _make_vnet_client(*, public_ip: str | None = "203.0.113.10") -> mock.MagicMock:
    client = mock.MagicMock(spec=oci.core.VirtualNetworkClient)
    client.get_vnic.return_value = _FakeResponse(
        mock.SimpleNamespace(public_ip=public_ip)
    )
    return client


def _make_identity_client() -> mock.MagicMock:
    client = mock.MagicMock(spec=oci.identity.IdentityClient)
    client.list_availability_domains.return_value = _FakeResponse(
        [mock.SimpleNamespace(name="zZIE:EU-PARIS-1-AD-1")]
    )
    return client


_CONFIG = {
    "user": "u",
    "key_content": "k",
    "fingerprint": "f",
    "tenancy": "t",
    "region": "eu-paris-1",
}


def _capture(callable_, *args, **kwargs) -> tuple[int, list[dict]]:
    """Run `callable_` while capturing stdout JSONL events."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = callable_(*args, **kwargs)
    finally:
        sys.stdout = saved
    events = []
    for line in buf.getvalue().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return rc, events


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_skips_when_instance_already_exists(self, monkeypatch):
        existing = _FakeInstance(
            id="ocid1.instance.oc1..EXISTING", lifecycle_state="RUNNING",
        )
        compute = _make_compute_client(existing_instances=[existing])
        vnet = _make_vnet_client(public_ip="198.51.100.7")
        identity = _make_identity_client()

        monkeypatch.setattr(oci.config, "validate_config", lambda c: None)
        monkeypatch.setattr(oci.core, "ComputeClient", lambda c: compute)
        monkeypatch.setattr(oci.core, "VirtualNetworkClient", lambda c: vnet)
        monkeypatch.setattr(oci.identity, "IdentityClient", lambda c: identity)

        rc, events = _capture(
            ptv.provision,
            _CONFIG,
            compartment_id="ocid1.compartment.oc1..X",
            subnet_id="ocid1.subnet.oc1..Y",
            ssh_pub_key="ssh-ed25519 AAAA...",
        )
        assert rc == 0
        terminal = events[-1]
        assert terminal["status"] == "already_exists"
        assert terminal["instance_id"] == "ocid1.instance.oc1..EXISTING"
        # No launch attempted.
        compute.launch_instance.assert_not_called()

    def test_treats_terminated_existing_as_absent(self, monkeypatch):
        # A previously terminated instance with same name should be
        # skipped over and a fresh launch attempted.
        terminated = _FakeInstance(lifecycle_state="TERMINATED")
        compute = _make_compute_client(
            existing_instances=[terminated],
            launched_instance=_FakeInstance(
                id="ocid1.instance.oc1..NEW", lifecycle_state="PROVISIONING",
            ),
        )
        vnet = _make_vnet_client()
        identity = _make_identity_client()
        monkeypatch.setattr(oci.config, "validate_config", lambda c: None)
        monkeypatch.setattr(oci.core, "ComputeClient", lambda c: compute)
        monkeypatch.setattr(oci.core, "VirtualNetworkClient", lambda c: vnet)
        monkeypatch.setattr(oci.identity, "IdentityClient", lambda c: identity)
        rc, events = _capture(
            ptv.provision,
            _CONFIG, compartment_id="C", subnet_id="S",
            ssh_pub_key="ssh-ed25519 AAAA...",
        )
        assert rc == 0
        statuses = [e["status"] for e in events]
        # `launching` fires only when proceeding to a fresh launch.
        assert "launching" in statuses
        assert "ready" in statuses
        compute.launch_instance.assert_called_once()


# ---------------------------------------------------------------------------
# Quota guardrail
# ---------------------------------------------------------------------------


class TestQuotaGuardrail:
    def test_refuses_when_quota_would_be_exceeded(self, monkeypatch):
        # Pretend the tenancy already has 4 OCPU in use. Adding 1
        # more would exceed the Always Free ceiling.
        # `find_existing_instance` does a filtered list (only the
        # display_name); the broader `running_ocpu_total` call does
        # a separate list without the filter. We satisfy both by
        # making list_instances side_effect different per-call.
        existing_other = _FakeInstance(
            id="ocid1.instance.oc1..LIVE",
            display_name="ict-live-trader",
            ocpus=4,
        )

        def _list_instances(
            *, compartment_id: str, display_name: str | None = None,
        ) -> _FakeResponse:
            if display_name == "ict-trainer-vm":
                return _FakeResponse([])
            return _FakeResponse([existing_other])

        compute = mock.MagicMock(spec=oci.core.ComputeClient)
        compute.list_instances.side_effect = _list_instances
        vnet = _make_vnet_client()
        identity = _make_identity_client()
        monkeypatch.setattr(oci.config, "validate_config", lambda c: None)
        monkeypatch.setattr(oci.core, "ComputeClient", lambda c: compute)
        monkeypatch.setattr(oci.core, "VirtualNetworkClient", lambda c: vnet)
        monkeypatch.setattr(oci.identity, "IdentityClient", lambda c: identity)
        rc, events = _capture(
            ptv.provision,
            _CONFIG, compartment_id="C", subnet_id="S",
            ssh_pub_key="ssh-ed25519 AAAA...",
        )
        assert rc == 0
        terminal = events[-1]
        assert terminal["status"] == "quota_would_exceed"
        assert terminal["current_ocpu"] == 4
        assert terminal["quota"] == ptv.ALWAYS_FREE_OCPU_QUOTA
        compute.launch_instance.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_launches_and_reports_public_ip(self, monkeypatch):
        compute = _make_compute_client(
            launched_instance=_FakeInstance(
                id="ocid1.instance.oc1..NEW", lifecycle_state="PROVISIONING",
            ),
            instance_state_progression=["PROVISIONING", "RUNNING"],
        )
        vnet = _make_vnet_client(public_ip="203.0.113.42")
        identity = _make_identity_client()
        monkeypatch.setattr(oci.config, "validate_config", lambda c: None)
        monkeypatch.setattr(oci.core, "ComputeClient", lambda c: compute)
        monkeypatch.setattr(oci.core, "VirtualNetworkClient", lambda c: vnet)
        monkeypatch.setattr(oci.identity, "IdentityClient", lambda c: identity)
        # Skip the poll sleep so the test runs fast.
        monkeypatch.setattr(ptv.time, "sleep", lambda _s: None)
        rc, events = _capture(
            ptv.provision,
            _CONFIG, compartment_id="C", subnet_id="S",
            ssh_pub_key="ssh-ed25519 AAAA...",
            display_name="ict-trainer-vm",
        )
        assert rc == 0
        terminal = events[-1]
        assert terminal["status"] == "ready"
        assert terminal["public_ip"] == "203.0.113.42"
        assert "ict-trainer-vm" in terminal.get("display_name", "")
        assert terminal["tags"]["ict-role"] == "training-center"

    def test_passes_cloud_init_when_path_given(
        self, tmp_path: Path, monkeypatch,
    ):
        ci = tmp_path / "cloud-init.yaml"
        ci.write_text("#cloud-config\nfoo: bar\n")
        compute = _make_compute_client()
        vnet = _make_vnet_client()
        identity = _make_identity_client()
        monkeypatch.setattr(oci.config, "validate_config", lambda c: None)
        monkeypatch.setattr(oci.core, "ComputeClient", lambda c: compute)
        monkeypatch.setattr(oci.core, "VirtualNetworkClient", lambda c: vnet)
        monkeypatch.setattr(oci.identity, "IdentityClient", lambda c: identity)
        monkeypatch.setattr(ptv.time, "sleep", lambda _s: None)
        ptv.provision(
            _CONFIG, compartment_id="C", subnet_id="S",
            ssh_pub_key="ssh-ed25519 AAAA...",
            cloud_init_path=str(ci),
        )
        details = compute.launch_instance.call_args[0][0]
        meta = details.metadata
        assert "user_data" in meta
        # Base64-encoded; decode and check the marker.
        import base64
        decoded = base64.b64decode(meta["user_data"]).decode()
        assert "foo: bar" in decoded


# ---------------------------------------------------------------------------
# Provisioning failure
# ---------------------------------------------------------------------------


class TestProvisioningFailure:
    def test_emits_failure_when_state_never_becomes_running(
        self, monkeypatch,
    ):
        compute = _make_compute_client(
            instance_state_progression=["PROVISIONING"] * 100,
        )
        vnet = _make_vnet_client()
        identity = _make_identity_client()
        monkeypatch.setattr(oci.config, "validate_config", lambda c: None)
        monkeypatch.setattr(oci.core, "ComputeClient", lambda c: compute)
        monkeypatch.setattr(oci.core, "VirtualNetworkClient", lambda c: vnet)
        monkeypatch.setattr(oci.identity, "IdentityClient", lambda c: identity)
        monkeypatch.setattr(ptv.time, "sleep", lambda _s: None)
        # Compress the wait loop so the test doesn't actually wait
        # PROVISIONING_TIMEOUT_S real seconds. Patch the timeout to
        # a tiny value.
        monkeypatch.setattr(ptv, "PROVISIONING_TIMEOUT_S", 0)
        rc, events = _capture(
            ptv.provision,
            _CONFIG, compartment_id="C", subnet_id="S",
            ssh_pub_key="ssh-ed25519 AAAA...",
        )
        assert rc == 0
        statuses = [e["status"] for e in events]
        assert "provisioning_failed" in statuses


# ---------------------------------------------------------------------------
# main() entrypoint
# ---------------------------------------------------------------------------


class TestMainEntrypoint:
    def test_missing_env_var_emits_config_error(self, monkeypatch):
        # Clear all relevant env vars.
        for v in (
            "COMPARTMENT_ID", "SUBNET_ID", "SSH_AUTHORIZED_KEY",
            "OCI_CLI_USER", "OCI_CLI_KEY_CONTENT", "OCI_CLI_FINGERPRINT",
            "OCI_CLI_TENANCY", "OCI_CLI_REGION",
        ):
            monkeypatch.delenv(v, raising=False)
        rc, events = _capture(ptv.main)
        assert rc == 2
        assert events[-1]["status"] == "config_error"
