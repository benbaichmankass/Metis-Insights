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
cap_R   min 2.9837 · median 4.2924 · max 7.5639        arm 1.0 -> 30/30 (100%)
by status: rejected 23/23 | closed 7/7
```

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

**1. `exit_plan_state.targets[].reach_r` is `min(cap_R, 5.0)`, not `cap_R`.**
`exit_plan_realism.py:44` sets `DEFAULT_MAX_REACH_R = 5.0`; `clamp_exit_plan()`
rewrites any fixed final target beyond it and parks the pre-clamp value in
`realism_notes[].reach_r`. **7 of these 30 rows are clamped** — reading the
stored field reports a max of `5.0000` against a true `7.5639`.

The direction is why it is filed rather than noted: it **UNDER**-reports the
ceiling, so it makes a lever look **less** reachable than it is and manufactures
false `inert` / `vol_conditional` verdicts — this registry's own failure class
running backwards. It is invisible on any leg whose `cap_R` never exceeds 5.

**The three rows landed today are unaffected, and this was checked rather than
assumed** — `sol_4h` max 4.32, `xrp` max 4.46, both below the clamp, so no stored
value there was rewritten and their recorded numbers stand. The overnight
session's method also self-protects: it cross-checked `reach_r` against the
identity and reported 0 mismatches, and a clamped row would have surfaced *as* a
mismatch. Fixed in the shared `basis_note`; **their two rows' wording was left to
them.** `position_telemetry.py::cap_r` computes the value directly and is correct.

**2. My own memo was wrong on its own page.** It called `trend_donchian_sol_4h`
"the last entry recorded `unmeasured`" while describing `scha_trend_long_1d` as
`unmeasured` two bullets above, and `uso_trend_1h` was a third. Corrected with
the error named rather than silently overwritten.

**3. A relay request was rejected by my own error, not the relay's.** #9777 put
prose in the body; the relay parses the body as a one-path-per-line list and it
**wins over the title**, so the first prose line became the "path". Re-dispatched
with a bare-path body. Worth recording because the failure message was precise
and the mistake was mine.

## Risks and Follow-Ups

- `PB-20260816-REACH-R-IS-CLAMPED-NOT-CAP-R` — **open.** The shared note is fixed;
  the two per-row `basis` strings that present `reach_r` as the source are the
  overnight session's call.
- The registry now holds **zero `unmeasured` rows**, but **three remain
  `queued_tier3`** (`sol_4h`, `xrp`, `scha`) awaiting the operator. Nothing here
  decided any of them.

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
- [x] Contradictions were recorded — including two of this session's own and one
      relay mistake.
- [x] Remaining unknowns stated: the clamp's effect on any future leg whose
      `cap_R` exceeds 5, and the two per-row `basis` strings left to their owner.
