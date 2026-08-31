# Sprint Log: S-SYSTEM-REVIEW-2026-08-31

## Date Range
- Start: 2026-08-30 (session `19b15dec`, branch `claude/full-system-review-v68vcm`)
- End: 2026-08-31

## Objective
- Primary goal: `/system-review` — run the three sub-reviews, assess promotion/soak/training health, find and fix bugs, drive the backlogs down, and produce the consolidated report.
- Secondary goals: none planned. **Two unplanned items displaced the mandate at operator direction** and became the bulk of the sprint: (a) replacing the hand-pasted real-money reconciliation with a live path, (b) fixing a safety-critical prop cushion double-count before arming `PROP_TICKET_RISK_GATE_MODE=enforce`.

## Tier
- **Mixed: Tier 1, Tier 2 and Tier 3**, each explicitly gated.
- Justification: Tier 1 — tests, guards, docs, read surfaces, the checklist tool. Tier 2 — `pull-and-deploy` ×2 (#10562, #10569), `pull-bybit-transaction-log` (#10565), the new hourly puller + diag route. **Tier 3 — `PROP_TICKET_RISK_GATE_MODE=enforce` (#10563)**, approved by the operator in conversation ("enforce the prop risk gate") and sequenced after the cushion fix on the operator's own follow-up decision ("Fix double-count, then enforce").

## Starting Context
- Active roadmap items: the standing `/system-review` mandate (13 `_REQUIRED_COVERAGE_KEYS` + three sub-reviews).
- Prior sprint reference: `docs/sprint-logs/S-PER-ACCOUNT-ARBITRATION-2026-08-31.md` and `S-LANE-P-SIGNAL-JOURNAL-AXIS-2026-08-30.md` (concurrent sessions).
- Known risks at start: `breakout_1` sitting close to its $4,700 static-DD floor; `comms/broker_truth_ledger.json` frozen; heavy concurrent-session traffic on `main`.

## Repo State Checked
- Branch/commits: branched from `bee07359`; shipped `0dabfca7` (#10532) and `e3c2ad71` (#10566). Branch restarted from `origin/main` after #10532 squash-merged, per the merged-PR rule.
- Deployment state: verified via `/api/diag/version` at each step. Pre-deploy the trader ran `a8077ee3` with `git_sha_on_disk bee07359` and `restart_pending true` — disk had moved, the process had not.
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `docs/claude/OPEN-ITEMS.json`, `docs/claude/system-actions.md`, `docs/api-tier-policy.md`.

## Files and Systems Inspected
- Code: `src/prop/prop_reconcile.py`, `src/prop/prop_risk_gate.py`, `src/runtime/bybit_wallet_truth.py`, `src/runtime/broker_truth.py`, `src/web/api/routers/diag.py`.
- Config: `comms/broker_truth_ledger.json`, `config/prop_rulesets/breakout.yaml`.
- Deployment: `scripts/ops/deploy_pull_restart.sh` (via run log), `scripts/ops/set_env.sh`, `scripts/ops/get_env.py`, `scripts/ops/diag_fetch.sh`, `scripts/ops/backlog_append.py`, `scripts/ops/render_session_brief.py`, `scripts/ops/system_review_checklist.py`.
- Docs: `CLAUDE.md`, `docs/api-tier-policy.md`, `docs/claude/system-actions.md`.
- Services/timers: `ict-trader-live.service`, `ict-web-api.service` (restarted by both deploys); the new hourly transaction-log timer.
- Workflows: `.github/workflows/system-actions.yml`, `pytest-run.yml`, `alpaca-settlement-soak-watch.yml`.

## Work Completed
- **Prop cushion double-count fixed (safety-critical).** `reconstruct_equity` selected fills on `reported_at` alone, so on a manual bridge a close the snapshot already held was applied twice. Replaced with an asymmetric rule: event time known → place exactly, both directions; event time unknown → apply a loss, **withhold a gain**. Only **4 of 19** pnl-carrying fills carry `closed_at`, so an event-time-only rule would have dropped 79% of the stream.
- **`PROP_TICKET_RISK_GATE_MODE=enforce` armed** on `breakout_1` after the fix, not before.
- **Live Bybit wallet-truth path** (~1,050 lines, 27 tests): hourly `/v5/account/transaction-log` pull + `GET /api/diag/bybit_wallet_truth`, walking the window in ≤7-day chunks. Replaces a hand-pasted CSV.
- **`pull-bybit-transaction-log` registered in all five required sites** (it had two). One gap was caught by `EXPECTED_ACTIONS`; the `ACTION_DAYS` forwarding gap had **no** guard and would have made the 60-day backfill pull 7 days and exit 0. Added a test deriving the expectation from the wrappers.
- **`fills_withheld_unplaceable_gain` forwarded to the panel** (#10566) — it was computed and dropped at the seam.
- **Two soaks given a read surface** (`conflict_taxonomy_soak`, `macro_thesis_soak`) via a test that derives every `SOAK_LOG_NAME` rather than enumerating names.
- **System-review checklist tool** (`scripts/ops/system_review_checklist.py`), item list derived from `_REQUIRED_COVERAGE_KEYS`; unknown state keys now reported instead of silently ignored.

## Validation Performed
- Tests: `test_prop_rule_distance_reconstruction.py` 11/11. Full CI green on `0dabfca7` and `e3c2ad71` (`pytest-run` ~14–15 min each, 13,744 collected). `run_guards.py` PASS 40 · FAIL 1 (only `layer-guard` exit 127, `lint-imports` absent in sandbox — established against a clean tree).
- Non-vacuity demonstrated, not assumed: the soak-coverage test was written to fail first and did, on exactly the two unreadable names; removing the panel forward fails the new test while the other 10 in the file still pass.
- Live verification: cushion `distance_to_dd_floor_usd` **122.62 → 87.34** against an **unchanged** snapshot (id 19, `reported_at 2026-08-30T19:33:29.584285Z`) with the **same** +35.28 fill present — identical inputs, different output. `enforce` confirmed on `/proc/<MainPID>/environ` (`process` = `declared`). `fills_withheld_unplaceable_gain` = **1** after the second deploy, same unchanged inputs. Transaction-log pull `days=60 chunks=9` ×3 accounts, 2,789 rows.
- **Gaps not yet verified:** (1) `enforce` has never CAPPED a ticket — armed ≠ exercised. (2) The live wallet-truth figure does **not** reconcile with the ledger and cannot yet — see Contradictions. (3) `journalTrust` still reads the frozen ledger, deliberately. (4) 12 of 37 mandate items untouched.

## Documentation Updated
- Rules doc updates: none required.
- Architecture doc updates: none required.
- Trade pipeline doc updates: **none — no pipeline stage changed.** The prop bridge is an observability/report-back path, and the cushion fix alters a safety *reading*, not the order path.
- Roadmap updates: none — this sprint opened no milestone.
- GitHub Actions doc updates: `docs/claude/system-actions.md` gained the `pull-bybit-transaction-log` row + allowlist entry.
- Subsystem doc updates: `CLAUDE.md` `log_file` allowlist row (now 44 names); `docs/api-tier-policy.md` for `/api/diag/bybit_wallet_truth` (coverage 99 → 100).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- **`SKILL.md` says "the TEN required keys"; `_REQUIRED_COVERAGE_KEYS` holds 13.** The doc teaching *field beats comment* was wrong about its own gate. Not fixed in this sprint (the skill is the subject of a scheduled operator-led review); the checklist tool reads the field instead.
- **The live wallet-truth path does not supersede the ledger, contrary to the natural reading of #10532.** Ledger −$262.52 covers 2026-04-15 → 2026-07-13; the live 60-day read (+$17.04) covers 2026-07-02 → 2026-08-31 — ~11 days of overlap, **zero** at the 30-day default. Worse, the ledger records that figure as *stitched across two sub-accounts* with **99.4% in SUB**, while the puller holds one credential pair per account id. Filed `BL-20260831-WALLET-TRUTH-CANNOT-REPRODUCE-THE-LEDGER-WINDOW-OR-ITS-SUB-ACCOUNT-STITCH`.
- **Drift I caused and fixed:** `OPEN-ITEMS.json` reformatted by a hand-rolled `json.dumps(indent=2)` against a file using `indent=1` (508-line diff for a 3-row edit); and `CLAUDE.md`'s rendered SESSION-BRIEF block left stale after editing the register (`session-brief-guard` caught it in CI, not locally, because I ran guards against the first commit and pushed a second).

## Risks and Follow-Ups
- Technical: `enforce` is live on an account that can be **permanently disabled**, and has never been exercised. Its first cap is the moment to watch.
- Tier-3 product decisions: switching `journalTrust` to the live wallet-truth path is now a real decision, blocked on the `bybit_2` UID read and either a reproducing pull or a recorded statement that the window is unreachable.
- Blockers: none.

## Deferred Items
- **The consolidated system report and its Telegram ping** — checklist items 36 and 37. Deferred by explicit operator scoping, not forgotten.
- **12 of 37 mandate items**, incl. `trade_decision_grades`, `proposed_tweaks`, `experiments_proposed`, `sprint_doc_review`, `new_work_compliance`, and the e35 disposition (23 legs).

## Next Recommended Sprint
- Suggested: the **operator-led review of the `/system-review` skill itself**, already scheduled. Retrospective + measured proposals delivered as `system-review-retrospective-2026-08-31.md`.
- Why next: 25/37 with a third of the budget displaced by unplanned work is a scoping problem, not an execution one. The mandate has grown monotonically (13 keys, none ever retired) and the backlog gate measured *looking* rather than *closing* (opened/closed 43/8, 231/94, 249/175, 536/326 — net positive every month, 1.64× filed vs closed).
- Required verification before starting: none.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage changed, so `docs/TRADE-PIPELINE.md` was correctly not touched.
- [x] Roadmap status was checked — no milestone row applies.
- [x] Contradictions were recorded, including the two I caused.
- [x] Remaining unknowns were stated clearly (see *Gaps not yet verified*).
