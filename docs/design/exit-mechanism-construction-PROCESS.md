# How to build an exit MECHANISM (not another lever)

**Status:** process proposal, 2026-08-19. Written after ~20 lever cells across the
pullback family produced zero shippable results, and the operator pushed back on the
framing rather than the results:

> *"A bracket isn't an exit strategy. It's a safeguard. That shouldn't be what is deciding
> most exits unless that's how it's set from the beginning."*

That is correct, and the rest of this document is the consequence.

---

## 0. The diagnosis: we have been at step 3 of a 6-step process, with steps 1–2 missing

### 0.1 The bracket decides most exits

`xrp_pullback_2h`, 284 harness trades: `take_profit 85 · stop 138 · trail_stop 56 ·
timeout 5`. **223 of 284 (78.5%) are decided by a level fixed at entry.** Only the 56
trail exits carry any post-entry information, and a Chandelier trail is still a function
of the trade's own path.

Live, the same shape: `exit_path_coverage` graded **22 of 34 open trades `price_only`** —
closable by nothing except price arriving somewhere.

### 0.2 The decision surface contains no exogenous information — THIS IS THE ROOT CAUSE

`src/research/intrabar_features.INTRABAR_FEATURE_NAMES` is the feature set every exit
study and the ML exit head learn from. All **11 of 11** are endogenous:

| feature | what it is a function of |
|---|---|
| `running_mfe_r`, `running_mae_r`, `upnl_r`, `mfe_giveback_r`, `dmae_dt` | the trade's own path |
| `bars_in_trade`, `bars_in_trade_frac` | the trade's own clock |
| `dist_to_stop_atr` | the trade's own geometry |
| `in_trade_vol_ratio` | the trade's own symbol |
| `taker_imbalance`, `taker_imbalance_intrade` | the trade's own symbol's flow |

**Nothing about the rest of the market is visible to an exit decision.** Not the peer
symbols it is 0.70–0.89 correlated with, not the regime, not the session, not what else
the book is holding.

So the levers being path-based is not a design choice anyone made — it is the only kind
of lever this substrate can express. `stale_stop`, `giveback_stop`, `trail_decay`,
`rr_floor`, `thesis_decay` are five ways of re-reading the same eleven numbers.

**The corollary that matters:** the repeated finding *"no lever beats holding"* is not
evidence that exits cannot be improved. It is evidence that **no function of these eleven
inputs beats holding** — a much narrower and much less discouraging claim.

### 0.3 And the verdicts we did get were not stable

Measured 2026-08-19: holding corpus and commit fixed and moving only `--split-target-oos`
50 → 35 swung `dOOS` **5.14×** and flipped the pre-registered rule PASS → FAIL
(`m20_split_dispersion.py` now measures this). So even the negative results were noisier
than they were reported to be.

---

## 1. The process, in order, with the gate that stops each step from lying

Each step has a **falsification condition** — the thing that must be true for the step to
have produced evidence rather than a number.

### E0 · Census — who decides exits on this leg today?
Per `(strategy, symbol, timeframe)`: the exit-reason distribution, the R-path shape, and
what fraction of exits carry any post-entry information at all.
*Falsifier:* if `>70%` of exits are bracket-decided, the leg has **no exit mechanism** and
steps E3+ are premature. Say so rather than sweeping levers at it.

### E-lit · Survey what has already been solved outside this repo — RUNS FIRST, RUNS CONTINUOUSLY
Practitioner and academic work on exits is large, old, and mostly not in this repo. Every
step below is cheaper if it starts from what is already known rather than from what this
fleet happens to have measured. This step is not a one-off literature review that gets
ticked off — it is re-entered whenever a step returns a negative, because a negative means
*the constructs we tried* did not work, and the survey is where the next constructs come
from.
*Falsifier:* a construct lifted from outside is a **hypothesis, not a result**. It enters
at E1 (as a feature) or E3 (as a lever form) and is subject to the same E2 information
test and E4 dispersion test as anything invented here. Citing a paper is never evidence
about this fleet. Conversely, a construct that fails here has failed **here** — record the
conditions, not a verdict on the construct.

Findings already gathered (2026-08-20) are in §1.5; they are seeds for E0/E1/E3, not
conclusions.

### E1 · Widen the decision surface — the step that is currently missing
Add **exogenous** features to the in-trade panel. Candidate families, cheapest first:
- **peer-symbol state** — the measured 0.70–0.89 correlated names' returns, relative
  strength, and whether the peer has already turned (`comms/research/crypto_correlation_*.json`)
- **regime** — the ADX-14 label the entry gate already computes, as an in-trade series
- **session / time** — venue session, hour-of-day, weekend (crypto funding windows)
- **portfolio state** — gross exposure, opposing correlated positions, capital pressure
  (`src/runtime/portfolio_conflicts.py` already computes the conflict half)
*Falsifier:* a feature that is not available **at decision time in live** is lookahead and
must be refused. Every feature ships with the live accessor that produces it, or it does
not ship.

### E2 · Measure which inputs carry information — BEFORE designing any lever
For each candidate feature, its relationship to **forward R** on held trades, with a
purged/embargoed split grouped by `trade_id` (the `analyze_exit_head` discipline).
*Falsifier:* a feature that does not beat a shuffled-label control carries no information,
and no lever built on it can work.

**RUN 2026-08-20** (`docs/research/e2-feature-information-2026-08-20.md`;
`scripts/research/e2_feature_information.py`). Two preconditions had to be built first:
`analyze_exit_head._univariate_fdr` turned out to be the **pooled, un-purged** version of
this test (`BL-20260820-UNIVARIATE-FDR-IS-POOLED-AND-UNPURGED`), and **no shuffled-label
control existed anywhere in the repo** — E2's declared falsifier had no implementation.

⚠️ **The result depends on WHICH OUTCOME you score, and that is the finding.** On
`ict_scalp` XRPUSDT 15m — 10,103 rows, 530 trades, xa block joined at **`row_coverage`
1.0**, controls valid — six features clear the family-wise bar against `forward_r` and
**every one collapses by one to two orders of magnitude against `advantage_r`**
(`feat_upnl_r` 0.5753 → 0.0062, a factor of 93). `forward_r` is measured **from entry**,
so it shares its baseline with `feat_upnl_r` and every path feature tracking accrued R;
those six are largely arithmetic about where the trade already **is**. Against the
decision-relevant targets — `advantage_r` and `label_hold` — **nothing clears, at either
bar**, endogenous or exogenous, and the negative survives a scale-free min-p companion
shown to fire on the same panel.

**So E3 is still not licensed on this leg**, and the reason has changed: it is no longer
"nobody measured", it is "measured, and the widened panel does not carry the increment".
Disposition is §3.1 — regroup and widen; the thread does not close. Anyone quoting a
`forward_r` score as evidence a lever is buildable must state the target, because the
same panel says the opposite one bar over.

**HORIZON ARM 2026-08-20** (`docs/research/e2-horizon-arm-2026-08-20.md`;
`.github/workflows/research-e2-horizon-arm.yml`). The negative above was measured at a
**12-bar (3 h) vertical barrier**, recorded then as a condition on the answer. Varying only
the horizon — feature set pinned, embargo tracking the horizon, 12-bar replication control
passing on both legs — **the verdict FLIPS on `label_hold`**: `no_feature_beats_control` at
h=12 and h=24, `informative_features_found` at h=48 (XRP) and h=96 (BOTH legs). Tracking one
feature with its own threshold beside it, `feat_dist_to_stop_atr` is monotone in the
statistic AND in the gap to the bar on both legs (XRP −0.0799 → +0.0657; SOL −0.0726 →
+0.0388), with fold sign-agreement rising 0.50 → 1.00. `advantage_r` stays negative wherever
admissible.

⚠️ **Three limits travel with it.** Every feature that clears is **endogenous** — no peer or
order-flow column clears at any rung, so it is the HORIZON that changed the answer and **not
E1's widening**, which §0.2's diagnosis is therefore not rescued by. Part of the effect may
be **barrier geometry** (far from stop ⇒ SL less likely) rather than edge, which E2 cannot
distinguish and the M20 net-of-cost gate must. And h=96 is 4× the harness's own 24-bar
timeout, so it asks a strategy-change question, not an exit-timing one.

**So E3 is licensed on `label_hold` at a long horizon** — explicitly not on `advantage_r`,
and not at 3 h. **State the horizon when quoting any of this**: the same panel, same
features, same legs return opposite verdicts three rungs apart. 4 of 24 runs were
`harness_invalid` and are excluded, all on SOL, at a rate below the gate's own α
(`BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER`); they were **not** re-rolled.

**GATE + ADMISSIBILITY, RESOLVED 2026-08-20**
(`docs/research/e2-gate-decision-and-sol-clustering-2026-08-20.md`). Two corrections to the
paragraph above, both measured.

*The "SOL-panel factor" is withdrawn.* A control **bank** pushed through E2's own imported
machinery (`scripts/research/e2_null_calibration.py`) measures the SOL h48 null as
**calibrated** — clear-rate 0.040 against α = 0.05, mean permutation *p* **0.517** (a valid
null gives 0.5), and a length-matched comparison null agrees. The real cause is that
`inject_controls` seeds from the run seed and fills in row order, so **the noise column is
byte-identical across all three targets of a panel**: the sweep had **8** independent control
draws, not 24. Under the measured co-discard dependence (6.8–14.0× lift) the correct
arithmetic is **P(≥4) = 0.086, not 0.0298**, and **P(all on one leg | ≥4) = 0.329, not
0.125.** Both published numbers assumed an independence the runs do not have; the pattern is
unremarkable.

*The gate now reads a RATE.* K = 64 noise columns, refused only when the Binomial(K, α) upper
tail falls below `gate_level` = 0.01; `harness_state` is four never-collapsed states
(`valid` / `invalid_positive_control_dead` / `invalid_null_miscalibrated` / **`unchecked`** —
K = 0, *we did not look*, which is not `valid`). Measured over 40 seeded sound null panels:
the old single-draw rule discards **2/40 = 5.0%** (α, exactly), this gate **0/40**. The FWER
gate the backlog row proposed was designed, measured (2/64 on a narrow family) and
**rejected** — P(any of K clears) *rises* with K, so it gets more trigger-happy the more
carefully you measure it.

**RE-RUN 2026-08-20 — and it moves the headline.** The full 24-cell sweep under the new
rule: **every admissible cell reproduces its published FWER/min-p/pointwise counts
EXACTLY** — the gate decides admissibility, not verdicts. What moved is the *set*, and
**the holes swapped legs**: `advantage_r` SOL h24/h48/h96 recover (so `advantage_r` is now
negative on **8 of 8** cells, a *stronger* claim than published), `label_hold` **SOL h48
recovers as a HIT 3/5/6** — the "hole at the decisive rung" was the gate's error — while
`label_hold` **XRP h48 and h96 become inadmissible**.

⚠️ **So the `label_hold` flip is carried by SOL ALONE, and the horizon arm's "two
independent legs" defence is WITHDRAWN for that target.** The reason is measured: pooled
over the sweep's 1,536 control draws, **XRP's null is anticonservative at 2.27× α**
(87/768, +8.0 sd, *p* ≈ 2 × 10⁻¹²) while **SOL's is textbook** (38/768, 0.99× α, mean
permutation *p* 0.4956). The original suspicion — that *SOL's* null was the narrow one —
was exactly backwards (`BL-20260820-TRADE-BLOCK-NULL-IS-ANTICONSERVATIVE-ON-XRP`).
⚠️ Read the **leg-level** statement, not the two per-cell firings: re-running one cell
under six seeds fires the gate on 2 of 6, exactly the 0.19 power at K = 64 against a 2×
null that the rule's pre-shipped power curve predicted. The mechanism is **not
established** — the cycling distortion is a structural candidate but the `cv_length`
ordering runs the wrong way.

### E3 · Design levers over informative features — and COMBINE them
Only features that survived E2. Levers are swept **jointly**, not one at a time: the
single-lever sweeps cannot see an interaction, and *"exit when the thesis decayed AND the
peer already turned AND we are past the capital-efficient hold"* is not reachable by any
of them alone.
*Falsifier:* a combined cell must beat the best single cell by more than the added degrees
of freedom buy. State the comparison explicitly.

**PRECONDITION SETTLED 2026-08-20 — THE E2 LICENCE DOES NOT SURVIVE IT**
(`docs/research/e3-barrier-geometry-2026-08-20.md`). Limit 2 above — *"part of the effect may
be barrier geometry"* — was the load-bearing one. Measured: the terminal barrier alone
accounts for **13.9% → 27.0% → 46.6%** of `label_hold`'s entropy across h=12/24/48 **on one
leg**, with SOL h48 at 47.4% agreeing to 0.8 pp; P(hold) is 0.017 at the stop, 0.995 at the
target, 0.556 at the time stop. **The rung where E2 starts finding hits is the rung where the
barrier's share passes ~45%.** And the pooled association **reverses inside every stratum** —
`dist_to_stop_atr` +0.117 pooled / −0.194 within, `upnl_r` +0.106 / −0.219 — on exactly the
features the horizon arm reported clearing FWER. The *within* value is the stable one across
legs and rungs; the *pooled* value, which E2 scores, swings with the barrier mix.

The stratified view licenses nothing either (`touch` is the barrier the trade **later**
reached, so it conditions on the future). **So E3 is NOT licensed by an E2 information score
on this label** — only a decision-time-only, net-of-cost run can license a lever.

**RUN 2026-08-20** (`docs/research/e3-joint-lever-screen-2026-08-20.md`;
`scripts/research/e3_joint_lever_sweep.py`). Levers over the three named features, 11 singles
+ 179 cells, 503 + 567 trades, 4 anchored walk-forward folds, all numbers OOS. **The
falsifier FAILS on both legs.** XRP: the joint grid, given **16.3× the cells, selected the
same single cell every fold — +0.000 R**. SOL: both arms lose, so the comparison does not
apply (`falsifier_applicable: false`; beating a worse negative is not evidence). The one
positive cell (`bank0.75`, XRP, +5.467 R OOS, 4/4 folds) **breaks even at 0.02–0.05 R of
extra cost and the repo's own fee constant resolves to 0.082–0.163 R here** — it fails on
cost by 2–8×.

⚠️ **A decision-time lever screen is HORIZON-INVARIANT**, verified to full float equality:
the horizon moves only the *label*, a lever acts on the *trade*. "E3 at h=48" is the same
screen as at any rung, and **no lever inherits the h=48 licence.**

Two substrate defects surfaced and are filed: the label's 2.0 R upper barrier does not match
the live `tp_at_r: 1.5` and **no trade ever realises above +1.5 R**
(`BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY`); and the take-profit
**level** has never been a swept dimension in any exit sweep, on a fleet whose own E0 census
says a level fixed at entry decides 78.5% of exits
(`BL-20260820-TP-LEVEL-IS-THE-ONE-EXIT-PARAMETER-NEVER-SWEPT`). Per §3.1's *"check the
substrate before blaming the question"*, the bracket geometry is the next thing to measure —
graded **net of fees**, because §3–4 of the screen say cost is the binding term.
Only features that survived E2. Levers are swept **jointly**, not one at a time: the
single-lever sweeps cannot see an interaction, and *"exit when the thesis decayed AND the
peer already turned AND we are past the capital-efficient hold"* is not reachable by any
of them alone.
*Falsifier:* a combined cell must beat the best single cell by more than the added degrees
of freedom buy. State the comparison explicitly.

### E3.5 · OPERATOR DIRECTIVE 2026-08-20 — moving the brackets IS active management

Recorded verbatim in substance because it settles a scope question this document was
ambiguous about, and because § 0.1's *"a bracket isn't an exit strategy, it's a
safeguard"* could otherwise be read as ruling out the cheapest thing that works:

> *"It's fine if the active management is moving the brackets around. That's absolutely
> fine. It just needs to be something that we do consistently and have control of and are
> conscious of … I understand that we don't want to lose too much potential, or if that's
> going to kill our spread and push us over the edge with the fees to being in minus. I
> just want to make sure that we're also actively re-evaluating and adjusting. There is
> some active management on the SL side, but it seems not active enough."*

**Three things follow, and they change what E3 is allowed to propose.**

1. **A bracket that MOVES is an exit mechanism; a bracket that is set once is not.** The
   distinction § 0.1 draws is between *fixed-at-entry* and *re-evaluated*, not between
   *level-based* and *close-based*. A lever that ratchets a stop or pulls a target in on
   decision-time state is squarely in scope.

2. **The cost measurement says this is the form that can work here.** Measured
   2026-08-20 (`e3-joint-lever-screen-2026-08-20.md`): a round trip costs **0.082–0.163 R**
   against a fee-free mean edge of **+0.1376 R** (XRP) / **+0.1167 R** (SOL). So
   *crossing the spread to exit early eats most of a trade's edge* — an early-close lever
   must be rare and decisive, and the E3 screen's one positive cell died precisely because
   it fired on 34–52% of trades. **Amending a resting stop or target is not a fill and
   costs nothing.** The operator's "kill our spread … push us over into minus" is the same
   constraint arrived at from the other side, and it is now a number rather than an
   instinct.

3. **"Not active enough" is measurably right.** Of **48 live strategy legs, 30 (62.5%)
   declare no exit lever at all**; of the 18 that do, `trail_decay` is on 16, `stale_stop`
   on 3, `giveback_stop` on 1, and `vol_trail` on **zero** (measured against
   `m20_fleet_exit_sweep.LEVER_DECLARED_KEYS` over `config/strategies.yaml`, 2026-08-20).
   Beside that, `m31-p5-telemetry-reading-lever-PROPOSAL.md` § 2 records **13 lever
   firings lifetime against 1,142 closed trades**. ⚠️ Neither figure counts the monitor's
   own break-even stop-trail, which is not a declared YAML lever — so they bound the
   *declared* surface, not all SL movement. Filed as
   `BL-20260820-EXIT-LEVER-COVERAGE-IS-THE-MINORITY-OF-LIVE-LEGS`.

**So the E3 lever family to design next is a bracket that is a FUNCTION OF STATE rather
than a constant** — and E1's exogenous block gets a second hearing there, because it would
be conditioning a *level* rather than triggering an *exit*. The gate is unchanged: net of
fees, walk-forward, dispersion-tested, and Tier-3 to flip.

### E3.6 · OPERATOR DIRECTIVE 2026-08-20 (second) — this is ACTIVE TRADE MANAGEMENT, and the bracket must be PREDICTIVE

E3.5 settled that moving a bracket is legitimate. This settles what the bracket is
*for*, and it renames the problem:

> *"I do think that the brackets should be predictive. Right? Like, when we enter a
> trade, we should know what the expectation is for when it's gonna exit. It's
> possible that the active monitoring will change and adjust that over time, which
> is fine. Like, sometimes a trade will do much better than we thought it would at
> first, and it's worth extending the TP a few times to try and gain more … We're
> really not just looking for exit points anymore. We're looking for how to
> correctly manage trades actively and create not just the entry strategy with some
> brackets that we already have, but how do we create active management strategies
> to optimize our profits and capital utilization … those strategies that have
> brackets that aren't supposed to be hit, maybe we need to change the way that we
> go about that. Maybe those should have predictive brackets even if they need to
> be adjusted based on the conditions defined within the strategy itself. So if
> it's a momentum strategy and we see that we're getting close to the take profit,
> but the momentum is only getting higher, then we know that we can definitely
> expand the TP there. But at least know in the beginning where we think that
> momentum is gonna burn out."*

**Four things follow.**

1. **The milestone is ACTIVE TRADE MANAGEMENT, not exit refinement.** The objective
   is net P&L *and capital utilisation* over the whole life of a trade — entry, the
   plan, and every revision of it. "Exit refinement" named one endpoint of that and
   is why every lever ever screened here can only **cut a trade short**: *extend the
   target* has no implementation anywhere in the harness or the live monitor. That
   is a missing capability, not a missing result.

2. **A bracket must carry an expectation at entry, or it is not a bracket.** *"Where
   do we expect this to exit, and why"* becomes a required output of the entry
   decision. **This is not what the fleet does today**, and the measurement is
   unambiguous (`docs/research/e35-bracket-is-not-a-decision-2026-08-20.md`, 6,428
   trades / 19 legs / 2021-08-16→2026-08-19 / net of fees): **16 of 19 legs declare
   `tp_r: 50.0`**, so the placed take-profit is `entry × 1.099` — *the exchange's
   rejection threshold*. Because that is a fixed fraction of price, `tp_R` and
   `ATR/close` are **the same variable** (collinearity `confirmed` 19/19, worst
   deviation 2.78e-17), the target distance varies **6.5×–38.9× within every leg**,
   and **76.2% of the fleet's net R comes from the 23.1% of trades whose target is
   more than 5 R away** — i.e. from trades the bracket cannot close.

3. **"Adjust the bracket" and "drop the trade" are not symmetric options, and the
   cost measurement already ranks them.** A round trip is **0.082–0.163 R** against
   a fee-free mean edge of **+0.1376 R** (XRP) / **+0.1167 R** (SOL), so exiting
   early eats most of a trade's edge, while **amending a resting level is not a fill
   and costs nothing**. Bracket revision may therefore be frequent; discretionary
   early exit must be rare and decisive. E3's one positive cell died precisely
   because it fired on 34–52% of trades.

4. **Revision must be conditioned on the strategy's OWN thesis.** The operator's
   momentum example is the specification: state at entry where the move is expected
   to exhaust, and if price nears the target while the exhaustion condition has not
   fired, extend. A revision rule that reads only the trade's own path is the same
   eleven-endogenous-feature substrate § 0.2 already identified as the root cause.

*Falsifier, unchanged in kind:* a predictive bracket is a **claim about where the
trade will exit**, so it is graded against realised exits — calibration first
(does the stated expectation match the observed distribution?), P&L second. A
bracket that improves net R while being systematically wrong about *where* trades
exit has not met this bar; it has found a different edge and should say so.

### E-ML · The ML track — named, not left to whoever gets to it

Second half of the same directive: *"we haven't really been … using or building new
MLs for this task, and maybe that's also something we need to consider adding into
the mix … in general as a way to push the AI side forward and specifically to help
us solve this problem."*

That reading is correct, and the reason is more specific than "we did not try":
**the rig exists and has only ever been pointed at a label that could not carry
it.** `scripts/research/analyze_exit_head.py` already implements grouped / purged /
embargoed walk-forward CV, uniqueness weighting, deflated Sharpe and PBO-via-CSCV —
and trains a binary take/skip head on `label_hold`, which
`docs/research/e3-barrier-geometry-2026-08-20.md` measured at **13.9% / 27.0% /
46.6%** barrier composition at h=12/24/48.

**ML-1 · the conditional barrier race.** Target `touch ∈ {tp, sl, time}` at
decision time. Legitimate where E3's stratified view was not: that view
*stratified* on `touch` (conditioning on a barrier the trade only reaches later),
whereas this *forecasts* it — `triple_barrier_forward` computes `touch` over the
strictly-future window `candles[t+1 : t+1+time_stop]`.
⚠️ **Precondition:** the label's barriers must be the ones the leg actually trades.
E2 labelled a 2.0 R upper barrier; the donchian/pullback fleet trades the 9.9% cap
(a *per-trade* R distance) and `ict_scalp` trades `tp_at_r: 1.5`. Neither is 2.0.
`BL-20260820-E2-LABEL-BARRIER-DOES-NOT-MATCH-THE-LIVE-EXIT-POLICY` must be closed
**before** this is measured, not after.

**ML-2 · the predictive bracket.** Regress the exit location/time an entry should
expect, and grade it on **calibration** before P&L (per E3.6's falsifier).

**ML-3 · the revision policy.** Given ML-1 and ML-2, when to move a level. Bound by
E3.6(3): revising a resting level is free, so this may act often; any variant that
crosses the spread inherits the 0.082–0.163 R charge and must clear it.

*Falsifier for all three, inherited unchanged:* E2's information test with a
shuffled-label control that is shown to fire, the E4 dispersion test, and net-of-fee
grading. **An ML head is subject to exactly the same bar as a hand-written lever**
— it does not get a softer one for being a model.

### E4 · Dispersion-test the verdict — split AND fold
`m20_split_dispersion.py` for the IS/OOS boundary; `m20_dispersion_rate` for fold offsets.
*Falsifier:* `split_sensitive: true` is a **refusal**, not a caveat. It does not proceed.

### E5 · Annotate-soak live — observe-only, declared default-off
The lever runs and logs what it *would* have done against what actually happened.
*Falsifier:* a soak with no rows is not a pass. State the accrual denominator and the date
the sample becomes decidable.

### E6 · Tier-3 declare
Operator approval against the specific numbers, with the dispersion band attached.

---

---

## 1.5 E-lit survey, round 1 (2026-08-20) — seeds, not evidence

**Read the source-quality column before reading anything else.** Roughly half of
what a search returns on this topic is vendor content whose backtest numbers are
unverifiable marketing. Those rows are kept because the *construct* is worth
testing here, not because the number is worth believing.

| tier | meaning |
|---|---|
| **A** peer-reviewed / working paper with a stated method | the construct AND the result are worth reading |
| **B** practitioner writing with a stated method but no reproducible artifact | the construct is worth testing; the number is a claim |
| **C** vendor blog / indicator marketing | the construct only. **Numbers from tier C do not get quoted in any artifact in this repo** |

### 1.5.1 Exit-quality metrics we do not currently compute — feeds E0

The E0 census counts exit REASONS. The practitioner literature measures exit
*quality*, which is a different question and the one the operator actually asked
("we gave back 2R"). Two standard ratios, neither of which exists anywhere in
this repo:

- **MFE capture rate** = realised exit profit / MFE. Tier B sources put "poor
  exit timing" below 50% and a healthy band at 65–80%; below 60% is read as
  systematically early exits. **On XRP 4163 this is 1.425 / 3.418 = 41.7%.** We
  have `peak_r` (a LOWER bound) on the live side and `mfe_r` in the harness, so
  this is computable today per leg — it just never has been.
- **MAE-to-stop ratio** = mean MAE / stop distance. Tier B reads below 0.6 as
  stops set too wide (risk booked that the trade never uses) and above 0.85 as
  well-calibrated-but-tight. This is the quantitative form of *"is the bracket a
  safeguard or is it the strategy?"*

Both go into E0 as census columns. Both are **descriptive** — a bad capture rate
does not by itself imply a lever exists, which is exactly what E2 is for.

### 1.5.2 Optimal stopping is a solved problem class — feeds E3 lever FORM

Tier A. The exit question has a formal literature under *optimal stopping*, and
its central result is structural rather than parametric:

- **Dai, Zhang & Zhu (2010), "Optimal Trend Following Trading Rules"**
  (`10.2139/ssrn.1630903`) — under a continuous-time regime-switching model the
  optimal policy is a pair of **threshold curves in the conditional probability
  of being in the bull state**, not a price level and not a fixed trailing
  distance. The exit boundary moves with the posterior, so the same price action
  exits or holds depending on inferred regime.
- The same literature's discrete analogue: under a **Markov** state model the
  optimal rule reduces to an EMA crossing, while under a **semi-Markov** model —
  where the probability of the state ENDING increases with its age — the optimal
  rule takes a MACD-like form. **That is a formal argument that time-in-state
  belongs in an exit rule**, and this fleet's `bars_in_trade` is a crude proxy
  for it that has never been paired with a state estimate.

The transferable claim is the FORM: *exit on a function of the posterior over
market state, with an age term*. That is a lever family E3 can express only once
E1 supplies a regime series — which is the second item on E1's candidate list.

### 1.5.3 Trailing-stop evidence — feeds E3, with a caution

- Tier A/B. **Kaminski & Lo (2014)** report that a trend-following stop rule cut
  maximum drawdowns by more than half with slightly higher returns.
  **Clare, Seaton, Smith & Thomas (2013)** report a 10% trailing stop improving
  Sharpe across equity markets and asset classes.
- Tier C sources report Chandelier-style exits beating fixed stops on expectancy
  and drawdown. **Those numbers are not quoted here** and are not evidence about
  this fleet; the construct is already implemented as `trail_decay`.

⚠️ **The caution is the interesting part.** Both tier-A results are primarily
**drawdown** results — the stop bought risk reduction, and return improvement was
secondary or slight. Our own gate (`beats()`) requires net_R improvement AND
maxDD no worse. **A construct whose literature support is drawdown-side will fail a
gate written net_R-first even when it is working as designed.** That is a gate
question, not a lever question, and it is now on the E4 agenda: the fleet may
need a declared drawdown-primary acceptance path, pre-registered, rather than
one universal `beats()`.

### 1.5.4 Labelling and validation — feeds E2, and validates the E2 design

Tier A. López de Prado's **triple-barrier** labelling (profit barrier / stop
barrier / **time barrier**) and **meta-labelling** (a primary model picks the
side, a secondary model predicts whether the primary was right, and its output
drives SIZE) are the standard framing for exactly the question E2 asks.

Two things follow:

1. **The E2 design as written is right, and for the documented reason.**
   Triple-barrier labels overlap in time, so ordinary k-fold leaks and inflates
   accuracy — purging and embargoing are mandatory, and average-uniqueness
   weighting corrects the redundancy. E2 already specifies purged/embargoed
   splits grouped by `trade_id`; this is the external confirmation that the
   grouping is load-bearing, not defensive decoration.
2. **Meta-labelling is a SIZING construct, not only an exit construct** — the
   secondary model's output is naturally a position size. That connects this
   thread to `CONVICTION_SIZING_MODE`, which is already live reductive-only on
   `bybit_1`. An exit head and a size head over the same widened panel are the
   same model wearing two hats; E3 should not design them as unrelated levers.

### 1.5.5 Correlation and portfolio-level exits — feeds E1 directly

- Tier B. Average pairwise correlation inside an equity index is reported around
  0.30 in normal conditions and **above 0.70 in selloffs** — i.e. correlation is
  itself regime-dependent, and a book that is diversified at entry is not
  diversified at the moment it matters. The practitioner framing is **portfolio
  heat**: cap total open risk and cap risk within a correlated cluster.
- Tier A. Crypto-specific: BTC leads both directions, with dominance rising in
  drawdowns; cross-chain spillovers are frequently **negative** (a surge on one
  chain coincides with declines on others), unlike equities — so a peer feature
  must carry a SIGN that is estimated, never assumed positive.

**This is the concrete E1 feature that the operator's own observation demands**
(short XRP held while long ETH at rho 0.88): not just the peer's return, but the
**current** pairwise correlation against its own history — a rolling correlation
z-score is the cheapest form. A live accessor is required before it is a feature
(E1's falsifier), and `comms/research/crypto_correlation_*.json` is a measured
starting matrix, not a live series.

### 1.5.6 Time stops — feeds E1 and E3

Tier B, but the framing is precise enough to test: choose a time barrier where
the **marginal reward decays faster than the marginal risk**, by fitting the
exit-rate curve against bar count. The stated distinction is that mean-reversion
payoff concentrates in early bars so a timer dominates, while trend-following
wants a wide trailing distance instead. Our fleet holds BOTH kinds and applies
neither rule deliberately — and `timeout` is 5 of 284 exits on `xrp_pullback_2h`,
i.e. effectively absent.

### 1.5.7 What round 1 did NOT cover

Named so the next round starts here rather than re-treading the above:
execution-cost-aware exits, options-implied signals as exit inputs (IV rank, term
structure, skew), order-flow / microstructure exit triggers beyond our two taker
features, funding-rate and basis as crypto-specific exogenous state, and
survival-analysis framings (hazard of adverse excursion) as an alternative
prediction target to forward R.

## 2. What this changes about the fleet

The coverage matrix currently records `honest_negative` for most pullback lever cells.
Those verdicts were produced at **E3 over an endogenous-only E1, with no E2**. They are
honest about what they measured and they do **not** establish that the leg cannot be
improved — only that path-only levers did not improve it. The matrix rows should carry
that qualification rather than reading as closed questions.

## 3. Execution order

1. **E0 census across the fleet** — cheap, uses the existing corpus; establishes per-leg
   whether an exit mechanism exists at all.
2. **E1 peer-symbol features first** — the correlation matrix is already measured and the
   operator's own observation (short XRP while long ETH at rho 0.88) is the motivating case.
3. **E2 on the widened panel** — the first honest answer to *"does anything in THIS panel
   carry information about forward R?"*
4. E3+ on whatever survives E2.

### 3.1 What a negative result means, and what it does not

A negative at any step means: **the constructs tried up to that point, over the substrate
available at that point, did not beat holding.** It is a statement about a tried set, with
a date and a corpus attached. It is never a statement that the leg cannot be improved, and
it never closes the thread.

The disposition after a negative is **regroup and widen**, in this order:

1. **Check the substrate before blaming the question.** §0.2 is the worked example: twenty
   lever cells returned negative, and the cause was that all 11 available features were
   endogenous. The levers were fine; the panel was empty of the thing they needed.
2. **Re-enter E-lit** for constructs not yet tried — different feature families, different
   lever forms, different horizons, different aggregation levels (per-symbol → per-regime →
   portfolio).
3. **Change the level.** A question that has no answer per-trade may have one per-portfolio
   (correlated exposure, heat, opposing legs) or per-regime. The pullback family's negatives
   are all per-trade negatives.
4. **Change what is being predicted.** Forward R on a held trade is one target. Time-to-
   adverse-excursion, probability of give-back beyond X, and capital-efficiency-adjusted
   hold value are different targets with different learnability.
5. **Record the conditions.** Every negative goes in the coverage matrix with corpus,
   commit, split band, and the substrate it was measured over, so the next attempt knows
   what was already covered and does not re-run it.

Sizing and diversification are a **parallel** track that is always worth improving. They
are not the fallback that gets accepted when a lever search returns negative — treating
them as the consolation prize is how a search gets abandoned one step before the substrate
gets fixed.
