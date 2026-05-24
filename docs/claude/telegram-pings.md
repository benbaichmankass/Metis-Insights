# Telegram pings — what triggers what, and where the wiring lives

The system uses two bots:
- **@bict_trading_bot** — operator command bot (the 4-item menu) + trade and
  hourly notifications. Spec: `docs/TELEGRAM-SPEC.md`.
- **@claude_ict_comms_bot** — **one-way** outbound channel for everything
  *Claude* is doing: sprint open/close + checkpoints, health-review open/close,
  training-session open/close (+ results), "waiting-for-input" pings, system-
  health snapshots, blocker and merge-review pings. **No operator response
  path exists.** The operator reads via Telegram; any reply is handled through
  GitHub (PR/issue comments) or a new Claude session reading repo state. This
  is intentional — the channel is send-only.

This is the **single source of truth** for the Claude update channel
(`src/bot/claude_bridge.py`). The bridge is strictly send-only: as of the
2026-05-24 overhaul it has **no freeform Anthropic chat and no trigger
commands** (`/audit`, `/improve_strategy`, `/train_model` were removed); it
only drains the inbox and posts to the single thread.

## Single thread (TELEGRAM_CLAUDE_THREAD_ID)

All Claude updates land in **one** thread. Two cases:
- **Normal (non-forum) chat** — there is only one conversation, so nothing to
  configure. Leave `TELEGRAM_CLAUDE_THREAD_ID` unset.
- **Forum chat (topics enabled)** — set `TELEGRAM_CLAUDE_THREAD_ID` to the
  numeric message-thread id of the topic you want updates in. The bridge
  passes it as `message_thread_id` on **every** send, so updates never scatter
  across topics. This is the fix for the historical "several threads" bug
  (the bridge previously omitted `message_thread_id`).

Required env for the bridge (`deploy/ict-claude-bridge.service`,
`EnvironmentFile=.env`):

| Var | Required | Purpose |
|---|---|---|
| `TELEGRAM_CLAUDE_BOT_TOKEN` | **yes** | @claude_ict_comms_bot token. If unset the bridge cannot start — the #1 reason the channel ever appears "dead". |
| `TELEGRAM_CHAT_ID` | **yes** | Operator chat id (shared with the trader bot). |
| `TELEGRAM_CLAUDE_THREAD_ID` | no | Forum topic id to pin all messages to one thread. |

The git-relay wiring described below is **implemented and verified** as of
S-042 (2026-05-06); the one-way/single-thread/event-vocabulary changes landed
in the 2026-05-24 bots overhaul.

## Required pings

Every event below MUST send exactly one Telegram message. If the
sandbox running Claude can't reach Telegram (token absent, DNS blocked,
etc.), the fallback rule applies — see § "Fallback when sandbox can't
ping" below.

| Event | Trigger | Message contents | Priority |
|---|---|---|---|
| **Sprint start** | T0 of every sprint | sprint id, title | normal |
| **Checkpoint appended** | A commit on `main` modifies `docs/claude/checkpoints/CHECKPOINT_LOG.md`, or a `checkpoint` line in `pending-pings.jsonl` | CP id, sprint, current-phase line, next checkpoint id, link to the commit on github | normal |
| **Sprint complete** | Final checkpoint of a sprint (e.g. CP-…-WRAPPED, CP-…-COMPLETE in title) | sprint id, PRs merged count, drafts left, link to the sprint summary | high |
| **Health review start** | Claude begins a `/health-review` (layer-2) pass | scope/title | normal |
| **Health review complete** | Claude finishes a health review | grade, one-line summary, link to the review artifact | high |
| **Training session start** | A model training cycle / session begins (`training-start`, or commit `[TRAINING-START]`) | strategy/model under study, link | normal |
| **Training session complete** | A training cycle finishes (`training-complete`, or PR `TRAINING-RESULTS:`) | strategy/model, result, one-line summary, link to SUMMARY.md | high |
| **Waiting for input** | Claude is blocked on an operator decision but does not need the full draft-PR dance | the question, link to the chat / PR | **urgent** |
| **Blocker — needs PM input** | A commit message contains `[BLOCKED-PM]` OR a PR title starts with `BLOCKED:` | what's blocked, the question, link to the PR / commit, link to the chat | **urgent** |
| **PR opened as DRAFT for PM review** | A PR is opened with `draft: true` and a title containing `(PM REVIEW)` or `DRAFT:` | PR title, why it's gated, link | high |
| **PR merged** | Any squash-merge to `main` from a `claude/*` branch | PR title, link | low |
| **CI failure on a Claude branch** | If/when CI is wired — failure on any branch starting with `claude/` | branch, failed job, link | high |
| **Session-close** | Last commit of an autonomous session pushes any new content | sprint id, "session ending", commit count this session, link to last checkpoint | normal |
| **Training session start** | Checkpoint commit with `[TRAINING-START]` in title | strategy/model under study, link to commit | normal |
| **Training notebook ready** | PR opened with title prefix `TRAINING-PLAN:` | run-id, Colab open-in-Colab URL, expected runtime, link to PR | high |
| **Training run complete** | PR opened with title prefix `TRAINING-RESULTS:` (or `TRAINING-RESULTS [FAILED]:`) | run-id, hypothesis count, success/failure, link to SUMMARY.md, link to PR | high |
| **Recommendations ready for approval** | PR opened with title prefix `RECOMMENDATIONS (PM REVIEW):` (matches existing `(PM REVIEW)` rule). Writeup only — no code changes. | run-id, proposed change summary, link to PR, chat link | high |
| **Implementation PR (post-approval)** | PR opened with title prefix `IMPLEMENT:` after the operator approves the recommendations writeup | run-id, files touched, link to PR | high |

The session-close ping is a fallback for sessions that didn't already
trigger a "checkpoint appended" or "sprint complete" ping. Don't
double-fire — if a checkpoint commit landed in this session, the
session-close ping is suppressed.

## Where the wiring lives

Two distinct paths because the sandbox can't always reach Telegram:

### Path 1 — VM-side (primary, runs on the Oracle VM)

**Status: VERIFIED WORKING** as of 2026-05-06 (BUG-058 PR #423 +
BUG-059 PR #426 deployed; `ict-claude-bridge.service` confirmed active).

The VM has:
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `/etc/ict-trader/claude.env`.
- `ict-git-sync.timer` — pulls `main` every 5 minutes (`deploy/ict-git-sync.timer`).
- `scripts/deploy_pull_restart.sh` — runs on each pull; calls `scripts/notify_on_pull.py`.
- `scripts/notify_on_pull.py` — drains `docs/claude/pending-pings.jsonl` + detects
  checkpoint/blocker/training events; enqueues via `scripts/send_ping.py` with
  `target="claude"`.
- `scripts/send_ping.py` — writes JSON files to `runtime_logs/pending_claude_pings/`.
- `ict-claude-bridge.service` — drains `runtime_logs/pending_claude_pings/` every
  ~5 s; sends to `@claude_ict_comms_bot`.

End-to-end latency: ≤5 min (git-sync tick) + ≤5 s (bridge drain) = ≤5 min from push.
Hash-based dedup (`runtime_logs/pending_pings_delivered.txt`) prevents old
`pending-pings.jsonl` lines from re-firing on subsequent git pulls.

### Path 2 — sandbox-side (fallback, runs inside Claude Code)

When Claude Code is running in a sandbox:

- `TELEGRAM_BOT_TOKEN` is **not** present in env.
- Outbound HTTPS is restricted to pypi + github.

So sandbox-side direct pings are infeasible. The fallback (and the **mandatory
ping habit** described below):

1. Append a ping-request entry to `docs/claude/pending-pings.jsonl`.
2. Push the commit; the VM's git-sync timer picks it up within ≤5 min.
3. The VM-side pipeline (`notify_on_pull.py` → `send_ping.py` →
   `ict-claude-bridge.service`) drains the file and forwards each entry
   to `@claude_ict_comms_bot`.
4. Hash-based dedup prevents old lines from re-firing on subsequent pulls.

**One-way channel:** The operator reads pings in Telegram. There is no
response-writeback path from the operator to Claude via this channel.
Operator decisions happen through GitHub (PR comments, issue updates) or
a new Claude session reading repo state. This is intentional — the channel
is send-only; no polling, no bot-command handling on the Claude side.

**Latency note:** Adds ≤5 min for sandbox-originated pings; acceptable for
status updates. For blockers also flip the PR to draft + add `BLOCKED:` in
the PR title so GitHub notifications fire immediately in parallel.

## Mandatory ping habit (established S-042, carry forward forever)

Claude MUST append one line to `docs/claude/pending-pings.jsonl` at each
of the following events before committing:

| Event | When | JSON schema |
|---|---|---|
| **Sprint start** | T0 of every sprint | `{"event": "sprint-start", "sprint": "S-NNN", "title": "..."}` |
| **Checkpoint** | Each intermediate checkpoint | `{"event": "checkpoint", "sprint": "S-NNN", "cp_id": "CP-...", "title": "...", "next_cp": "..."}` |
| **Sprint complete** | Final checkpoint of every sprint | `{"event": "sprint-complete", "sprint": "S-NNN", "title": "...", "summary_url": "..."}` |
| **Health review start** | Start of a `/health-review` pass | `{"event": "health-review-start", "title": "..."}` |
| **Health review complete** | End of a health review | `{"event": "health-review-complete", "grade": "🟢 healthy", "summary": "...", "summary_url": "..."}` |
| **Training start** | A training session/cycle begins | `{"event": "training-start", "strategy": "...", "title": "..."}` |
| **Training complete** | A training session/cycle ends | `{"event": "training-complete", "strategy": "...", "result": "...", "summary": "...", "summary_url": "..."}` |
| **Waiting for input** | Blocked on an operator decision | `{"event": "waiting-input", "question": "...", "chat_url": "..."}` |
| **Blocker** | Any session that cannot proceed | `{"event": "blocker", "sprint": "S-NNN", "question": "..."}` |
| **Tier 2 merge review** | Any Tier 2 PR opened for operator review | `{"event": "merge-review", "sprint": "S-NNN", "pr_url": "..."}` |

`priority` is optional: omit it and the fanout assigns one by event type
(`*-complete` / `merge-review` → high; `blocker` / `waiting-input` → urgent;
everything else → normal). The friendly title shown to the operator
(`🟢 Sprint started …`, `✅ Sprint complete …`, `⏳ Waiting for your input …`)
is rendered VM-side by `scripts/notify_on_pull.py::_render_event_body` —
`EVENT_LABELS` / `EVENT_DEFAULT_PRIORITY` there are the canonical mapping.
Recognised detail fields: `sprint`, `title`, `cp_id`, `next_cp`, `phase`,
`strategy`, `model`, `result`, `grade`, `question`, `summary`, and the URL
fields `pr_url` / `commit_url` / `chat_url` / `summary_url`.

Rules:
- One line per event. Lines are JSONL (newline-delimited JSON).
- The file is gitignored but tracked (`git add -f` or explicit `git add` works;
  the GitHub API writes directly without gitignore checks).
- The VM's hash-based dedup (`DELIVERED_HASHES`) prevents re-fire on subsequent
  pulls — never truncate the file from Claude-side.
- Append only — never modify or delete existing lines.

Example — sprint-start (T0):
```json
{"event": "sprint-start", "priority": "normal", "sprint": "S-042", "title": "M1 verify ClaudeBot channel"}
```

Example — sprint-complete (T5):
```json
{"event": "sprint-complete", "priority": "high", "sprint": "S-042", "title": "M1 closed — ClaudeBot channel verified", "summary_url": "https://github.com/benbaichmankass/ict-trading-bot/blob/main/docs/sprint-summaries/sprint-042-summary.md"}
```

## Blocker pings — escalation contract

When the autonomous Claude session can't proceed and needs operator
input:

1. Make a commit with `[BLOCKED-PM] <one-line question>` in the
   subject **on the work branch**. The body of the commit message
   contains: what's blocking, what context is needed to unblock,
   what Claude tried.
2. Open a **draft** PR with title `BLOCKED: <one-line question>`.
   This PR carries the actual change (or the half-built change). It
   is **never** self-merged — only the operator decides.
3. Include the chat link in the PR body (the session URL the operator
   can open to reply directly to Claude).
4. **Open a separate ping-PR** (see "Ping-PR vs work-PR" below) and
   self-merge it. That merge fires the Telegram alert via the
   existing checkpoint / pending-pings drain.
5. Stop. Do not start unrelated work in the meantime.

The VM-side ping then surfaces this immediately (highest priority), with
a link to both the work PR and the chat.

## Ping-PR vs work-PR (MANDATORY pattern)

Pings ride on **merged commits**, not on draft PRs. So when an
autonomous session needs to fire a ping while keeping its actual
change open for review, two PRs are required, on two branches:

| Concern | Ping-PR | Work-PR |
|---|---|---|
| Branch | `claude/ping-<slug>` | `claude/<sprint-or-task>-…` |
| Title prefix | `PING:` (high priority) or `BLOCKED-PING:` (urgent) | `BLOCKED:`, `(PM REVIEW):`, normal sprint title |
| Payload | ≤5 lines: append to `docs/claude/pending-pings.jsonl` *or* checkpoint-log entry | the actual code/docs change |
| Action | self-merge immediately → fires ping | left as **draft** → operator reviews/approves |
| Body must include | link to the work-PR + the question / status | chat link + question / context |

Why two PRs:
- Merging the work-PR to "fire the ping" silently approves your own
  change. Operator loses the chance to gate the merge.
- Leaving the work-PR as draft without a ping means the operator
  doesn't know they were waited on.
- The split keeps the channel (ping-PR) and the content (work-PR) on
  different commits, so reverting one never affects the other.

Failure mode the pattern prevents: the recurring "I'm waiting on you,
but you didn't see anything in Telegram" loop noted in
CP-2026-05-02-03 review. Always do this when a session needs operator
weigh-in before continuing.

## Failure modes the wiring must handle

| Failure | Required behaviour |
|---|---|
| `TELEGRAM_BOT_TOKEN` empty on VM | Log a warning, do not crash the deploy script. Operator sees a "ping suppressed" entry in `journalctl -u ict-git-sync` |
| Telegram API 5xx | Retry up to 3× with exponential backoff (1 s, 4 s, 16 s). Then drop the ping with a logged warning |
| `pending-pings.jsonl` corrupt | Move to `pending-pings.jsonl.broken-<timestamp>`, send a single "ping queue corrupt" ping (if Telegram reachable), continue |
| Sandbox-side write to `pending-pings.jsonl` fails | Best-effort — log and continue. The session-close `git status` check surfaces uncommitted work to the operator |
| HEAD didn't advance | Send nothing (idempotency) |
| Old lines re-fire on subsequent pulls | Hash-based dedup via `runtime_logs/pending_pings_delivered.txt` prevents re-fire |

## Decisions log

- **Why VM-side, not GitHub Actions?** The VM already has the token and
  network; adding a GitHub secret is more attack surface for one-line
  notification logic. Revisit if VM access ever becomes the bottleneck.
- **Why JSONL queue, not direct push from the sandbox?** Sandbox can't
  reach Telegram. The queue file plus VM drain is the cheapest reliable
  channel; round-trip ≤5 min matches the existing sync cadence.
- **Why 5-min pull cadence, not webhook?** The repo already has the
  pull timer; webhooks need a public endpoint and deeper change. 5 min
  is fast enough for status; blockers double-route via PR notifications.
- **Why one-way?** ClaudeBot is a status-reporting channel. Operator
  decisions (merge, hold, approve) happen through GitHub workflows and
  new sessions reading repo state. Adding a response path would require
  a polling loop or webhook that complicates the architecture without
  adding value given the existing PR-based workflow.

## Title-prefix grep list (VM-side script)

The VM-side `notify_on_pull.py` recognises these title prefixes when
scanning the new commit range / open PRs. Adding a new ping = adding a
prefix here.

| Prefix | Surface |
|---|---|
| `[TRAINING-START]` (commit subject) | training session start |
| `[BLOCKED-PM]` (commit subject) | blocker (urgent) |
| `comms(response):` (commit subject) | **silently ignored** — comms response writebacks |
| `BLOCKED:` (PR title) | blocker (urgent, also notifies via GitHub) |
| `TRAINING-PLAN:` (PR title) | training notebook ready |
| `TRAINING-RESULTS:` (PR title) | training run complete |
| `TRAINING-RESULTS [FAILED]:` (PR title) | training run failed (still notify) |
| `RECOMMENDATIONS (PM REVIEW):` (PR title) | recommendations ready for approval (writeup only) |
| `IMPLEMENT:` (PR title) | post-approval implementation PR with code changes |
| `(PM REVIEW)` / `DRAFT:` (PR title) | generic PM-review draft |
| `CP-…-WRAPPED` / `CP-…-COMPLETE` (in checkpoint title) | sprint complete |

## Cross-references

- `scripts/notify_session.py` — existing session-end ping helper. Keep
  it for the manual fallback path (`PYTHONPATH=. python …`); the new
  wiring above runs automatically without operator action.
- `src/runtime/notify.py::send_via_alert_manager` — the actual Telegram
  API caller; reads token + chat-id from env.
- `scripts/deploy_pull_restart.sh` — the VM's pull-and-restart script
  that the ping logic plugs into.
- `scripts/notify_on_pull.py` — VM-side ping fanout; drains
  `pending-pings.jsonl` + detects blocker/training/checkpoint events.
- `scripts/send_ping.py` — enqueue helper; `target="claude"` routes to
  `@claude_ict_comms_bot`.
- `docs/claude/sprint-planning.md` — references this doc for the
  per-sprint ping requirements.
- `docs/claude/training-improvement-workflow.md` — defines the four
  `TRAINING-*` / `RECOMMENDATIONS` PR title prefixes that ride on the
  generic PR-opened ping.
- `docs/claude/bug-log.md` — entry BUG-018 tracks "operator not
  receiving sprint progress pings".
