# Why the operator got no pings for four hours — and what carries them now

**MI-80 · 2026-09-02 · Tier-1**

The operator asked at 18:33Z: *"no pings for 3 hours?"* It was four. This is what
was measured, what was ruled out, what was built, and what is still not fixed.

---

## 1. The cause is GitHub's scheduler, and it is not a work-digest bug

The obvious reading is that `work-digest.yml` is misconfigured. It is not. The
same shape holds across **every** scheduled producer in the repo.

| workflow | cron | declared | last output landed | slip |
|---|---|---|---|---|
| `probes.yml` | `20 5 * * *` | daily | `generated_at` **2026-09-01T10:13Z** | **+4h53m**, and nothing at all on 09-02 |
| `due-list.yml` | `50 5 * * *` | daily | commit **2026-09-02T09:57Z** | **+4h07m** |
| `econ-calendar-produce.yml` | `30 22 * * *` | daily | commit **2026-09-02T00:44Z** | **+2h14m** |
| `macro-valuation-snapshot.yml` | `30 7 * * *` | daily | commit **2026-09-01T12:52Z** | **+5h22m**, and nothing on 09-02 |
| `work-digest.yml` | `20 * * * *` (from 16:22Z) | hourly | **4 runs in its entire life** | 17:20Z and 18:20Z both absent |

Four different workflows, four different concurrency groups, four different job
durations, one shape. **State the population:** the first four rows are the
complete commit history of each producer's own output path; the work-digest row
is `run_number: 4`, which is a census — a run number cannot exceed the number of
runs.

⚠️ **The commit-time column overstates the slip by up to ~30 min**, because
`commit-to-main` with `verify-merged` waits for its auto-merge PR. The
`probes.yml` row is the honest one: `generated_at` is stamped by the job itself,
and it reads +4h53m — which independently reproduces the figure already in
`work-digest.yml`'s own header (~4h50m).

**Ruled out, with reasons rather than by elimination:**

- **The `concurrency: work-digest` group.** It governs *execution*, not run
  *creation*; a run cancelled by concurrency still appears in the run history
  with `conclusion: cancelled`, and none do. It also cannot explain `probes.yml`,
  which is in a different group entirely and is equally late.
- **The 40-minute job budget vs a 60-minute cadence.** A run that is never
  created cannot be budget-limited. And the overlap is theoretical rather than
  real: the 18:34Z dispatch run took **2m31s** end to end (18:34:21Z →
  18:36:52Z). The 40 is a ceiling for the pathological auto-merge wait, not a
  duration.
- **This repo's heavy Actions usage.** Plausible as a contributor and **not
  claimed**: GitHub's scheduler is not observable from here, and asserting a
  mechanism I cannot measure would be the unprovenanced-diagnosis failure this
  repo names elsewhere.

What *is* established is the effect, and it is repo-wide, consistent, and not
fixable from inside this repository.

## 2. `push` on the same repo is reliable

Directly observed in the same run listing, minutes apart:

- merge `8c82be1a` at **18:36:23Z** → five push-event workflows created at
  **18:36:26Z** (3 seconds).
- merge `002e0ab8`, and pushes to two `claude/**` branches at 18:33:57–18:35:39Z,
  all firing their workflows within seconds.

And `main` is busy: **34 merges in the 6h to 18:36:23Z — one every ~10.6
minutes.**

So the digest now also fires on `push: branches: [main]`, with
`scripts/ops/digest_due.py` as the rate limiter that turns per-push into hourly.
The cron is **kept**, not replaced: it is the only carrier that can fire while
`main` is quiet.

## 2b. THIS IS A RECURRENCE, and that is the most important thing here

`BL-20260830-SCHEDULED-WORKFLOW-LAG-WENT-FROM-1H-TO-6-12H-ON-2026-08-27` is
**open**, filed three days ago, and says in its own words:

> *"ANY plan whose correctness depends on a cron firing at roughly its declared
> time is now wrong, and the plan looks fine right up until it silently does not
> happen on time."*

It was itself filed by a session that had been bitten the same way, and it notes
the evidence was already in the repo and *"was not checked before the approach
was chosen."*

On **2026-09-02** the operator's notification cadence was raised to an hourly
cron regardless. Nobody read the row. Four hours later the operator asked why
the pings had stopped.

So the technical cause is GitHub's scheduler; the *systemic* cause is that a
filed, open, correct row about this exact failure mode did not reach the session
that needed it. That is a `RECURRENCE`, and the fix for it is not in this PR —
this PR only stops the digest depending on the broken carrier.

## 3. Already filed, re-confirmed: the daily latch is inert

⚠️ **This was NOT found here.**
`BL-20260901-WORK-DIGEST-DAILY-LATCH-CANNOT-FIRE-ON-THE-ONLY-PATH-THAT-RUNS-IT`
is open and already records it, with a sharper reading than mine — it notes the
two environments *disagree*: `runtime_logs/` persists on the VM, so the identical
code latches there and does not on a runner, "same source, opposite cadence,
decided by which box it runs on and stated nowhere."

What this session adds is only a re-measurement under the new hourly cadence.

`work_digest.py::_already_sent_today` says:

> *"One digest per UTC day. A latch, so a double invocation cannot double-ping."*

It keys on `runtime_logs/work_digest_state.json`, and `runtime_logs/` is
`.gitignore`d (line 29). On a GitHub runner — **the only host that runs the
digest** — the file never exists, the function always returns `False`, and the
latch guarantees nothing.

Re-verified two ways: by the gitignore, and by **three digests landing on the
same UTC day** (#10710 00:21Z, #10813 14:35Z, #10836 18:36Z). *Field beats
comment.* The prior row measured this against a six-per-day cron; it now stands
against an hourly one, so the as-written claim is off by 24×, not 6×.

This did **not** cause the missing pings — an inert latch blocks nothing. It
matters because it is a second mechanism answering "should we notify?", and an
ephemeral per-runner latch silently disagreeing with a committed receipt is the
two-sources-of-truth shape this repo keeps paying for. The workflow now passes
`--force`, making the receipt the single authority on the interval. The latch is
left in place — removing a safety latch is a wider call than this change — and
is filed rather than silently tolerated.

## 4. Nothing could say the digest had stopped

This is the finding that outlives the incident. F6 makes operator notification
the **condition** the autonomy grant rests on, so that precondition had been
unmet all day and no mechanism could report it.

`render_due_list.src_red_crons` looked like the detector and is not: it grades
the latest scheduled run's **conclusion**, so a cron that simply never fires
leaves a stale-but-successful latest run and reads perfectly clean. **A missed
slot and a quiet hour are indistinguishable from it.** Worse, it runs inside
`due-list.yml` — itself a cron, itself measured landing 4h07m late. *A cron
watchdog for crons cannot report its own carrier dying.*

`scripts/ci/check_digest_liveness.py` closes that. It reads the committed
receipt, grades four never-collapsed states (`fresh` / `stale` / `never_ran` /
`unreadable`), and runs in `run_guards.py` on **every pull request** — an event
measured firing in seconds, not invokable from a prompt, a skill, or a checklist
step. It is not invoked by the thing it watches.

It **passes** on `never_ran` and arms itself on the first landed receipt, for
the reason `check_pr_queue_watch.py` already records: failing on it would red
every PR in the repo the day it merges, which is how a guard gets disabled
instead of fixed. No receipt is committed with this change, deliberately —
committing a hand-made one would be a receipt claiming a digest the operator
never received, precisely what the receipt exists to make impossible.

## 5. What is NOT fixed

**A push-driven digest cannot produce a heartbeat while `main` is genuinely
quiet.** There is no push to ride. The cron remains as the second carrier for
exactly that case, and the cron is best-effort *by measurement*.

The workflow header is explicit that the empty run **is** the evidence the
cadence is alive, so this boundary is real and not a technicality: through a
quiet night the operator can still lose the reassurance heartbeat.

A guaranteed quiet-period heartbeat needs a carrier this repo does not own — a
**Claude Routine**, which fires reliably on this account (the `:26` manager
sweep fired 54 seconds late) but costs a full session per firing, 24 a day. That
is an operator cost decision and is deliberately **not** assumed here.

The cadence itself is untouched: the operator accepted hourly noise explicitly
(*"I realize that's a lot of noise, but that's how I want it for now"*), and
nothing here quietly reduces it.

## 6. Also tripped over — not mine, not fixed

`run_guards.py` reports **`artifact-validity-guard: FAIL`** and
**`operator-owed-guard: FAIL`** on a clean checkout of `main`, with no changes
applied. Verified by control: the same two, and only those two, fail before and
after this change; the only verdict that moves is `digest-liveness-guard`
appearing as PASS. Out of scope here, recorded so it is not mistaken for
fallout from this PR.

## 7. Verification performed

- `digest_due.py --self-test` — 20 assertions, both directions on every state.
- `check_digest_liveness.py --self-test` — 18 assertions, including that the
  guard's window stays wider than the producer's interval so the pair cannot
  silently invert.
- **Mutation-tested against real files**, because a check that only ever passes
  proves nothing: a fresh receipt → `not_due` / `fresh` (rc 1 / rc 0); the same
  receipt back-dated 9h → `due` / `stale` **FAIL** (rc 0 / rc 1); a corrupt
  receipt → `unreadable` **FAIL**.
- Full `run_guards.py` before/after diff — one line changes, the new guard.
- `tests/test_commit_to_main_callers.py` — 4 passed (the 40-minute budget pin).
- End-to-end dry run of the workflow's exact sequence: gate → self-tests →
  render → record → gate flips to `not_due`, guard reads `fresh`.

**Not verified, and it cannot be from here:** that a `push`-triggered
`work-digest` run actually fires and lands. A workflow file that parses is not a
run — the same distinction this file is about. The first merge to `main` after
this lands is the real test, and the receipt's `trigger` field records which
event carried it.

---

# Addendum — the general case: what fires reliably AND survives its creator

Scope widened by the operator, relayed 18:52Z: *"we definitely need that, like,
more reliable timers. We can't just go dead every time I stop checking you."*

The requirement is not "the digest arrives hourly." It is **the system must not
go dead when the operator stops looking.** The digest is one instance.

## The census — measured, not assumed

`list_triggers(limit=100)` → **n = 26, `next_cursor: None`.** ⚠️ **Population
caveat:** `include_completed` defaults to false, so already-fired one-shots are
hidden. This is a census of **live** Routines, not of all Routines ever.

| fact | count |
|---|---|
| Routines returned | **26** |
| cron-driven | **3** (`26 * * * *`, `56 * * * *`, `10 * * * *`) |
| bound to a `persistent_session_id` | **25 of 26** |
| carrying an observable `last_run` | **1 of 26** |

**25 of 26 are bound to `session_011JWFxuYAaEQKCFCmG6gnHJ`.** When that session
ends, the reliable half of this system's cadence ends with it. That is the
operator's complaint, stated structurally: the durable carrier does not fire,
and the firing carrier is not durable.

## The one that is not session-bound — and it is the answer

`trig_01TWdAvrwFLe6T9XFoNopTeo` · *"Manager queue watch — escalate a blocked
sub-session PAST the manager"* · `cron_expression: 56 * * * *`

- `persist_session`: **absent**. `persistent_session_id`: **absent.**
- `last_run`: `{"status": "ROUTINE_RUN_STATUS_PENDING", "fired_at":
  "2026-09-02T18:56:42Z", "session_id": "cse_01Lz7NKcTSW99UJn3eLoAdBA"}`

It fired **42 seconds** after its `:56` slot, into a **new session**. It is the
**only** Routine in the census whose run is observable at all.

So the manager's open question — *does a fresh-session Routine survive its
creator?* — is answered **positively and by measurement, not by assumption**:
this account already runs one, it is creator-independent by construction, and it
fires on time.

⚠️ **What this does NOT show.** The two session-bound sweeps report `last_run:
absent`, which per `CLAUDE.md` is the *correct* state of a working poke-only
Routine — so their absence is **not** evidence they are broken, and I did not
treat it as such. The claim "Routines fire reliably" rests here on **one**
observable firing, not on three. That is thinner than it looks and is stated
rather than rounded up.

## Recommendation, costed

**Do not put the digest itself on a Routine.** Hourly would cost 24 fresh
sessions a day, each loading the full project context, to carry a job the
push-trigger already carries whenever anything is happening.

**Put the WATCHDOG on a fresh-session Routine instead — ~4 firings a day.** It
reads the digest receipt and pings only when stale. This is strictly better on
three axes:

1. **It is the carrier that survives**, per the measurement above.
2. **It is cheap** — 4 sessions/day, not 24, because a watchdog does not need
   the cadence of the thing it watches.
3. **It satisfies the repo's own rule** that the detector must not be the timer
   it watches. The CI guard shipped in this PR is a good detector with one
   weakness: it only runs when somebody opens a PR, so it is silent in exactly
   the "operator stopped looking" window. A fresh-session Routine has no such
   dependency.

**Cost I could not measure:** the token spend of one fired session. I can state
the shape (full project context load per firing) but not the number, and I am
not going to invent one.

**Third carrier, assessed and rejected for this job: a VM systemd timer.** The
`ict-*.timer` units are the existence proof that they fire reliably here. But
the digest must *commit to `main`*, and the VM holds only
`VM_GIT_DEPLOY_TOKEN`, which is **Contents: read-only** — it cannot push. A VM
timer could still ping the operator directly (the VM already owns the Telegram
drain), which makes it a genuine candidate for the *watchdog* role. It is
**Tier-2** (a new unit on the live VM) and is therefore proposed, not enacted.

**Creating the Routine is not mine to do**: it is recurring spend on the
operator's account. Evidence and cost are here; the decision is theirs.
