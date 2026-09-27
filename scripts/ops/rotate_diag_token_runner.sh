#!/usr/bin/env bash
# init-diag-token, RUNNER half: rotate DIAG_READ_TOKEN end to end without the
# value ever reaching a log, an issue comment, argv, or an artifact.
#
# Runs on the GitHub-hosted runner inside system-actions.yml's exec step. Its
# stdout+stderr become action-output.txt, which is posted VERBATIM to the
# request issue on a PUBLIC repo — and `::add-mask::` does NOT apply to that
# file, only to the run log. So this script prints status words only, never
# the token and never a fingerprint of it.
#
# Inputs (env):
#   NEW_TOKEN_FILE        600 file holding the new 64-hex token. The workflow
#                         generates it (openssl rand -hex 32) and ::add-mask::s
#                         it in the step's own log BEFORE calling this script.
#   OLD_DIAG_READ_TOKEN   the CURRENT secrets.DIAG_READ_TOKEN (secrets are
#                         resolved at job start, so this is the pre-rotation value).
#   GH_TOKEN              a PAT that can write Actions secrets
#                         (secrets.BRANCH_PROTECTION_TOKEN — see init-actions-secrets.yml).
#   GITHUB_REPOSITORY     owner/repo.
#   VM_SSH_USER, VM_SSH_HOST, SSH_KEY (default ~/.ssh/id_rsa).
#   REMOTE_SCRIPT         default /home/ubuntu/ict-trading-bot/scripts/ops/init_diag_token.sh
#
# Order, and why it is safe at every exit:
#   0. preflight: token shapes, new != old, PAT can reach the secrets API.
#      Failure here touches NOTHING.                    → not_rotated
#   1. VM rotate (token on ssh STDIN).  failure → rollback
#   2. verify on the VM, token on curl STDIN (`-H @-`):
#        new → 200 (retried while the unit warms up), old → 401. failure → rollback
#   3. `gh secret set DIAG_READ_TOKEN` (value on stdin). failure → rollback
#   4. VM commit (delete backups that hold the retired token).
#   rollback = VM restores its backups, then OLD → 200 is re-verified, so the
#   state left behind is "old token valid on VM, secret unchanged".
#
# Exactly one FINAL_STATE line is printed:
#   rotated                       new token on VM and in the secret; old → 401
#   not_rotated                   preflight refused; nothing touched
#   rolled_back                   old token restored on VM and re-verified 200;
#                                 secret unchanged
#   INCONSISTENT                  rollback failed or could not be verified —
#                                 diag relays may be broken; see the fields.
# Exit 0 only for `rotated`.

set -uo pipefail

REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY unset}"
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/id_rsa}"
REMOTE_SCRIPT="${REMOTE_SCRIPT:-/home/ubuntu/ict-trading-bot/scripts/ops/init_diag_token.sh}"
DIAG_URL="${DIAG_URL:-http://127.0.0.1:8001/api/diag/status}"
UNIT="ict-web-api.service"
VERIFY_ATTEMPTS="${VERIFY_ATTEMPTS:-12}"
VERIFY_SLEEP_S="${VERIFY_SLEEP_S:-5}"

rotated=no
new_token_200=no
old_token_401=no
secret_updated=no
unit_post_state=unknown

say() { printf '%s\n' "$*"; }

vm() {
    # vm <remote command> — stdin passes through. Remote stdout/stderr are
    # the VM script's status lines (token-free by construction and by test).
    ssh -i "${SSH_KEY}" \
        -o BatchMode=yes \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=15 \
        "${VM_SSH_USER}@${VM_SSH_HOST}" "$1"
}

# http_code_with <token> — the bearer rides curl's STDIN as a
# header (`-H @-`), so it is on no command line on the VM.
http_code_with() {
    local token="$1"
    printf 'Authorization: Bearer %s\n' "${token}" \
      | vm "curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -H @- '${DIAG_URL}' 2>/dev/null || echo 000" \
      2>/dev/null | tr -dc '0-9' | tail -c 3
}

emit_summary() {
    local final="$1"
    unit_post_state="$(vm "systemctl is-active ${UNIT} 2>/dev/null || true" 2>/dev/null | tr -dc 'a-z-' )"
    [ -n "${unit_post_state}" ] || unit_post_state=unknown
    say ""
    say "rotated=${rotated}"
    say "new_token_200=${new_token_200}"
    say "old_token_401=${old_token_401}"
    say "secret_updated=${secret_updated}"
    say "unit_post_state=${unit_post_state}"
    say "FINAL_STATE=${final}"
    say "DIAG_ROTATION_RESULT={\"rotated\": \"${rotated}\", \"new_token_200\": \"${new_token_200}\", \"old_token_401\": \"${old_token_401}\", \"secret_updated\": \"${secret_updated}\", \"unit_post_state\": \"${unit_post_state}\", \"final_state\": \"${final}\"}"
}

rollback_and_exit() {
    local why="$1"
    say ">>> ${why} — rolling the VM back to the previous token."
    if vm "bash ${REMOTE_SCRIPT} rollback" </dev/null; then
        local code
        code="$(http_code_with "${OLD_DIAG_READ_TOKEN}")"
        say "post_rollback_old_token_http=${code:-000}"
        rotated=no
        if [ "${code}" = "200" ]; then
            say ">>> Rolled back: the previous token is valid on the VM again; the Actions secret was NOT changed. Diag relays keep working. Nothing rotated."
            emit_summary rolled_back
        else
            say ">>> Rollback ran but the previous token does NOT authorize (http ${code:-000}). Diag relays using the Actions secret may be broken."
            emit_summary INCONSISTENT
        fi
    else
        say ">>> ROLLBACK FAILED on the VM. State unknown: the VM may serve the NEW token while the Actions secret still holds the OLD one. Re-dispatch init-diag-token (it restarts from whatever is installed) or run set-diag-token after fixing the cause."
        emit_summary INCONSISTENT
    fi
    exit 1
}

# ── 0. preflight — touches nothing ──────────────────────────────────────────
if [ -z "${NEW_TOKEN_FILE:-}" ] || [ ! -r "${NEW_TOKEN_FILE}" ]; then
    say ">>> preflight: NEW_TOKEN_FILE missing."; emit_summary not_rotated; exit 1
fi
NEW_TOKEN="$(tr -d '[:space:]' < "${NEW_TOKEN_FILE}")"
if ! [[ "${NEW_TOKEN}" =~ ^[0-9a-f]{64}$ ]]; then
    say ">>> preflight: new token is not 64 hex chars."; emit_summary not_rotated; exit 1
fi
if [ -z "${OLD_DIAG_READ_TOKEN:-}" ]; then
    say ">>> preflight: secrets.DIAG_READ_TOKEN is empty — cannot prove the old token dies (old→401). Refusing."
    emit_summary not_rotated; exit 1
fi
if [ "${NEW_TOKEN}" = "${OLD_DIAG_READ_TOKEN}" ]; then
    say ">>> preflight: new token equals the current secret — that is not a rotation. Refusing."
    emit_summary not_rotated; exit 1
fi
if [ -z "${GH_TOKEN:-}" ]; then
    say ">>> preflight: GH_TOKEN (secrets.BRANCH_PROTECTION_TOKEN) unset — could not update the Actions secret afterwards, so the VM is not touched."
    emit_summary not_rotated; exit 1
fi
if ! gh api "repos/${REPO}/actions/secrets/public-key" --silent >/dev/null 2>&1; then
    say ">>> preflight: GH_TOKEN cannot read repos/${REPO}/actions/secrets/public-key (needs Actions-secrets write). The VM is not touched."
    emit_summary not_rotated; exit 1
fi
# The VM's copy of the script must speak this protocol. ict-git-sync pulls main
# every ~5 min, so right after merge the VM can still hold the OLD script —
# which ignores stdin, reuses the leaked token and PRINTS it into the output
# this runner posts publicly. Never call it without the marker.
if ! vm "grep -qx '# ROTATION-PROTOCOL: stdin-v1' ${REMOTE_SCRIPT}" </dev/null >/dev/null 2>&1; then
    say ">>> preflight: the VM's ${REMOTE_SCRIPT} does not carry '# ROTATION-PROTOCOL: stdin-v1' (not yet synced from main, or unreachable). Refusing — the old script prints the token. Wait for ict-git-sync (~5 min) or run pull-and-deploy, then re-dispatch."
    emit_summary not_rotated; exit 1
fi
secret_before="$(gh api "repos/${REPO}/actions/secrets/DIAG_READ_TOKEN" --jq .updated_at 2>/dev/null || echo unknown)"
say "secret_updated_at_before=${secret_before}"
pre_old="$(http_code_with "${OLD_DIAG_READ_TOKEN}")"
say "pre_old_token_http=${pre_old:-000}"
if [ "${pre_old}" != "200" ]; then
    # Not fatal: a rotation is exactly how a drifted VM/secret pair is fixed.
    # But the rollback target ("old token valid") then does not exist, so say so.
    say ">>> note: the CURRENT secret does not authorize on the VM (http ${pre_old:-000}) — the VM and the secret had already drifted. A rollback would restore the VM's previous files, not a working secret."
fi

# ── 1. VM rotate — token on ssh STDIN ───────────────────────────────────────
say ">>> VM: rotate"
vm "bash ${REMOTE_SCRIPT} rotate" < "${NEW_TOKEN_FILE}"
rc=$?
if [ "${rc}" -eq 2 ]; then
    # The VM script refuses (bad input / no-op) BEFORE taking backups or
    # writing anything, so there is nothing to roll back.
    say ">>> VM refused the rotation before writing anything (exit 2)."
    emit_summary not_rotated; exit 1
elif [ "${rc}" -ne 0 ]; then
    rollback_and_exit "VM rotate failed (exit ${rc})"
fi
rotated=yes

# ── 2. verify: new → 200, old → 401 ─────────────────────────────────────────
code=000
for _ in $(seq 1 "${VERIFY_ATTEMPTS}"); do
    code="$(http_code_with "${NEW_TOKEN}")"
    [ "${code}" = "200" ] && break
    sleep "${VERIFY_SLEEP_S}"
done
say "new_token_http=${code:-000}"
[ "${code}" = "200" ] && new_token_200=yes
old_code="$(http_code_with "${OLD_DIAG_READ_TOKEN}")"
say "old_token_http=${old_code:-000}"
[ "${old_code}" = "401" ] && old_token_401=yes
if [ "${new_token_200}" != yes ] || [ "${old_token_401}" != yes ]; then
    rollback_and_exit "verification failed (new=${code:-000}, old=${old_code:-000}; need 200 / 401)"
fi

# ── 3. Actions secret — value on stdin ──────────────────────────────────────
say ">>> Actions secret: set DIAG_READ_TOKEN"
if ! gh secret set DIAG_READ_TOKEN --repo "${REPO}" < "${NEW_TOKEN_FILE}" >/dev/null 2>&1; then
    rollback_and_exit "gh secret set failed"
fi
secret_after="$(gh api "repos/${REPO}/actions/secrets/DIAG_READ_TOKEN" --jq .updated_at 2>/dev/null || echo unknown)"
say "secret_updated_at_after=${secret_after}"
secret_updated=yes
if [ "${secret_after}" = "${secret_before}" ] || [ "${secret_after}" = unknown ]; then
    say ">>> note: gh secret set returned 0 but updated_at did not visibly change (${secret_before} → ${secret_after})."
fi

# ── 4. commit — drop backups holding the retired token ──────────────────────
if ! vm "bash ${REMOTE_SCRIPT} commit" </dev/null; then
    say ">>> note: VM commit failed — *.diag-rotate.bak files (holding the RETIRED token) remain next to the env files. Harmless to operation; re-run commit or delete them."
fi

emit_summary rotated
exit 0
