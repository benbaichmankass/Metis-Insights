# Design-A multi-symbol (#1) — ETH confirmation (2026-06-27)

The BTC vol-gate (Design-A) generalizes to **ETHUSDT**, decisively. This is the
first cross-symbol confirmation that the ML vol-verdict gate is not a BTC
artifact.

## Enabler

`scripts/backtest_system.py` was hardcoded to BTCUSDT; this session generalized
it to `--symbol` (default BTCUSDT → byte-identical BTC behavior, all 9 evidence
tests pass) + a per-symbol roster mapping the live `*_eth`/`*_sol` strategy names
to their shared logic modules (`trend_donchian` / `htf_pullback_trend_2h`).

## ETH vol-split (trainer-vm-diag #4849/#4851)

`--symbol ETHUSDT --data data/ETHUSDT_5m.csv --roster trend_donchian_eth,
trend_donchian_eth_4h,eth_pullback_2h --clock-tf 1h --vol-verdict ml
--ml-model-id eth-regime-1h-lgbm-v1`. The ETH head resolved **live in the
harness** (`available=True scored=3007 fell_back_to_frozen=0`).

Ungated ETH book: **net $63 / maxDD $1691 (15%) / 1016 trades** — marginal, large
drawdown. Per-cell decomposition (worst-first, meaningful sample):

| cell | net $ | trades |
|---|---:|---:|
| **trend_donchian_eth \| trending \| volatile \| long** | **−968** | 175 |
| trend_donchian_eth \| transitional \| volatile \| long | −383 | 84 |
| eth_pullback_2h \| trending \| calm \| short | −260 | 22 |
| trend_donchian_eth \| chop \| volatile \| short | −169 | 45 |
| trend_donchian_eth \| chop \| calm \| short | −168 | 18 |
| trend_donchian_eth \| trending \| calm \| short | −138 | 13 |
| eth_pullback_2h \| trending \| volatile \| short | −117 | 81 |
| trend_donchian_eth \| chop \| volatile \| long | −50 | 61 |
| trend_donchian_eth_4h \| trending \| volatile \| short | −32 | 17 |
| … | | |
| **trend_donchian_eth \| trending \| volatile \| SHORT** | **+433** | 193 |
| eth_pullback_2h \| trending \| calm \| long | +464 | 13 |

**The dominant ETH loser is `trend_donchian_eth | trending | volatile | long`
(−$968/175t) — the SAME vol-conditioned pattern as BTC** (`trend_donchian|trending|
volatile|long` was −$224 there). A Donchian long in a *volatile* "trend" is a
false-breakout trap on both symbols; the SHORT side of that same cell *wins*
(+$433/193t on ETH). The cells are authored in
`docs/research/regime_policy_eth_trend_vol-2026-06-27.yaml` (9 OFF-sides,
meaningful ≥10t net-negatives; the marginal −$49/15t `trending|calm|long` left ON
per the strong BTC prior that calm-trend-long is the winner).

## Confirmation A/B (trainer-vm-diag #4852/#4854)

| arm | net $ | maxDD $ | ret/DD | trades |
|---|---:|---:|---:|---:|
| ungated | 63 | 1691 (15%) | 0.04 | 1016 |
| **ev-ml-gated** | **2336 (23%)** | **1270 (10%)** | **1.84** | 669 |

**Gating the evidence cells lifts the ETH book net $63 → $2336 AND cuts maxDD
25% ($1691 → $1270)** — ret/DD 0.04 → 1.84 — by removing ~347 net-negative trades.
The lift is *larger* than BTC's (37× vs 4.3×) because the ungated ETH base was
near break-even, so the losing sleeves dominated it. Same mechanism, second
symbol → **the vol-gate generalizes cross-symbol.**

## Cell-selection walk-forward (the strict OOS test — DONE, trainer #4857/#4861)

`scripts/ml/walkforward_cell_selection.py --symbol ETHUSDT` re-derives the ETH
OFF-cells from each in-sample window and applies them OOS (expanding window):

| OOS fold | ungated net / maxDD | ev-ml net / maxDD | net | maxDD |
|---|---:|---:|:--:|:--:|
| 2023-07 → 2024-07 | $126 / $773 | −$76 / $453 | **FAIL** | PASS |
| 2024-07 → 2025-07 | −$387 / $848 | $412 / $403 | PASS (+$799) | PASS |
| 2025-07 → 2026-06 | $612 / $835 | $751 / $432 | PASS (+$139) | PASS |

**Honest mixed result — weaker than BTC's clean 3/3+3/3:**
- **maxDD: PASS 3/3** — the cells reliably ~halve drawdown out-of-sample every
  fold. The de-risking generalizes.
- **net: 2/3** — the *earliest* fold FAILS (gating hurt net, −$76 vs +$126); its
  cells were authored from a thin <2023-07 window (460 trades) and the broader
  cell set (short-side + chop cells) didn't generalize. The other two folds pass,
  one a large rescue (−$387 → $412).
- **The CORE cell generalizes:** `trend_donchian_eth|trending|volatile|long` is
  net-negative in **all three** in-sample windows (−$211 / −$355 / −$655) — the
  load-bearing volatile-long sleeve is robust; the noise is in the marginal
  short/chop cells the thin early window over-selected.

**Verdict:** the in-sample $2336 was optimistic. The realistic OOS read is "ETH
multi-symbol A reliably **cuts drawdown** and is **net-positive in 2 of 3**
windows, anchored by the robust volatile-long cell." Worth pursuing — primarily
for the drawdown benefit + the strong core cell — but NOT a slam-dunk like BTC; a
live ETH cell set should likely be **conservative** (the robust core cell ±
the largest-sample losers), not the full 9-cell in-sample set.

## Labeling-gap fix + the RG4 live verdict (DONE, trainer #4865/#4866)

Root-caused the MES/ETH live-labeling gap: `scripts/ops/build_trainer_datasets.sh`
rebuilt `market_raw`/`market_features` for **BTCUSDT only**, so the alt regime
heads' realized-label datasets (built once, ETH 06-17) perpetually went stale →
RG4's label join never covered the live shadow rows. **Fixed** (`build_bybit_pair`
+ ETH/SOL added to the daily loop, commit 7a051e5). Validated: refreshed the ETH
dataset → ETH RG4 unlabeled **353/353 → 6**. Gap closed, durably.

**But the fix revealed the decisive finding — the ETH head FAILS RG4 live:**

| head | RG3 offline | RG4 live (post-fix) | verdict |
|---|---:|---:|---|
| eth-regime-1h-lgbm-v1 | 0.73 | **0.46** (111 labeled) | **NO_EDGE** |
| eth-regime-1h-lgbm-xasset-v1 | 0.70 | **0.46** (347 labeled) | **NO_EDGE** |

The ETH head discriminates the vol regime **offline / on clean harness candles**
(RG3 0.70-0.73 → drove the backtest A/B), but its **actual live logged predictions
do not** (RG4 ~0.46 ≈ random, on a non-trivial 347-row sample). That is
train/serve skew — the exact failure RG4 exists to catch.

**Consequence (honest revision):** the backtest A/B ($63 → $2336) is **optimistic
for LIVE** — it scored the head on clean candles where it works; live, the head
would feed the order path a ~random vol label, not the backtest's labels. **ETH
multi-symbol A is NOT live-ready.** RG4 prevented a bad promotion.

## Honest caveats / what's needed for ETH to go live

1. **The ETH head must clear RG4 first.** It's NO_EDGE live (0.46) — needs
   retraining / live-feature-parity investigation (why does it discriminate
   offline but not on the logged live rows? — same skew class as the BTC `yz`
   heads). Until a retrained ETH head passes RG4, no advisory promotion → no live
   ETH cells, regardless of how good the backtest looks.
2. **net only 2/3 OOS** (cell-selection WF above) — even with a good head, a live
   ETH cell set should be conservative (core cell + biggest losers).
3. The labeling-gap fix is the lasting win: ANY alt head can now be RG4-validated
   each cycle — the gate works, and it's already separating the wheat (BTC head,
   RG4 0.72) from the chaff (ETH head, RG4 0.46).
2. **Live promotion blocked on the labeling gap.** Taking ETH live needs
   `eth-regime-1h-lgbm-v1` at **advisory**, which needs an RG4 live-row pass —
   currently UNSCOREABLE because every live ETH regime row is unlabeled
   (`MB-20260627-002` / `MB-20260626-001` #1). Fix the MES/ETH live-labeling gap
   → RG4 can judge → promotion → live ETH cells (Tier-3).
3. Single backtest pass, one alt-symbol. SOL is the next candidate (head needs
   training; data present).

---

# RG4 retrain session — 2026-06-28 (MB-20260627-003)

Picked up the open MB-20260627-003 ("ETH head NO_EDGE live; needs retrain") and
root-caused the offline-good / live-bad skew, then trained finer-timeframe
replacements. The headline revises the 2026-06-27 read in two ways: the 0.46
"NO_EDGE" was **partly an RG4 harness threshold artifact**, and the real fix is
the **5m/15m timeframe**, not the 1h.

## 1. Root cause — why the live score doesn't discriminate

Pulled the EXACT logged-live feature rows from `shadow_predictions.jsonl` and
compared them, per feature, to the training `market_features` distribution
(`scripts/ml/_feature_parity_probe.py`, trainer-diag #4869). Both 1h heads emit
a **near-constant high P(volatile) on live rows** — they call "volatile" on
essentially every bar, so they cannot discriminate:

| head | live rows | predicted P(volatile) mean / min | live vol_bucket |
|---|---:|---|---|
| eth-regime-1h-lgbm-v1 | 117 | **0.776 / 0.508** | 73% in top `vol_b2` |
| eth-regime-1h-lgbm-xasset-v1 | 353 | **0.758 / 0.500** | balanced |

Two distinct drivers:
- **v1**: live `rolling_log_return_vol` runs **+51%** above the 5-yr training
  mean (0.00955 vs 0.00633) and compressed, pushing 73% of live rows into the
  top vol bucket — which the model maps to volatile. (Partly a genuinely
  higher-vol recent window; the 1h head has too few live bars — one score/hour —
  for the recent sample to look like the 5-yr training mix.)
- **xasset**: `xa_breadth_up` (the cross-asset breadth feature) is **all-zeros
  in the training dataset** (mean 0, std 0 over 43.8k rows) but **~0.45 live** —
  the cross_asset side-stream was dead at train time, so the head trained on dead
  peer features and sees live noise. A real, separate dataset-build bug
  (`BL-20260628-XA-TRAINING-ZERO`).

## 2. The RG4 harness threshold mismatch (recalibration)

RG4 (`replay_pregate_live.py`) **defaults `vol_threshold=0.003`**, but the Bybit
dataset build (`build_trainer_datasets.sh::build_bybit_pair`) labels
`regime_label` at **`vol_threshold=0.005`**. RG4 must score a head against the
SAME label definition it trained on; the 0.003 default mis-scored every Bybit
head. Re-ran RG4 across thresholds (trainer-diag #4892/#4893):

| head | rows | 0.003 | 0.004 | **0.005 (matched)** | 0.006 | 0.007 |
|---|---:|---:|---:|---:|---:|---:|
| eth-regime-1h-lgbm-v1 | 111 | 0.46 | 0.51 | **0.58** | 0.48 | 0.47 |
| eth-regime-1h-lgbm-xasset-v1 | 347 | 0.46 | — | **0.51** | — | 0.46 |
| btc-regime-1h-lgbm-v2 | 851 | 0.62 | — | **0.58** | — | — |
| btc-regime-5m-lgbm-v2 | 11704 | 0.79 | — | **0.83** | — | — |
| btc-regime-15m-lgbm-v2 | 2352 | 0.73 | — | **0.71** | — | — |

Two honest reads:
- **The mismatch is real** (BTC 5m shifts 0.79→0.83) and the harness should score
  at the dataset's threshold. Logged as a tooling fix (`MB-20260628-RG4-THRESH`).
- **But it does NOT rescue ETH 1h.** The matched-0.005 0.58 is a **knife-edge
  spike** — every neighbouring threshold sits at 0.46–0.51 on the thin 111-row
  sample. Contrast the BTC heads, which clear the bar robustly at BOTH thresholds
  on much larger samples. So the corrected verdict for the ETH 1h base head is
  **weak / borderline live, NOT live-ready** — the original "needs a better head"
  conclusion stands; only its severity ("0.46 random") was overstated by the
  harness threshold. The xasset head is flatly NO_EDGE at every threshold
  (the xa bug).

## 3. The fix — ETH 5m + 15m heads (the strong-timeframe path)

The 1h ETH head being weak mirrors the BTC 1h head (RG4 0.58–0.62, WATCH-tier)
while the BTC **5m/15m** heads pass robustly (0.71–0.83). The 1h regime family is
the weak timeframe — too few live observations and a vol-saturated output. So
trained the proven BTC 5m/15m recipe on ETH:

- `eth-regime-5m-lgbm-v1` + `eth-regime-15m-lgbm-v1` (identical to
  `btc-regime-{5m,15m}-lgbm-v2` except symbol). Built the ETH 5m/15m
  `market_features` datasets first (525,864 / 175,272 rows, 5 yr; trainer-diag
  #4870/#4871) and added ETH 5m/15m to the daily `build_trainer_datasets.sh` so
  their label datasets stay fresh for the RG4 soak.
- **RG3 (clean-candle discrimination), trainer-diag #4872/#4889:**

  | head | RG3 overall AUC | verdict | recent-fold AUC | volatile base rate |
  |---|---:|---|---:|---:|
  | eth-regime-15m-lgbm-v1 | **0.788** | TRUSTWORTHY | 0.751 | 8.2% |
  | eth-regime-5m-lgbm-v1 | **0.770** | TRUSTWORTHY | 0.738 | 1.5% |

  Both clear RG3 decisively, in the BTC 5m/15m band (0.71–0.83). The recent-fold
  AUC (0.74–0.75) — the closest in-session proxy for a live sample — shows they
  discriminate on the most-recent window, unlike the constant-output 1h head.

- Both register at `shadow` → they auto-wire into the live per-bar regime scorer
  on the next registry publish (same path the ETH 1h xasset soak already uses)
  and begin logging live shadow rows. **RG4 on the new heads is future-dated**:
  it scores the EXACT rows the live runtime logs, and a brand-new head has none
  until it soaks (trainer-diag #4892 confirms 0 live rows today).

## 4. Deliverable + recommendation

- **Honest negative for the ETH 1h heads:** the base head is weak/borderline live
  (knife-edge RG4 0.58 at one threshold, ~0.48 at neighbours, 111 rows); the
  xasset head is broken by a dead-cross-asset-feature training bug. **Do not
  promote either** ETH 1h head. The 2026-06-27 in-sample A/B ($63→$2336) was run
  on the v1 1h head and is therefore **optimistic for live** — consistent with
  the cell-selection walk-forward's mixed 2/3-net result. Not re-run here: no ETH
  head robustly clears RG4 in-session, so the "re-run the A/B with a GOOD head"
  step is genuinely blocked until the 5m/15m heads soak.
- **Positive path:** `eth-regime-{5m,15m}-lgbm-v1` are RG3-strong, register at
  shadow, and are now soaking. Their RG4 verdict lands after they accrue live
  rows (~days–2 wk; 5m accrues ~1 row/5 min so its RG4 sample will dwarf the 1h
  head's 111). Expectation per the BTC analogy: they pass RG4 robustly.
- **Next (post-soak):** RG4 the 5m/15m heads at the matched 0.005 threshold; if
  they pass (≥0.55 robustly), re-run the ETH vol-split A/B + cell-selection
  walk-forward using the passing head and propose advisory promotion (Tier-3,
  operator-gated). SOL is the follow-on.
- **Two tooling fixes filed:** `MB-20260628-RG4-THRESH` (RG4 should score at the
  dataset's vol_threshold, not the 0.003 default) and `BL-20260628-XA-TRAINING-ZERO`
  (the ETH 1h cross_asset side-stream is dead at train time → xasset head trained
  on zeros).

## 5. Watch item

The new ETH 5m/15m heads add two `(symbol, timeframe)` groups to the live
per-bar regime scorer on the 2-core money VM (5m scores ~1×/5 min). Bounded by
the existing `REGIME_BAR_SCORING_BUDGET_S` + fetch-gate, and ETH candles are
already fetched for the live ETH strategies, so the increment is modest — but
watch the live heartbeat / CPU over the first cycles (cf. the 2026-06-09/10
per-bar-scorer wedges, `MB-20260618-XA-SOAK-WATCH`).
