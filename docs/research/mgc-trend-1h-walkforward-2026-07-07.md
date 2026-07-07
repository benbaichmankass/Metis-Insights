# mgc_trend_1h — window-aligned walk-forward (2026-07-07)

**Author:** Claude. **Status:** research finding. **No config change** — this
sharpens WHY `mgc_trend_1h` stays `shadow`; it is not a promotion.

## The question this closes

`mgc_trend_1h` (`trend_donchian` @1h on gold) has three continuous 1h series
that disagree by **sign**:

- **native MGC futures continuous (panama)** → **+196.2R** over 2023-03→2026-07
  (`ib-metals-native-backtest-2026-07-07.md`, #5893) — the roll-artifact
  hypothesis was refuted there (spliced +221.6R → continuous +196.2R, only −11%).
- **GC=F (yfinance futures) 1h** → **−15.5R** over 2024-01→2026-06
- **XAUUSD spot 1h** → **−50.7R** over 2024+
  (both from `recombination-sweep-2026-06-18.md`, the original shadow demote)

The open question the metals note flagged: is the native-MGC positive result just
**2023-concentration**? The native +196R has **+120R sitting in 2023 alone**, and
the GC=F/spot demote windows *start in 2024* — so they never saw 2023. If you
remove 2023, does native MGC collapse toward the proxies' negative, or does it
stay positive? If it stays positive, the disagreement is **structural** (vendor /
session / instrument), not a windowing artifact.

## Method

One series, one param set, one fee basis — only the **window** changes. Native
MGC per-contract shard (`market_raw_percontract/MGC/1h/v001`, 86,075 per-contract
bars) → `build_continuous_contract.py --method panama` → **15,003 continuous 1h
bars** (2023-03-21 → 2026-07-06, 14 stitched contracts). Then
`scripts/research/backtest_trend.py --donchian 20 --atr-period 14 --atr-stop-mult
2.5 --trail-mult 3.0` (net-of-fee; the harness bakes **7.5bps**, ≈7× the ~1bp
micro-gold round-trip — deliberately conservative), sliced with the harness's
native `--start/--end`. Live `mgc_pullback_1d`-family params, **not tuned to 1h**.

## Result — native MGC continuous, by window

| Window | Trades | Win% | Net R | Long / Short |
|---|---|---|---|---|
| **FULL** 2023-03→2026-07 | 680 | 37.9% | **+196.2** | +112.2 / +84.1 |
| **ALIGNED** 2024-01→2026-06 | 570 | 37.7% | **+77.0** | +68.4 / +8.6 |
| — 2024 | 250 | 32.8% | **−26.8** | −27.7 / +0.9 |
| — 2025 | 221 | 41.2% | **+71.0** | +81.7 / −10.7 |
| — 2026-H1 | 96 | 42.7% | **+31.9** | +12.5 / +19.4 |

(Per-year sum −26.8 + 71.0 + 31.9 = **+76.1R** ≈ the aligned +77.0R — the small
gap is boundary trades straddling year edges. Consistent.)

## The head-to-head, on the SAME 2024-01→2026-06 window

| Series (gold 1h, `trend_donchian` dc20/atr14/stop2.5/trail3.0, net-of-fee) | Net R |
|---|---|
| **Native MGC futures continuous (panama)** | **+77.0** |
| GC=F (yfinance futures) | **−15.5** |
| XAUUSD spot | **−50.7** |

On the exact window the demote used — **2023 fully excluded** — native MGC
continuous is **still +77.0R**, disagreeing by *sign* with both proxy series by
a wide margin (a +77 vs −15/−51 gap can't be closed by a fee-basis difference).

## Verdict

1. **The 2023-concentration hypothesis is REFUTED as the sole cause.** Removing
   2023 entirely, native MGC continuous stays **net-positive (+77R)**. The
   cross-series sign disagreement is therefore **structural** — a difference in
   the *instrument/vendor/session* the three series represent (native COMEX MGC
   micro ~23h/session vs GC=F full-size Yahoo futures vs 24h XAUUSD spot), not an
   artifact of which years each window happened to span.

2. **But the native edge is regime-dependent, not clean.** Even in the aligned
   window, **2024 is −26.8R**; the +77R is carried by 2025 (+71R) and 2026-H1
   (+31.9R). So native MGC is "positive but with a losing year," not a uniformly
   positive multi-regime edge.

3. **`mgc_trend_1h` STAYS `shadow` — sharpened reason.** The demote is now
   defended by neither of the two hypotheses that were tested and fell:
   - **NOT a roll artifact** (refuted #5893: −11% roll impact).
   - **NOT merely 2023-concentration** (refuted here: +77R with 2023 removed).

   It stays shadow for the surviving reason: an **unresolved structural
   cross-series conflict**. You cannot promote a cell on one series (native MGC,
   +77R) when the two other legitimate representations of the same underlying
   (GC=F −15.5R, spot −50.7R) are negative on the identical window — the
   disagreement means we do not yet understand which series a *live* MGC-micro
   fill will actually track. Promotion needs that resolved first, plus the native
   series' own 2024 losing year explained.

## Proposed next step (Tier-3, operator-gated — NOT enacted here)

To resolve the structural conflict before any promotion consideration:

- **Isolate the driver.** Re-pull GC=F and XAUUSD 1h on the *native* window
  bounds and compare bar-session structure (RTH vs 23h vs 24h), then re-run all
  three under a common session mask. If native-vs-GC=F reconciles under a matched
  session, the conflict is a session-structure artifact and the native (tradeable)
  series is the trustworthy one; if it persists, the micro-contract's own
  liquidity/tape is the edge and needs a paper soak, not a backtest, to trust.
- Until then `mgc_trend_1h` remains `shadow` (observe-only); **no
  `strategies.yaml` change**.

## Provenance

Native MGC continuous numbers: trainer-vm-diag run
`actions/runs/28893076178` (2026-07-07), `build_continuous_contract.py` (#5870) +
`backtest_trend.py`. Proxy demote numbers: `recombination-sweep-2026-06-18.md`.
Roll-artifact refutation: `ib-metals-native-backtest-2026-07-07.md` (#5893).

> **Note on the harness label.** `backtest_trend.py` prints a cosmetic
> `trend_donchian — MES 1d` banner regardless of the input series; the data here
> is MGC 1h continuous (680 trades over 15,003 1h bars is 1h cadence, not 1d).
> Label-only artifact, not a data error.
