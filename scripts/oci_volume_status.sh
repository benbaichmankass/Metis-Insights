#!/usr/bin/env bash
# scripts/oci_volume_status.sh — list block volumes and their state in the
# compartment. Read-only: never mutates OCI state.
#
# Usage:
#   ./scripts/oci_volume_status.sh              # table of all volumes
#   ./scripts/oci_volume_status.sh --dry-run    # print command, don't run
#
# Env:
#   OCI_COMPARTMENT_OCID  — defaults to OCI_CLI_TENANCY.
#   OCI_CLI_REGION        — defaults to eu-paris-1.
set -euo pipefail

COMPARTMENT_OCID="${OCI_COMPARTMENT_OCID:-${OCI_CLI_TENANCY:-}}"
REGION="${OCI_CLI_REGION:-eu-paris-1}"
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 64 ;;
    esac
done

if [[ -z "$COMPARTMENT_OCID" ]]; then
    echo "ERROR: OCI_COMPARTMENT_OCID (or OCI_CLI_TENANCY) must be set." >&2
    exit 2
fi

cmd=(oci bv volume list
    --compartment-id "$COMPARTMENT_OCID"
    --region "$REGION"
    --lifecycle-state AVAILABLE
    --query 'data[*].{name:"display-name",id:id,sizeGB:"size-in-gbs",ad:"availability-domain"}'
    --output table)

if $DRY_RUN; then
    printf 'DRY-RUN: %s\n' "${cmd[*]}"
    exit 0
fi

"${cmd[@]}"
