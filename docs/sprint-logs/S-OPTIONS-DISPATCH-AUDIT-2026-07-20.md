# Sprint Log: S-OPTIONS-DISPATCH-AUDIT-2026-07-20

## Date Range
- Start: 2026-07-20 ~13:40 UTC
- End: 2026-07-20 ~14:40 UTC

## Objective
- Primary goal: check whether the Alpaca paper-portfolio account is running options strategies (operator ask), and fix a live dispatch-alert false-alarm the operator flagged mid-session.
- Secondary goals: assess whether any `alpaca_options_paper` options strategy is ready to graduate to real money; investigate a suspected `bybit_2`/`bybit_portfolio` "Paper" display bug the operator flagged; identify any `bybit_1` soak strategy ready to graduate to `bybit_2` real money.

## Tier
- Tier 2 (the `dry_run_sizing_skip` change touches the live dispatch-alert path — no order-path behavior change, but runtime/alerting) for PR #7127; Tier 1 (docs-only) for PR #7133.
- Justification: per CLAUDE-RULES-CANONICAL.md § Permission Tiers, a runtime/alerting change on the live trader is Tier 2 (operator OK required before merge — obtained in-chat); a pure backlog-doc addition is Tier 1 (no approval needed).

## Starting Context
- Active roadmap items: no specific roadmap milestone targeted; this was an ad-hoc operator investigation + a live-bug report.
- Prior sprint reference: `S-SYSREVIEW-PROP-ADX-2026-07-20` (same day, unrelated — system review + prop dedup + ADX tuning).
- Known risks at start: none flagged; `alpaca_options_paper` had not been checked for actual fill activity before this session.

## Repo State Checked
- Branch or commit reviewed: started on `main` @ `ceec068`; both PRs based on/merged into `main`.
- Deployment state reviewed: confirmed live via `/api/diag/version` — `git_sha: cea08712` (= PR #7133's squash commit) was already deployed and serving `/api/bot/config` correctly by 14:32 UTC, i.e. `ict-git-sync` picked up both merges within ~15-25 min of merge.
- Canonical docs reviewed: `CLAUDE.md` (root, bot repo), `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md` (this doc-freshness pass).

## Files and Systems Inspected
- Code files inspected: `src/core/coordinator.py` (multi_account_execute sizing/dry-run gate ordering), `src/runtime/execution_diagnostics.py` (EXPECTED_DISPATCH_SKIP_REASONS, enqueue_all_accounts_failed_dispatch), `src/analysis/paper_record_classifier.py` (_REFUSAL_MARKERS), `src/units/accounts/options_selector.py` (select_debit_vertical refusal reasons), `src/web/api/routers/trades_closed.py` (MAX_LIMIT=200 — cause of a 422 on an over-limit diag request, not a bug), `src/web/api/routers/bot_config.py` (confirmed `paper_role` is in the public-fields allowlist).
- Config files inspected: `config/accounts.yaml` (`alpaca_options_paper`, `alpaca_portfolio`, `alpaca_live`, `bybit_1`, `bybit_2`, `bybit_portfolio` blocks in full), `config/pairs.yaml` (`account_id: bybit_1` default + per-pair `execution` gates).
- Deployment files inspected: none directly (deployment verified via diag relay, not by reading `install_systemd_units.sh` etc.).
- Docs inspected: `ict-trading-bot/CLAUDE.md`, `ict-trader-dashboard/CLAUDE.md` (§ "Paper" means the live-portfolio mirror), `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`.
- Services or timers inspected: `ict-web-api.service` journal tail (via diag relay) — confirmed a clean restart cycle at 14:04 UTC picking up the PR #7127 deploy, no crash loops.
- GitHub Actions workflows inspected: `vm-diag-snapshot.yml` (had to fix my own malformed batched-request body twice — operator error, not a workflow bug), CI checks on both PRs (26 and 16 check runs respectively, all green before merge).

## Work Completed
- Answered the original ask: `alpaca_portfolio` (the Alpaca paper-portfolio mirror) runs **zero** options strategies — options expression is account-scoped (not strategy-scoped) and lives only on the separate `alpaca_options_paper` soak account. Recorded the operator's chosen policy (graduation-only — the portfolio carries only real-money-graduated options strategies) as `PRB-20260720-OPTIONS-PORTFOLIO-GRADUATION` in the performance-review backlog.
- Investigated `alpaca_options_paper` and found it has placed **zero fills** in its entire history (14+ attempts since 2026-07-07, 100% `options_expression:no_selection:*` refusals — `fewer_than_two_quotable_strikes` / `no_expiration_in_dte_band`, pointing at the account's `data_feed: indicative` config). Logged as `BL-20260720-OPTIONS-PAPER-ZERO-FILLS` (health-review backlog) with evidence + a concrete next step. This makes the graduation question moot today — there is no track record to grade.
- Root-caused and fixed a live operator-reported bug: `alpaca_live` (shelved `mode: dry_run` + deliberately defunded 2026-07-15) was alarming `🚨 1/3 accounts failed to dispatch: zero_balance` on every routed signal, because the per-account sizer runs BEFORE the risk gate's `account_mode_dry_run` rejection, so the 2026-07-15 alert-suppression fix (which only matched the risk-gate reason) never caught this path. Shipped PR #7127: tag a `sized_qty<=0` refusal on an effective-dry account with `dry_run_sizing_skip:`; `execution_diagnostics.EXPECTED_DISPATCH_SKIP_REASONS` now recognises the token (alert suppressed, journal untouched); `paper_record_classifier._REFUSAL_MARKERS` updated to match. Scoped strictly to effective-dry accounts (pinned by test — a live account with a genuinely empty wallet still alarms).
- Investigated the operator-reported "`bybit_2`/`bybit_portfolio` Paper view shows `bybit_1`" concern. Found NO bug: live `/api/bot/config` correctly serves `paper_role: "portfolio"` for `bybit_portfolio`/`alpaca_portfolio` and correctly omits it for `bybit_1`/`alpaca_paper`; both the Streamlit dashboard (`_portfolio_paper_ids`/`_row_is_portfolio_paper`) and Android app (`portfolioPaperIds`/`isPaperInScope`) already have correct client-side scoping code; the deploy was current (git_sha matched the just-merged PR). `bybit_portfolio` is trading correctly (closed an ETHUSDT `eth_pullback_2h` paper position ~3h before the check, correctly netting-guard-refused repeat entries while it was open). No code/config change made; reported the finding to the operator and asked which screen showed the mixed view (likely the Overview page's intentionally-unscoped system-wide positions snapshot, not the funding-segmented "Paper" toggle) — unresolved pending operator confirmation, not treated as a bug.
- Graded `bybit_1`'s soak-only strategies against `bybit_2`'s real-money roster for graduation readiness (operator ask). Pulled `/api/bot/performance?window=30d`'s `paper.perStrategy` block for the four never-tried-on-real-money candidates (`trend_donchian_ada_4h`, `trend_donchian_sol_4h`, `trend_donchian_avax_4h`, `sol_pullback_2h`). Verdict: none are ready — largest sample is n=7 (`ada_pullback_2h`, already-demoted, still negative — sanity-check only), the four fresh candidates range n=2 to n=6. `trend_donchian_ada_4h` (n=6, +$1875 PnL, +0.45R expectancy) is the only one trending positive and worth continued soak-watching; `sol_pullback_2h` and `trend_donchian_avax_4h` are both at 0% win rate on n=4 (concerning early signal, not yet a kill decision). No Tier-3 PR proposed — sample sizes are far below this repo's own promotion bar (the M15 WS-C k-fold gate). No backlog entry opened; this is exactly what `/performance-review` already tracks and reported to the operator directly instead.

## Validation Performed
- Tests run: `pytest tests/test_multi_account_execute_per_account_mode.py tests/test_all_accounts_failed_ping.py -q` → 43 passed. `pytest tests/test_coordinator_rejection_journal.py tests/test_s043_order_refusal_paths.py tests/test_execute_journal_rejections.py tests/test_intent_delta_dispatch.py tests/test_account_state_gate.py -q` → 102 passed. `pytest tests/test_paper_record_classifier.py tests/test_strategy_execution_gate.py -q` → 19 passed. `tests/test_multi_strategy_intents.py` could not run in this sandbox (`ModuleNotFoundError: No module named 'pandas'` — sandbox dependency gap, not a code issue; CI ran the full suite and passed).
- Dry-runs or staging checks: none — this repo has no staging environment; verification was via the live diag relay (read-only) post-deploy.
- Manual code verification: read the full `multi_account_execute` sizing/dry-run-gate code path (coordinator.py lines ~780-1700) line-by-line to confirm `effective_dry` is resolved before the `sized_qty<=0` branch, so tagging at that point is correct and doesn't require restructuring the gate order. Read `options_selector.py::select_debit_vertical` in full to confirm the two refusal reasons' exact trigger conditions. Read the dashboard's `_portfolio_paper_ids`/`_row_is_portfolio_paper` and the Android app's `portfolioPaperIds`/`isPaperInScope` + confirmed `PositionsScreen.kt` actually wires `portfolioPaperIds()` into its filter call (not just declared-but-unused).
- Gaps not yet verified: which exact dashboard/Android screen produced the operator's "mixed Paper view" screenshot — not confirmed, since the operator moved on before answering; if it resurfaces, check whether it was the Overview page's unscoped system-wide positions snapshot vs a funding-segmented view. `alpaca_options_paper`'s root cause (feed-quality vs selector/config bug) was NOT investigated past the refusal-reason level — left as the explicit next step in `BL-20260720-OPTIONS-PAPER-ZERO-FILLS`.

## Documentation Updated
- Rules doc updates: none needed (see Contradictions section — none found).
- Architecture doc updates: none needed.
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): not touched — this session's fix is an alert-classification change, not a pipeline-stage change (no order-routing, no signal-generation, no execution-path behavior changed; only which refusals raise an operator Telegram alert).
- Roadmap updates: none added as a milestone row — this was an ad-hoc bug-fix + investigation session, not a milestone-moving sprint. Logged as a sprint log only (see Decision-landing note below).
- GitHub Actions doc updates: none.
- Subsystem doc updates: `docs/claude/performance-review-backlog.json` (+`PRB-20260720-OPTIONS-PORTFOLIO-GRADUATION`), `docs/claude/health-review-backlog.json` (+`BL-20260720-OPTIONS-PAPER-ZERO-FILLS`).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- None. Ran `python scripts/ci/check_canonical_doc_coherence.py` — all 4 checks pass (dead VM IP single-source, removed gates not described as live, no 7-stage ML ladder, instruction-hierarchy mirror). Manually checked `docs/CLAUDE-RULES-CANONICAL.md` and `docs/ARCHITECTURE-CANONICAL.md` for any existing description of the dispatch-failure alert / `zero_balance` semantics that this session's fix would contradict — found none; the alert-classification logic lives entirely in code (`execution_diagnostics.py`) and is not duplicated in either canonical doc, so no doc update was required by the fix itself.

## Risks and Follow-Ups
- Remaining technical risks: `alpaca_options_paper` cannot accrue any graduation evidence until `BL-20260720-OPTIONS-PAPER-ZERO-FILLS` is root-caused and fixed — flagged as blocking `PRB-20260720-OPTIONS-PORTFOLIO-GRADUATION`.
- Remaining product decisions (Tier 3): none proposed this session (both the options-graduation and bybit-graduation questions concluded "not ready, more data needed" — no Tier-3 change to bring to the operator yet).
- Blockers: none.

## Deferred Items
- Root-cause the `alpaca_options_paper` indicative-feed quote coverage (pull a live SLV/GDX chain to check `mid` population) — deferred to a health-review/ml-review session per the backlog item's `next_step`.
- Confirm which screen produced the operator's "mixed Paper view" screenshot, if the operator revisits it.
- Continue watching `trend_donchian_ada_4h` (positive early signal, n=6) and `sol_pullback_2h`/`trend_donchian_avax_4h` (negative early signal, n=4 each, 0% win rate) via the normal `/performance-review` cadence — no standalone backlog item opened since this is within that skill's existing scope.

## Next Recommended Sprint
- Suggested next sprint: a focused options-data-quality investigation (pull a live SLV/GDX chain via `AlpacaOptionsData` under the `indicative` feed and inspect raw `mid` coverage) to resolve `BL-20260720-OPTIONS-PAPER-ZERO-FILLS`.
- Why next: it's the single blocker on the operator's stated options-graduation policy — without it, `alpaca_options_paper` will keep accruing zero evidence indefinitely regardless of how long the soak runs.
- Required verification before starting: confirm `ALPACA_API_KEY_ID_OPTIONS`/`ALPACA_API_SECRET_KEY_OPTIONS` are usable for a direct read (via the diag relay or a scratch script) before assuming a code-path root cause.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. — N/A, no pipeline stage was touched (alert-classification only).
- [x] Roadmap status was checked.
- [x] Contradictions were recorded. — none found.
- [x] Remaining unknowns were stated clearly. — see Gaps not yet verified + Deferred Items.
