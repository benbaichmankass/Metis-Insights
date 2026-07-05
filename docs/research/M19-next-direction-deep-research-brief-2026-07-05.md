# M19 next-direction deep-research brief (2026-07-05)

> **Provenance note:** this brief was referenced by the operator's research
> directive of 2026-07-05 but had not been committed to the repo (nor had the
> ROADMAP "Next research directions" block it pointed at). It was reconstructed
> at run time from the directive itself plus the M19 record (the T0.1/T0.3/T0.4/
> T1.1/T1.2 evidence docs, the S-M19-* sprint logs, and the open ml-review
> backlog items), and is committed here so the recommendation report has a
> stable referent. The run's output is
> [`M19-next-direction-recommendation-2026-07-05.md`](M19-next-direction-recommendation-2026-07-05.md).

## Question

M19's representation frontier is closed as exhausted (three independent
negatives on price-only representation learning) and the T0.4 `fc`
quantile-forecast feature is the milestone's one durable win, now soaking at
shadow on BTC+ETH. What is the next M19 execution line? Rank and sequence the
four candidate directions against our data reality.

## The four candidate directions

| Direction | What it is | Tier | Gate | Binding constraint |
|---|---|---|---|---|
| **D1 — live fc-geometry shadow-soak** | Observe-only logger (exit_ladder_soak shape): per opening order, log the fc-vol-scaled SL/TP alongside the placed SL/TP; compare realized outcomes. Built because the offline 3-arm backtest failed its own reality-calibration anchor (real-realized −0.68R vs fixed-resim −0.06R, `MB-20260705-FC-SLTP-GEOMETRY`). | Tier-1/2 build (observe-only); any eventual geometry change Tier-3 | Soak shows a real net-R/maxDD edge under account rulesets before any Tier-3 proposal | Live trade rate (slow accrual clock) |
| **D2 — break the label wall** | Label-efficiency program for the trade-outcome heads (conviction n_eval=20; ranker 214 labelable order-packages; ~350 real closed trades): meta-labeling on the journal's own outcomes, paper-trade labels with a domain flag, triple-barrier labels over historical bars as auxiliary signal. | Tier-1 (offline research) | A trade-outcome head clears purged-CV with an EPV-defensible sample before any soak | Label volume + label quality (paper≠real; barrier-sim≠live) |
| **D3 — task-matched corpus-embedding head** | The only unexplored place the T1.2 corpus-SSL embedding might pay off: a head whose target lives on the corpus's daily/cross-asset clock (daily risk head, M18 cross-sectional ranker) — per the representation-target clock-mismatch root cause of the T1.2 negative (`MB-20260704-T12-SSL-NEGATIVE`). | Tier-1 (offline) | corpus_emb beats BOTH the no-embedding and frozen-Chronos baselines on a task-matched head | A daily/cross-asset head being an active experiment at all (it isn't — ranker deferred at 214 labels) |
| **D4 — mature fc→advisory** | Graduate the validated fc vol-regime head from shadow to advisory: volatile-episode soak coverage + a powered fresh-mirror RG4 (first look was 48 labeled rows, ANTI_PREDICTIVE/AUC=None — noise, and a watch-flag) + a head-pinned money-gate walk-forward. | Tier-3 money gate (operator) | Powered RG4 positive + walk-forward PASS + operator approval | Volatile-episode accrual (~4.6% base rate → positives clock) |

## Data reality (anchors)

- **Label scarcity:** ~350 real-money closed trades lifetime; 214 labelable
  order-packages (07-04); conviction head n_eval=20; journal at 3,179 trades /
  2,756 order-packages total incl. paper (07-05 diag pull).
- **The fc win:** `btc-regime-15m-lgbm-fc-pcv-v1` at shadow since 07-03 (199
  preds/~48h as of 07-05, per-15m-bar cadence ≈96/day/symbol), ETH head added
  07-04 (74 preds/~18h). Purged-CV lift survives at the production threshold;
  direction heads clean-negative.
- **Price-representation frontier exhausted 3-for-3:** T0.1 frozen-TSFM
  embeddings marginal at the production base rate; T1.1 TCN below the tree on
  both metrics; T1.2 corpus-SSL clean negative across two corpus widths.
- **Compute is slack:** ~$10/mo GPU burst path proven end-to-end (~$0.04/run);
  the free trainer clears the daily cycle. Labels, not compute, bind.
- **Offline exit re-simulation is unfaithful:** the triple-barrier forward
  engine misses realized outcomes by ~0.6R (live closes are fees/monitor/flip/
  reconciler exits, not clean barriers).

## The five sub-questions

1. **Shadow-soak validity (D1):** is a live observe-only soak the
   methodologically sound instrument for evaluating exit-geometry changes when
   offline re-simulation fails reality-calibration — and what are its known
   limits (counterfactual censoring) and duration norms?
2. **Label efficiency (D2):** which techniques are evidence-backed for tiny
   financial trade-outcome datasets — meta-labeling, triple-barrier labels,
   transfer, semi-supervised, augmentation — and what are their pitfalls at
   n≈200–350?
3. **Cross-asset transfer (D3):** when do cross-asset/macro representations
   transfer (frequency/task matching), and what does the evidence say about
   cross-sectional ranking with learned embeddings at our data scale?
4. **Powered promotion gates (D4):** what statistical standards govern
   validating a rare-class (~4.6% positive) classifier's live discrimination
   (AUC CIs, events-per-variable, positive-event counts), and what do
   shadow-deployment promotion gates look like in practice?
5. **Sequencing:** under a binding data constraint (labels/episodes accrue on
   wall-clock, compute is slack), what ordering of D1–D4 maximizes information
   gain per week?

## Constraints on the run

Tier-1 only — no `src/`, `config/`, `ml/`, or live-path change; GPU spend stays
gated; fc→advisory must NOT be proposed for promotion without a
volatile-episode soak + a powered fresh-mirror RG4
(`MB-20260705-FC-ADVISORY-READINESS`).

## Method

External literature via the deep-research harness (5 search angles → 22
sources → 103 extracted claims → adversarial 3-vote verification; 21 claims
confirmed, 1 killed, verification of the last batch + synthesis truncated by a
session rate limit and completed in the main session), plus our own data pulled
live over the diag relays (#5610 BTC fc soak, #5611 ETH fc soak, #5613 journal
counts) and the M19 evidence-doc record.
