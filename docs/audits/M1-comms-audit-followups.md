# M1 Comms Audit — Follow-up Sprint Backlog (2026-05-07)

> **Operator correction (2026-05-07, post-write) — see § "Operator-
> correction redlines" at the end of this file.** Three P1 entries
> below are dropped because the on-disk "ClaudeBot one-way" design is
> the correct architecture; the drift is in the workplan, not the
> implementation.

> **Source:** `docs/audits/M1-comms-audit-2026-05-07.md` (D1 of S-048).
> **Status:** prioritized backlog. Sprint numbers (`S-NNN`) are placeholders
> per workplan § "Sprint and checkpoint numbering" — assign at the time the
> prompt is filed, not now. The next available number after S-048 is S-049.
> **Order:** P1 entries first, in execution order; then P2 cluster.

Each entry is a sketch — enough for the next session to expand into a full
sprint prompt under `docs/sprints/sprint-NNN-prompt.md` when the time comes.

---

## P1 — Significant gaps (workplan-mandated, system limps along)

### S-NNN — Relocate the comms request/response system to `@claude_ict_comms_bot`

- **Severity:** P1
- **Workplan ref:** § "Telegram bots" / `@claude_ict_comms_bot` /
  "ClaudeBot workflow"
- **Files in scope:**
  - `src/bot/claude_bridge.py` (add `install_comms_handlers` call)
  - `src/bot/telegram_query_bot.py` (remove the `install_comms_handlers`
    call at line 2955; remove the `comms_handler` import at line 20)
  - `src/bot/comms_handler.py` (no logic change expected; verify it does
    not depend on trader-bot symbols)
  - `deploy/ict-claude-bridge.service` (verify env vars cover the comms
    handler's needs — `TELEGRAM_CHAT_ID` already present)
  - `tests/test_s027_comms_handler.py` (update import paths if any)
  - `docs/claude/comms-architecture.md` (update which bot owns the
    surface)
- **Tier:** 2 (bot-process behaviour change, not strategy / order logic;
  affects which Telegram chat receives operator-action requests). Smoke-
  test on the VM after merge.
- **Goal:** make the workplan-described split real — ClaudeBot owns the
  five-step request/response workflow; trader bot is for trade-control
  surfaces and notifications.
- **Out of scope:** the merge-review schema (separate sprint below);
  schema unification (separate sprint below); `/new-session` and `/test`
  commands (separate sprints).
- **Acceptance:**
  1. `claude_bridge.py` calls `install_comms_handlers(application,
     repo_root=REPO_ROOT)`.
  2. `telegram_query_bot.py` no longer registers the comms handlers.
  3. A smoke test artifact in `comms/requests/` is delivered via
     `@claude_ict_comms_bot`, not `@bict_trading_bot`.
  4. All `tests/test_s027_*` still pass.
  5. `docs/claude/comms-architecture.md` reflects the new ownership.

---

### S-NNN — Implement merge-review (Merge / Hold) inline buttons in the comms request system

- **Severity:** P1 (workplan-mandated for Tier 2 PRs; today operator merges
  via GitHub web UI which works, but the workplan-prescribed surface
  doesn't exist)
- **Workplan ref:** § "Decision and merge authority" / "Tier 2" + § "Merge
  review flow"
- **Files in scope:**
  - `comms/schema/request.schema.json` (add `merge_review` to `input_type`
    enum + supporting fields: `pr_url`, `risk_summary`, `validation_done`)
  - `src/comms/models.py` (model + validation for the new type)
  - `src/comms/state.py` (allowed transitions if any new states needed)
  - `src/bot/comms_handler.py` (callback handler emits
    `gh pr merge --squash <pr_url>` on Merge, posts a "Hold" comment +
    transitions request to `cancelled` on Hold)
  - `scripts/comms_ask.py` (CLI helper for Claude to author a merge-review
    request)
  - `tests/test_s027_comms_handler.py` (new test class for merge-review
    callbacks)
  - `tests/test_s027_comms_models.py` (schema validation tests)
- **Tier:** 3 — this gives Claude the ability to merge PRs from a Telegram
  callback. Operator approval required before this surface goes live.
- **Goal:** turn `pending-pings.jsonl`-blocker workflow into a
  comms-request workflow with structured Merge / Hold callbacks.
- **Out of scope:** PR-author-side automation (Claude already opens draft
  PRs); migrating *existing* `pending-pings.jsonl` blocker entries
  (one-time backfill is a P2).
- **Acceptance:**
  1. A `merge_review` comms request renders Merge / Hold buttons in
     Telegram.
  2. Tap-Merge results in a `gh pr merge --squash` invocation against the
     stored `pr_url`; tap-Hold posts a comment and transitions the
     request to `cancelled` with an annotation.
  3. Tests pin both paths.
  4. Operator approval recorded before the change goes live (Tier 3
     gate).

---

### S-NNN — Stuck-request recovery alerts

- **Severity:** P1
- **Workplan ref:** § "ClaudeBot workflow" / "Recovery alerts for stuck or
  stale requests" + § "Repeatable operator-triggered workflows" / "Stuck
  request recovery"
- **Files in scope:**
  - `src/bot/comms_handler.py` (extend `CommsPoller.poll_once` to send a
    Telegram alert before transitioning to `EXPIRED`; add a separate
    "stuck (sent but no response after threshold)" detection path that
    fires before TTL expiry)
  - `comms/schema/request.schema.json` (add `stuck_alert_threshold`
    optional field)
  - `tests/test_s027_comms_handler.py` (pin both alert paths)
  - `comms/README.md` § "Stuck request? How to recover" (replace the
    "manually edit the file" workaround with the new bot commands)
- **Tier:** 1 — read-only detection + outbound notification, no live
  trading impact.
- **Goal:** never silently expire a comms request. Operator sees a
  Telegram alert with the request id, age, and a one-tap "extend" /
  "cancel" path.
- **Out of scope:** auto-resend after extension (next sprint);
  pending-pings.jsonl recovery (separate, since it's a different surface).
- **Acceptance:**
  1. A request that stays in `sent` past its `stuck_alert_threshold`
     fires a Telegram alert.
  2. A request that hits `expires_at` without an answer fires a final
     Telegram alert and transitions to `EXPIRED`.
  3. Tests pin both transitions.
  4. README updated.

---

### S-NNN — Auto-hourly snapshot broadcast

- **Severity:** P1
- **Workplan ref:** § "Telegram bots" / `@bict_trading_bot` /
  "Notifications" / "Hourly snapshots"
- **Files in scope:**
  - `deploy/ict-hourly-snapshot.timer` (new) + `.service` (new)
  - `scripts/send_hourly_now.py` (already exists; verify it is
    timer-safe — idempotent, locks against parallel runs)
  - `src/runtime/hourly_report.py` (already exists; verify the
    dedup marker behaviour the timer needs)
  - `tests/test_send_hourly_report.py` (new — assert the timer runs
    on a defined cadence; assert dedup works)
- **Tier:** 2 — adds a recurring service. Smoke-test on the VM.
- **Goal:** hourly snapshot fires automatically without operator
  pressing `/hourly`.
- **Out of scope:** changing the snapshot content (separate hardening
  sprint); per-account snapshots (already covered by the existing
  hourly report).
- **Acceptance:**
  1. New timer + service unit lands in `deploy/`.
  2. Operator-onboarding doc updated to install the new unit.
  3. After install, `journalctl -u ict-hourly-snapshot` shows the timer
     firing hourly.
  4. Telegram receives the hourly report from `@bict_trading_bot`
     within 60 s of the timer firing.

---

### S-NNN — Operator commands `/new-session <sprint_id>` and `/test <strategy>`

- **Severity:** P1 (two related missing commands; bundling because they
  share the comms-request artifact-writing pattern)
- **Workplan ref:** § "Repeatable operator-triggered workflows" / "New
  session command" + "Strategy test command"
- **Files in scope:**
  - `src/bot/claude_bridge.py` (assuming relocation sprint above has
    landed; otherwise `src/bot/telegram_query_bot.py`)
  - `src/comms/templates/` (new — template artifacts for `new-session`
    and `test` requests)
  - `scripts/comms_ask.py` (helper to construct the two artifact types)
  - `tests/test_s027_comms_handler.py` (or new file)
  - `docs/claude/comms-architecture.md` (document the two commands)
- **Tier:** 2 — adds two new operator-facing commands; no live-trading
  change but smoke-test on the VM.
- **Goal:**
  - `/new-session S-099` writes a `comms/requests/REQ-…-new-session.json`
    artifact with the sprint id and acks in Telegram. Claude reads it on
    next sync.
  - `/test vwap` writes `comms/requests/REQ-…-test-strategy.json` with
    `strategy: "vwap"` and an empty results section. Claude (or M5's
    backtest workflow) picks it up, writes results into the request
    response, and the operator sees the answer back in Telegram.
- **Out of scope:** the actual M5 backtest workflow that consumes the
  `test` request (separate M5 sprint); session-bootstrap behaviour
  on Claude's side (lives in CLAUDE.md / sprint prompts, not here).
- **Acceptance:**
  1. Both commands registered on ClaudeBot.
  2. Each writes a well-formed `comms/requests/REQ-…json` artifact and
     commits.
  3. Telegram acknowledgement message includes the request id.
  4. Tests pin artifact creation + commit-subject prefix.

---

### S-NNN — Unify `pending-pings.jsonl` and `comms/requests/` into one canonical channel

- **Severity:** P1 (architectural — affects every comms surface)
- **Workplan ref:** § "Telegram bots" / `@claude_ict_comms_bot` / workflow
  + § "Required logs" / Messages Log
- **Files in scope:**
  - `docs/claude/pending-pings.jsonl` (deprecation; one-shot migration to
    `comms/requests/` artifacts)
  - `docs/claude/pending-pings.jsonl.template` (deprecate)
  - `scripts/send_ping.py`, `scripts/notify_on_pull.py`,
    `scripts/notify_session.py` (rewrite to emit comms-request artifacts
    or retire entirely)
  - `runtime_logs/pending_pings/` and `runtime_logs/pending_claude_pings/`
    (deprecation; producers retargeted to comms-request artifacts)
  - `src/bot/telegram_query_bot.py` lines 1671-1742 (`_drain_pending_pings`
    — retire or repurpose)
  - `src/bot/claude_bridge.py` lines 289-351
    (`_drain_pending_claude_pings` — retire or repurpose)
  - `docs/claude/telegram-pings.md` (rewrite around the unified channel;
    correct the S-042 "one-way intentional" claim)
- **Tier:** 3 — large surface touched, behaviour change visible to the
  operator on every comms surface. Operator review required.
- **Goal:** one canonical comms surface. `comms/requests/` for two-way
  structured exchanges; one append-only Messages Log
  (`comms/messages.ndjson` or similar) for one-way notifications.
  No more parallel tracks.
- **Out of scope:** the merge-review schema (lands earlier, in its own
  sprint); the messages-log workplan ambiguity decision (separate
  operator-decision artifact).
- **Acceptance:**
  1. New canonical Messages Log writer in `src/comms/messages_log.py`.
  2. All current `send_ping` / `pending-pings.jsonl` callers retargeted
     or retired.
  3. Both `_drain_pending_*pings` loops removed from the bots; comms
     handler covers the surface.
  4. Migration plan + smoke test on the VM.
  5. `docs/claude/telegram-pings.md` rewritten and the "one-way
     intentional" claim removed.

---

### S-NNN — Correct S-042 documentation drift; align telegram-pings.md with workplan

- **Severity:** P1 (docs-only; small but the wrong claim is canonical
  today)
- **Workplan ref:** § "Verify-before-trusting-done"
- **Files in scope:**
  - `docs/claude/telegram-pings.md` (lines 6-10 and 195-199 — remove the
    "one-way intentional" claim; replace with the workplan's two-way
    workflow description)
  - `docs/sprint-summaries/sprint-042-summary.md` (add an addendum noting
    this audit's correction; do *not* edit the body — see workplan §
    "Sprint and checkpoint numbering" — checkpoint IDs and summaries are
    fixed once committed)
  - `docs/claude/comms-architecture.md` (cross-reference the correction)
- **Tier:** 1 — docs only.
- **Goal:** the canonical comms doc no longer contradicts the workplan or
  the S-027 implementation that's been on disk all along.
- **Out of scope:** any code change.
- **Acceptance:**
  1. The "one-way; no response path; intentional" claim is removed
     from `telegram-pings.md`.
  2. S-042 summary has an addendum block citing this audit.
  3. CI green.

---

## P2 — Minor: drift, redundancy, naming

### S-NNN — P2 cluster: comms hygiene

A single Tier 1 docs / cleanup sprint covering all of:

- **Trader-bot extras that should live on ClaudeBot:**
  `/sprintlet_status`, `/sprintlet_complete`, `/checkpoint`, `/ping_test`
  (`telegram_query_bot.py:2985-2990`) — move to ClaudeBot once the
  relocation sprint above lands.
- **Schema drift:** version `pending-pings.jsonl` events under the
  comms-request schema, or formally retire the JSONL surface (depends on
  unification sprint).
- **Comms log retention:** decide whether `comms/log.ndjson` should be
  tracked in git, rotated, or backed up. Currently gitignored at
  `comms/.gitignore`.
- **Missing test pins:**
  - Pin `comms(response):` exclusion in `notify_on_pull.py`
    (no `test_comms_response_commits_ignored_in_generic_drain`).
  - Pin trader-bot restart recovery for inflight comms callbacks.
- **Documentation tidy-up:** `comms/README.md` § "Stuck request? How to
  recover" instructs operators to manually edit JSON files — remove
  once the recovery sprint lands.

- **Severity:** P2
- **Tier:** 1
- **Bundle reason:** these are all small drift items that don't pay back
  one sprint each but together justify a hygiene pass.

---

## Hand-off (per § 8 of `sprint-048-prompt.md`)

No P0 surfaced. **Default next sprint = S-047 T3.** The follow-ups above
get filed and prioritized into the queue *after* S-047 T3 closes (M5
inherits the highest-priority comms followup that gates strategy testing
flow — likely the "S-NNN — Operator commands `/new-session` and `/test`"
sprint, since `/test <strategy>` is the M5 dispatch surface).

---

## Operator-correction redlines (2026-05-07)

The operator confirmed post-audit that the on-disk "ClaudeBot one-way;
no response path; intentional" architecture is the correct design and
should be reflected in the workplan. The audit's framing of this as a
*drift in the implementation* is wrong; the drift is in the
**workplan's § "ClaudeBot workflow"** spec, which mis-describes the
intended architecture.

### Dropped (no longer real follow-ups)

The following P1 entries above are **dropped** as non-issues under the
corrected interpretation:

- ❌ **"Relocate the comms request/response system to
  `@claude_ict_comms_bot`"** — the S-027 system lives correctly on
  `@bict_trading_bot` as an operator-question surface for the trader
  bot. Trader bot continues to host both the trade-control surface and
  the structured ask/answer channel. ClaudeBot is intentionally narrow.
- ❌ **"Implement merge-review (Merge / Hold) inline buttons in the
  comms request system"** — operator merge decisions happen on GitHub
  (PR comments + web-UI merge), not via a Telegram callback that
  invokes `gh pr merge`. Tier 2 ping is informational only; the
  operator clicks merge on the PR itself. Adding bot-side merge
  authority would expand the live surface unnecessarily.
- ❌ **"Correct S-042 documentation drift"** — S-042's verdict is
  reaffirmed as correct. `telegram-pings.md` is accurate. No
  correction is needed.

### Reframed

- ✏️ **"Unify `pending-pings.jsonl` and `comms/requests/`"** is
  **dropped as a unification sprint**. The two surfaces are
  intentionally distinct under the corrected architecture:
  - `pending-pings.jsonl` → ClaudeBot one-way notification channel
    (Claude → operator pings).
  - `comms/requests/` → Trader-bot operator-question channel
    (Claude ↔ operator structured ask/answer for things like
    "which strategy do you want to test?", *not* merge decisions).
  - The Messages Log workplan ambiguity stays (still flagged in the
    "Required logs" row of D1) but is solved by adding an append-only
    log to one or both surfaces, not by merging them.

### Reframed: workplan correction sprint (replaces S-042 doc-drift item)

#### S-NNN — Workplan correction: ClaudeBot is intentionally one-way

- **Severity:** P1 (workplan doc — the canonical authority of the
  project; correcting it is more valuable than its size suggests)
- **Workplan ref:** `docs/claude/workplan.md` § "Telegram bots / @claude_ict_comms_bot / ClaudeBot workflow"
- **Files in scope:**
  - `docs/claude/workplan.md` — replace the 5-step two-way workflow
    description + "channel must support merge-review buttons / required-
    user-action prompts / recovery alerts" with: ClaudeBot is a one-way
    Claude → operator notification channel; operator decisions happen
    through GitHub (PR comments, merges) or fresh sessions; the
    structured ask/answer comms surface lives on `@bict_trading_bot`
    via S-027.
  - `docs/audits/M1-comms-audit-2026-05-07.md` — annotate that the
    body of the audit is preserved as-written; this redline section
    governs the corrected reading. (Already done in the audit's header
    — operator correction note.)
  - `docs/sprint-summaries/sprint-042-summary.md` — no edit (S-042
    verdict reaffirmed). Optional addendum noting the audit
    reaffirmation.
- **Tier:** 1 (docs only).
- **Goal:** the canonical workplan no longer describes an architecture
  the project doesn't intend to build.
- **Out of scope:** any code change; the comms-handler stays on the
  trader bot as it is.
- **Acceptance:**
  1. Workplan § "ClaudeBot workflow" describes the one-way channel.
  2. The "channel must support merge-review buttons" / required-action
     prompts / recovery alerts language is removed or relocated to
     describe the trader-bot comms-handler surface accurately.
  3. CI green.

### Unchanged (still real P1 gaps)

- ✅ **"Stuck-request recovery alerts"** — still real; applies to the
  trader-bot comms-request surface. Stuck `comms/requests/` artifacts
  should fire a Telegram alert before silent expiry. (Not a ClaudeBot
  feature — trader-bot.)
- ✅ **"Auto-hourly snapshot broadcast"** — still real; workplan-
  mandated for the trader bot. Today `/hourly` is on-demand only.
- ✅ **"Operator commands `/new-session <sprint_id>` and
  `/test <strategy>`"** — still real; both go on the trader bot's
  comms-handler surface (writes a `comms/requests/` artifact for
  Claude to pick up on next sync). Not on ClaudeBot.

### P2 cluster — unchanged

The hygiene cluster from the original D2 stays as-written, with one
edit: the line *"Trader-bot extras that should live on ClaudeBot:
`/sprintlet_status`, `/sprintlet_complete`, `/checkpoint`, `/ping_test`"*
is **dropped** — under the corrected architecture, those commands stay
on the trader bot.

### Net result

Original P1 count: 7. Operator-correction-adjusted P1 count: **4** (3
dropped, 1 reframed as workplan correction). P2 cluster: unchanged
modulo one line.
