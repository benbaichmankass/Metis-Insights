▶️ **START — MI-126 (sunset pass: an absence is not a measured zero)**

- **Branch:** `claude/mi126-sunset-pass-absence-not-zero`
- **PR:** #11020
- **Session:** `session_01L1MbHt6xogVvRn3zvy23pB` (sub-session, dispatched by the manager)
- **Unit contract:** `WO-20260905-SUNSET-PASS-MANUFACTURES-A-ZERO-FROM-AN-ABSENCE` — ⚠️ **this file does not exist on `main`**; the registry row is `pending-20260905T020449Z` in `docs/claude/work/SESSIONS.json`.

**Scope I am touching:**
- `scripts/ops/sunset_pass.py` — the per-leg lifetime default at the old line 288, and the false comment defending it
- `tests/test_phase_g_sunset_and_pull.py` — two added tests
- `docs/claude/diagnoses/MI-126-sunset-absence-not-zero.md` — the change note (the PR body could not be set; see below)
- `.github/pr-landing/` + `.github/pr-automerge-requests/` — the Tier-1 landing pair

**Explicitly NOT touching:** `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml`, any order path, `src/web/api/routers/performance.py`'s SQL, and `docs/claude/SUNSET-DISPOSITIONS.json`.

⚠️ **NOTHING IS RETIRED, DISABLED OR SHADOWED.** This is a measurement repair. Its effect is to REMOVE 8 retirement proposals that were manufactured from an absence (`retire_candidate` 9 → 1 over 52 enabled legs).

**Reads only:** `GET /api/bot/performance?window=all` and `GET /api/bot/db/table/trades` on `https://ict-bot.duckdns.org`. No VM mutation, no system-action dispatched.

⚠️ **Board note, recurrence of MI-124 §9:** `add_issue_comment`, `create_pull_request` and `update_pull_request` all returned `403 Resource not accessible by integration` from this session, while `issue_read` / `list_pull_requests` succeeded — the write-scope boundary, not the transient MCP drop. `claude-pr-automerge` won the race and opened #11020 titled from the head-commit subject with a boilerplate body, and `update_pull_request` cannot correct it. The real rationale is therefore committed to the branch as `docs/claude/diagnoses/MI-126-sunset-absence-not-zero.md` — **read that, not the PR body.** Already filed as `BL-20260905-AUTOMERGE-RELAY-WINS-THE-RACE-WITH-PR-OPENER-SO-EVERY-TIER-1-PR-GETS-A-BOILERPLATE-BODY`; not re-filed.
