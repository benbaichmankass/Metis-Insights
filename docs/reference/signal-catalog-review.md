# Signal catalog — review (unused, redundant, unmeasured, candidate additions)

> **Doc status:** `unknown` · category `lookup` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Companion to [`signal-catalog.yaml`](signal-catalog.yaml) (guard:
`scripts/ci/check_signal_catalog.py`). Extends
[`signal-research-framework-DESIGN.md`](../research/signal-research-framework-DESIGN.md),
[`technical-signal-research-ledger.md`](../research/technical-signal-research-ledger.md) and
[`M28-signal-research-ledger.md`](../research/M28-signal-research-ledger.md).
**Population:** static code/config inventory on main, 2026-10-08; nothing here was
measured against the VM, the journal or live APIs. Every item is a research
QUESTION for the research-planning session — none is a queue unit, a finding of
fact about live behaviour, or a recommendation to change a leg.

## A. Unused or unconsumed

1. Do `xa_peer1_present`, `xa_peer2_present` and `xa_breadth_present` (emitted by
   `ml/datasets/cross_asset_features.py`, used by no manifest) change any
   cross-asset head's skill once a zero-filled peer slot can be told apart from a true 0?
2. Do the 32 `tsfm_emb_*` and 16 `corpus_emb_*` columns, used only by retired
   manifests, have any residual value, or should the builders be retired?
3. `hf_displacement_cont` and `hf_vwap_revert` appear in no strategy/account config
   and no signal builder: is there a harness result that justifies wiring either, or
   are they dead modules?
4. `config/macro_events.yaml` / `event_calendar.py` have no reader in `src/` or
   `scripts/` outside `macro_thesis/` and the calendar producers (grep, 2026-10-08):
   which decision is the event calendar meant to feed?
5. `crowding_read` is read by `thesis_conditioned` and one offline script only; does
   the M28 ledger (rows 2, 5, 7: COT, no deployable edge) argue for retiring it?

## B. Redundant

6. Four range-vol estimators (`parkinson_vol`, `garman_klass_vol`,
   `rogers_satchell_vol`, `yang_zhang_vol`) sit beside `rolling_log_return_vol`: how
   much independent information does each add to the regime head, by purged-CV
   ablation?
7. Two regime classifiers coexist: ADX-14 chop/transitional/trending
   (`src/runtime/regime/detector.py`, the router) and the trend%/range-bps
   `classify_regime` (`src/units/strategies/regime.py`, feeding `vwap_policy`). On
   the same bars, how often do they disagree, and does disagreement predict
   per-leg expectancy?
8. Do `funding_rate`, `funding_rate_zscore` and `funding_rate_abs_z` carry distinct
   information, given M28 entries 3, 9–11 found no funding edge on its own?

## C. Unmeasured (no per-input health metric exists today)

9. Candles, funding/OI, peer candles and order-book inputs have no per-input
   null-rate or staleness metric (only forecasts expose `fc_served`/`fc_stale`;
   macro and L2 capture have workflow-only liveness). What is the live null/stale
   rate of each input family, and is a single served route the right surface?
10. `funding_rate*`, `open_interest_change*`, `vpin`, `ofi*`, `rel_spread_mean`,
    `microprice_dev`, `vix_*`, `dxy_*`, `ust_*` appear in trained manifests, and a grep
    of `src/` finds no live computation for them (2026-10-08). Are those heads
    scored live with these features absent, neutral-filled, or not at all? (Mode
    reconciliation against `ml/promotion/live_parity.py`.)
11. `ml/datasets/audit.py` dead-column findings go to `dataset_audit.jsonl` and are
    not served. Would serving them change any promotion decision?
12. `config/economic_calendar.yaml`, the live news-influence input, has its last
    event at 2026-07-30 and no events for several classes: what is the cost of
    news-risk gating on a calendar that has run dry?
13. The `ict_scalp` and `vwap` HTF filters fail open (inventory finding): how often
    does the fail-open branch fire live, and what is the expectancy of those trades?
14. `pairs_executor` aligns the two legs by tail position, not timestamp, and a
    candle-fetch failure skips the tick with no log row: what is the realised
    misalignment rate?
15. Entry/exit-head feature lists live in VM artifact JSON, not git
    (`unguarded_surfaces`): can they be exported into the repo so the guard covers
    the 25 names?

## D. Candidate additions

16. Does open-interest delta, as a gate on trend legs, add net-of-cost edge over the
    ADX-14 gate alone?
17. Does perp-vs-spot basis add information to the crowding-fade construction that
    funding level alone lacked (M28 rows 3, 10, 11 are no-edge for funding)?
18. Does realised-vs-implied vol (VIX term slope is already a feature) predict
    breakout-leg failure better than the existing vol buckets?
19. Does session/time-of-day structure beyond `hour_of_day`/`dayofweek` (killzone
    flags exist in conviction meta) explain scalp-leg variance?
20. Does a liquidation-cluster or large-trade-imbalance proxy from the existing L2
    capture predict reversals at the sweep level `ict_scalp` already detects?
