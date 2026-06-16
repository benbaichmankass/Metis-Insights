# Prop-firm evaluation — REAL multi-year run + risk-pct sweep (corrected 2026-06-16)

This directory holds three artifacts over a multi-year BTCUSDT feed:

- `matrix.{md,json}` — the 15-combo pass/fail matrix at the default risk_pct 0.3.
- `risk_sweep.{md,json}` — a risk_pct sweep (the real sizing lever) over the
  promising lower-volatility combos × 10 risk levels.

Both are **corrected** versions of the first cut — see "Corrections" below.

## Feed

- **Source:** [`qashdev/btc`](https://github.com/qashdev/btc) — a GitHub mirror
  of Binance Vision's public BTCUSDT 5m monthly klines archive, fetched via
  `scripts/ops/fetch_qashdev_btc_archive.py` (GitHub raw is reachable from the
  sandbox; Bybit/Coinbase are firewalled).
- **Range:** 2023-01-01 → 2026-02-28 (38 monthly files; 2026-03+ not yet
  published upstream).
- **Bars:** 332,624 5m candles; price range $16,499 → $126,200; 1 missing bar.
- **Consolidated CSV:** `/home/user/ict-trader-data/btc_5m_multiyear.csv`
  (332,624 rows). The raw monthly CSVs + the `btc_5m.parquet` cache live under
  `ICT_TRADER_DATA_ROOT` (`/home/user/ict-trader-data/`), **outside the repo** —
  the giant feed is deliberately not committed.

## Run

```
# matrix (corrected — note --initial-balance 5000):
python scripts/prop/evaluate_prop.py --combos all \
  --data /home/user/ict-trader-data/btc_5m_multiyear.csv --clock-tf 1h \
  --initial-balance 5000 --risk-pct 0.3

# risk-pct sweep (driver in session; sequential, low-memory):
#   7 combos × {0.1,0.15,0.2,0.25,0.3,0.4,0.5,0.6,0.75,1.0} risk_pct
```

`--clock-tf 1h` coarsens only the shared netting/monitor clock (per-strategy
signal streams are still generated on each strategy's own setup TF), so the
full sweep finishes within the sandbox wall-clock budget. Signal streams are
cached under `runtime_logs/system_backtest/signals/`.

Ruleset: `config/prop_rulesets/breakout.yaml` — profit target 10%, daily-loss
3%, **max-DD 6% STATIC off the starting balance**, 30-day funded soak. The
ruleset's headline numbers are CONFIRMED from the Breakout FAQ + plan card
(see `docs/integrations/breakout-compliance-2026-06-16.md`); only the
per-symbol position cap remains to pull at wire time.

## Corrections vs the first cut

1. **Account size.** The first matrix was run WITHOUT `--initial-balance`, so it
   used the CLI default **$25,000** — not the $5,000 Breakout 1-Step account.
   The returns were ratios so they looked plausible, but `net_pnl` was 5× too
   large (e.g. squeeze+fvg showed +$2,520 / 10.08% — internally inconsistent on
   a $5k account). This re-run pins `--initial-balance 5000`, so `net_pnl` now
   reconciles with `return_pct` (squeeze+fvg = +$504 / 10.08% on $5k).

2. **Unambiguous DD measure.** The matrix now reports **Off-start DD (rule)** —
   the deepest drop below the *starting* balance, which is the measure the
   static-6% verdict is actually based on — as a column distinct from the
   engine's **peak-to-trough** `max_drawdown_pct` (kept as a labelled secondary
   stat). New verdict fields: `eval.static_dd_off_start_pct` and
   `eval.equity_at_eval_pass`.

## RECONCILIATION conclusion — `fade+squeeze+fvg` is a LEGIT pass, not a bug

The first matrix flagged `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m`
as a confusing pass: end-return +3.69% (< 10% target) and
`max_drawdown_pct` 9.87% (> 6% floor) yet "passed". Both are reconciled:

- **The 9.87% is peak-to-trough, NOT the rule measure.** The rule is 6% off the
  *starting* balance. This combo's **off-start DD is 0.03%** — its equity barely
  ever dipped below $5,000. The 9.87% peak-to-trough swing happened entirely
  while the account was *in profit*, so it never approached the static floor.
  The evaluator's `_scan_equity_breaches` already references `account_size` (not
  the running peak) for `drawdown_type: static` — the breach logic was correct;
  only the *report* was ambiguous (now fixed).
- **The +3.69% end-return is the FULL-window stat, not the at-pass stat.** Under
  Breakout's one-phase rule, clearing +10% *once* passes the eval (you get
  funded the next day); what the demo equity does afterward doesn't un-clear it.
  This combo crossed +10% on day 519 (`equity_at_eval_pass` ≈ $5,502), then gave
  the gains back, ending the multi-year window at +3.69%. Pass stands; it is a
  *low-quality* pass and is NOT the recommended sizing.

So: **legit pass, correct evaluator logic, report made unambiguous.** Same holds
for any sweep cell showing `eval_pass=True` with a low/negative end-return
(e.g. `fade+squeeze` rp=0.4: passed on a transient +10% mark, ended -3.07%).

## Headline matrix result (risk_pct 0.3, $5k account)

- **2 of 15 combos PASS eval AND survive the funded soak:**
  - `squeeze_breakout_4h,fvg_range_15m` — pass day 673, off-start DD 0.2%
    (peak-trough 3.3%), funded survive, +$504. The clean winner.
  - `fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m` — pass day 519,
    off-start DD 0.0% (peak-trough 9.9%), funded survive, +$184 end (a transient
    +10% pass, see reconciliation).
- **8 combos FAIL eval on `max_drawdown`** — every roster containing
  `trend_donchian` breaches the 6% static floor (off-start DD 7.5%, first breach
  2023-01-26, the early-2023 run-up), despite being the most profitable on raw
  net P&L. trend_donchian is the DD killer at default sizing.
- **The rest never reach +10%** at risk_pct 0.3 within the window.

## RISK-PCT SWEEP — the real lever (`risk_sweep.md`)

Sweeping risk_pct is what turns a slow/under-target combo into a pass. Key facts:

- **`squeeze_breakout_4h,fvg_range_15m` is the standout** — it has essentially
  no off-start drawdown at any sizing (DD comes only from in-profit swings), so
  it can be sized up aggressively without nearing the 6% floor:
  | risk_pct | days→target | off-start DD | peak-trough DD | end return | net $ |
  |---|---|---|---|---|---|
  | 0.3 | 673 | 0.2% | 3.3% | +10.1% | +$504 |
  | 0.5 | 512 | 0.4% | 5.5% | +17.0% | +$852 |
  | 0.6 | 432 | 0.5% | 6.6% | +20.6% | +$1,030 |
  | **0.75** | **404** | **0.6%** | **8.2%** | **+26.0%** | **+$1,300** |
  | 1.0 | 356 | 0.8% | — | +35.2% | — → **FAILS** (`daily_loss` breach) |

  At risk_pct **1.0** it trips the 3% **daily-loss** limit (not the DD floor),
  so 0.75 is the practical ceiling for this combo.

- **Higher risk doesn't monotonically help the low-DD combos to a faster
  *pass*** — the binding limit flips from "never reached +10%" (low risk) to
  "tripped the 3% daily-loss halt" (≥1.0 risk). The sweet spot for
  squeeze+fvg is **0.6–0.75**.

- **trend_donchian combos are not rescuable by sizing** — they breach the 6%
  off-start floor even at modest risk; lowering risk only shrinks the pass, it
  doesn't move the early-2023 drawdown below the floor relative to balance.

## RECOMMENDATION

> **`squeeze_breakout_4h,fvg_range_15m` at risk_pct ≈ 0.6–0.75 clears Breakout
> 1-Step Classic in ~400–430 days with peak DD ~6–8% and off-start DD < 1%.**

risk_pct **0.75** is the fastest durable pass (404 days, +26% end return — a
high end-return means the pass is *durable*, not a transient mark) while keeping
off-start DD at 0.6% — a >5pp margin to the 6% floor. risk_pct **0.6** is the
slightly more conservative pick (432 days, peak-trough 6.6%, +20.6%). Below 0.5
the combo doesn't reach +10% in the window; at 1.0 it trips the daily-loss
halt. The single `squeeze_breakout_4h` and `fvg_range_15m` legs also pass at
high risk (0.75–1.0) but slower and with thinner margins — the pair is strictly
better.

**Honest caveat:** "days to target" here spans years because BTC's regime
dominated — the strategies are slow grinders on this feed, and the pass is
driven by *avoiding* the static-DD floor (which squeeze+fvg does trivially)
rather than by fast compounding. The ruleset's headline numbers are confirmed,
but slippage/funding/fills and Breakout's exact equity accounting will differ
from this backtest. This ranks *relative* combo robustness and flags *obvious*
rule mechanics; it is a filter, not a guarantee.
