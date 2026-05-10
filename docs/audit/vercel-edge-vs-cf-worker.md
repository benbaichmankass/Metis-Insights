# Vercel Edge / Vercel rewrites vs. Cloudflare Workers for plain-HTTP upstream

> **Status:** Investigation note (S-CFW-1, 2026-05-10).
> **Companion to:** [`docs/sprint-logs/S-CFW-1-cloudflare-worker.md`](../sprint-logs/S-CFW-1-cloudflare-worker.md), [`cf-worker/README.md`](../../cf-worker/README.md).

## TL;DR

- The dashboard's Vercel rewrite from `/api/bot/*` to
  `http://158.178.210.252:8001` stopped resolving on 2026-05-10.
- A Vercel Edge Function attempt (route handler that proxied to
  the same upstream) also failed; it was reverted in the
  dashboard repo the same day.
- Cloudflare Workers' `fetch` API has no equivalent restriction
  on plain HTTP to arbitrary IP+port, which is why
  [`cf-worker/`](../../cf-worker/) is the path forward.

## What we observed

From the 2026-05-10 session wrap-up:

> Edge Function attempt + revert (Vercel restricts user-function
> HTTP outbound too)

> the dashboard's Vercel rewrite from `/api/bot/*` to
> http://158.178.210.252:8001 stopped resolving. The bot's API
> is reachable directly from a browser (verified 2026-05-10) but
> Vercel's edge isn't proxying — likely Vercel tightened their
> HTTP-upstream policy on Hobby plan.

The bot's API itself is healthy: `curl http://158.178.210.252:8001/api/health`
from any unrestricted host returns `{"status": "ok"}`. The
failure mode is **Vercel-side**, not VM-side.

## Why Vercel can't reach plain-HTTP IP upstreams

Three relevant restrictions, layered:

1. **Vercel rewrites destination must be HTTPS** for external
   destinations on Hobby plan. Vercel's rewrite docs note that
   external destinations are fetched over the platform's edge
   network and that HTTPS is required; plain-HTTP destinations
   to non-vercel hosts have been progressively tightened.
   ([Vercel rewrites docs](https://vercel.com/docs/edge-network/rewrites)
   describe the supported destination forms — internal paths
   and HTTPS URLs.)
2. **Vercel Edge Functions** run on a constrained edge runtime
   (V8 isolates, similar to Cloudflare Workers). The runtime
   itself supports `fetch`, but Vercel's egress policy on
   Hobby/Pro restricts fetches to **HTTPS-only** for outbound
   connections to non-Vercel destinations. A 2026-05-10 attempt
   to wrap the proxy in an Edge route handler hit the same
   block as the rewrite — Vercel returned a fetch error rather
   than letting the function reach the IP+port directly.
3. **Vercel Serverless (Node) Functions** in principle can do
   plain HTTP via Node's `http` module, but Vercel's networking
   layer blocks outbound to arbitrary IPs without an explicit
   allowlist on the higher-tier plans, and the Hobby plan never
   exposed that allowlist mechanism.

The practical effect: from the dashboard's Vercel deployment,
**no path** reaches `http://158.178.210.252:8001` directly. The
upstream must be an HTTPS hostname owned by something other than
Vercel.

## Why Cloudflare Workers can

Cloudflare Workers' `fetch` is implemented on Cloudflare's
network and has no analogous restriction:

- Plain HTTP (port 80, port 8001, any open TCP port that
  Cloudflare's edge routes to via HTTP) is supported.
  ([CF Workers fetch reference](https://developers.cloudflare.com/workers/runtime-apis/fetch/) —
  the Worker fetcher does what the standard Fetch API allows
  plus CF-specific options; there is no plan-tier restriction
  on the destination scheme for outbound `fetch`.)
- Workers' egress is from Cloudflare's edge IPs, not from the
  user's own colo, so destinations on the public internet are
  reachable as long as the destination accepts the connection.

The Oracle VM accepts `:8001` from anywhere (verified
2026-05-10), so Worker → VM works without any VM-side
firewall change.

## Architectural alternatives considered

| Option | Verdict |
|---|---|
| **Vercel rewrite → VM directly** | Failed 2026-05-10. Vercel won't proxy plain HTTP. |
| **Vercel Edge Function → VM directly** | Failed 2026-05-10. Same restriction at the function-runtime layer. |
| **Vercel Serverless (Node) Function → VM** | Hobby plan blocks outbound to arbitrary IPs; no allowlist exposed. |
| **Cloudflare quick tunnel + Vercel rewrite to `*.trycloudflare.com`** | Works today, but the URL is ephemeral — every cloudflared restart picks a new hostname, requiring a `vercel.json` redeploy. Live but fragile. |
| **Cloudflare named tunnel + Vercel rewrite to `bot.<our-domain>`** | Cleanest, but blocked on us not having a CF zone. |
| **Cloudflare Worker at `*.workers.dev` → VM (or → quick tunnel)** | Works, no domain needed, hostname stable forever. **Path chosen in S-CFW-1.** |

## Out-of-scope but related

- **Vercel Pro plan / Enterprise** documents an outbound
  network allowlist that would unblock the rewrite path. Cost
  is $20/user/month minimum; not justified for a dashboard with
  a known-good Workers alternative.
- **Cloudflare Tunnel on the VM (no domain)** — `cloudflared`
  also supports a "no DNS" mode where the tunnel exposes a
  stable `<id>.cfargotunnel.com` hostname **without** needing a
  zone. This is technically a third stable-URL option. Worth
  re-evaluating in a follow-up if the Worker hits limits;
  documented here for completeness so the next investigator
  doesn't re-derive it.

## Sources

- Vercel rewrites docs — [vercel.com/docs/edge-network/rewrites](https://vercel.com/docs/edge-network/rewrites).
- Vercel Edge Functions — [vercel.com/docs/functions/edge-functions](https://vercel.com/docs/functions/edge-functions).
- Cloudflare Workers fetch — [developers.cloudflare.com/workers/runtime-apis/fetch](https://developers.cloudflare.com/workers/runtime-apis/fetch/).
- Cloudflare Tunnel public hostnames — [developers.cloudflare.com/cloudflare-one/connections/connect-networks](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).
- Empirical evidence: 2026-05-10 dashboard repo wrap-up + this
  repo's `runtime_logs/cloudflared_tunnel_url.txt` (quick tunnel
  established same day).
