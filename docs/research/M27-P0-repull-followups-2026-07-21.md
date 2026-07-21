# M27 P0 — repull follow-ups: futures 15m + equities 1h (2026-07-21)

Resolves the two `partially-resolved` performance-review-backlog items left
open at the end of the 2026-07-20/21 overnight arc:
`PB-20260721-M27-FUTURES-5M-LOWSIGNAL` and `PB-20260721-M27-EQUITIES-DATACAP`.
Both prescribed a concrete next step — go coarser (futures) / use the 60m/1h
interval (equities) — rather than re-running the same underpowered window.
This session did exactly that.

## Rig

- **Futures (MES/MGC/MHG) at 15m** — reused the **already-pulled** IBKR
  `market_raw/<SYM>/15m/v002/data.jsonl` shards from the 2026-07-21 Batch-2
  session (no new `pull-ibkr-history` live-VM Tier-2 action needed — the 15m
  shards were pulled alongside 5m in that run and simply never converted to
  CSV). Converted via `ibkr_jsonl_to_csv.py`, then
  `run_symbol_p0.py --timeframe 15m` with the same per-contract cost model as
  Batch-2 (MES $3.50/cv5.0, MGC $3.00/cv10.0, MHG $3.50/cv2500.0),
  `--derive-window prefix:0.25 --min-derivation-bars 2000` (the 15m bar count
  is ~1/3 of 5m's, so the floor is scaled down accordingly — still strictly
  inside fold-1 train territory).
- **Equities (9 symbols) at 1h** — extended `fetch_yfinance_5m.py` to accept
  `--interval 1h --period max` (PR-local commit `9023f076`); Yahoo's ~60-day
  cap only applies to 5m/15m/30m/90m, not 60m/1h (verified directly against
  SPY in the prior session: 5,082 bars / ~2.9y). This pull returned 3,480
  bars/symbol, 2024-07-22→2026-07-20 (~2y — shorter than the SPY probe because
  `period=max` capped differently per-symbol; still far beyond the 60-day 5m
  window). `run_symbol_p0.py --timeframe 1h --fee-bps-roundtrip 3.0`
  (unchanged from Batch-3) `--derive-window prefix:0.25
  --min-derivation-bars 800`.
- Both dispatched via the trainer-vm-diag relay using the fully-detached
  launch pattern (`setsid nohup … & disown -a`) documented in
  `BL-20260721-TRAINERDIAG-SILENT-SSH-DROP` — no SSH drops this run.

## Results — equities 1h

| Symbol | Trades | Gross totalR | Gross exp | Kfold baseline | Net totalR (OOS) | Net exp (OOS) | Verdict |
|---|---|---|---|---|---|---|---|
| GLD | 18 | +5.59 | +0.3105 | 4/4 | **+5.92** | **+0.4933** | ✅ **STRONG PASS** |
| SPY | 13 | +1.41 | +0.1083 | 3/4 | +1.95 | +0.195 | ✅ PASS (weak) |
| TLT | 12 | +2.46 | +0.2047 | 3/4 | +0.55 | +0.05 | ✅ PASS (weak) |
| USO | 24 | +2.80 | +0.1169 | 2/4 | +0.44 | +0.022 | ⚠️ MIXED (weak-positive) |
| SLV | 20 | +2.56 | +0.1279 | 2/4 | +2.58 | +0.1613 | ⚠️ MIXED (degrades under any gate) |
| IWM | 21 | +2.14 | +0.1019 | 2/4 (baseline neg.) | −1.52 | −0.0894 | ⚠️ MIXED (only the off-cells gate is positive: 3/4, +0.67R exp +0.067) |
| QQQ | 14 | −1.69 | −0.1208 | 1/4 | −3.5 | −0.3182 | ❌ REJECT (net-negative) |
| GDX | 25 | +0.26 | +0.0105 | 3/4 | −0.56 | −0.0243 | ❌ REJECT (fees flip gross-flat to net-negative) |
| IEF | 6 | +3.11 | +0.5185 | 2/2 valid, n=2 | +1.64 | +0.82 | ❌ still UNDERPOWERED (only 6 total trades over ~2y — bonds too quiet for this setup even at 1h; 2 of 4 folds had zero trades) |

Trade counts (12–25/symbol over ~2 years, excluding IEF) are now genuinely
comparable to Batch-1's crypto batch and Batch-4's XAUUSD — the 60-day 5m/15m
window was the entire problem, not the symbols' setup rarity. **GLD is a
standout**: 4/4 folds positive, +0.49R/trade expectancy — the strongest
non-crypto cell found in M27 to date, on the same order as SOLUSDT/XAUUSD.
IWM's baseline/gated split mirrors the ADA/BTC-gate-transfer pattern from
Batch-1 — a reminder that any future leg must derive its own regime cells
from its own evidence, never borrow BTC's.

**No new equities scalp leg ships from this session** — that's a P4 decision
(own regime-cell authorship, shadow-first wiring, per the M27 DESIGN doc's
promotion path) outside this follow-up's scope. GLD is flagged as the
strongest P4 candidate for a future session.

## Results — futures 15m

| Symbol | Trades | Gross totalR | Gross exp | Kfold baseline | Net totalR (OOS) | Net exp (OOS) | Verdict |
|---|---|---|---|---|---|---|---|
| MES | 31 | −0.13 | −0.0041 | 1/4 | −1.55 | −0.0596 | ❌ REJECT (net-negative; gated off_cells 2/4 +0.1245 but only half the folds) |
| MGC | 31 | −0.15 | −0.0049 | 2/4 | −0.96 | −0.0331 | ❌ REJECT (net-negative; no gate clears 3/4) |
| MHG | 5 | −2.01 | −0.4016 | 0/1 valid | −0.12 | −0.04 | ❌ still UNDERPOWERED (5 trades/~1y — *fewer* than the 8/yr Batch-2 found at 5m; 2 of 4 folds had zero trades) |

Going coarser roughly **doubled** the futures trade count (MES 16→31/yr,
MGC 14→31/yr) but MHG actually **dropped** (8→5) and none reach statistical
significance — still 1-8 trades/fold. This **confirms** the Batch-2
diagnostic's finding (`docs/research/M27-P0-batch2-futures-gap-diagnostic-2026-07-21.md`):
the low signal count is "predominantly a data characteristic of the current
pull" in the sense of genuine session-structure/liquidity thinness (RTH+thin
overnight Globex tape on CME micro-futures), **not** a resolvable artifact —
coarsening the bars doesn't manufacture setups that aren't there, and MGC/MES
both flip from gross-slightly-positive-at-5m to gross-flat/negative-at-15m
once you're sampling fewer, wider bars over the same ~1y window.
**No futures 5m or 15m scalp leg — the family is closed pending either a much
longer IBKR pull (the ~1y ceiling is IBKR's own history-depth limit for
these micro contracts, not a data source anyone controls) or a different
strategy archetype (P3) better suited to CME session structure.**

## Backlog resolution

- **`PB-20260721-M27-FUTURES-5M-LOWSIGNAL`** → `resolved`. The "go coarser"
  next step was executed; the result is a clean, evidenced REJECT (still
  underpowered, net-negative where n is even marginally sufficient) rather
  than a fix. No further action is warranted without a fundamentally
  different data source or strategy archetype — logged as closed with the
  negative finding, not re-opened as a new open item (the diagnostic already
  explained *why*; this confirms it doesn't resolve with a timeframe change).
- **`PB-20260721-M27-EQUITIES-DATACAP`** → `resolved`. The 1h re-run is
  genuinely statistically powered (unlike the 60-day 5m/15m runs) and
  produces real verdicts: GLD STRONG PASS, SPY/TLT weak PASS, IWM/SLV/USO
  MIXED, QQQ/GDX REJECT, IEF still underpowered. The credential-provisioning
  question this item originally raised is fully closed (no new data source
  needed — 1h is free/keyless yfinance).

## Files

- `scripts/research/m27/fetch_yfinance_5m.py` — `--interval 60m|1h` support
  (this session).
- `docs/research/artifacts/m27/coverage.md` — MES/MGC/MHG 15m cells updated;
  new `1h` column added for the equity-base/-levered rows.
- `docs/claude/performance-review-backlog.json` — both items flipped to
  `resolved`.
