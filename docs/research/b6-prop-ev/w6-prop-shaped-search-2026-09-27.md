# W6 round 3 — prop-shaped walk-forward parameter search (`RQ-20260927-005`)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Lane: checklist row **W6-PROP-R3**. This is Tier-1 offline research: no
> `config/` edit, nothing placed, no gate flipped. Continues round 2,
> [`w6-candidates-v2-2026-09-27.md`](w6-candidates-v2-2026-09-27.md) (PR #13195).
> Machine-readable result: [`w6-prop-shaped-search-2026-09-27.json`](w6-prop-shaped-search-2026-09-27.json).
> Producer: [`scripts/research/w6_prop_shaped_search.py`](../../../scripts/research/w6_prop_shaped_search.py).

## TL;DR

- **Verdict under the pre-registered rule: NULL.** None of the 6 candidates
  passed `RULE-RQ0927-005-PROP-SHAPED-WF`. **There is no roster or parameter
  proposal.**
- **One candidate passes on its own and fails as an addition**
  (`STANDALONE_ONLY`): trend_donchian on ETHUSDT, re-tuned in-sample to
  `donchian 40 · atr_stop 2.5 · trail 3.5 · tp_r 3 · stale_exit 12`.
  - **On its own,** its out-of-sample evidence-p5 is above 0 on 5 of 5 seeds
    (+96.6 / +65.2 / +83.3 / +145.7 / +41.4). The share of re-drawn histories
    with positive EV is at least 0.99 on every seed, so the result survives a
    Bonferroni correction over the 6 candidates.
  - **Added to the current two legs,** it lowers the portfolio's EV on 5 of 5
    seeds. The mean falls from about $134 to about $48 per life.
- **Every addition lowers portfolio EV.** All 6 candidates, on all 5 seeds
  (30 of 30 cells), give a lower point EV than the two-leg baseline when added
  to it. On this account, with room sizing at k=0.33, a third leg dilutes the
  portfolio. The only pre-registered route to a proposal was an addition, so
  that route is closed.
- **Round-2 re-check (item 3).** `trend_donchian_eth` was re-run at the full
  default Monte Carlo size (4000/100/200). The result gets weaker: on its own it
  now clears on **0 of 5** seeds (round 2 said 2 of 5 at the lighter MC). Added
  to the baseline it clears on **0 of 5**, the same as before.

## 1. Pre-registration

The research unit
[`research/queue/RQ-20260927-005.yaml`](../../../research/queue/RQ-20260927-005.yaml)
and the producer script were committed first, in `b382cc5` at
2026-09-27T15:03:57Z. That commit contains no sweep output. The sweep ran
afterwards. The rule, verbatim in substance:

- **PASS** requires all of the following:
  - OOS n ≥ 88;
  - the candidate's evidence-p5 on its own (path mode) is above 0 on all 5
    seeds;
  - with the candidate added, both the baseline's p5 **and** its point EV are
    higher than the baseline's own, on all 5 seeds.
- **multiplicity_robust** is an extra label on a PASS. It requires the
  on-its-own `p_ev_positive` ≥ 0.99 on every seed.
- **STANDALONE_ONLY** means the candidate clears on its own but the portfolio
  clause fails. It gives no proposal for an addition.
- **FAIL** means the evidence-p95 is below 0 on some seed.
- **NULL / INDETERMINATE** covers everything else.

**Setup:**

- **Grid.** trend_donchian has 32 cells: donchian {20, 40} × atr_stop
  {1.5, 2.5} × trail {2.0, 3.5} × tp_r {3, 6} × stale_exit {off, 12}.
  htf_pullback on 2h candles has 16 cells: atr_stop {1.5, 2.5} × trail
  {2.0, 3.5} × tp_r {3, 6} × stale_exit {off, 6}.
  - Each grid is run on BTCUSDT, ETHUSDT and SOLUSDT, giving 144 cells.
  - Every command line was built with the canonical
    `regime_debt_matrix.build_harness_cmd`, including the live-capped TP
    (`--tp-cap-pct 0.099`). All 146 command lines, including the 2 baseline
    legs, came out `faithful`.
- **Walk-forward.** Anchored expanding windows with 4 folds of about 9 months
  out of sample each: F1 = 2023-10→2024-06, F2 = 2024-07→2025-03,
  F3 = 2025-04→2025-12, F4 = 2026-01→2026-09-24.
  - In-sample data for a fold is every trade that **exited** before the fold's
    OOS start. Trades still open at the cut are excluded, because their outcome
    was unknowable when the selection was made.
- **Selection (in-sample only).** For each (family, symbol, fold), I picked the
  cell with the highest `prop_ev_sim` point EV per life for that cell on its
  own.
  - Settings: path mode, 1000 lives, no outer bootstrap, seed 20260927.
  - Costs: `--costs breakout` (8 bps commission round trip, 3 bps slippage
    round trip, 0.033%/day swap on the dxtrade schedule).
  - Sizing: `--sizing room --room-frac 0.33 --min-risk 10`.
  - Cells with fewer than 30 in-sample trades were not eligible.
  - That makes 576 in-sample simulations. OOS data was never read during
    selection.
- **Grading (OOS only).** The stitched OOS leg is the fold-selected cell's
  trades in each OOS block.
  - It was graded by the `prop_ev_sim.py` CLI at the tool's **full default**
    size: 4000 lives, 100 outer histories, 200 lives per outer history.
  - Seeds: {20260927, 1, 2, 3, 4}.
  - Arms: candidate alone, baseline + candidate, and baseline alone.
- **Baseline.** `trend_donchian_eth_prop` + `trend_donchian_sol_prop` at their
  **current** `config/strategies.yaml` params, on the same candles, limited to
  the same OOS span. That gives n = 529 + 200.

## 2. Ranked OOS results (sorted by the median of the on-its-own p5)

| rank | candidate | OOS n | OOS net R (Breakout costs) | p5 > 0 alone (of 5) | portfolio improved (of 5) | verdict |
|--:|---|--:|--:|--:|--:|---|
| 1 | **trend · ETHUSDT** | 357 | +45.87 | **5** | 0 | **STANDALONE_ONLY** (multiplicity-robust alone) |
| 2 | pullback · BTCUSDT | 426 | +23.62 | 0 | 0 | NULL / INDETERMINATE |
| 3 | trend · SOLUSDT | 411 | +28.10 | 0 | 0 | NULL / INDETERMINATE |
| 4 | pullback · ETHUSDT | 388 | +3.17 | 0 | 0 | NULL / INDETERMINATE |
| 5 | pullback · SOLUSDT | 428 | −9.87 | 0 | 0 | NULL / INDETERMINATE |
| 6 | trend · BTCUSDT | 356 | −14.30 | 0 | 0 | NULL / INDETERMINATE |

All 6 clear the OOS n ≥ 88 power floor. No candidate is a FAIL, because no
evidence-p95 is below 0 on any seed.

### 2a. Per-seed p5, OOS stitched, full default MC (path mode, $ net per account life after the $45 fee)

Each cell shows **p5** on its own, then in parentheses the point EV · p95 ·
p_ev_positive.

| candidate | s=20260927 | s=1 | s=2 | s=3 | s=4 |
|---|---|---|---|---|---|
| trend · ETHUSDT | **+96.57** (396 · 743 · 1.00) | **+65.22** (380 · 1018 · 1.00) | **+83.33** (375 · 839 · 0.99) | **+145.70** (383 · 894 · 1.00) | **+41.37** (396 · 952 · 0.99) |
| pullback · BTCUSDT | −2.91 (64 · 290 · .93) | −10.37 (65 · 278 · .91) | −8.61 (66 · 320 · .91) | −7.04 (68 · 305 · .89) | −14.68 (60 · 193 · .86) |
| trend · SOLUSDT | −9.79 (78 · 305 · .90) | −20.33 (80 · 237 · .89) | −6.33 (77 · 234 · .92) | −20.51 (76 · 313 · .82) | −10.33 (71 · 303 · .89) |
| pullback · ETHUSDT | −34.26 (13 · 145 · .65) | −30.78 (12 · 127 · .66) | −30.16 (15 · 166 · .68) | −31.95 (10 · 129 · .59) | −32.80 (12 · 132 · .63) |
| pullback · SOLUSDT | −41.00 (−4 · 96 · .33) | −40.88 (−1 · 141 · .44) | −40.59 (−5 · 165 · .46) | −43.78 (−7 · 145 · .42) | −40.89 (−8 · 165 · .46) |
| trend · BTCUSDT | −40.32 (−19 · 98 · .38) | −42.46 (−22 · 69 · .25) | −41.89 (−22 · 67 · .27) | −41.30 (−18 · 73 · .36) | −42.67 (−20 · 75 · .36) |

### 2b. The portfolio clause — baseline vs baseline + candidate, per seed (p5 / point EV)

| arm | s=20260927 | s=1 | s=2 | s=3 | s=4 |
|---|---|---|---|---|---|
| **baseline (2 legs)** | **+10.15 / 145.2** | **+10.65 / 131.1** | **+13.59 / 132.5** | **+10.27 / 135.2** | **−6.83 / 127.8** |
| + trend · ETHUSDT | −0.27 / 44.1 | −3.98 / 50.4 | −3.16 / 48.1 | −1.92 / 52.9 | +6.34 / 45.7 |
| + pullback · BTCUSDT | +4.60 / 96.7 | +15.37 / 94.0 | +8.17 / 90.1 | +10.40 / 90.0 | +12.12 / 98.0 |
| + pullback · SOLUSDT | +7.40 / 82.9 | +5.76 / 90.4 | +3.18 / 83.0 | +7.28 / 90.8 | +12.18 / 83.1 |
| + pullback · ETHUSDT | −4.64 / 60.5 | −4.14 / 72.6 | +8.39 / 67.1 | −4.56 / 67.7 | −4.55 / 67.1 |
| + trend · BTCUSDT | −17.62 / 26.2 | −26.41 / 29.6 | −8.48 / 30.6 | −26.34 / 28.6 | −22.80 / 28.6 |
| + trend · SOLUSDT | −27.49 / 8.1 | −25.93 / 11.6 | −28.03 / 7.7 | −27.25 / 6.0 | −21.80 / 7.6 |

**Every addition lowers the point EV, on every seed: 30 of 30 cells.** Some
additions raise the p5 on some seeds (the pullback legs on seed 4, where the
baseline's p5 is itself negative). None raises both the p5 and the point EV on
any seed.

The likely mechanism is visible in how the simulator is built.
`room` sizing gives each new ticket at most 33% of the binding cushion. With
more legs open at the same time, each ticket is smaller and more tickets are
skipped. Correlated losses also land on the same $150 daily-loss limit on the
same day. The sim prices this directly, as a shared balance and overlapping
trades. It is not a correlation proxy. **This is a mechanism reading, not a
separate measurement.**

### 2c. Per fold (descriptive, NOT part of the verdict)

Each fold was graded on its own at seed 20260927 with the lighter MC
(1000/30/100). These rows were added after the registered grade and do not
enter the verdict. Cells show p5 / point EV, in $, then n, then the fold's
Breakout-cost net R.

| candidate | F1 (23-10→24-06) | F2 (24-07→25-03) | F3 (25-04→25-12) | F4 (26-01→26-09) | selected cell per fold |
|---|---|---|---|---|---|
| trend · ETHUSDT | +11.8 / 313 · n 88 · +9.2R | +9.1 / 359 · n 99 · +11.5R | +177.5 / 1029 · n 83 · +20.4R | **−39.6** / 101 · n 87 · +4.8R | 31, 29, 29, 29 |
| trend · SOLUSDT | −34.9 / 64 · +12.8R | +10.3 / 132 · +11.0R | −45.0 / −44 · −10.1R | −24.4 / 436 · +14.5R | 4, 14, 30, 14 |
| pullback · BTCUSDT | −17.2 / 222 · +20.1R | −41.3 / 17 · +1.7R | −45.0 / 170 · +7.8R | −45.0 / −34 · −6.0R | 7, 7, 14, 15 |
| pullback · ETHUSDT | −35.1 / 90 · +5.6R | −45.0 / −43 · −10.0R | −21.6 / 151 · +25.9R | −45.0 / −33 · −18.3R | 12, 15, 6, 6 |
| pullback · SOLUSDT | −45.0 / 79 · +3.4R | −32.2 / 101 · +5.1R | −39.9 / −4 · −0.5R | −45.0 / −44 · −17.8R | 13, 12, 12, 13 |
| trend · BTCUSDT | −45.0 / −35 · −1.6R | −45.0 / −27 · −2.8R | −45.0 / −32 · −10.8R | −45.0 / 48 · +0.9R | 30, 30, 30, 30 |

What the per-fold view says about trend · ETHUSDT:

- **The selection was stable.** Cell 31 (tp_r 6) won F1. Cell 29 (tp_r 3,
  otherwise identical) won F2 to F4, and cell 31 was a close second in F4
  (370.9 vs 374.1 in-sample). That is a plateau, not a single-cell spike.
- **The most recent fold is the weakest.** In F4 (2026-01→09) the OOS net R is
  still positive (+4.8R on 87 trades), but the fold's own p5 is −39.6. The
  on-its-own pass depends on 2023-10→2025-12. This is stated plainly because it
  is the direction a reader would least want to find out later.
- Across the other five candidates, the in-sample choice often changes from
  fold to fold, which is itself a symptom of no stable prop-shaped optimum.
  trend · BTCUSDT picked the same cell in every fold and lost out of sample in
  3 of 4.

## 3. Round-2 re-check: `trend_donchian_eth` at the full default MC (task item 3)

This repeats round 2's inputs exactly:

- `runs/2026-09-25/trend_donchian_eth__trades.jsonl`, n = 107.
- The baseline legs come from their committed evidence `source_run` rows:
  `runs/2026-09-26/trend_donchian_eth_prop__trades.jsonl` and
  `runs/2026-09-25/trend_donchian_sol_prop__trades.jsonl`.
- Room sizing k=0.33, path mode.
- MC size changed from round 2's 1000/30/100 to **4000/100/200**.

| seed | alone p5 / p50 / p95 | + baseline p5 / p50 / p95 |
|--:|---|---|
| 20260927 | −29.56 / 181.98 / 984.49 | −36.12 / 9.48 / 147.40 |
| 1 | −29.33 / 146.90 / 763.67 | −37.10 / 16.99 / 205.60 |
| 2 | −26.79 / 130.30 / 699.88 | −37.45 / 16.85 / 230.17 |
| 3 | −6.08 / 125.40 / 737.32 | −37.54 / 4.47 / 140.39 |
| 4 | −12.69 / 205.42 / 1131.83 | −36.63 / 12.41 / 178.14 |
| **p5 > 0** | **0 / 5** (round 2: 2 / 5) | **0 / 5** (round 2: 0 / 5) |

**The read changes, and it gets weaker.** Round 2's two positive seeds (+1.05,
+6.30) were an artifact of taking a p5 over only 30 outer histories. That p5
is roughly the second-lowest draw, which is noisy. At 100 outer histories, all
5 seeds are negative. On its own the verdict is INDETERMINATE (p95 > 0) and
leans toward not clearing. As an addition it is unchanged: 0 of 5. Pipeline
item `PI-20260927-NLMC4E9Z-0001` is updated with this re-read (§6).

## 4. POST-HOC — replacement, descriptive only (NOT a verdict, NOT pre-registered)

This was run **after** the registered result above was seen, so it cannot be
graded against any rule set up in advance. It is reported only so the next
question can be registered with real numbers in hand. The arm is the
trend · ETHUSDT candidate **in place of** `trend_donchian_eth_prop`, next to the
unchanged `trend_donchian_sol_prop`. Same OOS span, full default MC.

| seed | p5 | p50 | p95 | point EV | p_ev_positive |
|--:|--:|--:|--:|--:|--:|
| 20260927 | +64.45 | 255.68 | 645.18 | 246.37 | 1.00 |
| 1 | +98.24 | 242.99 | 656.49 | 253.84 | 0.99 |
| 2 | +49.49 | 263.00 | 620.94 | 264.40 | 1.00 |
| 3 | +83.72 | 252.58 | 660.86 | 251.39 | 0.99 |
| 4 | +64.72 | 297.20 | 783.44 | 251.12 | 0.99 |

For comparison, the current two-leg baseline on the same span has a point EV
of $128–145 and a p5 of −6.8 to +13.6. The candidate on its own is $375–396.
**On this sample, fewer and better-shaped legs beat more legs.** That is a
hypothesis for the next pre-registered unit, not a finding that authorizes
anything:

- the same candles and the same OOS span were used to observe it;
- the F4 fold is weak (§2c);
- all of this is on Binance-vision candles (§5).

## 5. Caveats that bind any follow-up

1. **The candles are Binance-vision USDT-M, not Bybit.**
   `fetch_backtest_candles.py --source auto` fell back to binance_vision
   because Bybit is geoblocked from this runner. The live signal is computed
   on Bybit candles. The difference is small where it can be checked: the current
   `trend_donchian_eth_prop` faithful argv on these candles, entries from
   2025-09-25 on, gives n = 172 and +13.68R net (Bybit cost stack). The
   current committed record (`runs/2026-09-26/…__bt.json`, Bybit candles,
   2025-09-26→2026-09-25) gives n = 169 and +13.61R. It is still a different
   feed. **Any wiring would need its evidence record rebuilt on the
   canonical feed.**
2. **This is not the committed evidence window.** Round 2 graded the one-year
   evidence window. This unit grades 2023-10→2026-09 out of sample. The
   baseline's own B6 read here (p5 > 0 on 4 of 5 seeds) is a statement about
   this 3-year span. It is not a re-grade of `breakout_1`, whose registered
   verdict is V1's from 2026-09-27.
3. **What was not tested.** Session filters, max-trades-per-day, the ict_scalp
   family and non-crypto symbols were outside the registered grid and were not
   tested. The null covers only the grid in §1.
4. **Multiplicity.** Selection was over 32 or 16 cells in-sample only. OOS
   grading covered 6 candidates. The one on-its-own pass is multiplicity-robust
   under the registered Bonferroni(6) label. The 4-fold stitch and the
   5 seeds do not add independent evidence: seeds vary only the MC noise, not
   the history.
5. **The candidate is a new parameterisation, not an existing leg.** It
   differs from the live `trend_donchian_eth_prop` in `donchian` (40 vs 20),
   `tp_r` (3 vs 6) and the trail-decay levers (not set). It has no
   `strategies.yaml` entry and no committed evidence record.

## 6. Disposition and pipeline

- **No proposal.** The pre-registered rule was not cleared, so this document
  proposes no roster or parameter change. There is no ROSTER/PARAMETER
  PROPOSAL section, by design.
- **Research unit:** `RQ-20260927-005` → `status: done`, with its result
  recorded on the unit.
- **Pipeline:**
  - **New:** `PI-20260927-2SKAFUF5-0001`. Pre-register and run a *replacement*
    question: trend · ETHUSDT (`donchian 40 · tp_r 3 · stale 12`) replacing
    `trend_donchian_eth_prop`, and the leg-count question, on the canonical
    Bybit feed with a holdout not seen here.
  - **Updated:** `PI-20260927-NLMC4E9Z-0001`, with the full-MC re-read (§3).

## Reproduce

```bash
for s in BTCUSDT ETHUSDT SOLUSDT; do
  python3 scripts/ops/fetch_backtest_candles.py --symbol $s --interval 60 \
    --start-date 2021-10-01 --end-date 2026-09-24 --source auto \
    --output data/ohlcv/${s}_1h_w6r3.csv; done
python3 scripts/research/w6_prop_shaped_search.py --work /tmp/w6r3            # cells, select, stitch, grade, r2
python3 scripts/research/w6_prop_shaped_search.py --work /tmp/w6r3 --phase perfold,posthoc   # descriptive §2c, §4
```

The run took about 90 minutes on 4 cores. `prop_ev_sim.py` was run at repo
commit `b382cc5`.
