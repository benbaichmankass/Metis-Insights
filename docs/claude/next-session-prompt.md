# Next-session prompt — post-S-067-followups (2026-05-10)

Use this as the prompt when starting the next Claude Code session on
`benbaichmankass/ict-trading-bot`. Copy-paste the block below verbatim
into a fresh session.

---

You are picking up an autonomous session on `benbaichmankass/ict-trading-bot`.
The S-067 follow-up queue closed on 2026-05-10 — see
`docs/claude/checkpoints/CHECKPOINT_LOG.md` § CP-2026-05-10-03 for the
ledger (10 items shipped, 4 Phase-2 follow-ups filed).

## Read first (in this order)

1. `docs/claude/checkpoints/CHECKPOINT_LOG.md` § top entry —
   confirms what shipped + the 4 Phase-2 follow-ups filed.
2. `docs/claude/milestone-state.md` § Queued milestones — workplan
   priority order.
3. The phase-2 references inside the most recent CP entries
   (linked below).

## Hard constraints (unchanged from last session)

- **Tier 1 by default; Tier 2 = DRAFT + ping operator.** See
  `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers (canonical
  authority since 2026-05-10).
- **One PR per item.** Don't bundle.
- **Self-merge Tier 1 after CI green.**
- **Live-mode invariant.** No edits to `src/runtime/{orders,pipeline,
  risk_counters,order_monitor}.py`, `src/main.py`,
  `src/units/accounts/execute.py`, `config/{accounts,strategies}.yaml`,
  or `deploy/*.service` in any Tier-1 PR. Items A and B below
  explicitly touch these and are flagged Tier 2.
- **Auto-ping mechanics.** `notify_on_pull.py` keys off
  `docs/claude/checkpoints/CHECKPOINT_LOG.md` diff lines. Append to
  the canonical log directly when you have local clone access; only
  fall back to standalone CP files in `docs/claude/checkpoints/CP-*.md`
  when the MCP API can't round-trip the full file. Lesson learned
  from CP-2026-05-10-03.

## Pickup queue (priority order)

### Workplan priorities (from `milestone-state.md` § Queued milestones)

1. **S-047 T6 — end-to-end live smoke + runbook (D8)** — workplan
   priority #1. Operator-gated on a Bybit web-UI Spot Margin toggle
   for `bybit_2`. Ad-hoc / live-trading. Runs in parallel with the
   queue below.
2. **S-047 T7 — sprint close** — docs-only after T6.
3. **M5 — Strategy testing workflow** — auto-claude.

### S-067 Phase-2 follow-ups (filed 2026-05-10)

Each is one PR, sized for 30-90 min:

#### A. Closed-flat invariant tick-loop wiring (Tier 2 — DRAFT + ping)

Phase-2 of S-067 follow-up #3. The Phase-1 module + tests + design
memo shipped via PR #658 (`src/runtime/closed_flat_invariant.py`,
`docs/claude/closed-flat-invariant.md`). This phase wires
`closed_flat_invariant.check()` into the tick loop in
`src/runtime/order_monitor.py` (post-close hook, alongside
`_reconcile_orphan_positions`), gated by
`CLOSED_FLAT_INVARIANT_ENABLED` (default `false`). After a 7-day
alert-only soak, file the auto-flatten promotion PR (per-account
`closed_flat_auto_flatten` flag in `config/accounts.yaml`).

#### B. Env-gate purge Phase-2 annotations (Tier 2 — DRAFT + ping)

Phase-2 of S-067 follow-up #4. The Phase-1 audit + lint guard
shipped via PR #659. This phase adds inline
`# allow-silent: <reason>` comments on the two surviving env-gate
call sites (`src/runtime/pipeline.py:194` for
`MULTI_ACCOUNT_DISPATCH`, `src/runtime/order_monitor.py:680` for
`MONITOR_RECONCILE_ENABLED`) plus per-survivor regression tests
asserting "flipping this gate does NOT bypass `RiskManager.evaluate`".

#### C. Exchange-fills FIFO lot-matching P&L (Tier 1)

Phase-2 of S-067 follow-up #6. The Phase-1 store + puller +
fee/flow aggregates shipped via PR #652
(`src/runtime/exchange_fills_{store,puller}.py`,
`/api/bot/pnl/exchange?days=N`). This phase adds true P&L
attribution via FIFO buy/sell lot pairing over the fills stream,
with `realized_pnl` / `unrealized_pnl` fields added to the wire
shape (additive — existing dashboard readers won't break).

#### D. hourly_report + boot_audit borderline narrowings (Tier 1, 4 small PRs)

Phase-2 of S-067 follow-up #8. The Phase-1 audit + lint guard
extension shipped via PR #656
(`docs/audits/silent-empty-reporting-2026-05-10.md`). Each of the
four borderline sites becomes one Tier-1 PR:

* D1. `src/runtime/boot_audit.py:72` — replace `0`-on-failure with
  `None`-on-failure; render `(query failed)` in the boot ping.
* D2. `src/runtime/hourly_report.py:250` (`list_accounts`) — narrow
  except, surface "data unavailable" in report body.
* D3. `src/runtime/hourly_report.py:312`
  (`strategy_dashboard_data`) — same shape.
* D4. `src/runtime/hourly_report.py:409` (`run_all_checks`) —
  same; downstream `checks_critical = any(...)` aggregation needs
  to tolerate an "unknown" sentinel.

Each ships with a regression test that asserts the new sentinel
flows through the report assembly without crashing.

## Stop conditions (unchanged)

- >90 min on a single item without a shippable PR → commit
  `BLOCKED-PM`, file draft, skip.
- Tier-1 fix surfacing Tier-2 concern → stop, refile.
- End of queue → append a checkpoint to `CHECKPOINT_LOG.md`
  (canonical log; this is what triggers the auto-ping) and stop.

## What's already deployed (don't redo)

The full S-067 + S-067-followup ledger is in
`CHECKPOINT_LOG.md` § CP-2026-05-10-03. Highlights:

- Shared real-schema test fixture (`tests/fixtures/real_schema_db.py`).
  Default for any new endpoint regression test.
- `/api/diag/version` (returns the running web-api git SHA) +
  deploy script enumeration via `systemctl list-units 'ict-*.service'`
  + post-deploy SHA round-trip assertion.
- Exchange-fills sqlite store + Bybit puller CLI + endpoint
  (Phase 1).
- `silent-empty-guard` + `env-gate-guard` CI workflows + their
  `# allow-silent: <reason>` override syntax. Both lint scripts:
  `scripts/check_silent_empty_in_diff.py` +
  `scripts/check_env_gate_in_diff.py`.
- Closed-flat invariant module (Phase 1, tests + memo).

## What you must NOT touch

- Live-order path files except as flagged Tier-2 in items A and B
  above (which require operator ack pre-merge).
- `config/accounts.yaml` / `config/strategies.yaml` — Tier 3.
- `deploy/*.service` — outside scope of every queued item.
- Anything that would silence the `silent-empty-guard` or
  `env-gate-guard` workflows. The guards are the contract this
  sprint shipped; respect them.

## Workplan order context

S-047 T6 is workplan priority #1 but operator-gated on a Bybit
toggle and runs on its own branch in parallel. The 4 Phase-2
follow-ups (A–D above) are the next-most-actionable Tier-1 work
while S-047 T6 waits.
