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
| AVAXUSDT | crypto | 1 | ⏳ pending (Batch 1 — trainer pull dispatched) | 📋 P1 (after the symbol's 5m cell resolves) |
| MES | futures | 2 | ⏳ pending (Batch 2 — IBKR history pulls, session-aware) | 📋 P1 (after the symbol's 5m cell resolves) |
| MGC | futures | 2 | ⏳ pending (Batch 2 — IBKR history pulls, session-aware) | 📋 P1 (after the symbol's 5m cell resolves) |
| MHG | futures | 2 | ⏳ pending (Batch 2 — IBKR history pulls, session-aware) | 📋 P1 (after the symbol's 5m cell resolves) |
| SPY | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| QQQ | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| IWM | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| TLT | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| GLD | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| SLV | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| GDX | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| USO | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| IEF | equity-base | 3 | ⏳ pending (Batch 3 — Alpaca intraday bars) | 📋 P1 (after the symbol's 5m cell resolves) |
| TQQQ | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| QLD | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| SCHA | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| IAUM | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| SPLG | equity-levered | 3 | 🚫 gated-on-base (tests only if its base index passes) | 📋 P1 (after the symbol's 5m cell resolves) |
| XAUUSD | fx-metal | 4 | ⏳ pending (Batch 4 — OANDA; primary thread is 15m/P1) | ✅ M15 survivor (+39R/+10R) — re-validate config-exact (P1) |

## Standing blocked-with-reason rows

- **Prop bridge (breakout_1 ETH/SOL legs) @ 5m** — manual ticket latency is incompatible with scalp fills; revisit at 15m+ only.
- **Options expression (alpaca_options_paper SLV/GDX) as a scalp vehicle** — DTE-banded debit verticals don't map to 5m scalps; the underlyings are tested as equities above.
- **1m timeframe** — added per symbol in P1 only where data quality supports it; not enumerated until then.

## Log

- 2026-07-20 (late) — Batch-1 attempt-3 results landed (tz-fixed CSVs, #7211/#7212): ETH/SOL/XRP PASS, ADA mixed, AVAX pending at write time. Per-symbol frozen 2023-only vol specs derived on the trainer; full fold JSONs in the findings doc + relay issues.
- 2026-07-20 — table initialized (M27 kickoff). BTCUSDT 5m carried over from Phase 0. Batch-1 crypto data pull dispatched to the trainer.
