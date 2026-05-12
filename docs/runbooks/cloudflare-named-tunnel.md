# Cloudflare Named Tunnel — Operator Runbook

> **Adopted:** 2026-05-12 (T1 of the post-outage reconstruction sprint)
> **Replaces:** the ephemeral quick-tunnel pattern in `scripts/ops/setup_cloudflare_tunnel.sh`
> **Audit:** [`docs/audit/2026-05-12-end-to-end-audit.md`](../audit/2026-05-12-end-to-end-audit.md) § L3 + § 6 T1

## What this is

A **stable** HTTPS edge for `ict-web-api.service` (port 8001). The hostname
does not change when `cloudflared` restarts, so `vercel.json` is pinned
once and stays pinned — no more rotation-chasing PRs.

```
Browser → Vercel edge → https://<stable>.cfargotunnel.com
                              (or https://<hostname>.<your-zone>)
                      → cloudflared (systemd, Restart=always)
                      → http://localhost:8001 (ict-web-api.service)
```

The tunnel is named `ict-trader-bot-tunnel`. The systemd unit is
`ict-cloudflared-tunnel.service` (`deploy/ict-cloudflared-tunnel.service`).
The setup script is `scripts/ops/setup_named_cloudflare_tunnel.sh`.

## Prerequisites (one-time)

Set on the bot repo at **Settings → Secrets and variables → Actions**:

| Secret | Scope | Notes |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Account: **Cloudflare Tunnel:Edit** | Required. Add **Zone:DNS:Edit** if you want a custom hostname. The token already used by `cf-worker-deploy.yml` does NOT have Tunnel:Edit; create a new fine-grained token or extend the scope. |
| `CLOUDFLARE_ACCOUNT_ID` | n/a | Found in the CF dashboard right-sidebar. Already used by `cf-worker-deploy.yml`. |

No sudoers changes needed — the script writes to `/etc/ict-trader/cloudflared/`
using the existing `NOPASSWD systemctl` grant.

## First-time setup

### Path A — `cfargotunnel.com` (no DNS zone required, default)

This gives a stable URL of the form
`https://<tunnel-id>.cfargotunnel.com`. Ugly but stable. Choose this if
you don't own a CF-managed domain.

Open an issue on `benbaichmankass/ict-trading-bot`:

```
Title: [operator-action] setup-named-cloudflare-tunnel
Labels: operator-action
Body:
  action: setup-named-cloudflare-tunnel
  reason: T1 reconstruction — replace ephemeral quick tunnel with stable hostname
```

The workflow comments back with the stable URL on completion.

### Path B — custom hostname (e.g. `bot-api.example.com`)

Requires:
1. A CF DNS zone you own.
2. `CLOUDFLARE_API_TOKEN` with `Zone:DNS:Edit` on that zone.

Open the same issue with an extra line:

```
  hostname: bot-api.example.com
```

The script will:
1. Create the named tunnel.
2. Upsert a proxied CNAME `bot-api.example.com → <tunnel-id>.cfargotunnel.com`.
3. Return `https://bot-api.example.com` as the stable URL.

If the zone isn't found / token lacks scope, the script logs a WARN and
falls back to the `cfargotunnel.com` URL (Path A).

## Wiring the dashboard

Once the stable URL is captured (from the workflow's comment-back), update
`ict-trader-dashboard/vercel.json`:

```json
{
  "rewrites": [
    { "source": "/api/bot/:path*", "destination": "https://<stable-url>/api/bot/:path*" },
    { "source": "/(.*)", "destination": "/" }
  ]
}
```

Commit + push to a branch, open + merge a draft PR. Vercel auto-redeploys
`main` after the merge.

## Verifying

After the dashboard redeploys, the per-endpoint diagnostics panel should
show 200s on `/api/bot/{stats,logs,positions,signals,config}`. On the VM:

```bash
systemctl is-active ict-cloudflared-tunnel.service  # active
curl https://<stable-url>/api/health                # {"ok":true}
sudo journalctl -u ict-cloudflared-tunnel -n 50 --no-pager
```

## Retiring the old quick tunnel

After **one** full healthy cycle on the named tunnel (at least 24 h with
the dashboard live), tear down the quick-tunnel residue:

```
Title: [operator-action] teardown-cloudflare-tunnel
Labels: operator-action
Body:
  action: teardown-cloudflare-tunnel
  reason: named tunnel verified live for 24h; quick tunnel no longer needed
```

The named tunnel is unaffected — these are independent paths.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Setup workflow exit 1 with "CLOUDFLARE_API_TOKEN unset" | Secrets not configured | Set on the repo per the Prerequisites table. |
| Setup workflow runs but probe fails / 502 | DNS propagation (first run only) | Wait 60 s and re-curl. If still failing after 5 min, `journalctl -u ict-cloudflared-tunnel -n 200`. |
| Tunnel keeps restarting (`is-failed`) | Bad credentials file | Re-run `setup-named-cloudflare-tunnel` (idempotent; rewrites `<tunnel-id>.json`). |
| 502 on the dashboard after `vercel.json` update | URL typo, or upstream `ict-web-api` down | Compare URL with `cat /home/ubuntu/ict-trading-bot/runtime_logs/cloudflared_tunnel_url.txt`. If correct, run `vm-web-api-recover`. |
| `setup` reports "WARN: zone not found" | Token lacks Zone:Read for that zone, or zone not on this CF account | Add `Zone:Read` + `Zone:DNS:Edit` scope on the matching zone. The script falls back to `cfargotunnel.com`; setup still succeeds. |

## Why we didn't just keep the quick tunnel

See `docs/audit/2026-05-12-end-to-end-audit.md` § L3 — the quick tunnel
rotates hostname on every cloudflared restart. With no systemd unit
and no watchdog, every silent crash forced a dashboard PR. The named
tunnel + `Restart=always` + (future) tunnel watchdog (T5) eliminates
the entire failure class.
