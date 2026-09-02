▶️ **START** — Backlog drain #3

**Scope (exclusive): `docs/claude/performance-review-backlog.json`.** No other backlog file will be touched; `docs/claude/OPEN-ITEMS.json` is READ-ONLY for this session.

- Session: `session_01JXBmVC65hkkoSQ2LcV1ETY` (child of manager `session_011JWFxuYAaEQKCFCmG6gnHJ`)
- Branch: `claude/drain-perf-backlog-20260902`
- Base sha: `de61ead93b80ef5fb472bc1a1649e6b97edbf195`

**Denominator at base sha:** 111 rows total — 66 `resolved`, **45 unresolved (13 `open` + 32 `kept_open`)**.

Working class-first. Direct diag access is up (`served by https://ict-bot.duckdns.org`), so closures will rest on live fleet reads, not code citations.

⚠️ Noted for the fleet: `/api/diag/version` reports `git_sha=49f03e37`, `git_sha_on_disk=b466e327`, `restart_pending=true` — the live trader is running a sha older than its own working tree. Any closure resting on live behaviour is a statement about `49f03e37`, and I will say so.
