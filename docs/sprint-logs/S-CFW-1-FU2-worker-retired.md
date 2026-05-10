# Sprint Log: S-CFW-1-FU2 — cf-worker retired (IP restriction)

## Date Range
- Start: 2026-05-10
- End: 2026-05-10 (PARTIAL — code + docs done; operator-side
  Vercel rewrite verification + (later) Worker dashboard deletion
  pending)

## Objective
- Primary goal: empirically verify whether the Cloudflare
  Worker shipped in S-CFW-1 actually solves the
  Vercel-can't-reach-VM-IP problem. Outcome: **no, it
  doesn't.** Worker's outbound `fetch()` to a raw IPv4 host is
  rejected by Cloudflare's edge (error 1003).
- Secondary goal: leave a clean trail so the next session
  doesn't re-run the same experiment. Mark `cf-worker/` as
  deprecated, fix the wrong claim in the original investigation
  note, and confirm the existing `*.trycloudflare.com` quick
  tunnel remains the live path for Vercel rewrites.
- Tertiary: extend `scripts/ops/pull_logs.sh` to surface the
  current quick-tunnel URL in the operator-actions issue
  comment so future sessions don't have to ask the operator to
  paste it.

## Tier
- Tier 2 (corrects published architectural claim + retires a
  deployed network surface).
- Justification: the original Worker investigation note
  asserted "CF Workers' fetch has no such restriction" on
  plain HTTP IP+port. That claim was empirically wrong. Other
  sessions reading the note would make the same mistake. This
  sprint corrects the record + retires the live (broken)
  Worker URL.

## Starting Context
- S-CFW-1 (PR #735) shipped the Worker source + documented the
  Vercel-Edge-vs-Worker investigation; deployment was
  operator-gated.
- S-CFW-1-FU (PR #740) shipped the GHA deploy workflow.
- Same-day operator unblocked all three deploy prerequisites:
  rotated the `CLOUDFLARE_API_TOKEN` after first auth-error
  10000, registered a `*.workers.dev` subdomain
  (`ben-baichmankass.workers.dev`).
- Run #3 (issue #751) succeeded: Worker deployed at
  `https://ict-trader-bot-proxy.ben-baichmankass.workers.dev`.
- Probe results from the operator's browser:
  - `/__worker/health` → `{"ok":true,"origin":"http://158.178.210.252:8001","worker":"ict-trader-bot-proxy"}` (Worker code healthy).
  - `/api/health` → `error code: 1003` (Cloudflare HTML page).
- Operator picked "Drop Worker, use tunnel direct" path.

## Repo State Checked
- Branch or commit reviewed: `claude/cfw-1-fu2-drop-worker`
  off `main` at the latest post-#740 commit.
- Deployment state reviewed: `*.trycloudflare.com` quick tunnel
  still the live path for the dashboard's Vercel rewrite
  (per 2026-05-10 wrap-up; pending re-confirmation via
  pull-latest-logs in this sprint's follow-up).
- Canonical docs reviewed:
  `docs/audit/vercel-edge-vs-cf-worker.md` (S-CFW-1 original),
  `docs/sprint-logs/S-CFW-1-cloudflare-worker.md`,
  `docs/sprint-logs/S-CFW-1-FU-gha-deploy.md`,
  `cf-worker/README.md`.

## Files and Systems Inspected
- Code files inspected: `cf-worker/src/index.js` (confirmed
  `/__worker/health` works → code path that doesn't proxy is
  fine; only the `fetch(IP-target)` path is broken),
  `scripts/ops/pull_logs.sh` (existing structure for the
  operator-action audit-trail bundle).
- Config files inspected: `.github/workflows/operator-actions.yml`
  (confirmed `pull-latest-logs` is a Tier-1 action
  reachable via issue-driven dispatch).
- Docs inspected: `docs/audit/vercel-edge-vs-cf-worker.md`
  (location of the wrong claim that needed correcting).

## Work Completed
- Updated: `docs/audit/vercel-edge-vs-cf-worker.md`. Added a
  new "CF error 1003 — what we hit" section documenting the
  empirical failure. Replaced the "Why Cloudflare Workers can"
  section with a more accurate "What Cloudflare Workers can
  and can't do" matrix (HTTPS hostname ✅, HTTP hostname ✅,
  raw IPv4 ❌). Updated the architectural-alternatives table:
  Worker → VM directly is now marked retired with the 1003
  failure noted; added Worker → CF-fronted-hostname as a
  considered-but-not-pursued option. Replaced the wrong
  "no DNS mode" cargo-cult claim with the actual Cloudflare
  Tunnel + Zero Trust requirement (a public hostname route
  needs a CF zone). Updated the TL;DR to lead with "the Worker
  layer was retired in S-CFW-1-FU2".
- Updated: `cf-worker/README.md`. Added a top-of-file
  DEPRECATED banner that pins the failure mode (1003 on
  IP-target fetch), points at this sprint log, and lists the
  two viable revival paths (CF zone + named tunnel; or
  tunnel-URL auto-refresh hook). Body kept intact for
  historical reference.
- Updated: `scripts/ops/pull_logs.sh`. New section that cats
  `runtime_logs/cloudflared_tunnel_url.txt` so the
  operator-actions issue comment now carries the current
  quick-tunnel URL out of the box. Falls back to a clear
  "(no cloudflared tunnel URL recorded — tunnel may not be
  running)" message when the file is absent.
- Updated: `ROADMAP.md` ledger row for S-CFW-1 + S-CFW-1-FU
  flipped to "RETIRED 2026-05-10 (S-CFW-1-FU2)"; new ledger
  row added for S-CFW-1-FU2.
- New: this sprint log.

## Validation Performed
- Tests run: n/a (no Python or JS test changes).
- Dry-runs or staging checks: n/a — `pull_logs.sh` change is a
  single new section emitting plain text; the operator-action
  workflow runs the script from main on each invocation and
  uploads the output as an artifact.
- Manual code verification:
  - Walked the Worker code one more time to confirm
    `/__worker/health` does not call `fetch` and would have
    succeeded regardless of the IP restriction. The contrast
    with `/api/health` (which does call `fetch(http://IP:port/...)`)
    isolates the failure to CF's outbound edge interception.
  - Checked the wrong claim's exact wording in
    `vercel-edge-vs-cf-worker.md` and rewrote it with the
    matrix that distinguishes HTTPS / HTTP hostname / raw IPv4.
- Gaps not yet verified (operator-driven):
  - Operator-actions `pull-latest-logs` run with the new
    pull_logs.sh in main, to confirm the tunnel URL surfaces
    in the issue comment.
  - Quick tunnel still alive (pending the same run).
  - Optional: operator deletes the deprecated Worker via the
    Cloudflare dashboard. Not required for correctness — the
    Worker just sits idle on the operator's account — but
    cleanest.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none directly. The Worker isn't
  part of the trade pipeline.
- Trade pipeline doc updates: none.
- Roadmap updates: see "Work Completed". S-CFW-1 + S-CFW-1-FU
  ledger rows flipped to RETIRED; S-CFW-1-FU2 row added.
- GitHub Actions doc updates: none.
- Subsystem doc updates:
  `docs/audit/vercel-edge-vs-cf-worker.md` (corrected),
  `cf-worker/README.md` (deprecated banner).
- Historical docs marked superseded: implicitly — the
  S-CFW-1 / S-CFW-1-FU sprint logs now have their conclusions
  inverted, but they remain accurate as records of what was
  attempted.

## Contradictions or Drift Found
- **The wrong claim**: `vercel-edge-vs-cf-worker.md` originally
  said "CF Workers' fetch has no such restriction" on plain
  HTTP IP+port. The empirical failure (CF error 1003 on
  `/api/health`) contradicts this. Corrected in this sprint
  with the HTTPS/HTTP-hostname/IP matrix.
- **The wrong claim, version 2**: same doc claimed
  `<id>.cfargotunnel.com` is reachable without a CF zone.
  Per CF Zero Trust docs, this requires a Public Hostname
  route which itself requires a zone. Corrected.
- **Sprint log titles**: S-CFW-1 + S-CFW-1-FU still describe
  the Worker as the path forward. Left as-is — they are
  historical records of the decision at that point. The
  ROADMAP ledger now reflects RETIRED so anyone scanning the
  current state lands on this sprint log instead.

## Risks and Follow-Ups
- Remaining technical risks:
  - The quick tunnel is still ephemeral. Every cloudflared
    restart picks a new `*.trycloudflare.com` hostname. The
    operator must update `vercel.json` rewrites in the
    dashboard repo each time. This was the original problem
    S-CFW-1 was meant to solve and is now back on the
    "items under consideration" list.
  - The deprecated Worker still exists in the operator's
    Cloudflare account. It serves no traffic (no Vercel
    rewrite points at it) and incurs no cost, but it's
    untidy. Operator can delete via the Cloudflare dashboard
    at any time.
- Remaining product decisions (Tier 3): none.
- Blockers: none for this sprint's deliverables.

## Operator runbook (post-merge)
1. Wait for this PR to merge to main (CI required for
   `pull_logs.sh` changes to take effect on the next
   operator-actions run, since the workflow checks out main).
2. Open an issue with title `[operator-action]
   pull-latest-logs` and label `operator-action`, body:
   ```
   action: pull-latest-logs
   reason: pull current cloudflared_tunnel_url.txt to confirm
   live URL for the dashboard rewrite after S-CFW-1-FU2 retired
   the Worker path
   ```
   Workflow comments the artifact contents back on the issue.
3. From the comment, copy the `*.trycloudflare.com` URL.
4. Verify the dashboard repo's `vercel.json` rewrites already
   point at that URL (per 2026-05-10 wrap-up they should).
   If they don't, edit + redeploy.
5. (Optional) Cloudflare dashboard → Workers & Pages →
   `ict-trader-bot-proxy` → Settings → Delete. Removes the
   deprecated Worker. Keep the
   `ben-baichmankass.workers.dev` subdomain — it's free and
   may be useful later.

## Deferred Items
- **Tunnel-URL auto-refresh** — VM-side hook that pushes the
  new `*.trycloudflare.com` URL into either Vercel (via API)
  or back into a CF Worker's `ORIGIN` env var (via wrangler
  / CF API) every time `setup_cloudflare_tunnel.sh` produces
  a new URL. Eliminates the operator-update step. Out of
  scope today.
- **Named tunnel migration** — still blocked on the operator
  adding a domain to Cloudflare. Unchanged from S-CFW-1.
- **Vercel Pro plan** — would unblock direct
  Vercel-rewrite-to-IP, but at $20/user/month not justified
  while the quick tunnel works.

## Next Recommended Sprint
- Suggested next sprint: operator runs `pull-latest-logs`,
  confirms the live tunnel URL, and we close this log with
  the actual hostname appended.
- Why next: closes the loop on the S-CFW-1 / -FU / -FU2 thread
  and re-establishes confidence in the live dashboard path
  before any further infra work.
- Required verification before starting: this PR merged.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from
  summaries.
- [x] Documentation was reviewed and updated as part of the
  sprint.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
