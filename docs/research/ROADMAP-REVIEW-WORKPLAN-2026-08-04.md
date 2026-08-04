# Roadmap Review + Prioritized Workplan — 2026-08-04

> **Operator-requested full roadmap review + forward workplan.** Three focus
> areas — **technical strategies**, the **macro sleeve**, and the **ML roster** —
> that the operator explicitly asked be treated as **converging into one more
> robust system** (a "master AI model" at the top, and mutual support at every
> level of decision-making), not three parallel tracks.
>
> **Method:** built on the live-verified [`S-ROADMAP-STATUS-REVIEW-2026-08-01`](../sprint-logs/S-ROADMAP-STATUS-REVIEW-2026-08-01.md)
> baseline (diag-relay #8266, 15 endpoints) + three deep read-only research
> passes over `config/strategies.yaml`, `ml/configs/`, the decision-path code
> (`intent_multiplexer` → `intents` → `coordinator` → `conviction*` →
> `allocator_ev`), `ROADMAP_MACRO.md`, and the three review backlogs. Every state
> claim is **[verified-live 08-01]**, **[repo-record]** (a same-week live-verified
> sprint log / research doc), or **[code]** (read directly this session). Tier-1,
> docs-only — no `src/`, `config/`, order-path, or VM writes.

---

## 0. The one-paragraph answer

The system is **operationally healthy and world-class at grading and killing bad
ideas**, but it has **exactly one proven, deployed edge — the BTC (now +SOL) ML
vol-gate** — and a real-money book that is modestly negative (bybit_2: −$24.62 /
30d; −$262.52 wallet-truth lifetime). The research frontier is now **mostly
honest-null**: value, COT, crypto-funding, energy-events, gas system-dynamics,
the allocator ranker, meta-labeling, symmetric conviction sizing, and every
mean-reversion/fade strategy have all been **disproven on real evidence**. The
strategic conclusion is that **the next level does not come from more strategies,
more models, or more macro tests in parallel — it comes from building the
convergence layer that makes the pieces we already have support each other.**
That layer has **two keystones** (§2) that between them unblock five stalled
programs, and the three focus areas are the tributaries that feed and are fed by
it (§3–§5).

---

## 1. Where we actually stand (verified)

### 1.1 Live health & result [verified-live 08-01]
- Trader / web-api / bots all `active`, on current `main`, heartbeat running; VM
  cpu ~5% / mem ~11% / disk ~38%; zero alert banners; DB single-inode clean
  (trades 4309, order_packages 3460, signals 1.3M).
- **bybit_2 is the only live-money account.** 7d: 10 trades, 20% win, −$6.99
  (PF 0.60). 30d: 34 trades, −$24.62 (PF 0.65). Loss concentrated in
  `eth_pullback_2h` / `xrp_pullback_2h` / `trend_donchian`. Authoritative
  lifetime wallet-truth −$262.52 (as_of 2026-07-13, ~3wk stale).
- **Real-money 7d/30d `pnlCoverage` 90% / 88% measured** — the provenance
  overhaul is holding; real-money numbers are now trustworthy. **All paper/demo
  per-row PnL remains fabricated at scale (65% of Jul closed rows) — do NOT tune
  on it.**

### 1.2 Milestone posture (condensed from the 08-01 table)
| Bucket | Milestones |
|---|---|
| **✅ Closed / shipped** | M0–M5, M7, M11, M13(S1+S2), M15 platform migration, provenance overhaul, 3-repo provenance surfacing, full-system-audit P0–P2 |
| **🔄 Genuinely open** | M6 web UI (incremental), M12 android P2b (WS push, not built), **M16 Unified Confidence** (observe-first), **M20 exit** (ladder+heads remain), M24 net-R (P3/P4 blocked on cost coverage), M25 ML promotion (fc-pcv v2 swap **now COMPLETE** 08-04), M27 scalp (regime-tune) |
| **📋 Dormant / not-started** | M21 entry refinement (paused since 07-14), M30 deep quant-research platform (prompt ready, not started) |
| **⛔ Disproven — do NOT re-open without a NEW input** | M18 allocator *selection* (ranker AUC≈0.51), M19 new model *types* (emb/TCN/SSL), M22 pairs (not real-money-viable), M23 meta-labeling (label wall), M28 value *standalone book*, M29 gas system-dynamics, cross-sectional momentum, symmetric conviction sizing, mechanical exit levers on trend/pullback, macro-M1 energy-surprise edge (survey-consensus null) |

### 1.3 The three focus areas, as-found

**Strategies — the roster has already self-pruned to two survivors.** 57 config
cells collapse to ~9 types on 4 shared engines. **Every mean-reversion and fade
member has been killed or demoted on live evidence** (vwap killed, turtle_soup
shadow, fade_breakout_4h −86R, fvg_range dormant, squeeze real-money-demoted,
htf_pullback/ada/avax demoted off bybit_2). The two net-positive survivors are
**Donchian trend** (the +52R flagship, `trend_donchian` BTC) and **ICT scalp**
(`ict_scalp_5m` BTC, 59% WR pre-live). The recurring failure mode is *"OOS
expectancy halves + month-concentrated"* — backtest winners that don't survive
live. [repo-record + code]

**Macro — extensively tested, almost entirely honest-null, because every test
used the weakest possible construction.** Value (D1–D5 all sub-threshold as a
standalone long-biased book), COT positioning (no predictive horizon),
crypto-funding/OI (no edge at gate), energy event-study (survey-consensus null,
wrong-sign IC), gas system-dynamics (no mechanistic edge over static). The common
thread the operator correctly identified: **these were single-signal,
trailing-percentile, contrarian, standalone tests fired off without an upfront
research framework for which macro indicators matter or how a macro view becomes
a trade.** The M28 08-02 correction already points the way: value/macro is *"a
validated-but-weak lead"* whose right expression is **a conditioning/overlay
input, not a standalone book.** [ROADMAP_MACRO.md]

**ML — a long roster, almost none of it doing anything.** 89 active manifests,
all trained every daily cycle. **61 are `candidate`/`research_only` — the shadow
factory refuses them, so they train daily but never log a prediction and never
touch an order.** **Exactly one model class influences a live order: an
advisory-stage regime head via the vol-gate (BTC, and SOL-as-observer).**
Everything else — conviction, allocator EV, exit heads, fc-geometry, news
influence, meta-labeling — is observe-only, parked, or data-walled. This narrow
influence surface *is* the "the MLs feel unimportant" problem. [code + ml-review-backlog]

---

## 2. The organizing thesis — one spine, two keystones

The operator's framing is the correct one: these must **support each other**. The
research this session makes the mechanism concrete. The current decision pipeline
is **a serial chain of independent, mostly-reductive, mostly-observe-only hooks**,
and the single most important finding is:

> **The fused conviction number (`c_strat ⊕ c_setup ⊕ c_wr ⊕ c_reg`,
> `src/runtime/conviction.py::compute_conviction`) is stamped onto every order
> package and then read by nothing on the order path.** The operator's
> "one basis for risk" is already computed on every decision — and thrown away. [code]

Everything converges on **two keystones**:

### Keystone A — the conviction / P_win / EV head (the master-model core)
One well-built, honestly-labeled, **net-of-cost** P_win/EV head is the single
input that unblocks **five** stalled things at once:
1. It's the value `apply_conviction_sizing` needs to make conviction **advise
   size** (reductive-first) — closing the orphaned-blend gap.
2. It's the **exact missing "proven P_win input"** the M18 allocator was parked
   for — turning a dead milestone into a testable one.
3. It's what **M23 meta-labeling** was reaching for.
4. It lets the intent aggregator resolve conflicts by **conviction instead of a
   static priority integer** (`DEFAULT_PRIORITIES`) — the real convergence point
   becomes confidence-driven.
5. Wired as the `c_reg` lens, it makes the **same regime head feed both the gate
   and the conviction blend** — one model supporting two stages. [code, §(C) convergence agent]

### Keystone B — the real-live-label wall
Every *learned* decision head (conviction-meta, trade-outcome, setup-quality,
M23, the exit head) died on the **same** wall: ~78–400 real labels. The fix is
**shared infrastructure**, built once, feeding every head — not re-hit per
project:
- the now-honest `provenance.py`-graded closed trades (`totalPnlMeasured`,
  `pnlProvenance`);
- the **L3 paper-book eval population** (`event_source='live_paper'`, breaks the
  ~376-row wall — M30);
- **per-trade backtest augmentation** (`MB-20260530-001`, open since **May**).

Everything below is a tributary of this spine: **strategy** work produces the
cleaner labeled decisions Keystone B feeds on and the confidence Keystone A
blends; **macro** becomes the `c_macro` conditioning lens into Keystone A rather
than a standalone book; **ML** cleanup clears the noise so A and the OFF-cell
wiring can land.

---

## 3. Focus area I — Technical strategies (new ideas, not tuning)

**Principle (from the evidence):** the roster has *already disproven* standalone
mean-reversion/fade on this system. So the highest-leverage new ideas are
**structural hybrids that harden the two survivors** using components that
already exist and are individually validated — each fusing ≥2 existing pieces,
each with a ready backtest harness. Ranked by promise/effort:

| # | Hybrid | Fuses | Hypothesized edge | Validate with | Effort/Tier |
|---|---|---|---|---|---|
| **S1** | **FVG-confirmed Donchian breakout** | `trend_donchian` entry + `ict_scalp` FVG + ≥1.3×ATR displacement filter | Structural false-breakout filter; directly targets the `trending/volatile → off` cell's failure mode | `backtest_trend.py` + `fvg_confirm` flag, 3-fold WF vs conf-0.70 baseline on BTC | Med / T3 |
| **S2** | **Vol-gate the real-money pullbacks** | advisory regime vol head + `eth/xrp_pullback_2h` | Extend the *one proven edge* to the decaying real-money performers | `regime_cell_walkforward.py` per-symbol; author OFF cells (no-cosmetic-cell rule) | Low / T3 |
| **S3** | **Ship the killzone/HTF-hardened ict_scalp** | `hf_displacement_cont.py` (already built, unwired) levers → live ict_scalp | Lift WR 37→45%, attack ict_scalp's fee-load (its one real weakness) | Clean OOS + WF on the existing IS-tuned config | Low-Med / T3 |
| **S4** | **Cross-asset (BTC-lead) gate for alt scalps** | `xa_*` peer features + `ict_scalp` on ETH/SOL/XRP | Kill counter-BTC alt scalps (alts 0.7–0.9 corr to BTC) | `backtest_ict_scalp.py` + xa-bias gate | Low-Med / T3 |
| **S5** | **VWAP-anchored partial exit for ict_scalp** | ict_scalp entry + rolling-VWAP scale target + Chandelier trail on remainder | Attack the fixed-1.5R-TP fee drag (~0.20R/trade) | `backtest_ict_scalp.py` + VWAP-partial lever | Med / T3 |
| **S6** | **Unified 4h vol-cycle sleeve** | `fade_breakout_4h` ⇄ `squeeze_breakout_4h` regime handoff (uncorrelated −0.05) | One regime-switched book: fade while compressed, flip to squeeze on expansion | combined state-machine harness | Med-High / T3 |

**Convergence hooks (why these serve the spine, not just themselves):**
- S1–S4 each **improve the confidence signal** the survivors emit — richer,
  better-calibrated `confidence` is a direct input to Keystone A.
- Every hardened live/paper leg **produces more honest labeled decisions** →
  feeds Keystone B.
- S2 is literally *"give the one proven ML edge more strategies to gate"* —
  widening ML prominence at the strategy level.

**Do first:** S1 and S2 (highest leverage; S2 is pure evidence+config on an
already-built ML capability). **Do not** build new standalone MR strategies —
that class is disproven here.

---

## 4. Focus area II — Macro sleeve (framework & deep research FIRST)

The operator's directive is explicit and correct: **plan and deep-research before
firing off more tests.** The evidence backs it — five null sleeves, all from the
same weak recipe. The work here is a **research-design phase**, not more producers.

### 4.1 The reframe (from standalone book → conditioning lens)
Adopt the M28 08-02 correction as the organizing principle: **macro's job is to
CONDITION the master model, not to trade a standalone book.** A weak-but-real
signal (value `change`/composite calibrates positively; crypto-funding pays at 1d;
COT leads at 90d) is worthless as a long/short book vs all-long, but is exactly
the right shape for a **`c_macro` conviction lens** and a **regime/vol
conditioner** feeding Keystone A. This is the convergence the operator wants:
macro stops running in parallel and starts supporting the trade decision.

### 4.2 The framework to build (before any new test)
| # | Deliverable | Why |
|---|---|---|
| **MA1** | **A written macro research-design doc**: the indicator universe (rates/curve, real-yields, DXY, credit spreads, funding/basis/OI, positioning, energy-storage, liquidity/reserves), the a-priori *mechanism* for each (why it should predict *this* instrument), the horizon it should act on, and the **pre-registered gate** — before touching data. Kills the "fit noise to a story" failure. | The missing upfront framework the operator named |
| **MA2** | **A signal→conviction translation harness** (the piece M28 still owes): conviction-weighted, net-of-cost **portfolio PnL** (Sharpe/maxDD/turnover), not just IC. IC≠PnL is why weak-but-real signals looked dead. | Turns the honest-null program into a fair test of the *conditioning* expression |
| **MA3** | **Construction-space search on existing inputs** (the D1–D4 dimensions M28 already scoped): change/impulse, divergence, conditioning, cross-section, composites — graded through MA2 as a **conditioning input to an existing strategy**, not standalone. | The untested expressions the 08-02 correction named as "continue-building" |
| **MA4** | **Fix `BL-20260730-EIA-SERIES-IDS-NOT-FRED`** (the one live macro line with a positive signal — energy-event tracking, Spearman 0.59) so the calendar populates. Tier-1, ready now. | Cheap unblock of the only non-null macro lead |

### 4.3 What NOT to do
- **Do not** re-litigate the energy-surprise → forward-return edge — it is a real
  null on real survey consensus at 1/3/5/10/21td, wrong-signed. Only a *new
  hypothesis* (different horizon, conditioning, non-surprise formulation) reopens it.
- **Do not** ship another standalone trailing-percentile contrarian sleeve.
- **Do not** build new producers until MA1 (the design) and MA2 (the PnL harness)
  exist.

---

## 5. Focus area III — ML roster (clean up, then unlock the next level)

**Two facts frame everything:** (1) only the vol-gate reaches an order — the
influence surface is one model wide; (2) 61 of 89 manifests train daily and do
nothing. The plan: **clear the noise, wire the heads you already have, then build
the one head that widens the whole surface.**

### 5.1 Roster cleanup — KEEP / DROP / SOAK
**KEEP + build out (~10):** BTC 15m `fc-pcv-v2` (the live edge) + v1 rollback pin;
SOL 15m `fc-pcv-v2` (observer) + ETH 15m (retrain→promote); one champion+challenger
per symbol at 15m; the M20 `exit-policy-v1` head; MES 15m `-v2` (gate-ready, needs
an OFF-cell).

**DROP → `ml/configs/retired/` (~35):** all `*-emb-*`/`*-corpusemb-*`/`*-fcemb-*`
+ `corpus-ssl-encoder-mae-v1` (SSL is a **closed negative**, `MB-20260704-T12-SSL-NEGATIVE`);
the 3 TCN heads (never beat LGBM → also lets the whole `market_sequences` family
go); 5 HMM heads; the degenerate baselines (`f1=0` by construction); 9 of 10
`setup-candidates-metalabel-*` (M23 NO-GO); MES 5m/1d/yz dupes; vt-pin threshold
arms. **Net: ~89 active → ~25–30.** Tier-1 housekeeping; frees the 1-OCPU trainer
(the `MB-20260719` OOM is a direct symptom of bloat) and kills the ML-review
alarm-fatigue.

**SOAK / decide (don't drop, don't build yet):** ETH 15m (retrain first),
fc-geometry (let the live soak accrue; stop polishing the offline sim),
conviction-meta (data-walled), order-flow/VPIN (data-blocked, genuine future
edge), allocator ranker (parked pending the P_win head below).

### 5.2 The "next level" — widen what a model is allowed to do
The ceiling is **not model quality; it's the narrow influence surface + the label
wall.** Ranked:

| # | Move | Unblocks | Data | Gate |
|---|---|---|---|---|
| **ML1 — the EV/P_win head (Keystone A)** | The master-model core: conviction-sizing, **M18 allocator selection**, M23, conviction arbitration, `c_reg` | honest-provenance closed trades + L3 paper-book (`live_paper`) + per-trade backtest rows + M24 net-R label | net-of-cost purged WF-CV, `oos_edge` on measured-provenance population only; allocator P2 behind a Tier-3 flip |
| **ML2 — author the missing regime OFF-cells** | Makes gate-ready heads (MES 15m, BTC 1h) actually *bite* — zero new training, pure wiring | `regime_cell_walkforward.py` + regime-debt matrix | WF proves a money-losing cell that **generalizes** (no-cosmetic-cell) |
| **ML3 — graduate the M20 exit head to advisory** | A **second** order-influencing surface (proactive profit-banking, operator-requested) | live closed paths + `--emit-trades` volume + exit-ladder/fc-geometry soaks | truncation-replay vs shipped hard levers, then candidate→shadow→advisory + Tier-3 |
| **ML4 — break the label wall as shared infra (Keystone B)** | The precondition under ML1 + the whole learned-decision family | L3 paper-book population + per-trade backtest augmentation (`MB-20260530-001`) | BH-FDR + positive OOS discrimination under purged WF-CV |
| **ML5 — conviction fusion (v1→v2 learned)** | The "master trader" endpoint: regime + P_win + exit heads → one score | the advisory class-prob vector + ML1 + ML3 as lens inputs | per-lens soak gate; **reductive-only** (symmetric already failed 4.5× maxDD) |

**Sequence:** 5.1 cleanup (now, parallel) → ML2 (days, wiring) → ML4 (label infra)
→ ML1 (the keystone) → ML3 (exit head) → ML5 (fusion). ML6 (sequence models on
now-honest data) is deferred, evidence-gated.

---

## 6. The convergence spine — the master model, sequenced

This is the through-line the operator asked for. The master model is **not a new
model to build from scratch — it is the act of connecting pieces that already
exist.** Six steps, most already built and merely *un-wired*:

```
[LIVE today]   strategy signal ──▶ intent multiplexer ──▶ aggregate_intents ──▶ RiskManager size ──▶ order
                                                            │ (static priority integer)   │ (confidence NOT read)
                                                            ▼                              ▼
[ORPHANED]     conviction blend computed here ─────────────┴──────────────────────────────┘  ← read by nothing
```

| Step | What it is | State today | The move |
|---|---|---|---|
| **C1** | **Reductive conviction sizing on demo** | `apply_conviction_sizing` fully built, `reductive` direction, daily-loss clamp, allowlist; `CONVICTION_SIZING_MODE=off` | Flip to `apply/reductive` on `bybit_1` (demo, no money at risk); A/B in the sizing-normalized harness. **Zero new code.** T3 flag on demo. Highest leverage / lowest risk. |
| **C2** | **Conviction-driven conflict resolution** | `conviction_arbitration` runs + logs every tick (observe-only) | Read the soak log; graduate `aggregate_intents` to pick the conflict winner by conviction, gated on demo. Makes the *convergence point itself* confidence-driven. T3. |
| **C3** | **P_win = the conviction blend → un-park the allocator** | `candidate_ev_score` uses raw `c_strat` as `P_win`; the richer blend isn't passed in | One-line swap to the stamped blend (or ML1's EV head), re-run `allocator_ranker_eval.py`. Converts a parked milestone into a testable one. T1 until an A/B passes. |
| **C4** | **`c_reg` lens — regime head feeds conviction too** | `c_reg` needs a `regime_alignment` calibrator, unfit; the head only drives the gate today | Fit the calibrator offline (`fit_confidence_calibrators.py`). Same model → gate **and** conviction. T1 to build. |
| **C5** | **`c_macro` lens — macro conditions the decision** | macro runs as a standalone (null) book | §4's MA2/MA3 output feeds a `c_macro` conviction lens. Macro stops running in parallel. T1 to build, T3 to weight. |
| **C6** | **Learned fusion (the master model)** | `conviction-meta-v1` trained but data-walled at coin-flip | Depends on ML1 + ML4 + C1–C5. Promote v1 formulaic → v2 learned once the label wall is broken. Reductive-only first. |

**The single most important, lowest-risk first move is C1** — it makes the
already-computed conviction number *do something* for the first time, on demo,
with zero new code. Everything else builds on that being real.

---

## 7. Prioritized workplan (waves)

Each item: **objective · Tier · first action · done-condition.** Ordered
ready-now → gated. Cross-refs to §3–§6.

### Wave 0 — Ready now, no gate, high leverage (do these first)
- **W0.1 · ML roster cleanup (§5.1).** T1. Move the ~35 dead/disproven manifests
  to `ml/configs/retired/` with a README rationale each. Done: daily cycle trains
  only wired-or-promising heads; trainer OOM pressure relieved; `~89→~25–30`.
- **W0.2 · Read the two observe-only soaks the master model depends on.** T1.
  Pull `conviction_arbitration.jsonl` (how often would conviction disagree with
  the priority table, and does it win?) + `conviction_sizing.jsonl` (would-be vs
  actual size). Done: an evidence base to size C1/C2 before flipping anything.
- **W0.3 · Fix the macro energy-event series IDs (§4.2 MA4 / `BL-20260730-EIA-SERIES-IDS-NOT-FRED`).**
  T1. Correct the EIA-vs-FRED series mapping; re-run the producer. Done: the one
  positive-signal macro line populates.
- **W0.4 · S2 evidence: vol-split the real-money pullbacks (§3).** T1 (evidence;
  the cell change stays T3). Run `regime_cell_walkforward.py` on
  `eth/xrp_pullback_2h`. Done: a walk-forward verdict on whether an OFF-cell
  generalizes — the highest-leverage extension of the one proven ML edge.

### Wave 1 — The convergence keystone begins (mostly built, needs wiring + a flag)
- **W1.1 · C1 — reductive conviction sizing on demo (§6).** T3 (demo flag). Flip
  `CONVICTION_SIZING_MODE=apply/reductive` on `bybit_1`; A/B in the
  sizing-normalized harness. Done: the orphaned conviction number advises size on
  demo, measured. **The single most important first move.**
- **W1.2 · C4 — fit the `c_reg` calibrator (§6).** T1. Fit `regime_alignment`
  offline so the regime head feeds conviction, not only the gate. Done: `c_reg`
  is a live lens input (observe) — one model, two stages.
- **W1.3 · S1 backtest — FVG-confirmed Donchian (§3).** T1 (evidence). Add the
  `fvg_confirm` lever to `backtest_trend.py`; 3-fold WF vs baseline on BTC. Done:
  a go/no-go on the highest-promise strategy hybrid.
- **W1.4 · Macro research-design doc MA1 (§4.2).** T1. Write the indicator
  universe + per-indicator mechanism + pre-registered gate, *before* any new
  producer. Done: the upfront framework exists; no macro test runs without it.

### Wave 2 — The keystone head + widening the ML surface
- **W2.1 · ML4 — label-wall infrastructure (§5.2, Keystone B).** T1. Wire the L3
  paper-book population + per-trade backtest augmentation as one shared labeled
  corpus. Done: the decision-head training population is no longer ~78–400 rows.
- **W2.2 · ML1 — the net-of-cost EV/P_win head (§5.2, Keystone A).** T1 to build /
  T3 to influence. Train on the W2.1 corpus; gate on `oos_edge` over the
  measured-provenance population. Done: a P_win head that clears the net-of-cost
  purged WF-CV gate — the input under C1/C2/C3.
- **W2.3 · C3 — un-park the allocator with the real P_win (§6).** T1. Swap ML1's
  head into `candidate_ev_score`; re-run `allocator_ranker_eval.py`. Done: the
  M18 selection A/B is re-run with the input it was actually missing.
- **W2.4 · ML2 — author regime OFF-cells for the gate-ready heads (§5.2).** T3.
  MES 15m + BTC 1h, following the regime-selectivity skill. Done: gate-ready heads
  bite; ML influence surface widens beyond one symbol.
- **W2.5 · Macro MA2/MA3 — the conviction-PnL harness + conditioning-input search
  (§4.2).** T1. Build the net-of-cost portfolio-PnL harness; grade `change`/composite
  as a *conditioning* input. Done: a fair test of macro-as-lens (the C5 feed).

### Wave 3 — Second influence surface + fusion
- **W3.1 · C2 — conviction-driven conflict resolution on demo (§6).** T3 (demo).
  Graduate `aggregate_intents` to pick by conviction. Done: the convergence point
  is confidence-driven, measured on demo.
- **W3.2 · ML3 — M20 exit head to advisory (§5.2).** T3. Truncation-replay →
  candidate→shadow→advisory. Done: a second order-influencing ML surface
  (proactive profit-banking).
- **W3.3 · S3/S4/S5 — ship the hardened-scalp hybrids that passed backtest (§3).**
  T3. Done: the survivors carry structural filters; each produces cleaner labels.
- **W3.4 · C5 — `c_macro` lens (§6).** T1 build / T3 weight. Feed MA3's
  conditioning signal into conviction. Done: macro conditions the decision instead
  of running parallel.

### Wave 4 — The master model
- **W4.1 · C6 / ML5 — learned conviction fusion (§6).** T3. Once C1–C5 + ML1 + ML4
  hold, promote `conviction-meta` v1→v2 learned, reductive-only. Done: one learned
  score fuses regime + P_win + exit + macro lenses — the master model, wired.

---

## 8. What NOT to do (disproven — do not re-open without a NEW input)

Re-litigating these burns cycles the frontier can't spare. Each has a quoted
verdict in the record:
- Allocator **selection** by the current ranker (AUC≈0.51) — only a proven P_win
  input reopens it (that's ML1/C3).
- New model **types** (frozen-embeddings, TCN, corpus-SSL) — closed negative;
  only fc survived.
- **Symmetric** conviction sizing (4.5× maxDD) — reductive-only is the live path.
- **Standalone** value/macro books, cross-sectional momentum, energy-surprise
  edge — null on real evidence; macro's future is *conditioning*, not standalone.
- Mechanical **exit levers** on the trend/pullback fleet — the bleed is
  entry-selection, not exits.
- Standalone **mean-reversion / fade** strategies on this system — every one has
  failed live.
- The pairs sleeve as **real-money** — net-negative on taker fees.

---

## 9. The one-look summary

| Focus area | Stop doing | Start doing | Serves the spine by |
|---|---|---|---|
| **Strategies** | New standalone MR/fade strategies | Structural hybrids that harden the 2 survivors (S1 FVG-confirm, S2 vol-gate pullbacks) | Better confidence + more honest labels |
| **Macro** | Firing single-signal percentile tests | Research-design framework (MA1) + PnL harness (MA2) → macro as a **conditioning lens** | Becomes the `c_macro` lens (C5) |
| **ML** | Training 61 heads that do nothing | Cleanup → wire OFF-cells → the net-of-cost **EV/P_win head** | The keystone head under the whole master model |
| **Master model** | Computing conviction and throwing it away | C1 (reductive sizing on demo) → C2/C3/C4/C5 → C6 learned fusion | *Is* the spine |

**First three actions, concretely:** W0.1 (ML cleanup), W0.2 (read the two
convergence soaks), W1.1 (flip reductive conviction sizing on demo). The first two
are Tier-1 and can start cold; W1.1 is the first real step of the master model and
carries no money risk.
