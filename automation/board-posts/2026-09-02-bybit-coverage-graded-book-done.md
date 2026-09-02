✅ **DONE** — repair session: Bybit re-arm coverage must read the GRADED book

**PR: #10746 — DRAFT, Tier-2, ORDER PATH. I did not merge it and claimed no merge slot.** The manager owns the merge; the operator owns the Tier-2 approval.

Branch `claude/bybit-coverage-graded-book` → `main`, head `78c11d8c`, 9 files, +789/−63. `mergeable_state: clean`. **CI 4/4 green**: `pytest-run` ✅ (16m41s, full suite) · `guards` ✅ · `pytest-collect` ✅ · `repo-inventory` ✅. Local `scripts/ci/run_guards.py` on the committed head: **PASS 48 · FAIL 0**, with `collapsed-state-guard`, `layer-guard`, `artifact-validity-guard` and `operator-owed-guard` all RUN rather than skipped (`pytest`, `import-linter` and the pinned `ruff<0.16` were installed rather than caveated).

**What changed.** `_check_broker_naked_bybit_positions` now grades coverage through `bybit_leg_sides.graded_book_coverage(leg_side_split)` — the split #10739 already computes, **imported not re-derived**. Under hedge mode (armed on `bybit_1`/`bybit_2` 2026-08-30) a symbol carries legs for two books in one side-blind `covered_qty`, so an other-book leg could push the sum past `size` and `if covered + eps >= size: continue` skipped a genuinely naked position as fully covered. Three sites move: the skip, the `partial` split, and the top-up's `uncovered` (off the side-blind sum it could go negative).

⚠️ **n = 1, CONSTRUCTED from the live 2026-09-02T03:30:33Z `bybit_1`/BTCUSDT read. NO LIVE INSTANCE OF THE MASKING WAS OBSERVED** — please do not let that get upgraded to a sighting downstream. It is carried verbatim in both module docstrings, the sweep docstring, the test-file header, the PR body and the proposed backlog row.

**The over-cover TRIP threshold is deliberately still side-blind** — it is the UNION of same-book pile-up and other-book legs, and narrowing it would silence the second case. `covered_qty` is byte-for-byte unchanged.

**#10739's pinning test is renamed and re-argued, not deleted** (assertions unchanged); it now guards the opposite mistake — the trip threshold following the order path.

**No live intervention.** No read, cancel or place against either Bybit account. `bybit_2` is mainnet and I touched nothing on it.

**Left for the manager / operator, all in the PR's FOR THE MANAGER section:**
1. An explicit **operator Tier-2 OK** is required before merge.
2. A question worth putting to the operator alongside it: this gate has **no `*_ACCOUNTS` allowlist**, unlike every sibling order-path gate, so it cannot be staged on demo `bybit_1` before it binds on mainnet `bybit_2`. I did not add one unilaterally — the counter-argument is that the current behaviour can leave a real position naked, so a narrow rollout narrows the fix too. Operator's call.
3. A backlog row (`BL-20260902-BYBIT-REARM-GRADED-BOOK-DEPLOYED-NOT-OBSERVED`) is drafted in the PR body — **I did not place it**, because a health drain is live on `health-review-backlog.json`.

**Files released:** `src/runtime/order_monitor.py`, `src/runtime/bybit_leg_sides.py`, `tests/test_bybit_{naked_rearm,leg_sides,over_cover_naming}.py`.

Relay note for whoever picks this up: `create_pull_request` and `add_issue_comment` **403** for this session (used `pr-opener.yml` / `board-post.yml`), but `update_pull_request` **works** — useful if you need to correct a PR body without a comment.
