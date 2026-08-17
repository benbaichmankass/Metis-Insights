# M31 P5 — a lever that READS telemetry. Proposal, and why it does not ship yet.

**Status: PROPOSED, NOT SHIPPED. Tier-3.**
Written 2026-08-17 at the close of M31 P3/P4. Nothing in this document has been
merged into an order path, and nothing here should be read as approved.

> **This is not a claim of edge.** It is a description of the one change P5
> would make first, the gate it has to clear, and the measured reason that gate
> is currently **not met**.

## 1. What P5 is

M31's phase table defines P5 as *"any lever that READS telemetry to change an
exit"*, Tier-3, gated on *"the same backtest gate as every other lever"*.

P1–P4 were deliberately measurement-only. P5 is the first phase that would let
`position_telemetry` change what happens to money.

## 2. The gate is NOT met. This is the whole finding.

Three measurements from this milestone, each with its population stated:

| | measured | what it means for P5 |
|---|---|---|
| **P4 Check B (parity)** | **abstains** — live final-MFE population is **n=1** fleet-wide (14 telemetry rows = 13 open + 1 closed, lifecycle join certified `filter_state: applied`, `total: 27`) | there is **no established backtest↔live MFE parity** for a telemetry-reading lever to stand on |
| **M20 lever firings, lifetime** | **13** (`stale_stop` 10 · `exit_head` 2 · `giveback_stop` 1, that one on paper) against **1,142 closed trades** | the live journal **cannot grade** an exit lever at this n, and waiting does not fix it |
| **Harness per-trade `mfe_r`** | **not committed anywhere** — key census over all 1,376 corpus rows found cell aggregates only | the backtest half of the gate has no standing artifact either |

A P5 lever shipped today would be tuned on a harness whose agreement with the
live book **has never been checked**, which is the exact defect family M31 was
created to close (*the harness measured a book production does not run*).
Shipping it now would spend the milestone's own result.

## 3. The candidate, named so it is not re-derived

**`rr_from_here` floor** — close (or refuse to keep holding) a position whose
remaining upside no longer justifies the give-back at risk:

```
rr_from_here = r_to_target / r_to_stop
```

It is the right FIRST P5 lever for three reasons:

1. **It is the milestone's own motivating question.** *"Should we hold this
   18-day XRP short?"* On that trade (4163, `xrp_pullback_2h`, real money,
   `bybit_2`) `rr_from_here` = 1.04 / 1.46 = **0.71** — holding for the target
   risks more than it stands to make. Nothing computed that before P2.
2. **It reads a field that is already correct and already stored**, rather than
   needing a new derivation. `r_to_stop`/`r_to_target` are computed from the
   live price and the row's own geometry, and are `None` (never `0.0`) whenever
   either level sits the wrong side of price.
3. **It does not depend on `peak_r`** — and `peak_r` is the field with the
   unresolved lower-bound problem (§ 4). A first lever that avoids the weakest
   input is the cheaper thing to get right.

**Explicitly NOT proposed first:** a giveback/trailing lever driven by stored
`peak_r`. `giveback_min_mfe_r` already exists computed transiently, and swapping
its input to a stored lower bound would make the lever fire **late** by an
unquantified amount (§ 4) while looking like a refactor.

## 4. A blocker P5 inherits, and must not paper over

`position_telemetry.peak_r` is a **LOWER BOUND on true MFE**: the last write
precedes the close by up to one exit-loop pass, and a bar extreme cannot see an
intrabar excursion (hence `peak_provenance: estimated`, never `measured`). The
size of that gap is **unquantified**, and it does not shrink with soak.

⚠️ **The terminal writer NARROWED this and did not close it** (shipped
2026-08-17 — see § 5.1). An earlier draft of this section said the gap "closes
only when a terminal writer exists", which overstated what that change does: the
stamp records *when we observed finality*, not a re-measured peak, so
`peak_r_is_lower_bound` stays `true` on stamped rows too. **Quantifying the
residual is still open work**, and it remains the reason a `peak_r`-driven
giveback lever is deliberately not first.

The *other* half of this blocker IS closed: the table now carries an explicit
finality marker, so a lever no longer has to join `trades` to know whether the
trade it is acting on is still live.

## 5. Preconditions — falsifiable, in order

P5 may be proposed for approval when **all** hold. Each is checkable, not a
judgement call:

1. ✅ **MET 2026-08-17 (partially — read the caveat).**
   `PB-20260817-TELEMETRY-HAS-NO-TERMINAL-SNAPSHOT` is closed: `update_trade`
   stamps `terminal_state='final'` + `terminal_at` on close, never overwriting
   an existing stamp and never inserting a row, and readers publish a four-state
   `finality_source` (`stamped` / `derived_join` / `not_final` / `unknown`)
   registered with `collapsed-state-guard`. **Finality is now a stamped fact.**
   ⚠️ The precondition as originally worded also asked for `peak_r`'s
   lower-bound gap to be *"closed or measured"* — it is **neither**; see § 4.
   A lever that does not read `peak_r` (the `rr_from_here` floor proposed here)
   is unaffected; a `peak_r`-driven lever is still blocked on this.
   Sprint log: `docs/sprint-logs/S-M31-TERMINAL-STAMP-2026-08-17.md`.
2. **P4 Check B returns `compared` on at least one leg** — i.e.
   `scripts/research/m31_mfe_parity.py` finds ≥ 8 final live rows for a leg AND
   a harness `mfe_r` distribution for it, and reports `parity: consistent`.
   `insufficient_n` is not a pass.
3. **A walk-forward on the `rr_from_here` floor CLEARS the do-nothing arm** —
   not merely beats an alternative lever.
   ⚠️ **This has an unstated prerequisite: the lever does not exist in the
   harness** (measured 2026-08-17,
   `PB-20260817-RR-FROM-HERE-LEVER-ABSENT-FROM-HARNESS`).
   `scripts/backtest_trend.py` implements `stale_exit_bars`,
   `giveback_min_mfe_r`/`giveback_r` and `trail_decay_*` — verified as a
   **positive control**, the same probe finds all three — and finds nothing for
   `rr_from_here` / `r_to_target` / `r_to_stop`. The quantity lives in exactly
   one module repo-wide, `src/runtime/position_telemetry.py`, which is live and
   observe-only. So this precondition is **implement-then-measure**, not
   measure, and phrasing it as "run the walk-forward" would be a claim about
   backtest evidence no artifact could produce — the shape M31 exists to close.
   The inputs ARE present (the harness carries `tp_cap_pct`/`tp_r` and the stop
   geometry), so it is tractable, not blocked. This is the standard
   `BL-20260811-FLIP-OVERRIDE-NEVER-WALKFORWARDED` learned the hard way: the
   live `0.15/4.0` flip override lost to plain `hold` and had run on real money
   for a day with no walk-forward behind it.
4. **Inert folds are excluded from the win count** — a fold where the lever
   changed nothing counts as neither win nor loss
   (`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`; measured at **75 of
   386 `ok` folds, 19.4%**). Use `wins_effective`, not `wins`.
5. **The declared arm is reachable on the live book** — `arm_reach` must not be
   `unreachable` for the leg it is declared on. Live today, 2 of 3 declared arms
   in the telemetry table are `unreachable` (`xrp_pullback_2h` 4.49 vs cap
   3.9233; `qqq_trend_long_1d` 3.56 vs cap 2.1258).

## 6. Shape, when it does ship

- A `*_MODE` env var (`off` → `annotate` → `apply`), **never** a default-off
  `*_ENABLED` gate (Prime Directive). `off` is byte-for-byte unchanged.
- An `annotate` soak first, written and **read** before any apply — the P3
  reader exists precisely so a new signal does not repeat the write-only shape.
- An accounts allowlist, remembering that **empty means ALL accounts including
  real money** — the widest setting, not a safe default.
- Rollback is one env flip plus a restart, no redeploy.
- Reductive first: a lever that can only close earlier is a smaller claim than
  one that can also hold longer.

## 7. Failure signal

If a P5 lever ships and, after a month, its soak shows **zero rows where the
lever would have changed the outcome**, it is inert and should be withdrawn
rather than left declared — the cosmetic-cell anti-pattern
(`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS`). A declared-but-inert lever is
worse than none: it reads as coverage.

## 8. Recommendation

**Do not ship a P5 lever now.** The Tier-2 terminal writer is done (2026-08-17,
precondition 1); **precondition 2 is the binding one and it is a data-accrual
problem the writer does not solve** — it makes future closes gradeable without a
join, but it creates no closed trades, and the fleet-wide final population is
still n=1. So: let the soak reach Check B's floor, run the walk-forward
(precondition 3 — still the only remaining item that is *work* rather than
waiting, but **implement-then-measure**: the lever must first exist in the
harness, per the warning in § 5. A failure there would retire this candidate
outright, which is worth learning before the soak matures), then bring the exact
diff for approval. M31's other four phases are complete and the milestone's value —
making the exit-lever programme *checkable* — is already delivered without P5.
