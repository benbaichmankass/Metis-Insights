# Can we field intraday crypto-perp strategies that fit HyroTrader and are profitable net of cost? (2026-09-30)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **desk research plus an exploratory Monte Carlo on committed ledgers. Nothing was bought, no backtest was run in this lane (the container has no candle data), no config was touched.** Lane HYRO-STRATS, operator request relayed by the manager, ~05:40Z 2026-09-30: *"it is an issue that we don't have a lot of strategies that are geared towards it, but maybe we can scope out the feasibility of building a few strategies that are shorter time frames so that they fit within that framework, but also ... are also profitable."*

Rules come from [`hyrotrader-bybit-deep-dive-2026-09-30.md`](hyrotrader-bybit-deep-dive-2026-09-30.md) (first-party pages, read 2026-09-30). The qualifying-day wording was re-read for this memo (§ 1).

## 0. Bottom line

**MARGINAL, leaning NOT FEASIBLE on current evidence.**

1. **No committed strategy has a comfortable pass probability.** Best case (no fill flagged, strict ruleset, 1% risk, 720-day horizon, committed 365-day ledgers taken at face value): P(pass both evaluation phases) is about **0.27 for the ict_scalp 15m book** (ETH+SOL+XRP pooled, n=378) and **0.47 for ict_scalp_eth_15m alone** (n=117). Shift every trade by −0.03 R (the book's mean is only +0.048 R/trade) and the book falls to about **0.20**.
2. **The demo-realism rule is close to decisive for these strategies.** Every entry we have is a Market order. If half of them are flagged (profit credit ×0.4, losses in full), the book's P(pass both) falls from 0.27 to **0.03–0.05**. On a profit factor near 1.1 a haircut on profits alone turns counted progress negative. This is a model result on an assumed flag rate; how often the firm flags is unknown.
3. **Same-day closing is not the problem.** The 15m/5m scalps close same-UTC-day 92–97% of the time and would give roughly 2.4 qualifying days a week as a book (strict reading). It is the thin edge, the market-order fills and the 5% static / 4% trailing limits that bind.
4. **Break-even is about P ≥ 0.23** on the $59 deposit (refundable with the first payout), so the best case is roughly break-even and every haircut is negative EV (§ 3.4).
5. **The evidence is 365 days, one regime, with every 15m leg's final fold negative** and `param_selection_provenance: not_established` on every record. What this memo can honestly say is *not disproven, not established*.

6. **Trade propensity is NOT the problem (added on the operator's ~05:44Z requirement, § 6).** The realistic ≤1h portfolio trades **about 32–45 times a month with 10–12 qualifying days a month** (strict reading), not "once or twice a month"; five qualifying days accrue in about two weeks. Only the BTC/SOL 1h trend legs (5–6 trades, ~1.3 qualifying days a month) and fvg_range_15m (1.8 trades a month) trade at that thin a rate, and they are not candidates. The minimum-days rule is an evaluation-phase rule; the funded phase has an inactivity rule (30 days without a closed trade) but no trading-day requirement for payouts was found. **What is not worthwhile at $5k is the payout size: about $18–$99 a month per funded $5k account**, so the EV per attempt is roughly zero to +$36 best case and negative under either stress. Sizing up ($25k–$100k) makes the best case positive (+$38 to +$207 a month) because the fee scales sub-linearly, but the sign still flips on the flag question.

**Operator decision (§ 7):** do not buy an evaluation on the existing legs; authorise the cheap sequence RQ-20260930-501 → 502 (maker entries) and, only if both pass, decide on one $59 challenge as an experiment.

## 1. The rules that shape the fit (re-verified where it mattered)

| rule | wording (first-party, 2026-09-30) | model used |
|---|---|---|
| **Qualifying trading day** | `/faq/evaluation-process/minimum-trading-days/`: "Each trade must be at least 5% of the initial account balance"; "The PnL of a trade must be at least ±1% of the trade value"; "A day qualifies if at least one trade is executed and closed on that day, regardless of the holding period."; "Minimum of 5 distinct trading days". | **Ambiguity: "trade value" = notional or margin?** Reading A (notional): needs a ≥1% price move. Reading B (margin at 10x): ≥0.1%. Both are run. The 5%-of-balance test is met at any risk ≥ 0.05 × stop% (a 2.6% stop needs ≥0.13% risk); it never binds at 1% risk. |
| 40% single-day rule, phases 1–2 | "no single trading day may contribute more than 40% of the trader's total net result"; example: $500 target, +$320 day capped at $200 | each day's counted profit capped at 0.40 × target; losses in full |
| Daily loss | trailing from the day's peak equity, floating counts, UTC reset (4% or 5%: conflict) | strict = 4% of initial, trailing |
| Max loss | static from initial (5% or 6%: conflict) | strict = 5%; lenient = 6% |
| Per-position loss | ≤3% of initial, "not monitored by our automated system and is reviewed manually" | not simulated; see the caveat in § 3.3 | <!-- population-ok: firm rule text quoted from its own page, not a measurement -->
| Demo realism | "Only 40% of profits from flagged trades count toward profit targets"; flagged = market orders "filled at exact levels ... with no slippage" | `flag_frac` × profit credit 0.4 | <!-- population-ok: firm rule text quoted from its own page, not a measurement -->
| Targets | 10% phase 1, 5% phase 2, no time limit, 30-day inactivity disables | 720-day horizon per phase |

Rulesets written for this lane (they, the scripts and the test ride the companion HELD PR, not this docs-only PR): `config/prop_rulesets/hyrotrader.yaml` (strict) and `hyrotrader_lenient.yaml` (4/6), both `unconfirmed: true` (the firm's own pages conflict; § 5 of the deep dive).

## 2. Rule-fit table and evidence: every family on timeframe ≤ 1h

**Where the numbers come from.** Hold time, same-day share, stop distance and qualifying days are **computed by this lane from the per-trade ledgers** `comms/strategy_evidence/runs/2026-09-25/<leg>__trades.jsonl` (avax: `2026-09-28`; eth_prop: `2026-09-26`), 365-day window, 4 time-folds, OOS pooled, cost stack fee 7.5 bps round trip + slippage 3.0 bps + funding 1.0 bp/window via `execution_costs`. The harnesses (`backtest_ict_scalp.py`, `backtest_trend.py`, `backtest_fvg_range.py`) resolve venue-aware slippage and funding in `main()` and are **not** on CLAUDE.md's four cost-incomplete harnesses (`backtest_orb.py`, `src/backtest/backtester.py`, `backtest_xsec_momentum.py`, `backtest_vol_target.py`). I recomputed n, net R, PF, hold and same-day for all 13 legs below; they agree with the sub-agent reports.

"Qualifying days/wk" counts **distinct UTC days a week with ≥1 same-day trade whose net price move (|net R| × stop%) is ≥1% (A) or ≥0.1% (B)**. It is a proxy: the firm's PnL/value test is on gross fills and I use net R.

| family / leg | tf | typical hold (median) | same-UTC-day | qual. days/wk (A / B) | trades/day | order type | maker option |
|---|---|---|---|---|---|---|---|
| ict_scalp_eth_15m | 15m | 3.2 h | 97% | 0.88 / 1.91 | 0.33 | Market | none built |
| ict_scalp_sol_15m | 15m | 5.9 h | 92% | 1.16 / 2.06 | 0.38 | Market | none |
| ict_scalp_xrp_15m | 15m | 6.2 h | 92% | 1.06 / 1.90 | 0.36 | Market | none |
| **15m book (3 legs)** | 15m | | 94% | **2.35 / 3.98** | 1.06 | Market | none |
| ict_scalp_avax_5m | 5m | 2.0 h (harness cap) | 94% | 1.47 / 3.36 | 0.71 | Market | none |
| ict_scalp_xrp_5m / sol_5m / _5m (BTC) | 5m | 2.0 h | 93–95% | 0.91 / 1.23 / 0.41 (A) | 0.54–0.71 | Market | none |
| fvg_range_15m (BTC) | 15m | 2.2 h | 94% | 0.02 / 0.38 | 0.06 | Market | none |
| trend_donchian_eth_prop | 1h | 12 h | 47% | 1.11 / 1.50 | 0.47 | Market | none |
| trend_donchian_eth / sol / sol_prop / btc | 1h | 18–23 h | 28–38% | 0.27–0.52 (A) | 0.16–0.30 | Market | none |
| vwap 5m (BTC) | 5m | not recorded (config window 240 min) | not recorded | not computable | not recorded | Market | none |
| chop_scalp (research only) | 5m/15m/1h | not recorded | not recorded | not computable | 0.05 | Market | none |
| turtle_soup | 15m/1m | not recorded | not recorded | not computable | not recorded | Market | none |
| crypto pullback ≤1h | — | **no crypto pullback leg at 1h/30m/15m exists** (the 1h pullbacks are ETFs; crypto pullbacks are 2h, 8–25% same-day) | | | | | |

Caveats on hold time: the 5m/15m harness force-closes at `--timeout-bars` 24 (2 h on 5m); **the live `ict_scalp.monitor()` has no bar timeout** (only `ict_scalp_eth_15m` declares `stale_exit_bars`), so live holds are probably longer than the ledger shows (**INFERRED**). The older live sample (n=36 with both timestamps, mostly paper) gave a 194-min median and 83% same-day, from dirty exit times.

**Order type, measured:** every crypto entry is `orderType: "Market"` (`src/units/accounts/execute.py:1528`, close at `:3256`). No Bybit limit/post-only path exists in `src/`; `docs/research/small-tf-directions-2026-07-15.md` records `maker_band_post_only` as "recognized but deferred". Only the Breakout prop bridge uses limits (`src/prop/prop_executor.py:276`). So today **every one of our trades is the pattern the demo-compliance rule names.**

### 2.1 Best committed net-of-cost record per family

| leg | window | n | net R (full cost) | fee-only net R | PF | win % | folds+ | cost-complete? |
|---|---|---|---|---|---|---|---|---|
| ict_scalp_eth_15m | 365d | 117 | **+9.12** (E +0.078) | +12.64 | 1.26 | 38 | 2/4 | yes |
| ict_scalp_sol_15m | 365d | 132 | +4.86 | +8.69 | 1.10 | 53 | 2/4 | yes |
| ict_scalp_xrp_15m | 365d | 129 | +4.08 | +9.69 | 1.09 | 53 | 3/4 | yes |
| ict_scalp_avax_5m | 365d | 257 | +6.49 | +16.15 | 1.07 | 50 | 2/4 | yes; **pass rests on `atr_sl_buffer_mult` 0.20→0.50 chosen on this window** (was −9.13) |
| ict_scalp_xrp_5m | 365d | 243 | −0.92 | +10.61 | 0.99 | 52 | 1/4 | yes (`approximate` fidelity) |
| ict_scalp_sol_5m | 365d | 259 | −8.86 | +1.61 | 0.92 | 50 | 1/4 | yes |
| ict_scalp_5m (BTC) | 365d | 193 | −9.48 | +3.45 | 0.89 | 47 | 1/4 | yes; **demoted to shadow 2026-09-28** |
| fvg_range_15m | 365d | 17 | −9.70 | | 0.25 | 35 | 0/4 | yes; n far below any floor; harness targets `mid`, live `far` |
| trend_donchian_eth_prop | 365d | 169 | +13.61 (E +0.081) | +17.66 | 1.19 | 33 | 2/4 | yes |
| trend_donchian_eth / sol / btc | 365d | 107 / 59 / 73 | +16.18 / +4.46 / +4.60 | | 1.25 / 1.12 / 1.09 | 29–36 | 1–2/4 | yes (`approximate` fidelity) |
| vwap 5m (BTC) | 2020-03→2026-05 | 40,650 | **−10,724 R** (E ≈ −0.26) | | | 49.8 | | **fee-only** (predates slippage/funding); killed by the M7 gate 2026-06-09; no ledger in repo |
| chop_scalp (BTC/ETH) | 2023-01→2026-02 | 194 / 188 | −74.3 / −101.6 R | | | 21 / 15 | | **fee-only**; memo: "do not wire a faster multi-TF chop scalper"; no per-trade rows committed |
| turtle_soup | | | **no record** (`coverage_state: no_harness`); the "−66.5%" config comment has no artifact I could find | | | | | |

**Plainly: no evidence exists** for turtle_soup, for vwap or chop_scalp at the current cost stack, for any live-exit hold time, for any maker/limit entry, or for any funding-window/session-open effect on crypto. Real-money data exists only for `ict_scalp_5m` on `bybit_2` (37 closes, −$12.65; 8 of 21 own-bracket exits won). Every 15m leg's final fold is negative (eth −5.03, sol −3.15, xrp −5.15 R) — the pooled pass is not fold-uniform. The exit head did not recover R on the 15m legs (RQ-20260928-005 / RQ-20260929-401). RQ-20260929-044/-053 (Breakout prop fit on the same ledgers) graded `indeterminate` for eth_15m and `fail` for xrp_5m.

## 3. Pass probability

### 3.1 Method, and why it is a lane script

`src/prop/montecarlo.py::run_montecarlo` (read this session) models a static drawdown floor and a **realised-only** daily loss on a per-trade block bootstrap. It cannot express HyroTrader's trailing daily drawdown on floating equity, the 5 qualifying days, the 40% day cap or the flag haircut. `scripts/research/hyro_passprob_mc.py` adds those, block-bootstraps the committed ledgers (block 4, exit order), and is tested (`tests/test_hyro_passprob_mc.py`, 6 tests: huge edge passes, zero edge rarely does, breach rises with risk, the qualifying-day gate can block a pass, a big winner is not mis-read as a drawdown). **One bug was caught by that test and fixed before any number here**: a winner's peak was credited before its adverse excursion, so a +5R trade counted as a 5% intraday drawdown.

**What is not modelled (all bias the result optimistic unless noted):** overlap between legs (the "book" ledger is treated as one sequential stream — simultaneous losses across ETH/SOL/XRP, which are highly correlated, are ignored); intratrade excursion is **assumed** (winners draw an adverse excursion of 0.3R × U(0,2); sensitivity 0–1.0 below), not measured, because the ledgers hold MFE but no MAE; funded-phase payouts are not simulated, only survival and mean return over 90 days; one trade's 3%-per-position rule is not enforced.

**What n it rests on:** ict_scalp 15m book n=378 (357 days); eth_15m n=117; avax_5m n=257; trend_eth_prop n=169; mixed n=286. Each is one 365-day window with `param_selection_provenance: not_established`. A pass probability from n≈120–380 trades on one regime has a wide, unmeasured error bar, which is why the shifted (−0.03 R) run is reported next to it.

### 3.2 Results (strict ruleset, reading A, best case = no flagged fills, 720-day per-phase horizon, 1000 paths, seed 20260930)

Source: [`hyro-strategy-feasibility-mc-horizon720-2026-09-30.json`](hyro-strategy-feasibility-mc-horizon720-2026-09-30.json). Breach columns are for phase 1.

| candidate | risk/trade | P(pass phase 1) | P(breach: daily trailing / static max) | **P(pass both phases)** | median days to pass phase 1 | P(funded survives 90 d \| funded) |
|---|---|---|---|---|---|---|
| 15m book (n=378) | 0.5% | 0.58 | 0.09 / 0.33 | **0.39** | 170 | 0.82 |
| | 1.0% | 0.49 | 0.16 / 0.36 | **0.27** | 49 | 0.51 |
| eth_15m alone (n=117) | 0.5% | 0.56 | 0.00 / 0.13 | 0.44 | 400 | 0.99 |
| | 1.0% | 0.65 | 0.00 / 0.35 | 0.47 | 170 | 0.85 |
| avax_5m (n=257) | 1.0% | 0.43 | 0.01 / 0.56 | 0.22 | 90 | 0.61 |
| trend_eth_prop 1h (n=169) | 1.0% | 0.51 | 0.00 / 0.48 | 0.18 | 90 | 0.61 |
| eth_15m + trend_eth_prop (n=286) | 0.5% | 0.74 | 0.00 / 0.25 | 0.57 | 180 | 0.87 |

Read these as **optimistic**: overlap ignored; the 0.5%-risk rows take 6–13 months to reach 10%, and 2% risk (in the 180-day grid) collapses because the daily trailing rule breaches (book 0.50).

### 3.3 Sensitivities (180-day-horizon grid in `…-mc-results-hyrotrader-2026-09-30.json`; lenient in `…-hyrotrader_lenient-…json`)

| stress | book P(both) @1% | note |
|---|---|---|
| baseline (180 d) | 0.27 | matches the 720 d run: the book mostly resolves within 180 d at 1% |
| every R shifted −0.03 | 0.18 (720 d smoke run: 0.20) | pooled mean is only +0.048 R; a 0.03 haircut is 63% of it |
| **half of fills flagged** | **0.03–0.05** (720 d; 0.03 at 1500 paths, 0.05 at 1000) / 0.00 (all flagged) | the profit-only haircut turns counted progress negative on a PF ~1.1 book |
| qualifying reading B instead of A | 0.27 vs 0.27 | reading A does not bind the book (44% of its trades qualify) |
| lenient ruleset (4% / 6%) | 0.31 | the 5% vs 6% conflict moves the book ~4 points |
| winner adverse excursion 0 / 0.6 / 1.0 R-fractions | 0.27 / 0.24 / 0.21 | the MAE assumption is worth ±3–6 points |

**A tail-risk item the MC cannot see:** the pooled ledger's worst trade is **−6.18 R** (`ict_scalp_xrp_15m`). At 1% risk that is a 6.2% loss on one position: over the firm's 3% per-position rule (manually reviewed, so a warning/termination risk) and over the whole 5% max-loss line. Any HyroTrader deployment needs a hard per-trade loss cap the strategies do not have.

### 3.4 Economics per attempt (rough, INFERRED arithmetic on § 3.2)

Deposit $59, refundable with the first payout (deep dive § 1.6). A passed attempt: about 5% × $5,000 × 80% ≈ $200 in the first 90 funded days (the MC's mean funded return for the book is 0.051). EV ≈ P(both) × $200 − (1 − P(both)) × $59: **book @1% best case +$12; shifted −0.03 (P≈0.18): about −$12; half-flagged (P≈0.04): about −$49.** Break-even P ≈ 0.23. This excludes the funded phase's own breach risk (book survives 90 d only 51% of the time at 1% risk) and any venue difference in the funded phase (unresolved).

## 4. Gaps and candidate new strategies

The binding constraints, in order: (1) **edge per trade** (0.03–0.08 R after cost; progress needs edge × frequency); (2) **flag exposure** (market fills); (3) **trailing 4% daily on floating equity** against high-variance trend legs; (4) **frequency**: one leg gives 0.3–0.7 trades/day.

| candidate | hypothesis | why it fits | evidence that would test it | status |
|---|---|---|---|---|
| **A. Post-only limit entries on the ict_scalp 15m book** | The same signals entered with a maker limit keep a positive net R after adverse selection and unfilled signals, and remove the flag exposure. | Addresses constraint (2) directly; maker 2 bps vs taker 5.5 bps also saves ~3.5 bps on the entry side. | New harness flag (`--entry-mode limit`), 1830 d, ≥300 filled trades, fill rate ≥50% | **RQ-20260930-502 (blocked on flag)** |
| **B. Same-UTC-day-flat trend (ETH prop 1h)** | A 23:45 UTC flatten (no entries after 20:00) keeps net R > 0. | Highest payoff (avg win +1.54 R) and the best qualifying-day yield among 1h legs. My prior is that the flatten removes most of the edge. | `backtest_trend.py` needs `--flat-at-utc`, 1830 d | **RQ-20260930-503 (blocked on flag)** |
| C. Session-open range breakout (ETH/BTC 15m at 00:00 / 08:00 / 13:30 UTC), time-stopped | Volatility expansion at session opens gives an intraday edge that survives the crypto cost stack. | Same-day by construction; frequency ~1/session. | Only fee-only, exploratory `RQ-20260929-108` (ETH 5m ORB) is queued; the only ORB result (MES, `backtest_orb.py`, cost-incomplete) was **SHELVE**. Needs a cost-complete crypto ORB harness first | not registered here (108 covers a first look; a cost-complete harness is the prerequisite) |
| D. VWAP reversion | — | — | 6-year fee-only record is −0.26 R/trade (net-negative in every regime) | **Prior is negative; not proposed** |
| E. Funding-window effect (00/08/16 UTC) | Perp price drifts predictably into/after funding. | Time-stopped, same-day. | **No evidence exists** in the repo (probe with hits, not exhaustive). Would need a 1m/5m event study first | speculative; not registered |
| F. More legs to raise frequency | Adding uncorrelated 15m legs raises trades/day. | Progress ∝ edge × frequency. | The ETH+trend mix helps in MC (0.57 at 0.5%) but overlap is ignored; 15m legs on the same three coins are correlated | covered by 501's book design |

## 5. Pre-registered research units (HELD; nothing was run)

Opened in the companion HELD PR (with the ruleset, scripts and test); **the manager merges it before any run** (a new research-queue unit is `hold`).

| id | file | what | state |
|---|---|---|---|
| **RQ-20260930-501** | `research/queue/` | 15m book, 1830 d, cost-complete ledgers via `build_strategy_evidence.py`, graded by `scripts/research/hyro_fit_verdict.py`: PASS iff pooled n ≥ 300, net R > 0, P(both) ≥ 0.25 on all seeds, and ≥ 0.10 with R shifted −0.03. Informational: P(both) if half the fills are flagged. | queued, runnable (`research-script-run.yml`, trainer-resident data) |
| RQ-20260930-502 | `research/queue/blocked/` | maker-limit entries (fill model registered now) | blocked on a `backtest_ict_scalp.py` flag; clears when 501 PASSes |
| RQ-20260930-503 | `research/queue/blocked/` | same-day-flat ETH trend | blocked on `backtest_trend.py` flags |

Checked: 501–503 collide with nothing (`origin/main` has no `20260930` ids; the one in-flight `20260930` PR is #14514, id 402; `RQ-ID-ALLOCATOR` is a queued checklist row with no allocator, so the ids were taken from the `≥500` hand-written range). `tests/test_research_queue.py` (90 passed) and `research-queue-decision-rule-guard` (OK, base graded) pass locally.

⚠️ **Honesty about pre-registration:** the exploratory MC in § 3 was run *before* the units were written and shows the book near the 0.25 bar. The units are therefore not blind to that number; what they add is a **new, 5-year window** that the exploratory run never saw, so PASS/FAIL is not decided by data already looked at. The thresholds (0.25 from break-even, 0.10 for the shifted stress, n ≥ 300) come from § 3.4 arithmetic, not from tuning to the result. Also: `hyro_fit_verdict.py`'s builder step could not be exercised here (no candles); its grading half is tested on committed ledgers (`--from-ledger` smoke path: n=378, PASS, as the exploratory run predicts).

## 6. Trade propensity, rule scope and worthwhileness (operator requirement, ~05:44Z 2026-09-30)

> Operator, verbatim: *"it's okay if not every day has a trade ... but if we only figure out one or two strategies and they only trade once or twice a month, then it's not necessarily going to be a worthwhile path as opposed to building some sort of local option for breakout."*

### 6.1 Worthwhileness test (stated before the numbers)

HyroTrader is worth building over the Breakout local-agent route (L3) only if **all three** hold for the realistic portfolio:

1. **Propensity:** ≥ 10 executed trades a month **and** ≥ 4 qualifying days a month (strict reading A), so the 5-day minimum clears in about six weeks and the funded account cannot go 30 days without a closed trade.
2. **Pass probability:** P(pass both phases) ≥ 0.23 (the break-even at the $59 refundable deposit, § 3.4) **and** still ≥ 0.15 with every R shifted −0.03 (the same stress as RQ-20260930-501, tightened from 0.10 because a portfolio should clear a higher bar than one leg).
3. **EV:** expected value per month net of the account fee, at the account size actually bought, exceeds Breakout's by more than the extra build cost, amortised over 12 months. Build cost side: HyroTrader ≈ 4–6 lane-days (deep dive § 3, INFERRED) vs L3 ≈ 2.5–3 (manager's figure, not re-derived here). At this lane's own ceiling of about $40 a lane-day (an INFERRED proxy for compute and tokens, not payroll) the difference is roughly $60–$120, i.e. **too small to decide anything**; gate 3 is decided by EV per month, not by build cost.

### 6.2 Rule scope: evaluation only, or also funded? (first-party, fetched 2026-09-30; WebFetch returns a small-model summary, so quotes are near-verbatim)

| question | what the pages say | scope finding |
|---|---|---|
| Minimum / qualifying trading days | `/faq/evaluation-process/minimum-trading-days/`: "Minimum of 5 distinct trading days"; a day qualifies if "at least one trade is executed and closed on that day, regardless of the holding period", with the 5%-of-balance and ±1%-of-value tests (§ 1). The page is in the *evaluation-process* section and says nothing about funded accounts. | **Evaluation phases. No funded-phase trading-day requirement was found** (absence in the pages read, not proof). |
| Payout eligibility | `/faq/hyrotrader-account/how-can-i-withdraw-my-profits/`: "You have the option to request a payout on the same day as your first trade executed on the account."; processed in 12–24 h. No minimum trading days, cycle, consistency or profit-distribution condition appears. Separate page: payout needs "at least $100 in profit after the split". | **Payouts are not gated by trading days.** |
| Consistency (40% day rule) | phases 1 and 2 only (deep dive § 1.5); `/faq/rules/are-there-any-other-rules-for-a-funded-account/`: funded accounts "do not have a profit distribution rule". | **Evaluation only.** |
| Funded-account rules that DO continue | same page: "The following rules from the Challenge phase continue to apply during the funded (live) phase: Daily Drawdown", "Maximum Loss"; plus "up to 25% of the initial account balance as total margin" and "total notional value ... must not exceed 2×" the balance. | Drawdown rules and exposure caps bind for the life of the account. |
| Inactivity | `/faq/rules/do-you-have-an-inactivity-rule/`: "if your most recent trading day is older than 30 days, your account will be disabled."; "A trading day is defined as the last day you closed a trade, not the day you opened it". The page does not say which account types it covers. | **Assume it applies to funded accounts too.** Any *closed* trade resets it (no size or P&L test stated there). Longest gap between closed trades in the committed ledgers: book 6 days, top-3 portfolio 5 days, single 1h trend legs 7–19 days, fvg_range 57 days. |
| Are intraday round trips required? | No rule requires them as such; the funded page says positions may stay open overnight/weekends (deep dive § 1.4). "Same-day" only enters through the *qualifying-day* definition, i.e. it is what earns the 5 evaluation days. The wording "executed and closed on that day" can be read as close-day-only; I model strict same-day (reading A) and report the close-day count too. | Intraday matters for **evaluation only**. |

### 6.3 Trade frequency, measured (365-day cost-complete ledgers; trades and qualifying days per month)

"A / B" are the strict (≥1% price move) and lenient (≥0.1%, 10x margin) readings of the ±1% test on same-day trades; "C" counts a trade on its **close** day (opened earlier allowed), reading A. Computed by this lane from `comms/strategy_evidence/runs/*/<leg>__trades.jsonl` (ETH prop 2026-09-26; avax 2026-09-28; others 2026-09-25); ticket-independent, harness-signal rates, not live-journal counts (the journal endpoint needed a session token this lane does not have).

| set | n (365 d) | trades/mo | qual. days/mo A | B | C | days/mo with any close | longest gap between closes |
|---|---|---|---|---|---|---|---|
| ict_scalp_eth_15m | 117 | 10.0 | 3.8 | 8.3 | 3.8 | 8.8 | 15 d |
| ict_scalp_sol_15m | 132 | 11.5 | 5.0 | 9.0 | 5.6 | 10.4 | 12 d |
| ict_scalp_xrp_15m | 129 | 11.0 | 4.6 | 8.3 | 4.8 | 9.8 | 13 d |
| ict_scalp_avax_5m | 257 | 21.5 | 6.4 | 14.6 | 6.8 | 16.3 | 9 d |
| ict_scalp_xrp_5m / sol_5m / BTC 5m | 243 / 259 / 193 | 20.4 / 21.7 / 16.5 | 4.0 / 5.4 / 1.8 | 13.6 / 13.8 / 11.0 | 4.4 / 5.7 / 1.8 | 12–16 | 7–12 d |
| fvg_range_15m | 17 | **1.8** | **0.1** | 1.7 | 0.1 | 1.8 | 57 d |
| trend_donchian_eth_prop 1h | 169 | 14.3 | 4.8 | 6.5 | 8.5 | 13.4 | 7 d |
| trend_donchian_eth 1h | 107 | 9.0 | 2.3 | 2.5 | 7.4 | 8.9 | 10 d |
| trend_donchian sol_prop / sol / btc 1h | 65 / 59 / 73 | **5.5 / 5.0 / 6.2** | **1.4 / 1.3 / 1.2** | 1.9 / 1.5 / 2.3 | 3.8 / 4.6 / 4.0 | 5–6 | 14–19 d |
| **15m book (eth+sol+xrp)** | 378 | **32.2** | **10.2** | 17.3 | 10.8 | 19.2 | 6 d |
| **top-3 portfolio (eth_15m + avax_5m + trend_eth_prop)** | 543 | **45.3** | **12.3** | 21.0 | 14.8 | 24.1 | 5 d |

**Plainly:** the realistic portfolio is a **30–45 trades a month** book, not one or two. The operator's concern is real only for the BTC/SOL 1h trend legs and `fvg_range_15m`, and none of them is proposed.

**Cross-check against the live prop ticket stream** (`GET /api/bot/prop/tickets?account_id=breakout_1`, 170 real tickets, 2026-06-21 → 2026-09-28, summary in [`…-breakout-ticket-rate-2026-09-30.json`](hyro-strategy-feasibility-breakout-ticket-rate-2026-09-30.json)): `trend_donchian_eth_prop` alone raised 83 tickets in ~101 days (about 25 a month including suppressed/shadow rows) against the harness's 14 executed a month, so the committed ledgers are not overstating signal frequency.

### 6.4 Time to clear each phase, and funded payouts (strict ruleset, reading A, best case = no flagged fills, 720-day horizon)

Source: [`…-mc-frequency-strict-2026-09-30.json`](hyro-strategy-feasibility-mc-frequency-strict-2026-09-30.json), 1000 paths, seed 20260930. Days are calendar days to first reach the phase target (10% then 5%) with the 5 qualifying days and the 40% day cap enforced, over paths that pass. "Payout" is the mean, over paths that reach the funded phase, of weekly banked profit (80% split, ≥$100 per payout, everything above start withdrawn) per month over the first 90 funded days, on a **$5k** account, including paths that later breach.

| candidate | risk | P(both) | phase 1 days p10 / p50 / p90 | phase 2 days p10 / p50 / p90 | median calendar to fund | funded payout $/mo | P(survive 90 d funded) |
|---|---|---|---|---|---|---|---|
| 15m book | 1.0% | 0.29 | 20 / 49 / 115 | 11 / 26 / 78 | ~75 d | **88** | 0.28 |
| 15m book | 0.5% | 0.41 | 74 / 170 / 369 | 25 / 70 / 180 | ~240 d | 53 | 0.73 |
| eth_15m alone | 1.0% | 0.45 | 74 / 172 / 424 | 32 / 89 / 226 | ~260 d | 49 | 0.77 |
| top-3 portfolio | 1.0% | 0.22 | 14 / 34 / 85 | 10 / 23 / 70 | ~57 d | **99** | 0.09 |
| top-3 portfolio | 0.5% | 0.46 | 45 / 118 / 270 | 19 / 54 / 152 | ~170 d | 67 | 0.63 |
| trend_eth_prop 1h | 1.0% | 0.18 | 41 / 89 / 216 | 31 / 66 / 179 | ~155 d | 70 | 0.42 |
| avax_5m | 1.0% | 0.24 | 39 / 94 / 229 | 18 / 44 / 114 | ~140 d | 56 | 0.42 |

Stress rows (same file plus `…-frequency-shift…json`): with R shifted −0.03 the 1%-risk book falls to P(both) **0.20**, payout $76/mo; if half of fills are flagged, book 1% P(both) **0.05** and 0.5% **0.006**. The qualifying-day rule is not what sets these times (at 10–12 qualifying days a month five accrue in about two weeks); **edge per trade and the drawdown limits are.** Note the trade-off: the risk level that reaches the target quickly (1%) is the one that rarely survives the funded phase (28% for the book, 9% for the top-3), because the 4% trailing daily and 5% static limits continue to bind there.

### 6.5 EV per month, net of fees, by account size (INFERRED arithmetic on § 6.4)

EV per attempt = P(both) × (3 months × payout $/mo × size multiple) − (1 − P(both)) × fee. Fees from the deep dive (§ 1.6): $5k $59, $25k $249, $100k $579 (deposit refundable only with the first payout; refund on success ignored, a small conservative bias). Months per attempt = median calendar to fund + 3. Only the first 90 funded days are counted. Account-size scaling assumes payouts scale linearly, which holds only if the firm's per-position (3%), exposure (2× notional) and Bybit-demo liquidity limits do not bind.

| case (15m book, 1% risk) | P(both) | $5k | $25k | $100k |
|---|---|---|---|---|
| best case (no flags) | 0.29 | +$36 → **+$6/mo** | +$211 → **+$38/mo** | +$1,139 → **+$207/mo** |
| R shifted −0.03 | 0.20 | −$2 → −$0/mo | +$25 → +$5/mo | +$436 → +$78/mo |
| half of fills flagged | 0.05 | −$46 → −$7/mo | −$187 → −$27/mo | −$351 → −$51/mo |
| top-3 portfolio, best case | 0.22 | +$4/mo | +$26/mo | +$169/mo |
| eth_15m alone at 0.5% risk, best case | 0.44 | −$0/mo | −$1/mo | +$6/mo |

**Reading it:** at $5k the path pays about $0–6 a month even in the best case; the fee schedule makes larger accounts much better (fee grows ~10× while size grows 20×), but every row is still hostage to whether the firm flags our market fills. This is EV for **one attempt at a time**; parallel accounts are not independent (same signals, same regime) and the firm caps total capital at $200,000.

### 6.6 Against Breakout (measured ticket rate, and what is not comparable)

- **Breakout ticket stream, measured** (`/api/bot/prop/tickets`, 2026-06-21 → 2026-09-28, 170 real tickets): **18 tickets reached `closed` (a human took and closed the trade) = 5.3 a month over the full 101 days, 4.0 a month over the last 60 days, 1.0 over the last 30 days** (closed per month: Jun 3, Jul 7, Aug 7, Sep 1). The other 152 tickets never became trades: 48 shadow, 44 suppressed, 24 skipped, 22 orphaned, 8 expired, 6 invalidated_prompted. No ticket has been created since 2026-09-28, consistent with the manager's note; the latest 50 rows of the fills table (`/api/bot/prop/fills?limit=50`) read 23 `skipped`, 16 `closed`, 6 `filled`, 5 `open`.
- **So Breakout's executed rate is a property of the manual-click bridge, not of the strategies:** the same signal families raise about 50 tickets a month and 90% die before a trade. An automated local agent (L3) would push the executed rate toward the signal rate, which is exactly the 10–45 a month above. **Frequency therefore does not separate the two routes; rules, fees and edge do.**
- Breakout's ruleset (`config/prop_rulesets/breakout.yaml`): 3% daily / 6% static, **no consistency rule, no minimum trading days, no demo-realism flag rule**, $45 fee, 80% split; HyroTrader adds the 4% trailing on floating equity, the 40% day cap, 5 qualifying days and the flag haircut, and its evaluation runs on a simulated venue. **I did not compute Breakout's EV per month in this lane**, so gate 3 in § 6.1 is decided only on the HyroTrader side here: a like-for-like Breakout figure needs `scripts/research/prop_ev_grid.py` on the same ledgers under `--costs breakout`, which RQ-20260929-044 (eth_15m: `indeterminate`) and -053 (xrp_5m: `fail`) started and did not settle.

### 6.7 Verdict on the worthwhileness test

| gate | result |
|---|---|
| 1. propensity ≥ 10 trades and ≥ 4 qualifying days a month | **PASS** for the 15m book (32 / 10.2) and the top-3 (45 / 12.3); FAIL for the BTC/SOL 1h trend legs and fvg_range |
| 2. P(both) ≥ 0.23, and ≥ 0.15 shifted −0.03 | book 0.29 and 0.20: **PASS on the exploratory run, not yet on new data**; top-3 0.22 (FAIL), and its 1%-risk funded survival is 9% |
| 3. EV/month beats Breakout's by more than build cost | **not decidable** (Breakout EV not measured); HyroTrader's own EV is +$0–$6 a month at $5k and +$38 to +$207 at $25k–$100k, best case, negative if fills are flagged |

**The path is not worthwhile as it stands, but not for the reason the operator feared.** Propensity is adequate. It fails on payout size at the account size the $59 tier gives, on the flag rule, and on a funded-phase survival rate that is poor at the risk needed to pass quickly. **What would change that:** (a) a maker/limit entry path that removes the flag exposure (RQ-20260930-502); (b) evidence of edge above ~0.08 R a trade after cost on more than one regime (RQ-20260930-501); (c) buying a larger account tier, where the fee structure makes the same edge pay; (d) a lower-variance funded-phase sizing (0.5% risk) once the account is funded.

## 7. Decision for the operator

1. **Do nothing yet (recommended).** Approve merging the held units and let the manager run 501 on the trainer. Cost: trainer minutes, no capital. Outcome: a 5-year answer on whether the 15m book's edge is real.
2. **Build the maker-entry harness flag now (Tier 1) in parallel.** Unblocks 502, the only lever on the flag rule. About 1 lane-day (INFERRED).
3. **Buy one $59 challenge as a fill-realism experiment**, not a pass attempt: run one 15m leg at minimum size on the demo to see whether the firm flags our market fills. Only defensible after 501 passes; it answers the biggest unknown (flag rate) for $59 at risk. Also unresolved and cheaper to read first: Terms § 10.2(e) and the 4/5/6% figures on the checkout page.
4. **Park HyroTrader** and spend the effort on the Breakout/other platforms. This is the correct call if 501 FAILs or 502 shows adverse selection eats the edge.

## Appendix A — files

- `scripts/research/hyro_passprob_mc.py`, `scripts/research/hyro_fit_verdict.py`, `tests/test_hyro_passprob_mc.py`
- `config/prop_rulesets/hyrotrader.yaml`, `hyrotrader_lenient.yaml`
- Frequency/payout runs: `…-mc-frequency-strict-…json`, `…-mc-frequency-shift-…json`, spec `…-mc-spec-with-portfolio-…json` (adds the top-3 portfolio), Breakout ticket summary `…-breakout-ticket-rate-…json`.
- Inputs: [`hyro-strategy-feasibility-mc-spec-2026-09-30.json`](hyro-strategy-feasibility-mc-spec-2026-09-30.json) (per-candidate trade samples copied from the committed ledgers). Outputs: `…-mc-results-hyrotrader-2026-09-30.json`, `…-mc-results-hyrotrader_lenient-…json`, `…-mc-horizon720-…json`, `…-mc-mae-sensitivity-…json`.
- Reproduce: `python3 scripts/research/hyro_passprob_mc.py --spec docs/research/hyro-strategy-feasibility-mc-spec-2026-09-30.json --paths 1000 --risks 0.5,1.0 --readings A --flag-fracs 0.0,0.5 --cap-days 720`.
