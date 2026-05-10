# Vercel Edge / Vercel rewrites vs. Cloudflare Workers for plain-HTTP upstream

> **Status:** Investigation note. Originally written 2026-05-10
> in S-CFW-1; **revised 2026-05-10 in S-CFW-1-FU2** after the
> Worker path was empirically retired.
> **Companion to:** [`docs/sprint-logs/S-CFW-1-cloudflare-worker.md`](../sprint-logs/S-CFW-1-cloudflare-worker.md), [`docs/sprint-logs/S-CFW-1-FU2-worker-retired.md`](../sprint-logs/S-CFW-1-FU2-worker-retired.md), [`cf-worker/README.md`](../../cf-worker/README.md).

## TL;DR

- The dashboard's Vercel rewrite from `/api/bot/*` to
  `http://158.178.210.252:8001` stopped resolving on 2026-05-10.
- A Vercel Edge Function attempt (route handler that proxied to
  the same upstream) also failed; it was reverted in the
  dashboard repo the same day.
- We then tried a Cloudflare Worker at `*.workers.dev`
  (S-CFW-1) hoping its `fetch` had no IP-target restriction.
  **It does.** First live test (2026-05-10, after the Worker
  was deployed at `ict-trader-bot-proxy.ben-baichmankass.workers.dev`)
  returned Cloudflare error 1003 ("Direct IP Access Not
  Allowed") for `/api/health`. The Worker's `fetch()` to a raw
  IPv4 host is rejected at Cloudflare's edge.
- **Net result:** the Vercel rewrite to the existing
  `*.trycloudflare.com` quick tunnel (HTTPS hostname behind
  Cloudflare's edge) is the only currently-working path. The
  Worker layer was retired in S-CFW-1-FU2.

## CF error 1003 — what we hit (added 2026-05-10 in S-CFW-1-FU2)

When the deployed Worker called
`fetch("http://158.178.210.252:8001/api/health")`, Cloudflare's
edge intercepted the outbound subrequest and returned an HTML
error page with `error code: 1003`.

Cloudflare error 1003: **"Direct IP Access Not Allowed:
Cloudflare account does not have direct IP access enabled."**

This is documented for inbound requests to Cloudflare-fronted
IPs, but it also fires on **outbound Worker subrequests whose
target host is a raw IPv4 address**. The restriction is enforced
at CF's edge before the request leaves Cloudflare's network. It
applies to Free / Pro / Business plans; Enterprise can request
the override.

The Worker's `/__worker/health` (which doesn't proxy upstream)
returned the expected JSON, confirming the Worker code was
healthy — the failure is specifically the IP-target subrequest.

The original TL;DR claim ("Cloudflare Workers' fetch has no
such restriction") was **wrong**. The restriction is narrower
than Vercel's (Workers can fetch to arbitrary HTTPS / HTTP
**hostnames**, including custom ports), but it still blocks
raw IPv4 targets. Workers are useful behind any HTTPS hostname
(named tunnel, custom domain, or another CF-fronted service),
not behind a bare-IP origin.

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

## What Cloudflare Workers can and can't do (revised 2026-05-10)

Cloudflare Workers' `fetch` is implemented on Cloudflare's
network. Important distinctions, learned the hard way in
S-CFW-1-FU2:

- ✅ **HTTPS hostname** (any port that the destination
  accepts): supported.
- ✅ **Plain HTTP hostname** on a custom port (e.g.
  `http://example.com:8001`): supported.
- ❌ **Raw IPv4 host** (e.g. `http://158.178.210.252:8001`):
  **NOT supported**. CF returns error 1003 ("Direct IP Access
  Not Allowed"). Enforced at CF's edge before the request
  leaves Cloudflare's network.

The IPv4-host restriction is the one that killed our Worker
plan. We had assumed (incorrectly) that "Workers can fetch
arbitrary URLs" extended to bare IPs. It doesn't — the host
must resolve through DNS, not be presented as a literal address.

The Oracle VM at `158.178.210.252` accepts `:8001` from anywhere
(verified 2026-05-10), so the **VM** would respond — but the
request never leaves Cloudflare's edge to reach it. To use a
Worker as the fronting layer we would need an HTTPS hostname
the Worker could call (named tunnel, custom domain, or another
CF-fronted service). With neither available, the Worker layer
adds no value over calling the existing quick tunnel directly
from Vercel.

## Architectural alternatives considered

| Option | Verdict |
|---|---|
| **Vercel rewrite → VM directly** | Failed 2026-05-10. Vercel won't proxy plain HTTP. |
| **Vercel Edge Function → VM directly** | Failed 2026-05-10. Same restriction at the function-runtime layer. |
| **Vercel Serverless (Node) Function → VM** | Hobby plan blocks outbound to arbitrary IPs; no allowlist exposed. |
| **Cloudflare quick tunnel + Vercel rewrite to `*.trycloudflare.com`** | **Live path 2026-05-10.** URL is ephemeral — every cloudflared restart picks a new hostname, requiring a `vercel.json` redeploy. Operator updates the rewrite when the URL rotates. |
| **Cloudflare named tunnel + Vercel rewrite to `bot.<our-domain>`** | Cleanest, but blocked on us not having a CF zone. |
| **Cloudflare Worker at `*.workers.dev` → VM directly** | **Tried in S-CFW-1, retired in S-CFW-1-FU2.** Worker deploys cleanly but its outbound `fetch()` to a raw IPv4 host is rejected by Cloudflare with error 1003. Worker URL is stable, but the Worker itself can't reach the VM. |
| **Cloudflare Worker → CF-fronted hostname (e.g. quick tunnel) → VM** | Architecturally works (Worker → HTTPS hostname is allowed), but adds a moving piece: Worker's `ORIGIN` env var must be re-set when the quick tunnel URL rotates. Same coupling as Vercel-direct without measurable benefit unless we add tunnel-URL auto-refresh. Not pursued. |

## Out-of-scope but related

- **Vercel Pro plan / Enterprise** documents an outbound
  network allowlist that would unblock the rewrite path. Cost
  is $20/user/month minimum; not justified.
- **Cloudflare Tunnel "no zone" mode (`<id>.cfargotunnel.com`)**
  — the original S-CFW-1 investigation note claimed cloudflared
  exposes a stable `<id>.cfargotunnel.com` hostname without a
  zone. **This was wrong.** Per Cloudflare Zero Trust docs, the
  `<id>.cfargotunnel.com` form is reachable only after a
  Public Hostname route is configured in Zero Trust, which
  requires a CF zone for the public hostname. Without a zone
  there is no stable hostname option short of switching to a
  third-party reverse-proxy service or paying for Vercel Pro.
- **Tunnel-URL auto-refresh** — could write a small VM-side hook
  that runs `wrangler secret put ORIGIN` (or hits the Cloudflare
  API directly) every time `setup_cloudflare_tunnel.sh` produces
  a new URL. Only worth doing if we revive the Worker layer for
  some other reason; not on the table today.

## Sources

- Vercel rewrites docs — [vercel.com/docs/edge-network/rewrites](https://vercel.com/docs/edge-network/rewrites).
- Vercel Edge Functions — [vercel.com/docs/functions/edge-functions](https://vercel.com/docs/functions/edge-functions).
- Cloudflare Workers fetch — [developers.cloudflare.com/workers/runtime-apis/fetch](https://developers.cloudflare.com/workers/runtime-apis/fetch/).
- Cloudflare Tunnel public hostnames — [developers.cloudflare.com/cloudflare-one/connections/connect-networks](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).
- Empirical evidence: 2026-05-10 dashboard repo wrap-up + this
  repo's `runtime_logs/cloudflared_tunnel_url.txt` (quick tunnel
  established same day).
