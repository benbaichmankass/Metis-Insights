# xrp_pullback_2h: the joint (stop, target) tuning attempt, and what it found instead

**Date:** 2026-08-23 · **Tier:** research only — nothing is proposed, shipped,
promoted or demoted here · **Leg:** `xrp_pullback_2h` (XRPUSDT 2h, `bybit_2`,
real money, `execution: live`)

**Asked for:** *"measure which (stop, target) pairs are actually placeable at
~5.8% ATR/entry, and bring one exact Tier-3 change."*

**Answer:** there is no change to bring, and the premise of the question was
wrong — including a number I supplied. Both corrections are below.

---

## 0. Two corrections to my own earlier claims, before anything else

**(a) "XRP is structurally impossible — 5.76% ATR/entry, `cap_r` 0.687, binds
5/5."** That was measured on **five live open trades**. Measured on the leg's
actual entry population — **296 emitted trades**, 2021-08-19 → 2026-08-23 —
ATR/entry at entry bars is:

| | min | p10 | median | p90 | max |
|---|---|---|---|---|---|
| ATR/entry | 0.47% | 1.03% | **1.87%** | 4.19% | 9.38% |
| `cap_r` at the live stop 2.5 | 0.42 | 0.92 | **2.11** | 3.75 | 8.47 |

The 5.76% figure sits around the **p95** of that distribution. The median is
1.87%. **`xrp_pullback_2h` is not structurally impossible**, and writing it off
on a 5-observation sample would have been wrong.

**(b) "PR #10171 moved this leg from `sentinel` to `clamped`, not to
`declared`."** Partly right, and quantified now: at the live stop, `tp_r = 3.0`
is genuinely placeable on **22.3%** of entries and clamped on 77.7%. So the
change is real but binds on roughly three entries in four — not on all of them.

---

## 1. The placeability surface

`cap_r = 0.099 × entry / (atr_stop_mult × ATR)`. A **tighter** stop **raises**
the reachable target, because it shrinks the R the fixed 9.9% price distance is
measured in. Percentage of the 296 entries where a declared `tp_r` is actually
placeable:

| stop \ `tp_r` | 1.0 | 1.5 | 2.0 | 2.5 | 3.0 |
|---|---|---|---|---|---|
| 0.75 | 100.0% | 99.7% | 97.6% | 94.9% | 91.6% |
| 1.0 | 100.0% | 97.6% | 93.6% | 88.9% | 82.8% |
| 1.25 | 98.6% | 94.9% | 88.9% | 81.4% | 73.3% |
| 1.5 | 97.6% | 91.6% | 82.8% | 73.3% | 61.8% |
| 2.0 | 93.6% | 82.8% | 68.6% | 54.1% | 36.5% |
| **2.5 (live)** | 88.9% | **73.3%** | 54.1% | 33.1% | **22.3%** |

So a declared 3.0R becomes mostly placeable at a stop of ~1.0–1.25. **That is
the answer to the question as asked** — and it is the wrong thing to do, per
§ 2.

---

## 2. Placeable is not profitable: 37 cells, zero positive

Placeability says what the venue will accept, not what makes money. Run at the
leg's other declared params (trend 40, pullback 10 / 0.5, trail 6.0,
`adx_min` 25, `trail_decay_arm_r` 4.49), cap **0.099 ON**, net of
fee + slippage + funding, full history, n=296 at the live cell.

**Grid 1 — 25 cells.** net R:

| stop \ `tp_r` | 1.0 | 1.5 | 2.0 | 2.5 | 3.0 |
|---|---|---|---|---|---|
| 1.0 | −114.19 | −107.18 | −115.13 | −99.03 | −78.91 |
| 1.25 | −84.59 | −68.93 | −90.39 | −78.19 | −72.97 |
| 1.5 | −83.30 | −67.47 | −60.41 | −52.09 | −45.89 |
| 2.0 | −57.45 | −45.58 | −45.41 | −26.55 | −23.35 |
| **2.5 (live)** | −45.49 | −30.62 | −20.83 | −14.17 | **−13.02** |

Zero positive cells, and the optimum sat at the **corner** — a boundary, not an
optimum. Concluding there would have been finding the edge of a grid and calling
it a result.

**Grid 2 — extended in the improving direction, 16 more cells.** net R:

| stop \ `tp_r` | 3.0 | 4.0 | 6.0 | 50 |
|---|---|---|---|---|
| **2.5 (live)** | **−13.02** | −15.60 | −13.94 | −13.94 |
| 3.0 | −21.84 | −19.11 | −20.61 | −20.61 |
| 3.5 | −22.03 | −21.63 | −21.63 | −21.63 |
| 4.0 | −14.94 | −16.07 | −16.07 | −16.07 |

Zero positive. **The optimum is now interior, and it is the live geometry:**
stop 2.5 / `tp_r` 3.0, −13.02R, maxDD 33.42, MAR −0.39.

**There is no exact Tier-3 change to propose.** The measurement's value is that
it prevents one: tightening the stop to make the declared 3.0R reachable — the
obvious move, and the one the placeability table invites — costs a further
**66R** at stop 1.0. And `tp_r` 6.0 and 50 return **identical** results at every
stop, because past a certain declared value the clamp binds on every entry and
the declared number is wholly inert.

**PR #10171 was directionally right.** `tp_r = 3.0` is the best value in its
column at every stop tested. Its effect is just smaller than it looks.

---

## 3. What the tuning attempt actually uncovered

Chasing the clamp across the fleet produced a larger finding than the leg did.

`_TP_SENTINEL_CAP_PCT`'s comment (`trend_donchian.py:126-132`, mirrored at
`htf_pullback_trend_2h.py:98`) justifies the clamp as *"still far enough that
the monitor's Chandelier trail remains the real profit-exit."*

Clamped-TP exits vs trail exits, per sentinel leg (e35 base runs, cap ON, full
history; population = 10 sentinel legs):

| leg | sym/tf | TP : trail | placed target (median R) |
|---|---|---|---|
| `trend_donchian` | BTC/1h | 0.10 | 5.98 |
| `trend_donchian_1h` | BTC/1h | 0.18 | 5.38 |
| `trend_donchian_eth` | ETH/1h | 0.33 | 4.08 |
| `trend_donchian_sol` | SOL/1h | 0.68 | 3.22 |
| `trend_donchian_avax_4h` | AVAX/4h | 0.97 | 1.48 |
| `trend_donchian_eth_4h` | ETH/4h | **1.71** | 2.04 |
| `xrp_pullback_2h` | XRP/2h | **2.78** | — |
| `trend_donchian_xrp_4h` | XRP/4h | **3.11** | 2.11 |
| `trend_donchian_ada_4h` | ADA/4h | **3.86** | 1.57 |
| `trend_donchian_sol_4h` | SOL/4h | **3.88** | 1.44 |

**The claim holds on every 1h leg and fails on every 4h leg plus the 2h
pullback.** It is not so much wrong as made on one instrument — BTC 1h, after
the 2026-05-27 ErrCode 10001 incident — and silently inherited by legs where the
arithmetic reverses. A longer bar carries a bigger ATR, so the same 9.9% price
distance is fewer R. Field beats comment.

### Why this matters to the governing question

Every leg in that table **declares 50R** — graded `sentinel_no_expectation`,
*"a sentinel target is not an expectation; it is the absence of one."* That
grade is right about the config and wrong about the venue: on **5 of 10** legs a
hard target exists, is hit **1.7×–3.9× more often than the trail**, and
**nobody chose its value**. It is 9.9%, a number picked to satisfy an exchange
error code.

So the sentinel legs are not running without an expectation. They are running
one the venue wrote for them. `sentinel_no_expectation` cannot distinguish
*"no target, the trail is the exit"* from *"no declared target, and the clamp
is the dominant exit"* — the collapsed-state shape, in the state vocabulary
built to grade exactly this.

Filed as `BL-20260823-VENUE-CLAMP-IS-THE-UNDECLARED-TARGET-ON-HALF-THE-SENTINEL-LEGS`.

### The coherence check

The best risk-adjusted cell in the whole 2,189-cell donchian corpus is
`trend_donchian_sol_4h tp1.5_sm2_to96` (IS MAR 4.25 → 7.41, OOS 0.73 → 1.44).
Its declared **1.5R** sits right beside sol_4h's clamp-imposed effective target
of **1.44R**, and its tighter stop (2.0 vs 2.5) lifts `cap_r` by 1.25× so that
1.5R is genuinely *reachable* rather than truncated.

The winning geometry is, almost exactly: **declare what the venue was already
placing, and tighten the stop until it is reachable.** That is one leg and one
cell and it is not a proposal — but it is the first measured instance of the
claim this thread opened with, arrived at from the opposite direction.

---

## 4. On active management — what this does and does not settle

A declared entry bracket is not a static prediction that competes with managing
the trade; it is the **precondition** for managing it. `_base.monitor` has
declared a `{"tp": float}` verdict — *move the take-profit* — since it was
written, and no strategy has ever produced one; `target_extension_soak` is now
the annotate-only producer, and its `sentinel_no_expectation` /
`no_expectation_declared` states are precisely these legs.

A leg running a sentinel behind a trail has **no expectation to revise**, so the
revision machinery is inert on it by construction — and, per § 3, what it is
actually running is a venue constant that no thesis can revise either, because
no thesis chose it. Declaring the entry expectation is what turns the trail from
*the whole exit policy* into *one input to a revisable one*.

**What is still untested for `xrp_pullback_2h`:** the entry axis (`adx_min` 25 /
the trend-structure predicate) and the trail axis (`trail_mult` 6.0 with
`trail_decay_arm_r` 4.49 — an arm the position-telemetry read already grades
**`unreachable`** under this leg's own cap of 3.9233, so the decay never arms).
TUNE-BEFORE-DEMOTE is per-axis. One axis is now exhausted; two are not, and no
disposition is proposed until they are.

---

## 5. What I am NOT claiming

- **Not** that the clamp is wrong or should be removed. Without it these legs
  cannot place a bracket at all — Bybit rejects a TP beyond ~10%.
- **Not** that the 4h legs are mis-performing because of it. They are among the
  better performers in the corpus.
- **Not** that `xrp_pullback_2h` should be demoted. Two axes are untested.
- **Not** that PR #10171 was wrong. It moved the leg the right way.
- **Not** a Tier-3 proposal of any kind. Nothing here changes a live parameter.

## Reproduce

```bash
# placeability + the 296-entry ATR distribution
python3 scripts/backtest_pullback.py --data data/XRPUSDT_1h.csv --symbol XRPUSDT \
  --resample 2h --trend-lookback 40 --pullback-lookback 10 --pullback-frac 0.5 \
  --atr-stop-mult 2.5 --trail-mult 6.0 --min-confidence 0.0 --adx-min 25 \
  --trail-decay-arm-r 4.49 --trail-decay-tight-mult 2.5 \
  --tp-r 3.0 --tp-cap-pct 0.099 --strategy-name xrp_pullback_2h \
  --emit-trades /tmp/xrp/A.jsonl
# then vary --atr-stop-mult over {1.0,1.25,1.5,2.0,2.5,3.0,3.5,4.0}
#      and --tp-r over {1.0,1.5,2.0,2.5,3.0,4.0,6.0,50}
```

The fleet table in § 3 reads `base.by_outcome` and `base.tp_r_effective_median`
from the e35 sweep, whose per-cell rows are committed at
`docs/research/e35-bracket-corpus.jsonl`.
