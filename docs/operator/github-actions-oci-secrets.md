# GitHub Actions OCI secrets contract

**Audience:** PM, operator, anyone reviewing a workflow that touches OCI.
**Status:** Adopted 2026-05-11. Five secrets are configured at the repository level. No environment-scoped variant exists yet.

## What these secrets are for

The trader needs the **operator** to do tenancy-level setup once (create the block volume, attach it to the instance, mount it). Day-to-day, the workflows in `.github/workflows/` need to make narrow OCI lookups — currently just "what subnet is the live VM on?" for `vm-diag-snapshot.yml`. Those lookups require the same five pieces of identifying information any OCI API call needs:

| Secret name | What it is | Why it's needed |
|---|---|---|
| `OCI_CLI_USER` | OCID of an IAM user | Identifies the principal making the API call. |
| `OCI_CLI_TENANCY` | OCID of the tenancy | Scopes the call to our tenancy. |
| `OCI_CLI_REGION` | OCI region identifier (e.g. `us-ashburn-1`) | Routes the API call to the right OCI region endpoint. |
| `OCI_CLI_FINGERPRINT` | Fingerprint of the API signing key pair | Lets OCI verify the request signature against the right public key on file. |
| `OCI_CLI_KEY_CONTENT` | Private key (PEM, multi-line) | Signs the request. Never leaves the workflow runner. |

The five values together are the OCI API equivalent of a username/password pair plus a region code. Any one is useless without the rest, but the combination grants whatever IAM policies are attached to the user.

## Where they live

- **Storage:** GitHub repository secrets, scoped to `benbaichmankass/ict-trading-bot`. They are encrypted at rest and only decrypted into the runner's memory for the workflow steps that reference them.
- **Visibility:** they do not appear in workflow logs because GitHub auto-masks any string matching a registered secret. A workflow that tried to `echo` one of these would print `***` in the log.
- **Reachability:** workflows in this repo can reference them as `${{ secrets.OCI_CLI_USER }}` etc. Forked-PR workflows cannot — that's a GitHub-side guarantee, not something we configured.

## Which workflows consume them

| Workflow file | What it does with the secrets |
|---|---|
| `.github/workflows/vm-diag-snapshot.yml` | Looks up the live VM's subnet OCID via `oci network subnet get`. Output is the subnet OCID, posted back to the issue that triggered the workflow. The five secrets are exported into the step's environment via `env:` block, then the `oci` CLI reads them itself. |

If a future workflow needs broader OCI access (volume create, instance restart), the right answer is **not** to give these five secrets more authority. The right answer is to create a separate, narrower IAM user with its own key pair, and store those credentials under a different set of secret names. See "Recommended hardening" below.

## What the secrets are NOT used for

- The trader process on the VM. The trader makes zero OCI API calls; it just writes files to a path the OS mounted for it. (See "Tier 4" in [`docs/security/permissions-tiers.md`](../security/permissions-tiers.md).)
- The dashboard. The Vercel React app talks to `/api/bot/*` on the VM; it does not authenticate to OCI.
- Claude in the web sandbox. The sandbox session has GitHub MCP tools, not OCI ones, and cannot egress to the OCI API.

## Expected masking and logging behavior

- Any step that uses `${{ secrets.OCI_CLI_KEY_CONTENT }}` must avoid emitting it to stdout. GitHub will mask the literal value, but the safest pattern is `--key-content-file <(echo "$KEY")` with the secret in a step-local env var rather than passed as a CLI argument.
- `oci` CLI is generally well-behaved here: it reads the key from a temporary file and doesn't echo it.
- Workflows must not write any of these values to artifacts, comments, or commit messages. If a workflow needs to *report* a fingerprint or user OCID, that's fine — those two are mid-sensitivity identifiers. The private key contents are never reportable.

## Recommended hardening

### 1. Protected environment

Move these five secrets to a GitHub **environment** named `production-oci` rather than the repo root. Environments add:

- **Deployment branch rules** — restrict which branches can launch workflows that use the environment (e.g. `main` only).
- **Required reviewers** — a workflow that needs these secrets pauses until a named reviewer approves the run.
- **Wait timer** — optional delay between approval and execution.

For a tenancy-admin-equivalent credential, the reviewer rule is the most useful: it forces a human eye on any new workflow that asks for these secrets before they're exposed to it.

### 2. Narrower IAM user for CI

The current user behind `OCI_CLI_USER` should be scoped to just the read operations the workflows actually do. Concretely:

```
Allow group <ci-readonly> to inspect virtual-network-family in compartment <project>
Allow group <ci-readonly> to read instance-family in compartment <project>
```

Adding `manage` permissions to this user "to keep it simple" is the path that ends in the wrong workflow detaching the wrong volume. Don't.

### 3. Key rotation cadence

OCI API signing keys don't expire. That's convenient and dangerous in equal measure. Rotate the key pair every 6 months:

1. Generate a new pair (`oci setup keys`).
2. Add the public key to the IAM user.
3. Update `OCI_CLI_FINGERPRINT` and `OCI_CLI_KEY_CONTENT` in repo secrets.
4. Wait for at least one successful workflow run on the new key.
5. Remove the old public key from the IAM user.

A rotation incident is a Tier 0 action (see permissions tiers doc) and should be planned with a known-good rollback (the previous key stays valid until step 5).

## If a secret is exposed

If `OCI_CLI_KEY_CONTENT` ever leaks (printed to logs, pasted into chat, committed):

1. **Immediately** revoke the corresponding public key in the OCI Console (Identity → Users → API Keys).
2. Generate a new pair, update the four `OCI_CLI_*` secrets that change.
3. Audit the IAM user's recent activity (Audit → Events) for unfamiliar calls.
4. Delete the IAM user if the exposure window was long or unbounded; create a new one with the same narrow policies.

The fingerprint, user OCID, tenancy OCID, and region are not sensitive on their own — revealing them just identifies which OCI tenancy you're on. The leak that matters is the key content.

## Cross-references

- [`docs/security/permissions-tiers.md`](../security/permissions-tiers.md) — the five tiers; these secrets are Tier 2.
- [`docs/architecture/oci-block-storage.md`](../architecture/oci-block-storage.md) — why we use OCI at all.
- [`docs/runbooks/mounted-storage.md`](../runbooks/mounted-storage.md) — the operator procedure these secrets don't participate in.
- [`docs/github-actions-workflows.md`](../github-actions-workflows.md) — canonical workflow reference (broader than just OCI).
