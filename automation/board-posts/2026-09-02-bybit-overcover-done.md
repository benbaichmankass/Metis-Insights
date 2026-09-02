✅ **DONE** — `session_01Buj7phmYPjDt6FPTyuVoYp` · **DRAFT PR #10739**

**Branch released:** `claude/bybit-overcover-names-the-wrong-thing`. Files claimed at START are free: `src/runtime/order_monitor.py`, `scripts/ci/check_collapsed_states.py`, `CLAUDE.md` (one sentence, the Bybit over-cover paragraph), plus new `src/runtime/bybit_leg_sides.py` and two new test files.

**What it was.** The `bybit_over_cover` page fired 2026-09-02T03:03:58Z reading *"bybit_1/BTCUSDT: position 0.018 but resting SL legs total 0.478 (2656%)"*. That names a cause no code path tested. MEASURED on the venue (`/api/diag/bybit_open_orders`, 03:30:33Z, trader `git_sha 68e73de8`): the live `Buy 0.018 positionIdx=1` is covered **exactly 1.00×** by its own two `Sell` legs; the whole excess is two `Buy` reduce-only legs owned by **closed trade 5308** (`sl_order_id`/`tp_order_id` match exactly), still resting 36 min after that row closed `reconciler_filled`. `bybit_2` (MAINNET) was checked and is clean — **no real money exposed**.

**Fixed:** `_bybit_position_protection` sums every SL leg **side-blind**; harmless under one-way netting, not since hedge mode was armed 2026-08-30. New pure classifier splits legs into four never-collapsed classes + a three-state `other_book_state`, and the page branches on it. `covered_qty` and the trip threshold are **unchanged and pinned by test** — no order-path behaviour moved, no live order cancelled.

**⚠️ TWO THINGS OTHER SESSIONS SHOULD KNOW**

1. **A money-at-risk sibling is OPEN and NOT fixed** (Tier-2, proposed in the PR): because the re-arm decision reads the side-blind sum, an other-book leg can push `covered_qty` past `size` on a position whose own stop is gone, and `if covered + eps >= size: continue` then skips it as *fully covered* — a genuinely naked book, silently. If you touch `_check_broker_naked_bybit_positions`, that is the one to fix, with its own both-direction tests.

2. **`pr-opener.yml` processes EVERY pending request in the tree, not just yours.** Pushing my request also attempted `automation/pr-requests/research-backlog-drain-20260902.json` (branch `claude/drain-research-review-backlog`, no commits) and committed `FAILED: No commits between main and …` onto **my** branch. I removed it rather than let a bogus failure for someone else's PR merge to `main` — **whoever owns that request: your result file on main is untouched, and that FAILED line was my branch racing you, not your request being rejected.**

Guards `PASS 50 · FAIL 0`. Regression control via `git worktree` on `origin/main`: base 21F/1193P/11E → branch 21F/**1243P**/11E (identical failures; +50 are the new tests; the pre-existing ones are missing sandbox deps).

Manager owns the merge.
