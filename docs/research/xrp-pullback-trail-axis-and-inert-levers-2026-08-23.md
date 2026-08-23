# The trail axis: a declared lever that has never once fired, and it is not the only one

**Date:** 2026-08-23 · **Tier:** research only — nothing proposed, shipped,
promoted or demoted · **Leg:** `xrp_pullback_2h` (XRPUSDT 2h, `bybit_2`, real
money, `execution: live`)

Second of the three axes TUNE-BEFORE-DEMOTE requires. The geometry axis is in
[`xrp-pullback-joint-geometry-2026-08-23.md`](xrp-pullback-joint-geometry-2026-08-23.md)
(37 cells, zero positive, live geometry is the interior optimum).

---

## 1. The trail axis is also exhausted

`trail_mult` × `trail_decay_arm_r`, 20 cells, cap 0.099 ON, net of cost, full
history, n=296 at the live cell. **net R:**

| trail \ arm | 0.0 | 1.0 | 1.5 | 2.0 | 4.49 |
|---|---|---|---|---|---|
| 3.0 | −35.81 | −30.70 | −32.58 | −33.54 | −35.81 |
| 4.0 | −32.27 | −36.06 | −33.62 | −31.27 | −32.27 |
| **6.0 (live)** | −13.02 | −34.33 | −23.91 | −14.84 | **−13.02** |
| 8.0 | −20.14 | −34.33 | −24.15 | −15.30 | −20.14 |

**Zero positive cells. The live cell is the optimum.** Every arm value that
actually fires (1.0 / 1.5 / 2.0) makes the leg *worse* at the live `trail_mult`.

So two of three axes are now exhausted — **57 cells, zero positive, the live
configuration optimal on both.** The entry axis (`adx_min` 25 / the pullback
predicate) remains untested, and no disposition is proposed until it is.

## 2. The reason the live cell ties `arm 0.0`: the lever is inert

Look at the first and last columns. **`arm 4.49` and `arm 0.0` (disabled) are
byte-identical at every `trail_mult` tested.** That is not a coincidence; it is
the whole story.

Measured on the same 296 trades, live config:

| | value |
|---|---|
| declared `trail_decay_arm_r` | **4.49** |
| trades whose peak R ever reached 4.49 | **0 / 296 = 0.0%** |
| entries whose venue cap even *permits* 4.49R | 15 / 296 = 5.1% |
| MFE distribution | p50 **0.70** · p90 **2.07** · p99 **2.86** · max **2.92** |

The declared arm sits at **1.54× the largest peak the leg has printed in five
years.** Two independent methods agree: the MFE distribution says it cannot
fire, and the A/B says disabling it changes nothing.

### Why a sane-looking value is inert: the harness was uncapped

| run | n | p50 MFE | p90 | max | reach 4.49R |
|---|---|---|---|---|---|
| **cap 0.099 ON** (live) | 296 | 0.70 | 2.07 | 2.92 | **0 / 296 = 0.0%** |
| cap OFF (sentinel) | 224 | 0.86 | 3.82 | 8.55 | 16 / 224 = **7.1%** |

**4.49 is reachable in an uncapped backtest and unreachable in production.** A
value fitted without `--tp-cap-pct` looks defensible and is inert once the venue
truncates every winner at ~9.9%.

That is the **same declared-vs-placed gap** as the clamp finding — the venue
truncating what the strategy declared — one lever further down. The clamp does
not only replace the target; it silently disarms every lever whose trigger was
calibrated above the truncation point.

## 3. It is not just this leg

Declared `trail_decay_arm_r` against the largest peak each leg has printed
(`max_mfe_r` from the e35 base runs — full history, cap ON, i.e. live
conditions):

| leg | sym/tf | arm | max MFE | n | verdict |
|---|---|---|---|---|---|
| `trend_donchian_sol_4h` | SOL/4h | 5.57 | **3.85** | 197 | **INERT — never fires** |
| `xrp_pullback_2h` | XRP/2h | 4.49 | **2.92** | 296 | **INERT — never fires** |
| `trend_donchian` | BTC/1h | 6.49 | 9.84 | 340 | reachable |
| `trend_donchian_xrp_4h` | XRP/4h | 2.00 | 2.81 | 137 | reachable |

⚠️ **State the population: 4 legs measured, 2 inert.** Four more enabled legs
declare an arm (`avax_pullback_2h` 4.86, `gld_pullback_1d` 5.06,
`qqq_trend_long_1d` 3.56, `scha_trend_long_1d` 2.00) and were **not** in this
run — that is *we did not look*, which is not the same as reachable, and they
are not counted either way.

`sol_4h` matters more than `xrp_pullback_2h` here: it is one of the **better**
performers in the corpus, and the cell that beats it hardest
(`tp1.5_sm2_to96`) is precisely one that stops relying on the trail. A leg
doing well with a dead lever is doing well *for other reasons*, and nobody
could see that from the config.

### The audit for this exists and cannot answer it

[`scripts/ops/lever_reachability_audit.py`](../../scripts/ops/lever_reachability_audit.py)
was built for exactly this question and names `xrp_pullback_2h`'s 4.49 in its
own docstring. Run today it returns **`unmeasured` for all 8 declared levers**
(`0 journal rows supplied`) — it needs live journal rows, and correctly refuses
to grade without them.

But the **backtest already knows**: `max_mfe_r` is in every base run, and it
answers the question over hundreds of trades instead of the handful a live
journal holds. The audit's refusal is right; its input is too scarce.
Filed as `BL-20260823-LEVER-REACHABILITY-AUDIT-BLIND-WITHOUT-JOURNAL-ROWS`.

## 4. What I am NOT claiming

- **Not** that removing the arm would help. `arm 0.0` and `arm 4.49` are the
  same number; removing an inert lever changes no outcome, it only stops the
  config asserting something false.
- **Not** that a reachable arm would help — on this leg every reachable value
  measured **worse**.
- **Not** that the four unmeasured legs are inert. They are unmeasured.
- **Not** a demotion proposal. The entry axis is untested.
- **Not** a Tier-3 proposal of any kind.
