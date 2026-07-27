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

## Reproducing a study

Run the two bricks in sequence against the book you want (real `trade_journal.db`
resolved by the canonical resolver, or `--db <path>`):

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

- **2 · `vwap` per-strategy panel** — the immediate follow-on to Study 1: the
  dominant book (318 real rows) run C1 `--strategy vwap` → C2 so its own dense
  features (`feat_vwap_deviation_std` et al.) yield complete-case rows and the
  multivariate + permutation-importance + VIF pass can finally run, giving
  `vwap_deviation_std` the OOS reading it needs to graduate from lead → finding
  (or null). This is the first study that can actually clear criterion (b).
- **3 · common-core pooled panel** — a small additive C2 `--features`
  selector restricting the pooled multivariate fit to the strategy-agnostic
  dense columns (`feat_confidence`, `feat_model_score_*`, `cat_regime`,
  `feat_adx_14`) so complete-case rows exist across strategies — the
  cross-strategy counterpart to Study 2, and where `feat_model_score_mean`
  gets its OOS test.
- **4+ · per-strategy sweep** — the same C1→C2 pass per remaining live
  strategy above the power floor; pool by asset class where a single book is
  too thin (most are — ict_scalp_5m 13, trend_donchian 8, the pullback sleeve
  ≤ 8 each). Each an appended row (edge or null).
- **C3 bridge** — any feature that clears BOTH bar conditions routes into the
  standing backtest→walk-forward gate (the entry-tuning gate), never a direct
  config change. That wiring is the next platform increment (C3), not a ledger
  study.

## Reading the ledger

Entry 0 is a platform-validation proof on synthetic data (the guards recover an
injected edge and reject noise); it is deliberately first so every real row
below can be read against a pipeline known to bite. Every subsequent row is a
real book run through the same honest gate. As with M28, the expectation is that
**most rows are nulls** — the small-book coin-flip prior guarantees it — and a
null recorded faithfully is the compounding asset: it tells the next session
which (cohort × feature) cells are exhausted so compute isn't burned re-deriving
a foregone result.
