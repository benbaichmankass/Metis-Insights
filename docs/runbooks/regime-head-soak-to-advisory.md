# Runbook — graduating a regime vol-head: shadow → advisory → live vol-gate

**Scope.** The deterministic sequence to take a newly-trained **regime vol-head**
(e.g. `eth-regime-15m-lgbm-v1`, `sol-regime-15m-lgbm-v1`) from `shadow` to
`advisory` so its **per-symbol** `P(volatile)` label can drive the live
Design-A vol-gate for that symbol. This is the multi-symbol follow-on to the
BTC vol-gate go-live (2026-06-28) — it generalises that exact path to ETH/SOL
and any future symbol.

**Who/when.** Tier-1 stages (0–4) are autonomous trainer research; Tier-3
stages (5–6) are operator-gated. Run it from a trainer-scoped session (or
`/ml-review`) once a head's soak has matured.

> **The one metric that matters here is RG4, not `live_agreement`.** The formal
> `python -m ml gate-check` profile blocks regime heads on `live_agreement`
> (rank-AUC of the regime-vol *score* vs trade *win*), which
> [`docs/research/promotion-gatecheck-and-mes-labeling-2026-06-26.md`](../research/promotion-gatecheck-and-mes-labeling-2026-06-26.md)
> found is **mis-targeted and sample-starved** for a regime head (a vol label
> isn't a win predictor, and there are too few closed trades per head). **RG4**
> (`scripts/ml/rg4_targeted.sh`) is the purpose-built train/serve-skew gate that
> replaces it for this decision: it asks whether the head still discriminates
> vol regime on the *logged-live* rows it actually emitted. A regime vol-head
> graduates on **RG4 ≥ 0.55 TRUSTWORTHY**, not on `live_agreement`.

---

## The pipeline

### Stage 0 — trained + registered at shadow (done at training time)
The head is trained from `ml/configs/<sym>-regime-{5m,15m}-lgbm-v1.yaml`
(mirrors the proven `btc-regime-{5m,15m}-lgbm-v2` recipe) and registers at
`target_deployment_stage: shadow`. In-session gate is **RG3 ≥ 0.55** (clean-candle
discrimination, `scripts/ml/replay_pregate_fleet.py`). A head that fails RG3 is
an honest negative — do not soak it.

### Stage 1 — SOAK (accrue live shadow rows)
At `shadow`, the per-bar scorer (`src/runtime/regime_bar_scoring.py`, gated by
`REGIME_BAR_SCORING_DISABLED`, default-on) scores the head on its `(symbol,
timeframe)` bar cadence and writes to `runtime_logs/shadow_predictions.jsonl`.
**Readiness for RG4:** a 15m head emits ~96 rows/day; target **≥ ~300–500 live
rows** before trusting RG4. The ETH session saw RG4 go knife-edge at ~111 rows
(0.46–0.58 across thresholds) — too few. More rows → a real discriminating
sample. Daily dataset builds must keep the symbol's `market_features` fresh
(`scripts/ops/build_trainer_datasets.sh` builds `<SYM> 5m/15m`) or RG4 has no
realized label to score against (the `BL-20260628-XA-TRAINING-ZERO` /
`MB-20260627-002` stale-label class of bug).

### Stage 2 — RG4 (live-row train/serve skew) — Tier-1
```bash
# Score the head's logged-live rows AT THE THRESHOLD IT TRAINED AT.
# Bybit heads train at vol_threshold=0.005 — scoring at the harness default
# 0.003 understates a real edge (MB-20260628-RG4-THRESH). Pass the match.
scripts/ml/rg4_targeted.sh <model_id> --vol-threshold 0.005
# Robustness: confirm it isn't a knife-edge across nearby thresholds.
scripts/ml/rg4_vt_sweep.sh <model_id>          # 0.003 .. 0.007
```
**Gate:** AUC **≥ 0.55 TRUSTWORTHY** (and not knife-edge across the sweep).
< 0.45 = ANTI_PREDICTIVE (demote/retire), else NO_EDGE (keep soaking or retrain
a finer timeframe — the 1h→5m/15m lesson).

### Stage 3 — per-symbol vol-split A/B — Tier-1
Re-run the cell-attribution vol-split + confirmation A/B **under the 15m head's
label** (NOT a different-timeframe head). The validated design uses the single
15m advisory head as the per-symbol vol label for *every* cell of that symbol.
```bash
python scripts/backtest_system.py --symbol <SYM> \
  --regime-router on --regime-policy <symbol trend_vol cells> \
  --vol-verdict ml --ml-stage advisory --ml-model-id <sym>-regime-15m-lgbm-v1
# compare arms: ml-gated vs frozen-vol vs ungated
```
**Gate:** ml-gated **beats frozen AND beats ungated** on net AND maxDD (the BTC
result: ML-gated 4.3× the book while trimming maxDD; the SAME cells LOSE money
under the frozen label — so the cells are correct ONLY under the ML label).
⚠️ **Re-validate any pre-drafted cells.** The ETH draft
([`docs/research/regime_policy_eth_trend_vol-2026-06-27.yaml`](../research/regime_policy_eth_trend_vol-2026-06-27.yaml))
was authored under `eth-regime-1h-lgbm-v1`'s label (the head that FAILED RG4) —
it must be re-derived under the 15m head before it can go live.

### Stage 4 — walk-forward — Tier-1
```bash
scripts/ml/walkforward_vol_gating.sh --symbol <SYM> --ml-model-id <sym>-regime-15m-lgbm-v1
python scripts/ml/walkforward_cell_selection.py --symbol <SYM>
```
**Gate:** ev-ml **≥ ungated net AND lower maxDD in every fold** (BTC passed 4/4).

### Stage 5 — author the LIVE cells — **Tier-3 (operator-gated)**
Add the symbol's `trend_vol` OFF-cells (the walk-forward survivors, re-derived
under the 15m label) to `config/regime_policy.yaml`. PR, draft, operator
approves before merge. Cells are a behavioural no-op until Stage 6.

### Stage 6 — promote the 15m head shadow → advisory — **Tier-3 (operator-gated)**
```bash
python -m ml gate-check <sym>-regime-15m-lgbm-v1        # go/no-go packet (read RG4, not live_agreement)
python -m ml promote-stage <sym>-regime-15m-lgbm-v1 advisory
```
This is the live-trading switch: it makes `ml_vol_regime_for_symbol(<SYM>)`
resolve the head's label live, so the Stage-5 cells start enforcing (the router
is already baseline-on — `REGIME_ROUTER_DISABLED` is the kill-switch). Only the
**15m** head needs advisory; the 5m head stays at shadow as a finer-grained
observer (per-symbol resolution prefers the 15m clock).

### Stage 7 — verify live
Soak the `regime_ml_vol_shadow` agreement rows and confirm
`vol_label_source=ml` resolves for `<SYM>` (diag `audit_query` event=
`regime_ml_vol_shadow`), then watch for the first `regime_hard_gate`
`enforced:true` + `vol_label_source=ml` fire on a `<SYM>` candidate.

---

## Readiness tracker

| Head | Registered | Stage | RG3 | Next gate | Blocker / note |
|---|---|---|---|---|---|
| `eth-regime-15m-lgbm-v1` | 2026-06-28 | 1 (soak) | 0.788 ✅ | post-soak RG4 (~≥300 rows) | accruing live rows; re-derive ETH cells under THIS label (the draft used the 1h head) |
| `eth-regime-5m-lgbm-v1` | 2026-06-28 | 1 (soak) | 0.770 ✅ | post-soak RG4 | finer observer; 15m is the advisory clock |
| `sol-regime-15m-lgbm-v1` | 2026-06-28 | 1 (soak) | 0.803 ✅ | post-soak RG4 | RG3 PASS (n=30k, folds 0.78–0.81 — strongest of the multi-symbol heads); accruing live rows (PR #4918) |
| `sol-regime-5m-lgbm-v1` | 2026-06-28 | 1 (soak) | TRUSTWORTHY ✅ | post-soak RG4 | finer observer; 15m is the advisory clock |

**Already live:** `btc-regime-15m-lgbm-v2` @ advisory → BTC `trend_vol` cells
enforce now (the 2026-06-28 go-live; `MB-20260628-VOLGATE-GOLIVE`).

---

## Tier summary

| Stage | Tier | Gate |
|---|---|---|
| 0 train→shadow | 1 | RG3 ≥ 0.55 |
| 1 soak | 1 | ≥ ~300–500 live rows |
| 2 RG4 | 1 | AUC ≥ 0.55 (threshold-matched, not knife-edge) |
| 3 vol-split A/B | 1 | ml-gated beats frozen AND ungated |
| 4 walk-forward | 1 | ev-ml ≥ ungated + lower maxDD per fold |
| 5 author live cells | **3** | operator approves the `regime_policy.yaml` PR |
| 6 promote 15m → advisory | **3** | operator approves; gate-check packet (RG4) |
| 7 verify live | 1 | `vol_label_source=ml` + first enforced fire |
