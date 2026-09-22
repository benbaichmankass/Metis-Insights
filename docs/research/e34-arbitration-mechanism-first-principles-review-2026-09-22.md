# E34 — the arbitration mechanism, reviewed from the original problem

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> ⚠️ **TIER 3 SUBJECT MATTER. `landing: hold`, `hold_reason: tier_2_3_needs_approval`.**
> This lane changed **no runtime behaviour**. No `set-env`, no `config/`, no
> `src/`. Everything below is a measurement, a reading of committed code, or a
> proposal. The manager lands.

**The operator's ask, verbatim (2026-09-22):**

> *"This fanout was a solution to a bug that came into being when we started
> trading pairs and needed hedge mode — the solution created new problems, nor
> am I sure that it solved what we wanted it to. We need a bigger review of the
> original problem and the mechanism, to be [sure] we're maintaining expected
> trading behavior."*

---

## Headline answers, before the working

1. **The fan-out was not built for the pairs/hedge-mode bug.** Those are two
   separate defects found six days apart, with two separate mechanisms. The
   recollection conflates them. **The pairs bug got hedge mode; the fan-out was
   built for something else.** MEASURED from git history and the two backlog
   rows — § 1.
2. **Neither mechanism has been checked against its own resolution criterion.**
   The pairs row is still `status: open` and its criterion is unmet; the
   fan-out's end-to-end proof was never taken. For the fan-out the stronger
   statement is available: it is **measured to have made routing narrower than
   leaving it unarmed** — § 2.
3. **The structural fix is far cheaper than every prior row assumes**, because
   the two things those rows named as blockers — the per-account election and
   the audit-emission constraint — **are both already built, already pure, and
   already running in production since 2026-08-31.** § 3 and § 5.

---

## 1 — What was the original problem?

**In one paragraph.** Two distinct defects, six days apart, neither of which
caused the other. On **2026-08-21** the market-neutral pairs sleeve was measured
stranding a leg on **8 of 8** SOL/ETH opens, because `bybit_1` is a one-way
**netting** account and a pairs leg opposite a concurrent directional position
merely reduces it instead of opening a book
(`BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN`, in
`docs/archive/2026-09-21-operating-reset/registers/health-review-backlog.json`).
Its remedy was **hedge mode** — `src/runtime/bybit_position_mode.py`, introduced
**`1f06fd23d`, 2026-08-22, #10130**, shipped deliberately inert, and the
venue-side flip is the `switch-bybit-position-mode` system-action. Separately,
on **2026-08-27**, `BL-20260827-PROP-ONLY-TWIN-WINS-THE-GLOBAL-SYMBOL-SLOT-AND-STARVES-ITS-PAPER-SIBLING`
measured that `intents.aggregate_intents` collapses to **one winner per SYMBOL,
globally, before account fan-out**, so a prop-only twin wins the SOLUSDT slot and
`bybit_1` receives nothing — SOLUSDT buy-side winners, `event=pipeline_result`,
2026-08-01..08-27, **n=60**: `ict_scalp_sol_5m` 23, `trend_donchian_sol_prop` 15,
`ict_scalp_sol_15m` 14, `trend_donchian_sol_4h` 6, **`trend_donchian_sol` 0**.
That row, and the code comment corrected alongside it (**`b05872423`,
2026-08-27, #10359**), is what `src/runtime/arbitration_fanout.py` was built for
— **`b0f586299`, 2026-08-30, #10501**, at `annotate`, routing explicitly
unchanged.

### The timeline, with the commit or row that introduced each piece

| date | artifact | commit / row | what it is |
|---|---|---|---|
| 2026-05-14 | `src/runtime/intent_multiplexer.py` | `3f897b700` (#1125) | the intent layer. **The global winner-per-symbol election predates pairs by two months and hedge mode by three.** |
| 2026-07-15 | `config/pairs.yaml`, `pairs_executor` | `272c8dd6b` (#6519) | the M22 pairs sleeve, isolated 2-leg path, `account_id: bybit_1` |
| 2026-08-21 | `BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN` | archived health backlog | **problem A** — netting strands a pairs leg, 8 of 8 |
| 2026-08-22 | `src/runtime/bybit_position_mode.py` | `1f06fd23d` (#10130) | **remedy for A** — hedge-mode `positionIdx` plumbing, shipped inert |
| 2026-08-27 | `BL-20260827-PROP-ONLY-TWIN-WINS-…` | archived health backlog | **problem B** — one global winner per symbol starves a sibling account, n=60 |
| 2026-08-30 | `src/runtime/arbitration_fanout.py` | `b0f586299` (#10501) | **remedy for B**, at `annotate` — measures starvation, routes nothing |
| 2026-08-30 | `BYBIT_HEDGE_MODE_SYMBOLS` armed | `src/units/accounts/clients.py:2600` | **A's remedy actually armed** (a hedge-mode sibling book is called "routine since … armed 2026-08-30") |
| 2026-08-31 | `gate_intents` / `elect_from_gated` / `plan_per_account_election` | `12e4a9eb4` (#10544) | the per-account election **and** the fix for the audit-emission constraint |
| 2026-09-12 | `apply_rounds` carries round geometry | `1baff7a1f` (#11970), **merged 2026-09-12T14:42:34Z** | MI-286 — the fan-out starts actually dispatching |

### The conflation, stated precisely

**There is no code path by which the pairs sleeve reaches the arbitration
mechanism.** VERIFIED by reading, not inferred:

* `config/pairs.yaml:46` — `account_id: bybit_1`, and the file's own header says
  *"there is NO pairs config on a real-money account."*
* `src/units/strategies/pairs_executor.py:5` — *"once-per-tick hook
  (`run_pairs_tick`), **never through `multi_account_execute`**."*
* `src/main.py:1050-1051` — `run_pairs_tick(settings)` is called directly, once
  per tick, outside the multiplexer.
* CLAUDE.md § "The two execution gates" says the same thing independently.

**So the multiplexer carries no constraint on the pairs sleeve's behalf, and
never has.** The operator's instinct to ask *"does the hedge-mode/pairs
requirement actually need a global election at all?"* has a clean answer: **no —
it needs nothing from the election, because it never enters it.** Whatever the
right scope for the election is, the pairs sleeve is not an argument for any
particular one.

⚠️ **What is true in the recollection**, and worth keeping: both defects are
consequences of *accounts being a first-class thing the system routes to* — one
at the venue's book level (netting vs hedge), one at the election's scope level
(global vs per-account). They arrived in the same fortnight, on the same
account, and were worked by overlapping sessions. Treating them as one problem is
an understandable compression; acting on them as one problem would fix neither.

---

## 2 — Did the mechanism solve it?

**Measured, per problem. Neither answer is "yes".**

### Problem A (pairs stranding) — improved, never closed, never re-asked

`BYBIT_HEDGE_MODE_SYMBOLS` was armed **2026-08-30**. The row's own resolution
criterion is *"ten consecutive pairs opens where both legs remain open until a
`pairs_revert` / `pairs_stop` / `max_hold_bars` close, with zero `half_open` rows
in the same window."*

MEASURED **2026-09-08**, locator
`comms/reports/since-last/20260908T112000Z/report.json`, population **a 207-row
close window**: **8 `pairs_half_open_cleanup` closes (3.9%)**, against 36
`pairs_revert` + 34 `pairs_stop`. The prior reading, 2026-08-29 (pre-arming), was
**17 of 42 closes (40.5%)** and **35 `pairs_half_open` WARN events over
08-20..08-29**.

**INFERRED** from those two populations (they are different denominators, so this
is a direction, not a ratio): arming hedge mode **materially reduced** the
stranding and **did not eliminate it**. The criterion — *zero* — is **not met**.

**And nobody ever checked.** The row's last update is **2026-08-29**, one day
*before* arming. Its `status` is still `open`. It was archived on 2026-09-21
with the rest of the registers and **is not imported** (that is `A8`, unrun), so
**no mechanism will re-ask this question.** That is the likelier-than-it-sounds
answer the brief anticipated, and it is the answer here.

### Problem B (global election starving an account) — not solved, and the armed state is worse than the unarmed state

The fan-out's end-to-end proof, named by the soak module's own docstring, is *"a
starved account writing a JOURNAL ROW on a tick this log shows it was elected
on."* **That has never been taken** — stated on checklist row `E17` and not
contradicted anywhere I could find.

What *was* taken is stronger and worse. PR **#12723** / `docs/research/e33-arbitration-scope-starves-real-money-2026-09-22.md`
(branch `lane/e33-per-account-arbitration-scope`, `424be5380`) measured on the
live soak row **`2026-09-21T20:30:08Z`, ETHUSDT** — locator diag-request
**#12708**, run `35695727461`, read 2026-09-22T06:38:27Z, path
`/api/diag/log_file?name=arbitration_fanout_soak&lines=30`, population **11
complete rows over ~6.4 h**:

`rounds_planned[0].accounts = [bybit_1, bybit_2, bybit_portfolio]` →
`rounds_written[0].accounts = [bybit_1]`, with `starved_count: 0`.

**I re-derived the mechanism from the source independently and it holds.** Two
lines do it:

* `src/runtime/pipeline.py:1087` — `_rounds = _fanout_apply_rounds(signal)`, then
  `if _rounds: <per-round dispatch> else: <global dispatch>`. The fan-out
  **replaces** the global dispatch; it does not supplement it.
* `src/runtime/intent_multiplexer.py::_attach_fanout_plan` — `if mode == "apply"
  and allow:` then `scope_round_to_accounts(r, allow)`, whose body is
  `accounts = [a for a in (round_.get("accounts") or ()) if a in allow]`. It
  **intersects** the round's accounts with the allowlist.

So an account left off the allowlist is **deleted from a round it had already
won**, and the surviving round suppresses the global path that would have served
it. **That is a subtraction, not a narrowing of an addition** — which is why the
partially-armed state is the only one of the three that is worse than doing
nothing.

**I can tighten E33's one loose end.** E33 had a shallow clone and wrote *"the
regression window is bounded, not dated … probably does not cover the whole
2026-09-13 → 09-22 trade window."* With full history: MI-286 (`1baff7a1f`,
#11970) **merged to `main` at 2026-09-12T14:42:34Z**, which is **before the whole
12 / 1 / 1 trade window opens** (`closedAt` 2026-09-13T20:55:29Z →
2026-09-22T08:48:49Z). ⚠️ **Merged is not deployed** — the trader must restart to
re-read the code, and I could not read the VM (§ 6) — so the honest statement is:
**the merge precedes the entire window; the deploy is unestablished.** E33's
hedge was conservative in the wrong direction.

### The monitoring defect rides with it, and is worse than the allowlist alone

`arbitration_fanout.assess` grades `starved` against the **global** election and
has no allowlist parameter, so starvation caused by the fan-out is invisible to
`starved_accounts`, `starved_count`, and therefore to
`src/runtime/starved_account_alert.py`, which reads exactly those two fields
(`starved_account_alert.py:410, 528`). Inherited from E33 and **confirmed by
reading both files this session.**

**Two further invisible-starvation paths, NOT previously on file** (§ 4, rows):

1. `arbitration_fanout.accepted_rounds` is **all-or-nothing and fail-closed** —
   *"One malformed round voids the WHOLE plan."* It returns `[]`, which
   `pipeline.py:1087` reads as *"no fan-out"* and silently takes the global
   `else:` branch. The fallback is correct in isolation, but it means **a single
   bad round anywhere re-introduces `BL-20260827`'s starvation for that tick with
   no distinct signal** — `[]` from a refusal and `[]` from "nothing to fan out"
   are the same value at the call site.
2. `plan_per_account_election` **drops a round whose candidate has incomplete
   geometry** and sets its accounts to `state: "unknown"`. If other rounds
   survive, `accepted_rounds` accepts them, the fan-out dispatches, and the
   dropped round's accounts are starved — as `unknown`, which is neither
   `starved` nor `routed` and is counted by nothing.

Both are the same class as E22 and E31: a reading surface that cannot express
the state that matters.

---

## 3 — What does it cost, and is expected trading behaviour being maintained?

**This is the review's product.**

### The cost, measured

MEASURED (inherited from checklist row `E33`, **not re-taken by me** — see § 6):
the 200 most recent closed trades, `closedAt` 2026-09-13T20:55:29Z →
2026-09-22T08:48:49Z (~9 days), restricted to the six legs `bybit_2` was actually
running: **`bybit_1` 12 · `bybit_2` 1 · `bybit_portfolio` 1.** Same strategies,
same window.

The structural reason it is total rather than partial, VERIFIED against
`config/accounts.yaml` this session by parsing the file: **11 accounts**, of
which `bybit_1` carries **26** legs, `bybit_2` **3**, `bybit_portfolio` **3**
(identical, CI-enforced), `breakout_1` **2**. `bybit_1`'s roster is a **superset**
of `bybit_2`'s. So on **every** tick where `bybit_2` has a candidate, `bybit_1`
has one too — and with `mode=apply` and a partial allowlist naming only
`bybit_1`, one of the two subtraction branches fires on every such tick.

**Three consequences, each worth stating separately:**

1. **Gate 2's demotion signal is not measuring what it claims to.** The ladder
   reads demotion off the live/mirror pair, and both halves are being starved by
   the same mechanism. The signal is currently reporting arbitration outcomes,
   not strategy performance.
2. **Any judgement of a real-money leg's live results is confounded** — the leg
   is not being allowed to trade its own signals.
3. **The mirror invariant is enforced at the wrong layer.** CI asserts roster
   equality (`tests/test_paper_portfolio_accounts.py::test_bybit_portfolio_mirrors_bybit_2_exactly`).
   **Nothing asserts dispatch equality**, and § 4's new finding is that the
   allowlist can break it silently.

### Is expected trading behaviour being maintained? **No.**

Stated against the operator's own directive — *"`bybit_1` runs everything (soak,
paper, with fees) and must never constrain another account; `bybit_2` and
`bybit_portfolio` trade the same things"* — as **three testable properties**.
None of the three is checked by anything today.

> **P1 — NO SUBTRACTION (the monotonicity property).**
> For every tick, the set of `(strategy, account)` pairs the fan-out dispatches
> must be a **superset** of what the global path would have dispatched.
> Operationally, on every soak row:
> `⋃ rounds_planned[].accounts  ⊆  ⋃ rounds_written[].accounts`.
> **This is the property the current live configuration violates**, and it is
> the one that makes "armed" safe by construction: under P1 the fan-out can
> never be worse than unarmed, whatever the allowlist says.
> *Checkable two ways:* a unit test over `_attach_fanout_plan` (pure), and a
> live invariant on each soak row, which is also the missing alert input.

> **P2 — `bybit_1` NON-INTERFERENCE.**
> For any account `A ≠ bybit_1` and any symbol `S`, `A`'s dispatch decision is a
> function **only of the candidates `A`'s own roster declares**. Formally:
> deleting from the candidate set every strategy that is on `bybit_1` and not on
> `A` must not change `A`'s dispatch.
> *Note this already holds inside `plan_per_account_election`* — the invariant is
> asserted there in terms (*"AN ACCOUNT ONLY EVER ELECTS FROM STRATEGIES IT
> DECLARES"*, with a `logger.warning` and a refusal). **It is broken downstream,
> at the scoping and dispatch steps**, which is precisely why the fix is cheap.

> **P3 — THE MIRROR IS NOT SPLITTABLE.**
> `bybit_2` and `bybit_portfolio` appear in **exactly the same rounds** on every
> tick. Today this follows from P1+P2 *only if* the allowlist names both or
> neither — and **nothing enforces that** (§ 4).
> *Checkable as:* a CI guard on the allowlist value, plus a soak-row assertion.

---

## 4 — Recommendation on the structural fix

### The cost of the structural fix has been overestimated by every prior row, and this is the finding that matters most

Both `E17` and E33 § 4(b) treat the per-`(symbol, account)` fix as a Tier-3
rewrite of `aggregate_intents` blocked on an unanswered design question. **Both
are working from a constraint that was removed three weeks ago.**

**The audit-emission constraint is SOLVED, not open.** VERIFIED by reading
`src/runtime/intents.py`:

* `gate_intents` (line 1544) — *"**This is the half with side effects.** It emits
  one `regime_hard_gate` … row per candidate per call."*
* `elect_from_gated` (line 1615) — *"**Pure** — no audit emission, no policy
  load, no env read. **Safe to call repeatedly within one tick (once per account
  / per book)**, which `aggregate_intents` is not."*
* `gate_intents`'s own docstring names the reason: *"Per-account arbitration was
  blocked precisely because re-running `aggregate_intents` re-ran the gate
  (`BL-20260827-…`). **That is the entire reason this split exists.**"*

Both landed in **`12e4a9eb4`, 2026-08-31, #10544** — the same commit that built
`plan_per_account_election`. `src/runtime/intent_multiplexer.py:877-882` runs
them in production today: `gate_intents(...)` once, then `elect_from_gated(...)`.

**And the per-account election itself already exists and already runs.**
`arbitration_fanout.plan_per_account_election` elects a winner per account off
the single gated candidate set, is pure, asserts the roster invariant, and its
output is on every live soak row (`elected_by_account`, `per_account`,
`rounds`).

> **So the structural fix is not "make `aggregate_intents` per-account".
> That has effectively been written. The residual defect is entirely in how its
> output is scoped and dispatched — two code sites, both small.**

### The recommendation

**Do (1) and (2). They are not alternatives, and (1) is the one that matters.**

**(1) Make P1 structural — the fan-out may ADD accounts to a dispatch, never
REMOVE them.** *Tier 3 (it changes live routing). ~15–25 lines, two sites, no
new concepts.*

Exact parameters, as a proposal only:

* In `intent_multiplexer._attach_fanout_plan`, after scoping: if the set of
  accounts in `scoped` is a **proper subset** of the accounts in `rounds`, **do
  not write `apply_rounds` at all** — set `apply_state` to a new,
  distinguishable value (`refused_would_subtract`) and let `pipeline.py:1087`
  take the unchanged global `else:` branch. Add the dropped accounts to the plan
  under a new key so the soak records them.
* Equivalently and preferably *in addition*, in `pipeline.py` change the
  `if _rounds: … else: …` to dispatch the per-round packages **and** the global
  package to any account named in no accepted round — making the two paths a
  union rather than a replacement. This also closes both invisible-starvation
  paths in § 2 (`accepted_rounds` returning `[]` mid-plan, and the
  geometry-dropped round).

**Cost:** small and confined; no new election logic, no order-path change, no new
env var. **Risk:** the fan-out falls back to the global path more often, which
reinstates `BL-20260827`'s starvation on those ticks. ⚠️ **That risk is bounded
by construction: under P1 the system is never worse than unarmed**, which is
exactly the property today's configuration lacks. The residual is that a
partially-armed allowlist becomes a **no-op** instead of a subtraction — which is
the correct semantics for a safety allowlist and should be said out loud in the
module docstring.

**(2) Make the starvation visible.** *Tier 1.* Give `assess` the allowlist (or
grade a second axis), and have `starved_account_alert.py` read the
`rounds_planned` vs `rounds_written` pair. **No alert reads that pair today**,
and it is the only field pair that can express this class of starvation. A
surface reporting `starved_count: 0` on a tick it starved real money is the
defect, independent of whether (1) lands.

**What I do NOT recommend, and why, so it is not re-proposed:**

* **Rewriting `aggregate_intents` per-account** — unnecessary; § 4's first half.
  The global election can stay exactly as it is as the fallback, provided P1
  holds.
* **Leaving the allowlist as the load-bearing control.** The manager's widening
  to all 11 accounts today is **a mitigation, not the fix**: it makes the
  subtraction empty *at the current value*, and nothing prevents the next value
  from re-creating it. ⚠️ Note that under **(1)** the widening is still correct
  and should stay — the two compose.
* **`ARBITRATION_FANOUT_MODE=annotate`** (E33's option (c)) — strictly better
  than a *partial* allowlist, but it is a retreat to `BL-20260827`, and it is
  worse than the widened state the manager already has running.

---

## 5 — Rows filed

Four findings here are not already on file. Filed as `PIPELINE.jsonl` rows in
this PR, not as memo prose:

| row | what |
|---|---|
| `PI-20260922-E34-AUDIT-EMISSION-CONSTRAINT-IS-SOLVED-NOT-OPEN` | the blocker E17/E33 cite was removed by `12e4a9eb4` on 2026-08-31; the structural fix is cheap |
| `PI-20260922-E34-ARBITRATION-ALLOWLIST-CAN-SPLIT-THE-STAGE-2-MIRROR` | `ARBITRATION_FANOUT_ACCOUNTS` has **no validation anywhere** — `allowlisted_accounts()` splits a CSV, `set_env.sh` validates only the *service* — so a value naming `bybit_2` and not `bybit_portfolio` breaks the mirror invariant CI enforces at the roster level |
| `PI-20260922-E34-TWO-MORE-INVISIBLE-STARVATION-PATHS-IN-THE-FANOUT` | `accepted_rounds` all-or-nothing, and the geometry-drop `unknown` state |
| `PI-20260922-E34-PAIRS-STRANDING-ROW-NEVER-RE-ASKED-AFTER-HEDGE-MODE-ARMED` | `BL-20260821` is `open`, last updated the day before its own remedy was armed, archived and unimported, criterion unmet at 3.9% |

---

## 6 — What I could NOT establish, and what I tried

* **I could not read the live VM at all.** `issue_write` returned **`403
  Resource not accessible by integration`** on `POST /repos/.../issues` —
  **the same failure the E33 lane hit**, tried once this session with
  `title: "[diag-request] log_file?name=arbitration_fanout_soak&lines=200"`,
  label `vm-diag-request`. `issue_read` on #12708 succeeded in the same session.
  **So I did not verify the manager's widening of the allowlist on the running
  process**, and I did not take the denominator read E33 asked for (rows where
  `rounds_planned[].accounts ⊃ rounds_written[].accounts` over a stated window).
  I am working from what is readable rather than narrowing the population
  silently.
* **I did not re-take the 12 / 1 / 1 closed-trade split.** It is inherited from
  row `E33` and needs the same relay. Everything I *could* check is consistent
  with it — in particular the roster superset relation, which I re-derived from
  `config/accounts.yaml` directly.
* **I did not establish the MI-286 DEPLOY time**, only its merge
  (2026-09-12T14:42:34Z, `1baff7a1f`). Merged ≠ deployed, and the deploy is what
  opens the regression window.
* **I did not measure the pairs stranding rate after 2026-09-08.** The
  2026-09-08 `since-last` report is the newest committed artifact naming
  `pairs_half_open` that I could find; a fresher read needs the relay.
* **I did not establish whether `BYBIT_HEDGE_MODE_SYMBOLS` is still armed today**
  or what symbols it names. The arming is asserted in
  `src/units/accounts/clients.py:2600` as of 2026-08-30 — that is **a claim in a
  comment**, and under *field beats comment* it wants a `get-env` read this
  session could not perform.
* **Not attempted, and flagged rather than guessed:** whether P1 as specified
  interacts badly with `multi_account_execute`'s `account_scope` parameter when
  both a per-round and a global dispatch run on the same tick. A union dispatch
  must not double-place on an account named in both. **That is the one design
  question in recommendation (1)** and it belongs to whoever writes the diff.

---

## Relationship to the open rows

* **`E33` (#12723, open, draft, Tier-3 hold)** — this review does not contradict
  it. It confirms its mechanism independently from source, tightens its MI-286
  bound, and **replaces its § 4(b) reasoning**: the audit-emission question it
  deferred on is already answered.
* **`E17` (blocked on `E33`)** — unchanged; `breakout_1` belongs in any
  widening, and under recommendation (1) the widening stops being load-bearing.
* **`BL-20260827`** — **not closed by anything.** Under recommendation (1) it
  remains the behaviour on every tick the fan-out declines to dispatch, and that
  is the correct, stated residual rather than a silent one.
* **`BL-20260821`** — see § 5; nothing will re-ask it until `A8` runs.

---

## 7 — ADDENDUM 2026-09-22T12:33Z — the manager's population read

⚠️ **Added after the body above was written, and kept as a separate section
rather than folded in**, so a reader can see which claims are mine and which
arrived later. The manager measured the defect over a population I could not
reach (`issue_write` 403, § 6); this section says what it changes and what it
does not.

**MEASURED by the manager**, locator: 400 soak rows,
2026-09-14T06:30:57Z → 2026-09-22T12:05:01Z, read direct over
`https://ict-bot.duckdns.org` (Transport A, `log_file?name=arbitration_fanout_soak&lines=400`).

* **PRE-FIX — 392 rows / 194.9 h.** 94 rows graded more than one account;
  **89 of those 94 show the subtraction.** Times each account was DELETED from
  a round it had already won: **`bybit_2` 58 · `bybit_portfolio` 58 ·
  `breakout_1` 57.** By symbol: ETHUSDT 47 · XRPUSDT 23 · SOLUSDT 15 · BTCUSDT 9.
* **POST-FIX — 8 rows / 2.2 h, 0 multi-account rows, which proves NOTHING.**
  At the pre-fix rate of 0.482 multi-account rows/hour you would expect ~1.1, so
  **0 is the modal outcome of a sample too small to contain the case.**

### What it changes

1. **§ 2's evidence base, upgraded — and this is the one that matters.** The
   body rests on **one** soak row (`2026-09-21T20:30:08Z`, inherited from E33)
   plus a reading of the source. The mechanism is now measured at **89 of 94
   contended rows over 194.9 h**. Everything § 2 concludes stands; it is no
   longer an n=1 claim.
2. **`E17` IS THE SAME DEFECT FROM THE PROP SIDE, AND THE BODY UNDER-STATES
   THIS.** § 7's closing list calls `E17` *"unchanged"* and only says
   `breakout_1` belongs in the widening. **That is too weak.** `breakout_1` was
   deleted **57** times — statistically indistinguishable from `bybit_2`'s 58 —
   so *"breakout_1 receives nothing"* is **not a separate prop-account outage**;
   it is this same allowlist subtraction, and E17's own row already carried the
   proof (`apply_scope: {breakout_1: "not_allowlisted"}`, its elected leg
   planned, graded and thrown away). **Recommendation (1) therefore closes E17
   as well, and E17 should not be worked as a distinct fix.**
3. **One over-statement in § 3, corrected.** The body says one of the two
   subtraction branches fires *"on every such tick."* The population says
   **89 of 94 (94.7%)**, not 94 of 94. The five exceptions are unexplained and
   I did not see the rows. Read it as *almost every*, and the residual as
   unestablished rather than zero.
4. **One item leaves § 6's could-not-establish list.** The widening **was
   applied and the process re-read it** — post-fix rows exist and are graded as
   such. What is still NOT established is that it *works*, for the reason the
   manager states: 8 rows over 2.2 h cannot contain the case.

### What it does not change

* **§ 1 (the origin and the conflation)** — established from git history and
  the two backlog rows; a soak population cannot bear on it.
* **§ 3's headline (the audit-emission constraint is solved)** — established by
  reading `intents.py` and `arbitration_fanout.py` on `main`. Unaffected.
* **§ 4's recommendation** — unchanged, and better supported: P1's whole value
  is that it removes the subtraction **by construction**, which is exactly what
  a post-fix window too small to detect a regression cannot confirm by
  observation. The widening's unverifiability is an argument *for* P1, not a
  substitute for it.

### What the addendum makes newly askable

**The post-fix denominator is now a stated, closable question rather than an
absence.** At 0.482 multi-account rows/hour, distinguishing "fixed" from
"we have not looked long enough" needs on the order of **a few days**, not
hours — and the read is already scripted (count rows where
`rounds_planned[].accounts ⊃ rounds_written[].accounts`). That is the
observation that would move the widening from `landed_unproven` to `done`, and
it belongs on whichever row owns the widening.
