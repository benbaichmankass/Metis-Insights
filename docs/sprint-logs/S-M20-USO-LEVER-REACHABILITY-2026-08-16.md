# Sprint Log: S-M20-USO-LEVER-REACHABILITY-2026-08-16

## Date Range
- Start: 2026-08-16 19:52 UTC
- End: 2026-08-16 20:15 UTC

## Objective

Answer the one row the concurrent overnight M20 session flagged and handed off in
its 19:53Z board release:

> `uso_trend_1h` is `disposition: ok` over `verdict: unmeasured` — "this lever is
> fine" asserted on top of "we have not looked" … Whoever owns the reachability
> queue should either measure it or record why `ok` is right without a measurement.

It is the one leg of the eight that
`S-M20-ARM-REACHABILITY-VOL-THRESHOLD-2026-08-16`'s entry-conditioned method
never covered — that sprint measured the five **queued** rows; this one was
`disposition: ok` and fell outside the sweep.

## Tier

**Tier 1 throughout.** A measurement record, one backlog row, two doc
corrections. **No `src/`, no config value, no arm, no disposition flip, no order
path.** `verdict` moves `unmeasured → reachable` because that is what the
measurement says; no queued Tier-3 proposal existed on this row, so it pre-empts
no operator decision.

## Starting Context

`main` @ `45b39e41`. The registry held **three** `unmeasured` rows, two of them
`queued_tier3` (the operator's call) and this one at `ok`. The guard permits the
pairing — only `recorded_inert` constrains its verdict — so this was never a CI
failure, it was a semantic gap: `ok` and `unmeasured` answer different questions,
and pairing them lets an unexamined lever read as cleared.

The row's `unmeasured_reason` carried an **argument**, not an observation: the
arm "needs only `risk/entry <= 9.9%` … a threshold no plausible 1h ATR
approaches."

## Repo State Checked

- Board #6927 read to comment 934 (the overnight session's 19:53Z release) before
  the first substantive call; `▶️ START` posted naming the one row and the file.
- The concurrent overnight M20 session had merged **four** PRs against
  `config/lever_reachability.json` in ~90 minutes (#9748, #9765, #9771, #9772).
  Edits here were confined to the `uso_trend_1h` object and the shared
  `basis_note`; **their two rows were deliberately not touched.**
- Lane: live-VM diag relay. The trainer heavy lane was left free.

## Files and Systems Inspected

Read directly, not inferred:

- `config/lever_reachability.json` (full row + legends + `basis_note`)
- `config/strategies.yaml::uso_trend_1h` (`atr_stop_mult: 2.5`, `tp_r: 50.0`)
- `scripts/ci/check_lever_reachability.py` (what the guard does and does not enforce)
- `src/runtime/exit_plan_realism.py:44` + `clamp_exit_plan`
- `src/runtime/exit_plan_materializer.py:157`
- `src/units/accounts/execute.py:391 / :630-648 / :703 / :1467 / :1875 / :1912` —
  the placement path, read to settle Contradictions (1)
- `scripts/research/m20_fleet_exit_sweep.py` (`LIVE_TP_CAPPED_FAMILIES`, leg enumeration)
- Live `order_packages` via relay #9778 / #9780 / #9781 / #9782 / #9783

## Work Completed

**The population, stated.** Live `order_packages` — the authoritative basis, and
**COMPLETE**: envelope `total` 30, `filter_state: applied` on all five pages,
**30/30 recovered, 0 lost to truncation**, span 2026-07-07 → 2026-08-07. Unlike
`xrp_pullback_2h` every page returned inside the relay byte budget with its
certification fields intact, so this is the whole population, not a bounded
interval.

```
OPERATIVE ceiling  min 2.9837 · median 4.2924 · max 7.5639   arm 1.0 -> 30/30 (100%)
  (= the VENUE TP cap, 0.099/(risk_per_unit/entry) — the value that reaches the broker)
clamped ladder     min 2.9837 · median 4.2924 · max 5.0000   observe-only counterfactual
  (= exit_plan_state.targets[].reach_r = min(venue_cap_R, 5.0); would bind on 7/30)
by status: rejected 23/23 | closed 7/7
```

The venue cap is the operative ceiling — see Contradictions (1), which records
three positions on this question before it settled. **The grade is identical
under either basis**, because arm 1.0 sits far below both; only the reported max
moves. That is why the flip-flop was survivable on this leg and would not have
been on a high-arm one.

The **worst** entry in the entire population clears the arm by **2.98×**. Arm 1.0
needs `risk/entry <= 9.9%` (`ATR/close <= 3.960%` on a 1h bar); observed
`ATR/close` is **0.524%–1.327%**, so the tightest hour this leg has ever seen is
3.0× away from binding.

**It did not merely clear the ceiling — it fired.** `pkg-29261c70cb4e41ba`
(trade 4511, opened 08-07, closed 08-13) closed `close_reason: giveback_stop`.
That is a direct observation of the lever arming and executing, and it is
stronger than any ceiling arithmetic: the other seven rows in this registry are
graded on whether they *could* fire; this one is graded on the fact that it
*did*.

**The prior argument was right, and is recorded as confirmed rather than
deleted.** Measured, it holds by a factor of 3. It is logged as *confirmed*
because the same screen-based reasoning **overstated** reachability on every leg
it was checked against — xrp 90.5% → 5.9% (backtest) → 0.0% (live), sol_4h 2.8%
→ 0.0%. Being right here is a fact about this leg, not a licence to trust the
method.

## Validation Performed

- `scripts/ci/check_lever_reachability.py` — **8/8 current**. It first **rejected**
  the change (`verdict=reachable claims a measurement but observations is None`);
  fixed by adding the real count, not by loosening the guard. This is the guard
  behaving exactly as its docstring promises — not presence-only.
- `scripts/ci/run_guards.py` — **PASS 15 · FAIL 0**, re-run *after* committing so
  `lever-reachability-guard` and `canonical-doc-coherence` were actually
  selected. An uncommitted run reported PASS while scanning **nothing** (guard
  relevance is commit-range scoped) and said so; that warning is load-bearing.
- Identity cross-check: **0 mismatches on 30/30**, comparing
  `0.099/(risk_per_unit/entry)` against the **pre-clamp** value.
- `filter_state: applied` asserted on every page before any `total` was trusted
  (`BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN`).

## Documentation Updated

- `config/lever_reachability.json` — the `uso_trend_1h` row (verdict, observations,
  basis, `live_entry_conditioned_2026_08_16`), and the shared `basis_note`
- `docs/claude/performance-review-backlog.json` — `PB-20260816-REACH-R-IS-CLAMPED-NOT-CAP-R`
- `docs/research/m20-arm-reachability-is-a-vol-threshold-2026-08-16.md` — one
  false sentence corrected

## Contradictions or Drift Found

**1. I took THREE positions on which field is the reachability ceiling, and only
the third was settled on evidence.** Recorded in sequence, because the sequence
is the lesson:

- **Position 1 (in the PR as first written):** the venue TP cap
  `0.099/(risk_per_unit/entry)` is the ceiling; `exit_plan_state.targets[].reach_r`
  is `min(cap_R, 5.0)` and under-reports it. Right conclusion, stated for a weak
  reason (that the identity "looks authoritative").
- **Position 2 (retraction, 20:28Z — WRONG, and it MERGED in PR #9785):** on
  reading a concurrent session's `read_the_right_field` note (PR #9779, merged
  `58799055`) I conceded and rewrote the `basis_note`, this log, the backlog row
  and the `uso_trend_1h` max (7.5639 → 5.0) to say the **clamped** value is
  operative. The argument was "the TP the bot places is the clamped one" — an
  assumption about the placement path, not a reading of it.
- **Position 3 (settled, and what this file now says):** I read the placement
  path. `src/units/accounts/execute.py:391` builds the order with `"tp": pkg.tp`;
  `:703` and `:1467` send `kwargs["takeProfit"] = quantize_price(order["tp"], tick)`;
  `:1875`/`:1912` journal `take_profit_1 = float(pkg.tp)`. **The clamp never
  touches a placed order.** `clamp_exit_plan` rewrites the observe-only ExitPlan
  ladder, whose sole reference in `execute.py` is the soak block at `:630-648`
  — its own comment reads *"nothing reads it back (graduation to a real laddered
  exit is the backtest-gated P4)"*. So Position 1's conclusion stands and
  Position 2 was a regression.

**The test that settles it is neither "which field looks authoritative" nor
"which is more conservative" — it is WHICH VALUE REACHES `kwargs["takeProfit"]`.**
That is a code path, and I should have read it before either the original claim
or the retraction. Conceding to a concurrent session's confident note without
opening the file is the same failure as asserting without checking.

**Worked example, both values on one order.** `pkg-61cb138b930c4a05`:
`tp = 117.66993` (entry 107.07, +9.90% = the venue cap, **7.19R**) is PLACED;
`exit_plan_state.targets[0].price = 114.445` (**5.0R**) is the counterfactual.

**Blast radius of Position 2, had it stood.** Reading the clamped field caps
every observation at 5.0, so the three arms above the clamp — `trend_donchian`
6.49, `trend_donchian_sol_4h` 5.57, `gld_pullback_1d` 5.06 — grade a blanket
false `inert` (`trend_donchian` flips from reachable-at-100% to inert). **No
landed verdict was actually wrong**, checked individually: arms below 5.0 are
unaffected because `min(x,5) >= arm` iff `x >= arm` when `arm < 5`, which is why
`xrp_pullback_2h`'s 2/37 at arm 4.49 is correct as recorded; `gld`'s rows
(2.20–3.01) sit below the clamp; `trend_donchian`'s p50 11.91 came from the audit
script's venue-cap computation.

**The `uso_trend_1h` verdict is basis-independent and unchanged** — 30/30 clearing
arm 1.0 at a tightest margin of 2.98×. Only the reported max moves (7.5639 under
the operative basis, 5.0000 under the clamped one), and it is now restored to
7.5639.

**What survives as an open item** is narrower than what I originally filed, and
narrower again than the retraction claimed: `position_telemetry.py::cap_r`
computes the venue cap and is **correct** — the withdrawn claim that it needs a
`min(…, 5.0)` is itself wrong. The residual is documentation-only: nothing at
`exit_plan_realism.py::clamp_exit_plan` or on the `exit_plan_state` column says
the clamped ladder is never placed, which is what let two readers in one day
mistake it for the operative geometry. Rewritten as
`PB-20260816-REACH-R-IS-CLAMPED-NOT-CAP-R` with that scope, kept as a row rather
than deleted so the inverted rule is not re-derived a third time.

**2. My own memo was wrong on its own page.** It called `trend_donchian_sol_4h`
"the last entry recorded `unmeasured`" while describing `scha_trend_long_1d` as
`unmeasured` two bullets above, and `uso_trend_1h` was a third. Corrected with
the error named rather than silently overwritten.

**3. I made the SAME "wrong on its own page" error I had just corrected — in
this log, and on the coordination board.** Both claimed the registry would hold
"zero `unmeasured` rows" after this merge. It holds **two**
(`trend_donchian_sol_4h`, `scha_trend_long_1d`). The board comment even printed a
table showing both rows as `unmeasured` directly under the prose asserting none
existed — the identical shape as the memo sentence corrected in (2), committed
minutes after correcting it. Caught by re-deriving the claim from the file, not
by re-reading the sentence, which is the whole point of *cross-check with
arithmetic*. The cause is worth naming: the two rows gained a **live basis** in
their sub-objects from the concurrent session, and I read "has a measurement" as
"is graded" — a sub-object basis is not a top-level verdict, and treating them as
one is the collapse this registry exists to prevent.

**4. A relay request was rejected by my own error, not the relay's.** #9777 put
prose in the body; the relay parses the body as a one-path-per-line list and it
**wins over the title**, so the first prose line became the "path". Re-dispatched
with a bare-path body. Worth recording because the failure message was precise
and the mistake was mine.

## Risks and Follow-Ups

- `PB-20260816-REACH-R-IS-CLAMPED-NOT-CAP-R` — **open, and rewritten** (see
  Contradictions 1). Two successive resolution criteria were withdrawn: the first
  asked for the concurrent session's rows to be reworded (premature — the question
  was not yet settled), the second asked `position_telemetry.cap_r` to model the
  clamp (**wrong** — that accessor computes the venue cap and is correct as it
  stands). The row's remaining scope is documentation-only: `clamp_exit_plan` and
  the `exit_plan_state` column say nothing about the ladder never being placed,
  which is what let two readers in one day take it for the operative geometry.
  Not a runtime proposal.
- **TWO rows still carry `verdict: unmeasured`** — `trend_donchian_sol_4h` and
  `scha_trend_long_1d`. A draft of this log claimed the registry now holds "zero
  `unmeasured` rows"; that is **false**, and it was checked against the file
  rather than caught by reading. Both rows gained a *live basis* in their
  sub-objects from the concurrent session, but their **top-level verdict was
  deliberately left pending** — a sub-object measurement is not a graded verdict,
  and conflating the two is exactly the collapse this registry exists to prevent.
  This session moved **one** row (`uso_trend_1h`) from `unmeasured` to
  `reachable`; it did not clear the field.
- **Three remain `queued_tier3`** (`sol_4h`, `xrp`, `scha`) awaiting the operator.
  Nothing here decided any of them.

## Deferred Items

- `PB-20260816-ARM-SWEEP-POOLS-VOL-ERAS` half (2) — per-era p80 reporting. Still
  open, and must be verified on a leg whose pooled and recent-era figures
  **differ**; a leg where they coincide demonstrates nothing.
- `PB-20260816-BYBIT-TP-CAP-BINDS-ON-ALPACA-AND-IB-LEGS` — Tier-3, open.
- M31 P3 readers — unblocked, untouched.

## Next Recommended Sprint

**M31 P4 — backtest↔live MFE parity.** Unchanged from the previous sprint's
recommendation and still the named binding blocker for the other half of arm
reachability: the shipped `p80_arm_reach` check grades against the **backtest**
ceiling, so `within_measured_median_ceiling` still must not be read as "reachable
in production."

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched — `docs/TRADE-PIPELINE.md` not applicable.
- [x] Roadmap checked — M31's status cell already records P4 as the binding
      blocker (set in the prior sprint); this sprint changes nothing there, so it
      was deliberately not edited.
- [x] Contradictions were recorded — including FOUR of this session's own and one
      relay mistake.
- [x] Remaining unknowns stated: whether the observe-only ladder ever graduates
      (backtest-gated P4 — if it does, the operative ceiling changes and every
      row above 5.0R must be re-graded), and the two per-row `basis` strings left
      to their owner.
