# E0 census, harness half — no leg on this fleet keeps more than half its peak

**Date:** 2026-08-20 · **Step:** E0 of
[`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md)
· Companion: [the live half](./e0-exit-census-live-2026-08-20.md)

**Population.** `runtime_logs/m20_flip_replay_liveparity_v2/2026-08-16/*_trades.jsonl`
on the trainer — the fleet flip-replay corpus. **42 files · 10,280 rows · 34
`(family, symbol)` legs.** Run via trainer-diag
[#9992](https://github.com/benbaichmankass/Metis-Insights/issues/9992) with
`scripts/research/exit_census.py` fetched from `origin/main` at `113741bc`.

**Transport verified, not assumed.** The tool's sha256 on the trainer
(`b8698630a48e352a6983f0b0a64d98be7ac05f749d9f5bce21dbe2c77be2ea0b`) matches the
local file byte-for-byte, and its 39 self-tests passed there before the census
ran — so a corrupted transport could not have produced a quiet wrong answer.

> **This is a different population from the live half and the two are never
> blended.** The harness records the exit reason it itself chose and no
> reconciler exists, so unlike the live journal it *can* answer "who decided".

---

## 1. The vocabulary is fully classified — the census is gradeable

Six distinct `exit_reason` values across all 10,280 rows, **zero unclassified**:

| reason | n | class |
|---|--:|---|
| `trail_stop` | 4,498 | path |
| `stop` | 3,722 | bracket |
| `take_profit` | 1,595 | bracket |
| `stale_stop` | 317 | path |
| `giveback_stop` | 75 | path |
| `timeout` | 73 | clock |

This was checked **before** any share was computed, which is the point of the
`ungradeable` verdict: a leg whose unclassified share exceeded the bound would
have been refused a verdict rather than graded over a vocabulary the module does
not understand. Nothing was refused, so every leg below is genuinely graded.

## 2. Who decides exits, fleet-wide

| class | n | share |
|---|--:|--:|
| bracket — a price level fixed at entry | 5,317 | 51.7% |
| path — post-entry information | 4,890 | 47.6% |
| clock — a timer fixed at entry | 73 | 0.7% |
| **fixed at entry (bracket + clock)** | **5,390** | **52.4%** |

| verdict | legs | rows |
|---|--:|--:|
| `mechanism` (path ≥ 50%) | 16 | 5,562 |
| `partial` | 11 | 3,510 |
| **`no_mechanism`** (> 70% fixed at entry) | **7** | **1,208** |

The seven with **no exit mechanism**, which is E0's falsifier for "E3+ is
premature here":

| leg | n | fixed at entry | path |
|---|--:|--:|--:|
| `htf_pullback_trend_2h` GDX | 90 | 91.1% | 8.9% |
| `trend_donchian` ADAUSDT | 220 | 88.2% | 11.8% |
| `htf_pullback_trend_2h` ADAUSDT | 243 | 86.0% | 14.0% |
| `trend_donchian` TQQQ | 75 | 85.3% | 14.7% |
| `htf_pullback_trend_2h` SOLUSDT | 225 | 81.8% | 18.2% |
| `htf_pullback_trend_2h` XRPUSDT | 297 | 81.8% | 18.2% |
| `trend_donchian` QLD | 58 | 77.6% | 22.4% |

`htf_pullback_trend_2h|XRPUSDT` at 81.8% reproduces the shape the process doc's
§ 0.1 recorded from a different run (284 trades, 78.5%), on 297 trades here.

## 3. The finding: MFE capture

**Median capture is NEGATIVE on 27 of 34 legs — 9,859 of 10,280 rows (95.9%).**
**Zero legs** fall in the 65–80% band the practitioner literature calls healthy
(§ 1.5.1 of the process doc). Only two clear even the 0.5 "poor exit timing"
line, and both are leveraged-ETF longs that also grade `no_mechanism`:
`trend_donchian` TQQQ 1.056 (n=75) and QLD 0.993 (n=58).

Per-leg share of trades capturing under 0.5: **median 0.746**, range 0.386–0.863.

Worst medians: `trend_donchian` BTCUSDT −1.049 (n=1,165) · `trend_donchian`
ADAUSDT −0.986 (n=220) · `htf_pullback_trend_2h` XRPUSDT −0.809 (n=297) ·
`trend_donchian` SLV −0.709 (n=310) · `trend_donchian` ETHUSDT −0.643 (n=1,833).

### ⚠️ What a negative capture rate does and does not establish

**It does not isolate exit timing.** Capture is `net_r / mfe_r` over trades with
a positive MFE, so it is jointly determined by **win rate and exit quality**: a
trade that goes favourable and then stops out scores about `−1 / mfe_r`, and on a
book whose median trade loses, a negative median capture follows close to
mechanically. Reporting this as "the exits are terrible" would be exactly the
unprovenanced-diagnostic error — a real number under a label that describes
something narrower than what was computed.

**What it does establish**, and it is not small: across 95.9% of this fleet's
backtested trades, **the median trade that went favourable at all still ended
below breakeven**. Whatever the split between entry edge and exit timing, the
books are not converting excursions into realised R.

**The cut that would isolate exits** — and which this census deliberately does
not claim to have made — is capture computed on winners only, or conditional on
having reached a given fraction of `cap_R`. That conditional form is exactly what
`peak_banking_basis.conditional_hit_rate` already computes for one leg, and
extending it fleet-wide is the next measurement, not an inference available here.

## 4. MAE-to-stop is unmeasured fleet-wide — a declared gap, not a clean result

**`mae_to_stop: unmeasured_no_mae_field` on all 34 legs, `measured_n = 0`.**

`scripts/backtest_pullback.py`'s `--emit-trades` writer emits `mfe_r` and not
`mae_r`. So the stop-calibration half of the E0 census cannot be computed on this
corpus at all — reported as a coverage gap rather than omitted, because a missing
calibration number and a good one look identical in a report that simply leaves
the row out.

**This is a gap in these two writers, not in the repo**, and the difference
matters because "we cannot measure this" deserves more scepticism than any other
claim here, so here is the tool that was actually read —
checked: scripts/backtest_ict_scalp.py — which emits `meta["mae_r"]` at line 550,
so the quantity is defined and produced
elsewhere. `scripts/research/m21_entry_baseline.py` computes one too. The corpus
in § 1 comes from the pullback and trend harnesses, neither of which writes it,
so the field is absent from *this* population and recoverable by changing those
writers — not by a new metric.

## 5. `xauusd_trend_1h` was swept against MGC's price series — PROVEN, not inferred

The census flagged it: `trend_donchian|MGC` and `trend_donchian|XAUUSD` agree on
all eight statistics — n=228 · bracket 90 · clock 4 · path 134 · capture −0.6223
· share-below-0.5 0.863 · hold median 32.5h · **hold max 342.0h**. Two different
instruments (COMEX micro gold futures vs spot gold) producing identical hold
*maxima* to one decimal is not a coincidence.

Trainer-diag [#9995](https://github.com/benbaichmankass/Metis-Insights/issues/9995)
settles the cause, and the arithmetic is exact:

- The two files are **not** byte-identical — different sha256, and **no two files
  in the 42 collide at all**, so this is not a copied file.
- Their first rows are the same trade with one field changed: same
  `entry_time 2024-01-30 09:00:00+00:00`, same `entry 2054.0`, same
  `sl 2038.8214285714287`, same `exit_time`, same `exit_reason: stop`, same
  `net_r −1.1685`, same `mfe_r 0.0`. Only `symbol` differs.
- **The file sizes prove it for every row, not just the first two.** 98,565 −
  97,881 = **684 bytes** over **228 rows** = **exactly 3.0 bytes per row**, and
  `len("XAUUSD") − len("MGC")` = **3**. The files differ by the symbol string and
  by nothing else.

So `xauusd_trend_1h` was backtested against **MGC's candles**. In
`config/strategies.yaml` it is `symbols: [XAUUSD]`, `enabled: false` (while
`mgc_trend_1h` is `symbols: [MGC]`, `enabled: true`, `execution: shadow`) — so no
live order is affected. What is affected is the **evidence**: the corpus carries
a leg row that looks like an independent measurement of spot gold and is the MGC
futures measurement wearing a different name.

Consequences to apply before quoting anything fleet-wide:

- **The § 2 totals above double-count gold** — 228 of 10,280 rows (2.2%).
- Any coverage-matrix or sweep verdict for `xauusd_trend_1h` is not independent
  evidence and must not be counted as a second leg agreeing.

Filed as `BL-20260820-XAUUSD-LEG-SWEPT-AGAINST-MGC-CANDLES`.

## 6. Two more things worth knowing before quoting these numbers

- **Legs are keyed by FAMILY, not by config leg name.** The corpus labels rows
  `htf_pullback_trend_2h|XRPUSDT`, not `xrp_pullback_2h`, because
  `strategy_name` defaults to the family in the harness. Do not join these to
  `config/strategies.yaml` leg names without translating.
- **`mfe_r` excludes the fill bar**, which is what made a `mfe_r >= cap_r` hit
  predicate unsatisfiable in `peak_banking_basis` and produced `p = 0.0` at every
  threshold. Capture uses `net_r / mfe_r` and is not affected by that defect, but
  anything else built on `mfe_r` should know it.

## 7. What this settles for the process

E0 is now complete on both halves, and they answer different questions:

- **Live**: 63.7% of closes carry a reason the producer could not classify, so
  the live journal cannot say who decides exits.
- **Harness**: it can, and the answer is that **52.4% of exits are decided by a
  level or timer fixed at entry**, with seven legs above 70%.

For those seven, E0's falsifier fires: they have no exit mechanism, and sweeping
lever cells at them is premature. For the rest, the mechanism that exists is
almost entirely `trail_stop` — 4,498 of the 4,890 path exits, **92.0%** — which
is still a function of the trade's own path. So § 0.2 stands unchanged after
measurement: the fleet's only working exit mechanism reads the same endogenous
substrate every failed lever read.
