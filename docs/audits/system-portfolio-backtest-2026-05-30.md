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
