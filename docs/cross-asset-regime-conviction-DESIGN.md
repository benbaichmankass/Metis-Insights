# Cross-asset regime → conviction `c_reg` lens (DESIGN, 2026-06-18)

> **Status: DESIGN — for operator review before any live-path code.** The probe
> work (S-CROSS-ASSET-PROBE) is Tier-1 and shipped; THIS doc proposes wiring its
> validated signal into the live conviction blend, which touches the trader's
> feature-computation + shadow-predict path (Tier-2) and needs a model promotion
> (Tier-3). Nothing here is built yet.
>
> Origin: operator decision 2026-06-18 — after the probe showed cross-asset is a
> *strong regime conditioner / weak directional signal*, "**take it as the
> regime / confidence / sizing lens (M16), not a standalone directional
> strategy.**"

## 1. What the probe established (the input to this design)

Two leak-free A/Bs (`docs/research/cross-asset-feature-probe-2026-06-18.md`):

- **Regime (state) — strong, confirmed.** Peer-asset features (BTC/SOL) lift an
  ETH regime classifier under purged-WF-CV: weighted-f1 +0.026, **f1_range
  +0.043**, recall_range +0.062. The edge is the *contemporaneous relative
  geometry* — SOL peer + relative-strength + beta carry it; lead-lag is inert.
- **Direction — weak.** +0.97pp sign-accuracy over a coin-flip base; real but far
  thinner.

Conclusion: cross-asset belongs as a **regime/sizing conditioner**, not a
standalone entry signal. This doc routes it into the place the architecture
already reserves for exactly that.

## 2. The socket already exists — `c_reg`

The M16 unified-confidence blend (`src/runtime/conviction.py`) is already:

```
conviction = m_news × ( 0.45·c_strat + 0.20·c_setup + 0.20·c_wr + 0.15·c_reg )
```

where **`c_reg` = "P(favorable regime)"** — the regime-alignment lens (weight
0.15). And `conviction_inputs.classify_head` already maps any model_id
containing `"regime"` → the `c_reg` slot. **But `c_reg` is currently inert:**
`conviction_inputs._default_normalize` returns `None` for it —

> *"a single stored scalar is not a usable regime-alignment probability without
> the class vector — skip in v1 unless a head calibrator is provided."*

So today the regime lens contributes nothing; the blend renormalizes over the
other three. This design **fills that empty socket** with the cross-asset regime
read — the probe's strongest, validated signal. No new lens, no new weight, no
blend-shape change: we make an already-declared input real.

## 3. Proposal — three pieces, each independently gated

### 3.1 The model (Tier-3 promotion gate)

`eth-regime-1h-lgbm-xasset-v1` (the validated cross-asset regime head) is
promoted `candidate → shadow` so it scores live and logs predictions (the WS7
shadow path). **Promotion past candidate is the operator gate** (VM authority
split § promotion gate). At `shadow` it influences nothing — it only accrues a
live track record, exactly as every other shadow model does.

A generalization note: the probe is ETH-specific (peer1=BTC, peer2=SOL). For a
multi-symbol regime lens, one cross-asset regime head per traded symbol (its
peers configured), or a single joint head with `symbol` as a feature (the xsym
precedent). v1 scope = ETH only (where we have the validated result).

### 3.2 Live cross-asset feature computation (the real new wiring — Tier-2)

For the shadow predictor to score `eth-regime-1h-lgbm-xasset-v1` live, the
trader must compute the `xa_*` feature block at tick time. New module
`src/runtime/cross_asset_live.py`:

- For a target symbol, read its configured peers (a small `config/cross_asset.yaml`:
  `ETHUSDT: [BTCUSDT, SOLUSDT]`), fetch each peer's recent candles via the
  **existing** `connector_for_symbol` + `fetch_candles` path (the same fetcher
  the candles endpoint + signal builders use), and compute the `xa_*` block by
  reusing the **pure** `ml/datasets/cross_asset_features.compute_cross_asset_feature_rows`
  (no new math — the exact function the offline dataset uses, so live == train).
- The peer fetch is **one extra `(symbol, timeframe)` fetch per scored bar**,
  bounded by the same fetch-gate + per-tick budget pattern as
  `regime_bar_scoring.py` (`_BAR_SECONDS − buffer`, `REGIME_BAR_SCORING_BUDGET_S`)
  — a 1h peer set is fetched ~1×/hour, never per tick, so the cost is negligible
  and cannot wedge the loop (the BL-20260609 cold-start lesson is carried
  forward: cap the call, defer whole groups).
- Fail-permissive: any peer-fetch/compute error → the xa block is the
  default-zero vector (exactly the offline `cross_asset_path=None` behaviour), so
  the regime head still scores (degraded to own-features) and the trader never
  stalls on a peer feed.

This is the load-bearing Tier-2 change — it adds a peer fetch to the live
feature path. It is observe-only (feeds only the shadow predictor) and
kill-switched (`CROSS_ASSET_LIVE_DISABLED`), but it touches the trader, so it's
operator-acked before deploy.

### 3.3 The `c_reg` mapping + calibrator (Tier-1 offline)

Make `c_reg` real: a regime-alignment function `regime_alignment(regime_probs,
direction) → P(favorable regime) ∈ [0,1]` (e.g. P(range) for a mean-revert
setup, P(trending/volatile) for a momentum setup — the alignment rule is per
strategy family, a small table). This needs the head's **class-probability
vector**, not the stored scalar — so the shadow-predict capture must persist the
regime class probs (a small extension to the model-scores capture for regime
heads), and `conviction_inputs._default_normalize` for `c_reg` is replaced by
this alignment map (+ an optional fitted calibrator, like the other lenses,
fit offline by `fit_confidence_calibrators.py`). All Tier-1 / offline.

## 4. Rollout (mirrors the M16 phases + the regime-bar-scoring soak)

| Phase | Scope | Gate | Status |
|---|---|---|---|
| **D1** | This design, operator review | — | ✅ approved 2026-06-18 ("that's a go") |
| **D2a** | `config/cross_asset.yaml` + `src/runtime/cross_asset_live.py` + wiring into the **per-bar regime scorer** (`regime_bar_scoring.py`) so the cross-asset regime head scores with correct live `xa_*` features → `shadow_predictions.jsonl`. Observe-only, kill-switched (`CROSS_ASSET_LIVE_DISABLED`), fail-permissive (peer error → NaN xa, never fabricated zeros), peers ride the target's gated fetch cadence. Reuses the offline pure fns (live==train). | Tier-2 (touches live feature path; observe-only) | ✅ BUILT 2026-06-18 (this PR; 19 tests, ruff clean). Needs: trainer registers + promotes `eth-regime-1h-lgbm-xasset-v1` to `shadow`, then live deploy. |
| **D2b** | Feed `xa_*` at **signal time** too (`strategy_signal_builders` shadow capture — needs a bounded peer fetch on the signal path) + expose the regime **class-probability vector** from the predictor + the `c_reg` `regime_alignment(probs, dir)` map/calibrator in `conviction_inputs.py` (so `c_reg` stops being skipped). Still observe-only — feeds the already-soaking `meta.conviction`, never the order. | Tier-2 + Tier-3 promotion | ⏳ next |
| **D3** | Soak: `c_reg` now contributes to the observe-only conviction; verify via the conviction soak log (`conviction_sizing`) that the cross-asset-fed `c_reg` is populated, sane, and moves with regime. No influence. | soak accrues | — |
| **D4** | Graduate `c_reg` into the **influencing** conviction (sizing) — only after the M16 P2+ gate (backtest of conviction-sized vs flat) AND the cross-asset `c_reg` is shown to improve it. | Tier-3, operator + backtest | — |

**D2a → D2b split (why):** the predictor's `predict()` returns a single scalar;
a real `c_reg` needs the regime **class-probability vector** — a deeper
predictor-interface change. D2a ships the safe, isolated piece (the per-bar scorer
soak with correct features) so the cross-asset head accrues a clean live track
record now; D2b does the prob-vector + signal-time + `c_reg` wiring. Until D2b,
the signal-time path scores the head with NaN `xa_*` (LightGBM handles missing) —
degraded but safe, and distinguishable in the shadow log (`event_source` ≠
`per_bar`); the per-bar scorer is the clean soak source.

D4 is the same operator/backtest gate as all M16 live influence — this design
does **not** front-run it; it gets the validated regime signal *soaking* in the
right socket so the P2+ decision has real data.

## 5. Why this and not a standalone strategy

A standalone cross-asset *directional* strategy would lean on the +0.97pp
directional edge — too thin to deploy. The regime signal is +4.3pp and
validated; as a **sizing lens** it improves decisions already being made (size
up when ETH's regime is favorable & the peer complex agrees, down when not)
without needing to *originate* a trade. It also reuses the entire M16 machinery
(the blend, the soak, the calibration loop, the arbitration) instead of building
a parallel path — the smallest, safest way to convert the finding into live value.

## 6. Open decisions for the operator

1. **Scope:** ETH-only v1 (validated) vs build the per-symbol/joint regime lens
   now. Recommend ETH-only first — prove the live wiring + soak on the one cell
   we have evidence for.
2. **Peer config:** `config/cross_asset.yaml` `ETHUSDT: [BTCUSDT, SOLUSDT]`
   (ablation says SOL is load-bearing; BTC adds a little). Add more alts?
3. **Alignment rule:** the per-family `regime_alignment` table (which regime is
   "favorable" for which strategy family) — confirm the mapping before D2.
4. **Go/no-go on D2** — the only step that touches the live trader.
