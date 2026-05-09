#!/usr/bin/env bash
# Idempotent one-shot bootstrap for the PM-side diag relay.
#
# What this does:
#   - creates the `vm-diag-request` repo label that the
#     `vm-diag-snapshot` workflow filters on, if absent
#   - sanity-checks that the two required repo secrets exist
#     (VM_SSH_KEY, DIAG_READ_TOKEN); their *contents* aren't
#     readable by API, so we can only assert presence.
#
# What this does NOT do:
#   - set the secrets — that's a manual operator step under
#     Settings → Secrets and variables → Actions
#   - touch the VM
#
# Usage:
#   GH_TOKEN=ghp_… bash scripts/bootstrap_diag_relay.sh
#
# Or, in an environment that already has `gh auth status` clean:
#   bash scripts/bootstrap_diag_relay.sh
set -euo pipefail

REPO="${REPO:-benbaichmankass/ict-trading-bot}"
LABEL="vm-diag-request"
LABEL_COLOR="0e8a16"           # green — matches "open / read-only"
LABEL_DESC="Issues that drive the PM-side VM diag relay (auto-created + auto-closed by the vm-diag-snapshot workflow)."

if ! command -v gh >/dev/null 2>&1; then
  cat >&2 <<EOF
ERROR: gh (GitHub CLI) not installed in this environment.

This script is intended for the operator's laptop or Colab. The
Claude session itself doesn't need to run it — it can create the
label via mcp__github__create_label directly.

Install: https://cli.github.com/
EOF
  exit 1
fi

echo "→ checking label '${LABEL}' on ${REPO}"
if gh label list --repo "${REPO}" --search "${LABEL}" --json name --jq '.[].name' \
     | grep -Fxq "${LABEL}"; then
  echo "  label exists — leaving as-is"
else
  echo "  label absent — creating"
  gh label create "${LABEL}" \
    --repo "${REPO}" \
    --color "${LABEL_COLOR}" \
    --description "${LABEL_DESC}"
fi

echo
echo "→ checking required repo secrets on ${REPO}"
secrets=$(gh secret list --repo "${REPO}" --json name --jq '.[].name')
missing=()
for required in VM_SSH_KEY DIAG_READ_TOKEN; do
  if printf '%s\n' "${secrets}" | grep -Fxq "${required}"; then
    echo "  ${required}: present"
  else
    echo "  ${required}: MISSING"
    missing+=("${required}")
  fi
done

if [ "${#missing[@]}" -gt 0 ]; then
  cat >&2 <<EOF

The following repo secrets are missing:
  ${missing[*]}

Add them under:
  https://github.com/${REPO}/settings/secrets/actions

  VM_SSH_KEY:       full contents of ict-bot-ovm-private.key
                    (already in your Drive, see the Keys spreadsheet)
  DIAG_READ_TOKEN:  bearer from /etc/ict-trader/web-api.env on the VM

Re-run this script to verify.
EOF
  exit 2
fi

echo
echo "✅ diag relay bootstrap complete."
echo "   Test from a Claude session by opening an issue titled"
echo "   '[diag-request] snapshot?limit=20' with label '${LABEL}'."
