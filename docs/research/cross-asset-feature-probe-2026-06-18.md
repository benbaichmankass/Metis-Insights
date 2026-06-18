# Cross-asset feature probe — "do peer assets predict this asset?" (2026-06-18)

> **Tier-1 research.** Offline ML feature A/B on the trainer. Touches nothing
> live (`src/`, `config/strategies.yaml`, `config/accounts.yaml`). Status:
> **code shipped + trainer A/B dispatched** — results section filled on return.
>
> Origin: operator direction 2026-06-18 — *"predicting what one asset will do
> based on how other assets are performing … expanding the pool of indicators a
> strategy can look at even for trading a specific asset."* This is **step 1** of
> the research-framework build order (§5 step 4 / the cross-asset scope doc): the
> **cheap probe** — does peer-asset information add edge to a model for one asset,
> *before* any cross-asset strategy is wired live?

## 1. The question, made falsifiable

Take ETH as the target and BTC + SOL as peers. Build one model that sees only
ETH's own features, and an otherwise-identical model that also sees a block of
**peer-asset** features. If the peer block moves the holdout metric, cross-asset
information carries signal for ETH; if it doesn't, it's an honest negative and we
stop before spending a live wire on it. This is the same A/B shape that probed
the MES macro features (`mes-regime-5m-lgbm-v2` vs `-macro-v1`).

## 2. What was built (all Tier-1, offline)

| Piece | File | Role |
|---|---|---|
| Peer-asset feature transforms | `ml/datasets/cross_asset_features.py` | pure fns → the fixed `CROSS_ASSET_FEATURE_COLUMNS` block |
| Side-stream join | `ml/datasets/families/market_features.py` (`cross_asset_path` kwarg, builder v7→v8) | as-of carries the block onto the target's bars; `0.0` + non-xa columns byte-identical when omitted |
| Side-stream producer | `scripts/ml/build_cross_asset.py` | reads peer/target `market_raw` (no external fetch) → `data.jsonl` |
| A/B manifests | `ml/configs/eth-regime-1h-lgbm-v1.yaml` (base) + `…-xasset-v1.yaml` (treatment) | differ ONLY by the feature list; both read one dataset |
| Tests | `tests/ml/test_cross_asset_features.py` + `TestCrossAssetFeatures` in `tests/ml/datasets/test_market_features.py` | pure-fn correctness + wiring/default-preservation/leakage |

### The feature block (per peer slot, past-only)

Positional slots (`peer1`/`peer2`) keep the `market_features` schema fixed while
the producer's `metadata.json` records the slot→symbol map (for the probe
`peer1=BTCUSDT`, `peer2=SOLUSDT`). Per peer:

- `xa_<slot>_ret` — peer's **contemporaneous** bar log-return (co-movement).
- `xa_<slot>_ret_lag1` — peer's **previous** bar log-return — the *lead signal*
  ("what BTC just did" as a predictor of the target's next move).
- `xa_<slot>_vol` — peer's rolling log-return vol.
- `xa_<slot>_rel_strength` — target cum-return minus peer cum-return over the
  window (relative momentum).
- `xa_<slot>_beta` — rolling OLS beta of target on peer (how much of the move is
  "the market").
- `xa_<slot>_beta_residual` — `ret_target − beta·ret_peer` (the idiosyncratic,
  non-peer-explained move).

Plus cross-sectional `xa_breadth_up` — fraction of present peers up this bar.

### Cadence + leakage (the load-bearing part)

Unlike macro (daily series → intraday bars, so the producer lags one day),
crypto peers are **same-cadence** as the target — a peer 1h bar closes at the
same instant as the target 1h bar, so at the target's decision time `t` the
peer's bar-`t` close is genuinely available; contemporaneous reads are realistic,
not leakage. Every feature reads only bars `≤ t`; the `market_features` forward
label spans `[t+1 .. t+forward_window_m]` (strictly after `t`). The two windows
never overlap — leakage-safe by construction, asserted in
`TestCrossAssetFeatures.test_asof_alignment_past_only` (a side-stream stamped
entirely after the bars populates nothing).

**Default-preservation verified:** with no `cross_asset_path`, all 13 xa columns
are `0.0` AND every non-xa column is byte-identical to the pre-change build (the
A/B's only moving part is the feature list). The base manifest reads the same
cross-asset-built dataset and simply omits the columns.

## 3. The A/B (dispatched on the trainer)

```bash
# 1. market_raw for target + peers, 1h, 5y (Bybit offvm adapter)
for S in ETHUSDT BTCUSDT SOLUSDT; do
  python -m ml build-dataset market_raw --output-dir datasets-out --version v002 \
    --source bybit_v5_offvm --symbol-scope $S --timeframe 1h --overwrite \
    adapter=bybit_v5_offvm symbol=$S timeframe=1h \
    start=<5y-ago> end=<today>
done
# 2. the cross_asset side-stream (ETH ← BTC, SOL)
python -m scripts.ml.build_cross_asset \
  --target datasets-out/market_raw/ETHUSDT/1h/v002 \
  --peer datasets-out/market_raw/BTCUSDT/1h/v002 \
  --peer datasets-out/market_raw/SOLUSDT/1h/v002 \
  --out datasets-out/cross_asset/ETHUSDT/1h/v001
# 3. ETH market_features WITH the side-stream (both legs read this)
python -m ml build-dataset market_features --output-dir datasets-out --version v001 \
  --source datasets-out/market_raw/ETHUSDT/1h/v002 --symbol-scope ETHUSDT \
  --timeframe 1h --overwrite market_raw_path=datasets-out/market_raw/ETHUSDT/1h/v002 \
  cross_asset_path=datasets-out/cross_asset/ETHUSDT/1h/v001 \
  vol_window_n=20 forward_window_m=5
# 4. the A/B
python -m ml compare eth-regime-1h-lgbm-v1 eth-regime-1h-lgbm-xasset-v1
```

Read the `f1_volatile` / accuracy delta under the time-aware holdout.

## 4. Decision rule (set before seeing results)

- **Material improvement** (xasset beats base on `f1_volatile`/accuracy under the
  Phase-0 CV): green light to (a) widen the probe to a **directional / trade-
  outcome** target — the more natural home for cross-asset alpha than a vol-regime
  head — using the framework's ablation to isolate *which* xa columns carry it,
  and (b) draft a Tier-3 cross-asset strategy wire.
- **No improvement:** honest negative — log it and move on. Cross-asset alpha for
  a vol-regime head may simply be thin; the regime target is a proxy, not the
  thing we ultimately want to predict.

## 5. Results (trainer A/B)

_To be filled from the vm-driver result._
