# Sprint Log: S-CFW-1-cloudflare-worker

## Date Range
- Start: 2026-05-10
- End: 2026-05-10 (PARTIAL — Worker code + runbook are ready;
  operator-driven `wrangler deploy` + Vercel rewrite swap
  pending)

## Objective
- Primary goal: stand up a Cloudflare Worker at
  `<name>.workers.dev` that proxies `/api/*` to the bot's
  FastAPI on `http://158.178.210.252:8001`. Replaces the
  ephemeral `*.trycloudflare.com` quick tunnel with a stable
  hostname that survives any restart, with no domain required.
- Secondary goals:
  - Capture the investigation findings from the wrap-up note
    "Vercel Edge Functions can't outbound plain HTTP" in a
    durable doc so the next session doesn't re-derive it.
  - Defer the original Task #2 (Cloudflare **named tunnel**
    migration) cleanly with the prereq stated, since the
    operator does not currently have a domain whose nameservers
    point at Cloudflare.

## Tier
- Tier 2 (introduces a new public network surface in front of
  the bot's API).
- Justification: the Worker fronts the same Tier 1 / Tier 2
  read endpoints already reachable via the quick tunnel — no new
  data is exposed and the bot's own auth (`DIAG_READ_TOKEN`,
  session cookies) still gates anything sensitive. But the
  Worker URL is **stable and public** once deployed, so it
  belongs above Tier 1 in the rules taxonomy. Operator approval
  recorded in this conversation.

## Starting Context
- Wrap-up of 2026-05-10 listed three sustainability follow-ups
  for the next session (CFI 7-day soak, named tunnel migration,
  Edge-Functions-vs-Worker investigation). This sprint takes the
  second + third.
- Operator confirmed in-conversation they do **not** have a
  domain whose nameservers are pointed at Cloudflare. The Vercel
  app URL (`bentzbk-ict-trader-dashboard.vercel.app`) is owned
  by Vercel and cannot host a Cloudflare tunnel hostname. So the
  named-tunnel half of the original two-task list is blocked on
  an operator prereq and was deferred.
- Prior sprint reference: `setup_cloudflare_tunnel.sh`'s header
  comment already flagged "A follow-up sprint will swap to a
  named tunnel via Cloudflare API token (stable URL, survives
  all restarts) once the operator generates a token." This
  sprint redirects that follow-up to the Worker path because the
  named-tunnel prereq isn't met.
- Known risks at start: Worker → VM reaches `http://158.178.210.252:8001`
  over plain HTTP; if the operator later firewalls the VM port
  to private-only, the Worker breaks. Mitigation deferred to a
  later sprint (would need either (a) Cloudflare WARP / tunnel
  on the VM, or (b) IP allowlist for Cloudflare's egress
  ranges).

## Repo State Checked
- Branch or commit reviewed: `claude/start-sprint-tasks-2-3-99SJR`
  off `main` at `637955a`.
- Deployment state reviewed: `setup_cloudflare_tunnel.sh` is the
  current path, run via `operator-actions.yml`. Quick tunnel was
  established 2026-05-10 at
  `planners-lbs-blind-trainer.trycloudflare.com` per the wrap-up;
  this sprint does not tear it down — the Worker stands up
  alongside, then takes over once the Vercel rewrite is swapped.
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md` (for
  tier classification), `docs/claude/operator-actions.md`,
  `CLAUDE.md` § "PM-side session capabilities".

## Files and Systems Inspected
- Code files inspected: `scripts/ops/setup_cloudflare_tunnel.sh`,
  `scripts/ops/teardown_cloudflare_tunnel.sh`,
  `.github/workflows/operator-actions.yml`,
  `src/web/api/main.py` (CORS allowlist).
- Config files inspected: `ROADMAP.md` (sprint ledger + items
  under consideration).
- Docs inspected: `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`,
  `docs/sprint-logs/S-CANON-FU-3-branch-protection.md` (style
  reference for partial-completion sprint logs).

## Work Completed
- New: `cf-worker/src/index.js` — Module-Worker proxy. `/api/*`
  forwards method, headers (filtered), body, query string to
  the upstream `ORIGIN` env var. Strips Cloudflare-managed
  headers (`cf-*`, `host`, `x-forwarded-for`, `x-real-ip`)
  before forwarding so the FastAPI app sees clean headers. 502
  on upstream-unreachable. `/__worker/health` lets the
  operator probe the Worker itself without proxying.
- New: `cf-worker/wrangler.toml` — module-worker config,
  `compatibility_date = "2024-12-01"`. `ORIGIN` is a
  `[vars]` entry so the operator can change it via
  `wrangler secret put` or the dashboard without code edits.
- New: `cf-worker/README.md` — deploy runbook (auth, dry-run,
  deploy, post-deploy probe, rollback, env-var update flow).
- New: `docs/audit/vercel-edge-vs-cf-worker.md` — investigation
  findings: why Vercel rewrites + Vercel Edge Functions can't
  reach `http://158.178.210.252:8001` while CF Workers can.
  Captures the empirical evidence from the 2026-05-10 wrap-up
  and the relevant Vercel + Cloudflare doc citations so the
  next session has a single canonical reference.
- Updated: `ROADMAP.md` — sprint ledger row for `S-CFW-1`,
  follow-up note under "Items Under Consideration" pinning the
  named-tunnel prereq (CF zone needed).

## Validation Performed
- Tests run: n/a (no Python or JS tests added — the Worker is
  the only code change and runs in CF's runtime, not in CI).
- Dry-runs or staging checks: n/a — `wrangler deploy` is
  operator-gated (operator must `wrangler login` from a machine
  they auth on; the sandbox cannot). Runbook walks the operator
  through a `wrangler dev` local smoke test before the live
  deploy.
- Manual code verification: walked the Worker fetch handler by
  hand against the Cloudflare Workers Runtime APIs reference —
  fetch with body streaming on `request.body`, manual redirect
  mode, header-iteration via the `Headers` web API. Cross-checked
  the `for...of` over `headers` shape against the
  `lib.dom.iterable` types Workers ships.
- Gaps not yet verified (operator-gated):
  - Actual `wrangler deploy` and resulting `*.workers.dev` URL.
  - End-to-end probe via the Worker URL once live.
  - `vercel.json` rewrite swap on the dashboard repo.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none (the Worker isn't part of the
  trade pipeline; it's a network-front-door artefact).
- Trade pipeline doc updates: none.
- Roadmap updates: ledger row + items-under-consideration
  pruning (named-tunnel item gets a prereq tag).
- GitHub Actions doc updates: none (Worker deploy is not
  workflow-driven; future sprint may add a GitHub Action that
  runs `wrangler deploy` on a `cf-worker/**` change with a
  `CLOUDFLARE_API_TOKEN` repo secret, but that's a separate
  scope).
- Subsystem doc updates: new `cf-worker/README.md` is the
  Worker's own subsystem doc.
- Historical docs marked superseded: none. The quick-tunnel
  setup script remains valid as a fallback / development tool.

## Contradictions or Drift Found
- None new. The wrap-up's named-tunnel item carried an implicit
  assumption ("operator generates a CF API token") that was
  insufficient — a CF zone is also required. This sprint
  records that explicitly so future planning doesn't re-make
  the same scoping mistake.

## Risks and Follow-Ups
- Remaining technical risks:
  - VM port 8001 currently accepts traffic from anywhere. The
    Worker doesn't change that. If the operator later wants to
    lock the origin down, the cleanest path is a CF tunnel on
    the VM with the Worker as the only public surface. Out of
    scope here.
  - Worker plan: Workers Free tier gives 100k requests/day; the
    dashboard's polling rate is far below this even at 5 active
    users. Bumping to Paid is one click if it ever bites.
  - CORS: the Worker forwards the FastAPI CORS response headers
    untouched. The Worker's hostname (`*.workers.dev`) is **not**
    currently in `src/web/api/main.py`'s CORS allowlist — but
    Vercel rewrites are server-side, so the browser never sees
    the Worker URL in a cross-origin context. If we ever wire
    the dashboard to call the Worker directly (no Vercel rewrite),
    we'd need to add it to `DASHBOARD_ORIGIN` / the allowlist.
- Remaining product decisions (Tier 3): none.
- Blockers: operator runs `wrangler deploy` and shares the
  resulting `*.workers.dev` URL so the Vercel rewrite can be
  swapped.

## Operator runbook (pending action)
1. From a workstation: `cd cf-worker && npm install -g wrangler`
   (or use `npx wrangler@latest`).
2. `wrangler login` → browser auth flow.
3. `wrangler dev` → local smoke. `curl
   http://localhost:8787/api/health` should return the bot's
   health JSON.
4. `wrangler deploy`. Note the published URL — looks like
   `ict-trader-bot-proxy.<your-subdomain>.workers.dev`.
5. Probe: `curl https://<that-url>/api/health` returns JSON;
   `curl https://<that-url>/__worker/health` returns
   `{"ok": true, "origin": "http://158.178.210.252:8001"}`.
6. In the **dashboard repo** (`bentzbk/ict-trader-dashboard` and
   `the-lizardking/ict-trader-dashboard`), update
   `vercel.json` rewrites:
   ```json
   { "source": "/api/bot/(.*)", "destination": "https://<worker>.workers.dev/api/bot/$1" }
   ```
   Apply on the analogous routes (`/api/pnl/*`, `/api/status`,
   etc. — anything currently rewritten to the trycloudflare URL).
7. Redeploy both Vercel projects. Verify the dashboard loads
   live data.
8. Once verified, the quick tunnel is no longer load-bearing.
   Leave it running for one cycle as a fallback, then run
   `teardown-cloudflare-tunnel` via `operator-actions.yml` to
   shut it down.
9. Append the actual Worker URL + verification timestamps to
   this log (or a follow-up).

## Deferred Items
- **Cloudflare named tunnel migration (original Task #2)** —
  blocked on operator adding a domain to Cloudflare. When that
  prereq is met, the work is: generate API token scoped to
  `Tunnel:Edit` + `DNS:Edit` for that zone; new wrapper
  `setup_named_tunnel.sh` that creates the tunnel, writes
  `~/.cloudflared/<id>.json` credentials, runs cloudflared with
  that file, and creates a CNAME `bot.<domain> → <tunnel-id>.cfargotunnel.com`.
  Once live, the Worker becomes optional (named tunnel + Vercel
  rewrite is a simpler topology). Estimate ~30 min as originally
  scoped.
- **GitHub-Actions-driven Worker deploy** — add a workflow that
  runs `wrangler deploy` on changes under `cf-worker/**`,
  gated by a `CLOUDFLARE_API_TOKEN` repo secret. Removes the
  operator-workstation step from the deploy loop. Out of scope
  for this sprint to keep the surface area minimal until the
  Worker has been live for a cycle.
- **Worker observability** — Cloudflare Workers ship logs via
  `wrangler tail` and analytics via the dashboard. No log
  forwarding to the bot's own `runtime_logs/` is wired up. If
  diagnostics ever need correlation with bot-side audit logs,
  add a Logpush destination in a follow-up sprint.

## Next Recommended Sprint
- Suggested next sprint: operator deploys the Worker per the
  runbook above; Claude appends the live URL + verification
  timestamps to this log.
- Why next: closes out two of the three wrap-up follow-ups
  (Worker as primary stable URL; Edge-vs-Worker investigation
  done). Leaves only the CFI 7-day soak (operator-driven, file
  the auto-flatten promotion PR if violations stay at zero
  through 2026-05-17).
- Required verification before starting: operator has a
  Cloudflare account (free tier is enough) and a workstation
  where they can run `wrangler login`.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from
  summaries.
- [x] Documentation was reviewed and updated as part of the
  sprint.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
