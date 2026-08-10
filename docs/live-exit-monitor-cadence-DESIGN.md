# Live exit-monitor cadence and intrabar evaluation — DESIGN

**Status:** design, nothing shipped. Tier-2 (a new runtime loop) to build,
Tier-3 to let any of it change an exit.
**Operator directive (2026-08-10):** *"once a trade is open, the trade monitor
should be evaluating live trades MUCH more often than once-per-bar per the
strategy trade — no live trade should ever go more than 60s without a realtime
evaluation, and the monitor should be evaluating all intrabar price movements,
not just closed bars. we need to build this out correctly (this time) and test
that it improves performance."*

This document exists because the directive contains two separable asks that the
codebase answers differently, and one of them is already partly satisfied. Both
need stating before anything is built, or the build will target the wrong half.

---

## 1. What the live monitor actually does today (VERIFIED 2026-08-10)

Read from the code this session, not from memory or from a prior doc.

| | Fact | Evidence |
|---|---|---|
| Cadence | Once per pipeline cycle, **sequentially after** signal generation | `src/main.py` — `with _tick_hook("run_one_tick"): run_one_tick(...)` then `with _tick_hook("order_monitor"): run_monitor_tick(...)`. No thread, no separate schedule. |
| Measured cycle | **308.9 s** (4016.6 s / 13 cycles) = ~253.6 s of tick work + a 60 s sleep | `/api/diag/tick_cost` first real reading, 2026-08-10: `mean_ms` 253 600, `max_ms` 295 600, `ticks_measured` 13 |
| Price the levers read | `float(candles_df["close"].iloc[-1])` — the **last row's close** | `src/units/strategies/trend_donchian.py::monitor` and siblings |
| Frame the monitor fetches | `limit=200` candles **at the strategy's own timeframe** (`meta.timeframe`, falling back to the YAML `timeframe`) | `src/main.py::_build_monitor_ohlcv_fetcher` |
| Who covers SL/TP between evaluations | The **broker bracket**, not the bot | Exchange-side SL/TP placed at entry; the monitor's `sl_cross` branch is documented as belt-and-braces |

### 1.1 The "not just closed bars" half is already partly true

The last row of a live OHLCV fetch is the **forming** bar. Its `close` is the
latest trade price and its `high`/`low` are the running intrabar extremes. So a
monitor evaluation at 14:07 on a 1h leg is reading 14:00-14:07 price action, not
the 13:00 bar close — and MFE-style state built from bar highs is intrabar
accurate.

What is *not* intrabar is the **level-cross test**. `current_price <= sl` reads
the forming bar's CLOSE. A dip through the level that recovers before the next
evaluation is invisible to it. For SL and TP that gap is covered by the broker
bracket, which fills on the touch. For **stale-exit, giveback, the trailing
ratchet, and the exit head it is covered by nothing** — those are bot-side only.

So the honest statement of the gap is narrower and sharper than the directive's
framing, and it is worth having stated correctly before building:

> Bot-side exit levers are evaluated roughly every 5 minutes, on the forming
> bar's close. Nothing evaluates them between those samples. The broker covers
> SL/TP touches; it covers nothing else.

### 1.2 The 60 s target is not reachable by config

`TICK_INTERVAL_SECONDS` is already `60` and is **the small term** — 60 s of sleep
against 253.6 s of work. Because the monitor runs after signal generation in the
same sequential loop, its cadence is floored by whatever signal generation
costs. Setting the interval to 1 would change the cycle from ~309 s to ~254 s.

**Therefore the 60 s ask requires decoupling the monitor from signal generation.
There is no configuration that reaches it.** That is the design problem.

---

## 2. What we do not know yet, and must before choosing

`attributed_pct` from the per-hook split (`src/runtime/tick_cost.py`, built and
tested, **awaiting the Tier-2 OK to deploy**) answers the one question that
selects the design:

- If **signal generation dominates** the 253.6 s, the monitor is cheap and
  decoupling it is a small, contained change.
- If the **monitor itself** is a large share, decoupling it does not make it run
  every 60 s — it makes it run continuously, which is a different and worse
  problem, and the fix is inside the monitor.

Choosing between §3's options before that number exists would be choosing on a
guess. The instrumentation is deliberately coarse (two hooks) for this reason:
it answers the selecting question with the least code, and refines only if the
answer is surprising.

---

## 3. Options for the cadence half

| | Approach | Hazards |
|---|---|---|
| **A** | A second loop **inside the trader process** (thread) on its own 60 s schedule | The heartbeat is on the main thread deliberately — a pipeline hang stops it, which is how liveness reflects pipeline health. A monitor thread must not become a way for the process to look alive while the pipeline is wedged. Concurrent broker reads collide: `BL-20260706-IBACCTUPDATES-COLLISION` is exactly a second IB client touching the same account. |
| **B** | A **separate systemd service** | Same broker-collision surface, plus two processes writing the money DB. The netting/reconciler paths assume one writer. |
| **C** | Shrink the tick until the whole cycle is < 60 s | Not available today (253.6 s of work, mostly unattributed) and it fixes the cadence for *everything*, which is more change than the ask. |

**A is the likely answer and B is the likely trap**, but neither is chosen here.
Whichever wins, three constraints hold:

1. **The monitor loop must not be able to fake liveness.** The heartbeat stays
   owned by whatever thread runs the pipeline.
2. **One broker client per account, ever.** A 60 s monitor that opens its own IB
   connection re-creates a documented incident. The read must route through the
   existing readonly-client discipline, or be served from a shared cache the
   pipeline already populates.
3. **A missed evaluation must be legible.** "We evaluated and found nothing" and
   "we did not evaluate" are different states, and the second one is what the
   directive is about. Whatever ships records its own actual cadence — the same
   reasoning as `exposure_soak`'s `measured` flag, and the reason
   `MONITOR_BLINDNESS_ALERT_TICKS` already exists for the per-position case.

---

## 4. The performance half — "test that it improves performance"

This is testable **offline, today**, with no live risk, and it should gate the
build rather than follow it.

The backtest and live already disagree about exit-evaluation granularity, **in
both directions**:

- The harnesses evaluate the levers **once per leg bar**, at that bar's close.
- Live evaluates them **~12 times per bar** on a 1h leg (every ~5 min), but on
  the forming bar's close rather than its extremes.

So neither is a model of the other, and the question "does more frequent
evaluation help?" has never been asked of the data. The experiment:

> Give the harness a **finer frame for exit evaluation than for signal
> generation** — a 1h leg's entries decided on 1h bars, its exits evaluated on
> the 5m bars inside each 1h bar — and run it through the existing Path A / Path
> B gate with the IS/OOS split and the yearly walk-forward.

Two properties make this the right experiment rather than a plausible one:

- It reuses the gate. A cell that improves net_R on one window and loses on the
  other is not a finding, and the 2026-08-10 sweep is a standing reminder — five
  cells passed both windows and still failed the walk-forward.
- It is **honest about direction**. Finer evaluation is not free: a stale-exit
  or giveback rule that fires on a 5 m wick exits trades that a 1 h close would
  have held. That is the same cost the `be_touch_arm` smoke test surfaced
  (arming break-even on a touch scratched a trade that recovered, −0.022 R on
  four trades). The experiment can return "evaluating more often is worse", and
  that would be a result.

`resolve_data` already locates the finest available grain per symbol, so the
data side is largely in place. The harness change is to carry two frames rather
than one.

**Nothing about the live cadence should ship before this returns a verdict.**
Building a 60 s monitor to apply a rule that a 5 m evaluation makes worse would
be a faster way to lose money, and the directive's own words — *"build this out
correctly (this time) and test that it improves performance"* — put the test
first.

---

## 5. Sub-strategy regime conditioning (operator, 2026-08-10)

*"we've already been trying to use regime classification at the strategy level,
but if needed we can also use it at the sub-strategy level (e.g. for choosing
the correct exit mechanism/threshold)."*

Recorded here because it composes with §4 rather than competing with it. The
2026-08-10 sweep's own evidence is two-sided and says the same thing from the
other end: `vt_*` regime cells supplied 5 of 17 both-window cells including the
best one (`trend_donchian_1h vt_hot90`), while on `trend_donchian_sol` the two
`vt_*` cells were the two WORST, and `stale12` passed on both ETH donchian legs
while being the worst cell on `trend_donchian`. **No lever is fleet-wide
correct**, which is precisely the case for selecting the mechanism per regime
rather than gating one lever by it.

Binding constraints when it is picked up: `.claude/skills/regime-selectivity`
(no-cosmetic-cell, walk-forward-before-Tier-3, axis-fidelity), and round 1's
"tightest fired mult wins" precedence. Tier-3 to ship.
