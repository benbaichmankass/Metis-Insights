# Sprint Log: S-PROP-RISK-ACCOUNT-ID-2026-06-17

## Date Range
- Start: 2026-06-17
- End: 2026-06-17

## Objective
- Primary goal: Wire `account_id` through `PropRiskManager.__init__` so prop accounts get the journal-based daily-risk-state self-healing rebuild (the deferred Tier-3 fix split out of PR #3865, BL-20260617-PROP-RISK-ACCOUNT-ID), and rework the 4 in-memory prop-breach tests onto the journal-sourced path.
- Secondary goals: Add a regression guard proving the cap engages from journal PnL and survives a restart; keep the full prop/risk/accounts/coordinator test set green.

## Tier
- Tier 3
- Justification: Changes risk-state resolution for a live account class (prop). Prop accounts execute via Telegram ticket, not a broker API, so there is no money-at-risk urgency — but the change is gated on operator approval before merge. Shipped as a DRAFT PR.

## Starting Context
- Active roadmap items: prop-accounts architecture (scalable, integrated into the standard strategy flow).
- Prior sprint reference: PR #3865 (repo-compliance audit) — this fix was deliberately split out of it.
- Known risks at start: passing `account_id` switches on the journal-sourced `daily_pnl` rebuild, which breaks tests that seed `daily_pnl` in memory.

## Repo State Checked
- Branch or commit reviewed: `claude/prop-risk-account-id-gn4zgk` off the PR #3865 merge commit.
- Deployment state reviewed: n/a (no VM mutation; code + tests only).
- Canonical docs reviewed: CLAUDE.md (Prime Directive, two execution gates, prop-accounts section), risk.py module docstring (self-healing `daily_risk_state` rebuild contract).

## Files and Systems Inspected
- Code files inspected: `src/units/accounts/prop_risk.py`, `src/units/accounts/risk.py` (RiskManager `__init__` + `_load_daily_state` / `_recompute_daily_pnl_from_db` / `_refresh_daily_from_sources`), `src/units/accounts/__init__.py` (loader: regular accounts already pass `account_id=name`; prop passed `account_name` but dropped it at `super()`).
- Config files inspected: none changed.
- Docs inspected: SPRINT-LOG-TEMPLATE-CANONICAL.md, health-review-backlog.json.
- Tests inspected: `tests/test_prop_risk_manager.py`, `tests/test_prop_state_persistence.py`, `tests/test_accounts_integration.py`, `tests/test_coordinator_flow.py`, `tests/test_daily_risk_state_persistence.py` (the canonical journal-seed pattern).

## Work Completed
- `prop_risk.py`: `PropRiskManager.__init__` now calls `super().__init__(..., account_id=account_name or "")` — prop accounts get the same journal-based daily-risk rebuild every regular account already gets; `""` preserves the in-memory contract for nameless test/one-off constructions.
- Reworked the 4 prop-breach tests (`test_accounts_integration.py`, `test_coordinator_flow.py`) to seed the breaching daily loss as a real today-dated closed trade in an isolated temp journal (new `prop_journal` fixture + `_seed_breach_trade` helper) instead of poking in-memory `daily_pnl`; added an assertion that the journal rebuild populated `daily_pnl` (strengthening, not weakening).
- Added `TestJournalSourcedDailyRisk` to `test_prop_risk_manager.py`: `account_name` → `account_id` wiring, cap engages from journal PnL, breach survives a "restart", and a clean journal still allows (positive control).
- Created resolved backlog item `BL-20260617-PROP-RISK-ACCOUNT-ID`.

## Validation Performed
- Tests run: `test_prop_risk_manager.py` (33) + `test_prop_state_persistence.py` + `test_accounts_integration.py` + `test_coordinator_flow.py` → all pass (113 before the new guard, 117 after). Broader risk/accounts set (`s010/s012/s026/s043/daily_loss_pct/daily_risk_state/daily_cap_alert/per_strategy_risk/breakout_prop_wiring/accounts_status`) green.
- Manual code verification: confirmed the 4 named tests fail on the change BEFORE the rework (journal rebuild overwrites in-memory `-200` → `0`), then pass after — proving the rework exercises the real path, not papering over a regression.
- Gaps not yet verified: full repo-wide `pytest` cannot run in the web sandbox (web/API test modules fail at collection on missing heavy deps — `fastapi`, `pyo3`/pydantic, `pybit`). 4 `test_smoke_test_pipeline.py` failures are pre-existing and environmental (`MagicMock → Decimal`, missing `pybit`) — they fail identically on the pre-change baseline.

## Documentation Updated
- Rules doc updates: none required.
- Architecture doc updates: none.
- Roadmap updates: none.
- Subsystem doc updates: `docs/claude/health-review-backlog.json` — added `BL-20260617-PROP-RISK-ACCOUNT-ID` (resolved).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- The prop-account `account_id`-drop was a latent Prime-Directive-adjacent gap (a required risk capability silently degraded for one account class). Now closed — prop matches the regular-account path.

## Risks and Follow-Ups
- Remaining technical risks: low — change is confined to the constructor argument; the journal-rebuild machinery it enables is the same code regular accounts have run since S-PERSIST-CANON.
- Remaining product decisions (Tier 3): operator approval required before merging the draft PR.
- Blockers: none.

## Deferred Items
- None.

## Next Recommended Sprint
- Suggested next sprint: operator review + merge of this draft PR; then drain remaining `BL-20260617-*` backlog items.
- Why next: Tier-3 gate — merge is operator-approved.
- Required verification before starting: confirm CI green on the PR.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. (n/a — no pipeline-stage change; risk-state init only.)
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
