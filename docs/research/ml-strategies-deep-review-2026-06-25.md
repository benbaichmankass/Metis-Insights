# Deep review — strategies + ML: diagnosis, complementary-edge research, and a prioritized test plan (2026-06-25)

> **Tier-1 research memo. Analysis + proposals only.** Nothing here touches the
> live order path, `config/strategies.yaml`, `config/accounts.yaml`,
> `config/regime_policy.yaml`, or any unit the live VM consumes. Every config /
> live-promotion change it proposes is called out as **Tier-3 (operator-gated)**.
> Origin: operator direction 2026-06-25 — *"a very deep research session on the
> strategies and the MLs … analyze performance and think about tweaks … and bring
> in scope to what's NOT in the roster — complementary, diverse systems so we're
> always trading something well … make a plan, do a deep review, do deep open
> research, then propose what to test."* Scope chosen by the operator this
> session: **balanced across (A) fix existing / (B) new edges / (C) ML**, **go
> broad including market-neutral**, **research-and-propose only** (this memo + a
> green-lightable test plan; no backtests run, no config touched).

---

## 0. TL;DR — the one thesis the whole session converged on

Three independent cited research deep-dives (market-neutral/relative-value;
non-trend directional + portfolio allocation; ML-that-contributes) **and** our own
prior results all point to the same conclusion:

> **"Always trading something well" is won by STRUCTURAL diversification + RISK
> management, NOT by timing capital allocation.** Add genuinely orthogonal
> always-on sleeves (different ON-regimes), run a book-level volatility-target +
> correlation-cluster caps, and let each sleeve's own entry logic do the regime
> selection. Do **not** build a regime-classifier capital router — we already
> proved it loses to naive diversification OOS, and the literature says that's the
> rule, not the exception (DeMiguel/Garlappi/Uppal RFS 2009; Cederburg et al JFE
> 2020).

The book's real problem is **edge-type concentration, not symbol count**: ~39
strategies but only **two edges** (Donchian breakout trend + HTF pullback
continuation), both trend-following. Every alt/ETF cell we added improved *asset*
diversification (good — they're BTC-uncorrelated) but **not edge** diversification,
so in a chop/range tape the whole book stalls together — which is exactly the
real-money-idle state the live reports show.

**The highest-value moves, in order of confidence × buildability:**
1. **Book-level vol-targeting** + **correlation-cluster caps** — risk/structure wins
   using edges we already have. (Tier-1) *(The regime router — originally listed here
   as "enforce it" — was found ALREADY ENFORCING live on 2026-06-25; see §A3. The
   remaining router work is verification + decomposing the never-measured alt/ETF cells.)*
2. **Re-baseline the headline backtests** under exact-live-params + live data
   (the "+43.8R" trend_donchian number was inflated — honest value +11R/−6R OOS).
3. **New directional sleeve: Opening-Range Breakout on MES** (real evidence, no
   decay, no leverage confound, clean infra fit) + **generalize `squeeze`**.
4. **Market-neutral: crypto cross-sectional momentum** (expanded basket) +
   **cointegration ratio mean-reversion** — genuinely orthogonal; both force-build
   the cross-asset loader.
5. **ML: stop building directional/regime advisory heads.** Build a **P(win)
   meta-filter** on existing positive-EV signals and a **vol-forecast → vol-targeted
   sizer**; fix the promotion gate to require **sustained LIVE-shadow** brier_lift
   (not validation). Demote regime to a *feature*.

The full prioritized, spec'd test plan is **§5**. The explicit **do-NOT-build**
list (decayed / inaccessible / empirically dead) is **§6** — it's as valuable as the
build list because it saves wasted sweeps.

---

## 1. Current state (grounding)

- **Roster:** ~39 strategies across 6 asset classes / 8 accounts. Live edges =
  Donchian breakout (`trend_donchian` + 8 symbol/TF clones) and HTF pullback
  (`htf_pullback` + ~12 clones). The diverse primitives — `turtle_soup`
  (sweep-reversal), `fade_breakout` (fade, shadow), `squeeze` (vol-expansion),
  `fvg_range` (range MR), `vwap` (MR, killed), `ict_scalp` — are **BTC-only,
  single-cell**.
- **Real money** (`bybit_2`, 5 BTC/ETH trend/pullback strats): **idle — 0 closed
  trades / 24h** in the latest reports. The "always trading something" gap, in
  dollars.
- **ML fleet:** 46 manifests (mostly regime classifiers + setup-quality/trade-
  outcome + a conviction meta-stacker). 3-stage ladder candidate→shadow→advisory;
  only advisory influences orders (reductively). **Zero models currently influence
  orders** — the lone advisory BTC regime head went degenerate live (brier_lift
  −0.277, AUC 0.40) and was demoted. Conviction framework is observe-only soak.

---

## 2. Workstream A — diagnosis of the existing book

### A1. The `trend_donchian` "−198R live / 0% win vs research-best" puzzle is ALREADY root-caused
(PERF-20260601-001; journal pulls #2542–#2547; `regime-roster-matrix-2026-06-01.md`.)
It is **not** a dead edge and **not** a broken exit path. Three causes, all
instructive:
- **Regime:** breakout strategies (trend_donchian + squeeze) whipsawed because BTC
  ranged the entire ~1-month live window (the logged channels were all ranges).
- **Inflated backtest:** the advertised "+43.8R OOS" *omitted the live
  `min_confidence` gate* (→ −53R when applied) and used a now-deleted `/tmp/btc5m.csv`
  5m source. **Honest gated value on fresh Bybit-perp 1h: +11R full / −6R OOS.** The
  real edge is far thinner than the roster headline implied.
- **Execution amplifier (re-entry storm):** the open-package gate only blocks while a
  package is OPEN, so a mid-bar close re-fires the strategy within the same bar (9
  packages in ~1h on a 2h strategy); the intent layer no-ops the duplicate, flooding
  the journal with `intent_noop` rows that **distort per-strategy live stats**.

**Cross-cutting lesson (backtest-hygiene debt):** every "research-best" claim in the
roster may share these three failure modes — live gates omitted, non-live data
source, post-hoc best-of-N selection. The validation discipline (k-fold every-fold →
2× fee → out-of-pool holdout → bootstrap) is sound *when followed*; the gap is
enforcing exact-live-param + live-source parity. → Test item T0.1.

### A2. The regime × strategy × direction matrix (BTC) — the complementarity backbone
From `regime-roster-matrix-2026-06-01.md` (net R, fee-adj 7.5bps, 2021–2026):

| Strategy | Total | trending (L/S) | transitional (L/S) | chop (L/S) | maxDD | read |
|---|---:|---|---|---|---:|---|
| trend_donchian 1h | +10.9 | −5.7 (+22.3/−28.0) | −2.3 (+21.7/−24.1) | +18.9 (+3.3/+15.6) | 20.6 | long=trend edge; short only earns in **chop** |
| fade_breakout_4h | +19.4 | — (gated) | +5.2 | +14.2 (long-led) | 30.2 | chop MR |
| squeeze_breakout_4h | +17.6 | +5.1 | +1.6 | +10.9 | **7.9** | **+ in EVERY regime, lowest DD — most router-friendly** |
| fvg_range_15m | **−16.9** | — | — | −16.9 | 13.2 | **net loser both sides even in target chop — no measured edge** |
| htf_pullback_trend_2h | +26.3 | +30.1 | +8.4 | **−12.2** | 14.8 | trend-continuation owns trending, loses chop |
| vwap 5m | **−10,724** | − | − | − | n/a | **fee-murdered: gross +3,399 vs fees −14,123 (4.2×) over 40,650 trades** |

Structural reads:
- **Long-only is correct for the trend edges** (BTC's secular uptrend punishes
  trend-shorts), but the **chop-short trend edge (+15.6R) is real and currently left
  on the table.**
- **`squeeze` is the model citizen** (positive every regime, lowest DD) — correctly
  re-promoted live 2026-06-23.
- **Fine-TF mean-reversion is NOT this market's edge** (fvg −16.9; vwap fee-murdered).
  This is a hard constraint on the MR research: any MR sleeve must clear a brutal
  fee:gross test on crypto perps; equities/futures are the more plausible MR home.

### A3. The regime router is ALREADY ENFORCING (Phase 3) — verified live 2026-06-25
`config/regime_policy.yaml` encodes the OFF cells from the matrix. **Live diag pull
(2026-06-25, T0.3) shows the router is in Phase-3 ENFORCEMENT, not Phase-2 shadow:**
`regime_hard_gate` rows with `enforced: true` are being written in real time (newest
2026-06-25T11:11Z at pull time), and `regime_shadow_gate` stopped emitting ~2026-06-08
(the shadow→enforced flip). So the "cheapest lever" is **already pulled** — this
corrects the earlier draft assumption that it was log-only.
- **It's working as designed:** the captured hard-gates are `htf_pullback_trend_2h`
  SHORT in `transitional` (the matrix's −4.3R transitional-short loser cell) — the
  router correctly killing a measured loser, exactly the "always trade something well"
  behaviour we wanted.
- **But two things surfaced:** (1) that gated short intent fires every ~2.3 min (≈ tick
  cadence) on a 2h strategy — the **re-entry-storm** duplicate-intent flood (A1 cause 3 /
  A5), benign because gated but it pollutes the signals table and distorts stats; (2)
  every row carries `vol_regime: unknown`, so the 2-D vol-axis cells (S15b) can never
  fire live — the vol detector isn't resolving.
- **Caveat retained (our prior negative + literature):** the router is a **GATE** (turn
  off measured losers in the wrong regime), **NOT a capital re-weighter** — keep it that
  way (regime-conditional *weighting* lost to naive diversification OOS).
- **Doc-freshness:** `CLAUDE.md`'s env table still says `REGIME_ROUTER_ENABLED` "default
  off → phase 2"; the live VM is enforced (field beats comment) → log a doc-freshness fix.

### A4. Flags
- **`fvg_range_15m`** is live (bybit_1 + bybit_2) yet matrix-negative every regime →
  candidate DEMOTE_SHADOW pending a live-cohort confirm.
- **The regime matrix is BTC-only** — the 30+ alt/ETF/futures cells were *never*
  regime-decomposed. We're running cells whose regime profile we haven't measured.
- **Exec hygiene that distorts measurement:** the re-entry storm (A1) and the open
  **BUG-049 target_qty=0 emit-then-orphan** cluster (ada/mgc/spy/slv/qqq) mean
  "36 strategies evaluating" overstates how many actually fill. Fixing these is
  health-side, but it gates the *trustworthiness* of every performance-review decision.

---

## 3. Workstream B — complementary / diverse edges (the "what's not in the roster" hunt)

All four families the operator selected were researched (cited in full in the session
research files; key citations inline). Verdicts are calibrated to **our** venues
(Bybit perps; IBKR MES/MGC/MHG; Alpaca ETFs; no options; no L2; single-symbol
strategy contract; taker fees; single venue) and our validation discipline.

### B1. Mean-reversion / range
- **Why our `vwap` died (diagnosis):** short-horizon MR is *paid liquidity provision*
  (Nagel, *Evaporating Liquidity*, RFS 2012) — its expected return is **regime-
  conditional on vol/stress** and **brutally cost-sensitive**. A naive always-on fade
  pays the spread every round-trip but collects the premium only sometimes, and fades
  in trends (wrong sign) → net-negative every aggregate bucket. Exactly what we saw.
- **Verdict:** do **not** resurrect an always-on fade. MR is viable only **(a)
  regime-gated** (ADX-low, inside Bollinger/Keltner, no fresh breakout), **(b) on the
  most liquid instruments** (BTC/ETH, SPY/QQQ), **(c)** with per-trade edge clearing
  **2× fees** by a wide margin. Extend `fvg_range` inside a verified range filter; the
  legitimate crypto reversion signal is **funding/positioning extremes**, not price RSI.
  Medium confidence — easy to get wrong (that's how vwap died). → P13.

### B2. Market-neutral (pairs / cross-sectional) — the genuinely orthogonal core
- **Cross-sectional momentum on an EXPANDED alt basket (TOP market-neutral pick).**
  Rank ~10–15 liquid alt perps, long-top / short-bottom, **dollar-neutral** → strips
  beta, earns when the index is flat but alts disperse (the trend book's OFF-regime).
  Best-documented crypto anomaly (weekly LS Sharpe ~1.5 OOS; ScienceDirect
  S1057521924007415) **but** adversarial: OOS collapses (Starkiller +69%→−2.35%),
  75–94% momentum-crash drawdowns, cost-breakeven ~125bps. **6 coins is too thin** —
  must widen the universe; a BTC-trend overlay cut a practitioner DD 75%→45%.
  → P7. *Needs the cross-asset loader.*
- **Cointegration ratio mean-reversion (truly orthogonal P&L — convergence, not
  direction).** Real but fragile: ETH/BTC was a 3-year *value trap* (cointegration is
  not stationary → must roll-re-test); ~0.22% round-trip fee wall (4 taker fills) is
  binding; **funding asymmetry is a genuine tailwind** (short legs collect ~10–17% APR).
  Use rolling Engle-Granger selection, OU half-life time-stops, ±2σ entry, maker fills,
  funding-netted costs. → P8. *Needs the loader.*
- **ETF/futures pairs are empirically DEAD** (high correlation ≠ cointegration):
  QQQ/IWM residual ADF p=0.53; TLT/IEF p≈0.18; SPY/TLT a coin-flip post-2022 flip;
  stocks-vs-metals ~0 correlation. Do not build (§6).

### B3. Carry / funding harvest
- **Verdict: keep dormant (our prior call was right).** Delta-neutral funding/basis
  carry is genuinely orthogonal and historically high-Sharpe, **but inaccessible to
  us**: no spot inventory for cash-and-carry, single venue (no cross-exchange leg), and
  the post-ETF basis has **compressed ~90%** (BTC front-month ~25%→4.5% ann.; CoinDesk
  Mar-2025 unwind). It also breaks the single-symbol contract. **Funding is a
  feature/tailwind inside B2, not a standalone sleeve.** A single-perp
  *funding-overextension reversion* is single-symbol-compatible but crowded/decaying →
  shadow-only at most. → P15.

### B4. Volatility-based
- **Generalize `squeeze` (vol-expansion breakout) — best-motivated generalization.**
  Vol-clustering is asset-universal (GARCH), so porting our BTC-only squeeze to
  ETFs/futures/ETH-SOL is better-founded than porting any directional pattern. It's
  **complementary to trend** (fires the *initiation* of the move trend later rides —
  different ON-regime). Low-win-rate, convex; each symbol needs its own band/percentile
  params. → P6.
- **Opening-Range Breakout (ORB) on MES — strongest NEW directional candidate.** ORB on
  index futures has **NOT decayed**: a 2015–2025 ES/NQ study shows ~55–64% continuation
  with no 11-yr downtrend (tradingstats.net). **Adversarial:** the famous Zarattini
  +1,484% is a **TQQQ-leverage artifact** (TQQQ −79% in 2022), not signal alpha;
  slippage is unmodeled; the "5-min best" claim is window-overfit. Build it
  **unleveraged**, on the **IBKR consolidated feed** (not raw IEX — backtests flip with
  the feed), with a false-breakout stop, an IB-width filter, and a **walk-forwarded**
  window. Clean fit to our RTH bar-loop harness; MES has deep liquidity and sizes as a
  single contract with no leverage confound. → P5.
- **Seasonality / events** yield **no standalone directional alpha** net of cost on our
  venues (overnight drift, turn-of-month, ICT killzones, pre-FOMC drift all decayed or
  cost-killed — NY Fed SR512 vs Kurov 2021; QuantSeeker TOM re-test). Their robust use
  is a **defensive econ-calendar/clock risk-gate** (suppress entries / cut size around
  CPI/FOMC & thin weekend crypto liquidity) — which also protects the prop $150
  daily-loss limit. → P16.

---

## 4. Workstream C — ML that actually contributes

The ML literature, read honestly, **confirms our own live results.**

### C1. Why the advisory keeps going degenerate
AUC 0.40 is *anti-predictive*, a tell. Ranked causes: **(a) regime-label instability**
(the label boundary itself drifts between train and serve → the head predicts a target
that changed meaning); **(b) label leakage** if the labeler uses any forward window;
**(c) base-rate/calibration drift** (crypto regime mix is wildly non-stationary); **(d)
metric mismatch** (a good regime classifier can be a terrible conviction lens). With 46
manifests + Optuna, our **effective trial count is huge** — a model passing a *raw*
validation gate is precisely the selection-bias trap the Deflated Sharpe Ratio warns
about (Bailey & López de Prado, SSRN 2460551).

### C2. What ML targets actually monetize (at our scale)
- **P(win)/expectancy meta-filter on existing signals (de Prado meta-labeling) — the
  realistic win.** Primary rule decides side; ML decides take/size only → overfitting is
  bounded. Honest efficacy is *modest* (Hudson & Thames: precision 0.48→0.54 on trend;
  the win is mostly learning to **abstain** + risk-adjust) and it **cannot rescue a bad
  primary** — apply only to strategies with positive *real* closed-trade expectancy. This
  is exactly our setup-quality/trade-outcome family. → P9.
- **Volatility forecasting → vol-targeted sizing — best-evidenced ML→PnL pathway.**
  Vol is forecastable where direction is not. Monetize via sizing (Harvey et al: vol
  targeting reliably cuts tails/DD across *all* assets; lifts Sharpe on leverage-effect
  assets BTC/ETH/MES, negligible on MGC/MHG — so don't gate on Sharpe there). No return
  prediction required. → P10.
- **Directional next-move prediction ≈ coin-flip OOS (~51%)** — do **not** build as an
  order-influencing head; at most a weak feature.

### C3. Sizing — highest-ROI ML use, with precise caveats
Vol-targeting (safest) → conviction-scaled sizing off a *calibrated* P(win) → **capped
fractional-Kelly** only later. Full Kelly blows up on estimation error; use **quarter-
Kelly capped**, floored by the vol target. Calibration is load-bearing (isotonic on a
rolling, recent, non-resampled window — a static calibrator is a future degeneracy).

### C4. The actual fix for degeneracy (highest-leverage ML work)
Harden the promotion gate: **(i)** causal-labeler audit (no forward windows); **(ii)**
rolling-window isotonic calibration with an ECE sub-gate; **(iii)** Deflated-Sharpe /
brier_lift computed against the *effective* trial count; **(iv)** a **sustained
LIVE-shadow brier_lift floor** before any promotion. The BTC head's collapse would have
been caught by requiring sustained *live-shadow* brier_lift ≥ 0 rather than validation.
→ P11. **Demote regime from a standalone advisory to a feature inside the P(win) model.**

### C5. Do NOT build (ML): a **regime-driven capital allocator** (own negative + DeMiguel
RFS 2009 + Cederburg JFE 2020) and **RL** (non-reproducible, regime-dependent, brittle,
infra-mismatched). Conviction→sizing graduation only *after* P9/P10 prove out in live
shadow, as a bounded 0.5×–1.5× multiplier on a vol-targeted base.

---

## 5. PRIORITIZED TEST PLAN (ranked by EV × buildability × orthogonality)

Each item: what to test, concrete spec, success gate (our k-fold every-fold → 2× fee →
out-of-pool holdout → bootstrap discipline), and tier. **Nothing here is applied** —
each is a research/backtest task whose *output* is either a documented decision or a
Tier-3 proposal PR. Recommended sequencing is top-to-bottom (T0 enables the rest).

### Tier 0 — enabling / hygiene (do first; cheap; unblocks trust)
- **T0.1 Re-baseline the live roster's headline edges** under exact-live-params + live
  data source. *Gate:* each live strategy's standalone backtest reproduced with the
  YAML params + a Bybit-perp (not deleted-CSV) source; flag any whose honest gated edge
  is materially below its roster headline (trend_donchian already known: +11R/−6R). *Tier-1.*
- **T0.2 Build the multi-asset / 2-symbol backtest loader** (reuse the ML layer's
  leakage-safe `merge_asof`; cross-asset scope doc `cross-asset-strategy-scope-2026-06-18.md`).
  Forcing function for P7/P8. *Gate:* backtest==live alignment on a known single-symbol
  case. *Tier-1 tooling.*
- **T0.3 Analyze the accrued `regime_shadow_gate` soak** vs the matrix; if it confirms
  the OFF cells, draft the Phase-3 enforcement proposal. *Tier-1 analysis → Tier-3 PR.*

### Tier 1 — risk/structure wins using edges we already have (highest confidence)
- **P1 Book-level volatility-targeting overlay.** Scale total gross to a constant
  realized-vol budget (blend 20d/60d to damp turnover); cap 0.5×–1.5×; **target once at
  book/asset-class-cluster level, never per sleeve.** *Gate (portfolio bootstrap):*
  reduces maxDD + 5%-CVaR vs un-targeted equal-weight **without materially lowering
  Sharpe**; also cuts simulated prop daily-loss breaches. Sold as risk management, not
  alpha. *Research → Tier-3 to deploy.*
- **P2 Correlation-cluster exposure caps.** Cap aggregate risk per cluster
  (crypto-beta / US-equity / metals / bonds). No return forecast needed. *Gate:*
  prevents simulated silent concentration without lowering bootstrap Sharpe. *Research → Tier-3.*
- **P3 Regime router — ALREADY ENFORCING (done); now VERIFY + extend.** T0.3 found the
  router live in Phase-3 enforcement (`regime_hard_gate enforced:true`, 2026-06-25). So
  this item is no longer "propose enforcement" — it is: (a) **verify health** (it's
  correctly gating the htf_pullback transitional-short loser cell — good); (b) **fix the
  re-entry-storm** that re-fires the gated intent every tick (health-side); (c) **fix the
  unresolved `vol_regime: unknown`** so the 2-D vol cells can fire; (d) feed P4's alt/ETF
  decomposition into new policy cells (the alt/ETF cells default permissive-ON, unmeasured).
- **P4 Roster pruning + regime-decomposition.** Decide `fvg_range_15m` (demote unless a
  live cohort contradicts the matrix); **regime-decompose the alt/ETF/futures expansion**
  (the matrix is BTC-only). *Gate:* per-cell regime×direction matrix using exact live
  params (extends `regime_matrix.py`). *Tier-1 research → Tier-3 demotions.*

### Tier 2 — new directional sleeves (real evidence, clean infra)
- **P5 Opening-Range Breakout on MES** (then SPY/QQQ/IWM). 5-min RTH bars, OR = first N
  bars (N∈{1,3,6} **walk-forward selected per symbol**), entry on close beyond OR
  high/low, stop = opposite OR end or 0.5×ATR, **skip if OR width >1.5×ATR**, flat by
  RTH close, **unleveraged**, IBKR consolidated feed. *Gate:* PF>1.3 & Sharpe>0.7 after
  **2× fees+slippage** in **every** k-fold, holdout Sharpe ≥0.5, reject if profit
  concentrates in <2 fold-years. *new-strategy, paper-first.*
- **P6 Generalize `squeeze`** to SPY/QQQ/GLD, MES/MGC, ETH/SOL (one cell per symbol).
  TTM squeeze (BBW <20th pct over 100 bars AND BB inside Keltner), entry on first close
  outside BB w/ range confirm, trail exit, time-stop. *Gate:* positive expectancy after
  2× fees every k-fold; **right-tail health check** (top-decile-trade-removed expectancy
  ≥0 — not a single-trade mirage); holdout ≥0. *new-strategy, shadow cells.*

### Tier 3 — market-neutral (genuinely orthogonal; needs T0.2)
- **P7 Crypto cross-sectional momentum.** Universe = top ~10–15 liquid Bybit USDT perps
  (liquidity-filtered); daily candles, **weekly rebalance**; formation = trailing 28-day
  return (≥7d to dodge short-horizon reversal); long-top/short-bottom tercile,
  dollar-neutral; overlays = BTC-50d-SMA risk-off gate + book vol-target +
  funding-aware short selection. *Gate:* every-fold positive net of 1× fees, survives
  2× fees, **out-of-pool holdout positive** (not pool-overfit like the alt-recombination
  attempt), bootstrap-additive Sharpe **and correlation to the trend book <~0.3** in
  holdout; target net Sharpe ≥0.7 with DD far below the un-overlaid 75%+. *needs P0.2 +
  universe expansion; Tier-3 to live.*
- **P8 Crypto cointegration ratio mean-reversion.** Screen all pairs in the expanded
  basket; keep only pairs passing rolling Engle-Granger ADF (p<0.05, 120-day) with OU
  half-life ∈[0.5,10]d; re-screen weekly. 1h/4h candles; z-score of residual (β refit
  weekly); entry |z|≥2, exit |z|≤0.5, stop |z|≥3.5, time-stop ~2–3× half-life. Cost =
  4 taker fills (~0.22%) + per-leg funding; prefer maker. *Gate:* the 4-stage gate +
  **survives removal of any single pair** (no one-pair dependence) + net-positive after
  the fee wall + funding every fold; target net Sharpe ≥0.8, ~0 correlation to trend
  book. *needs P0.2; Tier-3.*

### Tier 4 — ML
- **P9 P(win)/expectancy meta-filter** on existing positive-EV strategies. Target =
  triple-barrier **net-of-cost** R per closed trade, per (strategy, symbol); features =
  vol estimators + buckets, time-of-day, momentum/lags, **regime probability vector as a
  feature**, funding/OI extremity flags, macro, account-context; **no leaky labels**;
  restrict to strategies with enough closed trades. *Gate:* purged WF; **Brier-lift vs
  base-rate AND realized net-PnL uplift on OOS** beating both "take every signal" and
  "fixed downsize" baselines; **Deflated Sharpe ≥ threshold** vs effective trial count;
  isotonic ECE sub-gate on a recent non-resampled window. *ML experiment.*
- **P10 Vol-forecast → vol-targeted sizing.** Target = next-bar Yang-Zhang/Garman-Klass
  vol per (symbol, TF); GARCH baseline, LightGBM/hybrid challenger. *Gate:* beats GARCH
  on QLIKE/MSE (else just use GARCH); portfolio-level maxDD + CVaR reduction vs
  constant-notional net of turnover; Sharpe benefit expected only on BTC/ETH/MES.
  Ties into P1. *ML experiment.*
- **P11 Promotion-gate hardening** (the actual degeneracy fix): causal-labeler audit +
  rolling isotonic calibration + ECE sub-gate + **sustained LIVE-shadow brier_lift
  floor** + Deflated-Sharpe vs effective trials. Demote regime heads to features. *Gate:*
  a re-run of the demoted advisory under the new gate must fail it (proves the gate would
  have caught the collapse). *ML tooling — ship alongside P9.*
- **P12 (later, conditional) conviction-meta → capped quarter-Kelly sizing**, floored by
  P10's vol target, bounded 0.5×–1.5×. Gated behind P9+P10 proving out in live shadow.
  *Tier-3.*

### Tier 5 — lower-confidence / shadow-only
- **P13 Regime-gated range MR** (fix-don't-resurrect vwap; extend `fvg_range`): trade
  only ADX<20 + inside-channel + no fresh breakout, liquid symbols only, per-trade edge
  must beat 2× round-trip cost by ≥50% and be positive *only with the gate on*. *shadow.*
- **P14 Generalize `turtle_soup`** sweep-reversal to SPY/QQQ + MES, per-symbol-gated
  shadow cells (folklore evidence — expect alts to fail). *shadow.*
- **P15 Funding-overextension reversion** on single Bybit perps — shadow cell with
  skeptical gating (crowded/decaying signal). *shadow.*
- **P16 Defensive econ-calendar / seasonality risk-gate** (suppress entries / cut size
  around CPI/FOMC & thin weekend crypto liquidity; protects prop limits). A gate, not a
  strategy. *Tier-2/3.*

---

## 6. Do NOT build (decayed / inaccessible / empirically dead) — saves wasted sweeps
- **Regime-classifier capital allocator** — own negative result + DeMiguel (RFS 2009) /
  Cederburg (JFE 2020). Any de-risking value is already in P1 vol-targeting.
- **RL for trading** — non-reproducible, regime-dependent, brittle, needs an execution
  simulator we don't have.
- **Funding-carry / cash-and-carry** — no spot inventory, single venue, basis compressed
  ~90% post-ETF; breaks single-symbol contract. Keep dormant.
- **BTC lead-lag as alpha** — exploitable lag is seconds–minutes, sub-fee, decaying,
  needs HFT infra. Repurpose BTC-dominance as a regime *feature* only.
- **ETF mean-reversion pairs** — TLT/IEF, QQQ/IWM, SPY/TLT, MES-vs-metals are *correlated
  but not cointegrated* (trending residuals). Copper/gold macro signal has broken.
- **GLD/SLV short-the-ratio** — SLV borrow blows out (>20% / un-borrowable) exactly when
  GSR is high and the trade looks best.
- **Standalone seasonality / overnight-drift / turn-of-month / ICT-killzone directional**
  strategies — decayed or cost-killed; use only as risk-gates.
- **Always-on (un-gated) mean-reversion fades** — structurally short-momentum, fee-bled
  (how vwap died).
- **Directional next-move ML heads** as order-influencing models — ~coin-flip OOS.

---

## 7. The portfolio thesis, restated (why this plan serves "always trading something well")
A book that is "always trading something well" is **structurally diversified + risk-
managed**, not allocation-timed. This plan adds sleeves with **non-overlapping ON-
regimes** — trend (have it) + squeeze/vol-breakout (P6, low-vol→high-vol transition) +
ORB (P5, the RTH open) + cross-sectional dispersion (P7, flat/dispersive crypto) +
ratio convergence (P8, range-bound spreads) + regime-gated range MR (P13, quiet ranges)
— each always-on at static weight, with regime logic **inside** each sleeve (P3 router
as a gate). On top: **book-level vol-targeting (P1) + cluster caps (P2)** as the robust
overlay, and **ML used reductively** (P9 filter, P10/P11 sizing) — never as a capital
router. The diversification, not the timing, does the work — which is exactly what beat
dynamic weighting for us before.

---

## 8. Recommended first concrete steps (for operator green-light)
**Execution-session update (2026-06-25):** T0.3 ran first and found the router **already
enforcing** (P3 done) — so the freed priority moves to **P4** (regime-decompose the
never-measured alt/ETF/futures cells) + the **re-entry-storm fix**. A tooling audit also
found several plan items are **already built**, not to-build: `scripts/backtest_pairs.py`
(P8 cointegration ratio MR — complete, self-tested), `scripts/backtest_funding_carry.py`
(carry, validated dormant), `scripts/backtest_system.py` (portfolio substrate for P1),
`scripts/research/regime_matrix.py` (P4 decomposition), and robustness/fee/corr gates. So
the genuine **builds** remaining are: **P7** (cross-sectional *basket* momentum —
`research_momentum.py` is time-series, not cross-sectional), **P1** (vol-target overlay on
`backtest_system.py`), and **P5** (ORB harness). The cheapest high-value start is now:
**run P8 (pairs) + P4 (alt/ETF regime decomposition) + P6 (squeeze multi-symbol)** on the
trainer (harnesses exist), **build P1 + P5 + P7**, and start ML with **P11 (gate
hardening) + P9 (P(win) filter)**.

**Execution-session update 2 — the ML replay pre-gate + a verified advisory bug
(2026-06-25, PR #4602):** built the replay pre-gate the "compress soak" idea called
for, in two stages: **stage 1** (`replay_pregate.py` + `replay_pregate_fleet.py`) replays
clean candles through the live feature function vs the dataset's own `regime_label`
(true parity), and **stage 2** (`replay_pregate_live.py`, RG4) re-runs `predict_proba`
on the EXACT rows the live runtime logged, broken down by stage — the train/serve-skew
detector. A durable `replay-pregate-nightly.yml` runs the fleet session-independently.

The RG2 **acid test settled §C1/§C4**: the demoted `btc-regime-1h-lgbm-yz-v1` head, with
correct label parity, scores **AUC 0.79** through the live feature function — it is NOT a
broken model. Live evidence (#4596) + registry stage-history (#4601) + code pinned the
real cause: **`src/runtime/advisory_sizing.py::compute_advisory_factor` scores advisory
regime heads on the bare `_feature_row_from_pkg` row (6 fields, no `market_features`)** —
unlike the signal path (`_emit_shadow_preds`) and per-bar path (`regime_bar_scoring`),
which both enrich via `feature_row_for_predictor`. So any regime head promoted to advisory
gets a feature-less row → constant ~0.98 → the `auc 0.40 / brier_lift −0.277` that demoted
both yz advisories. This is **distinct from** (and dominates) the vol_bucket-edge
calibration issue diagnosed earlier on the *shadow* path: edge re-calibration alone cannot
make advisory promotion work. **Tier-3 fix (proposed, not applied — PR #4602):** (B,
recommended) exclude regime heads from the advisory directional-downsize quorum (a
`P(volatile)` score is not a bullish/bearish view) + a promotion-gate guard that refuses
`shadow→advisory` until the advisory-path score distribution is verified non-degenerate.
Logged to `ml-review-backlog` `MB-20260625-001`/`-001`.

**Execution-session update 3 — P4 fully gated: a complementary convex trend sleeve
(2026-06-25):** the P4 regime-decomposition (19 alt/ETF/futures instruments,
`scripts/research/regime_matrix.py` → `scripts/backtest_trend.py`) cleared the FULL gate
(2× fee → IS/OOS holdout → per-year → right-tail → orthogonality). **Survivor sleeve:
SLV (+101R, exp 0.26), QQQ long-only (+76R, exp 0.36), USO (+56R), GLD (+35R)** — net of
15bps, all OOS-positive, mostly-positive by year. **Orthogonal to the existing BTC/MES
trend book** (monthly-return |corr| ≤ 0.17 vs BTC, ≤ 0.11 vs MES over 113 months) — the
diversification the portfolio thesis wanted. Profile: convex/right-tail-dependent (top
decile carries the edge — *distributed*, not a freak; same character as the SOL squeeze,
so size small / disclose). DROPPED: SPY (redundant with QQQ, corr 0.61), IWM (marginal),
DBC (failed OOS), crypto-alts-15m (all −44 to −674: trend bleeds on chop → MR/squeeze
territory), ES 1h (−99, consistent with the live MES trend difficulty). **Tier-3 proposal
(awaiting operator):** add `trend_donchian` cells on SLV/QQQ-LO/USO(±GLD) to `alpaca_live`
(already trades ETFs), `execution: shadow` first, after `account_compat_matrix`. Together
with the SOL-squeeze convex satellite (P6b), this forms a small **convex-satellite book**
orthogonal to the directional core. Full work: scratchpad `P4-regime-decompose-result.md`.

*Sources: full cited research (market-neutral, non-trend+allocation, ML) was produced
this session and is summarized inline above with key citations; the existing-book
evidence is in `regime-roster-matrix-2026-06-01.md`, `regime-router-design-2026-06-01.md`,
`config/regime_policy.yaml`, and the performance/ml review backlogs.*
