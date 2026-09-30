#!/usr/bin/env python3
"""Back up, then delete, the ONE orphaned block volume `ict-bot-data-vol`.

Operator decision, 2026-09-29 ~16:46Z, popup answer verbatim: "Back up, then
delete (Recommended)", relayed by the manager (PI-20260929-CMXYTHSP-0002).
Evidence it is unused: trainer-boot-volume measure #14283 found NO attachment
record in any state; status-check #14285 showed live /data/bot-data is a
directory on the boot volume (not a mountpoint, a single disk, no fstab entry).

Deleting a volume cannot be undone, so this tool refuses unless EVERY check
passes, in this order:

  1. The target is PINNED. The volume must match all of EXPECTED_NAME,
     EXPECTED_SIZE_GB and EXPECTED_CREATED (time_created is immutable), and
     exactly one volume in the tenancy may match. In execute mode the caller
     must also echo that volume's OCID back as --volume-id; a mismatch refuses.
     `plan` mode is read-only and prints the OCID to echo.
  2. The volume is AVAILABLE and has NO attachment record in ANY state. A
     listing error refuses. It does NOT count as "none".
  3. A FULL backup is created and polled until AVAILABLE. A backup that ends
     FAULTY/TERMINATED, or does not reach AVAILABLE within the timeout,
     refuses with no delete.
  4. Attachments are re-checked immediately before the delete (same rule as 2).
  5. delete_volume, then poll until TERMINATED (or 404). The backup id and
     the post-state are printed.

Exit codes: 0 done / plan printed; 2 refused before any mutation; 3 refused
after the backup was taken (the backup is kept, the volume is untouched);
4 the delete was issued but the volume did not reach TERMINATED in time.

Env: OCI_CLI_USER, OCI_CLI_FINGERPRINT, OCI_CLI_TENANCY, OCI_CLI_KEY_CONTENT,
OCI_CLI_REGION.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

EXPECTED_NAME = "ict-bot-data-vol"
EXPECTED_SIZE_GB = 100
# Immutable; MEASURED by trainer-boot-volume measure #14283 (run 36597942261).
EXPECTED_CREATED = dt.datetime(2026, 5, 11, 10, 29, 0, 512000, tzinfo=dt.timezone.utc)
BACKUP_NAME = "ict-bot-data-vol-final-backup-20260929"
LIVE_STATES = {"PROVISIONING", "RESTORING", "AVAILABLE", "FAULTY"}


class Refused(Exception):
    """A check failed; `code` is the exit code, and no further mutation happens."""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


def _is_target(v) -> bool:
    created = v.time_created
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    return (v.display_name == EXPECTED_NAME
            and v.size_in_gbs is not None and int(v.size_in_gbs) == EXPECTED_SIZE_GB
            and created == EXPECTED_CREATED)


def resolve_target(list_volumes, compartments: list[str]):
    """Exactly one live volume matching the pinned identity, or Refused."""
    matches = []
    for comp in compartments:
        for v in list_volumes(comp):
            if v.lifecycle_state in LIVE_STATES and _is_target(v):
                matches.append(v)
    if len(matches) != 1:
        raise Refused(f"expected exactly 1 volume matching the pinned identity "
                      f"({EXPECTED_NAME}, {EXPECTED_SIZE_GB} GB, created "
                      f"{EXPECTED_CREATED.isoformat()}), found {len(matches)}")
    v = matches[0]
    if v.lifecycle_state != "AVAILABLE":
        raise Refused(f"volume is {v.lifecycle_state}, not AVAILABLE")
    return v


def assert_unattached(list_attachments, compartments: list[str], vol_id: str, code: int = 2) -> None:
    """Refuse on ANY attachment record (any state), or on any listing error."""
    for comp in compartments:
        try:
            atts = list_attachments(comp, vol_id)
        except Exception as exc:  # noqa: BLE001 — an unreadable listing is not "none"
            raise Refused(f"could not list attachments in {comp}: {exc}", code) from exc
        if atts:
            desc = ", ".join(f"{a.lifecycle_state}->{a.instance_id}" for a in atts)
            raise Refused(f"volume has attachment record(s): {desc}", code)


def backup_and_wait(create_backup, get_backup, vol_id: str, timeout_s: int,
                    poll_s: int = 30, sleep=time.sleep, now=time.monotonic):
    """Create a FULL backup and wait for AVAILABLE, or Refused (exit 2, no delete)."""
    try:
        b = create_backup(vol_id, BACKUP_NAME)
    except Exception as exc:  # noqa: BLE001
        raise Refused(f"create_volume_backup failed: {exc}") from exc
    print(f"backup requested: {b.id} ({b.lifecycle_state})")
    deadline = now() + timeout_s
    while True:
        b = get_backup(b.id)
        print(f"  backup poll: {b.lifecycle_state}")
        if b.lifecycle_state == "AVAILABLE":
            return b
        if b.lifecycle_state in ("FAULTY", "TERMINATING", "TERMINATED"):
            raise Refused(f"backup {b.id} ended {b.lifecycle_state}; volume NOT deleted")
        if now() >= deadline:
            raise Refused(f"backup {b.id} not AVAILABLE within {timeout_s}s "
                          f"(last {b.lifecycle_state}); volume NOT deleted")
        sleep(poll_s)


def run(api, *, mode: str, volume_id: str, compartments: list[str],
        backup_timeout_s: int, delete_timeout_s: int, sleep=time.sleep,
        now=time.monotonic) -> int:
    v = resolve_target(api.list_volumes, compartments)
    print(f"target: {v.display_name} {v.id} {int(v.size_in_gbs)} GB {v.lifecycle_state} "
          f"created {v.time_created}")
    assert_unattached(api.list_attachments, compartments, v.id)
    print("attachments: none, in any state")
    if mode == "plan":
        print(f"PLAN ONLY — nothing changed. To execute, dispatch mode: execute with "
              f"volume_id: {v.id} and confirm: yes")
        return 0
    if volume_id != v.id:
        raise Refused("volume_id does not match the pinned target's OCID; nothing changed")

    b = backup_and_wait(api.create_backup, api.get_backup, v.id, backup_timeout_s,
                        sleep=sleep, now=now)
    print(f"BACKUP AVAILABLE: {b.id} ({b.display_name})")
    # Re-check right before the delete; a failure now keeps the backup (exit 3).
    assert_unattached(api.list_attachments, compartments, v.id, code=3)
    print("attachments re-checked: none; deleting")
    api.delete_volume(v.id)
    deadline = now() + delete_timeout_s
    state = None
    while now() < deadline:
        state = api.volume_state(v.id)
        print(f"  delete poll: {state}")
        if state in ("TERMINATED", "NOT_FOUND"):
            print(f"POST-STATE: volume {v.id} {state}; backup {b.id} AVAILABLE")
            return 0
        sleep(15)
    print(f"ERROR: delete issued but volume state is {state} after {delete_timeout_s}s",
          file=sys.stderr)
    return 4


class OciApi:  # pragma: no cover — thin SDK adapter, exercised only against OCI
    def __init__(self, cfg: dict):
        import oci  # noqa: PLC0415

        self.oci = oci
        self.bs = oci.core.BlockstorageClient(cfg)
        self.compute = oci.core.ComputeClient(cfg)
        self.identity = oci.identity.IdentityClient(cfg)

    def compartments(self, tenancy: str) -> list[str]:
        subs = self.oci.pagination.list_call_get_all_results(
            self.identity.list_compartments, tenancy, compartment_id_in_subtree=True,
            access_level="ACCESSIBLE", lifecycle_state="ACTIVE").data
        return [tenancy] + [c.id for c in subs]

    def list_volumes(self, comp):
        return self.oci.pagination.list_call_get_all_results(
            self.bs.list_volumes, compartment_id=comp).data

    def list_attachments(self, comp, vol_id):
        return self.oci.pagination.list_call_get_all_results(
            self.compute.list_volume_attachments, compartment_id=comp, volume_id=vol_id).data

    def create_backup(self, vol_id, name):
        return self.bs.create_volume_backup(self.oci.core.models.CreateVolumeBackupDetails(
            volume_id=vol_id, display_name=name, type="FULL")).data

    def get_backup(self, bid):
        return self.bs.get_volume_backup(bid).data

    def delete_volume(self, vol_id):
        self.bs.delete_volume(vol_id)

    def volume_state(self, vol_id):
        try:
            return self.bs.get_volume(vol_id).data.lifecycle_state
        except self.oci.exceptions.ServiceError as exc:
            if exc.status == 404:
                return "NOT_FOUND"
            raise


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=["plan", "execute"], default="plan")
    ap.add_argument("--volume-id", default="")
    ap.add_argument("--backup-timeout-s", type=int, default=5400)
    ap.add_argument("--delete-timeout-s", type=int, default=600)
    a = ap.parse_args(argv)
    missing = [k for k in ("OCI_CLI_USER", "OCI_CLI_FINGERPRINT", "OCI_CLI_TENANCY",
                           "OCI_CLI_KEY_CONTENT") if not os.environ.get(k)]
    if missing:
        print(f"ERROR: missing env: {', '.join(missing)}", file=sys.stderr)
        return 2
    cfg = {"user": os.environ["OCI_CLI_USER"], "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
           "tenancy": os.environ["OCI_CLI_TENANCY"], "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
           "region": os.environ.get("OCI_CLI_REGION") or "eu-paris-1"}
    api = OciApi(cfg)
    try:
        comps = api.compartments(cfg["tenancy"])
    except Exception as exc:  # noqa: BLE001
        print(f"REFUSED: could not list compartments ({exc})", file=sys.stderr)
        return 2
    try:
        return run(api, mode=a.mode, volume_id=a.volume_id, compartments=comps,
                   backup_timeout_s=a.backup_timeout_s, delete_timeout_s=a.delete_timeout_s)
    except Refused as r:
        print(f"REFUSED: {r}", file=sys.stderr)
        return r.code


if __name__ == "__main__":
    sys.exit(main())
