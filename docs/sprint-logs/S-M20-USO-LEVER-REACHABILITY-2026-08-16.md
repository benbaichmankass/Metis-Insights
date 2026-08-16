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
OPERATIVE ceiling  min 2.9837 · median 4.2924 · max 5.0000   arm 1.0 -> 30/30 (100%)
  (= targets[].reach_r = min(venue_cap_R, DEFAULT_MAX_REACH_R 5.0); clamp binds on 7/30)
venue TP cap       min 2.9837 · median 4.2924 · max 7.5639   secondary, NOT the ceiling
by status: rejected 23/23 | closed 7/7
```

The operative ceiling is the one to read — see Contradictions (1); an earlier
draft of this log reported the venue cap as the headline and was corrected before
merge. The grade is identical under either basis.

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

**1. I filed a finding with its direction INVERTED, and a concurrent session
landed the correct reading the same hour.** I recorded that
`exit_plan_state.targets[].reach_r` is `min(cap_R, 5.0)` — true — and concluded
that reading it **under**-reports the ceiling and manufactures false `inert`
verdicts, with the identity `0.099/(risk_per_unit/entry)` as authoritative.
**Both halves are backwards.**

Reachability asks whether an arm can be reached *before the trade's exit closes
it*. The TP the bot actually places is the **clamped** one, and that TP fills and
ends the trade — so the operative ceiling is `min(venue_cap_R, 5.0)`, which is
exactly `targets[].reach_r`. The identity ignores the clamp, so where
`cap_R > 5.0` it **over**-reports and would grade a 6R arm `reachable` on a row
whose placed target is 5.0R. The canonical statement is the concurrent session's
`read_the_right_field` note (PR #9779, merged `58799055`), with the worked
example `pkg-639da91607cc46d3`: original 8.3837R clamped to 5.0000R.

**Caught before merging, and only because their correction touched a claim in
this PR** — I read their landed row while checking my own "xrp max 4.46,
unaffected" line before claiming the merge slot. Had the `basis_note` merged as
written, `config/lever_reachability.json` would have carried **two contradictory
instructions**: mine at file level saying trust the identity, theirs in the xrp
row saying read `targets[].reach_r`. That is worse than the gap it tried to
close.

**The `uso_trend_1h` verdict is basis-independent and unchanged.** On the
operative basis: min 2.9837 · median 4.2924 · **max 5.0000** (was 7.5639), clamp
binding on **7/30**, still **30/30** clearing arm 1.0 at a tightest margin of
2.98×. Arm 1.0 sits far below even the clamp, so min and median are identical
under both readings and only the reported max moves. That is *why the error was
survivable here* — and it would not have been on a leg with a high arm, which is
the part worth remembering.

**What survives as an open item** is narrower than what I filed:
`src/runtime/position_telemetry.py::cap_r` computes the venue cap and does not
model the clamp. Correct as a record of the venue constraint; wrong if any
consumer reads it as the ceiling. Rewritten as
`PB-20260816-REACH-R-IS-CLAMPED-NOT-CAP-R` with that scope, kept as a row rather
than deleted so the inverted rule is not re-derived.

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
  Contradictions 1). Its original resolution criterion asked for the concurrent
  session's rows to be reworded to name the identity as the source; that criterion
  was **itself the inverted rule** and would have propagated the error into their
  work. Withdrawn. The row's remaining scope is `position_telemetry.cap_r`, which
  computes the venue cap and does not model the clamp — a docstring/consumer
  question, not a runtime proposal.
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
- [x] Remaining unknowns stated: the clamp's effect on any future leg whose
      `cap_R` exceeds 5, and the two per-row `basis` strings left to their owner.
