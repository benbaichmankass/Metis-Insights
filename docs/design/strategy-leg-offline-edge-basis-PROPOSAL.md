# A strategy leg's edge verdict has no offline basis to rest on — what must exist before it can

> **Doc status:** `live` · category `plan` · last verified `2026-09-09` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Status: PROPOSAL. Nothing here is enacted.** `scripts/ml/strategy_review_packet.py`
is byte-identical to `main`. `MIN_CLOSED_FOR_ACTION` is unchanged at 20, the window is
unchanged at 7 days, no leg is retired, no config is touched, no exit-matrix cell is
re-graded. Changing what a KILL/DEMOTE/PROMOTE verdict rests on is **Tier-3**.

Filed by **MI-215** (`WO-20260901-PHASE-F`), 2026-09-09.

---

## 1. The finding

`docs/CLAUDE-RULES-CANONICAL.md` § "Promotion evidence — offline edge, live mechanics"
has been **binding since 2026-07-26**. Its scope is stated as *"STRATEGY legs, not just
ML models/heads/policies"*, and its clause 3 reads:

> **No gate may require calendar-time accrual to prove edge.** A fixed "N days at stage"
> / "wait for K live episodes" requirement is a policy artifact, not evidence.

The M7 daily strategy-review gate does exactly that. Measured over every committed index
in the repo at `a77afb8e` — **7 indexes, 2026-09-01..09-07** (the brief that commissioned
this work said 8 through 09-08; only 7 are committed here, and the finding is identical
either way):

| date | graded | actionable | by_action | floor | window | below_floor |
|---|---|---|---|---|---|---|
| 2026-09-01 | 52 | 0 | `{hold: 52}` | *(field absent)* | *(absent)* | *(absent)* |
| 2026-09-02 .. 09-07 | 52 | 0 | `{hold: 52}` | 20 | 7.0 | **52** |

**Every leg, every day, held. `below_evidence_floor: 52` of 52.** Not one verdict has
ever been proposed.

`decide()` carries **seven** branches whose verdict is control-dependent on live or
calendar accrual (AST-measured, aliases resolved):

| line | reads | what it is |
|---|---|---|
| 1031 | `n` | `n == 0` → hold |
| 1034 | `n`, `MIN_CLOSED_FOR_ACTION` | the `n < 20` floor → forced hold |
| 1045, 1066 | `n` | threshold-tier selection (`n < 30`, `n < 100`) |
| 1101, 1129 | `n` | `n` inside the tune / kill-softening tests |
| **1149** | **`shadow_soak_days`** | **`promote requires >= 14 days shadow soak`** |

`n` is bound from `headline.n_closed` — closes inside a 7-day live window.

**Line 1149 is the clearest-cut and was not previously named.** `shadow_soak_days < 14`
is literally the "N days at stage" construct clause 3 forbids by name. It is not a
side-effect of the floor; it is a second, independent instrument, and it gates the one
verdict (`promote`) that the floor cannot reach.

⚠️ **The forced `hold` is the violation, not an escape from it.** Withholding a verdict
for want of accrual *is* the prohibited gate. A reading that only counted KILL/PROMOTE
would call this surface compliant, which is how it stayed green across every one of those days.

## 2. The premise correction — read this before proposing a fix

The work that commissioned this proposal stated: *"The repo already has what is needed:
purged walk-forward CV `oos_edge`, `replay_pregate_fleet`, `backfill-shadow-predictions`,
the point-in-time backfills."* **Measured, that is wrong in the direction that matters,
and building on it would produce a re-pointing exercise with nothing to point at.**

- **All four `scripts/ml/replay_pregate*.py` are scoped to ML regime HEADS, by their own
  docstrings** — `replay_pregate.py:2` *"Point-in-time REPLAY PRE-GATE for shadow regime
  heads (RG1)"*; `replay_pregate_fleet.py` speaks of *"all shadow regime heads"* and
  *"all 22 heads"*. 22 heads, not 52 legs.
- **`ml/promotion/oos_edge.py` is manifest+dataset scoped to an ML model** — it
  re-runs *"the candidate's own manifest"* against a baseline trainer. There is no
  strategy leg in its input.
- **The packet's only offline hook carries no edge number, by design.**
  `load_backtest_anchor` returns `{date, summary_table_present, summary_path, note}`; its
  own docstring says `best_variant_net_r` / `current_config_net_r` *"are NOT populated
  here because `all_metrics.json` shape varies by run mode"*, and *"the gate matrix does
  not depend on this block."*
- **And in production that hook is effectively dead.** Across all **52** committed full
  packets (2026-09-01): **51 carry `backtest_anchor: null`.** The single non-null one is
  `turtle_soup`, pointing at a **2026-06-09** sweep — three months stale — and still
  carrying no number, only *"consult SUMMARY.md for per-variant net R."*

**So the root cause is not that the gate looks in the wrong place. It is that there is
nowhere per-leg to look.** The gate grades off live closes because live closes are the
only per-leg quantity it has. The raw material exists — the `scripts/backtest_*.py`
family, `strategy_tune_sweep.py`, the trainer's `trainer_mirror/backtests/<date>/`
publications — but **no normalised, machine-readable, per-leg offline edge record**
exists for a gate to consume.

*(One limit stated: the `all_metrics.json` shape claim is the repo's own statement in
that docstring. This session could not read the trainer mirror to confirm it — the
directory does not exist in a runner checkout, and generation runs on the VM.)*

## 3. The model, which is the actual deliverable

⚠️ **This section replaces a "what is NOT proposed" list that banned the window/floor
question.** That framing was retracted by the operator on 2026-09-09, and the retraction
is the most useful thing in this document:

> *"you keep adding guards in the shape of forbidding questions from being asked - this
> is incorrect. the problem isn't the question, it's the underlying misunderstanding of
> infra and processes, and not asking questions isn't what fixes that - it just makes it
> more likely that you will f\*\*\* again in the future because you're not asking the right
> question ... it's that you don't understand how the system works. that's what we need
> to fix here, not the question"*

**So ask about the window and the floor freely.** What was wrong with the four options
put to the operator earlier that day was not that they were asked — it is that all four
rested on a false model of how this system establishes evidence. **Ban the false model,
not the question.** The model:

1. **EDGE is established OFFLINE** — purged walk-forward CV, `replay_pregate_fleet`, the
   point-in-time backfills. That is where *"does this strategy work"* is answered.
2. **LIVE data establishes MECHANICS ONLY** — that live executions match the simulator,
   that the pipeline feeds what was trained on. Canon puts that at **1–2 executed
   trades**, not calendar time.
3. **SLOW LIVE ACCRUAL IS A FEATURE OF THIS SYSTEM, NOT A BARRIER TO OVERCOME.** A leg
   closing ~1.2 trades a week is not a problem to engineer around, and
   **`none_gradeable` on a live window is not a defect.**
4. **Fewer legs does not raise trades-per-leg**, and on paper accounts every leg runs
   regardless — so retiring legs cannot buy gradeability.

⚠️ **READ §1's TABLE THROUGH POINT 3.** The 52/52 `hold` is **not** evidence that the
fleet trades too slowly; the slowness is expected and fine. It is evidence that the gate
is asking the **wrong source**. A reader who takes the table as a case for raising trade
frequency, widening the window, or lowering the floor has inherited exactly the model
this section exists to retire.

**What follows from the model:** `MIN_CLOSED_FOR_ACTION` is a **symptom**, not the
finding. The finding is that the review/validation layer is built on a *live-accrual*
model of evidence while this system's actual evidence engine is the *backtest
infrastructure* — the operator's own words, *"our overall operation is clearly not built
to suit the system's structure."* Reconciling those two is the work. §2's measurements
are what that mismatch looks like from the code side: the gate reads live closes because
live closes are the only per-leg number anyone ever built.

Two things this proposal still does not do, for reasons of authority rather than
inquiry: it does **not** retire any leg (Tier-3, and point 4 says it would not help
anyway), and it does **not** enact the repair (Tier-3 — §6 puts it to the operator).

## 4. What is proposed

**A per-leg offline edge record, produced off historical data, that the gate reads
instead of `headline`.** The shape, stated so it can be argued with:

```
comms/strategy_evidence/<leg>.json
{
  "strategy": "<leg>",
  "config_fingerprint": "<hash of the leg's config/strategies.yaml block>",
  "basis": "purged_walkforward" | "live_faithful_replay",
  "net_r_oos": <float>,          # out-of-sample, net of costs
  "n_trades_oos": <int>,         # BACKTEST trades — history, not calendar
  "folds": <int>,
  "generated_at": "...",
  "source_run": "<path/run id>"
}
```

Then the gate's shape becomes, in the canon's own division of labour:

- **Edge verdict** ← `net_r_oos` from this record. No live count appears in any branch
  that assigns a verdict.
- **Mechanics verdict** ← live rows, needing **1–2 executed trades**, answering only
  *do live executions match the simulator?* — never *is the edge real?*
- **`config_fingerprint` is load-bearing**: an edge record is evidence about the leg
  *as configured*. If the config moved, the record is stale, and the honest verdict is
  `no_offline_evidence` — a **fourth state**, distinct from pass, fail, and
  *we-could-not-read-it*.

⚠️ **`no_offline_evidence` must be loud, not a quiet hold.** Today's 52/52 `hold` reads
as *"the fleet is fine"*; under this proposal a leg with no valid edge record reads as
*"this leg is running with no offline proof"*, which per the canonical rule is *"the gap
is the missing backtest, not more soak time."* That change in what silence means is the
substance of the repair, not a presentational detail.

**Sequencing.** The producer comes first. Re-pointing `decide()` before any record
exists would replace a gate that holds everything with a gate that reads `null`
everywhere — the same 52/52 outcome, now harder to see.

## 5. What MI-215 shipped instead (Tier-1, landed with this doc)

The rule was contradicted across all seven committed days **unseen**, and that is the half a session can
fix. `scripts/check_soak_doctrine.py` enforced the doctrine's *prose* and reached
**exactly three files** — the canonical doc and two `SKILL.md`s. Measured by reading all
132 lines: `scripts/ml/strategy_review_packet.py` was reachable by **no code path**, not
merely absent from the text.

**Check D** now scans registered gate surfaces' ASTs, resolves local aliases of
live/calendar-accrual names to a fixpoint, and fails when a verdict assignment is
control-dependent on one. It is a **ratchet**: the seven known branches are declared in
[`docs/claude/soak-doctrine-exceptions.json`](../claude/soak-doctrine-exceptions.json)
with an exact count — one more fails as a new violation, one fewer fails as a stale
ledger — so the number can only be walked **down** as this proposal is enacted, and the
declaring entry is **verified** against a real backlog row rather than merely present.

Observed, not asserted: with a violation planted the guard exits **1** and names it; with
it removed, **0**. `tests/test_soak_doctrine_guard.py` keeps that true (12 tests),
including that a **renamed** accrual variable is still caught — the real gate already
does `n = headline.n_closed`, so a keyword matcher would have missed six of its seven
branches.

## 6. The decision for the operator

The window and the floor are fine to ask about; they are simply not where this
decision sits, because under §3's model no setting of either makes a live count an
edge basis. The question that does sit here:

> **Do we build the per-leg offline edge record in §4, so a strategy leg's edge verdict
> has an offline basis to rest on — and until it exists, does the M7 packet keep
> emitting a verdict at all, or does it declare `no_offline_evidence` and stop implying
> the fleet was assessed?**

**Recommendation:** build the producer, and in the meantime have the packet say
`no_offline_evidence` rather than `hold`. A `hold` that means *"we could not look"* is
the collapsed state this repo has a guard family for, and it is what let those days of
52/52 read as a quiet fleet. Note this recommendation is NOT in tension with §3's
point 3: `no_offline_evidence` says *nobody has proven this leg's edge offline*, which
is a statement about the EVIDENCE, never a complaint that the leg trades slowly.

If the answer is instead that the packet should remain a denominator report and never
propose an action, that is legitimate and must be **recorded as a decision** —
`BL-20260909-STRATEGY-REVIEW-PACKET-GRADES-EDGE-OFF-A-LIVE-WINDOW-WHICH-THE-PROMOTION-EVIDENCE-RULE-FORBIDS`
does not clear on it, it *changes*.
