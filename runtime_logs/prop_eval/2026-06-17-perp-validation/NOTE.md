# trend_donchian on alts — REAL PERP + OOS validation (2026-06-17)

**PB-20260616-004 gate.** Validates the 2026-06-16 finding (`../2026-06-16-bybit-multisymbol/NOTE.md`)
that `trend_donchian` is strongly +EV on high-vol crypto alts under the Breakout
prop ruleset. The original run used **Binance SPOT** as a Bybit-perp proxy and was
realised-only-optimistic. This run re-checks it on **real Binance USD-M PERPETUAL
futures** (the right proxy for a Bybit LINEAR PERP — same USDT-margined perpetual
contract, ~same funding/basis regime) **and** out-of-sample (walk-forward), and
factors perp funding. Tier-1 research only — nothing here touches
`config/strategies.yaml`, `config/accounts.yaml`, or the live order path.

> **VERDICT — HOLDS. `trend_donchian` stays solidly +EV on perp AND out-of-sample
> for all three alts (SOL/ETH/BNB).** The edge is NOT a spot artefact and NOT
> overfit to the in-sample window: SOL and ETH are actually *stronger* OOS than
> IS; BNB decays but stays +EV. Perp 12-mo full-history EV lands within ~0-30% of
> the spot-proxy numbers (SOL/ETH within ~3%; BNB ~30% lower). Funding drag is
> small (~$124-143/yr worst-case on a ~$2.7-3.1k average notional — a ~5-15%
> haircut, not verdict-changing, and an upper bound since a trend-follower is
> often on the *receiving* side of funding). **Recommendation: proceed to the
> Tier-3 wiring proposal** (alt-variant strategy + prop account in `accounts.yaml`
> + executor), operator-gated as always.

## Result — 12-month cost-aware EV, `trend_donchian @ risk 1.5%`

$5k Breakout 1-Step Classic (fee $45, 80/20 split, 6% static DD, BANK-ASAP).
Engine: `montecarlo_prop --cost-aware`, block-bootstrap 3000 paths, block_len 8,
seed 1234, clock_tf 1h, flip_policy hold. Real Binance USD-M PERP 5m candles.

| symbol | window | mean net $ /12mo | P(net>0) | p5 $ | accts/yr | spot-proxy (full) |
|---|---|---|---|---|---|---|
| **SOLUSDT** | full (2023-01→2026-02) | **+$1,670** | 93% | −$179 | 4.5 | +$1,707 |
| SOLUSDT | IS (2023-01→2025-02) | +$1,256 | 83% | −$270 | 4.7 | — |
| **SOLUSDT** | **OOS (2025-02→2026-02)** | **+$2,228** | 99% | **+$717** | 3.7 | — |
| **ETHUSDT** | full | **+$1,239** | 94% | −$135 | 4.4 | +$1,183 |
| ETHUSDT | IS | +$949 | 90% | −$225 | 4.9 | — |
| **ETHUSDT** | **OOS** | **+$1,994** | 100% | **+$656** | 2.9 | — |
| **BNBUSDT** | full | **+$748** | 82% | −$360 | 6.6 | +$1,101 |
| BNBUSDT | IS | +$714 | 78% | −$405 | 7.0 | — |
| **BNBUSDT** | **OOS** | **+$667** | 84% | −$315 | 6.0 | — |

(1.0% risk is lower EV but higher risk-adjusted — e.g. SOL OOS @1.0 = +$1,339,
P=97%, p5=+$330, 13-15× ROI/fees. Full per-risk-cell tables in each
`<symbol>_<window>/ev.md`.)

### Reading the table

- **OOS survives — and then some.** The single most important check (does the
  edge persist on data the original observation never saw) is a clean PASS. SOL
  OOS p5 = +$717 and ETH OOS p5 = +$656 mean the *5th-percentile* path is solidly
  profitable — i.e. even an unlucky year nets positive. BNB OOS p5 is negative
  (−$315) but the mean stays +$667 at 84% P(net>0): a +EV churner, lower
  conviction than SOL/ETH.
- **OOS > IS for SOL/ETH** is the 2024-25 alt trend regime (strong directional
  moves favour a Donchian breakout trend-follower). It is NOT proof of a
  permanent edge — a different regime could compress it — but it is the opposite
  of overfitting decay, which is what an OOS check is designed to catch.
- **Perp ≈ spot for SOL/ETH; BNB lower.** SOL full +$1,670 vs spot +$1,707
  (−2%); ETH +$1,239 vs +$1,183 (+5%); BNB +$748 vs +$1,101 (−32%). The
  spot/perp gap is within the basis-noise the original NOTE warned about; BNB's
  larger gap + higher account-burn rate (6.6/yr) flags it as the weakest of the
  three on perp.

## Funding — qualitative + quantitative haircut

`trend_donchian` holds positions **median ~30h / mean ~54h** (≈ 6.7-7.0 funding
periods of 8h each), trading **~75 trades/yr**. Worst-case funding drag (assume
ALWAYS on the paying side at the typical 0.01%/8h):

| symbol | avg notional/trade | funding $/yr (worst case) | as % of 1.5% EV |
|---|---|---|---|
| SOLUSDT | ~$2,657 | ~$124 | ~7% of +$1,670 |
| ETHUSDT | ~$2,848 | ~$125 | ~10% of +$1,239 |
| BNBUSDT | ~$3,117 | ~$143 | ~19% of +$748 |

**Why this is an UPPER bound, not the expected drag:** funding is *signed*. A
trend-follower that enters in the direction of a move is frequently on the side
that *receives* funding (longs receive when funding is negative; in a strong
up-trend perp funding is usually positive so longs PAY, but in down-trends shorts
receive). Over a full cycle the net is far smaller than the one-sided $124-143/yr,
and could be a small *credit*. Even taken at the pessimistic one-sided figure, it
is a ~5-19% haircut on the 1.5%-risk EV — it does **not** flip any symbol from +EV
to −EV. **Verdict unchanged after funding.**

Caveat: the cost-aware EV model (`src/prop/montecarlo.py`) does not itself model
funding — the figures above are a separate post-hoc estimate from the real trade
ledger's notional × hold time. A future refinement could fold a per-bar funding
series into the ledger P&L, but the magnitude here doesn't warrant it for the
go/no-go decision.

## Honest caveats (carried forward + new)

1. **Realised-only-optimistic still applies.** The block-bootstrap has no intraday
   open-position equity swing, so daily-loss / static-DD breaches (hence fee
   churn) are UNDER-counted — true EV is somewhat lower than shown (same caveat as
   the spot run). The relative ranking and the +EV/−EV sign are robust to this;
   the absolute dollar figure is the optimistic end.
2. **BNB's raw engine net P&L @1.5% is slightly negative full-history (−$602)**,
   yet its prop EV is +$748. That is the bank-ASAP economics working as designed:
   banking the winning runs (80% split, withdrawn weekly above start) before a
   breach, then re-buying a $45 account — the strategy is a +EV *churner* on a
   disposable account even when a single never-reset account would bleed. This is
   exactly the thesis the prop ruleset was built to capture, but it means BNB is
   the most fragile of the three (most dependent on the re-buy economics).
3. **Params are BTC-tuned, not alt-optimised.** As in the spot run, this is the
   live `config/strategies.yaml` `trend_donchian` config, no per-alt re-tune. The
   3/3 OOS consistency argues a real trend-following edge, not luck; an alt-specific
   walk-forward tune is a possible follow-up (could improve it or reveal fragility).
4. **Binance perp ≈ Bybit perp, not identical.** Both are USDT-margined linear
   perps with near-identical basis/funding regimes, so this is a far better proxy
   than spot — but absolute EV should still be spot-checked on actual Bybit perp
   candles + the real Bybit funding series before sizing live capital (Bybit REST
   is 403-blocked from the sandbox; that check belongs to the Tier-3 wiring step
   on the VM).
5. **No prop account is wired in `accounts.yaml` yet** (it is the Tier-3 step this
   gate unblocks), so `account_compat_matrix.py` only enumerates the existing
   `standard` accounts. The breakout-prop EV here is produced directly against
   `config/prop_rulesets/breakout.yaml` via `montecarlo_prop --cost-aware` — the
   same path the spot NOTE used. The compat-matrix artifact
   (`solusdt_compat/`) is included as a cross-check: `trend_donchian` shows a
   positive mean end-return (ROUTE) on every standard account too (high P(breach)
   on the tight per-account DD limits is expected — that is precisely why the
   prop bank-ASAP EV model, not the single-account survival lens, is the right
   objective for a disposable $45 prop account).

## Reproduce

```bash
# 1) Fetch real Binance USD-M PERPETUAL 5m candles (generalised fetcher):
python3 scripts/ops/fetch_binance_vision.py --symbol SOLUSDT --market futures/um --start 2023-01 --end 2026-02
python3 scripts/ops/fetch_binance_vision.py --symbol ETHUSDT --market futures/um --start 2023-01 --end 2026-02
python3 scripts/ops/fetch_binance_vision.py --symbol BNBUSDT --market futures/um --start 2023-01 --end 2026-02
#   -> ~/ict-trader-data/<sym>_perp_5m.parquet  (332,640 bars each, 2023-01-01..2026-02-28)

# 2) Cost-aware EV — full history, IS, OOS (repeat per symbol):
python3 scripts/prop/montecarlo_prop.py \
  --data ~/ict-trader-data/solusdt_perp_5m.parquet \
  --combos "trend_donchian" --risk-pct-grid "0.5,1.0,1.5" --n-paths 3000 \
  --clock-tf 1h --flip-policy hold --cost-aware \
  --out-dir runtime_logs/prop_eval/2026-06-17-perp-validation/solusdt_full
# IS:  add --start 2023-01-01 --end 2025-02-01   (out-dir solusdt_is)
# OOS: add --start 2025-02-01 --end 2026-02-28   (out-dir solusdt_oos)

# 3) Per-account compat cross-check:
python3 scripts/prop/account_compat_matrix.py --strategy trend_donchian \
  --data ~/ict-trader-data/solusdt_perp_5m.parquet --clock-tf 1h \
  --out-dir runtime_logs/prop_eval/2026-06-17-perp-validation/solusdt_compat
```

Data window: 2023-01..2026-02, all 38 months present for all three symbols (no
404 gaps — SOL/ETH/BNB perps all listed before 2023). Parquets verified loadable
via `scripts.backtest_system._load_candles` (332,640 bars each, sane price ranges:
SOL $10→$84, ETH $1196→$1963, BNB $246→$617). Parquets live outside the repo
(`~/ict-trader-data/`), not committed.

## Artifacts in this directory

- `<sym>_full/`, `<sym>_is/`, `<sym>_oos/` — `ev.{md,json}` + `montecarlo.{md,json}`
  per symbol per window (SOL/ETH/BNB).
- `solusdt_compat/compat_trend_donchian.{md,json}` — per-account compat matrix.
