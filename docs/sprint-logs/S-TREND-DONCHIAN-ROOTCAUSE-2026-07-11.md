# Sprint Log: S-TREND-DONCHIAN-ROOTCAUSE

## Date Range
- Start: 2026-07-11
- End: 2026-07-11

## Objective
- Primary goal: Root-cause why `trend_donchian` — the research-BEST strategy —
  showed **0% win / −198R over 19 live trades** (`PERF-20260601-001`). Determine
  whether this is an execution/monitor/reconciler bug or a genuinely dead edge,
  and deliver either a Tier-3 fix proposal or a proven "the edge failed live."
- Secondary goals: Answer the specific sub-questions (regime vol-gate dropping
  signals? FLIP_POLICY=hold? min-lot over-risk? is it even filling?); guard
  against recurrence; land the decision durably (backlog + roadmap + sprint log).

## Tier
- Tier 1 (analysis + read-path analytics guard). The one code change
  (`exclude_reduce_leg_predicate` in the read-only `/strategy/attribution`
  analytics path) is Tier-1; it touches no order/strategy/risk/config path.
- Justification: Pure retrieval + analysis of live journal state, plus a
  read-side analytics filter. No live-trade behaviour changed. Opened as a
  **draft PR + ping** per the standard flow (touches analytics that feed
  decision reads).

## Starting Context
- Active roadmap items: `PERF-20260601-001` (the item under investigation, #1
  next-strategy triage, operator-directed 2026-07-11); ROADMAP "Next" item 2.
- Prior sprint reference: the 2026-06-01 investigation (in the item's
  evidence_log) which already partially root-caused this to regime + re-entry
  storm + orphan rate, but left it `in_progress` pending a trending-BTC window.
- Known risks at start: the strong prior was an execution/monitor/reconciler bug
  (the class that turned 1 MGC position into 18 phantom −$20k trades). Needed to
  rule that in or out with per-trade evidence.

## Repo State Checked
- Branch: `claude/trend-donchian-root-cause-slfw1t` (off `main`).
- Deployment state reviewed: live→trainer synced `trade_journal.db`
  (mtime 2026-07-11T00:02 UTC) — current, with the 2026-06-25 `reconcile_status`
  backfill applied.
- Canonical docs reviewed: `CLAUDE-RULES` (via CLAUDE.md), CLAUDE.md (execution
  gates, PnL-resolution contract, Position/BotStats shapes), ROADMAP "Next",
  performance-review-backlog (`PERF-20260601-001/002/009`, `PB-20260630-003`,
  `PB-20260618-001`, `PB-20260710-ETF-PULLBACK-ZEROWIN`).

## Files and Systems Inspected
- Code files: `src/units/strategies/trend_donchian.py` (entry/exit + TP-sentinel
  cap), `src/web/api/_clean_trades.py`, `src/web/api/routers/attribution.py`,
  `src/units/accounts/execute.py` + `order_monitor.py` (intent_reduce semantics),
  `src/units/db/database.py` (trades/order_packages/signals schema).
- Config files: `config/strategies.yaml::trend_donchian` (1h / donchian 20 /
  atr_stop 2.5 / trail 5.0 / tp_r 50 / min_confidence 0.60 / long_only).
- Data: `trade_journal.db` `trades` (85 trend_donchian BTCUSDT rows),
  `order_packages`, `signals` (audit dual-write) — via `trainer-vm-diag` relays
  #6136 / #6137 / #6138.
- Services/workflows: `trainer-vm-diag.yml` (relay used for the pulls).

## Work Completed
- **Pulled the ground truth** (trainer-vm-diag #6136/#6137/#6138): all 85
  `trend_donchian` BTCUSDT non-backtest rows with `reconcile_status` /
  `setup_type` / `exit_reason` / account; the `signals` reason taxonomy; the
  `order_packages` status/close_reason. (Direct diag was egress-blocked; the
  `journal?table=trades` relay only tails newest rows, so the trainer's synced DB
  + arbitrary-SQL relay was the right tool. Base64-encoded the Python to survive
  the relay's awk `cmd:` parser.)
- **Decomposed the −198R** exactly (matches the item's own −196.87/−1.56/4 split):
  4 genuine `reconciler_filled` fills — **−$196.87 DEMO** (bybit_1 1722/1731) +
  **−$1.56 REAL** (bybit_2 1721/1732) — all LONG breakouts stopped out in the
  late-May BTC $75–78k RANGE. The "19" count was padded with ~15 NULL-pnl
  `intent_reduce`/`reconciler_incomplete` bookkeeping legs → "0 of 19".
- **Classified every row** into (a) genuine loss, (b) reconciler/orphan artifact,
  (c) execution mismatch — full table in
  `docs/research/trend-donchian-live-anomaly-rootcause-2026-07-11.md`. (b) is
  minimal (zero `superseded` rows; 4-row `stuck_strategy_watchdog` tail); the
  amplifiers were (c): the TP-sentinel exchange-reject storm (negative/>10% TP →
  Bybit ErrCode 10001 → **no fills**) and the same-bar re-entry storm — **both
  already fixed** (`_TP_SENTINEL_CAP_PCT=0.099` + `long_only`; `#2548` bar-debounce).
- **Answered the sub-questions:** regime vol-gate is **not** dropping signals
  (all 6 `regime_hard_gate` rows `gated:false`/`regime_allow_explicit`/`cell:on`);
  `long_only` fires (`short_suppressed_long_only` ×351); FLIP_POLICY=hold not
  implicated (losses predate it, plain SL stop-outs); real-money fills are rare
  because `bybit_2` is undercapitalized for BTC (24 `sized_qty=0` refusals), not
  because of gating.
- **Forward verdict:** full window (05-25→07-10) is **net-positive on both
  accounts** (demo +$1191, real +$4.77) → **KEEP LIVE, no demote.**
- **Shipped the recurrence guard** (Tier-1): `exclude_reduce_leg_predicate` in
  `_clean_trades.py`, applied in `/strategy/attribution`, with tests in
  `tests/test_clean_trades.py` — a reduce leg can no longer pad a win-rate
  denominator or inject a phantom win/loss.
- **Landed the decision:** resolved `PERF-20260601-001`; logged the upstream
  reconciler phantom-pnl bug (`BL-20260711-INTENT-REDUCE-PHANTOM-PNL`) to the
  health-review backlog; updated ROADMAP.

## Validation Performed
- Tests run: `pytest` is absent in the sandbox, so verified the new predicate
  directly against in-memory sqlite — the reduce-leg rows (NULL-pnl + phantom
  +pnl + notes-flag variants) are dropped, the two real fills survive, and the
  `t.`-prefix parity holds. `exclude_reduce_leg_predicate` imports cleanly from
  `_clean_trades`.
- Manual code verification: read the full `trend_donchian.py` exit path
  (SL-cross → TP-cross → Chandelier ratchet) and confirmed the TP-sentinel cap
  matches the observed May rejection dates (the code comment names 2026-05-27 —
  the exact date of the reject storm). Confirmed `intent_reduce` is canonical
  bookkeeping (execute stamps it, `apply_intent_reduce_partial_close` leaves pnl
  NULL, order_monitor `is_reduce_leg`).
- Gaps not verified: the full pytest suite (sandbox dep gap — fastapi/pytest
  absent); CI will run `tests/test_clean_trades.py` on the PR. The attribution
  router edit's live behaviour is verified by construction (predicate + import)
  but not exercised end-to-end here.

## Documentation Updated
- Rules doc updates: none.
- Architecture doc updates: none.
- Trade pipeline doc updates: none (no pipeline stage changed).
- Roadmap updates: "Next" item 2 marked ✅ RESOLVED with the keep-live verdict;
  the "runs FIRST" preamble re-pointed to M20 Exit Refinement as the immediate
  next; a "Recent sessions" changelog line added.
- Subsystem doc updates: new research doc
  `docs/research/trend-donchian-live-anomaly-rootcause-2026-07-11.md`.
- Backlogs: `PERF-20260601-001` resolved; `BL-20260711-INTENT-REDUCE-PHANTOM-PNL`
  opened in the health-review backlog.

## Contradictions or Drift Found
- The headline framing ("0% win / −198R real-money edge failure") contradicted
  the actual journal: −$1.56 real money; the rest demo + count-padding. Resolved
  by decomposition, not left as drift.
- `intent_reduce` reduce legs can be booked with a phantom non-NULL pnl on
  `entry==exit`, contradicting the `apply_intent_reduce_partial_close` NULL-pnl
  design — recorded as `BL-20260711-INTENT-REDUCE-PHANTOM-PNL` (write-path fix)
  and masked in the read path by the new guard.

## Risks and Follow-Ups
- Remaining technical risks: the upstream reconciler phantom-pnl bug still books
  a fabricated pnl on demo reduce legs; a real-money reconciler-flip would inject
  a fabricated win/loss upstream of any single reader (masked, not fixed).
- Remaining product decisions (Tier-3): whether/how to fund `bybit_2` so
  `trend_donchian` can accrue a real-money record (`PB-20260630-001`).
- Blockers: none.

## Deferred Items
- The `PB-20260630-003` low-fill-rate investigation (`squeeze_breakout_4h` 1.2%,
  `htf_pullback_trend_2h` 9%) and `PB-20260630-ICTSCALP-DEGRADE` — decoupled from
  trend_donchian; stay open.
- The reconciler write-path fix (`BL-20260711-INTENT-REDUCE-PHANTOM-PNL`) — a
  focused reconciler-robustness sprint (ROADMAP item 4).

## Next Recommended Sprint
- Suggested next sprint: **M20 — Exit Refinement** (now the #1 next-strategy
  program, unblocked by this triage).
- Why next: the trend_donchian triage that was gating the queue is resolved and
  the research→live path is confirmed trustworthy.
- Required verification before starting: confirm the `exit_ladder_soak` +
  `fc_geometry_soak` have accrued enough rows (per the M20 gate).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated — N/A (no pipeline stage changed).
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
