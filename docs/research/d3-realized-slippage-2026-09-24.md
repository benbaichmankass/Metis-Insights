# D3: realized slippage per venue, and how many Stage-0 verdicts flip

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Date:** 2026-09-24 · **Checklist row:** D3 · **Tier:** 1 (measurement only).
This lane did not change `execution_costs.py`, anything under `config/`, or any
`comms/strategy_evidence/*.json` record.

**Record:** [`comms/research/d3_realized_slippage/2026-09-24.json`](../../comms/research/d3_realized_slippage/2026-09-24.json)
(summary, distributions, per-leg re-price) and `…__rows.jsonl` (one row per
measured entry or exit, with the reference price and the fill). **Re-run it:**

```
python3 scripts/research/realized_slippage.py pull --out-dir /tmp/d3        # needs DIAG_READ_TOKEN
python3 scripts/research/realized_slippage.py analyze --in-dir /tmp/d3 --out /tmp/d3/out.json
```

## The answer

| venue | real-money basis | round-trip slippage (bps) | 95% CI | verdict |
|---|---|---|---|---|
| **Bybit perps** | `bybit_2`: 87 entries (2026-06-29 to 09-24) + 42 sl/tp exits (2026-06-01 to 09-24) | **+0.87** (mean) | **[-0.91, +2.95]** | **MEASURED**. The flat 5.0 is above the CI, so it overstates the cost |
| Alpaca equities | `alpaca_live`: 3 entries, 0 measured sl/tp exits | none | none | **unmeasurable**: too few fills, and the exit side can't be measured (see below) |
| IBKR futures | `ib_live` is `dry_run`, 0 fills | none | none | **unmeasurable** |
| Prop (Breakout) | `breakout_1`: 0 fills in the store | none | none | **unmeasurable** |

**Stage-0 flips under `RULE-D1-STAGE0-NET-OF-FULL-COST`** (`net_r_oos > 0`).
Only perp legs can be re-priced: 26 of the 27 perp records among the 49
`measured` ones. `fvg_range_15m` has neither per-trade rows nor a fee-only arm.
The 22 equity and futures records stay on the unmeasured 5.0.

| slippage used for perps | pass→fail | fail→pass | undetermined |
|---|---|---|---|
| 0.87 bps (point estimate) | **0** | **3**: `ict_scalp_5m`, `ict_scalp_avax_5m`, `ict_scalp_xrp_5m` | 2: `eth_pullback_2h`, `trend_donchian` |
| 2.95 bps (CI upper) | **0** | **0** | 1: `trend_donchian` |

**Conclusions (INFERRED from the table above):**

1. **No pass→fail flips under any measured value.** No leg that passes today
   needs slippage to be lower than it actually is on Bybit. For perps, the
   worry that the corpus is optimistic on slippage is answered: 5.0 bps is
   pessimistic.
2. **The three fail→pass flips are not robust.** Their break-even slippage
   (0.87, 1.83 and 2.79 bps) is inside the measured CI, and none of them
   flips at the CI upper. These are 5m scalp legs with a narrow stop, so each
   bp of slippage is a large fraction of R. They need a tighter slippage
   estimate before anyone reads them as passes. `ict_scalp_5m`'s break-even
   is exactly the point estimate: its new net is +0.02R over 193 trades.
3. **For equities and futures the corpus is still priced on an assumption.**
   It is not measured.

## Method

**Reference price.** Slippage is measured against the price the Stage-0
harness assumes it gets:
- **Entry.** Reference = `order_packages.entry`, the intended entry the order
  was sized on. Fill = qty-weighted VWAP of the `exchange_fills` rows whose
  `order_id` equals the trade's `broker_order_id`. These are venue-reported
  fills, so MEASURED. `scripts/backtest_ict_scalp.py:537` reads the same
  `pkg["entry"]`. For the other harness families it is **INFERRED, not
  checked per harness**, that they enter at the level the package records.
- **Exit.** Only closed trades whose `exit_price_source` classifies MEASURED
  in `src/runtime/provenance.py` count, and only if the exit has a level the
  harness also fills at: `sl`/`sl_cross` → `order_packages.sl`, which is the
  last stop after any trail. `tp`/`tp_cross` → `order_packages.tp`. Every
  other exit (reconciler, watchdog, reduce, netted, market close: 1,728 rows
  in total: 1,348 not MEASURED, 380 MEASURED with no reference level) has no fixed reference and is excluded and counted, never priced.
- Sign: positive = adverse. The unit matches `DEFAULT_SLIPPAGE_BPS_ROUNDTRIP`:
  bps, entry side + exit side.

**Headline uses package-referenced exits only.** 144 of bybit_2's MEASURED
sl/tp exits are from May 2026, before packages existed. The fallback reference
for those, `trades.stop_loss`/`take_profit_1`, agreed with the package on 290
of 298 closes where both exist. The disagreements are trailed stops that one
side did not record, and they read as large favourable fills (min −37.9 bps).
Including them gives −0.39 bps with CI [−2.20, +1.62]. That is reported in the
record as `roundtrip_incl_journal_ref` and is not used.

**Bybit perps distribution** (`bybit_2`, all taker fills):

| side | n | mean | median | p75 | p90 |
|---|---|---|---|---|---|
| entry | 87 | −0.25 | −2.13 | +4.32 | +7.48 |
| exit, stop (sl) | 33 | +1.56 | +0.71 | +1.61 | +4.31 |
| exit, take-profit (tp) | 9 | −0.49 | −0.14 | −0.01 | 0.00 |

Entry slippage is wide and centred near zero. It measures drift between package
creation and the market fill (latency), not a spread cost. The cost signal is
on the stop side: about +1.5 bps adverse per stop-out.
**Symbols covered:** BTC 38, ETH 24, XRP 17, ADA 8 entries. **There are no real
SOL or AVAX fills.** Applying the venue figure to the SOL and AVAX legs is an
inference.

**Paper/demo fills, reported separately and never used in the re-price.**
These measure a simulator's fill model, not a market:
`bybit_1` (Bybit demo) round-trip −0.34 [−2.06, +1.22], n=947 entries / 88
exits. It agrees with the real `bybit_2`.
`bybit_portfolio`: 63 entries / 16 exits, below n=20, unmeasurable.
`alpaca_paper` and `alpaca_portfolio`: entry means +1.4 and +0.3 bps (n=48 and
60), 0 measured exits.
`ib_paper`: entry +8.1 bps on 22 maker (limit) fills, 7 exits.

**Positive control, run before any input was changed.** For every perp record
with durable per-trade rows, Σ`net_r` over its `source_run` rows reproduces the
record's `net_r_oos` at 5.0 bps exactly. This holds for 37 of 37 records
corpus-wide (18 of them perps). The per-row identity `net_r_fee_only = net_r + slippage_r +
funding_r` holds to ≤0.0012R per record. Re-pricing is linear:
`new = net + slip_r·(1 − bps_new/5.0)`. No harness re-run is needed.
**12 records have no durable per-trade rows**: their `source_run` is a deleted
`/tmp` dir, already filed as
`PI-20260922-R5-PATH-STATS-MISSING-FOR-THE-12-LEGS-WHOSE-SERIES-IS-IN-A-DEAD-TMP-DIR`.
For 8 of the 9 perp ones among them, the fee-only-minus-net difference mixes
slippage and funding, so the result is an interval. The 9th is `fvg_range_15m`,
which has no fee-only arm and is not re-priced. Verdicts come out `undetermined`
when the interval straddles 0.

An arithmetic check caught a sign error in the first draft of the re-price: it
lowered net R when slippage was lowered. The fix was checked against the
break-even column.

## What this could NOT establish

- **Alpaca exit slippage can't be measured by this method, however many trades
  accrue.** No Alpaca sl/tp close carries a MEASURED exit source.
  `exchange_fill` appears only on `exchange_flat_reconciled` closes, which have
  no reference level. Filed as `PI-20260924-A87KVAFC-0002`.
- **Population limits of the pull.** The fills endpoint caps pages at 1,000.
  Truncated accounts were re-pulled per symbol, but only for symbols seen in
  the first page. That affects `bybit_1` (paper), and `ib_paper` MGC stayed
  truncated at 1,000 (paper). `bybit_2` was not truncated: 315 fills. 270 trade
  rows sit on accounts no longer in `config/accounts.yaml` and were excluded.
- **Missed-fill cost is not in this number.** All `bybit_2` entries are taker
  fills, so there is no limit-order survivorship bias here. It would matter for
  `ib_paper`, whose entries are maker fills.

## Follow-ups filed

- `PI-20260924-A87KVAFC-0001` (ask_operator): proposes per-venue values for
  `slippage_bps_roundtrip_for`. Perps **3.0** (the CI upper, rounded up).
  Equities and futures stay **5.0** until measured.
- `PI-20260924-A87KVAFC-0002` (check_observation): equities and futures are
  unmeasurable, including the structural Alpaca exit gap.
