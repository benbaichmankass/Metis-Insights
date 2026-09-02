▶️ **START** · ping delivery ledger read surface · session `session_01TASYv35o6XciFnMC9wmnHi` · branch `diag-pending-pings-delivered-read-surface`

**Scope — one file:** `src/web/api/routers/diag.py` (`_LOG_FILES`), plus the `CLAUDE.md` `log_file` row that `test_diag_log_file_allowlist_coherence.py` pins in both directions and that therefore *must* ship in the same commit.

Posting through this relay because `add_issue_comment` returns **403 `Resource not accessible by integration`** on #6927 — the read-only-MCP write-scope case `board-post.yml` exists for. `create_pull_request` 403s identically, so the PR comes through `pr-opener.yml`.

## What and why

`scripts/notify_on_pull.py` writes the sha256 of every `docs/claude/pending-pings.jsonl` line it enqueues into `runtime_logs/pending_pings_delivered.txt`. That file is on **no read surface** — measured against the *running* web-api, not just source: `?name=pending_pings_delivered` → `unknown_log_file`, 45 names allowlisted, 0 ping/notify/delivery related, with `exit_loop_health` / `stray_oca_soak` / `pairs_soak` as positive controls so the negative has a denominator.

Consequence: *"the operator's 00:19:53Z work digest was delivered"* and *"the drain never ran"* are indistinguishable from outside. That is `BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE`, at least its fifth instance.

## ⚠️ One finding for anyone else touching this file

The obvious entry — `runtime_logs_dir() / "pending_pings_delivered.txt"` — is **wrong here**, and wrong in the direction that looks like an answer.

`notify_on_pull.py` hardcodes its own `REPO_ROOT / "runtime_logs"` and never calls the path helpers; it runs from `ict-git-sync.service`, which carries no data-dir drop-in. The diag reader runs in `ict-web-api.service`, which **does** carry `deploy/dropins/data-dir.conf`, so `runtime_logs_dir()` there resolves to `/data/bot-data/runtime_logs` — confirmed live, `?name=exit_loop_health` reports exactly that prefix.

So the helper form would point the read surface at a path **nothing writes** and serve a permanently-absent file, which reads as *"nothing was ever delivered"*. Same writer/reader split that hid the `ict-hourly-snapshot` balance stall for ~3 weeks (`BL-20260611-M15-2`). Shipped anchored to `repo_root()` instead — the reader follows the writer.

I did **not** move the writer onto the helper to "fix" this: moving the ledger empties it, and an empty ledger re-fires all 50 retained `pending-pings.jsonl` lines on the next pull — a ping storm at a sleeping operator, out of an observability PR. Reported as a follow-up in the PR body instead.

Tier-1 throughout. Not touching `config/`, the order path, `OPEN-ITEMS.json`, or any backlog file.
