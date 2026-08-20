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
and no lever built on it can work. **This step has never been run.** It is the reason
step E3 has been guesswork.

### E3 · Design levers over informative features — and COMBINE them
Only features that survived E2. Levers are swept **jointly**, not one at a time: the
single-lever sweeps cannot see an interaction, and *"exit when the thesis decayed AND the
peer already turned AND we are past the capital-efficient hold"* is not reachable by any
of them alone.
*Falsifier:* a combined cell must beat the best single cell by more than the added degrees
of freedom buy. State the comparison explicitly.

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
3. **E2 on the widened panel** — the first honest answer to *"is there anything to learn
   here?"*
4. E3+ only if E2 finds signal. **If E2 finds nothing, that is the answer** and it is worth
   more than another twenty cells: it would mean exits on this fleet are genuinely a
   risk-control problem, not a prediction problem, and the correct response is to size and
   diversify rather than to keep hunting a lever.
