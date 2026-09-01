▶️ **START** · Phase H **preconditions only** · session `session_01LvzsinECH8HPCyauVJZBZw` · branches `claude/phase-h-preconditions-*`

Posting via this relay because `add_issue_comment` returns **403 "Resource not accessible by integration"** — the same read-only-MCP case `session_01MDjxAnncsh71UiRAWtYHkH` hit at 21:22Z today, so it is the condition `board-post.yml` exists for.

**Board tail read first:** page 19 @ `perPage=100` returned **70 items — a short page, which is the proof of end** (`BL-20260817-BOARD-TAIL-READ-CANNOT-ASSERT-IT-REACHED-THE-END`). Latest was the #10676 merge-claim audit at 21:23:20Z. One live session — `session_01MDjxAnncsh71UiRAWtYHkH` on Phase F / C3 (`strategy_review_packet.py`, `comms/strategy_reviews/`) — **no overlap with this scope.**

## Scope

The two PRECONDITIONS Phase H names. **Phase H itself is NOT mine** (decisions from the UI, attaching `require_session` to the read surface) — I am clearing its path.

| Row | Files I will touch |
|---|---|
| `BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN` | `src/web/api/routers/db_explorer.py`, its tests, `docs/api-tier-policy.md` |
| `BL-20260901-RETIRE-ANDROID-AND-STREAMLIT-FROM-THE-LIVE-FEED` | `CLAUDE.md` (§ Dashboard consumer / § Dashboard REST API / § CORS), `docs/ARCHITECTURE-CANONICAL.md`, `docs/api-tier-policy.md`, router docstrings under `src/web/api/routers/` |

Separate PRs per row. **Narrowing a live API route is Tier-2 → both open as DRAFT for operator approval; I do not merge.** Not touching the 64 CI guards or the provenance layer (build plan § "What is NOT built"). Not opening work in `ict-trader-android` — that repo is ON ICE, and retiring it *from the live feed* is not the same as working in it.

## ⚠️ Stating the live exposure plainly before I change anything, per the row

Measured against the live host just now, **unauthenticated, no bearer** — `GET https://ict-bot.duckdns.org/api/bot/db/tables` returns **200** with **22 tables across `trade_journal` + `trainer_store`**, including:

```
device_tokens   columns = [id, token, platform, label, subscriptions, created_at, last_seen_at]   rows = 0
```

`db_explorer.py` has **no `Depends(require_session)`** and its table read is `SELECT *`, so the raw `token` column is in the projection. The asymmetry the row names is real: the dedicated `/api/bot/devices` route is token-gated via `DASHBOARD_API_TOKEN` **and** deliberately returns only `token_suffix` (last 8 chars), while the generic explorer reaches the same table unredacted and ungated.

**Two corrections to the severity, both in the honest direction:**

1. ⚠️ **`device_tokens` is EMPTY today (`rows = 0`)** — consistent with the standing "push channel is dark, 0 registered devices" finding. **No FCM token has actually been exposed, because there are none.** This is an open *path*, not a live *leak*, and I will not describe it as one. It becomes a real leak the moment the operator opens the Android app once.
2. The much larger unauthenticated surface is the **other 21 tables**, which are not empty: `signals` **2,301,022** rows, `insights_usage` 125,526, `insights_history` 125,420, `balance_snapshots` 29,848, `dataset_builds` 13,967, `trades` 5,313, `order_packages` 4,272. Full trading history is world-readable on a public host today. That is outside this row's `done_condition` but it is the reason the allowlist inversion matters more than the one redaction.

I did **not** fetch the contents of `device_tokens` — the column schema above came from the table *listing*, and retrieving rows from a token table is not something I should do to prove a point that the schema plus `SELECT *` already proves.

**Fix per the row's `done_condition` (operator decision 2026-09-01): table ALLOWLIST + COLUMN REDACTION**, with the inversion as the point — a table added to the schema *without* being added to the allowlist must be **unreachable**, so a new table is invisible until someone admits it deliberately. Also correcting `db_explorer.py`'s docstring, which currently asserts *"No secrets live in either DB"* — that premise is false and the next person to add a table would read it and trust it.

⚠️ **The row's live verification `(a)` cannot be satisfied from a draft PR** — it requires the narrowed route to be *deployed*, and deploy is the operator's. The PR will carry the pre-state evidence above plus the exact post-deploy commands, and the row stays open until that runs. I will not mark it done off the diff.

Will post `✅ DONE` with PR numbers.
