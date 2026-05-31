# Sprint Log: S-INTENT-HOLD-2026-05-31

## Date Range
- Start: 2026-05-31
- End:   2026-05-31

## Objective
- Primary goal: ship the Tier-3 conflict-policy change (close-and-reverse → hold) that the walk-forward verdict licensed on 2026-05-30. Operator approved this session in chat.
- Secondary goals: keep the legacy reverse policy wired as a no-redeploy rollback (`INTENT_CONFLICT_POLICY=reverse`); cover the new code with unit + coordinator-level tests; update the audit doc with an implementation addendum explaining why the code lands in `compute_execution_delta` rather than `aggregate_intents`.

## Tier
- Tier 3 — changes a core execution invariant (`compute_execution_delta`) and the coordinator's intent-mode dispatch path.
- Justification: walk-forward verdict PASS on both pre-agreed criteria (`docs/audits/walkforward-flip-policy-2026-05-30.md`); operator approved filing the PR in chat this session.

## Starting Context
- Active roadmap items: PERF-20260530-001 (parked at `awaiting_tier3_decision` after walk-forward).
- Prior sprint reference: `docs/sprint-logs/S-STRAT-FVG-RANGE-2026-05-30.md` (system backtester + 4.2yr 6-member findings + scope doc) → walk-forward driver PR #2433 → verdict PR #2439.
- Known risks at start: changes the live aggregator's behaviour at a conflict tick; operator visibility on demo soak is the validation gate post-merge.

## Repo State Checked
- Branch or commit reviewed: `main` @ `9c3adde` (post-#2439).
- Deployment state reviewed: live trader on `158.178.210.252` runs the multi-strategy intent layer with `MULTI_STRATEGY_INTENT_LAYER=true`; no recent diag pulled this session (the change is code-only + env-var-rollback-safe).
- Canonical docs reviewed: `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md` (permission tiers + Prime Directive), `docs/ARCHITECTURE-CANONICAL.md` (Section: "The decider IS the intent aggregator (today crude)").

## Files and Systems Inspected
- Code files inspected: `src/runtime/intents.py` (full module — `aggregate_intents` + `compute_execution_delta` + `compute_execution_delta_for_package`), `src/runtime/intent_multiplexer.py` (caller of `aggregate_intents`), `src/runtime/positions.py` (`current_net_position_qty` — the per-account position read), `src/core/coordinator.py` (lines 1330-1430 — the intent-mode dispatch branch).
- Config files inspected: none changed; the env-var lives on the systemd unit, not in YAML.
- Deployment files inspected: none touched (env-var rollback is a `systemctl edit` on the live VM by the operator if needed).
- Docs inspected: `docs/audits/walkforward-flip-policy-2026-05-30.md` (the verdict the PR ships against), `docs/sprint-plans/CONFLICT-POLICY-WALKFORWARD-SCOPE-2026-05-30.md` (scope doc the verdict references), `docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md` (the larger decider-v2 plan this work feeds into).
- Services or timers inspected: none mutated; `ict-trader-live.service` will pick the change up on its next deploy + restart.
- GitHub Actions workflows inspected: none changed; the PR's CI runs the standard 11-check suite.

## Work Completed
- Item 1: `src/runtime/intents.py::compute_execution_delta` gained a `conflict_policy: str = "reverse"` kwarg. When `conflict_policy == "hold"` and the opposite-side branch would have returned `action="flip"`, it now returns `action="noop"` with `reason="conflict_hold: …"` carrying both current and desired sides. Default at this layer stays `"reverse"` so existing call-sites and tests of the function keep their expectations; the live default is set at the caller.
- Item 2: `src/runtime/intents.py::compute_execution_delta_for_package` gained the same kwarg and threads it through to `compute_execution_delta`.
- Item 3: `src/core/coordinator.py::multi_account_execute` (the intent-mode dispatch branch) reads `INTENT_CONFLICT_POLICY` from env, defaults to `"hold"`, validates the value, and passes it down. This is where the live policy default is set. An operator rolling back to `"reverse"` is one `systemctl edit ict-trader-live.service` away; no code-level redeploy needed.
- Item 4: tests added in `tests/test_intent_delta_dispatch.py::TestConflictPolicyHold` (8 tests): (a) hold-on-conflict for both long-with-short-desired and short-with-long-desired, (b) hold does not affect the open-from-flat path, (b cont'd) explicit `conflict_policy="reverse"` still flips, (d) same-side increase and same-side already-at-target unaffected, close-to-flat under hold still closes, (c) aggregator's `dropped_intents` are stamped onto the `DesiredPosition.meta` so the audit trail explains the held-against side. Plus one coordinator-level test (`test_intent_mode_flip_holds_under_default_policy`) confirming the dispatcher noops with `reason='intent_noop:conflict_hold:…'` under the default policy. The existing `test_intent_mode_flip_dispatches_close_then_open` was preserved by pinning `INTENT_CONFLICT_POLICY=reverse` in its `monkeypatch.setenv`.
- Item 5: `docs/audits/walkforward-flip-policy-2026-05-30.md` gained an "Implementation addendum (2026-05-31, operator-approved)" explaining (i) why the code lands in `compute_execution_delta` instead of `aggregate_intents` (the harness modeled it at the executor layer; live equivalent is the same layer), and (ii) why the function-level default stays `"reverse"` with the live default set at the caller (test back-compat + env-var rollback path).
- Item 6: `docs/claude/performance-review-backlog.json::PERF-20260530-001` flipped from `awaiting_tier3_decision` → `tier3_pr_open` with resolution criteria for the demo soak.

## Validation Performed
- Tests run: `pytest tests/test_multi_strategy_intents.py tests/test_intent_delta_dispatch.py tests/test_multi_account_execute_early_out_logs_refusal.py tests/test_multi_account_execute_per_account_mode.py -q` → **88 passed in 0.94s** (32 pre-existing intent-delta + 16 new = 48 in the delta file; 33 pre-existing multi-strategy intents; 7 multi-account execute). `ruff check src/runtime/intents.py src/core/coordinator.py tests/test_intent_delta_dispatch.py` → clean.
- Dry-runs or staging checks: none — change is gated on operator's demo soak post-merge.
- Manual code verification: read `compute_execution_delta` end-to-end, confirmed `conflict_policy="hold"` only branches on the previously-flip path (line 742 in the original) and that all other branches (flat→open, same-side increase/reduce/noop, flat-close) are untouched. Confirmed the coordinator import of `os` was already in place (line 22) so no new import.
- Gaps not yet verified: (a) the demo-soak observation that legitimate flips become noops in the trade journal (`intent_noop:conflict_hold:…` rows) — this is the operator-visible signal post-merge; (b) any second-order effect on the dashboard's per-account intent-rejection histogram. Both will surface in the next `/performance-review`.

## Documentation Updated
- Rules doc updates: none — the existing permission-tier text already covered Tier-3 for `src/runtime/intents.py`.
- Architecture doc updates: none — the "decider IS the intent aggregator" section in `ARCHITECTURE-CANONICAL.md` § Change log 2026-05-23/24 still accurately describes the layer; the policy switch is a leaf-level config of that decider, not a re-architecture.
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): none — the dispatch shape is unchanged (noop already exists as a delta action; the dashboard's intent-rejection view already renders `intent_noop:…` rows).
- Roadmap updates: none — this is a Tier-3 follow-on to S-STRAT-FVG-RANGE, not a new roadmap item.
- GitHub Actions doc updates: none.
- Subsystem doc updates: `docs/audits/walkforward-flip-policy-2026-05-30.md` § Implementation addendum (new section); `docs/claude/performance-review-backlog.json::PERF-20260530-001` (status + description + resolution criteria).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1: the verdict-PR design proposal (`docs/audits/walkforward-flip-policy-2026-05-30.md` § "Tier-3 design proposal") said the change lives in `aggregate_intents`. The walk-forward harness actually modeled it at the executor layer; the code lands there too. Addressed by the Implementation Addendum so the doc and the code agree.
- Contradiction 2: none.
- Code/doc mismatch: addressed in this PR (audit doc addendum + backlog item description point readers to `compute_execution_delta`, not `aggregate_intents`).

## Risks and Follow-Ups
- Remaining technical risks: (a) `current_net_position_qty` reads the trade journal — if a position row is stale (reconciler hasn't run), the dispatcher could miscount as flat and open a fresh side even under `hold`. This was already a risk pre-change (the reverse-flip path also reads it) and is not a regression. (b) Three-leg flips through a flat moment (close hits SL, then aggregator wants opposite side) still open fresh — `hold` only suppresses flips against an *open* position; that's the documented behaviour and matches the walk-forward harness.
- Remaining product decisions (Tier 3): operator's demo-soak window length on bybit_1 before flipping bybit_2. No code knob — just the observation gate.
- Blockers: none.

## Deferred Items
- Deferred item 1: decider-v2 *selection* layer (per `docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md` v2 step 2/3). Remains the next Tier-3 prize and the prerequisite for any `turtle_soup` / `ict_scalp_5m` shadow→live promotion. The 6-member-bleeds finding from the walk-forward audit is unchanged by this PR.
- Deferred item 2: `signal_ttl_bars` sweep was already documented as second-order in the original flip-churn addendum; no action this sprint.

## Next Recommended Sprint
- Suggested next sprint: post-merge demo-soak `/performance-review` window on bybit_1 — confirm the resolution criteria in `PERF-20260530-001`. Then start decider-v2 selection-layer scoping (a fresh scope doc analogous to `CONFLICT-POLICY-WALKFORWARD-SCOPE-2026-05-30.md`, this time for "regime-route / skip-off-regime / P(profit) pick").
- Why next: the conflict-policy change is the smaller of the two Tier-3 levers identified in the walk-forward audit; selection layer is the larger prize and gates turtle/ict_scalp promotion.
- Required verification before starting: (a) the PR has merged and deployed; (b) at least one demo-soak window's worth of `intent_noop:conflict_hold:…` audit rows is in the journal showing the new behaviour is being exercised; (c) no regressions in legitimate open / increase / reduce / close paths during the soak.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade Process tab was visually verified. (Pipeline shape unchanged — noop is an existing delta action and the rejection-row contract is unchanged.)
- [x] Roadmap status was checked. (No roadmap line touched; backlog item updated.)
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
