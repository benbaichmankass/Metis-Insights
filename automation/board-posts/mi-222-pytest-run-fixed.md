## ✅ MI-222 — #11566 `pytest-run` FIXED, all four checks GREEN, `mergeable_state: clean`

**Session:** `session_01WmFdLwfq4U5aLLjFRDDy6b` · **PR #11566** · head **`e38855b8`**

### Checklist

| item | state |
|---|---|
| Diagnose #11566's red `pytest-run` | **done** — root cause reproduced locally |
| Fix it without weakening the invariant | **done** — return path untouched |
| #11566 green | **done** — 4/4, `mergeable_state: clean` |
| Merge #11566 | **YOURS** — Tier-2, R5 refuses self-landing |
| #11550 merge | **YOURS** — landed on main since (its tests are now on `main`) |

### The cause — reproduced, not guessed

```
tests/test_diag_log_file_allowlist_coherence.py
  ::test_every_allowlisted_log_file_is_documented
AssertionError: these log_file names are reachable but UNDOCUMENTED,
so a session reading the docs cannot know to ask for them
  ['position_read_state_soak']
```

My earlier commit added `position_read_state_soak` to `diag.py::_LOG_FILES` and to
**no doc**. That test is this repo's standing recurrence detector for exactly that
class. **The test was right and the diff was wrong**, so the diff moved — per your
instruction, and it never came near the invariant.

⚠️ **THE ONE INVARIANT DID NOT MOVE.** No line of `account_open_positions`'s read
or return path is touched by the fix. `TestReturnValueIsByteIdentical` still passes.

### What shipped (`e38855b8`, +5/-5 across 3 files)

1. **`docs/reference/bot-api-reference.md`** — the single documented home (the test
   asserts exactly one, so copies cannot drift). The name, plus a description
   saying which field a reviewer reads (`dropped_symbol_dedupe`), that `no_rows`
   and `could_not_look` are different facts, and that an **empty log is a deploy
   defect, not a quiet book**.
2. **`POSITION_READ_SOAK_FILENAME` → `POSITION_READ_SOAK_LOG_NAME`.** Not cosmetic.
   `test_every_declared_soak_log_has_a_read_surface` **derives** its population from
   `SOAK_LOG_NAME`/`_SOAK_NAME` constants, so the old name excluded my soak from
   that guard **by naming accident alone**. Measured, not read: the derived set goes
   **12 → 13** and now contains `position_read_state_soak`, so deleting the
   `_LOG_FILES` entry later fails there instead of shipping an unreadable soak.

### Verification

- **CI 4/4 green** on `e38855b8`: `guards`, `pytest-collect`, `pytest-run`
  (17:20:05Z → **17:38:02Z**), `repo-inventory`. `mergeable_state: clean`.
- `run_guards.py --base main` → **PASS 72 · FAIL 0 · SKIP 20**.
- **Full local suite: 15,799 passed.** The 25 failures + 3 errors are ALL missing
  third-party packages in this sandbox (`ccxt` ×23, `sklearn` ×5) — and that is a
  **control, not an assumption**: the identical failures reproduce on clean
  `origin/main` **without my diff** (`test_set_leverage_preflight` 4 failed,
  `test_ib_sizing_and_data` 10 failed, `test_exit_loop_wiring` 1 error).

### Two things I want on the record

1. **I could not read the CI logs** — `get_job_logs` is not in this session's tool
   set. I found the failure by local reproduction. The match is exact, and CI is now
   green on the fix, but the diagnosis began as inference.
2. **The sandbox was masking it.** `cffi`, `email-validator`, `pandas` and `psutil`
   were all absent, so the suites that mattered died at *collection* and the real
   assertion never ran. My first full-suite run was also invalidated when I switched
   branches mid-run; I discarded it and re-ran rather than quote it.

**No order was placed, modified or cancelled on any account.**

⚠️ Posted from a **separate branch**, deliberately: a board post on #11566's own
branch would land the relay's `github-actions[bot]` results commit as that PR's head
and **re-bury the green checks** (the documented `board-post.yml` trap).
