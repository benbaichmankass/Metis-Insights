# AI-driven trader — research & testing plan (2026-07-19)

> Operator-directed. Companion to the ROADMAP research-week plan (07-20→07-26)
> and the M23/M24/M25 design docs; this is the LONGER-horizon program that the
> weekly plans draw from. Grounded in the evidence on file — every direction
> below cites what earned it or killed its predecessor.

## North star — what "truly AI-driven" means here

The trader's five money decisions — **whether** to take a setup, **how big**,
**how to exit**, **which** of several candidates gets the capital, and
eventually **what to trade at all** — made or materially shaped by learned
models with *verified live edge*, promoted through the operator's Tier-3 gate.
Not hand-tuned rules with ML as decoration, and not ML for its own sake: a
model earns authority only by beating the incumbent rule on net-R + drawdown,
out of sample, then live.

The **authority ladder** (each rung is a Tier-3 promotion):

```
observe (shadow logs)  →  advise (annotations)  →  gate (veto/filter)
       →  size (scale qty)  →  select (allocate)  →  generate (originate entries)
```

## Where we honestly are

- **ML influences real money in exactly ONE place today**: the BTC 15m
  advisory regime head driving the Design-A vol hard gate (live since
  2026-06-28; the 4-arm A/B showed the ML label decisively beats the frozen
  detector). That is the template for every future rung: shadow soak → powered
  RG4 → operator promotion → kill-switch.
- **A large shadow fleet** (~88 registry models) accruing track records; the
  fc quantile-forecast heads (BTC+ETH+SOL 15m) are the lead promotion
  candidates; ETH/SOL vol heads re-check ~08-01.
- **The closed negatives teach one lesson.** M18 allocator EV-selection ≈ dumb
  priority (OOS AUC 0.51); T1.3 learned ranker negative; T1.2 SSL-corpus
  embedding negative; M23 C2 regression < classification; symmetric conviction
  sizing failed (4.5× worse maxDD); chop-scalp negative; xsec momentum failed
  its gate. The common cause is NOT model class or features — it is **label
  volume/quality** (M23 P1's C1–C3 legs measured this directly: every lever
  capped at ~11 net-positive trades because the real eval book is 376 rows).
- **The cost-aware label (M24) is now accruing** go-forward (fees + funding
  timers live as of this week), and the R-aware classification target (C1) is
  the proven tool waiting for volume.

## Thrust 1 — Manufacture decision labels at scale (the binding constraint)

*Hypothesis: with per-cell n ≥ ~300 in-distribution labels, the already-built
meta-label machinery converts label volume into usable take/skip edge.*

| Experiment | What | Test / gate |
|---|---|---|
| L1 — 3-symbol pooling (running) | M23 P2: BTC+ETH+SOL harness replay + pooled real eval book | Population-matched gate (recomputed base rates) + C1 EV sweep; go/no-go for P3 |
| L2 — full-fleet coverage | WS-B candle shards → every closed trade path-resolves → eval book grows past crypto | Coverage % of closed trades; re-run P2 test on the wider book |
| L3 — paper-book labels | The soak paper accounts produce REAL market fills on the full roster daily. Test whether paper rows transfer as *train-side* labels (account_class as covariate, real-money rows remain the only eval book) | Fair A/B: train +paper vs −paper, same real-money holdout; adopt only on clean lift |
| L4 — Claude decision-grades as weak labels | Every /performance-review grades order packages (A–F, persisted). Test the grades as an auxiliary/pretraining target for setup-quality heads | Correlation of grade with realized net-R first; then aux-target A/B |
| L5 — net-R label swap | As M24 broker-truth fee coverage widens, re-target the meta-label heads from gross `won_r` to net-R | Re-run C1 sweep on net labels; the EV gate already prices cost |
| L6 — external corpus | Phase 2 stays **ToS-gated + operator-gated**; do not start | — |

## Thrust 2 — Harvest & prune the shadow fleet (discipline compounds)

*Months of soak only pay at the promotion gate; a roster full of dead heads
hides the live ones.*

- **M25 cadence**: powered-RG4 sweep per matured head → PROMOTE-PROPOSE /
  WAIT(+date) / NEVER packet, weekly. (RG3-pass-RG4-fail is the graveyard
  pattern — powered RG4 is the only bar that counts.)
- **Vol-gate expansion**: ETH/SOL 15m heads → advisory promotes the authored
  trend_vol OFF-cells from frozen-label to ML-label enforcement (the proven
  BTC template, zero new mechanism).
- **fc heads → advisory** unlocks two consumers at once: fc-geometry exits
  (M19 D1 soak already accruing decision-time snapshots) and fc features in
  entry heads (the fc-pcv variants).
- **Demote/retire sweep** every cycle: any head that RG4-fails or drifts with
  no fix path gets a verdict, not a lingering slot (drift-remediation skill
  governs fix-vs-demote).

## Thrust 3 — Climb the authority ladder

Each rung reuses the same rollout shape: **backtest gate → shadow/annotate
soak → powered eval → Tier-3 `*_MODE` flip → kill-switch rollback.**

1. **Size (nearest rung).** Conviction P4 reductive-only sizing
   (`CONVICTION_SIZING_MODE=apply`, direction=reductive) once the calibrator
   soak + a fresh backtest A/B pass — the symmetric variant is already a
   documented kill. News-influence downsize is the same shape.
2. **Take/skip (M23 P3).** Per-cell R-aware heads on the strongest cell only
   (within-cell edge, not the global pool that keeps failing). Gate: beats
   take-all on net-R AND maxDD in ≥⅔ walk-forward folds incl. trend years,
   per-fold AUC ≥ 0.55, then shadow. P5's live wiring is reductive-only
   (skip suppresses; never invents a trade).
3. **Select (M18 unlock).** The allocator stays PARKED until a P_win/net-R
   ranker demonstrably beats dumb priority OOS — the M21+M24 join (P_win
   trained on net-R labels) is the named unlock condition. Re-attempt only
   after L5 lands.
4. **Generate (the frontier — new program, propose-only).** Two candidate
   ML-native entry experiments, backtest-harness-first, paper-only for any
   live expression:
   - **G1 — fc-asymmetry entry**: enter when the promoted fc head's quantile
     spread (q10/q90 vs cost) prices an asymmetric move; exit via the
     existing ladder. Prereq: fc→advisory (Thrust 2).
   - **G2 — direction-head + meta-filter stack**: the btc-direction-15m
     family as signal source, M23 take/skip head as filter, conviction as
     sizer — the full ladder composed. Prereq: P3 head with a real edge.
   Gates: beat the best incumbent strategy per symbol on net-R + maxDD in
   purged walk-forward incl. 2022-class trend years; then bybit_portfolio
   paper mirror; real money only by operator promotion.
5. **LLM/agentic layer (observe-only track).** M13 insights + review grading
   stay observational; their route into the money path is as *labels* (L4) and
   as the operator's decision support — not as a live decision-maker until a
   measured edge exists.

## Testing methodology (standing, non-negotiable)

- **Purged walk-forward with embargo** for anything sequential; single
  time-aware holdout only for cheap first cuts.
- **Population-matched fair tests** (the M23 machinery): train and eval must
  span the same strategy×symbol population; base rates recomputed per book,
  never hardcoded.
- **Cost-aware EV gates**: selection experiments are judged on net-R at
  usable volume (≥10% coverage / ≥40 trades), not accuracy — the C1 lesson.
- **Powered RG4** before any promotion proposal (≥40–50 labeled
  volatile-class bars across ≥5 episodes for regime-family heads).
- **Paper-portfolio mirrors** (`bybit_portfolio`/`alpaca_portfolio`) as the
  last pre-money stage; account_compat_matrix before any account routing.
- **Kill criteria pre-registered** per experiment; negatives get written up
  (docs/research/) and closed — the negative-results discipline is why the
  label-wall diagnosis exists.

## Sequencing

- **Week of 07-20** — the approved six-workstream plan (ROADMAP): M25 harvest,
  WS-B coverage (feeds L2), M23 P2→P3 decision, M24 prep, strategy
  refinement proposals, maker-carry verdict.
- **Weeks 2–3** — L3/L4 label experiments; M23 P3 per-cell head (if P2/L1
  supports); M24 P3/P4 unlock as fee coverage crosses ~2 weeks; ETH/SOL
  advisory promotions if RG4 passes; G1 design doc once fc promotes.
- **Weeks 4+** — G1/G2 backtest gates; allocator re-attempt only if the
  ranker unlock condition is met; quarterly roster prune.
- **Review points**: /ml-review weekly drives promotions + this plan's
  experiment queue; /system-review rolls up; every Tier-3 flip comes to the
  operator as a packet with evidence + rollback.

## What we deliberately do NOT do

- No live wiring of any head below its gate (n≈11-trade positive regions are
  not tradeable — the C1 lesson).
- No new default-off `*_ENABLED` capability gates (Prime Directive; graduated
  influence ships as `*_MODE`).
- No external-corpus ingestion before the ToS review + distribution
  pre-check + operator go.
- No frontier-chasing while the label wall stands: model-class upgrades
  (bigger nets, new families) are LAST after labels, per the measured
  evidence.
