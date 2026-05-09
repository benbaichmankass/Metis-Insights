# M1 Comms Audit — Follow-up Sprint Backlog (2026-05-07, fresh)

> **Source:** `docs/audits/M1-comms-audit-2026-05-07-fresh.md` (D1 of S-048).
> **Supersedes:** `docs/audits/M1-comms-audit-followups.md` (PR #463) —
> the post-write redlines are now baked directly into the body below.
> **Status:** prioritized backlog. Sprint numbers (`S-NNN`) are placeholders
> per workplan § "Sprint and checkpoint numbering" — the next available
> number after S-049 is **S-051** (S-050 is reserved for VWAP Phase 2 per
> `docs/claude/milestone-state.md` Queued Milestones row 4). Assign at the
> time the prompt is filed.
> **Order:** workplan-correction first (unblocks documentation drift),
> then `/new-session`/`/test` commands (unblocks M5), then comms hardening.

Each entry below is a sketch — enough for the next session to expand into
a full sprint prompt under `docs/sprints/sprint-NNN-prompt.md` when the
time comes.

---

## P1 — Significant gaps (workplan-mandated, system limps along)

### P1-A — Workplan correction: ClaudeBot is intentionally one-way

- **Severity:** P1 (workplan doc — the canonical authority of the
  project; correcting it is more valuable than its size suggests).
- **Workplan ref:** `docs/claude/workplan.md` § "Telegram bots /
  @claude_ict_comms_bot / ClaudeBot workflow".
- **Files in scope:**
  - `docs/claude/workplan.md` — replace the 5-step two-way workflow
    description plus "channel must support merge-review buttons /
    required-user-action prompts / recovery alerts" with: ClaudeBot is
    a one-way Claude → operator notification channel; operator decisions
    happen through GitHub (PR comments, merges) or fresh sessions; the
    structured ask/answer comms surface lives on `@bict_trading_bot`
    via S-027.
  - `docs/audits/M1-comms-audit-2026-05-07-fresh.md` — already reflects
    the corrected reading. No edit needed.
  - `docs/sprint-summaries/sprint-042-summary.md` — no edit (S-042
    verdict reaffirmed under the corrected architecture).
- **Tier:** 1 (docs only).
- **Goal:** the canonical workplan no longer describes an architecture
  the project doesn't intend to build.
- **Out of scope:** any code change; the comms-handler stays on the
  trader bot as it is.
- **Acceptance:**
  1. Workplan § "ClaudeBot workflow" describes the one-way channel.
  2. The "channel must support merge-review buttons / required-action
     prompts / recovery alerts" language is removed or relocated to
     describe the trader-bot S-027 surface accurately.
  3. CI green.

---

### P1-D — Operator commands `/new-session <sprint_id>` and `/test <strategy>`

- **Severity:** P1 (two related missing commands; bundled because they
  share the comms-request artifact-writing pattern).
- **Workplan ref:** § "Repeatable operator-triggered workflows" / "New
  session command" + "Strategy test command".
- **Files in scope:**
  - `src/bot/telegram_query_bot.py` — new command handlers
    `cmd_new_session(update, context)` and `cmd_test_strategy(update,
    context)` registered alongside the existing operator commands at
    the `register_handlers` block.
  - `src/comms/templates.py` (new) — small module with
    `make_new_session_request(sprint_id, *, repo_root)` and
    `make_test_strategy_request(strategy, *, repo_root)` helpers that
    construct schema-compliant `comms/requests/REQ-…json` artifacts.
  - `tests/test_s051_session_test_commands.py` (new) — pin artifact
    creation, file naming, schema validity, ack message content.
  - `docs/claude/comms-architecture.md` — document the two commands.
- **Tier:** 2 — adds two new operator-facing commands; smoke-test on
  the VM but no live-trading risk (commands just write files).
- **Goal:**
  - `/new_session S-099` writes `comms/requests/REQ-<ts>-new-session.json`
    with the sprint id; replies in Telegram with the request id and
    "Claude will pick this up on the next sync."
  - `/test vwap` writes `comms/requests/REQ-<ts>-test-strategy.json`
    with `strategy: "vwap"` and an empty results block. M5's backtest
    workflow consumes the artifact and writes results back via the
    existing `apply_answer` writeback.
- **Out of scope:** the actual M5 backtest workflow that consumes the
  `test` request (separate M5 sprint); session-bootstrap behaviour on
  Claude's side (lives in CLAUDE.md / sprint prompts, not here).
- **Acceptance:**
  1. Both commands registered on the trader bot.
  2. Each writes a well-formed `comms/requests/REQ-…json` artifact and
     commits via `GitPusher` (gated by `COMMS_PUSH_ENABLED`).
  3. Telegram acknowledgement message includes the request id.
  4. Tests pin artifact creation + commit-subject prefix.

---

### P1-B — Stuck-request recovery alerts

- **Severity:** P1.
- **Workplan ref:** § "Required workflows" / "Stuck request recovery"
  + corrected-architecture: applies to the trader-bot S-027 surface.
- **Files in scope:**
  - `src/bot/comms_handler.py` — extend `CommsPoller.poll_once` to:
    (a) detect requests stuck in `sent` past a configurable
    `stuck_alert_threshold` and fire a one-time Telegram alert with
    request id, age, and a short hint;
    (b) fire a final alert before transitioning to `EXPIRED` so silent
    expiry never happens.
  - `comms/schema/request.schema.json` — add `stuck_alert_threshold`
    optional field (seconds; default 24h).
  - `tests/test_s052_stuck_request_recovery.py` (new) — pin both alert
    paths.
  - `comms/README.md` § "Stuck request? How to recover" — replace the
    "manually edit the file" workaround with a description of the new
    bot-side alerts.
- **Tier:** 1 — read-only detection + outbound notification; no
  live-trading impact.
- **Goal:** never silently expire a comms request. Operator sees a
  Telegram alert with the request id, age, and the path forward.
- **Out of scope:** auto-resend after extension (next sprint);
  `pending-pings.jsonl` recovery (different surface; not in scope per
  corrected architecture).
- **Acceptance:**
  1. A request that stays in `sent` past its `stuck_alert_threshold`
     fires a Telegram alert (one-time per request).
  2. A request that hits `expires_at` without an answer fires a final
     Telegram alert and transitions to `EXPIRED`.
  3. Tests pin both transitions.
  4. README updated.

---

### P1-C — Auto-hourly snapshot broadcast

- **Severity:** P1.
- **Workplan ref:** § "Telegram bots" / `@bict_trading_bot` /
  "Notifications" / "Hourly snapshots".
- **Files in scope:**
  - `deploy/ict-hourly-snapshot.timer` (new) + `.service` (new) — fires
    `scripts/send_hourly_now.py` once per hour with a 60s randomized
    delay.
  - `scripts/send_hourly_now.py` — verify timer-safe (idempotent; locks
    against parallel runs). Add a flock-based lock if absent.
  - `src/runtime/hourly_report.py` — verify the dedup marker behaviour
    the timer needs.
  - `tests/test_s053_hourly_snapshot.py` (new) — assert script
    idempotency + lock semantics.
  - `docs/runbooks/hourly-snapshot.md` (new, brief) — operator install
    instructions for the new unit.
- **Tier:** 2 — adds a recurring service. Smoke-test on the VM after
  install.
- **Goal:** hourly snapshot fires automatically without operator
  pressing `/hourly`.
- **Out of scope:** changing the snapshot content (separate hardening
  sprint); per-account snapshot variants.
- **Acceptance:**
  1. New timer + service unit lands in `deploy/`.
  2. Operator-onboarding doc updated to install the new unit.
  3. After install, `journalctl -u ict-hourly-snapshot` shows the timer
     firing hourly.
  4. Telegram receives the hourly report from `@bict_trading_bot`
     within 60 s of the timer firing.

---

## P2 — Minor: drift, redundancy, naming

### P2 cluster — comms hygiene

> **Status (2026-05-09 Janitor pass):** four of the five items closed
> at audit time, three of those because the work landed in P1-B / P1-C
> and one because the test pin already existed under a different name.
> The schema-drift envelope (item 1) is the only piece of residual code
> work; it remains carved out for an explicit follow-up.

A single Tier 1 docs / cleanup sprint covering all of:

- ⏸ **Schema-drift envelope:** add a small shared envelope (priority,
  event-id, timestamp) across `pending-pings.jsonl` events and
  `comms/requests/` artifacts so consumer code can be unified. Surfaces
  themselves stay distinct per the corrected architecture. **Status
  2026-05-09:** **carved out**, not closed. The two surfaces both have
  `priority` + `event` already, but neither carries a stable `event_id`
  or `timestamp`. Real residual work; the two emitters
  (`docs/claude/pending-pings.jsonl` writers in
  `src/bot/telegram_query_bot.py` + the comms request writer in
  `src/comms/`) are decoupled, so a fully-shared envelope means
  changing both. Promote to its own focused sprint when consumer-side
  code starts wanting unified dedup keying. Not blocking anything as
  of the Janitor pass.
- ⏸ **Comms log retention:** decide whether `comms/log.ndjson` should be
  tracked in git, rotated, or backed up. Currently gitignored at
  `comms/.gitignore`. **Status 2026-05-09:** **carved out**, not closed.
  This is a policy call (track? archive to S3? rotate locally?) that
  needs operator input — not a code smell. File a runbook / ops sprint
  if the file size starts mattering operationally; it's append-only and
  the VM has room.
- ✅ **Missing test pins:**
  - **Done.** The `comms(response):` exclusion in `notify_on_pull.py`
    is pinned by `tests/test_notify_on_pull.py::test_blocker_pings_suppresses_comms_response_commits`
    (line 653, landed in S-048). The audit doc named the test
    `test_comms_response_commits_ignored_in_generic_drain` —
    different name, same coverage.
  - **Architecturally safe; no test needed.** Trader-bot restart
    recovery for inflight comms callbacks: `CommsPoller`
    (`src/comms/comms_handler.py`) holds no in-memory state beyond
    `(store, chat_id)` and re-derives the work queue from
    `comms/requests/` on every poll. Restart = next-poll resync, no
    callback to lose. Logged here as "verified safe by architecture"
    rather than left as a missing pin.
- ✅ **Documentation tidy-up:** `comms/README.md` § "Stuck request? How
  to recover" — already replaced with the P1-B bot-side alert
  description. **Done as part of the P1-B landing PR;** the audit doc
  was filed before that PR merged.
- ⏸ **Command-name cosmetics:** `/sprintlet_status`,
  `/sprintlet_complete`, `/checkpoint`, `/ping_test` on the trader
  bot — names suggest ClaudeBot affordances, but under the corrected
  architecture they correctly stay on the trader bot. Optional rename
  pass for clarity. **Status 2026-05-09:** **carved out**, not closed.
  Mechanical (find/replace in `src/bot/telegram_query_bot.py`'s
  `CommandHandler` registrations + docstrings, ~20 touches) but low
  ROI — current names work and aren't ambiguous in operator usage.
  Promote if a broader bot-UX pass surfaces.

- **Severity:** P2.
- **Tier:** 1.
- **Bundle reason:** all small drift items that don't pay back one
  sprint each but together justify a hygiene pass.

---

## Hand-off

Per sprint-prompt § 8 — no P0 surfaced — the next sprint after S-048
follows the active-sprint default. The current active sprint per
`docs/claude/milestone-state.md` is **S-047 T6 (end-to-end live smoke +
runbook)**.

Per operator directive 2026-05-07 evening, the four P1 follow-ups in
this backlog are being executed **in this same session** in the order
**P1-A → P1-D → P1-B → P1-C**. The P2 hygiene cluster is filed for a
future Janitor / hygiene sprint.

If the session ends before the P1 queue completes, the next session
picks up wherever this one left off; this file remains the canonical
backlog reference.
