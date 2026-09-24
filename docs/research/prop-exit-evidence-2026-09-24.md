# breakout_1 exit geometry: MFE evidence (lane E65)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Question (checklist row E65):** for breakout_1's two legs, `trend_donchian_eth_prop` and
`trend_donchian_sol_prop`, what is the MFE distribution (in R) of their trades, and what exit
geometry does it support, net of the full cost stack?

**Tier:** this document is Tier-1 evidence. The geometry it recommends is a **Tier-3 proposal**,
filed with `next_action: ask_operator`. `config/strategies.yaml` is not edited.

---

## §0 PRE-REGISTRATION: committed before any candidate geometry was run

This section was committed and pushed, and the PR opened, **before** any of the candidate runs
below executed. The results sections were added in later commits. The commit history of this
file is the registration record.

### 0.1 A fact that changes what "current geometry" means

`src/prop/breakout_ticket.py:18` tells the placer: *"Do not manage the exit: the broker-side
bracket is the exit."* The prop account is manually bridged. **Nothing trails the stop, and
nothing applies the stale-exit or trail-decay levers on breakout_1.** The design doc says the same
(`prop-dynamic-exits-faster-banking-DESIGN.md` §3: *"it's frozen at the entry SL unless a human
trails it"*).

The committed evidence records (E55, `comms/strategy_evidence/trend_donchian_*_prop.json`) model
`trail_mult 3.5` and, for ETH, `stale_exit_bars 12` and trail decay. **That is how the Bybit
server-monitor would manage the trade, not how the prop account executes it.** So this lane
evaluates the prop legs **as executed**: a static bracket.

### 0.2 Geometries (fixed now, not chosen after seeing results)

All runs use `scripts/backtest_trend.py` on the leg's own YAML entry params (donchian 20, atr 14,
atr_stop_mult 2.5, min_confidence, long_only for SOL), the default full cost stack (fee 7.5 bps
round trip, slippage 5.0 bps, funding 1.0 bps per window), and **one candle file per leg**: 365
days of 1h Binance-vision candles fetched once and reused across every geometry.

| id | what | harness flags beyond the entry params |
|---|---|---|
| **T0** | the E55 record's geometry (trail and stale levers as the YAML declares). **Reference only: not executable on prop.** | exactly `regime_debt_matrix.build_harness_cmd` |
| **M**  | MFE probe: static SL, **no TP**, no trail | `--trail-mult 1000 --timeout-bars 720` |
| **B0** | **prop as executed today**: static SL + TP = min(entry ± 9.9%, 6R) | `--trail-mult 1000 --tp-cap-pct 0.099 --tp-r 6.0 --timeout-bars 720` |
| **B1** | static SL + TP 2R | as B0 with `--tp-r 2.0` |
| **B2** | static SL + TP 3R | as B0 with `--tp-r 3.0` |
| **B3** | the ExitPlan ladder shape: 50% banked at 1.5R, remainder TP 3R | as B2 plus `--bank-frac 0.5 --bank-at-r 1.5` |

B3 differs from B2 by exactly one lever (the rung), so it is judged against B2 as well as B0.
`--timeout-bars 720` (30 days) stands in for "no timeout", because prop has none. A timeout exit
is reported separately: it means the trade would still be open after 30 days.

### 0.3 What is reported

- **MFE** (from M, and separately from the committed E55 trades files): n; p25/p50/p75/p90; the
  fraction of trades reaching 1R, 1.5R, 2R, 3R and the B0 TP level **before the stop**; bars to
  MFE and bars to first reach each level. A level counts as reached on a bar only if that bar did
  not also take the stop (the harness's SL-first convention).
- **Per geometry and leg:** n, pooled net R (full cost stack), fee-only net R, maxDD in R, bars
  held p50/p90, `net_r_per_capital_day`, exit-reason counts, and net R in each of **4 calendar
  folds** (four equal calendar windows over the candle file, trades assigned by entry time).
- **Prop ruleset** (`config/prop_rulesets/breakout.yaml`: daily loss 3% = $150, static DD floor):
  both legs' trades under the same geometry, merged by exit time, at **$75 risk per trade**
  (E59 measured: 1.5% × nominal $5,000).
  - Daily loss: number of UTC days (reset shifted 00:30 UTC) whose realized loss is ≥ $150.
    Realized P&L only; the intraday equity excursion of an open trade is **not** modelled.
  - P(breach within N trades): iid bootstrap of the merged per-trade $ outcomes, 20,000 paths,
    seed 65, N ∈ {10, 25, 50}, starting cushion ∈ {**$94.76** (E59 measured distance to the floor),
    $300 (a fresh account)}. Breach = cumulative realized loss ≥ cushion at any point.

### 0.4 DECISION RULE (lane-registered)

This rule is registered by this lane for this question. It is **not** the M20 Path B floor, which
the operator has not set (exit-refinement skill), and it does not replace that gate.

A candidate **Bk ∈ {B1, B2, B3}** is ELIGIBLE to be proposed over **B0** only if all of these hold:

- **R0 (denominator).** Each leg has ≥ 25 trades under B0 (`MIN_OOS_TRADES`). A leg below that
  gets `insufficient_base`, which is not a pass.
- **R1 (Stage 0).** Pooled net R of Bk > 0 on each leg, net of the full cost stack.
- **R2 (return non-inferiority).** On each leg, net R(Bk) ≥ net R(B0), **or**
  net_r_per_capital_day(Bk) > net_r_per_capital_day(B0) **and** net R(Bk) ≥ 0.75 × net R(B0)
  (the 0.75 applies only when net R(B0) > 0; when net R(B0) ≤ 0, net R(Bk) ≥ net R(B0) is required).
- **R3 (time in market, the operator's stated reason).** p90 bars held under Bk < p90 bars held
  under B0, on each leg.
- **R4 (walk-forward).** On each leg, the number of folds with net R > 0 under Bk ≥ the number
  under B0.
- **R5 (prop survival).** Account-level P(breach within 25 trades | cushion $94.76) under Bk ≤ the
  figure under B0, and the count of days with a realized loss ≥ $150 under Bk ≤ the count under B0.

Among the ELIGIBLE candidates, the one with the highest account-level `net_r_per_capital_day`
is recommended. **If none is eligible, the recommendation is "no geometry change is supported"**,
and it is recorded as an honest negative. Separately, if **B0 itself fails R1**, that is reported
as its own finding: the prop legs would then have no Stage-0 evidence for the way they actually
execute.

---

## Answer first

**The pre-registered rule selects no candidate. No geometry change is supported.** None of B1,
B2 or B3 is eligible (§3). The current prop bracket, **B0** (static SL plus TP = min(9.9%, 6R)),
has the best account-level net R per capital-day of the four: **0.088**, against B2 0.027,
B3 −0.007 and B1 −0.013.

Three findings matter more than the geometry verdict:

1. **The current TP is not unreachable.** Under B0 it filled on **25 of 100** ETH trades and
   **12 of 51** SOL trades. Its effective level is 4.4R (ETH) and 3.8R (SOL), not the 6R the
   YAML says, because the 9.9% price cap binds. On ETH those TP fills are the edge: B0 nets
   +28.0R, and every tighter TP loses most of it.
2. **What is long is the time in market.** B0 holds p50 **26.5 h** / p90 **119 h (5.0 days)** /
   max **302 h (12.6 days)** on ETH, and p50 31 h / p90 128 h (5.3 days) / max 389 h (16.2 days)
   on SOL. Tighter TPs cut p90 to 3–4 days, but only by giving back the ETH edge.
3. **At $75 risk, no exit geometry makes the account survivable.** P(breach within 25 trades)
   from the E59-measured $94.76 cushion is **73–81% under every geometry tested**, and 40–57%
   even from a fresh $300. Exit geometry is second-order here. The survival lever is sizing:
   E59's DD-budget rule, `PI-20260924-MQ3CDMU6-0001`.

MEASURED means produced by the harness runs in this lane, from the committed trades files.
INFERRED means derived by arithmetic, with the assumption stated.

## §1 How it was run (and the positive controls)

- `scripts/research/prop_exit_evidence.py` (manual-only). Each geometry is a
  `scripts/backtest_trend.py` run whose argv is `regime_debt_matrix.build_harness_cmd` with only
  the exit flags swapped. Candles: 8,760 1h bars per leg, 2025-09-24T00Z → 2026-09-23T23Z,
  Binance vision, one file per leg shared by every geometry.
- **Cost stack:** fee 7.5, slippage **5.0** (pinned as registered), funding 1.0 bps per 8h window.
  The first run used main's default of 3.0 bps slippage, because E60/#12855 moved the crypto-perp
  default to 3.0 from **bybit_2** fills. It was discarded before any number from it was used:
  breakout_1 is not Bybit. **Not modelled:** Breakout's real commission (~$1.5/side, E59) and
  its daily financing (E59 measured −$1.17 over ≈0.8 day on 1.3 ETH, ≈1.4 bps per 8h,
  INFERRED, close to the 1.0 modelled), and manual-bridge entry drift (#44 filled 15.81 worse
  than ticketed).
- **Positive control 1:** T0 (the record's own argv) reproduces the E55 records exactly. ETH:
  n 170, net 8.8346R, maxDD 13.2061R. SOL: n 65, net 4.5902R.
- **Positive control 2:** each trade's MFE was re-walked from the candles and compared with the
  harness's own `mfe_r`. **0 mismatches across all 12 leg×geometry runs.** The bars-to-MFE and
  bars-to-level figures come from that same walk.

## §2 The MFE distribution

**(a) Bracket-only, no TP (geometry M): the question "a TP at X, is it reached before the stop?"**
MEASURED. Because the positions have no TP, they stay open long, so M takes fewer entries than
B0. The population is M's own entry set.

| leg | n | MFE p25 | p50 | p75 | p90 | ≥1R | ≥1.5R | ≥2R | ≥3R | ≥ B0's TP (median level) |
|---|---|---|---|---|---|---|---|---|---|---|
| ETH | 54 | 0.25 | 0.77 | 2.37 | 9.20 | 44% | 37% | 30% | 20% | 18.5% (5.0R) |
| SOL | 34 | 0.47 | 1.54 | 3.80 | 5.33 | 62% | 53% | 44% | 38% | 29.4% (3.8R) |

Time to reach a level, in 1h bars (the trades that reached it):

| leg | bars to MFE p50 / p90 | 1R p50 / p90 | 1.5R | 2R | 3R |
|---|---|---|---|---|---|
| ETH | 4.5 / 259.5 | 2.5 / 30.7 | 10.5 / 59.5 | 14 / 66 | 48 / 90 |
| SOL | 23.5 / 130.4 | 10 / 52 | 17 / 82.9 | 27 / 87.8 | 41 / 94.8 |

**(b) As the E55 records measure it (T0: trail plus stale-exit, TP 6R capped),** from the
committed `runs/2026-09-24-e55/*_prop__trades.jsonl`. MFE here is truncated by the trail.
A TP fill counts as reaching every level up to its effective TP.

| leg | n | p25 | p50 | p75 | p90 | ≥1R | ≥1.5R | ≥2R | ≥3R | bars to MFE p50 / p90 |
|---|---|---|---|---|---|---|---|---|---|---|
| ETH | 170 | 0.24 | 0.70 | 1.36 | 2.50 | 37% | 25% | 17% | 9.4% | 3 / 14 |
| SOL | 65 | 0.40 | 1.10 | 1.84 | 3.01 | 52% | 34% | 25% | 12% | 10 / 42.6 |

Read (a) for prop and (b) for Bybit. **T0 cannot run on breakout_1**, because nothing trails the
stop there.

## §3 The geometries, net of the full cost stack

MEASURED. Folds are 4 equal calendar windows, with trades assigned by entry time.

| leg | geo | n | net R | fee-only R | maxDD R | R per capital-day | hold p50 / p90 (h) | fold net R | folds > 0 |
|---|---|---|---|---|---|---|---|---|---|
| ETH | T0 *(ref)* | 170 | 8.83 | 14.76 | 13.21 | 0.101 | 12 / 23 | 1.21, 10.02, −1.99, −0.41 | 2 |
| ETH | **B0** | 100 | **28.03** | 33.36 | 12.10 | **0.142** | 26.5 / 119 | −0.57, 18.70, 14.67, −4.77 | 2 |
| ETH | B1 TP 2R | 122 | 0.88 | 5.81 | 13.82 | 0.006 | 15 / 74 | 3.05, 6.49, −2.54, −6.13 | 2 |
| ETH | B2 TP 3R | 112 | −4.10 | 0.95 | 17.17 | −0.025 | 19 / 82 | 1.96, 2.28, −2.77, −5.57 | 2 |
| ETH | B3 ladder | 112 | −9.82 | −4.77 | 20.81 | −0.059 | 19 / 82 | 1.56, 0.06, −5.42, −6.01 | 2 |
| SOL | T0 *(ref)* | 65 | 4.59 | 6.93 | 7.78 | 0.068 | 18 / 54 | 1.30, −0.74, −1.97, 6.00 | 2 |
| SOL | **B0** | 51 | **1.09** | 4.15 | 7.97 | 0.008 | 31 / 128 | −4.55, 1.34, −2.46, 6.76 | 2 |
| SOL | B1 TP 2R | 63 | −3.77 | −1.36 | 12.49 | −0.048 | 16 / 80 | −2.82, 1.01, −6.33, 4.37 | 2 |
| SOL | B2 TP 3R | 59 | 11.14 | 13.66 | 8.11 | 0.118 | 19 / 95 | 1.62, 1.23, −4.30, 12.59 | 3 |
| SOL | B3 ladder | 59 | 7.96 | 10.49 | 7.59 | 0.084 | 19 / 95 | 2.67, 1.09, −5.30, 9.50 | 3 |

B0 exits: ETH 74 stop / 25 TP / 1 timeout; SOL 39 stop / 12 TP. B3 vs B2 (its one-lever
baseline): the 1.5R rung costs 5.7R on ETH and 3.2R on SOL. On this population the ladder rung
is **worse** than no rung.

**Prop ruleset, account level** (both legs under one geometry, merged by exit time, $75 risk):

| geo | net $ | days with realized loss ≥ $150 | worst day $ | historical max DD $ | P(breach ≤10 / 25 / 50 trades), cushion $94.76 | same, cushion $300 |
|---|---|---|---|---|---|---|
| T0 *(ref)* | 1,006.85 | 7 | −209.39 | 1,181.54 | 62% / 73% / 79% | 19% / 40% / 52% |
| **B0** | **2,183.96** | 18 | −188.95 | 1,094.58 | 71% / **78%** / 81% | 43% / 56% / 62% |
| B1 | −216.44 | 23 | −251.53 | 1,483.55 | 71% / 81% / 86% | 34% / 54% / 68% |
| B2 | 528.12 | 18 | −251.53 | 1,605.02 | 71% / 80% / 85% | 37% / 57% / 67% |
| B3 | −139.07 | 14 | −251.53 | 1,673.26 | 66% / 79% / 85% | 33% / 54% / 67% |

The breach probabilities come from an iid bootstrap of per-trade outcomes, so they ignore serial
correlation and the intraday equity excursion of open trades. They are ordinal, not a forecast.

### Rule verdicts (§0.4)

| candidate | ETH R0–R4 | SOL R0–R4 | R5 | eligible |
|---|---|---|---|---|
| B1 | fails R2 (0.88 vs 28.03) | fails R1, R2 | fails (80.6% > 77.9%; 23 > 18 days) | **no** |
| B2 | fails R1, R2 | **passes all** | fails (80.1% > 77.9%) | **no** |
| B3 | fails R1, R2 | **passes all** | fails (78.8% > 77.9%) | **no** |

B0 passes R1 on both legs, so the as-executed geometry has a positive full-cost Stage-0 figure.
On SOL, though, it is **+1.09R over 51 trades with 2 of 4 folds positive**. That is thin.

### Exploratory only (NOT pre-registered, chosen after seeing §3)

ETH kept on B0 with SOL on B2: account net $2,937, 17 days ≥ $150, P(breach ≤25 | $94.76) 74.1%,
| $300 49.1%. That is better than B0 on every account metric. **It was picked after looking at the
results, on 59 SOL trades whose fold 4 alone is +12.6R of the +11.1R total.** It is a hypothesis
for a fresh-window test, not a recommendation.

## §4 Live prop fills: too small to compare

MEASURED (`GET /api/bot/prop/fills?account_id=breakout_1&limit=500`, read 2026-09-24 ≈15:50Z):
44 rows, **18 closed** (2026-06-23 → 2026-08-30). They cannot be used here, for four reasons:

- the fill row carries **no strategy field**, and the 18 span more legs than the two `_prop`
  twins;
- no fill carries a price path, so **no MFE is computable** from the journal;
- `sl` is null on most rows;
- **5 are operator-discretionary closes.**

Bracket-confirmed outcomes: 1 TP (#14, +$299.66) and 4 stops (#19, #21, #29, #39). **n = 5
attributable-by-mechanism outcomes is too small to say anything**, and I do not.

## §5 Recommendation (Tier-3, filed `ask_operator`, NOT applied)

1. **Keep the current prop bracket (B0).** Do **not** graduate a tighter TP or the 1.5R ladder
   rung to breakout_1 tickets on this evidence. Each candidate either costs the ETH leg its edge
   or raises the breach probability. This is an honest negative for exit-ladder (P4) on prop at
   these rung levels.
2. **The operator's time-in-market concern is real, and exit geometry is the wrong lever for it.**
   p90 is about 5 days per trade. The levers that address it are **sizing**
   (`PI-20260924-MQ3CDMU6-0001`, which makes a long hold survivable) and a **time close-by rung**
   baked into the ticket as a GTD/expiry order, which needs no realtime action. The time close-by
   rung was not tested here and would need its own pre-registered run.
3. **The evidence records model a geometry prop never executes.** T0 (trail plus stale) and B0
   (bracket) differ by +19.2R on ETH and −3.5R on SOL. breakout_1's roster evidence should be
   graded on the bracket it actually runs. Filed.

## What this lane could NOT establish

- Realized prop MFE (the journal has no paths), and so any live-vs-backtest agreement.
- Breakout's true cost stack (commission per side, financing): approximated by the Bybit-shaped
  harness model.
- Out-of-window confirmation of anything. There is one 365-day window. The folds are calendar
  slices of it, not independent tuning splits, because no parameter was fitted.

## Filed (docs/claude/work/PIPELINE.jsonl)

| id | what | next_action |
|---|---|---|
| PI-20260924-GIGNEYV1-0001 | Tier-3: keep B0 on breakout_1. No tighter TP or 1.5R rung. Optional fresh-window tests: SOL TP 3R, GTD time-close rung | ask_operator |
| PI-20260924-GIGNEYV1-0002 | the `_prop` evidence records grade `faithful` on trail/stale levers prop never executes; grade on the bracket. Also records that PI-20260924-MQ3CDMU6-0004's `/tmp` premise is superseded for these two legs (E55 committed their trades) | dispatch_lane |

`docs/research/exit-refinement-coverage.json`: the `exit_ladder` cell on both `_prop` rows is
now `honest_negative` (the 1.5R rung only).
