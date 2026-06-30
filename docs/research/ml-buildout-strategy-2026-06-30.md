# ML buildout strategy — where the AI goes next (2026-06-30)

> Source: a 29-agent grounded+adversarial analysis (map the decision pipeline →
> ideate across 5 lenses → adversarially verify each idea against the actual repo
> + prior research). Companion to the deep-review P1–P16 plan in
> `docs/research/` and the ML optimization roadmap (`docs/ml/optimization-roadmap.md`).
> Verdict tally: **0 build-now, 8 prototype-first, 12 skip** — the bar was the
> M18 finding (per-trade outcome ~coin-flip OOS from decision-time features).

## The headline reframe

**The bottleneck is not a shortage of models — it's that the models we have are
stuck observe-only, behind two hard walls:**

1. **The prediction ceiling (M18 + the n~352 data wall):** per-trade
   direction/outcome is ~51% AUC out-of-sample from decision-time features. No
   new classifier on the same features breaks this. It correctly kills
   hold-duration, entry go/no-go, dynamic-stop, c_wr/c_setup calibration — all
   are cousins of the unpredictable target.
2. **The influence wall:** of the entire pipeline, **exactly ONE ML output
   touches a live real-money order** — the advisory BTC 15m regime head's
   `P(volatile)` driving the vol-gate (BTC only, vol axis only). The whole
   conviction lens is *stamped on `meta.conviction` and never read by the order
   path*; the only apply path (`apply_conviction_sizing`) is default-off after
   its backtest A/B failed (4.5× worse maxDD).

So "more AI" ≠ "more classifiers." Leverage is on the three fronts the evidence
actually supports.

## Decision-pipeline coverage (what has NO model today)

| Decision | Today | ML status |
|---|---|---|
| Signal trigger / entry | ICT-TA geometry per strategy | none (meta-label filters lost the live holdout, stay research_only) |
| Regime TREND gate | ADX-14 heuristic | heuristic (trend ML head exists, unwired) |
| **Regime VOL gate** | **advisory ML `P(volatile)`** | **advisory_ml — the ONLY live ML→order path** |
| Conviction stamp | formulaic blend | shadow, **never read by order path** |
| Intent conflict / reinforcement | static priority ints + max(qty) | shadow soak only; `confidence` carried-but-ignored |
| Flip vs hold | fixed `FLIP_POLICY=hold` | heuristic |
| Position sizing | `risk_pct` math + hand-tuned `_confidence_scalar` | heuristic (conviction does NOT size) |
| SL / TP placement | fixed ATR / fixed-R | none |
| Exit timing / trailing / partial | Chandelier ATR-trail + fixed-R rungs | none (exit-ladder is observe-only) |
| News veto + sizing | **keyword lexicon, no NLP** | rule-based (a LIVE trade-blocker with no sentiment model) |
| Capital allocation | one winner per symbol by priority | observe-only soak; M18 found a scorer has no edge over priority |
| **Concurrent / correlated exposure** | **nothing** | **EXPOSURE lens entirely unbuilt** |

## The three fronts that survive the evidence bar

### Front 1 — Graduate & widen the ONE proven axis (vol-regime → influence)
Vol IS forecastable (the only validated ML→PnL path). Two moves:
- **Extend the vol-gate to ETH/SOL/MES.** Gated on soak maturity (the 15m heads
  are RG4-TRUSTWORTHY but sub-target on volume) + the MES labeling fix. The
  small unblocker: **fix `MB-20260630-001`** (the demote path scores regime
  heads on trade-win — "structurally blocks regime-head promotion"). Highest
  leverage-per-effort item on the board.
- **Book-level vol-targeted sizing** (deep-review **P1**, the "best-evidenced
  ML→PnL pathway", validated but **never wired**): scale total book size off
  forecast vol. Observe-only overlay first; Tier-3 to graduate.

### Front 2 — Build the missing RISK-MEASUREMENT lens (immune to the M18 ceiling)
Concurrent-risk + correlation are **measurements, not forecasts**, so the
unpredictability finding does not bear against them. The **EXPOSURE lens** (3rd
unified-confidence lens) has zero code; `bybit_2` (real money) trades
BTC+ETH as two independent `risk_pct` positions with no correlated-exposure
awareness. Build the decision-time correlation/covariance feature + the
concurrent-open-risk read (`MB-20260629-ALLOC-CORR` / deep-review **P2** cluster
caps) → observe-only soak → Tier-3 throttle. Validate **offline against the
portfolio-bootstrap backtest gate**, not a live in-sample soak (the discipline
that has repeatedly killed things that soaked fine).

### Front 3 — Fix the data plumbing blinding everything (enablers, not edge)
- **MES live-labeling gap** — blinds RG4 on half the multi-symbol fleet; fix
  first (unblocks ETH/MES vol-gate extension + the BTC→MES transfer).
- **Build-time label-quality / train-serve parity audit gate** — would have
  caught `BL-20260628-XA-TRAINING-ZERO` (dead xa_* features) and the RG4
  threshold mismatch *before* weeks of wasted soak.
- **Real-money fill-volume wall** — `bybit_2` pinned at the 0.001 min lot →
  ~0 real-money labels → `live_agreement` can't accrue → decision/conviction
  models can't earn promotion. This is the **deepest constraint**, and it's a
  capital/business call as much as an ML one.

## What is ALREADY built — do not rebuild
The adversarial pass caught these as already-shipped (the work is wiring, not greenfield):
- **Cost-estimate writer** — wired in `database.py:740` (`_record_trade_cost_estimate`) on every close (broker-truth fee capture is the open delta, `MB-20260629-ALLOC-COSTCAP`).
- **Order-flow capture side-car** — deployed + accruing on the trainer since 2026-06-04 (the join is deliberately deferred until enough bars accrue; still data-thin for a model).
- **Backtest-in-the-loop evaluator** — `sim/` already computes net-R-with/without-model; the only gap is wiring it into a standing promotion gate.
- **Champion-challenger** — that IS the shadow→advisory promotion gate (G7).

## What NOT to build (so we don't waste cycles)
More per-trade outcome/direction classifiers (coin-flip); entry go/no-go on the
dense synthetic corpus (relabels a known weak edge, fails live); hold-duration /
dynamic-stop per-trade overlays (every regime-conditioned per-trade overlay
backtested has blown up maxDD); order-flow models (data-blocked indefinitely,
no offline A/B possible). The "real AI" leaps (RL sizing, transformers on the
bar stream, LLM-in-the-loop, diffusion synthetic data) stay on the Phase-R
research shelf — premature while the proven axis isn't fully live and the data
walls bind. The one near-term "real AI" upgrade with a path: replace the **news
keyword lexicon with a sentiment model** (it's a live trade-blocker running on a
dictionary) — prove it as veto-quality first.

## Recommended sequence
1. **Fix `MB-20260630-001`** (stage-guard regime demote axis) — Tier-1, tiny, unblocks the multi-symbol vol-gate graduation pipeline.
2. **MES labeling-gap fix** — Tier-1, unblocks ETH/MES RG4 + BTC→MES transfer.
3. **Build-time parity/label audit gate** — Tier-1, stops wasted soak.
4. **Exposure/correlation lens** — prototype OFFLINE against the bootstrap gate (Front 2).
5. **Book-level vol-targeted sizing** — observe-only overlay (Front 1, deep-review P1).

Items 1–3 are pure-win plumbing that can start now; 4–5 are the real new
capability and are Tier-3-gated on backtest evidence + operator approval.
