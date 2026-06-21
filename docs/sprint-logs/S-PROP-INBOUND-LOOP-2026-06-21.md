# Sprint Log: S-PROP-INBOUND-LOOP-2026-06-21

## Date Range
- Start: 2026-06-21
- End: 2026-06-21

## Objective
- Primary goal: Build the Breakout prop manual-bridge **inbound** loop (P2/P3) — the
  fill/close report-back the operator was missing — and remove the per-trade
  "pause for confirmation" from the outbound ticket.
- Secondary goals: surface it on the dashboard (Prop tab); then, after a wiring
  bug surfaced, fix the bug AND add structural prevention so the class recurs less.

## Tier
- Tier 2 (new API write endpoint + DB writer + new notification + reconciliation +
  service-consumed code) for the bot; Tier-1 for docs/CI/hook prevention work.
- Justification: operator-approved build ("build it now"); the live order path is
  untouched (prop has no broker API — emission + journaling only).

## Starting Context
- Active roadmap items: prop-accounts architecture (Tier-1 done 2026-06-17; P1
  outbound emitter shipped; P2/P3 design-only).
- Prior sprint reference: `docs/integrations/breakout-poc-manual-bridge-DESIGN.md`.
- Known risks at start: prop must never blend into real-money/paper KPIs.

## Repo State Checked
- Branch/commit reviewed: `main` @ pre-session HEAD; live VM `ict-bot-arm`.
- Deployment state reviewed: deploy + web-api restart logs via system-actions.
- Canonical docs reviewed: CLAUDE.md, CLAUDE-RULES-CANONICAL.md, the prop DESIGN doc.

## Files and Systems Inspected
- Code: `src/prop/breakout_executor.py`, `breakout_notify.py`, `breakout_ticket.py`,
  `account_rulesets.py`, `src/units/db/database.py`, `src/web/api/main.py`,
  `routers/devices.py`, `src/runtime/mobile_push/{trade_events,event_kinds}.py`,
  `src/utils/paths.py`.
- Config: `config/accounts.yaml` (breakout_1), `config/prop_rulesets/breakout.yaml`.
- Live data: `order_packages` (diag relay) — confirmed the real SOL ticket exists.

## Work Completed
- **Outbound:** dropped "Pause for my confirmation" from the prop ticket
  (`breakout_ticket.py`); bracket SL/TP + TTL/entry-band guards unchanged.
- **Inbound P2 (PR #4070):** `src/prop/prop_journal.py` (`prop_tickets` /
  `prop_fills` / `prop_account_status` tables in `trade_journal.db`, isolated from
  `trades`); `prop_report.ingest_report`; `POST /api/bot/prop/report` (token-gated,
  `routers/prop.py`); `prop_closed` / `prop_fill` event kinds + `emit_prop_fill`;
  executor records each outbound ticket (+ rendered message) at emit time.
- **Reconciliation P3:** `prop_reconcile.py` — `match_fill_to_ticket`,
  `find_unacted_tickets`, `compute_rule_distance` ($150 daily / $300 static-DD).
- **Read surface:** `GET /api/bot/prop/{fills,tickets,status,reconcile}`.
- **Dashboard (ict-trader-dashboard #113/#114/#115):** Prop tab — rule-distance
  panel, report-back form, journal; open-trade cards + sent-messages log with the
  trade-message drop-down; graceful 404 during the deploy window.
- **Wiring bug + fix (PR #4083):** the tickets view read the new (empty for history)
  `prop_tickets` table instead of the canonical `order_packages`. Fixed:
  `list_outbound_tickets` now PROJECTS over `order_packages` (filtered to prop
  strategies from `accounts.yaml`) enriched by the sidecar. Regression test added.
- **Recurrence prevention (PR #4084):** § Generation Discipline **Rule 3**
  (compliance gate before merge) in CLAUDE-RULES-CANONICAL; mirrored into the
  SessionStart hook (`.claude/settings.json`); `new-table-wiring` CI guard
  (`scripts/check_new_table_wiring.py` + workflow + tests) requiring a
  `# data-wiring:` annotation on any new persistent table.

## Validation Performed
- Tests run: prop suite + event-kinds + new-table-guard — all green (ruff +
  silent-empty + new-table guards clean).
- Manual code verification: confirmed via live diag pull that the SOL
  `trend_donchian_sol` ticket is in `order_packages` (the projection source).
- Deploy verified: pull-and-deploy synced `49ceb76→2937913` and restarted
  `ict-web-api` (deploy log).
- Gaps not yet verified: could not pull the literal `/api/bot/prop/tickets` JSON —
  blocked by the diag-relay `/api/bot/*` allowlist (prop paths not listed) + the
  sandbox VM firewall. Verified the source data + logic + deploy instead; operator
  reload of the Prop tab is the authoritative end-to-end check.

## Documentation Updated
- Rules doc updates: CLAUDE-RULES-CANONICAL § Generation Discipline Rule 3.
- Architecture doc updates: none (API contract lives in CLAUDE.md, updated there).
- Roadmap updates: this session's entry (see ROADMAP "Last Updated").
- Subsystem doc updates: `breakout-poc-manual-bridge-DESIGN.md` (P2/P3 marked done,
  ingest-channel decision recorded); CLAUDE.md API table + tables list; dashboard
  CLAUDE.md (Prop tab + write-exception note).

## Contradictions or Drift Found
- None across the canonical set (mechanical `canonical-doc-coherence` checks pass).
- The wiring bug itself was a doc-vs-reality avoidance failure (db-wiring skill not
  run) — addressed structurally by Rule 3 + the CI guard rather than a doc line.

## Risks and Follow-Ups
- Remaining technical risks: prop message drop-down is blank for tickets emitted
  before this deploy (message-capture is new) — expected, not a bug.
- Diag-relay `/api/bot/*` allowlist should add the prop read paths so future
  sessions can verify the prop endpoints live (logged to health backlog).

## Deferred Items
- Adding prop GET paths to the `vm-diag-snapshot` allowlist (health backlog).
- Android `EventKind.kt` mirror for `prop_fill`/`prop_closed` custom channels
  (optional — new kinds already flow via the event-kinds endpoint).

## Next Recommended Sprint
- Suggested next: operator soak of the Prop tab with a real fill report (verify the
  `prop_closed` notification + open-trade card end-to-end).
- Required verification before starting: confirm the tickets log shows the SOL ticket.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No live pipeline stage changed (prop emission/journaling only).
- [x] Roadmap status was checked + updated.
- [x] Contradictions were recorded (none).
- [x] Remaining unknowns were stated clearly (endpoint live-pull gap).
