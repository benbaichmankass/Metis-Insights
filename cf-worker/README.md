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

## Deploy runbook (primary: GitHub Actions)

The `cf-worker-deploy` workflow at
[`.github/workflows/cf-worker-deploy.yml`](../.github/workflows/cf-worker-deploy.yml)
runs `wrangler deploy` from CI, so neither the operator nor the
sandbox needs to `wrangler login` locally.

**One-time setup (operator-side):**

1. **Create a Cloudflare API token.** Cloudflare dashboard →
   "My Profile" → "API Tokens" → "Create Token" → use the
   "Edit Cloudflare Workers" template, **or** create a custom
   token with these permissions:
   - `Account → Workers Scripts → Edit` (required)
   - `Account → Account Settings → Read` (lets wrangler list
     accounts)
   No Zone permissions are needed for `*.workers.dev` deploys.
   Add Zone permissions only if/when we move to a custom domain.
2. **Find your Account ID.** Cloudflare dashboard → right
   sidebar → "Account ID".
3. **Add both as repo secrets** (Settings → Secrets and
   variables → Actions → New repository secret):
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID`

**To deploy:**

- **From the Actions UI:** Run the `cf-worker-deploy` workflow
  via "Run workflow". The job summary contains the deployed
  URL + a probe of `/api/health` end-to-end.
- **From a sandbox session (issue-driven):** open an issue in
  this repo with the `cf-worker-deploy` label. The workflow
  runs, comments the deployed URL back on the issue, and
  closes it. Mirrors the pattern used by
  `vm-diag-snapshot.yml` / `operator-actions.yml`.

The workflow fails clearly if either secret is unset — see
the "Verify required secrets" step in the workflow file.

## Deploy runbook (fallback: workstation `wrangler`)

Use only if CI is broken or for `wrangler dev` local testing.

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

## Auto-deploy on push (deferred)

The current CI workflow is **manually dispatched** —
either via the Actions UI button or via an issue with the
`cf-worker-deploy` label. A future sprint may add a
`push: paths: cf-worker/**` trigger so doc-and-code edits to the
Worker auto-deploy. Held back for now to make every production
deploy intentional while the Worker bedds in.
