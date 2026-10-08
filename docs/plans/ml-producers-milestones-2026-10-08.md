# ML-PRODUCERS — milestone plan for the four demoted heads

> **Doc status:** `unknown` · category `plan` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> **Row:** `ML-PRODUCERS` (checklist) · **Lane:** item 8 of `PACE-W40` · **Written:** 2026-10-08 · **Plan only — no producer code, no registry change.**
> Operator, 2026-10-08, verbatim: *"Let's do three but we need to first plan the work and make sure that we can have like milestones ... that we don't waste all the usage ... and it doesn't ... end up like defaults taking priority over everything else"*.

## What was read (this session) and what was not

Read: the four manifests in `ml/configs/`, `src/runtime/regime_shadow.py`, `src/runtime/strategy_signal_builders.py::_emit_shadow_preds`, `src/runtime/conviction_inputs.py`, the `market_features` / `conviction_meta` / `setup_labels` families, `scripts/ml/orderflow_capture.py`, `docs/claude/work/TRAINER-CAPTURE-WATCH.json`, the five `PI-20260929-K85Q64SF-*` demotion rows, `docs/sprint-logs/S-ML-FEATURE-GAPS-20260729.md`, ROADMAP rows M36-D / T0.3 / S-MLOPT-S6.
**Not read: the trainer registry or shadow logs.** The served-vs-declared counts below are the 2026-09-29 `ML-DEMOTE` measurements carried in the pipeline rows, not re-measured here. All dollar sizes are **estimates**, not measurements.

## The hazard the operator named: defaults taking priority

Verified in code: `build_parity_feature_row` (`regime_shadow.py:309`) says side-stream features "are NOT emitted — … a no-side-stream training build emits them as `0.0` anyway". `market_features` writes `0.0` via `_finite_or_zero` when a side-stream is absent. So a producer that fails quietly would serve `0.0` — the same value training saw for the pre-capture era — and the head would score confidently on a default. **Rule for every producer below:** on any missing/stale input return `None` from `feature_row_for_predictor` (the existing skip path), never `0.0`; and the parity check counts *present AND non-default*, not just *present*.

## Per head

| head | declared → served (09-29) | why the rest are missing | cheapest honest path | class |
|---|---|---|---|---|
| `btc-regime-1h-lgbm-funding-svble-v1` | 7/10 → funding_rate, _zscore, _abs_z absent, n=4111 | **Missing producer.** Manifest already dropped the OI columns (retention ~8d) and is identical to the parent otherwise. Source is serveable: Bybit funding history (`ml/datasets/adapters/bybit_funding_oi.py`). | **Build the producer; no retrain.** Fetch the last ≥168 h of funding settlements, as-of carry-forward onto the closed-bar grid, then `rolling_zscore`/`extreme_magnitude` from `ml/datasets/funding_oi_features.py` — the same functions training uses. Pass as a `funding_row` beside `forecast_row` in `feature_row_for_predictor`. | LightGBM multiclass, existing |
| `btc-regime-5m-lgbm-flow-v1-offload` | 7/13 → ofi, ofi_zscore, vpin, order_imbalance, rel_spread_mean, microprice_dev absent, n=8448 | **Offline-only source** (forward-only L2 capture on the trainer VM; guarded off the live VM by `ICT_OFFVM_BUILD_HOST`). Capture is fresh (`TRAINER-CAPTURE-WATCH.json`, 2026-10-08T13:45Z). The head was promoted by the operator 2026-08-29, so demotion reversed an approved transition. | Read the side-car's per-bar `data.jsonl` and derive the six columns with `ml/datasets/orderflow_features.py` over the same 50-bar window training uses — **only if the offload scorer runs where that file is.** Whether it does is Milestone 2's first check, not assumed. | LightGBM multiclass, existing |
| `setup-quality-lgbm-v2` | 5/9 → account_id, bias, dayofweek, hour_of_day absent, n=32643 | `hour_of_day`/`dayofweek` are trivially producible. **`account_id` is not a signal-time fact**: the shadow row is built per strategy signal (`_emit_shadow_preds`, `base_row` has no account) and the account binds at fan-out. `bias` exists only if the strategy stamps `meta.bias` (vwap does). The row's own pipeline note ("all available at signal time") is wrong for `account_id`. | Feature-set cut (drop `account_id`) + add the two time features + retrain — but see the retire evidence. | LightGBM regression |
| `conviction-meta-v1` | 3/10 → c_strat, c_setup, c_wr, c_reg, adx_14, regime, vol_regime absent, n=31701 | **Ordering, not just a missing field.** The `c_*` inputs are computed from the *other* heads' captured scores *after* the predictor loop (`conviction_inputs.build_conviction_inputs`, called after `capture_shadow_preds`). A stacker scored inside that loop cannot see them. `c_reg` is also not a real signal yet (manifest: expected-optional, `MB-20260618-XA-D2B`). | A second-pass scorer after conviction is stamped, plus `regime`/`adx_14`/`vol_regime` from `meta`. Real work, and it inherits every upstream head's gaps. | LightGBM binary |

## Retire / park evidence

- **`setup-quality-lgbm-v2` — recommend RETIRE.** ROADMAP S-MLOPT-S6: it "lost to the baseline at n=80", and the meta-labeling line (`setup-candidates-metalabel-*`) was built as its replacement. Fixing the producer would feed a head already beaten by its own baseline, minus a feature (`account_id`) it trained on. Retiring is an operator act (pipeline row `…-0002` asks for it).
- **`conviction-meta-v1` — recommend PARK, spend $0 on a producer now.** ROADMAP M36-D (2026-07-27): both the live-only and backtest-augmented versions scored ~0.525 accuracy, **below the majority baseline**; T0.3 (2026-07-01) was inconclusive at n_eval=20. The binding constraint is labels (~99 closed BTC trades then), not the producer. A producer cannot fix that. Re-open only when Milestone 5's label gate passes.
- **`btc-regime-5m-lgbm-flow-v1` — keep, but gate hard.** The 2026-07-29 captured-window A/B was **unmeasurable**: 42 volatile of 15,816 bars (0.27%), zero volatile in the eval fold, both heads `f1_volatile 0.0`. There is no demonstrated lift to serve. Milestone 2 re-measures that before any producer money is spent.
- **`btc-regime-1h-lgbm-funding-svble-v1` — fix first.** Parent measured as the strongest 1h binary regime head (macro_f1 0.656, f1_volatile 0.479, per the manifest header — *not re-verified here*).

## Milestones (ordered by value per cost; each lands alone; each has a stop/go)

| # | milestone | est. | lands as | STOP/GO |
|---|---|---|---|---|
| **M0** | **Replay-parity harness.** Given a head and N closed bars, build the training row (offline `market_features`) and the live row (`feature_row_for_predictor`) for the *same* bars; report per-feature `present%`, `non-default%`, max abs diff. Build on `scripts/ml/_feature_parity_probe.py`. Shared by M1 and M3. | ~$1.5, Haiku/Sonnet lane | tool + tests, Tier-1 | **GO** if it reproduces the known gap on the current svble head (funding ×3 reported absent). **STOP** if it cannot run on the trainer without the OOM noted in `S-ML-FEATURE-GAPS` (then do M1 parity inline). |
| **M1** | **Funding producer** for svble-v1 (no retrain). Fail-closed → `None`. Parity-check on ≥500 bars incl. a settlement boundary (the z-score runs over 168 *carried-forward hourly* values, not ~21 settlements — the likeliest parity bug). | ~$3, Sonnet | `src/runtime` change — **Tier-2** (live signal path; observe-only, but it is the order-path builder) | **GO to shadow** only if every funding column has `non-default%` ≥ 99% and max abs diff ≤ 1e-6 on identical bars. **STOP** on any diff not explained by float order; do not tune. Then: promote-stage → shadow, require ≥1 live row with all 10 declared features non-null (the existing `clears_when`), then the ladder; promotion past shadow is Tier-3. |
| **M2** | **Flow feasibility check, $0 build.** (a) Where does the offload scorer run, and can it read the capture file? (b) How many volatile bars in the captured window *now*, and in an eval fold? | ~$0.5, read-only | note on `ML-PRODUCERS` | **GO to M3** only if (a) is yes **and** (b) gives ≥30 volatile bars in the last-20% fold (basis: below that, `f1_volatile` is noise; it was 0 on 07-29). **STOP** otherwise → file a `due_when` row on volatile-coverage and stop spending. |
| **M3** | **Flow producer** (six columns, `orderflow_features.py` reused, 50-bar window, fail-closed on stale capture). Parity via M0. **First** run the captured-window A/B with the new data — a head with no lift is not served. | ~$4, Sonnet | Tier-2 if it touches the live builder; Tier-1 if trainer-side only | **GO to shadow** only if M2 passed, parity as M1, **and** the A/B shows `f1_volatile` ≥ the v2 baseline under purged CV. **STOP** if lift is ≤ 0 → recommend retire. |
| **M4** | **Retire `setup-quality-lgbm-v2`**: close pipeline row `…-0002` with `terminal_reason`, ask the operator to kill. | ~$0.3 | pipeline edit, Tier-1 | Operator decision; no engineering. |
| **M5** | **Conviction-meta label gate.** Count closed, provenance-`MEASURED` trades available to `conviction_meta` now. | ~$0.3, read-only | note | **GO to a producer plan** only if labels ≥ ~1,000 (basis: T0.3 noise at 20 and 99; threshold is a judgement, state it before the run) **and** a stacker beats the majority baseline offline. Otherwise stays parked. |

**Spend envelope if everything passes: ~$9–10; if M2 and M5 fail (the likelier case by the evidence above): ~$5.3.** M1 is the only milestone with strong standalone evidence for its head; the other three exist to be stopped cheaply. Suggested per-lane ceilings: M0 $2, M1 $4, M2 $1, M3 $5.

## The ladder after a producer lands

Parity check (M0) on identical bars → `promote-stage` to `shadow` via the trainer relay → **observed** (not merged, not deployed): ≥1 live shadow row with every declared feature non-null and non-default → soak under the standing cost/fidelity rules → shadow → advisory is Tier-3, operator-gated. A head's soak days count only from the first row that passes that presence check.

## Open decisions for the operator

1. Kill `setup-quality-lgbm-v2` (M4).
2. Accept "park, $0" for `conviction-meta-v1` until the M5 label gate — this narrows "build all four" to "build one, test one, retire one, park one".
