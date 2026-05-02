# Comms architecture — Claude ↔ Telegram operator channel

**Status:** PR 1 (foundation) merged. PR 2 (Telegram bot integration) deferred.
**Sprint:** S-027 — telegram-comms-infrastructure.

This is the canonical architecture doc for the operator-communication
feature. Operator-facing instructions live in
[`comms/README.md`](../../comms/README.md). Timer/polling tradeoffs live
in [`comms-timer-assessment.md`](comms-timer-assessment.md).

---

## 1. Goal & non-goals

**Goal.** Let Claude ask the operator a structured question — multiple
choice, "Other" free-text, or one-or-many questions in a session — over
Telegram, get the answer back into the repo as structured JSON, and
keep the whole thing isolated from PR discussion and trading logic.

**Non-goals (deliberately out of scope):**

- Real-time chat. The latency floor is ~1 min (the bot's poll); ~6 min
  end-to-end (git-sync 5 min + bot poll 1 min + Telegram <1 s).
- Replacing PR review. PRs remain the channel for code review;
  `comms/` is for *operational* questions ("which exchange should we
  prioritise?") that are noise inside a code-review thread.
- General Telegram bot UX. Existing commands (`/halt`, `/status`,
  `/signals`, …) stay where they are. Comms is a sibling subsystem.
- Database / queue infra. The repo is the database. Files are the queue.

## 2. High-level flow

```
   Claude (sandbox)        GitHub             VM                 Telegram
   ────────────────        ──────             ──                 ────────
   write REQ-*.json    ──► commit/push    ──► git-sync      ──►
   (status=pending)        on branch          (5 min)
                           merge to main     restart bot
                                              │
                                              ▼
                                    bot polls comms/requests/
                                    (in-process, 1 min — PR 2)
                                              │
                                              ▼  send menu
                                    ┌───────────────────────────►
                                    │                          operator taps button
                                    │  callback / text  ◄──────
                                    ▼
                                    write .response into REQ-*.json
                                    flip status=answered
                                    commit "comms(response): REQ-..."
                                    push origin/main
                                              │
   read REQ-*.json     ◄── pull ◄────────────┘
   (status=answered)
   set status=acknowledged
   archive
```

## 3. State machine

States live in [`src/comms/state.py`](../../src/comms/state.py).

```
    ┌──────────┐   bot delivered                        ┌──────────────┐
    │ pending  │ ────────────────────────────────────► │     sent     │
    └────┬─────┘                                        └──────┬───────┘
         │ cancelled (Claude/operator)                         │
         │                                                     │ partial answer
         │                                ┌────────────────────┤
         │                                │                    │
         │                                ▼                    │
         │                    ┌───────────────────────┐        │
         │                    │ partially_answered    │ ───────┤
         │                    └──────────┬────────────┘        │
         │                               │ all required        │ all required
         │                               ▼                     ▼
         │                          ┌──────────┐  ◄────────────┘
         │                          │ answered │
         │                          └─────┬────┘
         │                                │ Claude reads
         │                                ▼
         │                       ┌────────────────┐
         │                       │ acknowledged   │  (terminal → archive/)
         │                       └────────────────┘
         │
         │  (any non-terminal → expired on TTL elapse)
         ▼
    ┌──────────┐
    │ expired  │ (terminal → archive/)
    │cancelled │
    └──────────┘
```

| Transition | Owner | When |
|---|---|---|
| `→ pending` | Claude | request authored |
| `pending → sent` | bot | Telegram API confirms send |
| `sent → partially_answered` | bot | first answer of a multi-q request lands |
| `* → answered` | bot | every required question has an answer |
| `answered → acknowledged` | Claude | next session reads the answer |
| `* → expired` | bot poll | `expires_at < now` |
| `pending → cancelled` | either | revoke before delivery |

**Terminal:** `acknowledged`, `expired`, `cancelled`. Bot moves
terminal artifacts from `comms/requests/` to `comms/archive/` on the
next poll.

## 4. File contract

### 4.1 Request artifact

One JSON file per request at `comms/requests/<request_id>.json`,
schema [`comms/schema/request.schema.json`](../../comms/schema/request.schema.json).

Required top-level fields:

- `request_id` — `REQ-YYYYMMDD-HHMMSS-<slug>` (regex-validated).
- `schema_version` — currently `1`. Bot refuses unknown versions.
- `created_at` — UTC ISO-8601.
- `source.actor` — `"claude" | "operator" | "system"`.
- `questions[]` — 1..10 items; each has `question_id`, `prompt`,
  `input_type` (`choice|multi_choice|free_text|yes_no`), and
  optionally `choices`, `allow_other`, `allow_free_text`, `required`,
  `default_choice`.
- `status` — current state-machine state.
- `default_on_timeout` — `expire | use_defaults | close`.

Optional but useful:

- `expires_at` — UTC ISO-8601 deadline.
- `topic` / `context` — operator-facing labels.
- `delivery` — bot-managed bookkeeping (`sent_at`, `telegram_message_id`,
  `send_attempts`).
- `response` — populated by the bot on operator reply.
- `history[]` — append-only state-transition trail.

### 4.2 Response sub-document

Embedded inline at `request.response`, schema
[`comms/schema/response.schema.json`](../../comms/schema/response.schema.json).
Single source of truth — no separate `input_response.json`. This was a
deliberate choice over the spec's two-file suggestion:

- avoids correlation bugs (no chance of orphan response files);
- cleaner git history (one path per artifact);
- atomic state transitions (status + response update in one write).

The response carries `answers[]` (one per answered question), an overall
`status` (`partial | complete | invalid`), and optional operator
identity (`telegram_user_id`, `telegram_username`).

## 5. Module layout

```
src/comms/
├── __init__.py    public surface
├── models.py      dataclasses: Request, Question, Choice, Answer, Response
├── state.py       STATUS, ANSWER_STATUS, can_transition, next_status_after_answer
├── store.py       RequestStore: load/list/create/transition/archive
└── log.py         log_event() → comms/log.ndjson

comms/
├── README.md      operator-facing
├── schema/*.json  JSON Schema draft 2020-12 contracts
├── requests/      active artifacts (.gitkeep)
└── archive/       terminal artifacts (.gitkeep)
```

`src/comms/` has **zero imports from the trader runtime**. Live trading
cannot regress because of a comms bug — the only consumer of
`src/comms` (post PR 2) is `src/bot/telegram_query_bot.py`, which is a
sibling unit to the trader.

## 6. Idempotency & safety

| Hazard | Guard |
|---|---|
| Bot restart re-sends an already-delivered question | `status == sent` check before delivery; bot reads `delivery.send_attempts` and refuses if > 0 unless explicitly told to retry |
| Two poll-loop ticks fire concurrently | Single-threaded asyncio loop in PR 2; each request file is read-then-written atomically (`tempfile + os.replace`) |
| Operator answers the same question twice | Last write wins per `question_id`; `partial → complete` transition is monotonic |
| Malformed JSON in `comms/requests/` | `RequestStore._iter_files` swallows the exception, logs WARNING, skips the file. Bot's poll loop never crashes on a single bad artifact. |
| Stale request never answered | `expires_at` + bot's expiry sweep. `default_on_timeout` controls behaviour. |
| Bot crashes mid-write | `tempfile + os.replace` is atomic. A leftover `.tmp` file is safe to delete; the original artifact is intact. |
| Claude races the bot | Both write to the same artifact. Convention: only the bot writes `delivery` and `response`; only Claude writes initial fields. The bot's transition validator (`can_transition`) refuses out-of-order writes. |
| Self-triggered ping loop | Response-writeback commits use the prefix `comms(response):` which `notify_on_pull.py`'s filter list ignores (PR 2 task). |

## 7. Logging

Two log surfaces:

- **`comms/log.ndjson`** — one JSON event per line. `log_event()` is
  best-effort and never raises. Events: `request_created`,
  `request_sent`, `answer_received`, `request_answered`,
  `request_acknowledged`, `request_expired`, `request_cancelled`,
  `error`. The file is gitignored — diagnostic only.
- **Per-artifact `history[]`** — committed to git, schema-validated,
  the auditable record. Every state transition is appended here.

PR descriptions and sprint logs **do not** carry comms traffic. The
comms log lives outside `docs/` so it cannot pollute checkpoint /
training / debug-memory docs.

## 8. Implementation phases

### PR 1 — foundation (this PR)

- [x] JSON Schemas (`comms/schema/*.json`).
- [x] Python module (`src/comms/`) with models, state machine, store, log.
- [x] `comms/` directory + operator README.
- [x] Architecture doc (this file).
- [x] Timer assessment (`comms-timer-assessment.md`).
- [x] Tests (parsing, state transitions, store ops, malformed handling).
- [x] **No** Telegram bot integration. **No** new polling. **No** behaviour
      change to the live system.

### PR 2 — Telegram bot integration (next session)

- [ ] `src/bot/comms_handler.py` — registers a `CommsPoller` async task
      with the existing `Application` in `src.bot.telegram_query_bot`.
- [ ] `CommsPoller`: every 60 s, list pending requests, deliver each
      via inline-keyboard menu, mark sent.
- [ ] `CallbackQueryHandler` for `comms:<request_id>:<question_id>:<choice_id>`
      callback-data tuples; routes to `RequestStore.attach_response`.
- [ ] "Other" path: callback `comms:...:OTHER` puts the operator into
      a per-chat free-text capture state; the next text message becomes
      the answer.
- [ ] Multi-question session: the bot sends one message per question
      OR a stitched menu, depending on count; uses
      `next_status_after_answer` to track completion.
- [ ] Repo writeback: `git add comms/requests/REQ-*.json && git commit
      -m "comms(response): <id>" && git push`. Push retries on rebase
      race. ``scripts/notify_on_pull.py`` is opt-in (only fires for
      ``[BLOCKED-PM]``, ``TRAINING-*`` prefixes, and ``CHECKPOINT_LOG.md``
      touches) so comms commits are naturally silent — PR 2 adds an
      explicit ``COMMS_RESPONSE_PREFIX`` constant + ``logger.info``
      audit line for forward compat.
- [ ] Expiry sweep: every poll, check `is_expired()` on awaiting
      requests, transition → `expired`, archive.
- [ ] Cancellation handler: artifacts manually edited to
      `status: cancelled` are archived on next poll.
- [ ] Integration tests that exercise the full callback round-trip
      with a mocked `Application` (matches `tests/test_telegram_*.py`
      patterns).
- [ ] Operator-facing doc updates: `comms/README.md` "stuck request"
      section gains real bot commands (e.g. `/comms_resend REQ-...`).

### PR 3 — operator hardening (only if PR 2 surfaces issues)

- [ ] `/comms_status` Telegram command listing active requests.
- [ ] `/comms_resend <id>` to manually re-deliver a stuck request.
- [ ] Optional 1-min `OnUnitActiveSec` for `ict-git-sync.timer` —
      see [`comms-timer-assessment.md`](comms-timer-assessment.md) §4.
      Operator decision, not a default change.

## 9. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Operator perceives latency as broken ("I tapped, nothing happened") | medium | PR 2 sends an immediate confirmation reply; bot answer-write is async but visible within 5 s |
| Schema evolution breaks live requests in flight | low | `schema_version` is checked on read; bumps require bot version match. Carry forward old version readers for one cycle. |
| Operator types secret material into "Other" free-text and it commits | medium | Document this in `comms/README.md` ("treat answers as public"). PR 2 redacts obvious secret patterns (regex on `[A-Z0-9]{32,}` etc.). Not a hard guarantee. |
| Multi-question request gets partially answered then ignored | low | `expires_at` + expiry sweep; the partial answers are preserved in `archive/` for forensics. |
| Bot push race against a Claude commit | low | PR 2 push wraps `git pull --rebase` with retry-on-conflict; comms artifacts are append-only by convention so rebases are clean. |
| Comms file corruption hangs the bot | low | `RequestStore._iter_files` skips malformed; poll loop continues. |

## 10. Authoring a request from a Claude session (preview, PR 2 lands the helper)

PR 2 will add a small CLI under `scripts/comms_ask.py`:

```bash
PYTHONPATH=. python scripts/comms_ask.py \
    --topic "Live mode for new account" \
    --context "Adding the BTC-only sub-account; should it default to live or paper?" \
    --question "mode" \
        --choice live --choice paper \
        --allow-other \
    --expires-in 24h
```

…which writes a fully validated `comms/requests/REQ-*.json` and commits
it. Until that ships, the same effect can be produced inline:

```python
from datetime import datetime, timezone, timedelta
from src.comms import Request, Question, Choice, RequestStore
from src.comms.models import make_request_id

req = Request(
    request_id=make_request_id(slug="acctmode"),
    topic="Live mode for new account",
    context="Adding the BTC-only sub-account; should it default to live or paper?",
    questions=[Question(
        question_id="mode",
        prompt="Default mode for the new account?",
        input_type="choice",
        choices=[Choice(id="live", label="Live"), Choice(id="paper", label="Paper")],
        allow_other=True,
    )],
    expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
)
RequestStore().create(req)
```

## 11. References

- Sprint prompt: telegram-communication-infrastructure (S-027).
- [`docs/claude/telegram-pings.md`](telegram-pings.md) — existing
  ping pipeline this builds alongside.
- [`docs/claude/repo-map.md`](repo-map.md) — Unit 5 (Telegram bot)
  context.
- [`comms/schema/request.schema.json`](../../comms/schema/request.schema.json) — canonical request contract.
- [`comms/schema/response.schema.json`](../../comms/schema/response.schema.json) — canonical response contract.
