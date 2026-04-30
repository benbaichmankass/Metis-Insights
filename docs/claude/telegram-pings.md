# Telegram pings — what triggers what, and where the wiring lives

The operator has a single Telegram bot account that should receive pings
for every meaningful sprint event. This doc is the spec; the actual
wiring is implemented in the housekeeping session that follows the
S-015 close.

## Required pings

Every event below MUST send exactly one Telegram message. If the
sandbox running Claude can't reach Telegram (token absent, DNS blocked,
etc.), the fallback rule applies — see § "Fallback when sandbox can't
ping" below.

| Event | Trigger | Message contents | Priority |
|---|---|---|---|
| **Checkpoint appended** | A commit on `main` modifies `docs/claude/checkpoints/CHECKPOINT_LOG.md` | CP id, sprint, current-phase line, next checkpoint id, link to the commit on github | normal |
| **Sprint complete** | Final checkpoint of a sprint (e.g. CP-…-WRAPPED, CP-…-COMPLETE in title) | sprint id, PRs merged count, drafts left, link to the sprint summary | high |
| **Blocker — needs PM input** | A commit message contains `[BLOCKED-PM]` OR a PR title starts with `BLOCKED:` | what's blocked, the question, link to the PR / commit, link to the chat | **urgent** |
| **PR opened as DRAFT for PM review** | A PR is opened with `draft: true` and a title containing `(PM REVIEW)` or `DRAFT:` | PR title, why it's gated, link | high |
| **PR merged** | Any squash-merge to `main` from a `claude/*` branch | PR title, link | low |
| **CI failure on a Claude branch** | If/when CI is wired — failure on any branch starting with `claude/` | branch, failed job, link | high |
| **Session-close** | Last commit of an autonomous session pushes any new content | sprint id, "session ending", commit count this session, link to last checkpoint | normal |

The session-close ping is a fallback for sessions that didn't already
trigger a "checkpoint appended" or "sprint complete" ping. Don't
double-fire — if a checkpoint commit landed in this session, the
session-close ping is suppressed.

## Where the wiring lives

Two distinct paths because the sandbox can't always reach Telegram:

### Path 1 — VM-side (primary, runs on the Oracle VM)

The VM already has:
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `/etc/ict-trader/claude.env`.
- `ict-git-sync.timer` pulling `main` every 5 minutes.
- `scripts/deploy_pull_restart.sh` running on each pull.

**Implementation plan (housekeeping session):**

1. Extend `scripts/deploy_pull_restart.sh` (or add a sibling
   `scripts/notify_on_pull.sh` it calls) to:
   - Compare the previous and new HEAD.
   - If the diff touches `docs/claude/checkpoints/CHECKPOINT_LOG.md`,
     extract the topmost CP entry's title + next-checkpoint line.
   - If any commit in the range contains `[BLOCKED-PM]` in the message
     subject, surface it as an urgent message *first*, before normal
     checkpoint pings.
   - Send via the existing `src.runtime.notify.send_via_alert_manager`.
2. The script must be idempotent: a re-pull that doesn't advance HEAD
   sends nothing (matches the BUG-008 fix).
3. A 5-minute pull cadence is acceptable — operator already accepted
   that in CP-2026-04-30-05.
4. Tests live in `tests/test_notify_on_pull.py`: synthetic
   CHECKPOINT_LOG diffs, blocker-keyword detection, idempotency
   on no-advance pulls.

### Path 2 — sandbox-side (fallback, runs inside Claude Code)

When Claude Code is running in this sandbox:

- `TELEGRAM_BOT_TOKEN` is **not** present in env.
- Outbound HTTPS is restricted to pypi + github.

So sandbox-side direct pings are infeasible. The fallback:

1. Append a ping-request entry to `docs/claude/pending-pings.jsonl`
   (gitignored except for an empty `.gitkeep`).
2. Push the commit; the VM's git-sync timer picks it up within 5 min.
3. The VM-side script (path 1) drains `pending-pings.jsonl` and
   forwards each entry via Telegram.
4. After successful drain, the VM-side script truncates the file in a
   follow-up commit (clean working-tree invariant).

This adds ≤ 5 min latency for sandbox-originated pings; acceptable for
status updates. **Not acceptable for blocker pings** that need a
fast PM response — for those, also flip the PR to draft + put `BLOCKED:`
in the PR title so the operator's GitHub notifications fire too.

## Blocker pings — escalation contract

When the autonomous Claude session can't proceed and needs operator
input:

1. Make a commit with `[BLOCKED-PM] <one-line question>` in the
   subject. The body of the commit message contains: what's blocking,
   what context is needed to unblock, what Claude tried.
2. Open a draft PR with title `BLOCKED: <one-line question>`.
3. Include the chat link in the PR body (the session URL the operator
   can open to reply directly to Claude).
4. Stop. Do not start unrelated work in the meantime.

The VM-side ping then surfaces this immediately (highest priority), with
a link to both the PR and the chat.

## Failure modes the wiring must handle

| Failure | Required behaviour |
|---|---|
| `TELEGRAM_BOT_TOKEN` empty on VM | Log a warning, do not crash the deploy script. Operator sees a "ping suppressed" entry in `journalctl -u ict-git-sync` |
| Telegram API 5xx | Retry up to 3× with exponential backoff (1 s, 4 s, 16 s). Then drop the ping with a logged warning |
| `pending-pings.jsonl` corrupt | Move to `pending-pings.jsonl.broken-<timestamp>`, send a single "ping queue corrupt" ping (if Telegram reachable), continue |
| Sandbox-side write to `pending-pings.jsonl` fails | Best-effort — log and continue. The session-close `git status` check surfaces uncommitted work to the operator |
| HEAD didn't advance | Send nothing (idempotency) |

## Decisions log

- **Why VM-side, not GitHub Actions?** The VM already has the token and
  network; adding a GitHub secret is more attack surface for one-line
  notification logic. Revisit if VM access ever becomes the bottleneck.
- **Why JSONL queue, not direct push from the sandbox?** Sandbox can't
  reach Telegram. The queue file plus VM drain is the cheapest reliable
  channel; round-trip ≤ 5 min matches the existing sync cadence.
- **Why 5-min pull cadence, not webhook?** The repo already has the
  pull timer; webhooks need a public endpoint and deeper change. 5 min
  is fast enough for status; blockers double-route via PR notifications.

## Cross-references

- `scripts/notify_session.py` — existing session-end ping helper. Keep
  it for the manual fallback path (`PYTHONPATH=. python …`); the new
  wiring above runs automatically without operator action.
- `src/runtime/notify.py::send_via_alert_manager` — the actual Telegram
  API caller; reads token + chat-id from env.
- `scripts/deploy_pull_restart.sh` — the VM's pull-and-restart script
  that the new ping logic plugs into.
- `docs/claude/sprint-planning.md` — references this doc for the
  per-sprint ping requirements.
- `docs/claude/bug-log.md` — entry BUG-018 tracks "operator not
  receiving sprint progress pings".
