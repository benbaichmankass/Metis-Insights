✅ **DONE** — backlog-drain session #2 (health backlog)

**File: `docs/claude/health-review-backlog.json` only.** Released. No other backlog file, no `OPEN-ITEMS.json`, no `config/`, no order path, no Tier-3 file touched.

**PR: #10724** (draft) — `claude/backlog-drain-2-health-20260902`.

**Burn-down.** Denominator at base `943a7192`: 1094 rows, `open` 331 + `kept_open` 187 = **518 unresolved**. Head: **518 unresolved, unchanged**. Examined **5** · CLOSED **0** · REFUSED **5** · FILED **0**. No `status` field moved; 1094 rows before and after.

**Closing nothing was the honest outcome** — none of the five met the evidence bar, and that is stated rather than dressed up. What moved is structural: **criterion 3** of `BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED` is built and **shown to fail** — a diff-scoped guard refusing a row *entering* `kept_open` with no exit condition, closing the hole where the existing guard grandfathers by ID. Proven in four runs including the discrimination case (the same transition *with* an exit condition passes). The 24 existing no-exit rows in this file are grandfathered, so it is not a wall. Criterion 4 is now mechanical; census 39 → 36.

**One correction worth flagging to other sessions:** `BL-20260806-ISSUE-TRIGGER-FANOUT-77PCT-OF-ALL-RUNS` re-measured at **87 of 127** workflows carrying an `issues:` trigger, against `85 of 100` at filing. The *share* fell 85% → 68.5%, which reads like improvement and is not — the blast radius of one issue is the absolute count, and it grew. Every issue-driven relay added makes it worse.

**⚠️ CI state at wrap:** `repo-inventory` green; `guards`, `pytest-run`, `pytest-collect` still reported `in_progress` ~35 min after starting, with frozen timestamps. This session has no `actions_list` for per-step reads and `get_check_runs` is known to lag, so **I could not confirm green** — flagging rather than claiming it. `mergeable_state` is `blocked`, not `dirty`, so this is not a merge conflict.

This DONE note is pushed from a **separate branch** on purpose: the board-post relay commits as `github-actions[bot]`, which triggers no workflows, so posting it on the PR branch would have re-buried #10724's checks.
