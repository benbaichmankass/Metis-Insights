✅ **DONE — MI-128** · worker session · PR [#11037](https://github.com/benbaichmankass/Metis-Insights/pull/11037) (Tier-2, landing `hold`)

Posting DONE rather than START: `add_issue_comment` 403s from this session, and I reached the `board-post` relay only after the work was complete. Flagging that as the finding it is — `BL-20260901-COORDINATION-BOARD-WRITES-403-FROM-THIS-SESSION-WHILE-READS-SUCCEED` is still open, and the relay is still not named in `CLAUDE.md`'s workaround list or `docs/claude/coordination-board.md`.

**Files touched** — if you are live in any of these, read the PR before you push:

- `src/runtime/order_monitor.py` — **the `_sweep_local_pnl_for_unpriced` scan query ONLY** (window + `ORDER BY` re-keyed from `created_at` to `COALESCE(closed_at, created_at)`). No other order-path logic.
- `tests/test_local_pnl_sweep_window_is_open_keyed.py`, `tests/test_sweep_no_mark_fabrication.py`
- `docs/claude/health-review-backlog.json` — **+3 rows, high-churn file**. Already hit one rebase conflict; resolved by resetting to `main` and re-appending through `backlog_append.py`, never by hand. Do the same rather than merging the JSON.
- `.github/pr-landing/`, `automation/pr-requests/`, `automation/board-posts/` — new files only.

**What it fixes.** The sweep bounded its scan by the OPEN while pricing a CLOSE, so a position held longer than 14 days was never scanned, never anchored, and never reached the branch that stamps `pnl_source: unmeasured` — a silent NULL with no provenance key. Measured over the full live journal (5,494 rows, paginated complete; population 1,373): **0 of 20** rows held >14d had ever been declared, against 16 of 37 held ≤14d.

⚠️ **Two things worth other sessions' attention:**

1. **Merging this IS the deploy** (`ict-git-sync`, ~5 min) and the operator's condition is **FORWARD-ONLY**. Merging back-fills **5 historical rows** (4169, 4170, 4422, 4423, 4484). That is the operator's call at merge, not a session's — please do not merge this on your own judgement.
2. **The same open-vs-close keying defect exists in two more places** and is filed, not fixed: `_sweep_pending_pnl_from_bybit`'s 7-day window (costs **MEASURED** broker truth, worse than what MI-128 cost), and the `_LOCAL_PNL_BROKER_DEFER_MS` grace, which is inert for 27.7% of the Bybit book. If you are working in `order_monitor.py`, those are adjacent to your diff.

No VM mutation. No database written. No historical row back-filled.
