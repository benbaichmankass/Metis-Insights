"""Read the ACTUAL OCI compute inventory and diff it against what we declare.

Motivation (2026-08-18). The repo's canonical docs describe a VM topology in
prose — live trader 2 OCPU / 12 GB, trainer 1/6, gateway 1/6, x86 micro
terminated. Nothing ever checked that against the cloud. Two things made that
gap urgent:

* Oracle halved the Always Free Ampere allowance to 2 OCPU / 12 GB on
  2026-06-15, so the documented 4/24 total may now exceed the free ceiling.
* A stale reference was found the hard way — ``DIAG_BASE_URL`` still pointed at
  ``158.178.210.252``, a micro terminated on 2026-06-16, and failed *silently*.

**This asks the cloud rather than modelling it.** Shapes, OCPU and memory come
from the OCI API, never from a table in this file that could drift the same way
the prose did.

Three verdicts per instance, never collapsed:

    match       declared, and the live shape agrees
    drift       declared, but the live shape differs (fields listed)
    missing     declared, and no such instance exists live
    undeclared  live, but absent from the expectations file

A fifth state applies to the run as a whole: ``not_declared`` — there IS no
expectations file. That is NOT a pass. "We have nothing to compare against" and
"everything matches" are opposite findings, and collapsing them would let an
empty declaration render as a clean bill of health.

Seeding: ``--emit-expected`` writes the CURRENT live state in expectations
format, for a human to review and commit. The expectations file is deliberately
never auto-written by the diff path — a checker that rewrites its own baseline
on every drift can never fail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

EXPECTED_PATH = Path("comms/cloud/expected_topology.json")

# Always Free Ampere ceilings. BOTH are reported because which one applies
# depends on the tenancy's account type, which is visible only in the OCI
# console billing page and cannot be read from this API.
AMPERE_SHAPE = "VM.Standard.A1.Flex"
CEILING_CURRENT = {"ocpus": 2, "memory_gb": 12}   # Always Free, from 2026-06-15
CEILING_LEGACY = {"ocpus": 4, "memory_gb": 24}    # pre-2026-06-15 / reportedly PAYG

# An instance in these states holds no allocation.
DEAD_STATES = {"TERMINATED", "TERMINATING"}


def emit(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=False))


def _config_from_env() -> dict[str, str]:
    missing = [k for k in ("OCI_CLI_USER", "OCI_CLI_KEY_CONTENT", "OCI_CLI_FINGERPRINT",
                           "OCI_CLI_TENANCY", "OCI_CLI_REGION") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"missing OCI env vars: {', '.join(missing)}")
    return {
        "user": os.environ["OCI_CLI_USER"],
        "key_content": os.environ["OCI_CLI_KEY_CONTENT"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "region": os.environ["OCI_CLI_REGION"],
    }


def fetch_live(compartment_id: str) -> list[dict]:
    """Live compute inventory. Shape/OCPU/memory come from the API, not a table."""
    import oci

    cfg = _config_from_env()
    compute = oci.core.ComputeClient(cfg)
    rows: list[dict] = []
    for inst in oci.pagination.list_call_get_all_results(
        compute.list_instances, compartment_id=compartment_id
    ).data:
        sc = getattr(inst, "shape_config", None)
        rows.append({
            "display_name": inst.display_name,
            "shape": inst.shape,
            "ocpus": getattr(sc, "ocpus", None),
            "memory_gb": getattr(sc, "memory_in_gbs", None),
            "lifecycle_state": inst.lifecycle_state,
            "availability_domain": inst.availability_domain,
            "time_created": str(inst.time_created),
            "ocid": inst.id,
        })
    return sorted(rows, key=lambda r: r["display_name"] or "")


def _shape_fields(row: dict) -> dict:
    return {k: row.get(k) for k in ("shape", "ocpus", "memory_gb")}


def diff(live: list[dict], expected: list[dict] | None) -> dict:
    """Compare live against declared. `expected is None` => not_declared."""
    live_alive = [r for r in live if r["lifecycle_state"] not in DEAD_STATES]

    if expected is None:
        return {
            "declaration_state": "not_declared",
            "note": (f"no expectations file at {EXPECTED_PATH} — this is NOT a pass. "
                     "Seed one with --emit-expected, review it, and commit."),
            "findings": [],
        }

    by_name_live = {r["display_name"]: r for r in live_alive}
    by_name_exp = {r["display_name"]: r for r in expected}
    findings = []

    for name, exp in sorted(by_name_exp.items()):
        got = by_name_live.get(name)
        if got is None:
            findings.append({"display_name": name, "verdict": "missing",
                             "expected": _shape_fields(exp), "actual": None})
            continue
        deltas = {k: {"expected": v, "actual": got.get(k)}
                  for k, v in _shape_fields(exp).items() if got.get(k) != v}
        findings.append({
            "display_name": name,
            "verdict": "drift" if deltas else "match",
            "expected": _shape_fields(exp),
            "actual": _shape_fields(got),
            "lifecycle_state": got["lifecycle_state"],
            **({"deltas": deltas} if deltas else {}),
        })

    for name in sorted(set(by_name_live) - set(by_name_exp)):
        got = by_name_live[name]
        findings.append({"display_name": name, "verdict": "undeclared",
                         "expected": None, "actual": _shape_fields(got),
                         "lifecycle_state": got["lifecycle_state"]})

    return {"declaration_state": "declared", "findings": findings}


def ampere_budget(live: list[dict]) -> dict:
    """Ampere usage vs both free-tier ceilings, split by lifecycle state.

    Deliberately does NOT return a single pass/fail. Which ceiling applies
    depends on the tenancy account type (console-only), and whether a STOPPED
    instance consumes the allowance is a billing rule this API does not state.
    Reporting the split and the assumption beats asserting a verdict we cannot
    substantiate.
    """
    amp = [r for r in live if r["shape"] == AMPERE_SHAPE
           and r["lifecycle_state"] not in DEAD_STATES]
    by_state: dict[str, dict] = {}
    for r in amp:
        b = by_state.setdefault(r["lifecycle_state"], {"instances": 0, "ocpus": 0.0, "memory_gb": 0.0})
        b["instances"] += 1
        b["ocpus"] += r["ocpus"] or 0
        b["memory_gb"] += r["memory_gb"] or 0

    total = {
        "instances": len(amp),
        "ocpus": sum(r["ocpus"] or 0 for r in amp),
        "memory_gb": sum(r["memory_gb"] or 0 for r in amp),
    }
    return {
        "shape": AMPERE_SHAPE,
        "total_all_non_terminated": total,
        "by_lifecycle_state": by_state,
        "ceiling_current_always_free": CEILING_CURRENT,
        "ceiling_legacy_or_payg": CEILING_LEGACY,
        "exceeds_current_ceiling": (total["ocpus"] > CEILING_CURRENT["ocpus"]
                                    or total["memory_gb"] > CEILING_CURRENT["memory_gb"]),
        "exceeds_legacy_ceiling": (total["ocpus"] > CEILING_LEGACY["ocpus"]
                                   or total["memory_gb"] > CEILING_LEGACY["memory_gb"]),
        "caveat": ("Which ceiling binds depends on the tenancy account type "
                   "(Always Free vs upgraded/PAYG), which is visible only in the "
                   "OCI console billing page and is NOT readable from this API. "
                   "Counts include non-TERMINATED instances of every state; "
                   "whether a STOPPED instance consumes the allowance is a "
                   "billing rule this tool does not assert."),
    }


def to_markdown(report: dict) -> str:
    d = report["diff"]
    b = report["ampere_budget"]
    out = ["## OCI inventory", "", f"region `{report['region']}` · {report['instance_count']} instances", ""]

    out += ["### Ampere free-tier budget", "",
            f"| | OCPU | GB |", "|---|---|---|",
            f"| **in use** (non-terminated) | {b['total_all_non_terminated']['ocpus']} | {b['total_all_non_terminated']['memory_gb']} |",
            f"| ceiling — Always Free (since 2026-06-15) | {b['ceiling_current_always_free']['ocpus']} | {b['ceiling_current_always_free']['memory_gb']} |",
            f"| ceiling — legacy / reportedly PAYG | {b['ceiling_legacy_or_payg']['ocpus']} | {b['ceiling_legacy_or_payg']['memory_gb']} |",
            "",
            f"exceeds current Always Free ceiling: **{b['exceeds_current_ceiling']}** · "
            f"exceeds legacy ceiling: **{b['exceeds_legacy_ceiling']}**", "",
            f"> {b['caveat']}", ""]

    out += ["### Declared vs actual", ""]
    if d["declaration_state"] == "not_declared":
        out += [f"⚠️ **{d['declaration_state']}** — {d['note']}", ""]
    else:
        counts: dict[str, int] = {}
        for f in d["findings"]:
            counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
        out += [" · ".join(f"**{v}**: {n}" for v, n in sorted(counts.items())), "",
                "| instance | verdict | expected | actual | state |", "|---|---|---|---|---|"]
        for f in d["findings"]:
            e, a = f.get("expected"), f.get("actual")
            fmt = lambda x: "—" if not x else f"{x['shape']} {x['ocpus']}/{x['memory_gb']}"
            out.append(f"| `{f['display_name']}` | {f['verdict']} | {fmt(e)} | {fmt(a)} "
                       f"| {f.get('lifecycle_state','—')} |")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="OCI compute inventory + declared-topology diff.")
    ap.add_argument("--compartment-id", default=os.environ.get("OCI_COMPARTMENT_OCID")
                    or os.environ.get("COMPARTMENT_ID") or os.environ.get("OCI_CLI_TENANCY"))
    ap.add_argument("--emit-expected", action="store_true",
                    help="print the live state in expectations format (for review, then commit)")
    ap.add_argument("--markdown", action="store_true", help="also print a markdown report")
    ap.add_argument("--fail-on-drift", action="store_true",
                    help="exit 1 when any drift/missing/undeclared finding exists")
    args = ap.parse_args()

    if not args.compartment_id:
        raise SystemExit("no compartment: set OCI_COMPARTMENT_OCID (or COMPARTMENT_ID)")

    live = fetch_live(args.compartment_id)

    if args.emit_expected:
        emit({"_comment": "Declared OCI topology. Reviewed by a human, then committed. "
                          "The diff path never writes this file.",
              "instances": [{"display_name": r["display_name"], **_shape_fields(r),
                             "role": "TODO: describe this instance's role"}
                            for r in live if r["lifecycle_state"] not in DEAD_STATES]})
        return 0

    expected = None
    if EXPECTED_PATH.is_file():
        try:
            expected = json.loads(EXPECTED_PATH.read_text()).get("instances")
        except (OSError, ValueError) as exc:
            raise SystemExit(f"expectations file unreadable ({exc}) — refusing to "
                             "report a clean diff over a file we could not parse")

    report = {
        "region": os.environ.get("OCI_CLI_REGION", "unknown"),
        "compartment_id": args.compartment_id,
        "instance_count": len(live),
        "instances": live,
        "ampere_budget": ampere_budget(live),
        "diff": diff(live, expected),
    }
    emit(report)
    if args.markdown:
        print("\n<!--MARKDOWN-->\n" + to_markdown(report))

    if args.fail_on_drift:
        bad = {"drift", "missing", "undeclared"}
        if report["diff"]["declaration_state"] == "not_declared":
            return 1
        if any(f["verdict"] in bad for f in report["diff"]["findings"]):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
