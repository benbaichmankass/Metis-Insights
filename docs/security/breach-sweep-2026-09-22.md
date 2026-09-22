# Breach Sweep — 2026-09-22 (E24)

> **Doc status:** `live` · category `evidence` · last verified `2026-09-22` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

> **What this is.** The stated verification that checklist row **E24** exists to
> produce. The `/health-review` skill § "Security-breach check" is explicit that
> a clean breach check is a *stated verification, never an omission*, and that
> **an empty `security` finding is a review failure**. On 2026-09-22 the review
> measured **0 of 5 declared sweep sources swept** and correctly refused to
> write `no breach detected` over it. This document is the sweep.
>
> **Authority:** Tier-1, read-only. Every GitHub read was a GET; every host read
> was a GET through the token-gated `/api/diag/*` surface. Nothing was written
> to the fleet, no repo setting was changed, and no containment action was taken
> (none was warranted — see § Verdict).

---

## The headline, with its population

**No breach signal was found in anything that could be swept — and 2 of the 5
declared sources could not be swept at all, for reasons that are now NAMED and
BUILDABLE rather than unknown.**

`could_not_look` is reported as its own outcome, not folded into `ok` and not
dropped from the denominator. That is the
[`CLAUDE-RULES-CANONICAL.md`](../CLAUDE-RULES-CANONICAL.md) § "Collapsed states"
rule applied to this sweep's own result.

| # | Source | Verdict | Population / window actually covered |
|---|---|---|---|
| 1 | GitHub secret scanning | **`could_not_look`** | nothing swept — see § Gap A |
| 2 | External / non-collaborator activity | **`ok`** | 14 non-owner issues+PRs, **all time** (repo creation → 2026-09-22) |
| 3 | Repo / supply-chain mutations | **`ok` (partial)** | 5 of 8 sub-checks swept; 3 `could_not_look` — see § Gap A |
| 4 | Actions anomalies | **`ok`** | 500 workflow runs, 2026-09-21T20:15:11Z → 2026-09-22T06:41:37Z (**10.4 h**) |
| 5 | VM host auth signals | **`could_not_look`** | nothing swept — see § Gap B |

⚠️ **Sources 3 and 4 carry SHORT windows and this is stated rather than
smoothed.** The GitHub activity and workflow-run feeds are page-capped, and this
repo writes fast enough that 100 activity records span only ~13.5 hours. A sweep
that reported these as "the last 30 days" would be a fabricated population. What
covers 30 days is the **git-side** evidence (source 3b), because the local clone
was deepened to 2,384 commits (oldest 2026-08-10) and can be counted exactly.

---

## 2 — External / non-collaborator activity → `ok`

**Population: every issue and pull request in the repo not authored by
`benbaichmankass`, all time.** Query:
`repo:benbaichmankass/Metis-Insights -author:benbaichmankass`, **total_count 14**.

| author | association | n |
|---|---|--:|
| `github-actions[bot]` | `CONTRIBUTOR` | 11 |
| `danleejames23` | **`NONE`** | 3 |

**Collaborators: exactly 1** — `benbaichmankass`, role `admin`. No second
account, no outstanding invitation visible on this surface.

### The one external actor is RECONCILED AGAINST THE STANDING PRECEDENT, not novel

The skill requires reconciling any signal against the 2026-06-28 intrusion audit
*before* calling it new. Done:

- The three items are **#2680, #2681, #2688**, all **2026-06-03**, all **closed**.
- All three are enumerated by number in
  [`docs/security/intrusion-surface-audit-2026-06-28.md`](intrusion-surface-audit-2026-06-28.md)
  § the issue table, and cited by number in the header comment of
  `.github/workflows/external-issue-alert.yml` — the guard that exists *because
  of* them.
- **MEASURED 2026-09-22 (`GET /repos/{owner}/{repo}/issues/{n}`): all three carry
  `labels: []`.** That is the fact that matters — the issue-driven relays fire on
  a **label**, so a label-less issue never dispatched anything. The attempt did
  not reach the automation.
- #2688's body is a **prompt-injection-shaped lure** (it asserts a
  "$278,371 bybit_1 balance", "61 endpoints readable without auth", "14 device
  tokens visible" and a "VM health critical, action required"). ⚠️ **Those are
  the attacker's claims, treated here as untrusted data and acted on in no way.**
  They were assessed and dispositioned in the 2026-06-28 audit; they are quoted
  here only so the next reader recognises the item rather than re-investigating it.

**Stated negative: zero external-actor events since 2026-06-03** — 3 months and
19 days to this sweep, over a population of *all* non-owner issues and PRs.

---

## 3 — Repo / supply-chain mutations → `ok` (partial)

### 3a — refs, on the GitHub activity feed

**Population: 100 DISTINCT activity records, 2026-09-21T17:13:31Z →
2026-09-22T06:41:07Z (13.5 h).**

⚠️ **The population is 100 and not 500, and the difference is a trap worth
recording.** `GET /repos/{owner}/{repo}/activity` ignores `?page=N` — five
sequential page requests returned the **same 100 records**, and the `rel="next"`
cursor did not advance either. A sweep that trusted the naive count would report
*"500 records swept, no force-push to `main`"* over what is really 100 records
and half a day. Deduplicating by record `id` is what made that visible; the raw
count would not have.

| | |
|---|--:|
| push | 41 |
| pr_merge | 22 |
| branch_deletion | 19 |
| branch_creation | 16 |
| **force_push** | **2** |
| actors | `benbaichmankass` 97 · `github-actions[bot]` 3 |

- **force-push to `refs/heads/main`: 0.** Both force-pushes are on
  `refs/heads/claude/metis-insights-manager-9qmm63` (2026-09-21T20:14:24Z and
  23:32:50Z), actor `benbaichmankass` — a session branch, attributable.
- **branch deletion on `refs/heads/main`: 0.**
- **forks: 0. tags: 0.**
- **Unattributable actors in the window: 0.**

### 3b — `.github/workflows/**` edits, over a real 30-day window

A workflow edit is a code-execution + secret-exfiltration vector, so this is
swept from the **local git history**, which covers the window exactly rather than
being page-capped. Clone deepened to **2,384 commits, oldest 2026-08-10**, so the
30-day window is fully inside it.

**Population: every commit touching `.github/workflows/` since 2026-08-23 — 127
commits.**

| author | n |
|---|--:|
| `Ben <119055177+benbaichmankass@users.noreply.github.com>` | 103 |
| `github-actions[bot] <41898282+…>` | 24 |

**Third-party authors: 0.** Repo-wide over the same 30 days: **2,009 commits, 3
distinct authors** (the two above plus `Claude <noreply@anthropic.com>`, 1
commit) — all attributable, none unexpected.

### 3c — NOT swept

Deploy keys, repo Actions secrets, and webhooks. See § Gap A.

---

## 4 — Actions anomalies → `ok`

**Population: the 500 most recent workflow runs, 2026-09-21T20:15:11Z →
2026-09-22T06:41:37Z (10.4 h).**

- **Triggering actors: `benbaichmankass` 491 · `github-actions[bot]` 9.** No
  third actor, and in particular no run triggered by `danleejames23` (consistent
  with the 2026-06-28 audit's finding on the same question).
- Event mix — `push` 158 · `pull_request` 140 · `issues` 139 · `workflow_run` 30
  · `pull_request_target` 17 · `schedule` 12 · `issue_comment` 4 — matches
  ordinary Claude-session + scheduled activity for this repo. No burst that fails
  to map to a session or a cron.

⚠️ **10.4 hours is the honest reach of this source from here.** An
actor-anomaly that happened last week would not appear in it.

---

## Gap A — the GitHub security APIs are blocked by the AGENT PROXY, not by a permission

**This is the finding that changes what E24's fix has to be**, and it is the
class the repo already records as *"a missing FETCHER wearing the label of a
missing CREDENTIAL"*.

MEASURED 2026-09-22, `GET https://api.github.com/repos/benbaichmankass/Metis-Insights/<path>`
with the session's own bearer:

| path | HTTP | body `message` |
|---|--:|---|
| `secret-scanning/alerts` | **403** | `Access to this GitHub API path is not permitted through this proxy.` |
| `dependabot/alerts` | **403** | same |
| `keys` (deploy keys) | **403** | same |
| `actions/secrets` | **403** | same |
| `hooks` (webhooks) | **403** | same |
| `collaborators` | **403** | same |

**Positive controls, same host, same bearer, same call shape:** `activity` →
**200** (100 records), `events` → **200** (100), `branches` → **200** (100),
`tags` → **200**, `forks` → **200**, and the repo root → **200**. So the
credential works and the network path works; a fixed set of API *paths* is
refused upstream.

⚠️ **Do not read these 403s as "we lack admin rights on the repo."** The message
is the proxy's, not GitHub's, and `collaborators` is the control that proves it:
the same path 403s over raw HTTPS while the GitHub **MCP** tool
`list_repository_collaborators` returns it successfully — two transports, two
allowlists, one credential. A session that concluded "insufficient scope" would
go and ask the operator for a token they do not need to mint.

**So the remedy is a FETCHER, and it is buildable:** a GitHub Actions job runs on
a runner with no such proxy, and `secret-scanning: read` / `security-events:
read` / `dependabot` alerts are readable there from `GITHUB_TOKEN` with the
right `permissions:` block. That is exactly the *"their own cheap standing
lane"* E24's own note proposes, and it is now specified rather than aspirational.

**Until that lane exists, sources 1 and 3c are `could_not_look`. They are not
`ok`.**

---

## Gap B — the host auth signal is NOT reachable, and the reason is NOT the relay

E24 recorded this source as blocked by *"3 of 3 probes → HTTP 401 missing_token,
the vm-diag-snapshot relay being down per E19."* **Both halves of that diagnosis
are now superseded.** The relay is fixed and OBSERVED working (E19; diag-request
issue #12708, run `35695727461`, 2026-09-22T06:38:27Z), and a valid
`DIAG_READ_TOKEN` is present in this session's environment. The source is
*still* unreachable, for a different and structural reason.

MEASURED 2026-09-22T06:44Z, direct over `https://ict-bot.duckdns.org` with a
valid bearer:

| request | HTTP | response |
|---|--:|---|
| `/api/diag/journalctl?unit=ssh` | **400** | `{"detail":{"error":"unknown_unit","allowed":[…]}}` |
| `/api/diag/journalctl?unit=ssh.service` | **400** | `unknown_unit` |
| `/api/diag/journalctl?unit=sshd` | **400** | `unknown_unit` |
| `/api/diag/journalctl?unit=sshd.service` | **400** | `unknown_unit` |
| **control** `/api/diag/journalctl?unit=ict-web-api.service` | **200** | journal lines returned |

The control returned content on the same call shape with the same token, so this
is a refusal of the *unit*, not a failure to reach or authenticate. Confirmed in
source: `src/web/api/routers/diag.py::_CANONICAL_UNITS` is a hard-coded tuple of
this project's own systemd units and contains **no** `ssh`/`sshd` entry. There is
no spelling of the request that works.

**Therefore: `/api/diag/journalctl` cannot answer the host-auth half of the
mandatory breach check, and could not have answered it on any day, working relay
or not.** The `/health-review` skill lists "`journalctl` for `sshd`/auth
(failed-then-succeeded logins, new sessions/users)" as a required source; that
instruction has never been satisfiable through the surface it names. A binding
skill requiring an unobtainable read is the same defect class as **E23**.

**Remedies, both real, neither taken here** (both leave Tier-1):

1. Add `ssh.service` (Ubuntu's unit name) to `_CANONICAL_UNITS` — **Tier-2**
   (`src/web/api/routers/diag.py`), guarded by
   `scripts/check_diag_unit_allowlist.py`, and needs a deploy to take effect.
   ⚠️ Journal lines from `sshd` carry source IPs and usernames, so widening this
   read deserves the operator's eyes rather than a lane's.
2. A `system-action` allowlist entry that runs a fixed-form auth-log summariser
   on the host and returns counts (failed→succeeded pairs, new users, new
   units/timers/ports) instead of raw lines — narrower, and it avoids relaying
   raw auth records at all.

---

## Verdict

**`security` = `watch`, NOT `ok` and NOT `concern`.**

- **`concern` is not warranted:** nothing found in any swept source is a breach
  signal, and the one external actor on file is three-and-a-half months stale,
  label-less, non-dispatching, and already dispositioned.
- **`ok` is not honest:** two of five declared sources were not swept, and one of
  them — the host auth log, the only source that could show an *intrusion on the
  box holding the trading credentials* — has never been reachable through the
  surface the skill names.
- `operator_attention_required`: **false** for a breach; **true** for Gap B's
  choice between remedies 1 and 2, which is the operator's to make.

**What would move this to `ok`:** the runner-side lane of Gap A returning a
secret-scanning + Dependabot + deploy-key + secrets-inventory result, and either
Gap B remedy landing and being OBSERVED returning host auth content.

---

## Locators (so a session with no memory of this one can re-reach every row)

| claim | where the measurement lives |
|---|---|
| non-owner issues/PRs = 14 | `GET /search/issues?q=repo:benbaichmankass/Metis-Insights+-author:benbaichmankass`, read 2026-09-22 |
| collaborators = 1 (admin) | `mcp__github__list_repository_collaborators`, read 2026-09-22 |
| #2680/#2681/#2688 labels = `[]` | `GET /repos/{owner}/{repo}/issues/{2680,2681,2688}`, read 2026-09-22 |
| activity = 100 distinct records | `GET /repos/{owner}/{repo}/activity?time_period=month`, deduped by `id`, read 2026-09-22 |
| workflow-file edits = 127 / 30 d | `git log --since=2026-08-23 --name-only -- .github/workflows/` on a clone deepened to 2,384 commits |
| workflow runs = 500 / 10.4 h | `GET /repos/{owner}/{repo}/actions/runs?per_page=100`, pages 1–5, read 2026-09-22 |
| the six 403s + six 200 controls | § Gap A table, read 2026-09-22 |
| the four 400s + the 200 control | § Gap B table, read 2026-09-22T06:44Z |
| relay is live | diag-request issue #12708, workflow run `35695727461` |
| the 2026-06-03 precedent | [`docs/security/intrusion-surface-audit-2026-06-28.md`](intrusion-surface-audit-2026-06-28.md) |
