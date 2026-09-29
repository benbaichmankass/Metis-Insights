#!/usr/bin/env python3
"""Measure tenancy block storage, and (optionally) grow the TRAINER's boot volume.

Built for PI-20260929-BTG1YDOI-0002 (trainer root disk at 92%, 2026-09-29;
operator Tier-2 OK "All three (Recommended)", relayed by the manager). Driven by
`.github/workflows/trainer-boot-volume.yml`.

Why the total is measured FIRST, every run: OCI Always Free covers 200 GB of
block storage in total, BOOT VOLUMES AND BLOCK VOLUMES COMBINED. Growing one boot
volume past that ceiling starts a monthly bill silently. So the resize is refused
unless the projected total (current total - trainer's current size + target) is
<= the free ceiling. Over the ceiling, the run prints the overage and a list-price
estimate and exits 3 without touching anything.

The target instance is identified from the TRAINER's own IMDS document (passed in
as a file), never by display name and never via the live VM: this tool can only
ever resize the boot volume attached to the instance whose IMDS it was handed.

Modes:
  measure  read-only. Prints the per-volume table, the total, and the verdict.
  resize   measure, then grow the trainer boot volume to --size-gb if (and only
           if) the projected total stays within the free ceiling. Shrinking is
           never attempted (OCI cannot shrink a volume); a volume already at or
           above the target is a no-op.

Exit codes: 0 ok / no-op, 2 bad input or API failure, 3 refused (would exceed the
free ceiling), 4 resize issued but the volume did not reach the target size.

Env: OCI_CLI_USER, OCI_CLI_FINGERPRINT, OCI_CLI_TENANCY, OCI_CLI_KEY_CONTENT,
     OCI_CLI_REGION (the standard OCI_CLI_* secrets the other oci-* workflows use).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

FREE_CEILING_GB = 200
# ⚠️ INFERRED list price, not read from any bill: OCI Block Volume storage
# $0.0255/GB-month + Balanced performance (10 VPU/GB x $0.0017) = $0.0425/GB-month.
# Printed only to size an overage for the operator; nothing is spent on it.
LIST_PRICE_PER_GB_MONTH = 0.0425
LIVE_STATES = {"PROVISIONING", "RESTORING", "AVAILABLE", "FAULTY"}


def _config() -> dict:
    missing = [k for k in ("OCI_CLI_USER", "OCI_CLI_FINGERPRINT", "OCI_CLI_TENANCY",
                           "OCI_CLI_KEY_CONTENT") if not os.environ.get(k)]
    if missing:
        print(f"ERROR: missing env: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)
    return {
        "user": os.environ["OCI_CLI_USER"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
        "region": os.environ.get("OCI_CLI_REGION") or "eu-paris-1",
    }


def _compartments(identity, tenancy: str) -> tuple[list[str], str]:
    """Every ACTIVE compartment in the tenancy plus the root, and the scope read."""
    import oci  # noqa: PLC0415

    try:
        subs = oci.pagination.list_call_get_all_results(
            identity.list_compartments, tenancy,
            compartment_id_in_subtree=True, access_level="ACCESSIBLE",
            lifecycle_state="ACTIVE").data
        return [tenancy] + [c.id for c in subs], "tenancy root + all accessible sub-compartments"
    except Exception as exc:  # noqa: BLE001 — scope is reported, never hidden
        print(f"WARNING: could not list sub-compartments ({exc}); measuring the root only",
              file=sys.stderr)
        return [tenancy], "tenancy root ONLY (sub-compartment listing failed)"


def measure(cfg: dict, imds: dict) -> dict:
    import oci  # noqa: PLC0415

    identity = oci.identity.IdentityClient(cfg)
    bs = oci.core.BlockstorageClient(cfg)
    compute = oci.core.ComputeClient(cfg)
    tenancy = cfg["tenancy"]

    ads = [a.name for a in identity.list_availability_domains(tenancy).data]
    comps, scope = _compartments(identity, tenancy)

    rows = []
    for comp in comps:
        for ad in ads:
            for bv in oci.pagination.list_call_get_all_results(
                    bs.list_boot_volumes, availability_domain=ad, compartment_id=comp).data:
                if bv.lifecycle_state in LIVE_STATES:
                    rows.append({"kind": "boot", "name": bv.display_name, "id": bv.id,
                                 "size_gb": int(bv.size_in_gbs), "state": bv.lifecycle_state,
                                 "vpus_per_gb": bv.vpus_per_gb})
        for v in oci.pagination.list_call_get_all_results(
                bs.list_volumes, compartment_id=comp).data:
            if v.lifecycle_state in LIVE_STATES:
                rows.append({"kind": "block", "name": v.display_name, "id": v.id,
                             "size_gb": int(v.size_in_gbs), "state": v.lifecycle_state,
                             "vpus_per_gb": v.vpus_per_gb})

    # The trainer's boot volume, resolved from ITS OWN instance id.
    inst_id = imds["id"]
    atts = compute.list_boot_volume_attachments(
        availability_domain=imds["availabilityDomain"],
        compartment_id=imds["compartmentId"], instance_id=inst_id).data
    atts = [a for a in atts if a.lifecycle_state == "ATTACHED"]
    if len(atts) != 1:
        print(f"ERROR: expected exactly 1 ATTACHED boot volume on {inst_id}, found {len(atts)}",
              file=sys.stderr)
        sys.exit(2)
    trainer_bv = bs.get_boot_volume(atts[0].boot_volume_id).data

    total = sum(r["size_gb"] for r in rows)
    ids = {r["id"] for r in rows}
    if trainer_bv.id not in ids:
        # Denominator check: the enumeration must be able to see the one volume
        # we KNOW exists. If it cannot, the total is not trustworthy.
        print("ERROR: the trainer's boot volume is not in the enumerated set — "
              "the tenancy total cannot be trusted", file=sys.stderr)
        sys.exit(2)
    return {"scope": scope, "availability_domains": ads, "compartments_read": len(comps),
            "volumes": rows, "total_gb": total,
            "trainer": {"instance_id": inst_id, "display_name": imds.get("displayName"),
                        "boot_volume_id": trainer_bv.id,
                        "boot_volume_name": trainer_bv.display_name,
                        "size_gb": int(trainer_bv.size_in_gbs)}}


def verdict(m: dict, target: int) -> dict:
    cur = m["trainer"]["size_gb"]
    projected = m["total_gb"] - cur + max(cur, target)
    over = max(0, projected - FREE_CEILING_GB)
    return {"target_gb": target, "current_gb": cur, "projected_total_gb": projected,
            "free_ceiling_gb": FREE_CEILING_GB, "within_free": over == 0,
            "overage_gb": over,
            "est_monthly_cost_usd_list_price": round(over * LIST_PRICE_PER_GB_MONTH, 2)}


def resize(cfg: dict, bv_id: str, target: int, timeout_s: int = 900) -> int:
    import oci  # noqa: PLC0415

    bs = oci.core.BlockstorageClient(cfg)
    bs.update_boot_volume(bv_id, oci.core.models.UpdateBootVolumeDetails(size_in_gbs=target))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        bv = bs.get_boot_volume(bv_id).data
        print(f"  poll: state={bv.lifecycle_state} size_gb={bv.size_in_gbs}")
        if bv.lifecycle_state == "AVAILABLE" and int(bv.size_in_gbs) >= target:
            return int(bv.size_in_gbs)
        time.sleep(15)
    return int(bs.get_boot_volume(bv_id).data.size_in_gbs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=["measure", "resize"], default="measure")
    ap.add_argument("--size-gb", type=int, default=100)
    ap.add_argument("--imds", required=True, help="trainer IMDS /opc/v2/instance/ JSON file")
    ap.add_argument("--out", default="boot_volume_report.json")
    a = ap.parse_args(argv)
    if not 50 <= a.size_gb <= FREE_CEILING_GB:
        print("ERROR: --size-gb must be within [50, 200]", file=sys.stderr)
        return 2

    cfg = _config()
    with open(a.imds, encoding="utf-8") as fh:
        imds = json.load(fh)
    m = measure(cfg, imds)
    v = verdict(m, a.size_gb)
    report = {"mode": a.mode, "measure": m, "verdict": v, "resized_to_gb": None}

    print(f"scope: {m['scope']} ({m['compartments_read']} compartments, ADs {m['availability_domains']})")
    for r in sorted(m["volumes"], key=lambda r: (r["kind"], r["name"] or "")):
        print(f"  {r['kind']:5} {r['size_gb']:>4} GB  {r['state']:<11} vpus={r['vpus_per_gb']}  {r['name']}")
    print(f"TENANCY BLOCK TOTAL: {m['total_gb']} GB of {FREE_CEILING_GB} GB free "
          f"({len(m['volumes'])} volumes)")
    print(f"trainer boot volume: {m['trainer']['boot_volume_name']} = {v['current_gb']} GB")
    print(f"projected total at {a.size_gb} GB: {v['projected_total_gb']} GB -> "
          + ("WITHIN free tier" if v["within_free"] else
             f"EXCEEDS by {v['overage_gb']} GB (~${v['est_monthly_cost_usd_list_price']}/month, list-price estimate)"))

    rc = 0
    if a.mode == "resize":
        if v["current_gb"] >= a.size_gb:
            print(f"no-op: boot volume already {v['current_gb']} GB >= {a.size_gb} GB")
            report["resized_to_gb"] = v["current_gb"]
        elif not v["within_free"]:
            print("REFUSED: resize would exceed the Always Free block-storage ceiling; nothing changed")
            rc = 3
        else:
            print(f"resizing {m['trainer']['boot_volume_id']} {v['current_gb']} -> {a.size_gb} GB (online)")
            got = resize(cfg, m["trainer"]["boot_volume_id"], a.size_gb)
            report["resized_to_gb"] = got
            print(f"boot volume now {got} GB")
            rc = 0 if got >= a.size_gb else 4

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return rc


if __name__ == "__main__":
    sys.exit(main())
