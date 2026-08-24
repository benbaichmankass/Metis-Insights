# Sprint Log: S-SYSTEM-REVIEW-STRUCTURAL-2026-08-24

## Date Range

2026-08-24 (single session). Review window `since-last`: 2026-08-21T13:05Z → 2026-08-24T15:25Z.

## Objective

Run `/system-review`, then — on operator directive mid-session — extend it to find the
**structural** problems rather than only per-item defects: *"if we see that trades aren't
closing properly, or that there are bugs that are not really resolving themselves over time
because we're just putting on band-aids and we need a bigger structural fix, those are also
things you should be looking for and suggesting here."*

## Tier

Tier-1 (docs, tests, CI, review contract) plus two operator-approved changes obtained
in-session: **Tier-2** (`provenance.py` + `order_monitor.py`) and **Tier-3**
(`config/strategies.yaml` — `slv_trend_1h` `live` → `shadow`). No account mode flipped, no
risk cap or sizing rule touched.

## Starting Context

Previous review left `execution_capture` at 8.1% and reported it as a metric. The grading
log was stale on entry (newest grade predated `window_start`). `silent_refusal_alert` had
been latched `alerting: true` on `alpaca_live` since 2026-08-21T12:38Z.

## Repo State Checked

`main` at `318ab60` on entry; `69dc0d7` after concurrent sessions merged; `6f970b7` at close.
Live VM verified via `/api/diag/version` (`git_sha` == `git_sha_on_disk`, `restart_pending
false`) and `/api/diag/tick_cost` (`process_started_utc`) — not assumed from the merge.

## Files and Systems Inspected

- `src/runtime/{dead_leg,silent_refusal_alert,provenance,order_monitor}.py`
- `src/strategy_registry.py` (module-level `_cache` — why a config demote needs a restart)
- `scripts/reports/render_system_report.py`, `comms/schema/system_report_response.template.json`
- `.claude/skills/system-review/SKILL.md`
- Live journal: 1324 closed non-backtest trades via the Data Explorer (`filter_state ==
  "applied"` asserted before any count was trusted), `/api/bot/strategies`,
  `/api/diag/{services,exchange_positions,broker_account_status}`

## Work Completed

**Three point defects, root-caused and fixed** (PR #10223, merged `6f7c14f`):

1. `silent_refusal_alert` latched a `real_money`-labelled alarm for **3 days** on
   `alpaca_live`, which is `mode: dry_run` and refusing correctly. The detector claimed to
   watch for *"declared live … places NOTHING"* and never established "declared live". The
   repo had already ruled on this exact account (`EXPECTED_DISPATCH_SKIP_REASONS`, operator
   directive 2026-07-15); 162 of 186 rows carry the token it recognises. Fixed with a fourth
   `policy_skipped` bucket + `refusing_by_declaration`, predicate **imported** not re-derived,
   fail-**safe**, suppression per-**row**. Verified over 28 daily windows: 24 of 28 now grade
   correctly, the 2 pre-prefix windows still alert.
2. `recorded_exit_price` graded MEASURED while outnumbering all genuine broker truth combined
   (82 vs 79; all `local_compute`, zero `close_fees_usd`). Split at the writer — the root
   cause was `order_monitor` overwriting `exit_price_source` **unconditionally**.
3. `slv_trend_1h` demoted `live` → `shadow` (0 wins in 13, −$5,375, gate met twice).

**A ratchet defect the demote exposed** — `_unstamped_ceiling` is an ABSOLUTE count over a
population that can shrink, so removing 9 lever cells created 5 cells of slack with no diff a
reviewer reads as loosening. Found by that guard's own planted-omission tests.

**The structural pass** (operator-directed), measured over the whole history:

| finding | measurement |
|---|---|
| Exits performed by cleanup, not decisions | **857 of 1324 (64.7%)** |
| M20 levers | **17 closes ever (1.3%)** |
| DECIDED path provenance vs JANITOR | **27.0% vs 52.0%**; 41.8% of decided closes unstamped |
| `vwap_cross` (22.9% of real-money closes) | **67 of 102 (65.7%) unstamped** |
| Pairs sleeve | **79 of 79 unstamped** |
| Divergence machinery | 14 sweep passes, 23 env knobs, no single owner |
| Open backlog that is one class | **150 of 204 (73.5%)** |

**Hypothesis stated and REFUTED** — predicted the provenance gap was downstream of janitor
closes; the measurement said the opposite. That refutation is why rule 3 below exists.

**Made permanent** (PR #10232, merged `6f970b7`): `review_coverage.structural_health` —
population is the whole history, every finding carries a trend, one falsifiable hypothesis with
`refuted` as legal as `supported` (no `inconclusive`, or a review discharges the rule by not
deciding). Key + validator + 22 failure-path tests.

**A second drift found by asking what else builds the payload**: the schema template carried
**8 keys against the renderer's 11** — missing `since_last_build_verification`,
`backlog_classes`, `ml_output_actionability`, `unexercised_fixes`. That is why the renderer
rejected this session's payload four times. Synced, plus a bidirectional drift detector.

## Validation Performed

- 62 tests across the five suites touching the renderer or template; 39 on the silent-refusal
  fix (32 pre-existing unchanged).
- **Both new detectors verified to FAIL for the right reason**, not merely to pass: removing
  `backlog_classes` from the template reproduces the real drift and names that key.
- Guards: `claim-basis`, `impossibility-claims`, `canonical-doc-coherence`,
  `collapsed-state`, `provenance-consumer`, `diagnostic-provenance`, `dry-run`,
  `exit-coverage-matrix`, `strategy-coverage`, `roadmap-status-glyphs`. `layer-guard`
  verified locally (6 contracts kept, 0 broken) — its CI binary was absent from the sandbox.
- Live verification of the Tier-3 demote: trader restarted 16:58:50Z (`ticks_measured: 1`),
  `/api/bot/strategies` reads `execution='shadow'`. This mattered because
  `strategy_registry.load_strategies` caches in a module-level `_cache` — disk state alone
  would not have proved it.
- M39 parses via `roadmap._parse_milestones` as `planned` 📋; M20 stays `in_progress`.

## Documentation Updated

- `CLAUDE.md` — provenance coverage re-measured (494/1151 = 42.9%, with the 592/1151
  counterfactual measured rather than asserted); silent-refusal "three states" → four.
- `ROADMAP.md` — **M39 opened**; M20's status carries the finding about its own premise.
- `.claude/skills/system-review/SKILL.md`, `comms/schema/system_report_response.template.json`.
- `docs/claude/health-review-backlog.json` — 1 drained, 7 filed.
- Report `comms/reports/since-last/20260824T152500Z/`; board #6927; ping issue #10229.

## Contradictions or Drift Found

1. Schema template 4 keys behind the renderer (fixed + detector added).
2. `CLAUDE.md` provenance figures stale in both terms (re-measured).
3. `CLAUDE.md` silent-refusal state count stale (corrected).
4. Prior review's *"last cycle trained 0 manifests"* describes only the 05:40Z cycle — the
   01:08Z cycle trained **68**. Not a stall.
5. My own PR #10223 body claimed `config/` untouched after the Tier-3 demote landed in it —
   corrected before merge.

## Risks and Follow-Ups

- **M39(A) sequences before further live M20 lever flips.** A lever result on `vwap_cross` or
  the pairs legs cannot currently be falsified.
- **Exit execution (64.7% janitor) remains untouched** and is the largest number found. It
  will still be true after M39 lands; M39 makes it measurable, not smaller.
- `structural_health` forces the question, a number, a trend and a tested hypothesis. It
  **cannot force the answer to be a good one** — the `trend` field is the main defence, since
  a class that stays `flat` across reviews is self-indicting.
- `breakout_1` sits $64 above its $4,700 DD floor; operator accepted, no change made.

## Deferred Items

- `alpaca_live` go-live — operator chose to fund it, but shorting must be enabled broker-side
  first (128 of 186 routed orders = 68.8% short) and the notional bound wants an exposure-soak
  distribution. Operator-owned.
- "One divergence owner" (collapse 14 sweeps / 23 knobs) — considered and **not** selected;
  it rewrites the machinery closing 64.7% of trades.
- `reconciler_incomplete` / `entry_order_avg_price_unreliable` (91 rows) — looked like a
  root-level PnL defect, measured as 86 paper / 7 real with 2 rows carrying PnL. Not a P0.

## Next Recommended Sprint

**M39(A)** — stamp provenance on every decided-exit close, with the three-state discipline
`exit_anchor` uses (a monitor-derived level is not a fill). Success is measured on trades
opened after deploy only, never back-filled. Then **M39(B)**, pairs conformance with the
sleeve left running.

## Wrap-Up Check

- [x] Three PRs merged and verified on `main` (`6f7c14f`, `2574721`, `6f970b7`)
- [x] Tier-2 and Tier-3 changes each put to the operator before being written
- [x] Live VM verified for the config change (process restart, not just disk)
- [x] Report rendered `--strict`, ping delivered, board `✅ DONE` posted
- [x] Roadmap + sprint log + backlog all carry this session's decisions
