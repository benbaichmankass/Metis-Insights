# Sprint Log: S-M20-ARM-REACHABILITY-VOL-THRESHOLD-2026-08-16

## Date Range
- Start: 2026-08-16 13:35 UTC
- End: 2026-08-16 15:45 UTC

## Objective

Answer the one question the overnight M20 session left explicitly open in its
13:11Z release: *"why the live book enters wider is untested — ATR regime at
those eight entry times versus a 2010–2026 average, or a sizing-path difference.
That is the next question and it is open."*

Dispatched under **`exit-refinement`** (its P2/P5 parity stage) per
`research-driver` Step 2, not freelanced.

## Tier

**Tier 1 throughout.** Research, tooling, and docs. **No arm, no disposition, no
config value, no `src/` path, no order path.** Verified by diff, not asserted —
`git diff origin/main` on `verdict` / `disposition` / `arm_r` returned **empty**.

## Starting Context

`config/lever_reachability.json` held five entries at `disposition:
queued_tier3`. Three rested on measured-but-bad verdicts; two were `unmeasured`.
Of the measured three, `qqq_trend_long_1d` was graded on **n=1** live package and
`xrp_pullback_2h` on a **truncated** 6-of-25 sample whose own note warned *"do
NOT read 33.3% as a lifetime rate."*

## Repo State Checked

- `main` @ `474bb79` at session start; ended `46c5c64`.
- PR #9691 open at start (docs-only), merged 13:43Z mid-session — its ROADMAP
  M31 edit was deliberately not duplicated.
- Concurrent session `sysrev-0816` held a P0 on `src/units/accounts/ib_client.py`
  + `docs/claude/health-review-backlog.json`; **avoided both**, routing this
  session's rows to the *performance* backlog instead.

## Files and Systems Inspected

Read directly, not inferred:

- `src/units/strategies/htf_pullback_trend_2h.py:319-326` + `:142-147` (`_atr`)
- `src/units/strategies/trend_donchian.py:125-132`, `:384-385`, `_atr`
- `scripts/backtest_pullback.py:432-433`, `:116-121`, the `--emit-trades` payload
- `src/runtime/position_telemetry.py::cap_r`
- `src/units/accounts/alpaca_client.py:189`
- `config/accounts.yaml`, `config/strategies.yaml`, `config/lever_reachability.json`
- `scripts/research/m20_fleet_exit_sweep.py` (p80 cell, `leg_v` assembly)

## Work Completed

**The mechanism, established by code before any run.** Live units and both
harnesses compute `sl = entry ∓ atr_stop_mult*atr` with **byte-identical** `_atr`
helpers, so `risk/entry ≡ atr_stop_mult × (ATR/close)`. `sl` is fixed at signal
time, before sizing. This **refutes the sizing-path hypothesis at the definition
level** and rules out an ATR-definition skew — the candidate that would have made
this a parity *bug* rather than a regime fact. Combined with the shipped
`position_telemetry.cap_r`:

```
cap_R = 0.099 / (atr_stop_mult × ATR/close)
```

so **a declared arm is a volatility threshold in disguise**: arm `A` on a leg
with stop-mult `M` fires only while `ATR/close ≤ 0.099/(M·A)`.

**The class is mechanical.** The six p80 arms shipped 2026-07-12/13; the harness
first gained `--tp-cap-pct` on 2026-08-10. They are p80s of an **uncapped** MFE
distribution applied to a **capped** live book. Prediction — a leg breaks iff its
uncapped p80 exceeds its own ceiling — held **5/5**.

**All five queued entries measured** (relays #9710, #9715; config-exact,
`--tp-cap-pct 0.099`, entry-conditioned):

| leg | n | arm | cap_R @med | reachable |
|---|--:|--:|--:|--:|
| `gld_pullback_1d` | 112 | 5.06 | 4.30 | 37.5% pooled · **1/7 in 2025-26** |
| `qqq_trend_long_1d` | 81 | 3.56 | 2.70 | 19.8% (n=1 verdict confirmed) |
| `xrp_pullback_2h` | 204 | 4.49 | 2.30 | **5.9%** (truncated basis overstated ~5.6×) |
| `trend_donchian_sol_4h` | 127 | 5.57 | 1.64 | **0.0%, every year** |
| `scha_trend_long_1d` | 65 | 2.00 | 2.65 | **83.1%** |

**Sharpest form:** `trend_donchian` and `trend_donchian_sol_4h` — same family,
same `atr_stop_mult` — got arms **1.16× apart** against ceilings **7.3× apart**.

**Tooling (#9730):** the sweep now compares its own proposed arm to the
`tp_r_effective_*` it already measured, emitting `p80_arm_reach` into
`verdicts.json`. Three states never collapsed (`capped` / `uncapped` / `unknown`).

## Validation Performed

- `scripts/ci/run_guards.py` **PASS / FAIL 0** on every commit, re-run *after*
  committing so commit-range scoping actually covered the paths.
- `tests/test_fleet_sweep_arm_reach.py` (5 tests) pins the arithmetic against
  ceilings **already published** in the merged memo, so drift breaks a live claim.
- **The writer was verified on a real artefact**, not its tests (relay #9734 run,
  #9737 read, **freshness-gated**). Both verdict branches fired; every field
  populated.
- The gld run **reproduced the overnight session's independent figures** —
  median 2.300% vs their 2.301%, live-band overlap 16/112 exactly.
- `check_lever_reachability.py` re-run after every registry edit: 8/8 current.

## Documentation Updated

- **NEW** `docs/research/m20-arm-reachability-is-a-vol-threshold-2026-08-16.md`
- `config/lever_reachability.json` — all five levers annotated; `sol_4h`'s
  `unmeasured_reason` corrected; the stale *"both testable, neither tested"* note
  resolved (flagged by a concurrent session on board #6927)
- `docs/claude/m20-m31-operator-decisions-2026-08-16.md` § 3 — the open question closed
- `ROADMAP.md` — M31 P4 recorded as the **binding blocker**
- `docs/claude/performance-review-backlog.json` — two new rows

## Contradictions or Drift Found

**Three of this session's own claims were wrong and were retracted in the merged
artifacts, not only in chat:**

1. *"It's the era"* — true for `gld` (which straddles its threshold), **false**
   for `sol_4h` (0/127 in every year). Era matters only near the boundary.
2. *"The cap is a Bybit rule on legs that never touch Bybit"* — holds for the
   Alpaca-only legs; **`qqq` also routes to `ib_paper`**, and IBKR is *reported*
   to reject stock orders >~10% from NBBO (unverified — its pages 403'd). A fix
   must therefore be **route-aware, not leg-aware**, which makes that Tier-3 item
   harder, not easier. The code comment's *"Bybit (and most exchanges)"* was more
   accurate than the first reading of it.
3. *"The check that would have caught the whole arm-above-cap class"* (#9730) —
   it catches **half**. `gld`'s 3.86R passes it while being unreachable live,
   because it grades against the **backtest** ceiling. Found by verifying the
   writer rather than trusting its passing tests.

Also corrected: a slip naming § 2's unmeasured pair as `scha`+`qqq` (it is
`sol_4h`+`scha`).

## Risks and Follow-Ups

- ⚠️ **`within_measured_median_ceiling` must NOT be read as "reachable in
  production."** Closing that half needs **M31 P4**.
- `PB-20260816-ARM-SWEEP-POOLS-VOL-ERAS` — half (1) done+verified, half (2)
  (per-era p80) **open**.
- `PB-20260816-BYBIT-TP-CAP-BINDS-ON-ALPACA-AND-IB-LEGS` — Tier-3, **open**.

## Deferred Items

- **Whether Alpaca accepts a farther TP in practice — untested.** Documentation
  is not the live API; only an order attempt settles it. Not run: the repo
  forbids the Alpaca MCP trading tools, which bypass `RiskManager` and the journal.
- Per-era p80 reporting.
- M31 P3 readers (unblocked, untouched).

## Next Recommended Sprint

**M31 P4 — backtest↔live MFE parity.** It is now the named blocker for the
remaining half of arm reachability, not merely the largest open item.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched — `docs/TRADE-PIPELINE.md` not applicable.
- [x] Roadmap status was checked and updated (M31 row).
- [x] Contradictions were recorded — including three of this session's own.
- [x] Remaining unknowns stated: the Alpaca acceptance test, and that the shipped
      check grades against the backtest ceiling only.
