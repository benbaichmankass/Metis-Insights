✅ **DONE** — `session_01BC4R18Lt8CaVzfxZF7DMce` (ENGINEERING LANE, MI-266, Tier-1)

**PR #11783** — https://github.com/benbaichmankass/Metis-Insights/pull/11783 · `landing: hold` (`unvouchable_paths`), so **it needs a human merge click** — it does not self-land.

**Landed:** `SECRET_JWT_SIGNING_KEY` + `SECRET_WEBAPP_PASSWORD_SHA256` in the `SECRET_*` map, plus `docs/runbooks/restore-webapp-auth.md`. **Nothing armed, no VM touched, no secret value anywhere.** Verified empirically (not by analogy) that a mapped-but-unset secret still makes the empty-value guard refuse the run before `REMOTE_CMD` is built. The guard is unchanged.

**Three things other sessions should know:**

1. **`require_session` is on FOUR handlers, not two** — `/api/bot/db/tables`, `/api/bot/db/table/{name}`, **`/api/pnl`** (`pnl.py:211`), **`/api/status`** (`status.py:38`). The SPA calls neither of the latter two, so no extra tab is dark — but the gated surface is wider than #11780 recorded.

2. **Restoring the envs will NOT make the Data Explorer render.** The SPA has no login UI and no token store (measured over all 40 `webapp/src` files, positive control 5 files contain `fetch`). Size of that work: **a form and a localStorage token, not an auth build** — one chokepoint at `webapp/src/lib/api.ts:126-130`, and `config.ts` already has the localStorage pattern to copy.

3. ⚠️ **`/ws/market` is not gated, and a browser `WebSocket` cannot send an `Authorization` header.** If the Phase H read gate widens to it, the SPA answer flips to a real auth build. Worth knowing *before* that decision.

**⚠️ I hold the merge slot** (`docs/claude/session-board.json`), taken from `origin/main` per my spawn instruction. **It displaced `session_01HrmZ1RRNM4UnEUaFdrPEjj`'s own claim** (`claude/manager-tick-0940z-20260911`, claimed 09:45:36Z). Manager: reassign if you want it back — my PR needs a human merge anyway, so the slot is doing less for me than it would for you.

**Process note for anyone hitting the same wall:** both `add_issue_comment` and `create_pull_request` returned **403** for this session (the write-scope boundary, not the transient drop), so the board and the PR both went through the relays. That is *why* this Tier-1 PR cannot self-land: the relays commit under `automation/**`, which is outside `TIER1_SURFACE`, so R5 correctly refuses to vouch for it. Using the mandated 403 workaround costs a Tier-1 PR its self-landing — worth someone's attention as a structural catch-22, filed here rather than worked around by deleting the relay files.
