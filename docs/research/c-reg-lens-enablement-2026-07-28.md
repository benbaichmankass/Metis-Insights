# c_reg lens enablement — the wiring is complete; the calibrator is the only blocker

> **Tier-1 observe-only (offline draft).** The `c_reg` conviction lens feeds the
> **observe-only** unified conviction stamped on the signal meta — it is **never
> read back into the order** (`compute_conviction` output is pure logging until the
> Tier-3, backtest-gated sizing/arbitration graduation, `CONVICTION_SIZING_MODE`).
> Enabling `c_reg` changes a logged annotation, not the order path. **For operator
> review** — the one live-ship step (mirror-publish of the calibrator) is called out
> below.
>
> Adopted 2026-07-28 (Track-1 continuation). Companion of the A+B conviction
> program (`B-conviction-graduation-DESIGN-2026-06-27.md` § "c_reg enabler").

## Finding: `c_reg` is dead only for lack of a fitted calibrator

A session-level audit of the full path confirms the `c_reg` (regime-alignment)
lens is **fully wired end-to-end in code** and dead for exactly one reason: the
regime-alignment calibrator has never been fit and shipped. Nothing in the code
needs changing to bring it live — only the offline fit.

The verified chain:

| Stage | Where | State |
|---|---|---|
| Regime head is scored at signal time | `strategy_signal_builders.py` — `shadow_model_ids` omitted ⇒ every model (incl. the regime heads) auto-wires as a predictor; `capture_shadow_preds([predictor], row)` captures its score into `captured` → stamped on `sig.meta.model_scores` | ✅ live |
| Regime score is classified to the `c_reg` slot | `conviction_inputs.classify_head` — a `model_id` containing `"regime"` → `"c_reg"` (e.g. the advisory `btc-regime-15m-lgbm-fc-pcv-v1`) | ✅ live |
| Conviction is built with the regime-alignment inputs | `strategy_signal_builders.py` call site passes `regime_alignment=load_regime_alignment_cached()` **and** `direction=base_row["direction"]` to `build_conviction_inputs` | ✅ live |
| `c_reg` flows iff a calibrator exists | `build_conviction_inputs` — for the `c_reg` slot, `predict_alignment(model_cals, score, direction)` runs **only when `ra.get(model_id)` is non-empty**; absent ⇒ `_default_normalize` returns `None` ⇒ `c_reg` dropped, byte-for-byte the pre-calibrator behaviour | ⛔ **calibrator absent** |
| The calibrator artifact | `regime_alignment` section of the shared `calibrators.json`, loaded by `load_regime_alignment_cached()`; rides the trainer-mirror → live path (`runtime_logs/trainer_mirror/calibration/calibrators.json`) | ⛔ **never fit / never shipped** |
| The fitter (the "c_reg enabler") | `scripts/ml/fit_regime_alignment_calibrators.py` — fits `(regime score, direction) → P(favorable\|regime,direction)` per `(model_id, direction)` from closed non-backtest trades (`trade_journal.db::trades` JOIN `order_packages.model_scores`), stdlib-logistic by default (no sklearn), read-merge-writes the `regime_alignment` section (preserving the confidence calibrators) | ✅ exists, **never run** |

So there is **no code gap**. `c_reg` is a "dead lens" purely because the offline
fit that produces its calibrator has not been executed — the exact
"waiting on data to accrue that could be reconstructed now" phantom the research
rigor standard warns against: the corpus (regime-head scores joined to trade
outcomes) already exists in the journal, so the calibrator is fittable **today**.

## Enablement — one offline fit + one operator-gated ship

**Step 1 (trainer-autonomous, Tier-1 — offline fit).** Run the fitter on the
trainer VM (it holds the synced journal) via the `trainer-vm-diag` relay:

```bash
cd /home/ubuntu/ict-trading-bot
.venv/bin/python scripts/ml/fit_regime_alignment_calibrators.py \
    --db /home/ubuntu/ict-trading-bot/data/trade_journal.db \
    --method auto --min-rows 10 \
    --out-calibrators artifacts/calibration/calibrators.json \
    --out-report artifacts/calibration/regime_alignment_report.json
```

This merges a `regime_alignment` section into the trainer's `calibrators.json`
and emits a per-`(model_id, direction)` fit report (n_rows, win-rate spread,
method). Reading the report **verifies** the regime heads now map a score →
alignment probability — the offline "wire is live" confirmation.

**Step 2 (operator-gated — live ship).** Publish the calibrator to the live VM's
mirror (`publish_trainer_mirror.sh`, the existing calibrators path). Once mirrored,
`load_regime_alignment_cached()` picks it up (mtime-refreshed) and **`c_reg` begins
flowing into the observe-only conviction soak** on the next signals — no redeploy,
no order-path change. This step feeds the conviction score that is on the Tier-3
sizing-graduation path, so although it is observe-only it is called out for the
operator's nod rather than shipped silently.

## Why this is safe to soak (and why the ship is still operator-called)

- **Observe-only.** The conviction with `c_reg` in it is logged on the signal meta
  and feeds the `conviction_meta` dataset / the conviction soak — it is **never
  read back into the order** (`CONVICTION_SIZING_MODE=off/annotate` gates any real
  sizing influence, Tier-3, backtest-gated). Enabling `c_reg` grows the soak's
  information, not the order path.
- **Fail-permissive.** Every entry point drops `c_reg` on any error; a malformed
  calibrator or a bad score simply falls back to the pre-calibrator behaviour.
- **The ship is the boundary.** Fitting + validating the calibrator offline is the
  Tier-1 draft (this doc). Mirroring it to live begins populating a score on the
  Tier-3 sizing path, so it rides an explicit operator OK — the same discipline the
  B-conviction graduation program applies to every `c_*` lens.

## Disposition

The code wiring needs nothing. Enablement is: **(1) run the fit on the trainer
(autonomous), read the report to confirm the mapping; (2) operator approves the
mirror-publish → `c_reg` soaks observe-only.** Proposed to the operator in the
Track-1 continuation session wrap (alongside the other Tier-3 items) rather than
shipped, because Step 2 populates the Tier-3-path conviction soak.

## Step-1 fit result (2026-07-28, trainer-vm-diag #7811)

Ran `fit_regime_alignment_calibrators.py --method logistic` against the trainer's
synced live journal (`data/trade_journal.db`). **Step 1 is CONFIRMED: the fit runs
and produces calibrators — the wiring is complete, the only blocker was running
it.** It fit **8 regime heads / 11 direction-calibrators**:

| head | rows (all/long/short) | fit | method | note |
|---|---|---|---|---|
| `btc-regime-1h-lgbm-funding-svble-v1` | 18 / 18 / — | all, long | platt | borderline usable |
| `btc-regime-1h-lgbm-v2` | 18 / 18 / — | all, long | platt | borderline usable |
| `btc-regime-1h-lgbm-yz-v1` | 11 / 11 / — | all, long | platt | a=−34/b=+32 — overfit |
| `btc-regime-5m-baseline-v1` | 13 / 6 / 7 | all | platt | a=−163/b=+163 — overfit |
| `btc-regime-5m-lgbm-v2` | 13 / 6 / 7 | all | platt | overfit |
| `btc-regime-5m-lgbm-yz-v1` | 13 / 6 / 7 | all | platt | overfit |
| `eth-regime-1h-lgbm-v1` | 1 / 1 / — | all | constant rate=0.0 | degenerate (n=1) |
| `eth-regime-1h-lgbm-xasset-v1` | 1 / 1 / — | all | constant rate=0.0 | degenerate (n=1) |

**Assessment: fittable but thin.** Only the two BTC 1h heads (n=18) are borderline
trustworthy; the BTC 5m heads (n=13) show extreme platt coefficients (overfit
signatures on a tiny sample), and the ETH heads (n=1) are degenerate constants.
The corpus is the join of `order_packages.model_scores` (regime-head signal-time
scores) to *resolved closed* trades — it thickens only as more regime-head-scored
trades close.

**Implication for the Step-2 ship (operator decision).** Because publishing the
calibrator only makes `c_reg` flow into the **observe-only conviction soak** (never
the order until `CONVICTION_SIZING_MODE` graduates it, Tier-3), shipping now is
**low-risk even with a thin calibrator** — it starts accruing `c_reg` soak data and
the calibrator is re-fit as the corpus grows. Two reasonable paths:

1. **Ship now (observe-only)** + schedule a re-fit as the corpus thickens (esp.
   for the non-BTC heads). Starts the soak; the degenerate ETH/overfit-5m mappings
   are inert in the soak and get corrected on re-fit.
2. **Wait** until the corpus is less degenerate (more scored closed trades per head,
   beyond the BTC 1h pair) before the first publish.

Given the observe-only boundary, path 1 is defensible, but the early calibrator
must be treated as **provisional** (flagged here so a later session doesn't mistake
the n=1 ETH constant for a real mapping). No live sizing influence is possible
without the separate Tier-3 `CONVICTION_SIZING_MODE` graduation regardless.
