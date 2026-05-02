# `comms/` — operator communication channel

This directory is the **isolated communication area** between Claude (the
agent that writes code in this repo) and the human operator (Ben).

It is intentionally separate from PR descriptions, sprint plans, and
trading logic. Nothing in `comms/` ever influences the trader. Everything
in `comms/` is just a structured ask/answer log between two humans (one
of whom happens to be an LLM).

The full architecture lives in
[`docs/claude/comms-architecture.md`](../docs/claude/comms-architecture.md).
This README is the operator-facing TL;DR.

---

## Layout

```
comms/
├── README.md                 ← you are here
├── requests/                 ← active requests (one JSON file each)
│   └── REQ-YYYYMMDD-HHMMSS-<slug>.json
├── archive/                  ← closed/expired/cancelled requests
│   └── REQ-...json
├── log.ndjson                ← append-only event log (gitignored)
└── schema/
    ├── request.schema.json   ← what Claude writes
    └── response.schema.json  ← what the bot writes back
```

## How it flows

1. **Claude writes** a `comms/requests/REQ-*.json` artifact with
   `status: "pending"` and one or more questions. Commits it. Pushes.
2. **The VM pulls** (`ict-git-sync.timer`, default 5 min — see
   [`docs/claude/comms-timer-assessment.md`](../docs/claude/comms-timer-assessment.md)
   for the 1-min feasibility note).
3. **The Telegram bot** picks up the new pending request, sends it to
   you as an inline-keyboard menu, and flips `status: "sent"`.
4. **You answer** in Telegram. Multiple choice → tap a button. "Other"
   → tap "Other" then type a reply. Free text → just type a reply.
5. **The bot writes** your answer into the same JSON file under
   `.response`, sets `status: "answered"` (or `"partially_answered"` if
   it's a multi-question request and only some are done), and commits.
6. **The VM pushes** the commit. Claude reads the answer on its next
   sync and acts on it.

## States at a glance

| Status | Means |
|---|---|
| `pending` | Claude wrote it; bot has not delivered yet |
| `sent` | Bot delivered it; awaiting your reply |
| `partially_answered` | Multi-question request; some answered |
| `answered` | All required questions answered |
| `acknowledged` | Claude saw the answer; archived |
| `expired` | TTL elapsed without a complete answer |
| `cancelled` | Withdrawn before delivery |

## Stuck request? How to recover

1. **You missed a Telegram menu.** It's still in `comms/requests/`.
   The bot will *not* re-send unless you ask it to (idempotency).
   The recovery commands live in the bot itself (PR 2). Until then,
   manually edit the file's `delivery.send_attempts` to `0` and
   `status` back to `pending` — the next bot poll resends it.
2. **The bot sent garbage / wrong question.** Set `status: "cancelled"`
   in the JSON file, commit, push. The artifact moves to `archive/` on
   the next bot poll and is no longer in scope.
3. **The bot crashed mid-write.** Look for `.REQ-*.json.*.tmp` files in
   `comms/requests/`. Safe to delete — writes are atomic, so a leftover
   tmp file means the final replace never happened. The original
   artifact (if any) is intact.

## Safety

- **No secrets in this directory.** The bot enforces an HTML-escape on
  any operator free-text before it writes; even so, treat answers as
  public (they ship in commits).
- **One question batch per file.** The bot will refuse to deliver any
  artifact whose `schema_version` is unknown — that's your forward
  compat guard.
- **The trader does not read this directory.** No code path under
  `src/runtime/` or `src/units/` imports `src.comms`. Live trading is
  unaffected by anything in `comms/`.
