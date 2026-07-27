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
- **4+ · per-strategy sweep** — the same C1→C2 pass per remaining live strategy
  above the power floor, pooling thin books by asset class. Best done via the
  sweep driver (P2 loose-end) rather than hand-running each.
- **C3 bridge** — any feature that clears BOTH bar conditions routes into the
  standing backtest→walk-forward gate. No survivor has cleared the bar yet
  (Study 1 leads-only; Study 2 weak/null), so there is nothing to route today —
  but the wire (P3) is still the increment that closes the discovery loop.

> **Platform loose-ends feeding these studies** (from the 2026-07-27 readiness
> audit; tracked in the scoping doc): **P1** C2 `--features` selector (small —
> unblocks Study 3), **P2** per-strategy sweep driver (medium), **P3** C3
> backtest bridge (medium), **P4** widen C1 decision-time feature capture incl.
> killzone/session (medium — the Study-2 instrumentation finding), **P5** per-bar
> panel (large), **P6** SHAP (small). Plus **L3** paper-book eval population
> (operator-approved) to grow the eval count past the ~376 real-money wall.

## Reading the ledger

Entry 0 is a platform-validation proof on synthetic data (the guards recover an
injected edge and reject noise); it is deliberately first so every real row
below can be read against a pipeline known to bite. Every subsequent row is a
real book run through the same honest gate. As with M28, the expectation is that
**most rows are nulls** — the small-book coin-flip prior guarantees it — and a
null recorded faithfully is the compounding asset: it tells the next session
which (cohort × feature) cells are exhausted so compute isn't burned re-deriving
a foregone result.
