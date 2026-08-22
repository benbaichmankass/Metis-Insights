# S-EXIT-PROTECTION-CLUSTER-2026-08-22

## Date Range
2026-08-22 (single session, `s2-exitclust`), continuing the 2026-08-21 review programme
from `S-WORKPLAN-CONTINUATION-2026-08-22` (main at `968bbea8`).

## Objective
Work the operator's 11:1xZ direction — **bugs and technical blockers before research** —
starting with the CRITICAL exit/protection cluster, then T.3 (`slv_trend_1h`, investigate
and keep live) and T.4 (ETH 15m, packet then report, promote nothing).

## Tier
Tier 1 throughout. No Tier-3 gate touched, no `execution:` change, no account mode, no
model promoted, no live-VM mutation. The one shipped change is a **read-only** diag route.

## Starting Context
`docs/claude/WORKPLAN-2026-08-21.md` is the queue; both operator-decision blocks (07:0xZ
and 11:1xZ) read, the 11:1xZ block current. Phase 0, item 1.0, T.2 and the trainer disk
closed by the previous session. Board tail **proved** at `perPage=5, page=256` → short page
of 4; sole session, slot free.

## Repo State Checked
`main` at `968bbea8`; the live web-api serves `968bbea8` (`/api/diag/version`), so reads in
this log are against deployed code. ⚠️ The clone ships **shallow at 52 commits** —
`git fetch --unshallow` (→ 3,551) was required before any `git log -S` result was
trustworthy, and that mattered: see § Validation.

## Files and Systems Inspected
- `src/runtime/order_monitor.py` — `_bybit_position_protection`, `_check_broker_naked_bybit_positions`, the stuck-cascade sweep (`:4414`)
- `src/units/accounts/alpaca_client.py` — `protection_state`, `has_protective_orders`
- `src/units/accounts/clients.py`, `src/web/api/routers/diag.py`
- `scripts/ops/broker_bracket_reconcile.py` + `.github/workflows/broker-bracket-reconcile.yml`
- `config/strategies.yaml`, `config/accounts.yaml`
- Live: `/api/diag/exchange_positions`, `/api/bot/ml/registry`, `/api/bot/performance`, `/api/bot/order-packages`, `/api/bot/db/table/trades`

## Work Completed
**1. `BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND` criterion 5 — answered.**
All three venues are price-blind. Bybit's Full-mode branch returns `covered_qty == size` on
any `pos["stopLoss"]` *string* that is non-empty and not `"0"`; Alpaca's `protection_state`
returns booleans, not even a quantity. **The row's own instance is `ib_paper`; `bybit_2` is
mainnet** — 9 live Bybit positions across three accounts, 2 on real money.
Criterion 3 (a scheduled caller) had already landed and the row was stale on it.

**2. Built the missing read surface** — `GET /api/diag/bybit_open_orders` +
`clients.account_bybit_open_orders` (**PR #10142**). The axis was not merely unread on
Bybit, it was **unreadable**: the detector that closed the IB half consumes
`/api/diag/ib_open_orders` and no Bybit sibling existed. Carries **both** the Full-mode
position-level stop and the Partial-mode legs, because Full mode has no resting order and an
orders-only surface would grade a protected position **naked** — the inverse error, and the
worse one, since it would drive a re-arm.

**3. T.4 — premise REFUTED, promoted nothing.** `eth-regime-15m-lgbm-fc-pcv-v1` is
`deployment_bucket: OFFLINE`. Its `stage_history`: `candidate → shadow` 2026-07-04, then
**`shadow → candidate` 2026-07-19** — *"operator-approved 2026-07-19: powered RG4 NO_EDGE
(0.476)"*. **14 days at shadow, ended by an operator-approved demotion**; the plan's "54 days
at shadow" is ~51 days since **registration**. No gate packet is owed on a shadow→advisory
question that is not live for a candidate-stage head. Row:
`MB-20260822-ETH-15M-HEAD-IS-AT-CANDIDATE-NOT-SHADOW`.

**4. T.3 — investigated, `execution:` untouched.** The 0-for-13 reproduces. But **every
account carrying `slv_trend_1h` is paper or dry-run**, so T.3's own real-money condition
fails independently of the operator's keep-live instruction. The `min_confidence: 0.3` floor
landed 2026-08-01 (`0093d2ba`) and **works** — zero violations after, all three violations
before. And **6 of 6** of its closes are `exchange_flat_reconciled`: the leg has no
decision-driven exit, making it an instance of the cluster rather than a strategy verdict.

**5. New finding — the stuck-cascade sweep carries 45.5% of package closes**, and it is
**concentrated**: pairs 42/61 (`pairs_bnb_btc_a` 13/13) against `trend_donchian_*` **0/14**.
That concentration rules out the tick-race explanation and points at the isolated pairs order
path. Rows `BL-20260822-PAIRS-PACKAGES-CLOSED-BY-THE-STUCK-CASCADE-SWEEP` and
`BL-20260822-PACKAGE-CLOSE-REASON-IS-NOT-THE-EXIT-RECORD`.

**6. Item 1.1 re-measured at the TRADE level.** `trades.exit_reason`, newest 500 closed
(2026-07-15..08-22 of 1,292; `filter_state` asserted `applied`): `reconciler_filled` 41.2%,
corroborating the plan's 40% on an independent population. **Main order path, pairs excluded
(n=323): decision-driven 15.2%, `tp_cross` 3 rows = 0.9%, `sl_cross`+`sl` 9.6% — a stop:target
ratio of 10.3 : 1.**

## Validation Performed
- **Falsified the new tests against three planted defects, each with the edit `assert`ed
  applied**: naive price coercion → 3 price tests fail; single order filter → the filter test
  fails; cross-check dropped → the cross-check test fails; restored → 15/15 pass.
- ⚠️ My **first** run of the second break reported the *first* break's failure signature, from
  a stale file in my own scripting. Caught and re-run. Reporting a falsification that did not
  run is the same class as everything else on this row.
- Guards **36 PASS / 0 FAIL / 14 skip** on a **committed** tree. `layer-guard`'s `exit 127`
  was a missing local `lint-imports`; installed and run → 6 contracts kept, 0 broken.
- CI caught one thing the local guard run could not, because it is a **test, not a guard**:
  `docs/api-tier-policy.md` carries a machine-checked coverage line and I added the 98th route
  while it still claimed 97 of 97. Restated by **deriving** the number from the code.
- Backlog arithmetic asserted at every write: health 788 → 791, ml 103 → 104, performance
  105 → 105, **no duplicate ids**.

## Documentation Updated
`CLAUDE.md` (diag table), `docs/api-tier-policy.md` (route row + coverage line),
`docs/claude/WORKPLAN-2026-08-21.md` (T.3, T.4, item 1.1, session log), three backlog files,
this log.

## Contradictions or Drift Found
- `BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND` is cited in **six** places across `src/`, `tests/`
  and `CLAUDE.md` and **had never been filed**. Every citation read as tracked while tracked by
  nobody. It surfaced only because `artifact-validity-guard` is diff-scoped and my change added
  a seventh reference — **so the count is a LOWER BOUND on the class**: a pre-existing dangling
  id is invisible to a diff-scoped guard by construction. Filed retrospectively as `resolved`
  (the code fix shipped 2026-07-13); the original incident PR number is not recoverable and
  **none was invented**.
- The workplan's "T.4 — 54 days at shadow" was days-since-registration wearing a
  days-at-shadow label. Corrected in place.

## Risks and Follow-Ups
- **Criterion 4 is NOT done and was deliberately not attempted**: whether
  `protection_coverage` / `_bybit_position_protection` should themselves carry the price axis
  so the live sweep can *act*. A price-divergent stop grading uncovered would trigger a
  **re-arm** — an order-path action on a live position. Tier-2/3, needs an operator decision
  and a tolerance chosen on a measured distribution.
- **The Alpaca read surface has the same gap and was not built.**
- The pairs package-cascade finding is a **hypothesis with strong circumstantial evidence**
  (concentration), not a traced code path. The row says so.

## Deferred Items
The M7 packet `PB-20260821-...` asks for was **not** run and is not claimed as run. Research
items 2.1–2.4 stay behind the cluster, per the operator's ordering.

## Next Recommended Sprint
Trace `pairs_executor`'s package-close path (Tier-1 read) and establish whether the
strategy-monocle gate governs the isolated pairs path — if it does, the sleeve is being
unblocked by a repair sweep every tick, which is a much larger finding than the bookkeeping.
Then item 1.1 proper, recomputed from `trades.exit_reason` with the pairs split stated.

## Wrap-Up Check
Board `▶️ START` posted before the first change; `✅ DONE` at close. Scheduled Sunday
2026-08-23 22:30Z session (`trig_014S3NAzMKy2Ac2AM2GgyRE5`) for the MES attach + MGC flatten
was **not** duplicated or pre-empted — it had not fired at session end.
