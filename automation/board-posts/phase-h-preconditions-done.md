✅ **DONE** · Phase H **preconditions** · session `session_01LvzsinECH8HPCyauVJZBZw`

Four branches, **three DRAFT PRs opened, none merged.** Tier-2 work — the manager owns merges.

| PR | Repo | What |
|---|---|---|
| **#10682** | Metis-Insights | db explorer: default-deny table allowlist + column redaction |
| **#10686** | Metis-Insights | every reference naming Streamlit/Android as a live consumer, corrected |
| **#10687** | Metis-Insights | three backlog findings from this work |
| ⚠️ **branch only** | ict-trader-dashboard | `claude/phase-h-preconditions-retire-streamlit` — **pushed, PR NOT opened** (see blocker) |

---

## 1 · The DB explorer (the higher-priority one)

**What was reachable, measured against the live host unauthenticated, before I changed anything:** `GET /api/bot/db/tables` → **200**, 22 tables, including `device_tokens` with its full column list `[id, token, platform, label, subscriptions, created_at, last_seen_at]`. No `Depends(require_session)`, and the read was `SELECT *`.

**Two corrections to the severity, both in the honest direction:**

1. **`device_tokens` is EMPTY (`rows = 0`)** — the standing "push channel is dark" finding. **No FCM token was actually exposed, because there are none.** An open *path*, not a realised *leak*. It becomes real the moment the operator opens the Android app once.
2. The bigger surface is the **other 21 tables**, which are not empty — `signals` **2,301,022** rows, `trades` 5,313, `balance_snapshots` 29,848. Full trading history, world-readable, public host. **Filed separately (#10687) rather than folded into the row** — that row's `done_condition` is about `device_tokens`, and widening it quietly would have buried the larger finding inside the narrower one.

**Fix:** table allowlist (default-deny) + column redaction, per the operator's decision. The redaction routes through `_columns`, which every other path derives from — so a redacted column is also **not filterable and not orderable**. That closes a real oracle: `filter_state` + `total` would otherwise let someone brute-force a hidden value one `LIKE` prefix at a time *without the row ever being returned*.

**The falsifier:** all **9** new `TestExposureContract` tests were run against the **pre-change** router and all 9 **fail** there. One plants a known token value and greps the response bodies — its failure on the old code is the direct demonstration that the old route served the raw token.

⚠️ **The row's live verification (a) is NOT satisfied and I have not claimed it is.** It needs the narrowed route *deployed* and re-probed. Exact post-deploy commands are in the PR body. **The row stays open.**

## 2 · The retirement — and what could NOT be retired

**Streamlit — genuinely retired, not banner-only.** `streamlit_app.py` (9,628 lines) → `archive/streamlit_app_RETIRED_2026-09-01.py`, byte-identical; the entry point is now a notice page. **Verified by AST, not grep** (grep matches the docstring describing the absence): only imports are `__future__` and `streamlit`; only URL literals point at the SPA. It **cannot** reach the live feed. Replacing rather than deleting is the point — Community Cloud serves the last good build of a deleted file, which would have left a live-looking dashboard still polling the bot.

**Doc corrections (#10686)** — CLAUDE.md's "two front-ends" opener, 20 diagram annotations, ARCHITECTURE-CANONICAL's transport table, api-tier-policy, 16 router docstrings. ⚠️ **One is a real defect, not tidying:** three places said **"CORS isn't load-bearing"** — true of Streamlit's server-side call, **false** of the browser-direct SPA. CORS is now load-bearing and a mistake there breaks the only consumer. Corrected at the middleware itself.

⚠️ **Android — NOT retired repo-side, and this is a hand-off, not an omission.** `ict-trader-android` is 🧊 ON ICE, reversible *"only on an explicit operator instruction"*, so I opened no work there. What I **can** evidence: `device_tokens` has **0 rows**, so its only write path (FCM registration) has no live device. What I **cannot**: prove it makes no *reads* — reads leave no queryable trace. **So I am not claiming Android's part (1) is demonstrated.**

## ⚠️ Blocker — needs someone with wider scope

**I could not open the PR in `ict-trader-dashboard`.** The branch is pushed and ready. All three paths failed: MCP `403`, no `gh` CLI, and a **proxy-level 403** (*"GitHub access is not enabled for this session"*) that is a *different* refusal from the MCP one. That repo has no `pr-opener.yml` relay — the bot repo solved this same 403 with committed relays and the dashboard repo never got one. Filed as `BL-20260901-NO-PR-PATH-INTO-ICT-TRADER-DASHBOARD-FROM-A-WEB-SESSION`. **The full intended PR body is in the branch's commit message.**

## Two other operator-only items

- **Delete the Streamlit Community Cloud app** at share.streamlit.io. Post-merge it redeploys the notice and reaches no bot data — but the app object still exists, and "retired" ≠ "deleted".
- **The ON-ICE banner is now internally stale**: it justifies the Android freeze with *"the operator is using the web app (**Streamlit** + the Svelte SPA)"*. Filed, not fixed — fixing it is work in the frozen repo.

## Correcting my own brief

My brief said **pytest is absent from the sandbox** and such failures are tooling. **Not accurate** — pytest is present; `fastapi`, `pandas`, `cffi`, `email-validator` were missing. I installed them and ran the suites for real (**31** and **442** passed) rather than shipping on the assumption. Flagging it so the next session doesn't skip validation on the same false premise. Separately, three collection errors (`test_main_loop` et al.) are a missing `ccxt` and **reproduce identically on `main` — verified by stashing**, not assumed.

**No merges. No work opened in the frozen Android repo. The 64 CI guards and the provenance layer untouched.**
