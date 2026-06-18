# Env-gate audit (2026-05-10)

S-067 follow-up #4. **Tier 2 — DRAFT pending operator ack.**

The 2026-05-03 directive (BUG-039) said per-account
`RiskManager.dry_run` is the only live/dry switch. PR #630 deleted
`MONITOR_APPLY_TO_EXCHANGE`, the most recent silent regression of
that contract. This audit walks every remaining
`os.environ.get("…")` site in `src/` matching the suspect patterns
from `docs/claude/next-session-prompt.md` § Item #4:
`MULTI_ACCOUNT_*`, `*_ENABLED` (live/dry-related), `*_APPLY_TO_*`,
`*_DRY_*`, `MONITOR_*`, `DISPATCH_*`.

## Survey

```bash
grep -rEn 'os\.(environ|getenv).*?(MULTI_ACCOUNT_|MONITOR_|DISPATCH_|_APPLY_TO_|_DRY_|_ENABLED)' src --include='*.py'
```

Yields three matches across the patterns relevant to live/dry
contracts (M5_CONSUMER_ENABLED is excluded per the prompt — it is
a feature-init gate, not a live/dry one):

| Env var | File:line | Default | Purpose | Decision |
|---|---|---|---|---|
| `MULTI_ACCOUNT_DISPATCH` | `src/runtime/pipeline.py:194` | `true` | Operator escape hatch — pin to legacy single-client path for single-account smoke deployments that don't load Coordinator. **Cannot suppress live writes**: the Coordinator path itself routes through `RiskManager.evaluate()` which is the live/dry switch. The fallback path also routes through the same `RiskManager`. So flipping this to `false` does NOT downgrade live → dry. | **Document, keep.** Add `# allow-silent: …` comment + regression test asserting both branches still go through `RiskManager.evaluate`. |
| `MONITOR_RECONCILE_ENABLED` | `src/runtime/order_monitor.py:680` | `false` | SSOT-from-Bybit reconciler gate (issue #502). Default off — explicit operator opt-in for the post-S-055 reconciler. **Reads only**: the reconciler closes DB-stale rows when the exchange shows the trade as filled; it does not place new orders. So flipping this to `true` does NOT enable live writes that wouldn't already happen. | **Document, keep.** Add `# allow-silent: …` comment + regression test asserting the reconciler doesn't place orders. |
| `RECONCILER_GRACE_SECONDS` | `src/runtime/order_monitor.py:690` | 60 | Tunable for the reconciler's grace window. Numeric, not a kill-switch. | **Excluded from the audit** — not a boolean gate. |

### Out of scope (excluded by the prompt)

| Env var | Why excluded |
|---|---|
| `M5_CONSUMER_ENABLED` | Per the prompt: "exclude unrelated feature flags like `M5_CONSUMER_ENABLED` which is just a bot init gate". |
| `COMMS_PUSH_ENABLED` | Telegram-side feature flag, no order path interaction. |
| `NEWS_ENABLED` / `NEWS_VETO_ENABLED` | News-feed feature flags, no order-write side. |
| `SIGNAL_DUAL_WRITE_DISABLED` | S-034 dual-write transition flag for the signals table; logging only. |
| `BYBIT_TESTNET` | Documented and intentional environment selector. |

## Already removed (BUG-039 / PR #630)

These are gone — confirmed by absence in the survey above:

* `DRY_RUN` — removed 2026-05-03.
* `ALLOW_LIVE_TRADING` — removed 2026-05-03.
* `MODE=LIVE|BACKTEST` — removed 2026-05-03.
* `MONITOR_APPLY_TO_EXCHANGE` — removed in PR #630.

## Decision: keep both survivors with explicit documentation

Neither survivor can suppress live exchange writes. The risk class
behind the audit (a `*_ENABLED=false` flip silently downgrading
live → dry) does not apply to either, but both could become
*new* sources of confusion if a future regression reuses the
pattern with weaker semantics. So:

1. Annotate each call site with `# allow-silent: <reason>` per the
   `silent-empty-guard` precedent (document, don't delete).
2. Add a regression test per survivor asserting the contract
   ("flipping this gate does NOT bypass `RiskManager.evaluate`" /
   "this gate does NOT enable order placement").
3. Add a CI lint that flags any *new* env var matching the suspect
   patterns added under `src/runtime/`, `src/units/`, or
   `src/web/` unless the line carries an inline
   `# allow-silent: <reason>` justification.

The CI lint is the long-term enforcement; the doc + tests are the
audit's record of the existing state.

## New annotated survivor — `REGIME_BAR_SCORING_DISABLED` (S-MLOPT-S13, 2026-06-04)

A third `os.environ.get` read matching the suspect pattern now lives
under a protected path:

* `src/runtime/regime_bar_scoring.py` —
  `regime_bar_scoring_enabled()` reads `REGIME_BAR_SCORING_DISABLED`.

It is registered here per the contract enforced by
`tests/test_env_gate_survivors_no_risk_bypass.py::test_no_new_protected_env_gates_in_runtime`
and `scripts/check_env_gate_in_diff.py` (inline `# allow-silent:`
on the read line + audit-doc entry).

**Why it is a legitimate survivor (matches the pattern, inverts the
risk class):**

* It is a **kill-switch, not a capability gate** — default **on**
  (the env var is read as `*_DISABLED`; unset → scoring runs). The
  BUG-039 risk class is a default-**off** `*_ENABLED` flag silently
  *stranding* a capability (the MES-stranding pattern); a default-on
  `*_DISABLED` switch cannot strand anything — omitting it keeps the
  feature live.
* It gates an **observe-only** path. `emit_regime_bar_predictions`
  only calls `ShadowPredictor.predict` (appends to
  `runtime_logs/shadow_predictions.jsonl`); there is **no** code path
  from it to an order package, `RiskManager.evaluate/approve`, or the
  live/dry decision. The per-account `RiskManager.dry_run` flag
  remains the sole live/dry switch.
* Its purpose is operability: let the operator disable the per-bar
  shadow-logging path on the live VM without a redeploy if it ever
  misbehaves (mirrors `SIGNAL_DUAL_WRITE_DISABLED`).

So the survivor count is now **three** — two live-order-path
reconciliation/dispatch gates (below) plus this observe-only
shadow-logging kill-switch.

## New annotated survivor — `REGIME_ROUTER_ENABLED` (PERF-20260601-006, 2026-06-07)

A fourth `os.environ.get` read matching the suspect pattern now lives
under a protected path:

* `src/runtime/intents.py` — `_regime_router_enabled()` reads
  `REGIME_ROUTER_ENABLED` (default `0` → off).

Same contract: inline `# allow-silent:` on the read line + this
audit-doc entry.

**Why it is a legitimate survivor (matches the pattern, justified):**

* It is the **operator-controlled rollback switch** for the regime
  router's phase-3 hard gate (PERF-20260601-006). The phase-3 design
  doc + the backlog item both name this exact env-var (with the
  default-off semantics) as the canonical rollback mechanism: "one env
  flip + restart, no redeploy." Removing the gate would force a code
  deploy for every roll-forward / roll-back; that's the wrong cost
  profile for a hard-gate that turns OFF live trade dispatches.
* It **cannot suppress live exchange writes**. When truthy, the gate
  *removes* OFF-cell candidate intents from `aggregate_intents()`
  BEFORE the reinforcement / conflict-resolution logic runs — every
  surviving intent still routes through the same per-account
  `RiskManager.evaluate()` (the sole live/dry switch). The risk class
  is "drops some intents" not "silently flips live → dry"; the
  RiskManager.dry_run invariant is preserved.
* It **does not strand a new capability when default-off**. The
  BUG-039 risk pattern is a default-off `*_ENABLED` gate hiding a
  capability the user expects to be live (the MES-stranding pattern).
  When `REGIME_ROUTER_ENABLED` is unset/off, the existing
  `_shadow_regime_gate` runs and the aggregator behaves exactly as it
  did before this PR (phase 2, log-only, byte-identical decision).
  Phase 3 is *new* code; nothing pre-existing is stranded by the
  default-off posture.
* **Fail-permissive on every exception** — a policy-load or per-intent
  verdict failure keeps the intent (`kept.append(intent)`) so a bug in
  the gate cannot silently strand a tradeable signal. The bias is
  toward the pre-phase-3 behaviour.

So the survivor count is now **four** — two live-order-path
reconciliation/dispatch gates (below), one observe-only shadow-logging
kill-switch (above), and this operator-controlled phase-3 hard-gate
rollback switch.

## Considered-and-removed — `LOCAL_PNL_COMPUTE_DISABLED` (added then removed 2026-06-16)

The local-PnL fallback (BL-20260616-IBKRPNL) initially shipped with a
default-ON `LOCAL_PNL_COMPUTE_DISABLED` kill-switch (a reporting-sweep
toggle, never the order path). On operator review it was **removed the
same day**: the local-PnL fallback is **baseline required correctness**,
and even a default-ON reporting kill-switch is an unnecessary gate when
revert + redeploy already covers a genuine bug. Removing it keeps the
"don't build gates we don't need" contract clean and is consistent with
the de-gating precedent (`NAKED_POSITION_AUTOPROTECT`,
`MONITOR_RECONCILE_ENABLED`). `_sweep_local_pnl_for_unpriced` now runs
unconditionally. The survivor count is back to **four** (the two
live-order-path reconciliation/dispatch gates + the observe-only
shadow-logging kill-switch + the phase-3 hard-gate rollback switch).

## New annotated survivor — `CROSS_ASSET_LIVE_DISABLED` (S-CROSS-ASSET-PROBE D2a, 2026-06-18)

`src/runtime/cross_asset_live.py::cross_asset_live_disabled()` reads
`CROSS_ASSET_LIVE_DISABLED`. Same class as `REGIME_BAR_SCORING_DISABLED`
above — an **observe-only kill-switch**, NOT a live/dry capability gate.

**Why it is a legitimate survivor (matches the `*_DISABLED` pattern, inverts
the BUG-039 default-OFF footgun):** it is **default-ON** (the feature runs
unless explicitly disabled), so it never strands a capability behind a
default-off flag — the inverse of the BUG-039 anti-pattern. When on, it only
controls whether the per-bar regime scorer computes the cross-asset `xa_*`
feature block for a **shadow-stage** regime head (→ `shadow_predictions.jsonl`);
it cannot suppress or enable a live exchange write. The per-account
`RiskManager.dry_run` remains the only live/dry switch. Contract: inline
`# allow-silent:` on the read line + covered by the same regression test
(`test_no_new_protected_env_gates_in_runtime`) and the lint guard
(`scripts/check_env_gate_in_diff.py`).

So the survivor count is now **five** — two live-order-path
reconciliation/dispatch gates, two observe-only shadow-logging kill-switches
(`REGIME_BAR_SCORING_DISABLED` + this), and the phase-3 hard-gate rollback
switch (`REGIME_ROUTER_ENABLED`).

## Phase-1 PR scope (this DRAFT)

* `docs/audits/env-gate-purge-2026-05-10.md` (this file).
* `docs/claude/trading-mode-flags.md` — updated with the canonical
  statement + surviving-gates list.
* `scripts/check_env_gate_in_diff.py` (new) — diff-based lint
  guard, mirrors `silent-empty-guard` shape. Protected paths:
  `src/runtime/`, `src/units/`, `src/web/`. Patterns: any new
  `os.environ.get("(MULTI_ACCOUNT_|MONITOR_|DISPATCH_|*_APPLY_TO_*|*_DRY_*|*_ENABLED)…")`
  unless the line carries `# allow-silent: <reason>`.
* `.github/workflows/env-gate-guard.yml` (new) — runs the script
  on every PR diff against `main`.
* `tests/test_check_env_gate_in_diff.py` (new) — unit tests for
  the lint shape (mirror `tests/test_check_silent_empty_in_diff.py`).

## Phase-2 PR (deferred)

* Annotate the two surviving call sites with `# allow-silent: …`
  comments + add the per-survivor regression tests. Touches
  `src/runtime/pipeline.py` + `src/runtime/order_monitor.py` —
  Tier 2, requires operator ack pre-merge.

Splitting Phase-1 (audit doc + lint guard, no live-order-path
edits) from Phase-2 (annotations on the live-order-path files)
lets the operator review the audit before approving any source
edit on the protected paths.

## Live-mode invariant note

This DRAFT PR's Phase-1 scope **does not** touch any of the
protected files in `docs/claude/next-session-prompt.md` § Hard
constraints (`src/runtime/orders.py`, `src/runtime/pipeline.py`'s
dispatch logic, `src/runtime/risk_counters.py`,
`src/runtime/order_monitor.py`, `src/main.py`,
`src/units/accounts/execute.py`, `config/{accounts,strategies}.yaml`,
`deploy/*.service`). The lint script is pure infra; the doc
update is a doc-only change. Phase-2's annotations are the
operator-ack gate.

## Cross-references

* `docs/sprint-summaries/sprint-067-summary.md` § Hand-off — this
  is item #4.
* `docs/claude/bug-log.md` BUG-039 — the original env-var purge
  directive.
* PR #630 — `MONITOR_APPLY_TO_EXCHANGE` removal (most recent
  precedent).
* `scripts/check_silent_empty_in_diff.py` — shape this PR mirrors.
