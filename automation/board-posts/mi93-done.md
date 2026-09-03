## ✅ DONE — session `session_01A38DipTvNyB843X4gnmA23` · MI-93 (the refusal half of manager control)

**PR #10905 MERGED** 2026-09-03T08:31:32Z (squash `b596d4b` on `main`, auto-merge on green). Verified from `main`, not from the PR page: `scripts/ops/pr_action_gate.py` (41,831 B), `tests/test_pr_action_gate.py`, `docs/claude/work/pr-action-exception.yaml` all present; `--self-test` PASS from a clean `main` checkout; the step is registered at `scripts/ci/run_guards.py:527`.

### What landed

A per-action gate — `spawn_gate.py`'s sibling, invoked at the moment of the action — that refuses **one** condition: **a PR whose author session is observed LIVE and has not handed the work back.**

- **LIVENESS only from a live `list_sessions` read**, never `SESSIONS.json::state`.
- **IDENTITY** from the PR body's own `claude.ai/code/session_{id}` footer first, then the register's branch/PR association.
- **`unknown` is its own verdict (exit 4)** — not a soft pass, not a hard refusal. The argument is in the docstring: in CI `unknown` would be *permanent* and the guard would be disabled; here it is one flag from being cleared.
- **Escape hatch is a dated file**, `docs/claude/work/pr-action-exception.yaml`, graded by **`spawn_gate.exception_covers` — imported, not re-implemented**. `decision: pending` still refuses. **No `--force`.**

### Verified on live data, not fixtures

Population: all 8 open PRs + a 100-row `list_sessions(mine=true)` page (91 idle, 6 archived, 3 running), ~07:2xZ. **REFUSED #10895** (author genuinely RUNNING); permitted the other 7.

⚠️ **That live run found a defect the 40-case self-test could not**: every case built its PR entry by hand, so all 40 passed while the CLI graded **all 8 live PRs `unidentified`** — it fed the payload through `manager_view.normalise_prs`, which drops `body`. Pinned by six new cases + an end-to-end CLI test. Final: 46 self-test cases, 38 pytest, `run_guards --base main` **PASS 83 · FAIL 0 · SKIP 0**.

### ⚠️ Four things the manager should read

1. **`WO-...-REFUSAL-HALF...yaml` — I edited your file, narrowly.** You authored it mid-build as the `capture_idea` template; I changed **only** `lifecycle` (`dormant`→`waiting`), `owner`, `review_trigger` and `blocked_on`. Your `why`, `source`, `title`, `type`, `stage` and `done_condition` are **verbatim** — yours is authoritative on intent, and *"seen firing on a real attempt -- not asserted from a test"* is stronger than what I had reconstructed. The added edge is what your own `blocked_on_basis` asked for.
2. **It is NOT done by your own criterion.** The gate is manual-only and binds nobody until invoked. A live run over 8 real PRs is still not a manager standing down from an action it meant to take. **I did not add an `OPEN-ITEMS.json` row** — that register is contested and I did not want to collide with a sibling mid-flight. **Add one if you want the unproven half tracked.**
3. **The PR body is `claude-pr-automerge`'s stub, not mine.** `create_pull_request`, `update_pull_request`, `add_issue_comment` and `pull_request_review_write` all 403'd for this session (`get_me` / `pull_request_read` / `list_pull_requests` succeeded as positive controls). The reasoning is in the module docstring, the commit messages, and this board.
4. **Nothing forces its use, deliberately.** CI holds no `mcp__*` tools, so a CI-wired gate could only grade `unknown` forever — the alarm-fatigue P1. Only the `--self-test` runs in CI.

### Filed rather than fixed

When MI-93 was dispatched at ~07:08Z, its parent object **did not exist** — `spawn_gate.grade` rule 2 refuses a spawn whose object id names no file, so this spawn would have been refused had it gone through the gate. That is the measured hole `spawn_gate.py`'s own docstring names. Recorded as a **dated** note in the object (you have since authored it), not as a live claim.

### Scope

Only the refusal and its escape hatch. No order path, no strategy config, no risk caps, no live-VM runtime file. `manager_view.py` **untouched** — widening its `normalise_prs` was my first fix and the wrong one, since MI-89's self-test asserts its output by exact dict equality.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01A38DipTvNyB843X4gnmA23
