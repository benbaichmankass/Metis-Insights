# S-M31-TERMINAL-STAMP — finality becomes a stored fact

- **Sprint ID:** S-M31-TERMINAL-STAMP-2026-08-17
- **Milestone:** M31 (position telemetry) — **P5 precondition 1**
- **Dates:** 2026-08-17 → 2026-08-17
- **Tier:** **2** — it touches the live trader's close path (`Database.update_trade`).
  Operator-approved in-conversation 2026-08-17. No `config/strategies.yaml`, no
  order path, no VM mutation, no Tier-3 value moved.

## Objective

Close `PB-20260817-TELEMETRY-HAS-NO-TERMINAL-SNAPSHOT` — the single change the
prior sprint named as *"three unblocks"*: it closes the collapsed state at
source, narrows `peak_r`'s lower-bound gap, and makes future closes gradeable by
M31 P4 Check B without a join.

## Files and Systems Inspected

- `src/units/db/database.py` — `update_trade` in full, its existing close-path
  observers (`_fire_trade_closed_event`, `_record_trade_cost_estimate`), the
  `trades` DDL + its migration block, and the `position_telemetry` DDL
- `src/runtime/position_telemetry.py` — `enrich_record` / `read_records`
- `scripts/ci/check_collapsed_states.py` — the `CONTRACTS` schema and the
  producer/consumer scan, read before registering rather than pattern-matched
  from a neighbour
- `tests/test_m31_p3_telemetry_readers.py` (full)

## Work Completed

### The write — an observer, not a participant

`update_trade` stamps `terminal_state='final'` + `terminal_at` when a row
transitions to `closed`, inside **its own** `try/except` mirroring
`_record_trade_cost_estimate`. The separate guard is the point: sharing the cost
estimate's guard would mean either failure silently skips the other.

Two properties are the design, not details:

1. **Never overwrites an existing stamp.** A re-close — a reconciler flip, a
   flap — must not move `terminal_at` forward. The first observation of finality
   is the honest one, the same reasoning as the netting reconciler's
   *anchor at first observation, never "now"*. Enforced in SQL
   (`WHERE ... AND terminal_state IS NULL`), not in Python, so a concurrent
   writer cannot race past it.
2. **Stamps nothing when there is no row**, rather than inserting one. Telemetry
   exists only for legs whose monitor writes it (donchian/pullback); creating a
   row at close would fabricate an entire trajectory that was never measured —
   the fabrication class this milestone exists to stop.

`terminal_at` is deliberately **not** a copy of `trades.closed_at`: it records
when *we observed* finality, so the residual gap between the last telemetry write
and the actual close stays visible instead of being papered over.

### The read — `finality_source`, four states never collapsed

`enrich_record` now prefers the stored stamp over the join and publishes
**which evidence decided**, which is a different question from the verdict:

| state | meaning |
|---|---|
| `stamped` | the close path wrote it on the row itself |
| `derived_join` | only the `trades` join knows — the row **predates** the writer |
| `not_final` | still in flight |
| `unknown` | we could not look (no trade id, or a trade id `trades` lacks) |

**Collapsing `derived_join` into `stamped` is the dangerous direction** — it
would report the close hook as firing on rows where it never ran, hiding exactly
the regression the split exists to expose. So `summary.by_finality_source` ships
beside `final_rows`: a `final_rows` count that is entirely `derived_join` on
trades closed *after* deploy means the hook is not firing, which a bare count
hides.

### Registered with `collapsed-state-guard`, and mutation-tested

The backlog item's own `resolution_criteria` said registration *"is what makes it
enforced rather than merely present"*, so `position_telemetry.finality_source` is
now a declared contract. It is not taken on trust: collapsing `derived_join` into
`stamped` in the producer makes the guard fire —

```
position_telemetry.finality_source: producer src/runtime/position_telemetry.py
never emits ['derived_join'] on any line naming `finality_source`
```

— and restoring it returns clean. A guard that passes on a broken state is worse
than no guard, so the probe was shown to find a positive before its negative was
believed.

### A migration bug caught by its own tests, not in production

The migration was first placed in the **`trades`** migration block, ~200 lines
before `position_telemetry` is created. An empty `PRAGMA table_info` means *no
such table*, and the code fell through to `ALTER TABLE`, which raised on **every
fresh DB**. Two fixes, both kept: the call moved to immediately after the
telemetry DDL (where it can do its actual job — upgrading a *deployed* table),
and an early return on the empty-PRAGMA result.

## Validation Performed

- `tests/test_m31_p3_telemetry_readers.py` — **17 passed, 1 skipped** (the skip
  is `pytest.importorskip("fastapi")`, absent in this sandbox and present in CI).
- `scripts/ci/run_guards.py` — **PASS 32 · FAIL 0 · SKIP 11**, run post-commit
  so guard relevance was actually computed (guard selection is commit-range
  scoped; running it on an uncommitted tree scans nothing and warns).
- `check_collapsed_states.py --verbose` — 11 contracts clean, the new one at
  3 consumers with all four states read; plus the mutation test above.

Three test-suite defects were fixed rather than worked around, each of a kind
this repo has been bitten by:

- **The lifted `_TELEMETRY_DDL` is now asserted column-for-column against the
  real table.** Every other test in the file builds a bare sqlite DB from that
  constant, so a drift would make *all of them* pass against a table production
  does not have — precisely how the pairs `order_packages` bug survived its own
  suite (its tests declared two columns the real table never had). "Lifted
  verbatim" is now a checked claim, not a comment.
- **The pre-migration case got its own fixture**, DERIVED from the same constant
  by a documented strip, so the current and legacy schemas cannot drift into two
  independent hand-written definitions.
- **The close test goes through the public `update_trade`, not the private
  stamper.** Its docstring already claimed the close path; it was calling
  `_stamp_telemetry_terminal` directly. That is the mislabel class in miniature
  — and the substantive risk is not that the stamper works, it is that the hook
  never calls it, which only the public path can pin.

A new test covers the upgrade path itself: a pre-existing row survives the
migration and reads `terminal_state` **NULL**, distinct from `'final'`.

## Documentation Updated

- `CLAUDE.md` — the `/api/diag/position_telemetry` row (the stamp +
  `finality_source` + the `by_finality_source` reading discipline), and the
  `peak_r` lower-bound caveat corrected to say the stamp **narrows** it
- `ROADMAP.md` — M31, precondition 1 closed with the residual stated
- `docs/claude/performance-review-backlog.json` — `PB-20260817-TELEMETRY-HAS-NO-TERMINAL-SNAPSHOT`
  resolved, with an explicit `residual` field
- this log

## Contradictions or Drift Found

**1. The stamp does NOT eliminate the `peak_r` lower bound, and nothing here
should be read as claiming it does.** `peak_r` is still the value from the last
exit pass *before* the close, and a bar extreme cannot see an intrabar
excursion. The stamp records *when we observed finality*, not a re-measured
peak. `peak_r_is_lower_bound` therefore stays `true` on stamped rows too.
Quantifying the residual is separate work — and is exactly why the P5 proposal
deliberately does not put a `peak_r`-driven giveback lever first.

**2. P5's precondition 2 is untouched by this change.** The writer makes future
closes gradeable *without a join*; it does not create closed trades. The
fleet-wide final population is still **n=1** today and the harness per-trade
`mfe_r` is still committed nowhere (`PB-20260817-NO-COMMITTED-PER-TRADE-HARNESS-MFE`).
Precondition 2 is a data-accrual problem, and calling precondition 1 "the
unblock" would overstate what shipped.

## Risks and Follow-Ups

- **The hook's live firing is UNVERIFIED as of this log.** It is proven by unit
  test through `update_trade`; it has not yet been observed stamping a real
  close on the live trader. The check is cheap and specific: once a
  telemetry-writing leg closes post-deploy, `by_finality_source` must show a
  non-zero `stamped`. **If `final_rows` keeps rising while `stamped` stays 0,
  the hook is not firing** — that is the failure signal, and the split exists so
  it cannot hide.
- The 13 currently-open live rows will stamp as they close; the 1 already-closed
  row stays `derived_join` forever, correctly.

## Deferred Items

- `PB-20260817-NO-COMMITTED-PER-TRADE-HARNESS-MFE` (Tier-1) — needs a sweep
  re-run with `--emit-trades`; the other half of P4 Check B.
- `PB-20260816-BYBIT-TP-CAP-BINDS-ON-ALPACA-AND-IB-LEGS` — Tier-3, untouched.
- The two `unmeasured` reachability rows and three `queued_tier3` dispositions —
  the operator's, unchanged.

## Next Recommended Sprint

**P5 precondition 3** — the `rr_from_here` walk-forward. It is the only
remaining precondition that is *runnable work* rather than waiting: preconditions
2 (soak depth) and 5 (arm reachability) accrue on their own, and a walk-forward
that fails to clear the do-nothing arm would retire the P5 candidate outright,
which is worth knowing before the soak matures.

## Wrap-Up Check

- [x] Code inspected directly, not inferred from summaries.
- [x] Documentation reviewed and updated as part of the sprint.
- [x] Tier-2 change carries an explicit operator approval.
- [x] Roadmap updated — precondition 1 closed, P5 still withheld.
- [x] Contradictions recorded, including that this does **not** close the
      lower-bound gap and does **not** unblock P5.
- [x] Remaining unknowns stated: whether the hook fires on the live trader, and
      the size of the residual `peak_r` gap.
