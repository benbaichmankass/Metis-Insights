# P2 — Breakout prop candidates, 2026-09-29: the `ict_scalp` gap closed, still zero passes

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **P2** (operator priority "Prop first", 2026-09-28). Tier-1 offline
> research: no `config/`, `src/` or execution change. Continues
> [`W6-PROP-R2`](b6-prop-ev/w6-candidates-v2-2026-09-27.md) and
> [`W6-PROP-R3`](b6-prop-ev/w6-prop-shaped-search-2026-09-27.md), which each ran
> exhaustive Breakout-symbol × strategy-family searches and found zero roster
> additions. Adding a leg to `breakout_1` is Tier-3; this document proposes, it
> does not decide.

## 0. What this round does that R2/R3 did not

Candidates are scoped to the operator's watchlist symbols (BTCUSD, ETHUSD,
SOLUSD, ADAUSD, AVAXUSD, XRPUSD), per the standing constraint on checklist row
**P2** itself. R3's own §5.3 states plainly: *"Session filters,
max-trades-per-day, the **ict_scalp family** and non-crypto symbols were
outside the registered grid and were not tested."* This round:

1. **Runs the `ict_scalp` family** against the pre-registered
   `RULE-TPL-PROP-FIT-B6V2` bar (`research/templates/prop-fit-breakout.yaml`)
   for the first time — `ict_scalp_eth_15m`, `ict_scalp_sol_15m`,
   `ict_scalp_xrp_15m`, `ict_scalp_xrp_5m` all carry adequate OOS evidence
   (n = 117–243) that no prop-fit round had ever scored.
2. **Re-grades `trend_donchian_eth` and `trend_donchian_eth_prop` at the
   tool's full default Monte Carlo** (4000 lives × 100 outer × 200
   lives/outer). R2 used a lighter MC (1000/30/100) "to make a 20-candidate
   wide screen tractable" and flagged in its own §6 that a candidate at the
   edge could read differently at full default sizing; R3's own §3 confirmed
   exactly that for `trend_donchian_eth` (2/5 seeds at light MC → 0/5 at full
   MC). This round settles that question at full MC directly, standalone (not
   walk-forward re-tuned, unlike R3 — this scores the leg AS CURRENTLY
   CONFIGURED in `config/strategies.yaml`).
3. **Pre-registers the full remaining candidate grid** (42 units,
   `RQ-20260929-019..060`, PR #14049) so every Breakout-mapped, live-roster
   leg on the watchlist symbols that `queue_replenish`'s own candidate list
   carries has a queued, self-reproducing decision rule — whether or not this
   round runs all of them (see §4).
4. **Notes that the ADA/XRP DXtrade symbol-mapping blocker R2 §5.3 flagged is
   now cleared** (`PROP-MAP`, #13522, 2026-09-28: `ADAUSDT`→`ADAUSD`,
   `XRPUSDT`→`XRPUSD` operator-confirmed in
   `config/prop_rulesets/breakout_routing.yaml`) — moot this round only
   because, again, nothing cleared the EV bar.

## 1. Population and rule

**Decision rule** (`RULE-TPL-PROP-FIT-B6V2`, per leg × sizing arm):
PASS if `ev_net_usd_per_life > 0` on **all 5 seeds** AND `p_net_positive >=
0.70` on **all 5 seeds**; UNDERPOWERED (indeterminate) if `n < 88`; FAIL if
`ev <= 0` on any seed; otherwise NULL (indeterminate). `prop_ev_sim.py`
path-mode, `--ruleset config/prop_rulesets/breakout.yaml --costs breakout
--funded-start fresh --swap-model dxtrade`, tool defaults (4000/100/200 MC),
seeds {1,2,3,4,5}. Every leg's trades come from its committed evidence record
(`comms/strategy_evidence/<leg>.json` → `cost_stack.source` →
`__trades.jsonl`) — no new backtest, no re-tuning.

**Sizing arms** (`research/templates/prop-fit-breakout.yaml`):
`bal015` = flat 1.5% of current balance (**the CURRENT live `breakout_1`
instance's actual sizing** — `config/prop_rulesets/breakout.yaml::sizing.mode:
flat`, `max_risk_usd: $75` on the $5,000 account); `room033` = cushion-fit room
sizing at k=0.33 (**the declared sizing for the NEXT fresh account instance**,
`sizing.next_instance_mode: room`, matching R2/R3's own canonical sizing).

**Power floor.** Of the 16 live-roster, Breakout-mapped legs on the watchlist
symbols, only 6 clear `n_trades_oos >= 88`. The other 10 are recorded here by
`n_trades_oos` alone, without simulation: the pre-registered rule's own
UNDERPOWERED clause depends only on `n`, so simulating them would not change
the verdict and would have cost roughly 2.5 min/cell × 50 cells for no
decision-grade information.

## 2. Ranked results — the 6 legs that clear the power floor

Both sizing arms, full default MC, 5 seeds each. `P(daily_loss)` /
`P(dd_floor)` are the death-cause shares directly reported by
`prop_ev_sim.py`'s `results.path.death_causes` (mean across the 5 seeds), over
one simulated account life; `p_alive_at_horizon` is ≤0.3% for every leg below,
so death-cause share is close to a full partition of outcomes. `ev/trade` =
`ev_net_usd_per_life / mean_trades_per_life`.

### 2a. Under flat $75 risk (`bal015` — the account's ACTUAL current sizing)

| leg | n (OOS) | ev/life ($, 5-seed range) | P(net>0) (5-seed range) | ev/trade | P(breach daily-loss) | P(breach DD floor) | trades/life | trades/week | verdict |
|---|--:|---|---|--:|--:|--:|--:|--:|---|
| `ict_scalp_eth_15m` | 117 | +197 to +215 | 0.435–0.463 | **+$3.81** | 0.000 | 0.997 | 54.5 | 2.30 | **NULL** — ev>0 all 5, p<0.70 all 5 |
| `ict_scalp_xrp_15m` | 129 | +159 to +169 | 0.371–0.388 | +$3.02 | 0.145 | 0.854 | 54.0 | 2.51 | **NULL** |
| `ict_scalp_sol_15m` | 132 | +129 to +138 | 0.358–0.367 | +$2.67 | 0.214 | 0.785 | 50.3 | 2.66 | **NULL** |
| `trend_donchian_eth_prop` | 169 | +128 to +140 | 0.299–0.323 | +$4.31 | 0.286 | 0.714 | 30.6 | 3.28 | **NULL** |
| `trend_donchian_eth` | 107 | +66 to +77 | 0.228–0.243 | +$4.27 | 0.236 | 0.764 | 16.6 | 2.03 | **NULL** |
| `ict_scalp_xrp_5m` | 243 | **−4.7 to −7.9** | 0.123–0.134 | **−$0.22** | 0.585 | 0.415 | 29.5 | 4.72 | **FAIL** — ev<0 on all 5 |

### 2b. Under fresh-account room sizing (`room033` — R2/R3's canonical sizing)

| leg | ev/life ($, 5-seed range) | P(net>0) (5-seed range) | verdict |
|---|---|---|---|
| `ict_scalp_eth_15m` | +294 to +302 | **0.622–0.634** (closest of any candidate to the 0.70 bar) | **NULL** |
| `trend_donchian_eth_prop` | +197 to +213 | 0.431–0.441 | **NULL** |
| `trend_donchian_eth` | +179 to +189 | 0.414–0.424 | **NULL** |
| `ict_scalp_xrp_15m` | +149 to +161 | 0.407–0.418 | **NULL** |
| `ict_scalp_sol_15m` | +139 to +158 | 0.433–0.455 | **NULL** |
| `ict_scalp_xrp_5m` | **−9.4 to −14.5** | 0.122–0.141 | **FAIL** |

**Full per-seed cells, sim inputs and death-cause breakdowns** are committed at
`research/results/RQ-20260929-*` (landed alongside this doc) and the raw
`grid.json`/per-cell sim JSON referenced there.

## 3. Verdict

**Zero of the 6 power-adequate candidates clear `RULE-TPL-PROP-FIT-B6V2`, at
either sizing arm.** The pattern is consistent with, and extends, R2/R3's
findings:

- **`ict_scalp_eth_15m` is the standout NULL**, the same role
  `trend_donchian_eth` played in R2/R3: positive EV on every seed at both
  sizings, and under room sizing its `P(net>0)` reaches 0.62–0.63 — closer to
  the 0.70 bar than any other candidate this round or in R2/R3 got, but not
  there on any of 5 seeds.
- **`ict_scalp_xrp_5m` is a clean FAIL, not a NULL**: negative EV on every
  seed at both sizings, the only leg in this round whose evidence-p95 is
  clearly negative. This is a real, non-marginal result — 243 committed OOS
  trades, high frequency (4.7 trades/week, the highest of any candidate) but
  net-negative once Breakout's cost stack (8bps commission + 3bps slippage
  round-trip, 0.033%/day swap) and the daily-loss/DD-floor ruin mechanics are
  priced in.
- **The two legs already live on `breakout_1`'s current roster**
  (`trend_donchian_eth_prop`, and by construction `trend_donchian_sol_prop`,
  not re-tested standalone here since it was excluded from the `ict_scalp`-gap
  scope) show the same shape as every other candidate: positive EV,
  `P(net>0)` well short of 0.70. This is a **standalone** reading (not the
  portfolio reading B6/R2/R3 use for the roster's own re-grade) and is not a
  demotion signal — `trend_donchian_eth_prop`'s live status was decided on the
  portfolio bar (`RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2`), which this document
  does not reopen.
- **The daily-loss/DD-floor split is informative on its own.** Every clearing
  candidate's simulated account deaths lean toward the static drawdown floor
  over the daily-loss cap (e.g. `ict_scalp_eth_15m`: 99.7% DD-floor vs 0.0%
  daily-loss) — consistent with `breakout.yaml`'s DD floor being fixed at 6%
  of the *starting* balance while daily-loss recalculates off the *current*
  balance each day, so a leg that survives its first month gets meaningfully
  more daily-loss room before it gets more DD-floor room. `ict_scalp_xrp_5m`
  is the exception (58.5% daily-loss / 41.5% DD-floor) because its shorter
  average life (29.5 trades, 43.8 days) means fewer accounts survive long
  enough for that asymmetry to compound.

**No leg is proposed for addition to `breakout_1`.** Per the same standard R2
applied ("never round a borderline result up to a Tier-3 proposal"), neither
`ict_scalp_eth_15m` nor any of the other 5 power-adequate candidates clears
its pre-registered bar robustly enough to propose.

## 4. What is NOT covered by this round, and where it is queued

The 10 remaining watchlist-symbol candidates are all UNDERPOWERED by
`n_trades_oos`:

| leg | symbol | n (OOS) | net_R (OOS, Bybit costs) | why not simulated |
|---|---|--:|--:|---|
| `trend_donchian` (BTC, 1h) | BTCUSDT | 73 | +4.60 | n<88 |
| `trend_donchian_eth_4h` | ETHUSDT | 44 | +10.17 | n<88 |
| `eth_pullback_2h` | ETHUSDT | 62 | −1.74 | n<88 |
| `trend_donchian_sol` | SOLUSDT | 59 | +4.46 | n<88 |
| `trend_donchian_sol_prop` | SOLUSDT | 65 | +5.19 | n<88 (live `breakout_1` leg; portfolio bar already covers it) |
| `sol_pullback_2h` | SOLUSDT | 18 | +4.59 | n<88 |
| `trend_donchian_xrp_4h` | XRPUSDT | 16 | +8.94 | n<88 |
| `xrp_pullback_2h` | XRPUSDT | 57 | +12.79 | n<88 |
| `ada_pullback_2h` | ADAUSDT | 57 | +19.25 | n<88 — **already queued** `RQ-20260928-024/025/026` (all 3 sizing arms), re-rendered after #13748's template-placeholder fix, pending the auto-dispatcher's next 6-hourly cycle |
| `squeeze_breakout_4h` | BTCUSDT | 10 | −0.40 | n<88, and the shallowest evidence window of any candidate |

None of these would clear the power floor at their current evidence size
regardless of EV sign or sizing arm — running the full Monte Carlo grid on
them now would not produce a decision. `eth_pullback_2h` is likewise queued
(`RQ-20260928-027/028/029`) and will land its own record on the same
dispatcher cycle.

**AVAX is entirely absent from this round.** Every AVAX strategy in
`config/strategies.yaml` (`ict_scalp_avax_5m`, `trend_donchian_avax_4h`,
`avax_pullback_2h`) is `execution: shadow` — there is no live leg to score.
Separately, `BL-20260902-AVAX-VENUE-MAX-CLAMP-INERT-WHEN-THE-LIVE-LOOKUP-MISSES`
(open) records that AVAX's venue-max sizing clamp is a silent no-op when the
live lot-rule lookup misses — worth fixing before any AVAX leg is graduated to
live, independent of whether it ever clears the EV bar.

**The remaining 32 pre-registered queue units** (`RQ-20260929-019..060` minus
the 6 legs graded above; PR #14049) cover the same 6 legs' `bal010`/`room033`
(where not already run) and mechanically hand the same question to the
auto-dispatcher, plus the 10 non-crypto legs from the template's `markets:`
block, which are out of this round's watchlist scope (P2's standing constraint
restricts candidates to the operator's 6-symbol watchlist) but were generated
as part of the same `queue_replenish` candidate expansion and will be graded
by whatever lane owns non-crypto prop-fit next.

## 5. Method notes and caveats

- **This is a re-pricing of committed evidence, not a new backtest.** Every
  number above comes from `prop_ev_sim.py` run against the leg's existing
  `comms/strategy_evidence/<leg>.json` → `__trades.jsonl` rows. No strategy
  parameter, harness, or evidence record was changed to produce this
  document.
- **The `ict_scalp` candles are the same feed the live evidence was already
  built on** — no candle re-fetch was needed since this reads committed
  trades, not raw OHLCV, so R3's Binance-vision-vs-Bybit caveat (§5.1 there)
  does not apply here the same way; the trades files themselves are Bybit-fee
  committed records under their own evidence provenance.
- **Portfolio effects are not measured in this round.** Every number above is
  a STANDALONE leg reading. R2/R3 both found that even a leg with a positive
  standalone reading can turn negative once added to the current two-leg
  roster (shared daily-loss/DD budget). A future round adding any of these
  candidates to a portfolio-addition test should not skip that step just
  because this round's own standalone numbers looked better than R2/R3's.
- **`n_trades_oos < 88` is a real evidence-thinness problem, not just a power
  technicality**, for 4 of the 10 excluded legs (`sol_pullback_2h` n=18,
  `trend_donchian_xrp_4h` n=16, `squeeze_breakout_4h` n=10, and to a lesser
  extent `ada_pullback_2h`/`eth_pullback_2h`/`xrp_pullback_2h` at n=57–62) —
  these would need a materially longer track record before any Stage-0
  verdict is reachable, independent of prop-fit specifically.

## 6. Pipeline and disposition

- **No proposal.** This document proposes no roster or parameter change for
  `breakout_1`.
- **Research units:** `RQ-20260929-019..060` land `status: done` (the 6 graded
  legs, both arms where run) or stay `queued` (the remainder, for the
  auto-dispatcher). Full per-seed records at `research/results/RQ-20260929-*`.
- Closes the immediate ask on checklist row **P2** for the crypto watchlist
  symbols. The standing constraint on P2 (candidates only from the operator's
  watchlist) is unchanged and binds any follow-on round.
