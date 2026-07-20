# ict_scalp_5m — Phase 0 findings: honest baseline + clean per-cell dataset (2026-07-20)

**Status:** Phase 0 of
[`ict_scalp_5m-modernization-research-plan-2026-07-20.md`](./ict_scalp_5m-modernization-research-plan-2026-07-20.md)
— COMPLETE. Research only (Tier-1); no config or live-path change is made or
proposed for direct enactment here. Owner item: `PB-20260630-ICTSCALP-DEGRADE`.

**Artifacts:** `docs/research/artifacts/ict_scalp_phase0/` — the compact live
dataset (52 order packages + 97 trade legs extracted from the trainer's synced
`trade_journal.db`, 2026-07-20 07:57 UTC pull), the frozen BTCUSDT/5m vol spec,
both backtest summaries, gzipped per-trade JSONLs (regime-stamped, MFE/MAE),
and the combined per-cell JSON. Tooling:
`scripts/backtest_ict_scalp.py` (new additive flags `--stamp-regime`,
`--vol-spec-json`, `--sim-breakeven`; emit rows now carry
`mfe_r/mae_r/bars_held/exit_time/exit_price/tp`) +
`scripts/research/ict_scalp_phase0/{build_percell,stamp_vol_post,volspec_probe}.py`.

## Headline: the −467R demotion baseline does not reproduce, and no artifact of it can be found

The demotion note (config/strategies.yaml, landed in the 2026-07-15 squash
#6447) rests on: *"5-year backtest −0.99R/trade (−467R); a min_confidence sweep
found no floor that salvages net_R."* Phase 0 could not reproduce either claim
and could not locate the run's artifact:

- **Fresh config-exact backtest, canonical data** (qashdev/btc 5m, 2023-01-01 →
  2026-02-28, 332,624 bars — the longest archive reachable; the "5y" window of
  the original claim exceeds the canonical 3.2y archive, which is itself a
  red flag for that run's data provenance), current YAML params:

  | Run | Exit model | n | WR (gross) | total R (gross) | exp R (gross) | maxDD |
  |---|---|---|---|---|---|---|
  | A "legacy" | static SL/TP + 24-bar timeout (the harness the demotion cites) | 659 | 51.1% | **+117.5R** | **+0.178** | 13.3R |
  | B "live-exit" | + `monitor_breakeven_sl` BE-trail @1R (+15bps), 288-bar timeout | 615 | 53.5% | **+122.5R** | **+0.199** | 17.8R |

  Both **gross-positive** — nowhere near −0.99R/trade. The trainer's own two
  config-exact re-validations (2026-05-17 and 2026-06-09, 90-day windows,
  `/home/ubuntu/ict-trader-data/backtests/*/ict_scalp_metrics.json`) are also
  positive (+20.6R / 54 trades / 59.3% WR — matching the PR #1156 pre-live
  gate). A trainer-wide search (`strategy_tunes/` is empty, `results/` has no
  July ict_scalp outputs, no file greps to −467) found **no artifact** of the
  −467R run.
- **Verdict:** the demotion's *magnitude* rested on an unreproducible
  measurement. The config-inexactness hypothesis (v1 unit defaults instead
  of the YAML block — the SOL-tuning lesson) is **refuted**: a
  `--ignore-yaml` run (Run C, `runC_ignoreyaml.json`) is bar-for-bar
  identical to Run A (659 trades, +117.5R — the unit defaults equal the
  live YAML for every consumed field). Remaining candidates: a
  different/broken data feed or window, or a harness regression since
  fixed. Whatever it was, it is not reachable from current code + canonical
  data.

## The real structural problem is FEE LOAD, not gross R:R

The harness is gross-only (no fee model — BL-20260610-M15-1). Charging the
standard 7.5bps round-trip per trade against each trade's own risk geometry:

- ict_scalp's stops average ≈0.4% of price, so 7.5bps of notional ≈ **0.20R
  of fees per trade** — enormous for a scalp.
- Run A net: **−12.9R** (exp −0.020R/trade). Run B net: **−1.7R** (exp
  −0.003R/trade). i.e. **net-flat, not catastrophically negative.**

So: "wins small, lets losses run" is not what the clean data shows (gross
avg win 1.23R vs avg loss −0.98R in Run B). The strategy grinds out a real
~+0.2R/trade gross edge and hands ~all of it to the exchange. That is the
honest restatement of the "structural R:R problem".

## Per-(trend, vol) cell table — the honest replacement for the M7 figure

Decision-time stamps: ADX-14 trend axis (`detect_regime`) + frozen registry
vol spec `btc-regime-5m-baseline-v1` (edges [0.000836, 0.001401], window 20,
`rolling_log_return_vol`; verified consistent with every live-stamped row).
Backtest cells (net = gross − per-trade fee R at 7.5bps):

**Run B (live-exit faithful), 2023-01 → 2026-02:**

| cell | n | WR | expR gross | expR net | totR net | med MFE | med MAE |
|---|---|---|---|---|---|---|---|
| chop/calm | 59 | 57.6% | +0.317 | **+0.073** | +4.3 | 1.28 | −0.78 |
| chop/volatile | 155 | 47.7% | +0.054 | **−0.104** | −16.2 | 1.08 | −1.00 |
| transitional/calm | 36 | 55.6% | +0.297 | **+0.011** | +0.4 | 1.37 | −0.79 |
| transitional/volatile | 157 | 58.6% | +0.243 | **+0.080** | +12.6 | 1.31 | −0.78 |
| trending/calm | 42 | 57.1% | +0.372 | **+0.087** | +3.7 | 1.49 | −0.89 |
| trending/volatile | 166 | 51.2% | +0.186 | **−0.039** | −6.5 | 1.18 | −0.81 |

(Run A shows the same shape: all three calm cells net-positive, all three
volatile cells net-negative. Full numbers in `percell_2026-07-20.json`.)

- **Every cell is gross-positive with n ≥ 36.** The regime separation is a
  *net-of-fee* effect, and the axis that matters is **vol** (calm cells are
  net-positive in both runs), not the trend axis the M7 packet pointed at.
  trending/volatile is net-negative but mildly (−0.04); chop/volatile is the
  worst cell (−0.10). Phase 4's n≥30-per-cell bar is met in the backtest for
  all six cells.
- **The M7 `unknown` bucket is resolved:** the first 14 live decisions
  (2026-05-18 → 2026-06-01) predate regime stamping (`prestamp`) — the
  "+$141 unknown-cell edge" was unstamped-era rows, exactly the suspected
  measurement skew.
- **Confidence floor (contradicting the "no floor salvages" claim):**
  post-hoc on the same walks, net-of-fee: conf≥0.7 → Run A +20.3R
  (exp +0.057), Run B +24.4R (exp +0.075); **calm-only** → +9.4R / +8.4R;
  **calm ∧ conf≥0.7** → +11.5R (exp +0.093) / +15.5R (exp +0.133), n≈120.
  In-sample and post-hoc — needs the k-fold OOS discipline before any Tier-3
  proposal, but the direction is unambiguous.

**Live per-cell (52 decisions, price-based R so paper-journal pnl corruption
can't skew it; real-money leg preferred):** n per cell is 0–5 resolved —
too thin to carry conclusions. The trending/volatile live bleed (−17.9R
total over 5 resolved) is dominated by legs whose journal exits sit **7–14R
past their stops** (62402–62725 vs stops at 63929–64565).

**Root-caused 2026-07-20 (forensics #7122 + candles #7117/#7118 —
supersedes the interim "no effective bracket" reading):** the exchange
behaved **correctly**; those R reads are **journal artifacts**. Under
multi-strategy netting, the Bybit position carries ONE (the newest
trade's) position-level bracket; each bracket fire flattens the whole
position but the journal closes only the newest row. The older rows went
phantom-"open" and were later mis-resolved — real-money trade 2765 carries
trade 2799's closed-pnl record byte-for-byte (−1.63903789 @ 62724.0);
2764←2769; 2796←2798; the orphaned rows (2757/2762/2770) were stamped
resolution-time mark price 62402.2 by `reconcile_orphan_history` on Jun 25.
2765's real share actually exited at the 11:37 Jun-22 TP fire (~64729, a
small **profit**); 2783's at the 21:23 SL fire (~64250, ≈ −0.9R). Real
losses were bracket-bounded and small. Full mechanism + fixes:
`BL-20260720-ICTSCALP-PASTSTOP-EXITS`. The live "lets losses run" record
for this window is measurement, not strategy geometry. Separately noteworthy: two paper trade rows
carry an identical corrupt pnl (−2970.986 on trades 2453 and 2529) — logged
to the health-review backlog.

## Gate verdict (per the Phase-0 gate)

**The clean backtest is NOT structurally negative.** Gross: solidly positive.
Net of fees: flat unfiltered, positive in calm cells / at conf≥0.7. The
demotion's −467R magnitude rested on an unreproducible measurement, and the
live-record magnitude was inflated by crash-day execution failures. Per the
plan's gate → **propose the Phase-4 regime-gated re-promotion path directly**,
with these components (all Tier-3, operator-gated, each needing k-fold OOS
validation first):

1. **Vol-cell gate:** author 2-D `trend_vol` OFF cells for ict_scalp_5m in
   `config/regime_policy.yaml` for the two net-negative cells
   (`chop/volatile`, `trending/volatile`) — n≥30 per cell is satisfied by the
   backtest dataset. Ties `PB-20260609-002`.
2. **min_confidence 0.7** (config `min_confidence` — the M8
   `strategy_tune_sweep` k-fold harness is the validation vehicle).
3. **Keep the BE-trail** (Run B ≥ Run A net) — already live behaviour.
4. **Fee-load reduction is the highest-value follow-up** (Phase 2 M20
   territory): wider stops / maker-entry / higher-TF variant all attack the
   0.20R/trade fee drag directly. MFE medians (1.2–1.5R vs TP at 1.5R) also
   suggest TP/trail tuning headroom.
5. **Execution-side fix independent of strategy:** the crash-day
   past-stop exits (−7..−14R realized on rows with armed stops) deserve their
   own diagnosis — filed to the health-review backlog.

Phase 1 (R:R leak diagnosis) is largely subsumed: exit-reason attribution and
MFE/MAE are in the artifacts (Run A's 323 timeouts collapse to 22 under the
live exit model; be_stop banks 68 scratches). Remaining Phase-1 item: live-leg
MFE/MAE needs May–Jul 2026 5m candles (Bybit + Binance Vision are
proxy-blocked from this sandbox; fetchable trainer-side).

## Caveats (read before acting)

- Backtest window ends 2026-02-28 (archive limit); the live trading window
  (May–Jul 2026) is NOT in the backtest — no overlap-based parity check yet.
- Fees modeled as a flat 7.5bps round-trip taker; funding not modeled.
- Vol stamps use the *current* frozen edges (retrained daily; drift across
  the window is real but second-order — the detector only needs the lowest cut).
- Intra-bar SL/TP ordering is pessimistic (SL first). MFE/MAE on the exit bar
  uses the full bar range.
- The confidence/calm filters are in-sample post-hoc reads, not validated
  proposals.
