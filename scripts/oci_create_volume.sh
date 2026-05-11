#!/usr/bin/env bash
# scripts/oci_create_volume.sh — create the ict-bot-data-vol block volume.
#
# Idempotent: if a volume with the same display-name already exists in
# AVAILABLE state, prints its OCID and exits 0 without creating a new one.
#
# Usage:
#   ./scripts/oci_create_volume.sh
#   ./scripts/oci_create_volume.sh --dry-run
#
# Env:
#   OCI_COMPARTMENT_OCID    — defaults to OCI_CLI_TENANCY.
#   OCI_CLI_REGION          — defaults to eu-paris-1.
#   OCI_VOLUME_NAME         — defaults to ict-bot-data-vol.
#   OCI_VOLUME_SIZE_GB      — defaults to 100.
#   OCI_AVAILABILITY_DOMAIN — defaults to Eu-Paris-1-AD-3.
#
# Outputs (when run inside GitHub Actions):
#   $GITHUB_OUTPUT receives volume_ocid=<ocid>.
set -euo pipefail

VOLUME_NAME="${OCI_VOLUME_NAME:-ict-bot-data-vol}"
SIZE_GB="${OCI_VOLUME_SIZE_GB:-100}"
AD="${OCI_AVAILABILITY_DOMAIN:-Eu-Paris-1-AD-3}"
REGION="${OCI_CLI_REGION:-eu-paris-1}"
COMPARTMENT_OCID="${OCI_COMPARTMENT_OCID:-${OCI_CLI_TENANCY:-}}"
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 64 ;;
    esac
done

if [[ -z "$COMPARTMENT_OCID" ]]; then
    echo "ERROR: OCI_COMPARTMENT_OCID (or OCI_CLI_TENANCY) must be set." >&2
    exit 2
fi

emit_ocid() {
    local id="$1"
    printf 'VOLUME_OCID=%s\n' "$id"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        printf 'volume_ocid=%s\n' "$id" >> "$GITHUB_OUTPUT"
    fi
}

echo "Checking for existing volume '$VOLUME_NAME' in $REGION..."
existing_id=$(oci bv volume list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --lifecycle-state AVAILABLE \
    --query "data[?\"display-name\"=='${VOLUME_NAME}'].id | [0]" \
    --raw-output 2>/dev/null || true)

if [[ -n "${existing_id:-}" && "$existing_id" != "null" ]]; then
    echo "Volume already exists; reusing."
    emit_ocid "$existing_id"
    exit 0
fi

create_cmd=(oci bv volume create
    --compartment-id "$COMPARTMENT_OCID"
    --availability-domain "$AD"
    --size-in-gbs "$SIZE_GB"
    --display-name "$VOLUME_NAME"
    --region "$REGION"
    --wait-for-state AVAILABLE)

if $DRY_RUN; then
    printf 'DRY-RUN: %s\n' "${create_cmd[*]}"
    exit 0
fi

echo "Creating volume '$VOLUME_NAME' (${SIZE_GB} GB) in $AD..."
output=$("${create_cmd[@]}")
new_id=$(printf '%s' "$output" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')
echo "Created volume."
emit_ocid "$new_id"
