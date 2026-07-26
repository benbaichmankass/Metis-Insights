# M30 — Technical-side deep quant-research platform (scoping)

**Status:** SCOPING — for operator review (operator-directed 2026-07-26 night).
**Anchor:** `MB-20260726-M30-QUANT-RESEARCH-PLATFORM`.
**Tier:** Tier-1 throughout (offline research tooling, observe-only; no order path,
no `src/` runtime behavior change, no `config/`strategy/risk/account edit). Anything
that would touch a live path graduates via the existing tier ladder, operator-gated.
**Composes with:** the existing (mostly-unbuilt) `signal-research-framework-DESIGN.md`,
the M28 macro `M28-signal-research-methodology.md` + `M28-signal-research-ledger.md`,
and `RESEARCH-RIGOR-STANDARD.md` (binding).

## The operator's ask (verbatim intent, 2026-07-26)

> So far when we've built a strategy we've done research, come up with an idea,
> then tweaked it until we found something that seems to work. But we haven't
> developed a workflow for *deeper* research — being able to do multivariate
> regressions on complex datasets to understand what's actually correlating with
> different types of movements, market structures, and price-action; a better
> handle on what actually drives edges instead of copying basic strategies and
> tweaking. Continue the macro side, but build out the technical/price-action
> research infrastructure too.

The ask is **data-first edge DISCOVERY** to replace **hypothesis-first
copy-and-tweak**: not "prove this ICT setup works," but "regress outcomes on a
rich feature panel and let the data tell us which structures/features carry
edge, then turn that into a testable strategy."

## The honest finding (verify-by-reading, not assumed)

A grounded read-only inventory (this session, 2026-07-26) found the picture is
**more built than "we have nothing," and more incomplete than "we have a
platform."** Three load-bearing facts:

1. **The ingredients — and even a real discovery analyzer — already exist and
   are high-quality.** We have a mature feature/label foundry (`ml/datasets/`,
   ~50 families, triple-barrier + CUSUM + range-vols, leakage-stamped), strong
   overfit discipline (purged walk-forward CV + embargo + `live_holdout` in
   `ml/experiments/splitters.py`; OOS-edge gating in `ml/promotion/oos_edge.py`),
   a faithful backtest bridge (`scripts/backtest_system.py` + per-strategy
   harnesses + `ml/datasets/backtest_recorder.py`), AND — critically — an actual
   open-ended discovery tool: **`scripts/research/component_edge_report.py`**
   (~1300 lines) already computes conditional edge tables (win-rate/mean-R/
   expectancy by feature bucket), univariate discrimination (Mann-Whitney AUC),
   and **multivariate marginal lift via a hand-rolled IRLS logistic regression**
   of realized-win on the decision-time feature vector, plus edge-decay and a
   verdict. So the operator's "we can't do multivariate regressions" is
   *directionally* right about the *workflow* but not literally true about the
   *code* — the regression analyzer is written; it just isn't a standing,
   integrated, surfaced platform.

2. **What's missing is the standing, integrated, cross-strategy DISCOVERY LOOP.**
   `component_edge_report.py` is **ad-hoc** — grep finds zero `.timer`/`.service`/
   `.yml`/`.sh` callers; it is not referenced by any review skill; there is no
   `/api/bot/signal-research/*` surface (designed, unbuilt). Of the already-
   designed framework (`signal-research-framework-DESIGN.md`), only **Layer 1a**
   (the component-edge report) is built; **Layer 2** (per-generator scorecard /
   GenScore), **Layer 1b** (hard-gate ablation knobs), and **Layer 3** (new-signal
   observe→graduate ladder) are **NOT built**. There is no cross-strategy feature
   **importance / redundancy / collinearity** instrument, no **standing**
   regression over the whole book, and — unlike the macro side — **no single
   technical-side research ledger** (findings are scattered across ~150
   `docs/research/*.md`).

3. **A load-bearing prior the platform must not ignore: entries are ~coin-flip;
   edge lives in exit + regime.** The discovery that *was* run
   (`where-edge-lives-entry-wall-2026-06-30.md`) found every entry-feature AUC at
   **0.49–0.52 OOS** — i.e. the ICT entry predicates (FVG-present, sweep,
   killzone, HTF-align) do not discriminate winners in the traded book. Two
   reasons this matters: (a) the platform's early studies should target where
   edge plausibly *is* (exit timing, regime conditioning, sizing) rather than
   re-litigate entries; (b) the hard entry gates are true for ~100% of *traded*
   rows, so their marginal value is **censored out of the journal** — measuring
   them at all requires eval-audit near-misses or backtest ablation (Layer 1b),
   which is unbuilt.

**Net:** the scoping work is **~70% assembly + completion** (promote L1a to a
standing surfaced instrument, build L2/L1b/L3, add a cross-strategy
regression/importance/redundancy instrument, add one ledger) and **~30% honest
methodology** (adopt the M28 funnel + ledger + honest-negative discipline for the
technical domain). It is **not greenfield** — most primitives are in hand.

## Relationship to the existing framework design (no duplication)

`docs/research/signal-research-framework-DESIGN.md` (status: *DESIGN — for operator
review before build*) already designed this as Layers 1a/1b/2/3 with a P0→P3 build
order and a `/api/bot/signal-research/soak` surface. **M30 does not replace it —
M30 is its execution + the two things it lacks:** (a) the operator's explicit
*data-first regression/importance/redundancy* instrument as a first-class
component (the design centers on per-strategy component attribution; the operator
wants cross-strategy, whole-panel discovery), and (b) a single compounding
**ledger** mirroring M28's, so every study (including every null) is a recorded
result. The design's Layer definitions, the shared adapter, and the "the gate is
already built and shared" principle are adopted verbatim.

## Grounded inventory — EXISTS vs GAP

| Area | State | Evidence | Gap to close in M30 |
|---|---|---|---|
| **Feature/label foundry** | ✅ mature | `ml/datasets/families/setup_candidates.py` (triple-barrier + CUSUM + past-only features + `won`/`won_r`/`r_multiple`), `market_features.py` (regime/vol/flow/macro/xa features), ~50 families, leakage-stamped | reuse as-is; the panel builder reads from here |
| **Market-structure / price-action features** | 🟡 computed + persisted, but strategy-specific | `order_packages.signal_logic` JSON (sweep/FVG/displacement/killzone/bias per decision), `signals.zones[]`; read adapter `src/research/component_vector.py` (covers ict_scalp/turtle/vwap/fade only) | **standardize** the panel across all strategies + per-bar; the hard gates are censored in the journal (needs L1b ablation) |
| **Discovery analyzer** | 🟡 built but ad-hoc | `scripts/research/component_edge_report.py` — conditional edge tables, AUC, IRLS logistic marginal-lift, decay, verdict | promote to **standing** (scheduled + surfaced); add SHAP/permutation importance, collinearity/redundancy, whole-book regression |
| **Overfit / CV discipline** | ✅ strong | `ml/experiments/splitters.py` (purged WF-CV + embargo + live_holdout), `ml/promotion/oos_edge.py` | reuse as the discovery gate (no discovery result stands without it) |
| **Backtest bridge** | ✅ multiple harnesses + portfolio gate | per-strategy `scripts/backtest_*.py`, `scripts/backtest_system.py` (real intent-netting), `ml/datasets/backtest_recorder.py` (feeds outcomes back into datasets) | wire as the **standing PnL gate** at the end of the discovery loop |
| **Methodology / ledger** | 🟡 macro side has it; technical side scattered | M28 `M28-signal-RnD-program.md` + `M28-signal-research-ledger.md` + `RESEARCH-RIGOR-STANDARD.md`; technical findings scattered across ~150 docs | add **one** technical research ledger + adopt the funnel + honest-negative rule |

## The platform (4 components)

### C1 · The research PANEL — one analysis-ready table
A single queryable per-trade (and per-bar) panel joining **outcome** (`won`,
`r_multiple`, realized-R, hold time, MAE/MFE) ← **decision-time features**
(the standardized `signal_logic` structure vector + `market_features` regime/vol/
session/flow + `model_scores`). Build by **extending `src/research/component_vector.py`**
to a standardized cross-strategy schema (fill the empty `trend_donchian` /
`htf_pullback_trend_2h` stubs) and a builder script that emits the panel from
`trade_journal.db` + the order-package join. **v1 needs no new data source** — it
is a join over data we already persist. Reuses the `ml/datasets/` builder+validator
abstraction (leakage-stamp the panel).

### C2 · The analysis TOOLKIT — standing discovery
Promote `component_edge_report.py` from ad-hoc to a standing instrument and add
the operator's explicit asks:
- **Conditional edge / expectancy-by-bucket tables** (have it) across the whole
  panel, per feature and per feature×regime cell.
- **Multivariate + logistic regression** of outcome on the feature panel (have
  IRLS logistic; add a documented interpretation + regularization + the R-multiple
  linear regression variant).
- **Feature importance** — permutation + (numpy-gated) SHAP over the panel.
- **Collinearity / redundancy map** — correlation + VIF so a "discovery" isn't
  three views of the same variable; interaction terms for the top predictors.
- **All under purged/embargoed CV** (`splitters.iter_folds`) with the M18
  coin-flip prior and multiple-comparisons control — discovery on the ~376-row
  real book (the M23 wall) will manufacture spurious correlations otherwise, so
  the CV + a Benjamini-Hochberg-style FDR control are **mandatory, not optional**
  (this is why C1 is coupled to the M23 label-volume work — a wider eval book is
  what gives the toolkit statistical power).

### C3 · The hypothesis→backtest BRIDGE
A discovered conditional edge → a candidate rule/overlay → the existing per-
strategy harness → `scripts/ops/m15_ws_b_fold_report.py` (anchored k-fold, net-of-
fee) → `scripts/backtest_system.py` (portfolio gate) → tier ladder → Tier-3 demo
PR. Nothing new to build here except the **standing wire** (a discovery result
carries its candidate rule spec forward automatically) + `backtest_recorder.py`
feeding the outcome back into the panel — closing the loop.

### C4 · The technical research LEDGER
One file — `docs/research/technical-signal-research-ledger.md` — mirroring
`M28-signal-research-ledger.md`: every study is a row (hypothesis · construction ·
panel · gate result · learning · verdict ∈ edge/weak/none/insufficient). **Every
null is recorded** (never a silent drop or retry-until-significant). This is the
compounding record the technical side lacks; it is the durable deliverable even if
individual studies come back null (exactly as the M28 macro program's own
conclusion demonstrated).

## First studies (the initial turns of the loop — informed by the coin-flip prior)

1. **Exit-timing edge study.** Where the 2026-06-30 finding says edge lives.
   Regress realized-R giveback / MAE-MFE-capture on decision-time + in-trade
   features; conditional-edge tables for the M20 exit levers. (Highest prior.)
2. **Regime-conditioned entry study.** Entries are coin-flip *overall* — are they
   coin-flip *within a regime cell*? Marginal lift of entry predicates conditioned
   on the regime heads (reuses `market_features` regime labels).
3. **Cross-strategy feature-redundancy map.** VIF/correlation over the full panel:
   which of the persisted `signal_logic` features are independent vs. three views
   of ATR/vol. Prunes the search space before deeper study.
4. **Hard-gate ablation (L1b).** Measure the marginal value of FVG-present /
   in-killzone / HTF-align via backtest `--disable-<condition>` ablation, since the
   journal censors them (they're ~100% of traded rows). Requires the L1b build.
5. **Sizing/conviction → outcome study.** Does `model_scores` / conviction predict
   realized R (calibration), the technical analogue of the M28 conviction-spread
   check.

## Data sources

**v1 needs no new external source** — the panel is a join over data we already
persist (`trade_journal.db`, `market_features`/`market_raw` datasets, the candles
we already fetch). Price/volume-derived structure is all keyless.

If a later study wants a NEW external technical data source (e.g. historical
order-book depth, options-flow/skew term-structure, on-chain), the rule
(operator-directed 2026-07-26) is: **prefer keyless/free; never sign up for a key
or register an account autonomously.** Candidates worth an API key get **flagged
in the morning summary** with name + what it provides + free-tier limits + why
worth it, for the operator to provision. Known keyless technical sources already
wired: Bybit public klines, yfinance (equities/metals daily). A key-gated source
already noted elsewhere: options skew needs the Schwab app registered (M31 Track-B,
operator-hold).

## Build sequence (M30 phases — all Tier-1, observe-only)

- **P0 (this doc)** — scoping + inventory + ledger seed. ✅
- **P1** — C1 the panel builder (extend `component_vector.py` to a standardized
  cross-strategy schema + a panel-emit script). *Pure analysis code → mergeable.*
- **P2** — C2 the standing toolkit (promote `component_edge_report.py` + add
  permutation/SHAP importance + collinearity/VIF + FDR control, all under purged
  CV). *Pure analysis code → mergeable.*
- **P3** — run study #1 (exit-timing) end-to-end on the panel; record the result
  (edge or null) in the C4 ledger. *Findings doc → mergeable.*
- **P4** — C3 the standing backtest wire + `backtest_recorder` loop-back. Any
  change touching a per-strategy harness or `src/` stays a **draft PR** for
  operator review.
- **P5+** — complete the designed L2 (generator scorecard) + L3 (new-signal
  ladder) + the `/api/bot/signal-research/soak` surface; run studies #2–5.
  Anything that would place a live per-bar soak on the money box is **Tier-3,
  operator-gated** (per the framework design's own autonomy note).

**Nothing in M30 changes a live trading decision.** A discovered edge only ever
becomes a live change by clearing the existing net-of-cost walk-forward gate
out-of-sample AND operator approval (Tier-3) — identical to every other strategy
path. M30 builds the *discovery instrument*; the *gate* is unchanged and shared.
