# M27 coverage table — symbol × timeframe (scalp family)

_Generated 2026-07-20 from `accounts.yaml` + `strategies.yaml` (24 symbols)._
_Every traded symbol has a row; a missing row is a milestone bug (no silent caps)._
_Dispositions: ✅ promoted/done · ⏳ pending · 📋 planned · 🚫 blocked/gated (with reason) · ❌ rejected (with numbers)._

| Symbol | Family | P0 batch | 5m | 15m |
|---|---|---|---|---|
| BTCUSDT | crypto | 1 | ✅ done (Phase-0 2026-07-20: gated leg LIVE; findings doc) | 📋 P1 (after the symbol's 5m cell resolves) |
| ETHUSDT | crypto | 1 | ✅ P0 done — PASS w/ off-cells gate (kfold net: off_cells 3/4, +12.1R, exp +0.059; ungated baseline 3/4 +20.4R). Weakest passer; findings doc | 📋 P1 (after the symbol's 5m cell resolves) |
| SOLUSDT | crypto | 1 | ✅ P0 done — STRONG PASS (kfold net: off_cells 4/4, +37.7R, exp +0.112; baseline 4/4 +62.7R even ungated). Findings doc | 📋 P1 (after the symbol's 5m cell resolves) |
| XRPUSDT | crypto | 1 | ✅ P0 done — PASS, gate load-bearing (baseline only 2/4; off_cells 4/4 +34.3R exp +0.144; off+conf070 4/4 exp +0.255). Findings doc | 📋 P1 (after the symbol's 5m cell resolves) |
| ADAUSDT | crypto | 1 | ⚠️ P0 done — MIXED: baseline 3/4 +55.1R exp +0.064 ungated, but the BTC-shape off-cells gate does NOT transfer (2/4, +3.9R). Needs own-evidence cells before any leg; findings doc | 📋 P1 (after the symbol's 5m cell resolves) |
| AVAXUSDT | crypto | 1 | ✅ P0 done — PASS (baseline 4/4 +60.8R exp .065 ungated; off+conf070 4/4 +19.2R exp .160; plain off-cells 2/4 does not transfer). Findings doc | 📋 P1 (after the symbol's 5m cell resolves) |
| MES | futures | 2 | ❌ rejected — UNDERPOWERED (16 trades/yr; gross +6.2R/69% but k-fold is 2-4 trades/fold = noise). Findings doc | 📋 P1 + diagnostic prerequisite (PB-20260721-M27-FUTURES-5M-LOWSIGNAL) |
| MGC | futures | 2 | ❌ rejected — UNDERPOWERED + gross-negative (14 trades/yr, −4.73R, 36% win). Findings doc | 📋 P1 + diagnostic prerequisite |
| MHG | futures | 2 | ❌ rejected — UNDERPOWERED + gross-negative (8 trades/yr, −0.33R, 38% win). Findings doc | 📋 P1 + diagnostic prerequisite |
| SPY | equity-base | 3 | ❌ rejected — UNDERPOWERED (2 trades/60d; yfinance data cap, not a setup verdict). Findings doc | 📋 P1 + data-source prerequisite (PB-20260721-M27-EQUITIES-DATACAP) |
| QQQ | equity-base | 3 | ❌ rejected — UNDERPOWERED (2 trades/60d; yfinance data cap, not a setup verdict). Findings doc | 📋 P1 + data-source prerequisite |
| IWM | equity-base | 3 | ❌ rejected — UNDERPOWERED (15 trades/60d, healthiest of the 9 — still n≤5/fold; baseline 3/4 folds +1.13R exp +0.094, not statistical). Findings doc | 📋 P1 + data-source prerequisite |
| TLT | equity-base | 3 | ❌ rejected — UNDERPOWERED (2 trades/60d; yfinance data cap, not a setup verdict). Findings doc | 📋 P1 + data-source prerequisite |
| GLD | equity-base | 3 | ❌ rejected — UNDERPOWERED (3 trades/60d; yfinance data cap, not a setup verdict). Findings doc | 📋 P1 + data-source prerequisite |
| SLV | equity-base | 3 | ❌ rejected — UNDERPOWERED (10 trades/60d, 2nd-healthiest — still n≤5/fold; baseline 3/4 folds +3.05R exp +0.381, not statistical). Findings doc | 📋 P1 + data-source prerequisite |
| GDX | equity-base | 3 | ❌ rejected — UNDERPOWERED (12 trades/60d; baseline 3/4 folds +0.99R exp +0.099, not statistical). Findings doc | 📋 P1 + data-source prerequisite |
| USO | equity-base | 3 | ❌ rejected — UNDERPOWERED + gross-flat (9 trades/60d, −0.08R, 44% win). Findings doc | 📋 P1 + data-source prerequisite |
| IEF | equity-base | 3 | ❌ rejected — UNDERPOWERED (1 trade/60d; yfinance data cap, not a setup verdict). Findings doc | 📋 P1 + data-source prerequisite |
| TQQQ | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| QLD | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| SCHA | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| IAUM | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| SPLG | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| XAUUSD | fx-metal | 4 | 🚫 gated-on-15m (5m not tested — 15m was this symbol's primary thread per the M15 Phase-0 lead) | ✅ P0/P1 done — STRONG PASS, ungated (kfold net: baseline 4/4, +44.35R, exp +0.1848; off-cells gate underperforms baseline here, unlike BTC/ETH/XRP). Re-validates M15 Phase-0 (+39R/+10R single-split) under k-fold. **Promotion venue-blocked**: OANDA US can't trade XAU_USD (BL-20260611-007), `oanda_practice` shelved 2026-06-12 — no live-tradeable venue yet. Findings doc |

## Standing blocked-with-reason rows

- **Prop bridge (breakout_1 ETH/SOL legs) @ 5m** — manual ticket latency is incompatible with scalp fills; revisit at 15m+ only.
- **Options expression (alpaca_options_paper SLV/GDX) as a scalp vehicle** — DTE-banded debit verticals don't map to 5m scalps; the underlyings are tested as equities above.
- **1m timeframe** — added per symbol in P1 only where data quality supports it; not enumerated until then.

## Log

- 2026-07-21 (late) — Batch-4 XAUUSD 15m results landed (Dukascopy, 178,466 bars 2019-2026, trainer relays #7295/#7302-7305): ✅ STRONG PASS, ungated baseline 4/4 folds, +44.35R net, exp +0.1848R/trade — re-validates the M15 Phase-0 finding under k-fold discipline. Promotion is venue-blocked (OANDA US / XAU_USD), not evidence-blocked. Findings doc; required adding a `--timeframe` passthrough to `run_symbol_p0.py` (PR #7293) for native-15m (non-5m-derived) data.
- 2026-07-21 — Batch-3 equities results landed (trainer relay #7271, yfinance data via #7259/#7263): all 9 symbols ❌ rejected, underpowered by data cap (1–15 trades/60d each). IWM/SLV are the least-starved but still not statistical. Findings doc; `PB-20260721-M27-EQUITIES-DATACAP` opened for a future deep-history data-source decision.
- 2026-07-21 (early) — Batch-2 futures results landed: MES/MGC/MHG ❌ rejected, underpowered (8–16 trades/yr). Findings doc; `PB-20260721-M27-FUTURES-5M-LOWSIGNAL` opened.
- 2026-07-20 (late) — Batch-1 attempt-3 results landed (tz-fixed CSVs, #7211/#7212): ETH/SOL/XRP PASS, ADA mixed, AVAX PASS (appended 23:33Z; chain ALL_DONE 23:27Z). Per-symbol frozen 2023-only vol specs derived on the trainer; full fold JSONs in the findings doc + relay issues.
- 2026-07-20 — table initialized (M27 kickoff). BTCUSDT 5m carried over from Phase 0. Batch-1 crypto data pull dispatched to the trainer.
