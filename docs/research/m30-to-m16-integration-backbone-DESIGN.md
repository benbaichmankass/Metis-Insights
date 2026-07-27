# M30 → M16 Integration Backbone — design of record

**Status:** design of record (2026-07-27). **Milestone:** M36 Track D. **Anchor:**
`MB-20260727-M36-CONSOLIDATION-INTEGRATION`. **Tier:** Tier-1 research +
observe-only training-data integration. A discovered edge influences a live
decision only through the existing candidate → shadow → advisory ladder + the
net-of-cost walk-forward gate + Tier-3 operator approval. **This is NOT a new
order path.**

> Companion docs: the unified-confidence design
> [`docs/unified-confidence-risk-DESIGN.md`](unified-confidence-risk-DESIGN.md)
> (the M16 conviction framework); the M30 platform scoping
> [`docs/research/technical-quant-research-platform-scoping-2026-07-27.md`];
> the research ledger [`docs/research/technical-quant-research-ledger.md`].

## 1. The problem this closes

The **M16 conviction master model** — the learned v2 conviction meta-model
`conviction-meta-v1` (`ml/configs/conviction-meta-v1.yaml`, dataset family
`ml/datasets/families/conviction_meta.py`) — is the stacker that is meant to
replace the formulaic v1 conviction blend (`src/runtime/conviction.py`:
`news_mult × (w1·c_strat + w2·c_setup + w3·c_wr + w4·c_reg)`). It ingests the
calibrated conviction-lens inputs + decision context and emits a single
calibrated **P(win)**.

It is **data-bottlenecked** and has stalled (ROADMAP T0.3). Its dataset family
builds **one row per closed, filled, non-backtest order package** joined to its
trade outcome — i.e. the live real+paper closed-trade corpus, which is small
(the same ~few-hundred-row wall that nulled every M30 live-journal study). The
unified-confidence design anticipated this exactly (§ 4.5): *"the v2 meta-model
is data-bottlenecked and will likely need **backtest-augmented rows**."*

M30's pivot — **backtest-first discovery** (`build_backtest_panel.py`, merged
#7744; Study 7 proved it runs real OOS-computed discovery at scale) — produces
precisely those rows: large-N, feature-rich, decision-time-honest rows with
realized outcomes, on the native candle path. **M30 is the backbone; the
conviction master model is its first consumer.**

## 2. The core insight — the schema is already almost 1:1

The M30 backtest panel (C1-for-backtests schema) and the `conviction_meta`
family schema describe the **same object**: a decision (decision-time context +
signal confidence) paired with its realized outcome. The mapping is nearly
identity:

| `conviction_meta` column | M30 panel source | Note |
|---|---|---|
| `strategy_name` | `strategy` | per-row winning strategy (portfolio adapter) or the single strategy |
| `symbol` | `symbol` | |
| `direction` | `SimTrade.side` | **add `direction` to the panel rec** (§ 5.1) — currently unemitted |
| `regime` | `cat_regime` | decision-time trend regime |
| `adx_14` | `feat_adx_14` | |
| `vol_regime` | `cat_vol_regime` | present when `--vol-spec` supplied; else `""` |
| `confidence` | `feat_confidence` | the raw signal confidence |
| `c_strat` | `build_conviction_inputs(strategy, confidence, None)` | **reuse the live adapter** — no train/serve skew |
| `c_setup` / `c_wr` / `c_reg` | (absent offline) | no ML heads replayed offline → NaN, exactly as the live path when a head didn't score |
| `won` | `win` | `pnl > 0`; the classification target |
| `pnl` | `pnl` (== realized R proxy) | backtest has no dollar PnL; **R units**, not $ — see § 4.2 |
| `pnl_percent` / `r_multiple` | `r` (realized R) | `r_multiple ← r`; `pnl_percent ← r × risk_pct` |
| `created_at` | `closed_at` | the exit timestamp (the WF-CV time axis) |
| `source` | — | **`"backtest"`** (vs the family's `"live"`) |
| TSFM `emb_0..31` | — | `0.0` (no `embedding_path`), byte-identical to the v1 live default |

The feature space the meta-model would train on (`c_strat`, `c_setup`, `c_wr`,
`c_reg`, `adx_14`, `regime`, `vol_regime`, `symbol`, `direction`,
`strategy_name`) is **fully producible from the panel** — `c_strat` via the same
`build_conviction_inputs` the live stamp calls (no skew), the context columns
directly, and the head-slot lenses honestly absent (NaN) offline just as they
are live whenever a head didn't score that decision.

## 3. What "feed M30 into M16" means concretely — two channels

**Channel A — rows (the training-data integration).** M30 backtest panels are
converted to `conviction_meta`-schema rows tagged `source: "backtest"` and made
available to the dataset build, so the meta-model trains on thousands of
backtest-substrate decisions in addition to (or instead of) the ~few-hundred
live rows. This is the primary deliverable and the one the design § 4.5 named.

**Channel B — study results (the "what's working vs not" feed).** M30's *findings*
— which features/regimes carry edge under FDR × OOS, and which are coin-flips —
are not thrown away once a row lands; they steer the master model's inputs and
the operator's read:

1. **Feature/context inclusion.** A context column that C2 shows is
   discriminative within a regime (the Track-D Phase-1b per-cell question) is a
   column worth conditioning on; a proven coin-flip is a candidate to drop or
   down-weight. The `conviction_meta` manifest's `feature_columns` is the knob.
2. **Regime-aware sample weighting / priors.** If the exit-timing study (Phase-1a)
   locates the edge in *exits within a vol regime* rather than entries, that says
   the master model's value is as a **sizing/exit conditioner**, not an entry
   filter — informing where its P(win) is trusted.
3. **Operator legibility.** The study ledger + the per-cell tables are the
   "what's working vs not" picture the operator asked to see (ROADMAP M36 Track D).
4. **Macro-sleeve feed (where relevant).** The same panels/features condition the
   M28⊕M29 macro sleeves' `c_macro` path — out of scope for this doc (Track C owns
   the macro merge); the backbone simply makes the substrate available to both.

## 4. Design decisions

### 4.1 One family, a `source` axis — not a fork
Keep **one** `conviction_meta` family + **one** schema. Backtest rows differ only
by `source: "backtest"` and by having only `c_strat` populated (head slots NaN).
A manifest chooses the training population via build params: **live-only**
(today's behaviour, unchanged), **backtest-only**, or **union**. This preserves
the no-train/serve-skew property (same `build_conviction_inputs`, same context
decode) and lets us A/B *augmented vs live-only* rather than maintain two
schemas that drift.

### 4.2 R-units vs dollars — the target is comparable, the magnitude is not
A backtest has no dollar PnL, so `pnl`/`pnl_percent` on a backtest row are **R
proxies**, not currency. The **classification target `won` (`pnl > 0`) is
directly comparable** across live and backtest rows — a win is a win. Only the
*magnitude* columns differ in unit; the v1 manifest targets `won`, so the
augmentation is sound for it. A regression variant on `r_multiple` is likewise
R-consistent (live `r_multiple` is already `pnl_percent / risk_pct`). The `source`
column lets any consumer segregate or reweight by cohort, and lets an evaluator
report live-vs-backtest calibration separately.

### 4.3 The head slots are honestly absent — and that is fine
Offline the ML shadow heads are not replayed, so `c_setup`/`c_wr`/`c_reg` are
NaN on backtest rows. This is **not** a defect: (a) `c_reg` is already
expected-optional in the v1 manifest (the regime-conviction lens isn't wired,
MB-20260618-XA-D2B); (b) live rows also carry NaN head slots whenever a head
didn't score that decision; (c) LightGBM handles NaN natively. The backtest rows
strengthen the model precisely on the **always-present** axis — `c_strat` +
context — which is where a data-starved stacker most needs support.

### 4.4 Zero live influence until the ladder clears
The augmentation changes *what the candidate meta-model is trained on*, nothing
else. `conviction-meta-v1` registers at `candidate` and **logs alongside the v1
blend with zero influence** until the operator promotes it candidate → shadow →
advisory. Any live conviction influence remains Tier-3 + backtest-gated. This
integration is a **training-data change to an observe-only model**, full stop.

### 4.5 Distribution-shift honesty
Backtest rows are a *different distribution* than live (perfect fills, no
slippage, the harness's exit logic, no broker rejects). The guard is the
existing evaluation ladder: the meta-model must **beat the v1 blend on the LIVE
holdout** (the evaluator's `time_aware_holdout` on live `created_at`) before
promotion — augmentation that helps the backtest split but hurts the live split
fails the gate. The `source` column makes a live-only holdout / a cohort-stratified
eval straightforward. We explicitly do **not** claim backtest augmentation is
free lift; we claim it is the only way to give the stacker enough rows to *have a
testable fit at all*, then let the live gate arbitrate.

## 5. Wiring (drafted for operator review — touches `ml/`)

### 5.1 Panel emits `direction`
`build_backtest_panel.py` adds `direction` (canonicalized `long`/`short`) to each
panel row from `SimTrade.side`. Additive; the C2 analyzer ignores unknown
non-`feat_`/`cat_`/`gate_` columns, so every existing consumer is unaffected.
(Tier-1, ships with the converter.)

### 5.2 `conviction_meta` gains a backtest row-source
`ConvictionMetaBuilder.iter_rows` gains an optional `backtest_panels` param (a
path/glob of M30 panel JSONLs). When supplied it yields `source: "backtest"`
rows built from the panels via a pure `_row_from_panel(panel_row)` mapper (the §2
table), reusing `build_conviction_inputs` for `c_strat`. Combined with the
existing DB scan via a `source_mode ∈ {live, backtest, union}` param (default
`live` → **today's behaviour byte-for-byte**). Leakage contract unchanged
(outcomes never enter `feature_columns`; the trainer's `_OUTCOME_FORBIDDEN` gate
still enforces it).

### 5.3 A manifest variant for the augmented model
`ml/configs/conviction-meta-v1-bt.yaml` — identical trainer/feature/target config
to v1, built with `source_mode=union` + a `backtest_panels=` pointer, registered
at `candidate` under a distinct `model_id` (`conviction-meta-v1-bt`) so it soaks
**alongside** the live-only v1 for an honest A/B. Neither influences a live
decision.

### 5.4 Build recipe
```
# 1. Build M30 backtest panels (ict_scalp + backtest_system) on the trainer feed
python scripts/research/build_backtest_panel.py --harness ict_scalp \
    --data data/backtest_BTCUSDT_5m.csv --stamp-regime --vol-spec-json <spec> \
    --out datasets-out/_panels/ict_scalp_btc_5m.jsonl
# 2. Build the augmented conviction_meta dataset (union of live + backtest rows)
python -m ml build-dataset conviction_meta --output-dir ./datasets-out \
    --version v002 --overwrite -- \
    source_mode=union backtest_panels=datasets-out/_panels/'*.jsonl'
# 3. Train the candidate augmented meta-model (registers at candidate, zero influence)
python -m ml train ml/configs/conviction-meta-v1-bt.yaml --datasets-root ./datasets-out
```

## 6. Rollout phases

- **P0 (this doc + the Tier-1 tooling, done):** the M30 discovery substrate
  broadened (ict_scalp + backtest_system + per-cell C2). Panels are the row
  source.
- **P1 (drafted here, DRAFT PR):** the `conviction_meta` backtest row-source +
  `direction` emit + the `-bt` manifest. Operator review (touches `ml/`).
- **P2 (trainer, gated):** build the augmented dataset, train
  `conviction-meta-v1-bt` at `candidate`, and evaluate it on the **live holdout**
  vs the live-only v1. Report calibration by `source`.
- **P3 (Tier-3, operator):** if the augmented model beats v1 on the live gate,
  promote candidate → shadow (observe-only logging), then the standard
  shadow → advisory ladder. Only advisory ever influences a decision, and that
  step is the operator's.

## 7. What this is not

- Not a new order path, not a live-influence change, not a config/runtime touch.
- Not a claim that backtest rows are equivalent to live rows — the `source`
  column and the live-holdout gate keep them honest.
- Not a fork of the family or the schema — one family, a `source` axis.
