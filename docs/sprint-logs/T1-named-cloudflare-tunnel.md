# Sprint Log: T1 — Named Cloudflare Tunnel

## Date Range
- Start: 2026-05-12
- End: (in-flight)

## Objective
- **Primary:** Replace the ephemeral `*.trycloudflare.com` quick tunnel with a named tunnel that has a STABLE hostname. Pin `vercel.json` once and never touch it again.
- **Secondary:** Wrap `cloudflared` in a proper systemd unit (`Restart=always`) so a crash never silently kills the dashboard transport.

## Tier
- **Tier 2** — touches operator-action allowlist + ships a new systemd unit the live VM will consume.
- Justification: per `docs/CLAUDE-RULES-CANONICAL.md`, new operator actions and new unit files are Tier-2. The script does not touch live order code, `config/accounts.yaml`, risk caps, or strategy YAML.

## Starting Context
- **Roadmap item:** T1 from `docs/audit/2026-05-12-end-to-end-audit.md` § 6.
- **Prior sprints (cloudflare lineage):**
  - `S-CFW-1` — CF Worker as direct VM proxy. **Retired** in `S-CFW-1-FU2` (CF error 1003 — Direct IP Access Not Allowed).
  - `S-CFW-1-FU-gha-deploy` — CF Worker deploy via GitHub Actions. Infra reused here (same `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` secrets).
- **Triggering outage:** 2026-05-12 — 4th `vercel.json` URL-rotation PR in 2 days (ict-trader-dashboard#22 → #23 → #25 → #29 → #30). Each one repointed `/api/bot/*` at a fresh `*.trycloudflare.com` host after the prior one died with cloudflared.

## Repo State Checked
- Branch: `claude/fix-trade-pipeline-MG5qb`
- Deployment state: post-#30 — dashboard live on a quick tunnel that will rotate again on the next cloudflared crash.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md` § Tier policy, `docs/audit/2026-05-12-end-to-end-audit.md`.

## Files and Systems Inspected
- `scripts/ops/setup_cloudflare_tunnel.sh` — the quick-tunnel pattern this replaces.
- `scripts/ops/teardown_cloudflare_tunnel.sh` — symmetric companion.
- `scripts/ops/_lib.sh` — `log()` + `record_audit()` helpers used here too.
- `cf-worker/wrangler.toml`, `cf-worker/src/index.js` — kept for reference; not used here.
- `.github/workflows/operator-actions.yml` — extended with two new allowlisted actions.
- `.github/workflows/cf-worker-deploy.yml` — secret-naming source of truth (we reuse `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`).
- `deploy/ict-web-api.service` — template for the new unit file (User=ubuntu, journal logging, Restart=always).

## Work Completed

### Infrastructure
- **`deploy/ict-cloudflared-tunnel.service`** — new systemd unit. `Restart=always`, `RestartSec=10`, journal logging, hardening (NoNewPrivileges, ProtectSystem=strict, ReadOnlyPaths=/etc/ict-trader/cloudflared). Reads config from `/etc/ict-trader/cloudflared/config.yml`.
- **`scripts/ops/setup_named_cloudflare_tunnel.sh`** — installs cloudflared, creates/fetches the named tunnel via CF API, writes credentials + ingress config to `/etc/ict-trader/cloudflared/`, optional CNAME routing, installs + starts the unit, end-to-end probe.
- **`scripts/ops/teardown_named_cloudflare_tunnel.sh`** — symmetric. Stops + disables the unit, deletes the tunnel via CF API, removes on-disk state.

### Wiring
- **`.github/workflows/operator-actions.yml`** — adds `setup-named-cloudflare-tunnel` + `teardown-named-cloudflare-tunnel` to the dispatch choice list and the Tier-2 case arms. Adds an optional `tunnel_hostname` input. Threads `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` (and `TUNNEL_HOSTNAME` when provided) into the SSH command for these actions. Issue-body parser accepts a new `hostname:` line.

### Docs
- **`docs/runbooks/cloudflare-named-tunnel.md`** — operator runbook (setup, retire, troubleshoot).
- **This sprint log.**

## Validation Performed
- **Static checks only in this session:**
  - Shell scripts pass `bash -n` (will be verified by ruff-lint workflow's shellcheck step on PR).
  - YAML parses (will be verified by `arch-doc-guard` + workflow CI on PR).
  - Operator-actions allowlist + Tier-2 case arms updated in lockstep — verified by inspection.
- **Live verification deferred to operator approval:**
  - Operator fires `setup-named-cloudflare-tunnel` from the workflow_dispatch UI (with `reason:` filled in, optional `tunnel_hostname:`).
  - Workflow run page + the issue comment-back contain the stable URL.
  - Follow-up PR repoints `ict-trader-dashboard/vercel.json` to the stable URL.
  - After 24 h of healthy traffic, retire the quick tunnel via `teardown-cloudflare-tunnel`.

## Documentation Updated
- **Audit doc** — `docs/audit/2026-05-12-end-to-end-audit.md` § 6 T1 marked **in progress (PR #N)** (updated in same commit).
- **New runbook** — `docs/runbooks/cloudflare-named-tunnel.md`.
- **CLAUDE.md** — no changes; the operator-actions allowlist is now self-describing through workflow_dispatch UI.

## Known follow-ups (queued, NOT in this PR)
- **T2 — transport watchdog:** extend `ict-liveness-watchdog` to probe the stable URL end-to-end every minute. Telegram on stale.
- **T5 — secondary-unit watchdogs:** generalize T2 pattern to cover `ict-heartbeat`, `ict-git-sync`, `ict-hourly-snapshot`.
- **Deprecate quick-tunnel scripts:** after T1 is verified live for one full week, retire `setup_cloudflare_tunnel.sh` + `teardown_cloudflare_tunnel.sh` and their allowlist entries. Keep the named-tunnel scripts.

## Tier-3 paths NOT touched
- `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml` — unchanged.
- `src/runtime/orders.py`, `src/runtime/risk_counters.py` — unchanged.
- Live trader unit (`ict-trader-live.service`) — unchanged.

## Why this kills the recurring patch class

Five `vercel.json` PRs in two days (ict-trader-dashboard#22 → #23 → #25 → #29 → #30) all share one root cause: the upstream hostname rotates. The named tunnel pins the hostname for as long as the tunnel exists on the CF account. The only remaining trigger for a `vercel.json` PR becomes intentional transport migration (e.g., to a custom domain or Tailscale Funnel) — not silent infrastructure drift.
