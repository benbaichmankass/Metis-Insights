# Tailscale Funnel migration — stable dashboard ↔ bot connection

## Why

The dashboard's `vercel.json` rewrite proxies `/api/bot/*` to the live
trader's FastAPI on `localhost:8001`. The path between Vercel's edge
and that port is the unstable hop:

- Plain-HTTP IP origin (`http://158.178.210.252:8001`) — Vercel
  rewrites + Edge Functions both refuse to proxy to plain-HTTP IP
  destinations (verified 2026-05-10 in
  `docs/audit/vercel-edge-vs-cf-worker.md`).
- Cloudflare Workers — same restriction: outbound `fetch()` to raw
  IPv4 hosts returns CF error 1003 (verified 2026-05-10 in S-CFW-1-FU2).
- Cloudflare quick tunnels (`*.trycloudflare.com`) — works, but the
  hostname is **ephemeral**: every `cloudflared` restart picks a new
  random subdomain, breaking the dashboard until vercel.json is
  repointed.

Tailscale Funnel exposes `localhost:8001` on the VM as a **permanent
public HTTPS URL** of the form `https://ict-trader-live.<tailnet>.ts.net`
that survives Tailscale daemon restarts, VM reboots, and key
rotations. Free for the one-VM-one-port use case.

## Operator setup (one-time, browser-only — no VM SSH required)

1. **Sign up for Tailscale** at https://login.tailscale.com (free).
   Recommended: sign in with the same email you use for ops.
2. **Enable HTTPS for the tailnet** —
   admin console → Settings → DNS → "HTTPS Certificates" → toggle on.
   Funnel requires this.
3. **Generate an auth key** —
   admin console → Settings → Keys → "Generate auth key":
   - Reusable: **no**
   - Ephemeral: **no**
   - Pre-approved: **yes** (only matters if device approval is on)
   - Tags: none
   - Expiration: **90 days** (max)
   Copy the `tskey-auth-...` value — you'll paste it once in step 4.
4. **Add the auth key as a GitHub Actions secret** —
   github.com/benbaichmankass/ict-trading-bot → Settings →
   Secrets and variables → Actions → "New repository secret":
   - Name: `TS_AUTHKEY`
   - Value: `tskey-auth-...` (paste from step 3)

   The operator-actions workflow reads this secret only when
   dispatching `setup-tailscale-funnel`, passes it to the VM as a
   one-shot SSH env var, the wrapper consumes it once for
   `tailscale up`, and immediately unsets it. The key never lands on
   disk on the VM, never logs, never commits.
5. **Approve Funnel for the device** — do this AFTER the first
   successful run of `setup-tailscale-funnel`. The first run adds
   the VM to your tailnet but Funnel is opt-in per-machine. So:
     a. Dispatch `setup-tailscale-funnel` once (steps below).
        It will succeed at `tailscale up` and fail-fast at the
        Funnel step with a clear "Funnel not enabled for device"
        error — that's the cue to do (b).
     b. Admin console → Machines → `ict-trader-live` → "Edit Funnel"
        → enable for this device.
     c. Re-dispatch `setup-tailscale-funnel`. It's idempotent, so
        the second run skips re-installing/re-authenticating and
        just enables the Funnel exposure. The workflow comments
        back with the public HTTPS URL.

## Dispatch the migration

Once prereqs 1-4 are done, the migration is three labelled-issue
dispatches plus a dashboard PR. Every step is GitHub-Action-driven;
no VM SSH from the operator at any point. Each action requires
operator approval before the workflow runs (per the operator-actions
contract — the dispatching agent opens the issue, but only the
operator can merge / approve the workflow run if the repo gating
is enabled).

```text
1.  operator-action: setup-tailscale-funnel  (first run)
        → installs tailscale on the VM, authenticates via the
          TS_AUTHKEY secret passed over SSH, fails-fast at Funnel
          enablement.
        → operator does step 5(b) of "Operator setup" above
          (admin-console click to enable Funnel for the device).

2.  operator-action: setup-tailscale-funnel  (second run)
        → idempotent re-run; this time Funnel succeeds. Workflow
          comments back the public URL of the form
          https://ict-trader-live.<tailnet>.ts.net.

3.  dashboard PR: patch ict-trader-dashboard/vercel.json
        → set the rewrite destination to
          https://<that-url>/api/bot/:path*
        → merge to main; Vercel auto-deploys.

4.  operator-action: teardown-cloudflare-tunnel
        → stops the cloudflared quick-tunnel process and removes
          the @reboot crontab entry. The trycloudflare URL stops
          serving immediately. (Keep this step LAST, so we don't
          have a window where neither path is up.)
```

After step 4, the dashboard talks to the bot over a stable HTTPS URL
that survives reboots and Tailscale daemon restarts. The next time
this breaks, it's a real Tailscale outage (rare, Tailscale's edge has
99.99%+ uptime SLA), not a "URL changed".

## Rollback

If anything breaks during or after the migration, revert by running:

```text
operator-action: setup-cloudflare-tunnel
        → mints a fresh trycloudflare URL.

dashboard PR: revert vercel.json to the previous trycloudflare URL,
            or set it to the new one printed in this step.

operator-action: teardown-tailscale-funnel
        → drops the public Funnel exposure (Tailscale stays
          installed for any future use).
```

The two systems can coexist briefly (both pointing at `localhost:8001`
on the VM, neither interfering with the other), so the rollback can
be done in any order without a hard outage window.

## Why Tailscale Funnel specifically (vs. alternatives)

Considered + rejected for this iteration:

- **Cloudflare Named Tunnel** — works, stable URL, free. **Requires
  a domain on a Cloudflare zone**, which we don't have yet. If the
  operator buys a domain later (e.g., via Cloudflare Registrar at
  ~$10/yr), we can migrate from Tailscale Funnel to a named tunnel
  on `bot-api.<your-domain>` without changing the bot side — only
  the dashboard's `vercel.json` destination changes.
- **Caddy + Let's Encrypt on the VM** — works, no domain needed if
  using a DDNS provider, but it adds TLS-cert-management surface
  that Tailscale automates for free. Worth revisiting only if we
  ever want to drop Tailscale.
- **Tailscale Serve (tailnet-only)** — exposes the service to
  authenticated tailnet members but NOT to the public Vercel edge.
  Wrong scope for this use case; Funnel is the public variant of
  Serve.

## Files touched by this migration

- `scripts/ops/setup_tailscale_funnel.sh` — new wrapper.
- `scripts/ops/teardown_tailscale_funnel.sh` — symmetric companion.
- `.github/workflows/operator-actions.yml` — `setup-tailscale-funnel`
  + `teardown-tailscale-funnel` added to the allowlist (Tier-2,
  reason required) and mapped to the wrappers above.
- `docs/runbooks/tailscale-funnel-migration.md` — this file.
- `ict-trader-dashboard/vercel.json` — `destination` repointed in
  a follow-up PR once the public URL is known.
