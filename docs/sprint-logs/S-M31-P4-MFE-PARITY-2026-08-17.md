# S-M31-P4-MFE-PARITY — backtest↔live MFE parity, shipped and run

- **Sprint ID:** S-M31-P4-MFE-PARITY-2026-08-17
- **Milestone:** M31 (position telemetry) — **P4**
- **Dates:** 2026-08-17 → 2026-08-17
- **Tier:** 1 (research tooling + CI + docs). No `src/`, no `config/strategies.yaml`,
  no order path, no VM mutation.

## Objective

Ship M31 **P4 — backtest↔live MFE parity**: compare the harness's per-trade
`mfe_r` against the live `position_telemetry.peak_r` for the same leg, so the
tp-cap defect family moves from *found by accident* to *found by a check*.

P4 was the **named binding blocker** for the arm-reachability programme: the
fleet sweep grades its own proposal (`verdicts.json::p80_arm_reach`) **only
against the BACKTEST ceiling**, and `gld_pullback_1d` passes that at 3.86R while
being unreachable on the live book (0 of 8 entries).

## Files and Systems Inspected

Read directly, not inferred:

- `src/runtime/position_telemetry.py` (full: `build_record`, `write_record`, the
  UPSERT, `cap_r`) and its **only two callers** —
  `src/units/strategies/trend_donchian.py:967` and
  `htf_pullback_trend_2h.py:534`, both on the OPEN-position monitor path
- `src/units/db/database.py:629-660` — the `position_telemetry` DDL
- `scripts/backtest_trend.py` — `Trade.mfe_r` (`:75`), accumulation (`:514`/`:533`),
  the TP-cap block (`:450-468`), the per-trade emit schema (`:587-640`),
  `max_mfe_r` in the summary (`:787`)
- `docs/design/position-telemetry-DESIGN.md` §§ 4, 5, 7
- `docs/research/m20-sweep-corpus.jsonl` (1,376 rows) — `tp_cap_pct` census
- `config/lever_reachability.json` — all 8 declared reach-gates
- Live `position_telemetry` + `db_info` + `trades` via relays #9848 / #9850 / #9851

## Work Completed

### The checker — `scripts/research/m31_mfe_parity.py`

**Two checks, deliberately separate, because they need different denominators.**
Collapsing them would have made P4 read as blocked (Check B alone) or as proven
(Check A alone), and neither is true.

- **Check A — ceiling.** A live trade's `peak_r` must not exceed its own `cap_r`,
  the venue TP ceiling that ends the trade. **Per-row, so gradeable at n=1** —
  it works the day telemetry starts writing.
- **Check B — distribution.** Harness `mfe_r` vs live `peak_r` per leg. Needs
  FINAL (post-close) live MFE at n, and **abstains** until it has it.

**Three refusals, each a defect the tool would otherwise commit:**

1. **Never grades a live row whose lifecycle it does not know.** The table is
   `order_package_id PRIMARY KEY` + UPSERT with **no status column**; a closed
   trade's row simply stops being updated, so open and closed are byte-identical
   from the table alone. `peak_r` on an OPEN trade is a partial, not that trade's
   MFE, so grading without the `trades` join biases the live distribution
   **downward** by exactly the trades that have not peaked yet → `live_lifecycle_unknown`.
2. **Never compares against an UNCAPPED harness book.** `backtest_trend.py`
   defaults `tp_cap_pct=0.0` — *no take-profit exit path at all* — and an
   uncapped book's `mfe_r` runs past the venue ceiling by construction. That is
   a category error, not a parity failure → `harness_uncapped`.
3. **Never treats live `peak_r` as MFE-final.** Even on a closed trade the last
   write precedes the close by up to one exit-loop pass, so it is a **LOWER
   BOUND**. `live_peak_is_lower_bound: true` is stamped on every Check-B record,
   and divergence is only ever called in the direction that bound permits (live
   ABOVE the harness max), never below.

Seven never-collapsed `parity_state` values (`compared` · `harness_absent` ·
`harness_uncapped` · `live_absent` · `live_lifecycle_unknown` ·
`live_no_final_rows` · `insufficient_n`) and four `ceiling_state` values
(`within_cap` · `above_cap` · `no_cap` · `no_rows`), so an ungraded leg can
never read as a passing one.

It also **asserts `filter_state`** on any Data-Explorer envelope it reads
(`BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN`) and refuses
the rows outright when the server dropped the filter.

### CHECK A RESULT — the first live MFE-vs-ceiling measurement ever taken

Live population: **14 rows, 14 legs, one row each** (envelope `total: 14`,
corroborated by `db_info` `position_telemetry: 14`).

```
14/14 within_cap · 0 breaches · 0 ungradeable
peak_r as % of that row's own cap_r:
  xrp_pullback_2h        87.1%   <- closest to its ceiling
  ada_pullback_2h        73.3%
  trend_donchian_ada_4h  60.6%
  spy_trend_long_1d      31.8%   ... 11 more, all below
```

The invariant holds on every row. This is a real negative with a stated
denominator, not an absence of evidence.

### CHECK B RESULT — abstains, and the abstention is the finding

The lifecycle join was **done, not assumed**. Open-trade set pulled with
`filter_col=status eq open`, returning `filter_state: "applied"` and
`total: 27` — a certified-complete set, so a telemetry `trade_id` absent from it
is closed by construction rather than by inference.

```
telemetry rows 14 · OPEN 13 · CLOSED 1  (trade 4697, trend_donchian_sol_4h)
```

So the **live final-MFE population is n=1 across the entire fleet**, one leg
deep, against a floor of 8. Check B abstains on every leg. P4's own gate in the
design table is *"needs P2 soak depth"* — that gate is real and **not yet met**.
Reporting a parity verdict off this population would have been the exact sin the
tool exists to prevent, so it does not.

**The harness side is missing too, and separately.** The committed corpus
carries **no per-trade `mfe_r`** — verified by a key census over all 1,376 rows,
which hold cell-level aggregates only. `mfe_r` exists per trade in
`backtest_trend.py` and reaches the `--emit-trades` JSONL, but that artifact is
not committed anywhere. So Check B needs **both** halves: live soak depth *and*
a sweep re-run with `--emit-trades`. Today it reports `harness_absent`, which is
the honest state and not a live-side problem.

### The harness models the cap — measured, not assumed

`tp_cap_pct` across **all 1,376** corpus rows: **1,376 at `0.099`, zero at `0`**
(and 227 of 227 on the newest run per `(leg, lever)`). So although the harness
*default* is off, every committed sweep passes the live cap. Check B's
`harness_uncapped` refusal is a live guard against a regression, not a
description of today's corpus.

## Validation Performed

- `m31_mfe_parity.py --self-test` — **10/10 PASS**. Cases 1 and 9 assert the
  probe FLAGS a planted ceiling breach and a planted divergence; 3–7 assert it
  ABSTAINS (`no_cap`, `live_lifecycle_unknown`, `live_no_final_rows`,
  `insufficient_n`, `harness_uncapped`) rather than passing. A checker that
  cannot fail is not evidence, so the positives are asserted first.
- `scripts/ci/run_guards.py` — PASS, with `mfe-parity-instrument-guard` selected.
- Ran against the real 14-row live payload; output reproduced above.

## Documentation Updated

- `scripts/research/m31_mfe_parity.py` (new)
- `scripts/ci/run_guards.py` — `mfe-parity-instrument-guard`
- `ROADMAP.md` — M31 P4 status
- `docs/claude/performance-review-backlog.json` — the two blockers below
- this log

## Contradictions or Drift Found

**1. P2 shipped half of its own design recommendation, and P4 is blocked on the
other half.** `docs/design/position-telemetry-DESIGN.md` § 7.2 recommends *"live
row + a terminal snapshot written at close"*, giving P4 "one honest per-trade
MFE without per-bar volume". The live row shipped; **the terminal snapshot did
not**. Verified by reading the module and its callers: `write_record` is an
UPSERT, there is no close-path writer, and both callers are open-position
monitors. Filed `PB-20260817-TELEMETRY-HAS-NO-TERMINAL-SNAPSHOT`.

**2. The table cannot say whether a row is final.** No `status`, no `closed_at`,
no terminal marker — a closed trade's row is identical to an open one that has
not been updated recently. This is the **collapsed-state** class this repo has a
canonical rule and a CI guard for, in a table shipped *after* that rule.

Confirmed live, and it is exactly a 1-in-14 needle: `trend_donchian_sol_4h`'s row
(trade 4697) last updated 2026-08-17T04:10Z with `r_to_stop: 0.0337` — and the
certified open set (`total: 27`, `filter_state: applied`) does **not** contain
4697, so it is closed. **The row is byte-shaped like the 13 open ones**; the only
in-table hint was a staler `updated_at`, which is not a signal (a quiet leg and a
closed leg both go stale). Mitigation available today is the `trade_id` →
`trades.status` join, which is why the checker demands it and refuses to grade
without it; the durable fix is (1).

**3. A ceiling verdict recorded as complete has already moved.** The prior sprint
measured `trend_donchian_sol_4h` at **0 of 16** over a population correctly
described as complete *at that time*. The live telemetry row for that leg carries
`cap_r 5.8294` against its declared `arm_r 5.57` — **a 17th observation in which
the arm IS below the cap**. Nothing is decided here (the row stays
`unmeasured`/`queued_tier3`, the operator's), but it is a concrete argument for a
standing check over one-off audits: a "complete" population is complete only up
to its timestamp.

**4. `qqq_trend_long_1d`'s `inert` verdict is corroborated on a fresh row.** Live
`cap_r 2.1258` against declared `arm_r 3.56` — the arm sits at **167% of the
cap**, unreachable. The registry already grades it `inert`/`recorded_inert` on
`observations: 1`; this is an independent second observation agreeing, not a new
finding.

**5. `pct_of_cap` is computed from `open_r`, not `peak_r`.** So the table stores
how close the position *currently* is to its ceiling, and **not** how close it
ever GOT — which is the quantity Check A needs. The checker derives peak/cap
itself. Not a defect (the field is correct for what it names), but a reader
looking for the peak-vs-ceiling ratio will not find it stored.

## Risks and Follow-Ups

- `PB-20260817-TELEMETRY-HAS-NO-TERMINAL-SNAPSHOT` — **open**. Until a terminal
  write exists, every live MFE is a lower bound truncated at the last exit pass,
  and finality is only inferrable via a join.
- **Check B remains ungraded and will stay so until the soak deepens.** The
  falsifier is written down: if `position_telemetry` accrues a month of rows and
  Check B still reports `live_no_final_rows` on every leg, the writer is not
  capturing closes and (1) is the cause — that is the `exit_price_source`
  failure shape (written in 12 files, branched on in one) repeating.
- **Deliberately NOT done: a CI guard over the parity RUN.** It would green on
  `harness_absent`/`live_no_final_rows` — a pass that checked nothing. CI guards
  the instrument (the self-test); the abstention states guard the conclusion.

## Deferred Items

- `PB-20260816-BYBIT-TP-CAP-BINDS-ON-ALPACA-AND-IB-LEGS` — Tier-3, open,
  untouched. Note Check A's spread is consistent with it: the Alpaca/IB legs
  carry caps of 7.1–14.3R while the Bybit legs sit at 2.2–3.9R.
- The two `unmeasured` reachability rows and three `queued_tier3` dispositions —
  the operator's, unchanged.

## Next Recommended Sprint

**M31 P3 readers**, not more P4. P4 is shipped and is now waiting on data rather
than on work, and §8's own ranking already puts counterfactual readers above
more sweeping — the M20 exit levers have fired **13 times ever** against 1,142
closed trades, so the live journal cannot grade them and waiting will not fix
that. The terminal-snapshot writer (Tier-2) is the one change that would move
P4 from waiting to gradeable.

## Wrap-Up Check

- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation reviewed and updated as part of the sprint.
- [x] No pipeline stage touched — `docs/TRADE-PIPELINE.md` not applicable.
- [x] Roadmap updated — M31 P4 status.
- [x] Contradictions recorded — five, including P2 shipping half its own design.
- [x] Remaining unknowns stated: Check B is ungraded at n=0 final rows, and the
      live `peak_r` lower bound is unquantified (it needs the terminal writer to
      close, not more soak).
