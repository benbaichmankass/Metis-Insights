# Dukascopy coverage — the adjudication for the 18 blocked symbols

**Date:** 2026-08-24 · **Evidence:** probe run
[`32748059443`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/32748059443)
on `main` @ `dd5955d` (the **fixed** matcher, #10226), catalogue size **1388**.
**Tier-1, observe-only.** No candles fetched, no config changed, no lane switched.

## Why this file exists

`.github/workflows/dukascopy-coverage-probe.yml` deliberately **dumps the
catalogue and refuses to declare coverage** — the symbol→instrument mapping is a
judgement, and a job that made it would be asserting a quantity it never
computed. That contract is right, and it has a consequence: **the judgement then
lives only in a job log**, which is not a record. Run 1's adjudication was lost
that way and had to be re-derived. This file is the durable half.

## What the fixed matcher changed

Run 1 (`32744577414`) was wrong twice, **in opposite directions**. Both are now
fixed and the fix is confirmed on the venue, not inferred:

| | run 1 | run 3 (fixed) |
|---|---|---|
| `MHG` | matched `INSTRUMENT_NORWAY_MHG_NO_NOK` — **Mowi ASA, a Norwegian salmon farmer**. Ours is CME Micro Copper. | flagged `NEEDS_ADJUDICATION`, and stated to have **no same-ticker ETF CFD** |
| `XAUUSD` | reported **unmatched** | found via the relaxed form → `INSTRUMENT_FX_METALS_XAU_USD` |

Totals: **probed 18 · exact 10 · relaxed 1 · unmatched 7.**

## The three states, kept apart

⚠️ **NONE of these is "covered".** A same-ticker US ETF CFD is the same
underlying but **still a CFD** — financing and fees differ from the ETF. A proxy
is a *different instrument*. Collapsing the two is the semantic substitution
`diagnostic-provenance-guard` sub-class A exists to stop.

### SAME-TICKER US ETF CFD — same underlying (10)

`GDX` · `GLD` · `IEF` · `IWM` · `QQQ` · `SLV` · `SPY` · `TLT` · `USO`
(9 via the exact form) — each → `INSTRUMENT_ETF_CFD_US_<TICKER>_US_USD`.

Near-miss hits correctly split out as `NEEDS_ADJUDICATION` rather than scored:
`GDXJ` (junior miners, not `GDX`), `SGLD` + `RGLD` (a different gold ETF, and
Royal Gold **the equity**), `CTLT` (Catalent, not `TLT`).

### MATCHED ONLY VIA THE RELAXED FORM (1)

`XAUUSD` → `INSTRUMENT_FX_METALS_XAU_USD`. Kept in its own bucket: the relaxed
form is a **guess about how the venue punctuates**, and a reader must see that it
was needed rather than have it silently equated with an exact hit.

### NO MATCH IN EITHER FORM (7) — adjudicated below

`IAUM` · `MES` · `MGC` · `QLD` · `SCHA` · `SPLG` · `TQQQ`

**Measured, not assumed:** each of the seven returns **0 hits against the whole
1388-line catalogue** in any substring form. So this is *"the venue does not
carry it"*, not *"our matcher missed it"* — the distinction run 1's `XAUUSD`
false negative exists to make.

## Adjudication of the 7

Every row below is a **PROXY**: a different instrument that tracks a related
underlying. **None is coverage.** The proxy column names what exists on the
venue; it is a candidate for a *separately justified* decision, never a
substitution made silently.

| symbol | what it is | nearest venue instrument | verdict |
|---|---|---|---|
| `MES` | CME Micro E-mini S&P 500 **future** | `INSTRUMENT_IDX_AMERICA_E_SANDP_500` (index CFD) | **PROXY** — CFD vs future: different venue, hours, financing, settlement, multiplier, no expiry/roll |
| `MGC` | COMEX Micro Gold **future** | `INSTRUMENT_FX_METALS_XAU_USD` (spot gold) | **PROXY** — spot vs future: no roll, different carry (contango/backwardation) |
| `SPLG` | SPDR Portfolio S&P 500 ETF | `INSTRUMENT_ETF_CFD_US_SPY_US_USD` | **PROXY, close** — same index, different share price + expense ratio |
| `IAUM` | iShares Gold Trust Micro | `INSTRUMENT_ETF_CFD_US_GLD_US_USD` | **PROXY, close** — same underlying (gold), different vehicle |
| `SCHA` | Schwab US Small-Cap ETF | `INSTRUMENT_ETF_CFD_US_IWM_US_USD` | **PROXY** — ⚠️ **a different index**: SCHA tracks Dow Jones US Small-Cap, IWM tracks Russell 2000 |
| `QLD` | ProShares Ultra QQQ (**2x**) | `INSTRUMENT_ETF_CFD_US_QQQ_US_USD` | ⚠️ **PROXY REFUSED** — see below |
| `TQQQ` | ProShares UltraPro QQQ (**3x**) | `INSTRUMENT_ETF_CFD_US_QQQ_US_USD` | ⚠️ **PROXY REFUSED** — see below |

### ⚠️ The two leveraged ETFs are a stronger statement than "PROXY"

`QLD` and `TQQQ` reset their leverage **daily**, so their price path is not
`N ×` the underlying's path — the deviation compounds with realised volatility
and is **path-dependent**. A backtest whose entries, stops and targets are read
off a path is precisely the consumer that difference breaks, and it breaks in a
direction that flatters nothing predictably.

So for these two, "substitute QQQ and scale" is **not a labelled approximation,
it is a wrong answer**, and they are recorded as refused rather than as a proxy a
later reader might quietly adopt. If these legs need history, the source has to
carry the leveraged instrument itself.

### `MHG` — matched, but its real proxy is elsewhere

`MHG` is in the exact-match bucket only because of the salmon farmer. Its actual
underlying is copper, and the venue **does** carry
`INSTRUMENT_CMD_METALS_COPPER_CMD_USD`. Recorded here so the next reader does not
repeat run 1's error in reverse — dismissing MHG as unavailable because the only
*named* hit was wrong.

## ⚠️ EXISTENCE IS NOT SPAN, AND SPAN IS THE ACTUAL QUESTION

This probe answers *"does the venue carry the instrument?"*. The blocked cells
are blocked on **history depth**: yfinance **refuses** a >730 d 1h request
outright (zero rows, proof run `32734360738`), and the 8 × 1h legs want ~1825 d.

**Nothing here has measured Dukascopy's span for any instrument.** A same-ticker
ETF CFD that carries 18 months of 1h history would not solve the problem it was
probed for. Treating this table as "the feed is fixed" would be exactly the
unstated-denominator move the probe's own contract warns about.

**Next input, if the operator wants this lane:** a span probe — fetch the
earliest available bar per instrument at 1h for the 10 same-ticker hits + the
`XAU_USD` relaxed hit, and report depth per instrument. That is a different job
from this one and should stay a different job.
