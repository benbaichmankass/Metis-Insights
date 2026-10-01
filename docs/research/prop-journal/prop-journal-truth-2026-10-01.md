# PROP-JOURNAL — is the breakout_1 journal true before the first live ETH/SOL tickets are graded?

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · lane PROP-JOURNAL (session_013pigAqkqWXnSaZS8DmymUN). Tier 1: no config, sizing, roster or order-path change.

Live reads 2026-10-01 ~20:50Z: `/api/bot/prop/{fills,status,tickets}` (breakout_1) and two quote reads from issue comments #14999 / #15021.

## Dispositions

| # | item | disposition |
|---|---|---|
| 1 | fill #44 stop / observation | **Already true in the field; stale rows closed.** #44 (ETH short 1.3 @ 2666.11) carries `sl 2711.1` and a one-entry `amendments` trail (stop 2736.00 → 2711.10, 2026-09-24); its close is fill **#45** (exit 2711.10 = the amended stop, pnl −58.49 gross, commissions −2.80, 2026-09-25 09:28Z). `/prop/status` reads `open_risk.state = no_open_positions`, `stop_unknown_count = 0`. The `#44` row stays `status: filled` by design (open row + separate close row, the journal's pattern for every trade). Pipeline rows PI-20260924-BOEZJAFZ-0001 and -MQ3CDMU6-0003 closed `done`. |
| 2 | baseline residual −68.70 | **Re-measured and decomposed: −70.90 now; $48.57 explained, $22.33 UNVERIFIED.** See below. Stays open (narrowed). |
| 3 | PROP-EVIDENCE-BRACKET | **Confirmed: the evidence records grade exits the prop bracket never performs.** Record fixed (additive, regeneration-safe). **A finding for the manager: SOL's Stage-0 pass does not survive.** See below. |
| 4 | ETH-prop cost headroom | **Partly measured from live data; the remainder cannot be measured until an executor ETH fill closes (checked: docs/research/RESEARCH-CAPABILITY-INDEX.md, scripts/research/prop_cost_headroom.py — both price a modelled stack; none reads executor fills).** See below. |

## 2 — the residual

`(venue balance 4724.00 − 5000) = −276.00` vs `sum of journaled closed pnl = −205.10` over 22 closed fills (#52 removed: it is the executor's ESTIMATED close of the round trip that #53 re-reports with venue figures). Residual **−70.90**. The old −68.70 was 18 fills against a 4784.78 balance; it is the same shape.

| term | USD | provenance |
|---|---|---|
| commissions on gross-basis fills whose text states both legs (#14 #29 #41 #45) | −14.00 | MEASURED |
| commissions on gross/unknown-basis fills with no both-leg figure (#2 #11 #23 #25 #27 #38 #39), 2 × 4.0 bps × notional | −16.50 | ESTIMATED (4.0 bps/side is MEASURED on #29, #35, #41) |
| swap, one debit per midnight UTC crossed, 3.3 bps/crossing, 11 multi-night fills | −18.07 | ESTIMATED (rate MEASURED twice: #9 swap 1.96 = 1183 × 3.3 bps × 5 nights; #44 financing 1.17 = 3466 × 3.38 bps × 1) |
| **unexplained remainder** | **−22.33** | **UNVERIFIED — not folded into any measured term** |

What it could be, none verified: unjournaled trades or fills, crossing counts built from report timestamps rather than venue times, commission legs on the three "commissions −x" rows whose leg split is ambiguous, or a baseline other than $5,000. **It cannot be closed from the journal.** The feed has written `prop_account_status` every 5 min since 2026-09-28, but there is no history read path (only the latest row is served), and every journaled trade except the tests predates the feed. The next closed trade closes the question: balance-delta vs journaled pnl then gives its true net cost. Reproduce: `python3 scripts/research/prop_baseline_reconcile.py` (output committed as `breakout_1-baseline-reconciliation-2026-10-01.json`).

Two journal-hygiene facts the reconciliation surfaced: the closed rows mix gross-of-commission (#14 #27 #29 #38 #39 #41 #45) and net (#17 #19 #21 #31 #32 #33 #35) pnl, and #17's "net" also contains swap while #19's does not. Any journal-derived baseline is therefore a mixed-basis sum; do not quote it as net of costs.

## 3 — what the bracket does vs what the record models

- **Live bracket (code):** `src/prop/breakout_ticket.py` — "Do not manage the exit — the broker-side bracket is the exit." `execute.py` stamps the package `emitted`, which `order_monitor` ignores, so nothing trails it. `prop_executor.modify_bracket` is called only from `_contain`, to restore the original SL/TP. Close is manual-only (`--close-position`). Live closes agree: bracket SL (#19 #21 #29 #39), TP (#14) and operator manual closes; no trail or stale exit has ever fired.
- **Record:** `comms/strategy_evidence/trend_donchian_{eth,sol}_prop.json` are `fidelity: faithful`, which means the harness modelled every lever the YAML declares (chandelier trail 3.5, `stale_exit_bars 12`, trail-decay). Per-trade exits in the n=169 ETH record: `trail_stop` 89 (+41.43 R), `stale_stop` 36 (−10.37 R), `stop` 37 (−39.74 R), `take_profit` 7 (+22.29 R). **125 of 169 trades (74%) exit through levers the bracket does not execute.** SOL (n=65): `trail_stop` 43 (+8.21 R).
- **Bracket-faithful re-run** (`scripts/research/prop_bracket_exit_model.py`: same candles, trail/stale/decay off, no time exit; Binance Vision 1h, 2025-10-05 → 2026-09-30, venue-aware cost stack). The declared-lever baseline on this window reproduces the record's shape (ETH 168 trades, +10.63 R vs record 169, +13.61 R; the window is 5 days later, so not identical):

| leg | declared levers (record's model) | **SL + TP only (what the bracket does)** | quarters, SL+TP only |
|---|---|---|---|
| ETH | 168 trades, +10.63 R | **100 trades, +21.28 R** (76 stops −81.63, 24 TPs +102.90; fee-only +25.55) | −6.17, +19.98, +13.87, −6.40 |
| SOL | 65 trades, +3.69 R | **53 trades, −0.49 R** (41 stops −44.21, 12 TPs +43.72; fee-only +2.08) | −5.51, +0.42, +0.05, +4.54 |

- **Fix to the record:** `build_strategy_evidence.py` now attaches `venue_bracket_arm` (read from `comms/strategy_evidence/venue_bracket/<leg>.json`) to the record; both committed records carry it, `None` where unmeasured. The pooled `net_r_oos` is unchanged, since changing the Stage-0 number is not Tier 1.
- ⚠️ **For the manager.** (a) SOL's Stage-0 predicate (`net_r_oos > 0`) is positive only because of the trail it does not get; under the bracket it is −0.49 R (one window; not a verdict, not at the declared power). (b) `prop_ev_sim`/B6 V2 (+$150…+$162 ETH, indeterminate 5/5) consume the declared-lever trades, so they inherit the mismatch. (c) ETH looks better under the bracket, but the static run takes 100 trades, not 168, because positions stay open longer and suppress re-entries (the live `reticket suppressed, open_position` behaviour) — a different trade set, 24% win rate, TP-lumpy. Re-grading either leg is Tier-3 evidence work; filed, not done.

## 4 — ETH cost headroom, from live data

Modelled stack (cost-headroom-2026-09-27): flat all-in break-even **26.6 bps**; Breakout 8 + 3 (spread+slippage, ASSUMED) + swap 1.6 = 12.6 bps → ≈ 17 bps room.

| component | modelled | measured now | provenance |
|---|---|---|---|
| commission | 8 bps RT | 4.0 bps/side on 3 fills → **8 bps RT** | MEASURED |
| swap | 3.3 bps/day | 3.3 bps/night on 2 fills (#9, #44) | MEASURED (n=2) |
| ETHUSD spread | inside the 3 bps | bid/ask 2683.58/2683.59 (02:48Z) and 2697.73/2697.74 (04:06Z) = 1 tick = **0.037 bps**, i.e. ~0.02 bps/side at market | MEASURED, **n=2 quiet-hour quotes** |
| entry slippage vs signal price | inside the 3 bps | 16 manual fills vs ticket entry: mean +3.1 bps adverse, median −2.6, mean abs 26, sd 33 | MEASURED but **not a spread/slippage estimate**: human latency dominates; SE ≈ 8 bps |
| executor-fill slippage, ETH | — | **none exist** (ETH went live today; the only executor fills are 0.01-lot SOL tests with no quote beside the fill) | UNMEASURED |

Result: commission and swap are confirmed; the 3 bps spread+slippage allowance is **not** shown to be tight (spread is ~0.04 bps) but the market-order slippage that matters is unmeasured until the first executor ETH fill closes. Headroom on the pooled record stays ≈ 17 bps; on the last two folds (85 trades) it is 3.4 bps flat break-even, below commission alone (cost-headroom § 3), which this work does not change.
