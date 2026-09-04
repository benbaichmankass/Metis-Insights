# DEMOTE-TO-SHADOW-AND-TUNE — the third path

**Status:** proposed, awaiting operator agreement (`DEC-20260904-DEMOTE-AND-TUNE-FLOW`)
**Owner:** MI-107, under `WO-20260903-SUNSET-DISPOSITIONS-OWED`
**Tier:** this document is Tier-1. Every *move* it describes is Tier-3.

---

## What this is

Operator, 2026-09-03, verbatim:

> "I don't want to retire anything yet — we need a flow for downgrading to
> shadow and tuning, not just turning things off out right."

That rejected all four options the previous ask offered, and the rejection was
the finding: every option was a point on a *retire / do-not-retire* axis, and
the operator rejected **the axis**. Sunset's only terminal move is RETIRE. This
document adds the third path.

**The state already exists; only the flow was missing.**
`config/strategies.yaml::execution: live | shadow` is a declared,
default-permissive gate. A `shadow` leg runs, evaluates, and logs its order
packages everywhere, and never sends an order. A leg can be demoted today —
verified in `src/core/coordinator.py` (the `execution: shadow` fold at
`multi_account_execute`, line ~1295). What did not exist is **(a)** criteria for
when a candidate is demoted rather than retired, **(b)** a tuning loop attached
to the demotion, and **(c)** an exit condition that returns it to `live` or
finally retires it.

**This flow rides `execution: shadow`. It adds no third execution gate.** There
are exactly two (`accounts.yaml::mode`, `strategies.yaml::execution`) and a
capability must never hide behind a default-off `*_ENABLED` flag — the MES
stranding is the recorded cost. Nothing here introduces a new switch, and the
registers it writes are decision records, not gates.

---

## (c) THE EXIT CONDITION — built first, deliberately

A demote with no exit condition is how a leg becomes permanently half-off and
unowned.
`BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED`
records that failure for backlog rows, and a shadowed strategy is worse: it
silently consumes evaluation budget while producing no money. Built second, this
flow would convert a retire-backlog into a shadow-backlog and call it progress.

**This is not hypothetical here. The shadow-backlog already exists.** Measured
against `config/strategies.yaml` on 2026-09-04: **8 of 52 enabled legs are
already `execution: shadow`**, and the oldest — `turtle_soup` — has been shadow
since **2026-04-29**, four months, with zero lifetime closed trades, routed to
zero accounts, and it is now surfacing as a *retirement candidate*. It is the
exhibit for what this section exists to prevent: demoted, unowned, and only
noticed because a different mechanism eventually flagged it.

### The bound is on the TUNING BUDGET, not on the outcome

The obvious exit condition — "return to live once it clears the M7 promotion
gate" — is **unevaluable for exactly the legs that get demoted**, and shipping
it would be the decorative half of this design.

`scripts/strategy_gate.py::GateThresholds.min_live_trades = 30`. A 1d equity
leg trades roughly 4 times a year
(`BL-20260814-1D-EQUITY-LEGS-TRADE-4-PER-YEAR-SO-PER-LEG-OOS-25-CONSUMES-6-YEARS`),
so 30 closes is **~7.5 years**. And `execution: shadow` folds into
`effective_dry` on *every* account including paper, so demoting a leg **stops
its paper fills too** — the demotion makes its own exit condition strictly
harder to satisfy. An outcome-keyed exit condition on a low-frequency leg is a
condition that is never met, which is the state this section forbids.

So the exit condition is keyed on something that advances **whether or not the
leg trades**: the weekly sunset pass.

### The contract

Every demotion writes a row carrying four fields, and **a demotion without them
is refused**:

| field | meaning |
|---|---|
| `demoted_at` | the date the leg went to `shadow` |
| `hypothesis` | what the tuning is meant to change, stated *before* the tuning |
| `lever` | the parameter family the hypothesis will move |
| `tuning_budget_passes` | how many weekly sunset passes the demotion gets (default **8** ≈ two months) |

**What reads it, and on what cadence.** `scripts/ops/sunset_pass.py`, on its
existing weekly cron (`.github/workflows/sunset-pass.yml`). It already reads
`comms/strategy_reviews/*/INDEX.json` (the M7 gate's own output), already reads
lifetime closes, and already carries a candidate forward and escalates on carry
count. It re-implements no detector — the anti-duplication contract that pass
already declares.

Each pass, for each demoted leg, it resolves **one of three exits**:

1. **RETURN TO LIVE** — the M7 gate grades `PROPOSE_PROMOTE_TO_LIVE`.
   This verdict already exists in `scripts/strategy_gate.py`; nothing new
   computes it. Tier-3: proposed, never enacted.
2. **RETIRE** — the budget is spent and the gate still grades `KEEP_SHADOW`
   (it ran and the leg is below the bar). Tier-3: proposed, never enacted.
3. **BUDGET EXPIRED WITH NOTHING GRADED** — `HOLD_SHADOW_COLLECT_DATA`, i.e.
   the gate could not run, for the whole budget.

### What happens when it is never met

**Exit 3 is the never-met case, and it is the reason this section is first.**

When `tuning_budget_passes` is spent, the row **cannot remain `demoted`**. The
sunset pass emits a **forced disposition**: the leg becomes either
`promote_proposed` or `retire_proposed`, and if the evidence supports neither,
it becomes `retire_proposed` **with the reason recorded as
"budget expired, never gradeable"** — which is a different and more useful
statement than "this leg lost money", and must not be written as if it were.

The default terminal action is therefore *retire-proposed*, not *stay shadow*.
That direction is chosen deliberately: a leg that could not be graded in two
months of shadow is consuming evaluation budget and producing no evidence, and
the failure mode this whole document exists to prevent is the one where nothing
forces the question. **The operator still answers it — retiring is Tier-3 — but
the row escalates instead of sitting.**

This reuses the carry-escalation shape `SUNSET-DISPOSITIONS.json` already
declares (`CARRY_ESCALATION_PASSES = 3`, enforced by
`scripts/ci/check_sunset_dispositions.py`), where carrying an item forward
unmoved is itself the measurement. No new register, no new guard, no new gate.

---

## (a) DEMOTE CRITERIA — and the finding that shapes them

The axis that separates DEMOTE from RETIRE is **not** "how badly is it doing".
It is:

> **Is there anything to tune?**

- **DEMOTE-AND-TUNE is for a leg that trades and loses.** There is a measured
  quantity, tuning can move it, and `shadow` keeps it running at zero money risk
  while the tuning happens. This is the flow the operator asked for and it is
  the right instrument for that leg.

- **It is the wrong instrument for a leg that produces nothing.** Demoting a
  non-trading leg to `shadow` stops even its paper fills, so it becomes *less*
  able to produce the closes any exit condition needs. The demotion mechanically
  converts *"never closed a trade while live"* into *"never closed a trade, and
  now cannot"*. Tuning a strategy that never fires is tuning nothing.

So the flow needs a **third disposition the operator's framing did not have**:

### `REPAIR` — not demote, not retire

The leg is broken **upstream of the strategy**: a data feed, a routing entry, an
arbitration loss, a config inconsistency. The fix is in the plumbing, not the
parameters. A `REPAIR` row names the suspected cause, points at the diagnosis
that would confirm it, and carries the same budget-and-escalation contract as a
demotion — so a repair lane cannot become its own backlog either.

### The decision table

| the leg… | disposition | why |
|---|---|---|
| closes trades, loses money | **DEMOTE + tune** | there is a measurable thing and a lever |
| closes trades, wins | keep live | — |
| **produces no closes, cause is upstream** | **REPAIR** | tuning cannot reach the fault |
| produces no closes, cause is the strategy | **RETIRE-proposed** | nothing to tune, nothing to repair |
| produces no closes, cause unknown | **REPAIR**, budget-bounded | the diagnosis IS the work |

---

## (b) THE TUNING LOOP

Attached to a DEMOTE only. It is a loop, not a wish:

1. **Hypothesis, written before the sweep.** What is believed wrong and what
   would change it. Recorded in the demotion row's `hypothesis` field.
2. **Sweep the declared lever** on the existing harness —
   `scripts/ml/strategy_tune_sweep.py`, the per-family backtest harnesses, or the
   `exit-refinement` pipeline where the lever is exit geometry. No new harness.
3. **Walk-forward before proposing.** A sweep winner that does not survive
   out-of-sample is not a result; this repo has the recorded cost of shipping
   levers that were measured OOS-negative
   (`BL-20260813-FIVE-SHIPPED-LEVERS-MEASURED-OOS-NEGATIVE`).
4. **Grade against the shadow book**, not against the backtest alone — the leg
   is running in shadow and logging order packages, which is the whole point of
   demoting rather than disabling.
5. **One pass through the loop consumes one unit of `tuning_budget_passes`**, and
   the row records what was tried. A budget spent with nothing tried is a
   different finding from a budget spent on four refuted hypotheses, and the row
   must be able to say which.

If the loop produces a candidate that clears the M7 promotion gate, that is
exit 1 and it goes to the operator as `PROPOSE_PROMOTE_TO_LIVE`.

---

## The ten candidates, run through the flow

The ten that prompted this: `gdx_pullback_1d`, `gld_pullback_1d`,
`iaum_pullback_1d`, `mes_trend_long_1d`, `scha_trend_long_1d`,
`splg_trend_long_1d`, `spy_trend_long_1d`, `tqqq_trend_long_1d`,
`trend_donchian_sol`, `turtle_soup`.

**Population and source:** `comms/sunset/2026-09-01/INDEX.json`, the E3 pass over
all 52 enabled legs, joined to `config/strategies.yaml` read 2026-09-04.

**Measured, all ten:**

- **`lifetime_closed_trades: 0`.** Every one. Not one of the ten has closed a
  single trade in its life.
- **Not one is routed to real money.** Every routing is `paper` —
  `alpaca_paper`, `alpaca_portfolio`, `alpaca_options_paper`, `ib_paper`,
  `bybit_1`. `turtle_soup` is routed to **nothing**.
- Nine grade `never_closed_lifetime`; `turtle_soup` grades `unrouted`.
- `turtle_soup` is **already** `execution: shadow`, and has been since
  2026-04-29.

**So none of the ten qualifies for demote-and-tune.** Not because the operator's
instinct was wrong — it was right, and the flow above is built — but because
these ten are a **different fault class**: they are not underperforming, they are
**not running**. There is no money being lost to protect (all paper) and no
measured behaviour to tune (zero closes). Demoting them would stop their paper
fills and guarantee the exit condition is never met.

**Three already have a named cause sitting dormant in the work store:**

| leg | cause already filed | class |
|---|---|---|
| `splg_trend_long_1d` | `BL-20260825-SPLG-HAS-NO-USABLE-YFINANCE-HISTORY`, `BL-20260829-SPLG-TREND-LONG-1D-EMITS-ZERO-TRADES-IN-730-DAYS` | data feed |
| `trend_donchian_sol` | `BL-20260830-TREND-DONCHIAN-SOL-SIGNALS-144-TIMES-AND-JOURNALS-NOTHING-ON-BYBIT-1` — it **does** signal, 144 times; nothing reaches the journal | routing / arbitration |
| `turtle_soup` | `enabled: true` and in the `strategies:` list of **zero** accounts | config inconsistency |

`trend_donchian_sol` is the sharpest case: 144 actionable signals and zero
journal rows. The strategy is working. Something between the signal and the
order is not. Tuning its parameters would change nothing, and retiring it would
delete the evidence `OI-20260831-PER-ACCOUNT-ARBITRATION` is waiting on.

**Proposed disposition for all ten: `REPAIR`, budget-bounded** — with the
diagnosis, not the parameters, as the work. Nothing is retired. Nothing is
demoted. Each keeps a `review_by` so the lane cannot become its own backlog.

---

## What is settled and what is not

**Settled:**

- **(c)** the exit condition, its reader (`sunset_pass.py`), its cadence
  (weekly), its three exits, and its behaviour when never met (forced
  disposition at budget expiry, defaulting to `retire_proposed` with the reason
  recorded as *not gradeable* rather than *lost money*).
- **(a)** the demote-vs-retire criteria, and the `REPAIR` third disposition the
  data forced.
- **(b)** the tuning loop's shape and its existing tooling.

**Not settled, and needing the operator:**

- **Agreement to the flow itself.** It is written; it is not agreed.
- **The `REPAIR` lane for the ten.** These legs need *diagnosis*, which is
  session work nobody has been asked to schedule. Whether to spend that, or to
  make "never fired in N passes" a retirement basis on its own, is a real fork
  and it is the operator's call.
- **Every move remains Tier-3.** No leg's `execution:` changes in this PR.

**Explicitly NOT claimed:** that the flow works. It is designed and agreed by
nobody yet, no leg has run through it, and `sunset_pass.py` does not yet
implement the budget-expiry forced disposition — that build follows agreement,
so it is not reported as shipped.
