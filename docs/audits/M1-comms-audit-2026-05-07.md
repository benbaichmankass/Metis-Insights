# M1 Comms Infrastructure — Static Audit (2026-05-07)

> **Operator correction (2026-05-07, post-write):** the on-disk
> "ClaudeBot is one-way; no response path; intentional" design is
> **correct** and is the architecture the project wants. The drift this
> audit identified is **in the workplan**, not in the implementation:
> `docs/claude/workplan.md` § "Telegram bots / @claude_ict_comms_bot /
> ClaudeBot workflow" describes a 5-step two-way request/response loop
> + merge/hold buttons + recovery alerts that were never the intended
> design. The corrected reading:
>
> - **ClaudeBot** is the **one-way Claude → operator notification
>   channel** (sprint pings, blocker pings, training events, session
>   completion). No response path. Operator decisions happen through
>   GitHub (PR comments, merges) or fresh Claude sessions reading repo
>   state.
> - **The S-027 repo-driven request/response system** (the two-way
>   structured ask/answer with merge buttons, free-text capture, git
>   writeback) lives correctly on **`@bict_trading_bot`** as an
>   operator-question surface. It is **not** misplaced.
> - **`telegram-pings.md`** lines 6-10 / 195-199 are accurate and stay.
> - **S-042's verdict** is reaffirmed as correct.
>
> See the corresponding revisions in
> `docs/audits/M1-comms-audit-followups.md` § "Operator-correction
> redlines (2026-05-07)" — three P1 entries are dropped, one is
> reframed as a workplan-correction sprint, and the verdict on the
> remaining P1s is unchanged.
>
> The body of this audit is preserved as-written for audit-trail
> integrity; treat the operator correction above as the controlling
> interpretation when reading the body.

---

> **Sprint:** S-048 (M1 reopen). Tier 1 docs-only audit.
> **Rubric:** `docs/claude/workplan.md` § "Telegram bots", § "Data and
> logging architecture" / "Required logs" / "Additional logs and registries
> to add", § "Repeatable operator-triggered workflows".
> **Method:** static read of `src/bot/`, `src/comms/`, `comms/`, `scripts/`,
> `deploy/`, and `tests/`. No live bot or VM observation — that lives in the
> follow-up sprints filed in D2.
> **Scope:** comms surface only. Strategy, risk, dispatcher, order routing,
> and the dashboard are explicitly out of scope.

## Summary

**Verdict: 🔄 PARTIAL.** M1 cannot stand on the new workplan as it stands.
The implementation surface is broadly present — both bot processes are
running, the trader bot has the operator-control trio (killswitch / close-all
/ live-dry toggle) and most info menus, and S-027 already shipped a
repo-driven request / response system with schema + git writeback + comms
log. But three structural drifts and four missing features keep M1 from
matching the workplan.

The dominant drift is process-level: S-027's repo-driven request / response
infrastructure is wired into **`@bict_trading_bot`** (the AI Trader Bot) via
`install_comms_handlers` at `src/bot/telegram_query_bot.py:2955`. The
workplan assigns that surface to **`@claude_ict_comms_bot`** (ClaudeBot).
The on-disk ClaudeBot (`src/bot/claude_bridge.py`) is an Anthropic-API
chat companion plus a one-way ping drain — it does not implement any of
the workplan's five-step workflow, channel features, or recovery flows.
S-042's close-out evidence ("ClaudeBot is one-way send-only; no response
path; intentional design" — `docs/claude/telegram-pings.md:6-10` and
`195-199`) directly contradicts both the new workplan and the existing
S-027 code — i.e. the verdict S-042 wrote was wrong even at the time it
was written, because S-027 had already shipped the response path on the
trader bot.

No gap meets the P0 bar (the system can still halt, close, and toggle live
state safely; trade-execution alerts still flow). All real gaps are P1 or
P2. Per § 8 hand-off, next sprint after S-048 closes = **S-047 T3**.

---

## Bot 1 — `@bict_trading_bot` (AI Trader Bot)

Service: `deploy/ict-telegram-bot.service` → `python3 -m
src.bot.telegram_query_bot`.

### Notifications

| Workplan requirement | On-disk implementation | Status | Gap |
|---|---|---|---|
| Every entry to every log in the database | `_drain_pending_pings` at `src/bot/telegram_query_bot.py:1671-1742` drains `runtime_logs/pending_pings/`. Trader components write JSON files there via `scripts/send_ping.py` (`target="trader"` branch). `src/bot/alert_manager.py:11-36` is a thin Telegram wrapper. | ⚠️ PARTIAL | No enforced fan-out from each required log (Signals / Order Package / Risk Manager Decision / Trade / Messages / Sprint / Bug / Lessons). Coverage is whatever each writer chooses. No test pins "every log → ping". |
| Hourly snapshots | `/hourly` operator command at `src/bot/telegram_query_bot.py:2808` (`cmd_hourly` → `processor.get_hourly_report`). `scripts/send_hourly_now.py` is a manual one-shot. | ❌ MISSING (auto) | No timer fires hourly. `deploy/ict-heartbeat.timer` is a heartbeat watchdog, not a snapshot driver. `src/bot/recurring_dispatch.py` is a session-trigger logger. The workplan calls for *automatic* hourly broadcasts. |
| Errors returned by any system component | Errors raised inside the trader pipeline can call `AlertManager.send_alert` (`src/bot/alert_manager.py:18-36`). | ⚠️ PARTIAL | No central error-funnel contract. No test pins "every component error → ping". |
| Trade and account events | `pending_pings/` drainer surfaces trade-execution pings produced by `execution_diagnostics`, `liveness_watchdog`, `order_monitor` (per `src/bot/claude_bridge.py:8-12` docstring). | ✅ MATCHES | Test: `tests/test_send_ping.py`. |
| Other operational signals | Same drainer covers ad-hoc pings. | ⚠️ PARTIAL | Coverage is producer-defined, not contract-defined. |

### Operator commands

| Workplan requirement | On-disk implementation | Status | Gap |
|---|---|---|---|
| Toggle account live / dry-run | `/toggle` registered at `src/bot/telegram_query_bot.py:2973` (handler at `:1426`). `/accounts` at `:2979` opens an inline-button per-account flip flow whose callback chain is `acct_flip_ask` → `acct_flip_do` → `acct_flip_cancel` at `:2181-2232`. | ✅ MATCHES | Test: `tests/test_telegram_query_bot.py` (account-flip suite). |
| Killswitch | `/halt` at `:2960` and `/resume` at `:2961` toggle `HALT_FLAG_PATH = /tmp/trader_halt.flag` (`:44`, `:108-109`). The trader process honours the flag at startup. | ✅ MATCHES | Test: `tests/test_telegram_query_bot.py`. |
| Close all positions | `/closeall` at `:2965` invokes `_do_closeall_strategy` at `:1487` → `dl.close_all_bybit_positions_for_strategy()`. | ✅ MATCHES | Tested via `tests/test_telegram_query_bot.py`. |

### Information menus

| Workplan menu | On-disk command | Status | Gap |
|---|---|---|---|
| Operator commands | `/help` at `:2958`; `render_help_top` / `render_help_category` at `:745-820`; callback routes `help_top` / `help_cat` at `:2148-2158`. | ✅ MATCHES | — |
| Trader snapshot | `/status` at `:2962`. | ✅ MATCHES | — |
| Signals Log | `/signals` at `:2969`; stepper callbacks `signals_top` / `signals_strat` / `signals_n` at `:2235-2265`. | ✅ MATCHES | — |
| Order Package Log | `/packages` at `:2968`. | ✅ MATCHES | — |
| Trade Log | `/log` at `:2972`; `/last5` at `:2967`; `/trades` at `:2964`. | ✅ MATCHES | — |
| System health | `/health` at `:2988`. | ✅ MATCHES | — |
| Hourly update | `/hourly` at `:2982` (on-demand only — see Notifications row above). | ✅ MATCHES (menu) / ❌ MISSING (auto-fire) | The menu surface exists; the auto-broadcast does not. |
| VM stats | `/vmstats` at `:2989`. | ✅ MATCHES | — |

### Trader-bot extras (workplan-silent)

The trader bot also registers `/start`, `/set_keys`, `/balance`,
`/strategies`, `/backtest`, `/latest_backtest`, `/download_journal`,
`/price`, `/alerts`, `/reload_strats`, `/backtest_ui`, `/accounts_status`,
`/set_all_live`, `/risk_check`, `/smoke_test`, `/sprintlet_status`,
`/sprintlet_complete`, `/checkpoint`, `/ping_test`, `/webapp`, `/vm`
(`telegram_query_bot.py:2957-2992`). These extend M1 beyond the workplan
spec — the workplan describes the *minimum*, not a ceiling. Note for D2:
several of these (`/sprintlet_status`, `/sprintlet_complete`, `/checkpoint`)
look like they belong on ClaudeBot per the workplan's split, not on the
trader bot.

---

## Bot 2 — `@claude_ict_comms_bot` (ClaudeBot)

Service: `deploy/ict-claude-bridge.service` → `python3 -m
src.bot.claude_bridge`.

### What ClaudeBot actually is

`src/bot/claude_bridge.py:1-22` describes the on-disk implementation
plainly: *"Long-lived process that listens for Telegram messages from an
authorized chat ID and forwards them to Claude via the Anthropic API.
Conversation history is kept per-chat in memory… Also drains
`runtime_logs/pending_claude_pings/` so Claude session pings ride on this
bot rather than `@bict_trading_bot`."* That is, ClaudeBot is:

1. An Anthropic-API chat companion (`chat` handler at `:107-158`).
2. A one-way ping drain for `runtime_logs/pending_claude_pings/`
   (`_drain_pending_claude_pings` at `:289-351`, scheduled every
   `CLAUDE_PING_DRAIN_INTERVAL_S = 5 s` at `:392-398`).
3. Three session-trigger commands (`/audit`, `/improve_strategy`,
   `/train_model`) at `:176-226` that log to
   `runtime_logs/recurring_sessions.jsonl` and reply with starter prompts
   the operator pastes into a fresh Claude Code session.
4. `/roadmap`, `/schedules`, `/start`, `/reset`, `/model` housekeeping
   commands.

Critically, **it does not call `install_comms_handlers`** and is not aware
of `comms/requests/` at all. The repo-driven five-step workflow is wired
into the trader bot — see § Cross-cutting concerns below.

### Five-step workflow (workplan §)

| Step | On-disk implementation | Status | Gap |
|---|---|---|---|
| 1. Claude writes a structured pending-request artifact | `comms/schema/{request,response}.schema.json` define the contract; `src/comms/models.py` and `src/comms/store.py` validate and persist; CLI helper `scripts/comms_ask.py` lets Claude author one. | ✅ MATCHES | Tests: `tests/test_s027_comms_models.py`, `tests/test_s027_comms_store.py`, `tests/test_s027_comms_ask_cli.py`. |
| 2. Bot detects it and sends Telegram message | `CommsPoller` at `src/bot/comms_handler.py:145-256` (`poll_once` + `_deliver`). Renders inline keyboards from question schemas, transitions `pending → sent`. | ⚠️ PARTIAL | Installed in **the trader bot** (`telegram_query_bot.py:2955` calls `install_comms_handlers`) — not in `claude_bridge.py`. Workplan says ClaudeBot owns this surface. |
| 3. Operator responds in Telegram | `comms_callback_handler` at `:288-326` parses `comms:<reqid>:<qid>:<choiceid>` callback data; `comms_text_handler` at `:328-402` handles "Other" free-text. | ⚠️ PARTIAL | Same wrong-bot drift as Step 2. |
| 4. Response written back to repo | `apply_answer` at `:408-477` merges answer into the JSON artifact, transitions state per `src/comms/state.py`, and `GitPusher` at `:487-538` commits with `comms(response):` prefix and pushes. | ✅ MATCHES | Test: `tests/test_s027_comms_handler.py`. |
| 5. Claude reads on next sync cycle | Out of repo by design — the next Claude session reads `comms/requests/` and `comms/archive/` during its read order. | ✅ MATCHES | Not a code path in this repo. |

### Channel features (workplan §)

| Feature | On-disk implementation | Status | Gap |
|---|---|---|---|
| Merge-review buttons | None. `request.schema.json` enumerates `input_type ∈ {choice, multi_choice, free_text, yes_no}` (line 88-89 of the schema). No `merge_review` / `approval` type, no callback path that resolves to "Merge" or "Hold" actions on a PR, no `gh pr merge` invocation in any bot. | ❌ MISSING | Workplan § "Tier 2 — Claude must ping the operator with a merge / hold decision" + § "Merge review flow" are unimplemented. Today: Claude pings via `pending-pings.jsonl`, operator merges via GitHub web UI. |
| PM sprint-start pings | `pending-pings.jsonl` event `sprint-start`, drained by `scripts/notify_on_pull.py:259-310`, fan-out via `scripts/send_ping.py` with `target="claude"` to `runtime_logs/pending_claude_pings/`. | ⚠️ PARTIAL | One-way only — no operator-response path. Lives outside the comms-request system, in a parallel surface. See "Two parallel comms surfaces" in Cross-cutting. |
| Sprint-completion updates | Same path as sprint-start (`event: "sprint-complete"`). | ⚠️ PARTIAL | Same one-way drift. |
| Required-action prompts | None as a first-class workflow. The S-027 comms-request system *could* host this if Claude wrote a `required_action` request, but no such writer exists in `src/` and no schema-level distinction is drawn. | ❌ MISSING | Workplan calls out "required-user-action prompts" as a distinct channel feature. |
| Recovery alerts for stuck or stale requests | `CommsPoller.poll_once` at `:195-224` calls `is_expired()` on each pending request and silently archives it (transitions to `EXPIRED`, moves to `comms/archive/`). | ❌ MISSING | No Telegram message is sent to the operator about the expired request. `comms/README.md` itself says: *"The recovery commands live in the bot itself (PR 2). Until then, manually edit the file's delivery.send_attempts to 0 and status back to pending — the next bot poll resends it."* The README acknowledges the recovery flow is unimplemented. |

---

## Required logs

| Workplan log | On-disk implementation | Status | Gap |
|---|---|---|---|
| **Comms log** (workplan § "Additional logs and registries to add") | `src/comms/log.py:36-69` writes `comms/log.ndjson`. Events logged: `request_created`, `request_sent`, `answer_received`, `request_answered`, `request_acknowledged`, `request_expired`, `request_cancelled`, `error`. | ✅ MATCHES | Test: `tests/test_s027_comms_store.py`. |
| **Messages Log** (workplan § "Required logs") | No canonical messages-log file. Outbound notifications flow through `pending_pings/` (drain → send → unlink) and through `pending_claude_pings/`; neither retains a structured record. `src/comms/log.ndjson` only records comms-request lifecycle events, not arbitrary outbound messages. | ⚠️ AMBIGUOUS | Workplan says "all messages sent to the operator" with timestamps, bot identity, type. The current architecture treats `pending_pings/` as ephemeral. Operator decision: should the messages log subsume `pending_pings/`, or should it be a parallel append-only journal? Flagging as ambiguous, not missing. |

---

## Operator-triggered workflows

| Workplan workflow | On-disk implementation | Status | Gap |
|---|---|---|---|
| `new-session <sprint_id>` | `/audit`, `/improve_strategy`, `/train_model` (claude_bridge.py:176-226) trigger fixed *recurring* sessions (hardening, strategy, model). They do not initialize a sprint-id-targeted context. There is no `/new-session` handler in either bot. | ❌ MISSING | Workplan calls for sprint-id parameterization. The closest existing surface is `/sprintlet_status` / `/sprintlet_complete` / `/checkpoint` on the *trader* bot, which are status reads, not session bootstraps. |
| `test <strategy_name>` (writes a structured test-request artifact) | None. No `/test` handler exists. `/backtest` (telegram_query_bot.py:2970) runs a *local* backtest in the bot process — not a comms-request artifact for Claude to pick up. | ❌ MISSING | M5 is the deliverable, but the bot-side dispatch surface is the M1 piece. |
| Merge-review with **Merge** / **Hold** buttons | None — see Channel features above. | ❌ MISSING | Cross-listed: this is the same gap as the channel feature. |
| Stuck-request recovery flow | None — see Channel features above. | ❌ MISSING | Cross-listed. |

---

## Deployment / runtime

| Concern | On-disk evidence | Notes |
|---|---|---|
| `ict-claude-bridge.service` health | Unit at `deploy/ict-claude-bridge.service`. `Restart=always`, `RestartSec=15`, `MemoryMax=200M`, `MemoryHigh=150M`. Logs append-only to `runtime_logs/claude_bridge*.log`. | OK on paper; live state not verified in this static audit. |
| `ict-telegram-bot.service` health | Unit at `deploy/ict-telegram-bot.service`. `Restart=always`, `RestartSec=15`. Journal logging. `After=…ict-trader-live.service`. | OK on paper. |
| `ict-git-sync.timer` cadence | 5-min default per `comms/README.md`. | This is the latency floor for repo-driven comms. |
| Auto-sync from main | The trader VM pulls `main` every 5 min, restarts services on change. The comms request poller uses `poll_interval` independent of git-sync. | Two clocks; document drift OK so far. |
| Restart behaviour for the comms poller | `CommsPoller.start` is registered as `Application.post_init` (`comms_handler.py:600-614`). On bot restart the poller restarts. Pending requests on disk survive restarts. | Inflight Telegram callbacks may be lost — no test pins recovery from a restart mid-callback. |
| Secret rotation | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CLAUDE_BOT_TOKEN`, `TELEGRAM_CHAT_ID` loaded from `/home/ubuntu/ict-trading-bot/.env` (per both unit files). | Out of scope for static audit; flag for the deployment / change log workplan item. |

---

## Test coverage

| Workplan requirement | Test pinning |
|---|---|
| `/halt`, `/closeall`, `/toggle` operator commands | ✅ `tests/test_telegram_query_bot.py` (existence check + handler args). |
| Information menus on the trader bot | ✅ `tests/test_s007_bot_commands.py`, `tests/test_s008_telegram_rewired.py`. |
| Trade-execution pings via `pending_pings/` drain | ✅ `tests/test_send_ping.py`. |
| Comms request lifecycle (state machine, store, schema) | ✅ `tests/test_s027_comms_state.py`, `tests/test_s027_comms_store.py`, `tests/test_s027_comms_models.py`. |
| Comms callback parsing + git writeback | ✅ `tests/test_s027_comms_handler.py`. |
| `comms_ask` CLI helper | ✅ `tests/test_s027_comms_ask_cli.py`. |
| Hourly snapshot auto-fire | ❌ NO TEST (because no implementation). |
| Merge / Hold inline buttons | ❌ NO TEST. |
| Stuck-request recovery alert | ❌ NO TEST. |
| `/new-session`, `/test` operator commands | ❌ NO TEST. |
| ClaudeBot end-to-end of the five-step workflow | ❌ NO TEST — the test that exists pins the trader-bot wiring instead. |
| S-042 verification of "ClaudeBot one-way" claim | The 6 tests added in `tests/test_notify_on_pull.py` cover the JSONL drain, not the response path. They are correct as far as they go, but they pinned a model the workplan disagrees with. |

---

## Cross-cutting concerns

### Wrong-bot drift (the dominant structural gap)

The workplan assigns the repo-driven request / response surface to
`@claude_ict_comms_bot`. The on-disk install puts it on
`@bict_trading_bot` via `install_comms_handlers` at
`src/bot/telegram_query_bot.py:2955`. Practical consequences:

- If `ict-telegram-bot.service` is down (e.g. mid-deploy or a crash loop),
  *no* operator response can be received, even though
  `ict-claude-bridge.service` may still be up.
- The `@claude_ict_comms_bot` handle, which the workplan describes as
  the comms channel, can only send free-text Anthropic chat replies — it
  has no comms-request awareness.
- Operators who tap a comms-request inline button do so in the trader
  bot's chat, mixing trade-control affordances and Claude-comms
  affordances on the same UI.

This is P1 because the system limps along — comms requests still work, just
on the wrong process. Filed in D2 as a relocation sprint.

### S-042 verdict drift

`docs/sprint-summaries/sprint-042-summary.md` line 30 documents
"VERIFIED WORKING" with the verdict that "ClaudeBot is send-only; no
response path exists." `docs/claude/telegram-pings.md:6-10` and
`195-199` codify the same claim as intentional design. This is wrong on
two counts:

1. The new workplan explicitly mandates a five-step *two-way* workflow
   on this bot.
2. S-027 (which had already shipped before S-042 closed) implements the
   two-way response path in `src/comms/`. S-042 didn't audit S-027 at
   all — its evidence table (sprint-042-summary.md lines 24-35) only
   touches the `pending-pings.jsonl` one-way ping queue.

The workplan's "Verify-before-trusting-done" rule is exactly why this
audit reopened M1. Filed in D2 as a documentation-correction sprint.

### Two parallel comms surfaces

The repo currently runs two distinct comms channels in parallel:

| Surface | Purpose today | Direction | File of record |
|---|---|---|---|
| `docs/claude/pending-pings.jsonl` + `runtime_logs/pending_claude_pings/` | Sprint-start / sprint-complete / blocker-PM / merge-review *announcements* | One-way (Claude → operator) | `pending-pings.jsonl` (append-only) |
| `comms/requests/` + `comms/archive/` + `comms/log.ndjson` | Structured questions + operator answers | Two-way (Claude ↔ operator) | per-request JSON files + ndjson event log |

The workplan envisions one canonical channel ("the bot detects it and
sends the message in Telegram… the response is written back into the
repo in structured form"). The two surfaces have grown independently
(S-019 → pending-pings; S-027 → comms-request) and don't share a state
machine, dedup logic, or schema. Practical consequences:

- `pending-pings.jsonl` blockers don't get merge-review buttons; the
  comms-request system that *has* the button infrastructure isn't
  consulted.
- The dedup story differs: `pending-pings.jsonl` uses hash-based dedup
  in `runtime_logs/pending_pings_delivered.txt`; `comms/requests/`
  relies on filesystem state + schema.
- The "messages log" workplan requirement is satisfied by neither
  surface cleanly.

Filed in D2 as a unification sprint.

### Pending-request artifact schema drift

`comms/schema/request.schema.json` is the canonical schema. `docs/claude/
pending-pings.jsonl` uses an ad-hoc, free-form JSONL schema (varies per
event — see sample lines in `pending-pings.jsonl`). They are not
versioned or aligned. If the unification sprint above lands, this
drift goes away.

### Bot identity confusion in command names

The trader bot registers `/sprintlet_status`, `/sprintlet_complete`,
`/checkpoint`, `/ping_test` (telegram_query_bot.py:2985-2990) — these are
sprint-lifecycle commands that the workplan would put on ClaudeBot. The
operator currently has to remember two bot UIs and which command lives
where. P2.

### `comms(response):` commit prefix vs notification routing

`scripts/notify_on_pull.py:165-207` recognises commit-subject prefixes for
ping fanout. The `comms(response):` prefix is silently ignored
(`docs/claude/telegram-pings.md:211`). That's correct: response writebacks
shouldn't fire pings. But there's no test that pins this exclusion in
`scripts/notify_on_pull.py` (the existing test at
`tests/test_notify_on_pull.py::test_blocker_pings_suppresses_comms_response_commits`
covers the blocker side, not the generic ignore). P2.

### Observability gap on the comms log

`comms/log.ndjson` is gitignored (`comms/.gitignore`). The on-disk file is
the only record. If the VM disk is wiped without backup, the comms-state
audit trail is lost. The trader's `signal_audit.jsonl` has the same issue
but is treated as a feature there. P2 — flag for the audit / log-retention
sprint, not blocking.

---

## Severity classification

Per § 4.D1 rubric:

- **P0** — none.
- **P1** — the four named structural / functional drifts (wrong-bot
  install; merge-review absent; recovery alert absent; auto-hourly
  absent; `/new-session` + `/test` absent; two parallel surfaces;
  S-042 doc drift).
- **P2** — schema drift between pending-pings and comms-request;
  trader-bot extras that should live on ClaudeBot; missing test pins;
  comms log retention; `comms(response):` exclusion not pinned by test.

The detailed prioritized backlog is in
`docs/audits/M1-comms-audit-followups.md`.

---

## Hand-off

Per sprint prompt § 8 — no P0 surfaced — the next sprint is
**S-047 T3** (`feat(exec): route spot-margin orders via isLeverage=1`
+ `feat(coordinator): direction-aware balance for spot-margin
accounts`). Plan: `docs/sprint-plans/S-047-bybit2-spot-margin.md` § T3.
Tier 2/3 — will pause at the operator-merge gate.

The comms-followup queue (D2) sits behind S-047 T3 in priority; M5
sequencing will incorporate the highest-priority comms followup once
S-047 closes.
