# `automation/session_handoff/` — bounded sprint continuation state

This directory is the **machine-readable handoff area** between Claude Code
sessions that are working through a single pre-planned sprint together.

It is intentionally separate from:

- `comms/` — operator ↔ Claude Q&A artifacts (different concern; the
  operator is the human in the loop).
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — the human-readable
  append-only checkpoint narrative. The handoff file in this directory
  is the **structured** counterpart that automation can act on.

Operator-facing documentation lives in
[`docs/claude/session-handoff.md`](../../docs/claude/session-handoff.md).
This README is the in-tree TL;DR.

---

## Layout

```
automation/session_handoff/
├── README.md                      ← you are here
├── next_session.json              ← live handoff for the active sprint
├── schema/
│   └── handoff.schema.json        ← JSON Schema (v1)
└── examples/
    └── example_handoff.json       ← starter file matching the schema
```

`next_session.json` is the **single canonical handoff file** for the
current sprint. There is one of these at a time. Older handoffs live
in git history; we don't accumulate parallel files here.

## What it is — and what it isn't

This is for **a specific pre-planned sprint** that exceeds one Claude
session's comfortable context window. It lets a session stop at a clean
checkpoint, write down exactly what the next session needs, commit, push,
trigger a GitHub workflow, and exit.

It is **not** an always-on autonomous planner, a roadmap runner, or a
perpetual self-directed agent loop. The sprint scope comes from a sprint
plan. When the sprint is done, the handoff file is removed (or left with
`ready_for_continue: false`) and the routine ends.

## Lifecycle

1. A sprint is planned the usual way (sprint plan doc, branch, etc.).
2. A Claude Code session starts work on the sprint.
3. When the session decides it must stop (context-limit near, session
   too long, fragmented state, blocked, or just at a clean checkpoint),
   it runs the close-session helper:

   ```bash
   python scripts/session_handoff/close_session.py \
       --sprint-id S-061-session-handoff \
       --sprint-title "Session handoff + continue-work workflow" \
       --reason natural_checkpoint \
       --dispatch
   ```

   The helper validates the live `next_session.json`, commits any
   updates, pushes the branch, and (with `--dispatch`) triggers
   `.github/workflows/continue-work.yml` via `repository_dispatch`.
4. The next session pulls the branch, reads `next_session.json`, and
   resumes from the first entry of `next_actions`.
5. When the sprint is finished, set `ready_for_continue: false` and
   append a `sprint_closed` event to `history` in the final commit.
   The continue-work workflow refuses to dispatch on a closed handoff.

## Validation

Validation is enforced both client-side (the helper) and CI-side (the
workflow). Anything that doesn't conform to `schema/handoff.schema.json`
is rejected before push.

```bash
python scripts/session_handoff/validate_handoff.py \
    automation/session_handoff/next_session.json
```

## Safety

- **Repo is the source of truth.** No hidden state outside the repo
  except the standard GitHub Actions dispatch metadata that the
  workflow itself emits.
- **No secrets.** Never write `.env` content, tokens, or PII into
  `continuation_prompt`, `commands_to_run`, or any other field. The
  handoff file is committed and visible to anyone with repo access.
- **The trader does not read this directory.** No code under
  `src/runtime/` or `src/units/` imports anything from
  `automation/session_handoff/`. Live trading is unaffected.
