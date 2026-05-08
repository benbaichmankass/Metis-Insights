# M1 Comms Infrastructure — Static Audit (2026-05-07, fresh)

> **Sprint:** S-048 (M1 reopen). Tier 1 docs-only audit.
> **Supersedes:** `docs/audits/M1-comms-audit-2026-05-07.md` (PR #463).
> The earlier audit was verified by the operator post-write; this version
> bakes those corrections directly into the body (no redlines section).
> **Rubric:** `docs/claude/workplan.md` § "Telegram bots", § "Data and logging
> architecture" / "Required logs" / "Additional logs and registries to add",
> § "Repeatable operator-triggered workflows".
> **Method:** static read of `src/bot/`, `src/comms/`, `comms/`, `scripts/`,
> `deploy/`, and `tests/`. No live bot or VM observation — live behaviour
> verification lives in the follow-up sprints filed in
> `docs/audits/M1-comms-audit-followups.md`.
> **Scope:** comms surface only. Strategy, risk, dispatcher, order routing,
> and the dashboard are explicitly out of scope.

---

## Reading guide — the corrected architecture

The workplan as written (§ "Telegram bots / @claude_ict_comms_bot /
ClaudeBot workflow") describes a 5-step two-way request/response loop on
ClaudeBot, with merge/hold buttons, required-action prompts, and recovery
alerts. **That description is wrong.** The intended architecture, confirmed
by the operator on 2026-05-07 and corroborated by the on-disk
implementation, is:

- **`@bict_trading_bot`** (AI Trader Bot) hosts:
  - The trade-control surface (killswitch, close-all, live/dry-run toggle).
  - All info menus (status, signals, packages, trades, health, vmstats, hourly).
  - **The S-027 repo-driven request/response system** — structured ask/answer
    artifacts under `comms/requests/` with inline-keyboard menus, free-text
    capture, git writeback. This is for operator-question flows like "which
    strategy do you want to test?", **not** for merge decisions.
  - All trade-execution alerts (drained from `runtime_logs/pending_pings/`).

- **`@claude_ict_comms_bot`** (ClaudeBot) is a **deliberately one-way
  Claude → operator notification channel**:
  - Sprint pings, checkpoint commits, blocker pings, training-stage events.
  - Plus three convenience commands: `/audit`, `/improve_strategy`,
    `/train_model` — which write to `runtime_logs/recurring_sessions.jsonl`
    and reply with starter prompts for the operator to paste into a fresh
    Claude Code session.
  - **No response path.** Operator decisions flow through GitHub (PR
    comments, merges) or by opening a new Claude session that reads repo
    state.

- **Merge decisions** happen on GitHub (PR review + web-UI merge), **not**
  via Telegram callbacks. Adding a `gh pr merge` callback to a bot would
  expand the live surface unnecessarily.

This audit grades the implementation against the **corrected** architecture,
not the workplan's words verbatim. Where the workplan disagrees with the
correct architecture, the verdict is "workplan needs correcting" — not
"implementation is wrong." Those workplan-correction tasks land in
`docs/audits/M1-comms-audit-followups.md`.

---

## Summary

**Verdict: 🔄 PARTIAL.** No P0 surfaced — the system can halt, close, and
toggle live state safely; trade-execution alerts still flow; the
two-bot split is operationally healthy and matches the corrected
architecture.

Four P1 gaps remain against the corrected architecture:

1. **Workplan correction** — `docs/claude/workplan.md` § "ClaudeBot
   workflow" describes the wrong design and must be rewritten to match
   the one-way reality. (Tier 1 docs.)
2. **Auto-hourly snapshot broadcast missing** — the workplan mandates
   hourly snapshots; only the `/hourly` on-demand command exists today.
   (Tier 2 — new systemd timer.)
3. **Stuck-request recovery alerts missing** — `comms/requests/`
   artifacts that hit TTL silently transition to `EXPIRED` and archive
   without notifying the operator. (Tier 1 — read-only detection +
   outbound notification.)
4. **`/new-session <sprint_id>` and `/test <strategy>` commands missing**
   — both are workplan-mandated repeatable workflows. (Tier 2 — adds
   two new operator-facing commands; smoke-test on the VM.)

A small P2 hygiene cluster covers schema-drift, missing test pins, and
log-retention policy on `comms/log.ndjson`.

Per sprint-prompt § 8 hand-off, no P0 was found, so the next sprint
after S-048 follows the active sprint default. **The current active
sprint per `docs/claude/milestone-state.md` is S-047 T6 (end-to-end
live smoke + runbook)** — T1 through T5 plus S-049 fast-followup all
shipped between this audit's first draft and operator-correction. The
hand-off accordingly points at S-047 T6, not S-047 T3 as the earlier
draft incorrectly specified.

---

## Bot 1 — `@bict_trading_bot` (AI Trader Bot)

Service: `deploy/ict-telegram-bot.service` → `python3 -m
src.bot.telegram_query_bot`. Working directory `/home/ubuntu/ict-trading-bot`,
restart-always, journal logging.

### Notifications

| Workplan requirement | On-disk implementation | Status | Gap |
|---|---|---|---|
| Every entry to every log in the database | `_drain_pending_pings` in `src/bot/telegram_query_bot.py:1671-1742` drains `runtime_logs/pending_pings/`. Producers (execution_diagnostics, liveness_watchdog, order_monitor, signal pipelines) write JSON files via `scripts/send_ping.py` (`target="trader"` branch). `src/bot/alert_manager.py:11-36` is the thin Telegram-API wrapper. | ⚠️ PARTIAL | No enforced fan-out from each required log (Signals / Order Package / Risk Manager Decision / Trade / Messages / Sprint / Bug / Lessons). Coverage is producer-defined, not contract-defined. No test pins "every log → ping". |
| Hourly snapshots | `/hourly` command at `telegram_query_bot.py:2808` (`cmd_hourly` → `processor.get_hourly_report`). `scripts/send_hourly_now.py` is a manual one-shot. | ❌ MISSING (auto) | No timer fires hourly. `deploy/ict-heartbeat.timer` is daily (13:00 UTC), not hourly, and is a heartbeat watchdog. The workplan calls for **automatic** hourly broadcasts. **P1-C** in D2. |
| Errors returned by any system component | Pipeline errors call `AlertManager.send_alert` (`alert_manager.py:18-36`). | ⚠️ PARTIAL | No central error-funnel contract. No test pins "every component error → ping". |
| Trade and account events | `pending_pings/` drainer surfaces trade-execution pings produced by `execution_diagnostics`, `liveness_watchdog`, `order_monitor` (per `claude_bridge.py:8-12` docstring cross-reference). | ✅ MATCHES | Test: `tests/test_send_ping.py`. |
| Other operational signals | Same drainer covers ad-hoc pings. | ⚠️ PARTIAL | Coverage producer-defined, not contract-defined. |

### Operator commands

| Workplan requirement | On-disk implementation | Status | Gap |
|---|---|---|---|
| Toggle account live / dry-run | `/toggle` registered at `telegram_query_bot.py:2973` (handler at `:1426`). `/accounts` at `:2979` opens an inline-button per-account flip whose callback chain is `acct_flip_ask` → `acct_flip_do` → `acct_flip_cancel` at `:2181-2232`. | ✅ MATCHES | Test: `tests/test_telegram_query_bot.py` (account-flip suite). |
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
| Hourly update | `/hourly` at `:2982` (on-demand). | ✅ MATCHES (menu) / ❌ MISSING (auto-fire) | The menu surface exists; the auto-broadcast does not. **P1-C** in D2. |
| VM stats | `/vmstats` at `:2989`. | ✅ MATCHES | — |

### Repo-driven request/response surface (S-027) — correctly placed here

Per the corrected architecture, the trader bot **is** the right host for
the structured operator-question system. Verified on-disk:

| Step | On-disk implementation | Status |
|---|---|---|
| 1. Claude writes a structured request artifact | `comms/schema/{request,response}.schema.json` define the contract; `src/comms/models.py` and `src/comms/store.py` validate and persist; `scripts/comms_ask.py` is the CLI helper. | ✅ MATCHES |
| 2. Bot detects + sends Telegram message | `CommsPoller` at `src/bot/comms_handler.py:145-256` (`poll_once` + `_deliver`). Renders inline keyboards from `Question` schemas; transitions `pending → sent` via `RequestStore.mark_sent`. Installed in trader bot via `install_comms_handlers(application, repo_root=REPO_ROOT)` at `telegram_query_bot.py:2955`. | ✅ MATCHES |
| 3. Operator responds | `comms_callback_handler` at `comms_handler.py:288-326` parses `comms:<reqid>:<qid>:<choiceid>` callback data; `comms_text_handler` at `:328-402` handles the `Other` free-text path. | ✅ MATCHES |
| 4. Response written back to repo | `apply_answer` at `:408-477` merges the answer into the JSON artifact, transitions per `src/comms/state.py`, then `GitPusher.commit_and_push` at `:487-538` commits with the `comms(response):` prefix and pushes (gated by `COMMS_PUSH_ENABLED=1`). | ✅ MATCHES |
| 5. Claude reads on next sync | Out-of-repo by design — Claude's next session reads `comms/requests/` and `comms/archive/` during its bootstrap read order. | ✅ MATCHES |

Tests pin the lifecycle: `tests/test_s027_comms_models.py`,
`tests/test_s027_comms_store.py`, `tests/test_s027_comms_state.py`,
`tests/test_s027_comms_handler.py`, `tests/test_s027_comms_ask_cli.py`.

### Trader-bot extras (workplan-silent)

`/start`, `/set_keys`, `/balance`, `/strategies`, `/backtest`,
`/latest_backtest`, `/download_journal`, `/price`, `/alerts`,
`/reload_strats`, `/backtest_ui`, `/accounts_status`, `/set_all_live`,
`/risk_check`, `/smoke_test`, `/sprintlet_status`, `/sprintlet_complete`,
`/checkpoint`, `/ping_test`, `/webapp`, `/vm` (`telegram_query_bot.py:2957-2992`).
These extend M1 beyond the workplan's spec — the workplan describes the
**minimum**, not a ceiling. Under the corrected architecture they all
stay on the trader bot.

---

## Bot 2 — `@claude_ict_comms_bot` (ClaudeBot)

Service: `deploy/ict-claude-bridge.service` → `python3 -m src.bot.claude_bridge`.
Working directory `/home/ubuntu/ict-trading-bot`, restart-always, memory-capped
at 200 MB, separate log files under `runtime_logs/claude_bridge*.log`.

### What ClaudeBot is (and is supposed to be)

`src/bot/claude_bridge.py:1-22` describes the on-disk implementation,
which **matches** the corrected architecture:

1. **Anthropic-API chat companion** — `chat` handler at `:107-158`. Per-chat
   in-memory history with `MAX_HISTORY=40`, model `claude-opus-4-7` by
   default.
2. **One-way ping drain** for `runtime_logs/pending_claude_pings/` —
   `_drain_pending_claude_pings` at `:289-351`, scheduled every
   `CLAUDE_PING_DRAIN_INTERVAL_S = 5 s` at `:392-398`. Mirrors the trader
   bot's drain semantics; only the inbox path differs.
3. **Three session-trigger commands** — `/audit`, `/improve_strategy`,
   `/train_model` at `:176-226` log to `runtime_logs/recurring_sessions.jsonl`
   and reply with starter prompts the operator pastes into a fresh Claude
   session.
4. **Housekeeping commands** — `/roadmap`, `/schedules`, `/start`, `/reset`,
   `/model`.

### Workplan-vs-reality reconciliation

| Workplan claim (§ ClaudeBot workflow) | On-disk reality | Verdict |
|---|---|---|
| 5-step two-way request/response loop | Not implemented on ClaudeBot — the response path lives on the trader bot. | **Workplan wrong.** Filed as P1-A. |
| Merge-review buttons (Merge / Hold) | Not implemented anywhere. Operator merges via GitHub web-UI; Tier 2 ping is an informational nudge. | **Workplan wrong.** Adding bot-side merge authority would expand the live surface. Filed as P1-A. |
| Required-user-action prompts as a first-class channel feature | Not implemented as a distinct surface. The S-027 system on the trader bot can host them when needed. | **Workplan wrong.** Filed as P1-A. |
| Recovery alerts for stuck/stale requests | Not implemented anywhere. `CommsPoller.poll_once` silently transitions expired requests to `EXPIRED` (`comms_handler.py:201-205`) without notifying the operator. `comms/README.md` § "Stuck request? How to recover" itself says the recovery commands "live in the bot itself (PR 2). Until then, manually edit the file's `delivery.send_attempts` to 0 and `status` back to `pending`." | **Real gap.** Filed as P1-B. |
| PM sprint-start pings | Implemented via `pending-pings.jsonl` event `sprint-start`, drained by `scripts/notify_on_pull.py:259-310`, fanned out via `scripts/send_ping.py` with `target="claude"`. | ✅ MATCHES |
| Sprint-completion updates | Same path as sprint-start (`event: "sprint-complete"`). | ✅ MATCHES |

The corrected architecture says ClaudeBot only has to deliver the two
sprint-lifecycle ping types and the trigger commands. Both sets are
present.

---

## Required logs

| Workplan log | On-disk implementation | Status | Gap |
|---|---|---|---|
| **Comms log** (workplan § "Additional logs and registries to add") | `src/comms/log.py:36-69` writes `comms/log.ndjson`. Events logged: `request_created`, `request_sent`, `answer_received`, `request_answered`, `request_acknowledged`, `request_expired`, `request_cancelled`, `error`. | ✅ MATCHES | Test: `tests/test_s027_comms_store.py`. P2: file is gitignored — backup/retention policy unwritten. |
| **Messages Log** (workplan § "Required logs" — "all messages sent to operator") | No canonical messages-log file. Outbound notifications flow through `pending_pings/` (drain → send → unlink) and through `pending_claude_pings/`; neither retains a structured record. `src/comms/log.ndjson` only records comms-request lifecycle events. | ⚠️ AMBIGUOUS | Operator decision required: should the messages log subsume `pending_pings/`, or be a parallel append-only journal? Flagging as ambiguous, not missing. |

---

## Operator-triggered workflows (workplan § "Repeatable operator-triggered workflows")

| Workplan workflow | On-disk implementation | Status | Gap |
|---|---|---|---|
| `new-session <sprint_id>` | None. `/audit`, `/improve_strategy`, `/train_model` (claude_bridge.py:176-226) trigger fixed *recurring* sessions; they don't initialize a sprint-id-targeted context. The closest existing surface is `/sprintlet_status` / `/sprintlet_complete` / `/checkpoint` on the trader bot, which are status reads, not session bootstraps. | ❌ MISSING | **P1-D** in D2. Under the corrected architecture this lands on the trader bot's S-027 comms-request surface (writes a `comms/requests/REQ-…-new-session.json` artifact for Claude to read on next sync). |
| `test <strategy_name>` (writes a structured test-request artifact) | None. No `/test` handler exists. `/backtest` (`telegram_query_bot.py:2970`) runs a *local* backtest in the bot process — not a comms-request artifact. | ❌ MISSING | **P1-D** in D2. M5 owns the backtest workflow that consumes the artifact; this audit covers only the bot-side dispatch surface. |
| Merge-review with **Merge** / **Hold** buttons | Operator merges on GitHub. | ✅ MATCHES (corrected architecture) | Workplan wording wrong — see P1-A. |
| Stuck-request recovery flow | Not implemented. | ❌ MISSING | **P1-B** in D2. |

---

## Deployment / runtime

| Concern | On-disk evidence | Notes |
|---|---|---|
| `ict-claude-bridge.service` health | Unit at `deploy/ict-claude-bridge.service`. `Restart=always`, `RestartSec=15`, `MemoryMax=200M`, `MemoryHigh=150M`. Logs append-only to `runtime_logs/claude_bridge*.log`. | OK on paper; live state not verified in this static audit. |
| `ict-telegram-bot.service` health | Unit at `deploy/ict-telegram-bot.service`. `Restart=always`, `RestartSec=15`. Journal logging. `After=…ict-trader-live.service`. | OK on paper. |
| `ict-git-sync.timer` cadence | 5-min default (`deploy/ict-git-sync.timer`). | This is the latency floor for repo-driven comms. |
| Auto-sync from main | The trader VM pulls `main` every 5 min, restarts services on change. The comms request poller uses its own `poll_interval` independent of git-sync. | Two clocks; document drift acceptable so far. |
| Restart behaviour for the comms poller | `CommsPoller.start` is registered as `Application.post_init` (`comms_handler.py:600-614`). On bot restart the poller restarts; pending requests on disk survive restarts. | Inflight Telegram callbacks may be lost — no test pins recovery from a restart mid-callback. P2. |
| Secret rotation | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CLAUDE_BOT_TOKEN`, `TELEGRAM_CHAT_ID` loaded from `/home/ubuntu/ict-trading-bot/.env`. | Out of scope for static audit; flag for the deployment / change log workplan item. |

---

## Test coverage

| Workplan requirement | Test pinning |
|---|---|
| `/halt`, `/closeall`, `/toggle` operator commands | ✅ `tests/test_telegram_query_bot.py` (existence + handler args). |
| Information menus on the trader bot | ✅ `tests/test_s007_bot_commands.py`, `tests/test_s008_telegram_rewired.py`. |
| Trade-execution pings via `pending_pings/` drain | ✅ `tests/test_send_ping.py`. |
| Comms request lifecycle (state, store, schema) | ✅ `tests/test_s027_comms_state.py`, `tests/test_s027_comms_store.py`, `tests/test_s027_comms_models.py`. |
| Comms callback parsing + git writeback | ✅ `tests/test_s027_comms_handler.py`. |
| `comms_ask` CLI helper | ✅ `tests/test_s027_comms_ask_cli.py`. |
| ClaudeBot pending-ping drain | ✅ Covered by `pending-pings.jsonl` flow tests in `tests/test_notify_on_pull.py`. |
| Hourly snapshot auto-fire | ❌ NO TEST (no implementation). |
| Stuck-request recovery alert | ❌ NO TEST (no implementation). |
| `/new-session`, `/test` operator commands | ❌ NO TEST (no implementation). |

---

## Cross-cutting concerns

### Two parallel comms surfaces (informational)

The repo runs two comms surfaces in parallel **by design** under the
corrected architecture:

| Surface | Purpose | Direction | File of record |
|---|---|---|---|
| `docs/claude/pending-pings.jsonl` + `runtime_logs/pending_claude_pings/` | Sprint-start, sprint-complete, blocker, training notifications, etc. | One-way (Claude → operator) | `pending-pings.jsonl` (append-only); ClaudeBot drains. |
| `comms/requests/` + `comms/archive/` + `comms/log.ndjson` | Structured questions + operator answers for ask/answer flows | Two-way (Claude ↔ operator) | per-request JSON files + ndjson event log; trader bot delivers. |

The earlier audit framed this as a structural gap. Under the corrected
architecture it is **intentional** — the two surfaces serve different
purposes and unifying them would conflate one-way pings with two-way
asks. The schema-drift between the two formats is logged as a P2
hygiene item only.

### Pending-request artifact schema drift (P2)

`comms/schema/request.schema.json` is the canonical schema for the S-027
two-way surface. `pending-pings.jsonl` uses an ad-hoc, free-form JSONL
schema (varies per event). They are not versioned or aligned. Under the
corrected architecture the surfaces are intentionally distinct, but a
common envelope (priority, event-id, timestamp) would make consumer code
simpler. P2.

### `comms(response):` commit prefix vs notification routing (P2)

`scripts/notify_on_pull.py:165-207` recognises commit-subject prefixes for
ping fanout. The `comms(response):` prefix is silently ignored
(`docs/claude/telegram-pings.md:211`). That's correct: response writebacks
shouldn't fire pings. But there's no test that pins this generic-ignore
in `notify_on_pull.py` (the existing test covers the blocker side). P2.

### Comms log retention (P2)

`comms/log.ndjson` is gitignored (`comms/.gitignore`). The on-disk file is
the only record. If the VM disk is wiped without backup, the comms-state
audit trail is lost. P2 — flag for the audit / log-retention sprint.

### Bot identity confusion in command names (P2)

The trader bot registers `/sprintlet_status`, `/sprintlet_complete`,
`/checkpoint`, `/ping_test` (`telegram_query_bot.py:2985-2990`). Under
the corrected architecture these stay on the trader bot (consistent
with the rest of S-027 living there), but the names suggest comms /
ClaudeBot affordances. Cosmetic at best. P2.

---

## Severity classification

Per § 4.D1 rubric:

- **P0** — none.
- **P1 — four real gaps** against the corrected architecture:
  - **P1-A** — workplan correction (canonical doc currently misdescribes
    the architecture).
  - **P1-B** — stuck-request recovery alerts (no Telegram notification on
    TTL expiry).
  - **P1-C** — auto-hourly snapshot timer.
  - **P1-D** — `/new-session <sprint_id>` and `/test <strategy>` commands.
- **P2** — schema drift, missing test pins, log retention, command-naming.

The detailed prioritized backlog is in
`docs/audits/M1-comms-audit-followups.md`.

---

## Hand-off

Per sprint-prompt § 8 — no P0 surfaced — the next sprint default is the
current active sprint per `docs/claude/milestone-state.md`. As of
2026-05-07 evening that is **S-047 T6 (end-to-end live smoke + runbook)**
— T1 through T5 plus the S-049 fast-followup all shipped between this
audit's first draft and operator-correction. The hand-off accordingly
points at S-047 T6, not S-047 T3 as the earlier draft incorrectly
specified.

The four P1 follow-ups (and the P2 hygiene cluster) sit behind S-047
T6/T7 in priority. Per operator directive 2026-05-07 evening, this
session executes them in the order **P1-A → P1-D → P1-B → P1-C** for
fastest visible progress (P1-A is a 10-minute workplan edit; P1-D
unblocks the M5 strategy-test workflow; P1-B and P1-C harden the comms
surface).
