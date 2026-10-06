# VELOTRADE-FIT — which roster and risk can go live on velotrade_1 (Velotrade CLASSIC 1-Step $5k), with the 5-qualifying-day rule measured (lane VELOTRADE-FIT, 2026-10-06)

> **Doc status:** `unknown` · category `evidence` · last verified `2026-10-06` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> Lane VELOTRADE-FIT (session_01MD2PLWdM6ePF7r1bPXEujR), dispatched by manager session_01MM8o5js6TcDFeNAPBY4Ntv 2026-10-06 ~06:35Z. **Offline Monte Carlo over committed trade records only.** Nothing was traded; `config/accounts.yaml` is not edited by this lane (the roster the manager merged in #16705 cites this unit). Research unit [`research/queue/RQ-20261006-062.yaml`](../../research/queue/RQ-20261006-062.yaml), rule `RULE-RQ1006-VELOTRADE-FIT`, result record `research/results/RQ-20261006-062/velotrade-fit-062.jsonl`, raw cells `comms/research/RQ-20261006-062/sims/`, report `comms/research/RQ-20261006-062/fit.json`. Reproduce: `python3 scripts/research/velotrade_fit_sweep.py --unit RQ-20261006-062 --out <dir>`.

## Answer

**ETH+SOL (`trend_donchian_eth_prop` + `trend_donchian_sol_prop`) at flat 0.5% of balance ($25/trade) is GO_LIVE on all 5 seeds and ranks first.** It clears every clause of the go-live gate the operator re-scoped at 06:40Z (risk requirements, not trading days): max entry gap 6.6 d (inactivity floor 30 d), worst realised 00:30-UTC day −$78 against the −$160 line (80% of the $200 daily limit), P(daily-loss breach) per account life 0.000 on every seed, EV per account life +$167..+$188 (MC SE ≈ $7). That is the roster and `risk_pct` already on `main` for `velotrade_1`; this memo is the evidence record behind it.

**The 5-qualifying-day rule is NOT binding for the evaluation at any size.** P(target reached and never passed) is 0.000 at 0.5% and at most 0.02 anywhere: by the time +10% is reached (median 223 d at 0.5%) a median 15 qualifying days (realised ≥ +$40 in a 00:30-day) have accrued. Nothing needs fixing for the qualifying rule. **What binds the pass is pace**, the +10% target at $25 risk, and the registered rule does not gate on pace. The rule does bind one thing: the **funded** first payout, which needs 5 fresh qualifying days — a mean 34 daily re-asks per life over all lives at 0.5% (≈55 per funded life), so the first payout lands ~2 months after funding rather than at day 14. That is a payout-timing cost, not a breach, and it falls to 11 re-asks at 1.0%.

**The edge bar is not met anywhere** (reported, not gated): P(net>0) per life is 0.45 at the top cell, below the prop-fit lanes' 0.70, and the evidence bootstrap on EV straddles zero (p5 −$20..−$39, p95 +$587..+$875) — the same class as every earlier ETH+SOL run (RQ-20261005-903: 0.42 under Breakout costs). A $54 pilot measures mechanics and cost; it is not a promotion on edge.

## Grid (9 cells × 5 seeds; velotrade_classic_1step.yaml; Velotrade cost stack)

| book | risk | class | binding | (G) gap d | (D) worst day $ / days>80% | (B) max P(daily breach) | P(maxDD breach) | (R) EV/life $ min..max (5 seeds) | P(net>0) min | edge | P(pass 730d) min | alive@730d | days to pass p50 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ETH+SOL | 0.5% | **GO_LIVE** | — | 6.62 | -78.16 / 0 | 0.000 | 0.72 | +167..+188 | 0.45 | EDGE_NULL | 0.61 | 0.28 | 223 |
| ETH+SOL | 1.0% | **GO_LIVE** | — | 6.62 | -156.33 / 0 | 0.015 | 0.99 | +125..+142 | 0.30 | EDGE_NULL | 0.49 | 0.00 | 62 |
| ETH+SOL | 1.5% | **NULL** | B:p_breach_daily | 6.62 | -234.49 / 3 | 0.302 | 0.71 | +44..+63 | 0.16 | EDGE_NULL | 0.35 | 0.00 | 32 |
| SOL | 0.5% | **FAIL** | R:ev | 18.62 | -28.02 / 0 | 0.000 | 0.12 | -24..-21 | 0.13 | EDGE_NULL | 0.31 | 0.88 | 466 |
| ETH | 0.5% | **GO_LIVE** | — | 7.54 | -52.62 / 0 | 0.000 | 0.47 | +72..+87 | 0.37 | EDGE_NULL | 0.52 | 0.53 | 314 |
| SOL | 1.0% | **NULL** | G:gap | 18.62 | -56.05 / 0 | 0.013 | 0.69 | +117..+138 | 0.38 | EDGE_NULL | 0.55 | 0.30 | 244 |
| ETH | 1.0% | **GO_LIVE** | — | 7.54 | -105.24 / 0 | 0.001 | 0.99 | +132..+149 | 0.34 | EDGE_NULL | 0.51 | 0.01 | 103 |
| SOL | 1.5% | **NULL** | B:p_breach_daily | 18.62 | -84.07 / 0 | 0.247 | 0.76 | +80..+90 | 0.22 | EDGE_NULL | 0.42 | 0.01 | 122 |
| ETH | 1.5% | **GO_LIVE** | — | 7.54 | -157.86 / 0 | 0.028 | 0.97 | +94..+109 | 0.25 | EDGE_NULL | 0.44 | 0.00 | 48 |

Population: 730 d committed trade records, `comms/strategy_evidence/runs/2026-10-05-730d/` (ETH n = 355, SOL n = 132, 2024-10-06 → 2026-10-02; record params match `config/strategies.yaml` on every recorded key). Per cell: `prop_ev_sim` path mode, 2000 lives + 50 × 100 evidence bootstrap, seeds 1–5, `--no-hedge`, lev caps ETH 5× / SOL 5×, funded-start fresh, sizing on balance. Cost stack: commission 6 bps round trip (0.03%/side), slippage 3 bps, swap 0.05% of notional per 00:30-UTC crossing (`dxtrade` model). RQ-20261005-903 used the Breakout defaults (8 bps, 0.033%/night prorated) — the Velotrade stack is cheaper on commission and dearer on swap; EV at ETH+SOL 0.5% moved +$151 → +$174. "P(pass 730d)" is a 2-year lower bound: the evaluation has unlimited time and 28% of lives at 0.5% are still in it at the horizon. "P(maxDD breach)" is where every prop life ends eventually (the accepted cost of the bet, B6); it is reported, not gated.

## The qualifying-day measurement

| book | risk | qual label | P(target reached, never passed) | expected qualifying days /30d /60d /90d | P(5 by day 30/60/90/180) | funded first-payout deferrals per life (daily re-asks) |
|---|---|---|---|---|---|---|
| ETH+SOL | 0.5% | QUAL_DAYS_NOT_BINDING | 0.000 | 1.89 / 3.87 / 5.72 | 0.02 / 0.36 / 0.71 / 0.89 | 34 |
| ETH+SOL | 1.0% | QUAL_DAYS_NOT_BINDING | 0.006 | 3.03 / 5.03 / 6.19 | 0.17 / 0.68 / 0.70 / 0.70 | 11 |
| ETH+SOL | 1.5% | QUAL_DAYS_NOT_BINDING | 0.020 | 2.85 / 3.67 / 3.88 | 0.27 / 0.49 / 0.49 / 0.49 | 5 |
| SOL | 0.5% | QUAL_DAYS_NOT_BINDING | 0.000 | 0.65 / 1.37 / 2.1 | 0.00 / 0.01 / 0.06 / 0.43 | 46 |
| ETH | 0.5% | QUAL_DAYS_NOT_BINDING | 0.000 | 1.49 / 3.07 / 4.62 | 0.00 / 0.16 / 0.52 / 0.93 | 41 |
| SOL | 1.0% | QUAL_DAYS_NOT_BINDING | 0.000 | 1.13 / 2.34 / 3.48 | 0.00 / 0.06 / 0.28 / 0.81 | 52 |
| ETH | 1.0% | QUAL_DAYS_NOT_BINDING | 0.001 | 2.55 / 4.75 / 6.34 | 0.09 / 0.62 / 0.77 / 0.78 | 18 |
| SOL | 1.5% | QUAL_DAYS_NOT_BINDING | 0.013 | 1.23 / 2.4 / 3.27 | 0.00 / 0.09 / 0.31 / 0.62 | 29 |
| ETH | 1.5% | QUAL_DAYS_NOT_BINDING | 0.005 | 2.68 / 4.12 / 4.83 | 0.15 / 0.59 / 0.62 / 0.62 | 10 |

Reading: at 0.5% the book produces ~1.9 qualifying days per 30 d and reaches 5 by day 90 with probability 0.71 — slower than the target, which is why the rule never withholds a pass. At 1.0% the P(5 by day N) curves plateau at 0.70 because lives die (P(maxDD breach) 0.99), not because days stop accruing.

## What the sizing sweep says

- **1.0%** is also GO_LIVE for ETH+SOL and for ETH alone: median days to pass drops 223 → 62, P(daily-loss breach) 0.015, but P(maxDD breach) rises to 0.99, P(net>0) falls to 0.30 and EV to +$125..+$142. Ranked below 0.5% by the registered order (min P(net>0), then EV). It is the arm to look at if pace is to be bought with account lives — a decision the operator's 07:15Z pace directive (pipeline item `PI-20261006-…` "STRATEGY SEARCH FOR FASTER PROP PASSES") may want; not proposed here.
- **1.5%** is refused: ETH+SOL fails (D) in the ledger itself (worst realised day −$234, beyond the $200 limit, 3 days beyond the 80% line) and (B) in the sim (P(daily-loss breach) 0.30).
- **SOL alone** fails (G) at every size (18.6 d gap) and is EV-negative at 0.5%; **ETH alone** is GO_LIVE at every size but ranks below the book at 0.5% (EV +$77 vs +$174, P(net>0) 0.37 vs 0.45).

## What this does not establish

The ruleset is desk research (`unconfirmed: true`; first-party pages read 2026-10-04) and the funded offer is discretionary (Terms 5.3(b)) — neither is modelled. Backtest gaps do not prove live gaps. The cost stack is assumed, not measured: `PI-20261006-1BPXEUJR-0001` re-runs this sweep with the realised commission and swap once `velotrade_1` has two closed live round trips, and compares the first realised qualifying day to the rate above. Disclosure: a first version of the rule gated on P(net>0) ≥ 0.70 and P(pass) ≥ 0.50; it was re-scoped to the risk gate on the manager's 06:4xZ relay before any cell was graded (15 v1 cells ran, 4 SOL-solo cells were previewed, all discarded and re-run; v1 text in git history of the unit).
