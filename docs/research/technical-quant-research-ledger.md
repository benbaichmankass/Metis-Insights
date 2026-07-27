# M30 — Technical quant-research ledger

The compounding record of every **technical / price-action feature→outcome
discovery** run through the M30 research platform, its honest verdict, and the
learning. The price-action counterpart of the macro
[`M28-signal-research-ledger.md`](M28-signal-research-ledger.md) — same
append-only discipline: **a null is a completed entry, never a non-event**
(`RESEARCH-RIGOR-STANDARD.md` § honest negatives). One row per study
(cohort × feature set × outcome × method).

Platform + method: the scoping doc
[`technical-quant-research-platform-scoping-2026-07-27.md`](technical-quant-research-platform-scoping-2026-07-27.md).
The instruments:

- **C1 panel** — `scripts/research/build_research_panel.py` joins each closed
  trade's **outcome** (`pnl`/`win`/R-multiple) to its **decision-time features**
  (structure vector + regime/vol categoricals + gates + model-score summary),
  leakage-stamped.
- **C2 toolkit** — `scripts/research/analyze_research_panel.py`: conditional
  edge/expectancy-by-bucket tables + multivariate logistic/ridge-OLS regression
  + permutation importance + correlation/VIF, **all under purged/embargoed
  walk-forward CV** (`ml.experiments.splitters.iter_folds`) + a Benjamini-Hochberg
  FDR control.

## The bar (why most rows will be nulls)

Discovery on the small real book — the M23 **~376-row wall** — manufactures
spurious correlations. The **M18 coin-flip prior** is binding: a feature is a
**LEAD, not a finding**, unless it **both**

1. **survives Benjamini-Hochberg FDR** at the chosen alpha over the full
   univariate-p set (never a single cherry-picked p), **and**
2. shows **positive out-of-sample discrimination** under the purged/embargoed
   walk-forward CV (OOS AUC > 0.5 for `win`, OOS R² > 0 for `r`) — an in-sample
   coefficient is never a gate.

A feature that clears both is still only a hypothesis to route into the C3
backtest→walk-forward bridge (the standing entry gate), never a direct
config change (that is Tier-3, operator-gated).

| # | Cohort · book | Feature(s) probed | Outcome | Method | Honest verdict | Learning |
|---|---|---|---|---|---|---|
| 0 | **Synthetic** (platform validation — NOT a live finding) | injected `feat_confidence` (drives P(win)) + noise model-score features | `win` | C1→C2 end-to-end on a 220-row synthetic ISO-timestamped `trade_journal.db`; purged WF-CV (5 folds), BH-FDR α=0.1 | `platform_validated` — the injected signal is recovered end-to-end: **OOS AUC 0.72** across 5 purged folds, `feat_confidence` **tops permutation importance (0.25)** AND is the **sole BH-FDR survivor**; the noise features sit at ≈0 importance and do not survive. | **The platform works and the guards bite.** The CV recovers a real injected edge out-of-sample and the FDR correctly rejects the noise — so a null on the real book will be a real null, not a broken pipeline. This entry is a *pipeline* proof on fabricated data; it says **nothing** about any live strategy. The smoke that produced it also caught + fixed a real null-`closed_at` CV-crash bug (#7687) before the real-book run. |
| 1 | **All strategies · real (377) + real+paper (612)** — live closed trades, VM-side run (2026-07-27) | full 15-col decision-time panel (structure vector + regime/vol cats + gates + model-score summary) | `win` (+ `r`) | C1 (no `--strategy` filter) → C2, purged WF-CV (5 folds), BH-FDR α=0.1, on the **live** `trade_journal.db` via the trainer-vm-diag relay | **LEADS-ONLY (multivariate blocked).** 2 BH-FDR univariate survivors — `feat_vwap_deviation_std` (n=274, p=0.0073, q=0.044 real / 0.079 both) + `feat_model_score_mean` (n=38/41, p=0.021/0.014, q=0.064 real / 0.079 both) — stable across real/win, real/r, both/win. Multivariate regression + permutation-importance + VIF **not computed**: **0 complete-case rows** across the 11 graded feats (block-sparse by strategy), so criterion (b) OOS-discrimination is **unmet** → **LEADS, not findings**. | The real book is **84% one strategy** (`vwap` = 318/377); features are block-sparse (non-null of 612: confidence 409, vwap-dev 274, adx 61, regime 54, model-score 41, **all others ≤11**). The `vwap_deviation_std` lead is **confounded with strategy identity** (populated ≈only on vwap trades). → platform iteration #1: **per-strategy panels + a cross-strategy common-core feature set**. |
| 2 | **`vwap`, real-money** (318 closed trades) — per-strategy, VM-side (2026-07-27) | vwap's own dense features — only **2 graded feats instrument on vwap**: `feat_confidence`, `feat_vwap_deviation_std` | `win` + `r` | C1 `--strategy vwap` → C2, purged WF-CV (5 folds), BH-FDR α=0.1 | **WEAK on `win`, NULL on `r` — not a confirmed finding.** Multivariate now runs (complete cases exist at 2 dense feats). `feat_vwap_deviation_std`: FDR q=0.0145; **OOS win AUC 0.593** but unstable (folds [0.87, 0.68, 0.48, 0.54, 0.39] — 2/5 < 0.5); **OOS `r` R² −0.54** (fails — worse than the mean). `feat_confidence` perm-importance 0.0. VIF clean. | Even per-strategy, **vwap instruments only 2 graded decision-time features** — the "multivariate" is effectively bivariate. The lead clears FDR (a) but OOS discrimination (b) is marginal+fragile for win and negative for r → **does not clear the bar**; also strategy-mechanical (vwap-deviation on a vwap strategy ≈ "how far the entry sat from vwap"). → the binding gap is now **feature-CAPTURE breadth**, not row count. |
| 3 | **All strategies · real (377)** — pooled common-core, VM-side (2026-07-27) | common-core dense strategy-agnostic cols via the P1 `--features` selector: `feat_confidence`, `feat_model_score_mean`, `feat_model_score_max`, `feat_adx_14` | `win` + `r` | C1 (pooled, `--db` populated journal) → C2 `--features …`, purged WF-CV (5 folds), BH-FDR α=0.1 | **NULL — `feat_model_score_mean` FAILS its first OOS test.** P1 `--features` works (mv fit restricted to the 4 requested cols, none ignored/missing). But listwise-complete cases across the 4 collapse to **35 rows** (model-score pair populate ~41/377) → **`win` OOS not computable** (degenerate CV, <2 usable folds) and **`r` OOS R² −10.79** on the single usable fold (catastrophic — far worse than the mean). FDR survivors unchanged (vwap_dev q=0.044, model_score_mean q=0.064). VIF clean. | The Study-1 lead `feat_model_score_mean` gets its OOS test at last — and **does not clear (b)**. Root cause is the same at the pooled level as per-strategy: the strategy-agnostic ML features are too **sparse** (~41/377), so even the densest common-core can't muster a powered complete-case set. **The binding constraint is decision-time feature DENSITY, not the `--features` mechanic.** → next common-core should drop the sparse `model_score_*` and lean on the P4-widened dense cats (`feat_confidence`+`feat_adx_14`+`cat_killzone`/`cat_bias`/`cat_setup_type`); grow rows via L3 paper-book + P2 sweep. |
| 4 | **Every strategy · real (377)** — P2 sweep, one pass, VM-side (2026-07-27) | full panel per group; strategies ≥ floor 30 get their own C2, thinner books pooled by asset class | `win` + `r` | **P2 sweep driver** (`sweep_research_panels.py`) → C1 once + C2 per group, purged WF-CV, BH-FDR α=0.1, power-floor 30 | **NOTHING NEW clears the bar.** Only 2 groups reach the floor: **`vwap` (318)** — `win` auto-**candidate_finding** (`feat_vwap_deviation_std`, OOS AUC 0.611) but per-fold **[0.83, 0.49, 0.56, 0.42, 0.75] = 2/5 < 0.5** → the SAME unstable, strategy-mechanical lead Study 2 already declined; `r` **lead** (OOS R² −0.065). **`asset:crypto` pool (58, 10 thin books)** — **null** both: no FDR survivor AND multivariate not computable (**0 complete-case rows** for 10 feats — block-sparse pooling doesn't rescue it). `asset:bond` (n=1) underpowered. | **The P2 driver is validated end-to-end on the real book** — one command covered the whole roster. Coverage verdict: the real book is too thin + block-sparse for cross-strategy discovery — pooling grows the *univariate* denominator but **not the complete-case count** the multivariate needs, so the crypto pool nulls. The P4 session/bias cats surfaced **no** FDR survivor either → the regime/session-conditioned angle is a null at this cut. Also a **driver caveat**: the auto `candidate_finding` label (mean OOS AUC > 0.5) is a *triage flag*, not a confirmation — it re-flagged the known unstable vwap lead; per-fold stability + mechanical-confound scrutiny still gate a true finding. |
| 5 | **Live-journal exit-timing** (relay #7742) | — | `giveback_r` / excursions | — | **SUPERSEDED — not landed as a live-journal study.** The operator re-scoped discovery to the **backtest substrate** (2026-07-27 CORRECTION): MFE/MAE come free on the backtest candle path, so the offline range-fetch exit-timing run on the ~376-row journal is subsumed by the C1-for-backtests platform (row 6). No live-journal exit study recorded. | The offline range-fetch approach (P5, #7737/#7741) is superseded by the in-memory backtest candle path — the backtest loop already holds the bars, so no per-trade historical fetch is needed. The P5 exit-panel toolchain remains valid for a live-journal exit study if ever wanted; it is simply not the discovery substrate. |
| 6 | **Platform · backtest substrate** (C1-for-backtests) — the PIVOT, NOT a live finding | full ict_scalp decision-time vector (sweep/fvg/displacement/mitigation/regime/adx/htf-gate) + native MFE/MAE/giveback | `win` + `r` + excursions | `build_backtest_panel.py` (new bridge) → the **existing** C2 analyzer unchanged, purged WF-CV + BH-FDR | `platform_built` — the backtest bridge produces a **feature-RICH** panel: on ict_scalp it emits **9 feature columns** (sweep_depth_atr, fvg_size_atr, displacement_strength, adx_14, confidence, mitigation_mode, regime, setup_type, htf_bias gate) vs the live journal's **2** for the same strategy — Study 2's binding feature-capture-breadth constraint is **solved on this substrate** — plus full excursion outcomes (mfe_r/mae_r/giveback_r/capture_ratio/…) at 100% coverage. C2 reads it unchanged: leakage `clean:true`, `manifest_asserted:true`, both `--outcome win` and `--outcome giveback_r` run. **No discovery result yet:** the committed candle sample (`data/backtest_candles.csv`, 5k bars) yields only ~4 ict_scalp trades — a large-N run needs the full 5m feed (VM-side / a fetched history). | **The substrate change is the whole game.** The prior 5 studies nulled because discovery ran on the row-starved, block-sparse ~376-row journal (M18 coin-flip prior binding). The backtest gives large N *and* live-faithful features (it calls the live `order_package` per bar) *and* MFE/MAE for free — the three things the journal couldn't. This is a *platform* entry (like Entry 0), not a live finding: it proves the bridge is wired and C2-clean end-to-end. The first real discovery run is gated only on a large candle feed. |
| 7 | **`ict_scalp_5m` · backtest** — 282 simulated trades on ~1.4yr real 5m BTCUSDT (VM-side, relay #7747) | the 9-col decision-time vector (sweep_depth/fvg_size/displacement/adx/confidence + mitigation/regime/setup_type cats + htf gate) | `win` + `r` + `giveback_r` | C1-for-backtests bridge → C2, purged WF-CV (5 folds), BH-FDR α=0.1 | **NULL on a POWERED sample — the first real out-of-sample null the platform has produced.** 282 trades (vs 13 real ict_scalp in the journal — ~20×); the multivariate + OOS pass **actually runs** (`regression: computed`, not the "0 complete-case rows / not_computed" of Studies 1–4). **No FDR survivor** on any outcome. OOS: **`win` AUC 0.443** (folds [0.49, 0.38, 0.42, 0.34, 0.59] — below chance), **`giveback_r` R² −0.177** (all folds negative), **`r` R² −0.063** — all **fail (b)**. Permutation importances ≈0/negative across the board; `feat_confidence` high-VIF (redundant); interaction leads tiny (~0.03). | **The substrate delivers, and the honest answer is a clean null.** For the first time discovery ran on a **powered** panel where the OOS pass computed — and ict_scalp's decision-time **entry** features are **~coin-flip out-of-sample** (`win` AUC 0.44 < 0.5), directly **confirming the platform's load-bearing prior** ("entries are ~coin-flip; edge lives in exit/regime") on 282 trades. The **exit-timing** angle (`giveback_r`) also nulls **at this N** — but 282 is still modest (~1.4yr); the exit prior is not refuted, just untested at scale. Next turns: the **full 647k-bar feed** (~1200 trades) + **per-regime-cell** conditioning + **more strategies** (system/vwap adapters). |
| 8 | **M36 Track D** — substrate broadened + full exit-outcome sweep + M16 backbone (2026-07-27) | (tooling) `backtest_system` portfolio adapter + per-regime-cell C2b driver | full excursion set (`giveback_r`/`capture_ratio`/`mae_r`/`time_to_mfe_frac`) + per-regime `win` | **1525 trades** (full 647k feed, detached #7761 → read-back #7763); tooling #7752; infra #7764 | **POWERED result — the story flips from Study 7.** `win`: **FDR survivor `cat_regime`, OOS AUC 0.538, all 5 folds > 0.5** — a thin but STABLE regime-conditioned ENTRY edge at scale (Study 7's 0.443 at N=282 was noise), **concentrated in chop (0.551)** vs trending/transitional (~0.51). Exit outcomes: `giveback_r` R² −0.03 / `capture_ratio` −1.47 = **firmly NULL**; `mae_r`/`time_to_mfe` have thin univariate FDR survivors (cat_regime/adx, sweep_depth) but ~0 multivariate OOS. So "entries are pure coin-flip" is too strong (a thin regime-conditioned entry signal exists), while "edge lives in EXIT" is **not** supported from decision-time features. First FDR-surviving OOS lead on real powered volume. | **M30 becomes the integration backbone.** The tooling broadens discovery past one strategy + adds the per-regime lens; the entry edge is a **regime/FVG/ADX-conditioned ENTRY**, not an exit overlay — the next look. **Phase 2:** M30 panels feed the **M16 conviction master model** as `source="backtest"` training rows (`conviction_meta` `source_mode` axis + `conviction-meta-v1-bt` manifest, #7756) — the design-§4.5 augmentation that escapes the ~99-live-label T0.3 bottleneck. **Infra:** bounded relays all timed out → the stable fix is the self-contained GH-runner `research-panel-build` workflow (#7764), NOT GPU (CPU-bound replay). Observe-only; Tier-3 to influence. |

### Study 1 detail — pooled real-book first pass (2026-07-27)

The first real feature→outcome discovery, run VM-side on the trainer's fresh
synced `trade_journal.db` (897 closed trades → panel; relay issues #7689–#7691).
Entry 1 was originally scoped to `trend_donchian` alone, but that book is **8
real rows** — below any power floor — so the honest first study is the **pooled
all-strategy panel**, with the per-strategy sweep deferred to iteration.

**Panel (C1).** 377 real / 612 real+paper closed trades, 15 decision-time
feature columns, leakage contract **clean** (`manifest_asserted: true`,
feature/outcome overlap `[]`). Base `win` rate 26.5% (real) / 26.8% (both).

**What computed (the sparsity-tolerant half).** Univariate conditional-edge +
BH-FDR (α=0.1, m=6 testable features). **Survivors, stable across all three
specs** (real/win, real/r, both/win):

| Feature | n (measured) | univariate p | BH q (real / both) |
|---|---|---|---|
| `feat_vwap_deviation_std` | 274 | 0.0073 | 0.044 / 0.079 |
| `feat_model_score_mean`   | 38 / 41 | 0.021 / 0.014 | 0.064 / 0.079 |

**What did NOT compute (and why it matters).** Multivariate logistic/OLS
regression, permutation importance, and VIF all returned an honest
`not_computed`: **0 complete-case rows** across the 11 graded features (the
toolkit needs ≥ max(20, 5·n_feat)). Cause — the graded features are
**block-sparse by strategy**: non-null counts (of 612) are `feat_confidence`
409, `feat_vwap_deviation_std` 274, `feat_adx_14` 61, `cat_regime` 54,
`feat_model_score_{mean,max}` 41, and **every other feature ≤ 11**
(`feat_fvg_size_atr` / `feat_sweep_depth_atr` fire only for ict_scalp,
`feat_fade_adx` only for fade, etc.). No single trade carries all 11, so
listwise-complete cases collapse to zero when the book is pooled.

**Why the leads stay LEADS, not findings.** Per the platform's binding bar, a
feature is confirmed only if it clears FDR **and** shows positive OOS
discrimination under the purged WF-CV. The OOS half lives in the (blocked)
multivariate/importance path, so it **could not run** — both survivors clear (a)
only. Worse, the real book is **84% `vwap`-strategy** (318/377; next largest:
ict_scalp_5m 13, trend_donchian 8, eth_pullback_2h 8, fade_breakout_4h 7), so
`feat_vwap_deviation_std` (n=274) is **confounded with strategy identity** — it
is populated almost exclusively on vwap trades, so its univariate edge is
partly "this is a vwap trade," not a strategy-agnostic price-action edge.
`feat_model_score_mean` (a cross-strategy ML feature) is the cleaner lead.

The FDR guard demonstrably bites: `gate_htf_bias_aligned` showed a 72.7%
true-bucket win-rate (vs 26.8% base) but on **n=11** → q=1.0, correctly rejected
as noise — exactly the small-book coin-flip the platform exists to catch.

**Disposition → platform iteration #1** (the study's real yield):

1. **Per-strategy panels** — a `vwap`-only panel (318 rows, its own features
   dense) unlocks the multivariate + OOS-importance pass that the pooled panel
   can't support; ditto any strategy above the power floor.
2. **A cross-strategy common-core feature set** — restrict the pooled
   multivariate fit to the densely-populated, strategy-agnostic columns
   (`feat_confidence`, `feat_model_score_*`, `cat_regime`, and where present
   `feat_adx_14`) so complete-case rows exist across strategies.
3. Carry **`feat_model_score_mean`** as the cleaner lead into the C3
   backtest→walk-forward bridge once (1)/(2) give it an OOS reading; treat
   `feat_vwap_deviation_std` as a vwap-scoped lead pending the per-strategy run.

Both iteration items are **C1/C2 usage changes** (a `--strategy` filter is
already supported; a common-core `--features` selector is a small additive
toolkit option) — no order-path or config touch. Tier-1, observe-only.

### Study 2 detail — `vwap` per-strategy panel (2026-07-27)

Platform iteration #1 from Study 1: isolate the dominant book (`vwap`, 318 real
closed trades) so its own features are dense and the multivariate + OOS pass the
pooled panel couldn't support finally runs (relay #7693).

**Panel (C1 `--strategy vwap`).** 318 rows — but only **2 graded feature columns
survive the density filter**: `feat_confidence` (318/318) and
`feat_vwap_deviation_std` (274/318). The vwap signal builder simply does not
record the other graded decision-time features (adx, model-score, fvg/sweep
geometry, regime/vol cats), so per-strategy the "multivariate" is effectively
**bivariate**. Leakage clean.

**Criterion (b) — OOS discrimination (now computable):**

| Outcome | Model | OOS metric (5 purged folds) | Per-fold | Verdict |
|---|---|---|---|---|
| `win` | logistic | **AUC 0.593** | [0.87, 0.68, 0.48, 0.54, 0.39] | weak + **unstable** (2/5 folds < 0.5) |
| `r` | ridge-OLS | **R² −0.54** | [−2.45, −0.05, −0.16, 0.00, −0.06] | **fails** (worse than the mean) |

Permutation importance: `feat_vwap_deviation_std` 0.067 (win) / 0.103 (r);
`feat_confidence` **0.0** (no standalone edge). VIF clean; top interaction
`confidence × vwap_deviation_std` (0.108).

**Verdict — NOT a confirmed finding.** `feat_vwap_deviation_std` clears FDR
(a = q 0.0145) but its OOS discrimination (b) is **marginal-and-fragile for
`win`** (mean AUC barely > 0.5, two folds below chance) and **negative for `r`**.
Under the platform's binding bar it does **not** graduate lead → finding. It is
also **strategy-mechanical** — on a vwap strategy, "vwap-deviation at entry"
partly encodes the setup itself, not an independent edge. Do **not** route it to
the C3 backtest bridge yet.

**The real yield — an instrumentation finding.** Study 1 said the gap was row
count + block-sparsity; Study 2 sharpens it: **even the dominant, data-rich book
carries only 2 graded decision-time features.** The binding constraint on
multivariate discovery is therefore **feature-capture breadth**, not just eval
volume. Two concrete platform loose-ends fall out:

1. **Widen decision-time feature capture** per strategy (the signal builders /
   `src.research.component_vector` extraction) so a trade records more of the
   structure / regime / session / vol vector it actually saw — the
   highest-leverage thing for real multivariate discovery.
2. **L3 paper-book eval population** (operator-approved 2026-07-27) — admit the
   soak paper books as a distinct tagged population to grow the eval count past
   the ~376 real-money wall; paper trades exist precisely to accrue this.

### Study 3 detail — pooled common-core panel (2026-07-27)

Platform iteration #1's second item from Study 1: consume the new **P1 C2
`--features` selector** (merged `f4cbc3b`, #7718) to restrict the pooled
multivariate fit to the strategy-agnostic dense columns, so
`feat_model_score_mean` — the cleaner cross-strategy lead from Study 1 — finally
gets the OOS discrimination test criterion (b) demands. Run VM-side via the
`trainer-vm-diag` relay (#7725, after the first attempt #7720 mis-resolved the
empty repo-root DB — see the repro note below).

**Panel (C1, pooled `--cohort real`).** 377 real closed trades, 15 feature
cols, leakage clean (`manifest_asserted: true`). Sourced from the populated
`/home/ubuntu/ict-trading-bot/data/trade_journal.db` (897 closed) via `--db`.

**The P1 `--features` mechanic works.** The mv fit was restricted exactly to the
requested `['feat_confidence', 'feat_model_score_mean', 'feat_model_score_max',
'feat_adx_14']` — `applied: true`, nothing ignored-as-non-graded, nothing
missing-from-panel. So the selector delivers what Study 1 asked for.

**Criterion (b) — OOS discrimination:**

| Outcome | Model | Complete-case rows | OOS metric | Verdict |
|---|---|---|---|---|
| `win` | logistic | ~35 | **not computed** — no fold had both a stable fit AND a defined OOS metric (only 1 usable purged fold) | **cannot clear (b)** |
| `r` | ridge-OLS | 35 (cv_rows 35, folds_usable **1**) | **OOS R² −10.79** (single fold [−10.79]) | **fails (b)** — catastrophically worse than the mean |

FDR survivors unchanged from Study 1 (`feat_vwap_deviation_std` q=0.0435,
`feat_model_score_mean` q=0.0644). The `r` regression's in-sample standardized
coeffs (adx 0.32, model_score_mean 0.21, confidence 0.13) and permutation
importances are **leads only** and, sitting on a model with R² ≪ 0, carry no
weight. VIF clean; top interaction `confidence × model_score_mean` (0.24).

**Verdict — NULL.** `feat_model_score_mean` gets its long-awaited OOS test and
**does not clear the bar**: `win` OOS is not even computable and `r` OOS is
deeply negative. It stays a lead that has now **failed** its first honest OOS
reading on the pooled common-core — **do not route to the C3 bridge.**

**The real yield — density, not the mechanic, is binding.** The listwise-complete
set across the 4 chosen dense columns is only **35 of 377 rows**, because the
strategy-agnostic ML features (`feat_model_score_{mean,max}`) fire on just
~41/377 trades, so their intersection with `feat_adx_14` (~61) collapses the
powered sample below any purged-CV floor. This is the pooled-level restatement
of Study 2's instrumentation finding: **the binding constraint on multivariate
discovery is decision-time feature DENSITY, not row count and not the
`--features` selector** (which works). Concrete dispositions:

1. **Re-pick the common-core off the P4-widened capture.** P4 (#7723, merged)
   now lands `cat_killzone` / `cat_bias` / `cat_setup_type` on every strategy
   from `order_packages.meta`. The next pooled run should drop the sparse
   `feat_model_score_*` pair and use `feat_confidence` + `feat_adx_14` + those
   three common cats — densely populated across strategies, so complete-cases
   should survive. (Best driven by the P2 sweep, not hand-run.)
2. **Grow rows** via the L3 paper-book population (merged) + pooling thin books
   by asset class in the P2 sweep — attacks the sample-size half.
3. **Model-score density is its own gap** — if `feat_model_score_mean` is to be
   testable pooled, decision-time shadow-prediction capture must be denser than
   ~11% of trades. Logged as the model-score-capture follow-up (P4-adjacent).

Tier-1, observe-only; no order-path or config touch.

### Study 4 detail — P2 per-strategy sweep, one pass (2026-07-27)

The first use of the **P2 sweep driver** (`scripts/research/sweep_research_panels.py`,
#7730) — self-serve coverage across the whole roster in one command instead of a
hand-run per book. Run VM-side via the `trainer-vm-diag` relay (#7732) against the
populated real journal (377 rows, power-floor 30).

**Coverage.** Only **2 of the roster's ~20 strategies clear the floor** as an
analyzable group; everything else is a thin book:

| Group | kind | n | `win` verdict | `r` verdict | FDR survivor |
|---|---|---|---|---|---|
| `vwap` | strategy | 318 | candidate_finding (auto) | lead | `feat_vwap_deviation_std` |
| `asset:crypto` | asset_pool (10 books) | 58 | null | null | — |
| `asset:bond` | — | 1 | underpowered | underpowered | — |

**`vwap` — the auto `candidate_finding` is the known, already-declined lead.**
`win` OOS AUC **0.611** with FDR survivor `feat_vwap_deviation_std` trips the
driver's `candidate_finding` label — but the per-fold trace is
**[0.83, 0.49, 0.56, 0.42, 0.75]** (2/5 < 0.5), the same instability Study 2
scrutinized, and it is the same strategy-mechanical feature ("how far the entry
sat from vwap" on a vwap strategy). `r` is a **lead** (OOS R² −0.065, all folds
negative). So the driver **correctly narrows** discovery to this one cell, but the
Study-2 verdict stands: it does **not** graduate to a confirmed finding. **Nothing
new surfaced.**

**`asset:crypto` pool — pooling does NOT rescue block-sparsity.** Pooling the 10
thin crypto books (ada/eth/xrp pullbacks, ict_scalp, trend_donchian family,
squeeze, fade, htf_pullback) gives n=58 but **0 complete-case rows across the 10
graded feats** — each book instruments different columns, so listwise-complete
cases stay zero even pooled. The multivariate can't run; the univariate FDR runs
and finds **no survivor** → an honest null. This is the pooled restatement of the
Studies 1–3 lesson: **pooling grows the univariate denominator, not the
complete-case count the OOS pass needs.** (A follow-up sweep with `--features`
restricting to the dense graded common-core — `feat_confidence`, `feat_adx_14` —
is the way to let a pool attempt the multivariate; not run here.)

**Regime/session (P4 cats) — null at this cut.** The P4-widened
`cat_killzone` / `cat_bias` / `cat_setup_type` (+ `cat_regime`/`cat_vol_regime`)
are in every group's panel and feed the univariate FDR, but **none surfaced as an
FDR survivor** in either analyzable group, and as categoricals they don't enter
the graded multivariate fit. So the widened session/bias context does **not**
yield a univariate edge on the real book at the per-strategy / per-pool cut — the
regime/session-conditioned entry angle is a **null** here (its stronger form is
the feature×regime-cell interaction analysis + the exit-timing study, both of
which need more instrumentation/rows than the current book supports).

**Driver caveat (a real P2 follow-up).** The auto `candidate_finding` verdict
uses **mean OOS AUC > 0.5**, which is a permissive *triage* threshold — it flagged
the unstable vwap lead a human read (Study 2) declined on per-fold stability +
mechanical confound. The label is a search-narrowing flag, **not** a confirmation;
the confirmation gate remains stricter. A worthwhile driver refinement: annotate
`candidate_finding` with a fold-stability flag (e.g. "k/5 folds < 0.5") so the
summary carries the caveat inline. Logged in the queued items below.

**Net:** the sweep is validated and the roster is now covered in one pass — and
the honest finding is that **the real book is too thin + block-sparse for
cross-strategy discovery today.** The compounding value is the coverage map: it
tells the next session exactly which cells are exhausted (vwap = mechanical lead
only; crypto pool = block-sparse null; everything else sub-floor) so no compute is
burned re-deriving them. The unblock is instrumentation density (P4 continued) +
eval volume (L3 paper book), not more discovery passes on this book.

### Study 6 detail — C1-for-backtests: the backtest-substrate pivot (2026-07-27)

The operator's re-scope (2026-07-27 CORRECTION on the coordination board): **stop
discovering on the ~376-row live journal; discover on the backtest engine.** The
backtest already produces large-N samples with the full candle path in memory —
that is the discovery substrate; the live/paper journal is validation. Studies 1–5
nulled for a *structural* reason (row-starvation + block-sparsity under the M18
prior), not because there is no edge.

**The bridge — `scripts/research/build_backtest_panel.py`.** Runs a backtest harness
in-process and emits, per simulated trade, the **same C1 schema** the journal panel
produces: decision-time features via `src.research.component_vector.extract` +
outcomes `win`/`r` + **native MFE/MAE/giveback** from the backtest's own candle path
via `src.research.excursions.compute_excursions`. Feeds the **existing** C2 analyzer
(`analyze_research_panel.py`) unchanged, under purged/embargoed WF-CV + BH-FDR. A
harness-adapter architecture; the flagship **ict_scalp** adapter is implemented
(system / vwap adapters are the documented follow-ups).

**Why the backtest panel is feature-rich where the journal is sparse.** The bridge
targets the harnesses that call the **live signal builder** per bar
(`scripts/backtest_ict_scalp.py` → live `order_package`), so each simulated trade
carries the same rich `meta` a live order-package would. On ict_scalp the panel
emits **9 feature columns** — `feat_sweep_depth_atr`, `feat_fvg_size_atr`,
`feat_displacement_strength`, `feat_adx_14`, `feat_confidence`,
`cat_mitigation_mode`, `cat_regime`, `cat_setup_type`, `gate_htf_bias_aligned` —
against the **2** graded feats the same strategy's live book carried (Study 2). The
binding feature-capture-breadth constraint Studies 1–3 identified is **solved on
this substrate.** Excursion outcomes are 100%-covered (the candle path is always in
memory). The harnesses that only re-implement a thin entry inline
(trend/fade/squeeze/ICTBacktester) are deliberately **not** wired — they'd feed C2
an empty feature vector, the very sparsity the pivot escapes.

**Leakage discipline (the whole game on a backtest — future info leaks trivially).**
Three guards: (a) the feature set is EXACTLY `component_vector.extract`'s output,
which reads only decision-time specs — the harness ALSO stamps outcome keys on the
meta (`mfe_r`/`exit_price`/`bars_held`), and those can never enter a `feat_` column
because no spec reads them (unit-tested: `feat_mfe_r`/`feat_exit_price` never
appear); (b) the excursion columns are stamped OUTCOMES in the manifest, never
regressors; (c) the C2 purged WF-CV orders rows by `closed_at` = the trade's **exit
timestamp**, so the split respects backtest time order, not row order.

**Validation (this session).** End-to-end on the committed sample
(`data/backtest_candles.csv`, 5k bars): bridge → 4-row panel (9 feature cols, 100%
excursion coverage, ISO-8601 `closed_at`) → C2 reads it with leakage `clean:true` +
`manifest_asserted:true`, both `--outcome win` and `--outcome giveback_r` (exit
timing) run and correctly decline the fit as underpowered at N=4. 7 new unit tests
(synthetic-adapter, offline) + the 51 existing ict_scalp/component/excursion tests
pass; ruff clean. The harness change is a **single additive, default-off**
`return_trades` hook on `run_backtest` (every existing caller byte-for-byte
unchanged).

**No discovery result yet — gated only on a large candle feed.** The committed
sample yields ~4 ict_scalp trades; the whole point (large N) needs the full 5m
history (a fetched/VM-side feed). This is a *platform* entry (like Entry 0): it
proves the bridge is wired + C2-clean, and says nothing about any live edge. The
first real discovery run — ict_scalp on years of 5m BTCUSDT, `win` + `giveback_r`
under the FDR×OOS bar — is the immediate next turn.

**Reproduce:**

```bash
python scripts/research/build_backtest_panel.py \
    --harness ict_scalp --data <5m_ohlcv.csv> --stamp-regime \
    --out runtime_logs/research/bt_ict_scalp_panel.jsonl
python scripts/research/analyze_research_panel.py \
    --panel runtime_logs/research/bt_ict_scalp_panel.jsonl --outcome win
python scripts/research/analyze_research_panel.py \
    --panel runtime_logs/research/bt_ict_scalp_panel.jsonl --outcome giveback_r
```

### Study 7 detail — ict_scalp on the real 5m feed, first powered run (2026-07-27)

The first discovery run on the backtest substrate (Study 6's bridge), VM-side via
the `trainer-vm-diag` relay (#7747) against the real committed feed
`data/backtest_BTCUSDT_5m.csv` (**647,586 5m BTCUSDT bars, ~6 yr**), bounded to the
most-recent **150,000 bars (~1.4 yr)** to keep the run fast + un-preempted.

**Panel (C1-for-backtests).** **282 simulated ict_scalp trades**, 9 feature columns,
**100% excursion coverage**, leakage `clean:true` / `manifest_asserted:true`. Harness
sanity: win-rate 52.5%, expectancy +0.171 R. For comparison the live journal carries
**13** real ict_scalp closed trades — this is a **~20× larger, feature-dense** panel,
and (unlike Studies 1–4) the multivariate + permutation-importance + VIF pass **all
compute** (complete-case rows exist because the features are dense on this substrate).

**Criterion (a) — BH-FDR (α=0.1, m=9): no survivor** on any of `win` / `giveback_r`
/ `r`. **Criterion (b) — OOS discrimination (5 purged folds):**

| Outcome | Model | OOS metric | Per-fold | Verdict |
|---|---|---|---|---|
| `win` | logistic | **AUC 0.443** | [0.49, 0.38, 0.42, 0.34, 0.59] | **fails** — below chance (4/5 folds < 0.5) |
| `giveback_r` | ridge-OLS | **R² −0.177** | [−0.05, −0.56, −0.00, −0.17, −0.10] | **fails** — all folds negative |
| `r` | ridge-OLS | **R² −0.063** | [−0.12, −0.11, −0.06, −0.06, +0.04] | **fails** — worse than the mean |

Permutation importances are ≈0 or negative for every feature on every outcome (no
feature carries OOS signal); `feat_confidence` is high-VIF (redundant with the
geometry); top interaction leads are tiny (~0.03–0.04).

**Verdict — NULL, and it is a *meaningful* null.** This is the **first time the
discovery machinery ran end-to-end on a powered sample with the OOS pass actually
computing** — Studies 1–4 could never reach criterion (b) (0 complete-case rows).
The clean out-of-sample result: **ict_scalp's decision-time ENTRY features are
~coin-flip** (`win` AUC 0.44 < 0.5), which **directly confirms the platform's
load-bearing prior** — "entries are ~coin-flip; edge lives in exit/regime"
(`where-edge-lives-entry-wall-2026-06-30.md`) — now on 282 backtest trades, OOS, not
just the traded-journal cut. The **exit-timing** angle (`giveback_r`) also nulls, but
**only at N=282 over ~1.4 yr** — the exit prior is **not refuted**, merely untested at
scale; the immediate next turn is the full 647k feed (~1200 trades) + per-regime-cell
conditioning, where the exit signal (if any) has the power to show.

**The compounding value.** The substrate is proven to run *real* discovery (powered,
OOS-computed, leakage-clean) — the thing the ~376-row journal structurally could not
do. A null here is a real datum about ict_scalp entries, not a pipeline artifact.

### Study 8 — M36 Track D: broadened substrate + full exit-outcome sweep + per-regime + the M16 backbone (2026-07-27)

The M36 Track D session. Four threads, three landed + one in flight:

**(1) Substrate broadened (tooling, merged #7752).** Two additions to the M30
discovery toolchain: a **`backtest_system` portfolio adapter** for
`build_backtest_panel.py` (drives the WHOLE roster through the real
`aggregate_intents` on one shared account — each simulated closed trade carries
its winning strategy as the per-row `strategy` + that strategy's decision-time
`meta`, so discovery spans every roster strategy, not just ict_scalp; the
`_ClosedTrade → meta/confidence/entry_idx/entry_sl` thread the harness-map flagged
now lets the adapter extract features + native MFE/MAE excursions), and a
**per-regime-cell conditional-discovery driver** (`analyze_panel_by_cell.py`, C2b)
— partitions a panel by a decision-time `cat_regime`/`cat_vol_regime` cell and runs
the SAME C2 `analyze` per cell, to test whether an overall coin-flip entry (Study 7)
is directional *within* a regime. Every C2 guard inherited; the cell count is
stamped as the implicit multiple-comparison denominator.

**(2) The full exit-outcome sweep is now runnable.** Study 7 ran only
`win`/`giveback_r`/`r`. The C2 analyzer already supports the full excursion set as
continuous outcomes (Spearman univariate + ridge-OLS OOS), so this session runs the
**complete exit-timing outcome sweep** — `giveback_r` / `capture_ratio` / `mae_r` /
`time_to_mfe_frac` — plus per-regime `win`+`giveback_r`, on a powered panel.

**(3) Infra finding — the relay time-wall (a real, logged datum).** Every *bounded*
`trainer-vm-diag` relay build **timed out** (`timeout` exit 124): 500k, 300k, 250k,
and even **150k** (which Study 7's prior-session relay had completed). The trainer
is a 1-OCPU/6-GB box and was **CPU-contended** (concurrent Track-C relays + the
per-bar `order_package` cost puts ict_scalp well under ~350 bars/s), so the 10-min
GitHub-Actions relay cap cannot hold a build of useful size. **Fix (no GPU needed —
this is CPU-bound backtest replay, not an ML train job): run the build DETACHED**
(`nohup … &`, past the relay cap) and read the finished panel with a follow-up
relay. The full **647,586-bar** build was launched detached (relay #7761).

**(4) Numeric exit-timing verdict @ full-647k — LANDED (detached build #7761 →
read-back #7763).** The full 647,586-bar feed built to **1525 ict_scalp trades**
(5.4× Study 7's 282, ~6 yr), 100% excursion coverage, win-rate 51.02%, expectancy
+0.166R. C2 (purged WF-CV 5 folds, BH-FDR α=0.1, m=9):

| Outcome | FDR survivor(s) | OOS metric | Per-fold | Read |
|---|---|---|---|---|
| `win` | **`cat_regime`** | AUC **0.538** | [0.547, 0.595, 0.522, 0.513, 0.515] — **all 5 > 0.5** | a **thin but STABLE, above-chance entry edge at scale** — unlike Study 7's noisy 0.443 at N=282. Top imp `feat_fvg_size_atr` (0.046), `feat_adx_14` (0.026). |
| `giveback_r` | none | R² **−0.032** | mixed/neg | **NULL** — no exit-timing edge. |
| `capture_ratio` | none | R² **−1.47** | strongly neg | **NULL**. |
| `mae_r` | **`cat_regime`, `feat_adx_14`** | R² **−0.008** | ~0 | univariate regime/adx association, but ~coin-flip multivariate OOS. |
| `time_to_mfe_frac` | **`feat_sweep_depth_atr`** | R² **+0.005** | 3/5 slightly + | thinnest of positives — sweep depth relates to time-to-MFE. |

**Per-regime `win` (the Phase-1b question):** the entry edge is **concentrated in
CHOP** — `chop` OOS AUC **0.551** vs `trending` 0.513 / `transitional` 0.512
(giveback_r nulls in every cell). So the coin-flip is a *mixture*: ict_scalp's
entry is mildly more predictable in chop.

**Verdict — a MEANINGFUL, POWERED result, and it flips the story from Study 7.**
At N=1525 the entry `win` carries a **real (if thin) regime-conditioned OOS edge**
(`cat_regime` the FDR survivor; AUC 0.538, all folds > 0.5; strongest in chop) —
the "entries are pure coin-flip" prior is **too strong at scale**; there IS a thin
regime-conditioned entry signal. BUT the **exit-timing outcomes carry NO learnable
multivariate signal** — `giveback_r`/`capture_ratio` are firmly null, and
`mae_r`/`time_to_mfe_frac` have only thin univariate FDR survivors with ~0
multivariate OOS. So the "edge lives in EXIT" half of the prior is **NOT supported
here**: from decision-time features, the exits are unpredictable; the modest edge
that exists is a **regime-conditioned ENTRY** one. Leads worth the next look:
`cat_regime` (win + mae_r), `feat_fvg_size_atr` (win), `feat_adx_14` (win/mae_r),
`feat_sweep_depth_atr` (time_to_mfe) — a regime/FVG/ADX-conditioned entry-and-hold,
not an exit-timing overlay. This is the first FDR-surviving OOS lead the platform
has produced on real, powered, leakage-clean backtest volume.

**Infra note:** the bounded relay could not build even 150k (10-min cap); the
verdict came from a **detached `nohup` build** (#7761, ~30 min on the 1-OCPU
trainer) read back by a follow-up relay. The stable replacement — a self-contained
GH-runner `research-panel-build` workflow (fetch feed + build + C2 + per-regime,
6-h limit, $0 GPU) — shipped in PR #7764.

**(5) The M16 integration backbone (Phase 2, DRAFT PR #7756).** M30's outputs are
wired into the **M16 conviction master model** (`conviction-meta-v1`) as **training
data** — the `conviction_meta` family gains a `source_mode ∈ {live, backtest, union}`
+ `backtest_panels` axis (`live` default = byte-for-byte unchanged; `backtest`/`union`
map M30 panel rows → `conviction_meta` payloads tagged `source="backtest"`, reusing
the live `build_conviction_inputs` for `c_strat` so there is no train/serve skew),
plus an augmented `conviction-meta-v1-bt` manifest at `candidate`. This escapes the
~99-live-label bottleneck that stalled the stacker (T0.3) — the exact
"backtest-augmented rows" the unified-confidence design § 4.5 anticipated.
**Observe-only, not a new order path**; the augmented model rides
candidate → shadow → advisory before any influence (Tier-3). Design of record:
[`m30-to-m16-integration-backbone-DESIGN.md`](m30-to-m16-integration-backbone-DESIGN.md).

### Task 2 — L3 paper-ledger volume audit: root cause found (2026-07-27)

Study 1 flagged the paper eval cohort at only ~235 rows — "implausibly low for
soak books that trade the full instrument roster." Audited via the `trainer-vm-diag`
relay (#7743) against the synced journal (897 closed total). **The eval-population
writer is NOT broken and the cohort filter is NOT dropping soak books — the paper
cohort is small because the soak books' *closed-trade yield* is genuinely low.**

The funnel (paper = `account_class='paper' OR is_demo=1`, closed, non-backtest):

| Stage | N |
|---|---|
| paper closed non-backtest | 477 |
| + `pnl IS NOT NULL` | 371 |
| + exclude `adopted_orphan` | 327 |
| + exclude `superseded` (final panel N) | **219** |

Per-account **closed** counts (with the account's **total** rows in parens):
`bybit_1` 385 (1380) · `alpaca_paper` 44 (266) · `ib_paper` 26 (151) ·
`alpaca_options_paper` 8 (31) · `bybit_portfolio` 7 (29) · `alpaca_portfolio` 7
(71) · `prop_velotrade_1` **0** (267) · `oanda_practice` **0** (8).

**Root cause.** The soak books DO trade at scale in *rows* (bybit_1 1380,
prop_velotrade_1 267, alpaca_paper 266, ib_paper 151 total) — but the vast majority
never reach `status='closed'`: the whole DB carries **2594 `rejected` + 380
`exchange_rejected`** rows (risk-/exchange-refused order attempts that never
opened), plus 131 `orphaned` and only 28 `open`. So the full-roster soak generates
mostly **rejections**, not closed positions. Only ~477 paper rows ever close with an
outcome; ~106 of those carry NULL pnl (non-Bybit local-compute lag) and ~150 are
orphan/superseded artifacts → **219** clean rows.

**Disposition — confirmed working-as-designed, NOT a bug.** L3 (#7700, `52d51df`)
admits **all** paper (every soak book appears in the breakdown); it populates
exactly as much as the books actually close. The ceiling is soak closed-yield, not a
filter. Two sub-observations (neither a defect): `prop_velotrade_1` /
`oanda_practice` close **zero** — prop is a manual bridge (no auto-close path) and
oanda_practice is shelved, both expected; the large `rejected` population is the risk
manager doing its job on a full-roster soak (and is itself the eval-audit near-miss
population for a future L1b hard-gate-ablation study, not closed-trade data).

**The compounding lesson.** Live + paper together top out at **~596 clean closed
rows** (377 real + 219 paper) even with all paper admitted — still far below what
purged-WF-CV × BH-FDR discovery needs. This is the same wall from the paper side,
and it is exactly why the backtest substrate (Study 6) is the right move: the eval
book cannot be grown to discovery scale from the journal alone.

### P5 feasibility (per-bar exit-timing panel) — BLOCKED on infrastructure (2026-07-27)

Scoping P5 (the per-bar panel that the operator's load-bearing prior —
"edge lives in exit/regime" — most wants, for MFE/MAE / realized-R-giveback exit
studies) against the live infrastructure (relay #7731) found it **cannot be built
as a Tier-1 offline join**, unlike C1:

1. **No per-bar source is persisted.** `trade_journal.db` has **no
   `market_features` / `market_raw` / candle table** (tables are
   trades/order_packages/signals/prop_*/insights/etc.), and `trades` carries
   **no MFE/MAE** — only entry/exit price, SL, TP1-3, entry+close timestamps,
   symbol, direction. (The scoping doc's assumption that `market_features`
   regime/vol/session/flow is queryable does not hold on the money DB.)
2. **The offline candle fetcher cannot reach historical windows.** Every
   connector's `get_ohlcv(symbol, timeframe, limit)` (`src/exchange/*_connector.py`)
   returns only the **most-recent `limit` candles** — there is no `since`/`start`
   parameter, and `src/runtime/market_data.fetch_candles` exposes none. So MFE/MAE
   for the 897 historical closed trades (weeks-to-months old) is **unreconstructable
   offline** with the current path.

**Consequence.** Per-bar exit-timing needs one of:
- **(a) a historical-range candle-fetch capability** — extend `get_ohlcv` /
  `fetch_candles` with a `since`/`[start,end]` window (modest for the
  Bybit/CCXT path — `fetch_ohlcv` already takes `since=`; more work for
  IBKR `endDateTime`+`durationStr` and Alpaca/OANDA `start`/`end`). This is a
  **Tier-2 read-path change** to `src/exchange/*` + `src/runtime/market_data.py`
  (touches runtime, makes historical exchange calls) — a bounded, well-scoped
  build, but not Tier-1 observe-only. **Recommended first step**, since it also
  unblocks any future historical backfill and covers the crypto book (the bulk of
  the real trades) cheaply.
- **(b) a forward per-bar excursion soak** — a live writer that records MFE/MAE
  per open trade going forward. This is the "live per-bar soak on the money box"
  the scoping doc already flags as **Tier-3, operator-gated**; it also only
  accrues new data (no backfill).

The **pure excursion math** (given a candle path + entry/SL/side/exit →
MFE_R / MAE_R / giveback_R / capture-ratio / bars-to-MFE) is a small, unit-testable
Tier-1 helper that any of the above feeds — but building it in isolation is
premature until (a) or (b) supplies the price path. **Recommendation to the
operator:** approve the **(a) Tier-2 range-fetch extension** (Bybit/CCXT first) as
the unblock; then P5 = range-fetch → excursion helper → exit panel is a clean
Tier-1 build on top. Filed so the next session doesn't blind-build a panel that
returns empty for ~every historical trade.

## Reproducing a study

Run the two bricks in sequence against the book you want (real `trade_journal.db`
resolved by the canonical resolver, or `--db <path>`):

> **VM-side `--db` is REQUIRED on the trainer relay (Study-3 repro note).** The
> trainer VM's clone has **two** `trade_journal.db` files: the populated synced
> journal at **`/home/ubuntu/ict-trading-bot/data/trade_journal.db`** (897 closed
> trades) and an **empty repo-root `/home/ubuntu/ict-trading-bot/trade_journal.db`**
> (0 rows) that the canonical resolver picks up when `DATA_DIR` is unset — which
> is what silently produced Study 3's first empty run (#7720, "0 closed trades",
> **not a real null**). Always pass `--db "$(populated journal)"` on the relay, or
> locate it by closed-trade count first (as #7725 does). **Recommendation for the
> relay harness:** export `DATA_DIR=/home/ubuntu/ict-trading-bot/data` (or set
> `TRADE_JOURNAL_DB`) so `src.utils.paths.trade_journal_db_path()` resolves the
> synced DB without the per-command `--db` — this is the durable fix and avoids
> the next session re-hitting the empty-DB trap.

```bash
# C1 — build the panel for one strategy's real closed trades
python scripts/research/build_research_panel.py \
    --strategy trend_donchian --cohort real \
    --out runtime_logs/research/trend_donchian_panel.jsonl

# C2 — discover, under purged WF-CV + BH-FDR
python scripts/research/analyze_research_panel.py \
    --panel runtime_logs/research/trend_donchian_panel.jsonl \
    --outcome win --n-buckets 4 --fdr-alpha 0.1 --cv-folds 5 \
    --out runtime_logs/research/trend_donchian_analysis.json
```

The C2 report stamps the M18 coin-flip prior, the leakage contract (it refuses
to model if the panel's feature/outcome split is violated), the BH-FDR survivor
set, and the OOS CV metric per fold. Read the `.md` sibling for the human
summary.

**Whole-roster coverage in one pass — the P2 sweep** (`sweep_research_panels.py`):

```bash
# C1 once + C2 per strategy (>= floor) / per asset-class pool (thin books),
# rolling each group into the platform-bar verdict. Writes sweep.md + sweep.json
# + one full C2 report per group under groups/.
python scripts/research/sweep_research_panels.py \
    --db /home/ubuntu/ict-trading-bot/data/trade_journal.db \
    --cohort real --power-floor 30 --out-dir runtime_logs/research/sweep
# add --features feat_confidence,feat_adx_14 to let block-sparse pools attempt
# the multivariate on a dense common-core.
```

## Next entries (queued)

- **2 · `vwap` per-strategy panel** — **DONE** (Study 2 above): weak/unstable on
  `win` (OOS AUC 0.593), null on `r`; not a confirmed finding. Surfaced the
  binding gap = **feature-capture breadth** (vwap instruments only 2 graded
  feats).
- **3 · common-core pooled panel** — **DONE** (Study 3 above): P1 `--features`
  selector works, but the strategy-agnostic dense columns are too sparse
  (~41/377 for model-score) → complete-cases collapse to 35 rows → `win` OOS not
  computable, `r` OOS R² −10.79. `feat_model_score_mean` **fails** its first OOS
  test → NULL. Yield: **decision-time feature DENSITY** is the binding
  constraint; next common-core should use the P4-widened dense cats and drop the
  sparse model-score pair.
- **3b · common-core v2 (post-P4 dense cats)** — re-run the pooled common-core
  with `feat_confidence` + `feat_adx_14` + `cat_killzone` / `cat_bias` /
  `cat_setup_type` (P4-landed, dense across strategies) instead of the sparse
  `feat_model_score_*`. Best driven by the P2 sweep once it exists.
- **4 · per-strategy sweep** — **DONE** (Study 4 above, via the merged P2 driver):
  one pass covered the roster; only `vwap` (318) + an `asset:crypto` pool (58)
  clear floor 30. `vwap/win` auto-flagged candidate_finding on the known unstable
  vwap-mechanical lead (2/5 folds < 0.5 — Study-2 caveat stands); crypto pool null
  (block-sparse, 0 complete-case); regime/session cats no FDR survivor. **Nothing
  new clears the bar.** The real book is too thin + block-sparse for cross-strategy
  discovery today.
- **4a · P2 driver refinement (open)** — annotate the auto `candidate_finding`
  verdict with a per-fold-stability flag ("k/N folds < 0.5") so the triage label
  carries its caveat inline; optionally a `--features` default of the dense graded
  common-core for pools. Small tooling follow-up.
- **3b · common-core v2 (post-P4 dense cats)** — re-run the pooled/pool common-core
  with `feat_confidence` + `feat_adx_14` (the dense graded cols) via the sweep's
  `--features`, and lean on the P4 `cat_killzone`/`cat_bias`/`cat_setup_type` in
  the univariate edge tables. Runnable now via the P2 sweep `--features`.
- **P5 · per-bar exit-timing panel — BLOCKED** (feasibility section above): needs a
  **(a) Tier-2 historical-range candle-fetch extension** (recommended, Bybit/CCXT
  first) or **(b) Tier-3 forward per-bar soak**. Operator decision required before
  build. The pure excursion helper is a small Tier-1 piece that sits on top of (a).
- **C3 bridge (P3)** — any feature that clears BOTH bar conditions routes into the
  standing backtest→walk-forward gate. **No survivor has cleared the bar across
  Studies 1–4** (leads / weak-unstable / null), so there is **nothing to route
  today** — the wire (P3) is the increment that closes the loop, worth building
  ahead of a confirmed finding but lower-value than unblocking density (P4/P5) +
  volume (L3).

### Backtest-first discovery (the pivot — operator re-scope 2026-07-27)

- **C1-for-backtests bridge** — **DONE** (Study 6): `scripts/research/build_backtest_panel.py`
  (ict_scalp adapter) → C2 unchanged. Feature-rich (9 cols vs 2), leakage-clean,
  excursions free.
- **First large-N discovery run** — **DONE** (Study 7): ict_scalp, 282 trades on
  ~1.4yr real 5m BTCUSDT → **powered NULL** (`win` OOS AUC 0.44 confirms the
  coin-flip-entry prior; `giveback_r`/`r` null at this N). The substrate is proven
  to run real, OOS-computed discovery. **Next turns:**
  1. **Scale up** — the full **647k-bar** feed (~1200 trades) for `win` + the
     **exit-timing** outcomes (`giveback_r`/`capture_ratio`/`mae_r`) — the exit
     prior is untested at scale, and the VM feed lives at
     `data/backtest_BTCUSDT_5m.csv` (use `python3`/`.venv/bin/python`, NOT `python`).
  2. **Per-regime-cell** conditioning — the coin-flip is *overall*; test whether
     entries discriminate *within* a `cat_regime`/`cat_vol_regime` cell (the
     scoping doc's Study #2).
  3. **More adapters** — `backtest_system.py` (portfolio-realistic, needs the
     `_ClosedTrade`→meta thread) + `run_backtest_vwap.py` (vwap).
- **More adapters** — `backtest_system.py` (portfolio-realistic, netted; needs the
  `_ClosedTrade`→meta/confidence/entry_idx thread the harness map flagged) and
  `run_backtest_vwap.py` (live `build_vwap_signal`, vwap-only). Both call the live
  builder → feature-rich. The three inline-entry harnesses (trend/fade/squeeze) +
  `ICTBacktester` are feature-poor (confidence-only) → excursion-only studies at
  best; not wired.
- **L3 paper-ledger volume** — **DONE** (Task 2 above): the L3 writer works as
  designed (all soak books admitted); the paper cohort is bounded by genuine
  closed-yield (~477 paper closed, 219 clean), not a filter bug. Live+paper top out
  at ~596 clean rows — reinforces the backtest-substrate pivot.

> **Platform loose-ends status** (SESSION-PROMPT numbering; from the 2026-07-27
> readiness audit): **P1** C2 `--features` selector — **DONE** (`f4cbc3b`);
> **P4** widen C1 capture (killzone/bias/setup_type from `order_packages.meta`) —
> **DONE** (#7723); **P2** per-strategy sweep driver — **DONE** (#7730, exercised
> in Study 4); **L3** paper-book eval population — **DONE** (merged prior session).
> Open: **P5** per-bar panel (large) — **BLOCKED** on a Tier-2 range-fetch
> extension or Tier-3 forward soak (feasibility section above); **P3** C3 backtest
> bridge (medium — nothing to route yet); **P6** SHAP (small, low-priority).

## Reading the ledger

Entry 0 is a platform-validation proof on synthetic data (the guards recover an
injected edge and reject noise); it is deliberately first so every real row
below can be read against a pipeline known to bite. Every subsequent row is a
real book run through the same honest gate. As with M28, the expectation is that
**most rows are nulls** — the small-book coin-flip prior guarantees it — and a
null recorded faithfully is the compounding asset: it tells the next session
which (cohort × feature) cells are exhausted so compute isn't burned re-deriving
a foregone result.
