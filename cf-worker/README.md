# `cf-worker/` — Cloudflare Worker proxy for the bot's API

Stable `*.workers.dev` hostname that proxies `/api/*` to the Oracle
VM's FastAPI on `http://158.178.210.252:8001`. Replaces the
ephemeral `*.trycloudflare.com` quick tunnel for the dashboard's
Vercel rewrite path.

## Why a Worker (and not a Vercel Edge Function)

Short version: Vercel rewrites and Vercel Edge Functions can't
reach plain `http://<ip>:<port>` upstreams from the dashboard's
Hobby-plan project. Cloudflare Workers' `fetch` has no such
restriction. Full evidence + citations in
[`docs/audit/vercel-edge-vs-cf-worker.md`](../docs/audit/vercel-edge-vs-cf-worker.md).

## Why a Worker (and not a Cloudflare named tunnel)

A named tunnel gives the cleanest topology, but it requires a
domain whose nameservers are pointed at Cloudflare so the Cloudflare
zone can host a `bot.<domain>` CNAME at `<tunnel-id>.cfargotunnel.com`.
We don't have such a domain right now, so the Worker is the
no-domain-needed path. When the operator adds a domain to
Cloudflare, swapping to a named tunnel is the next sprint
(see `docs/sprint-logs/S-CFW-1-cloudflare-worker.md` § Deferred
Items).

## What it does

| Path | Behaviour |
|---|---|
| `GET/POST/...  /api/*` | Forward method + filtered headers + body to `${ORIGIN}/api/*`. Streams the response body back. |
| `GET /__worker/health` | Returns `{"ok": true, "origin": <origin>, "worker": "ict-trader-bot-proxy"}`. Does NOT hit upstream. |
| Anything else | `404 Not found`. |

The Worker strips Cloudflare-managed headers (`cf-*`, `host`,
`x-forwarded-*`, `x-real-ip`, `cdn-loop`) before forwarding so the
FastAPI app sees clean headers. Upstream errors return `502` with
a JSON body identifying the upstream URL.

## Deploy runbook

Operator-driven; the sandbox cannot run `wrangler login`.

1. From a workstation:
   ```bash
   npm install -g wrangler   # or use `npx wrangler@latest …` below
   cd cf-worker
   ```
2. Authenticate with Cloudflare:
   ```bash
   wrangler login
   ```
3. Local smoke test:
   ```bash
   wrangler dev
   # in another terminal:
   curl http://localhost:8787/api/health
   curl http://localhost:8787/__worker/health
   ```
   Both should return JSON; the first proxies to the VM, the
   second is Worker-internal.
4. Deploy:
   ```bash
   wrangler deploy
   ```
   Wrangler prints the published URL, e.g.
   `https://ict-trader-bot-proxy.<your-subdomain>.workers.dev`.
5. Post-deploy probe:
   ```bash
   WORKER_URL="https://ict-trader-bot-proxy.<your-subdomain>.workers.dev"
   curl "${WORKER_URL}/api/health"
   curl "${WORKER_URL}/__worker/health"
   ```
6. Swap the dashboard's Vercel rewrite. In **both** dashboard
   projects (`bentzbk/ict-trader-dashboard` and
   `the-lizardking/ict-trader-dashboard`), update `vercel.json`:
   ```json
   {
     "rewrites": [
       { "source": "/api/bot/(.*)",  "destination": "https://ict-trader-bot-proxy.<sub>.workers.dev/api/bot/$1" },
       { "source": "/api/pnl/(.*)",  "destination": "https://ict-trader-bot-proxy.<sub>.workers.dev/api/pnl/$1" },
       { "source": "/api/status",    "destination": "https://ict-trader-bot-proxy.<sub>.workers.dev/api/status" },
       { "source": "/api/health",    "destination": "https://ict-trader-bot-proxy.<sub>.workers.dev/api/health" }
     ]
   }
   ```
   Apply on every route currently rewritten to the
   `*.trycloudflare.com` URL.
7. Redeploy both Vercel projects (clear build cache once if it
   bites — see the 2026-05-10 wrap-up note about bentzbk's stale
   cache).
8. Verify the dashboard loads live data from both URLs.
9. Append the Worker URL + verification timestamps to
   `docs/sprint-logs/S-CFW-1-cloudflare-worker.md`.
10. After one healthy cycle, retire the quick tunnel via the
    `teardown-cloudflare-tunnel` operator action. (Leave it
    running until then as a fallback.)

## Updating the upstream `ORIGIN`

If the VM IP ever changes:

```bash
# Edit wrangler.toml [vars] ORIGIN = "http://<new-ip>:8001"
# then redeploy:
wrangler deploy
```

Or, without a code change:

```bash
wrangler secret put ORIGIN
# paste the new value when prompted
```

`wrangler secret` overrides `[vars]` at runtime. Use this for
quick incident response; commit the `wrangler.toml` change for
durability.

## Rollback

Two options if the Worker misbehaves:

- **Fast (zero downtime)**: revert the `vercel.json` rewrites to
  the `*.trycloudflare.com` URL. The quick tunnel is still
  running until step 10 above.
- **Permanent**: `wrangler delete` removes the Worker entirely.

## Plan limits

Workers Free tier: 100,000 requests/day, 10 ms CPU per request.
The dashboard's polling rate is well below this; if it ever
bites, switch to the Paid plan ($5/mo) via the Cloudflare
dashboard — no code change required.

## Why not host this in CI

A future sprint may add a GitHub Action that runs `wrangler
deploy` on changes under `cf-worker/**`, gated by a
`CLOUDFLARE_API_TOKEN` repo secret. Out of scope here — keeping
the operator workflow manual until the Worker has been live for
a cycle.
