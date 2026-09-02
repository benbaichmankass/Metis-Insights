## ✅ DONE — bybit graded-book coverage is now STAGED behind an account allowlist (PR #10746, still DRAFT)

**Session:** `session_01Wu7y3KL6MMgAV1ghetQWFx` · **Branch:** `claude/bybit-coverage-graded-book` · releases the START at [#6927 issuecomment-5504926945](https://github.com/benbaichmankass/Metis-Insights/issues/6927#issuecomment-5504926945)

**Claim released.** No merge slot was ever claimed and none is needed — the PR stays DRAFT and the manager merges.

### What landed
The operator's Tier-2 decision (*stage on `bybit_1` demo first*) is now a property of the system rather than a sentence in a doc. `BYBIT_GRADED_COVERAGE_MODE` (`off`/`annotate`/`apply`) + `BYBIT_GRADED_COVERAGE_ACCOUNTS`, following the `PROTECTION_REASSERT_ACCOUNTS` polarity: **empty means NONE**, and there is a test whose only job is to fail if that is ever harmonised toward `CONVICTION_SIZING_ACCOUNTS`' empty-means-ALL.

The allowlist scopes the **binding**, never the **measurement** — every Bybit account is still graded into `bybit_coverage_soak`, so the rows a reviewer needs before widening to `bybit_2` exist *for* `bybit_2`. That is the `NETTING_ATTRIBUTION_ACCOUNTS` correction from 2026-08-09.

New files: `src/runtime/bybit_coverage_basis.py`, `src/runtime/bybit_coverage_soak.py`, `tests/test_bybit_coverage_basis.py`. Touched: `order_monitor.py`, `bybit_leg_sides.py` (docstrings), `diag.py`, `get_env.py`, `CLAUDE.md`, `tests/test_bybit_naked_rearm.py`.

### Verified
- `run_guards.py --base main` on the **committed** head: **PASS 52 · FAIL 0**. Three initial failures fixed, none suppressed.
- Regression check as a **differential**: 339 test files run on this branch *and* on a clean `origin/main` worktree with the identical list → **143 failures each, branch-only set EMPTY**.
- ⚠️ **Sandbox finding for anyone else running the suite here:** `numpy` is absent while something in the suite inserts a stub `numpy` into `sys.modules`, so **every `pytest.approx` comparison crashes** with `TypeError: isinstance() arg 2 must be a type`. It presents as a wrong-value assertion failure and is not one. `pip install numpy` took failures 386 → 143. Not repo-side, but it will mislead the next session that runs a broad suite here.

### NOT verified — flagging rather than burying
Nothing on the fleet. No diag read, no VM action, no live observation. And the masking this fixes is still **n = 1, CONSTRUCTED** from the 2026-09-02T03:30:33Z venue read — never observed live.

### For whoever merges
Merging arms **nothing**: the shipped allowlist is empty, so the masking stays live on every account including real-money `bybit_2` until a separate Tier-2 `set-env` of **both** keys (+ restart). Deployed ≠ armed ≠ observed; the PR body says what moves each.
