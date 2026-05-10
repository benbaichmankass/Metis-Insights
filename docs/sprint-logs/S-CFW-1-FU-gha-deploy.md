# Sprint Log: S-CFW-1-FU — cf-worker GitHub-Actions deploy

## Date Range
- Start: 2026-05-10
- End: 2026-05-10 (PARTIAL — workflow + label + docs are
  ready; operator-gated secret setup + first deploy pending)

## Objective
- Primary goal: ship a GitHub Actions workflow that deploys
  `cf-worker/` via `wrangler deploy`, removing the need for
  any operator-workstation `wrangler login`. The sandbox can
  fire the deploy by opening a labelled issue; the workflow
  comments the deployed URL back.
- Secondary goal: pull the resulting `*.workers.dev` URL into
  the audit trail (sprint log) so the next session does not
  have to re-derive it.

## Tier
- Tier 2 (introduces a new CI workflow that mutates a public
  network surface).
- Justification: matches the Tier-2 framing of the original
  S-CFW-1 sprint. Operator approval is recorded in this
  conversation ("you need to deploy with guys actions and pull
  the url" — 2026-05-10).

## Starting Context
- S-CFW-1 (PR #735) merged 2026-05-10, shipping `cf-worker/`
  source + a manual workstation runbook, with CI deploy
  explicitly deferred under "Why not host this in CI".
- Operator subsequently asked to ship CI deploy now rather than
  wait for the deferred sprint, since they didn't want to
  `wrangler login` on a workstation. This sprint moves that
  follow-up forward.
- Known risks at start: requires two new repo secrets
  (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`). The
  sandbox cannot create repo secrets; this is operator-gated.

## Repo State Checked
- Branch or commit reviewed: `claude/cfw-1-fu-gha-deploy`
  off `main` at `403a782` (the squash of PR #735).
- Deployment state reviewed: `*.trycloudflare.com` quick tunnel
  is still the live path. The Worker has not been deployed yet.
- Canonical docs reviewed: existing issue-driven workflow
  patterns in `vm-diag-snapshot.yml`, `vm-web-api-recover.yml`,
  `operator-actions.yml`, `health-snapshot-pr.yml`.

## Files and Systems Inspected
- Code files inspected: `cf-worker/wrangler.toml`,
  `cf-worker/src/index.js`, `cf-worker/README.md`.
- Config files inspected: `.github/workflows/bootstrap-labels.yml`,
  `.github/workflows/health-snapshot-pr.yml` (style reference
  for `actions/github-script@v7` issue-comment + close).
- Docs inspected: `docs/sprint-logs/S-CFW-1-cloudflare-worker.md`.

## Work Completed
- New: `.github/workflows/cf-worker-deploy.yml`. Two dispatch
  paths:
  - `workflow_dispatch` — operator clicks Run.
  - `issues.opened` filtered to label `cf-worker-deploy` —
    sandbox-driven.
  Steps: secrets verification (fails early with a clear error
  if either secret is missing); `cloudflare/wrangler-action@v3`
  to deploy from the `cf-worker/` working directory; probe
  both `/__worker/health` (Worker-internal) and `/api/health`
  (end-to-end via the VM); job summary with the deployed URL +
  copy-pasteable `vercel.json` snippet; on issue-driven runs,
  comment the same content back on the issue and close it.
  Failure path also comments the run URL + the most common
  failure modes back on the issue (kept open for triage).
- Updated: `.github/workflows/bootstrap-labels.yml` adds the
  `cf-worker-deploy` label so the issue trigger has something
  to filter on. Idempotent — existing labels are left alone.
- Updated: `cf-worker/README.md` swaps the "Deploy runbook"
  section to lead with the GHA path (one-time secret setup +
  two trigger options) and demotes the workstation `wrangler`
  flow to a fallback. The "Why not host this in CI" section
  is replaced with an "Auto-deploy on push (deferred)" note
  capturing the remaining follow-up (path-filtered push trigger).
- Updated: `ROADMAP.md` follow-up entry "Worker CI deploy"
  marks the issue-driven / dispatch deploy as done; the
  remaining `push: paths` auto-deploy is recorded as the
  narrower follow-up.

## Validation Performed
- Tests run: n/a (workflow + docs only).
- Dry-runs or staging checks: n/a — the workflow can't run
  until the operator adds the two repo secrets. The
  "Verify required secrets" step exits 1 with a clear error
  message before any wrangler call if the secrets are missing,
  so a misfire is non-destructive.
- Manual code verification:
  - Walked the workflow YAML against the
    `cloudflare/wrangler-action@v3` README; confirmed
    `apiToken`, `accountId`, `workingDirectory`, `command`
    inputs and `deployment-url` output.
  - Cross-checked the `actions/github-script@v7` env-var
    pattern against `vm-web-api-recover.yml` to keep injection
    surface minimal (no inline `${{ }}` interpolation of
    issue-author-controlled fields).
  - Verified the `if:` filter rejects `issues.opened` events
    that don't carry the `cf-worker-deploy` label, matching
    `vm-diag-snapshot.yml`'s pattern.
- Gaps not yet verified (operator-gated):
  - Live `wrangler deploy` against the operator's Cloudflare
    account (requires the two secrets).
  - The actual `*.workers.dev` URL.
  - End-to-end probe `Vercel → Worker → VM`.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none.
- Roadmap updates: ledger row added; "Worker CI deploy" item
  under consideration narrowed to the auto-deploy follow-up.
- GitHub Actions doc updates: the workflow's own header
  comment + this sprint log are the canonical reference. If a
  future audit sprint refreshes
  `docs/github-actions-workflows.md`, an entry can be appended
  there.
- Subsystem doc updates: `cf-worker/README.md` (Deploy runbook
  section).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- `cf-worker/README.md` § "Why not host this in CI" was
  written 2026-05-10 morning saying CI deploy was deferred —
  same-day operator request reversed that. Replaced the
  section in this sprint so the docs do not contradict the
  current state.

## Risks and Follow-Ups
- Remaining technical risks:
  - The API token's permissions need to match the workflow's
    needs. The README + workflow comment both pin
    `Workers Scripts:Edit` as the only required scope, but if
    the operator picks a more permissive template ("Edit
    Cloudflare Workers" includes more) the deploy still works.
    No risk to the bot itself.
  - If the operator later moves to a custom domain (named
    tunnel deferred sprint), the workflow needs a Zone
    permission added. Captured in the "Items Under
    Consideration" entry for the named-tunnel sprint.
- Remaining product decisions (Tier 3): none.
- Blockers: operator adds `CLOUDFLARE_API_TOKEN` +
  `CLOUDFLARE_ACCOUNT_ID` repo secrets. Once landed, sandbox
  opens a labelled issue and the deploy fires.

## Operator runbook (pending action)
1. Cloudflare dashboard → "My Profile" → "API Tokens" →
   "Create Token". Use the "Edit Cloudflare Workers" template,
   or a custom token with `Account → Workers Scripts → Edit`
   + `Account → Account Settings → Read`. No Zone permissions.
2. Copy the token value (only shown once).
3. Cloudflare dashboard → right sidebar → copy the **Account ID**.
4. GitHub: Repository **Settings → Secrets and variables →
   Actions → New repository secret**. Add both:
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID`
5. (Optional) merge the bootstrap-labels run so the
   `cf-worker-deploy` label exists. Auto-fires on the merge of
   this PR since the workflow file changes.
6. Tell the sandbox session "secrets are set" — the session
   opens a labelled issue, the workflow runs, the Worker URL
   lands as a comment + on the run summary.
7. The session appends the URL + verification timestamps to
   this sprint log + to `S-CFW-1`'s log.

## Deferred Items
- **Auto-deploy on push** — `push: paths: cf-worker/**` trigger
  so code edits to the Worker fire a deploy without the issue
  / dispatch step. Held until the Worker has been live for one
  healthy cycle.
- **Vercel rewrite swap** — the dashboard repos
  (`bentzbk/ict-trader-dashboard`,
  `the-lizardking/ict-trader-dashboard`) need their
  `vercel.json` updated. Tracked under the parent S-CFW-1
  sprint log.
- **Quick-tunnel teardown** — fire `teardown-cloudflare-tunnel`
  via the operator-actions workflow once the Worker has been
  serving prod for one healthy cycle.
- **Named tunnel migration** — still blocked on operator adding
  a domain to Cloudflare; unchanged from S-CFW-1's deferred
  list.

## Next Recommended Sprint
- Suggested next sprint: operator adds the two secrets per the
  runbook above; Claude opens a labelled issue, captures the
  deployed URL, and the sandbox swaps `vercel.json` on the
  dashboard repos.
- Why next: closes the GHA-deploy half of the operator's
  same-day ask + finishes the move off the ephemeral quick
  tunnel.
- Required verification before starting: the two secrets exist.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from
  summaries.
- [x] Documentation was reviewed and updated as part of the
  sprint.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
