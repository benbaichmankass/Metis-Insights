# M23 Phase 2 — label-volume expansion (3-symbol pooling) results (2026-07-19)

**Verdict: NO-GO on P3 per-cell heads. The 3-symbol pooling lever did NOT widen the
net-positive region past P1's ~11-trade cap — it erased it.** The pooled `won` head
FAILS the recomputed population-matched gate (accuracy 0.7258 vs majority 0.7311;
precision 0.2500 vs base 0.2689), and the pooled R-aware `won_r` head never crosses
net-positive at ANY threshold at either τ (best points: τ=0.50 → n=1 @ −3.93R;
τ=0.75 → n=3 @ −2.78R), where P1's BTC-only C1 at least held a tiny positive tip
(n≤11, up to +3.85R). Root cause is visible in the pooled book itself: **pooling
tripled the backtest TRAIN pool (1,685 → 5,077 rows) but grew the LIVE eval book by
only 7 rows (376 → 383: BTC 376, ETH 7, SOL 0)** — the roster strategies have
essentially no real closed trades on ETH/SOL. Cross-symbol pooling adds training
data the eval book cannot cash: the binding constraint remains **real live labels**,
which pooled backtests cannot manufacture. The honest next lever is eval-side
coverage (P2b, below) + time, not more pooled training legs.

Run provenance: trainer verification bundle stage 3, relaunched after a
`heavy_lock_timeout` (behind a `drift_retrain` job) via trainer-diag
[#6941](https://github.com/benbaichmankass/ict-trading-bot/issues/6941); full output
read via [#6942](https://github.com/benbaichmankass/ict-trading-bot/issues/6942) /
[#6943](https://github.com/benbaichmankass/ict-trading-bot/issues/6943)
(`/tmp/m23_p2_result.txt`, 2026-07-19 11:03–11:05Z, repo @ `bc71bac`). Harness:
`scripts/ml/m23_phase2_labelvol.sh` (landed in PR #6917).

## What P2 ran

Per the P1 conclusion (`M23-phase1-C1-results-2026-07-17.md`: every Phase-1 lever
hit the same ~11-trade net-positive ceiling → the constraint is label volume), P2
pushed label volume by pooling THREE symbols through the full pipeline:

1. Per symbol ∈ {BTCUSDT, ETHUSDT, SOLUSDT}: nightly market_raw 1h (5y) → 1h CSV,
   leakage-free resample to 2h + 4h.
2. Per symbol × roster (`trend_donchian`@1h, `squeeze_breakout_4h`@4h,
   `htf_pullback_trend_2h`@2h): harness `--emit-trades` replay → 9 legs, all 9
   emitted (BTC 1154/223/308 · ETH 1172/232/324 · SOL 1162/200/302 trades),
   recorded into one temp DB (`is_backtest=1`, never the money journal):
   **5,077 pooled backtest rows** (BTC 1,685 · ETH 1,728 · SOL 1,664).
3. `setup_candidates` v020 (`won`) + v021 (`won_r`, τ ∈ {0.5, 0.75}), each symbol's
   REAL closed trades as the live_holdout eval book; symbol as a categorical
   feature (`p2pool` manifests).
4. Gate references RECOMPUTED from the pooled eval book (majority + base rate are
   book properties, not the P1 BTC-book constants).

## Results

**Pooled book composition (the headline number):**

| | backtest (train) | live (eval) |
|---|---|---|
| BTCUSDT | 1,685 | **376** |
| ETHUSDT | 1,728 | **7** |
| SOLUSDT | 1,664 | **0** |
| total | **5,077** (P1: 1,685) | **383** (P1: 376) |

**`won` leg (v020, p2pool-v1) — population-matched gate: FAIL.**
Eval book: n_live=383, wins=103, base_rate=0.2689, majority=0.7311.
Model: accuracy 0.7258 (< majority 0.7311), precision 0.2500 (< base 0.2689),
recall 0.0097, F1 0.019, brier 0.203. Same shape as P1's pooled-`won` result —
the head collapses toward the majority class and selects almost nothing.

**`won_r` leg (v021, p2pool-c1-v1) — EV-gate net-R selection sweep: never
net-positive.** Take-all baseline on the 383-row book: win-rate 0.2689, total R
−157.45, net R −176.60 (0.05R/trade cost; 366/383 rows carry real reconstructed-R,
17 coarse unit-R).

| τ | best point (by delta) | crosses net-positive? | P1 (BTC-only) comparison |
|---|---|---|---|
| 0.50 | t*=0.43, n_sel=1, net R **−3.93** | **no** | P1: n=10 @ +2.56, tip n=6 @ +3.85 |
| 0.75 | t*=0.36, n_sel=3, net R **−2.78** | **no** | P1: n=11 @ +0.85 (widest) |

Both verdict lines: "below usable-volume floor / no edge — the meta-label is NOT
(yet) a net-positive trade filter at cost 0.05R." The usable-volume floor is ≥40
trades / ≥10% coverage; P2 doesn't even reach P1's n≈11.

**Harness nit (cosmetic):** the per-(symbol,strategy) debug breakdown SELECT in
step 2 raises `sqlite3.OperationalError: no such column: strategy` (the temp DB
seeds the live `trades` schema, which carries the strategy under a different
column). The pooled count (5,077) records correctly; fix the debug query whenever
the harness is next touched.

## Interpretation

1. **The label wall is EVAL-side, and pooling cannot climb it.** The M23 thesis
   (backtest-augmented labels break the label wall) now has a clean two-phase
   answer: P1 showed the R-aware target works but caps at ~11 confident trades on
   376 real labels; P2 shows 3× more *training* labels doesn't move that cap,
   because the scarce resource is the *real eval book* — and the roster's real
   trades live almost entirely on BTC (ETH 7, SOL 0). Pooled backtest legs added
   in-distribution training variety but zero usable eval labels.
2. **Pooling actively hurt the BTC-cell signal.** The pooled C1 head lost P1's
   small positive tip on what is effectively the same 376-row BTC book. The 9 legs
   are heterogeneous (donchian nets ≈flat-to-negative on BTC/ETH but +2.7R on SOL;
   squeeze/pullback net-positive everywhere, up to +66.7R SOL pullback), and the
   symbol-categorical feature did not prevent cross-symbol dilution of the
   BTC-specific ranking.
3. This mirrors the P1 pooled-`won` finding ("the pooled edge is base-rate-between-
   cells, not within-cell take/skip") one level up: cross-SYMBOL pooling learns
   between-symbol structure, not the within-cell discrimination a take/skip filter
   needs.

## Recommendation — what happens to M23

- **P3 (per-cell heads → shadow → advisory): NO-GO / stays parked.** No pooled or
  BTC-only variant has produced a usable-volume net-positive selection; there is
  nothing to graduate. (Any future P3 remains Tier-3, backtest-A/B-gated.)
- **P2b — eval-side coverage is the one lever left that isn't "wait":** grow the
  REAL eval book, not the train pool. 216 of 491 closed trades in the 90d window
  had no candle shard (the 4h donchian alt symbols + the whole equities/metals
  fleet), so they can't enter the `setup_candidates` live_holdout. The WS-B
  coverage shards (alt-USDT 15m + equities/metals daily, PR #6934) land with the
  next nightly build; once present, a P2b rerun can pool the FULL real closed-trade
  fleet as the eval book (~all 491+ and growing) — real labels, not synthetic ones.
  Expectation management: that's still only ~500 rows against a ≥40-selected floor,
  so treat P2b as a measurement, not a promised unlock.
- **Otherwise: time.** Real labels accrue at live-trading speed. The right cadence
  is a quarterly (or post-P2b) re-run of this exact harness — not another
  target/model/pooling variant; three independent levers have now converged on the
  same wall.

Backlog: `MB-20260717-M23-META-LABEL` updated with this result (P2 done, honest
negative; P2b re-run gated on the WS-B shards); `MB-20260705-META-LABEL-WALL`
unchanged (the wall stands, now with a sharper localization).
