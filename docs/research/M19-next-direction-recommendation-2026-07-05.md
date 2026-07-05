# M19 next direction — ranked, sequenced recommendation (2026-07-05)

**Brief:** [`M19-next-direction-deep-research-brief-2026-07-05.md`](M19-next-direction-deep-research-brief-2026-07-05.md)
(the drafting session's handoff brief, landed on `main` in #5593 minutes after
this session branched — this run answered it from the directive text plus the
same M19 record it cites; the reconciliation is recorded in
`S-M19-NEXT-DIRECTION-2026-07-05`).

**Verdict up front — the chosen priority order:**

> **D4 (mature fc→advisory, patiently) ▸ D1 (build the fc-geometry shadow-soak
> now) ▸ D2 (label-wall spike on the existing journal) ▸ D3 (stays
> trigger-gated, dormant).**
>
> The organizing principle, from both the external evidence and our own
> 07-05 backtest failure: **when the binding constraint is data that accrues on
> wall-clock, start every accrual clock first, then spend researcher/compute
> time offline while the clocks run — and stop iterating on offline simulators
> that have already failed reality-calibration.** D4's clock is already
> running (fc soak, ~96 preds/day/symbol). D1's clock does not exist until we
> build it — that makes the D1 build the highest-urgency *construction* item
> even though D4 is nearest to money impact. D2 is pure offline work on data we
> already have, so it fills the researcher time between. D3 has no active
> task-matched head to attach to and therefore no claim on this cycle.

Citation confidence key: **[✓3-0]** / **[✓2-1]** = adversarially verified
(3-vote refutation panel); **[◐]** = extracted verbatim from the primary source
but the verification pass was truncated by a rate limit — quote checked, not
independently refuted; **[internal]** = our own data/history (diag relays,
evidence docs, sprint logs). Sources are listed at the end.

---

## 1. Sub-question 1 — is a live observe-only soak the right instrument for exit-geometry (D1)?

**Yes — it is the standard instrument, and the literature says our failed
offline route is not just broken but counterproductive to keep polishing. One
material design caveat: counterfactual censoring.**

- Shadow-mode deployment — run the new logic on real production flow, log its
  outputs, never let it influence the decision — is the canonical ML-ops
  pattern for exactly this situation: validating against real inputs that a
  staging/offline environment cannot reproduce [◐ S3, S4]. That is the shape
  of our existing `exit_ladder_soak` and of the proposed fc-geometry soak.
- The case against continuing to iterate offline is quantified. In a cohort of
  888 real trading algorithms with ≥6 months of true out-of-sample
  performance, backtest metrics had almost no predictive value for OOS results
  (R² < 0.025) [✓3-0 S2]; and the same study found that *more* backtesting
  before deployment correlated with a *larger* IS→OOS Sharpe shortfall
  (Spearman R²=0.017, p<0.0001) [✓3-0 S2]. Bailey & López de Prado put it
  bluntly: a backtest whose search multiplicity is uncontrolled is "worthless,
  regardless of how excellent the reported performance might be" [✓3-0 S1], and
  repeated holdout/k-fold evaluation makes false positives *expected*, not
  unlikely [✓3-0 S1]. Our own anchor-arm result is the local instance: the
  forward triple-barrier engine misses realized outcomes by ~0.6R because live
  trades close on fees/monitor/flip/reconciler exits, not clean barriers
  [internal: `T0.4-fc-sltp-geometry-evidence-2026-07-05.md`]. Making that
  simulator faithful is a large build with a known failure mode (fitting the
  simulator to the past); the soak replaces it with ground truth.
- **The caveat the literature adds — and the one thing the soak design must
  handle:** shadow evaluation is only fully valid when the logged prediction
  does not need to *influence* the outcome to be scored. An alternative SL/TP
  is a counterfactual: its outcome can only be partially reconstructed from
  the placed order's realized price path [◐ S3]. Concretely: if the fc-scaled
  stop is *tighter* than the placed stop, the observed path usually resolves it
  (we see whether the tighter barrier was touched before the actual exit); if
  it is *wider*, the counterfactual trade may still be "open" when the real
  trade closes — the observation is **censored**. The soak logger must
  therefore (a) persist the bar path from entry until each counterfactual
  barrier resolves or a hard horizon, and (b) record an explicit
  `censored` flag instead of silently scoring truncated counterfactuals. A
  soak that ignores censoring re-imports the exact bias that killed the
  offline backtest.
- Duration norms: shadow-soak time scales inversely with event rate — high
  traffic needs hours, low traffic months [◐ S4]; Quantopian's allocation
  practice required ≥6 months of genuine OOS data [✓3-0 S2]. Our real-money
  trade rate is low (~350 lifetime), so **the soak is a months-scale clock —
  which is the argument for starting it now, not later**, and for logging it on
  every venue/account class (paper included, flagged) to multiply events.

## 2. Sub-question 2 — which label-efficiency techniques are evidence-backed at n≈200–350 (D2)?

**Meta-labeling is the one direction with a peer-reviewed, task-matched
evidence base; paper-trade labels are our cheapest real multiplier; barrier
labels are usable as *training* signal but our own evidence proves they are a
noisy proxy; generative augmentation is not supported at our scale.**

- Meta-labeling — a secondary ML layer over an existing primary strategy that
  sizes positions and filters false-positive signals — learns from the
  strategy's *own* trade outcomes rather than needing an independent labeled
  corpus [✓3-0 S5, S6, S7]. It has a four-paper JFDS methodological series
  behind it (theory/framework, architectures, calibration+sizing, ensembles)
  [◐ S7], a decomposition study showing each component individually improves
  strategy metrics [✓3-0 S5], and an E-mini futures study where event-based
  sampling + triple-barrier + meta-labeling improved two primary strategies
  out-of-sample [◐ S8]. "Inverse meta-labeling" explicitly targets increasing
  the *quantity* of side forecasts — i.e., manufacturing more labelable events
  [✓3-0 S6]. This is the closest published analogue to our conviction stacker,
  and it says the stacker's *frame* is right; what's wrong is its n.
- **The sample-size arithmetic is the real content of D2.** The classical
  ≥10-events-per-variable guideline [◐ S11] is itself unreliable as a fixed
  rule — required EPV in worked examples spans ~4.8 to ~23 [◐ S13, Riley et
  al.] — and AUC point estimates only approach unbiasedness with event counts
  in the hundreds-to-~1000 range [◐ S10]. Against that: the conviction head's
  n_eval=20 (5 positives) and the ranker's 214 labelable order-packages with
  16–32 embedding dims are **an order of magnitude under any defensible
  standard** [internal: `T0.3-conviction-embedding-evidence-2026-07-01.md`,
  `S-M19-FC-GRADUATION-PROGRAM-2026-07-04.md`]. No modeling cleverness fixes
  that; only more labels do.
- **Where more labels actually exist today:** the journal holds 3,179 trades /
  2,756 order-packages including paper (07-05 diag pull [internal #5613]) —
  roughly an order of magnitude more than the ~350 real-money rows, on the
  same strategies, same signal builders, same feature surface. The
  evidence-backed D2 spike is therefore: **train trade-outcome heads on
  real+paper pooled with an `account_class` domain flag, validate under purged
  CV, and hold real-money rows as the decisive evaluation slice.** Paper
  execution differs (fills, slippage, sizing), so the domain flag +
  real-only evaluation is mandatory, not optional.
- Triple-barrier labels over historical bars can manufacture unlimited
  labels, and the meta-labeling literature uses them [◐ S8] — but our own
  anchor-arm result shows barrier outcomes diverge from live outcomes by
  ~0.6R on this system [internal]. So barrier labels are usable as
  *pre-training / auxiliary* signal (cheap, plentiful, directionally
  informative) but must never be the evaluation target; the label a promotion
  decision rests on stays a realized trade outcome.
- Generative augmentation (GAN/synthetic paths) is not supported at our
  scale: the study proposing GAN-path evaluation itself cautions that at ~100
  training samples the GAN likely memorizes rather than learns the
  distribution, its fidelity was strategy-dependent even on synthetic
  ground truth, and it was never validated on real market data [◐ S9]. Skip.

## 3. Sub-question 3 — when do cross-asset/macro representations transfer (D3)?

**The clock-match hypothesis behind D3 is externally supported — but every
demonstrated win sits at data scales we do not have, so D3 stays trigger-gated
rather than becoming an active line.**

- Frequency/task matching is a real, measured phenomenon: TSFM representations
  transfer when the downstream series shares dominant frequency bands with
  pretraining, and degrade badly under spectral mismatch (MSE +10% to +78%
  across benchmarks for a frozen encoder off-band) [✓3-0 S12]. The paper's
  practitioner recommendation — **assess spectral overlap between corpus and
  target before deploying an embedding, adapt when overlap is low** [✓3-0
  S12] — retroactively explains the T1.2 negative (daily macro panel → 15-min
  volatility bursts) as an expected property, not an implementation defect,
  and gives D3 a cheap mandatory pre-check if it ever activates.
- Finance-specific TSFM evidence points the same way: generic time-series
  pretraining does not transfer to financial forecasting (Chronos-large
  zero-shot OOS R² −1.37%, TimesFM-500M −2.80%, both under CatBoost/LightGBM
  baselines) [✓3-0 S13]; fine-tuning doesn't close the gap [✓2-1 S13 — the
  dissenting voter correctly notes this is shown for daily excess-return
  forecasting, the near-zero-signal regime, and shouldn't be over-generalized];
  finance-native pretraining *plus data scaling* is what works [✓3-0 S13].
  Boosted trees remain the strongest baseline class any embedding must clear
  [◐ S13] — exactly our T1.1/T1.2 experience.
- The demonstrated cross-sectional wins are daily-clock AND data-rich:
  LambdaMART learning-to-rank roughly tripled Sharpe vs classical
  cross-sectional momentum (2.156 vs 0.696) — on ~40 years of NYSE daily data,
  monthly rebalance, 100 stocks per leg, price-derived features only, costs
  excluded [✓2-0 S14; horizon/universe detail ◐]. Contrastive daily
  co-movement embeddings beat baselines on industry classification and cut
  hedge-portfolio volatility (19.1% vs 23.8%) — on 611 stocks × 18 years
  [◐ S15]. Notably, the ranking win needed **no learned embedding at all**
  [◐ S14], and even in the successful embedding setup, transfer was
  task-dependent within the same clock [◐ S15].
- **Application:** our M18 ranker has 214 labelable rows and a thin live
  decision space (~24 multi-candidate ticks/5 days) [internal:
  `S-M19-FC-GRADUATION-PROGRAM-2026-07-04.md`] — three orders of magnitude
  from the regimes where these wins are demonstrated. D3's trigger stays as
  `MB-20260704-T12-SSL-NEGATIVE` defines it (a daily/cross-asset head becomes
  an active experiment, e.g. the ranker revival at ~500+ labels), now with the
  spectral-overlap pre-check added, and with the extra note that the
  literature's first lever at that point is hand-engineered features +
  LambdaMART, not the corpus embedding.

## 4. Sub-question 4 — what does a *powered* fc→advisory gate look like (D4)?

**The statistics literature converts "more soak" into a number: budget
positive-class events, not predictions or days — and the first honest target is
≈40–50 labeled volatile bars per symbol spanning multiple distinct episodes,
with logit-transformed AUC CIs; the RG4 first look at 48 labeled rows (and
effectively fewer positives) was structurally unpowered.**

- AUC estimation quality in rare-event settings is driven by the **absolute
  number of positive events**, not the event rate or total row count [◐ S10];
  sensitivity is governed by positives specifically [◐ S10]. At the shipped
  4.6% volatile base rate, positives are the scarce resource: at the observed
  ~96 preds/day/symbol [internal #5610: 199 preds/~48h BTC; #5611: 74/~18h
  ETH], expected positives ≈ **4.4/day/symbol**.
- Assurance-based sample-size planning for AUC (the correct frame for a
  promotion gate): even a *strong* classifier (true AUC 0.92) needs ~36
  positives + ~57 negatives to demonstrate a CI lower bound above 0.8 with 80%
  assurance, ~48 positives for 90% — and planning to an *average* CI width
  gives only 50% assurance [◐ S16]. With prevalence-fixed class ratio
  (r≈21 at 4.6%), the positive count is the binding driver [◐ S16]. Wald CIs
  are unreliable at these sizes; use logit-transformed intervals [◐ S16].
  Fixed-EPV shortcuts are explicitly discouraged in the modern prediction-
  model literature [◐ S13-stats/Riley]; near-zero AUC bias needs event counts
  approaching ~1000 [◐ S10] — that is the *robust*-verdict scale, not the
  first-read scale.
- Two system-specific corrections to raw bar-counting:
  1. **Episode diversity.** Volatile 15m bars cluster into episodes; the
     effective sample size is closer to the number of distinct volatile
     *episodes* than of volatile bars. A gate defined on bars alone can be
     "powered" by one long episode and prove nothing about the next one. The
     gate must require ≥ some minimum distinct episodes (proposed: ≥5 per
     symbol) alongside the bar count.
  2. **Mirror freshness.** The 07-04 RG4 read was doubly degraded: a ~19h-stale
     trainer mirror (48 labeled BTC rows, 0 ETH) on top of the rare class →
     `ANTI_PREDICTIVE / AUC=None`, noise-dominated [internal:
     `S-M19-FC-GRADUATION-PROGRAM-2026-07-04.md`]. A powered RG4 needs the
     shadow-log mirror fresh at read time — that is a small pipeline fix, and
     it gates everything else in D4.
- Shadow-promotion practice adds the comparative frame: the shadow model must
  match-or-beat the incumbent on the decision metrics before influencing
  production [◐ S3], with the train/serve-skew input check as a core soak
  deliverable [◐ S4] — which is precisely what RG4 is. External duration
  anchor: 6 months of OOS before *allocation* [✓3-0 S2]; advisory influence is
  a bounded, reductive step below allocation, so a shorter but *explicitly
  powered* gate is defensible — the power calculation, not a calendar, is the
  gate.
- **Concrete D4 evidence standard (proposed, recorded as
  `MB-20260705-FC-ADVISORY-READINESS`):** fresh mirror at read time; ≥40–50
  labeled volatile-class bars per symbol spanning ≥5 distinct volatile
  episodes; logit-CI AUC lower bound materially above the incumbent frozen
  detector's on the same window; plus the head-pinned money-gate walk-forward
  (scratch-registry step) — then, and only then, a Tier-3 proposal to the
  operator. At ~4.4 positives/day/symbol this is **≈10–14 more soak days
  minimum** (episode-diversity permitting) — i.e., mid-to-late July at the
  earliest, and the calendar is set by the market's volatile-episode supply,
  not by us.

## 5. Sub-question 5 — sequencing under a binding data constraint

- Portfolio-decision-analysis evidence: disciplined *prioritization* of
  candidate projects adds more value than refining per-project estimates
  [◐ S17] — rank cheaply, commit, don't over-analyze the ranking.
- Soak duration scales inversely with event rate [◐ S4] → **anything that
  accrues data on wall-clock is started before anything that is pure offline
  compute**, because starting it late delays the decision it feeds
  one-for-one, while offline work is time-shiftable.
- Iterating further on the unfaithful offline simulator has negative expected
  value per the backtest-overfitting evidence [✓3-0 S1, S2] — the fc-geometry
  question moves to live observation or it stalls.
- Applying benefit-per-cost [◐ S17]: D4's remaining cost is near-zero (a
  mirror-freshness fix + a re-run script) with the highest proximity to money
  impact; D1 is a small Tier-1/2 build that starts an otherwise-nonexistent
  months-scale clock; D2 is a medium offline spike with the highest ceiling
  (it attacks the binding constraint itself) but no wall-clock component; D3
  has no active target head and demonstrated wins only at unreachable data
  scales.

---

## The recommendation — next M19 execution line

**Priority order: D4 ▸ D1 ▸ D2 ▸ D3** — with D4 and D1 as *clocks* (start/keep
running, cheap active work) and D2 as the *researcher workload* between clock
reads.

| Seq | Item | Work | Tier | Expected first read |
|---|---|---|---|---|
| **1 (now)** | **D4a — un-degrade RG4**: fix trainer shadow-log mirror freshness (sync before read), script the powered re-run with the evidence standard above; keep the BTC+ETH fc soak accruing (zero marginal cost). SOL fc head optional widening if the producer extension is cheap. | small | Tier-1 (trainer-autonomous) | powered RG4 read ~mid-July (≈10–14 soak days for 40–50 positives/symbol across ≥5 episodes) |
| **2 (now)** | **D1 — build the fc-geometry shadow-soak**: exit_ladder_soak-shaped observe-only logger — per opening order, log placed SL/TP + fc-vol-scaled SL/TP + fc snapshot; resolve counterfactual barriers against the realized bar path with an explicit **censored** flag (the design requirement the shadow-mode literature adds); Tier-1 read endpoint. Nothing reads it back. | small/medium build | Tier-1/2 (observe-only; wiring into `execute.py` is the Tier-2 touch needing one operator OK) | months-scale accrual — which is exactly why it starts now |
| **3 (next research slot)** | **D2 — label-wall spike A (meta-labeling on the full journal)**: retrain the conviction/outcome heads on real+paper pooled with an `account_class` domain flag (≈2,700+ labelable rows vs 214), purged CV, EPV-disciplined feature budget (≤ n_pos/10 effective dims), calibration per the meta-labeling literature, **real-money rows as the held evaluation slice**. Barrier labels only as auxiliary/pre-training signal (proven ~0.6R off reality). | medium, offline | Tier-1 | one research session; promotion of anything it produces still soak-gated |
| **4 (dormant)** | **D3 — task-matched corpus head**: unchanged trigger (`MB-20260704-T12-SSL-NEGATIVE` — a daily/cross-asset head becomes active, e.g. ranker revival at ~500+ labels), now **plus a spectral-overlap pre-check** before any embedding work, and with hand-engineered-features + learning-to-rank as the literature's first lever at that point, not the embedding. | none now | — | on trigger |

**What this is not:** no fc→advisory promotion proposal now (RG4 unpowered —
the 07-04 ANTI_PREDICTIVE read is a watch-flag, not evidence either way); no
SL/TP geometry change (offline evidence void); no new GPU spend required
(all four items are CPU/observability; the $10/mo burst path stays idle unless
D2 spike A motivates a retrain, which fits in well under $1).

**Falsifiers / exit ramps:** if the powered RG4 comes back negative with
adequate power, that is a live train/serve-skew red flag — D4 halts and the fc
serving parity gets audited before any further soak is trusted. If D2 spike A
shows the paper-domain labels don't transfer (real-slice evaluation flat), the
label wall genuinely waits on account-sizing/label accrual and D2 closes until
then. If the D1 soak shows no edge after a powered sample, the fc-geometry
lever closes per `MB-20260705-FC-SLTP-GEOMETRY` resolution criteria.

---

## Sources

External (fetched + claim-extracted by the deep-research harness; verification
votes as marked):

- **S1** Bailey & López de Prado, *The Deflated Sharpe Ratio* — davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- **S2** Wiecki et al., *All that Glitters Is Not Gold: Comparing Backtest and Out-of-Sample Performance on a Large Cohort of Trading Algorithms* (Quantopian, 888 algos) — community.portfolio123.com (mirrored PDF)
- **S3** Gude, *Machine Learning Deployment: Shadow Mode* — alexgude.com/blog/machine-learning-deployment-shadow-mode/
- **S4** Samiullah, *Deploying ML Applications in Shadow Mode* — christophergs.com
- **S5** Joubert, *Meta-Labeling: Theory and Framework* — Journal of Financial Data Science (jfds.pm-research.com/content/early/2022/06/23/jfds.2022.1.098)
- **S6** *Meta-Labeling* series companion — JFDS 4(4) (jfds.pm-research.com/content/4/4/10)
- **S7** Hudson & Thames meta-labeling reproduction repo — github.com/hudson-and-thames/meta-labeling
- **S8** *Event-based sampling + triple-barrier + meta-labeling on E-mini futures* — arxiv.org/pdf/2209.04895
- **S9** *GAN/LSTM synthetic-path backtesting* — arxiv.org/pdf/2208.01614
- **S10** *AUC behavior in rare-event settings* — pmc.ncbi.nlm.nih.gov/articles/PMC12667734/
- **S11** *Sample size for sensitivity/specificity* — pmc.ncbi.nlm.nih.gov/articles/PMC6683590/
- **S12** Wang et al., *Frequency Matters: When Time Series Foundation Models Fail Under Spectral Shift* — arxiv.org/html/2511.05619
- **S13** Rahimikia, Ni & Wang, *Re(Visiting) Time Series Foundation Models in Finance* — arxiv.org/abs/2511.18578; plus Riley et al., *Minimum sample size for developing a prediction model* (via the same verification batch)
- **S14** Poh, Lim, Zohren & Roberts, *Building Cross-Sectional Systematic Strategies by Learning to Rank* — arxiv.org/pdf/2012.07149
- **S15** *Contrastive asset embeddings from daily co-movement* — dl.acm.org/doi/fullHtml/10.1145/3677052.3698610 (ICAIF)
- **S16** *Sample size for AUC with precision and assurance* — onlinelibrary.wiley.com/doi/10.1002/sim.7992
- **S17** Keisler, *Value of Project Prioritization in Portfolio Decision Analysis* — pubsonline.informs.org/doi/10.1287/deca.1040.0023
- also consulted: TSFM-vs-baseline equity study (arxiv.org/abs/2606.27100); CPCV/PBO study (sciencedirect.com/science/article/abs/pii/S0950705124011110); daytrading.com forward-testing primer; hudsonthames.org triple-barrier explainer

Internal (via diag relays + repo record):

- Diag pulls #5610 (BTC fc soak: 199 preds 07-03→07-05), #5611 (ETH fc soak: 74 preds), #5613 (journal: 3,179 trades / 2,756 order-packages), #5552 (07-04 baseline: 117 preds)
- `docs/research/T0.4-fc-sltp-geometry-evidence-2026-07-05.md` (the ~0.6R reality-calibration failure)
- `docs/research/T0.1-embedding-*-2026-07-01.md`, `T1.2-ssl-encoder-AB-evidence-2026-07-04.md`, sprint logs `S-M19-T1.1-DEEP-SEQUENCE-2026-07-02`, `S-M19-FC-GRADUATION-PROGRAM-2026-07-04` (the 3-for-3 representation negatives, RG4 first look, label counts)
- `docs/claude/ml-review-backlog.json` — `MB-20260704-T12-SSL-NEGATIVE`, `MB-20260705-FC-SLTP-GEOMETRY`

Method note: 5 search angles → 22 sources → 103 extracted claims →
3-vote adversarial verification; 21 confirmed, 1 killed (an over-general
TSFM-transfer phrasing; the narrower frequency-matching claims survived), 3
unverified when a session rate limit truncated the final verification batch +
synthesis — synthesis completed in the main session; truncated-batch claims
are marked [◐] throughout.
