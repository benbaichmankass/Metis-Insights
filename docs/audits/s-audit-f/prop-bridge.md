# S-AUDIT-F — prop-bridge slice (`src/prop/`)

**Session:** `session_01LGsKNEjcTeujEGVACSPESK-Fprop` · branch `claude/audit-F-prop-bridge`
**Date:** 2026-06-28 · **Part of:** M17 Full-System Audit (`docs/audits/full-system-audit-2026-06-28.md`)

## Scope & method

Read **every file in `src/prop/` in full, line by line** (20 modules, ~2,300 LOC):
`__init__.py`, `account_rulesets.py`, `breakout_executor.py`, `breakout_notify.py`,
`breakout_ticket.py`, `evaluator.py`, `funding.py`, `montecarlo.py`,
`multi_account_ticket.py`, `prop_expiry_prompt.py`, `prop_journal.py`,
`prop_monitor_pulse.py`, `prop_reconcile.py`, `prop_report.py`, `report.py`,
`ruleset.py`, `symbol_map.py`, `telegram_commands.py`, `telegram_report_handler.py`.
Cross-read the live wiring (`src/units/accounts/execute.py` breakout branch,
`coordinator.py`, `src/units/accounts/prop_risk.py`) and the prop tests to verify
the inbound/outbound ticket+fill lifecycle and the **prop-isolation contract**
(prop is a third funding class, never blended into real/paper KPIs).

Verified against the design (`docs/integrations/prop-accounts-architecture-DESIGN.md`)
and the root `CLAUDE.md` env-var + REST-API contract.

Classification legend: **BUG** (real defect) · **DEAD** (unreachable/zombie) ·
**DRIFT** (code-vs-doc/comment) · **RISK** (latent / multi-account correctness) · **OK**.

---

## Headline: isolation holds, lifecycle is sound

- **Prop isolation VERIFIED.** The breakout branch in `execute.py` (§6a, lines
  241–301) writes **no `trades` row** — it emits a ticket and terminates the
  order-package lifecycle with `status='emitted'` / `close_reason='prop_ticket_emitted'`
  (a non-`open` status the orphan sweep + strategy-monocle both ignore). The
  three prop tables (`prop_tickets`/`prop_fills`/`prop_account_status`) are
  **physically separate** from `trades`, and `prop_journal.py`'s module docstring
  + the schema confirm prop never feeds `/stats`/`/performance`/`/pnl`. The
  attribution endpoints (`/api/bot/strategy/attribution`) are real-money-only.
  **No leak path found.**
- **Ticket/fill lifecycle is sound.** `emitted → (expiry_prompted → expired |
  awaiting_report) → filled/closed | skipped` is consistently implemented across
  `breakout_executor` (emit), `prop_report` (ingest), `prop_reconcile`
  (match/unacted), `prop_expiry_prompt` (stale prompt), and the status-flip
  idempotency guards hold. The research/eval half (`ruleset`/`evaluator`/
  `montecarlo`/`funding`/`report`/`account_rulesets`) is pure Tier-1 tooling with
  no live-path imports, as documented.
- **No DEAD/zombie code found.** Every module has a live consumer
  (`emit_prop_ticket` ← execute.py; `run_prop_*` ← main.py; `handle_command`/
  `handle_expiry_callback` ← `src/bot/claude_bridge.py`; eval tooling ←
  `scripts/prop/account_compat_matrix.py`). `breakout` is a live `EXCHANGE_MAP`
  entry routed by `accounts.yaml`. No retire-arc-without-delete-arc.

---

## Findings

### F1 — RISK (multi-account latent bug): `find_unacted_tickets` cross-account `acted_keys`

`src/prop/prop_reconcile.py::find_unacted_tickets` (lines 87–101). The
"acted-on" fallback set is built **without the account in the key**:

```python
acted_keys = {
    (str(f.get("symbol")...upper()), str(f.get("direction")...lower()))
    for f in fills
}
...
key = (symbol, direction)
if key in acted_keys:
    continue   # <-- considered acted-on
```

When the caller passes `account_id=None` — which is exactly what
`prop_expiry_prompt.run_prop_expiry_prompts()` does (it calls
`find_tickets_to_prompt(now=now)` → `find_unacted_tickets(account_id=None)`) —
`fills` spans **all** prop accounts. A fill on account A for `(SOLUSDT, long)`
then masks a still-emitted, genuinely-unacted ticket on account B for the same
`(SOLUSDT, long)`, so account B's stale ticket is **never prompted / never
flagged as drift**. The same cross-account masking applies to the symmetric
`acted_ids` set only by coincidence (ticket_ids are unique), but `acted_keys`
is the fallback that fires when no explicit `ticket_id` link exists — the common
manual-bridge case.

- **Latency:** today there is exactly one prop account (`breakout_1`), so this
  cannot misfire **yet**. But the design's first scalability invariant is
  "**Accounts are always a list … built multi-account now even though only one
  prop account exists today — no single-account bugs later.**" This is precisely
  such a single-account bug.
- **Same shape, lower stakes:** `prop_monitor_pulse._position_key` correctly
  *includes* `account_id` in its fallback key, and `match_fill_to_ticket`
  correctly scopes by `account_id` — so the fix is to make `find_unacted_tickets`
  consistent with them: include `account_id` in `acted_keys` and the ticket key.
- **Tier:** **Tier-2** — it changes drift-detection / expiry-prompt behaviour
  (which tickets get prompted). Proposed as a DRAFT PR with a regression test
  (a 2-account fixture where A's fill must NOT mask B's ticket); **not merged**.
- **Evidence it's untested:** `tests/test_prop_report_ingest.py::test_find_unacted_tickets`
  uses a single account; no two-account masking case exists.

### F2 — DRIFT (Tier-1): `prop_journal.record_ticket` docstring omits `message`/`side`, and ON CONFLICT can't backfill `order_package_id`

`src/prop/prop_journal.py::record_ticket` (lines 163–208).

- **(a) Docstring drift (Tier-1, safe to fix now).** The docstring lists the
  accepted keys as "… side, … order_package_id, meta(dict)" but **omits
  `message`**, which the function reads and inserts (line 200) and which
  `breakout_executor.emit_prop_ticket` passes (the rendered ticket text). Field
  beats comment — update the docstring to include `message`.
- **(b) Latent re-emit gap (note only).** The `ON CONFLICT(ticket_id) DO UPDATE`
  clause updates `status`, `qty`, `valid_until`, `message` but **not**
  `order_package_id` or `entry/sl/tp/strategy/symbol/direction`. A ticket re-emit
  with a now-populated `order_package_id` (after an earlier emit wrote NULL)
  would **not** backfill it. In practice ticket_ids are freshly generated per
  emit (`prop-manual-<uuid>`), so a true re-emit of the same id is rare — but if
  it happens, the ticket↔package join silently stays broken. Logging to backlog
  rather than changing the upsert (the upsert change is Tier-2 — touches the
  journal write contract). The 2026-06-21 prop-tickets incident was about this
  exact join, so it's worth recording.

### F3 — DRIFT (Tier-1): `_prop_scope` and `default_prop_account` disagree on what counts as a prop account

Two helpers independently classify "is this a prop account", with **different
predicates**:

- `prop_journal.py::_prop_scope` (lines 270–276): prop if
  `exchange==breakout` OR `account_class==prop` OR `type==prop` OR
  (`backtest_ruleset` set and ≠ "standard").
- `telegram_report_handler.py::default_prop_account` (lines 49–55): prop if
  `exchange==breakout` OR `account_class==prop` **only** (no `type`, no
  `backtest_ruleset`).
- `account_rulesets.py::unit_for_account` (line 128): prop if
  `backtest_ruleset` set & ≠ standard **OR** `exchange==breakout` (no
  `account_class`, no `type`).

Three call sites, three different "is_prop" definitions. Today all collapse to
the same answer because `breakout_1` matches every predicate, so this is latent —
but a future prop account declared by `account_class: prop` **without**
`exchange: breakout` would be invisible to `default_prop_account`'s
disambiguation count, and one declared only by `backtest_ruleset` would be
invisible to both `default_prop_account` and the journal-side reconcile. **Fix
direction:** extract one canonical `is_prop_account(acct: dict) -> bool` helper
and have all three call it (Tier-1 refactor, no behaviour change since the
current single account matches all). Logged to backlog; the safe consolidation
is a focused follow-up, not bundled with the audit cleanup PR.

### F4 — OK (verified, no action): the documented "optimistic" caveats are honestly labelled

`montecarlo.py` (daily-loss is realised-only → under-counts breaches) and
`funding.py` (`PB-20260616-004` re-validation requirement; constant-rate
fallback clearly labelled) both document their modelling limits in-module rather
than hiding them. The `swap_rate_daily` CFD-style override (Breakout charges a
flat daily swap, not directional 8h perp funding) is correctly side-agnostic and
takes precedence over the perp paths. No correctness bug; the honesty bar is met.

### F5 — OK (verified): `_prop_bot_token` triple-fallback + `mirror_to_fcm=False`

`breakout_notify.py::_prop_bot_token` (lines 114–128) correctly falls back
`TELEGRAM_PROP_BOT_TOKEN → TELEGRAM_CLAUDE_BOT_TOKEN → TELEGRAM_BOT_TOKEN`
(the 2026-06-22 Ampere-cutover fix so prop tickets always reach the operator),
and every prop send passes `mirror_to_fcm=False` because the typed `prop_signal`/
`prop_fill`/`prop_closed`/`prop_monitor` FCM push already covers Android — no
double-notify. Verified the event kinds exist in `mobile_push/event_kinds.py`.

### F6 — OK (verified): expiry-prompt + monitor-pulse idempotency

`prop_expiry_prompt.run_prop_expiry_prompts` flips status to `expiry_prompted`
**only after a confirmed send** (line 200–208) so a delivery failure retries; the
status flip is the sole idempotency guard (no state file), and `find_unacted_tickets`
filters `status=='emitted'` so a prompted ticket drops out. `prop_monitor_pulse`
persists per-position cadence in `prop_monitor_pulse.json`, prunes to live keys,
and uses an account-scoped position key. Both are baseline (no enable gate) per
the Prime Directive, with only a cadence pause knob — matches the CLAUDE.md env
table. (F1 is the one defect in this otherwise-sound subsystem — it lives in the
shared `find_unacted_tickets`, which the expiry prompt depends on.)

---

## Disposition

| # | Class | Tier | Action |
|---|---|---|---|
| F1 | RISK (multi-account latent) | Tier-2 | DRAFT PR + regression test; operator-gated (drift/prompt behaviour) |
| F2a | DRIFT (docstring) | Tier-1 | Fix in audit-cleanup PR |
| F2b | RISK (re-emit join) | — | health-review backlog |
| F3 | DRIFT (3 divergent is_prop predicates) | Tier-1 | health-review backlog (focused consolidation follow-up) |
| F4–F6 | OK | — | none |

No DEAD code, no isolation leak, no broken lifecycle. The single real defect (F1)
is a multi-account latent bug, inert under today's single prop account.
