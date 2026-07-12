# M20 Exit-Head Program — design, testing, and validation plan (2026-07-12)

**Operator directive (2026-07-12):** build the ML exit supplement — "we need a
full plan for design, testing, and validating across all relevant strategies…
the system should be more proactive on realizing when a trade has reached max
potential and it's time to bank profits."

**The one-sentence design:** a per-bar model that answers, for every OPEN
position, *"does holding from here still pay?"* — trained on observed trade
paths (no simulator), evaluated by truncation replay against the shipped hard
exit levers, rolled out through the existing candidate → shadow → advisory
ladder with the usual operator promotion gate.

## Why a learned head (and what the hard rules can't do)

The M20 evidence (memo § 2–6) shows the exit problem is *state-dependent*:
mean giveback 2.08R, 26% round-trippers, but every *fixed* rule helps some
families and hurts others (stale-stop: donchian ETH/SOL yes, scalps no;
banking: net_R loser everywhere; trail geometry: per-family, opposite signs).
A learned head conditions on the trade's own state (age, open R, peak, chop,
vol, regime) and can express exactly the operator's ask — "this trade has
reached max potential" — without a one-size threshold.

## Phase E0 — dataset (trainer, Tier-1, free CPU)

**Row = (trade, bar).** For every resolvable trade, one row per native-TF bar
of the hold:

| group | features (strictly from bars ≤ t — leakage-guarded) |
|---|---|
| trade state | `age_bars`, `open_r`, `mfe_r`, `mae_r`, `giveback_r` (= mfe − open), `chop_frac_so_far`, `stagnation_run`, `dist_to_stop_r` |
| market state | native-TF `yang_zhang_vol` / `parkinson_vol` (normalized by entry-time value), `donchian_mid_dist_atr`, `atr_ratio_now_vs_entry`, `hour_of_day`, `dayofweek` |
| context | strategy-family one-hot, direction, symbol asset-class; regime-head score where served (optional column, honest-null) |

**Labels (pure truncation observables — the T0.4 lesson, no simulator):**
- regression: `future_r_delta = final_realized_R − mark_R(t)`
- classification (primary): `holding_pays = future_r_delta ≥ +0.25R`

**Two data sources, deliberately:**
1. **Live closed trades** (journal + candles) — ground truth incl. real
   fees/monitor exits; small n (~275 path-resolvable today, growing).
2. **Harness-generated trades** (`backtest_{trend,pullback,squeeze,fade}.py
   --emit-trades` over 5y, per family) — volume (thousands of trades), same
   entry logic, engine-consistent exits. This breaks the n≈78 label wall the
   same way `MB-20260530-001` prescribes. Train on (2), validate on (1) as the
   distribution-shift check: if the head's edge disappears on live trades, it
   learned the engine, not the market — hard stop.

**Deliverable:** `scripts/ml/build_exit_head_dataset.py` →
`datasets-out/exit_head/<family>/` + a build report (rows, class balance,
per-family/per-year counts). Coverage requirement: every family with ≥300
harness trades AND ≥20 live trades enters E1; others wait for data
(today that gates in: donchian-1h crypto, pullback-2h, squeeze/fade-4h;
scalps-5m are candle-granularity-limited on 15m data — needs 5m side-streams;
equities-1d blocked on the candle-coverage backlog `MB-20260712-EXIT-ANALYSIS-COVERAGE`).

## Phase E1 — training + offline policy evaluation (trainer)

- **Models:** LightGBM classifier per family + one pooled model with family
  one-hots (pick per-family vs pooled by OOS AUC; the fleet's existing
  manifest/registry machinery, `ml/configs/` + `python -m ml train`).
- **Splits:** purged walk-forward by TIME (per-year folds, 1-week embargo
  around fold edges so overlapping holds can't leak).
- **Model metric:** OOS AUC + calibration (reliability curve) — but the
  decision metric is the **policy replay**: for threshold τ, replay "exit at
  the first bar where P(pays) < τ" over held-out trades by truncation (exit
  value = observed close, identical honesty to the M20 counterfactuals).
  Report Δnet_R, ΔmaxDD, Δhold-time vs (a) actual exits and (b) the best hard
  lever per family (stale-stop / giveback-stop with their swept params).
- **GATE E1→E2 (per family):** OOS AUC materially > 0.55 AND the τ-policy
  beats the best hard rule on net_R AND maxDD in the walk-forward, AND the
  live-trade validation set agrees in sign. Anything else = honest negative,
  the hard rules stand.
- **Capital-efficiency metric (the operator's point):** also report
  `net_R per position-day` — an exit policy that matches net_R but frees the
  slot 3× faster wins, because the slot is re-deployable into new trades.

## Phase E2 — live shadow (observe-only, Tier-2 deploy)

- Register `exit-head-<family>-v1` at **shadow**. A bounded per-tick scorer in
  the monitor path (same budget pattern as `regime_bar_scoring`) scores every
  open position's current row, logging to `shadow_predictions.jsonl` +
  would-exit rows to `exit_lever_soak.jsonl` (`lever: "exit_head"`).
- **Gate doctrine (revised 2026-07-12, operator directive): the OFFLINE
  validation is the confidence gate — the live shadow is a MECHANICAL
  verification, measured in hours-to-days, not weeks.** The purged
  walk-forward + truncation-honest replay harness exists precisely so a
  passing head doesn't need a long calendar soak; the only genuinely new
  risks the live phase retires are (a) **train/serve feature skew** — the
  live scorer computes features in a different code path than the E0
  builder — and (b) plumbing correctness. Both are verified directly:
  1. **Feature-parity check:** diff the live records' `feature_row` against
     the E0 builder's row for the same trade/bars. Match ⇒ skew risk retired.
  2. **First-decision mechanics:** the first live scores are sanity-read
     (score in-distribution, dedup working, in-family guard holding).
  Once both pass, E3 proceeds; the head then keeps "soaking" ONLINE (live,
  order-influencing) under the standing monitoring — the next health-review
  MUST check the first real head-driven exit's mechanics, and the
  shadow/soak logs keep accruing the realized `future_r_delta` record for
  ongoing review. A long observe-only calendar soak is reserved for cases
  where offline validation was weak or impossible — not the default.
- Nothing reads it back while at shadow; standard stage-gate applies
  (shadow never influences orders).

## Phase E3 — graduation (Tier-3, operator-gated)

- Per-strategy YAML declaration (the M20 pattern):
  `exit_head_model: exit-head-<family>-v1`, `exit_head_threshold: τ*`,
  `exit_head_action: close | tighten_to_breakeven`. Absent = off; rollback =
  delete the lines. Monitor consults the ADVISORY-stage head only (the
  existing ladder is the safety rail).
- Evidence pack to the operator: E1/E1.5 walk-forward table + the E2
  mechanical verification (feature parity + first-decision sanity) +
  per-account compat (`account_compat_matrix`) — then the promotion decision.
- Post-flip: the first head-driven exit is a mandatory health-review check
  (mechanics working as designed); the live track record is reviewed in the
  standard /ml-review cadence, with demotion (delete the YAML lines) as the
  cheap rollback if live diverges from the walk-forward.

## Sequencing & compute

| step | where | cost | calendar |
|---|---|---|---|
| E0 dataset builder + first build | trainer, free CPU | one session | now |
| E1 train + policy replay | trainer nightly cycles | free CPU (LightGBM) | 1–2 sessions |
| E2 shadow scorer + mechanical verification | live VM (Tier-2 deploy) | negligible per-tick | hours–days (feature parity + first-decision sanity; revised 2026-07-12) |
| E3 graduation | PR + operator | — | after E2's mechanical checks pass |

Backlog anchor: `MB-20260712-ML-EXIT-HEAD` (updated to point here). Related:
`MB-20260712-SHADOW-LOG-HISTORY` (history horizon),
`MB-20260712-EXIT-ANALYSIS-COVERAGE` (equities/alt candle coverage — expands
E0's family set when closed).
