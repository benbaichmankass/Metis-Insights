# M30 × M20 — the per-bar in-trade EXIT head (dense-substrate fusion) — DESIGN

> **Tier-1 research build; Tier-3 to ship.** Observe-only offline tooling + a
> $0 GH-runner discovery workflow. No live order path, config, or money-DB change
> until a net-of-cost backtest-gated, operator-approved graduation (the M20
> exit-management lifecycle). **For operator review before any live surface.**
>
> Adopted 2026-07-28 (Track-1 continuation). Fuses the M30 backtest-substrate
> research platform ([`technical-quant-research-ledger.md`](technical-quant-research-ledger.md))
> with the M20 exit-management framing
> ([`exit-management-ml-experiment-DESIGN.md`](exit-management-ml-experiment-DESIGN.md)).

## 1. Why this exists — the structural escape from the feature wall

Every M30 discovery pass to date has nulled or gone `not_computed` for the **same
structural reason**: the **decision-time** (entry-side) feature vector is
**block-sparse** — each strategy instruments only its own columns — so the
listwise-complete-case set the out-of-sample multivariate pass needs collapses
toward zero:

- Live journal, pooled: **Studies 1 / 3 / 4** — 0 complete-case rows.
- Backtest substrate, single-strategy ict_scalp: **Studies 7 / 8 / 9** — entry
  features ~coin-flip OOS (a thin, unstable `cat_regime` lead at best).
- Backtest substrate, **whole roster** (`backtest_system`): **Study 10** — 1574
  trades but **0 complete-vector rows across the 6 graded feats on every outcome**
  → multivariate/OOS not computed. Study 8's `cat_regime` edge did **not**
  replicate at roster scale.

The escape is not another discovery pass on a block-sparse cut — it is a feature
set that is **dense by construction**. The operator's load-bearing prior — *edge
lives in exit/regime, via in-trade PATH features (running MFE/MAE-R, bars-in-trade,
in-trade vol, dMAE/dt, distance-to-stop), not decision-time features* — points
exactly there. Given an already-open position and its trajectory so far, those
path features are populated on **every bar of every trade**, so a per-bar panel is
dense and the OOS multivariate pass **finally runs at scale**.

This is corroborated from four directions in the deep survey: (a) the entry-wall
findings (M18 + Studies 7–10); (b) the external exit/microstructure literature
(magnitude/vol is learnable, direction is not); (c) the M20 exit-management §8
result — Framing A `should_hold` on `market_features` **nulled** (AUC 0.52 @
n=138k) — which says the *feature set* matters, since (d) the **shipped M20 E3
exit head reaches AUC 0.70 / +82% net-R at half-DD** using in-trade PATH features
(`exit-refinement-coverage.json` → `exit_head_ml: shipped`). The bet here is the
E3 PATH-feature framing, generalized onto the M30 large-N substrate under full
de-Prado rigor.

**This is NOT a re-run of the M20 §8 null.** §8 used `market_features` + LGBM on
one config; this uses the **in-trade path state** the E3 winner used, on a
larger substrate, with uniqueness weighting + a net-of-fee policy gate the §8 test
did not apply. A null here would still be honest and decisive; a pass routes to
the M20 net-of-cost gate.

## 2. The panel — per-bar in-trade rows (dense by construction)

`scripts/research/build_intrabar_exit_panel.py` reuses the M30 backtest adapters
(`build_backtest_panel.ADAPTERS` — ict_scalp / backtest_system, both replay the
**live** signal builder, so trades are live-faithful and large-N). For each
simulated trade it walks the bars the position was actually open
(`[entry_index+1 .. min(exit_index, cap)]`) and emits **one row per in-trade
decision bar**.

**Features (`feat_*`, strictly PAST — from `[entry_index+1 .. t]` only)** —
`src/research/intrabar_features.py`:

| feature | meaning |
|---|---|
| `running_mfe_r` / `running_mae_r` | max favorable / adverse excursion so far, in R |
| `upnl_r` | current mark-to-market R |
| `mfe_giveback_r` | peak profit surrendered so far (`mfe_r − max(upnl_r,0)`) |
| `bars_in_trade` / `bars_in_trade_frac` | how far into the trade (÷ expected hold = the time-stop) |
| `dist_to_stop_atr` | cushion left to the stop, in entry-ATR units |
| `in_trade_vol_ratio` | in-trade realized range vol ÷ entry ATR |
| `dmae_dt` | rate the adverse excursion is growing (R/bar) |
| `taker_imbalance` / `taker_imbalance_intrade` | signed taker buy/sell imbalance (free OFI proxy; last bar + in-trade mean) |

Every feature is populated on every row (dMAE defaults to 0 before its window;
ATR falls back to the trade's own risk). The two taker columns are the one honest
exception — `None` → **dropped from the panel** on a feed without the taker split
(Bybit); dense on the Binance-vision feed (the fetcher now preserves
`taker_buy_base`, field 9). `cat_regime`/`cat_vol_regime`/`cat_setup_type` ride as
**context** for conditioning, not regressors.

**Label — triple-barrier + time-stop (strictly FUTURE, `[t+1 .. t+time_stop_bars]`)** —
`src/research/triple_barrier.py`. From decision bar `t`, the outcome of
**continuing to hold**: first-touch of the **upper** barrier (`entry + tp_r·R`),
the **lower** barrier (the stop, −1R), or the **vertical** time-stop
(mark-to-market at `t+time_stop_bars`). The **time-stop is the highest-value lever
on 5m** per the literature, so it is the anchoring barrier. `forward_r` is the
realized R of holding; the **meta-label** (`hold_meta_label`) is
`label_hold = 1[forward_r − upnl_r − cost_r > 0]` (take = keep holding, net of a
fee buffer `cost_r`) with sizing magnitude `size = |advantage_r|`.

**Leakage invariant:** the feature window `[..t]` and the label window `[t+1..]`
are disjoint by construction. `label_t0`/`label_t1` (absolute feed-bar indices)
carry each label's span for uniqueness/purge; `trade_id` groups a trade's bars so
no trade splits across a fold; the CV orders by `decision_time`.

## 3. The head + gate — `analyze_exit_head.py`

The overlapping per-bar labels demand the de-Prado corrections
(`src/research/meta_label.py`, *Advances in Financial ML* ch. 4/11/14):

1. **Grouped, purged, embargoed walk-forward CV** — a whole trade stays on one
   side of every split; train rows whose label window overlaps/adjoins the test
   period are purged; rows ordered by `decision_time`.
2. **Uniqueness-weighted fit** — each train row's `sample_weight` = its average
   uniqueness (`1/concurrency` over its label span) so redundant overlapping
   labels don't over-count. A weighted logistic gives the take/skip head; a
   weighted ridge on `advantage_r` gives the sizing magnitude. `sequential_bootstrap`
   is provided for a decorrelated bagged robustness estimate on a bounded pool.
3. **OOS discrimination** — out-of-sample AUC (hold vs exit) + per-fold stability;
   univariate BH-FDR over the features.
4. **Net-of-fee EXIT POLICY sim** — the head's decisions are simulated **per
   trade** (first bar `P(hold) < threshold` ⇒ the trade realizes its mark-to-market
   R there, net of an exit fee; else it keeps its fixed-exit R) vs the baseline
   fixed SL/TP exit → the headline **net-R improvement**, its realized-R Sharpe
   graded by the **probabilistic / deflated Sharpe** and, across a config grid,
   **PBO via CSCV**.

**The pre-registered bar (mirrors M20 §4):** the head must (a) discriminate OOS —
**AUC > 0.55, stable across folds** — AND (b) deliver a **positive net-of-fee R
improvement vs the fixed exit**. Both are required; a high AUC with negative net-R
is a fail (the "AUC ≠ profit" trap — verified caught in validation). Miss either
⇒ the exit wall is as hard as the entry wall; record the null and stop.

## 4. What runs where — the $0 discovery workflow

`.github/workflows/research-exit-head-build.yml` (sibling of
`research-panel-build.yml`): a GH-hosted runner fetches the feed from Binance's
public archive (keyless, taker-imbalance preserved), builds the dense panel, runs
the analyzer, uploads the artifacts, and posts the verdict. No VM, no trainer
contention, no relay cap, no GPU. Dispatch via `workflow_dispatch` or the
`research-exit-head-request` issue label.

## 5. Phasing + tiers (the M20 lifecycle — unchanged)

- **P0 — offline feasibility (Tier-1, THIS build).** Dense panel + meta-label head
  + OOS AUC + net-of-fee policy sim + DSR/PBO → the pre-registered verdict. No live
  surface. The powered run (full 5m feed, `win`+exit outcomes, the time-stop/tp_r
  grid) lands the honest verdict in the ledger as the next Study.
- **P1 — observe-only shadow soak (Tier-2).** If P0 clears: the head logs would-be
  exits per open position (the exit-ladder-soak pattern), kill-switch + per-tick
  wall-clock budget.
- **P2 — backtest-gated apply proposal (Tier-3).** Net-of-fee + survival vs the
  fixed exit on the account-compat matrix; operator-approved.
- **P3 — advisory exit influence (Tier-3).** Graduated like the regime vol-gate.

## 6. Honesty / risks

- **Leakage is the live risk** in exit labeling (the future defines the label);
  mitigated by the disjoint feature/label windows (unit-tested), the label-span
  purge, and the grouped-by-trade CV. The feature module reads only `[..t]`.
- **Threshold calibration** — the policy's `exit_threshold` is a real knob: a high
  AUC can still lose net-R at the wrong threshold (validation showed exactly this).
  The powered run sweeps it; the verdict is net-of-fee, not AUC alone.
- **The M20 §8 null is the prior to beat** — a repeat null here would confirm
  exit-timing is at the wall for the PATH-feature framing too, and point the same
  rigor at **sizing/selection** (the allocator + conviction-sizing soaks). Recorded
  plainly either way.
- **Substrate caveats** — BTCUSDT 5m to start; one barrier family per run; the
  backtest fill model (no intraday order-book). Net-of-fee, not gross.

## 7. Validation done (this build)

- 17 focused unit tests (`tests/test_m30_exit_head.py`) — feature math + leakage,
  triple-barrier geometry (tp/sl/time/degenerate), meta-label, and the de-Prado
  primitives (uniqueness on known concurrency, sequential bootstrap, norm CDF/PPF
  round-trip, PSR/DSR monotonicity, PBO overfit-vs-robust). Ruff clean.
- End-to-end on the committed sample: builder emits 64 dense per-bar rows from 4
  ict_scalp trades (taker cols correctly dropped on the taker-less sample); the
  analyzer returns an honest `underpowered` at N=64.
- Computed-path validation on a powered synthetic panel (1222 rows, injected
  giveback/dMAE signal): OOS AUC 0.89 recovered across 5/5 folds, and the
  net-of-fee policy sim correctly flagged a **high-AUC-but-negative-net-R** case as
  `clears_bar=False` — the dual-criterion gate works.
