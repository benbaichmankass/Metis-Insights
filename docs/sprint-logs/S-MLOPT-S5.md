# Sprint Log: S-MLOPT-S5

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
- Primary goal: break the decision-model **data wall** (gap G4 — the
  setup-quality / trade-outcome models train on ~80 real closed trades and
  collapse to a baseline). Manufacture a **dense, properly-labeled** dataset of
  *hypothetical* setups from bar history with the de Prado **triple-barrier**
  method, so there are thousands of labeled rows instead of 78 trades.
- Deliverable: a reusable triple-barrier labeler + a `setup_candidates` dataset
  family, with leak-free signal-time features and the domain-shift discipline
  (synthetic fills ≠ live fills) baked in.

## Tier
- Tier 1. Trainer-side dataset family + labeling tooling + tests only. It reads
  already-built `market_raw` datasets and writes a new dataset family; it
  touches **no** `src/runtime/`, order-path, config, or live file. A
  trainer/manifest that *consumes* this family to train a decision model
  (S-MLOPT-S6 meta-labeling) is Tier-2/3 and ships separately with operator
  approval.

## Starting Context
- M14 Phase 1.1, the first sprint of Phase 1 (break the data wall). Depends on
  Phase 0 (S1–S4): the purged WF-CV + honest holdout discipline is what lets us
  *trust* a result trained on synthetic candidates, and the gates (S4) are what
  a model trained here must eventually clear.
- Reference: de Prado, *Advances in Financial ML*, Ch. 2 (CUSUM event sampling)
  + Ch. 3 (triple-barrier labeling); `mlfinpy` labeling.
- Closest existing pattern: `market_features` (forward-window regime labeling on
  `market_raw`) — triple-barrier is its **path-dependent** cousin (race three
  barrier prices bar-by-bar rather than read a fixed forward window).

## Files and Systems Inspected
- `ml/datasets/{builder,registry,metadata}.py` (family ABC, registration,
  `LeakageStatus`), `ml/datasets/families/{market_features,setup_labels,trade_outcomes,setup_labels_audit}.py`
  (DB/market_raw read patterns, feature engineering, leakage notes),
  `ml/datasets/adapters/base.py` (`CANONICAL_SCHEMA` for `market_raw`).

## Work Completed
- **`ml/datasets/labeling/triple_barrier.py` (new):** pure-stdlib de Prado
  primitives —
  - `cusum_events(values, threshold)` — symmetric CUSUM filter; returns
    `(index, side)` where side ±1 is the breach direction (up → long candidate,
    down → short). The canonical event sampler that de-clusters bar history into
    thousands of events.
  - `label_event(highs, lows, closes, …)` — races an upper (TP), lower (SL), and
    vertical (timeout) barrier from the entry bar; TP/SL distances = `pt_mult` /
    `sl_mult` × signal-bar local vol. Returns a `BarrierOutcome`
    (`barrier`/`label`/`r_multiple`/`ret`/`holding_bars`).
  - **Realistic-fill discipline** (mitigates the synthetic-vs-live optimism the
    roadmap flags): touches detected on bar **high/low** not close;
    **adverse-first** on a bar that straddles both barriers (resolve to the
    stop — never claim the profit when the bar could have stopped you first);
    optional `slippage` charged against every fill.
- **`ml/datasets/families/setup_candidates.py` (new):** wires the labeler over a
  built `market_raw` dataset. CUSUM-samples events (adaptive threshold =
  `cusum_threshold_mult` × median rolling vol), enters at the **next bar's
  open** (no signal-bar look-ahead), computes **signal-time features**
  (past-only: log return, rolling vol + quantile bucket, momentum, hour/dow,
  lagged returns, direction) and emits them with the **future-only** barrier
  label. `leakage_test_status: passed` by construction (feature window
  `[e-w+1..e]` and label window `[e+1..]` never overlap). Every row carries
  `is_live_trade: false` so a later PR appends REAL closed-trade rows for the
  mandatory held-out real-trade eval. Registered in
  `ml/datasets/registry.py`.
- **Tests:** `tests/ml/test_triple_barrier.py` (CUSUM up/down/none; TP/SL/timeout;
  adverse-first straddle; short-side TP; slippage reduces return; vol guard;
  bad-tick log-price), `tests/ml/test_setup_candidates.py` (family registered;
  builds labeled rows both directions; builder schema-validates the write;
  **no-look-ahead** entry == next-bar open; empty on too-few bars).

## Validation Performed
- `tests/ml/test_triple_barrier.py` + `tests/ml/test_setup_candidates.py` →
  15 passed. Full `tests/ml/` (excluding the pandas-only `test_resample` /
  `test_yfinance_offvm` the sandbox can't import) → **494 passed, 1 skipped**.
  `ruff check` clean; `py_compile` clean; `python -m ml list-families` shows
  `setup_candidates`.
- **No-leakage** is asserted directly (`test_no_lookahead_entry_is_next_bar`:
  every row's `entry_price` equals the post-signal bar's open) and holds by
  construction (disjoint feature/label windows), mirroring the `market_features`
  guarantee.
- **Density verification** (the roadmap's "≥ low-thousands of labeled candidates
  per symbol" success bar) runs on the trainer VM against the real
  `market_raw` BTCUSDT/MES datasets — reported below once the build returns.

## Density build (trainer VM) — ✅ success bar cleared
Built `setup_candidates` over the real `market_raw` datasets via the
`trainer-vm-diag` relay (#2692, branch `3535838` in a detached worktree). The
"≥ low-thousands of labeled candidates per symbol" bar is **comfortably met** —
from just the 1h / 15m datasets (the 1m/5m files hold far more):

| `market_raw` dataset | bars | candidates | long / short | tp / sl / timeout | win_rate |
|---|---|---|---|---|---|
| BTCUSDT 1h (v002) | 43,824 | **15,732** | 8044 / 7688 | 7120 / 8478 / 134 | 0.457 |
| MES 15m (v001) | 28,996 | **6,723** | 3474 / 3249 | 2722 / 3183 / 818 | 0.450 |

vs the **~80** real closed trades the decision models train on today (G4). Reads:
- **Balanced long/short** — the symmetric CUSUM filter samples both breach sides
  evenly, as designed.
- **Win rate sits just below 0.50** (0.45–0.46) with symmetric `pt_mult=sl_mult=1`
  — the **conservative-fill discipline working**: the adverse-first straddle rule
  resolves ambiguous bars to the stop, so the base rate is *not* optimistically
  inflated above coin-flip. That sub-0.5 base rate is exactly what a meta-label
  model (S-MLOPT-S6) must beat to earn its keep.
- `tp + sl + timeout = rows` in both (sanity ✓); MES 15m's higher timeout share
  (818) vs BTC 1h (134) reflects its different vol/horizon dynamics.
- Note (relay mechanics): a first attempt looping over **all** datasets broke the
  SSH pipe on the 3.2M-bar BTC 1m file — the family is fine; the relay just can't
  load multi-GB JSONL + process it in one SSH session. Build per-dataset (or stream)
  for the big timeframes; the dataset builder writes incrementally so a real
  `python -m ml.datasets build setup_candidates` cycle is unaffected.

## Documentation Updated
- `ROADMAP.md` S-MLOPT-S5 row; `docs/ml/optimization-roadmap.md` Session 1.1
  shipped-block; `docs/architecture/ai-model-platform.md` (family inventory row
  + change-log row); this sprint log.

## Risks and Follow-Ups
- **Event source.** Candidates are sampled from bar history with CUSUM (the de
  Prado-canonical dense sampler the success criterion needs). Wiring the
  strategies' *actually-logged* signals (`trade_journal.db::signals`) as an
  additional event source is a natural follow-up once we want setups that match
  strategy logic specifically rather than price-move events.
- **Domain shift is real.** These are synthetic fills. The labeler hedges
  optimism (conservative fills + slippage), but a model trained here MUST be
  evaluated on REAL trades — the `is_live_trade` column reserves that split; the
  real-trade rows + the held-out evaluator land with S-MLOPT-S6 / S7.
- **No manifest shipped** (Tier-1 boundary). The meta-labeling decision model +
  its manifest is S-MLOPT-S6 (Tier-2/3, operator-gated).

## Next Recommended Sprint
- **S-MLOPT-S6 (1.2)** — meta-labeling decision model on the `setup_candidates`
  labels, evaluated on the REAL-trade holdout under the S1 purged WF-CV and
  graded by the S4 `gate-check`. (Or **S-MLOPT-S13 (3.1)** per-bar regime
  scoring — Tier-2, higher-leverage unblock — if the operator wants to approve
  the live-runtime change.)

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched.
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns were stated clearly.
