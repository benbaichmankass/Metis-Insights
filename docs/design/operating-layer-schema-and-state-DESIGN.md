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

## 2 · Where coordination state lives

**Split by lifetime.** This is not a compromise between the two clean options — each
requirement names its own home, and the requirements are not all the same shape.

**The lease and reaper force a live store.** *"No work lost if a session stops
mid-way"* requires something that notices a session's **absence**. Git cannot: a dead
session cannot commit its own lease expiry, and a heartbeat-per-commit is absurd. The
decision round-trip likewise needs a write path reachable from a browser.

**The audit trail forces the repo.** A decision's basis must be diffable and
reviewable before it lands. In SQLite alone it is neither.

| | Holds |
|---|---|
| **Repo — durable, versioned** | Intents · work objects with their done-condition, evidence links, verdict, decision and basis · the archive |
| **Live store — volatile, API** | Steps in flight · leases and heartbeats · progress · the decision inbox · the notification queue · computed views (constraint readout, blocker graph) |

**The boundary rule:** *anything that must survive the VM lives in the repo; anything
that must be observed in real time lives in the store.* A step's **outcome** is promoted
to the repo at close-out; its **progress** never is.

**On disagreement:** the repo wins for durable facts, the store wins for liveness.

The live store is a **sidecar**, not the money DB — following the existing
`trainer_store_db_path()` precedent beside `trade_journal_db_path()`, so operating state
never contends with the live trader's writes and cannot widen its blast radius.

---

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
