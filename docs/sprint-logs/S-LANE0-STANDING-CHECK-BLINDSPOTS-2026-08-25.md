# Sprint Log: S-LANE0-STANDING-CHECK-BLINDSPOTS-2026-08-25

## Date Range
- Start: 2026-08-25 ~09:30Z
- End: 2026-08-25

## Objective
- Primary goal: close out **Lane 0 — Live-capability integrity**
  (`docs/research/WORKPLAN-2026-08-14.md`, P0/in-progress) toward its
  done-condition — *every `mode: live` account × enabled leg is either
  demonstrably able to place an order, or carries a filed row saying why not,
  **and a standing check exists***.
- Secondary goals: re-measure the three queued rows (0.3 / 0.5 / 0.6) against
  live state rather than quoting an 11-day-old table; extend
  `scripts/ops/dead_leg_audit.py` if a leg class it cannot see turns up.

## Tier
- **Tier 1.**
- Justification: observability + tooling only. `scripts/ops/dead_leg_audit.py`
  is an offline report; `src/runtime/dead_leg.py` gained a **pure function**
  with no live-tick caller changed; `scripts/ops/get_env.py` gained one
  allowlist entry. No `config/`, no order path, no VM mutation, no service
  restart. The two Tier-2 items this surfaced are **proposed, not taken**.

## Starting Context
- Active roadmap items: WORKPLAN Lane 0 (P0); ROADMAP M36 (consolidate M25→M30
  before opening new frontiers).
- Prior sprint reference:
  [`S-DIAGTOKEN-GATE-AND-ALPACA-AFFORDABILITY-2026-08-25`](S-DIAGTOKEN-GATE-AND-ALPACA-AFFORDABILITY-2026-08-25.md).
- Known risks at start: a concurrent `/system-review` session
  (branch `claude/full-system-review-qhpxyh`, START 08:52:27Z) owns all three
  review backlogs.

## Repo State Checked
- Branch: `claude/metis-lane-0-closeout-8exavc`, branched fresh off `main`
  `3c5338e` (verified `git merge-base --is-ancestor origin/main HEAD`; **not**
  stacked on the merged `claude/m20-measurement-integrity-tpj5w3`).
- Deployment state: live trader ticking (`bybit_1`/`bybit_2`/`ib_paper` all
  journalled rows on 2026-08-25); `/api/health` 200 over the Caddy host.
- Canonical docs reviewed: `CLAUDE.md`, `WORKPLAN-2026-08-14.md` Lane 0,
  `docs/claude/coordination-board.md` (board protocol), sprint-log template.
- Coordination: board tail **proven** — `perPage=5, page=288` returned a short
  page of **3** (287×5+3 = 1438). `▶️ START` posted before the first
  substantive call.

## Files and Systems Inspected
- Code: `src/runtime/dead_leg.py`, `scripts/ops/dead_leg_audit.py`,
  `src/runtime/silent_refusal_alert.py`, `src/runtime/local_pnl.py`,
  `src/runtime/market_data.py`, `src/web/api/routers/dashboard.py`,
  `src/web/api/routers/candles.py`, `src/runtime/execution_diagnostics.py`,
  `scripts/ops/get_env.py`.
- Config: `config/accounts.yaml` (IB client ids), `config/strategies.yaml`
  (52 enabled), `config/instruments.yaml` (contract values).
- Live surfaces: `/api/bot/prop/status`, `/api/bot/positions`,
  `/api/bot/candles`, `/api/bot/db/table/{trades,signals}`, `/api/bot/db/tables`,
  `/api/diag/{ib_state,exchange_positions,broker_account_status,journalctl,audit_query}`.
- Tests: `tests/test_dead_leg_audit.py`, `tests/test_silent_refusal_alert.py`.

## Work Completed

### 1. The standing check could not see two whole leg classes — both fixed

**(a) It graded a deliberately-shelved account as its most alarming verdict.**
`src/runtime/dead_leg.py` exists precisely so the offline report and the live
alert cannot disagree about a row — its docstring says so. `bucket_for` grew a
second parameter on 2026-08-24 (the declared policy-skip bucket) and **only one
of its two callers was updated**: `silent_refusal_alert.py:216` passes
`entry_reason`; `dead_leg_audit.py:155` did not. So `policy_skipped` was
structurally unreachable in the report, and a `mode: dry_run` account graded
`signalled_never_placed` — the most alarming verdict it has — wearing an
`account_class: real_money` label. Measured live: **156 of `alpaca_live`'s 312
refusals** carry the `dry_run_sizing_skip` token. The exact drift the module was
built to prevent, in the module's own sibling.

**(b) A leg that STOPS RUNNING vanishes instead of being graded.** Legs are
built from `trades` rows, so a strategy that stopped produces no row, no leg and
no line — byte-identical to one that ran all week and found no setup. Now graded
from the `signals` dual-write on its own axis (`eval_state`), never folded into
the order verdict — a leg can be `evaluating` **and** `signalled_never_placed`,
which is the AVAX shape. Four states; **`unknown` sits on the refusing side**,
because `SIGNAL_DUAL_WRITE_DISABLED` is supported and reading its absence as *no
leg ever evaluated* would alarm on the whole fleet. A new
`strategies_not_evaluating` list is sourced from `signals`, so it can see a leg
with **zero** trade rows — the case the leg table structurally cannot reach.

⚠️ **The window is the discriminator, and the report says so.** Legs stop
evaluating whenever their **venue is shut**: all 19 US-equity legs stopped
within a **13-second band at 19:59Z = 15:59 ET**, the equity close. A detector
without that caveat fires on every equity leg every night — the
desensitized-alarm P1. This repo had already solved the same problem once, in
`exposure_soak`'s `venue_session_us_equity` stamp.

**(c) A regression the change itself would have introduced.** `entry_reason`
joining the `GROUP BY` makes one status span several rows, so the pre-existing
`by_status[status] = n` kept only the **last** reason's count. Changed to
accumulate; pinned by its own test.

### 2. `IB_MD_CLIENT_ID` gained a read surface
Added to `get_env.py::ALLOWED_KEYS` + a `system-actions.md` row. It governs
whether the web-api's IB market-data socket collides with the trader's own, and
its live value was unreadable — `BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE`
on a var that decides whether two processes fight over one socket.

### 3. All three queued Lane 0 rows re-measured — **none closed**
Full evidence in `WORKPLAN-2026-08-14.md` § "Lane 0 re-measured LIVE". Summary:

| # | verdict | the number that decides it |
|---|---|---|
| 0.3 | **OPEN — not presenting, not fixed** | newest `balance()`-None row is 2026-08-13; **66** clean attempts / 12 days, against a condition whose historical inter-arrival is *weeks*; **no fix exists in the code** |
| 0.5 | **OPEN — masked by a second defect** | fabrication path unchanged since 08-01; IB `connected`; candles return real bars for all 3 symbols in the same process; the fallback would produce ≈**$17,966.50** |
| 0.6 | **OPEN, cushion $64.00 — and staleness is only HALF of it** | 41.26 h stale; `distance_to_dd_floor_usd` **$64.00**; `distance_to_daily_loss_usd` **null** (`day_pnl_state: realized_unreported`). Plus the half I got wrong — see the correction below |

### 4. A correction I did NOT find myself, and a test race I did not write

**0.6 — I closed it on the wrong conclusion.** My first write-up ended
*"operator-owed input, not a code defect"*. The concurrent `/system-review`
session had reached the same ground ~10 minutes earlier and gone further (board
#6927 09:31:06Z; PR #10256 finding 1): **staleness is not the whole defect.**
The emitter sized ticket `prop-manual-1a29db54154e` at **`risk_usd: 75.00`**
against the **$64.00** cushion, because
`compute_rule_distance::distance_to_dd_floor_usd` has **three DISPLAY consumers
and zero DECISION consumers** — `breakout_ticket.py:133` computes the floor only
to print it at line 175. A perfectly fresh snapshot still emits an
account-killing ticket. Corrected in the workplan and credited; **not
re-derived**, which is what their heads-up asked for.

Worth naming the failure mode in myself: I had the same `/api/bot/prop/status`
payload in front of me, read `status_freshness: stale`, and stopped at the axis
the row named. The write-only-signal class is one this repo has a whole guard
family for, and I walked past an instance of it.

**A genuine test race, root-caused by them, verified and landed by me.**
`tests/test_diag_token_workflows.py` failed `pytest-run` on PR #10256 at
10:04:45Z on a sha that had **passed the identical job 21 minutes earlier**.
`subprocess.Popen` returns after `fork`, not `execve`, so `/proc/<pid>/environ`
does not yet carry `DIAG_READ_TOKEN` and `before_token_source` reads `envfile`
where the test expects `process`. The fixture writes the same token to both, so
**every other assertion still passes and only the source label flips** — a real
failure wearing a passing test's clothes, on exactly the distinction
`set-diag-token.yml` exists to make.

⚠️ **Not taken on trust.** Reproduced independently here before landing:
**21/60 = 35%** of first reads missed the token (they measured 12%; rate varies
with load, mechanism identical), readable 0.02–0.81 ms later. State the
population: that is the RAW race on the first read — the real test does enough
work in between that the window is usually covered, which is why it passes
locally and passed CI on the same sha an hour before. Fixed by waiting for the
state the test asserts about, with an `else` clause so a genuinely broken
fixture stays **loud** instead of degrading into a silent `envfile` reading.
Stressed 5×, plus the full 24-test file.

## Validation Performed
- Tests run: `tests/test_dead_leg_audit.py` **18 pass** (10 pre-existing + 8
  new); `tests/test_silent_refusal_alert.py` green.
- Guards: `scripts/ci/run_guards.py` → **35 PASS / 0 FAIL / 16 skip**, run
  **after committing** because the runner scopes relevance to a commit range and
  says so — an uncommitted run reported "all selected guards passed" while
  having scanned none of my three files. `collapsed-state-guard` and
  `test-schema-fidelity` both ran and passed.
- Manual code verification: read `_ib_connection_identity`,
  `_build_exchange_client_uncached`, `_resolve_position_pnl`,
  `_local_unrealised_for_trade`, `candles.py::_settings` directly rather than
  inferring from comments.
- Smoke test: the audit run against a synthetic journal reproducing all three
  shapes (venue-rejected / declared-skip / stopped-evaluating).

### Gaps not yet verified — stated, not papered over
- **The live value of `IB_MD_CLIENT_ID` on `ict-web-api` was never read.** The
  clientId-collision account of 0.5 is the **best-supported explanation**, not a
  measurement: it predicts exactly the observed asymmetry (candles work, mark
  fails), and nothing in the repo provisions the var — but the operator could
  have set it by hand. The allowlist entry added here is what makes it a
  one-call question next session.
- **Exchange truth for `ib_paper` is unreadable** (`positions: null`), so the
  `$119,490` vs `$37.80` contradiction in `BL-20260807` could not be re-measured
  today — only the *presentation* was.
- ~~The full pytest suite does not collect in this sandbox~~ — **now measured,
  not merely asserted.** The sandbox throws 103 collection errors
  (`pyo3_runtime.PanicException`, the pydantic/pyo3 binding). Rather than
  hand-wave it as environmental, both commits were run to completion in matched
  worktrees:

  | | failed | passed | errors |
  |---|--:|--:|--:|
  | baseline `3c5338e` (pre-change) | **1011** | 10367 | 103 |
  | this branch `cf7f83e` | **1011** | 10375 | 103 |

  **Identical failure and error counts; +8 passed — exactly the 8 tests added
  here.** So the change introduces zero failures, and the 1011 are the
  sandbox's, not mine. Independently corroborated by CI, where `pytest-collect`
  passes outright.
- **`--timeout=300` silently invalidated a run.** pytest rejected the unknown
  flag and the wrapper still exited **0**. Re-run without it. Recording it
  because it is the diagnostic sub-class C shape (an empty result read as a
  clean pass) landing on my own validation.

## Documentation Updated
- Rules doc updates: none needed.
- Architecture doc updates: none.
- Trade pipeline doc updates: none (no pipeline stage touched).
- Roadmap updates: 2026-08-25 row for this session.
- GitHub Actions doc updates: `docs/claude/system-actions.md` — `get-env`
  `ALLOWED_KEYS` row extended with `IB_MD_CLIENT_ID` + its rationale.
- Subsystem doc updates: `docs/research/WORKPLAN-2026-08-14.md` — Lane 0 state
  column corrected on 0.3/0.5/0.6 + the full re-measurement section.

## Contradictions or Drift Found
- **`dead_leg.py`'s single-vocabulary promise was already broken.** Its docstring
  says two copies are how the report and the alert start disagreeing; they had
  diverged anyway, because only one caller passed the new second argument.
  Fixed.
- **`candles.py`'s `IB_MD_CLIENT_ID` docstring describes a protection that only
  protects itself.** It reads as a process-wide reservation ("override via the
  `IB_MD_CLIENT_ID` env on the `ict-web-api` unit"); it is a literal inside one
  function, and nothing in the repo sets the env it names.
- **The Lane 0 table's 0.6 row said "25 days stale (last report 2026-07-20)".**
  Now 41.26 h against a 2026-08-23 snapshot — the row had gone stale in the
  *reassuring* direction on the age axis while the cushion narrowed to $64.
- **Not a contradiction, but a correction to my own first read:** the three
  Alpaca accounts stopping dead at 2026-08-21T15:05:56 looked like a capability
  failure. It is not — the legs evaluate normally (`spy_pullback_1h_eval`
  2026-08-24T19:59:43Z, `chop`, ADX 16.9, *"no setup — non-actionable"*). Absence
  of setups, not absence of capability. Chasing that produced the correct design
  constraint for the detector.

## Risks and Follow-Ups
- Remaining technical risks: the `balance()` → `None` burst class is unfixed and
  will recur; the uPnL fabrication path is intact behind a masking bug, so
  repairing the mask **re-exposes** the fabrication.
- **Remaining product decisions (Tier 2/3), proposed and NOT taken:**
  1. **Tier 2** — provision `IB_MD_CLIENT_ID=600` on the `ict-web-api` unit
     (env/unit change on the live VM). Sequencing matters: it must land
     **before** `candles.py`'s literal is removed, or candles break.
  2. **Tier 2** — the operator-owed prop status report for `breakout_1`.
- Blockers: none for this branch.

## Deferred Items
- **Three backlog rows are NOT filed** — the concurrent `/system-review`
  (`claude/full-system-review-qhpxyh`) posted START 08:52:27Z and no `✅ DONE`,
  so it still owns all three backlog files. Handed to it on the board instead of
  racing it. The candidate rows: the `dead_leg` caller divergence (now fixed —
  file as recurrence record), the `IB_MD_CLIENT_ID` unprovisioned reservation,
  and 0.5's masking bug.
- The `$119,490` vs exchange-truth contradiction itself — needs a readable
  `ib_paper` exchange position.

## Next Recommended Sprint
- Suggested next sprint: **finish Lane 0's `alpaca_live` done-condition case.**
- Why next: Lane 0's done-condition names `alpaca_live` as its case, and
  affordability is **already solved and is not the blocker**. The four real
  blockers are the 2026-08-23 audit's: `shorting_enabled: False` against 60.0%
  short flow (157/262 packages); **7 of 15 legs exceed 100% of account notional
  at every funding level** (sizing is scale-invariant, so money does not fix it)
  with no `max_gross_exposure_pct` declared; the paper record net-negative
  except `uso_trend_1h`; and **both silent-failure detectors skip the account**.
  That last one is now *partly* addressed — this change makes the offline audit
  grade it `refusing_by_declaration` instead of falsely alarming — but the live
  `silent_refusal_alert` skip is untouched.
- Required verification before starting: read `IB_MD_CLIENT_ID` via `get-env`
  (now possible) to settle 0.5; do **not** re-derive affordability — it is
  measured at 42 of 51 with the wall at `0.9 × equity` = $180.00, and must never
  be hand-computed (`_ROUND_UP_BUDGET_MULT = 1.5`).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
