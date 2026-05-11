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

## Operator setup (one-time)

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
   Copy the `tskey-auth-...` value — you'll paste it once in step 5.
4. **Approve Funnel for the device** (you do this after the device
   first appears in your tailnet, i.e., after the first run of
   `setup-tailscale-funnel` puts the VM in the tailnet) —
   admin console → Machines → `ict-trader-live` → "Edit Funnel"
   → enable for this device.
5. **Put the auth key on the VM** (NOT in repo secrets — stays
   local-only):
   ```bash
   ssh ubuntu@158.178.210.252
   sudo mkdir -p /etc/ict-trader
   sudo install -m 600 /dev/null /etc/ict-trader/tailscale.env
   echo 'TS_AUTHKEY=tskey-auth-...' | sudo tee /etc/ict-trader/tailscale.env
   ```

## Dispatch the migration

Once the prereqs above are in place, the migration is two operator
actions plus a dashboard PR. From a Claude PM-side session, all three
can be driven via labelled issues — no operator clicks needed past
step 5.

```text
1.  operator-action: setup-tailscale-funnel
        → installs tailscale on the VM, authenticates via TS_AUTHKEY,
          enables Funnel on :8001, writes the public URL to
          runtime_logs/tailscale_funnel_url.txt, prints the URL in
          the workflow comment.

2.  dashboard PR: patch ict-trader-dashboard/vercel.json
        → set the rewrite destination to
          https://<vm-hostname>.<tailnet>.ts.net/api/bot/:path*
          (the URL printed in step 1).
        → merge to main; Vercel auto-deploys.

3.  operator-action: teardown-cloudflare-tunnel
        → stops the cloudflared quick-tunnel process and removes
          the @reboot crontab entry. The trycloudflare URL stops
          serving immediately. (Keep this step LAST, so we don't
          have a window where neither path is up.)
```

After step 3, the dashboard talks to the bot over a stable HTTPS URL
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
