# Permissions tiers — who can do what

**Audience:** PM, operator, anyone proposing a new automation that needs to touch OCI, GitHub, the VM, or the running trader.
**Status:** Adopted 2026-05-11 as part of the OCI block-storage externalization.

This doc separates "permissions" into five tiers. The split exists because the same word — *permission* — covers very different things: a GitHub repo write bit, an OCI IAM policy, an SSH key, a systemd unit file. Lumping them together hides the fact that some are easy to revoke (a GitHub deploy key) while others are nearly impossible to undo cleanly (a tenancy-admin grant).

## TL;DR

| Tier | Role | Can do | Cannot do |
|---|---|---|---|
| **0 — Owner / Admin** | The human operator | Everything below + tenancy IAM, billing, root key rotation | (Nothing — but should rarely act in this tier) |
| **1 — Infrastructure operator** | Operator wearing their "ops" hat | Create + attach block volumes, modify instance metadata, manage networks | Tenancy IAM, billing |
| **2 — CI/CD automation** | GitHub Actions workflows | A narrow, fixed allowlist of OCI API calls (look-ups, status reads) | Destructive control-plane actions, secret reads from outside the workflow |
| **3 — VM operator** | Claude over SSH, or the operator shelled in | Mount, unmount, copy data, restart services, install drop-ins | Edit live `.service` files, change risk caps, rotate keys |
| **4 — Application runtime** | The trader Python process | Read/write under `/data/bot-data/`, read configs, append logs | Anything OCI control-plane, any path outside its `WorkingDirectory` |

## Tier 0 — Owner / Admin

**Identity:** the tenancy root user; ultimately the human operator.

**Authority:** unbounded inside the OCI tenancy and on the GitHub org.

**When this tier is used:**
- Bootstrapping a new VM or block volume policy.
- Rotating an API signing key.
- Setting up IAM groups, dynamic groups, and policies.
- Changing billing limits.

**What this tier must NOT be used for routinely:**
- Day-to-day deploys.
- Block-volume create/attach (delegate to Tier 1 with a scoped policy).
- Any action a workflow could perform with a narrower grant.

> **Rule of thumb:** if you find yourself logged in as the tenancy admin to do something that's automatable, stop and write the automation instead. The blast radius is too large.

## Tier 1 — Infrastructure operator

**Identity:** a named OCI IAM user (or a group containing them) with policies scoped to compute + storage.

**Authority:**
- Create / attach / detach / delete block volumes in the project compartment.
- Create / restart / terminate compute instances.
- Take volume snapshots.

**What this tier still cannot do:**
- Manage IAM users, groups, or policies (those are Tier 0).
- Touch billing or tenancy quotas.

**Why it's separate:** block-volume control-plane operations have very different blast radii from application-level reads/writes. Detaching the wrong volume from the wrong instance can wipe live trading state. We want the smaller set of humans / automations who can do this to be explicit.

**Recommendation:** keep volume create/attach as a **human-approved action**. The trader is too small to benefit from automating volume provisioning, and the failure modes (volume attached to the wrong VM, mount point overlap) are subtle.

## Tier 2 — CI/CD automation

**Identity:** GitHub Actions workflows running with `oci` SDK credentials provided via repository secrets.

**Authority:** a fixed, named allowlist of operations. Today this means:
- Looking up the live VM's subnet OCID via the OCI API (`vm-diag-snapshot.yml`).
- Reading workflow-scoped GitHub secrets (`OCI_CLI_USER`, `OCI_CLI_TENANCY`, `OCI_CLI_REGION`, `OCI_CLI_FINGERPRINT`, `OCI_CLI_KEY_CONTENT`).

**What this tier must NOT have:**
- Volume create / attach / detach.
- Instance terminate.
- IAM mutation.
- Any policy that grants `manage` on `volume-family` or `instance-family`.

**Why it's separate:** workflow runners are stateless and short-lived but have the credentials of a tenancy principal. A compromised workflow (malicious PR, supply-chain dependency injection) inherits whatever IAM policies were granted to that principal. The narrower the grant, the smaller the blow-up.

**Recommendation:** consume Tier 2 secrets only from a **protected environment** in repository settings, with deployment-branch and reviewer rules attached. Don't paste the values into logs (`set-mask`, `::add-mask::`). Don't expose them to forked-PR workflows.

> The five OCI secrets the human operator set up (`OCI_CLI_USER`, `OCI_CLI_TENANCY`, `OCI_CLI_REGION`, `OCI_CLI_FINGERPRINT`, `OCI_CLI_KEY_CONTENT`) live in this tier. See [`docs/operator/github-actions-oci-secrets.md`](../operator/github-actions-oci-secrets.md) for the full contract.

## Tier 3 — VM operator

**Identity:** anyone who can SSH to `141.145.193.91` as `ubuntu` (`ict-bot-arm`, the Ampere live trader since the 2026-06-14 cutover; was the x86 micro `158.178.210.252`, terminated 2026-06-16). In practice: the human operator and the issue-driven `system-actions.yml` workflow (and Claude, indirectly, through that workflow).

**Authority on the live VM:**
- `mount` / `umount /data/bot-data`.
- Run `scripts/check_data_dir.sh`, `scripts/migrate_to_data_dir.sh`.
- Install or remove `deploy/dropins/data-dir.conf` under `/etc/systemd/system/*.service.d/`.
- `systemctl daemon-reload`, `systemctl restart <unit>`.
- Read and edit `/home/ubuntu/ict-trading-bot/.env`.

**What this tier still cannot do (hard limits per CLAUDE.md):**
- Modify `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml`.
- Modify `src/runtime/orders.py`, `src/runtime/risk_counters.py`.
- Modify any `.service` file the live VM consumes. (Drop-ins are OK; the base unit files stay untouched.)
- Rotate the trader's exchange API keys without operator approval.

**Why it's separate:** the things this tier *can* do (mount, restart) are recoverable: if it goes wrong, you `umount` and `systemctl restart` and you're back. The things it *can't* do (touch risk caps, edit unit files) have ongoing consequences that survive a reboot.

**Recommendation:** keep installation of the data-dir drop-in as a **manual operator step**. Don't try to automate it via `system-actions.yml`. The action is rare (once per VM) and the consequences of getting it wrong (trader silently writes to the wrong filesystem) outweigh the convenience.

## Tier 4 — Application runtime

**Identity:** the `ubuntu` user as far as the OS is concerned, but really just the trader Python process started by systemd (`ict-trader-live.service`, `ict-web-api.service`, `ict-claude-bridge.service`).

**Authority:**
- POSIX read / write on `/data/bot-data/` (or whatever `DATA_DIR` points at).
- Read `/home/ubuntu/ict-trading-bot/.env`.
- Read `/home/ubuntu/ict-trading-bot/config/`.
- Outbound HTTPS to Bybit, Binance, Telegram, Anthropic.

**What this tier must NOT have:**
- Any OCI control-plane credentials. The trader process doesn't make OCI API calls; it just writes files to a path the OS already mounted for it.
- Sudo, ever. There is no scenario where the trader benefits from elevated privileges.

**Why it's separate:** this is the runtime under attacker assumption. If the trader process is compromised (a malicious exchange response, a poisoned dependency, anything), the damage is bounded by what this tier can reach. Keeping it at "files in `/data/bot-data/` + outbound HTTPS" means a compromised trader can't, e.g., detach the volume it lives on.

## Decision rubric for new automations

When proposing a new script, workflow, or feature that touches infrastructure:

1. **What tier does it need to run in?** Pick the lowest tier that can do the job.
2. **What's the blast radius if it misfires?** If it spans tiers, split it.
3. **Is the action reversible?** Volume detach is reversible; volume *delete* is not.
4. **Does it need a human in the loop?** Anything in Tier 0 or Tier 1 should default to "yes."

## Cross-references

- [`docs/architecture/oci-block-storage.md`](../architecture/oci-block-storage.md) — what's on the volume.
- [`docs/runbooks/mounted-storage.md`](../runbooks/mounted-storage.md) — Tier-3 ops procedure.
- [`docs/operator/github-actions-oci-secrets.md`](../operator/github-actions-oci-secrets.md) — Tier-2 secret contract.
- [`docs/claude/vm-operator-mode.md`](../claude/vm-operator-mode.md) — the trust contract Claude operates under on the live VM (subset of Tier 3).
