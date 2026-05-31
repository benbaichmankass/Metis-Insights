# Overnight strategy research — 2026-06-01

**Operator ask (2026-05-31, end of S-PROFIT-GAPS session):** run a long autonomous
research session to find good strategy leads on BTC / MES (or other ideas);
formulate strategies, do variation backtesting, have results by morning.

**Method (the bar that cleared today):** formulate hypotheses → param-sweep
variations across timeframes → **net-of-fee (7.5 bps round-trip)** →
**walk-forward** (in-sample 2021–2023, out-of-sample 2024–2026; SPX/MES IS from
2020/2025) → keep only configs **net-positive in BOTH windows** → rank by
out-of-sample net R → flag the robust ones. Everything is autonomous via the
trainer-VM diag relay; all raw results in `/tmp/research/results.jsonl` on the VM.

**Markets / data (trainer VM):**
- BTC — `market_raw/BTCUSDT/5m/v002` (525,888 bars, 2021-05 → 2026-05), resampled.
- SPX — `data/SPX500_1m.parquet` (2.15M 1m bars, 2020 → 2026-05).
- MES — `market_raw/MES/5m/v001` (only 2025-01 → 2026-05; too short for a clean
  walk-forward, see the S-PROFIT-GAPS MES caveat — used for spot checks only).

**Harnesses swept (standalone, net-of-fee, shared JSON schema):**
`backtest_trend.py` (Donchian breakout), `backtest_pullback.py` (HTF-pullback
trend), `backtest_fade.py` (mean-reversion), `backtest_squeeze.py` (vol
breakout), and a new `research_momentum.py` (time-series momentum + MA-cross —
pure momentum entry, ATR-Chandelier trail exit).

---

## Headline leads

Wave 1 swept 79 configs × 3 windows = 237 backtests across trend / pullback /
fade / squeeze on BTC + SPX. **41 of 79 configs were net-positive in BOTH the
in-sample and out-of-sample windows** — i.e. they survived a walk-forward, not
just an in-sample fit. The strongest, ranked by **out-of-sample** net R:

| Rank | Strategy (family / market / TF / params) | OOS net R | IS net R | full net R | trades | maxDD R | win% | Read |
|---|---|---|---|---|---|---|---|---|
| **1** | **trend (Donchian) / BTC / 1h / dc20, trail=5.0** | **+43.8** | **+42.4** | +90.2 | 730 | 20.6 | 34.8% | **Best lead.** OOS ≈ IS (near-perfect walk-forward symmetry), big sample. This is the *live* `trend_donchian` on a faster TF (1h vs live 2h) + wider trail → directly actionable tuning. |
| 2 | pullback / BTC / 2h / tl40, pf0.5, trail=5.0 | +34.0 | +44.6 | +75.8 | 295 | 15.2 | 39.3% | Extends today's pullback winner; **tl40 beats tl50**. Lower DD than the dc20/1h lead. |
| 3 | squeeze (vol breakout) / BTC / 2h / bb_std2.0, trail=3.0 | +31.6 | +7.5 | +42.2 | 505 | 24.1 | 39.0% | **New family.** OOS≫IS so treat the size cautiously, but it clears both windows. std2.0/trail5.0 is more balanced (OOS +24 / IS +23). |
| 4 | trend / BTC / 1h / dc30, trail=5.0 | +25.4 | +12.1 | +41.5 | 694 | 27.9 | 32.6% | Confirms the dc20/1h lead generalises across the lookback. |
| 5 | pullback / BTC / 4h / tl60, pf0.5, trail=4.0 | +28.2 | +14.9 | +42.4 | 175 | **9.2** | 40.0% | **Best risk-adjusted** of the top group — DD only 9.2 R for +28 OOS. |
| 6 | trend / SPX / 1d / dc20–30, trail=4–5 (long-only) | +5–7.6 | +4–7 | +11–16 | ~30 | **2–4.6** | — | The SPX diversification lead, **resurrected on the DAILY TF** (2h failed). Tiny size + tiny DD → a low-vol, BTC-uncorrelated sleeve. |

**The dominant cross-cutting signal:** a **wide ATR trail (`trail_mult` ≈ 5.0)**
lifts net R in *every* family. The program's edge is the trend runner; the
default trail=3.0 cuts winners early. This replicates today's S-PROFIT-GAPS
pullback finding and now generalises to Donchian, squeeze, and SPX.

## Full walk-forward leaderboard
All 41 both-window-positive configs are recorded in `/tmp/research/results.jsonl`
on the trainer VM (and reproducible from `scripts/research/`). The top 20 by OOS
net R are in the table above + the next tranche (all BTC unless noted):
trend/1h/dc55·trail5 (OOS+22.3), trend/2h/dc30·trail4 (+21.3), pullback/4h
variants (+10–20, DD 8–13), trend/4h/dc20–30 (+8–13, DD 7–15), SPX/1d
long-only (+5–7.6, DD 2–4.6), squeeze/4h/std2.5 (+2.8). Momentum (wave 2) folds
in below once complete.

## What did NOT work
- **`fade` (mean-reversion)** — no config reached the top tranche; BTC does not
  cleanly mean-revert at Donchian extremes on 1h/2h (consistent with the live
  `ict_scalp`/`fade` being losers). Mean-reversion is not this market's edge.
- **`session_breakout`** (from today) — dead at default; not re-swept here.
- **Short side on SPX** — still negative (only long-only SPX clears).
- **Narrow trails (trail=3.0)** — systematically worse than 4–5 across families.
- **MES** — excluded from the walk-forward (only 16 months of data; see caveat).

## Honest caveats
- In-sample param selection over a grid carries overfitting risk on the
  *magnitude*; the walk-forward (separate OOS window) + cross-parameter
  consistency are the guards on the *sign* and rough size.
- Single fee assumption (7.5 bps round-trip); single market per result; R-based
  (risk-normalized) accounting, not $-with-slippage.
- A walk-forward pass is a *candidate*, not a deployable strategy — the next step
  for any lead is a finer multi-fold walk-forward, max-DD / return-correlation
  to the live roster, then `execution: shadow` (Tier-3, operator-gated).

## Recommended next actions (for the operator — all Tier-3 to act on)

In priority order, the leads worth maturing toward `execution: shadow`:

1. **Tune the LIVE `trend_donchian` toward the wide-trail / faster-TF profile.**
   The single most robust result is `dc20, 1h, trail_mult=5.0` (OOS +43.8 ≈ IS
   +42.4, 730 trades). The live `trend_donchian` already exists and is the one
   durable winner — this is a **config change** (timeframe + `trail_mult`), not a
   new strategy, so it's the lowest-friction, highest-confidence move. Recommend:
   a finer multi-fold walk-forward on `{1h,2h} × dc{20,30} × trail{4,5}`, then an
   A/B `shadow` deploy of the tuned variant alongside the live one.
2. **Mature `htf_pullback_trend_2h` at pf=0.5 / trail=5.0** (PERF-20260531-002).
   The sweep re-confirms it and finds **tl40** slightly better than tl50, and a
   low-DD 4h variant (tl60, +28 OOS, DD 9). Wire the scaffold `shadow`-first.
3. **Add the wide-trail change to the roster default** — across families the
   `trail_mult` 3→5 change is the highest-leverage single knob. Worth a roster-wide
   re-tune proposal, not just per-strategy.
4. **SPX/MES 1d long-only trend sleeve** — tiny, BTC-uncorrelated, tiny DD. Blocked
   on deeper MES history for the live instrument (SPX is the CFD proxy), but the
   daily-TF long-only profile is the cleanest diversifier found.
5. **Squeeze (vol-breakout) as a new family** — `bb_std2.0` clears walk-forward;
   worth a dedicated finer sweep before deciding whether to scaffold it.

**Do NOT** chase mean-reversion/fade on BTC, narrow trails, or two-sided SPX —
all negative or fragile here.

---
_Generated by the autonomous overnight research session. Raw results +
reproduction harnesses: `scripts/research/`. Backlog items:
PERF-20260531-002 (pullback), PERF-20260531-001 (SPX/MES)._
