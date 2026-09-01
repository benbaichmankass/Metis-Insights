# Sprint Log: S-STRAY-OCA-SOAK-WRITER-2026-09-01

## Date Range
- Start: 2026-09-01 (session `01HYXKHpDQeWv3u4rjWWoL2J`, Lane P continuation)
- End: 2026-09-01

## Objective
- Primary goal: give `IBClient._sweep_stray_oca_groups` a durable, probe-able read
  surface. It computed a complete decision plan and its ONE call site **discarded
  the return value**, leaving a `logger.warning` into journald as the only output —
  so `PROTECTION_STRAY_GROUP_MODE` had no read surface while `CLAUDE.md` told a
  Tier-2 reviewer to read soak rows before arming a path that CANCELS a live
  position's protective legs. Those rows did not exist.
- Secondary goals: three carried items from the prior handoff — collect the 3B LLM
  re-run for ANSWER QUALITY, do the venue-side Bybit read E35 half (b) asks for,
  and check `PROBES.json` after the next scheduled `probes` run.

## Tier
- Tier 1 for the writer by CONTENT; Tier 2 by FILE.
- Justification: the change is observe-only — it captures a return value that was
  already computed and appends a JSONL row; it alters no decision, no default, no
  allowlist polarity, and `PROTECTION_STRAY_GROUP_MODE` still ships at `annotate`
  with an empty allowlist meaning NONE. But it edits `src/units/accounts/ib_client.py`,
  which is on the IB order path, and CLAUDE.md's Tier-2 scope reads "runtime /
  deploy / order-path … changes". **That ambiguity was surfaced to the operator
  rather than resolved silently in my own favour.** Operator ruled: merge, then
  restart `ict-web-api`. The deploy (a live-VM service restart) is Tier 2 and was
  covered by the same approval.

## Starting Context
- Active roadmap items: Lane P of `docs/claude/WORKPLAN-2026-08-29.md`. This work
  is NOT on that plan — it came from the prior session's handoff and is tracked in
  `OPEN-ITEMS.json` + the backlogs, not the lane structure.
- Prior sprint reference: `docs/sprint-logs/S-LANE-P-SIGNAL-JOURNAL-AXIS-2026-08-30.md`;
  the handoff itself came from session `ayloee`.
- Known risks at start: the file is on the live IB order path, and a raise inside
  `place_protective` would cost a protective arm rather than an observation.

## Repo State Checked
- Branch or commit reviewed: branched from `origin/main` @ `3fc08ca4`; merged to
  `main` as `2f4600c5`.
- Deployment state reviewed: `/api/diag/version` before and after. Before the
  deploy the VM ran `3fc08ca4` against a disk at `b396eb01` (`restart_pending: true`).
- Canonical docs reviewed: `CLAUDE.md`, `docs/claude/OPEN-ITEMS.json`,
  `docs/claude/WORKPLAN-2026-08-29.md`, `src/runtime/tp_venue_cap.py`,
  `scripts/ops/run_probes.py`, `scripts/ops/probe_soak.py`.

## Files and Systems Inspected
- Code files inspected: `src/units/accounts/ib_client.py` (with `git log -p` first,
  per the handoff), `src/runtime/stray_oca_groups.py`, `src/web/api/routers/diag.py`,
  `scripts/ops/{run_probes,probe_soak,probe_lib,probe_actions_log,diag_fetch.sh}`,
  `scripts/deploy_pull_restart.sh`, `src/runtime/tp_venue_cap.py`.
- Config files inspected: live `/api/bot/config` (the ten e35 legs' `atr_stop_mult`
  and `tp_r`).
- Deployment files inspected: `.github/workflows/probes.yml` (permissions, cron).
- Docs inspected: `CLAUDE.md`, the two workplans, `docs/claude/system-actions.md`,
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.
- Services or timers inspected: `ict-web-api.service` (restarted), `ict-git-sync`.
- GitHub Actions workflows inspected: `probes.yml`, `due-list.yml`,
  `health-snapshot.yml` (as the scheduling control), the 16 cron-declaring workflows.

## Work Completed
- **Item 1 — the deliverable.** `src/runtime/stray_oca_soak.py`: persists the plan
  the sweep already returned and **re-decides nothing** (a second mode resolution is
  a second source of truth, free to drift from the one that governed the cancel).
  `decision` is three states, never collapsed — `stray_unkeyed` / `no_strays` /
  `could_not_look` — graded off the sweep's own `read_state`, never inferred from an
  empty `stray_groups`. The `no_strays` row is written deliberately as the
  DENOMINATOR. `cancel_calls_made` keeps that name so it cannot be read as an
  outcome. The apply-side read-back is ABSENT, not zeroed, when nothing acted. All
  five leg states ship with explicit zeros so `legs_by_state` sums to `legs_seen`
  checkably. `off` writes nothing. The diag allowlist entry shipped in the SAME
  commit (#8778), and the constant is named `SOAK_LOG_NAME` so the DERIVED guard
  covers it rather than an enumeration that cannot catch the next one.
- **Item 2 — merged and DEPLOYED, verified end to end.** Merged `2f4600c5`;
  `pull-and-deploy` (issue #10645) took the VM `b396eb01 → 2f4600c5` and restarted
  `ict-web-api`. `/api/diag/log_file?name=stray_oca_soak` moved from **HTTP 400**
  (05:55Z) to **HTTP 200 `present: false`** (06:29Z), with `protection_reassert_soak`
  read as a positive control on both occasions.
- **Item 3 — E35 half (b).** Did the venue-side read. Trade 5250 (`bybit_2`,
  real money, `trend_donchian_xrp_4h`): entry AND exit fills matched the journal's
  `broker_order_id` and `sl_order_id` **exactly by order id**, net −4.810873 vs the
  journal's −4.81892813 (delta $0.008). The exit filled on the bot's OWN stop leg,
  which is what rules out the operator-flatten exclusion by evidence rather than by
  `exit_reason`. `atr_stop_mult=2` is therefore verified end to end on real money.
- **Item 4 — the 3B LLM arm.** Collected (trainer-diag #10642). Both controls pass
  (`single-shot flag: -st`, `RESULT: benchmarked`). Better than the 1.5B on both
  defects it was failed for — no fabricated causal link, no misattribution to venue
  liquidity — but it never names the mechanism.
- **Item 5 — the probes machinery.** Dispatched `probes.yml` for the first time
  ever (run #32) and established that `probe_actions_log` reads **0 of 40** job
  logs, and that the cron has **never** fired.

## Validation Performed
- Tests run: 15 new (`tests/test_stray_oca_soak.py`), all passing; regression 63
  passing across the four IB protection suites; CI `pytest-run` green (15m20s, a
  real run, not the docs-only short-circuit), `guards`, `pytest-collect`,
  `repo-inventory` all green.
- Dry-runs or staging checks: `scripts/ci/run_guards.py --base-ref main` →
  `PASS 46 · FAIL 0` on the committed diff.
- Manual code verification: **five load-bearing properties mutation-verified, each
  caught** — collapsing `could_not_look` into `no_strays` (2 failures); dropping the
  denominator row (2); emitting `by_state` verbatim so zeros vanish (1);
  re-discarding the call-site return value (1, AST-based); removing the diag
  allowlist entry (1). Two further mutations on the doc/read-surface coverage also
  caught.
- Gaps not yet verified: **the writer has never produced a row.** `present: false`
  means the name is served and the file does not exist — a row appears only when an
  `ib_paper` MGC/MHG keyed protective re-arm next fires. The local sandbox cannot
  run the suite (no `fastapi`; 70 failures there are that import cascade and are not
  this change) — CI is the authoritative signal.

## Documentation Updated
- Rules doc updates: `CLAUDE.md` — the `log_file` allowlist enumeration, a
  `stray_oca_soak` description, and a correction to the `PROTECTION_STRAY_GROUP_MODE`
  row, which had promised since 2026-08-26 that a held-back row "can never read as an
  applied one" while no writer existed.
- Architecture doc updates: none required.
- Trade pipeline doc updates: none — no pipeline stage changed.
- Roadmap updates: `docs/claude/WORKPLAN-2026-08-29.md` — the P3 status row and its
  Recommended-order entry (see Contradictions).
- Subsystem doc updates: `docs/claude/OPEN-ITEMS.json` — probe attached to
  `OI-20260826-STRAY-OCA-...`; observations on the E35, LLM and probe-reader rows;
  one new monitoring row for the cron.
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **Contradiction 1 — the workplan's P3 row.** It read "`apply` is **NOT
  implemented** … `apply_implemented: false` on every row." Measured over the
  complete soak (44 rows): `apply_implemented: true` on 28, `applied: true` on 18.
  Stale in the GOOD direction, which is the dangerous kind here because Recommended
  order calls P3 the highest-value open item — a session would have rediscovered
  shipped work. Corrected, with the finding that matters attached: **all 18 applied
  rows are `accounts_planned: 1` / `starved_count: 0`, so contention has never been
  resolved**, while the starvation sits in 14 non-applied rows on BTCUSDT (11) and
  ETHUSDT (3) — outside the armed allowlist.
- **Contradiction 2 — `PROTECTION_STRAY_GROUP_MODE`'s soak promise.** Documented a
  soak row's fields for six days while the producer discarded its input. Corrected.
- **Code/doc mismatch:** `E35` half (b) was satisfiable on its literal text by a
  trade that cannot demonstrate the change — the venue clamp (`cap_r` 2.892634) sat
  below `tp_r=3`, so the identical bracket would have been placed at the old
  `tp_r=50`. Criterion sharpened on operator decision.

## Risks and Follow-Ups
- Remaining technical risks: the soak writer is deployed and **unexercised**; the
  probes machinery is broken in two independent ways (log read, and cron).
- Remaining product decisions (Tier 3): E35's tp_r half is unverifiable on 7 of 10
  legs, which still carry the `tp_r: 50` sentinel; the local-LLM verdict is recorded
  and left open at the operator's direction.
- Blockers: none.

## Deferred Items
- Deferred item 1: fixing `probe_actions_log`'s log download — filed, not fixed
  (operator chose the cron investigation instead).
- Deferred item 2: the workplan's #4 (B6 split) and #5 (B5) — untouched this session.

## Next Recommended Sprint
- Suggested next sprint: P3 contention evidence — get a CONTENDED symbol under an
  armed account, or obtain evidence from a contended row.
- Why next: it is the workplan's highest-value open item and today's measurement
  shows a full day of `apply` proved nothing about routing.
- Required verification before starting: read `arbitration_fanout_soak` for a row
  with `starved_count > 0` **and** `applied: true`; do NOT widen
  `ARBITRATION_FANOUT_ACCOUNTS` past `bybit_1` to manufacture one.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated — N/A, no pipeline stage changed.
- [x] Roadmap status was checked (the workplan; two stale entries corrected).
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.

## Process Corrections This Session
Recorded because they were mine, and both were caught by a check rather than by care.

1. **A CI failure I caused.** Editing `OPEN-ITEMS.json` made `CLAUDE.md`'s generated
   SESSION-BRIEF block stale, tripping `session-brief-guard` on the merge commit.
   Fixed by re-rendering, never by hand-editing the block. Cause confirmed from the
   job log (`PASS 46 · FAIL 1`, that guard alone) rather than assumed.
2. **A near-miss that would have been a confident wrong answer.** A per-leg sweep
   over the ten e35 legs returned **0 rows on all ten** and was one step from being
   reported as "no order packages since the deploy". The real answer is **8**: a raw
   `+00:00` in a query string decodes as a space, and the route answers 200 with an
   empty list. Caught only by running RULE ONE's positive control before trusting the
   quiet. Filed as
   `BL-20260901-ORDER-PACKAGES-SINCE-WITH-A-RAW-PLUS-OFFSET-RETURNS-ZERO-ROWS-SILENTLY`.
