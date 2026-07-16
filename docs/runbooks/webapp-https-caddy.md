# Web-app HTTPS front (Caddy) — SPA Phase-0

The Svelte SPA (`ict-trader-dashboard/webapp`, GitHub Pages) calls this bot's
FastAPI **browser-direct**. A GitHub Pages page is HTTPS, so a mixed-content
`http://141.145.193.91:8001` fetch is hard-blocked. Phase-0 puts a public HTTPS
front in place: **Caddy** on the live VM reverse-proxies `localhost:8001` with
an automatic Let's Encrypt cert for `ict-bot.duckdns.org`. `reverse_proxy`
upgrades WebSocket connections transparently, so `/ws/market` streams over WSS
unchanged.

```
Browser ─HTTPS→ GitHub Pages (SPA)
   └─HTTPS/WSS→ ict-bot.duckdns.org (:443) → Caddy → localhost:8001 (ict-web-api)
```

## Pieces

| Piece | Where |
|---|---|
| Caddy config | [`deploy/caddy/Caddyfile`](../../deploy/caddy/Caddyfile) — reverse_proxy localhost:8001, LE-pinned issuer |
| Install script | [`scripts/ops/install_caddy.sh`](../../scripts/ops/install_caddy.sh) — idempotent apt install + deploy Caddyfile + reload |
| Deploy workflow | [`.github/workflows/vm-caddy-deploy.yml`](../../.github/workflows/vm-caddy-deploy.yml) — label `vm-caddy-deploy` |
| CORS allow | `src/web/api/main.py` — adds `https://benbaichmankass.github.io` (browser-direct now); extra origins via `WEBAPP_ORIGINS` (CSV) |
| Port opening | `vm-cloud-fix` (OCI Security List) + `vm-net-fix` (host firewall) |

## Deploy sequence (one-time)

Preconditions already done by the operator: DuckDNS `ict-bot.duckdns.org →
141.145.193.91`; GitHub Pages Source = "GitHub Actions" on the dashboard repo.

1. **Merge the Phase-0 PR to `main`** (this repo) so `install_caddy.sh` + the
   Caddyfile + the CORS change are on `main` (the deploy resets the VM checkout
   to `origin/main`).
2. **Open ports 80 + 443** — dispatch each for both ports:
   - `vm-cloud-fix-request` issue, body `port: 443` (then `port: 80`) — OCI Security List ingress.
   - `vm-net-fix-request` issue, body `port: 443` (then `port: 80`) — host iptables/ufw.
3. **Run `vm-caddy-deploy`** — open a `vm-caddy-deploy`-labelled issue. It syncs
   the VM to `origin/main`, installs/reloads Caddy, restarts `ict-web-api` (CORS),
   and verifies (`caddy` active, upstream `:8001` `/health`, public HTTPS
   `/api/health`, CORS preflight from the Pages origin).
4. **Point the SPA at it** — the webapp default is already
   `https://ict-bot.duckdns.org`; merge the dashboard PR to trigger the Pages
   deploy, then verify the live site pulls data.

Caddy issues the cert on the first inbound ACME challenge once 80+443 are open;
until then the HTTPS probe in step 3 reports "not reachable yet" (not a failure).

## Verify

```
curl -sS https://ict-bot.duckdns.org/api/health          # {"status":"ok"} over TLS
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X OPTIONS -H 'Origin: https://benbaichmankass.github.io' \
  -H 'Access-Control-Request-Method: GET' \
  https://ict-bot.duckdns.org/api/bot/stats               # 200 preflight
```

## Rollback

Caddy is additive — the plain `:8001` origin is untouched, so removing the front
is non-destructive. `sudo systemctl disable --now caddy` stops it; the CORS
allow-list entry is inert without a browser-direct consumer. Ports can be
re-closed by reversing the `vm-net-fix` rule (`sudo iptables -D INPUT -p tcp
--dport 443 -j ACCEPT`) and the OCI Security List entry in the console.

## Notes

- **Tier 2** (new live service surface + a web-api read-bounce). No trader code
  path, account mode, strategy, or risk config is touched.
- Caddy runs on the live VM only. It is installed by this workflow, not by
  `install_systemd_units.sh` (which auto-enables `deploy/*.service` — the
  Caddyfile deliberately lives under `deploy/caddy/`, not as a unit there, so it
  is never auto-enabled on the trainer/gateway VMs).
