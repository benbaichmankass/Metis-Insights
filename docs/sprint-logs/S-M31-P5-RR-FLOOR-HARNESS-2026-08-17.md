# Sprint Log: S-M31-P5-RR-FLOOR-HARNESS-2026-08-17

## Date Range

2026-08-17 (single session).

## Objective

Close `PB-20260817-RR-FROM-HERE-LEVER-ABSENT-FROM-HARNESS` — M31 **P5
precondition 3**. The candidate P5 lever (an `rr_from_here` floor) could not be
backtested at all, so the precondition as written asked for evidence no artifact
could produce.

## Tier

**Tier 1.** Research harness + CI tooling + docs. No `config/`, no order path,
no live lever, no service. **P5 itself remains Tier-3 and withheld** — nothing
here changes what happens to money.

One file in the change is imported by the live trader:
`src/runtime/position_telemetry.py`. The edit is a **pure extraction** of an
existing expression into a function the same module then calls, in a module that
is observe-only and fully exception-guarded at both call sites. Behaviour is
pinned unchanged by the existing suite (41 passed, 1 skipped).

## Starting Context

Precondition 1 (the terminal-snapshot writer) shipped and was post-deploy
verified earlier the same day (#9861 / #9868). Precondition 2 is data accrual —
final live population n=1 fleet-wide — which no code change here touches.
Precondition 3 was recorded as *"the only remaining item that is work rather
than waiting"*, with the caveat that it is implement-then-measure.

## Repo State Checked

- Branch `claude/m31-p5-rr-from-here-lever-hfur8h`, cut fresh from
  `origin/main` at `beefb071`; 0 ahead / 0 behind, clean tree.
- Coordination board #6927 read to the tail (1,027 comments). One active
  session, `9952b154` on `claude/m20-exit-coverage-matrix-8d3he7`, holding
  `scripts/research/m20_*`, `docs/research/exit-refinement-coverage.json`,
  `tests/test_path_b_floor_*`, `docs/claude/health-review-backlog.json`, and
  next going into `m20_fleet_exit_sweep.py`. **Disjoint from this change** —
  verified rather than assumed by grepping all three backlog files: the row
  worked here is in `performance-review-backlog.json`, not the health one they
  hold. `m20_wf_effective.py` was read as a grading tool and **not modified**.
  `▶️ START` posted before the first substantive edit.

## Files and Systems Inspected

- `scripts/backtest_trend.py` — the live-faithful trend engine (frozen entry-bar
  ATR, SL-first nested loop, post-exit cooldown) and its lever chain.
- `src/runtime/position_telemetry.py` — the only module repo-wide that computes
  `rr_from_here`.
- `src/units/strategies/trend_donchian.py:966-977` — the live call site, read to
  establish what `stop` and `target` actually are rather than inferring them.
- `src/research/trail_levers.py` — the "ONE definition" precedent this follows.
- `scripts/ci/check_collapsed_states.py`, `tests/test_trend_harness_levers.py`,
  `tests/trend_harness_engine.py`.

## Work Completed

**1. One definition, not a second derivation.**
`position_telemetry.r_distances` extracted as the single definition of
`(r_to_stop, r_to_target, rr_from_here)`; `build_record` now calls it and
`scripts/backtest_trend.py` imports it. A lookalike copy in the harness is
exactly the defect M31 exists to close — *the harness measured a book production
does not run* — and would be invisible, since both copies would look correct in
isolation. The mapping was read at the live call site, not assumed:

| live (`trend_donchian.monitor`) | harness |
|---|---|
| `stop=sl` — the **current trailed** stop | `trail` |
| `target=open_pkg["tp"]` — the **venue-capped** TP | `tp_price` |

**2. The lever.** `--rr-floor` (default `0.0`, byte-identical). Exits at bar
CLOSE when `rr_from_here < floor`. Placed **last** in the precedence chain
(stop → tp → giveback → stale → rr_floor) so composing it with an
already-declared lever cannot re-grade that lever's recorded verdict.

**3. Measurable and inert are different states.** With no capped TP there is no
`r_to_target`, so the lever cannot fire and the run returns exactly-zero deltas
**byte-identical to a measured no-op** — the shape of
`BL-20260817-A-SHIPPED-LEVER-RE-SWEPT-AGAINST-ITSELF-READS-AS-A-MEASURED-NO-OP`,
filed by a concurrent session hours earlier. Handled twice over: `rr_floor_state`
(`off` / `measurable` / `unmeasurable_no_tp_cap`), registered with
`collapsed-state-guard`; and `main()` **refuses** the combination (exit 2) rather
than letting an inert row reach a corpus.

**4. A reachability instrument.** `rr_min_p10/median/p90` — the lowest
`rr_from_here` each trade reaches — computed whenever a capped TP exists,
independent of the floor. A floor can only fire on a trade whose `rr_min` falls
below it, so this says which floors are testable *before* a sweep spends a fold.
`None`, never `0.0`, when unmeasured. It deliberately excludes the bar that
fills the TP (there `rr → 0`, which would report every floor as trivially
reachable).

## Validation Performed

- **43/43 guards pass** on the committed diff. Two ran clean only after fixing
  real findings they surfaced (below).
- `ruff check .` clean — after re-pinning to `ruff>=0.15,<0.16`; my first run
  used 0.16 and produced 103 phantom errors, which is precisely the expanded
  default ruleset `requirements-dev.txt` pins against.
- 86 passed / 1 skipped across `test_rr_floor_lever.py` (new, 15 tests),
  `test_trend_harness_levers.py`, `test_position_telemetry.py`,
  `test_m31_p3_telemetry_readers.py`.
- `rr_floor=0.0` added to `ALL_LEVERS_AT_DEFAULT`, so the **per-lever**
  parametrized no-op guard now covers it. A lever absent from that dict is a
  lever nothing pins.
- **POSITIVE CONTROL**, because a suite that only ever asserts "no change"
  cannot tell a correct no-op from a lever wired to nothing. On
  `data/backtest_candles.csv` with `--tp-cap-pct 0.099`:

  | floor | 10 | 25 | 40 | 100 |
  |---|--:|--:|--:|--:|
  | `rr_floor_exit`s | 1 | 50 | 176 | 315 |

  Monotone. The test derives its floors from the run's own measured `rr_min_p90`
  rather than hardcoding, so it cannot rot into asserting a stale constant.

### The measurement — and why it is NOT a verdict on the lever

**Population: the whole of `data/backtest_candles.csv`** — BTCUSDT **1-minute**
bars, 5,000 rows, 2022-07-23 → 2022-07-27, median `(high−low)/close` **0.101%**.
With `--tp-cap-pct 0.099`: `tp_r_effective` median **36.73R** (min 10.83, max
**50.0** — at 50.0 the `tp_r` *sentinel* binds, i.e. even 50R is nearer than
9.9%), `rr_min` p10 **21.16** / median **35.88** / p90 **58.36** over 143
measured trades, and **zero** `take_profit` exits in the entire file.

The arithmetic reconciles: risk ≈ `2.5 × ATR` ≈ 0.25% of entry, so the 9.9% cap
sits near `0.099/0.0025` = **39.6R** against a measured 36.73R — ~8% apart using
median true range as an ATR proxy.

The **live** legs measured in M31 P3/P4 sit at cap_R **2.1258**
(`qqq_trend_long_1d`), **3.9233** (`xrp_pullback_2h`), **5.8294**
(`trend_donchian_sol_4h`), and the motivating XRP trade's `rr_from_here` was
**0.71**. Roughly an order of magnitude apart, and `rr_from_here` scales
directly with target distance — so a floor fitted on this fixture would have to
be ~25–40 to fire at all.

⚠️ **No edge verdict is offered from that fixture, deliberately.**
`net_total_r` does worsen monotonically with the floor (−82.65 baseline →
−150.25 at floor 100), but that is a net-negative book at absurd floors in the
wrong volatility regime; quoting it as "the lever loses" would be this
milestone's own defect class. Filed as
`PB-20260817-RR-FLOOR-UNMEASURED-ON-LIVE-REGIME-DATA`.

**Scope note, so this is not over-read:** this is a limitation of the fixture in
this checkout, **not** a defect in the recorded sweep corpus.
`m20_fleet_exit_sweep.py` resolves per-`(symbol, timeframe)` files from
`--data-dir`, and `backtest_candles.csv` does not parse as `(sym, tf)` at all,
so real sweeps already use real per-leg candles.

## Documentation Updated

- `docs/design/m31-p5-telemetry-reading-lever-PROPOSAL.md` § 5 precondition 3
  split into **3a IMPLEMENT (done)** / **3b MEASURE (not done)**, with the
  measured regime gap; § 8 recommendation updated.
- `docs/claude/performance-review-backlog.json` —
  `PB-20260817-RR-FROM-HERE-LEVER-ABSENT-FROM-HARNESS` → **resolved** via option
  (a); new `PB-20260817-RR-FLOOR-UNMEASURED-ON-LIVE-REGIME-DATA` for the measure
  half.
- `ROADMAP.md` § M31.

## Contradictions or Drift Found

**Two of my own, both caught by guards rather than by me.**

1. **I wrapped a tracking id across lines, twice** — in a file whose *existing*
   comments warn about exactly that ("kept on ONE line: a hyphen-wrapped
   tracking id silently becomes a DIFFERENT id that resolves to no filed row —
   `check_backlog_refs` caught exactly that here"). It caught it here again.
   Both now sit on their own line.
2. **`collapsed-state-guard` is stricter than I assumed, and correctly so.** My
   first `rr_floor_state` was a nested ternary, so two of the three state
   literals sat on continuation lines that did not name the field. The guard
   refused it: *"a contract naming a state its own module does not produce is a
   dead claim, not a guarantee."* Rewritten as an explicit branch — more
   readable, and every state literal now sits on a line naming its field.

Neither reached `main`. Recording them because the useful pattern is that both
were self-inflicted violations of rules **already written down in the very files
I was editing**, and reading the rule was not enough — running the guard was.

## Risks and Follow-Ups

- `PB-20260817-RR-FLOOR-UNMEASURED-ON-LIVE-REGIME-DATA` (open) — the 3b half.
- The lever has **no live counterpart**, deliberately. `harness-lever-coupling`
  passes because it classifies YAML strategy keys and this is not one. If P5 is
  ever approved, the live monitor lever is a separate Tier-3 change.
- `r_distances` is now on the live import path from a research script. It is
  stdlib-only and pure; no new runtime dependency reaches the trader.

## Deferred Items

The 3b walk-forward itself — it needs per-leg candles at 1h–1d, which are not in
this checkout. Not attempted rather than half-attempted: a sweep on the wrong
regime would have produced a confident number worth less than none.

## Next Recommended Sprint

Pull per-leg candles (trainer relay or a populated `--data-dir`) and run 3b:
read `rr_min_*` from a lever-OFF run to pick floors that can fire, sweep, grade
with `m20_wf_effective.py` so inert folds are excluded
(`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`, 75/386 = 19.4%), and
assert `rr_floor_state == "measurable"` on every recorded row. **Do not fit a
floor over raw `rr_from_here`** — live it hit 201.87 on the fleet's only closed
telemetry row (0.0337R from stop), 19.6× the next value across the same 14 rows.
Grade the decision, not the distribution. A loss retires the candidate, which is
a useful outcome.

## Wrap-Up Check

- [x] Guards 43/43 on the committed diff; ruff clean on the pinned version.
- [x] Tests: 86 passed / 1 skipped; new lever covered by the per-lever no-op
      guard and by a positive control.
- [x] Backlog: one row resolved, one opened, both passing
      `check_backlog_refs` + `check_backlog_criteria`.
- [x] Board `START` posted before the first edit; `DONE` at wrap.
- [x] Nothing shipped to a live path; P5 remains Tier-3 and withheld.
