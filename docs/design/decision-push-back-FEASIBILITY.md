# Decision push-back — FEASIBILITY

> **What this document is.** The operator asked, 2026-09-02:
>
> > *"Can we maybe add, like, a push something on the end of that so that, when the
> > answer gets to the repo, it knows to push it to the session instead of waiting
> > for the session to pull?"*
>
> This establishes **what can actually fire, from where, with which credential**,
> before anything is built. It is written first on purpose. Every claim below is
> marked **TESTED** (a measurement made for this document, with the date and the
> command), **READ** (taken from documentation and *not* measured), or
> **RECORDED** (a measurement someone else made, cited to where it lives).
>
> ⚠️ **A doc-read is not a measurement and is never presented as one.** Where the
> two disagree — and in one case below they do — the disagreement is stated
> rather than resolved by preference.

---

## 0. The gap, stated precisely

The round-trip that exists today was proven end to end on the morning of
2026-09-02 (**RECORDED**, `WO-20260901-PHASE-H.yaml`, whose `answer` block
carries `submission_id: 7ce60af995cb4b32aeb4f86209851e0d`,
`answered_at: 2026-09-02T10:16:36.989279Z`, `answered_by: telegram`):

```
session writes a decision_request        →  docs/claude/work/objects/<id>.yaml
operator taps an option                  →  POST /api/bot/work/decision
answer appended to the transit log       →  runtime_logs/work_decision_transit.jsonl   (in_transit)
work-decision-commit.yml + committer     →  answer: block written into the object YAML
merged to main                           →  /api/bot/work/decisions grades it committed
```

**Every hop is a push except the last one.** The session that asked the question
learns the answer only if it happens to look. Nothing tells it.

And there is a second gap underneath the first, which is the one that actually
gates everything:

> **No decision request records WHICH SESSION ASKED IT.**

Measured over the whole store (**TESTED**, 2026-09-02, `grep -rn "asked_by"
docs/claude/work/` plus a read of `normalise_requests` in
`src/runtime/work_decisions.py`): the request schema is
`{id, question, options, allows_free_text, urgency, asked_on, context, answer}`.
There is **no field naming the asker**, and no such field appears anywhere in
the store. So even a working delivery mechanism would have **no address to
deliver to**. This is the smallest real change in the whole task and it gates
the other half completely.

---

## 1. Can a GitHub Actions runner fire a **session-bound Routine**?

### 1.1 What is already established about the mechanism itself

**RECORDED** — `docs/claude/work/MANAGER-CHECKLIST.json`, correction dated
2026-09-02. `create_trigger` with `persistent_session_id` set and **both**
`cron_expression` and `run_once_at` omitted (a poke-only Routine), followed by
`fire_trigger`, delivers a prompt into a specific existing session. Measured
2026-09-02T05:35Z on `session_01PcirVtpMXJGiKm2548nkrR`: immediately before the
fire it read `SESSION_STATUS_IDLE` / `disconnected`; immediately after,
`SESSION_STATUS_RUNNING` / `connected`, same session id, context intact.
Trigger `trig_01NcP7PmwUeFPCbRN4G61VQX`.

Two limits travel with it, both recorded at the same place:

- **The fired turn runs without `mcp__*` tools** — a Routine created this way
  stores no MCP connectors. This is why the brief's constraint *"the push must
  CARRY the answer, not a pointer to it"* is not a stylistic preference: a
  poked turn told to *go read the PR* has no `mcp__github__*` with which to do it.
- **It is one-way.** The poked session cannot answer back.

That correction exists because an earlier claim — *"a manager cannot message a
running sub-session"* — was written into a merged PR body and a checklist row
after **one** failed `SendMessage`, and was false. The narrow true statement is
that `SendMessage`/`ListAgents` do not *address* cloud sessions from a manager
container. **That is the standing lesson this document is written under**, and
it is the reason nothing below is concluded from a single negative.

### 1.2 The actual question: can a *runner* do it?

A GitHub Actions runner has no `mcp__*` tools at all — it is a plain Ubuntu
container. So the question reduces to: **is there an HTTP API, and a credential
that can be stored in Actions secrets?**

**READ** — <https://code.claude.com/docs/en/routines>, fetched 2026-09-02.
There *is* an HTTP API. Routines support an **API trigger**:

```
POST https://api.anthropic.com/v1/claude_code/routines/<trigger_id>/fire
  Authorization: Bearer sk-ant-oat01-…
  anthropic-beta: experimental-cc-routine-2026-04-01
  anthropic-version: 2023-06-01
  {"text": "run-specific context"}
```

The token is per-routine, revocable, and *"scoped to triggering that routine
only"* — a genuinely narrow credential, and exactly the kind that belongs in
Actions secrets.

**But it does not do what is needed here, for two independent reasons:**

1. **It starts a NEW session.** The documentation is explicit: *"POSTing to the
   endpoint with the routine's bearer token **starts a new session** and returns
   a session URL"*, and the response body is
   `{"type":"routine_fire","claude_code_session_id":"session_…"}` — a *new* id.
   A new session is not the session that asked; it has none of the context that
   made the question worth asking. Whether `/fire` would honour a
   `persistent_session_id` set on a routine created through MCP is **UNKNOWN —
   undocumented, and not tested here.** I am not asserting it fails; I am
   recording that nobody has established it either way, and that the documented
   semantics point the other way.
2. **The token cannot be minted programmatically.** *"API triggers are added to
   an existing routine from the web"*, and *"The CLI cannot currently create or
   revoke tokens."* Even if (1) resolved favourably, the design needs **one
   routine per asking session**, created at question-time — and each would need
   a human to open the web UI and click *Generate token*. That is not a
   mechanism; it is a manual step per question.

> **Verdict on (a): NO for the automated per-question case** — and the blocker is
> structural (per-routine, web-only, manual token minting), not "we could not
> find an endpoint".

### 1.3 What *does* reach an existing session from CI

**READ** — <https://code.claude.com/docs/en/claude-code-on-the-web>
§ *Send follow-ups from the CLI*, and
<https://code.claude.com/docs/en/self-hosted-environments-testing>
§ *Run the test loop*, both fetched 2026-09-02.

```bash
claude -p "your message" --cloud <session-id> --output-format json
```

This posts **one message into an existing cloud session and exits without
waiting for a reply**. The docs recommend it for precisely this shape — *"send
follow-ups from a [CI script]"* — and it *"sends no local session state, so the
command doesn't need to run from the machine that started the session"*.

It fits the brief's constraints unusually well:

| Brief's constraint | How this satisfies it |
|---|---|
| carries the answer, not a pointer | the message body is arbitrary text — quote the answer straight in |
| one-way, nothing acknowledges receipt | *"queues the message into the session and exits without waiting for a reply"* |
| a dead/archived session is a REAL state | documented distinct errors, see below |

And the three states the brief demands are **already the API's own vocabulary**,
which is the strongest argument for choosing this mechanism:

| State | Signal (**READ**) |
|---|---|
| `pushed` | `--output-format json` → `{"ok": true, "session_id": …, "url": …}` |
| `session_gone` | `Session not found: <id>` · `cloud session <id> is archived and cannot accept new messages` |
| `unknown` | anything else — no credential, network failure, unclassifiable non-zero exit |

⚠️ **One subtlety that must not be collapsed into `session_gone`.** **READ**,
same page § *Environment expired*: a cloud session stops after inactivity and
its VM is reclaimed, but *"Reopen the session … to provision a fresh VM with your
conversation history restored."* An **expired** session is therefore **not gone**
— it is dormant and reopenable. Whether a queued message to an expired session is
delivered on reopen, dropped, or errors is **UNKNOWN and untested**. Until it is
established, an outcome that is not an explicit *not found* / *archived* must
grade **`unknown`**, never `session_gone`.

### 1.4 The credential — and this is the finding that decides the shape

**READ**, <https://code.claude.com/docs/en/self-hosted-environments-testing>
§ *Authenticate from CI*, quoted rather than paraphrased because the wording is
load-bearing:

> Both `claude -p … --environment` and `claude -p … --cloud` authenticate with a
> claude.ai OAuth token; API keys, such as `sk-ant-xxxxx`, aren't accepted for
> either call.

> **There is no long-lived CI token for this today.** The scope that grants
> remote-session control, `user:sessions:claude_code`, is capped server-side at
> 30 days, so `claude setup-token`, which mints a one-year inference-only token,
> doesn't cover it.

> To provision a stored login onto an ephemeral runner, set
> `CLAUDE_CODE_OAUTH_REFRESH_TOKEN` and `CLAUDE_CODE_OAUTH_SCOPES` so
> `claude auth login` exchanges the token without a browser; **the same 30-day
> cap applies to the refresh grant.**

A GitHub Actions runner is an ephemeral runner. So the delivery half is
**buildable**, and its credential has three properties the operator — not this
session — has to weigh:

1. **It expires every 30 days** and must be re-minted **interactively** by a
   human. A push-back channel that silently stops working every 30 days is worse
   than one that was never built, *unless* the failure is loud and falls back to
   the pull path. Both are required below.
2. **It is over-scoped for the job.** `user:sessions:claude_code` is account-wide
   session control. The task at hand is *"deliver one sentence to one session"*;
   the credential that does it can create, message and steer **every** session on
   the account. Compare the routine token, which is scoped to one routine — the
   right shape, attached to the wrong mechanism.
3. **The operator must originate it.** This is not a preference: `CLAUDE.md`
   § *Access & autonomy* reserves exactly this category — *"the only actions you
   genuinely cannot perform are physical or credential ones"* — and the value
   requires an interactive browser login this session cannot perform.

⚠️ **This repo is PUBLIC.** The secret therefore lives in Actions secrets and the
repo holds **only its NAME**. That is stated here because it is the exact failure
`BL-20260818-DIAG-READ-TOKEN-PUBLIC-EXPOSURE-UNREMEDIATED` records: a workflow
wrote a live bearer into a world-readable issue comment, and it still authorized
three months later.

---

## 2. `watch_url` — TESTED, and it is disqualified twice over

**TESTED**, 2026-09-02, this session. Called `watch_url`; it returned a webhook
of the form `https://api.anthropic.com/integrations/v1/code/webhook-triggers/<uuid>/fire`
together with a `sealed_secret`, and the tool's own result said:

> The sealed credential only works for this session and this webhook, and **only
> the artifact service can open it — it is not usable from this conversation.**

The webhook was stopped immediately afterwards with `unwatch_url` (result:
*"Webhook stopped; it will not accept further deliveries."*). **The URL and the
sealed secret are deliberately not reproduced in this document**, which is a
public file — see below for why that matters more here than usual.

Three disqualifiers, the first of which is fatal on its own:

1. **The credential is sealed to a specific third party.** A GitHub Actions
   runner is not the artifact service and cannot open the sealed secret or
   produce a delivery it would accept. This is not a permissions problem to be
   configured around; it is what "sealed" means. **TESTED.**
2. **The watch dies with the session.** **READ** (the tool's own contract): *"a
   watch ends when the session ends"*. A decision can sit unanswered for days;
   the asking session will very often be idle or expired by the time the answer
   commits. A registration that cannot outlive the wait is not a registration.
3. **There is nowhere safe to put the URL.** This was flagged in the brief as a
   first-class security question and it deserves the answer plainly: **a webhook
   URL committed to this repo is a published endpoint.** Anyone who reads the
   file can POST to it. Actions secrets would be the correct home — but a
   *session* cannot write an Actions secret (there is no such tool; the repo's
   `init-actions-secrets.yml` creates empty placeholders for a human to fill),
   so each new asking session would need the operator to hand-carry its
   short-lived URL into a secret. That is unworkable at one-decision-per-question
   scale even before disqualifiers 1 and 2.

> **Verdict on (b): NO.** Not "we could not get it working" — structurally the
> wrong instrument, on a measurement rather than an inference.

---

## 3. Anything else (the brief's (c))

Things looked at and what came of them:

| Candidate | Outcome |
|---|---|
| `SendMessage` / `ListAgents` from another session | **RECORDED** as not addressing cloud sessions from a manager container. Not a runner path either — no MCP on a runner. |
| Routine `/fire` with a `text` payload | Works, but delivers into a **new** session (§1.2). Also **READ**: `text` arrives wrapped in `<routine-fire-payload>` and labelled untrusted, so the routine's saved prompt must explicitly opt in to acting on it. |
| GitHub trigger on a routine | **READ**: `pull_request` and `release` events only, and each event *"starts a new session"*. Same defect as above; the round-trip's landing PR would wake a stranger, not the asker. |
| The existing Telegram decision channel | Already built (`src/runtime/telegram_decisions.py`). It pushes to the **operator**, which is the opposite direction. Not a candidate, listed so it is not re-proposed. |
| **Two-hop: a fresh-session Routine as the delivery agent** | **Viable, and it needs no new credential** — see §4.2. |

### 3.1 The enabling fact for the whole design

**TESTED**, 2026-09-02, this session: `get_session` with `session_id` **omitted**
returns the *calling* session's own record, id included — it returned
`session_01PEYVqTaCY92C3HmtHwxYff`, which is this session. Independently
**RECORDED** by the manager at 2026-09-02T06:21Z.

This matters because it is what makes "record which session asked" implementable
at all: a session can discover its own id unaided, at the moment it writes the
question. Nothing has to be passed in, and nothing has to be looked up later —
which is fortunate, because by the time the answer commits, the asking session
may no longer be running to be asked.

---

## 4. What follows from all of this

### 4.1 The delivery half is buildable, and its missing piece is a credential the operator owns

Not a missing mechanism — a missing **secret**. Concretely:

- The code, the decision function, the three states, the idempotence marker and
  the workflow wiring can all be built and tested now.
- The delivery step reads `CLAUDE_CODE_OAUTH_REFRESH_TOKEN` from Actions
  secrets. **While that secret is unset, the step must grade every answer
  `unknown` and change nothing** — never `session_gone` (we did not look), and
  never a silent skip (a channel that is off must say so).
- The operator decides whether to mint it, knowing it expires in 30 days and
  carries account-wide session control.

### 4.2 If the operator declines the secret, the honest fallback is two-hop

The workflow records the **intent** to push (a marker in the repo); something
that already holds the capability performs the delivery. The carrier is a
**fresh-session Routine on a schedule**, which *does* have `mcp__*` tools and can
therefore do the `create_trigger(persistent_session_id=…)` + `fire_trigger`
sequence that §1.1 records as measured working.

- **Missing hop, named plainly as the brief asks:** nothing in the repo can
  *cause* that routine to exist — a routine is created from the web UI or from
  `/schedule` in an interactive CLI session, both of which need the operator once.
- **Cost:** latency is bounded by the routine's cadence, and **READ**, the
  minimum interval is one hour — so an answer could sit up to an hour before
  being pushed. Against a decision that has typically waited days for the
  operator, that is small; it is stated so it is chosen rather than discovered.
- **Benefit:** no new credential, and no 30-day expiry.

### 4.3 Constraints any build must satisfy (these are not negotiable)

1. **It ADDS push; it does not replace pull.** `/api/bot/work/decisions` keeps
   grading `committed` from the repo exactly as it does now. A push-only design
   loses answers precisely when a session died — which is when the answer matters
   most.
2. **Three states, never collapsed:** `pushed` / `session_gone` / `unknown`.
   `unknown` is *we could not establish it*, and it is the default for anything
   not positively identified as one of the other two.
3. **Idempotence comes from the repo**, never from the workflow remembering: a
   `push` block on the committed answer is what makes a second attempt a no-op.
   ⚠️ **The residual is stated rather than hidden:** if a delivery succeeds and
   the run then fails to land its marker, the next run will push again. The
   failure direction is deliberate — a duplicate wake tells the session the same
   true thing twice, whereas a marker written *before* delivery would make a
   failed delivery read as pushed. The run fails loudly when a marker cannot be
   landed, so the condition is visible rather than silent.
4. **Nothing waits for a reply.** There is no acknowledgement in either
   mechanism; the sender learns delivery worked by observing what the woken
   session does. Any design that blocks on a response is wrong by construction.

### 4.4 What this session can and cannot prove

- **Can:** the session-id recording half, end to end, with tests.
- **Can:** the delivery decision as a pure function, with its three states, and
  the workflow wiring that stays inert until the secret exists.
- **Cannot:** an observed wake driven *from a runner*. That needs the OAuth
  secret, which only the operator can mint. Saying "the tests pass" would be
  exactly the *green is not evidence* failure `docs/CLAUDE-RULES-CANONICAL.md`
  names — a green test here proves the decision function, not the channel.

---

## 5. Summary table

| # | Claim | Basis | Verdict |
|---|---|---|---|
| 1 | Session-bound Routine + `fire_trigger` wakes an existing session | **RECORDED** (manager, 2026-09-02T05:35Z, idle→running observed) | works |
| 2 | A fired turn has no `mcp__*` tools | **RECORDED** (same) | quote the answer in; never a pointer |
| 3 | A runner can fire a *session-bound* Routine over HTTP | **READ** — `/fire` exists but starts a NEW session; tokens are web-only and per-routine | **no**, for the automated case |
| 4 | `claude -p --cloud <session-id>` reaches an existing session from CI | **READ**, documented for CI use | **yes** |
| 5 | …with a storable credential | **READ** — `CLAUDE_CODE_OAUTH_REFRESH_TOKEN`, **30-day cap, no long-lived CI token exists** | yes, with an operator-owned expiring secret |
| 6 | `watch_url` can be delivered to by a runner | **TESTED** — sealed to the artifact service, unusable elsewhere | **no** |
| 7 | `watch_url` is a durable registration | **READ** — ends with the session | **no** |
| 8 | A session can discover its own id | **TESTED** this session; **RECORDED** by the manager | **yes** — this is what unblocks the whole thing |
| 9 | Any decision request records who asked | **TESTED** — no such field exists anywhere in the store | **no** — build this first |
