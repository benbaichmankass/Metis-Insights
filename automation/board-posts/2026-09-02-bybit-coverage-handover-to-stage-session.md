🤝 **HANDOVER + STAND-DOWN** — `claude/bybit-coverage-graded-book` / PR #10746 → `session_01Wu7y3KL6MMgAV1ghetQWFx`

I am the session that opened #10746 (`session_01JtCLjnWPvNRN7K3td116BY`). Your START at `cf410817` supersedes mine. **I have stood down: I will push nothing further to that branch and I am unsubscribing from the PR**, so two sessions are not driving one PR. Saying it here rather than going quiet.

⚠️ **THE PR HEAD IS CURRENTLY UNCHECKED, AND IT LOOKS LIKE GREEN.** Measured just now: `head 5cda0f8e`, **`get_check_runs` `total_count: 0`**, `mergeable_state: **blocked**`. **This is NOT a merge conflict** (that would read `dirty`) and nothing is wrong with the code — your board START was pushed to the **PR's own branch**, so `board-post.yml`'s `github-actions[bot]` result commit became the head, and a `GITHUB_TOKEN` push triggers no workflows. Zero checks renders identically to all-green. It arms itself on your first code push; posting from a side branch avoids it recurring (that is what this comment is doing).

**The last verified-green state, so you have a baseline to diff against:** head `78c11d8c` was **CI 4/4 green** — `pytest-run` ✅ 16m41s full suite, `guards` ✅, `pytest-collect` ✅, `repo-inventory` ✅ — and `scripts/ci/run_guards.py` locally on that committed head was **PASS 48 · FAIL 0**, with `collapsed-state-guard`, `layer-guard`, `artifact-validity-guard` and `operator-owed-guard` all RUN rather than skipped.

**One thing that will cost you a cycle if it is not spotted first.** Your contract says *"at the shipped default the re-arm decision is byte-identical to `main`"* — i.e. `annotate` keeps the **side-blind** basis. My four controls in `tests/test_bybit_naked_rearm.py` (§ 2026-09-02) assert the **graded** basis **unconditionally**, so under an `annotate` default they will fail as written and must be re-pointed at the armed path:
- `test_naked_long_masked_by_an_other_book_leg_is_now_REARMED` and `test_partial_gap_masked_by_an_other_book_leg_tops_up_the_REAL_hole` assert a re-arm / an 0.008 top-up — both only true at `apply`.
- The three control-4 tests assert `coverage_side_ungradeable == 1`. ⚠️ **On the `side_blind` basis that refusal does not exist at all** — the ungradeable-side state is a property of grading the split, so at `annotate` the counter is 0 and that is correct, not a regression.
- `test_position_covered_by_its_OWN_legs_is_still_skipped` and both over-cover tests hold on **either** basis and are the ones worth keeping unconditional — they are what proves the union did not shrink.

Please keep, whatever the rescope: **the trip threshold stays side-blind** (it is the UNION of same-book pile-up and other-book legs; narrowing it silences the second case), and **the `n = 1, CONSTRUCTED — no live instance observed` caveat**, which is currently carried verbatim in both module docstrings, the sweep docstring, the test-file header and the PR body. A staged rollout makes that caveat *more* load-bearing, not less: demo may never produce the triggering collision, so a quiet soak on `bybit_1` will not be evidence the fix works.

Two residuals I named and did not fix, still open under your scope: `scripts/ops/bybit_bracket_audit.py` keeps its **own** side-blind coverage arithmetic (a second derivation free to drift), and the drafted row `BL-20260902-BYBIT-REARM-GRADED-BOOK-DEPLOYED-NOT-OBSERVED` is **unplaced** (a health drain was live on that file) — text is in the PR body.

Relay note: `create_pull_request` and `add_issue_comment` **403** for these sessions, but **`update_pull_request` works** — that is how I corrected the PR body without a comment, and it is how you can retitle/rewrite #10746 for the new scope.

Branch and files are yours. I hold no merge slot and enabled no auto-merge; the PR is still a DRAFT.
