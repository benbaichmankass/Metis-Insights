#!/usr/bin/env bash
# scripts/oci_attach_volume.sh — attach the data volume to the live VM.
#
# Idempotent: looks up instance + volume by display-name; if a current
# attachment already exists in ATTACHED state, returns 0 without retrying.
#
# Usage:
#   ./scripts/oci_attach_volume.sh
#   ./scripts/oci_attach_volume.sh --dry-run
#
# Env:
#   OCI_COMPARTMENT_OCID  — defaults to OCI_CLI_TENANCY.
#   OCI_CLI_REGION        — defaults to eu-paris-1.
#   OCI_VOLUME_NAME       — defaults to ict-bot-data-vol.
#   VM_INSTANCE_NAME      — defaults to instance-20260414-1555.
#   OCI_ATTACH_TYPE       — defaults to paravirtualized.
set -euo pipefail

VM_NAME="${VM_INSTANCE_NAME:-instance-20260414-1555}"
VOLUME_NAME="${OCI_VOLUME_NAME:-ict-bot-data-vol}"
REGION="${OCI_CLI_REGION:-eu-paris-1}"
COMPARTMENT_OCID="${OCI_COMPARTMENT_OCID:-${OCI_CLI_TENANCY:-}}"
ATTACH_TYPE="${OCI_ATTACH_TYPE:-paravirtualized}"
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 64 ;;
    esac
done

if [[ -z "$COMPARTMENT_OCID" ]]; then
    echo "ERROR: OCI_COMPARTMENT_OCID (or OCI_CLI_TENANCY) must be set." >&2
    exit 2
fi

echo "Resolving instance OCID for '$VM_NAME'..."
instance_id=$(oci compute instance list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --query "data[?\"display-name\"=='${VM_NAME}' && \"lifecycle-state\"=='RUNNING'].id | [0]" \
    --raw-output)
if [[ -z "$instance_id" || "$instance_id" == "null" ]]; then
    echo "ERROR: no RUNNING instance named '$VM_NAME'." >&2
    exit 3
fi
echo "  instance: $instance_id"

echo "Resolving volume OCID for '$VOLUME_NAME'..."
volume_id=$(oci bv volume list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --lifecycle-state AVAILABLE \
    --query "data[?\"display-name\"=='${VOLUME_NAME}'].id | [0]" \
    --raw-output)
if [[ -z "$volume_id" || "$volume_id" == "null" ]]; then
    echo "ERROR: no AVAILABLE volume named '$VOLUME_NAME'. Run oci_create_volume.sh first." >&2
    exit 3
fi
echo "  volume:   $volume_id"

echo "Checking existing attachments..."
attached=$(oci compute volume-attachment list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --instance-id "$instance_id" \
    --volume-id "$volume_id" \
    --query "data[?\"lifecycle-state\"=='ATTACHED'].id | [0]" \
    --raw-output 2>/dev/null || true)
if [[ -n "${attached:-}" && "$attached" != "null" ]]; then
    echo "Volume already attached ($attached); nothing to do."
    exit 0
fi

attach_cmd=(oci compute volume-attachment attach
    --instance-id "$instance_id"
    --volume-id "$volume_id"
    --type "$ATTACH_TYPE"
    --region "$REGION"
    --wait-for-state ATTACHED)

if $DRY_RUN; then
    printf 'DRY-RUN: %s\n' "${attach_cmd[*]}"
    exit 0
fi

echo "Attaching volume (type=$ATTACH_TYPE)..."
"${attach_cmd[@]}"
echo "Attached."
