# PM-side session capabilities (Claude Code on the web)

> Extracted verbatim from `CLAUDE.md` on 2026-09-21 by the operating reset.
> Reference material — read on demand, not at session start.
> Registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md).


What the sandbox session can and can't do directly. Future sessions
should not re-derive this — if the contract changes, edit here.

**MCP tools available** — `mcp__github__*` (subset: issue
read/write, PR read/write/merge, file read/create/update, branch
create, secret scanning, **but no `create_label`, no artifact download;
`actions_list` / `get_job_logs` DO work — run-log read is available
since the 2026-06 MCP update. **`run_workflow` NOW WORKS** —
re-verified 2026-08-06 19:45Z (`actions_run_trigger method=run_workflow`
on `branch-protection-sync.yml` ref `main` → HTTP 204, run queued). It
403'd when checked 2026-06-11; the 2026-08 MCP added it. This is
load-bearing: `workflow_dispatch` is how a session updates branch
protection on a ref other than `main`, which is the only way to land a
change that alters the required-check set without deadlocking every open
PR. `cancel_workflow_run` was observed returning **502** during the
2026-08-06 Actions incident — that was the incident, not a permission
boundary; re-test before concluding it is unavailable**), Google Drive (file search
+ read), Hugging Face (hub search, doc fetch), Bigdata.com (market
data), Gmail (read-only labels).

**The hosted GitHub MCP drops intermittently — DO NOT treat it as an
expired token.** In long-running sessions the `mcp__github__*` server
disconnects and reconnects repeatedly (a single 2026-05-29 session saw
~6 cycles). A call that lands during a drop fails with
`MCP server "github" requires re-authorization (token expired)` — but
this is a **transient, self-healing blip, not a real OAuth expiry**: a
cheap retry (e.g. `get_me`) succeeds seconds later, as verified that
session. **Correct handling:** on that error, wait a few seconds and
retry with backoff (2s/4s/8s/16s) — `ToolSearch "select:mcp__github__get_me"`
then `get_me` is a good liveness probe. Only escalate to the operator
after the failures persist for **several minutes across multiple
retries**. **Never ask the operator to "re-authorize GitHub" on the
first hit** — they cannot trigger an in-session reauth on Claude Code
on the web, and 16h-long monitoring loops are exactly what surface
these drops, so a premature hand-off strands the task on a false alarm.
The underlying connector stability is Anthropic-hosted (not fixable
from this repo); the durable workaround for a VM-data task that must not
depend on GitHub is the **direct diag path** (`DIAG_BASE_URL` +
`DIAG_READ_TOKEN` + `scripts/ops/diag_fetch.sh`). ⚠️ **THE HOSTNAME DECIDES,
NOT THE NETWORK-ACCESS LEVEL — and the relay is NOT the only channel**
(corrected 2026-08-20; the claim below had been half wrong since the Caddy
cutover, and it cost every web session a relay round-trip it did not need).
**Measured from a default-`Trusted` web session, both arms in one go:**

| target | result |
|---|---|
| `http://141.145.193.91:8001/api/health` (raw IP) | **000** — firewalled, as documented |
| `https://ict-bot.duckdns.org/api/health` (Caddy) | **200** `{"ok":true}` |
| `https://ict-bot.duckdns.org/api/diag/version` + bearer | **200** `{"git_sha":"e4c274af",…}` |

So the **raw-IP** half of the old claim is right and the **"egress to the VM is
firewalled, the relay is the only channel"** half is wrong: the Caddy HTTPS
hostname (the same one the Svelte SPA uses) is allowlisted like any other HTTPS
host, and **credentialed `/api/diag/*` works over it at Trusted**. Try the
hostname before falling back to the issue relay. ⚠️ **But check `DIAG_BASE_URL`
before trusting it** — as of 2026-08-20 the cloud environment still ships
`http://158.178.210.252:8001`, the x86 micro **terminated 2026-06-16**, so the
canned var points at a dead host over the one scheme that cannot work
(`BL-20260818-DIAG-BASE-URL-POINTS-AT-TERMINATED-VM`, re-confirmed live two days
after filing). ✅ **`scripts/ops/diag_fetch.sh` now handles that for you** — it
tries an ORDERED list of candidate bases and puts the canonical HTTPS one FIRST
whenever the configured value is plain-http or names a known VM IP, so a stale
env var no longer strands the direct path; it prints `served by <base>` on
stderr so you can see which one answered. **Do not describe this as an
operator-only problem** — that claim was made in the backlog and was wrong: the
var is consumed by a repo file, so the repo decides what to do with a bad value.
It previously "self-healed" the retired micro to the **raw live IP**, which the
proxy drops — measured 2026-08-20 as `curl (28)` timeout then exit 3, i.e. a
heal that reported success and produced an unreachable host. Fixed + verified
in-session with the stale env still set (`exit 0`, real JSON, served by the
Caddy host); regression-tested in `tests/test_diag_fetch_sh.py`.

**The GitHub REST API is NOT reachable by `curl` — use the MCP (2026-07-30).**
`*.github.com` being nominally allowlisted does **not** mean
`https://api.github.com/...` works: the sandbox intercepts it and returns
**HTTP 403** with a Claude-specific body (`"GitHub access is not enabled for
this session. An org admin must connect the Claude GitHub App…"`). This bites
hardest in a **poll loop**, because the natural defensive idiom hides it: a
`curl … || echo '{}'` fallback turns the 403 into an empty result, `len(check_runs)`
reads `0`, and a CI watcher then spins its full timeout and reports
**`TIMEOUT`** — a red that checked nothing, which
`docs/CLAUDE-RULES-CANONICAL.md` § "Green is not evidence" names as the same
sin as a green that checked nothing, and worse for trust. A session hit exactly
this watching its own PR on 2026-07-30. **Poll CI/PR state through
`mcp__github__pull_request_read` (`get_check_runs`), never `curl`** — and if you
do write a shell poller against any API, assert a plausible non-zero denominator
(`total_count > 0`) before believing a "nothing pending" answer.

⚠️ **`total_count: 0` on a PR usually means MERGE CONFLICT, not "CI hasn't
started yet" — read `mergeable_state` FIRST** (`pull_request_read` method
`get`). GitHub builds `pull_request`-event runs against the **merge ref**; when
that ref cannot be built (`mergeable_state: "dirty"`) the workflows are silently
skipped, and zero check runs renders identically to *queued* and to *all green*.
Push events do not cover you either — every CI workflow here is `pull_request` +
`push: branches: [main]`, so a branch push fires nothing. The fix is the merge,
and CI fires within seconds of the push that resolves it. **This was already
documented and it has still cost two sessions ~10 minutes each**
(`BL-20260720-GH-ACTIONS-PUSH-EVENTS-DEAD` on 2026-07-20 →
`BL-20260830-ZERO-CHECK-RUNS-READ-AS-CI-NOT-STARTED-NOT-AS-MERGE-CONFLICT` on
2026-08-30), because the lesson lived only in a 1025-row backlog nobody reads
mid-task — which is why it is restated here, at the point of use. The fastest
disproof of an "Actions outage" is another branch's runs in `actions_list`, but
check `mergeable_state` before you even reach for that.

**Network from inside the session** — governed by the cloud
environment's **Network access** level (None / Trusted / Full /
Custom). At the default **Trusted** level outbound is allowlisted to
package registries + `*.github.com` / `*.anthropic.com` etc., and
arbitrary IPs (incl. the Oracle VM) are firewalled —
`dangerouslyDisableSandbox: true` does **not** help, the egress
restriction is enforced one layer below the Bash sandbox. To reach the
live VM's diag API directly, the environment must be set to **Full**
(or **Custom** allowlisting the host) AND carry the `DIAG_BASE_URL` +
`DIAG_READ_TOKEN` env vars — see "Reaching `/api/diag/*`" above. Note
the security proxy is HTTP/HTTPS-only even at Full, so SSH/raw-TCP to
the VMs never works from a web session. ⚠️ **A raw `http://IP:port` is not
"may still be dropped" — it IS dropped** (measured 2026-08-20: rc/http `000`
against `141.145.193.91:8001` at Trusted), **while the Caddy HTTPS hostname
works at Trusted with no Full/Custom change at all** (`200` on both
`/api/health` and a bearer'd `/api/diag/version`). The **scheme + hostname** is
what the proxy allowlists, not the destination host's identity — so "must be set
to Full to reach the live VM's diag API" is true only of the raw-IP route.
Point `DIAG_BASE_URL` at `https://ict-bot.duckdns.org` and the direct path works
from an ordinary session.
Network-access changes take effect on a **new** session, not the
running one.

**No custom MCP servers.** Claude Code on the web doesn't honour
project `.mcp.json` and can't run `claude mcp add`. To get richer
GitHub powers (workflow_dispatch, run artifacts, label CRUD), the
operator has to either (a) wait for Anthropic to expand the hosted
GitHub MCP, or (b) move the ops session to Claude Code desktop / CLI
and install `github/github-mcp-server`. Until then, the workarounds
below are the contract.

**Workarounds shipped:**

- **VM diag access (read-only)** — issue-driven, see § "Reaching
  `/api/diag/*` from a PM-side / web-sandbox session" above and the
  full doc at `docs/claude/diag-relay.md`.
- **VM operator actions (narrow mutating)** —
  `.github/workflows/system-actions.yml` exposes a fixed
  allowlist (`status-check`, `pull-latest-logs`, `pull-and-deploy`,
  `restart-bot-service`, `reboot-vm`, `set-account-mode`, …).
  Tier-1 actions are autonomous; Tier-2 actions require an operator
  ack first (in-conversation approval is sufficient). Two dispatch
  paths, identical allowlist + audit:
  - `workflow_dispatch` — operator clicks "Run workflow" in the
    Actions UI.
  - **Issue-driven** — open a labelled issue (`system-action`)
    with body `action: <name>\nreason: <text>` (plus `account:` +
    `mode:` lines for `set-account-mode`). Workflow runs, comments
    back, closes the issue. Body parsing rides through env
    (`ISSUE_BODY`), not inline interpolation.

  Full contract: `docs/claude/system-actions.md`. **Account-mode
  flips have one sanctioned wire (`set-account-mode`); strategy
  parameter changes, risk caps, and live order code remain Tier-3
  PRs.**
- **Web-API self-heal (autonomous, single-purpose)** —
  `.github/workflows/vm-web-api-recover.yml` is the issue-driven
  recovery path for `ict-web-api.service`. When the diag relay
  starts returning curl exit 7 (`Failed to connect to 127.0.0.1`),
  the FastAPI process serving `/api/diag/*` is down and Claude is
  blinded. Open a labelled issue (`vm-web-api-recover`) to fire a
  fixed-form `systemctl restart ict-web-api.service` + health
  probe; the workflow comments back and closes. Restart-only, no
  edits, no other unit touched. Wrapper:
  `scripts/ops/restart_web_api.sh`.
- **Live-VM git-fetch credential (the repo-went-private fix)** —
  **Current visibility (2026-07-07): the repo is PUBLIC.** It was flipped
  back from private by operator choice to keep the free unlimited GitHub
  Actions budget; the guard against external abuse is the repo interaction
  limit **"Limit to repository collaborators"** (only the owner/collaborators
  — of which there are none besides the owner — can comment / open issues or
  PRs) plus the `external-comment-alert.yml` workflow (auto-hides + alerts on
  any external comment), NOT privacy. The git credential below stays in place
  and is harmless on a public repo (anonymous fetch would also work again).
  The incident record follows as history:
  the repo flipped from public to private 2026-07-06
  (`BL-20260706-GITSYNC-AUTH-BROKEN`); `ict-git-sync.timer`'s
  `git fetch` on the live VM had always been anonymous and stopped
  authenticating. The credential is a **single global git config
  value** (`http.https://github.com/.extraheader`, a Basic-auth
  header built from a fine-grained Contents:Read-only PAT stored as
  the `VM_GIT_DEPLOY_TOKEN` Actions secret) set ONCE by the
  one-shot `.github/workflows/vm-git-credential-bootstrap.yml`
  (label `vm-git-credential-bootstrap`) — it runs on a GitHub-hosted
  runner (auto-authenticated to the private repo, no VM-side git
  state involved) and SSHes the credential onto the VM directly,
  breaking the chicken-and-egg where the fix can't reach the VM via
  the mechanism it fixes. **Deliberately not per-invocation** —
  `http.extraheader` is a multi-valued git config key, so an
  earlier version that ALSO attached the header per-fetch call
  caused git to send it twice, which GitHub rejects outright
  (`remote: Duplicate header: Authorization`, 400). One source of
  truth only. Re-provisioning a fresh VM needs this workflow re-run
  once (a fresh home dir has no global git config). **Recurrence
  (2026-07-06, same day):** even after the duplicate-header fix
  landed on `main`, `pull-and-deploy` failed the same way again —
  the on-disk `deploy_pull_restart.sh` was itself still the old,
  broken pre-fix copy, so its own fetch could never pull the fix
  that would repair it (the same deadlock one level deeper). Fixed
  by extending the bootstrap workflow: after its verification fetch,
  if the worktree reads behind `origin/main` it now `git reset --hard
  origin/main` + directly invokes `bash scripts/deploy_pull_restart.sh`
  itself, landing the fix in one shot instead of waiting on the
  broken script to pull it. **BL-20260706-GITSYNC-AUTH-BROKEN is
  resolved and live-verified** — `git_sha` confirmed current via
  `/api/diag/version` after a real restart-bot-service cycle.
  **Trainer target (2026-07-06, BL-20260706-TRAINER-GIT-AUTH-BROKEN):**
  the trainer VM's anonymous `git pull` broke identically, so the
  workflow takes a `target` (live default | trainer — via the
  `workflow_dispatch` input or a `target: trainer` issue-body line).
  The trainer branch sets the same single credential on
  `158.178.209.121` and recovers with a plain
  `git reset --hard origin/main` (no deploy script / service restart
  on the trainer).
- **Prop report-back POST (the diag relay's write counterpart)** —
  `.github/workflows/prop-report.yml` is the issue-driven path to
  `POST /api/bot/prop/report` (the read-only `vm-diag-snapshot` relay
  is GET-only and can't POST). Open a labelled issue (`prop-report`)
  whose **body** is a single JSON object (optionally inside a ```json
  fence — stripped) in one of the two `src/prop/prop_report.py`
  shapes (fill/close, or `kind:"account_status"`); the workflow
  validates it (`jq -e 'type=="object"'`), POSTs it to the VM over
  SSH + curl, and comments the endpoint's JSON response + HTTP status
  back before closing. **Tier 2** (DB write + notification); it
  sources `DASHBOARD_API_TOKEN` from `/etc/ict-trader/web-api.env`
  **on the VM** and sends the bearer header only when set (never
  reaches the runner / run log). The untrusted body rides a base64
  hop, never inline-interpolated. Full flow:
  `docs/claude/diag-relay.md` § "Posting a prop report-back".
- **Repo label creation** — `.github/workflows/bootstrap-labels.yml`
  self-creates the labels other workflows filter on. Edit the
  `LABELS` array in that file and merge; the next push runs the
  sync. No `create_label` MCP needed.
- **PR open + auto-merge when the MCP is read-only (403)** —
  `.github/workflows/claude-pr-automerge.yml` is the durable path for a
  PM-side session whose GitHub MCP integration 403s on PR create/merge
  ("Resource not accessible by integration"). `git push` works and the
  workflow's own `GITHUB_TOKEN` has write perms, so on a push to any
  `claude/**` branch that touches `.github/pr-automerge-requests/<branch-slug>.txt` it
  finds-or-opens the branch's PR to `main` (title = head-commit subject)
  and enables native auto-merge (squash) — GitHub still merges only on
  green required checks (branch-protection is the safety net; CI is never
  bypassed). The branch no longer needs to be up to date with `main`:
  `require-up-to-date` was unticked 2026-08-10 (`strict: false`) because it
  forced a ~9-minute CI re-run on every PR that went `behind` without
  serializing anything. With a bounded poll-then-merge
  fallback if the repo disallows auto-merge. Generalized 2026-07-27 from
  the one-off `m28-value-grade-push`/`m28-merge-push` workflows. **Only
  needed when the MCP is 403** — the normal path is `merge_pull_request` /
  `enable_pr_auto_merge` via the MCP under the merge protocol.
- **The OTHER TWO relays for that same 403 — `pr-opener` and `board-post`.**
  ⚠️ **This list named only `claude-pr-automerge` until 2026-09-01, and the
  omission had a measured cost**: the strings `pr-opener` and `board-post`
  appeared **zero times** in this file, `docs/claude/coordination-board.md`
  (the board's own body of record), `docs/CLAUDE-RULES-CANONICAL.md` and the
  `session-coordination` skill — measured with a positive control
  (`claude-pr-automerge` appears 3× here). A session hit the 403 on
  2026-09-01, read these docs, correctly concluded no board path existed, and
  found both relays only by reading `.github/workflows/` after every documented
  path had failed. **A capability that is built but unreachable from the surface
  its user reads is, for that user, identical to no capability at all**
  (`BL-20260901-COORDINATION-BOARD-WRITES-403-FROM-THIS-SESSION-WHILE-READS-SUCCEED`).
  - **`.github/workflows/pr-opener.yml`** — OPEN a PR with a full title and
    body: drop `automation/pr-requests/<name>.json`
    (`{head, base, title, body, draft}`) and push it. The URL comes back at
    `automation/pr-results/<name>.txt`. **Use a fresh filename per PR** — the
    result file is the idempotency key, so reusing a name is a silent no-op.
    ⚠️ Its results commit is pushed by `github-actions[bot]`, and GitHub does
    not trigger workflows for `GITHUB_TOKEN` pushes, so when that commit lands
    last the PR shows **zero checks** — blocked, not green. Push one ordinary
    commit yourself to arm CI.
    ⚠️ **THIS APPLIES TO `board-post.yml` TOO, and `pr-opener.yml`'s header does
    not say so** — it documents the trap only for itself. Both relays commit a
    result file back the same way, so **every board post you make on an open
    PR's branch re-buries that PR's checks**, and the more diligently you use
    the board the more often it happens. Measured on PR #10680 (2026-09-01): it
    hit twice in one PR, once per relay. Read `mergeable_state` to tell the two
    zero-check causes apart — `blocked` is this (no checks fired), `dirty` is a
    merge conflict, and both render as `total_count: 0`.
    ⚠️ **AND IT APPLIES TO `claude-pr-automerge.yml`, BY A DIFFERENT ROUTE THAT
    IS WORSE — measured THREE times on 2026-09-08 alone** (#11356 MI-193,
    #11392 and #11395 MI-199). The two relays above bury an EXISTING PR's checks
    with a results commit; `claude-pr-automerge` **opens the PR itself** under
    `GITHUB_TOKEN`, so the PR is *born* with no `pull_request` event and carries
    exactly ONE check run — its own `open-and-automerge`. It therefore reads as
    a PR whose CI has not started yet and never will, and because that one run
    is GREEN it is easy to glance at and call ready. **Push one ordinary commit
    to the branch after the workflow opens the PR.** ⚠️ **A SECOND HAZARD ON THE
    SAME ROUTE, and it is the one that loses work: ONCE AUTO-MERGE IS ARMED,
    TREAT THE BRANCH AS CLOSED TO NEW CONTENT.** Measured on #11392 — a commit
    pushed to the branch while CI ran on the previous head was NOT in the squash:
    auto-merge took the PR the moment that earlier head went green, and the later
    commit, though pushed before the merge, never reached `main`. Whether a
    post-arming commit lands is a race with CI duration, so anything that must
    land goes on a fresh branch (a merged PR is finished and must not be reused).
    ✅ **PREVENT IT INSTEAD OF WORKING AROUND IT — OPEN THE PR YOURSELF FIRST,
    THEN PUSH THE ARMING FILE.** Verified in the workflow source, not assumed:
    step 1 does `pulls.list({owner, repo, head, state:'open'})` and calls
    `pulls.create` **only when that returns nothing**, so a PR you opened via
    `mcp__github__create_pull_request` is ADOPTED — the workflow just enables
    auto-merge on it. Because you opened it, it carries a real `pull_request`
    event and CI fires normally; there is no zero-check window and no second
    commit to invent. The two-push order is: (1) push the content and open the
    PR yourself, (2) push `.github/pr-automerge-requests/<slug>.txt` plus the
    merge-slot claim R13 wants. ⚠️ **R13 TAKES EITHER OF TWO CLAIMS, AND ON A
    LANE BRANCH YOU WANT THE SECOND ONE.** The original is the single
    `docs/claude/session-board.json::merge_slot` field, which still works and
    which `commit-to-main` and its 27 workflows still write. But it is ONE field
    every armed branch must overwrite, so every armed branch conflicts with
    `main` the moment anything else claims it: measured over the last 40 commits
    touching that file, **39 of 40 moved `merge_slot.branch`**, median gap 11.5
    minutes and 26 of 38 gaps under 16, and resolving the conflict pushes a new
    head that RESTARTS CI — so resolving faster does not help. Observed with a
    control the same morning: all four ARMED PRs of one session went `dirty`
    together while the only two that stayed CLEAN were the two declaring
    `landing: "hold"`, which write no claim at all. Use the per-branch claim
    instead — `python3 scripts/ops/claim_merge_slot.py --branch-claim --branch
    <branch> --held-by <session>` writes `.github/merge-slots/<slug>.json`,
    which no other branch can contend for. Nothing is given up: R13's own
    docstring says the claim does NOT serialize, so the shared field bought a
    guaranteed conflict and no exclusion, and attribution is preserved (the
    branch is named in the path AND in the body, and both must agree). See
    `.github/merge-slots/README.md`. ⚠️ `pr-landing-guard` R6/R13 are satisfied by
    the SECOND push, so do not expect the guard to pass on the first — that is
    the expected intermediate state, not a failure. This is worth the extra
    step: the trap hit **six times on 2026-09-08 alone** (#11356, #11392,
    #11395, #11407, #11419, #11424), and on a Tier-1 self-landing PR the
    "invent one more commit" workaround pressures you into padding a diff,
    which is how an unrelated change ends up riding a landing PR.
  - **`.github/workflows/board-post.yml`** — POST to the coordination board
    (it resolves the issue from `docs/claude/board-pointer.json` **on the default
    branch**, so your own branch cannot redirect it) when `add_issue_comment` 403s: drop
    `automation/board-posts/<name>.md`, whose entire contents become the
    comment, and push it on a `claude/**` branch; read
    `automation/board-results/<name>.txt` back. An empty body is **refused**
    and a failed post **fails the run**, deliberately louder than `pr-opener`
    — a session that believes it claimed the board and did not is invisible to
    every other session and to itself. **So a 403 is never a reason to skip the
    board.**
  - ⚠️ **Distinguish this 403 from the transient drop documented above.** A
    write-scope boundary returns `403 Resource not accessible by integration`
    on writes while `issue_read` on the *same* object succeeds; retrying with
    backoff will not clear it, and neither will `gh` (absent) or `curl` to
    `api.github.com` (403 at the proxy). Reach for a relay, not a retry loop.
- **Broker-credential propagation (Actions → VM)** —
  `.github/workflows/sync-vm-secrets.yml` is the canonical path for
  mirroring broker-credential Actions secrets to the live trader's
  `.env` (added 2026-06-02). One workflow declares the full known
  set (`REQUIRED_SECRETS` + `OPTIONAL_SECRETS`); adding a new broker
  appends env-var names there. Idempotent — re-running with no
  change is a no-op. Values ride through SSH `SendEnv` and never
  reach run logs. Replaces the per-broker workflow pattern the
  earlier Bybit-only `rotate-account-keys.yml` followed; that
  workflow stays in place as the legacy Bybit path pending a
  separate migration PR.
- **Actions-secret placeholder pre-creation** —
  `.github/workflows/init-actions-secrets.yml` creates empty
  placeholder repo Actions secrets so the operator pastes values
  into pre-existing slots (Settings → Secrets → Update) instead of
  clicking "New repository secret" N times. Idempotent — already-set
  names are skipped, never overwritten. Used by Claude as the first
  step on a new-broker hookup ping. Dispatchable via
  `workflow_dispatch` (UI / Actions API) or via issue label
  `init-actions-secrets` (Claude-driven; PR #2652).
- **Trainer VM full visibility** — `.github/workflows/trainer-vm-diag.yml`
  is the unrestricted SSH relay for the trainer VM. Claude opens a
  `trainer-vm-diag-request`-labelled issue with a `cmd:` block
  (any bash) and the output comes back as an issue comment. No
  operator approval needed — trainer VM is autonomous territory.
  See `docs/claude/trainer-vm-mode.md` § 9 for usage and the
  complete list of what Claude pulls routinely.
- **Workflow dispatch** — there's no general-purpose workaround.
  Workflows that need to be Claude-driven from a session must use
  an `issues.opened` (or `pull_request.opened`) trigger filtered to
  a label. Pattern is the diag relay (`vm-diag-snapshot.yml`),
  `vm-web-api-recover.yml`, `init-actions-secrets.yml`,
  `purge-artifacts.yml` (label `purge-artifacts-now`), and now
  `system-actions.yml` (whose Tier-2 ack is the operator's
  in-conversation approval — Claude carries that approval into the
  issue body).
- **Alpaca account lookups (read-only)** — if the operator has connected
  the official [Alpaca MCP server](https://docs.alpaca.markets/us/docs/alpaca-mcp-server)
  to a session, it gives fast direct account/portfolio/market-data reads
  (buying power, positions, margin status) without a diag-relay round trip.
  **Its trading tools must never be used from a session touching this
  repo** — they place orders directly against Alpaca's API, bypassing
  `RiskManager.position_size()` (the repo's one sanctioned order path) and
  the journal, which would surface as an un-audited phantom orphan. The
  operator scopes this with `ALPACA_TOOLSETS` to exclude the trading
  category; full writeup, the risk, and the setup contract:
  `docs/claude/alpaca-mcp-server.md`.

