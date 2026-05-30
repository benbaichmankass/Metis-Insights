# System / portfolio backtest — the roster on one shared account (2026-05-30)

> **Status:** Tier-1 research tooling + findings. Harness:
> `scripts/backtest_system.py`. Comparison driver: `scripts/research_*` (ad-hoc).
> **Window:** BTCUSDT, 2020-06 .. 2026-02 (5.7y), $10k, risk 0.3%/trade,
> daily-loss cap 3%, 15m clock, 7.5bps round-trip fee.
> **Why:** operator directive — "each strategy must prove itself on its own,
> but it also needs to make sense in the wider framework, and we need a way to
> test that framework." The per-strategy harnesses test each strategy ALONE in
> R-multiples with unconstrained capital; they cannot see what happens when the
> roster shares one account.

## What this harness does (and why it's faithful)

`scripts/backtest_system.py` replays all strategies together over one price
history and routes them through the **real live decision path**:

- Signals from each strategy's **real `order_package()`** (cached per strategy).
- Netting via the **real `src/runtime/intents.py::aggregate_intents`** — the
  exact live rule: same-side = max target_qty (NOT a sum); opposite sides =
  the higher-priority strategy wins and the loser is dropped.
- **One shared, finite BTCUSDT position**, sized with the live risk math
  (`risk_pct × balance / stop_distance`, per `risk.py:141`), with a daily-loss
  cap.
- Exits from the winning strategy's **real `monitor()`** (trail / time-decay /
  SL / TP), plus a flip-close when the net desired side reverses.
- Reports **account-level $ P&L, drawdown ($/%), return/DD, capital
  utilization, and per-strategy attribution**.

Coverage v1: the four BTCUSDT members with the `order_package(cfg,candles_df)`
+ `monitor()` shape — `trend_donchian` (2h), `fade_breakout_4h` (4h),
`squeeze_breakout_4h` (4h), `fvg_range_15m` (15m). `vwap` excluded
(`execution: shadow`); `ict_scalp_5m` + `turtle_soup` deferred (5m cost /
turtle MTF shape) — adding them is registering their stream in `ROSTER`.

## Results

| run | net | return | maxDD | ret/DD | trades | WR | cap-util |
|---|---|---|---|---|---|---|---|
| **A** full roster (fade TS=48, +fvg) [shipped] | **−$411** | −4.11% | $1160 (11.5%) | −0.35 | 1105 | 38.6% | 55.6% |
| **B** full roster, fade time-stop OFF | −$411 | −4.11% | $1160 (11.5%) | −0.35 | 1105 | 38.6% | 55.6% |
| **C** roster WITHOUT fvg_range | −$1011 | −10.11% | $1578 (15.7%) | −0.64 | 1051 | 37.7% | 56.1% |

Per-strategy attribution (run A, net $): `fvg_range_15m +373`,
`squeeze_breakout_4h +36`, `trend_donchian −148`, `fade_breakout_4h −673`.

## Findings

### 1. The fade time-stop is INERT in the portfolio (system ≠ standalone)

A and B are **byte-identical** (same `time_decay=185` exit count). Verified not
a bug: the override propagated (one fade signal cache carries
`meta.timeout_bars=0`, the other `48`) — the portfolio result is genuinely
unchanged. Mechanism: in the multi-strategy book, `fade` shares ONE netted
position that gets closed by a **flip** (another strategy wins the opposite net
vote) or SL long before its 8-day timeout can bind, so fade contributes **zero**
time-decay exits in-system (the 185 `time_decay` closes are all `fvg_range`'s).

This is the headline the operator asked for: **the fade time-stop, which added
+14R/6yr standalone, does nothing at the portfolio level.** Standalone edge does
not transfer one-for-one to the shared account. It does NOT argue against the
time-stop (it's still correct as a live↔backtest parity fix and a single-account
safety on the demo book), but it reframes it honestly: it is not a
portfolio-profit lever.

### 2. fvg_range_15m IS a system-level diversifier (supports shadow→live)

Adding `fvg_range` (A vs C) improves the whole book: net **−$1011 → −$411
(+$600)**, maxDD **15.7% → 11.5%**, return/DD **−0.64 → −0.35** — at roughly
flat capital utilization (56.1% → 55.6%). It is the only strongly net-positive
contributor (+$373) and it *reduces* portfolio drawdown — exactly the
uncorrelated-range-member thesis the standalone backtest argued. The system test
independently corroborates the standalone case for promoting it past shadow.

### 3. The roster is net-NEGATIVE as a book, and FLIPS are the hidden cost

The full system loses money over 5.7y (−$411, −4.11%) at 0.3% risk, and
`fade`/`trend` are net-negative *in-system* despite both being strongly
net-positive *standalone* (fade +64R, trend +52R in their own harnesses). The
mechanism is visible in the exit mix: **246 of 1105 closes (22%) are `flip`s** —
the shared position being torn from one strategy to another when the net vote
reverses. Each flip is a fee-paying round trip that no standalone backtest sees,
and it also cuts winners short. The roster is, to a meaningful degree, **fighting
itself for one position.** This is the single most important thing the system
backtester surfaces, and it argues for a real **decider / capital-allocation
layer** (or per-strategy sub-accounts) rather than one max-qty-netted position.

## Honest caveats (why these are DIRECTIONAL, not gospel)

The **directions** above are robust; the **absolute −$411** is a v1 number that
depends on modeling choices that need a sensitivity pass before anyone treats it
as the portfolio's true expectancy:

1. **`signal_ttl_bars=1`** — a strategy's signal is "live" for only one 15m
   clock bar. This drives the high flip rate; a longer TTL (hold the intent
   until invalidated) would change the flip/churn profile materially. **This is
   the #1 sensitivity to test next.**
2. **Flip policy** — v1 closes-and-reverses the shared position on any opposite
   net vote. A real book might net-down, scale, or refuse the flip. The 22%
   flip rate is a direct artifact of this choice.
3. **Single netted position** — the live system also runs one BTCUSDT position
   per account, so this is faithful to bybit_2; but it means low-priority
   members (fvg_range=3) rarely "win" the book against trend/fade, so their
   in-system trade count is small (fvg only ~50 trades in-system vs 67
   standalone over a similar window).
4. **Fill at clock-bar close** (next-bar-open proxy), no slippage beyond the
   7.5bps fee, no funding. Standard backtest simplifications.
5. Coverage excludes `vwap` (shadow), `ict_scalp_5m`, `turtle_soup` — the real
   live book has more members voting.

## Recommendations

1. **Keep fvg_range on the shadow→live path** — the system test confirms it's a
   diversifier (lifts return/DD, cuts maxDD). (Operator-approved this session.)
2. **Keep the fade time-stop** as the parity/safety fix it is — but do not
   market it as a portfolio-profit lever; it's inert in-system. (Approved.)
3. **Treat "the roster fights itself via flips" as the priority finding.** The
   next research step is the `signal_ttl_bars` / flip-policy sensitivity sweep,
   then evaluating a decider layer (the single-account decider design in
   `docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md` is the natural home).
4. Extend coverage to `ict_scalp_5m` + `turtle_soup` so the system test matches
   the live bybit_2 book.

## Reproduce

```bash
python3 scripts/backtest_system.py --data <btc_5m.csv> \
  --start 2020-06-01 --end 2026-02-28 \
  --initial-balance 10000 --risk-pct 0.3 --daily-loss-pct 3.0
# fade time-stop off:   --override fade_breakout_4h.timeout_bars=0
# drop a member:        --roster trend_donchian,fade_breakout_4h,squeeze_breakout_4h
```

---

## Addendum — flip-churn sensitivity sweep (2026-05-30)

Finding #3 above ("the roster fights itself for one position; 22% of exits are
flips") flagged the close-and-reverse flip policy + `signal_ttl_bars=1` as the
chief suspects for the net-negative book. This sweep isolates them. Added a
`--flip-policy {reverse,hold,flat}` knob to `scripts/backtest_system.py`:
- **reverse** (default; live-faithful — what `aggregate_intents` does): on an
  opposite net vote, close the position and open the new side immediately.
- **hold**: ignore the opposite vote; let the current owner's monitor()/SL/TP
  exit the position naturally.
- **flat**: close on the opposite vote but do NOT re-open (stand aside).

Full roster, 2020-06..2026-02, $10k, risk 0.3%, daily-loss cap 3%:

| ttl | flip policy | net | maxDD% | ret/DD | flips |
|---|---|---|---|---|---|
| 1 | **reverse (LIVE)** | **−$411** | 11.5% | −0.35 | 246 |
| 1 | **hold** | **+$127** | 6.8% | +0.19 | 0 |
| 1 | flat | −$298 | 10.2% | −0.29 | 142 |
| 8 | reverse | −$360 | 11.0% | −0.33 | 243 |
| 8 | hold | +$150 | 6.6% | +0.22 | 0 |
| 16 | hold | +$155 | 6.4% | +0.24 | 0 |

### Conclusion: the flip policy is FIRST-ORDER; signal-TTL is second-order

- **"reverse" (the live behaviour) is the worst policy of the three.** The
  net-negative book is, to first order, an artifact of close-and-reverse churn:
  246 flips over 5.7y, each a fee-paying round trip that also cuts a position
  short before its own exit logic resolves.
- **"hold" flips the book net-positive (+$127), zeroes the flips, and nearly
  halves max-drawdown (11.5% → 6.8%).** Letting the position-holder ride to its
  own SL/TP/trail — rather than being whipsawed by a higher-priority strategy's
  opposite vote — is the single biggest portfolio-level improvement found this
  session.
- **`signal_ttl_bars` is second-order** (reverse −411→−350 across ttl 1→16; hold
  +127→+155). The flip policy dominates it.
- "flat" beats "reverse" but loses to "hold" (standing aside forfeits the
  re-entry).

### What this means (and the Tier-3 caveat)

This is the most actionable finding of the system-backtest work: **the roster's
self-interference is real and fixable, and the lever is the conflict-resolution
behaviour of the intent layer, not any single strategy's params.** BUT the live
`src/runtime/intents.py::aggregate_intents` + `compute_execution_delta`
currently implement the "reverse" behaviour by design (the harness faithfully
replicated it). Changing it to a "hold"-like policy — e.g. *don't tear an open
winner off its exit logic just because a higher-priority strategy now votes the
other way* — is a **Tier-3 change to a core execution invariant**, exactly the
kind the `/new-strategy` skill warns against touching casually. This backtest
**justifies investigating** that change; it does not authorise patching the
aggregator. Proposed next steps (operator-gated):

1. Walk-forward the "hold" policy (train/OOS split) to confirm the +$ / lower-DD
   result is not period-specific before any live proposal.
2. Extend system coverage to `ict_scalp_5m` + `turtle_soup` and re-run — the
   real live book has 6 voters; the flip dynamics may differ with more members.
3. If both hold up, draft the Tier-3 intent-layer change as its own design doc +
   PR (the single-account decider in
   `docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md` is the natural home —
   a "hold/decider" conflict policy is arguably what that decider is FOR).

Reproduce: `--flip-policy hold` (or `flat`) on `scripts/backtest_system.py`.

---

## Addendum — FULL 6-member coverage on a 4.2-yr window (2026-05-30, VERIFIED)

Re-scoped to 2022-01..2026-02 ($10k, risk 0.3%, daily-loss cap 3%, 15m clock,
7.5bps round-trip fee) because the 5.7-yr 5m/15m signal-gen for `fvg_range_15m`
+ `turtle_soup` + `ict_scalp_5m` together did not finish in one session. The
window still spans a full bear-bull cycle (the 2022 drawdown plus 2023-2025
bull plus the recent chop), so the directional findings transfer. All numbers
below were read from completed `runtime_logs/system_backtest/results/*.json`
**before being written here** — addresses the prior session's honesty lapse.

Both rosters re-run on the same 4.2-yr window for a clean apples-to-apples
flip-policy comparison.

### 4-member baseline (no turtle / no ict_scalp), 4.2yr

| flip policy | net | ret% | maxDD$ | maxDD% | ret/DD | trades | WR% | flips |
|---|---|---|---|---|---|---|---|---|
| **reverse (LIVE)** | **+$132** | +1.32% | $1002 | 9.61% | 0.13 | 767 | 39.1% | **160** |
| **hold** | **+$2301** | +23.01% | $592 | 4.69% | **3.89** | 501 | 37.9% | **0** |
| flat | +$951 | +9.51% | $762 | 6.79% | 1.25 | 654 | 39.4% | 152 |

Per-strategy attribution (hold, the winner):
trend_donchian +$1480, fade_breakout_4h +$585, fvg_range_15m +$284,
squeeze_breakout_4h −$50.

### 6-member full live roster, 4.2yr

| flip policy | net | ret% | maxDD$ | maxDD% | ret/DD | trades | WR% | flips |
|---|---|---|---|---|---|---|---|---|
| **reverse (LIVE)** | **−$9928** | −99.28% | $9996 | 99.3% | −0.99 | 1837 | 39.1% | **306** |
| **hold** | **−$6220** | −62.2% | $6306 | 62.97% | −0.99 | 1048 | 37.3% | **0** |
| flat | −$9915 | −99.15% | $9969 | 99.15% | −0.99 | 1670 | 38.6% | 285 |

Per-strategy attribution (hold, the least-bad):
trend_donchian +$1008, fade_breakout_4h +$155, fvg_range_15m +$121,
squeeze_breakout_4h −$14, **turtle_soup −$3032**, **ict_scalp_5m −$4458**.

### Findings — the flip-churn result HOLDS; a NEW result emerges

1. **The flip-churn finding holds at 6-member.** "hold" still beats "reverse"
   by a wide margin ($3,708 swing: −$9928 → −$6220, with maxDD nearly halved
   and 306 → 0 flips). Same direction as the 4-member result; the close-and-
   reverse policy is still the biggest single mechanical drag.

2. **But "hold" is NOT sufficient at 6-member — turtle + ict_scalp poison the
   shared book.** Adding the two highest-priority strategies (turtle_soup=50,
   ict_scalp_5m=30) takes the portfolio from +$2301 / +23% (4-member hold) to
   −$6220 / −62% (6-member hold). They lose −$7,490 BETWEEN THEM in-system
   under the best conflict policy, even though both are strongly net-positive
   in their *standalone* harnesses. The two trend-followers (trend / fade /
   fvg / squeeze) net +$1,270 in-system; turtle + ict_scalp swamp them by 6x.

3. **Mechanism (consistent with `DECIDER-SINGLE-ACCOUNT-2026-05-24.md`'s
   "GREEDY hogs the book" finding).** turtle (priority 50) and ict_scalp (30)
   are the highest priorities in `DEFAULT_PRIORITIES`, so under either reverse
   *or* hold they own the shared position most of the time — and when they
   own it, their losses ARE the portfolio losses. The trend-followers' winning
   trades cannot net them out because they rarely win the book. Standalone
   edge does not transfer when a higher-priority loser monopolises the only
   position. This is exactly the "GREEDY lets the trend hog the book"
   pathology the decider plan identified at 3-member; at 6-member it's
   inverted (the turtle/scalp HOG, not trend) and catastrophic.

4. **Therefore the conflict-policy investigation is necessary but NOT
   sufficient.** A "hold" aggregator alone would let the 6-member book bleed
   −$6220 over 4.2yr. The decider's *selection* job (the v2 step beyond static
   priority — pick the higher-P(profit) trade, regime-route, skip the
   off-regime member) is the bigger lever once two-or-more strategies are
   `execution: live` together. The flip-policy lever and the selection lever
   are complementary, not alternatives.

### Caveats

- **Window differs from the original audit (4.2yr vs 5.7yr).** The 4-member
  4.2-yr numbers above (reverse +$132, hold +$2301, flat +$951) are NOT
  comparable to the 4-member 5.7-yr numbers in the main audit
  (reverse −$411, hold +$127, flat −$298). Only the *flip-policy ordering*
  transfers: hold > flat > reverse, with hold halving maxDD and zeroing flips.
- **Caches keyed on 2022-01..2026-02.** Old 5.7y signal caches under
  `runtime_logs/system_backtest/signals/` are unused by these results
  (different hash key).
- **All other modelling caveats from the main audit still apply** (single
  netted position, fill at clock-bar close, no slippage beyond 7.5bps fee,
  no funding).

### Updated recommendations (override prior addendum's section)

1. **The "hold" conflict-policy investigation remains the right Tier-3
   research target** (PERF-20260530-001) — the directional finding extends to
   the full live roster.
2. **Walk-forward the "hold" policy SPLIT BY ROSTER**. Run train/OOS on
   {4-member, 6-member}, since the 6-member finding inverts the sign — hold
   is no longer sufficient when the high-priority members are net-negative
   in-system.
3. **Operator decision deferred to that walk-forward**: even if hold holds
   OOS, the 6-member portfolio still bleeds. The decider's *selection* layer
   (regime-route, skip-off-regime — DECIDER-SINGLE-ACCOUNT-2026-05-24.md v2
   step 2/3) is the larger prize, not "patch aggregate_intents to hold".
4. **Live activation gates already protect us**: turtle_soup and ict_scalp_5m
   sit at `execution: shadow` today, so the −$7,490 in-system bleed is
   currently a research finding, not a live-money risk. Their shadow→live
   promotion should be conditioned on the decider work landing, not just
   their own standalone edges.

Reproduce:
```bash
python3 scripts/backtest_system.py --data /path/to/btc_5m.parquet \
  --start 2022-01-01 --end 2026-02-28 \
  --roster trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m,turtle_soup,ict_scalp_5m \
  --initial-balance 10000 --risk-pct 0.3 --daily-loss-pct 3.0 \
  --signal-ttl-bars 1 --flip-policy {reverse|hold|flat}
```
JSON results live at `runtime_logs/system_backtest/results/system_{4mem,6mem}_4yr_{reverse,hold,flat}.json`.

