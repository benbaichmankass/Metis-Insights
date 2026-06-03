# Sprint Log: S-MLOPT-S6-FU (signal-log event source + MES root-cause)

## Date Range
- Start: 2026-06-03
- End: 2026-06-03

## Objective
Two linked M14 follow-ups from S-MLOPT-S4–S8 (read the originating sprint
logs for context):

1. **Investigation 1 — root-cause MES's empty trade journal**
   (`setup_labels` MES = 0 rows; S8 cross-symbol transfer claim unmeasurable).
   Tier-1 read-and-propose; do not flip any live switch.
2. **Investigation 2 — MB-20260603-002**: add a signal-log event source to
   `ml/datasets/families/setup_candidates.py` (sample candidates from the
   strategies' real decision points instead of CUSUM momentum events) and
   re-run the live_holdout meta-label eval to test the highest-leverage
   Tier-1 ML lever from S6.

Success: MES root cause identified with evidence; signal-log enabler shipped
Tier-1; new Tier-3 manifest proposed (`research_only`); the eval evidence
appended to `MB-20260603-002`.

## Tier
- **Tier-1** for: the MES investigation (read-only diag), the
  `signal_log_db` enabler in `setup_candidates.py`, the new
  `event_source` schema column, the tests, the backlog update.
- **Tier-3** (operator-gated) for:
  `ml/configs/setup-candidates-metalabel-siglog-v1.yaml` and any promotion
  past `shadow`. The manifest ships at `research_only`; the PR is a draft.
  This sprint **proposes**, the operator approves.

## Starting Context
- S-MLOPT-S6 honest negative: meta-label scored acc 0.670 < majority baseline
  0.756 on the 352 real BTCUSDT closed trades; synthetic CUSUM win rate
  ~0.457 vs real 0.244 ⇒ large train↔eval domain gap.
- S-MLOPT-S8 honest null: cross-symbol transfer to MES unmeasurable — MES
  has **0 real closed trades** in the journal (`setup_labels` MES = 0 rows;
  joint build assembles 6,723 MES synth + 16,084 BTC synth).
- The two findings are linked: same problem (not enough realistic labeled
  trade data) showing up two different ways. This sprint addresses both.

## Files and Systems Inspected
- `ml/datasets/families/setup_candidates.py` (the event-sampling +
  triple-barrier emit core), `ml/datasets/labeling/triple_barrier.py` (the
  labeler reused for signal-log rows), `ml/experiments/splitters.py`
  (`live_holdout` partition rules), `ml/configs/setup-candidates-metalabel-v1.yaml`
  (the S6 manifest mirrored), `src/utils/signal_audit_logger.py` +
  `src/units/db/database.py::insert_signal` (the audit→DB dual-write the new
  reader depends on), `src/web/api/routers/diag.py::/audit_query` (the
  contract the dual-write supports).
- Live VM state via the diag relays — `/api/diag/services`,
  `/api/diag/snapshot?limit=300`, `/api/diag/log_file?name=ibkr_mes_pull`,
  `/api/diag/journal?table=order_packages` (issues #2705 / #2708 / #2710 /
  #2711). `config/accounts.yaml` + `config/strategies.yaml` on disk for
  routing + execution gates.

## Investigation 1 — MES root cause (evidence-led)

### What MES looks like on the live VM right now

From the diag snapshot (#2710, `/api/diag/snapshot?limit=300`, 2026-06-03
13:35Z): in ~13 minutes of audit-tail traffic, MES + MGC + MHG each generated
**16 dedicated per-strategy eval rows + 16 pipeline_result rows** (32 each).
Every MES row is the same shape:

```
event: mes_trend_long_1d_eval
strategy: mes_trend_long_1d
symbol: MES
timeframe: 1d
side: none
reason: Strategy 'trend_donchian': no breakout on the latest bar
  (close=7607.5 within channel [7079.75, 7632.75]) — non-actionable
regime_source: adx-14, adx_14: 32.9274
```

Plus a `pipeline_result` `multiplexed_intents` row, also `side: none, reason:
no_signal`. **No MES order_packages exist** (#2711 — the 200-row order-package
tail contains only BTCUSDT). MES is being evaluated, not silently dropped.

### Service state (#2705)

| Unit | State |
|---|---|
| `ict-trader-live.service` | **active** |
| `ict-web-api.service` | active |
| `ict-liveness-watchdog.timer` | active |
| `ict-ib-gateway-watchdog.timer` | active |

Healthy. Watchdogs not in restart loops.

### Routing on disk

`config/accounts.yaml::ib_paper` (the only IB account at `mode: live` with
strategies):

```yaml
ib_paper:
  mode: live
  strategies: [turtle_soup, vwap, ict_scalp_5m, mes_trend_long_1d,
               mgc_pullback_1d, mhg_pullback_1d]
  symbols: [MES, MGC, MHG]
```

`config/strategies.yaml::mes_trend_long_1d` (the ONLY MES-emitting strategy —
the crypto strategies on `ib_paper` are gated out by the symbol→exchange
dispatch):

```yaml
mes_trend_long_1d:
  enabled: true
  execution: live           # PROMOTED shadow → live on 2026-06-02 09:03 UTC
  timeframe: "1d"           # daily — at most ONE evaluation per UTC day
  symbols: [MES]
  donchian: 30              # long-only Donchian-30 breakout + Chandelier trail
  long_only: true
```

`git log -- config/strategies.yaml` confirms the promotion is **commit
`1ec7075` 2026-06-02 09:03 UTC**. The strategy was first wired (at
`execution: shadow`) on 2026-05-31 in `73d3292`. So:

| Pre-2026-05-31 | strategy didn't exist; no MES signals possible |
| 2026-05-31 → 2026-06-02 | execution: shadow — logged-only, NEVER produces order packages |
| 2026-06-02 → now (~30h) | execution: live — eligible to fire, but… |

### Root cause

**Hypothesis 4 — "no signals" — is correct.** Three independent reasons
pile up:

1. The strategy was at `execution: shadow` for its entire history before
   2026-06-02. Shadow strategies log eval rows but never produce order
   packages → trades, by design ([the two execution gates contract in
   CLAUDE.md][gates]).
2. Since promotion to `live` on 2026-06-02 (~30 hours of live eligibility),
   the strategy is a Donchian-30 long-only breakout on the **daily**
   timeframe. The daily timeframe yields ~1 evaluation per UTC day; the
   breakout condition `close > prior_30d_high` is a once-in-many-weeks
   event.
3. The diag snapshot confirms today's MES sits at 7607–7608 — **25 points
   below the prior-30d high of 7632.75**. No breakout has fired since
   promotion. The strategy is correctly emitting `side: none` per tick.

**There is no recording bug, no broker outage, no gating bug, no missing
market-data entitlement.** The pipeline is wired correctly; the strategy
simply hasn't triggered an entry yet. MGC + MHG (added the same day) tell
the same story: per-tick eval rows with `side: none` because no
trend-pullback setup has formed.

### What this means for the S8 cross-symbol claim

The S8 transfer claim ("does adding BTC training rows help MES?") remains
unmeasurable for the right reason: **no MES real holdout exists yet, and
none can plausibly exist within days.** A daily-timeframe breakout strategy
needs months to a year to accumulate a powered holdout. The fix surfaces
align with what the S8 sprint already proposed:

- **Wait for live MES trades to accumulate** (months) — the slow,
  highest-quality path. Nothing to do; the pipeline is correctly
  recording.
- **S-MLOPT-S7 backtest-augmented MES labels** — already shipped (#2698).
  The recorder can manufacture an MES backtest holdout from
  `scripts/backtest_trend.py` runs. This is the actionable lever for an
  earlier transfer measurement.
- **Add more MES strategies** that fire faster (lower timeframes, more
  setups per day) — out of scope for this sprint; would need its own
  Tier-3 proposal + paper-trade validation.

### Concrete fix proposal

**No code/config change is needed to "fix" MES** — there is no bug to fix.
The honest read is the strategy works as designed and the data we want
takes time to accumulate. The closest Tier-1 follow-up that converts this
finding into testable evidence is:

> **Health-review backlog entry (Tier-1, additive)**: log "the strategy is
> correctly inert" as the baseline so a future /health-review doesn't
> re-raise "MES never trades" as a regression. Add a coarse alert: if
> `mes_trend_long_1d_eval` rows STOP appearing for >24h, that's a real
> regression (vs `side=none` being normal).

If the operator wants an earlier MES transfer signal, the actionable Tier-3
proposal is **add a faster-cadence MES strategy** (e.g. an intraday MES
breakout on 15m / 1h instead of 1d) so MES generates orders within weeks
rather than quarters. This is a separate, explicit operator decision
(strategy selection + risk allocation are Tier-3); this sprint flags it as
the lever and stops.

[gates]: ../../CLAUDE.md#the-two-execution-gates

## Investigation 2 — Signal-log event source (the Tier-1 enabler)

### Work Completed

- **`ml/datasets/families/setup_candidates.py`** — new `signal_log_db` kwarg
  reads every `side=buy|sell` row in `trade_journal.db::signals` (the
  audit-log dual-write — the strategies' real decision points), locates each
  at the bar covering its `logged_at_utc` (`bisect`), and emits a candidate
  labeled with the SAME triple-barrier as CUSUM. Same `BarrierConfig`, same
  local-vol sizing, same fill rules, same `_feature_fields` — so signal-log
  rows live in one feature space with CUSUM + live-trade rows. New row tag
  `event_source ∈ {cusum, signal_log, live}` disambiguates the three
  samplers. `signal_log_strategies` optionally restricts to specific
  strategies; `signal_log_sides` defaults to `("buy","sell")` so no-signal
  eval rows are skipped. `include_cusum` is the preferred name for the
  legacy `include_synthetic` toggle (kept as alias for backwards compat).
  Best-effort on missing DB / missing-signals-table / missing-column —
  returns `[]` rather than crashing the build.
- **`ml/configs/setup-candidates-metalabel-siglog-v1.yaml`** *(Tier-3
  proposal, draft)* — meta-label manifest that mirrors the S6 manifest
  (`LightGBMRegressionTrainer → won`, `ClassificationEvaluator`,
  `live_holdout`, `research_only`) but trains on the signal-log
  distribution instead of CUSUM. `forbidden_features` updated to exclude
  the new `event_source` column. Promotion past `shadow` stays
  operator-gated.
- **Tests** (`tests/ml/test_setup_candidates.py` — 8 new) cover sampling +
  triple-barrier labeling, strategy filter, symbol filter, missing-DB +
  missing-signals-table no-op, `include_cusum=False` signal-log-only mode,
  the three-source mix (CUSUM + signal_log + live), and the comma-string CLI
  form of `signal_log_strategies`.
- **Backlog** (`docs/claude/ml-review-backlog.json::MB-20260603-002`) —
  evidence-log + status_history updated; the headline trainer-VM eval is
  the next step (dispatched in this session).

### Validation Performed
- `pytest tests/ml/test_setup_candidates.py tests/ml/test_cross_symbol.py tests/ml/test_metalabel.py tests/ml/test_splitters.py` → **51 passed**. The existing 43 tests are untouched; the 8 new are signal-log-only.
- Manifest loads via `TrainingManifest.from_yaml`; `target_deployment_stage:
  research_only`; `forbidden_features` excludes every outcome + label +
  source column (`won`, `label`, `r_multiple`, `ret`, `barrier_touched`,
  `is_live_trade`, `event_source`, `entry_price`, `signal_vol`,
  `holding_bars`).
- **No-leakage by construction**: signal-log rows reuse `_feature_fields`
  (past-only window from bar `e`) and `label_event` (future-only window
  from bar `e+1` to `e+1+max_holding`). The two windows never overlap —
  same guarantee CUSUM rows have.
- **Trainer-VM eval** — #2716 (after #2713/#2714/#2715 were chasing CLI-flag
  + venv-python issues). Signal-log meta-label trained on the v003 dataset
  (signal_log train rows=8,739 win=0.469; live eval rows=352 win=0.244;
  `include_cusum=false`), scored on the same 352 real BTCUSDT holdout.

  | Metric | S6 CUSUM (#2697) | S6-FU Signal-log (#2716) | Majority baseline |
  |---|---|---|---|
  | accuracy | 0.670 | **0.526** | 0.756 |
  | precision | 0.266 | 0.290 | — |
  | recall | 0.198 | 0.651 | — |
  | f1 | 0.227 | 0.401 | — |
  | brier | 0.219 | 0.417 | — |

  **Honest negative — signal-log accuracy is WORSE than the S6 CUSUM run
  (0.526 < 0.670) and both lose to the baseline.** The recall jumped because
  the train base rate is higher (0.469 vs 0.244 real → predictor biased toward
  "win"); precision barely moved; brier doubled (less calibrated). The
  "CUSUM is easier than real setups" hypothesis was **wrong**: signal-log win
  rate (0.469) is almost identical to CUSUM (0.457), so both synthetic
  populations are equally pulled from a different distribution than real
  trades (0.244). **Real root cause: the triple-barrier LABELS, not the event
  sampler.** Synthetic candle barriers assume optimistic fills, no slippage,
  no risk-manager filtering; real trades win less because real execution +
  real risk gating filter the obvious winners *out*, leaving structurally
  harder cases. No amount of "better train-event-sampling" closes this gap.

## Documentation Updated
- `docs/claude/ml-review-backlog.json` — `MB-20260603-002` evidence + status.
- This sprint log.
- The PR (#2712) body documents the change-set, tier split, and test plan.

## Risks and Follow-Ups
- **MB-20260603-002 RESOLVED-NEGATIVE.** The signal-log lever did NOT close
  the gap, and accuracy is *worse* than the S6 CUSUM baseline. The Tier-1
  enabler + manifest still stand as useful infrastructure (event-source
  composability matters for future mixing); the manifest stays
  `research_only`. No promotion proposed.
- **MES will not produce a measurable transfer holdout this sprint or next**
  — Investigation 1 above. The S8 capability is ready; the data takes time.
- **The new `event_source` column** is a schema addition; downstream readers
  that strictly schema-check (the validator runs on build) will see one new
  column. The existing builder validate step accepts it (the `schema`
  classvar is the source of truth, and the change is additive).
- **Tier-3 gates stand**: the signal-log manifest is a proposal; promotion
  past `shadow` is operator-gated. No live-path file or strategy / risk
  config was touched.

## Next Recommended Sprint

The eval rules out "better train-event-sampling" as the lever. The next
high-leverage Tier-1 move is to attack the *labels* directly with REAL
execution modeling:

- **S-MLOPT-S6-FU-2 — backtest-augmented setup_candidates labels using the
  standalone strategy backtest harnesses** (`scripts/backtest_squeeze.py`,
  `backtest_fade.py`, `backtest_trend.py`, `backtest_ict_scalp.py`,
  `src/backtest/run_backtest_vwap.py`). These harnesses already model real
  slippage + the strategies' actual entry rules + per-strategy exit logic,
  so each backtest trade is a real-distribution label for that strategy at
  that historical bar. Port the S-MLOPT-S7 `backtest_recorder` pattern
  (`source` column + `include_backtest` flag) to `setup_candidates`: each
  backtest trade becomes a row with the strategy's actual entry features +
  the harness's actual realized outcome (NOT a triple-barrier synthetic
  label). Then re-run the live_holdout eval with **backtest-train + real-eval**.
  This is the apples-to-apples version of the train distribution we tried to
  get with signal-log. Tier-1 trainer-side; Tier-3 if it yields a new
  manifest worth promoting.
- **In parallel, Phase 2 features** (S9 range-based vol estimators — Yang-Zhang,
  Garman-Klass — Tier-1) is the alternative track. At n=352 real trades,
  *better features* may also matter more than a better sampler.
- **S-MLOPT-S13 per-bar regime scoring** (Tier-2, live-runtime) remains the
  highest-leverage *regime* unblock — operator-gated, parked as its own arc.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched; manifest is a Tier-3 proposal.
- [x] Roadmap status was checked + (no row update needed — this is an S6
      follow-up on the already-closed S5–S8 block; the backlog entry tracks
      it).
- [x] Contradictions were recorded (none new — investigation 1 confirms the
      strategy is correctly inert per the canonical execution-gate contract).
- [x] Remaining unknowns were stated clearly (the eval number is pending; the
      PR is draft until it lands).
