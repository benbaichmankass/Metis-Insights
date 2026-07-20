# ict_scalp_5m — Modernization Research Plan (2026-07-20)

**Status:** DRAFT research plan (Tier-1). Every *live* change it leads to
(param/exit/gate edits, shadow→live re-promotion) is **Tier-3, operator-gated**.
Owner review item: `PB-20260630-ICTSCALP-DEGRADE`.

## Why this plan

`ict_scalp_5m` was one of the **first strategies we built** (v1 issue #1145,
live 2026-05-14 #1156). It is currently `execution: shadow` — demoted on an
operator-approved call with a **structural-R:R** rationale, not a bad week:

> "wins small, lets losses run; real-money −0.64R/trade over 15 trades
> (totalR −9.6); 5-year backtest −0.99R/trade (−467R); net-negative at any
> size; a 5y sweep found **no** `min_confidence` floor salvages net_R — it's a
> structural R:R problem. **Revisit only after an exit-logic redesign fixes the
> R:R (re-backtested + operator-approved).**"

Two things make it worth a fresh look rather than a quiet retirement:

1. **Our methodology has matured a lot since v1.** In 2026-05 we had a single
   pre-live gate. We now have: the M7 review matrix + M8 `strategy_tune_sweep`
   (anchored k-fold walk-forward), the **M20 exit-refinement pipeline**
   (hard-lever sweep → E0–E3 ML exit head → live-parity → Tier-3 flip), the
   **M21 entry-refinement pipeline** (P_win entry head + `signal_bar_v2`
   vol-at-entry features), the **regime router** (1-D ADX cells + the empty 2-D
   `trend_vol` scaffold) and the maturing shadow **regime heads**, plus
   `account_compat_matrix` (cost-aware EV + survival per account). None of these
   existed when ict_scalp's exits were designed.
2. **The M7 per-cell read hints the loss is localized.** The 2026-06-30 packet
   (n_closed=18) shows the negative PnL concentrated in the **`trending+volatile`**
   cell (−$8.53 / 25% win / 4 closed) while chop-calm / chop-volatile /
   transitional-volatile / trending-calm cells are positive. **Caveat:** the
   large "+$142 lifetime" is dominated by a small `unknown`-regime bucket
   (+$141 / 7 closed) that is likely paper/unstamped and must NOT be trusted as
   an edge — Phase 0 exists to remove exactly this skew before any conclusion.

## Hypotheses (to confirm/reject with evidence, not assume)

- **H1 — Exit-side R:R is the primary defect.** Wins are cut short and losses
  run past a sane stop. This is what the demotion note asserts; it is the
  leading hypothesis and the M20 pipeline is built for it.
- **H2 — Regime-conditioned.** The strategy has a real edge in some
  `(trend, vol)` cells and bleeds only in others (trending+volatile /
  marginal-trend). A 2-D regime gate salvages it without touching the entry.
- **H3 — Entry precision is low on BTC 5m.** The sweep+displacement+FVG-rejection
  entry may fire too often at low quality on the 5m chart; an entry-quality
  filter (confirmation, vol-at-entry, P_win head) lifts it.
- **H4 — Structurally dead on BTC 5m.** The edge may simply not exist net-of-fees
  on this symbol/timeframe, and only a different instrument/timeframe (or full
  retirement) is the honest outcome.

These are not mutually exclusive; the plan sequences the cheapest,
highest-signal diagnostics first so we don't over-invest before H1/H4 is settled.

## Phase 0 — Honest baseline + clean per-cell dataset  *(Tier-1)*

Kill the measurement skew before drawing any conclusion.

- **Rebuild the trade-level dataset** for `ict_scalp_5m` across real + paper +
  shadow, each row stamped with **decision-time** `regime` + `vol_regime` (from
  the `signals` dual-write / `order_packages.meta`, not backfilled) so the
  `unknown`-cell bucket collapses to real cells. Tool: `diag/audit_query` +
  `/api/bot/order-packages?strategy=ict_scalp_5m` + `trades`.
- **Re-run the config-exact backtest** on fresh multi-year BTC 5m under current
  fees: `scripts/backtest_ict_scalp.py` (the canonical harness). Confirm or
  update the −467R / −0.99R-per-trade baseline. This is the number every later
  phase must beat OOS.
- **Deliverable:** a per-`(trend, vol)` cell table with **real n**, expectancy-R,
  win rate, and MFE/MAE per cell — the honest replacement for the M7 packet's
  skewed lifetime figure.

**Gate to proceed:** if the clean backtest is *not* structurally negative (i.e.
the demotion rested on the skew), that itself is the finding → propose
re-promotion via the regime-gated path (Phase 4) directly. If it confirms
negative, continue to diagnosis.

## Phase 1 — Diagnose the R:R leak  *(Tier-1)*

- **MFE/MAE distribution** per trade: do winners give back most of their peak
  (giveback problem)? do losers blow well past 1R before the stop
  (stop-geometry problem)? do trades die at the timeout flat (no-edge / hold-too-long)?
- **Exit-reason attribution:** split realized-R by `sl` / `tp` / `trail` /
  `timeout` / `flip`. A scalp that "lets losses run" should show a fat left tail
  on `sl`/`timeout` and a thin right tail on `tp`.
- **Hold-time vs outcome:** is there a hold-time past which expectancy goes
  negative (→ a stale-stop lever)?
- **Deliverable:** the specific R:R failure mode(s), which directly selects the
  Phase 2 levers.

## Phase 2 — Exit refinement (the M20 pipeline)  *(Tier-1 research → Tier-3 flip)*

Run the **`exit-refinement` skill** end-to-end for `ict_scalp_5m × BTCUSDT × 5m`
(add the row to `docs/research/exit-refinement-coverage.json`):

- **P2 hard-lever sweep (IS/OOS, config-exact)** via `scripts/backtest_ict_scalp.py`
  (mirror the pullback harness levers): stale-exit-bars/`below-r`, giveback
  (`giveback-min-mfe-r` / `giveback-r`), trail-decay (`arm-r` / `stall-bars` /
  `tight-mult`), partial-TP banking (`bank-frac` / `bank-at-r`), timeout. Grid
  each lever; keep only OOS-positive, maxDD-improving settings.
- **E0→E1→E1.5 ML exit head** if levers alone don't clear the gate: build the
  per-bar truncation-honest dataset (`scripts/ml/build_exit_head_dataset.py`),
  train the "P(recover ≥ X R from here)" head (`scripts/ml/train_exit_head.py`,
  purged walk-forward + τ-policy replay), ties into `MB-20260712-ML-EXIT-HEAD`.
- **Gate:** OOS net_R **> 0** with maxDD ≤ baseline, holding across k-fold folds
  (not one lucky split).

## Phase 3 — Entry refinement (the M21 pipeline)  *(Tier-1 research → Tier-3)*

Only if Phase 2 gets R:R to neutral/positive but win-rate/precision is still the
ceiling:

- **Entry-quality filters:** confirmation-bars, vol-at-entry cap/floor
  (`vol-skip-above/below-pctl`), time-of-day (killzone) cells — config-exact
  sweeps under the M21 discipline.
- **P_win entry head:** `scripts/ml/train_entry_head.py --features signal_bar_v2`
  (decision-bar features incl. `entry_atr_pctl`), observe-only shadow first, per
  the E-3 leakage lesson (decision-bar features only — no age-0 anchor).

## Phase 4 — Regime routing (compose, don't replace)  *(Tier-1 → Tier-3)*

- Once Phase 0 gives **n ≥ 30 per `(trend, vol)` cell**, author the 2-D
  `trend_vol` OFF cell(s) in `config/regime_policy.yaml` for the losing cell(s)
  (the scaffold is shipped empty for exactly this). Ties `PB-20260609-002`.
- Cross-check against the maturing **ML regime heads** (BTC 15m advisory is live;
  the 2-D vol label already routes BTC cells via `REGIME_ML_VERDICT_MODE`) — a
  better regime detector may gate the bad cell without a hand-authored row.
- This is the "turn it off *temporarily in the wrong regime*, not kill it"
  mechanism the operator wants — it layers **on top of** a fixed R:R, it does not
  substitute for one.

## Phase 5 — Symbol / timeframe expansion  *(Tier-1)*

If BTC 5m stays structurally negative after 2–4 (H4 confirmed on BTC):

- Re-test the same ruleset on other symbols/timeframes (the config already
  documents a 1m-variant path; candidates: ETH/SOL 5m, SPY 5m during RTH).
- **Mandatory `account_compat_matrix`** per candidate account before any routing
  (prop → cost-aware EV+survival; standard → net-of-fee) — never route a leg to
  an account it wasn't evaluated against.

## Phase 6 — Validation & re-promotion  *(Tier-3, operator-gated)*

- Walk-forward **k-fold** confirmation (`scripts/ml/strategy_tune_sweep.py`
  discipline — ≥3 folds, OOS lift ≥ train lift).
- **Live-parity check** (M20 P5): diff live-logged decision rows vs the offline
  recompute for the same bars before trusting any head/lever live.
- Re-promote `execution: shadow → live` **with** the Phase-4 regime cell as
  protection, then the **first-decision health check** (M20 P7) on the first live
  fires.

## Kill criterion (the honest off-ramp)

If after Phases 2–4 the OOS net_R on BTC 5m stays **≤ 0** AND no Phase-5
symbol/timeframe clears the k-fold gate, **retire `ict_scalp_5m`** (remove from
config, archive the research) rather than leave it shadow-soaking indefinitely.
A permanent-shadow strategy that never graduates is its own form of drift.

## Tooling / skill map

| Phase | Vehicle |
|---|---|
| 0 | `diag/audit_query`, `/order-packages`, `scripts/backtest_ict_scalp.py` |
| 1 | backtest harness trade export + MFE/MAE/exit-reason analysis |
| 2 | **`exit-refinement` skill** → `backtest_ict_scalp.py`, `build_exit_head_dataset.py`, `train_exit_head.py` |
| 3 | **M21** → `train_entry_head.py --features signal_bar_v2`, config-exact entry sweeps |
| 4 | `config/regime_policy.yaml` 2-D cells + ML regime heads (`REGIME_ML_VERDICT_MODE`) |
| 5 | backtest harness per symbol/tf + **`scripts/prop/account_compat_matrix.py`** |
| 6 | `strategy_tune_sweep.py` (k-fold), M20 P5 parity, Tier-3 flip + first-decision check |

## Related backlog / milestones

- `PB-20260630-ICTSCALP-DEGRADE` (owner review item — this plan is its execution).
- `PB-20260609-002` (author regime cells for ict_scalp once n accrues) — Phase 4.
- `MB-20260712-ML-EXIT-HEAD` — the exit head this composes with.
- M20 (`docs/research/M20-exit-head-PROGRAM.md`) + M21
  (`docs/research/M21-entry-refinement-DESIGN.md`) — the pipelines Phases 2–3 run.
