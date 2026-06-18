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

## 5. Results (trainer A/B) — POSITIVE

Run on the trainer via `vm-driver` (`automation/results/xa-eth-probe.txt`, exit 0,
2026-06-18). 5y of 1h bars (ETH/BTC/SOL market_raw = 43,824 each; SOL listed
later so its early bars carry-forward as 0 until present), one cross_asset
side-stream (peer1=BTCUSDT, peer2=SOLUSDT; all 43,800 feature rows carry non-zero
xa values), one ETH `market_features` dataset both legs read. Time-aware holdout =
the last 20% (n_eval = 8,760 = exactly one year of 1h bars).

| metric | base (v1) | +cross-asset (xasset-v1) | Δ |
|---|---|---|---|
| accuracy | 0.5727 | 0.6114 | **+0.0387** |
| macro_f1 | 0.5673 | 0.6113 | **+0.0440** |
| weighted_f1 | 0.5560 | 0.6095 | **+0.0535** |
| f1_range | 0.5188 | 0.6038 | **+0.0850** |
| f1_volatile | 0.6157 | 0.6187 | +0.0030 |
| precision_volatile | 0.4701 | 0.4961 | +0.0261 |
| recall_range | 0.3738 | 0.4805 | +0.1067 |
| recall_volatile | 0.8923 | 0.8218 | −0.0705 |

**Read:** cross-asset features add edge to the ETH regime head — broadly and
materially (every aggregate metric up; +5.4 weighted-f1). The base over-commits
to *volatile* (recall_volatile 0.89, recall_range 0.37 — it rarely calls a calm
bar); the cross-asset model **rebalances** (recall_range 0.37→0.48, recall_volatile
0.89→0.82), so the lift is concentrated in the **range (calm) regime** (+0.085
f1_range). That is the intuitive direction: when BTC/SOL are quiet and ETH is
co-moving calmly, ETH is more reliably in range — and the peer block is what lets
the model see it. It is *not* a leakage artifact (clean isolation: the ONLY change
between the two runs is the feature list; the side-stream is past-only).

**Verdict:** GREEN by the §4 decision rule. Cross-asset information carries
predictive signal for an ETH state head. Caveats kept honest: (1) a single
time-aware holdout, not purged-WF-CV or multi-fold — the magnitude wants
corroboration under the leak-free splitter before any strong claim
(**done — see § 5.1**); (2) this is a **vol-regime proxy**, not a directional/PnL
target — "improves a regime classifier" is the green light to invest in the
directional probe, not yet "tradeable edge."

### 5.1 Corroboration under purged-WF-CV — CONFIRMED

Re-ran the identical A/B with the leak-free splitter (`automation/results/xa-eth-pwf.txt`,
exit 0): `split_strategy: purged_walk_forward`, 5 folds, `embargo_n=10`,
`min_train_fraction=0.5` — pooled `n_eval = 21,900` across folds. The lift
**survives**, same direction, same concentration in the range regime:

| metric | base (v1) | +cross-asset | Δ (pwf) | Δ (single holdout, § 5) |
|---|---|---|---|---|
| weighted_f1 | 0.5974 | 0.6233 | **+0.0259** | +0.0535 |
| accuracy | 0.6058 | 0.6265 | +0.0206 | +0.0387 |
| macro_f1 | 0.5968 | 0.6182 | +0.0215 | +0.0440 |
| f1_range | 0.5742 | 0.6175 | **+0.0433** | +0.0850 |
| recall_range | 0.4580 | 0.5204 | +0.0624 | +0.1067 |
| f1_volatile | 0.6193 | 0.6189 | −0.0004 | +0.0030 |

The magnitude is roughly half the single-holdout's (expected — the § 5 holdout was
a favorable recent year; the 5-fold pooled estimate is the honest one) but
**unambiguously positive and leak-free**. f1_volatile is flat under BOTH splits —
the cross-asset block does not help ETH detect *volatility*; it helps ETH
recognize *calm/range* (the entire edge is in range recall + f1). The
single-holdout caveat is resolved; the vol-regime-proxy caveat stands (the
directional target is § 6 step 3).

### 5.2 Ablation — which columns carry the edge (purged-WF-CV)

Same leak-free split, the xasset feature group ablated one sub-block at a time
(`automation/results/xa-eth-ablation.txt`, exit 0). base weighted-f1 = 0.5974,
full = 0.6233 (total edge **+0.0259**).

| variant | xa cols | weighted_f1 | f1_range | recall_range |
|---|---|---|---|---|
| **full** (all) | 13 | 0.6233 | 0.6175 | 0.5204 |
| base (none) | 0 | 0.5974 | 0.5742 | 0.4580 |
| drop SOL peer | 7 | 0.6046 | 0.5901 | 0.4824 |
| drop rel_strength | 11 | 0.6128 | 0.6024 | 0.5026 |
| drop beta+residual | 9 | 0.6133 | 0.6002 | 0.4945 |
| drop BTC peer | 7 | 0.6175 | 0.6074 | 0.5071 |
| drop lead-lag | 11 | 0.6211 | 0.6134 | 0.5142 |
| keep co-move only | 3 | 0.6032 | 0.5838 | 0.4708 |
| keep lead-lag only | 3 | 0.6016 | 0.5803 | 0.4663 |

**Edge lost when a sub-block is dropped** (full − variant): **SOL peer −0.0187
(≈72% of the edge!)**, rel_strength −0.0105, beta+residual −0.0100, BTC peer
−0.0058, lead-lag −0.0022 (negligible).

**Read:** the edge is broad-based but **SOL is the load-bearing peer** — a
higher-beta alt that exaggerates/leads ETH's regime, so its presence carries most
of the lift; **relative-strength + beta/residual** (the *relative / idiosyncratic*
geometry) matter more than raw co-movement, and the **lead-lag** ("what peers did
one bar ago") is nearly worthless — it is the *contemporaneous* relative structure
that predicts ETH's state. Design steer for the directional follow-up: **keep SOL
+ rel_strength + beta; drop lead-lag**; a richer/closer-correlated peer set
(more alts) is worth trying since SOL >> BTC here.

## 5.3 Directional probe (step 3) — WEAK-POSITIVE

Operator greenlit widening from the regime *proxy* to the decision-relevant
target: predict ETH's forward **direction** (`direction_label` = sign of the
5-bar forward return; class balance up 22,110 / down 21,662 / flat 28 ≈ 50/50).
3-way A/B under purged-WF-CV (`automation/results/xa-eth-direction.txt`, exit 0,
n_eval 21,900 pooled, 5 folds):

| model | features | accuracy | weighted_f1 | macro_f1 |
|---|---|---|---|---|
| base | ETH own | 0.5056 | 0.5053 | 0.3369 |
| **+ full cross-asset** | own + 13 xa | **0.5153** | 0.5145 | 0.3431 |
| + lean | own + SOL/rel-str/beta (9 xa) | 0.5112 | 0.5101 | 0.3401 |

Δ (full − base): accuracy **+0.0097**, weighted_f1 +0.0092, symmetric across both
classes (precision_up +0.010, precision_down +0.010, recall_up +0.008,
recall_down +0.012).

**Read — weak-positive.** The own-features base is a **coin flip** (50.6% — ETH's
own 1h features barely predict its own 5-bar forward sign). Cross-asset lifts it
to **51.5%**, leak-free and directionally symmetric — ~2.9 std-errors
(SE≈0.34pp at p=0.5, n=21,900), so real but small. The full block beats the
ablation-lean here (51.5 > 51.1), so for *direction* the wider block earns its
keep (unlike the regime target where lean was competitive).

**The honest caveat that gates the next step:** +0.97pp is **far weaker than the
regime lift** (+4.3pp f1_range, § 5.1–5.2). Peer information strongly conditions
ETH's *state* (calm/range) but only faintly predicts its *direction* at this
horizon. And accuracy is a **classifier metric, not a PnL edge** — a 51.5%
sign-accuracy ignores move *size* and transaction costs; being right 51.5% of the
time loses money if the 48.5% wrong moves are bigger or fees eat the edge.

## 7. Strategic read + recommendation

The two probes together draw a clear, coherent line:

- **Cross-asset is a strong REGIME conditioner** (+4.3pp f1_range, leak-free) —
  peer state reliably tells you *what kind of market ETH is in*.
- **Cross-asset is a weak DIRECTIONAL signal** (+0.97pp sign-accuracy over a
  coin-flip base) — it barely tells you *which way ETH goes next*.

That points away from a standalone "cross-asset directional strategy" and toward
using the cross-asset signal as a **regime / confidence / sizing lens** — exactly
the M16 unified-confidence direction (a conditioner that advises sizing &
arbitration, not a standalone entry signal). The recommended next move is
therefore NOT a Tier-3 directional wire on a 1pp edge (too thin to deploy on
faith), but one of:

1. **Convert-to-PnL gate (cheap, decisive):** turn the directional signal into
   entries and run it through the net-of-fee backtest + readiness ladder — does
   the 51.5% sign-edge survive costs as positive expectancy? If yes, *that* is the
   tradeable result; if no, the directional angle is closed honestly.
2. **Regime-conditioner wire (the stronger signal):** feed the validated
   cross-asset *regime* read into the confidence/sizing path (M16), where a +4.3pp
   regime-separation lift is the kind of edge that improves sizing decisions
   without needing standalone directional alpha.

Both are follow-ups for operator steer; neither is auto-fired.

## 8. Original follow-up checklist (status)

1. ✅ **Corroborate** under purged-WF-CV — done (§ 5.1): lift survives leak-free.
2. ✅ **Ablate** which xa columns carry it — done (§ 5.2): SOL + rel-strength/beta.
3. ✅ **Widen to a directional target** — done (§ 5.3): weak-positive (+0.97pp).
4. ⏸ **Tier-3 wire** — NOT recommended on the 1pp directional edge; superseded by
   the § 7 recommendation (convert-to-PnL gate, or regime-conditioner into M16).
   Operator-gated either way.
