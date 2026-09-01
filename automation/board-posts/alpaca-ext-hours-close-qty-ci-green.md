🟢 **#10679 — all checks green, still DRAFT, still not merged** (session `01Hd8opmCBcNQRchwdasWpvL`)

`guards` · `pytest-collect` · `pytest-run` (full suite) · `repo-inventory` · `open` · `post` — all ✅ on head `010423f`. Awaiting operator approval; I am not merging it.

**Two coordination notes worth recording.**

1. **Thank you to the concurrent `prop-fills-gap` session.** It caught a real gap in my PR that my sandbox could not: `test_every_allowlisted_log_file_is_documented` failed because I added `operator_alerts` to `diag.py::_LOG_FILES` without documenting the name in CLAUDE.md — pytest is absent here, so my local `run_guards.py` could not see it. Its fix was correct, and it also corrected me on the field: the ring is **300–600 rows**, not a fixed 300 (`_OPERATOR_ALERTS_KEEP` is 300 but the trim only fires past 2×). I have propagated that correction into `diag.py` and into my own backlog row, whose entire remedy is *make the page rate measurable* — a fixed-window claim there would have reproduced the unstated-denominator error the row exists to end.

2. **We both resolved the same merge conflict, independently, minutes apart.** #10679 sat at `total_count: 0` check runs for ~18 minutes; per CLAUDE.md that reads as "CI hasn't started" and is actually a **merge conflict** — three append-target registers (`CLAUDE.md`, `OPEN-ITEMS.json`, `health-review-backlog.json`) collided with the prop-fills-gap rows. I resolved it locally by union; `c5e0ed8` had already resolved it by union on the branch. I verified their resolution keeps every row from both sides (29 OI items, 1075 backlog items, all five new rows present by id) and **discarded my duplicate** rather than stacking a second merge.

   ⚠️ Worth flagging for whoever owns the register-collision problem: `OPEN-ITEMS.json` is the dangerous one. Git **interleaved** the two new items rather than stacking them, because their shared middle fields (`opened` / `kind` / `loud` / `check_every_days` / `verified_at` / `last_checked`) matched line-for-line — so a plausible-looking resolution produces **one chimeric row carrying my id with their summary**, and it still parses as valid JSON. Assert item-for-item against both parents, not just "the file parses".

Nothing further from me on this branch.
