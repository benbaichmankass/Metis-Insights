# Operating Layer — Work-Object Schema, State Home, and Access Posture

> **Status: DECIDED (operator, 2026-09-01).** Companion to
> [`operating-model-DESIGN.md`](./operating-model-DESIGN.md), which settles the
> structure and the 24 functions. This document settles the pass that one names as
> its next stage: **what a work object contains, where coordination state lives,
> how a session writes to it, and who may read it.**
>
> Still no build order. What gets built follows from the function-by-function
> derivation, which has not happened yet.

## Why these four questions are one pass

What a work object holds determines what the store must hold. Where the store lives
determines what a session and the dashboard can each do with it. How a session
authenticates determines whether a live store is reachable at all. And who may read
it determines whether the store can be the same public API everything else uses.
Answering any one of them alone produces an answer the other three break.

---

## 1 · The work-object model

**Three levels.** INTENT → WORK OBJECT → STEP.

| Level | What it is | Cardinality |
|---|---|---|
| **INTENT** | A direction committed to, expressible before it decomposes ("we are going after X"). Hand-written, hard-capped, reconciled each cycle. | Few; short enough to read in full |
| **WORK OBJECT** | A **question** we want answered or a **commitment** we have made. Carries the done-condition, the verdict or the decision, and the evidence. | **The WIP ceiling of 8 counts these, in flight** |
| **STEP** | The smallest assignable thing: one session's worth of work. Cannot exist without a parent work object. | Unbounded — session-sized and short-lived |

### Fields

**INTENT** — `id` · `title` · `why` · `status` (live / dormant / retired) · `opened_at` · `review_cadence`.

**WORK OBJECT** — `id` · `type` (question | commitment) · `parent_intent` · `title` ·
`stage` (QUESTION | EVIDENCE | DECISION | DEPLOYMENT | OBSERVATION | CAPABILITY | INTEGRITY) ·
`done_condition` (the observable that ends it) · `lifecycle` · `blocked_on[]` ·
`owner` · `evidence[]` · `verdict` (questions) · `decision` + `basis` (commitments) ·
`opened_at` / `closed_at` · `review_trigger`.

**STEP** — `id` · `parent` (required) · `title` · `entry_state` · `exit_state` ·
`successor` (what becomes possible, and who acts next) · `urgency` (routine | blocking) ·
`lease` · `status` · `progress[]` · `session_ref`.

### Three rules that keep the model honest

**`lifecycle` is never collapsed.** `dormant` · `ready` · `in_flight` · `waiting` ·
`done` · `accepted`. *Not started*, *blocked*, *being worked* and *waiting on the
operator* are four different facts, and a design that renders them identically is the
collapsed-state defect this repo already has a CI guard for.

**`blocked_on` is a typed edge, not a flag.** Each entry is
`{kind, ref, since}` where `kind` ∈ `object` · `operator_decision` · `external_event` ·
`data_accrual` · `capability`. This is what lets the constraint be **computed** rather
than judged — where the system is held up falls out of the graph, which is the input the
operator's cycle decision depends on.

**A verdict states its population.** Any quantitative claim in a `verdict` or `basis`
carries its population and its basis (MEASURED / INFERRED / DECIDED), so the existing
`stated-population-guard` and `claim-basis-guard` apply to work objects with no new
machinery.

---

## 2 · Where state lives — one source of truth

**The repo is the single source of truth. The live layer owns no truth at rest.**

The axis is not durable-versus-volatile storage — that framing is what made this look
like a forced trade between the reaper and the audit trail. The axis is **truth versus
observation**. A heartbeat is not a fact about the work; it is telemetry about a session,
and losing every heartbeat ever emitted loses nothing anyone would want back.

**The invariant — the wipe test.** *Deleting the entire live layer and rebuilding it from
the repo must lose nothing anyone would want back.* Anything that fails this test is
truth, and belongs in the repo.

| | Where | Wipe test |
|---|---|---|
| A step's **definition** — parent, entry/exit state, successor | Truth → repo | Clean |
| A step's **"alive, currently doing X"** | Observation → live | Clean |
| **Lease / heartbeat** | Observation → live | Clean — sessions re-register on the next beat |
| **Pending decision** — the question | Truth → repo; *pending* is DERIVED from an unanswered `operator_decision` edge | Clean — the inbox rebuilds by scanning |
| **Constraint readout, blocker graph** | Derived | Clean — recompute |
| **Dashboard view state** (filters, scroll) | Browser-local, neither store | Clean |
| **A submitted answer, pre-commit** | **Truth in transit** | ⚠️ Not clean |
| **A queued notification, pre-send** | **Truth in transit** | ⚠️ Not clean |

So the accurate invariant is not *"the live layer owns nothing"* — it is: **the live layer
holds observations and truth-in-transit; it never holds truth at rest.**

**This resolves the reaper and the audit trail together, without a second source.** The
reaper works because heartbeats sit in the non-truth layer where a per-few-minutes signal
costs nothing; when a lease expires, the *recovery* — what the dead session actually
accomplished — is written to the repo as a commit, so the truth-bearing act remains
auditable. Nothing durable ever exists in two places.

### The transit contract

Truth in transit is accepted **on condition that every window is accountable**: a failure
is identifiable, and each window is verifiably closed before anything moves on.

**Three states, never collapsed:** `not_submitted` · `in_transit` · `committed`.

**Transit fails BACK, never forward.** An answer that does not commit leaves its question
**unanswered** — never "answered", never ambiguous. The safe direction on a lost write is
the un-transacted state, because a question wrongly shown as answered is a decision nobody
made.

**Open windows are enumerable and close observably.** The set of in-transit items is
listable at any moment; a window older than its bound is a reportable condition, not a
silent one; and the dashboard renders a submitted-but-uncommitted answer as *not landed*
rather than as done. This is the same three-state discipline `collapsed-state-guard`
already enforces elsewhere in the system, applied to writes.

`pending-pings.jsonl` already works this way, so the pattern is established rather than new.

### What repo-as-truth costs, stated up front

**Concurrency → one file per object.** Multiple sessions committing to a shared JSON file
is a measured pain here: `backlog_append.py` exists because naive read-append-write
reformats the file and buries a one-row change in a 47,000-line diff. One file per object
means two sessions touching different work never conflict. `research/queue/*.yaml` — one
YAML per job — is the existing precedent.

**Freshness → the dashboard reads a projection.** Truth is in the repo, so the dashboard
sees it through a push-triggered projection and is stale by however long that takes
(seconds). Acceptable for work state. It would not be acceptable for market data — which
is not truth this system owns, and is already read live and separately.

**A session produces to the repo, not to the live layer.** Anything a session *makes*
commits as it is made; only *"I am alive and currently doing X"* stays live. Without this
rule a session could work for an hour, narrate only to the live layer, and lose the record
on a wipe — which is the F2 close-out failure the operating model exists to prevent.

## 3 · How a session writes

**A scoped write token, originated by the operator.** Measured 2026-09-01 from inside a
session: `DIAG_READ_TOKEN` is present and `/api/diag/version` returns 200 over the Caddy
host, but **`DASHBOARD_API_TOKEN` is unset**, so token-gated writes are closed to a
session today.

The token is **scoped to the operating-layer endpoints only** — deliberately not
`DASHBOARD_API_TOKEN`, which also authorises prop-journal writes a session has no reason
to reach. Rejected alternatives: a GitHub Action relay (a workflow round-trip per
heartbeat is the wrong shape for the one write that must be cheap), and following the
unauthenticated `learning/progress` precedent (it would leave the work registry writable
by anyone on the internet).

Originating the value is one of the three genuine operator hand-offs under the autonomy
contract. Propagation to Actions secrets and the VM is not.

---

## 4 · Access posture

**Decided: gate reads behind a token; one frontend; archive the other two.**

- **The Svelte SPA on GitHub Pages is the only live consumer.** The **Android app** and
  the **Streamlit dashboard** are pulled off the live feed and archived.
- **The API's read surface is gated**, not only its writes.
- **Operating state rides the same API as everything else**, now that the API is gated.

**The mechanism already exists and is almost entirely unused.** `require_session`
(`src/web/api/auth.py`) is genuinely default-deny — 401 on missing/malformed/expired/bad
signature/`alg=none`, 403 on a de-allowlisted email, and **500 `auth_unavailable` when
the server's own auth env is missing, so a dropped secret fails closed rather than
opening the routes**. `POST /api/auth/login` issues the bearer. `PUBLIC_ROUTES`
(`auth.py:44`) is the opt-out list, and the test suite asserts the contract.

**Exactly two routes attach it today: `/api/pnl` and `/api/status`.** Everything else is
ungated. So the work is attaching an existing, enforced, tested gate to the routes that
never got it, keeping `/api/health` and `/api/auth/login` public, and giving the SPA a
login. Archiving the other two consumers is what makes that tractable — there is nothing
else left to keep working.

**One scope, everything.** Any authenticated caller reads the whole surface; there is no
per-actor partition of the data. Simplicity was chosen deliberately over least privilege
here, and the consequence is explicit: a compromised session token reaches everything the
gate protects. The least-privilege control that *is* adopted sits at the data layer
instead — see the explorer allowlist below.

**The DB explorer is narrowed to a table allowlist plus column redaction.** A route gate is
authentication, not authorization: one token would otherwise open all 22 tables. A generic
any-table reader is how `device_tokens` became exposed with nobody deciding it should be —
so tables are reachable only when explicitly listed, and credential-shaped columns are
never emitted. **This inverts the failure mode: a newly added table is invisible until
someone deliberately admits it**, rather than exposed until someone notices. Read
connections are already `mode=ro`, so writes were never the gap; reads were.

⚠️ **A static site cannot hold a secret.** The SPA is served from GitHub Pages, so
nothing may be baked into the bundle; the operator logs in and the browser holds a
short-lived session token. That is exactly what `/api/auth/login` was built for and what
no consumer has ever called.

---

## 5 · What the pass found on the way

**The public read surface is broad, and grew route by route rather than by decision.**
Measured 2026-09-01 with **no credentials at all** against `https://ict-bot.duckdns.org`:
`/api/bot/stats`, `/api/bot/config`, `/api/bot/positions`, `/api/bot/trades/closed`,
`/api/bot/accounts/balances`, `/api/bot/order-packages`, `/api/bot/prop/fills`,
`/api/bot/logs` and `/api/bot/db/tables` all return **200**. The generic explorer reaches
**22 tables**, among them `signals` (2,273,237 rows — the complete strategy decision
stream), `balance_snapshots` (29,716), `trades` (5,288) and `order_packages` (4,254).
The documented gates *do* hold: `/api/bot/devices`, `/api/diag/*`, `/api/pnl` and
`/api/status` all return **401**.

**`device_tokens` is reachable through the ungated explorer, unredacted.** Its columns
are `id, token, platform, label, subscriptions, created_at, last_seen_at` — the raw
`token`, anonymously. The dedicated `/api/bot/devices` route is token-gated **and**
deliberately returns only `token_suffix`, which is what establishes that public exposure
was never the intent: **two paths to one asset, one gated and one not.**
`db_explorer.py`'s own docstring asserts the premise this violates — *"No secrets live in
either DB"* — the repo's own *field beats comment* pattern.

Proportion, stated rather than implied: the table holds **0 rows**, so nothing is leaking
now; it becomes live on first device registration, which is the Android app's only write
to the bot; and an FCM registration token is a device identifier rather than a directly
usable credential, since sending push additionally requires the FCM server key, which is
not exposed here. **The archiving decision in § 4 removes the producer**, which resolves
the live risk — but the ungated path through the explorer remains, and that is the part
worth fixing rather than the symptom.

**Minor:** `require_session`'s docstring places `PUBLIC_ROUTES` in `main.py`; it is in
`auth.py:44`.

---

## 6 · Not settled here

The function-by-function derivation (what of the 24 already exists, is broken, or is
missing) · the migration of the carried rows into the model · the dashboard's
information design · the retirement path for the Android and Streamlit consumers · and
any build order.
