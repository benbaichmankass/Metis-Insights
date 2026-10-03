# P2 round 2 — Breakout-listed symbols × corpus harnesses (2026-10-01)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current** · Tier-1 research, no config/src change, checklist row **P2**.

## Constraint and source
Candidates come ONLY from `https://www.breakoutprop.com/symbols/`. That page returns HTTP 403 (Cloudflare) to non-browser clients from this sandbox, so the list used is the operator's committed transcription, `docs/integrations/breakout-instruments-2026-09-27.md` (**unverified against the live page**). It names 17 crypto symbols (BTC ETH SOL BNB XRP HYPE TRX AAVE ADA FIL ONDO DOGE LINK LTC SUI UNI ZEC) plus ~50 unnamed small alts. The 4 non-crypto symbols are Terminal-only and unreachable on `breakout_1` (DXTrade), so they are out. AVAX is not named on the list, so it is NOT scoped (round 1's template mapped it anyway).

## Harness families used (full cost stack only)
`trend` (Donchian, donor `trend_donchian_eth_prop`, 1h), `ict15` (donor `ict_scalp_xrp_15m`), `pb2` (pullback 2h, donor `xrp_pullback_2h`). Excluded as having no full cost stack: `backtest_orb.py`, `src/backtest/backtester.py`, `backtest_xsec_momentum.py`, `backtest_vol_target.py` (CLAUDE.md). Donor configs are tuned on other symbols, so each new-symbol cell is an untuned transfer test; the multiplicity (49 cells) is carried into any proposal. Window 730 days (data.binance.vision); round 1's 365-day evidence left several cells at n < 88.

## Matrix (cell → unit RQ-20261001-NNN, or what already covers it)
| symbol | trend 1h | ict_scalp 15m | pullback 2h | ict_scalp 5m (committed trades) |
|---|---|---|---|---|
| BTCUSD | 001 | 002 | 003 | 048 |
| ETHUSD | covered (round 1: trend_donchian_eth_prop n=169, NULL) | covered (round 1: ict_scalp_eth_15m n=117, NULL) | 004 |  |
| SOLUSD | 005 | covered (round 1: ict_scalp_sol_15m n=132, NULL) | 006 | 049 |
| BNBUSD | 007 | 008 | 009 |  |
| XRPUSD | 010 | covered (round 1: ict_scalp_xrp_15m n=129, NULL) | 011 |  |
| HYPEUSD | 012 | 013 | 014 |  |
| TRXUSD | 015 | 016 | 017 |  |
| AAVEUSD | 018 | 019 | 020 |  |
| ADAUSD | 021 | 022 | 023 |  |
| FILUSD | 024 | 025 | 026 |  |
| ONDOUSD | 027 | 028 | 029 |  |
| DOGEUSD | 030 | 031 | 032 |  |
| LINKUSD | 033 | 034 | 035 |  |
| LTCUSD | 036 | 037 | 038 |  |
| SUIUSD | 039 | 040 | 041 |  |
| UNIUSD | 042 | 043 | 044 |  |
| ZECUSD | 045 | 046 | 047 |  |

The 5m column reuses committed 365-day evidence (`ict_scalp_5m` BTC n=193, `ict_scalp_sol_5m` n=259). Round 1 skipped both because it graded live-roster legs only; both are `execution: shadow`. `ict_scalp_xrp_5m` was a round-1 FAIL.

## Data census (730 d of 1h bars requested)
Checked symbols return the full 17,520 bars from 2024-10-01, except HYPE: 11,726 bars from 2025-05-30 (~489 days), so it is history-short. SUI, UNI, ZEC were still fetching when this was written.

## Rule (identical in every unit, registered before any run)
Gate A: n ≥ 88 and harness net_r > 0 pooled and in both chronological halves. Gate B: `prop_ev_grid.py`, `--costs breakout` (8 bps commission + 3 bps slippage round trip, 0.033%/day swap, dxtrade), 5 seeds, arms bal015 and room033, PASS iff ev > 0 and P(net>0) ≥ 0.70 on all 5 seeds. Driver: `scripts/research/p2_breakout_symbol_screen.py`.

## Results (2026-10-02): 49 of 49 graded, **0 PASS**
Run session-local with `scripts/research/p2_breakout_symbol_screen.py`; per-cell record in `docs/research/p2-breakout-r2-cells-2026-10-02.jsonl` and `research/results/RQ-20261001-*`.

| outcome | cells | meaning |
|---|--:|---|
| FAIL at Gate A | 31 | n >= 88 but harness net_r not positive pooled and in both halves |
| UNDERPOWERED | 1 | HYPE x pullback 2h, n = 83 (only ~489 days of history) |
| NULL at Gate B | 17 | cleared Gate A; ev > 0 on every seed, but P(net>0) < 0.70 on at least one seed under both arms |
| PASS | 0 | |

**Ranked shortlist of the 17 NULL cells** (closest to the 0.70 bar first; best-arm minimum P(net>0) over 5 seeds, harness trades net of fee + slippage + funding, re-priced under Breakout costs):

| rank | cell | n | net R (730 d) | bal015 P(net>0) | room033 P(net>0) |
|--:|---|--:|--:|---|---|
| 1 | ZECUSD trend 1h | 364 | +40.7 | 0.48-0.50 | **0.68-0.70** |
| 2 | HYPEUSD trend 1h | 201 | +15.0 | 0.34-0.36 | 0.58-0.59 |
| 3 | ONDOUSD ict15 | 279 | +17.5 | 0.42-0.44 | 0.56-0.57 |
| 4 | LINKUSD trend 1h | 343 | +27.0 | 0.21-0.23 | 0.55-0.56 |
| 5 | AAVEUSD ict15 | 279 | +13.6 | 0.33-0.34 | 0.50-0.52 |
| 6 | XRPUSD trend 1h | 357 | +31.0 | 0.27-0.29 | 0.50-0.52 |
| 7 | HYPEUSD ict15 | 193 | +8.4 | 0.32-0.34 | 0.49-0.52 |
| 8 | FILUSD trend 1h | 339 | +22.5 | 0.26-0.29 | 0.47-0.49 |
| 9-17 | XRP pb2, UNI trend, SUI ict15, SOL trend, ZEC ict15, ADA trend, SUI trend, DOGE ict15 | | | <= 0.42 | <= 0.47 |

Nothing clears. ZEC trend under room033 is the nearest (0.70 on at least one seed, below it on others), but its harness net R is lopsided across halves (+8.1 then +32.7), it is one of 49 screened cells, and `trend_donchian_eth_prop`/`ict_scalp_eth_15m` already sat at 0.43-0.63 in round 1 without qualifying. **No roster PR is proposed.** Caveats: donor configs are untuned transfer, the 49-cell multiplicity, and the Breakout symbols list is the unverified transcription.

Driver bugs found and fixed mid-run, rule unchanged: the ICT harness reads `config/strategies.yaml` by strategy name (cells now keyed by the donor leg's name), and `prop_ev_grid.py` needs trade files inside the repo.
