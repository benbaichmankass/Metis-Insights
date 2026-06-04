# S-RINA-REVIEW — health + strategy review, health-snapshot revival, session follow-ups

## Date Range
- **Start:** 2026-06-04
- **End:** 2026-06-04

## Objective
- **Primary:** Run the autonomous `/health-review` + `/performance-review` ("Rina" reviews) over the live ICT trading bot and act on what surfaced.
- **Secondary (operator-directed mid-session):** Fix the two operator-facing items the reviews surfaced — the dead health-snapshot writer and the docs-commit-restart gate — then knock off the remaining backlog follow-ups (#3 + #4) before wrapping.

## Tier
- **Mixed 1/2/3.** Tier-1 (reviews, doc fixes, insights/relay code, observability); Tier-2 (`ict-health-snapshot` + `ict-web-api-watchdog` VM timers); Tier-3 (`config/accounts.yaml` comment). All Tier-2/3 went via draft PRs; the health-snapshot Tier-2 fix (#2728) was merged on explicit operator approval, the rest (#2732) left for operator review.

## Starting Context
- Last `/health-review` ~2026-06-01T17:10Z; last `/performance-review` 2026-06-01T19:30Z. Window covered the Meantime Expansion (Gold/Copper paper sleeve, per-strategy symbol scope, Tradovate phase-1) + the M14 ML-optimization sprints.
- Live roster: bybit_2 real money (trend_donchian, ict_scalp_5m, fvg_range_15m, htf_pullback_trend_2h); bybit_1 demo (full set); ib_paper (MES/MGC/MHG); prop + ib_live + tradovate dry.

## Repo State Checked
- Branch `claude/rina-health-strategy-review-9zqy1`; main advanced bead54f (merge of #2728) during the session.
- Diag state pulled via the `vm-diag-request` relay (direct egress firewalled at Trusted network). Snapshot/journal/insights_history/services pulls; trainer service via `trainer-vm-diag`.

## Files and Systems Inspected
- **Live runtime:** heartbeat/status/order_packages/trades/audit (diag relay), trainer service health, M13 insights_history.
- **Code/config read:** `config/{accounts,strategies}.yaml`, `src/runtime/health.py`, `src/runtime/insights/{template_analyst,data_sources}.py`, `src/web/api/routers/health_snapshots.py`, `scripts/{install_systemd_units,check_ib_gateway}.py`, `.github/workflows/vm-diag-snapshot.yml`, `deploy/dropins/data-dir.conf`, `tests/test_s012_service_consolidation.py`.

## Work Completed
1. **/health-review — caution (0 concern / 3 watch).** Trader healthy; real-money `ict_scalp_5m` SHORT dispatch (trade 2428, bybit_2, is_demo=0) journaled + order-package-linked clean; all 11 enabled strategies eval'ing; state-consistency exact across 6 accounts. Thursday `config/` compliance rotation logged BL-20260604-001 (Tradovate checklist). Fixed BL-20260602-002 (sync-vm-secrets note in system-actions.md); closed BL-20260529-002 (already shipped #2595). Response JSON + ping delivered.
2. **/performance-review — caution (quiet window).** Live book flat (last close trade 2064, ~06-01 12:09Z). 19 decisions graded → 1 **A** (ict_scalp_5m short 2428: textbook sweep+displacement+FVG, HTF, ADX 29.7) + 18 **D** (vwap shadow churn, confidence pinned 1.0). M13 cross-check found the false health `concern` (BL-20260529-005) + the recent-card header/table contradiction (BL-20260529-006). Scores appended to `comms/claude_strategy_scores.jsonl`; perf-backlog evidence added (PERF-20260601-001 progress: trend_donchian shorts now closing positive).
3. **fix(health) BL-20260529-005 (#2728, MERGED + VERIFIED LIVE).** Revived the JSON health-snapshot writer the 2026-05-12 refactor deleted: `scripts/write_health_snapshot.py` + `ict-health-snapshot.{service,timer}` (15-min oneshot, auto-enabled) + `install_systemd_units.sh` data-dir drop-in wiring (so writer + ict-web-api resolve the same `artifacts_dir()` — the 2026-05-12 path-split trap) + `template_analyst.health_template` grades on the checks themselves (concern only on critical; warn/stale→watch; healthy→good). **Verified live:** M13 health card flipped from 24-day-stale `concern (0/11)` → fresh `watch (6/7)`, snapshot age 10s.
4. **fix(insights) BL-20260529-006 (#2732).** Recent-card header now matches its table (rows are already the closed set; don't re-drop on the absent `status` key).
5. **ci(diag-relay) BL-20260604-002 (#2732).** Relay now reaches an allowlisted set of read-only `/api/bot/*` endpoints (perf-review's documented pulls), default stays `/api/diag/`.
6. **feat(ops) BL-20260604-003 (#2732).** `ict-web-api-watchdog` — VM-side self-heal for the read surface (decision logic byte-identical to the proven IB-gateway watchdog).
7. **docs(accounts) BL-20260604-001 (#2732, Tier-3).** Tradovate hookup checklist rewritten to the autonomy contract.

## Validation Performed
- **Tests (local):** `test_write_health_snapshot.py` (9), `test_check_web_api.py` (9), `test_s012_service_consolidation.py` (unit-inventory guard, both new services registered), `test_insights_template_analyst.py` (11, incl. the recent-card regression). Ruff clean. YAML parses (accounts.yaml, vm-diag-snapshot.yml). Relay allowlist routing verified by isolated bash harness.
- **CI:** #2728 went green (after fixing the S-012 guard the new service tripped) and merged (squash bead54f). #2732 guards + ruff green; pytest finishing at wrap.
- **Live verify:** BL-20260529-005 confirmed resolved via insights_history relay (#2733) — fresh snapshot, grade flipped.

### Gaps not yet verified
- **#2732 not merged** (Tier-2/3 → operator review). The web-api watchdog RESTART path proves out only on a real/induced wedge post-deploy (healthy-path will show in its journal).
- **BL-20260604-004** (new): the revived card's one failing check, `accounts_api`, is a standalone-context artifact (`account_balance()` returns None outside the trader process) — card sits at `watch` not `good` until that check reads the connection-free balance snapshot.
- A ~6-min GitHub MCP outage mid-`/health-review` blocked 3 diag pulls (services / 04:39Z restart-cause / IB-watchdog journal) — carried forward, not fabricated.

## Documentation Updated
- `docs/claude/system-actions.md` (sync-vm-secrets note), `docs/claude/deployment-ops.md` (2 new units), `config/accounts.yaml` (Tradovate checklist), `docs/claude/health-review-backlog.json` (drain + BL-20260604-001/002/003/004), `comms/claude_strategy_scores.jsonl`, `docs/claude/pending-pings.jsonl`.

## Contradictions or Drift Found
- Found + fixed: the M13 health card served a 2026-05-11-frozen snapshot (writer deleted 2026-05-12) and the recent-card header diverged from its own table. Both root-caused + fixed.
- Residual (logged, not fixed): `CLAUDE.md` "Important Notes" watchdog list does not yet mention `ict-health-snapshot.timer` or `ict-web-api-watchdog.timer` — minor doc-freshness incompleteness (not a contradiction); add in a follow-up.

## Risks and Follow-Ups
- **Operator action:** review/merge #2732 (the Tier-3 `config/accounts.yaml` comment is the only piece strictly needing sign-off); then the watchdog timer auto-enables on deploy.
- Open backlog of note: BL-20260604-004 (accounts_api standalone check), BL-20260529-002 secondary (web-api had no self-heal → addressed by #4c), PERF-20260601-006/010 (regime-router phase-3 + vwap degenerate confidence — the vwap D-churn target).

## Deferred Items
- `CLAUDE.md` watchdog-list touch for the two new timers.
- BL-20260604-004 fix (point check_accounts_api at the connection-free balance snapshot).

## Next Recommended Sprint
- After #2732 merges + deploys: verify the web-api watchdog healthy-path in its journal; fix BL-20260604-004 so the health card goes fully green; then a `/performance-review` once the live book is active enough to grade realized PnL.

## Wrap-Up Check
- [x] Code inspected directly (file:line; agent-free, hand-written).
- [x] Canonical docs reviewed + updated; residual doc-freshness gap recorded (CLAUDE.md watchdog list).
- [x] TRADE-PIPELINE: unchanged (reviews + observability/insights + ops watchdogs only; no order-path edit).
- [x] ROADMAP: no milestone change (review + maintenance session).
- [x] Contradictions recorded (above).
- [x] Unknowns stated (Gaps not yet verified).
- [x] Production-active: BL-20260529-005 fix merged + deployed + verified live; #2732 follow-ups pending operator merge.
