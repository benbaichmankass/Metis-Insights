# Sprint Log: S-PER-ACCOUNT-ARBITRATION-2026-08-31

## Date Range
- Start: 2026-08-31
- End: 2026-08-31

## Objective
- Primary goal: find out why `trend_donchian_sol` makes no trades anywhere, and build the per-account arbitration fan-out.
- Secondary goals: replace name-based trade prioritisation with evidence-based prioritisation; arm the fan-out on `bybit_1`; leave the unproven half logged so a review session picks it up.

## Tier
- Tier 3 (order-routing semantics + a live env flip), operator-approved in-conversation 2026-08-31.
- Justification: `_election_sort_key` decides which competing trade routes; `account_scope` narrows dispatch; `ARBITRATION_FANOUT_*` arms a live order path.

## Starting Context
- Active roadmap items: Lane P (signal→journal axis), M18 allocator (adjacent, parked).
- Prior sprint reference: `S-LANE-P-SIGNAL-JOURNAL-AXIS-2026-08-30.md` (shipped the soak's starved/no_winner split).
- Known risks at start: a live arbitration conflict cannot be fabricated on the VM, so tests are the strongest pre-production evidence available.

## Repo State Checked
- Branch/commit reviewed: `claude/fanout-starved-state-grading-wy9tk2` → merged `12e4a9e`; deploy verified at live sha `12e4a9eb`.
- Deployment state reviewed: `/api/diag/version`, `/api/diag/status`, `get-env` on both fan-out keys.
- Canonical docs reviewed: `CLAUDE.md`, `OPEN-ITEMS.json`, `ROADMAP.md`.

## Files and Systems Inspected
- Code: `src/runtime/intents.py`, `arbitration_fanout.py`, `arbitration_fanout_soak.py`, `intent_multiplexer.py`, `pipeline.py`, `conviction_arbitration.py`, `src/core/coordinator.py`, `scripts/ops/get_env.py`.
- Config/docs: `CLAUDE.md`, `ROADMAP.md`, `docs/claude/OPEN-ITEMS.json`, the three review backlogs.
- Services: `ict-trader-live.service` (restarted 07:47Z, PID 2810047).
- Workflows: `system-actions` (`set-env`, `get-env`, `pull-and-deploy`), `vm-diag-snapshot`, `pytest-run`, `guards`.

## Work Completed

**THREE defects, not one.** `trend_donchian_sol` emitted 120 buy signals since 2026-08-01 and wrote zero journal rows.

1. **Arbitration was global; it is an account-level decision.** `aggregate_intents` elected ONE winner per symbol before any account was consulted, so an account holding its own candidate produced no order package — invisible to the journal AND to every per-account detector. Fixed by splitting `gate_intents` (side effects, once) from `elect_from_gated` (pure) and electing per account, dispatched through a narrowing-only `account_scope` applied LAST in `_eligible_for_dispatch`. Measured from declared config: **13 of 23 live symbols** have ≥2 live accounts competing for one global slot. The starved account is `bybit_1` — 26 strategies, class paper, no `paper_role`: the **soak book**, so the cost was the ML training feed, not PnL.

2. **A name that is a prefix of a competitor's could never win.** The same-side key was `max()` over `tuple(-ord(c) for c in name)`; per-character negation reverses the alphabet but **cannot reverse LENGTH**, so a prefix tuple compares smaller and `max()` returned the LONGER name. Every SOLUSDT contender is priority 0 with the inert `0.0` qty sentinel, so the name was the entire decision — deterministically, every tick. **This is why the fan-out alone would not have fixed it:** the leg loses to `trend_donchian_sol_prop` ACROSS accounts and to `trend_donchian_sol_4h` INSIDE `bybit_1`.

3. **The election now ranks by evidence:** target size (reinforcement only) → **confidence** → declared priority → **recent 3-day PnL** → timestamp → name. This is the graduation of `conviction_arbitration`, which has computed this winner as an observe-only annotation since 2026-06-17 and names its own graduation *"a future deliberate change to `aggregate_intents` itself"*. New `src/runtime/election_track_record.py` supplies the PnL tier (TTL-cached, one read-only query, three never-collapsed states, `+inf` for ungraded — never `0.0`, which would rank "no record" above every losing strategy). Every election now carries **`decided_by`**, computed against the RUNNER-UP, so "ranked by confidence" is falsifiable from the audit log.

**Arming.** Both env keys written BEFORE the merge with `service: none` (inert — the deployed code had no reader), so arming and code entered the same process. That ordering is load-bearing: the tiebreak fix landing WITHOUT the allowlist INVERTS the starvation onto `breakout_1`.

## Validation Performed
- Tests run: **69 new** across 6 files (19 apply-path, 9 seam multiplexer→meta→pipeline→dispatch, 9 observability, 16 evidence-ranking, 10 tiebreak parametrized over 5 pairs both ways, 6 gate/election split); `tests/runtime/` 291 passed; CI `pytest-run` 13,769 passed.
- Guard fleet: **PASS 46 · FAIL 0**, re-run AFTER committing (the runner warns it is commit-range scoped and scans nothing uncommitted — the first run had scanned zero of my files).
- Live verification: `/api/diag/version` `git_sha 12e4a9eb == git_sha_on_disk`, `restart_pending false`; `/api/diag/status` uptime 254s, heartbeat 38s; `get-env` process `'apply'` + process `'bybit_1'` from `/proc/<MainPID>/environ`.
- Non-vacuity proof: the new inertness test was checked by SABOTAGING the annotator to mutate `intent.side` and confirming it FAILS; source restored byte-identical (`git status --porcelain src/` empty).
- **Gaps not yet verified:** the fan-out has never ELECTED or ROUTED anything. Zero soak rows since the restart.

## Documentation Updated
- Rules doc updates: `CLAUDE.md` — corrected the `arbitration_fanout_soak` row's now-false *"Live routing is nonetheless UNCHANGED today … the allowlist ships EMPTY"*; flagged the OI id as half-stale.
- Roadmap updates: `ROADMAP.md` → Items Under Consideration — trade prioritisation, marked as already-live-and-unproven with the harness gap as the blocking dependency.
- Register: `OPEN-ITEMS.json` — cleared one row, corrected one, added one (below).

## Contradictions or Drift Found
- **The soak lied about its own capability.** `apply_implemented` was hardcoded `false` and `mode` hardcoded `"annotate"`; once the apply path landed, a reviewer reading the LOG would have concluded the fan-out was never built. The field was present and confidently wrong, which is worse than a missing one. Fixed to record the EFFECT (`applied`, `rounds_applied`, `plan_state`, `apply_scope`).
- **`CLAUDE.md` went stale in the dangerous direction** the moment the allowlist was armed — corrected in this sprint rather than left.
- **Two CI failures were correct.** `test_conviction_arbitration.py` pinned the PRE-graduation contract ("priority still wins"), conflating *the soak is inert* (a property of module A, still true) with *priority outranks confidence* (a property of module B, deliberately reversed). Rewritten to prove inertness DIFFERENTIALLY — soak stubbed out vs live, identical decision — which is invariant to future ranking changes.
- **`pull-and-deploy` reported success while skipping its own post-deploy version assertion** (`DIAG_READ_TOKEN` unset). Re-observed onto the existing open row `BL-20260813-DEPLOY-SKIPS-ITS-OWN-POST-DEPLOY-VERSION-ASSERTION` (0.73 overlap) rather than filed as a duplicate — PR #10558.

## Risks and Follow-Ups
- **Remaining technical risks:** the fan-out is armed and unexercised; a rollback that disarms the allowlist while leaving the merged tiebreak in place re-opens the `breakout_1` inversion.
- **Remaining product decisions (Tier 3):** widening `ARBITRATION_FANOUT_ACCOUNTS` beyond `bybit_1` — gate on end-to-end `trades`-row evidence, not on a soak decision row.
- **Blockers:** `scripts/backtest_system.py` models ONE shared book, so BOTH open A/Bs (global-vs-per-account, and the ranking key) are unrunnable until it grows an N-book mode.

## Deferred Items
- The N-book backtest harness + ranking-key arm (the blocking dependency above).
- `BL-20260831-CONFIDENCE-SATURATES-AT-ONE-SO-HALF-OF-ARBITRATIONS-CANNOT-BE-DECIDED-ON-IT` — `1.0` is 20.5% of observations; contenders tie exactly on 50.1% of contests.
- `BL-20260831-RESEARCH-QUEUE-CANNOT-EXPRESS-A-BLOCKED-JOB` — the README documents `blocked`, the validator demands `run.workflow`/`lands.store` regardless of status.
- Opposite-side collapse under hedge mode — untouched, flagged.

## Evidence preserved from a cleared OPEN-ITEMS row
`OI-20260830-FANOUT-SOAK-SPLIT-SHIPPED-NOT-YET-READ-LIVE` was CLEARED this sprint, both criteria observed on the live endpoint:
- **(a)** rows read back from `/api/diag/log_file?name=arbitration_fanout_soak` carry `fanout_schema: 2` — the deployed trader was running the split, not merely the PR merged.
- **(b)** across the **3** valid schema-2 rows (2026-08-30T21:35Z→23:34Z) `starved_count = 1` and `no_winner_count = 2` — **both non-zero**, so the two populations are separable on LIVE data rather than on a replay. The starved row is the lane's own thesis: ETHUSDT, `bybit_1`, 23:34:17Z.
- ⚠️ **n = 3.** Small. And the pooling trap is real and was hit in-session: over all 16 rows the same sum reads `starved = 18`, an **18×** overstatement, because the 13 pre-split rows conflate the populations. Check `fanout_schema` before pooling.

## Next Recommended Sprint
- Suggested next: build the N-book / ranking-key arm on `scripts/backtest_system.py` + its firing workflow.
- Why next: it is the single blocking dependency for both outstanding A/Bs, and the ranking key it would test is ALREADY live on the order path.
- Required verification before starting: read `OI-20260831-PER-ACCOUNT-ARBITRATION-SHIPPED-NOT-YET-ARMED-OR-EXERCISED` and re-read the soak — if the fan-out is still unexercised, that observation is itself an input to the harness design.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [ ] Trade pipeline doc / dashboard Trade Process tab — **not applicable**: no pipeline STAGE changed; the change is which candidate wins an existing stage, and the dashboard cannot be rendered from a sandbox.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly — the fan-out is armed, deployed, and UNEXERCISED.
