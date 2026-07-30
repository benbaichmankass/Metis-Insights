# Sprint Log: S-FULL-SYSTEM-REVIEW-807M08-20260730

## Date Range
- Start: 2026-07-30 (session `807m08`, branch `claude/full-system-review-807m08`)
- End: 2026-07-30

## Objective
- Primary goal: Run a full `/system-review`, then deep-dive the money bleed the
  operator flagged ("trades get near TP then snap back to SL"), build an
  MFE-vs-realized capture metric, and harden the review skills so the failure
  can't recur unmeasured.
- Secondary goals: crypto research→results-gap diagnosis; the crypto short-only
  fine-tunes; correct dishonest R-vs-dollars reporting.

## Tier
- Mixed. Tier-1 (review report, research docs, `side_filter` capability,
  `/system-review` hardening, backlog/sprint docs). Tier-3 (crypto short-only on
  real-money `trend_donchian_xrp_4h` + `sol_pullback_2h` shadow→live) — all
  **operator-approved in chat** before merge.

## Starting Context
- Active roadmap: exit-refinement (M20) + the crypto book performance problem.
- Prior sprint reference: the 2026-07-28 `/system-review` (RPT-20260728-091200).
- Known risks at start: the crypto book red at every scale; unclear why research
  wasn't translating to live results.

## Repo State Checked
- Branch/commit: `claude/full-system-review-807m08` off `origin/main`.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md` (tiers,
  "if you see something"), the three review skills + `system-review` SKILL.

## Files and Systems Inspected
- Code: `src/runtime/strategy_signal_builders.py`, `scripts/backtest_trend.py` /
  `backtest_pullback.py`, `scripts/reports/render_system_report.py`.
- Config: `config/strategies.yaml`, `config/accounts.yaml` (routing truth).
- Docs/schema: `.claude/skills/system-review/SKILL.md`,
  `comms/schema/system_report_response.template.json`, the two review backlogs.
- Live/trainer state: via the `vm-diag-request` + `trainer-vm-diag-request` relays.

## Work Completed
- **PR #7940 (merged `39f4826`):** the `/system-review` since-last report +
  four research memos (exit-capture deep-dive, research→results gap, crypto edge
  audit, crypto fine-tune proposals) + 16 graded trades + the naked-position
  backlog drain.
- **PR #7966 (merged `97aae2f`):** the Tier-1 `side_filter: long|short|both`
  capability (trend + pullback builders + both backtest harnesses; default
  `both` = no-op, generalizes `long_only`) + the Tier-3 crypto short-only
  fine-tunes — `side_filter: short` on `trend_donchian_xrp_4h` (**real-money
  bybit_2**; corrected a stale "demo-only" config comment — field beats comment)
  and `sol_pullback_2h` (re-promoted shadow→live as short-only, paper).
- **PR #7973 (merged `e3c09e3`):** `/system-review` hardening — a **mandatory,
  measured `execution_capture`** review-coverage block (round-trippers% /
  giveback / hold-vs-expected per live strategy), **anti-normalization aging**
  (an anomaly open ≥2 reviews forces a `flags_raised[]` + operator-priority
  escalation), the **dollars-are-the-scoreboard** honesty rule (real-money P&L
  reconciled to exchange-fills + broker-truth, never journal-R), and a
  `render_system_report.py --strict` mechanical backstop.
- Deployed context: `BYBIT_TPSL_MODE=partial` re-asserted 05:37Z (see the
  correction below — it was NOT an activation).

## Validation Performed
- Tests: `tests/test_side_filter.py` (14 assertions) + `test_trend_donchian_long_only.py`
  regression pass; harness `side_filter` verified on synthetic data; renderer
  `--strict` verified to fail on missing key / un-escalated ≥2-review anomaly and
  pass when escalated. All three PRs merged green (19–22 checks each).
- Live: dispatched the exit-fix re-check over the diag relays.
- Gaps not verified: the scalp exit leak's TRUE root cause (see below).

## Documentation Updated
- Roadmap: n/a this session (review + research + tooling).
- Subsystem docs: `.claude/skills/system-review/SKILL.md` +
  `comms/schema/system_report_response.template.json` (execution-capture guard);
  `docs/research/exit-capture-deepdive-2026-07-30.md` (added the TPSL-premise
  correction banner — see below).
- Backlogs: `PB-20260730-SCALP-CAPTURE-STANDING-WATCH` (perf),
  `BL-20260730-TRAINER-JOURNAL-PULL-STALE` (health).

## Contradictions or Drift Found
- **Load-bearing correction — my own premise was disproven mid-session by a
  concurrent 2026-07-30 investigation session.** The exit-capture deep-dive (in
  the merged PR #7940) attributes the scalp exit leak to `BYBIT_TPSL_MODE=full`
  and treats `partial` as "the fix, deployed today (05:37Z)." **That is wrong:**
  `partial` was ALREADY live since ~07-21 (verified via `.env` / unit env /
  `/proc/<PID>/environ` + 75/211 pre-flip opens carrying `sl_order_id`, a
  partial-only column). The 05:37Z change was a no-op re-assertion; the scalps
  hold hours + close `reconciler_filled` UNDER partial mode, so the root cause is
  still open. The concurrent session filed `BL-20260730-EXITCAPTURE-DEEPDIVE-WRONG-TPSL-PREMISE`
  and built the real Bybit partial-leg/naked-bracket fix (PR #8000,
  `BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT`). I reconciled: added a top-of-doc
  correction banner to the deep-dive, and re-filed my exit follow-up as a
  standing capture-metric WATCH (not a "verify the flip") item.
- Stale config comment: `trend_donchian_xrp_4h` said "demo-only soak" but
  `accounts.yaml` routes it to real-money `bybit_2` — corrected in PR #7966.

## Risks and Follow-Ups
- The MFE/giveback capture METRIC and the `execution_capture` review mandate
  stand; only the TPSL-mode root-cause attribution was wrong. The standing watch
  (`PB-20260730-SCALP-CAPTURE-STANDING-WATCH`) tracks whether scalp capture
  actually improves as the #8000-family fixes soak.
- Tier-3 already-approved changes are live; verify next `/system-review` that the
  short-only gate suppresses longs on both legs (audit rows).

## Deferred Items
- Exit-leak true root cause: owned by the concurrent session's #8000-family +
  its filed items; my standing-watch item cross-references them.
- Trainer db-pull freshness: `BL-20260730-TRAINER-JOURNAL-PULL-STALE`.

## Next Recommended Sprint
- Suggested next: after the #8000-family soaks ~24–48h, a `/system-review` whose
  `execution_capture` block confirms bybit scalp hold-times/roundtrippers
  improved vs the pre-fix baseline (avg hold 9.05h / 47-of-59 reconciler_filled).
- Why next: closes the open exit-leak loop with the new standing metric.
- Required verification before starting: n>=20 post-fix closed bybit scalps.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [ ] No pipeline stage touched (review/tooling/config only) — `TRADE-PIPELINE.md` n/a.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded (the TPSL-premise correction, front and center).
- [x] Remaining unknowns were stated clearly (exit-leak true root cause is open).
